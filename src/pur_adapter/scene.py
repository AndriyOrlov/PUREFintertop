"""Whole-scene calculations: world placement and the order items are drawn in.

PureRef is built on Qt's QGraphicsScene, so it follows Qt's stacking rules,
confirmed with the installed PureRef 2.1.3 (see docs/NATIVE_WINDOWS.md):

* `z` only orders an item among its siblings (items with the same parent).
* A child is drawn above its parent, together with the parent's whole subtree.
* Siblings with the same `z` are drawn in creation order (lower ID first).
"""

from . import qt

MAX_NESTING_DEPTH = 256


def world_properties(board):
    """Return {item_id: (world_matrix, world_opacity)} for every item.

    The world matrix is the item's own transform followed by all parent
    transforms. Opacity multiplies down the hierarchy.
    """
    cache = {}
    visiting = set()

    def resolve(item_id):
        if item_id in cache:
            return cache[item_id]
        if item_id in visiting or len(visiting) > MAX_NESTING_DEPTH:
            raise ValueError("Invalid or excessive parent hierarchy")
        visiting.add(item_id)
        item = board.get(item_id)
        matrix = board.transform_matrix(item_id)
        opacity = item.opacity
        if item.parent_id is not None:
            parent_matrix, parent_opacity = resolve(item.parent_id)
            matrix = qt.multiply(matrix, parent_matrix)
            opacity *= parent_opacity
        visiting.remove(item_id)
        cache[item_id] = (matrix, opacity)
        return cache[item_id]

    for item in board.items:
        resolve(item.id)
    return cache


def drawing_order(board):
    """Return item IDs from the bottom-most to the top-most, as PureRef draws them."""
    children = {}
    for item in board.items:
        children.setdefault(item.parent_id, []).append(item)

    order = []

    def visit(parent_id, depth):
        if depth > MAX_NESTING_DEPTH:
            raise ValueError("Invalid or excessive parent hierarchy")
        for item in sorted(children.get(parent_id, []), key=lambda i: (i.z, i.id)):
            order.append(item.id)
            visit(item.id, depth + 1)

    visit(None, 0)
    if len(order) != len(board.items):
        raise ValueError("Parent cycle or missing parent in the scene")
    return order
