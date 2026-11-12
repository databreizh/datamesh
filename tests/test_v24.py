import importlib.util, sys
from pathlib import Path
from argparse import Namespace

def module():
    path=Path(__file__).resolve().parents[1]/'examples'/'benchmark_v24.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('v24bench',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def test_policy_margin_and_cost():
    m=module()
    k=(8,5,0,'chain',False)
    base=dict(nodes=8,rows=5,selectivity=0,topology='chain',hidden=False,seed=0)
    train=[dict(base,full_total_ms=1,igv_total_ms=5)]
    init=[dict(base,adaptive_initialization_ms=1)]
    assert m.train_policy(train,init)[k]['choice']=='full'
    init[0]['adaptive_initialization_ms']=10
    assert m.train_policy(train,init)[k]['choice']=='igv'

def test_disjoint_holdout_and_correctness(tmp_path):
    m=module()
    args=Namespace(nodes=[8],rows=[5],selectivities=[0,1],topologies=['chain','fanout'],
        train_seeds=2,test_seeds=2,seed_offset=5000,edits=2,bootstrap=30,margin=.05,
        out=str(tmp_path/'v24.csv'),summary=str(tmp_path/'v24.json'))
    rows,report=m.run(args)
    assert len(rows)==32 and report['all_equal']
    assert report['training_edits']==32
    assert report['test_instances']==16
    assert {r['seed'] for r in rows}=={5002,5003}
    assert all(r['oracle_hindsight_ms']<=r['igv_total_ms'] for r in rows)
    assert all(r['oracle_hindsight_ms']<=r['full_switch_total_ms'] for r in rows)
