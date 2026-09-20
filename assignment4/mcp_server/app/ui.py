import json
from pathlib import Path

from prefab_ui.app import PrefabApp
from prefab_ui.components import Badge, Card, CardContent, Column, Container, H1, H3, Metric, Muted, Row, Tabs, Tab, Text
from prefab_ui.components.charts import PieChart, Sparkline

from .repositories import DossierRepository, KnowledgeRepository


def build_dossier_app(name: str, dossiers: DossierRepository, knowledge: KnowledgeRepository) -> PrefabApp:
    path = dossiers.path_for(name)
    title = path.stem.replace("-", " ").title()
    if path.exists():
        body = path.read_text(encoding="utf-8")
        badge_text, badge_variant = "Saved on disk", "success"
    else:
        body = f"No dossier named '{name}' exists yet. Run save_dossier first."
        badge_text, badge_variant = "Missing", "destructive"

    state = knowledge.load()
    finance = state.get("finance", {})
    comparison = state.get("comparison", {})

    with PrefabApp(state={"dossier": name}, css_class="bg-slate-950 text-slate-50 min-h-screen font-sans") as app:
        with Container(css_class="max-w-3xl mx-auto p-8"):
            with Column(gap=6):
                H1(f"Dossier: {title}")
                Badge(badge_text, variant=badge_variant)
                with Card(css_class="bg-slate-900 border-slate-800 shadow-2xl"):
                    with CardContent(css_class="p-8"):
                        Text(body, css_class="text-slate-300 leading-relaxed whitespace-pre-wrap")
                if finance:
                    with Tabs(value="performance"):
                        with Tab("Performance", value="performance"):
                            with Column(gap=4):
                                H3(f"{finance.get('ticker', 'Stock')} - {finance.get('days', 0)} day performance")
                                with Row(gap=6):
                                    Metric(label="Start", value=f"{finance.get('start_price', 0):.2f}")
                                    Metric(label="End", value=f"{finance.get('end_price', 0):.2f}")
                                    Metric(label="Return", value=f"{finance.get('change_percent', 0):.2f}%")
                                if finance.get("sparkline"):
                                    Sparkline(data=finance["sparkline"])
                        with Tab("Comparison", value="comparison"):
                            if comparison:
                                with Column(gap=4):
                                    H3(f"{finance.get('ticker', 'Primary')} vs {comparison.get('ticker', 'Comparison')}")
                                    winner = finance if finance.get("change_percent", 0) >= comparison.get("change_percent", 0) else comparison
                                    Text(
                                        f"Best performer: {winner.get('ticker', 'Unknown')} "
                                        f"({winner.get('change_percent', 0):+.2f}%)",
                                        css_class="text-emerald-300 font-semibold",
                                    )
                                    with Row(gap=6):
                                        with Card(css_class="bg-slate-800 border-slate-700 flex-1"):
                                            with CardContent(css_class="p-4"):
                                                H3(finance.get("ticker", "Primary"))
                                                Metric(label="Return", value=f"{finance.get('change_percent', 0):+.2f}%")
                                                Muted(f"{finance.get('start_price', 0):.2f} -> {finance.get('end_price', 0):.2f}")
                                        with Card(css_class="bg-slate-800 border-slate-700 flex-1"):
                                            with CardContent(css_class="p-4"):
                                                H3(comparison.get("ticker", "Comparison"))
                                                Metric(label="Return", value=f"{comparison.get('change_percent', 0):+.2f}%")
                                                Muted(f"{comparison.get('start_price', 0):.2f} -> {comparison.get('end_price', 0):.2f}")
                                    H3("Return split")
                                    PieChart(
                                        data=[
                                            {"ticker": finance.get("ticker", "Primary"), "return": abs(finance.get("change_percent", 0))},
                                            {"ticker": comparison.get("ticker", "Comparison"), "return": abs(comparison.get("change_percent", 0))},
                                        ],
                                        data_key="return",
                                        name_key="ticker",
                                        show_label=True,
                                        show_legend=True,
                                        height=260,
                                    )
                                    with Row(gap=4):
                                        Sparkline(data=finance.get("sparkline", []))
                                        Sparkline(data=comparison.get("sparkline", []))
                            else:
                                Muted("No comparison has been added yet. Ask the agent to add another stock.")
                Muted(f"Source file: {path}")
    return app
