import re
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import audio, service, settings, shortcuts


def icon_for(output):
    if output is None:
        return "audio-volume-muted-symbolic"
    if "headphone" in output.port or "headset" in output.port:
        return "audio-headphones-symbolic"
    if output.kind == "Bluetooth":
        return "bluetooth-active-symbolic"
    if output.kind == "HDMI / DisplayPort":
        return "video-display-symbolic"
    return "audio-speakers-symbolic"


def label(text, css=None, *, ellipsize=False):
    widget = Gtk.Label(label=text, xalign=0)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    if css:
        widget.get_style_context().add_class(css)
    return widget


MODIFIER_MASKS = {
    "Ctrl": Gdk.ModifierType.CONTROL_MASK, "Alt": Gdk.ModifierType.MOD1_MASK,
    "Shift": Gdk.ModifierType.SHIFT_MASK,
    "Super": Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK,
}
MODIFIER_NAMES = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
                  "super": "Super", "win": "Super", "meta": "Super"}


def shortcut_key_name(value):
    if value in (0, Gdk.KEY_VoidSymbol):
        raise audio.AudioError("Choose a key with a recognized name.")
    value = Gdk.keyval_to_lower(value)
    name = Gdk.keyval_name(value) or ""
    # GTK omits the XF86 prefix that the compositor's XKB key names use.
    if 0x10080000 <= value < 0x10090000 and not name.startswith("XF86"):
        name = "XF86" + name
    if name.startswith("U+"):
        name = "U" + name[2:]
    return name


def parse_shortcut(text):
    text = text.strip()
    text = "plus" if text == "+" else re.sub(r"\+\s*\+$", "+plus", text)
    *modifiers, key = (part.strip() for part in text.split("+"))
    modifiers = tuple(MODIFIER_NAMES.get(part.lower(), part) for part in modifiers)
    if key.lower() in MODIFIER_NAMES:
        family = MODIFIER_NAMES[key.lower()]
        key = next(name for name, modifier in settings.MODIFIER_KEYS.items() if modifier == family)
    aliases = {"enter": "Return", "esc": "Escape", "ctrl_l": "Control_L", "ctrl_r": "Control_R"}
    key = aliases.get(key.lower(), key)
    if len(key) == 1:
        value = Gdk.unicode_to_keyval(ord(key))
    elif re.fullmatch(r"U[0-9a-fA-F]{4,6}", key):
        codepoint = int(key[1:], 16)
        value = Gdk.unicode_to_keyval(codepoint) if codepoint <= 0x10FFFF else 0
    else:
        value = Gdk.keyval_from_name(key)
        if value == Gdk.KEY_VoidSymbol:
            lookup = key.casefold()
            if lookup.startswith("xf86"):
                lookup = lookup[4:]
            symbol = next((name for name in dir(Gdk) if name.startswith("KEY_") and
                           name[4:].casefold() == lookup), None)
            if symbol:
                value = getattr(Gdk, symbol)
    return settings.validate_shortcut((*modifiers, shortcut_key_name(value)))


