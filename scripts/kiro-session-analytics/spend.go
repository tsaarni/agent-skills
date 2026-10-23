package main

import (
	"fmt"
	"sort"
	"strings"

	"github.com/spf13/cobra"
)

// topTurns is the number of most expensive turns listed below the table.
const topTurns = 5

type spendRow struct {
	Key     string
	Credits float64
	Turns   int // turns with usage data
	Avg     float64

	// Turns that also logged a request count (v3).
	ReqTurns   int
	Requests   int
	ReqCredits float64
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
	Turns     int // turns with usage data
	Avg       float64
	Unmetered int
	Rows      []spendRow
	TopTurns  []spendTop
}

var spendGroups = []string{"workspace", "model", "agent", "day", "session"}

func newSpendCmd(g *globals) *cobra.Command {
	var tf timeFlags
	var by string
	cmd := &cobra.Command{
		Use:   "spend",
		Short: "Where the credits went: by workspace, model, agent, day or session (default: last 7 days)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			w, err := tf.resolve(now, trailingWeek(now))
			if err != nil {
				return err
			}
			return runSpend(g, w, by)
		},
	}
	g.addFilters(cmd, &tf)
	cmd.Flags().StringVar(&by, "by", "workspace", "group by: "+strings.Join(spendGroups, ", "))
	return cmd
}

func runSpend(g *globals, w Window, by string) error {
	if !contains(spendGroups, by) {
		return fmt.Errorf("--by must be one of %v, got %q", spendGroups, by)
	}
	sessions, err := g.loadWindow(w, LoadOpts{})
	if err != nil {
		return err
	}
	rep := buildSpend(turnsIn(sessions, w), by)
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
		// Tabs make the parts separate columns, see spendHeader.
		return fmt.Sprintf("%s\t%s\t%s\t%s", shortID(r.S.ID), r.S.Updated.Format("01-02 15:04"),
			wsBase(r.S.Workspace), shorten(r.S.DisplayTitle(), 40))
	}
}

// groupSpend aggregates the turns with usage data into rows keyed by
// spendKey(by).
func groupSpend(refs []turnRef, by string) []spendRow {
	rows := map[string]*spendRow{}
	for _, r := range refs {
		if !r.T.Metered {
			continue
		}
		k := spendKey(r, by)
		row := rows[k]
		if row == nil {
			row = &spendRow{Key: k}
			rows[k] = row
		}
		row.Turns++
		row.Credits += r.T.Credits
		if r.T.Requests > 0 {
			row.ReqTurns++
			row.Requests += r.T.Requests
			row.ReqCredits += r.T.Credits
		}
	}
	var out []spendRow
	for _, row := range rows {
		row.Avg = row.Credits / float64(row.Turns)
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

func buildSpend(refs []turnRef, by string) spendReport {
	rep := spendReport{By: by}
	var metered []turnRef
	for _, r := range refs {
		if r.T.Metered {
			rep.Credits += r.T.Credits
			rep.Turns++
			metered = append(metered, r)
		} else {
			rep.Unmetered++
		}
	}
	if rep.Turns > 0 {
		rep.Avg = rep.Credits / float64(rep.Turns)
	}
	if len(refs) > 0 {
		rep.From = refs[0].T.Start.Format("2006-01-02")
		rep.To = refs[len(refs)-1].T.Start.Format("2006-01-02")
	}
	rep.Rows = groupSpend(refs, by)

	sort.SliceStable(metered, func(i, j int) bool { return metered[i].T.Credits > metered[j].T.Credits })
	if len(metered) > topTurns {
		metered = metered[:topTurns]
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
	return rep
}

func printSpend(g *globals, rep spendReport) {
	if rep.Turns == 0 && rep.Unmetered == 0 {
		fmt.Fprintln(g.out, "No turns found in the selected period.")
		return
	}
	fmt.Fprintf(g.out, "Credits %s .. %s    total %s   turns %d   avg %s/turn\n",
		rep.From, rep.To, fmtCredits(rep.Credits), rep.Turns, fmtCredits(rep.Avg))
	if rep.Unmetered > 0 {
		fmt.Fprintf(g.out, "(%d more turns have no usage data and are not counted)\n", rep.Unmetered)
	}
	fmt.Fprintln(g.out)

	printSpendRows(g, spendHeader(rep.By), rep.By == "model", rep.Rows)

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

// printSpendRows prints the grouped table. With requests set it adds the
// request columns, which split a turn's cost into steps and price per step.
func printSpendRows(g *globals, header string, requests bool, rows []spendRow) {
	tw := newTable(g.out)
	if requests {
		fmt.Fprintf(tw, "%s\tCREDITS\tTURNS\tCREDITS/TURN\tREQ/TURN\tCREDITS/REQ\n", header)
	} else {
		fmt.Fprintf(tw, "%s\tCREDITS\tTURNS\tCREDITS/TURN\n", header)
	}
	for _, r := range rows {
		fmt.Fprintf(tw, "%s\t%s\t%d\t%s", r.Key, fmtCredits(r.Credits), r.Turns, fmtCredits(r.Avg))
		if requests {
			reqTurn, perReq := "-", "-"
			if r.Requests > 0 {
				reqTurn = fmt.Sprintf("%.1f", float64(r.Requests)/float64(r.ReqTurns))
				perReq = fmt.Sprintf("%.2f", r.ReqCredits/float64(r.Requests))
			}
			fmt.Fprintf(tw, "\t%s\t%s", reqTurn, perReq)
		}
		fmt.Fprintln(tw)
	}
	tw.Flush()
}

// spendHeader is the header of the key column(s).
func spendHeader(by string) string {
	if by == "session" {
		return "SESSION\tUPDATED\tWORKSPACE\tTITLE"
	}
	return upper(by)
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
