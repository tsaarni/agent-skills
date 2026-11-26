"""Tests for tasks.py: python3 test_tasks.py"""

import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).with_name("tasks.py"))
HOST = socket.gethostname().split(".")[0]


def run(root, *args, check=True):
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--dir", str(root), *args], capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(p.stderr + p.stdout)
    return p


class Store:
    def __init__(self, case):
        self.tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "records").mkdir()
        (self.root / "logs").mkdir()

    def rec(self, name, **fm):
        body = "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\nbody\nstatus: done\n---\n"
        (self.root / "records" / name).write_text(body)

    def fm(self, name):
        text = (self.root / "records" / name).read_text()
        return dict(l.split(": ", 1) for l in text.split("---")[1].strip().splitlines())

    def log(self, text):
        (self.root / "logs" / f"{HOST}.md").write_text(text)

    def logtext(self):
        return (self.root / "logs" / f"{HOST}.md").read_text()


def groups(out):
    return {line.split()[1].rstrip(":"): line.split()[0] for line in out.splitlines() if line and not line.startswith("(")}


class Tasks(unittest.TestCase):
    def setUp(self):
        s = self.s = Store(self)
        base = dict(tags="[x]", created="2026-11-01", updated="2026-11-01")
        s.rec("Overdue.md", summary="o", status="in-progress", due="2026-11-20", **base)
        s.rec("DueToday.md", summary="d", status="open", due="2026-11-26", **base)
        s.rec("StartedToday.md", summary="s", status="open", start="2026-11-26", **base)
        s.rec("LaterToday.md", summary="l", status="open", start="2026-11-26T15:00", **base)
        s.rec("NoDates.md", summary="n", status="open", **base)
        s.rec("Blocked.md", summary="b", status="waiting", **base)
        s.rec("BlockedLate.md", summary="bl", status="waiting", due="2026-11-01", **base)
        s.rec("BlockedFuture.md", summary="bf", status="waiting", start="2026-12-20", **base)
        s.rec("Biweekly.md", summary="bw", status="open", start="2026-12-03", repeat="biweekly", **base)
        s.rec("FriTask.md", summary="f", status="open", start="2026-11-27T09:00", due="2026-11-28", **base)
        s.rec("Far.md", summary="far", status="open", start="2027-01-10", **base)
        s.rec("Done.md", summary="done", status="done", **base)
        s.rec("Knowledge.md", summary="k", **base)
        s.rec("Spaces.md", summary='"quoted summary"', status="open  ", **base)
        s.rec("Bad.md", summary="bad", status="open", start="tomorrow", **base)
        s.rec("Weird.md", summary="w", status="todo", **base)
        s.rec("NoDates.sync-conflict-1.md", summary="dup", status="open", **base)
        s.rec("DueNextWeek.md", summary="dnw", status="open", due="2026-12-02", **base)
        s.rec("DueSunEve.md", summary="dse", status="open", due="2026-12-06T18:00", **base)
        s.rec("DueAfter.md", summary="da", status="open", due="2026-12-07", **base)
        s.rec("StartsAfter.md", summary="sa", status="open", start="2026-12-07", **base)

    def test_groups_today(self):
        p = run(self.s.root, "--now", "2026-11-26T12:00")
        g = groups(p.stdout)
        self.assertEqual(g["records/Overdue.md"], "overdue")
        self.assertEqual(g["records/DueToday.md"], "now")  # due today is not overdue
        self.assertEqual(g["records/StartedToday.md"], "now")
        self.assertEqual(g["records/LaterToday.md"], "upcoming")  # 15:00 not reached at 12:00
        self.assertEqual(g["records/NoDates.md"], "now")
        self.assertEqual(g["records/Blocked.md"], "waiting")
        self.assertEqual(g["records/BlockedLate.md"], "overdue")
        self.assertEqual(g["records/BlockedFuture.md"], "upcoming")
        self.assertEqual(g["records/Biweekly.md"], "upcoming")
        self.assertEqual(g["records/FriTask.md"], "upcoming")
        self.assertEqual(g["records/Spaces.md"], "now")  # trailing spaces tolerated
        self.assertEqual(g["records/Bad.md"], "now")
        for absent in ("Done", "Knowledge", "Weird", "NoDates.sync-conflict-1"):
            self.assertNotIn(f"records/{absent}.md", g)
        self.assertNotIn("soon", p.stdout)
        self.assertIn("Spaces.md: quoted summary |", p.stdout)
        self.assertIn("invalid start 'tomorrow'", p.stderr)
        self.assertIn("unknown status 'todo'", p.stderr)
        order = [l.split()[0] for l in p.stdout.splitlines()]
        self.assertEqual(order, sorted(order, key=["overdue", "now", "waiting", "soon", "upcoming"].index))

    def test_until(self):
        g = groups(run(self.s.root, "--now", "2026-11-26T12:00", "--until", "2026-12-04").stdout)
        self.assertEqual(g["records/Biweekly.md"], "soon")
        self.assertEqual(g["records/FriTask.md"], "soon")  # not overdue when looking ahead
        self.assertEqual(g["records/LaterToday.md"], "soon")
        self.assertEqual(g["records/Far.md"], "upcoming")
        self.assertEqual(g["records/BlockedFuture.md"], "upcoming")
        self.assertEqual(g["records/Overdue.md"], "overdue")

    def test_until_date_covers_whole_day(self):
        g = groups(run(self.s.root, "--now", "2026-11-26T12:00", "--until", "2026-11-27").stdout)
        self.assertEqual(g["records/FriTask.md"], "soon")  # starts 2026-11-27T09:00


    def window(self, *args, now="2026-11-26T12:00"):
        p = run(self.s.root, "--now", now, *args)
        head = p.stdout.splitlines()[0]
        due_in = {l.split()[1].rstrip(":") for l in p.stdout.splitlines() if l.endswith("| due in window")}
        return head, groups("\n".join(p.stdout.splitlines()[1:])), due_in

    def test_next_week(self):
        head, g, due_in = self.window("--next-week")  # today is Thu 2026-11-26
        self.assertEqual(head, "window: 2026-11-30 .. 2026-12-06 (soon = starts or is due by the end)")
        self.assertEqual(g["records/Biweekly.md"], "soon")     # starts Thu next week
        self.assertEqual(g["records/FriTask.md"], "soon")      # starts before the window, actionable by then
        self.assertEqual(g["records/StartsAfter.md"], "upcoming")
        self.assertEqual(g["records/Far.md"], "upcoming")
        self.assertEqual(g["records/DueNextWeek.md"], "now")   # actionable already
        self.assertEqual(g["records/Overdue.md"], "overdue")   # still relative to today
        self.assertEqual(due_in, {"records/DueNextWeek.md", "records/DueSunEve.md"})

    def test_this_week_on_sunday(self):
        head, _, _ = self.window("--this-week", now="2026-11-29T20:00")
        self.assertTrue(head.startswith("window: 2026-11-23 .. 2026-11-29 "))
        head, _, _ = self.window("--next-week", now="2026-11-29T20:00")
        self.assertTrue(head.startswith("window: 2026-11-30 .. 2026-12-06 "))

    def test_today_tomorrow_days(self):
        head, g, _ = self.window("--today")
        self.assertTrue(head.startswith("window: 2026-11-26 .. 2026-11-26 "))
        self.assertEqual(g["records/LaterToday.md"], "soon")
        self.assertEqual(g["records/FriTask.md"], "upcoming")
        head, g, _ = self.window("--tomorrow")
        self.assertTrue(head.startswith("window: 2026-11-27 .. 2026-11-27 "))
        self.assertEqual(g["records/FriTask.md"], "soon")
        self.assertEqual(g["records/Biweekly.md"], "upcoming")
        head, g, _ = self.window("--days", "3")
        self.assertTrue(head.startswith("window: 2026-11-26 .. 2026-11-28 "))
        self.assertEqual(g["records/FriTask.md"], "soon")
        self.assertEqual(g["records/Biweekly.md"], "upcoming")

    def test_months(self):
        head, g, _ = self.window("--this-month")
        self.assertTrue(head.startswith("window: 2026-11-01 .. 2026-11-30 "))
        self.assertEqual(g["records/Biweekly.md"], "upcoming")
        head, g, _ = self.window("--next-month")
        self.assertTrue(head.startswith("window: 2026-12-01 .. 2026-12-31 "))
        self.assertEqual(g["records/Biweekly.md"], "soon")
        self.assertEqual(g["records/Far.md"], "upcoming")
        head, _, _ = self.window("--next-month", now="2026-12-15T10:00")
        self.assertTrue(head.startswith("window: 2027-01-01 .. 2027-01-31 "))
        head, _, _ = self.window("--next-month", now="2027-01-31T10:00")
        self.assertTrue(head.startswith("window: 2027-02-01 .. 2027-02-28 "))

    def test_since_until(self):
        head, g, due_in = self.window("--since", "2026-12-01", "--until", "2026-12-04")
        self.assertTrue(head.startswith("window: 2026-12-01 .. 2026-12-04 "))
        self.assertEqual(g["records/Biweekly.md"], "soon")
        self.assertEqual(due_in, {"records/DueNextWeek.md"})
        head, _, _ = self.window("--until", "2026-11-26T18:00")
        self.assertTrue(head.startswith("window: 2026-11-26T12:00 .. 2026-11-26T17:59 "))

    def test_until_weekday(self):
        # today is Thu 2026-11-26
        for arg, end in (("fri", "2026-11-27"), ("Friday", "2026-11-27"), ("thu", "2026-11-26"),
                         ("wed", "2026-12-02"), ("SUN", "2026-11-29"), ("mon", "2026-11-30")):
            head, _, _ = self.window("--until", arg)
            self.assertEqual(head, f"window: 2026-11-26T12:00 .. {end}T23:59 (soon = starts or is due by the end)", arg)
        _, g, _ = self.window("--until", "fri")
        self.assertEqual(g["records/FriTask.md"], "soon")
        self.assertEqual(g["records/Biweekly.md"], "upcoming")
        head, _, _ = self.window("--since", "mon", "--until", "sun")  # next Monday to the Sunday after
        self.assertTrue(head.startswith("window: 2026-11-30 .. 2026-12-06 "))
        head, _, _ = self.window("--since", "sat", "--until", "tue")
        self.assertTrue(head.startswith("window: 2026-11-28 .. 2026-12-01 "))
        head, _, _ = self.window("--until", "thu", now="2026-11-26T23:00")  # today, until end of day
        self.assertTrue(head.startswith("window: 2026-11-26T23:00 .. 2026-11-26T23:59 "))
        head, _, _ = self.window("--since", "mon", "--until", "2026-12-06")
        self.assertTrue(head.startswith("window: 2026-11-30 .. 2026-12-06 "))

    def test_window_errors(self):
        for args in (("--today", "--next-week"), ("--days", "3", "--until", "2026-12-01"), ("--since", "2026-12-01"),
                     ("--since", "2026-12-05", "--until", "2026-12-01"), ("--days", "0"), ("--until", "fr"), ("--until", "someday"),
                     ("--since", "fri", "--until", "2026-11-26")):
            p = run(self.s.root, "--now", "2026-11-26T12:00", *args, check=False)
            self.assertEqual(p.returncode, 2, args)


