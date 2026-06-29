import re
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st

GST_PORTAL_URL = "https://services.gst.gov.in/services/searchtp"

# -------------------------------
# GSTIN Validation
# -------------------------------
def validate_gstin(gstin):
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    return re.match(pattern, gstin)

# -------------------------------
# Fetch Captcha
# -------------------------------
def fetch_captcha(session):
    captcha_page = session.get(GST_PORTAL_URL)
    soup = BeautifulSoup(captcha_page.text, "html.parser")
    # Try multiple ways to locate captcha image
    captcha_img_tag = soup.find("img", {"id": "captchaImg"}) \
                      or soup.find("img", {"class": "captcha-img"}) \
                      or soup.find("img", {"alt": "Captcha"})
    if captcha_img_tag and "src" in captcha_img_tag.attrs:
        captcha_url = "https://services.gst.gov.in" + captcha_img_tag["src"]
        return captcha_url
    return None

# -------------------------------
# Submit GSTIN + Captcha
# -------------------------------
def submit_search(session, gstin, captcha):
    payload = {"gstin": gstin, "captcha": captcha}
    response = session.post(GST_PORTAL_URL, data=payload)
    return response.text

# -------------------------------
# Parse Result Page
# -------------------------------
def parse_result(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    try:
        result["Legal Name"] = soup.find("span", {"id": "legalName"}).text.strip()
        result["Trade Name"] = soup.find("span", {"id": "tradeName"}).text.strip()
        result["Status"] = soup.find("span", {"id": "status"}).text.strip()
        result["Filing Frequency"] = soup.find("span", {"id": "filingFreq"}).text.strip()
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
                    st.write("**Legal Name:**", result.get("Legal Name", "N/A"))
                    st.write("**Trade Name:**", result.get("Trade Name", "N/A"))
                    st.write("**Status:**", result.get("Status", "N/A"))
                    st.write("**Filing Frequency:**", result.get("Filing Frequency", "N/A"))
                    
                    if result.get("Filing Table"):
                        df = pd.DataFrame(result["Filing Table"], columns=["Period", "Return Type", "Status"])
                        st.dataframe(df)
                        st.download_button("Download as Excel", df.to_csv(index=False).encode("utf-8"), "filing_table.csv")
                        st.download_button("Download as PDF", df.to_string().encode("utf-8"), "filing_table.pdf")
        else:
            st.error("Could not fetch captcha. GST portal HTML may have changed.")
            st.info("Tip: Inspect the GST portal manually and update the selector in fetch_captcha().")
    else:
        st.error("Invalid GSTIN format. Please check again.")
