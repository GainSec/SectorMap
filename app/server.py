import csv
import shutil
import io
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SECTORMAP_DATA_DIR", str(APP_DIR / "data"))).resolve()
STATIC_DIR = Path(os.getenv("SECTORMAP_STATIC_DIR", str(APP_DIR / "static"))).resolve()
DEFAULT_DB_PATH = DATA_DIR / os.getenv("SECTORMAP_DEFAULT_DB", "archive.db")
DEFAULT_CSV_PATH = Path(os.getenv("SECTORMAP_DEFAULT_CSV", str(APP_DIR / "seed" / "default.csv"))).resolve()
ACTIVE_DB_POINTER = DATA_DIR / "active_db.json"

LOCAL_HEADER = [
    "title",
    "url",
    "type",
    "candidate_types",
    "tags",
    "description",
    "source",
]

ALLOWED_TYPES = [
    "firmware analysis",
    "reverse engineering",
    "web application",
    "mobile application",
    "RF",
    "embedded systems",
    "IoT",
    "hardware",
    "software",
    "fuzzing",
    "debug interfaces",
    "internal networks",
    "external networks",
    "cloud",
    "host based",
    "OSINT",
    "social engineering",
    "red team",
    "wireless",
    "physical",
]

SEARCHABLE_FIELDS = {
    "all": [
        "title",
        "description",
        "tags",
        "type",
        "candidate_types",
        "source",
        "url",
    ],
    "title": ["title"],
    "description": ["description"],
    "tags": ["tags"],
    "type": ["type", "candidate_types"],
    "source": ["source"],
    "url": ["url"],
}

RELATIONSHIP_MODES = ["type", "tags", "source", "domain", "text_similarity"]


class RecordUpdatePayload(BaseModel):
    title: str = ""
    description: str = ""
    type: str = ""
    tags: str = ""


class CustomTypePayload(BaseModel):
    name: str


class DatabaseCreatePayload(BaseModel):
    name: str


class DatabaseSwitchPayload(BaseModel):
    name: str


