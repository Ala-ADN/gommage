# Neo4j AER DAG Implementation Plan

Using Neo4j to model the Agent Execution Record (AER) as a Directed Acyclic Graph (DAG) is highly effective because it natively supports traversing data dependencies, causal chains, and evidence links. This plan outlines how to ingest traces into Neo4j and use Cypher queries to analyze both single traces and aggregated agent behaviors.

---

## 1. Environment Setup

We'll use Docker to run a local Neo4j instance and the official Python driver for ingestion.

### 1.1 Start Neo4j

Start a local Neo4j container with the APOC plugin (useful for graph algorithms):

```bash
docker run \
    --name gommage-neo4j \
    -p 7474:7474 -p 7687:7687 \
    -d \
    -v $PWD/neo4j/data:/data \
    -e NEO4J_AUTH=neo4j/gommage_secret \
    -e NEO4J_apoc_export_file_enabled=true \
    -e NEO4J_apoc_import_file_enabled=true \
    -e NEO4J_apoc_import_file_use__neo4j__config=true \
    -e NEO4JLABS_PLUGINS='["apoc"]' \
    neo4j:5.12
```

### 1.2 Python Dependencies

Add the Neo4j driver to the project:

```bash
pip install neo4j
```

---

## 2. Graph Schema Design

### Nodes
*   **`Run`**: Represents an entire trace execution.
    *   Properties: `run_id`, `ticket_id`, `agent_name`, `status`, `started_at`
*   **`Step`**: Represents an AERStep.
    *   Properties: `step_id`, `kind` (llm/tool/observation), `intent`, `latency_ms`, `error`, `timestamp`
    *   Additional labels based on kind: `:LLMStep`, `:ToolStep`, `:ObservationStep`
*   **`DataArtifact`**: Represents contextual data passed between steps (inferred from `context`, `produces`, `depends_on`).
    *   Properties: `key_name`
*   **`AggregateStep`**: (For Phase 2) Canonicalized step representing a common action across multiple runs.
    *   Properties: `canonical_signature`, `count`, `avg_latency_ms`

### Relationships
*   `(Step)-[:BELONGS_TO]->(Run)`: Links a step to its trace.
*   `(Step)-[:NEXT_STEP]->(Step)`: Represents the chronological sequence of execution.
*   `(Step)-[:PRODUCES]->(DataArtifact)`: When a tool returns data or an LLM makes a decision.
*   `(Step)-[:CONSUMES]->(DataArtifact)`: When a step uses data from the context.
*   `(Step)-[:EVIDENCE_FOR]->(Step)`: Explicit links from the `evidence_chain`.
*   `(Step)-[:DIVERGED_FROM]->(Step)`: (For Replays) Links an edited step to the original step it replaced.
*   `(AggregateStep)-[:TRANSITIONS_TO {count: int, probability: float}]->(AggregateStep)`: Flow between canonical steps across runs.

---

## 3. Phase 1: Single Trace Ingestion

Create a Python script `replay/engine/neo4j_ingester.py` to parse an `AgentExecutionRecord` and push it to Neo4j.

### 3.1 Ingestion Logic (Python)

```python
from neo4j import GraphDatabase
from recorder.serializer.aer_schema import AgentExecutionRecord

class Neo4jAERIngester:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def ingest_run(self, record: AgentExecutionRecord):
        with self.driver.session() as session:
            # 1. Create the Run node
            session.run("""
                MERGE (r:Run {run_id: $run_id})
                SET r.ticket_id = $ticket_id,
                    r.agent_name = $agent_name,
                    r.status = $status
            """, run_id=record.run_id, ticket_id=record.jira_ticket_id, 
                 agent_name=record.agent_name, status=record.status)

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
                if step.kind == "tool" and step.tool:
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
                elif step.kind == "llm" and step.llm:
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
                        source_step_id = int(evidence.source.split(":")[1])
                        session.run("""
                            MATCH (source:Step {run_id: $run_id, step_id: $source_step_id})
                            MATCH (target:Step {global_id: $curr_sid})
                            CREATE (source)-[:EVIDENCE_FOR {verdict: $verdict}]->(target)
                        """, run_id=record.run_id, source_step_id=source_step_id, 
                             curr_sid=current_step_node_id, verdict=evidence.verdict)

                prev_step_node_id = current_step_node_id
```

### 3.2 Single Trace Analysis Commands (Neo4j Browser)

Open `http://localhost:7474` and run these Cypher queries:

**Visualize the entire trace flow:**
```cypher
MATCH p=(s:Step {run_id: "run-example-id"})-[r:NEXT_STEP]->() RETURN p
```

