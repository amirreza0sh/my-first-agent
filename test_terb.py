"""Tests for the TERB tool layer and agent loop. No API key needed.

Run:  pytest -q        (or)        python test_terb.py
"""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import agent as agent_mod
import sample_data
from agent import TerbAgent, build_system_prompt
from terb_tools import (TEHRAN, TOOL_SCHEMAS, Dataset, TerbTools, haversine_km, open_status,
                        parse_hours, whatsapp_link)

# Sunday 2026-10-25 15:00 Tehran time
SUNDAY_3PM = datetime(2026, 10, 25, 15, 0, tzinfo=TEHRAN)
AZADI = (35.6997, 51.3380)
SHOP1 = (35.7010, 51.3395)


def make_tools(now=SUNDAY_3PM):
    return TerbTools(Dataset.sample(), clock=lambda: now)


def ids(result_key):
    return {s["shop_id"] for s in result_key}


# ---------------------------------------------------------------- opening hours
def test_weekday_mapping_uses_real_day():
    t = make_tools(datetime(2026, 10, 23, 12, 0, tzinfo=TEHRAN))  # Friday
    assert t.is_shop_open("shop_1")["status"] == "closed"          # friday: تعطیل
    t = make_tools(datetime(2026, 10, 24, 12, 0, tzinfo=TEHRAN))  # Saturday
    assert t.is_shop_open("shop_1")["status"] == "open"


def test_open_closed_unknown_and_opens_at():
    t = make_tools()
    assert t.is_shop_open("shop_1")["status"] == "open"
    assert t.is_shop_open("shop_4")["status"] == "closed"          # explicit "closed" on Sunday
    assert t.is_shop_open("shop_5")["status"] == "unknown"         # no hours in data
    early = make_tools(datetime(2026, 10, 25, 8, 0, tzinfo=TEHRAN)).is_shop_open("shop_1")
    assert early["status"] == "closed" and early["opens_at"] == "09:00"


def test_closing_soon():
    r = make_tools(datetime(2026, 10, 25, 20, 45, tzinfo=TEHRAN)).is_shop_open("shop_1")
    assert r["status"] == "open" and r["closes_at"] == "21:00"
    assert r["minutes_until_close"] == 15 and r["closing_soon"] is True


def test_overnight_and_split_hours():
    hours = {"saturday": "20:00-02:00", "sunday": "20:00-02:00", "monday": "09:00-13:00,16:00-21:00"}
    at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=TEHRAN)
    assert open_status(hours, at(25, 22, 0))["status"] == "open"            # Sunday 22:00
    spill = open_status(hours, at(25, 1, 0))                                # Sunday 01:00, from Saturday
    assert spill["status"] == "open" and spill["closes_at"] == "02:00"
    assert open_status(hours, at(25, 3, 0))["status"] == "closed"
    mid = open_status(hours, at(26, 14, 0))                                 # Monday between intervals
    assert mid["status"] == "closed" and mid["opens_at"] == "16:00"
    assert parse_hours("تعطیل") == [] and parse_hours("") is None and parse_hours("abc") is None


# ---------------------------------------------------------------- stage 1
def test_search_persian_and_english_sorted_by_availability():
    t = make_tools()
    en = t.search_products("Nike")
    fa = t.search_products("نایک")
    assert [p["product_id"] for p in en["products"]] == ["prod_1", "prod_3"]
    assert [p["product_id"] for p in fa["products"]] == ["prod_1", "prod_3"]
    assert en["products"][0]["shop_count"] == 5


def test_search_budget_brand_and_size():
    t = make_tools()
    assert [p["product_id"] for p in t.search_products("Nike", budget_min=450000, budget_max=550000)["products"]] == ["prod_1"]
    assert t.search_products("Nike", size="45")["total_matching"] == 0
    assert t.search_products("Nike", size="۴۲")["total_matching"] == 2          # Persian digits
    adidas = t.search_products("کفش", brand="adidas")["products"][0]
    assert adidas["product_id"] == "prod_2"
    assert adidas["shop_count"] == 2                                            # out_of_stock offer ignored
    assert (adidas["lowest_price"], adidas["highest_price"]) == (450000, 470000)


# ---------------------------------------------------------------- location
def test_resolve_location():
    t = make_tools()
    assert t.resolve_location("نزدیک میدان آزادی")["name"] == "میدان آزادی"
    assert t.resolve_location("ونک")["found"] is True
    exact = t.resolve_location("35.7895, 51.3890")
    assert exact["found"] and exact["lat"] == 35.7895 and exact["approximate"] is False
    assert t.resolve_location("40.71, -74.00")["found"] is False               # outside Tehran
    unknown = t.resolve_location("شیراز")
    assert unknown["found"] is False and unknown["known_areas"]


