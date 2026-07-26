"""Load and enforce the versioned JSON boundary contracts.

This module validates structure only. It contains no Section 194R decision
logic; accepted facts still have to cross the deterministic Lean boundary.
"""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIRECTORY = REPOSITORY_ROOT / "contracts"

FORMAL_QUERY = "formal-query-v0"
FORMALIZATION_PROPOSAL = "formalization-proposal-v0"
VERIFICATION_RESULT = "verification-result-v0"

SCHEMA_FILES = {
    FORMAL_QUERY: "formal-query-v0.schema.json",
    FORMALIZATION_PROPOSAL: "formalization-proposal-v0.schema.json",
    VERIFICATION_RESULT: "verification-result-v0.schema.json",
}


class ContractValidationError(ValueError):
    """Raised when JSON does not satisfy the selected boundary contract."""

    def __init__(self, contract: str, errors: list[str]):
        self.contract = contract
        self.errors = tuple(errors)
        super().__init__(f"{contract}: " + "; ".join(errors))


def _is_exact_integer(_checker: Any, instance: Any) -> bool:
    return type(instance) is int


ExactDraft202012Validator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", _is_exact_integer
    ),
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_integer_number(token: str) -> NoReturn:
    raise ValueError(f"non-integer JSON number: {token}")


def load_json(path: Path) -> Any:
    """Read JSON while rejecting duplicate object keys."""

    with path.open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_non_integer_number,
        )


@lru_cache(maxsize=1)
def _schemas() -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for name, filename in SCHEMA_FILES.items():
        schema = load_json(CONTRACTS_DIRECTORY / filename)
        Draft202012Validator.check_schema(schema)
        loaded[name] = schema
    return loaded


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in _schemas().values()
    ]
    return Registry().with_resources(resources)


def validate(contract: str, value: Any) -> None:
    """Validate a decoded JSON value against a named v0 contract."""

    try:
        schema = _schemas()[contract]
    except KeyError as error:
        raise ValueError(f"unknown contract: {contract}") from error

    validator = ExactDraft202012Validator(schema, registry=_registry())
    failures = sorted(
        validator.iter_errors(value),
        key=lambda failure: [str(part) for part in failure.absolute_path],
    )
    if not failures:
        return

    messages: list[str] = []
    for failure in failures:
        path = ".".join(str(part) for part in failure.absolute_path) or "$"
        messages.append(f"{path}: {failure.message}")
    raise ContractValidationError(contract, messages)


def load_and_validate(contract: str, path: Path) -> Any:
    """Load a JSON file and validate it against the selected contract."""

    value = load_json(path)
    validate(contract, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate one JSON file against a creator-commerce v0 contract."
    )
    parser.add_argument("contract", choices=sorted(SCHEMA_FILES))
    parser.add_argument("json_file", type=Path)
    arguments = parser.parse_args()

    try:
        load_and_validate(arguments.contract, arguments.json_file)
    except (ContractValidationError, OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"INVALID: {error}\n")

    print(f"VALID: {arguments.json_file} ({arguments.contract})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
