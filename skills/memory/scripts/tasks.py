#!/usr/bin/env python3
"""Task helper for the memory skill (stdlib only). Tasks are records with a status field.

  tasks.py [WINDOW]                 list active tasks: overdue / now / waiting / [soon] / upcoming
                                    WINDOW: --today --tomorrow --this-week --next-week --this-month
                                    --next-month --days N --since/--until (date or weekday)
  tasks.py complete NAME [NOTE]     complete a task: close it, or advance a repeating one,
                                    bump updated, append COMPLETED: to this host's log

Store: $MEMORY_DIR (default ~/storage/memory). --now overrides the current time (testing).
"""

import argparse
import calendar
import datetime as dt
import os
import re
import socket
import sys
from pathlib import Path

ACTIVE = ("open", "in-progress", "waiting")
STATUSES = ACTIVE + ("done", "cancelled")
SHOWN = ("status", "start", "due", "repeat", "priority")
GROUPS = ("overdue", "now", "waiting", "soon", "upcoming")
RECORD_NAME = re.compile(r"^[^./]+\.md$")  # skips sync conflict copies like X.sync-conflict-1.md
REPEAT = re.compile(r"^(daily|weekly|biweekly|monthly|yearly|([1-9]\d*)([dw]))$")
MAX_DAYS = 3660
DATE_FMT, DATETIME_FMT = "%Y-%m-%d", "%Y-%m-%dT%H:%M"


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def parse_when(value):
    """Return (datetime, has_time) for YYYY-MM-DD or YYYY-MM-DDTHH:MM, else None."""
    for fmt, has_time in ((DATETIME_FMT, True), (DATE_FMT, False)):
        try:
            return dt.datetime.strptime(value, fmt), has_time
        except ValueError:
            pass
    return None


def format_when(when, has_time):
    return when.strftime(DATETIME_FMT if has_time else DATE_FMT)


class Frontmatter:
    """Top-level `key: value` fields of a record. lines[1:end] is the frontmatter;
    line endings and a UTF-8 BOM are preserved when written back."""

    def __init__(self, path):
        self.path = path
        data = path.read_bytes()
        self.bom = data.startswith(b"\xef\xbb\xbf")
        text = data.decode("utf-8-sig")  # UnicodeDecodeError for non-UTF-8 files
        self.lines = text.splitlines(keepends=True)
        self.fields, self.duplicates, self.end = {}, [], None
        if not self.lines or self.lines[0].strip() != "---":
            return
        fields = {}
        for i in range(1, len(self.lines)):
            line = self.lines[i].rstrip("\r\n")
            if line.strip() == "---":
                self.fields, self.end = fields, i
                return
            key, sep, value = line.partition(":")
            if sep and re.fullmatch(r"[a-z]+", key):  # column 0 only: nested keys are not fields
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key in fields:
                    self.duplicates.append(key)
                if value:
                    fields[key] = value
        self.duplicates = []  # no closing ---: not frontmatter

    def set(self, key, value):
        newline = "\r\n" if self.lines[0].endswith("\r\n") else "\n"
        for i in range(1, self.end):
            if self.lines[i].partition(":")[0] == key:
                self.lines[i] = f"{key}: {value}{newline}"
                return
        self.lines.insert(self.end, f"{key}: {value}{newline}")
        self.end += 1

    def save(self):
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_bytes((b"\xef\xbb\xbf" if self.bom else b"") + "".join(self.lines).encode("utf-8"))
        os.replace(tmp, self.path)


def read_fields(path, root):
    """Frontmatter fields for the listing; warns and returns None for unreadable files."""
    try:
        fm = Frontmatter(path)
    except UnicodeDecodeError:
        warn(f"{path.relative_to(root)}: not UTF-8, skipped")
        return None
    if fm.duplicates:
        warn(f"{path.relative_to(root)}: duplicate {', '.join(sorted(set(fm.duplicates)))}; last value used")
    return fm.fields


def records(root):
    for path in sorted((root / "records").glob("*.md")):
        if RECORD_NAME.match(path.name):
            yield path


