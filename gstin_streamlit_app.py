import re
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

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
    "Capture Source",
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


# -------------------------------------------------------------------
# Bookmarklet JS
# -------------------------------------------------------------------
CAPTURE_BOOKMARKLET_JS = r"""javascript:(()=>{const txt=document.body.innerText||'';const gst=(txt.match(/\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b/i)||['UNKNOWN'])[0].toUpperCase();const block='\n\n===== GST_CAPTURE_START =====\nGSTIN: '+gst+'\nCAPTURED_AT: '+new Date().toISOString()+'\nURL: '+location.href+'\n\n'+txt+'\n===== GST_CAPTURE_END =====\n';const key='CHATGPT_GST_CAPTURE_QUEUE_V1';const old=localStorage.getItem(key)||'';localStorage.setItem(key,old+block);alert('GST result captured for '+gst+'. Total saved characters: '+localStorage.getItem(key).length+'. Use the Export GST Captures bookmarklet when done.');})();"""

EXPORT_BOOKMARKLET_JS = r"""javascript:(()=>{const key='CHATGPT_GST_CAPTURE_QUEUE_V1';const data=localStorage.getItem(key)||'';if(!data){alert('No GST captures found in this browser.');return;}navigator.clipboard.writeText(data).then(()=>alert('All GST captures copied. Paste them into Streamlit.')).catch(()=>{const w=open('','GST Captures');w.document.body.innerHTML='<textarea style="width:100%;height:95vh">'+data.replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))+'</textarea>';});})();"""

CLEAR_BOOKMARKLET_JS = r"""javascript:(()=>{localStorage.removeItem('CHATGPT_GST_CAPTURE_QUEUE_V1');alert('GST capture queue cleared.');})();"""


# -------------------------------------------------------------------
# GSTIN helpers
# -------------------------------------------------------------------
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
    return compute_gstin_check_digit(gstin[:14]) == gstin[-1]


def find_gstin_in_text(text: str) -> str:
    match = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", text or "", flags=re.IGNORECASE)
    return clean_gstin(match.group(0)) if match else ""


def make_customer_base(gstin: str) -> dict:
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
        "Capture Source": "",
        "Notes": "",
    }


# -------------------------------------------------------------------
# Parser helpers
# -------------------------------------------------------------------
def normalize_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def find_field(text: str, labels: list[str]) -> str:
    lines = normalize_lines(text)
    compact = "\n".join(lines)

    for label in labels:
        # Same line: Label: Value
        match = re.search(rf"{re.escape(label)}\s*[:\-]\s*(.+)", compact, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                return value

        # Next line:
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
            ["Legal Name of Business", "Legal Name", "Legal Name of the Business"],
        ),
        "Trade Name": find_field(
            text,
            ["Trade Name", "Trade Name, if any", "Trade Name of Business"],
        ),
        "Constitution of Business": find_field(
            text,
            ["Constitution of Business", "Constitution", "Business Constitution"],
        ),
        "GSTIN / UIN Status": find_field(
            text,
            ["GSTIN / UIN Status", "GSTIN/UIN Status", "GSTIN Status", "Status"],
        ),
        "Filing Frequency": find_field(
            text,
            ["Filing Frequency", "Return Filing Frequency", "Filing Preference", "Return Frequency", "Frequency"],
        ),
    }


def standardize_return_type(value: str) -> str:
    value = (value or "").upper().replace(" ", "")
    value = value.replace("GSTR", "GSTR-").replace("CMP", "CMP-").replace("--", "-")
    return value


def parse_period(line: str) -> str:
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

    quarter_match = re.search(r"\b(Q[1-4])\s*[-/]?\s*(20\d{2}[-/]\d{2,4}|20\d{2})\b", line, flags=re.IGNORECASE)
    if quarter_match:
        return f"{quarter_match.group(1).upper()}-{quarter_match.group(2)}"

    return ""


def parse_date(line: str) -> str:
    match = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", line)
    return match.group(1) if match else ""


def parse_status(line: str) -> str:
    if re.search(r"\bnot\s+filed\b|\bpending\b|\bdefault\b|\bnot\s+available\b", line, flags=re.IGNORECASE):
        return "Not Filed / Pending"
    if re.search(r"\bfiled\b|\byes\b", line, flags=re.IGNORECASE):
        return "Filed"
    return ""


