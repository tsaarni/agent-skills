// Package pricing implements the time- and date-aware cost model used by the
// Pi session analytics tool.
//
// Pi computes each turn's cost client-side from one flat rate set per model and
// logs the raw token counts. This package re-prices those tokens against a
// small, explicit rule language so peak/off-peak, holiday and promotional rates
// can be audited deterministically and even retroactively.
//
// Design:
//
//   - Cost only. It never touches model identity, capabilities or endpoints.
//   - Pure resolution. Book.Resolve(provider, model, at) is a function of the
//     model key and an instant; the same input always yields the same result.
//   - Absolute rates. A rule states input/output/cacheRead/cacheWrite in USD
//     per 1M tokens. Rates are never derived as a fraction of another window,
//     because peak and off-peak need not scale uniformly.
//   - One matcher language. A rule matches when it satisfies the optional,
//     ANDed constraints weekly, dates and excludeDates. No when block means
//     "always". Rules are ordered and the first match wins.
//   - Fail loudly. An invalid file returns an error instead of silently
//     producing wrong money.
package pricing

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "time/tzdata" // embed the IANA database so timezone matching is portable
)

// RateKeys are the four absolute rate components every rule must state.
var RateKeys = []string{"input", "output", "cacheRead", "cacheWrite"}

var (
	topKeys   = keySet("version", "timezone", "currency", "source", "calendars", "overrides")
	specKeys  = keySet("rules", "source", "retrievedAt")
	ruleKeys  = keySet("id", "when", "rates")
	whenKeys  = keySet("weekly", "dates", "excludeDates")
	weekKeys  = keySet("days", "start", "end")
	rangeKeys = keySet("from", "to")
)

var weekdayNames = map[string]int{
	"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

func keySet(keys ...string) map[string]bool {
	m := make(map[string]bool, len(keys))
	for _, k := range keys {
		m[k] = true
	}
	return m
}

func isComment(key string) bool { return strings.HasPrefix(key, "_") }

// Rates holds absolute USD-per-1M-token prices for one window.
type Rates struct {
	Input      float64
	Output     float64
	CacheRead  float64
	CacheWrite float64
}

type weeklyWindow struct {
	days     map[int]bool // nil means "every day"
	hasStart bool
	start    int // minutes since midnight
	end      int
}

type dateRange struct{ low, high time.Time }

type whenSpec struct {
	hasWeekly    bool
	weekly       []weeklyWindow
	hasDates     bool
	dates        []dateRange
	hasExclude   bool
	excludeDates []dateRange
}

func (w whenSpec) empty() bool {
	return !w.hasWeekly && !w.hasDates && !w.hasExclude
}

type compiledRule struct {
	id    string
	when  whenSpec
	rates Rates
}

// Book is an ordered set of pricing rules for a set of provider/model keys.
type Book struct {
	Path         string
	Source       string
	Currency     string
	TimezoneName string

	loc   *time.Location
	specs map[string]map[string][]compiledRule
}

// --- validation helpers -----------------------------------------------------

func errf(format string, a ...any) error { return fmt.Errorf(format, a...) }

func checkKeys(obj map[string]any, allowed map[string]bool, ctx string) error {
	var unknown []string
	for k := range obj {
		if !allowed[k] && !isComment(k) {
			unknown = append(unknown, k)
		}
	}
	if len(unknown) == 0 {
		return nil
	}
	sort.Strings(unknown)
	allowedList := make([]string, 0, len(allowed))
	for k := range allowed {
		allowedList = append(allowedList, k)
	}
	sort.Strings(allowedList)
	return errf("%s: unknown key(s) %v; allowed %v", ctx, unknown, allowedList)
}

func asMap(v any, ctx string) (map[string]any, error) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, errf("%s: expected an object", ctx)
	}
	return m, nil
}

func asNumber(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}

