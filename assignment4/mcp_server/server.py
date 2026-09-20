"""MCP server entrypoint for Assignment 4."""

from app.mcp_tools import create_application
from app.ui import build_dossier_app


mcp, registry = create_application()


def _build_dossier_app(name: str):
    return build_dossier_app(name, registry.dossiers, registry.knowledge)


if __name__ == "__main__":
    mcp.run(transport="stdio")
