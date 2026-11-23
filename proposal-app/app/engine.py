"""Calculation engine (spec §6). Pure functions, no UI or database code.

All money is kept unrounded; round only for display. Inputs are plain dicts / dataclasses so the same
module runs in the live wizard preview, the PDF and the tests (tests/test_engine.py, spec §11).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------
# Tariffs (§5.4). Each slab list is [(upper_limit_units, rate)], last upper limit = inf.
# ---------------------------------------------------------------------------------------------
INF = float("inf")


@dataclass
class Tariff:
    code: str                      # "LT-1", "LT-7A", "LT-6F"
    name: str
    telescopic: list               # slabs billed telescopically (LT-1 up to 250 units); [] if none
    telescopic_upto: float         # telescopic applies when u <= this
    flat: list                     # non-telescopic slabs: all units at the rate of the slab u falls in
    duty: float = 0.10             # electricity duty on energy charge
    fixed_per_kw: float = 0.0      # informational; fixed charges are NOT counted in savings
    domestic: bool = False


def default_tariffs() -> dict:
    """KSEB 2025-27 tables from spec §5.4 (admin can version/replace them in the database)."""
    return {
        "LT-1": Tariff("LT-1", "LT-1 Domestic",
                       telescopic=[(50, 3.35), (100, 4.25), (150, 5.35), (200, 7.20), (250, 8.50)], telescopic_upto=250,
                       flat=[(300, 6.75), (350, 7.60), (400, 7.95), (500, 8.25), (INF, 9.20)], domestic=True),
        "LT-7A": Tariff("LT-7A", "LT-7A Commercial", telescopic=[], telescopic_upto=0,
                        flat=[(100, 6.05), (200, 6.80), (300, 7.50), (500, 8.15), (INF, 9.40)]),
        # LT-6F: spec gives only the first and last slab; middle slabs to be completed by admin.
        "LT-6F": Tariff("LT-6F", "LT-6F", telescopic=[], telescopic_upto=0,
                        flat=[(100, 6.00), (200, 6.80), (300, 7.50), (500, 8.15), (INF, 9.25)], fixed_per_kw=195),
    }


def _rate(slabs, u):
    for upper, rate in slabs:
        if u <= upper:
            return rate
    return slabs[-1][1]


def bill(u: float, t: Tariff) -> float:
    """₹ per month: energy charge + duty (§6.2). Fixed charges excluded."""
    u = max(0.0, u)
    if t.telescopic and u <= t.telescopic_upto:
        energy, lo = 0.0, 0.0
        for upper, rate in t.telescopic:
            if u <= lo:
                break
            energy += (min(u, upper) - lo) * rate
            lo = upper
    else:
        energy = u * _rate(t.flat, u)
    return energy * (1 + t.duty)


def units_from_bill_amount(amount: float, t: Tariff) -> float:
    """Back-solve monthly units from a monthly bill amount (estimate; §6.1)."""
    lo, hi = 0.0, 5000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if bill(mid, t) < amount:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------------------------
# Constants (§5.5) — admin-editable; this is the default set.
# ---------------------------------------------------------------------------------------------
@dataclass
class Rules:
    cfa_first2_per_kw: float = 30000
    cfa_third_kw: float = 18000
    cfa_max: float = 78000
    kseb_per_kw: float = 1000
    kseb_gst: float = 0.18
    gst_divisor: float = 1.089            # 70% supply @5% + 30% service @18%
    export_rate: float = 2.79
    net_metering_loss: float = 0.07
    yield_ongrid: float = 4.0             # units / kWp / day
    yield_hybrid: float = 3.9
    escalation: float = 0.03
    inverter_repl_year: int = 12
    inverter_repl_small: float = 30000    # plant <= 3 kW
    inverter_repl_large: float = 40000
    battery_repl_years: tuple = (10, 20)
    battery_repl_cost: float = 50000
    daytime_share: float = 0.60           # commercial net billing
    battery_round_trip: float = 0.90
    payment_schedule: tuple = (0.80, 0.10, 0.10)
    monthly_profile: tuple = (715, 703, 803, 728, 665, 498, 502, 565, 607, 602, 571, 652)
    co2_per_unit: float = 0.71
    roof_sqft_per_kwp: float = 90
    offer_validity_days: int = 30
    min_cell_temp: float = 15             # Kerala, for Voc cold
    years: int = 25


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------------------------
# Products (§5.2)
# ---------------------------------------------------------------------------------------------
@dataclass
class Module:
    code: str
    make: str
    model: str
    wp: float
    vmp: float
    imp: float
    voc: float
    isc: float
    eff: float
    beta_voc: float          # %/°C (negative)
    d1: float                # year-1 degradation (fraction)
    dn: float                # yearly degradation after year 1
    dcr: bool = True
    short: str = ""
    warranty: str = ""


@dataclass
class Inverter:
    code: str
    make: str
    model: str
    kw: float
    hybrid: bool
    max_dc_v: float
    mppt_min: float
    mppt_max: float
    n_mppt: int
    max_a_per_mppt: float
    max_pv_kw: float
    short: str = ""
    warranty: str = ""


@dataclass
class Battery:
    code: str
    make: str
    model: str
    kwh: float
    usable_kwh: float
    short: str = ""
    warranty: str = ""


# ---------------------------------------------------------------------------------------------
# §6.3 / §6.4 subsidy and price split
# ---------------------------------------------------------------------------------------------
def cfa(kw: float, subsidy: bool, r: Rules = Rules()) -> float:
    if not subsidy:
        return 0.0
    v = min(kw, 2) * r.cfa_first2_per_kw + max(0.0, min(kw, 3) - 2) * r.cfa_third_kw
    return min(v, r.cfa_max)


def kseb_charges(kw: float, r: Rules = Rules()) -> float:
    return r.kseb_per_kw * kw * (1 + r.kseb_gst)


def price_split(price: float, kw: float, subsidy: bool, r: Rules = Rules()) -> dict:
    epc = round(price / r.gst_divisor)
    k = kseb_charges(kw, r)
    c = cfa(kw, subsidy, r)
    return {
        "price": price, "epc": epc, "gst": price - epc, "kseb": k, "cfa": c,
        "net_cost": price + k - c,
        "schedule": [price * s for s in r.payment_schedule],
        "words": amount_in_words(price),
    }


# ---------------------------------------------------------------------------------------------
# Amount in words, Indian system (§6.4)
# ---------------------------------------------------------------------------------------------
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _three(n):
    h, rest = divmod(n, 100)
    out = []
    if h:
        out.append(_ONES[h] + " Hundred")
    if rest:
        out.append(_two(rest))
    return " ".join(out)


def amount_in_words(amount: float) -> str:
    n = int(round(amount))
    if n == 0:
        return "Rupees Zero only"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(_three(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return "Rupees " + " ".join(parts) + " only"


def inr(x: float, decimals: int = 0) -> str:
    """Indian digit grouping: 1,30,540 (§8.5)."""
    neg = x < 0
    x = abs(x)
    s = f"{x:.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def lakh(x: float) -> str:
    return f"₹{x / 100000:.1f} L"


# ---------------------------------------------------------------------------------------------
# §6.5 generation
# ---------------------------------------------------------------------------------------------
def gen_year(kwp: float, hybrid: bool, m: Module, n: int, r: Rules = Rules()) -> float:
    y = r.yield_hybrid if hybrid else r.yield_ongrid
    return kwp * y * 365 * (1 - m.d1 - m.dn * (n - 1))


def monthly_year1(total: float, r: Rules = Rules()) -> list:
    """Split year-1 generation by the Kerala profile; whole units, March absorbs rounding (§6.5)."""
    p = r.monthly_profile
    s = sum(p)
    months = [round(total * v / s) for v in p]
    months[2] += round(total) - sum(months)
    return months


# ---------------------------------------------------------------------------------------------
# §6.6 / §6.7 savings, §6.8 payback
# ---------------------------------------------------------------------------------------------
def replacement(n: int, kw: float, hybrid: bool, r: Rules = Rules(), n_inv: int = 1, n_bat: int = 1) -> float:
    """Replacement cost in year n. Plants built from several inverters / batteries replace each unit."""
    if hybrid:
        return r.battery_repl_cost * n_bat if n in r.battery_repl_years else 0.0
    if n == r.inverter_repl_year:
        per = kw / max(n_inv, 1)
        return (r.inverter_repl_small if per <= 3 else r.inverter_repl_large) * n_inv
    return 0.0


def saving_residential(n, cons, gen, t: Tariff, r: Rules = Rules()) -> dict:
    usable = gen * (1 - r.net_metering_loss)
    net_units = max(0.0, cons * 12 - usable) / 12
    surplus = max(0.0, usable - cons * 12)
    s = (bill(cons, t) - bill(net_units, t)) * 12 * (1 + r.escalation) ** (n - 1) + surplus * r.export_rate
    return {"saving": s, "used": min(usable, cons * 12), "export": surplus}


def saving_commercial(n, cons, gen, t: Tariff, battery_usable_kwh: float, hybrid: bool, r: Rules = Rules()) -> dict:
    """Net billing (§6.7). Battery: charged from midday surplus (up to usable capacity × 30 days),
    delivers charge × round-trip efficiency in the evening; what is charged is not exported.
    Reproduces §11.2 (used on site 5,396; saving 65,682)."""
    G = gen / 12
    direct = min(G, cons * r.daytime_share)
    rem = G - direct
    if hybrid:
        charge = min(battery_usable_kwh * 30, rem, max(0.0, cons - direct) / r.battery_round_trip)
        delivered = charge * r.battery_round_trip
    else:
        charge = delivered = 0.0
    export = rem - charge
    imp = max(0.0, cons - direct - delivered)
    s = ((bill(cons, t) - bill(imp, t)) * (1 + r.escalation) ** (n - 1) + export * r.export_rate) * 12
    return {"saving": s, "used": (direct + delivered) * 12, "export": export * 12}


@dataclass
class OptionResult:
    years: list = field(default_factory=list)   # per year dict: n, gen, saving, replacement, cum
    payback: float | None = None
    total_saving: float = 0.0
    net_gain: float = 0.0


def project(cons, kw, kwp, hybrid, m: Module, t: Tariff, net_cost, commercial=False,
            battery_usable_kwh=0.0, r: Rules = Rules(), n_inv: int = 1, n_bat: int = 1) -> OptionResult:
    res = OptionResult()
    cum = -net_cost
    prev_cum = cum
    bill_today = bill(cons, t) * 12
    for n in range(1, r.years + 1):
        g = gen_year(kwp, hybrid, m, n, r)
        if commercial:
            s = saving_commercial(n, cons, g, t, battery_usable_kwh, hybrid, r)
        else:
            s = saving_residential(n, cons, g, t, r)
        rep = replacement(n, kw, hybrid, r, n_inv, n_bat)
        prev_cum = cum
        cum = cum + s["saving"] - rep
        if res.payback is None and cum >= 0:
            res.payback = (n - 1) + (-prev_cum) / (s["saving"] - rep)
        res.years.append({"n": n, "gen": g, "saving": s["saving"], "used": s["used"], "export": s["export"],
                          "replacement": rep, "cum": cum,
                          "bill_without": bill_today * (1 + r.escalation) ** (n - 1)})
        res.total_saving += s["saving"]
    res.net_gain = cum
    return res


def payback_text(p) -> str:
    return "Over 25 years" if p is None else f"{p:.1f} yrs"


# ---------------------------------------------------------------------------------------------
# §6.9 string design
# ---------------------------------------------------------------------------------------------
def string_design(n_panels: int, m: Module, inv: Inverter, kwp: float, subsidy: bool, r: Rules = Rules()) -> dict:
    """Pick a layout and check it. Returns {layout, text, errors[], warnings[]}."""
    k_cold = 1 + abs(m.beta_voc) / 100 * (25 - r.min_cell_temp)
    window_min, window_max = inv.mppt_min, inv.mppt_max

    def check(n_series, strings):
        voc = n_series * m.voc
        voc_cold = voc * k_cold
        vmp = n_series * m.vmp
        errs = []
        if voc_cold > inv.max_dc_v:
            errs.append(f"Voc cold {voc_cold:.0f} V exceeds inverter max {inv.max_dc_v:.0f} V")
        if not (window_min <= vmp <= window_max):
            errs.append(f"String Vmp {vmp:.0f} V outside MPPT window {window_min:.0f}–{window_max:.0f} V")
        return voc, voc_cold, vmp, errs

    layouts = [(n_panels, 1)]
    if inv.n_mppt >= 2 and n_panels % 2 == 0:
        layouts.append((n_panels // 2, 2))          # one string per MPPT (§6.9 string split)
    chosen = None
    for ns, strings in layouts:
        voc, voc_cold, vmp, errs = check(ns, strings)
        if not errs:
            chosen = (ns, strings, voc, voc_cold, vmp, [])
            break
        if chosen is None:
            chosen = (ns, strings, voc, voc_cold, vmp, errs)
    ns, strings, voc, voc_cold, vmp, errors = chosen
    warnings = []
    imp_str = m.imp  # one string per MPPT in every layout we pick
    if imp_str > inv.max_a_per_mppt:
        warnings.append(f"Input limited to {inv.max_a_per_mppt:.0f} A (string Imp {imp_str:.1f} A)")
    if kwp > inv.max_pv_kw + 1e-9:
        warnings.append(f"DC {kwp:.2f} kWp above inverter max recommended PV {inv.max_pv_kw:g} kW")
    if subsidy and not m.dcr:
        errors.append("Subsidy (PM Surya Ghar) proposals need DCR modules")
    where = "on MPPT" if strings == 1 else "on MPPT 1 & 2"
    text = (f"{strings} × {ns} {where} · Voc {voc:.0f} V (≈ {voc_cold:.0f} V cold; max {inv.max_dc_v:.0f} V) · "
            f"Vmp {vmp:.0f} V · Imp {imp_str:.1f} A")
    return {"series": ns, "strings": strings, "voc": voc, "voc_cold": voc_cold, "vmp": vmp, "imp": imp_str,
            "text": text, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------------------------
# §6.10 sizing helper, §6.11 backup
# ---------------------------------------------------------------------------------------------
def suggested_kwp(cons: float, r: Rules = Rules()) -> float:
    return cons * 12 / (r.yield_ongrid * 365 * (1 - r.net_metering_loss))


def default_panel_qty(kw: float, wp: float) -> int:
    return math.ceil(kw * 1.1 * 1000 / wp)


def backup_hours(battery_kwh: float, load_kw: float = 1.0, r: Rules = Rules()) -> float:
    return battery_kwh * r.battery_round_trip / load_kw


def sizing_hints(cons, plant_kw, connected_load_kw, domestic, gen_y1=None, r: Rules = Rules()) -> list:
    hints = []
    s = suggested_kwp(cons, r)
    if domestic and s < 3:
        hints.append("3 kW gets the full ₹78,000 subsidy; surplus units earn only ₹2.79.")
    if plant_kw and connected_load_kw and plant_kw > connected_load_kw:
        hints.append("KSEB load extension needed — charges extra at actuals.")
    if gen_y1 and gen_y1 > 1.3 * cons * 12:
        hints.append("Oversized: most surplus is sold at ₹2.79.")
    return hints
