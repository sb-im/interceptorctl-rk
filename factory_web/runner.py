"""Run the existing interceptorctl CLI and preserve its JSON output."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


def parse_json_output(output: str) -> Optional[Any]:
    text = output.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Keep stdout verbatim for the log, but also recognize a final JSON line
        # if an older CLI happens to print an informational prefix.
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


class CliRunner:
    def __init__(self, cli_path: Path, socket_path: str, timeout: float = 180.0) -> None:
        self.cli_path = Path(cli_path)
        self.socket_path = socket_path
        self.timeout = timeout

    def run(self, args: Sequence[str]) -> Dict[str, Any]:
        argv = [str(value) for value in args]
        timeout = self._effective_timeout(argv)
        command = [
            sys.executable,
            str(self.cli_path),
            "--json",
            "--socket",
            self.socket_path,
            *argv,
        ]
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()

        if not self.cli_path.is_file():
            return self._result(
                argv,
                started_at,
                started,
                127,
                "",
                f"cli.py 不存在: {self.cli_path}",
            )

        try:
            process = subprocess.run(
                command,
                cwd=self.cli_path.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
            return self._result(
                argv,
                started_at,
                started,
                process.returncode,
                process.stdout,
                process.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            return self._result(
                argv,
                started_at,
                started,
                124,
                self._timeout_text(exc.stdout),
                f"命令执行超过 {timeout:g} 秒\n{self._timeout_text(exc.stderr)}".strip(),
            )
        except OSError as exc:
            return self._result(argv, started_at, started, 126, "", str(exc))

    @staticmethod
    def _timeout_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _effective_timeout(self, args: Sequence[str]) -> float:
        """Allow an explicitly longer CLI --timeout plus process cleanup margin."""
        result = self.timeout
        for index, value in enumerate(args[:-1]):
            if value != "--timeout":
                continue
            try:
                result = max(result, float(args[index + 1]) + 15.0)
            except (TypeError, ValueError):
                pass
        return result

    @staticmethod
    def _result(
        args: Sequence[str],
        started_at: str,
        started: float,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> Dict[str, Any]:
        stdout = stdout.rstrip("\r\n")
        stderr = stderr.rstrip("\r\n")
        parsed = parse_json_output(stdout)
        json_ok = not isinstance(parsed, dict) or parsed.get("ok") is not False
        return {
            "ok": returncode == 0 and json_ok,
            "args": list(args),
            "command": "interceptorctl " + shlex.join(list(args)),
            "returncode": returncode,
            "started_at": started_at,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
            "json": parsed,
        }
