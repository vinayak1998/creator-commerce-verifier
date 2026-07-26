"""Deterministic two-pass boundary between accepted JSON and Lean.

This module translates already validated values to Lean syntax, runs Lean, and
translates Lean's explicit JSON protocol back to data. It never implements or
recalculates the Section 194R assessment.
"""

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verifier.contracts import (
    CHECKED_DECISION_REFERENCE,
    FORMAL_QUERY,
    REPOSITORY_ROOT,
    ContractValidationError,
    loads_json,
    validate,
    validate_reference,
)


CANDIDATE_SCHEMA_VERSION = "lean-candidate-v0"
CHECKED_SCHEMA_VERSION = "lean-checked-v0"
MODEL_VERSION = "in-s194r-fy2024-25-v0"

EVALUATE_FILENAME = "Evaluate.lean"
CHECK_FILENAME = "GeneratedCase.lean"
CANDIDATE_FILENAME = "candidate.json"
CHECKED_FILENAME = "checked.json"
LEAN_MEMORY_MEGABYTES = 512

DISPOSITION_LEAN = {
    "retained": ".retained",
    "returned": ".returned",
}

RULE_ID_LEAN = {
    "s194rScope": ".s194rScope",
    "influencerProduct": ".influencerProduct",
    "annualThreshold": ".annualThreshold",
    "inKindReleaseGate": ".inKindReleaseGate",
}

UNSUPPORTED_REASON_LEAN = {
    "priorBenefitsNeedPriorTds": ".priorBenefitsNeedPriorTds",
}


class LeanBoundaryError(RuntimeError):
    """A fail-closed error at a named Lean boundary stage."""

    def __init__(
        self,
        stage: str,
        details: str,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.stage = stage
        self.details = details
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"{stage}: {details}")


@dataclass(frozen=True)
class CheckedCase:
    """Artifacts returned only after the concrete equality has passed Lean."""

    _formal_query_json: str
    _decision_json: str
    _candidate_output_json: str
    _checked_output_json: str
    case_directory: Path
    evaluate_source: str
    theorem_source: str

    @property
    def formal_query(self) -> dict[str, Any]:
        """Return a detached copy; callers cannot mutate the checked snapshot."""

        return loads_json(self._formal_query_json)

    @property
    def decision(self) -> dict[str, Any]:
        """Return a detached copy of the kernel-checked decision."""

        return loads_json(self._decision_json)

    @property
    def candidate_output(self) -> dict[str, Any]:
        return loads_json(self._candidate_output_json)

    @property
    def checked_output(self) -> dict[str, Any]:
        return loads_json(self._checked_output_json)

    @property
    def replay_command(self) -> list[str]:
        return [
            "lake",
            "env",
            "lean",
            "--trust=0",
            f"--memory={LEAN_MEMORY_MEGABYTES}",
            "--run",
            str(self.case_directory / CHECK_FILENAME),
        ]

    @property
    def replay_cwd(self) -> Path:
        return REPOSITORY_ROOT

    @property
    def replay_build_command(self) -> list[str]:
        return ["lake", "build"]


def _facts_source(facts: dict[str, Any]) -> str:
    disposition = DISPOSITION_LEAN[facts["productDisposition"]]
    return (
        "  { priorBenefitsPaise := "
        f"{facts['priorBenefitsPaise']}\n"
        "    productFmvPaise := "
        f"{facts['productFmvPaise']}\n"
        "    productDisposition := "
        f"{disposition} }}"
    )


def generate_evaluate_source(formal_query: dict[str, Any]) -> str:
    """Generate the first pass; the only operation is Lean's `assess`."""

    validate(FORMAL_QUERY, formal_query)
    facts_source = _facts_source(formal_query["facts"])
    return f"""import CreatorCommerce.CaseProtocol

open CreatorCommerce.Section194R
open CreatorCommerce.CaseProtocol

set_option autoImplicit false

def caseFacts : Facts :=
{facts_source}

def main : IO Unit :=
  emit (candidateJson caseFacts)
"""


def _bool_source(value: bool) -> str:
    return "true" if value else "false"


def _rules_source(rules: list[str]) -> str:
    constructors = ", ".join(RULE_ID_LEAN[rule] for rule in rules)
    return f"[{constructors}]"


def _decision_source(decision: dict[str, Any]) -> str:
    validate_reference(CHECKED_DECISION_REFERENCE, decision)
    if decision["kind"] == "unsupported":
        reason = UNSUPPORTED_REASON_LEAN[decision["reason"]]
        rules = _rules_source(decision["citedRules"])
        return f"  .unsupported {reason} {rules}"

    answer = decision["answer"]
    rules = _rules_source(answer["appliedRules"])
    return (
        "  .answered\n"
        f"    {{ benefitQualifies := {_bool_source(answer['benefitQualifies'])}\n"
        f"      currentBenefitPaise := {answer['currentBenefitPaise']}\n"
        f"      aggregateBenefitsPaise := {answer['aggregateBenefitsPaise']}\n"
        f"      thresholdExceeded := {_bool_source(answer['thresholdExceeded'])}\n"
        f"      tdsDuePaise := {answer['tdsDuePaise']}\n"
        f"      releaseGateRequired := {_bool_source(answer['releaseGateRequired'])}\n"
        f"      appliedRules := {rules} }}"
    )


