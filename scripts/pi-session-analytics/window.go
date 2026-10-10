package main

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"pi-session-analytics/pricing"
)

// --- micro mode --------------------------------------------------------------

type microTurn struct {
	ts          *time.Time
	sessionID   string
	cwd         string
	model       string
	provider    string
	prevModel   string
	inp         int
	cr          int
	cacheWrite  int
	out         int
	reas        int
	totalCtx    int
	cost        float64
	piCost      Cost
	pricingRule *string
	rates       *pricing.Rates
	costOutput  float64
	tools       []string
	errMsg      *string

	turnIdx    int
	rollingTPM int64
	rollingRPM int
}

type microAPIError struct {
	Turn       int        `json:"turn"`
	TS         *time.Time `json:"ts"`
	RollingTPM int64      `json:"rolling_tpm"`
	RollingRPM int        `json:"rolling_rpm"`
	Snippet    string     `json:"snippet"`
}

type microOutput struct {
	Label            string          `json:"label"`
	Since            *time.Time      `json:"since"`
	Until            *time.Time      `json:"until"`
	TurnsCount       int             `json:"turns_count"`
	PromptsCount     int             `json:"prompts_count"`
	SessionsCount    int             `json:"sessions_count"`
	TotalCost        float64         `json:"total_cost"`
	CostWithoutCache float64         `json:"cost_without_cache"`
	Savings          float64         `json:"savings"`
	SavingsPct       float64         `json:"savings_pct"`
	PeakTPM60s       int64           `json:"peak_tpm_60s"`
	PeakRPM60s       int             `json:"peak_rpm_60s"`
	Pricing          PricingMeta     `json:"pricing"`
	CostByRule       OrderedFloats   `json:"cost_by_rule"`
	APIErrors        []microAPIError `json:"api_errors"`
}

