
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

FEELINGS_LAYER_KEY = "feelings"

DEFAULT_LAYERS = [
    {
        "key": FEELINGS_LAYER_KEY,
        "name": "Pocitova mapa",
        "kind": "interactive",
        "allow_user_points": 1,
        "is_enabled": 1,
        "sort_order": 10,
    },
    {
        "key": "city_buildings",
        "name": "Mestske budovy",
        "kind": "static",
        "allow_user_points": 0,
        "is_enabled": 1,
        "sort_order": 20,
    },
]


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(create_users_table_sql())
        conn.execute(create_pins_table_sql())
        conn.execute(create_layers_table_sql())
        conn.execute(create_layer_points_table_sql())
        migrate_pins_table(conn)
        migrate_layers_table(conn)
        migrate_layer_points_table(conn)
        ensure_default_layers(conn)
        migrate_pins_to_layer_points(conn)
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


def create_layers_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS layers (
          key TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'static',
          allow_user_points INTEGER NOT NULL DEFAULT 0,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def create_layer_points_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS layer_points (
          id TEXT PRIMARY KEY,
          layer_key TEXT NOT NULL,
          lat REAL NOT NULL,
          lng REAL NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          data_json TEXT,
          type TEXT NOT NULL DEFAULT '',
          comment TEXT NOT NULL DEFAULT '',
          created_by_user_id TEXT,
          created_by_name TEXT NOT NULL DEFAULT 'Neznamy',
          created_from_ip TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (layer_key) REFERENCES layers(key),
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


def migrate_layers_table(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('layers')").fetchall()
        if row["name"]
    }
    if "kind" not in columns:
        conn.execute("ALTER TABLE layers ADD COLUMN kind TEXT NOT NULL DEFAULT 'static'")
    if "allow_user_points" not in columns:
        conn.execute(
            "ALTER TABLE layers ADD COLUMN allow_user_points INTEGER NOT NULL DEFAULT 0"
        )
    if "is_enabled" not in columns:
        conn.execute("ALTER TABLE layers ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")
    if "sort_order" not in columns:
        conn.execute("ALTER TABLE layers ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100")


def migrate_layer_points_table(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('layer_points')").fetchall()
        if row["name"]
    }
    if "title" not in columns:
        conn.execute("ALTER TABLE layer_points ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    if "description" not in columns:
        conn.execute(
            "ALTER TABLE layer_points ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    if "data_json" not in columns:
        conn.execute("ALTER TABLE layer_points ADD COLUMN data_json TEXT")
    if "type" not in columns:
        conn.execute("ALTER TABLE layer_points ADD COLUMN type TEXT NOT NULL DEFAULT ''")
    if "comment" not in columns:
        conn.execute(
            "ALTER TABLE layer_points ADD COLUMN comment TEXT NOT NULL DEFAULT ''"
        )
    if "created_by_user_id" not in columns:
        conn.execute("ALTER TABLE layer_points ADD COLUMN created_by_user_id TEXT")
    if "created_by_name" not in columns:
        conn.execute(
            "ALTER TABLE layer_points ADD COLUMN created_by_name TEXT NOT NULL DEFAULT 'Neznamy'"
        )
    if "created_from_ip" not in columns:
        conn.execute("ALTER TABLE layer_points ADD COLUMN created_from_ip TEXT")


def ensure_default_layers(conn: sqlite3.Connection) -> None:
    for layer in DEFAULT_LAYERS:
        conn.execute(
            """
            INSERT INTO layers (key, name, kind, allow_user_points, is_enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                kind = excluded.kind,
                allow_user_points = excluded.allow_user_points,
                is_enabled = excluded.is_enabled,
                sort_order = excluded.sort_order,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                layer["key"],
                layer["name"],
                layer["kind"],
                int(layer["allow_user_points"]),
                int(layer["is_enabled"]),
                int(layer["sort_order"]),
            ),
        )


def migrate_pins_to_layer_points(conn: sqlite3.Connection) -> None:
    pins_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pins'"
    ).fetchone()
    if not pins_table:
        return

    existing_feelings = conn.execute(
        "SELECT COUNT(*) AS c FROM layer_points WHERE layer_key = ?",
        (FEELINGS_LAYER_KEY,),
    ).fetchone()["c"]
    if existing_feelings > 0:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO layer_points (
            id, layer_key, lat, lng, type, comment,
            created_by_user_id, created_by_name, created_at, updated_at
        )
        SELECT
            id,
            ?,
            lat,
            lng,
            type,
            COALESCE(comment, ''),
            created_by_user_id,
            COALESCE(NULLIF(TRIM(created_by_name), ''), 'Neznamy'),
            created_at,
            updated_at
        FROM pins
        """,
        (FEELINGS_LAYER_KEY,),
    )


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def seed_from_file_if_needed(conn: sqlite3.Connection) -> None:
    if not SEED_IF_EMPTY:
        return

    if not SEED_FILE.exists():
        return

    try:
        payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Seed skipped (cannot read {SEED_FILE}): {error}")
        return

    if not isinstance(payload, dict):
        print(f"Seed skipped ({SEED_FILE} has invalid structure)")
        return

    for layer in payload.get("layers", []):
        if not is_valid_seed_layer(layer):
            continue
        conn.execute(
            """
            INSERT INTO layers (key, name, kind, allow_user_points, is_enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              name = excluded.name,
              kind = excluded.kind,
              allow_user_points = excluded.allow_user_points,
              is_enabled = excluded.is_enabled,
              sort_order = excluded.sort_order,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                layer["key"].strip(),
                layer["name"].strip(),
                str(layer.get("kind", "static"))[:40],
                int(bool(layer.get("allow_user_points", False))),
                int(bool(layer.get("is_enabled", True))),
                int(layer.get("sort_order", 100)),
            ),
        )

    inserted = 0

    feelings_count = conn.execute(
        "SELECT COUNT(*) AS c FROM layer_points WHERE layer_key = ?",
        (FEELINGS_LAYER_KEY,),
    ).fetchone()["c"]
    pins = payload.get("pins")
    if isinstance(pins, list) and feelings_count == 0:
        for pin in pins:
            if not is_valid_seed_pin(pin):
                continue
            data_json = safe_json_dump(pin.get("data"))
            conn.execute(
                """
                INSERT OR IGNORE INTO layer_points (
                    id, layer_key, lat, lng, type, comment,
                    created_by_name, data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pin["id"].strip(),
                    FEELINGS_LAYER_KEY,
                    float(pin["lat"]),
                    float(pin["lng"]),
                    pin["type"].strip(),
                    str(pin.get("comment", ""))[:300],
                    str(pin.get("created_by_name", "Seed data"))[:80],
                    data_json,
                ),
            )
            inserted += 1

    points = payload.get("points")
    if isinstance(points, dict):
        for layer_key, point_list in points.items():
            if not isinstance(layer_key, str) or not isinstance(point_list, list):
                continue
            layer_count = conn.execute(
                "SELECT COUNT(*) AS c FROM layer_points WHERE layer_key = ?",
                (layer_key.strip(),),
            ).fetchone()["c"]
            if layer_count > 0:
                continue
            for point in point_list:
                if not is_valid_seed_point(point):
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO layer_points (
                        id, layer_key, lat, lng, title, description, data_json,
                        type, comment, created_by_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        point["id"].strip(),
                        layer_key.strip(),
                        float(point["lat"]),
                        float(point["lng"]),
                        str(point.get("title", ""))[:120],
                        str(point.get("description", ""))[:500],
                        safe_json_dump(point.get("data")),
                        str(point.get("type", ""))[:40],
                        str(point.get("comment", ""))[:300],
                        str(point.get("created_by_name", "Seed data"))[:80],
                    ),
                )
                inserted += 1

    if inserted > 0:
        print(f"Seeded {inserted} points from {SEED_FILE.name}")


def safe_json_dump(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    try:
        return json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        return None


def is_valid_seed_layer(layer: object) -> bool:
    if not isinstance(layer, dict):
        return False
    return (
        isinstance(layer.get("key"), str)
        and layer["key"].strip() != ""
        and isinstance(layer.get("name"), str)
        and layer["name"].strip() != ""
    )


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


def is_valid_seed_point(point: object) -> bool:
    if not isinstance(point, dict):
        return False
    return (
        isinstance(point.get("id"), str)
        and point["id"].strip() != ""
        and isinstance(point.get("lat"), (int, float))
        and isinstance(point.get("lng"), (int, float))
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
        if path == "/api/layers":
            self.handle_get_layers()
            return

        layer_key = self.extract_layer_key(path)
        if layer_key:
            self.handle_get_layer_points(layer_key)
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

        layer_key = self.extract_layer_key(path)
        if layer_key:
            self.handle_create_layer_point(layer_key)
            return

        if path == "/api/auth/login":
            self.handle_auth_login()
            return
        if path == "/api/auth/register":
            self.handle_auth_register()
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

    def extract_layer_key(self, path: str) -> str | None:
        if not path.startswith("/api/layers/"):
            return None
        prefix = "/api/layers/"
        suffix = "/points"
        if not path.endswith(suffix):
            return None
        layer_key = path[len(prefix) : -len(suffix)].strip("/")
        if not layer_key:
            return None
        return layer_key

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

    def get_layer(self, conn: sqlite3.Connection, layer_key: str):
        return conn.execute(
            """
            SELECT key, name, kind, allow_user_points, is_enabled, sort_order
            FROM layers
            WHERE key = ?
            """,
            (layer_key,),
        ).fetchone()

    def get_layer_point_row(
        self, conn: sqlite3.Connection, point_id: str, layer_key: str | None = None
    ):
        if layer_key:
            return conn.execute(
                """
                SELECT id, layer_key, lat, lng, title, description, data_json, type, comment,
                       created_by_user_id, created_by_name, created_from_ip, created_at
                FROM layer_points
                WHERE id = ? AND layer_key = ?
                """,
                (point_id, layer_key),
            ).fetchone()

        return conn.execute(
            """
            SELECT id, layer_key, lat, lng, title, description, data_json, type, comment,
                   created_by_user_id, created_by_name, created_from_ip, created_at
            FROM layer_points
            WHERE id = ?
            """,
            (point_id,),
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

    def get_public_client_ip(self) -> str | None:
        forwarded_for = self.headers.get("X-Forwarded-For")
        if isinstance(forwarded_for, str) and forwarded_for.strip():
            first_ip = forwarded_for.split(",")[0].strip()
            if first_ip:
                return first_ip[:64]

        real_ip = self.headers.get("X-Real-IP")
        if isinstance(real_ip, str) and real_ip.strip():
            return real_ip.strip()[:64]

        cf_ip = self.headers.get("CF-Connecting-IP")
        if isinstance(cf_ip, str) and cf_ip.strip():
            return cf_ip.strip()[:64]

        if isinstance(self.client_address, tuple) and self.client_address:
            raw_ip = self.client_address[0]
            if isinstance(raw_ip, str) and raw_ip.strip():
                return raw_ip.strip()[:64]

        return None

    def parse_data_json(self, raw_value):
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def serialize_pin(self, row, auth_user=None):
        can_edit = False
        can_delete = False
        is_owner = False
        if auth_user:
            is_owner = row["created_by_user_id"] == auth_user["id"]
            can_edit = self.can_edit_pin(auth_user, row)
            can_delete = self.is_admin(auth_user) or is_owner
        payload = {
            "id": row["id"],
            "layer_key": row["layer_key"],
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
        if self.is_admin(auth_user):
            payload["created_from_ip"] = row["created_from_ip"]
        return payload

    def serialize_layer(self, row):
        return {
            "key": row["key"],
            "name": row["name"],
            "kind": row["kind"],
            "allow_user_points": bool(row["allow_user_points"]),
            "is_enabled": bool(row["is_enabled"]),
            "sort_order": int(row["sort_order"]),
        }

    def serialize_layer_point(self, row, auth_user=None):
        data = self.parse_data_json(row["data_json"])
        base = {
            "id": row["id"],
            "layer_key": row["layer_key"],
            "lat": row["lat"],
            "lng": row["lng"],
            "title": row["title"],
            "description": row["description"],
            "data": data,
            "created_by_name": row["created_by_name"],
            "created_at": row["created_at"],
        }

        if row["layer_key"] == FEELINGS_LAYER_KEY:
            pin_payload = self.serialize_pin(row, auth_user=auth_user)
            base.update(
                {
                    "type": pin_payload["type"],
                    "comment": pin_payload["comment"],
                    "is_owner": pin_payload["is_owner"],
                    "can_edit": pin_payload["can_edit"],
                    "can_delete": pin_payload["can_delete"],
                }
            )
        else:
            base.update(
                {
                    "type": row["type"],
                    "comment": row["comment"],
                    "is_owner": False,
                    "can_edit": False,
                    "can_delete": False,
                }
            )

        return base

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
            if not user:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Neznamy uzivatel"})
                return

            token = generate_auth_token()
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

            self.write_json(
                HTTPStatus.OK,
                {
                    "token": user["auth_token"],
                    "user": self.serialize_user(user),
                },
            )
        finally:
            conn.close()

    def handle_auth_register(self):
        payload = self.read_json()
        if payload is None:
            return

        email = payload.get("email")
        name = payload.get("name")

        if not isinstance(email, str) or not isinstance(name, str):
            self.write_json(
                HTTPStatus.BAD_REQUEST, {"error": "Invalid register payload"}
            )
            return

        email = normalize_email(email)
        name = normalize_name(name)
        if not email or "@" not in email or not name:
            self.write_json(
                HTTPStatus.BAD_REQUEST, {"error": "Invalid register payload"}
            )
            return

        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if existing:
                self.write_json(HTTPStatus.CONFLICT, {"error": "Uzivatel uz existuje"})
                return

            users_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            role = "admin" if users_count == 0 else "user"
            user_id = generate_id("usr")
            token = generate_auth_token()
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
                HTTPStatus.CREATED,
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

    def handle_get_layers(self):
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT key, name, kind, allow_user_points, is_enabled, sort_order
                FROM layers
                WHERE is_enabled = 1
                ORDER BY sort_order ASC, key ASC
                """
            ).fetchall()
            layers = [self.serialize_layer(row) for row in rows]
            self.write_json(HTTPStatus.OK, {"layers": layers})
        finally:
            conn.close()

    def handle_get_layer_points(self, layer_key: str):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            layer = self.get_layer(conn, layer_key)
            if not layer or not bool(layer["is_enabled"]):
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Layer not found"})
                return

            rows = conn.execute(
                """
                SELECT id, layer_key, lat, lng, title, description, data_json, type, comment,
                       created_by_user_id, created_by_name, created_from_ip, created_at
                FROM layer_points
                WHERE layer_key = ?
                ORDER BY created_at ASC
                """,
                (layer_key,),
            ).fetchall()
            points = [self.serialize_layer_point(row, auth_user=auth_user) for row in rows]
            self.write_json(HTTPStatus.OK, {"points": points})
        finally:
            conn.close()

    def handle_get_pins(self):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            rows = conn.execute(
                """
                SELECT id, layer_key, lat, lng, title, description, data_json, type, comment,
                       created_by_user_id, created_by_name, created_from_ip, created_at
                FROM layer_points
                WHERE layer_key = ?
                ORDER BY created_at ASC
                """,
                (FEELINGS_LAYER_KEY,),
            ).fetchall()
            pins = [self.serialize_pin(row, auth_user=auth_user) for row in rows]
            self.write_json(HTTPStatus.OK, {"pins": pins})
        finally:
            conn.close()

    def handle_create_layer_point(self, layer_key: str):
        payload = self.read_json()
        if payload is None:
            return

        lat = payload.get("lat")
        lng = payload.get("lng")
        title = payload.get("title", "")
        description = payload.get("description", "")
        data = payload.get("data")
        point_type = payload.get("type", "")
        comment = payload.get("comment", "")

        if (
            not isinstance(lat, (int, float))
            or not isinstance(lng, (int, float))
            or not isinstance(title, str)
            or not isinstance(description, str)
            or (data is not None and not isinstance(data, dict))
            or not isinstance(point_type, str)
            or not isinstance(comment, str)
        ):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid point payload"})
            return

        conn = get_conn()
        try:
            layer = self.get_layer(conn, layer_key)
            if not layer or not bool(layer["is_enabled"]):
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Layer not found"})
                return
            if not bool(layer["allow_user_points"]):
                self.write_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Layer does not allow user-created points"},
                )
                return

            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return

            point_id = payload.get("id")
            if not isinstance(point_id, str) or not point_id.strip():
                point_id = generate_id("pt")

            conn.execute(
                """
                INSERT INTO layer_points (
                    id, layer_key, lat, lng, title, description, data_json,
                    type, comment, created_by_user_id, created_by_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    point_id.strip(),
                    layer_key,
                    float(lat),
                    float(lng),
                    title[:120],
                    description[:500],
                    safe_json_dump(data),
                    point_type[:40],
                    comment[:300],
                    auth_user["id"],
                    auth_user["name"][:80],
                ),
            )
            conn.commit()
            row = self.get_layer_point_row(conn, point_id.strip(), layer_key=layer_key)
            self.write_json(
                HTTPStatus.CREATED, self.serialize_layer_point(row, auth_user=auth_user)
            )
        except sqlite3.IntegrityError:
            self.write_json(HTTPStatus.CONFLICT, {"error": "Point id already exists"})
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
            source_ip = self.get_public_client_ip()

            conn.execute(
                """
                INSERT INTO layer_points (
                    id, layer_key, lat, lng, type, comment, created_by_user_id, created_by_name, created_from_ip
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pin_id.strip(),
                    FEELINGS_LAYER_KEY,
                    float(lat),
                    float(lng),
                    pin_type.strip(),
                    comment[:300],
                    auth_user["id"],
                    auth_user["name"][:80],
                    source_ip,
                ),
            )
            conn.commit()
            row = self.get_layer_point_row(
                conn, pin_id.strip(), layer_key=FEELINGS_LAYER_KEY
            )
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

            row = self.get_layer_point_row(conn, pin_id, layer_key=FEELINGS_LAYER_KEY)
            if not row:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            if not self.can_edit_pin(auth_user, row):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Permission denied"})
                return

            conn.execute(
                """
                UPDATE layer_points
                SET comment = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND layer_key = ?
                """,
                (comment[:300], pin_id, FEELINGS_LAYER_KEY),
            )
            conn.commit()
            row = self.get_layer_point_row(conn, pin_id, layer_key=FEELINGS_LAYER_KEY)
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
            cursor = conn.execute(
                "DELETE FROM layer_points WHERE layer_key = ?", (FEELINGS_LAYER_KEY,)
            )
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
            row = self.get_layer_point_row(conn, pin_id, layer_key=FEELINGS_LAYER_KEY)
            if not row:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "Pin not found"})
                return
            is_owner = row["created_by_user_id"] == auth_user["id"]
            if not (self.is_admin(auth_user) or is_owner):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Permission denied"})
                return
            conn.execute(
                "DELETE FROM layer_points WHERE id = ? AND layer_key = ?",
                (pin_id, FEELINGS_LAYER_KEY),
            )
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
