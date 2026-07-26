from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from verifier.contracts import (
    RENDERED_ANSWER,
    REPOSITORY_ROOT,
    ContractValidationError,
    validate,
)
from verifier.render import (
    DEFAULT_SOURCE_MAP,
    format_money,
    load_source_map,
    render_text,
    render_verification_result,
    write_rendered_artifacts,
)
from verifier.verify import _effective_verification_result


FORMAL_QUERY = {
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
    "theoremName": "checkedCase",
    "theoremSource": "theorem checkedCase : assess caseFacts = expectedDecision := by decide\n",
    "modelSnapshot": [
        "lean-toolchain",
        "lakefile.toml",
        "lake-manifest.json",
        "CreatorCommerce.lean",
        "CreatorCommerce/Section194R.lean",
        "CreatorCommerce/CaseProtocol.lean",
    ],
    "replayCwd": "model",
    "replayBuildCommand": ["lake", "build"],
    "replayCommand": [
        "lake",
        "env",
        "lean",
        "--trust=0",
        "--memory=512",
        "--run",
        "GeneratedCase.lean",
    ],
}

ANSWERED_RESULT = {
    "schemaVersion": "verification-result-v0",
    "modelVersion": "in-s194r-fy2024-25-v0",
    "status": "PROVED",
    "formalQuery": FORMAL_QUERY,
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
}


class SourceMapTests(unittest.TestCase):
    def test_frozen_source_map_has_one_mapping_for_every_lean_rule(self) -> None:
        source_map = load_source_map()
        citations = source_map.resolve(
            [
                "s194rScope",
                "influencerProduct",
                "annualThreshold",
                "inKindReleaseGate",
            ]
        )
        self.assertEqual(
            [citation["ruleId"] for citation in citations],
            [
                "IT-194R-SCOPE",
                "IT-194R-RETAINED",
                "IT-194R-THRESHOLD",
                "IT-194R-RELEASEGATE",
            ],
        )
        self.assertEqual(source_map.status["reviewStatus"], "needs_independent_tax_review")

    def test_duplicate_lean_mapping_fails_closed_in_rendered_output(self) -> None:
        with DEFAULT_SOURCE_MAP.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
        document["rules"]["IT-194R-RETAINED"]["lean_id"] = "s194rScope"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            rendered = render_verification_result(
                ANSWERED_RESULT,
                source_map_path=path,
            )

        self.assertEqual(rendered["status"], "UNKNOWN")
        self.assertEqual(rendered["verificationStatus"], "PROVED")
        self.assertEqual(rendered["unknownReason"], "SOURCE_MAPPING_FAILED")
        self.assertEqual(rendered["citations"], [])

    def test_changed_citation_content_or_relationship_fails_closed(self) -> None:
        changes = [
            (
                ("sources", 0, "official_url"),
                "https://example.com/invented-section-194r",
            ),
            (
                ("rules", "IT-194R-RETAINED", "source_id"),
                "ITA-1961-S194R",
            ),
            (
                ("rules", "IT-194R-THRESHOLD", "location"),
                "Invented proviso",
            ),
            (
                ("rules", "IT-194R-SCOPE", "encoded_interpretation"),
                "Invented interpretation.",
            ),
            (
                ("rules", "IT-194R-RELEASEGATE", "additional_source_id"),
                "ITA-1961-S194R",
            ),
        ]

        for path_parts, replacement in changes:
            with self.subTest(path=path_parts):
                with DEFAULT_SOURCE_MAP.open("r", encoding="utf-8") as handle:
                    document = yaml.safe_load(handle)
                target = document
                for part in path_parts[:-1]:
                    target = target[part]
                target[path_parts[-1]] = replacement

                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "sources.yaml"
                    path.write_text(yaml.safe_dump(document), encoding="utf-8")
                    rendered = render_verification_result(
                        ANSWERED_RESULT,
                        source_map_path=path,
                    )

                self.assertEqual(rendered["status"], "UNKNOWN")
                self.assertEqual(
                    rendered["unknownReason"], "SOURCE_MAPPING_FAILED"
                )

    def test_duplicate_yaml_keys_fail_closed(self) -> None:
        canonical = DEFAULT_SOURCE_MAP.read_text(encoding="utf-8")
        duplicate = "model_version: invented-model\n" + canonical
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_text(duplicate, encoding="utf-8")
            rendered = render_verification_result(
                ANSWERED_RESULT,
                source_map_path=path,
            )

        self.assertEqual(rendered["status"], "UNKNOWN")
        self.assertEqual(rendered["unknownReason"], "SOURCE_MAPPING_FAILED")

    def test_invalid_utf8_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_bytes(b"\xff\xfe\x00")
            rendered = render_verification_result(
                ANSWERED_RESULT,
                source_map_path=path,
            )

        self.assertEqual(rendered["status"], "UNKNOWN")
        self.assertEqual(rendered["unknownReason"], "SOURCE_MAPPING_FAILED")


