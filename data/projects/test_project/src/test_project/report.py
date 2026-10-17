from . import orders


def summary():
    return f"Итого: {orders.total()}"
