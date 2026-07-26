# Architecture

This is an independent teaching reconstruction of one public architectural
idea associated with Pramaana Labs. It does not reproduce Pramaana's private
implementation and is not affiliated with Pramaana Labs or Wishlink.

The public description separates an offline domain-formalization path from an
online verifier. This POC keeps that shape while reducing the domain to one
understandable Lean file.

```mermaid
flowchart LR
  subgraph Offline["Offline: compile the domain once"]
    S["Official rule locations"] --> W["Versioned Lean world model"]
  end
  subgraph Online["Online: verify one question"]
    Q["Natural-language question"] --> F["Visible typed facts"]
    F --> G["Deterministic Lean goal"]
    G --> K["Lean kernel"]
    K --> A["Checked decision + RuleIds"]
    A --> R["Template-rendered answer + citations"]
  end
  W --> G
```

## Trust boundary

The natural-language formalizer is untrusted. Before proof, its interpretation
must be visible as exactly three facts and the fixed assumptions. Missing or
uncertain information yields `UNKNOWN`.

The compiler is deterministic. It may emit a concrete Lean equality to check,
but it must never contain a second implementation of the tax calculation.
Lean's kernel is the checker. A failed proof is not automatically a refutation:
`REFUTED` requires a checked proof of an explicit opposite; otherwise the
status is `UNKNOWN`.

The renderer consumes only a checked structured decision. It maps the
decision's checked `RuleId` values to `sources.yaml`. A later LLM may polish
wording, but it may not add facts, numbers, conclusions, or citations.

## Mapping to the public architecture

| Public architecture box | This repository |
|---|---|
| Domain knowledge | Two official source entries and four reviewed-as-experimental rule mappings |
| Domain formalizer | Human-authored, commented `Section194R.lean` in v0 |
| Symbolic world model | `CreatorCommerce/Section194R.lean` |
| Auto-formalizer | Deferred: NL to a visible three-fact record |
| Facts + theorems | Concrete facts plus an equality against `assess` |
| Solver/prover | Lean reduction and kernel checking; no separate Z3 layer |
| Answer + proof | Checked `Decision`, including `RuleId` values |
| De-formalizer | Deferred: deterministic templates over the checked decision |

## Build order

1. **Done:** compile the tiny world model and handwritten example theorems.
2. **Done:** define one versioned JSON `FormalQuery` containing only the three
   facts, plus the adjacent untrusted-proposal and checked-result envelopes.
3. **Next:** deterministically generate a replayable Lean case theorem.
4. Run Lean and preserve the checked result/artifact.
5. Render a plain-English answer from the result and exact rule map.
6. Only then add a constrained NL-to-`FormalQuery` adapter.

A proposed feature belongs in v0 only if it makes one of these boxes visible
or executes the one supported question family. Everything else stays deferred.

## Reference

The architectural framing comes from Unbound's public write-up,
[Pramaana Labs - The Antithesis to AI Rollups](https://unbound-advisors.com/pramaana-labs-investment-thesis.html#summary),
especially its offline/online diagram and its statement that verification
operates inside a closed formal domain rather than guessing.
