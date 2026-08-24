"""Hardware abstraction for EA PS 10000 series (Modbus RTU over USB).

Provides a pure-async interface. Real path uses pymodbus AsyncModbusSerialClient;
mock path returns plausible values for development without the instrument.
"""

from __future__ import annotations

import asyncio
import struct
import time
from abc import ABC, abstractmethod

from loguru import logger as log

from .config import SerialConfig
from .models import ConnectionState, Nominals, PsuReading, PsuStatus, StatusBitmap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# EA full-scale: 0xCCCC (52428) == 100 % of nominal (programming guide §4.3).
# Set/actual registers accept 0–102 %.
FULL_SCALE = 0xCCCC  # 52428


def _raw_to_percent(raw: int) -> float:
    """Convert EA raw register value → percent of nominal."""
    return raw * 100.0 / FULL_SCALE


def _percent_to_raw(pct: float) -> int:
    """Clamp 0–102 % and convert to EA raw (0…~53476)."""
    pct = max(0.0, min(102.0, pct))
    return int(pct * FULL_SCALE / 100.0)


def _decode_float(regs: list[int]) -> float:
    """Big-endian IEEE-754 float spanning two 16-bit registers."""
    raw = struct.pack(">HH", regs[0], regs[1])
    return struct.unpack(">f", raw)[0]


