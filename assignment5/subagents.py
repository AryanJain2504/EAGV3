"""AegisOps Sub-agents for Multi-Agent Orchestration Mode.

ForensicsAgent  — specializes in log querying and network topology analysis.
ThreatIntelAgent — specializes in threat intelligence lookups and mitigation simulation.

Each sub-agent runs its own focused ReAct loop (2-3 turns) with a narrower system prompt.
Used by SupervisorAgent to parallelize independent investigation tracks.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
from pydantic import ValidationError

from schemas import AgentTurnOutput, ToolCall
from tools import TOOL_REGISTRY


# ── Full AgentTurnOutput schema instruction (shared) ────────────────────────────
AGENT_TURN_SCHEMA = """
### REQUIRED JSON SCHEMA (AgentTurnOutput)

You must emit EXACTLY ONE valid JSON object per turn conforming strictly to this schema:

```json
{
  "step_number": 1,
  "reasoning_type": "deductive | lookup | diagnostic | arithmetic | heuristic | verification",
  "step_by_step_thinking": "Detailed chain of thought analyzing prior facts and explaining rationale...",
  "self_check": {
    "is_reasonable": true,
    "sanity_notes": "Sanity check confirming evidence supports the hypothesis without hallucinations.",
    "verification_check": "Validation that current evidence justifies the requested tool action."
  },
  "confidence": 0.95,
  "tool_calls": [
    {"name": "tool_name_1", "arguments": {"arg": "val"}},
    {"name": "tool_name_2", "arguments": {"arg": "val"}}
  ],
  "tool_call": null,
  "fallback_plan": "Specific procedure to execute if the tool fails or data is unavailable.",
  "is_final_step": false,
  "final_remediation_summary": null
}
```

> **Note**: Use `tool_calls` (list) for parallel execution of two independent tools. Use `tool_call` (object) for a single tool. Set both to null when `is_final_step` is true.

---

### REASONING TYPE DEFINITIONS
- "deductive": Analyzing alerts to formulate breach hypotheses
- "lookup": Querying logs, CVE databases, or threat actor registries
- "diagnostic": Analyzing topology, dependency chains, and network traffic
- "arithmetic": Computing blast radius percentages or exfiltrated byte counts
- "heuristic": Formulating candidate containment strategies or tuning alert thresholds
- "verification": Evaluating sanity checks and validating simulated mitigation results

---

### FEW-SHOT EXAMPLES

#### Example 1: Parallel Tool Dispatch (Two Independent Tools)
```json
{
  "step_number": 1,
  "reasoning_type": "deductive",
  "step_by_step_thinking": "I need to gather two independent data points: system logs for evidence and network topology for blast radius. These can run in parallel.",
  "self_check": {
    "is_reasonable": true,
    "sanity_notes": "Fetching logs and topology simultaneously is valid since neither depends on the other.",
    "verification_check": "Both tools accept the service name as input which is known from the alert."
  },
  "confidence": 0.90,
  "tool_calls": [
    {"name": "query_system_logs", "arguments": {"service": "auth-proxy-us-east", "time_window_mins": 15}},
    {"name": "inspect_network_topology", "arguments": {"service": "auth-proxy-us-east"}}
  ],
  "tool_call": null,
  "fallback_plan": "If either tool fails, proceed with the successful one and flag the gap in confidence.",
  "is_final_step": false,
  "final_remediation_summary": null
}
```

#### Example 2: Single Tool (Sequential Step)
```json
{
  "step_number": 2,
  "reasoning_type": "lookup",
  "step_by_step_thinking": "Logs confirmed suspicious IP. Now I need to cross-reference this IP against threat intelligence feeds.",
  "self_check": {
    "is_reasonable": true,
    "sanity_notes": "Threat intel lookup depends on the IP identified from logs.",
    "verification_check": "IP confirmed from log observation."
  },
  "confidence": 0.95,
  "tool_calls": null,
  "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "198.51.100.44"}},
  "fallback_plan": "If threat intel returns no data, proceed with severity classification as UNKNOWN_ACTOR.",
  "is_final_step": false,
  "final_remediation_summary": null
}
```

