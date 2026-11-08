import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from time import sleep

from . import audio, settings


GSETTINGS = {
    "gnome": (
        "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings",
        "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
        "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/",
        ("org.gnome.desktop.wm.keybindings", "org.gnome.shell.keybindings"),
    ),
    "cinnamon": (
        "org.cinnamon.desktop.keybindings", "custom-list",
        "org.cinnamon.desktop.keybindings.custom-keybinding",
        "/org/cinnamon/desktop/keybindings/custom-keybindings/",
        ("org.cinnamon.desktop.keybindings.wm", "org.cinnamon.desktop.keybindings.media-keys"),
    ),
}
NATIVE_NAMES = {
    "hyprland": "Hyprland", "sway": "Sway", "gnome": "GNOME", "cinnamon": "Cinnamon", "xfce": "Xfce",
}
MODMASK = {"Ctrl": 4, "Alt": 8, "Shift": 1, "Super": 64}
BEGIN = "BEGIN QuickSwitch Audio shortcut"
END = "END QuickSwitch Audio shortcut"


def backend_for_session():
    desktop = set(os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":"))
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return "hyprland"
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg") and shutil.which("sway"):
        return "sway"
    if "cinnamon" in desktop or "x-cinnamon" in desktop:
        return "cinnamon"
    if "xfce" in desktop and shutil.which("xfconf-query"):
        return "xfce"
    if "gnome" in desktop:
        return "gnome"
    return "manual"


def gtk_binding(parts, *, primary=False):
    names = {"Ctrl": "Primary" if primary else "Control"}
    return "".join(f"<{names.get(modifier, modifier)}>" for modifier in parts[:-1]) + parts[-1]


def same_binding(value, parts):
    if not isinstance(value, str):
        return False
    modifiers = re.findall(r"<([^>]+)>", value.lower())
    names = {"primary": "ctrl", "control": "ctrl", "mod1": "alt", "mod4": "super", "meta": "super"}
    normalized = {names.get(modifier, modifier) for modifier in modifiers}
    key = parts[-1].lower()
    spellings = {key, key[4:]} if key.startswith("xf86") else {key}
    return normalized == {part.lower() for part in parts[:-1]} and value.rsplit(">", 1)[-1].lower() in spellings


def set_gsettings_binding(backend, parts):
    from gi.repository import Gio

    schema_id, list_key, child_id, root, conflict_schemas = GSETTINGS[backend]
    source = Gio.SettingsSchemaSource.get_default()
    parent_schema = source.lookup(schema_id, True) if source else None
    child_schema = source.lookup(child_id, True) if source else None
    if not parent_schema or not child_schema:
        raise audio.AudioError(f"The {NATIVE_NAMES[backend]} shortcut settings are unavailable. Use manual setup.")
    parent = Gio.Settings.new_full(parent_schema, None, None)
    own_path = root + "quickswitch-audio/"
    own_entry = "quickswitch-audio" if backend == "cinnamon" else own_path
    own = Gio.Settings.new_full(child_schema, None, own_path)
    entries = parent.get_strv(list_key)
    command = settings.shortcut_command()
    owned = own.get_string("command")
    if owned and owned != command:
        raise audio.AudioError("The QuickSwitch shortcut entry already belongs to another command.")
    if not parent.is_writable(list_key) or not all(own.is_writable(key) for key in ("name", "command", "binding")):
        raise audio.AudioError("Your desktop shortcut settings are locked. Use manual setup.")
    array_binding = own.get_value("binding").get_type_string() == "as"
    if parts:
        for entry in entries:
            if entry == own_entry:
                continue
            path = root + entry + "/" if backend == "cinnamon" else entry
            if not path.startswith("/") or not path.endswith("/"):
                continue
            value = Gio.Settings.new_full(child_schema, None, path).get_value("binding").unpack()
            if any(same_binding(binding, parts) for binding in (value if isinstance(value, list) else [value])):
                raise audio.AudioError("That key combination is already assigned to a custom shortcut.")
        for candidate in (schema_id, *conflict_schemas):
            schema = source.lookup(candidate, True)
            if schema is None:
                continue
            desktop = Gio.Settings.new_full(schema, None, None)
            for key in schema.list_keys():
                value = desktop.get_value(key).unpack()
                if any(same_binding(binding, parts) for binding in (value if isinstance(value, list) else [value])):
                    raise audio.AudioError("That key combination is already used by the desktop.")
        own.delay()
        own.set_string("name", "Switch audio output")
        own.set_string("command", command)
        binding = gtk_binding(parts)
        if array_binding:
            own.set_strv("binding", [binding])
        else:
            own.set_string("binding", binding)
        own.apply()
        if own_entry not in entries and not parent.set_strv(list_key, [*entries, own_entry]):
            for key in ("name", "command", "binding"):
                own.reset(key)
            own.apply()
            raise audio.AudioError("The desktop rejected the new shortcut.")
    elif own_entry in entries:
        if not parent.set_strv(list_key, [entry for entry in entries if entry != own_entry]):
            raise audio.AudioError("The desktop could not remove the shortcut.")
        for key in ("name", "command", "binding"):
            own.reset(key)
    Gio.Settings.sync()


