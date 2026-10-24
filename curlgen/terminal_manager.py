import codecs
import locale
import os
import subprocess
import threading
import time
import uuid


class TerminalManager:
    MAX_EVENTS = 1000
    MAX_EVENT_BYTES = 512 * 1024

    def __init__(self, session_factory=None, ssh_config_loader=None):
        self._sessions = {}
        self._lock = threading.RLock()
        self._session_factory = session_factory
        self._ssh_config_loader = ssh_config_loader

    def open(self, kind="local", label=None):
        self.cleanup_idle()
        if kind not in ("local", "ssh"):
            raise ValueError("unsupported terminal kind")
        sid = uuid.uuid4().hex
        session = {
            "id": sid,
            "kind": kind,
            "label": label or "",
            "status": "connecting",
            "events": [],
            "event_bytes": 0,
            "next": 1,
            "process": None,
            "adapter": None,
            "encoding": self._local_encoding(),
            "closed": False,
            "last_used": time.monotonic(),
        }
        with self._lock:
            self._sessions[sid] = session
        if kind == "local":
            self._start_local(session)
        else:
            threading.Thread(target=self._start_ssh, args=(session,), daemon=True).start()
        return self._info(session)

    def write(self, session_id, text):
        with self._lock:
            session = self._sessions[session_id]
            session["last_used"] = time.monotonic()
            if session["closed"]:
                raise RuntimeError("terminal session is closed")
            process = session["process"]
            adapter = session["adapter"]
        if process is not None:
            if not text.endswith(("\n", "\r")):
                text += "\n"
            data = text.encode(session["encoding"], errors="replace")
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise RuntimeError("terminal input is unavailable") from exc
        elif adapter is not None:
            adapter.write(text)
        else:
            raise RuntimeError("terminal input is unavailable")

    def events(self, session_id, since=0):
        self.cleanup_idle()
        with self._lock:
            session = self._sessions[session_id]
            session["last_used"] = time.monotonic()
            retained = session["events"]
            selected = [dict(event) for event in retained if event["seq"] > since]
            result = {
                "session": self._info(session),
                "events": selected,
                "next": session["next"],
            }
            if retained and since < retained[0]["seq"] - 1:
                result["reset"] = True
            return result

    def interrupt(self, session_id):
        with self._lock:
            session = self._sessions[session_id]
            if session["closed"]:
                return
            process = session["process"]
            adapter = session["adapter"]
        if process is not None:
            self._terminate_process(process)
        elif adapter is not None:
            adapter.write(b"\x03")

    def close(self, session_id):
        with self._lock:
            session = self._sessions[session_id]
            process = session["process"]
            adapter = session["adapter"]
            self._finish(session)
        if process is not None:
            self._terminate_process(process)
            self._close_stream(process.stdin)
        if adapter is not None:
            try:
                adapter.close()
            except (OSError, ValueError):
                pass

    def cleanup_idle(self, max_age=1800):
        now = time.monotonic()
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items()
                if session["closed"] and now - session["last_used"] > max_age
            ]
            for sid in expired:
                self._sessions.pop(sid, None)

    def close_all(self):
        with self._lock:
            session_ids = list(self._sessions)
        for session_id in session_ids:
            self.close(session_id)

    def _start_local(self, session):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/Q"] if os.name == "nt" else ["/bin/sh"]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            self._emit(session, "status", "error")
            self._emit(session, "stderr", str(exc))
            self._finish(session)
            return
        with self._lock:
            if session["closed"]:
                self._terminate_process(process)
                return
            session["process"] = process
            session["status"] = "ready"
            self._emit(session, "status", "ready")
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name)
            threading.Thread(
                target=self._read_local,
                args=(session, stream, stream_name),
                daemon=True,
            ).start()
        threading.Thread(target=self._wait_local, args=(session, process), daemon=True).start()

    def _start_ssh(self, session):
        self._emit(session, "status", "error")
        self._emit(session, "stderr", "SSH sessions are not implemented")
        self._finish(session)

    def _read_local(self, session, stream, stream_name):
        decoder = codecs.getincrementaldecoder(session["encoding"])(errors="replace")
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
                text = decoder.decode(data)
                if text:
                    self._emit(session, stream_name, text)
            text = decoder.decode(b"", final=True)
            if text:
                self._emit(session, stream_name, text)
        except (OSError, ValueError):
            pass
        finally:
            self._close_stream(stream)

    def _wait_local(self, session, process):
        try:
            process.wait()
        finally:
            self._finish(session)
            self._close_stream(process.stdin)

    def _emit(self, session, event_type, text):
        encoded = text.encode("utf-8")
        if len(encoded) > self.MAX_EVENT_BYTES:
            encoded = encoded[-self.MAX_EVENT_BYTES:]
            text = encoded.decode("utf-8", errors="ignore")
            encoded = text.encode("utf-8")
        with self._lock:
            event = {"seq": session["next"], "type": event_type, "text": text}
            session["next"] += 1
            session["events"].append(event)
            session["event_bytes"] += len(encoded)
            while (
                len(session["events"]) > self.MAX_EVENTS
                or session["event_bytes"] > self.MAX_EVENT_BYTES
            ):
                removed = session["events"].pop(0)
                session["event_bytes"] -= len(removed["text"].encode("utf-8"))

    def _finish(self, session):
        with self._lock:
            if session["closed"]:
                return
            session["closed"] = True
            session["status"] = "closed"
            session["last_used"] = time.monotonic()
            self._emit(session, "status", "closed")

    def _info(self, session):
        return {
            "id": session["id"],
            "kind": session["kind"],
            "label": session["label"],
            "status": session["status"],
        }

    @staticmethod
    def _local_encoding():
        return locale.getpreferredencoding(False) if os.name == "nt" else "utf-8"

    @staticmethod
    def _close_stream(stream):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _terminate_process(process):
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            else:
                process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
