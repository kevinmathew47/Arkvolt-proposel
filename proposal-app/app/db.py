"""Database (SQLite file in data/; SQLAlchemy so it can move to PostgreSQL by changing DATABASE_URL)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from . import catalog
from .engine import INF, Rules, default_tariffs

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
for sub in ("", "pdfs", "uploads", "datasheets"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def now():
    return dt.datetime.now()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True, nullable=False)
    name = Column(String(120), default="")
    role = Column(String(20), default="sales")  # sales | manager | admin
    pw_hash = Column(String(200), nullable=False)
    active = Column(Boolean, default=True)

    def set_password(self, pw: str):
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
        self.pw_hash = f"pbkdf2${salt}${h}"

    def check_password(self, pw: str) -> bool:
        try:
            _, salt, h = self.pw_hash.split("$")
        except ValueError:
            return False
        return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex(), h)


class Company(Base):
    __tablename__ = "companies"
    brand = Column(String(20), primary_key=True)  # ADROIT | ARKVOLT
    data = Column(JSON, default=dict)


class Product(Base):
    __tablename__ = "products"
    code = Column(String(40), primary_key=True)
    category = Column(String(20))  # module | inverter | battery
    data = Column(JSON, default=dict)
    datasheet = Column(String(200), default="")  # file name in data/datasheets
    almm = Column(Boolean, default=True)
    active = Column(Boolean, default=True)


class Package(Base):
    __tablename__ = "packages"
    id = Column(Integer, primary_key=True)
    brand = Column(String(20))
    name = Column(String(120))
    kw = Column(Float)
    type = Column(String(10))  # ongrid | hybrid
    module = Column(String(40))
    qty = Column(Integer)
    inverter = Column(String(40))
    battery = Column(String(40), nullable=True)
    price = Column(Float)
    note = Column(String(200), default="")
    active = Column(Boolean, default=True)


class TariffVersion(Base):
    __tablename__ = "tariffs"
    id = Column(Integer, primary_key=True)
    code = Column(String(20))
    name = Column(String(60))
    valid_from = Column(String(10))
    valid_to = Column(String(10), default="")
    source = Column(String(200), default="")
    data = Column(JSON)


class RuleVersion(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    valid_from = Column(String(10))
    note = Column(String(200), default="")
    data = Column(JSON)


class TemplateText(Base):
    __tablename__ = "template_text"
    id = Column(Integer, primary_key=True)
    brand = Column(String(20))
    key = Column(String(60))
    text = Column(Text)
    __table_args__ = (UniqueConstraint("brand", "key"),)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(60), primary_key=True)
    value = Column(String(400))


class RefCounter(Base):
    __tablename__ = "ref_counters"
    id = Column(Integer, primary_key=True)
    brand = Column(String(20))
    fy = Column(String(10))
    last = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("brand", "fy"),)


class Proposal(Base):
    __tablename__ = "proposals"
    id = Column(Integer, primary_key=True)
    ref_base = Column(String(60), default="")      # assigned on first PDF; never reused
    brand = Column(String(20))
    status = Column(String(20), default="Draft")   # Draft | Generated | Sent | Won | Lost
    customer_name = Column(String(160), default="")
    consumer_no = Column(String(20), default="")
    summary = Column(String(200), default="")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    versions = relationship("ProposalVersion", back_populates="proposal", order_by="ProposalVersion.version_no")
    owner = relationship("User")


class ProposalVersion(Base):
    __tablename__ = "proposal_versions"
    id = Column(Integer, primary_key=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"))
    version_no = Column(Integer, default=1)
    ref = Column(String(60), default="")
    inputs = Column(JSON)          # everything the user entered
    snapshot = Column(JSON)        # tariff + rules + products used (reproduce old quotes exactly)
    results = Column(JSON)         # key numbers per option
    pdf_path = Column(String(300), default="")
    needs_approval = Column(Boolean, default=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=now)
    proposal = relationship("Proposal", back_populates="versions")


class Outbox(Base):
    """Reports waiting to be sent to the online monitor (laptop offline)."""
    __tablename__ = "outbox"
    id = Column(Integer, primary_key=True)
    path = Column(String(80))
    payload = Column(JSON)
    tries = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


class Audit(Base):
    __tablename__ = "audit"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now)
    user_id = Column(Integer, nullable=True)
    action = Column(String(60))
    detail = Column(Text, default="")


def audit(s: Session, user_id, action, detail=""):
    s.add(Audit(user_id=user_id, action=action, detail=detail if isinstance(detail, str) else json.dumps(detail, default=str)))


# ---------------------------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------------------------
def _slabs(sl):
    return [[None if u == INF else u, r] for u, r in sl]


DEFAULT_TEXT = {
    ("ADROIT", "about"): (
        "Adroit Universe Pvt. Ltd. is a Kerala-based private limited company that designs, supplies, installs and "
        "maintains rooftop solar power plants for homes and businesses across Kerala.\n\n"
        "We handle the complete project — site survey, engineering, KSEB feasibility and net-meter registration, "
        "installation, commissioning and the PM Surya Ghar subsidy process — so you deal with a single point of "
        "contact from enquiry to switch-on."),
    ("ADROIT", "why"): json.dumps([
        ["One-stop EPC", "Design, supply, installation, KSEB liaison and subsidy filing under one contract."],
        ["Quality equipment", "ALMM-listed DCR modules from Statcon Energiaa and Avaada, BIS-approved inverters and LFP battery — made in India."],
        ["Safe engineering", "Design to IS/IEC standards with proper earthing, surge and lightning protection."],
        ["Long-term care", "5 years of free maintenance support and remote monitoring after commissioning."]]),
    ("ADROIT", "terms"): json.dumps([
        ["Validity of offer", "30 days from the date of this proposal."],
        ["Prices", "Firm for the validity period and the scope stated; KSEB charges extra."],
        ["GST", "Included at current rates; any change at invoicing will be adjusted."],
        ["Other taxes / levies", "Any new statutory tax or levy at invoicing will be charged extra."],
        ["Operation & maintenance", "Free maintenance visits for 5 years from commissioning. Customer cleans module glass as needed, early morning or evening, with a soft cloth and plain water."],
        ["Battery care", "Installed indoors, away from water and direct sun; to be removed or opened only by authorised personnel."],
        ["Force majeure", "Delays due to KSEB approvals, weather or events beyond our control are excluded from the timeline."],
        ["Jurisdiction", "Courts at Ernakulam, Kerala."]]),
    ("ADROIT", "scope_ours"): json.dumps([
        "Site survey, engineering drawings and structure design as per relevant IS/IEC standards.",
        "Supply of modules, inverter{battery}, structure, cables and balance of system.",
        "Installation of structure, modules, inverter, {battery_comma}AC/DC cabling, DCDB, ACDB, earthing and lightning arrester.",
        "Configuration of remote monitoring and the customer app.",
        "Testing, commissioning{hyb_backup}.",
        "KSEB registration, feasibility, net-metering documents{subsidy_docs}."]),
    ("ADROIT", "scope_customer"): json.dumps([
        "Safe storage space for materials during installation.",
        "Shadow-free roof space of about {roof} sq. ft. with safe access, and wall space for inverter{battery_wall}.",
        "Approval of design and layout drawings within 7 days of submission.",
        "Documents for KSEB{and_subsidy}: electricity bill, ID proof, bank details and ownership proof.",
        "Power and water during installation, and Wi-Fi for remote monitoring.",
        "{hyb:Separation of essential loads in house wiring, if not already done (quoted separately).}"]),
    ("ARKVOLT", "about"): (
        "ARKVOLT Energy Pvt. Ltd. designs, supplies and commissions solar, battery storage and hybrid energy systems "
        "for businesses, institutions and homes across Kerala, with engineering, KSEB liaison and after-sales support "
        "from one team."),
    ("ARKVOLT", "terms"): json.dumps([
        ["Validity of offer", "30 days from the date of this proposal."],
        ["Prices", "Firm for the validity period and the scope stated; KSEB charges extra at actuals."],
        ["GST", "Included at current rates. Input tax credit may be available to GST-registered businesses."],
        ["Tax benefit", "Solar plant qualifies for 40% accelerated depreciation (WDV) for taxable businesses — confirm with your tax adviser."],
        ["Operation & maintenance", "Free maintenance visits for 5 years from commissioning."],
        ["Force majeure", "Delays due to KSEB approvals, weather or events beyond our control are excluded from the timeline."],
        ["Jurisdiction", "Courts at Thiruvananthapuram, Kerala."]]),
}

DEFAULT_SETTINGS = {
    "max_discount_pct": "5",
    "rate_per_kw": "{}",
    "monitor_url": "",              # online monitor address, e.g. https://arkvolt-monitor.vercel.app
    "monitor_key": "",              # this laptop's device key (from the monitor's Staff & devices page)
    "staff_max_kw": "5",            # staff may quote up to this size at standard prices without approval            # custom-size price per kW; empty = derived from the standard packages        # override more than this % below package price needs manager approval
    "timeline": json.dumps([["Up to 15 days", "Registration & KSEB feasibility"], ["7 days", "Engineering, procurement & supply"],
                            ["3–5 days", "Solar plant installation"], ["Up to 15 days", "Commissioning, testing & net metering"]]),
}

DATASHEETS = {"STC550": "statcon_modules.pdf", "STC600": "statcon_modules.pdf", "AVD720": "avaada_modules.pdf",
              "X3T": "statcon_ongrid_inverter.pdf", "X3.6T": "statcon_ongrid_inverter.pdf", "X4T": "statcon_ongrid_inverter.pdf",
              "X5T": "statcon_ongrid_inverter.pdf", "X5.5T": "statcon_ongrid_inverter.pdf",
              "BHF3K": "statcon_hybrid_inverter.pdf", "BHF5K": "statcon_hybrid_inverter.pdf", "MIRAKAL": "statcon_mirakal_battery.pdf"}


def init_db() -> str | None:
    """Create tables and seed defaults. Returns a first-login note if an admin user was just created."""
    Base.metadata.create_all(engine)
    note = None
    with SessionLocal() as s:
        if not s.scalar(select(User).limit(1)):
            pw = secrets.token_urlsafe(9)
            u = User(username="admin", name="Administrator", role="admin")
            u.set_password(pw)
            s.add(u)
            note = f"First login → username: admin   password: {pw}   (change it in Admin → Users)"
            (DATA / "FIRST_LOGIN.txt").write_text(note + "\n", encoding="utf-8")
        if not s.scalar(select(Company).limit(1)):
            for b, d in catalog.COMPANIES.items():
                s.add(Company(brand=b, data=d))
        if not s.scalar(select(Product).limit(1)):
            for cat, items in (("module", catalog.MODULES), ("inverter", catalog.INVERTERS), ("battery", catalog.BATTERIES)):
                for code, obj in items.items():
                    s.add(Product(code=code, category=cat, data=dict(obj.__dict__), datasheet=DATASHEETS.get(code, "")))
        if not s.scalar(select(Package).limit(1)):
            for b, name, kw, typ, mod, qty, inv, bat, price, note_ in catalog.PACKAGES:
                s.add(Package(brand=b, name=name, kw=kw, type=typ, module=mod, qty=qty, inverter=inv, battery=bat,
                              price=price, note=note_))
        if not s.scalar(select(TariffVersion).limit(1)):
            for code, t in default_tariffs().items():
                s.add(TariffVersion(code=code, name=t.name, valid_from="2025-04-01", source="KSERC tariff order 2025–27 (spec §5.4)",
                                    data=dict(telescopic=_slabs(t.telescopic), telescopic_upto=t.telescopic_upto,
                                              flat=_slabs(t.flat), duty=t.duty, fixed_per_kw=t.fixed_per_kw, domestic=t.domestic)))
        if not s.scalar(select(RuleVersion).limit(1)):
            r = Rules()
            s.add(RuleVersion(valid_from="2025-04-01", note="Spec §5.5 defaults",
                              data={k: list(v) if isinstance(v, tuple) else v for k, v in r.__dict__.items()}))
        for (b, k), v in DEFAULT_TEXT.items():
            if not s.scalar(select(TemplateText).where(TemplateText.brand == b, TemplateText.key == k)):
                s.add(TemplateText(brand=b, key=k, text=v))
        for k, v in DEFAULT_SETTINGS.items():
            if not s.get(Setting, k):
                s.add(Setting(key=k, value=v))
        s.commit()
    return note
