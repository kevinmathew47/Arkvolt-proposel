"""Solar Proposal Generator — web app (FastAPI). Run: python run.py  →  http://localhost:8000"""
from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import billparse, compute as C, engine as E, monitor, pdfgen
from .db import (DATA, ROOT, Audit, Company, Outbox, Package, Product, Proposal, ProposalVersion, RefCounter, RuleVersion,
                 SessionLocal, Setting, TariffVersion, TemplateText, User, audit, init_db)

FIRST_LOGIN = init_db()
if FIRST_LOGIN:
    print("\n" + "=" * 70 + "\n" + FIRST_LOGIN + "\n" + "=" * 70 + "\n")

key_file = DATA / "secret.key"
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32))

app = FastAPI(title="Solar Proposal Generator")
monitor.start_background_sender()
app.add_middleware(SessionMiddleware, secret_key=key_file.read_text().strip(), max_age=60 * 60 * 12, same_site="lax")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
tpl = Jinja2Templates(directory=ROOT / "app" / "templates")
tpl.env.filters["inr"] = lambda v, d=0: E.inr(v or 0, d)
tpl.env.globals["v"] = str(int(max(f.stat().st_mtime for f in (ROOT / "app" / "static").glob("*.*"))))  # cache-buster for app.js/css
tpl.env.filters["tojson_pretty"] = lambda v: json.dumps(v, indent=1, ensure_ascii=False)
ROLE_RANK = {"sales": 1, "manager": 2, "admin": 3}


# ---------------------------------------------------------------------------------------------
# Session / auth helpers
# ---------------------------------------------------------------------------------------------
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class NotLoggedIn(Exception):
    pass