#### Example 3: Final Resolution Step
```json
{
  "step_number": 3,
  "reasoning_type": "verification",
  "step_by_step_thinking": "All investigation phases complete. Evidence gathered, threat identified, mitigation simulated successfully.",
  "self_check": {
    "is_reasonable": true,
    "sanity_notes": "All required forensic dimensions have been observed and validated.",
    "verification_check": "All mitigation tests passed; ready to emit final report."
  },
  "confidence": 0.99,
  "tool_calls": null,
  "tool_call": null,
  "fallback_plan": "If health checks degrade post-mitigation, trigger rollback playbook.",
  "is_final_step": true,
  "final_remediation_summary": "INCIDENT RESOLVED: Contained threat with zero downtime."
}
```
"""


FORENSICS_PROMPT = f"""You are ForensicsAgent, a specialized cyber-forensics sub-agent.
Your ONLY job is to gather raw evidence: query system logs and inspect network topology.
You have access to ONLY these two tools:
  - query_system_logs(service, time_window_mins)
  - inspect_network_topology(service)

You must emit strict AgentTurnOutput JSON on every turn.
When you have gathered sufficient log and topology evidence, set is_final_step: true
and summarize your forensic findings in final_remediation_summary.
Do NOT attempt mitigation. Do NOT look up threat intel. That is ThreatIntelAgent's job.

{AGENT_TURN_SCHEMA}
"""

THREAT_INTEL_PROMPT = f"""You are ThreatIntelAgent, a specialized threat intelligence and containment sub-agent.
You will receive forensic evidence from ForensicsAgent. Your job is to:
  1. Look up threat intelligence for suspicious IPs or CVEs found in the forensic report.
  2. Simulate the best containment or mitigation action.

You have access to ONLY these two tools:
  - lookup_threat_intel(indicator)
  - simulate_mitigation(action, target_service)

You must emit strict AgentTurnOutput JSON on every turn.
When mitigation is verified, set is_final_step: true and summarize your verdict in final_remediation_summary.

