"""Transactional mail over HTTP."""

import httpx


def deliver(to: str, subject: str) -> None:
    """Post a message to the mail provider."""
    httpx.post("https://mail.example.com/send", json={"to": to, "subject": subject})
