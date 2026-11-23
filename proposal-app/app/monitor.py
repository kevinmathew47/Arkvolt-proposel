"""Link to the online ARKVOLT proposal monitor (arkvolt-monitor on Vercel).

* Every generated proposal (and later status changes) is reported: staff, customer, place, kW, type, price, reference.
  Phone numbers, consumer numbers and bills are never sent.
* Staff (role "sales") may download straight away only when every option is within the staff rules
  (plant ≤ staff limit, standard package / per-kW price). Anything else needs approval on the monitor first.
* If the laptop is offline, reports wait in the outbox and are sent later.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from sqlalchemy import select

from .db import Outbox, SessionLocal, Setting
from .engine import inr


def config(s) -> dict:
    get = lambda k, d="": (s.get(Setting, k).value if s.get(Setting, k) else d)  # noqa: E731
    return {"url": get("monitor_url").strip().rstrip("/"), "key": get("monitor_key").strip(),
            "max_kw": float(get("staff_max_kw", "5") or 5)}


def _call(cfg, method, path, body=None, timeout=8):
    req = urllib.request.Request(cfg["url"] + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Device-Token": cfg["key"], "Content-Type": "application/json", "User-Agent": "arkvolt-proposal-app"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def ping(cfg) -> str:
    if not cfg["url"] or not cfg["key"]:
        raise RuntimeError("Enter the monitor address and the device key first.")
    try:
        return _call(cfg, "GET", "/api/v1/ping")["staff"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("The monitor refused this device key." if e.code == 401 else f"Monitor error {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Cannot reach the monitor ({getattr(e, 'reason', e)}).") from e


# ---------------------------------------------------------------------------------------------
# Staff rules
# ---------------------------------------------------------------------------------------------
def rule_breaks(out: dict, max_kw: float) -> list:
    """Reasons an option is outside what staff may quote on their own (empty list = within the rules)."""
    reasons = []
    for o in out.get("options", []):
        if o["kw"] > max_kw + 1e-9:
            reasons.append(f"Option {o['letter']}: {o['kw']:g} kW is above the {max_kw:g} kW staff limit")
        std = o.get("package_price") or o.get("suggested_price")
        if not std:
            reasons.append(f"Option {o['letter']}: no standard price for this system (custom price ₹{inr(o.get('price') or 0)})")
        elif o.get("price") and abs(float(o["price"]) - float(std)) > 0.5:
            reasons.append(f"Option {o['letter']}: price ₹{inr(float(o['price']))} differs from the standard ₹{inr(float(std))}")
    return reasons


def price_key(out: dict) -> str:
    return json.dumps([[o["letter"], o["type"], o["kw"], o["module"], o.get("price")] for o in out.get("options", [])])


def _options(out):
    return [dict(letter=o["letter"], type=o["type"], kw=o["kw"], kwp=round(o["kwp"], 2), module=o["module"], inverter=o["inverter"],
                 battery=o.get("battery"), price=o.get("price"), package_price=o.get("package_price") or o.get("suggested_price"),
                 overridden=bool(o.get("overridden")), payback_text=o.get("payback_text")) for o in out.get("options", [])]


def _customer(inputs):
    c = inputs.get("customer") or {}
    return (" ".join(x for x in (c.get("salutation"), c.get("name")) if x) or ("Common quote" if c.get("blank") else "")), (c.get("city") or "")


# ---------------------------------------------------------------------------------------------
# Approvals (synchronous: the salesperson is waiting)
# ---------------------------------------------------------------------------------------------
def request_approval(cfg, p, inputs, out, reasons, note="") -> int:
    cust, place = _customer(inputs)
    r = _call(cfg, "POST", "/api/v1/approvals", {"local_id": p.id, "customer": cust, "kw_text": out.get("kw_text", ""),
                                                 "reasons": reasons, "options": _options(out), "note": note})
    return r["id"]


def approval_status(cfg, aid) -> dict:
    return _call(cfg, "GET", f"/api/v1/approvals/{aid}")


# ---------------------------------------------------------------------------------------------
# Reports (fire-and-forget through the outbox)
# ---------------------------------------------------------------------------------------------
def report(s, p, v, out, status, within_rules=True):
    """Queue the proposal's current state; sent now if online, otherwise later."""
    cfg = config(s)
    if not cfg["url"] or not cfg["key"]:
        return
    cust, place = _customer(v.inputs or {})
    payload = {"local_id": p.id, "version": v.version_no, "ref": v.ref or p.ref_base, "brand": p.brand, "customer": cust, "place": place,
               "kw_text": (out or {}).get("kw_text") or p.summary, "status": status, "within_rules": within_rules,
               "options": _options(out) if out else (v.results or {}).get("options", [])}
    s.add(Outbox(path="/api/v1/proposals", payload=payload))
    s.commit()
    threading.Thread(target=flush, daemon=True).start()


_lock = threading.Lock()


def flush():
    """Send queued reports in order; stop at the first network failure and retry later."""
    if not _lock.acquire(blocking=False):
        return
    try:
        with SessionLocal() as s:
            cfg = config(s)
            if not cfg["url"] or not cfg["key"]:
                return
            for item in s.scalars(select(Outbox).order_by(Outbox.id)).all():
                try:
                    _call(cfg, "POST", item.path, item.payload)
                except urllib.error.HTTPError as e:
                    if e.code in (400, 404, 422):  # malformed / rejected → drop, retrying won't help
                        s.delete(item)
                        s.commit()
                        continue
                    item.tries = (item.tries or 0) + 1
                    s.commit()
                    return
                except (urllib.error.URLError, TimeoutError, OSError):
                    return
                s.delete(item)
                s.commit()
    finally:
        _lock.release()


def start_background_sender(every_s=120):
    def loop():
        while True:
            try:
                flush()
            except Exception:  # noqa: BLE001 — never let the sender crash the app
                pass
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True, name="monitor-outbox").start()
