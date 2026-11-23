"""One-off: build the fixed Adroit artwork from the approved reference proposal, so generated PDFs match it.
python tools/extract_assets.py "F:/Adroit_5kW_OnGrid_Hybrid_Solar_Proposal.pdf"

Writes app/static/brand/adroit/*.svg|png and data/datasheets/*.pdf (fixed product datasheets + contact page).
Text that changes per proposal is removed here and re-drawn by the HTML templates.
"""
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "brand" / "adroit"
DS = ROOT / "data" / "datasheets"
OUT.mkdir(parents=True, exist_ok=True)
DS.mkdir(parents=True, exist_ok=True)
src = fitz.open(sys.argv[1] if len(sys.argv) > 1 else "F:/Adroit_5kW_OnGrid_Hybrid_Solar_Proposal.pdf")

KEEP_IMG, TXT = fitz.PDF_REDACT_IMAGE_NONE, fitz.PDF_REDACT_TEXT_REMOVE


def single(pno):
    d = fitz.open()
    d.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
    return d, d[0]


def redact(page, rects, graphics=fitz.PDF_REDACT_LINE_ART_NONE):
    for r in rects:
        page.add_redact_annot(fitz.Rect(r))
    page.apply_redactions(images=KEEP_IMG, graphics=graphics, text=TXT)


def spans(page, *needles):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if any(n in s["text"] for n in needles):
                    out.append(fitz.Rect(s["bbox"]) + (-1, -1, 1, 1))
    return out


def save_svg(page, name, clip=None):
    if clip:
        page.set_cropbox(fitz.Rect(clip))
    (OUT / name).write_text(page.get_svg_image(text_as_path=True), encoding="utf-8")
    print("wrote", name)


FOOTER = [(40, 805, 420, 830), (530, 805, 570, 830)]  # footer text + page number in the badge

# 1. page frame (top wave, logo, footer band): page 2 without any text and without the body graphics
d, p = single(2)
redact(p, [p.rect])  # all text
redact(p, [(0, 100, 595, 798)], graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
save_svg(p, "frame.svg")

# 2. cover art (sun, panels, logo) without text or the To-block lines
d, p = single(1)
redact(p, [p.rect])
redact(p, [(290, 495, 595, 830)], graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
save_svg(p, "cover.svg")

# 3. logo
for x in src[1].get_images():
    pix = fitz.Pixmap(src, x[0])
    if pix.width > 1000:
        if pix.alpha == 0 and pix.n > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        pix.save(OUT / "logo.png")
        print("wrote logo.png", pix.width, pix.height)
        break

# 4. diagrams: whole page fixed, variable labels/footers removed (re-drawn from the options)
d, p = single(4)
redact(p, spans(p, "kWp array", "Statcon Energiaa X5T") + FOOTER)
save_svg(p, "page_ongrid.svg")
d, p = single(5)
redact(p, spans(p, "kWp array", "BOXER HF 5 kW", "MIRAKAL 5.12", "Inverter isolates", "kW).") + FOOTER)
save_svg(p, "page_hybrid.svg")

# 5. timeline graphic only
d, p = single(8)
save_svg(p, "timeline.svg", clip=(40, 180, 555, 325))

# 6. datasheets + contact page (appended as PDF pages)
for name, a, b in [("statcon_modules", 14, 16), ("avaada_modules", 17, 18), ("statcon_ongrid_inverter", 19, 20),
                   ("statcon_hybrid_inverter", 21, 22), ("statcon_mirakal_battery", 23, 24), ("adroit_contact", 25, 25)]:
    d = fitz.open()
    d.insert_pdf(src, from_page=a - 1, to_page=b - 1)
    d.save(DS / f"{name}.pdf", garbage=4, deflate=True)
    print("wrote", name, (DS / f"{name}.pdf").stat().st_size // 1024, "KB")
