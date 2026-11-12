from dataclasses import replace
from datamesh_formal.incremental import GovernanceMesh, Node, Policy, Certificate

cert = Certificate("no-export", "sales", 1, frozenset({"aggregate"}))
mesh = GovernanceMesh([
    Node("orders", "Sales", source_obligations=frozenset({"no-export"})),
    Node("metrics", "Analytics", ("orders",), "aggregate", certificates=(cert,)),
    Node("campaigns", "Marketing", ("metrics",), "publish"),
    Node("billing", "Finance", ("orders",), "copy"),
], [Policy("sales", 1, frozenset({"no-export"}))])
print("INITIAL:", mesh.initialize())
print("CHANGE:", mesh.update(policy_changes={"sales": Policy("sales", 2, frozenset())}))
print("AFTER:", mesh.cache)
assert mesh.cache == mesh.full_verify()
