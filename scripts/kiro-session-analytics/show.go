package main

import (
	"fmt"
	"io"
	"strings"

	"github.com/spf13/cobra"
)

const defaultOutputLines = 6

func newShowCmd(g *globals) *cobra.Command {
	var outputLines int
	cmd := &cobra.Command{
		Use:   "show <session>[#turn]",
		Short: "Print one turn, or a session, in readable form",
		Long: `Print a turn ("5437257e#12") or a session ("5437257e").

A turn is shown with its prompt, tool calls and response. Tool output is cut
to a few lines; use --output-lines N to change that.

A session without a turn number is shown as a list of turns.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runShow(g, args[0], outputLines)
		},
	}
	cmd.Flags().IntVar(&outputLines, "output-lines", defaultOutputLines, "lines of tool output to show per call (0 = none)")
	return cmd
}

func runShow(g *globals, selector string, outputLines int) error {
	sel, err := parseSelector(selector)
	if err != nil {
		return err
	}
	st, err := g.store()
	if err != nil {
		return err
	}
	h, err := st.Resolve(sel.Prefix)
	if err != nil {
		return err
	}
	sess, err := st.Load(h, LoadOpts{ToolOutput: true})
	if err != nil {
		return err
	}
	redactSession(sess)

	if sel.Turn > 0 {
		var turn *Turn
		for i := range sess.Turns {
			if sess.Turns[i].Index == sel.Turn {
				turn = &sess.Turns[i]
			}
		}
		if turn == nil {
			return fmt.Errorf("session %s has no turn #%d (%d turns)", shortID(sess.ID), sel.Turn, len(sess.Turns))
		}
		printTurn(g.out, sess, turn, outputLines)
		return nil
	}

	printSessionSummary(g.out, sess)
	return nil
}

func turnHeader(s *Session, t *Turn) string {
	usage := "no usage data"
	if t.Metered {
		usage = fmtCredits(t.Credits) + " credits"
	}
	model := t.Model
	if model == "" {
		model = "unknown model"
	}
	return fmt.Sprintf("── sess %s #%d   %s   %s   %s   %s   %s",
		shortID(s.ID), t.Index, t.Start.Format("2006-01-02 15:04"), wsBase(s.Workspace), model, usage, fmtDur(t.Duration))
}

func printTurn(w io.Writer, s *Session, t *Turn, outputLines int) {
	fmt.Fprintln(w, turnHeader(s, t))
	if t.Prompt != "" {
		fmt.Fprintln(w, prefixLines(t.Prompt, "> "))
	}
	for _, tc := range t.Tools {
		printTool(w, tc, s.Workspace, outputLines)
	}
	if t.Response != "" {
		fmt.Fprintln(w, prefixLines(t.Response, "< "))
	}
}

func printTool(w io.Writer, tc ToolCall, workspace string, outputLines int) {
	tag := ""
	if tc.Kind == KindShell || tc.Kind == KindWrite || tc.Status != StatusOK {
		tag = "     [" + tc.Status + "]"
	}
	switch tc.Kind {
	case KindShell:
		fmt.Fprintf(w, "$ %s%s\n", tc.Command, tag)
		printOutput(w, tc.Output, outputLines)
	case KindWrite:
		for _, p := range tc.Paths {
			fmt.Fprintf(w, "✎ %s%s\n", relPath(p, workspace), tag)
		}
		if len(tc.Paths) == 0 {
			fmt.Fprintf(w, "✎ %s%s\n", tc.Name, tag)
		}
	case KindRead:
		fmt.Fprintf(w, "· read %s%s\n", joinRel(tc.Paths, workspace), tag)
	default:
		fmt.Fprintf(w, "· %s%s\n", tc.Name, tag)
	}
}

func joinRel(paths []string, ws string) string {
	rel := make([]string, len(paths))
	for i, p := range paths {
		rel[i] = relPath(p, ws)
	}
	return strings.Join(rel, ", ")
}

// printOutput prints tool output indented, cut to max lines (-1 = all).
func printOutput(w io.Writer, out string, max int) {
	out = strings.Trim(out, "\n")
	if out == "" || max == 0 {
		return
	}
	lines := strings.Split(out, "\n")
	shown := lines
	if max > 0 && len(lines) > max {
		shown = lines[:max]
	}
	for _, l := range shown {
		fmt.Fprintf(w, "  %s\n", l)
	}
	if len(shown) < len(lines) {
		fmt.Fprintf(w, "  (%d more lines)\n", len(lines)-len(shown))
	}
}

func prefixLines(s, prefix string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	for i, l := range lines {
		lines[i] = prefix + l
	}
	return strings.Join(lines, "\n")
}

func printSessionSummary(w io.Writer, s *Session) {
	fmt.Fprintf(w, "── sess %s   %s   %s   %s   %s\n", shortID(s.ID), s.Engine, wsLabel(s.Workspace), s.Agent, s.Updated.Format("2006-01-02 15:04"))
	fmt.Fprintf(w, "   %s\n", s.DisplayTitle())
	fmt.Fprintf(w, "   %d turns, %s credits\n\n", len(s.Turns), fmtCredits(s.TotalCredits()))
	tw := newTable(w)
	fmt.Fprintln(tw, "#\tSTART\tCREDITS\tDURATION\tMODEL\tTOOLS\tPROMPT")
	for _, t := range s.Turns {
		credits := "-"
		if t.Metered {
			credits = fmtCredits(t.Credits)
		}
		fmt.Fprintf(tw, "%d\t%s\t%s\t%s\t%s\t%d\t%s\n",
			t.Index, t.Start.Format("2006-01-02 15:04"), credits, fmtDur(t.Duration), t.Model, len(t.Tools), shorten(t.Prompt, 60))
	}
	tw.Flush()
}
