import os
import re
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

# ============================================================
# App Config
# ============================================================
PROD_BASE_URL = "https://api.sandbox.co.in"
TEST_BASE_URL = "https://test-api.sandbox.co.in"

AUTH_ENDPOINT = "/authenticate"
SEARCH_GSTIN_ENDPOINT = "/gst/compliance/public/gstin/search"
TRACK_GSTR_ENDPOINT = "/gst/compliance/public/gstrs/track"
PREFERENCE_ENDPOINT = "/gst/compliance/public/gstrs/preference"

DEFAULT_TIMEOUT = 45

STATE_CODES = {
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
    "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",
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

CUSTOMER_COLUMNS = [
    "GSTIN",
    "Valid Format",
    "Checksum Valid",
    "State From GSTIN",
    "PAN From GSTIN",
    "Legal Name of Business",
    "Trade Name",
    "Constitution of Business",
    "GSTIN / UIN Status",
    "Taxpayer Type",
    "Registration Date",
    "Cancellation Date",
    "Last Updated on GSTN",
    "E-Invoice Status",
    "Nature of Business",
    "Principal Place State",
    "Principal Place City",
    "Principal Place Pincode",
    "Filing Frequency",
    "Preference Detail",
    "API Status",
    "API Message",
    "Fetched At",
]

FILING_COLUMNS = [
    "GSTIN",
    "Return Type",
    "Return Period",
    "Date of Filing",
    "Status",
    "ARN",
    "Mode of Filing",
    "Valid",
    "Financial Year",
]

ERROR_COLUMNS = [
    "GSTIN",
    "Step",
    "Error",
    "Raw Response",
    "Fetched At",
]


# ============================================================
# GSTIN Validation
# ============================================================
def clean_gstin(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()


def gstin_format_valid(gstin: str) -> bool:
    gstin = clean_gstin(gstin)
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
    return bool(re.match(pattern, gstin))


def compute_gstin_check_digit(first_14_chars: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    factor = 2
    total = 0

    for char in reversed(first_14_chars.upper()):
        code_point = chars.find(char)
        if code_point == -1:
            return ""

        addend = factor * code_point
        factor = 1 if factor == 2 else 2
        addend = (addend // 36) + (addend % 36)
        total += addend

    check_code_point = (36 - (total % 36)) % 36
    return chars[check_code_point]


def gstin_checksum_valid(gstin: str) -> bool:
    gstin = clean_gstin(gstin)
    if len(gstin) != 15:
        return False
    return compute_gstin_check_digit(gstin[:14]) == gstin[-1]


def gstin_state(gstin: str) -> str:
    gstin = clean_gstin(gstin)
    return STATE_CODES.get(gstin[:2], "Unknown State Code")


def pan_from_gstin(gstin: str) -> str:
    gstin = clean_gstin(gstin)
    return gstin[2:12] if len(gstin) >= 12 else ""


# ============================================================
# API Helpers
# ============================================================
def get_base_url(env: str) -> str:
    return PROD_BASE_URL if env.lower() == "production" else TEST_BASE_URL


def get_nested(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def make_headers(api_key: str, token: str, accept_cache: bool = True) -> Dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "authorization": token,
        "x-api-version": "1.0.0",
        "Content-Type": "application/json",
    }
    if accept_cache:
        headers["x-accept-cache"] = "true"
    return headers


def authenticate(api_key: str, api_secret: str, base_url: str) -> str:
    url = f"{base_url}{AUTH_ENDPOINT}"
    headers = {
        "x-api-key": api_key,
        "x-api-secret": api_secret,
        "x-api-version": "1.0.0",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    try:
        payload = response.json()
    except Exception:
        response.raise_for_status()
        raise RuntimeError("Authentication response was not valid JSON.")

    if response.status_code >= 400:
        raise RuntimeError(f"Authentication failed: {payload}")

    token = get_nested(payload, "data", "access_token")
    if not token:
        raise RuntimeError(f"Authentication succeeded but access_token was not found: {payload}")

    return token


def post_api(
    base_url: str,
    endpoint: str,
    api_key: str,
    token: str,
    body: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    accept_cache: bool = True,
) -> Dict[str, Any]:
    url = f"{base_url}{endpoint}"
    response = requests.post(
        url,
        headers=make_headers(api_key, token, accept_cache=accept_cache),
        json=body,
        params=params or {},
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        payload = response.json()
    except Exception:
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        raise RuntimeError(f"Invalid JSON response: {response.text[:500]}")

    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {payload}")

    return payload


def extract_business_data(search_payload: Dict[str, Any]) -> Dict[str, Any]:
    # Sandbox responses normally nest actual taxpayer data here:
    # response["data"]["data"]
    data = get_nested(search_payload, "data", "data", default={})
    if not isinstance(data, dict):
        data = {}

    addr = get_nested(data, "pradr", "addr", default={})
    if not isinstance(addr, dict):
        addr = {}

    nba = data.get("nba", [])
    if isinstance(nba, list):
        nature_of_business = ", ".join([str(x) for x in nba])
    else:
        nature_of_business = str(nba or "")

    return {
        "Legal Name of Business": data.get("lgnm", ""),
        "Trade Name": data.get("tradeNam", ""),
        "Constitution of Business": data.get("ctb", ""),
        "GSTIN / UIN Status": data.get("sts", ""),
        "Taxpayer Type": data.get("dty", ""),
        "Registration Date": data.get("rgdt", ""),
        "Cancellation Date": data.get("cxdt", ""),
        "Last Updated on GSTN": data.get("lstupdt", ""),
        "E-Invoice Status": data.get("einvoiceStatus", ""),
        "Nature of Business": nature_of_business,
        "Principal Place State": addr.get("stcd", ""),
        "Principal Place City": addr.get("loc", "") or addr.get("dst", ""),
        "Principal Place Pincode": addr.get("pncd", ""),
    }


def extract_preference(preference_payload: Dict[str, Any]) -> Tuple[str, str]:
    response = get_nested(preference_payload, "data", "data", "response", default=[])
    if not isinstance(response, list):
        return "", ""

    parts = []
    prefs = []
    for item in response:
        if not isinstance(item, dict):
            continue
        quarter = item.get("quarter", "")
        pref = item.get("preference", "")
        readable = {"M": "Monthly", "Q": "Quarterly"}.get(str(pref).upper(), str(pref))
        if readable:
            prefs.append(readable)
        if quarter or readable:
            parts.append(f"{quarter}: {readable}".strip(": "))

    if not parts:
        return "", ""

    unique = sorted(set(prefs))
    frequency = unique[0] if len(unique) == 1 else "Mixed"
    return frequency, "; ".join(parts)


def extract_filing_rows(track_payload: Dict[str, Any], gstin: str, financial_year: str) -> List[Dict[str, Any]]:
    rows = []
    filed_list = get_nested(track_payload, "data", "data", "EFiledlist", default=[])

    if not isinstance(filed_list, list):
        return rows

    for item in filed_list:
        if not isinstance(item, dict):
            continue

        rows.append(
            {
                "GSTIN": gstin,
                "Return Type": item.get("rtntype", ""),
                "Return Period": item.get("ret_prd", ""),
                "Date of Filing": item.get("dof", ""),
                "Status": item.get("status", ""),
                "ARN": item.get("arn", ""),
                "Mode of Filing": item.get("mof", ""),
                "Valid": item.get("valid", ""),
                "Financial Year": financial_year,
            }
        )

    return rows


def build_error(gstin: str, step: str, error: str, raw_response: Any = "") -> Dict[str, Any]:
    return {
        "GSTIN": gstin,
        "Step": step,
        "Error": error,
        "Raw Response": str(raw_response)[:2000],
        "Fetched At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_one_gstin(
    gstin: str,
    api_key: str,
    token: str,
    base_url: str,
    financial_year: str,
    fetch_profile: bool,
    fetch_filings: bool,
    fetch_preference: bool,
    accept_cache: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    gstin = clean_gstin(gstin)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    customer_row = {
        "GSTIN": gstin,
        "Valid Format": gstin_format_valid(gstin),
        "Checksum Valid": gstin_checksum_valid(gstin) if gstin_format_valid(gstin) else False,
        "State From GSTIN": gstin_state(gstin),
        "PAN From GSTIN": pan_from_gstin(gstin),
        "Legal Name of Business": "",
        "Trade Name": "",
        "Constitution of Business": "",
        "GSTIN / UIN Status": "",
        "Taxpayer Type": "",
        "Registration Date": "",
        "Cancellation Date": "",
        "Last Updated on GSTN": "",
        "E-Invoice Status": "",
        "Nature of Business": "",
        "Principal Place State": "",
        "Principal Place City": "",
        "Principal Place Pincode": "",
        "Filing Frequency": "",
        "Preference Detail": "",
        "API Status": "Not Started",
        "API Message": "",
        "Fetched At": now,
    }

    filing_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []

    if not gstin_format_valid(gstin):
        customer_row["API Status"] = "Skipped"
        customer_row["API Message"] = "Invalid GSTIN format"
        return customer_row, filing_rows, [build_error(gstin, "Validation", "Invalid GSTIN format")]

    if not customer_row["Checksum Valid"]:
        customer_row["API Status"] = "Warning"
        customer_row["API Message"] = "GSTIN format is valid but checksum failed. API call skipped."
        return customer_row, filing_rows, [build_error(gstin, "Validation", "Checksum failed")]

    if fetch_profile:
        try:
            payload = post_api(
                base_url,
                SEARCH_GSTIN_ENDPOINT,
                api_key,
                token,
                {"gstin": gstin},
                accept_cache=accept_cache,
            )
            business = extract_business_data(payload)
            customer_row.update(business)
            customer_row["API Status"] = "Profile OK"
            customer_row["API Message"] = "GSTIN profile fetched"
        except Exception as exc:
            customer_row["API Status"] = "Profile Failed"
            customer_row["API Message"] = str(exc)
            error_rows.append(build_error(gstin, "GSTIN Search", str(exc)))

    if fetch_preference:
        try:
            payload = post_api(
                base_url,
                PREFERENCE_ENDPOINT,
                api_key,
                token,
                {"gstin": gstin},
                params={"financial_year": financial_year},
                accept_cache=accept_cache,
            )
            frequency, preference_detail = extract_preference(payload)
            customer_row["Filing Frequency"] = frequency
            customer_row["Preference Detail"] = preference_detail
        except Exception as exc:
            if customer_row["API Status"] in ["Not Started", "Profile OK"]:
                customer_row["API Status"] = "Preference Failed"
            customer_row["API Message"] = (customer_row["API Message"] + " | " if customer_row["API Message"] else "") + str(exc)
            error_rows.append(build_error(gstin, "Return Preference", str(exc)))

    if fetch_filings:
        try:
            payload = post_api(
                base_url,
                TRACK_GSTR_ENDPOINT,
                api_key,
                token,
                {"gstin": gstin},
                params={"financial_year": financial_year},
                accept_cache=accept_cache,
            )
            filing_rows = extract_filing_rows(payload, gstin, financial_year)
            if customer_row["API Status"] in ["Not Started", "Profile OK"]:
                customer_row["API Status"] = "OK"
            customer_row["API Message"] = (customer_row["API Message"] + " | " if customer_row["API Message"] else "") + f"{len(filing_rows)} filing rows fetched"
        except Exception as exc:
            if customer_row["API Status"] in ["Not Started", "Profile OK"]:
                customer_row["API Status"] = "Filing Failed"
            customer_row["API Message"] = (customer_row["API Message"] + " | " if customer_row["API Message"] else "") + str(exc)
            error_rows.append(build_error(gstin, "Track GST Returns", str(exc)))

    if customer_row["API Status"] == "Not Started":
        customer_row["API Status"] = "Completed"
        customer_row["API Message"] = "No API option selected"

    return customer_row, filing_rows, error_rows


# ============================================================
# Input / Export
# ============================================================
def read_uploaded_gstins(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file, dtype=str)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file, dtype=str)
    else:
        raise ValueError("Upload only CSV or Excel.")

    return df


def sample_template() -> bytes:
    df = pd.DataFrame({"GSTIN": ["27ABCDE1234F1Z5", "07ABCDE1234F1Z2"]})
    return df.to_csv(index=False).encode("utf-8")


def make_excel(customers: pd.DataFrame, filings: pd.DataFrame, errors: pd.DataFrame) -> bytes:
    output = BytesIO()

    summary = pd.DataFrame(
        [
            {"Metric": "Total GSTINs", "Value": len(customers)},
            {"Metric": "Successful / Partial Rows", "Value": int(customers["API Status"].str.contains("OK|Profile OK|Completed", case=False, na=False).sum()) if not customers.empty else 0},
            {"Metric": "Active GSTINs", "Value": int(customers["GSTIN / UIN Status"].str.contains("Active", case=False, na=False).sum()) if not customers.empty else 0},
            {"Metric": "Filing Rows", "Value": len(filings)},
            {"Metric": "Error Rows", "Value": len(errors)},
        ]
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="Summary")
        customers.to_excel(writer, index=False, sheet_name="Taxpayer Details")
        filings.to_excel(writer, index=False, sheet_name="Filing Table")
        errors.to_excel(writer, index=False, sheet_name="Errors")

    return output.getvalue()


# ============================================================
# Streamlit UI
# ============================================================
st.set_page_config(
    page_title="Bulk GSTIN API Lookup",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Bulk GSTIN API Lookup")
st.caption("Fetch Legal Name, Trade Name, Constitution, Status, Filing Table, and Filing Frequency for multiple GSTINs in one go.")

with st.expander("Which login/credentials should I use?", expanded=True):
    st.markdown(
        """
        **Use API provider credentials, not your normal GST portal login.**

        For public GSTIN lookup, return tracking, and return preference, this app needs:
        - `SANDBOX_API_KEY`
        - `SANDBOX_API_SECRET`

        You get these from your API provider console, for example Sandbox Console → Settings → API Keys.

        Your **GST portal username/password is not required** for this public lookup app.

        GST portal taxpayer login / OTP consent is needed only for taxpayer-private APIs like downloading GSTR-2A/2B, ledgers, filing returns, etc.
        """
    )

with st.sidebar:
    st.header("API Settings")

    env_default = os.getenv("SANDBOX_ENV", "production").lower()
    env = st.selectbox(
        "Environment",
        ["production", "test"],
        index=0 if env_default == "production" else 1,
        help="Use production for live data and test for Sandbox test environment.",
    )
    base_url = get_base_url(env)

    api_key = st.text_input(
        "Sandbox API Key",
        value=os.getenv("SANDBOX_API_KEY", ""),
        type="password",
    )
    api_secret = st.text_input(
        "Sandbox API Secret",
        value=os.getenv("SANDBOX_API_SECRET", ""),
        type="password",
    )

    financial_year = st.text_input("Financial Year", value="FY 2025-26", help="Example: FY 2025-26")

    fetch_profile = st.checkbox("Fetch Taxpayer Profile", value=True)
    fetch_filings = st.checkbox("Fetch Filing Table", value=True)
    fetch_preference = st.checkbox("Fetch Filing Frequency", value=True)
    accept_cache = st.checkbox("Accept Cached API Response", value=True)
    delay_seconds = st.number_input("Delay between GSTIN calls (seconds)", min_value=0.0, max_value=10.0, value=0.3, step=0.1)

    st.download_button(
        "Download CSV Template",
        data=sample_template(),
        file_name="bulk_gstin_template.csv",
        mime="text/csv",
    )

tab_input, tab_run, tab_results = st.tabs(["1. Input GSTINs", "2. Run API", "3. Results & Export"])

with tab_input:
    st.subheader("Input Multiple GSTINs")

    col1, col2 = st.columns(2)

    with col1:
        st.write("### Paste GSTINs")
        pasted = st.text_area(
            "Paste GSTINs, one per line",
            height=240,
            placeholder="27ABCDE1234F1Z5\n07ABCDE1234F1Z2",
        )

    with col2:
        st.write("### Upload CSV/Excel")
        uploaded = st.file_uploader("Upload file", type=["csv", "xlsx", "xls"])

    gstin_list: List[str] = []

    if pasted.strip():
        gstin_list.extend([clean_gstin(line) for line in pasted.splitlines() if clean_gstin(line)])

    if uploaded is not None:
        try:
            df_upload = read_uploaded_gstins(uploaded)
            st.write("Uploaded preview")
            st.dataframe(df_upload.head(10), use_container_width=True)

            columns = list(df_upload.columns)
            default_idx = 0
            for i, col in enumerate(columns):
                if "gst" in str(col).lower():
                    default_idx = i
                    break

            selected_col = st.selectbox("Select GSTIN column", columns, index=default_idx)
            gstin_list.extend(df_upload[selected_col].dropna().astype(str).map(clean_gstin).tolist())
        except Exception as exc:
            st.error(f"Could not read uploaded file: {exc}")

    gstin_list = sorted(set([g for g in gstin_list if g]))

    st.write("### GSTIN Validation Preview")
    if not gstin_list:
        st.info("Paste GSTINs or upload a file.")
    else:
        preview_df = pd.DataFrame(
            [
                {
                    "GSTIN": g,
                    "Valid Format": gstin_format_valid(g),
                    "Checksum Valid": gstin_checksum_valid(g) if gstin_format_valid(g) else False,
                    "State": gstin_state(g),
                    "PAN": pan_from_gstin(g),
                }
                for g in gstin_list
            ]
        )
        st.dataframe(preview_df, use_container_width=True)
        st.session_state["gstin_list"] = gstin_list

with tab_run:
    st.subheader("Run Bulk API Lookup")

    gstin_list = st.session_state.get("gstin_list", [])

    if not gstin_list:
        st.info("Go to Input GSTINs tab and add GSTINs first.")
    elif not api_key or not api_secret:
        st.warning("Enter Sandbox API Key and API Secret in the sidebar.")
    else:
        st.write(f"Ready to process **{len(gstin_list)} GSTINs** for **{financial_year}**.")

        if st.button("Start Bulk API Lookup", type="primary"):
            progress = st.progress(0)
            status_box = st.empty()

            customer_rows: List[Dict[str, Any]] = []
            filing_rows_all: List[Dict[str, Any]] = []
            error_rows_all: List[Dict[str, Any]] = []

            try:
                status_box.info("Authenticating with API provider...")
                token = authenticate(api_key, api_secret, base_url)
                status_box.success("Authentication successful.")
            except Exception as exc:
                st.error(f"Authentication failed: {exc}")
                st.stop()

            for idx, gstin in enumerate(gstin_list, start=1):
                status_box.info(f"Processing {idx}/{len(gstin_list)}: {gstin}")

                customer_row, filing_rows, error_rows = fetch_one_gstin(
                    gstin=gstin,
                    api_key=api_key,
                    token=token,
                    base_url=base_url,
                    financial_year=financial_year,
                    fetch_profile=fetch_profile,
                    fetch_filings=fetch_filings,
                    fetch_preference=fetch_preference,
                    accept_cache=accept_cache,
                )

                customer_rows.append(customer_row)
                filing_rows_all.extend(filing_rows)
                error_rows_all.extend(error_rows)

                progress.progress(idx / len(gstin_list))

                if delay_seconds:
                    time.sleep(float(delay_seconds))

            st.session_state["customers_df"] = pd.DataFrame(customer_rows, columns=CUSTOMER_COLUMNS)
            st.session_state["filings_df"] = pd.DataFrame(filing_rows_all, columns=FILING_COLUMNS)
            st.session_state["errors_df"] = pd.DataFrame(error_rows_all, columns=ERROR_COLUMNS)

            status_box.success("Bulk API lookup completed.")

with tab_results:
    st.subheader("Results & Export")

    customers = st.session_state.get("customers_df", pd.DataFrame(columns=CUSTOMER_COLUMNS))
    filings = st.session_state.get("filings_df", pd.DataFrame(columns=FILING_COLUMNS))
    errors = st.session_state.get("errors_df", pd.DataFrame(columns=ERROR_COLUMNS))

    if customers.empty:
        st.info("No results yet. Run the API lookup first.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("GSTINs Processed", len(customers))
        m2.metric("Active GSTINs", int(customers["GSTIN / UIN Status"].str.contains("Active", case=False, na=False).sum()))
        m3.metric("Filing Rows", len(filings))
        m4.metric("Errors", len(errors))

        st.write("### Taxpayer Details")
        st.dataframe(customers, use_container_width=True)

        st.write("### Filing Table in Detail")
        st.dataframe(filings, use_container_width=True)

        if not errors.empty:
            st.write("### Errors / Failed GSTINs")
            st.dataframe(errors, use_container_width=True)

        excel_bytes = make_excel(customers, filings, errors)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.download_button(
                "Download Full Excel Report",
                data=excel_bytes,
                file_name=f"bulk_gstin_api_report_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        with col_b:
            st.download_button(
                "Download Taxpayer Details CSV",
                data=customers.to_csv(index=False).encode("utf-8"),
                file_name=f"taxpayer_details_{stamp}.csv",
                mime="text/csv",
            )
        with col_c:
            st.download_button(
                "Download Filing Table CSV",
                data=filings.to_csv(index=False).encode("utf-8"),
                file_name=f"filing_table_{stamp}.csv",
                mime="text/csv",
            )
