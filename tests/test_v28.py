import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v28 import features,train_transfer_policy,decide,run
from argparse import Namespace

def test_transfer_ignores_topology():
    a=dict(nodes=20,rows=20,selectivity=.5,topology='chain',hidden=False,igv_ms=10.,full_sync_ms=7.)
    b={**a,'topology':'layered'}
    model=train_transfer_policy([a],.03,'full')
    assert features(a,'full')==features(b,'full')
    assert decide(model,b,'full')=='sync'
    assert decide({},b,'rule')=='sync'
    assert decide({},dict(b,hidden=True),'rule')=='igv'

def test_loto_pilot(tmp_path):
    a=Namespace(nodes=[6],rows=[5],selectivities=[0.,1.],topologies=['chain','fanout','layered'],train_seeds=1,test_seeds=1,
                seed_offset=9000,edits=2,min_gain=.03,training_out=str(tmp_path/'train.csv'),out=str(tmp_path/'test.csv'),summary=str(tmp_path/'summary.json'))
    result=run(a)
    assert result['all_equal']
    assert len(result['folds'])==3
    assert all(f['train_edits']==16 and f['test_edits']==8 for f in result['folds'])
    assert all(f['heldout_topology'] not in f['train_topologies'] for f in result['folds'])
