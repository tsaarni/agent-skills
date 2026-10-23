package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

type listRow struct {
	ID        string
	Engine    string
	Workspace string
	Title     string
	Agent     string
	Updated   string
	Turns     int
	Credits   float64
}

func newListCmd(g *globals) *cobra.Command {
	var tf timeFlags
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List sessions (default: last 7 days)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			now := g.now()
			w, err := tf.resolve(now, trailingWeek(now))
			if err != nil {
				return err
			}
			return runList(g, w)
		},
	}
	g.addFilters(cmd, &tf)
	return cmd
}

func runList(g *globals, w Window) error {
	st, err := g.store()
	if err != nil {
		return err
	}
	f, err := g.filter(w)
	if err != nil {
		return err
	}
	hs, err := st.List(f)
	if err != nil {
		return err
	}

	var load []SessionHeader
	for _, h := range hs {
		if h.Turns != 0 {
			load = append(load, h)
		}
	}

	var rows []listRow
	for _, s := range st.LoadAll(load, LoadOpts{}, g.err) {
		var turns []Turn
		for _, ref := range turnsIn([]*Session{s}, w) {
			turns = append(turns, *ref.T)
		}
		if len(turns) == 0 {
			continue
		}
		var credits float64
		for _, t := range turns {
			credits += t.Credits
		}
		title := redact(s.DisplayTitle())
		rows = append(rows, listRow{
			ID: s.ID, Engine: s.Engine, Workspace: s.Workspace, Title: title, Agent: s.Agent,
			Updated: s.Updated.Format("2006-01-02 15:04"), Turns: len(turns), Credits: credits,
		})
	}

	if len(rows) == 0 {
		fmt.Fprintln(g.out, "No sessions found.")
		return nil
	}
	tw := newTable(g.out)
	fmt.Fprintln(tw, "UPDATED\tENGINE\tID\tWORKSPACE\tTURNS\tCREDITS\tTITLE")
	for _, r := range rows {
		fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%d\t%s\t%s\n",
			r.Updated, r.Engine, shortID(r.ID), wsLabel(r.Workspace), r.Turns, fmtCredits(r.Credits), shorten(r.Title, 60))
	}
	return tw.Flush()
}
