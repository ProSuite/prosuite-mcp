from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TILE_PROGRESS_RE = re.compile(
    r"\bProcessing\s+tile\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ProgressEvent:
    """Progress values used by MCP notifications and async run status."""

    current: int | None = None
    total: int | None = None
    message: str = ""
    message_level: str | None = None

    @classmethod
    def from_response(cls, response: Any) -> "ProgressEvent | None":
        progress = getattr(response, "progress", None)
        response_message = getattr(response, "message", "")
        response_level = getattr(response, "message_level", None)

        messages = [response_message]
        if progress is not None:
            messages = [
                getattr(progress, "message", ""),
                response_message,
                getattr(progress, "processing_step_message", ""),
            ]
        message = next((value for value in messages if value), "")

        current = total = None
        if progress is not None:
            for current_name, total_name in (
                ("overall_progress_current_step", "overall_progress_total_steps"),
                ("detailed_progress_current_step", "detailed_progress_total_steps"),
            ):
                candidate_total = getattr(progress, total_name, None)
                if candidate_total and candidate_total > 0:
                    current = getattr(progress, current_name, None)
                    total = candidate_total
                    break

        if total is None:
            for value in messages:
                match = _TILE_PROGRESS_RE.search(value)
                if match:
                    current, total = (int(part) for part in match.groups())
                    break

        if not message and total is None:
            return None
        return cls(
            current=current,
            total=total,
            message=message,
            message_level=(
                getattr(progress, "message_level", None)
                if progress is not None
                else None
            )
            or response_level,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "message_level": self.message_level,
        }
