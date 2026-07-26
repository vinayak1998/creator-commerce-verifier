# Frozen v0 scope

The v0 model is intentionally one small historical slice: Section 194R for a
product sent by a company brand to a creator during FY 2024-25. It is designed
to make the architecture understandable, not to answer creator-tax questions
generally.

## The three variable facts

1. Product fair-market value, in paise.
2. Product disposition: genuinely `retained` or genuinely `returned`.
3. Earlier Section 194R benefits from the same provider in the financial year.

## Fixed assumptions

Every answer assumes all of the following:

- the provider is a company acting in business or profession;
- the creator is resident and has furnished PAN;
- the product is wholly in kind;
- the creator bears the tax and the provider does not gross it up;
- no TDS has previously been deducted;
- prior benefits do not exceed Rs 20,000;
- the entered FMV, disposition, and prior-benefit total are true; and
- the online v0 accepts whole-rupee inputs, represented internally as paise.

If prior benefits already exceed Rs 20,000, v0 returns `UNKNOWN`/`unsupported`
because it does not know prior TDS. It does not silently double-count tax.

## The one supported question family

Given those facts and assumptions, assess:

- whether the current product qualifies as a benefit;
- the resulting financial-year aggregate;
- whether the aggregate strictly exceeds Rs 20,000;
- the Section 194R TDS due under this model; and
- whether the in-kind release gate is required.

Different natural-language phrasings may map to this one typed intent. Adding
another intent is a scope change, not a prompt tweak.

## Explicitly outside v0

- GST and barter-supply analysis;
- cash-fee classification under Sections 194C or 194J;
- PAN-missing rates under Section 206AA;
- individual/HUF provider carve-outs;
- provider-borne tax and gross-up;
- non-residents, mixed cash/in-kind benefits, or prior TDS;
- source ingestion, general legal NLP, RAG, Z3, Bayesian reasoning, or a UI;
- current-law conclusions or production/legal reliance.

Unsupported, missing, contradictory, or uncertain inputs must become
`UNKNOWN`; they must never be filled in by an LLM.

## What the proof does and does not establish

Lean can prove that a concrete decision follows from this encoded model and
the supplied facts. It cannot prove that the source interpretation is legally
correct, that the source remains current, or that a user's facts are true.
Every rule in `sources.yaml` is therefore marked as needing independent tax
review.
