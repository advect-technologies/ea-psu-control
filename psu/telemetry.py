"""Telemetry bridge: PSU readings → daq-tools DataPoint JSONL drops.

The control app only writes atomic *.jsonl files into ``watch_dir``.
``DAQIngestor`` (started separately from config/daq.toml) watches that
directory and fans out to sinks. Control path is never blocked by sink
failures.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger as log

from .config import TelemetryConfig
from .hardware import PowerSupply
from .models import PsuReading, PsuStatus
from .profile import ProfilePlayer


def _reading_to_datapoint(
    reading: PsuReading,
    status: PsuStatus,
    measurement: str,
    *,
    mock: bool,
    player: ProfilePlayer | None,
):
    """Build a daq-tools DataPoint from a live reading."""
    from daq_tools import DataPoint

    sn = status.serial_number or "unknown"
    source = "mock" if mock else "ea-ps"

    tags: dict[str, str] = {
        "serial": sn,
        "source": source,
        "profile": "manual",
    }
    fields: dict = {
        "voltage_v": reading.voltage_v,
        "current_a": reading.current_a,
        "power_w": reading.power_w,
        "target_voltage_v": reading.target_voltage_v,
        "target_current_a": reading.target_current_a,
        "target_power_w": reading.target_power_w,
        "remote": int(status.remote_active),
        "output": int(status.output_on),
    }

    if player is not None:
        ps = player.state()
        if ps.active and ps.profile_name:
            tags["profile"] = ps.profile_name
            if ps.run_id:
                tags["run_id"] = ps.run_id

            fields["profile_norm"] = ps.normalized
            fields["profile_power_w"] = ps.power_w

    return DataPoint(
        measurement=measurement,
        time=time.time(),
        tags=tags,
        fields=fields,
    )


class TelemetryWriter:
    """Queue + batched atomic JSONL writer into a daq-tools watch_dir."""

    def __init__(self, config: TelemetryConfig) -> None:
        self._cfg = config
        self._watch_dir = Path(config.watch_dir)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_maxsize)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._flush_now = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    async def start(self) -> None:
        if not self._cfg.enabled:
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._task = asyncio.create_task(self._flush_loop(), name="telemetry-flush")
        log.info(
            f"TelemetryWriter started  watch_dir={self._watch_dir}  "
            f"batch_size={self._cfg.max_batch_size}  "
            f"batch_interval_s={self._cfg.batch_interval_s}"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                self._flush_now.set()
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    log.debug("Telemetry flush task cancelled or failed")
            self._task = None
        # final flush of anything left
        await self._flush_available()
        log.info("TelemetryWriter stopped")

    def submit(self, point) -> None:
        """Non-blocking enqueue. Drops if queue is full (never blocks control)."""
        if not self._cfg.enabled:
            return
        try:
            self._queue.put_nowait(point)
        except asyncio.QueueFull:
            log.warning("Telemetry queue full — dropped point(s)")
        if self._queue.qsize() >= self._cfg.max_batch_size:
            self._flush_now.set()

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with asyncio.timeout(self._cfg.batch_interval_s):
                    await self._flush_now.wait()
                    self._flush_now.clear()
            except TimeoutError:
                pass
            try:
                await self._flush_available()
            except Exception as e:
                log.warning(f"Telemetry flush error: {e}")

    async def _flush_available(self) -> None:
        batch = []
        while len(batch) < self._cfg.max_batch_size and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        await self._write_batch(batch)

    async def _write_batch(self, points: list) -> None:
        if not points:
            return
        ts = time.time()
        name = f"psu_{ts:.3f}_{len(points)}.jsonl"
        dest = self._watch_dir / name
        tmp = self._watch_dir / f".{name}.tmp"

        try:
            points = [p.to_json() for p in points]
            tmp.write_text("\n".join(points), encoding="utf-8")
            tmp.replace(dest)  # atomic on same filesystem
            log.debug(f"Telemetry wrote {len(points)} point(s) → {dest.name}")
        except Exception as e:
            log.warning(f"Failed to write telemetry batch: {e}")
            tmp.unlink(missing_ok=True)

    async def publish_reading(
        self,
        psu: PowerSupply,
        reading: PsuReading,
        *,
        mock: bool,
        player: ProfilePlayer | None = None,
    ) -> None:
        """Convert a reading and enqueue it. Safe to call every poll tick."""
        if not self.enabled:
            return
        try:
            point = _reading_to_datapoint(
                reading, psu.status(), self._cfg.measurement, mock=mock, player=player
            )
            self.submit(point)
        except Exception as e:
            log.warning(f"Telemetry publish failed: {e}")
