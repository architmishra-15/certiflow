"""Send certificate attachments through Gmail SMTP or Resend HTTPS."""

import asyncio
import base64
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx


async def send_certificate_email(
    to_email: str,
    subject: str,
    html_body: str,
    pdf_bytes: bytes,
    filename: str,
) -> str:
    provider = os.getenv("EMAIL_PROVIDER", "resend").strip().lower()
    if provider == "gmail":
        # smtplib is blocking; keep network I/O off FastAPI's event loop.
        return await asyncio.to_thread(
            _send_gmail, to_email, subject, html_body, pdf_bytes, filename
        )
    if provider != "resend":
        raise RuntimeError("EMAIL_PROVIDER must be gmail or resend.")
    return await _send_resend(to_email, subject, html_body, pdf_bytes, filename)


def _send_gmail(to_email, subject, html_body, pdf_bytes, filename) -> str:
    username = os.getenv("SMTP_USERNAME", "").strip()
    # Google displays App Passwords in groups separated by spaces.
    password = "".join(os.getenv("SMTP_PASSWORD", "").split())
    if not username:
        raise RuntimeError("SMTP_USERNAME is not configured. Set it to your Gmail address.")
    if not password:
        raise RuntimeError("SMTP_PASSWORD is not configured. Add a Google App Password in backend secrets.")

    message = EmailMessage()
    message["From"] = formataddr(("CertiFlow", username))
    message["To"] = to_email
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    message.set_content("Your certificate is attached. View this email in an HTML-capable client for the full message.")
    message.add_alternative(html_body, subtype="html")
    message.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(username, password)
            refused = smtp.send_message(message, from_addr=username, to_addrs=[to_email])
            if refused:
                raise RuntimeError("Gmail rejected the recipient. Check the recipient address.")
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "Gmail authentication failed. Check SMTP_USERNAME and use a Google App Password, "
            "not your Google account password."
        ) from None
    except smtplib.SMTPRecipientsRefused:
        raise RuntimeError("Gmail rejected the recipient. Check the recipient address.") from None
    except smtplib.SMTPSenderRefused:
        raise RuntimeError("Gmail rejected the sender. Check SMTP_USERNAME and account sending restrictions.") from None
    except (TimeoutError, smtplib.SMTPServerDisconnected):
        raise RuntimeError(
            "Gmail connection interrupted; delivery status is unknown. Check Gmail Sent before retrying."
        ) from None
    except (smtplib.SMTPException, OSError):
        raise RuntimeError(
            "Gmail sending failed. Check SMTP access to smtp.gmail.com:587 and Gmail sending limits. "
            "Check Gmail Sent before retrying."
        ) from None
    return str(message["Message-ID"])


async def _send_resend(to_email, subject, html_body, pdf_bytes, filename) -> str:
    """Return the message ID once Resend accepts the email.

    Acceptance does not confirm inbox delivery. Do not automatically retry an
    ambiguous timeout: the provider may already have accepted the email.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not configured. Add it in environment secrets.")
    if not sender:
        raise RuntimeError("EMAIL_FROM is not configured. Use a Resend-verified sender address.")

    payload = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "attachments": [{
            "filename": filename,
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.TimeoutException:
        raise RuntimeError(
            "Resend request timed out; delivery status is unknown. "
            "Check the Resend dashboard before retrying."
        ) from None
    except httpx.RequestError:
        raise RuntimeError(
            "Could not reach Resend. Allow api.resend.com in network settings. "
            "Check the Resend dashboard before retrying."
        ) from None

    # Do not expose raw provider responses or request headers to the UI.
    if not response.is_success:
        hints = {
            400: "Check the sender, recipient, subject, and attachment in the Resend dashboard.",
            401: "Check RESEND_API_KEY and its sending permission.",
            403: "Check API key permissions, sender domain verification, and test recipient restrictions.",
            413: "The certificate attachment is too large.",
            422: "Check the sender, recipient, subject, and attachment in the Resend dashboard.",
            429: "Resend rate or quota limit reached. Wait before retrying.",
        }
        hint = hints.get(response.status_code, "Check the Resend dashboard before retrying.")
        raise RuntimeError(f"Resend email request failed (HTTP {response.status_code}). {hint}")

    try:
        message_id = response.json().get("id")
    except (ValueError, AttributeError):
        message_id = None
    if not isinstance(message_id, str) or not message_id.strip():
        raise RuntimeError(
            "Resend returned no message ID; delivery status is unknown. "
            "Check the Resend dashboard before retrying."
        )
    return message_id
