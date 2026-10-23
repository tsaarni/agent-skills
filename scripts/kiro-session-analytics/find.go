package main

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type findOpts struct {
	File, Cmd, Text, Output string
}

type findHit struct {
	Time      string
	Engine    string
	Workspace string
	Session   string
	SessionID string
	Turn      int
	Match     string // cmd | file | output | text
	Detail    string

	at time.Time
}

func newFindCmd(g *globals) *cobra.Command {
	var tf timeFlags
	var o findOpts
	cmd := &cobra.Command{
		Use:   "find",
		Short: "Find the moments where a command ran, a file was touched or a text appeared",
		Long: `Find turns across all sessions in the time window (default: last 7 days).
Search options combine with AND, together with the time and workspace
filters. Patterns are case-insensitive substrings.

--output scans tool output and is the slowest option.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			w, err := tf.resolve(now, trailingWeek(now))
			if err != nil {
				return err
			}
			return runFind(g, w, o)
		},
	}
	g.addFilters(cmd, &tf)
	f := cmd.Flags()
	f.StringVar(&o.File, "file", "", "file read or written (matched by path suffix)")
	f.StringVar(&o.Cmd, "cmd", "", "shell command")
	f.StringVar(&o.Text, "text", "", "prompt or response text")
	f.StringVar(&o.Output, "output", "", "tool output (slow)")
	return cmd
}

// matcher finds a case-insensitive pattern in text.
type matcher struct {
	sub string // lower-cased
}

func newMatcher(pattern string) *matcher {
	if pattern == "" {
		return nil
	}
	return &matcher{sub: strings.ToLower(pattern)}
}

func (m *matcher) find(s string) (start, end int, ok bool) {
	i := strings.Index(strings.ToLower(s), m.sub)
	if i < 0 {
		return 0, 0, false
	}
	// Lower-casing can change byte lengths for rare characters; clamp.
	start = min(i, len(s))
	return start, min(start+len(m.sub), len(s)), true
}

// matchPath matches by path suffix.
func (m *matcher) matchPath(p string) bool {
	pat := strings.TrimPrefix(filepath.ToSlash(expandHome(m.sub)), "./")
	lp := strings.ToLower(p)
	return lp == pat || strings.HasSuffix(lp, "/"+pat)
}

func runFind(g *globals, w Window, o findOpts) error {
	if o.File == "" && o.Cmd == "" && o.Text == "" && o.Output == "" {
		return fmt.Errorf("give at least one of --file, --cmd, --text, --output")
	}
	mFile, mCmd, mText, mOut := newMatcher(o.File), newMatcher(o.Cmd), newMatcher(o.Text), newMatcher(o.Output)

	sessions, err := g.loadWindow(w, LoadOpts{ToolOutput: mOut != nil})
	if err != nil {
		return err
	}

	var hits []findHit
	for _, r := range turnsIn(sessions, w) {
		hits = append(hits, matchTurn(r, mFile, mCmd, mText, mOut)...)
	}
	sort.SliceStable(hits, func(i, j int) bool { return hits[i].at.After(hits[j].at) })
	for i := range hits {
		hits[i].Detail = redact(hits[i].Detail)
	}

	if len(hits) == 0 {
		fmt.Fprintln(g.out, "No matches.")
		return nil
	}
	now := g.now()
	tw := newTable(g.out)
	for _, h := range hits {
		ts := h.at.Format("01-02 15:04")
		if h.at.Year() != now.Year() {
			ts = h.at.Format("2006-01-02 15:04")
		}
		fmt.Fprintf(tw, "%s\t%s\tsess %s #%d\t%s\n", ts, wsBase(h.Workspace), h.Session, h.Turn, h.Detail)
	}
	tw.Flush()
	return nil
}

// matchTurn returns the hits of one turn. Every given criterion must match
// something in the turn; the hits describe the items of the first criterion
// in the order cmd, file, output, text.
func matchTurn(r turnRef, mFile, mCmd, mText, mOut *matcher) []findHit {
	base := findHit{
		at: r.T.Start, Time: r.T.Start.Format(time.RFC3339), Engine: r.S.Engine, Workspace: r.S.Workspace,
		Session: shortID(r.S.ID), SessionID: r.S.ID, Turn: r.T.Index,
	}
	mk := func(match, detail string) findHit {
		h := base
		h.Match, h.Detail = match, detail
		return h
	}

	var cmdHits, fileHits, outHits, textHits []findHit

	for _, tc := range r.T.Tools {
		if mCmd != nil && tc.Kind == KindShell {
			if _, _, ok := mCmd.find(tc.Command); ok {
				cmdHits = append(cmdHits, mk("cmd", "$ "+shorten(tc.Command, 120)+statusTag(tc.Status)))
			}
		}
		if mFile != nil && (tc.Kind == KindWrite || tc.Kind == KindRead) {
			for _, p := range tc.Paths {
				if mFile.matchPath(p) {
					verb := "read"
					if tc.Kind == KindWrite {
						verb = "edit"
					}
					fileHits = append(fileHits, mk("file", verb+" "+relPath(p, r.S.Workspace)+statusTag(tc.Status)))
					break
				}
			}
		}
		if mOut != nil && tc.Output != "" {
			if s, e, ok := mOut.find(tc.Output); ok {
				label := tc.Name
				if tc.Kind == KindShell {
					label = "$ " + shorten(tc.Command, 50)
				}
				outHits = append(outHits, mk("output", fmt.Sprintf("%s -> %s", label, snippet(tc.Output, s, e, 50))))
			}
		}
	}
	if mText != nil {
		if s, e, ok := mText.find(r.T.Prompt); ok {
			textHits = append(textHits, mk("text", "> "+snippet(r.T.Prompt, s, e, 60)))
		}
		if s, e, ok := mText.find(r.T.Response); ok {
			textHits = append(textHits, mk("text", "< "+snippet(r.T.Response, s, e, 60)))
		}
	}

	if (mCmd != nil && len(cmdHits) == 0) || (mFile != nil && len(fileHits) == 0) ||
		(mOut != nil && len(outHits) == 0) || (mText != nil && len(textHits) == 0) {
		return nil
	}
	switch {
	case mCmd != nil:
		return dedupe(cmdHits)
	case mFile != nil:
		return dedupe(fileHits)
	case mOut != nil:
		return dedupe(outHits)
	default:
		return dedupe(textHits)
	}
}

// dedupe drops hits of one turn that read the same (for example repeated
// edits of one file).
func dedupe(hits []findHit) []findHit {
	seen := map[string]bool{}
	out := hits[:0]
	for _, h := range hits {
		if !seen[h.Detail] {
			seen[h.Detail] = true
			out = append(out, h)
		}
	}
	return out
}

func statusTag(status string) string {
	if status == StatusOK || status == "" {
		return ""
	}
	return " [" + status + "]"
}
