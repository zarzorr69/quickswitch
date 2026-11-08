from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .audio import APP_SLUG, AudioError, config_dir


MODIFIERS = ("Ctrl", "Alt", "Shift", "Super")
MODIFIER_KEYS = {
    "Control_L": "Ctrl", "Control_R": "Ctrl", "Alt_L": "Alt", "Alt_R": "Alt",
    "Shift_L": "Shift", "Shift_R": "Shift", "Super_L": "Super", "Super_R": "Super",
}
DEFAULT_SHORTCUT = ("Alt", "Super", "a")
LAUNCHER = Path.home() / ".local/bin" / APP_SLUG
UNIT_NAME = f"{APP_SLUG}.service"


@dataclass(frozen=True)
class Preferences:
    theme: str = "dark"
    background: bool | None = None
    startup: str = ""
    shortcut: tuple[str, ...] = DEFAULT_SHORTCUT
    shortcut_backend: str = ""
    shortcut_location: str = ""

    @property
    def shortcut_enabled(self):
        return bool(self.shortcut_backend)


def validate_shortcut(parts):
    if not isinstance(parts, (tuple, list)) or not parts or not all(isinstance(part, str) for part in parts):
        raise AudioError("Record a valid key combination.")
    key = parts[-1]
    modifiers = set(parts[:-1])
    if not modifiers.issubset(MODIFIERS) or not re.fullmatch(r"[A-Za-z0-9_]+", key):
        raise AudioError("This key combination is unavailable. Choose another key.")
    modifiers.discard(MODIFIER_KEYS.get(key))
    return tuple(modifier for modifier in MODIFIERS if modifier in modifiers) + (key,)


def shortcut_label(parts):
    key = parts[-1]
    return "+".join((*parts[:-1], key.upper() if len(key) == 1 else key))


def desktop_arg(value):
    quoted = re.sub(r'([\\"`$])', r'\\\1', str(value).replace("%", "%%"))
    return '"' + quoted.replace("\\", "\\\\") + '"'


def shortcut_command():
    return f"{shlex.quote(str(LAUNCHER))} shortcut"


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load():
    path = config_dir() / "settings.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return Preferences()
    except (ValueError, OSError) as error:
        raise AudioError(f"Could not read settings at {path}.") from error
    if not isinstance(data, dict):
        raise AudioError(f"Settings at {path} must be a JSON object.")
    defaults = Preferences()
    values = {name: data.get(name, value) for name, value in asdict(defaults).items()}
    if values["theme"] not in ("light", "dark") or values["startup"] not in ("", "systemd", "desktop"):
        raise AudioError(f"Theme or login settings at {path} are invalid.")
    if values["background"] is not None and not isinstance(values["background"], bool):
        raise AudioError(f"The background setting at {path} is invalid.")
    if not all(isinstance(values[key], str) for key in ("shortcut_backend", "shortcut_location")):
        raise AudioError(f"The shortcut settings at {path} are invalid.")
    values["shortcut"] = validate_shortcut(values["shortcut"])
    return Preferences(**values)


def save(preferences):
    atomic_write(config_dir() / "settings.json", json.dumps(asdict(preferences), indent=2) + "\n")


@contextmanager
def lock():
    folder = config_dir()
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (folder / "settings.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
    except OSError as error:
        raise AudioError(f"Could not update QuickSwitch Audio settings. {error}") from error


def update(**values):
    with lock():
        preferences = replace(load(), **values)
        save(preferences)
        return preferences


def login_entry():
    return f"""[Desktop Entry]
Type=Application
Name=QuickSwitch Audio Service
Exec={desktop_arg(LAUNCHER)} daemon
Terminal=false
NoDisplay=true
X-QuickSwitch-Audio=true
"""


def unit_text():
    return f"""[Unit]
Description=QuickSwitch Audio background switch service

[Service]
ExecStart=\"%h/.local/bin/{APP_SLUG}\" daemon
Restart=on-failure
RestartSec=2
TimeoutStopSec=45
KillMode=mixed
UMask=0077

[Install]
WantedBy=default.target
"""
