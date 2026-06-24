#!/usr/bin/env python3
"""
Konsole Screenshot Tool

Opens a new Konsole terminal, runs a command, takes a screenshot of that
specific window (by UUID, no focus required), saves it to a file, and closes
the terminal.

Uses KWin's ScreenShot2 dbus interface for pixel-perfect window capture.

Prerequisites:
  - Install the .desktop file for authorization (one-time setup):
    cp $SKILL_DIR/python3.12.desktop ~/.local/share/applications/
    kbuildsycoca5
"""

import dbus
import os
import struct
import subprocess
import sys
import tempfile
import time


def get_window_uuid_by_pid(pid, timeout=5):
    """Get the KWin internal UUID for a window owned by the given PID."""
    bus = dbus.SessionBus()
    kwin_script = dbus.Interface(
        bus.get_object('org.kde.KWin', '/Scripting'),
        'org.kde.kwin.Scripting'
    )

    marker = f"KONSOLE_SCREENSHOT_{os.getpid()}"
    script = f'''
var clients = workspace.clientList();
for (var i = 0; i < clients.length; i++) {{
    var c = clients[i];
    if (c.pid === {pid}) {{
        print("{marker}:" + c.internalId);
    }}
}}
'''
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False)
    f.write(script)
    f.close()

    kwin_script.loadScript(f.name)
    kwin_script.start()
    time.sleep(0.3)
    os.unlink(f.name)

    # Read UUID from journal
    result = subprocess.run(
        ['journalctl', '-n', '50', '--no-pager', '-t', 'kwin_x11'],
        capture_output=True, text=True
    )
    # Also check without -t filter
    if marker not in result.stdout:
        result = subprocess.run(
            ['journalctl', '-n', '100', '--no-pager'],
            capture_output=True, text=True
        )

    for line in result.stdout.splitlines():
        if marker in line:
            uuid = line.split(marker + ":")[1].strip()
            return uuid

    return None


def capture_window(uuid, output_file):
    """Capture a window screenshot via KWin ScreenShot2 dbus interface."""
    bus = dbus.SessionBus()
    screenshot = dbus.Interface(
        bus.get_object('org.kde.KWin', '/org/kde/KWin/ScreenShot2'),
        'org.kde.KWin.ScreenShot2'
    )

    read_fd, write_fd = os.pipe()
    options = dbus.Dictionary({
        'include-decoration': dbus.Boolean(True),
        'include-cursor': dbus.Boolean(False),
        'include-shadow': dbus.Boolean(False),
    }, signature='sv')

    try:
        results = screenshot.CaptureWindow(uuid, options, dbus.types.UnixFd(write_fd))
    finally:
        os.close(write_fd)

    width = int(results['width'])
    height = int(results['height'])
    stride = int(results['stride'])
    fmt = int(results['format'])

    with os.fdopen(read_fd, 'rb') as f:
        data = f.read(stride * height)

    # Convert raw ARGB32_Premultiplied to PNG using ffmpeg
    # QImage::Format_ARGB32_Premultiplied = 6, Format_ARGB32 = 5
    # Both are 4 bytes per pixel, BGRA in memory on little-endian
    proc = subprocess.run(
        ['ffmpeg', '-y', '-f', 'rawvideo', '-pixel_format', 'bgra',
         '-video_size', f'{width}x{height}', '-i', '-',
         '-frames:v', '1', output_file],
        input=data, capture_output=True
    )
    if proc.returncode != 0:
        print(f"ffmpeg error: {proc.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <output_file.png> <command...>", file=sys.stderr)
        sys.exit(1)

    output_file = os.path.abspath(sys.argv[1])
    command = ' '.join(sys.argv[2:])

    # Launch Konsole
    proc = subprocess.Popen(['konsole', '--separate', '--hold', '-e', 'bash', '-c', command])
    konsole_pid = proc.pid

    # Wait for Konsole dbus service
    dbus_service = f"org.kde.konsole-{konsole_pid}"
    bus = dbus.SessionBus()
    for _ in range(50):
        try:
            bus.get_object(dbus_service, '/Windows/1')
            break
        except dbus.exceptions.DBusException:
            time.sleep(0.1)
    else:
        print("Timeout waiting for Konsole dbus service", file=sys.stderr)
        sys.exit(1)

    # Wait for command output to render
    time.sleep(1)

    # Get window UUID
    uuid = get_window_uuid_by_pid(konsole_pid)
    if not uuid:
        print("Could not find window UUID", file=sys.stderr)
        subprocess.run(['qdbus', dbus_service, '/MainApplication',
                       'org.qtproject.Qt.QCoreApplication.quit'])
        sys.exit(1)

    # Capture screenshot
    try:
        capture_window(uuid, output_file)
    except dbus.exceptions.DBusException as e:
        if 'NoAuthorized' in str(e):
            print(
                "ERROR: Not authorized to take screenshots.\n"
                "Install the desktop file for authorization:\n"
                "  cp $SKILL_DIR/python3.12.desktop ~/.local/share/applications/\n"
                "  kbuildsycoca5",
                file=sys.stderr
            )
        else:
            print(f"Screenshot error: {e}", file=sys.stderr)
        subprocess.run(['qdbus', dbus_service, '/MainApplication',
                       'org.qtproject.Qt.QCoreApplication.quit'])
        sys.exit(1)

    # Close Konsole
    subprocess.run(['qdbus', dbus_service, '/MainApplication',
                   'org.qtproject.Qt.QCoreApplication.quit'],
                  capture_output=True)

    print(f"Screenshot saved to {output_file}")


if __name__ == '__main__':
    main()
