import re
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st


GST_PORTAL_URL = "https://services.gst.gov.in/services/searchtp"

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

GSTIN_COLUMNS = [
    "GSTIN",
    "State",
    "PAN",
    "Valid Format",
    "Checksum Valid",
    "Legal Name of Business",
    "Trade Name",
    "Constitution of Business",
    "GSTIN / UIN Status",
    "Filing Frequency",
    "Last Updated",
    "Notes",
]

FILING_COLUMNS = [
    "GSTIN",
    "Return Type",
    "Return Period",
    "Date of Filing",
    "Status",
    "ARN",
    "Mode",
    "Raw Text",
]


# ------------------------------------------------------------
# GSTIN validation helpers
# ------------------------------------------------------------
def clean_gstin(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()


def gstin_format_valid(gstin: str) -> bool:
    gstin = clean_gstin(gstin)
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
    return bool(re.match(pattern, gstin))


def gstin_state(gstin: str) -> str:
    gstin = clean_gstin(gstin)
    return STATE_CODES.get(gstin[:2], "Unknown State Code")


def pan_from_gstin(gstin: str) -> str:
    gstin = clean_gstin(gstin)
    return gstin[2:12] if len(gstin) >= 12 else ""


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
    expected = compute_gstin_check_digit(gstin[:14])
    return expected == gstin[-1]


def make_customer_row(gstin: str) -> dict:
    gstin = clean_gstin(gstin)
    return {
        "GSTIN": gstin,
        "State": gstin_state(gstin),
        "PAN": pan_from_gstin(gstin),
        "Valid Format": gstin_format_valid(gstin),
        "Checksum Valid": gstin_checksum_valid(gstin) if gstin_format_valid(gstin) else False,
        "Legal Name of Business": "",
        "Trade Name": "",
        "Constitution of Business": "",
        "GSTIN / UIN Status": "",
        "Filing Frequency": "",
        "Last Updated": "",
        "Notes": "",
    }


# ------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------
def normalize_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def find_field(text: str, labels: list[str]) -> str:
    lines = normalize_lines(text)
    compact = "\n".join(lines)

    for label in labels:
        # Pattern: Label: Value
        match = re.search(
            rf"{re.escape(label)}\s*[:\-]\s*(.+)",
            compact,
            flags=re.IGNORECASE,
        )
        if match:
            value = match.group(1).strip()
            if value:
                return value

        # Pattern:
        # Label
        # Value
        for i, line in enumerate(lines):
            clean_line = line.strip().strip(":").strip()
            if clean_line.lower() == label.lower() and i + 1 < len(lines):
                return lines[i + 1].strip()

    return ""


def parse_taxpayer_details(text: str) -> dict:
    return {
        "Legal Name of Business": find_field(
            text,
            [
                "Legal Name of Business",
                "Legal Name",
                "Legal Name of the Business",
            ],
        ),
        "Trade Name": find_field(
            text,
            [
                "Trade Name",
                "Trade Name, if any",
                "Trade Name of Business",
            ],
        ),
        "Constitution of Business": find_field(
            text,
            [
                "Constitution of Business",
                "Constitution",
                "Business Constitution",
            ],
        ),
        "GSTIN / UIN Status": find_field(
            text,
            [
                "GSTIN / UIN Status",
                "GSTIN/UIN Status",
                "GSTIN Status",
                "Status",
            ],
        ),
        "Filing Frequency": find_field(
            text,
            [
                "Filing Frequency",
                "Return Filing Frequency",
                "Filing Preference",
                "Return Frequency",
                "Frequency",
            ],
        ),
    }


def standardize_return_type(value: str) -> str:
    value = (value or "").upper().replace(" ", "")
    value = value.replace("GSTR", "GSTR-").replace("CMP", "CMP-").replace("--", "-")
    return value


def parse_period_from_line(line: str) -> str:
    month_pattern = (
        r"\b("
        r"Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|"
        r"Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December"
        r")[-\s']*(\d{2,4})\b"
    )
    month_match = re.search(month_pattern, line, flags=re.IGNORECASE)
    if month_match:
        return f"{month_match.group(1)[:3].title()}-{month_match.group(2)}"

    fy_match = re.search(r"\b(20\d{2}\s*[-/]\s*\d{2,4})\b", line)
    if fy_match:
        return fy_match.group(1).replace(" ", "")

    return ""


def parse_date_from_line(line: str) -> str:
    date_match = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", line)
    return date_match.group(1) if date_match else ""


def parse_arn_from_line(line: str) -> str:
    arn_match = re.search(r"\b([A-Z]{2}\d{13,20}|[A-Z0-9]{12,25})\b", line)
    if arn_match and not re.search(r"GSTR|CMP|FILED", arn_match.group(1), flags=re.IGNORECASE):
        return arn_match.group(1)
    return ""


def parse_status_from_line(line: str) -> str:
    if re.search(r"\bnot\s+filed\b|\bpending\b|\bdefault\b|\bnot\s+available\b", line, flags=re.IGNORECASE):
        return "Not Filed / Pending"
    if re.search(r"\bfiled\b|\byes\b", line, flags=re.IGNORECASE):
        return "Filed"
    return ""


def parse_filing_rows(text: str, gstin: str) -> pd.DataFrame:
    rows = []
    return_pattern = r"\b(GSTR-?1|GSTR-?3B|GSTR-?4|GSTR-?9C|GSTR-?9|CMP-?08|IFF)\b"

    for raw_line in (text or "").splitlines():
        line = " ".join(str(raw_line).split())
        if not line:
            continue

        return_match = re.search(return_pattern, line, flags=re.IGNORECASE)
        if not return_match:
            continue

        rows.append(
            {
                "GSTIN": clean_gstin(gstin),
                "Return Type": standardize_return_type(return_match.group(1)),
                "Return Period": parse_period_from_line(line),
                "Date of Filing": parse_date_from_line(line),
                "Status": parse_status_from_line(line),
                "ARN": parse_arn_from_line(line),
                "Mode": "",
                "Raw Text": line,
            }
        )

    if not rows:
        return pd.DataFrame(columns=FILING_COLUMNS)

    return pd.DataFrame(rows, columns=FILING_COLUMNS)


# ------------------------------------------------------------
# Import / export helpers
# ------------------------------------------------------------
def read_uploaded_table(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, dtype=str)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, dtype=str)
    raise ValueError("Please upload CSV or Excel file only.")


