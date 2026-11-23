"""ARKVOLT proposal monitor — live view of every proposal made on staff laptops + online approvals.
Runs on Vercel (api/index.py) with Postgres via DATABASE_URL, or locally with SQLite:  python run.py
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# On Vercel the disk is read-only: never write files there, and say clearly what is missing instead of crashing.
ON_VERCEL = bool(os.environ.get("VERCEL"))
SETUP_PROBLEMS: list[str] = []


def _db_url():
    url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL")
           or os.environ.get("NEON_DATABASE_URL"))
    if not url:
        # Vercel's Neon integration can add a custom prefix (e.g. STORAGE_URL, ARKVOLT_DATABASE_URL):
        # take any variable holding a Postgres address, preferring the pooled *DATABASE_URL / *POSTGRES_URL ones.
        cands = sorted(((k, v) for k, v in os.environ.items() if v.startswith(("postgres://", "postgresql://"))),
                       key=lambda kv: (("UNPOOLED" in kv[0] or "NON_POOLING" in kv[0]),
                                       not kv[0].endswith(("DATABASE_URL", "POSTGRES_URL", "_URL")), kv[0]))
        url = cands[0][1] if cands else None
    if not url:
        if ON_VERCEL:
            SETUP_PROBLEMS.append("No database is connected. In Vercel open Storage → Create Database → Neon (Postgres), "
                                  "connect it to this project, then Redeploy.")
            url = "sqlite://"  # in-memory placeholder so the setup page can render
        else:
            url = f"sqlite:///{ROOT / 'monitor.db'}"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _db_url()
engine = create_engine(DB_URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def now():
    return dt.datetime.now(IST).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):  # people who log in to the dashboard (admin / manager)
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True)
    name = Column(String(120), default="")
    role = Column(String(20), default="manager")
    pw_hash = Column(String(200))
    active = Column(Boolean, default=True)

    def set_password(self, pw):
        salt = secrets.token_hex(16)
        self.pw_hash = f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000).hex()}"

    def check_password(self, pw):
        try:
            _, salt, h = self.pw_hash.split("$")
        except (ValueError, AttributeError):
            return False
        return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex(), h)


class Device(Base):  # one staff laptop; identified by a secret token stored in its proposal app
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    staff_name = Column(String(120))
    label = Column(String(120), default="")
    token_hash = Column(String(64), unique=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)
    last_seen = Column(DateTime, nullable=True)


class ProposalEvent(Base):  # latest state of each proposal version made on a laptop
    __tablename__ = "proposals"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"))
    local_id = Column(Integer)
    version = Column(Integer, default=1)
    ref = Column(String(80), default="")
    brand = Column(String(20), default="")
    customer = Column(String(200), default="")
    place = Column(String(120), default="")
    kw_text = Column(String(60), default="")
    types = Column(String(60), default="")
    max_kw = Column(Float, default=0)
    min_price = Column(Float, default=0)
    max_price = Column(Float, default=0)
    options = Column(JSON, default=list)
    status = Column(String(20), default="Generated")
    within_rules = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)
    device = relationship("Device")
    __table_args__ = (UniqueConstraint("device_id", "local_id", "version"),)


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"))
    local_id = Column(Integer)
    customer = Column(String(200), default="")
    kw_text = Column(String(60), default="")
    reasons = Column(JSON, default=list)
    options = Column(JSON, default=list)
    note = Column(Text, default="")
    status = Column(String(20), default="Pending")  # Pending | Approved | Rejected
    decided_by = Column(String(120), default="")
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=now)
    decided_at = Column(DateTime, nullable=True)
    device = relationship("Device")


try:
    Base.metadata.create_all(engine)
except Exception as e:  # noqa: BLE001 — wrong / unreachable database
    SETUP_PROBLEMS.append(f"Cannot open the database: {type(e).__name__}: {str(e)[:200]}. Check DATABASE_URL in Vercel → Settings → Environment Variables.")


def _seed_admin():
    with SessionLocal() as s:
        if s.scalar(select(User).limit(1)):
            return
        u = User(username=os.environ.get("ADMIN_USER", "admin").lower(), name="Administrator", role="admin")
        pw = os.environ.get("ADMIN_PASSWORD")
        if not pw:
            if ON_VERCEL:
                SETUP_PROBLEMS.append("ADMIN_PASSWORD is not set. Add it in Vercel → Settings → Environment Variables (8+ characters), then Redeploy.")
                return
            pw = secrets.token_urlsafe(9)
            (ROOT / "FIRST_LOGIN.txt").write_text(f"Monitor login → {u.username} / {pw}\n", encoding="utf-8")
        u.set_password(pw)
        s.add(u)
        s.commit()


if not SETUP_PROBLEMS:
    try:
        _seed_admin()
    except Exception as e:  # noqa: BLE001
        SETUP_PROBLEMS.append(f"Database error while creating the admin user: {type(e).__name__}: {str(e)[:200]}")

app = FastAPI(title="ARKVOLT Proposal Monitor")
secret = os.environ.get("SECRET_KEY")
if not secret:
    if ON_VERCEL:
        SETUP_PROBLEMS.append("SECRET_KEY is not set. Add it in Vercel → Settings → Environment Variables (any random text, 40+ characters), then Redeploy.")
        secret = secrets.token_hex(32)
    else:
        kf = ROOT / ".secret.key"
        if not kf.exists():
            kf.write_text(secrets.token_hex(32))
        secret = kf.read_text().strip()
app.add_middleware(SessionMiddleware, secret_key=secret, max_age=60 * 60 * 12, same_site="lax",
                   https_only=bool(os.environ.get("VERCEL")))
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


@app.middleware("http")
async def setup_check(request, call_next):
    """Until the deployment is configured, every page explains what to fix instead of crashing."""
    if SETUP_PROBLEMS and not request.url.path.startswith("/static"):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Monitor not set up", "problems": SETUP_PROBLEMS}, status_code=503)
        items = "".join(f"<li>{p}</li>" for p in SETUP_PROBLEMS)
        return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>Monitor setup</title><link rel=stylesheet href=/static/app.css>
<main style="max-width:720px"><h1>ARKVOLT Monitor — setup needed</h1><div class=warnbox><ol style="margin:0;padding-left:18px">{items}</ol></div>
<p class=muted>After changing settings in Vercel, open <b>Deployments</b> → “…” on the latest one → <b>Redeploy</b>.</p></main>""", status_code=503)
    return await call_next(request)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok" if not SETUP_PROBLEMS else "setup needed: " + " | ".join(SETUP_PROBLEMS)
