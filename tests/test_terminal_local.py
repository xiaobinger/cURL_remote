import io
import os
import signal
import tempfile
import threading
import time
import unittest
from unittest import mock

from curlgen.terminal_manager import TerminalManager


class FakeProcess:
    def __init__(self, stdin=None, stdout=None, stderr=None, pid=4321):
        self.pid = pid
        self.stdin = stdin or io.BytesIO()
        self.stdout = stdout or io.BytesIO()
        self.stderr = stderr or io.BytesIO()

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


class PartialConcurrentStdin:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = 0
        self._blocked_once = False
        self.chunks = []
        self.max_active = 0
        self.first_entered = threading.Event()
        self.concurrent_entered = threading.Event()
        self.release_first = threading.Event()

    def write(self, data):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            should_block = not self._blocked_once
            self._blocked_once = True
            if self._active == 1:
                self.first_entered.set()
            else:
                self.concurrent_entered.set()
        if should_block:
            self.release_first.wait(2)
        written = min(7, len(data))
        with self._lock:
            self.chunks.append(bytes(data[:written]))
            self._active -= 1
        return written

    def flush(self):
        pass

    def close(self):
        pass


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

    def make_fake_session(self, process):
        sid = "controlled-session"
        session = {
            "id": sid,
            "kind": "local",
            "label": "",
            "status": "ready",
            "events": [],
            "event_bytes": 0,
            "next": 1,
            "process": process,
            "adapter": None,
            "encoding": "utf-8",
            "closed": False,
            "closing": False,
            "last_used": time.monotonic(),
            "write_lock": threading.Lock(),
            "readers": [],
        }
        self.manager._sessions[sid] = session
        return session

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

    def assert_tail_output_precedes_closed(self, explicit_close):
        process = FakeProcess()
        session = self.make_fake_session(process)
        release_reader = threading.Event()

        def emit_tail():
            release_reader.wait(2)
            self.manager._emit(session, "stdout", "tail-output")

        reader = threading.Thread(target=emit_tail)
        session["readers"] = [reader]
        reader.start()
        if explicit_close:
            with mock.patch.object(self.manager, "_terminate_process", return_value=None):
                finalizer = threading.Thread(target=self.manager.close, args=(session["id"],))
                finalizer.start()
                finalizer.join(0.1)
                release_reader.set()
                finalizer.join(2)
        else:
            finalizer = threading.Thread(target=self.manager._wait_local, args=(session, process))
            finalizer.start()
            finalizer.join(0.1)
            release_reader.set()
            finalizer.join(2)
        reader.join(2)
        self.assertFalse(finalizer.is_alive())
        self.assertFalse(reader.is_alive())
        events = self.manager.events(session["id"], 0)["events"]
        descriptions = [(event["type"], event["text"]) for event in events]
        self.assertIn(("stdout", "tail-output"), descriptions)
        self.assertEqual(descriptions[-1], ("status", "closed"))
        self.assertEqual(descriptions.count(("status", "closed")), 1)

    def test_natural_exit_drains_tail_output_before_closed(self):
        self.assert_tail_output_precedes_closed(explicit_close=False)

    def test_explicit_close_drains_tail_output_before_closed(self):
        self.assert_tail_output_precedes_closed(explicit_close=True)

    def test_posix_shell_starts_in_its_own_process_group(self):
        process = FakeProcess()
        with mock.patch("curlgen.terminal_manager.os.name", "posix"), mock.patch(
            "curlgen.terminal_manager.subprocess.Popen", return_value=process
        ) as popen:
            session = self.manager.open("local")
            wait_status(self.manager, session["id"], "closed")
        self.assertIs(popen.call_args.kwargs.get("start_new_session"), True)

    def test_posix_termination_signals_the_process_group(self):
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = None
        process.wait.return_value = 0
        with mock.patch("curlgen.terminal_manager.os.name", "posix"), mock.patch(
            "curlgen.terminal_manager.os.killpg", create=True
        ) as killpg:
            self.manager._terminate_process(process)
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        process.terminate.assert_not_called()

    def test_close_all_skips_session_removed_by_idle_cleanup(self):
        expired = self.manager.open("local")
        active = self.manager.open("local")
        self.manager.close(expired["id"])
        self.manager._sessions[expired["id"]]["last_used"] = time.monotonic() - 3600
        original_close = self.manager.close
        close_entered = threading.Event()
        continue_close = threading.Event()
        errors = []

        def controlled_close(session_id):
            if session_id == expired["id"]:
                close_entered.set()
                continue_close.wait(2)
            return original_close(session_id)

        def run_close_all():
            try:
                self.manager.close_all()
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(self.manager, "close", side_effect=controlled_close):
            thread = threading.Thread(target=run_close_all)
            thread.start()
            self.assertTrue(close_entered.wait(1))
            self.manager.cleanup_idle()
            continue_close.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            self.manager.events(active["id"])["session"]["status"], "closed"
        )

    def test_concurrent_writes_are_serialized_and_written_completely(self):
        stream = PartialConcurrentStdin()
        process = FakeProcess(stdin=stream)
        session = self.make_fake_session(process)
        errors = []
        first_text = "A" * 16384
        second_text = "B" * 16384

        def write(text):
            try:
                self.manager.write(session["id"], text)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=write, args=(first_text,))
        second = threading.Thread(target=write, args=(second_text,))
        first.start()
        self.assertTrue(stream.first_entered.wait(1))
        second.start()
        stream.concurrent_entered.wait(0.1)
        stream.release_first.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        with mock.patch.object(self.manager, "_terminate_process", return_value=None):
            self.manager.close(session["id"])
        self.assertEqual(errors, [])
        self.assertEqual(stream.max_active, 1)
        self.assertEqual(
            b"".join(stream.chunks),
            (first_text + "\n" + second_text + "\n").encode("utf-8"),
        )

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
