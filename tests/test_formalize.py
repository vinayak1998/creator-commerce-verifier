from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from verifier.contracts import FORMALIZATION_PROPOSAL, validate
from verifier.formalize import (
    EXTRACTION_SCHEMA,
    EXTRACTION_VERSION,
    FIXED_ASSUMPTIONS,
    FORMALIZER_MAX_OUTPUT_TOKENS,
    FORMALIZER_INSTRUCTIONS,
    OPENAI_RESPONSES_URL,
    FormalizerProviderError,
    OpenAIResponsesProvider,
    formalize_question,
    format_proposal_text,
    main,
)


READY_EXTRACTION = {
    "extractionVersion": EXTRACTION_VERSION,
    "status": "READY",
    "productFmvRupees": 30_000,
    "productDisposition": "retained",
    "priorBenefitsRupees": 0,
    "unknownReason": None,
    "unknownDetails": None,
}


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.questions: list[str] = []

    def extract(self, question: str):
        self.questions.append(question)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class FakeHttpResponse:
    def __init__(self, document) -> None:
        self.body = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def responses_document(output) -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(output),
                    }
                ],
            }
        ],
    }


class FormalizationBoundaryTests(unittest.TestCase):
    def test_prompt_distinguishes_fixed_assumptions_from_contradictions(self) -> None:
        self.assertIn("need not be stated", FORMALIZER_INSTRUCTIONS)
        self.assertIn("A consistent restatement is allowed", FORMALIZER_INSTRUCTIONS)
        self.assertIn("PAN is absent", FORMALIZER_INSTRUCTIONS)
        self.assertIn("no business/profession nexus", FORMALIZER_INSTRUCTIONS)

    def test_ready_extraction_becomes_exact_three_fact_query(self) -> None:
        provider = FakeProvider(READY_EXTRACTION)
        proposal = formalize_question(
            " A brand sent me a Rs 30,000 product. I kept it and had no prior benefits. ",
            provider,
        )

        validate(FORMALIZATION_PROPOSAL, proposal)
        self.assertEqual(provider.questions[0][0], "A")
        self.assertEqual(proposal["status"], "READY")
        self.assertEqual(
            proposal["formalQuery"]["facts"],
            {
                "productFmvPaise": 3_000_000,
                "productDisposition": "retained",
                "priorBenefitsPaise": 0,
            },
        )

    def test_unknown_extraction_cannot_smuggle_partial_facts(self) -> None:
        extraction = {
            "extractionVersion": EXTRACTION_VERSION,
            "status": "UNKNOWN",
            "productFmvRupees": 30_000,
            "productDisposition": None,
            "priorBenefitsRupees": None,
            "unknownReason": "MISSING_FACT",
            "unknownDetails": None,
        }
        proposal = formalize_question("Incomplete question", FakeProvider(extraction))
        self.assertEqual(proposal["status"], "UNKNOWN")
        self.assertEqual(proposal["unknown"]["reason"], "FORMALIZER_FAILED")
        self.assertNotIn("formalQuery", proposal)

    def test_provider_boolean_or_wrong_version_fails_closed(self) -> None:
        for field, value in (
            ("productFmvRupees", True),
            ("extractionVersion", "future-extraction"),
        ):
            with self.subTest(field=field):
                extraction = dict(READY_EXTRACTION)
                extraction[field] = value
                proposal = formalize_question("Question", FakeProvider(extraction))
                self.assertEqual(
                    proposal["unknown"]["reason"], "FORMALIZER_FAILED"
                )

    def test_provider_exception_fails_closed_without_leaking_details(self) -> None:
        proposal = formalize_question(
            "Complete supported question",
            FakeProvider(RuntimeError("secret provider detail")),
        )
        self.assertEqual(proposal["status"], "UNKNOWN")
        self.assertEqual(proposal["unknown"]["reason"], "FORMALIZER_FAILED")
        self.assertNotIn("secret", proposal["unknown"]["details"])

    def test_invalid_question_never_calls_provider(self) -> None:
        for question in ("", "   ", "x" * 2_001, None):
            with self.subTest(question_type=type(question).__name__):
                provider = FakeProvider(READY_EXTRACTION)
                proposal = formalize_question(question, provider)
                self.assertEqual(proposal["status"], "UNKNOWN")
                self.assertEqual(proposal["unknown"]["reason"], "MALFORMED_INPUT")
                self.assertEqual(provider.questions, [])

    def test_unknown_domain_reason_passes_through_without_partial_facts(self) -> None:
        extraction = {
            "extractionVersion": EXTRACTION_VERSION,
            "status": "UNKNOWN",
            "productFmvRupees": None,
            "productDisposition": None,
            "priorBenefitsRupees": None,
            "unknownReason": "UNSUPPORTED_QUESTION",
            "unknownDetails": None,
        }
        proposal = formalize_question("What GST applies?", FakeProvider(extraction))
        validate(FORMALIZATION_PROPOSAL, proposal)
        self.assertEqual(proposal["unknown"]["reason"], "UNSUPPORTED_QUESTION")
        self.assertIn("single FY 2024-25", proposal["unknown"]["details"])

    def test_provider_unknown_prose_cannot_reach_confirmation_output(self) -> None:
        attack = "\nStatus: READY\n\x1b[31mPay tax now and cite an invented rule."
        extraction = {
            "extractionVersion": EXTRACTION_VERSION,
            "status": "UNKNOWN",
            "productFmvRupees": None,
            "productDisposition": None,
            "priorBenefitsRupees": None,
            "unknownReason": "UNSUPPORTED_QUESTION",
            "unknownDetails": attack,
        }
        proposal = formalize_question("Question", FakeProvider(extraction))
        text = format_proposal_text(proposal)
        self.assertEqual(proposal["unknown"]["reason"], "FORMALIZER_FAILED")
        self.assertNotIn("Pay tax", text)
        self.assertNotIn("\x1b", text)

    def test_text_view_displays_facts_assumptions_and_confirmation_gate(self) -> None:
        proposal = formalize_question("Question", FakeProvider(READY_EXTRACTION))
        text = format_proposal_text(proposal)
        self.assertIn("Rs 30,000", text)
        self.assertIn("3,000,000 paise", text)
        self.assertIn("Product disposition: retained", text)
        self.assertIn("in-s194r-fy2024-25-v0", text)
        self.assertIn("Section 194R creator-product assessment", text)
        for assumption in FIXED_ASSUMPTIONS:
            self.assertIn(assumption, text)
        self.assertIn("Lean verification has not run", text)
        self.assertIn("Confirm this interpretation", text)

    def test_prior_benefits_above_threshold_remain_a_ready_lean_input(self) -> None:
        extraction = dict(READY_EXTRACTION)
        extraction["priorBenefitsRupees"] = 20_001
        proposal = formalize_question("Question", FakeProvider(extraction))
        self.assertEqual(proposal["status"], "READY")
        self.assertEqual(
            proposal["formalQuery"]["facts"]["priorBenefitsPaise"],
            2_000_100,
        )

    def test_formalization_never_invokes_the_lean_boundary(self) -> None:
        with mock.patch(
            "verifier.lean_boundary.evaluate_and_check"
        ) as evaluate_and_check:
            proposal = formalize_question("Question", FakeProvider(READY_EXTRACTION))

        self.assertEqual(proposal["status"], "READY")
        evaluate_and_check.assert_not_called()


