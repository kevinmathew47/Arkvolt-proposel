"""Everything the wizard summary and the PDF need, computed from the user's inputs + master data.

compute(inputs, master) is pure given `master` (a snapshot dict), so a stored proposal version can be
re-rendered years later with exactly the tariff/rules/products it was quoted with.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re

from sqlalchemy import select

from . import engine as E
from .db import Company, Package, Product, RuleVersion, Setting, TariffVersion, TemplateText

LETTERS = "ABCD"
STATCON_BLUE, AVAADA_GOLD = "#1b69a8", "#f5b82e"


# ---------------------------------------------------------------------------------------------
# Master data snapshot
# ---------------------------------------------------------------------------------------------
def load_master(s, on_date: str | None = None) -> dict:
    on_date = on_date or dt.date.today().isoformat()
    tariffs = {}
    for t in s.scalars(select(TariffVersion).order_by(TariffVersion.valid_from)):
        if t.valid_from <= on_date and (not t.valid_to or t.valid_to >= on_date):
            tariffs[t.code] = {"id": t.id, "name": t.name, "valid_from": t.valid_from, **t.data}
    rv = s.scalars(select(RuleVersion).where(RuleVersion.valid_from <= on_date).order_by(RuleVersion.valid_from.desc())).first()
    products = {p.code: {"category": p.category, "datasheet": p.datasheet, "active": p.active, **p.data}
                for p in s.scalars(select(Product))}
    packages = [dict(id=p.id, brand=p.brand, name=p.name, kw=p.kw, type=p.type, module=p.module, qty=p.qty,
                     inverter=p.inverter, battery=p.battery, price=p.price, note=p.note)
                for p in s.scalars(select(Package).where(Package.active.is_(True)))]
    companies = {c.brand: c.data for c in s.scalars(select(Company))}
    texts = {f"{t.brand}.{t.key}": t.text for t in s.scalars(select(TemplateText))}
    settings = {x.key: x.value for x in s.scalars(select(Setting))}
    return dict(date=on_date, tariffs=tariffs, rules={"id": rv.id, **rv.data}, products=products, packages=packages,
                companies=companies, texts=texts, settings=settings)


def _tariff(m: dict) -> E.Tariff:
    inf = lambda sl: [(E.INF if u is None else u, r) for u, r in sl]  # noqa: E731
    return E.Tariff(m.get("code", ""), m["name"], inf(m["telescopic"]), m["telescopic_upto"], inf(m["flat"]),
                    m.get("duty", 0.1), m.get("fixed_per_kw", 0), m.get("domestic", False))


def _rules(m: dict) -> E.Rules:
    d = {k: v for k, v in m.items() if k in E.Rules.__dataclass_fields__}
    for k in ("battery_repl_years", "payment_schedule", "monthly_profile"):
        if k in d:
            d[k] = tuple(d[k])
    return E.Rules(**d)


def _module(p):
    return E.Module(**{k: p[k] for k in E.Module.__dataclass_fields__})


def _inverter(p):
    return E.Inverter(**{k: p[k] for k in E.Inverter.__dataclass_fields__})


def _battery(p):
    return E.Battery(**{k: p[k] for k in E.Battery.__dataclass_fields__})


# ---------------------------------------------------------------------------------------------
# Rules: brand, inverter choice, references
# ---------------------------------------------------------------------------------------------
def brand_for(tariff_code: str | None, override: str | None = None) -> str | None:
    if override in ("ADROIT", "ARKVOLT"):
        return override
    if not tariff_code:
        return None
    return "ADROIT" if tariff_code == "LT-1" else "ARKVOLT"


def auto_inverter(products: dict, kw: float, hybrid: bool) -> str | None:
    cands = [(c, p) for c, p in products.items() if p["category"] == "inverter" and p["hybrid"] == hybrid and p.get("active", True)]
    cands.sort(key=lambda cp: cp[1]["kw"])
    if not cands:
        return None
    # fewest inverters first, then the smallest model that covers the plant (10 kW → 2 × 5 kW, not 2 × 5.5 kW)
    n = max(1, math.ceil(kw / cands[-1][1]["kw"] - 1e-9))
    for c, p in cands:
        if p["kw"] * n >= kw - 1e-9:
            return c
    return cands[-1][0]


def rate_per_kw(master, brand, typ, module, kw):
    """₹ per kW for a custom-size plant. Admin can set rates in Settings (rate_per_kw JSON, keys like
    "ADROIT|ongrid|STC550" or "ADROIT|ongrid"); otherwise the price per kW of the largest matching standard package."""
    try:
        rates = json.loads(master["settings"].get("rate_per_kw") or "{}")
    except json.JSONDecodeError:
        rates = {}
    for key in (f"{brand}|{typ}|{module}", f"{brand}|{typ}"):
        if rates.get(key):
            return float(rates[key])
    for flt in (lambda p: p["brand"] == brand and p["type"] == typ and p["module"] == module,
                lambda p: p["brand"] == brand and p["type"] == typ,
                lambda p: p["type"] == typ and p["module"] == module):
        cands = [p for p in master["packages"] if flt(p)]
        if cands:
            p = max(cands, key=lambda p: (p["kw"] <= kw, p["kw"]))  # largest package not bigger than the plant
            return p["price"] / p["kw"]
    return None


def fy_of(d: dt.date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def ref_type(brand: str, types: set) -> str:
    if brand == "ARKVOLT":
        return "COM"
    if types == {"ongrid"}:
        return "ONG"
    if types == {"hybrid"}:
        return "HYB"
    return "RES"


def file_name(brand, kw_text, types, customer):
    t = {"ongrid": "OnGrid", "hybrid": "Hybrid"}
    tt = "_".join(t[x] for x in ("ongrid", "hybrid") if x in types)
    nm = "".join(ch if ch.isalnum() else "_" for ch in (customer or "Customer")).strip("_")
    while "__" in nm:
        nm = nm.replace("__", "_")
    b = "Adroit" if brand == "ADROIT" else "Arkvolt"
    return f"{b}_{kw_text.replace(' ', '').replace('/', '_')}_{tt}_Proposal_{nm}.pdf"


def _short_make(m):
    return "Statcon" if "Statcon" in m["make"] else m["make"].split()[0]


def _module_label(m):
    if "Avaada" in m["make"]:
        return "Avaada TOPCon bifacial"
    if "TOPCon" in m["model"]:
        return "Statcon TOPCon bifacial"
    return "Statcon Mono PERC bifacial"


def _half(h):
    """Backup hours rounded down to the half hour: 4.6 → '4.5' (spec §6.11: 5.12 kWh ≈ 4.5 h)."""
    v = math.floor(h * 2) / 2
    return f"{v:g}"


def _kw_txt(x):
    return f"{x:g}"


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------
def compute(inputs: dict, master: dict) -> dict:
    cust = inputs.get("customer", {}) or {}
    b = inputs.get("bill", {}) or {}
    rules = _rules(master["rules"])
    products = master["products"]
    no_bill = bool(b.get("no_bill"))
    tariff_code = b.get("tariff") or ("LT-1" if no_bill else None)
    brand = brand_for(tariff_code, inputs.get("brand_override"))
    out = {"brand": brand, "errors": [], "warnings": [], "hints": [], "options": []}
    if not tariff_code:
        out["errors"].append("Pick the KSEB tariff of the connection first.")
    if tariff_code and tariff_code not in master["tariffs"]:
        out["errors"].append(f"No active tariff table for {tariff_code} — add it in Admin → Tariffs.")
    if out["errors"]:
        return out

    t = _tariff({"code": tariff_code, **master["tariffs"][tariff_code]})
    subsidy = brand == "ADROIT"
    commercial = brand == "ARKVOLT"
    company = master["companies"].get(brand, {})
    out.update(company=company, subsidy=subsidy, commercial=commercial, tariff_code=tariff_code, tariff_name=t.name,
               tariff_version=master["tariffs"][tariff_code].get("valid_from"))

    # consumption
    months = float(b.get("months") or 2)
    cons = None
    opts_in = [o for o in (inputs.get("options") or []) if o.get("kw")][:4]
    out["assumed"] = False
    if no_bill and not b.get("units"):
        if b.get("assumed_units"):
            cons = float(b["assumed_units"])
        elif opts_in:  # use that roughly matches what the largest option generates
            kwps = []
            for o in opts_in:
                m_ = products.get(o.get("module") or "STC550")
                if m_:
                    kwps.append(int(o.get("qty") or E.default_panel_qty(float(o["kw"]), m_["wp"])) * m_["wp"] / 1000)
            if kwps:
                cons = round(max(kwps) * rules.yield_ongrid * 365 * (1 - rules.net_metering_loss) / 12 / 10) * 10
        out["assumed"] = cons is not None
    elif b.get("units"):
        cons = float(b["units"]) / months
    elif b.get("bill_amount"):
        cons = E.units_from_bill_amount(float(b["bill_amount"]) / months, t)
        out["warnings"].append("Monthly units estimated from the bill amount — enter the units for an exact quote.")
    out["cons"] = cons
    if cons is None:
        out["errors"].append("Add an option (step 3) to estimate the use." if no_bill else
                             "Enter the units in the bill (or the bill amount) — or tick 'No KSEB bill' for a standard quote.")
        return out
    out["bill_today_month"] = E.bill(cons, t)
    out["bill_today_year"] = out["bill_today_month"] * 12
    out["suggested_kwp"] = E.suggested_kwp(cons, rules)
    out["cfa_max"] = E.cfa(3, True, rules) if subsidy else 0
    conn_load = float(b.get("connected_load_kw") or 0)

    # options
    max_disc = float(master["settings"].get("max_discount_pct", 5))
    needs_approval = False
    for i, o in enumerate(opts_in):
        hybrid = o.get("type") == "hybrid"
        kw = float(o["kw"])
        mcode = o.get("module") or "STC550"
        m = products.get(mcode)
        if not m:
            out["errors"].append(f"Option {LETTERS[i]}: unknown module {mcode}")
            continue
        icode = o.get("inverter") or auto_inverter(products, kw, hybrid)
        inv = products.get(icode)
        bcode = (o.get("battery") or "MIRAKAL") if hybrid else None
        bat_unit = products.get(bcode) if bcode else None
        if not inv:
            out["errors"].append(f"Option {LETTERS[i]}: no {'hybrid' if hybrid else 'on-grid'} inverter in the catalogue — add one in Admin → Products")
            continue
        # several inverters only when the inverter is left on "Auto" (or a count is given); a model the
        # salesperson picks is one unit, so an undersized pick is caught by the string checks (spec §11.3)
        n_inv = int(o.get("inverter_qty") or (max(1, math.ceil(kw / inv["kw"] - 1e-9)) if not o.get("inverter") else 1))
        n_bat = int(o.get("battery_qty") or n_inv) if hybrid else 0
        bat = {**bat_unit, "kwh": bat_unit["kwh"] * n_bat, "usable_kwh": bat_unit["usable_kwh"] * n_bat} if bat_unit else None
        qty = int(o.get("qty") or 0) or math.ceil(E.default_panel_qty(kw, m["wp"]) / n_inv) * n_inv
        kwp = qty * m["wp"] / 1000
        mod, invo = _module(m), _inverter(inv)
        per_inv = qty // n_inv
        sd = E.string_design(per_inv, mod, invo, kwp / n_inv, subsidy, rules)
        if n_inv > 1:
            sd["text"] = f"{n_inv} inverters, {per_inv} modules each: " + sd["text"]
            if qty % n_inv:
                sd["warnings"].append(f"{qty} modules do not divide evenly across {n_inv} inverters — use a multiple of {n_inv}")
        pkg_price = None
        pkg = next((p for p in master["packages"] if p["id"] == o.get("package_id")), None)
        if pkg:
            pkg_price = pkg["price"]
        rate = None
        if not pkg:  # custom capacity: suggest a price from the per-kW rate
            rate = rate_per_kw(master, brand, "hybrid" if hybrid else "ongrid", mcode, kw)
        suggested = round(rate * kw / 1000) * 1000 if rate else None
        ref_price = pkg_price or suggested
        override = o.get("price_override")
        price = float(override) if override not in (None, "", 0) else (float(o["price"]) if o.get("price") else ref_price)
        overridden = override not in (None, "", 0) and pkg_price is not None and float(override) != pkg_price
        cut = float(override) if overridden else (float(o["price"]) if (not pkg and o.get("price")) else None)
        if cut is not None and ref_price and cut < ref_price * (1 - max_disc / 100):
            needs_approval = True
        opt = dict(letter=LETTERS[i], type="hybrid" if hybrid else "ongrid", hybrid=hybrid, kw=kw, kwp=kwp, qty=qty,
                   module=mcode, module_obj=m, inverter=icode, inverter_obj=inv, battery=bcode, battery_obj=bat,
                   battery_unit=bat_unit, n_inv=n_inv, n_bat=n_bat, rate_per_kw=rate, suggested_price=suggested,
                   string=sd, package=pkg, package_price=pkg_price, price=price, overridden=overridden,
                   make=_short_make(m), module_label=_module_label(m),
                   color=STATCON_BLUE if "Statcon" in m["make"] else AVAADA_GOLD,
                   label=f"{'Hybrid' if hybrid else 'On-grid'} · {_short_make(m)}")
        opt["title"] = f"Option {opt['letter']} · {_kw_txt(kw)} kW {'Hybrid' if hybrid else 'On-grid'} · {opt['module_label']}"
        for e in sd["errors"]:
            out["errors"].append(f"Option {opt['letter']}: {e}")
        for w in sd["warnings"]:
            out["warnings"].append(f"Option {opt['letter']}: {w}")
        if price:
            ps = E.price_split(price, kw, subsidy, rules)
            res = E.project(cons, kw, kwp, hybrid, mod, t, ps["net_cost"], commercial=commercial,
                            battery_usable_kwh=(bat or {}).get("usable_kwh", 0) if hybrid else 0, r=rules,
                            n_inv=n_inv, n_bat=max(n_bat, 1))
            opt.update(split=ps, years=res.years, payback=res.payback, payback_text=E.payback_text(res.payback),
                       total_saving=res.total_saving, net_gain=res.net_gain, saving1=res.years[0]["saving"],
                       gen1=res.years[0]["gen"], used1=res.years[0]["used"],
                       monthly=E.monthly_year1(res.years[0]["gen"], rules))
        else:
            opt["split"] = None
            out["errors"].append(f"Option {opt['letter']}: enter a price (no package or per-kW rate for this system).")
        if hybrid and bat:
            opt["backup_h"] = math.floor(E.backup_hours(bat["kwh"], 1.0, rules) * 2) / 2
        out["options"].append(opt)
    out["needs_approval"] = needs_approval
    if needs_approval:
        out["warnings"].append(f"Price more than {max_disc:g}% below the package / per-kW price — manager approval needed.")
    if not out["options"]:
        out["errors"].append("Add at least one option.")
        return out

    # hints & conditional warnings (§6.10, §8.4)
    kws = sorted({o["kw"] for o in out["options"]})
    gen_max = max((o.get("gen1") or 0) for o in out["options"])
    out["hints"] = E.sizing_hints(cons, max(kws), conn_load, t.domestic, gen_max, rules)
    if conn_load and max(kws) > conn_load:
        out["hints"] = [h for h in out["hints"] if "load extension" not in h]
        out["warnings"].append(f"Plant {max(kws):g} kW is above the connected load {conn_load:g} kW — KSEB load extension needed.")
    if inputs.get("bill", {}).get("rented"):
        out["warnings"].append("Rented premises — owner's consent / NOC needed.")
    tb = b.get("transformer_balance_kw")
    if tb not in (None, ""):
        tb = float(tb)
        out["feasibility"] = {"balance_kw": tb, "ok": tb >= max(kws)}
        if tb < max(kws):
            out["warnings"].append(f"Transformer balance capacity {tb:g} kW is below the plant size {max(kws):g} kW — KSEB may not approve without augmentation.")
    types = {o["type"] for o in out["options"]}
    out["types"] = types
    out["has_hybrid"] = "hybrid" in types
    out["has_ongrid"] = "ongrid" in types
    out["kws"] = kws
    out["kw_text"] = " / ".join(f"{_kw_txt(k)} kW" for k in kws)
    out["kw_main"] = _kw_txt(max(kws))
    out["roof_sqft"] = math.ceil(max(o["kwp"] for o in out["options"]) * rules.roof_sqft_per_kwp / 10) * 10
    priced = [o for o in out["options"] if o.get("split")]
    out["texts"] = build_texts(out, inputs, master, cons, t, rules) if priced else {}
    out["bom"] = build_bom(out)
    out["warranty"] = build_warranty(out)
    out["assumptions"] = build_assumptions(out, inputs, cons, t, rules) if priced else []
    out["datasheets"] = datasheet_list(out)
    out["file_name"] = file_name(brand, out["kw_text"], types, cust.get("name"))
    out["ref_type"] = ref_type(brand, types)
    out["rules"] = rules
    return out


# ---------------------------------------------------------------------------------------------
# Text, BOM, warranty, assumptions
# ---------------------------------------------------------------------------------------------
def build_texts(out, inputs, master, cons, t, rules):
    cust, b = inputs.get("customer", {}) or {}, inputs.get("bill", {}) or {}
    brand, opts = out["brand"], out["options"]
    company = out["company"]
    types = {o["type"] for o in opts}
    kw_text = out["kw_text"]
    if types == {"ongrid"}:
        title = "On-Grid Solar Proposal"
    elif types == {"hybrid"}:
        title = "Hybrid Solar Proposal"
    else:
        title = "Rooftop Solar Proposal"
    makes = []
    for o in opts:
        if o["module_label"] not in makes:
            makes.append(o["module_label"])
    sys_line = {frozenset({"ongrid"}): "On-grid system", frozenset({"hybrid"}): "Hybrid with lithium battery",
                frozenset({"ongrid", "hybrid"}): "On-grid or hybrid with lithium battery"}[frozenset(types)]
    mod_line = " or ".join(dict.fromkeys(m.split()[0] for m in makes)) + (" DCR modules" if out["subsidy"] else " modules")
    subtitle = [sys_line + ",", mod_line + ","]
    subtitle.append("under PM Surya Ghar: Muft Bijli Yojana" if out["subsidy"] else "Commercial rooftop solar")
    home = "home" if out["subsidy"] else "premises"
    bat = next((o["battery_obj"] for o in opts if o["hybrid"] and o.get("battery_obj")), None)
    sub_tail = []
    if "ongrid" in types:
        sub_tail.append("On-grid")
    if "hybrid" in types:
        sub_tail.append(f"Hybrid (with {bat['kwh']:g} kWh Lithium Battery)" if bat else "Hybrid")
    scheme = " under PM Surya Ghar: Muft Bijli Yojana" if out["subsidy"] else ""
    subject = (f"Proposal for a {kw_text} Rooftop Solar PV Power Plant{scheme} — "
               f"{' and '.join(sub_tail)} option{'s' if len(opts) > 1 else ''}")
    consumer = b.get("consumer_no") or "—"
    legal = company.get('legal_name', '')
    basis = f"Based on your KSEB bill (consumer no. {consumer}), w" if (b.get("consumer_no") and not out.get("assumed")) else "W"
    p1 = (f"Thank you for your interest in {legal}{'' if legal.endswith('.') else '.'} {basis}"
          f"e are pleased to submit our offer for a {kw_text} rooftop solar power plant at your {home}.")
    if len(types) == 2 and len(makes) > 1:
        p1 += f" You can choose an on-grid system or a hybrid system with battery, each with {makes[0]} or {makes[1]} modules."
    elif len(types) == 2:
        p1 += " You can choose an on-grid system or a hybrid system with battery."
    elif len(makes) > 1:
        p1 += f" You can choose {makes[0]} or {makes[1]} modules."
    net = "net billing" if out["commercial"] else "the net meter"
    p2 = (f"Solar power first runs your {home}, and the surplus is exported to KSEB through {net}"
          + (" and adjusted against your night-time use." if not out["commercial"] else "."))
    if "hybrid" in types:
        p2 += " The hybrid option also stores solar in a lithium battery and keeps essential loads running within milliseconds when the grid fails."
    if inputs.get("welcome_extra"):
        p2 += " " + inputs["welcome_extra"].strip()
    s1 = [o["saving1"] for o in opts if o.get("saving1")]
    lo, hi = (round(min(s1) / 1000) * 1000, round(max(s1) / 1000) * 1000) if s1 else (0, 0)
    cards = []
    if "hybrid" in types and bat:
        cards.append(("shield", "Backup during power cuts",
                      f"Hybrid options: about {_half(E.backup_hours(bat['kwh'], 1, rules))} hours for a 1 kW essential load from a full battery."))
    if out["subsidy"]:
        cards.append(("rupee", "Government subsidy", f"₹{E.inr(out['cfa_max'] if max(out['kws']) >= 3 else E.cfa(max(out['kws']), True, rules))} credited directly to your bank account."))
    else:
        cards.append(("rupee", "Tax benefit", "40% accelerated depreciation (WDV) for taxable businesses, plus GST input credit."))
    cards.append(("bolt", "Lower electricity bills",
                  f"Year-1 saving of about ₹{E.inr(lo)}" + (f" – ₹{E.inr(hi)}." if hi != lo else ".")))
    cards.append(("home", "Higher self-use of solar",
                  "Stored solar covers your evening peak hours." if "hybrid" in types else "Daytime loads run directly on solar."))
    return dict(title=title, subtitle=subtitle, subject=subject, p1=p1, p2=p2, cards=cards,
                to_lines=_to_lines(cust), saving_lo=lo, saving_hi=hi)


ADDR_LINE_CHARS = 36  # fits the To-block width on the cover and the welcome letter


def _clean(s):
    """Tidy typed text: single spaces, no space before commas, one space after."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = re.sub(r"\s*,\s*", ", ", s)
    return s.strip(" ,")


