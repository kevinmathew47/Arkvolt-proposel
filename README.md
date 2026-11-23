# ARKVOLT Proposal System

| Folder | What it is | Where it runs |
|---|---|---|
| [`proposal-app/`](proposal-app/) | Solar Proposal Generator — wizard, calculations, branded PDF proposals (Adroit / ARKVOLT) | Each staff Windows laptop (`Start Proposal App.bat`) |
| [`arkvolt-monitor/`](arkvolt-monitor/) | Live monitor — every staff proposal, totals per staff, online approvals, staff devices | Vercel (Root Directory: `arkvolt-monitor`) + Postgres |

Customer data (database, generated PDFs, uploaded bills) and secrets are never committed — see each folder's `.gitignore`.
