import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"CURLGEN_CONFIG_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _write(self, name, data):
        (self.root / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def test_config_dir_honors_environment_override(self):
        self.assertEqual(config.config_dir(), self.root)
        self.assertEqual(config.env_config_file(), self.root / "env_configs.json")
        self.assertEqual(config.ssh_config_file(), self.root / "ssh_config.json")

    def test_load_envs_and_find_alias(self):
        self._write(
            "env_configs.json",
            [
                {
                    "alias": "生产",
                    "enable_sign": True,
                    "key": "k",
                    "algo": "SHA256",
                    "param_name": "sign",
                    "ssh_target": "线上",
                    "custom_params": [["host", "api.example.com"]],
                }
            ],
        )
        envs = config.load_envs()
        self.assertEqual(len(envs), 1)
        self.assertEqual(config.find_environment("生产")["algo"], "SHA256")
        self.assertIsNone(config.find_environment("测试"))

    def test_default_environment_from_environment_variable(self):
        self._write("env_configs.json", [{"alias": "A"}, {"alias": "B"}])
        with mock.patch.dict(os.environ, {"CURLGEN_ENV": "B"}):
            self.assertEqual(config.default_environment()["alias"], "B")
        self.assertIsNone(config.default_environment())

    def test_public_env_never_returns_secret_values(self):
        raw = {
            "alias": "生产",
            "key": "SUPER_SECRET",
            "enable_sign": True,
            "algo": "MD5",
            "param_name": "sign",
            "ssh_target": "线上",
            "custom_params": [["token", "TOKEN_VALUE"], ["host", "api.example.com"]],
        }
        public = config.public_environment(raw)
        rendered = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("SUPER_SECRET", rendered)
        self.assertNotIn("TOKEN_VALUE", rendered)
        self.assertEqual(public["alias"], "生产")
        self.assertEqual(public["variables"], ["token", "host"])
        self.assertTrue(public["has_secret_key"])

    def test_ssh_config_normalizes_legacy_single_target(self):
        data = {
            "jump_host": "jump.example.com",
            "jump_port": "60022",
            "jump_user": "u",
            "jump_pass": "p",
            "target_host": "10.0.0.8",
            "target_port": "2222",
            "target_user": "root",
            "target_label": "旧目标",
        }
        normalized = config.normalize_ssh_config(data)
        self.assertEqual(
            normalized["targets"],
            [{"label": "旧目标", "host": "10.0.0.8", "port": "2222", "user": "root"}],
        )

    def test_resolve_target_prefers_label_then_first(self):
        data = {
            "jump_host": "jump",
            "jump_user": "u",
            "jump_pass": "p",
            "targets": [
                {"label": "A", "host": "1.1.1.1", "port": "22", "user": "root"},
                {"label": "B", "host": "2.2.2.2", "port": "2202", "user": "ops"},
            ],
        }
        self.assertEqual(config.resolve_target(data, "B")["host"], "2.2.2.2")
        self.assertEqual(config.resolve_target(data, "missing")["label"], "A")
        self.assertEqual(config.resolve_target(data, None)["label"], "A")

    def test_environment_overrides_ssh_config(self):
        self._write(
            "ssh_config.json",
            {
                "jump_host": "old",
                "jump_port": "22",
                "jump_user": "old",
                "jump_pass": "old",
                "targets": [{"label": "A", "host": "1.1.1.1", "port": "22", "user": "root"}],
            },
        )
        env = {
            "CURLGEN_JUMP_HOST": "new",
            "CURLGEN_JUMP_PORT": "60022",
            "CURLGEN_JUMP_USER": "newuser",
            "CURLGEN_JUMP_PASSWORD": "newpass",
            "CURLGEN_TARGET_HOST": "9.9.9.9",
            "CURLGEN_TARGET_PORT": "2200",
            "CURLGEN_TARGET_USER": "ops",
        }
        with mock.patch.dict(os.environ, env):
            cfg = config.load_ssh_config()
        self.assertEqual(cfg["jump_host"], "new")
        self.assertEqual(cfg["jump_port"], "60022")
        self.assertEqual(cfg["jump_user"], "newuser")
        self.assertEqual(cfg["jump_pass"], "newpass")
        self.assertEqual(cfg["targets"][0]["host"], "9.9.9.9")
        self.assertEqual(cfg["targets"][0]["port"], "2200")
        self.assertEqual(cfg["targets"][0]["user"], "ops")

    def test_public_ssh_config_masks_password(self):
        raw = {
            "jump_host": "jump",
            "jump_port": "22",
            "jump_user": "u",
            "jump_pass": "TOP_SECRET",
            "targets": [{"label": "A", "host": "1.1.1.1", "port": "22", "user": "root"}],
        }
        public = config.public_ssh_config(raw)
        self.assertNotIn("TOP_SECRET", json.dumps(public, ensure_ascii=False))
        self.assertTrue(public["jump_password_set"])

    def test_resolve_target_includes_optional_credentials(self):
        data = {
            "jump_host": "jump",
            "jump_user": "u",
            "jump_pass": "p",
            "jump_key": "/keys/jump",
            "targets": [
                {
                    "label": "A",
                    "host": "1.1.1.1",
                    "port": "22",
                    "user": "root",
                    "password": "target-pass",
                    "key": "/keys/target",
                }
            ],
        }
        resolved = config.resolve_target(data, "A")
        self.assertEqual(resolved["target_pass"], "target-pass")
        self.assertEqual(resolved["target_key"], "/keys/target")
        self.assertEqual(resolved["jump_key"], "/keys/jump")

    def test_public_ssh_config_masks_target_credentials(self):
        raw = {
            "jump_host": "jump",
            "jump_user": "u",
            "jump_pass": "jump-secret",
            "targets": [
                {
                    "label": "A",
                    "host": "1.1.1.1",
                    "port": "22",
                    "user": "root",
                    "password": "TARGET_SECRET",
                    "key": "/secret/key/path",
                }
            ],
        }
        public = config.public_ssh_config(raw)
        rendered = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("TARGET_SECRET", rendered)
        self.assertNotIn("/secret/key/path", rendered)
        self.assertTrue(public["targets"][0]["target_password_set"])
        self.assertTrue(public["targets"][0]["target_key_set"])

    def test_target_password_can_come_from_environment(self):
        data = {
            "jump_host": "jump",
            "jump_user": "u",
            "jump_pass": "p",
            "targets": [{"label": "A", "host": "1.1.1.1", "port": "22", "user": "root"}],
        }
        with mock.patch.dict(os.environ, {"CURLGEN_TARGET_PASSWORD": "env-pass"}):
            self.assertEqual(config.resolve_target(data)["target_pass"], "env-pass")


if __name__ == "__main__":
    unittest.main()