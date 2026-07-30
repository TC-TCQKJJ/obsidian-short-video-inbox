from __future__ import annotations

import unittest

from web.app import _resolve_doubao_api_key, app


class FakeDoubaoApiKeyStore:
    def __init__(self) -> None:
        self.value = ""

    def is_configured(self) -> bool:
        return bool(self.value)

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        value = str(value or "").strip()
        if not value:
            raise ValueError("Doubao API key must not be empty")
        self.value = value

    def clear(self) -> None:
        self.value = ""


class DoubaoSecretApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeDoubaoApiKeyStore()
        app.config.update(
            TESTING=True,
            WECHAT_CAPTURE_TOKEN="test-token",
            DOUBAO_API_KEY_STORE=self.store,
        )
        self.client = app.test_client()

    def tearDown(self) -> None:
        app.config.pop("WECHAT_CAPTURE_TOKEN", None)
        app.config.pop("DOUBAO_API_KEY_STORE", None)

    def request(self, method: str, *, json=None, authorized=True):
        headers = (
            {"X-Xiaolou-Capture-Token": "test-token"} if authorized else {}
        )
        return self.client.open(
            "/api/secrets/doubao",
            method=method,
            json=json,
            headers=headers,
        )

    def test_secret_routes_require_authentication(self) -> None:
        response = self.request("GET", authorized=False)
        self.assertEqual(response.status_code, 401)

    def test_set_status_and_clear_never_echo_the_key(self) -> None:
        response = self.request("PUT", json={"api_key": "unit-test-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "configured": True})
        self.assertNotIn("unit-test-key", response.get_data(as_text=True))

        response = self.request("GET")
        self.assertEqual(response.get_json(), {"success": True, "configured": True})

        response = self.request("DELETE")
        self.assertEqual(response.get_json(), {"success": True, "configured": False})

    def test_authenticated_requests_resolve_the_encrypted_store_key(self) -> None:
        self.store.value = "stored-test-key"
        with app.test_request_context(
            json={},
            headers={"X-Xiaolou-Capture-Token": "test-token"},
        ):
            self.assertEqual(_resolve_doubao_api_key({}), "stored-test-key")

        with app.test_request_context(json={}):
            self.assertEqual(_resolve_doubao_api_key({}), "")
