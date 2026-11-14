"""Run with dbus-run-session -- python3 -m unittest discover -s tests."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gio, GLib

os.environ["FLATPAK_ID"] = "io.github.zarzorr69.quickswitch"

from quickswitch_audio import audio, portals, service, settings, shortcuts


class FlatpakIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready = threading.Event()
        cls.allowed = True
        cls.cancelled = False
        cls.autostart = False
        cls.loop = GLib.MainLoop()
        node = Gio.DBusNodeInfo.new_for_xml('''
          <node><interface name="org.freedesktop.portal.Background">
            <method name="RequestBackground">
              <arg type="s" direction="in"/>
              <arg type="a{sv}" direction="in"/>
              <arg type="o" direction="out"/>
            </method>
          </interface></node>''')

        def acquired(connection, _):
            connection.register_object(portals.OBJECT_PATH, node.interfaces[0], cls.handle, None, None)
            cls.ready.set()

        cls.owner = Gio.bus_own_name(
            Gio.BusType.SESSION, portals.BUS_NAME, Gio.BusNameOwnerFlags.NONE, acquired, None, None,
        )
        cls.thread = threading.Thread(target=cls.loop.run, daemon=True)
        cls.thread.start()
        if not cls.ready.wait(5):
            raise RuntimeError("The private test bus did not start.")

    @classmethod
    def tearDownClass(cls):
        Gio.bus_unown_name(cls.owner)
        cls.loop.quit()
        cls.thread.join(2)

    @classmethod
    def handle(cls, connection, sender, _path, _interface, _method, parameters, invocation):
        _, options = parameters.unpack()
        token = options["handle_token"]
        path = f"{portals.OBJECT_PATH}/request/{sender.lstrip(':').replace('.', '_')}/{token}"
        invocation.return_value(GLib.Variant("(o)", (path,)))
        if not cls.cancelled:
            cls.autostart = options["autostart"] and cls.allowed

        def respond():
            connection.emit_signal(
                sender, path, portals.REQUEST_INTERFACE, "Response",
                GLib.Variant("(ua{sv})", (1 if cls.cancelled else 0, {
                    "background": GLib.Variant("b", cls.allowed),
                    "autostart": GLib.Variant("b", cls.autostart),
                })),
            )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(respond)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": self.folder.name,
            "XDG_RUNTIME_DIR": self.folder.name,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        type(self).allowed = True
        type(self).cancelled = False
        type(self).autostart = False
        settings.save(settings.Preferences())

    def test_background_keeps_autostart_and_can_be_disabled_after_denial(self):
        self.assertEqual(service.set_startup(True).startup, "portal")
        self.assertTrue(type(self).autostart)
        self.assertTrue(settings.set_background(True).background)
        self.assertTrue(type(self).autostart)
        type(self).allowed = False
        with patch.object(service, "request", return_value=None):
            self.assertEqual(service.set_startup(False).startup, "")
        self.assertFalse(type(self).autostart)

    def test_denied_or_cancelled_requests_preserve_preferences(self):
        previous = settings.load()
        type(self).allowed = False
        with self.assertRaises(audio.AudioError):
            service.set_startup(True)
        self.assertEqual(settings.load(), previous)
        type(self).allowed = True
        type(self).cancelled = True
        with self.assertRaises(audio.AudioError):
            settings.set_background(True)
        self.assertEqual(settings.load(), previous)

    def test_flatpak_shortcut_and_worker_share_the_app_namespace(self):
        self.assertEqual(settings.shortcut_command(), "flatpak run io.github.zarzorr69.quickswitch shortcut")
        self.assertEqual(shortcuts.backend_for_session(), "manual")
        expected = Path(self.folder.name) / "app/io.github.zarzorr69.quickswitch/quickswitch-audio/control.sock"
        self.assertEqual(service.socket_path(), expected)

    def test_sysfs_codec_detection_without_host_proc(self):
        root = Path(self.folder.name)
        for name, vendor in (("hdaudioC12D2", "0x10ec0897"), ("hdaudioC1D0", "0x10ec0295")):
            (root / name).mkdir()
            (root / name / "vendor_id").write_text(vendor + "\n")
        with patch.object(audio, "CODEC_PATH", root / "empty"), patch.object(audio, "SYSFS_CODECS", root):
            self.assertEqual(audio.realtek_cards(), {"12"})


if __name__ == "__main__":
    unittest.main()
