import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v32 import case, train_costs, choose
from benchmark_v31 import bucket

def test_real_training_bucket_recognized():
    rows=case((8,8,.5,'chain',False),42,2,1)
    assert isinstance(rows[0]['bucket'],tuple)
    model=train_costs(rows,min_support=1)
    assert all(r['bucket'] in model for r in rows)

def test_inference_recognizes_training_bucket():
    rows=case((8,8,.5,'chain',False),42,2,1)
    model=train_costs(rows,min_support=1)
    # independently build same features for the initial state
    from benchmark_v23 import build_instance
    from benchmark_v31 import graph_features
    base,reqs,boundaries=build_instance(8,8,.5,False,'chain',42)
    f=graph_features(base,reqs,boundaries)
    assert bucket(f) in model
    assert choose(f,model,min_support=1)[1]!='unseen_bucket'

def test_csv_serialization_is_not_model_key():
    rows=case((8,8,.5,'chain',False),42,1,1)
    assert repr(rows[0]['bucket']) != rows[0]['bucket']
