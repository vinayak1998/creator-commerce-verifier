# End-to-end walkthrough

This walkthrough follows the complete teaching path in about 45–60 minutes
once Python and Lean are installed:

```text
question or exact JSON facts
  -> visible three-fact FormalQuery
  -> Lean candidate Decision
  -> kernel-checked concrete equality
  -> deterministic answer and exact rule citations
```

It is intentionally limited to the historical FY 2024-25 Section 194R
creator-product question in the [frozen scope](SCOPE.md). There are exactly
three variable facts and four public rule IDs. `PROVED` means that Lean checked
the result against this encoding. It does not establish that the encoding is
correct law, current law, independently reviewed, or based on true facts.

## 1. Build the frozen model

Run these commands from the repository root. The estimate above excludes a
first-time Python or Lean toolchain installation.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
lake build
python -m unittest discover -v
```

No API key or network call is needed for the deterministic examples or their
proof replays. Before continuing, skim the three central inputs:

- [`CreatorCommerce/Section194R.lean`](../CreatorCommerce/Section194R.lean) is
  the sole domain oracle;
- [`examples/retained-30000.json`](../examples/retained-30000.json) contains
  only the version and three variable facts; and
- [`sources.yaml`](../sources.yaml) maps the four checked Lean rule values to
  their frozen public IDs and official locations.

## 2. Run a checked `PROVED` case

Keep the demonstration artifacts outside the repository so the commands below
have stable names and can be replayed directly:

```bash
DEMO_REPO_ROOT="$PWD"
DEMO_RUN_ROOT="$(mktemp -d)"
echo "$DEMO_RUN_ROOT"
```

Validate the public query, then run both Lean passes and the deterministic
renderer:

```bash
python -m verifier.contracts \
  formal-query-v0 examples/retained-30000.json
python -m verifier.verify examples/retained-30000.json \
  --artifacts-root "$DEMO_RUN_ROOT" \
  --case-name proved \
  --format answer-text
```

The verifier exits `0`. Its checked answer says that the retained Rs 30,000
product qualifies, aggregate benefits are ₹30,000.00, modeled TDS is ₹3,000.00,
and the release gate is required. The checked rule order is:

1. `IT-194R-SCOPE`
2. `IT-194R-RETAINED`
3. `IT-194R-THRESHOLD`
4. `IT-194R-RELEASEGATE`

Those values are not recalculated in Python. The first generated Lean program
asks `assess` for a candidate `Decision`. The second embeds that concrete
candidate in this shape and asks Lean's kernel to check it:

```lean
theorem checkedCase :
    assess caseFacts = expectedDecision := by
  decide
```

Inspect both generated programs and the resulting certificate:

```bash
sed -n '1,220p' "$DEMO_RUN_ROOT/proved/model/Evaluate.lean"
sed -n '1,260p' "$DEMO_RUN_ROOT/proved/model/GeneratedCase.lean"
python -m json.tool "$DEMO_RUN_ROOT/proved/certificate.json"
```

## 3. Understand and replay the evidence

Each accepted run preserves a small, self-contained evidence directory:

| Artifact | Role |
|---|---|
| `formal-query.json` | The exact validated three-fact input |
| `model/Evaluate.lean` and `candidate.json` | Lean's first-pass candidate |
| `model/GeneratedCase.lean` and `checked.json` | The concrete equality and second-pass output |
| `certificate.json` | Checked decision, theorem source, model snapshot list, and replay recipe |
| `answer.json` and `answer.txt` | Deterministic presentation of checked fields |
| `sources.yaml` | The exact citation map used by the renderer |
| `model/` | The tiny Lean project snapshot used for both passes |

Replay the preserved equality from the working directory recorded in
`certificate.json`:

```bash
cd "$DEMO_RUN_ROOT/proved/model"
lake build
lake env lean --trust=0 --memory=512 --run GeneratedCase.lean
cd "$DEMO_REPO_ROOT"
```

The replay exits `0` and emits the checked decision. This is replayable kernel
evidence against the preserved model snapshot; it is not a cryptographic or
tamper-proof assurance claim.

## 4. Contrast a Lean-checked `UNKNOWN`

The second public query supplies Rs 20,001 of earlier same-provider benefits.
It is a valid three-fact input, but v0 has no prior-TDS fact and refuses to risk
double counting:

```bash
python -m verifier.contracts \
  formal-query-v0 examples/unsupported-priors-20001.json
