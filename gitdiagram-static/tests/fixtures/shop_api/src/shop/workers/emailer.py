"""Order confirmation emails, delivered asynchronously."""

import os

from celery import Celery

from shop.db.models import Order
from shop.integrations.mailer import deliver

celery_app = Celery("shop", broker=os.environ.get("CELERY_BROKER_URL"))


@celery_app.task(name="send_confirmation")
def send_confirmation(order_id: str) -> None:
    """Send the confirmation message for an order."""
    deliver(to="customer@example.com", subject=f"Order {order_id}")
