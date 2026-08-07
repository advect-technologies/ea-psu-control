"""CSV solar-power profile storage and async playback.

Template (required columns):
    time,normalized_power
Optional:
    seconds   — relative timeline; derived from time if absent

Playback is wall-clock based on relative seconds so the player does not
drift if a step takes longer than the sample interval.
"""

from __future__ import annotations

import asyncio
import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log

from .hardware import PowerSupply

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ProfilePoint:
    """One sample on the relative timeline."""

    t_s: float  # seconds from profile start
    normalized: float  # 0…1


@dataclass
class Profile:
    """Loaded power profile."""

    name: str
    points: list[ProfilePoint]
    source_path: Path | None = None

    @property
    def duration_s(self) -> float:
        if not self.points:
            return 0.0
        return self.points[-1].t_s

    @property
    def n_points(self) -> int:
        return len(self.points)

    def sample_at(self, t_s: float) -> ProfilePoint:
        """Nearest sample at or before t_s (hold-last between points)."""
        if not self.points:
            raise RuntimeError("empty profile")
        if t_s <= self.points[0].t_s:
            return self.points[0]
        if t_s >= self.points[-1].t_s:
            return self.points[-1]
        # linear search is fine for ~10k points; binary would be overkill for now
        lo, hi = 0, len(self.points) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.points[mid].t_s <= t_s:
                lo = mid
            else:
                hi = mid - 1
        return self.points[lo]


@dataclass
class PlayerState:
    """Snapshot of player progress for the UI."""

    active: bool = False
    profile_name: str | None = None
    power_scale_kw: float = 0.0
    index: int = 0
    n_points: int = 0
    elapsed_s: float = 0.0
    duration_s: float = 0.0
    normalized: float = 0.0
    power_w: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "profile_name": self.profile_name,
            "power_scale_kw": self.power_scale_kw,
            "index": self.index,
            "n_points": self.n_points,
            "elapsed_s": round(self.elapsed_s, 1),
            "duration_s": round(self.duration_s, 1),
            "normalized": round(self.normalized, 6),
            "power_w": round(self.power_w, 1),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Load / validate
# ---------------------------------------------------------------------------


class ProfileError(ValueError):
    """Raised when a CSV fails integrity checks."""


def _sanitize_name(name: str) -> str:
    base = Path(name).stem
    safe = _SAFE_NAME.sub("_", base).strip("._") or "profile"
    return safe[:80]


def load_profile_csv(path: Path, name: str | None = None) -> Profile:
    """Parse and validate a profile CSV.

    Required columns: time, normalized_power
    Optional: seconds (relative); derived from time - time[0] if missing.
    """
    path = Path(path)
    if not path.is_file():
        raise ProfileError(f"file not found: {path}")

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ProfileError("empty CSV")
        fields = {h.strip().lower(): h for h in reader.fieldnames}
        if "time" not in fields or "normalized_power" not in fields:
            raise ProfileError("CSV must contain columns 'time' and 'normalized_power'")
        time_key = fields["time"]
        power_key = fields["normalized_power"]
        seconds_key = fields.get("seconds")

        raw_rows: list[tuple[float, float | None, float]] = []
        for i, row in enumerate(reader, start=2):
            try:
                t_abs = float(row[time_key])
                norm = float(row[power_key])
            except (KeyError, TypeError, ValueError) as e:
                raise ProfileError(f"row {i}: bad numeric value ({e})") from e
            if not (0.0 <= norm <= 1.0):
                raise ProfileError(f"row {i}: normalized_power={norm} outside [0, 1]")
            rel: float | None = None
            if seconds_key is not None and row.get(seconds_key, "") != "":
                try:
                    rel = float(row[seconds_key])
                except (TypeError, ValueError) as e:
                    raise ProfileError(f"row {i}: bad seconds ({e})") from e
            raw_rows.append((t_abs, rel, norm))

    if len(raw_rows) < 2:
        raise ProfileError("profile needs at least 2 rows")

    # Prefer explicit seconds column; else derive from absolute time
    if all(r[1] is not None for r in raw_rows):
        rels = [float(r[1]) for r in raw_rows]  # type: ignore[arg-type]
    else:
        t0 = raw_rows[0][0]
        rels = [r[0] - t0 for r in raw_rows]

    for i in range(1, len(rels)):
        if rels[i] < rels[i - 1]:
            raise ProfileError(
                f"row {i + 2}: time not non-decreasing ({rels[i - 1]} → {rels[i]})"
            )

    # Shift so first sample is t=0
    t_offset = rels[0]
    points = [
        ProfilePoint(t_s=rel - t_offset, normalized=norm)
        for rel, (_, _, norm) in zip(rels, raw_rows)
    ]

    profile_name = _sanitize_name(name or path.name)
    log.info(
        f"Loaded profile '{profile_name}'  points={len(points)}  "
        f"duration={points[-1].t_s:.0f}s"
    )
    return Profile(name=profile_name, points=points, source_path=path)


