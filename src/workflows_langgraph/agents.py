"""Direct OpenAI Responses clients for Foundry agents.

Both prompt-style and hosted (containerized) agents in Azure AI Foundry
expose an OpenAI-compatible Responses endpoint. We bypass
``langchain-azure-ai`` entirely and call them ourselves so the graph stays
lean and the failure modes are easy to reason about.

Endpoint shapes:

* **Prompt agent** (project-level Responses):
  ``POST {project}/openai/v1/responses?api-version=v1``
  body includes ``extra_body={"agent_reference": {"name": ..., "type": "agent_reference"}}``.

* **Hosted agent** (per-agent endpoint):
  ``POST {project}/agents/{name}/endpoint/protocols/openai/responses?api-version=v1``
  with header ``Foundry-Features: HostedAgents=V1Preview``.
  ``model`` is the agent name; **no** ``agent_reference`` body.

Both are authenticated with a Bearer token from
``DefaultAzureCredential`` (scope ``https://ai.azure.com/.default``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableLambda
from openai import APIStatusError, OpenAI


_logger = logging.getLogger(__name__)

_TOKEN_SCOPE = "https://ai.azure.com/.default"
_DEFAULT_API_VERSION = "v1"
_HOSTED_FEATURE_HEADER = {"Foundry-Features": "HostedAgents=V1Preview"}


def _make_credential(use_managed_identity: bool = False) -> TokenCredential:
    return DefaultAzureCredential(
        exclude_managed_identity_credential=not use_managed_identity,
    )


# TODO(memory): switch to server-side Foundry conversations once the
# per-agent (hosted) endpoints accept project-scoped conversation IDs.
# Today, ``conversations.create()`` against the project endpoint returns a
# conversation that prompt agents can join, but the hosted-agent endpoint
# returns 404 for the same ID. Until the platform unifies that, we rely on
# LangGraph's checkpointer + full message history per request (below).
# Tracking issue / docs: https://aka.ms/foundry/agents/conversations


def _message_to_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if t:
                    parts.append(t)
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content or "")


def _messages_as_input(state: Any) -> list[dict]:
    """Convert graph state messages into an OpenAI Responses ``input`` list.

    Maps LangChain ``HumanMessage`` / ``AIMessage`` to ``{"role": ..., "content": ...}``
    so each agent receives the full conversation context (memory across the
    graph) in a way both prompt and hosted Foundry endpoints accept.
    """
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if isinstance(messages, BaseMessage):
        messages = [messages]
    out: list[dict] = []
    for msg in messages:
        text = _message_to_text(msg)
        if not text:
            continue
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        else:
            continue  # skip system / tool / unknown
        out.append({"role": role, "content": text})
    if not out:
        raise ValueError("No usable messages found in graph state.")
    return out


def _output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    # Fallback for shapes where output_text isn't populated.
    output = getattr(response, "output", None) or []
    parts: list[str] = []
    for item in output:
        for block in getattr(item, "content", None) or []:
            t = getattr(block, "text", None)
            if t:
                parts.append(t)
    return "".join(parts)


def _call_with_cold_start_retry(
    fn,
    *,
    name: str,
    max_retries: int,
    backoff: float,
):
    """Retry on hosted-agent ``424 session_not_ready`` cold-starts."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except APIStatusError as exc:
            if exc.status_code == 424 and exc.code == "session_not_ready":
                last_exc = exc
                _logger.info(
                    "Agent '%s' cold start (attempt %d/%d)",
                    name,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(backoff * (attempt + 1))
                continue
            raise
    assert last_exc is not None
    raise last_exc


def prompt_agent_node(
    *,
    project_endpoint: str,
    name: str,
    credential: Optional[TokenCredential] = None,
) -> Runnable:
    """LangGraph runnable for a Foundry **prompt** agent.

    Calls the project-level OpenAI Responses endpoint and references the
    agent by name via ``extra_body.agent_reference``. ``model`` is omitted
    deliberately — Foundry rejects the call when ``model`` is specified
    alongside an ``agent_reference`` unless it matches the agent's model
    exactly.

    The full conversation history from graph state is sent as ``input``,
    so the agent has memory across turns when the graph runs with a
    checkpointer (e.g. SQLite) keyed on a stable ``thread_id``.
    """
    project_endpoint = project_endpoint.rstrip("/")
    creds = credential or _make_credential()
    token_provider = get_bearer_token_provider(creds, _TOKEN_SCOPE)
    client = OpenAI(
        base_url=f"{project_endpoint}/openai/v1",
        api_key="placeholder",
    )

    def _invoke(state) -> dict:
        response = client.with_options(
            default_headers={"Authorization": f"Bearer {token_provider()}"},
        ).responses.create(
            input=_messages_as_input(state),
            extra_body={
                "agent_reference": {"name": name, "type": "agent_reference"}
            },
        )
        return {"messages": [AIMessage(content=_output_text(response), name=name)]}

    async def _ainvoke(state) -> dict:
        return await asyncio.to_thread(_invoke, state)

    return RunnableLambda(_invoke, afunc=_ainvoke, name=name)


def hosted_agent_node(
    *,
    project_endpoint: str,
    name: str,
    version: Optional[str] = None,
    credential: Optional[TokenCredential] = None,
    api_version: str = _DEFAULT_API_VERSION,
    cold_start_max_retries: int = 4,
    cold_start_backoff_seconds: float = 2.0,
) -> Runnable:
    """LangGraph runnable for a Foundry **hosted (containerized)** agent.

    Calls the per-agent endpoint with the ``Foundry-Features`` header and
    retries the typical cold-start ``424 session_not_ready`` response.
    Sends the full conversation history from graph state so the agent
    has memory across turns when the graph runs with a checkpointer.

    Parameters
    ----------
    version:
        Optional pinned agent version (e.g. ``"2"``). When set, the
        ``model`` field is sent as ``"{name}:{version}"``; otherwise the
        endpoint resolves to the currently-active version.
    """
    project_endpoint = project_endpoint.rstrip("/")
    creds = credential or _make_credential()
    token_provider = get_bearer_token_provider(creds, _TOKEN_SCOPE)
    base_url = f"{project_endpoint}/agents/{name}/endpoint/protocols/openai"
    hosted_client = OpenAI(
        base_url=base_url,
        api_key="placeholder",
        default_query={"api-version": api_version},
    )
    model = f"{name}:{version}" if version else name

    def _invoke(state) -> dict:
        input_messages = _messages_as_input(state)

        def _do():
            return hosted_client.with_options(
                default_headers={
                    "Authorization": f"Bearer {token_provider()}",
                    **_HOSTED_FEATURE_HEADER,
                },
            ).responses.create(model=model, input=input_messages)

        response = _call_with_cold_start_retry(
            _do,
            name=name,
            max_retries=cold_start_max_retries,
            backoff=cold_start_backoff_seconds,
        )
        return {"messages": [AIMessage(content=_output_text(response), name=name)]}

    async def _ainvoke(state) -> dict:
        return await asyncio.to_thread(_invoke, state)

    return RunnableLambda(_invoke, afunc=_ainvoke, name=name)
