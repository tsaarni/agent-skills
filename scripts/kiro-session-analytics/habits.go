package main

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// The habits report: when you work, how long your sessions are, and what that
// costs.
//
// Sessions and turn numbers are bucketed in steps of lengthStep, up to the
// longest session in the period. The cost-by-turn-number table is split by
// model, to separate the effect of a growing conversation from the effect of
// the model choice.

const (
	lengthStep      = 5 // sessions and turn numbers are bucketed in steps of this size
	maxTurnBuckets  = 8 // columns of the cost-by-turn-number table; the last is open-ended
	minCellRequests = 3 // turns needed before a cost-by-turn-number cell is shown
	barWidth        = 30
)

var weekdayNames = []string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

func weekdayIndex(t time.Time) int { return (int(t.Weekday()) + 6) % 7 }

func newHabitsCmd(g *globals) *cobra.Command {
	var tf timeFlags
	cmd := &cobra.Command{
		Use:   "habits",
		Short: "When you work and how session length affects cost (default: last 30 days)",
		Long: `When you work and how session length affects cost.

Prints turns, credits and active time by hour of day and by weekday, then
sessions grouped by number of turns, then credits per model request by turn
number for each model. Session length tables use v3 sessions only.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			today := startOfDay(now)
			w, err := tf.resolve(now, Window{today.AddDate(0, 0, -29), today.AddDate(0, 0, 1)})
			if err != nil {
				return err
			}
			return runHabits(g, w)
		},
	}
	g.addFilters(cmd, &tf)
	return cmd
}

func runHabits(g *globals, w Window) error {
	sessions, err := g.loadWindow(w, LoadOpts{})
	if err != nil {
		return err
	}
	refs := turnsIn(sessions, w)
	if len(refs) == 0 {
		fmt.Fprintln(g.out, "No turns found in the selected period.")
		return nil
	}
	fmt.Fprintf(g.out, "Habits %s .. %s    turns %d\n\n",
		refs[0].T.Start.Format("2006-01-02"), refs[len(refs)-1].T.Start.Format("2006-01-02"), len(refs))

	hours := fillHours(groupActivity(refs, func(r turnRef) string { return fmt.Sprintf("%02d:00", r.T.Start.Hour()) }))
	printActivity(g, "HOUR", hours)
	fmt.Fprintln(g.out)
	days := fillWeekdays(groupActivity(refs, func(r turnRef) string { return weekdayNames[weekdayIndex(r.T.Start)] }))
	printActivity(g, "WEEKDAY", days)
	fmt.Fprintln(g.out)
	printLength(g, buildLength(refs))
	return nil
}

// --- activity by hour and weekday ---

type activityRow struct {
	Key     string
	Turns   int
	Credits float64
	Active  time.Duration
}

// groupActivity counts every turn, with or without usage data; credits
// include only the turns that have it.
func groupActivity(refs []turnRef, key func(turnRef) string) []activityRow {
	rows := map[string]*activityRow{}
	for _, r := range refs {
		k := key(r)
		row := rows[k]
		if row == nil {
			row = &activityRow{Key: k}
			rows[k] = row
		}
		row.Turns++
		row.Active += r.T.Duration
		if r.T.Metered {
			row.Credits += r.T.Credits
		}
	}
	out := make([]activityRow, 0, len(rows))
	for _, r := range rows {
		out = append(out, *r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}

// fillWeekdays returns one row for each weekday, Monday first.
func fillWeekdays(rows []activityRow) []activityRow {
	byKey := map[string]activityRow{}
	for _, r := range rows {
		byKey[r.Key] = r
	}
	out := make([]activityRow, 0, len(weekdayNames))
	for _, d := range weekdayNames {
		r, ok := byKey[d]
		if !ok {
			r = activityRow{Key: d}
		}
		out = append(out, r)
	}
	return out
}

// fillHours returns one row for each hour between the first and last active
// hour, so that gaps in the day are visible.
func fillHours(rows []activityRow) []activityRow {
	if len(rows) == 0 {
		return rows
	}
	byKey := map[string]activityRow{}
	for _, r := range rows {
		byKey[r.Key] = r
	}
	var first, last int
	fmt.Sscanf(rows[0].Key, "%d:", &first)
	fmt.Sscanf(rows[len(rows)-1].Key, "%d:", &last)
	var out []activityRow
	for h := first; h <= last; h++ {
		k := fmt.Sprintf("%02d:00", h)
		r, ok := byKey[k]
		if !ok {
			r = activityRow{Key: k}
		}
		out = append(out, r)
	}
	return out
}

// printActivity prints the rows with a bar proportional to the turn count.
func printActivity(g *globals, header string, rows []activityRow) {
	maxTurns := 0
	for _, r := range rows {
		maxTurns = max(maxTurns, r.Turns)
	}
	tw := newTable(g.out)
	fmt.Fprintf(tw, "%s\tTURNS\tCREDITS\tACTIVE\n", header)
	for _, r := range rows {
		if r.Turns == 0 {
			fmt.Fprintf(tw, "%s\t-\t-\t-\n", r.Key)
			continue
		}
		n := max(1, r.Turns*barWidth/maxTurns)
		fmt.Fprintf(tw, "%s\t%d\t%s\t%s\t%s\n", r.Key, r.Turns, fmtCredits(r.Credits), fmtActive(r.Active), strings.Repeat("█", n))
	}
	tw.Flush()
}

// fmtActive renders active time as minutes, or hours and minutes.
func fmtActive(d time.Duration) string {
	m := int(d.Round(time.Minute) / time.Minute)
	if m >= 60 {
		return fmt.Sprintf("%dh%02dm", m/60, m%60)
	}
	return fmt.Sprintf("%dm", m)
}

// --- session length ---

type lengthRow struct {
	Lo, Hi   int
	Sessions int
	Turns    int
	Metered  int
	Credits  float64
	// Only metered turns that logged a request count.
	ReqTurns   int
	Requests   int
	ReqCredits float64
}

type turnCostRow struct {
	Model   string
	Credits float64 // total, for ordering
	Cells   []turnCostCell
}

type turnCostCell struct {
	Turns      int
	Requests   int
	ReqCredits float64
}

type lengthReport struct {
	Rows     []lengthRow
	Credits  float64 // metered total
	TurnCols []string
	TurnRows []turnCostRow
	// SkippedV2 is set when v2 turns were left out.
	SkippedV2 bool
}

func bucketLabel(lo, hi int) string {
	if lo == hi {
		return fmt.Sprintf("%d", lo)
	}
	return fmt.Sprintf("%d-%d", lo, hi)
}

func buildLength(refs []turnRef) *lengthReport {
	rep := &lengthReport{}
	// v2 credits are incomplete (see AGENTS.md), which would make long
	// sessions look cheap, so only v3 sessions are analysed.
	var v3 []turnRef
	for _, r := range refs {
		if r.S.Engine == "v3" {
			v3 = append(v3, r)
		} else {
			rep.SkippedV2 = true
		}
	}
	refs = v3
	maxLen, maxIdx := 0, 0
	for _, r := range refs {
		maxLen = max(maxLen, len(r.S.Turns))
		maxIdx = max(maxIdx, r.T.Index)
	}
	if maxLen == 0 {
		return rep
	}

	nrows := (maxLen-1)/lengthStep + 1
	rep.Rows = make([]lengthRow, nrows)
	for i := range rep.Rows {
		rep.Rows[i].Lo = i*lengthStep + 1
		rep.Rows[i].Hi = (i + 1) * lengthStep
	}

	// Turn-number columns, capped; the last column is open-ended.
	ncols := min((maxIdx-1)/lengthStep+1, maxTurnBuckets)
	colOf := func(idx int) int { return min((idx-1)/lengthStep, ncols-1) }
	for i := 0; i < ncols; i++ {
		lo, hi := i*lengthStep+1, (i+1)*lengthStep
		if i == ncols-1 && maxIdx > hi {
			rep.TurnCols = append(rep.TurnCols, fmt.Sprintf("%d+", lo))
		} else {
			rep.TurnCols = append(rep.TurnCols, bucketLabel(lo, hi))
		}
	}
	byModel := map[string]*turnCostRow{}

	seen := map[*Session]bool{}
	for _, r := range refs {
		row := &rep.Rows[(len(r.S.Turns)-1)/lengthStep]
		if !seen[r.S] {
			seen[r.S] = true
			row.Sessions++
		}
		row.Turns++
		if !r.T.Metered {
			continue
		}
		row.Metered++
		row.Credits += r.T.Credits
		rep.Credits += r.T.Credits
		if r.T.Requests == 0 {
			continue
		}
		row.ReqTurns++
		row.Requests += r.T.Requests
		row.ReqCredits += r.T.Credits

		m := r.T.Model
		if m == "" {
			m = "unknown"
		}
		mr := byModel[m]
		if mr == nil {
			mr = &turnCostRow{Model: m, Cells: make([]turnCostCell, ncols)}
			byModel[m] = mr
		}
		mr.Credits += r.T.Credits
		c := &mr.Cells[colOf(r.T.Index)]
		c.Turns++
		c.Requests += r.T.Requests
		c.ReqCredits += r.T.Credits
	}

	for _, mr := range byModel {
		rep.TurnRows = append(rep.TurnRows, *mr)
	}
	sort.Slice(rep.TurnRows, func(i, j int) bool {
		if rep.TurnRows[i].Credits != rep.TurnRows[j].Credits {
			return rep.TurnRows[i].Credits > rep.TurnRows[j].Credits
		}
		return rep.TurnRows[i].Model < rep.TurnRows[j].Model
	})
	return rep
}

func printLength(g *globals, rep *lengthReport) {
	if rep.SkippedV2 {
		fmt.Fprintln(g.out, "(session length: v2 sessions are excluded, their credits are incomplete)")
	}
	if len(rep.Rows) == 0 {
		fmt.Fprintln(g.out, "No v3 sessions in the selected period.")
		return
	}
	fmt.Fprintln(g.out, "SESSION LENGTH (turns per session)")
	tw := newTable(g.out)
	fmt.Fprintln(tw, "TURNS\tSESSIONS\tTURNS\tCREDITS\tSHARE\tCREDITS/TURN\tREQ/TURN\tCREDITS/REQ")
	for i := 0; i < len(rep.Rows); i++ {
		r := rep.Rows[i]
		if r.Sessions == 0 {
			// Collapse a run of empty buckets into one row.
			j := i
			for j+1 < len(rep.Rows) && rep.Rows[j+1].Sessions == 0 {
				j++
			}
			fmt.Fprintf(tw, "%s\t-\t-\t-\t-\t-\t-\t-\n", bucketLabel(r.Lo, rep.Rows[j].Hi))
			i = j
			continue
		}
		share, perTurn, reqTurn, perReq := "-", "-", "-", "-"
		if rep.Credits > 0 {
			share = fmt.Sprintf("%.0f%%", 100*r.Credits/rep.Credits)
		}
		if r.Metered > 0 {
			perTurn = fmtCredits(r.Credits / float64(r.Metered))
		}
		if r.ReqTurns > 0 && r.Requests > 0 {
			reqTurn = fmt.Sprintf("%.1f", float64(r.Requests)/float64(r.ReqTurns))
			perReq = fmt.Sprintf("%.2f", r.ReqCredits/float64(r.Requests))
		}
		fmt.Fprintf(tw, "%s\t%d\t%d\t%s\t%s\t%s\t%s\t%s\n",
			bucketLabel(r.Lo, r.Hi), r.Sessions, r.Turns, fmtCredits(r.Credits), share, perTurn, reqTurn, perReq)
	}
	tw.Flush()

	if len(rep.TurnRows) == 0 {
		return
	}
	fmt.Fprintf(g.out, "\nCREDITS PER REQUEST BY TURN NUMBER (same model, - = fewer than %d turns)\n", minCellRequests)
	tw = newTable(g.out)
	fmt.Fprint(tw, "MODEL")
	for _, c := range rep.TurnCols {
		fmt.Fprintf(tw, "\t%s", c)
	}
	fmt.Fprintln(tw)
	for _, r := range rep.TurnRows {
		fmt.Fprint(tw, r.Model)
		for _, c := range r.Cells {
			if c.Turns < minCellRequests || c.Requests == 0 {
				fmt.Fprint(tw, "\t-")
				continue
			}
			fmt.Fprintf(tw, "\t%.2f", c.ReqCredits/float64(c.Requests))
		}
		fmt.Fprintln(tw)
	}
	tw.Flush()
}
