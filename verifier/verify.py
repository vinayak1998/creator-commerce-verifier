"""Manual-JSON verification entry point and certificate writer."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from verifier.contracts import (
    FORMALIZATION_PROPOSAL,
    FORMAL_QUERY,
    MODEL_VERSION,
    REPOSITORY_ROOT,
    VERIFICATION_RESULT,
    ContractValidationError,
    load_json,
    loads_json,
    validate,
)
from verifier.lean_boundary import CheckedCase, LeanBoundaryError, evaluate_and_check


CERTIFICATE_FILENAME = "certificate.json"
FORMAL_QUERY_FILENAME = "formal-query.json"
FAILURE_FILENAME = "failure.json"
FAILURE_TEXT_LIMIT = 16_384
CASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class VerificationRun:
    """An immutable certificate snapshot and its replayable artifact directory."""

    _result_json: str
    artifact_directory: Path

    @property
    def result(self) -> dict[str, Any]:
        return loads_json(self._result_json)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _proof_from_checked_case(case: CheckedCase) -> dict[str, Any]:
    return {
        "checker": "lean-kernel",
        "kernelCheck": "PASSED",
        "theoremName": "checkedCase",
        "theoremSource": case.theorem_source,
        "modelSnapshot": case.model_snapshot_files,
        "replayCwd": str(case.replay_cwd.relative_to(case.case_directory)),
        "replayBuildCommand": case.replay_build_command,
        "replayCommand": case.replay_command,
    }


def _result_from_checked_case(case: CheckedCase) -> dict[str, Any]:
    decision = case.decision
    proof = _proof_from_checked_case(case)
    result: dict[str, Any] = {
        "schemaVersion": "verification-result-v0",
        "modelVersion": MODEL_VERSION,
        "formalQuery": case.formal_query,
        "decision": decision,
        "proof": proof,
    }

    if decision["kind"] == "answered":
        result["status"] = "PROVED"
    else:
        result["status"] = "UNKNOWN"
        result["unknown"] = {
            "reason": "UNSUPPORTED_INPUT",
            "details": (
                "Prior benefits exceed the v0 supported boundary; prior-TDS "
                "information is intentionally absent."
            ),
        }
    validate(VERIFICATION_RESULT, result)
    return result


def _result_from_boundary_error(
    formal_query: dict[str, Any], error: LeanBoundaryError
) -> dict[str, Any]:
    if error.stage == "lean-check":
        reason = "LEAN_CHECK_FAILED"
    elif error.stage in {
        "lean-build",
        "lean-evaluation",
        "lean-process",
    }:
        reason = "LEAN_EVALUATION_FAILED"
    else:
        reason = "INTERNAL_ERROR"

    result = {
        "schemaVersion": "verification-result-v0",
        "modelVersion": MODEL_VERSION,
        "status": "UNKNOWN",
        "formalQuery": formal_query,
        "unknown": {
            "reason": reason,
            "details": f"Verification stopped safely at the {error.stage} stage.",
        },
    }
    validate(VERIFICATION_RESULT, result)
    return result


def _internal_error_result(formal_query: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schemaVersion": "verification-result-v0",
        "modelVersion": MODEL_VERSION,
        "status": "UNKNOWN",
        "formalQuery": formal_query,
        "unknown": {
            "reason": "INTERNAL_ERROR",
            "details": "Verification could not preserve its local artifact safely.",
        },
    }
    validate(VERIFICATION_RESULT, result)
    return result


def _bounded_failure_text(text: str) -> tuple[str, bool]:
    return text[:FAILURE_TEXT_LIMIT], len(text) > FAILURE_TEXT_LIMIT


def _write_failure_artifact(
    artifact_directory: Path, error: LeanBoundaryError
) -> None:
    details, details_truncated = _bounded_failure_text(error.details)
    stdout, stdout_truncated = _bounded_failure_text(error.stdout)
    stderr, stderr_truncated = _bounded_failure_text(error.stderr)
    failure = {
        "stage": error.stage,
        "details": details,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": {
            "details": details_truncated,
            "stdout": stdout_truncated,
            "stderr": stderr_truncated,
        },
    }
    (artifact_directory / FAILURE_FILENAME).write_text(
        _canonical_json(failure), encoding="utf-8"
    )


def _case_name(requested: Optional[str]) -> str:
    if requested is None:
        return f"case-{uuid.uuid4().hex[:12]}"
    if not CASE_NAME_PATTERN.fullmatch(requested):
        raise ValueError(
            "case name must be 1-64 letters, digits, hyphens, or underscores"
        )
    return requested


def verify_formal_query(
    formal_query: dict[str, Any],
    *,
    artifacts_root: Path,
    case_name: Optional[str] = None,
    timeout: float = 30.0,
) -> VerificationRun:
    """Verify one accepted query and persist its complete local evidence trail."""

    formal_query = copy.deepcopy(formal_query)
    validate(FORMAL_QUERY, formal_query)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    artifact_directory = artifacts_root / _case_name(case_name)

    try:
        checked_case = evaluate_and_check(
            formal_query,
            artifact_directory,
            timeout=timeout,
        )
    except LeanBoundaryError as error:
        # The boundary creates its case directory before invoking Lean. Preserve
        # the valid input and fail-closed result alongside any partial evidence.
        _write_failure_artifact(artifact_directory, error)
        result = _result_from_boundary_error(formal_query, error)
    else:
        result = _result_from_checked_case(checked_case)

    query_text = _canonical_json(formal_query)
    result_text = _canonical_json(result)
    (artifact_directory / FORMAL_QUERY_FILENAME).write_text(
        query_text, encoding="utf-8"
    )
    (artifact_directory / CERTIFICATE_FILENAME).write_text(
        result_text, encoding="utf-8"
    )
    return VerificationRun(
        _result_json=result_text,
        artifact_directory=artifact_directory,
    )


def _unknown_proposal(reason: str, details: str) -> dict[str, Any]:
    proposal = {
        "schemaVersion": "formalization-proposal-v0",
        "status": "UNKNOWN",
        "unknown": {
            "reason": reason,
            "details": details[:500],
        },
    }
    validate(FORMALIZATION_PROPOSAL, proposal)
    return proposal


def _print_json(value: Any, *, stream: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True), file=stream)


def _positive_timeout(value: str) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return timeout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one FormalQuery v0 through a replayable Lean theorem."
    )
    parser.add_argument("query_file", type=Path)
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=REPOSITORY_ROOT / ".artifacts",
    )
    parser.add_argument("--case-name")
    parser.add_argument("--timeout", type=_positive_timeout, default=30.0)
    arguments = parser.parse_args()

    try:
        formal_query = load_json(arguments.query_file)
    except (OSError, ValueError, json.JSONDecodeError):
        _print_json(
            _unknown_proposal(
                "MALFORMED_INPUT",
                "The manual input could not be decoded as exact JSON.",
            ),
            stream=sys.stdout,
        )
        return 2

    try:
        validate(FORMAL_QUERY, formal_query)
    except ContractValidationError:
        reason = "MALFORMED_INPUT"
        if (
            type(formal_query) is dict
            and formal_query.get("modelVersion") != MODEL_VERSION
        ):
            reason = "WRONG_MODEL_VERSION"
        _print_json(
            _unknown_proposal(
                reason,
                "The manual input does not satisfy the exact FormalQuery v0 contract.",
            ),
            stream=sys.stdout,
        )
        return 2

    try:
        run = verify_formal_query(
            formal_query,
            artifacts_root=arguments.artifacts_root,
            case_name=arguments.case_name,
            timeout=arguments.timeout,
        )
    except (OSError, ValueError):
        _print_json(_internal_error_result(formal_query), stream=sys.stdout)
        return 1

    _print_json(run.result, stream=sys.stdout)
    print(f"Artifacts: {run.artifact_directory}", file=sys.stderr)
    return 0 if run.result["status"] == "PROVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
