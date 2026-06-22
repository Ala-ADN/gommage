"""Replay fidelity metric."""

from __future__ import annotations

from replay.engine.replay_runner import ReplayResult


def replay_fidelity_score(result: ReplayResult) -> float:
    if not result.replayed_steps:
        return 1.0
    matches = sum(1 for step in result.replayed_steps if step.input_matches_original)
    return matches / len(result.replayed_steps)
