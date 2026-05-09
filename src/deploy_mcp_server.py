"""Build and update the custom web-search MCP Container App."""

import os
import subprocess
from pathlib import Path

from deploy_helpers import build_image, get_env

MCP_SERVER_DIR = Path(__file__).resolve().parent / "agents" / "web-search-mcp"


def _resource_group() -> str:
    rg = os.getenv("AZURE_RESOURCE_GROUP", "")
    if rg:
        return rg
    env_name = get_env("AZURE_ENV_NAME")
    return f"rg-{env_name}"


def deploy() -> None:
    registry = get_env("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    app_name = get_env("MCP_SERVER_APP_NAME")
    rg = _resource_group()

    image = build_image(registry, "web-search-mcp", MCP_SERVER_DIR)

    # Attach ACR with system-assigned identity. Idempotent — re-running just
    # updates the registry config in place. Done here rather than in bicep so
    # the AcrPull role assignment has time to propagate before the first pull.
    print(f"Attaching ACR '{registry}' to Container App '{app_name}'...")
    subprocess.run(
        [
            "az", "containerapp", "registry", "set",
            "--name", app_name,
            "--resource-group", rg,
            "--server", registry,
            "--identity", "system",
        ],
        check=True,
    )

    print(f"Updating Container App '{app_name}' (rg={rg}) to image {image}...")
    subprocess.run(
        [
            "az", "containerapp", "update",
            "--name", app_name,
            "--resource-group", rg,
            "--image", image,
        ],
        check=True,
    )
    print(f"MCP server '{app_name}' updated.")


if __name__ == "__main__":
    deploy()
