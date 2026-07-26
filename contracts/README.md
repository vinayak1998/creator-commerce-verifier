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

## `VerificationResult v0`

An answered, kernel-checked decision is user-facing `PROVED`. A checked Lean
`unsupported` decision is still user-facing `UNKNOWN`, even though its concrete
equality theorem passed the kernel. Evaluation errors, proof-check failures,
and source-map failures also remain `UNKNOWN`.

There is intentionally no `REFUTED` result in this assessment-only protocol.
Adding a user-supplied claim to prove or refute would be a second intent and a
scope change.
