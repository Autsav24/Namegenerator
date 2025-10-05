import streamlit as st
import pandas as pd
import json
import re
import sqlite3
from datetime import datetime
from io import BytesIO

# =================================
# App Config & Branding
# =================================
st.set_page_config(page_title="Enterprise Naming Console", page_icon="🏢", layout="wide")
st.markdown(
    '''
    <style>
    .success-box {padding:10px 14px;border-radius:10px;background:#ecfdf5;border:1px solid #10b98133;}
    .warn-box {padding:10px 14px;border-radius:10px;background:#fffbeb;border:1px solid #f59e0b33;}
    .danger-box {padding:10px 14px;border-radius:10px;background:#fef2f2;border:1px solid #ef444433;}
    .pill {display:inline-block;padding:2px 8px;border-radius:999px;background:#f3f4f6;border:1px solid #e5e7eb;margin-right:6px;}
    </style>
    ''',
    unsafe_allow_html=True
)

APP_TITLE = "🏢 Enterprise Naming Console"
st.title(APP_TITLE)
st.caption("Standardize names for SPs, Jobs, Pipelines, Tables, Views — with policies, tokens, history, analytics, and bulk tools.")

# =================================
# Constants & Defaults
# =================================
TEMPLATES_FILE = "templates.json"
DB_FILE = "names_history.db"

DEFAULT_TEMPLATES = {
    "Stored Procedure": "usp_Merge_ITF_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Job (Ingress)": "Ingress_{SYSTEM}_{CLIENT}_{PROCESS}",
    "ADF Pipeline": "PL_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Function": "fn_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Table": "tbl_{SYSTEM}_{CLIENT}_{PROCESS}",
    "View": "vw_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Generic (with ENV/DATE/SEQ)": "obj_{SYSTEM}_{CLIENT}_{PROCESS}_{ENV}_{DATE}_{SEQ}"
}

# Tokens that are auto-handled (no user input required)
AUTO_TOKENS = {"DATE", "SEQ"}
# Tokens that have a known UI (dropdown or sensible defaults)
SPECIAL_TOKENS = {"ENV"}

ENV_OPTIONS = ["DEV", "TEST", "UAT", "STAGE", "PROD"]

ALLOWED_PATTERN = re.compile(r"^[A-Z0-9_]+$")
MAX_LENGTH = 128

# =================================
# Auth (Very Simple In-App)
# =================================
# NOTE: For real corp use, integrate with SSO/Azure AD. This is a simple demo.
USERS = {
    "admin": {"password": "admin@123", "role": "admin"},
    "user": {"password": "user@123", "role": "user"}
}

def login():
    with st.sidebar:
        st.subheader("🔐 Login")
        if "auth" not in st.session_state:
            st.session_state.auth = {"logged_in": False, "username": None, "role": None}

        if not st.session_state.auth["logged_in"]:
            u = st.text_input("Username", key="login_user")
            p = st.text_input("Password", type="password", key="login_pass")
            if st.button("Sign in"):
                if u in USERS and USERS[u]["password"] == p:
                    st.session_state.auth = {"logged_in": True, "username": u, "role": USERS[u]["role"]}
                    st.success(f"Welcome, {u}!")
                    st.experimental_rerun()
                else:
                    st.error("Invalid credentials")
        else:
            st.write(f"**User:** {st.session_state.auth['username']}")
            st.write(f"**Role:** `{st.session_state.auth['role']}`")
            if st.button("Sign out"):
                st.session_state.auth = {"logged_in": False, "username": None, "role": None}
                st.experimental_rerun()

login()
if not st.session_state.auth["logged_in"]:
    st.stop()

IS_ADMIN = st.session_state.auth["role"] == "admin"

# =================================
# Utilities
# =================================
def load_templates() -> dict:
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or not data:
                return DEFAULT_TEMPLATES.copy()
            return data
    except FileNotFoundError:
        return DEFAULT_TEMPLATES.copy()
    except Exception:
        return DEFAULT_TEMPLATES.copy()

def save_templates(templates: dict):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2)

