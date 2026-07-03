# Gondolin

Run pi inside a light Alpine Linux VM. File ops, grep, and shell commands
execute inside the sandbox. Each session gets a fresh root filesystem.

```bash
pi --gondolin
```

## Slash Commands

| Command | What it does |
|---|---|
| `/gondolin` | Show VM status (vmm, cpu, memory, mounts, overlay) |
| `/mount <path> [--rw]` | Mount a host directory into the VM (read-only by default) |
| `/mounts` | List active mounts |
| `/unmount <path>` | Remove a mount (`/umount` works too) |

Mounts use 1-to-1 path mirroring — a host path like `/Users/you/project` appears at
the same path inside the VM. Files created inside the VM are owned by your host user.

```
/mount ~/docs           # read-only
/mount /tmp/scratch --rw # read-write
```

## gondolin-vm.json

Drop this file in your project root. All keys are optional.

```json
{
  "cpus": 2,
  "memory": "1G",
  "persist": false,
  "sandbox": { "vmm": "krun" },
  "vfs": {
    "mounts": {
      "/Users/tsaarni/docs": "ro",
      "/tmp/scratch": "rw"
    }
  }
}
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `cpus` | number | `2` | |
| `memory` | string | `"1G"` | qemu-style (`512M`, `2G`, …) |
| `persist` | boolean | `false` | Keep overlay in `~/.gondolin/overlays/<project>/` |
| `sandbox.vmm` | string | auto | `"krun"` or `"qemu"` |
| `sandbox.qemuIdlePauseMs` | number | `30000` (Qemu on macOS) | `0` disables |
| `vfs.mounts` | object | — | `"ro"`/`"rw"` shorthand, or `{ "path": "<host path>", "readonly": true/false }` |

## Custom Image

Add packages to the default minimal Alpine image:

```bash
npx gondolin build --init-config > build-config.json
npx gondolin build --config build-config.json --tag alpine-base:latest
```

Delete `~/.cache/gondolin/images/refs/alpine-base` to go back to the default.

## How It Works

- File tools (`read`, `write`, `edit`, `ls`, `find`) route through the VM.
- `grep` runs `rg` inside the VM; `find` uses `fd`.
- Shell commands run via `/bin/sh`.
- Overlay is discarded on exit (unless `--persist`).
