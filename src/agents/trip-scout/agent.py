"""Trip Scout Agent.

Travel-search agent that uses Foundry Toolbox MCP (custom web-search MCP server) to
ground real travel options (flights, hotels, activities) for user requests.
"""

from __future__ import annotations

import os

import httpx

# IMPORTANT: patch agent_framework's MCP name normalizer BEFORE importing
# MCPStreamableHTTPTool. The Foundry toolbox returns its tools as
# `<server_label>.<tool_name>` (e.g. `web-search.web_search`). The default
# normalizer in agent_framework allows dots, but the OpenAI Responses API
# rejects function names that don't match `^[a-zA-Z0-9_-]+$`, so the model
# call fails after the tool runs. Replace dots with underscores so the
# exposed function name (`toolbox_web-search_web_search`) is accepted.
import re as _re

import agent_framework._mcp as _af_mcp


def _normalize_mcp_name_no_dots(name: str) -> str:
    return _re.sub(r"[^A-Za-z0-9_-]", "_", name)


_af_mcp._normalize_mcp_name = _normalize_mcp_name_no_dots

from agent_framework import Agent, MCPStreamableHTTPTool  # noqa: E402
from agent_framework.foundry import FoundryChatClient  # noqa: E402
from agent_framework_foundry_hosting import ResponsesHostServer  # noqa: E402
from azure.identity import DefaultAzureCredential, get_bearer_token_provider  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()


_SYSTEM_PROMPT = """\
You are a travel search assistant for TripMate AI.
You MUST call the `toolbox_web-search_web_search` tool to find real, current flight, hotel,
and activity options. Never answer from prior knowledge alone — always search
first. For each user request:

- Issue at least one `toolbox_web-search_web_search` call with a focused query.
- Suggest at least 2 flight options, 2 hotel options, and 2 activities.
- Quote prices in EUR with realistic ranges.
- Include source links / citations from your tool results.
- Finish with an estimated total budget.

Be concise and friendly.
"""


# Prefer the explicit, versioned URL written by the deploy script. The
# platform-injected `FOUNDRY_AGENT_TOOLBOX_ENDPOINT` is an unversioned alias
# and isn't a valid MCP route — only `/toolboxes/{name}/versions/{n}/mcp` is.
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
    # which otherwise gets dispatched as a no-op stub (duration=0, empty result).
    tool_name_prefix="toolbox",
)

_chat_client = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    model=os.environ["MODEL_DEPLOYMENT_NAME"],
    credential=_credential,
)

agent = Agent(
    client=_chat_client,
    instructions=_SYSTEM_PROMPT,
    tools=[_mcp_tool],
    default_options={"store": False},
)


if __name__ == "__main__":
    ResponsesHostServer(agent).run()
