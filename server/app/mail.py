"""Sends a freshly generated assistant key to the operator by email.

Falls back to the server log when SMTP is not configured, so the flow can be
tested before email credentials exist. The key is never returned to whoever
requested it: the point of the email is that only the operator sees it.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def _config() -> tuple[str, int, str, str, str, str]:
    user = os.environ.get("IDROID_SMTP_USER", "").strip()
    return (
        os.environ.get("IDROID_SMTP_HOST", "").strip(),
        int(os.environ.get("IDROID_SMTP_PORT", "587") or 587),
        user,
        os.environ.get("IDROID_SMTP_PASSWORD", "").strip(),
        os.environ.get("IDROID_SMTP_FROM", "").strip() or user,
        os.environ.get("IDROID_KEY_EMAIL", "ibrahcombo360@gmail.com").strip(),
    )


def send_key(key: str, *, requested_by: str = "") -> str:
    """Emails [key] and returns "email" or "logged"."""
    host, port, user, password, sender, recipient = _config()
    origin = requested_by or "a user of the app"

    if not (host and user and password):
        print(f"[assistant] SMTP not configured; key for {recipient}: {key} "
              f"(requested by {origin})", flush=True)
        return "logged"

    message = EmailMessage()
    message["Subject"] = "i-Droid assistant access key"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Someone asked for access to the i-Droid assistant.\n\n"
        f"Access key: {key}\n\n"
        f"Requested by: {origin}\n"
        "Give this key to the people who should be able to use the assistant.\n"
        "It can be revoked by deleting its entry in assistant_keys.json.\n"
    )

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
    return "email"