class ShortcutDialog(Gtk.Dialog):
    TYPING_HELP = "Type a key or a combination like Super+Ctrl+F9. On Hyprland, Super+Ctrl means hold Super and release Left Ctrl."

    def __init__(self, parent, parts):
        super().__init__(title="Edit switch shortcut", transient_for=parent, modal=True)
        self.set_default_size(430, -1)
        self.recording = False
        self.capture_seat = None
        self.held_modifiers = {}
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save shortcut", Gtk.ResponseType.OK)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        self.get_content_area().add(body)
        self.entry = Gtk.Entry(text=settings.shortcut_label(parts))
        self.entry.set_placeholder_text("Super+Ctrl+F9")
        self.entry.get_accessible().set_name("Key combination")
        self.entry.connect("activate", lambda _: self.response(Gtk.ResponseType.OK))
        body.pack_start(self.entry, False, False, 0)
        self.instructions = Gtk.Label(label=self.TYPING_HELP, xalign=0, wrap=True, max_width_chars=54)
        body.pack_start(self.instructions, False, False, 0)
        self.record = Gtk.Button(label="Record keys", halign=Gtk.Align.START)
        self.record.connect("clicked", self.toggle_recording)
        body.pack_start(self.record, False, False, 0)
        self.connect("key-press-event", self.key_pressed)
        self.connect("key-release-event", self.key_released)
        self.connect("focus-out-event", self.stop_recording)
        self.connect("unmap", self.stop_recording)

    def toggle_recording(self, _):
        if self.recording:
            self.stop_recording()
            return
        self.recording = True
        self.held_modifiers.clear()
        self.entry.set_sensitive(False)
        self.set_response_sensitive(Gtk.ResponseType.OK, False)
        self.record.set_label("Stop recording")
        self.record.grab_focus()
        seat = self.get_display().get_default_seat()
        window = self.get_window()
        if seat and window and seat.grab(window, Gdk.SeatCapabilities.KEYBOARD, False,
                                         None, Gtk.get_current_event(), None, None) == Gdk.GrabStatus.SUCCESS:
            self.capture_seat = seat
        self.instructions.set_text("Press your shortcut. For modifiers alone, hold the keys together and release the trigger key. Choose Stop recording to type it instead.")

    def stop_recording(self, *_):
        if not self.recording:
            return False
        self.recording = False
        self.held_modifiers.clear()
        if self.capture_seat:
            self.capture_seat.ungrab()
            self.capture_seat = None
        self.entry.set_sensitive(True)
        self.set_response_sensitive(Gtk.ResponseType.OK, True)
        self.record.set_label("Record keys")
        self.instructions.set_text(self.TYPING_HELP)
        return False

    def modifiers_at(self, state):
        names = {name for name, mask in MODIFIER_MASKS.items() if state & mask}
        names.update(settings.MODIFIER_KEYS[key] for key in self.held_modifiers.values())
        return names

    def key_pressed(self, _, event):
        if not self.recording:
            return False
        key = Gdk.keyval_name(event.keyval)
        if key in settings.MODIFIER_KEYS:
            self.held_modifiers[event.hardware_keycode] = key
            return True
        if event.is_modifier:
            return True
        ignored = Gdk.ModifierType.LOCK_MASK
        for mask in MODIFIER_MASKS.values():
            ignored |= mask
        keymap = Gdk.Keymap.get_for_display(self.get_display())
        translated, value, *_ = keymap.translate_keyboard_state(
            event.hardware_keycode, event.state & ~ignored, event.group,
        )
        try:
            self.finish_recording(self.modifiers_at(event.state), shortcut_key_name(value if translated else event.keyval))
        except audio.AudioError as error:
            self.instructions.set_text(str(error))
        return True

    def key_released(self, _, event):
        if not self.recording:
            return False
        key = self.held_modifiers.pop(event.hardware_keycode, None)
        if key:
            self.finish_recording(self.modifiers_at(event.state), key)
        return True

    def finish_recording(self, modifiers, key):
        parts = settings.validate_shortcut((*modifiers, key))
        self.stop_recording()
        self.entry.set_text(settings.shortcut_label(parts))
        self.instructions.set_text("Shortcut captured. Save it or record again.")


