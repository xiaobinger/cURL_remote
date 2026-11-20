import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp.codegen import LANGS, generate_code, strip_signature  # noqa: E402
from curlgen_mcp.request import build_request  # noqa: E402


ENV = {
    "alias": "测试",
    "enable_sign": True,
    "key": "secret",
    "algo": "MD5",
    "param_name": "sign",
    "custom_params": [],
}


def make_post(placement="auto", header="X-Sign"):
    return build_request(
        {
            "method": "POST",
            "url": "https://api.example.com/pay?fixed=1",
            "params": {"b": "2"},
            "body_type": "json",
            "body": {"a": 1, "ok": True},
            "sign": {"placement": placement, "header": header},
        },
        env_config=ENV,
    )


def make_get():
    return build_request(
        {
            "method": "GET",
            "url": "https://api.example.com/query",
            "params": {"b": "2", "a": "1"},
        },
        env_config=ENV,
    )


class CodegenTests(unittest.TestCase):
    def test_all_languages_are_supported(self):
        self.assertEqual(
            set(LANGS),
            {
                "curl",
                "python",
                "javascript",
                "typescript",
                "node",
                "java",
                "csharp",
                "go",
                "php",
                "rust",
            },
        )

    def test_curl_contains_signature_body_and_method(self):
        req = make_post()
        code = generate_code(req, "curl")["code"]
        self.assertTrue(code.startswith("curl "))
        self.assertIn("-X POST", code)
        self.assertIn("api.example.com/pay", code)
        self.assertIn(req["sign"]["value"], code)
        self.assertIn("Content-Type: application/json", code)
        self.assertIn('"a": 1', code)

    def test_static_code_embeds_signature_for_every_language(self):
        req = make_post()
        for lang in LANGS:
            with self.subTest(lang=lang):
                result = generate_code(req, lang)
                self.assertEqual(result["sign_mode"], "static")
                self.assertIn("api.example.com/pay", result["code"])
                self.assertIn(req["sign"]["value"], result["code"])

    def test_get_signature_is_in_generated_url(self):
        req = make_get()
        for lang in LANGS:
            with self.subTest(lang=lang):
                code = generate_code(req, lang)["code"]
                self.assertIn("sign=", code)
                self.assertIn(req["sign"]["value"], code)

    def test_header_placement_static_contains_header(self):
        req = make_post("header", "X-Trace-Sign")
        for lang in LANGS:
            with self.subTest(lang=lang):
                code = generate_code(req, lang)["code"]
                self.assertIn("X-Trace-Sign", code)
                self.assertIn(req["sign"]["value"], code)

    def test_python_static_is_valid_python(self):
        code = generate_code(make_post(), "python")["code"]
        self.assertIn("requests.request", code)
        compile(code, "generated_static.py", "exec")

    def test_javascript_static_passes_node_syntax_check(self):
        code = generate_code(make_post(), "javascript")["code"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated_static.js"
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_python_runtime_signing_is_valid_python_and_has_no_static_value(self):
        req = make_post()
        result = generate_code(req, "python", sign_mode="runtime")
        code = result["code"]
        self.assertEqual(result["sign_mode"], "runtime")
        self.assertIn("def compute_sign", code)
        self.assertIn("hashlib", code)
        self.assertNotIn(req["sign"]["value"], code)
        self.assertIn('SIGN_PLACEMENT = "body"', code)
        compile(code, "generated.py", "exec")

    def test_python_runtime_header_placement(self):
        req = make_post("header")
        code = generate_code(req, "python", sign_mode="runtime")["code"]
        self.assertIn("headers[SIGN_HEADER] = sign", code)
        self.assertNotIn(req["sign"]["value"], code)

    def test_python_runtime_query_placement(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "body_type": "json",
                "body": {"a": 1},
                "sign": {"placement": "query"},
            },
            env_config=ENV,
        )
        code = generate_code(req, "python", sign_mode="runtime")["code"]
        self.assertIn("SIGN_PLACEMENT = \"query\"", code)
        self.assertIn("url +=", code)
        self.assertNotIn(req["sign"]["value"], code)

    def test_javascript_runtime_passes_node_syntax_check(self):
        req = make_post()
        code = generate_code(req, "javascript", sign_mode="runtime")["code"]
        self.assertIn("crypto.createHash", code)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated.js"
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_typescript_static_is_generated(self):
        req = make_post()
        code = generate_code(req, "typescript")["code"]
        self.assertIn("const response", code)
        self.assertIn(req["sign"]["value"], code)

    @unittest.skipUnless(shutil.which("javac"), "javac not installed")
    def test_java_static_and_runtime_compile(self):
        for mode in ("static", "runtime"):
            code = generate_code(make_post(), "java", sign_mode=mode)["code"]
            with tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "Main.java"
                source.write_text(code, encoding="utf-8")
                proc = subprocess.run(
                    ["javac", "-d", temp, str(source)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertEqual(proc.returncode, 0, f"{mode}: {proc.stderr}")

    def test_java_runtime_contains_message_digest(self):
        req = make_post()
        code = generate_code(req, "java", sign_mode="runtime")["code"]
        self.assertIn("MessageDigest", code)
        self.assertIn("computeSign", code)
        self.assertNotIn(req["sign"]["value"], code)

    def test_runtime_code_reads_secret_from_environment_instead_of_embedding_it(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "body_type": "json",
                "body": {"a": 1},
                "sign": {"placement": "body", "secret": "SUPER_SECRET"},
            }
        )
        for lang in ("python", "javascript", "typescript", "node", "java"):
            result = generate_code(req, lang, sign_mode="runtime")
            self.assertEqual(result["sign_mode"], "runtime", lang)
            self.assertNotIn("SUPER_SECRET", result["code"], lang)
            self.assertIn("CURLGEN_SIGN_KEY", result["code"], lang)
            self.assertTrue(result["notes"], lang)

    def test_runtime_unsupported_language_falls_back_to_static(self):
        req = make_post()
        result = generate_code(req, "rust", sign_mode="runtime")
        self.assertEqual(result["sign_mode"], "static")
        self.assertTrue(result["notes"])
        self.assertIn(req["sign"]["value"], code_or_empty(result))

    def test_unknown_language_raises(self):
        with self.assertRaisesRegex(ValueError, "语言"):
            generate_code(make_post(), "brainfuck")

    def test_strip_signature_from_query(self):
        req = make_get()
        unsigned = strip_signature(req)
        query = parse_qs(urlparse(unsigned["url"]).query)
        self.assertNotIn("sign", query)
        self.assertEqual(query["a"], ["1"])

    def test_strip_signature_from_header(self):
        req = make_post("header")
        unsigned = strip_signature(req)
        self.assertNotIn("X-Sign", unsigned["headers"])

    def test_strip_signature_from_json_body(self):
        req = make_post()
        unsigned = strip_signature(req)
        body = json.loads(unsigned["body"])
        self.assertNotIn("sign", body)
        self.assertEqual(body["a"], 1)

    def test_form_runtime_appends_signature(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/form",
                "body_type": "form",
                "body": "a=1&b=2",
            },
            env_config=ENV,
        )
        code = generate_code(req, "python", sign_mode="runtime")["code"]
        self.assertIn('SIGN_PLACEMENT = "body"', code)
        self.assertIn("body +=", code)
        compile(code, "generated_form.py", "exec")


def code_or_empty(result):
    return result.get("code", "")


if __name__ == "__main__":
    unittest.main()