"""
Design-attribute similarity for demand-forecasting cold start.

similar_design.py's neighbor graph is built from the ERP article-master
view's USED_IN column - effectively a per-design Bill-of-Materials
fingerprint. That fingerprint only exists once raw material has actually
been *issued* against a design, i.e. once production has started. A design
that's been designed and costed but never yet put into production has no
BOM fingerprint at all, so it gets zero neighbors from that graph - the
true cold-start case.

This module builds a second, independent similarity graph from
SKU_Master_Data_Final_Cleaned.csv (repo root) - a per-SKU garment design
sheet (category, fabric, color, embroidery, print, neck/sleeve style for
top and bottom) that's known at DESIGN stage, before a single unit is cut.
It covers designs the BOM-based graph has no entry for at all.

Feature engineering mirrors similar_design.py: explode each design's
descriptive columns into "COLUMN:VALUE" tokens (placeholder values like
"Unknown"/"Not Applied"/"-" dropped), then IDF-weighted-Jaccard neighbors,
compared only within the same top-level CATEGORY so two designs sharing a
common fabric (e.g. "SILK") aren't treated as demand-similar just because
a shirt and a lehenga both happen to use it. See _build_neighbors() for why
this uses a plain pairwise scan rather than similar_design.py's inverted-
index approach - the two source columns have very different fan-out.

Standalone by design: no ERP calls, no dependency on similar_design.py.
similar_design.py imports this module and uses it as a *fallback*, filling
in neighbors only for designs its own BOM graph has no entry for - a
design that already has >=1 BOM-based neighbor is left untouched.
"""
from __future__ import annotations

import itertools
import math
import sys
import threading
from pathlib import Path

_CSV_PATH = Path(__file__).resolve().parent.parent / "SKU_Master_Data_Final_Cleaned.csv"

_TOP_K = 6

# Source column -> short token prefix. Only descriptive/style columns -
# NO PIECES, TOP/BOTTOM LENGTH, Designer_Name, Brand deliberately excluded
# (sizing and catalog/vendor grouping, not a garment-similarity signal).
_ATTR_COLS = {
    "SUB CATEGORY": "SUBCAT",
    "Top type": "TOPTYPE",
    "Top_Color": "TOPCOLOR",
    "TOP_Fabric": "TOPFABRIC",
    "Top Emboidery_Type": "TOPEMB",
    "Bottom Type": "BOTTYPE",
    "Bottom_Color": "BOTCOLOR",
    "Bottom_Fabric": "BOTFABRIC",
    "Bottom Embroidery_Type": "BOTEMB",
    "Print_Type": "PRINT",
    "NECK STYLE": "NECK",
    "Sleeve Pattern": "SLVPAT",
    "SLEEVE TYPE": "SLVTYPE",
}
_CATEGORY_COL = "CATEGORY"
_SUBCAT_COL = "SUB CATEGORY"
_STYLE_COL = "STYLE NO"
# Primary garment color shown to users as a design's "color" (2026-09-14,
# user-requested: which color sells best during a festival/sale). Top_Color
# is used rather than Bottom_Color/PALETTE COLOR - by far the best-covered
# of the three in the current sheet (~98% vs ~76%/~61%).
_COLOR_COL = "Top_Color"

# Columns exposed as direct per-design categorical FEATURES to the demand-
# forecasting model itself (lgbm_forecast.py's compute()/compute_design(),
# see that module's _MASTER_ATTR_COLS) - 2026-09-11, user-requested. A
# deliberately narrow subset of _ATTR_COLS' full column set (garment
# fabric/embroidery/neck style, plus SUB CATEGORY) rather than every
# descriptive column, to keep the model's added categorical cardinality in
# check; extend if a wider sweep later shows more of them help.
FORECAST_ATTR_COLS: tuple[str, ...] = ("SUB CATEGORY", "TOP_Fabric", "Top Emboidery_Type", "NECK STYLE")

_NULL_VALUES = {"", "UNKNOWN", "NOT APPLIED", "NOT_APPLIED", "-", "N/A", "NA", "NONE"}