@app.exception_handler(NotLoggedIn)
def _to_login(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Please log in again."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def user(request: Request, s=Depends(db)) -> User:
    uid = request.session.get("uid")
    u = s.get(User, uid) if uid else None
    if not u or not u.active:
        raise NotLoggedIn()
    return u


def need(role):
    def dep(u: User = Depends(user)):
        if ROLE_RANK.get(u.role, 0) < ROLE_RANK[role]:
            raise HTTPException(403, f"{role.title()} access needed")
        return u
    return dep


def can_see(u: User, p: Proposal):
    return ROLE_RANK[u.role] >= 2 or p.created_by == u.id


def page(request, name, u=None, **ctx):
    return tpl.TemplateResponse(request, name, {"me": u, **ctx})


# ---------------------------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), s=Depends(db)):
    u = s.scalar(select(User).where(User.username == username.strip().lower()))
    if not u or not u.active or not u.check_password(password):
        return page(request, "login.html", error="Wrong username or password.")
    request.session["uid"] = u.id
    audit(s, u.id, "login")
    s.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", brand: str = "", status: str = "", u: User = Depends(user), s=Depends(db)):
    st = select(Proposal).order_by(Proposal.updated_at.desc())
    if ROLE_RANK[u.role] < 2:
        st = st.where(Proposal.created_by == u.id)
    if q:
        like = f"%{q}%"
        st = st.where(or_(Proposal.customer_name.ilike(like), Proposal.ref_base.ilike(like), Proposal.consumer_no.ilike(like)))
    if brand:
        st = st.where(Proposal.brand == brand)
    if status:
        st = st.where(Proposal.status == status)
    rows = s.scalars(st.limit(300)).all()
    pending = s.scalars(select(ProposalVersion).where(ProposalVersion.needs_approval.is_(True), ProposalVersion.approved_by.is_(None))).all() if ROLE_RANK[u.role] >= 2 else []
    return page(request, "dashboard.html", u, rows=rows, q=q, brand=brand, status=status, pending=pending)


# ---------------------------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------------------------
def wizard_ctx(s):
    m = C.load_master(s)
    prods = {code: {k: v for k, v in p.items() if k in ("category", "make", "model", "wp", "kw", "hybrid", "kwh", "code", "active")}
             for code, p in m["products"].items()}
    return {"packages": m["packages"], "products": prods, "tariffs": {k: v["name"] for k, v in m["tariffs"].items()},
            "max_discount": m["settings"].get("max_discount_pct", "5")}


@app.get("/new", response_class=HTMLResponse)
def new(request: Request, u: User = Depends(user), s=Depends(db)):
    return page(request, "wizard.html", u, pid=None, inputs={}, ctx=wizard_ctx(s), ocr=billparse.ocr_available())


@app.get("/p/{pid}/edit", response_class=HTMLResponse)
def edit(pid: int, request: Request, u: User = Depends(user), s=Depends(db)):
    p = s.get(Proposal, pid)
    if not p or not can_see(u, p):
        raise HTTPException(404)
    inputs = p.versions[-1].inputs if p.versions else {}
    return page(request, "wizard.html", u, pid=pid, inputs=inputs, ctx=wizard_ctx(s), ocr=billparse.ocr_available())


@app.post("/api/compute")
async def api_compute(request: Request, pid: int | None = None, u: User = Depends(user), s=Depends(db)):
    inputs = await request.json()
    out = C.compute(inputs, C.load_master(s))
    approved = False
    p = s.get(Proposal, pid) if pid else None
    if p and can_see(u, p) and p.versions:
        v = p.versions[-1]
        key = json.dumps([[o.get("package_id"), o.get("price"), o.get("price_override")] for o in inputs.get("options") or []])
        approved = bool(v.approved_by) and (v.results or {}).get("approval_key") == key
    return C.summary_json(out) | {"approved": approved, "assumptions": out.get("assumptions"), "texts": {k: v for k, v in (out.get("texts") or {}).items() if k in ("title", "subject")}}


@app.post("/api/advise")
async def api_advise(request: Request, u: User = Depends(user), s=Depends(db)):
    return C.advise(await request.json(), C.load_master(s))


@app.post("/api/bill/pdf")
async def api_bill_pdf(file: UploadFile = File(...), u: User = Depends(user)):
    data = await file.read()
    if not data[:5] == b"%PDF-":
        return {"warnings": ["That is not a PDF. Upload the bill PDF from KSEB, or use 'Bill photo' for a picture."]}
    dest = DATA / "uploads" / f"bill_{dt.datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}.pdf"
    dest.write_bytes(data)
    b = billparse.parse_bill_pdf(data)
    b["file"] = dest.name
    return b


@app.post("/api/bill/photo")
async def api_bill_photo(file: UploadFile = File(...), u: User = Depends(user)):
    return billparse.ocr_bill_image_bytes(await file.read())


def _save(s, u, pid, inputs) -> Proposal:
    master = C.load_master(s)
    out = C.compute(inputs, master)
    cust = inputs.get("customer", {})
    if pid:
        p = s.get(Proposal, pid)
        if not p or not can_see(u, p):
            raise HTTPException(404)
    else:
        p = Proposal(created_by=u.id, status="Draft")
        s.add(p)
        s.flush()
    p.brand = out.get("brand") or p.brand
    p.customer_name = " ".join(x for x in (cust.get("salutation"), cust.get("name")) if x) or ("Common quote" if cust.get("blank") else "")
    p.consumer_no = (inputs.get("bill") or {}).get("consumer_no") or ""
    p.summary = out.get("kw_text", "") + (" · " + ", ".join(sorted(out.get("types", []))) if out.get("types") else "")
    # the draft lives in the latest *ungenerated* version; generated versions are frozen
    v = p.versions[-1] if p.versions and not p.versions[-1].pdf_path else None
    if not v:
        v = ProposalVersion(proposal_id=p.id, version_no=(p.versions[-1].version_no + 1 if p.versions else 1), created_by=u.id)
        s.add(v)
        p.versions.append(v)
    # an approval covers exactly the prices that were approved; any later price change needs a new approval
    price_key = json.dumps([[o.get("package_id"), o.get("price"), o.get("price_override")] for o in inputs.get("options") or []])
    old_key = (v.results or {}).get("approval_key")
    online = (v.results or {}).get("online_approval")
    v.inputs = inputs
    v.results = C.summary_json(out) | {"approval_key": price_key} | ({"online_approval": online} if online else {})
    v.needs_approval = bool(out.get("needs_approval"))
    if not v.needs_approval or (v.approved_by and old_key != price_key):
        v.approved_by = v.approved_at = None
    audit(s, u.id, "save", {"proposal": p.id, "version": v.version_no})
    s.commit()
    return p


@app.post("/api/proposals")
async def api_save(request: Request, u: User = Depends(user), s=Depends(db)):
    body = await request.json()
    p = _save(s, u, body.get("id"), body.get("inputs") or {})
    return {"id": p.id}


@app.post("/api/proposals/{pid}/generate")
async def api_generate(pid: int, request: Request, u: User = Depends(user), s=Depends(db)):
    body = await request.json()
    p = _save(s, u, pid, body.get("inputs") or {})
    v = p.versions[-1]
    master = C.load_master(s)
    out = C.compute(v.inputs, master)
    if out["errors"]:
        return JSONResponse({"error": "Fix these first:\n• " + "\n• ".join(out["errors"])}, status_code=400)
    mcfg = monitor.config(s)
    reasons = monitor.rule_breaks(out, mcfg["max_kw"]) if u.role == "sales" else []
    online_ok = False
    if reasons:  # staff outside the rules → approval on the online monitor first
        if not (mcfg["url"] and mcfg["key"]):
            return JSONResponse({"error": "This quote is outside the staff rules:\n• " + "\n• ".join(reasons) +
                                          "\n\nOnline approval is not set up on this laptop — ask your admin."}, status_code=400)
        key = monitor.price_key(out)
        ap = (v.results or {}).get("online_approval") or {}
        try:
            if ap.get("key") != key:
                aid = await run_in_threadpool(monitor.request_approval, mcfg, p, v.inputs, out, reasons, body.get("note", ""))
                v.results = {**(v.results or {}), "online_approval": {"id": aid, "key": key}}
                audit(s, u.id, "approval.requested", {"proposal": p.id, "approval": aid, "reasons": reasons})
                s.commit()
                return JSONResponse({"pending": True, "error": "Sent to your manager for approval:\n• " + "\n• ".join(reasons) +
                                                               "\n\nYou can download the PDF as soon as it is approved."}, status_code=400)
            st = await run_in_threadpool(monitor.approval_status, mcfg, ap["id"])
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"Could not reach the approval server: {e}. Check the internet connection and try again."}, status_code=400)
        if st["status"] == "Pending":
            return JSONResponse({"pending": True, "error": "Still waiting for approval. Try again in a little while."}, status_code=400)
        if st["status"] == "Rejected":
            return JSONResponse({"error": f"Rejected by {st.get('decided_by') or 'manager'}" + (f": {st['comment']}" if st.get("comment") else "") +
                                          ".\nChange the quote and generate again to send a new request."}, status_code=400)
        online_ok = True
    if v.needs_approval and not v.approved_by and not online_ok:
        return JSONResponse({"error": "This price override needs manager approval before the PDF can be generated. "
                                      "A manager can approve it from the proposal page.", "approval": True}, status_code=400)
    if not body.get("warnings_seen") and out["warnings"]:
        return JSONResponse({"error": "Tick 'I have seen the warnings' first.", "warnings": out["warnings"]}, status_code=400)
    today = dt.date.today()
    company = master["companies"][out["brand"]]
    if not p.ref_base:
        fy = C.fy_of(today)
        rc = s.scalar(select(RefCounter).where(RefCounter.brand == out["brand"], RefCounter.fy == fy))
        if not rc:
            rc = RefCounter(brand=out["brand"], fy=fy, last=0)
            s.add(rc)
        rc.last += 1
        p.ref_base = f"{company['ref_prefix']}/SOL/{out['ref_type']}/{fy}/{rc.last:03d}"
    n_generated = sum(1 for x in p.versions if x.pdf_path)
    v.ref = p.ref_base if n_generated == 0 else f"{p.ref_base}-R{n_generated}"
    date = today.strftime("%d/%m/%Y")
    dest = DATA / "pdfs" / f"{p.id:05d}_v{v.version_no}_{out['file_name']}"
    try:
        # Playwright's sync API must not run on the event loop → worker thread
        info = await run_in_threadpool(pdfgen.generate, out, master, v.ref, date, dest,
                                       signatory=v.inputs.get("signatory", ""), bill=v.inputs.get("bill"))
    except Exception as e:  # noqa: BLE001
        s.rollback()
        return JSONResponse({"error": f"PDF engine failed: {e}"}, status_code=500)
    v.pdf_path = dest.name
    v.snapshot = {k: master[k] for k in ("date", "rules")} | {"tariff": master["tariffs"].get(out["tariff_code"]),
                                                             "products": {c: master["products"][c] for o in out["options"] for c in (o["module"], o["inverter"], o["battery"]) if c}}
    p.status = "Generated" if p.status in ("Draft", "Generated") else p.status
    audit(s, u.id, "generate", {"proposal": p.id, "ref": v.ref, **info})
    s.commit()
    monitor.report(s, p, v, out, p.status, within_rules=not reasons)
    return {"id": p.id, "pdf": f"/files/{v.id}", "ref": v.ref, **info}


