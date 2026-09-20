# Assignment 5: AegisOps - Autonomous Cyber Incident Triage & Zero-Day Mitigation Orchestrator

An enterprise-grade autonomous SecOps orchestrator built using **LLM Gateway architecture** and **Pydantic V2** for structured multi-turn reasoning, self-verifications, and automated tool execution.

---

## 1. System Architecture & Flow

### LLM Execution Scope

AegisOps does not call the model directly from the agent loop during normal
execution. All LLM requests are routed through the local `llm_gatewayV3`
service at `http://localhost:8101/v1/chat`. The gateway uses Ollama as the
active local provider for development and demo runs, giving the agent a stable
provider boundary and avoiding direct model-specific coupling in `agent.py`.

The security tools in `tools.py` are deterministic scenario adapters for safe
rehearsal. In a production deployment, these adapters would be replaced with
real SIEM, network telemetry, threat-intelligence, and approved remediation
integrations while keeping the agent contract unchanged.

AegisOps monitors production telemetry and executes a deterministic **ReAct (Reason $\rightarrow$ Act $\rightarrow$ Observe $\rightarrow$ Verify)** loop to investigate and contain zero-day attacks without service disruption.

```mermaid
flowchart TD
    A[🚨 SOC Alert Ingested] -->|Turn 1: Deductive Reasoning| B[Triage & Hypothesize]
    B -->|Tools: query_system_logs + inspect_network_topology (parallel)| C[Turn 2: Diagnostic Reasoning]
    C -->|Tool: lookup_threat_intel| D[Turn 3: Threat Intel & CVE Cross-Check]
    D -->|Internal Self-Check| E[Turn 4: Heuristic Containment]
    E -->|Tool: simulate_mitigation| F[Turn 5: Verification & Health Probes]
    F -->|Pydantic V2 Final Verdict| G[📋 IncidentRemediationReport & Rollback Plan]
```

Two execution modes share one entry point (`main.py --mode`):
- **single** — one `AegisOpsAgent` runs the full ReAct loop (max 7 turns).
- **multi** — `SupervisorAgent` runs `ForensicsAgent` (logs + topology) to completion, feeds its findings into `ThreatIntelAgent` (intel + mitigation simulation), then synthesizes a unified `[MULTI-AGENT VERDICT]` report.

### Core Design Principles
1. **Multi-Turn ReAct Loop**: Consecutive turns where tool observations directly guide subsequent actions.
2. **Pydantic V2 Type Safety**: Strict typed validation on inputs, intermediate reasoning turns (`AgentTurnOutput`), and final verdicts (`IncidentRemediationReport`). Robust parsing with validation-error feedback for self-correction.
3. **Gateway Resiliency**: `localhost:8101 (V3) → 8100 (V2) → 8099 (V1) → direct Gemini`, with exponential-backoff retries on 429s.
4. **Self-Verification**: Explicit sanity checks (`self_check.is_reasonable`) to guard against destructive automated misconfigurations.
5. **Quantitative Confidence**: Mandatory `confidence: float` (0.0 to 1.0) emitted on every single turn.
6. **Confidence-Gated Escalation**: `simulate_mitigation` is blocked below 0.70 confidence and escalated to a human responder.
7. **Parallel Tool Dispatch**: Independent tools (logs + topology) execute concurrently via `ThreadPoolExecutor`.

---

## 2. Prompt Evaluation Assistant Scorecard

The core agent prompt in [`prompt.py`](./prompt.py) was qualified against the **9-point Prompt Evaluation Assistant Rubric** (`uv run python validate_prompt.py` → [`prompt_evaluation_result.json`](./prompt_evaluation_result.json)):

```json
{
  "explicit_reasoning": true,
  "structured_output": true,
  "tool_separation": true,
  "conversation_loop": true,
  "instructional_framing": true,
  "internal_self_checks": true,
  "reasoning_type_awareness": true,
  "fallbacks": true,
  "overall_clarity": "Exemplary agentic prompt. Enforces rigorous Chain-of-Thought reasoning, strict Pydantic JSON schemas, complete tool/thought separation, multi-turn state handoffs, internal sanity self-checks, explicit reasoning modality tagging, robust fallback procedures, and quantitative confidence scores."
}
```

