import json
import re
from typing import Any

from fastmcp import FastMCP
from prefab_ui.app import PrefabApp

from .repositories import DossierRepository, KnowledgeRepository
from .services import InternetService, MarketService
from .ui import build_dossier_app


class ToolRegistry:
    """Application services exposed through the MCP protocol."""

    def __init__(self, internet: InternetService, market: MarketService, dossiers: DossierRepository, knowledge: KnowledgeRepository):
        self.internet = internet
        self.market = market
        self.dossiers = dossiers
        self.knowledge = knowledge

    def market_dossier_content(self) -> str:
        state = self.knowledge.load()
        primary = state.get("finance", {})
        comparison = state.get("comparison", {})
        lines = [
            "MARKET PERFORMANCE DOSSIER",
            f"Period: {primary.get('days', 0)} days",
            "",
            f"{primary.get('ticker', 'Primary')}",
            f"Start price: {primary.get('start_price', 0):.2f} {primary.get('currency', 'USD')}",
            f"End price: {primary.get('end_price', 0):.2f} {primary.get('currency', 'USD')}",
            f"Change: {primary.get('change', 0):+.2f} ({primary.get('change_percent', 0):+.2f}%)",
        ]
        if comparison:
            lines.extend([
                "",
                f"{comparison.get('ticker', 'Comparison')}",
                f"Start price: {comparison.get('start_price', 0):.2f} {comparison.get('currency', 'USD')}",
                f"End price: {comparison.get('end_price', 0):.2f} {comparison.get('currency', 'USD')}",
                f"Change: {comparison.get('change', 0):+.2f} ({comparison.get('change_percent', 0):+.2f}%)",
            ])
        return "\n".join(lines)

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool()
        def lookup_entity(query: str) -> dict[str, Any]:
            """INTERNET: Look up an entity with Wikipedia and recent news."""
            return self.internet.lookup_entity(query)

        @mcp.tool()
        def analyze_stock_performance(request: str) -> dict[str, Any]:
            """INTERNET: Analyze a stock over a natural-language period."""
            days_match = re.search(r"(\d+)\s*(?:day|days)", request.lower())
            days = int(days_match.group(1)) if days_match else 30
            ticker = self.market.resolve_ticker(request)
            finance = self.market.performance(ticker, days) if ticker != "NONE" else {}
            if not finance:
                raise ValueError(f"No market data found for request: {request}")
            state = self.knowledge.load()
            state.update({"target": ticker, "finance": finance, "comparison": {}})
            self.knowledge.save(state)
            return {"ok": True, "ticker": ticker, "days": days, "finance": finance}

        @mcp.tool()
        def add_stock_comparison(request: str) -> dict[str, Any]:
            """INTERNET: Add another stock to the current comparison."""
            state = self.knowledge.load()
            primary = state.get("finance", {})
            days_match = re.search(r"(\d+)\s*(?:day|days)", request.lower())
            days = int(days_match.group(1)) if days_match else int(primary.get("days", 30))
            ticker = self.market.resolve_ticker(request)
            comparison = self.market.performance(ticker, days) if ticker != "NONE" else {}
            if not comparison:
                raise ValueError(f"No comparison market data found for request: {request}")
            state["comparison"] = comparison
            self.knowledge.save(state)
            return {"ok": True, "primary": primary.get("ticker", "unknown"), "comparison": comparison}

        @mcp.tool()
        def save_dossier(name: str, content: str) -> dict[str, Any]:
            """FILE CRUD: Create or replace a local text dossier."""
            if name == "market-performance":
                content = self.market_dossier_content()
            path, operation = self.dossiers.write(name, content)
            return {"ok": True, "operation": operation, "path": str(path), "size_bytes": path.stat().st_size}

        @mcp.tool()
        def update_dossier(name: str, content: str) -> dict[str, Any]:
            """FILE CRUD: Update an existing local text dossier."""
            if name == "market-performance":
                content = self.market_dossier_content()
            path = self.dossiers.update(name, content)
            return {"ok": True, "operation": "updated", "path": str(path), "size_bytes": path.stat().st_size}

        @mcp.tool()
        def read_dossier(name: str) -> dict[str, Any]:
            """FILE CRUD: Read a local text dossier."""
            path, content = self.dossiers.read(name)
            return {"ok": True, "path": str(path), "size_bytes": path.stat().st_size, "content": content}

        @mcp.tool()
        def list_dossiers() -> list[str]:
            """FILE CRUD: List local text dossiers."""
            return self.dossiers.list()

        @mcp.tool()
        def delete_dossier(name: str) -> dict[str, Any]:
            """FILE CRUD: Delete a local text dossier."""
            path = self.dossiers.delete(name)
            return {"ok": True, "operation": "deleted", "path": str(path)}

        @mcp.tool(app=True)
        def show_dossier(name: str) -> PrefabApp:
            """PREFAB UI: Render a saved dossier and market comparison dashboard."""
            return build_dossier_app(name, self.dossiers, self.knowledge)


def create_application() -> tuple[FastMCP, ToolRegistry]:
    from dotenv import load_dotenv
    from google import genai
    from .config import Settings

    load_dotenv()
    settings = Settings.from_environment()
    settings.ensure_directories()
    llm_client = genai.Client()
    internet = InternetService(settings.request_timeout_seconds)
    market = MarketService(internet, llm_client, settings.llm_model)
    registry = ToolRegistry(internet, market, DossierRepository(settings.dossier_dir), KnowledgeRepository(settings.database_file))
    mcp = FastMCP("Autonomous Knowledge Graph")
    registry.register(mcp)
    return mcp, registry
