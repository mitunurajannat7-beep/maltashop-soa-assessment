"""Small HTTP and SQLite infrastructure; no external web framework required."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def log(service, event, correlation_id="system", level="INFO", **fields):
    # Callers provide identifiers and error codes, never tokens or customer bodies.
    print(encode(dict(timestamp=now(), service=service, level=level,
                      event=event, correlation_id=correlation_id, **fields)), flush=True)


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class DependencyUnavailable(Exception):
    pass


def text_field(body, name, max_length=100):
    value = body.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ApiError(400, "INVALID_" + name.upper())
    return value


def int_field(body, name, minimum=1, maximum=100):
    value = body.get(name)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ApiError(400, "INVALID_" + name.upper())
    return value


class Database:
    def __init__(self, path, schema):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(schema)
            connection.commit()
        finally:
            connection.close()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rows(self, sql, parameters=()):
        connection = self.connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()

    def one(self, sql, parameters=()):
        rows = self.rows(sql, parameters)
        return rows[0] if rows else None


class Service:
    def __init__(self, name, db):
        self.name, self.db = name, db
        self.stop_event = threading.Event()
        self.threads = []
        self.api_key = os.environ.get("SERVICE_API_KEY", "local-demo-service-key")

    def start_thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()

    def start(self):
        pass

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=8)

    def handle(self, method, path, body, headers, correlation_id):
        if method == "GET" and path == "/health":
            self.db.one("SELECT 1 AS ok")
            return 200, {"service": self.name, "status": "healthy"}
        if self.name != "order" and headers.get("X-Service-Key") != self.api_key:
            raise ApiError(401, "SERVICE_KEY_REQUIRED")
        return self.route(method, path, body, headers, correlation_id)


def request_json(method, url, payload=None, correlation_id="system", api_key=None, timeout=3):
    data = None if payload is None else encode(payload).encode()
    headers = {"Content-Type": "application/json", "X-Correlation-ID": correlation_id}
    if api_key:
        headers["X-Service-Key"] = api_key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except (ValueError, OSError) as cause:
            raise DependencyUnavailable("INVALID_DEPENDENCY_RESPONSE") from cause
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as cause:
        raise DependencyUnavailable("DEPENDENCY_UNAVAILABLE") from cause


def make_server(service, host="0.0.0.0", port=8000):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            correlation_id = self.headers.get("X-Correlation-ID") or str(uuid.uuid4())
            try:
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", correlation_id):
                    correlation_id = str(uuid.uuid4())
                    raise ApiError(400, "INVALID_CORRELATION_ID")
                body = {}
                if self.command == "POST":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError as cause:
                        raise ApiError(400, "INVALID_CONTENT_LENGTH") from cause
                    if not 0 < length <= 65536:
                        raise ApiError(400, "INVALID_BODY_LENGTH")
                    try:
                        body = json.loads(self.rfile.read(length))
                    except (ValueError, UnicodeDecodeError) as cause:
                        raise ApiError(400, "INVALID_JSON") from cause
                    if not isinstance(body, dict):
                        raise ApiError(400, "JSON_OBJECT_REQUIRED")
                status, result = service.handle(self.command, self.path, body,
                                                self.headers, correlation_id)
            except ApiError as error:
                status, result = error.status, {"error": error.code}
            except Exception:
                log(service.name, "unhandled_error", correlation_id, level="ERROR")
                status, result = 500, {"error": "INTERNAL_ERROR"}
            data = encode(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Correlation-ID", correlation_id)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            log(service.name, "http_response", correlation_id, status=status,
                method=self.command, path=self.path.split("?")[0])

        do_GET = dispatch
        do_POST = dispatch

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
