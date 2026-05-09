"""Orchestrator — deploys all agent types to Azure AI Foundry."""

from deploy_prompt_agents import deploy as deploy_prompt
from deploy_mcp_server import deploy as deploy_mcp
from deploy_hosted_agents import deploy as deploy_hosted
from deploy_workflow_agents import deploy as deploy_workflow


def main() -> None:
    print("=== Deploying prompt agents ===")
    deploy_prompt()

    print("\n=== Deploying MCP web-search server ===")
    deploy_mcp()

    print("\n=== Deploying hosted agents ===")
    deploy_hosted()

    print("\n=== Deploying workflow agents ===")
    deploy_workflow()

    print("\nAll agents deployed.")


if __name__ == "__main__":
    main()