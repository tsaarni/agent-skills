package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"
	"unicode/utf8"
)

// tildePath replaces the home directory prefix with "~".
func tildePath(p string) string {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return p
	}
	if p == home {
		return "~"
	}
	if strings.HasPrefix(p, home+"/") {
		return "~" + strings.TrimPrefix(p, home)
	}
	return p
}

// relPath returns p relative to the workspace when it lies inside it,
// otherwise p with the home directory abbreviated.
func relPath(p, ws string) string {
	if ws != "" && strings.HasPrefix(p, strings.TrimRight(ws, "/")+"/") {
		if rel, err := filepath.Rel(ws, p); err == nil {
			return rel
		}
	}
	return tildePath(p)
}

// wsLabel is the display name of a workspace.
func wsLabel(ws string) string {
	if ws == "" {
		return "(none)"
	}
	return tildePath(ws)
}

func wsBase(ws string) string {
	if ws == "" {
		return "(none)"
	}
	return filepath.Base(ws)
}

// shorten collapses whitespace and cuts s to at most n runes.
func shorten(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	if n <= 3 {
		return string(r[:n])
	}
	return string(r[:n-3]) + "..."
}

func fmtCredits(c float64) string { return fmt.Sprintf("%.1f", c) }

// fmtDur renders a duration compactly: 1h52m, 5m30s, 12s.
func fmtDur(d time.Duration) string {
	d = d.Round(time.Second)
	h := int(d / time.Hour)
	m := int(d % time.Hour / time.Minute)
	s := int(d % time.Minute / time.Second)
	switch {
	case h > 0:
		return fmt.Sprintf("%dh%02dm", h, m)
	case m > 0:
		return fmt.Sprintf("%dm%02ds", m, s)
	default:
		return fmt.Sprintf("%ds", s)
	}
}

func newTable(w io.Writer) *tabwriter.Writer {
	return tabwriter.NewWriter(w, 0, 0, 3, ' ', 0)
}

// snippet returns the part of s around the first occurrence at [start,end),
// on a single line.
func snippet(s string, start, end, context int) string {
	lo := max(0, start-context)
	hi := min(len(s), end+context)
	// Stay on rune boundaries.
	for lo > 0 && !utf8.RuneStart(s[lo]) {
		lo--
	}
	for hi < len(s) && !utf8.RuneStart(s[hi]) {
		hi++
	}
	out := strings.Join(strings.Fields(s[lo:hi]), " ")
	if lo > 0 {
		out = "..." + out
	}
	if hi < len(s) {
		out += "..."
	}
	return out
}

func weekday(t time.Time) string { return t.Format("Mon 2006-01-02") }
