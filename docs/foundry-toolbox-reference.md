# Foundry Toolbox integration (reference)

This project currently calls the custom web-search MCP server
([`src/agents/web-search-mcp/`](../src/agents/web-search-mcp/)) **directly**
from the trip-scout agent. Earlier iterations went through the **Foundry
Toolbox** aggregator instead, which is a useful pattern when:

- Multiple agents need to share the same set of MCP tools.
- You want central versioning, RBAC, and approval policies for tool calls.
- You need to fan in tools from several MCP servers behind one endpoint.

The toolbox path was dropped here only because it forces tool function names of
the shape `<server_label>.<tool_name>` (literal dot), and the OpenAI Responses
API rejects function names that don't match `^[a-zA-Z0-9_-]+$`. Working around
that requires a monkey-patch of `agent_framework._mcp._normalize_mcp_name`.
For a single MCP server consumed by a single agent, going direct is simpler.

## Toolbox URL shape

The toolbox MCP endpoint is **versioned** — the unversioned alias is not a
valid MCP route:

```
{project_endpoint}/toolboxes/{name}/versions/{N}/mcp?api-version=v1
```

Required header on every request:

```
Foundry-Features: Toolboxes=V1Preview
```

## `deploy_toolbox.py`

Run after the MCP server is deployed and before hosted agents are deployed.
Creates a new toolbox version that wraps the MCP server.

```python
"""Create or update the Foundry Toolbox that hosted agents consume at runtime.

Registers the custom web-search MCP server (Azure Container App) as the
toolbox's `mcp` tool.
"""

from azure.ai.projects.models import MCPTool

from deploy_helpers import get_client, get_env

TOOLBOX_NAME = "tripmate-tools"


def deploy() -> None:
    client = get_client()

    mcp_url = get_env("MCP_SERVER_URL")

    mcp_tool = MCPTool(
        server_label="web-search",
        server_url=mcp_url,
        server_description=(
            "Domain-restricted web search over a curated list of travel sites. "
            "Use the `web_search` tool with a focused query."
        ),
    )

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")

    version = client.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        description="TripMate AI shared toolbox — custom MCP web search",
        tools=[mcp_tool],
    )

    consumer_endpoint = (
        f"{project_endpoint}/toolboxes/{TOOLBOX_NAME}"
        f"/versions/{version.version}/mcp?api-version=v1"
    )
    print(f"Toolbox '{TOOLBOX_NAME}' version '{version.version}' created.")
    print(f"  Backed by MCP server: {mcp_url}")
    print(f"  Consumer endpoint:    {consumer_endpoint}")


if __name__ == "__main__":
    deploy()
```

## `deploy_hosted_agents.py` additions

Resolve the latest toolbox version and inject it into each hosted agent's
environment:

```python
from deploy_toolbox import TOOLBOX_NAME


def _latest_toolbox_version(client, name: str) -> str:
    """Return the highest-numbered version of the named toolbox.

    The Foundry MCP route requires an explicit version segment; there's no
    'latest' alias on the unversioned URL. Falls back to the toolbox's
    ``default_version`` if version listing is unavailable.
    """
    try:
        versions = list(client.beta.toolboxes.list_versions(name=name))
    except Exception as exc:
        print(f"  WARNING: could not list versions for toolbox '{name}': {exc}")
        versions = []

    numeric: list[int] = []
    for v in versions:
        raw = getattr(v, "version", None)
        if raw is None and hasattr(v, "as_dict"):
            raw = v.as_dict().get("version")
        try:
            numeric.append(int(str(raw)))
        except (TypeError, ValueError):
            continue
    if numeric:
        return str(max(numeric))

    tb = client.beta.toolboxes.get(name=name)
    default = getattr(tb, "default_version", None)
    if default is None and hasattr(tb, "as_dict"):
        default = tb.as_dict().get("default_version")
    if not default:
        raise RuntimeError(f"Could not determine a version for toolbox '{name}'.")
    return str(default)


# Inside deploy():
toolbox_version = _latest_toolbox_version(client, TOOLBOX_NAME)
toolbox_endpoint = (
    f"{project_endpoint}/toolboxes/{TOOLBOX_NAME}"
    f"/versions/{toolbox_version}/mcp?api-version=v1"
)
hosted_env["TOOLBOX_NAME"] = TOOLBOX_NAME
hosted_env["TOOLBOX_MCP_ENDPOINT"] = toolbox_endpoint
hosted_env["TOOLBOX_VERSION"] = str(toolbox_version)
```

Make sure each agent's instance + blueprint Entra principals get the **Azure
AI User** role at project scope so they can hit the toolbox endpoint.

## Trip-scout agent wired through the toolbox

```python
import os
import re

import httpx
import agent_framework._mcp as _af_mcp


# Workaround for the OpenAI Responses API rejecting function names containing
# dots. The toolbox aggregator returns tools as `<server_label>.<tool_name>`
# and agent_framework's normalizer keeps dots — patch BEFORE importing
# MCPStreamableHTTPTool.
def _normalize_mcp_name_no_dots(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


_af_mcp._normalize_mcp_name = _normalize_mcp_name_no_dots

from agent_framework import Agent, MCPStreamableHTTPTool  # noqa: E402
from agent_framework.foundry import FoundryChatClient  # noqa: E402
from agent_framework_foundry_hosting import ResponsesHostServer  # noqa: E402
from azure.identity import DefaultAzureCredential, get_bearer_token_provider  # noqa: E402


# Prefer the explicit, versioned URL written by the deploy script. The
# platform-injected `FOUNDRY_AGENT_TOOLBOX_ENDPOINT` is an unversioned alias
# and isn't a valid MCP route.
_TOOLBOX_ENDPOINT = (
    os.environ.get("TOOLBOX_MCP_ENDPOINT")
    or os.environ["FOUNDRY_AGENT_TOOLBOX_ENDPOINT"]
)

_credential = DefaultAzureCredential()
_token_provider = get_bearer_token_provider(_credential, "https://ai.azure.com/.default")


class _ToolboxAuth(httpx.Auth):
    """Inject a fresh Entra token on every toolbox MCP request."""

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {_token_provider()}"
        yield request


_http_client = httpx.AsyncClient(
    auth=_ToolboxAuth(),
    headers={"Foundry-Features": "Toolboxes=V1Preview"},
    timeout=120.0,
)

_mcp_tool = MCPStreamableHTTPTool(
    name="toolbox",
    url=_TOOLBOX_ENDPOINT,
    http_client=_http_client,
    load_prompts=False,
    # Prefix prevents collision with the model's built-in `web_search` tool,
    # which otherwise gets dispatched as a no-op stub.
    tool_name_prefix="toolbox",
)
```

The exposed function name then becomes `toolbox_web-search_web_search` (after
the normalizer patch). Reference that exact name in the system prompt.
