
import json
import math
import os
import re
import secrets
import sqlite3
import unicodedata
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from html import unescape
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


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
CITY_BUILDINGS_LAYER_KEY = "city_buildings"
HUSTOPECE_BOUNDS = {
    "south": 48.928,
    "north": 48.956,
    "west": 16.713,
    "east": 16.758,
}
CZECH_BOUNDS = {
    "south": 48.45,
    "north": 51.15,
    "west": 12.05,
    "east": 18.95,
}

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
        "key": CITY_BUILDINGS_LAYER_KEY,
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
        conn.execute(create_city_building_parcels_table_sql())
        migrate_pins_table(conn)
        migrate_layers_table(conn)
        migrate_layer_points_table(conn)
        migrate_city_building_parcels_table(conn)
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


def create_city_building_parcels_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS city_building_parcels (
          id TEXT PRIMARY KEY,
          source_url TEXT NOT NULL,
          parcel_label TEXT NOT NULL,
          parcel_url TEXT NOT NULL UNIQUE,
          building_object_url TEXT NOT NULL DEFAULT '',
          object_type TEXT NOT NULL DEFAULT '',
          street TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '',
          lat REAL,
          lng REAL,
          has_building INTEGER NOT NULL DEFAULT 1,
          imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def migrate_city_building_parcels_table(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('city_building_parcels')").fetchall()
        if row["name"]
    }
    if "source_url" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
        )
    if "parcel_label" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN parcel_label TEXT NOT NULL DEFAULT ''"
        )
    if "parcel_url" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN parcel_url TEXT NOT NULL DEFAULT ''"
        )
    if "building_object_url" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN building_object_url TEXT NOT NULL DEFAULT ''"
        )
    if "object_type" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN object_type TEXT NOT NULL DEFAULT ''"
        )
    if "street" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN street TEXT NOT NULL DEFAULT ''"
        )
    if "address" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN address TEXT NOT NULL DEFAULT ''"
        )
    if "lat" not in columns:
        conn.execute("ALTER TABLE city_building_parcels ADD COLUMN lat REAL")
    if "lng" not in columns:
        conn.execute("ALTER TABLE city_building_parcels ADD COLUMN lng REAL")
    if "has_building" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN has_building INTEGER NOT NULL DEFAULT 1"
        )
    if "imported_at" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "updated_at" not in columns:
        conn.execute(
            "ALTER TABLE city_building_parcels ADD COLUMN updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )

    # Legacy migration from older builds that stored only EPSG:2065 coordinates.
    has_legacy_krovak = "krovak_y" in columns and "krovak_x" in columns
    if has_legacy_krovak:
        rows = conn.execute(
            """
            SELECT id, krovak_y, krovak_x
            FROM city_building_parcels
            WHERE (lat IS NULL OR lng IS NULL)
              AND krovak_y IS NOT NULL
              AND krovak_x IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            lat, lng = convert_epsg2065_to_wgs84(row["krovak_y"], row["krovak_x"])
            if lat is None or lng is None:
                continue
            conn.execute(
                """
                UPDATE city_building_parcels
                SET lat = ?, lng = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (lat, lng, row["id"]),
            )

        # Keep only GPS coordinates in legacy schemas too.
        conn.execute(
            """
            UPDATE city_building_parcels
            SET krovak_y = NULL, krovak_x = NULL
            WHERE lat IS NOT NULL
              AND lng IS NOT NULL
              AND (krovak_y IS NOT NULL OR krovak_x IS NOT NULL)
            """
        )

    rows_to_normalize = conn.execute(
        """
        SELECT id, object_type, street, address
        FROM city_building_parcels
        """
    ).fetchall()
    for row in rows_to_normalize:
        object_type_norm, street_norm, address_norm = normalize_city_building_row(
            str(row["object_type"] or ""),
            str(row["street"] or ""),
            str(row["address"] or ""),
        )
        if (
            object_type_norm != str(row["object_type"] or "")
            or street_norm != str(row["street"] or "")
            or address_norm != str(row["address"] or "")
        ):
            conn.execute(
                """
                UPDATE city_building_parcels
                SET object_type = ?, street = ?, address = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (object_type_norm, street_norm, address_norm, row["id"]),
            )


def city_building_table_has_legacy_krovak(conn: sqlite3.Connection) -> bool:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('city_building_parcels')").fetchall()
        if row["name"]
    }
    return "krovak_y" in columns and "krovak_x" in columns


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


def normalize_text(value: str) -> str:
    lowered = value.lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_accents.split())


def clean_html_text(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(plain).split())


def remove_cadastral_code(value: str) -> str:
    text = " ".join(unescape(value or "").split())
    text = re.sub(r"\s*\[\s*\d+\s*\]\s*", " ", text)
    return " ".join(text.split()).strip(" ,;")


def extract_building_number(value: str) -> str:
    text = remove_cadastral_code(value)
    cp_match = re.search(r"c\.\s*p\.\s*([0-9]+(?:/[0-9]+)?)", normalize_text(text))
    if cp_match:
        return cp_match.group(1)
    number_match = re.search(r"\b([0-9]+(?:/[0-9]+)?)\b", text)
    if number_match:
        return number_match.group(1)
    return ""


def normalize_object_type(value: str) -> str:
    text = remove_cadastral_code(value)
    if ";" in text:
        _, right = text.split(";", 1)
        return right.strip()[:200]
    return text.strip()[:200]


def compose_building_address(street: str, building_number: str, city: str) -> str:
    street_clean = remove_cadastral_code(street)
    city_clean = remove_cadastral_code(city)
    number_clean = extract_building_number(building_number)

    first_part = street_clean
    if number_clean and number_clean not in first_part:
        first_part = f"{first_part} {number_clean}".strip()

    if city_clean and city_clean.lower() not in first_part.lower():
        return ", ".join(part for part in (first_part, city_clean) if part)[:260]
    return first_part[:260]


def normalize_city_building_row(
    object_type: str, street: str, address: str
) -> tuple[str, str, str]:
    street_clean = remove_cadastral_code(street)
    object_type_clean = normalize_object_type(object_type)

    address_clean = remove_cadastral_code(address)
    number_from_object = ""
    if ";" in remove_cadastral_code(object_type):
        left = remove_cadastral_code(object_type).split(";", 1)[0]
        number_from_object = extract_building_number(left)
    number_from_address = extract_building_number(address_clean)
    building_number = number_from_address or number_from_object

    city = ""
    if "," in address_clean:
        city = address_clean.split(",")[-1].strip()
    elif street_clean:
        rest = address_clean
        if rest.lower().startswith(street_clean.lower()):
            rest = rest[len(street_clean) :].strip(" ,")
        if building_number:
            rest = re.sub(
                rf"\b{re.escape(building_number)}\b", "", rest, flags=re.IGNORECASE
            ).strip(" ,")
        city = rest

    address_composed = compose_building_address(street_clean, building_number, city)
    if not address_composed:
        address_composed = address_clean[:260]

    return (
        object_type_clean[:200],
        street_clean[:200],
        address_composed[:260],
    )


def extract_parcel_number(value: str) -> str | None:
    patterns = (
        r"\bst\.\s*\d+(?:/\d+)?\b",
        r"\b\d+/\d+\b",
        r"\b\d+\b",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(0).split())
    return None


def parse_building_parcels_from_html(html_text: str, source_url: str) -> list[dict]:
    positive_phrase = "pozemku je stavba"
    block_pattern = re.compile(
        r"<tr\b[^>]*>.*?</tr>|<li\b[^>]*>.*?</li>",
        re.IGNORECASE | re.DOTALL,
    )
    anchor_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    results_by_url: dict[str, dict] = {}

    def add_candidate(href: str, raw_label: str, fallback_text: str) -> None:
        absolute_url = urljoin(source_url, unescape(href.strip()))
        if not absolute_url.lower().startswith(("http://", "https://")):
            return

        label_text = clean_html_text(raw_label)
        parcel_number = extract_parcel_number(label_text)
        if not parcel_number:
            parcel_number = extract_parcel_number(fallback_text)
        if not parcel_number:
            return

        existing = results_by_url.get(absolute_url)
        candidate = {
            "parcel_label": parcel_number[:200],
            "parcel_url": absolute_url[:800],
        }
        if not existing:
            results_by_url[absolute_url] = candidate

    def block_matches_target(text: str) -> bool:
        normalized = normalize_text(text)
        return positive_phrase in normalized

    for block_html in block_pattern.findall(html_text):
        block_text = clean_html_text(block_html)
        if not block_matches_target(block_text):
            continue
        anchors = anchor_pattern.findall(block_html)
        for href, label in anchors:
            add_candidate(href, label, block_text)

    if not results_by_url:
        for anchor_match in anchor_pattern.finditer(html_text):
            start = max(0, anchor_match.start() - 700)
            end = min(len(html_text), anchor_match.end() + 700)
            context_text = clean_html_text(html_text[start:end])
            if not block_matches_target(context_text):
                continue
            add_candidate(anchor_match.group(1), anchor_match.group(2), context_text)

    return list(results_by_url.values())


def fetch_remote_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PocitovaMapaBot/1.0; +https://example.invalid)"
        },
        method="GET",
    )
    with urlopen(request, timeout=15) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    content_type_normalized = content_type.lower()
    if "charset=" in content_type_normalized:
        charset = content_type_normalized.split("charset=", 1)[1].split(";", 1)[0].strip()
        for encoding in (charset, "utf-8", "windows-1250", "cp1250", "latin-1"):
            try:
                return raw.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
    for encoding in ("utf-8", "windows-1250", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_building_detail_from_parcel_html(html_text: str, parcel_url: str) -> dict:
    row_pattern = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
    cell_pattern = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
    anchor_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    building_object_url = ""
    building_number = ""
    object_type = ""
    street = ""
    city = ""

    for row_html in row_pattern.findall(html_text):
        cells = cell_pattern.findall(row_html)
        if len(cells) < 2:
            continue
        label = normalize_text(clean_html_text(cells[0]))

        if "budova s cislem popisnym" in label:
            detail_cell = cells[-1]
            detail_text = clean_html_text(detail_cell)
            if ";" in detail_text:
                left, right = detail_text.split(";", 1)
                building_number = extract_building_number(left)
                object_type = normalize_object_type(right)
            else:
                building_number = extract_building_number(detail_text)

        if "budova bez cisla popisneho nebo evidencniho" in label:
            detail_cell = cells[-1]
            detail_text = clean_html_text(detail_cell)
            if detail_text:
                object_type = normalize_object_type(detail_text)

        if "stavebni objekt" in label:
            for cell in cells[1:]:
                anchor = anchor_pattern.search(cell)
                if not anchor:
                    continue
                building_object_url = urljoin(parcel_url, unescape(anchor.group(1).strip()))
                if not building_number:
                    building_number = extract_building_number(clean_html_text(anchor.group(2)))
                break

        if label.startswith("ulice"):
            street = remove_cadastral_code(clean_html_text(cells[1]))
        if label.startswith("obec"):
            city = remove_cadastral_code(clean_html_text(cells[1]))

    address = compose_building_address(street, building_number, city)
    object_type = normalize_object_type(object_type)

    return {
        "building_object_url": building_object_url[:800],
        "object_type": object_type[:200],
        "street": street[:200],
        "address": address[:260],
    }


def parse_epsg2065_coordinates_from_object_html(
    html_text: str,
) -> tuple[float | None, float | None]:
    normalized = html_text.replace("&nbsp;", " ").replace("\xa0", " ")
    match = re.search(
        r"Y:\s*([0-9\.\,\-\s]+)\s*X:\s*([0-9\.\,\-\s]+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None

    def parse_number(value: str) -> float | None:
        compact = value.replace(" ", "").replace("\u202f", "").replace("\u00a0", "")
        if compact.count(",") == 1 and compact.count(".") > 1:
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", ".")
        try:
            return float(compact)
        except ValueError:
            return None

    y = parse_number(match.group(1))
    x = parse_number(match.group(2))
    return y, x


KROVAK_A = 6377397.155
KROVAK_ES = 0.006674372230614
KROVAK_E = math.sqrt(KROVAK_ES)
KROVAK_LAT0 = math.radians(49.5)
KROVAK_LON0 = math.radians(24.8333333333333)
KROVAK_K0 = 0.9999
KROVAK_S45 = math.pi / 4
KROVAK_S90 = math.pi / 2
KROVAK_UQ = 1.04216856380474
KROVAK_S0 = 1.37008346281555
KROVAK_AD = KROVAK_S90 - KROVAK_UQ

_krovak_sin_lat0 = math.sin(KROVAK_LAT0)
_krovak_cos_lat0 = math.cos(KROVAK_LAT0)
KROVAK_ALPHA = math.sqrt(
    1 + (KROVAK_ES * (_krovak_cos_lat0**4)) / (1 - KROVAK_ES)
)
KROVAK_U0 = math.asin(_krovak_sin_lat0 / KROVAK_ALPHA)
KROVAK_G = ((1 + KROVAK_E * _krovak_sin_lat0) / (1 - KROVAK_E * _krovak_sin_lat0)) ** (
    (KROVAK_ALPHA * KROVAK_E) / 2
)
KROVAK_K = (
    math.tan((KROVAK_U0 / 2) + KROVAK_S45)
    / (math.tan((KROVAK_LAT0 / 2) + KROVAK_S45) ** KROVAK_ALPHA)
) * KROVAK_G
KROVAK_N0 = (
    KROVAK_A * math.sqrt(1 - KROVAK_ES) / (1 - KROVAK_ES * (_krovak_sin_lat0**2))
)
KROVAK_N = math.sin(KROVAK_S0)
KROVAK_RO0 = KROVAK_K0 * KROVAK_N0 / math.tan(KROVAK_S0)

BESSEL_A = 6377397.155
BESSEL_ES = 0.006674372230614
WGS84_A = 6378137.0
WGS84_ES = 0.0066943799901413165
_SECONDS_TO_RADIANS = math.pi / (180 * 3600)
TOWGS84 = (
    570.8,
    85.7,
    462.8,
    4.998 * _SECONDS_TO_RADIANS,
    1.587 * _SECONDS_TO_RADIANS,
    5.261 * _SECONDS_TO_RADIANS,
    1 + 3.56e-6,
)


def is_inside_bounds(lat: float, lng: float, bounds: dict[str, float], padding: float) -> bool:
    return (
        lat >= bounds["south"] - padding
        and lat <= bounds["north"] + padding
        and lng >= bounds["west"] - padding
        and lng <= bounds["east"] + padding
    )


def coordinate_score(lat: float, lng: float) -> int:
    if is_inside_bounds(lat, lng, HUSTOPECE_BOUNDS, 0.2):
        return 3
    if is_inside_bounds(lat, lng, CZECH_BOUNDS, 0):
        return 2
    return 0


def geodetic_to_geocentric(
    lat: float, lon: float, a: float, es: float, height: float = 0.0
) -> tuple[float, float, float]:
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)
    n = a / math.sqrt(1 - es * sin_lat * sin_lat)
    x = (n + height) * cos_lat * cos_lon
    y = (n + height) * cos_lat * sin_lon
    z = (n * (1 - es) + height) * sin_lat
    return x, y, z


def geocentric_to_geodetic(
    x: float, y: float, z: float, a: float, es: float
) -> tuple[float, float]:
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p <= 1e-12:
        lat = math.copysign(math.pi / 2, z)
        return lat, lon

    lat = math.atan2(z, p * (1 - es))
    for _ in range(15):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1 - es * sin_lat * sin_lat)
        height = p / math.cos(lat) - n
        next_lat = math.atan2(z, p * (1 - es * n / (n + height)))
        if abs(next_lat - lat) < 1e-13:
            lat = next_lat
            break
        lat = next_lat

    return lat, lon


def helmert_bessel_to_wgs84(x: float, y: float, z: float) -> tuple[float, float, float]:
    dx, dy, dz, rx, ry, rz, scale = TOWGS84
    x2 = dx + scale * (x - rz * y + ry * z)
    y2 = dy + scale * (rz * x + y - rx * z)
    z2 = dz + scale * (-ry * x + rx * y + z)
    return x2, y2, z2


def inverse_krovak_to_bessel(lat_x: float, lon_y: float, czech_variant: bool) -> tuple[float, float] | None:
    x = float(lat_x)
    y = float(lon_y)
    if not czech_variant:
        x = -x
        y = -y

    ro = math.hypot(x, y)
    if ro <= 1e-9:
        return None

    eps = math.atan2(-x, -y)
    d = eps / KROVAK_N
    s = 2 * (
        math.atan(((KROVAK_RO0 / ro) ** (1 / KROVAK_N)) * math.tan((KROVAK_S0 / 2) + KROVAK_S45))
        - KROVAK_S45
    )

    u = math.asin(
        math.cos(KROVAK_AD) * math.sin(s)
        - math.sin(KROVAK_AD) * math.cos(s) * math.cos(d)
    )
    kau = math.tan((u / 2) + KROVAK_S45)

    fi = u
    for _ in range(15):
        sin_fi = math.sin(fi)
        ratio = (1 + KROVAK_E * sin_fi) / (1 - KROVAK_E * sin_fi)
        fi_next = 2 * (
            math.atan(
                (KROVAK_K ** (-1 / KROVAK_ALPHA))
                * (kau ** (1 / KROVAK_ALPHA))
                * (ratio ** (KROVAK_E / 2))
            )
            - KROVAK_S45
        )
        if abs(fi_next - fi) < 1e-13:
            fi = fi_next
            break
        fi = fi_next

    denominator = (
        math.sin(KROVAK_AD) * math.sin(s)
        + math.cos(KROVAK_AD) * math.cos(s) * math.cos(d)
    )
    if abs(denominator) < 1e-14:
        return None

    lam = -math.atan((math.cos(s) * math.sin(d)) / denominator) / KROVAK_ALPHA
    lon = KROVAK_LON0 + lam
    lat = fi
    return lat, lon


def convert_epsg2065_variant_to_wgs84(
    x: float, y: float, czech_variant: bool
) -> tuple[float, float] | None:
    bessel_lat_lon = inverse_krovak_to_bessel(x, y, czech_variant=czech_variant)
    if not bessel_lat_lon:
        return None
    bessel_lat, bessel_lon = bessel_lat_lon

    x_geo, y_geo, z_geo = geodetic_to_geocentric(
        bessel_lat, bessel_lon, BESSEL_A, BESSEL_ES
    )
    x_wgs, y_wgs, z_wgs = helmert_bessel_to_wgs84(x_geo, y_geo, z_geo)
    wgs_lat, wgs_lon = geocentric_to_geodetic(x_wgs, y_wgs, z_wgs, WGS84_A, WGS84_ES)
    lat = math.degrees(wgs_lat)
    lng = math.degrees(wgs_lon)
    if not math.isfinite(lat) or not math.isfinite(lng):
        return None
    return lat, lng


def convert_epsg2065_to_wgs84(
    source_y: float | None, source_x: float | None
) -> tuple[float | None, float | None]:
    if source_y is None or source_x is None:
        return None, None
    if not math.isfinite(source_y) or not math.isfinite(source_x):
        return None, None

    variants = [
        (source_x, source_y),
        (source_y, source_x),
        (-source_x, -source_y),
        (-source_y, -source_x),
        (source_x, -source_y),
        (-source_x, source_y),
        (source_y, -source_x),
        (-source_y, source_x),
    ]

    best: tuple[float, float, int] | None = None
    for x, y in variants:
        for czech_variant in (False, True):
            converted = convert_epsg2065_variant_to_wgs84(x, y, czech_variant)
            if not converted:
                continue
            lat, lng = converted
            score = coordinate_score(lat, lng)
            if score <= 0:
                continue
            if not best or score > best[2]:
                best = (lat, lng, score)

    if not best:
        return None, None
    return best[0], best[1]


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
        if path == "/api/admin/users":
            self.handle_get_admin_users()
            return
        if path == "/api/admin/buildings/parcels":
            self.handle_get_admin_building_parcels()
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
        if path == "/api/admin/buildings/parcels/import-html":
            self.handle_import_admin_building_parcels_html()
            return
        if path == "/api/admin/buildings/parcels/refresh-coordinates":
            self.handle_refresh_admin_building_coordinates()
            return
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
        if path.startswith("/api/admin/users/"):
            user_id = path.removeprefix("/api/admin/users/").strip()
            if not user_id or "/" in user_id:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing user id"})
                return
            self.handle_update_admin_user(user_id)
            return
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
        if path == "/api/admin/buildings/parcels":
            self.handle_delete_admin_building_parcels()
            return
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

    def serialize_admin_user(self, user_row, current_user_id: str | None):
        return {
            "id": user_row["id"],
            "email": user_row["email"],
            "name": user_row["name"],
            "role": user_row["role"],
            "last_login_at": user_row["last_login_at"],
            "created_at": user_row["created_at"],
            "updated_at": user_row["updated_at"],
            "has_active_session": bool(user_row["auth_token"]),
            "is_current_user": bool(
                current_user_id and current_user_id == user_row["id"]
            ),
        }

    def serialize_admin_building_parcel(self, row):
        return {
            "id": row["id"],
            "source_url": row["source_url"],
            "parcel_label": row["parcel_label"],
            "parcel_url": row["parcel_url"],
            "building_object_url": row["building_object_url"],
            "object_type": row["object_type"],
            "street": row["street"],
            "address": row["address"],
            "lat": row["lat"],
            "lng": row["lng"],
            "has_building": bool(row["has_building"]),
            "imported_at": row["imported_at"],
            "updated_at": row["updated_at"],
        }

    def serialize_city_building_layer_point(self, row):
        data = {
            "parcel_url": row["parcel_url"],
            "building_object_url": row["building_object_url"],
            "object_type": row["object_type"],
            "street": row["street"],
            "address": row["address"],
            "lat": row["lat"],
            "lng": row["lng"],
        }
        return {
            "id": row["id"],
            "layer_key": CITY_BUILDINGS_LAYER_KEY,
            "lat": row["lat"],
            "lng": row["lng"],
            "title": row["parcel_label"],
            "description": row["object_type"] or row["address"] or "",
            "data": data,
            "created_by_name": "Import",
            "created_at": row["updated_at"] or row["imported_at"],
            "type": "",
            "comment": "",
            "is_owner": False,
            "can_edit": False,
            "can_delete": False,
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

    def handle_get_admin_users(self):
        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return

            rows = conn.execute(
                """
                SELECT id, email, name, role, auth_token, last_login_at, created_at, updated_at
                FROM users
                ORDER BY
                  CASE role
                    WHEN 'admin' THEN 0
                    WHEN 'moderator' THEN 1
                    ELSE 2
                  END ASC,
                  email COLLATE NOCASE ASC
                """
            ).fetchall()
            users = [
                self.serialize_admin_user(row, current_user_id=auth_user["id"])
                for row in rows
            ]
            self.write_json(HTTPStatus.OK, {"users": users})
        finally:
            conn.close()

    def handle_update_admin_user(self, user_id: str):
        payload = self.read_json()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid user payload"})
            return

        requested_role = payload.get("role")
        requested_name = payload.get("name")
        revoke_token = payload.get("revoke_token", False)

        role_value = None
        if requested_role is not None:
            if not isinstance(requested_role, str):
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid user role"})
                return
            role_value = requested_role.strip().lower()
            if role_value not in ("admin", "moderator", "user"):
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid user role"})
                return

        name_value = None
        if requested_name is not None:
            if not isinstance(requested_name, str):
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid user name"})
                return
            name_value = normalize_name(requested_name)
            if not name_value:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid user name"})
                return

        if not isinstance(revoke_token, bool):
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid revoke_token value"})
            return

        if role_value is None and name_value is None and not revoke_token:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "No changes requested"})
            return

        conn = get_conn()
        try:
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return

            row = conn.execute(
                """
                SELECT id, email, name, role, auth_token, last_login_at, created_at, updated_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "User not found"})
                return

            updates = []
            params = []

            if role_value is not None and role_value != row["role"]:
                if row["role"] == "admin" and role_value != "admin":
                    admins_left = conn.execute(
                        "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND id <> ?",
                        (row["id"],),
                    ).fetchone()["c"]
                    if admins_left == 0:
                        self.write_json(
                            HTTPStatus.CONFLICT,
                            {"error": "Neni mozne odebrat roli poslednimu adminovi"},
                        )
                        return
                updates.append("role = ?")
                params.append(role_value)

            if name_value is not None and name_value != row["name"]:
                updates.append("name = ?")
                params.append(name_value[:80])

            if revoke_token:
                updates.append("auth_token = NULL")

            if updates:
                sql = f"UPDATE users SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
                params.append(row["id"])
                conn.execute(sql, tuple(params))
                conn.commit()

            refreshed = conn.execute(
                """
                SELECT id, email, name, role, auth_token, last_login_at, created_at, updated_at
                FROM users
                WHERE id = ?
                """,
                (row["id"],),
            ).fetchone()
            self.write_json(
                HTTPStatus.OK,
                {"user": self.serialize_admin_user(refreshed, current_user_id=auth_user["id"])},
            )
        finally:
            conn.close()

    def handle_get_admin_building_parcels(self):
        conn = get_conn()
        try:
            conn.execute(create_city_building_parcels_table_sql())
            migrate_city_building_parcels_table(conn)
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return

            rows = conn.execute(
                """
                SELECT id, source_url, parcel_label, parcel_url, building_object_url,
                       object_type, street, address, lat, lng,
                       has_building, imported_at, updated_at
                FROM city_building_parcels
                ORDER BY updated_at DESC, parcel_label COLLATE NOCASE ASC
                """,
            ).fetchall()
            parcels = [self.serialize_admin_building_parcel(row) for row in rows]
            self.write_json(HTTPStatus.OK, {"parcels": parcels})
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

            if layer_key == CITY_BUILDINGS_LAYER_KEY:
                conn.execute(create_city_building_parcels_table_sql())
                migrate_city_building_parcels_table(conn)
                rows = conn.execute(
                    """
                    SELECT id, parcel_label, parcel_url, building_object_url,
                           object_type, street, address, lat, lng,
                           imported_at, updated_at
                    FROM city_building_parcels
                    WHERE has_building = 1
                    ORDER BY updated_at DESC, parcel_label COLLATE NOCASE ASC
                    """
                ).fetchall()
                points = [self.serialize_city_building_layer_point(row) for row in rows]
                self.write_json(HTTPStatus.OK, {"points": points})
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

    def handle_import_admin_building_parcels_html(self):
        payload = self.read_json()
        if payload is None:
            return

        html_source = payload.get("html", "")
        source_url = payload.get("source_url", "https://nahlizenidokn.cuzk.gov.cz/")
        if not isinstance(html_source, str) or not html_source.strip():
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "Chybi HTML obsah"})
            return
        if len(html_source) > 7_000_000:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "HTML soubor je prilis velky"})
            return
        if not isinstance(source_url, str):
            source_url = "https://nahlizenidokn.cuzk.gov.cz/"
        source_url = source_url.strip() or "https://nahlizenidokn.cuzk.gov.cz/"
        if not source_url.startswith(("http://", "https://")):
            source_url = "https://nahlizenidokn.cuzk.gov.cz/"

        conn = get_conn()
        try:
            conn.execute(create_city_building_parcels_table_sql())
            migrate_city_building_parcels_table(conn)
            auth_user = self.get_auth_user(conn)
            if not auth_user:
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Login required"})
                return
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return
            parsed_parcels = parse_building_parcels_from_html(html_source, source_url)
            if not parsed_parcels:
                self.write_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "Na strance nebyly nalezeny pozemky se stavbou"},
                )
                return

            enriched_parcels = []
            enrichment_failures = 0
            coordinate_failures = 0
            for parcel in parsed_parcels:
                details = {
                    "building_object_url": "",
                    "object_type": "",
                    "street": "",
                    "address": "",
                    "lat": None,
                    "lng": None,
                }
                try:
                    detail_html = fetch_remote_html(parcel["parcel_url"])
                    details = parse_building_detail_from_parcel_html(
                        detail_html, parcel["parcel_url"]
                    )
                    details["lat"] = None
                    details["lng"] = None
                    if details.get("building_object_url"):
                        try:
                            object_html = fetch_remote_html(details["building_object_url"])
                            y, x = parse_epsg2065_coordinates_from_object_html(object_html)
                            lat, lng = convert_epsg2065_to_wgs84(y, x)
                            details["lat"] = lat
                            details["lng"] = lng
                            if lat is None or lng is None:
                                coordinate_failures += 1
                        except URLError:
                            coordinate_failures += 1
                        except TimeoutError:
                            coordinate_failures += 1
                except URLError:
                    enrichment_failures += 1
                except TimeoutError:
                    enrichment_failures += 1

                enriched_parcels.append(
                    {
                        **parcel,
                        **details,
                    }
                )

            inserted_count, updated_count = self.upsert_admin_building_parcels(
                conn, enriched_parcels, source_url
            )
            conn.commit()
            self.write_json(
                HTTPStatus.OK,
                {
                    "imported": len(enriched_parcels),
                    "inserted": inserted_count,
                    "updated": updated_count,
                    "detail_failures": enrichment_failures,
                    "coordinate_failures": coordinate_failures,
                },
            )
        finally:
            conn.close()

    def upsert_admin_building_parcels(
        self, conn: sqlite3.Connection, parsed_parcels: list[dict], source_url: str
    ) -> tuple[int, int]:
        inserted_count = 0
        updated_count = 0
        has_legacy_krovak = city_building_table_has_legacy_krovak(conn)
        for parcel in parsed_parcels:
            existing = conn.execute(
                "SELECT id FROM city_building_parcels WHERE parcel_url = ?",
                (parcel["parcel_url"],),
            ).fetchone()
            object_type_norm, street_norm, address_norm = normalize_city_building_row(
                str(parcel.get("object_type", "")),
                str(parcel.get("street", "")),
                str(parcel.get("address", "")),
            )
            parcel_id = existing["id"] if existing else generate_id("parcel")
            if existing:
                updated_count += 1
            else:
                inserted_count += 1

            conn.execute(
                """
                INSERT INTO city_building_parcels (
                    id, source_url, parcel_label, parcel_url,
                    building_object_url, object_type, street, address,
                    lat, lng, has_building
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(parcel_url) DO UPDATE SET
                    source_url = excluded.source_url,
                    parcel_label = excluded.parcel_label,
                    building_object_url = excluded.building_object_url,
                    object_type = excluded.object_type,
                    street = excluded.street,
                    address = excluded.address,
                    lat = excluded.lat,
                    lng = excluded.lng,
                    has_building = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    parcel_id,
                    source_url[:800],
                    parcel["parcel_label"][:200],
                    parcel["parcel_url"][:800],
                    str(parcel.get("building_object_url", ""))[:800],
                    object_type_norm[:200],
                    street_norm[:200],
                    address_norm[:260],
                    parcel.get("lat"),
                    parcel.get("lng"),
                ),
            )
            if has_legacy_krovak:
                conn.execute(
                    """
                    UPDATE city_building_parcels
                    SET krovak_y = NULL, krovak_x = NULL
                    WHERE id = ?
                    """,
                    (parcel_id,),
                )
        return inserted_count, updated_count

    def handle_refresh_admin_building_coordinates(self):
        conn = get_conn()
        try:
            conn.execute(create_city_building_parcels_table_sql())
            migrate_city_building_parcels_table(conn)
            auth_user = self.get_auth_user(conn)
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return

            rows = conn.execute(
                """
                SELECT id, building_object_url
                FROM city_building_parcels
                ORDER BY updated_at DESC, parcel_label COLLATE NOCASE ASC
                """
            ).fetchall()

            updated = 0
            failed = 0
            skipped = 0
            has_legacy_krovak = city_building_table_has_legacy_krovak(conn)
            for row in rows:
                object_url = row["building_object_url"]
                if not isinstance(object_url, str) or not object_url.strip():
                    skipped += 1
                    continue
                try:
                    object_html = fetch_remote_html(object_url)
                    y, x = parse_epsg2065_coordinates_from_object_html(object_html)
                    lat, lng = convert_epsg2065_to_wgs84(y, x)
                    if lat is None or lng is None:
                        failed += 1
                        continue
                    if has_legacy_krovak:
                        conn.execute(
                            """
                            UPDATE city_building_parcels
                            SET lat = ?, lng = ?, krovak_y = NULL, krovak_x = NULL, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (lat, lng, row["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE city_building_parcels
                            SET lat = ?, lng = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (lat, lng, row["id"]),
                        )
                    updated += 1
                except URLError:
                    failed += 1
                except TimeoutError:
                    failed += 1

            conn.commit()
            self.write_json(
                HTTPStatus.OK,
                {"updated": updated, "failed": failed, "skipped": skipped},
            )
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

    def handle_delete_admin_building_parcels(self):
        conn = get_conn()
        try:
            conn.execute(create_city_building_parcels_table_sql())
            migrate_city_building_parcels_table(conn)
            auth_user = self.get_auth_user(conn)
            if not self.is_admin(auth_user):
                self.write_json(HTTPStatus.FORBIDDEN, {"error": "Admin only"})
                return
            cursor = conn.execute("DELETE FROM city_building_parcels")
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
