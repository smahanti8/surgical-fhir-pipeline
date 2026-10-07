Feature: Missing laterality on a sided procedure drops the case
  Per D2 in DECISIONS.md, a sided procedure (e.g. inguinal hernia repair)
  with no recorded laterality is a wrong-site-surgery-class data defect.
  The pipeline drops the case rather than defaulting a side.

  Scenario: A sided procedure case with no laterality is not exchangeable
    Given a synthetic cohort of 50 surgical cases generated with seed 42
    And at least one case in the cohort has a sided procedure with no laterality recorded
    When that case is mapped to FHIR
    Then the case produces no FHIR resources
    And the case's mapping issues include an ERROR on Procedure.bodySite
