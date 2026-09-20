import json
from typing import Any, Dict

# ---------------------------------------------------------------------------
# ENTERPRISE SECOPS DIAGNOSTIC & MITIGATION TOOLS (MULTI-SCENARIO)
# ---------------------------------------------------------------------------

def query_system_logs(service: str, time_window_mins: int = 15) -> str:
    """Queries security and access audit logs for a given microservice."""
    service_norm = service.lower().strip()

    # Scenario A: Real Zero-Day Breach on Auth Proxy
    if "auth" in service_norm or "proxy" in service_norm:
        logs = [
            {"timestamp": "2026-09-17T00:02:11Z", "src_ip": "198.51.100.44", "endpoint": "/api/v2/oauth/token", "status": 200, "event": "Token exchange success with abnormal claim header"},
            {"timestamp": "2026-09-17T00:04:35Z", "src_ip": "198.51.100.44", "endpoint": "/api/v2/admin/keys", "status": 200, "event": "Elevated admin privilege key fetch attempt"},
            {"timestamp": "2026-09-17T00:05:12Z", "src_ip": "198.51.100.44", "endpoint": "/api/v2/users/export", "status": 206, "bytes_sent": 842019, "alert": "High egress burst"},
            {"timestamp": "2026-09-17T00:08:44Z", "src_ip": "10.0.4.12 (internal)", "endpoint": "/healthz", "status": 200, "event": "Pod health probe normal"}
        ]
        return json.dumps({
            "service": service,
            "time_window_mins": time_window_mins,
            "total_entries": len(logs),
            "suspicious_ip": "198.51.100.44",
            "anomaly_summary": "Unusual JWT signing key dump and data exfiltration from external IP 198.51.100.44",
            "sample_logs": logs
        }, indent=2)

    # Scenario B: False Positive - Scheduled Internal Backup on Billing Worker
    elif "billing" in service_norm or "worker" in service_norm:
        logs = [
            {"timestamp": "2026-09-17T01:59:00Z", "src_ip": "10.0.12.9 (internal VPC)", "actor": "sa-db-backup-runner@corp.internal", "operation": "MONTHLY_LEDGER_ARCHIVE", "status": 200, "event": "Scheduled database snapshot initiated by cron"},
            {"timestamp": "2026-09-17T02:01:14Z", "src_ip": "10.0.12.9 (internal VPC)", "actor": "sa-db-backup-runner@corp.internal", "destination": "s3-cold-storage-eu-west", "bytes_transferred": 1420000000, "status": 200, "event": "Encrypted ledger archive transfer to cold storage bucket"},
            {"timestamp": "2026-09-17T02:04:30Z", "src_ip": "10.0.12.9 (internal VPC)", "event": "Archive checksum verified (SHA-256). Job status: COMPLETED_SUCCESS"}
        ]
        return json.dumps({
            "service": service,
            "time_window_mins": time_window_mins,
            "total_entries": len(logs),
            "source_ip": "10.0.12.9",
            "actor_identity": "sa-db-backup-runner@corp.internal",
            "anomaly_summary": "High egress burst detected, but correlated with scheduled maintenance window and internal service account.",
            "sample_logs": logs
        }, indent=2)

    # Scenario C: Tool Degradation / Container Log Daemon Timeout on Payment Gateway
    elif "payment" in service_norm or "pay" in service_norm:
        return json.dumps({
            "service": service,
            "status": 504,
            "error": "Log streaming daemon timeout on payment-gateway-ap container. Pod logs temporarily inaccessible.",
            "degradation_state": "TOOL_FAILURE",
            "suggested_fallback": "query_vpc_flow_logs (VPC network level capture)"
        }, indent=2)

    # Default baseline
    return json.dumps({
        "service": service,
        "time_window_mins": time_window_mins,
        "total_entries": 2,
        "anomaly_summary": "No critical anomaly detected. Baseline traffic normal.",
        "sample_logs": [{"timestamp": "2026-09-17T00:05:00Z", "event": "heartbeat ok"}]
    }, indent=2)


def query_vpc_flow_logs(vpc_id: str, service: str = "payment-gateway") -> str:
    """Fallback tool: Queries network-level VPC flow logs when container logging fails."""
    return json.dumps({
        "vpc_id": vpc_id,
        "target_service": service,
        "capture_window_mins": 10,
        "total_packets": 48200,
        "flow_summary": "Volumetric SYN-flood traffic from 15 distributed IPs targeting port 443 with connection resets.",
        "top_sources": [
            {"ip": "203.0.113.19", "packets": 12000, "action": "REJECT"},
            {"ip": "203.0.113.22", "packets": 11400, "action": "REJECT"},
            {"ip": "198.51.100.88", "packets": 9800, "action": "REJECT"}
        ],
        "verdict": "Network-level L7 DDoS attack attempt; no host compromise or internal data exfiltration observed."
    }, indent=2)


