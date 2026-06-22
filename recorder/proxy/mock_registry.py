"""Side-effect detection and replay mock payloads."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from recorder.serializer.aer_schema import ToolCall


MUTATING_SQL = {"insert", "update", "delete", "drop", "alter", "create", "truncate"}


@dataclass(slots=True)
class MockDecision:
    tool_name: str
    side_effecting: bool
    reason: str
    mock_result: Any | None = None


class MockRegistry:
    """Classifies tools that must never run live during replay."""

    def __init__(self) -> None:
        self._explicit: dict[str, MockDecision] = {}
        self._patterns: list[tuple[re.Pattern[str], str]] = [
            (re.compile(r"(send|email|mail|notify|slack|sms)", re.I), "external message"),
            (re.compile(r"(write|update|delete|create|insert|attach|publish|commit)", re.I), "mutation verb"),
            (re.compile(r"(payment|charge|refund|deploy)", re.I), "high-impact operation"),
        ]

    def register(
        self,
        tool_name: str,
        *,
        side_effecting: bool,
        reason: str = "explicit registry entry",
        mock_result: Any | None = None,
    ) -> None:
        self._explicit[tool_name] = MockDecision(
            tool_name=tool_name,
            side_effecting=side_effecting,
            reason=reason,
            mock_result=mock_result,
        )

    def classify(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> MockDecision:
        if tool_name in self._explicit:
            return self._explicit[tool_name]

        parameters = parameters or {}
        sql = str(parameters.get("sql") or parameters.get("query") or "").strip().lower()
        first_sql_word = sql.split(None, 1)[0] if sql else ""
        if first_sql_word in MUTATING_SQL:
            return MockDecision(tool_name, True, f"mutating SQL: {first_sql_word}")

        for pattern, reason in self._patterns:
            if pattern.search(tool_name):
                return MockDecision(tool_name, True, reason)

        return MockDecision(tool_name, False, "read-only by default")

    def is_side_effecting(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> bool:
        return self.classify(tool_name, parameters).side_effecting

    def mock_payload_for(self, call: ToolCall) -> Any:
        explicit = self._explicit.get(call.tool_name)
        if explicit and explicit.mock_result is not None:
            return explicit.mock_result
        return call.result
