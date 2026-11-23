import os
import re
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "m.db")
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
os.environ["SECRET_KEY"] = "x" * 32

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)


def login():
    r = c.post("/login", data={"username": "admin", "password": "test-admin-pw"}, follow_redirects=False)
    assert r.status_code == 303


def new_device(name):
    r = c.post("/devices", data={"staff_name": name, "label": "laptop"})
    return re.search(r'word-break:break-all">([^<]+)<', r.text).group(1)


def test_full_flow():
    assert c.get("/", follow_redirects=False).status_code == 303  # needs login
    login()
    tok = new_device("Ebinraj B")
    H = {"X-Device-Token": tok}
    assert c.get("/api/v1/ping", headers=H).json()["staff"] == "Ebinraj B"
    assert c.get("/api/v1/ping", headers={"X-Device-Token": "wrong"}).status_code == 401

    p = {"local_id": 7, "version": 1, "ref": "AUPL/SOL/ONG/2026-27/010", "brand": "ADROIT", "customer": "Mr. Suresh Kumar",
         "place": "Kowdiar", "kw_text": "3 kW", "status": "Generated", "within_rules": True,
         "options": [{"letter": "A", "type": "ongrid", "kw": 3, "price": 205000, "package_price": 205000, "phone": "9999999999"}]}
    assert c.post("/api/v1/proposals", json=p, headers=H).json()["ok"]
    p["status"] = "Sent"
    c.post("/api/v1/proposals", json=p, headers=H)  # same proposal updated, not duplicated
    html = c.get("/").text
    assert "Mr. Suresh Kumar" in html and "Ebinraj B" in html and html.count("AUPL/SOL/ONG/2026-27/010") == 1
    assert "9999999999" not in html  # only whitelisted option fields are kept
    csv = c.get("/export.csv").text
    assert "Suresh Kumar" in csv and "Sent" in csv

    a = c.post("/api/v1/approvals", headers=H, json={"local_id": 8, "customer": "M/s. Big Mall", "kw_text": "10 kW",
                                                     "reasons": ["Above 5 kW"], "options": [{"letter": "A", "type": "hybrid", "kw": 10, "price": 800000}]}).json()
    assert a["status"] == "Pending"
    assert "M/s. Big Mall" in c.get("/approvals").text
    c.post(f"/approvals/{a['id']}", data={"decision": "Approved", "comment": "OK for this customer"})
    st = c.get(f"/api/v1/approvals/{a['id']}", headers=H).json()
    assert st["status"] == "Approved" and st["comment"] == "OK for this customer"

    other = new_device("Meera Hari B")
    assert c.get(f"/api/v1/approvals/{a['id']}", headers={"X-Device-Token": other}).status_code == 404  # not their request

    dev_id = re.findall(r'action="/devices/(\d+)/toggle"', c.get("/devices").text)[0]
    c.post(f"/devices/{dev_id}/toggle")
    assert c.get("/api/v1/ping", headers=H).status_code == 401  # disabled device is cut off
