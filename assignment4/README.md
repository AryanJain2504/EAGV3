# Assignment 4: Autonomous Knowledge Graph

This project demonstrates the required MCP chain:

1. **Internet**: `lookup_entity` fetches a Wikipedia summary and recent news.
2. **Local file CRUD**: dossier tools create or replace, read, list, update, and delete `.txt` files.
3. **Prefab UI**: `show_dossier` renders the saved dossier as a Prefab dashboard.

The assignment uses Prefab directly. There is no React or Vite frontend.

## Project structure

```text
assignment4/
	start_dashboard.sh       # local process launcher
	README.md
	mcp_server/
		server.py              # MCP composition root / stdio entrypoint
		client.py              # FastAPI + SSE adapter and Prefab HTTP route
		dossier_cli.py         # conversational CLI adapter
		app/
			config.py            # environment-backed settings
			repositories.py      # JSON and local-file persistence
			services.py          # Internet and market-data integrations
			mcp_tools.py         # MCP tool registration and orchestration
			ui.py                # Prefab view builders
		test_chain.py          # deterministic Internet/file integration check
```

The dependency direction is intentionally one-way: adapters call the
application services, services call repositories or external APIs, and the
Prefab view only reads repository state. This keeps MCP, HTTP, and CLI entry
points replaceable without duplicating business logic.

## Run

Start the local LLM gateway on `http://localhost:8101`, then run:

```bash
cd assignment4
./start_dashboard.sh
```

The Prefab dossier page is available at:

```text
http://127.0.0.1:8000/ui/dossier?name=tata-sons
```

The agent endpoint is:

```text
http://127.0.0.1:8000/api/agent?query=Tata%20Sons
```

Use a prompt such as:

> Find the ownership details and key facts of Tata Sons, save the findings to a text dossier, and show the saved dossier on the Prefab dashboard.

The agent must call `lookup_entity`, then `save_dossier`, then `show_dossier`.

## Deterministic validation

This test exercises the Internet and file stages without LLM quota:

```bash
cd assignment4/mcp_server
venv/bin/python test_chain.py "Tata Sons"
```

The MCP CRUD operations are exposed through `save_dossier`, `update_dossier`, `read_dossier`, `list_dossiers`, and `delete_dossier`.
