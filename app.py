



import streamlit as st
import pandas as pd
import sqlite3
import hmac
import re
from io import BytesIO
from pathlib import Path

# ---------------------------------------------------------
# PAGE SETTINGS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Barcode Checking Portal",
    page_icon="🔎",
    layout="wide"
)

DB_PATH = Path("barcode_history.db")

# ---------------------------------------------------------
# LOGIN SECURITY
# Set PORTAL_USERNAME and PORTAL_PASSWORD in Streamlit Cloud
# Settings -> Secrets. Never put your real password in this file.
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Barcode Checking Portal")
    st.subheader("Secure Login")

    if "PORTAL_USERNAME" not in st.secrets or "PORTAL_PASSWORD" not in st.secrets:
        st.error("Login setup is incomplete. Please add PORTAL_USERNAME and PORTAL_PASSWORD in Streamlit Cloud → App settings → Secrets.")
        st.code(
            'PORTAL_USERNAME = "your_username"\n'
            'PORTAL_PASSWORD = "your_strong_password"',
            language="toml"
        )
        st.stop()

    with st.form("portal_login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", use_container_width=True)

    if submitted:
        correct_username = str(st.secrets["PORTAL_USERNAME"])
        correct_password = str(st.secrets["PORTAL_PASSWORD"])

        username_ok = hmac.compare_digest(username, correct_username)
        password_ok = hmac.compare_digest(password, correct_password)

        if username_ok and password_ok:
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Username ya password ghalat hai.")

    st.stop()

# Keep login active during normal page interactions; logout is explicit.
with st.sidebar:
    st.success("Login successful")
    if st.button("Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()

# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS barcode_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL DEFAULT '',
            barcode TEXT NOT NULL,
            creation_date TEXT NOT NULL,
            resolve_date TEXT NOT NULL,
            remarks TEXT NOT NULL DEFAULT '',
            UNIQUE(barcode, creation_date, resolve_date)
        )
        """
    )
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(barcode_history)").fetchall()
    }
    if "order_number" not in existing_columns:
        conn.execute("ALTER TABLE barcode_history ADD COLUMN order_number TEXT NOT NULL DEFAULT ''")
    if "remarks" not in existing_columns:
        conn.execute("ALTER TABLE barcode_history ADD COLUMN remarks TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn





