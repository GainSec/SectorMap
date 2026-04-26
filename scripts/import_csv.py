#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.server import (
    ACTIVE_DB_POINTER,
    DATA_DIR,
    database_display_name,
    database_file_for_name,
    import_csv_to_db,
    save_active_db_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a CSV into a new SectorMap database.")
    parser.add_argument("csv_path", type=Path, help="Path to a CSV with SectorMap columns.")
    parser.add_argument("--name", required=True, help="Name for the new database.")
    parser.add_argument("--activate", action="store_true", help="Set the imported DB as active.")
    args = parser.parse_args()

    csv_path = args.csv_path.expanduser().resolve()
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    target = database_file_for_name(DATA_DIR, args.name)
    if target.exists():
        raise SystemExit(f"Database already exists: {target}")

    import_csv_to_db(target, csv_path)
    if args.activate:
        save_active_db_path(ACTIVE_DB_POINTER, target)

    print(f"Imported {csv_path} -> {target}")
    print(f"Database name: {database_display_name(target)}")
    print(f"Activated: {'yes' if args.activate else 'no'}")


if __name__ == "__main__":
    main()
