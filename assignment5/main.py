"""Main CLI runner for Assignment 5: AegisOps Orchestrator.

Usage:
  uv run main.py                          # Scenario A, single-agent mode
  uv run main.py --scenario B             # Scenario B (false positive)
  uv run main.py --scenario C             # Scenario C (DDoS + tool degradation)
  uv run main.py --mode multi             # Multi-agent orchestration (Supervisor)
  uv run main.py --mode multi --scenario B
  uv run main.py --demo                   # Demo mode (no API calls)
  uv run main.py --demo --scenario B      # Demo mode with scenario B
  uv run main.py --mode multi --scenario ALL --demo   # All scenarios, multi-agent
"""

import argparse
import json
import os
import sys
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule

# Load environment from common locations
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "assignment4", "mcp_server", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from agent import AegisOpsAgent
from supervisor import SupervisorAgent


console = Console()

# ── Scenario definitions ──────────────────────────────────────────────────────
SCENARIOS = {
    "A": (
        "CRIT-ALERT-8921: Anomalous token exfiltration burst and admin key fetch detected in "
        "production microservice 'auth-proxy-us-east'. Source IP 198.51.100.44. "
        "Potential zero-day signature bypass."
    ),
    "B": (
        "ALERT-0044: Anomalous high egress burst detected on billing-worker-eu. "
        "Large data transfer to cold storage observed at 02:00 UTC. "
        "Triggered by threshold rule on bytes_sent > 1GB."
    ),
    "C": (
        "ALERT-7712: Volumetric SYN-flood traffic detected on payment-gateway-ap. "
        "Container log daemon unresponsive (504 timeout). "
        "Possible L7 DDoS attack on edge payment receiver."
    ),
}


def display_header(mode: str, scenario: str):
    console.print(Panel.fit(
        "[bold cyan]🛡️  AEGIS-OPS: AUTONOMOUS CYBER INCIDENT & ZERO-DAY MITIGATION ORCHESTRATOR[/bold cyan]\n"
        f"[dim]EAG V3 Assignment 5 | Mode: [bold]{mode.upper()}[/bold] | Scenario: [bold]{scenario}[/bold] | "
        "Powered by LLM Gateway & Pydantic V2[/dim]",
        border_style="cyan"
    ))


def display_telemetry_table(trace_events, title="Agent Telemetry & Decision Trace"):
    table = Table(title=f"[bold yellow]{title}[/bold yellow]", border_style="dim")
    table.add_column("Turn", justify="center", style="cyan", no_wrap=True)
    table.add_column("Sub-Agent", style="blue")
    table.add_column("Reasoning", style="magenta")
    table.add_column("Provider", style="green")
    table.add_column("Latency", justify="right", style="blue")
    table.add_column("Confidence", justify="right", style="bold yellow")
    table.add_column("Self-Check", justify="center", style="green")
    table.add_column("⚡ Parallel", justify="center")
    table.add_column("🚨 Escalated", justify="center")
    table.add_column("Tools Dispatched", style="white")

    for ev in trace_events:
        self_check_str = "✅" if ev.get("is_reasonable", True) else "❌"
        tools = ev.get("tools_called") or ev.get("tool_called")
        if isinstance(tools, list):
            tools_str = ", ".join(tools) if tools else "[bold green]Final[/bold green]"
        elif tools:
            tools_str = str(tools)
        else:
            tools_str = "[bold green]Final[/bold green]"

        parallel_str = "[bold cyan]⚡ YES[/bold cyan]" if ev.get("parallel") else "—"
        escalated_str = "[bold red]🚨 YES[/bold red]" if ev.get("escalation_fired") else "—"
        subagent = ev.get("subagent", "AegisOps")

        table.add_row(
            str(ev["turn"]),
            subagent,
            ev["reasoning_type"].upper(),
            ev.get("provider", "—"),
            f"{ev['latency_ms']}ms",
            f"{ev['confidence'] * 100:.0f}%",
            self_check_str,
            parallel_str,
            escalated_str,
            tools_str
        )

    console.print(table)


def display_final_report(rep):
    escalation_note = ""
    if rep.get("escalation_triggered"):
        escalation_note = "\n[bold red]⚠️  ESCALATION: Confidence gate triggered mid-loop. Human review required for destructive actions.[/bold red]"

    console.print(Panel(
        f"[bold green]Incident ID:[/bold green] {rep['incident_id']}\n"
        f"[bold green]Threat Level:[/bold green] [bold red]{rep['threat_level']}[/bold red]\n"
        f"[bold green]Root Cause:[/bold green] {rep['root_cause']}\n"
        f"[bold green]Affected Services:[/bold green] {', '.join(rep['affected_services'])}\n"
        f"[bold green]Mitigation Actions Taken:[/bold green]\n" + "\n".join(f"  • {act}" for act in rep['mitigation_actions_taken']) + "\n"
        f"[bold green]Confidence Score:[/bold green] [bold yellow]{rep['confidence_score'] * 100:.1f}%[/bold yellow]\n"
        f"[bold green]Rollback Plan:[/bold green] {rep['rollback_safety_plan']}\n\n"
        f"[bold cyan]Executive Summary:[/bold cyan]\n{rep['executive_summary']}"
        + escalation_note,
        title="[bold green]📋 FINAL INCIDENT REMEDIATION REPORT (PYDANTIC V2 VERDICT)[/bold green]",
        border_style="green"
    ))


