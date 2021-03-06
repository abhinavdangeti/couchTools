import java.io.IOException;
import java.net.URISyntaxException;
import java.util.LinkedList;
import java.util.List;
import java.util.Random;
import java.util.concurrent.ExecutionException;

import net.spy.memcached.internal.OperationFuture;

import org.codehaus.jettison.json.JSONException;
import org.codehaus.jettison.json.JSONObject;

import com.couchbase.client.CouchbaseClient;


public class Creater {

    public static void create_items (int _itemCount, int _itemSize, boolean _json, CouchbaseClient client, String _prefix) 
	throws URISyntaxException, IOException, JSONException, InterruptedException, ExecutionException {
	    Random gen = new Random ( 987654321 );
	    StringBuffer value = new StringBuffer();
	    String CHAR_LIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
	    while (value.length() < _itemSize) {
		value.append(CHAR_LIST);
	    }

	    List<OperationFuture<Boolean>> creates = new LinkedList<OperationFuture<Boolean>>();
	    for (int i=0; i<_itemCount; i++){
		OperationFuture<Boolean> setOp;
		String key = String.format("%s%d", _prefix, i);
		if (_json) {
		    JSONObject _val = Spawner.retrieveJSON(gen, _itemSize);
		    setOp = client.set(key, 0, _val.toString());
		} else {
		    setOp = client.set(key, 0, value.toString());
		}
		creates.add(setOp);
	    }
	    while (!creates.isEmpty()) {
		if (creates.get(0).get().booleanValue() == false){
		    System.err.println("Set failed: "/* + setOp.getStatus().getMessage()*/);
		    continue;
		}
		creates.remove(0);
	    }
	}
}