def _wrap_address(text, width=ADDR_LINE_CHARS):
    """Break an address into lines at commas (then at spaces if one part is still too long)."""
    parts = [p for p in (x.strip() for x in _clean(text).split(",")) if p]
    lines, cur = [], ""
    for p in parts:
        cand = f"{cur}, {p}" if cur else p
        if len(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur + ",")
        if len(p) <= width:
            cur = p
        else:  # a single very long part: wrap on words
            words, cur = p.split(), ""
            for w in words:
                if cur and len(cur) + 1 + len(w) > width:
                    lines.append(cur)
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _to_lines(c):
    """Customer block: name, then the address wrapped to fit, then city + PIN (max 6 lines)."""
    if c.get("blank"):
        return []
    name = _clean(" ".join(x for x in (c.get("salutation"), c.get("name")) if x))
    lines = [name] if name else []
    addr = ", ".join(_clean(c.get(k)) for k in ("addr1", "addr2") if _clean(c.get(k)))
    lines += _wrap_address(addr)
    cp = " – ".join(x for x in (_clean(c.get("city")), _clean(c.get("pin"))) if x)
    if cp:
        if lines and len(lines) > 1 and len(lines[-1]) + 2 + len(cp) <= ADDR_LINE_CHARS:
            lines[-1] = f"{lines[-1].rstrip(',')}, {cp}"
        else:
            if len(lines) > 1:
                lines[-1] = lines[-1].rstrip(",") + ","
            lines.append(cp)
    return lines[:6]


