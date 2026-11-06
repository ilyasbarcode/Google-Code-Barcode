import streamlit as st
import pandas as pd
import datetime
import io
import requests

# 1. Secure Database Setup via Direct HTTP API
TURSO_URL = st.secrets.get("TURSO_URL", "https://turso.io")
TURSO_AUTH_TOKEN = st.secrets.get("TURSO_AUTH_TOKEN", "")

# Standardized direct API caller function
def query_turso(statements):
    url = TURSO_URL
    if url.startswith("libsql://"):
        url = url.replace("libsql://", "https://")
    
    endpoint = f"{url.rstrip('/')}/v2/pipeline"
    headers = {
        "Authorization": f"Bearer {TURSO_AUTH_TOKEN}",
        "Content-Type": "application/json"
    }
    
    requests_payload = []
    for item in statements:
        sql = item[0]
        params = item[1] if len(item) > 1 else []
        
        formatted_args = []
        for p in params:
            if isinstance(p, int):
                formatted_args.append({"type": "integer", "value": str(p)})
            else:
                formatted_args.append({"type": "text", "value": str(p)})
                
        requests_payload.append({
            "type": "execute",
            "stmt": {
                "sql": sql,
                "args": formatted_args
            }
        })
        
    payload = {"requests": requests_payload}
    
    try:
        response = requests.post(endpoint, json=payload, headers=headers)
        if response.status_code == 200:
            res_json = response.json()
            results = res_json.get("results", [])
            output = []
            for r in results:
                if r.get("type") == "execute":
                    exec_res = r.get("response", {}).get("result", {})
                    cols = [c.get("name") for c in exec_res.get("cols", [])]
                    rows_data = exec_res.get("rows", [])
                    
                    parsed_rows = []
                    for row in rows_data:
                        parsed_row = []
                        for cell in row:
                            parsed_row.append(cell.get("value"))
                        parsed_rows.append(dict(zip(cols, parsed_row)))
                    output.append(parsed_rows)
                else:
                    output.append([])
            return output
        else:
            return [[] for _ in statements]
    except:
        return [[] for _ in statements]

