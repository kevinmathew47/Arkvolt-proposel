"""Build the folder to give to staff:  D:\\Arkvolt energy pvt\\staff-setup\\ARKVOLT-Staff-Setup (+ .zip)
python tools/build_staff_package.py

Contains the app code, brand artwork, datasheets, THIS PC's current price list (master.json, without the
dashboard key) and Setup.bat. No customers, proposals, PDFs, bills or passwords are included.
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

if PKG.exists():
    shutil.rmtree(PKG)
PKG.mkdir(parents=True)

# app code + artwork (no caches), datasheets, launcher
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