python -m verifier.verify examples/unsupported-priors-20001.json \
  --artifacts-root "$DEMO_RUN_ROOT" \
  --case-name checked-unknown \
  --format answer-text
```

The second command intentionally exits `1` because its user-facing status is
`UNKNOWN/UNSUPPORTED_INPUT`. This is the expected fail-closed application
result, not a Lean evaluation or kernel-check failure, and it does not mean
"no tax." Open the certificate:

```bash
python -m json.tool "$DEMO_RUN_ROOT/checked-unknown/certificate.json"
```

It still records `proof.kernelCheck = PASSED`, `decision.kind = unsupported`,
the Lean reason `priorBenefitsNeedPriorTds`, and the checked rule
`annualThreshold`. The renderer therefore cites only
`IT-194R-THRESHOLD`. Lean proved that the frozen model returns `unsupported`;
it did not prove a tax conclusion or refute a user claim.

Replay that checked unsupported equality too:

```bash
cd "$DEMO_RUN_ROOT/checked-unknown/model"
lake build
lake env lean --trust=0 --memory=512 --run GeneratedCase.lean
cd "$DEMO_REPO_ROOT"
```

The replay exits `0` even though the verifier intentionally returned exit `1`.
That contrast is the core fail-closed lesson: a proof can establish that the
model declines to answer.

## 5. Try the confirmation-gated natural-language path

This step is optional. It requires an OpenAI API key and an explicitly selected
Responses API model; the deterministic cases above do not.

```bash
export OPENAI_API_KEY="your_key"
export OPENAI_MODEL="your_explicit_responses_model"
python -m verifier.web
```

Open the exact `http://127.0.0.1:8765` URL printed by the server and enter:

> A company brand sent me a product with an FMV of Rs 30,000 in FY 2024-25. I
> retained it and had no earlier Section 194R benefits from that brand. Under
> the frozen assumptions, what does the model assess?

Although the UI server binds only to loopback, submitting the question sends
that text to the configured OpenAI Responses API. Do not enter sensitive data.
The API key remains server-side and is not written to verification artifacts.

Before confirmation, inspect all of the following on screen:

- the original question;
- exactly three facts in rupees and paise;
- every fixed assumption;
- the exact versioned `FormalQuery`; and
- the explicit statement that Lean has not run.

The confirmation request contains only a short-lived opaque token. The server,
not the browser, retains the validated proposal and sends it unchanged to the
existing verifier. The result page shows the checked answer, exact citations,
artifact directory, replay directory, build command, and kernel command.

The prompt requires a missing earlier-benefits fact to remain pre-Lean
`UNKNOWN`, but the provider's exact reason and output remain untrusted. A Rs
20,001 prior-benefit question reaches the checked `UNKNOWN` path only if the
provider proposes the exact `READY` `FormalQuery` and the user confirms it. Use
Section 4 for the deterministic reproduction.

## 6. Keep the trust boundary visible

| Stage | What it may establish | What it cannot establish |
|---|---|---|
| Natural-language model | An untrusted proposal of exactly three facts, or `UNKNOWN` | Tax result, proof, or citations |
| User confirmation | That the displayed interpretation is the one to check | That the real-world facts or legal encoding are true |
| Deterministic adapter | Exact contract validation and Lean syntax generation | A second implementation of `assess` |
| Lean | The `Decision` follows from the supplied facts and frozen encoding | Correct/current law or factual truth |
| Renderer and source map | Fixed prose and exact mappings for checked `RuleId` values | Independent tax review or a guessed citation |

The source map remains marked `needs_independent_tax_review`. A missing or
changed mapping fails closed to `UNKNOWN/SOURCE_MAPPING_FAILED`. There is no
`REFUTED` status in v0 because the only intent is an assessment, not a
user-supplied claim to prove or refute.

Stop here for v0. GST, Sections 194C/194J/206AA, gross-up, carve-outs, current
law, general NLP, source ingestion, evaluation, another solver, and production
deployment are separate projects or explicit later versions—not extensions to
this walkthrough.
