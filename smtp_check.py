import os
import smtplib
import ssl
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

username = os.getenv("SMTP_USERNAME", "").strip()
password = "".join(os.getenv("SMTP_PASSWORD", "").split())

smtp = None

try:
    print("1. Connecting and waiting for Gmail greeting...", flush=True)
    smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
    print("   Connected.", flush=True)

    print("2. Sending EHLO...", flush=True)
    smtp.ehlo()
    print("   EHLO completed.", flush=True)

    print("3. Starting verified TLS...", flush=True)
    smtp.starttls(context=ssl.create_default_context())
    print("   TLS established.", flush=True)

    print("4. Sending EHLO after TLS...", flush=True)
    smtp.ehlo()
    print("   EHLO completed.", flush=True)

    if not username or not password:
        raise RuntimeError(
            "SMTP_USERNAME or SMTP_PASSWORD is missing from .env."
        )

    print("5. Logging in...", flush=True)
    smtp.login(username, password)
    print("   Login successful.", flush=True)

    from email.message import EmailMessage

# Use the actual certificate PDF if you have it available.
    pdf_path = Path(__file__).resolve().parent / "sample_certificate_template.pdf"
    pdf_bytes = pdf_path.read_bytes()

    message = EmailMessage()
    message["From"] = username
    message["To"] = username
    message["Subject"] = "CertiFlow PDF test"
    message.set_content("Testing certificate delivery with a PDF attachment.")

    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )

    print(
        f"6. Sending PDF ({len(pdf_bytes):,} bytes)...",
        flush=True,
    )

    smtp.send_message(
        message,
        from_addr=username,
        to_addrs=[username],
    )

    print("   Gmail accepted the PDF email.", flush=True)

    print("7. Closing the SMTP session...", flush=True)
    smtp.quit()
    print("   Session closed successfully.", flush=True)


except Exception as exc:
    print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)

finally:
    if smtp is not None:
        smtp.close()
