from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import sleep


APP_NAME = "QuickSwitch Audio"
APP_SLUG = "quickswitch-audio"
FLATPAK_ID = os.environ.get("FLATPAK_ID", "")
APP_ID = FLATPAK_ID or "local.quickswitch.Audio"
ICON_DIR = Path(__file__).with_name("assets")
ICON_PATH = ICON_DIR / f"{APP_SLUG}.svg"
CODEC_PATH = Path("/proc/asound")
SYSFS_CODECS = Path("/sys/bus/hdaudio/devices")
MIXER_LEVEL = "87"
REALTEK_PORTS = {
    "analog-output-lineout": ("Line Out", "Front"),
    "analog-output-headphones": ("Headphones", "Headphone"),
}
COMMAND_ENV = {**os.environ, "LC_ALL": "C"}


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class Output:
    sink: str
    port: str
    title: str
    device: str
    kind: str
    realtek_card: str | None
    jack_available: bool
    node_id: int | None = None

    @property
    def key(self):
        return self.sink, self.port


@dataclass(frozen=True)
class Snapshot:
    outputs: tuple[Output, ...]
    default_sink: str
    ports: dict[str, str]
    volume: int
    muted: bool
    backend: str = "pulse"

    @property
    def active(self):
        key = self.default_sink, self.ports.get(self.default_sink, "")
        return next((output for output in self.outputs if output.key == key), None)


def xdg_dir(variable, fallback):
    value = os.environ.get(variable)
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def config_dir():
    return xdg_dir("XDG_CONFIG_HOME", ".config") / APP_SLUG


def command(*args, timeout=3):
    try:
        result = subprocess.run(
            args, env=COMMAND_ENV, capture_output=True, text=True, errors="replace",
            check=True, timeout=timeout,
        )
    except FileNotFoundError as error:
        raise AudioError(f"{args[0]} is missing. See the installation instructions.") from error
    except subprocess.TimeoutExpired as error:
        raise AudioError(f"{args[0]} timed out.") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or f"exit status {error.returncode}"
        raise AudioError(f"{' '.join(args[:2])}: {detail}") from error
    except OSError as error:
        raise AudioError(f"Could not run {args[0]}: {error}") from error
    return result.stdout.strip()


def realtek_cards():
    cards = set()
    for codec in CODEC_PATH.glob("card[0-9]*/codec#*"):
        try:
            with codec.open(errors="replace") as stream:
                if stream.readline().strip() == "Codec: Realtek ALC897":
                    cards.add(codec.parent.name[4:])
        except OSError:
            continue
    for codec in SYSFS_CODECS.glob("hdaudioC*D*"):
        match = re.fullmatch(r"hdaudioC(\d+)D\d+", codec.name)
        try:
            if match and int((codec / "vendor_id").read_text().strip(), 16) == 0x10EC0897:
                cards.add(match[1])
        except (OSError, ValueError):
            continue
    return cards


def sink_ports(sink):
    ports = sink.get("ports") or []
    if isinstance(ports, dict):
        return [{**port, "name": name} for name, port in ports.items()]
    return ports


def output_kind(sink_name, props, port):
    bus = props.get("device.bus")
    if bus == "bluetooth" or props.get("device.api") == "bluez5" or sink_name.startswith("bluez_output."):
        return "Bluetooth"
    if re.search(r"hdmi|displayport", f"{sink_name} {port}", re.IGNORECASE):
        return "HDMI / DisplayPort"
    if bus == "usb" or sink_name.startswith("alsa_output.usb-"):
        return "USB"
    return "Analog" if port.startswith("analog-") else "Playback device"


