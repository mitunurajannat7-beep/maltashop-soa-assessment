import os
import signal
import threading
from .common import log, make_server
from .inventory import Inventory
from .notification import Notification
from .order import Order
from .payment import Payment


def main():
    role = os.environ.get("SERVICE_NAME", "order")
    classes = {"order":Order,"inventory":Inventory,"payment":Payment,"notification":Notification}
    if role not in classes:
        raise SystemExit("Invalid SERVICE_NAME")
    service = classes[role](os.environ.get("DB_PATH", "/data/service.db"))
    server = make_server(service, port=int(os.environ.get("PORT", "8000")))
    def shutdown(*args):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    service.start()
    log(role, "service_started")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        service.stop()


if __name__ == "__main__":
    main()
