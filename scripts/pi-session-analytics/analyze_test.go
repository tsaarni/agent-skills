package main

import (
	"testing"
	"time"
)

// naiveRolling is the O(n²) reference implementation used to validate the
// sliding-window version.
func naiveRolling(turns []*Turn) {
	for i, t := range turns {
		if t.TS == nil {
			continue
		}
		start := t.TS.Add(-60 * time.Second)
		var sum int64
		count := 0
		for j := 0; j < i; j++ {
			p := turns[j]
			if p.TS != nil && !p.TS.Before(start) {
				sum += int64(p.TotalCtx)
				count++
			}
		}
		t.RollingTPM = sum
		t.RollingRPM = count
	}
}

func TestComputeRollingMatchesNaive(t *testing.T) {
	base := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	offsets := []int{0, 5, 12, 30, 55, 61, 90, 119, 121, 180, 240, 299, 301, 360}
	turns := make([]*Turn, 0, len(offsets))
	for i, off := range offsets {
		ts := base.Add(time.Duration(off) * time.Second)
		turns = append(turns, &Turn{Turn: i + 1, TS: &ts, TotalCtx: (i + 1) * 1000})
	}

	naive := make([]*Turn, len(turns))
	for i, turn := range turns {
		cp := *turn
		ts := *turn.TS
		cp.TS = &ts
		naive[i] = &cp
	}
	naiveRolling(naive)
	computeRolling(turns)

	for i := range turns {
		if turns[i].RollingTPM != naive[i].RollingTPM || turns[i].RollingRPM != naive[i].RollingRPM {
			t.Errorf("turn %d: rolling=(%d,%d) naive=(%d,%d)", i+1,
				turns[i].RollingTPM, turns[i].RollingRPM, naive[i].RollingTPM, naive[i].RollingRPM)
		}
	}
}

func TestContentLen(t *testing.T) {
	cases := map[string]int{
		`"hello"`: 5,
		`[{"type":"text","text":"abc"},{"type":"text","text":"de"}]`: 5,
		`null`: 0,
	}
	for raw, want := range cases {
		if got := contentLen([]byte(raw)); got != want {
			t.Errorf("contentLen(%s) = %d, want %d", raw, got, want)
		}
	}
}

func TestErrorSnippet(t *testing.T) {
	cases := map[string]string{
		"429 Too Many Requests": "429 Too Many Requests (Rate Limit)",
		"503 unavailable":       "503 Service Unavailable",
		"500 boom":              "500 Internal Server Error",
		"401 nope":              "401 Unauthorized",
	}
	for raw, want := range cases {
		if got := errorSnippet(raw); got != want {
			t.Errorf("errorSnippet(%q) = %q, want %q", raw, got, want)
		}
	}
}

func TestResolveTimeFiltersMicro(t *testing.T) {
	cases := []struct {
		since    string
		until    string
		timeline bool
		want     bool
	}{
		{"2026-01-01 10:00", "2026-01-01 11:30", false, true},
		{"2026-01-01 10:00", "2026-01-01 13:00", false, false},
		{"2026-01-01 10:00", "2026-01-01 13:00", true, true},
	}
	for _, tc := range cases {
		opts := &options{since: tc.since, until: tc.until, timeline: tc.timeline, days: -1, recentRank: -1}
		_, _, _, isMicro, err := resolveTimeFilters(opts)
		if err != nil {
			t.Fatalf("resolveTimeFilters(%s..%s): %v", tc.since, tc.until, err)
		}
		if isMicro != tc.want {
			t.Errorf("window %s..%s: isMicro=%v, want %v", tc.since, tc.until, isMicro, tc.want)
		}
	}
}
