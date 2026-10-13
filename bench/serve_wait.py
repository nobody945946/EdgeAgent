"""Wait for a llama-server to say it is listening; never ask it at an interval.

Once the model is loaded, llama-server prints

    0.02.486.788 I srv  llama_server: model loaded
    0.02.486.790 I srv  llama_server: listening on http://127.0.0.1:18189

(older builds: "main: server is listening on http://..."). That line is the
readiness; the output ending first means the server exited while loading,
and its last lines say why. The bench does not ask /health (the owner,
2026-10-14: nothing asks at a fixed interval); the one deadline is for a load
that hangs.

The server is a child with its output on a pipe; a thread reads it line by
line (blocking reads), appends each line to the server's log, and keeps
reading after the server is ready, so the pipe never fills.
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from pathlib import Path

LISTENING = b"listening on http://"
LAST_LINES = 20


class NotListening(SystemExit):
    """The server exited while loading, or was not listening by the deadline
    (then it was stopped). A SystemExit: a tool that does not catch it stops
    with the reason."""

    def __init__(self, message: str, exited: bool) -> None:
        super().__init__(message)
        self.exited = exited


class LlamaServer:
    """llama-server as a child process, its output appended to `log`; back
    from the constructor once it is listening (else NotListening)."""

    def __init__(self, args: list[str], log: Path, timeout: float) -> None:
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
        self._listening = False
        self._settled = threading.Event()  # listening, or the output ended
        self._last: deque[bytes] = deque(maxlen=LAST_LINES)
        self._reader = threading.Thread(target=self._follow, args=(log,), daemon=True)
        self._reader.start()
        self._settled.wait(timeout)
        if self._listening:
            return
        exited = self._settled.is_set()  # the output ended: the server is going
        self.stop()
        why = (f"exited ({self.process.returncode}) before it was listening" if exited
               else f"did not become ready in {timeout:.0f} s (stopped it)")
        last = b"".join(self._last).decode("utf-8", "replace").rstrip()
        raise NotListening(f"llama-server {why}; its last lines ({log}):\n{last}", exited)

    def _follow(self, log: Path) -> None:
        assert self.process.stdout is not None
        try:
            with log.open("ab") as sink:
                for line in iter(self.process.stdout.readline, b""):
                    sink.write(line)
                    sink.flush()
                    self._last.append(line)
                    if not self._listening and LISTENING in line:
                        self._listening = True
                        self._settled.set()
        finally:
            self._settled.set()

    def stop(self, timeout: float = 30) -> None:
        """TERM, KILL after `timeout`; returns once the log has every line."""
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self._reader.join()
