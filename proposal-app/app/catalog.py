"""Default master data from the spec (§5.2, §5.3). Seeds the database; admin edits it afterwards."""
from .engine import Battery, Inverter, Module

MODULES = {
    "STC550": Module("STC550", "Statcon Energiaa", "Mono PERC bifacial 144 half-cut 550 Wp", 550, 42.05, 13.08, 49.88, 14.11,
                     21.29, -0.26, 0.025, 0.007, dcr=True,
                     short="550 Wp Statcon Energiaa Mono PERC bifacial (DCR), 144 half-cut cells, 21.29% eff.",
                     warranty="10-yr product, 25-yr linear performance"),
    "STC600": Module("STC600", "Statcon Energiaa", "TOPCon N-type bifacial 600 Wp", 600, 44.28, 13.57, 51.55, 14.14,
                     23.22, -0.26, 0.01, 0.004, dcr=True,
                     short="600 Wp Statcon Energiaa TOPCon N-type bifacial (DCR), 23.22% eff.",
                     warranty="10-yr product, 25-yr linear performance (≥ 80.8%)"),
    "AVD720": Module("AVD720", "Avaada Electro", "AVN66G12G N-type TOPCon bifacial glass-to-glass 720 Wp", 720, 41.3, 17.43,
                     49.0, 18.48, 23.18, -0.25, 0.01, 0.004, dcr=True,  # DCR to be confirmed (spec §12.4)
                     short="720 Wp Avaada N-type TOPCon bifacial, glass-to-glass (DCR), 23.18% eff.",
                     warranty="12-yr workmanship, 30-yr linear performance (≥ 87.4%)"),
}

INVERTERS = {
    "X3T": Inverter("X3T", "Statcon Energiaa", "X3T", 3, False, 450, 100, 450, 1, 16, 3.6,
                    short="3 kW single-phase on-grid, IP65, >97.5% eff.", warranty="8 years"),
    "X3.6T": Inverter("X3.6T", "Statcon Energiaa", "X3.6T", 3.6, False, 550, 100, 550, 1, 21, 4.4,
                      short="3.6 kW single-phase on-grid", warranty="8 years"),
    "X4T": Inverter("X4T", "Statcon Energiaa", "X4T", 4, False, 550, 100, 550, 1, 21, 4.8,
                    short="4 kW single-phase on-grid", warranty="8 years"),
    "X5T": Inverter("X5T", "Statcon Energiaa", "X5T", 5, False, 550, 100, 550, 1, 21, 6,
                    short="5 kW single-phase on-grid, IP65, >97.5% eff.", warranty="8 years"),
    "X5.5T": Inverter("X5.5T", "Statcon Energiaa", "X5.5T", 5.5, False, 550, 100, 550, 1, 21, 6.6,
                      short="5.5 kW single-phase on-grid", warranty="8 years"),
    "BHF3K": Inverter("BHF3K", "Statcon Energiaa", "BOXER HF BHF-048-3K000-1P-6M2", 3, True, 500, 200, 450, 1, 20, 6,
                      short="3 kW, 48 V high-frequency hybrid, 1 MPPT", warranty="As per Statcon OEM terms"),
    "BHF5K": Inverter("BHF5K", "Statcon Energiaa", "BOXER HF BHF-048-5K000-1P-10M2", 5, True, 500, 200, 450, 2, 20, 10,
                      short="5 kW, 48 V high-frequency hybrid, 2 MPPT", warranty="As per Statcon OEM terms"),
}

BATTERIES = {
    "MIRAKAL": Battery("MIRAKAL", "Statcon Energiaa", "MIRAKAL 51.2 V / 100 Ah LFP", 5.12, 4.6,
                       short="51.2 V / 100 Ah LFP, 5.12 kWh, BMS, ≥ 3,000 cycles", warranty="As per Statcon OEM terms"),
}