# ---------------------------------------------------------------- stage 2
def test_find_shops_sorted_by_distance_with_price_age():
    r = make_tools().find_shops("prod_1", *AZADI)
    dists = [s["distance_km"] for s in r["shops"]]
    assert dists == sorted(dists) and r["shops"][0]["shop_id"] == "shop_1"
    by_id = {s["shop_id"]: s for s in r["shops"]}
    assert by_id["shop_1"]["price_age_days"] == 3 and by_id["shop_1"]["price_stale"] is False
    assert by_id["shop_3"]["price_age_days"] == 7 and by_id["shop_3"]["price_stale"] is True
    assert by_id["shop_1"]["name"] == "مغازه الف"                               # has instagram
    assert by_id["shop_2"]["name"] is None                                      # fake-name shop: use address


def test_filters_and_fallback_query():
    t = make_tools()
    assert ids(t.find_shops("prod_1", *AZADI, installment_only=True)["shops"]) == {"shop_1", "shop_3", "shop_4"}
    assert ids(t.find_shops("prod_1", *AZADI, delivery_only=True)["shops"]) == {"shop_3"}
    assert ids(t.find_shops("prod_1", *AZADI, max_distance_km=1)["shops"]) == {"shop_1", "shop_3"}
    assert ids(t.find_shops("prod_1", *AZADI, open_now_only=True)["shops"]) == {"shop_1", "shop_2", "shop_3"}
    # "shop A was closed / out of stock" -> alternatives that are open now
    alt = t.find_shops("prod_1", *AZADI, open_now_only=True, exclude_shop_ids=["shop_1"])["shops"]
    assert [s["shop_id"] for s in alt] == ["shop_3", "shop_2"]
    assert "shop_5" not in ids(t.find_shops("prod_2", *AZADI)["shops"])        # out_of_stock never listed


def test_unknown_product():
    assert "error" in make_tools().find_shops("nope", *AZADI)


# ---------------------------------------------------------------- stage 3
def test_compare_never_recommends_closed_shop_even_if_cheapest():
    r = make_tools().compare_shops("prod_1", *AZADI)
    assert r["recommendation"]["shop_id"] == "shop_1"
    assert "shop_4" not in ids(r["ranked"])                                     # cheaper (490K) but closed
    assert ids(r["closed_shops"]) == {"shop_4"}
    unknown = next(s for s in r["ranked"] if s["shop_id"] == "shop_5")
    assert unknown["score_breakdown"]["hours"] == 60.0
    assert all(s["hours"]["status"] != "closed" for s in r["ranked"])


def test_zero_distance_gets_best_distance_score():
    best = make_tools().compare_shops("prod_1", *SHOP1)["recommendation"]
    assert best["shop_id"] == "shop_1" and best["distance_km"] == 0
    assert best["score_breakdown"]["distance"] == 100.0 and best["score"] == 100.0


def test_compare_flags_and_no_mutation():
    t = make_tools()
    r = t.compare_shops("prod_1", *AZADI)
    top = r["recommendation"]
    assert top["is_cheapest"] and top["is_closest"] and top["saving_vs_next"] == 5000
    first = t.find_shops("prod_1", *AZADI)
    assert "score" not in first["shops"][0]                                     # scoring doesn't mutate


def test_compare_when_everything_closed():
    night = make_tools(datetime(2026, 10, 25, 23, 30, tzinfo=TEHRAN))
    r = night.compare_shops("prod_1", *AZADI, open_now_only=False, exclude_shop_ids=["shop_5"])
    assert r["recommendation"] is None and r["closed_shops"] and "message" in r


# ---------------------------------------------------------------- stage 4
def test_shop_details_links_and_no_phone():
    d = make_tools().get_shop_details("shop_1", product_id="prod_1", size="42")
    assert d["whatsapp_link"].startswith("https://wa.me/989123456789?text=")
    assert "42" in unquote(d["whatsapp_link"])
    assert "35.701" in d["google_maps_link"] and d["hours"]["status"] == "open"
    assert d["product_offer"]["price"] == 495000 and d["product_offer"]["price_age_days"] == 3
    assert "0210000001" not in json.dumps(d)                                    # fake phone numbers never leak
    no_wa = make_tools().get_shop_details("shop_2")
    assert no_wa["whatsapp_link"] is None and no_wa["contact_note"]
    assert make_tools().get_shop_details("shop_5")["likely_in_mall_or_bazaar"] is True
    assert make_tools().get_shop_details("shop_2")["likely_in_mall_or_bazaar"] is False


