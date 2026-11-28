- Raise the Tooling Tests job limit from 35 to 63 minutes. The documented rule
  adds the step caps (20 minutes) to 1.25 times the longest healthy run of the
  uncapped steps (34.3 minutes). Pytest still limits each test to 300 seconds.