func normDays(v any, ctx string) (map[int]bool, error) {
	if v == nil {
		return nil, nil
	}
	list, ok := v.([]any)
	if !ok {
		return nil, errf("%s.days: expected a list", ctx)
	}
	out := map[int]bool{}
	for _, d := range list {
		key := strings.ToLower(strings.TrimSpace(fmt.Sprint(d)))
		if wd, ok := weekdayNames[key]; ok {
			out[wd] = true
			continue
		}
		if n, err := strconv.Atoi(key); err == nil && n >= 0 && n <= 6 {
			out[n] = true
			continue
		}
		return nil, errf("%s.days: unknown weekday %v", ctx, d)
	}
	if len(out) == 0 {
		return nil, nil
	}
	return out, nil
}

func parseHM(v any, ctx string) (int, error) {
	s := strings.TrimSpace(fmt.Sprint(v))
	hourStr, minStr := s, ""
	if i := strings.Index(s, ":"); i >= 0 {
		hourStr, minStr = s[:i], s[i+1:]
	}
	hour, err := strconv.Atoi(hourStr)
	if err != nil {
		return 0, errf("%s: invalid time %q; expected 'HH:MM'", ctx, s)
	}
	minute := 0
	if minStr != "" {
		minute, err = strconv.Atoi(minStr)
		if err != nil {
			return 0, errf("%s: invalid time %q; expected 'HH:MM'", ctx, s)
		}
	}
	minutes := hour*60 + minute
	if minutes < 0 || minutes >= 24*60 {
		return 0, errf("%s: time %q out of range", ctx, s)
	}
	return minutes, nil
}

func compileWeekly(v any, ctx string) (weeklyWindow, error) {
	obj, err := asMap(v, ctx)
	if err != nil {
		return weeklyWindow{}, err
	}
	if err := checkKeys(obj, weekKeys, ctx); err != nil {
		return weeklyWindow{}, err
	}
	days, err := normDays(obj["days"], ctx)
	if err != nil {
		return weeklyWindow{}, err
	}
	_, hasStart := obj["start"]
	_, hasEnd := obj["end"]
	if hasStart != hasEnd {
		return weeklyWindow{}, errf("%s: 'start' and 'end' must appear together", ctx)
	}
	win := weeklyWindow{days: days, hasStart: hasStart}
	if hasStart {
		win.start, err = parseHM(obj["start"], ctx+".start")
		if err != nil {
			return weeklyWindow{}, err
		}
		win.end, err = parseHM(obj["end"], ctx+".end")
		if err != nil {
			return weeklyWindow{}, err
		}
	}
	if days == nil && !hasStart {
		return weeklyWindow{}, errf("%s: needs 'days' and/or a 'start'/'end' window", ctx)
	}
	return win, nil
}

func parseDate(s string) (time.Time, error) { return time.Parse("2006-01-02", s) }

var (
	dateMin = time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC)
	dateMax = time.Date(9999, 12, 31, 0, 0, 0, 0, time.UTC)
)

func parseDateEntry(v any, calendars map[string][]time.Time, ctx string) ([]dateRange, error) {
	switch e := v.(type) {
	case string:
		if strings.HasPrefix(e, "$") {
			name := e[1:]
			days, ok := calendars[name]
			if !ok {
				return nil, errf("%s: unknown calendar $%s", ctx, name)
			}
			out := make([]dateRange, 0, len(days))
			for _, d := range days {
				out = append(out, dateRange{d, d})
			}
			return out, nil
		}
		day, err := parseDate(e)
		if err != nil {
			return nil, errf("%s: invalid date %q; expected YYYY-MM-DD", ctx, e)
		}
		return []dateRange{{day, day}}, nil
	case map[string]any:
		if err := checkKeys(e, rangeKeys, ctx); err != nil {
			return nil, err
		}
		fromS, _ := e["from"].(string)
		toS, _ := e["to"].(string)
		if fromS == "" && toS == "" {
			return nil, errf("%s: range needs 'from' and/or 'to'", ctx)
		}
		low, high := dateMin, dateMax
		if fromS != "" {
			d, err := parseDate(fromS)
			if err != nil {
				return nil, errf("%s: invalid range %v", ctx, e)
			}
			low = d
		}
		if toS != "" {
			d, err := parseDate(toS)
			if err != nil {
				return nil, errf("%s: invalid range %v", ctx, e)
			}
			high = d
		}
		if low.After(high) {
			return nil, errf("%s: range start %s is after end %s", ctx, low.Format("2006-01-02"), high.Format("2006-01-02"))
		}
		return []dateRange{{low, high}}, nil
	}
	return nil, errf("%s: expected a date, a {from,to} range, or $calendar", ctx)
}