{AGENT_TURN_SCHEMA}
"""

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:8101")


def _call_llm(system_prompt: str, messages: List[Dict[str, str]], provider: Optional[str] = None) -> str:
    """Shared LLM caller used by both sub-agents."""
    payload = {
        "messages": messages,
        "system": system_prompt,
        "max_tokens": 1500,
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }
    if provider:
        payload["provider"] = provider

    # Try gateway first
    for url in [GATEWAY_URL, "http://localhost:8100"]:
        try:
            resp = httpx.post(f"{url}/v1/chat", json=payload, timeout=120.0)
            if resp.status_code == 200:
                return resp.json().get("text", "")
        except Exception as e:
            print(f"    [Gateway call failed: {e}]")
            continue

    # If provider is explicitly set (and not gemini), don't fall back to direct Gemini
    # The gateway should handle the provider routing
    if provider and provider != "gemini":
        raise RuntimeError(f"Gateway failed for provider={provider}. No fallback for explicit non-gemini providers.")

    # Fallback to direct Gemini API only when no explicit provider or provider=gemini
    if GEMINI_API_KEY and (not provider or provider == "gemini"):
        conversation = system_prompt + "\n\n--- CONVERSATION ---\n"
        for m in messages:
            conversation += f"\n[{m['role'].upper()}]:\n{m['content']}\n"
        body = {
            "contents": [{"parts": [{"text": conversation}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        # Retry with exponential backoff for 429 rate limit errors
        max_retries = 5
        base_delay = 3.0
        for attempt in range(max_retries):
            try:
                resp = httpx.post(url, json=body, timeout=60.0)
                if resp.status_code == 429:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"  ⏳ Rate limited (429). Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"  ⏳ Rate limited (429). Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Max retries exceeded for Gemini API (rate limited)")

    raise RuntimeError("No LLM available for sub-agent call.")


def _parse(raw: str) -> AgentTurnOutput:
    cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return AgentTurnOutput.model_validate(json.loads(cleaned))
    except Exception:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            return AgentTurnOutput.model_validate(json.loads(match.group()))
        raise


def _call_demo_subagent(name: str, alert: str, forensic_summary: str, turn: int) -> Dict:
    """Generate mock responses for sub-agents in demo mode."""
    # ForensicsAgent responses
    if name == "ForensicsAgent":
        if "auth-proxy" in alert or "CRIT-ALERT-8921" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "Need to gather forensic evidence for auth-proxy-us-east. Will query system logs and inspect network topology in parallel.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Both tools independent", "verification_check": "Service name known from alert"},
                    "confidence": 0.95,
                    "tool_calls": [
                        {"name": "query_system_logs", "arguments": {"service": "auth-proxy-us-east", "time_window_mins": 15}},
                        {"name": "inspect_network_topology", "arguments": {"service": "auth-proxy-us-east"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "If logs fail, use VPC flow logs",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "diagnostic",
                    "step_by_step_thinking": "Logs confirm: admin key fetch and 1.2GB exfiltration from 198.51.100.44. Topology shows critical blast radius to user-credentials-db and session-cache-redis. Forensic evidence gathered.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Evidence confirms breach", "verification_check": "All forensic data collected"},
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "N/A",
                    "is_final_step": True,
                    "final_remediation_summary": "ForensicsAgent confirmed: anomalous token exfiltration and admin key fetch from IP 198.51.100.44 on auth-proxy-us-east. Blast radius includes user-credentials-db (PII) and session-cache-redis."
                }
            }
            return demo_responses.get(turn, demo_responses[2])
        
        elif "billing" in alert or "ALERT-0044" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "Need to verify if billing-worker-eu egress burst is scheduled backup. Query logs and topology in parallel.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Time matches backup window", "verification_check": "Internal service expected"},
                    "confidence": 0.90,
                    "tool_calls": [
                        {"name": "query_system_logs", "arguments": {"service": "billing-worker-eu", "time_window_mins": 60}},
                        {"name": "inspect_network_topology", "arguments": {"service": "billing-worker-eu"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "If unclear, check threat intel",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Logs confirm: scheduled MONTHLY_LEDGER_ARCHIVE by sa-db-backup-runner@corp.internal from 10.0.12.9 (internal VPC). Topology: internal-only, no public exposure. This is a false positive.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "All indicators point to legitimate backup", "verification_check": "Internal service account verified"},
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "N/A",
                    "is_final_step": True,
                    "final_remediation_summary": "ForensicsAgent confirmed: scheduled monthly ledger archive by internal service account sa-db-backup-runner triggered egress alert. Service is internal-only with no external exposure."
                }
            }
            return demo_responses.get(turn, demo_responses[2])
        
        elif "payment" in alert or "ALERT-7712" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "Container logs degraded (504). Must use VPC flow logs fallback and check topology for payment-gateway-ap.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Fallback procedure correct", "verification_check": "VPC logs independent of container"},
                    "confidence": 0.90,
                    "tool_calls": [
                        {"name": "query_vpc_flow_logs", "arguments": {"vpc_id": "vpc-ap-southeast-01", "service": "payment-gateway-ap"}},
                        {"name": "inspect_network_topology", "arguments": {"service": "payment-gateway-ap"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "Escalate to network team if VPC logs fail",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "diagnostic",
                    "step_by_step_thinking": "VPC flow logs confirm volumetric SYN-flood from 15 IPs (top: 203.0.113.19, 203.0.113.22, 198.51.100.88). Topology shows payment-gateway is internet-facing with downstream visa-direct-gw and vault. No host compromise.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Network telemetry confirms volumetric flood", "verification_check": "No internal lateral movement"},
                    "confidence": 0.95,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "N/A",
                    "is_final_step": True,
                    "final_remediation_summary": "ForensicsAgent confirmed: volumetric L7 SYN-flood from 15 distributed IPs targeting payment-gateway-ap. Container logs degraded; VPC flow logs used as fallback. No host compromise detected."
                }
            }
            return demo_responses.get(turn, demo_responses[2])
        
        return {
            "step_number": turn,
            "reasoning_type": "verification",
            "step_by_step_thinking": "Demo complete",
            "self_check": {"is_reasonable": True, "sanity_notes": "Demo", "verification_check": "Demo"},
            "confidence": 0.95,
            "tool_calls": None,
            "tool_call": None,
            "fallback_plan": "Demo",
            "is_final_step": True,
            "final_remediation_summary": "Demo completed."
        }
    
    # ThreatIntelAgent responses
    else:
        if "auth-proxy" in alert or "CRIT-ALERT-8921" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "lookup",
                    "step_by_step_thinking": "Forensics found IP 198.51.100.44. Need to check threat intel for this IP.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "IP from forensics", "verification_check": "Direct indicator from logs"},
                    "confidence": 0.95,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "198.51.100.44"}},
                    "fallback_plan": "If no intel, assume UNKNOWN_ACTOR",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "heuristic",
                    "step_by_step_thinking": "Threat intel confirms: UNC4912 (state-sponsored APT) exploiting CVE-2026-3199 (CVSS 9.8). With confirmed exfiltration and high blast radius, must simulate containment.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "APT + CVE + exfiltration = critical", "verification_check": "All evidence supports containment"},
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "isolate_service_and_block_ip", "target_service": "auth-proxy-us-east"}},
                    "fallback_plan": "Full shutdown if isolation fails",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Mitigation simulation successful: IP blocked, pod isolated, keys rotated. Health checks pass.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "All mitigation verified", "verification_check": "Zero disruption confirmed"},
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "Trigger RB-202 if health degrades",
                    "is_final_step": True,
                    "final_remediation_summary": "ThreatIntelAgent confirmed: UNC4912 / CVE-2026-3199 zero-day exploitation. WAF block applied, pod isolated, JWT keys rotated with 0% downtime."
                }
            }
            return demo_responses.get(turn, demo_responses[3])
        
        elif "billing" in alert or "ALERT-0044" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "lookup",
                    "step_by_step_thinking": "Forensics found internal IP 10.0.12.9 and service account sa-db-backup-runner. Checking threat intel.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Internal indicator", "verification_check": "From forensic evidence"},
                    "confidence": 0.90,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "10.0.12.9"}},
                    "fallback_plan": "Rely on forensic evidence if intel unavailable",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "heuristic",
                    "step_by_step_thinking": "Threat intel confirms: BENIGN_INTERNAL_ASSET with 365-day clean history. This is a false positive. Simulating stand-down and alert tuning.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Benign internal asset confirmed", "verification_check": "No threat indicators"},
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "stand_down_and_tune_alert", "target_service": "billing-worker-eu"}},
                    "fallback_plan": "Monitor only if simulation fails",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Stand-down simulation successful: service account whitelisted, egress threshold tuned for backup window. Zero disruption.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "False positive confirmed", "verification_check": "Zero impact verified"},
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "Monitor next backup cycle",
                    "is_final_step": True,
                    "final_remediation_summary": "ThreatIntelAgent confirmed: BENIGN_INTERNAL_ASSET. Alert rule tuned for backup window; zero service disruption."
                }
            }
            return demo_responses.get(turn, demo_responses[3])
        
        elif "payment" in alert or "ALERT-7712" in alert:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "lookup",
                    "step_by_step_thinking": "Forensics found botnet IPs. Checking threat intel for 203.0.113.19.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Botnet indicator from VPC logs", "verification_check": "Direct from forensics"},
                    "confidence": 0.95,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "203.0.113.19"}},
                    "fallback_plan": "Assume unknown botnet if no intel",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "heuristic",
                    "step_by_step_thinking": "Threat intel confirms: Mirai-variant BOTNET_NODE. Simulating WAF rate-limiting and SYN proxy at edge.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Botnet attribution confirmed", "verification_check": "Edge mitigation appropriate"},
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "enable_ddos_rate_limiting", "target_service": "payment-gateway-ap"}},
                    "fallback_plan": "Traffic scrubbing if edge mitigation insufficient",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "DDoS mitigation simulation successful: WAF rate-limiting + SYN proxy. Canary health checks pass. Zero disruption.",
                    "self_check": {"is_reasonable": True, "sanity_notes": "Edge absorption verified", "verification_check": "Legitimate traffic confirmed"},
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "Maintain rules 24h",
                    "is_final_step": True,
                    "final_remediation_summary": "ThreatIntelAgent confirmed: Mirai-variant botnet DDoS. WAF rate-limiting + SYN proxy enabled; zero service disruption."
                }
            }
            return demo_responses.get(turn, demo_responses[3])
        
        return {
            "step_number": turn,
            "reasoning_type": "verification",
            "step_by_step_thinking": "Demo complete",
            "self_check": {"is_reasonable": True, "sanity_notes": "Demo", "verification_check": "Demo"},
            "confidence": 0.95,
            "tool_calls": None,
            "tool_call": None,
            "fallback_plan": "Demo",
            "is_final_step": True,
            "final_remediation_summary": "Demo completed."
        }


def _run_subagent_loop(
    name: str,
    system_prompt: str,
    allowed_tools: set,
    initial_message: str,
    max_turns: int = 3,
    demo_mode: bool = False,
    alert: str = "",
    forensic_summary: str = "",
    provider: Optional[str] = None
) -> Tuple[List[Dict], str]:
    """Generic sub-agent loop. Returns (trace_events, final_summary)."""
    messages = [{"role": "user", "content": initial_message}]
    trace: List[Dict[str, Any]] = []
    final_summary = ""

    for turn_idx in range(1, max_turns + 1):
        t0 = time.time()
        print(f"    [{name}] Turn {turn_idx}/{max_turns}...")
        
        if demo_mode:
            raw = json.dumps(_call_demo_subagent(name, alert, forensic_summary, turn_idx))
            latency = 0
        else:
            raw = _call_llm(system_prompt, messages, provider)
            latency = int((time.time() - t0) * 1000)

        try:
            out = _parse(raw)
        except Exception as e:
            print(f"    [{name}] Parse error: {e}")
            break

        tool_calls = out.get_all_tool_calls()
        # Filter to allowed tools only
        allowed_calls = [tc for tc in tool_calls if tc.name in allowed_tools]

        trace.append({
            "subagent": name,
            "turn": turn_idx,
            "latency_ms": latency,
            "reasoning_type": out.reasoning_type.value,
            "confidence": out.confidence,
            "tools_called": [tc.name for tc in allowed_calls],
            "is_final": out.is_final_step,
            "provider": "demo" if demo_mode else "gemini-direct",
            "model": "demo-mode" if demo_mode else "gemini-2.5-flash",
            "is_reasonable": out.self_check.is_reasonable,
            "parallel": len(allowed_calls) > 1,
            "escalation_fired": False
        })

        print(f"    [{name}] 🧠 {out.reasoning_type.value.upper()} | 🎯 {out.confidence*100:.0f}% | Tools: {[tc.name for tc in allowed_calls]}")
        messages.append({"role": "assistant", "content": json.dumps(out.model_dump())})

        if out.is_final_step:
            final_summary = out.final_remediation_summary or ""
            break

        if allowed_calls:
            obs_parts = []
            for tc in allowed_calls:
                fn = TOOL_REGISTRY.get(tc.name)
                if fn:
                    try:
                        obs = fn(**tc.arguments)
                    except Exception as exc:
                        obs = json.dumps({"error": str(exc)})
                else:
                    obs = json.dumps({"error": f"Tool {tc.name} not in registry"})
                obs_parts.append(f"TOOL OBSERVATION for {tc.name}:\n{obs}")
                print(f"    [{name}] 📥 {tc.name} → {len(obs)} bytes")
            messages.append({"role": "user", "content": "\n\n".join(obs_parts) + f"\nProceed to step {turn_idx+1}."})

    return trace, final_summary


def run_forensics_agent(alert: str, demo_mode: bool = False, provider: Optional[str] = None) -> Tuple[List[Dict], str]:
    """Runs ForensicsAgent to gather log + topology evidence."""
    initial = (
        f"INCIDENT ALERT: {alert}\n"
        "Your task: Query system logs and inspect network topology for 'auth-proxy-us-east'. "
        "Gather all forensic evidence and report your findings."
    )
    return _run_subagent_loop(
        name="ForensicsAgent",
        system_prompt=FORENSICS_PROMPT,
        allowed_tools={"query_system_logs", "inspect_network_topology"},
        initial_message=initial,
        max_turns=3,
        demo_mode=demo_mode,
        alert=alert,
        provider=provider
    )


def run_threat_intel_agent(alert: str, forensic_summary: str, demo_mode: bool = False, provider: Optional[str] = None) -> Tuple[List[Dict], str]:
    """Runs ThreatIntelAgent to look up intel and simulate mitigation."""
    initial = (
        f"INCIDENT ALERT: {alert}\n\n"
        f"FORENSIC EVIDENCE FROM ForensicsAgent:\n{forensic_summary}\n\n"
        "Your task: Look up threat intelligence for suspicious IPs/CVEs found above, "
        "then simulate the best containment action. Report your mitigation verdict."
    )
    return _run_subagent_loop(
        name="ThreatIntelAgent",
        system_prompt=THREAT_INTEL_PROMPT,
        allowed_tools={"lookup_threat_intel", "simulate_mitigation"},
        initial_message=initial,
        max_turns=3,
        demo_mode=demo_mode,
        alert=alert,
        forensic_summary=forensic_summary,
        provider=provider
    )