Feature: Unmapped procedure code drops the case
  A SurgicalCase whose procedure_local_code has no terminology binding
  cannot be safely coded in FHIR. Per D1 in DECISIONS.md, the pipeline
  drops such a case entirely rather than emitting an uncoded Procedure.

  Scenario: A case with an unmapped procedure code is not exchangeable
    Given a synthetic cohort of 50 surgical cases generated with seed 42
    And at least one case in the cohort has an unmapped procedure code
    When that case is mapped to FHIR
    Then the case produces no FHIR resources
    And the case's mapping issues include an ERROR on Procedure.code
