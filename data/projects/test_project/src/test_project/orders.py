from . import storage


def total(items=None):
    items = storage.load() if items is None else items
    return sum(i["price"] for i in items)
