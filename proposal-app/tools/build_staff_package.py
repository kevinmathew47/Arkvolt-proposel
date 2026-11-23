"""Build the folder to give to staff:  D:\\Arkvolt energy pvt\\staff-setup\\ARKVOLT-Staff-Setup (+ .zip)
python tools/build_staff_package.py

Contains the app code, brand artwork, datasheets, THIS PC's current price list (master.json, without the
dashboard key), the branded setup window (Setup.bat) and the no-console launcher. No customers, proposals,
PDFs, bills or passwords are included.
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parents[1]
OUT_ROOT = APP.parent / "staff-setup"
PKG = OUT_ROOT / "ARKVOLT-Staff-Setup"
sys.path.insert(0, str(APP))
from app.db import SessionLocal, export_master  # noqa: E402

CR, LF = bytes([13]), bytes([10])
BOM = bytes([0xEF, 0xBB, 0xBF])

if PKG.exists():
    shutil.rmtree(PKG)
PKG.mkdir(parents=True)

# app code + artwork (no caches), datasheets, setup window + launcher
shutil.copytree(APP / "app", PKG / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copytree(APP / "data" / "datasheets", PKG / "data" / "datasheets")
shutil.copy2(APP / "run.py", PKG / "run.py")
for f in (APP / "tools" / "staff_package").iterdir():
    shutil.copy2(f, PKG / f.name)

# runtime requirements only (tests and photo OCR are not needed on staff laptops)
req = [l for l in (APP / "requirements.txt").read_text(encoding="utf-8").splitlines()
       if l.strip() and not l.startswith("#") and not l.split(">")[0].strip() in ("pytest", "httpx", "pytesseract")]
(PKG / "requirements.txt").write_text("\n".join(req) + "\n", encoding="utf-8")

# this PC's current price list, products, company details, tariffs, rules and text
with SessionLocal() as s:
    master = export_master(s)
(PKG / "master.json").write_text(json.dumps(master, ensure_ascii=False, indent=1), encoding="utf-8")

# desktop icon from the ARKVOLT mark
mark = Image.open(APP.parent / "qr" / "arkvolt-mark.png").convert("RGBA")
side = max(mark.size)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2), mark)
sq.save(PKG / "arkvolt.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# white ARKVOLT logo for the setup window and the start-up splash (rendered from the official SVG)
from playwright.sync_api import sync_playwright  # noqa: E402

svg = (APP / "app" / "static" / "brand" / "arkvolt" / "logo-reversed.svg").as_uri()
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge")
    pg = br.new_page(viewport={"width": 300, "height": 68}, device_scale_factor=1)
    tmp = PKG / "_logo.html"  # a file:// page may load the file:// SVG (an about:blank page may not)
    tmp.write_text(f'<html><body style="margin:0;background:#0A1527"><img src="{svg}" style="width:300px;display:block"></body></html>', encoding="utf-8")
    pg.goto(tmp.as_uri(), wait_until="load")
    ok = pg.evaluate("document.images[0].complete && document.images[0].naturalWidth > 0")
    pg.screenshot(path=str(PKG / "logo-white.png"))
    br.close()
    tmp.unlink()
assert ok, "ARKVOLT logo did not render"

# Windows line endings for scripts; PowerShell 5.1 needs a BOM to read UTF-8 correctly
for f in list(PKG.glob("*.bat")) + list(PKG.glob("*.ps1")) + list(PKG.glob("*.txt")):
    t = f.read_bytes().replace(CR + LF, LF).replace(LF, CR + LF)
    if f.suffix == ".ps1" and not t.startswith(BOM):
        t = BOM + t
    f.write_bytes(t)

# safety check: nothing private in the package
bad = [p for p in PKG.rglob("*") if p.suffix in (".db", ".key") or p.name in ("FIRST_LOGIN.txt", "NEEDS_SETUP")
       or "pdfs" in p.parts or "uploads" in p.parts]
assert not bad, f"private files in package: {bad}"
assert "monitor_key" not in master["settings"], "device key must not be exported"

zip_path = OUT_ROOT / "ARKVOLT-Staff-Setup.zip"
zip_path.unlink(missing_ok=True)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in PKG.rglob("*"):
        z.write(p, p.relative_to(OUT_ROOT))
size = sum(p.stat().st_size for p in PKG.rglob("*") if p.is_file())
print(f"{PKG}  ({size / 1e6:.1f} MB, {len(master['packages'])} packages, {len(master['products'])} products)")
print(f"{zip_path}  ({zip_path.stat().st_size / 1e6:.1f} MB)")
