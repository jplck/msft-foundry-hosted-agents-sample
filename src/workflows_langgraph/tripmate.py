"""TripMate AI — LangGraph workflow.

Orchestrates the Foundry-hosted and prompt-based agents using LangGraph::

    user → travel-concierge (prompt agent, JSON router)
              ├── next_agent == "trip-scout"       → trip-scout (hosted agent)
              ├── next_agent == "booking-manager"  → booking-manager (hosted agent)
              └── next_agent == "none"             → end (concierge already replied)

Agents are called directly via the OpenAI Responses API exposed by the
Foundry project — no Azure SDK abstractions, no ``langchain-azure-ai``
factory. See :mod:`agents` for the prompt vs hosted endpoint shapes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Literal

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

# ``langgraph dev`` loads this file by path (not as part of its package), so
# fall back to an absolute import when the relative one fails.
try:
    from .agents import hosted_agent_node, prompt_agent_node
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agents import hosted_agent_node, prompt_agent_node  # type: ignore[no-redef]


_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

PROJECT_ENDPOINT = (
    os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    or os.environ.get("PROJECT_ENDPOINT")
)
if not PROJECT_ENDPOINT:
    raise RuntimeError(
        "AZURE_AI_PROJECT_ENDPOINT (or FOUNDRY_PROJECT_ENDPOINT) is not set. "
        "Run `azd up` first or copy the generated .env to the repo root."
    )

# Skip the IMDS managed-identity probe locally to avoid the 169.254.169.254
# noise in DefaultAzureCredential. Set FOUNDRY_USE_MANAGED_IDENTITY=1 on
# Azure compute to opt back in.
_use_mi = os.environ.get("FOUNDRY_USE_MANAGED_IDENTITY", "").lower() in (
    "1",
    "true",
    "yes",
)
_credential = DefaultAzureCredential(exclude_managed_identity_credential=not _use_mi)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TripMateState(MessagesState):
    """Graph state.

    * ``messages`` (inherited) — full LangGraph chat history. Persisted
      via the SQLite checkpointer so each thread retains short-term
      memory across turns; every agent call resends the full history
      as ``input`` so all agents share the same context.
    * ``next_agent`` — routing decision parsed from the concierge.
    """

    next_agent: str | None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

concierge_node = prompt_agent_node(
    project_endpoint=PROJECT_ENDPOINT,
    name="travel-concierge",
    credential=_credential,
)
trip_scout_node = hosted_agent_node(
    project_endpoint=PROJECT_ENDPOINT,
    name="trip-scout",
    credential=_credential,
    version="2",
)
booking_manager_node = hosted_agent_node(
    project_endpoint=PROJECT_ENDPOINT,
    name="booking-manager",
    credential=_credential,
)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(text)
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content or "")


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return _message_text(msg)
    return ""


def _parse_concierge_decision(text: str) -> str:
    """Extract ``next_agent`` from the concierge's JSON output."""
    if not text:
        return "none"

    candidates: list[str] = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        next_agent = payload.get("next_agent")
        if isinstance(next_agent, str):
            return next_agent

    return "none"


def classify(state: TripMateState) -> dict:
    """Inspect the concierge response and decide the next hop."""
    decision = _parse_concierge_decision(_last_ai_text(state["messages"]))
    return {"next_agent": decision}


def _route(
    state: TripMateState,
) -> Literal["trip_scout", "booking_manager", "__end__"]:
    decision = (state.get("next_agent") or "none").strip().lower()
    if decision == "trip-scout":
        return "trip_scout"
    if decision == "booking-manager":
        return "booking_manager"
    return END


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


# Short-term memory: a SQLite checkpointer keyed on ``thread_id`` so each
# chat thread retains its message history across graph invocations. The DB
# file is configurable; default lives next to this module.
#
# When ``langgraph dev`` runs the graph, it injects its own platform
# checkpointer and ignores any compiled-in one — so the SQLite checkpointer
# only kicks in for direct ``graph.invoke(..., config={"configurable": {"thread_id": ...}})``
# calls (CLI / scripts / your own host).
SQLITE_PATH = Path(
    os.environ.get(
        "TRIPMATE_SQLITE_PATH",
        str(_REPO_ROOT / ".langgraph_sqlite" / "tripmate.sqlite"),
    )
)


def _make_checkpointer():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "langgraph-checkpoint-sqlite is required for SQLite memory. "
            "Install it: pip install langgraph-checkpoint-sqlite"
        ) from exc

    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    conn = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
    return SqliteSaver(conn)


def build_graph(*, checkpointer=None):
    workflow = StateGraph(TripMateState)

    workflow.add_node("concierge", concierge_node)
    workflow.add_node("classify", classify)
    workflow.add_node("trip_scout", trip_scout_node)
    workflow.add_node("booking_manager", booking_manager_node)

    workflow.add_edge(START, "concierge")
    workflow.add_edge("concierge", "classify")
    workflow.add_conditional_edges(
        "classify",
        _route,
        {
            "trip_scout": "trip_scout",
            "booking_manager": "booking_manager",
            END: END,
        },
    )
    workflow.add_edge("trip_scout", END)
    workflow.add_edge("booking_manager", END)

    return workflow.compile(checkpointer=checkpointer)


# Default exported graph: no compiled-in checkpointer so ``langgraph dev``
# can attach its own. The CLI block below builds a separate instance with
# the SQLite checkpointer for stand-alone runs.
graph = build_graph()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the TripMate LangGraph workflow.")
    parser.add_argument(
        "query",
        nargs="?",
        default="I want to plan a weekend trip to Barcelona in June.",
    )
    parser.add_argument(
        "--thread-id",
        default="cli",
        help=(
            "Thread ID used to look up / persist short-term memory in the "
            "SQLite checkpointer. Reuse the same value across invocations "
            "to continue a conversation."
        ),
    )
    args = parser.parse_args()

    cli_graph = build_graph(checkpointer=_make_checkpointer())
    config = {"configurable": {"thread_id": args.thread_id}}

    result = cli_graph.invoke(
        {"messages": [HumanMessage(content=args.query)]},
        config=config,
    )
    for message in result["messages"]:
        role = getattr(message, "type", "?")
        name = getattr(message, "name", None) or ""
        prefix = f"{role}" + (f" [{name}]" if name else "")
        print(f"--- {prefix} ---")
        print(_message_text(message))
    print(f"\nrouted to: {result.get('next_agent')!r}")
    print(f"thread_id: {args.thread_id!r}  (sqlite: {SQLITE_PATH})")
