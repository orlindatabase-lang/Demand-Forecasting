"""
Similar-design retrieval for demand-forecasting cold start.

ERP view: View_Dboard_Trans_Article_Master_Details_Test_BI
  Raw columns: ARTICLE, ARTICLE_GROUP, UOM, ARTICLE_RATE, PARENT_ARTICLE,
               IS_TRIMS, ACTIVE, USED_IN, PARTY_NAME, QTY_SIZE, PARTY_RATE,
               QUALITY, SHADE, CAPACITY

USED_IN is a comma-separated list of design codes that consume that raw
material/article (a fabric type, an embroidery pattern, lace, buttons...).
That's effectively a per-design Bill-of-Materials fingerprint, and a much
sharper similarity signal than DESIGN_GROUP/CATALOG_NAME alone: the ARTICLE
grain is frequently unique to a single catalog run (median USED_IN fan-out
across ~5,400 articles is 1 design), so two designs sharing the same specific
fabric/embellishment article are far more likely to sell alike than two that
just share a category label.

Feature engineering: explode USED_IN into (design, ARTICLE_GROUP) pairs,
build a per-design set of ARTICLE_GROUP tokens, and weight overlap by inverse
design frequency (IDF) so a shared "COTTON"/"EMBROIDERY" (used by hundreds of
designs) counts far less than a shared "NC VICHITRA"/"BURBURRY" (used by a
handful) — this is what keeps the similarity discriminative instead of just
clustering everything that touches common fabric into one blob.

Launch dates (fetched from the same design-master view lifecycle.py already
reads LAUNCH_DATE from) gate who is allowed to be a *donor*: a design must be
at least _MIN_DONOR_AGE_DAYS past its own launch before another design can
borrow its demand level, otherwise two brand-new designs could borrow off
each other and compound cold-start uncertainty instead of resolving it.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

import design_attributes

_VIEW = "View_Dboard_Trans_Article_Master_Details_Test_BI"

_TOP_K = 6                    # neighbors kept per design
# A neighbor must be at least this old to lend its demand level/curve - kept
# equal to the "newly launched" threshold used everywhere else (the KPI,
# festival-spike prediction) so a design still counted as newly launched
# itself can never be used as a donor for an even-younger design.
_MIN_DONOR_AGE_DAYS = 90
# Exception to the age gate above (2026-09-22, user-requested diagnostic):
# a near-identical material match - the same fabric/print collection
# launched as several SKUs at once - is worth borrowing from even before
# the normal age gate clears. Diagnosed against live data: 22.7% of T0
# ("new launch") designs had ZERO eligible donor despite averaging 5-6
# neighbors each, almost entirely because an entire collection launched
# together is mutually under-90-days and so can never help itself for its
# first 3 months - even though same-collection siblings are each other's
# highest-confidence similarity match (1.0). The alternative (no donor at
# all) is worse than a same-collection sibling's own thin early data - and
# if that sibling has no real sales yet either, borrow_design_level()/
# similar_design_curve() already skip it for having nothing to lend, so
# this exception can't introduce a donor with zero information either way.
_SAME_COLLECTION_SIMILARITY = 0.9


def _donor_eligible(other: str, sim: float, launch: dict, as_of) -> bool:
    """Is ``other`` old enough (or similar enough) to lend its demand level/
    curve as of ``as_of``? See _SAME_COLLECTION_SIMILARITY above for why a
    near-identical match bypasses the normal age gate. An unknown launch
    date is treated as eligible (existing behaviour, unchanged) - most
    designs missing a launch date are legacy stock predating this app's
    2015+ plausibility floor, not recent unproven ones."""
    dt = launch.get(other)
    if dt is None or (as_of - dt).days >= _MIN_DONOR_AGE_DAYS:
        return True
    return sim >= _SAME_COLLECTION_SIMILARITY

_DESIGN_SECTION_SUFFIXES = (
    "-DUPATTA", "-PLAZZO", "-PALAZZO", "-BOTTOM", "-PANT", "-TROUSER",
    "-SKIRT", "-BLOUSE", "-SHARARA", "-SHRUGE", "-SALWAR", "-SLAWAR",
    "-LEHENGA", "-DHOTI", "-ANARKALI", "-KURTI", "-JACKET",
)


def _base_design(design: str) -> str:
    """Strip a known section suffix, e.g. "417-03-PANT" -> "417-03"."""
    d = str(design).strip().upper()
    for sfx in _DESIGN_SECTION_SUFFIXES:
        if d.endswith(sfx):
            return d[: -len(sfx)]
    return d


# ── Cache / background refresh ──────────────────────────────────────────── #
_LOCK = threading.Lock()
_FETCHED_AT: float = 0.0
_REFRESH_SECS = 6 * 3600
_REFRESHING = False

_DESIGN_MATERIALS: dict[str, set[str]] = {}
_NEIGHBORS: dict[str, list[tuple[str, float]]] = {}
_LAUNCH_DATES: dict = {}  # design -> pandas.Timestamp
_DESIGN_SUBCATEGORY: dict[str, str] = {}  # design -> SUB CATEGORY (see design_attributes.sub_category_of)

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "similar_design.json"
_CACHE_MAX_AGE = 24 * 3600


def _save_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetchedAt": _FETCHED_AT,
            "materials": {d: sorted(g) for d, g in _DESIGN_MATERIALS.items()},
            "neighbors": _NEIGHBORS,
            "launchDates": {d: ts.isoformat() for d, ts in _LAUNCH_DATES.items()},
            "designSubCategory": _DESIGN_SUBCATEGORY,
            "attrFallback": True,
        }
        _CACHE_FILE.write_text(json.dumps(payload))
        print(f"[similar_design] cache saved ({len(_DESIGN_MATERIALS)} designs)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[similar_design] cache save failed: {exc}", file=sys.stderr)


def _load_cache() -> bool:
    global _DESIGN_MATERIALS, _NEIGHBORS, _LAUNCH_DATES, _DESIGN_SUBCATEGORY, _FETCHED_AT
    if not _CACHE_FILE.exists():
        return False
    try:
        import pandas as pd
        payload = json.loads(_CACHE_FILE.read_text())
        cached_at = float(payload.get("fetchedAt", 0))
        age = time.time() - cached_at
        if age > _CACHE_MAX_AGE:
            print(f"[similar_design] disk cache too old ({age / 3600:.1f}h), ignoring", file=sys.stderr)
            return False
        # No "designSubCategory" key means this cache predates same-sub-category
        # donor gating (either the pre-vertical version, or the older
        # vertical-based "designVertical" key) - ignore it so a stale/wrongly-
        # gated neighbor graph isn't reused.
        if "designSubCategory" not in payload:
            print("[similar_design] disk cache predates sub-category gating, ignoring", file=sys.stderr)
            return False
        # No "attrFallback" key means this cache predates the design_attributes
        # gap-filling fallback (see refresh()) - ignore it so designs with no
        # BOM/USED_IN history don't sit with zero neighbors until the next
        # scheduled 6h background refresh.
        if not payload.get("attrFallback"):
            print("[similar_design] disk cache predates attribute fallback, ignoring", file=sys.stderr)
            return False
        with _LOCK:
            _DESIGN_MATERIALS = {d: set(g) for d, g in payload.get("materials", {}).items()}
            _NEIGHBORS = {d: [tuple(p) for p in neigh] for d, neigh in payload.get("neighbors", {}).items()}
            _LAUNCH_DATES = {d: pd.Timestamp(ts) for d, ts in payload.get("launchDates", {}).items()}
            _DESIGN_SUBCATEGORY = dict(payload.get("designSubCategory", {}))
            _FETCHED_AT = cached_at
        print(f"[similar_design] loaded {len(_DESIGN_MATERIALS)} designs from disk cache "
              f"({age / 60:.0f}m old)", file=sys.stderr)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[similar_design] cache load failed: {exc}", file=sys.stderr)
        return False


def _fetch_design_meta() -> tuple[dict, dict]:
    """(launch_dates, sub_categories) per base design. Launch dates come from
    the same master view lifecycle.py/data.py already read LAUNCH_DATE from;
    SUB CATEGORY (used for same-sub-category donor gating) comes from
    design_attributes.py's SKU_Master_Data_Final_Cleaned.csv-backed lookup,
    not from any ERP master-view column."""
    import pandas as pd
    import live_source

    master = live_source.fetch_erp_view(live_source.VIEW_MASTER)
    if master.empty or "DESIGN_NO" not in master.columns:
        return {}, {}
    launch = pd.to_datetime(master.get("LAUNCH_DATE"), errors="coerce")
    launch_dates: dict = {}
    design_subcat: dict[str, str] = {}
    _min_plausible = pd.Timestamp("2015-01-01")
    for i, design in enumerate(master["DESIGN_NO"].astype(str)):
        base = _base_design(design)
        dt = launch.iloc[i]
        # Guard against a placeholder LAUNCH_DATE (e.g. the Excel epoch,
        # 1900-01-01) masquerading as a real one - see the matching check in
        # data.py's `launched` computation for the full explanation.
        if not pd.isna(dt) and dt >= _min_plausible and (base not in launch_dates or dt < launch_dates[base]):
            launch_dates[base] = dt
        if base not in design_subcat:
            design_subcat[base] = design_attributes.sub_category_of(base)
    return launch_dates, design_subcat


def _build_neighbors(
    design_materials: dict, design_subcat: dict | None = None, top_k: int = _TOP_K
) -> dict:
    """Top-K neighbors per design by IDF-weighted-Jaccard over material sets.

    Uses an inverted index (ARTICLE_GROUP -> designs containing it) so only
    designs that actually share >=1 material are ever compared, instead of
    the full O(n^2) pairwise scan.

    ``design_subcat`` (design -> SUB CATEGORY, see design_attributes.py), if
    given, restricts candidates to the SAME sub-category as the query design:
    a shared fabric between, say, a Kurti and an unrelated Top shouldn't make
    them demand-similar just because both happen to use that fabric. A
    design on EITHER side with an unknown/missing sub-category is exempt
    from this restriction (missing category data shouldn't block an
    otherwise-good material match).
    """
    n_designs = len(design_materials)
    if n_designs < 2:
        return {}
    dv = design_subcat or {}

    group_doc_count: dict[str, int] = {}
    for groups in design_materials.values():
        for g in groups:
            group_doc_count[g] = group_doc_count.get(g, 0) + 1
    idf = {g: math.log(n_designs / c) + 1.0 for g, c in group_doc_count.items()}

    inv: dict[str, list[str]] = {}
    for d, groups in design_materials.items():
        for g in groups:
            inv.setdefault(g, []).append(d)

    neighbors: dict[str, list[tuple[str, float]]] = {}
    for d, groups in design_materials.items():
        d_sc = dv.get(d, design_attributes.SUB_CATEGORY_UNKNOWN)
        candidates: dict[str, float] = {}
        for g in groups:
            w = idf.get(g, 1.0)
            for other in inv.get(g, ()):
                if other == d:
                    continue
                if d_sc != design_attributes.SUB_CATEGORY_UNKNOWN:
                    other_sc = dv.get(other, design_attributes.SUB_CATEGORY_UNKNOWN)
                    if other_sc != design_attributes.SUB_CATEGORY_UNKNOWN and other_sc != d_sc:
                        continue
                candidates[other] = candidates.get(other, 0.0) + w
        scored: list[tuple[str, float]] = []
        for other, shared_w in candidates.items():
            union = groups | design_materials[other]
            union_w = sum(idf.get(g, 1.0) for g in union)
            scored.append((other, shared_w / union_w if union_w else 0.0))
        scored.sort(key=lambda t: t[1], reverse=True)
        neighbors[d] = scored[:top_k]
    return neighbors


def refresh() -> None:
    """Fetch the article-master view + launch dates/sub-categories, rebuild
    the similarity graph, and cache to disk. Safe to call from a background
    thread."""
    global _DESIGN_MATERIALS, _NEIGHBORS, _LAUNCH_DATES, _DESIGN_SUBCATEGORY, _FETCHED_AT, _REFRESHING
    _REFRESHING = True
    try:
        import live_source

        df = live_source.fetch_erp_view(_VIEW)
        if df.empty or "USED_IN" not in df.columns:
            print("[similar_design] empty/unexpected view response — keeping cache", file=sys.stderr)
            return

        design_materials: dict[str, set[str]] = {}
        for used_in, group in zip(df["USED_IN"], df.get("ARTICLE_GROUP", [])):
            if not used_in:
                continue
            grp = str(group or "").strip().upper()
            if not grp:
                continue
            for token in str(used_in).split(","):
                token = token.strip().upper()
                if not token:
                    continue
                base = _base_design(token)
                design_materials.setdefault(base, set()).add(grp)

        launch_dates, design_subcat = _fetch_design_meta()
        neighbors = _build_neighbors(design_materials, design_subcat)

        # Gap-filling fallback: a design with no USED_IN records yet (never
        # been through production) has no entry in `neighbors` at all, so it
        # would borrow nothing from borrow_design_level()/similar_design_curve()
        # forever. design_attributes.py builds a second similarity graph from
        # the pre-production SKU design sheet (fabric/color/embroidery/neck/
        # sleeve) - use it ONLY where the BOM graph is empty, so a design that
        # already has real neighbors is completely unaffected.
        filled_from_attrs = 0
        try:
            for design, attr_neigh in design_attributes.get_all_neighbors().items():
                if not neighbors.get(design) and attr_neigh:
                    neighbors[design] = attr_neigh
                    filled_from_attrs += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[similar_design] design_attributes fallback failed: {exc!r}", file=sys.stderr)

        with _LOCK:
            _DESIGN_MATERIALS = design_materials
            _NEIGHBORS = neighbors
            _LAUNCH_DATES = launch_dates
            _DESIGN_SUBCATEGORY = design_subcat
            _FETCHED_AT = time.time()
        print(f"[similar_design] built {len(design_materials)} design material fingerprints, "
              f"{len(launch_dates)} launch dates, {len(design_subcat)} sub-categories, "
              f"{filled_from_attrs} filled from design_attributes fallback", file=sys.stderr)
        _save_cache()
    except Exception as exc:  # noqa: BLE001
        print(f"[similar_design] refresh failed: {exc!r}", file=sys.stderr)
    finally:
        _REFRESHING = False


def _maybe_refresh_bg() -> None:
    global _REFRESHING
    if time.time() - _FETCHED_AT < _REFRESH_SECS or _REFRESHING:
        return
    _REFRESHING = True
    threading.Thread(target=refresh, daemon=True, name="similar_design-refresh").start()


# ── Public API ───────────────────────────────────────────────────────────── #
def get_similar_designs(
    design_no: str, top_k: int = _TOP_K, eligible_donors_only: bool = False, as_of=None
) -> list[dict]:
    """Top-K material-similar designs for one design (debugging / future UI).

    ``eligible_donors_only=True`` filters out any neighbor that couldn't
    actually be used as a donor right now - i.e. applies the SAME
    ``_MIN_DONOR_AGE_DAYS`` gate borrow_design_level()/similar_design_curve()
    use. Without this, a raw neighbor list can include a design that's
    itself too young to lend anything (e.g. two designs launched the same
    week can be each other's closest material match), which is fine for
    pure similarity debugging but actively misleading in any UI that
    attributes a forecast to "borrowed from X" - X might never have been
    eligible to donate in the first place.
    """
    _maybe_refresh_bg()
    base = _base_design(design_no)
    with _LOCK:
        neigh = list(_NEIGHBORS.get(base, []))
        own = set(_DESIGN_MATERIALS.get(base, set()))
        materials = dict(_DESIGN_MATERIALS)
        launch = dict(_LAUNCH_DATES)
    if eligible_donors_only:
        import pandas as pd
        as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
        neigh = [(other, sim) for other, sim in neigh if _donor_eligible(other, sim, launch, as_of)]
    neigh = neigh[:top_k]
    return [
        {
            "design": other,
            "similarity": round(sim, 4),
            "sharedMaterials": sorted(own & materials.get(other, set())),
        }
        for other, sim in neigh
    ]


def borrow_design_level(dlevel: dict, as_of=None) -> dict:
    """For every design with a materials fingerprint, borrow the
    similarity-weighted average recent-demand level (``dlevel``) of its
    top-K neighbors — restricted to neighbors that (a) actually have a
    ``dlevel`` value (i.e. sold recently) and (b) are at least
    ``_MIN_DONOR_AGE_DAYS`` past their own launch date, so a brand-new
    design can't borrow from an equally unproven neighbor.

    ``dlevel`` is the same design -> avg-recent-daily-demand dict
    lgbm_forecast.compute() already builds for its own ``design_level``
    feature; this is a purely in-memory lookup (no ERP calls) so it's cheap
    to call once per training run.
    """
    _maybe_refresh_bg()
    import pandas as pd
    with _LOCK:
        neighbors = dict(_NEIGHBORS)
        launch = dict(_LAUNCH_DATES)
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()

    out: dict[str, float] = {}
    for design, neigh in neighbors.items():
        num = 0.0
        den = 0.0
        for other, sim in neigh:
            v = dlevel.get(other)
            if not v or v <= 0:
                continue
            if not _donor_eligible(other, sim, launch, as_of):
                continue
            num += sim * v
            den += sim
        if den > 0:
            out[design] = num / den
    return out


def similar_design_curve(
    design: str,
    weekly_by_design: dict,
    start_offset: int,
    own_early_level: float,
    horizon_weeks: int = 6,
    top_k: int = _TOP_K,
    as_of=None,
) -> list | None:
    """Borrow a demand-curve *shape* for a young design's next ``horizon_weeks``
    weeks, using what material-similar designs actually sold at the SAME
    weeks-since-their-own-launch — not just a flat average level. This is
    what lets a design inherit a festival spike or seasonal ramp it hasn't
    lived through yet, as long as a similar design already has.

    Each neighbor's contribution is scaled by ``own_early_level`` (this
    design's own average weekly qty over its first ~3 observed weeks, or the
    pooled design-level average if the design has no sales of its own yet)
    divided by that neighbor's own early level, so two designs of different
    absolute popularity but the same *pattern* still combine correctly. Only
    neighbors that are already ``_MIN_DONOR_AGE_DAYS`` past their own launch
    (see module docstring) are used as donors, and only for the specific
    future weeks where they actually have data at that offset — weeks with
    no eligible donor are returned as ``None`` so the caller keeps the
    model's own prediction there.

    Args:
        design:            The design to forecast for.
        weekly_by_design:  {design: {weeks_since_launch: qty}}, built by the
                            caller from its own weekly sales panel (this
                            module has no direct access to sales data).
        start_offset:       Weeks-since-launch of the FIRST forecast week.
        own_early_level:    This design's own average weekly qty over its
                            first ~3 observed weeks (0 if it has none yet).
        horizon_weeks:      Number of future weeks to borrow (default 6).
        top_k:              Max neighbors considered.
        as_of:              Reference date for donor-age gating (default: today).

    Returns:
        A list of length ``horizon_weeks`` (values or None per week), or
        None if no eligible neighbor had any usable data.
    """
    import pandas as pd
    _maybe_refresh_bg()
    base = _base_design(design)
    with _LOCK:
        neigh = list(_NEIGHBORS.get(base, []))[:top_k]
        launch = dict(_LAUNCH_DATES)
    if not neigh:
        return None
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()

    curve = [0.0] * horizon_weeks
    weight = [0.0] * horizon_weeks
    for other, sim in neigh:
        if not _donor_eligible(other, sim, launch, as_of):
            continue
        other_curve = weekly_by_design.get(other)
        if not other_curve:
            continue
        other_early_vals = [v for o, v in other_curve.items() if 0 <= o <= 2]
        other_level = sum(other_early_vals) / len(other_early_vals) if other_early_vals else 0.0
        scale = (own_early_level / other_level) if other_level > 0 else 1.0
        for s in range(horizon_weeks):
            v = other_curve.get(start_offset + s)
            if v is None:
                continue
            curve[s] += sim * v * scale
            weight[s] += sim

    if not any(w > 0 for w in weight):
        return None
    return [curve[s] / weight[s] if weight[s] > 0 else None for s in range(horizon_weeks)]


# Warm the cache at import time (disk cache first, then a background refresh
# if it's missing/stale) so the first forecast training run isn't blocked on
# a live ERP fetch.
if not _load_cache():
    _FETCHED_AT = 0.0
_maybe_refresh_bg()
