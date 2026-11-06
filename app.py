            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
            if st.button("Append Records to Cloud Master Data", type="primary"):
                with st.spinner("Records cloud database mein save ho rahe hain..."):
                    master_insert(df)
                st.success(f"{len(df):,} records successfully append ho gaye.")
        except Exception as exc:
            st.error(f"Upload error: {exc}")


def render_master_manage():
    st.header("Master Data — View / Manage")
    try:
        df = master_fetch()
        if df.empty:
            st.info("Master Data abhi empty hai."); return
        st.metric("Total Master Records", f"{len(df):,}")
        search = st.text_input("Barcode ya Order Number se search karein")
        view = df.copy()
        if search.strip():
            term = search.strip().lower()
            view = view[view["barcode"].astype(str).str.lower().str.contains(term, na=False) |
                        view["order_number"].astype(str).str.lower().str.contains(term, na=False)]
        st.caption("Delete ke liye Select column mein checkbox tick karein.")
        editable = view[["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"]].copy()
        editable.insert(0, "Select", False)
        edited = st.data_editor(editable, use_container_width=True, hide_index=True,
                                disabled=["id", "barcode", "order_number", "creation_date", "resolve_date", "remarks"],
                                column_config={"Select": st.column_config.CheckboxColumn("Select"),
                                               "id": st.column_config.NumberColumn("Record ID")},
                                key="master_editor")
        selected = edited.loc[edited["Select"], "id"].astype(int).tolist()
        x,y = st.columns(2)
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
                st.error(f"Confirm karein: {len(st.session_state['confirm_master_delete'])} records permanently delete honge.")
                a,b = st.columns(2)
                if a.button("Yes, permanently delete"):
                    master_delete(st.session_state["confirm_master_delete"])
                    st.session_state.pop("confirm_master_delete", None)
                    st.session_state.pop("select_all_master", None)
                    st.success("Selected records delete ho gaye."); st.rerun()
                if b.button("Cancel Delete"):
                    st.session_state.pop("confirm_master_delete", None); st.rerun()
    except Exception as exc:
        st.error(f"Master Data load nahi ho saka: {exc}")


def render_warranty_search():
    st.header("Warranty Search")
    st.write("Ek ya multiple barcodes enter karein. Search Master Data se hogi; yeh page read-only hai.")
    raw = st.text_area("Barcode(s) — har line, comma ya semicolon se separate karein", height=120)
    if st.button("Search Warranty", type="primary"):
        barcodes = list(dict.fromkeys([x.strip() for x in re.split(r"[\n,;]+", raw) if x.strip()]))
        if not barcodes:
            st.warning("Kam az kam ek barcode enter karein."); return
        try:
            if len(barcodes) > 500:
                st.error("Ek search mein maximum 500 barcodes enter karein."); return
            with st.spinner("Master Data search ho rahi hai..."):
                records_df = master_fetch(barcodes)
            if records_df.empty:
                st.warning("In barcodes ka koi record nahi mila."); return
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
    st.write("Cloud Master Data ka Excel backup download karein.")
    if st.button("Load Master Data for Backup"):
        try:
            df = master_fetch()
            st.session_state["master_backup_df"] = df
        except Exception as exc:
            st.error(f"Backup data load nahi ho saka: {exc}")
    df = st.session_state.get("master_backup_df")
    if df is not None:
        st.metric("Backup Records", f"{len(df):,}")
        if not df.empty:
            backup = df.rename(columns={"id":"Record ID", "barcode":"Barcode", "order_number":"Order Number",
                "creation_date":"Creation Date", "resolve_date":"Resolve Date", "remarks":"Remarks", "uploaded_at":"Uploaded At"})
            st.download_button("Download Master Data Backup Excel", to_excel(backup), "Google-Code-Barcode_Master_Backup.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
        else:
            st.info("Master Data empty hai; backup mein records nahi hain.")


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

st.caption("Google-Code-Barcode | Master Data cloud database: Turso | Checking history: local SQLite")
