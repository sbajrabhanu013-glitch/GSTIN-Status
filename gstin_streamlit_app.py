"""
GSTIN Streamlit Dashboard
-------------------------
What it shows after entering a GSTIN:
1. Legal Name of Business
2. Trade Name
3. Constitution of Business
4. GSTIN / UIN Status
5. Filing Table in Detail
6. Filing Frequency / Return Preference

API provider used in this script: Sandbox.co.in GST Compliance Public APIs.
You need API Key and API Secret from Sandbox Console.

Run:
    pip install streamlit requests pandas python-dotenv
    streamlit run gstin_streamlit_app.py

Optional: create a .env file in the same folder:
    SANDBOX_API_KEY=your_api_key
    SANDBOX_API_SECRET=your_api_secret
    SANDBOX_ENV=production   # or test
"""

from __future__ import annotations

import os
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# -----------------------------
# Page config and CSS
# -----------------------------
st.set_page_config(
    page_title="GSTIN Customer Dashboard",
    page_icon="🧾",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main {
        background: linear-gradient(135deg, #f7fff8 0%, #effdf5 50%, #f4fbff 100%);
    }
    .block-container {
        padding-top: 1.6rem;
    }
    .hero-card {
        padding: 1.2rem 1.4rem;
        border-radius: 22px;
        background: linear-gradient(135deg, #e7ffe9 0%, #e9fff9 45%, #eef8ff 100%);
        border: 1px solid rgba(0, 150, 80, 0.14);
        box-shadow: 0 10px 30px rgba(0,0,0,0.06);
        margin-bottom: 1rem;
    }
    .hero-card h1 {
        margin: 0;
        font-size: 2.05rem;
        color: #10391f;
    }
    .hero-card p {
        margin: 0.4rem 0 0 0;
        color: #446153;
        font-size: 1rem;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid rgba(0, 150, 80, 0.12);
        border-radius: 18px;
        padding: 1rem;
        box-shadow: 0 8px 22px rgba(0,0,0,0.045);
    }
    .small-note {
        color: #5a6b62;
        font-size: 0.86rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# GSTIN validation helpers
# -----------------------------
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def normalize_gstin(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def gstin_checksum_char(first_14: str) -> str:
    """Calculate GSTIN checksum character for first 14 characters."""
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


def is_valid_gstin(gstin: str) -> tuple[bool, str]:
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


def period_to_month_year(ret_prd: Any) -> str:
    """Convert GST return period MMYYYY into readable month-year."""
    if ret_prd is None:
        return ""
    value = str(ret_prd).strip()
    if len(value) == 6 and value.isdigit():
        mm = value[:2]
        yyyy = value[2:]
        try:
            return date(int(yyyy), int(mm), 1).strftime("%b %Y")
        except Exception:
            return value
    return value


def preference_label(value: str) -> str:
    value = (value or "").strip().upper()
    return {"M": "Monthly", "Q": "Quarterly"}.get(value, value or "Not available")


# -----------------------------
# Generic JSON helpers
# -----------------------------
def deep_get(payload: Any, paths: List[str], default: Any = "") -> Any:
    """Try multiple dot-separated paths in a nested dict/list payload."""
    for path in paths:
        cur = payload
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def unwrap_sandbox_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sandbox responses often nest real data at data.data."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
        return data["data"]
    if isinstance(data, dict):
        return data
    return payload


# -----------------------------
# API client
# -----------------------------
class SandboxGSTClient:
    def __init__(self, api_key: str, api_secret: str, env: str = "production") -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.env = env
        self.base_url = "https://api.sandbox.co.in" if env == "production" else "https://test-api.sandbox.co.in"
        self.api_version = "1.0.0"

    def authenticate(self) -> str:
        cache_key = f"sandbox_token_{self.env}_{self.api_key[-6:]}"
        cache_time_key = f"sandbox_token_time_{self.env}_{self.api_key[-6:]}"

        token = st.session_state.get(cache_key)
        token_time = st.session_state.get(cache_time_key, 0)

        # Token is valid for 24 hours; refresh a little earlier.
        if token and (time.time() - token_time) < (23 * 60 * 60):
            return token

        url = f"{self.base_url}/authenticate"
        headers = {
            "x-api-key": self.api_key,
            "x-api-secret": self.api_secret,
            "x-api-version": self.api_version,
            "Content-Type": "application/json",
        }
        response = requests.post(url, headers=headers, timeout=30)
        self._raise_for_response(response, "Authentication failed")

        payload = response.json()
        access_token = deep_get(payload, ["data.access_token", "access_token"])
        if not access_token:
            raise RuntimeError(f"Access token not found in response: {payload}")

        st.session_state[cache_key] = access_token
        st.session_state[cache_time_key] = time.time()
        return access_token

    def _headers(self) -> Dict[str, str]:
        token = self.authenticate()
        return {
            "x-api-key": self.api_key,
            "authorization": token,  # Sandbox expects token without Bearer prefix.
            "x-api-version": self.api_version,
            "x-accept-cache": "true",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_response(response: requests.Response, message: str) -> None:
        if response.ok:
            return
        try:
            details = response.json()
        except Exception:
            details = response.text
        raise RuntimeError(f"{message}. HTTP {response.status_code}: {details}")

    def post(self, path: str, body: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = requests.post(url, headers=self._headers(), json=body, params=params, timeout=45)
        self._raise_for_response(response, f"API call failed: {path}")
        return response.json()

    def search_gstin(self, gstin: str) -> Dict[str, Any]:
        return self.post("/gst/compliance/public/gstin/search", {"gstin": gstin})

    def track_returns(self, gstin: str, financial_year: str, gstr_filter: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {"financial_year": financial_year}
        if gstr_filter:
            params["gstr"] = gstr_filter
        return self.post("/gst/compliance/public/gstrs/track", {"gstin": gstin}, params=params)

    def return_preference(self, gstin: str, financial_year: str) -> Dict[str, Any]:
        return self.post("/gst/compliance/public/gstrs/preference", {"gstin": gstin}, params={"financial_year": financial_year})


# -----------------------------
# Normalizers for UI
# -----------------------------
def normalize_taxpayer_profile(raw: Dict[str, Any]) -> Dict[str, str]:
    data = unwrap_sandbox_data(raw)
    return {
        "GSTIN": deep_get(data, ["gstin", "gstin_no", "GSTIN", "gstinNo"]),
        "Legal Name of Business": deep_get(data, ["lgnm", "legal_name", "legalName", "legal_name_of_business"]),
        "Trade Name": deep_get(data, ["tradeNam", "trade_name", "tradeName", "trade_name_of_business"]),
        "Constitution of Business": deep_get(data, ["ctb", "constitution", "constitution_of_business", "business_constitution"]),
        "GSTIN / UIN Status": deep_get(data, ["sts", "status", "gstin_status", "taxpayer_status"]),
        "Taxpayer Type": deep_get(data, ["dty", "taxpayer_type", "taxpayerType"]),
        "Registration Date": deep_get(data, ["rgdt", "registration_date", "registrationDate"]),
        "Last Updated": deep_get(data, ["lstupdt", "last_updated", "lastUpdated"]),
        "E-Invoice Status": deep_get(data, ["einvoiceStatus", "einvoice_status"]),
    }


def normalize_filing_rows(raw: Dict[str, Any]) -> pd.DataFrame:
    data = unwrap_sandbox_data(raw)
    rows = deep_get(data, ["EFiledlist", "efiled_list", "filings", "returns", "response"], default=[])
    if isinstance(rows, dict):
        rows = rows.get("EFiledlist") or rows.get("response") or rows.get("data") or []
    if not isinstance(rows, list):
        rows = []

    normalized = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        ret_prd = item.get("ret_prd") or item.get("return_period") or item.get("tax_period") or item.get("period")
        normalized.append(
            {
                "Return Type": item.get("rtntype") or item.get("return_type") or item.get("returnType") or "",
                "Return Period": period_to_month_year(ret_prd),
                "Return Period Code": ret_prd or "",
                "Date of Filing": item.get("dof") or item.get("date_of_filing") or item.get("filing_date") or "",
                "Mode of Filing": item.get("mof") or item.get("mode_of_filing") or "",
                "ARN": item.get("arn") or item.get("ARN") or "",
                "Status": item.get("status") or item.get("filing_status") or "",
                "Valid": item.get("valid") or item.get("is_valid") or "",
            }
        )

    df = pd.DataFrame(normalized)
    if not df.empty:
        df = df.sort_values(by=["Return Period Code", "Return Type"], ascending=[False, True])
    return df


def normalize_frequency(raw: Dict[str, Any]) -> pd.DataFrame:
    data = unwrap_sandbox_data(raw)
    rows = deep_get(data, ["response", "preferences", "return_preference", "data.response"], default=[])
    if isinstance(rows, dict):
        rows = rows.get("response") or rows.get("preferences") or []
    if not isinstance(rows, list):
        rows = []

    normalized = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "Quarter": item.get("quarter") or item.get("qtr") or "",
                "Preference Code": item.get("preference") or item.get("pref") or "",
                "Filing Frequency": preference_label(item.get("preference") or item.get("pref") or ""),
            }
        )
    return pd.DataFrame(normalized)


def download_json_button(label: str, data: Dict[str, Any], file_name: str) -> None:
    import json

    st.download_button(
        label=label,
        data=json.dumps(data, indent=2, ensure_ascii=False),
        file_name=file_name,
        mime="application/json",
    )


# -----------------------------
# UI
# -----------------------------
st.markdown(
    """
    <div class="hero-card">
        <h1>GSTIN Customer Dashboard</h1>
        <p>Enter a customer GSTIN to fetch business details, filing status table and filing frequency.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("API Settings")
    env = st.selectbox(
        "Environment",
        options=["production", "test"],
        index=0 if os.getenv("SANDBOX_ENV", "production").lower() == "production" else 1,
        help="Use test only if your provider has test credentials and sample data enabled.",
    )
    api_key = st.text_input("Sandbox API Key", value=os.getenv("SANDBOX_API_KEY", ""), type="password")
    api_secret = st.text_input("Sandbox API Secret", value=os.getenv("SANDBOX_API_SECRET", ""), type="password")
    st.divider()
    financial_year = st.text_input("Financial Year", value=current_financial_year(), help="Example: FY 2025-26")
    gstr_filter = st.selectbox("Return Type Filter", ["", "gstr-1", "gstr-3b", "gstr-9"], index=0)
    show_raw = st.checkbox("Show raw JSON responses", value=False)

col_input, col_status = st.columns([2.4, 1])
with col_input:
    gstin_input = st.text_input("Customer GSTIN", placeholder="Example: 29ABCDE1234F1Z5")
with col_status:
    st.write("")
    st.write("")
    search_clicked = st.button("Search GSTIN", type="primary", use_container_width=True)

gstin = normalize_gstin(gstin_input)

if gstin_input:
    valid, msg = is_valid_gstin(gstin)
    if valid:
        st.success(msg)
    else:
        st.warning(msg)

if search_clicked:
    valid, msg = is_valid_gstin(gstin)
    if not valid:
        st.error(msg)
        st.stop()

    if not api_key or not api_secret:
        st.error("Please enter your Sandbox API Key and API Secret in the sidebar, or set them in a .env file.")
        st.stop()

    client = SandboxGSTClient(api_key=api_key, api_secret=api_secret, env=env)

    try:
        with st.spinner("Fetching GSTIN profile, filing table and filing frequency..."):
            profile_raw = client.search_gstin(gstin)
            filing_raw = client.track_returns(gstin, financial_year=financial_year, gstr_filter=gstr_filter)
            frequency_raw = client.return_preference(gstin, financial_year=financial_year)
    except Exception as exc:
        st.error(str(exc))
        st.info("Check API credentials, environment, financial year format, and whether your API plan has GST compliance endpoints enabled.")
        st.stop()

    profile = normalize_taxpayer_profile(profile_raw)
    filing_df = normalize_filing_rows(filing_raw)
    frequency_df = normalize_frequency(frequency_raw)

    st.subheader("Business Details")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Legal Name", profile.get("Legal Name of Business") or "Not available")
    m2.metric("Trade Name", profile.get("Trade Name") or "Not available")
    m3.metric("Constitution", profile.get("Constitution of Business") or "Not available")
    m4.metric("GSTIN / UIN Status", profile.get("GSTIN / UIN Status") or "Not available")

    detail_cols = st.columns(3)
    detail_cols[0].write(f"**GSTIN:** {profile.get('GSTIN') or gstin}")
    detail_cols[0].write(f"**Taxpayer Type:** {profile.get('Taxpayer Type') or 'Not available'}")
    detail_cols[1].write(f"**Registration Date:** {profile.get('Registration Date') or 'Not available'}")
    detail_cols[1].write(f"**Last Updated:** {profile.get('Last Updated') or 'Not available'}")
    detail_cols[2].write(f"**E-Invoice Status:** {profile.get('E-Invoice Status') or 'Not available'}")
    detail_cols[2].write(f"**Financial Year:** {financial_year}")

    st.divider()

    st.subheader("Filing Frequency / Return Preference")
    if frequency_df.empty:
        st.info("No filing frequency data returned for this GSTIN/FY.")
    else:
        st.dataframe(frequency_df, hide_index=True, use_container_width=True)

    st.subheader("Filing Table in Detail")
    if filing_df.empty:
        st.info("No return filing rows returned for this GSTIN/FY/filter.")
    else:
        summary_col1, summary_col2, summary_col3 = st.columns(3)
        summary_col1.metric("Total Filing Rows", len(filing_df))
        summary_col2.metric("Filed Rows", int((filing_df["Status"].str.lower() == "filed").sum()))
        summary_col3.metric("Return Types", filing_df["Return Type"].nunique())

        st.dataframe(filing_df, hide_index=True, use_container_width=True)

        csv = filing_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Filing Table CSV",
            data=csv,
            file_name=f"{gstin}_filing_table_{financial_year.replace(' ', '_')}.csv",
            mime="text/csv",
        )

    with st.expander("Downloads"):
        download_json_button("Download GSTIN Profile JSON", profile_raw, f"{gstin}_profile.json")
        download_json_button("Download Filing JSON", filing_raw, f"{gstin}_filings.json")
        download_json_button("Download Frequency JSON", frequency_raw, f"{gstin}_frequency.json")

    if show_raw:
        st.subheader("Raw API Responses")
        st.write("GSTIN Profile")
        st.json(profile_raw)
        st.write("Filing Table")
        st.json(filing_raw)
        st.write("Filing Frequency")
        st.json(frequency_raw)
else:
    st.info("Enter a GSTIN and click Search GSTIN.")
    st.markdown(
        """
        <div class="small-note">
        Note: This app uses API access. Do not try to bypass GST portal captcha or login protections. Use official/GSP/provider APIs for production use.
        </div>
        """,
        unsafe_allow_html=True,
    )