def get_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT '',
            candidate_types TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS custom_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_records_url_title
        ON records(url, title);

        DROP TRIGGER IF EXISTS records_ai;
        DROP TRIGGER IF EXISTS records_ad;
        DROP TRIGGER IF EXISTS records_au;
        DROP TABLE IF EXISTS records_fts;

        CREATE VIRTUAL TABLE records_fts USING fts5(
            title,
            url,
            tags,
            type,
            candidate_types,
            description,
            source,
            content='records',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
            INSERT INTO records_fts(rowid, title, url, tags, type, candidate_types, description, source)
            VALUES (new.id, new.title, new.url, new.tags, new.type, new.candidate_types, new.description, new.source);
        END;

        CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
            INSERT INTO records_fts(records_fts, rowid, title, url, tags, type, candidate_types, description, source)
            VALUES ('delete', old.id, old.title, old.url, old.tags, old.type, old.candidate_types, old.description, old.source);
        END;

        CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
            INSERT INTO records_fts(records_fts, rowid, title, url, tags, type, candidate_types, description, source)
            VALUES ('delete', old.id, old.title, old.url, old.tags, old.type, old.candidate_types, old.description, old.source);
            INSERT INTO records_fts(rowid, title, url, tags, type, candidate_types, description, source)
            VALUES (new.id, new.title, new.url, new.tags, new.type, new.candidate_types, new.description, new.source);
        END;
        """
    )
    connection.executemany(
        "INSERT OR IGNORE INTO custom_types(name) VALUES (?)",
        [(value,) for value in ALLOWED_TYPES],
    )
    connection.commit()


def import_csv_to_db(db_path: Path, csv_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as connection:
        initialize_schema(connection)
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = [
                tuple((row.get(column, "") or "").strip() for column in LOCAL_HEADER)
                for row in reader
            ]
        connection.executemany(
            """
            INSERT OR IGNORE INTO records
            (title, url, type, candidate_types, tags, description, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def validate_existing_sectormap_db(db_path: Path) -> None:
    required_columns = {"id", *LOCAL_HEADER}
    try:
        with sqlite3.connect(db_path) as connection:
            table_row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'records'"
            ).fetchone()
            if table_row is None:
                raise ValueError("Missing records table")
            column_rows = connection.execute("PRAGMA table_info(records)").fetchall()
            existing_columns = {str(row[1]) for row in column_rows}
            if not required_columns.issubset(existing_columns):
                raise ValueError("Records table is missing required columns")
            connection.execute("SELECT COUNT(*) FROM records").fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Uploaded file is not a valid SectorMap database") from exc


def import_db_file_to_db(db_path: Path, source_db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    validate_existing_sectormap_db(source_db_path)
    shutil.copyfile(source_db_path, db_path)
    with get_connection(db_path) as connection:
        initialize_schema(connection)


def fetch_record_count(db_path: Path) -> int:
    with get_connection(db_path) as connection:
        initialize_schema(connection)
        row = connection.execute("SELECT COUNT(*) AS count FROM records").fetchone()
        return int(row["count"])


def ensure_database(db_path: Path, csv_path: Path) -> None:
    if not db_path.exists():
        import_csv_to_db(db_path, csv_path)
        return
    if fetch_record_count(db_path) == 0:
        import_csv_to_db(db_path, csv_path)


def ensure_empty_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as connection:
        initialize_schema(connection)


def sanitize_database_name(raw_name: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", " "} else "_"
        for char in raw_name.strip()
    ).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned or "archive"


def database_file_for_name(data_dir: Path, raw_name: str) -> Path:
    return data_dir / f"{sanitize_database_name(raw_name)}.db"


def database_display_name(db_path: Path) -> str:
    return db_path.stem


def remove_database_files(db_path: Path) -> None:
    for candidate in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]:
        if candidate.exists():
            candidate.unlink()


def load_active_db_path(data_dir: Path, default_db_path: Path, pointer_path: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    if pointer_path.exists():
        try:
            payload = json.loads(pointer_path.read_text(encoding="utf-8"))
            name = str(payload.get("name", "")).strip()
            if name:
                candidate = database_file_for_name(data_dir, name)
                if candidate.exists():
                    return candidate
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    if default_db_path.exists():
        return default_db_path
    existing = sorted(data_dir.glob("*.db"))
    if existing:
        return existing[0]
    return default_db_path


def save_active_db_path(pointer_path: Path, db_path: Path) -> None:
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        json.dumps({"name": database_display_name(db_path)}, indent=2) + "\n",
        encoding="utf-8",
    )


def list_databases(data_dir: Path, active_db_path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for db_file in sorted(data_dir.glob("*.db")):
        entries.append(
            {
                "name": database_display_name(db_file),
                "filename": db_file.name,
                "path": str(db_file),
                "active": db_file.resolve() == active_db_path.resolve(),
                "size_bytes": db_file.stat().st_size,
            }
        )
    return entries


def describe_database(db_path: Path, active_db_path: Path) -> dict[str, Any]:
    return {
        "name": database_display_name(db_path),
        "filename": db_path.name,
        "path": str(db_path),
        "active": db_path.resolve() == active_db_path.resolve(),
    }


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def extract_domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def split_tags(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def tokenize_text(value: str) -> list[str]:
    tokens: list[str] = []
    current = []
    for char in value.lower():
        if char.isalnum():
            current.append(char)
        elif current:
            token = "".join(current)
            if len(token) >= 4:
                tokens.append(token)
            current = []
    if current:
        token = "".join(current)
        if len(token) >= 4:
            tokens.append(token)
    return tokens


def build_search_query(query: str, field: str) -> tuple[str, list[Any], str, list[Any]]:
    columns = SEARCHABLE_FIELDS.get(field, SEARCHABLE_FIELDS["all"])
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    if not terms:
        return "", [], "0", []
    where_parts: list[str] = []
    where_params: list[Any] = []
    score_parts: list[str] = []
    score_params: list[Any] = []
    for term in terms:
        like_value = f"%{term}%"
        where_parts.append(
            "(" + " OR ".join([f"LOWER({column}) LIKE ?" for column in columns]) + ")"
        )
        where_params.extend([like_value] * len(columns))
        score_parts.extend([f"CASE WHEN LOWER({column}) LIKE ? THEN 1 ELSE 0 END" for column in columns])
        score_params.extend([like_value] * len(columns))
    where_sql = " AND ".join(where_parts)
    score_sql = " + ".join(score_parts) if score_parts else "0"
    return where_sql, where_params, score_sql, score_params


def fetch_records_for_graph(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, title, url, type, candidate_types, tags, description, source
        FROM records
        ORDER BY id ASC
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def derive_text_buckets(records: list[dict[str, Any]]) -> dict[int, str]:
    token_counts: Counter[str] = Counter()
    record_tokens: dict[int, list[str]] = {}
    for record in records:
        tokens = tokenize_text(f"{record['title']} {record['description']}")
        record_tokens[record["id"]] = tokens
        token_counts.update(set(tokens))
    top_tokens = {token for token, _ in token_counts.most_common(40)}
    buckets: dict[int, str] = {}
    for record in records:
        for token in record_tokens[record["id"]]:
            if token in top_tokens:
                buckets[record["id"]] = token
                break
    return buckets


def group_records(records: list[dict[str, Any]], mode: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    text_buckets = derive_text_buckets(records) if mode == "text_similarity" else {}
    tag_counts: Counter[str] = Counter(tag for record in records for tag in split_tags(record["tags"]))
    top_tags = {tag for tag, _ in tag_counts.most_common(30)}
    for record in records:
        key = "untyped"
        if mode == "type":
            key = record["type"].strip().lower() or "untyped"
        elif mode == "source":
            key = record["source"].strip().lower() or "unknown-source"
        elif mode == "domain":
            key = extract_domain(record["url"]) or "unknown-domain"
        elif mode == "tags":
            tags = [tag for tag in split_tags(record["tags"]) if tag in top_tags]
            key = tags[0] if tags else "untagged"
        elif mode == "text_similarity":
            key = text_buckets.get(record["id"], "misc-similarity")
        groups[key].append(record)
    return groups


def build_graph_payload(records: list[dict[str, Any]], mode: str, focus_key: str | None) -> dict[str, Any]:
    groups = group_records(records, mode)
    ordered_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    clusters: list[dict[str, Any]] = []
    visible_groups = ordered_groups[:18]
    positioned_clusters: list[dict[str, Any]] = []
    total_visible = max(1, len(visible_groups))

    for index, (key, members) in enumerate(visible_groups):
        count = len(members)
        body_kind = "sun" if count >= 120 else "planet" if count >= 35 else "moon"
        angle = (index / total_visible) * 6.28318
        radius = 48 + (index % 6) * 22 + (index // 6) * 18
        weight = 26 if body_kind == "sun" else 18 if body_kind == "planet" else 12
        positioned_clusters.append(
            {
                "key": key,
                "label": key.replace("-", " ").title(),
                "count": count,
                "body_kind": body_kind,
                "x": 50 + radius * math.cos(angle),
                "y": 50 + radius * math.sin(angle) * 0.74,
                "weight": weight,
                "sample_records": members[:12],
            }
        )

    for _ in range(18):
        moved = False
        for index, cluster in enumerate(positioned_clusters):
            for other in positioned_clusters[index + 1:]:
                dx = other["x"] - cluster["x"]
                dy = other["y"] - cluster["y"]
                distance = math.hypot(dx, dy) or 0.01
                minimum_distance = cluster["weight"] + other["weight"]
                if distance >= minimum_distance:
                    continue
                overlap = (minimum_distance - distance) / 2
                push_x = (dx / distance) * overlap
                push_y = (dy / distance) * overlap
                cluster["x"] -= push_x
                cluster["y"] -= push_y
                other["x"] += push_x
                other["y"] += push_y
                moved = True
        for cluster in positioned_clusters:
            cluster["x"] = max(-48, min(148, cluster["x"]))
            cluster["y"] = max(-28, min(128, cluster["y"]))
        if not moved:
            break

    for cluster in positioned_clusters:
        clusters.append(
            {
                "key": cluster["key"],
                "label": cluster["label"],
                "count": cluster["count"],
                "body_kind": cluster["body_kind"],
                "x": round(cluster["x"], 2),
                "y": round(cluster["y"], 2),
                "sample_records": cluster["sample_records"],
            }
        )
    focus_entry = None
    if not focus_key and clusters:
        focus_key = clusters[0]["key"]
    if focus_key and focus_key in groups:
        members = groups[focus_key]
        focus_entry = {
            "key": focus_key,
            "label": focus_key.replace("-", " ").title(),
            "count": len(members),
            "records": members[:120],
        }
    return {
        "mode": mode,
        "available_modes": RELATIONSHIP_MODES,
        "clusters": clusters,
        "focus": focus_entry,
    }


def find_focus_key_for_record(records: list[dict[str, Any]], mode: str, record_id: int) -> str | None:
    groups = group_records(records, mode)
    for key, members in groups.items():
        if any(member["id"] == record_id for member in members):
            return key
    return None


def build_filters(
    query: str | None,
    field: str,
    type_filter: str | None,
    source_filter: str | None,
    tag_filter: str | None,
) -> tuple[str, list[Any], str, list[Any]]:
    clauses: list[str] = []
    where_params: list[Any] = []
    score_sql = "0"
    score_params: list[Any] = []
    if type_filter:
        clauses.append("type = ?")
        where_params.append(type_filter)
    if source_filter:
        clauses.append("source = ?")
        where_params.append(source_filter)
    if tag_filter:
        clauses.append("LOWER(tags) LIKE ?")
        where_params.append(f"%{tag_filter.lower()}%")
    if query:
        where_sql, query_where_params, score_sql, score_params = build_search_query(query, field)
        if where_sql:
            clauses.append(f"({where_sql})")
            where_params.extend(query_where_params)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_clause, where_params, score_sql, score_params


def create_app(
    db_path: Path = DEFAULT_DB_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
    static_dir: Path = STATIC_DIR,
    data_dir: Path = DATA_DIR,
    active_db_pointer: Path | None = None,
) -> FastAPI:
    active_pointer = active_db_pointer or (data_dir / "active_db.json")
    data_dir.mkdir(parents=True, exist_ok=True)
    if db_path.parent != data_dir:
        db_path = data_dir / db_path.name
    active_path = load_active_db_path(data_dir, db_path, active_pointer)
    if csv_path.exists():
        ensure_database(active_path, csv_path)
    else:
        ensure_empty_database(active_path)
    save_active_db_path(active_pointer, active_path)

    app = FastAPI(title="Stitch Archive Server")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def root() -> FileResponse:
        file_path = (static_dir / "MainOperations.html").resolve()
        return FileResponse(file_path)

    @app.get("/galaxy")
    def galaxy_route() -> FileResponse:
        file_path = (static_dir / "GalaxyViewMain.html").resolve()
        return FileResponse(file_path)

    @app.get("/manage")
    def manage_route() -> FileResponse:
        file_path = (static_dir / "AdminDashboard.html").resolve()
        return FileResponse(file_path)

    def get_active_db_path() -> Path:
        active = load_active_db_path(data_dir, db_path, active_pointer)
        if not active.exists():
            ensure_empty_database(active)
        save_active_db_path(active_pointer, active)
        return active

    def set_active_database(target: Path) -> Path:
        ensure_empty_database(target)
        save_active_db_path(active_pointer, target)
        return target

    @app.get("/api/databases")
    def fetch_databases() -> dict[str, Any]:
        active = get_active_db_path()
        return {
            "active": describe_database(active, active),
            "databases": list_databases(data_dir, active),
        }

    @app.post("/api/databases")
    def create_database(payload: DatabaseCreatePayload) -> dict[str, Any]:
        name = sanitize_database_name(payload.name)
        if not name:
            raise HTTPException(status_code=400, detail="Database name is required")
        target = database_file_for_name(data_dir, name)
        if target.exists():
            raise HTTPException(status_code=409, detail="Database already exists")
        ensure_empty_database(target)
        active = set_active_database(target)
        return {
            "database": describe_database(target, active),
            "name": database_display_name(target),
            "active": True,
            "databases": list_databases(data_dir, active),
        }

    @app.post("/api/databases/switch")
    def switch_database(payload: DatabaseSwitchPayload) -> dict[str, Any]:
        target = database_file_for_name(data_dir, payload.name)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Database not found")
        active = set_active_database(target)
        return {
            "active": describe_database(active, active),
            "databases": list_databases(data_dir, active),
        }

    @app.post("/api/databases/reset")
    def reset_database() -> dict[str, Any]:
        active = get_active_db_path()
        remove_database_files(active)
        ensure_empty_database(active)
        active = set_active_database(active)
        return {
            "active": describe_database(active, active),
            "databases": list_databases(data_dir, active),
        }

    @app.post("/api/databases/import")
    async def import_database(
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
    ) -> dict[str, Any]:
        uploaded_name = file.filename or "archive.csv"
        raw_name = name or Path(uploaded_name).stem
        target = database_file_for_name(data_dir, raw_name)
        if target.exists():
            raise HTTPException(status_code=409, detail="Database already exists")
        payload = await file.read()
        suffix = Path(uploaded_name).suffix.lower()
        if suffix in {".db", ".sqlite", ".sqlite3"}:
            temp_file = data_dir / f".tmp-import-{sanitize_database_name(raw_name)}.db"
        else:
            temp_file = data_dir / f".tmp-import-{sanitize_database_name(raw_name)}.csv"
        temp_file.write_bytes(payload)
        try:
            if temp_file.suffix == ".csv":
                import_csv_to_db(target, temp_file)
            else:
                import_db_file_to_db(target, temp_file)
        except ValueError as exc:
            remove_database_files(target)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if temp_file.exists():
                temp_file.unlink()
        active = set_active_database(target)
        return {
            "database": describe_database(target, active),
            "name": database_display_name(target),
            "active": True,
            "databases": list_databases(data_dir, active),
        }

    @app.get("/api/records")
    def list_records(
        q: str | None = Query(default=None),
        field: str = Query(default="all"),
        type: str | None = Query(default=None),
        source: str | None = Query(default=None),
        tag: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        where_clause, where_params, score_sql, score_params = build_filters(q, field, type, source, tag)
        with get_connection(get_active_db_path()) as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) AS count FROM records {where_clause}",
                where_params,
            ).fetchone()
            records = connection.execute(
                f"""
                SELECT id, title, url, type, candidate_types, tags, description, source,
                       {score_sql} AS score
                FROM records
                {where_clause}
                ORDER BY score DESC, id ASC
                LIMIT ? OFFSET ?
                """,
                [*score_params, *where_params, limit, offset],
            ).fetchall()
        return {
            "total": int(total_row["count"]),
            "limit": limit,
            "offset": offset,
            "records": [row_to_dict(row) for row in records],
        }

    @app.get("/api/records/{record_id}")
    def get_record(record_id: int) -> dict[str, Any]:
        with get_connection(get_active_db_path()) as connection:
            row = connection.execute(
                """
                SELECT id, title, url, type, candidate_types, tags, description, source
                FROM records
                WHERE id = ?
                """,
                [record_id],
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return row_to_dict(row)

    @app.get("/api/types")
    def list_types() -> dict[str, Any]:
        with get_connection(get_active_db_path()) as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM custom_types
                ORDER BY name ASC
                """
            ).fetchall()
        return {"types": [row["name"] for row in rows]}

    @app.post("/api/types")
    def create_type(payload: CustomTypePayload) -> dict[str, Any]:
        normalized_name = payload.name.strip().lower()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Type name is required")
        with get_connection(get_active_db_path()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO custom_types(name) VALUES (?)",
                [normalized_name],
            )
            connection.commit()
        return {"name": normalized_name}

    @app.post("/api/records/{record_id}")
    def update_record(record_id: int, payload: RecordUpdatePayload) -> dict[str, Any]:
        normalized_title = payload.title.strip()
        normalized_description = payload.description.strip()
        normalized_type = payload.type.strip()
        normalized_tags = ", ".join(
            dict.fromkeys(tag.strip() for tag in payload.tags.split(",") if tag.strip())
        )
        with get_connection(get_active_db_path()) as connection:
            valid_type_row = None
            if normalized_type:
                valid_type_row = connection.execute(
                    "SELECT name FROM custom_types WHERE name = ?",
                    [normalized_type],
                ).fetchone()
            if normalized_type and valid_type_row is None:
                raise HTTPException(status_code=400, detail="Invalid type")
            existing = connection.execute(
                "SELECT id FROM records WHERE id = ?",
                [record_id],
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Record not found")
            connection.execute(
                """
                UPDATE records
                SET title = ?, description = ?, type = ?, tags = ?
                WHERE id = ?
                """,
                [
                    normalized_title,
                    normalized_description,
                    normalized_type,
                    normalized_tags,
                    record_id,
                ],
            )
            connection.commit()
            updated = connection.execute(
                """
                SELECT id, title, url, type, candidate_types, tags, description, source
                FROM records
                WHERE id = ?
                """,
                [record_id],
            ).fetchone()
        return row_to_dict(updated)

    @app.get("/api/search")
    def search_records(
        q: str = Query(..., min_length=1),
        field: str = Query(default="all"),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        where_sql, where_params, score_sql, score_params = build_search_query(q, field)
        with get_connection(get_active_db_path()) as connection:
            rows = connection.execute(
                """
                SELECT
                    records.id,
                    records.title,
                    records.url,
                    records.type,
                    records.candidate_types,
                    records.tags,
                    records.description,
                    records.source,
                    """
                    + score_sql
                    + """
                    AS score
                FROM records
                WHERE """
                    + where_sql
                    + """
                ORDER BY score DESC, id ASC
                LIMIT ?
                """,
                [*score_params, *where_params, limit],
            ).fetchall()
        return {"query": q, "field": field, "results": [row_to_dict(row) for row in rows]}

    @app.get("/api/graph")
    def fetch_graph(
        mode: str = Query(default="type"),
        focus_key: str | None = Query(default=None),
    ) -> dict[str, Any]:
        normalized_mode = mode if mode in RELATIONSHIP_MODES else "type"
        with get_connection(get_active_db_path()) as connection:
            records = fetch_records_for_graph(connection)
        return build_graph_payload(records, normalized_mode, focus_key)

    @app.get("/api/graph/focus")
    def fetch_graph_for_record(
        record_id: int = Query(..., ge=1),
        mode: str = Query(default="type"),
    ) -> dict[str, Any]:
        normalized_mode = mode if mode in RELATIONSHIP_MODES else "type"
        with get_connection(get_active_db_path()) as connection:
            records = fetch_records_for_graph(connection)
        focus_key = find_focus_key_for_record(records, normalized_mode, record_id)
        if focus_key is None:
            raise HTTPException(status_code=404, detail="Record not found in graph")
        payload = build_graph_payload(records, normalized_mode, focus_key)
        payload["selected_record_id"] = record_id
        return payload

    @app.get("/api/stats")
    def fetch_stats() -> dict[str, Any]:
        with get_connection(get_active_db_path()) as connection:
            totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(DISTINCT source) AS total_sources,
                    COUNT(DISTINCT CASE WHEN type != '' THEN type END) AS total_types
                FROM records
                """
            ).fetchone()
            source_rows = connection.execute(
                """
                SELECT source, COUNT(*) AS count
                FROM records
                WHERE source != ''
                GROUP BY source
                ORDER BY count DESC, source ASC
                """
            ).fetchall()
            type_rows = connection.execute(
                """
                SELECT type, COUNT(*) AS count
                FROM records
                WHERE type != ''
                GROUP BY type
                ORDER BY count DESC, type ASC
                LIMIT 8
                """
            ).fetchall()
        return {
            "total_records": int(totals["total_records"]),
            "total_sources": int(totals["total_sources"]),
            "total_types": int(totals["total_types"]),
            "top_sources": [row_to_dict(row) for row in source_rows],
            "top_types": [row_to_dict(row) for row in type_rows],
        }

    @app.get("/api/export")
    def export_records(ids: str | None = Query(default=None)) -> StreamingResponse:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=LOCAL_HEADER)
        writer.writeheader()
        query = """
            SELECT title, url, type, candidate_types, tags, description, source
            FROM records
        """
        params: list[Any] = []
        if ids:
            parsed_ids = [int(value) for value in ids.split(",") if value.strip()]
            placeholders = ", ".join("?" for _ in parsed_ids)
            query += f" WHERE id IN ({placeholders})"
            params.extend(parsed_ids)
        query += " ORDER BY id ASC"
        with get_connection(get_active_db_path()) as connection:
            rows = connection.execute(query, params).fetchall()
        for row in rows:
            writer.writerow(row_to_dict(row))
        output.seek(0)
        headers = {
            "Content-Disposition": 'attachment; filename="etc-channel-merged-normalized.csv"'
        }
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

    @app.get("/{asset_path:path}")
    def serve_static_asset(asset_path: str) -> FileResponse:
        asset_name = asset_path or "MainOperations.html"
        file_path = (static_dir / asset_name).resolve()
        if not str(file_path).startswith(str(static_dir.resolve())) or not file_path.exists():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(file_path)

    return app


app = create_app()