def compare(value, now):
    """-1 / 0 / 1 for value before / at / after now. Date-only values compare by day,
    so a task due today is not overdue and a task starting today has started."""
    when, has_time = parse_when(value)
    a, b = (when, now) if has_time else (when.date(), now.date())
    return (a > b) - (a < b)


def at(value):
    """Datetime of a start/due value; a date-only value means 00:00 of that day."""
    return parse_when(value)[0]


def classify(fields, now, window):
    """Group relative to the real current time. A window only decides what is
    'soon': not actionable yet, but starting or due before the window ends."""
    start, due = fields.get("start"), fields.get("due")
    if due and compare(due, now) < 0:
        return "overdue"
    if not start or compare(start, now) <= 0:
        return "waiting" if fields["status"] == "waiting" else "now"
    if window and (at(start) < window[1] or (due and at(due) < window[1])):
        return "soon"
    return "upcoming"


def active_tasks(root, now, window=None):
    rows = []
    for path in records(root):
        fields = read_fields(path, root) or {}
        status = fields.get("status")
        if status is None:
            continue
        if status not in STATUSES:
            warn(f"{path.relative_to(root)}: unknown status {status!r}")
            continue
        if status not in ACTIVE:
            continue
        rel = path.relative_to(root)
        if "summary" not in fields:
            warn(f"{rel}: task has no summary")
        if "repeat" in fields and not REPEAT.match(fields["repeat"]):
            warn(f"{rel}: invalid repeat {fields['repeat']!r}; completing it will fail")
        elif "repeat" in fields and "start" not in fields:
            warn(f"{rel}: repeat without start; completing it will fail")
        bad = [k for k in ("start", "due") if k in fields and not parse_when(fields[k])]
        if bad:
            warn(f"{rel}: invalid {', '.join(f'{k} {fields[k]!r}' for k in bad)}; shown under now")
            fields = {k: v for k, v in fields.items() if k not in bad}
        group = classify(fields, now, window)
        due_in = bool(window and "due" in fields and window[0] <= at(fields["due"]) < window[1])
        prio = {"high": 0, "low": 2}.get(fields.get("priority"), 1)
        rows.append((GROUPS.index(group), prio, str(rel), group, fields, due_in))
    rows.sort()
    return rows


def print_tasks(rows, window=None):
    if window:
        last = window[1] - dt.timedelta(minutes=1)
        fmt = DATE_FMT if window[1].time() == dt.time() and window[0].time() == dt.time() else DATETIME_FMT
        print(f"window: {window[0].strftime(fmt)} .. {last.strftime(fmt)} (soon = starts or is due by the end)")
    if not rows:
        print("(no active tasks)")
    for _, _, rel, group, fields, due_in in rows:
        extra = " | ".join(f"{k}={fields[k]}" for k in SHOWN if k in fields)
        print(f"{group:<8} {rel}: {fields.get('summary', '')} | {extra}{' | due in window' if due_in else ''}")


def step(value, repeat, n):
    """Advance a datetime by n repeat intervals. Monthly/yearly keep the day of month,
    clamped to month end; a date on the last day of its month stays on the last day
    (Jan 31 -> Feb 28 -> Mar 31), so short months do not cause drift."""
    m = REPEAT.match(repeat)
    kind = m.group(1)
    if kind in ("monthly", "yearly"):
        months = n * (1 if kind == "monthly" else 12)
        y, mo = divmod(value.month - 1 + months, 12)
        year, month = value.year + y, mo + 1
        last = calendar.monthrange(year, month)[1]
        at_end = value.day == calendar.monthrange(value.year, value.month)[1]
        day = last if at_end else min(value.day, last)
        return value.replace(year=year, month=month, day=day)
    if kind in ("daily", "weekly", "biweekly"):
        days = {"daily": 1, "weekly": 7, "biweekly": 14}[kind]
    else:
        days = int(m.group(2)) * (7 if m.group(3) == "w" else 1)
    return value + dt.timedelta(days=n * days)


