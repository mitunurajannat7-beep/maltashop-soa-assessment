"""Run inside the Order container: real HTTP + real RabbitMQ consumption."""
import os
import time
import uuid
from app.common import encode, request_json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    base = "http://127.0.0.1:8000"
    inventory = os.environ.get("INVENTORY_URL", "http://inventory:8000")
    key = os.environ.get("SERVICE_API_KEY", "local-demo-service-key")
    cid = "smoke-" + str(uuid.uuid4())
    body = {"customer_id":"demo-customer","sku":"HEADPHONES","quantity":1,"payment_token":"mock-success"}

    def place(payload, idem):
        # Use urllib directly to supply the client idempotency header.
        import json
        import urllib.request
        import urllib.error
        request = urllib.request.Request(base+"/orders", data=encode(payload).encode(),
            headers={"Content-Type":"application/json","Idempotency-Key":idem,"X-Correlation-ID":cid},method="POST")
        try:
            with urllib.request.urlopen(request,timeout=15) as response:
                return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code,json.loads(error.read())

    status, result = place(body,cid)
    require(status==201 and result["status"]=="CONFIRMED","Happy-path order did not confirm")
    order_id = result["order_id"]
    print("PASS: real HTTP Order -> Inventory -> Payment saga",flush=True)
    status, replay = place(body,cid)
    require(status==200 and replay["order_id"]==order_id and replay["replayed"],"Idempotency replay failed")
    status, conflict = place(dict(body,quantity=2),cid)
    require(status==409 and conflict.get("error")=="IDEMPOTENCY_CONFLICT","Conflicting reuse was accepted")
    print("PASS: repeat requests have one order; conflicting input is rejected",flush=True)

    deadline = time.monotonic()+45
    while time.monotonic()<deadline:
        _, notifications = request_json("GET","http://notification:8000/notifications?order_id="+order_id,
                                        correlation_id=cid,api_key=key)
        if notifications.get("notifications"):
            break
        time.sleep(0.5)
    require(len(notifications.get("notifications",[]))==1,"RabbitMQ event was not consumed within 45 seconds")
    record = notifications["notifications"][0]
    require(record["correlation_id"]==cid and record["event_id"]==result["event_id"],"Correlation/event identity mismatch")
    print("PASS: real RabbitMQ publication and Notification consumption, matching correlation ID",flush=True)

    _, stock_before = request_json("GET",inventory+"/stock/HEADPHONES",api_key=key)
    status, declined = place(dict(body,payment_token="mock-decline"),cid+"-decline")
    require(status==409 and declined["status"]=="REJECTED" and declined["reason"]=="PAYMENT_DECLINED",
            "Simulated payment decline did not reject")
    _, stock_after = request_json("GET",inventory+"/stock/HEADPHONES",api_key=key)
    require(stock_before["available"]==stock_after["available"],"Compensation did not restore inventory")
    require(declined["event_id"] is None,"Rejected order emitted a confirmation event")
    print("PASS: rejected payment restores stock and emits no OrderPlaced event",flush=True)

    status, unavailable = place(dict(body,sku="MISSING"),cid+"-stock")
    require(status==409 and unavailable["reason"]=="INSUFFICIENT_STOCK","Missing stock was not rejected")
    print("PASS: insufficient inventory is rejected",flush=True)
    print("SMOKE TEST PASSED",flush=True)


if __name__ == "__main__":
    main()
