import os
import tempfile
import time
import unittest

from curlgen.terminal_manager import TerminalManager


def wait_text(manager, session_id, needle, timeout=5):
    deadline = time.time() + timeout
    text, since = "", 0
    while time.time() < deadline:
        result = manager.events(session_id, since)
        since = result["next"]
        text += "".join(e.get("text", "") for e in result["events"])
        if needle in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"未等到 {needle!r}，输出为 {text!r}")


def wait_status(manager, session_id, status, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = manager.events(session_id, 0)
        if result["session"]["status"] == status:
            return result
        time.sleep(0.05)
    raise AssertionError(f"未等到状态 {status!r}")


class LocalTerminalTests(unittest.TestCase):
    def setUp(self):
        self.manager = TerminalManager()

    def tearDown(self):
        self.manager.close_all()

    def test_shell_preserves_working_directory_and_cursor(self):
        session = self.manager.open("local")
        sid = session["id"]
        with tempfile.TemporaryDirectory() as folder:
            self.manager.write(sid, f'cd /d "{folder}"' if os.name == "nt" else f'cd "{folder}"')
            self.manager.write(sid, "cd" if os.name == "nt" else "pwd")
            wait_text(self.manager, sid, os.path.basename(folder))
            self.manager.close(sid)
        first = self.manager.events(sid, 0)
        second = self.manager.events(sid, first["next"])
        self.assertEqual(second["events"], [])

    def test_unknown_session_and_close(self):
        with self.assertRaises(KeyError):
            self.manager.write("missing", "echo no")
        session = self.manager.open("local")
        self.manager.close(session["id"])
        self.manager.close(session["id"])
        result = self.manager.events(session["id"], 0)
        self.assertEqual(result["session"]["status"], "closed")
        status_events = [e for e in result["events"] if e["type"] == "status"]
        self.assertEqual([e["text"] for e in status_events].count("closed"), 1)

    def test_interrupt_closes_local_shell(self):
        session = self.manager.open("local")
        self.manager.interrupt(session["id"])
        result = wait_status(self.manager, session["id"], "closed")
        status_events = [e for e in result["events"] if e["type"] == "status"]
        self.assertEqual([e["text"] for e in status_events].count("closed"), 1)

    def test_event_buffer_is_bounded_and_reports_reset(self):
        session = self.manager.open("local")
        sid = session["id"]
        for index in range(1002):
            self.manager._emit(self.manager._sessions[sid], "stdout", str(index) * 600)
        result = self.manager.events(sid, 0)
        self.assertTrue(result["reset"])
        self.assertLessEqual(len(result["events"]), 1000)
        self.assertLessEqual(sum(len(e["text"].encode("utf-8")) for e in result["events"]), 512 * 1024)
        sequences = [e["seq"] for e in result["events"]]
        self.assertEqual(sequences, list(range(sequences[0], sequences[0] + len(sequences))))
        self.assertEqual(result["next"], sequences[-1] + 1)

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.manager.open("unknown")
        self.assertEqual(self.manager._sessions, {})

    def test_cleanup_idle_only_removes_expired_closed_sessions(self):
        active = self.manager.open("local")
        closed = self.manager.open("local")
        self.manager.close(closed["id"])
        now = time.monotonic()
        self.manager._sessions[active["id"]]["last_used"] = now - 3600
        self.manager._sessions[closed["id"]]["last_used"] = now - 3600
        self.manager.cleanup_idle()
        self.assertIn(active["id"], self.manager._sessions)
        self.assertNotIn(closed["id"], self.manager._sessions)
        with self.assertRaises(KeyError):
            self.manager.events(closed["id"])
        self.assertEqual(self.manager.events(active["id"])["session"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
