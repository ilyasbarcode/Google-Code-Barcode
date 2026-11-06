import streamlit as st
import pandas as pd
import datetime
import io
import libsql_client as libsql

# 1. Secure Database Setup
TURSO_URL = st.secrets.get("TURSO_URL", "https://turso.io")
TURSO_AUTH_TOKEN = st.secrets.get("TURSO_AUTH_TOKEN", "")

def get_db_client():
    if TURSO_URL.startswith("sqlite://"):
        return libsql.create_client_sync(url=TURSO_URL)
    return libsql.create_client_sync(url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)

# Clean Database Initialization (Strictly 5 Data Columns + ID)
def init_db():
    client = get_db_client()
    client.execute("""
        CREATE TABLE IF NOT EXISTS master_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT,
            order_no TEXT,
            creation_date TEXT,
            resolve_date TEXT,
            remarks TEXT
        )
    """)
    client.close()

init_db()

# Core Business Logic: Takes 5 columns input and returns the full 13-columns structure
def calculate_warranty_durations(df_input):
    df = df_input.copy()
    
    # Standardizing incoming data text
    df['Barcode'] = df['Barcode'].astype(str).str.strip()
    df['Remarks'] = df['Remarks'].astype(str).str.strip()
    
    # Preparing the 13-columns structural layout columns
    extended_cols = ["Previous Order Number", "Previous Resolve Date", "Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty", "Col11", "Col12", "Col13"]
    for col in extended_cols:
        df[col] = "—"
        
    # Process sequential lookback logic row by row
    for idx, row in df.iterrows():
        current_barcode = row['Barcode']
        current_material = row['Remarks']
        
        try:
            current_create_dt = pd.to_datetime(row['Creation Date'])
        except:
            continue
            
        # Scan behind inside current context for identical tracking footprints
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
                
                # Dynamic matching based on material category limits
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
                
    # Standardizing layout to final 13-columns sequence output order
    final_sequence = ["Barcode", "Order No", "Creation Date", "Resolve Date", "Remarks", "Previous Order Number", "Previous Resolve Date", "Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty", "Col11", "Col12", "Col13"]
    # Keep only available output fields gracefully
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

# --- 1. BARCODE CHECKING PORTAL (Process and download instantly) ---
if page == "Barcode Checking Portal":
    st.header("📋 Barcode Checking Portal")
    st.info("Upload an Excel file with these columns: Barcode, Order No, Creation Date, Resolve Date, Remarks.")
    
    file = st.file_uploader("Upload Excel File (.xlsx)", type=["xlsx"])
    if file:
        df_uploaded = pd.read_excel(file)
        # Rename flexibility helper to match inner casing rules smoothly
        df_uploaded.rename(columns={"Order No": "Order No"}, inplace=True)
        
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

# --- 2. MASTER DATA (Store purely 5 data columns on cloud database node) ---
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
                client = get_db_client()
                for _, row in df_master.iterrows():
                    client.execute(
                        """INSERT INTO master_data (barcode, order_no, creation_date, resolve_date, remarks) 
                        VALUES (?, ?, ?, ?, ?)""",
                        [
                            str(row.get('Barcode', '')), str(row.get('Order No', '')), 
                            str(row.get('Creation Date', '')), str(row.get('Resolve Date', '')), 
                            str(row.get('Remarks', ''))
                        ]
                    )
                client.close()
                st.success(f"Successfully saved {len(df_master)} records permanently to Turso cloud database!")
                
    with tabs[1]:
        st.subheader("Bulk Row Deletion Zone (Paste 'X' to remove rows)")
        client = get_db_client()
        res = client.execute("SELECT id, barcode, order_no, creation_date, resolve_date, remarks FROM master_data LIMIT 1000")
        db_rows = res.rows
        client.close()
        
        if db_rows:
            grid_data = [{"Select": "", "ID": r[0], "Barcode": r[1], "Order No": r[2], "Creation Date": r[3], "Resolve Date": r[4], "Remarks": r[5]} for r in db_rows]
            df_grid = pd.DataFrame(grid_data)
            
            edited_df = st.data_editor(df_grid, column_config={"ID": st.column_config.NumberColumn(disabled=True)}, num_rows="dynamic")
            selected_rows = edited_df[edited_df['Select'].str.upper() == 'X']
            
            if not selected_rows.empty:
                st.warning(f"Targeted rows for permanent deletion: {len(selected_rows)}")
                confirm = st.checkbox("Verify permanent action strike")
                if st.button("Execute Permanent Delete"):
                    if confirm:
                        client = get_db_client()
                        for idx in selected_rows['ID'].tolist():
                            client.execute("DELETE FROM master_data WHERE id = ?", [idx])
                        client.close()
                        st.success("Selected records cleared successfully!")
                        st.rerun()
        else:
            st.info("Database table is empty.")

# --- 3. WARRANTY SEARCH (Queries 5 columns from Turso, dynamic evaluation to 13 columns layout) ---
elif page == "Warranty Search":
    st.header("🔍 Direct Live Warranty Search")
    search_query = st.text_input("Enter Barcode:")
    
    if search_query:
        client = get_db_client()
        res = client.execute("SELECT barcode, order_no, creation_date, resolve_date, remarks FROM master_data WHERE barcode = ? ORDER BY id ASC", [search_query.strip()])
        rows = res.rows
        client.close()
        
        if rows:
            # Reconstruct raw records safely back into lookback dataframes
            cols = ["Barcode", "Order No", "Creation Date", "Resolve Date", "Remarks"]
            df_query = pd.DataFrame([list(r) for r in rows], columns=cols)
            
            # Fire lookback calculator engine logic dynamically
            processed_query_df = calculate_warranty_durations(df_query)
            st.success("Tracking footprints located inside database!")
            st.dataframe(processed_query_df)
        else:
            st.warning("No tracking history found inside central cloud server for this barcode.")

# --- 4. EXPORT / BACKUP (Extract pure 5 columns raw archive storage state) ---
elif page == "Export / Backup":
    st.header("💾 System Export & Offline Backup")
    st.markdown("Click below to download the pure raw database structure state as an Excel Backup sheet.")
    
