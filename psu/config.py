"""TOML → dataclass configuration loader."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger as log


def _resolve_config_path(name: str = "psu.toml") -> Path:
    return Path(__file__).parent.parent / "config" / name


def _resolve_ingest_path(name="inbound") -> Path:
    return Path(__file__).parent.parent / "ingest" / name


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
class WebAppConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8081


@dataclass
class AppConfig:
    mock: bool = True
    log_level: str = "INFO"


@dataclass
class TelemetryConfig:
    """Writer-side telemetry settings (drop location + batching).

    Sink configuration lives in a separate daq-tools TOML (``daq_config_path``).
    """

    measurement: str = "ea_psu"
    enabled: bool = False
    watch_dir: str | Path = field(default_factory=lambda: _resolve_ingest_path())
    max_batch_size: int = 500
    batch_interval_s: float = 2.0
    queue_maxsize: int = 500
    daq_config_path: str | Path = field(
        default_factory=lambda: _resolve_config_path("data.toml")
    )


@dataclass
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    poll: PollConfig = field(default_factory=PollConfig)
    web_app: WebAppConfig = field(default_factory=WebAppConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

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
            telemetry=TelemetryConfig(**raw.get("telemetry", {})),
        )
