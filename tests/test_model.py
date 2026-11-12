import unittest
from datamesh_formal import Product, Transform, Mesh, ContractError

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.a = Product('a', 'sales', 'raw', 1, 2)
        self.b = Product('b', 'analytics', 'derived', 1, 3)
        self.t = Transform('derive', ('a',), 'b', 1, ('raw',))

    def test_valid(self):
        self.assertTrue(Mesh([self.a, self.b], [self.t]).check())

    def test_classification(self):
        with self.assertRaisesRegex(ContractError, 'classification'):
            Mesh([self.a, Product('b', 'analytics', 'derived', 0, 3)], [self.t]).check()

    def test_freshness(self):
        with self.assertRaisesRegex(ContractError, 'age'):
            Mesh([self.a, Product('b', 'analytics', 'derived', 1, 2)], [self.t]).check()

    def test_schema(self):
        with self.assertRaisesRegex(ContractError, 'schema'):
            Mesh([self.a, self.b], [Transform('derive', ('a',), 'b', 1, ('wrong',))]).check()

    def test_cycle(self):
        reverse = Transform('reverse', ('b',), 'a', 0, ('derived',))
        with self.assertRaisesRegex(ContractError, 'cyclic'):
            Mesh([self.a, Product('b', 'analytics', 'derived', 1, 2)], [Transform('derive', ('a',), 'b', 0, ('raw',)), reverse]).check()

if __name__ == '__main__':
    unittest.main()