def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS master_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode TEXT,
        order_no TEXT,
        creation_date TEXT,
        resolve_date TEXT,
        remarks TEXT
    )
    """
    query_turso([[sql]])

init_db()

# Core Business Logic: Takes 5 columns input and returns the full 13-columns structure
def calculate_warranty_durations(df_input):
    df = df_input.copy()
    
    df['Barcode'] = df['Barcode'].astype(str).str.strip()
    df['Remarks'] = df['Remarks'].astype(str).str.strip()
    
    extended_cols = ["Previous Order Number", "Previous Resolve Date", "Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty", "Col11", "Col12", "Col13"]
    for col in extended_cols:
        df[col] = "—"
        
    for idx, row in df.iterrows():
        current_barcode = row['Barcode']
        current_material = row['Remarks']
        
        try:
            current_create_dt = pd.to_datetime(row['Creation Date'])
        except:
            continue
            
        past_records = df.iloc[:idx]
        match_condition = (past_records['Barcode'] == current_barcode) & (past_records['Remarks'] == current_material)
        matched_history = past_records[match_condition]
        
        if not matched_history.empty:
            orders_list = matched_history['Order No'].astype(str).tolist()
            dates_list = matched_history['Resolve Date'].astype(str).tolist()
            
            df.at[idx, "Previous Order Number"] = ", ".join(orders_list)
            df.at[idx, "Previous Resolve Date"] = ", ".join(dates_list)
            
            last_resolve_raw = dates_list[-1]
            try:
                last_resolve_dt = pd.to_datetime(last_resolve_raw)
                days_diff = (current_create_dt - last_resolve_dt).days
                
                if current_material.lower() in ['compressor', 'gas']:
                    if days_diff <= 90:
                        target_col = "Compressor Warranty" if 'compressor' in current_material.lower() else "Gas Warranty"
                        df.at[idx, target_col] = "Within 90 Days"
                else:
                    if days_diff <= 30:
                        target_col = "Parts Warranty" if 'part' in current_material.lower() else "Service Warranty"
                        df.at[idx, target_col] = "Within 30 Days"
            except:
                pass
                
    final_sequence = ["Barcode", "Order No", "Creation Date", "Resolve Date", "Remarks", "Previous Order Number", "Previous Resolve Date", "Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty", "Col11", "Col12", "Col13"]
    existing_cols = [c for c in final_sequence if c in df.columns]
    return df[existing_cols]

def to_excel_buffer(dataframe):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        dataframe.to_excel(writer, index=False, sheet_name='Warranty Report')
    return output.getvalue()

# --- Main Layout Architecture Structure ---
st.set_page_config(page_title="Barcode Portal", layout="wide")
st.sidebar.title("📌 Portal Menu")
page = st.sidebar.radio("Navigate", ["Barcode Checking Portal", "Master Data", "Warranty Search", "Export / Backup"])

# --- 1. BARCODE CHECKING PORTAL ---
if page == "Barcode Checking Portal":
    st.header("📋 Barcode Checking Portal")
    st.info("Upload an Excel file with these columns: Barcode, Order No, Creation Date, Resolve Date, Remarks.")
    
    file = st.file_uploader("Upload Excel File (.xlsx)", type=["xlsx"])
    if file:
        df_uploaded = pd.read_excel(file)
        if st.button("Process & Generate Sheet"):
            with st.spinner("Executing calculations..."):
                processed_df = calculate_warranty_durations(df_uploaded)
                st.success("Calculations completed successfully!")
                st.dataframe(processed_df)
                
                excel_data = to_excel_buffer(processed_df)
                st.download_button(
                    label="📥 Download 13-Columns Calculated Excel File",
                    data=excel_data,
                    file_name=f"Processed_Warranty_Report_{datetime.date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

# --- 2. MASTER DATA ---
elif page == "Master Data":
    st.header("🗄️ Central Master Data Repository")
    tabs = st.tabs(["📤 Upload New Master Records", "⚙️ Manage & Clear Duplicates"])
    
    with tabs[0]:
        st.subheader("Upload 5-Columns Sheet permanently to Turso Cloud")
        master_file = st.file_uploader("Choose Master Excel Sheet", type=["xlsx"], key="master_upload")
        if master_file:
            df_master = pd.read_excel(master_file)
            st.write("Preview:", df_master.head(3))
            if st.button("Sync & Save to Turso Cloud Server"):
                batch_inserts = []
                for _, row in df_master.iterrows():
                    sql = "INSERT INTO master_data (barcode, order_no, creation_date, resolve_date, remarks) VALUES (?, ?, ?, ?, ?)"
                    params = [str(row.get('Barcode', '')), str(row.get('Order No', '')), str(row.get('Creation Date', '')), str(row.get('Resolve Date', '')), str(row.get('Remarks', ''))]
                    batch_inserts.append([sql, params])
                
                if batch_inserts:
                    query_turso(batch_inserts)
                st.success(f"Successfully saved {len(df_master)} records permanently to Turso cloud database!")
                
    with tabs[1]:
        st.subheader("Bulk Row Deletion Zone (Paste 'X' to remove rows)")
        res = query_turso([["SELECT id, barcode, order_no, creation_date, resolve_date, remarks FROM master_data LIMIT 1000"]])
        db_rows = res[0] if res else []
        
        if db_rows:
            grid_data = []
            for r in db_rows:
                grid_data.append({
                    "Select": "", 
                    "ID": r.get("id"), 
                    "Barcode": r.get("barcode"), 
                    "Order No": r.get("order_no"), 
                    "Creation Date": r.get("creation_date"), 
                    "Resolve Date": r.get("resolve_date"), 
                    "Remarks": r.get("remarks")
                })
                    
            df_grid = pd.DataFrame(grid_data)
            edited_df = st.data_editor(df_grid, column_config={"ID": st.column_config.NumberColumn(disabled=True)}, num_rows="dynamic")
            selected_rows = edited_df[edited_df['Select'].str.upper() == 'X']
            
            if not selected_rows.empty:
                st.warning(f"Targeted rows for permanent deletion: {len(selected_rows)}")
                confirm = st.checkbox("Verify permanent action strike")
                if st.button("Execute Permanent Delete"):
                    if confirm:
                        batch_deletes = []
                        for idx in selected_rows['ID'].tolist():
                            batch_deletes.append(["DELETE FROM master_data WHERE id = ?", [int(idx)]])
                        query_turso(batch_deletes)
                        st.success("Selected records cleared successfully!")
                        st.rerun()
        else:
            st.info("Database table is empty.")

# --- 3. WARRANTY SEARCH ---
elif page == "Warranty Search":
    st.header("🔍 Direct Live Warranty Search")
    search_query = st.text_input("Enter Barcode:")
    
    if search_query:
