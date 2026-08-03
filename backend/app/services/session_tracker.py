from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SessionActivity:
    session_id: str
    operation: str
    message: str
    started_at: float = field(default_factory=time.time)


_activities: dict[str, SessionActivity] = {}


def start_activity(session_id: str, operation: str, message: str = "") -> None:
    _activities[session_id] = SessionActivity(
        session_id=session_id,
        operation=operation,
        message=message,
    )


def end_activity(session_id: str) -> None:
    _activities.pop(session_id, None)


def get_session_status(session_id: str) -> dict:
    activity = _activities.get(session_id)
    if not activity:
        return {
            "session_id": session_id,
            "active": False,
            "operation": None,
            "message": "",
            "elapsed_seconds": 0,
        }
    elapsed = max(0, int(time.time() - activity.started_at))
    return {
        "session_id": session_id,
        "active": True,
        "operation": activity.operation,
        "message": activity.message,
        "elapsed_seconds": elapsed,
    }
