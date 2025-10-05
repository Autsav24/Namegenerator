import streamlit as st

# ========== CONFIG ==========
st.set_page_config(page_title="Naming Convention Generator", page_icon="🧩", layout="centered")

st.title("🧩 Naming Convention Generator")
st.markdown("Generate consistent technical names based on your organization’s standards.")

# ========== USER INPUT ==========
naming_type = st.selectbox(
    "Select Naming Type",
    [
        "Stored Procedure (usp_Merge_ITF_{SYSTEM}_{CLIENT}_{PROCESS})",
        "Job (Ingress_{SYSTEM}_{CLIENT}_{PROCESS})",
        "ADF Pipeline (PL_{SYSTEM}_{CLIENT}_{PROCESS})",
        "Function (fn_{SYSTEM}_{CLIENT}_{PROCESS})",
        "Table (tbl_{SYSTEM}_{CLIENT}_{PROCESS})",
        "View (vw_{SYSTEM}_{CLIENT}_{PROCESS})"
    ]
)

system = st.text_input("Enter System Name (e.g. CRM, HRMS, ERP)")
client = st.text_input("Enter Client Name (e.g. ABC, XYZ)")
process = st.text_input("Enter Process Name (e.g. Orders, Invoice, Extract)")

# ========== GENERATE NAME ==========
if st.button("Generate Name"):
    system = system.strip().upper()
    client = client.strip().upper()
    process = process.strip().upper()

    if not system or not client or not process:
        st.warning("⚠️ Please fill in all fields.")
    else:
        if naming_type.startswith("Stored Procedure"):
            name = f"usp_Merge_ITF_{system}_{client}_{process}"
        elif naming_type.startswith("Job"):
            name = f"Ingress_{system}_{client}_{process}"
        elif naming_type.startswith("ADF Pipeline"):
            name = f"PL_{system}_{client}_{process}"
        elif naming_type.startswith("Function"):
            name = f"fn_{system}_{client}_{process}"
        elif naming_type.startswith("Table"):
            name = f"tbl_{system}_{client}_{process}"
        elif naming_type.startswith("View"):
            name = f"vw_{system}_{client}_{process}"
        else:
            name = "Invalid type"

        st.success("✅ Generated Name:")
        st.code(name, language="text")

        # Copy to clipboard button
        st.markdown(
            f"""
            <button onclick="navigator.clipboard.writeText('{name}')"
            style="background-color:#4CAF50;color:white;padding:8px 16px;border:none;border-radius:6px;cursor:pointer;margin-top:10px;">
            📋 Copy to Clipboard
            </button>
            """ ,
            unsafe_allow_html=True
        )

# ========== FOOTER ==========
st.markdown("---")
st.caption("Developed by CodeToCashChronicles 🧠")