def build_bom(out):
    from .catalog import BOS
    opts = out["options"]
    rows = []

    def per_opt(fn):
        return [fn(o) for o in opts]

    for code in dict.fromkeys(o["module"] for o in opts):
        m = next(o["module_obj"] for o in opts if o["module"] == code)
        rows.append(["Solar PV module", m["make"], m.get("short") or m["model"],
                     per_opt(lambda o: f"{o['qty']} Nos." if o["module"] == code else "—")])
    for code in dict.fromkeys(o["inverter"] for o in opts):
        inv = next(o["inverter_obj"] for o in opts if o["inverter"] == code)
        rows.append(["Hybrid inverter" if inv["hybrid"] else "On-grid inverter", inv["make"], f"{inv['model']} — {inv.get('short', '')}",
                     per_opt(lambda o: (f"{o['n_inv']} No{'s' if o['n_inv'] > 1 else ''}." if o["inverter"] == code else "—"))])
    for code in dict.fromkeys(o["battery"] for o in opts if o["battery"]):
        bat = next(o["battery_unit"] for o in opts if o["battery"] == code)
        rows.append(["Lithium battery", bat["make"], bat.get("short") or bat["model"],
                     per_opt(lambda o: (f"{o['n_bat']} No{'s' if o['n_bat'] > 1 else ''}." if o["battery"] == code else "—"))])
    has_h = any(o["hybrid"] for o in opts)
    for item, make, spec, qty, hyb_only in BOS:
        if hyb_only and not has_h:
            continue
        make = make.replace("Adroit", out["company"].get("short_name", "Adroit"))
        rows.append([item, make, spec, per_opt(lambda o: ("—" if hyb_only and not o["hybrid"] else qty))])
    return rows


