import streamlit as st
from streamlit import rerun
import pandas as pd
import json
import re
import sqlite3
from datetime import datetime
from io import BytesIO

# =================================
# App Config
# =================================
st.set_page_config(page_title="Enterprise Naming Console", page_icon="🏢", layout="wide")

APP_TITLE = "🏢 Enterprise Naming Console"
st.title(APP_TITLE)
st.caption("Standardize names for SPs, Jobs, Pipelines, Tables, Views — with policies, tokens, history, analytics, and bulk tools.")

# =================================
# Constants
# =================================
TEMPLATES_FILE = "templates.json"
DB_FILE = "names_history.db"
MAX_LENGTH = 128

DEFAULT_TEMPLATES = {
    "Stored Procedure": "usp_Merge_ITF_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Job (Ingress)": "Ingress_{SYSTEM}_{CLIENT}_{PROCESS}",
    "ADF Pipeline": "PL_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Function": "fn_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Table": "tbl_{SYSTEM}_{CLIENT}_{PROCESS}",
    "View": "vw_{SYSTEM}_{CLIENT}_{PROCESS}",
    "Generic (with ENV/DATE/SEQ)": "obj_{SYSTEM}_{CLIENT}_{PROCESS}_{ENV}_{DATE}_{SEQ}"
}

ENV_OPTIONS = ["DEV", "TEST", "UAT", "STAGE", "PROD"]

# Reserved SQL keywords
RESERVED_WORDS = {
    "SELECT","TABLE","VIEW","INDEX","INSERT","UPDATE",
    "DELETE","CREATE","DROP","ALTER","PROCEDURE","FUNCTION"
}

# =================================
# Auth
# =================================
USERS = {
    "admin": {"password": "admin@123", "role": "admin"},
    "user": {"password": None, "role": "user"}  # no password needed
}

def login():
    with st.sidebar:
        st.subheader("🔐 Login")

        if "auth" not in st.session_state:
            st.session_state.auth = {"logged_in": False, "username": None, "role": None}

        if not st.session_state.auth["logged_in"]:
            if st.button("👤 Login as User"):
                st.session_state.auth = {"logged_in": True, "username": "user", "role": "user"}
                st.success("Welcome, User!")
                rerun()

            st.markdown("---")
            st.write("**Admin Login**")
            u = st.text_input("Username", key="login_user")
            p = st.text_input("Password", type="password", key="login_pass")
            if st.button("🔑 Login as Admin"):
                if u in USERS and USERS[u]["password"] == p:
                    st.session_state.auth = {"logged_in": True, "username": u, "role": USERS[u]["role"]}
                    st.success(f"Welcome, {u}!")
                    rerun()
                else:
                    st.error("Invalid admin credentials")
        else:
            st.write(f"**User:** {st.session_state.auth['username']}")
            st.write(f"**Role:** `{st.session_state.auth['role']}`")
            if st.button("Sign out"):
                st.session_state.auth = {"logged_in": False, "username": None, "role": None}
                rerun()

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
            return json.load(f)
    except FileNotFoundError:
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
            user TEXT,
            created_at TEXT
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

def sanitize_token_value(v: str) -> str:
    v = (v or "").strip()
    v = re.sub(r"[^A-Za-z0-9]+", " ", v)
    parts = v.split()
    pascal = "".join(word.capitalize() for word in parts)
    return pascal[:MAX_LENGTH]

def validate_name(name: str) -> tuple[bool, str]:
    if len(name) > MAX_LENGTH:
        return False, f"Name exceeds max length {MAX_LENGTH}"
    if name.upper() in RESERVED_WORDS:
        return False, f"Name '{name}' is a reserved SQL keyword"
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
        val = sanitize_token_value(token_values.get("ENV", "DEV"))
        used["ENV"] = val
        name = name.replace("{ENV}", val)
    tokens = set(re.findall(r"\{([A-Z0-9_]+)\}", template))
    for t in tokens:
        if t in {"DATE", "SEQ", "ENV"}: continue
        val = sanitize_token_value(token_values.get(t, ""))
        used[t] = val
        name = name.replace("{%s}" % t, val)
    return name, used