def inspect_network_topology(service: str) -> str:
    """Inspects upstream/downstream microservices, VPC peering, and database connectivity."""
    service_norm = service.lower().strip()

    if "billing" in service_norm:
        return json.dumps({
            "service": service,
            "cluster": "prod-eu-west-k8s",
            "namespace": "batch-workers",
            "exposed_publicly": False,
            "ingress": "None (Internal cluster worker only)",
            "downstream_dependencies": [
                {"service": "billing-archive-s3", "type": "Encrypted S3 Bucket", "access_level": "WRITE_ONLY"},
                {"service": "ledger-sql-db", "type": "PostgreSQL (Replica)", "access_level": "READ_ONLY"}
            ],
            "blast_radius_assessment": "LOW: Service is non-publicly facing; acts strictly as an internal batch cron runner. No external egress allowed."
        }, indent=2)

    elif "payment" in service_norm:
        return json.dumps({
            "service": service,
            "cluster": "prod-ap-southeast-k8s",
            "namespace": "payment-pipeline",
            "exposed_publicly": True,
            "ingress": "edge-payment-alb.corp.net",
            "downstream_dependencies": [
                {"service": "vault-token-service", "type": "HashiCorp Vault", "access_level": "ENCRYPT_DECRYPT"},
                {"service": "visa-direct-gw", "type": "External Bank API", "access_level": "HTTPS_OUTBOUND"}
            ],
            "blast_radius_assessment": "MEDIUM: Edge payment receiver under volumetric pressure, but upstream vault remains isolated."
        }, indent=2)

    # Scenario A Default: Auth Proxy
    return json.dumps({
        "service": service,
        "cluster": "prod-us-east-k8s",
        "namespace": "core-services",
        "exposed_publicly": True,
        "ingress": "alb-external-auth.corp.net",
        "downstream_dependencies": [
            {"service": "user-credentials-db", "type": "PostgreSQL (RDS)", "contains_pii": True, "access_level": "READ_WRITE"},
            {"service": "kms-key-vault", "type": "AWS KMS / Vault", "contains_secrets": True, "access_level": "SIGNING_ONLY"},
            {"service": "session-cache-redis", "type": "Redis Cluster", "contains_tokens": True, "access_level": "READ_WRITE"}
        ],
        "blast_radius_assessment": "CRITICAL: If auth-proxy credentials compromised, attacker has direct network path to user-credentials-db and session-cache-redis."
    }, indent=2)


def lookup_threat_intel(indicator: str) -> str:
    """Cross-references an IP address, domain, or CVE identifier against Threat Intelligence feeds."""
    ind = indicator.strip().upper()

    # Scenario B: Internal Service Account or Internal IP (Benign)
    if "10.0.12.9" in indicator or "sa-db-backup" in indicator.lower():
        return json.dumps({
            "indicator": indicator,
            "type": "INTERNAL_ASSET",
            "reputation_score": 0,
            "threat_classification": "BENIGN_INTERNAL_ASSET",
            "details": "Verified internal service account used for scheduled automated database snapshots. Clean history across 365 days."
        }, indent=2)

    # Scenario C: DDoS Botnet IPs
    elif "203.0.113" in indicator or "198.51.100.88" in indicator:
        return json.dumps({
            "indicator": indicator,
            "type": "BOTNET_NODE",
            "reputation_score": 92,
            "threat_classification": "VOLUMETRIC_DDOS_PARTICIPANT",
            "details": "Known member of Mirai-variant scanning botnet. Engages in indiscriminate port 443 SYN flooding."
        }, indent=2)

    # Scenario A: Zero-day threat actor UNC4912 / CVE-2026-3199
    elif "198.51.100.44" in indicator:
        return json.dumps({
            "indicator": indicator,
            "type": "IPV4",
            "reputation_score": 98,
            "threat_classification": "MALICIOUS_ACTOR",
            "known_threat_actor": "UNC4912 (Automated Exploit Framework)",
            "associated_cve": "CVE-2026-3199",
            "geo": "Unknown / Bulletproof VPS",
            "details": "Actively scanning and exploiting zero-day vulnerability in microservice auth proxy JWT header deserialization."
        }, indent=2)

    elif "CVE-2026-3199" in ind or "CVE" in ind:
        return json.dumps({
            "indicator": "CVE-2026-3199",
            "type": "VULNERABILITY",
            "cvss_v3_score": 9.8,
            "severity": "CRITICAL",
            "attack_vector": "NETWORK",
            "privileges_required": "NONE",
            "vulnerability_type": "Improper Input Validation leading to Remote JWT Signature Bypass and Key Leak",
            "recommended_action": "Immediately revoke current signing key pair, rotate KMS keys, and isolate compromised pods."
        }, indent=2)

    return json.dumps({
        "indicator": indicator,
        "type": "UNKNOWN",
        "reputation_score": 0,
        "details": "No known malicious signatures or intelligence hits found."
    }, indent=2)