**Visualize the evidence/reasoning DAG (ignoring strict chronology):**
```cypher
MATCH p=(s1:Step {run_id: "run-example-id"})-[r:EVIDENCE_FOR]->(s2:Step) RETURN p
```

**Find the longest sequential bottleneck (slowest subpath):**
```cypher
MATCH p=(start:Step {run_id: "run-example-id"})-[:NEXT_STEP*]->(end:Step)
WHERE NOT ()-[:NEXT_STEP]->(start) AND NOT (end)-[:NEXT_STEP]->()
WITH p, reduce(total_latency = 0, node IN nodes(p) | total_latency + coalesce(node.latency_ms, 0)) AS path_latency
RETURN [n in nodes(p) | n.step_id + ':' + coalesce(n.tool_name, n.intent)] AS path, path_latency
ORDER BY path_latency DESC LIMIT 1
```

---

## 4. Phase 2: Multi-Trace (Multiple Agents) Aggregation

To aggregate multiple agents/traces, we extract canonical signatures from steps and project them onto `AggregateStep` nodes.

### 4.1 Canonicalization Logic

We need a function in python to determine the `canonical_signature`:
*   `ToolStep`: `tool:tool_name:sorted_param_keys` (e.g., `tool:email.send:subject,to`)
*   `LLMStep`: `llm:intent_keyword` (e.g., `llm:classify_ticket`)

### 4.2 Aggregation Ingestion (Python update)

Update the ingestion script to maintain the aggregate graph concurrently:

```python
def canonicalize(step):
    if step.kind == "tool":
        param_keys = ",".join(sorted(step.tool.parameters.keys()))
        return f"tool:{step.tool.tool_name}:{param_keys}"
    elif step.kind == "llm":
        return f"llm:{step.intent.lower().replace(' ', '_')}"
    return f"other:{step.kind}"

def ingest_aggregate(self, record):
    with self.driver.session() as session:
        prev_canonical = "START"
        
        for step in record.steps:
            curr_canonical = canonicalize(step)
            
            # Upsert AggregateStep node
            session.run("""
                MERGE (agg:AggregateStep {signature: $sig})
                ON CREATE SET agg.count = 1, agg.total_latency = $latency
                ON MATCH SET agg.count = agg.count + 1, agg.total_latency = agg.total_latency + $latency
            """, sig=curr_canonical, latency=getattr(step.llm or step.tool, 'latency_ms', 0))

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
            MERGE (from_agg)-[t:TRANSITIONS_TO]->(end)
            ON CREATE SET t.count = 1
            ON MATCH SET t.count = t.count + 1
        """, from_sig=prev_canonical)
```

### 4.3 Multi-Agent Analysis Commands (Neo4j Browser)

**View the aggregated flow diagram (The "Golden Path"):**
```cypher
MATCH p=(a:AggregateStep)-[t:TRANSITIONS_TO]->(b:AggregateStep)
RETURN p
```

**Find Divergence Points (Steps where agents branch into multiple behaviors):**
```cypher
MATCH (a:AggregateStep)-[t:TRANSITIONS_TO]->(b:AggregateStep)
WITH a, count(b) AS branches, sum(t.count) as total_out
WHERE branches > 1
RETURN a.signature, branches, total_out
ORDER BY branches DESC
```

**Find error hotspots across all agents:**
```cypher
MATCH (s:ToolStep)
WHERE s.error IS NOT NULL AND s.error <> ""
WITH s.tool_name AS tool, count(s) AS error_count
RETURN tool, error_count
ORDER BY error_count DESC
```

**Calculate conditional probability of transitions (Transition Matrix):**
```cypher
MATCH (a:AggregateStep)-[t:TRANSITIONS_TO]->(b:AggregateStep)
WITH a, sum(t.count) AS total_out_edges
MATCH (a)-[t:TRANSITIONS_TO]->(b:AggregateStep)
RETURN a.signature AS From, b.signature AS To, t.count AS Traversed, 
       toFloat(t.count) / total_out_edges AS Probability
ORDER BY From, Probability DESC
```

---

## 5. Execution & Testing Workflow

1.  **Start DB:** Run the Docker command in Section 1.1.
2.  **Generate Traces:** Use your demo agent to generate JSON traces in `.gommage/traces/`.
    ```bash
    python main.py record-demo --ticket DEMO-101
    python main.py record-demo --ticket DEMO-102
    ```
3.  **Ingest Data:** Run the `Neo4jAERIngester` python script to load the `.json` files into Neo4j.
4.  **Inspect:** Open `http://localhost:7474`, login with `neo4j` / `gommage_secret`.
5.  **Run Cypher:** Paste the analysis queries from Section 3.2 and 4.3 into the prompt to visualize the graph. In Neo4j Browser, clicking a node will allow you to see the exact payloads (prompt, context, tool inputs) stored as properties.
