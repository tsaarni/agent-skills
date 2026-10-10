package pricing

import (
	"testing"
	"time"
)

func mustBook(t *testing.T, data map[string]any) *Book {
	t.Helper()
	book, err := NewBook(data, "test")
	if err != nil {
		t.Fatalf("NewBook: %v", err)
	}
	return book
}

func ruleAt(t *testing.T, book *Book, provider, model, iso string) string {
	t.Helper()
	at, err := time.Parse(time.RFC3339, iso)
	if err != nil {
		t.Fatalf("parse %q: %v", iso, err)
	}
	_, id, ok := book.Resolve(provider, model, at)
	if !ok {
		return ""
	}
	return id
}

func TestPeakOffPeak(t *testing.T) {
	data := map[string]any{
		"timezone":  "UTC",
		"calendars": map[string]any{"cn-holidays": []any{"2026-10-01"}},
		"overrides": map[string]any{
			"deepseek": map[string]any{
				"deepseek-flash": map[string]any{
					"rules": []any{
						map[string]any{
							"id": "peak",
							"when": map[string]any{
								"weekly": []any{
									map[string]any{"days": []any{"Mon", "Tue", "Wed", "Thu", "Fri"}, "start": "01:00", "end": "04:00"},
									map[string]any{"days": []any{"Mon", "Tue", "Wed", "Thu", "Fri"}, "start": "06:00", "end": "10:00"},
								},
								"excludeDates": []any{"$cn-holidays"},
							},
							"rates": map[string]any{"input": 0.30, "output": 1.20, "cacheRead": 0.006, "cacheWrite": 0.30},
						},
						map[string]any{
							"id":    "offpeak",
							"rates": map[string]any{"input": 0.15, "output": 0.60, "cacheRead": 0.003, "cacheWrite": 0.15},
						},
					},
				},
			},
		},
	}
	book := mustBook(t, data)
	cases := map[string]string{
		"2026-10-09T02:00:00Z": "peak",    // Friday inside peak
		"2026-10-09T12:00:00Z": "offpeak", // Friday outside peak
		"2026-10-10T02:00:00Z": "offpeak", // Saturday
		"2026-10-01T02:00:00Z": "offpeak", // CN holiday
	}
	for iso, want := range cases {
		if got := ruleAt(t, book, "deepseek", "deepseek-flash", iso); got != want {
			t.Errorf("%s: got %q want %q", iso, got, want)
		}
	}
}

func TestMidnightWrap(t *testing.T) {
	rules := []any{
		map[string]any{"id": "w", "when": map[string]any{
			"weekly": []any{map[string]any{"days": []any{"Sat"}, "start": "22:00", "end": "04:00"}}},
			"rates": map[string]any{"input": 1, "output": 1, "cacheRead": 1, "cacheWrite": 1}},
		map[string]any{"id": "o", "rates": map[string]any{"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
	}
	book := mustBook(t, map[string]any{
		"overrides": map[string]any{"p": map[string]any{"m": map[string]any{"rules": rules}}},
	})
	cases := map[string]string{
		"2026-10-10T23:30:00Z": "w",
		"2026-10-11T02:00:00Z": "w",
		"2026-10-11T05:00:00Z": "o",
	}
	for iso, want := range cases {
		if got := ruleAt(t, book, "p", "m", iso); got != want {
			t.Errorf("%s: got %q want %q", iso, got, want)
		}
	}
}

func TestIncompleteRatesRejected(t *testing.T) {
	_, err := NewBook(map[string]any{
		"overrides": map[string]any{"p": map[string]any{"m": map[string]any{"rules": []any{
			map[string]any{"id": "x", "rates": map[string]any{"input": 1, "output": 1}},
		}}}},
	}, "test")
	if err == nil {
		t.Fatal("expected error for incomplete rate set")
	}
}

func TestComputeCost(t *testing.T) {
	cost := ComputeCost(Rates{Input: 0.15, Output: 0.6, CacheRead: 0.003, CacheWrite: 0.15},
		100_000, 1_000_000, 0, 10_000, 0)
	want := 0.015 + 0.003 + 0.006
	if diff := cost.Total - want; diff > 1e-9 || diff < -1e-9 {
		t.Fatalf("total = %v, want %v", cost.Total, want)
	}
}
