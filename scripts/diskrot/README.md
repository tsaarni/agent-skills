# diskrot

Find large and stale directories on disk.

## Build

```bash
go build -o diskrot .
```

## Usage

```bash
diskrot [path] [flags]
```

Path defaults to `$HOME` if not given.

### Examples

```bash
diskrot ~                          # show biggest directories
diskrot ~ --stale-days 90          # show stale content not accessed in 90 days
diskrot ~/.cache --min-size 100MB  # focus on a specific directory
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--stale-days` | 0 | Show content not accessed in this many days. 0 means show all. |
| `--min-size` | 500MB | Hide directories smaller than this. Accepts MB, GB, etc. |
| `--top` | 20 | Number of top-level results. |
| `--min-ratio` | 0.3 | Subdirectory must be at least this fraction of parent to be shown. |
| `--workers` | CPU count | Parallel workers for filesystem walk. |

## How it works

Uses a pool of parallel workers to read directories and stat files concurrently across all CPU cores. Directory entries are read with raw Linux syscalls, skipping the overhead of Go's standard library (no sorting, no extra allocations per file). A full home directory scan with ~14 million files completes in about 5 seconds.

Results are shown as a flat list with full paths. When a subdirectory holds a large fraction of its parent (controlled by `--min-ratio`), it gets its own line showing where the space concentrates.

With `--stale-days`, output includes two size columns: `STALE` shows how much data in that subtree has not been accessed within the threshold, `TOTAL` shows the full size. Staleness is checked per file using atime. This works with the `relatime` mount option common on Linux.

Linux only.
