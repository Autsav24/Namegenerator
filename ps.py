import streamlit as st
import pandas as pd
import json
from datetime import datetime

st.set_page_config(page_title="Advanced Naming Generator", page_icon="🧩", layout="wide")

# Load templates
if "templates" not in st.session_state:
    st.session_state.templates = {
        "Stored Procedure": "usp_Merge_ITF_{SYSTEM}_{CLIENT}_{PROCESS}",
        "Job": "Ingress_{SYSTEM}_{CLIENT}_{PROCESS}",
        "Pipeline": "PL_{SYSTEM}_{CLIENT}_{PROCESS}",
        "Function": "fn_{SYSTEM}_{CLIENT}_{PROCESS}",
        "Table": "tbl_{SYSTEM}_{CLIENT}_{PROCESS}",
        "View": "vw_{SYSTEM}_{CLIENT}_{PROCESS}"
    }

# History log
if "history" not in st.session_state:
    st.session_state.history = []

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["🔑 Generate", "📜 History", "📂 Bulk Upload", "⚙️ Admin Panel"])

# --- Generate Tab ---
with tab1:
    st.header("Generate a Name")
    naming_type = st.selectbox("Select Naming Type", list(st.session_state.templates.keys()))
    system = st.text_input("System Name")
    client = st.text_input("Client Name")
    process = st.text_input("Process Name")

    if st.button("Generate"):
        template = st.session_state.templates[naming_type]
        name = template.format(
            SYSTEM=system.upper().strip(),
            CLIENT=client.upper().strip(),
            PROCESS=process.upper().strip()
        )

        st.success("✅ Generated Name:")
        st.code(name, language="text")

        # Add to history
        st.session_state.history.append({
            "Name": name,
            "Type": naming_type,
            "System": system,
            "Client": client,
            "Process": process,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

# --- History Tab ---
with tab2:
    st.header("Generated History")
    df = pd.DataFrame(st.session_state.history)
    if not df.empty:
        st.dataframe(df, use_container_width=True)
        st.download_button("📥 Export to CSV", df.to_csv(index=False), "history.csv")
    else:
        st.info("No history yet.")

# --- Bulk Upload Tab ---
with tab3:
    st.header("Bulk Generate Names")
    uploaded = st.file_uploader("Upload CSV with columns: system, client, process")
    if uploaded:
        bulk_df = pd.read_csv(uploaded)
        bulk_df["GeneratedName"] = bulk_df.apply(
            lambda row: st.session_state.templates["Stored Procedure"].format(
                SYSTEM=row["system"].upper(),
                CLIENT=row["client"].upper(),
                PROCESS=row["process"].upper()
            ), axis=1
        )
        st.dataframe(bulk_df)
        st.download_button("📥 Export Bulk CSV", bulk_df.to_csv(index=False), "bulk_generated.csv")

# --- Admin Panel ---
with tab4:
    st.header("Manage Templates")
    st.write("Add or edit naming templates here.")

    new_type = st.text_input("New Template Type")
    new_template = st.text_input("New Template Format (use {SYSTEM}, {CLIENT}, {PROCESS})")

    if st.button("Add Template"):
        if new_type and new_template:
            st.session_state.templates[new_type] = new_template
            st.success(f"✅ Added new template: {new_type}")
