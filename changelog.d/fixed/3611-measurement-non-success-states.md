- **The perf regression gate and the external-bench comparison no longer report success for runs that measured nothing.**
  `scripts/perf/check-regression.py` exits 3 and names a state (`missing`, `failed`, `skipped`,
  `incomparable`) for every cell it could not compare, including cells from different hardware or a
  different workload; `--advisory` masks only the regression exit code. `tools/external-bench/compare.py`
  exits 5 and counts failed wrapper runs per competitor, and `normalise_output` refuses to fill an empty or
  incomplete wrapper output with zeros (ADR-3611).