@app.post("/api/preview", response_class=HTMLResponse)
async def api_preview(request: Request, u: User = Depends(user), s=Depends(db)):
    inputs = await request.json()
    master = C.load_master(s)
    out = C.compute(inputs, master)
    if not out.get("options") or not out.get("texts"):
        return HTMLResponse("<p style='font-family:sans-serif;padding:20px'>Complete steps 1–4 to see the preview.</p>")
    html = pdfgen.render_html(out, master, "(assigned when generated)", dt.date.today().strftime("%d/%m/%Y"),
                              inputs.get("signatory", ""), inputs.get("bill"))
    base = str(request.base_url).rstrip("/")
    html = html.replace((ROOT / "app" / "static").as_uri(), base + "/static")
    return HTMLResponse(html.replace("</head>", "<style>body{background:#8a94a3}.page{margin:14px auto;box-shadow:0 4px 18px rgba(0,0,0,.3)}</style></head>"))


@app.get("/files/{vid}")
def file_download(vid: int, dl: int = 0, u: User = Depends(user), s=Depends(db)):
    v = s.get(ProposalVersion, vid)
    if not v or not v.pdf_path or not can_see(u, v.proposal):
        raise HTTPException(404)
    f = DATA / "pdfs" / v.pdf_path
    name = v.pdf_path.split("_", 2)[2] if v.pdf_path.count("_") >= 2 else v.pdf_path
    return FileResponse(f, media_type="application/pdf", filename=name, content_disposition_type="attachment" if dl else "inline")


