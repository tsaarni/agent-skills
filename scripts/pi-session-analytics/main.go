// Command pi-session-analytics is a deterministic performance, cost economics,
// cache health and habit analysis tool for Pi Coding Agent sessions.
//
// It parses session JSONL logs directly from ~/.pi/agent/sessions/. The
// implementation is split into focused files for a clean design. Run it with:
//
//	cd scripts/pi-session-analytics && go run .
package main

import (
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	"pi-session-analytics/pricing"
)

type options struct {
	path           string
	listSessions   bool
	listWorkspaces bool
	limit          int
	errorsOnly     bool
	recentRank     int
	latest         bool
	globalSearch   bool
	timeline       bool
	today          bool
	yesterday      bool
	thisWeek       bool
	lastWeek       bool
	thisMonth      bool
	lastMonth      bool
	days           int
	since          string
	until          string
	workspace      string
	habits         bool
	asJSON         bool
	verbose        bool
	pricingPath    string
	noPricing      bool
}

func main() {
	if err := newRootCmd().Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func newRootCmd() *cobra.Command {
	opts := &options{limit: 15, days: -1, recentRank: -1}

	root := &cobra.Command{
		Use:   "pi-session-analytics [target]",
		Short: "Pi Session Analytics & Audit Tool",
		Long: "Deterministic performance, cost economics, cache health and habit\n" +
			"analysis for Pi Coding Agent sessions (~/.pi/agent/sessions/**/*.jsonl).",
		Args:          cobra.MaximumNArgs(1),
		SilenceUsage:  true,
		SilenceErrors: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) > 0 {
				opts.path = args[0]
			}
			return run(opts)
		},
	}

	// --delve is an alias for --timeline.
	root.Flags().SetNormalizeFunc(func(_ *pflag.FlagSet, name string) pflag.NormalizedName {
		if name == "delve" {
			name = "timeline"
		}
		return pflag.NormalizedName(name)
	})

	f := root.Flags()
	f.BoolVarP(&opts.listSessions, "list", "l", false, "List recent sessions across workspaces in date order")
	f.BoolVar(&opts.listWorkspaces, "workspaces", false, "List all workspaces with session counts and latest activity")
	f.IntVar(&opts.limit, "limit", 15, "Max sessions to display with --list (0 for all)")
	f.BoolVar(&opts.errorsOnly, "errors-only", false, "Filter --list to sessions that encountered errors")
	f.IntVarP(&opts.recentRank, "recent", "n", -1, "Audit the N-th most recent session (1 = latest)")
	f.BoolVar(&opts.latest, "latest", false, "Audit the latest session in the current workspace")
	f.BoolVarP(&opts.globalSearch, "global", "g", false, "Audit the latest session across all workspaces globally")
	f.BoolVar(&opts.timeline, "timeline", false, "Force chronological turn timeline (Micro mode); alias --delve")
	f.BoolVar(&opts.today, "today", false, "Audit activity from today (midnight to now)")
	f.BoolVar(&opts.yesterday, "yesterday", false, "Audit activity from yesterday (full 24h)")
	f.BoolVar(&opts.thisWeek, "this-week", false, "Audit activity from this week (Monday to now)")
	f.BoolVar(&opts.lastWeek, "last-week", false, "Audit activity from last week (Monday to Sunday)")
	f.BoolVar(&opts.thisMonth, "this-month", false, "Audit activity from this calendar month")
	f.BoolVar(&opts.lastMonth, "last-month", false, "Audit activity from last calendar month")
	f.IntVar(&opts.days, "days", -1, "Audit activity from the last N days")
	f.StringVar(&opts.since, "since", "", "Start datetime: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
	f.StringVar(&opts.until, "until", "", "End datetime: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
	f.StringVar(&opts.workspace, "workspace", "", "Filter to specific workspace directory path")
	f.BoolVar(&opts.habits, "habits", false, "Audit all historical habits across all sessions")
	f.BoolVar(&opts.asJSON, "json", false, "Output raw JSON metrics")
	f.BoolVar(&opts.verbose, "verbose", false, "Include verbose turn-by-turn breakdown")
	f.StringVar(&opts.pricingPath, "pricing", "", "Cost override file (default: ~/.pi/agent/pricing.json, else the shipped pricing.json)")
	f.BoolVar(&opts.noPricing, "no-pricing", false, "Disable re-pricing; trust Pi's logged usage costs")

	return root
}

func run(opts *options) error {
	book, err := loadPricingBook(opts)
	if err != nil {
		return err
	}

	if opts.listWorkspaces {
		listWorkspaces(opts.asJSON)
		return nil
	}

	if opts.listSessions {
		since, until, err := listTimeBounds(opts)
		if err != nil {
			return err
		}
		listSessions(opts.limit, opts.workspace, opts.errorsOnly, since, until, opts.asJSON)
		return nil
	}

	if opts.habits {
		analyzeTimeWindowMacro(nil, nil, "All Time (Habits)", opts.asJSON, opts.workspace, book)
		return nil
	}

	if opts.hasTimeFilter() {
		since, until, label, isMicro, err := resolveTimeFilters(opts)
		if err != nil {
			return err
		}
		if isMicro {
			analyzeTimeWindowMicro(since, until, label, opts.asJSON, opts.workspace, opts.verbose, book)
		} else {
			analyzeTimeWindowMacro(since, until, label, opts.asJSON, opts.workspace, book)
		}
		return nil
	}

	return runSingleSession(opts, book)
}

func runSingleSession(opts *options, book *pricing.Book) error {
	cwd, _ := os.Getwd()
	targetPath := resolveSessionTarget(opts.path, cwd, opts.globalSearch, opts.recentRank)
	if targetPath == "" {
		return fmt.Errorf("no session file specified and could not resolve target session; provide a path, session ID prefix, rank number, or run with --list / --latest")
	}
	if info, err := os.Stat(targetPath); err != nil || info.IsDir() {
		return fmt.Errorf("file not found: %s", targetPath)
	}

	branch, totalRaw, meta := loadActiveBranch(targetPath)
	if len(branch) == 0 {
		return fmt.Errorf("no valid entries found in %s", targetPath)
	}

	metrics := analyzeSession(branch, totalRaw, meta, book)

	switch {
	case opts.asJSON:
		if opts.verbose || opts.timeline {
			turns := metrics.AssistantTurns
			metrics.Turns = &turns
		}
		printJSON(metrics)
	case opts.timeline:
		printSessionTimeline(metrics, opts.verbose)
	default:
		printSessionReport(metrics, opts.verbose)
	}
	return nil
}

func loadPricingBook(opts *options) (*pricing.Book, error) {
	if opts.noPricing {
		return nil, nil
	}
	book, err := pricing.Load(opts.pricingPath, pricing.ShippedPath())
	if err != nil {
		return nil, err
	}
	if book == nil && opts.pricingPath != "" {
		return nil, fmt.Errorf("pricing file not found: %s", opts.pricingPath)
	}
	return book, nil
}

func (o *options) hasTimeFilter() bool {
	return o.today || o.yesterday || o.thisWeek || o.lastWeek ||
		o.thisMonth || o.lastMonth || o.days > 0 || o.since != "" || o.until != ""
}

// listTimeBounds resolves the time presets relevant to --list.
func listTimeBounds(opts *options) (*time.Time, *time.Time, error) {
	var since, until *time.Time
	if opts.since != "" {
		t, err := parseUserDatetime(opts.since, false)
		if err != nil {
			return nil, nil, err
		}
		since = &t
	}
	if opts.until != "" {
		t, err := parseUserDatetime(opts.until, true)
		if err != nil {
			return nil, nil, err
		}
		until = &t
	}
	now := time.Now()
	switch {
	case opts.today:
		start := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
		since, until = &start, &now
	case opts.yesterday:
		y := now.AddDate(0, 0, -1)
		start := time.Date(y.Year(), y.Month(), y.Day(), 0, 0, 0, 0, time.Local)
		end := time.Date(y.Year(), y.Month(), y.Day(), 23, 59, 59, 999999999, time.Local)
		since, until = &start, &end
	case opts.days > 0:
		start := now.AddDate(0, 0, -opts.days)
		since, until = &start, &now
	}
	return since, until, nil
}

// resolveTimeFilters resolves CLI time presets into (since, until, label,
// isMicro).
func resolveTimeFilters(opts *options) (*time.Time, *time.Time, string, bool, error) {
	now := time.Now()
	isMicroForced := opts.timeline

	if opts.today {
		start := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
		return &start, &now, "Today", isMicroForced, nil
	}
	if opts.yesterday {
		y := now.AddDate(0, 0, -1)
		start := time.Date(y.Year(), y.Month(), y.Day(), 0, 0, 0, 0, time.Local)
		end := time.Date(y.Year(), y.Month(), y.Day(), 23, 59, 59, 999999999, time.Local)
		return &start, &end, "Yesterday", isMicroForced, nil
	}
	if opts.thisWeek {
		monday := now.AddDate(0, 0, -((int(now.Weekday()) + 6) % 7))
		start := time.Date(monday.Year(), monday.Month(), monday.Day(), 0, 0, 0, 0, time.Local)
		return &start, &now, "This Week", isMicroForced, nil
	}
	if opts.lastWeek {
		monday := now.AddDate(0, 0, -((int(now.Weekday())+6)%7)-7)
		sunday := monday.AddDate(0, 0, 6)
		start := time.Date(monday.Year(), monday.Month(), monday.Day(), 0, 0, 0, 0, time.Local)
		end := time.Date(sunday.Year(), sunday.Month(), sunday.Day(), 23, 59, 59, 999999999, time.Local)
		return &start, &end, "Last Week", isMicroForced, nil
	}
	if opts.thisMonth {
		start := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.Local)
		return &start, &now, "This Month", isMicroForced, nil
	}
	if opts.lastMonth {
		firstThis := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.Local)
		lastPrev := firstThis.AddDate(0, 0, -1)
		start := time.Date(lastPrev.Year(), lastPrev.Month(), 1, 0, 0, 0, 0, time.Local)
		end := time.Date(lastPrev.Year(), lastPrev.Month(), lastPrev.Day(), 23, 59, 59, 999999999, time.Local)
		return &start, &end, "Last Month", isMicroForced, nil
	}
	if opts.days > 0 {
		start := now.AddDate(0, 0, -opts.days)
		return &start, &now, fmt.Sprintf("Last %d Days", opts.days), isMicroForced, nil
	}

	var since, until *time.Time
	if opts.since != "" {
		t, err := parseUserDatetime(opts.since, false)
		if err != nil {
			return nil, nil, "", false, err
		}
		since = &t
	}
	if opts.until != "" {
		t, err := parseUserDatetime(opts.until, true)
		if err != nil {
			return nil, nil, "", false, err
		}
		until = &t
	}
	if since != nil && until != nil && until.Before(*since) {
		return nil, nil, "", false, fmt.Errorf("--until (%s) cannot be earlier than --since (%s)", until, since)
	}

	label := "Custom Time Window"
	switch {
	case since != nil && until != nil:
		label = fmt.Sprintf("%s to %s", since.Format("2006-01-02 15:04"), until.Format("2006-01-02 15:04"))
	case since != nil:
		label = "Since " + since.Format("2006-01-02 15:04")
	case until != nil:
		label = "Until " + until.Format("2006-01-02 15:04")
	}
	if since == nil && until == nil {
		return nil, nil, "", false, fmt.Errorf("time-window query requires a time boundary (e.g. --today, --yesterday, --since 'YYYY-MM-DD')")
	}

	isMicro := isMicroForced || (since != nil && until != nil && until.Sub(*since).Seconds() <= 7200)
	return since, until, label, isMicro, nil
}
