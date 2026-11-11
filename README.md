# CertiFlow
## Team Anonymous
Members -
- Archit Mishra
- Jyoti Sahu
- Anubhav Patil

## How to run
```
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Sending certificate emails from Gmail

To send from your own Gmail address, enable 2-Step Verification and create a
[Google App Password](https://myaccount.google.com/apppasswords) if your account
supports it. The App Password's label (for example, `email_sending`) is only a
label in Google; the application does not use it.

Configure these on the backend, then restart it:

| Setting | Value |
| --- | --- |
| `EMAIL_PROVIDER` | `gmail` |
| `SMTP_USERNAME` | Your full Gmail email address |
| `SMTP_PASSWORD` | A Google App Password, stored securely as a backend secret |

The sender uses `smtp.gmail.com:587`, STARTTLS with certificate verification,
and authenticated login. The sender address is the Gmail login address; a
Resend key and `EMAIL_FROM` are not needed in Gmail mode. Spaces in Google's
displayed App Password are removed automatically. Never put the password in
frontend code or commit it. Revoke any credential shared in chat and enter a
replacement securely in your hosting platform's settings. Gmail sending limits
and account policies apply.

The backend host must permit outbound **TCP port 587** to `smtp.gmail.com`.
An HTTP/HTTPS domain allowlist alone does not provide SMTP connectivity. A
proxy-backed secret intended for HTTPS header substitution also cannot
authenticate SMTP: the App Password must be available to the backend process
as a real secret environment variable. If your cloud environment only supports
HTTPS egress or proxy-backed credentials, run Gmail SMTP on a host that supports
it, or use Resend with a verified sending domain. Resend cannot send as your
personal `@gmail.com` address. Gmail API with OAuth is another option, but is not
implemented here.

Status `sent` means Gmail accepted the email, not guaranteed inbox delivery.
Check Gmail Sent and any bounce notifications before retrying ambiguous failures.
Demo mode never sends real email. The tests below mock both providers.

## Sending certificate emails with Resend

The backend sends emails through [Resend](https://resend.com) over HTTPS, with
the generated certificate attached as a PDF when `EMAIL_PROVIDER=resend` (the
default). Gmail settings are ignored in this mode.

1. Create a Resend account, verify a domain you control, and create an API key
   with sending permission. Resend's test sender can only send to the account's
   permitted test recipient; use a verified domain to email students.
2. Set `RESEND_API_KEY` as a backend secret and `EMAIL_FROM` as a backend
   environment variable (for example, `CertiFlow <certificates@your-domain.com>`).
   Replace the example with an address on your verified domain. Keep the API key
   out of `index.html`, source control, and chat. Configure these variables in
   your hosting platform too when deploying outside Codex.
3. Restart the backend after changing environment variables. Upload a template
   and CSV, configure the field mapping and email, validate, then send a single
   selected record to an address you control before sending a batch. Demo mode
   simulates sending and never sends real email.

The existing API is unchanged: `POST /api/v1/batches/{batch_id}/jobs` with action
`send` or `generate_and_send` sends the generated PDF using Resend. Failed sends
appear in each record's `error.message`. Status `sent` means Resend accepted the
message, not that it reached the inbox; inspect the Resend dashboard for bounces
and delivery events. Check that dashboard before retrying a timeout to avoid
duplicate email. Provider rate limits and quotas also apply to large batches.

### Codex cloud network settings

Open the selected environment in Codex environment settings and edit its
Internet/network access. Keep the existing package manager access and allow
these additional hostnames (without `https://` or paths):

```text
api.resend.com
cdn.tailwindcss.com
cdnjs.cloudflare.com
fonts.googleapis.com
fonts.gstatic.com
```

`api.resend.com` is required for Resend email; the others load the frontend's CSS,
spreadsheet library, and fonts. Add `RESEND_API_KEY` under secrets (destination
`api.resend.com` if requested), and `EMAIL_FROM` under environment variables.
Save the settings, restart the app, and publish the environment when available
to reuse the configuration in future tasks. Allowing domains alone does not
configure an email account or sender.

## Tests

```sh
python -m unittest discover -s tests -v
```

Email tests mock the provider; they do not send real email or need credentials.
