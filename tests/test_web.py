from __future__ import annotations

import argparse
import copy
import io
import re
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from urllib.parse import urlencode

from verifier.formalize import EXTRACTION_VERSION, FIXED_ASSUMPTIONS
from verifier.verify import verify_and_render_formal_query
from verifier.web import (
    MAX_FORM_BYTES,
    LocalVerifierApp,
    ProposalStore,
    _positive_timeout,
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

READY_PROPOSAL = {
    "schemaVersion": "formalization-proposal-v0",
    "status": "READY",
    "formalQuery": {
        "schemaVersion": "formal-query-v0",
        "modelVersion": "in-s194r-fy2024-25-v0",
        "facts": {
            "productFmvPaise": 3_000_000,
            "productDisposition": "retained",
            "priorBenefitsPaise": 0,
        },
    },
}


class FakeProvider:
    def __init__(self, output) -> None:
        self.output = output
        self.questions: list[str] = []

    def extract(self, question: str):
        self.questions.append(question)
        return copy.deepcopy(self.output)


def invoke_wsgi(
    application,
    method: str,
    path: str,
    *,
    form: dict[str, str] | None = None,
    raw_body: bytes | None = None,
    content_length: int | None = None,
    authority: str = "127.0.0.1:8765",
    origin: str | None = None,
) -> tuple[str, dict[str, str], str]:
    if raw_body is None:
        raw_body = urlencode(form or {}).encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(raw_body) if content_length is None else content_length),
        "wsgi.input": io.BytesIO(raw_body),
        "HTTP_HOST": authority,
    }
    if method == "POST":
        environ["HTTP_ORIGIN"] = origin or f"http://{authority}"
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application(environ, start_response)).decode("utf-8")
    return captured["status"], captured["headers"], body


def confirmation_token(body: str) -> str:
    match = re.search(r'name="confirmationToken" value="([^"]+)"', body)
    if match is None:
        raise AssertionError("confirmation token was not rendered")
    return match.group(1)


class ProposalStoreTests(unittest.TestCase):
    def test_token_is_server_side_one_time_and_expires(self) -> None:
        now = [100.0]
        store = ProposalStore(ttl_seconds=10, clock=lambda: now[0])
        token = store.issue(READY_PROPOSAL)

        confirmed = store.consume(token)
        self.assertEqual(confirmed, READY_PROPOSAL)
        self.assertIsNot(confirmed, READY_PROPOSAL)
        self.assertIsNone(store.consume(token))

        expired_token = store.issue(READY_PROPOSAL)
        now[0] += 11
        self.assertIsNone(store.consume(expired_token))

    def test_store_is_bounded_and_rejects_unknown_proposals(self) -> None:
        store = ProposalStore(max_entries=1)
        first = store.issue(READY_PROPOSAL)
        second = store.issue(READY_PROPOSAL)
        self.assertIsNone(store.consume(first))
        self.assertEqual(store.consume(second), READY_PROPOSAL)

        unknown = {
            "schemaVersion": "formalization-proposal-v0",
            "status": "UNKNOWN",
            "unknown": {
                "reason": "MISSING_FACT",
                "details": "One required fact is missing.",
            },
        }
        with self.assertRaises(ValueError):
            store.issue(unknown)


