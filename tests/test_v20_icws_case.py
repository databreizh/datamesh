from datamesh_formal.evidence import row
from datamesh_formal.lifecycle import Mode, Requirement, Attestation
from datamesh_formal.materialized import MaterializedMesh
from examples.icws_case_v20 import scenario, run

def test_scenario_stages_and_oracle():
    steps=run()
    assert len(steps)==5
    assert [s['published'] for s in steps]==[False,False,True,False,False]
    assert [s['published_pending'] for s in steps]==[1,0,0,0,1]
    assert all(s['published_lineage']==3 for s in steps)

def test_join_reintroduces_identity_even_after_review():
    steps=run()
    assert steps[1]['published_violations']==2
    assert steps[1]['published'] is False
    assert steps[2]['published_violations']==0

def test_local_only_not_equivalent_to_global_lifecycle():
    steps=run()
    assert steps[0]['local_only'] is False
    # Even when the direct input is clean, missing action must still block.
    assert steps[4]['published'] is False

def test_attestation_does_not_erase_lineage():
    nodes, reqs=scenario()
    mesh=MaterializedMesh(nodes,reqs,boundaries=frozenset({'published'}))
    before=mesh.decisions()['published'].lineage
    mesh.update(attestations=[Attestation(reqs[1],'recommendations','reviewed')])
    assert mesh.decisions()['published'].lineage==before
