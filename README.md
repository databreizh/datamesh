# Data Mesh Calculus — research artifact (CAiSE 2027)

This repository contains the Python prototype, tests, original result files and reproduction commands for the governance maintenance study. It is a **research artifact**, not a production governance service.

## Quick start (Python >=3.10)

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e . pytest
python -m pytest -q
python experiments/run_e1.py
python experiments/analyze_lineage.py
```

The first E1 command generates `results/e1_reproduced.csv` with 2,700 edit records; the second annotates targets and lineage in `results/e1_lineage_reproduced.csv`. The E1 driver uses fixed seeds, graph sizes 20/100, topologies chain/fanout/layered, origins source/middle/leaf, five seeds and 30 edits per setting. It compares IGV, descendants and the full public-decision oracle. **Check all PASS counts before comparing aggregate results.**

## E1: governance-only ablation (Tables 2–4)

- Driver: `experiments/run_e1.py`
- Lineage reconstruction: `experiments/analyze_lineage.py`
- Supplied original records: `results/e1_original.csv`, `results/e1_lineage_original.csv`
- `run_e1.py` is a path-portable copy of the actual V15 experiment script. `analyze_lineage.py` is a path-portable copy of the V16 analysis script.
- For attestation edits, some target nodes are outside the originating requirement's lineage; interpret aggregate savings accordingly.
- These tables count governance evaluations **not elapsed-time speedups**.

## E2: broader differential/data-refresh campaign

- Driver: `examples/benchmark_v32.py`
- Original test records: `results/e2_original.csv`
- Original training records: `results/e2_training_original.csv`
- Original summary: `results/e2_summary_original.json`
- The experiment refreshes data as well as updating governance. Its latency comparisons **must not** be presented as governance-only speedups.

### E2 quick pilot

```bash
python examples/benchmark_v32.py --nodes 8 --rows 8 --selectivities 0.5 --topologies chain fanout layered --train-seeds 2 --test-seeds 2 --edits 2 --repeats 1 --min-support 2 --training-out results/e2_training_pilot_reproduced.csv --out results/e2_pilot_reproduced.csv --summary results/e2_pilot_summary_reproduced.json
```

### E2 full experiment (substantially longer)

```bash
python examples/benchmark_v32.py --nodes 20 100 --rows 20 100 --selectivities 0 0.5 1 --topologies chain fanout layered --train-seeds 10 --test-seeds 10 --edits 8 --repeats 3 --training-out results/e2_training_reproduced.csv --out results/e2_reproduced.csv --summary results/e2_summary_reproduced.json
```

Compare correctness/row counts before timing. Timings depend on hardware, CPU load and Python version. Do not expect bit-identical latencies.

## Notes on reproducibility and limitations

- E1 is a diagnostic synthetic workload. Its random edits include attestations outside the requirement lineage.
- E1 full oracle checks **public decisions**, while IGV vs descendants also compares complete caches.
- The original E1 CSV omits edit target; `analyze_lineage.py` reconstructs it from the fixed generator and random seeds. Its lineage labeling assumes the graph generator in `run_e1.py` is unchanged.
- The code in `src/` and `examples/` comes from the v0.32.1 prototype snapshot. Historical experiment scripts are retained where needed to preserve imports, not to claim additional evaluations.
- The artifact does not implement distributed execution or legal compliance verification.

## Contents

- `src/datamesh_formal/`: executable core
- `tests/`: original regression tests
- `examples/`: original benchmark scripts and their dependencies
- `experiments/`: E1 and lineage reproduction
- `results/`: original reported outputs (reproduced outputs generated locally)

## Artifact scope

This repository provides the executable prototype, experiment drivers, regression tests, and recorded results needed to inspect and reproduce the evaluation reported in the accompanying submission. It is intended for research reproducibility and is not a production-ready deployment.
