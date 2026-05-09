"""Trip Scout Agent.

Travel-search agent that calls the custom web-search MCP server directly
(deployed as an Azure Container App) to ground real travel options
(flights, hotels, activities) for user requests.

Note: an alternative integration path is to register the MCP server with the
Foundry Toolbox and consume it through the toolbox aggregator. See
`docs/foundry-toolbox-reference.md` for that pattern.
"""

from __future__ import annotations

import os

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()


_SYSTEM_PROMPT = """\
You are a travel search assistant for TripMate AI.
You MUST call the `web_search` tool to find real, current flight, hotel,
and activity options. Never answer from prior knowledge alone — always search
first. For each user request:

- Issue at least one `web_search` call with a focused query.
- Suggest at least 2 flight options, 2 hotel options, and 2 activities.
- Quote prices in EUR with realistic ranges.
- Include source links / citations from your tool results.
- Finish with an estimated total budget.

Be concise and friendly.
"""


_mcp_tool = MCPStreamableHTTPTool(
    name="web-search",
    url=os.environ["MCP_SERVER_URL"],
    load_prompts=False,
)

_chat_client = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    model=os.environ["MODEL_DEPLOYMENT_NAME"],
    credential=DefaultAzureCredential(),
)

agent = Agent(
    client=_chat_client,
    instructions=_SYSTEM_PROMPT,
    tools=[_mcp_tool],
    default_options={"store": False},
)


if __name__ == "__main__":
    ResponsesHostServer(agent).run()
