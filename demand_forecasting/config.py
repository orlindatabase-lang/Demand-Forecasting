"""
Central configuration for the apparel demand-forecasting system.

Everything tunable lives here so the pipeline modules stay free of magic
numbers. Import the module and read the constants, e.g.::

    import config
    df = pd.read_csv(config.DATA_PATH)
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR: Path = Path(__file__).resolve().parent          # .../demand_forecasting
PROJECT_DIR: Path = BASE_DIR.parent                        # repo root

# Input produced by the data-assembly notebook (final_merged_data.csv).
DATA_PATH: Path = PROJECT_DIR / "final_merged_data.csv"

OUTPUT_DIR: Path = BASE_DIR / "outputs"
MODEL_DIR: Path = OUTPUT_DIR / "models"
FORECAST_DIR: Path = OUTPUT_DIR / "forecasts"
REPORT_DIR: Path = OUTPUT_DIR / "reports"

for _d in (OUTPUT_DIR, MODEL_DIR, FORECAST_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Source column names
# --------------------------------------------------------------------------- #
COL_QTY: str = "qty"
COL_STATUS: str = "order_status"
COL_ORDER_DATE: str = "order_date"          # forecasting timeline
COL_DELIVERY_DATE: str = "delivery_date"    # NOT used for forecasting
COL_LAUNCH_DATE: str = "LAUNCH_DATE"
COL_SKU: str = "listing_sku_code"
COL_DESIGN: str = "DESIGN_NO"

DATE_COLS: List[str] = [COL_ORDER_DATE, COL_DELIVERY_DATE, COL_LAUNCH_DATE]
NUMERIC_COLS: List[str] = [
    "qty", "total", "fee_discounts", "settlement_amount",
    "TOTAL_WIP_QTY", "PENDING_QTY_PIECES",
]

# Static product attributes carried into the feature matrix.
PRODUCT_ATTR_COLS: List[str] = ["DESIGN_GROUP", "COLOR", "SECTION", "CATALOG_NAME"]

# --------------------------------------------------------------------------- #
# Target
# --------------------------------------------------------------------------- #
TARGET: str = "net_demand"
DATE_COL: str = "date"          # canonical name after aggregation

# --------------------------------------------------------------------------- #
# Status -> demand sign business rules
#   Delivered                  -> +qty   (positive demand)
#   Return Received            -> -qty   (negative demand)
#   Cancelled                  ->  0      (no demand)
#   Cancelled Return Received  ->  0      (no demand)
# Matching is done on the lower-cased status string; "cancel" is tested first
# so "Cancelled Return Received" resolves to 0 rather than -1.
# --------------------------------------------------------------------------- #
def demand_sign(status_norm: str) -> int:
    """Return +1 / 0 / -1 for a normalised (lower-cased, stripped) status."""
    if "cancel" in status_norm:
        return 0
    if "return" in status_norm:
        return -1
    if "deliver" in status_norm:
        return 1
    return 0  # unknown statuses contribute no realised demand (reported in QA)


# --------------------------------------------------------------------------- #
# Aggregation levels
# --------------------------------------------------------------------------- #
# Each level defines the entity we forecast for and the id column name used in
# the daily panel. The id column is renamed to "sku" only in the final forecast
# output to satisfy the required output schema.
LEVELS: Dict[str, Dict[str, str]] = {
    "sku":    {"id_col": COL_SKU,    "label": "SKU (listing_sku_code)"},
    "design": {"id_col": COL_DESIGN, "label": "DESIGN_NO"},
}

# --------------------------------------------------------------------------- #
# Forecasting frequency
# --------------------------------------------------------------------------- #
# The panel is aggregated to WEEKLY buckets (week starting Monday). Lags/rolling
# windows and horizons below are therefore counted in WEEKS, not days. Weekly
# aggregation cuts the heavy daily intermittency and the recursive horizon from
# 35 steps to 5, improving both accuracy on the long tail and speed.
FREQ_DAYS: int = 7

# --------------------------------------------------------------------------- #
# Feature engineering (units = WEEKS)
# --------------------------------------------------------------------------- #
LAGS: List[int] = [1, 2, 3, 4, 8, 12]
ROLLING_MEAN_WINDOWS: List[int] = [4, 8, 12]
ROLLING_STD_WINDOWS: List[int] = [4, 12]

# Maximum history (in weeks) a recursive forecast needs to recompute features.
MAX_FEATURE_LOOKBACK: int = max(LAGS) + max(ROLLING_MEAN_WINDOWS + ROLLING_STD_WINDOWS)

# --------------------------------------------------------------------------- #
# Splitting / validation
# --------------------------------------------------------------------------- #
TEST_DAYS: int = 30          # final hold-out horizon
VAL_DAYS: int = 30           # early-stopping / model-selection horizon
WALK_FORWARD_FOLDS: int = 3  # number of expanding-window folds
WALK_FORWARD_HORIZON: int = 30  # days validated in each fold

# Walk-forward CV retrains the model once per fold, which roughly triples the
# training cost on large panels. The single time-based test split already gives
# an honest out-of-sample read, so walk-forward is OFF by default; enable it for
# a deeper stability check via config or the --walk-forward CLI flag.
RUN_WALK_FORWARD: bool = False

# --------------------------------------------------------------------------- #
# Forecast horizons (WEEKS)
# --------------------------------------------------------------------------- #
FORECAST_HORIZONS: List[int] = [1, 2, 5]

# --------------------------------------------------------------------------- #
# LightGBM hyper-parameters
#   L2 `regression` is used so the model predicts EXPECTED (mean) demand. It
#   handles the negative net_demand values that returns create, unlike count
#   objectives (`tweedie`/`poisson`, which require a non-negative target).
#   `regression_l1` (MAE) was rejected: it predicts the conditional median,
#   which collapses to 0 on intermittent per-SKU demand and yields all-zero
#   forecasts — accurate by MAE but useless for planning.
# --------------------------------------------------------------------------- #
LGBM_PARAMS: Dict[str, object] = {
    "objective": "regression",
    "metric": "mae",
    "n_estimators": 2000,
    "learning_rate": 0.03,
    "num_leaves": 63,
    "max_depth": -1,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
    # force_row_wise avoids LightGBM's costly row-wise/col-wise auto-probe on
    # tall panels (millions of rows) and removes the associated startup warning.
    "force_row_wise": True,
}
EARLY_STOPPING_ROUNDS: int = 100

RANDOM_SEED: int = 42
