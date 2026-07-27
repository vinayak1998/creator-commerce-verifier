from __future__ import annotations

import argparse
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from verifier.contracts import (
    REPOSITORY_ROOT,
    VERIFICATION_RESULT,
    load_json,
    validate,
)
from verifier.lean_boundary import LeanBoundaryError
from verifier.verify import _positive_timeout, main, verify_formal_query


RETAINED_THIRTY_THOUSAND = {
    "schemaVersion": "formal-query-v0",
    "modelVersion": "in-s194r-fy2024-25-v0",
    "facts": {
        "productFmvPaise": 3_000_000,
        "productDisposition": "retained",
        "priorBenefitsPaise": 0,
    },
}


class VerificationCertificateTests(unittest.TestCase):
    def test_answered_case_writes_a_proved_replayable_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = verify_formal_query(
                RETAINED_THIRTY_THOUSAND,
                artifacts_root=Path(directory),
                case_name="retained-30000",
            )
            result = run.result
            validate(VERIFICATION_RESULT, result)
            self.assertEqual(result["status"], "PROVED")
            self.assertEqual(result["decision"]["answer"]["tdsDuePaise"], 300_000)

            expected_files = {
                "candidate.json",
                "checked.json",
                "formal-query.json",
                "certificate.json",
                "model",
            }
            self.assertEqual(
                {path.name for path in run.artifact_directory.iterdir()},
                expected_files,
            )
            certificate = load_json(run.artifact_directory / "certificate.json")
            self.assertEqual(certificate, result)
            self.assertEqual(result["proof"]["replayCwd"], "model")
            self.assertEqual(
                result["proof"]["theoremSource"],
                (run.artifact_directory / "model/GeneratedCase.lean").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                load_json(run.artifact_directory / "formal-query.json"),
                RETAINED_THIRTY_THOUSAND,
            )

    def test_checked_unsupported_case_is_user_facing_unknown(self) -> None:
        query = copy.deepcopy(RETAINED_THIRTY_THOUSAND)
        query["facts"]["priorBenefitsPaise"] = 2_000_100
        with tempfile.TemporaryDirectory() as directory:
            run = verify_formal_query(
                query,
                artifacts_root=Path(directory),
                case_name="unsupported-priors",
            )
        result = run.result
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["proof"]["kernelCheck"], "PASSED")
        self.assertEqual(result["decision"]["kind"], "unsupported")
        self.assertEqual(result["unknown"]["reason"], "UNSUPPORTED_INPUT")

    def test_boundary_failure_is_unknown_without_a_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)

            def fail_boundary(_query, case_directory, *, timeout):
                case_directory.mkdir(parents=True)
                raise LeanBoundaryError(
                    "lean-check",
                    "deliberate test failure",
                    stdout="partial stdout",
                    stderr="kernel rejected the equality",
                )

            with mock.patch(
                "verifier.verify.evaluate_and_check", side_effect=fail_boundary
            ):
                run = verify_formal_query(
                    RETAINED_THIRTY_THOUSAND,
                    artifacts_root=artifact_root,
                    case_name="failed-check",
                )
            failure = load_json(run.artifact_directory / "failure.json")

        result = run.result
        validate(VERIFICATION_RESULT, result)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unknown"]["reason"], "LEAN_CHECK_FAILED")
        self.assertNotIn("decision", result)
        self.assertNotIn("proof", result)
        self.assertEqual(failure["stage"], "lean-check")
        self.assertEqual(failure["stdout"], "partial stdout")
        self.assertEqual(failure["stderr"], "kernel rejected the equality")


