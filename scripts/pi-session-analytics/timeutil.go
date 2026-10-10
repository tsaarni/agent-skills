package main

import (
	"bytes"
	"encoding/json"
	"fmt"

	"strings"
	"time"
)

// parseTimestamp parses an ISO-8601 string or millisecond integer into a local
// datetime, mirroring the reference implementation.
func parseTimestamp(raw json.RawMessage) *time.Time {
	if len(raw) == 0 {
		return nil
	}
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return nil
	}
	switch trimmed[0] {
	case '"':
		var s string
		if err := json.Unmarshal(trimmed, &s); err == nil {
			return parseTimestampString(s)
		}
	case 'n':
		return nil
	default:
		var n float64
		if err := json.Unmarshal(trimmed, &n); err == nil {
			t := time.UnixMilli(int64(n)).Local()
			return &t
		}
	}
	return nil
}

func parseTimestampString(s string) *time.Time {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	// Normalise a UTC "Z" suffix before parsing.
	clean := strings.ReplaceAll(s, "Z", "+00:00")
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if t, err := time.Parse(layout, clean); err == nil {
			local := t.Local()
			return &local
		}
	}
	// Naive timestamps are interpreted as local time.
	for _, layout := range []string{
		"2006-01-02T15:04:05.999999999",
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05.999999999",
		"2006-01-02 15:04:05",
	} {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return &t
		}
	}
	return nil
}

// parseUserDatetime parses a CLI date/time string in the system local timezone.
func parseUserDatetime(dtStr string, isEnd bool) (time.Time, error) {
	dtStr = strings.TrimSpace(dtStr)
	layouts := []string{"2006-01-02 15:04:05", "2006-01-02 15:04", "2006-01-02"}
	for _, layout := range layouts {
		parsed, err := time.ParseInLocation(layout, dtStr, time.Local)
		if err != nil {
			continue
		}
		if layout == "2006-01-02" {
			if isEnd {
				y, m, d := parsed.Date()
				parsed = time.Date(y, m, d, 23, 59, 59, 999999999, time.Local)
			}
		}
		return parsed, nil
	}
	return time.Time{}, fmt.Errorf("invalid date/time format: %q. Expected 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'", dtStr)
}

// formatDuration renders seconds into a human-readable duration.
func formatDuration(seconds float64) string {
	total := int(seconds)
	m, s := total/60, total%60
	h, m := m/60, m%60
	switch {
	case h > 0:
		return fmt.Sprintf("%dh %02dm %02ds", h, m, s)
	case m > 0:
		return fmt.Sprintf("%dm %02ds", m, s)
	default:
		return fmt.Sprintf("%ds", s)
	}
}
