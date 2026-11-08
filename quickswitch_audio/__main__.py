import argparse
import sys

from . import audio, service, settings


def main():
    parser = argparse.ArgumentParser(description="QuickSwitch Audio for PipeWire and PulseAudio")
    parser.add_argument("action", nargs="?", default="show",
                        choices=("show", "tray", "toggle", "shortcut", "status", "daemon"))
    action = parser.parse_args().action
    try:
        if action in ("toggle", "shortcut"):
            if action == "shortcut" and not settings.load().shortcut_enabled:
                return 0
            output = service.switch_output()
            print(output.title)
            audio.notify(output)
        elif action == "daemon":
            service.run()
        elif action == "status":
            state = audio.snapshot()
            if not state.outputs:
                print("No playback outputs detected.")
                return 0
            selected = audio.cycle_keys(state)
            for output in state.outputs:
                tags = [label for flag, label in (
                    (output == state.active, "active"), (output.key in selected, "in cycle"),
                ) if flag]
                suffix = f" [{', '.join(tags)}]" if tags else ""
                device = f" · {output.device}" if output.device else ""
                print(f"{output.title}{device}{suffix}")
        else:
            try:
                from .app import Application
            except (ImportError, ValueError) as error:
                raise audio.AudioError("GTK 3 and Python GObject are required. See README.md.") from error
            return Application().run([audio.APP_SLUG, action])
    except audio.AudioError as error:
        print(f"{audio.APP_NAME}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
