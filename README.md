# Demand Forecasting & Production Planning: Project Report

This report explains how the Demand Forecasting project works from start to finish:
which data goes in, how it is cleaned and combined, which techniques and algorithms
turn it into forecasts, and how those forecasts become production decisions on the
dashboard.

It describes the **method only**. It does not quote any forecast, accuracy result or
fitted value, because those change with every data refresh and are shown live on the
dashboard. Numbers that do appear are **design parameters** of the method, such as the
horizon length or the number of cross-validation folds.

---

## Contents

1. [Project objective](#1-project-objective)
2. [Technology stack](#2-technology-stack)
3. [System architecture](#3-system-architecture)
4. [Module dependency map](#4-module-dependency-map)
5. [Master flowchart: input to output](#5-master-flowchart-input-to-output)
6. [Stage 1: Data acquisition](#6-stage-1-data-acquisition)
7. [Stage 2: Data cleaning and integration](#7-stage-2-data-cleaning-and-integration)
8. [Stage 3: Demand definition and weekly aggregation](#8-stage-3-demand-definition-and-weekly-aggregation)
9. [Stage 4: Festival and sale effect model](#9-stage-4-festival-and-sale-effect-model)
10. [Stage 5: Similar-design graph](#10-stage-5-similar-design-graph)
11. [Stage 6: Feature engineering](#11-stage-6-feature-engineering)
12. [Stage 7: Model training and selection](#12-stage-7-model-training-and-selection)
13. [Stage 8: Recursive multi-step forecasting](#13-stage-8-recursive-multi-step-forecasting)
14. [Stage 9: Cold-start blending for new designs](#14-stage-9-cold-start-blending-for-new-designs)
15. [Stage 10: Post-processing and hierarchy reconciliation](#15-stage-10-post-processing-and-hierarchy-reconciliation)
16. [Stage 11: Product lifecycle classification](#16-stage-11-product-lifecycle-classification)
17. [Stage 12: Inventory and production planning](#17-stage-12-inventory-and-production-planning)
18. [Stage 13: Evaluation and backtesting](#18-stage-13-evaluation-and-backtesting)
19. [Stage 14: Outputs and delivery](#19-stage-14-outputs-and-delivery)
20. [Design Suggestions](#20-design-suggestions)
21. [Runtime orchestration and refresh cycle](#21-runtime-orchestration-and-refresh-cycle)
22. [Data model](#22-data-model)
23. [Summary of techniques and algorithms](#23-summary-of-techniques-and-algorithms)
24. [Appendix: code map, API, configuration, running, deployment](#24-appendix)

---

## 1. Project objective

The system predicts **weekly gross demand** for every product for the next
**13 weeks**. It then turns that prediction into **how many units to produce**, given
the finished-goods stock and work-in-progress (WIP) that already exist.

Forecasts are made at two levels, SKU and Style. Sub Category figures are the sum of
their Styles:

```mermaid
flowchart TD
    SC["Sub Category<br/>e.g. Kurta Sets"] --> ST1["Style / Design<br/>e.g. 417-03"]
    SC --> ST2["Style / Design<br/>e.g. 474-01"]
    ST1 --> SKU1["SKU<br/>417-03-S"]
    ST1 --> SKU2["SKU<br/>417-03-M"]
    ST1 --> SKU3["SKU<br/>417-03-XL"]

    classDef lvl1 fill:#e8eaf6,stroke:#312c5c;
    classDef lvl2 fill:#fff3e0,stroke:#e65100;
    classDef lvl3 fill:#e8f5e9,stroke:#1b5e20;
    class SC lvl1;
    class ST1,ST2 lvl2;
    class SKU1,SKU2,SKU3 lvl3;
```

| Level | Unit | Why it is needed |
|---|---|---|
| **SKU** | design + size | Size-level stock and production decisions |
| **Style** | design number (`DESIGN_NO`) | More stable totals for planning at the style level |
| **Sub Category** | sum of its Styles | Category-level view in the report |

Sales channels (Amazon, Flipkart, Myntra, Meesho, Nykaa, Ajio and the brand websites)
are shown as **actual sales only**. There is no per-channel forecast.

Two further parts build on the same sales data:

- **Sales analysis tabs** (Sub Category, Category, Tiers Spike Rate): monthly styles,
  orders, units and month-on-month spike, with a platform-wise drill-down
  ([section 19.4](#194-sales-analysis-tabs)).
- **Design Suggestions**: which product types, colours, fabrics and details are trending
  or fading, and a design plan per product for the running and the next season
  ([section 20](#20-design-suggestions)).

---

## 2. Technology stack

| Layer | Technology |
|---|---|
| Data warehouse | Google BigQuery (sales orders) |
| Operational data | DigiBizz ERP report API (design master, WIP, stock, bill of materials) |
| Catalog metadata | Google Cloud SQL, PostgreSQL (catalog tiers, style images) |
| Backend | Python 3.13, FastAPI, Uvicorn, Pydantic |
| Data processing | pandas, NumPy |
| Machine learning | LightGBM (demand forecasts), CatBoost (Design Suggestions), scikit-learn (metrics) |
| Persistence | JSON forecast snapshots, SQLite weekly log and SQLite frozen-forecast store in `api/.cache/` (copied to Cloud Storage on Cloud Run) |
| Frontend | React 19, TypeScript, Vite, Material UI, TanStack Query / Table / Virtual |
| Deployment | Docker, Google Cloud Run (API and dashboard), nginx (serves the dashboard's static files) |

---

## 3. System architecture

```mermaid
flowchart LR
    subgraph EXT["External data sources"]
        direction TB
        BQ[("BigQuery<br/>sales_orders")]
        ERP1[("ERP<br/>Design master")]
        ERP2[("ERP<br/>Program planning / WIP")]
        ERP3[("ERP<br/>Finished-goods inventory")]
        ERP4[("ERP<br/>Article master / BOM")]
        CSQL[("Cloud SQL<br/>CatalogStyle + tier history")]
    end

    subgraph FILES["Reference files"]
        direction TB
        FCAL[/"Festival & sale calendar CSV"/]
        FATT[/"Garment attribute master CSV"/]
        FSNAP[/"Offline sales snapshot CSV<br/>(fallback)"/]
    end

    subgraph BACKEND["Backend: FastAPI service (api/)"]
        direction TB
        ING["Ingestion & integration<br/>live_source.py"]
        ORC["Orchestrator & planning<br/>data.py"]
        FEST["Festival effect model<br/>festival.py + festival_calendar.py"]
        SIM["Similarity engine<br/>similar_design.py + design_attributes.py"]
        ML["Forecasting engine<br/>lgbm_forecast.py"]
        LIFE["Lifecycle rules<br/>lifecycle.py"]
        CAT["Catalog tiers<br/>catalog_style.py"]
        LOG["Weekly production log<br/>weekly_log.py"]
        FRZ["Frozen completed weeks<br/>forecast_freeze.py"]
        DSG["Design suggestions<br/>design_suggest.py (CatBoost)"]
        APIL["REST layer<br/>main.py"]
    end

    subgraph STORE["Local persistence (api/.cache)"]
        direction TB
        JS[("Forecast snapshots<br/>SKU / Design JSON")]
        SQ[("weekly_production_log<br/>SQLite")]
        FZQ[("frozen_forecast<br/>SQLite")]
        SC[("Similarity graph cache")]
    end

    subgraph FRONT["Frontend: React dashboard"]
        direction TB
        P1["Weekly Sales Report<br/>+ channel drill-down"]
        P2["Sub Category · Category ·<br/>Tiers Spike Rate"]
        P3["Production Log"]
        P4["Design Suggestions"]
    end

    BQ --> ING
    ERP1 --> ING
    ERP2 --> ING
    ERP3 --> ING
    FSNAP -.fallback.-> ORC
    ING --> ORC
    FCAL --> FEST
    ERP4 --> SIM
    FATT --> SIM
    CSQL --> CAT
    ORC <--> FEST
    ORC <--> ML
    ML --> FEST
    ML --> SIM
    ML --> CAT
    ORC --> LIFE
    ORC --> LOG
    ML <--> JS
    LOG <--> SQ
    SIM <--> SC
    ORC <--> FRZ
    FRZ <--> FZQ
    ORC --> DSG
    FRZ --> DSG
    FATT --> DSG
    DSG --> APIL
    ORC --> APIL
    APIL -- "JSON over HTTP" --> P1
    APIL --> P2
    APIL --> P3
    APIL --> P4
```

---

## 4. Module dependency map

This diagram shows which backend modules call which. Arrows point from the caller to
the module it uses.

```mermaid
flowchart TD
    main["main.py<br/>REST routes"] --> data["data.py<br/>orchestrator"]
    main --> catalog["catalog_style.py"]
    main --> ds["design_suggest.py<br/>Design Suggestions"]
    run["run.py<br/>server launcher"] --> main

    data --> live["live_source.py"]
    data --> lgbm["lgbm_forecast.py"]
    data --> festival["festival.py"]
    data --> fcal["festival_calendar.py"]
    data --> life["lifecycle.py"]
    data --> attrs["design_attributes.py"]
    data --> wlog["weekly_log.py"]
    data --> sim["similar_design.py"]
    data --> freeze["forecast_freeze.py"]

    ds --> data
    ds --> attrs
    ds --> festival
    ds --> freeze

    lgbm --> festival
    lgbm --> sim
    lgbm --> catalog
    lgbm --> attrs
    lgbm --> live

    festival --> fcal
    sim --> attrs
    sim --> live
```

---

## 5. Master flowchart: input to output

```mermaid
flowchart TD
    subgraph S0["INPUT"]
        I1[("Sales orders")]
        I2[("Design master")]
        I3[("WIP")]
        I4[("Stock")]
        I5[("Bill of materials")]
        I6[("Catalog tiers")]
        I7[/"Festival calendar"/]
        I8[/"Garment attributes"/]
    end

    subgraph S1["PREPARATION"]
        A1["Stage 1<br/>Acquire & validate"]
        A2["Stage 2<br/>Clean · filter · derive keys · join"]
        A3["Stage 3<br/>Gross / Net labels · weekly panel"]
    end

    subgraph S2["SUPPORTING MODELS"]
        B1["Stage 4<br/>Festival effects<br/>ridge regression"]
        B2["Stage 5<br/>Similar designs<br/>IDF-Jaccard kNN"]
    end

    subgraph S3["FORECASTING ENGINE ×2<br/>SKU · Design"]
        C1["Stage 6<br/>Feature engineering"]
        C2["Stage 7<br/>CV + grid search<br/>LightGBM"]
        C3["Stage 8<br/>Recursive 13-week forecast"]
        C4["Stage 9<br/>Cold-start blending"]
    end

    subgraph S4["DECISION LAYER"]
        D1["Stage 10<br/>Naive blend · festival uplift ·<br/>group boost · reconciliation"]
        D2["Stage 11<br/>Lifecycle stage & scores"]
        D3["Stage 12<br/>Available qty · production ·<br/>safety stock"]
        D4["Stage 13<br/>Walk-forward backtest"]
        D5["Design Suggestions<br/>trend stages · CatBoost ·<br/>product cards"]
    end

    subgraph S5["OUTPUT"]
        E1["REST API"]
        E2["Dashboard pages"]
        E3[("Weekly production log")]
    end

    I1 --> A1
    I2 --> A1
    I3 --> A1
    I4 --> A1
    A1 --> A2 --> A3
    A3 --> B1
    I7 --> B1
    I5 --> B2
    I8 --> B2
    I2 --> B2
    A3 --> C1
    B1 --> C1
    B2 --> C1
    I6 --> C1
    C1 --> C2 --> C3 --> C4
    B2 --> C4
    C4 --> D1
    B1 --> D1
    A3 --> D1
    D1 --> D2 --> D3
    D1 --> D4
    D3 --> E1
    D4 --> E1
    A3 --> D5
    I8 --> D5
    I6 --> D5
    D1 --> D5
    D5 --> E1
    E1 --> E2
    D3 --> E3
```

---

## 6. Stage 1: Data acquisition

**Module:** `api/live_source.py` (sales and ERP), `api/similar_design.py` (BOM),
`api/catalog_style.py` (tiers), `api/festival_calendar.py` and
`api/design_attributes.py` (CSV files).

| # | Source | Main fields | Purpose |
|---|---|---|---|
| 1 | BigQuery sales-order table | SKU, listing SKU, quantity, order status, order date, channel, source, buyer state and city, warehouse, value, promotion discount, order and sub-order IDs | Demand history |
| 2 | ERP design master | Design number, group, catalog, colour, section, launch date | Product metadata, product age |
| 3 | ERP program planning | In-house WIP, job-work WIP, allocation, per design + size | Units in production |
| 4 | ERP inventory | Pending pieces (finished goods) per design + size | Units in stock |
| 5 | ERP article master | Raw-material article, article group, designs that use it | Bill-of-materials similarity |
| 6 | Cloud SQL `CatalogStyle` | Current tier, tier change history, images | Catalog tier feature, gallery |
| 7 | Festival calendar CSV | Event name, start date, end date | Festival and sale windows |
| 8 | Garment attribute CSV | Category, sub-category, fabric, colour, embroidery, print, neck, sleeve | Category grouping, fallback similarity |

```mermaid
flowchart TD
    A["Start fetch"] --> B["Query BigQuery<br/>orders since configured start date"]
    B --> C["Server-side COUNT(*)"]
    C --> D{"Row counts match?"}
    D -- yes --> F["Sales frame ready"]
    D -- no --> E["Retry through the REST row iterator"]
    E --> F
    A --> G["Call each ERP view"]
    G --> H{"Response?"}
    H -- "data" --> I["View frame ready"]
    H -- "empty / network error" --> J["Retry with back-off<br/>up to N attempts"]
    J --> H
    H -- "HTTP 500" --> K["Skip view for 15 min<br/>treat as empty"]
    F --> L["Stage 2"]
    I --> L
    K --> L
    L -. "any exception" .-> M["Fall back to offline CSV snapshot"]
    M -. "also fails" .-> N["Fall back to generated mock data<br/>so the API still starts"]
```

---

## 7. Stage 2: Data cleaning and integration

**Technique:** rule-based filtering, regular-expression key extraction, relational
joins, and de-duplication.

```mermaid
flowchart TD
    A["Raw sales rows"] --> B{"Real single-garment sale?"}
    B -- "combo SKU: two designs in one line" --> X["Drop"]
    B -- "placeholder SKU: INACTIVE…" --> X
    B -- "free promotional item" --> X
    B -- "marketplace listing-prefix SKU" --> X
    B -- "channel excluded by business decision" --> X
    B -- yes --> C["Design key from product_sku_code<br/>regex: first two segments"]
    C --> C2{"Second segment<br/>is a size?"}
    C2 -- yes --> C3["Use first segment only"]
    C2 -- no --> C4["Use both segments"]
    C3 --> D["Left-join design master"]
    C4 --> D
    D --> E{"Master match?"}
    E -- no --> E2["Keep derived key as DESIGN_NO"]
    E -- yes --> E3["Attach group, colour, section, launch date"]
    E2 --> F["Derive design + size keys<br/>letter-series designs keep colour segment"]
    E3 --> F

    W["ERP planning view"] --> W1["WIP = in-house + job-work + allocation"]
    W1 --> W2["Sum per design + size"]
    V["ERP inventory view"] --> V1["Sum pending pieces per design + size"]

    F --> J["Left-join WIP and stock"]
    W2 --> J
    V1 --> J
    J --> K{"Several SKU codes share<br/>one design + size?"}
    K -- yes --> K1["Keep qty on the SKU with most orders,<br/>zero on the others"]
    K -- no --> L
    K1 --> L["Drop exact duplicates<br/>incl. order + sub-order IDs"]
    L --> M["Drop future-dated orders;<br/>launch dates before 2015 = unknown"]
    M --> N["Clean integrated frame"]
```

**Key rules**

1. **Junk filtering.** Remove order lines that are not a single real garment: bundles of
   two designs, unresolved placeholder codes, promotional freebies, marketplace listing
   prefixes, and the excluded channel.
2. **Canonical SKU.** Parse the internal `product_sku_code`, not the per-marketplace
   listing code, because the internal code follows a consistent `DESIGN-SIZE` pattern.
3. **Design key examples:**
   - `417-03-XL` → design `417-03`, size `XL`
   - `6302-L` → design `6302`, size `L`
   - `K-108-01-32` → design `K-108-01`, size `32`
4. **No lost sales.** A design with no master record keeps its derived key, so its sales
   still appear.
5. **No double counting.** When several SKU codes map to the same design + size, the
   stock and WIP for that design + size are assigned to one SKU only.
6. **True de-duplication.** Rows are de-duplicated on the full row, including the
   marketplace order IDs, so two genuinely different orders are never merged.

---

## 8. Stage 3: Demand definition and weekly aggregation

**Technique:** status-based labelling, calendar bucketing, and dense panel construction.

```mermaid
flowchart LR
    A["Order line"] --> B{"order_status"}
    B -- "cancelled / cancel approved" --> X["Not demand"]
    B -- "forward-delivery states" --> N["NET sale<br/>+ GROSS sale"]
    B -- "returned / RTO / reverse / lost" --> G["GROSS sale only<br/>+ RETURN"]
    N --> W["Map order date → Monday week start"]
    G --> W
    W --> P["Sum units per ID per week"]
    P --> D["Dense panel:<br/>every week from first sale to latest,<br/>missing weeks = 0"]
    D --> M["Remove weeks ending within<br/>7 days of the latest order<br/>(maturation cut-off)"]
    M --> OUT["Training panel"]
```

| Label | Meaning | Used for |
|---|---|---|
| **Gross sale** | Every order that was actually placed, including ones later returned. Only cancellations are excluded | **Training target**, naive baseline, festival fitting, evaluation, reported "Actual Sale" |
| **Net sale** | Orders still moving forward to delivery | Lifecycle velocity, safety-stock variability, geography rankings |
| **Return** | Return-type statuses | Lifecycle return-rate score |

- **Maturation.** Order statuses keep updating for several days after a sale, so the
  most recent weeks are incomplete. They are left out of training until their actuals
  have settled.
- **Dense panel.** Weeks with no sales appear as explicit zeros, so the model sees how
  intermittent the demand is.

---

## 9. Stage 4: Festival and sale effect model

**Module:** `api/festival.py`.

**Technique:** log-linear **ridge regression** that estimates the effect of every event
type together, measured against a same-weekday baseline.

```mermaid
flowchart TD
    A["Daily gross units<br/>whole catalogue"] --> B["Event windows from calendar CSV"]
    B --> B2["Add derived windows:<br/>pre-Rakhi gifting ramp,<br/>Diwali delivery-cut-off dip"]
    B2 --> C["Mark each day: which events are active"]
    C --> D["Quiet days = no event"]
    D --> E["Baseline(day) = median of the<br/>nearest earlier same-weekday quiet days<br/>(4 to 6 of them)"]
    E --> F["y(day) = log(units / baseline)"]
    C --> G["X(day) = 0/1 indicator per event type"]
    F --> H["Ridge regression<br/>β = (XᵀX + λI)⁻¹ Xᵀy, λ = 5"]
    G --> H
    H --> I["effect(event) = exp(β)"]
    I --> J{"|effect − 1| ≤ 7%?"}
    J -- yes --> K["Treat as no effect"]
    J -- no --> L["Keep effect"]
    L --> M["Daily multiplier = product of active effects,<br/>clamped to [0.5, 3.0]"]
    M --> N["Weekly signal = mean of 7 days<br/>+ dominant event + days to nearest peak"]
    N --> O1["Used as a MODEL FEATURE"]
    N --> O2["Used as a POST-HOC MULTIPLIER"]
```

**Why this approach**

- **Same-weekday baseline.** Comparing each day with the same weekday removes the normal
  weekly shopping pattern from the measurement.
- **Log-linear model.** Taking the log turns overlapping multiplicative effects into a
  sum. Fitting all events together separates the effect of each one when several run at
  the same time.
- **Ridge penalty.** Pulls rarely-seen events towards "no effect", so a few noisy days
  cannot create a large multiplier.
- **Dips are allowed.** Multipliers below 1 capture festivals when shoppers buy less
  online.
- **Re-fitted every refresh.** New festival seasons update the effects automatically. If
  there is too little history, the previous effects are kept.

---

## 10. Stage 5: Similar-design graph

**Modules:** `api/similar_design.py` (primary), `api/design_attributes.py` (fallback).

**Technique:** **k-nearest-neighbour** retrieval with **IDF-weighted Jaccard
similarity**, using an inverted index so only designs that share a material are
compared.

```mermaid
flowchart TD
    A["ERP article master<br/>article → USED_IN design list"] --> B["Explode to design → set of material groups"]
    B --> C["IDF(material) = log(N / designs using it) + 1<br/>rare materials weigh more"]
    C --> D["Inverted index<br/>material → designs"]
    D --> E["For design d: candidates =<br/>designs sharing ≥ 1 material,<br/>same sub-category"]
    E --> F["sim(d, e) = Σ IDF(shared) / Σ IDF(union)"]
    F --> G["Top-6 neighbours per design"]

    H["Garment attribute CSV"] --> H1["Tokens COLUMN:VALUE<br/>(fabric, colour, neck, sleeve, print, …)"]
    H1 --> H2["Same IDF-Jaccard,<br/>same top-level category"]
    H2 --> H3{"Design has no<br/>BOM neighbours?"}
    H3 -- yes --> G
    G --> I{"Neighbour eligible as donor?"}
    I -- "≥ 90 days since its launch" --> J["Eligible"]
    I -- "near-identical sibling<br/>(similarity ≥ 0.9)" --> J
    I -- otherwise --> K["Ignored"]
    J --> L1["Borrow LEVEL<br/>→ feature similar_design_level"]
    J --> L2["Borrow CURVE by weeks since launch<br/>→ cold-start blending"]
```

**Idea.** A design's bill of materials (fabric, embroidery, lace, trims) works as a
fingerprint. Two designs that share rare, specific materials usually belong to the same
collection or aesthetic and tend to sell in a similar way. Designs that have not entered
production have no bill of materials yet, so they fall back to similarity based on
their garment attributes.

---

## 11. Stage 6: Feature engineering

**Module:** `api/lgbm_forecast.py`.

**Technique:** supervised-learning features built from lags, rolling statistics,
calendar fields, categorical attributes and hierarchy signals. Every feature is
**point-in-time**: a training row for a given week sees only information that was known
in that week.

```mermaid
flowchart LR
    P["Weekly panel"] --> L["Lags<br/>1, 2, 4, 8, 12, 26, 52 weeks"]
    P --> R["Rolling stats (shifted by 1 week)<br/>mean 4/8/12 · std 4/8 · max/min 4"]
    P --> T["Trend<br/>diff · growth % · ratio to mean ·<br/>coefficient of variation ·<br/>expanding mean · zero-sales ratio"]
    P --> CAL["Calendar<br/>year · month · quarter · week of year ·<br/>day of year · month/quarter start & end"]
    M["Design master +<br/>attribute CSV"] --> AT["Product attributes (categorical)<br/>category · brand · channel · sub-category ·<br/>fabric · embroidery · neck · design · size"]
    CT["Tier history"] --> TR["Catalog tier in effect that week"]
    LD["Launch date"] --> LC["Product age · launch-period sales rate"]
    P --> H["Hierarchy<br/>point-in-time design-level median"]
    SD["Similar-design graph"] --> H2["Borrowed similar-design level"]
    FE["Festival model"] --> FF["Festival multiplier · flag · event ·<br/>days to peak · category × event"]

    L --> X["Feature matrix"]
    R --> X
    T --> X
    CAL --> X
    AT --> X
    TR --> X
    LC --> X
    H --> X
    H2 --> X
    FF --> X
```

| Group | Purpose |
|---|---|
| Lags | Short-term momentum (1–12 weeks), half-year and yearly seasonality (26, 52 weeks) |
| Rolling statistics | Recent level, volatility and range |
| Trend | Direction of change and how intermittent demand is |
| Calendar | Seasonality inside the year |
| Product attributes | Lets the model share what it learns across similar products |
| Catalog tier | The merchandising team's view of the style, as it stood in that week |
| Lifecycle | Where the product is between launch and maturity |
| Hierarchy | A noisy SKU can lean on its design's more stable signal and on similar designs |
| Festival | Event uplift or dip |

**No promotion inputs (since model v50 / d37, 19 Oct 2026).** The week's promotion
discount is only known after the week (it is the sum of that week's order discounts),
and it was always 0 when forecasting, so the models forecast every future week as if
there were no promotion. Measured on the training data with promotion set to 0, as in
production, removing it cut the SKU model's error from 68.6% to 53.8% WAPE (bias −59% →
−20%) and the style model's from 47.2% to 43.1% (bias −32% → −14%).

**Outlier treatment.** Outside festival weeks, each ID's weekly demand is capped at its
own **99th percentile**. The percentile is computed from the training period only, so it
uses no future data. One-off spikes are dampened while real festival spikes are kept.

---

## 12. Stage 7: Model training and selection

**Module:** `api/lgbm_forecast.py`.

**Technique:** gradient-boosted decision trees with a **Tweedie** loss, tuned with
**rolling-origin time-series cross-validation**. The algorithm is **LightGBM**; a grid
search picks its hyperparameters.

**Why Tweedie.** Weekly apparel demand has many zero weeks and occasional large ones. A
Tweedie distribution models this mix of "no sale" and a positive amount directly, and
never predicts negative demand.

### 12.1 Time-series split

```mermaid
gantt
    title Rolling-origin cross-validation (illustrative)
    dateFormat X
    axisFormat %s
    section Fold 1
    Train      :a1, 0, 40
    Validate   :crit, a2, 40, 55
    section Fold 2
    Train      :b1, 0, 55
    Validate   :crit, b2, 55, 70
    section Fold 3
    Train      :c1, 0, 70
    Validate   :crit, c2, 70, 85
    section Hold-out
    Test (last 15% of weeks) :done, d1, 85, 100
```

### 12.2 Training and selection flow

```mermaid
flowchart TD
    A["Feature matrix + target"] --> B["Hold out last 15% of weeks as TEST"]
    B --> C["3 rolling-origin folds<br/>train = all earlier weeks,<br/>validate = next 15% span"]
    C --> D["Recency weights<br/>w = 0.5^(age in weeks / 26)"]
    D --> E["For each LightGBM config in the grid:<br/>train each fold with early stopping,<br/>record mean WAPE"]
    E --> F["Best config = lowest mean WAPE"]
    F --> G["Fit it on the last fold"]
    G --> K["Log diagnostics: WAPE, R², MAE, RMSE<br/>on validation and test"]
    K --> L["Refit on ALL history;<br/>tree count scaled up by full rows / train rows"]
    L --> M["Final predictor"]
```

**Search space.** The grid varies tree depth, number of leaves, minimum samples per
leaf, L1/L2 regularisation, row and column subsampling, and the Tweedie variance power.
Every candidate uses a learning rate of 0.04, up to 3,000 trees, and stops early after
50 rounds without improvement.

**Metric.** WAPE (weighted absolute percentage error) = `Σ|actual − forecast| / Σ actual`.
It is weighted by volume, so fast-selling items count for more than rare ones.

### 12.3 The two models

**Style model only (default since 19 Oct 2026, `FORECAST_LEVEL=style`).** Every
Style, Sub Category and TOTAL number already came from the style model; the SKU model
only filled 15 styles without a style forecast (0.07% of the forecast). Compared on the
same test weeks (13 Jul – 5 Oct 2026), the style model beat the sum of the SKU model at
every level: style rows 43.1% vs 46.5% WAPE (bias −13.7% vs −19.7%), Sub Category 31.0% vs
32.6%, weekly total 28.3% vs 29.3%. Even per SKU, the style forecast split by each SKU's
recent share of its style's sales (52.1%) beat the SKU model (53.8%). Styles the style
model does not cover use the run-rate × festival. `FORECAST_LEVEL=both` brings the SKU
model back.

The same engine trains two independent models:

```mermaid
flowchart LR
    E["Shared training engine"] --> M1["SKU model<br/>ID = SKU code<br/>scope: SKUs sold in the last 13 weeks"]
    E --> M2["Design model<br/>ID = design number<br/>scope: all designs"]
```

---

## 13. Stage 8: Recursive multi-step forecasting

**Technique:** **recursive (iterated) forecasting.** The model predicts one week ahead,
and that prediction becomes an input for the week after.

```mermaid
flowchart LR
    H0["History window<br/>last 52 weeks per ID"] --> F1["Features for week t+1"]
    F1 --> P1["Predict t+1<br/>clip at 0"]
    P1 --> H1["Append to window"]
    H1 --> F2["Features for week t+2"]
    F2 --> P2["Predict t+2"]
    P2 --> H2["Append to window"]
    H2 --> DOTS["… repeat …"]
    DOTS --> P13["Predict t+13"]
    P13 --> OUT["13-week forecast per ID"]
```

At each of the **13 steps**:

- Lag, rolling and trend features are recomputed from the window, which now includes
  the predictions made so far.
- Calendar and festival features are those of the target week.
- Promotions are set to zero, since future promotions are not known.
- Static features (attributes, tier, hierarchy level, launch date) stay the same.

---

## 14. Stage 9: Cold-start blending for new designs

**Technique:** **confidence-weighted shrinkage** towards an **analogue demand curve**
borrowed from similar designs.

A design with little sales history cannot be forecast reliably from its own data, so
its model forecast is blended with the curve that similar designs followed at the same
age.

```mermaid
flowchart TD
    A["Design with model forecast"] --> B["confidence = min(1, active weeks / 13)"]
    B --> C{"Has it lived through<br/>a festival week?"}
    C -- no --> C1["confidence = min(confidence, 0.5)"]
    C -- yes --> D
    C1 --> D{"Catalog tier cap?"}
    D -- T0 --> D0["cap 0.3"]
    D -- T1 --> D1["cap 0.6"]
    D -- T2 --> D2["cap 0.8"]
    D -- other --> D3["no cap"]
    D0 --> E
    D1 --> E
    D2 --> E
    D3 --> E{"confidence = 1?"}
    E -- yes --> F["Keep model forecast"]
    E -- no --> G["Borrow neighbours' sales at the same<br/>weeks-since-launch, scaled to the design's<br/>own early sales level"]
    G --> H["forecast = confidence · model<br/>+ (1 − confidence) · borrowed curve"]
```

The borrowed curve is aligned by **weeks since launch**, not by calendar date. This lets
a new design pick up a launch ramp, seasonal pattern or festival spike that similar
designs have already been through.

---

## 15. Stage 10: Post-processing and hierarchy reconciliation

**Module:** `api/data.py`.

### 15.1 From raw model output to a published forecast

```mermaid
flowchart TD
    MO["Model forecast, 13 weeks"] --> BL["Shrink towards baseline<br/>f = α · model + (1 − α) · naive · festival<br/>α = FORECAST_BLEND = 0.5"]
    NV["Naive run-rate<br/>= gross units in the last 60 days ÷ (60/7)<br/>RUNRATE_DAYS, was 35 until 19 Oct 2026"] --> BL
    BL --> FL["floor at 0"]
    FL --> SKU["SKU weekly series"]
    SKU --> H7["7-day = week 1"]
    SKU --> H10["10-day = week 1 + 3/7 of week 2"]
    SKU --> H35["35-day = weeks 1–5"]
```

- **Shrinkage towards the naive baseline.** Blending with each item's own recent run-rate
  keeps the model close to that item's actual current level. `α` controls how much
  weight the model gets.
- **Festival multiplier.** Only the run-rate part is multiplied by the week's festival
  signal from Stage 4; the model already learns events from its festival features.
  Since 19 Oct 2026, with α = 0.5 (was 0.25 with the multiplier on both parts). A
  walk-forward backtest of the style model (6 cutoffs, May–Sep 2026) cut the weekly-total
  error from 27.5% to 22.7%, Sub Category from 36.2% to 31.8%, style from 62.5% to 61.0%,
  and the bias from +5.3% to −1.4%.
- **Fallback.** Weeks with no model value, or a cold start with no model at all, use
  the naive run-rate × the festival multiplier.

### 15.2 Style and Sub Category rows (Weekly Sales Report)

```mermaid
flowchart TD
    G["Group = Style or Sub Category"] --> D{"For each design in the group:<br/>design-model forecast exists?"}
    D -- yes --> D1["Use the design-model series,<br/>blended and festival-adjusted as in 15.1"]
    D -- no --> D2["Sum its SKU series"]
    D1 --> S["Sum across designs"]
    D2 --> S
    S --> B["Group festival boost<br/>(15.3)"]
    B --> P["Past weeks: backtested model prediction<br/>if available, else naive × festival"]
    P --> R["Re-bucket Monday–Sunday weeks into<br/>calendar weeks 1–7, 8–15, 16–23, 24–end"]
    R --> ROW["Report row:<br/>forecast + actual per calendar week,<br/>month totals, grand totals"]
```

Design-level forecasts are used in preference to adding up SKUs, because a design's
total demand is less noisy than each of its sizes.

### 15.3 Group festival boost

```mermaid
flowchart LR
    U["Next upcoming Festival<br/>and next upcoming Sale"] --> H["Find the same event last year"]
    H --> A["Group's own sales during that event"]
    H --> C["Group's own sales in an equal-length<br/>window just before it"]
    A --> R["ratio = event ÷ pre-event<br/>(capped)"]
    C --> R
    R --> Q{"pre-event volume enough<br/>and ratio > 1?"}
    Q -- no --> N["No boost"]
    Q -- yes --> Y["Upcoming event weeks =<br/>max(current forecast, naive × ratio)"]
```

This uses each Style's or Sub Category's own history of how it responded to that event.
It can only **raise** a forecast. When a week falls in both a festival and a sale, the
larger boost wins.

### 15.4 Channel breakdown (actuals only)

Expanding a Style row, or the TOTAL row, shows its **actual gross sales per marketplace**
for each calendar week. No forecast is made at channel level.

Marketplaces are derived from the channel name. Amazon (including Wishlink), Flipkart,
Meesho, Myntra, Nykaa and Ajio come from the OMS source; the brand websites come from the
WEBSITE source.

### 15.5 Frozen completed weeks

**Module:** `api/forecast_freeze.py`.

Every forecast cell the Weekly Sales Report shows is saved per (scope, row, week). The
rule decides when a cell stops changing:

```mermaid
stateDiagram-v2
    [*] --> Open: week is upcoming
    Open --> Open: data refresh - save the latest forecast
    Open --> Open: week is running - still follows the latest forecast
    Open --> Locked: week has ended - keep the last value it had
    Locked --> Locked: later refreshes or retrains never change it
```

- **Upcoming and running weeks** follow the latest forecast on every refresh.
- **Completed weeks** are locked at the last value they had while they ran, so past
  weeks never change when the model is retrained.
- A week first seen after it had already ended is locked at the value computed then.
- **Frozen view.** The Weekly Sales Report's **Forecast: Latest / Frozen** switch shows:
  - **Running and upcoming weeks:** the 2-month snapshot (`frozen_snapshot`). The forecast
    of the running week and the next ~2 months is saved once and kept unchanged until the
    window ends (62 days); then the next 2 months are frozen. The current one was taken on
    15 Oct 2026; the next is on 16 Dec 2026.
  - **Completed weeks:** each week's forecast as it stood when it began (the freeze store's
    start value, else the Production Log), else the value locked when it ended.

  Hovering a forecast shows both. `FORECAST_2M_FREEZE=off` shows completed weeks only.

The Weekly Production Log is separate: it records each week's forecast and suggested
production as they were when the week started ([section 19.3](#193-weekly-production-log-lifecycle)).

---

## 16. Stage 11: Product lifecycle classification

**Module:** `api/lifecycle.py`.

**Technique:** a **rule-based decision system** with **percentile tiering** and
**weighted scoring**. It runs after forecasting and is never used as a model input,
because it describes the current state and would leak future information into training.

```mermaid
flowchart TD
    A["SKU history"] --> L["launch DRR = sales in first 20 days ÷ days"]
    A --> C["current DRR = sales in last 30 days ÷ 30"]
    L --> G["growth = (current − launch) / launch"]
    C --> G
    G --> R1{"≤ 20 days since launch?"}
    R1 -- yes --> EM["Emerging"]
    R1 -- no --> R2{"No sale in 60 days?"}
    R2 -- yes --> DE["Dead"]
    R2 -- no --> R3{"Sold 60–90 days ago,<br/>quiet 30–60, selling now?"}
    R3 -- yes --> RV["Reviving"]
    R3 -- no --> R4{"growth ≤ −30%?"}
    R4 -- yes --> DC["Declining"]
    R4 -- no --> R5{"growth ≥ +100% and<br/>velocity above median?"}
    R5 -- yes --> EM
    R5 -- no --> R6{"growth ≥ +20%?"}
    R6 -- yes --> GR["Growing"]
    R6 -- no --> STB["Stable"]
```

DRR is the daily run rate: average units sold per day.

**Tiers.** Launch and current tiers T0–T3 are assigned using the 40th, 70th and 90th
percentile cut-offs of DRR across active SKUs.

**Scores (0–100)**

| Score | Built from |
|---|---|
| Health | velocity rank, growth rank, stock turnover, how few returns there are, stage |
| Risk | stage, overstock, return rate |
| Production priority | stage, velocity rank, stockout risk |

| Stage | Production factor | Policy |
|---|---|---|
| Emerging | 1.40 | Scale up |
| Growing | 1.25 | Increase production |
| Reviving | 1.15 | Increase cautiously |
| Stable | 1.00 | Match demand |
| Declining | 0.50 | Taper off |
| Dead | 0.00 | Stop |

---

## 17. Stage 12: Inventory and production planning

**Technique:** inventory-position arithmetic and a **statistical safety-stock** formula.

```mermaid
flowchart LR
    ST["Finished-goods stock"] --> AV["Available = Stock + WIP"]
    WIP["WIP = in-house + job-work + allocation"] --> AV
    FC["Forecast over horizon"] --> REQ["Requirement"]
    DRR["Current DRR"] --> REQ
    STG["Lifecycle stage factor"] --> REQ
    AV --> SUG["Suggested production =<br/>max(0, Requirement − Available)"]
    REQ --> SUG
    SIG["σ of recent weekly demand"] --> SS["Safety stock =<br/>z · σ · √(lead time in weeks)"]
    LT["Replenishment lead time"] --> SS
```

| Output | Formula |
|---|---|
| Available quantity | stock + WIP |
| Produce Now (SKU) | `DRR × 35 − available`, triggered only when available is below the 35-day forecast |
| Stock status | "In Stock" if available ≥ 35-day forecast, otherwise "Needs Production" |
| Lifecycle-adjusted suggestion | `max(0, 35-day forecast × stage factor − available)` |
| Style suggestion (report) | `max(0, forward forecast over the report horizon − available)` |
| Safety stock | `z · σ_weekly · √(lead time in weeks)`; z is the service-level score |

---

## 18. Stage 13: Evaluation and backtesting

**Technique:** **walk-forward (out-of-time) backtesting**, scored with **WAPE**, plus
the diagnostics logged during training.

```
WAPE     = Σ |actual − forecast| / Σ actual
Accuracy = 1 − WAPE   (volume-weighted, clamped to 0–100%)
```

```mermaid
flowchart TD
    A["Forecast snapshot saved on each training run"] --> B{"Has its first forecast week<br/>ended AND settled for 7 days?"}
    B -- no --> W["Wait for later refreshes"]
    B -- yes --> C["Re-apply the same blend + festival<br/>multiplier as was published"]
    C --> D["Compare with real gross actuals"]
    D --> E["Keep the latest prediction per (ID, week)<br/>so each week counts once"]
    E --> F1["Pooled accuracy"]
    E --> F2["Per-ID accuracy"]
    E --> F3["Latest-week accuracy"]
    E --> F4["Per-catalog-tier accuracy"]
```

| Evaluation | When | What it measures |
|---|---|---|
| Cross-validation WAPE | Every training run | Choosing hyperparameters and the model family |
| Test WAPE, R², MAE, RMSE | Every training run | Performance on held-out weeks |
| Walk-forward backtest | Every rebuild, if `RUN_BACKTEST=1` | How the forecast as actually shown compared with what sold, separately for SKU and design |
| Naive proxy | When the backtest is off | Recent-sales baseline projected over recent weeks, compared with actuals |
| Offline scripts | On demand | Compare method choices on real history |

---

## 19. Stage 14: Outputs and delivery

### 19.1 Outputs

| Output | Contents |
|---|---|
| **SKU plan** | 7, 10 and 35-day forecasts; stock, WIP, available; lifecycle stage, tiers, scores; safety stock; production suggestions; predicted festival spike |
| **Weekly Sales Report** | One row per Style or Sub Category: forecast and actual per calendar week, month and grand totals, available quantity, suggested production, upcoming festival and sale outlook |
| **Channel drill-down** | Actual sales per marketplace per week, for one Style or the filtered total |
| **Style lifecycle panel** | Styles that sold last year vs styles launched this year, per Sub Category |
| **Sub Category / Category analysis** | Per month: styles sold, orders, units (with units per style) and month-on-month spike, with a platform-wise drill-down per row and a monthly chart |
| **Tiers Spike Rate** | The same monthly figures per catalog tier (T0, T1, T2, T3, T11, T12, Unclassified), a platform-wise drill-down per tier, and a chart filtered by Tier, Category, Sub Category and Style |
| **Design Suggestions** | Design Analysis (trend status, trending designs, what drives sales) and Suggestion (one design plan per product type) for the running and the next season |
| **Weekly production log** | For each Style and report week: the forecast recorded at the start of the week, the suggestion while the week runs, and the actual sales once it ends |

### 19.2 Delivery path

```mermaid
flowchart LR
    MEM["In-memory plan<br/>& forecasts"] --> CACHE["Response caches<br/>keyed by data version + date"]
    CACHE --> API["FastAPI endpoints<br/>JSON"]
    API --> RQ["React Query cache<br/>(browser)"]
    RQ --> W["Weekly Sales Report page"]
    RQ --> C["Channel drill-down rows"]
    RQ --> CA["Sub Category · Category ·<br/>Tiers Spike Rate pages"]
    RQ --> DS["Design Suggestions page"]
    RQ --> PL["Production Log page"]
    W --> EXP["Export to file"]
    CA --> EXP
```

### 19.3 Weekly production log lifecycle

```mermaid
stateDiagram-v2
    [*] --> Open: week starts - record forecast and suggestion
    Open --> Open: data refresh - update outlook and suggestion
    Open --> Completed: week ends - fill actual sales and lock
    Completed --> Removed: older than latest 8 completed weeks
    Removed --> [*]
```

### 19.4 Sales analysis tabs

**Module:** `api/data.py` (`get_category_analysis`, `get_category_analysis_channels`,
`get_category_analysis_filtered`).

Three tabs share one monthly table built from Gross sale rows since April 2025:

| Tab | One row per | Drill-down |
|---|---|---|
| **Sub Category** | Sub Category | Platform-wise sales of that Sub Category |
| **Category** | Category | Platform-wise sales of that Category |
| **Tiers Spike Rate** | Catalog tier (the style's current tier from Cloud SQL) | Platform-wise sales of that tier |

For each month and row:

| Figure | Definition |
|---|---|
| Styles (+new / −stopped) | distinct styles with a Gross sale that month; in brackets, styles new this month and styles that sold last month but not this month |
| Orders | order lines |
| Units (per style) | units sold; in brackets, units ÷ styles |
| Spike | **same-style spike**: units of the styles that sold in **both** months, this month vs last month, so styles being added or dropped do not distort it. The hover also shows the total-units change, new styles and their units, and stopped styles. The current, incomplete month is compared with the **same days** of the previous month |

- **Tier as: Current tier / Tier Log.** By default every month uses the
  style's current tier, so a style that moves tier takes its past sales with it. The
  "Tier Log" switch counts each sale under the tier the style had on that day (the tier log),
  from the Cloud SQL tier-change log (`CatalogStyleForecastHistory`, which starts on
  1 June 2026); earlier sales keep the current tier.
- **Tier movement** (second sub-tab, `/api/catalog/tier-movement`): per tier, styles now,
  moves in and out, median / average days before moving out, median days so far, and the
  main destinations; each style's tier journey as a timeline; a from → to move grid.
  Same-day tier changes are treated as corrections. Styles whose current tier has no
  entry in the log are shown as "not tracked".
- **Tiers Spike Rate chart filters.** Brand, Tier, Category, Sub Category and a
  searchable Style narrow the chart together. Brand is the master sheet's Brand; for a
  style missing from the sheet, the brand its sales orders carry most often (spelling
  unified). The totals are summed on the server from the sale
  rows of exactly the matching styles, with the same same-style spike as the table.

---

## 20. Design Suggestions

**Module:** `api/design_suggest.py` (endpoint `/api/design/suggestions`), dashboard page
`dashboard/src/pages/DesignSuggestions.tsx`.

The page answers two questions for the **running** and the **next** season:
which product types, colours, fabrics and necks are trending or fading
(**Design Analysis** tab), and what to design for each product type
(**Suggestion** tab).

Seasons: S1 Nov–Jan (winter + wedding), S2 Feb–Apr (wedding, pre-summer, Eid),
S3 May–Jul (summer), S4 Aug–Oct (festival + wedding).

### 20.1 Pipeline

```mermaid
flowchart TD
    S["Gross sale rows<br/>style · date · qty · marketplace"] --> W["Bucket into report weeks<br/>W1 1–7 · W2 8–15 · W3 16–23 · W4 24–end"]
    FC["Running week's forecast<br/>(Weekly Sales Report)"] --> P["Complete the running week:<br/>real sales so far + forecast for the<br/>days left × this week's pace"]
    W --> P
    P --> CH["Weight every marketplace at its usual share<br/>(late marketplace orders do not look like a drop)"]
    CH --> TS["Trend status per value<br/>6 blocks of 4 report weeks"]
    CH --> SP["Sudden spike<br/>one report week vs the 4 before"]
    CH --> TR["4-week trend per style"]
    A[/"Garment attributes<br/>category · sub category · colour · fabric · neck …"/] --> TS
    A --> M
    TS --> M["CatBoost model<br/>style × completed season"]
    TR --> R["Scores per value<br/>(overall and inside each sub category)"]
    TS --> R
    M --> R
    R --> PC["Product cards<br/>trending / in trend only"]
    TS --> DA["Design Analysis tab"]
    SP --> DA
    TR --> DA
    M --> DA
    PC --> SG["Suggestion tab"]
```

### 20.2 Report weeks and the running week

- **Weeks** are the Weekly Sales Report's calendar weeks (W1 1–7, W2 8–15, W3 16–23,
  W4 24–end). "Now" is the last 4 report weeks, compared with the 4 before.
- **The running week** is not finished, so its remaining days are filled with that
  week's forecast as the Weekly Sales Report currently shows it, scaled by the week's
  **pace**: real units so far ÷ the forecast for those same days, per sub category,
  pulled towards 1 while the week has few real days and kept within fixed limits.
- **Late marketplace orders.** Marketplaces report some orders days late. Within each
  week every marketplace is scaled to its usual share of sales, so an incomplete
  marketplace cannot make its products look like they are fading.
- Pieces shown on the page are always **real sales**; the running week's estimate is
  only used for trends and growth.

### 20.3 Trend status and spikes

Each Sub Category, Colour, Fabric and Neck gets its **share of sales** in six blocks of
4 report weeks (about 6 months). Share, not units, so a festival month that lifts
everything is not mistaken for a trend.

```mermaid
flowchart TD
    V["Value on sale now?<br/>(enough units and designs selling)"] -- no --> O["⊘ Out of trend"]
    V -- yes --> C1{"Share up ≥ threshold<br/>vs the block before?"}
    C1 -- yes --> T["▲ Trending"]
    C1 -- no --> C2{"Share ≤ half its best block<br/>and not growing?"}
    C2 -- yes --> O
    C2 -- no --> C3{"Share down ≥ threshold?"}
    C3 -- yes --> F["▼ Starting to fade"]
    C3 -- no --> I["● In trend"]
```

- **Sudden spike (⚡):** a value's share in one report week is at least a set multiple
  of its share in the 4 weeks before, and up by a minimum number of share points. The
  running week is used once it has enough real days, otherwise the last finished week.
- **Trending designs:** individual styles whose real sales plus the running week's
  estimate grew most against the 4 weeks before.

### 20.4 Scores and the model

- **Scores** rank each value overall and inside each sub category, from percentile
  ranks of: sales per design now, share of sales now, the 4-week trend, the trend
  stage, the value's lift in the same season last year, last season's sales per design
  (Empirical-Bayes shrunk), and year-on-year momentum. The running season leans on
  what sells now; the next season gives last year's same season more weight.
- **CatBoost (Tweedie loss)** is trained on one row per style × completed season, with
  the garment attributes, price band, age and each attribute's **trend stage at the
  start of that season** (from sales before the season only). It explains each
  attribute's effect (SHAP, "What drives sales") and ranks colour + fabric mixes.
- **Validation:** 5-fold cross-validation grouped by style (a style is never in train
  and test together) picks the loss power, depth, regularisation and number of trees.
  The search runs in the background after a quick first fit; the chosen settings are
  kept for a week. A **top-pick check** reports how often the model's top 20% of styles
  in a past season really were in that season's top 20%.

### 20.5 Product cards (Suggestion tab)

One card per product type (sub category) that is **Trending or In trend**, best first,
each built only from that product's own ranking so its parts always agree:

| Part | Content |
|---|---|
| Header | Category, product type, trend label, strength (Strong / Good / Try small) |
| Facts | Share of sales, designs selling, sales per design vs average, last year's same season |
| Use these | Best colours, fabrics and details inside this product (trending / in trend only) |
| Best mixes | Model-ranked colour + fabric pairs, made only from the colours and fabrics above; made before or new idea |
| Make more / Cut back | Values selling well with few designs; values fading, out of trend or with too many weak designs |

Strength labels are shown instead of unit numbers because the model ranks reliably
but its unit estimates are rough. An "In short" summary is built only from the cards.

---

## 21. Runtime orchestration and refresh cycle

The whole pipeline runs **on startup**, **every day at 06:00** and **on demand** (the
dashboard's Refresh button). It is designed so users always see a model-based forecast
and never have to wait for training.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Dashboard
    participant API as FastAPI
    participant D as data.rebuild()
    participant L as live_source
    participant F as festival
    participant C as Forecast cache
    participant T as Background trainer
    participant W as weekly_log

    Note over D: Triggered at startup, 06:00 daily,<br/>or POST /admin/refresh
    D->>L: assemble()
    L-->>D: clean integrated frame (or CSV fallback)
    D->>F: calibrate(daily gross units)
    D->>C: newest saved SKU / design forecasts
    C-->>D: forecasts ≤ 28 days old, shifted to align weeks
    D->>D: build plan (Stages 10–12), publish in one step
    D->>D: warm response caches
    User->>UI: open report
    UI->>API: GET /api/reports/weekly-grid
    API-->>UI: JSON (served immediately)
    alt today's forecasts not trained yet
        D->>T: start training thread
        T->>T: SKU model (Stages 6–9)
        T->>T: Design model (Stages 6–9)
        T->>C: save snapshots
        T->>D: rebuild plan with fresh forecasts
        T->>W: complete finished weeks, save running week
        User->>UI: refresh page
        UI->>API: GET …
        API-->>UI: updated forecasts
    end
```

```mermaid
flowchart TD
    S["rebuild()"] --> A{"Saved forecasts<br/>≤ 28 days old?"}
    A -- "yes, today's and current version" --> P1["Publish model plan"]
    A -- "yes, older or previous version" --> P2["Publish model plan (stand-in)<br/>+ start training"]
    A -- no --> P3["Publish naive plan<br/>+ start training"]
    P2 --> T["Training thread"]
    P3 --> T
    T --> OK{"Training succeeded?"}
    OK -- yes --> P4["Save snapshot · publish new plan ·<br/>update weekly log"]
    OK -- "design failed" --> P5["Keep previous design forecasts"]
    OK -- "SKU failed" --> P6["Keep currently published plan"]
```

**Design Suggestions** is built in a background thread on the first request after each
data refresh (the page shows "Preparing suggestions" until then); its cross-validation
search runs afterwards and the page switches to the tuned model when it finishes.

**Design principles**

- **Stale but safe.** The last saved forecast is served, shifted onto the current
  calendar, while the new model trains.
- **Fail soft.** Any failure keeps the previous state. The system never replaces a
  working plan with an empty one.
- **One-step publish.** A new plan replaces the old one in a single step. Caches are
  keyed to the data version and today's date, so they never serve outdated answers.
- **Versioned snapshots.** A change to features or training logic bumps the cache
  version, which forces a retrain. Older snapshots stay usable as stand-ins and remain
  part of the backtest.

---

## 22. Data model

```mermaid
erDiagram
    SALES_ORDER {
        string product_sku_code
        string listing_sku_code
        int qty
        string order_status
        date order_date
        string channel_name
        string source
        string buyer_state
        string buyer_city
        float total
        float promo_discount
        string channel_order_id
        string channel_sub_order_id
    }
    DESIGN_MASTER {
        string DESIGN_NO PK
        string DESIGN_GROUP
        string CATALOG_NAME
        string COLOR
        string SECTION
        date LAUNCH_DATE
    }
    WIP {
        string design
        string size
        float in_house
        float job_work
        float allocation
    }
    STOCK {
        string design
        string size
        float pending_pieces
    }
    ARTICLE {
        string ARTICLE
        string ARTICLE_GROUP
        string USED_IN
    }
    CATALOG_STYLE {
        string name PK
        string tier
        date effective_from
        string image_url
    }
    PLAN_ROW {
        string skuCode PK
        string designNo
        int forecast7
        int forecast10
        int forecast35
        int inventoryQty
        int wipQty
        int availableQty
        string lifecycleStage
        int safetyStock
        int calculatedProductionSuggestion
    }
    FORECAST_SNAPSHOT {
        string id
        date snapshot
        string level
        float_array weekly
    }
    FROZEN_FORECAST {
        string scope PK
        string key PK
        date period_start PK
        float qty
        string status
        date updated_on
    }
    WEEKLY_PRODUCTION_LOG {
        string style PK
        date week_start PK
        int forecast_qty
        int forecast_2m_qty
        int available_qty
        int suggested_production_qty
        int actual_qty
        string status
    }

    DESIGN_MASTER ||--o{ SALES_ORDER : "design key"
    DESIGN_MASTER ||--o{ WIP : "design + size"
    DESIGN_MASTER ||--o{ STOCK : "design + size"
    DESIGN_MASTER }o--o{ ARTICLE : "USED_IN"
    DESIGN_MASTER ||--o| CATALOG_STYLE : "style name"
    SALES_ORDER }o--|| PLAN_ROW : "aggregated into"
    FORECAST_SNAPSHOT }o--|| PLAN_ROW : "blended into"
    PLAN_ROW }o--|| WEEKLY_PRODUCTION_LOG : "rolled up per style"
    PLAN_ROW }o--|| FROZEN_FORECAST : "report cells, locked once a week ends"
```

---

## 23. Summary of techniques and algorithms

| Stage | Technique / algorithm | Module |
|---|---|---|
| Acquisition | BigQuery SQL with row-count validation; ERP REST with retry and back-off; fallback chain | `live_source.py`, `data.py` |
| Cleaning | Rule-based filtering, regex key extraction, left joins, de-duplication, double-count prevention | `live_source.py` |
| Labelling | Status allowlists (Gross / Net / Return) | `data.py`, `lgbm_forecast.py` |
| Aggregation | Monday-anchored weekly bucketing, dense zero-filled panel, maturation cut-off | `lgbm_forecast.py` |
| Festival effects | Log-linear ridge regression against a same-weekday median baseline | `festival.py` |
| Similarity | IDF-weighted Jaccard, inverted index, top-k nearest neighbours, attribute fallback | `similar_design.py`, `design_attributes.py` |
| Features | Lags, rolling statistics, trend, calendar, categorical attributes, point-in-time hierarchy and tier | `lgbm_forecast.py` |
| Outlier handling | Per-ID 99th-percentile capping outside festival weeks, fitted on the training period | `lgbm_forecast.py` |
| Learning algorithm | Gradient-boosted trees: **LightGBM**, Tweedie loss | `lgbm_forecast.py` |
| Validation | Rolling-origin time-series CV, hold-out test set, early stopping | `lgbm_forecast.py` |
| Tuning | Grid search minimising WAPE | `lgbm_forecast.py` |
| Sample weighting | Exponential recency decay, 26-week half-life | `lgbm_forecast.py` |
| Multi-step forecasting | Recursive 13-week forecasting | `lgbm_forecast.py` |
| Cold start | Confidence-weighted shrinkage to analogue curves aligned by weeks since launch | `lgbm_forecast.py`, `similar_design.py` |
| Post-processing | Shrinkage to the naive run-rate, multiplicative festival adjustment, group event-ratio boost | `data.py` |
| Hierarchy | Design-level forecast preferred over summed SKU forecasts | `data.py` |
| Lifecycle | Rule-based stage classification, percentile tiers, weighted scoring | `lifecycle.py` |
| Planning | Inventory position, stage-scaled requirement, statistical safety stock | `data.py`, `lifecycle.py` |
| Evaluation | Walk-forward backtest, WAPE / accuracy, R², MAE, RMSE | `data.py`, `lgbm_forecast.py` |
| Report freeze | Completed report weeks locked at their last value; running and upcoming weeks live | `forecast_freeze.py` |
| Sales analysis | Monthly group aggregation with same-days comparison for the current month; channel and filtered roll-ups | `data.py` |
| Trend status | Share-of-sales time blocks, stage rules (trending / in trend / fading / out), share-based spike detection | `design_suggest.py` |
| Running-week completion | Forecast for remaining days × credibility-weighted pace; per-channel share normalisation | `design_suggest.py` |
| Design ranking | Percentile-rank scoring, Empirical-Bayes shrinkage, season lift | `design_suggest.py` |
| Design model | **CatBoost** (Tweedie) with point-in-time trend-stage features; style-grouped K-fold CV; SHAP effects; top-20% hit rate | `design_suggest.py` |
| Persistence | Versioned JSON forecast snapshots; SQLite weekly log and frozen-forecast store; tuned design-model settings (JSON) | `lgbm_forecast.py`, `weekly_log.py`, `forecast_freeze.py`, `design_suggest.py` |

---

## 24. Appendix

### 24.1 Code map

| Path | Role |
|---|---|
| `api/main.py` | FastAPI routes, CORS, duplicate-instance guard |
| `api/run.py` | Local server entry point (IPv4, IPv6 and LAN sockets on port 8000) |
| `api/data.py` | Orchestration, plan building, post-processing, backtests, report endpoints |
| `api/live_source.py` | Acquisition, cleaning, integration |
| `api/lgbm_forecast.py` | Features, training, selection, recursive forecasting, cold start, snapshot cache |
| `api/festival.py`, `api/festival_calendar.py` | Festival effect model and event calendar |
| `api/similar_design.py`, `api/design_attributes.py` | Similarity graphs; category lookups |
| `api/lifecycle.py` | Lifecycle classification |
| `api/catalog_style.py` | Catalog tiers, tier history, images |
| `api/weekly_log.py` | Weekly production log (SQLite) |
| `api/forecast_freeze.py` | Locks completed report weeks' forecasts (SQLite) |
| `api/design_suggest.py` | Design Suggestions: report weeks, trend status, spikes, scores, CatBoost model, product cards |
| `api/cache_sync.py` | Copies `api/.cache` to and from Cloud Storage on Cloud Run |
| `api/job.py` | Daily batch job (load, train, save the serving snapshot) |
| `api/models.py` | API response schemas |
| `api/tests/` | Unit tests (see 24.7) |
| `api/tools/` | Offline diagnostics: `backtest_sweep.py`, `retro_backtest.py`, `check_month_total_bq.py` |
| `dashboard/src/pages/WeeklySalesGrid.tsx` | Weekly Sales Report |
| `dashboard/src/pages/CategoryAnalysis.tsx` | Sub Category, Category and Tiers Spike Rate tabs |
| `dashboard/src/components/charts/CategoryAnalysisCharts.tsx`, `TierSpikeChart.tsx` | Monthly units & spike chart; the Tiers chart with its filters |
| `dashboard/src/pages/DesignSuggestions.tsx` | Design Suggestions (Design Analysis and Suggestion tabs) |
| `dashboard/src/components/design/` | Trend status, product cards, trend badges, About texts |
| `dashboard/src/pages/ProductionLog.tsx` | Weekly production log |
| `dashboard/src/services/` | API client |
| `Dockerfile`, `dashboard/Dockerfile` | Container images |

### 24.2 API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service, data-source and model status |
| GET | `/api/data-health` | Data checks on the loaded data (see below) |
| POST | `/admin/refresh` | Re-run the full pipeline (one at a time: 409 while running; 429 within `REFRESH_COOLDOWN_SECS` of the last one) |
| GET | `/api/reports/weekly-grid` | Weekly Sales Report data |
| GET | `/api/reports/channel-source/{style}/weekly` | Channel drill-down for one Style |
| GET | `/api/reports/channel-source-total/weekly` | Channel drill-down for the filtered total |
| GET | `/api/reports/weekly-production-log` | Weekly production log |
| GET | `/api/catalog/style-tiers` | Styles by catalog tier, with images |
| GET | `/api/catalog/style-lifecycle` | Styles present last year vs launched this year |
| GET | `/api/reports/category-analysis?level=` | Monthly styles / orders / units / spike per Category, Sub Category, Style or Tier |
| GET | `/api/reports/category-analysis/channels?level=&group=` | One row of that table split by marketplace |
| GET | `/api/reports/category-analysis/filtered?tier=&category=&subCategory=&style=` | Monthly totals for the matching styles (Tiers Spike Rate chart) and every style's filter values |
| GET | `/api/catalog/tier-movement` | Tier stays, moves between tiers and each style's tier journey |
| GET | `/api/design/suggestions` | Design Suggestions (both tabs, running and next season); returns `building` until ready |
| GET | `/api/debug/overall-accuracy`, `/api/debug/tier-accuracy` | Evaluation results currently loaded |

Interactive documentation: `http://localhost:8000/docs`.

### Data checks (`/api/data-health`)

Data and business problems cannot be prevented, so every refresh checks for them. The
results are available at `GET /api/data-health` (not shown on the dashboard, by request,
19 Oct 2026). Nothing is changed by the checks.

| Level | Check | Why it matters |
|---|---|---|
| Error | ERP stock or WIP view failed to load | Available = 0, so every Suggested Production is overstated |
| Error | Sales download incomplete (rows vs BigQuery count) | Actuals and run-rate too low |
| Error | Live data failed, offline CSV or sample data served | Every number out of date |
| Warning | Sales data older than `HEALTH_STALE_DAYS` | Running week and run-rate miss recent days |
| Warning | A sale spike (> `HEALTH_SPIKE_RATIO` × a normal day) inside the run-rate window (`RUNRATE_DAYS`), with the date it clears | Upcoming forecasts and production may be too high until then |
| Warning | A spike on days with no event in the sale calendar | The calendar needs the real sale dates |
| Warning | The calendar expects a sale in the last 7 days but sales are normal | Sale ended or dates wrong; running and upcoming weeks over-forecast |
| Warning | Last completed week's accuracy below `HEALTH_ACCURACY_MIN` % (Production Log) | Review before trusting the next weeks |
| Info | Selling styles with no Sub Category | Missing from the SKU master CSV |

Regression tests for the forecast fixes and these checks: `api/tests/test_forecast_safeguards.py`.

### 24.3 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATA_SOURCE` | `live` | `live` (BigQuery + ERP) or `csv` |
| `FORECAST_MODEL` | `lgbm` | `lgbm` (ML models) or `naive` (baseline only) |
| `FORECAST_BLEND` | `0.5` | Weight α of the model vs the naive run-rate (was 0.25 until 19 Oct 2026) |
| `RUNNING_WEEK_PACE_DAYS` | `7` | The running week is shown as actual so far + this many days' average daily sales × the days left (the model value is still what is locked and scored) |
| `HEALTH_STALE_DAYS`, `HEALTH_SPIKE_RATIO`, `HEALTH_ACCURACY_MIN` | `2`, `2.0`, `70` | Data-check thresholds: data age in days, a sale day vs a normal day, last completed week's accuracy % |
| `FORECAST_2M_FREEZE` | `on` | `on`: the Frozen view also shows the next 2 months as frozen in the active 2-month snapshot; `off`: completed weeks only |
| `FESTIVAL_ON` | `runrate` | `runrate`: forecast = α · model + (1 − α) · naive · festival (the model already learns events); `both`: (α · model + (1 − α) · naive) · festival, the setup before 19 Oct 2026 |
| `RUNRATE_DAYS` | `60` | Days in the naive run-rate (was 35 until 19 Oct 2026; backtest: weekly-total error 22.7% → 21.4%, sale weeks 33.5% → 29.8%, per style 61.0% → 61.6%) |
| `FORECAST_LEVEL` | `style` | `style`: only the style (design) model is trained and served; `both`: also the SKU model (the setup before 19 Oct 2026) |
| `RUN_BACKTEST` | `0` | Enable walk-forward evaluation |
| `DAILY_REFRESH`, `REFRESH_HOUR`, `REFRESH_MINUTE` | `on`, `6`, `0` | Daily pipeline schedule |
| `PROD_LEAD_DAYS`, `SERVICE_Z` | `84`, `1.65` | Safety-stock inputs |
| `CLOUDSQL_DB_USER`, `CLOUDSQL_DB_PASSWORD` | set in `api/.env` | Catalog tier database credentials |
| `ERP_TOKEN` | **required**, set in `api/.env` or the Cloud Run environment | ERP report API token (secret; without it the ERP views - design master, WIP, stock - are skipped) |
| `REFRESH_COOLDOWN_SECS` | `600` | Minimum time between two `/admin/refresh` calls (the dashboard's Refresh button) |
| `ALLOW_LAN` | `1` | Also listen on the LAN interface |
| `VITE_API_URL` | derived from the page's host | Dashboard → API URL |

### 24.4 Running locally

```bash
# Backend (from the repo root)
python -m venv venv
venv\Scripts\pip install -r api\requirements.txt
cd api
..\venv\Scripts\python.exe run.py

# Dashboard
cd dashboard
npm install
npm run dev
```

BigQuery access needs `gcloud auth application-default login`. Use `DATA_SOURCE=csv` to
run from the local snapshot instead. On a first run with no saved forecasts, the naive
baseline is served until background training finishes.

### 24.5 Deployment

```mermaid
flowchart LR
    DEV["Source code"] --> B1["Build API image<br/>Python 3.13 + OpenMP runtime"]
    DEV --> B2["Build dashboard image<br/>Vite build → nginx"]
    B1 --> CR1["Cloud Run: API service"]
    B2 --> CR2["Cloud Run: dashboard service"]
    CR2 -- "HTTPS JSON" --> CR1
    CR1 --> BQ[("BigQuery")]
    CR1 --> ERP[("ERP API")]
    CR1 --> SQL[("Cloud SQL")]
```

Secrets are not in the image: set `ERP_TOKEN`, `CLOUDSQL_DB_USER` and
`CLOUDSQL_DB_PASSWORD` as Cloud Run environment variables (or Secret Manager references).

On Cloud Run, `api/.cache` (forecast snapshots, the weekly log and the frozen-forecast
store) is copied to a Cloud Storage bucket when `CACHE_BUCKET` is set (`cache_sync.py`),
so a restart or redeploy keeps them. The API image must be rebuilt after a change to
`api/requirements.txt` (for example CatBoost for Design Suggestions); without CatBoost,
Design Suggestions falls back to rankings only.

### 24.6 Estimated GCP running cost

These are **estimates**, not a quote. They use Cloud Run's published rates for
**instance-based billing** in a Tier 1 region: $0.000018 per vCPU-second and $0.000002
per GiB-second, with a monthly free allowance of 240,000 vCPU-seconds and 450,000
GiB-seconds. A month is taken as 730 hours. `asia-south1` (Mumbai) is billed at the
higher Tier 2 rates, estimated here at about 1.4× Tier 1. Check your region and the
current rates with the [GCP pricing calculator](https://cloud.google.com/products/calculator).

**Why the API has to stay on all the time.** The backend keeps the whole dataset and
all forecasts in memory, runs the 06:00 refresh from a background thread, and trains the
models in a background thread. On Cloud Run this needs:

- **min-instances = 1**, so the in-memory state isn't lost when the service scales to zero
- **instance-based billing** ("CPU always allocated"), so the background threads are not
  paused between requests

The API service is therefore billed 24/7. That is where almost all of the cost comes
from.

**Monthly cost of the API service**

| API instance size | Tier 1 (e.g. `us-central1`) | Tier 2 (e.g. `asia-south1`) |
|---|---|---|
| 2 vCPU, 8 GiB | about $130 | about $185 |
| 4 vCPU, 16 GiB | about $270 | about $375 |

The size you need depends on peak memory while loading all sales rows and training the
two models. Measure it locally before choosing; 8 GiB may be too little.

**Other services**

| Service | Expected monthly cost | Why |
|---|---|---|
| Dashboard (Cloud Run, nginx) | ≈ $0 | Static files, scales to zero, stays inside the request-based free allowance |
| BigQuery | ≈ $0 | One scan a day; the first 1 TiB of queries per month is free |
| Cloud SQL | $0 extra | Uses the existing `orlin-prod` instance |
| Artifact Registry, Cloud Build | < $1 | Two container images; builds fit in the free build minutes |
| Cloud Logging | ≈ $0 | Well under the 50 GiB/month free allowance |
| Internet egress | < $1 | Small JSON responses |
| Static outbound IP (optional) | ≈ $5–40 | Only needed if the ERP server allows only whitelisted IPs (Cloud NAT + static IP) |

**Estimated total**

| Setup | Monthly total |
|---|---|
| Current design, 2 vCPU / 8 GiB | ≈ $135–190 |
| Current design, 4 vCPU / 16 GiB | ≈ $275–380 |

**Ways to reduce the cost**

1. **Move training to a Cloud Run Job**, triggered daily by Cloud Scheduler. The job
   runs for a limited time with large resources, then stops.
2. **Save forecasts to Cloud Storage** instead of the container's local disk. This costs
   cents, and a restart no longer triggers a full retrain.
3. **Keep the API small** (for example 1 vCPU / 4 GiB) and have it only load the saved
   forecasts. This could bring the total to roughly **$75–110 per month**, but it needs
   code changes.
4. **Use a committed use discount** for 1 or 3 years to lower the always-on compute
   rate.
5. **Set a budget alert** in Cloud Billing so unexpected usage is caught early.

### 24.7 Tests and diagnostics

| Script | Purpose |
|---|---|
| `api/tests/test_lgbm_forecast.py` | Unit tests: WAPE, outlier capping, CV folds, recency weights, point-in-time features |
| `api/tests/test_design_suggest.py` | Unit tests: seasons, report weeks, trend stages, spikes, late-channel correction, running-week pace, scores, product cards, style-grouped folds |
| `api/tests/test_forecast_freeze.py` | Unit tests: running weeks follow the live forecast, completed weeks lock, old locks reopen |
| `api/tests/test_weekly_log.py` | Unit tests for the weekly production log |
| | Run all: `python -m pytest api -q` |
| `api/tools/backtest_sweep.py` | Compare blend weights and festival on/off using saved snapshots |
| `api/tools/retro_backtest.py` | Retrain the design model at past cut-off dates and score against real history |
| `api/tools/check_month_total_bq.py` | Independent monthly Gross/Net total check against BigQuery |
