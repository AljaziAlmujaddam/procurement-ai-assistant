"""Describes the `purchase_orders` collection to the LLM.

Static field documentation is combined with values read from the live database
(date range, counts, and every value of low-cardinality fields) so the model can
write exact filters instead of guessing spellings.
"""

from functools import lru_cache

from app.db import get_collection

FIELDS: list[tuple[str, str, str]] = [
    ("order_id", "string", "Unique order ID: '<department_name> | <purchase_order_number>'. One order has one or more line items (documents). Count DISTINCT order_id for orders."),
    ("purchase_order_number", "string", "Order number as assigned by the department. NOT unique: different departments reuse the same numbers, so never count orders by this field."),
    ("creation_date", "date", "Date the order was created in the system. Primary date for all time-based questions."),
    ("creation_year", "int", "Calendar year of creation_date, e.g. 2014."),
    ("creation_quarter", "int", "Calendar quarter of creation_date, 1-4 (Q1 = Jan-Mar)."),
    ("creation_month", "int", "Month of creation_date, 1-12."),
    ("creation_year_month", "string", "'YYYY-MM' of creation_date, e.g. '2014-03'."),
    ("fiscal_year", "string", "California fiscal year (July-June), e.g. '2013-2014' = 2013-07-01 to 2014-06-30."),
    ("fiscal_quarter", "int", "Fiscal quarter 1-4 within fiscal_year (Q1 = Jul-Sep, Q2 = Oct-Dec, Q3 = Jan-Mar, Q4 = Apr-Jun)."),
    ("purchase_date", "date|null", "Date the purchase was made. Often missing; use only if explicitly asked."),
    ("department_name", "string", "Purchasing state department, e.g. 'Corrections and Rehabilitation, Department of'."),
    ("supplier_name", "string|null", "Supplier / vendor name (free text, inconsistent casing). 'Unknown' is a placeholder for missing supplier data, not a real supplier; mention this if it appears in rankings."),
    ("supplier_code", "string|null", "Supplier ID; more reliable than supplier_name for grouping."),
    ("supplier_qualifications", "string[]", "Supplier certifications, e.g. ['CA-MB', 'CA-SB']. CA-SB = small business, CA-MB = micro business, CA-DVBE / DVBE = disabled veteran business enterprise, CA-SBE = small business for public works. Empty array if none."),
    ("supplier_zip_code", "string|null", "Supplier 5-digit zip code."),
    ("supplier_location", "GeoJSON Point|null", "Supplier coordinates {type: 'Point', coordinates: [lon, lat]}."),
    ("acquisition_type", "string", "Category of purchase (see values below)."),
    ("sub_acquisition_type", "string|null", "Finer purchase category, mostly for services."),
    ("acquisition_method", "string", "Procurement method used (see values below)."),
    ("sub_acquisition_method", "string|null", "Finer procurement method."),
    ("calcard", "bool", "True if paid with CalCard (state credit card)."),
    ("lpa_number", "string|null", "Leveraged Procurement Agreement (statewide contract) number, if any."),
    ("requisition_number", "string|null", "Requisition number, if any."),
    ("item_name", "string|null", "Line item name as typed by the buyer (free text)."),
    ("item_name_normalized", "string|null", "item_name lowercased with whitespace collapsed. Use for grouping/counting items."),
    ("item_description", "string|null", "Longer free-text line item description."),
    ("quantity", "number|null", "Quantity ordered."),
    ("unit_price", "number|null", "Price per unit in USD."),
    ("total_price", "number|null", "Line item total in USD. Can be negative (credits). Sum this for spending."),
    ("classification_codes", "string[]", "Raw UNSPSC codes entered for the item."),
    ("normalized_unspsc", "string|null", "Primary 8-digit UNSPSC commodity code."),
    ("commodity_title", "string|null", "UNSPSC commodity (most specific standardized product category), e.g. 'Gasoline or Petrol'."),
    ("class_title", "string|null", "UNSPSC class (groups commodities)."),
    ("family_title", "string|null", "UNSPSC family (groups classes)."),
    ("segment_title", "string|null", "UNSPSC segment (broadest category)."),
    ("class_code", "string|null", "UNSPSC class code."),
    ("family_code", "string|null", "UNSPSC family code."),
    ("segment_code", "string|null", "UNSPSC segment code."),
]

CATEGORICAL_FIELDS = [
    "fiscal_year",
    "acquisition_type",
    "sub_acquisition_type",
    "acquisition_method",
    "sub_acquisition_method",
    "segment_title",
    "department_name",
]


@lru_cache
def get_dataset_stats() -> dict:
    collection = get_collection()
    [stats] = collection.aggregate(
        [
            {
                "$group": {
                    "_id": None,
                    "line_items": {"$sum": 1},
                    "first": {"$min": "$creation_date"},
                    "last": {"$max": "$creation_date"},
                    "total_spend": {"$sum": "$total_price"},
                }
            }
        ]
    )
    [orders] = collection.aggregate(
        [{"$group": {"_id": "$order_id"}}, {"$count": "n"}], allowDiskUse=True
    )
    stats["orders"] = orders["n"]
    stats["departments"] = len(collection.distinct("department_name"))
    stats["suppliers"] = len(collection.distinct("supplier_code"))
    stats["fiscal_years"] = sorted(v for v in collection.distinct("fiscal_year") if v)
    return stats


def data_period() -> str:
    """The period the data covers, e.g. 'fiscal years 2012-2013 to 2014-2015 (July 2012 to June 2015)'."""
    stats = get_dataset_stats()
    years = stats["fiscal_years"]
    return f"fiscal years {years[0]} to {years[-1]} ({stats['first']:%B %Y} to {stats['last']:%B %Y})"


@lru_cache
def build_schema_context() -> str:
    collection = get_collection()
    stats = get_dataset_stats()

    lines = [
        f"## Collection `{collection.name}`",
        "Large purchases made by the State of California. Each document is ONE LINE ITEM of a purchase order.",
        f"- {stats['line_items']:,} line items across {stats['orders']:,} distinct purchase orders",
        f"- creation_date range: {stats['first']:%Y-%m-%d} to {stats['last']:%Y-%m-%d} "
        f"(fiscal years {', '.join(stats['fiscal_years'])})",
        f"- total spend (sum of total_price): ${stats['total_spend']:,.0f}",
        "",
        "## Fields",
    ]
    lines += [f"- `{name}` ({type_}): {desc}" for name, type_, desc in FIELDS]

    lines += ["", "## All values of categorical fields (match these exactly)"]
    for field in CATEGORICAL_FIELDS:
        values = sorted(v for v in collection.distinct(field) if v)
        lines.append(f"- `{field}`: " + " | ".join(values))

    return "\n".join(lines)
