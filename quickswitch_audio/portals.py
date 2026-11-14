"""Desktop integration for the Flatpak build."""

from uuid import uuid4

from gi.repository import Gio, GLib

from .audio import APP_SLUG, AudioError


BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"


def background_request(autostart):
    context = GLib.MainContext.new()
    context.push_thread_default()
    connection = None
    subscription = None
    timer = None
    request_path = None
    results = []
    loop = GLib.MainLoop.new(context, False)

    def response(_connection, _sender, _path, _interface, _signal, parameters):
        results.append(parameters.unpack())
        loop.quit()

    def expired(*_):
        loop.quit()
        return GLib.SOURCE_REMOVE

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        token = "quickswitch_" + uuid4().hex
        sender = connection.get_unique_name().lstrip(":").replace(".", "_")
        request_path = f"{OBJECT_PATH}/request/{sender}/{token}"
        subscription = connection.signal_subscribe(
            BUS_NAME, REQUEST_INTERFACE, "Response", request_path, None,
            Gio.DBusSignalFlags.NONE, response,
        )
        options = {
            "handle_token": GLib.Variant("s", token),
            "reason": GLib.Variant("s", "Switch audio outputs while the window is closed"),
            "autostart": GLib.Variant("b", autostart),
            "commandline": GLib.Variant("as", [APP_SLUG, "daemon"]),
        }
        connection.call_sync(
            BUS_NAME, OBJECT_PATH, "org.freedesktop.portal.Background", "RequestBackground",
            GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType.new("(o)"),
            Gio.DBusCallFlags.NONE, 10000, None,
        )
        timer = GLib.timeout_source_new(120000)
        timer.set_callback(expired)
        timer.attach(context)
        if not results:
            loop.run()
        if not results:
            raise AudioError("The desktop did not answer the background request. Try again in Settings.")
        status, values = results[0]
        if status:
            raise AudioError("The desktop declined the background request.")
        return values
    except GLib.Error as error:
        raise AudioError("The desktop background portal is unavailable. Set up autostart in your desktop settings.") from error
    finally:
        if timer is not None:
            timer.destroy()
        if connection is not None and subscription is not None:
            connection.signal_unsubscribe(subscription)
        if connection is not None and request_path is not None and not results:
            connection.call(
                BUS_NAME, request_path, REQUEST_INTERFACE, "Close", None, None,
                Gio.DBusCallFlags.NONE, 2000, None, None, None,
            )
        context.pop_thread_default()


def require_background(autostart, *, required=True):
    values = background_request(autostart)
    if required and not values.get("background"):
        raise AudioError("Allow background activity in the desktop prompt, then try again.")
    if bool(values.get("autostart")) != autostart:
        raise AudioError("The desktop did not apply the requested login setting.")


def notify(output):
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        connection.call_sync(
            BUS_NAME, OBJECT_PATH, "org.freedesktop.portal.Notification", "AddNotification",
            GLib.Variant("(sa{sv})", (APP_SLUG, {
                "title": GLib.Variant("s", output.title),
                "body": GLib.Variant("s", output.device or output.kind),
            })), None, Gio.DBusCallFlags.NONE, 2000, None,
        )
    except GLib.Error:
        pass
