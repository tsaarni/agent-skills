package main

import (
	"fmt"
	"sort"

	"github.com/spf13/cobra"
)

type spendRow struct {
	Key     string
	Credits float64
	Turns   int
	Metered int
	Avg     float64
}

type spendTop struct {
	Credits   float64
	Time      string
	Workspace string
	Session   string
	Turn      int
	Model     string
	Prompt    string
}

type spendReport struct {
	From      string
	To        string
	By        string
	Credits   float64
	Turns     int
	Metered   int
	Avg       float64
	Rows      []spendRow
	ModelRows []spendRow // per-model breakdown, filled when By is not "model"
	TopTurns  []spendTop
	Unmetered int
}

var spendGroups = []string{"workspace", "model", "agent", "day", "session"}

func newSpendCmd(g *globals) *cobra.Command {
	var tf timeFlags
	var by string
	var top int
	cmd := &cobra.Command{
		Use:   "spend",
		Short: "Credits by workspace, model, agent, day or session (default: last 7 days)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			w, err := tf.resolve(now, trailingWeek(now))
			if err != nil {
				return err
			}
			return runSpend(g, w, by, top)
		},
	}
	g.addFilters(cmd, &tf)
	cmd.Flags().IntVar(&top, "top", 5, "number of most expensive turns to list (0 = none)")
	cmd.Flags().StringVar(&by, "by", "workspace", "group by: workspace, model, agent, day, session")
	return cmd
}

func runSpend(g *globals, w Window, by string, top int) error {
	if !contains(spendGroups, by) {
		return fmt.Errorf("--by must be one of %v, got %q", spendGroups, by)
	}
	sessions, err := g.loadWindow(w, LoadOpts{})
	if err != nil {
		return err
	}
	rep := buildSpend(turnsIn(sessions, w), by, top)
	for i := range rep.TopTurns {
		rep.TopTurns[i].Prompt = redact(rep.TopTurns[i].Prompt)
	}
	if by == "session" {
		for i := range rep.Rows {
			rep.Rows[i].Key = redact(rep.Rows[i].Key)
		}
	}
	printSpend(g, rep)
	return nil
}

func spendKey(r turnRef, by string) string {
	switch by {
	case "workspace":
		return wsLabel(r.S.Workspace)
	case "model":
		if r.T.Model == "" {
			return "unknown"
		}
		return r.T.Model
	case "agent":
		if r.S.Agent == "" {
			return "unknown"
		}
		return r.S.Agent
	case "day":
		return r.T.Start.Format("2006-01-02")
	default: // session
		return shortID(r.S.ID) + "  " + shorten(r.S.DisplayTitle(), 40)
	}
}

// groupSpend aggregates refs into rows keyed by spendKey(by).
func groupSpend(refs []turnRef, by string) []spendRow {
	rows := map[string]*spendRow{}
	for _, r := range refs {
		k := spendKey(r, by)
		row := rows[k]
		if row == nil {
			row = &spendRow{Key: k}
			rows[k] = row
		}
		row.Turns++
		if r.T.Metered {
			row.Credits += r.T.Credits
			row.Metered++
		}
	}
	var out []spendRow
	for _, row := range rows {
		if row.Metered > 0 {
			row.Avg = row.Credits / float64(row.Metered)
		}
		out = append(out, *row)
	}
	if by == "day" {
		sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	} else {
		sort.Slice(out, func(i, j int) bool {
			if out[i].Credits != out[j].Credits {
				return out[i].Credits > out[j].Credits
			}
			return out[i].Key < out[j].Key
		})
	}
	return out
}

func buildSpend(refs []turnRef, by string, top int) spendReport {
	rep := spendReport{By: by}
	for _, r := range refs {
		rep.Turns++
		if r.T.Metered {
			rep.Credits += r.T.Credits
			rep.Metered++
		} else {
			rep.Unmetered++
		}
	}
	if rep.Metered > 0 {
		rep.Avg = rep.Credits / float64(rep.Metered)
	}
	if len(refs) > 0 {
		rep.From = refs[0].T.Start.Format("2006-01-02")
		rep.To = refs[len(refs)-1].T.Start.Format("2006-01-02")
	}
	rep.Rows = groupSpend(refs, by)
	if by != "model" {
		rep.ModelRows = groupSpend(refs, "model")
	}

	if top > 0 {
		var metered []turnRef
		for _, r := range refs {
			if r.T.Metered {
				metered = append(metered, r)
			}
		}
		sort.SliceStable(metered, func(i, j int) bool { return metered[i].T.Credits > metered[j].T.Credits })
		if len(metered) > top {
			metered = metered[:top]
		}
		for _, r := range metered {
			rep.TopTurns = append(rep.TopTurns, spendTop{
				Credits:   r.T.Credits,
				Time:      r.T.Start.Format("2006-01-02 15:04"),
				Workspace: r.S.Workspace,
				Session:   shortID(r.S.ID),
				Turn:      r.T.Index,
				Model:     r.T.Model,
				Prompt:    shorten(r.T.Prompt, 60),
			})
		}
	}
	return rep
}

func printSpend(g *globals, rep spendReport) {
	if rep.Turns == 0 {
		fmt.Fprintln(g.out, "No turns found in the selected period.")
		return
	}
	fmt.Fprintf(g.out, "Credits %s .. %s    total %s   turns %d   avg %s/turn\n",
		rep.From, rep.To, fmtCredits(rep.Credits), rep.Turns, fmtCredits(rep.Avg))
	if rep.Unmetered > 0 {
		fmt.Fprintf(g.out, "(%d turns have no usage data and are not counted in credits or the average)\n", rep.Unmetered)
	}
	fmt.Fprintln(g.out)

	printSpendRows(g, upper(rep.By), rep.Rows)
	if len(rep.ModelRows) > 0 {
		fmt.Fprintln(g.out)
		printSpendRows(g, "MODEL", rep.ModelRows)
	}

	if len(rep.TopTurns) > 0 {
		fmt.Fprintln(g.out, "\nTOP TURNS")
		tw := newTable(g.out)
		for _, t := range rep.TopTurns {
			fmt.Fprintf(tw, " %s\t%s\t%s\tsess %s #%d\t\"%s\"\n",
				fmtCredits(t.Credits), t.Time[5:], wsBase(t.Workspace), t.Session, t.Turn, t.Prompt)
		}
		tw.Flush()
	}
}

func printSpendRows(g *globals, header string, rows []spendRow) {
	tw := newTable(g.out)
	fmt.Fprintf(tw, "%s\tCREDITS\tTURNS\tAVG\n", header)
	for _, r := range rows {
		fmt.Fprintf(tw, "%s\t%s\t%d\t%s\n", r.Key, fmtCredits(r.Credits), r.Turns, fmtCredits(r.Avg))
	}
	tw.Flush()
}

func upper(s string) string {
	b := []byte(s)
	for i, c := range b {
		if c >= 'a' && c <= 'z' {
			b[i] = c - 32
		}
	}
	return string(b)
}
