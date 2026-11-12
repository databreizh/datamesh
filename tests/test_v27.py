import importlib.util
from pathlib import Path

def load():
    spec=importlib.util.spec_from_file_location('benchmark_v27',Path(__file__).resolve().parents[1]/'examples'/'benchmark_v27.py')
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_train_only_and_margin():
    m=load()
    base={'nodes':20,'rows':20,'selectivity':1.,'topology':'chain','hidden':False}
    a={**base,'igv_ms':10.,'full_sync_ms':8.}
    b={**base,'igv_ms':10.,'full_sync_ms':11.}
    assert m.train_policy([a],.03)[m.key(20,20,1.,'chain',False)]=='sync'
    assert m.train_policy([b],.03)[m.key(20,20,1.,'chain',False)]=='igv'
    assert m.train_policy([a],.25)[m.key(20,20,1.,'chain',False)]=='igv'

def test_small_heldout_run(tmp_path):
    from argparse import Namespace
    m=load()
    result=m.run(Namespace(nodes=[6],rows=[5],selectivities=[0.,1.],topologies=['chain'],train_seeds=2,test_seeds=2,
                           seed_offset=6000,edits=2,min_gain=.03,training_out=str(tmp_path/'train.csv'),
                           out=str(tmp_path/'test.csv'),summary=str(tmp_path/'summary.json')))
    assert result['all_equal'] and result['train_edits']==16 and result['test_edits']==16
    assert result['test_seed_range'][0]>result['train_seed_range'][1]