# ---------------------------------------------------------------------------------------------
# Proposal detail: versions, status, duplicate, approval
# ---------------------------------------------------------------------------------------------
@app.get("/p/{pid}", response_class=HTMLResponse)
def detail(pid: int, request: Request, u: User = Depends(user), s=Depends(db)):
    p = s.get(Proposal, pid)
    if not p or not can_see(u, p):
        raise HTTPException(404)
    users = {x.id: x.name or x.username for x in s.scalars(select(User))}
    return page(request, "detail.html", u, p=p, users=users)


@app.post("/p/{pid}/status")
def set_status(pid: int, status: str = Form(...), u: User = Depends(user), s=Depends(db)):
    p = s.get(Proposal, pid)
    if not p or not can_see(u, p):
        raise HTTPException(404)
    if status in ("Won", "Lost") and ROLE_RANK[u.role] < 2:
        raise HTTPException(403, "Only a manager can mark Won / Lost")
    if status not in ("Sent", "Won", "Lost", "Generated"):
        raise HTTPException(400)
    p.status = status
    audit(s, u.id, "status", {"proposal": pid, "status": status})
    s.commit()
    gen = [x for x in p.versions if x.pdf_path]
    if gen:
        monitor.report(s, p, gen[-1], None, status)
    return RedirectResponse(f"/p/{pid}", status_code=303)


