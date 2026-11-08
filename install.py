#!/usr/bin/env python3
import argparse
import shlex
import shutil
import sys
from pathlib import Path

from quickswitch_audio import service, settings, shortcuts
from quickswitch_audio.audio import APP_ID, APP_NAME, APP_SLUG, ICON_PATH, AudioError, xdg_dir


SOURCE = Path(__file__).resolve().parent
APP_DIR = xdg_dir("XDG_DATA_HOME", ".local/share") / APP_SLUG
BIN = Path.home() / ".local/bin" / APP_SLUG
DESKTOP = APP_DIR.parent / "applications" / f"{APP_ID}.desktop"
ICON = APP_DIR.parent / "icons/hicolor/scalable/apps" / ICON_PATH.name
MARKER = "# QuickSwitch Audio launcher"


def write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def install():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except (ImportError, ValueError) as error:
        raise SystemExit("Install Python GObject and GTK 3, then run the installer with your distro’s Python.") from error
    if BIN.exists() and MARKER not in BIN.read_text(errors="replace"):
        raise SystemExit(f"A different launcher already exists at {BIN}.")
    shutil.copytree(SOURCE / "quickswitch_audio", APP_DIR / "quickswitch_audio",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    write(BIN, f'''#!/bin/sh
{MARKER}
cd {shlex.quote(str(APP_DIR))} || exit 1
exec {shlex.quote(sys.executable)} -m quickswitch_audio "$@"
''', 0o755)
    entry = settings.desktop_arg(BIN)
    write(DESKTOP, f'''[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Switch playback outputs and control the active volume
Exec={entry}
Icon={APP_SLUG}
Terminal=false
Categories=AudioVideo;Audio;
Keywords=Sound;Audio;Headphones;Speakers;PipeWire;
Actions=SwitchOutput;

[Desktop Action SwitchOutput]
Name=Switch output
Exec={entry} toggle
''', 0o644)
    ICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON_PATH, ICON)
    if settings.load().startup == "systemd" and service.systemd_available():
        settings.atomic_write(service.unit_path(), settings.unit_text(), mode=0o644)
        service.systemctl("daemon-reload")
        service.systemctl("try-restart", settings.UNIT_NAME)
    print(f"Installed {APP_NAME}. Open it from your app launcher or run:\n  {BIN}")


def uninstall():
    if not BIN.exists() or MARKER not in BIN.read_text(errors="replace"):
        raise SystemExit(f"Could not find the installed QuickSwitch Audio launcher at {BIN}.")
    preferences = settings.load()
    if preferences.shortcut_enabled:
        shortcuts.set_shortcut(enabled=False)
    if preferences.startup:
        service.set_startup(False)
    unit = service.unit_path()
    if unit.exists() and "Description=QuickSwitch Audio background switch service" in unit.read_text():
        unit.unlink()
        if service.systemd_available():
            service.systemctl("daemon-reload")
    for path in (DESKTOP, ICON):
        path.unlink(missing_ok=True)
    module = APP_DIR / "quickswitch_audio"
    if module.is_dir():
        shutil.rmtree(module)
    if APP_DIR.is_dir() and not any(APP_DIR.iterdir()):
        APP_DIR.rmdir()
    BIN.unlink()
    print(f"Removed {APP_NAME}. Your cycle preferences were kept.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Install {APP_NAME} for the current user")
    parser.add_argument("--uninstall", action="store_true")
    try:
        if parser.parse_args().uninstall:
            uninstall()
        else:
            install()
    except AudioError as error:
        raise SystemExit(str(error)) from error
