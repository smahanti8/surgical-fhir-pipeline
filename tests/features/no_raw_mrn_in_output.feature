Feature: No raw MRN appears in output
  patient_mrn is a local identifier from the source system. It must not
  survive into any FHIR resource in its raw form.

  Scenario: Raw MRNs from the source cohort do not appear in the transaction bundle
    Given a synthetic cohort of 30 surgical cases generated with seed 42
    When all cases are mapped and assembled into a transaction bundle
    Then none of the cohort's raw patient MRNs appear in the bundle