@app.post("/p/{pid}/duplicate")
def duplicate(pid: int, u: User = Depends(user), s=Depends(db)):
    p = s.get(Proposal, pid)
    if not p or not can_see(u, p):
        raise HTTPException(404)
    q = Proposal(created_by=u.id, brand=p.brand, customer_name=p.customer_name, consumer_no=p.consumer_no, summary=p.summary)
    s.add(q)
    s.flush()
    s.add(ProposalVersion(proposal_id=q.id, version_no=1, inputs=json.loads(json.dumps(p.versions[-1].inputs)), created_by=u.id))
    audit(s, u.id, "duplicate", {"from": pid, "to": q.id})
    s.commit()
    return RedirectResponse(f"/p/{q.id}/edit", status_code=303)


@app.post("/p/{pid}/approve")
def approve(pid: int, u: User = Depends(need("manager")), s=Depends(db)):
    p = s.get(Proposal, pid)
    v = p.versions[-1]
    v.approved_by, v.approved_at = u.id, dt.datetime.now()
    audit(s, u.id, "approve", {"proposal": pid, "version": v.version_no, "reason": (v.inputs or {}).get("discount_reason", "")})
    s.commit()
    return RedirectResponse(f"/p/{pid}", status_code=303)


# ---------------------------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, tab: str = "company", msg: str = "", err: str = "", u: User = Depends(need("admin")), s=Depends(db)):
    ctx = dict(tab=tab, msg=msg, err=err, mon=_monitor_info(s),
               companies=s.scalars(select(Company)).all(),
               products=s.scalars(select(Product).order_by(Product.category, Product.code)).all(),
               packages=s.scalars(select(Package).order_by(Package.brand, Package.kw, Package.type)).all(),
               tariffs=s.scalars(select(TariffVersion).order_by(TariffVersion.code, TariffVersion.valid_from.desc())).all(),
               rules=s.scalars(select(RuleVersion).order_by(RuleVersion.valid_from.desc())).all(),
               texts=s.scalars(select(TemplateText).order_by(TemplateText.brand, TemplateText.key)).all(),
               users=s.scalars(select(User).order_by(User.username)).all(),
               counters=s.scalars(select(RefCounter).order_by(RefCounter.brand, RefCounter.fy)).all(),
               settings={x.key: x.value for x in s.scalars(select(Setting))},
               audit_rows=s.scalars(select(Audit).order_by(Audit.id.desc()).limit(80)).all(),
               datasheets=sorted(f.name for f in (DATA / "datasheets").glob("*.pdf")), fy=C.fy_of(dt.date.today()))
    return page(request, "admin.html", u, **ctx)


def _back(tab, msg):
    return RedirectResponse(f"/admin?tab={tab}&msg={msg}", status_code=303)


@app.post("/admin/company/{brand}")
async def admin_company(brand: str, request: Request, u: User = Depends(need("admin")), s=Depends(db)):
    c = s.get(Company, brand)
    form = await request.form()
    c.data = {**c.data, **{k: str(v).strip() for k, v in form.items()}}
    audit(s, u.id, "admin.company", brand)
    s.commit()
    return _back("company", f"{brand} saved")


@app.post("/admin/product")
async def admin_product(request: Request, code: str = Form(...), category: str = Form(...), data: str = Form(...),
                        active: str = Form(""), almm: str = Form(""), datasheet_name: str = Form(""),
                        datasheet: UploadFile | None = File(None), u: User = Depends(need("admin")), s=Depends(db)):
    try:
        d = json.loads(data)
    except json.JSONDecodeError as e:
        return _back("products", f"Invalid JSON for {code}: {e}")
    d["code"] = code
    p = s.get(Product, code) or Product(code=code)
    p.category, p.data, p.active, p.almm = category, d, bool(active), bool(almm)
    if datasheet is not None and datasheet.filename:
        raw = await datasheet.read()
        if raw[:5] != b"%PDF-":
            return _back("products", "Datasheet must be a PDF")
        fname = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in datasheet.filename)
        (DATA / "datasheets" / fname).write_bytes(raw)
        p.datasheet = fname
    elif datasheet_name:
        p.datasheet = datasheet_name
    s.add(p)
    audit(s, u.id, "admin.product", code)
    s.commit()
    return _back("products", f"{code} saved")


