"""Live Compose checks. Run inside Order; stop/restart broker from the host."""
from pathlib import Path
import json
import os
import sys
import time
import urllib.request
import uuid
from app.common import encode, request_json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def location(name):
    return Path(os.environ.get("DB_PATH","/data/service.db")).parent / ("verification-"+name+".json")


def place(state):
    request = urllib.request.Request("http://127.0.0.1:8000/orders",data=encode(state["body"]).encode(),
        headers={"Content-Type":"application/json","Idempotency-Key":state["key"],
                 "X-Correlation-ID":state["key"]},method="POST")
    with urllib.request.urlopen(request,timeout=15) as response:
        return response.status,json.loads(response.read())


def wait_for_notification(state):
    deadline = time.monotonic()+45
    while time.monotonic()<deadline:
        _, data = request_json("GET","http://notification:8000/notifications?order_id="+state["order_id"],
            api_key=os.environ.get("SERVICE_API_KEY","local-demo-service-key"))
        rows = data.get("notifications",[])
        if rows:
            require(len(rows)==1,"Expected one notification")
            require(rows[0]["correlation_id"]==state["key"],"Correlation ID did not survive recovery")
            require(rows[0]["event_id"]==state["event_id"],"Event ID did not survive recovery")
            return
        time.sleep(0.5)
    raise RuntimeError("Notification did not arrive after recovery")


def prepare(name):
    state={"key":"resilience-"+str(uuid.uuid4()),"body":{
        "customer_id":"demo-customer","sku":"LAMP","quantity":1,"payment_token":"mock-success"}}
    status, order = place(state)
    require(status==201 and order["status"]=="CONFIRMED","Checkout did not confirm")
    if name=="outage":
        require(not order["event_published"],"Stop RabbitMQ before the outage preparation check")
    state.update(order_id=order["order_id"],event_id=order["event_id"])
    location(name).write_text(encode(state))
    print("PASS: confirmed order persisted" if name=="restart" else
          "PASS: checkout confirmed while RabbitMQ was stopped; event retained in outbox",flush=True)


def check(name):
    state=json.loads(location(name).read_text())
    status, order=place(state)
    require(status==200 and order["replayed"] and order["order_id"]==state["order_id"],
            "Restart/recovery lost request idempotency")
    wait_for_notification(state)
    print("PASS: service restart preserved order/idempotency and delivered its event" if name=="restart" else
          "PASS: broker recovery delivered the retained event with matching IDs",flush=True)


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ("restart-prepare","restart-check","outage-prepare","outage-check"):
        raise SystemExit("Use restart-prepare, restart-check, outage-prepare or outage-check")
    name,action=sys.argv[1].split("-")
    (prepare if action=="prepare" else check)(name)


if __name__=="__main__":
    main()
