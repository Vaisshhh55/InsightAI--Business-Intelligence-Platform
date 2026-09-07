from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class GeminiClient:
    """Thin wrapper around the Google Gemini API with safe defaults for dataset-only responses."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash") -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, *, temperature: float = 0.2, max_output_tokens: int = 800) -> Dict[str, Any]:
        if not self.is_configured():
            return {
                "answer": "The Gemini API is not configured. Please set the GEMINI_API_KEY environment variable.",
                "table": {},
                "chart": {},
                "recommendation": "",
                "confidence": "low",
                "follow_up_questions": [],
            }

        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("Unexpected response shape")
            return parsed
        except Exception as exc:
            return {
                "answer": f"The assistant could not produce a response from Gemini. Error: {exc}",
                "table": {},
                "chart": {},
                "recommendation": "",
                "confidence": "low",
                "follow_up_questions": [],
            }
