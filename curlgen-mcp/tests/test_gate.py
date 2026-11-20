import unittest

from curlgen_mcp.gate import (
    _extract_body,
    parse_gate_hosts,
    strip_ansi,
    wrap_command,
)


MENU = (
    "Quit: Use q or Ctrl+C to quit\r\n"
    "001: web-01(10.0.0.1)   10.0.0.1:22   ssh   root\r\n"
    "002: db 01(10.0.0.2)    10.0.0.2:2222 ssh   admin\r\n"
    "003: tomcat01          10.0.0.3:22   ssh   ops\r\n"
    "3/90, name-asc\r\n"
)


class GateParsingTests(unittest.TestCase):
    def test_strip_ansi_removes_csi_osc_and_normalizes_crlf(self):
        raw = "\x1b[2J\x1b]0;title\x07hello\r\n\x1b[31mred\x1b[0m\r"
        self.assertEqual(strip_ansi(raw), "hello\nred\n")

    def test_parse_gate_hosts_reads_all_columns(self):
        hosts = parse_gate_hosts(MENU)
        self.assertEqual(len(hosts), 3)
        self.assertEqual(hosts[0], {
            "index": "001",
            "name": "web-01(10.0.0.1)",
            "ip": "10.0.0.1",
            "port": 22,
            "proto": "ssh",
            "user": "root",
        })
        self.assertEqual(hosts[1]["port"], 2222)
        self.assertEqual(hosts[2]["name"], "tomcat01")

    def test_parse_gate_hosts_deduplicates_repainted_rows(self):
        text = MENU + "001: web-01(10.0.0.1) 10.0.0.1:22 ssh root\r\n"
        self.assertEqual(len(parse_gate_hosts(text)), 3)

    def test_parse_gate_hosts_ignores_noise(self):
        self.assertEqual(parse_gate_hosts("Quit: Use q\nno hosts here"), [])

    def test_wrap_command_contains_start_end_and_exit_code(self):
        wrapped, start, end = wrap_command("hostname", token="abc123")
        self.assertIn(f"echo {start};", wrapped)
        self.assertIn("hostname 2>&1", wrapped)
        self.assertIn(f"echo {end}$__ec", wrapped)
        self.assertIn("__CG_S_abc123__", wrapped)
        self.assertIn("__CG_E_abc123__", wrapped)

    def test_extract_body_ignores_echoed_command_and_prompt(self):
        start, end = "__CG_S_t__", "__CG_E_t__"
        raw = (
            f"echo {start}; hostname 2>&1; __ec=$?; echo; echo {end}$__ec\r\n"
            f"{start}\r\nmy-host\r\n{end}0\r\n[root@my-host ~]# "
        ).encode()
        body = _extract_body(raw, start, end)
        self.assertEqual(body.strip(), "my-host")

    def test_extract_body_returns_empty_without_markers(self):
        self.assertEqual(_extract_body(b"just output", "S", "E"), "just output")


if __name__ == "__main__":
    unittest.main()