import Lean.Data.Json
import CreatorCommerce.Section194R

namespace CreatorCommerce.CaseProtocol

open Lean CreatorCommerce.Section194R

set_option autoImplicit false

/-!
# The generated-case wire protocol

Lean is the only component that evaluates `assess`. The first generated case
prints a candidate `Decision` in this explicit JSON format. A second generated
case states that candidate as a concrete equality, asks the kernel to check it,
and prints the checked form only if the theorem elaborates successfully.
-/

def candidateSchemaVersion : String := "lean-candidate-v0"
def checkedSchemaVersion : String := "lean-checked-v0"
def modelVersion : String := "in-s194r-fy2024-25-v0"

def productDispositionJson : ProductDisposition → Json
  | .retained => .str "retained"
  | .returned => .str "returned"

def ruleIdJson : RuleId → Json
  | .s194rScope => .str "s194rScope"
  | .influencerProduct => .str "influencerProduct"
  | .annualThreshold => .str "annualThreshold"
  | .inKindReleaseGate => .str "inKindReleaseGate"

def unsupportedReasonJson : UnsupportedReason → Json
  | .priorBenefitsNeedPriorTds => .str "priorBenefitsNeedPriorTds"

def ruleIdsJson (rules : List RuleId) : Json :=
  .arr (rules.toArray.map ruleIdJson)

def factsJson (facts : Facts) : Json :=
  Json.mkObj
    [ ("priorBenefitsPaise", toJson facts.priorBenefitsPaise)
    , ("productFmvPaise", toJson facts.productFmvPaise)
    , ("productDisposition", productDispositionJson facts.productDisposition)
    ]

def answerJson (answer : Answer) : Json :=
  Json.mkObj
    [ ("benefitQualifies", toJson answer.benefitQualifies)
    , ("currentBenefitPaise", toJson answer.currentBenefitPaise)
    , ("aggregateBenefitsPaise", toJson answer.aggregateBenefitsPaise)
    , ("thresholdExceeded", toJson answer.thresholdExceeded)
    , ("tdsDuePaise", toJson answer.tdsDuePaise)
    , ("releaseGateRequired", toJson answer.releaseGateRequired)
    , ("appliedRules", ruleIdsJson answer.appliedRules)
    ]

def decisionJson : Decision → Json
  | .answered answer =>
      Json.mkObj
        [ ("kind", .str "answered")
        , ("answer", answerJson answer)
        ]
  | .unsupported reason citedRules =>
      Json.mkObj
        [ ("kind", .str "unsupported")
        , ("reason", unsupportedReasonJson reason)
        , ("citedRules", ruleIdsJson citedRules)
        ]

def caseJson (schemaVersion : String) (facts : Facts) (decision : Decision) : Json :=
  Json.mkObj
    [ ("schemaVersion", .str schemaVersion)
    , ("modelVersion", .str modelVersion)
    , ("facts", factsJson facts)
    , ("decision", decisionJson decision)
    ]

def candidateJson (facts : Facts) : Json :=
  caseJson candidateSchemaVersion facts (assess facts)

def checkedJson (facts : Facts) (decision : Decision) : Json :=
  caseJson checkedSchemaVersion facts decision

def emit (json : Json) : IO Unit :=
  IO.println (Json.compress json)

end CreatorCommerce.CaseProtocol
