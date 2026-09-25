import argparse
import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_cloud_api import (
    AuthError,
    CodexApiError,
    DEVICE_REDIRECT_URI,
    DEVICE_TOKEN_URL,
    DEVICE_USER_CODE_URL,
    REFRESH_TOKEN_URL,
    acquire_credentials,
    async_main,
    build_parser,
    choose_store,
    device_code_login,
)


def args(credential_file: Path) -> argparse.Namespace:
    return argparse.Namespace(
        credential_file=str(credential_file),
        verbose=False,
        timeout=60.0,
    )


class CredentialStorageTests(unittest.TestCase):
    document = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "access",
            "refresh_token": "refresh",
            "account_id": "account",
        },
        "last_refresh": "2026-01-01T00:00:00Z",
    }

    def test_loads_managed_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "credentials.json"
            managed.write_text(json.dumps(self.document), encoding="utf-8")

            credentials = choose_store(args(managed))

            self.assertEqual(credentials.store.description, str(managed))
            self.assertEqual(json.loads(managed.read_text()), self.document)

    def test_missing_managed_file_requests_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(choose_store(args(Path(directory) / "missing.json")))

    def test_store_creates_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / "cloud-api" / "credentials.json"
            __import__("codex_cloud_api").AuthJsonStore(managed).save(self.document)
            if os.name == "posix":
                self.assertEqual(managed.stat().st_mode & 0o777, 0o600)
                self.assertEqual(managed.parent.stat().st_mode & 0o777, 0o700)


class FakeResponse:
    def __init__(self, status: int, value: object):
        self.status = status
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return None

    async def json(self, **unused):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class DeviceCodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.managed = self.root / "managed" / "credentials.json"
        self.id_token = jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}})

    def tearDown(self):
        self.temp.cleanup()

    def session(self, polls=(403, 404, 200), exchange_status=200):
        responses = [FakeResponse(200, {
            "device_auth_id": "device-id", "usercode": "ABCD-EFGH", "interval": "0.01"
        })]
        for status in polls:
            value = ({"authorization_code": "authorization-secret", "code_challenge": "challenge",
                      "code_verifier": "verifier-secret"} if status == 200 else {})
            responses.append(FakeResponse(status, value))
        responses.append(FakeResponse(exchange_status, {
            "id_token": self.id_token, "access_token": "access-secret", "refresh_token": "refresh-secret"
        }))
        return FakeSession(responses)

    async def test_polling_exchange_and_persistence(self):
        session = self.session()
        sleeps = []
        credentials = await device_code_login(
            __import__("codex_cloud_api").AuthJsonStore(self.managed), session=session,
            sleep=lambda delay: self._record_sleep(sleeps, delay),
        )
        self.assertEqual(sleeps, [0.01, 0.01])
        self.assertEqual(credentials.account_id, "acct-1")
        document = json.loads(self.managed.read_text())
        self.assertEqual(document["tokens"]["account_id"], "acct-1")
        self.assertEqual([call[0] for call in session.calls], [
            DEVICE_USER_CODE_URL, DEVICE_TOKEN_URL, DEVICE_TOKEN_URL, DEVICE_TOKEN_URL, REFRESH_TOKEN_URL
        ])
        form = session.calls[-1][1]["data"]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["redirect_uri"], DEVICE_REDIRECT_URI)
        self.assertTrue(form["client_id"])
        self.assertEqual(form["code"], "authorization-secret")
        self.assertEqual(form["code_verifier"], "verifier-secret")
        self.assertFalse(any(call[1].get("allow_redirects") for call in session.calls))

    async def _record_sleep(self, values, delay):
        values.append(delay)

    async def test_unexpected_poll_error_does_not_save_or_leak_secret(self):
        session = self.session(polls=(500,))
        with self.assertRaisesRegex(AuthError, "HTTP 500") as caught:
            await device_code_login(__import__("codex_cloud_api").AuthJsonStore(self.managed), session=session)
        self.assertFalse(self.managed.exists())
        self.assertNotIn("ABCD-EFGH", str(caught.exception))
        self.assertNotIn("device-id", str(caught.exception))

    async def test_timeout_does_not_save(self):
        session = FakeSession([
            FakeResponse(200, {"device_auth_id": "id", "user_code": "CODE", "interval": "1"}),
            FakeResponse(403, {}),
        ])
        times = iter((0.0, 0.0, 2.0))
        with self.assertRaisesRegex(AuthError, "timed out"):
            await device_code_login(
                __import__("codex_cloud_api").AuthJsonStore(self.managed), session=session,
                login_timeout=1, sleep=lambda unused: asyncio.sleep(0), monotonic=lambda: next(times),
            )
        self.assertFalse(self.managed.exists())

    async def test_missing_credentials_starts_device_code_login(self):
        import unittest.mock as mock
        options = args(self.root / "missing.json")
        expected = object()
        with mock.patch("codex_cloud_api.device_code_login", return_value=expected) as login:
            self.assertIs(await acquire_credentials(options), expected)
            login.assert_awaited_once()

    async def test_managed_credentials_do_not_start_device_login(self):
        import unittest.mock as mock
        managed = self.root / "existing.json"
        managed.write_text(json.dumps(CredentialStorageTests.document))
        options = args(managed)
        with mock.patch("codex_cloud_api.device_code_login") as login:
            self.assertEqual((await acquire_credentials(options)).access_token, "access")
            login.assert_not_called()


class LoginOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.managed = Path(self.temp.name) / "credentials.json"
        self.managed.write_text(json.dumps(CredentialStorageTests.document))

    def tearDown(self):
        self.temp.cleanup()

    async def test_login_only_accepts_no_request_arguments(self):
        options = build_parser().parse_args([
            "--login-only", "--credential-file", str(self.managed)
        ])

        self.assertEqual(await async_main(options), 0)

    async def test_regular_invocation_still_requires_method_and_path(self):
        options = build_parser().parse_args([])

        with self.assertRaisesRegex(CodexApiError, "METHOD and PATH are required"):
            await async_main(options)

    async def test_login_only_rejects_request_arguments(self):
        options = build_parser().parse_args(["GET", "/wham/environments", "--login-only"])

        with self.assertRaisesRegex(CodexApiError, "cannot be used with --login-only"):
            await async_main(options)


if __name__ == "__main__":
    unittest.main()
