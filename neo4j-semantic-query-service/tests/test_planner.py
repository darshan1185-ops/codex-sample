from __future__ import annotations

import unittest

from app.planner import SemanticPlanner


CATALOG = {
    "domains": ["Payments", "Cards"],
    "capabilities": ["Payment Approval", "Card Authorization"],
    "entities": ["Payment", "Card Transaction"],
    "workflows": ["Payment Lifecycle", "Card Transaction Lifecycle"],
    "workflowStages": ["Approval", "Authorization"],
    "actions": ["Approve", "Authorize"],
    "operationTypes": ["State Changing", "Read Only"],
    "consumers": ["Treasury Portal", "Merchant Checkout"],
}

IDENTITIES = [
    {"apiId": "payment-approval", "method": "POST", "path": "/v1/payments/{paymentId}/approve", "normalizedPath": "/payments/{paymentId}/approve"},
    {"apiId": "card-authorization", "method": "POST", "path": "/cards/authorize", "normalizedPath": "/cards/authorize"},
]


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = SemanticPlanner()

    def test_capability_question(self) -> None:
        plan = self.planner.plan("What business capability does payment-approval represent?", catalog=CATALOG, identities=IDENTITIES, explicit_api_id=None, limit=20)
        self.assertEqual(plan.intent, "capability")
        self.assertEqual(plan.target_api_id, "payment-approval")

    def test_high_risk_public_filter(self) -> None:
        plan = self.planner.plan("Show public high-risk APIs", catalog=CATALOG, identities=IDENTITIES, explicit_api_id=None, limit=20)
        self.assertEqual(plan.filters["exposures"], ["Public"])
        self.assertEqual(plan.filters["min_risk"], "High")

    def test_duplicate_intent(self) -> None:
        plan = self.planner.plan("Show semantic duplicate APIs", catalog=CATALOG, identities=IDENTITIES, explicit_api_id=None, limit=20)
        self.assertEqual(plan.intent, "duplicates")


if __name__ == "__main__":
    unittest.main()
