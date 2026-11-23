"""Export this PC's price list, products, company details, tariffs, rules, template text and settings
(without the dashboard device key) to master.json, for the staff setup package.
python tools/export_master.py [output path]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal, export_master  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "master.json"
with SessionLocal() as s:
    m = export_master(s)
out.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{out}  ·  {len(m['packages'])} packages, {len(m['products'])} products, {len(m['tariffs'])} tariffs")