func compileDates(spec any, calendars map[string][]time.Time, ctx string) ([]dateRange, error) {
	items := []any{spec}
	if list, ok := spec.([]any); ok {
		items = list
	}
	var ranges []dateRange
	for _, item := range items {
		r, err := parseDateEntry(item, calendars, ctx)
		if err != nil {
			return nil, err
		}
		ranges = append(ranges, r...)
	}
	return ranges, nil
}

func compileWhen(v any, calendars map[string][]time.Time, ctx string) (whenSpec, error) {
	if v == nil {
		return whenSpec{}, nil
	}
	obj, err := asMap(v, ctx+".when")
	if err != nil {
		return whenSpec{}, err
	}
	if err := checkKeys(obj, whenKeys, ctx); err != nil {
		return whenSpec{}, err
	}
	var out whenSpec
	if wv, ok := obj["weekly"]; ok {
		windows, ok := wv.([]any)
		if !ok {
			return whenSpec{}, errf("%s.when.weekly: expected a list", ctx)
		}
		out.hasWeekly = true
		for i, w := range windows {
			win, err := compileWeekly(w, fmt.Sprintf("%s.when.weekly[%d]", ctx, i))
			if err != nil {
				return whenSpec{}, err
			}
			out.weekly = append(out.weekly, win)
		}
	}
	for _, key := range []string{"dates", "excludeDates"} {
		val, ok := obj[key]
		if !ok {
			continue
		}
		ranges, err := compileDates(val, calendars, ctx+".when."+key)
		if err != nil {
			return whenSpec{}, err
		}
		if key == "dates" {
			out.hasDates, out.dates = true, ranges
		} else {
			out.hasExclude, out.excludeDates = true, ranges
		}
	}
	return out, nil
}

// --- construction -----------------------------------------------------------