# brand, name, plant kW, type, module, qty, inverter, battery, price (incl. GST, excl. KSEB), note
PACKAGES = [
    ("ADROIT", "3 kW On-grid · Statcon", 3, "ongrid", "STC550", 6, "X3T", None, 205000, ""),
    ("ADROIT", "3 kW On-grid · Avaada", 3, "ongrid", "AVD720", 5, "X3T", None, 220000, ""),
    ("ADROIT", "5 kW On-grid · Statcon", 5, "ongrid", "STC550", 10, "X5T", None, 300000, ""),
    ("ADROIT", "5 kW On-grid · Avaada", 5, "ongrid", "AVD720", 8, "X5T", None, 320000, ""),
    ("ADROIT", "3 kW Hybrid · Statcon", 3, "hybrid", "STC550", 6, "BHF3K", "MIRAKAL", 308000, "Price to be confirmed"),
    ("ADROIT", "3 kW Hybrid · Avaada", 3, "hybrid", "AVD720", 5, "BHF3K", "MIRAKAL", 320000, ""),
    ("ADROIT", "5 kW Hybrid · Statcon", 5, "hybrid", "STC550", 10, "BHF5K", "MIRAKAL", 420000, ""),
    ("ADROIT", "5 kW Hybrid · Avaada", 5, "hybrid", "AVD720", 8, "BHF5K", "MIRAKAL", 440000, ""),
]

# Fixed balance-of-system BOM lines (§5.2). hybrid_only lines are dropped for on-grid options.
BOS = [
    ("Mounting structure", "Adroit", "Elevated hot-dip galvanised GI", "1 Set", False),
    ("DC cables & conduits", "Polycab / Havells / KEI", "4 sq. mm solar DC cable, UV-resistant, MC4", "As reqd.", False),
    ("Battery cables & protection", "Adroit", "DC MCB / fuse between battery and inverter", "1 Set", True),
    ("AC cables", "Polycab / Havells / KEI", "Copper, FR-LSH", "As reqd.", False),
    ("DCDB / ACDB", "Adroit", "DC isolator, fuse, MCB / RCCB, SPD, IP65 box", "1 each", False),
    ("Earthing & LA", "Maintenance-free chemical / ESE", "Separate pits for DC, AC & lightning arrester", "3 + LA", False),
    ("Net & generation meter", "Genus / Secure / L&T", "KSEB-approved bi-directional", "As per KSEB", False),
    ("Remote monitoring", "Statcon Wi-Fi / GPRS", "Generation (and battery) data on app", "1 No.", False),
]

COMPANIES = {
    "ADROIT": dict(
        legal_name="Adroit Universe Pvt. Ltd.", short_name="Adroit Universe",
        address="Ground Floor, Regency Arcade, Jawahar Nagar, Kowdiar, Thiruvananthapuram, Keralam – 695003",
        phone="", email="", website="",
        gstin="27ABGCA4889D1ZF",  # spec §12.1: confirm 27ABGCA4889D1ZF vs 32ABDCA2447E1Z7
        pmsg_reg="", kseb_licence="",
        bank_name="IDFC FIRST Bank Ltd", account_no="10236269923", ifsc="IDFB0080514", branch="M.G. Road, Kochi",
        ref_prefix="AUPL", signatory_name="", signatory_designation="Authorised Signatory",
        primary="#1b69a8", constitution="Private Limited Company"),
    "ARKVOLT": dict(
        legal_name="ARKVOLT Energy Pvt. Ltd.", short_name="ARKVOLT",
        address="Ground Floor, Regency Arcade, Jawahar Nagar, Kowdiar, Thiruvananthapuram, Keralam – 695003",
        phone="+91 97410 01777", email="info@arkvoltenergy.com", website="www.arkvoltenergy.com",
        gstin="", pmsg_reg="", kseb_licence="",
        bank_name="", account_no="", ifsc="", branch="",
        ref_prefix="ARK", signatory_name="", signatory_designation="Authorised Signatory",
        primary="#80C200", constitution="Private Limited Company",
        head_office="1st Floor / 102, C-38-39, Parinee Crescenzo, BKC, Bandra East, Mumbai – 400051"),
}