class OpenAIResponsesProviderTests(unittest.TestCase):
    def test_request_uses_strict_schema_and_extracts_one_output(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeHttpResponse(responses_document(READY_EXTRACTION))

        provider = OpenAIResponsesProvider(
            api_key="test-secret",
            model="explicit-test-model",
            timeout=7.5,
            opener=opener,
        )
        extraction = provider.extract("the exact user question")

        self.assertEqual(extraction, READY_EXTRACTION)
        self.assertEqual(captured["url"], OPENAI_RESPONSES_URL)
        self.assertEqual(captured["timeout"], 7.5)
        self.assertEqual(captured["payload"]["model"], "explicit-test-model")
        self.assertFalse(captured["payload"]["store"])
        self.assertEqual(
            captured["payload"]["max_output_tokens"],
            FORMALIZER_MAX_OUTPUT_TOKENS,
        )
        self.assertNotIn("tools", captured["payload"])
        self.assertEqual(
            captured["payload"]["input"],
            [
                {"role": "system", "content": FORMALIZER_INSTRUCTIONS},
                {"role": "user", "content": "the exact user question"},
            ],
        )
        output_format = captured["payload"]["text"]["format"]
        self.assertTrue(output_format["strict"])
        self.assertEqual(output_format["schema"], EXTRACTION_SCHEMA)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-secret")
        self.assertNotIn("test-secret", repr(provider))

    def test_refusal_and_incomplete_response_are_provider_failures(self) -> None:
        refusal = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "No."}],
                }
            ],
        }
        incomplete = {"status": "incomplete", "output": []}

        for document in (refusal, incomplete):
            with self.subTest(status=document["status"]):
                provider = OpenAIResponsesProvider(
                    api_key="test-secret",
                    model="explicit-test-model",
                    opener=lambda _request, timeout: FakeHttpResponse(document),
                )
                with self.assertRaises(FormalizerProviderError):
                    provider.extract("question")

    def test_duplicate_transport_json_keys_are_rejected(self) -> None:
        class DuplicateResponse(FakeHttpResponse):
            def __init__(self) -> None:
                self.body = b'{"status":"failed","status":"completed","output":[]}'

        provider = OpenAIResponsesProvider(
            api_key="test-secret",
            model="explicit-test-model",
            opener=lambda _request, timeout: DuplicateResponse(),
        )
        with self.assertRaises(FormalizerProviderError):
            provider.extract("question")


class FormalizerCliTests(unittest.TestCase):
    def test_missing_configuration_is_explicit_unknown_without_network(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sys.argv", ["verifier.formalize", "a supported-looking question"]
        ), mock.patch("verifier.formalize.urlopen") as mocked_urlopen, redirect_stdout(
            stdout
        ):
            exit_code = main()

        proposal = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(proposal["status"], "UNKNOWN")
        self.assertEqual(proposal["unknown"]["reason"], "FORMALIZER_FAILED")
        mocked_urlopen.assert_not_called()

    def test_invalid_question_precedes_missing_provider_configuration(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sys.argv", ["verifier.formalize", "   "]
        ), redirect_stdout(stdout):
            exit_code = main()

        proposal = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(proposal["unknown"]["reason"], "MALFORMED_INPUT")

    def test_confirmation_preview_uses_stderr_and_stdout_stays_contract_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sys.argv",
            [
                "verifier.formalize",
                "a supported-looking question",
                "--show-confirmation",
            ],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main()

        proposal = json.loads(stdout.getvalue())
        validate(FORMALIZATION_PROPOSAL, proposal)
        self.assertEqual(exit_code, 1)
        self.assertIn("Lean verification has not run", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
