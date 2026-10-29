"""Delivery must never lose the key.

Whatever happens to the transport, the operator has to be able to find the key
somewhere — that is the whole point of these tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mail  # noqa: E402


def clear_transports(monkeypatch):
    for name in ("IDROID_RESEND_API_KEY", "IDROID_SMTP_HOST",
                 "IDROID_SMTP_USER", "IDROID_SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IDROID_KEY_EMAIL", "operator@example.com")


def test_without_a_transport_the_key_is_logged(monkeypatch, capsys):
    clear_transports(monkeypatch)

    assert mail.send_key("idroid-test-key", requested_by="1.2.3.4") == "logged"

    out = capsys.readouterr().out
    assert "idroid-test-key" in out
    assert "no mail transport configured" in out


def test_a_failing_https_send_still_reports_the_key(monkeypatch, capsys):
    clear_transports(monkeypatch)
    monkeypatch.setenv("IDROID_RESEND_API_KEY", "re_not_a_real_key")

    def unreachable(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(mail, "_send_resend", unreachable)

    assert mail.send_key("idroid-test-key", requested_by="1.2.3.4") == "logged"

    out = capsys.readouterr().out
    assert "idroid-test-key" in out
    assert "HTTPS delivery failed" in out


def test_a_failing_smtp_send_still_reports_the_key(monkeypatch, capsys):
    clear_transports(monkeypatch)
    monkeypatch.setenv("IDROID_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("IDROID_SMTP_USER", "someone@example.com")
    monkeypatch.setenv("IDROID_SMTP_PASSWORD", "secret")

    def unreachable(key, origin):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(mail, "_send_smtp", unreachable)

    assert mail.send_key("idroid-test-key") == "logged"

    out = capsys.readouterr().out
    assert "idroid-test-key" in out
    assert "SMTP delivery failed" in out


def test_a_working_https_send_does_not_log_the_key(monkeypatch, capsys):
    clear_transports(monkeypatch)
    monkeypatch.setenv("IDROID_RESEND_API_KEY", "re_key")
    monkeypatch.setattr(mail, "_send_resend", lambda *a, **k: None)

    assert mail.send_key("idroid-secret-key") == "email"
    assert "idroid-secret-key" not in capsys.readouterr().out
