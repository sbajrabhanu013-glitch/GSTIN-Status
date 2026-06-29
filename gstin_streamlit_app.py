import re
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

GST_PORTAL_URL = "https://services.gst.gov.in/services/searchtp"

# -------------------------------
# Step 1: GSTIN Validation
# -------------------------------
def validate_gstin(gstin):
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    return re.match(pattern, gstin)

# -------------------------------
# Step 2: Fetch Captcha
# -------------------------------
def fetch_captcha(session):
    captcha_page = session.get(GST_PORTAL_URL)
    # Parse captcha image URL from HTML
    soup = BeautifulSoup(captcha_page.text, "html.parser")
    captcha_img_tag = soup.find("img", {"id": "captchaImg"})
    if captcha_img_tag:
        captcha_url = "https://services.gst.gov.in" + captcha_img_tag["src"]
        return captcha_url
    return None

# -------------------------------
# Step 3: Submit GSTIN + Captcha
# -------------------------------
def submit_search(session, gstin, captcha):
    payload = {"gstin": gstin, "captcha": captcha}
    response = session.post(GST_PORTAL_URL, data=payload)
    return response.text

# -------------------------------
# Step 4: Parse Result Page
# -------------------------------
def parse_result(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    try:
        result["Legal Name"] = soup.find("span", {"id": "legalName"}).text.strip()
        result["Trade Name"] = soup.find("span", {"id": "tradeName"}).text.strip()
        result["Status"] = soup.find("span", {"id": "status"}).text.strip()
        result["Filing Frequency"] = soup.find("span", {"id": "filingFreq"}).text.strip()
        # Filing Table example
        filing_table = []
        table = soup.find("table", {"id": "filingTable"})
        if table:
            for row in table.find_all("tr")[1:]:
                cols = [col.text.strip() for col in row.find_all("td")]
                filing_table.append(cols)
        result["Filing Table"] = filing_table
    except Exception as e:
        result["Error"] = f"Parsing failed: {e}"
    return result

# -------------------------------
# Streamlit UI
# -------------------------------
st.title("GST Taxpayer Search App")

gstin = st.text_input("Enter GSTIN")

if gstin:
    if validate_gstin(gstin):
        st.success("GSTIN format looks valid.")
        session = requests.Session()
        captcha_url = fetch_captcha(session)
        
        if captcha_url:
            st.image(captcha_url, caption="Enter Captcha")
            captcha = st.text_input("Captcha")
            
            if captcha:
                html = submit_search(session, gstin, captcha)
                result = parse_result(html)
                
                if "Error" in result:
                    st.error(result["Error"])
                else:
                    st.subheader("Taxpayer Details")
                    st.write("**Legal Name:**", result["Legal Name"])
                    st.write("**Trade Name:**", result["Trade Name"])
                    st.write("**Status:**", result["Status"])
                    st.write("**Filing Frequency:**", result["Filing Frequency"])
                    
                    if result["Filing Table"]:
                        df = pd.DataFrame(result["Filing Table"], columns=["Period", "Return Type", "Status"])
                        st.dataframe(df)
                        
                        # Export options
                        st.download_button("Download as Excel", df.to_csv(index=False).encode("utf-8"), "filing_table.csv")
                        st.download_button("Download as PDF", df.to_string().encode("utf-8"), "filing_table.pdf")
        else:
            st.error("Could not fetch captcha. GST portal may have changed.")
    else:
        st.error("Invalid GSTIN format. Please check again.")
