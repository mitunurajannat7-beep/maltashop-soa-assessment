"""Persisted order saga and transactional outbox.

This educational implementation uses one process per service. Striped in-process
locks protect saga execution here; production replicas need database-backed leases.
"""
import json
import os
import threading
import uuid
from urllib.parse import urlparse
from .common import (ApiError, Database, DependencyUnavailable, Service, encode,
                     fingerprint, int_field, log, now, request_json, text_field)

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
    request_hash TEXT NOT NULL, customer_id TEXT NOT NULL, sku TEXT NOT NULL,
    quantity INTEGER NOT NULL, payment_token TEXT NOT NULL,
    amount_cents INTEGER, status TEXT NOT NULL, stage TEXT NOT NULL,
    reason TEXT, correlation_id TEXT NOT NULL, created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL REFERENCES orders(order_id),
    payload TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
"""


class Order(Service):
    def __init__(self, path, transport=None):
        super().__init__("order", Database(path, SCHEMA))
        self.transport = transport or self.http_transport
        self.inventory_url = os.environ.get("INVENTORY_URL", "http://inventory:8000")
        self.payment_url = os.environ.get("PAYMENT_URL", "http://payment:8000")
        self.locks = [threading.RLock() for _ in range(64)]

    def http_transport(self, target, path, body, cid):
        base = self.inventory_url if target == "inventory" else self.payment_url
        return request_json("POST", base + path, body, cid, self.api_key)

    def get(self, order_id):
        row = self.db.one("""SELECT order_id,customer_id,sku,quantity,amount_cents,
            status,stage,reason,correlation_id,created_at,attempts,last_error
            FROM orders WHERE order_id=?""", (order_id,))
        if not row:
            raise ApiError(404, "ORDER_NOT_FOUND")
        event = self.db.one("SELECT event_id,published FROM outbox WHERE order_id=?", (order_id,))
        row["event_id"] = event["event_id"] if event else None
        row["event_published"] = bool(event and event["published"])
        row["currency"] = "EUR"
        return row

    def create(self, body, key, cid):
        if set(body) != {"customer_id", "sku", "quantity", "payment_token"}:
            raise ApiError(400, "EXPECTED_CUSTOMER_SKU_QUANTITY_PAYMENT_TOKEN")
        customer = text_field(body, "customer_id")
        sku = text_field(body, "sku")
        quantity = int_field(body, "quantity")
        token = text_field(body, "payment_token")
        if token not in ("mock-success", "mock-decline"):
            raise ApiError(400, "ONLY_MOCK_TOKENS_ACCEPTED")
        if not key or len(key) > 128:
            raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED_MAX_128")
        digest = fingerprint(body)
        with self.db.transaction() as connection:
            existing = connection.execute("SELECT * FROM orders WHERE idempotency_key=?", (key,)).fetchone()
            replayed = existing is not None
            if existing:
                if existing["request_hash"] != digest:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT")
                order_id = existing["order_id"]
            else:
                order_id = str(uuid.uuid4())
                connection.execute("""INSERT INTO orders
                    (order_id,idempotency_key,request_hash,customer_id,sku,quantity,
                     payment_token,status,stage,correlation_id,created_at)
                    VALUES (?,?,?,?,?,?,?,'PENDING','RESERVE',?,?)""",
                    (order_id,key,digest,customer,sku,quantity,token,cid,now()))
        self.run_saga(order_id)
        result = self.get(order_id)
        result["replayed"] = replayed
        if result["status"] == "PENDING":
            status = 202
        elif result["status"] == "REJECTED":
            status = 409
        else:
            status = 200 if replayed else 201
        return status, result

    def transition(self, order_id, stage, reason=None, amount=None):
        with self.db.transaction() as connection:
            connection.execute("""UPDATE orders SET stage=?,reason=?,
                amount_cents=COALESCE(?,amount_cents),last_error=NULL WHERE order_id=?""",
                (stage, reason, amount, order_id))

    def reject(self, order_id, reason, cid):
        with self.db.transaction() as connection:
            connection.execute("""UPDATE orders SET status='REJECTED',stage='DONE',
                reason=?,last_error=NULL WHERE order_id=?""", (reason, order_id))
        log(self.name, "order_rejected", cid, order_id=order_id, reason=reason)

    def confirm(self, order):
        event = {
            "event_id": str(uuid.uuid4()), "event_type": "OrderPlaced",
            "schema_version": 1, "occurred_at": now(),
            "correlation_id": order["correlation_id"],
            "data": {k: order[k] for k in ("order_id","customer_id","sku","quantity","amount_cents")},
        }
        event["data"]["currency"] = "EUR"
        # Local transaction: confirmation and its eventual outbound event are atomic.
        with self.db.transaction() as connection:
            connection.execute("UPDATE orders SET status='CONFIRMED',stage='DONE',last_error=NULL WHERE order_id=?",
                               (order["order_id"],))
            connection.execute("INSERT INTO outbox(event_id,order_id,payload) VALUES (?,?,?)",
                               (event["event_id"], order["order_id"], encode(event)))
        log(self.name, "order_confirmed", order["correlation_id"],
            order_id=order["order_id"], event_id=event["event_id"])

    def run_saga(self, order_id):
        with self.locks[hash(order_id) % len(self.locks)]:
            try:
                for _ in range(5):
                    order = self.db.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
                    if not order or order["status"] != "PENDING":
                        return
                    cid = order["correlation_id"]
                    if order["stage"] == "RESERVE":
                        status, result = self.transport("inventory", "/reservations",
                            {"order_id":order_id,"sku":order["sku"],"quantity":order["quantity"]}, cid)
                        if status == 409 and result.get("error") == "INSUFFICIENT_STOCK":
                            self.reject(order_id, "INSUFFICIENT_STOCK", cid)
                            return
                        if status != 200 or result.get("status") != "RESERVED":
                            raise DependencyUnavailable("RESERVATION_NEEDS_RECONCILIATION")
                        self.transition(order_id, "PAYMENT", amount=result["unit_price_cents"] * order["quantity"])
                    elif order["stage"] == "PAYMENT":
                        status, result = self.transport("payment", "/payments",
                            {"order_id":order_id,"amount_cents":order["amount_cents"],
                             "payment_token":order["payment_token"]}, cid)
                        if status == 409 and result.get("error") == "PAYMENT_DECLINED":
                            self.transition(order_id, "COMPENSATE", reason="PAYMENT_DECLINED")
                        elif status == 200 and result.get("status") == "AUTHORIZED":
                            self.transition(order_id, "CONFIRM")
                        else:
                            # A timeout is an UNKNOWN outcome, never proof of rejection.
                            raise DependencyUnavailable("PAYMENT_NEEDS_RECONCILIATION")
                    elif order["stage"] == "COMPENSATE":
                        for target, path in (("payment", f"/payments/{order_id}/cancel"),
                                             ("inventory", f"/reservations/{order_id}/release")):
                            status, _ = self.transport(target, path, {}, cid)
                            if status != 200:
                                raise DependencyUnavailable("COMPENSATION_PENDING")
                        self.reject(order_id, order["reason"], cid)
                        return
                    elif order["stage"] == "CONFIRM":
                        self.confirm(order)
                        return
                    else:
                        raise DependencyUnavailable("INVALID_SAGA_STAGE")
            except (DependencyUnavailable, OSError) as error:
                # Persist the stage; recovery retries the same idempotent operations.
                with self.db.transaction() as connection:
                    connection.execute("UPDATE orders SET attempts=attempts+1,last_error=? WHERE order_id=?",
                                       ("DEPENDENCY_OR_RECONCILIATION_PENDING", order_id))
                log(self.name, "saga_pending", order["correlation_id"], level="WARNING",
                    order_id=order_id, stage=order["stage"])

    def start(self):
        self.start_thread(self.recovery_loop)
        self.start_thread(self.outbox_loop)

    def recovery_loop(self):
        while not self.stop_event.wait(2):
            try:
                for row in self.db.rows("SELECT order_id FROM orders WHERE status='PENDING' ORDER BY created_at LIMIT 20"):
                    if self.stop_event.is_set():
                        return
                    self.run_saga(row["order_id"])
            except Exception:
                log(self.name, "recovery_retry", level="ERROR")

    def outbox_loop(self):
        from .broker import publish
        while not self.stop_event.wait(1):
            try:
                for row in self.db.rows("SELECT * FROM outbox WHERE published=0 LIMIT 20"):
                    event = json.loads(row["payload"])
                    publish(event)
                    with self.db.transaction() as connection:
                        connection.execute("UPDATE outbox SET published=1 WHERE event_id=?", (row["event_id"],))
                    log(self.name, "event_published", event["correlation_id"],
                        order_id=row["order_id"], event_id=row["event_id"])
            except Exception:
                # The outbox remains durable and checkout has already returned.
                log(self.name, "outbox_retry", level="WARNING")

    def route(self, method, path, body, headers, cid):
        parts = urlparse(path).path.strip("/").split("/")
        if method == "POST" and parts == ["orders"]:
            return self.create(body, headers.get("Idempotency-Key"), cid)
        if method == "GET" and len(parts) == 2 and parts[0] == "orders":
            return 200, self.get(parts[1])
        raise ApiError(404, "NOT_FOUND")
