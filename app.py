import streamlit as st
import pandas as pd
import hmac
import re
from io import BytesIO

# ---------------------------------------------------------
# PAGE SETTINGS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Google-Code-Barcode",
    page_icon="🔎",
    layout="wide"
)


# ---------------------------------------------------------
# LOGIN SECURITY
# Set PORTAL_USERNAME and PORTAL_PASSWORD in Streamlit Cloud
# Settings -> Secrets. Never put your real password in this file.
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Google-Code-Barcode")
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
            st.error("Incorrect username or password.")

    st.stop()

# Keep login active during normal page interactions; logout is explicit.
with st.sidebar:
    st.success("Login successful")
    if st.button("Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()

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
# TURSO CLOUD MASTER DATABASE (HTTP API; no libsql native wheel)
# ---------------------------------------------------------
def turso_settings():
    try:
        raw_url = str(st.secrets.get("TURSO_DATABASE_URL", "")).strip()
        token = str(st.secrets.get("TURSO_AUTH_TOKEN", "")).strip()
    except Exception:
        raw_url, token = "", ""
    if not raw_url or not token:
        raise RuntimeError("Turso settings are missing. Please add TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in Streamlit Secrets.")
    url = raw_url.rstrip("/")
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    elif not url.startswith("https://"):
        raise RuntimeError("TURSO_DATABASE_URL must start with libsql:// or https://.")
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
    import requests
    endpoint, token = turso_settings()
    requests_list = []
    for sql, params in statements:
        requests_list.append({"type": "execute", "stmt": {
            "sql": sql,
            "args": [_hrana_value(v) for v in (params or [])]
        }})
    requests_list.append({"type": "close"})
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": requests_list},
        timeout=60
    )
    if not response.ok:
        raise RuntimeError(f"Turso HTTP error {response.status_code}: {response.text[:500]}")
    payload = response.json()
    outputs = []
    for item in payload.get("results", []):
        if item.get("type") == "error":
            err = item.get("error", {})
            raise RuntimeError(err.get("message", str(err)))
        result = item.get("response", {}).get("result", {})
        cols = [c.get("name", "") for c in result.get("cols", [])]
        rows = [[_decode_hrana(v) for v in row] for row in result.get("rows", [])]
        outputs.append({"columns": cols, "rows": rows, "affected": result.get("affected_row_count", 0)})
    return outputs


def ensure_master_table():
    turso_pipeline([("""CREATE TABLE IF NOT EXISTS master_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode TEXT NOT NULL,
        order_number TEXT NOT NULL DEFAULT '',
        creation_date TEXT NOT NULL,
        resolve_date TEXT NOT NULL,
        remarks TEXT NOT NULL DEFAULT '',
        uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""", [])])


def master_insert(df):
    ensure_master_table()
    sql = "INSERT INTO master_records (barcode, order_number, creation_date, resolve_date, remarks) VALUES (?, ?, ?, ?, ?)"
    rows = []
    for _, row in df.iterrows():
        rows.append((str(row['Barcode']), str(row['Order Number']),
                     pd.Timestamp(row['Creation Date']).strftime('%Y-%m-%d %H:%M:%S'),
                     pd.Timestamp(row['Resolve Date']).strftime('%Y-%m-%d %H:%M:%S'),
                     str(row['Remarks'])))
    # Small chunks keep HTTP request payloads manageable; duplicate rows are intentionally retained.
    for start in range(0, len(rows), 100):
        turso_pipeline([(sql, values) for values in rows[start:start+100]])


def master_fetch(barcodes=None):
    ensure_master_table()
    if barcodes:
        marks = ",".join(["?"] * len(barcodes))
        sql = f"SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records WHERE barcode IN ({marks}) ORDER BY barcode, creation_date, id"
        params = list(barcodes)
    else:
        sql = "SELECT id, barcode, order_number, creation_date, resolve_date, remarks, uploaded_at FROM master_records ORDER BY id DESC"
        params = []
    result = turso_pipeline([(sql, params)])[0]
    cols = result["columns"]
    return pd.DataFrame(result["rows"], columns=cols)


def master_delete(ids):
    ensure_master_table()
    for start in range(0, len(ids), 100):
        batch = ids[start:start+100]
        marks = ",".join(["?"] * len(batch))
        turso_pipeline([(f"DELETE FROM master_records WHERE id IN ({marks})", batch)])