def append_log(root, now, project, description, rel):
    log = root / "logs" / f"{socket.gethostname().split('.')[0]}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if log.exists() and log.stat().st_size:
        with log.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            prefix = "" if f.read(1) == b"\n" else "\n"
    line = f"{now.strftime(DATETIME_FMT)} | {project} | {description} | {rel}"
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}{line}\n")
    return log, line


def complete(root, name, note, project, now, dry_run, early):
    stem = Path(name).name.removesuffix(".md")
    path = root / "records" / f"{stem}.md"
    rel = f"records/{stem}.md"
    if not path.is_file():
        sys.exit(f"error: {rel} not found")
    try:
        fm = Frontmatter(path)
    except UnicodeDecodeError:
        sys.exit(f"error: {rel} is not UTF-8")
    fields = fm.fields
    if fm.end is None:
        sys.exit(f"error: {rel} has no frontmatter")
    if fm.duplicates:
        sys.exit(f"error: {rel}: duplicate {', '.join(sorted(set(fm.duplicates)))} in frontmatter; fix it first")
    if fields.get("status") not in ACTIVE:
        sys.exit(f"error: {rel} is not an active task (status: {fields.get('status')})")

    changes = {"updated": now.strftime(DATE_FMT)}
    repeat = fields.get("repeat")
    if repeat:
        if not REPEAT.match(repeat):
            sys.exit(f"error: {rel}: invalid repeat {repeat!r}")
        if "start" not in fields or not parse_when(fields["start"]):
            sys.exit(f"error: {rel}: repeating task needs a valid start")
        start, start_time = parse_when(fields["start"])
        due = parse_when(fields["due"]) if "due" in fields else None
        if "due" in fields and not due:
            sys.exit(f"error: {rel}: invalid due {fields['due']!r}")
        if compare(fields["start"], now) > 0 and not early:
            sys.exit(f"error: {rel}: next run starts {fields['start']}, not reached yet. Already completed? "
                     "Use --early to complete the next run ahead of time")
        n = 1
        while not (step(start, repeat, n) > now if start_time else step(start, repeat, n).date() > now.date()):
            n += 1
        changes["start"] = format_when(step(start, repeat, n), start_time)
        if due:
            changes["due"] = format_when(step(due[0], repeat, n), due[1])
        changes["status"] = "open"
        description = f"COMPLETED: next start {changes['start']}"
        if due:
            description += f", due {changes['due']}"
    else:
        changes["status"] = "done"
        description = "COMPLETED:"
    if note:
        description += f" {note}" if not repeat else f"; {note}"

    changes = {k: v for k, v in changes.items() if fields.get(k) != v}
    for key, value in changes.items():
        print(f"{rel}: {key}: {fields.get(key, '(none)')} -> {value}")
    if dry_run:
        print(f"log (dry run): {now.strftime(DATETIME_FMT)} | {project} | {description} | {rel}")
        return
    for key, value in changes.items():
        fm.set(key, value)
    fm.save()
    log, line = append_log(root, now, project, description, rel)
    print(f"logged to {log.relative_to(root)}: {line}")


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse_time(value):
    parsed = parse_when(value)
    if not parsed:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD or YYYY-MM-DDTHH:MM")
    return parsed


def parse_bound(value):
    """A window bound: YYYY-MM-DD[THH:MM], or a weekday name (mon..sun or full name)."""
    name = value.lower()
    if len(name) >= 3:
        for i, day in enumerate(WEEKDAYS):
            if day.startswith(name):
                return ("weekday", i)
    parsed = parse_when(value)
    if not parsed:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, YYYY-MM-DDTHH:MM or a weekday name (mon..sun)")
    return parsed


def resolve_bound(bound, base):
    """(datetime, has_time). A weekday is its first occurrence on or after base, as a whole day."""
    if bound[0] != "weekday":
        return bound
    day = base.replace(hour=0, minute=0)
    return day + dt.timedelta(days=(bound[1] - day.weekday()) % 7), False


