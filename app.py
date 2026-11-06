import streamlit as st
import pandas as pd
import datetime
import libsql_client as libsql

# 1. Turso Database Connection Setup
# Streamlit secrets (st.secrets) se credentials read honge taake code secure rahe
TURSO_URL = st.secrets.get("TURSO_URL", "sqlite:///:memory:")
TURSO_AUTH_TOKEN = st.secrets.get("TURSO_AUTH_TOKEN", "")

def get_db_client():
    if TURSO_URL.startswith("sqlite://"):
        return libsql.create_client_sync(url=TURSO_URL)
    return libsql.create_client_sync(url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)

# Database table initialization (Agar table nahi bani to ban jaye)
def init_db():
    client = get_db_client()
    client.execute("""
        CREATE TABLE IF NOT EXISTS master_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT,
            order_no TEXT,
            resolve_date TEXT,
            compressor_warranty TEXT,
            gas_warranty TEXT,
            parts_warranty TEXT,
            service_warranty TEXT,
            col9 TEXT, col10 TEXT, col11 TEXT, col12 TEXT, col13 TEXT
        )
    """)
    client.close()

init_db()

# 2. Main App Navigation
st.title("📦 Barcode Portal & Master Data Management")
page = st.sidebar.selectbox("Navigate Pages", ["Barcode Checking & Search", "Upload Master Data", "Manage Master Data (Bulk Actions)"])

# --- PAGE 1: BARCODE CHECKING & WARRANTY SEARCH ---
if page == "Barcode Checking & Search":
    st.header("🔍 Warranty Search (13 Columns Layout)")
    
    search_barcode = st.text_input("Enter Barcode to Search:")
    
    if search_barcode:
        client = get_db_client()
        # Turso se record fetch karna
        result = client.execute("SELECT * FROM master_data WHERE barcode = ?", [search_barcode])
        rows = result.rows
        client.close()
        
        if rows:
            # Data found, processing historical data
            order_numbers = [str(r[2]) for r in rows if r[2]]
            resolve_dates = [str(r[3]) for r in rows if r[3]]
            
            # Format rule: Comma separated values (e.g., 123, 456)
            prev_orders_str = ", ".join(order_numbers)
            prev_dates_str = ", ".join(resolve_dates)
            
            # Formulating the 13 columns final display row
            # Target Format: Order No, Previous Order No, Previous Resolve Date, and 4 Warranty status columns
            display_data = {
                "Barcode": [search_barcode],
                "Current Order No": [rows[-1][2]], # Latest Order
                "Previous Order Number": [prev_orders_str], # Raw format agreed (123, 456)
                "Previous Resolve Date": [prev_dates_str],   # Raw format agreed (02-10-2026)
                "Compressor Warranty": [rows[-1][4] if rows[-1][4] else "—"],
                "Gas Warranty": [rows[-1][5] if rows[-1][5] else "—"],
                "Parts Warranty": [rows[-1][6] if rows[-1][6] else "—"],
                "Service Warranty": [rows[-1][7] if rows[-1][7] else "—"],
                "Column 9": [rows[-1][8]], "Column 10": [rows[-1][9]], 
                "Column 11": [rows[-1][10]], "Column 12": [rows[-1][11]], "Column 13": [rows[-1][12]]
            }
            
            df_display = pd.DataFrame(display_data)
            st.success("Record Found!")
            st.dataframe(df_display)
        else:
            st.warning("No records found for this barcode in Turso Database.")

# --- PAGE 2: UPLOAD MASTER DATA ---
elif page == "Upload Master Data":
    st.header("📤 Bulk Upload Master Data (Excel/CSV)")
    st.info("Upload your central sheet here. It will safely store data inside your Turso Server (5GB Free space).")
    
    uploaded_file = st.file_uploader("Choose Excel or CSV file", type=["xlsx", "csv"])
    
    if uploaded_file:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
            
        st.write("Preview of Uploading Data:", df.head())
        
        if st.button("Confirm & Sync to Turso Cloud"):
            client = get_db_client()
            # Bulk insertion processing loop
            for _, row in df.iterrows():
                client.execute(
                    """INSERT INTO master_data 
                    (barcode, order_no, resolve_date, compressor_warranty, gas_warranty, parts_warranty, service_warranty, col9, col10, col11, col12, col13) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        str(row.get('Barcode', '')), str(row.get('Order No', '')), str(row.get('Resolve Date', '')),
                        str(row.get('Compressor Warranty', '—')), str(row.get('Gas Warranty', '—')),
                        str(row.get('Parts Warranty', '—')), str(row.get('Service Warranty', '—')),
                        str(row.get('Col9', '')), str(row.get('Col10', '')), str(row.get('Col11', '')), str(row.get('Col12', '')), str(row.get('Col13', ''))
                    ]
                )
            client.close()
            st.success(f"Successfully uploaded {len(df)} records to your permanent Turso database!")

# --- PAGE 3: MANAGE MASTER DATA (BULK SELECTION & DELETE) ---
elif page == "Manage Master Data (Bulk Actions)":
    st.header("⚙️ Manage Master Records & Duplicate Resolution")
    st.markdown("You can paste **'X'** or **'x'** in the **Select** column to perform bulk deletion actions on duplicates.")
    
    client = get_db_client()
    result = client.execute("SELECT id, barcode, order_no, resolve_date, compressor_warranty, gas_warranty FROM master_data LIMIT 500")
    rows = result.rows
    client.close()
    
    if rows:
        # Constructing editable grid simulation using Streamlit Data Editor
        data_list = []
        for r in rows:
            data_list.append({
                "Select": "", # Default Blank for Excel pasting zone
                "ID": r[0],
                "Barcode": r[1],
                "Order No": r[2],
                "Resolve Date": r[3],
                "Compressor": r[4],
                "Gas": r[5]
            })
            
        df_manage = pd.DataFrame(data_list)
        
        # Streamlit standard data_editor enables users to easily paste "X" into cells in bulk
        edited_df = st.data_editor(df_manage, column_config={"ID": st.column_config.NumberColumn(disabled=True)}, num_rows="dynamic")
        
        # Filtering records where user put 'X' or 'x'
        selected_rows = edited_df[edited_df['Select'].str.upper() == 'X']
        
        if not selected_rows.empty:
            st.warning(f"You have targeted **{len(selected_rows)}** records for deletion.")
            
            # Double safety confirmation step before permanent query strike
            confirm_delete = st.checkbox("I verify that I want to delete these duplicate records permanently.")
            
            if st.button("Delete Selected Rows"):
                if confirm_delete:
                    ids_to_delete = selected_rows['ID'].tolist()
                    client = get_db_client()
                    
                    # Batch deletion processing
                    for idx in ids_to_delete:
                        client.execute("DELETE FROM master_data WHERE id = ?", [idx])
                    client.close()
                    
                    st.success("Selected records have been cleared from Turso server successfully!")
                    st.rerun()
                else:
                    st.error("Please check the verification box first to execute permanent deletion.")
    else:
        st.info("Your Turso Master Data table is currently empty.")
