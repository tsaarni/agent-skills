#!/bin/sh
set -eu

# 1. Parse host credentials from kernel command line
AGENT_HOME=""
if [ -r /proc/cmdline ]; then
  for arg in $(cat /proc/cmdline); do
    case "${arg}" in
      gondolin.home=*) AGENT_HOME="${arg#gondolin.home=}" ;;
    esac
  done
fi

# 2. Create placeholder home directory and update /etc/passwd if provided
if [ -n "$AGENT_HOME" ]; then
  mkdir -p "$AGENT_HOME"
  sed -i "s|^root:x:0:0:root:[^:]*:|root:x:0:0:root:${AGENT_HOME}:|" /etc/passwd
fi

# 3. Self-healing fix for sudo permissions (resolves macOS host build UID mapping issue)
# Only apply if permissions are actually wrong to avoid noisy no-op changes at every boot
if [ -e /usr/bin/sudo ]; then
  needs_fix=0
  if [ "$(stat -c %U /usr/bin/sudo 2>/dev/null)" != "root" ] || [ "$(stat -c %a /usr/bin/sudo 2>/dev/null)" != "4755" ]; then
    needs_fix=1
  fi
  if [ -e /etc/sudoers ] && [ "$(stat -c %a /etc/sudoers 2>/dev/null)" != "440" ]; then
    needs_fix=1
  fi
  if [ "$needs_fix" -eq 1 ]; then
    chown root:root /usr/bin/sudo /etc/sudoers /etc/sudo.conf 2>/dev/null || true
    chmod 4755 /usr/bin/sudo || true
    chmod 0440 /etc/sudoers || true

    if [ -d /usr/lib/sudo ]; then
      chown -R root:root /usr/lib/sudo || true
      chmod 755 /usr/lib/sudo || true
      chmod 755 /usr/lib/sudo/*.so 2>/dev/null || true
    fi

    if [ -d /etc/sudoers.d ]; then
      chown -R root:root /etc/sudoers.d || true
      chmod 750 /etc/sudoers.d || true
    fi
  fi
fi
