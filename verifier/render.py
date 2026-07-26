"""Deterministic presentation of kernel-checked decisions and exact rule sources."""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from verifier.contracts import (
    MODEL_VERSION,
    RENDERED_ANSWER,
    REPOSITORY_ROOT,
    VERIFICATION_RESULT,
    validate,
)


PUBLIC_RULE_IDS = {
    "IT-194R-SCOPE",
    "IT-194R-RETAINED",
    "IT-194R-THRESHOLD",
    "IT-194R-RELEASEGATE",
}

LEAN_RULE_IDS = {
    "s194rScope",
    "influencerProduct",
    "annualThreshold",
    "inKindReleaseGate",
}

SOURCE_IDS = {
    "ITA-1961-S194R",
    "CBDT-CIRCULAR-12-2022",
}

CANONICAL_SOURCE_RECORDS = {
    "ITA-1961-S194R": {
        "id": "ITA-1961-S194R",
        "title": "Income-tax Act, 1961 - Section 194R",
        "authority": (
            "Central Board of Direct Taxes, Department of Revenue, "
            "Ministry of Finance, Government of India"
        ),
        "official_url": "https://www.incometaxindia.gov.in/w/section-194r-4",
    },
    "CBDT-CIRCULAR-12-2022": {
        "id": "CBDT-CIRCULAR-12-2022",
        "title": "CBDT Circular No. 12 of 2022",
        "authority": (
            "Central Board of Direct Taxes, Department of Revenue, "
            "Ministry of Finance, Government of India"
        ),
        "official_url": (
            "https://incometaxindia.gov.in/communications/circular/"
            "circular-no-12-2022.pdf"
        ),
    },
}

CANONICAL_RULE_RECORDS = {
    "IT-194R-SCOPE": {
        "lean_id": "s194rScope",
        "source_id": "ITA-1961-S194R",
        "location": "Section 194R(1), main clause",
        "encoded_interpretation": (
            "A company provider supplies a resident creator a business/profession "
            "benefit and the encoded rate is 10 percent."
        ),
        "fixed_assumptions": [
            "company provider",
            "business/profession nexus",
            "resident creator",
            "PAN furnished",
        ],
    },
    "IT-194R-RETAINED": {
        "lean_id": "influencerProduct",
        "source_id": "CBDT-CIRCULAR-12-2022",
        "location": (
            "Question 6, social-media influencer example, official PDF pages 4-5"
        ),
        "encoded_interpretation": (
            "A genuinely returned promotional product is not a benefit in this "
            "example; a retained product is."
        ),
        "fixed_assumptions": [
            "entered disposition is factually correct",
            "entered FMV is accepted",
        ],
    },
    "IT-194R-THRESHOLD": {
        "lean_id": "annualThreshold",
        "source_id": "ITA-1961-S194R",
        "location": "Section 194R(1), second proviso",
        "encoded_interpretation": (
            "The v0 obligation does not trigger unless same-provider FY aggregate "
            "benefits exceed Rs 20,000."
        ),
        "fixed_assumptions": [
            "prior benefits are correctly aggregated",
            "prior benefits do not already exceed the threshold",
        ],
    },
    "IT-194R-RELEASEGATE": {
        "lean_id": "inKindReleaseGate",
        "source_id": "ITA-1961-S194R",
        "additional_source_id": "CBDT-CIRCULAR-12-2022",
        "location": (
            "Section 194R(1), first proviso; Circular 12/2022, Question 9, "
            "official PDF pages 5-6"
        ),
        "encoded_interpretation": (
            "When TDS is due on this wholly in-kind benefit, payment must be "
            "ensured before release."
        ),
        "fixed_assumptions": [
            "benefit is wholly in kind",
            "creator bears the tax",
            "provider gross-up is outside v0",
        ],
    },
}

DEFAULT_SOURCE_MAP = REPOSITORY_ROOT / "sources.yaml"
ANSWER_JSON_FILENAME = "answer.json"
ANSWER_TEXT_FILENAME = "answer.txt"
SOURCE_MAP_SNAPSHOT_FILENAME = "sources.yaml"

DISCLAIMER = (
    "Educational historical FY 2024-25 model only; not legal or tax advice. "
    "The encoded interpretations and source map need independent tax review."
)

