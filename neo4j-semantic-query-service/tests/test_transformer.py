from __future__ import annotations

import unittest

from app.config import Settings
from app.transformer import ProfileTransformer


PROFILE = {
    "api_id": "payment-approval",
    "method": "POST",
    "path": "/v1/payments/{paymentId}/approve",
    "normalized_path": "/payments/{paymentId}/approve",
    "semanticProfile": {
        "domain": "Payments",
        "businessCapability": "Payment Approval",
        "entity": "Payment",
        "action": "Approve",
        "workflow": "Payment Lifecycle",
        "workflowStage": "Approval",
        "operationType": "State Changing",
        "riskLevel": "High",
        "dataSensitivity": "High",
        "criticality": "High",
        "exposure": "Internal",
        "dataCategories": ["Financial Data", "Transaction Data"],
        "compliance": ["AML"],
        "consumers": ["Treasury Portal"],
    },
    "inferred": {
        "entities": ["Payment", "MonetaryAmount"],
        "containsFinancialData": True,
        "privilegedOperation": True,
        "risk": "HIGH",
    },
    "dependencies": [{"apiId": "payment-execution", "dependencyType": "Synchronous"}],
}


class TransformerTests(unittest.TestCase):
    def test_profile_transformation(self) -> None:
        row = ProfileTransformer(Settings()).transform_profile(PROFILE, source="test", run_id="run-1")
        self.assertEqual(row["api"]["id"], "payment-approval")
        self.assertEqual(row["api"]["businessCapability"], "Payment Approval")
        self.assertTrue(row["api"]["containsFinancialData"])
        self.assertEqual(row["dependencies"][0]["apiId"], "payment-execution")

    def test_document_wrapper(self) -> None:
        rows = ProfileTransformer(Settings()).transform_document({"profiles": [PROFILE]}, source="test", run_id="run-1")
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
