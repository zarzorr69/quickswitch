# QuickSwitch

A Linux playback switcher inspired by [SoundSwitch](https://soundswitch.aaflalo.me/). It discovers the outputs available to your audio server, including onboard speakers and jacks, USB audio, Bluetooth, HDMI and DisplayPort. Pick an output directly or check the outputs you want to cycle through.

## Install or upgrade

QuickSwitch Audio uses Python 3.8 or newer, PyGObject, GTK 3, and a running PipeWire or PulseAudio session. Install the dependencies with your distribution’s package manager. These commands cover three common package families.

| Distribution | Dependencies |
| --- | --- |
| CachyOS, Arch, Manjaro | `sudo pacman -S --needed python python-gobject gtk3 libpulse alsa-utils libnotify libayatana-appindicator` |
| Debian, Ubuntu, Linux Mint | `sudo apt install python3-gi gir1.2-gtk-3.0 pulseaudio-utils alsa-utils libnotify-bin gir1.2-ayatanaappindicator3-0.1` |
| Fedora | `sudo dnf install python3-gobject gtk3 pulseaudio-utils alsa-utils libnotify libayatana-appindicator-gtk3` |

On other distributions, install the packages that provide GTK 3 with its introspection files, Python `gi`, and `pactl`. Native PipeWire sessions can use `pw-dump` and WirePlumber’s `wpctl` instead. `amixer` enables the ALC897 jack workaround. AppIndicator support enables the tray on desktops with a compatible tray host, and `notify-send` enables switch notifications.

Use the Python interpreter that can import your distro’s `gi` package.

```sh
git clone https://github.com/zarzorr69/quickswitch.git
cd quickswitch
python3 install.py
```

Open **QuickSwitch Audio** from your launcher or run `~/.local/bin/quickswitch-audio`. When upgrading from v0.1, quit the old window and tray before installing. Your existing cycle preferences carry over.

## Playback outputs

The main page updates when devices connect, disconnect, or change through your desktop’s sound controls. It lists the playback outputs exposed by the active audio server and available ports when PulseAudio-compatible routing is in use. Devices whose card profile is off can be enabled in your desktop’s sound settings. The automatic dummy sink is hidden.

- Click a row to use that output immediately. Use the volume slider or mute button for the current output.
- Check at least two outputs under **In cycle**. The shortcut and **Switch output** visit them in list order. Unplugged members remain in your saved cycle and rejoin when their output returns.
- For an ALC897 with the two analog ports, **Line Out** and **Headphones** are the automatic pair. The app applies the original `Front` and `Headphone` mixer sequence when those ALSA controls are available. A physical dial bound to the system default output follows the switch.

Standalone ALSA without a session audio server does not expose a desktop-wide default output. Use PipeWire or PulseAudio to switch the outputs of that hardware from the app.

## Settings

Click **Settings** in the title bar. Each control is saved for your user account.

| Setting | Effect |
| --- | --- |
| **Color theme** | Switch between Light and Dark immediately. |
| **Keep app running** | Keep the window app available after closing it. Reopen from the launcher or from the tray on desktops that provide one. Choose **Quit** from the menu to exit the window app. |
| **Start audio service at login** | Enable a systemd user service that runs without a window or tray. On sessions without a systemd user manager, it uses XDG desktop autostart when the desktop honors it. Turning the setting off disables startup and stops the worker. |
| **Enable switch shortcut** | Let the desktop trigger the standalone switch command. It can switch with the app and audio service closed. |

### Set a custom shortcut

1. Click **Edit shortcut** and type a combination such as `Super+Ctrl+F9`, or click **Record keys** and press the combination. Click **Save shortcut** to apply it. You can edit it while the shortcut is enabled. On Hyprland, a modifier-only combination like `Super+Ctrl` switches when you release Left Ctrl while holding Super. Record Right Ctrl or type `Super+Control_R` to use that side.
2. Turn on **Enable switch shortcut**. In Hyprland, Sway, GNOME, Cinnamon, or Xfce the app tries to install the binding in the desktop. It keeps the app’s binding separate from your other shortcuts. If the combination is in use, choose a different one.
3. On KDE Plasma and other desktops, choose **Set up manually** and **Copy command**, then assign the recorded combination to the copied command in the desktop’s keyboard shortcut settings. Use manual setup on any desktop where automatic setup is unavailable or the compositor config is read-only.

For a compositor with text-only shortcut configuration, bind your chosen combination to the copied command in its config. The command ends in `shortcut` and checks the app’s enable switch every time it runs. This lets you turn it off from Settings even when the desktop binding was added manually. If you previously bound `quickswitch-audio toggle`, replace or remove that older bind before reusing its keys. The `toggle` command still works independently for scripts.

### Commands

| Command | Action |
| --- | --- |
| `~/.local/bin/quickswitch-audio` | Open the window |
| `~/.local/bin/quickswitch-audio tray` | Open in the tray where available |
| `~/.local/bin/quickswitch-audio status` | List detected outputs and cycle membership |
| `~/.local/bin/quickswitch-audio toggle` | Switch to the next checked output |
| `~/.local/bin/quickswitch-audio shortcut` | Switch when the shortcut setting is enabled |
| `systemctl --user status quickswitch-audio.service` | Inspect the service after enabling it in Settings on systemd |

To uninstall, quit the window app and run `python3 install.py --uninstall` from the extracted folder. The uninstaller removes app-managed shortcuts and startup entries. Remove any shortcut you added manually in your desktop settings. Your saved theme, key combination, and output cycle remain in `~/.config/quickswitch-audio` or the equivalent directory under `$XDG_CONFIG_HOME`.
