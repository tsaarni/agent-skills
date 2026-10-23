package main

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type journalCommit struct {
	Hash    string
	Subject string
}

type journalSession struct {
	ID      string
	Engine  string
	Title   string
	Turns   int
	Edited  []string
	Commits []journalCommit
	Pushes  []string
	PRs     []string
}

type journalWorkspace struct {
	Workspace      string
	ActiveSec      float64
	First          string
	Last           string
	Credits        float64
	UnmeteredTurns int
	Sessions       []*journalSession

	active      time.Duration
	first, last time.Time
	byID        map[string]*journalSession
}

type journalDay struct {
	Date       string
	Workspaces []*journalWorkspace

	day  time.Time
	byWS map[string]*journalWorkspace
}

func newJournalCmd(g *globals) *cobra.Command {
	var tf timeFlags
	cmd := &cobra.Command{
		Use:   "journal",
		Short: "Work history by day and workspace (default: today)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			today := startOfDay(now)
			w, err := tf.resolve(now, Window{today, today.AddDate(0, 0, 1)})
			if err != nil {
				return err
			}
			return runJournal(g, w)
		},
	}
	g.addFilters(cmd, &tf)
	return cmd
}

func runJournal(g *globals, w Window) error {
	sessions, err := g.loadWindow(w, LoadOpts{OutputFor: isGitActionTool})
	if err != nil {
		return err
	}
	days := buildJournal(turnsIn(sessions, w))
	for _, d := range days {
		for _, ws := range d.Workspaces {
			for _, s := range ws.Sessions {
				s.Title = redact(s.Title)
				for j := range s.Commits {
					s.Commits[j].Subject = redact(s.Commits[j].Subject)
				}
				for j := range s.Pushes {
					s.Pushes[j] = redact(s.Pushes[j])
				}
			}
		}
	}
	printJournal(g, days)
	return nil
}

func buildJournal(refs []turnRef) []*journalDay {
	var days []*journalDay
	byDay := map[string]*journalDay{}

	for _, r := range refs { // refs are ordered by start time
		dkey := r.T.Start.Format("2006-01-02")
		d := byDay[dkey]
		if d == nil {
			d = &journalDay{Date: dkey, day: startOfDay(r.T.Start), byWS: map[string]*journalWorkspace{}}
			byDay[dkey] = d
			days = append(days, d)
		}
		ws := d.byWS[r.S.Workspace]
		if ws == nil {
			ws = &journalWorkspace{Workspace: r.S.Workspace, byID: map[string]*journalSession{}}
			d.byWS[r.S.Workspace] = ws
			d.Workspaces = append(d.Workspaces, ws)
		}
		ws.active += r.T.Duration
		if ws.first.IsZero() || r.T.Start.Before(ws.first) {
			ws.first = r.T.Start
		}
		if r.T.End.After(ws.last) {
			ws.last = r.T.End
		}
		if r.T.Metered {
			ws.Credits += r.T.Credits
		} else {
			ws.UnmeteredTurns++
		}

		js := ws.byID[r.S.ID]
		if js == nil {
			js = &journalSession{ID: shortID(r.S.ID), Engine: r.S.Engine, Title: shorten(r.S.DisplayTitle(), 100)}
			ws.byID[r.S.ID] = js
			ws.Sessions = append(ws.Sessions, js)
		}
		js.Turns++
		addActivity(js, r.S.Workspace, r.T)
	}

	for _, d := range days {
		for _, ws := range d.Workspaces {
			ws.ActiveSec = ws.active.Seconds()
			ws.First = ws.first.Format("15:04")
			ws.Last = ws.last.Format("15:04")
		}
	}
	return days
}

// addActivity records edits, commits, pushes and PRs of one turn.
func addActivity(js *journalSession, workspace string, t *Turn) {
	for _, tc := range t.Tools {
		if tc.Status != StatusOK {
			continue
		}
		switch tc.Kind {
		case KindWrite:
			for _, p := range tc.Paths {
				rp := relPath(p, workspace)
				if !contains(js.Edited, rp) {
					js.Edited = append(js.Edited, rp)
				}
			}
		case KindShell:
			a := detectGit(tc)
			js.Commits = append(js.Commits, a.Commits...)
			js.Pushes = append(js.Pushes, a.Pushes...)
			for _, pr := range a.PRs {
				if !contains(js.PRs, pr) {
					js.PRs = append(js.PRs, pr)
				}
			}
		}
	}
}

