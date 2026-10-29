"""Tests for design_suggest.py (season-wise design suggestions).

Run: python -m pytest api/tests/test_design_suggest.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import pandas as pd

import design_suggest as ds


def test_season_mapping_and_labels():
    assert ds.season_of(date(2026, 10, 8)) == ("S4", 2026)
    assert ds.season_of(date(2026, 1, 15)) == ("S1", 2025)      # January belongs to the S1 that began in November
    assert ds.season_of(date(2026, 11, 2)) == ("S1", 2026)
    assert ds.season_of(date(2026, 3, 1)) == ("S2", 2026)
    assert ds.next_season("S4", 2026) == ("S1", 2026)
    assert ds.next_season("S1", 2025) == ("S2", 2026)
    assert ds.season_bounds("S1", 2025) == (date(2025, 11, 1), date(2026, 1, 31))
    assert ds.season_bounds("S4", 2026) == (date(2026, 8, 1), date(2026, 10, 31))
    assert ds.season_label("S1", 2025) == "S1 2025-26"
    assert ds.season_label("S3", 2026) == "S3 2026"


def test_neck_names_are_unified():
    assert ds._clean_neck("round") == "Round Neck"
    assert ds._clean_neck("V Neck") == "V Neck"
    assert ds._clean_neck("Unknown") == ""


def _joined(rows):
    return pd.DataFrame(rows).set_index("style")


def test_rank_attribute_shrinks_small_samples_and_scores():
    # Pink: 2 styles with very high sales; Blue: 10 styles with solid sales.
    rows = [{"style": f"P{i}", "units": 900.0, "colour": "Pink"} for i in range(2)]
    rows += [{"style": f"B{i}", "units": 400.0, "colour": "Blue"} for i in range(10)]
    rows += [{"style": "X1", "units": 50.0, "colour": "Rare"}]          # 1 style -> not ranked
    joined = _joined(rows)
    season_total = joined["units"].sum()
    mu = season_total / len(joined)
    out = {r["value"]: r for r in ds._rank_attribute("colour", joined, joined, season_total, season_total, mu, {})}
    assert "Rare" not in out                                             # below MIN_STYLES
    assert out["Pink"]["perStyleRaw"] == 900.0
    assert mu < out["Pink"]["perStyle"] < 900.0                          # pulled toward the average
    assert out["Blue"]["confidence"] == 1.0 and out["Pink"]["confidence"] < 1.0
    assert all(0 <= r["score"] <= 100 for r in out.values())


def test_style_folds_never_split_a_style():
    style = pd.Series([f"S{i // 3}" for i in range(60)])           # 20 styles x 3 seasons
    folds = ds._style_folds(style, 5)
    assert len(folds) == 5
    assert sum(int(f.sum()) for f in folds) == 60                    # every row tested exactly once
    for f in folds:
        assert set(style[f]).isdisjoint(set(style[~f]))              # no style in train and test


def test_opportunities_flag_gaps_and_saturation():
    rows = []
    rows += [{"style": f"G{i}", "units": 3000.0, "subCategory": "Kurta", "colour": "Maroon", "fabric": "Silk"} for i in range(2)]
    rows += [{"style": f"S{i}", "units": 50.0, "subCategory": "Kurta", "colour": "Beige", "fabric": "Silk"} for i in range(12)]
    rows += [{"style": f"N{i}", "units": 400.0, "subCategory": "Kurta", "colour": "Blue", "fabric": "Silk"} for i in range(5)]
    joined = _joined(rows)
    on_sale = {s: (30.0, 30.0) for s in joined.index}   # every style sold recently, flat year on year
    opp = ds._opportunities(joined, joined["units"].mean(), on_sale)
    kinds = {(o["value"], o["type"]) for o in opp if o["attribute"] == "colour"}
    assert ("Maroon", "gap") in kinds
    assert ("Beige", "saturated") in kinds
    assert not any(v == "Blue" for v, _ in kinds)
    # Nothing sold recently (discontinued): not suggested at all - the same
    # "on sale now" rule as the ranked cards.
    opp = ds._opportunities(joined, joined["units"].mean(), {})
    assert not [o for o in opp if o["attribute"] == "colour"]


def _opp_fixture():
    rows = []
    rows += [{"style": f"G{i}", "units": 3000.0, "subCategory": "Kurta", "colour": "Maroon", "fabric": "Silk"} for i in range(2)]
    rows += [{"style": f"S{i}", "units": 50.0, "subCategory": "Kurta", "colour": "Beige", "fabric": "Silk"} for i in range(12)]
    rows += [{"style": f"N{i}", "units": 400.0, "subCategory": "Kurta", "colour": "Blue", "fabric": "Silk"} for i in range(5)]
    return _joined(rows)


def test_current_trend_overrides_last_season_calls():
    joined = _opp_fixture()
    # Last 4 weeks vs the 4 before: Maroon's share collapses, Beige's share jumps.
    trend = {**{f"G{i}": (20.0, 200.0) for i in range(2)},
             **{f"S{i}": (100.0, 20.0) for i in range(12)},
             **{f"N{i}": (100.0, 100.0) for i in range(5)}}
    opp = ds._opportunities(joined, joined["units"].mean(), {}, trend=trend, joined_all=joined)
    kinds = {(o["value"], o["type"]) for o in opp if o["attribute"] == "colour"}
    assert ("Maroon", "declining") in kinds and ("Maroon", "gap") not in kinds      # fading -> not "design more"
    assert ("Beige", "rising") in kinds and ("Beige", "saturated") not in kinds     # rising -> not "design less"


def test_sections_share_one_rule_set():
    # The same TREND_STRONG decides rising / falling everywhere.
    assert ds._direction(ds.TREND_STRONG) == 1 and ds._direction(-ds.TREND_STRONG) == -1
    assert ds._direction(ds.TREND_STRONG - 1) == 0 and ds._direction(None) == 0
    joined = _opp_fixture()
    trend = {**{f"G{i}": (20.0, 200.0) for i in range(2)},
             **{f"S{i}": (100.0, 20.0) for i in range(12)},
             **{f"N{i}": (100.0, 100.0) for i in range(5)}}
    sc = ds._subcategory_rankings(joined, joined, {}, trend, ds.WEIGHTS)["Kurta"]["rankings"]["colour"]
    opp = ds._opportunities(joined, joined["units"].mean(), {}, trend=trend, joined_all=joined)
    by_value = {o["value"]: o for o in opp if o["attribute"] == "colour"}
    for r in sc:   # every call agrees with the within-sub-category evaluation it came from
        if r["value"] in by_value:
            assert by_value[r["value"]]["trend"] == r["trend"]
            assert by_value[r["value"]]["score"] == r["score"]
    assert by_value["Beige"]["type"] == "rising" and ds._direction(by_value["Beige"]["trend"]) == 1
    assert by_value["Maroon"]["type"] == "declining" and ds._direction(by_value["Maroon"]["trend"]) == -1
    assert ds.rules()["trendStrong"] == ds.TREND_STRONG


def _block_day(snap, k):
    """A day inside lifecycle block k (0 = oldest) - report weeks, see _chunk_frame."""
    o = ds._chunk_ord(snap) - (ds.LIFE_BLOCKS - 1 - k) * ds.TREND_CHUNKS - 1
    return pd.Timestamp(ds._chunk_bounds(o)[0])


def test_lifecycle_stages():
    snap = date(2026, 10, 7)          # last day of Oct W1: the running report week is complete
    # Units per block (oldest -> newest, 4 report weeks each) per colour; 2 styles each.
    plan = {
        "Rising": [100, 100, 100, 100, 100, 250],     # share jumps in the last block
        "Steady": [400, 400, 400, 400, 400, 400],
        "Fading": [100, 100, 200, 200, 200, 120],     # down 40% but above half its peak
        "Out": [300, 300, 300, 100, 80, 70],          # far below its peak, not rising
        "Gone": [200, 200, 200, 100, 50, 0],          # nothing sold now
    }
    rows, attrs = [], []
    for colour, blocks in plan.items():
        for i in range(2):
            st = f"{colour}{i}"
            attrs.append({"style": st, "category": "Ethnic", "subCategory": "Kurta", "colour": colour, "fabric": "Silk"})
            for k, q in enumerate(blocks):
                if q:
                    rows.append({"style": st, "date": _block_day(snap, k), "qty": q / 2})
    sales = pd.DataFrame(rows)
    sales["qty"] = sales["qty"].round().astype("Int64")   # the live data's nullable integer qty
    styles = pd.DataFrame(attrs).set_index("style")
    life = ds._lifecycle(ds._chunk_frame(sales, snap), styles, snap)
    stage = {r["value"]: r["stage"] for r in life["colour"]}
    assert stage == {"Rising": "trending", "Steady": "steady", "Fading": "fading", "Out": "out", "Gone": "out"}
    assert all(len(r["share"]) == ds.LIFE_BLOCKS for r in life["colour"])
    assert len(life["blocks"]) == ds.LIFE_BLOCKS
    assert life["blocks"][-1]["to"] == "2026-10-07" and life["blocks"][-1]["from"] == "2026-09-08"   # Sep W2 - Oct W1
    assert ds._stage_maps(life)["colour"]["Out"] == "out"


def test_lifecycle_recovering_value_is_not_out():
    snap = date(2026, 10, 7)
    plan = {"Back": [400, 400, 100, 60, 60, 100], "Base": [400] * 6}   # far below its best but growing again
    rows, attrs = [], []
    for colour, blocks in plan.items():
        for i in range(2):
            st = f"{colour}{i}"
            attrs.append({"style": st, "category": "Ethnic", "subCategory": "Kurta", "colour": colour, "fabric": "Silk"})
            for k, q in enumerate(blocks):
                rows.append({"style": st, "date": _block_day(snap, k), "qty": q / 2})
    life = ds._lifecycle(ds._chunk_frame(pd.DataFrame(rows), snap), pd.DataFrame(attrs).set_index("style"), snap)
    assert {r["value"]: r["stage"] for r in life["colour"]}["Back"] == "trending"


def test_rank_attribute_reports_share_trend():
    joined = _opp_fixture()
    trend = {**{f"S{i}": (100.0, 20.0) for i in range(12)}, **{f"N{i}": (100.0, 100.0) for i in range(5)}}
    total = joined["units"].sum()
    out = {r["value"]: r for r in ds._rank_attribute("colour", joined, joined, total, total, total / len(joined), {}, trend=trend)}
    assert out["Beige"]["trend"] > 0 > out["Blue"]["trend"]       # Beige gains share, Blue loses it
    assert "Maroon" not in out                                     # nothing selling now -> not suggested
    assert out["Beige"]["activeStyles"] == 12
    assert out["Beige"]["score"] > out["Blue"]["score"]            # ranked on what sells and grows now


def test_trend_stage_feeds_the_score_and_suggestions():
    joined = _opp_fixture()
    trend = {st: (100.0, 100.0) for st in joined.index}               # same sales now for everything
    total = joined["units"].sum()
    args = ("colour", joined, joined, total, total, total / len(joined), {})
    plain = {r["value"]: r["score"] for r in ds._rank_attribute(*args, trend=trend)}
    staged = {r["value"]: r for r in ds._rank_attribute(*args, trend=trend,
                                                        stage_of={"Maroon": "trending", "Blue": "out"})}
    assert staged["Maroon"]["stage"] == "trending" and staged["Beige"]["stage"] == "none"
    assert [ds._suggestable(staged[v]) for v in ("Maroon", "Blue", "Beige")] == [True, False, False]
    assert ds._suggestable({"stage": None})   # attributes without a trend stage (neck, ...)
    assert staged["Maroon"]["score"] > plain["Maroon"] and staged["Blue"]["score"] < plain["Blue"]
    # Out of trend is never "design more", even when it sells well per design.
    stages = {"colour": {"Maroon": "out"}}
    sc_rank = ds._subcategory_rankings(joined, joined, {}, trend, ds.WEIGHTS, stages)
    opp = ds._opportunities(joined, joined["units"].mean(), {}, trend=trend, joined_all=joined, sc_rank=sc_rank)
    assert not [o for o in opp if o["value"] == "Maroon" and o["type"] == "gap"]
    # ... nor anything inside a sub category that is out of trend.
    sc_rank = ds._subcategory_rankings(joined, joined, {}, trend, ds.WEIGHTS, {"subCategory": {"Kurta": "out"}})
    opp = ds._opportunities(joined, joined["units"].mean(), {}, trend=trend, joined_all=joined, sc_rank=sc_rank)
    assert opp and not [o for o in opp if o["type"] in ("gap", "rising")]
    assert abs(sum(ds.WEIGHTS.values()) - 1) < 1e-9 and abs(sum(ds.WEIGHTS_UPCOMING.values()) - 1) < 1e-9


class _FakeModel:
    """Predicts by colour so the mix order is known."""
    def predict(self, X):
        return X["colour"].map({"Maroon": 300.0, "Blue": 200.0, "Beige": 100.0}).fillna(150.0).to_numpy()


def test_product_cards_parts_agree():
    rows = []
    rows += [{"style": f"G{i}", "units": 3000.0, "subCategory": "Kurta", "colour": "Maroon", "fabric": "Silk"} for i in range(2)]
    rows += [{"style": f"S{i}", "units": 50.0, "subCategory": "Kurta", "colour": "Beige", "fabric": "Silk"} for i in range(12)]
    rows += [{"style": f"N{i}", "units": 400.0, "subCategory": "Kurta", "colour": "Blue", "fabric": "Cotton"} for i in range(5)]
    rows += [{"style": f"O{i}", "units": 400.0, "subCategory": "Kurta", "colour": "Grey", "fabric": "Silk"} for i in range(4)]
    joined = _joined(rows).assign(category="Ethnic", embroidery="", neck="", sleeve="", topType="")
    trend = {st: (100.0, 100.0) for st in joined.index}
    trend.update({f"G{i}": (400.0, 400.0) for i in range(2)})
    stages = {"subCategory": {"Kurta": "steady"}, "category": {"Ethnic": "steady"},
              "colour": {"Maroon": "trending", "Beige": "steady", "Blue": "steady", "Grey": "out"},
              "fabric": {"Silk": "steady", "Cotton": "steady"}}
    total = joined["units"].sum()
    block = {"rankings": {"subCategory": ds._rank_attribute("subCategory", joined, joined, total, total, total / len(joined), {},
                                                            trend=trend, stage_of=stages["subCategory"])}}
    sc_rank = ds._subcategory_rankings(joined, joined, {}, trend, ds.WEIGHTS, stages)
    block["opportunities"] = ds._opportunities(joined, joined["units"].mean(), {}, trend=trend, joined_all=joined, sc_rank=sc_rank)
    model = {"model": _FakeModel(), "cap": 1e9}
    cards = ds._products(block, sc_rank, model, "S4", joined, stages, avg_now=100.0)
    assert [(c["subCategory"], c["category"]) for c in cards] == [("Kurta", "Ethnic")]
    card = cards[0]
    use = {(a, u["value"]) for a in ("colour", "fabric") for u in card["use"][a]}
    assert ("colour", "Grey") not in use                                    # out of trend: never to use
    assert {("colour", "Grey")} <= {(c["attribute"], c["value"]) for c in card["cutBack"]}
    assert not use & {(c["attribute"], c["value"]) for c in card["cutBack"]}   # cut back never in use
    assert {(m["attribute"], m["value"]) for m in card["makeMore"]} <= use     # make more always in use
    assert card["mixes"] and all(("colour", m["colour"]) in use and ("fabric", m["fabric"]) in use for m in card["mixes"])
    assert card["mixes"][0]["colour"] == "Maroon" and card["mixes"][0]["level"] == "strong"
    assert ds._overview(cards)["focus"] == ["Kurta"]


def test_recommended_styles_rank_feature_fit_with_forecast():
    styles = pd.DataFrame([
        {"style": "FULL", "category": "Ethnic", "subCategory": "Kurta", "colour": "Red", "fabric": "Silk",
         "embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed"},
        {"style": "PARTIAL", "category": "Ethnic", "subCategory": "Kurta", "colour": "Red", "fabric": "Silk",
         "embroidery": "", "neck": "", "sleeve": "", "topType": ""},
        {"style": "WRONG", "category": "Ethnic", "subCategory": "Kurta", "colour": "Blue", "fabric": "Cotton",
         "embroidery": "", "neck": "", "sleeve": "", "topType": ""},
    ]).set_index("style")
    cards = [{
        "subCategory": "Kurta", "category": "Ethnic", "score": 80, "level": "strong",
        "use": {
            "colour": [{"value": "Red"}], "fabric": [{"value": "Silk"}],
            "details": {"embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed"},
        },
    }]
    result = ds._recommended_styles(cards, styles, {"FULL": 100, "PARTIAL": 500, "WRONG": 1000})
    assert [row["style"] for row in result] == ["FULL", "PARTIAL"]
    assert result[0]["featureMatchCount"] == 8
    assert result[0]["matchedFeatures"]["colour"] == "Red"
    assert result[1]["expectedSale"] == 500 and result[1]["forecastScore"] == 100.0
    assert all(row["style"] != "WRONG" for row in result)  # does not match enough suggested features


def test_recommended_styles_put_most_matched_features_first():
    base = {"category": "Ethnic", "subCategory": "Kurta", "colour": "Red", "fabric": "Silk",
            "embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed"}
    styles = pd.DataFrame([
        {"style": "ALL8_SMALL", **base},
        {"style": "ALL8_BIG", **base},
        {"style": "SEVEN_HUGE", **base, "sleeve": "Half Sleeve"},
    ]).set_index("style")
    cards = [{
        "subCategory": "Kurta", "category": "Ethnic", "score": 80, "level": "strong",
        "use": {"colour": [{"value": "Red"}], "fabric": [{"value": "Silk"}],
                "details": {"embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed"}},
    }]
    result = ds._recommended_styles(cards, styles, {"ALL8_SMALL": 50, "ALL8_BIG": 400, "SEVEN_HUGE": 9000})
    # every suggested feature first, the bigger expected sale breaking the tie
    assert [row["style"] for row in result] == ["ALL8_BIG", "ALL8_SMALL", "SEVEN_HUGE"]
    assert result[2]["missingFeatures"] == ["sleeve"] and result[2]["styleFeatures"] == {"sleeve": "Half Sleeve"}


def test_recommended_styles_carry_feature_trend_stages():
    styles = pd.DataFrame([{"style": "A", "category": "Ethnic", "subCategory": "Kurta", "colour": "Red", "fabric": "Silk",
                            "embroidery": "Zari", "neck": "Round Neck", "sleeve": "", "topType": ""}]).set_index("style")
    cards = [{"subCategory": "Kurta", "category": "Ethnic", "score": 80, "level": "strong",
              "use": {"colour": [{"value": "Red"}], "fabric": [{"value": "Silk"}],
                      "details": {"embroidery": "Zari", "neck": "Round Neck"}}}]
    stages = {"colour": {"Red": "trending"}, "fabric": {"Silk": "steady"}, "neck": {"V Neck": "trending"}}
    row = ds._recommended_styles(cards, styles, {"A": 10}, stages)[0]
    # only matched features with a Trend status get a stage
    assert row["featureStages"] == {"colour": "trending", "fabric": "steady"}


def test_recommended_styles_are_based_on_sub_category_and_colour():
    def style(code, colour, **kw):
        return {"style": code, "category": "Ethnic", "subCategory": kw.pop("sc", "Kurta"), "colour": colour, "fabric": "Silk",
                "embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed", **kw}
    styles = pd.DataFrame([
        style("PINK_ALL", "Pink"),                          # 2nd colour, every feature
        style("RED_FEW", "Red", fabric="Cotton", neck="V Neck"),   # best colour, fewer features
        style("BLUE_ALL", "Blue"),                          # colour not suggested -> left out
        style("DRESS_RED", "Red", sc="Dress"),              # 2nd card
        style("NOCOL", "Red", sc="Shrug"),                  # its card suggests no colour -> left out
    ]).set_index("style")
    details = {"embroidery": "Zari", "neck": "Round Neck", "sleeve": "Full Sleeve", "topType": "Dyed"}
    cards = [
        {"subCategory": "Kurta", "category": "Ethnic", "score": 80, "level": "strong",
         "use": {"colour": [{"value": "Red", "stage": "trending"}, {"value": "Pink", "stage": "steady"}],
                 "fabric": [{"value": "Silk"}], "details": details}},
        {"subCategory": "Dress", "category": "Ethnic", "score": 60, "level": "good",
         "use": {"colour": [{"value": "Red"}], "fabric": [{"value": "Silk"}], "details": details}},
        {"subCategory": "Shrug", "category": "Ethnic", "score": 50, "level": "good",
         "use": {"colour": [], "fabric": [{"value": "Silk"}], "details": details}},
    ]
    rows = ds._recommended_styles(cards, styles, {s: 100 for s in styles.index})
    # sub-category first, then the card's colour order - before the feature count
    assert [r["style"] for r in rows] == ["RED_FEW", "PINK_ALL", "DRESS_RED"]
    assert rows[0]["colour"] == "Red" and rows[0]["colourRank"] == 0 and rows[0]["colourStage"] == "trending"
    assert rows[2]["subCategoryRank"] == 1


def test_recommended_styles_stay_visible_when_the_expected_sale_is_unavailable():
    styles = pd.DataFrame([{"style": "A", "category": "Ethnic", "subCategory": "Kurta", "colour": "Red", "fabric": "Silk",
                            "embroidery": "Zari", "neck": "Round Neck", "sleeve": "", "topType": ""}]).set_index("style")
    cards = [{"subCategory": "Kurta", "category": "Ethnic", "score": 80, "level": "strong",
              "use": {"colour": [{"value": "Red"}], "fabric": [{"value": "Silk"}],
                      "details": {"embroidery": "Zari", "neck": "Round Neck"}}}]
    rows = ds._recommended_styles(cards, styles, {}, include_zero=True)
    assert len(rows) == 1 and rows[0]["expectedSale"] == 0


def test_style_forecast_uses_design_total_once_and_falls_back_to_skus():
    import types
    data = types.SimpleNamespace(PLAN_ROWS=[
        types.SimpleNamespace(designNo="A", designForecast35=120, forecast35=50),
        types.SimpleNamespace(designNo="A", designForecast35=120, forecast35=70),
        types.SimpleNamespace(designNo="B", designForecast35=0, forecast35=20),
        types.SimpleNamespace(designNo="B", designForecast35=0, forecast35=30),
    ])
    assert ds._style_forecast35(data) == {"A": 120, "B": 50}


def test_style_forecast_9w_sums_running_and_next_report_weeks():
    import types
    ns = types.SimpleNamespace
    starts, months = [], []
    year, month = 2026, 10
    for _ in range(4):  # Oct 2026 through Jan 2027: 16 calendar report weeks
        report_weeks = []
        for number, day in enumerate((1, 8, 16, 24), start=1):
            start = date(year, month, day).isoformat()
            starts.append(start)
            report_weeks.append(ns(weekStart=start, label=f"W{number}"))
        months.append(ns(label=date(year, month, 1).strftime("%B %Y"), weeks=report_weeks))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    # Oct W1/W2/W3 complete, Oct W4 running, the rest upcoming; 10 units a week.
    def cell(i):   # Oct W4 running with 4 sold so far
        return ns(actual=5 if i < 3 else 4 if i == 3 else None, partial=i >= 3, forecast=10.0)
    grid = ns(months=months, weeklyTotals=ns(cells={s: cell(i) for i, s in enumerate(starts)}),
              rows=[ns(key="a-1", cells={s: cell(i) for i, s in enumerate(starts)})])
    expected, window = ds._style_forecast_9w(ns(get_weekly_grid=lambda *a, **k: grid))
    assert expected == {"A-1": 90}                       # Oct W4 .. Dec W4 = 9 report weeks
    weekly = window.pop("_weekly")
    week_list = window.pop("weekList")
    assert window == {"weeks": 9, "from": "Oct W4", "to": "Dec W4", "start": "2026-10-24", "end": "2026-12-31",
                      "running": True}
    assert weekly == {"A-1": {"weekly": [10] * 9, "soldSoFar": 4}}
    assert [w["label"] for w in week_list] == ["W4", "W1", "W2", "W3", "W4", "W1", "W2", "W3", "W4"]
    assert week_list[0] == {"start": "2026-10-24", "end": "2026-10-31", "month": "October 2026", "label": "W4",
                            "eventCategory": "", "eventName": ""}


def test_top_pick_hit_rate():
    y = list(range(50))
    assert ds._top_pick_hit(y, y, ["S4 2025"] * 50) == 100.0               # perfect ranking
    assert ds._top_pick_hit(y, [-v for v in y], ["S4 2025"] * 50) == 0.0   # reversed


def test_spikes_use_share_not_units():
    snap = date(2026, 10, 7)          # Oct W1 complete -> the spike week is Oct W1
    o = ds._chunk_ord(snap)
    rows = []
    for colour, base_q, now_q in (("Steady", 100, 400), ("Jump", 20, 300), ("Small", 1, 4)):
        for i in range(2):
            for k in range(0, ds.SPIKE_BASE_CHUNKS + 1):
                q = now_q if k == 0 else base_q        # everything x4 this week (a festival)...
                rows.append({"style": f"{colour}{i}", "date": pd.Timestamp(ds._chunk_bounds(o - k)[0]), "qty": float(q * 7)})
    styles = pd.DataFrame([{"style": f"{c}{i}", "category": "E", "subCategory": "K", "colour": c, "fabric": "Silk"}
                           for c in ("Steady", "Jump", "Small") for i in range(2)]).set_index("style")
    sp, window = ds._spikes(ds._chunk_frame(pd.DataFrame(rows), snap), styles, snap)
    sp = sp["colour"]
    assert window["week"] == "Oct W1"
    assert "Jump" in sp and sp["Jump"]["ratio"] >= ds.SPIKE_RATIO          # ...only Jump gained share
    assert "Steady" not in sp                                              # rose with everything else
    assert "Small" not in sp                                               # grew no faster than the total


def test_report_weeks_match_the_weekly_report():
    assert ds._chunk_bounds(ds._chunk_ord(date(2026, 10, 8))) == (date(2026, 10, 8), date(2026, 10, 15))   # W2: 8-15
    assert ds._chunk_bounds(ds._chunk_ord(date(2026, 2, 28))) == (date(2026, 2, 24), date(2026, 2, 28))    # W4: 24-end
    assert ds._chunk_label(ds._chunk_ord(date(2026, 10, 1))) == "Oct W1"
    assert ds._chunk_ord(date(2027, 10, 1)) - ds._chunk_ord(date(2026, 10, 1)) == ds.CHUNKS_PER_YEAR


def test_late_channel_orders_do_not_look_like_a_drop():
    # Two colours; Red sells only on a marketplace, Blue only on the website.
    # In the latest week the marketplace's orders are only half in (late).
    snap = date(2026, 10, 7)
    o = ds._chunk_ord(snap)
    rows = []
    for k in range(8):
        late = 0.5 if k == 0 else 1.0
        day = pd.Timestamp(ds._chunk_bounds(o - k)[0])
        for i in range(2):
            rows.append({"style": f"R{i}", "date": day, "qty": 100.0 * late, "channel": "Myntra"})
            rows.append({"style": f"B{i}", "date": day, "qty": 100.0, "channel": "Website"})
    frame = ds._chunk_frame(pd.DataFrame(rows), snap)
    trend = ds._trend(frame)
    tot_now, tot_before = ds._trend_totals(trend)
    _, _, change, _ = ds._share_trend(["R0", "R1"], trend, tot_now, tot_before)
    assert abs(change) < 1e-6                     # without the channel weighting Red would be "falling"


def test_running_week_is_completed_by_its_forecast():
    snap = date(2026, 10, 9)          # Oct W2 (8-15): 2 days in, 6 to come
    rows = [{"style": "A1", "date": pd.Timestamp(date(2026, 10, 8)), "qty": 10.0, "channel": "Website"}]
    frame = ds._chunk_frame(pd.DataFrame(rows), snap, projection={"A1": {"Website": 30.0}})
    now = frame[frame["offset"] == 0]
    assert float(now["qty"].sum()) == 40.0 and bool(now["proj"].any())
    trend = ds._trend(frame)
    assert trend["A1"][0] == 40.0 and trend["A1"][2] == 10.0      # completed week; real units kept apart
    assert ds._active(trend["A1"]) and not ds._active((40.0, 0.0, 0.0))   # forecast alone is not "selling"




def test_running_week_rest_follows_this_weeks_pace(monkeypatch):
    import types
    import forecast_freeze
    snap = date(2026, 10, 9)                       # Oct W2 (8-15): 2 real days, 6 to come
    # The report's weekly forecast 80 for the 8-day week = 10 a day; it really sold 40 in 2 days (2x pace).
    monkeypatch.setattr(forecast_freeze, "stored", lambda scope: {"A1": {"2026-10-08": 80.0}})
    data = types.SimpleNamespace(PLAN_ROWS=[], _NAIVE_WEEK={})
    sales = pd.DataFrame([{"style": "A1", "date": pd.Timestamp("2026-10-08"), "qty": 40.0, "channel": "Website"}])
    sales["ord"] = ds._add_chunk_ord(sales)
    styles = pd.DataFrame([{"style": "A1", "subCategory": "Kurta"}]).set_index("style")
    proj, info = ds._running_projection(data, sales, snap, styles)
    credibility = 2 / (2 + ds.PACE_PRIOR_DAYS)
    expected_pace = 1 + (2.0 - 1) * credibility                   # pulled toward 1 with only 2 real days
    assert abs(info["pace"]["Kurta"] - round(expected_pace, 2)) < 1e-9
    assert abs(sum(proj["A1"].values()) - 80 * 6 / 8 * expected_pace) < 1e-6
    assert info["frozenStyles"] == 1 and info["forecastDays"] == 6
