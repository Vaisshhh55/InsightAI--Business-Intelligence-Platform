from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable, List, Optional


class ChatMemory:
    """Persist and retrieve recent assistant conversation turns for a dataset/user pair."""

    def __init__(self, db_factory: Optional[Callable[[], sqlite3.Connection]] = None) -> None:
        self._db_factory = db_factory

    def _get_connection(self) -> sqlite3.Connection:
        if self._db_factory is None:
            raise RuntimeError("A database factory is required for chat memory.")
        connection = self._db_factory()
        connection.row_factory = sqlite3.Row
        return connection

    def add_turn(self, dataset_id: str, user_id: Optional[int], user_message: str, assistant_response: str) -> None:
        if not dataset_id:
            return
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO chats (dataset_id, user_id, role, message, response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (dataset_id, user_id, "user", user_message, assistant_response, datetime.utcnow().isoformat()),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_recent_context(self, dataset_id: str, user_id: Optional[int], limit: int = 6) -> List[dict]:
        if not dataset_id:
            return []
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT role, message, response FROM chats WHERE dataset_id = ? AND (user_id IS NULL OR user_id = ?) ORDER BY id DESC LIMIT ?",
                (dataset_id, user_id, limit),
            ).fetchall()
            conn.close()
        except Exception:
            return []

        turns: List[dict] = []
        for row in reversed(rows):
            if row["role"] == "user":
                turns.append({"role": "user", "content": row["message"]})
            else:
                turns.append({"role": "assistant", "content": row["response"]})
        return turns
