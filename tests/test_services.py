"""Unit, saga-component, contract and real local HTTP integration tests.

Broker I/O is mocked in unit tests. Compose smoke tests separately exercise real
RabbitMQ; do not describe the mocked tests as broker integration evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch
import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from app.broker import decode_event, publish
from app.common import ApiError, DependencyUnavailable, encode, make_server
from app.inventory import Inventory
from app.notification import Notification
from app.order import Order
from app.payment import Payment

BODY = {"customer_id":"demo-customer","sku":"HEADPHONES","quantity":1,"payment_token":"mock-success"}


class ServicesTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.inventory = Inventory(root / "inventory.db")
        self.payment = Payment(root / "payment.db")
        self.notification = Notification(root / "notification.db")
        self.order = Order(root / "order.db", transport=self.transport)

    def transport(self, target, path, body, cid):
        service = self.inventory if target == "inventory" else self.payment
        try:
            return service.handle("POST",path,body,{"X-Service-Key":service.api_key},cid)
        except ApiError as error:
            return error.status, {"error":error.code}

    def place(self, key="test-order-1", **changes):
        return self.order.create(dict(BODY, **changes), key, "test-correlation-1")

    def count(self, service, table):
        return service.db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]

    def test_happy_path_uses_inventory_price_and_atomic_outbox(self):
        status, result = self.place()
        self.assertEqual(status,201)
        self.assertEqual(result["status"],"CONFIRMED")
        self.assertEqual(result["amount_cents"],4999)
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='HEADPHONES'")["available"],99)
        self.assertEqual(self.count(self.payment,"payments"),1)
        self.assertEqual(self.count(self.order,"outbox"),1)
        self.assertFalse(result["event_published"])

    def test_same_key_same_payload_returns_same_order_without_side_effects(self):
        _, first = self.place()
        status, second = self.place()
        self.assertEqual(status,200)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["order_id"],second["order_id"])
        for service,table in ((self.order,"orders"),(self.order,"outbox"),
                              (self.inventory,"reservations"),(self.payment,"payments")):
            self.assertEqual(self.count(service,table),1)

    def test_failed_outbox_write_rolls_back_confirmation_and_can_recover(self):
        with self.order.db.transaction() as connection:
            connection.execute("""CREATE TRIGGER simulate_outbox_failure BEFORE INSERT ON outbox
                BEGIN SELECT RAISE(ABORT,'simulated outbox write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.place()
        row = self.order.db.one("SELECT order_id,status,stage FROM orders")
        self.assertEqual((row["status"],row["stage"]),("PENDING","CONFIRM"))
        self.assertEqual(self.count(self.order,"outbox"),0)
        self.assertEqual(self.payment.db.one("SELECT status FROM payments")["status"],"AUTHORIZED")
        with self.order.db.transaction() as connection:
            connection.execute("DROP TRIGGER simulate_outbox_failure")
        self.order.run_saga(row["order_id"])
        self.assertEqual(self.order.get(row["order_id"])["status"],"CONFIRMED")
        self.assertEqual(self.count(self.order,"outbox"),1)

    def test_same_key_different_payload_is_conflict(self):
        self.place()
        with self.assertRaises(ApiError) as caught:
            self.place(quantity=2)
        self.assertEqual(caught.exception.code,"IDEMPOTENCY_CONFLICT")

    def test_decline_compensates_stock_and_does_not_emit_confirmation(self):
        status, result = self.place(payment_token="mock-decline")
        self.assertEqual(status,409)
        self.assertEqual(result["reason"],"PAYMENT_DECLINED")
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='HEADPHONES'")["available"],100)
        self.assertEqual(self.count(self.order,"outbox"),0)

    def test_insufficient_stock_does_not_attempt_payment(self):
        status, result = self.place(sku="MISSING")
        self.assertEqual(status,409)
        self.assertEqual(result["reason"],"INSUFFICIENT_STOCK")
        self.assertEqual(self.count(self.payment,"payments"),0)

    def test_timeout_after_payment_side_effect_remains_pending_then_recovers(self):
        original = self.transport
        dropped = False
        def unreliable(target,path,body,cid):
            nonlocal dropped
            result = original(target,path,body,cid)
            if target == "payment" and path == "/payments" and not dropped:
                dropped = True
                raise DependencyUnavailable("simulated lost response AFTER authorization")
            return result
        self.order.transport = unreliable
        status, result = self.place()
        self.assertEqual(status,202)
        self.assertEqual(result["stage"],"PAYMENT")
        self.assertEqual(self.payment.db.one("SELECT status FROM payments")["status"],"AUTHORIZED")
        # Re-create coordinator to prove progress is recoverable from durable state.
        recovered = Order(self.order.db.path, transport=original)
        recovered.run_saga(result["order_id"])
        self.assertEqual(recovered.get(result["order_id"])["status"],"CONFIRMED")
        self.assertEqual(self.count(self.payment,"payments"),1)
        self.assertEqual(self.count(self.inventory,"reservations"),1)

    def test_interrupted_compensation_can_be_repeated(self):
        original = self.transport
        def unreliable(target,path,body,cid):
            if target == "inventory" and path.endswith("/release"):
                raise DependencyUnavailable("simulated inventory outage")
            return original(target,path,body,cid)
        self.order.transport = unreliable
        status, result = self.place(payment_token="mock-decline")
        self.assertEqual(status,202)
        self.assertEqual(result["stage"],"COMPENSATE")
        self.order.transport = original
        self.order.run_saga(result["order_id"])
        self.order.run_saga(result["order_id"])
        self.assertEqual(self.order.get(result["order_id"])["status"],"REJECTED")
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='HEADPHONES'")["available"],100)

    def test_inventory_release_is_idempotent(self):
        self.inventory.reserve({"order_id":"reservation-1","sku":"LAMP","quantity":3},"cid")
        self.inventory.release("reservation-1","cid")
        self.inventory.release("reservation-1","cid")
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='LAMP'")["available"],100)

    def test_cancellation_tombstones_block_late_side_effects(self):
        self.inventory.release("late-order","cid")
        with self.assertRaises(ApiError):
            self.inventory.reserve({"order_id":"late-order","sku":"LAMP","quantity":1},"cid")
        self.payment.cancel("late-order","cid")
        with self.assertRaises(ApiError):
            self.payment.authorize({"order_id":"late-order","amount_cents":100,"payment_token":"mock-success"},"cid")
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='LAMP'")["available"],100)

    def test_payment_cancellation_is_idempotent(self):
        body = {"order_id":"pay-1","amount_cents":100,"payment_token":"mock-success"}
        self.payment.authorize(body,"cid")
        self.payment.authorize(body,"cid")
        self.payment.cancel("pay-1","cid")
        self.payment.cancel("pay-1","cid")
        self.assertEqual(self.count(self.payment,"payments"),1)
        self.assertEqual(self.payment.db.one("SELECT status FROM payments")["status"],"CANCELLED")

    def test_order_to_notification_v1_consumer_contract(self):
        _, result = self.place()
        payload = self.order.db.one("SELECT payload FROM outbox")["payload"]
        event = json.loads(payload)
        contract = json.loads((Path(__file__).parents[1]/"contracts"/"order-placed.schema.json").read_text())
        self.assertTrue(set(contract["required"]).issubset(event))
        self.assertTrue(set(contract["properties"]["data"]["required"]).issubset(event["data"]))
        self.assertEqual(event["schema_version"],1)
        accepted = decode_event(payload)
        self.assertTrue(self.notification.consume(accepted))
        notification = self.notification.db.one("SELECT * FROM notifications")
        self.assertEqual(notification["order_id"],result["order_id"])
        self.assertEqual(notification["correlation_id"],result["correlation_id"])

    def test_duplicate_event_is_ignored_after_consumer_restart(self):
        self.place()
        event = decode_event(self.order.db.one("SELECT payload FROM outbox")["payload"])
        self.notification.consume(event)
        restarted = Notification(self.notification.db.path)
        self.assertFalse(restarted.consume(event))
        self.assertEqual(self.count(restarted,"notifications"),1)
        self.assertEqual(self.count(restarted,"processed_events"),1)

    def test_unsupported_or_malformed_event_is_rejected(self):
        for payload in ("{}","[]","not JSON",'{"schema_version":2}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_event(payload)

    def test_invalid_quantity_and_real_payment_tokens_are_rejected(self):
        for quantity in (True,0,-1,101,1.5,"1"):
            with self.subTest(quantity=quantity), self.assertRaises(ApiError):
                self.place(quantity=quantity)
        with self.assertRaises(ApiError):
            self.place(payment_token="real-card-data-not-supported")

    def test_internal_services_require_the_service_key(self):
        with self.assertRaises(ApiError) as caught:
            self.payment.handle("POST","/payments",{}, {},"cid")
        self.assertEqual(caught.exception.status,401)

    def test_concurrent_identical_requests_have_one_effect(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _:self.place(),range(6)))
        self.assertEqual(len({result[1]["order_id"] for result in results}),1)
        self.assertEqual(self.count(self.order,"outbox"),1)
        self.assertEqual(self.count(self.payment,"payments"),1)

    def test_concurrent_stock_reservations_cannot_oversell(self):
        def reserve(index):
            try:
                self.inventory.reserve({"order_id":f"stock-{index}","sku":"LAMP","quantity":30},"cid")
                return True
            except ApiError:
                return False
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(reserve,range(5)))
        self.assertEqual(sum(results),3)
        self.assertEqual(self.inventory.db.one("SELECT available FROM stock WHERE sku='LAMP'")["available"],10)

    def test_real_http_order_inventory_and_payment_integration(self):
        servers = []
        for service in (self.inventory,self.payment,self.order):
            server = make_server(service,"127.0.0.1",0)
            thread = threading.Thread(target=server.serve_forever,kwargs={"poll_interval":0.05},daemon=True)
            thread.start()
            servers.append((server,thread))
        try:
            self.order.inventory_url = f"http://127.0.0.1:{servers[0][0].server_port}"
            self.order.payment_url = f"http://127.0.0.1:{servers[1][0].server_port}"
            self.order.transport = self.order.http_transport
            url = f"http://127.0.0.1:{servers[2][0].server_port}/orders"
            request = urllib.request.Request(url,data=encode(BODY).encode(),headers={
                "Content-Type":"application/json","Idempotency-Key":"http-order-1",
                "X-Correlation-ID":"http-integration-cid"},method="POST")
            with urllib.request.urlopen(request,timeout=10) as response:
                result = json.loads(response.read())
                self.assertEqual(response.status,201)
                self.assertEqual(result["status"],"CONFIRMED")
                self.assertEqual(response.headers["X-Correlation-ID"],"http-integration-cid")
            bad = urllib.request.Request(url,data=b"[]",headers={"Content-Type":"application/json"},method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(bad,timeout=10)
            self.assertEqual(caught.exception.code,400)
        finally:
            for server,thread in servers:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_publisher_enables_confirms_persistence_and_mandatory_routing(self):
        self.place()
        event = decode_event(self.order.db.one("SELECT payload FROM outbox")["payload"])
        connection = Mock()
        with patch("app.broker.connect",return_value=connection):
            publish(event)
        channel = connection.channel.return_value
        channel.confirm_delivery.assert_called_once()
        args = channel.basic_publish.call_args.kwargs
        self.assertTrue(args["mandatory"])
        self.assertEqual(args["properties"].delivery_mode,2)
        self.assertEqual(args["properties"].correlation_id,event["correlation_id"])

    def test_consumer_commits_before_acknowledgement_and_rejects_bad_message(self):
        self.place()
        payload = self.order.db.one("SELECT payload FROM outbox")["payload"].encode()
        connection = Mock()
        channel = connection.channel.return_value
        good = SimpleNamespace(delivery_tag=1)
        bad = SimpleNamespace(delivery_tag=2)
        def deliveries(*args,**kwargs):
            yield good,None,payload
            yield bad,None,b"{}"
            self.notification.stop_event.set()
        channel.consume.side_effect = deliveries
        def ack(tag):
            self.assertEqual(tag,1)
            self.assertEqual(self.count(self.notification,"notifications"),1)
            self.assertEqual(self.count(self.notification,"processed_events"),1)
        channel.basic_ack.side_effect = ack
        with patch("app.broker.connect",return_value=connection):
            self.notification.consume_loop()
        channel.basic_ack.assert_called_once_with(1)
        channel.basic_nack.assert_called_once_with(2,requeue=False)


if __name__ == "__main__":
    main()
