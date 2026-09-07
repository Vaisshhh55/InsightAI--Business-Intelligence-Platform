from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify, request, session, g
import bleach

from assistant_service import AssistantService
from chat_memory import ChatMemory
from gemini_client import GeminiClient
from app import get_db_connection, DATASETS, login_or_jwt_required

assistant_bp = Blueprint("assistant_bp", __name__)


def create_assistant_service() -> AssistantService:
    return AssistantService(
        gemini_client=GeminiClient(),
        chat_memory=ChatMemory(db_factory=get_db_connection),
    )


@assistant_bp.route("/assistant", methods=["POST"])
@login_or_jwt_required
def assistant_endpoint():
    payload = request.get_json(silent=True) or {}
    dataset_id = payload.get("dataset_id", "demo")
    message = payload.get("message", "")

    if dataset_id == "demo":
        import os
        import pandas as pd
        from app import clean_dataset, prepare_analysis

        demo_path = os.path.join(os.path.dirname(__file__), "sample_data.csv")
        if not os.path.exists(demo_path):
            return jsonify({"reply": "Upload or load a dataset first."})
        data = pd.read_csv(demo_path)
        cleaned, quality_score, details = clean_dataset(data)
        analysis = prepare_analysis(cleaned, "demo", quality_score)
        analysis["quality_score"] = quality_score
        analysis["details"] = details
        DATASETS[dataset_id] = {"path": demo_path, "data": cleaned, "analysis": analysis}

    dataset = DATASETS.get(dataset_id)
    if not dataset:
        return jsonify({"reply": "Upload or load a dataset first."})

    safe_message = bleach.clean(message or "")
    user_id = session.get("user_id") or getattr(g, "user_id", None)
    service = create_assistant_service()
    response_payload = service.answer(dataset, safe_message, user_id=user_id, dataset_id=dataset_id)

    return jsonify({
        "reply": response_payload["answer"],
        "answer": response_payload["answer"],
        "table": response_payload["table"],
        "chart": response_payload["chart"],
        "recommendation": response_payload["recommendation"],
        "confidence": response_payload["confidence"],
        "follow_up_questions": response_payload["follow_up_questions"],
    })