// analyzeTimeWindowMicro delves into a specific short time window or incident
// timeline across sessions.
func analyzeTimeWindowMicro(since, until *time.Time, label string, asJSON bool, workspace string, verbose bool, book *pricing.Book) {
	if since == nil && until == nil {
		fmt.Println("Error: Time-window delve requires a bounded time range (e.g. --today, --since 'YYYY-MM-DD').")
		fmt.Println("To audit a specific session timeline, provide the session ID or rank: go run . <session> --timeline")
		return
	}

	var (
		costByRule  OrderedFloats
		turns       []*microTurn
		userPrompts []*time.Time
		sessionsSet = map[string]bool{}
	)

	for _, f := range globSessions() {
		if since != nil && time.Unix(0, int64(mtime(f)*1e9)).Before(*since) {
			continue
		}
		currentCwd := ""
		currentID := ""
		prevModel := ""

		_ = readJSONLines(f, func(line []byte) {
			entry, ok := parseEntry(line)
			if !ok {
				return
			}
			if entry.Type == "session" {
				currentCwd = entry.Cwd
				currentID = entry.ID
			}
			if workspace != "" && currentCwd != "" && !strings.Contains(currentCwd, workspace) {
				return
			}
			ts := entry.time
			if ts == nil {
				return
			}
			if since != nil && ts.Before(*since) {
				return
			}
			if until != nil && ts.After(*until) {
				return
			}
			if entry.Type != "message" || entry.Message == nil {
				return
			}
			msg := entry.Message
			switch msg.Role {
			case "user":
				sessionsSet[sessionKey(currentID, f)] = true
				userPrompts = append(userPrompts, ts)
			case "assistant":
				sessionsSet[sessionKey(currentID, f)] = true
				m := msg.Model
				if m == "" {
					m = "unknown"
				}
				usage := msg.Usage
				var inp, cr, cw, out, reas int
				var piCost Cost
				if usage != nil {
					inp, cr, cw, out, reas = usage.Input, usage.CacheRead, usage.CacheWrite, usage.Output, usage.Reasoning
					piCost = usage.cost()
				}
				isErr := msg.StopReason == "error"

				comp, ruleID, rates := priceTurn(book, msg.Provider, m, ts, usage, isErr)
				if ruleID != nil {
					costByRule.Add(*ruleID, comp.Total)
				}
				turns = append(turns, &microTurn{
					ts:          ts,
					sessionID:   currentID,
					cwd:         currentCwd,
					model:       m,
					provider:    msg.Provider,
					prevModel:   prevModel,
					inp:         inp,
					cr:          cr,
					cacheWrite:  cw,
					out:         out,
					reas:        reas,
					totalCtx:    inp + cr,
					cost:        comp.Total,
					piCost:      piCost,
					pricingRule: ruleID,
					rates:       rates,
					costOutput:  comp.Output,
					tools:       toolCalls(msg.Content),
					errMsg:      errorMessagePtr(msg, isErr),
				})
				prevModel = m
			}
		})
	}

	sort.SliceStable(turns, func(i, j int) bool { return turns[i].ts.Before(*turns[j].ts) })
	sort.SliceStable(userPrompts, func(i, j int) bool { return userPrompts[i].Before(*userPrompts[j]) })

	if len(turns) == 0 && len(userPrompts) == 0 {
		fmt.Printf("\nNo activity found for window: %s\n", label)
		return
	}

	// Rolling 60s throughput over the sorted stream.
	left := 0
	var windowSum int64
	var peakTPM int64
	peakRPM := 0
	peakTurnIdx := 1
	totalCost := 0.0
	costWithoutCache := 0.0
	var modelsCounter OrderedInts
	var apiErrors []microAPIError

	for i, t := range turns {
		t.turnIdx = i + 1
		cutoff := t.ts.Add(-60 * time.Second)
		for left < i && turns[left].ts.Before(cutoff) {
			windowSum -= int64(turns[left].totalCtx)
			left++
		}
		t.rollingTPM = windowSum
		t.rollingRPM = i - left
		windowSum += int64(t.totalCtx)

		if t.rollingTPM > peakTPM {
			peakTPM = t.rollingTPM
			peakTurnIdx = t.turnIdx
		}
		if t.rollingRPM > peakRPM {
			peakRPM = t.rollingRPM
		}

		modelsCounter.Inc(t.model)
		totalCost += t.cost

		if t.rates != nil {
			costWithoutCache += pricing.UncachedCost(*t.rates, t.inp, t.cr, t.cacheWrite, t.out)
		} else {
			rate := 0.0
			if t.inp > 0 && t.piCost.Input > 0 {
				rate = t.piCost.Input / float64(t.inp)
			}
			costWithoutCache += float64(t.totalCtx)*rate + t.costOutput
		}

		if t.errMsg != nil {
			apiErrors = append(apiErrors, microAPIError{
				Turn:       t.turnIdx,
				TS:         t.ts,
				RollingTPM: t.rollingTPM,
				RollingRPM: t.rollingRPM,
				Snippet:    errorSnippet(*t.errMsg),
			})
		}
	}

	if costWithoutCache < totalCost {
		costWithoutCache = totalCost
	}
	savings := costWithoutCache - totalCost
	savingsPct := 0.0
	if costWithoutCache > 0 {
		savingsPct = savings / costWithoutCache * 100
	}
	if apiErrors == nil {
		apiErrors = []microAPIError{}
	}

	if asJSON {
		out := microOutput{
			Label:            label,
			Since:            since,
			Until:            until,
			TurnsCount:       len(turns),
			PromptsCount:     len(userPrompts),
			SessionsCount:    len(sessionsSet),
			TotalCost:        totalCost,
			CostWithoutCache: costWithoutCache,
			Savings:          savings,
			SavingsPct:       savingsPct,
			PeakTPM60s:       peakTPM,
			PeakRPM60s:       peakRPM,
			Pricing:          pricingMetadata(book),
			CostByRule:       costByRule,
			APIErrors:        apiErrors,
		}
		printJSON(out)
		return
	}

	fmt.Println("\n" + strings.Repeat("=", 68))
	fmt.Printf("           PI TIME-WINDOW DELVE: %s\n", strings.ToUpper(label))
	fmt.Println(strings.Repeat("=", 68))
	startLabel := "Earliest"
	if since != nil {
		startLabel = since.Format("2006-01-02 15:04:05 MST")
	}
	endLabel := "Latest"
	if until != nil {
		endLabel = until.Format("2006-01-02 15:04:05 MST")
	}
	fmt.Printf("Window      : %s -> %s\n", startLabel, endLabel)
	fmt.Printf("Activity    : %d assistant turns, %d user prompts across %d session(s)\n", len(turns), len(userPrompts), len(sessionsSet))
	modelsStr := modelCounterString(modelsCounter)
	fmt.Printf("Models Used : %s\n", modelsStr)
	fmt.Printf("Cost        : $%.4f (Saved $%.4f / %.1f%% vs $%.4f w/o cache)\n", totalCost, savings, savingsPct, costWithoutCache)
	if ruleStr := spendByRuleStr(costByRule, "%s: $%.4f"); ruleStr != "" {
		fmt.Printf("Spend by Rule: %s\n", ruleStr)
	}
	fmt.Printf("Peak 60s TPM: %s tokens/min (at Turn %d) | Peak RPM: %d turns/min\n", thousands64(peakTPM), peakTurnIdx, peakRPM)

	if len(apiErrors) > 0 {
		fmt.Println("\nAPI RATE ERRORS DETECTED:")
		for _, err := range apiErrors {
			fmt.Printf("  * Turn %d (%s): %s (rolling 60s was %s tokens across %d turns)\n",
				err.Turn, err.TS.Format("15:04:05"), err.Snippet, thousands64(err.RollingTPM), err.RollingRPM)
		}
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Printf("%-10s %-5s %-18s %-12s %-13s %s\n", "Time", "Turn", "Model", "Total Ctx", "Rolling TPM", "Note / Tools")
	fmt.Println(strings.Repeat("-", 68))

	sampleTurns := microSample(turns, verbose)

	var prevRule *string
	for _, t := range sampleTurns {
		note := ""
		switch {
		case t.errMsg != nil:
			if strings.Contains(*t.errMsg, "429") {
				note = "[FAIL] 429 RATE LIMIT"
			} else {
				note = "[FAIL] API ERROR"
			}
		case t.prevModel != "" && t.prevModel != t.model:
			note = "MODEL SWITCH: " + t.model
		case t.inp > 25000:
			note = fmt.Sprintf("SPIKE: +%dk fresh", t.inp/1000)
		case len(t.tools) > 0:
			n := len(t.tools)
			if n > 2 {
				n = 2
			}
			note = "`" + strings.Join(t.tools[:n], ",") + "`"
		}
		rule := t.pricingRule
		if rule != nil && (prevRule == nil || *rule != *prevRule) {
			if note != "" {
				note += " "
			}
			note += "[RULE:" + *rule + "]"
		}
		if rule != nil {
			prevRule = rule
		}
		fmt.Printf("%-10s %-5d %-18s %-12s %-13s %s\n", t.ts.Format("15:04:05"), t.turnIdx, truncate(t.model, 17), thousands(t.totalCtx), thousands64(t.rollingTPM), note)
	}
	if len(sampleTurns) < len(turns) {
		fmt.Printf("... and %d more turns omitted (use --verbose to view all)\n", len(turns)-len(sampleTurns))
	}
	fmt.Println(strings.Repeat("=", 68) + "\n")
}

func microSample(turns []*microTurn, verbose bool) []*microTurn {
	if verbose || len(turns) <= 40 {
		return turns
	}
	mid := len(turns) / 2
	var sample []*microTurn
	sample = append(sample, turns[:15]...)
	start := mid - 5
	if start < 0 {
		start = 0
	}
	end := mid + 5
	if end > len(turns) {
		end = len(turns)
	}
	sample = append(sample, turns[start:end]...)
	sample = append(sample, turns[len(turns)-15:]...)
	return sample
}

func sessionKey(id, fallback string) string {
	if id == "" {
		return fallback
	}
	return id
}

func modelCounterString(models OrderedInts) string {
	parts := make([]string, 0, models.Len())
	for _, m := range models.Keys() {
		parts = append(parts, fmt.Sprintf("%s (%d)", m, models.Get(m)))
	}
	return strings.Join(parts, ", ")
}

// --- macro mode --------------------------------------------------------------

type macroOutput struct {
	Label             string        `json:"label"`
	SessionsCount     int           `json:"sessions_count"`
	PromptsCount      int           `json:"prompts_count"`
	AssistantTurns    int           `json:"assistant_turns"`
	TotalCost         float64       `json:"total_cost"`
	TotalUncachedCost float64       `json:"total_uncached_cost"`
	Savings           float64       `json:"savings"`
	SavingsPct        float64       `json:"savings_pct"`
	Models            OrderedInts   `json:"models"`
	ModelCosts        OrderedFloats `json:"model_costs"`
	PeakTPM60s        int64         `json:"peak_tpm_60s"`
	PeakRPM60s        int           `json:"peak_rpm_60s"`
	Pricing           PricingMeta   `json:"pricing"`
	CostByRule        OrderedFloats `json:"cost_by_rule"`
	APIErrorsCount    int           `json:"api_errors_count"`
}

type macroAgg struct {
	sessionsSet       map[string]bool
	prompts           int
	assistantTurns    int
	totalCost         float64
	totalUncachedCost float64
	costByRule        OrderedFloats
	hourlyHistogram   [24]int
	hourlyModels      [24]map[string]int64
	weekdayHistogram  [7]int
	weekdayModels     [7]map[string]int64
	modelTurns        OrderedInts
	modelCost         OrderedFloats
	thinkTimes        []float64
	workspaces        OrderedInts
	apiErrorsCount    int
	windowEvents      []windowEvent
}

type windowEvent struct {
	ts  time.Time
	ctx int
}

// analyzeTimeWindowMacro aggregates a daily, weekly, monthly or historical
// range. A nil since/until means all time (used by --habits).
func analyzeTimeWindowMacro(since, until *time.Time, label string, asJSON bool, workspace string, book *pricing.Book) {
	files := globSessions()
	if len(files) == 0 {
		fmt.Printf("No session logs found in %s\n", sessionsDir())
		return
	}

	agg := macroAgg{sessionsSet: map[string]bool{}, hourlyModels: newHourModelMaps(), weekdayModels: newWeekModelMaps()}

	for _, f := range files {
		if since != nil && time.Unix(0, int64(mtime(f)*1e9)).Before(*since) {
			continue
		}
		lastAssistantTS := (*time.Time)(nil)
		currentCwd := ""
		currentID := ""

		_ = readJSONLines(f, func(line []byte) {
			entry, ok := parseEntry(line)
			if !ok {
				return
			}
			if entry.Type == "session" {
				currentCwd = entry.Cwd
				currentID = entry.ID
			}
			if workspace != "" && currentCwd != "" && !strings.Contains(currentCwd, workspace) {
				return
			}
			ts := entry.time
			if ts == nil {
				return
			}
			if since != nil && ts.Before(*since) {
				return
			}
			if until != nil && ts.After(*until) {
				return
			}
			if entry.Type != "message" || entry.Message == nil {
				return
			}
			msg := entry.Message
			switch msg.Role {
			case "user":
				agg.sessionsSet[sessionKey(currentID, f)] = true
				agg.prompts++
				agg.hourlyHistogram[ts.Hour()]++
				agg.weekdayHistogram[pyWeekday(ts)]++
				if currentCwd != "" {
					agg.workspaces.Inc(currentCwd)
				}
				if lastAssistantTS != nil {
					if delta := ts.Sub(*lastAssistantTS).Seconds(); delta >= 0 {
						agg.thinkTimes = append(agg.thinkTimes, delta)
					}
				}
			case "assistant":
				agg.sessionsSet[sessionKey(currentID, f)] = true
				agg.assistantTurns++
				lastAssistantTS = ts
				m := msg.Model
				if m == "" {
					m = "unknown"
				}
				usage := msg.Usage
				var inp, cr, cw, out int
				if usage != nil {
					inp, cr, cw, out = usage.Input, usage.CacheRead, usage.CacheWrite, usage.Output
				}
				isErr := msg.StopReason == "error"
				totCtx := inp + cr

				comp, ruleID, rates := priceTurn(book, msg.Provider, m, ts, usage, isErr)
				if rates != nil {
					agg.totalUncachedCost += pricing.UncachedCost(*rates, inp, cr, cw, out)
				} else {
					cin := usage.cost().Input
					if inp > 0 && cin > 0 {
						agg.totalUncachedCost += float64(inp+cr)*(cin/float64(inp)) + comp.Output
					} else {
						agg.totalUncachedCost += comp.Total
					}
				}
				if ruleID != nil {
					agg.costByRule.Add(*ruleID, comp.Total)
				}
				agg.totalCost += comp.Total
				agg.modelTurns.Inc(m)
				agg.modelCost.Add(m, comp.Total)
				hour := ts.Hour()
				agg.hourlyModels[hour][m]++
				day := pyWeekday(ts)
				agg.weekdayModels[day][m]++
				agg.windowEvents = append(agg.windowEvents, windowEvent{ts: *ts, ctx: totCtx})
				if isErr {
					agg.apiErrorsCount++
				}
			}
		})
	}

	if agg.assistantTurns == 0 && agg.prompts == 0 {
		fmt.Printf("\nNo session activity found for period: %s\n", label)
		return
	}

	peakTPM, peakRPM := peakWindow(agg.windowEvents)
	savings := agg.totalUncachedCost - agg.totalCost
	savingsPct := 0.0
	if agg.totalUncachedCost > 0 {
		savingsPct = savings / agg.totalUncachedCost * 100
	}

	if asJSON {
		out := macroOutput{
			Label:             label,
			SessionsCount:     len(agg.sessionsSet),
			PromptsCount:      agg.prompts,
			AssistantTurns:    agg.assistantTurns,
			TotalCost:         agg.totalCost,
			TotalUncachedCost: agg.totalUncachedCost,
			Savings:           savings,
			SavingsPct:        savingsPct,
			Models:            agg.modelTurns,
			ModelCosts:        agg.modelCost,
			PeakTPM60s:        peakTPM,
			PeakRPM60s:        peakRPM,
			Pricing:           pricingMetadata(book),
			CostByRule:        agg.costByRule,
			APIErrorsCount:    agg.apiErrorsCount,
		}
		printJSON(out)
		return
	}

	fmt.Println("\n" + strings.Repeat("=", 68))
	fmt.Printf("             PI MACRO REVIEW: %s\n", strings.ToUpper(label))
	fmt.Println(strings.Repeat("=", 68))
	fmt.Printf("Scope                : %s (%d sessions analyzed)\n", label, len(agg.sessionsSet))
	fmt.Printf("Total User Prompts   : %s\n", thousands(agg.prompts))
	fmt.Printf("Total Assistant Turns: %s\n", thousands(agg.assistantTurns))
	fmt.Printf("Total Model Cost     : $%s (Saved $%s / %.1f%% vs $%s w/o cache)\n",
		thousandsF(agg.totalCost), thousandsF(savings), savingsPct, thousandsF(agg.totalUncachedCost))
	if agg.prompts > 0 {
		fmt.Printf("Autonomy Ratio       : %.1f agent turns per user prompt\n", float64(agg.assistantTurns)/float64(agg.prompts))
	}
	fmt.Printf("Peak 60s Throughput  : %s tokens/min | Peak RPM: %d turns/min\n", thousands64(peakTPM), peakRPM)
	if agg.apiErrorsCount > 0 {
		fmt.Printf("API Rate Errors      : %d error event(s) recorded\n", agg.apiErrorsCount)
	}
	if ruleStr := spendByRuleStr(agg.costByRule, "%s: $%.2f"); ruleStr != "" {
		fmt.Printf("Spend by Rule        : %s\n", ruleStr)
	}

	topModels := agg.modelTurns.SortedByValueDesc()
	if len(topModels) > 4 {
		topModels = topModels[:4]
	}
	symbols := []string{"#", "=", "*", "+"}
	modelSym := map[string]string{}
	for i, m := range topModels {
		modelSym[m] = symbols[i]
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("MODEL USAGE & SPEND BREAKDOWN")
	fmt.Println(strings.Repeat("-", 68))
	maxMCnt := int64(1)
	if len(agg.modelTurns.Keys()) > 0 {
		ordered := agg.modelTurns.SortedByValueDesc()
		if len(ordered) > 0 {
			maxMCnt = agg.modelTurns.Get(ordered[0])
		}
	}
	mostCommon := agg.modelTurns.SortedByValueDesc()
	if len(mostCommon) > 6 {
		mostCommon = mostCommon[:6]
	}
	for _, m := range mostCommon {
		cnt := agg.modelTurns.Get(m)
		pctT := 0.0
		if agg.assistantTurns > 0 {
			pctT = float64(cnt) / float64(agg.assistantTurns) * 100
		}
		pctC := 0.0
		if agg.totalCost > 0 {
			pctC = agg.modelCost.Get(m) / agg.totalCost * 100
		}
		bar := strings.Repeat("#", int(float64(cnt)/float64(maxMCnt)*20))
		sym := "    "
		if s, ok := modelSym[m]; ok {
			sym = "[" + s + "] "
		}
		fmt.Printf("  %s%-24s | %-6s turns (%4.1f%%) | $%-7.2f (%4.1f%%) | %s\n",
			sym, m, thousands(int(cnt)), pctT, agg.modelCost.Get(m), pctC, bar)
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("HOURLY ACTIVITY DISTRIBUTION (Local Time)")
	fmt.Println(strings.Repeat("-", 68))
	legend := make([]string, 0, len(topModels)+1)
	for _, m := range topModels {
		legend = append(legend, "["+modelSym[m]+"] "+m)
	}
	legend = append(legend, "[.] other")
	fmt.Println("Legend:", strings.Join(legend, "  "))
	fmt.Println(strings.Repeat(".", 68))
	maxH := maxInt(agg.hourlyHistogram[:])
	for h := 0; h < 24; h++ {
		fmt.Printf("  %02d:00 | %-5d | %s\n", h, agg.hourlyHistogram[h], stackedBar(agg.hourlyHistogram[h], agg.hourlyModels[h], topModels, modelSym, maxH))
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("WEEKDAY ACTIVITY DISTRIBUTION")
	fmt.Println(strings.Repeat("-", 68))
	dayNames := []string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
	maxD := maxInt(agg.weekdayHistogram[:])
	for d := 0; d < 7; d++ {
		fmt.Printf("  %s   | %-5d | %s\n", dayNames[d], agg.weekdayHistogram[d], stackedBar(agg.weekdayHistogram[d], agg.weekdayModels[d], topModels, modelSym, maxD))
	}

	if len(agg.thinkTimes) > 0 {
		fmt.Println("\n" + strings.Repeat("-", 68))
		fmt.Println("USER REVIEW & THINK-TIME PACING")
		fmt.Println(strings.Repeat("-", 68))
		fast, norm, deep := 0, 0, 0
		for _, t := range agg.thinkTimes {
			switch {
			case t < 30:
				fast++
			case t <= 180:
				norm++
			default:
				deep++
			}
		}
		totalTT := len(agg.thinkTimes)
		sortedTT := append([]float64(nil), agg.thinkTimes...)
		sort.Float64s(sortedTT)
		medianTT := sortedTT[totalTT/2]
		sum := 0.0
		for _, t := range agg.thinkTimes {
			sum += t
		}
		avgTT := sum / float64(totalTT)
		fmt.Printf("  Median Think Time : %.1fs  (Average: %.1fs)\n", medianTT, avgTT)
		fmt.Printf("  Fast Steering (<30s)   : %-5d (%.1f%%)\n", fast, float64(fast)/float64(totalTT)*100)
		fmt.Printf("  Normal Review (30s-3m) : %-5d (%.1f%%)\n", norm, float64(norm)/float64(totalTT)*100)
		fmt.Printf("  Deep Work/Pause (>3m)  : %-5d (%.1f%%)\n", deep, float64(deep)/float64(totalTT)*100)
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("TOP WORKSPACES")
	fmt.Println(strings.Repeat("-", 68))
	wsNames := agg.workspaces.SortedByValueDesc()
	if len(wsNames) > 5 {
		wsNames = wsNames[:5]
	}
	for _, ws := range wsNames {
		fmt.Printf("  - %s: %d prompts\n", ws, agg.workspaces.Get(ws))
	}

	fmt.Println(strings.Repeat("=", 68) + "\n")
}

func newHourModelMaps() [24]map[string]int64 {
	var m [24]map[string]int64
	for i := range m {
		m[i] = map[string]int64{}
	}
	return m
}

func newWeekModelMaps() [7]map[string]int64 {
	var m [7]map[string]int64
	for i := range m {
		m[i] = map[string]int64{}
	}
	return m
}

func pyWeekday(t *time.Time) int {
	return (int(t.Weekday()) + 6) % 7
}

// peakWindow finds the peak 60-second token and request counts over a stream
// of equally-typed events using a sliding window.
func peakWindow(events []windowEvent) (int64, int) {
	sort.SliceStable(events, func(i, j int) bool { return events[i].ts.Before(events[j].ts) })
	left := 0
	var windowSum int64
	var peakTPM int64
	peakRPM := 0
	for i, e := range events {
		cutoff := e.ts.Add(-60 * time.Second)
		for left < i && events[left].ts.Before(cutoff) {
			windowSum -= int64(events[left].ctx)
			left++
		}
		if windowSum > peakTPM {
			peakTPM = windowSum
		}
		if i-left > peakRPM {
			peakRPM = i - left
		}
		windowSum += int64(e.ctx)
	}
	return peakTPM, peakRPM
}

// stackedBar builds a model-segmented bar of the requested length.
func stackedBar(total int, models map[string]int64, topModels []string, symbols map[string]string, max int) string {
	barLen := 0
	if max > 0 {
		barLen = int(float64(total) / float64(max) * 35)
	}
	if barLen == 0 {
		return ""
	}
	totModels := int64(0)
	for _, v := range models {
		totModels += v
	}
	if totModels == 0 {
		return strings.Repeat("#", barLen)
	}
	var b strings.Builder
	for _, m := range topModels {
		seg := int(float64(models[m])/float64(totModels)*float64(barLen) + 0.5)
		b.WriteString(strings.Repeat(symbols[m], seg))
	}
	bar := b.String()
	if len(bar) < barLen {
		bar += strings.Repeat(".", barLen-len(bar))
	} else if len(bar) > barLen {
		bar = bar[:barLen]
	}
	return bar
}

func maxInt(vals []int) int {
	m := 1
	for _, v := range vals {
		if v > m {
			m = v
		}
	}
	return m
}
