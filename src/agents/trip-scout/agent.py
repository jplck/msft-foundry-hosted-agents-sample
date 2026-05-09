"""Trip Scout Agent.

Travel-search agent that uses Foundry Toolbox MCP (custom web-search MCP server) to
ground real travel options (flights, hotels, activities) for user requests.
"""

from __future__ import annotations

import os

import httpx
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv

load_dotenv()


_SYSTEM_PROMPT = """\
You are a travel search assistant for TripMate AI.
You MUST call the `toolbox-web_search` tool to find real, current flight, hotel,
and activity options. Never answer from prior knowledge alone — always search
first. For each user request:

- Issue at least one `toolbox-web_search` call with a focused query.
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