UNKNOWN_SUMMARIES = {
    "UNSUPPORTED_INPUT": (
        "The answer is UNKNOWN because prior benefits exceed the v0 "
        "supported boundary and prior-TDS information is not an input."
    ),
    "LEAN_EVALUATION_FAILED": (
        "The answer is UNKNOWN because Lean evaluation did not complete."
    ),
    "LEAN_CHECK_FAILED": (
        "The answer is UNKNOWN because the concrete Lean equality was not checked."
    ),
    "SOURCE_MAPPING_FAILED": (
        "The answer is UNKNOWN because the checked rules could not be mapped "
        "uniquely to the frozen public source map."
    ),
    "INTERNAL_ERROR": (
        "The answer is UNKNOWN because the local verification pipeline could "
        "not complete safely."
    ),
}


class SourceMapError(ValueError):
    """The frozen source map is missing, ambiguous, or internally inconsistent."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class SourceMap:
    period: str
    retrieved_on: str
    review_status: str
    _citations_by_lean_id: dict[str, dict[str, Any]]

    def resolve(self, lean_rule_ids: list[str]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for lean_rule_id in lean_rule_ids:
            try:
                citation = self._citations_by_lean_id[lean_rule_id]
            except KeyError as error:
                raise SourceMapError(
                    f"no public rule mapping for Lean RuleId {lean_rule_id}"
                ) from error
            citations.append(copy.deepcopy(citation))
        return citations

    @property
    def status(self) -> dict[str, str]:
        return {
            "period": self.period,
            "retrievedOn": self.retrieved_on,
            "reviewStatus": self.review_status,
        }


def _required_string(mapping: dict[str, Any], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if type(value) is not str or not value.strip():
        raise SourceMapError(f"{context}.{key} must be a non-empty string")
    return value


def load_source_map(path: Path = DEFAULT_SOURCE_MAP) -> SourceMap:
    """Load only the canonical four-rule map and reject changed citation content."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.load(handle, Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise SourceMapError(f"could not load source map: {error}") from error

    if type(document) is not dict:
        raise SourceMapError("source map root must be an object")
    if document.get("schema_version") != 1:
        raise SourceMapError("source map schema_version must be 1")
    if document.get("model_version") != MODEL_VERSION:
        raise SourceMapError("source map model_version does not match FormalQuery")
    if document.get("jurisdiction") != "IN" or document.get("period") != "FY2024-25":
        raise SourceMapError("source map jurisdiction or period is outside frozen v0")
    if document.get("review_status") != "needs_independent_tax_review":
        raise SourceMapError("source map review status must remain explicit")

    retrieved_on = str(document.get("retrieved_on", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", retrieved_on):
        raise SourceMapError("source map retrieved_on must be an ISO date")

    sources_value = document.get("sources")
    if type(sources_value) is not list:
        raise SourceMapError("sources must be a list")
    sources_by_id: dict[str, dict[str, str]] = {}
    for index, source_value in enumerate(sources_value):
        context = f"sources[{index}]"
        if type(source_value) is not dict:
            raise SourceMapError(f"{context} must be an object")
        source_id = _required_string(source_value, "id", context=context)
        if source_id in sources_by_id:
            raise SourceMapError(f"duplicate source id: {source_id}")
        if source_value != CANONICAL_SOURCE_RECORDS.get(source_id):
            raise SourceMapError(f"{context} differs from the frozen source record")
        official_url = _required_string(source_value, "official_url", context=context)
        if not official_url.startswith("https://"):
            raise SourceMapError(f"{context}.official_url must use https")
        sources_by_id[source_id] = {
            "sourceId": source_id,
            "title": _required_string(source_value, "title", context=context),
            "authority": _required_string(source_value, "authority", context=context),
            "officialUrl": official_url,
        }
    if set(sources_by_id) != SOURCE_IDS:
        raise SourceMapError("source map must contain exactly the two frozen sources")

    rules_value = document.get("rules")
    if type(rules_value) is not dict or set(rules_value) != PUBLIC_RULE_IDS:
        raise SourceMapError("source map must contain exactly the four public rule ids")

    citations_by_lean_id: dict[str, dict[str, Any]] = {}
    for public_rule_id, rule_value in rules_value.items():
        context = f"rules.{public_rule_id}"
        if type(rule_value) is not dict:
            raise SourceMapError(f"{context} must be an object")
        if rule_value != CANONICAL_RULE_RECORDS[public_rule_id]:
            raise SourceMapError(f"{context} differs from the frozen rule mapping")
        lean_id = _required_string(rule_value, "lean_id", context=context)
        if lean_id not in LEAN_RULE_IDS:
            raise SourceMapError(f"{context}.lean_id is not a frozen Lean RuleId")
        if lean_id in citations_by_lean_id:
            raise SourceMapError(f"duplicate Lean RuleId mapping: {lean_id}")

        source_ids = [_required_string(rule_value, "source_id", context=context)]
        additional_source_id = rule_value.get("additional_source_id")
        if additional_source_id is not None:
            if type(additional_source_id) is not str or not additional_source_id:
                raise SourceMapError(
                    f"{context}.additional_source_id must be a non-empty string"
                )
            source_ids.append(additional_source_id)
        if len(set(source_ids)) != len(source_ids):
            raise SourceMapError(f"{context} cites the same source more than once")
        try:
            cited_sources = [sources_by_id[source_id] for source_id in source_ids]
        except KeyError as error:
            raise SourceMapError(
                f"{context} points to missing source {error.args[0]}"
            ) from error

        citations_by_lean_id[lean_id] = {
            "ruleId": public_rule_id,
            "leanId": lean_id,
            "location": _required_string(rule_value, "location", context=context),
            "encodedInterpretation": _required_string(
                rule_value, "encoded_interpretation", context=context
            ),
            "sources": copy.deepcopy(cited_sources),
        }

    if set(citations_by_lean_id) != LEAN_RULE_IDS:
        raise SourceMapError("each frozen Lean RuleId needs exactly one public mapping")

    return SourceMap(
        period="FY2024-25",
        retrieved_on=retrieved_on,
        review_status="needs_independent_tax_review",
        _citations_by_lean_id=citations_by_lean_id,
    )


def format_money(paise: int) -> str:
    """Format a checked paise amount without changing or rounding it."""

    rupees, remaining_paise = divmod(paise, 100)
    return f"₹{rupees:,}.{remaining_paise:02d}"


def _detail(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _source_mapping_unknown(
    verification_status: str,
) -> dict[str, Any]:
    rendered = {
        "schemaVersion": "rendered-answer-v0",
        "modelVersion": MODEL_VERSION,
        "status": "UNKNOWN",
        "verificationStatus": verification_status,
        "unknownReason": "SOURCE_MAPPING_FAILED",
        "summary": UNKNOWN_SUMMARIES["SOURCE_MAPPING_FAILED"],
        "details": [],
        "citations": [],
        "disclaimer": DISCLAIMER,
    }
    validate(RENDERED_ANSWER, rendered)
    return rendered


def render_verification_result(
    result: dict[str, Any],
    *,
    source_map_path: Path = DEFAULT_SOURCE_MAP,
) -> dict[str, Any]:
    """Render only fields and RuleIds already present in a checked result."""

    result = copy.deepcopy(result)
    validate(VERIFICATION_RESULT, result)
    decision = result.get("decision")

    if (
        result["status"] == "UNKNOWN"
        and result["unknown"]["reason"] == "SOURCE_MAPPING_FAILED"
    ):
        return _source_mapping_unknown("UNKNOWN")

    if decision is None:
        unknown_reason = result["unknown"]["reason"]
        rendered = {
            "schemaVersion": "rendered-answer-v0",
            "modelVersion": MODEL_VERSION,
            "status": "UNKNOWN",
            "verificationStatus": "UNKNOWN",
            "unknownReason": unknown_reason,
            "summary": UNKNOWN_SUMMARIES[unknown_reason],
            "details": [],
            "citations": [],
            "disclaimer": DISCLAIMER,
        }
        validate(RENDERED_ANSWER, rendered)
        return rendered

    if decision["kind"] == "answered":
        checked_rule_ids = decision["answer"]["appliedRules"]
    else:
        checked_rule_ids = decision["citedRules"]

    try:
        source_map = load_source_map(source_map_path)
        citations = source_map.resolve(checked_rule_ids)
    except SourceMapError:
        return _source_mapping_unknown(result["status"])

    if result["status"] == "UNKNOWN":
        unknown_reason = result["unknown"]["reason"]
        rendered = {
            "schemaVersion": "rendered-answer-v0",
            "modelVersion": MODEL_VERSION,
            "status": "UNKNOWN",
            "verificationStatus": "UNKNOWN",
            "unknownReason": unknown_reason,
            "summary": UNKNOWN_SUMMARIES[unknown_reason],
            "details": [],
            "citations": citations,
            "disclaimer": DISCLAIMER,
            "sourceStatus": source_map.status,
        }
        validate(RENDERED_ANSWER, rendered)
        return rendered

    answer = decision["answer"]
    qualification = "qualifies" if answer["benefitQualifies"] else "does not qualify"
    release_gate = "is required" if answer["releaseGateRequired"] else "is not required"
    summary = (
        f"Under the frozen model, the current product {qualification}. "
        f"Aggregate benefits are {format_money(answer['aggregateBenefitsPaise'])}; "
        f"modeled TDS due is {format_money(answer['tdsDuePaise'])}; and the "
        f"in-kind release gate {release_gate}."
    )
    rendered = {
        "schemaVersion": "rendered-answer-v0",
        "modelVersion": MODEL_VERSION,
        "status": "PROVED",
        "verificationStatus": "PROVED",
        "summary": summary,
        "details": [
            _detail(
                "Current product qualifies",
                "Yes" if answer["benefitQualifies"] else "No",
            ),
            _detail("Current benefit", format_money(answer["currentBenefitPaise"])),
            _detail(
                "Financial-year aggregate",
                format_money(answer["aggregateBenefitsPaise"]),
            ),
            _detail(
                "Rs 20,000 threshold exceeded",
                "Yes" if answer["thresholdExceeded"] else "No",
            ),
            _detail("Modeled TDS due", format_money(answer["tdsDuePaise"])),
            _detail(
                "Release gate required",
                "Yes" if answer["releaseGateRequired"] else "No",
            ),
        ],
        "citations": citations,
        "disclaimer": DISCLAIMER,
        "sourceStatus": source_map.status,
    }
    validate(RENDERED_ANSWER, rendered)
    return rendered


def render_text(rendered: dict[str, Any]) -> str:
    """Create fixed prose from a validated RenderedAnswer."""

    validate(RENDERED_ANSWER, rendered)
    lines = [f"Status: {rendered['status']}", rendered["summary"]]
    if rendered["details"]:
        lines.extend(["", "Checked result:"])
        lines.extend(
            f"- {detail['label']}: {detail['value']}"
            for detail in rendered["details"]
        )
    if rendered["citations"]:
        lines.extend(["", "Checked rules and sources:"])
        for citation in rendered["citations"]:
            lines.append(f"- {citation['ruleId']} — {citation['location']}")
            lines.extend(
                f"  {source['title']}: {source['officialUrl']}"
                for source in citation["sources"]
            )
    if "sourceStatus" in rendered:
        source_status = rendered["sourceStatus"]
        lines.extend(
            [
                "",
                (
                    "Source status: "
                    f"{source_status['period']}; retrieved "
                    f"{source_status['retrievedOn']}; "
                    f"{source_status['reviewStatus']}."
                ),
            ]
        )
    lines.extend(["", rendered["disclaimer"]])
    return "\n".join(lines) + "\n"


def write_rendered_artifacts(
    artifact_directory: Path,
    rendered: dict[str, Any],
    *,
    source_map_path: Path = DEFAULT_SOURCE_MAP,
) -> None:
    """Preserve both machine-readable and plain-text deterministic answers."""

    validate(RENDERED_ANSWER, rendered)
    (artifact_directory / ANSWER_JSON_FILENAME).write_text(
        json.dumps(rendered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (artifact_directory / ANSWER_TEXT_FILENAME).write_text(
        render_text(rendered), encoding="utf-8"
    )
    if source_map_path.is_file():
        shutil.copy2(
            source_map_path,
            artifact_directory / SOURCE_MAP_SNAPSHOT_FILENAME,
        )