def simulate_mitigation(action: str, target_service: str) -> str:
    """Simulates or applies an automated containment and remediation policy in a sandboxed staging mesh."""
    action_norm = action.lower()

    # Scenario B: Standing Down on False Positive
    if "stand" in action_norm or "benign" in action_norm or "no action" in action_norm or "whitelist" in action_norm:
        return json.dumps({
            "action": action,
            "target_service": target_service,
            "simulation_status": "SUCCESS",
            "action_executed": "STAND_DOWN_AND_TUNE_ALERT",
            "policies_applied": [
                "Whitelisted service account sa-db-backup-runner in SOC alert rule #4102",
                "Adjusted egress burst threshold for scheduled monthly backup window (02:00-03:00 UTC)",
                "Zero workload interruption or network isolation triggered."
            ],
            "production_impact": {
                "service_downtime": "0.0%",
                "business_continuity": "PRESERVED - Backup job allowed to finish normally"
            },
            "verdict": "False positive cleanly resolved without disruptive pod kills."
        }, indent=2)

    # Scenario C: Rate Limiting WAF for DDoS
    elif "ddos" in action_norm or "rate_limit" in action_norm or "payment" in target_service.lower():
        return json.dumps({
            "action": action,
            "target_service": target_service,
            "simulation_status": "SUCCESS",
            "policies_applied": [
                "Enabled AWS WAF automated rate-limiting rule (drop > 500 req/min from botnet subnet 203.0.113.0/24)",
                "Enforced SYN proxy at Cloudflare edge to absorb connection resets",
                "Workload pods kept online; legitimate checkout API traffic unaffected"
            ],
            "production_impact": {
                "estimated_user_disruption_percent": 0.0,
                "canary_health_check": "PASSED (200 OK across 50 simulated payment checkout probes)"
            },
            "verdict": "Volumetric flood absorbed at edge; zero container restarts required."
        }, indent=2)

    # Scenario A: Full Zero-Day Containment
    return json.dumps({
        "action": action,
        "target_service": target_service,
        "simulation_status": "SUCCESS",
        "network_policies_applied": [
            f"Blocked inbound/outbound egress for IP 198.51.100.44 at WAF level",
            f"Isolated pod instance {target_service}-7df92b for forensic memory capture",
            f"Rotated JWT secret tokens and invalidated active refresh session cache in Redis"
        ],
        "production_impact": {
            "estimated_user_disruption_percent": 0.0,
            "session_invalidation_count": 14,
            "canary_health_check": "PASSED (200 OK across 50 simulated probes)"
        },
        "verdict": "Mitigation applied successfully with zero service downtime."
    }, indent=2)


# Registry mapping tool name to callable function and schema
TOOL_REGISTRY: Dict[str, Any] = {
    "query_system_logs": query_system_logs,
    "query_vpc_flow_logs": query_vpc_flow_logs,
    "inspect_network_topology": inspect_network_topology,
    "lookup_threat_intel": lookup_threat_intel,
    "simulate_mitigation": simulate_mitigation,
}

TOOL_DEFINITIONS = [
    {
        "name": "query_system_logs",
        "description": "Queries security and access audit logs for anomalous requests, status codes, and IP activity.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Microservice identifier (e.g. auth-proxy-us-east or billing-worker-eu)"},
                "time_window_mins": {"type": "integer", "description": "Time window in minutes (e.g. 15)"}
            },
            "required": ["service"]
        }
    },
    {
        "name": "query_vpc_flow_logs",
        "description": "Fallback tool: Queries network-level VPC flow logs when container logs are degraded or timed out.",
        "parameters": {
            "type": "object",
            "properties": {
                "vpc_id": {"type": "string", "description": "VPC identifier (e.g. vpc-ap-southeast-01)"},
                "service": {"type": "string", "description": "Target microservice identifier"}
            },
            "required": ["vpc_id"]
        }
    },
    {
        "name": "inspect_network_topology",
        "description": "Inspects downstream and upstream microservices, database access levels, and blast radius.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Microservice identifier (e.g. auth-proxy-us-east or billing-worker-eu)"}
            },
            "required": ["service"]
        }
    },
    {
        "name": "lookup_threat_intel",
        "description": "Queries known threat actor databases and CVE records for an IP or CVE identifier.",
        "parameters": {
            "type": "object",
            "properties": {
                "indicator": {"type": "string", "description": "IP address (e.g. 198.51.100.44) or internal service account / CVE ID"}
            },
            "required": ["indicator"]
        }
    },
    {
        "name": "simulate_mitigation",
        "description": "Simulates and verifies an automated containment, rate-limiting, or stand-down action before production execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Remediation or stand-down action"},
                "target_service": {"type": "string", "description": "Target microservice"}
            },
            "required": ["action", "target_service"]
        }
    }
]
