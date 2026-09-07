from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from chat_memory import ChatMemory
from gemini_client import GeminiClient


class AssistantService:
    """Dataset-aware chatbot service that uses Gemini for natural language responses."""

    def __init__(self, gemini_client: Optional[GeminiClient] = None, chat_memory: Optional[ChatMemory] = None) -> None:
        self.gemini_client = gemini_client or GeminiClient()
        self.chat_memory = chat_memory

    def build_prompt(self, dataset: Dict[str, Any], question: str, history: Optional[List[dict]] = None) -> str:
        df = dataset.get("data")
        analysis = dataset.get("analysis", {})
        if df is None or not hasattr(df, "columns"):
            raise ValueError("Dataset is not available")

        schema = []
        for column in df.columns:
            dtype = str(df[column].dtype)
            sample_values = df[column].dropna().astype(str).head(5).tolist()
            schema.append({"name": column, "dtype": dtype, "sample_values": sample_values})

        summary_stats = {}
        for column in df.columns:
            if pd.api.types.is_numeric_dtype(df[column]):
                series = pd.to_numeric(df[column], errors="coerce").dropna()
                summary_stats[column] = {
                    "count": int(series.count()),
                    "mean": round(float(series.mean()), 2) if series.count() else None,
                    "sum": round(float(series.sum()), 2) if series.count() else None,
                    "min": round(float(series.min()), 2) if series.count() else None,
                    "max": round(float(series.max()), 2) if series.count() else None,
                }
            else:
                summary_stats[column] = {
                    "count": int(df[column].count()),
                    "unique_values": int(df[column].nunique(dropna=True)),
                    "top_values": df[column].dropna().astype(str).value_counts().head(5).to_dict(),
                }

        history_text = ""
        if history:
            history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])

        return f"""
You are InsightAI, a dataset-only business intelligence assistant.
Rules:
- Answer ONLY from the uploaded dataset.
- If the information is not present in the dataset, say exactly: "The uploaded dataset does not contain this information."
- Never hallucinate. Never invent numbers or trends.
- Do not execute code. Do not reveal secrets or API keys.
- Prefer concise, business-friendly answers.
- When asked for plots or charts, return a Plotly-compatible JSON structure.
- Return valid JSON only.

Dataset name: {analysis.get('dataset_name', 'unknown')}
Row count: {int(df.shape[0])}
Column count: {int(df.shape[1])}
Quality score: {analysis.get('quality_score', 0)}
Metric column: {analysis.get('metric_column', '')}

Dataset schema:
{json.dumps(schema, indent=2)}

Summary statistics:
{json.dumps(summary_stats, indent=2)}

Previous conversation:
{history_text or 'None'}

Current question:
{question}

Return JSON with these keys:
{{
  "answer": string,
  "table": object,
  "chart": object,
  "recommendation": string,
  "confidence": string,
  "follow_up_questions": [string]
}}
"""

    def _fallback_answer(self, dataset: Dict[str, Any], question: str) -> Dict[str, Any]:
        df = dataset.get("data")
        analysis = dataset.get("analysis", {})
        if df is None or not hasattr(df, "columns"):
            return {
                "answer": "The uploaded dataset is not available.",
                "table": {},
                "chart": {},
                "recommendation": "",
                "confidence": "low",
                "follow_up_questions": ["Upload a dataset", "Describe this dataset"],
            }

        lowered = question.lower()
        row_count = int(df.shape[0])
        column_count = int(df.shape[1])
        quality_score = analysis.get("quality_score", 0)
        metric_total = analysis.get("metric_total")
        growth_rate = analysis.get("growth_rate")
        dataset_name = analysis.get("dataset_name", "the uploaded dataset")

        columns = [str(column).lower() for column in df.columns]
        region_columns = [column for column in df.columns if any(token in str(column).lower() for token in ["region", "state", "city", "territory"])]
        sales_column = None
        for column in df.columns:
            name = str(column).lower()
            if any(token in name for token in ["sales", "revenue", "amount", "total"]):
                sales_column = column
                break

        if any(token in lowered for token in ["highest sales region", "sales by region", "top region", "max revenue region", "maximum revenue region"]):
            if region_columns and sales_column:
                grouped = df.groupby(region_columns[0])[sales_column].sum().sort_values(ascending=False)
                top = grouped.head(1)
                if not top.empty:
                    answer = f"Highest sales region: {top.index[0]} with {top.iloc[0]:,.2f} in sales."
                else:
                    answer = f"I could not calculate a region ranking from the available columns in {dataset_name}."
            else:
                answer = f"The uploaded dataset does not contain enough region and sales information to answer that question."
        elif any(token in lowered for token in ["how many rows", "rows are in", "row count", "number of rows"]):
            answer = f"{dataset_name} contains {row_count} rows."
        elif any(token in lowered for token in ["how many columns", "column count", "number of columns", "columns are present"]):
            answer = f"{dataset_name} contains {column_count} columns."
        elif any(token in lowered for token in ["quality", "data quality"]):
            answer = f"The dataset quality score is {quality_score} out of 100."
        elif any(token in lowered for token in ["trend", "growth", "forecast"]):
            if growth_rate is not None:
                answer = f"The current growth rate is {growth_rate}%."
            else:
                answer = "The dataset does not include enough history to describe a trend clearly."
        elif any(token in lowered for token in ["summary", "describe this dataset", "summarize"]):
            answer = (
                f"{dataset_name} has {row_count} rows and {column_count} columns. "
                f"The current quality score is {quality_score} and the primary metric total is {metric_total}."
            )
        elif any(token in lowered for token in ["kpi", "key metrics"]):
            answer = f"Key KPIs to monitor include row volume, column completeness, quality score, and the primary metric total of {metric_total}."
        else:
            if sales_column:
                answer = f"The main sales metric appears to be {sales_column} with a total of {df[sales_column].sum():,.2f}."
            else:
                answer = (
                    f"I can help analyze {dataset_name}. It contains {row_count} rows and {column_count} columns. "
                    f"The current quality score is {quality_score}."
                )

        return {
            "answer": answer,
            "table": {},
            "chart": {},
            "recommendation": analysis.get("recommendations", ["Review the main metric trend and data quality"])[0] if analysis.get("recommendations") else "Review the main metric trend and data quality",
            "confidence": "medium",
            "follow_up_questions": ["Summarize this dataset", "What are the key KPIs?", "Show the main trend"],
        }

    def answer(self, dataset: Dict[str, Any], question: str, user_id: Optional[int] = None, dataset_id: Optional[str] = None) -> Dict[str, Any]:
        if not question or not str(question).strip():
            return {
                "answer": "Please ask a question about the uploaded dataset.",
                "table": {},
                "chart": {},
                "recommendation": "",
                "confidence": "medium",
                "follow_up_questions": ["Summarize this dataset", "What are the key KPIs?"],
            }

        if not self.gemini_client.is_configured():
            payload = self._fallback_answer(dataset, question)
            if self.chat_memory is not None and dataset_id:
                self.chat_memory.add_turn(dataset_id, user_id, question, payload["answer"])
            return payload

        history = []
        if self.chat_memory is not None and dataset_id:
            history = self.chat_memory.get_recent_context(dataset_id, user_id, limit=6)

        prompt = self.build_prompt(dataset, question, history)
        response = self.gemini_client.generate(prompt)

        # Ensure the response has the expected shape.
        payload = {
            "answer": response.get("answer") or "The assistant could not generate an answer.",
            "table": response.get("table") or {},
            "chart": response.get("chart") or {},
            "recommendation": response.get("recommendation") or "",
            "confidence": response.get("confidence") or "medium",
            "follow_up_questions": response.get("follow_up_questions") or [],
        }

        if self.chat_memory is not None and dataset_id:
            self.chat_memory.add_turn(dataset_id, user_id, question, payload["answer"])

        return payload
