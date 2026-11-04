import aiosmtplib
from email.message import EmailMessage
import os

async def send_certificate_email(to_email: str, subject: str, html_body: str, pdf_bytes: bytes, filename: str):
    '''Asynchronously sends the email with the attached PDF.'''
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM", "no-reply@hackathon.local")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Please enable HTML to view this email.")
    msg.add_alternative(html_body, subtype='html')
    
    msg.add_attachment(
        pdf_bytes,
        maintype='application',
        subtype='pdf',
        filename=filename
    )
    
    smtp_host = os.getenv("SMTP_HOST")

    if not smtp_host:
        raise RuntimeError("SMTP_HOST environment variable is not configured.")

    smtp_port = int(os.getenv("SMTP_PORT", 587))
    
    await aiosmtplib.send(
        msg,
        hostname=smtp_host,
        port=smtp_port,
        use_tls=False
    )
