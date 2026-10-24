import threading
import time
import unittest

from curlgen.ssh_executor import ExecutionError, GateShellSession
from curlgen.terminal_manager import TerminalManager
from tests.test_terminal_local import wait_text


class FakeChannel:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.incoming = []

    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def recv_ready(self):
        return bool(self.incoming)

    def recv(self, size):
        return self.incoming.pop(0) if self.incoming else b""

    def close(self):
        self.closed = True


class BlockingSendChannel(FakeChannel):
    def __init__(self):
        super().__init__()
        self.send_entered = threading.Event()
        self.release_send = threading.Event()

    def send(self, data):
        self.send_entered.set()
        if not self.release_send.wait(2):
            raise RuntimeError("timed out waiting to finish fake send")
        return super().send(data)


class GateShellChannelTests(unittest.TestCase):
    def make_session(self, channel=None):
        session = GateShellSession("gate", 22, "user", "secret")
        session._chan = channel if channel is not None else FakeChannel()
        return session

    def test_send_encodes_utf8_and_recv_available_drains_ready_bytes(self):
        channel = FakeChannel()
        channel.incoming = [b"root@test\n"]
        session = self.make_session(channel)

        session.send("hostname\n")
        self.assertEqual(session.recv_available(), b"root@test\n")
        self.assertEqual(session.recv_available(), b"")
        self.assertEqual(channel.sent, ["hostname\n".encode("utf-8")])
        self.assertFalse(session.closed)

    def test_closed_channel_rejects_send_and_reads_as_eof(self):
        session = self.make_session()
        session.close()

        self.assertTrue(session.closed)
        self.assertEqual(session.recv_available(), b"")
        with self.assertRaises(ExecutionError):
            session.send("hostname")

    def test_send_and_close_are_serialized(self):
        channel = BlockingSendChannel()
        session = self.make_session(channel)
        errors = []

        def send():
            try:
                session.send("hostname")
            except Exception as exc:
                errors.append(exc)

        sender = threading.Thread(target=send)
        sender.start()
        self.assertTrue(channel.send_entered.wait(1))
        closer = threading.Thread(target=session.close)
        closer.start()
        closer.join(0.05)
        self.assertTrue(closer.is_alive())
        channel.release_send.set()
        sender.join(2)
        closer.join(2)

        self.assertFalse(sender.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(channel.sent, [b"hostname"])
        self.assertTrue(session.closed)


class FakeGate:
    instances = []

    def __init__(self, cfg, on_log=None, stop_event=None):
        self.cfg = cfg
        self.on_log = on_log
        self.stop_event = stop_event
        self.sent = []
        self.targets = []
        self.opened = False
        self.closed = False
        self.output = []
        self._lock = threading.RLock()
        type(self).instances.append(self)

    def open(self):
        self.opened = True

    def connect_host(self, target):
        self.targets.append(target)
        self.output.append(b"root@test\n")

    def send(self, text):
        with self._lock:
            if self.closed:
                raise RuntimeError("closed")
            self.sent.append(text)

    def recv_available(self):
        with self._lock:
            if self.closed:
                return b""
            return self.output.pop(0) if self.output else b""

    def close(self):
        with self._lock:
            self.closed = True


class BlockingConnectFakeGate(FakeGate):
    connect_entered = threading.Event()
    release_connect = threading.Event()

    def connect_host(self, target):
        type(self).connect_entered.set()
        if not type(self).release_connect.wait(2):
            raise RuntimeError("timed out waiting to finish fake connect")
        super().connect_host(target)


class BlockingSendFakeGate(FakeGate):
    send_entered = threading.Event()
    release_send = threading.Event()

    def send(self, text):
        with self._lock:
            type(self).send_entered.set()
            if not type(self).release_send.wait(2):
                raise RuntimeError("timed out waiting to finish fake send")
            super().send(text)


class ClosingReaderFakeGate(FakeGate):
    def recv_available(self):
        with self._lock:
            if self.output:
                return self.output.pop(0)
            self.closed = True
            return b""


class FailingReaderFakeGate(FakeGate):
    def recv_available(self):
        raise RuntimeError("read failed: " + self.cfg["jump_pass"])


class SSHTerminalTests(unittest.TestCase):
    def setUp(self):
        self.manager = None
        for fake in (FakeGate, BlockingConnectFakeGate, BlockingSendFakeGate):
            fake.instances = []
        BlockingConnectFakeGate.connect_entered = threading.Event()
        BlockingConnectFakeGate.release_connect = threading.Event()
        BlockingSendFakeGate.send_entered = threading.Event()
        BlockingSendFakeGate.release_send = threading.Event()

    def tearDown(self):
        BlockingConnectFakeGate.release_connect.set()
        BlockingSendFakeGate.release_send.set()
        if self.manager is not None:
            self.manager.close_all()

    def make_manager(self, factory=FakeGate, cfg=None):
        if cfg is None:
            cfg = {
                "jump_host": "gate",
                "jump_port": "2222",
                "jump_user": "user",
                "jump_pass": "secret",
                "targets": [
                    {"label": "生产A", "host": "10.0.0.8", "port": "22", "user": "root"},
                    {"label": "生产B", "host": "10.0.0.9", "port": "22", "user": "root"},
                ],
            }
        self.manager = TerminalManager(
            session_factory=factory,
            ssh_config_loader=lambda: cfg,
        )
        return self.manager

    def test_connects_exact_target_and_never_emits_backend_config_or_password(self):
        manager = self.make_manager()
        session = manager.open("ssh", "生产B")
        sid = session["id"]
        wait_text(manager, sid, "root@test")
        manager.write(sid, "hostname")

        gate = FakeGate.instances[0]
        self.assertEqual(gate.targets, ["10.0.0.9"])
        self.assertEqual(gate.sent, ["hostname"])
        self.assertEqual(gate.cfg["jump_pass"], "secret")
        events = str(manager.events(sid, 0))
        self.assertNotIn("secret", events)
        self.assertNotIn("jump_pass", events)
        self.assertNotIn("jump_host", events)

    def test_rejects_unknown_or_missing_label_without_constructing_backend(self):
        for label in ("不存在", None):
            with self.subTest(label=label):
                FakeGate.instances = []
                manager = self.make_manager()
                session = manager.open("ssh", label)
                wait_text(manager, session["id"], "error: 目标机配置不存在")
                events = manager.events(session["id"], 0)["events"]
                self.assertIn(("status", "error"), [(e["type"], e["text"]) for e in events])
                self.assertEqual(FakeGate.instances, [])

    def test_close_during_config_load_does_not_emit_late_error(self):
        manager = self.make_manager()
        loader_entered = threading.Event()
        release_loader = threading.Event()
        original_loader = manager._ssh_config_loader

        def controlled_loader():
            loader_entered.set()
            if not release_loader.wait(2):
                raise RuntimeError("timed out waiting to finish fake config load")
            return original_loader()

        manager._ssh_config_loader = controlled_loader
        session = manager.open("ssh", "不存在")
        sid = session["id"]
        self.assertTrue(loader_entered.wait(1))
        manager.close(sid)
        release_loader.set()
        for reader in manager._sessions[sid]["readers"]:
            reader.join(2)
            self.assertFalse(reader.is_alive())

        events = manager.events(sid, 0)["events"]
        descriptions = [(event["type"], event["text"]) for event in events]
        self.assertNotIn(("status", "error"), descriptions)
        self.assertEqual(descriptions, [("status", "closed")])

    def test_close_during_target_connect_cleans_up_without_ready_event(self):
        manager = self.make_manager(factory=BlockingConnectFakeGate)
        session = manager.open("ssh", "生产A")
        sid = session["id"]
        self.assertTrue(BlockingConnectFakeGate.connect_entered.wait(1))

        closer = threading.Thread(target=manager.close, args=(sid,))
        closer.start()
        closer.join(1)
        self.assertFalse(closer.is_alive())
        self.assertEqual(manager.events(sid, 0)["session"]["status"], "closed")
        BlockingConnectFakeGate.release_connect.set()
        for reader in manager._sessions[sid]["readers"]:
            reader.join(2)
            self.assertFalse(reader.is_alive())

        gate = BlockingConnectFakeGate.instances[0]
        self.assertTrue(gate.closed)
        events = manager.events(sid, 0)["events"]
        descriptions = [(event["type"], event["text"]) for event in events]
        self.assertNotIn(("status", "ready"), descriptions)
        self.assertEqual(descriptions.count(("status", "closed")), 1)

    def test_remote_reader_exit_preserves_tail_then_closes_once(self):
        manager = self.make_manager(factory=ClosingReaderFakeGate)
        session = manager.open("ssh", "生产A")
        sid = session["id"]
        wait_text(manager, sid, "root@test")
        deadline = time.time() + 2
        while manager.events(sid, 0)["session"]["status"] != "closed":
            if time.time() >= deadline:
                self.fail("SSH reader did not finish after remote close")
            time.sleep(0.02)

        events = manager.events(sid, 0)["events"]
        descriptions = [(event["type"], event["text"]) for event in events]
        self.assertLess(descriptions.index(("stdout", "root@test\n")), descriptions.index(("status", "closed")))
        self.assertEqual(descriptions.count(("status", "closed")), 1)
        self.assertTrue(ClosingReaderFakeGate.instances[0].closed)

    def test_reader_error_is_sanitized_and_does_not_leak_password(self):
        manager = self.make_manager(factory=FailingReaderFakeGate)
        session = manager.open("ssh", "生产A")
        sid = session["id"]
        wait_text(manager, sid, "read failed:")
        events = str(manager.events(sid, 0))

        self.assertIn("read failed:", events)
        self.assertNotIn("secret", events)
        self.assertNotIn("jump_pass", events)
        self.assertIn(("status", "error"), [
            (event["type"], event["text"])
            for event in manager.events(sid, 0)["events"]
        ])

    def test_manager_send_and_close_race_does_not_deadlock_or_duplicate_close(self):
        manager = self.make_manager(factory=BlockingSendFakeGate)
        session = manager.open("ssh", "生产A")
        sid = session["id"]
        wait_text(manager, sid, "root@test")
        errors = []

        def write():
            try:
                manager.write(sid, "hostname")
            except Exception as exc:
                errors.append(exc)

        sender = threading.Thread(target=write)
        sender.start()
        self.assertTrue(BlockingSendFakeGate.send_entered.wait(1))
        closer = threading.Thread(target=manager.close, args=(sid,))
        closer.start()
        closer.join(0.05)
        self.assertTrue(closer.is_alive())
        BlockingSendFakeGate.release_send.set()
        sender.join(2)
        closer.join(2)

        self.assertFalse(sender.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(BlockingSendFakeGate.instances[0].sent, ["hostname"])
        events = manager.events(sid, 0)["events"]
        descriptions = [(event["type"], event["text"]) for event in events]
        self.assertEqual(descriptions.count(("status", "closed")), 1)
        self.assertEqual(descriptions[-1], ("status", "closed"))


if __name__ == "__main__":
    unittest.main()