class DeterministicRendererTests(unittest.TestCase):
    def test_answered_result_uses_checked_fields_and_rule_order(self) -> None:
        rendered = render_verification_result(ANSWERED_RESULT)
        validate(RENDERED_ANSWER, rendered)
        self.assertEqual(rendered["status"], "PROVED")
        self.assertIn("₹30,000.00", rendered["summary"])
        self.assertIn("₹3,000.00", rendered["summary"])
        self.assertEqual(
            [citation["ruleId"] for citation in rendered["citations"]],
            [
                "IT-194R-SCOPE",
                "IT-194R-RETAINED",
                "IT-194R-THRESHOLD",
                "IT-194R-RELEASEGATE",
            ],
        )
        self.assertEqual(
            rendered["citations"][1]["location"],
            "Question 6, social-media influencer example, official PDF pages 4-5",
        )

    def test_renderer_displays_checked_values_without_recalculating_them(self) -> None:
        deliberately_unusual = copy.deepcopy(ANSWERED_RESULT)
        deliberately_unusual["decision"]["answer"]["tdsDuePaise"] = 12_345
        rendered = render_verification_result(deliberately_unusual)
        self.assertIn("₹123.45", rendered["summary"])
        self.assertEqual(
            next(
                detail["value"]
                for detail in rendered["details"]
                if detail["label"] == "Modeled TDS due"
            ),
            "₹123.45",
        )

    def test_rendered_contract_pins_each_rule_to_its_exact_source(self) -> None:
        rendered = render_verification_result(ANSWERED_RESULT)
        changed = copy.deepcopy(rendered)
        changed["citations"][0]["sources"] = copy.deepcopy(
            rendered["citations"][1]["sources"]
        )
        with self.assertRaises(ContractValidationError):
            validate(RENDERED_ANSWER, changed)

        duplicated = copy.deepcopy(rendered)
        duplicated["citations"][3]["sources"][1] = copy.deepcopy(
            duplicated["citations"][3]["sources"][0]
        )
        with self.assertRaises(ContractValidationError):
            validate(RENDERED_ANSWER, duplicated)

    def test_checked_unsupported_result_keeps_exact_threshold_citation(self) -> None:
        result = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "formalQuery": FORMAL_QUERY,
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
        }
        rendered = render_verification_result(result)
        self.assertEqual(rendered["status"], "UNKNOWN")
        self.assertEqual(rendered["unknownReason"], "UNSUPPORTED_INPUT")
        self.assertEqual(
            [citation["ruleId"] for citation in rendered["citations"]],
            ["IT-194R-THRESHOLD"],
        )

    def test_operational_unknown_needs_no_source_map(self) -> None:
        result = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "formalQuery": FORMAL_QUERY,
            "unknown": {
                "reason": "LEAN_CHECK_FAILED",
                "details": "The concrete equality was not checked.",
            },
        }
        rendered = render_verification_result(
            result,
            source_map_path=Path("does-not-exist.yaml"),
        )
        self.assertEqual(rendered["status"], "UNKNOWN")
        self.assertEqual(rendered["unknownReason"], "LEAN_CHECK_FAILED")
        self.assertEqual(rendered["citations"], [])

    def test_unknown_contract_rejects_contradictory_states(self) -> None:
        operational = {
            "schemaVersion": "rendered-answer-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "verificationStatus": "UNKNOWN",
            "unknownReason": "LEAN_CHECK_FAILED",
            "summary": "Lean checking failed.",
            "details": [],
            "citations": [],
            "disclaimer": "Educational model.",
        }
        validate(RENDERED_ANSWER, operational)

        contradictory = copy.deepcopy(operational)
        contradictory["verificationStatus"] = "PROVED"
        with self.assertRaises(ContractValidationError):
            validate(RENDERED_ANSWER, contradictory)

        unsupported = {
            "schemaVersion": "verification-result-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "status": "UNKNOWN",
            "formalQuery": FORMAL_QUERY,
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
        }
        rendered_unsupported = render_verification_result(unsupported)
        rendered_unsupported["citations"] = []
        with self.assertRaises(ContractValidationError):
            validate(RENDERED_ANSWER, rendered_unsupported)

        mapping_failure = render_verification_result(
            ANSWERED_RESULT,
            source_map_path=Path("does-not-exist.yaml"),
        )
        mapping_failure["sourceStatus"] = load_source_map().status
        with self.assertRaises(ContractValidationError):
            validate(RENDERED_ANSWER, mapping_failure)

    def test_effective_verification_json_exposes_source_mapping_failure(self) -> None:
        mapping_failure = render_verification_result(
            ANSWERED_RESULT,
            source_map_path=Path("does-not-exist.yaml"),
        )
        effective = _effective_verification_result(
            ANSWERED_RESULT,
            mapping_failure,
        )
        validate("verification-result-v0", effective)
        self.assertEqual(effective["status"], "UNKNOWN")
        self.assertEqual(effective["unknown"]["reason"], "SOURCE_MAPPING_FAILED")
        self.assertEqual(effective["proof"]["kernelCheck"], "PASSED")

        rerendered = render_verification_result(effective)
        self.assertEqual(rerendered["status"], "UNKNOWN")
        self.assertEqual(rerendered["unknownReason"], "SOURCE_MAPPING_FAILED")
        self.assertEqual(rerendered["citations"], [])

    def test_text_and_artifact_writer_preserve_exact_urls(self) -> None:
        rendered = render_verification_result(ANSWERED_RESULT)
        text = render_text(rendered)
        self.assertIn("IT-194R-RELEASEGATE", text)
        self.assertIn("https://www.incometaxindia.gov.in/w/section-194r-4", text)
        self.assertIn("https://incometaxindia.gov.in/communications/circular/", text)

        with tempfile.TemporaryDirectory() as directory:
            artifact_directory = Path(directory)
            write_rendered_artifacts(artifact_directory, rendered)
            self.assertEqual(
                (artifact_directory / "answer.txt").read_text(encoding="utf-8"),
                text,
            )
            self.assertEqual(
                (artifact_directory / "sources.yaml").read_text(encoding="utf-8"),
                (REPOSITORY_ROOT / "sources.yaml").read_text(encoding="utf-8"),
            )

    def test_money_formatting_preserves_paise_exactly(self) -> None:
        self.assertEqual(format_money(10), "₹0.10")
        self.assertEqual(format_money(2_500_000), "₹25,000.00")


if __name__ == "__main__":
    unittest.main()