def merge_customers(existing_df: pd.DataFrame, new_rows: list[dict]) -> pd.DataFrame:
    new_df = pd.DataFrame(new_rows, columns=GSTIN_COLUMNS)
    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined["GSTIN"] = combined["GSTIN"].map(clean_gstin)
    combined = combined.drop_duplicates(subset=["GSTIN"], keep="first")
    combined = combined[combined["GSTIN"] != ""]
    return combined.reset_index(drop=True)


def upsert_filing_rows(existing_df: pd.DataFrame, gstin: str, new_df: pd.DataFrame) -> pd.DataFrame:
    gstin = clean_gstin(gstin)
    other = existing_df[existing_df["GSTIN"] != gstin].copy()
    if new_df.empty:
        return other.reset_index(drop=True)
    new_df = new_df.copy()
    new_df["GSTIN"] = gstin
    combined = pd.concat([other, new_df], ignore_index=True)
    return combined.reindex(columns=FILING_COLUMNS).reset_index(drop=True)


def make_excel(customers_df: pd.DataFrame, filings_df: pd.DataFrame) -> bytes:
    output = BytesIO()

    summary = pd.DataFrame(
        [
            {"Metric": "Total GSTINs", "Value": len(customers_df)},
            {"Metric": "Valid GSTIN Format", "Value": int(customers_df["Valid Format"].sum()) if not customers_df.empty else 0},
            {"Metric": "Checksum Valid", "Value": int(customers_df["Checksum Valid"].sum()) if not customers_df.empty else 0},
            {"Metric": "Active GSTINs", "Value": int(customers_df["GSTIN / UIN Status"].str.contains("active", case=False, na=False).sum()) if not customers_df.empty else 0},
            {"Metric": "Filing Rows", "Value": len(filings_df)},
        ]
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="Summary")
        customers_df.to_excel(writer, index=False, sheet_name="Customer Master")
        filings_df.to_excel(writer, index=False, sheet_name="Filing Details")

    return output.getvalue()


def sample_csv_bytes() -> bytes:
    sample = pd.DataFrame(
        {
            "GSTIN": [
                "27ABCDE1234F1Z5",
                "07ABCDE1234F1Z2",
            ]
        }
    )
    return sample.to_csv(index=False).encode("utf-8")


