import json
import os
import sqlite3
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT / "pins.db"))).resolve()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(create_pins_table_sql())
        migrate_legacy_type_check(conn)
        conn.commit()
    finally:
        conn.close()


def create_pins_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS pins (
          id TEXT PRIMARY KEY,
          lat REAL NOT NULL,
          lng REAL NOT NULL,
          type TEXT NOT NULL,
          comment TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def migrate_legacy_type_check(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pins'"
    ).fetchone()
    if not row or not row[0]:
        return

    table_sql = row[0]
    legacy_check = "CHECK (type IN ('good', 'bad'))"
    if legacy_check not in table_sql:
        return

    conn.execute("ALTER TABLE pins RENAME TO pins_old")
    conn.execute(create_pins_table_sql())
    conn.execute(
        """
        INSERT INTO pins (id, lat, lng, type, comment, created_at, updated_at)
        SELECT id, lat, lng, type, comment, created_at, updated_at
        FROM pins_old
        """
    )
    conn.execute("DROP TABLE pins_old")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # Development-friendly cache policy so phones always fetch fresh JS/CSS.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            self.write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/pins":
            self.handle_get_pins()
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/pins":
            self.handle_create_pin()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        path = urlparse(self.path).path
        if path.startswith("/api/pins/"):
            pin_id = path.removeprefix("/api/pins/").strip()
            if not pin_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "Missing pin id")
                return
            self.handle_update_pin(pin_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/pins":
            self.handle_delete_all_pins()
            return
        if path.startswith("/api/pins/"):
            pin_id = path.removeprefix("/api/pins/").strip()
            if not pin_id:
                self.send_error(HTTPStatus.BAD_REQUEST, "Missing pin id")
                return
            self.handle_delete_pin(pin_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_get_pins(self):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, lat, lng, type, comment FROM pins ORDER BY created_at ASC"
            ).fetchall()
            pins = [dict(row) for row in rows]
            self.write_json(HTTPStatus.OK, {"pins": pins})
        finally:
            conn.close()

    def handle_create_pin(self):
        payload = self.read_json()
        if payload is None:
            return

        pin_id = payload.get("id")
        lat = payload.get("lat")
        lng = payload.get("lng")
        pin_type = payload.get("type")
        comment = payload.get("comment", "")

        if (
            not isinstance(pin_id, str)
            or not isinstance(lat, (int, float))
            or not isinstance(lng, (int, float))
            or not isinstance(pin_type, str)
            or not pin_type.strip()
            or not isinstance(comment, str)
        ):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid pin payload"})
            return

        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO pins (id, lat, lng, type, comment)
                VALUES (?, ?, ?, ?, ?)
                """,
                (pin_id, float(lat), float(lng), pin_type.strip(), comment[:300]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, lat, lng, type, comment FROM pins WHERE id = ?", (pin_id,)
            ).fetchone()
            self.write_json(HTTPStatus.CREATED, dict(row) if row else {})
        except sqlite3.IntegrityError:
            self.write_json(HTTPStatus.CONFLICT, {"error": "Pin id already exists"})
        finally:
            conn.close()

    def handle_update_pin(self, pin_id: str):
        payload = self.read_json()
        if payload is None:
            return

        comment = payload.get("comment")
        if not isinstance(comment, str):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid comment payload"})
            return

        conn = get_conn()
        try:
            cursor = conn.execute(
                """
                UPDATE pins
                SET comment = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (comment[:300], pin_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            row = conn.execute(
                "SELECT id, lat, lng, type, comment FROM pins WHERE id = ?", (pin_id,)
            ).fetchone()
            self.write_json(HTTPStatus.OK, dict(row) if row else {})
        finally:
            conn.close()

    def handle_delete_pin(self, pin_id: str):
        conn = get_conn()
        try:
            cursor = conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
            conn.commit()
            if cursor.rowcount == 0:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            self.write_json(HTTPStatus.OK, {"deleted": 1})
        finally:
            conn.close()

    def handle_delete_all_pins(self):
        conn = get_conn()
        try:
            cursor = conn.execute("DELETE FROM pins")
            conn.commit()
            self.write_json(HTTPStatus.OK, {"deleted": cursor.rowcount})
        finally:
            conn.close()

    def read_json(self):
        content_length = self.headers.get("Content-Length")
        if not content_length:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing JSON body"})
            return None
        try:
            raw = self.rfile.read(int(content_length))
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return None

    def write_json(self, status: HTTPStatus, payload):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "0.0.0.0", port: int = 8080):
    init_db()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Serving on http://{host}:{port}")
    print(f"Using DB: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    run(host=host, port=port)
