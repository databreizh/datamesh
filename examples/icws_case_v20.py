"""E-commerce inspired synthetic scenario; NOT an implementation of ICWS 2024.

Compare (a) lifecycle-aware incremental state with (b) complete lifecycle oracle,
and (c) a deliberately limited local-only contract check. All measurements are
end-to-end Python wall time; local-only is a counterexample, not a fair speed baseline.
"""
from dataclasses import replace
from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Mode, Requirement, Attestation, recompute, check_predicate
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from time import perf_counter_ns
import argparse, csv, json, statistics


def scenario():
    # Customer data flows through an identity-stripping recommendation view.
    # A second source can reintroduce the identity at a later join.
    customer=DataNode('customers',records=(row(key='1',patient_id='P1',segment='A'),))
    recommendation=DataNode('recommendations',('customers',),'project','key,segment',
                            (row(key='1',segment='A'),))
    enrich=DataNode('external',records=(row(key='1',patient_id='P1'),))
    merged=DataNode('merged',('recommendations','external'),'join','key',
                    (row(key='1',segment='A',patient_id='P1'),))
    released=DataNode('published',('merged',),'project','key,segment,patient_id',
                      (row(key='1',segment='A',patient_id='P1'),))
    privacy=Requirement('commerce','remove_identity','customers',Mode.PERSISTENT,'absent:patient_id')
    review=Requirement('commerce','review','customers',Mode.ONCE,'action:reviewed')
    at_release=Requirement('commerce','no_identity_at_release','customers',Mode.BOUNDARY,'absent:patient_id')
    return [customer,recommendation,enrich,merged,released],[privacy,review,at_release]


def local_contract_only(nodes, boundary):
    """Intentionally incomplete control: check the immediate input only.

    Illustrates the loss of inherited obligations; NOT a claimed ICWS baseline.
    """
    by={n.id:n for n in nodes}
    return check_predicate('absent:patient_id',by[by[boundary].inputs[0]].records)


def run():
    nodes,reqs=scenario(); boundaries=frozenset({'recommendations','published'})
    mesh=MaterializedMesh(nodes,reqs,boundaries=boundaries)
    stages=[]
    def check(label, patch=None, att=None):
        t0=perf_counter_ns(); stats=mesh.update(sources=patch or (),attestations=att) if patch or att is not None else {'recomputed':0,'operators_evaluated':0}
        ti=(perf_counter_ns()-t0)/1e6
        t0=perf_counter_ns(); fresh=full_refresh(list(mesh.nodes.values())); oracle=recompute(fresh,reqs,mesh.engine.attestations,boundaries); tf=(perf_counter_ns()-t0)/1e6
        assert mesh.decisions()==oracle
        assert {n.id:n.records for n in fresh}=={n.id:n.records for n in mesh.nodes.values()}
        ds=mesh.decisions()
        stages.append(dict(stage=label,recommendations=ds['recommendations'].publishable,
                           published=ds['published'].publishable,
                           published_violations=len(ds['published'].violations),
                           published_pending=len(ds['published'].pending),
                           published_lineage=len(ds['published'].lineage),
                           local_only=local_contract_only(fresh,'published'),
                           incremental_ms=round(ti,6),full_ms=round(tf,6),
                           recomputed=stats['recomputed'],operators_evaluated=stats['operators_evaluated']))
    check('initial_no_attestation')
    att=[Attestation(reqs[1],'recommendations','reviewed')]
    check('review_attested',att=att)
    clean=replace(nodes[2],records=(row(key='1'),))
    check('external_source_cleaned',patch=[clean])
    check('identity_reintroduced',patch=[nodes[2]])
    check('attestation_revoked',att=[])
    return stages


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='icws_case_v20.csv');p.add_argument('--summary',default='icws_case_v20.json');args=p.parse_args()
    stages=run()
    with open(args.out,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(stages[0]));w.writeheader();w.writerows(stages)
    with open(args.summary,'w',encoding='utf8') as f:json.dump({'scenario':'synthetic e-commerce inspired, not ICWS reproduction','all_equal':True,'stages':stages,'timing_warning':'5 individual observations; no performance inference'},f,indent=2)
    for s in stages:print(s['stage'], 'published=',s['published'],'local_only=',s['local_only'],'violations=',s['published_violations'],'pending=',s['published_pending'])
    print('All incremental states equal full oracle')
if __name__=='__main__':main()