class SettingsPage(Gtk.ScrolledWindow):
    def __init__(self, app):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.app = app
        self.updating = False
        self.session_backend = shortcuts.backend_for_session()
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, margin=18)
        self.add(self.body)
        self.body.pack_start(label("Settings", "active-title"), False, False, 0)

        self.add_heading("APPEARANCE")
        self.theme = Gtk.ComboBoxText()
        self.theme.append("dark", "Dark")
        self.theme.append("light", "Light")
        self.theme.connect("changed", self.change_theme)
        self.add_row("Color theme", "Applies to the window and menus.", self.theme)

        self.add_heading("BACKGROUND")
        self.background = self.make_switch(settings.set_background)
        self.add_row("Keep app running", "Keep the app available after closing the window.", self.background)
        self.startup = self.make_switch(service.set_startup)
        self.startup_detail = self.add_row("Start audio service at login", "", self.startup)

        self.add_heading("SHORTCUT")
        self.shortcut = self.make_switch(lambda active: shortcuts.set_shortcut(enabled=active))
        self.shortcut_detail = self.add_row("Enable switch shortcut", "", self.shortcut)
        self.record = Gtk.Button(label="Edit shortcut")
        self.record.connect("clicked", self.capture_shortcut)
        self.chord = self.add_row("Key combination", "", self.record)
        setup = Gtk.Box(spacing=8)
        self.manual = Gtk.Button(label="Set up manually")
        self.manual.connect("clicked", lambda _: self.app.change_settings(
            lambda: shortcuts.set_shortcut(enabled=True, manual=True)))
        self.copy = Gtk.Button(label="Copy command")
        self.copy.connect("clicked", self.copy_command)
        setup.pack_start(self.manual, False, False, 0)
        setup.pack_start(self.copy, False, False, 0)
        self.body.pack_start(setup, False, False, 0)
        self.error = Gtk.Label(xalign=0, wrap=True, selectable=True, no_show_all=True)
        self.error.get_style_context().add_class("error-banner")
        self.body.pack_start(self.error, False, False, 0)

    def add_heading(self, text):
        heading = label(text, "eyebrow")
        heading.set_margin_top(7)
        self.body.pack_start(heading, False, False, 0)

    def add_row(self, title, detail, control):
        row = Gtk.Box(spacing=12)
        row.get_style_context().add_class("settings-row")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        texts.pack_start(label(title, "device-title"), False, False, 0)
        description = label(detail, "muted-label")
        description.set_line_wrap(True)
        description.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        texts.pack_start(description, False, False, 0)
        row.pack_start(texts, True, True, 0)
        control.set_valign(Gtk.Align.CENTER)
        control.get_accessible().set_name(title)
        row.pack_end(control, False, False, 0)
        self.body.pack_start(row, False, False, 0)
        return description

    def make_switch(self, callback):
        toggle = Gtk.Switch()
        toggle.connect("notify::active", self.switch_changed, callback)
        return toggle

    def switch_changed(self, toggle, _, callback):
        if not self.updating:
            active = toggle.get_active()
            self.app.change_settings(lambda: callback(active))

    def change_theme(self, combo):
        if not self.updating:
            theme = combo.get_active_id()
            self.app.change_settings(lambda: settings.update(theme=theme))

    def capture_shortcut(self, _):
        dialog = ShortcutDialog(self.get_toplevel(), self.app.preferences.shortcut)
        dialog.connect("response", self.save_shortcut)
        dialog.show_all()

    def save_shortcut(self, dialog, response):
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        try:
            parts = parse_shortcut(dialog.entry.get_text())
        except audio.AudioError as error:
            dialog.instructions.set_text(str(error))
            return
        dialog.destroy()
        self.app.change_settings(lambda: shortcuts.set_shortcut(parts=parts))

    def copy_command(self, _):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(settings.shortcut_command(), -1)
        self.copy.set_label("Copied")
        GLib.timeout_add(1500, self.reset_copy_label)

    def reset_copy_label(self):
        self.copy.set_label("Copy command")
        return GLib.SOURCE_REMOVE

    def render(self, preferences, keep_running, busy, error):
        self.updating = True
        self.theme.set_active_id(preferences.theme)
        self.background.set_active(keep_running)
        self.startup.set_active(bool(preferences.startup))
        self.shortcut.set_active(preferences.shortcut_enabled)
        self.chord.set_text(settings.shortcut_label(preferences.shortcut))
        self.startup_detail.set_text({
            "systemd": "Enabled through your user systemd service.",
            "desktop": "Enabled through desktop autostart for your next sign-in.",
            "portal": "Enabled through your desktop’s background permission.",
        }.get(preferences.startup, "Start the background audio worker with your session."))
        backend = preferences.shortcut_backend
        if backend == "manual":
            shortcut_detail = "Assign the recorded combination to the copied command in your desktop’s shortcut settings."
        elif backend in shortcuts.NATIVE_NAMES:
            name = shortcuts.NATIVE_NAMES[backend]
            shortcut_detail = (f"Installed in {name}. Works with the app closed." if backend == self.session_backend
                               else f"Saved for {name}. Turn it off and on to set it up for this desktop.")
        elif self.session_backend in shortcuts.NATIVE_NAMES:
            shortcut_detail = f"Set up the shortcut automatically in {shortcuts.NATIVE_NAMES[self.session_backend]}."
        else:
            shortcut_detail = "Assign the copied command in your desktop’s shortcut settings."
        self.shortcut_detail.set_text(shortcut_detail)
        for control in (self.theme, self.background, self.startup, self.shortcut, self.record, self.manual):
            control.set_sensitive(not busy)
        self.manual.set_sensitive(not busy and backend != "manual")
        self.error.set_text(error)
        self.error.set_visible(bool(error))
        self.updating = False


class SwitchWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=audio.APP_NAME)
        self.app = app
        self.row_signature = None
        self.updating_volume = False
        self.volume_timer = 0
        self.set_default_size(525, 620)
        self.set_size_request(385, 400)
        self.get_style_context().add_class("switch-window")
        self.connect("destroy", self.stop_volume_timer)

        header = Gtk.HeaderBar(title=audio.APP_NAME, show_close_button=True)
        header.get_style_context().add_class("switch-header")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(audio.ICON_PATH), 22, 22, True)
        header_icon = Gtk.Image.new_from_pixbuf(pixbuf)
        header.pack_start(header_icon)
        tools = Gtk.MenuButton()
        tools.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        tools.set_tooltip_text("QuickSwitch options")
        menu = Gtk.Menu()
        for text, action in (("Reset cycle", app.reset_cycle), ("Quit", app.quit)):
            item = Gtk.MenuItem(label=text)
            item.connect("activate", lambda _, callback=action: callback())
            menu.append(item)
        menu.show_all()
        tools.set_popup(menu)
        header.pack_end(tools)
        self.settings_button = Gtk.ToggleButton(label="Settings")
        self.settings_button.set_tooltip_text("Settings")
        self.settings_button.get_accessible().set_name("Settings")
        self.settings_button.connect("toggled", self.show_page)
        header.pack_end(self.settings_button)
        self.set_titlebar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.add(self.stack)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=18)
        self.stack.add_named(body, "outputs")
        self.preferences_page = SettingsPage(app)
        self.stack.add_named(self.preferences_page, "settings")
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.get_style_context().add_class("hero")
        body.pack_start(hero, False, False, 0)

        active_row = Gtk.Box(spacing=13)
        hero.pack_start(active_row, False, False, 0)
        self.active_icon = Gtk.Image.new_from_icon_name(icon_for(None), Gtk.IconSize.DIALOG)
        self.active_icon.set_pixel_size(31)
        self.active_icon.get_style_context().add_class("active-icon")
        active_row.pack_start(self.active_icon, False, False, 0)
        active_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        active_info.pack_start(label("CURRENT OUTPUT", "eyebrow"), False, False, 0)
        self.active_title = label("Looking for outputs…", "active-title", ellipsize=True)
        self.active_details = label("", "muted-label", ellipsize=True)
        active_info.pack_start(self.active_title, False, False, 0)
        active_info.pack_start(self.active_details, False, False, 0)
        active_row.pack_start(active_info, True, True, 0)

        self.volume_row = Gtk.Box(spacing=8)
        hero.pack_start(self.volume_row, False, False, 0)
        self.mute = Gtk.Button()
        self.mute.set_image(Gtk.Image.new_from_icon_name("audio-volume-muted-symbolic", Gtk.IconSize.BUTTON))
        self.mute.connect("clicked", self.toggle_mute)
        self.volume_row.pack_start(self.mute, False, False, 0)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_draw_value(False)
        self.volume.get_accessible().set_name("Output volume")
        self.volume.connect("value-changed", self.queue_volume)
        self.volume_row.pack_start(self.volume, True, True, 0)
        self.percent = label("—", "percent")
        self.percent.set_width_chars(5)
        self.percent.set_xalign(1)
        self.volume_row.pack_start(self.percent, False, False, 0)

        heading = Gtk.Box(spacing=8, margin_start=4, margin_end=5)
        self.output_heading = label("Playback outputs", "section-title")
        heading.pack_start(self.output_heading, True, True, 0)
        heading.pack_end(label("IN CYCLE", "eyebrow"), False, False, 0)
        body.pack_start(heading, False, False, 0)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller.add(self.rows)
        body.pack_start(scroller, True, True, 0)

        self.error = Gtk.Label(xalign=0, wrap=True, selectable=True, no_show_all=True)
        self.error.get_style_context().add_class("error-banner")
        body.pack_start(self.error, False, False, 0)
        footer = Gtk.Box(spacing=10)
        self.cycle_summary = label("", "muted-label")
        footer.pack_start(self.cycle_summary, True, True, 0)
        self.switch = Gtk.Button(label="Switch output")
        self.switch.get_style_context().add_class("switch-button")
        self.switch.set_tooltip_text("Switch to the next checked output")
        self.switch.connect("clicked", lambda _: app.switch_output())
        footer.pack_end(self.switch, False, False, 0)
        body.pack_start(footer, False, False, 0)
        self.volume_row.set_sensitive(False)
        self.switch.set_sensitive(False)

    def show_page(self, button):
        active = button.get_active()
        button.set_label("Outputs" if active else "Settings")
        button.set_tooltip_text("Back to playback outputs" if active else "Settings")
        self.stack.set_visible_child_name("settings" if active else "outputs")

    def stop_volume_timer(self, *_):
        if self.volume_timer:
            GLib.source_remove(self.volume_timer)
            self.volume_timer = 0

    def queue_volume(self, slider):
        if self.updating_volume or not self.app.state or not self.app.state.active:
            return
        key = self.app.state.active.key
        level = round(slider.get_value())
        self.percent.set_text(f"{level}%")
        self.stop_volume_timer()
        self.volume_timer = GLib.timeout_add(125, self.commit_volume, key, level)

    def commit_volume(self, key, level):
        self.volume_timer = 0
        self.app.perform(lambda: audio.change_volume(key, level))
        return GLib.SOURCE_REMOVE

    def toggle_mute(self, _):
        if self.app.state and self.app.state.active:
            key = self.app.state.active.key
            self.app.perform(lambda: audio.change_volume(key))

    def render(self, state, keys, busy, error):
        active = state.active if state else None
        outputs = state.outputs if state else ()
        self.output_heading.set_text(f"Playback outputs · {len(outputs)}" if outputs else "Playback outputs")
        if active:
            self.active_title.set_text(active.title)
            self.active_details.set_text(active.device or active.kind)
        else:
            self.active_title.set_text("No active output" if state else "Audio unavailable" if error else "Looking for outputs…")
            detail = "Select an output below" if outputs else "Connect an audio output to get started"
            self.active_details.set_text(detail if state else "")
        self.active_icon.set_from_icon_name(icon_for(active), Gtk.IconSize.DIALOG)
        self.active_icon.set_pixel_size(31)
        self.volume_row.set_sensitive(bool(active) and not busy)
        self.rows.set_sensitive(not busy)
        if not self.volume_timer and not busy:
            level = state.volume if active else 0
            self.updating_volume = True
            self.volume.set_value(min(100, level))
            self.updating_volume = False
            self.percent.set_text(f"{level}%" if active else "—")
        muted = state.muted if active else False
        mute_icon = "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        self.mute.get_image().set_from_icon_name(mute_icon, Gtk.IconSize.BUTTON)
        self.mute.set_tooltip_text("Unmute output" if muted else "Mute output")
        count = sum(output.key in keys for output in outputs)
        self.cycle_summary.set_text(f"{count} outputs in cycle" if count >= 2 else "Check two outputs to cycle")
        self.switch.set_sensitive(count >= 2 and not busy)
        self.error.set_text(error)
        self.error.set_visible(bool(error))

        signature = outputs, active, frozenset(keys)
        if signature != self.row_signature:
            self.row_signature = signature
            for child in self.rows.get_children():
                child.destroy()
            if not outputs:
                self.rows.add(label("No playback outputs detected.", "muted-label"))
            for output in outputs:
                self.rows.add(self.output_row(output, output == active, output.key in keys))
            self.rows.show_all()

    def output_row(self, output, active, checked):
        row = Gtk.Box(spacing=9)
        row.get_style_context().add_class("device-row")
        if active:
            row.get_style_context().add_class("selected-row")
        select = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        select.get_style_context().add_class("device-button")
        select.connect("clicked", lambda _: self.app.switch_output(output.key))
        details = Gtk.Box(spacing=10)
        glyph = Gtk.Image.new_from_icon_name(icon_for(output), Gtk.IconSize.BUTTON)
        glyph.set_pixel_size(20)
        details.pack_start(glyph, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.pack_start(label(output.title, "device-title", ellipsize=True), False, False, 0)
        summary = " · ".join(filter(None, (output.device, output.kind)))
        if not output.jack_available:
            summary += " · Jack reported unplugged"
        texts.pack_start(label(summary, "muted-label", ellipsize=True), False, False, 0)
        details.pack_start(texts, True, True, 0)
        if active:
            badge = label("ACTIVE", "active-badge")
            badge.set_valign(Gtk.Align.CENTER)
            details.pack_end(badge, False, False, 0)
        select.add(details)
        select.set_tooltip_text(f"Select {output.title} · {summary}")
        row.pack_start(select, True, True, 0)
        checkbox = Gtk.CheckButton(active=checked, valign=Gtk.Align.CENTER, margin_end=12)
        checkbox.set_tooltip_text(f"Include {output.title} in the cycle")
        checkbox.get_accessible().set_name(f"Include {output.title} in the cycle")
        checkbox.connect("toggled", lambda button: self.app.set_cycle_member(output.key, button.get_active()))
        row.pack_end(checkbox, False, False, 0)
        return row


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=audio.APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.state = None
        self.keys = set()
        self.busy = False
        self.reading = False
        self.refresh_again = False
        self.read_error = ""
        self.action_error = ""
        self.refresh_timer = 0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio")
        self.listener = None
        self.closed = False
        self.indicator = None
        self.tray_ready = False
        self.tray_watch = 0
        self.tray_signature = None
        self.tray_requested = False
        self.keepalive = False
        self.preferences = settings.Preferences()
        self.settings_error = ""
        self.theme_provider = None
        self.applied_theme = None
        self.poll_timer = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        try:
            self.preferences = settings.load()
        except audio.AudioError as error:
            self.settings_error = str(error)
        self.apply_theme()
        Gtk.Window.set_default_icon_from_file(str(audio.ICON_PATH))
        self.setup_tray()
        self.update_keepalive()
        self.subscribe()
        self.poll_timer = GLib.timeout_add_seconds(2, self.poll_audio)
        self.queue_refresh()

    def do_command_line(self, line):
        if line.get_arguments()[1:] == ["tray"] and self.indicator:
            self.tray_requested = True
            self.update_keepalive()
            if not self.tray_ready:
                self.hold()
                GLib.timeout_add(1500, self.finish_tray_start)
        else:
            self.activate()
        return 0

    def finish_tray_start(self):
        if not self.tray_ready:
            self.activate()
        self.release()
        return GLib.SOURCE_REMOVE

    def do_activate(self):
        if self.window is None:
            self.window = SwitchWindow(self)
            self.window.connect("destroy", self.clear_window)
            self.window.show_all()
            self.render()
        self.tray_requested = False
        self.update_keepalive()
        self.window.present()
        self.queue_refresh()

    def clear_window(self, _):
        self.window = None

    def do_shutdown(self):
        self.closed = True
        if self.listener:
            self.listener.force_exit()
        if self.tray_watch:
            Gio.bus_unwatch_name(self.tray_watch)
        if self.poll_timer:
            GLib.source_remove(self.poll_timer)
        self.worker.shutdown()
        Gtk.Application.do_shutdown(self)

    def setup_tray(self):
        from gi import repository

        for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(namespace, "0.1")
                Indicator = getattr(repository, namespace)
            except (ImportError, ValueError, AttributeError):
                continue
            break
        else:
            return
        self.indicator = Indicator.Indicator.new_with_path(
            audio.APP_SLUG, audio.APP_SLUG, Indicator.IndicatorCategory.HARDWARE, str(audio.ICON_DIR),
        )
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="Open QuickSwitch Audio")
        open_item.connect("activate", lambda _: self.activate())
        menu.append(open_item)
        settings_item = Gtk.MenuItem(label="Settings")
        settings_item.connect("activate", lambda _: self.open_settings())
        menu.append(settings_item)
        self.tray_switch = Gtk.MenuItem(label="Switch output")
        self.tray_switch.set_sensitive(False)
        self.tray_switch.connect("activate", lambda _: self.switch_output())
        menu.append(self.tray_switch)
        self.tray_select = Gtk.MenuItem(label="Playback outputs")
        self.tray_select.set_sensitive(False)
        self.tray_devices = Gtk.Menu()
        self.tray_select.set_submenu(self.tray_devices)
        menu.append(self.tray_select)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self.quit())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)
        self.indicator.set_secondary_activate_target(self.tray_switch)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.tray_watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            self.tray_appeared, self.tray_vanished,
        )

    def tray_appeared(self, *_):
        self.tray_ready = True
        self.update_keepalive()
        self.render()

    def tray_vanished(self, *_):
        self.tray_ready = False
        self.update_keepalive()
        self.render()

    def keep_running(self):
        preference = self.preferences.background
        return self.tray_ready if preference is None else preference

    def update_keepalive(self):
        keep = self.keep_running() or (self.tray_requested and self.tray_ready)
        if keep != self.keepalive:
            self.keepalive = keep
            self.hold() if keep else self.release()

    def open_settings(self):
        self.activate()
        self.window.settings_button.set_active(True)

    def apply_theme(self):
        theme = self.preferences.theme
        if theme == self.applied_theme:
            return
        screen = Gdk.Screen.get_default()
        if self.theme_provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, self.theme_provider)
        css = (audio.ICON_DIR / "style.css").read_bytes()
        if theme == "light":
            css += b"\n" + (audio.ICON_DIR / "style-light.css").read_bytes()
        self.theme_provider = Gtk.CssProvider()
        self.theme_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(screen, self.theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", theme == "dark")
        self.applied_theme = theme

    def change_settings(self, action):
        if self.closed or self.busy:
            return
        self.busy = True
        self.settings_error = ""
        self.render()
        self.worker.submit(action).add_done_callback(lambda future: GLib.idle_add(self.finish_settings, future))

    def finish_settings(self, future):
        self.busy = False
        if not self.closed:
            try:
                self.preferences = future.result()
            except (audio.AudioError, OSError, UnicodeError, GLib.Error) as error:
                self.settings_error = str(error)
            self.apply_theme()
            self.update_keepalive()
            self.render()
            self.queue_refresh()
        return GLib.SOURCE_REMOVE

    def poll_audio(self):
        if self.listener is None or (self.state and self.state.backend == "pipewire"):
            self.queue_refresh()
        return GLib.SOURCE_CONTINUE

    def subscribe(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        launcher = Gio.SubprocessLauncher.new(flags)
        launcher.setenv("LC_ALL", "C", True)
        try:
            self.listener = launcher.spawnv(["pactl", "subscribe"])
        except GLib.Error:
            GLib.timeout_add_seconds(4, self.subscribe)
        else:
            stream = Gio.DataInputStream.new(self.listener.get_stdout_pipe())
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.read_event)
            self.queue_refresh()
        return GLib.SOURCE_REMOVE

    def read_event(self, stream, result):
        if self.closed:
            return
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:
            self.listener = None
            self.queue_refresh()
            GLib.timeout_add_seconds(4, self.subscribe)
            return
        if any(f" on {kind} " in line for kind in ("sink", "card", "server")):
            self.queue_refresh()
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.read_event)

    def queue_refresh(self):
        if not self.closed and not self.refresh_timer:
            self.refresh_timer = GLib.timeout_add(120, self.refresh)

    def refresh(self):
        self.refresh_timer = 0
        if self.closed or self.busy:
            return GLib.SOURCE_REMOVE
        if self.reading:
            self.refresh_again = True
            return GLib.SOURCE_REMOVE
        self.reading = True
        self.worker.submit(self.read_state).add_done_callback(lambda future: GLib.idle_add(self.finish_refresh, future))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def read_state():
        state = audio.snapshot()
        try:
            return state, audio.cycle_keys(state), ""
        except audio.AudioError as error:
            return state, set(), str(error)

    def finish_refresh(self, future):
        self.reading = False
        if not self.closed:
            try:
                self.state, self.keys, self.read_error = future.result()
            except audio.AudioError as error:
                self.state, self.keys, self.read_error = None, set(), str(error)
            self.render()
            if self.refresh_again:
                self.refresh_again = False
                self.queue_refresh()
        return GLib.SOURCE_REMOVE

    def perform(self, action):
        if self.closed or self.busy:
            return
        self.busy = True
        self.action_error = ""
        if self.window:
            self.window.stop_volume_timer()
        self.render()

        def update():
            action()
            return self.read_state()

        self.worker.submit(update).add_done_callback(lambda future: GLib.idle_add(self.finish_action, future))

    def finish_action(self, future):
        self.busy = False
        if not self.closed:
            try:
                self.state, self.keys, self.read_error = future.result()
            except audio.AudioError as error:
                self.action_error = str(error)
                if self.window is None:
                    self.activate()
                self.window.row_signature = None
            self.render()
            self.queue_refresh()
        return GLib.SOURCE_REMOVE

    def switch_output(self, key=None):
        def switch():
            audio.notify(service.switch_output(key))
        self.perform(switch)

    def reset_cycle(self):
        self.perform(audio.reset_cycle)

    def set_cycle_member(self, key, enabled):
        self.perform(lambda: audio.set_cycle_member(key, enabled))

    def render(self):
        if self.window:
            self.window.render(self.state, self.keys, self.busy, self.action_error or self.read_error)
            self.window.preferences_page.render(self.preferences, self.keep_running(), self.busy, self.settings_error)
        if not self.indicator:
            return
        outputs = self.state.outputs if self.state else ()
        active = self.state.active if self.state else None
        self.indicator.set_title(f"{audio.APP_NAME} · {active.title}" if active else audio.APP_NAME)
        self.tray_switch.set_sensitive(not self.busy and sum(output.key in self.keys for output in outputs) >= 2)
        self.tray_select.set_sensitive(not self.busy and bool(outputs))
        signature = outputs, active
        if signature != self.tray_signature:
            self.tray_signature = signature
            for child in self.tray_devices.get_children():
                child.destroy()
            for output in outputs:
                text = f"{output.title} · {output.device}" if output.device else output.title
                item = Gtk.CheckMenuItem(label=text, active=(output == active), draw_as_radio=True)
                item.connect("activate", lambda _, key=output.key: self.switch_output(key))
                self.tray_devices.append(item)
            self.tray_devices.show_all()
