from xdcr.xdcrbasetests import XDCRReplicationBaseTest
from remote.remote_util import RemoteMachineShellConnection
from membase.api.rest_client import RestConnection, RestHelper
from membase.helper.cluster_helper import ClusterOperationHelper
from memcached.helper.data_helper import MemcachedClientHelper
from random import randrange
import time
import paramiko

class CBRbaseclass(XDCRReplicationBaseTest):
    def _autofail_enable(self, _rest_):
        status = _rest_.update_autofailover_settings(True, self._timeout / 2)
        if not status:
            self.log.info('failed to change autofailover_settings!')
            return
        #read settings and verify
        settings = _rest_.get_autofailover_settings()
        self.assertEquals(settings.enabled, True)

    def _autofail_disable(self, _rest_):
        status = _rest_.update_autofailover_settings(False, self._timeout / 2)
        if not status:
            self.log.info('failed to change autofailover_settings!')
            return
        #read settings and verify
        settings = _rest_.get_autofailover_settings()
        self.assertEquals(settings.enabled, False)

    def wait_for_failover_or_assert(self, master, autofailover_count, timeout):
        time_start = time.time()
        time_max_end = time_start + timeout + 60
        failover_count = 0
        while time.time() < time_max_end:
            failover_count = self.get_failover_count(master)
            if failover_count == autofailover_count:
                break
            self.sleep(2)

        if failover_count != autofailover_count:
            rest = RestConnection(master)
            self.log.info("Latest logs from UI:")
            for i in rest.getlogs(): self.log.error(i)
            self.log.warn("pools/default from {0} : {1}".format(master.ip, rest.cluster_status()))
            self.fail("{0} nodes failed over, expected {1} in {2} seconds".
                            format(failover_count, autofailover_count, time.time() - time_start))
        else:
            self.log.info("{O} nodes failed over as expected")

    def get_failover_count(self, master):
        rest = RestConnection(master)
        cluster_status = rest.cluster_status()

        failover_count = 0
        # check for inactiveFailed
        for node in cluster_status['nodes']:
            self.log.info("'clusterMembership' for node {0} is {1}".format(node["otpNode"], node['clusterMembership']))
            if node['clusterMembership'] == "inactiveFailed":
                failover_count += 1

        return failover_count

    def wait_for_catchup(self, servers):
        for bucket in self.buckets:
            start = time.time()
            while time.time() - start < 600:
                _items = 0
                for server in servers:
                    mc = MemcachedClientHelper.direct_client(server, bucket.name)
                    _items += int(mc.stats()["curr_items"])
                    mc.close()
                if _items == int(self._num_items):
                    break
                self.sleep(self._timeout)

    def cbr_routine(self, _healthy_, _compromised_, _compromised_cluster):
        shell = RemoteMachineShellConnection(_healthy_)
        info = shell.extract_remote_info()
        _ssh_client = paramiko.SSHClient()
        _ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _ssh_client.connect(hostname=_healthy_.ip,username=_healthy_.ssh_username,password=_healthy_.ssh_password)
        for bucket in self.buckets:
            if info.type.lower() == "linux":
                command = "/opt/couchbase/bin/cbrecovery http://{0}:{1}@{2}:{3} http://{4}:{5}@{6}:{7} -b {8} -B {8}".format(
                                                    _healthy_.rest_username, _healthy_.rest_password, _healthy_.ip, _healthy_.port, 
                                                    _compromised_.rest_username, _compromised_.rest_password, _compromised_.ip, _compromised_.port,
                                                    bucket.name)
                self.log.info("Running command .. {0}".format(command))
                _ssh_client.exec_command(command)
            elif info.type.lower() == "windows":
                command =  "C:/Program\ Files/Couchbase/Server/bin/cbrecovery.exe http://{0}:{1}@{2}:{3} http://{4}:{5}@{6}:{7} -b {8} -B {8}".format(
                                                    _healthy_.rest_username, _healthy_.rest_password, _healthy_.ip, _healthy_.port, 
                                                    _compromised_.rest_username, _compromised_.rest_password, _compromised_.ip, _compromised_.port,
                                                    bucket.name)
                self.log.info("Running command .. {0}".format(command))
                _ssh_client.exec_command(command)
        self.wait_for_catchup(_compromised_cluster)
        shell.disconnect()

    def trigger_rebalance(self, rest):
        _nodes_ = rest.node_statuses()
        rest.rebalance(otpNodes=[node.id for node in _nodes_], ejectedNodes=[])
        RestHelper(rest).rebalance_reached()

    def vbucket_map_checker(self, _before_, _after_, _ini_, _fin_):
        _pre_ = {}
        _post_ = {}

        for i in range(_ini_):
            _pre_[i] = 0
        for i in range(_fin_):
            _post_[i] = 0

        for i in _before_:
            for j in range(_ini_):
                if i[0] == j:
                    _pre_[j] += 1

        for i in _after_:
            for j in range(_fin_):
                if i[0] == j:
                    _post_[j] += 1

        for i in _pre_.keys():
            for j in _post_.keys():
                if i == j:
                    if _pre_[i] != _post_[j]:
                        return False
        return True

