# Local SDK patch — hosted-agent support for `langchain-azure-ai`

> **TL;DR** — `AgentServiceFactory.get_agent_node` in
> [`langchain-azure-ai==1.2.3`](https://pypi.org/project/langchain-azure-ai/)
> only works for **prompt agents**. Foundry **hosted (containerized) agents**
> need a different endpoint and reject the factory's calls. We work around
> this with a small in-repo monkey-patch that auto-detects hosted agents and
> dispatches them to the per-agent endpoint required by the hosted-agent
> preview. Once the upstream SDK adds native support, the patch can be
> deleted.

The patch lives in [`_hosted_agent_patch.py`](./_hosted_agent_patch.py) and
is applied at import time from [`tripmate.py`](./tripmate.py):

```python
from . import _hosted_agent_patch
_hosted_agent_patch.patch_agent_service_factory()
```

After that, every call site keeps using the standard factory API:

```python
factory = AgentServiceFactory(project_endpoint=..., credential=...)
node = factory.get_agent_node(name="trip-scout", version="1")  # hosted ✅
node = factory.get_agent_node(name="travel-concierge")          # prompt ✅
```

## Why the patch is needed

`langchain-azure-ai==1.2.3` builds a `ResponsesAgentNode` that always sends
requests to the **project-level** endpoint:

```
POST {project}/openai/v1/responses
{
  "input": "...",
  "extra_body": {"agent_reference": {"name": "...", "type": "agent_reference"}}
}
```

Hosted agents reject this with:

```
400 BadRequest — Hosted agents can only be called through the agent endpoint:
https://<acct>.services.ai.azure.com/api/projects/<proj>/agents/<agentName>/endpoint/protocols/openai/responses
```

There is no preview/RC version of `langchain-azure-ai` that supports hosted
agents at the time of writing (latest = 1.2.3, no pre-releases on PyPI), and
the package source contains no references to the hosted-agent endpoint
shape.

## What the patch does

1. **Detects agent kind.** On every `factory.get_agent_node(name, version)`
   call, the patch fetches the agent record via
   `client.agents.get_version(agent_name=name, agent_version=version or "latest")`
   and inspects `agent.definition`. If it's a `HostedAgentDefinition` it
   returns a custom `HostedAgentNode`; otherwise it delegates to the original
   `ResponsesAgentNode`.

2. **`HostedAgentNode` calls the per-agent endpoint correctly.**

   * Base URL: `{project}/agents/{name}/endpoint/protocols/openai`
   * Query: `api-version=v1` (override via
     `FOUNDRY_HOSTED_AGENT_API_VERSION`)
   * Header: `Foundry-Features: HostedAgents=V1Preview`
   * Auth: `DefaultAzureCredential` token, scope
     `https://ai.azure.com/.default`, refreshed per request.
   * Request body: a normal OpenAI `responses.create({"input": "...", "model": name})`
     — **no** `agent_reference` extra-body (the URL already implies the
     agent).

3. **Cold-start retry.** The first request after a hosted agent is idle
   often comes back as `424 session_not_ready` while the container warms up.
   The patch transparently retries up to 4 times with linear backoff before
   giving up.

4. **LangGraph-compatible surface.** `HostedAgentNode.invoke(state)` /
   `ainvoke(state)` accept and return the same `{"messages": [...]}` shape
   the factory's prompt-agent node uses, so the same orchestration code in
   `tripmate.py` works for both kinds.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | (required) | Foundry project endpoint. |
| `FOUNDRY_HOSTED_AGENT_API_VERSION` | `v1` | api-version query param for the hosted-agent endpoint. |
| `FOUNDRY_FORCE_HOSTED_AGENT_NODE` | `false` | If truthy, route every agent through `HostedAgentNode` (skips kind detection). |
| `FOUNDRY_USE_MANAGED_IDENTITY` | `false` | Re-enable IMDS in `DefaultAzureCredential` (useful on Azure compute; off locally to avoid the 169.254.169.254 probe). |

## What would need to change in `langchain-azure-ai` upstream

A clean upstream fix would be roughly:

1. **`AgentServiceFactory.get_agent_node`** — fetch the agent record,
   inspect `agent.definition`, and dispatch to a `HostedAgentNode` for
   `HostedAgentDefinition`.
2. **`_AzureAIAgentApiProxyModel`** — when used by a hosted node, swap the
   OpenAI client for one rooted at
   `{project}/agents/{name}/endpoint/protocols/openai?api-version=v1`,
   add the `Foundry-Features: HostedAgents=V1Preview` header, and **omit**
   the `agent_reference` `extra_body` (the URL implies the agent).
3. **Conversation creation** — the proxy currently calls
   `openai_client.conversations.create()` against the project endpoint.
   For hosted agents this either needs to target the same per-agent base
   URL or be skipped (hosted agents currently chain via
   `previous_response_id` rather than a top-level conversation).
4. **Tool / MCP-approval wiring** — hosted agents handle tool execution
   server-side; the existing `ToolNode` / `mcp_approval` conditional edges
   added by `create_prompt_agent` shouldn't be wired in for hosted agents.
5. **Cold-start retry** — add a built-in retry on `424 session_not_ready`
   so the first call after idle doesn't surface a transient error to
   callers.

Track upstream support at:

* [Azure/azure-sdk-for-python](https://github.com/Azure/azure-sdk-for-python)
* [`langchain-azure-ai` on PyPI](https://pypi.org/project/langchain-azure-ai/)

## How to remove the patch later

When upstream native hosted-agent support lands:

1. Pin `langchain-azure-ai>=<that version>` in
   [`requirements.txt`](./requirements.txt).
2. Delete the import in [`tripmate.py`](./tripmate.py):

   ```diff
   - from . import _hosted_agent_patch
   - _hosted_agent_patch.patch_agent_service_factory()
   ```

3. Delete this file and `_hosted_agent_patch.py`.