func printJournal(g *globals, days []*journalDay) {
	if len(days) == 0 {
		fmt.Fprintln(g.out, "No activity found in the selected period.")
		return
	}
	for i, d := range days {
		if i > 0 {
			fmt.Fprintln(g.out)
		}
		fmt.Fprintf(g.out, "## %s\n", weekday(d.day))
		for _, ws := range d.Workspaces {
			fmt.Fprintf(g.out, "\n### %s   active %s (%s-%s)   %s credits",
				wsLabel(ws.Workspace), fmtDur(ws.active), ws.First, ws.Last, fmtCredits(ws.Credits))
			if ws.UnmeteredTurns > 0 {
				fmt.Fprintf(g.out, " (+%d turns without usage data)", ws.UnmeteredTurns)
			}
			fmt.Fprintln(g.out)
			for _, s := range ws.Sessions {
				fmt.Fprintf(g.out, "- %s (sess %s)\n", s.Title, s.ID)
				if len(s.Edited) > 0 {
					fmt.Fprintf(g.out, "  - edited: %s\n", listWithMore(s.Edited, 5))
				}
				for _, c := range s.Commits {
					fmt.Fprintf(g.out, "  - commit: %s \"%s\"\n", c.Hash, c.Subject)
				}
				for _, p := range s.Pushes {
					fmt.Fprintf(g.out, "  - push: %s\n", p)
				}
				for _, pr := range s.PRs {
					fmt.Fprintf(g.out, "  - pr: %s\n", pr)
				}
			}
		}
	}
}

func listWithMore(items []string, max int) string {
	if len(items) <= max {
		return strings.Join(items, ", ")
	}
	return fmt.Sprintf("%s (+%d)", strings.Join(items[:max], ", "), len(items)-max)
}

// --- git / gh detection ---

type gitActivity struct {
	Commits []journalCommit
	Pushes  []string
	PRs     []string
}

var (
	segmentSplit  = regexp.MustCompile(`&&|\|\||;|\||\n`)
	commitLine    = regexp.MustCompile(`(?m)^\[(.+?) ([0-9a-f]{7,40})\] (.+)$`)
	prURL         = regexp.MustCompile(`https://[^\s"')>]+/pull/\d+`)
	gitValueFlags = map[string]bool{"-C": true, "-c": true, "--git-dir": true, "--work-tree": true, "--namespace": true}
)

type shellSegment struct {
	tool string   // "git" or "gh"
	sub  []string // subcommand and arguments
}

// segments finds git and gh invocations in a shell command line. Quoting is
// not interpreted; a segment only counts when it starts with the tool name
// (after env assignments), which keeps text inside quoted messages from
// matching in practice.
func segments(cmd string) []shellSegment {
	var out []shellSegment
	for _, seg := range segmentSplit.Split(cmd, -1) {
		f := strings.Fields(seg)
		for len(f) > 0 && (strings.Contains(f[0], "=") && !strings.HasPrefix(f[0], "-") || f[0] == "(" || f[0] == "{") {
			f = f[1:]
		}
		if len(f) == 0 {
			continue
		}
		switch f[0] {
		case "git":
			i := 1
			for i < len(f) && strings.HasPrefix(f[i], "-") {
				if gitValueFlags[f[i]] {
					i++
				}
				i++
			}
			if i < len(f) {
				out = append(out, shellSegment{"git", f[i:]})
			}
		case "gh":
			out = append(out, shellSegment{"gh", f[1:]})
		}
	}
	return out
}

// isGitActionTool selects the tool calls whose output the journal needs.
func isGitActionTool(tc *ToolCall) bool {
	if tc.Kind != KindShell {
		return false
	}
	return strings.Contains(tc.Command, "commit") || strings.Contains(tc.Command, "pr create")
}

// detectGit extracts commits, pushes and PRs from a successful shell call.
func detectGit(tc ToolCall) gitActivity {
	var a gitActivity
	if tc.Kind != KindShell || tc.Status != StatusOK {
		return a
	}
	for _, s := range segments(tc.Command) {
		switch {
		case s.tool == "git" && s.sub[0] == "commit":
			for _, m := range commitLine.FindAllStringSubmatch(tc.Output, -1) {
				a.Commits = append(a.Commits, journalCommit{Hash: m[2], Subject: strings.TrimSpace(m[3])})
			}
		case s.tool == "git" && s.sub[0] == "push":
			var pos []string
			for _, arg := range s.sub[1:] {
				if !strings.HasPrefix(arg, "-") {
					pos = append(pos, strings.Trim(arg, `"'`))
				}
			}
			a.Pushes = append(a.Pushes, strings.Join(pos, " "))
		case s.tool == "gh" && len(s.sub) >= 2 && s.sub[0] == "pr" && s.sub[1] == "create":
			if u := prURL.FindString(tc.Output); u != "" {
				a.PRs = append(a.PRs, u)
			}
		}
	}
	for i, p := range a.Pushes {
		if p == "" {
			a.Pushes[i] = "(default remote)"
		}
	}
	return a
}
