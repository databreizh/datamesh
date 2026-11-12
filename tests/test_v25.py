import importlib.util
from pathlib import Path

def load():
    spec=importlib.util.spec_from_file_location('benchmark_v25',Path(__file__).resolve().parents[1]/'examples'/'benchmark_v25.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_break_even_and_rebuild():
    m=load();r=dict(nodes=20,rows=20,selectivity=0.,topology='chain',hidden=False,seed=1,edit=0,
      igv_governance_nodes=2,igv_operators=1,full_total_ms=1.,igv_total_ms=2.,full_cache_rebuild_ms=2.,full_switch_total_ms=3.,adaptive_total_ms=2.,adaptive_choice='igv',equal=True)
    summary={'test_initializations':[dict(nodes=20,rows=20,selectivity=0.,topology='chain',hidden=False,seed=1,igv_initialization_ms=3.)]}
    a=m.audit([r],summary,[1,4],[0,.5]);g=a['groups'][0]
    assert g['full_without_rebuild_faster'] and not g['full_with_rebuild_faster']
    assert g['full_only_vs_igv_break_even_edits'] is None
    assert g['horizons'][1]['full_once_then_igv_ms']==3+3+3*2

def test_full_only_break_even():
    m=load();r=dict(nodes=20,rows=20,selectivity=0.,topology='chain',hidden=False,seed=1,edit=0,
      igv_governance_nodes=20,igv_operators=19,full_total_ms=3.,igv_total_ms=2.,full_cache_rebuild_ms=2.,full_switch_total_ms=5.,adaptive_total_ms=2.,adaptive_choice='igv',equal=True)
    summary={'test_initializations':[dict(nodes=20,rows=20,selectivity=0.,topology='chain',hidden=False,seed=1,igv_initialization_ms=3.)]}
    g=m.audit([r],summary,[3,4],[0])['groups'][0]
    assert g['full_only_vs_igv_break_even_edits']==4