@app.post("/admin/package")
async def admin_package(request: Request, u: User = Depends(need("admin")), s=Depends(db)):
    f = await request.form()
    pid = f.get("id")
    p = s.get(Package, int(pid)) if pid else Package()
    p.brand, p.name, p.kw, p.type = f["brand"], f["name"], float(f["kw"]), f["type"]
    p.module, p.qty, p.inverter = f["module"], int(f["qty"]), f["inverter"]
    p.battery = f.get("battery") or None
    p.price, p.note, p.active = float(f["price"]), f.get("note", ""), bool(f.get("active"))
    s.add(p)
    audit(s, u.id, "admin.package", {"name": p.name, "price": p.price})
    s.commit()
    return _back("packages", f"{p.name} saved")


@app.post("/admin/tariff")
def admin_tariff(code: str = Form(...), name: str = Form(...), valid_from: str = Form(...), source: str = Form(""),
                 data: str = Form(...), u: User = Depends(need("admin")), s=Depends(db)):
    """Tariffs are never edited in place: a new version with its own valid_from is added (old quotes stay reproducible)."""
    try:
        d = json.loads(data)
        _ = C._tariff({"code": code, "name": name, **d})
    except Exception as e:  # noqa: BLE001
        return _back("tariffs", f"Invalid tariff: {e}")
    for old in s.scalars(select(TariffVersion).where(TariffVersion.code == code, TariffVersion.valid_to == "")):
        if old.valid_from < valid_from:
            old.valid_to = (dt.date.fromisoformat(valid_from) - dt.timedelta(days=1)).isoformat()
    s.add(TariffVersion(code=code, name=name, valid_from=valid_from, source=source, data=d))
    audit(s, u.id, "admin.tariff", {"code": code, "valid_from": valid_from})
    s.commit()
    return _back("tariffs", f"{code} version from {valid_from} added")


@app.post("/admin/rules")
def admin_rules(valid_from: str = Form(...), note: str = Form(""), data: str = Form(...), u: User = Depends(need("admin")), s=Depends(db)):
    try:
        d = json.loads(data)
        C._rules(d)
    except Exception as e:  # noqa: BLE001
        return _back("rules", f"Invalid rules: {e}")
    s.add(RuleVersion(valid_from=valid_from, note=note, data=d))
    audit(s, u.id, "admin.rules", {"valid_from": valid_from})
    s.commit()
    return _back("rules", f"Rules version from {valid_from} added")


@app.post("/admin/text/{tid}")
def admin_text(tid: int, text: str = Form(...), u: User = Depends(need("admin")), s=Depends(db)):
    t = s.get(TemplateText, tid)
    if t.text.lstrip().startswith("["):
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            return _back("text", f"{t.brand}.{t.key}: list must stay valid JSON ({e})")
    t.text = text
    audit(s, u.id, "admin.text", f"{t.brand}.{t.key}")
    s.commit()
    return _back("text", f"{t.brand}.{t.key} saved")


@app.post("/admin/user")
def admin_user(username: str = Form(...), name: str = Form(""), role: str = Form("sales"), password: str = Form(""),
               active: str = Form(""), u: User = Depends(need("admin")), s=Depends(db)):
    uname = username.strip().lower()
    x = s.scalar(select(User).where(User.username == uname))
    if not x:
        if len(password) < 8:
            return _back("users", "New users need a password of at least 8 characters")
        x = User(username=uname)
        s.add(x)
    x.name, x.role, x.active = name, role if role in ROLE_RANK else "sales", bool(active)
    if password:
        if len(password) < 8:
            return _back("users", "Password must be at least 8 characters")
        x.set_password(password)
    if x.id == u.id and (x.role != "admin" or not x.active):
        return _back("users", "You cannot remove your own admin access")
    audit(s, u.id, "admin.user", {"username": uname, "role": x.role})
    s.commit()
    return _back("users", f"{uname} saved")


