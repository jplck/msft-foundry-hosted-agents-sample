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
