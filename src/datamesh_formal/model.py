from dataclasses import dataclass

class ContractError(ValueError):
    pass

@dataclass(frozen=True)
class Product:
    name: str
    domain: str
    schema: str
    classification: int
    max_age_hours: float

@dataclass(frozen=True)
class Transform:
    name: str
    inputs: tuple[str, ...]
    output: str
    delay_hours: float
    expected_schemas: tuple[str, ...]

class Mesh:
    def __init__(self, products: list[Product], transforms: list[Transform]):
        self.products = {p.name: p for p in products}
        if len(self.products) != len(products):
            raise ContractError('duplicate product')
        self.transforms = transforms

    def check(self) -> bool:
        edges = {p: set() for p in self.products}
        for p in self.products.values():
            if p.classification not in (0, 1, 2) or p.max_age_hours < 0:
                raise ContractError('invalid product contract')
        for t in self.transforms:
            if t.output not in self.products or not t.inputs or len(t.inputs) != len(t.expected_schemas) or t.delay_hours < 0:
                raise ContractError(f'invalid transformation {t.name}')
            if any(i not in self.products for i in t.inputs):
                raise ContractError(f'unknown input in {t.name}')
            inputs = [self.products[i] for i in t.inputs]
            if tuple(p.schema for p in inputs) != t.expected_schemas:
                raise ContractError(f'schema mismatch in {t.name}')
            out = self.products[t.output]
            if out.classification < max(p.classification for p in inputs):
                raise ContractError(f'classification downgrade in {t.name}')
            if out.max_age_hours < max(p.max_age_hours for p in inputs) + t.delay_hours:
                raise ContractError(f'unsound age bound in {t.name}')
            for i in t.inputs:
                edges[i].add(t.output)
        visited, active = set(), set()
        def visit(node):
            if node in active:
                raise ContractError('cyclic dependency')
            if node in visited:
                return
            active.add(node)
            for child in edges[node]:
                visit(child)
            active.remove(node)
            visited.add(node)
        for node in edges:
            visit(node)
        return True