def insert_history(name: str, ntype: str, used: dict, username: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO history(name, type, system, client, process, action, env, user, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            ntype,
            used.get("SYSTEM"),
            used.get("CLIENT"),
            used.get("PROCESS"),
            used.get("ACTION"),
            used.get("ENV"),
            username,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()

def fetch_history() -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM history ORDER BY id DESC", conn)
    conn.close()
    return df

# =================================
# Init
# =================================
ensure_db()
TEMPLATES = load_templates()
if "favorites" not in st.session_state:
    st.session_state["favorites"] = []

# =================================
# Sidebar
# =================================
with st.sidebar:
    st.header("⚙️ Controls")
    selected_type = st.selectbox("Template Type", list(TEMPLATES.keys()))
    template_str = TEMPLATES[selected_type]
    st.code(template_str, language="text")

# =================================
# Tabs
# =================================
tab_gen, tab_bulk, tab_hist, tab_admin, tab_fav = st.tabs([
    "🔑 Generate",
    "📦 Bulk",
    "📜 History",
    "🛠️ Admin",
    "⭐ Favorites"
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

    if st.button("Generate Name", type="primary"):
        name, used = format_with_tokens(template_str, token_values)
        ok, msg = validate_name(name)
        if not ok:
            st.error(f"❌ {msg}")
        else:
            st.success("✅ Generated")
            st.code(name, language="text")

            # Copy to clipboard
            st.download_button("📋 Copy to Clipboard", name, file_name="name.txt")

            # Add to favorites
            if st.button("⭐ Add to Favorites"):
                if name not in st.session_state["favorites"]:
                    st.session_state["favorites"].append(name)
                    st.success("Added to Favorites")

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
            results.append({"GeneratedName": name, **used})
            insert_history(name, selected_type, used, st.session_state.auth["username"])
        out_df = pd.DataFrame(results)
        st.dataframe(out_df, width="stretch")
        st.download_button("⬇️ Download CSV", data=out_df.to_csv(index=False), file_name="bulk_results.csv")

# =================================
# History Tab
# =================================
with tab_hist:
    st.subheader("History")
    df = fetch_history()
    st.dataframe(df, width="stretch", height=420)

# =================================
# Admin Tab
# =================================
with tab_admin:
    st.subheader("Template Management")
    if not IS_ADMIN:
        st.warning("Admins only")
    else:
        updated_templates = {}
        for t_type, t_string in TEMPLATES.items():
            col1, col2, col3, col4 = st.columns([2,4,1,1])
            col1.write(t_type)
            new_template = col2.text_input(f"Template for {t_type}", value=t_string, key=f"edit_{t_type}")
            
            if col3.button("💾 Save", key=f"save_{t_type}"):
                updated_templates[t_type] = new_template
                st.success(f"Updated: {t_type}")
            
            if col4.button("🗑️ Delete", key=f"delete_{t_type}"):
                TEMPLATES.pop(t_type, None)
                save_templates(TEMPLATES)
                st.warning(f"Deleted: {t_type}")
                rerun()

        if updated_templates:
            TEMPLATES.update(updated_templates)
            save_templates(TEMPLATES)
            rerun()

        st.markdown("---")
        st.subheader("➕ Add New Template")
        new_type = st.text_input("Template Type (label)", value="")
        new_template = st.text_input("Template String", value="")
        if st.button("Add Template"):
            if new_type and new_template:
                TEMPLATES[new_type] = new_template
                save_templates(TEMPLATES)
                st.success(f"Added: {new_type}")
                rerun()

# =================================
# Favorites Tab
# =================================
with tab_fav:
    st.subheader("⭐ Favorite Names")
    favs = st.session_state["favorites"]
    if favs:
        st.dataframe(pd.DataFrame(favs, columns=["Favorite Names"]), width="stretch")
        st.download_button("⬇️ Download Favorites", "\n".join(favs), file_name="favorites.txt")
    else:
        st.info("No favorites yet. Add some from the Generate tab!")
