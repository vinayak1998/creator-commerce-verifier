# Versioned boundary contracts

These schemas describe the only data allowed to cross the untrusted-language,
deterministic-verification, and presentation boundaries in v0.

## `FormalQuery v0`

An accepted query contains version metadata plus exactly three domain facts:

1. `productFmvPaise`;
2. `productDisposition`; and
3. `priorBenefitsPaise`.

Money inputs are non-negative whole-rupee amounts represented as paise. Prior
benefits above Rs 20,000 remain structurally valid: Lean, rather than the
contract validator, returns the modeled unsupported decision.

## `FormalizationProposal v0`

The natural-language adapter is untrusted. It can propose either:

- `READY`, with a complete `FormalQuery`; or
- `UNKNOWN`, with a bounded reason and explanation.

Only an explicitly confirmed `READY` proposal may enter verification.
The provider receives a smaller strict-output extraction schema; deterministic
code converts whole rupees to paise and then validates the canonical public
proposal contract. Provider, transport, refusal, or output-shape failures use
`UNKNOWN/FORMALIZER_FAILED` and never reach Lean.

## `VerificationResult v0`

An answered, kernel-checked decision is user-facing `PROVED`. A checked Lean
`unsupported` decision is still user-facing `UNKNOWN`, even though its concrete
equality theorem passed the kernel. Evaluation errors, proof-check failures,
and source-map failures also remain `UNKNOWN`.

Each passed proof preserves the exact small Lean project snapshot it checked,
then records the snapshot-relative working directory, model-build command, and
memory-bounded concrete-case command needed to replay it. No external hash or
assurance layer is required for this teaching artifact.

There is intentionally no `REFUTED` result in this assessment-only protocol.
Adding a user-supplied claim to prove or refute would be a second intent and a
scope change.

## `RenderedAnswer v0`

The presentation boundary contains only fixed prose over checked decision
fields and citations resolved from checked Lean `RuleId` values. It preserves
Lean's rule order and the exact public IDs, locations, and official URLs in
the canonical, non-user-selectable `sources.yaml`. A missing, changed,
duplicate, or inconsistent source relationship makes the presented answer
`UNKNOWN/SOURCE_MAPPING_FAILED`; it never guesses a citation. Operational
failure, checked unsupported input, and source-mapping failure are separate
schema branches, so contradictory UNKNOWN states are invalid.