#Assumption that at least 2 nodes on every cluster
class cbrecovery(CBRbaseclass, XDCRReplicationBaseTest):
    def setUp(self):
        super(cbrecovery, self).setUp()
        self._failover_count = self._input.param("fail_count", 0)
        self._add_count = self._input.param("add_count", 0)
        self.failover_reason = self._input.param("failover_reason", "stop_server")     # or firewall_block
        self.failed_nodes = []
        self._ifautofail = 0

    def tearDown(self):
        if self._ifautofail == 1:
            if "stop_server" in self.failover_reason:
                for node in self.failed_nodes:
                    shell = RemoteMachineShellConnection(node)
                    shell.start_couchbase()
                    shell.disconnect()
            elif "firewall_block" in self.failover_reason:
                for node in self.failed_nodes:
                    shell = RemoteMachineShellConnection(node)
                    o, r = shell.execute_command("iptables -F")
                    shell.log_command_output(o, r)
                    o, r = shell.execute_command("/sbin/iptables -A INPUT -p tcp -i eth0 --dport 1000:60000 -j ACCEPT")
                    shell.log_command_output(o, r)
                    shell.disconnect()
        super(cbrecovery, self).tearDown()

    def cbrecover_multiple_failover_swapout_reb_routine(self):
        self._load_all_buckets(self.src_master, self.gen_create, "create", 0)
        tasks = []
        if self._doc_ops is not None:
            if "update" in self._doc_ops:
                tasks.extend(self._async_load_all_buckets(self.src_master, self.gen_update, "update", self._expires))
            if "delete" in self._doc_ops:
                tasks.extend(self._async_load_all_buckets(self.src_master, self.gen_delete, "delete", 0))
        for task in tasks:
            task.result()

        self.sleep(self._timeout / 3)
        vbucket_map_before = []
        initial_node_count = 0
        vbucket_map_after = []
        final_node_count = 0

        if self._failover is not None:
            if "source" in self._failover:
                initial_node_count = len(self.src_nodes)
                rest = RestConnection(self.src_master)
                vbucket_map_before = rest.fetch_vbucket_map()       # JUST FOR DEFAULT BUCKET AS OF NOW
                if self._failover_count >= len(self.src_nodes):
                    self.log.info("Won't failover .. count exceeds available servers on source : SKIPPING TEST")
                    self.tearDown()
                    return
                if len(self._floating_servers_set) < self._add_count:
                    self.log.info("Not enough spare nodes available, to match the failover count : SKIPPING TEST")
                    self.tearDown()
                    return
                self.log.info("Failing over {0} nodes on source ..".format(self._failover_count))
                self.failed_nodes = self.src_nodes[(len(self.src_nodes)-self._failover_count):len(self.src_nodes)]
                self._cluster_helper.failover(self.src_nodes, self.failed_nodes)
                for node in self.failed_nodes:
                    self.src_nodes.remove(node)
                self.sleep(self._timeout / 4)
                add_nodes = self._floating_servers_set[0:self._add_count]

                for node in add_nodes:
                    rest.add_node(user=node.rest_username, password=node.rest_password, remoteIp=node.ip, port=node.port)
                self.src_nodes.extend(add_nodes)
                # CALL THE CBRECOVERY ROUTINE
                self.cbr_routine(self.dest_master, self.src_master, self.src_nodes)

                self.trigger_rebalance(rest)
                vbucket_map_after = rest.fetch_vbucket_map()
                final_node_count = len(self.src_nodes)

            elif "destination" in self._failover:
                initial_node_count = len(self.dest_nodes)
                rest = RestConnection(self.dest_master)
                vbucket_map_before = rest.fetch_vbucket_map()       # JUST FOR DEFAULT BUCKET AS OF NOW
                if self._failover_count >= len(self.dest_nodes):
                    self.log.info("Won't failover .. count exceeds available servers on sink : SKIPPING TEST")
                    self.tearDown()
                    return
                if len(self._floating_servers_set) < self._add_count:
                    self.log.info("Not enough spare nodes available, to match the failover count : SKIPPING TEST")
                    self.tearDown()
                    return
                self.log.info("Failing over {0} nodes on destination ..".format(self._failover_count))
                self.failed_nodes = self.dest_nodes[(len(self.dest_nodes)-self._failover_count):len(self.dest_nodes)]
                self._cluster_helper.failover(self.dest_nodes, self.failed_nodes)
                for node in self.failed_nodes:
                    self.dest_nodes.remove(node)
                self.sleep(self._timeout / 4)
                add_nodes = self._floating_servers_set[0:self._add_count]

                for node in add_nodes:
                    rest.add_node(user=node.rest_username, password=node.rest_password, remoteIp=node.ip, port=node.port)
                self.dest_nodes.extend(add_nodes)
                # CALL THE CBRECOVERY ROUTINE
                self.cbr_routine(self.src_master, self.dest_master, self.dest_nodes)

                self.trigger_rebalance(rest)
                vbucket_map_after = rest.fetch_vbucket_map()
                final_node_count = len(self.dest_nodes)

            #TOVERIFY: Check if vbucket map unchanged if swap rebalance
            if self._failover_count == self._add_count:
                _flag_ = self.vbucket_map_checker(vbucket_map_before, vbucket_map_after, initial_node_count, final_node_count)
                if _flag_:
                    self.log.info("vbucket_map same as earlier")
                else:
                    self.log.info("vbucket_map differs from earlier")

        self.sleep(self._timeout / 2)
        self.merge_buckets(self.src_master, self.dest_master, bidirection=False)
        self.verify_results()

    def cbrecover_multiple_autofailover_swapout_reb_routine(self):
        self._ifautofail = 1
        self._load_all_buckets(self.src_master, self.gen_create, "create", 0)
        tasks = []
        if self._doc_ops is not None:
            if "update" in self._doc_ops:
                tasks.extend(self._async_load_all_buckets(self.src_master, self.gen_update, "update", self._expires))
            if "delete" in self._doc_ops:
                tasks.extend(self._async_load_all_buckets(self.src_master, self.gen_delete, "delete", 0))
        for task in tasks:
            task.result()

        self.sleep(self._timeout / 3)
        vbucket_map_before = []
        initial_node_count = 0
        vbucket_map_after = []
        final_node_count = 0

        if self._failover is not None:
            if "source" in self._failover:
                initial_node_count = len(self.src_nodes)
                rest = RestConnection(self.src_master)
                vbucket_map_before = rest.fetch_vbucket_map()       # JUST FOR DEFAULT BUCKET AS OF NOW
                if self._failover_count >= len(self.src_nodes):
                    self.log.info("Won't failover .. count exceeds available servers on source : SKIPPING TEST")
                    self.tearDown()
                    return
                if len(self._floating_servers_set) < self._add_count:
                    self.log.info("Not enough spare nodes available, to match the failover count : SKIPPING TEST")
                    self.tearDown()
                    return

                self._autofail_enable(rest)
                self.log.info("Triggering {0} over {1} nodes on source ..".format(self.failover_reason, self._failover_count))
                self.failed_nodes = self.src_nodes[(len(self.src_nodes)-self._failover_count):len(self.src_nodes)]
                _count_ = 1
                if "stop_server" in self.failover_reason:
                    for node in self.failed_nodes:
                        shell = RemoteMachineShellConnection(node)
                        shell.stop_couchbase()
                        self.wait_for_failover_or_assert(self.src_master, _count_, self._timeout)
                        shell.disconnect()
                        _count_ += 1
                elif "firewall_block" in self.failover_reason:
                    for node in self.failed_nodes:
                        shell = RemoteMachineShellConnection(node)
                        shell.log_command_output(o, r)
                        o, r = shell.execute_command("/sbin/iptables -A INPUT -p tcp -i eth0 --dport 1000:60000 -j REJECT")
                        self.wait_for_failover_or_assert(self.src_master, _count_, self._timeout)
                        shell.disconnect()
                        _count_ += 1
