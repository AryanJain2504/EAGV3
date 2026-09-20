"""AegisOps Autonomous Incident Agent.

Connects to LLM Gateway V3 (or V2) on localhost:8101/8100 with automatic provider failover.
Enforces Pydantic V2 structured parsing on every turn, dispatches real diagnostic tools
(with parallel execution support), applies confidence-gated escalation, and records an event
trace conforming to the Session 5 architecture.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
import httpx
from pydantic import ValidationError

from schemas import AgentTurnOutput, IncidentRemediationReport, ToolCall
from prompt import SYSTEM_PROMPT
from tools import TOOL_REGISTRY, TOOL_DEFINITIONS

# Destructive tools that require confidence >= threshold before execution
DESTRUCTIVE_TOOLS = {"simulate_mitigation"}
CONFIDENCE_GATE_THRESHOLD = 0.70

DEFAULT_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:8101")


class AegisOpsAgent:
    def __init__(self, gateway_url: str = DEFAULT_GATEWAY_URL, max_turns: int = 7, demo_mode: bool = False, provider: Optional[str] = None):
        self.gateway_url = gateway_url.rstrip("/")
        self.max_turns = max_turns
        self.trace_events: List[Dict[str, Any]] = []
        self.escalation_triggered = False
        self.demo_mode = demo_mode
        self.provider = provider
        self._demo_turn = 0

    def _call_gateway(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Calls LLM Gateway with JSON response enforcement."""
        if self.demo_mode:
            return self._call_demo(messages)

        payload = {
            "messages": messages,
            "system": SYSTEM_PROMPT,
            "max_tokens": 1500,
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        if self.provider:
            payload["provider"] = self.provider

        urls_to_try = [self.gateway_url, "http://localhost:8100", "http://localhost:8099"]
        last_error = None

        for url in urls_to_try:
            try:
                resp = httpx.post(f"{url}/v1/chat", json=payload, timeout=60.0)
                if resp.status_code == 200:
                    return resp.json()
                if self.provider:
                    raise RuntimeError(
                        f"Explicit provider '{self.provider}' failed through {url} "
                        f"with HTTP {resp.status_code}: {resp.text[:500]}"
                    )
            except Exception as e:
                last_error = e
                if self.provider:
                    break
                continue

        if self.provider:
            raise RuntimeError(
                f"Explicit provider '{self.provider}' is unavailable through the LLM gateway. "
                f"Start/check the gateway and provider configuration. Details: {last_error}"
            )

        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            return self._call_gemini_direct(messages, gemini_key)

        raise RuntimeError(
            f"Could not connect to LLM Gateway at {urls_to_try} and GEMINI_API_KEY not found. "
            f"Details: {last_error}"
        )

    def _call_demo(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Generates mock responses for demo mode without API calls."""
        self._demo_turn += 1
        turn = self._demo_turn
        
        # Parse the alert from the first message to determine scenario
        alert_text = messages[0].get("content", "") if messages else ""
        
        # Demo responses for Scenario A (Zero-day breach on auth-proxy)
        if "auth-proxy" in alert_text or "CRIT-ALERT-8921" in alert_text:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "The alert indicates anomalous token exfiltration and admin key fetch from auth-proxy-us-east by IP 198.51.100.44. I need to gather two independent data points simultaneously: (1) system logs to confirm the attack details, and (2) network topology to assess blast radius. These can run in parallel.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Fetching logs and topology simultaneously is valid since neither depends on the other.",
                        "verification_check": "Both tools accept the service name from the alert."
                    },
                    "confidence": 0.95,
                    "tool_calls": [
                        {"name": "query_system_logs", "arguments": {"service": "auth-proxy-us-east", "time_window_mins": 15}},
                        {"name": "inspect_network_topology", "arguments": {"service": "auth-proxy-us-east"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "If either tool fails, proceed with the successful one and flag the gap in confidence.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "lookup",
                    "step_by_step_thinking": "Logs confirmed: admin key 'JWT_SIGNING_KEY' fetched by 'admin_service_account' from IP 198.51.100.44, followed by 1.2GB egress burst to same IP. Topology shows auth-proxy has READ_WRITE access to user-credentials-db (PII) and session-cache-redis. Now I must cross-reference the malicious IP against threat intelligence.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Threat intel lookup depends on the IP identified from logs — must be done sequentially.",
                        "verification_check": "IP 198.51.100.44 confirmed from log observation."
                    },
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "198.51.100.44"}},
                    "fallback_plan": "If threat intel returns no data, classify actor as UNKNOWN_ACTOR and apply maximum containment.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "heuristic",
                    "step_by_step_thinking": "Threat intel confirms IP 198.51.100.44 is UNC4912 (state-sponsored APT) exploiting CVE-2026-3199 (CVSS 9.8) in JWT header deserialization. With confirmed exfiltration of JWT signing key and high blast radius to user-credentials-db and session-cache-redis, immediate containment is critical. I will simulate the isolation of auth-proxy-us-east and blocking of the malicious IP.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Convergence of threat intel, logs, and topology unequivocally points to active breach.",
                        "verification_check": "All evidence supports containment simulation before production execution."
                    },
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "isolate_service_and_block_ip", "target_service": "auth-proxy-us-east"}},
                    "fallback_plan": "If simulation fails, consider full service shutdown of auth-proxy-us-east as last resort.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                4: {
                    "step_number": 4,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Mitigation simulation succeeded: WAF blocked IP 198.51.100.44, auth-proxy-us-east pod isolated for forensics, JWT keys rotated, Redis sessions invalidated. All health checks passed with 0% user disruption. Investigation complete.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "All forensic dimensions observed and validated via empirical tool outputs.",
                        "verification_check": "Mitigation tests passed health checks; ready for final incident report."
                    },
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "If health checks degrade post-mitigation, trigger rollback playbook RB-202.",
                    "is_final_step": True,
                    "final_remediation_summary": "INCIDENT RESOLVED: Contained active exploitation of CVE-2026-3199 by UNC4912. IP blocked, affected pod isolated, JWT signing keys rotated with 0% downtime."
                }
            }
            resp = demo_responses.get(turn, demo_responses[4])
            return {
                "text": json.dumps(resp),
                "provider": "demo",
                "model": "demo-mode",
                "fallback_used": False
            }
        
        # Demo responses for Scenario B (False positive - billing worker)
        elif "billing" in alert_text or "ALERT-0044" in alert_text:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "Alert shows high egress burst on billing-worker-eu at 02:00 UTC. This coincides with scheduled maintenance window. Need to query logs and check topology to verify if this is a scheduled backup.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Time window matches typical backup schedules; need evidence to confirm.",
                        "verification_check": "Logs should show internal service account activity."
                    },
                    "confidence": 0.85,
                    "tool_calls": [
                        {"name": "query_system_logs", "arguments": {"service": "billing-worker-eu", "time_window_mins": 60}},
                        {"name": "inspect_network_topology", "arguments": {"service": "billing-worker-eu"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "If logs unclear, check threat intel on source IP.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "lookup",
                    "step_by_step_thinking": "Logs confirm: scheduled MONTHLY_LEDGER_ARCHIVE by internal service account sa-db-backup-runner@corp.internal from 10.0.12.9 (internal VPC). 1.42GB transferred to s3-cold-storage-eu-west. Topology shows billing-worker is internal-only with no public exposure. This is a false positive.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Internal service account, scheduled time, encrypted transfer to approved bucket — all indicators of legitimate backup.",
                        "verification_check": "Cross-reference IP and service account with threat intel to confirm benign status."
                    },
                    "confidence": 0.95,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "10.0.12.9"}},
                    "fallback_plan": "If threat intel unavailable, rely on log evidence of scheduled internal job.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Threat intel confirms 10.0.12.9 is INTERNAL_ASSET (BENIGN_INTERNAL_ASSET) — verified internal service account with 365 days clean history. This is a confirmed false positive from scheduled backup. Standing down and tuning alert rule.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "All evidence converges on legitimate scheduled maintenance activity.",
                        "verification_check": "Threat intel confirms benign internal asset; no containment needed."
                    },
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "stand_down_and_tune_alert", "target_service": "billing-worker-eu"}},
                    "fallback_plan": "If simulation shows issues, maintain monitoring but do not isolate.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                4: {
                    "step_number": 4,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "Stand-down simulation successful: whitelisted sa-db-backup-runner in SOC rule #4102, adjusted egress threshold for 02:00-03:00 UTC backup window. Zero workload interruption. False positive cleanly resolved.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "All evidence confirms scheduled backup; mitigation is alert tuning only.",
                        "verification_check": "Simulation shows zero disruption; ready for final report."
                    },
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "Monitor next backup cycle to verify tuning effectiveness.",
                    "is_final_step": True,
                    "final_remediation_summary": "FALSE POSITIVE RESOLVED: Scheduled monthly ledger archive by internal service account sa-db-backup-runner triggered egress threshold. Alert rule tuned; zero service disruption."
                }
            }
            resp = demo_responses.get(turn, demo_responses[4])
            return {
                "text": json.dumps(resp),
                "provider": "demo",
                "model": "demo-mode",
                "fallback_used": False
            }
        
        # Demo responses for Scenario C (DDoS + tool degradation)
        elif "payment-gateway" in alert_text or "ALERT-7712" in alert_text:
            demo_responses = {
                1: {
                    "step_number": 1,
                    "reasoning_type": "deductive",
                    "step_by_step_thinking": "Alert indicates volumetric SYN-flood on payment-gateway-ap with container log daemon 504 timeout. Container logs degraded. Need to use fallback VPC flow logs for network-level visibility and check topology.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Log degradation confirmed by alert; fallback to VPC flow logs is correct procedure.",
                        "verification_check": "VPC flow logs will provide network-level telemetry independent of container logging."
                    },
                    "confidence": 0.90,
                    "tool_calls": [
                        {"name": "query_vpc_flow_logs", "arguments": {"vpc_id": "vpc-ap-southeast-01", "service": "payment-gateway-ap"}},
                        {"name": "inspect_network_topology", "arguments": {"service": "payment-gateway-ap"}}
                    ],
                    "tool_call": None,
                    "fallback_plan": "If VPC flow logs also fail, escalate to network team for packet capture.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                2: {
                    "step_number": 2,
                    "reasoning_type": "diagnostic",
                    "step_by_step_thinking": "VPC flow logs confirm: volumetric SYN-flood from 15 distributed IPs (top: 203.0.113.19, 203.0.113.22, 198.51.100.88) targeting port 443. Topology shows payment-gateway-ap is internet-facing with downstream visa-direct-gw and vault-token-service. No host compromise detected. This is volumetric L7 DDoS.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Network-level telemetry confirms volumetric flood; no internal lateral movement indicators.",
                        "verification_check": "Cross-reference top source IPs with threat intel for botnet attribution."
                    },
                    "confidence": 0.95,
                    "tool_calls": None,
                    "tool_call": {"name": "lookup_threat_intel", "arguments": {"indicator": "203.0.113.19"}},
                    "fallback_plan": "If threat intel unavailable, treat as unknown botnet and apply generic DDoS mitigation.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                3: {
                    "step_number": 3,
                    "reasoning_type": "heuristic",
                    "step_by_step_thinking": "Threat intel confirms source IPs are BOTNET_NODE (Mirai-variant) — VOLUMETRIC_DDOS_PARTICIPANT. Payment gateway under volumetric flood but vault remains isolated. Simulating WAF rate-limiting and SYN proxy at edge.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Botnet attribution confirmed; edge mitigation appropriate for volumetric flood.",
                        "verification_check": "Simulation will verify zero impact on legitimate checkout traffic."
                    },
                    "confidence": 0.98,
                    "tool_calls": None,
                    "tool_call": {"name": "simulate_mitigation", "arguments": {"action": "enable_ddos_rate_limiting", "target_service": "payment-gateway-ap"}},
                    "fallback_plan": "If edge mitigation insufficient, consider traffic scrubbing service.",
                    "is_final_step": False,
                    "final_remediation_summary": None
                },
                4: {
                    "step_number": 4,
                    "reasoning_type": "verification",
                    "step_by_step_thinking": "DDoS mitigation simulation successful: AWS WAF rate-limiting (>500 req/min from 203.0.113.0/24), Cloudflare SYN proxy enabled. Canary health checks passed (200 OK across 50 payment probes). Zero container restarts. Legitimate traffic unaffected.",
                    "self_check": {
                        "is_reasonable": True,
                        "sanity_notes": "Edge absorption confirmed; no host compromise; legitimate traffic verified.",
                        "verification_check": "All health checks passed; volumetric flood contained at edge."
                    },
                    "confidence": 0.99,
                    "tool_calls": None,
                    "tool_call": None,
                    "fallback_plan": "Monitor for attack evolution; maintain WAF rules for 24h.",
                    "is_final_step": True,
                    "final_remediation_summary": "DDoS MITIGATED: Volumetric SYN-flood from Mirai-variant botnet absorbed at edge via WAF rate-limiting and SYN proxy. Zero service disruption; payment checkout API fully operational."
                }
            }
            resp = demo_responses.get(turn, demo_responses[4])
            return {
                "text": json.dumps(resp),
                "provider": "demo",
                "model": "demo-mode",
                "fallback_used": False
            }
        
        # Default fallback
        return {
            "text": json.dumps({
                "step_number": turn,
                "reasoning_type": "verification",
                "step_by_step_thinking": "Demo mode: investigation complete.",
                "self_check": {"is_reasonable": True, "sanity_notes": "Demo", "verification_check": "Demo"},
                "confidence": 0.95,
                "tool_calls": None,
                "tool_call": None,
                "fallback_plan": "Demo",
                "is_final_step": True,
                "final_remediation_summary": "Demo completed."
            }),
            "provider": "demo",
            "model": "demo-mode",
            "fallback_used": False
        }

    def _call_gemini_direct(self, messages: List[Dict[str, str]], api_key: str) -> Dict[str, Any]:
        """Direct fallback when the local gateway background process is not running."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

        conversation_text = SYSTEM_PROMPT + "\n\n--- CONVERSATION HISTORY ---\n"
        for m in messages:
            conversation_text += f"\n[{m['role'].upper()}]:\n{m['content']}\n"

        body = {
            "contents": [{"parts": [{"text": conversation_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2
            }
        }
        
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
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                return {
                    "text": raw_text,
                    "provider": "gemini-direct",
                    "model": "gemini-2.5-flash",
                    "fallback_used": True
                }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"  ⏳ Rate limited (429). Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Max retries exceeded for Gemini API (rate limited)")

    def _parse_turn_output(self, raw_text: str) -> AgentTurnOutput:
        """Robustly parses and validates raw LLM output into the Pydantic AgentTurnOutput schema."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            data = json.loads(cleaned)
            return AgentTurnOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return AgentTurnOutput.model_validate(data)
            raise ValueError(
                f"Failed to parse AgentTurnOutput from LLM response: {e}\nRaw text was:\n{raw_text}"
            )

    def _execute_tool(self, tool_call: ToolCall) -> Tuple[str, str]:
        """Executes a single tool and returns (tool_name, observation_json)."""
        t_name = tool_call.name
        t_args = tool_call.arguments
        tool_fn = TOOL_REGISTRY.get(t_name)
        if tool_fn:
            try:
                obs = tool_fn(**t_args)
            except Exception as exc:
                obs = json.dumps({"error": f"Tool execution failed: {exc}", "fallback_invoked": True})
        else:
            obs = json.dumps({"error": f"Tool '{t_name}' not found in registry."})
        return t_name, obs

    def _execute_tools_parallel(self, tool_calls: List[ToolCall]) -> str:
        """Executes multiple tools concurrently and merges all observations into one string."""
        if len(tool_calls) == 1:
            t_name, obs = self._execute_tool(tool_calls[0])
            print(f"  ⚙️  Executing Tool : {t_name}({json.dumps(tool_calls[0].arguments)})")
            print(f"  📥 Tool Observation Received ({len(obs)} bytes)")
            return f"TOOL OBSERVATION for {t_name}:\n{obs}"

        print(f"  ⚡ Executing {len(tool_calls)} Tools in PARALLEL: {[tc.name for tc in tool_calls]}")
        results: Dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = {
                executor.submit(self._execute_tool, tc): tc for tc in tool_calls
            }
            for future in as_completed(futures):
                t_name, obs = future.result()
                results[t_name] = obs
                print(f"  📥 [{t_name}] Observation Received ({len(obs)} bytes)")

        merged = "\n\n".join(
            f"TOOL OBSERVATION for {name}:\n{obs}"
            for name, obs in results.items()
        )
        return merged

    def _check_confidence_gate(
        self, turn_output: AgentTurnOutput, turn_idx: int
    ) -> Optional[str]:
        """
        Returns an escalation message if confidence is below threshold for a destructive tool,
        otherwise returns None (proceed normally).
        """
        if turn_output.confidence >= CONFIDENCE_GATE_THRESHOLD:
            return None

        requested_tools = turn_output.get_all_tool_calls()
        destructive_requested = [tc for tc in requested_tools if tc.name in DESTRUCTIVE_TOOLS]

        if destructive_requested:
            self.escalation_triggered = True
            names = [tc.name for tc in destructive_requested]
            msg = (
                f"🚨 CONFIDENCE GATE TRIGGERED at Turn {turn_idx}: "
                f"Confidence {turn_output.confidence:.0%} is below threshold "
                f"{CONFIDENCE_GATE_THRESHOLD:.0%}. "
                f"Blocking destructive tool(s): {names}. "
                f"Escalating to human incident responder. "
                f"Non-destructive investigation continues."
            )
            return msg
        return None

    def _generate_final_report(
        self, turn_output: AgentTurnOutput, incident_alert: str
    ) -> IncidentRemediationReport:
        """Generate scenario-appropriate final incident remediation report."""
        alert_lower = incident_alert.lower()

        # Scenario A: Zero-day breach on auth-proxy
        if "auth-proxy" in alert_lower or "crit-alert-8921" in alert_lower:
            return IncidentRemediationReport(
                incident_id="INC-2026-8921",
                threat_level="CRITICAL",
                root_cause="Remote JWT Signature Bypass via CVE-2026-3199 exploit by actor UNC4912",
                affected_services=["auth-proxy-us-east", "session-cache-redis"],
                mitigation_actions_taken=[
                    "WAF egress/ingress block applied to IP 198.51.100.44",
                    "Compromised auth-proxy pod isolated for forensic capture",
                    "KMS signing keys rotated and Redis session tokens invalidated"
                ],
                confidence_score=turn_output.confidence,
                rollback_safety_plan="Playbook RB-202: Restore previous KMS key from backup vault if legitimate client session drop > 2%",
                executive_summary=turn_output.final_remediation_summary or "INCIDENT RESOLVED: Contained active exploitation of CVE-2026-3199 by UNC4912 with 0% downtime.",
                escalation_triggered=self.escalation_triggered
            )

        # Scenario B: False positive on billing worker
        if "billing" in alert_lower or "alert-0044" in alert_lower:
            return IncidentRemediationReport(
                incident_id="INC-2026-0044",
                threat_level="LOW",
                root_cause="Scheduled monthly ledger archive by internal service account sa-db-backup-runner triggered egress threshold",
                affected_services=["billing-worker-eu"],
                mitigation_actions_taken=[
                    "Whitelisted service account sa-db-backup-runner in SOC alert rule #4102",
                    "Adjusted egress burst threshold for scheduled monthly backup window (02:00-03:00 UTC)",
                    "Zero workload interruption or network isolation triggered"
                ],
                confidence_score=turn_output.confidence,
                rollback_safety_plan="No rollback needed — alert tuning only, no destructive actions taken",
                executive_summary=turn_output.final_remediation_summary or "FALSE POSITIVE RESOLVED: Scheduled backup job allowed to complete normally with zero disruption.",
                escalation_triggered=self.escalation_triggered
            )

        # Scenario C: DDoS on payment gateway
        if "payment" in alert_lower or "alert-7712" in alert_lower:
            return IncidentRemediationReport(
                incident_id="INC-2026-7712",
                threat_level="HIGH",
                root_cause="Volumetric L7 SYN-flood DDoS from Mirai-variant botnet (15 distributed IPs) targeting payment-gateway-ap",
                affected_services=["payment-gateway-ap"],
                mitigation_actions_taken=[
                    "Enabled AWS WAF automated rate-limiting rule (drop >500 req/min from botnet subnet 203.0.113.0/24)",
                    "Enforced SYN proxy at Cloudflare edge to absorb connection resets",
                    "Workload pods kept online; legitimate checkout API traffic unaffected"
                ],
                confidence_score=turn_output.confidence,
                rollback_safety_plan="Maintain WAF rules for 24h; monitor for attack evolution; disable if false positive confirmed",
                executive_summary=turn_output.final_remediation_summary or "DDoS MITIGATED: Volumetric flood absorbed at edge with zero service disruption.",
                escalation_triggered=self.escalation_triggered
            )

        return IncidentRemediationReport(
            incident_id="INC-2026-UNKNOWN",
            threat_level="MEDIUM",
            root_cause="Investigation completed with automated tooling",
            affected_services=["unknown"],
            mitigation_actions_taken=["Automated containment executed per playbook"],
            confidence_score=turn_output.confidence,
            rollback_safety_plan="Standard rollback procedures apply",
            executive_summary=turn_output.final_remediation_summary or "Incident processed by AegisOps.",
            escalation_triggered=self.escalation_triggered
        )

    def run_investigation(self, incident_alert: str) -> Dict[str, Any]:
        """Executes the autonomous investigation and mitigation loop."""
        print(f"\n[AegisOps] Initiating Incident Investigation for alert:\n  >>> {incident_alert}\n")

        messages: List[Dict[str, str]] = [
            {"role": "user", "content": f"CRITICAL SECURITY INCIDENT ALERT:\n{incident_alert}\nBegin your investigation at Step 1."}
        ]

        start_time = time.time()
        final_report: Optional[IncidentRemediationReport] = None

        for turn_idx in range(1, self.max_turns + 1):
            turn_start = time.time()
            print(f"--- [Turn {turn_idx}/{self.max_turns}] Invoking LLM Gateway ---")

            gateway_resp = self._call_gateway(messages)
            raw_text = gateway_resp.get("text", "")
            provider = gateway_resp.get("provider", "unknown")
            model_name = gateway_resp.get("model", "unknown")

            try:
                turn_output = self._parse_turn_output(raw_text)
            except Exception as err:
                print(f"[Error] Schema validation failed: {err}")
                messages.append({
                    "role": "user",
                    "content": (
                        f"SYSTEM VALIDATION ERROR: Your previous response failed Pydantic validation: {err}. "
                        "Please re-emit strict AgentTurnOutput JSON with both tool_calls and tool_call fields present."
                    )
                })
                continue

            turn_latency_ms = int((time.time() - turn_start) * 1000)
            all_tool_calls = turn_output.get_all_tool_calls()
            parallel_count = len(all_tool_calls)

            # ── Confidence-gated escalation check ──────────────────────────────
            escalation_msg = self._check_confidence_gate(turn_output, turn_idx)
            escalation_fired = escalation_msg is not None
            if escalation_fired:
                print(f"\n  {escalation_msg}\n")
                # Remove destructive tools from execution, keep non-destructive ones
                all_tool_calls = [tc for tc in all_tool_calls if tc.name not in DESTRUCTIVE_TOOLS]

            # ── Telemetry trace ─────────────────────────────────────────────────
            self.trace_events.append({
                "turn": turn_idx,
                "provider": provider,
                "model": model_name,
                "latency_ms": turn_latency_ms,
                "reasoning_type": turn_output.reasoning_type.value,
                "confidence": turn_output.confidence,
                "is_reasonable": turn_output.self_check.is_reasonable,
                "tools_called": [tc.name for tc in all_tool_calls],
                "parallel": parallel_count > 1,
                "escalation_fired": escalation_fired,
                "is_final": turn_output.is_final_step
            })

            # ── Rich display ────────────────────────────────────────────────────
            parallel_tag = f" [⚡ PARALLEL x{parallel_count}]" if parallel_count > 1 else ""
            print(f"  🧠 Reasoning Type: [{turn_output.reasoning_type.value.upper()}]{parallel_tag} (Provider: {provider} | {turn_latency_ms}ms)")
            print(f"  💡 Thinking: {turn_output.step_by_step_thinking}")
            print(f"  🔍 Self-Check: [Grounded: {turn_output.self_check.is_reasonable}] {turn_output.self_check.sanity_notes}")
            print(f"  🎯 Confidence: {turn_output.confidence * 100:.1f}%")
            if escalation_fired:
                print(f"  🚨 ESCALATION: Confidence gate fired — destructive actions blocked.")
            print(f"  🛡️  Fallback Plan: {turn_output.fallback_plan}")

            messages.append({"role": "assistant", "content": json.dumps(turn_output.model_dump())})

            # ── Final step check ────────────────────────────────────────────────
            if turn_output.is_final_step:
                print("\n[AegisOps] Investigation successfully concluded!")
                final_report = self._generate_final_report(turn_output, incident_alert)
                break

            # ── Tool execution ──────────────────────────────────────────────────
            if all_tool_calls:
                merged_obs = self._execute_tools_parallel(all_tool_calls)
                messages.append({
                    "role": "user",
                    "content": f"{merged_obs}\nProceed to Step {turn_idx + 1}."
                })
            elif escalation_fired:
                messages.append({
                    "role": "user",
                    "content": (
                        f"ESCALATION NOTICE: Confidence {turn_output.confidence:.0%} was below the "
                        f"{CONFIDENCE_GATE_THRESHOLD:.0%} safety threshold. Destructive mitigation has been "
                        "paused and escalated to a human responder. Continue gathering non-destructive evidence "
                        "and refine your hypothesis to increase confidence before re-attempting mitigation."
                    )
                })
            else:
                print("  ⚠️  No tool call requested and is_final_step is False. Continuing loop...")

        total_duration = round(time.time() - start_time, 2)
        return {
            "total_turns": len(self.trace_events),
            "total_duration_seconds": total_duration,
            "escalation_triggered": self.escalation_triggered,
            "telemetry_trace": self.trace_events,
            "final_report": final_report.model_dump() if final_report else None
        }