def build_warranty(out):
    seen, cards = set(), []
    for o in out["options"]:
        for key, obj, label in (("module", o["module_obj"], "modules"), ("inverter", o["inverter_obj"], "inverter"),
                                ("battery", o["battery_obj"], "battery")):
            if not obj or (key, obj["code"]) in seen:
                continue
            seen.add((key, obj["code"]))
            letters = ", ".join(x["letter"] for x in out["options"] if x.get(key) == obj["code"])
            name = f"{_short_make(obj)} {label}" if key != "inverter" else f"{'Hybrid' if obj['hybrid'] else 'On-grid'} inverter"
            cards.append((f"{name} ({letters})", obj.get("warranty", "")))
    cards.append(("Balance of system & workmanship", "5 years from commissioning"))
    cards.append(("Free maintenance visits", "5 years from commissioning"))
    return cards


def build_assumptions(out, inputs, cons, t, rules):
    b = inputs.get("bill", {}) or {}
    opts = out["options"]
    bul = []
    if out.get("assumed"):
        bul.append(("Consumption", f"No KSEB bill used — savings assume about {E.inr(cons)} units a month (roughly what the plant generates), "
                    "held constant for 25 years. We will rework the savings from your actual bill."))
    else:
        bul.append(("Consumption", f"{E.inr(cons)} units a month (from the KSEB bill{', consumer no. ' + b['consumer_no'] if b.get('consumer_no') else ''}), held constant for 25 years."))
    bul.append(("Tariff", f"KSEB {t.name} tariff {out.get('tariff_version', '')[:4]}–27, energy charge plus 10% electricity duty; "
                f"today's bill for this use about ₹{E.inr(out['bill_today_month'])}/month. Fixed charges are not counted in savings. "
                f"Tariff assumed to rise {rules.escalation * 100:g}% a year."))
    bul.append(("Generation", f"{rules.yield_ongrid:g} units per kWp per day for on-grid and {rules.yield_hybrid:g} for hybrid (battery losses)."))
    degr = []
    for code in dict.fromkeys(o["module"] for o in opts):
        m = next(o["module_obj"] for o in opts if o["module"] == code)
        degr.append(f"{_module_label(m)} {m['d1'] * 100:g}% in year 1, then {m['dn'] * 100:g}%/yr")
    bul.append(("Module degradation", "; ".join(degr) + "."))
    if out["commercial"]:
        bul.append(("Net billing", f"{rules.daytime_share * 100:g}% of use assumed in daytime and met directly by solar; surplus exported at ₹{rules.export_rate:g}/unit (KSERC RE Regulations 2025)."))
    else:
        bul.append(("Net metering", f"{rules.net_metering_loss * 100:g}% of generation assumed lost in banking and adjustment; surplus units at settlement paid at ₹{rules.export_rate:g} (assumed, held flat)."))
    reps = []
    if out["has_ongrid"]:
        reps.append(f"on-grid inverter in year {rules.inverter_repl_year} (₹{E.inr(rules.inverter_repl_small)} up to 3 kW, ₹{E.inr(rules.inverter_repl_large)} above)")
    if out["has_hybrid"]:
        reps.append(f"lithium battery in years {' and '.join(str(y) for y in rules.battery_repl_years)} (₹{E.inr(rules.battery_repl_cost)} each)")
    bul.append(("Replacements", "; ".join(reps) + "."))
    clar = []
    if out["subsidy"]:
        clar.append(("Subsidy", f"PM Surya Ghar CFA is ₹30,000 per kW for the first 2 kW and ₹18,000 for the 3rd kW, up to ₹78,000 for 3 kW and above. "
                     "Eligible with DCR modules on an individual LT-1 domestic connection; credited to the customer's bank account after commissioning and portal approval."))
    else:
        clar.append(("Subsidy", "Not eligible for PM Surya Ghar (non-domestic connection); prices are without subsidy. "
                     "Taxable businesses can claim 40% accelerated depreciation (WDV) on the plant."))
    kws = out["kws"]
    conn = float(b.get("connected_load_kw") or 0)
    if conn and max(kws) > conn:
        clar.append(("Connected load", f"A {max(kws):g} kW plant needs connected load of at least {max(kws):g} kW; current load {conn:g} kW — load extension is needed, charges extra at actuals."))
    else:
        clar.append(("Connected load", "KSEB feasibility depends on the connected load and transformer capacity."))
    if out.get("feasibility"):
        f = out["feasibility"]
        clar.append(("Transformer capacity", f"KSEB shows {f['balance_kw']:g} kW of balance renewable capacity on the distribution transformer"
                     + (" — sufficient for this plant." if f["ok"] else " — less than the plant size; KSEB may need to augment the transformer.")))
    clar.append(("Roof", f"Shadow-free roof of about {out.get('roof_sqft') or 0:.0f} sq. ft. needed; sheet roofs and elevated structures are quoted at site visit."))
    if out["has_hybrid"]:
        bat = next(o["battery_obj"] for o in opts if o["hybrid"])
        clar.append(("Hybrid backup", f"About {_half(E.backup_hours(bat['kwh'], 1, rules))} hours at a 1 kW essential load from a full {bat['kwh']:g} kWh battery. "
                     "Essential loads are wired to the backup output; separation of loads in house wiring is quoted separately."))
    if b.get("rented"):
        clar.append(("Ownership", "Premises are rented — the owner's written consent / NOC is needed for KSEB and subsidy."))
    else:
        clar.append(("Ownership", "The electricity connection should be in the applicant's name; the owner's consent is needed for rented premises."))
    if b.get("second_connection"):
        clar.append(("Second connection", "The other connection on the bill is not covered by this plant; it can be quoted separately."))
    if b.get("current_month_amount") and out.get("bill_today_month"):
        cur = float(b["current_month_amount"])
        if abs(cur - out["bill_today_month"]) / out["bill_today_month"] > 0.3:
            clar.append(("Bill basis", f"Your latest bill (₹{E.inr(cur)}/month) differs by more than 30% from the average used; savings use the stated average."))
    for x in inputs.get("clarifications") or []:
        if x and x.strip():
            clar.append(("Note", x.strip()))
    return {"basis": bul, "clar": clar}