// NewBook validates data and compiles it into a ready-to-resolve Book.
func NewBook(data map[string]any, path string) (*Book, error) {
	if err := checkKeys(data, topKeys, "pricing"); err != nil {
		return nil, err
	}

	calendars := map[string][]time.Time{}
	if raw, ok := data["calendars"]; ok {
		m, err := asMap(raw, "calendars")
		if err != nil {
			return nil, err
		}
		for name, v := range m {
			if isComment(name) {
				continue
			}
			list, ok := v.([]any)
			if !ok {
				return nil, errf("calendars.%s: expected a list of YYYY-MM-DD strings", name)
			}
			days := make([]time.Time, 0, len(list))
			for _, d := range list {
				s, ok := d.(string)
				if !ok {
					return nil, errf("calendars.%s: expected a list of YYYY-MM-DD strings", name)
				}
				t, err := parseDate(s)
				if err != nil {
					return nil, errf("calendars.%s: invalid date", name)
				}
				days = append(days, t)
			}
			calendars[name] = days
		}
	}

	tzName := "UTC"
	if v, ok := data["timezone"].(string); ok && v != "" {
		tzName = v
	}
	var loc *time.Location
	if tzName == "UTC" {
		loc = time.UTC
	} else {
		var err error
		loc, err = time.LoadLocation(tzName)
		if err != nil {
			return nil, errf("unknown timezone %q", tzName)
		}
	}

	book := &Book{
		Path:         path,
		Source:       stringOr(data["source"], ""),
		Currency:     stringOr(data["currency"], "USD"),
		TimezoneName: tzName,
		loc:          loc,
		specs:        map[string]map[string][]compiledRule{},
	}

	overrides := map[string]any{}
	if raw, ok := data["overrides"]; ok {
		m, err := asMap(raw, "overrides")
		if err != nil {
			return nil, err
		}
		overrides = m
	}
	for provider, modelsV := range overrides {
		if isComment(provider) {
			continue
		}
		models, err := asMap(modelsV, "overrides."+provider)
		if err != nil {
			return nil, errf("overrides.%s: expected an object of models", provider)
		}
		book.specs[provider] = map[string][]compiledRule{}
		for model, specV := range models {
			if isComment(model) {
				continue
			}
			ctx := fmt.Sprintf("overrides.%s.%s", provider, model)
			spec, err := asMap(specV, ctx)
			if err != nil {
				return nil, errf("%s: expected an object", ctx)
			}
			if err := checkKeys(spec, specKeys, ctx); err != nil {
				return nil, err
			}
			rulesV, ok := spec["rules"].([]any)
			if !ok || len(rulesV) == 0 {
				return nil, errf("%s.rules: expected a non-empty list", ctx)
			}
			seen := map[string]bool{}
			var compiled []compiledRule
			for i, ruleV := range rulesV {
				rctx := fmt.Sprintf("%s.rules[%d]", ctx, i)
				rule, err := asMap(ruleV, rctx)
				if err != nil {
					return nil, errf("%s: expected an object", rctx)
				}
				if err := checkKeys(rule, ruleKeys, rctx); err != nil {
					return nil, err
				}
				id, ok := rule["id"].(string)
				if !ok || id == "" {
					return nil, errf("%s.id: required non-empty string", rctx)
				}
				if seen[id] {
					return nil, errf("%s.id: duplicate rule id %q", rctx, id)
				}
				seen[id] = true

				ratesObj, err := asMap(rule["rates"], rctx+".rates")
				if err != nil {
					return nil, errf("%s.rates: required object", rctx)
				}
				if err := checkKeys(ratesObj, keySet(RateKeys...), rctx+".rates"); err != nil {
					return nil, err
				}
				var missing []string
				for _, k := range RateKeys {
					if _, ok := ratesObj[k]; !ok {
						missing = append(missing, k)
					}
				}
				if len(missing) > 0 {
					return nil, errf("%s.rates: must state all of %v; missing %v", rctx, RateKeys, missing)
				}
				var rates Rates
				fields := map[string]*float64{
					"input": &rates.Input, "output": &rates.Output,
					"cacheRead": &rates.CacheRead, "cacheWrite": &rates.CacheWrite,
				}
				for _, k := range RateKeys {
					n, ok := asNumber(ratesObj[k])
					if !ok || n < 0 {
						return nil, errf("%s.rates.%s: expected a non-negative number", rctx, k)
					}
					*fields[k] = n
				}

				when, err := compileWhen(rule["when"], calendars, rctx)
				if err != nil {
					return nil, err
				}
				compiled = append(compiled, compiledRule{id: id, when: when, rates: rates})
			}
			book.specs[provider][model] = compiled
		}
	}
	return book, nil
}

func stringOr(v any, def string) string {
	if s, ok := v.(string); ok {
		return s
	}
	return def
}

// --- resolution -------------------------------------------------------------

func (b *Book) rulesFor(provider, model string) []compiledRule {
	for _, providerKey := range []string{provider, "*"} {
		bucket := b.specs[providerKey]
		if bucket == nil {
			continue
		}
		for _, modelKey := range []string{model, "*"} {
			if rules, ok := bucket[modelKey]; ok {
				return rules
			}
		}
	}
	return nil
}