class ManualJsonCliTests(unittest.TestCase):
    def test_cli_runs_the_canonical_query_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query_path = REPOSITORY_ROOT / "examples" / "retained-30000.json"
            self.assertEqual(load_json(query_path), RETAINED_THIRTY_THOUSAND)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "verifier.verify",
                    str(query_path),
                    "--artifacts-root",
                    str(root / "artifacts"),
                    "--case-name",
                    "cli-case",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "PROVED")
            self.assertEqual(result["schemaVersion"], "rendered-answer-v0")
            details = {item["label"]: item["value"] for item in result["details"]}
            self.assertEqual(details["Current product qualifies"], "Yes")
            self.assertEqual(details["Financial-year aggregate"], "₹30,000.00")
            self.assertEqual(details["Modeled TDS due"], "₹3,000.00")
            self.assertEqual(details["Release gate required"], "Yes")
            self.assertEqual(
                [citation["ruleId"] for citation in result["citations"]],
                [
                    "IT-194R-SCOPE",
                    "IT-194R-RETAINED",
                    "IT-194R-THRESHOLD",
                    "IT-194R-RELEASEGATE",
                ],
            )
            self.assertIn("Artifacts:", completed.stderr)
            self.assertTrue((root / "artifacts" / "cli-case" / "certificate.json").is_file())
            self.assertTrue((root / "artifacts" / "cli-case" / "answer.json").is_file())
            self.assertTrue((root / "artifacts" / "cli-case" / "answer.txt").is_file())

    def test_cli_returns_unknown_for_wrong_model_version_without_running_lean(self) -> None:
        query = copy.deepcopy(RETAINED_THIRTY_THOUSAND)
        query["modelVersion"] = "current-law"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query_path = root / "query.json"
            query_path.write_text(json.dumps(query), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "verifier.verify",
                    str(query_path),
                    "--artifacts-root",
                    str(root / "artifacts"),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            artifacts_created = (root / "artifacts").exists()

        self.assertEqual(completed.returncode, 2)
        proposal = json.loads(completed.stdout)
        self.assertEqual(proposal["status"], "UNKNOWN")
        self.assertEqual(proposal["unknown"]["reason"], "WRONG_MODEL_VERSION")
        self.assertFalse(artifacts_created)

    def test_cli_uses_nonzero_exit_for_checked_unsupported_result(self) -> None:
        expected_query = {
            "schemaVersion": "formal-query-v0",
            "modelVersion": "in-s194r-fy2024-25-v0",
            "facts": {
                "productFmvPaise": 100_000,
                "productDisposition": "retained",
                "priorBenefitsPaise": 2_000_100,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query_path = (
                REPOSITORY_ROOT / "examples" / "unsupported-priors-20001.json"
            )
            self.assertEqual(load_json(query_path), expected_query)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "verifier.verify",
                    str(query_path),
                    "--artifacts-root",
                    str(root / "artifacts"),
                    "--case-name",
                    "unsupported-cli",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            certificate = load_json(
                root / "artifacts" / "unsupported-cli" / "certificate.json"
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unknownReason"], "UNSUPPORTED_INPUT")
        self.assertEqual(
            [citation["ruleId"] for citation in result["citations"]],
            ["IT-194R-THRESHOLD"],
        )
        self.assertEqual(certificate["formalQuery"], expected_query)
        self.assertEqual(certificate["proof"]["kernelCheck"], "PASSED")
        self.assertEqual(certificate["decision"]["kind"], "unsupported")
        self.assertEqual(
            certificate["decision"]["reason"],
            "priorBenefitsNeedPriorTds",
        )

    def test_timeout_parser_rejects_non_finite_values(self) -> None:
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    _positive_timeout(value)
        self.assertEqual(_positive_timeout("0.25"), 0.25)

    def test_valid_query_artifact_failure_is_internal_not_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            query_path = Path(directory) / "query.json"
            query_path.write_text(
                json.dumps(RETAINED_THIRTY_THOUSAND), encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch(
                "sys.argv", ["verifier.verify", str(query_path)]
            ), mock.patch(
                "verifier.verify.verify_formal_query",
                side_effect=OSError("disk full"),
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main()

        self.assertEqual(exit_code, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["schemaVersion"], "verification-result-v0")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unknown"]["reason"], "INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