def datasheet_list(out):
    files = []
    for key in ("module", "inverter", "battery"):
        for o in out["options"]:
            obj = o.get(f"{key}_obj")
            if obj and obj.get("datasheet") and obj["datasheet"] not in files:
                files.append(obj["datasheet"])
    return files


def summary_json(out) -> dict:
    """Small, JSON-safe summary for the wizard's live panel and for storing with a version."""
    if not out.get("options"):
        return {k: out.get(k) for k in ("brand", "errors", "warnings", "hints", "subsidy", "cons", "bill_today_month",
                                        "suggested_kwp", "cfa_max", "feasibility", "assumed")}
    return dict(
        brand=out["brand"], subsidy=out.get("subsidy"), cons=out.get("cons"), bill_today_month=out.get("bill_today_month"), assumed=out.get("assumed"),
        suggested_kwp=out.get("suggested_kwp"), cfa_max=out.get("cfa_max"), errors=out["errors"], warnings=out["warnings"],
        hints=out["hints"], needs_approval=out.get("needs_approval"), feasibility=out.get("feasibility"),
        kw_text=out.get("kw_text"), file_name=out.get("file_name"),
        options=[dict(letter=o["letter"], label=o["label"], title=o["title"], kw=o["kw"], kwp=o["kwp"], qty=o["qty"],
                      module=o["module"], inverter=o["inverter"], battery=o["battery"], string=o["string"],
                      package_price=o["package_price"], price=o["price"], overridden=o["overridden"],
                      suggested_price=o.get("suggested_price"), rate_per_kw=o.get("rate_per_kw"), n_inv=o.get("n_inv"), n_bat=o.get("n_bat"),
                      split=o.get("split"), payback=o.get("payback"), payback_text=o.get("payback_text"),
                      saving1=o.get("saving1"), gen1=o.get("gen1"), total_saving=o.get("total_saving"),
                      net_gain=o.get("net_gain")) for o in out["options"]])


