import hashlib
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp import signing  # noqa: E402


class SigningTests(unittest.TestCase):
    def test_java_style_sort_is_case_insensitive_and_filters(self):
        text = signing.string_to_sign(
            {"b": "2", "A": "1", "empty": "", "none": None, "sign": "old"},
            "sign",
        )
        self.assertEqual(text, "A=1&b=2&")

    def test_no_params_keeps_leading_ampersand(self):
        self.assertEqual(signing.string_to_sign({}, "sign"), "&")

    def test_md5_is_upper_hex_and_matches_java_order(self):
        params = {"b": "2", "A": "1", "empty": "", "sign": "old"}
        expected = hashlib.md5(b"A=1&b=2&key=secret").hexdigest().upper()
        self.assertEqual(signing.compute_sign(params, "secret", "MD5", "sign"), expected)

    def test_sha256_accepts_lowercase_alias(self):
        params = {"a": "1"}
        expected = hashlib.sha256(b"a=1&key=k").hexdigest().upper()
        self.assertEqual(signing.compute_sign(params, "k", "sha256", "sign"), expected)

    def test_custom_sign_field_is_excluded(self):
        params = {"a": "1", "signature": "old"}
        expected = hashlib.md5(b"a=1&key=k").hexdigest().upper()
        self.assertEqual(
            signing.compute_sign(params, "k", "MD5", "signature"), expected
        )

    def test_missing_secret_returns_none(self):
        self.assertIsNone(signing.compute_sign({"a": "1"}, "", "MD5", "sign"))

    def test_unknown_algorithm_is_rejected(self):
        with self.assertRaises(ValueError):
            signing.compute_sign({"a": "1"}, "k", "SHA1", "sign")


if __name__ == "__main__":
    unittest.main()