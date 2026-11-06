
def insert_master_records(conn, df):
    values = []
    for _, row in df.iterrows():
        values.append((
            str(row["Order Number"] or ""),
            str(row["Barcode"] or ""),
            pd.Timestamp(row["Creation Date"]).strftime("%Y-%m-%d %H:%M:%S"),
            pd.Timestamp(row["Resolve Date"]).strftime("%Y-%m-%d %H:%M:%S"),
            str(row["Remarks"] or ""),
        ))
    # No UNIQUE constraint: duplicate rows are intentionally accepted.
    conn.executemany(
        "INSERT INTO master_records (order_number, barcode, creation_date, resolve_date, remarks) VALUES (?, ?, ?, ?, ?)",
        values
    )


def normalize_master_for_check(rows):
    records = []
    for row in rows:
        records.append({
            "Order Number": str(row[1] or ""),
            "Barcode": str(row[2] or ""),
            "Creation Date": pd.to_datetime(row[3], errors="coerce"),
            "Resolve Date": pd.to_datetime(row[4], errors="coerce"),
            "Remarks": str(row[5] or ""),
        })
    return pd.DataFrame(records, columns=MASTER_COLUMNS)


# ---------------------------------------------------------
# PAGE NAVIGATION
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### Portal Menu")
    selected_page = st.radio(
        "Open Page",
        [
            "Barcode Checking Portal",
            "Master Upload",
            "Master Data",
            "Warranty Search",
            "Export / Backup",
        ],
        key="main_navigation"
    )


if selected_page == "Barcode Checking Portal":
    st.title("Barcode Checking Portal")
    st.write(
        "Excel file upload karein. Required columns: "
        "**Order Number, Barcode, Creation Date, Resolve Date, Remarks**."
    )

    with st.expander("Rules / Checking Logic"):
        st.markdown(
            """
            - Remarks ke ek cell mein multiple materials ho sakte hain; portal unhein detect karega.
            - Warranty results alag columns mein aayenge: Compressor, Gas, Parts aur Service.
            - Compressor Warranty: same barcode aur compressor repeat; warranty period 90 days.
            - Gas Warranty: same barcode aur gas repeat; warranty period 90 days.
            - Parts Warranty: same barcode aur same part repeat; warranty period 30 days. Repeat part 30 din se baad aaye to No Warranty Part.
            - Service Warranty: jab current remarks mein koi part/compressor/gas na ho; service history ka warranty period 30 days hai.
            - Gap current Creation Date minus previous matching complaint Resolve Date se calculate hota hai.
            - Order Number sirf output/tracking ke liye hai; calculation par asar nahi karta.
            """
        )

    uploaded_file = st.file_uploader(
        "Excel file upload karein",
        type=["xlsx", "xls"]
    )

    if uploaded_file is not None:
        try:
            # Read barcode/order identifiers as text to preserve leading zeros.
            input_df = pd.read_excel(
                uploaded_file,
                dtype={"Order Number": str, "Barcode": str}
            )
        except Exception as exc:
            st.error(f"Excel file read nahi ho saki: {exc}")
            st.stop()

        input_df.columns = [str(col).strip() for col in input_df.columns]
        required_columns = [
            "Order Number", "Barcode", "Creation Date", "Resolve Date", "Remarks"
        ]
        missing = [col for col in required_columns if col not in input_df.columns]

        if missing:
            st.error("Excel mein yeh required columns missing hain: " + ", ".join(missing))
            st.info("Column headings bilkul is tarah hon: Order Number, Barcode, Creation Date, Resolve Date, Remarks.")
            st.stop()

        # Keep the input schema fixed so the exported result remains exactly 13 columns.
        input_df = input_df[required_columns].copy()

        # Clean identifiers and dates.
        for col in ["Order Number", "Barcode", "Remarks"]:
            input_df[col] = input_df[col].fillna("").astype(str).str.strip()

        input_df["Creation Date"] = pd.to_datetime(
            input_df["Creation Date"], errors="coerce"
        )
        input_df["Resolve Date"] = pd.to_datetime(
            input_df["Resolve Date"], errors="coerce"
        )

        invalid_rows = (
            input_df["Barcode"].eq("")
            | input_df["Creation Date"].isna()
            | input_df["Resolve Date"].isna()
        )

        if invalid_rows.any():
            st.error(
                f"{int(invalid_rows.sum())} row(s) mein Barcode blank hai ya date ghalat/blank hai. "
                "Excel theek karke dobara upload karein."
            )
            st.dataframe(input_df.loc[invalid_rows], use_container_width=True)
            st.stop()

        if input_df.empty:
            st.warning("Excel file mein koi data nahi hai.")
            st.stop()

        if (input_df["Resolve Date"] < input_df["Creation Date"]).any():
            st.warning(
                "Kuch rows mein Resolve Date, Creation Date se pehle hai. "
                "Dates verify kar lein; checking phir bhi calculate hogi."
            )

        history = load_history()
        result_df = check_barcodes(input_df, history)

        st.subheader("Checking Results")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Rows", len(result_df))
        warranty_cols = [
            "Compressor Warranty", "Gas Warranty", "Parts Warranty", "Service Warranty"
        ]
        c2.metric(
            "Warranty Matches",
            int(result_df[warranty_cols].apply(
                lambda col: col.astype(str).str.startswith("Within ")
            ).sum().sum())
        )
        c3.metric(
            "No Warranty / First",
            int(result_df[warranty_cols].apply(
                lambda col: col.astype(str).str.contains("No Warranty|First ", case=False, regex=True)
            ).sum().sum())
        )

        st.dataframe(result_df, use_container_width=True, hide_index=True)

        excel_bytes = to_excel(result_df)
        st.download_button(
            label="Download Results Excel",
            data=excel_bytes,
            file_name="barcode_checking_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        if st.button("Save This File to Barcode History", type="primary"):
            save_history(input_df.to_dict(orient="records"))
            st.success("Is file ke barcode records history mein save ho gaye hain.")

    st.caption("Existing Barcode Checking ki local history aur naya Turso Master Data alag databases hain. Existing history ke liye regular backup rakhein.")


elif selected_page == "Master Upload":
    st.title("Master Upload")
    st.write("Historical complaint Excel upload karein. Duplicate records bhi save honge; koi automatic reject ya overwrite nahi hoga.")
    st.info("Required columns: Order Number, Barcode, Creation Date, Resolve Date, Remarks")
    master_file = st.file_uploader("Master Excel file", type=["xlsx", "xls"], key="master_upload_file")
    if master_file is not None:
        try:
            preview_df = parse_master_excel(master_file)
            st.success(f"File ready: {len(preview_df):,} records")
            st.dataframe(preview_df.head(100), use_container_width=True, hide_index=True)
            st.caption("Preview mein pehle 100 rows dikh rahi hain. Upload par tamam valid rows save hongi.")
            if st.button("Save to Master Database", type="primary", use_container_width=True):
                conn = get_master_connection()
                if conn is not None:
                    try:
                        insert_master_records(conn, preview_df)
                        st.success(f"{len(preview_df):,} records Master Data mein save ho gaye. Duplicate rows bhi preserve ki gayi hain.")
