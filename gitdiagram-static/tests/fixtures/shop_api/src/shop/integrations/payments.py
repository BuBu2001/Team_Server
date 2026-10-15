"""Stripe payment intents."""

import os

import stripe


def configure_stripe() -> None:
    """Load the API key from the environment."""
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def create_payment_intent(amount):
    """Create a payment intent for an amount in minor units."""
    return stripe.PaymentIntent.create(amount=int(amount * 100), currency="eur")
