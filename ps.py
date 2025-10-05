import streamlit as st
from streamlit import rerun   # ✅ for rerun
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

AUTO_TOKENS = {"DATE", "SEQ"}
SPECIAL_TOKENS = {"ENV"}
ENV_OPTIONS = ["DEV", "TEST", "UAT", "STAGE", "PROD"]

ALLOWED_PATTERN = re.compile(r"^[A-Z0-9_]+$")
MAX_LENGTH = 128

# =================================
# Auth
# =================================
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
                    rerun()   # ✅ fixed
                else:
                    st.error("Invalid credentials")
        else:
            st.write(f"**User:** {st.session_state.auth['username']}")
            st.write(f"**Role:** `{st.session_state.auth['role']}`")
            if st.button("Sign out"):
                st.session_state.auth = {"logged_in": False, "username": None, "role": None}
                rerun()   # ✅ fixed

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
    used = {}
    name = template
    today = datetime.now().strftime("%Y%m%d")
    if "{DATE}" in name:
        used["DATE"] = today
        name = name.replace("{DATE}", today)
    if "{SEQ}" in name:
        key = f"{seq_scope}:{template}"
        next_seq = bump_sequence(key)
        used["SEQ"] = str(next_seq)
        name = name.replace("{SEQ}", str(next_seq))
    if "{ENV}" in name:
        val = token_values.get("ENV", "DEV")
        val = sanitize_token_value(val)
        used["ENV"] = val
        name = name.replace("{ENV}", val)
    tokens = set(re.findall(r"\{([A-Z0-9_]+)\}", template))
    for t in tokens:
        if t in {"DATE", "SEQ", "ENV"}: continue
        val = sanitize_token_value(token_values.get(t, ""))
        used[t] = val
        name = name.replace("{%s}" % t, val)
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

# =================================
# Init
# =================================
ensure_db()
TEMPLATES = load_templates()

# =================================
# Sidebar
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
    if IS_ADMIN and st.button("Reset SEQ for this template"):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM sequences WHERE key = ?", (f"GLOBAL:{template_str}",))
        conn.commit()
        conn.close()
        rerun()   # ✅ fixed

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
        token_values["ACTION"] = cols[1].text_input("ACTION")
    token_values["SYSTEM"] = cols[0].text_input("SYSTEM")
    token_values["CLIENT"] = cols[1].text_input("CLIENT")
    token_values["PROCESS"] = cols[2].text_input("PROCESS")

    extra_tokens = [t for t in tokens_in_template if t not in {"SYSTEM","CLIENT","PROCESS","ACTION","ENV","DATE","SEQ"}]
    for t in extra_tokens:
        token_values[t] = st.text_input(t)

    if st.button("Generate Name", type="primary"):
        name, used = format_with_tokens(template_str, token_values)
        ok, msg = validate_name(name)
        duplicate_df = fetch_history({"type": selected_type})
        duplicate_exists = not duplicate_df[duplicate_df["name"] == name].empty if not duplicate_df.empty else False

        if not ok:
            st.error(f"❌ Invalid name: {msg}")
        elif duplicate_exists:
            st.warning(f"⚠️ Already exists: {name}")
            st.code(name, language="text")
        else:
            st.success("✅ Generated")
            st.code(name, language="text")
            insert_history(name, selected_type, used, st.session_state.auth["username"])

# =================================
# Bulk Tab
# =================================
with tab_bulk:
    st.subheader("Bulk Name Generation")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded:
        df_in = pd.read_csv(uploaded)
        st.dataframe(df_in, width="stretch")
        results = []
        for _, row in df_in.iterrows():
            token_values_row = {k: str(row.get(k, "")) for k in df_in.columns}
            name, used = format_with_tokens(TEMPLATES[selected_type], token_values_row)
            ok, msg = validate_name(name)
            if ok:
                insert_history(name, selected_type, used, st.session_state.auth["username"])
                results.append({"GeneratedName": name, **used})
            else:
                results.append({"GeneratedName": f"ERROR: {msg}", **used})
        out_df = pd.DataFrame(results)
        st.dataframe(out_df, width="stretch")
        st.download_button("⬇️ Download CSV", data=out_df.to_csv(index=False), file_name="bulk_results.csv")

# =================================
# History Tab
# =================================
with tab_hist:
    st.subheader("History & Search")
    df = fetch_history({})
    st.dataframe(df, width="stretch", height=420)

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
        st.bar_chart(df_all["type"].value_counts())

# =================================
# Admin Tab
# =================================
with tab_admin:
    st.subheader("Template Management")
    if not IS_ADMIN:
        st.warning("Admins only")
    else:
        temp_df = pd.DataFrame([{"Type": k, "Template": v} for k,v in TEMPLATES.items()])
        st.dataframe(temp_df, width="stretch")
        new_type = st.text_input("New Type")
        new_template = st.text_input("New Template")
        if st.button("Save Template"):
            if new_type and new_template:
                TEMPLATES[new_type] = new_template
                save_templates(TEMPLATES)
                rerun()
