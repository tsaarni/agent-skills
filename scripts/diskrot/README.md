# diskrot

Find large directories that haven't been accessed recently. Parallel filesystem walker using raw `getdents64` + `lstat` syscalls.

## Build

```bash
go build -o diskrot .
```

## Usage

```bash
./diskrot -path /home/user -depth 2 -not-accessed-days 90 -min-size 104857600
```

- `-path` — root directory to scan (default: `$HOME`)
- `-depth` — aggregation depth (default: 2)
- `-not-accessed-days` — only show dirs not accessed in this many days (default: 180)
- `-min-size` — minimum aggregated size in bytes (default: 100 MiB)
- `-top` — number of results (default: 40)

## Example

```
$ ./diskrot -path /home/user -not-accessed-days 90 -min-size 0 -top 10
Target: /home/user  depth=2  not-accessed=90d  cutoff=2026-07-08

   952 MiB  2026-04-11   99599 files  .rbenv/versions
   780 MiB  2025-11-01       3 files  .cache/trivy
   605 MiB  2026-06-30       1 files  core
   576 MiB  2026-06-30       1 files  Downloads/clang+llvm-14.0.0.tar.xz
```

Output shows: size, last access date, file count, path. Lists both directories (with aggregated totals) and individual large files. Everything listed is a removal candidate.
