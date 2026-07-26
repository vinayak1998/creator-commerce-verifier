namespace CreatorCommerce.Section194R

/-!
# A deliberately tiny symbolic world model

This file encodes one educational interpretation of Indian Income-tax Act
section 194R for a creator who receives a product from a company brand.

Only three facts vary:

* the product's fair-market value;
* whether the creator retained or returned it; and
* earlier section 194R benefits from the same provider in the financial year.

Everything else is a visible fixed assumption documented in `docs/SCOPE.md`.
Lean proves what follows from this encoding and the supplied facts. It does
not prove that the encoding is legally correct or that the facts are true.
-/

/-- Money is represented as a natural number of paise: no floating point. -/
abbrev MoneyPaise := Nat

inductive ProductDisposition where
  | retained
  | returned
  deriving DecidableEq, Repr

/-!
Stable identifiers are part of the checked answer. A deterministic renderer
maps them to the exact locations in `sources.yaml`; an LLM must not invent or
silently change citations.
-/
inductive RuleId where
  | s194rScope
  | influencerProduct
  | annualThreshold
  | inKindReleaseGate
  deriving DecidableEq, Repr

inductive UnsupportedReason where
  /-- Prior benefits above the threshold may already have attracted TDS, but
      v0 deliberately has no `priorTdsPaise` fact. Guessing would double count. -/
  | priorBenefitsNeedPriorTds
  deriving DecidableEq, Repr

/-- The complete variable input to the v0 world model. -/
structure Facts where
  priorBenefitsPaise : MoneyPaise
  productFmvPaise : MoneyPaise
  productDisposition : ProductDisposition
  deriving DecidableEq, Repr

/-- The complete structured answer. The cited rules are proof-checked data. -/
structure Answer where
  benefitQualifies : Bool
  currentBenefitPaise : MoneyPaise
  aggregateBenefitsPaise : MoneyPaise
  thresholdExceeded : Bool
  tdsDuePaise : MoneyPaise
  releaseGateRequired : Bool
  appliedRules : List RuleId
  deriving DecidableEq, Repr

/-- `unsupported` is the model's fail-closed/UNKNOWN path. -/
inductive Decision where
  | answered (answer : Answer)
  | unsupported (reason : UnsupportedReason) (citedRules : List RuleId)
  deriving DecidableEq, Repr

/-- Rs 20,000, expressed in paise. The statutory comparison is strict: the
    encoded obligation starts only when the aggregate exceeds this amount. -/
def thresholdPaise : MoneyPaise := 2_000_000

/-- Ten percent, rounded half-up to the nearest paise.

This is a transparent POC convention, not an encoding of statutory whole-
rupee rounding. The online v0 will accept whole-rupee inputs only.
-/
def tenPercentRoundedToPaise (amount : MoneyPaise) : MoneyPaise :=
  (amount + 5) / 10

/-- Evaluate the three facts against the frozen world model.

No Python or JavaScript implementation is allowed to recalculate this answer.
Future online code may only translate inputs, ask Lean to check a concrete
equality, and render the checked result.
-/
def assess (facts : Facts) : Decision :=
  if facts.priorBenefitsPaise > thresholdPaise then
    .unsupported .priorBenefitsNeedPriorTds [.annualThreshold]
  else
    let retained := facts.productDisposition == .retained
    let benefitQualifies := retained && facts.productFmvPaise > 0
    let currentBenefit := if benefitQualifies then facts.productFmvPaise else 0
    let aggregate := facts.priorBenefitsPaise + currentBenefit
    let thresholdExceeded := aggregate > thresholdPaise
    let tdsDue :=
      if benefitQualifies && thresholdExceeded then
        tenPercentRoundedToPaise aggregate
      else
        0
    let releaseGateRequired := tdsDue > 0 && currentBenefit > 0
    let rules :=
      if !benefitQualifies then
        [.s194rScope, .influencerProduct]
      else if thresholdExceeded then
        [.s194rScope, .influencerProduct, .annualThreshold, .inKindReleaseGate]
      else
        [.s194rScope, .influencerProduct, .annualThreshold]
    .answered
      { benefitQualifies := benefitQualifies
        currentBenefitPaise := currentBenefit
        aggregateBenefitsPaise := aggregate
        thresholdExceeded := thresholdExceeded
        tdsDuePaise := tdsDue
        releaseGateRequired := releaseGateRequired
        appliedRules := rules }

/-!
These are executable examples and machine-checked theorems, not screenshots of
calculator output. `lake build` asks Lean's kernel to check each equality.
-/

theorem retainedThirtyThousand :
    assess
      { priorBenefitsPaise := 0
        productFmvPaise := 3_000_000
        productDisposition := .retained } =
      .answered
        { benefitQualifies := true
          currentBenefitPaise := 3_000_000
          aggregateBenefitsPaise := 3_000_000
          thresholdExceeded := true
          tdsDuePaise := 300_000
          releaseGateRequired := true
          appliedRules :=
            [.s194rScope, .influencerProduct, .annualThreshold, .inKindReleaseGate] } := by
  decide

theorem returnedThirtyThousand :
    assess
      { priorBenefitsPaise := 0
        productFmvPaise := 3_000_000
        productDisposition := .returned } =
      .answered
        { benefitQualifies := false
          currentBenefitPaise := 0
          aggregateBenefitsPaise := 0
          thresholdExceeded := false
          tdsDuePaise := 0
          releaseGateRequired := false
          appliedRules := [.s194rScope, .influencerProduct] } := by
  decide

theorem aggregateCrossesThreshold :
    assess
      { priorBenefitsPaise := 1_000_000
        productFmvPaise := 1_500_000
        productDisposition := .retained } =
      .answered
        { benefitQualifies := true
          currentBenefitPaise := 1_500_000
          aggregateBenefitsPaise := 2_500_000
          thresholdExceeded := true
          tdsDuePaise := 250_000
          releaseGateRequired := true
          appliedRules :=
            [.s194rScope, .influencerProduct, .annualThreshold, .inKindReleaseGate] } := by
  decide

theorem exactThresholdDoesNotTrigger :
    assess
      { priorBenefitsPaise := 0
        productFmvPaise := 2_000_000
        productDisposition := .retained } =
      .answered
        { benefitQualifies := true
          currentBenefitPaise := 2_000_000
          aggregateBenefitsPaise := 2_000_000
          thresholdExceeded := false
          tdsDuePaise := 0
          releaseGateRequired := false
          appliedRules := [.s194rScope, .influencerProduct, .annualThreshold] } := by
  decide

theorem priorBenefitsAboveThresholdAreUnsupported :
    assess
      { priorBenefitsPaise := 2_000_001
        productFmvPaise := 100_000
        productDisposition := .retained } =
      .unsupported .priorBenefitsNeedPriorTds [.annualThreshold] := by
  decide

end CreatorCommerce.Section194R
