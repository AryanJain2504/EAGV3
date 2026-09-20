import json
import unittest

from pydantic import ValidationError

from prompt import SYSTEM_PROMPT
from schemas import AgentTurnOutput
from tools import (
    inspect_network_topology,
    lookup_threat_intel,
    query_system_logs,
    query_vpc_flow_logs,
    simulate_mitigation,
)
from validate_prompt import evaluate_prompt_statically


class ToolBehaviorTests(unittest.TestCase):
    def test_zero_day_logs_and_topology_are_consistent(self):
        logs = json.loads(query_system_logs("auth-proxy-us-east"))
        topology = json.loads(inspect_network_topology("auth-proxy-us-east"))
        self.assertEqual(logs["suspicious_ip"], "198.51.100.44")
        self.assertEqual(topology["blast_radius_assessment"].split(":", 1)[0], "CRITICAL")

    def test_payment_log_failure_has_fallback(self):
        result = json.loads(query_system_logs("payment-gateway-ap"))
        self.assertEqual(result["status"], 504)
        self.assertIn("query_vpc_flow_logs", result["suggested_fallback"])
        fallback = json.loads(query_vpc_flow_logs("vpc-ap-southeast-01", "payment-gateway-ap"))
        self.assertIn("DDoS", fallback["verdict"])

    def test_benign_asset_and_stand_down(self):
        intel = json.loads(lookup_threat_intel("10.0.12.9"))
        mitigation = json.loads(simulate_mitigation("stand_down_and_tune_alert", "billing-worker-eu"))
        self.assertEqual(intel["threat_classification"], "BENIGN_INTERNAL_ASSET")
        self.assertEqual(mitigation["simulation_status"], "SUCCESS")


class ContractTests(unittest.TestCase):
    def test_agent_turn_rejects_out_of_range_confidence(self):
        with self.assertRaises(ValidationError):
            AgentTurnOutput(
                step_number=1,
                reasoning_type="lookup",
                step_by_step_thinking="test",
                self_check={
                    "is_reasonable": True,
                    "sanity_notes": "test",
                    "verification_check": "test",
                },
                confidence=1.5,
                fallback_plan="test",
            )

    def test_prompt_scorecard_passes(self):
        scorecard = evaluate_prompt_statically(SYSTEM_PROMPT)
        self.assertTrue(all(value is True for key, value in scorecard.items() if key != "overall_clarity"))


if __name__ == "__main__":
    unittest.main()
