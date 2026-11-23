"""Read KSEB bills.

* parse_bill_pdf — the PDF downloaded from kseb.in "Know Your Bill" (text layer, reliable).
* ocr_bill_photo — a phone photo of the thermal spot-bill receipt (Tesseract OCR; salesperson must confirm).

Both return the same dict shape; missing fields are None.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import fitz  # PyMuPDF

TARIFF_MAP = [  # text on the bill -> tariff code used by the engine
    (r"LT-?\s?1\b|LT-1A|LT-1\s*Dom", "LT-1"),
    (r"LT-?\s?7\s?A", "LT-7A"),
    (r"LT-?\s?6\s?F", "LT-6F"),
    (r"LT-?\s?6", "LT-6F"),
    (r"LT-?\s?7", "LT-7A"),
]


def _tariff_code(text: str | None):
    if not text:
        return None
    for pat, code in TARIFF_MAP:
        if re.search(pat, text, re.I):
            return code
    return None


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _after(label, text, pat=r"([^\n]+)"):
    m = re.search(re.escape(label) + r"\s*\n\s*" + pat, text)
    return m.group(1).strip() if m else None


def empty() -> dict:
    return dict(consumer_no=None, name=None, address=None, section=None, section_code=None, tariff_text=None, tariff=None,
                phase=None, connected_load_kw=None, bill_date=None, due_date=None, billing_period=None, months=2,
                units_total=None, units_by_zone={}, monthly_units=None, avg_monthly_by_zone={}, bill_amount=None,
                fixed_charge=None, energy_charge=None, duty=None, mobile_masked=None, tod=False, source=None, warnings=[])


def parse_bill_text(text: str) -> dict:
    b = empty()
    b["source"] = "pdf"
    m = re.search(r"Consumer#\s*\n?\s*(\d{10,16})", text)
    b["consumer_no"] = m.group(1) if m else None
    m = re.search(r"Section\s*\[(\d+)\]-\s*([^\n]+)", text)
    if m:
        b["section_code"], b["section"] = m.group(1), m.group(2).strip()
    m = re.search(r"Name & Mailing Address\s*\n(.+?)\n(.+?)\n(?:For redressing|Bill#)", text, re.S)
    if m:
        b["name"] = m.group(1).strip()
        b["address"] = " ".join(x.strip() for x in m.group(2).splitlines() if x.strip())
    tp = _after("Tariff/Phase", text)
    if tp:
        b["tariff_text"] = tp
        b["tariff"] = _tariff_code(tp)
        if "/" in tp:
            ph = tp.split("/", 1)[1].strip().lower()
            b["phase"] = "3" if ph.startswith(("three", "3")) else "1"
    bp = _after("Billing Period", text)
    if bp:
        b["billing_period"] = bp
        if "bi-monthly" in bp.lower():
            b["months"] = 2
        elif "monthly" in bp.lower():
            b["months"] = 1
    m = re.search(r"Connected Load\s*\n\s*([\d,.]+)\s*(Watts|W|kW)", text, re.I)
    if m:
        v = _num(m.group(1))
        b["connected_load_kw"] = v / 1000 if m.group(2).lower().startswith("w") else v
    b["bill_date"] = _after("Bill Date", text)
    b["due_date"] = _after("Due Date", text, r"([\d-]+)")
    m = re.search(r"Reg\.\s*Mob#\s*([0-9xX*]+)", text)
    b["mobile_masked"] = m.group(1) if m else None
    # Consumption table: KWH / [zone] / Import / IR / FR / OMF / Units
    zones = {}
    for m in re.finditer(r"KWH\s*\n\s*(?:(Normal|Off Peak|Peak)\s*\n\s*)?Import\s*\n\s*([\d.]+)\s*\n\s*([\d.]+)\s*\n\s*([\d.]+)\s*\n\s*([\d.]+)", text):
        zone = m.group(1) or "Total"
        zones[zone] = _num(m.group(5))
    if zones:
        b["units_by_zone"] = zones
        b["units_total"] = sum(zones.values())
        b["tod"] = len(zones) > 1
    m = re.search(r"Average consumption\(Monthly\).*?KWH\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)", text, re.S)
    if m:
        b["avg_monthly_by_zone"] = {"Normal": _num(m.group(1)), "Off Peak": _num(m.group(2)), "Peak": _num(m.group(3))}
    m = re.search(r"Total Amt\.\(Bill#\d+\)\s*\(a\+b\+c\+d\)\s*\n\s*([\d,.]+)", text)
    if not m:
        m = re.search(r"Demand for [\d/]+ is Rupees ([A-Za-z ]+?)\s+Only", text)
        b["bill_amount"] = None
    else:
        b["bill_amount"] = _num(m.group(1))
    b["fixed_charge"] = _num(_after("Fixed Charge[FC]", text, r"([\d,.]+)"))
    ec = sum(_num(x) for x in re.findall(r"Energy Charge[^\n\[]*\[[A-Z]+\]\s*\n\s*([\d,.]+)", text))
    b["energy_charge"] = ec or None
    b["duty"] = _num(_after("Electricity Duty[ED]", text, r"([\d,.]+)"))
    if b["units_total"] and b["months"]:
        b["monthly_units"] = b["units_total"] / b["months"]
    if not b["consumer_no"]:
        b["warnings"].append("Consumer number not found — please type it.")
    if not b["units_total"]:
        b["warnings"].append("Consumption not found — please type the units.")
    return b


def parse_bill_pdf(path_or_bytes) -> dict:
    doc = fitz.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, (bytes, bytearray)) else fitz.open(path_or_bytes)
    text = "\n".join(p.get_text() for p in doc)
    if len(text.strip()) < 50:  # scanned PDF: fall back to OCR of the first page
        pix = doc[0].get_pixmap(dpi=250)
        return ocr_bill_image_bytes(pix.tobytes("png"))
    return parse_bill_text(text)


# ---------------------------------------------------------------------------------------------
# Thermal receipt photo (OCR)
# ---------------------------------------------------------------------------------------------
TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def tesseract_path():
    p = shutil.which("tesseract")
    if p:
        return p
    for c in TESSERACT_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return tesseract_path() is not None


def _prep(img):
    """Grey, upscale, auto-rotate to portrait text and binarise — thermal receipts are low contrast."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1600:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=2)
    return img.point(lambda v: 255 if v > 150 else 0)