tpl = Jinja2Templates(directory=ROOT / "app" / "templates")


def _inr(v):
    try:
        n = int(round(float(v or 0)))
    except (TypeError, ValueError):
        return str(v)
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + parts + [tail])
    return ("-" if n < 0 else "") + s


tpl.env.filters["inr"] = _inr
tpl.env.filters["dt"] = lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—"
TYPE_LABEL = {"ongrid": "On-grid", "hybrid": "Hybrid", "offgrid": "Off-grid"}
tpl.env.filters["typ"] = lambda t: ", ".join(TYPE_LABEL.get(x, x) for x in (t or "").split(",") if x)


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class NotLoggedIn(Exception):
    pass


@app.exception_handler(NotLoggedIn)
def _login(request, exc):
    return RedirectResponse("/login", status_code=303)


def me(request: Request, s=Depends(db)) -> User:
    u = s.get(User, request.session.get("uid") or 0)
    if not u or not u.active:
        raise NotLoggedIn()
    return u


def admin(u: User = Depends(me)):
    if u.role != "admin":
        raise HTTPException(403, "Admin only")
    return u


def page(request, name, u=None, **ctx):
    return tpl.TemplateResponse(request, name, {"me": u, **ctx})


# ---------------------------------------------------------------------------------------------
# Device API (staff laptops) — header  X-Device-Token: <token>
# ---------------------------------------------------------------------------------------------
def device(x_device_token: str = Header(""), s=Depends(db)) -> Device:
    d = s.scalar(select(Device).where(Device.token_hash == hashlib.sha256(x_device_token.encode()).hexdigest()))
    if not x_device_token or not d or not d.active:
        raise HTTPException(401, "Unknown or disabled device")
    d.last_seen = now()
    s.commit()
    return d


@app.get("/api/v1/ping")
def ping(d: Device = Depends(device)):
    return {"ok": True, "staff": d.staff_name}


def _opts(options):
    clean = []
    for o in options or []:
        clean.append({k: o.get(k) for k in ("letter", "type", "kw", "kwp", "module", "inverter", "battery", "price",
                                            "package_price", "overridden", "payback_text")})
    return clean


