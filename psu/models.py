"""Shared data models for the EA PSU controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto


class ConnectionState(StrEnum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    ERROR = auto()


CONTROL_LOCATION = {
    0x00: "free",
    0x01: "local",
    0x03: "USB",
    0x04: "analog",
    0x05: "Profibus",
    0x06: "Ethernet",
    0x08: "Master/Slave",
    0x09: "RS232",
    0x10: "CANopen",
    0x12: "Modbus TCP 1P",
    0x13: "Profinet 1P",
    0x14: "Ethernet 1P",
    0x15: "Ethernet 2P",
    0x16: "Modbus TCP 2P",
    0x17: "Profinet 2P",
    0x18: "GPIB",
    0x19: "CAN",
    0x1A: "EtherCAT",
    0x1C: "free (CTO)",
}

REGULATION_MODE = {
    0: "CV",
    1: "CR",
    2: "CC",
    3: "CP",
}


@dataclass(frozen=True)
class Nominals:
    """Device nameplate values (read once at connect)."""

    voltage_v: float
    current_a: float
    power_w: float | None = None
    serial_number: str | None = None


@dataclass(frozen=True)
class PsuReading:
    """Snapshot of live output + setpoint values.

    Percentages are 0–100 relative to the device nominals.
    Absolute values are derived from those percentages.
    """

    # Actuals
    voltage_v: float
    current_a: float
    power_w: float
    voltage_pct: float
    current_pct: float
    power_pct: float

    # Setpoints (targets)
    target_voltage_v: float
    target_current_a: float
    target_voltage_pct: float
    target_current_pct: float

    timestamp: float  # time.monotonic()


@dataclass(frozen=True)
class StatusBitmap:
    """Decoded register 505 (32-bit device state)."""

    raw: int
    control_location: int
    control_location_name: str
    config_mode: bool
    ms_master: bool
    output_on: bool
    regulation_mode: int
    regulation_mode_name: str
    remote: bool
    external_sense: bool
    alarms: bool
    ovp: bool
    ocp: bool
    opp: bool
    ot: bool
    power_fail: bool
    msp: bool
    rem_sb: bool

    @classmethod
    def from_raw(cls, status: int) -> StatusBitmap:
        ctrl = status & 0x1F
        reg = (status >> 9) & 0x3
        return cls(
            raw=status,
            control_location=ctrl,
            control_location_name=CONTROL_LOCATION.get(ctrl, f"0x{ctrl:02X}"),
            config_mode=bool(status & (1 << 5)),
            ms_master=bool(status & (1 << 6)),
            output_on=bool(status & (1 << 7)),
            regulation_mode=reg,
            regulation_mode_name=REGULATION_MODE.get(reg, "?"),
            remote=bool(status & (1 << 11)),
            external_sense=bool(status & (1 << 14)),
            alarms=bool(status & (1 << 15)),
            ovp=bool(status & (1 << 16)),
            ocp=bool(status & (1 << 17)),
            opp=bool(status & (1 << 18)),
            ot=bool(status & (1 << 19)),
            power_fail=bool(status & (1 << 21)),
            msp=bool(status & (1 << 29)),
            rem_sb=bool(status & (1 << 30)),
        )

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "raw_hex": f"0x{self.raw:08X}",
            "control_location": self.control_location,
            "control_location_name": self.control_location_name,
            "config_mode": self.config_mode,
            "ms_master": self.ms_master,
            "output_on": self.output_on,
            "regulation_mode": self.regulation_mode,
            "regulation_mode_name": self.regulation_mode_name,
            "remote": self.remote,
            "external_sense": self.external_sense,
            "alarms": self.alarms,
            "ovp": self.ovp,
            "ocp": self.ocp,
            "opp": self.opp,
            "ot": self.ot,
            "power_fail": self.power_fail,
            "msp": self.msp,
            "rem_sb": self.rem_sb,
        }


@dataclass(frozen=True)
class PsuStatus:
    """Aggregate status exposed to the rest of the app."""

    connection: ConnectionState
    remote_active: bool
    output_on: bool
    serial_number: str | None
    nominals: Nominals | None
    reading: PsuReading | None
    last_error: str | None = None
    status_bitmap: StatusBitmap | None = None
