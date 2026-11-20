import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp.request import build_request  # noqa: E402


ENV = {
    "alias": "测试",
    "enable_sign": True,
    "key": "secret",
    "algo": "MD5",
    "param_name": "sign",
    "custom_params": [["host", "api.example.com"], ["appKey", "AK1"]],
}


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


class RequestTests(unittest.TestCase):
    def test_get_auto_places_signature_in_query(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://{{host}}/pay",
                "params": {"b": "2", "a": "1"},
            },
            env_config=ENV,
        )
        parsed = urlparse(req["url"])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "api.example.com")
        self.assertEqual(query["a"], ["1"])
        expected = md5("a=1&b=2&key=secret")
        self.assertEqual(query["sign"], [expected])
        self.assertEqual(req["sign"]["value"], expected)
        self.assertEqual(req["sign"]["placement"], "query")
        self.assertIsNone(req["body"])

    def test_custom_sign_field_name_and_sorting(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "params": {"B": "2", "a": "1"},
                "sign": {"field": "signature"},
            },
            env_config=ENV,
        )
        query = parse_qs(urlparse(req["url"]).query)
        expected = md5("a=1&B=2&key=secret")
        self.assertEqual(query["signature"], [expected])

    def test_post_json_auto_injects_signature_into_body(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "params": {"agent": "A"},
                "body_type": "json",
                "body": {"amount": 100, "ok": True, "nested": {"x": 1}},
            },
            env_config=ENV,
        )
        body = json.loads(req["body"])
        expected = md5(
            'agent=A&amount=100&nested={"x":1}&ok=true&key=secret'
        )
        self.assertEqual(body["sign"], expected)
        self.assertEqual(req["headers"]["Content-Type"], "application/json")
        self.assertEqual(req["sign"]["placement"], "body")

    def test_header_placement_keeps_body_untouched(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "params": {"a": "1"},
                "body_type": "json",
                "body": {"b": 2},
                "sign": {"placement": "header", "header": "X-Sign"},
            },
            env_config=ENV,
        )
        expected = md5("a=1&b=2&key=secret")
        self.assertEqual(req["headers"]["X-Sign"], expected)
        self.assertNotIn("sign", json.loads(req["body"]))
        self.assertNotIn("sign", req["url"])

    def test_query_placement_on_post(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "body_type": "json",
                "body": {"b": 2},
                "sign": {"placement": "query"},
            },
            env_config=ENV,
        )
        query = parse_qs(urlparse(req["url"]).query)
        self.assertEqual(query["sign"], [md5("b=2&key=secret")])
        self.assertNotIn("sign", json.loads(req["body"]))

    def test_form_body_appends_signature(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/form",
                "body_type": "form",
                "body": "b=2\na=1",
            },
            env_config=ENV,
        )
        self.assertEqual(req["body"], "b=2&a=1&sign=" + md5("a=1&b=2&key=secret"))
        self.assertEqual(
            req["headers"]["Content-Type"], "application/x-www-form-urlencoded"
        )

    def test_raw_body_appends_signature(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/raw",
                "body_type": "raw",
                "body": "a=1&b=2",
            },
            env_config=ENV,
        )
        self.assertEqual(req["body"], "a=1&b=2&sign=" + md5("a=1&b=2&key=secret"))

    def test_empty_body_with_body_placement_creates_json(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/x",
                "body_type": "none",
            },
            env_config=ENV,
        )
        expected = md5("&key=secret")
        self.assertEqual(json.loads(req["body"]), {"sign": expected})
        self.assertEqual(req["body_type"], "json")

    def test_disabled_sign_produces_no_signature(self):
        disabled = dict(ENV, enable_sign=False)
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/x",
                "body_type": "json",
                "body": {"a": 1},
            },
            env_config=disabled,
        )
        self.assertFalse(req["sign"]["enabled"])
        self.assertNotIn("sign", json.loads(req["body"]))
        self.assertNotIn("X-Sign", req["headers"])

    def test_placement_none_overrides_enabled_environment(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "params": {"a": "1"},
                "sign": {"placement": "none"},
            },
            env_config=ENV,
        )
        self.assertFalse(req["sign"]["enabled"])
        self.assertNotIn("sign", parse_qs(urlparse(req["url"]).query))

    def test_explicit_secret_and_algorithm_override_environment(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "sign": {"secret": "other", "algo": "SHA256", "enabled": True},
            },
            env_config=ENV,
        )
        expected = hashlib.sha256(b"&key=other").hexdigest().upper()
        self.assertEqual(req["sign"]["value"], expected)

    def test_missing_secret_raises_when_signing_enabled(self):
        env = dict(ENV, key="")
        with self.assertRaisesRegex(ValueError, "密钥"):
            build_request(
                {"method": "GET", "url": "https://api.example.com/x"},
                env_config=env,
            )

    def test_invalid_json_body_with_body_placement_raises(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            build_request(
                {
                    "method": "POST",
                    "url": "https://api.example.com/x",
                    "body_type": "json",
                    "body": "{not json",
                },
                env_config=ENV,
            )

    def test_bearer_and_api_key_auth(self):
        bearer = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "auth": {"type": "bearer", "token": "T"},
                "sign": {"enabled": False},
            }
        )
        self.assertEqual(bearer["headers"]["Authorization"], "Bearer T")
        api_key = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "auth": {"type": "api_key", "token": "T", "header": "X-Key"},
                "sign": {"enabled": False},
            }
        )
        self.assertEqual(api_key["headers"]["X-Key"], "T")

    def test_basic_auth_is_base64(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "auth": {"type": "basic", "username": "u", "password": "p"},
                "sign": {"enabled": False},
            }
        )
        expected = base64.b64encode(b"u:p").decode()
        self.assertEqual(req["headers"]["Authorization"], "Basic " + expected)

    def test_environment_variables_replace_in_headers_params_and_body(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://{{host}}/x?fixed={{appKey}}",
                "headers": {"X-App": "{{appKey}}"},
                "params": {"k": "{{appKey}}"},
                "body_type": "json",
                "body": {"app": "{{appKey}}"},
                "sign": {"enabled": False},
            },
            env_config=ENV,
        )
        self.assertIn("api.example.com", req["url"])
        self.assertEqual(req["headers"]["X-App"], "AK1")
        self.assertEqual(parse_qs(urlparse(req["url"]).query)["k"], ["AK1"])
        self.assertEqual(json.loads(req["body"])["app"], "AK1")

    def test_unknown_environment_raises(self):
        with self.assertRaisesRegex(ValueError, "环境"):
            build_request(
                {
                    "method": "GET",
                    "url": "https://api.example.com/x",
                    "environment": "不存在",
                }
            )

    def test_params_are_url_encoded_for_transport(self):
        req = build_request(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "params": {"q": "a b", "x": "1&2"},
                "sign": {"enabled": False},
            }
        )
        self.assertIn("q=a%20b", req["url"])
        self.assertIn("x=1%262", req["url"])

    def test_user_content_type_is_preserved(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/x",
                "headers": {"content-type": "application/vnd.api+json"},
                "body_type": "json",
                "body": {"a": 1},
                "sign": {"enabled": False},
            }
        )
        self.assertEqual(req["headers"]["content-type"], "application/vnd.api+json")
        self.assertNotIn("Content-Type", req["headers"])


if __name__ == "__main__":
    unittest.main()