"""Build container images and deploy hosted agents to Azure AI Foundry."""

from azure.ai.projects.models import (
    HostedAgentDefinition,
    ProtocolVersionRecord,
    AgentProtocol,
)

from agents import discover_hosted_agents
from deploy_helpers import (
    assign_azure_ai_user_role,
    build_image,
    get_client,
    get_env,
)


def deploy() -> None:
    client = get_client()

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    model_deployment_name = get_env("AZURE_AI_MODEL_DEPLOYMENT_NAME", default="o4-mini")
    aoai_endpoint = get_env("AZURE_OPENAI_ENDPOINT")
    openai_api_version = get_env("OPENAI_API_VERSION", default="2024-05-01-preview")
    registry = get_env("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    project_arm_id = get_env("AZURE_AI_PROJECT_ID", required=False, default="") or ""
    mcp_server_url = get_env("MCP_SERVER_URL")

    protocols = [ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="1.0.0")]

    # Inside containers, use the new SDK env var names. We also keep the legacy
    # AZURE_AI_* names for any code paths still reading them.
    # NOTE: FOUNDRY_* and AGENT_* are reserved env var prefixes injected by the
    # hosted agent platform — do not set them here.
    hosted_env = {
        "MODEL_DEPLOYMENT_NAME": model_deployment_name,
        "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_deployment_name,
        "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": model_deployment_name,
        "AZURE_OPENAI_ENDPOINT": aoai_endpoint,
        "OPENAI_API_VERSION": openai_api_version,
        "MCP_SERVER_URL": mcp_server_url,
    }

    for config in discover_hosted_agents():
        if not (config.path / "Dockerfile").exists():
            print(f"Skipping '{config.name}': no Dockerfile found")
            continue

        image_tag = build_image(registry, config.name, config.path)
        env_vars = {**hosted_env, **config.env_vars}

        agent = client.agents.create_version(
            agent_name=config.name,
            description=config.description,
            definition=HostedAgentDefinition(
                container_protocol_versions=protocols,
                cpu=config.cpu,
                memory=config.memory,
                image=image_tag,
                environment_variables=env_vars,
            ),
            metadata={"enableVnextExperience": "true"},
            headers={"Foundry-Features": "HostedAgents=V1Preview"},
        )
        print(f"Hosted agent '{config.name}' created: {agent.id}")

        # Grant the agent's dedicated Entra identities Azure AI User at project scope
        # so they can call models and read agent metadata.
        # Each hosted agent has TWO identities exposed by the API:
        #   * instance_identity — used by the running agent at request time
        #   * blueprint         — used by the deployment / management plane
        # Both need the role for end-to-end success.
        principal_ids = _extract_principal_ids(agent)
        if principal_ids and project_arm_id:
            for pid in principal_ids:
                assign_azure_ai_user_role(pid, project_arm_id)
        elif not principal_ids:
            print(f"  WARNING: could not find agent identity principals for '{config.name}'.")
        elif not project_arm_id:
            print("  WARNING: AZURE_AI_PROJECT_ID not set — skipping RBAC assignment.")


def _extract_principal_ids(agent_version) -> list[str]:
    """Return the agent's runtime + blueprint Entra principal IDs.

    The Foundry API exposes hosted-agent identities under ``instance_identity``
    (the running container's MI) and ``blueprint`` (the management-plane MI).
    Both must be granted Azure AI User for the agent to function.
    """
    data: dict = {}
    as_dict = getattr(agent_version, "as_dict", None)
    if callable(as_dict):
        data = as_dict()

    principals: list[str] = []
    seen: set[str] = set()
    for key in ("instance_identity", "blueprint", "identity", "agent_identity"):
        section = data.get(key) if data else getattr(agent_version, key, None)
        if not section:
            continue
        if hasattr(section, "as_dict"):
            section = section.as_dict()
        if isinstance(section, dict):
            pid = (
                section.get("principal_id")
                or section.get("principalId")
                or section.get("object_id")
                or section.get("objectId")
            )
            if pid and pid not in seen:
                principals.append(pid)
                seen.add(pid)
    return principals


if __name__ == "__main__":
    deploy()

