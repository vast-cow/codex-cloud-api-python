import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_cloud_api import AuthError, choose_store


def args(codex_home: Path, credential_file: Path) -> argparse.Namespace:
    return argparse.Namespace(
        codex_home=str(codex_home),
        credential_file=str(credential_file),
        credential_source="file",
        verbose=False,
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

    def test_imports_to_independent_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            source = codex_home / "auth.json"
            source.write_text(json.dumps(self.document), encoding="utf-8")
            managed = root / "cloud-api" / "credentials.json"

            credentials = choose_store(args(codex_home, managed))

            self.assertEqual(credentials.store.description, str(managed))
            self.assertEqual(json.loads(managed.read_text()), self.document)
            self.assertEqual(json.loads(source.read_text()), self.document)
            if os.name == "posix":
                self.assertEqual(managed.stat().st_mode & 0o777, 0o600)
                self.assertEqual(managed.parent.stat().st_mode & 0o777, 0o700)

    def test_existing_managed_file_does_not_require_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "credentials.json"
            managed.write_text(json.dumps(self.document), encoding="utf-8")

            credentials = choose_store(args(root / "missing-codex", managed))

            self.assertEqual(credentials.access_token, "access")
            self.assertEqual(credentials.store.description, str(managed))

    def test_refuses_to_manage_codex_auth_file_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            source = codex_home / "auth.json"
            source.write_text(json.dumps(self.document), encoding="utf-8")

            with self.assertRaisesRegex(AuthError, "must be separate"):
                choose_store(args(codex_home, source))


if __name__ == "__main__":
    unittest.main()
