from datamesh_formal import Product, Transform, Mesh
products = [Product('orders', 'sales', 'order_v1', 1, 2), Product('report', 'analytics', 'report_v1', 1, 3)]
transforms = [Transform('aggregate', ('orders',), 'report', 1, ('order_v1',))]
print('Mesh valid:', Mesh(products, transforms).check())
