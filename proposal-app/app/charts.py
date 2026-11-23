"""Server-side SVG charts (spec §9.3) — sharp in the PDF, no JS needed."""
from __future__ import annotations

from .engine import MONTHS, inr

FONT = "font-family=\"Poppins, 'Plus Jakarta Sans', sans-serif\""


def monthly_generation(out, w=505, h=168, accent_line="#e5484d", muted="#5b6b7c", grid="#e1e8ef"):
    """Grouped bars per panel make (year 1), dashed line at monthly use (§9.3)."""
    series, seen = [], set()
    for o in out["options"]:
        key = (o["module"], o["hybrid"])
        if o["module"] in seen or not o.get("monthly"):
            continue
        seen.add(o["module"])
        letters = ", ".join(x["letter"] for x in out["options"] if x["module"] == o["module"])
        series.append((f"{o['make']} · {o['kwp']:g} kWp (Options {letters})", o["monthly"], o["color"]))
    if not series:
        return ""
    cons = out["cons"]
    top = max(max(max(s[1]) for s in series), cons) * 1.15
    left, bottom, right, ttop = 34, h - 22, w - 6, 22
    ph = bottom - ttop
    gw = (right - left) / 12
    bw = gw * 0.78 / len(series)
    y = lambda v: bottom - v / top * ph  # noqa: E731
    o = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" {FONT}>']
    step = _nice(top / 4)
    v = 0
    while v <= top:
        o.append(f'<line x1="{left}" x2="{right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{grid}" stroke-width=".6"/>'
                 f'<text x="{left - 4}" y="{y(v) + 3:.1f}" font-size="6.5" fill="{muted}" text-anchor="end">{inr(v)}</text>')
        v += step
    for i in range(12):
        gx = left + i * gw + gw * 0.11
        for j, (_, vals, col) in enumerate(series):
            yy = y(vals[i])
            o.append(f'<rect x="{gx + j * bw:.1f}" y="{yy:.1f}" width="{bw - 0.8:.1f}" height="{bottom - yy:.1f}" fill="{col}" rx=".8"/>')
        o.append(f'<text x="{left + i * gw + gw / 2:.1f}" y="{bottom + 10}" font-size="7" fill="{muted}" text-anchor="middle">{MONTHS[i]}</text>')
    yc = y(cons)
    o.append(f'<line x1="{left}" x2="{right}" y1="{yc:.1f}" y2="{yc:.1f}" stroke="{accent_line}" stroke-width="1" stroke-dasharray="4 3"/>')
    o.append(f'<rect x="{right - 92}" y="{yc - 12:.1f}" width="90" height="10" rx="2" fill="#fff"/>'
             f'<text x="{right - 4}" y="{yc - 4:.1f}" font-size="6.8" fill="{accent_line}" text-anchor="end" font-weight="600">Use ≈ {inr(cons)} units/month</text>')
    lx = left
    for name, _, col in series:
        o.append(f'<rect x="{lx}" y="4" width="8" height="8" rx="1.5" fill="{col}"/><text x="{lx + 11}" y="11" font-size="7" fill="{muted}">{name}</text>')
        lx += 22 + len(name) * 3.6
    o.append("</svg>")
    return "".join(o)


def cumulative(out, w=505, h=182, muted="#5b6b7c", grid="#e1e8ef"):
    """One line per option: Statcon blue / Avaada gold, on-grid solid / hybrid dashed, end labels (§9.3)."""
    opts = [o for o in out["options"] if o.get("years")]
    if not opts:
        return ""
    vals = [v for o in opts for v in [-o["split"]["net_cost"]] + [y["cum"] for y in o["years"]]]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = hi - lo or 1
    lo -= span * 0.05
    hi += span * 0.08
    left, right, ttop, bottom = 40, w - 52, 26, h - 20
    x = lambda n: left + (right - left) * n / 25  # noqa: E731
    y = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - ttop)  # noqa: E731
    o = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" {FONT}>']
    step = _nice((hi - lo) / 5)
    v = (lo // step) * step
    while v <= hi:
        if v >= lo:
            o.append(f'<line x1="{left}" x2="{right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{grid}" stroke-width=".6"/>'
                     f'<text x="{left - 4}" y="{y(v) + 3:.1f}" font-size="6.5" fill="{muted}" text-anchor="end">₹{v / 1e5:g} L</text>')
        v += step
    o.append(f'<line x1="{left}" x2="{right}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#9aa6b2" stroke-width=".9"/>')
    for n in range(0, 26, 5):
        o.append(f'<text x="{x(n):.1f}" y="{bottom + 11}" font-size="7" fill="{muted}" text-anchor="middle">{n}</text>')
    labels = []
    for opt in opts:
        pts = [(x(0), y(-opt["split"]["net_cost"]))] + [(x(r["n"]), y(r["cum"])) for r in opt["years"]]
        d = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        dash = ' stroke-dasharray="5 3"' if opt["hybrid"] else ""
        o.append(f'<path d="{d}" fill="none" stroke="{opt["color"]}" stroke-width="1.6"{dash}/>')
        labels.append([pts[-1][1], opt])
    labels.sort(key=lambda t: t[0])
    for i in range(1, len(labels)):  # keep end labels apart
        if labels[i][0] - labels[i - 1][0] < 9:
            labels[i][0] = labels[i - 1][0] + 9
    for yy, opt in labels:
        o.append(f'<text x="{right + 4}" y="{yy + 2.5:.1f}" font-size="7" font-weight="600" fill="{opt["color"]}">{opt["letter"]} ₹{opt["net_gain"] / 1e5:.1f} L</text>')
    lx, ly = left, 8
    for opt in opts:
        dash = ' stroke-dasharray="5 3"' if opt["hybrid"] else ""
        name = f"{opt['letter']}: {opt['label']} · payback {opt['payback_text']}"
        o.append(f'<line x1="{lx}" x2="{lx + 14}" y1="{ly}" y2="{ly}" stroke="{opt["color"]}" stroke-width="1.6"{dash}/>'
                 f'<text x="{lx + 17}" y="{ly + 2.5}" font-size="6.6" fill="{muted}">{name}</text>')
        ly += 8.5
        if ly > 26:
            ly, lx = 8, lx + 230
    o.append(f'<text x="{(left + right) / 2:.0f}" y="{h - 2}" font-size="6.5" fill="{muted}" text-anchor="middle">Year</text>')
    o.append("</svg>")
    return "".join(o)


def _nice(x):
    import math
    if x <= 0:
        return 1
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * e:
            return m * e
    return 10 * e
