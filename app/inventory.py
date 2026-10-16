from urllib.parse import urlparse
from .common import ApiError, Database, Service, fingerprint, int_field, log, text_field

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock (
    sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available>=0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents>0));
INSERT OR IGNORE INTO stock VALUES ('HEADPHONES',100,4999);
INSERT OR IGNORE INTO stock VALUES ('LAMP',100,2999);
CREATE TABLE IF NOT EXISTS reservations (
    order_id TEXT PRIMARY KEY, request_hash TEXT, sku TEXT,
    quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL, status TEXT NOT NULL);
"""


class Inventory(Service):
    def __init__(self, path):
        super().__init__("inventory", Database(path, SCHEMA))

    def reserve(self, body, cid):
        order_id = text_field(body, "order_id")
        sku = text_field(body, "sku")
        quantity = int_field(body, "quantity")
        digest = fingerprint({"sku": sku, "quantity": quantity})
        with self.db.transaction() as connection:
            existing = connection.execute("SELECT * FROM reservations WHERE order_id=?", (order_id,)).fetchone()
            if existing:
                if existing["status"] == "RELEASED":
                    raise ApiError(409, "RESERVATION_RELEASED")
                if existing["request_hash"] != digest:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT")
                return dict(existing)
            item = connection.execute("SELECT * FROM stock WHERE sku=?", (sku,)).fetchone()
            if item is None or item["available"] < quantity:
                raise ApiError(409, "INSUFFICIENT_STOCK")
            connection.execute("UPDATE stock SET available=available-? WHERE sku=?", (quantity, sku))
            connection.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?)",
                               (order_id, digest, sku, quantity, item["unit_price_cents"], "RESERVED"))
            result = dict(connection.execute("SELECT * FROM reservations WHERE order_id=?", (order_id,)).fetchone())
        log(self.name, "stock_reserved", cid, order_id=order_id)
        return result

    def release(self, order_id, cid):
        with self.db.transaction() as connection:
            item = connection.execute("SELECT * FROM reservations WHERE order_id=?", (order_id,)).fetchone()
            if item and item["status"] == "RESERVED":
                connection.execute("UPDATE stock SET available=available+? WHERE sku=?", (item["quantity"], item["sku"]))
                connection.execute("UPDATE reservations SET status='RELEASED' WHERE order_id=?", (order_id,))
            elif item is None:
                # A tombstone prevents a delayed reserve request after cancellation.
                connection.execute("INSERT INTO reservations VALUES (?,NULL,NULL,0,0,'RELEASED')", (order_id,))
        log(self.name, "stock_released", cid, order_id=order_id)
        return {"order_id": order_id, "status": "RELEASED"}

    def route(self, method, path, body, headers, cid):
        parts = urlparse(path).path.strip("/").split("/")
        if method == "POST" and parts == ["reservations"]:
            return 200, self.reserve(body, cid)
        if method == "POST" and len(parts) == 3 and parts[0] == "reservations" and parts[2] == "release":
            return 200, self.release(parts[1], cid)
        if method == "GET" and len(parts) == 2 and parts[0] == "stock":
            row = self.db.one("SELECT * FROM stock WHERE sku=?", (parts[1],))
            if row:
                return 200, row
        raise ApiError(404, "NOT_FOUND")