def ensure_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            system TEXT,
            client TEXT,
            process TEXT,
            action TEXT,
            env TEXT,
            extra TEXT,
            user TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sequences (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def bump_sequence(key: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM sequences WHERE key = ?", (key,))
    row = c.fetchone()
    if row is None:
        val = 1
        c.execute("INSERT INTO sequences(key, value) VALUES(?, ?)", (key, val))
    else:
        val = row[0] + 1
        c.execute("UPDATE sequences SET value = ? WHERE key = ?", (val, key))
    conn.commit()
    conn.close()
    return val

def get_sequence(key: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM sequences WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def sanitize_token_value(v: str) -> str:
    v = (v or "").strip().upper()
    v = re.sub(r"\s+", "_", v)
    v = re.sub(r"[^A-Z0-9_]", "", v)
    return v[:MAX_LENGTH]

def validate_name(name: str) -> tuple[bool, str]:
    if len(name) > MAX_LENGTH:
        return False, f"Name exceeds max length {MAX_LENGTH}"
    if not ALLOWED_PATTERN.match(name):
        return False, "Only A–Z, 0–9, and _ are allowed"
    return True, ""

def format_with_tokens(template: str, token_values: dict, seq_scope: str = "GLOBAL") -> tuple[str, dict]:
    """
    Replaces tokens in template using token_values and auto tokens (DATE/SEQ).
    Returns (name, used_values)
    """
    used = {}
    name = template

    # Auto tokens
    today = datetime.now().strftime("%Y%m%d")
    if "{DATE}" in name:
        used["DATE"] = today
        name = name.replace("{DATE}", today)

    if "{SEQ}" in name:
        key = f"{seq_scope}:{template}"
        next_seq = bump_sequence(key)
        used["SEQ"] = str(next_seq)
        name = name.replace("{SEQ}", str(next_seq))

    # Special tokens
    if "{ENV}" in name:
        val = token_values.get("ENV", "DEV")
        val = sanitize_token_value(val)
        used["ENV"] = val
        name = name.replace("{ENV}", val)

    # User-provided tokens
    tokens = set(re.findall(r"\{([A-Z0-9_]+)\}", template))
    for t in tokens:
        if t in {"DATE", "SEQ", "ENV"}:
            continue
        val = sanitize_token_value(token_values.get(t, ""))
        used[t] = val
        name = name.replace("{%s}" % t, val)

    # Final cleanup
    name = re.sub(r"_+", "_", name).strip("_")
    return name, used

def insert_history(name: str, ntype: str, used: dict, username: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO history(name, type, system, client, process, action, env, extra, user, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            ntype,
            used.get("SYSTEM"),
            used.get("CLIENT"),
            used.get("PROCESS"),
            used.get("ACTION"),
            used.get("ENV"),
            json.dumps({k:v for k,v in used.items() if k not in {"SYSTEM","CLIENT","PROCESS","ACTION","ENV"}}),
            username,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()

def fetch_history(filters: dict | None = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    q = "SELECT id, name, type, system, client, process, action, env, user, created_at FROM history WHERE 1=1"
    params = []
    if filters:
        for col in ["type","system","client","process","action","env","user"]:
            if filters.get(col):
                q += f" AND {col} LIKE ?"
                params.append(f"%{filters[col]}%")
    q += " ORDER BY id DESC"
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df

def export_excel(df: pd.DataFrame) -> BytesIO:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="names")
    output.seek(0)
    return output

# Ensure persistence
ensure_db()
TEMPLATES = load_templates()

# =================================
# Sidebar: Template selection + Quick Actions
# =================================
with st.sidebar:
    st.header("⚙️ Controls")
    selected_type = st.selectbox("Template Type", list(TEMPLATES.keys()))
    template_str = TEMPLATES[selected_type]
    st.code(template_str, language="text")
    st.caption("Use tokens like {SYSTEM}, {CLIENT}, {PROCESS}, {ACTION}, {ENV}, {DATE}, {SEQ}")

    st.markdown("---")
    st.subheader("ℹ️ Sequence")
    st.write(f"Current SEQ for this template: **{get_sequence('GLOBAL:'+template_str)}**")
    if st.session_state.auth['role'] == 'admin' and st.button("Reset SEQ for this template"):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM sequences WHERE key = ?", (f"GLOBAL:{template_str}",))
        conn.commit()
        conn.close()
        st.experimental_rerun()

# =================================
# Tabs
# =================================
tab_gen, tab_bulk, tab_hist, tab_analytics, tab_admin = st.tabs([
    "🔑 Generate",
    "📦 Bulk",
    "📜 History",
    "📊 Analytics",
    "🛠️ Admin"
])

# =================================
# Generate Tab
# =================================
with tab_gen:
    st.subheader("Generate a Name")
    tokens_in_template = set(re.findall(r"\{([A-Z0-9_]+)\}", template_str))

    cols = st.columns(3)
    token_values = {}

    if "ENV" in tokens_in_template:
        token_values["ENV"] = cols[0].selectbox("ENV", ENV_OPTIONS, index=0)
    if "ACTION" in tokens_in_template:
        token_values["ACTION"] = cols[1].text_input("ACTION (e.g., Merge, Load, Extract)")

    token_values["SYSTEM"] = cols[0].text_input("SYSTEM (e.g., CRM, HRMS, ERP)")
    token_values["CLIENT"] = cols[1].text_input("CLIENT (e.g., ABC, XYZ)")
    token_values["PROCESS"] = cols[2].text_input("PROCESS (e.g., ORDERS, INVOICE)")

    known = {"SYSTEM","CLIENT","PROCESS","ACTION","ENV","DATE","SEQ"}
    extra_tokens = sorted([t for t in tokens_in_template if t not in known])
    if extra_tokens:
        st.markdown("**Custom Tokens**")
        ec = st.columns(min(3, len(extra_tokens)) or 1)
        for i, t in enumerate(extra_tokens):
            token_values[t] = ec[i % len(ec)].text_input(f"{t}")

    st.markdown("---")
    colA, colB = st.columns([1,2])
    if colA.button("Generate Name", type="primary"):
        name, used = format_with_tokens(template_str, token_values, seq_scope="GLOBAL")
        ok, msg = validate_name(name)
        duplicate_df = fetch_history({"type": selected_type})
        duplicate_exists = not duplicate_df[duplicate_df["name"] == name].empty if not duplicate_df.empty else False

        if not ok:
            st.error(f"❌ Invalid name: {msg}")
        elif duplicate_exists:
            st.warning(f"⚠️ Name already exists in history: `{name}`")
            st.code(name, language="text")
        else:
            st.success("✅ Generated Name")
            st.code(name, language="text")
            insert_history(name, selected_type, used, st.session_state.auth["username"])

            st.download_button("📄 Copy as TXT", data=name, file_name=f"{name}.txt")

    preview_name, _ = format_with_tokens(template_str, token_values, seq_scope="PREVIEW_ONLY")
    ok_prev, msg_prev = validate_name(preview_name)
    with colB:
        st.markdown("**Live Preview**")
        if ok_prev:
            st.code(preview_name, language="text")
        else:
            st.markdown(f"<div class='danger-box'>Preview invalid: {msg_prev}</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.caption("Rules: Uppercase only, digits, and underscores. Max length 128. DATE=YYYYMMDD, SEQ auto-increments per template.")

# =================================
# Bulk Tab
# =================================
with tab_bulk:
    st.subheader("Bulk Name Generation")
    st.write("Upload CSV with columns matching the tokens in your template (e.g., SYSTEM, CLIENT, PROCESS, ACTION, ENV, plus any custom tokens). DATE and SEQ are auto-filled.")

    sample = pd.DataFrame([
        {"SYSTEM": "CRM", "CLIENT": "ABC", "PROCESS": "ORDERS", "ACTION": "Merge", "ENV": "DEV"}
    ])
    st.download_button("📥 Download Sample CSV", data=sample.to_csv(index=False), file_name="sample_bulk.csv")

    up = st.file_uploader("Upload CSV", type=["csv"])
    if up is not None:
        try:
            df_in = pd.read_csv(up)
            st.dataframe(df_in, use_container_width=True)
            results = []
            for _, row in df_in.iterrows():
                token_values_row = {k: str(row.get(k, "")) for k in df_in.columns}
                name, used = format_with_tokens(TEMPLATES[selected_type], token_values_row, seq_scope="GLOBAL")
                ok, msg = validate_name(name)
                if ok:
                    insert_history(name, selected_type, used, st.session_state.auth["username"])
                    results.append({"GeneratedName": name, **used})
                else:
                    results.append({"GeneratedName": f"ERROR: {msg}", **used})
            out_df = pd.DataFrame(results)
            st.success("Bulk generation completed")
            st.dataframe(out_df, use_container_width=True)
            st.download_button("⬇️ Download Results CSV", data=out_df.to_csv(index=False), file_name="bulk_results.csv")
            xls = export_excel(out_df)
            st.download_button("⬇️ Download Results Excel", data=xls, file_name="bulk_results.xlsx")
        except Exception as e:
            st.error(f"Failed to process CSV: {e}")

# =================================
# History Tab
# =================================
with tab_hist:
    st.subheader("History & Search")

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    f = {
        "type": c1.text_input("Filter Type"),
        "system": c2.text_input("Filter SYSTEM"),
        "client": c3.text_input("Filter CLIENT"),
        "process": c4.text_input("Filter PROCESS"),
        "action": c5.text_input("Filter ACTION"),
        "env": c6.text_input("Filter ENV"),
        "user": c7.text_input("Filter USER"),
    }

    df = fetch_history(f)
    st.dataframe(df, use_container_width=True, height=420)

    if not df.empty:
        st.download_button("⬇️ Export CSV", data=df.to_csv(index=False), file_name="history.csv")
        xls = export_excel(df)
        st.download_button("⬇️ Export Excel", data=xls, file_name="history.xlsx")
        st.download_button("⬇️ Export JSON", data=df.to_json(orient="records", indent=2), file_name="history.json")

# =================================
# Analytics Tab
# =================================
with tab_analytics:
    st.subheader("Usage Analytics")
    df_all = fetch_history({})
    if df_all.empty:
        st.info("No data yet.")
    else:
        st.metric("Total Names", len(df_all))
        by_type = df_all["type"].value_counts().reset_index()
        by_type.columns = ["type", "count"]
        st.bar_chart(by_type.set_index("type"))

        by_env = df_all["env"].fillna("N/A").value_counts().reset_index()
        by_env.columns = ["env", "count"]
        st.bar_chart(by_env.set_index("env"))

        df_all["date"] = pd.to_datetime(df_all["created_at"]).dt.date
        by_day = df_all.groupby("date")["id"].count().reset_index().rename(columns={"id":"count"})
        st.line_chart(by_day.set_index("date"))

        top_systems = df_all["system"].dropna().value_counts().head(10)
        st.write("**Top Systems**")
        st.dataframe(top_systems.to_frame("count"))

# =================================
# Admin Tab
# =================================
with tab_admin:
    st.subheader("Template Management")
    if not IS_ADMIN:
        st.warning("Admin access only.")
    else:
        st.markdown("Configure naming templates. You can include tokens like `{SYSTEM}`, `{CLIENT}`, `{PROCESS}`, `{ACTION}`, `{ENV}`, `{DATE}`, `{SEQ}` and any custom tokens like `{COUNTRY}`.")
        st.markdown("**Current Templates**")
        temp_df = pd.DataFrame([{"Type": k, "Template": v} for k, v in TEMPLATES.items()])
        st.dataframe(temp_df, use_container_width=True)

        st.markdown("---")
        st.write("### Add / Update Template")
        col1, col2 = st.columns([1,2])
        new_type = col1.text_input("Template Type (label)", value="")
        new_template = col2.text_input("Template String", value="")
        if st.button("Save Template"):
            if new_type and new_template and "{" in new_template and "}" in new_template:
                TEMPLATES[new_type] = new_template
                save_templates(TEMPLATES)
                st.success(f"Saved template: {new_type}")
                st.experimental_rerun()
            else:
                st.error("Provide a valid template with at least one {TOKEN}.")

        st.markdown("---")
        st.write("### Delete Template")
        del_type = st.selectbox("Select template to delete", ["--"] + list(TEMPLATES.keys()))
        if st.button("Delete Selected Template"):
            if del_type != "--":
                TEMPLATES.pop(del_type, None)
                save_templates(TEMPLATES)
                st.success(f"Deleted template: {del_type}")
                st.experimental_rerun()

st.markdown("---")
st.caption("© Enterprise Naming Console — Streamlit-only implementation. DATE=YYYYMMDD, SEQ per-template. For secure auth, integrate SSO in production.")
