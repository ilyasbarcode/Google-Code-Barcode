import streamlit as st
import pandas as pd
import requests
import hmac
import re
import json
import hashlib
import secrets
from io import BytesIO
from datetime import datetime, timezone

# ---------------------------------------------------------
# PAGE SETTINGS & BRANDING
# ---------------------------------------------------------
st.set_page_config(
    page_title="Google-Code-Barcode | Warranty Management",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_NAME = "GOOGLE-CODE-BARCODE"
APP_SUBTITLE = "Enterprise Warranty Management Portal"
PBKDF2_ITERATIONS = 310_000
PAGE_SIZE = 100
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Corporate navy / white / light-grey visual system.
st.markdown("""
<style>
:root { --gcb-navy:#10233f; --gcb-blue:#1d4e89; --gcb-bg:#f4f6fa; --gcb-border:#e3e8f0; }
.stApp { background:var(--gcb-bg); }
[data-testid="stHeader"] { background:rgba(244,246,250,.92); }
[data-testid="stSidebar"] { background:#10233f; border-right:1px solid #263b59; }
[data-testid="stSidebar"] * { color:#f5f7fb; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#cbd5e1; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding:7px 9px; border-radius:8px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#1d3556; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color:#dbe5f2; }
.block-container { padding-top:1.6rem; padding-bottom:2.5rem; max-width:1500px; }
h1,h2,h3 { color:#10233f; letter-spacing:-.02em; }
[data-testid="stMetric"] { background:#fff; border:1px solid var(--gcb-border); padding:16px 18px; border-radius:13px; box-shadow:0 2px 8px rgba(16,35,63,.035); }
[data-testid="stMetricLabel"] p { color:#64748b; font-size:.82rem; }
[data-testid="stMetricValue"] { color:#10233f; font-weight:700; }
.stButton>button, .stDownloadButton>button, [data-testid="stFormSubmitButton"] button { border-radius:9px; font-weight:600; min-height:42px; }
.stButton>button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background:#1d4e89; border-color:#1d4e89; }
[data-testid="stDataFrame"], [data-testid="stTable"] { border:1px solid var(--gcb-border); border-radius:10px; overflow:hidden; }
[data-testid="stExpander"] { background:#fff; border:1px solid var(--gcb-border); border-radius:10px; }
.gcb-brand { font-size:1.05rem; font-weight:800; letter-spacing:.06em; color:#fff; line-height:1.25; }
.gcb-subbrand { font-size:.72rem; color:#b9c8dc; margin-top:4px; }
.gcb-page-kicker { color:#64748b; text-transform:uppercase; letter-spacing:.12em; font-size:.72rem; font-weight:700; }
.gcb-page-title { color:#10233f; font-size:1.8rem; font-weight:800; margin:.15rem 0 .25rem 0; }
.gcb-page-desc { color:#64748b; font-size:.94rem; margin-bottom:1.15rem; }
.gcb-login-wrap { max-width:520px; margin:5vh auto 0 auto; background:#fff; border:1px solid #e3e8f0; border-radius:18px; padding:2rem; box-shadow:0 12px 40px rgba(16,35,63,.08); }
.gcb-login-brand { color:#10233f; font-size:1.4rem; font-weight:850; letter-spacing:.04em; }
.gcb-login-sub { color:#64748b; font-size:.9rem; margin-top:.3rem; }
@media (max-width: 700px) {
 .block-container { padding:1rem .8rem 2rem .8rem; }
 .gcb-page-title { font-size:1.45rem; }
 [data-testid="stMetric"] { padding:12px; }
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# PERMISSIONS
# Page access and action access are checked independently.
# ---------------------------------------------------------
PERMISSION_GROUPS = {
    "Dashboard": [("view_dashboard", "View Dashboard")],
    "Barcode Checking": [
        ("open_barcode_checking", "Open Barcode Checking"),
        ("upload_temporary_data", "Upload Temporary Data"),
    ],
    "Master Data": [
        ("upload_master_data", "Upload Master Data"),
        ("view_records", "View Records"),
        ("delete_records", "Delete Records"),
    ],
    "Warranty Search": [("search_warranty", "Search Warranty")],
    "Export / Backup": [
        ("export_excel", "Export Excel"),
        ("download_full_backup", "Download Full Backup"),
    ],
    "User Management": [
        ("create_users", "Create Users"),
        ("edit_users", "Edit Users"),
        ("disable_users", "Disable Users"),
    ],
}
ALL_PERMISSION_KEYS = [key for group in PERMISSION_GROUPS.values() for key, _ in group]


def current_user():
    return st.session_state.get("auth_user", {})


def is_admin():
    return bool(current_user().get("is_admin"))


def has_permission(permission):
    if is_admin():
        return True
    return permission in current_user().get("permissions", [])


def require_permission(permission):
    """Server-side authorization guard. Must be called inside protected actions."""
    if not has_permission(permission):
        raise PermissionError("Aapke account ko is action ki permission nahi hai.")


def can_open_user_management():
    return is_admin() or any(has_permission(p) for p in ("create_users", "edit_users", "disable_users"))


def render_page_header(section, title, description):
    st.markdown(f'<div class="gcb-page-kicker">{section}</div><div class="gcb-page-title">{title}</div><div class="gcb-page-desc">{description}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------
# TURSO HTTP API
# Existing HTTP pipeline approach retained; no native libsql dependency.
# ---------------------------------------------------------
def turso_settings():
    try:
        raw_url = str(st.secrets.get("TURSO_DATABASE_URL", "")).strip()
        token = str(st.secrets.get("TURSO_AUTH_TOKEN", "")).strip()
    except Exception:
        raw_url, token = "", ""
    if not raw_url or not token:
        raise RuntimeError("Turso settings missing hain. Streamlit Secrets mein TURSO_DATABASE_URL aur TURSO_AUTH_TOKEN add karein.")
    url = raw_url.rstrip("/")
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    elif not url.startswith("https://"):
        raise RuntimeError("TURSO_DATABASE_URL libsql:// ya https:// se start hona chahiye.")
    return url + "/v2/pipeline", token


def _hrana_value(value):
    if value is None or pd.isna(value):
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode_hrana(value):
    if not isinstance(value, dict):
        return value
    typ = value.get("type")
    if typ == "null": return None
    if typ == "integer": return int(value.get("value", 0))
    if typ == "float": return float(value.get("value", 0))
    if typ == "text": return value.get("value", "")
    if typ == "blob": return value.get("base64", "")
    return value.get("value")


def turso_pipeline(statements):
    endpoint, token = turso_settings()
    request_items = []
    for sql, params in statements:
        request_items.append({"type": "execute", "stmt": {
            "sql": sql,
            "args": [_hrana_value(v) for v in (params or [])],
        }})
    request_items.append({"type": "close"})
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"requests": request_items},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Turso se connection nahi ho saka: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise RuntimeError(f"Turso HTTP error {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Turso ne valid JSON response nahi diya.") from exc
    outputs = []
    for item in payload.get("results", []):
        if item.get("type") == "error":
            err = item.get("error", {})
            raise RuntimeError(err.get("message", "Unknown Turso database error"))
        result = item.get("response", {}).get("result", {})
        cols = [c.get("name", "") for c in result.get("cols", [])]
        rows = [[_decode_hrana(v) for v in row] for row in result.get("rows", [])]
        outputs.append({"columns": cols, "rows": rows, "affected": result.get("affected_row_count", 0)})
    return outputs


def ensure_database_schema():
    """Create only missing portal tables/indexes; never drop or recreate master_records."""
    turso_pipeline([("""CREATE TABLE IF NOT EXISTS master_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode TEXT NOT NULL,
        order_number TEXT NOT NULL DEFAULT '',
        creation_date TEXT NOT NULL,
        resolve_date TEXT NOT NULL,
        remarks TEXT NOT NULL DEFAULT '',
        uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""", [])])
    schema = turso_pipeline([("PRAGMA table_info(master_records)", [])])[0]
    existing_columns = {row[1] for row in schema["rows"] if len(row) > 1}
    required_columns = {"id", "barcode", "order_number", "creation_date", "resolve_date", "remarks", "uploaded_at"}
    missing = sorted(required_columns - existing_columns)
    if missing:
        raise RuntimeError("Existing master_records schema expected structure se different hai. Missing columns: " + ", ".join(missing) + ". Koi migration automatically apply nahi ki gayi.")

    turso_pipeline([
        ("CREATE INDEX IF NOT EXISTS idx_master_barcode_creation ON master_records(barcode, creation_date)", []),
        ("CREATE INDEX IF NOT EXISTS idx_master_order_number ON master_records(order_number)", []),
        ("CREATE INDEX IF NOT EXISTS idx_master_uploaded_at ON master_records(uploaded_at)", []),
        ("CREATE TABLE IF NOT EXISTS portal_users ("
         "id INTEGER PRIMARY KEY AUTOINCREMENT, "
         "username TEXT NOT NULL COLLATE NOCASE UNIQUE, "
         "password_hash TEXT NOT NULL, salt TEXT NOT NULL, "
         "iterations INTEGER NOT NULL, permissions_json TEXT NOT NULL DEFAULT '[]', "
         "is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
         "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_login TEXT, created_by TEXT NOT NULL DEFAULT ''"
         ")", []),
        ("CREATE INDEX IF NOT EXISTS idx_portal_users_active ON portal_users(is_active)", []),
    ])


def db_rows(sql, params=None):
    result = turso_pipeline([(sql, params or [])])[0]
    return pd.DataFrame(result["rows"], columns=result["columns"])


# ---------------------------------------------------------
# USER AUTHENTICATION — PBKDF2-HMAC-SHA256
# ---------------------------------------------------------
def hash_password(password, salt_hex=None, iterations=PBKDF2_ITERATIONS):
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    return digest.hex(), salt_hex, iterations


def verify_password(password, stored_hash, salt_hex, iterations):
    try:
        candidate, _, _ = hash_password(password, salt_hex, int(iterations))
        return hmac.compare_digest(candidate, str(stored_hash))
    except (ValueError, TypeError):
        return False


def get_portal_user(username):
    df = db_rows(
        "SELECT id, username, password_hash, salt, iterations, permissions_json, is_active FROM portal_users WHERE username = ? COLLATE NOCASE LIMIT 1",
        [username.strip()],
    )
    return None if df.empty else df.iloc[0].to_dict()


def list_portal_users():
    return db_rows("SELECT id, username, is_active, created_at, updated_at, last_login, created_by FROM portal_users ORDER BY username COLLATE NOCASE")


def create_portal_user(username, password, permissions, created_by):
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ValueError("Username 3–32 characters ka ho aur sirf letters, numbers, dot, underscore ya hyphen use kare.")
    if len(password) < 12:
        raise ValueError("Password kam az kam 12 characters ka hona chahiye.")
    if not permissions:
        raise ValueError("Kam az kam ek permission select karein.")
    if username.lower() == str(st.secrets.get("PORTAL_USERNAME", "")).strip().lower():
        raise ValueError("Ye username bootstrap admin ke liye reserved hai.")
    digest, salt, iterations = hash_password(password)
    turso_pipeline([(
        "INSERT INTO portal_users (username, password_hash, salt, iterations, permissions_json, is_active, created_by) VALUES (?, ?, ?, ?, ?, 1, ?)",
        [username, digest, salt, iterations, json.dumps(sorted(set(permissions))), created_by],
    )])


def update_portal_user(username, permissions, new_password=None):
    if not permissions:
        raise ValueError("Kam az kam ek permission select rehni chahiye.")
    if new_password:
        if len(new_password) < 12:
            raise ValueError("New password kam az kam 12 characters ka hona chahiye.")
        digest, salt, iterations = hash_password(new_password)
        turso_pipeline([(
            "UPDATE portal_users SET permissions_json = ?, password_hash = ?, salt = ?, iterations = ?, updated_at = CURRENT_TIMESTAMP WHERE username = ? COLLATE NOCASE",
            [json.dumps(sorted(set(permissions))), digest, salt, iterations, username],
        )])
    else:
        turso_pipeline([(
            "UPDATE portal_users SET permissions_json = ?, updated_at = CURRENT_TIMESTAMP WHERE username = ? COLLATE NOCASE",
            [json.dumps(sorted(set(permissions))), username],
        )])


def disable_portal_user(username):
    turso_pipeline([("UPDATE portal_users SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE username = ? COLLATE NOCASE", [username])])


def authenticate_user(username, password):
    # Admin bootstrap remains in Streamlit Secrets and is not stored in the database.
    try:
        admin_username = str(st.secrets.get("PORTAL_USERNAME", ""))
        admin_password = str(st.secrets.get("PORTAL_PASSWORD", ""))
    except Exception:
        admin_username, admin_password = "", ""
    if admin_username and admin_password:
        if hmac.compare_digest(username, admin_username) and hmac.compare_digest(password, admin_password):
            return {"username": admin_username, "is_admin": True, "permissions": ALL_PERMISSION_KEYS}

    ensure_database_schema()
    user = get_portal_user(username)
    if not user or int(user.get("is_active", 0)) != 1:
        return None
    if not verify_password(password, user["password_hash"], user["salt"], user["iterations"]):
        return None
    try:
        permissions = json.loads(user.get("permissions_json") or "[]")
    except (TypeError, ValueError):
        permissions = []
    permissions = [p for p in permissions if p in ALL_PERMISSION_KEYS]
    turso_pipeline([("UPDATE portal_users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", [int(user["id"])])])
    return {"username": user["username"], "is_admin": False, "permissions": permissions}


def refresh_session_user():
    """Re-check active status and permissions on every Streamlit rerun."""
    user = current_user()
    if not user:
        return False
    if user.get("is_admin"):
        return True
    latest = get_portal_user(user.get("username", ""))
    if not latest or int(latest.get("is_active", 0)) != 1:
        st.session_state.pop("auth_user", None)
        st.session_state.pop("master_backup_df", None)
        return False
    try:
        permissions = json.loads(latest.get("permissions_json") or "[]")
    except (TypeError, ValueError):
        permissions = []
    st.session_state.auth_user = {
        "username": latest["username"], "is_admin": False,
        "permissions": [p for p in permissions if p in ALL_PERMISSION_KEYS],
    }
    return True


def render_login():
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        with st.container(border=True):
            st.markdown('<div style="font-size:2rem">🔎</div><div class="gcb-login-brand">GOOGLE-CODE-BARCODE</div><div class="gcb-login-sub">Enterprise Warranty Management Portal</div>', unsafe_allow_html=True)
            st.divider()
            st.markdown("### Secure sign in")
            with st.form("portal_login_form"):
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                submitted = st.form_submit_button("Sign in securely", type="primary", use_container_width=True)
            if submitted:
                if not username.strip() or not password:
                    st.error("Username aur password dono enter karein.")
                else:
                    try:
                        user = authenticate_user(username.strip(), password)
                        if user:
                            st.session_state.auth_user = user
                            st.rerun()
                        else:
                            st.error("Username ya password ghalat hai, ya account disabled hai.")
                    except Exception as exc:
                        st.error(f"Login/database error: {exc}")
            st.caption("Authorized personnel only · Secure access")


# Recognised terms from the material list. Longer names are checked first
# so "Door Gasket 400SAX Chest" is not confused with "Door Gasket 400SAX".
MATERIAL_TERMS = [
    ("Compressor", ["COMPRESSOR REFURBISHED R134 1/3 HP", "COMPRESSOR REPLACED", "COMPRESSOR"]),
    ("Gas", ["GAS CHARGED", "GAS CHARGING", "GAS CHARGE", "GAS"]),
    ("CFS Basic Service", ["CFS BASIC SERVICE"]),
    ("Door Gasket 400SAX Chest", ["DOOR GASKET 400SAX CHEST"]),
    ("Door Gasket 400SAX", ["DOOR GASKET 400SAX"]),
    ("Compressor Condensor 1/3", ["CONDENSOR 1/3", "CONDENSER 1/3"]),
    ("Capacitor", ["CAPACITOR"]),
    ("Caster Wheel", ["CASTER WHEEL"]),
    ("Fan Motor 10W", ["FAN MOTOR 10W"]),
    ("Fan Motor 5W", ["FAN MOTOR 5W"]),
    ("LED", ["LED REPLACED", "LED"]),
    ("Power Cord Plug-VISI & Chest", ["POWER CORD PLUG-VISI & CHEST", "POWER CORD PLUG"]),
    ("Relay", ["RELAY"]),
    ("Overload", ["OVERLOAD"]),
    ("Thermostat - Chest", ["THERMOSTAT - CHEST", "THERMOSTAT CHEST"]),
    ("Door Complete Chest", ["DOOR COMPLETE CHEST"]),
    ("Door Repaired", ["DOOR REPAIRED", "DOORREPAIRED"]),
]


def detect_materials(remarks):
    """Return canonical materials found in a single Remarks cell."""
    text = str(remarks or "").upper().replace("–", "-").replace("—", "-")
    found = []
    for canonical, terms in MATERIAL_TERMS:
        # Match complete terms/phrases so "GAS" does not accidentally match "GASKET".
        matched = any(
            re.search(r"(?<![A-Z0-9])" + re.escape(term) + r"(?![A-Z0-9])", text)
            for term in terms
        )
        if matched and canonical not in found:
            found.append(canonical)
    return found


def is_part(material):
    return material not in {"Compressor", "Gas", "CFS Basic Service"}


def gap_result(current_creation, previous_resolve):
    current_day = pd.Timestamp(current_creation).normalize()
    previous_day = pd.Timestamp(previous_resolve).normalize()
    if current_day < previous_day:
        return "Created Before Previous Resolve", (current_day - previous_day).days
    days = (current_day - previous_day).days
    if days <= 30:
        return "Within 30 Days", days
    if days <= 90:
        return "Within 90 Days", days
    return "After 90 Days", days


def check_barcodes(input_df, saved_history):
    """Check each warranty category independently and show separate result columns."""
    working = input_df.copy()
    working["__original_order"] = range(len(working))
    working = working.sort_values(["Creation Date", "__original_order"], kind="stable")

    previous_records = list(saved_history)
    output_rows = []

    for _, row in working.iterrows():
        barcode = str(row["Barcode"]).strip()
        creation = pd.Timestamp(row["Creation Date"])
        resolve = pd.Timestamp(row["Resolve Date"])
        remarks = str(row.get("Remarks", "") or "")
        materials = detect_materials(remarks)

        current_parts = [m for m in materials if is_part(m)]
        current_has_compressor = "Compressor" in materials
        current_has_gas = "Gas" in materials
        current_is_service = not materials or materials == ["CFS Basic Service"]

        earlier = [
            rec for rec in previous_records
            if str(rec.get("Barcode", "")).strip() == barcode
            and pd.Timestamp(rec["Creation Date"]) < creation
        ]
        earlier.sort(key=lambda rec: pd.Timestamp(rec["Creation Date"]), reverse=True)

        compressor_result = ""
        gas_result = ""
        parts_result = ""
        service_result = ""
        warranty_references = []  # (category, previous record used for that category)

        # Compressor warranty: same barcode + previous compressor.
        # Within 30 days and days 31-90 are shown distinctly in the existing column.
        if current_has_compressor:
            previous_compressors = [
                rec for rec in earlier
                if "Compressor" in rec.get("Materials", detect_materials(rec.get("Remarks", "")))
            ]
            if previous_compressors:
                prev = max(previous_compressors, key=lambda rec: pd.Timestamp(rec["Creation Date"]))
                label, days = gap_result(creation, prev["Resolve Date"])
                if label == "Created Before Previous Resolve":
                    compressor_result = label
                    warranty_references.append(("Compressor", prev))
                elif days <= 30:
                    compressor_result = "Within 30 Days"
                    warranty_references.append(("Compressor", prev))
                elif days <= 90:
                    compressor_result = "Within 90 Days"
                    warranty_references.append(("Compressor", prev))
                else:
                    compressor_result = "After 90 Days"
            else:
                compressor_result = "No Previous Compressor"

        # Gas warranty: same barcode + previous gas.
        # Within 30 days and days 31-90 are shown distinctly in the existing column.
        if current_has_gas:
            previous_gas = [
                rec for rec in earlier
                if "Gas" in rec.get("Materials", detect_materials(rec.get("Remarks", "")))
            ]
            if previous_gas:
                prev = max(previous_gas, key=lambda rec: pd.Timestamp(rec["Creation Date"]))
                label, days = gap_result(creation, prev["Resolve Date"])
                if label == "Created Before Previous Resolve":
                    gas_result = label
                    warranty_references.append(("Gas", prev))
                elif days <= 30:
                    gas_result = "Within 30 Days"
                    warranty_references.append(("Gas", prev))
                elif days <= 90:
                    gas_result = "Within 90 Days"
                    warranty_references.append(("Gas", prev))
                else:
                    gas_result = "After 90 Days"
            else:
                gas_result = "No Previous Gas"

        # Parts warranty: any same part repeats for the same barcode; 30-day period.
        if current_parts:
            part_matches = []
            for material in current_parts:
                previous_parts = [
                    rec for rec in earlier
                    if material in rec.get("Materials", detect_materials(rec.get("Remarks", "")))
                ]
                if previous_parts:
                    prev = max(previous_parts, key=lambda rec: pd.Timestamp(rec["Creation Date"]))
                    label, days = gap_result(creation, prev["Resolve Date"])
                    part_matches.append((label, days, prev))
            if part_matches:
                # Only retain references for exact repeated parts still within warranty.
                valid_part_matches = [
                    (label, days, prev) for label, days, prev in part_matches
                    if label == "Created Before Previous Resolve" or days <= 30
                ]
                if any(label == "Created Before Previous Resolve" for label, _, _ in valid_part_matches):
                    parts_result = "Created Before Previous Resolve"
                elif valid_part_matches:
                    parts_result = "Within 30 Days"
                else:
                    parts_result = "No Warranty Part"
                for _, _, prev in valid_part_matches:
                    warranty_references.append(("Parts", prev))
            else:
                parts_result = "No Warranty Part"

        # Service warranty applies only when no actual part, compressor, or gas
        # is recorded in the current complaint (CFS Basic Service counts as service).
        if current_is_service:
            previous_service = [
                rec for rec in earlier
                if not rec.get("Materials", detect_materials(rec.get("Remarks", "")))
                or rec.get("Materials", detect_materials(rec.get("Remarks", ""))) == ["CFS Basic Service"]
            ]
            if previous_service:
                prev = max(previous_service, key=lambda rec: pd.Timestamp(rec["Creation Date"]))
                label, days = gap_result(creation, prev["Resolve Date"])
                if label == "Created Before Previous Resolve":
                    service_result = label
                    warranty_references.append(("Service", prev))
                elif days <= 30:
                    service_result = "Within 30 Days"
                    warranty_references.append(("Service", prev))
                else:
                    service_result = "After 30 Days"
            else:
                service_result = "First Service"

        out = row.drop(labels=["__original_order"]).to_dict()
        out["Detected Materials"] = ", ".join(materials) if materials else "Service"
        out["Compressor Warranty"] = compressor_result
        out["Gas Warranty"] = gas_result
        out["Parts Warranty"] = parts_result
        out["Service Warranty"] = service_result

        # Previous Order Number and Resolve Date contain only order numbers and dates.
        # Each order/date pair refers to a previous complaint that triggered warranty.
        if warranty_references:
            order_parts = []
            date_parts = []
            seen_refs = set()
            for category, prev in warranty_references:
                order_number = str(prev.get("Order Number", "") or "").strip() or "Unknown"
                resolve_date = pd.Timestamp(prev["Resolve Date"])
                ref_key = (order_number, resolve_date.strftime("%Y-%m-%d"))
                if ref_key in seen_refs:
                    continue
                seen_refs.add(ref_key)
                order_parts.append(order_number)
                date_parts.append(resolve_date.strftime("%Y-%m-%d"))
            out["Previous Order Number"] = ", ".join(order_parts)
            out["Previous Resolve Date"] = ", ".join(date_parts)
        else:
            # No active warranty match: keep previous order/date blank.
            out["Previous Order Number"] = ""
            out["Previous Resolve Date"] = ""

        if earlier:
            latest_previous = earlier[0]
            out["Days Between Complaints"] = (
                creation.normalize() - pd.Timestamp(latest_previous["Resolve Date"]).normalize()
            ).days
        else:
            out["Days Between Complaints"] = None

        output_rows.append(out)

        # Include this complaint in the working history for later rows in this upload.
        previous_records.append({
            "Order Number": str(row.get("Order Number", "") or ""),
            "Barcode": barcode,
            "Creation Date": creation,
            "Resolve Date": resolve,
            "Remarks": remarks,
            "Materials": materials,
        })

    result_df = pd.DataFrame(output_rows)
    preferred = [
        "Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks",
        "Detected Materials", "Previous Order Number", "Previous Resolve Date",
        "Days Between Complaints", "Compressor Warranty", "Gas Warranty",
        "Parts Warranty", "Service Warranty"
    ]
    ordered = [col for col in preferred if col in result_df.columns]
    ordered += [col for col in result_df.columns if col not in ordered]
    return result_df[ordered]

def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Barcode Results")
    output.seek(0)
    return output.getvalue()


# ---------------------------------------------------------
# MASTER DATA OPERATIONS
# ---------------------------------------------------------
def prepare_master_upload(file):
    try:
        df = pd.read_excel(file, dtype={"Order Number": str, "Barcode": str})
    except Exception as exc:
        raise ValueError(f"Excel file read nahi ho saki: {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Barcode", "Order Number", "Creation Date", "Resolve Date", "Remarks"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("Required columns missing hain: " + ", ".join(missing))
    if df.empty:
        raise ValueError("Excel file mein koi data row nahi hai.")
    for col in ["Barcode", "Order Number", "Remarks"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["Creation Date"] = pd.to_datetime(df["Creation Date"], errors="coerce")
    df["Resolve Date"] = pd.to_datetime(df["Resolve Date"], errors="coerce")
    bad = df["Barcode"].eq("") | df["Creation Date"].isna() | df["Resolve Date"].isna()
    if bad.any():
        raise ValueError(f"{int(bad.sum())} rows mein blank Barcode ya invalid date hai.")
    return df[required]


def master_insert(df):
    sql = "INSERT INTO master_records (barcode, order_number, creation_date, resolve_date, remarks) VALUES (?, ?, ?, ?, ?)"
    rows = []
    for _, row in df.iterrows():
        rows.append((str(row["Barcode"]), str(row["Order Number"]),
                     pd.Timestamp(row["Creation Date"]).strftime("%Y-%m-%d %H:%M:%S"),
                     pd.Timestamp(row["Resolve Date"]).strftime("%Y-%m-%d %H:%M:%S"),
                     str(row["Remarks"])))
    # Duplicate rows intentionally remain allowed. Small chunks limit HTTP payload size.
    for start in range(0, len(rows), 100):
        turso_pipeline([(sql, values) for values in rows[start:start + 100]])


def master_fetch(barcodes=None):
    if barcodes:
        frames = []
        clean = list(dict.fromkeys([str(x).strip() for x in barcodes if str(x).strip()]))
        for start in range(0, len(clean), 400):
            batch = clean[start:start + 400]
            marks = ",".join(["?"] * len(batch))
            frames.append(db_rows(
                f"SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records WHERE barcode IN ({marks}) ORDER BY barcode, creation_date, id",
                batch,
            ))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks", "uploaded_at"])
    return db_rows("SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records ORDER BY id DESC")


def master_count(search=""):
    if search.strip():
        term = f"{search.strip()}%"
        df = db_rows("SELECT COUNT(*) AS total FROM master_records WHERE barcode LIKE ? OR order_number LIKE ?", [term, term])
    else:
        df = db_rows("SELECT COUNT(*) AS total FROM master_records")
    return int(df.iloc[0]["total"]) if not df.empty else 0


def master_fetch_page(search="", limit=PAGE_SIZE, offset=0):
    if search.strip():
        term = f"{search.strip()}%"
        return db_rows(
            "SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records WHERE barcode LIKE ? OR order_number LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
            [term, term, int(limit), int(offset)],
        )
    return db_rows(
        "SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records ORDER BY id DESC LIMIT ? OFFSET ?",
        [int(limit), int(offset)],
    )


def master_delete(ids):
    safe_ids = [int(x) for x in ids if str(x).isdigit()]
    for start in range(0, len(safe_ids), 100):
        batch = safe_ids[start:start + 100]
        if batch:
            marks = ",".join(["?"] * len(batch))
            turso_pipeline([(f"DELETE FROM master_records WHERE id IN ({marks})", batch)])


def master_to_history(df):
    records = []
    for _, row in df.iterrows():
        c = pd.to_datetime(row["creation_date"], errors="coerce")
        r = pd.to_datetime(row["resolve_date"], errors="coerce")
        if pd.notna(c) and pd.notna(r):
            remarks = str(row.get("remarks", "") or "")
            records.append({"Order Number": str(row.get("order_number", "") or ""),
                            "Barcode": str(row["barcode"]), "Creation Date": c,
                            "Resolve Date": r, "Remarks": remarks,
                            "Materials": detect_materials(remarks)})
    return records


# ---------------------------------------------------------
# DASHBOARD — database aggregates only; no full-table download
# ---------------------------------------------------------
def dashboard_data():
    kpis = db_rows("""SELECT COUNT(*) AS total_records,
        COUNT(DISTINCT barcode) AS unique_barcodes,
        COUNT(DISTINCT order_number) AS unique_orders,
        COALESCE(SUM(CASE WHEN strftime('%Y-%m', uploaded_at) = strftime('%Y-%m', 'now') THEN 1 ELSE 0 END), 0) AS this_month
        FROM master_records""")
    monthly = db_rows("""SELECT substr(uploaded_at, 1, 7) AS month, COUNT(*) AS records
        FROM master_records
        WHERE uploaded_at >= datetime('now', '-11 months')
        GROUP BY substr(uploaded_at, 1, 7) ORDER BY month""")
    recent = db_rows("""SELECT order_number AS 'Order Number', barcode AS Barcode,
        creation_date AS 'Creation Date', uploaded_at AS 'Uploaded At'
        FROM master_records ORDER BY id DESC LIMIT 10""")
    active = db_rows("SELECT COUNT(*) AS active_users FROM portal_users WHERE is_active = 1")
    overview = db_rows("""SELECT MIN(creation_date) AS first_creation_date,
        MAX(creation_date) AS latest_creation_date,
        COUNT(DISTINCT remarks) AS distinct_remarks
        FROM master_records""")
    return kpis.iloc[0].to_dict(), monthly, recent, int(active.iloc[0]["active_users"]), overview.iloc[0].to_dict()


def render_dashboard():
    require_permission("view_dashboard")
    render_page_header("Business Intelligence", "Dashboard", "Persistent Turso Master Data ka live overview aur upload activity.")
    try:
        k, monthly, recent, active_users, overview = dashboard_data()
        cols = st.columns(4)
        cols[0].metric("Master Records", f"{int(k['total_records']):,}")
        cols[1].metric("Unique Barcodes", f"{int(k['unique_barcodes']):,}")
        cols[2].metric("Records This Month", f"{int(k['this_month']):,}")
        cols[3].metric("Active Portal Users", f"{active_users:,}")
        left, right = st.columns([1.55, 1])
        with left:
            st.subheader("Monthly Upload Trend")
            if monthly.empty:
                st.info("Abhi upload trend ke liye data available nahi hai.")
            else:
                chart = monthly.set_index("month")["records"].astype(int)
                st.bar_chart(chart, height=280, use_container_width=True)
                st.caption("Monthly uploaded record count, uploaded_at timestamp ke mutabiq.")
        with right:
            st.subheader("Data Overview")
            overview_rows = [
                ("Total orders", f"{int(k['unique_orders']):,}"),
                ("Distinct remarks", f"{int(overview.get('distinct_remarks') or 0):,}"),
                ("First complaint date", str(overview.get("first_creation_date") or "—")[:10]),
                ("Latest complaint date", str(overview.get("latest_creation_date") or "—")[:10]),
            ]
            st.dataframe(pd.DataFrame(overview_rows, columns=["Metric", "Value"]), hide_index=True, use_container_width=True)
        st.subheader("Recent Uploads — Latest Added Records")
        if recent.empty:
            st.info("Master Data abhi empty hai.")
        else:
            st.dataframe(recent, hide_index=True, use_container_width=True)
    except Exception as exc:
        st.error(f"Dashboard data load nahi ho saka: {exc}")


# ---------------------------------------------------------
# APPLICATION PAGES
# ---------------------------------------------------------
def render_barcode_checking():
    require_permission("open_barcode_checking")
    render_page_header("Warranty Operations", "Barcode Checking", "Excel upload se temporary warranty analysis karein. Is file ka data Turso mein save nahi hota.")
    with st.expander("Warranty checking rules", expanded=False):
        st.markdown("""
        - Compressor: same barcode + previous compressor complaint; 30 aur 90 din.
        - Gas: same barcode + previous gas complaint; 30 aur 90 din.
        - Parts: same barcode + same part; 30 din.
        - Service: 30 din; CFS Basic Service bhi service count hoti hai.
        - Gap current Creation Date aur previous matching Resolve Date se calculate hota hai.
        """)
    if not has_permission("upload_temporary_data"):
        st.info("Aapko Barcode Checking page dekhne ki permission hai, lekin temporary file upload ki permission nahi hai.")
        return
    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"], key="check_upload")
    if uploaded_file is None:
        return
    try:
        input_df = pd.read_excel(uploaded_file, dtype={"Order Number": str, "Barcode": str})
        input_df.columns = [str(c).strip() for c in input_df.columns]
        required = ["Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks"]
        missing = [c for c in required if c not in input_df.columns]
        if missing:
            st.error("Required columns missing hain: " + ", ".join(missing))
            return
        if input_df.empty:
            st.warning("Excel file mein koi data row nahi hai.")
            return
        for col in ["Order Number", "Barcode", "Remarks"]:
            input_df[col] = input_df[col].fillna("").astype(str).str.strip()
        for col in ["Creation Date", "Resolve Date"]:
            input_df[col] = pd.to_datetime(input_df[col], errors="coerce")
        bad = input_df["Barcode"].eq("") | input_df["Creation Date"].isna() | input_df["Resolve Date"].isna()
        if bad.any():
            st.error(f"{int(bad.sum())} rows mein blank Barcode ya invalid date hai.")
            st.dataframe(input_df.loc[bad], use_container_width=True, hide_index=True)
            return
        require_permission("upload_temporary_data")
        barcodes = sorted(input_df["Barcode"].dropna().astype(str).str.strip().unique().tolist())
        with st.spinner("Turso Master Data se warranty history load ho rahi hai..."):
            history = master_to_history(master_fetch(barcodes))
            result_df = check_barcodes(input_df, history)
        a, b, c = st.columns(3)
        warranty_cols = ["Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty"]
        a.metric("Total Rows", f"{len(result_df):,}")
        b.metric("Warranty Matches", int(result_df[warranty_cols].apply(lambda x: x.astype(str).str.startswith("Within ")).sum().sum()))
        c.metric("No Warranty / First", int(result_df[warranty_cols].apply(lambda x: x.astype(str).str.contains("No Warranty|First ", case=False, regex=True)).sum().sum()))
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        if has_permission("export_excel"):
            require_permission("export_excel")
            st.download_button("Download Results Excel", to_excel(result_df), "barcode_checking_results.xlsx", EXCEL_MIME, use_container_width=True)
        else:
            st.caption("Excel download permission aapke account par enabled nahi hai.")
        st.success("Temporary checking complete. Uploaded file database mein save nahi hui.")
    except Exception as exc:
        st.error(f"Checking error: {exc}")


def render_master_upload():
    require_permission("upload_master_data")
    render_page_header("Master Data", "Upload Master Data", "Permanent records ko cloud Master Data mein append karein. Duplicate records allowed hain.")
    st.caption("Required columns: Barcode, Order Number, Creation Date, Resolve Date, Remarks")
    file = st.file_uploader("Choose Master Excel file", type=["xlsx", "xls"], key="master_upload")
    if file is None:
        return
    try:
        df = prepare_master_upload(file)
        st.info(f"{len(df):,} records upload ke liye ready hain.")
        st.dataframe(df.head(20), use_container_width=True, hide_index=True)
        if st.button("Append Records to Master Data", type="primary"):
            require_permission("upload_master_data")
            with st.spinner("Records Turso database mein save ho rahe hain..."):
                master_insert(df)
            st.success(f"{len(df):,} records Master Data mein add ho gaye. Duplicate rows bhi preserve ki gayi hain.")
    except Exception as exc:
        st.error(f"Upload error: {exc}")


def render_master_manage():
    require_permission("view_records")
    render_page_header("Master Data", "View / Manage Records", "Search aur page-wise records review karein. Deletion manual confirmation ke baad hoti hai.")
    search = st.text_input("Search Barcode or Order Number", key="master_search")
    try:
        total = master_count(search)
        st.metric("Matching Master Records", f"{total:,}")
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page_num = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1, key="master_page")
        offset = (int(page_num) - 1) * PAGE_SIZE
        view = master_fetch_page(search, PAGE_SIZE, offset)
        st.caption(f"Showing {len(view):,} of {total:,} records · Page {int(page_num)} of {total_pages}")
        if view.empty:
            st.info("Is search ke liye koi record nahi mila.")
            return
        editable = view[["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"]].copy()
        editable.insert(0, "Select", False)
        edited = st.data_editor(
            editable, use_container_width=True, hide_index=True,
            disabled=["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"],
            column_config={"Select": st.column_config.CheckboxColumn("Select"), "id": st.column_config.NumberColumn("Record ID")},
            key=f"master_editor_{int(page_num)}_{search}",
        )
        selected = edited.loc[edited["Select"], "id"].astype(int).tolist()
        if has_permission("delete_records"):
            require_permission("delete_records")
            if selected:
                st.warning(f"{len(selected)} records selected.")
                if st.button("Delete Selected Records", type="primary"):
                    require_permission("delete_records")
                    st.session_state["confirm_master_delete"] = selected
            pending = st.session_state.get("confirm_master_delete", [])
            if pending:
                st.error(f"Confirmation: {len(pending)} records permanently delete honge.")
                yes, no = st.columns(2)
                if yes.button("Confirm Permanent Delete", type="primary"):
                    require_permission("delete_records")
                    master_delete(pending)
                    st.session_state.pop("confirm_master_delete", None)
                    st.success("Selected records delete ho gaye.")
                    st.rerun()
                if no.button("Cancel"):
                    st.session_state.pop("confirm_master_delete", None)
                    st.rerun()
        else:
            st.caption("Aapke account ko record deletion ki permission nahi hai.")
    except Exception as exc:
        st.error(f"Master Data load error: {exc}")


def render_warranty_search():
    require_permission("search_warranty")
    render_page_header("Warranty Operations", "Warranty Search", "Master Data mein one ya multiple barcodes ki complaint history check karein.")
    raw = st.text_area("Barcode(s) — har line, comma ya semicolon par alag barcode", height=110)
    if st.button("Search Warranty", type="primary"):
        require_permission("search_warranty")
        barcodes = list(dict.fromkeys([x.strip() for x in re.split(r"[\n,;]+", raw) if x.strip()]))
        if not barcodes:
            st.warning("Kam az kam ek barcode enter karein.")
            return
        if len(barcodes) > 500:
            st.error("Ek search mein maximum 500 barcodes allowed hain.")
            return
        try:
            with st.spinner("Master Data search ho raha hai..."):
                records_df = master_fetch(barcodes)
            if records_df.empty:
                st.warning("Entered barcodes ka koi record nahi mila.")
                return
            input_df = records_df.rename(columns={"barcode": "Barcode", "order_number": "Order Number", "creation_date": "Creation Date", "resolve_date": "Resolve Date", "remarks": "Remarks"})
            input_df["Creation Date"] = pd.to_datetime(input_df["Creation Date"], errors="coerce")
            input_df["Resolve Date"] = pd.to_datetime(input_df["Resolve Date"], errors="coerce")
            input_df = input_df[["Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks"]]
            result = check_barcodes(input_df, [])
            st.metric("Matched Master Records", f"{len(result):,}")
            st.dataframe(result, use_container_width=True, hide_index=True)
            if has_permission("export_excel"):
                require_permission("export_excel")
                st.download_button("Download Warranty Search Excel", to_excel(result), "warranty_search_results.xlsx", EXCEL_MIME)
            else:
                st.caption("Excel export permission aapke account par enabled nahi hai.")
        except Exception as exc:
            st.error(f"Warranty Search error: {exc}")


def render_export_backup():
    render_page_header("Data Governance", "Export / Backup", "Excel results export aur complete database backup alag permissions ke zariye control hote hain.")
    if has_permission("export_excel"):
        st.subheader("Excel Export")
        st.write("Barcode Checking aur Warranty Search ke result pages par permitted Excel downloads available honge.")
        st.success("Export Excel permission active hai.")
    else:
        st.info("Aapke account par Export Excel permission enabled nahi hai.")
    if has_permission("download_full_backup"):
        require_permission("download_full_backup")
        st.divider()
        st.subheader("Full Master Data Backup")
        st.warning("Full backup mein tamam Master Data records shamil honge. Large database ke liye file banne mein waqt lag sakta hai.")
        if st.button("Prepare Full Database Backup", type="primary"):
            require_permission("download_full_backup")
            try:
                with st.spinner("Full Master Data backup load ho raha hai..."):
                    st.session_state["master_backup_df"] = master_fetch()
                st.success("Backup ready hai.")
            except Exception as exc:
                st.error(f"Backup load error: {exc}")
        df = st.session_state.get("master_backup_df")
        if df is not None:
            st.metric("Backup Records", f"{len(df):,}")
            if not df.empty:
                backup = df.rename(columns={"id": "Record ID", "barcode": "Barcode", "order_number": "Order Number", "creation_date": "Creation Date", "resolve_date": "Resolve Date", "remarks": "Remarks", "uploaded_at": "Uploaded At"})
                require_permission("download_full_backup")
                st.download_button("Download Full Master Data Backup", to_excel(backup), "Google-Code-Barcode_Master_Backup.xlsx", EXCEL_MIME, type="primary")
            else:
                st.info("Master Data empty hai; backup mein koi record nahi.")
    else:
        st.info("Full database backup permission enabled nahi hai.")


# ---------------------------------------------------------
# USER MANAGEMENT — each operation has its own permission check
# ---------------------------------------------------------
def render_permission_checkboxes(default_permissions=None, key_prefix="perm"):
    selected = []
    defaults = set(default_permissions or [])
    cols = st.columns(2)
    col_index = 0
    for group, permissions in PERMISSION_GROUPS.items():
        with cols[col_index % 2]:
            st.markdown(f"**{group}**")
            for key, label in permissions:
                if st.checkbox(label, value=(key in defaults), key=f"{key_prefix}_{key}"):
                    selected.append(key)
        col_index += 1
    return selected


def render_user_management():
    if not can_open_user_management():
        raise PermissionError("User Management access denied.")
    render_page_header("Administration", "User Management", "Individual accounts, account status aur custom permissions manage karein.")
    try:
        users = list_portal_users()
        if not users.empty:
            display = users[["username", "is_active", "created_at", "last_login", "created_by"]].copy()
            display["Status"] = display["is_active"].map({1: "Active", 0: "Disabled"})
            display = display.rename(columns={"username": "Username", "created_at": "Created At", "last_login": "Last Login", "created_by": "Created By"})
            st.subheader("Portal Accounts")
            st.dataframe(display[["Username", "Status", "Created At", "Last Login", "Created By"]], hide_index=True, use_container_width=True)
        else:
            st.info("Abhi koi individual user account create nahi hua.")

        tab_names = []
        if has_permission("create_users"): tab_names.append("Create User")
        if has_permission("edit_users"): tab_names.append("Edit User")
        if has_permission("disable_users"): tab_names.append("Disable User")
        if not tab_names:
            st.info("Aapke paas user-management action permission nahi hai.")
            return
        tabs = st.tabs(tab_names)
        for tab, tab_name in zip(tabs, tab_names):
            with tab:
                if tab_name == "Create User":
                    require_permission("create_users")
                    with st.form("create_user_form"):
                        new_username = st.text_input("New username")
                        new_password = st.text_input("Temporary password (minimum 12 characters)", type="password")
                        confirm_password = st.text_input("Confirm password", type="password")
                        st.markdown("**Assign permissions**")
                        new_permissions = render_permission_checkboxes(key_prefix="create")
                        create_submit = st.form_submit_button("Create Account", type="primary")
                    if create_submit:
                        require_permission("create_users")
                        if new_password != confirm_password:
                            st.error("Password aur confirmation match nahi karte.")
                        else:
                            try:
                                if not is_admin() and not set(new_permissions).issubset(set(current_user().get("permissions", []))):
                                    raise PermissionError("Aap sirf woh permissions assign kar sakte hain jo aapke apne account par hain.")
                                create_portal_user(new_username, new_password, new_permissions, current_user().get("username", ""))
                                st.success("User account successfully create ho gaya.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"User create nahi ho saka: {exc}")
                elif tab_name == "Edit User":
                    require_permission("edit_users")
                    if users.empty:
                        st.info("Edit karne ke liye user account available nahi.")
                    else:
                        choices = users["username"].tolist()
                        selected_user = st.selectbox("Select account to edit", choices, key="edit_user_select")
                        target = get_portal_user(selected_user)
                        try:
                            current_permissions = json.loads(target.get("permissions_json") or "[]")
                        except (TypeError, ValueError):
                            current_permissions = []
                        with st.form("edit_user_form"):
                            st.caption("Password blank chhorne se current password unchanged rahega.")
                            replacement_password = st.text_input("New password (optional, minimum 12 characters)", type="password")
                            st.markdown("**Update permissions**")
                            edited_permissions = render_permission_checkboxes(current_permissions, key_prefix="edit")
                            edit_submit = st.form_submit_button("Save User Changes", type="primary")
                        if edit_submit:
                            require_permission("edit_users")
                            try:
                                if not is_admin() and not set(edited_permissions).issubset(set(current_user().get("permissions", []))):
                                    raise PermissionError("Aap sirf apni available permissions assign kar sakte hain.")
                                update_portal_user(selected_user, edited_permissions, replacement_password or None)
                                st.success("User details update ho gayi hain.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"User update nahi ho saka: {exc}")
                elif tab_name == "Disable User":
                    require_permission("disable_users")
                    active_users = users[users["is_active"] == 1] if not users.empty else users
                    if active_users.empty:
                        st.info("Koi active individual account available nahi.")
                    else:
                        target_user = st.selectbox("Select account to disable", active_users["username"].tolist(), key="disable_user_select")
                        st.warning("Disabled user dobara login nahi kar sakega. Is action se master records affect nahi honge.")
                        if st.button("Disable Selected Account", type="primary"):
                            require_permission("disable_users")
                            if target_user.lower() == current_user().get("username", "").lower():
                                st.error("Aap apna current account khud disable nahi kar sakte.")
                            else:
                                disable_portal_user(target_user)
                                st.success("User account disable ho gaya.")
                                st.rerun()
    except Exception as exc:
        st.error(f"User Management error: {exc}")


# ---------------------------------------------------------
# MAIN PORTAL
# ---------------------------------------------------------
if not current_user():
    render_login()
    st.stop()

try:
    if not refresh_session_user():
        st.warning("Aapka account disabled ya unavailable hai. Dobara login karein.")
        render_login()
        st.stop()
except Exception as exc:
    st.error(f"Session security check failed: {exc}")
    st.stop()

# Verify existing schema before any page runs. This is non-destructive.
try:
    ensure_database_schema()
except Exception as exc:
    st.error(f"Database initialization failed: {exc}")
    st.stop()

user = current_user()
with st.sidebar:
    st.markdown('<div style="font-size:1.7rem">🔎</div><div class="gcb-brand">GOOGLE-CODE-BARCODE</div><div class="gcb-subbrand">Enterprise Warranty Management</div>', unsafe_allow_html=True)
    st.divider()
    st.caption("SIGNED IN AS")
    st.markdown(f"**{user.get('username', 'User')}**")
    st.caption("Administrator" if is_admin() else "Authorized user")
    st.divider()
    available_pages = []
    if has_permission("open_barcode_checking"): available_pages.append("Barcode Checking")
    if has_permission("upload_master_data"): available_pages.append("Master Upload")
    if has_permission("view_records"): available_pages.append("View / Manage Records")
    if has_permission("search_warranty"): available_pages.append("Warranty Search")
    if has_permission("export_excel") or has_permission("download_full_backup"): available_pages.append("Export / Backup")
    if has_permission("view_dashboard"): available_pages.append("Dashboard")
    if can_open_user_management(): available_pages.append("User Management")
    # Dashboard is intentionally not the default landing page.
    default_page = "Barcode Checking" if "Barcode Checking" in available_pages else (available_pages[0] if available_pages else None)
    if not available_pages:
        page = None
        st.info("No page access assigned. Please contact administrator.")
    else:
        if "active_page" not in st.session_state or st.session_state.active_page not in available_pages:
            st.session_state.active_page = default_page
        if st.session_state.get("portal_navigation") not in available_pages:
            st.session_state.pop("portal_navigation", None)
        page = st.radio("WORKSPACE", available_pages, index=available_pages.index(st.session_state.active_page), key="portal_navigation")
        st.session_state.active_page = page
    st.divider()
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.markdown(f"<div class='gcb-page-kicker'>{APP_SUBTITLE}</div>", unsafe_allow_html=True)
if page == "Dashboard":
    require_permission("view_dashboard")
    render_dashboard()
elif page == "Barcode Checking":
    require_permission("open_barcode_checking")
    render_barcode_checking()
elif page == "Master Upload":
    require_permission("upload_master_data")
    render_master_upload()
elif page == "View / Manage Records":
    require_permission("view_records")
    render_master_manage()
elif page == "Warranty Search":
    require_permission("search_warranty")
    render_warranty_search()
elif page == "Export / Backup":
    if not (has_permission("export_excel") or has_permission("download_full_backup")):
        raise PermissionError("Export / Backup access denied.")
    render_export_backup()
elif page == "User Management":
    if not can_open_user_management():
        raise PermissionError("User Management access denied.")
    render_user_management()

st.markdown("<div style='margin-top:2rem;padding-top:1rem;border-top:1px solid #e3e8f0;color:#94a3b8;font-size:.75rem'>Google-Code-Barcode · Secure warranty operations · Persistent data stored in Turso</div>", unsafe_allow_html=True)
