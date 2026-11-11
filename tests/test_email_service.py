import base64
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routers import batches, templates
from app.services import email_service


REAL_ASYNC_CLIENT = httpx.AsyncClient


def provider(handler):
    """Exercise HTTP serialization through an in-memory provider transport."""
    return patch.object(
        email_service.httpx,
        "AsyncClient",
        side_effect=lambda **kwargs: REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


class EmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {
            "RESEND_API_KEY": "test-key-not-a-secret",
            "EMAIL_FROM": "CertiFlow <certificates@example.com>",
        }, clear=True).start()

    async def send(self):
        return await email_service.send_certificate_email(
            "student@example.com", "Your certificate", "<h1>Congratulations</h1>",
            b"%PDF-1.7\n\x00\xff", "Ananya-certificate.pdf",
        )

    async def test_sends_html_and_lossless_pdf_attachment(self):
        requests = []

        def accept(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://api.resend.com/emails")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key-not-a-secret")
            payload = json.loads(request.content)
            self.assertEqual(payload["from"], os.environ["EMAIL_FROM"])
            self.assertEqual(payload["to"], ["student@example.com"])
            self.assertEqual(payload["subject"], "Your certificate")
            self.assertEqual(payload["html"], "<h1>Congratulations</h1>")
            attachment = payload["attachments"][0]
            self.assertEqual(attachment["filename"], "Ananya-certificate.pdf")
            self.assertEqual(base64.b64decode(attachment["content"]), b"%PDF-1.7\n\x00\xff")
            return httpx.Response(200, json={"id": "email-123"})

        with provider(accept):
            self.assertEqual(await self.send(), "email-123")
        self.assertEqual(len(requests), 1)

    async def test_missing_settings_fail_before_network(self):
        for name in ("RESEND_API_KEY", "EMAIL_FROM"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "  "}):
                with patch.object(email_service.httpx, "AsyncClient") as client:
                    with self.assertRaisesRegex(RuntimeError, name):
                        await self.send()
                    client.assert_not_called()

    async def test_provider_rejections_are_actionable_and_do_not_echo_response(self):
        for status, hint in ((401, "RESEND_API_KEY"), (403, "verification"),
                             (422, "recipient"), (429, "quota"), (500, "dashboard")):
            with self.subTest(status=status):
                with provider(lambda _: httpx.Response(status, text="sensitive-provider-details")):
                    with self.assertRaises(RuntimeError) as error:
                        await self.send()
                self.assertIn(f"HTTP {status}", str(error.exception))
                self.assertIn(hint, str(error.exception))
                self.assertNotIn("sensitive-provider-details", str(error.exception))
                self.assertNotIn("test-key-not-a-secret", str(error.exception))

    async def test_timeout_does_not_retry_an_ambiguous_send(self):
        calls = []

        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("private request details", request=request)

        with provider(timeout):
            with self.assertRaisesRegex(RuntimeError, "delivery status is unknown"):
                await self.send()
        self.assertEqual(len(calls), 1)

    async def test_connection_failure_explains_network_setting(self):
        def fail(request):
            raise httpx.ConnectError("private connection details", request=request)

        with provider(fail):
            with self.assertRaisesRegex(RuntimeError, "Allow api.resend.com"):
                await self.send()

    async def test_success_without_message_id_is_not_treated_as_sent(self):
        for body in (b"not json", b"{}", b"[]", b'{"id":null}', b'{"id":""}'):
            with self.subTest(body=body), provider(lambda _: httpx.Response(200, content=body)):
                with self.assertRaisesRegex(RuntimeError, "no message ID"):
                    await self.send()


class EmailJobTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {
            "RESEND_API_KEY": "test-key-not-a-secret",
            "EMAIL_FROM": "certificates@example.com",
        }, clear=True).start()
        # Isolate all in-memory stores and restore any previous contents.
        for module in (batches, templates):
            for name, value in vars(module).items():
                if name.isupper() and isinstance(value, dict):
                    patch.dict(value, {}, clear=True).start()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        root = Path(__file__).resolve().parents[1]
        response = self.client.post('/api/v1/templates', files={
            'file': ('template.pdf', (root / 'sample_certificate_template.pdf').read_bytes(), 'application/pdf')
        })
        self.assertEqual(response.status_code, 200)
        template_id = response.json()['id']
        response = self.client.post('/api/v1/batches', data={'name': 'Email test'}, files={
            'file': ('students.csv', b'Student Name,Student Email\nAnanya Rao,student@example.com\n', 'text/csv')
        })
        self.assertEqual(response.status_code, 200)
        self.path = '/api/v1/batches/' + response.json()['id']
        response = self.client.put(self.path + '/config', json={
            'template_id': template_id,
            'field_map': {'NAME': 'Student Name'},
            'email': {'primary_column': 'Student Email', 'subject': 'Hello {{NAME}}', 'body_html': '<p>{{NAME}}</p>'},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(self.path + '/validate').json()['valid'], 1)

    def test_send_job_generates_pdf_and_marks_provider_acceptance(self):
        def accept(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['subject'], 'Hello Ananya Rao')
            self.assertEqual(payload['html'], '<p>Ananya Rao</p>')
            self.assertTrue(base64.b64decode(payload['attachments'][0]['content']).startswith(b'%PDF'))
            return httpx.Response(200, json={'id': 'email-123'})

        with provider(accept):
            response = self.client.post(self.path + '/jobs', json={
                'action': 'generate_and_send', 'scope': 'all_valid', 'confirm_count': 1,
            })
        self.assertEqual(response.status_code, 202)
        record = self.client.get(self.path + '/records').json()['items'][0]
        self.assertEqual(record['status'], 'sent')
        self.assertEqual(record['attempts'], 1)
        self.assertIsNotNone(record['sent_at'])
        self.assertIsNone(record['error'])

    def test_rejected_email_marks_record_failed_with_error(self):
        with provider(lambda _: httpx.Response(403, json={'message': 'domain unverified'})):
            response = self.client.post(self.path + '/jobs', json={
                'action': 'send', 'scope': 'all_valid', 'confirm_count': 1,
            })
        self.assertEqual(response.status_code, 202)
        record = self.client.get(self.path + '/records').json()['items'][0]
        self.assertEqual(record['status'], 'failed')
        self.assertIsNone(record['sent_at'])
        self.assertIn('verification', record['error']['message'])
        job = batches.JOBS_DB[response.json()['id']]
        self.assertEqual(job['state'], 'completed_with_errors')
        self.assertEqual(job['failed'], 1)


class GmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from unittest.mock import MagicMock
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {
            'EMAIL_PROVIDER': 'gmail',
            'SMTP_USERNAME': 'sender@gmail.com',
            'SMTP_PASSWORD': 'dummy app password',
        }, clear=True).start()
        self.smtp = MagicMock()
        self.smtp.send_message.return_value = {}
        self.connection = patch.object(email_service.smtplib, 'SMTP').start()
        self.connection.return_value.__enter__.return_value = self.smtp

    async def send(self):
        return await email_service.send_certificate_email(
            'student@example.com', 'Your certificate', '<p>Congratulations!</p>',
            b'%PDF-1.7\n\x00\xff', 'certificate.pdf',
        )

    async def test_authenticates_after_tls_and_sends_pdf(self):
        from unittest.mock import ANY, call
        message_id = await self.send()
        self.connection.assert_called_once_with('smtp.gmail.com', 587, timeout=30)
        self.assertEqual(self.smtp.method_calls[:4], [
            call.ehlo(), call.starttls(context=ANY), call.ehlo(),
            call.login('sender@gmail.com', 'dummyapppassword'),
        ])
        context = self.smtp.starttls.call_args.kwargs['context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, email_service.ssl.CERT_REQUIRED)
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message['From'], 'CertiFlow <sender@gmail.com>')
        self.assertEqual(message['To'], 'student@example.com')
        self.assertEqual(str(message['Message-ID']), message_id)
        self.assertEqual(message.get_body(preferencelist=('html',)).get_content().strip(), '<p>Congratulations!</p>')
        attachment = next(message.iter_attachments())
        self.assertEqual(attachment.get_filename(), 'certificate.pdf')
        self.assertEqual(attachment.get_content_type(), 'application/pdf')
        self.assertEqual(attachment.get_payload(decode=True), b'%PDF-1.7\n\x00\xff')
        self.assertEqual(self.smtp.send_message.call_args.kwargs, {
            'from_addr': 'sender@gmail.com', 'to_addrs': ['student@example.com'],
        })

    async def test_missing_gmail_settings_fail_before_connection(self):
        for name in ('SMTP_USERNAME', 'SMTP_PASSWORD'):
            with self.subTest(name=name), patch.dict(os.environ, {name: ' '}):
                with self.assertRaisesRegex(RuntimeError, name):
                    await self.send()
        self.connection.assert_not_called()

    async def test_authentication_failure_is_safe_and_actionable(self):
        self.smtp.login.side_effect = email_service.smtplib.SMTPAuthenticationError(535, b'private details')
        with self.assertRaisesRegex(RuntimeError, 'Google App Password') as error:
            await self.send()
        self.assertNotIn('private details', str(error.exception))
        self.smtp.send_message.assert_not_called()

    async def test_refused_recipient_is_not_reported_as_sent(self):
        self.smtp.send_message.return_value = {'student@example.com': (550, b'rejected')}
        with self.assertRaisesRegex(RuntimeError, 'rejected the recipient'):
            await self.send()

    async def test_connection_failure_is_actionable(self):
        self.connection.side_effect = OSError('private connection details')
        with self.assertRaisesRegex(RuntimeError, 'smtp.gmail.com:587'):
            await self.send()

    async def test_ambiguous_timeout_does_not_retry(self):
        self.smtp.send_message.side_effect = TimeoutError()
        with self.assertRaisesRegex(RuntimeError, 'delivery status is unknown'):
            await self.send()
        self.smtp.send_message.assert_called_once()

    async def test_unknown_provider_fails_before_connection(self):
        with patch.dict(os.environ, {'EMAIL_PROVIDER': 'invalid'}):
            with self.assertRaisesRegex(RuntimeError, 'EMAIL_PROVIDER'):
                await self.send()
        self.connection.assert_not_called()