### Criteria Breakdown:
| # | Evaluation Criteria | How AegisOps Implements It | Status |
|---|---|---|:---:|
| 1 | **Explicit Reasoning Instructions** | Explicit `step_by_step_thinking` mandatory on every turn | `true` |
| 2 | **Structured Output Format** | Strict Pydantic `AgentTurnOutput` JSON schema | `true` |
| 3 | **Separation of Reasoning & Tools** | Reasoning isolated in thoughts; tool calls isolated in `{name, arguments}` | `true` |
| 4 | **Conversation Loop Support** | Multi-turn loop where observations feed into subsequent turns | `true` |
| 5 | **Instructional Framing** | Few-shot concrete examples for parallel, single-tool, and final steps | `true` |
| 6 | **Internal Self-Checks** | Mandatory `self_check: {is_reasonable, sanity_notes, verification_check}` | `true` |
| 7 | **Reasoning Type Awareness** | Tagged modality: `deductive`, `lookup`, `diagnostic`, `arithmetic`, `heuristic`, `verification` | `true` |
| 8 | **Parallel Tool Dispatch** | `tool_calls` list for independent tools executed concurrently | `true` |
| 9 | **Error Handling, Fallbacks & Confidence** | Mandatory `fallback_plan` + explicit `confidence` score (0.0 to 1.0) on every step | `true` |

> **External qualification (per assignment instructions):** paste [`prompt.py`](./prompt.py) `SYSTEM_PROMPT` into ChatGPT / Claude / Cursor alongside the 9 rules above and save the verdict as `prompt_qualification_external.md`. The static checker in `validate_prompt.py` is supporting evidence, not a substitute for the external LLM judge.

---

## 3. Project Structure

```
assignment5/
├── pyproject.toml                     # Fast package management via UV
├── schemas.py                         # Pydantic V2 models (AgentTurnOutput, IncidentRemediationReport)
├── prompt.py                          # 9-point rubric qualified system prompt & few-shot scaffolds
├── tools.py                           # 5 Enterprise SecOps diagnostic & mitigation tools + registry
├── agent.py                           # Single-agent ReAct loop (parallel tools, confidence gate, demo mode)
├── supervisor.py                      # Multi-agent Supervisor (forensics -> threat-intel -> synthesize)
├── subagents.py                       # ForensicsAgent + ThreatIntelAgent (narrowed prompts, demo mode)
├── validate_prompt.py                 # Automated Prompt Evaluation Assistant verification script
├── main.py                            # Single CLI entry point (--mode, --scenario incl. ALL, --demo)
├── prompt_evaluation_result.json      # Saved 100% pass evaluation scorecard
├── incident_remediation_summary_{mode}_{scenario}.json  # Per-run execution trace & verdict
├── incident_remediation_summary.json  # Legacy filename (last run wins, grader compat)
└── README.md                          # Full system documentation & run instructions
```

### Tools (5)
| Tool | Purpose | Scenario coverage |
|---|---|---|
| `query_system_logs` | Security/access audit logs per microservice | A (exfiltration), B (scheduled backup), C (504 degradation → fallback hint) |
| `query_vpc_flow_logs` | Network-level VPC capture when container logs fail | C (SYN-flood attribution) |
| `inspect_network_topology` | Dependencies, blast radius | A (CRITICAL), B (LOW internal), C (MEDIUM edge) |
| `lookup_threat_intel` | IP / service-account / CVE reputation | UNC4912+CVE-2026-3199, benign internal asset, Mirai botnet |
| `simulate_mitigation` | Sandboxed containment simulation (confidence-gated) | Full isolation, WAF rate-limit, alert stand-down |

---

## 4. Empirical Test Runs & Telemetry

