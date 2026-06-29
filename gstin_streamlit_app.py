import json
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

# Public API endpoints
SEARCH_GSTIN_ENDPOINT = "/gst/compliance/public/gstin/search"
TRACK_GSTR_ENDPOINT = "/gst/compliance/public/gstrs/track"
PREFERENCE_ENDPOINT = "/gst/compliance/public/gstrs/preference"

# Taxpayer-private API endpoints
TAXPAYER_OTP_ENDPOINT = "/gst/compliance/tax-payer/otp"
TAXPAYER_OTP_VERIFY_ENDPOINT = "/gst/compliance/tax-payer/otp/verify"
GSTR3B_DETAILS_ENDPOINT_TEMPLATE = "/gst/compliance/tax-payer/gstrs/gstr-3b/{year}/{month}"

# GSTR-1 document sections.
# Endpoint pattern: /gst/compliance/tax-payer/gstrs/gstr-1/{section}/{year}/{month}
GSTR1_SECTIONS = {
    "B2B": "b2b",
    "B2BA": "b2ba",
    "B2CL": "b2cl",
    "B2CLA": "b2cla",
    "B2CS": "b2cs",
    "B2CSA": "b2csa",
    "CDNR": "cdnr",
    "CDNRA": "cdnra",
    "CDNUR": "cdnur",
    "CDNURA": "cdnura",
    "EXP": "exp",
    "EXPA": "expa",
    "AT": "at",
    "ATA": "ata",
    "TXP": "txp",
    "TXPA": "txpa",
    "NIL": "nil",
    "HSN": "hsn",
    "DOC-ISSUE": "doc-issue",
}

DEFAULT_TIMEOUT = 60

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
    "Aggregate Turnover",
    "Aggregate Turnover FY",
    "Gross Total Income",
    "Gross Total Income FY",
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

RETURN_RESPONSE_COLUMNS = [
    "GSTIN",
    "Return",
    "Section",
    "Year",
    "Month",
    "Status",
    "Message",
    "Transaction ID",
    "Fetched At",
    "Raw JSON",
]

RETURN_FACT_COLUMNS = [
    "GSTIN",
    "Return",
    "Section",
    "Year",
    "Month",
    "Path",
    "Value",
]

