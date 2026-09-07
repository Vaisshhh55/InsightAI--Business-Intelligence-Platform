from pathlib import Path

import pandas as pd
import streamlit as st

from app import clean_dataset, prepare_analysis
from assistant_service import AssistantService


BASE_DIR = Path(__file__).resolve().parent


st.set_page_config(page_title="InsightAI", page_icon="I", layout="wide")


@st.cache_data(show_spinner=False)
def load_dataset(file_bytes, filename):
    from io import BytesIO

    stream = BytesIO(file_bytes)
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(stream)
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(stream)
    raise ValueError("Upload a CSV, XLS, or XLSX file.")


def analyze_dataset(dataframe, dataset_name):
    cleaned, quality_score, cleaning_stats = clean_dataset(dataframe)
    analysis = prepare_analysis(cleaned, dataset_name, quality_score)
    analysis["quality_score"] = quality_score
    analysis["cleaning_stats"] = cleaning_stats
    return cleaned, analysis


def format_metric(value):
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


st.title("InsightAI")
st.caption("Upload a dataset, inspect its quality, and ask questions about the results.")

with st.sidebar:
    st.header("Dataset")
    uploaded_file = st.file_uploader("Choose a dataset", type=["csv", "xls", "xlsx"])
    use_demo = st.button("Load demo dataset", use_container_width=True)

if use_demo:
    demo_path = BASE_DIR / "sample_data.csv"
    if demo_path.exists():
        st.session_state["dataset_bytes"] = demo_path.read_bytes()
        st.session_state["dataset_name"] = demo_path.name
    else:
        st.error("The demo dataset is not available in this deployment.")

if uploaded_file is not None:
    st.session_state["dataset_bytes"] = uploaded_file.getvalue()
    st.session_state["dataset_name"] = uploaded_file.name

if "dataset_bytes" not in st.session_state:
    st.info("Upload a CSV or Excel file from the sidebar to begin.")
    st.stop()

dataset_name = st.session_state["dataset_name"]

try:
    raw_dataframe = load_dataset(st.session_state["dataset_bytes"], dataset_name)
    dataframe, analysis = analyze_dataset(raw_dataframe, dataset_name)
except Exception as error:
    st.error(f"Could not process this dataset: {error}")
    st.stop()

st.subheader(dataset_name)
metric_column = analysis["metric_column"]
metric_total = analysis["metric_total"]
quality_score = analysis["quality_score"]

metric_one, metric_two, metric_three, metric_four = st.columns(4)
metric_one.metric("Rows", f"{analysis['row_count']:,}")
metric_two.metric("Columns", f"{analysis['column_count']:,}")
metric_three.metric("Data quality", f"{quality_score}/100")
metric_four.metric(f"Total {metric_column}", format_metric(metric_total))

overview_tab, data_tab, assistant_tab = st.tabs(["Overview", "Cleaned data", "Assistant"])

with overview_tab:
    st.write(analysis["executive_narrative"])
    left, right = st.columns(2)
    with left:
        st.metric("Average metric", format_metric(analysis["metric_average"]))
        st.metric("Growth rate", f"{analysis['growth_rate']}%")
    with right:
        if analysis["time_series"]:
            trend = pd.DataFrame(analysis["time_series"]).set_index("label")
            st.line_chart(trend["value"])
        elif analysis["top_categories"]:
            categories = pd.DataFrame(analysis["top_categories"]).set_index("name")
            st.bar_chart(categories["value"])
        else:
            st.caption("No date or categorical column was detected for a chart.")

    st.subheader("Recommendations")
    for recommendation in analysis["recommendations"]:
        st.write(f"- {recommendation}")

with data_tab:
    stats = analysis["cleaning_stats"]
    st.write(
        f"Removed {stats['duplicates_removed']} duplicate rows. "
        f"Missing values changed from {stats['missing_before']} to {stats['missing_after']}."
    )
    st.dataframe(dataframe, use_container_width=True, hide_index=True)

with assistant_tab:
    question = st.text_input("Ask a question about this dataset", placeholder="What is the trend?")
    if question:
        service = AssistantService()
        response = service.answer({"data": dataframe, "analysis": analysis}, question)
        st.write(response["answer"])
        if response.get("recommendation"):
            st.caption(response["recommendation"])