def xfce_property(parts):
    return "/commands/custom/" + gtk_binding(parts, primary=True)


def set_xfce_binding(previous, parts):
    query = ("xfconf-query", "--channel", "xfce4-keyboard-shortcuts")
    paths = set(audio.command(*query, "--list").splitlines())
    own_command = settings.shortcut_command()
    old_path = xfce_property(previous) if previous else ""
    new_path = xfce_property(parts) if parts else ""
    created = False
    if new_path:
        for path in paths:
            if "/custom/" not in path and "/default/" not in path:
                continue
            if not same_binding(path.rsplit("/", 1)[-1], parts):
                continue
            if path == new_path and audio.command(*query, "--property", path) == own_command:
                continue
            raise audio.AudioError("That key combination is already assigned in Xfce.")
        if new_path not in paths:
            audio.command(*query, "--property", new_path, "--create", "--type", "string", "--set", own_command)
            created = True
    if old_path and old_path != new_path and old_path in paths:
        try:
            if audio.command(*query, "--property", old_path) == own_command:
                audio.command(*query, "--property", old_path, "--reset")
        except audio.AudioError:
            if created:
                audio.command(*query, "--property", new_path, "--reset")
            raise


def json_query(*args):
    try:
        return json.loads(audio.command(*args))
    except ValueError as error:
        raise audio.AudioError(f"{args[0]} returned an unexpected configuration response.") from error


def hypr_config():
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature:
        return None
    instances = json_query("hyprctl", "-j", "instances")
    instance = next((item for item in instances if item.get("instance") == signature), None)
    if instance is None:
        raise audio.AudioError("Could not identify the running Hyprland instance. Use manual setup.")
    proc = Path("/proc") / str(instance["pid"])
    try:
        arguments = [os.fsdecode(arg) for arg in (proc / "cmdline").read_bytes().split(b"\0") if arg]
        for index, arg in enumerate(arguments):
            if arg.startswith("--config="):
                value = arg.split("=", 1)[1]
            elif arg in ("-c", "--config") and index + 1 < len(arguments):
                value = arguments[index + 1]
            else:
                continue
            path = Path(value).expanduser()
            return ((proc / "cwd").resolve() / path if not path.is_absolute() else path).resolve()
    except OSError as error:
        raise audio.AudioError("Could not inspect Hyprland's active config path. Use manual setup.") from error
    folder = audio.xdg_dir("XDG_CONFIG_HOME", ".config") / "hypr"
    return next((path.resolve() for path in (folder / "hyprland.lua", folder / "hyprland.conf") if path.is_file()), None)


def compositor_config(backend):
    if backend == "hyprland":
        return hypr_config()
    if os.environ.get("SWAYSOCK"):
        value = json_query("swaymsg", "-r", "-t", "get_version").get("loaded_config_file_name")
        return Path(value).resolve() if value else None
    return None


