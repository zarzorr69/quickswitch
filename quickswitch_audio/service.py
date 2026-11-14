import errno
import fcntl
import json
import os
import signal
import socket
import socketserver
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from . import audio, settings


MESSAGE_LIMIT = 8192


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if audio.FLATPAK_ID and runtime and Path(runtime).is_absolute():
        return Path(runtime) / "app" / audio.FLATPAK_ID / audio.APP_SLUG / "control.sock"
    base = Path(runtime) / audio.APP_SLUG if runtime and Path(runtime).is_absolute() else audio.config_dir()
    return base / "control.sock"


def request(action, key=None):
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        if error.errno in (errno.EAFNOSUPPORT, errno.EACCES, errno.EPERM):
            return None
        raise audio.AudioError(f"Could not open the audio service connection. {error}") from error
    with client:
        client.settimeout(1)
        try:
            client.connect(str(socket_path()))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        except OSError as error:
            raise audio.AudioError(f"Could not connect to the audio service. {error}") from error
        client.settimeout(40)
        try:
            client.sendall(json.dumps({"action": action, "key": key}).encode() + b"\n")
            with client.makefile("rb") as stream:
                result = json.loads(stream.readline(MESSAGE_LIMIT))
            if not isinstance(result, dict):
                raise ValueError("invalid response")
        except (OSError, ValueError) as error:
            raise audio.AudioError("The background service did not confirm the action. Check the active output before retrying.") from error
        if "error" in result:
            raise audio.AudioError(result["error"])
        return result


def switch_output(key=None):
    result = request("switch", key)
    if result is None:
        return audio.switch_output(key)
    try:
        return audio.Output(**result["output"])
    except (KeyError, TypeError) as error:
        raise audio.AudioError("The audio service returned an invalid output. Check the active output before retrying.") from error


class AudioRequest(socketserver.StreamRequestHandler):
    timeout = 2

    def handle(self):
        try:
            data = json.loads(self.rfile.readline(MESSAGE_LIMIT))
            if not isinstance(data, dict):
                raise ValueError
            action, key = data.get("action"), data.get("key")
            if action == "switch":
                if key is not None and (
                    not isinstance(key, list) or len(key) != 2 or not all(isinstance(value, str) for value in key)
                ):
                    raise ValueError
                output = audio.switch_output(tuple(key) if key is not None else None)
                result = {"output": asdict(output)}
            elif action == "stop":
                self.server.stopping = True
                result = {"stopping": True}
            else:
                raise ValueError
        except (ValueError, OSError):
            result = {"error": "Invalid audio service request."}
        except audio.AudioError as error:
            result = {"error": str(error)}
        try:
            self.wfile.write(json.dumps(result).encode() + b"\n")
        except OSError:
            pass


def run():
    path = socket_path()
    os.umask(0o077)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.with_name("daemon.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            path.unlink(missing_ok=True)
            with socketserver.UnixStreamServer(str(path), AudioRequest) as server:
                path.chmod(0o600)
                server.stopping = False
                server.timeout = 0.5
                for signum in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(signum, lambda *_: setattr(server, "stopping", True))
                try:
                    while not server.stopping:
                        server.handle_request()
                finally:
                    path.unlink(missing_ok=True)
    except OSError as error:
        raise audio.AudioError(f"Could not start the background audio service. {error}") from error


def unit_path():
    return audio.xdg_dir("XDG_CONFIG_HOME", ".config") / "systemd/user" / settings.UNIT_NAME


def login_path():
    return audio.xdg_dir("XDG_CONFIG_HOME", ".config") / "autostart" / f"{audio.APP_ID}.desktop"


def systemd_available():
    try:
        return subprocess.run(
            ["systemctl", "--user", "show", "--property=Version", "--value"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def systemctl(*args):
    audio.command("systemctl", "--user", *args, timeout=45)


def apply_startup(mode):
    if mode == "systemd":
        settings.atomic_write(unit_path(), settings.unit_text(), mode=0o644)
        systemctl("daemon-reload")
        try:
            systemctl("enable", "--now", settings.UNIT_NAME)
        except audio.AudioError:
            systemctl("disable", "--now", settings.UNIT_NAME)
            raise
    elif mode == "desktop":
        settings.atomic_write(login_path(), settings.login_entry(), mode=0o644)


def remove_startup(mode):
    if mode == "systemd":
        systemctl("disable", "--now", settings.UNIT_NAME)
    elif mode == "desktop":
        request("stop")
        login_path().unlink(missing_ok=True)


def set_startup(enabled):
    with settings.lock():
        previous = settings.load()
        if enabled == bool(previous.startup):
            return previous
        if audio.FLATPAK_ID:
            from . import portals
            portals.require_background(enabled, required=enabled)
            updated = replace(previous, startup="portal" if enabled else "")
            try:
                if not enabled:
                    request("stop")
                settings.save(updated)
            except (audio.AudioError, OSError):
                portals.require_background(bool(previous.startup), required=bool(previous.startup))
                raise
            return updated
        if enabled:
            mode = "systemd" if systemd_available() else "desktop"
            apply_startup(mode)
        else:
            mode = ""
            remove_startup(previous.startup)
        updated = replace(previous, startup=mode)
        try:
            settings.save(updated)
        except OSError:
            if enabled:
                remove_startup(mode)
            else:
                apply_startup(previous.startup)
            raise
        return updated