def generate_check_source(
    formal_query: dict[str, Any], decision: dict[str, Any]
) -> str:
    """Generate the concrete candidate equality checked in the second pass."""

    validate(FORMAL_QUERY, formal_query)
    facts_source = _facts_source(formal_query["facts"])
    decision_source = _decision_source(decision)
    return f"""import CreatorCommerce.CaseProtocol

open CreatorCommerce.Section194R
open CreatorCommerce.CaseProtocol

set_option autoImplicit false

def caseFacts : Facts :=
{facts_source}

def expectedDecision : Decision :=
{decision_source}

theorem checkedCase :
    assess caseFacts = expectedDecision := by
  decide

def main : IO Unit := do
  let _proof : assess caseFacts = expectedDecision := checkedCase
  emit (checkedJson caseFacts expectedDecision)
"""


def _run_lean(source_path: Path, *, trust_zero: bool, timeout: float) -> str:
    command = ["lake", "env", "lean"]
    if trust_zero:
        command.append("--trust=0")
    command.extend(
        [f"--memory={LEAN_MEMORY_MEGABYTES}", "--run", str(source_path)]
    )

    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LeanBoundaryError("lean-process", str(error)) from error

    if completed.returncode != 0:
        raise LeanBoundaryError(
            "lean-check" if trust_zero else "lean-evaluation",
            f"Lean exited with status {completed.returncode}",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise LeanBoundaryError(
            "lean-protocol",
            f"expected one JSON line, received {len(lines)}",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    return lines[0]


def _build_model(*, timeout: float) -> None:
    """Bring imported `.olean` files up to date with checked-in Lean sources."""

    try:
        completed = subprocess.run(
            ["lake", "build"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LeanBoundaryError("lean-build", str(error)) from error

    if completed.returncode != 0:
        raise LeanBoundaryError(
            "lean-build",
            f"lake build exited with status {completed.returncode}",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _parse_case_output(
    text: str,
    *,
    expected_schema_version: str,
    expected_facts: dict[str, Any],
) -> dict[str, Any]:
    try:
        output = loads_json(text)
    except (ValueError, json.JSONDecodeError) as error:
        raise LeanBoundaryError("lean-protocol", str(error), stdout=text) from error

    if type(output) is not dict:
        raise LeanBoundaryError("lean-protocol", "Lean output must be a JSON object")
    if set(output) != {"schemaVersion", "modelVersion", "facts", "decision"}:
        raise LeanBoundaryError("lean-protocol", "Lean output has unexpected fields")
    if output["schemaVersion"] != expected_schema_version:
        raise LeanBoundaryError("lean-protocol", "Lean schema version mismatch")
    if output["modelVersion"] != MODEL_VERSION:
        raise LeanBoundaryError("lean-protocol", "Lean model version mismatch")
    try:
        validate(
            FORMAL_QUERY,
            {
                "schemaVersion": FORMAL_QUERY,
                "modelVersion": MODEL_VERSION,
                "facts": output["facts"],
            },
        )
    except ContractValidationError as error:
        raise LeanBoundaryError("lean-protocol", str(error)) from error
    if output["facts"] != expected_facts:
        raise LeanBoundaryError("lean-protocol", "Lean facts do not match FormalQuery")

    try:
        validate_reference(CHECKED_DECISION_REFERENCE, output["decision"])
    except ContractValidationError as error:
        raise LeanBoundaryError("lean-protocol", str(error)) from error
    return output


def evaluate_and_check(
    formal_query: dict[str, Any],
    case_directory: Path,
    *,
    timeout: float = 30.0,
) -> CheckedCase:
    """Run both Lean passes and return only a kernel-checked concrete case."""

    formal_query = copy.deepcopy(formal_query)
    validate(FORMAL_QUERY, formal_query)
    case_directory.mkdir(parents=True, exist_ok=False)
    _build_model(timeout=timeout)

    evaluate_source = generate_evaluate_source(formal_query)
    evaluate_path = case_directory / EVALUATE_FILENAME
    evaluate_path.write_text(evaluate_source, encoding="utf-8")

    candidate_text = _run_lean(evaluate_path, trust_zero=False, timeout=timeout)
    candidate = _parse_case_output(
        candidate_text,
        expected_schema_version=CANDIDATE_SCHEMA_VERSION,
        expected_facts=formal_query["facts"],
    )
    (case_directory / CANDIDATE_FILENAME).write_text(
        json.dumps(candidate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    theorem_source = generate_check_source(formal_query, candidate["decision"])
    check_path = case_directory / CHECK_FILENAME
    check_path.write_text(theorem_source, encoding="utf-8")

    checked_text = _run_lean(check_path, trust_zero=True, timeout=timeout)
    checked = _parse_case_output(
        checked_text,
        expected_schema_version=CHECKED_SCHEMA_VERSION,
        expected_facts=formal_query["facts"],
    )
    if checked["decision"] != candidate["decision"]:
        raise LeanBoundaryError(
            "lean-protocol", "checked decision does not match candidate decision"
        )
    (case_directory / CHECKED_FILENAME).write_text(
        json.dumps(checked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return CheckedCase(
        _formal_query_json=json.dumps(formal_query, sort_keys=True),
        _decision_json=json.dumps(checked["decision"], sort_keys=True),
        _candidate_output_json=json.dumps(candidate, sort_keys=True),
        _checked_output_json=json.dumps(checked, sort_keys=True),
        case_directory=case_directory,
        evaluate_source=evaluate_source,
        theorem_source=theorem_source,
    )
