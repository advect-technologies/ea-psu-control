"""TOML → dataclass configuration loader."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger as log


def _resolve_config_path(name: str = "psu.toml") -> Path:
    return Path(__file__).parent.parent / "config" / name


@dataclass
class SerialConfig:
    port: str = "/dev/ttyACM0"
    baudrate: int = 115200
    device_id: int = 0  # EA Limited-mode default
    timeout_s: float = 1.5


@dataclass
class PollConfig:
    interval_s: float = 1.0
    """How often to read actual values from the PSU."""


@dataclass
class AppConfig:
    mock: bool = True
    log_level: str = "INFO"


@dataclass
class WebAppConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8081


@dataclass
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    poll: PollConfig = field(default_factory=PollConfig)
    web_app: WebAppConfig = field(default_factory=WebAppConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        if path is None:
            path = _resolve_config_path()

        path = Path(path)

        if not path.exists():
            log.warning(f"Config file not found: {path} — using defaults")
            return cls()

        raw = tomllib.loads(path.read_text())
        return cls(
            app=AppConfig(**raw.get("app", {})),
            serial=SerialConfig(**raw.get("serial", {})),
            poll=PollConfig(**raw.get("poll", {})),
            web_app=WebAppConfig(**raw.get("web_app", {})),
        )
