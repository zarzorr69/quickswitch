#!/usr/bin/env bash
set -euo pipefail

app_id=io.github.zarzorr69.quickswitch
export XDG_RUNTIME_DIR
XDG_RUNTIME_DIR=$(mktemp -d)
trap 'flatpak kill "$app_id" 2>/dev/null || true; pulseaudio --kill 2>/dev/null || true' EXIT

flatpak install --user --noninteractive --assumeyes ./QuickSwitch-x86_64.flatpak
flatpak run "$app_id" --help
flatpak run --command=sh "$app_id" -c 'pactl --version && amixer --version && python3 -c "import gi; gi.require_version(\"Gtk\", \"3.0\"); gi.require_version(\"AppIndicator3\", \"0.1\"); from gi.repository import Gtk, AppIndicator3"'

pulseaudio --start --exit-idle-time=-1 --log-level=error
pactl load-module module-null-sink sink_name=qs_speakers sink_properties=device.description=Speakers
pactl load-module module-null-sink sink_name=qs_headphones sink_properties=device.description=Headphones
pactl set-default-sink qs_speakers
flatpak run "$app_id" status
flatpak run "$app_id" toggle
test "$(pactl get-default-sink)" = qs_headphones

flatpak run --command=sh "$app_id" -c 'cd /app/share/quickswitch-audio && python3 -c "from quickswitch_audio import settings; settings.update(shortcut_backend=\"manual\")"'
flatpak run "$app_id" shortcut
test "$(pactl get-default-sink)" = qs_speakers

flatpak run "$app_id" daemon &
worker=$!
socket="$XDG_RUNTIME_DIR/app/$app_id/quickswitch-audio/control.sock"
for attempt in {1..50}; do
  if test -S "$socket"; then
    break
  fi
  sleep 0.1
done
test -S "$socket"
flatpak run "$app_id" shortcut
test "$(pactl get-default-sink)" = qs_headphones
flatpak run --command=sh "$app_id" -c 'cd /app/share/quickswitch-audio && python3 -c "from quickswitch_audio import service; assert service.request(\"stop\") == {\"stopping\": True}"'
wait "$worker"