class Complete(unittest.TestCase):
    def setUp(self):
        self.s = Store(self)
        self.base = dict(summary="t", tags="[x]", created="2026-11-01", updated="2026-11-01")

    def done(self, now, name="T.md", *extra):
        return run(self.s.root, "--now", now, "complete", name.removesuffix(".md"), *extra)

    def test_one_off(self):
        self.s.rec("T.md", status="in-progress", due="2026-12-01", **self.base)
        self.s.log("2026-11-01T10:00 | - | x | records/T.md")  # no trailing newline
        self.done("2026-11-26T21:00", "T.md", "fix", "merged", "-p", "contour")
        fm = self.s.fm("T.md")
        self.assertEqual((fm["status"], fm["updated"], fm["due"]), ("done", "2026-11-26", "2026-12-01"))
        self.assertEqual(self.s.logtext(),
                         "2026-11-01T10:00 | - | x | records/T.md\n"
                         "2026-11-26T21:00 | contour | COMPLETED: fix merged | records/T.md\n")
        self.assertIn("body\nstatus: done\n", (self.s.root / "records/T.md").read_text())  # body untouched

    def test_weekly_on_time(self):
        self.s.rec("T.md", status="open", start="2026-11-26", repeat="weekly", **self.base)
        self.done("2026-11-26T21:00")
        fm = self.s.fm("T.md")
        self.assertEqual((fm["start"], fm["status"]), ("2026-12-03", "open"))
        self.assertIn("| - | COMPLETED: next start 2026-12-03 | records/T.md\n", self.s.logtext())

    def test_weekly_late_skips_missed(self):
        self.s.rec("T.md", status="open", start="2026-12-03", repeat="weekly", **self.base)
        self.done("2026-12-12T10:00")
        self.assertEqual(self.s.fm("T.md")["start"], "2026-12-17")

    def test_early_completion_needs_flag(self):
        self.s.rec("T.md", status="open", start="2026-12-03", repeat="weekly", **self.base)
        p = run(self.s.root, "--now", "2026-11-30T10:00", "complete", "T", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not reached yet", p.stderr)
        self.assertEqual(self.s.fm("T.md")["start"], "2026-12-03")
        self.done("2026-11-30T10:00", "T.md", "--early")
        self.assertEqual(self.s.fm("T.md")["start"], "2026-12-10")

    def test_double_completion_refused(self):
        self.s.rec("T.md", status="open", start="2026-11-26", repeat="weekly", **self.base)
        self.done("2026-11-26T10:00")
        p = run(self.s.root, "--now", "2026-11-26T10:05", "complete", "T", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.s.fm("T.md")["start"], "2026-12-03")
        self.assertEqual(self.s.logtext().count("COMPLETED"), 1)

    def test_preserves_crlf_and_bom(self):
        path = self.s.root / "records/T.md"
        path.write_bytes(b"\xef\xbb\xbf---\r\nsummary: t\r\nstatus: open\r\nstart: 2026-11-26\r\nrepeat: weekly\r\n---\r\nbody\r\n")
        self.done("2026-11-26T10:00")
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf---\r\n"))
        self.assertEqual(data.count(b"\r\n"), data.count(b"\n"))
        self.assertIn(b"start: 2026-12-03\r\n", data)
        self.assertIn(b"updated: 2026-11-26\r\n---", data)  # inserted with the file's line ending
        self.assertEqual([p.name for p in path.parent.iterdir()], ["T.md"])  # no temp file left

    def test_refuses_broken_files(self):
        (self.s.root / "records/L.md").write_bytes(b"---\nsummary: \xe4\nstatus: open\n---\n")
        self.s.rec("D.md", summary="d", status="done", **{"status ": "x"})
        (self.s.root / "records/D.md").write_text("---\nsummary: d\nstatus: done\nstatus: open\n---\n")
        self.s.rec("Z.md", status="open", start="2026-11-20", repeat="0d", **self.base)
        for name, msg in (("L", "not UTF-8"), ("D", "duplicate status"), ("Z", "invalid repeat")):
            p = run(self.s.root, "--now", "2026-11-26T10:00", "complete", name, check=False)
            self.assertNotEqual(p.returncode, 0, name)
            self.assertIn(msg, p.stderr, name)

    def test_due_advances_with_start(self):
        self.s.rec("T.md", status="in-progress", start="2026-11-26", due="2026-11-28", repeat="biweekly", **self.base)
        self.done("2026-11-27T10:00", "T.md", "all merged")
        fm = self.s.fm("T.md")
        self.assertEqual((fm["start"], fm["due"], fm["status"]), ("2026-12-10", "2026-12-12", "open"))
        self.assertIn("COMPLETED: next start 2026-12-10, due 2026-12-12; all merged |", self.s.logtext())

    def test_datetime_start_keeps_time(self):
        self.s.rec("T.md", status="open", start="2026-11-26T09:00", repeat="3d", **self.base)
        self.done("2026-11-26T10:00")
        self.assertEqual(self.s.fm("T.md")["start"], "2026-11-29T09:00")

    def test_monthly_end_of_month(self):
        self.s.rec("T.md", status="open", start="2027-01-31", repeat="monthly", **self.base)
        self.done("2027-01-31T10:00")
        self.assertEqual(self.s.fm("T.md")["start"], "2027-02-28")
        self.done("2027-02-28T10:00")
        self.assertEqual(self.s.fm("T.md")["start"], "2027-03-31")

    def test_monthly_mid_month_and_yearly_leap(self):
        self.s.rec("T.md", status="open", start="2027-01-15", repeat="monthly", **self.base)
        self.done("2027-01-15T10:00")
        self.assertEqual(self.s.fm("T.md")["start"], "2027-02-15")
        self.s.rec("Y.md", status="open", start="2028-02-29", repeat="yearly", **self.base)
        self.done("2028-02-29T10:00", "Y.md")
        self.assertEqual(self.s.fm("Y.md")["start"], "2029-02-28")

    def test_adds_missing_updated(self):
        self.s.rec("T.md", summary="t", tags="[x]", created="2026-11-01", status="open")
        self.done("2026-11-26T10:00")
        self.assertEqual(self.s.fm("T.md")["updated"], "2026-11-26")

    def test_dry_run_changes_nothing(self):
        self.s.rec("T.md", status="open", start="2026-11-26", repeat="weekly", **self.base)
        before = (self.s.root / "records/T.md").read_text()
        p = self.done("2026-11-26T10:00", "T.md", "-n")
        self.assertIn("start: 2026-11-26 -> 2026-12-03", p.stdout)
        self.assertEqual((self.s.root / "records/T.md").read_text(), before)
        self.assertFalse((self.s.root / "logs" / f"{HOST}.md").exists())

    def test_errors(self):
        self.s.rec("Done.md", status="done", **self.base)
        self.s.rec("K.md", **self.base)
        self.s.rec("R.md", status="open", repeat="weekly", **self.base)
        self.s.rec("B.md", status="open", start="2026-11-26", repeat="fortnightly", **self.base)
        for name, msg in (("Done", "not an active task"), ("K", "not an active task"),
                          ("Missing", "not found"), ("R", "needs a valid start"), ("B", "invalid repeat")):
            p = run(self.s.root, "--now", "2026-11-26T10:00", "complete", name, check=False)
            self.assertNotEqual(p.returncode, 0, name)
            self.assertIn(msg, p.stderr, name)

    def test_accepts_path_forms(self):
        self.s.rec("T.md", status="open", **self.base)
        self.done("2026-11-26T10:00", "records/T.md")
        self.assertEqual(self.s.fm("T.md")["status"], "done")


class Robustness(unittest.TestCase):
    def test_listing_survives_bad_files(self):
        s = Store(self)
        (s.root / "records/Latin1.md").write_bytes(b"---\nsummary: \xe4\nstatus: open\n---\n")
        (s.root / "records/Dup.md").write_text("---\nsummary: dup\nstatus: done\nstatus: open\n---\n")
        (s.root / "records/Nested.md").write_text("---\nsummary: n\nstatus: open\nmeta:\n  start: 2030-01-01\n---\n")
        (s.root / "records/Unclosed.md").write_text("---\nsummary: u\nstatus: open\n")
        (s.root / "records/NoSummary.md").write_text("---\nstatus: open\n---\n")
        (s.root / "records/BadRepeat.md").write_text("---\nsummary: b\nstatus: open\nstart: 2026-11-26\nrepeat: fortnightly\n---\n")
        (s.root / "records/RepeatNoStart.md").write_text("---\nsummary: r\nstatus: open\nrepeat: weekly\n---\n")
        (s.root / "records/Empty.md").write_text("")
        p = run(s.root, "--now", "2026-11-26T12:00")
        g = groups(p.stdout)
        self.assertEqual(g["records/Nested.md"], "now")  # nested start is not a field
        self.assertEqual(g["records/Dup.md"], "now")
        self.assertNotIn("records/Latin1.md", g)
        self.assertNotIn("records/Unclosed.md", g)
        for msg in ("Latin1.md: not UTF-8", "Dup.md: duplicate status", "NoSummary.md: task has no summary",
                    "BadRepeat.md: invalid repeat", "RepeatNoStart.md: repeat without start"):
            self.assertIn(msg, p.stderr)

    def test_complete_rejects_window(self):
        s = Store(self)
        s.rec("T.md", summary="t", status="open")
        p = run(s.root, "--next-week", "complete", "T", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("only apply to listing", p.stderr)
        self.assertEqual(s.fm("T.md")["status"], "open")

    def test_empty_store(self):
        s = Store(self)
        self.assertEqual(run(s.root).stdout, "(no active tasks)\n")

    def test_days_limit(self):
        s = Store(self)
        p = run(s.root, "--days", "10000000", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--days must be between", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
