from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from verifier.contracts import (
    FORMALIZATION_PROPOSAL,
    FORMAL_QUERY,
    VERIFICATION_RESULT,
    ContractValidationError,
    REPOSITORY_ROOT,
    load_json,
    validate,
)


VALID_QUERY = {
    "schemaVersion": "formal-query-v0",
    "modelVersion": "in-s194r-fy2024-25-v0",
    "facts": {
        "productFmvPaise": 3_000_000,
        "productDisposition": "retained",
        "priorBenefitsPaise": 0,
    },
}

PROOF = {
    "checker": "lean-kernel",
    "kernelCheck": "PASSED",
    "theoremName": "generatedCase",
    "theoremSource": (
        "theorem generatedCase :\n"
        "    assess caseFacts = expectedDecision := by\n"
        "  decide\n"
    ),
    "replayCommand": ["lake", "env", "lean", "GeneratedCase.lean"],
}


class FormalQueryContractTests(unittest.TestCase):
    def test_canonical_fixture_is_valid(self) -> None:
        query = load_json(REPOSITORY_ROOT / "examples" / "retained-30000.json")
        validate(FORMAL_QUERY, query)

    def test_exact_three_fact_boundary_rejects_extra_fact(self) -> None:
        query = copy.deepcopy(VALID_QUERY)
        query["facts"]["creatorIsResident"] = True
        with self.assertRaises(ContractValidationError):
            validate(FORMAL_QUERY, query)

    def test_missing_fact_is_rejected(self) -> None:
        query = copy.deepcopy(VALID_QUERY)
        del query["facts"]["priorBenefitsPaise"]
        with self.assertRaises(ContractValidationError):
            validate(FORMAL_QUERY, query)

    def test_wrong_model_version_is_rejected(self) -> None:
        query = copy.deepcopy(VALID_QUERY)
        query["modelVersion"] = "current-law"
        with self.assertRaises(ContractValidationError):
            validate(FORMAL_QUERY, query)

    def test_money_must_be_non_negative_whole_rupees_in_paise(self) -> None:
        for invalid_value in (-100, 10_001, 1.5, 3_000_000.0, True):
            with self.subTest(invalid_value=invalid_value):
                query = copy.deepcopy(VALID_QUERY)
                query["facts"]["productFmvPaise"] = invalid_value
                with self.assertRaises(ContractValidationError):
                    validate(FORMAL_QUERY, query)

    def test_json_float_and_exponent_tokens_are_rejected_during_load(self) -> None:
        for token in ("3000000.0", "3e6"):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "float.json"
                path.write_text(
                    '{"productFmvPaise":' + token + "}", encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, "non-integer JSON number"):
                    load_json(path)

    def test_prior_benefits_above_threshold_remain_valid_input(self) -> None:
        query = copy.deepcopy(VALID_QUERY)
        query["facts"]["priorBenefitsPaise"] = 2_000_100
        validate(FORMAL_QUERY, query)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schemaVersion":"a","schemaVersion":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_json(path)


class FormalizationProposalContractTests(unittest.TestCase):
    def test_ready_proposal_requires_a_complete_formal_query(self) -> None:
        validate(
            FORMALIZATION_PROPOSAL,
            {
                "schemaVersion": "formalization-proposal-v0",
                "status": "READY",
                "formalQuery": VALID_QUERY,
            },
        )

    def test_unknown_proposal_cannot_smuggle_partial_facts(self) -> None:
        proposal = {
            "schemaVersion": "formalization-proposal-v0",
            "status": "UNKNOWN",
            "unknown": {
                "reason": "AMBIGUOUS_FACT",
                "details": "The question does not say whether the product was returned.",
            },
            "formalQuery": VALID_QUERY,
        }
        with self.assertRaises(ContractValidationError):
            validate(FORMALIZATION_PROPOSAL, proposal)


class VerificationResultContractTests(unittest.TestCase):
    def test_answered_checked_decision_is_proved(self) -> None:
        validate(
            VERIFICATION_RESULT,
            {
                "schemaVersion": "verification-result-v0",
                "modelVersion": "in-s194r-fy2024-25-v0",
                "status": "PROVED",
                "formalQuery": VALID_QUERY,
                "decision": {
                    "kind": "answered",
                    "answer": {
                        "benefitQualifies": True,
                        "currentBenefitPaise": 3_000_000,
                        "aggregateBenefitsPaise": 3_000_000,
                        "thresholdExceeded": True,
                        "tdsDuePaise": 300_000,
                        "releaseGateRequired": True,
                        "appliedRules": [
                            "s194rScope",
                            "influencerProduct",
                            "annualThreshold",
                            "inKindReleaseGate",
                        ],
                    },
                },
                "proof": PROOF,
            },
        )

    def test_checked_unsupported_decision_remains_unknown(self) -> None:
        validate(
            VERIFICATION_RESULT,
            {
                "schemaVersion": "verification-result-v0",
                "modelVersion": "in-s194r-fy2024-25-v0",
                "status": "UNKNOWN",
                "formalQuery": VALID_QUERY,
                "decision": {
                    "kind": "unsupported",
                    "reason": "priorBenefitsNeedPriorTds",
                    "citedRules": ["annualThreshold"],
                },
                "proof": PROOF,
                "unknown": {
                    "reason": "UNSUPPORTED_INPUT",
                    "details": "Prior benefits need prior-TDS information outside v0.",
                },
            },
        )

    def test_refuted_is_not_a_v0_status(self) -> None:
        result = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "REFUTED",
            "formalQuery": VALID_QUERY,
        }
        with self.assertRaises(ContractValidationError):
            validate(VERIFICATION_RESULT, result)

    def test_failed_evaluation_cannot_claim_a_passed_proof(self) -> None:
        result = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "formalQuery": VALID_QUERY,
            "decision": {
                "kind": "unsupported",
                "reason": "priorBenefitsNeedPriorTds",
                "citedRules": ["annualThreshold"],
            },
            "proof": PROOF,
            "unknown": {
                "reason": "LEAN_EVALUATION_FAILED",
                "details": "Lean did not emit a candidate decision.",
            },
        }
        with self.assertRaises(ContractValidationError):
            validate(VERIFICATION_RESULT, result)

    def test_source_mapping_failure_preserves_checked_answer(self) -> None:
        result = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "formalQuery": VALID_QUERY,
            "decision": {
                "kind": "answered",
                "answer": {
                    "benefitQualifies": False,
                    "currentBenefitPaise": 0,
                    "aggregateBenefitsPaise": 0,
                    "thresholdExceeded": False,
                    "tdsDuePaise": 0,
                    "releaseGateRequired": False,
                    "appliedRules": ["s194rScope", "influencerProduct"],
                },
            },
            "proof": PROOF,
            "unknown": {
                "reason": "SOURCE_MAPPING_FAILED",
                "details": "A checked Lean rule had no unique public mapping.",
            },
        }
        validate(VERIFICATION_RESULT, result)


if __name__ == "__main__":
    unittest.main()
