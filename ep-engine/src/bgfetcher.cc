/* -*- Mode: C++; tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*- */
#include "config.h"
#include "ep.hh"
#include "iomanager/iomanager.h"
#include "kvshard.hh"

const double BgFetcher::sleepInterval = 1.0;

void BgFetcher::start() {
    LockHolder lh(taskMutex);
    pendingFetch.cas(false, true);
    IOManager* iom = IOManager::get();
    taskId = iom->scheduleMultiBGFetcher(&(store->getEPEngine()), this,
                                         Priority::BgFetcherPriority,
                                         shard->getId());
    assert(taskId > 0);
}

void BgFetcher::stop() {
    LockHolder lh(taskMutex);
    pendingFetch.cas(true, false);
    assert(taskId > 0);
    IOManager::get()->cancel(taskId);
}

void BgFetcher::notifyBGEvent(void) {
    if (++stats.numRemainingBgJobs == 1) {
        LockHolder lh(taskMutex);
        assert(taskId > 0);
        IOManager::get()->wake(taskId);
    }
}

void BgFetcher::doFetch(uint16_t vbId) {
    hrtime_t startTime(gethrtime());
    LOG(EXTENSION_LOG_DEBUG, "BgFetcher is fetching data, vBucket = %d "
        "numDocs = %d, startTime = %lld\n", vbId, items2fetch.size(),
        startTime/1000000);

    shard->getROUnderlying()->getMulti(vbId, items2fetch);

    int totalfetches = 0;
    std::vector<VBucketBGFetchItem *> fetchedItems;
    vb_bgfetch_queue_t::iterator itr = items2fetch.begin();
    for (; itr != items2fetch.end(); itr++) {
        std::list<VBucketBGFetchItem *> &requestedItems = (*itr).second;
        std::list<VBucketBGFetchItem *>::iterator itm = requestedItems.begin();
        for(; itm != requestedItems.end(); itm++) {
            if ((*itm)->value.getStatus() != ENGINE_SUCCESS &&
                (*itm)->canRetry()) {
                // underlying kvstore failed to fetch requested data
                // don't return the failed request yet. Will requeue
                // it for retry later
                LOG(EXTENSION_LOG_WARNING, "Warning: bgfetcher failed to fetch "
                    "data for vb = %d seq = %lld key = %s retry = %d\n", vbId,
                    (*itr).first, (*itm)->key.c_str(), (*itm)->getRetryCount());
                continue;
            }
            fetchedItems.push_back(*itm);
            ++totalfetches;
        }
    }

    if (totalfetches > 0) {
        store->completeBGFetchMulti(vbId, fetchedItems, startTime);
        stats.getMultiHisto.add((gethrtime()-startTime)/1000, totalfetches);
    }

    // failed requests will get requeued for retry within clearItems()
    clearItems(vbId);
}

void BgFetcher::clearItems(uint16_t vbId) {
    vb_bgfetch_queue_t::iterator itr = items2fetch.begin();
    size_t numRequeuedItems = 0;
    for(; itr != items2fetch.end(); itr++) {
        // every fetched item belonging to the same seq_id shares
        // a single data buffer, just delete it from the first fetched item
        std::list<VBucketBGFetchItem *> &doneItems = (*itr).second;
        VBucketBGFetchItem *firstItem = doneItems.front();
        firstItem->delValue();

        std::list<VBucketBGFetchItem *>::iterator dItr = doneItems.begin();
        for (; dItr != doneItems.end(); dItr++) {
            if ((*dItr)->value.getStatus() == ENGINE_SUCCESS ||
                !(*dItr)->canRetry()) {
                delete *dItr;
            } else {
                RCPtr<VBucket> vb = store->getVBucket(vbId);
                assert(vb);
                (*dItr)->incrRetryCount();
                LOG(EXTENSION_LOG_DEBUG, "BgFetcher is re-queueing failed "
                    "request for vb = %d seq = %lld key = %s retry = %d\n",
                     vbId, (*itr).first, (*dItr)->key.c_str(),
                     (*dItr)->getRetryCount());
                ++numRequeuedItems;
                vb->queueBGFetchItem(*dItr, this, false);
            }
        }
    }

    if (numRequeuedItems) {
        stats.numRemainingBgJobs.incr(numRequeuedItems);
    }
}

bool BgFetcher::run(size_t tid) {
    assert(tid > 0);
    size_t num_fetched_items = 0;
    const VBucketMap &vbMap = store->getVBuckets();

    pendingFetch.cas(true, false);

    std::vector<int> vbIds = shard->getVBuckets();
    size_t numVbuckets = vbIds.size();
    for (size_t i = 0; i < numVbuckets; i++) {
        RCPtr<VBucket> vb = shard->getBucket(vbIds[i]);
        assert(items2fetch.empty());
        if (vb && vb->getBGFetchItems(items2fetch)) {
            doFetch(vbIds[i]);
            num_fetched_items += items2fetch.size();
            items2fetch.clear();
        }
    }

    stats.numRemainingBgJobs.decr(num_fetched_items);
    if (!pendingFetch.get()) {
        // wait a bit until next fetch request arrives
        double sleep = std::max(store->getBGFetchDelay(), sleepInterval);
        IOManager::get()->snooze(taskId, sleep);

        if (pendingFetch.get()) {
            // check again numRemainingBgJobs, a new fetch request
            // could have arrvied right before calling above snooze()
            IOManager::get()->snooze(taskId, 0);
        }
    }
    return true;
}

bool BgFetcher::pendingJob() {
    std::vector<int> vbIds = shard->getVBuckets();
    size_t numVbuckets = vbIds.size();
    for (size_t i = 0; i < numVbuckets; ++i) {
        RCPtr<VBucket> vb = shard->getBucket(vbIds[i]);
        if (vb && vb->hasPendingBGFetchItems()) {
            return true;
        }
    }
    return false;
}