"""Ingester for pushing AER traces into Neo4j for DAG visualization."""

from __future__ import annotations

from typing import Any
from neo4j import GraphDatabase

from recorder.serializer.aer_schema import AgentExecutionRecord, AERStep


def canonicalize(step: AERStep) -> str:
    """Extract a canonical signature from a step for multi-trace aggregation."""
    if step.kind == "tool" and step.tool is not None:
        param_keys = ",".join(sorted(step.tool.parameters.keys()))
        return f"tool:{step.tool.tool_name}:{param_keys}"
    elif step.kind == "llm":
        return f"llm:{step.intent.lower().replace(' ', '_')}"
    return f"other:{step.kind}"


class Neo4jAERIngester:
    def __init__(self, uri: str = "neo4j://localhost:7687", user: str = "neo4j", password: str = "gommage_secret") -> None:
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def ingest_run(self, record: AgentExecutionRecord) -> None:
        """Ingests a single AER run into Neo4j."""
        with self.driver.session() as session:
            # 1. Create the Run node
            session.run("""
                MERGE (r:Run {run_id: $run_id})
                SET r.ticket_id = $ticket_id,
                    r.agent_name = $agent_name,
                    r.status = $status,
                    r.started_at = $started_at
            """, run_id=record.run_id, ticket_id=record.jira_ticket_id, 
                 agent_name=record.agent_name, status=record.status, started_at=record.started_at)

            prev_step_node_id = None

            # 2. Iterate through steps to create Step nodes and NEXT_STEP edges
            for step in record.steps:
                # Basic step creation
                result = session.run("""
                    MATCH (r:Run {run_id: $run_id})
                    CREATE (s:Step {
                        global_id: $run_id + '_' + toString($step_id),
                        step_id: $step_id,
                        kind: $kind,
                        intent: $intent,
                        run_id: $run_id
                    })-[:BELONGS_TO]->(r)
                    RETURN s.global_id AS sid
                """, run_id=record.run_id, step_id=step.step_id, 
                     kind=step.kind, intent=step.intent or "")
                
                current_step_node_id = result.single()["sid"]

                # Add specific labels and properties
                if step.kind == "tool" and step.tool is not None:
                    session.run("""
                        MATCH (s:Step {global_id: $sid})
                        SET s:ToolStep,
                            s.tool_name = $tool_name,
                            s.side_effecting = $side_effecting,
                            s.error = $error,
                            s.latency_ms = $latency
                    """, sid=current_step_node_id, tool_name=step.tool.tool_name, 
                         side_effecting=step.tool.side_effecting, error=step.tool.error or "",
                         latency=step.tool.latency_ms)
                elif step.kind == "llm" and step.llm is not None:
                    session.run("""
                        MATCH (s:Step {global_id: $sid})
                        SET s:LLMStep, s.latency_ms = $latency
                    """, sid=current_step_node_id, latency=step.llm.latency_ms)

                # Chronological edge
                if prev_step_node_id:
                    session.run("""
                        MATCH (prev:Step {global_id: $prev_sid})
                        MATCH (curr:Step {global_id: $curr_sid})
                        CREATE (prev)-[:NEXT_STEP]->(curr)
                    """, prev_sid=prev_step_node_id, curr_sid=current_step_node_id)

                # Evidence links (Data Dependency DAG)
                for evidence in step.evidence:
                    if evidence.source.startswith("step:"):
                        try:
                            source_step_id = int(evidence.source.split(":")[1])
                            session.run("""
                                MATCH (source:Step {run_id: $run_id, step_id: $source_step_id})
                                MATCH (target:Step {global_id: $curr_sid})
                                CREATE (source)-[:EVIDENCE_FOR {verdict: $verdict}]->(target)
                            """, run_id=record.run_id, source_step_id=source_step_id, 
                                 curr_sid=current_step_node_id, verdict=evidence.verdict)
                        except ValueError:
                            pass # Skip if step id is not an integer

                # Data Artifacts
                # CONSUMES from context
                for key in step.context.keys():
                    session.run("""
                        MATCH (s:Step {global_id: $sid})
                        MERGE (d:DataArtifact {key_name: $key, run_id: $run_id})
                        MERGE (s)-[:CONSUMES]->(d)
                    """, sid=current_step_node_id, key=key, run_id=record.run_id)

                # PRODUCES
                if step.kind == "tool" and step.tool is not None:
                    session.run("""
                        MATCH (s:Step {global_id: $sid})
                        MERGE (d:DataArtifact {key_name: $key, run_id: $run_id})
                        MERGE (s)-[:PRODUCES]->(d)
                    """, sid=current_step_node_id, key=f"tool_result:{step.tool.tool_name}", run_id=record.run_id)
                elif step.kind == "llm" and step.llm is not None:
                    session.run("""
                        MATCH (s:Step {global_id: $sid})
                        MERGE (d:DataArtifact {key_name: $key, run_id: $run_id})
                        MERGE (s)-[:PRODUCES]->(d)
                    """, sid=current_step_node_id, key=f"llm_decision:{step.intent}", run_id=record.run_id)

                prev_step_node_id = current_step_node_id

    def ingest_aggregate(self, record: AgentExecutionRecord) -> None:
        """Ingests canonical signatures into an aggregated behavior DAG."""
        with self.driver.session() as session:
            # Ensure a START node exists
            session.run("""
                MERGE (start:AggregateStep {signature: 'START'})
                ON CREATE SET start.count = 0, start.total_latency = 0
            """)

            prev_canonical = "START"
            
            for step in record.steps:
                curr_canonical = canonicalize(step)
                latency = 0
                if step.kind == "tool" and step.tool is not None:
                    latency = step.tool.latency_ms
                elif step.kind == "llm" and step.llm is not None:
                    latency = step.llm.latency_ms
                
                # Upsert AggregateStep node
                session.run("""
                    MERGE (agg:AggregateStep {signature: $sig})
                    ON CREATE SET agg.count = 1, agg.total_latency = $latency, agg.label = $label
                    ON MATCH SET agg.count = agg.count + 1, agg.total_latency = agg.total_latency + $latency
                """, sig=curr_canonical, latency=latency, label=curr_canonical)

                # Upsert TRANSITIONS_TO edge
                session.run("""
                    MATCH (from_agg:AggregateStep {signature: $from_sig})
                    MATCH (to_agg:AggregateStep {signature: $to_sig})
                    MERGE (from_agg)-[t:TRANSITIONS_TO]->(to_agg)
                    ON CREATE SET t.count = 1
                    ON MATCH SET t.count = t.count + 1
                """, from_sig=prev_canonical, to_sig=curr_canonical)

                prev_canonical = curr_canonical
                
            # End node connection
            session.run("""
                MATCH (from_agg:AggregateStep {signature: $from_sig})
                MERGE (end:AggregateStep {signature: 'END'})
                ON CREATE SET end.count = 1, end.total_latency = 0, end.label = 'END'
                ON MATCH SET end.count = end.count + 1
                MERGE (from_agg)-[t:TRANSITIONS_TO]->(end)
                ON CREATE SET t.count = 1
                ON MATCH SET t.count = t.count + 1
            """, from_sig=prev_canonical)
