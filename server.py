import json
import os
import secrets
import sqlite3
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT / "pins.db"))).resolve()
SEED_FILE_ENV = os.environ.get("SEED_FILE", "seed.json")
SEED_FILE = Path(SEED_FILE_ENV)
if not SEED_FILE.is_absolute():
    SEED_FILE = (ROOT / SEED_FILE).resolve()
SEED_IF_EMPTY = os.environ.get("SEED_IF_EMPTY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(create_users_table_sql())
        conn.execute(create_pins_table_sql())
        migrate_pins_table(conn)
        seed_from_file_if_needed(conn)
        conn.commit()
    finally:
        conn.close()


def create_users_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'moderator', 'user')),
          auth_token TEXT UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_login_at TEXT
        )
    """


def create_pins_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS pins (
          id TEXT PRIMARY KEY,
          lat REAL NOT NULL,
          lng REAL NOT NULL,
          type TEXT NOT NULL,
          comment TEXT NOT NULL DEFAULT '',
          created_by_user_id TEXT,
          created_by_name TEXT NOT NULL DEFAULT 'Neznamy',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (created_by_user_id) REFERENCES users(id)
        )
    """


def migrate_pins_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pins'"
    ).fetchone()
    if not row or not row["sql"]:
        return

    table_sql = row["sql"]
    legacy_check = "CHECK (type IN ('good', 'bad'))"
    needs_rebuild = legacy_check in table_sql
    if needs_rebuild:
        conn.execute("ALTER TABLE pins RENAME TO pins_old")
        conn.execute(create_pins_table_sql())
        conn.execute(
            """
            INSERT INTO pins (
              id, lat, lng, type, comment, created_at, updated_at
            )
            SELECT id, lat, lng, type, comment, created_at, updated_at
            FROM pins_old
            """
        )
        conn.execute("DROP TABLE pins_old")

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('pins')").fetchall()
        if row["name"]
    }
    if "created_by_user_id" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN created_by_user_id TEXT")
    if "created_by_name" not in columns:
        conn.execute(
            "ALTER TABLE pins ADD COLUMN created_by_name TEXT NOT NULL DEFAULT 'Neznamy'"
        )
    conn.execute(
        "UPDATE pins SET created_by_name = COALESCE(NULLIF(TRIM(created_by_name), ''), 'Neznamy')"
    )


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_from_file_if_needed(conn: sqlite3.Connection) -> None:
    if not SEED_IF_EMPTY:
        return

    existing_count = conn.execute("SELECT COUNT(*) AS c FROM pins").fetchone()["c"]
    if existing_count > 0:
        return

    if not SEED_FILE.exists():
        return

    try:
        payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Seed skipped (cannot read {SEED_FILE}): {error}")
        return

    pins = payload.get("pins") if isinstance(payload, dict) else None
    if not isinstance(pins, list):
        print(f"Seed skipped ({SEED_FILE} has no valid 'pins' list)")
        return

    inserted = 0
    for pin in pins:
        if not is_valid_seed_pin(pin):
            continue
        conn.execute(
            """
            INSERT INTO pins (id, lat, lng, type, comment, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pin["id"].strip(),
                float(pin["lat"]),
                float(pin["lng"]),
                pin["type"].strip(),
                str(pin.get("comment", ""))[:300],
                str(pin.get("created_by_name", "Seed data"))[:80],
            ),
        )
        inserted += 1

    if inserted > 0:
        print(f"Seeded {inserted} pins from {SEED_FILE.name}")


def is_valid_seed_pin(pin: object) -> bool:
    if not isinstance(pin, dict):
        return False
    return (
        isinstance(pin.get("id"), str)
        and pin["id"].strip() != ""
        and isinstance(pin.get("lat"), (int, float))
        and isinstance(pin.get("lng"), (int, float))
        and isinstance(pin.get("type"), str)
        and pin["type"].strip() != ""
        and (pin.get("comment") is None or isinstance(pin.get("comment"), str))
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def generate_auth_token() -> str:
    return secrets.token_urlsafe(32)


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
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
        if path == "/api/auth/me":
            self.handle_auth_me()
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/pins":
            self.handle_create_pin()
            return
        if path == "/api/auth/login":
            self.handle_auth_login()
            return
        if path == "/api/auth/logout":
            self.handle_auth_logout()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        path = urlparse(self.path).path
        if path.startswith("/api/pins/"):
            pin_id = path.removeprefix("/api/pins/").strip()
            if not pin_id:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing pin id"})
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
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing pin id"})
                return
            self.handle_delete_pin(pin_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def get_auth_user(self, conn: sqlite3.Connection):
        token = self.headers.get("X-Auth-Token")
        if not token:
            return None
        token = token.strip()
        if not token:
            return None
        return conn.execute(
            "SELECT id, email, name, role, auth_token FROM users WHERE auth_token = ?",
            (token,),
        ).fetchone()

    def get_pin_row(self, conn: sqlite3.Connection, pin_id: str):
        return conn.execute(
            """
            SELECT id, lat, lng, type, comment, created_by_user_id, created_by_name, created_at
            FROM pins
            WHERE id = ?
            """,
            (pin_id,),
        ).fetchone()

    def can_edit_pin(self, user, pin_row) -> bool:
        if not user:
            return False
        role = user["role"]
        if role == "admin":
            return True
        if role in ("moderator", "user"):
            return pin_row["created_by_user_id"] == user["id"]
        return False

    def is_admin(self, user) -> bool:
        return bool(user and user["role"] == "admin")

    def serialize_pin(self, row, auth_user=None):
        can_edit = False
        can_delete = False
        is_owner = False
        if auth_user:
            is_owner = row["created_by_user_id"] == auth_user["id"]
            can_edit = self.can_edit_pin(auth_user, row)
            can_delete = self.is_admin(auth_user) or is_owner
        return {
            "id": row["id"],
            "lat": row["lat"],
            "lng": row["lng"],
            "type": row["type"],
            "comment": row["comment"],
            "created_by_name": row["created_by_name"],
            "created_at": row["created_at"],
            "is_owner": is_owner,
            "can_edit": can_edit,
            "can_delete": can_delete,
        }

    def serialize_user(self, user_row):
        return {
            "id": user_row["id"],
            "email": user_row["email"],
            "name": user_row["name"],
            "role": user_row["role"],
        }

    def handle_auth_me(self):
        conn = get_conn()
        try:
            user = self.get_auth_user(conn)
            if not user:
                self.write_json(HTTPStatus.OK, {"user": None})
                return
            self.write_json(HTTPStatus.OK, {"user": self.serialize_user(user)})
        finally:
            conn.close()

    def handle_auth_login(self):
        payload = self.read_json()
        if payload is None:
            return

        email = payload.get("email")
        name = payload.get("name")

        if not isinstance(email, str) or not isinstance(name, str):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid login payload"})
            return

        email = normalize_email(email)
        name = normalize_name(name)
        if not email or "@" not in email or not name:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid login payload"})
            return

        conn = get_conn()
        try:
            user = conn.execute(
                "SELECT id, email, name, role, auth_token FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            token = generate_auth_token()
            if user:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, auth_token = ?, updated_at = CURRENT_TIMESTAMP, last_login_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (name[:80], token, user["id"]),
                )
                conn.commit()
                user = conn.execute(
                    "SELECT id, email, name, role, auth_token FROM users WHERE id = ?",
                    (user["id"],),
                ).fetchone()
            else:
                users_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM users"
                ).fetchone()["c"]
                role = "admin" if users_count == 0 else "user"
                user_id = generate_id("usr")
                conn.execute(
                    """
                    INSERT INTO users (id, email, name, role, auth_token, last_login_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (user_id, email, name[:80], role, token),
                )
                conn.commit()
                user = conn.execute(
                    "SELECT id, email, name, role, auth_token FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()

            self.write_json(
                HTTPStatus.OK,
                {
                    "token": user["auth_token"],
                    "user": self.serialize_user(user),
                },
            )
        finally:
            conn.close()

    def handle_auth_logout(self):
        conn = get_conn()
        try:
            user = self.get_auth_user(conn)
            if user:
                conn.execute(
                    "UPDATE users SET auth_token = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (user["id"],),
                )
                conn.commit()
            self.write_json(HTTPStatus.OK, {"ok": True})
        finally:
            conn.close()

    def handle_get_pins(self):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            rows = conn.execute(
                """
                SELECT id, lat, lng, type, comment, created_by_user_id, created_by_name
                       , created_at
                FROM pins
                ORDER BY created_at ASC
                """
            ).fetchall()
            pins = [self.serialize_pin(row, auth_user=auth_user) for row in rows]
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
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return

            conn.execute(
                """
                INSERT INTO pins (id, lat, lng, type, comment, created_by_user_id, created_by_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pin_id.strip(),
                    float(lat),
                    float(lng),
                    pin_type.strip(),
                    comment[:300],
                    auth_user["id"],
                    auth_user["name"][:80],
                ),
            )
            conn.commit()
            row = self.get_pin_row(conn, pin_id.strip())
            self.write_json(HTTPStatus.CREATED, self.serialize_pin(row, auth_user=auth_user))
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
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return

            row = self.get_pin_row(conn, pin_id)
            if not row:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            if not self.can_edit_pin(auth_user, row):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Permission denied"})
                return

            conn.execute(
                """
                UPDATE pins
                SET comment = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (comment[:300], pin_id),
            )
            conn.commit()
            row = self.get_pin_row(conn, pin_id)
            self.write_json(HTTPStatus.OK, self.serialize_pin(row, auth_user=auth_user))
        finally:
            conn.close()

    def handle_delete_all_pins(self):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return
            cursor = conn.execute("DELETE FROM pins")
            conn.commit()
            self.write_json(HTTPStatus.OK, {"deleted": cursor.rowcount})
        finally:
            conn.close()

    def handle_delete_pin(self, pin_id: str):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return
            row = self.get_pin_row(conn, pin_id)
            if not row:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            is_owner = row["created_by_user_id"] == auth_user["id"]
            if not (self.is_admin(auth_user) or is_owner):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Permission denied"})
                return
            cursor = conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
            conn.commit()
            self.write_json(HTTPStatus.OK, {"deleted": 1})
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
    print(f"Seed file: {SEED_FILE} (enabled: {SEED_IF_EMPTY})")
    server.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    run(host=host, port=port)
