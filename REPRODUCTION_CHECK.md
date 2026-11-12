# Local reproduction check (2026-11-12)

Executed in the packaging environment (Python 3.13):

- `PYTHONPATH=src python -m pytest -q`: **143 passed**.
- `PYTHONPATH=src python experiments/run_e1.py`: **2700 rows; 2700 PASS**.
- `python experiments/analyze_lineage.py`: lineage split matches supplied E1 original results.
- E2 small pilot command in README: **exit status 0**.

This does not establish portability to every environment or identical wall-clock timing. Please reproduce on a clean installation and report the Python version, hardware, and any differences. Full E2 was **not rerun** in this packaging check.
