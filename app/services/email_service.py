"""Send certificate attachments through Gmail SMTP or Resend HTTPS."""

import asyncio
import base64
import os
import smtplib
import ssl
import resend
from resend.exceptions import ResendError
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
    import time

    username = os.getenv("SMTP_USERNAME", "").strip()
    password = "".join(os.getenv("SMTP_PASSWORD", "").split())

    if not username:
        raise RuntimeError(
            "SMTP_USERNAME is not configured. Set it to your Gmail address."
        )

    if not password:
        raise RuntimeError(
            "SMTP_PASSWORD is not configured. Add a Google App Password in .env."
        )

    message = EmailMessage()
    message["From"] = formataddr(("CertiFlow", username))
    message["To"] = to_email
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    message.set_content("Your certificate is attached.")
    message.add_alternative(html_body, subtype="html")
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )

    message_id = str(message["Message-ID"])
    started = time.monotonic()
    stage = "preparing message"
    smtp = None

    def step(name):
        nonlocal stage
        stage = name

        print(
            f"[Gmail {message_id}] {stage} "
            f"({time.monotonic() - started:.1f}s elapsed)",
            flush=True,
        )

    def interrupted(reason):
        if stage == "message submission":
            hint = (
                "delivery status is unknown. "
                "Check Gmail Sent before retrying."
            )
        else:
            hint = "No email was submitted."

        return RuntimeError(
            f"Gmail {reason} during {stage}. {hint}"
        )

    try:
        step("connecting to smtp.gmail.com:587 and waiting for greeting")
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)

        step("EHLO")
        smtp.ehlo()

        step("TLS negotiation")
        smtp.starttls(context=ssl.create_default_context())

        step("EHLO after TLS")
        smtp.ehlo()

        step("authentication")
        smtp.login(username, password)

        print(
            f"[Gmail {message_id}] PDF size: {len(pdf_bytes):,} bytes",
            flush=True,
        )

        step("message submission")
        smtp.sock.settimeout(120)

        refused = smtp.send_message(
            message,
            from_addr=username,
            to_addrs=[to_email],
        )
        if refused:
            raise RuntimeError(
                "Gmail rejected the recipient. Check the recipient address."
            )

        print(
            f"[Gmail {message_id}] Gmail accepted the email.",
            flush=True,
        )

    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "Gmail authentication failed. Check SMTP_USERNAME "
            "and use a Google App Password."
        ) from None

    except smtplib.SMTPRecipientsRefused:
        raise RuntimeError(
            "Gmail rejected the recipient. Check the recipient address."
        ) from None

    except smtplib.SMTPSenderRefused:
        raise RuntimeError(
            "Gmail rejected the sender. Check SMTP_USERNAME."
        ) from None

    except smtplib.SMTPDataError as exc:
        raise RuntimeError(
            "Gmail rejected the message during submission "
            f"(SMTP {exc.smtp_code})."
        ) from None

    except (TimeoutError, smtplib.SMTPServerDisconnected) as exc:
        raise interrupted(type(exc).__name__) from None

    except (smtplib.SMTPException, OSError) as exc:
        raise interrupted(type(exc).__name__) from None

    else:
        # Gmail has already accepted the email.
        # A shutdown failure must not trigger a duplicate send.
        step("connection shutdown")

        try:
            smtp.quit()
            print(
                f"[Gmail {message_id}] Session closed successfully.",
                flush=True,
            )
        except (smtplib.SMTPException, OSError) as exc:
            print(
                f"[Gmail {message_id}] Email accepted, but shutdown failed "
                f"({type(exc).__name__}). "
                "Do not resend because of this error.",
                flush=True,
            )

    finally:
        if smtp is not None:
            try:
                smtp.close()
            except OSError:
                pass

    return message_id

async def _send_resend(
    to_email,
    subject,
    html_body,
    pdf_bytes,
    filename,
) -> str:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()

    if not api_key:
        raise RuntimeError("RESEND_API_KEY is missing from .env.")

    if not sender:
        raise RuntimeError("EMAIL_FROM is missing from .env.")

    resend.api_key = api_key

    payload = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        ],
    }

    try:
        # Resend's send() is synchronous. Run it outside
        # FastAPI's event loop.
        response = await asyncio.to_thread(
            resend.Emails.send,
            payload,
        )
    except ResendError as exc:
        if exc.error_type == "HttpClientError":
            raise RuntimeError(
                "Could not reach Resend or the request timed out. "
                "Delivery status is unknown. Allow api.resend.com "
                "in network settings and check the Resend dashboard "
                "before retrying."
            ) from None

        hints = {
            "400": "Check the sender, recipient, subject, and attachment.",
            "401": "Check RESEND_API_KEY and its sending permission.",
            "403": (
                "Check API key permissions, sender verification, "
                "and test recipient restrictions."
            ),
            "413": "The PDF attachment is too large.",
            "422": "Check the sender, recipient, subject, and attachment.",
            "429": "Resend rate or quota limit reached. Wait before retrying.",
        }

        status = str(exc.code)
        hint = hints.get(
            status,
            "Check the Resend dashboard before retrying.",
        )

        raise RuntimeError(
            f"Resend email request failed (HTTP {status}). {hint}"
        ) from None

    message_id = (
        response.get("id")
        if isinstance(response, dict)
        else None
    )

    if not isinstance(message_id, str) or not message_id.strip():
        raise RuntimeError(
            "Resend returned no message ID. Check the Resend "
            "dashboard before retrying."
        )

    return message_id
