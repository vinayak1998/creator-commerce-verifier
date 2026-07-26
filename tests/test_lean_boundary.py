from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

from verifier.contracts import ContractValidationError, REPOSITORY_ROOT
from verifier.lean_boundary import (
    CANDIDATE_SCHEMA_VERSION,
    CHECK_FILENAME,
    CHECKED_SCHEMA_VERSION,
    LeanBoundaryError,
    _parse_case_output,
    _run_lean,
    evaluate_and_check,
    generate_check_source,
    generate_evaluate_source,
)


RETAINED_THIRTY_THOUSAND = {
    "schemaVersion": "formal-query-v0",
    "modelVersion": "in-s194r-fy2024-25-v0",
    "facts": {
        "productFmvPaise": 3_000_000,
        "productDisposition": "retained",
        "priorBenefitsPaise": 0,
    },
}

EXPECTED_ANSWER = {
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
}


class SourceGenerationTests(unittest.TestCase):
    def test_evaluation_source_contains_only_the_three_validated_facts(self) -> None:
        source = generate_evaluate_source(RETAINED_THIRTY_THOUSAND)
        self.assertIn("priorBenefitsPaise := 0", source)
        self.assertIn("productFmvPaise := 3000000", source)
        self.assertIn("productDisposition := .retained", source)
        self.assertIn("candidateJson caseFacts", source)

    def test_concrete_check_source_contains_candidate_answer(self) -> None:
        source = generate_check_source(RETAINED_THIRTY_THOUSAND, EXPECTED_ANSWER)
        self.assertIn("tdsDuePaise := 300000", source)
        self.assertIn("assess caseFacts = expectedDecision", source)
        self.assertIn("by\n  decide", source)


