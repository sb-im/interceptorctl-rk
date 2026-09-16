"""Runtime settings for the graphical interceptorctl menu."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    cli_path: Path
    socket_path: Path
    command_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cli_path=Path(
                os.getenv(
                    "INTERCEPTORCTL_CLI",
                    "/home/orangepi/interceptorctl/cli.py",
                )
            ).expanduser(),
            socket_path=Path(
                os.getenv(
                    "INTERCEPTORCTL_SOCKET",
                    "/tmp/interceptorctl.sock",
                )
            ).expanduser(),
            command_timeout=max(
                5.0,
                float(os.getenv("FACTORY_WEB_COMMAND_TIMEOUT", "180")),
            ),
        )
