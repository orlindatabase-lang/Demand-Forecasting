"""Canonical DESIGN_GROUP -> VERTICAL mapping.

DESIGN_GROUP is a raw ERP field (14 distinct values in the current data,
e.g. "KURTI SET", "ANARKALI KPD") - granular, but not a business-level
product category. VERTICAL buckets these into broader groups for
reporting, model features, and cold-start neighbor scoping.

There is no VERTICAL field in the ERP itself - this is our own
classification (confirmed against the live data on 2026-07-18: 2.14M
order rows, 14 distinct DESIGN_GROUP values). Update ``_GROUP_TO_VERTICAL``
if new DESIGN_GROUP values show up or the business reclassifies one.
"""
from __future__ import annotations

VERTICAL_UNKNOWN = "Unclassified"

_GROUP_TO_VERTICAL: dict[str, str] = {
    "TOP": "Western Tops/Shirts",
    "SHIRT": "Western Tops/Shirts",
    "KURTI SET": "Kurti/Ethnic Tops",
    "KURTI": "Kurti/Ethnic Tops",
    "MEN'S KURTA": "Kurti/Ethnic Tops",
    "ANARKALI KPD": "Anarkali/Ethnic Dresses",
    "DRESS": "Anarkali/Ethnic Dresses",
    "CO-ORDS": "Co-ord/Matching Sets",
    "SET": "Co-ord/Matching Sets",
    "SHRUGE SET": "Co-ord/Matching Sets",
    "JUMPSUIT": "Fusion/Casual Wear",
    "KAFTAN": "Fusion/Casual Wear",
    "PANT": "Bottoms",
}

# Stable display order (roughly by order volume) - reused anywhere verticals
# are listed (dashboard rollup, model feature category ordering, etc).
VERTICALS: tuple[str, ...] = (
    "Western Tops/Shirts",
    "Kurti/Ethnic Tops",
    "Anarkali/Ethnic Dresses",
    "Co-ord/Matching Sets",
    "Fusion/Casual Wear",
    "Bottoms",
    VERTICAL_UNKNOWN,
)


def vertical_of(design_group) -> str:
    """Map a raw DESIGN_GROUP value to its vertical bucket.

    Never calls bool() on ``design_group`` directly: pandas 3.x's Arrow-backed
    nullable string dtype represents missing values as ``pd.NA``, whose
    __bool__ raises "boolean value of NA is ambiguous" rather than being
    falsy like None/NaN/"". str() is safe on every variant (None, float NaN,
    pd.NA, pd.NaT, a real string), so route the emptiness check through it.
    """
    if design_group is None:
        return VERTICAL_UNKNOWN
    key = str(design_group).strip().upper()
    if key in ("", "NAN", "NONE", "<NA>", "NAT"):
        return VERTICAL_UNKNOWN
    return _GROUP_TO_VERTICAL.get(key, VERTICAL_UNKNOWN)
