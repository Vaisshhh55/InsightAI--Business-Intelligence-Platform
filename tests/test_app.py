import os
from pathlib import Path

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = os.path.join(os.path.dirname(__file__), "test_insightai.db")
    if os.path.exists(app.config["DATABASE_PATH"]):
        os.remove(app.config["DATABASE_PATH"])
    from app import init_db

    init_db()
    with app.test_client() as client:
        yield client


def test_demo_dataset_endpoint_returns_analysis(client):
    response = client.post(
        "/auth/register",
        json={"email": "analyst@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.post(
        "/auth/login",
        json={"email": "analyst@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.get("/demo")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataset_name"] == "demo"
    assert payload["quality_score"] >= 0
    assert "assistant_response" in payload


def test_xls_upload_endpoint_returns_analysis(client):
    response = client.post(
        "/auth/register",
        json={"email": "analyst2@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.post(
        "/auth/login",
        json={"email": "analyst2@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    workbook_path = Path(__file__).resolve().parents[1] / "uploads" / "sample_-_superstore.xls"
    assert workbook_path.exists()

    with workbook_path.open("rb") as handle:
        response = client.post(
            "/upload",
            data={"file": (handle, workbook_path.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataset_name"] == "sample_-_superstore"
    assert payload["row_count"] > 0
    assert payload["quality_score"] >= 0
    assert "assistant_response" in payload


def test_xls_upload_generates_forecast_narrative(client):
    response = client.post(
        "/auth/register",
        json={"email": "analyst3@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.post(
        "/auth/login",
        json={"email": "analyst3@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    workbook_path = Path(__file__).resolve().parents[1] / "uploads" / "sample_-_superstore.xls"
    with workbook_path.open("rb") as handle:
        response = client.post(
            "/upload",
            data={"file": (handle, workbook_path.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["forecast"]
    assert payload["time_series"]
    assert "forecast" in payload["executive_narrative"].lower()


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302


def test_assistant_reply_works_after_login(client):
    response = client.post(
        "/auth/register",
        json={"email": "assistant@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.post(
        "/assistant",
        json={"dataset_id": "demo", "message": "What is the trend?"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert "reply" in payload
    assert payload["reply"]


def test_assistant_answers_broader_dataset_questions(client):
    response = client.post(
        "/auth/register",
        json={"email": "assistant2@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.post(
        "/assistant",
        json={"dataset_id": "demo", "message": "How many rows are in this dataset?"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert "reply" in payload
    assert "rows" in payload["reply"].lower()


def test_session_endpoint_returns_user_profile(client):
    response = client.post(
        "/auth/register",
        json={"email": "profile@example.com", "password": "securepass123"},
    )
    assert response.status_code == 200

    response = client.get("/auth/session")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["authenticated"] is True
    assert payload["user"]["email"] == "profile@example.com"
