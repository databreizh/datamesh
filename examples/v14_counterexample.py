"""Demonstrate lifecycle distinctions using an independently evaluated finite DAG."""
from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Requirement, Mode, Attestation, recompute

nodes = [
    DataNode('health', records=(row(key='1', patient_id='private'),)),
    DataNode('research', records=(row(key='1', patient_id='returned'),)),
    DataNode('clean', ('health',), 'project', 'key', (row(key='1'),)),
    DataNode('merged', ('clean', 'research'), 'join', 'key',
             (row(key='1', patient_id='returned'),)),
]
for mode in (Mode.PERSISTENT, Mode.BOUNDARY):
    requirement = Requirement('health', 'redact', 'health', mode, 'absent:patient_id')
    states = recompute(nodes, [requirement], boundaries=frozenset({'clean', 'merged'}))
    print(mode.value, 'clean:', states['clean'].publishable,
          'merged:', states['merged'].publishable,
          'violations:', len(states['merged'].violations))
req = Requirement('health', 'audit', 'health', Mode.ONCE, 'action:audited')
states = recompute(nodes, [req], [Attestation(req, 'clean', 'audited')],
                   boundaries=frozenset({'clean', 'merged'}))
print('once', 'clean:', states['clean'].publishable,
      'merged:', states['merged'].publishable)