# Display bucket for a design with no (or an unrecognised) SUB CATEGORY value
# in the SKU master sheet - replaces the old DESIGN_GROUP-derived "vertical"
# bucket (see verticals.py, removed) everywhere a product grouping is shown
# or used for donor gating (similar_design.py).
SUB_CATEGORY_UNKNOWN = "Unclassified"
# Same bucket, for the broader top-level CATEGORY column (2026-09-21,
# user-requested Category filter) - CATEGORY is the ~6-value top-level
# grouping _build_neighbors() already gates similarity on internally
# (_DESIGN_CATEGORY); this just exposes it the same way sub_category_of()
# exposes SUB CATEGORY.
CATEGORY_UNKNOWN = "Unclassified"

_LOCK = threading.RLock()  # _ensure_loaded() calls _load(), which re-enters
_DESIGN_ATTRS: dict[str, set[str]] = {}
_DESIGN_CATEGORY: dict[str, str] = {}  # style -> UPPERCASED CATEGORY (similarity-gating use only)
_DESIGN_CATEGORY_DISPLAY: dict[str, str] = {}  # style -> human-readable CATEGORY (display casing kept)
_DESIGN_SUBCATEGORY: dict[str, str] = {}  # style -> human-readable SUB CATEGORY (display casing kept)
_DESIGN_COLOR: dict[str, str] = {}  # style -> human-readable Top_Color (display casing kept)
_DESIGN_FORECAST_ATTRS: dict[str, dict[str, str]] = {}  # style -> {FORECAST_ATTR_COLS col: value}
_NEIGHBORS: dict[str, list[tuple[str, float]]] = {}
_LOADED = False


def _clean(v) -> str:
    s = str(v if v is not None else "").strip().upper()
    return "" if s in _NULL_VALUES else s


def _build_neighbors(design_attrs: dict, design_category: dict, top_k: int = _TOP_K) -> dict:
    """Top-K neighbors per design by IDF-weighted-Jaccard over attribute-
    token sets, compared only within the same CATEGORY.

    similar_design.py's BOM-based version uses an inverted-index candidate
    scan, which works there because the ARTICLE grain it compares has a
    median fan-out of ~1 design. That doesn't hold here: a fabric/sub-
    category/embroidery token can be shared by hundreds of designs (e.g.
    "SILK" or "Anarkali KPD Set"), so an inverted-index scan degenerates
    into a near-quadratic blowup on the *popular* tokens specifically.
    Grouping by CATEGORY first (~6 categories, largest ~835 designs) and
    doing a plain pairwise scan within each group is both simpler and
    actually cheaper for this shape of data, and doubles as the same-
    category gating similar_design.py applies via SUB CATEGORY (see
    sub_category_of()).

    A design with no/blank CATEGORY is only compared against other blank-
    CATEGORY designs (not exempted from gating like similar_design.py does)
    - a deliberate simplification since CATEGORY is populated for every row
    in the current SKU master; revisit if that stops being true.
    """
    ids = list(design_attrs.keys())
    n_designs = len(ids)
    if n_designs < 2:
        return {}

    doc_count: dict[str, int] = {}
    for toks in design_attrs.values():
        for t in toks:
            doc_count[t] = doc_count.get(t, 0) + 1
    idf = {t: math.log(n_designs / c) + 1.0 for t, c in doc_count.items()}
    weight_sum = {d: sum(idf[t] for t in toks) for d, toks in design_attrs.items()}

    groups: dict[str, list[str]] = {}
    for d in ids:
        groups.setdefault(design_category.get(d, ""), []).append(d)

    scored: dict[str, list[tuple[str, float]]] = {d: [] for d in ids}
    for members in groups.values():
        for a, b in itertools.combinations(members, 2):
            shared = design_attrs[a] & design_attrs[b]
            if not shared:
                continue
            shared_w = sum(idf[t] for t in shared)
            union_w = weight_sum[a] + weight_sum[b] - shared_w
            if union_w <= 0:
                continue
            sim = shared_w / union_w
            if sim > 0:
                scored[a].append((b, sim))
                scored[b].append((a, sim))

    neighbors: dict[str, list[tuple[str, float]]] = {}
    for d, lst in scored.items():
        lst.sort(key=lambda t: t[1], reverse=True)
        neighbors[d] = lst[:top_k]
    return neighbors


def _clean_display(v) -> str:
    """Trimmed value with the sheet's own display casing/spacing kept (unlike
    _clean(), which uppercases for token/lookup-key use) - "" if blank or a
    placeholder. For values shown to users directly or fed to the forecast
    model as a raw categorical level, not turned into a similarity token."""
    s = str(v if v is not None else "").strip()
    return "" if s.upper() in _NULL_VALUES else s