@app.post("/admin/counter")
def admin_counter(brand: str = Form(...), fy: str = Form(...), last: int = Form(...), u: User = Depends(need("admin")), s=Depends(db)):
    rc = s.scalar(select(RefCounter).where(RefCounter.brand == brand, RefCounter.fy == fy)) or RefCounter(brand=brand, fy=fy)
    if rc.id and last < rc.last:
        return _back("refs", "Numbers are never reused — the counter can only move forward")
    rc.last = last
    s.add(rc)
    audit(s, u.id, "admin.counter", {"brand": brand, "fy": fy, "last": last})
    s.commit()
    return _back("refs", f"{brand} {fy}: next number will be {last + 1:03d}")


@app.post("/admin/settings")
async def admin_settings(request: Request, u: User = Depends(need("admin")), s=Depends(db)):
    for k, v in (await request.form()).items():
        x = s.get(Setting, k) or Setting(key=k)
        x.value = str(v)
        s.add(x)
    audit(s, u.id, "admin.settings", "")
    s.commit()
    return _back("settings", "Settings saved")


# ---------------------------------------------------------------------------------------------
# First run on a staff laptop (data/NEEDS_SETUP from the setup package)
# ---------------------------------------------------------------------------------------------
from .db import SETUP_MARKER  # noqa: E402


@app.middleware("http")
async def first_run(request: Request, call_next):
    if SETUP_MARKER.exists() and not request.url.path.startswith(("/setup", "/static")):
        return RedirectResponse("/setup", status_code=303)
    return await call_next(request)


@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, s=Depends(db)):
    if not SETUP_MARKER.exists():
        return RedirectResponse("/", status_code=303)
    return page(request, "setup.html", None, error=None, f={"monitor_url": monitor.config(s)["url"]})


@app.post("/setup")
async def setup_save(request: Request, s=Depends(db)):
    if not SETUP_MARKER.exists():
        return RedirectResponse("/", status_code=303)
    f = {k: str(v).strip() for k, v in (await request.form()).items()}
    err = None
    uname = f.get("staff_username", "").lower()
    if not f.get("staff_name") or not uname:
        err = "Enter the staff member's name and a username."
    elif uname == "admin" or not uname.replace(".", "").replace("_", "").isalnum():
        err = "Username: letters and numbers only (not 'admin')."
    elif len(f.get("staff_password", "")) < 6 or f.get("staff_password") != f.get("staff_password2"):
        err = "Staff password: at least 6 characters, typed the same twice."
    elif len(f.get("admin_password", "")) < 5:
        err = "Enter your admin password (the owner's), at least 5 characters."
    if err:
        return page(request, "setup.html", None, error=err, f=f)
    for k in ("monitor_url", "monitor_key"):
        x = s.get(Setting, k) or Setting(key=k)
        x.value = f.get(k, "").rstrip("/")
        s.add(x)
    adm = s.scalar(select(User).where(User.username == "admin")) or User(username="admin", name="Administrator", role="admin")
    adm.set_password(f["admin_password"])
    s.add(adm)
    staff = s.scalar(select(User).where(User.username == uname)) or User(username=uname)
    staff.name, staff.role, staff.active = f["staff_name"], "sales", True
    staff.set_password(f["staff_password"])
    s.add(staff)
    s.commit()
    note = ""
    if f.get("monitor_url") and f.get("monitor_key"):
        try:
            who = await run_in_threadpool(monitor.ping, monitor.config(s))
            note = f"Connected to the dashboard as {who}."
        except RuntimeError as e:
            note = f"Saved, but the dashboard test failed: {e} You can fix it later on the Account page."
    audit(s, staff.id, "setup", {"staff": uname, "monitor": bool(f.get("monitor_key"))})
    s.commit()
    SETUP_MARKER.unlink(missing_ok=True)
    request.session["uid"] = staff.id
    return page(request, "setup_done.html", staff, note=note)