GSTR1_INVOICE_COLUMNS = [
    "GSTIN",
    "Year",
    "Month",
    "Section",
    "Counterparty GSTIN",
    "Invoice Number",
    "Invoice Date",
    "Invoice Value",
    "Invoice Type",
    "POS",
    "Reverse Charge",
    "Taxable Value",
    "IGST",
    "CGST",
    "SGST",
    "CESS",
    "Source Type",
    "Raw Path",
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


def parse_period_to_year_month(return_period: str) -> Tuple[str, str]:
    """
    Converts GST return period like 052026 into (year=2026, month=05).
    """
    value = re.sub(r"[^0-9]", "", str(return_period or ""))
    if len(value) == 6:
        month = value[:2]
        year = value[2:]
        return year, month
    return "", ""


# ============================================================
# Common API Helpers
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


def authenticate(api_key: str, api_secret: str, base_url: str) -> str:
    """
    Authenticates your Sandbox/API-provider account.
    This is NOT GST portal username/password.
    """
    url = f"{base_url}{AUTH_ENDPOINT}"
    headers = {
        "x-api-key": (api_key or "").strip(),
        "x-api-secret": (api_secret or "").strip(),
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


def make_app_headers(api_key: str, token: str, accept_cache: bool = True) -> Dict[str, str]:
    headers = {
        "x-api-key": (api_key or "").strip(),
        "authorization": (token or "").strip(),
        "x-api-version": "1.0",
        "Content-Type": "application/json",
    }
    if accept_cache:
        headers["x-accept-cache"] = "true"
    return headers


def make_taxpayer_headers(api_key: str, taxpayer_token: str) -> Dict[str, str]:
    return {
        "x-api-key": (api_key or "").strip(),
        "authorization": (taxpayer_token or "").strip(),
        "x-api-version": "1.0.0",
        "Content-Type": "application/json",
    }


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
        headers=make_app_headers(api_key, token, accept_cache=accept_cache),
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


def taxpayer_post_api(
    base_url: str,
    endpoint: str,
    api_key: str,
    authorization_token: str,
    body: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Used for Generate OTP and Verify OTP.
    authorization_token is the normal Sandbox access token until OTP is verified.
    """
    url = f"{base_url}{endpoint}"
    response = requests.post(
        url,
        headers=make_taxpayer_headers(api_key, authorization_token),
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


def taxpayer_get_api(
    base_url: str,
    endpoint: str,
    api_key: str,
    taxpayer_token: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{base_url}{endpoint}"
    response = requests.get(
        url,
        headers=make_taxpayer_headers(api_key, taxpayer_token),
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


# ============================================================
# Public GSTIN APIs
# ============================================================
def find_first_profile_value(data: Any, candidate_keys: List[str]) -> str:
    """
    GST API providers sometimes change key names or nest turnover fields.
    This helper checks common candidate keys, first at root level and then recursively.
    """
    if not isinstance(data, (dict, list)):
        return ""

    candidates_lower = {key.lower(): key for key in candidate_keys}

    def scan(obj: Any) -> str:
        if isinstance(obj, dict):
            # First check direct key matches
            for key, value in obj.items():
                if str(key).lower() in candidates_lower and value not in [None, ""]:
                    if isinstance(value, (dict, list)):
                        return json.dumps(value, ensure_ascii=False)
                    return str(value)

            # Then scan nested objects
            for value in obj.values():
                found = scan(value)
                if found:
                    return found

        elif isinstance(obj, list):
            for value in obj:
                found = scan(value)
                if found:
                    return found

        return ""

    return scan(data)


def extract_business_data(search_payload: Dict[str, Any]) -> Dict[str, Any]:
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
        "E-Invoice Status": find_first_profile_value(
            data,
            [
                "einvoiceStatus",
                "eInvoiceStatus",
                "einvStatus",
                "einv_applicable",
                "isEinvoiceApplicable",
            ],
        ),
        "Aggregate Turnover": find_first_profile_value(
            data,
            [
                "aggreTurnOver",
                "aggreTurnover",
                "aggregateTurnover",
                "aggregate_turnover",
                "aggTurnOver",
                "aato",
                "AATO",
            ],
        ),
        "Aggregate Turnover FY": find_first_profile_value(
            data,
            [
                "aggreTurnOverFY",
                "aggreTurnOverFy",
                "aggregateTurnoverFY",
                "aggregateTurnoverFy",
                "aggregate_turnover_fy",
                "aatoFinancialYear",
                "aatoFY",
                "AATOFY",
            ],
        ),
        "Gross Total Income": find_first_profile_value(
            data,
            [
                "grossTotalIncome",
                "gross_total_income",
                "gti",
                "GTI",
            ],
        ),
        "Gross Total Income FY": find_first_profile_value(
            data,
            [
                "grossTotalIncomeFY",
                "grossTotalIncomeFy",
                "grossTotalIncomeFinancialYear",
                "gross_total_income_fy",
                "gtiFinancialYear",
                "gtiFY",
            ],
        ),
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
        "Aggregate Turnover": "",
        "Aggregate Turnover FY": "",
        "Gross Total Income": "",
        "Gross Total Income FY": "",
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
# Taxpayer Authentication + Private Return APIs
# ============================================================
def generate_taxpayer_otp(
    base_url: str,
    api_key: str,
    app_token: str,
    gstin: str,
    username: str,
) -> Dict[str, Any]:
    return taxpayer_post_api(
        base_url,
        TAXPAYER_OTP_ENDPOINT,
        api_key,
        app_token,
        {"username": username.strip(), "gstin": clean_gstin(gstin)},
    )


def verify_taxpayer_otp(
    base_url: str,
    api_key: str,
    app_token: str,
    gstin: str,
    username: str,
    otp: str,
) -> Dict[str, Any]:
    return taxpayer_post_api(
        base_url,
        TAXPAYER_OTP_VERIFY_ENDPOINT,
        api_key,
        app_token,
        {"username": username.strip(), "gstin": clean_gstin(gstin)},
        params={"otp": otp.strip()},
    )


def fetch_gstr3b_details(
    base_url: str,
    api_key: str,
    taxpayer_token: str,
    year: str,
    month: str,
) -> Dict[str, Any]:
    endpoint = GSTR3B_DETAILS_ENDPOINT_TEMPLATE.format(year=year.strip(), month=month.strip())
    return taxpayer_get_api(base_url, endpoint, api_key, taxpayer_token)


def fetch_gstr1_section(
    base_url: str,
    api_key: str,
    taxpayer_token: str,
    section_slug: str,
    year: str,
    month: str,
) -> Dict[str, Any]:
    endpoint = f"/gst/compliance/tax-payer/gstrs/gstr-1/{section_slug}/{year.strip()}/{month.strip()}"
    return taxpayer_get_api(base_url, endpoint, api_key, taxpayer_token)


def response_status_message(payload: Dict[str, Any]) -> Tuple[str, str]:
    status_cd = get_nested(payload, "data", "status_cd", default="")
    err = get_nested(payload, "data", "error", default={})
    if isinstance(err, dict) and err:
        return str(status_cd or "0"), f"{err.get('error_cd', '')} {err.get('message', '')}".strip()
    if str(status_cd) == "1":
        return "1", "Success"
    return str(status_cd or ""), ""


def json_leaf_rows(
    obj: Any,
    context: Dict[str, Any],
    path: str = "",
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if rows is None:
        rows = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else str(key)
            json_leaf_rows(value, context, new_path, rows)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            new_path = f"{path}[{idx}]"
            json_leaf_rows(value, context, new_path, rows)
    else:
        row = dict(context)
        row["Path"] = path
        row["Value"] = obj
        rows.append(row)

    return rows


def sum_item_amounts(invoice: Dict[str, Any]) -> Dict[str, float]:
    totals = {"txval": 0.0, "iamt": 0.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0}
    itms = invoice.get("itms", [])
    if not isinstance(itms, list):
        return totals

    for item in itms:
        if not isinstance(item, dict):
            continue
        itm_det = item.get("itm_det", {})
        if not isinstance(itm_det, dict):
            continue
        for key in totals:
            try:
                totals[key] += float(itm_det.get(key, 0) or 0)
            except Exception:
                pass

    return totals


def find_invoice_like_dicts(
    obj: Any,
    section: str,
    ancestors: Optional[List[Dict[str, Any]]] = None,
    path: str = "",
    out: Optional[List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]]] = None,
) -> List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]]:
    """
    Finds invoice/note-like dictionaries in GSTR-1 responses.
    This makes the app useful even when different sections have slightly different schemas.
    """
    if ancestors is None:
        ancestors = []
    if out is None:
        out = []

    if isinstance(obj, dict):
        keys = set(obj.keys())
        invoice_markers = {"inum", "idt", "val", "inv_typ"}
        note_markers = {"nt_num", "nt_dt", "ntty"}
        doc_markers = {"doc_num", "from", "to", "totnum", "net_issue"}

        is_invoice = bool(invoice_markers.intersection(keys)) and ("itms" in keys or "val" in keys or "inum" in keys)
        is_note = bool(note_markers.intersection(keys))
        is_doc = section == "DOC-ISSUE" and bool(doc_markers.intersection(keys))

        if is_invoice or is_note or is_doc:
            out.append((path, obj, ancestors.copy()))

        new_ancestors = ancestors + [obj]
        for key, value in obj.items():
            find_invoice_like_dicts(value, section, new_ancestors, f"{path}.{key}" if path else str(key), out)

    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            find_invoice_like_dicts(item, section, ancestors, f"{path}[{idx}]", out)

    return out


def extract_gstr1_invoice_rows(payload: Dict[str, Any], gstin: str, year: str, month: str, section: str) -> List[Dict[str, Any]]:
    data = get_nested(payload, "data", "data", default={})
    rows = []
    found = find_invoice_like_dicts(data, section)

    for path, doc, ancestors in found:
        counterparty = ""
        for anc in reversed(ancestors):
            if isinstance(anc, dict) and anc.get("ctin"):
                counterparty = str(anc.get("ctin"))
                break

        totals = sum_item_amounts(doc)
        rows.append(
            {
                "GSTIN": gstin,
                "Year": year,
                "Month": month,
                "Section": section,
                "Counterparty GSTIN": counterparty,
                "Invoice Number": doc.get("inum") or doc.get("nt_num") or doc.get("doc_num") or "",
                "Invoice Date": doc.get("idt") or doc.get("nt_dt") or "",
                "Invoice Value": doc.get("val") or "",
                "Invoice Type": doc.get("inv_typ") or doc.get("ntty") or "",
                "POS": doc.get("pos") or "",
                "Reverse Charge": doc.get("rchrg") or "",
                "Taxable Value": totals["txval"],
                "IGST": totals["iamt"],
                "CGST": totals["camt"],
                "SGST": totals["samt"],
                "CESS": totals["csamt"],
                "Source Type": doc.get("srctyp") or "",
                "Raw Path": path,
            }
        )

    return rows


def add_return_response(
    gstin: str,
    ret: str,
    section: str,
    year: str,
    month: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    status, msg = response_status_message(payload)
    return {
        "GSTIN": gstin,
        "Return": ret,
        "Section": section,
        "Year": year,
        "Month": month,
        "Status": status,
        "Message": msg,
        "Transaction ID": payload.get("transaction_id", ""),
        "Fetched At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Raw JSON": json.dumps(payload, ensure_ascii=False),
    }


def make_return_facts(
    gstin: str,
    ret: str,
    section: str,
    year: str,
    month: str,
    payload: Dict[str, Any],
) -> pd.DataFrame:
    data = get_nested(payload, "data", "data", default=payload)
    context = {
        "GSTIN": gstin,
        "Return": ret,
        "Section": section,
        "Year": year,
        "Month": month,
    }
    rows = json_leaf_rows(data, context)
    return pd.DataFrame(rows, columns=RETURN_FACT_COLUMNS)


# ============================================================
# Input / Export
# ============================================================
def read_uploaded_gstins(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, dtype=str)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, dtype=str)

    raise ValueError("Upload only CSV or Excel.")


def sample_template() -> bytes:
    df = pd.DataFrame({"GSTIN": ["27ABCDE1234F1Z5", "07ABCDE1234F1Z2"]})
    return df.to_csv(index=False).encode("utf-8")


def make_excel(
    customers: pd.DataFrame,
    filings: pd.DataFrame,
    errors: pd.DataFrame,
    return_responses: Optional[pd.DataFrame] = None,
    return_facts: Optional[pd.DataFrame] = None,
    gstr1_invoices: Optional[pd.DataFrame] = None,
) -> bytes:
    output = BytesIO()

    if return_responses is None:
        return_responses = pd.DataFrame(columns=RETURN_RESPONSE_COLUMNS)
    if return_facts is None:
        return_facts = pd.DataFrame(columns=RETURN_FACT_COLUMNS)
    if gstr1_invoices is None:
        gstr1_invoices = pd.DataFrame(columns=GSTR1_INVOICE_COLUMNS)

    summary = pd.DataFrame(
        [
            {"Metric": "Total GSTINs", "Value": len(customers)},
            {"Metric": "Successful / Partial Rows", "Value": int(customers["API Status"].str.contains("OK|Profile OK|Completed", case=False, na=False).sum()) if not customers.empty else 0},
            {"Metric": "Active GSTINs", "Value": int(customers["GSTIN / UIN Status"].str.contains("Active", case=False, na=False).sum()) if not customers.empty else 0},
            {"Metric": "Public Filing Rows", "Value": len(filings)},
            {"Metric": "Private Return Responses", "Value": len(return_responses)},
            {"Metric": "GSTR-1 Invoice Rows", "Value": len(gstr1_invoices)},
            {"Metric": "Return Fact Rows", "Value": len(return_facts)},
            {"Metric": "Error Rows", "Value": len(errors)},
        ]
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="Summary")
        customers.to_excel(writer, index=False, sheet_name="Taxpayer Details")
        filings.to_excel(writer, index=False, sheet_name="Public Filing Table")
        gstr1_invoices.to_excel(writer, index=False, sheet_name="GSTR1 Invoice Rows")
        return_facts.to_excel(writer, index=False, sheet_name="Return Facts")
        return_responses.to_excel(writer, index=False, sheet_name="Raw Return JSON")
        errors.to_excel(writer, index=False, sheet_name="Errors")

    return output.getvalue()


def append_df(existing: pd.DataFrame, incoming: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    if incoming.empty:
        return existing.reindex(columns=columns)
    combined = pd.concat([existing.reindex(columns=columns), incoming.reindex(columns=columns)], ignore_index=True)
    return combined.reindex(columns=columns)


def init_state() -> None:
    defaults = {
        "gstin_list": [],
        "customers_df": pd.DataFrame(columns=CUSTOMER_COLUMNS),
        "filings_df": pd.DataFrame(columns=FILING_COLUMNS),
        "errors_df": pd.DataFrame(columns=ERROR_COLUMNS),
        "taxpayer_sessions": {},
        "return_responses_df": pd.DataFrame(columns=RETURN_RESPONSE_COLUMNS),
        "return_facts_df": pd.DataFrame(columns=RETURN_FACT_COLUMNS),
        "gstr1_invoices_df": pd.DataFrame(columns=GSTR1_INVOICE_COLUMNS),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# Streamlit UI
# ============================================================
init_state()

st.set_page_config(
    page_title="Bulk GSTIN API + GSTR Reports",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 2rem;
    }
    .app-hero {
        padding: 1.4rem 1.6rem;
        border-radius: 22px;
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 48%, #0f766e 100%);
        color: white;
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
        margin-bottom: 1rem;
    }
    .app-hero h1 {
        margin: 0;
        font-size: 2.05rem;
        font-weight: 800;
        letter-spacing: -0.03em;
    }
    .app-hero p {
        margin: 0.45rem 0 0 0;
        opacity: 0.92;
        font-size: 1rem;
    }
    .soft-card {
        padding: 1rem 1.1rem;
        border-radius: 18px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        background: rgba(248, 250, 252, 0.85);
        box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
        margin-bottom: 0.9rem;
    }
    .small-note {
        color: #475569;
        font-size: 0.92rem;
    }
    div[data-testid="stMetric"] {
        background: rgba(248, 250, 252, 0.92);
        border: 1px solid rgba(148, 163, 184, 0.25);
        padding: 0.8rem 1rem;
        border-radius: 18px;
        box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
    }
    div[data-testid="stTabs"] button {
        font-weight: 650;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="app-hero">
        <h1>🧾 Bulk GSTIN API Lookup + GSTR Reports</h1>
        <p>Clean dashboard for public GSTIN profile, filing table, turnover fields, e‑invoice status, and OTP-authorized GSTR‑1 / GSTR‑3B data.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("Important: what credentials are needed?", expanded=True):
    st.markdown(
        """
        **Public data already working:** only Sandbox API Key + API Secret are needed. The app now also attempts to capture turnover-related profile fields when the API returns them:
        - Aggregate Turnover
        - Aggregate Turnover FY
        - Gross Total Income
        - Gross Total Income FY
        - E-Invoice Status

        **GSTR-1 / GSTR-3B detailed data:** requires taxpayer OTP authorization for that GSTIN. Do **not** collect GST portal password. Use:
        - Sandbox API Key + API Secret
        - Customer GSTIN
        - Customer GST portal username
        - OTP sent to the customer/authorized user's registered mobile/email

        The taxpayer session is stored only in this app session memory and may expire.
        """
    )

with st.sidebar:
    st.header("API Settings")

    env_default = os.getenv("SANDBOX_ENV", "production").lower()
    env = st.selectbox(
        "Environment",
        ["production", "test"],
        index=0 if env_default == "production" else 1,
    )
    base_url = get_base_url(env)

    api_key = st.text_input("Sandbox API Key", value=os.getenv("SANDBOX_API_KEY", ""), type="password")
    api_secret = st.text_input("Sandbox API Secret", value=os.getenv("SANDBOX_API_SECRET", ""), type="password")

    financial_year = st.text_input("Financial Year for public filing status", value="FY 2026-27", help="Example: FY 2026-27")

    fetch_profile = st.checkbox("Fetch Taxpayer Profile", value=True)
    fetch_filings = st.checkbox("Fetch Public Filing Table", value=True)
    fetch_preference = st.checkbox("Fetch Filing Frequency", value=True)
    accept_cache = st.checkbox("Accept Cached API Response", value=True)
    delay_seconds = st.number_input("Delay between GSTIN calls (seconds)", min_value=0.0, max_value=10.0, value=0.3, step=0.1)

    st.download_button(
        "Download CSV Template",
        data=sample_template(),
        file_name="bulk_gstin_template.csv",
        mime="text/csv",
    )

    if st.button("Test API Authentication"):
        try:
            test_token = authenticate(api_key, api_secret, base_url)
            st.success("Authentication successful.")
        except Exception as exc:
            st.error(f"Authentication failed: {exc}")

tab_input, tab_run, tab_results, tab_auth, tab_reports = st.tabs(
    [
        "① Input GSTINs",
        "② Public API Run",
        "③ Dashboard & Export",
        "④ Taxpayer OTP Auth",
        "⑤ GSTR-1 / GSTR-3B Pull",
    ]
)

# ------------------------------------------------------------
# Tab 1: Input GSTINs
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Tab 2: Run Public API
# ------------------------------------------------------------
with tab_run:
    st.subheader("Run Bulk Public API Lookup")

    gstin_list = st.session_state.get("gstin_list", [])

    if not gstin_list:
        st.info("Go to Input GSTINs tab and add GSTINs first.")
    elif not api_key or not api_secret:
        st.warning("Enter Sandbox API Key and API Secret in the sidebar.")
    else:
        st.write(f"Ready to process **{len(gstin_list)} GSTINs** for **{financial_year}**.")

        if st.button("Start Bulk Public API Lookup", type="primary"):
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

            status_box.success("Bulk public API lookup completed.")

# ------------------------------------------------------------
# Tab 3: Results & Export
# ------------------------------------------------------------
with tab_results:
    st.subheader("Results & Export")

    customers = st.session_state.get("customers_df", pd.DataFrame(columns=CUSTOMER_COLUMNS))
    filings = st.session_state.get("filings_df", pd.DataFrame(columns=FILING_COLUMNS))
    errors = st.session_state.get("errors_df", pd.DataFrame(columns=ERROR_COLUMNS))
    return_responses = st.session_state.get("return_responses_df", pd.DataFrame(columns=RETURN_RESPONSE_COLUMNS))
    return_facts = st.session_state.get("return_facts_df", pd.DataFrame(columns=RETURN_FACT_COLUMNS))
    gstr1_invoices = st.session_state.get("gstr1_invoices_df", pd.DataFrame(columns=GSTR1_INVOICE_COLUMNS))

    if customers.empty:
        st.info("No public results yet. Run the public API lookup first.")
    else:
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("GSTINs Processed", len(customers))
        m2.metric("Active GSTINs", int(customers["GSTIN / UIN Status"].str.contains("Active", case=False, na=False).sum()))
        m3.metric("Turnover Found", int(customers["Aggregate Turnover"].astype(str).str.strip().ne("").sum()) if "Aggregate Turnover" in customers.columns else 0)
        m4.metric("Public Filing Rows", len(filings))
        m5.metric("GSTR Responses", len(return_responses))
        m6.metric("Errors", len(errors))

        st.write("### Taxpayer Details")
        st.dataframe(customers, use_container_width=True)

        st.write("### Turnover, GTI & E-Invoice Summary")
        turnover_cols = [
            "GSTIN",
            "Legal Name of Business",
            "Trade Name",
            "Aggregate Turnover",
            "Aggregate Turnover FY",
            "Gross Total Income",
            "Gross Total Income FY",
            "E-Invoice Status",
            "Filing Frequency",
            "GSTIN / UIN Status",
        ]
        existing_turnover_cols = [col for col in turnover_cols if col in customers.columns]
        st.dataframe(customers[existing_turnover_cols], use_container_width=True)

        st.write("### Public Filing Table in Detail")
        st.dataframe(filings, use_container_width=True)

        if not gstr1_invoices.empty:
            st.write("### GSTR-1 Invoice Rows")
            st.dataframe(gstr1_invoices, use_container_width=True)

        if not return_facts.empty:
            st.write("### Return Fact Rows")
            st.dataframe(return_facts, use_container_width=True)

        if not return_responses.empty:
            st.write("### Raw Return API Responses")
            st.dataframe(return_responses.drop(columns=["Raw JSON"], errors="ignore"), use_container_width=True)

        if not errors.empty:
            st.write("### Errors / Failed GSTINs")
            st.dataframe(errors, use_container_width=True)

        excel_bytes = make_excel(customers, filings, errors, return_responses, return_facts, gstr1_invoices)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.download_button(
                "Download Full Excel Report",
                data=excel_bytes,
                file_name=f"bulk_gstin_gstr_report_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        with col_b:
            st.download_button(
                "Download Public Taxpayer CSV",
                data=customers.to_csv(index=False).encode("utf-8"),
                file_name=f"taxpayer_details_{stamp}.csv",
                mime="text/csv",
            )
        with col_c:
            st.download_button(
                "Download Public Filing CSV",
                data=filings.to_csv(index=False).encode("utf-8"),
                file_name=f"filing_table_{stamp}.csv",
                mime="text/csv",
            )

# ------------------------------------------------------------
# Tab 4: Taxpayer OTP Auth
# ------------------------------------------------------------
with tab_auth:
    st.subheader("Taxpayer OTP Authentication")
    st.warning(
        "This step is required only for private GSTR-1/GSTR-3B data. "
        "Do not ask for or store the customer's GST portal password."
    )

    customers = st.session_state.get("customers_df", pd.DataFrame(columns=CUSTOMER_COLUMNS))
    gstin_options = sorted(set(st.session_state.get("gstin_list", [])) | set(customers["GSTIN"].dropna().tolist() if not customers.empty else []))

    if not gstin_options:
        manual_gstin = st.text_input("GSTIN", placeholder="Enter GSTIN")
        auth_gstin = clean_gstin(manual_gstin)
    else:
        auth_gstin = st.selectbox("Select GSTIN", gstin_options)

    username = st.text_input("GST Portal Username for this GSTIN", placeholder="Customer GST portal username, not password")
    st.caption("Customer should enable API access on GST portal before OTP, otherwise OTP/API session may fail.")

    if st.button("Generate Taxpayer OTP", type="primary"):
        if not api_key or not api_secret:
            st.error("Enter Sandbox API Key and API Secret first.")
        elif not auth_gstin or not username:
            st.error("Enter GSTIN and GST portal username.")
        else:
            try:
                app_token = authenticate(api_key, api_secret, base_url)
                payload = generate_taxpayer_otp(base_url, api_key, app_token, auth_gstin, username)
                st.session_state["pending_taxpayer_auth"] = {
                    "gstin": auth_gstin,
                    "username": username,
                    "app_token": app_token,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "response": payload,
                }
                st.success("OTP request sent. Ask the customer/authorized user for the OTP received on registered mobile/email.")
                st.json(payload)
            except Exception as exc:
                st.error(f"OTP generation failed: {exc}")

    pending = st.session_state.get("pending_taxpayer_auth", {})
    if pending:
        st.write("### Verify OTP")
        st.write(f"Pending GSTIN: **{pending.get('gstin')}**")
        st.write(f"Username: **{pending.get('username')}**")
        otp = st.text_input("Enter OTP", type="password")

        if st.button("Verify OTP and Start Taxpayer Session"):
            try:
                payload = verify_taxpayer_otp(
                    base_url,
                    api_key,
                    pending["app_token"],
                    pending["gstin"],
                    pending["username"],
                    otp,
                )
                taxpayer_token = get_nested(payload, "data", "access_token")
                session_expiry = get_nested(payload, "data", "session_expiry", default="")
                if not taxpayer_token:
                    st.error("OTP verified response did not contain taxpayer access_token.")
                    st.json(payload)
                else:
                    sessions = st.session_state.get("taxpayer_sessions", {})
                    sessions[pending["gstin"]] = {
                        "gstin": pending["gstin"],
                        "username": pending["username"],
                        "taxpayer_token": taxpayer_token,
                        "session_expiry": session_expiry,
                        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    st.session_state["taxpayer_sessions"] = sessions
                    st.success("Taxpayer session started. You can now pull GSTR-1/GSTR-3B data.")
                    st.json({k: v for k, v in payload.items() if k != "data"})
            except Exception as exc:
                st.error(f"OTP verification failed: {exc}")

    sessions = st.session_state.get("taxpayer_sessions", {})
    if sessions:
        st.write("### Active Taxpayer Sessions in this Streamlit run")
        session_preview = pd.DataFrame(
            [
                {
                    "GSTIN": k,
                    "Username": v.get("username", ""),
                    "Verified At": v.get("verified_at", ""),
                    "Session Expiry": v.get("session_expiry", ""),
                }
                for k, v in sessions.items()
            ]
        )
        st.dataframe(session_preview, use_container_width=True)

# ------------------------------------------------------------
# Tab 5: Pull GSTR-1 / GSTR-3B
# ------------------------------------------------------------
with tab_reports:
    st.subheader("Pull GSTR-1 / GSTR-3B Data")

    sessions = st.session_state.get("taxpayer_sessions", {})
    filings = st.session_state.get("filings_df", pd.DataFrame(columns=FILING_COLUMNS))

    if not sessions:
        st.info("First complete Taxpayer OTP Auth for at least one GSTIN.")
    else:
        session_gstins = sorted(sessions.keys())
        selected_gstin = st.selectbox("Select authenticated GSTIN", session_gstins)

        gstin_filings = filings[filings["GSTIN"] == selected_gstin] if not filings.empty else pd.DataFrame(columns=FILING_COLUMNS)

        period_options = []
        period_map = {}

        if not gstin_filings.empty:
            for _, row in gstin_filings.iterrows():
                ret_period = str(row.get("Return Period", ""))
                year, month = parse_period_to_year_month(ret_period)
                label = f"{row.get('Return Type', '')} | {ret_period} | Filed {row.get('Date of Filing', '')} | ARN {row.get('ARN', '')}"
                if year and month:
                    period_options.append(label)
                    period_map[label] = (year, month)

        use_from_filing = st.checkbox("Select period from public filing table", value=bool(period_options))

        if use_from_filing and period_options:
            selected_period = st.selectbox("Select filed return period", period_options)
            year, month = period_map[selected_period]
            st.success(f"Selected API period: Year {year}, Month {month}")
        else:
            c1, c2 = st.columns(2)
            with c1:
                year = st.text_input("Year", value=str(datetime.now().year))
            with c2:
                month = st.text_input("Month", value=f"{datetime.now().month:02d}", help="Use 01 to 12")

        st.write("### What to fetch")
        fetch_gstr3b = st.checkbox("Fetch GSTR-3B Details", value=True)
        fetch_gstr1 = st.checkbox("Fetch GSTR-1 Documents", value=True)

        selected_sections = []
        if fetch_gstr1:
            selected_sections = st.multiselect(
                "GSTR-1 Sections",
                options=list(GSTR1_SECTIONS.keys()),
                default=["B2B", "B2CL", "B2CS", "CDNR", "CDNUR", "EXP", "NIL", "HSN", "DOC-ISSUE"],
            )

        if st.button("Pull Return Data", type="primary"):
            if not re.match(r"^\d{4}$", str(year)):
                st.error("Year must be YYYY, example 2026.")
                st.stop()
            if not re.match(r"^(0[1-9]|1[0-2])$", str(month)):
                st.error("Month must be 01 to 12.")
                st.stop()

            taxpayer_token = sessions[selected_gstin]["taxpayer_token"]
            response_rows: List[Dict[str, Any]] = []
            fact_frames: List[pd.DataFrame] = []
            invoice_rows: List[Dict[str, Any]] = []
            error_rows: List[Dict[str, Any]] = []

            progress_steps = (1 if fetch_gstr3b else 0) + (len(selected_sections) if fetch_gstr1 else 0)
            progress_steps = max(progress_steps, 1)
            progress = st.progress(0)
            done = 0

            if fetch_gstr3b:
                try:
                    payload = fetch_gstr3b_details(base_url, api_key, taxpayer_token, year, month)
                    response_rows.append(add_return_response(selected_gstin, "GSTR-3B", "DETAILS", year, month, payload))
                    fact_frames.append(make_return_facts(selected_gstin, "GSTR-3B", "DETAILS", year, month, payload))
                    st.success("Fetched GSTR-3B details.")
                except Exception as exc:
                    error_rows.append(build_error(selected_gstin, "GSTR-3B Details", str(exc)))
                    st.error(f"GSTR-3B fetch failed: {exc}")
                done += 1
                progress.progress(done / progress_steps)

            if fetch_gstr1:
                for section in selected_sections:
                    slug = GSTR1_SECTIONS[section]
                    try:
                        payload = fetch_gstr1_section(base_url, api_key, taxpayer_token, slug, year, month)
                        response_rows.append(add_return_response(selected_gstin, "GSTR-1", section, year, month, payload))
                        fact_frames.append(make_return_facts(selected_gstin, "GSTR-1", section, year, month, payload))
                        invoice_rows.extend(extract_gstr1_invoice_rows(payload, selected_gstin, year, month, section))
                        st.success(f"Fetched GSTR-1 {section}.")
                    except Exception as exc:
                        error_rows.append(build_error(selected_gstin, f"GSTR-1 {section}", str(exc)))
                        st.error(f"GSTR-1 {section} fetch failed: {exc}")
                    done += 1
                    progress.progress(done / progress_steps)

            new_responses = pd.DataFrame(response_rows, columns=RETURN_RESPONSE_COLUMNS)
            new_facts = pd.concat(fact_frames, ignore_index=True) if fact_frames else pd.DataFrame(columns=RETURN_FACT_COLUMNS)
            new_invoices = pd.DataFrame(invoice_rows, columns=GSTR1_INVOICE_COLUMNS)
            new_errors = pd.DataFrame(error_rows, columns=ERROR_COLUMNS)

            st.session_state["return_responses_df"] = append_df(
                st.session_state.get("return_responses_df", pd.DataFrame(columns=RETURN_RESPONSE_COLUMNS)),
                new_responses,
                RETURN_RESPONSE_COLUMNS,
            )
            st.session_state["return_facts_df"] = append_df(
                st.session_state.get("return_facts_df", pd.DataFrame(columns=RETURN_FACT_COLUMNS)),
                new_facts,
                RETURN_FACT_COLUMNS,
            )
            st.session_state["gstr1_invoices_df"] = append_df(
                st.session_state.get("gstr1_invoices_df", pd.DataFrame(columns=GSTR1_INVOICE_COLUMNS)),
                new_invoices,
                GSTR1_INVOICE_COLUMNS,
            )
            st.session_state["errors_df"] = append_df(
                st.session_state.get("errors_df", pd.DataFrame(columns=ERROR_COLUMNS)),
                new_errors,
                ERROR_COLUMNS,
            )

            st.success("Return data pull completed.")

        st.write("### Pulled Return Data Preview")
        return_responses = st.session_state.get("return_responses_df", pd.DataFrame(columns=RETURN_RESPONSE_COLUMNS))
        return_facts = st.session_state.get("return_facts_df", pd.DataFrame(columns=RETURN_FACT_COLUMNS))
        gstr1_invoices = st.session_state.get("gstr1_invoices_df", pd.DataFrame(columns=GSTR1_INVOICE_COLUMNS))

        if not return_responses.empty:
            st.write("#### API Response Summary")
            st.dataframe(return_responses.drop(columns=["Raw JSON"], errors="ignore"), use_container_width=True)

        if not gstr1_invoices.empty:
            st.write("#### GSTR-1 Invoice Rows")
            st.dataframe(gstr1_invoices, use_container_width=True)

        if not return_facts.empty:
            st.write("#### Generic Return Facts")
            st.dataframe(return_facts, use_container_width=True)

        if not return_responses.empty:
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            export_excel = make_excel(
                st.session_state.get("customers_df", pd.DataFrame(columns=CUSTOMER_COLUMNS)),
                st.session_state.get("filings_df", pd.DataFrame(columns=FILING_COLUMNS)),
                st.session_state.get("errors_df", pd.DataFrame(columns=ERROR_COLUMNS)),
                return_responses,
                return_facts,
                gstr1_invoices,
            )
            st.download_button(
                "Download Return Data Excel",
                data=export_excel,
                file_name=f"gstr1_gstr3b_data_{selected_gstin}_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
