"""Local browser UI for recording and replaying Gommage traces."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from recorder.env_loader import load_local_env

load_local_env()

from agent.jira_triage_agent import run_jira_triage
from evaluation.metrics.mock_recall import mock_recall_rate
from evaluation.metrics.replay_fidelity import replay_fidelity_score
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.storage.local_store import LocalTraceStore
from replay.engine.replay_runner import ReplayRunner
from replay.engine.step_editor import StepEdit
from replay.ui.fix_issue_brief import generate_fix_issue_brief


STATIC_ROOT = Path(__file__).with_name("web")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _summary(record: AgentExecutionRecord) -> dict[str, Any]:
    side_effecting = sum(
        1 for step in record.steps if step.tool is not None and step.tool.side_effecting
    )
    return {
        "run_id": record.run_id,
        "ticket_id": record.jira_ticket_id,
        "agent_name": record.agent_name,
        "status": record.status,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "steps": len(record.steps),
        "side_effecting_tools": side_effecting,
    }


class GommageUIServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        store: LocalTraceStore,
        static_root: Path = STATIC_ROOT,
    ) -> None:
        super().__init__(server_address, GommageUIHandler)
        self.store = store
        self.static_root = static_root


class GommageUIHandler(BaseHTTPRequestHandler):
    server: GommageUIServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/runs":
            query = parse_qs(parsed.query)
            ticket_id = query.get("ticket_id", [None])[0]
            self._send_json(self._list_runs(ticket_id=ticket_id))
            return
        if path.startswith("/api/runs/"):
            run_id = unquote(path.rsplit("/", 1)[-1])
            record = self.server.store.load(run_id)
            self._send_json({"record": record.to_dict(), "summary": _summary(record)})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        if parsed.path == "/api/record":
            ticket_id = str(payload.get("ticket_id") or "DEMO-101")
            record = run_jira_triage(
                ticket_id,
                issue=payload.get("issue"),
                llm_backend=str(payload.get("llm_backend") or "auto"),
                agent_mode=payload.get("agent_mode"),
                tool_mode=payload.get("tool_mode"),
                write_policy=payload.get("write_policy"),
                external_messages=payload.get("external_messages"),
                max_steps=payload.get("max_steps"),
                system_prompt=payload.get("system_prompt"),
            )
            self.server.store.save(record)
            self._send_json({"record": record.to_dict(), "summary": _summary(record)})
            return
        if parsed.path == "/api/replay":
            run_id = str(payload["run_id"])
            record = self.server.store.load(run_id)
            edits = _step_edits_from_payload(payload)
            result = ReplayRunner(record).replay(edits)
            self._send_json(
                {
                    "result": _jsonable(result),
                    "metrics": {
                        "replay_fidelity": replay_fidelity_score(result),
                        "mock_recall": mock_recall_rate(record, result),
                        "side_effects_blocked": result.side_effects_blocked,
                    },
                }
            )
            return
        if parsed.path == "/api/fix-brief":
            run_id = str(payload["run_id"])
            record = self.server.store.load(run_id)
            edits = _step_edits_from_payload(payload)
            replay_metrics = payload.get("replay_metrics")
            if replay_metrics is None:
                result = ReplayRunner(record).replay(edits)
                replay_metrics = {
                    "replay_fidelity": replay_fidelity_score(result),
                    "mock_recall": mock_recall_rate(record, result),
                    "side_effects_blocked": result.side_effects_blocked,
                }
            brief = generate_fix_issue_brief(
                record,
                replay_metrics=replay_metrics,
                edits=payload.get("edits", []),
                summary_hint=payload.get("summary"),
            )
            self._send_json({"brief": brief})
            return
        if parsed.path == "/api/fix-issue":
            run_id = str(payload["run_id"])
            record = self.server.store.load(run_id)
            fix_root = Path(".gommage/fix_issues")
            fix_root.mkdir(parents=True, exist_ok=True)
            fix_id = f"LOCAL-FIX-{len(list(fix_root.glob('*.json'))) + 1}"
            edits = _step_edits_from_payload(payload)
            result = ReplayRunner(record).replay(edits)
            replay_metrics = payload.get("replay_metrics") or {
                "replay_fidelity": replay_fidelity_score(result),
                "mock_recall": mock_recall_rate(record, result),
                "side_effects_blocked": result.side_effects_blocked,
            }
            brief = generate_fix_issue_brief(
                record,
                replay_metrics=replay_metrics,
                edits=payload.get("edits", []),
                summary_hint=payload.get("summary"),
            )
            fix_payload = {
                "fix_id": fix_id,
                "linked_ticket": record.jira_ticket_id,
                "run_id": run_id,
                "summary": brief["summary"],
                "description_paragraphs": brief["description_paragraphs"],
                "comment_paragraphs": brief["comment_paragraphs"],
                "recommended_prompt_change": brief["recommended_prompt_change"],
                "brief_source": brief["source"],
                "trace_hash": record.trace_hash(),
                "evidence": {
                    "steps": len(record.steps),
                    "side_effecting_tools": [
                        step.tool.tool_name
                        for step in record.steps
                        if step.tool is not None and step.tool.side_effecting
                    ],
                },
            }
            path = fix_root / f"{fix_id}.json"
            path.write_text(json.dumps(fix_payload, indent=2, sort_keys=True), encoding="utf-8")
            self._send_json({"fix_issue": fix_payload, "path": str(path)})
            return
        self.send_error(404, "Not found")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} - {format % args}")

    def _list_runs(self, *, ticket_id: str | None = None) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for run_id in self.server.store.list_run_ids():
            try:
                record = self.server.store.load(run_id)
                if ticket_id and record.jira_ticket_id != ticket_id:
                    continue
                runs.append(_summary(record))
            except Exception as exc:  # noqa: BLE001 - one bad trace should not break UI.
                runs.append({"run_id": run_id, "error": str(exc)})
        runs.sort(key=lambda item: item.get("started_at") or "", reverse=True)
        return {"runs": runs}

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (self.server.static_root / relative).resolve()
        static_root = self.server.static_root.resolve()
        if static_root not in path.parents and path != static_root:
            self.send_error(403, "Forbidden")
            return
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(_jsonable(payload), indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _step_edits_from_payload(payload: dict[str, Any]) -> list[StepEdit]:
    return [
        StepEdit(
            step_id=int(item["step_id"]),
            prompt=item.get("prompt"),
            tool_result=item.get("tool_result"),
            note=item.get("note", ""),
        )
        for item in payload.get("edits", [])
    ]


def run_ui_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8010,
    store_root: str | Path = ".gommage/traces",
) -> None:
    store = LocalTraceStore(store_root)
    server = GommageUIServer((host, port), store)
    print(f"Gommage UI running at http://{host}:{port}")
    server.serve_forever()
