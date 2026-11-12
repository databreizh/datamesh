from dataclasses import replace
import random
import pytest
from datamesh_formal.incremental import GovernanceMesh, Node, Policy, Certificate


def sample():
    cert = Certificate("no-export", "sales", 1, frozenset({"aggregate"}))
    return GovernanceMesh([
        Node("sales", "Sales", source_obligations=frozenset({"no-export"})),
        Node("analytics", "Analytics", ("sales",), "aggregate", certificates=(cert,)),
        Node("marketing", "Marketing", ("analytics",), "publish"),
        Node("finance", "Finance", ("sales",), "copy"),
        Node("reports", "Finance", ("finance",), "publish"),
    ], [Policy("sales", 1, frozenset({"no-export"}))])


def test_initial_and_stopping():
    m = sample()
    m.initialize()
    assert "no-export" not in m.cache["marketing"].obligations
    assert "no-export" in m.cache["reports"].obligations
    s = m.update(node_changes={"sales":replace(m.nodes["sales"], source_obligations=frozenset({"no-export", "research-only"}))})
    assert s.recomputed == 5
    assert m.cache == m.full_verify()
    s = m.update(node_changes={"sales":replace(m.nodes["sales"], domain="Sales2")})
    assert s.recomputed == 1 and s.stopped == 1
    assert m.cache == m.full_verify()


def test_policy_revocation_propagates_even_with_same_obligations():
    m = sample(); m.initialize()
    s = m.update(policy_changes={"sales":Policy("sales", 2, frozenset({"no-export"}))})
    assert s.recomputed >= 2
    assert not m.cache["analytics"].valid
    assert not m.cache["marketing"].valid
    assert "no-export" in m.cache["analytics"].obligations
    assert m.cache == m.full_verify()


def test_unrelated_policy_change_stops():
    m = sample(); m.initialize()
    s = m.update(policy_changes={"sales":Policy("sales", 1, frozenset({"no-export", "another"}))})
    assert s.recomputed == 1 and s.stopped == 1
    assert m.cache == m.full_verify()


def test_invalid_topology():
    with pytest.raises(ValueError, match="cyclic"):
        GovernanceMesh([Node("a","D",("b",),"copy"),Node("b","D",("a",),"copy")], [])


def test_randomized_differential():
    rng = random.Random(20261014)
    for seed in range(20):
        nodes = [Node("n0", "d0", source_obligations=frozenset({"o"}))]
        for i in range(1, 65):
            parents = tuple(sorted(rng.sample(range(i), rng.randint(1, min(i,3)))))
            nodes.append(Node(f"n{i}",f"d{i%7}",tuple(f"n{j}" for j in parents),"copy"))
        m = GovernanceMesh(nodes, [Policy("p", 1, frozenset({"o"}))]); m.initialize()
        for j in range(15):
            idx = rng.randrange(65)
            n = m.nodes[f"n{idx}"]
            new_ob = frozenset({"o", f"x{j%4}"}) if j%2 else frozenset({"o"})
            m.update(node_changes={n.id:replace(n,introduced_obligations=new_ob)})
            assert m.cache == m.full_verify(), (seed,j)
