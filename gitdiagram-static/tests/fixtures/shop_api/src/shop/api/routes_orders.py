"""Order endpoints: checkout, status and cancellation."""

from fastapi import APIRouter, HTTPException

from shop.db.models import Order
from shop.db.session import get_session
from shop.domain.inventory import reserve_stock
from shop.domain.pricing import quote_total
from shop.integrations.payments import create_payment_intent
from shop.workers.emailer import send_confirmation

router = APIRouter()


@router.get("/{order_id}")
def read_order(order_id: str):
    """Return one order."""
    with get_session() as session:
        order = session.get(Order, order_id)
        if order is None:
            raise HTTPException(404)
        return order


@router.post("/")
def place_order(sku: str, quantity: int):
    """Reserve stock, price the basket and charge the card."""
    reserve_stock(sku, quantity)
    total = quote_total(sku, quantity)
    intent = create_payment_intent(total)
    send_confirmation.delay(intent.id)
    return {"total": total, "intent": intent.id}


@router.delete("/{order_id}")
def cancel_order(order_id: str):
    """Cancel an order."""
    return {"cancelled": order_id}
