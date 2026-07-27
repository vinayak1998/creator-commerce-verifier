"""One-screen local confirmation UI over the existing verifier pipeline."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import secrets
import sys
import time
from collections import OrderedDict
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from verifier.contracts import (
    FORMALIZATION_PROPOSAL,
    RENDERED_ANSWER,
    REPOSITORY_ROOT,
    VERIFICATION_RESULT,
    loads_json,
    validate,
)
from verifier.formalize import (
    FIXED_ASSUMPTIONS,
    QUESTION_MAX_CHARS,
    FormalizationProvider,
    FormalizerProviderError,
    OpenAIResponsesProvider,
    formalize_question,
)
from verifier.verify import VerificationRun, verify_and_render_formal_query


# One Unicode character can occupy four UTF-8 bytes, each percent-encoded as
# three ASCII bytes in a browser form. Keep the transport limit aligned with
# the formalizer's public character limit without accepting an unbounded body.
MAX_FORM_BYTES = len("question=") + (QUESTION_MAX_CHARS * 4 * 3)
PROPOSAL_TTL_SECONDS = 15 * 60
MAX_PENDING_PROPOSALS = 64

CSS = """
:root {
  color-scheme: light;
  --ink: #17221d;
  --muted: #5a6961;
  --line: #d9e2dc;
  --paper: #fbfcf9;
  --panel: #ffffff;
  --green: #17643b;
  --green-soft: #e8f5ec;
  --amber: #8a5200;
  --amber-soft: #fff4da;
  --red: #8e2f2f;
  --red-soft: #fbeaea;
  --shadow: 0 18px 55px rgba(23, 34, 29, 0.09);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(circle at 15% 0%, #e4f1e8 0, transparent 32rem),
    var(--paper);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}
a { color: var(--green); }
.shell { width: min(940px, calc(100% - 32px)); margin: 0 auto; }
header { padding: 42px 0 24px; }
.brand { font: 700 0.78rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0.13em; text-transform: uppercase; color: var(--green); }
h1 { margin: 8px 0 6px; font-family: Georgia, "Times New Roman", serif; font-size: clamp(2rem, 5vw, 3.35rem); line-height: 1.04; font-weight: 500; }
h2 { margin-top: 0; font-size: 1.35rem; }
h3 { margin: 28px 0 8px; font-size: 1rem; }
.lede, .muted { color: var(--muted); }
main { padding-bottom: 54px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: clamp(20px, 4vw, 34px); box-shadow: var(--shadow); margin-bottom: 18px; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 18px 0 26px; }
.step { border: 1px solid var(--line); border-radius: 12px; padding: 12px; color: var(--muted); font-size: 0.9rem; }
.step strong { display: block; color: var(--ink); }
label { display: block; font-weight: 700; margin-bottom: 8px; }
textarea { width: 100%; min-height: 145px; resize: vertical; border: 1px solid #adbbb3; border-radius: 12px; padding: 14px; font: inherit; color: var(--ink); background: #fff; }
textarea:focus { outline: 3px solid #bfe1ca; border-color: var(--green); }
button { margin-top: 14px; border: 0; border-radius: 999px; padding: 12px 20px; background: var(--green); color: white; font: 700 0.95rem/1.2 inherit; cursor: pointer; }
button:hover { background: #0f4f2d; }
.status { display: inline-block; border-radius: 999px; padding: 5px 10px; font: 700 0.78rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0.04em; }
.proved, .ready { color: var(--green); background: var(--green-soft); }
.unknown { color: var(--amber); background: var(--amber-soft); }
.warning { border-left: 4px solid var(--amber); background: var(--amber-soft); border-radius: 8px; padding: 12px 14px; }
.error { border-left-color: var(--red); background: var(--red-soft); color: var(--red); }
dl { display: grid; grid-template-columns: minmax(170px, 0.75fr) 1.5fr; margin: 0; }
dt, dd { padding: 10px 0; border-bottom: 1px solid var(--line); }
dt { color: var(--muted); }
dd { margin: 0; font-weight: 650; overflow-wrap: anywhere; }
pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f3f6f3; border: 1px solid var(--line); border-radius: 10px; padding: 14px; font-size: 0.82rem; }
ul { padding-left: 22px; }
.citation { border-top: 1px solid var(--line); padding: 16px 0 4px; }
.citation code { color: var(--green); font-weight: 700; }
.citation p { margin: 6px 0; }
.source { display: block; margin: 5px 0; overflow-wrap: anywhere; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 20px; }
.actions button { margin: 0; }
footer { border-top: 1px solid var(--line); padding: 22px 0 36px; color: var(--muted); font-size: 0.83rem; }
@media (max-width: 680px) { .steps { grid-template-columns: 1fr; } dl { grid-template-columns: 1fr; } dt { padding-bottom: 0; border-bottom: 0; } }
"""


class RequestProblem(ValueError):
    def __init__(self, status: str, title: str, message: str):
        self.status = status
        self.title = title
        self.message = message
        super().__init__(message)


class ProposalStore:
    """Bounded, one-time server-side storage for exact READY proposals."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PROPOSAL_TTL_SECONDS,
        max_entries: int = MAX_PENDING_PROPOSALS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("proposal store limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def _discard_expired(self, now: float) -> None:
        expired = [
            token
            for token, (issued_at, _proposal_json) in self._entries.items()
            if now - issued_at > self._ttl_seconds
        ]
        for token in expired:
            self._entries.pop(token, None)

    def issue(self, proposal: dict[str, Any]) -> str:
        validate(FORMALIZATION_PROPOSAL, proposal)
        if proposal["status"] != "READY":
            raise ValueError("only READY proposals can be confirmed")
        proposal_json = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
        now = self._clock()
        self._discard_expired(now)
        while len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)
        token = secrets.token_urlsafe(32)
        while token in self._entries:
            token = secrets.token_urlsafe(32)
        self._entries[token] = (now, proposal_json)
        return token

    def consume(self, token: str) -> dict[str, Any] | None:
        now = self._clock()
        self._discard_expired(now)
        entry = self._entries.pop(token, None)
        if entry is None:
            return None
        issued_at, proposal_json = entry
        if now - issued_at > self._ttl_seconds:
            return None
        proposal = loads_json(proposal_json)
        validate(FORMALIZATION_PROPOSAL, proposal)
        if proposal["status"] != "READY":
            return None
        return proposal


class _UnavailableProvider:
    def extract(self, _question: str) -> Any:
        raise FormalizerProviderError("formalizer configuration is unavailable")


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)} · Creator Commerce Verifier</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="shell">
    <div class="brand">Creator Commerce Verifier · frozen v0</div>
    <h1>Ask plainly. Confirm precisely. Check in Lean.</h1>
    <p class="lede">One historical FY 2024-25 Section 194R creator-product assessment—not a general tax chatbot.</p>
  </header>
  <main class="shell">{body}</main>
  <footer><div class="shell">Educational historical model only; not legal or tax advice. The encoding and source map need independent tax review.</div></footer>
</body>
</html>"""


def _landing_page(*, provider_configured: bool) -> str:
    configuration = ""
    if not provider_configured:
        configuration = """<p class="warning error"><strong>Formalizer unavailable.</strong> Set <code>OPENAI_API_KEY</code> and <code>OPENAI_MODEL</code>, then restart this local server. Questions will fail closed to UNKNOWN until then.</p>"""
    body = f"""
<section class="card">
  <div class="steps">
    <div class="step"><strong>1 · Formalize</strong>Untrusted model proposes only three facts.</div>
    <div class="step"><strong>2 · Confirm</strong>You inspect facts, paise, and fixed assumptions.</div>
    <div class="step"><strong>3 · Verify</strong>Lean checks; templates return cited rules.</div>
  </div>
  {configuration}
  <form method="post" action="/formalize">
    <label for="question">Your creator-product question</label>
    <textarea id="question" name="question" maxlength="2000" required placeholder="A brand sent me a Rs 30,000 product in FY 2024-25. I kept it and had no earlier Section 194R benefits from that brand. What does this frozen model assess?"></textarea>
    <p class="muted">Include the product FMV, retained/returned disposition, and earlier same-provider FY benefits. Missing or out-of-scope information becomes UNKNOWN.</p>
    <p class="warning"><strong>Data boundary:</strong> submitting sends the question text to the configured OpenAI Responses API. The API key remains server-side and is not written to verification artifacts.</p>
    <button type="submit">Propose three facts</button>
  </form>
</section>"""
    return _layout("Ask", body)


def _formalization_unknown_page(proposal: dict[str, Any]) -> str:
    validate(FORMALIZATION_PROPOSAL, proposal)
    unknown = proposal["unknown"]
    body = f"""
<section class="card">
  <span class="status unknown">UNKNOWN</span>
  <h2>The question was not promoted to a FormalQuery.</h2>
  <dl>
    <dt>Reason</dt><dd>{_escape(unknown['reason'])}</dd>
    <dt>Details</dt><dd>{_escape(unknown['details'])}</dd>
  </dl>
  <p class="warning">Lean verification has not run. No partial facts were retained.</p>
  <div class="actions"><a href="/">Revise the question</a></div>
</section>"""
    return _layout("Unknown formalization", body)


def _confirmation_page(
    question: str,
    proposal: dict[str, Any],
    token: str,
) -> str:
    validate(FORMALIZATION_PROPOSAL, proposal)
    facts = proposal["formalQuery"]["facts"]
    formal_query_json = json.dumps(proposal["formalQuery"], indent=2, sort_keys=True)
    assumptions = "".join(f"<li>{_escape(item)}</li>" for item in FIXED_ASSUMPTIONS)
    body = f"""
<section class="card">
  <span class="status ready">READY · UNTRUSTED PROPOSAL</span>
  <h2>Confirm the interpretation before Lean runs.</h2>
  <h3>Original question</h3>
  <p>{_escape(question)}</p>
  <h3>Exactly three variable facts</h3>
  <dl>
    <dt>Product FMV</dt><dd>Rs {facts['productFmvPaise'] // 100:,} · {facts['productFmvPaise']:,} paise</dd>
    <dt>Disposition</dt><dd>{_escape(facts['productDisposition'])}</dd>
    <dt>Earlier same-provider FY benefits</dt><dd>Rs {facts['priorBenefitsPaise'] // 100:,} · {facts['priorBenefitsPaise']:,} paise</dd>
  </dl>
  <h3>Fixed assumptions</h3>
  <ul>{assumptions}</ul>
  <h3>Exact FormalQuery sent after confirmation</h3>
  <pre>{_escape(formal_query_json)}</pre>
  <p class="warning"><strong>Lean has not run.</strong> Confirm only if the three facts and every fixed assumption match the intended frozen question.</p>
  <form method="post" action="/verify">
    <input type="hidden" name="confirmationToken" value="{_escape(token)}">
    <div class="actions">
      <button type="submit">Confirm facts and run Lean</button>
      <a href="/">Start over</a>
    </div>
  </form>
</section>"""
    return _layout("Confirm", body)


def _result_page(run: VerificationRun, rendered: dict[str, Any]) -> str:
    validate(VERIFICATION_RESULT, run.result)
    validate(RENDERED_ANSWER, rendered)
    status_class = "proved" if rendered["status"] == "PROVED" else "unknown"
    unknown_reason = ""
    if "unknownReason" in rendered:
        unknown_reason = f"<p><strong>Reason:</strong> {_escape(rendered['unknownReason'])}</p>"
    details = "".join(
        f"<dt>{_escape(item['label'])}</dt><dd>{_escape(item['value'])}</dd>"
        for item in rendered["details"]
    )
    detail_section = f"<h3>Checked result</h3><dl>{details}</dl>" if details else ""

    citation_items: list[str] = []
    for citation in rendered["citations"]:
        sources = "".join(
            (
                f'<a class="source" href="{_escape(source["officialUrl"])}" '
                f'target="_blank" rel="noreferrer noopener">'
                f'{_escape(source["title"])} · {_escape(source["sourceId"])}</a>'
            )
            for source in citation["sources"]
        )
        citation_items.append(
            "<div class=\"citation\">"
            f"<code>{_escape(citation['ruleId'])}</code> · "
            f"Lean <code>{_escape(citation['leanId'])}</code>"
            f"<p><strong>Location:</strong> {_escape(citation['location'])}</p>"
            f"<p>{_escape(citation['encodedInterpretation'])}</p>"
            f"{sources}</div>"
        )
    citations = "".join(citation_items)
    citation_section = (
        f"<h3>Checked rules and official sources</h3>{citations}" if citations else ""
    )

    source_status = ""
    if "sourceStatus" in rendered:
        status = rendered["sourceStatus"]
        source_status = (
            "<p class=\"muted\">Source status: "
            f"{_escape(status['period'])}; retrieved {_escape(status['retrievedOn'])}; "
            f"{_escape(status['reviewStatus'])}.</p>"
        )

    proof = run.result.get("proof")
    if proof is None:
        evidence = "<p>No Lean kernel proof was produced.</p>"
    else:
        replay_directory = run.artifact_directory / proof["replayCwd"]
        replay_build = " ".join(proof["replayBuildCommand"])
        replay = " ".join(proof["replayCommand"])
        evidence = f"""
<dl>
  <dt>Kernel check</dt><dd>{_escape(proof['kernelCheck'])}</dd>
  <dt>Theorem</dt><dd>{_escape(proof['theoremName'])}</dd>
  <dt>Artifact directory</dt><dd><code>{_escape(run.artifact_directory)}</code></dd>
  <dt>Replay directory</dt><dd><code>{_escape(replay_directory)}</code></dd>
  <dt>Replay build command</dt><dd><code>{_escape(replay_build)}</code></dd>
  <dt>Replay kernel command</dt><dd><code>{_escape(replay)}</code></dd>
</dl>"""

    body = f"""
<section class="card">
  <span class="status {status_class}">{_escape(rendered['status'])}</span>
  <h2>{_escape(rendered['summary'])}</h2>
  {unknown_reason}
  {detail_section}
  {citation_section}
  {source_status}
  <h3>Lean evidence</h3>
  {evidence}
  <p class="muted">{_escape(rendered['disclaimer'])}</p>
  <div class="actions"><a href="/">Verify another question</a></div>
</section>"""
    return _layout("Result", body)


def _problem_page(title: str, message: str, *, lean_may_have_run: bool = False) -> str:
    lean_status = (
        "The confirmed pipeline may have created partial local evidence."
        if lean_may_have_run
        else "Nothing was sent to Lean."
    )
    body = f"""
<section class="card">
  <span class="status unknown">UNKNOWN</span>
  <h2>{_escape(title)}</h2>
  <p>{_escape(message)}</p>
  <p class="warning">{_escape(lean_status)}</p>
  <div class="actions"><a href="/">Start over</a></div>
</section>"""
    return _layout(title, body)


def _read_form(environ: dict[str, Any]) -> dict[str, str]:
    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise RequestProblem(
            "415 Unsupported Media Type",
            "Unsupported form submission",
            "Use the local HTML form to submit this request.",
        )
    try:
        content_length = int(environ.get("CONTENT_LENGTH", ""))
    except (TypeError, ValueError) as error:
        raise RequestProblem(
            "411 Length Required",
            "Missing request length",
            "The local form request had no valid Content-Length.",
        ) from error
    if content_length < 0 or content_length > MAX_FORM_BYTES:
        raise RequestProblem(
            "413 Payload Too Large",
            "Form request too large",
            f"Local form requests are limited to {MAX_FORM_BYTES} bytes.",
        )
    body = environ["wsgi.input"].read(content_length)
    if len(body) != content_length:
        raise RequestProblem(
            "400 Bad Request",
            "Incomplete form request",
            "The local server did not receive the complete form body.",
        )
    try:
        decoded = body.decode("utf-8")
        parsed = parse_qs(
            decoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
    except (UnicodeError, ValueError) as error:
        raise RequestProblem(
            "400 Bad Request",
            "Malformed form request",
            "The local form body could not be decoded exactly.",
        ) from error
    if any(len(values) != 1 for values in parsed.values()):
        raise RequestProblem(
            "400 Bad Request",
            "Ambiguous form request",
            "Each local form field must appear exactly once.",
        )
    return {key: values[0] for key, values in parsed.items()}


class LocalVerifierApp:
    """WSGI application with a hard proposal-confirmation-verification split."""

    def __init__(
        self,
        provider: FormalizationProvider,
        verify_service: Callable[
            [dict[str, Any]], tuple[VerificationRun, dict[str, Any]]
        ],
        *,
        expected_authority: str,
        store: ProposalStore | None = None,
        provider_configured: bool = True,
    ) -> None:
        if not expected_authority:
            raise ValueError("expected loopback authority must be non-empty")
        self._provider = provider
        self._verify_service = verify_service
        self._expected_authority = expected_authority
        self._store = store or ProposalStore()
        self._provider_configured = provider_configured

    def _dispatch(self, environ: dict[str, Any]) -> tuple[str, str, str]:
        method = environ.get("REQUEST_METHOD", "")
        path = environ.get("PATH_INFO", "")
        if environ.get("HTTP_HOST") != self._expected_authority:
            raise RequestProblem(
                "403 Forbidden",
                "Invalid local host",
                "Use the exact loopback URL printed by this server.",
            )
        if method == "POST" and environ.get("HTTP_ORIGIN") != (
            f"http://{self._expected_authority}"
        ):
            raise RequestProblem(
                "403 Forbidden",
                "Cross-origin request rejected",
                "Submit only from the page served by this local verifier.",
            )
        if method == "GET" and path == "/":
            return "200 OK", "text/html; charset=utf-8", _landing_page(
                provider_configured=self._provider_configured
            )
        if method == "GET" and path == "/style.css":
            return "200 OK", "text/css; charset=utf-8", CSS
        if method == "POST" and path == "/formalize":
            form = _read_form(environ)
            if set(form) != {"question"}:
                raise RequestProblem(
                    "400 Bad Request",
                    "Unexpected form fields",
                    "The formalization form accepts only one question.",
                )
            proposal = formalize_question(form["question"], self._provider)
            if proposal["status"] == "UNKNOWN":
                return (
                    "200 OK",
                    "text/html; charset=utf-8",
                    _formalization_unknown_page(proposal),
                )
            token = self._store.issue(proposal)
            return (
                "200 OK",
                "text/html; charset=utf-8",
                _confirmation_page(form["question"].strip(), proposal, token),
            )
        if method == "POST" and path == "/verify":
            form = _read_form(environ)
            if set(form) != {"confirmationToken"}:
                raise RequestProblem(
                    "400 Bad Request",
                    "Confirmation required",
                    "Verification accepts only a server-issued confirmation token.",
                )
            proposal = self._store.consume(form["confirmationToken"])
            if proposal is None:
                return (
                    "400 Bad Request",
                    "text/html; charset=utf-8",
                    _problem_page(
                        "Confirmation required",
                        "The proposal is missing, expired, or already used.",
                    ),
                )
            run, rendered = self._verify_service(proposal["formalQuery"])
            return "200 OK", "text/html; charset=utf-8", _result_page(run, rendered)
        if method not in {"GET", "POST"}:
            raise RequestProblem(
                "405 Method Not Allowed",
                "Method not allowed",
                "This local demo supports only GET and POST.",
            )
        raise RequestProblem(
            "404 Not Found",
            "Page not found",
            "This route is not part of the local verifier demo.",
        )

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], Any],
    ) -> Iterable[bytes]:
        try:
            status, content_type, body = self._dispatch(environ)
        except RequestProblem as error:
            status = error.status
            content_type = "text/html; charset=utf-8"
            body = _problem_page(error.title, error.message)
        except Exception as error:
            print(f"Local UI internal error: {type(error).__name__}", file=sys.stderr)
            status = "500 Internal Server Error"
            content_type = "text/html; charset=utf-8"
            body = _problem_page(
                "Verification could not complete",
                "Reason: INTERNAL_ERROR. The local pipeline failed safely.",
                lean_may_have_run=(
                    environ.get("REQUEST_METHOD") == "POST"
                    and environ.get("PATH_INFO") == "/verify"
                ),
            )

        encoded = body.encode("utf-8")
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                "default-src 'none'; style-src 'self'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'",
            ),
        ]
        if status.startswith("405"):
            headers.append(("Allow", "GET, POST"))
        start_response(status, headers)
        return [encoded]


def _positive_timeout(value: str) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return timeout


def _port(value: str) -> int:
    port = int(value)
    if port < 1 or port > 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve the confirmation-gated verifier on local loopback only."
    )
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--lean-timeout", type=_positive_timeout, default=30.0)
    parser.add_argument("--formalizer-timeout", type=_positive_timeout, default=30.0)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    arguments = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    provider_configured = bool(api_key and arguments.model)
    if provider_configured:
        try:
            provider: FormalizationProvider = OpenAIResponsesProvider(
                api_key=api_key,
                model=arguments.model,
                timeout=arguments.formalizer_timeout,
            )
        except ValueError:
            provider = _UnavailableProvider()
            provider_configured = False
    else:
        provider = _UnavailableProvider()

    def verify_service(
        formal_query: dict[str, Any],
    ) -> tuple[VerificationRun, dict[str, Any]]:
        return verify_and_render_formal_query(
            formal_query,
            artifacts_root=REPOSITORY_ROOT / ".artifacts",
            timeout=arguments.lean_timeout,
        )

    application = LocalVerifierApp(
        provider,
        verify_service,
        expected_authority=f"127.0.0.1:{arguments.port}",
        provider_configured=provider_configured,
    )
    try:
        server = make_server("127.0.0.1", arguments.port, application)
    except OSError as error:
        parser.exit(1, f"Could not start local server: {error}\n")
    host, port = server.server_address[:2]
    print(f"Creator Commerce Verifier: http://{host}:{port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