def parse_receipt_text(text: str) -> dict:
    b = empty()
    b["source"] = "photo"
    t = text.replace("O", "0") if False else text
    m = re.search(r"C\s*#\s*[:;.]?\s*([0-9OoIl ]{13,16})", t)
    if m:
        b["consumer_no"] = re.sub(r"\D", "", m.group(1).replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1"))[:13]
    if not b["consumer_no"]:
        m = re.search(r"\b(1\d{12})\b", t)
        b["consumer_no"] = m.group(1) if m else None
    m = re.search(r"Tariff\s*[:;]?\s*([^\n]+)", t, re.I)
    if m:
        b["tariff_text"] = m.group(1).strip()
        b["tariff"] = _tariff_code(b["tariff_text"])
    m = re.search(r"Load\s*[:;]?\s*([\d.]+)\s*kW", t, re.I)
    if m:
        b["connected_load_kw"] = _num(m.group(1))
    m = re.search(r"Phase\s*[:;]?\s*(\d)", t, re.I)
    if m:
        b["phase"] = m.group(1)
    m = re.search(r"Bill Amount\s*\(?R?s?\.?\)?\s*[:;]?\s*([\d,]+\.\d{2})", t, re.I)
    if m:
        b["bill_amount"] = _num(m.group(1))
    m = re.search(r"Bill Date\s*[:;]?\s*([\d-]{8,10})", t, re.I)
    if m:
        b["bill_date"] = m.group(1)
    # KWH/NL/I curr prev cons avg ...
    zones = {}
    for m in re.finditer(r"KWH/?\s*(NL|OP|PK)?/?I?\s+(\d+)\s+(\d+)\s+(\d+)", t, re.I):
        z = {"NL": "Normal", "OP": "Off Peak", "PK": "Peak"}.get((m.group(1) or "").upper(), "Total")
        zones[z] = _num(m.group(4))
    if zones:
        b["units_by_zone"] = zones
        b["units_total"] = sum(zones.values())
        b["monthly_units"] = b["units_total"] / 2
        b["tod"] = len(zones) > 1
    b["warnings"].append("Read from a photo — please check every number before continuing.")
    if not b["consumer_no"]:
        b["warnings"].append("Consumer number not readable — please type it.")
    return b


def ocr_bill_image_bytes(data: bytes) -> dict:
    import io
    from PIL import Image
    if not ocr_available():
        b = empty()
        b["source"] = "photo"
        b["warnings"].append("Photo reading (Tesseract OCR) is not installed on this PC — please type the consumer number.")
        return b
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_path()
    img = _prep(Image.open(io.BytesIO(data)))
    best = None
    for angle in (0, 90, 270, 180):
        txt = pytesseract.image_to_string(img.rotate(angle, expand=True), config="--psm 6")
        b = parse_receipt_text(txt)
        score = sum(bool(b[k]) for k in ("consumer_no", "tariff", "bill_amount", "units_total"))
        if best is None or score > best[0]:
            best = (score, b, txt)
        if score >= 3:
            break
    best[1]["ocr_text"] = best[2]
    return best[1]
