"""Account page: everyone can edit their name, username, password and the laptop's dashboard key."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "a.db"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import monitor  # noqa: E402
from app.db import SessionLocal, Setting, User  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)


def make(username, role, pw="pass-1234"):
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == username)) or User(username=username)
        u.name, u.role, u.active = username.title(), role, True
        u.set_password(pw)
        s.add(u)
        s.commit()


def login(username, pw="pass-1234"):
    c.get("/logout")
    return c.post("/login", data={"username": username, "password": pw}, follow_redirects=False)


def setting(k):
    with SessionLocal() as s:
        x = s.get(Setting, k)
        return x.value if x else None


def test_staff_edits_profile_and_dashboard_key(monkeypatch):
    make("meera", "sales")
    make("taken", "sales")
    login("meera")
    assert "My account" in c.get("/account").text

    # name change needs no password; username change needs the current one and must be free
    r = c.post("/account/profile", data={"name": "Meera Hari", "username": "meera"})
    assert "Profile saved" in r.text
    assert "already taken" in c.post("/account/profile", data={"name": "Meera Hari", "username": "taken", "password": "pass-1234"}).text
    assert "current password" in c.post("/account/profile", data={"name": "Meera Hari", "username": "meera.h"}).text
    assert "meera.h" in c.post("/account/profile", data={"name": "Meera Hari", "username": "meera.h", "password": "pass-1234"}).text
    assert login("meera.h").status_code == 303

    # staff may enter / replace the device key (tested right away) but not change the dashboard address
    url_before = setting("monitor_url")
    monkeypatch.setattr(monitor, "ping", lambda cfg: "Meera Hari" if cfg["key"] == "good-key" else (_ for _ in ()).throw(RuntimeError("The monitor refused this device key.")))
    r = c.post("/account/monitor", data={"monitor_url": "https://evil.example", "monitor_key": "good-key"})
    assert "Connected to the online dashboard as Meera Hari" in r.text
    assert setting("monitor_key") == "good-key" and setting("monitor_url") == url_before
    assert "good-key" not in c.get("/account").text  # the saved key is never shown again
    r = c.post("/account/monitor", data={"monitor_key": ""})  # blank keeps the key, just tests
    assert setting("monitor_key") == "good-key" and "Connected" in r.text
    assert "refused" in c.post("/account/monitor", data={"monitor_key": "bad"}).text

    # password change
    assert "not the same" in c.post("/account", data={"old": "pass-1234", "new": "newpass1", "new2": "other12"}).text
    assert "Password changed" in c.post("/account", data={"old": "pass-1234", "new": "newpass1", "new2": "newpass1"}).text
    assert login("meera.h", "newpass1").status_code == 303


def test_admin_changes_dashboard_address_from_settings(monkeypatch):
    make("boss", "admin")
    login("boss")
    monkeypatch.setattr(monitor, "ping", lambda cfg: "Owner")
    r = c.post("/account/monitor", data={"next": "/admin?tab=settings", "monitor_url": "https://dash.example/", "monitor_key": "k2"})
    assert "Online dashboard" in r.text and "Connected" in r.text and "Staff rules" in r.text
    assert setting("monitor_url") == "https://dash.example" and setting("monitor_key") == "k2"
    assert "k2" not in c.get("/admin?tab=settings").text
