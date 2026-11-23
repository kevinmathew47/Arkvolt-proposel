"""HTML templates → PDF with headless Edge/Chromium, then datasheets + contact page appended (spec §10)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import fitz
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import charts
from .db import DATA, ROOT
from .engine import inr, lakh

TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"
env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
env.filters["inr"] = lambda v, d=0: inr(v or 0, d)
env.filters["lakh"] = lambda v: lakh(v or 0)
CONTACT = {"ADROIT": "adroit_contact.pdf", "ARKVOLT": None}  # Arkvolt's contact page is drawn in HTML


def _scope(out, master):
    brand = out["brand"]
    hyb, sub = out["has_hybrid"], out["subsidy"]

    def fill(s):
        if s.startswith("{hyb:"):
            return s[5:-1] if hyb else ""
        return (s.replace("{battery}", ", battery (hybrid)" if hyb else "")
                 .replace("{battery_comma}", "battery, " if hyb else "")
                 .replace("{hyb_backup}", " and, for hybrid, connection of backup loads to the inverter" if hyb else "")
                 .replace("{subsidy_docs}", " and PM Surya Ghar subsidy application" if sub else "")
                 .replace("{roof}", f"{out['roof_sqft']:.0f}")
                 .replace("{battery_wall}", " (and battery for hybrid options)" if hyb else "")
                 .replace("{and_subsidy}", " and subsidy" if sub else ""))
    texts = master["texts"]
    ours = json.loads(texts.get(f"{brand}.scope_ours") or texts.get("ADROIT.scope_ours"))
    cust = json.loads(texts.get(f"{brand}.scope_customer") or texts.get("ADROIT.scope_customer"))
    return {"ours": [x for x in map(fill, ours) if x], "customer": [x for x in map(fill, cust) if x]}


def render_html(out, master, ref, date, signatory="", bill=None):
    brand = out["brand"]
    t = master["texts"]
    texts = {"about": t.get(f"{brand}.about", ""), "why": json.loads(t.get(f"{brand}.why", "[]")),
             "terms": json.loads(t.get(f"{brand}.terms", "[]"))}
    ch = {"monthly": charts.monthly_generation(out), "cumulative": charts.cumulative(out)}
    tpl = env.get_template("pdf/adroit.html" if brand == "ADROIT" else "pdf/arkvolt.html")
    return tpl.render(out=out, ref=ref, date=date, signatory=signatory, bill=bill or {}, texts=texts, charts=ch, scope=_scope(out, master),
                      timeline=json.loads(master["settings"].get("timeline", "[]")),
                      asset=(STATIC / "brand" / brand.lower()).as_uri(), static=STATIC.as_uri())


def html_to_pdf(html: str, dest: Path):
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "proposal.html"
        src.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="msedge")
            except Exception:
                b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(src.as_uri(), wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(300)
            pg.pdf(path=str(dest), prefer_css_page_size=True, print_background=True)
            b.close()


def body_overflow(html: str) -> list:
    """Overflow check (spec §10): pages whose body content runs into the footer (bottom > 800 pt)."""
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "p.html"
        src.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="msedge")
            except Exception:
                b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(src.as_uri(), wait_until="networkidle")
            res = pg.evaluate("""() => [...document.querySelectorAll('.page')].map((pg, i) => {
                const top = pg.getBoundingClientRect().top; let m = 0;
                pg.querySelectorAll('.body *').forEach(e => { const r = e.getBoundingClientRect(); if (r.height) m = Math.max(m, r.bottom - top) });
                return [i + 1, m * 0.75] })""")  # CSS px → pt
            b.close()
    return [(i, round(v)) for i, v in res if v > 800]


def generate(out, master, ref, date, dest: Path, signatory="", bill=None) -> dict:
    html = render_html(out, master, ref, date, signatory, bill)
    body_pdf = dest.with_suffix(".body.pdf")
    html_to_pdf(html, body_pdf)
    doc = fitz.open(body_pdf)
    n_body = doc.page_count
    ds_dir = DATA / "datasheets"
    for name in out.get("datasheets", []) + ([CONTACT[out["brand"]]] if CONTACT.get(out["brand"]) else []):
        f = ds_dir / name
        if f.exists():
            doc.insert_pdf(fitz.open(f))
    doc.save(dest, garbage=4, deflate=True)
    doc.close()
    body_pdf.unlink(missing_ok=True)
    return {"pages": fitz.open(dest).page_count, "body_pages": n_body, "size_kb": dest.stat().st_size // 1024}