def add_window_options(parser):
    g = parser.add_argument_group("time window (local time, weeks start on Monday; at most one preset)")
    for flag, text in (("today", "today"), ("tomorrow", "tomorrow"), ("this-week", "this week, Monday to Sunday"),
                       ("next-week", "next week, Monday to Sunday"), ("this-month", "this calendar month"),
                       ("next-month", "next calendar month")):
        g.add_argument(f"--{flag}", action="store_true", help=text)
    g.add_argument("--days", type=int, metavar="N", help="the next N days, including today")
    g.add_argument("--since", type=parse_bound, metavar="TIME",
                   help="window start, YYYY-MM-DD[THH:MM] or weekday (default: now)")
    g.add_argument("--until", type=parse_bound, metavar="TIME",
                   help="window end, YYYY-MM-DD[THH:MM] or weekday (mon..sun: the first one from the window start, which may be today); "
                        "a date or weekday includes that whole day")


def resolve_window(args, now, parser):
    """Return the half-open window [since, until), or None."""
    presets = [f for f in ("today", "tomorrow", "this_week", "next_week", "this_month", "next_month") if getattr(args, f)]
    if args.days is not None:
        if not 1 <= args.days <= MAX_DAYS:
            parser.error(f"--days must be between 1 and {MAX_DAYS}")
        presets.append("days")
    custom = args.since or args.until
    if len(presets) > 1:
        parser.error("only one of --today, --tomorrow, --this-week, --next-week, --this-month, --next-month, --days")
    if presets and custom:
        parser.error("--since/--until cannot be combined with the other time options")
    today = now.replace(hour=0, minute=0)
    day = dt.timedelta(days=1)
    week = today - (today.weekday() * day)
    month = today.replace(day=1)
    next_month = (month + 32 * day).replace(day=1)
    windows = {
        "today": (today, today + day),
        "tomorrow": (today + day, today + 2 * day),
        "this_week": (week, week + 7 * day),
        "next_week": (week + 7 * day, week + 14 * day),
        "this_month": (month, next_month),
        "next_month": (next_month, (next_month + 32 * day).replace(day=1)),
        "days": (today, today + (args.days or 0) * day),
    }
    if presets:
        return windows[presets[0]]
    if not custom:
        return None
    if not args.until:
        parser.error("--since needs --until")
    since = resolve_bound(args.since, now)[0] if args.since else now
    until, until_time = resolve_bound(args.until, since)  # weekday: first one on or after the start
    if not until_time:
        until += day
    if until <= since:
        parser.error("--until must be after --since")
    return since, until


GROUPS_HELP = """groups (always relative to the real current time):
  overdue   due before today (or before now, for a due time)
  now       actionable: start reached or absent
  waiting   status waiting and start reached or absent
  soon      only with a window: not actionable yet, starts or is due before the window ends
  upcoming  starts later
'due in window' marks tasks whose due date falls inside the window.
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog=GROUPS_HELP,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=Path(os.environ.get("MEMORY_DIR", "~/storage/memory")).expanduser())
    p.add_argument("--now", type=lambda v: parse_time(v)[0], help=argparse.SUPPRESS)
    add_window_options(p)
    sub = p.add_subparsers(dest="cmd", title="commands")
    c = sub.add_parser("complete", help="complete a task")
    c.add_argument("name", help="record name, e.g. MaintainDependencies")
    c.add_argument("note", nargs="*", help="optional note for the log line")
    c.add_argument("-p", "--project", default="-", help="project column for the log line")
    c.add_argument("-n", "--dry-run", action="store_true")
    c.add_argument("--early", action="store_true", help="allow completing a repeating task before its start")
    args = p.parse_args()

    root = args.dir
    now = args.now or dt.datetime.now().replace(second=0, microsecond=0)
    window = resolve_window(args, now, p)
    if not (root / "records").is_dir():
        sys.exit(f"error: {root}/records not found")

    if args.cmd == "complete":
        if window:
            p.error("time window options only apply to listing")
        complete(root, args.name, " ".join(args.note), args.project, now, args.dry_run, args.early)
    else:
        print_tasks(active_tasks(root, now, window), window)


if __name__ == "__main__":
    main()
