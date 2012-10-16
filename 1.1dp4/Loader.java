import java.io.IOException;
import java.net.URISyntaxException;
import java.util.Random;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

import org.codehaus.jettison.json.JSONException;
import org.codehaus.jettison.json.JSONObject;

import net.spy.memcached.PersistTo;
import com.couchbase.client.CouchbaseClient;
import net.spy.memcached.internal.OperationFuture;

public class Loader {

	/*
	 * Method that loads items from 1 to NUM_ITEMS.
	 * Enabling OBSERVE, makes the loader check if every item created has persisted or not.
	 * Inserted items have key [Key-i] and corresponding value [i].
	 */
	public static double load_items(int number_items, double ratio_exp, int expiration, Boolean OBSERVE) 
			throws URISyntaxException, IOException, InterruptedException, ExecutionException, JSONException {
		CouchbaseClient client = Mainhelper.connect();		
		int items_with_exp = (int)(number_items * ratio_exp);
		double tot_time = 0.0;
		int obs_true=0, obs_false=0;
		Random generator = new Random( 123456789 );
		for(int i=1;i<=(number_items - items_with_exp);i++){
			String Key = String.format("Key-%d", i);
			//String Value = String.format("%d", i);
			JSONObject Value = retrieveJSON(i, generator);
			if(OBSERVE){
				long preOBS = System.nanoTime();
				OperationFuture<Boolean> setOp = client.set(Key, 0, Value.toString(), PersistTo.MASTER);
				if(setOp.get().booleanValue())
					obs_true++;
				else
					obs_false++;
				long postOBS = System.nanoTime();
				System.out.println("SET-OBSERVE for item " + i + " :: TOOK: " + (double)(postOBS - preOBS) / 1000000.0 + " ms.");
				tot_time += (double)(postOBS - preOBS) / 1000000.0;
			}else{
				OperationFuture<Boolean> setOp = client.set(Key, 0, Value.toString());
				if (setOp.get().booleanValue() == false){ 
					System.err.println("Set failed: " + setOp.getStatus().getMessage());
			    	break;
				}
			}
		}
		for(int i=(number_items - items_with_exp + 1);i<=number_items;i++){
			String Key = String.format("Key-%d", i);
			//String Value = String.format("%d", i);
			JSONObject Value = retrieveJSON(i, generator);
			if(OBSERVE){
				long preOBS = System.nanoTime();
				OperationFuture<Boolean> setOp = client.set(Key, 0, Value.toString(), PersistTo.MASTER);
				if(setOp.get().booleanValue())
					obs_true++;
				else
					obs_false++;
				long postOBS = System.nanoTime();
				System.out.println("SET-OBSERVE for item " + i + " :: TOOK: " + (double)(postOBS - preOBS) / 1000000.0 + " ms.");
				tot_time += (double)(postOBS - preOBS) / 1000000.0;
			}else{
				OperationFuture<Boolean> setOp = client.set(Key, expiration, Value.toString());
				if (setOp.get().booleanValue() == false){ 
					System.err.println("Set failed: " + setOp.getStatus().getMessage());
			    	break;
				}
			}	
		}
		if (OBSERVE)
			System.out.println("No. of items that actually persisted to disk: " + obs_true);
			System.out.println("AVERAGE LATENCY SEEN FOR ALL SETS WITH OBSERVE: " + (tot_time / number_items));
		client.shutdown(10, TimeUnit.SECONDS);
		return (tot_time / (obs_true + obs_false));
	}

	private static JSONObject retrieveJSON(int i, Random gen) throws JSONException {
		String _number = null;
        Integer temp = gen.nextInt();
        JSONObject jsonobj = null;
        if(temp % 2 == 0){
            _number = "e" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"earth\", \"species\":\"human\", \"number\":\"" + _number + "\"}");
        } else if(temp % 3 == 0) {
            _number = "m" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"mars\", \"species\":\"martian\", \"number\":\"" + _number + "\"}");
        } else if(temp % 10 == 1) {
            _number = "v" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"venus\", \"species\":\"venusses\", \"number\":\"" + _number + "\"}");
        } else if(temp % 10 == 3) {
            _number = "j" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"jupiter\", \"species\":\"jupitorian\", \"number\":\"" + _number + "\"}");
        } else if(temp % 10 == 5) {
            _number = "s" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"saturn\", \"species\":\"saturness\", \"number\":\"" + _number + "\"}");
		} else {
            _number = "u" + temp.toString();
            jsonobj = new JSONObject("{\"planet\":\"unknown\", \"species\":\"unknown\", \"number\":\"" + _number + "\"}");
        }
		return jsonobj;
	}
}