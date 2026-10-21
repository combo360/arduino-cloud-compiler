"""Sends a freshly generated assistant key to the operator by email.

Two transports, tried in order:

1. **Resend** over HTTPS (`IDROID_RESEND_API_KEY`). Render's free instances
   block outbound SMTP entirely, so this is the one that works there.
2. **SMTP** (`IDROID_SMTP_*`), for paid instances and self-hosted machines.

If neither works, the key is written to the server log instead, so a broken
mail path can never lock the operator out of their own server. The key is
never returned to whoever requested it: the point of the email is that only
the operator sees it.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

RESEND_URL = "https://api.resend.com/emails"


def _recipient() -> str:
    return os.environ.get("IDROID_KEY_EMAIL", "ibrahcombo360@gmail.com").strip()


def _body(key: str, origin: str) -> str:
    return (
        "Someone asked for access to the i-Droid assistant.\n\n"
        f"Access key: {key}\n\n"
        f"Requested by: {origin}\n"
        "Give this key to the people who should be able to use the assistant.\n"
        "It can be revoked by deleting its entry in assistant_keys.json.\n"
    )


def _log_key(key: str, origin: str, why: str) -> str:
    print(f"[assistant] {why}; key for {_recipient()}: {key} "
          f"(requested by {origin})", flush=True)
    return "logged"


def _send_resend(api_key: str, key: str, origin: str) -> None:
    """Posts the key through Resend's HTTP API. Raises on failure."""
    import httpx  # imported here so the module loads without the dependency

    sender = os.environ.get("IDROID_EMAIL_FROM", "onboarding@resend.dev").strip()
    response = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": sender,
            "to": [_recipient()],
            "subject": "i-Droid assistant access key",
            "text": _body(key, origin),
        },
        timeout=20,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Resend answered {response.status_code}: {response.text[:200]}")


def _send_smtp(key: str, origin: str) -> str | None:
    """Returns "email" when sent, None when SMTP is not configured.

    Raises when SMTP is configured but fails.
    """
    user = os.environ.get("IDROID_SMTP_USER", "").strip()
    host = os.environ.get("IDROID_SMTP_HOST", "").strip()
    password = os.environ.get("IDROID_SMTP_PASSWORD", "").strip()
    if not (host and user and password):
        return None

    port = int(os.environ.get("IDROID_SMTP_PORT", "587") or 587)
    sender = os.environ.get("IDROID_SMTP_FROM", "").strip() or user

    message = EmailMessage()
    message["Subject"] = "i-Droid assistant access key"
    message["From"] = sender
    message["To"] = _recipient()
    message.set_content(_body(key, origin))

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
    return "email"


def send_key(key: str, *, requested_by: str = "") -> str:
    """Emails [key] and returns "email" or "logged"."""
    origin = requested_by or "a user of the app"

    resend_key = os.environ.get("IDROID_RESEND_API_KEY", "").strip()
    if resend_key:
        try:
            _send_resend(resend_key, key, origin)
            return "email"
        except Exception as error:  # noqa: BLE001 - the caller only logs it
            print(f"[assistant] Resend could not send the key: {error}",
                  flush=True)
            return _log_key(key, origin, "HTTPS delivery failed")

    try:
        result = _send_smtp(key, origin)
    except Exception as error:  # noqa: BLE001 - the caller only logs it
        print(f"[assistant] SMTP could not send the key: {error}", flush=True)
        return _log_key(key, origin, "SMTP delivery failed")

    if result is None:
        return _log_key(key, origin, "no mail transport configured")
    return result
