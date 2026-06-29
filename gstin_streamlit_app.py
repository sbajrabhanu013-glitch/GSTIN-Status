import re
import pandas as pd
import streamlit as st
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
import chromedriver_autoinstaller

# Auto-install ChromeDriver
chromedriver_autoinstaller.install()

GST_PORTAL_URL = "https://services.gst.gov.in/services/searchtp"

# -------------------------------
# GSTIN Validation
# -------------------------------
def validate_gstin(gstin):
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    return re.match(pattern, gstin)

# -------------------------------
# Selenium Workflow
# -------------------------------
def gst_search(gstin, captcha_text):
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")  # run headless for Streamlit Cloud
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    driver.get(GST_PORTAL_URL)
    time.sleep(3)  # wait for JS to load

    # Captcha image
    captcha_img = driver.find_element(By.ID, "captchaImg")
    captcha_src = captcha_img.get_attribute("src")
    captcha_url = "https://services.gst.gov.in" + captcha_src

    # Fill GSTIN and captcha
    gstin_input = driver.find_element(By.ID, "gstin")
    gstin_input.send_keys(gstin)
    captcha_input = driver.find_element(By.ID, "captcha")
    captcha_input.send_keys(captcha_text)

    # Submit
    submit_btn = driver.find_element(By.ID, "searchBtn")
    submit_btn.click()
    time.sleep(3)

    # Extract taxpayer details
    result = {}
    try:
        result["Legal Name"] = driver.find_element(By.ID, "legalName").text
        result["Trade Name"] = driver.find_element(By.ID, "tradeName").text
        result["Status"] = driver.find_element(By.ID, "status").text
        result["Filing Frequency"] = driver.find_element(By.ID, "filingFreq").text

        filing_table = []
        rows = driver.find_elements(By.CSS_SELECTOR, "table#filingTable tr")
        for row in rows[1:]:
            cols = [col.text.strip() for col in row.find_elements(By.TAG_NAME, "td")]
            filing_table.append(cols)
        result["Filing Table"] = filing_table
    except Exception as e:
        result["Error"] = f"Parsing failed: {e}"

    driver.quit()
    return captcha_url, result

# -------------------------------
# Streamlit UI
# -------------------------------
st.title("GST Taxpayer Search App (Selenium)")

gstin = st.text_input("Enter GSTIN")

if gstin:
    if validate_gstin(gstin):
        st.success("GSTIN format looks valid.")

        captcha_url = None
        if st.button("Fetch Captcha"):
            # Launch browser just to get captcha
            options = webdriver.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Chrome(options=options)
            driver.get(GST_PORTAL_URL)
            time.sleep(3)
            captcha_img = driver.find_element(By.ID, "captchaImg")
            captcha_src = captcha_img.get_attribute("src")
            captcha_url = "https://services.gst.gov.in" + captcha_src
            st.image(captcha_url, caption="Enter Captcha")
            driver.quit()

        captcha = st.text_input("Captcha")

        if captcha and st.button("Submit Search"):
            captcha_url, result = gst_search(gstin, captcha)

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
        st.error("Invalid GSTIN format. Please check again.")
