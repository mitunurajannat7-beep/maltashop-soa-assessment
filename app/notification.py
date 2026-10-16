from urllib.parse import parse_qs, urlparse
from .common import ApiError, Database, Service, log, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
    event_id TEXT PRIMARY KEY REFERENCES processed_events(event_id),
    order_id TEXT NOT NULL UNIQUE, correlation_id TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class Notification(Service):
    def __init__(self, path):
        super().__init__("notification", Database(path, SCHEMA))
        self.broker_connected = False

    def consume(self, event):
        data = event["data"]
        with self.db.transaction() as connection:
            duplicate = connection.execute("SELECT event_id FROM processed_events WHERE event_id=?",
                                           (event["event_id"],)).fetchone() is not None
            if not duplicate:
                connection.execute("INSERT INTO processed_events VALUES (?,?)", (event["event_id"], now()))
                connection.execute("INSERT INTO notifications VALUES (?,?,?,'SIMULATED_SENT',?)",
                    (event["event_id"],data["order_id"],event["correlation_id"],now()))
        log(self.name, "duplicate_event_ignored" if duplicate else "event_consumed",
            event["correlation_id"], order_id=data["order_id"], event_id=event["event_id"])
        return not duplicate

    def start(self):
        self.start_thread(self.consume_loop)

    def consume_loop(self):
        from .broker import QUEUE, connect, declare, decode_event
        while not self.stop_event.is_set():
            connection = None
            try:
                connection = connect()
                channel = connection.channel()
                declare(channel)
                channel.basic_qos(prefetch_count=10)
                self.broker_connected = True
                for method, properties, body in channel.consume(QUEUE, inactivity_timeout=1, auto_ack=False):
                    if self.stop_event.is_set():
                        break
                    if method is None:
                        continue
                    try:
                        event = decode_event(body)
                    except ValueError:
                        log(self.name, "invalid_event_dead_lettered", level="ERROR")
                        channel.basic_nack(method.delivery_tag, requeue=False)
                        continue
                    try:
                        self.consume(event)
                    except Exception:
                        log(self.name, "consumer_transaction_retry", event["correlation_id"], level="ERROR")
                        channel.basic_nack(method.delivery_tag, requeue=True)
                        self.stop_event.wait(1)
                    else:
                        # Commit inbox and business effect BEFORE acknowledging delivery.
                        channel.basic_ack(method.delivery_tag)
            except Exception:
                log(self.name, "broker_reconnect", level="WARNING")
                self.stop_event.wait(2)
            finally:
                self.broker_connected = False
                if connection and connection.is_open:
                    try:
                        connection.close()
                    except Exception:
                        pass

    def route(self, method, path, body, headers, cid):
        parsed = urlparse(path)
        if method == "GET" and parsed.path == "/notifications":
            order_id = parse_qs(parsed.query).get("order_id", [""])[0]
            return 200, {"notifications":self.db.rows(
                "SELECT * FROM notifications WHERE order_id=?", (order_id,))}
        raise ApiError(404, "NOT_FOUND")