@app.post("/api/v1/proposals")
async def push_proposal(request: Request, d: Device = Depends(device), s=Depends(db)):
    b = await request.json()
    opts = _opts(b.get("options"))
    prices = [float(o["price"]) for o in opts if o.get("price")]
    kws = [float(o["kw"]) for o in opts if o.get("kw")]
    e = s.scalar(select(ProposalEvent).where(ProposalEvent.device_id == d.id, ProposalEvent.local_id == b["local_id"],
                                             ProposalEvent.version == b.get("version", 1)))
    if not e:
        e = ProposalEvent(device_id=d.id, local_id=b["local_id"], version=b.get("version", 1))
        s.add(e)
    e.ref, e.brand, e.customer = b.get("ref", ""), b.get("brand", ""), (b.get("customer") or "")[:200]
    e.place, e.kw_text = (b.get("place") or "")[:120], b.get("kw_text", "")
    e.types = ",".join(sorted({o["type"] for o in opts if o.get("type")}))
    e.max_kw, e.min_price, e.max_price = max(kws or [0]), min(prices or [0]), max(prices or [0])
    e.options, e.status, e.within_rules = opts, b.get("status", "Generated"), bool(b.get("within_rules", True))
    e.updated_at = now()
    s.commit()
    return {"ok": True, "id": e.id}


@app.post("/api/v1/approvals")
async def ask_approval(request: Request, d: Device = Depends(device), s=Depends(db)):
    b = await request.json()
    a = Approval(device_id=d.id, local_id=b.get("local_id"), customer=(b.get("customer") or "")[:200], kw_text=b.get("kw_text", ""),
                 reasons=b.get("reasons") or [], options=_opts(b.get("options")), note=(b.get("note") or "")[:1000])
    s.add(a)
    s.commit()
    return {"id": a.id, "status": a.status}


@app.get("/api/v1/approvals/{aid}")
def approval_status(aid: int, d: Device = Depends(device), s=Depends(db)):
    a = s.get(Approval, aid)
    if not a or a.device_id != d.id:
        raise HTTPException(404)
    return {"id": a.id, "status": a.status, "comment": a.comment, "decided_by": a.decided_by,
            "options": a.options}


# ---------------------------------------------------------------------------------------------
# Dashboard
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
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _filtered(s, staff="", typ="", brand="", start="", end="", q=""):
    st = select(ProposalEvent).join(Device).order_by(ProposalEvent.updated_at.desc())
    if staff:
        st = st.where(Device.staff_name == staff)
    if typ:
        st = st.where(ProposalEvent.types.contains(typ))
    if brand:
        st = st.where(ProposalEvent.brand == brand)
    if start:
        st = st.where(ProposalEvent.created_at >= dt.datetime.fromisoformat(start))
    if end:
        st = st.where(ProposalEvent.created_at < dt.datetime.fromisoformat(end) + dt.timedelta(days=1))
    if q:
        st = st.where(ProposalEvent.customer.ilike(f"%{q}%") | ProposalEvent.ref.ilike(f"%{q}%"))
    return st


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, staff: str = "", typ: str = "", brand: str = "", start: str = "", end: str = "", q: str = "",
              u: User = Depends(me), s=Depends(db)):
    rows = s.scalars(_filtered(s, staff, typ, brand, start, end, q).limit(500)).all()
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    periods = {"Today": today, "Last 7 days": today - dt.timedelta(days=6), "This month": today.replace(day=1)}
    kpis = []
    for label, since in periods.items():
        n, kw = s.execute(select(func.count(ProposalEvent.id), func.coalesce(func.sum(ProposalEvent.max_kw), 0))
                          .where(ProposalEvent.created_at >= since)).one()
        kpis.append((label, n, kw))
    per_staff = s.execute(select(Device.staff_name, func.count(ProposalEvent.id), func.coalesce(func.sum(ProposalEvent.max_kw), 0),
                                 func.coalesce(func.sum(ProposalEvent.max_price), 0), func.max(ProposalEvent.updated_at))
                          .join(ProposalEvent, ProposalEvent.device_id == Device.id, isouter=True)
                          .group_by(Device.staff_name).order_by(Device.staff_name)).all()
    pending = s.scalar(select(func.count(Approval.id)).where(Approval.status == "Pending"))
    staff_names = sorted({d.staff_name for d in s.scalars(select(Device))})
    return page(request, "dashboard.html", u, rows=rows, kpis=kpis, per_staff=per_staff, pending=pending,
                staff_names=staff_names, f=dict(staff=staff, typ=typ, brand=brand, start=start, end=end, q=q))