# ------------------------------------------------------------
# Session state
# ------------------------------------------------------------
if "customers_df" not in st.session_state:
    st.session_state.customers_df = pd.DataFrame(columns=GSTIN_COLUMNS)

if "filings_df" not in st.session_state:
    st.session_state.filings_df = pd.DataFrame(columns=FILING_COLUMNS)

if "selected_gstin" not in st.session_state:
    st.session_state.selected_gstin = ""


# ------------------------------------------------------------
# Streamlit UI
# ------------------------------------------------------------
st.set_page_config(
    page_title="GSTIN Bulk Compliance Tracker",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 GSTIN Bulk Compliance Tracker")
st.caption("No API version: validate GSTINs, speed up portal checking, parse pasted GST portal results, and export customer-wise filing reports.")

with st.sidebar:
    st.header("Workflow")
    st.write(
        "1. Add or upload GSTINs\n"
        "2. Validate automatically\n"
        "3. Open GST portal\n"
        "4. Complete captcha on official website\n"
        "5. Copy visible result text\n"
        "6. Paste here and save\n"
        "7. Export Excel report"
    )
    st.divider()
    st.link_button("Open Official GST Portal", GST_PORTAL_URL)
    st.download_button(
        "Download GSTIN Upload Template",
        data=sample_csv_bytes(),
        file_name="gstin_upload_template.csv",
        mime="text/csv",
    )

tab_add, tab_work, tab_dashboard, tab_export = st.tabs(
    ["1. Add GSTINs", "2. Check & Save Details", "3. Dashboard", "4. Export"]
)

# ------------------------------------------------------------
# Tab 1: Add GSTINs
# ------------------------------------------------------------
with tab_add:
    st.subheader("Add GSTINs")

    col_single, col_bulk = st.columns(2)

    with col_single:
        st.write("### Add Single GSTIN")
        single_gstin = st.text_input("Enter GSTIN", placeholder="Example: 27ABCDE1234F1Z5")

        if st.button("Add GSTIN", type="primary"):
            gstin = clean_gstin(single_gstin)
            if not gstin:
                st.error("Please enter GSTIN.")
            elif not gstin_format_valid(gstin):
                st.error("Invalid GSTIN format.")
            else:
                row = make_customer_row(gstin)
                st.session_state.customers_df = merge_customers(st.session_state.customers_df, [row])
                st.session_state.selected_gstin = gstin
                st.success(f"Added GSTIN: {gstin}")

    with col_bulk:
        st.write("### Upload Bulk GSTIN File")
        uploaded_file = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

        if uploaded_file is not None:
            try:
                uploaded_df = read_uploaded_table(uploaded_file)
                st.write("Preview")
                st.dataframe(uploaded_df.head(10), use_container_width=True)

                columns = list(uploaded_df.columns)
                default_index = 0
                for i, col in enumerate(columns):
                    if "gst" in str(col).lower():
                        default_index = i
                        break

                gstin_col = st.selectbox("Select GSTIN column", columns, index=default_index)

                if st.button("Import GSTINs"):
                    gstins = uploaded_df[gstin_col].dropna().map(clean_gstin).tolist()
                    rows = [make_customer_row(g) for g in gstins if g]
                    st.session_state.customers_df = merge_customers(st.session_state.customers_df, rows)

                    valid_count = sum(row["Valid Format"] for row in rows)
                    st.success(f"Imported {len(rows)} GSTIN rows. Valid format: {valid_count}.")
            except Exception as exc:
                st.error(f"Import failed: {exc}")

    st.divider()
    st.write("### Customer Master")
    if st.session_state.customers_df.empty:
        st.info("No GSTINs added yet.")
    else:
        edited_customers = st.data_editor(
            st.session_state.customers_df,
            use_container_width=True,
            num_rows="dynamic",
            disabled=["State", "PAN", "Valid Format", "Checksum Valid"],
        )

        if st.button("Save Customer Master Edits"):
            edited_customers = edited_customers.copy()
            edited_customers["GSTIN"] = edited_customers["GSTIN"].map(clean_gstin)
            edited_customers["State"] = edited_customers["GSTIN"].map(gstin_state)
            edited_customers["PAN"] = edited_customers["GSTIN"].map(pan_from_gstin)
            edited_customers["Valid Format"] = edited_customers["GSTIN"].map(gstin_format_valid)
            edited_customers["Checksum Valid"] = edited_customers["GSTIN"].map(
                lambda x: gstin_checksum_valid(x) if gstin_format_valid(x) else False
            )
            st.session_state.customers_df = edited_customers.drop_duplicates(subset=["GSTIN"], keep="first").reset_index(drop=True)
            st.success("Customer master updated.")

# ------------------------------------------------------------
# Tab 2: Check and Save Details
# ------------------------------------------------------------
with tab_work:
    st.subheader("Check GSTIN and Save Taxpayer Details")

    if st.session_state.customers_df.empty:
        st.info("Add GSTINs first.")
    else:
        gstin_options = st.session_state.customers_df["GSTIN"].tolist()
        selected_index = 0
        if st.session_state.selected_gstin in gstin_options:
            selected_index = gstin_options.index(st.session_state.selected_gstin)

        selected_gstin = st.selectbox("Select GSTIN to work on", gstin_options, index=selected_index)
        st.session_state.selected_gstin = selected_gstin

        selected_row = st.session_state.customers_df[
            st.session_state.customers_df["GSTIN"] == selected_gstin
        ].iloc[0].to_dict()

        status_col1, status_col2, status_col3, status_col4 = st.columns(4)
        status_col1.metric("GSTIN", selected_gstin)
        status_col2.metric("State", selected_row.get("State", ""))
        status_col3.metric("PAN", selected_row.get("PAN", ""))
        status_col4.metric("Format Valid", "Yes" if selected_row.get("Valid Format") else "No")

        st.link_button("Open GST Portal for this GSTIN", GST_PORTAL_URL)

        st.write("### Paste GST Portal Result Text")
        st.info("On the official GST portal, enter GSTIN and captcha, copy the visible taxpayer details and filing table, then paste it below.")

        pasted_text = st.text_area(
            "Paste result text",
            height=280,
            placeholder=(
                "Example:\n"
                "Legal Name of Business: ABC PRIVATE LIMITED\n"
                "Trade Name: ABC TRADERS\n"
                "Constitution of Business: Private Limited Company\n"
                "GSTIN / UIN Status: Active\n"
                "Filing Frequency: Monthly\n"
                "GSTR-1 Mar-2024 11/04/2024 Filed\n"
                "GSTR-3B Mar-2024 20/04/2024 Filed"
            ),
        )

        parsed_details = parse_taxpayer_details(pasted_text)
        parsed_filings = parse_filing_rows(pasted_text, selected_gstin)

        st.write("### Taxpayer Details")
        form_col1, form_col2 = st.columns(2)

        with form_col1:
            legal_name = st.text_input(
                "Legal Name of Business",
                value=parsed_details.get("Legal Name of Business") or selected_row.get("Legal Name of Business", ""),
            )
            trade_name = st.text_input(
                "Trade Name",
                value=parsed_details.get("Trade Name") or selected_row.get("Trade Name", ""),
            )
            constitution = st.text_input(
                "Constitution of Business",
                value=parsed_details.get("Constitution of Business") or selected_row.get("Constitution of Business", ""),
            )

        with form_col2:
            gst_status = st.text_input(
                "GSTIN / UIN Status",
                value=parsed_details.get("GSTIN / UIN Status") or selected_row.get("GSTIN / UIN Status", ""),
            )
            filing_frequency = st.text_input(
                "Filing Frequency",
                value=parsed_details.get("Filing Frequency") or selected_row.get("Filing Frequency", ""),
            )
            notes = st.text_input("Notes", value=selected_row.get("Notes", ""))

        st.write("### Filing Table")
        existing_filings = st.session_state.filings_df[
            st.session_state.filings_df["GSTIN"] == selected_gstin
        ]

        if not parsed_filings.empty:
            default_filings = parsed_filings
            st.success(f"Detected {len(parsed_filings)} filing rows from pasted text.")
        elif not existing_filings.empty:
            default_filings = existing_filings
        else:
            default_filings = pd.DataFrame(
                [
                    {
                        "GSTIN": selected_gstin,
                        "Return Type": "",
                        "Return Period": "",
                        "Date of Filing": "",
                        "Status": "",
                        "ARN": "",
                        "Mode": "",
                        "Raw Text": "",
                    }
                ],
                columns=FILING_COLUMNS,
            )

        edited_filings = st.data_editor(
            default_filings,
            use_container_width=True,
            num_rows="dynamic",
            column_order=FILING_COLUMNS,
            disabled=["GSTIN"],
        )

        if st.button("Save Details for Selected GSTIN", type="primary"):
            idx = st.session_state.customers_df["GSTIN"] == selected_gstin
            st.session_state.customers_df.loc[idx, "Legal Name of Business"] = legal_name
            st.session_state.customers_df.loc[idx, "Trade Name"] = trade_name
            st.session_state.customers_df.loc[idx, "Constitution of Business"] = constitution
            st.session_state.customers_df.loc[idx, "GSTIN / UIN Status"] = gst_status
            st.session_state.customers_df.loc[idx, "Filing Frequency"] = filing_frequency
            st.session_state.customers_df.loc[idx, "Last Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            st.session_state.customers_df.loc[idx, "Notes"] = notes

            cleaned_filings = edited_filings.copy()
            cleaned_filings["GSTIN"] = selected_gstin
            cleaned_filings = cleaned_filings.fillna("")
            cleaned_filings = cleaned_filings[
                cleaned_filings[["Return Type", "Return Period", "Date of Filing", "Status", "ARN", "Raw Text"]]
                .astype(str)
                .agg("".join, axis=1)
                .str.strip()
                != ""
            ]

            st.session_state.filings_df = upsert_filing_rows(
                st.session_state.filings_df,
                selected_gstin,
                cleaned_filings,
            )

            st.success(f"Saved details for {selected_gstin}.")

# ------------------------------------------------------------
# Tab 3: Dashboard
# ------------------------------------------------------------
with tab_dashboard:
    st.subheader("Dashboard")

    customers_df = st.session_state.customers_df
    filings_df = st.session_state.filings_df

    if customers_df.empty:
        st.info("No data available yet.")
    else:
        total = len(customers_df)
        valid = int(customers_df["Valid Format"].sum())
        checksum_valid = int(customers_df["Checksum Valid"].sum())
        active = int(customers_df["GSTIN / UIN Status"].str.contains("active", case=False, na=False).sum())
        filing_rows = len(filings_df)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total GSTINs", total)
        m2.metric("Valid Format", valid)
        m3.metric("Checksum Valid", checksum_valid)
        m4.metric("Active", active)
        m5.metric("Filing Rows", filing_rows)

        st.write("### Status Summary")
        if "GSTIN / UIN Status" in customers_df.columns:
            status_summary = (
                customers_df["GSTIN / UIN Status"]
                .replace("", "Blank")
                .fillna("Blank")
                .value_counts()
                .reset_index()
            )
            status_summary.columns = ["Status", "Count"]
            st.dataframe(status_summary, use_container_width=True)

        st.write("### Filing Summary")
        if not filings_df.empty:
            filing_summary = (
                filings_df.groupby(["GSTIN", "Return Type", "Status"], dropna=False)
                .size()
                .reset_index(name="Count")
                .sort_values(["GSTIN", "Return Type"])
            )
            st.dataframe(filing_summary, use_container_width=True)
        else:
            st.info("No filing rows saved yet.")

        st.write("### Customer Master")
        st.dataframe(customers_df, use_container_width=True)

        st.write("### Filing Details")
        st.dataframe(filings_df, use_container_width=True)

# ------------------------------------------------------------
# Tab 4: Export
# ------------------------------------------------------------
with tab_export:
    st.subheader("Export Reports")

    customers_df = st.session_state.customers_df
    filings_df = st.session_state.filings_df

    if customers_df.empty:
        st.info("No data to export yet.")
    else:
        excel_bytes = make_excel(customers_df, filings_df)

        st.download_button(
            "Download Full Excel Report",
            data=excel_bytes,
            file_name=f"gstin_compliance_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

        st.download_button(
            "Download Customer Master CSV",
            data=customers_df.to_csv(index=False).encode("utf-8"),
            file_name="customer_master.csv",
            mime="text/csv",
        )

        st.download_button(
            "Download Filing Details CSV",
            data=filings_df.to_csv(index=False).encode("utf-8"),
            file_name="filing_details.csv",
            mime="text/csv",
        )

        st.write("### Export Preview")
        st.dataframe(customers_df, use_container_width=True)
