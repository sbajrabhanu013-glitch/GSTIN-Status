import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

PROD_BASE_URL = "https://api.sandbox.co.in"
TEST_BASE_URL = "https://test-api.sandbox.co.in"

st.set_page_config(page_title="Sandbox API Auth Test", page_icon="🔐", layout="centered")
st.title("🔐 Sandbox API Key Test")

st.info(
    "Use this only to test whether your Sandbox API Key and API Secret are valid. "
    "Do not enter GST portal username/password here."
)

env = st.selectbox("Environment", ["production", "test"])
base_url = PROD_BASE_URL if env == "production" else TEST_BASE_URL

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

st.write("Endpoint used:")
st.code(f"POST {base_url}/authenticate")

if st.button("Test Authentication", type="primary"):
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()

    if not api_key or not api_secret:
        st.error("Please enter both API Key and API Secret.")
        st.stop()

    headers = {
        "x-api-key": api_key,
        "x-api-secret": api_secret,
    }

    try:
        response = requests.post(
            f"{base_url}/authenticate",
            headers=headers,
            timeout=45,
        )

        try:
            payload = response.json()
        except Exception:
            payload = {"raw_response": response.text}

        st.write("HTTP Status:", response.status_code)

        if response.status_code == 200:
            token = payload.get("data", {}).get("access_token")
            if token:
                st.success("Authentication successful. Your API Key and Secret are valid for this environment.")
                st.write("Access token received successfully.")
            else:
                st.warning("Authentication returned 200, but access_token was not found.")
                st.json(payload)
        else:
            st.error("Authentication failed.")
            st.json(payload)

            if response.status_code == 401:
                st.warning(
                    "401 Invalid API key usually means one of these: wrong environment, wrong key copied, "
                    "extra spaces, key not active, or key belongs to another Sandbox project/account."
                )

    except Exception as exc:
        st.error(f"Request failed: {exc}")
