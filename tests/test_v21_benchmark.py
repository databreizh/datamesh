from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from types import SimpleNamespace

spec=spec_from_file_location('bench21',Path(__file__).resolve().parents[1]/'examples'/'benchmark_v21.py')
bench=module_from_spec(spec);spec.loader.exec_module(bench)

def test_selectivity_and_graph_size():
    for selectivity in (0,0.5,1):
        nodes,reqs,boundaries=bench.build(10,8,selectivity)
        assert len(nodes)==10
        assert len(next(n for n in nodes if n.id=='merged').records)==round(8*selectivity)

def test_hidden_cutoff():
    nodes,_,_=bench.build(10,8,1,True)
    assert all('patient_id' not in dict(r) for r in nodes[-1].records)

def test_small_paired_campaign(tmp_path):
    rows,groups=bench.run(SimpleNamespace(nodes=[8],rows=[4],selectivities=[0,1],seeds=2,edits=2,out=str(tmp_path/'a.csv'),summary=str(tmp_path/'a.json')))
    assert len(rows)==16 and len(groups)==4
    assert all(r['equal'] for r in rows)

def test_bootstrap_deterministic():
    pairs=[(2.,1.),(4.,2.),(6.,3.)]
    assert bench.bootstrap_ci(pairs)==[2.,2.]
