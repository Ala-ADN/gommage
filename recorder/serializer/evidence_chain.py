"""Small evidence-chain helpers for recorded agent steps."""

from __future__ import annotations

from typing import Any

from recorder.serializer.aer_schema import AERStep, EvidenceLink


class EvidenceChainBuilder:
    def __init__(self) -> None:
        self._links: list[EvidenceLink] = []

    def add(
        self,
        *,
        source: str,
        reference: str,
        verdict: str = "observed",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "EvidenceChainBuilder":
        self._links.append(
            EvidenceLink(
                source=source,
                reference=reference,
                verdict=verdict,
                confidence=confidence,
                metadata=metadata or {},
            )
        )
        return self

    def extend_step(self, step: AERStep) -> AERStep:
        step.evidence.extend(self._links)
        return step

    def build(self) -> list[EvidenceLink]:
        return list(self._links)


def verdict_summary(step: AERStep) -> dict[str, int]:
    summary: dict[str, int] = {}
    for link in step.evidence:
        summary[link.verdict] = summary.get(link.verdict, 0) + 1
    return summary