### Live run (real LLM via local gateway, Scenario A single-agent)
`uv run main.py --mode single --scenario A` → `incident_remediation_summary_single_A.json`:

```
provider: gemini (gemini-2.5-flash) | latencies: [5799, 7876, 6691, 7189]ms | 4 turns | 27.56s
```

### Final Pydantic Verdict (`IncidentRemediationReport`):
```json
{
  "incident_id": "INC-2026-8921",
  "threat_level": "CRITICAL",
  "root_cause": "Remote JWT Signature Bypass via CVE-2026-3199 exploit by actor UNC4912",
  "affected_services": ["auth-proxy-us-east", "session-cache-redis"],
  "mitigation_actions_taken": [
    "WAF egress/ingress block applied to IP 198.51.100.44",
    "Compromised auth-proxy pod isolated for forensic capture",
    "KMS signing keys rotated and Redis session tokens invalidated"
  ],
  "confidence_score": 0.99,
  "rollback_safety_plan": "Playbook RB-202: Restore previous KMS key from backup vault if legitimate client session drop > 2%",
  "executive_summary": "INCIDENT RESOLVED: Active exploitation of CVE-2026-3199 by UNC4912 ..."
}
```

### Scenario matrix
| Scenario | Alert | Threat | Single | Multi |
|---|---|---|---|---|
| A | Zero-day exfiltration, auth-proxy, 198.51.100.44 | CRITICAL | ✅ live | ✅ live |
| B | Egress burst, billing-worker (scheduled backup) | LOW (false positive) | ✅ live | ⏳ quota-blocked |
| C | SYN-flood + log daemon 504, payment-gateway | HIGH | ⏳ quota-blocked | ⏳ quota-blocked |

Live = `provider: gemini/gemini-direct` with real latencies. Remaining live runs are blocked on free-tier Gemini quota (`generate_content_free_tier_requests`, limit 20) — add more provider keys (Groq/Cerebras/NVIDIA/OpenRouter/GitHub) to `EAGV3/.env` and re-run.

---

## 5. How to Run Locally

### 0. Start the LLM gateway (required for live runs)
```bash
cd ../llm_gatewayV3
./run.sh        # starts on http://localhost:8101 (reads ../.env for provider keys)
curl -s http://localhost:8101/v1/providers | python3 -m json.tool
```

### 1. Run the Prompt Validator
```bash
cd assignment5
uv run python validate_prompt.py
```

### 2. Run the Autonomous Orchestrator
```bash
uv run main.py                                        # Scenario A, single-agent
uv run main.py --scenario B                           # Scenario B (false positive)
uv run main.py --scenario C                           # Scenario C (DDoS + tool degradation)
uv run main.py --mode multi                           # Multi-agent, Scenario A
uv run main.py --mode multi --scenario ALL            # All scenarios, multi-agent
```

### 3. Demo mode (no API calls, instant, for rehearsals only)
```bash
uv run main.py --demo --mode multi --scenario ALL
```
Demo uses pre-scripted responses (`provider: demo, 0ms`). **Do not submit demo artifacts as live evidence** — the telemetry table exposes them. Demo exists because free-tier quotas rate-limit repeated live runs.

---

## 6. YouTube Demo Video Outline

1. **Architecture Overview (0:00 - 0:45)**:
   - Introduce AegisOps, Pydantic V2 schemas, and LLM Gateway routing.
2. **Prompt Qualifier Scorecard (0:45 - 1:30)**:
   - Run `uv run python validate_prompt.py` showing all 9 rubric criteria evaluate to `true`.
   - Show the external ChatGPT/Claude qualification verdict.
3. **Live Orchestration Run (1:30 - 3:15)**:
   - Run `uv run main.py --mode single --scenario A` showing the live multi-turn trace table, explicit thinking, self-checks, tool execution, and the final remediation report.
4. **Multi-Agent + All Scenarios (3:15 - 4:30)**:
   - Run `uv run main.py --mode multi --scenario ALL` showing supervisor dispatch, sub-agent telemetry, and per-scenario verdicts.
