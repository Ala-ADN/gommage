from __future__ import annotations

import argparse

from recorder.env_loader import load_local_env

load_local_env()

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
    record_parser.add_argument("--agent-mode")
    record_parser.add_argument("--tool-mode")
    record_parser.add_argument("--write-policy")
    record_parser.add_argument("--external-messages")
    record_parser.add_argument("--max-steps", type=int)

    replay_parser = subparsers.add_parser("replay", help="replay a stored run")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--store", default=".gommage/traces")

    subparsers.add_parser("eval", help="run synthetic evaluation")

    ui_parser = subparsers.add_parser("ui", help="start the local browser UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", default=8010, type=int)
    ui_parser.add_argument("--store", default=".gommage/traces")

    ingest_parser = subparsers.add_parser("ingest-neo4j", help="ingest traces into neo4j")
    ingest_parser.add_argument("--store", default=".gommage/traces")
    ingest_parser.add_argument("--uri", default="neo4j://localhost:7687")
    ingest_parser.add_argument("--user", default="neo4j")
    ingest_parser.add_argument("--password", default="gommage_secret")

    args = parser.parse_args()

    if args.command == "record-demo":
        record = run_jira_triage(
            args.ticket,
            agent_mode=args.agent_mode,
            tool_mode=args.tool_mode,
            write_policy=args.write_policy,
            external_messages=args.external_messages,
            max_steps=args.max_steps,
        )
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

    if args.command == "ingest-neo4j":
        from replay.engine.neo4j_ingester import Neo4jAERIngester
        ingester = Neo4jAERIngester(uri=args.uri, user=args.user, password=args.password)
        try:
            store = LocalTraceStore(args.store)
            traces = store.list_run_ids()
            for run_id in traces:
                record = store.load(run_id)
                ingester.ingest_run(record)
                ingester.ingest_aggregate(record)
                print(f"Ingested run {run_id}")
        finally:
            ingester.close()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