#                self.wait_for_failover_or_assert(self.src_master, self._failover_count, self._timeout)
                for node in self.failed_nodes:
                    self.src_nodes.remove(node)
                self.sleep(self._timeout / 4)
                add_nodes = self._floating_servers_set[0:self._add_count]
                for node in add_nodes:
                    rest.add_node(user=node.rest_username, password=node.rest_password, remoteIp=node.ip, port=node.port)
                self.src_nodes.extend(add_nodes)
                # CALL THE CBRECOVERY ROUTINE
                self.cbr_routine(self.dest_master, self.src_master, self.src_nodes)

                self.trigger_rebalance(rest)
                vbucket_map_after = rest.fetch_vbucket_map()
                initial_node_count = len(self.src_nodes)

                self._autofail_disable(rest)

            elif "destination" in self._failover:
                initial_node_count = len(self.dest_nodes)
                rest = RestConnection(self.dest_master)
                vbucket_map_before = rest.fetch_vbucket_map()       # JUST FOR DEFAULT BUCKET AS OF NOW
                if self._failover_count >= len(self.dest_nodes):
                    self.log.info("Won't failover .. count exceeds available servers on source : SKIPPING TEST")
                    self.tearDown()
                    return
                if len(self._floating_servers_set) < self._add_count:
                    self.log.info("Not enough spare nodes available, to match the failover count : SKIPPING TEST")
                    self.tearDown()
                    return

                self._autofail_enable(rest)
                self.log.info("Triggering {0} over {1} nodes on source ..".format(self.failover_reason, self._failover_count))
                self.failed_nodes = self.dest_nodes[(len(self.dest_nodes)-self._failover_count):len(self.dest_nodes)]
                if "stop_server" in self.failover_reason:
                    for node in self.failed_nodes:
                        shell = RemoteMachineShellConnection(node)
                        shell.stop_couchbase()
                        shell.disconnect()
                elif "firewall_block" in self.failover_reason:
                    for node in self.failed_nodes:
                        shell = RemoteMachineShellConnection(node)
                        shell.log_command_output(o, r)
                        o, r = shell.execute_command("/sbin/iptables -A INPUT -p tcp -i eth0 --dport 1000:60000 -j REJECT")
                        shell.disconnect()
                self.wait_for_failover_or_assert(self.dest_master, self._failover_count, self._timeout)
                for node in self.failed_nodes:
                    self.dest_nodes.remove(node)
                self.sleep(self._timeout / 4)
                add_nodes = self._floating_servers_set[0:self._add_count]
                for node in add_nodes:
                    rest.add_node(user=node.rest_username, password=node.rest_password, remoteIp=node.ip, port=node.port)
                self.dest_nodes.extend(add_nodes)
                # CALL THE CBRECOVERY ROUTINE
                self.cbr_routine(self.src_master, self.dest_master, self.dest_nodes)

                self.trigger_rebalance(rest)
                vbucket_map_after = rest.fetch_vbucket_map()
                final_node_count = len(self.dest_nodes)

                self._autofail_disable(rest)

            #TOVERIFY: Check if vbucket map unchanged if swap rebalance
            if self._failover_count == self._add_count:
                _flag_ = self.vbucket_map_checker(vbucket_map_before, vbucket_map_after, initial_node_count, final_node_count)
                if _flag_:
                    self.log.info("vbucket_map same as earlier")
                else:
                    self.log.info("vbucket_map differs from earlier")

        self.sleep(self._timeout / 2)
        self.merge_buckets(self.src_master, self.dest_master, bidirection=False)
        self.verify_results()