def _go(url, **kw):
    from urllib.parse import urlencode
    return RedirectResponse(url + ("&" if "?" in url else "?") + urlencode(kw), status_code=303)


def _monitor_info(s):
    cfg = monitor.config(s)
    return {"url": cfg["url"], "key_set": bool(cfg["key"]), "waiting": s.scalar(select(func.count(Outbox.id))) or 0}


@app.post("/account/monitor")
async def account_monitor(monitor_url: str = Form(None), monitor_key: str = Form(""), next: str = Form("/account"),
                          u: User = Depends(user), s=Depends(db)):
    """Dashboard connection of this laptop: anyone signed in may enter / replace the device key (blank = keep);
    only the admin may change the dashboard address. Always tests the connection after saving."""
    next = next if next in ("/account", "/admin?tab=settings") else "/account"
    changed = []
    if monitor_url is not None and u.role == "admin" and monitor_url.strip().rstrip("/") != monitor.config(s)["url"]:
        x = s.get(Setting, "monitor_url") or Setting(key="monitor_url")
        x.value = monitor_url.strip().rstrip("/")
        s.add(x)
        changed.append("address")
    if monitor_key.strip():
        x = s.get(Setting, "monitor_key") or Setting(key="monitor_key")
        x.value = monitor_key.strip()
        s.add(x)
        changed.append("device key")
    if changed:
        audit(s, u.id, "monitor.settings", {"changed": changed})
    s.commit()
    saved = ("Saved the " + " and ".join(changed) + ". ") if changed else ""
    try:
        staff = await run_in_threadpool(monitor.ping, monitor.config(s))
    except RuntimeError as e:
        return _go(next, err=f"{saved}Connection test failed: {e}")
    monitor.flush()
    return _go(next, msg=f"{saved}Connected to the online dashboard as {staff}.")


@app.post("/admin/monitor/test")
async def admin_monitor_test(u: User = Depends(need("admin")), s=Depends(db)):
    return await account_monitor(monitor_url=None, monitor_key="", next="/admin?tab=settings", u=u, s=s)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, msg: str = "", err: str = "", u: User = Depends(user), s=Depends(db)):
    return page(request, "account.html", u, msg=msg, err=err, mon=_monitor_info(s))


@app.post("/account/profile")
def account_profile(name: str = Form(""), username: str = Form(""), password: str = Form(""),
                    u: User = Depends(user), s=Depends(db)):
    x = s.get(User, u.id)
    uname = username.strip().lower() or x.username
    if not name.strip():
        return _go("/account", err="Enter your name.")
    if uname != x.username:
        if not uname.replace(".", "").replace("_", "").isalnum():
            return _go("/account", err="Username: letters and numbers only (dot and underscore allowed).")
        if s.scalar(select(User).where(User.username == uname)):
            return _go("/account", err=f"The username '{uname}' is already taken.")
        if not x.check_password(password):
            return _go("/account", err="To change your username, enter your current password.")
    old = x.username
    x.name, x.username = name.strip(), uname
    audit(s, u.id, "account.profile", {"name": x.name, "username": uname, "was": old})
    s.commit()
    return _go("/account", msg="Profile saved" + (f". Your new username is {uname}." if uname != old else "."))


@app.post("/account")
def account_save(old: str = Form(...), new: str = Form(...), new2: str = Form(None), u: User = Depends(user), s=Depends(db)):
    x = s.get(User, u.id)
    if not x.check_password(old):
        return _go("/account", err="Current password is wrong.")
    if len(new) < 6:
        return _go("/account", err="Use at least 6 characters for the new password.")
    if new2 is not None and new != new2:
        return _go("/account", err="The two new passwords are not the same.")
    x.set_password(new)
    audit(s, u.id, "password.change")
    s.commit()
    (DATA / "FIRST_LOGIN.txt").unlink(missing_ok=True) if x.role == "admin" else None
    return _go("/account", msg="Password changed.")
