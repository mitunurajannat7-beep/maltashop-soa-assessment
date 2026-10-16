from urllib.parse import urlparse
from .common import ApiError, Database, Service, fingerprint, int_field, log, text_field

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    order_id TEXT PRIMARY KEY, request_hash TEXT,
    amount_cents INTEGER NOT NULL, status TEXT NOT NULL);
"""


class Payment(Service):
    def __init__(self, path):
        super().__init__("payment", Database(path, SCHEMA))

    def authorize(self, body, cid):
        order_id = text_field(body, "order_id")
        amount = int_field(body, "amount_cents", maximum=10000000)
        token = text_field(body, "payment_token")
        if token not in ("mock-success", "mock-decline"):
            raise ApiError(400, "ONLY_MOCK_TOKENS_ACCEPTED")
        digest = fingerprint({"amount_cents": amount, "payment_token": token})
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
            if row:
                if row["status"] == "CANCELLED":
                    raise ApiError(409, "PAYMENT_CANCELLED")
                if row["request_hash"] != digest:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT")
                result = dict(row)
            else:
                state = "AUTHORIZED" if token == "mock-success" else "DECLINED"
                connection.execute("INSERT INTO payments VALUES (?,?,?,?)", (order_id, digest, amount, state))
                result = {"order_id": order_id, "amount_cents": amount, "status": state}
        log(self.name, "payment_" + result["status"].lower(), cid, order_id=order_id)
        if result["status"] == "DECLINED":
            raise ApiError(409, "PAYMENT_DECLINED")
        return {k: result[k] for k in ("order_id", "amount_cents", "status")}

    def cancel(self, order_id, cid):
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
            if row is None:
                connection.execute("INSERT INTO payments VALUES (?,NULL,0,'CANCELLED')", (order_id,))
            elif row["status"] == "AUTHORIZED":
                connection.execute("UPDATE payments SET status='CANCELLED' WHERE order_id=?", (order_id,))
        log(self.name, "payment_compensated", cid, order_id=order_id)
        return {"order_id": order_id, "status": "CANCELLED_OR_NO_AUTHORIZATION"}

    def route(self, method, path, body, headers, cid):
        parts = urlparse(path).path.strip("/").split("/")
        if method == "POST" and parts == ["payments"]:
            return 200, self.authorize(body, cid)
        if method == "POST" and len(parts) == 3 and parts[0] == "payments" and parts[2] == "cancel":
            return 200, self.cancel(parts[1], cid)
        if method == "GET" and len(parts) == 2 and parts[0] == "payments":
            row = self.db.one("SELECT order_id,amount_cents,status FROM payments WHERE order_id=?", (parts[1],))
            if row:
                return 200, row
        raise ApiError(404, "NOT_FOUND")