def parse_arn(line: str) -> str:
    # ARN patterns vary. Keep this conservative to avoid capturing return type/month text.
    match = re.search(r"\b[A-Z]{2}\d{13,20}\b", line, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


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
                "Return Period": parse_period(line),
                "Date of Filing": parse_date(line),
                "Status": parse_status(line),
                "ARN": parse_arn(line),
                "Mode": "",
                "Raw Text": line,
            }
        )

    if not rows:
        return pd.DataFrame(columns=FILING_COLUMNS)

    return pd.DataFrame(rows, columns=FILING_COLUMNS)


def split_capture_blocks(text: str) -> list[str]:
    text = text or ""
    pattern = r"===== GST_CAPTURE_START =====(.*?)===== GST_CAPTURE_END ====="
    blocks = re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)

    if blocks:
        return [block.strip() for block in blocks if block.strip()]

    # If the user pasted plain page text, treat it as one block.
    return [text.strip()] if text.strip() else []


def parse_capture_blocks(raw_text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    customer_rows = []
    filing_frames = []

    for block in split_capture_blocks(raw_text):
        gstin = find_field(block, ["GSTIN"]) or find_gstin_in_text(block)
        gstin = clean_gstin(gstin)

        if not gstin:
            continue

        details = parse_taxpayer_details(block)
        row = make_customer_base(gstin)
        row.update(details)
        row["Last Updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        row["Capture Source"] = "Bookmarklet / Pasted Result"
        customer_rows.append(row)

        filing_df = parse_filing_rows(block, gstin)
        if not filing_df.empty:
            filing_frames.append(filing_df)

    customers_df = pd.DataFrame(customer_rows, columns=GSTIN_COLUMNS)
    filings_df = pd.concat(filing_frames, ignore_index=True) if filing_frames else pd.DataFrame(columns=FILING_COLUMNS)

    return customers_df, filings_df


def merge_customers(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if incoming.empty:
        return existing

    combined = pd.concat([existing, incoming], ignore_index=True)
    combined["GSTIN"] = combined["GSTIN"].map(clean_gstin)
    combined = combined[combined["GSTIN"] != ""]
    combined = combined.drop_duplicates(subset=["GSTIN"], keep="last")
    return combined.reindex(columns=GSTIN_COLUMNS).reset_index(drop=True)


def merge_filings(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if incoming.empty:
        return existing

    combined = pd.concat([existing, incoming], ignore_index=True)
    combined["GSTIN"] = combined["GSTIN"].map(clean_gstin)
    combined = combined.drop_duplicates(
        subset=["GSTIN", "Return Type", "Return Period", "Date of Filing", "Status", "ARN", "Raw Text"],
        keep="last",
    )
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


def read_uploaded_table(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, dtype=str)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, dtype=str)
    raise ValueError("Upload CSV or Excel only.")


def make_rows_from_gstin_list(values: list[str]) -> pd.DataFrame:
    rows = []
    for value in values:
        gstin = clean_gstin(value)
        if gstin:
            rows.append(make_customer_base(gstin))
    return pd.DataFrame(rows, columns=GSTIN_COLUMNS)


# -------------------------------------------------------------------
# Session state
# -------------------------------------------------------------------
if "customers_df" not in st.session_state:
    st.session_state.customers_df = pd.DataFrame(columns=GSTIN_COLUMNS)

if "filings_df" not in st.session_state:
    st.session_state.filings_df = pd.DataFrame(columns=FILING_COLUMNS)


# -------------------------------------------------------------------
# UI
# -------------------------------------------------------------------
st.set_page_config(
    page_title="GSTIN One-Click Capture Tracker",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 GSTIN One-Click Capture Tracker")
st.caption("No API version with bookmarklet helper: solve captcha on GST portal, then capture the result page in one click.")

with st.sidebar:
    st.header("Best no-API workflow")
    st.write(
        "1. Install bookmarklets once\n"
        "2. Upload or add GSTIN list\n"
        "3. Open GST portal\n"
        "4. Solve captcha on GST portal\n"
        "5. Click Capture GST Result\n"
        "6. Repeat for GSTINs\n"
        "7. Click Export GST Captures\n"
        "8. Paste once into this app"
    )
    st.divider()
    st.link_button("Open GST Portal", GST_PORTAL_URL)

tab_helper, tab_add, tab_import, tab_dashboard, tab_export = st.tabs(
    ["0. One-Click Helper", "1. GSTIN List", "2. Import Captures", "3. Dashboard", "4. Export"]
)

# -------------------------------------------------------------------
# Tab 0: Bookmarklet helper
# -------------------------------------------------------------------
with tab_helper:
    st.subheader("One-Click Capture Helper")

    st.write(
        "This is the fastest no-API method. It does not submit captcha or search in the backend. "
        "You use the official GST portal normally, then use these bookmarklets to capture the visible result page."
    )

    st.write("### Install these 3 bookmarklets")
    st.write("Create browser bookmarks and paste the following code into each bookmark's URL field.")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.write("#### 1. Capture GST Result")
        st.code(CAPTURE_BOOKMARKLET_JS, language="javascript")
        st.write("Click this after one GST result page opens.")

    with c2:
        st.write("#### 2. Export GST Captures")
        st.code(EXPORT_BOOKMARKLET_JS, language="javascript")
        st.write("Click this after capturing all GSTINs. It copies all captures to clipboard.")

    with c3:
        st.write("#### 3. Clear GST Captures")
        st.code(CLEAR_BOOKMARKLET_JS, language="javascript")
        st.write("Use this before starting a fresh batch.")

    st.write("### How it saves time")
    st.markdown(
        """
        Instead of copying each field manually, you only do this per GSTIN:
        - complete captcha on GST portal
        - click **Capture GST Result**

        After all GSTINs are captured, click **Export GST Captures** once and paste everything in the next tab.
        """
    )

# -------------------------------------------------------------------
# Tab 1: Add GSTIN list
# -------------------------------------------------------------------
with tab_add:
    st.subheader("GSTIN List")

    col1, col2 = st.columns(2)

    with col1:
        st.write("### Paste GSTINs")
        pasted_gstins = st.text_area(
            "Paste one GSTIN per line",
            height=200,
            placeholder="27ABCDE1234F1Z5\n07ABCDE1234F1Z2",
        )

        if st.button("Add Pasted GSTINs", type="primary"):
            values = [line.strip() for line in pasted_gstins.splitlines() if line.strip()]
            incoming = make_rows_from_gstin_list(values)
            st.session_state.customers_df = merge_customers(st.session_state.customers_df, incoming)
            st.success(f"Added/updated {len(incoming)} GSTIN rows.")

    with col2:
        st.write("### Upload CSV/Excel")
        uploaded = st.file_uploader("Upload file with GSTIN column", type=["csv", "xlsx", "xls"])
        if uploaded:
            try:
                df = read_uploaded_table(uploaded)
                st.dataframe(df.head(10), use_container_width=True)
                columns = list(df.columns)

                default_index = 0
                for i, col in enumerate(columns):
                    if "gst" in str(col).lower():
                        default_index = i
                        break

                gst_col = st.selectbox("Select GSTIN column", columns, index=default_index)

                if st.button("Import Uploaded GSTINs"):
                    incoming = make_rows_from_gstin_list(df[gst_col].dropna().astype(str).tolist())
                    st.session_state.customers_df = merge_customers(st.session_state.customers_df, incoming)
                    st.success(f"Imported {len(incoming)} GSTIN rows.")
            except Exception as exc:
                st.error(f"Upload failed: {exc}")

    st.write("### Current GSTIN Master")
    if st.session_state.customers_df.empty:
        st.info("No GSTINs added yet.")
    else:
        edited = st.data_editor(
            st.session_state.customers_df,
            use_container_width=True,
            num_rows="dynamic",
            disabled=["State", "PAN", "Valid Format", "Checksum Valid"],
        )

        if st.button("Save GSTIN Master Edits"):
            edited = edited.copy()
            edited["GSTIN"] = edited["GSTIN"].map(clean_gstin)
            edited["State"] = edited["GSTIN"].map(gstin_state)
            edited["PAN"] = edited["GSTIN"].map(pan_from_gstin)
            edited["Valid Format"] = edited["GSTIN"].map(gstin_format_valid)
            edited["Checksum Valid"] = edited["GSTIN"].map(lambda x: gstin_checksum_valid(x) if gstin_format_valid(x) else False)
            st.session_state.customers_df = edited.drop_duplicates(subset=["GSTIN"], keep="last").reset_index(drop=True)
            st.success("GSTIN master saved.")

# -------------------------------------------------------------------
# Tab 2: Import captures
# -------------------------------------------------------------------
with tab_import:
    st.subheader("Import Captured GST Results")

    st.write(
        "After using **Export GST Captures**, paste the copied text below. "
        "The app will read all captured blocks together."
    )

    raw_capture = st.text_area(
        "Paste exported GST captures here",
        height=360,
        placeholder="===== GST_CAPTURE_START =====\nGSTIN: ...\n...\n===== GST_CAPTURE_END =====",
    )

    parsed_customers, parsed_filings = parse_capture_blocks(raw_capture)

    if raw_capture:
        st.write("### Parsed Preview")
        pc1, pc2 = st.columns(2)
        pc1.metric("Parsed Customers", len(parsed_customers))
        pc2.metric("Parsed Filing Rows", len(parsed_filings))

        if not parsed_customers.empty:
            st.write("#### Customer Details Found")
            st.dataframe(parsed_customers, use_container_width=True)

        if not parsed_filings.empty:
            st.write("#### Filing Rows Found")
            st.dataframe(parsed_filings, use_container_width=True)

        if st.button("Save Parsed Captures", type="primary"):
            st.session_state.customers_df = merge_customers(st.session_state.customers_df, parsed_customers)
            st.session_state.filings_df = merge_filings(st.session_state.filings_df, parsed_filings)
            st.success(f"Saved {len(parsed_customers)} customers and {len(parsed_filings)} filing rows.")

# -------------------------------------------------------------------
# Tab 3: Dashboard
# -------------------------------------------------------------------
with tab_dashboard:
    st.subheader("Dashboard")

    customers = st.session_state.customers_df
    filings = st.session_state.filings_df

    if customers.empty:
        st.info("No data yet.")
    else:
        total = len(customers)
        valid = int(customers["Valid Format"].sum())
        checksum = int(customers["Checksum Valid"].sum())
        active = int(customers["GSTIN / UIN Status"].str.contains("active", case=False, na=False).sum())
        filing_rows = len(filings)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total GSTINs", total)
        m2.metric("Valid Format", valid)
        m3.metric("Checksum Valid", checksum)
        m4.metric("Active", active)
        m5.metric("Filing Rows", filing_rows)

        st.write("### Customer Master")
        edited_customers = st.data_editor(customers, use_container_width=True, num_rows="dynamic")
        if st.button("Save Customer Dashboard Edits"):
            st.session_state.customers_df = edited_customers.reindex(columns=GSTIN_COLUMNS)
            st.success("Customer changes saved.")

        st.write("### Filing Details")
        edited_filings = st.data_editor(filings, use_container_width=True, num_rows="dynamic")
        if st.button("Save Filing Dashboard Edits"):
            st.session_state.filings_df = edited_filings.reindex(columns=FILING_COLUMNS)
            st.success("Filing changes saved.")

        st.write("### Status Summary")
        status_summary = customers["GSTIN / UIN Status"].replace("", "Blank").fillna("Blank").value_counts().reset_index()
        status_summary.columns = ["Status", "Count"]
        st.dataframe(status_summary, use_container_width=True)

        if not filings.empty:
            st.write("### Return Type Summary")
            return_summary = filings["Return Type"].replace("", "Blank").fillna("Blank").value_counts().reset_index()
            return_summary.columns = ["Return Type", "Count"]
            st.dataframe(return_summary, use_container_width=True)

# -------------------------------------------------------------------
# Tab 4: Export
# -------------------------------------------------------------------
with tab_export:
    st.subheader("Export")

    customers = st.session_state.customers_df
    filings = st.session_state.filings_df

    if customers.empty:
        st.info("Nothing to export yet.")
    else:
        excel_bytes = make_excel(customers, filings)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")

        st.download_button(
            "Download Full Excel Report",
            data=excel_bytes,
            file_name=f"gstin_one_click_report_{stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

        st.download_button(
            "Download Customer Master CSV",
            data=customers.to_csv(index=False).encode("utf-8"),
            file_name=f"customer_master_{stamp}.csv",
            mime="text/csv",
        )

        st.download_button(
            "Download Filing Details CSV",
            data=filings.to_csv(index=False).encode("utf-8"),
            file_name=f"filing_details_{stamp}.csv",
            mime="text/csv",
        )
