"""Spec §11.3 rule tests + bill reader, run against a fresh database (defaults from the spec)."""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")

from app import pdfgen  # noqa: E402
from app.billparse import parse_bill_text, parse_receipt_text  # noqa: E402
from app.compute import advise, compute, load_master  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

init_db()
with SessionLocal() as s:
    M = load_master(s)


def opt(kw=5, typ="ongrid", module="STC550", qty=None, price=300000, **k):
    return dict(type=typ, kw=kw, module=module, qty=qty, price=price, **k)


def test_lt7a_is_arkvolt_without_subsidy_text():
    out = compute({"bill": {"tariff": "LT-7A", "units": 1085, "months": 2}, "options": [opt()]}, M)
    assert out["brand"] == "ARKVOLT" and out["options"][0]["split"]["cfa"] == 0
    assert any("40% accelerated depreciation" in t for _, t in out["assumptions"]["clar"])
    html = pdfgen.render_html(out, M, "ARK/SOL/COM/2026-27/001", "16/11/2026")
    assert "PM Surya Ghar: Muft Bijli" not in html and "Nil" in html


def test_lt1_2kw_subsidy():
    out = compute({"bill": {"tariff": "LT-1", "units": 400, "months": 2}, "options": [opt(kw=2, qty=4, price=150000, inverter="X3T")]}, M)
    assert out["options"][0]["split"]["cfa"] == 60000


def test_string_error_blocks():
    out = compute({"bill": {"tariff": "LT-1", "units": 700, "months": 2}, "options": [opt(qty=10, inverter="X3T")]}, M)
    assert any("512" in e and "450" in e for e in out["errors"])


def test_non_dcr_blocks_adroit():
    M2 = {**M, "products": {**M["products"], "STC550": {**M["products"]["STC550"], "dcr": False}}}
    out = compute({"bill": {"tariff": "LT-1", "units": 700, "months": 2}, "options": [opt()]}, M2)
    assert any("DCR" in e for e in out["errors"])


def test_load_extension_bullet():
    out = compute({"bill": {"tariff": "LT-1", "units": 1200, "months": 2, "connected_load_kw": 3}, "options": [opt()]}, M)
    assert any(h == "Connected load" and "load extension" in t for h, t in out["assumptions"]["clar"])


def test_ongrid_only_drops_hybrid_pages_and_battery_lines():
    out = compute({"bill": {"tariff": "LT-1", "units": 1200, "months": 2}, "options": [opt()]}, M)
    assert not any(r[0].startswith("Battery") or r[0] == "Lithium battery" for r in out["bom"])
    html = pdfgen.render_html(out, M, "R", "D")
    assert "page_hybrid.svg" not in html and "page_ongrid.svg" in html


def test_override_below_margin_needs_approval():
    pk = next(p for p in M["packages"] if p["name"] == "5 kW On-grid · Statcon")
    o = dict(type="ongrid", kw=5, module="STC550", qty=10, package_id=pk["id"], price_override=270000)
    out = compute({"bill": {"tariff": "LT-1", "units": 1200, "months": 2}, "options": [o]}, M)
    assert out["needs_approval"]
    o["price_override"] = 295000  # within 5%
    assert not compute({"bill": {"tariff": "LT-1", "units": 1200, "months": 2}, "options": [o]}, M)["needs_approval"]


def test_advisor_domestic():
    a = advise({"tariff": "LT-1", "units": 1025, "months": 2, "connected_load_kw": 4.5}, M)
    assert a["brand"] == "ADROIT" and a["target_kw"] == 5 and a["recommend"]["type"] == "ongrid"


SAMPLE_PDF_TEXT = """Section        [1234]-Electrical Section Testville
Consumer#
1100000000001
Name & Mailing Address
A. SAMPLE
HOUSE NO 1, TEST ROAD
For redressing complaints
Tariff/Phase
LT-1/Single
Billing Period
11/2026[Bi-Monthly]
Connected Load
3200 Watts    Security Deposit
Bill Date
06-11-2026
KWH
Normal
Import
100.00
300.00
1
200
KWH
Off Peak
Import
50.00
150.00
1
100
KWH
Peak
Import
10.00
60.00
1
50
Total Amt.(Bill#1)          (a+b+c+d)
4321.00
"""


def test_bill_pdf_text():
    b = parse_bill_text(SAMPLE_PDF_TEXT)
    assert b["consumer_no"] == "1100000000001" and b["tariff"] == "LT-1" and b["phase"] == "1"
    assert b["connected_load_kw"] == 3.2 and b["units_total"] == 350 and b["monthly_units"] == 175
    assert b["bill_amount"] == 4321 and b["section_code"] == "1234" and b["tod"]


def test_receipt_text():
    b = parse_receipt_text("C#:1100000000001\nTariff : LT-1 Dom\nLoad: 5kW\nKWH/NL/I 13053 12680 373 404\nKWH/OP/I 12037 11538 499 454\nBill Amount(Rs): 11044.00")
    assert b["consumer_no"] == "1100000000001" and b["tariff"] == "LT-1" and b["units_total"] == 872 and b["bill_amount"] == 11044


def test_no_bill_standard_3kw_uses_package_prices():
    pk = [p for p in M["packages"] if p["name"].startswith("3 kW")]
    opts = [dict(type=p["type"], kw=p["kw"], module=p["module"], qty=p["qty"], package_id=p["id"]) for p in pk]
    out = compute({"bill": {"no_bill": True}, "options": opts}, M)
    assert not out["errors"] and out["brand"] == "ADROIT" and out["assumed"]
    assert sorted(o["price"] for o in out["options"]) == sorted(p["price"] for p in pk)
    assert "No KSEB bill used" in out["assumptions"]["basis"][0][1]
    assert "consumer no." not in out["texts"]["p1"]


def test_custom_10kw_two_inverters_and_rate_price():
    out = compute({"bill": {"no_bill": True, "tariff": "LT-7A"},
                   "options": [dict(type="ongrid", kw=10, module="STC550"), dict(type="hybrid", kw=8, module="STC550")]}, M)
    a, b = out["options"]
    assert not out["errors"]
    assert (a["n_inv"], a["inverter"], a["qty"]) == (2, "X5T", 20) and a["price"] == 600000
    assert (b["n_inv"], b["n_bat"], b["inverter"]) == (2, 2, "BHF5K")
    assert any(r[3][0] == "2 Nos." for r in out["bom"] if r[0] == "On-grid inverter")
