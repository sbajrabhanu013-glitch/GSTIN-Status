"""
GSTIN Customer Dashboard - No API Version
-----------------------------------------
This Streamlit app does NOT ask for Sandbox/API credentials.

What it can do without any API:
1. Validate GSTIN format and checksum offline.
2. Show the GST state from the first 2 digits of GSTIN.
3. Open the official GST taxpayer search page for manual lookup.
4. Let you paste/enter business details, filing table, and filing frequency.
5. Build a clean dashboard and export CSV files.

Important limitation:
Legal Name, Trade Name, Constitution, GSTIN Status, Return Filing Table, and
Filing Frequency cannot be calculated from GSTIN alone. They must come from the
GST portal or an authorized data provider. This app keeps the workflow API-free
by making the official portal lookup manual.

Run:
    pip install -r requirements_no_api.txt
    streamlit run gstin_no_api_streamlit_app.py
"""

from __future__ import annotations

import re
from datetime import date
from io import StringIO
from typing import Dict, Tuple

import pandas as pd
import streamlit as st


# -----------------------------
# Page config
# -----------------------------
st.set_page_config(
    page_title="GSTIN Dashboard - No API",
    page_icon="🧾",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main {
        background: linear-gradient(135deg, #f7fff8 0%, #effdf5 52%, #f4fbff 100%);
    }
    .block-container { padding-top: 1.4rem; }
    .hero-card {
        padding: 1.25rem 1.45rem;
        border-radius: 24px;
        background: linear-gradient(135deg, #e9ffe9 0%, #ecfff8 45%, #eef8ff 100%);
        border: 1px solid rgba(0, 145, 80, 0.15);
        box-shadow: 0 10px 28px rgba(0,0,0,0.06);
        margin-bottom: 1rem;
    }
    .hero-card h1 {
        margin: 0;
        color: #10391f;
        font-size: 2.05rem;
    }
    .hero-card p {
        margin: .45rem 0 0 0;
        color: #4c6759;
        font-size: 1rem;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid rgba(0, 145, 80, 0.12);
        border-radius: 18px;
        padding: 0.85rem;
        box-shadow: 0 8px 20px rgba(0,0,0,0.045);
    }
    .info-box {
        padding: 1rem;
        border-radius: 16px;
        background: #ffffff;
        border: 1px solid rgba(0, 145, 80, 0.13);
        box-shadow: 0 8px 20px rgba(0,0,0,0.04);
        margin: .75rem 0;
    }
    .warn-box {
        padding: 1rem;
        border-radius: 16px;
        background: #fff9e8;
        border: 1px solid #eed384;
        color: #614700;
        margin: .75rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# GSTIN helpers
# -----------------------------
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STATE_CODES: Dict[str, str] = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman & Diu",
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh - Old",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}


def normalize_gstin(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def gstin_checksum_char(first_14: str) -> str:
    factor = 2
    total = 0
    modulus = 36

    for ch in reversed(first_14):
        code_point = GSTIN_CHARS.index(ch)
        addend = factor * code_point
        factor = 1 if factor == 2 else 2
        addend = (addend // modulus) + (addend % modulus)
        total += addend

    remainder = total % modulus
    check_code_point = (modulus - remainder) % modulus
    return GSTIN_CHARS[check_code_point]


def validate_gstin(gstin: str) -> Tuple[bool, str]:
    gstin = normalize_gstin(gstin)
    if len(gstin) != 15:
        return False, "GSTIN must be exactly 15 characters."
    if not GSTIN_REGEX.match(gstin):
        return False, "GSTIN format is invalid. Example format: 29ABCDE1234F1Z5"
    expected = gstin_checksum_char(gstin[:14])
    if gstin[-1] != expected:
        return False, f"GSTIN checksum is invalid. Expected last character: {expected}"
    return True, "Valid GSTIN format."


def current_financial_year() -> str:
    today = date.today()
    if today.month >= 4:
        start = today.year
        end = today.year + 1
    else:
        start = today.year - 1
        end = today.year
    return f"FY {start}-{str(end)[-2:]}"


def parse_profile_text(text: str) -> Dict[str, str]:
    """Best-effort parser for key-value text copied from the GST portal."""
    result = {
        "Legal Name of Business": "",
        "Trade Name": "",
        "Constitution of Business": "",
        "GSTIN / UIN Status": "",
        "Taxpayer Type": "",
        "Registration Date": "",
        "Cancellation Date": "",
        "Last Updated": "Manual Entry",
    }
    if not text.strip():
        return result

    key_aliases = {
        "legal name of business": "Legal Name of Business",
        "legal name": "Legal Name of Business",
        "trade name": "Trade Name",
        "constitution of business": "Constitution of Business",
        "constitution": "Constitution of Business",
        "gstin / uin status": "GSTIN / UIN Status",
        "gstin/uin status": "GSTIN / UIN Status",
        "status": "GSTIN / UIN Status",
        "taxpayer type": "Taxpayer Type",
        "date of registration": "Registration Date",
        "registration date": "Registration Date",
        "date of cancellation": "Cancellation Date",
        "cancellation date": "Cancellation Date",
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Supports: "Key: Value", "Key - Value", or tab separated values.
        if ":" in line:
            key, value = line.split(":", 1)
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) < 2:
                continue
            key, value = parts[0], parts[-1]
        elif " - " in line:
            key, value = line.split(" - ", 1)
        else:
            continue

        clean_key = re.sub(r"\s+", " ", key.strip().lower())
        clean_value = value.strip()
        mapped_key = key_aliases.get(clean_key)
        if mapped_key and clean_value:
            result[mapped_key] = clean_value

    return result


def read_csv_text(csv_text: str) -> pd.DataFrame:
    if not csv_text.strip():
        return pd.DataFrame()
    return pd.read_csv(StringIO(csv_text.strip()))


def render_dashboard(profile: Dict[str, str], filing_df: pd.DataFrame, frequency_df: pd.DataFrame, gstin: str, fy: str) -> None:
    st.subheader("Business Details")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Legal Name", profile.get("Legal Name of Business") or "Not available")
    c2.metric("Trade Name", profile.get("Trade Name") or "Not available")
    c3.metric("Constitution", profile.get("Constitution of Business") or "Not available")
    c4.metric("GSTIN / UIN Status", profile.get("GSTIN / UIN Status") or "Not available")

    d1, d2, d3 = st.columns(3)
    d1.write(f"**GSTIN:** {gstin}")
    d1.write(f"**State Code:** {gstin[:2]}")
    d1.write(f"**State:** {STATE_CODES.get(gstin[:2], 'Unknown')}")
    d2.write(f"**Taxpayer Type:** {profile.get('Taxpayer Type') or 'Not available'}")
    d2.write(f"**Registration Date:** {profile.get('Registration Date') or 'Not available'}")
    d3.write(f"**Cancellation Date:** {profile.get('Cancellation Date') or 'Not available'}")
    d3.write(f"**Financial Year:** {fy}")

    st.divider()

    st.subheader("Filing Frequency / Return Preference")
    if frequency_df.empty:
        st.info("No filing frequency data entered.")
    else:
        st.dataframe(frequency_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Filing Frequency CSV",
            data=frequency_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{gstin}_filing_frequency.csv",
            mime="text/csv",
        )

    st.subheader("Filing Table in Detail")
    if filing_df.empty:
        st.info("No filing table data entered.")
    else:
        s1, s2, s3 = st.columns(3)
        s1.metric("Total Rows", len(filing_df))
        if "Status" in filing_df.columns:
            filed_count = filing_df["Status"].astype(str).str.lower().eq("filed").sum()
            s2.metric("Filed Rows", int(filed_count))
        else:
            s2.metric("Filed Rows", "-")
        if "Return Type" in filing_df.columns:
            s3.metric("Return Types", filing_df["Return Type"].nunique())
        else:
            s3.metric("Return Types", "-")

        st.dataframe(filing_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Filing Table CSV",
            data=filing_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{gstin}_filing_table_{fy.replace(' ', '_')}.csv",
            mime="text/csv",
        )


# -----------------------------
# App UI
# -----------------------------
st.markdown(
    """
    <div class="hero-card">
        <h1>GSTIN Customer Dashboard - No API</h1>
        <p>Validate GSTIN, manually collect details from the official GST portal, and prepare a clean customer GST dashboard.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Settings")
    fy = st.text_input("Financial Year", value=current_financial_year(), help="Example: FY 2026-27")
    st.divider()
    st.markdown("**No API key required**")
    st.caption("This version does not call Sandbox or any paid API.")

left, right = st.columns([2.3, 1])
with left:
    gstin = normalize_gstin(st.text_input("Customer GSTIN", placeholder="Example: 29ABCDE1234F1Z5"))
with right:
    st.write("")
    st.write("")
    show_dashboard = st.button("Show Dashboard", type="primary", use_container_width=True)

if not gstin:
    st.info("Enter a GSTIN to start.")
    st.markdown(
        """
        <div class="warn-box">
        <b>Important:</b> GSTIN format can be checked offline, but legal name, trade name, status,
        return filing table, and filing frequency cannot be generated from the GSTIN number alone.
        Use the official GST portal manual search, then paste the details below.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

valid, message = validate_gstin(gstin)
if valid:
    st.success(message)
else:
    st.warning(message)

state_name = STATE_CODES.get(gstin[:2], "Unknown") if len(gstin) >= 2 else "Unknown"
st.markdown(
    f"""
    <div class="info-box">
    <b>Offline GSTIN Information</b><br>
    GSTIN: <b>{gstin}</b><br>
    State Code: <b>{gstin[:2] if len(gstin) >= 2 else '-'}</b><br>
    State: <b>{state_name}</b>
    </div>
    """,
    unsafe_allow_html=True,
)

st.subheader("Step 1: Get details from official GST portal")
st.write(
    "Open GST portal, go to **Search Taxpayer → Search by GSTIN/UIN**, enter the GSTIN and captcha, "
    "then copy the visible business details and filing details into this app."
)
st.link_button("Open GST Portal", "https://www.gst.gov.in/")

if not valid:
    st.stop()

st.subheader("Step 2: Paste or enter Business Details")
profile_text = st.text_area(
    "Optional: Paste copied business details text here",
    placeholder=(
        "Example:\n"
        "Legal Name of Business: ABC PRIVATE LIMITED\n"
        "Trade Name: ABC TRADERS\n"
        "Constitution of Business: Private Limited Company\n"
        "GSTIN / UIN Status: Active\n"
        "Taxpayer Type: Regular\n"
        "Registration Date: 01/07/2017"
    ),
    height=150,
)
parsed = parse_profile_text(profile_text)

b1, b2 = st.columns(2)
legal_name = b1.text_input("Legal Name of Business", value=parsed.get("Legal Name of Business", ""))
trade_name = b2.text_input("Trade Name", value=parsed.get("Trade Name", ""))
constitution = b1.text_input("Constitution of Business", value=parsed.get("Constitution of Business", ""))
status = b2.text_input("GSTIN / UIN Status", value=parsed.get("GSTIN / UIN Status", ""), placeholder="Active / Cancelled / Suspended")
taxpayer_type = b1.text_input("Taxpayer Type", value=parsed.get("Taxpayer Type", ""), placeholder="Regular / Composition / etc.")
registration_date = b2.text_input("Registration Date", value=parsed.get("Registration Date", ""), placeholder="DD/MM/YYYY")
cancellation_date = b1.text_input("Cancellation Date", value=parsed.get("Cancellation Date", ""), placeholder="Only if applicable")

st.subheader("Step 3: Enter Filing Frequency")
st.caption("You can paste CSV-style data below. Keep the column names in the first row.")
frequency_text = st.text_area(
    "Filing Frequency CSV",
    value="Quarter,Preference Code,Filing Frequency\nQ1,,\nQ2,,\nQ3,,\nQ4,,",
    height=130,
)

st.subheader("Step 4: Enter Filing Table in Detail")
st.caption("Paste filing rows copied from GST portal/Excel in CSV format.")
filing_text = st.text_area(
    "Filing Table CSV",
    value="Return Type,Return Period,Date of Filing,Mode of Filing,ARN,Status,Valid\nGSTR3B,Apr 2026,,,,,\nGSTR1,Apr 2026,,,,,",
    height=170,
)

if show_dashboard:
    try:
        frequency_df = read_csv_text(frequency_text)
        filing_df = read_csv_text(filing_text)
    except Exception as exc:
        st.error(f"Could not read pasted CSV data. Please check commas/columns. Error: {exc}")
        st.stop()

    profile = {
        "Legal Name of Business": legal_name,
        "Trade Name": trade_name,
        "Constitution of Business": constitution,
        "GSTIN / UIN Status": status,
        "Taxpayer Type": taxpayer_type,
        "Registration Date": registration_date,
        "Cancellation Date": cancellation_date,
        "Last Updated": "Manual Entry",
    }
    st.divider()
    render_dashboard(profile, filing_df, frequency_df, gstin, fy)
else:
    st.info("After entering details, click **Show Dashboard**.")