def hypr_modifiers(parts):
    modifiers = set(parts[:-1])
    family = settings.MODIFIER_KEYS.get(parts[-1])
    if family:
        # A modifier release is matched with the trigger's own modifier in the mask.
        modifiers.add(family)
    return tuple(name for name in settings.MODIFIERS if name in modifiers)


def hypr_description(parts):
    return f"QuickSwitch Audio [{settings.shortcut_label(parts)}]"


def hypr_binds(parts):
    mask = sum(MODMASK[part] for part in hypr_modifiers(parts))
    key = parts[-1].casefold()
    spellings = {key, key.replace("control_", "ctrl_", 1)} if key in ("control_l", "control_r") else {key}
    description = hypr_description(parts)
    binds = json_query("hyprctl", "-j", "binds")
    return [bind for bind in binds if not bind.get("submap") and (
        bind.get("description") == description or (
            bind.get("modmask") == mask and str(bind.get("key", "")).split("+")[-1].strip().casefold() in spellings
        )
    )]


def sway_chord(parts):
    names = {"Super": "Mod4", "Alt": "Mod1", "Ctrl": "Ctrl", "Shift": "Shift"}
    return "+".join((*[names[part] for part in parts[:-1]], parts[-1]))


def binding_line(backend, lua, parts):
    command = settings.shortcut_command()
    if backend == "sway":
        return f"bindsym --no-repeat {sway_chord(parts)} exec {command}"
    modifiers = tuple(part.upper() for part in hypr_modifiers(parts))
    description = hypr_description(parts)
    release = parts[-1] in settings.MODIFIER_KEYS
    if lua:
        chord = " + ".join((*modifiers, parts[-1]))
        options = f"description = {json.dumps(description)}"
        if release:
            options += ", release = true, non_consuming = true"
        return f"hl.bind({json.dumps(chord)}, hl.dsp.exec_cmd({json.dumps(command, ensure_ascii=False)}), {{ {options} }})"
    flags = "drn" if release else "d"
    return f"bind{flags} = {' '.join(modifiers)}, {parts[-1]}, {description}, exec, {command}"


def reload_compositor(backend):
    if backend == "hyprland":
        response = audio.command("hyprctl", "reload", timeout=8)
        if response.strip() != "ok":
            raise audio.AudioError(f"Hyprland could not reload the shortcut. {response}")
    else:
        response = json_query("swaymsg", "-r", "reload")
        if not response or not all(item.get("success") for item in response):
            raise audio.AudioError("Sway could not reload its configuration.")


def verify_hyprland_bind(parts, errors_before):
    # hyprctl can acknowledge reload before Hyprland has processed the file.
    for attempt in range(13):
        if attempt:
            sleep(0.1)
        errors = audio.command("hyprctl", "configerrors")
        if errors and errors != errors_before:
            raise audio.AudioError(f"Hyprland rejected the shortcut. {errors.splitlines()[0]}")
        if not parts or any(bind.get("description") == hypr_description(parts) for bind in hypr_binds(parts)):
            return
    raise audio.AudioError("Hyprland did not load the shortcut from the selected config.")