def legacy_pulse_sinks():
    sinks = []
    section = ""
    for line in command("pactl", "list", "sinks").splitlines():
        if re.fullmatch(r"Sink #\d+", line):
            sinks.append({"ports": [], "properties": {}, "volume": {}})
            section = ""
        if not sinks or not line.startswith("\t"):
            continue
        sink = sinks[-1]
        content = line.lstrip()
        if not line.startswith("\t\t"):
            section, _, value = content.partition(":")
            value = value.strip()
            fields = {"Name": "name", "Description": "description", "Active Port": "active_port", "Mute": "mute"}
            if section in fields:
                sink[fields[section]] = value
            elif section == "Volume":
                sink["volume"] = {name: {"value": int(level)} for name, level in re.findall(
                    r"([\w-]+):\s+(\d+)\s*/", value,
                )}
        elif section == "Properties":
            match = re.fullmatch(r'([\w.-]+) = "(.*)"', content)
            if match:
                sink["properties"][match[1]] = match[2]
        elif section == "Ports":
            name, _, value = content.partition(": ")
            description, _, details = value.rpartition(" (")
            sink["ports"].append({"name": name, "description": description or value,
                                  "availability": "no" if details.endswith("not available)") else "unknown"})
    return [sink for sink in sinks if "name" in sink]


def pulse_snapshot():
    legacy = False
    try:
        sinks = json.loads(command("pactl", "--format=json", "list", "sinks"))
        if not isinstance(sinks, list):
            raise ValueError("expected a list of sinks")
    except AudioError as error:
        if "unrecognized option" not in str(error).lower() and "invalid option" not in str(error).lower():
            raise
        sinks = legacy_pulse_sinks()
        legacy = True
    except ValueError as error:
        raise AudioError("pactl returned an unexpected list of audio outputs.") from error
    sinks = [sink for sink in sinks if sink.get("name") != "auto_null"]
    default = ""
    if sinks and legacy:
        info = command("pactl", "info")
        match = re.search(r"^Default Sink:\s*(.+)$", info, re.MULTILINE)
        default = match[1].strip() if match else ""
    elif sinks:
        default = command("pactl", "get-default-sink")
    cards = realtek_cards()
    outputs = []
    active_ports = {}
    volume = 0
    muted = False
    for sink in sinks:
        name = sink["name"]
        props = sink.get("properties") or {}
        description = sink.get("description") or name
        ports = sink_ports(sink)
        active_port = sink.get("active_port") or ""
        if isinstance(active_port, dict):
            active_port = active_port.get("name", "")
        active_ports[name] = active_port
        if name == default:
            channels = sink.get("volume") or {}
            levels = [int(channel["value"]) for channel in channels.values()]
            volume = round(sum(levels) * 100 / (65536 * len(levels))) if levels else 0
            muted = sink.get("mute") in (True, "yes", "true")

        numbers = (str(props.get(key, "")) for key in ("alsa.card", "api.alsa.card", "api.alsa.pcm.card"))
        card = next((number for number in numbers if number.isdecimal()), "")
        components = str(props.get("alsa.components") or props.get("api.alsa.card.components") or "")
        has_pair = REALTEK_PORTS.keys() <= {port["name"] for port in ports}
        use_realtek = bool(card) and has_pair and (
            card in cards or "hda:10ec0897" in components.casefold()
        )
        if not ports:
            outputs.append(Output(name, "", description, "", output_kind(name, props, ""), None, True))
        for port in ports:
            port_name = port["name"]
            is_realtek = use_realtek and port_name in REALTEK_PORTS
            availability = str(port.get("availability", port.get("available", ""))).lower()
            available = availability not in {"not available", "no", "false", "unavailable"}
            if not available and not is_realtek and port_name != active_port:
                continue
            title = REALTEK_PORTS[port_name][0] if is_realtek else port.get("description") or port_name
            device = "Realtek ALC897" if is_realtek else description
            outputs.append(Output(
                name, port_name, title, device, output_kind(name, props, port_name),
                card if is_realtek else None, available,
            ))
    outputs.sort(key=lambda output: (
        output.realtek_card is None, output.device.casefold(), output.sink,
        output.port != "analog-output-lineout", output.title.casefold(), output.port,
    ))
    return Snapshot(tuple(outputs), default, active_ports, volume, muted)