# ---------------------------------------------------------------------------------------------
# Advisor: what to recommend from a bill (feasibility helper for sales)
# ---------------------------------------------------------------------------------------------
def advise(bill: dict, master: dict) -> dict:
    tariff = bill.get("tariff")
    brand = brand_for(tariff)
    if not brand or tariff not in master["tariffs"]:
        return {"brand": brand, "error": "Tariff not recognised — pick it manually."}
    t = _tariff({"code": tariff, **master["tariffs"][tariff]})
    rules = _rules(master["rules"])
    months = float(bill.get("months") or 2)
    cons = float(bill["units"]) / months if bill.get("units") else None
    if not cons:
        return {"brand": brand, "error": "No consumption found."}
    sug = E.suggested_kwp(cons, rules)
    std = [p for p in master["packages"] if p["brand"] == brand]
    kw_opts = sorted({p["kw"] for p in std}) or [3, 5]
    # smallest standard plant that covers the use (allow 10% short), domestic minimum 3 kW for full subsidy
    target = next((k for k in kw_opts if k * 1.1 >= sug * 0.9), kw_opts[-1])
    if brand == "ADROIT":
        target = max(target, 3)
    conn = float(bill.get("connected_load_kw") or 0)
    rows = []
    for p in std:
        inputs = {"bill": {**bill, "tariff": tariff}, "options": [{"type": p["type"], "kw": p["kw"], "module": p["module"], "qty": p["qty"],
                                                                     "inverter": p["inverter"], "battery": p["battery"], "package_id": p["id"]}]}
        r = compute(inputs, master)
        if r.get("options") and r["options"][0].get("split"):
            o = r["options"][0]
            rows.append(dict(package_id=p["id"], name=p["name"], kw=p["kw"], type=p["type"], price=o["price"],
                             net_cost=o["split"]["net_cost"], saving1=o["saving1"], payback=o["payback"],
                             payback_text=o["payback_text"], net_gain=o["net_gain"], errors=r["errors"]))
    best = sorted([r for r in rows if r["kw"] == target and r["type"] == "ongrid" and not r["errors"]], key=lambda r: (r["payback"] or 99))
    best_h = sorted([r for r in rows if r["kw"] == target and r["type"] == "hybrid" and not r["errors"]], key=lambda r: (r["payback"] or 99))
    night = None
    z = bill.get("units_by_zone") or {}
    if z and sum(z.values()):
        night = (z.get("Off Peak", 0) + z.get("Peak", 0)) / sum(z.values())
    reasons = [f"Uses about {E.inr(cons)} units a month → about {sug:.1f} kWp of panels covers it."]
    if brand == "ADROIT":
        reasons.append("LT-1 domestic → Adroit Universe with PM Surya Ghar subsidy (₹78,000 at 3 kW and above).")
    else:
        reasons.append("Non-domestic tariff → Arkvolt Energy, no subsidy; net billing; 40% WDV for taxable businesses.")
    if night is not None and night > 0.5:
        reasons.append(f"{night * 100:.0f}% of use is in the evening/night — a hybrid with battery adds backup and covers peak hours.")
    if conn and target > conn:
        reasons.append(f"Connected load {conn:g} kW is below {target:g} kW — KSEB load extension needed.")
    return dict(brand=brand, cons=cons, suggested_kwp=sug, target_kw=target, bill_today_month=E.bill(cons, t), rows=rows,
                recommend=(best[0] if best else None), recommend_hybrid=(best_h[0] if best_h else None), reasons=reasons,
                night_share=night)
