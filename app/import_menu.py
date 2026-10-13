"""Safe CSV import for Agraharam POS menu.

Usage:
  python -m app.import_menu path/to/menu.csv
  python -m app.import_menu path/to/menu.csv --apply

Dry-run is the default. Back up the database before using --apply.
CSV columns: name,category,price,code,description,sac_code,active,
             duration_minutes,image
HSN and item-level GST are deliberately not accepted until billing supports them.
"""
import argparse
import csv
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, func

from .db import SessionLocal
from .models import MenuItem, Category
from .schemas import MenuIn

FIELDS = set(MenuIn.model_fields)
REQUIRED = {"name", "category", "price"}
DETAILS = ("code", "duration_minutes", "sac_code", "description", "image")


def normalized(value):
    return " ".join(str(value or "").casefold().split())


def main():
    parser = argparse.ArgumentParser(description="Preview or import Agraharam menu CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--apply", action="store_true", help="Commit new items after validation")
    args = parser.parse_args()
    if not args.csv_path.is_file():
        parser.error("CSV file not found")
    with args.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = set(reader.fieldnames or [])
        if not REQUIRED <= headers:
            parser.error("Missing required columns: " + ", ".join(sorted(REQUIRED - headers)))
        unknown = headers - FIELDS
        if unknown:
            parser.error("Unsupported columns (remove before import): " + ", ".join(sorted(unknown)))
        raw_rows = list(reader)
    if not raw_rows:
        parser.error("CSV has no rows")
    if len(raw_rows) > 1000:
        parser.error("Maximum 1000 rows per import")

    errors, rows, seen_codes, seen_names = [], [], set(), set()
    for index, raw in enumerate(raw_rows, 2):
        try:
            if None in raw:
                raise ValueError("Extra CSV columns")
            values = {k: v for k, v in raw.items() if v is not None and v != ""}
            if "active" in values:
                val = values["active"].strip().lower()
                if val not in ("true", "false", "1", "0", "yes", "no"):
                    raise ValueError("active must be true/false")
                values["active"] = val in ("true", "1", "yes")
            item = MenuIn.model_validate(values)
            code = normalized(item.code)
            name_key = (normalized(item.category), normalized(item.name))
            if code and code in seen_codes:
                raise ValueError("Duplicate code within CSV")
            if name_key in seen_names:
                raise ValueError("Duplicate category/name within CSV")
            if code:
                seen_codes.add(code)
            seen_names.add(name_key)
            rows.append((index, item))
        except (ValueError, ValidationError) as exc:
            errors.append(f"Row {index}: {str(exc)[:350]}")
    if errors:
        print("Validation failed; no changes made:")
        for error in errors:
            print(" -", error)
        raise SystemExit(1)

    with SessionLocal() as db:
        existing = list(db.scalars(select(MenuItem)))
        codes = {normalized((i.service_details or {}).get("code")) for i in existing}
        names = {(normalized(i.category), normalized(i.name)) for i in existing}
        existing_categories = {normalized(c.name): c for c in db.scalars(select(Category))}
        new_items, skipped, blocked = [], [], []
        for row_number, item in rows:
            code = normalized(item.code)
            key = (normalized(item.category), normalized(item.name))
            if (code and code in codes) or key in names:
                skipped.append((row_number, item.name))
                continue
            cat = existing_categories.get(normalized(item.category))
            if cat is not None and not cat.active:
                blocked.append((row_number, item.category))
                continue
            new_items.append((row_number, item))
            if code:
                codes.add(code)
            names.add(key)
        print(f"Rows: {len(rows)} | New: {len(new_items)} | Existing/duplicate: {len(skipped)} | Inactive category: {len(blocked)}")
        for row, name in skipped:
            print(f" SKIP row {row}: {name}")
        for row, category in blocked:
            print(f" BLOCK row {row}: inactive category {category}")
        if blocked:
            print("No changes made: inactive categories require review.")
            raise SystemExit(1)
        if not args.apply:
            print("DRY RUN ONLY. No changes made. Use --apply after reviewing.")
            return
        # Lock the import transaction on PostgreSQL to serialize concurrent imports.
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SELECT pg_advisory_xact_lock(74091201)"))
            # Refresh duplicate checks while holding the import lock.
            current = list(db.scalars(select(MenuItem)))
            live_codes = {normalized((i.service_details or {}).get("code")) for i in current}
            live_names = {(normalized(i.category), normalized(i.name)) for i in current}
            if any((normalized(item.code) and normalized(item.code) in live_codes) or
                   (normalized(item.category), normalized(item.name)) in live_names
                   for _, item in new_items):
                print("Existing menu changed since preview; aborting. Run dry run again.")
                raise SystemExit(1)
        for _, item in new_items:
            data = item.model_dump()
            details = {k: data.pop(k) for k in DETAILS}
            category = existing_categories.get(normalized(item.category))
            if category is None:
                category = Category(id=str(uuid4()), name=item.category, active=True)
                db.add(category)
                existing_categories[normalized(item.category)] = category
            db.add(MenuItem(id=str(uuid4()), service_details=details, **data))
        db.commit()
        print(f"Committed {len(new_items)} new menu items. Existing items unchanged.")


if __name__ == "__main__":
    main()