def prepare_master_upload(file):
    df = pd.read_excel(file, dtype={"Order Number": str, "Barcode": str})
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Barcode", "Order Number", "Creation Date", "Resolve Date", "Remarks"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("Required columns are missing: " + ", ".join(missing))
    for col in ["Barcode", "Order Number", "Remarks"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["Creation Date"] = pd.to_datetime(df["Creation Date"], errors="coerce")
    df["Resolve Date"] = pd.to_datetime(df["Resolve Date"], errors="coerce")
    bad = df["Barcode"].eq("") | df["Creation Date"].isna() | df["Resolve Date"].isna()
    if bad.any():
        raise ValueError(f"{int(bad.sum())} rows have a blank Barcode or an invalid date.")
    return df[required]


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


def render_barcode_checking():
    st.title("Google-Code-Barcode")
    st.write("Upload an Excel file. Required columns: **Order Number, Barcode, Creation Date, Resolve Date, Remarks**.")
    with st.expander("Rules / Checking Logic"):
        st.markdown("""
        - Multiple materials can be listed in one Remarks cell; the portal will detect them.
        - Compressor Warranty: same barcode and repeated compressor; 30-day and 90-day thresholds.
        - Gas Warranty: same barcode and repeated gas; 30-day and 90-day thresholds.
        - Parts Warranty: same barcode and same part repeated; 30 days.
        - Service Warranty: service history warranty period; 30 days.
        - The gap is calculated from the current Creation Date to the previous matching complaint Resolve Date.
        """)
    uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"], key="check_upload")
    if uploaded_file is None: return
    try:
        input_df = pd.read_excel(uploaded_file, dtype={"Order Number": str, "Barcode": str})
        input_df.columns = [str(c).strip() for c in input_df.columns]
        required = ["Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks"]
        missing = [c for c in required if c not in input_df.columns]
        if missing:
            st.error("Required columns are missing: " + ", ".join(missing)); return
        for col in ["Order Number", "Barcode", "Remarks"]:
            input_df[col] = input_df[col].fillna("").astype(str).str.strip()
        for col in ["Creation Date", "Resolve Date"]:
            input_df[col] = pd.to_datetime(input_df[col], errors="coerce")
        bad = input_df["Barcode"].eq("") | input_df["Creation Date"].isna() | input_df["Resolve Date"].isna()
        if bad.any():
            st.error(f"{int(bad.sum())} rows have a blank Barcode or an invalid date.")
            st.dataframe(input_df.loc[bad], use_container_width=True); return
        if input_df.empty:
            st.warning("The Excel file contains no data."); return
        # Read-only reference: only records already saved in Turso Master Data.
        # The newly uploaded checking file is never inserted into any database.
        uploaded_barcodes = sorted(input_df["Barcode"].dropna().astype(str).str.strip().unique().tolist())
        master_df = master_fetch(uploaded_barcodes)
        history = master_to_history(master_df)
        result_df = check_barcodes(input_df, history)
        a, b, c = st.columns(3)
        a.metric("Total Rows", len(result_df))
        warranty_cols = ["Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty"]
        b.metric("Warranty Matches", int(result_df[warranty_cols].apply(lambda x: x.astype(str).str.startswith("Within ")).sum().sum()))
        c.metric("No Warranty / First", int(result_df[warranty_cols].apply(lambda x: x.astype(str).str.contains("No Warranty|First ", case=False, regex=True)).sum().sum()))
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        st.download_button("Download Results Excel", to_excel(result_df), "barcode_checking_results.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        st.info("This upload is for temporary checking only. Its records will not be saved to the database. Warranty references are taken only from Turso Master Data.")
    except Exception as exc:
        st.error(f"Checking error: {exc}")


def render_master_upload():
    st.header("Master Data — Upload")
    st.write("Uploaded Excel records will be appended to the cloud database. Duplicate rows will also be saved.")
    st.caption("Required columns: Barcode, Order Number, Creation Date, Resolve Date, Remarks")
    file = st.file_uploader("Upload Master Excel File", type=["xlsx", "xls"], key="master_upload")
    if file is not None:
        try:
            df = prepare_master_upload(file)
            st.info(f"{len(df):,} records are ready to upload.")
            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
            if st.button("Append Records to Cloud Master Data", type="primary"):
                with st.spinner("Saving records to the cloud database..."):
                    master_insert(df)
                st.success(f"{len(df):,} records successfully added to Master Data.")
        except Exception as exc:
            st.error(f"Upload error: {exc}")


def render_master_manage():
    st.header("Master Data — View / Manage")
    try:
        df = master_fetch()
        if df.empty:
            st.info("Master Data is currently empty."); return
        st.metric("Total Master Records", f"{len(df):,}")
        search = st.text_input("Search by Barcode or Order Number")
        view = df.copy()
        if search.strip():
            term = search.strip().lower()
            view = view[view["barcode"].astype(str).str.lower().str.contains(term, na=False) |
                        view["order_number"].astype(str).str.lower().str.contains(term, na=False)]
        st.caption("To delete records, tick the checkbox in the Select column.")
        editable = view[["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"]].copy()
        editable.insert(0, "Select", False)
        edited = st.data_editor(editable, use_container_width=True, hide_index=True,
                                disabled=["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"],
                                column_config={"Select": st.column_config.CheckboxColumn("Select"),
                                               "id": st.column_config.NumberColumn("Record ID")},
                                key="master_editor")
        selected = edited.loc[edited["Select"], "id"].astype(int).tolist()
        x, y = st.columns(2)
        if x.button("Select All Visible"):
            st.session_state["select_all_master"] = True
            st.rerun()
        if y.button("Clear Selection"):
            st.session_state.pop("select_all_master", None)
            st.session_state.pop("confirm_master_delete", None)
            st.rerun()
        if st.session_state.get("select_all_master"):
            selected = view["id"].astype(int).tolist()
        if selected:
            st.warning(f"{len(selected)} records selected.")
            if st.button("Delete Selected Records", type="primary"):
                st.session_state["confirm_master_delete"] = selected
            if st.session_state.get("confirm_master_delete"):
                st.error(f"Please confirm: {len(st.session_state['confirm_master_delete'])} records will be permanently deleted.")
                a, b = st.columns(2)
                if a.button("Yes, Permanently Delete"):
                    master_delete(st.session_state["confirm_master_delete"])
                    st.session_state.pop("confirm_master_delete", None)
                    st.session_state.pop("select_all_master", None)
                    st.success("Selected records have been deleted."); st.rerun()
                if b.button("Cancel Delete"):
                    st.session_state.pop("confirm_master_delete", None); st.rerun()
    except Exception as exc:
        st.error(f"Unable to load Master Data: {exc}")


def render_warranty_search():
    st.header("Warranty Search")
    st.write("Enter one or more barcodes. Search results are read-only and retrieved from Master Data.")
    raw = st.text_area("Barcode(s) — separate each barcode with a new line, comma, or semicolon", height=120)
    if st.button("Search Warranty", type="primary"):
        barcodes = list(dict.fromkeys([x.strip() for x in re.split(r"[\n,;]+", raw) if x.strip()]))
        if not barcodes:
            st.warning("Please enter at least one barcode."); return
        try:
            if len(barcodes) > 500:
                st.error("A maximum of 500 barcodes can be searched at a time."); return
            with st.spinner("Searching Master Data..."):
                records_df = master_fetch(barcodes)
            if records_df.empty:
                st.warning("No records were found for the entered barcodes."); return
            input_df = records_df.rename(columns={"barcode":"Barcode", "order_number":"Order Number",
                "creation_date":"Creation Date", "resolve_date":"Resolve Date", "remarks":"Remarks"})
            input_df["Creation Date"] = pd.to_datetime(input_df["Creation Date"], errors="coerce")
            input_df["Resolve Date"] = pd.to_datetime(input_df["Resolve Date"], errors="coerce")
            input_df = input_df[["Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks"]]
            result = check_barcodes(input_df, [])
            st.metric("Matched Master Records", len(result))
            st.dataframe(result, use_container_width=True, hide_index=True)
            st.download_button("Download Warranty Search Excel", to_excel(result), "warranty_search_results.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as exc:
            st.error(f"Warranty search error: {exc}")


def render_export_backup():
    st.header("Export / Backup")
    st.write("Download an Excel backup of your cloud Master Data.")
    if st.button("Load Master Data for Backup"):
        try:
            df = master_fetch()
            st.session_state["master_backup_df"] = df
        except Exception as exc:
            st.error(f"Unable to load backup data: {exc}")
    df = st.session_state.get("master_backup_df")
    if df is not None:
        st.metric("Backup Records", f"{len(df):,}")
        if not df.empty:
            backup = df.rename(columns={"id":"Record ID", "barcode":"Barcode", "order_number":"Order Number",
                "creation_date":"Creation Date", "resolve_date":"Resolve Date", "remarks":"Remarks", "uploaded_at":"Uploaded At"})
            st.download_button("Download Master Data Backup Excel", to_excel(backup), "Google-Code-Barcode_Master_Backup.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
        else:
            st.info("Master Data is empty; there are no records to include in the backup.")


# ---------------------------------------------------------
# MAIN PORTAL
# ---------------------------------------------------------
st.title("Google-Code-Barcode")
with st.sidebar:
    st.title("Google-Code-Barcode")
    st.caption("Cooler Complaint & Warranty Management")
    page = st.radio("Navigation", ["Barcode Checking", "Master Upload", "View / Manage Records", "Warranty Search", "Export / Backup"])

if page == "Barcode Checking":
    render_barcode_checking()
elif page == "Master Upload":
    render_master_upload()
elif page == "View / Manage Records":
    render_master_manage()
elif page == "Warranty Search":
    render_warranty_search()
elif page == "Export / Backup":
    render_export_backup()

st.caption("Google-Code-Barcode | Turso Master Data: permanent records | Barcode Checking uploads: temporary only")