// Resolve returns the first matching rule's rates and id. ok is false when no
// rule matches.
func (b *Book) Resolve(provider, model string, at time.Time) (Rates, string, bool) {
	rules := b.rulesFor(provider, model)
	if len(rules) == 0 {
		return Rates{}, "", false
	}
	if at.IsZero() {
		at = time.Now()
	}
	local := at.In(b.loc)
	for _, rule := range rules {
		if rule.when.matches(local) {
			return rule.rates, rule.id, true
		}
	}
	return Rates{}, "", false
}

func (w whenSpec) matches(local time.Time) bool {
	if w.empty() {
		return true
	}
	if w.hasWeekly {
		any := false
		for _, win := range w.weekly {
			if win.matches(local) {
				any = true
				break
			}
		}
		if !any {
			return false
		}
	}
	day := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	if w.hasDates && !inRanges(day, w.dates) {
		return false
	}
	if w.hasExclude && inRanges(day, w.excludeDates) {
		return false
	}
	return true
}

func inRanges(day time.Time, ranges []dateRange) bool {
	for _, r := range ranges {
		if !day.Before(r.low) && !day.After(r.high) {
			return true
		}
	}
	return false
}

func (win weeklyWindow) matches(local time.Time) bool {
	// Go: Sunday=0..Saturday=6. Python: Monday=0..Sunday=6.
	pyWeekday := (int(local.Weekday()) + 6) % 7
	onDay := win.days == nil || win.days[pyWeekday]
	if !win.hasStart {
		return onDay
	}
	now := local.Hour()*60 + local.Minute()
	if win.start == win.end {
		return onDay
	}
	if win.start < win.end {
		return onDay && now >= win.start && now < win.end
	}
	// Window crosses midnight: the start day owns [start, 24:00) and the next
	// day owns [00:00, end).
	prevDay := (pyWeekday + 6) % 7
	return (onDay && now >= win.start) || ((win.days == nil || win.days[prevDay]) && now < win.end)
}

// --- loading ----------------------------------------------------------------

// ShippedPath returns pricing.json next to the source tree (or executable).
func ShippedPath() string {
	if _, file, _, ok := runtime.Caller(0); ok {
		return filepath.Join(filepath.Dir(filepath.Dir(file)), "pricing.json")
	}
	if exe, err := os.Executable(); err == nil {
		return filepath.Join(filepath.Dir(exe), "pricing.json")
	}
	return "pricing.json"
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(path, "~/"))
		}
	}
	return path
}

// ResolvePricingPath picks the pricing file: explicit path, then the user file
// in the agent directory, then the shipped file.
func ResolvePricingPath(explicit, shipped string) string {
	if explicit != "" {
		return expandHome(explicit)
	}
	user := expandHome("~/.pi/agent/pricing.json")
	if fileExists(user) {
		return user
	}
	return shipped
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// Load reads and validates a pricing book. It returns (nil, nil) when no file
// exists. A file that exists but is malformed returns an error.
func Load(explicit, shipped string) (*Book, error) {
	resolved := ResolvePricingPath(explicit, shipped)
	if !fileExists(resolved) {
		return nil, nil
	}
	raw, err := os.ReadFile(resolved)
	if err != nil {
		return nil, errf("%s: %v", resolved, err)
	}
	var data any
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if err := dec.Decode(&data); err != nil {
		return nil, errf("%s: invalid JSON: %v", resolved, err)
	}
	m, ok := normalizeNumbers(data).(map[string]any)
	if !ok {
		return nil, errf("top level must be an object")
	}
	book, err := NewBook(m, resolved)
	if err != nil {
		return nil, err
	}
	return book, nil
}

// normalizeNumbers converts json.Number values into float64 so downstream type
// switches are uniform.
func normalizeNumbers(v any) any {
	switch n := v.(type) {
	case json.Number:
		f, _ := n.Float64()
		return f
	case map[string]any:
		for k, val := range n {
			n[k] = normalizeNumbers(val)
		}
		return n
	case []any:
		for i, val := range n {
			n[i] = normalizeNumbers(val)
		}
		return n
	}
	return v
}
