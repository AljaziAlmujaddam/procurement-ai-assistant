"""Clean the California purchase-order CSV and load it into MongoDB.

Each CSV row is one *line item*; several line items can share a purchase order number.

Usage (from backend/):
    .venv/bin/python -m scripts.load_data [path/to/file.csv]
"""

import math
import re
import sys
import time
from pathlib import Path

import pandas as pd
from pymongo import ASCENDING, DESCENDING, MongoClient

from app.config import BACKEND_DIR, get_settings

DEFAULT_CSV = BACKEND_DIR.parent / "data" / "raw" / "purchase_orders_2012_2015.csv"
BATCH_SIZE = 10_000

# Dataset covers fiscal years 2012-13 to 2014-15; purchase dates outside this window are data-entry errors.
VALID_PURCHASE_DATE_RANGE = (pd.Timestamp("2000-01-01"), pd.Timestamp("2016-12-31"))

COLUMN_MAP = {
    "Creation Date": "creation_date",
    "Purchase Date": "purchase_date",
    "Fiscal Year": "fiscal_year",
    "LPA Number": "lpa_number",
    "Purchase Order Number": "purchase_order_number",
    "Requisition Number": "requisition_number",
    "Acquisition Type": "acquisition_type",
    "Sub-Acquisition Type": "sub_acquisition_type",
    "Acquisition Method": "acquisition_method",
    "Sub-Acquisition Method": "sub_acquisition_method",
    "Department Name": "department_name",
    "Supplier Code": "supplier_code",
    "Supplier Name": "supplier_name",
    "Supplier Qualifications": "supplier_qualifications",
    "Supplier Zip Code": "supplier_zip_code",
    "CalCard": "calcard",
    "Item Name": "item_name",
    "Item Description": "item_description",
    "Quantity": "quantity",
    "Unit Price": "unit_price",
    "Total Price": "total_price",
    "Classification Codes": "classification_codes",
    "Normalized UNSPSC": "normalized_unspsc",
    "Commodity Title": "commodity_title",
    "Class": "class_code",
    "Class Title": "class_title",
    "Family": "family_code",
    "Family Title": "family_title",
    "Segment": "segment_code",
    "Segment Title": "segment_title",
    "Location": "location",
}

LAT_LON_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def parse_money(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(r"[$,]", "", regex=True), errors="coerce")


def parse_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")


def split_list(s: pd.Series, sep: str | None = None) -> pd.Series:
    return s.map(lambda v: v.split(sep) if isinstance(v, str) else [])


def parse_geo_point(value) -> dict | None:
    """'95841\\n(38.66, -121.34)' -> GeoJSON Point (GeoJSON order is [lon, lat])."""
    if not isinstance(value, str):
        return None
    m = LAT_LON_RE.search(value)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return {"type": "Point", "coordinates": [lon, lat]}


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=COLUMN_MAP)
    df = df.apply(lambda col: col.str.strip()).replace("", None)

    df["creation_date"] = parse_date(df["creation_date"])
    purchase = parse_date(df["purchase_date"])
    lo, hi = VALID_PURCHASE_DATE_RANGE
    df["purchase_date"] = purchase.where(purchase.between(lo, hi))

    created = df["creation_date"].dt
    df["creation_year"] = created.year
    df["creation_month"] = created.month
    df["creation_quarter"] = created.quarter
    df["creation_year_month"] = created.strftime("%Y-%m")
    # California fiscal year runs July-June: Jul-Sep = Q1 ... Apr-Jun = Q4.
    df["fiscal_quarter"] = (created.month - 7) % 12 // 3 + 1

    df["quantity"] = pd.to_numeric(df["quantity"].str.replace(",", ""), errors="coerce")
    df["unit_price"] = parse_money(df["unit_price"])
    df["total_price"] = parse_money(df["total_price"])

    # Item names are free text typed in many spellings ("Medical Supplies", "medical supplies ").
    df["item_name_normalized"] = (
        df["item_name"].str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    )

    # Departments number their purchase orders independently, so the same number can belong to
    # orders of different departments. An order is identified by department + order number.
    df["order_id"] = df["department_name"] + " | " + df["purchase_order_number"]

    df["calcard"] = df["calcard"].eq("YES")
    df["supplier_qualifications"] = split_list(df["supplier_qualifications"])
    df["classification_codes"] = split_list(df["classification_codes"], "\n")
    df["supplier_location"] = df.pop("location").map(parse_geo_point)

    return df


def to_mongo_value(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):  # numpy scalar -> native Python
        return v.item()
    return v


def iter_documents(df: pd.DataFrame):
    columns = list(df.columns)
    for row in df.itertuples(index=False, name=None):
        yield {col: to_mongo_value(val) for col, val in zip(columns, row)}


def create_indexes(collection) -> None:
    for field in [
        "creation_date",
        "creation_year_month",
        "fiscal_year",
        "purchase_order_number",
        "order_id",
        "department_name",
        "supplier_name",
        "acquisition_type",
        "acquisition_method",
        "item_name_normalized",
        "normalized_unspsc",
        "commodity_title",
        "segment_title",
        "family_title",
    ]:
        collection.create_index([(field, ASCENDING)])
    collection.create_index([("creation_year", ASCENDING), ("creation_quarter", ASCENDING)])
    collection.create_index([("fiscal_year", ASCENDING), ("fiscal_quarter", ASCENDING)])
    collection.create_index([("total_price", DESCENDING)])


def main(csv_path: Path) -> None:
    settings = get_settings()
    t0 = time.perf_counter()

    print(f"Reading {csv_path} ...")
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    print(f"  {len(raw):,} rows, {len(raw.columns)} columns")

    print("Cleaning ...")
    df = transform(raw)
    dropped_dates = raw["Purchase Date"].str.strip().ne("").sum() - df["purchase_date"].notna().sum()
    print(f"  nulled {dropped_dates:,} implausible purchase dates")

    client = MongoClient(settings.mongodb_uri)
    collection = client[settings.mongodb_db][settings.mongodb_collection]
    collection.drop()

    print(f"Inserting into {settings.mongodb_db}.{settings.mongodb_collection} ...")
    batch, inserted = [], 0
    for doc in iter_documents(df):
        batch.append(doc)
        if len(batch) == BATCH_SIZE:
            collection.insert_many(batch, ordered=False)
            inserted += len(batch)
            batch.clear()
            print(f"  {inserted:,}", end="\r")
    if batch:
        collection.insert_many(batch, ordered=False)
        inserted += len(batch)
    print(f"  inserted {inserted:,} documents")

    print("Creating indexes ...")
    create_indexes(collection)

    print(f"Done in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV)
