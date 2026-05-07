# TripMate AI — LangGraph workflow

This is an alternative orchestration of the same TripMate agents (travel
concierge, trip-scout, booking-manager) implemented as a **LangGraph** graph.
It runs **locally** while delegating actual agent execution to the agents that
are deployed in your Foundry project, using
[`langchain-azure-ai`](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-agents).

The original Foundry workflow ([tripmate.yaml](../workflows/tripmate.yaml)) is
unchanged and still deployed by `azd up`; this is an additional, optional way
to drive the same agents.

## Architecture

```
                ┌──────────────────────┐
   user ───────▶│ travel-concierge     │  (Foundry prompt agent, JSON router)
                └─────────┬────────────┘
                          │
                  classify (local)
                          │
        ┌─────────────────┼──────────────────────┐
        ▼                 ▼                      ▼
  ┌───────────┐   ┌──────────────────┐         END
  │ trip-scout│   │ booking-manager  │  (Foundry hosted agents)
  └───────────┘   └──────────────────┘
```

* `concierge`, `trip_scout` and `booking_manager` are LangGraph nodes produced
  by `AgentServiceFactory.get_agent_node(...)` — they execute server-side in
  Foundry Agent Service.
* `classify` is a small **local** node that parses the concierge's structured
  JSON output and decides which specialist to invoke.

## Prerequisites

1. Run `azd up` first so all four Foundry agents (`travel-concierge`,
   `trip-scout`, `booking-manager`, `tripmate`) are deployed.
2. Make sure the generated `.env` is at the repo root (the `azd` postdeploy
   hook copies it for you). At minimum the workflow needs:

   ```env
   AZURE_AI_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
   ```

3. Be signed in with `az login` so `DefaultAzureCredential` can authenticate.
4. Python 3.10+.

## Install dependencies

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/workflows_langgraph/requirements.txt
```

## Run from the CLI

```bash
python -m workflows_langgraph.tripmate "Plan a weekend in Barcelona in June"
# (run from inside ./src so the package is importable)
```

Or directly:

```bash
python src/workflows_langgraph/tripmate.py "Book the Hilton and the morning Lufthansa flight"
```

## Run in LangGraph Studio (local UI)

The repository ships a `langgraph.json` at the root that registers this
workflow under the name `tripmate`. Start LangGraph Studio's local dev server
with:

```bash
langgraph dev
```

This launches an in-memory LangGraph server and opens LangGraph Studio in your
browser, where you can:

* visualize the graph,
* send messages to the `tripmate` graph and watch each node execute,
* inspect intermediate state (including the parsed `next_agent` decision and
  the `azure_ai_agents_conversation_id` returned by the Foundry nodes),
* time-travel and edit state for debugging.

> Foundry agent execution still happens server-side; only the orchestration
> graph runs locally.

## Tracing

To send LangGraph traces to the same Application Insights resource that the
Foundry project uses, wrap the graph with the OpenTelemetry tracer:

```python
from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer
from workflows_langgraph.tripmate import graph

tracer = AzureAIOpenTelemetryTracer(agent_id="tripmate-langgraph")
graph = graph.with_config({"callbacks": [tracer]})
```
