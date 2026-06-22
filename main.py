from __future__ import annotations

import argparse

from agent.jira_triage_agent import run_jira_triage
from evaluation.eval_runner import run_evaluation
from recorder.storage.local_store import LocalTraceStore
from replay.engine.replay_runner import ReplayRunner
from replay.ui.server import run_ui_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Gommage demo CLI")
    subparsers = parser.add_subparsers(dest="command")

    record_parser = subparsers.add_parser("record-demo", help="record a demo Jira run")
    record_parser.add_argument("--ticket", default="DEMO-101")
    record_parser.add_argument("--store", default=".gommage/traces")

    replay_parser = subparsers.add_parser("replay", help="replay a stored run")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--store", default=".gommage/traces")

    subparsers.add_parser("eval", help="run synthetic evaluation")

    ui_parser = subparsers.add_parser("ui", help="start the local browser UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", default=8010, type=int)
    ui_parser.add_argument("--store", default=".gommage/traces")

    args = parser.parse_args()

    if args.command == "record-demo":
        record = run_jira_triage(args.ticket)
        path = LocalTraceStore(args.store).save(record)
        print(f"recorded {record.run_id} to {path}")
        return

    if args.command == "replay":
        record = LocalTraceStore(args.store).load(args.run_id)
        result = ReplayRunner(record).replay()
        print(
            f"replayed {len(result.replayed_steps)} steps; "
            f"blocked {result.side_effects_blocked} side effects"
        )
        return

    if args.command == "eval":
        for result in run_evaluation():
            print(
                f"{result.scenario}: "
                f"RFS={result.replay_fidelity:.2f} "
                f"MRR={result.mock_recall:.2f} "
                f"blocked={result.side_effects_blocked}"
            )
        return

    if args.command == "ui":
        run_ui_server(host=args.host, port=args.port, store_root=args.store)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