# ---------------------------------------------------------------------------
# On-disk store
# ---------------------------------------------------------------------------


class ProfileStore:
    """Simple file-backed profile library under ``profiles/``."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            root = Path(__file__).resolve().parent.parent / "profiles"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_names(self) -> list[str]:
        names = sorted(p.stem for p in self.root.glob("*.csv") if p.is_file())
        return names

    def path_for(self, name: str) -> Path:
        return self.root / f"{_sanitize_name(name)}.csv"

    def load(self, name: str) -> Profile:
        path = self.path_for(name)
        if not path.is_file():
            raise ProfileError(f"unknown profile: {name}")
        return load_profile_csv(path, name=name)

    def save_upload(self, filename: str, data: bytes) -> Profile:
        name = _sanitize_name(filename)
        dest = self.path_for(name)
        dest.write_bytes(data)
        try:
            profile = load_profile_csv(dest, name=name)
        except ProfileError:
            dest.unlink(missing_ok=True)
            raise
        return profile

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        if path.is_file():
            path.unlink()


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------


class ProfilePlayer:
    """Async wall-clock player that writes power setpoints to the PSU."""

    def __init__(self, psu: PowerSupply, store: ProfileStore) -> None:
        self._psu = psu
        self._store = store
        self._profile: Profile | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._state = PlayerState()
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._state.active

    def state(self) -> PlayerState:
        return self._state

    async def start(self, profile_name: str, power_scale_kw: float) -> None:
        if power_scale_kw <= 0:
            raise ProfileError("power_scale_kw must be > 0")
        elif power_scale_kw > 30:
            # TODO use actual max power output
            raise ProfileError("power_scale_kw must be <= 30")

        async with self._lock:
            if self._state.active:
                raise ProfileError("profile already running — stop first")

            profile = self._store.load(profile_name)
            self._profile = profile
            self._stop.clear()
            self._state = PlayerState(
                active=True,
                profile_name=profile.name,
                power_scale_kw=power_scale_kw,
                n_points=profile.n_points,
                duration_s=profile.duration_s,
            )
            self._task = asyncio.create_task(
                self._run(profile, power_scale_kw),
                name=f"profile:{profile.name}",
            )
            log.info(
                f"Profile start '{profile.name}'  scale={power_scale_kw} kW  "
                f"duration={profile.duration_s:.0f}s"
            )

    async def stop(self) -> None:
        async with self._lock:
            if not self._state.active:
                return
            self._stop.set()
            task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    log.warning(f"Task Cancelled {task}")
        async with self._lock:
            self._task = None
            self._state.active = False
            self._state.error = None
            log.info("Profile stopped")

    async def _run(self, profile: Profile, power_scale_kw: float) -> None:
        scale_w = power_scale_kw * 1000.0
        t0 = time.monotonic()
        last_idx = -1
        try:
            # Ensure remote is on so setpoints take effect
            st = self._psu.status()
            if not st.remote_active:
                await self._psu.enable_remote(True)

            while not self._stop.is_set():
                elapsed = time.monotonic() - t0
                if elapsed >= profile.duration_s:
                    # final sample then exit cleanly
                    pt = profile.points[-1]
                    power_w = pt.normalized * scale_w
                    await self._psu.set_power(power_w)
                    self._state.elapsed_s = profile.duration_s
                    self._state.index = profile.n_points - 1
                    self._state.normalized = pt.normalized
                    self._state.power_w = power_w
                    log.info(
                        f"Profile '{profile.name}' complete  final P={power_w:.1f} W"
                    )
                    break

                pt = profile.sample_at(elapsed)
                # Find index for UI
                idx = 0
                for i, p in enumerate(profile.points):
                    if p.t_s <= elapsed:
                        idx = i
                    else:
                        break

                power_w = pt.normalized * scale_w
                if idx != last_idx:
                    await self._psu.set_power(power_w)
                    last_idx = idx
                    log.debug(
                        f"Profile step idx={idx} t={elapsed:.1f}s  "
                        f"norm={pt.normalized:.4f}  P={power_w:.1f} W"
                    )

                self._state.elapsed_s = elapsed
                self._state.index = idx
                self._state.normalized = pt.normalized
                self._state.power_w = power_w

                # Sleep until next sample boundary (or 0.25 s max for stop latency)
                next_t = profile.duration_s
                if idx + 1 < profile.n_points:
                    next_t = profile.points[idx + 1].t_s
                sleep_s = min(0.25, max(0.02, next_t - elapsed))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                except TimeoutError:
                    pass

        except Exception as e:
            log.error(f"Profile player error: {e}")
            self._state.error = str(e)
        finally:
            self._state.active = False
            self._task = None
