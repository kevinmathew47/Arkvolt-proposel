"""Acceptance tests from spec §11: money within ±₹1, payback within ±0.05 years."""
import pytest

from app import engine as E
from app.catalog import INVERTERS, MODULES

T = E.default_tariffs()
R = E.Rules()
LT1, LT7A = T["LT-1"], T["LT-7A"]


def test_bill_checks():
    assert E.bill(183, LT1) == pytest.approx(973.61, abs=0.01)
    assert E.bill(350, LT1) == pytest.approx(2926, abs=0.5)
    assert E.bill(600, LT1) == pytest.approx(6072, abs=0.5)


def test_cfa():
    assert E.cfa(2, True) == 60000
    assert E.cfa(3, True) == 78000
    assert E.cfa(5, True) == 78000
    assert E.cfa(5, False) == 0


def run(cons, kw, kwp, hybrid, mod, price, tariff=LT1, subsidy=True, commercial=False, usable=0.0):
    ps = E.price_split(price, kw, subsidy)
    res = E.project(cons, kw, kwp, hybrid, MODULES[mod], tariff, ps["net_cost"], commercial=commercial,
                    battery_usable_kwh=usable)
    return ps, res


# option, cons, kw, kwp, hybrid, module, price, net, epc, gst, units1, saving1, payback, total, gain
RESIDENTIAL = [
    (350, 3, 3.3, False, "STC550", 205000, 130540, 188246, 16754, 4698, 35583, 3.5, 1245263, 1084723),
    (350, 3, 3.6, False, "AVD720", 220000, 145540, 202020, 17980, 5203, 36895, 3.8, 1308377, 1132837),
    (350, 3, 3.3, True, "STC550", 308000, 233540, 282828, 25172, 4580, 35278, 6.2, 1232247, 898707),
    (350, 3, 3.6, True, "AVD720", 320000, 245540, 293848, 26152, 5073, 36558, 6.2, 1300347, 954807),
    (600, 5, 5.5, False, "STC550", 300000, 227900, 275482, 24518, 7829, 73091, 3.0, 2566118, 2298218),
    (600, 5, 5.76, False, "AVD720", 320000, 247900, 293848, 26152, 8326, 74378, 3.2, 2664800, 2376900),
    (600, 5, 5.5, True, "STC550", 420000, 347900, 385675, 34325, 7634, 72492, 4.6, 2540105, 2092205),
    (600, 5, 5.76, True, "AVD720", 440000, 367900, 404040, 35960, 8117, 73838, 4.7, 2645207, 2177307),
]


@pytest.mark.parametrize("cons,kw,kwp,hyb,mod,price,net,epc,gst,u1,s1,pb,total,gain", RESIDENTIAL)
def test_residential(cons, kw, kwp, hyb, mod, price, net, epc, gst, u1, s1, pb, total, gain):
    ps, res = run(cons, kw, kwp, hyb, mod, price)
    assert ps["net_cost"] == pytest.approx(net, abs=1)
    assert ps["epc"] == epc and ps["gst"] == gst
    assert round(res.years[0]["gen"]) == u1
    assert res.years[0]["saving"] == pytest.approx(s1, abs=1)
    assert res.payback == pytest.approx(pb, abs=0.05)
    assert res.total_saving == pytest.approx(total, abs=1)
    assert res.net_gain == pytest.approx(gain, abs=1)


def test_low_use_house():
    ps, res = run(183, 3, 3.3, False, "STC550", 205000)
    assert ps["net_cost"] == 130540
    assert res.years[0]["saving"] == pytest.approx(17745, abs=1)
    assert res.payback == pytest.approx(7.0, abs=0.05)
    assert res.net_gain == pytest.approx(390720, abs=1)


@pytest.mark.parametrize("hyb,price,inv,u1,used,s1,pb,total,gain", [
    (False, 300000, 305900, 7829, 3906, 56776, 5.1, 1897542, 1551642),
    (True, 420000, 425900, 7634, 5396, 65682, 6.1, 2282614, 1756714),
])
def test_commercial(hyb, price, inv, u1, used, s1, pb, total, gain):
    ps, res = run(1085 / 2, 5, 5.5, hyb, "STC550", price, tariff=LT7A, subsidy=False, commercial=True,
                  usable=4.6 if hyb else 0)
    assert ps["net_cost"] == pytest.approx(inv, abs=1)
    assert round(res.years[0]["gen"]) == u1
    assert round(res.years[0]["used"]) == used
    assert res.years[0]["saving"] == pytest.approx(s1, abs=1)
    assert res.payback == pytest.approx(pb, abs=0.05)
    assert res.total_saving == pytest.approx(total, abs=1)
    assert res.net_gain == pytest.approx(gain, abs=1)


def test_string_rules():
    d = E.string_design(10, MODULES["STC550"], INVERTERS["X3T"], 5.5, True)
    assert any("512" in e and "450" in e for e in d["errors"])
    d = E.string_design(5, MODULES["AVD720"], INVERTERS["X3T"], 3.6, True)
    assert not d["errors"] and any("16 A" in w for w in d["warnings"])
    d = E.string_design(10, MODULES["STC550"], INVERTERS["X5T"], 5.5, True)
    assert not d["errors"] and d["text"].startswith("1 × 10") and "512 V cold" in d["text"]
    d = E.string_design(10, MODULES["STC550"], INVERTERS["BHF5K"], 5.5, True)  # 512 V cold > 500 V → split
    assert not d["errors"] and d["strings"] == 2 and d["series"] == 5


def test_words_and_grouping():
    assert E.amount_in_words(320000) == "Rupees Three Lakh Twenty Thousand only"
    assert E.inr(130540) == "1,30,540"
    assert E.inr(2298218) == "22,98,218"