class LocalWebBoundaryTests(unittest.TestCase):
    def test_landing_has_no_script_and_sets_restrictive_headers(self) -> None:
        app = LocalVerifierApp(
            FakeProvider(READY_EXTRACTION),
            lambda _query: self.fail("verification must not run on GET"),
            expected_authority="127.0.0.1:8765",
        )
        status, headers, body = invoke_wsgi(app, "GET", "/")
        self.assertEqual(status, "200 OK")
        self.assertNotIn("<script", body.lower())
        self.assertIn("form-action 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn(
            "sends the question text to the configured OpenAI Responses API",
            body,
        )
        self.assertIn("API key remains server-side", body)

    def test_unknown_formalization_never_creates_confirmation(self) -> None:
        extraction = {
            "extractionVersion": EXTRACTION_VERSION,
            "status": "UNKNOWN",
            "productFmvRupees": None,
            "productDisposition": None,
            "priorBenefitsRupees": None,
            "unknownReason": "MISSING_FACT",
            "unknownDetails": None,
        }
        app = LocalVerifierApp(
            FakeProvider(extraction),
            lambda _query: self.fail("UNKNOWN must never reach verification"),
            expected_authority="127.0.0.1:8765",
        )
        status, _headers, body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": "The product was retained."},
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("UNKNOWN", body)
        self.assertIn("Lean verification has not run", body)
        self.assertNotIn("confirmationToken", body)

    def test_direct_or_ambiguous_confirmation_never_runs_verifier(self) -> None:
        calls = []
        app = LocalVerifierApp(
            FakeProvider(READY_EXTRACTION),
            lambda query: calls.append(query),
            expected_authority="127.0.0.1:8765",
        )
        status, _headers, body = invoke_wsgi(
            app,
            "POST",
            "/verify",
            form={"confirmationToken": "not-a-server-token"},
        )
        self.assertEqual(status, "400 Bad Request")
        self.assertIn("Confirmation required", body)
        self.assertEqual(calls, [])

        status, _headers, _body = invoke_wsgi(
            app,
            "POST",
            "/verify",
            raw_body=b"confirmationToken=a&confirmationToken=b",
        )
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(calls, [])

    def test_question_is_html_escaped_and_only_token_is_submitted(self) -> None:
        question = (
            '<script>alert("x")</script> Rs 30,000; retained; no prior benefits.'
        )
        app = LocalVerifierApp(
            FakeProvider(READY_EXTRACTION),
            lambda _query: self.fail("verification needs a separate POST"),
            expected_authority="127.0.0.1:8765",
        )
        status, _headers, body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": question},
        )
        self.assertEqual(status, "200 OK")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertEqual(body.count('type="hidden"'), 1)
        self.assertIn('name="confirmationToken"', body)
        self.assertNotIn('name="productFmvPaise"', body)
        self.assertIn("3,000,000 paise", body)
        for assumption in FIXED_ASSUMPTIONS:
            self.assertIn(assumption.replace("'", "&#x27;"), body)

    def test_oversized_form_is_rejected_before_reading_or_formalizing(self) -> None:
        provider = FakeProvider(READY_EXTRACTION)
        app = LocalVerifierApp(
            provider,
            lambda _query: self.fail("verification must not run"),
            expected_authority="127.0.0.1:8765",
        )
        status, _headers, body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            raw_body=b"",
            content_length=MAX_FORM_BYTES + 1,
        )
        self.assertEqual(status, "413 Payload Too Large")
        self.assertIn("limited", body)
        self.assertEqual(provider.questions, [])

    def test_full_length_unicode_question_fits_the_transport_boundary(self) -> None:
        provider = FakeProvider(READY_EXTRACTION)
        app = LocalVerifierApp(
            provider,
            lambda _query: self.fail("verification needs confirmation"),
            expected_authority="127.0.0.1:8765",
        )
        question = "😀" * 2_000

        status, _headers, body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": question},
        )

        self.assertEqual(status, "200 OK")
        self.assertIn("READY", body)
        self.assertEqual(provider.questions, [question])

    def test_timeout_parser_rejects_non_finite_values(self) -> None:
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    _positive_timeout(value)
        self.assertEqual(_positive_timeout("0.25"), 0.25)

    def test_bad_host_and_cross_origin_fail_before_formalizer(self) -> None:
        provider = FakeProvider(READY_EXTRACTION)
        app = LocalVerifierApp(
            provider,
            lambda _query: self.fail("verification must not run"),
            expected_authority="127.0.0.1:8765",
        )
        bad_host, _headers, _body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": "Question"},
            authority="evil.example",
            origin="http://127.0.0.1:8765",
        )
        bad_origin, _headers, _body = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": "Question"},
            origin="https://evil.example",
        )
        self.assertEqual(bad_host, "403 Forbidden")
        self.assertEqual(bad_origin, "403 Forbidden")
        self.assertEqual(provider.questions, [])

    def test_verifier_exception_is_generic_and_consumes_confirmation(self) -> None:
        calls = []

        def failing_verifier(query):
            calls.append(copy.deepcopy(query))
            raise RuntimeError("secret verifier detail <script>alert(1)</script>")

        app = LocalVerifierApp(
            FakeProvider(READY_EXTRACTION),
            failing_verifier,
            expected_authority="127.0.0.1:8765",
        )
        _status, _headers, confirmation = invoke_wsgi(
            app,
            "POST",
            "/formalize",
            form={"question": "Rs 30,000, retained, no earlier benefits."},
        )
        token = confirmation_token(confirmation)

        error_log = io.StringIO()
        with redirect_stderr(error_log):
            status, _headers, body = invoke_wsgi(
                app,
                "POST",
                "/verify",
                form={"confirmationToken": token},
            )

        self.assertEqual(status, "500 Internal Server Error")
        self.assertIn("UNKNOWN", body)
        self.assertIn("INTERNAL_ERROR", body)
        self.assertIn("may have created partial local evidence", body)
        self.assertNotIn("secret verifier detail", body)
        self.assertNotIn("secret verifier detail", error_log.getvalue())
        self.assertEqual(
            error_log.getvalue().strip(),
            "Local UI internal error: RuntimeError",
        )
        self.assertEqual(len(calls), 1)

        replay_status, _headers, _body = invoke_wsgi(
            app,
            "POST",
            "/verify",
            form={"confirmationToken": token},
        )
        self.assertEqual(replay_status, "400 Bad Request")
        self.assertEqual(len(calls), 1)


