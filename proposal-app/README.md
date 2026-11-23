# Solar Proposal Generator

Web app that turns a KSEB bill into a finished, branded PDF proposal — **Adroit Universe** (LT-1 domestic, PM Surya Ghar subsidy) or **ARKVOLT Energy** (commercial, no subsidy) — following `Solar_Proposal_Generator_Spec.md`.

## Start

1. Double-click **`Start Proposal App.bat`** (or run `py -3.12 run.py`).
2. Open **http://localhost:8520**. Other PCs on the office network: `http://<this-PC's-IP>:8520`.
3. First run creates an admin user; the password is in `data\FIRST_LOGIN.txt`. Log in, change it under **Account**, then add your sales team under **Admin → Users & roles**.

One-time setup on a new PC: `py -3.12 -m pip install -r requirements.txt` and `py -3.12 -m playwright install chromium` (Microsoft Edge is used automatically if installed).

## How sales uses it (about 5 minutes per quote)

| Step | What happens |
|---|---|
| 1 Customer | Name, address, KSEB-registered mobile. "Blank (common) quote" leaves the To-block empty. |
| 2 Bill & feasibility | **Bill photo** → reads the consumer number (needs Tesseract, below). **Assisted KSEB fetch** → copy consumer no. + mobile, open KSEB *Know Your Bill*, type the CAPTCHA, download the PDF, drop it on **Bill PDF** → tariff, phase, connected load, units by time zone and amount fill in. **Transformer feasibility** → open KSEB *RE capacity* page, enter the balance kW shown. The **Recommendation** card suggests the plant size and best option with reasons. |
| 3 Options | 1–4 options; quick picks, packages, panel/inverter/battery; string design checked live (green / amber / red). |
| 4 Prices | Package prices; overrides need a reason; more than the allowed % below package price needs manager approval. |
| 5 Review & generate | Warnings to acknowledge, extra clarifications, live page preview, **Generate PDF** (≈ 5–10 s, datasheets + contact page appended). |

KSEB protects *Know Your Bill* with a CAPTCHA and the RE-capacity page with a Cloudflare bot check, so a person types those codes; the app does everything else. It does not try to get around those protections.

## What's where

| Path | Purpose |
|---|---|
| `app/engine.py` | Calculation engine (§6) — pure functions |
| `app/compute.py` | Brand rules, options, texts, BOM, warnings, advisor |
| `app/billparse.py` | KSEB bill PDF reader + receipt-photo OCR |
| `app/pdfgen.py`, `app/templates/pdf/` | HTML → PDF (Adroit + ARKVOLT), datasheet merge, overflow check |
| `app/static/brand/adroit/` | Frame, cover, diagrams, timeline — taken from the approved reference proposal by `tools/extract_assets.py` |
| `app/main.py`, `app/templates/`, `app/static/wizard.js` | Web app: login, dashboard, wizard, versions, approvals, admin |
| `data/` | `app.db` (SQLite), generated PDFs, uploaded bills, datasheets — **back this folder up** |
| `tests/` | §11 acceptance tests + rule tests: `py -3.12 -m pytest` |

Everything an admin maintains (company & bank, products & datasheets, packages & prices, versioned tariffs and constants, template text, users, reference numbering) is under **Admin**. Tariffs and constants are versioned with an effective date, and each generated PDF stores the exact tariff, rules and products it used.

## Reading bill photos (optional)

Install the free Tesseract OCR program (UB Mannheim build, to `C:\Program Files\Tesseract-OCR`) and `py -3.12 -m pip install pytesseract`. Without it the app still works; the salesperson types the consumer number.

## Notes on the spec

* §6.7 hybrid battery: the formula as written does not give the spec's own §11.2 figures. The engine uses: the battery charges from midday surplus up to usable kWh × 30, delivers 90% of that in the evening, and the charged energy is not exported. That reproduces 5,396 units used on site and ₹65,682 saving exactly.
* §11 acceptance numbers all reproduce within ±₹1 / ±0.05 yr.
* Still to be decided by the business (§12): Adroit GSTIN, KSEB charge standard, 3 kW Hybrid Statcon price (₹3,08,000 placeholder), Avaada DCR, reference starting numbers, signatories, discount limit (default 5%), ARKVOLT packages. All are editable in Admin.
* Fonts (Poppins, Plus Jakarta Sans) load from Google Fonts, so the PC needs internet when generating PDFs.