def pipewire_snapshot():
    try:
        objects = json.loads(command("pw-dump"))
        if not isinstance(objects, list):
            raise ValueError
    except ValueError as error:
        raise AudioError("pw-dump returned an unexpected audio graph.") from error
    devices = {
        str(item["id"]): (item.get("info") or {}).get("props") or {}
        for item in objects if item.get("type") == "PipeWire:Interface:Device"
    }
    outputs = []
    for item in objects:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        props = (item.get("info") or {}).get("props") or {}
        name = props.get("node.name")
        if props.get("media.class") != "Audio/Sink" or not name or name == "auto_null":
            continue
        card = devices.get(str(props.get("device.id")), {})
        title = props.get("node.description") or props.get("node.nick") or name
        device = card.get("device.description") or props.get("device.description") or ""
        outputs.append(Output(name, "", title, device if device != title else "",
                              output_kind(name, {**card, **props}, ""), None, True, item["id"]))
    outputs.sort(key=lambda output: (output.title.casefold(), output.sink))
    default = ""
    volume = 0
    muted = False
    if outputs:
        try:
            inspection = command("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@")
        except AudioError:
            inspection = ""
        match = re.match(r"id\s+(\d+),", inspection)
        active = next((output for output in outputs if match and output.node_id == int(match[1])), None)
        if active is not None:
            default = active.sink
            level = command("wpctl", "get-volume", str(active.node_id))
            number = re.search(r"Volume:\s+([0-9.]+)", level)
            if number:
                volume = round(float(number[1]) * 100)
            muted = "[MUTED]" in level
    return Snapshot(tuple(outputs), default, {}, volume, muted, "pipewire")


def snapshot(backend=None):
    if backend == "pipewire":
        return pipewire_snapshot()
    try:
        return pulse_snapshot()
    except AudioError as pulse_error:
        if backend == "pulse" or not (shutil.which("pw-dump") and shutil.which("wpctl")):
            raise
        try:
            return pipewire_snapshot()
        except AudioError as pipewire_error:
            raise AudioError(f"Could not read playback devices.\n{pulse_error}\n{pipewire_error}") from pipewire_error


def cycle_keys(state):
    path = config_dir() / "cycle.json"
    try:
        values = json.loads(path.read_text())
    except FileNotFoundError:
        realtek = [output for output in state.outputs if output.realtek_card is not None]
        if realtek:
            selected = next((output for output in realtek if output.sink == state.default_sink), realtek[0])
            return {output.key for output in realtek if output.sink == selected.sink}
        return {output.key for output in state.outputs}
    except (OSError, ValueError) as error:
        raise AudioError(f"Could not read the cycle settings at {path}.") from error
    if not isinstance(values, list) or not all(
        isinstance(key, list) and len(key) == 2 and all(isinstance(value, str) for value in key)
        for key in values
    ):
        raise AudioError(f"The cycle settings at {path} are invalid. Use Reset cycle to reset them.")
    return {tuple(key) for key in values}


