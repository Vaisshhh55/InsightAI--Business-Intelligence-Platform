from __future__ import annotations

import io
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from werkzeug.security import check_password_hash, generate_password_hash

from app import clean_dataset, get_db_connection, init_db, prepare_analysis
from assistant_service import AssistantService
from chat_memory import ChatMemory


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

st.set_page_config(page_title="InsightAI", page_icon="I", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] { border-right: 1px solid #d9e2ec; }
    [data-testid="stMetric"] { background: #f7fafc; border: 1px solid #e2e8f0; padding: 14px; border-radius: 8px; }
    .insight-header { padding: 12px 0 8px; }
    .insight-kicker { color: #167d7f; font-size: 0.78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    </style>
    """,
    unsafe_allow_html=True,
)


def ensure_state():
    defaults = {
        "authenticated_user": None,
        "dataset_id": None,
        "dataset_name": None,
        "raw_dataframe": None,
        "cleaned_dataframe": None,
        "analysis": None,
        "cleaning_details": None,
        "page": "Overview",
        "auth_mode": "Login",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def user_by_email(email):
    conn = get_db_connection()
    row = conn.execute("SELECT id, email, role, password FROM users WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
    conn.close()
    return row


def current_user():
    user = st.session_state.get("authenticated_user")
    return user if user else None


def audit(user_id, action, target, details=""):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, target, details, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def register(email, password):
    if not email or "@" not in email or len(password) < 8:
        return False, "Use a valid email and a password with at least 8 characters."
    if user_by_email(email):
        return False, "An account with this email already exists."
    conn = get_db_connection()
    cursor = conn.execute(
        "INSERT INTO users (email, password, role, created_at) VALUES (?, ?, ?, ?)",
        (email.strip().lower(), generate_password_hash(password), "user", datetime.utcnow().isoformat()),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    audit(user_id, "register", email, "Streamlit registration")
    return True, "Account created. You can now sign in."


def authenticate(email, password):
    row = user_by_email(email)
    if not row or not check_password_hash(row["password"], password):
        return None
    user = {"id": row["id"], "email": row["email"], "role": row["role"] or "user"}
    audit(user["id"], "login", user["email"], "Streamlit login")
    return user


def parse_dataset(file_bytes, filename):
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Files must be 10 MB or smaller.")
    suffix = Path(filename).suffix.lower()
    stream = io.BytesIO(file_bytes)
    if suffix == ".csv":
        return pd.read_csv(stream)
    if suffix == ".xlsx":
        return pd.read_excel(stream, engine="openpyxl", nrows=20000)
    if suffix == ".xls":
        try:
            return pd.read_excel(stream, engine="xlrd", nrows=20000)
        except ImportError as error:
            raise ValueError("Legacy .xls files require the xlrd package. Please redeploy so requirements.txt is installed.") from error
        except Exception as error:
            raise ValueError(f"This .xls file could not be read: {error}") from error
    raise ValueError("Upload a CSV, XLS, or XLSX file.")


def analyze_dataset(dataframe, dataset_name):
    cleaned, quality_score, details = clean_dataset(dataframe)
    analysis = prepare_analysis(cleaned, dataset_name, quality_score)
    analysis["quality_score"] = quality_score
    analysis["details"] = details
    return cleaned, analysis


def load_dataset(file_bytes, filename, persist=True):
    raw = parse_dataset(file_bytes, filename)
    cleaned, analysis = analyze_dataset(raw, Path(filename).stem)
    dataset_id = f"{Path(filename).stem}-{secrets.token_hex(4)}"
    st.session_state.update(
        {
            "dataset_id": dataset_id,
            "dataset_name": Path(filename).stem,
            "raw_dataframe": raw,
            "cleaned_dataframe": cleaned,
            "analysis": analysis,
            "cleaning_details": analysis["details"],
            "page": "Overview",
        }
    )
    if persist:
        UPLOAD_DIR.mkdir(exist_ok=True)
        stored_path = UPLOAD_DIR / Path(filename).name
        stored_path.write_bytes(file_bytes)
        user = current_user()
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO datasets (dataset_id, filename, path, row_count, column_count, quality_score, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, filename, str(stored_path), analysis["row_count"], analysis["column_count"], analysis["quality_score"], user["id"], datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
        audit(user["id"], "upload", dataset_id, filename)


def metric_format(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def analysis_ready():
    return st.session_state.get("analysis") is not None and st.session_state.get("cleaned_dataframe") is not None


def refresh_analysis_if_needed():
    analysis = st.session_state.get("analysis")
    dataframe = st.session_state.get("cleaned_dataframe")
    if not analysis or dataframe is None:
        return
    if "top_categories" not in analysis:
        analysis["top_categories"] = []
    if not analysis["top_categories"]:
        refreshed = prepare_analysis(dataframe, st.session_state["dataset_name"], analysis.get("quality_score", 0))
        refreshed["quality_score"] = analysis.get("quality_score", 0)
        refreshed["details"] = analysis.get("details", {})
        st.session_state["analysis"] = refreshed


def render_auth():
    st.markdown('<div class="insight-header"><div class="insight-kicker">Business intelligence workspace</div><h1>InsightAI</h1></div>', unsafe_allow_html=True)
    st.write("Turn messy datasets into clear decisions, forecasts, and explainable answers.")
    login_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            user = authenticate(email, password)
            if user:
                st.session_state["authenticated_user"] = user
                st.rerun()
            st.error("Email or password is incorrect.")
    with register_tab:
        with st.form("register_form"):
            email = st.text_input("Email", key="register_email")
            password = st.text_input("Password", type="password", key="register_password")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account", use_container_width=True)
        if submitted:
            if password != confirm:
                st.error("Passwords do not match.")
            else:
                success, message = register(email, password)
                (st.success if success else st.error)(message)


def render_sidebar():
    user = current_user()
    with st.sidebar:
        st.markdown("## InsightAI")
        st.caption(user["email"])
        pages = ["Overview", "Upload Dataset", "Analytics", "Forecasting", "AI Assistant", "Reports", "Profile"]
        if user["role"] == "admin":
            pages.append("Admin")
        selected = st.radio("Workspace", pages, index=pages.index(st.session_state["page"]))
        if selected != st.session_state["page"]:
            st.session_state["page"] = selected
            st.rerun()
        st.divider()
        if st.button("Sign out", use_container_width=True):
            st.session_state["authenticated_user"] = None
            st.session_state["dataset_id"] = None
            st.rerun()


def render_dataset_loader():
    st.subheader("Upload Dataset")
    st.write("Load CSV or Excel data to create a cleaned, analyzed workspace.")
    uploaded = st.file_uploader("Choose a dataset", type=["csv", "xls", "xlsx"])
    if uploaded is not None and st.button("Analyze uploaded dataset", type="primary"):
        try:
            load_dataset(uploaded.getvalue(), uploaded.name)
            st.success("Dataset analyzed successfully.")
            st.rerun()
        except Exception as error:
            st.error(f"Could not process {uploaded.name}: {error}")
            st.info("Try downloading the workbook again or saving it as .xlsx or .csv before uploading.")
    st.divider()
    st.subheader("Demo dataset")
    st.write("Explore the included sample data without uploading a file.")
    if st.button("Load demo dataset", use_container_width=True):
        demo = BASE_DIR / "sample_data.csv"
        if demo.exists():
            load_dataset(demo.read_bytes(), demo.name, persist=False)
            st.success("Demo dataset loaded.")
            st.rerun()
        else:
            st.error("sample_data.csv is not available.")


def render_kpis(analysis):
    first, second, third = st.columns(3)
    first.metric("Rows", f"{analysis['row_count']:,}")
    second.metric("Columns", f"{analysis['column_count']:,}")
    third.metric("Quality", f"{analysis['quality_score']}/100")
    fourth, fifth = st.columns(2)
    fourth.metric("Growth", f"{analysis['growth_rate']}%")
    fifth.metric(f"Total {analysis['metric_column']}", metric_format(analysis["metric_total"]))


def render_overview():
    analysis = st.session_state["analysis"]
    st.subheader("Overview")
    st.caption(st.session_state["dataset_name"])
    render_kpis(analysis)
    st.info(analysis["executive_narrative"])
    left, right = st.columns(2)
    with left:
        st.subheader("Trend")
        if analysis["time_series"]:
            trend = pd.DataFrame(analysis["time_series"])
            st.line_chart(trend.set_index("label")["value"])
        else:
            st.caption("No date field was detected.")
    with right:
        st.subheader("Top segments")
        if analysis["top_categories"]:
            categories = pd.DataFrame(analysis["top_categories"]).set_index("name")
            st.bar_chart(categories["value"])
        else:
            st.caption("No categorical segment was detected.")
    lower_left, lower_right = st.columns(2)
    with lower_left:
        st.subheader("Recommendations")
        for item in analysis["recommendations"]:
            st.write(f"- {item}")
    with lower_right:
        st.subheader("Feature importance")
        if analysis["feature_importance"]:
            st.dataframe(pd.DataFrame(analysis["feature_importance"]), hide_index=True, use_container_width=True)
        else:
            st.caption("No correlated numeric features were found.")


def render_analytics():
    analysis = st.session_state["analysis"]
    dataframe = st.session_state["cleaned_dataframe"]
    st.subheader("Analytics")
    render_kpis(analysis)
    st.write(analysis["executive_narrative"])
    st.subheader("Cleaned dataset")
    details = analysis["details"]
    st.caption(f"Duplicates removed: {details['duplicates_removed']} | Missing values: {details['missing_before']} -> {details['missing_after']}")
    st.dataframe(dataframe, use_container_width=True, hide_index=True)


def render_forecasting():
    analysis = st.session_state["analysis"]
    st.subheader("Forecasting")
    if not analysis["time_series"]:
        st.warning("A date/time column and at least three periods are required for forecasting.")
        return
    history = pd.DataFrame(analysis["time_series"])
    st.line_chart(history.set_index("label")["value"])
    if analysis["forecast"]:
        forecast_labels = [f"Forecast {index + 1}" for index in range(len(analysis["forecast"]))]
        forecast = pd.DataFrame({"period": forecast_labels, "value": analysis["forecast"]})
        st.subheader("Next periods")
        st.dataframe(forecast, use_container_width=True, hide_index=True)
        st.bar_chart(forecast.set_index("period")["value"])
    else:
        st.info("The dataset does not contain enough history for a forecast.")


def render_assistant():
    st.subheader("AI Assistant")
    st.caption("Ask questions about the active dataset. Answers use the uploaded data only.")
    history_key = f"chat_{st.session_state['dataset_id']}"
    st.session_state.setdefault(history_key, [])
    for turn in st.session_state[history_key]:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])
    question = st.chat_input("Ask about sales, trends, quality, customers, or regions")
    if question:
        with st.chat_message("user"):
            st.write(question)
        service = AssistantService(chat_memory=ChatMemory(get_db_connection))
        response = service.answer(
            {"data": st.session_state["cleaned_dataframe"], "analysis": st.session_state["analysis"]},
            question,
            user_id=current_user()["id"],
            dataset_id=st.session_state["dataset_id"],
        )
        answer = response.get("answer", "No answer was returned.")
        st.session_state[history_key].extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
        with st.chat_message("assistant"):
            st.write(answer)
            if response.get("recommendation"):
                st.caption(response["recommendation"])
            st.caption(f"Confidence: {response.get('confidence', 'medium')}")
            if response.get("table"):
                st.json(response["table"])
            if response.get("follow_up_questions"):
                st.write("Suggested questions: " + " | ".join(response["follow_up_questions"]))


def build_excel(analysis, dataframe):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary = pd.DataFrame(
            {
                "Metric": ["Dataset", "Rows", "Columns", "Quality Score", "Metric Total", "Growth Rate"],
                "Value": [analysis["dataset_name"], analysis["row_count"], analysis["column_count"], analysis["quality_score"], analysis["metric_total"], analysis["growth_rate"]],
            }
        )
        summary.to_excel(writer, index=False, sheet_name="Executive Summary")
        dataframe.to_excel(writer, index=False, sheet_name="Cleaned Data")
    return output.getvalue()


def build_pdf(analysis):
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter)
    y = 740
    document.setFont("Helvetica-Bold", 16)
    document.drawString(54, y, "InsightAI Executive Report")
    y -= 34
    document.setFont("Helvetica", 11)
    lines = [
        f"Dataset: {analysis['dataset_name']}",
        f"Rows: {analysis['row_count']}",
        f"Columns: {analysis['column_count']}",
        f"Quality score: {analysis['quality_score']}/100",
        f"Metric total: {analysis['metric_total']}",
        f"Growth rate: {analysis['growth_rate']}%",
        "",
        "Recommendations:",
    ] + [f"- {item}" for item in analysis["recommendations"]]
    for line in lines:
        document.drawString(54, y, line[:110])
        y -= 20
    document.save()
    return output.getvalue()


def render_reports():
    analysis = st.session_state["analysis"]
    dataframe = st.session_state["cleaned_dataframe"]
    st.subheader("Reports")
    st.write("Download the current dataset analysis in portable formats.")
    excel_bytes = build_excel(analysis, dataframe)
    pdf_bytes = build_pdf(analysis)
    json_bytes = json.dumps(analysis, indent=2, default=str).encode("utf-8")
    first, second, third = st.columns(3)
    first.download_button("Download Excel", excel_bytes, f"{analysis['dataset_name']}_report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    second.download_button("Download PDF", pdf_bytes, f"{analysis['dataset_name']}_report.pdf", "application/pdf", use_container_width=True)
    third.download_button("Download JSON", json_bytes, f"{analysis['dataset_name']}_quality.json", "application/json", use_container_width=True)
    st.subheader("Executive summary")
    st.write(analysis["executive_narrative"])


def render_profile():
    user = current_user()
    st.subheader("Profile")
    st.write(f"Email: **{user['email']}**")
    st.write(f"Role: **{user['role']}**")
    st.caption("Your account and dataset metadata are stored in the SQLite database.")


def render_admin():
    if current_user()["role"] != "admin":
        st.error("Admin role required.")
        return
    st.subheader("Admin")
    users_tab, datasets_tab, audit_tab = st.tabs(["Users", "Datasets", "Audit logs"])
    with users_tab:
        conn = get_db_connection()
        users = conn.execute("SELECT id, email, role, created_at FROM users ORDER BY id").fetchall()
        conn.close()
        for row in users:
            first, second, third, fourth = st.columns([3, 1, 1, 1])
            first.write(row["email"])
            second.write(row["role"])
            if row["id"] != current_user()["id"]:
                if row["role"] == "admin":
                    if third.button("Demote", key=f"demote_{row['id']}"):
                        conn = get_db_connection()
                        conn.execute("UPDATE users SET role = 'user' WHERE id = ?", (row["id"],))
                        conn.commit()
                        conn.close()
                        audit(current_user()["id"], "demote", str(row["id"]), row["email"])
                        st.rerun()
                elif third.button("Promote", key=f"promote_{row['id']}"):
                    conn = get_db_connection()
                    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (row["id"],))
                    conn.commit()
                    conn.close()
                    audit(current_user()["id"], "promote", str(row["id"]), row["email"])
                    st.rerun()
                if fourth.button("Remove", key=f"remove_{row['id']}"):
                    conn = get_db_connection()
                    conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))
                    conn.commit()
                    conn.close()
                    audit(current_user()["id"], "remove_user", str(row["id"]), row["email"])
                    st.rerun()
    with datasets_tab:
        conn = get_db_connection()
        rows = conn.execute("SELECT dataset_id, filename, row_count, quality_score, created_at FROM datasets ORDER BY created_at DESC").fetchall()
        conn.close()
        st.dataframe(pd.DataFrame([dict(row) for row in rows]), use_container_width=True, hide_index=True)
    with audit_tab:
        conn = get_db_connection()
        rows = conn.execute("SELECT user_id, action, target, details, created_at FROM audit_logs ORDER BY id DESC LIMIT 200").fetchall()
        conn.close()
        st.dataframe(pd.DataFrame([dict(row) for row in rows]), use_container_width=True, hide_index=True)


def main():
    init_db()
    ensure_state()
    refresh_analysis_if_needed()
    if not current_user():
        render_auth()
        return
    render_sidebar()
    page = st.session_state["page"]
    if page == "Upload Dataset":
        render_dataset_loader()
    elif page == "Profile":
        render_profile()
    elif page == "Admin":
        render_admin()
    elif not analysis_ready():
        render_dataset_loader()
    elif page == "Overview":
        render_overview()
    elif page == "Analytics":
        render_analytics()
    elif page == "Forecasting":
        render_forecasting()
    elif page == "AI Assistant":
        render_assistant()
    elif page == "Reports":
        render_reports()


if __name__ == "__main__":
    main()
else:
    main()
