package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// Window is a half-open time range [Since, Until). A zero bound is unbounded.
type Window struct {
	Since time.Time
	Until time.Time
}

func (w Window) IsZero() bool { return w.Since.IsZero() && w.Until.IsZero() }

func (w Window) Contains(t time.Time) bool {
	if !w.Since.IsZero() && t.Before(w.Since) {
		return false
	}
	if !w.Until.IsZero() && !t.Before(w.Until) {
		return false
	}
	return true
}

// timeFlags holds the shared time filter options.
type timeFlags struct {
	Today, Yesterday     bool
	ThisWeek, LastWeek   bool
	ThisMonth, LastMonth bool
	Days                 int
	Since, Until         string
}

func (tf *timeFlags) register(cmd *cobra.Command) {
	f := cmd.Flags()
	f.BoolVar(&tf.Today, "today", false, "today")
	f.BoolVar(&tf.Yesterday, "yesterday", false, "yesterday")
	f.BoolVar(&tf.ThisWeek, "this-week", false, "this week (Monday to Sunday)")
	f.BoolVar(&tf.LastWeek, "last-week", false, "last week (Monday to Sunday)")
	f.BoolVar(&tf.ThisMonth, "this-month", false, "this calendar month")
	f.BoolVar(&tf.LastMonth, "last-month", false, "last calendar month")
	f.IntVar(&tf.Days, "days", 0, "trailing N days, including today")
	f.StringVar(&tf.Since, "since", "", "start, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (local time)")
	f.StringVar(&tf.Until, "until", "", "end (inclusive for a date), 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
}

// resolve turns the flags into a window. def is used when no flag is given.
func (tf *timeFlags) resolve(now time.Time, def Window) (Window, error) {
	presets := 0
	for _, b := range []bool{tf.Today, tf.Yesterday, tf.ThisWeek, tf.LastWeek, tf.ThisMonth, tf.LastMonth, tf.Days > 0} {
		if b {
			presets++
		}
	}
	custom := tf.Since != "" || tf.Until != ""
	if tf.Days < 0 {
		return Window{}, fmt.Errorf("--days must be positive")
	}
	if presets > 1 {
		return Window{}, fmt.Errorf("only one of --today, --yesterday, --this-week, --last-week, --this-month, --last-month, --days may be given")
	}
	if presets > 0 && custom {
		return Window{}, fmt.Errorf("--since/--until cannot be combined with the other time options")
	}
	if presets == 0 && !custom {
		return def, nil
	}

	today := startOfDay(now)
	switch {
	case tf.Today:
		return Window{today, today.AddDate(0, 0, 1)}, nil
	case tf.Yesterday:
		return Window{today.AddDate(0, 0, -1), today}, nil
	case tf.ThisWeek:
		s := startOfWeek(today)
		return Window{s, s.AddDate(0, 0, 7)}, nil
	case tf.LastWeek:
		s := startOfWeek(today).AddDate(0, 0, -7)
		return Window{s, s.AddDate(0, 0, 7)}, nil
	case tf.ThisMonth:
		s := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, now.Location())
		return Window{s, s.AddDate(0, 1, 0)}, nil
	case tf.LastMonth:
		s := time.Date(now.Year(), now.Month()-1, 1, 0, 0, 0, 0, now.Location())
		return Window{s, s.AddDate(0, 1, 0)}, nil
	case tf.Days > 0:
		return Window{today.AddDate(0, 0, -(tf.Days - 1)), today.AddDate(0, 0, 1)}, nil
	}

	var w Window
	var err error
	if tf.Since != "" {
		if w.Since, _, err = parseUserTime(tf.Since, now.Location()); err != nil {
			return Window{}, err
		}
	}
	if tf.Until != "" {
		t, dateOnly, err := parseUserTime(tf.Until, now.Location())
		if err != nil {
			return Window{}, err
		}
		if dateOnly {
			t = t.AddDate(0, 0, 1)
		}
		w.Until = t
	}
	if !w.Since.IsZero() && !w.Until.IsZero() && !w.Until.After(w.Since) {
		return Window{}, fmt.Errorf("--until must be after --since")
	}
	return w, nil
}

// trailingWeek is the default window of the commands that have no natural
// "today": the last 7 days, including today.
func trailingWeek(now time.Time) Window {
	today := startOfDay(now)
	return Window{today.AddDate(0, 0, -6), today.AddDate(0, 0, 1)}
}

func startOfDay(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, t.Location())
}

// startOfWeek returns Monday 00:00 of the week containing the (already
// day-aligned) time t.
func startOfWeek(t time.Time) time.Time {
	offset := (int(t.Weekday()) + 6) % 7
	return t.AddDate(0, 0, -offset)
}

func parseUserTime(s string, loc *time.Location) (t time.Time, dateOnly bool, err error) {
	s = strings.TrimSpace(s)
	for _, layout := range []string{"2006-01-02 15:04:05", "2006-01-02 15:04", "2006-01-02T15:04", "2006-01-02"} {
		if t, err := time.ParseInLocation(layout, s, loc); err == nil {
			return t, layout == "2006-01-02", nil
		}
	}
	return time.Time{}, false, fmt.Errorf("invalid date/time %q, expected 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'", s)
}
