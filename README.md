# Creator Commerce Verifier

A tiny Lean-first learning POC: turn one kind of creator-commerce tax question
into explicit facts, check the answer against a symbolic world model, and cite
the exact encoded rules.

This repository exists to support a short article about the architecture
publicly described around Pramaana Labs. It is an independent teaching demo,
not a reproduction of Pramaana, not a Wishlink project, and not legal or tax
advice.

## What exists today

The deterministic, no-LLM path is complete:

- one well-commented Lean world model;
- exactly three variable facts;
- four stable rule IDs linked to official source locations;
- five kernel-checked examples, including an explicit unsupported result;
- strict versioned contracts for the untrusted proposal, accepted three-fact
  query, verification result, and rendered answer;
- a two-pass generated Lean equality, portable certificate, and deterministic
  cited-answer renderer; and
- no duplicate Python/JavaScript tax engine.

The constrained natural-language formalizer and one loopback-only confirmation
screen are now present. The model may only propose the visible three-fact query
or return `UNKNOWN`; a `READY` proposal reaches Lean only after the user
confirms the exact displayed `FormalQuery`. The next milestone is the final
article-facing hardening and documentation pass, not another domain feature.

## The question v0 models

A company brand gives a wholly in-kind product to a resident creator during
FY 2024-25. Given its value, whether it was retained or returned, and earlier
benefits from the same provider, the model returns:

- whether the current product qualifies as a benefit;
- the financial-year aggregate;
- the modeled TDS due; and
- whether tax payment/evidence is required before release.

All other facts are fixed assumptions. See [the frozen scope](docs/SCOPE.md)
before reading the code.

## Run the proof checks

Install Lean through `elan`, then run:

```bash
lake build
```

That command compiles the model and asks Lean's kernel to check the concrete
examples in [`CreatorCommerce/Section194R.lean`](CreatorCommerce/Section194R.lean).

Validate the canonical JSON query with:

```bash
python -m pip install -r requirements.txt
python -m verifier.contracts formal-query-v0 examples/retained-30000.json
```

The four JSON boundaries and their fail-closed status semantics are documented
in [`contracts/README.md`](contracts/README.md).

Run the canonical query through both Lean passes and preserve its replayable
certificate with:

```bash
python -m verifier.verify examples/retained-30000.json
```

The command prints the deterministic cited answer as JSON and writes the
accepted query, an exact replayable snapshot of the tiny Lean project, both
generated Lean files, both Lean outputs, `certificate.json`, `answer.json`, and
`answer.txt` beneath the gitignored `.artifacts/` directory. It exits `0` only
for `PROVED`; every `UNKNOWN`, including a checked unsupported result, exits
nonzero.

Use `--format answer-text` for plain text or `--format verification-json` for
the effective verification envelope on standard output. The immutable raw
Lean certificate remains in `certificate.json`; if citation mapping fails, the
effective envelope reports `UNKNOWN/SOURCE_MAPPING_FAILED` instead of hiding
that failure behind the earlier kernel result.

Propose—but do not yet verify—the three facts from natural language with:

```bash
export OPENAI_API_KEY=your_key
export OPENAI_MODEL=your_explicit_responses_model
python -m verifier.formalize \
  "A brand sent me a Rs 30,000 product in FY 2024-25. I kept it and had no earlier benefits from that brand." \
  --show-confirmation
```

The key is read only from the environment and the model must be selected
explicitly; neither is stored in an artifact. The provider call uses the
Responses API with strict Structured Outputs, following the
[official OpenAI guide](https://developers.openai.com/api/docs/guides/structured-outputs).
Its output remains untrusted: deterministic code validates it, converts whole
rupees to paise, displays all three facts and fixed assumptions, and says that
Lean has not run. Missing configuration, refusal, malformed output, or an
uncertain/out-of-scope question is `UNKNOWN`.

## Run the local UI

With the same `OPENAI_API_KEY` and explicit `OPENAI_MODEL` environment
variables set, run:

```bash
python -m verifier.web
```

The command binds only to the printed `http://127.0.0.1:8765` loopback URL.
The first screen sends the natural-language question to the untrusted
formalizer. A `READY` response displays the original question, exactly three
facts in rupees and paise, every fixed assumption, and the exact `FormalQuery`.
Lean has not run at that point. A separate confirmation submits only a
short-lived opaque token; the server retrieves the unchanged proposal, runs the
existing two-pass Lean boundary, and returns the deterministic cited answer and
evidence location. Confirmed evidence is written beneath `.artifacts/`.

Without both provider settings, the page remains usable as an explanation of
the flow but formalization fails closed to `UNKNOWN`. This is a local teaching
server, not a production deployment.

## The implemented end-to-end demo

```text
question in natural language
  -> visible, confirmable three-fact interpretation
  -> deterministic Lean equality
  -> kernel-checked decision or explicit UNKNOWN
  -> constrained English answer with exact rule citations
```

The language model is allowed to propose the typed interpretation. It is not
the legal oracle, calculator, proof checker, or citation generator. Read the
[architecture note](docs/ARCHITECTURE.md) for the component-by-component trust
boundary and implementation order.

## Why this repository is separate

The predecessor experiment, `collabproof`, explored too many concerns at once:
multiple tax branches, multiple reasoning engines, evaluation, source
governance, a browser demo, and assurance machinery. Those were useful
experiments, but they obscured the central learning loop.

This repo restarts in the opposite order: one model, one question family, one
checked result, then one interface layer at a time.

## Legal and source status

The model is a simplified historical FY 2024-25 interpretation of Section
194R and CBDT Circular 12/2022. The source map has not received independent tax
review. Lean proves consequences of the encoding and supplied facts; it does
not prove that the encoding is correct law or that the facts are true.

See [`sources.yaml`](sources.yaml) for official URLs, exact locations, fixed
assumptions, and review status.
