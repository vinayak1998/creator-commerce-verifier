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

The next milestone is the constrained natural-language formalizer. It may only
propose the visible three-fact query or return `UNKNOWN`; the checked and
rendered path already works without it.

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

## The intended end-to-end demo

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
