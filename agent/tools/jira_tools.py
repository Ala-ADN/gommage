"""Jira tool adapters used by the demo and live agents.

By default this keeps deterministic in-memory behavior for stable local replays.
Set ``GOMMAGE_TOOL_MODE=live`` (or ``auto`` with complete Jira env) to execute
live Jira REST API calls.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request


def _read_text_block(node: Any) -> str:
    """Best-effort conversion of Jira ADF content to plain text."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (_read_text_block(item) for item in node))).strip()
    if not isinstance(node, dict):
        return str(node)

    own_text = node.get("text") or ""
    child_text = _read_text_block(node.get("content"))
    if node.get("type") == "paragraph":
        return "\n".join(filter(None, [own_text, child_text])).strip()
    return "".join(filter(None, [own_text, child_text])).strip()


def _adf_doc(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _jira_env_mode() -> bool:
    """Return whether live mode should be active based on env config."""
    mode = (os.getenv("GOMMAGE_TOOL_MODE") or "auto").strip().lower()
    if mode == "live":
        return True
    if mode in {"mock", "demo", "local"}:
        return False
    return bool(
        os.getenv("JIRA_CLOUD_URL")
        and os.getenv("JIRA_API_USER")
        and os.getenv("JIRA_API_TOKEN")
    )


@dataclass
class _LiveJiraClient:
    cloud_url: str
    api_user: str
    api_token: str
    request_timeout: float = 20.0

    def __post_init__(self) -> None:
        self.base_url = self.cloud_url.rstrip("/")
        credentials = f"{self.api_user}:{self.api_token}".encode("utf-8")
        self._auth = base64.b64encode(credentials).decode("ascii")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        target = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            target = f"{target}?{parse.urlencode(params)}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(
            target,
            data=data,
            method=method.upper(),
            headers={
                "Authorization": f"Basic {self._auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.request_timeout) as response:
                raw = response.read()
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Jira API error ({exc.code}) for {method} {path}: {details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Jira API request failed: {exc.reason}") from exc

        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def get_issue(self, issue_key: str, fields: list[str]) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": ",".join(fields)},
        )
        fields_payload = response.get("fields", {})
        status = fields_payload.get("status") or {}
        assignee = fields_payload.get("assignee") or {}
        reporter = fields_payload.get("reporter") or {}
        priority = fields_payload.get("priority") or {}
        issue_type = fields_payload.get("issuetype") or {}
        return {
            "ticket_id": issue_key,
            "summary": fields_payload.get("summary") or f"Jira issue {issue_key}",
            "description": _read_text_block(fields_payload.get("description")) or "",
            "priority": priority.get("name") or "medium",
            "reporter": reporter.get("emailAddress") or reporter.get("displayName") or "",
            "owner": assignee.get("emailAddress")
            or assignee.get("displayName")
            or reporter.get("emailAddress")
            or reporter.get("displayName")
            or "",
            "assignee": assignee.get("emailAddress") or assignee.get("displayName"),
            "labels": fields_payload.get("labels") or [],
            "status": status.get("name"),
            "issue_type": issue_type.get("name"),
        }

    def search(self, jql: str, max_results: int) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/rest/api/3/search",
            params={
                "jql": jql,
                "maxResults": max(1, min(int(max_results), 10)),
                "fields": "summary,status,priority,labels,issuetype",
            },
        )
        results: list[dict[str, Any]] = []
        for item in response.get("issues", []):
            fields_payload = item.get("fields") or {}
            results.append(
                {
                    "ticket_id": item.get("key"),
                    "summary": fields_payload.get("summary"),
                    "status": (fields_payload.get("status") or {}).get("name"),
                    "priority": (fields_payload.get("priority") or {}).get("name"),
                    "labels": fields_payload.get("labels") or [],
                    "issue_type": (fields_payload.get("issuetype") or {}).get("name"),
                }
            )
        return results

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/comment",
            payload={"body": _adf_doc(body)},
        )

    def update_issue(
        self,
        issue_key: str,
        *,
        priority: str | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if priority:
            fields["priority"] = {"name": priority}
        if labels is not None:
            fields["labels"] = labels
        if assignee:
            fields["assignee"] = {"accountId": assignee}
        if fields:
            self._request("PUT", f"/rest/api/3/issue/{issue_key}", payload={"fields": fields})
        comment_result = self.add_comment(issue_key, comment) if comment else None
        return {"ok": True, "ticket_id": issue_key, "fields": fields, "comment": comment_result}

    def get_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
        return list(response.get("transitions", []))

    def transition_issue(self, issue_key: str, transition_id: str, comment: str | None) -> None:
        payload: dict[str, Any] = {"transition": {"id": str(transition_id)}}
        self._request("POST", f"/rest/api/3/issue/{issue_key}/transitions", payload=payload)
        if comment:
            self.add_comment(issue_key, comment)


@dataclass
class JiraToolset:
    tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    enable_live: bool | None = None

    def __post_init__(self) -> None:
        configured_live = _jira_env_mode() if self.enable_live is None else bool(self.enable_live)
        self._live_client: _LiveJiraClient | None = None
        if configured_live:
            base_url = os.getenv("JIRA_CLOUD_URL", "").strip()
            api_user = os.getenv("JIRA_API_USER", "").strip()
            api_token = os.getenv("JIRA_API_TOKEN", "").strip()
            if not base_url or not api_user or not api_token:
                raise RuntimeError(
                    "Live Jira tools requested but credentials are incomplete. "
                    "Set JIRA_CLOUD_URL, JIRA_API_USER, and JIRA_API_TOKEN."
                )
            self._live_client = _LiveJiraClient(base_url, api_user, api_token)
        elif not self.tickets:
            self.tickets["DEMO-101"] = {
                "ticket_id": "DEMO-101",
                "summary": "Customer cannot access billing export",
                "description": "Export job fails after the account migration.",
                "priority": "high",
                "reporter": "ops@example.com",
                "owner": "billing-team@example.com",
                "labels": ["billing"],
                "status": "To Do",
            }

    @property
    def _use_live(self) -> bool:
        return self._live_client is not None

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        if self._use_live:
            assert self._live_client is not None
            return self._live_client.get_issue(
                ticket_id,
                [
                    "summary",
                    "description",
                    "priority",
                    "reporter",
                    "assignee",
                    "labels",
                    "status",
                    "issuetype",
                ],
            )
        if ticket_id not in self.tickets:
            self.tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "summary": "Demo support issue",
                "description": "Synthetic ticket generated for local replay testing.",
                "priority": "medium",
                "reporter": "ops@example.com",
                "owner": "support-team@example.com",
                "labels": [],
                "status": "To Do",
            }
        return dict(self.tickets[ticket_id])

    def search(self, jql: str, max_results: int = 10) -> list[dict[str, Any]]:
        if self._use_live:
            assert self._live_client is not None
            return self._live_client.search(jql, max_results)
        query = jql.lower()
        excluded = {
            match.group(1).upper()
            for match in re.finditer(r"(?:key|issue)\s*!=\s*\"?([A-Z][A-Z0-9]+-\d+)\"?", jql, re.I)
        }
        results = []
        for ticket in self.tickets.values():
            if str(ticket.get("ticket_id", "")).upper() in excluded:
                continue
            searchable = " ".join(
                str(ticket.get(key, "")) for key in ["ticket_id", "summary", "description", "status", "priority"]
            ).lower()
            if any(token.strip("'\"") in searchable for token in query.split() if len(token.strip("'\"")) > 2):
                results.append(dict(ticket))
        return results[: max(1, min(int(max_results), 10))]

    def add_comment(self, ticket_id: str, body: str) -> dict[str, Any]:
        if self._use_live:
            assert self._live_client is not None
            result = self._live_client.add_comment(ticket_id, body)
            return {"ok": True, "ticket_id": ticket_id, "comment_id": result.get("id")}
        comment = {"ticket_id": ticket_id, "body": body}
        self.comments.append(comment)
        return {"ok": True, **comment}

    def update_ticket(
        self,
        ticket_id: str,
        status: str | None = None,
        comment: str = "",
        priority: str | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
    ) -> dict[str, Any]:
        if self._use_live:
            assert self._live_client is not None
            result = self._live_client.update_issue(
                ticket_id,
                priority=priority,
                labels=labels,
                assignee=assignee,
                comment=comment or None,
            )
            if status:
                transition = self.transition(ticket_id, status=status, comment=comment)
                result["transition"] = transition
            return result

        ticket = self.get_ticket(ticket_id)
        if status:
            ticket["status"] = status
        if priority:
            ticket["priority"] = priority
        if labels is not None:
            ticket["labels"] = labels
        if assignee:
            ticket["assignee"] = assignee
            ticket["owner"] = assignee
        self.tickets[ticket_id] = ticket
        update = {
            "ticket_id": ticket_id,
            "status": status,
            "priority": priority,
            "labels": labels,
            "assignee": assignee,
            "comment": comment,
        }
        self.updates.append(update)
        return {"ok": True, **update}

    def assign(self, ticket_id: str, assignee: str) -> dict[str, Any]:
        return self.update_ticket(ticket_id, assignee=assignee)

    def transition(self, ticket_id: str, status: str, comment: str = "") -> dict[str, Any]:
        if self._use_live:
            assert self._live_client is not None
            issue = self.get_ticket(ticket_id)
            current_status = issue.get("status")
            transitions = self._live_client.get_transitions(ticket_id)
            chosen = None
            for transition in transitions:
                transition_to = transition.get("to") or {}
                if transition_to.get("name") == status or transition.get("name") == status:
                    chosen = transition
                    break

            if chosen is None:
                return {
                    "ok": False,
                    "ticket_id": ticket_id,
                    "from_status": current_status,
                    "to_status": status,
                    "error": f"Unable to transition to '{status}'.",
                    "available_columns": [
                        (t.get("to") or {}).get("name")
                        for t in transitions
                        if (t.get("to") or {}).get("name")
                    ],
                }

            self._live_client.transition_issue(ticket_id, str(chosen.get("id", "")), comment=comment or None)
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "from_status": current_status,
                "to_status": status,
            }

        return self.update_ticket(ticket_id, status=status, comment=comment)