@contextmanager
def audio_lock():
    folder = config_dir()
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (folder / "audio.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
    except OSError as error:
        raise AudioError(f"Could not access QuickSwitch Audio settings: {error}") from error


def set_cycle_member(key, enabled):
    with audio_lock():
        keys = cycle_keys(snapshot())
        if enabled:
            keys.add(key)
        else:
            keys.discard(key)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=config_dir(), delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(sorted(keys), stream, indent=2)
                stream.write("\n")
            temporary.replace(config_dir() / "cycle.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def reset_cycle():
    with audio_lock():
        (config_dir() / "cycle.json").unlink(missing_ok=True)


def mixer_restore_commands(card, control):
    try:
        result = command("amixer", "-c", card, "sget", control)
    except AudioError as error:
        if "unable to find simple control" in str(error).lower():
            return None
        raise
    channels = re.findall(r"^\s*(.+?): Playback (-?\d+)(.*)$", result, re.MULTILINE)
    if not channels:
        return None
    set_control = ("amixer", "-c", card, "sset", control, "playback")
    restore = [(*set_control, ",".join(level for _, level, _ in channels))]
    switches = re.findall(r"\[(on|off)\]", " ".join(detail for _, _, detail in channels))
    if switches and len(set(switches)) == 1:
        restore[0] += ("unmute" if switches[0] == "on" else "mute",)
    elif len(switches) == len(channels):
        for (channel, _, _), state in zip(channels, switches):
            restore.append((*set_control, channel.lower().replace(" ", ""),
                            "unmute" if state == "on" else "mute"))
    return restore


def apply_output(state, output):
    if state.active == output:
        return
    if state.backend == "pipewire":
        previous = state.active
        try:
            command("wpctl", "set-default", str(output.node_id))
            verify_output(state.backend, output)
        except AudioError:
            if previous is not None:
                command("wpctl", "set-default", str(previous.node_id))
            raise
        return
    card = output.realtek_card
    if card is not None and shutil.which("amixer"):
        mixer = REALTEK_PORTS[output.port][1]
        other = "Headphone" if mixer == "Front" else "Front"
        other_restore = mixer_restore_commands(card, other)
        active_restore = mixer_restore_commands(card, mixer)
        if other_restore is None or active_restore is None:
            card = None
        else:
            mixer_restore = other_restore + active_restore
    else:
        card = None

    previous_port = state.ports.get(output.sink, "")
    rollback = []
    try:
        if output.port and output.port != previous_port:
            if previous_port:
                rollback.append(("pactl", "set-sink-port", output.sink, previous_port))
            command("pactl", "set-sink-port", output.sink, output.port)
        if card is not None:
            rollback.extend(mixer_restore)
            command("amixer", "-c", card, "sset", other, "0")
            command("amixer", "-c", card, "sset", mixer, MIXER_LEVEL, "unmute")
        if output.sink != state.default_sink:
            if state.default_sink:
                rollback.append(("pactl", "set-default-sink", state.default_sink))
            command("pactl", "set-default-sink", output.sink)
        verify_output(state.backend, output)
    except AudioError as error:
        failures = []
        for args in rollback:
            try:
                command(*args)
            except AudioError as restore_error:
                failures.append(str(restore_error))
        if failures:
            restore_details = "\n".join(failures)
            raise AudioError(f"{error}\nCould not fully restore audio.\n{restore_details}") from error
        raise


def verify_output(backend, output):
    for attempt in range(4):
        if attempt:
            sleep(0.08)
        active = snapshot(backend).active
        if active is not None and active.key == output.key:
            return
    raise AudioError(f"The audio server did not activate {output.title}.")


def switch_output(key=None):
    with audio_lock():
        state = snapshot()
        if key is None:
            selected = cycle_keys(state)
            outputs = [output for output in state.outputs if output.key in selected]
            if len(outputs) < 2:
                raise AudioError("Select at least two connected outputs in the cycle.")
            if state.active in outputs:
                output = outputs[(outputs.index(state.active) + 1) % len(outputs)]
            else:
                output = outputs[0]
        else:
            output = next((candidate for candidate in state.outputs if candidate.key == key), None)
            if output is None:
                raise AudioError("That output is no longer connected.")
        apply_output(state, output)
        return output


def change_volume(key, level=None):
    with audio_lock():
        state = snapshot()
        active = state.active
        if active is not None and active.key == key:
            if state.backend == "pipewire":
                command("wpctl", "set-mute" if level is None else "set-volume",
                        str(active.node_id), "toggle" if level is None else f"{level}%")
            elif level is None:
                command("pactl", "set-sink-mute", active.sink, "toggle")
            else:
                command("pactl", "set-sink-volume", active.sink, f"{level}%")


def notify(output):
    if FLATPAK_ID:
        from . import portals
        portals.notify(output)
        return
    try:
        command("notify-send", "-a", APP_NAME, "-i", str(ICON_PATH),
                "-h", f"string:x-canonical-private-synchronous:{APP_SLUG}",
                "-t", "1600", "--", output.title, output.device or output.kind)
    except AudioError:
        pass