def run_one_scenario(mode: str, scenario: str, demo: bool, provider: str = None):
    """Run a single scenario/mode combo. Returns results dict."""
    display_header(mode, scenario)

    sample_alert = SCENARIOS[scenario]
    console.print(Panel(
        f"[bold red]Incoming High-Priority Alert (Scenario {scenario}):[/bold red]\n{sample_alert}",
        title="[bold red]🚨 SECURITY OPERATIONS CENTER (SOC) FEED[/bold red]",
        border_style="red"
    ))

    if demo:
        console.print("[bold yellow]🎭 DEMO MODE: Running with pre-scripted responses (no API calls)[/bold yellow]\n")

    # ── Run selected mode ─────────────────────────────────────────────────────
    if mode == "single":
        gateway_url = os.getenv("LLM_GATEWAY_URL", "http://localhost:8101")
        agent = AegisOpsAgent(gateway_url=gateway_url, max_turns=7, demo_mode=demo, provider=provider)

        with console.status("[bold green]AegisOps Reasoning & Mitigating...[/bold green]", spinner="dots"):
            results = agent.run_investigation(sample_alert)

        console.print("\n")
        display_telemetry_table(results["telemetry_trace"], f"Single-Agent Telemetry — Scenario {scenario}")

    else:
        console.print(Rule("[bold blue]🤖 Multi-Agent Orchestration Mode[/bold blue]"))
        supervisor = SupervisorAgent(demo_mode=demo, provider=provider)

        with console.status("[bold blue]Supervisor dispatching sub-agents...[/bold blue]", spinner="dots"):
            results = supervisor.run_investigation(sample_alert)

        console.print("\n")
        console.print(Rule("[bold blue]📊 Sub-Agent Telemetry[/bold blue]"))
        display_telemetry_table(results["telemetry_trace"], f"Multi-Agent Telemetry — Scenario {scenario}")

    # ── Final report ──────────────────────────────────────────────────────────
    if results.get("final_report"):
        display_final_report(results["final_report"])

        base_dir = os.path.dirname(__file__)
        # Per-scenario artifact (no overwrite when running ALL)
        out_file = os.path.join(base_dir, f"incident_remediation_summary_{mode}_{scenario}.json")
        with open(out_file, "w") as f:
            json.dump(results, f, indent=2)
        # Keep legacy filename for grader compat (last run wins)
        legacy_file = os.path.join(base_dir, "incident_remediation_summary.json")
        with open(legacy_file, "w") as f:
            json.dump(results, f, indent=2)
        console.print(f"[dim]Artifact saved to: {out_file}[/dim]\n")

        if results.get("escalation_triggered"):
            console.print("[bold red]⚠️  ESCALATION SUMMARY: Agent blocked a destructive action due to low confidence. "
                         "Human sign-off required before executing containment.[/bold red]\n")

    # Tag results for summary table
    results["_mode"] = mode
    results["_scenario"] = scenario
    return results


def display_all_summary(all_results):
    """Print a compact summary table after an ALL run."""
    table = Table(title="[bold yellow]ALL-Scenarios Summary[/bold yellow]", border_style="dim")
    table.add_column("Scenario", justify="center", style="cyan")
    table.add_column("Mode", justify="center", style="blue")
    table.add_column("Incident ID", style="green")
    table.add_column("Threat", justify="center", style="red")
    table.add_column("Turns", justify="right", style="blue")
    table.add_column("Confidence", justify="right", style="bold yellow")
    table.add_column("Artifact", style="dim")

    for r in all_results:
        rep = r.get("final_report") or {}
        table.add_row(
            r.get("_scenario", "?"),
            r.get("_mode", "?"),
            rep.get("incident_id", "—"),
            rep.get("threat_level", "—"),
            str(r.get("total_turns", "—")),
            f"{(rep.get('confidence_score', 0) or 0) * 100:.1f}%",
            f"incident_remediation_summary_{r.get('_mode')}_{r.get('_scenario')}.json",
        )

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="AegisOps Autonomous Incident Orchestrator")
    parser.add_argument(
        "--mode", choices=["single", "multi"], default="single",
        help="single = one AegisOps agent, multi = Supervisor + ForensicsAgent + ThreatIntelAgent"
    )
    parser.add_argument(
        "--scenario", choices=["A", "B", "C", "ALL"], default="A",
        help="A=Zero-day breach, B=False positive backup, C=DDoS + tool degradation, ALL=run A+B+C"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run in demo mode without API calls (uses pre-scripted responses)"
    )
    parser.add_argument(
        "--provider", choices=["gemini", "ollama", "groq", "nvidia", "cerebras", "openrouter", "github"],
        default=None,
        help="Explicit provider to use (bypasses auto-route). E.g. --provider ollama"
    )
    args = parser.parse_args()

    scenarios = ["A", "B", "C"] if args.scenario == "ALL" else [args.scenario]

    all_results = []
    for i, sc in enumerate(scenarios):
        if args.scenario == "ALL":
            console.print(Rule(f"[bold cyan]SCENARIO {sc} ({i+1}/{len(scenarios)}) — MODE: {args.mode.upper()}[/bold cyan]"))
        all_results.append(run_one_scenario(args.mode, sc, args.demo, args.provider))

    if args.scenario == "ALL":
        console.print(Rule("[bold green]✅ ALL SCENARIOS COMPLETE[/bold green]"))
        display_all_summary(all_results)


if __name__ == "__main__":
    main()