def test_whatsapp_link_formats():
    assert whatsapp_link("09121112233") == "https://wa.me/989121112233"
    assert whatsapp_link("+98 912 345 6789") == "https://wa.me/989123456789"
    assert whatsapp_link(None) is None and whatsapp_link("123") is None


# ---------------------------------------------------------------- plumbing
def test_execute_dispatch_and_json_serializable():
    t = make_tools()
    assert "error" in t.execute("nope", {})
    assert "error" in t.execute("find_shops", {"bogus": 1})
    calls = {
        "search_products": {"query": "Nike"}, "resolve_location": {"query": "آزادی"},
        "find_shops": {"product_id": "prod_1", "lat": AZADI[0], "lng": AZADI[1]},
        "compare_shops": {"product_id": "prod_1", "lat": AZADI[0], "lng": AZADI[1]},
        "get_shop_details": {"shop_id": "shop_1"}, "is_shop_open": {"shop_id": "shop_1"},
    }
    assert set(calls) == {s["name"] for s in TOOL_SCHEMAS}
    for name, args in calls.items():
        out = t.execute(name, args)
        assert "error" not in out, (name, out)
        json.dumps(out, ensure_ascii=False)


def test_haversine_zero_and_known_distance():
    assert haversine_km(*AZADI, *AZADI) == 0
    assert 8.5 < haversine_km(*AZADI, 35.7570, 51.4090) < 9.5                   # Azadi -> Vanak (~9 km)


def test_loader_reads_real_schema_files():
    with tempfile.TemporaryDirectory() as d:
        for name, data in [("shops", sample_data.SHOPS), ("products", sample_data.PRODUCTS), ("offers", sample_data.OFFERS)]:
            (Path(d) / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        t = TerbTools(Dataset.from_dir(d), clock=lambda: SUNDAY_3PM)
    assert t.compare_shops("prod_1", *AZADI)["recommendation"]["shop_id"] == "shop_1"


# ---------------------------------------------------------------- agent loop (fake client)
def _resp(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def _text(s):
    return SimpleNamespace(type="text", text=s)


def _tool(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    def __init__(self, responses):
        self.calls, self._responses = [], list(responses)
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # snapshot
        return self._responses.pop(0)


def test_agent_tool_roundtrip():
    client = FakeClient([_resp("tool_use", _tool("t1", "search_products", query="Nike")),
                         _resp("end_turn", _text("پیشنهاد من نایک ایر مکس است."))])
    a = TerbAgent(tools=make_tools(), client=client, model="test-model")
    assert a.chat("کفش نایک می‌خوام") == "پیشنهاد من نایک ایر مکس است."
    last = client.calls[1]["messages"][-1]
    assert last["role"] == "user" and last["content"][0]["tool_use_id"] == "t1"
    payload = json.loads(last["content"][0]["content"])
    assert payload["products"][0]["product_id"] == "prod_1" and "is_error" not in last["content"][0]
    assert client.calls[0]["model"] == "test-model"
    assert {t["name"] for t in client.calls[0]["tools"]} == {t["name"] for t in TOOL_SCHEMAS}


def test_agent_flags_tool_errors():
    client = FakeClient([_resp("tool_use", _tool("t1", "no_such_tool")), _resp("end_turn", _text("ok"))])
    a = TerbAgent(tools=make_tools(), client=client)
    a.chat("hi")
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_agent_round_limit_and_memory():
    looping = FakeClient([_resp("tool_use", _tool(f"t{i}", "search_products", query="Nike")) for i in range(2)])
    a = TerbAgent(tools=make_tools(), client=looping, max_tool_rounds=2)
    assert "دوباره" in a.chat("x") and len(looping.calls) == 2
    two_turns = FakeClient([_resp("end_turn", _text("یک")), _resp("end_turn", _text("دو"))])
    b = TerbAgent(tools=make_tools(), client=two_turns)
    b.chat("اول")
    b.chat("دوم")
    assert len(two_turns.calls[1]["messages"]) == 3                             # history kept across turns


def test_system_prompt_has_time_and_data_rules():
    p = build_system_prompt(SUNDAY_3PM)
    assert "یکشنبه" in p and "2026-10-25 ساعت 15:00" in p and "{NOW}" not in p
    assert "هرگز شماره تلفن نده" in p and "آدرس" in p and "فقط تهران" in p
    assert agent_mod.DEFAULT_MODEL


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
