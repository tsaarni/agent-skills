---
name: konsole-screenshot
description: Open a new Konsole terminal, run a command, take a screenshot, and close. Use for capturing terminal output as images.
argument-hint: <output_file> <command to run>
---

# Konsole Screenshot

Run a command in a new Konsole terminal window, capture a pixel-perfect screenshot of that specific window (no focus dependency), save it to a file, and close the terminal.

## Usage

```shell
$SKILL_DIR/scripts/konsole-screenshot.py <output_file.png> <command...>
```

## How it works

1. Launches a new Konsole window with `--separate --hold`
2. Waits for the Konsole dbus service `org.kde.konsole-<PID>` to appear
3. Finds the window's KWin internal UUID via a KWin script (journal output)
4. Captures that specific window using `org.kde.KWin.ScreenShot2.CaptureWindow` dbus API — targets by UUID, no focus/activation needed
5. Converts raw BGRA pixel data to PNG using `ffmpeg`
6. Quits Konsole cleanly via dbus

## One-time setup

The KWin ScreenShot2 API requires authorization via a `.desktop` file. Install the override:

```shell
cp $SKILL_DIR/python3.12.desktop ~/.local/share/applications/
kbuildsycoca5
```

This adds `X-KDE-DBUS-Restricted-Interfaces=org.kde.kwin.Screenshot,org.kde.KWin.ScreenShot2` to the Python desktop entry so KWin authorizes screenshot calls from Python processes.

**Important**: The `.desktop` file is Python interpreter version-specific — `python3.12.desktop` targets the `python3.12` executable. If you use a different Python version, copy and rename the file to match (e.g., `python3.11.desktop`) and update the `Exec=` and `TryExec=` fields inside it accordingly.

## Dependencies

- `python3` with `dbus-python` module
- `konsole` — KDE terminal emulator
- `qdbus` — for Konsole lifecycle management
- `ffmpeg` — for raw pixel data to PNG conversion
- `journalctl` — for reading KWin script output

## Examples

```shell
# Capture output of a command
$SKILL_DIR/scripts/konsole-screenshot.py /tmp/output.png "echo Hello; ls --color"

# Capture a program's colored output
$SKILL_DIR/scripts/konsole-screenshot.py /tmp/status.png "git status"
```
