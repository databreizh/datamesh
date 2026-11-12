from dataclasses import replace
import pytest
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute
from datamesh_formal.evidence import DataNode, row


def fixture():
    a=DataNode('a',records=(row(id='1', patient_id='p1'),))
    b=DataNode('b',inputs=('a',),operation='project',parameter='id',records=(row(id='1'),))
    return [a,b]


def test_full_sync_preserves_engine_and_untouched_states():
    nodes=fixture(); mesh=MaterializedMesh(nodes,(),boundaries=frozenset({'b'}))
    engine=mesh.engine; untouched=mesh.engine.cache['b']
    edit=replace(nodes[0],records=(row(id='1',patient_id='p2'),))
    stats=mesh.full_sync(sources=(edit,))
    expected=full_refresh(nodes,sources=(edit,))
    assert mesh.engine is engine and mesh.engine.cache['b'] is untouched
    assert {k:v.records for k,v in mesh.nodes.items()}=={n.id:n.records for n in expected}
    assert mesh.decisions()==recompute(expected,(),(),frozenset({'b'}))
    assert stats['cache_rebuilt'] is False


def test_full_sync_rejects_invalid_patch_without_mutation():
    mesh=MaterializedMesh(fixture(),())
    before=dict(mesh.nodes)
    with pytest.raises(ValueError): mesh.full_sync(sources=(replace(fixture()[1],records=()),))
    assert mesh.nodes==before
