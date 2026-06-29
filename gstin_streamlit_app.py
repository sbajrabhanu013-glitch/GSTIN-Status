import re
import pandas as pd
import streamlit as st
from playwright.sync_api import sync_playwright

GST_PORTAL_URL = "https://services.gst.gov.in/services/searchtp"

# -------------------------------
# GSTIN Validation
# -------------------------------
def validate_gstin(gstin):
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    return re.match(pattern, gstin)

# -------------------------------
# Playwright Workflow
# -------------------------------
def gst_search(gstin, captcha_text):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(GST_PORTAL_URL, timeout=60000)

        # Wait for captcha image
        page.wait_for_selector("img#captchaImg", timeout=10000)
        captcha_src = page.locator("img#captchaImg").get_attribute("src")
        captcha_url = "https://services.gst.gov.in" + captcha_src

        # Fill GSTIN and captcha
        page.fill("input#gstin", gstin)
        page.fill("input#captcha", captcha_text)
        page.click("button#searchBtn")

        # Wait for results
        page.wait_for_selector("span#legalName", timeout=15000)

        # Extract details
        result = {}
        result["Legal Name"] = page.locator("span#legalName").inner_text()
        result["Trade Name"] = page.locator("span#tradeName").inner_text()
        result["Status"] = page.locator("span#status").inner_text()
        result["Filing Frequency"] = page.locator("span#filingFreq").inner_text()

        # Filing table
        filing_table = []
        rows = page.locator("table#filingTable tr").all()
        for row in rows[1:]:
            cols = [c.inner_text().strip() for c in row.locator("td").all()]
            filing_table.append(cols)
        result["Filing Table"] = filing_table

        browser.close()
        return captcha_url, result

# -------------------------------
# Streamlit UI
# -------------------------------
st.title("GST Taxpayer Search App (Playwright)")

gstin = st.text_input("Enter GSTIN")

if gstin:
    if validate_gstin(gstin):
        st.success("GSTIN format looks valid.")

        # Step 1: Show captcha
        if st.button("Fetch Captcha"):
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(GST_PORTAL_URL, timeout=60000)
                page.wait_for_selector("img#captchaImg", timeout=10000)
                captcha_src = page.locator("img#captchaImg").get_attribute("src")
                captcha_url = "https://services.gst.gov.in" + captcha_src
                st.image(captcha_url, caption="Enter Captcha")
                browser.close()

        captcha = st.text_input("Captcha")

        if captcha and st.button("Submit Search"):
            captcha_url, result = gst_search(gstin, captcha)

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
        st.error("Invalid GSTIN format. Please check again.")