def validate_sway(path, contents):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent) as draft:
        draft.write(contents)
        draft.flush()
        try:
            result = subprocess.run(["sway", "--debug", "--validate", "--config", draft.name],
                                    capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise audio.AudioError("Could not validate the Sway shortcut. Use manual setup.") from error
    if result.returncode:
        raise audio.AudioError("Sway could not validate the configuration with the new shortcut. Use manual setup.")
    if any("Overwriting binding" in line and settings.shortcut_command() in line for line in result.stderr.splitlines()):
        raise audio.AudioError("That combination already has a Sway bind.")


def set_compositor_binding(backend, location, previous, parts):
    path = Path(location)
    if not path.is_file():
        if parts:
            raise audio.AudioError("The active compositor configuration could not be found. Use manual setup.")
        return
    info = path.stat()
    if info.st_uid != os.getuid() or not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        raise audio.AudioError("The compositor configuration is read-only. Use manual setup.")
    current = compositor_config(backend) == path if backend_for_session() == backend else False
    if parts and not current:
        raise audio.AudioError("The active compositor configuration changed. Use manual setup.")
    lua = backend == "hyprland" and path.suffix == ".lua"
    comment = "--" if lua else "#"
    contents = path.read_bytes().decode("utf-8")
    marker = re.escape(comment)
    pattern = rf"^{marker} {BEGIN}\r?\n[^\n]*\r?\n{marker} {END}\r?\n(?:\r?\n)?"
    base, count = re.subn(pattern, "", contents, flags=re.MULTILINE)
    if count > 1 or BEGIN in base or END in base:
        raise audio.AudioError("The managed shortcut block has changed. Review it in your compositor config.")
    if parts and backend == "hyprland":
        allowed = int(bool(count and previous == parts))
        if len(hypr_binds(parts)) > allowed:
            raise audio.AudioError("That combination already has a Hyprland bind. Choose another key or remove the older bind.")
    updated = base
    if parts:
        line = binding_line(backend, lua, parts)
        block = f"{comment} {BEGIN}\n{line}\n{comment} {END}\n\n"
        if "\r\n" in contents:
            block = block.replace("\n", "\r\n")
        updated = block + base
    if updated == contents:
        return
    if current and backend == "sway":
        validate_sway(path, updated)
    errors_before = audio.command("hyprctl", "configerrors") if current and backend == "hyprland" else ""
    if path.read_bytes().decode("utf-8") != contents:
        raise audio.AudioError("Your compositor config changed while the shortcut was being saved. Try again.")
    mode = stat.S_IMODE(info.st_mode)
    settings.atomic_write(path, updated, mode)
    if current:
        try:
            reload_compositor(backend)
            if backend == "hyprland":
                verify_hyprland_bind(parts, errors_before)
        except audio.AudioError as error:
            if path.read_bytes().decode("utf-8") == updated:
                settings.atomic_write(path, contents, mode)
                reload_compositor(backend)
            raise error


def configure(backend, location, previous, parts):
    if parts and parts[-1] in settings.MODIFIER_KEYS and backend in ("gnome", "cinnamon", "xfce", "sway"):
        raise audio.AudioError(f"Automatic setup on {NATIVE_NAMES[backend]} needs a regular key with those modifiers. You can also set it up manually.")
    if backend in GSETTINGS:
        set_gsettings_binding(backend, parts)
    elif backend == "xfce":
        set_xfce_binding(previous, parts)
    elif backend in ("hyprland", "sway"):
        set_compositor_binding(backend, location, previous, parts)


def set_shortcut(*, enabled=None, parts=None, manual=False):
    with settings.lock():
        previous = settings.load()
        combo = settings.validate_shortcut(parts) if parts is not None else previous.shortcut
        enabled = previous.shortcut_enabled if enabled is None else enabled
        backend = previous.shortcut_backend if enabled else ""
        location = previous.shortcut_location if enabled else ""
        if enabled and (manual or not backend):
            backend = "manual" if manual else backend_for_session()
            if backend in ("hyprland", "sway"):
                path = compositor_config(backend)
                if path is None:
                    raise audio.AudioError("Could not find the active compositor config. Use manual setup.")
                location = str(path)
            else:
                location = ""
        updated = replace(previous, shortcut=combo, shortcut_backend=backend, shortcut_location=location)
        if updated == previous:
            return previous
        old_backend, old_location = previous.shortcut_backend, previous.shortcut_location
        same_target = (old_backend, old_location) == (backend, location)
        if previous.shortcut_enabled and not same_target:
            configure(old_backend, old_location, previous.shortcut, None)
        try:
            if enabled:
                configure(backend, location, previous.shortcut if same_target else None, combo)
            try:
                settings.save(updated)
            except OSError:
                if enabled:
                    configure(backend, location, combo, previous.shortcut if same_target else None)
                raise
        except (audio.AudioError, OSError):
            if previous.shortcut_enabled and not same_target:
                configure(old_backend, old_location, None, previous.shortcut)
            raise
        return updated