class ConfirmedEndToEndWebTests(unittest.TestCase):
    def test_confirmed_proposal_runs_existing_lean_pipeline_once(self) -> None:
        provider = FakeProvider(READY_EXTRACTION)
        queries = []
        with tempfile.TemporaryDirectory() as directory:
            artifacts_root = Path(directory) / "artifacts"

            def verify_service(query):
                queries.append(copy.deepcopy(query))
                return verify_and_render_formal_query(
                    query,
                    artifacts_root=artifacts_root,
                    timeout=30,
                )

            app = LocalVerifierApp(
                provider,
                verify_service,
                expected_authority="127.0.0.1:8765",
            )
            question = (
                "A brand sent me a Rs 30,000 product in FY 2024-25. "
                "I kept it and had no earlier benefits from that brand."
            )
            status, _headers, confirmation = invoke_wsgi(
                app,
                "POST",
                "/formalize",
                form={"question": question},
            )
            token = confirmation_token(confirmation)

            tampered_status, _headers, _tampered_body = invoke_wsgi(
                app,
                "POST",
                "/verify",
                form={
                    "confirmationToken": token,
                    "productFmvPaise": "0",
                },
            )
            self.assertEqual(tampered_status, "400 Bad Request")
            self.assertEqual(queries, [])

            result_status, _headers, result = invoke_wsgi(
                app,
                "POST",
                "/verify",
                form={"confirmationToken": token},
            )

            self.assertEqual(status, "200 OK")
            self.assertEqual(result_status, "200 OK")
            self.assertEqual(
                queries,
                [
                    {
                        "schemaVersion": "formal-query-v0",
                        "modelVersion": "in-s194r-fy2024-25-v0",
                        "facts": {
                            "productFmvPaise": 3_000_000,
                            "productDisposition": "retained",
                            "priorBenefitsPaise": 0,
                        },
                    }
                ],
            )
            self.assertIn("PROVED", result)
            self.assertIn("IT-194R-RELEASEGATE", result)
            self.assertIn("https://www.incometaxindia.gov.in/w/section-194r-4", result)
            self.assertIn("Kernel check", result)
            self.assertIn("PASSED", result)
            self.assertTrue(any(artifacts_root.iterdir()))
            artifact_directory = next(artifacts_root.iterdir())
            self.assertIn("Replay directory", result)
            self.assertIn(str(artifact_directory / "model"), result)
            self.assertIn("Replay build command", result)
            self.assertIn("<code>lake build</code>", result)
            self.assertIn("Replay kernel command", result)

            replay_status, _headers, replay_body = invoke_wsgi(
                app,
                "POST",
                "/verify",
                form={"confirmationToken": token},
            )
            self.assertEqual(replay_status, "400 Bad Request")
            self.assertIn("already used", replay_body)
            self.assertEqual(len(queries), 1)

    def test_checked_unsupported_result_keeps_threshold_citation(self) -> None:
        extraction = copy.deepcopy(READY_EXTRACTION)
        extraction["priorBenefitsRupees"] = 20_001
        with tempfile.TemporaryDirectory() as directory:
            artifacts_root = Path(directory) / "artifacts"

            def verify_service(query):
                return verify_and_render_formal_query(
                    query,
                    artifacts_root=artifacts_root,
                    timeout=30,
                )

            app = LocalVerifierApp(
                FakeProvider(extraction),
                verify_service,
                expected_authority="127.0.0.1:8765",
            )
            _status, _headers, confirmation = invoke_wsgi(
                app,
                "POST",
                "/formalize",
                form={
                    "question": (
                        "Rs 30,000, retained, and Rs 20,001 of earlier "
                        "same-provider FY benefits."
                    )
                },
            )
            token = confirmation_token(confirmation)

            status, _headers, result = invoke_wsgi(
                app,
                "POST",
                "/verify",
                form={"confirmationToken": token},
            )

            self.assertEqual(status, "200 OK")
            self.assertIn("UNKNOWN", result)
            self.assertIn("UNSUPPORTED_INPUT", result)
            self.assertIn("IT-194R-THRESHOLD", result)
            for unrelated_rule in (
                "IT-194R-SCOPE",
                "IT-194R-RETAINED",
                "IT-194R-RELEASEGATE",
            ):
                self.assertNotIn(unrelated_rule, result)
            self.assertIn("Section 194R(1), second proviso", result)
            self.assertIn(
                "https://www.incometaxindia.gov.in/w/section-194r-4",
                result,
            )
            self.assertIn("Kernel check", result)
            self.assertIn("PASSED", result)


if __name__ == "__main__":
    unittest.main()