def _load() -> None:
    global _DESIGN_ATTRS, _DESIGN_CATEGORY, _DESIGN_CATEGORY_DISPLAY, _DESIGN_SUBCATEGORY, _DESIGN_COLOR, _DESIGN_FORECAST_ATTRS, _NEIGHBORS, _LOADED
    if not _CSV_PATH.exists():
        print(f"[design_attributes] {_CSV_PATH} not found - skipping", file=sys.stderr)
        _LOADED = True
        return
    try:
        import pandas as pd
        df = pd.read_csv(_CSV_PATH, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        print(f"[design_attributes] failed to read {_CSV_PATH}: {exc!r}", file=sys.stderr)
        _LOADED = True
        return

    df = df.drop_duplicates(subset=[_STYLE_COL], keep="first")

    attrs: dict[str, set[str]] = {}
    category: dict[str, str] = {}
    category_display: dict[str, str] = {}
    subcategory: dict[str, str] = {}
    color: dict[str, str] = {}
    forecast_attrs: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        style = _clean(row.get(_STYLE_COL))
        if not style:
            continue
        toks: set[str] = set()
        for col, prefix in _ATTR_COLS.items():
            v = _clean(row.get(col))
            if v:
                toks.add(f"{prefix}:{v}")
        cat = _clean(row.get(_CATEGORY_COL))
        if cat:
            category[style] = cat
        # Display-cased CATEGORY (2026-09-21, user-requested Category
        # filter) - same "keep the sheet's own casing" treatment as SUB
        # CATEGORY below; `category` above stays uppercased since
        # _build_neighbors() only uses it for equality gating, not display.
        cat_raw = _clean_display(row.get(_CATEGORY_COL))
        if cat_raw:
            category_display[style] = cat_raw
        # Keep the sheet's own display casing/spacing for SUB CATEGORY (only
        # trimmed) rather than _clean()'s uppercased token form - this is
        # shown to users directly (dashboard column) and returned as-is by
        # sub_category_of(), not just used as an internal similarity token.
        sub_raw = _clean_display(row.get(_SUBCAT_COL))
        if sub_raw:
            subcategory[style] = sub_raw
        color_raw = _clean_display(row.get(_COLOR_COL))
        if color_raw:
            color[style] = color_raw
        fa: dict[str, str] = {}
        for col in FORECAST_ATTR_COLS:
            v = sub_raw if col == _SUBCAT_COL else _clean_display(row.get(col))
            if v:
                fa[col] = v
        if fa:
            forecast_attrs[style] = fa
        if toks:
            attrs[style] = toks

    neighbors = _build_neighbors(attrs, category)

    with _LOCK:
        _DESIGN_ATTRS = attrs
        _DESIGN_CATEGORY = category
        _DESIGN_CATEGORY_DISPLAY = category_display
        _DESIGN_SUBCATEGORY = subcategory
        _DESIGN_COLOR = color
        _DESIGN_FORECAST_ATTRS = forecast_attrs
        _NEIGHBORS = neighbors
        _LOADED = True
    print(f"[design_attributes] loaded {len(attrs)} designs from {_CSV_PATH.name}, "
          f"{len(neighbors)} with >=1 neighbor, {len(subcategory)} with a SUB CATEGORY, "
          f"{len(color)} with a color, {len(forecast_attrs)} with >=1 forecast-model attribute",
          file=sys.stderr)


def _ensure_loaded() -> None:
    if not _LOADED:
        with _LOCK:
            if not _LOADED:
                _load()


# ── Public API ───────────────────────────────────────────────────────────── #
def sub_category_of(design_no) -> str:
    """Map a design/style number (DESIGN_NO / STYLE NO) to its SUB CATEGORY
    from the SKU master sheet (SKU_Master_Data_Final_Cleaned.csv) - the
    product grouping shown on the dashboard and used for same-group donor
    gating in similar_design.py, replacing the old DESIGN_GROUP-derived
    "vertical" bucket (see verticals.py, removed).

    Never calls bool() on ``design_no`` directly: pandas 3.x's Arrow-backed
    nullable string dtype represents missing values as ``pd.NA``, whose
    __bool__ raises "boolean value of NA is ambiguous" rather than being
    falsy like None/NaN/"". str() is safe on every variant (None, float NaN,
    pd.NA, pd.NaT, a real string), so route the emptiness check through it.
    """
    _ensure_loaded()
    if design_no is None:
        return SUB_CATEGORY_UNKNOWN
    key = str(design_no).strip().upper()
    if key in ("", "NAN", "NONE", "<NA>", "NAT"):
        return SUB_CATEGORY_UNKNOWN
    with _LOCK:
        return _DESIGN_SUBCATEGORY.get(key, SUB_CATEGORY_UNKNOWN)


def category_of(design_no) -> str:
    """Map a design/style number to its top-level CATEGORY from the SKU
    master sheet (e.g. "Ethnic Wear") - the broader grouping ABOVE SUB
    CATEGORY (e.g. "Anarkali Set" sits under a CATEGORY). Same lookup
    pattern/edge-case handling as sub_category_of() above."""
    _ensure_loaded()
    if design_no is None:
        return CATEGORY_UNKNOWN
    key = str(design_no).strip().upper()
    if key in ("", "NAN", "NONE", "<NA>", "NAT"):
        return CATEGORY_UNKNOWN
    with _LOCK:
        return _DESIGN_CATEGORY_DISPLAY.get(key, CATEGORY_UNKNOWN)


def color_of(design_no) -> str:
    """Map a design/style number to its primary garment color (Top_Color in
    the SKU master sheet), or "" if unknown/not in the sheet - deliberately
    no "Unknown"-style fallback bucket like sub_category_of(): a "top
    selling unknown color" line isn't useful, so callers ranking by color
    (e.g. the festival/sale outlook's top-colors list) should drop "" rows
    rather than let them accumulate into a bucket. Same None/NaN/pd.NA-safety
    as sub_category_of()."""
    _ensure_loaded()
    if design_no is None:
        return ""
    key = str(design_no).strip().upper()
    if key in ("", "NAN", "NONE", "<NA>", "NAT"):
        return ""
    with _LOCK:
        return _DESIGN_COLOR.get(key, "")


def get_forecast_attrs(design_no) -> dict[str, str]:
    """{column: value} of FORECAST_ATTR_COLS for one design/style, straight
    from the SKU master sheet - fed to the demand-forecasting model itself
    as extra per-design categorical features (lgbm_forecast.py's
    compute()/compute_design(), see that module's _MASTER_ATTR_COLS), not
    just used for cold-start similarity like _DESIGN_ATTRS/get_neighbors().

    {} if this design isn't in the sheet or has no usable value for any of
    FORECAST_ATTR_COLS - the caller (lgbm_forecast.py) treats a missing
    column as "UNKNOWN", the same convention it already uses for its other
    categorical features. Same None/NaN/pd.NA-safety as sub_category_of()."""
    _ensure_loaded()
    if design_no is None:
        return {}
    key = str(design_no).strip().upper()
    if key in ("", "NAN", "NONE", "<NA>", "NAT"):
        return {}
    with _LOCK:
        return dict(_DESIGN_FORECAST_ATTRS.get(key, {}))


def get_neighbors(design_no: str, top_k: int = _TOP_K) -> list[tuple[str, float]]:
    """Raw (design, similarity) neighbor pairs for one design, or [] if the
    design isn't in the SKU master or has no attribute-similar match."""
    _ensure_loaded()
    key = _clean(design_no)
    with _LOCK:
        return list(_NEIGHBORS.get(key, []))[:top_k]


def get_all_neighbors() -> dict:
    """Full design -> neighbor-list graph, for similar_design.py to merge
    in as a gap-filling fallback."""
    _ensure_loaded()
    with _LOCK:
        return {d: list(n) for d, n in _NEIGHBORS.items()}


def get_similar_designs(design_no: str, top_k: int = _TOP_K) -> list[dict]:
    """Top-K attribute-similar designs for one design (debugging / future
    UI), same shape as similar_design.get_similar_designs()."""
    _ensure_loaded()
    key = _clean(design_no)
    with _LOCK:
        neigh = list(_NEIGHBORS.get(key, []))[:top_k]
        own = _DESIGN_ATTRS.get(key, set())
        attrs = dict(_DESIGN_ATTRS)
    return [
        {
            "design": other,
            "similarity": round(sim, 4),
            "sharedAttributes": sorted(own & attrs.get(other, set())),
        }
        for other, sim in neigh
    ]