@app.get("/export.csv")
def export(staff: str = "", typ: str = "", brand: str = "", start: str = "", end: str = "", q: str = "",
           u: User = Depends(me), s=Depends(db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Staff", "Reference", "Brand", "Customer", "Place", "Capacity", "Types", "Options (type kW price)", "Status", "Within rules"])
    for e in s.scalars(_filtered(s, staff, typ, brand, start, end, q)):
        w.writerow([e.created_at.strftime("%d/%m/%Y %H:%M"), e.device.staff_name, e.ref, e.brand, e.customer, e.place, e.kw_text,
                    e.types, "; ".join(f"{o.get('letter')}: {o.get('type')} {o.get('kw')} kW ₹{_inr(o.get('price'))}" for o in e.options or []),
                    e.status, "yes" if e.within_rules else "no"])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=proposals_{now():%Y%m%d}.csv"})


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, u: User = Depends(me), s=Depends(db)):
    pend = s.scalars(select(Approval).where(Approval.status == "Pending").order_by(Approval.created_at)).all()
    done = s.scalars(select(Approval).where(Approval.status != "Pending").order_by(Approval.decided_at.desc()).limit(100)).all()
    return page(request, "approvals.html", u, pend=pend, done=done)


@app.post("/approvals/{aid}")
def decide(aid: int, decision: str = Form(...), comment: str = Form(""), u: User = Depends(me), s=Depends(db)):
    a = s.get(Approval, aid)
    if not a or a.status != "Pending" or decision not in ("Approved", "Rejected"):
        raise HTTPException(400)
    a.status, a.comment, a.decided_by, a.decided_at = decision, comment.strip(), u.name or u.username, now()
    s.commit()
    return RedirectResponse("/approvals", status_code=303)


@app.get("/devices", response_class=HTMLResponse)
def devices(request: Request, new_token: str = "", u: User = Depends(admin), s=Depends(db)):
    return page(request, "devices.html", u, devices=s.scalars(select(Device).order_by(Device.staff_name)).all(),
                users=s.scalars(select(User).order_by(User.username)).all(), new_token=new_token,
                base=str(request.base_url).rstrip("/"))


@app.post("/devices")
def add_device(request: Request, staff_name: str = Form(...), label: str = Form(""), u: User = Depends(admin), s=Depends(db)):
    token = secrets.token_urlsafe(24)
    s.add(Device(staff_name=staff_name.strip(), label=label.strip(), token_hash=hashlib.sha256(token.encode()).hexdigest()))
    s.commit()
    # shown once, on this page only; it is never stored in plain text
    return page(request, "devices.html", u, devices=s.scalars(select(Device).order_by(Device.staff_name)).all(),
                users=s.scalars(select(User).order_by(User.username)).all(), new_token=token, new_staff=staff_name.strip(),
                base=str(request.base_url).rstrip("/"))


@app.post("/devices/{did}/toggle")
def toggle_device(did: int, u: User = Depends(admin), s=Depends(db)):
    d = s.get(Device, did)
    d.active = not d.active
    s.commit()
    return RedirectResponse("/devices", status_code=303)


@app.post("/users")
def save_user(username: str = Form(...), name: str = Form(""), role: str = Form("manager"), password: str = Form(""),
              active: str = Form(""), u: User = Depends(admin), s=Depends(db)):
    uname = username.strip().lower()
    x = s.scalar(select(User).where(User.username == uname)) or User(username=uname)
    if not x.id and len(password) < 8:
        raise HTTPException(400, "New users need a password of at least 8 characters")
    x.name, x.role, x.active = name, role if role in ("admin", "manager") else "manager", bool(active)
    if password:
        x.set_password(password)
    if x.id == u.id and (x.role != "admin" or not x.active):
        raise HTTPException(400, "You cannot remove your own admin access")
    s.add(x)
    s.commit()
    return RedirectResponse("/devices", status_code=303)


@app.post("/account")
def change_pw(old: str = Form(...), new: str = Form(...), u: User = Depends(me), s=Depends(db)):
    x = s.get(User, u.id)
    if not x.check_password(old) or len(new) < 8:
        raise HTTPException(400, "Current password wrong, or new password shorter than 8 characters")
    x.set_password(new)
    s.commit()
    return RedirectResponse("/", status_code=303)
