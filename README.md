# QuickSwitch

A Linux playback switcher inspired by [SoundSwitch](https://soundswitch.aaflalo.me/). It discovers the outputs available to your audio server, including onboard speakers and jacks, USB audio, Bluetooth, HDMI and DisplayPort. Pick an output directly or check the outputs you want to cycle through.

## Install or upgrade

### Flatpak

Install Flatpak through your distribution. On CachyOS and Arch, run `sudo pacman -S --needed flatpak`. Then install the x86_64 download from this project's GitHub releases.

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && flatpak install --user https://github.com/zarzorr69/quickswitch/releases/latest/download/QuickSwitch-x86_64.flatpak
```

Before switching from a native install, turn off its login service and shortcut in Settings, then quit it. Open the Flatpak from your launcher or run `flatpak run io.github.zarzorr69.quickswitch`. PipeWire sessions need their PulseAudio compatibility service, usually `pipewire-pulse`. The package includes Python, GTK, `pactl`, and `amixer` through its runtime and bundled tools.

In Settings, enable the switch shortcut and use **Copy command** to assign `flatpak run io.github.zarzorr69.quickswitch shortcut` to your preferred keys in your desktop's shortcut settings. That command works with the window and background worker closed. The Flatpak keeps its preferences in `~/.var/app/io.github.zarzorr69.quickswitch/config/quickswitch-audio`.

**Keep app running** and **Start audio service at login** request permission through the desktop background portal. Login startup starts the worker on your next sign-in. The Flatpak uses this portal instead of installing a host systemd unit or editing compositor configuration. Availability depends on your installed desktop portal backend. The native installer continues to offer systemd startup and automatic desktop shortcut setup.

The package requests desktop display, rendering, and audio access. It uses the audio permission for device discovery, volume, routing, and the Realtek ALC897 mixer controls. Tray support depends on available AppIndicator support and a compatible desktop tray host.

The GitHub Actions **Flatpak** workflow builds the bundle and publishes each new version on the project's Releases page. To build locally, install `flatpak-builder`, clone this repository, and run the following commands from the source directory.

```sh
git submodule update --init --recursive
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --install-deps-from=flathub --disable-rofiles-fuse --force-clean --repo=flatpak-repo --default-branch=stable build-flatpak packaging/flatpak/io.github.zarzorr69.quickswitch.json
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo flatpak-repo QuickSwitch-x86_64.flatpak io.github.zarzorr69.quickswitch stable
```

This package is distributed through GitHub. A Flathub listing requires separate review. Flathub's [generative AI policy](https://docs.flathub.org/docs/for-app-authors/requirements#generative-ai-policy) requires human-authored submission manifests and submission interactions. The application code, this packaging, and documentation were created with AI assistance. This manifest is for the project's own builds and cannot be submitted to Flathub under that policy. A human maintainer must also select and document the application's source license before submitting it.

### Arch, CachyOS, and Manjaro

Install `base-devel` and `git` with `sudo pacman -S --needed base-devel git`, then build and install the `quickswitch` package as your regular user.

```sh
git clone https://github.com/zarzorr69/quickswitch.git && cd quickswitch/packaging/aur && makepkg -si
```

If you previously used `install.py`, quit QuickSwitch Audio and run `python3 install.py --uninstall` from your original checkout before installing the package. The uninstaller keeps your saved theme, key combination, and output cycle. Re-enable the shortcut and login service from Settings if you use them.

### Per-user installation

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

If you used the per-user installer and `~/.local/bin` is outside your `PATH`, use `~/.local/bin/quickswitch-audio` in the commands below.

| Command | Action |
| --- | --- |
| `quickswitch-audio` | Open the window |
| `quickswitch-audio tray` | Open in the tray where available |
| `quickswitch-audio status` | List detected outputs and cycle membership |
| `quickswitch-audio toggle` | Switch to the next checked output |
| `quickswitch-audio shortcut` | Switch when the shortcut setting is enabled |
| `systemctl --user status quickswitch-audio.service` | Inspect the service after enabling it in Settings on systemd |

### Uninstall

- **Flatpak.** Turn off login startup in Settings, quit the app, and run `flatpak uninstall --user io.github.zarzorr69.quickswitch`. Remove its custom shortcut in your desktop settings.
- **Arch package.** Turn off the switch shortcut and login service in Settings, quit the app, and run `sudo pacman -R quickswitch`.
- **Per-user install.** Quit the app and run `python3 install.py --uninstall` from the source folder. The uninstaller removes app-managed shortcuts and startup entries.

Remove any shortcut you added manually in your desktop settings. Your saved theme, key combination, and output cycle remain in `~/.config/quickswitch-audio` or the equivalent directory under `$XDG_CONFIG_HOME`.