class LeanBoundaryIntegrationTests(unittest.TestCase):
    def test_retained_case_is_evaluated_then_checked_by_lean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = evaluate_and_check(
                RETAINED_THIRTY_THOUSAND,
                Path(directory) / "case",
            )
            self.assertEqual(case.decision, EXPECTED_ANSWER)
            self.assertTrue((case.case_directory / "Evaluate.lean").is_file())
            self.assertTrue((case.case_directory / CHECK_FILENAME).is_file())
            self.assertTrue((case.case_directory / "candidate.json").is_file())
            self.assertTrue((case.case_directory / "checked.json").is_file())

            detached_decision = case.decision
            detached_decision["answer"]["tdsDuePaise"] = 1
            detached_query = case.formal_query
            detached_query["facts"]["productFmvPaise"] = 100
            self.assertEqual(case.decision, EXPECTED_ANSWER)
            self.assertEqual(
                case.formal_query["facts"]["productFmvPaise"], 3_000_000
            )

            rebuilt = subprocess.run(
                case.replay_build_command,
                cwd=case.replay_cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            replayed = subprocess.run(
                case.replay_command,
                cwd=case.replay_cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn('"schemaVersion":"lean-checked-v0"', replayed.stdout)
            self.assertIn("--memory=512", case.replay_command)

    def test_prior_benefits_above_threshold_is_checked_unsupported(self) -> None:
        query = copy.deepcopy(RETAINED_THIRTY_THOUSAND)
        query["facts"]["priorBenefitsPaise"] = 2_000_100
        with tempfile.TemporaryDirectory() as directory:
            case = evaluate_and_check(query, Path(directory) / "case")
        self.assertEqual(
            case.decision,
            {
                "kind": "unsupported",
                "reason": "priorBenefitsNeedPriorTds",
                "citedRules": ["annualThreshold"],
            },
        )

    def test_tampered_candidate_cannot_pass_the_second_lean_check(self) -> None:
        tampered = copy.deepcopy(EXPECTED_ANSWER)
        tampered["answer"]["tdsDuePaise"] = 300_001
        source = generate_check_source(RETAINED_THIRTY_THOUSAND, tampered)

        with tempfile.TemporaryDirectory() as directory:
            check_path = Path(directory) / CHECK_FILENAME
            check_path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [
                    "lake",
                    "env",
                    "lean",
                    "--trust=0",
                    "--run",
                    str(check_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('"schemaVersion":"lean-checked-v0"', completed.stdout)

    def test_lean_process_has_an_explicit_memory_ceiling(self) -> None:
        with mock.patch("verifier.lean_boundary.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"ok":true}\n', stderr=""
            )
            _run_lean(Path("Case.lean"), trust_zero=True, timeout=1)

        command = run.call_args.args[0]
        self.assertIn("--memory=512", command)
        self.assertIn("--trust=0", command)

    def test_timeout_fails_closed_at_the_process_boundary(self) -> None:
        with mock.patch(
            "verifier.lean_boundary.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["lake"], 1),
        ):
            with self.assertRaisesRegex(LeanBoundaryError, "lean-process"):
                _run_lean(Path("Case.lean"), trust_zero=False, timeout=1)


class LeanProtocolFailureTests(unittest.TestCase):
    def _output(
        self,
        *,
        schema_version: str = CANDIDATE_SCHEMA_VERSION,
        facts: Optional[dict] = None,
        decision: Optional[dict] = None,
    ) -> dict:
        return {
            "schemaVersion": schema_version,
            "modelVersion": "in-s194r-fy2024-25-v0",
            "facts": facts or RETAINED_THIRTY_THOUSAND["facts"],
            "decision": decision or EXPECTED_ANSWER,
        }

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaisesRegex(LeanBoundaryError, "lean-protocol"):
            _parse_case_output(
                "not json",
                expected_schema_version=CANDIDATE_SCHEMA_VERSION,
                expected_facts=RETAINED_THIRTY_THOUSAND["facts"],
            )

    def test_wrong_protocol_version_is_rejected(self) -> None:
        text = json.dumps(self._output(schema_version="future-protocol"))
        with self.assertRaisesRegex(LeanBoundaryError, "schema version mismatch"):
            _parse_case_output(
                text,
                expected_schema_version=CANDIDATE_SCHEMA_VERSION,
                expected_facts=RETAINED_THIRTY_THOUSAND["facts"],
            )

    def test_boolean_cannot_impersonate_zero_in_lean_facts(self) -> None:
        facts = copy.deepcopy(RETAINED_THIRTY_THOUSAND["facts"])
        facts["priorBenefitsPaise"] = False
        text = json.dumps(self._output(facts=facts))
        with self.assertRaisesRegex(LeanBoundaryError, "lean-protocol"):
            _parse_case_output(
                text,
                expected_schema_version=CANDIDATE_SCHEMA_VERSION,
                expected_facts=RETAINED_THIRTY_THOUSAND["facts"],
            )

    def test_candidate_and_checked_outputs_must_match(self) -> None:
        candidate = self._output()
        changed_decision = copy.deepcopy(EXPECTED_ANSWER)
        changed_decision["answer"]["tdsDuePaise"] = 300_001
        checked = self._output(
            schema_version=CHECKED_SCHEMA_VERSION,
            decision=changed_decision,
        )

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("verifier.lean_boundary._build_model"), mock.patch(
                "verifier.lean_boundary._run_lean",
                side_effect=[json.dumps(candidate), json.dumps(checked)],
            ):
                with self.assertRaisesRegex(
                    LeanBoundaryError, "checked decision does not match"
                ):
                    evaluate_and_check(
                        RETAINED_THIRTY_THOUSAND,
                        Path(directory) / "case",
                    )

    def test_existing_artifact_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                evaluate_and_check(RETAINED_THIRTY_THOUSAND, Path(directory))

    def test_invalid_query_never_reaches_lean(self) -> None:
        query = copy.deepcopy(RETAINED_THIRTY_THOUSAND)
        query["facts"]["productFmvPaise"] = 3_000_001
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ContractValidationError):
                evaluate_and_check(query, Path(directory) / "case")


if __name__ == "__main__":
    unittest.main()
