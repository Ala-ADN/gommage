"""Run the synthetic Gommage evaluation suite."""

from __future__ import annotations

from dataclasses import dataclass

from agent.confluence_audit_agent import run_confluence_audit
from agent.jira_triage_agent import run_jira_triage
from evaluation.metrics.mock_recall import mock_recall_rate
from evaluation.metrics.replay_fidelity import replay_fidelity_score
from replay.engine.replay_runner import ReplayRunner


@dataclass(slots=True)
class EvaluationResult:
    scenario: str
    replay_fidelity: float
    mock_recall: float
    side_effects_blocked: int


def run_evaluation() -> list[EvaluationResult]:
    records = {
        "scenario_a_side_effect": run_confluence_audit(),
        "scenario_b_triage": run_jira_triage(),
    }
    results: list[EvaluationResult] = []
    for name, record in records.items():
        replay = ReplayRunner(record).replay()
        results.append(
            EvaluationResult(
                scenario=name,
                replay_fidelity=replay_fidelity_score(replay),
                mock_recall=mock_recall_rate(record, replay),
                side_effects_blocked=replay.side_effects_blocked,
            )
        )
    return results


if __name__ == "__main__":
    for result in run_evaluation():
        print(
            f"{result.scenario}: "
            f"RFS={result.replay_fidelity:.2f} "
            f"MRR={result.mock_recall:.2f} "
            f"blocked={result.side_effects_blocked}"
        )
