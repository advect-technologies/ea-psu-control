"""Entry point for the EA PSU controller."""

from __future__ import annotations

import asyncio
import signal
import sys

from loguru import logger as log

from .config import Config
from .hardware import create_psu
from .models import PsuReading
from .web import start_web


def _setup_logging(level: str = "INFO") -> None:
    log.remove()
    log.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


async def _poll_loop(psu, interval_s: float, stop: asyncio.Event) -> None:
    """Continuously read the PSU and log a compact line."""
    while not stop.is_set():
        try:
            reading: PsuReading = await psu.read()
            log.info(
                f"U={reading.voltage_v:7.2f}/{reading.target_voltage_v:7.2f} V  "
                f"I={reading.current_a:6.2f}/{reading.target_current_a:6.2f} A  "
                f"P={reading.power_w:8.1f} W"
            )
        except Exception as e:
            log.warning(f"Poll error: {e}")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def run(config_path: str | None = None) -> None:
    config = Config.load(config_path)
    _setup_logging(config.app.log_level)

    log.info(
        f"Starting PSU controller  mock={config.app.mock}  "
        f"port={config.serial.port}  id={config.serial.device_id}"
    )

    psu = create_psu(config.app.mock, config.serial)
    stop = asyncio.Event()
    runner = None

    def _on_signal() -> None:
        log.info("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass

    await psu.connect()

    try:
        if config.web_app.enabled:
            runner = await start_web(
                psu,
                host=config.web_app.host,
                port=config.web_app.port,
            )

        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                _poll_loop(psu, config.poll.interval_s, stop),
                name="poll",
            )
            await stop.wait()

    except* Exception as eg:
        for exc in eg.exceptions:
            log.error(f"Task failed: {exc}")
    finally:
        if runner is not None:
            await runner.cleanup()
        await psu.disconnect()
        log.info("Bye")


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        asyncio.run(run(config_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