def _regs_to_serial(regs: list[int]) -> str | None:
    """Decode consecutive holding registers into a printable serial string.

    EA typically packs ASCII (or digit) data one char per low/high byte.
    Non-printable bytes are skipped.
    """
    chars: list[str] = []
    for r in regs:
        for b in ((r >> 8) & 0xFF, r & 0xFF):
            if 32 <= b < 127:
                chars.append(chr(b))
    s = "".join(chars).strip()
    return s or None


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class PowerSupply(ABC):
    """Async interface every concrete PSU driver must implement."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def read(self) -> PsuReading: ...

    @abstractmethod
    async def set_voltage(self, volts: float) -> None: ...

    @abstractmethod
    async def set_current(self, amps: float) -> None: ...

    @abstractmethod
    async def set_targets(
        self,
        voltage_v: float | None = None,
        current_a: float | None = None,
        power_w: float | None = None,
    ) -> None: ...

    @abstractmethod
    async def set_power(self, watts: float) -> None:
        """Write the power setpoint (reg 502) as absolute watts."""
        ...

    @abstractmethod
    async def enable_remote(self, enabled: bool = True) -> None:
        """Take / release remote control of the device."""
        ...

    @abstractmethod
    async def enable_output(self, enabled: bool = True) -> None:
        """Switch the DC terminal on or off (requires remote control)."""
        ...

    @abstractmethod
    def status(self) -> PsuStatus: ...

    @property
    @abstractmethod
    def serial_number(self) -> str | None: ...


# ---------------------------------------------------------------------------
# Mock implementation
# ---------------------------------------------------------------------------


class MockPowerSupply(PowerSupply):
    """In-memory simulator for development."""

    def __init__(self, serial: SerialConfig) -> None:
        self._serial: SerialConfig = serial
        self._connected: bool = False
        self._serial_number: str = "MOCK-3321060002"
        self._nominals: Nominals = Nominals(
            voltage_v=500.0,
            current_a=180.0,
            power_w=30_000.0,
            serial_number=self._serial_number,
        )
        self._voltage_pct: float = 0.0
        self._current_pct: float = 0.0
        self._target_voltage_pct: float = 0.0
        self._target_current_pct: float = 0.0
        self._target_power_pct: float = 0.0
        self._remote_active: bool = False
        self._output_on: bool = False
        self._last_reading: PsuReading | None = None
        self._last_error: str | None = None
        self._status_bitmap: StatusBitmap | None = None
        log.info("MockPowerSupply ready")

    @property
    def serial_number(self) -> str | None:
        return self._serial_number if self._connected else None

    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        self._connected = True
        log.info(f"MockPowerSupply connected  sn={self._serial_number}")

    async def disconnect(self) -> None:
        self._output_on = False
        self._remote_active = False
        self._connected = False
        log.info("MockPowerSupply disconnected")

    async def enable_remote(self, enabled: bool = True) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        self._remote_active = enabled
        if not enabled:
            self._output_on = False
        log.info(f"Mock remote → {'ON' if enabled else 'OFF'}")

    async def enable_output(self, enabled: bool = True) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        if enabled and not self._remote_active:
            raise RuntimeError(
                "remote control must be enabled before turning output on"
            )
        self._output_on = enabled
        log.info(f"Mock output → {'ON' if enabled else 'OFF'}")

    async def set_voltage(self, volts: float) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        pct = (volts / self._nominals.voltage_v) * 100.0
        self._target_voltage_pct = max(0.0, min(100.0, pct))
        log.info(f"Mock set voltage → {volts:.2f} V ({self._target_voltage_pct:.2f} %)")

    async def set_current(self, amps: float) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        pct = (amps / self._nominals.current_a) * 100.0
        self._target_current_pct = max(0.0, min(100.0, pct))
        log.info(f"Mock set current → {amps:.2f} A ({self._target_current_pct:.2f} %)")

    async def set_targets(
        self,
        voltage_v: float | None = None,
        current_a: float | None = None,
        power_w: float | None = None,
    ) -> None:
        if voltage_v is not None:
            await self.set_voltage(voltage_v)
        if current_a is not None:
            await self.set_current(current_a)
        if power_w is not None:
            await self.set_power(power_w)

    async def set_power(self, watts: float) -> None:
        if not self._connected:
            raise RuntimeError("not connected")
        p_nom = self._nominals.power_w
        pct = (watts / p_nom) * 100.0 if p_nom else 0.0
        # Keep U/I limits as the user set them (matches real hardware).
        # Only the independent power setpoint moves.
        self._target_power_pct = max(0.0, min(100.0, pct))
        log.info(f"Mock set power → {watts:.1f} W ({pct:.2f} % of {p_nom:.0f} W)")

    async def read(self) -> PsuReading:
        if not self._connected:
            raise RuntimeError("not connected")

        if self._output_on:
            self._voltage_pct += (self._target_voltage_pct - self._voltage_pct) * 0.15
            self._current_pct += (self._target_current_pct - self._current_pct) * 0.15
        else:
            self._voltage_pct *= 0.7
            self._current_pct *= 0.7

        u = self._voltage_pct / 100.0 * self._nominals.voltage_v
        i = self._current_pct / 100.0 * self._nominals.current_a
        # Actual power is limited by U*I and the power setpoint
        p_ui = u * i
        p_lim = self._target_power_pct / 100.0 * self._nominals.power_w
        p = min(p_ui, p_lim) if self._output_on else p_ui * 0.0

        tu = self._target_voltage_pct / 100.0 * self._nominals.voltage_v
        ti = self._target_current_pct / 100.0 * self._nominals.current_a
        tp = self._target_power_pct / 100.0 * self._nominals.power_w

        reading = PsuReading(
            voltage_v=round(u, 2),
            current_a=round(i, 2),
            power_w=round(p, 1),
            voltage_pct=round(self._voltage_pct, 2),
            current_pct=round(self._current_pct, 2),
            power_pct=round(p / self._nominals.power_w * 100.0, 2),
            target_voltage_v=round(tu, 2),
            target_current_a=round(ti, 2),
            target_power_w=round(tp, 1),
            target_voltage_pct=round(self._target_voltage_pct, 2),
            target_current_pct=round(self._target_current_pct, 2),
            target_power_pct=round(self._target_power_pct, 2),
            timestamp=time.monotonic(),
        )
        self._last_reading = reading
        return reading

    def status(self) -> PsuStatus:
        # Synthesize a plausible register-505 word for debug UI
        raw = 0
        if self._remote_active:
            raw |= 0x03  # control location = USB
            raw |= 1 << 11  # remote bit
        else:
            raw |= 0x01  # local
        if self._output_on:
            raw |= 1 << 7
        # default regulation mode CV when output on else nothing special
        if self._output_on:
            raw |= 0 << 9  # CV
        bitmap = StatusBitmap.from_raw(raw)
        return PsuStatus(
            connection=ConnectionState.CONNECTED
            if self._connected
            else ConnectionState.DISCONNECTED,
            remote_active=self._remote_active,
            output_on=self._output_on,
            serial_number=self.serial_number,
            nominals=self._nominals if self._connected else None,
            reading=self._last_reading,
            last_error=self._last_error,
            status_bitmap=bitmap if self._connected else None,
        )


# ---------------------------------------------------------------------------
# Real Modbus implementation
# ---------------------------------------------------------------------------


class EaPsPowerSupply(PowerSupply):
    """EA PS 10000 series over USB CDC-ACM (Modbus RTU).

    Uses device_id=0 by default (factory Limited compliance mode).
    """

    REG_U_NOM = 121
    REG_I_NOM = 123
    REG_P_NOM = 125
    REG_SERIAL = 151
    REG_SERIAL_COUNT = 20  # 40 ASCII chars
    REG_REMOTE = 402  # coil: remote control
    REG_OUTPUT = 405  # coil: DC terminal on/off
    REG_U_SET = 500
    REG_I_SET = 501
    REG_P_SET = 502  # power setpoint (percent of P_nom)
    REG_STATUS = 505  # 32-bit device state
    REG_ACTUALS = 507

    def __init__(self, serial: SerialConfig) -> None:
        self._cfg = serial
        self._client = None
        self._connected = False
        self._serial_number: str | None = None
        self._nominals: Nominals | None = None
        self._last_reading: PsuReading | None = None
        self._last_error: str | None = None
        self._remote_active = False
        self._output_on = False
        self._status_bitmap: StatusBitmap | None = None

    @property
    def serial_number(self) -> str | None:
        return self._serial_number

    async def connect(self) -> None:
        from pymodbus.client import AsyncModbusSerialClient

        self._client = AsyncModbusSerialClient(
            port=self._cfg.port,
            baudrate=self._cfg.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self._cfg.timeout_s,
        )
        ok = await self._client.connect()
        if not ok:
            self._last_error = f"Failed to open {self._cfg.port}"
            raise ConnectionError(self._last_error)

        self._connected = True
        log.info(
            f"EaPsPowerSupply connected  port={self._cfg.port}  "
            f"baud={self._cfg.baudrate}  id={self._cfg.device_id}"
        )

        try:
            rr = await self._client.read_holding_registers(
                address=self.REG_SERIAL,
                count=self.REG_SERIAL_COUNT,
                device_id=self._cfg.device_id,
            )
            if not rr.isError():
                self._serial_number = _regs_to_serial(rr.registers)
                log.info(f"Serial number: {self._serial_number}")
            else:
                log.warning(f"Could not read serial @ {self.REG_SERIAL}: {rr}")
                raise ConnectionError(f"Could not read serial: {rr}")
        except Exception as e:
            log.warning(f"Serial read failed: {e}")
            raise RuntimeError(f"Serial read failed: {e}")

        try:
            u_nom = await self._read_float(self.REG_U_NOM)
            i_nom = await self._read_float(self.REG_I_NOM)
            p_nom = await self._read_float(self.REG_P_NOM)

            self._nominals = Nominals(
                voltage_v=u_nom,
                current_a=i_nom,
                power_w=p_nom,
                serial_number=self._serial_number,
            )
            log.info(f"Nominals  U={u_nom:.1f} V  I={i_nom:.1f} A")
        except Exception as e:
            self._last_error = str(e)
            log.warning(f"Could not read nominals: {e}")
            raise RuntimeError(f"Could not read nominals: {e}")

        await self._refresh_status_flags()

    async def disconnect(self) -> None:
        try:
            if self._connected and self._output_on:
                await self.enable_output(False)
            if self._connected and self._remote_active:
                await self.enable_remote(False)
        except Exception as e:
            log.warning(f"Cleanup on disconnect: {e}")

        if self._client is not None:
            self._client.close()
            self._client = None
        self._connected = False
        log.info("EaPsPowerSupply disconnected")

    async def enable_remote(self, enabled: bool = True) -> None:
        await self._write_coil(self.REG_REMOTE, enabled)
        self._remote_active = enabled
        if not enabled:
            self._output_on = False
        log.info(f"Remote control → {'ON' if enabled else 'OFF'}")

    async def enable_output(self, enabled: bool = True) -> None:
        if enabled and not self._remote_active:
            raise RuntimeError(
                "remote control must be enabled before turning output on"
            )
        await self._write_coil(self.REG_OUTPUT, enabled)
        self._output_on = enabled
        log.info(f"DC output → {'ON' if enabled else 'OFF'}")

    async def set_voltage(self, volts: float) -> None:
        if self._nominals is None:
            raise RuntimeError("nominals not available")

        pct = (volts / self._nominals.voltage_v) * 100.0
        raw = _percent_to_raw(pct)
        await self._write_register(self.REG_U_SET, raw)
        log.info(f"Set voltage → {volts:.2f} V ({pct:.2f} %, raw={raw})")

    async def set_current(self, amps: float) -> None:
        if self._nominals is None:
            raise RuntimeError("nominals not available")

        pct = (amps / self._nominals.current_a) * 100.0
        raw = _percent_to_raw(pct)
        await self._write_register(self.REG_I_SET, raw)
        log.info(f"Set current → {amps:.2f} A ({pct:.2f} %, raw={raw})")

    async def set_targets(
        self,
        voltage_v: float | None = None,
        current_a: float | None = None,
        power_w: float | None = None,
    ) -> None:
        if voltage_v is not None:
            await self.set_voltage(voltage_v)
        if current_a is not None:
            await self.set_current(current_a)
        if power_w is not None:
            await self.set_power(power_w)

    async def set_power(self, watts: float) -> None:
        if self._nominals is None:
            raise RuntimeError("nominals not available")
        p_nom = self._nominals.power_w
        if p_nom <= 0:
            raise RuntimeError("invalid nominal power")
        pct = (watts / p_nom) * 100.0
        raw = _percent_to_raw(pct)
        await self._write_register(self.REG_P_SET, raw)
        log.info(f"Set power → {watts:.1f} W ({pct:.2f} %, raw={raw})")

    async def read(self) -> PsuReading:
        if not self._connected or self._client is None:
            raise RuntimeError("not connected")
        if self._nominals is None:
            raise RuntimeError("nominals not available")

        rr_act = await self._client.read_holding_registers(
            address=self.REG_ACTUALS,
            count=3,
            device_id=self._cfg.device_id,
        )
        if rr_act.isError():
            self._last_error = str(rr_act)
            raise RuntimeError(f"Modbus read error (actuals): {rr_act}")

        # 500=U_set, 501=I_set, 502=P_set
        rr_set = await self._client.read_holding_registers(
            address=self.REG_U_SET,
            count=3,
            device_id=self._cfg.device_id,
        )
        if rr_set.isError():
            self._last_error = str(rr_set)
            raise RuntimeError(f"Modbus read error (setpoints): {rr_set}")

        await self._refresh_status_flags()

        u_pct = _raw_to_percent(rr_act.registers[0])
        i_pct = _raw_to_percent(rr_act.registers[1])
        p_pct = _raw_to_percent(rr_act.registers[2])
        tu_pct = _raw_to_percent(rr_set.registers[0])
        ti_pct = _raw_to_percent(rr_set.registers[1])
        tp_pct = _raw_to_percent(rr_set.registers[2])

        u = u_pct / 100.0 * self._nominals.voltage_v
        i = i_pct / 100.0 * self._nominals.current_a
        p = p_pct / 100.0 * self._nominals.power_w
        tu = tu_pct / 100.0 * self._nominals.voltage_v
        ti = ti_pct / 100.0 * self._nominals.current_a
        tp = tp_pct / 100.0 * self._nominals.power_w

        reading = PsuReading(
            voltage_v=round(u, 2),
            current_a=round(i, 2),
            power_w=round(p, 1),
            voltage_pct=round(u_pct, 2),
            current_pct=round(i_pct, 2),
            power_pct=round(p_pct, 2),
            target_voltage_v=round(tu, 2),
            target_current_a=round(ti, 2),
            target_power_w=round(tp, 1),
            target_voltage_pct=round(tu_pct, 2),
            target_current_pct=round(ti_pct, 2),
            target_power_pct=round(tp_pct, 2),
            timestamp=time.monotonic(),
        )
        self._last_reading = reading
        self._last_error = None
        return reading

    def status(self) -> PsuStatus:
        conn = (
            ConnectionState.CONNECTED
            if self._connected
            else ConnectionState.DISCONNECTED
        )
        if self._last_error and self._connected:
            conn = ConnectionState.ERROR
        return PsuStatus(
            connection=conn,
            remote_active=self._remote_active,
            output_on=self._output_on,
            serial_number=self._serial_number,
            nominals=self._nominals,
            reading=self._last_reading,
            last_error=self._last_error,
            status_bitmap=self._status_bitmap,
        )

    async def _read_float(self, address: int) -> float:
        if not self._connected or self._client is None:
            raise RuntimeError("not connected")
        rr = await self._client.read_holding_registers(
            address=address,
            count=2,
            device_id=self._cfg.device_id,
        )
        if rr.isError():
            raise RuntimeError(f"Modbus error @ {address}: {rr}")
        return _decode_float(rr.registers)

    async def _write_register(self, address: int, value: int) -> None:
        if not self._connected or self._client is None:
            raise RuntimeError("not connected")
        rr = await self._client.write_register(
            address=address,
            value=value,
            device_id=self._cfg.device_id,
        )
        if rr.isError():
            self._last_error = str(rr)
            raise RuntimeError(f"Modbus write error @ {address}: {rr}")

    async def _write_coil(self, address: int, value: bool) -> None:
        if not self._connected or self._client is None:
            raise RuntimeError("not connected")
        rr = await self._client.write_coil(
            address=address,
            value=value,
            device_id=self._cfg.device_id,
        )
        if rr.isError():
            self._last_error = str(rr)
            raise RuntimeError(f"Modbus coil write error @ {address}: {rr}")

    async def _refresh_status_flags(self) -> None:
        """Parse register 505 (32-bit device state) per PS10000 register list KE3.10+.

        Official bitmap:
          bits 0-4  Control location
                    0x00 free, 0x01 local, 0x03 USB, 0x04 analog,
                    0x06 Ethernet, 0x08 Master/Slave, 0x09 RS232, ...
          bit  5    Config mode
          bit  6    MS type (0=slave, 1=master)
          bit  7    Output state (0=off, 1=on)
          bits 9-10 Regulation mode (00=CV, 01=CR, 10=CC, 11=CP)
          bit 11    Remote (0=off, 1=on)
          bit 14    External sense
          bit 15    Alarms (any)
          bit 16    OVP
          bit 17    OCP
          bit 18    OPP
          bit 19    OT
          bit 21    Power fail
          bit 29    MSP
          bit 30    REM-SB (1 = pin forces DC off)
        """
        if self._client is None:
            return
        try:
            rr = await self._client.read_holding_registers(
                address=self.REG_STATUS,
                count=2,
                device_id=self._cfg.device_id,
            )
            if rr.isError():
                return

            # Two holding regs → one uint32 (register 505 = high word, 506 = low word)
            status = (rr.registers[0] << 16) | rr.registers[1]

            bitmap = StatusBitmap.from_raw(status)
            self._status_bitmap = bitmap
            self._output_on = bitmap.output_on
            # Prefer explicit remote bit; fall back to control-location interface
            self._remote_active = bitmap.remote or (
                bitmap.control_location not in (0x00, 0x01)
            )

            log.debug(
                f"Status 505=0x{bitmap.raw:08X}  "
                f"ctrl={bitmap.control_location_name}  remote={self._remote_active}  "
                f"output={self._output_on}  mode={bitmap.regulation_mode_name}  "
                f"alarms={bitmap.alarms}"
            )
        except Exception as e:
            log.debug(f"Status refresh failed: {e}")


def create_psu(mock: bool, serial: SerialConfig) -> PowerSupply:
    if mock:
        return MockPowerSupply(serial)
    return EaPsPowerSupply(serial)
