from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from script.secret_store import DoubaoApiKeyStore


class DoubaoApiKeyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "doubao-api-key.dat"
        self.store = DoubaoApiKeyStore(
            self.path,
            protect=lambda value: b"protected:" + value[::-1],
            unprotect=lambda value: value.removeprefix(b"protected:")[::-1],
            harden=lambda _path: None,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_does_not_store_plaintext(self) -> None:
        self.store.set("unit-test-key")

        self.assertTrue(self.store.is_configured())
        self.assertEqual(self.store.get(), "unit-test-key")
        self.assertNotIn(b"unit-test-key", self.path.read_bytes())

    def test_clear_removes_encrypted_file(self) -> None:
        self.store.set("unit-test-key")
        self.store.clear()

        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.is_configured())

    def test_empty_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set("   ")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
    def test_real_dpapi_round_trip(self) -> None:
        store = DoubaoApiKeyStore(self.path, harden=lambda _path: None)
        store.set("real-dpapi-test-key")

        self.assertEqual(store.get(), "real-dpapi-test-key")
        self.assertNotIn(b"real-dpapi-test-key", self.path.read_bytes())
