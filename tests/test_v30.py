import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v30 import irregular_instance
from benchmark_v29 import collect, train
from datamesh_formal.materialized import MaterializedMesh
import pytest

def test_irregular_structure_and_mixed_governance():
    nodes,requirements,boundaries=irregular_instance(20,20,.5,False,'chain',11)
    assert len(nodes)==20 and len(requirements)==2 and len(boundaries)==2
    assert len({n.inputs for n in nodes[3:]})>1
    assert MaterializedMesh(nodes,requirements,boundaries=boundaries).decisions()

@pytest.mark.parametrize('topology',['chain','fanout','layered'])
@pytest.mark.parametrize('hidden',[True,False])
def test_irregular_oracle_equivalence(topology,hidden):
    cfg=(20,20,.5,topology,hidden)
    result=collect(cfg,15,3,policies={'adaptive_full':({(20,20,.5,hidden):'sync'},'full')},builder=irregular_instance)
    assert len(result)==3 and all(r['equal'] for r in result)

def test_invalid_generator_inputs():
    with pytest.raises(ValueError):irregular_instance(4,20,.5,False,'chain',1)
    with pytest.raises(ValueError):irregular_instance(20,20,2,False,'chain',1)
