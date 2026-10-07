Feature: Out-of-range physiological readings are never emitted as final
  A sensor artefact outside physiologic bounds must not enter a clinical
  record as a normal reading. The pipeline emits it as entered-in-error
  with the value removed instead.

  Scenario: An out-of-range reading is recorded as entered-in-error, not final
    Given a synthetic cohort of 60 surgical cases generated with seed 42
    When all cases are mapped and built into a quality report
    Then every entered-in-error Observation has no value
    And every entered-in-error Observation has a dataAbsentReason
