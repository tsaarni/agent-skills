package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/dustin/go-humanize"
)

func modelSummary(models OrderedInts) string {
	parts := make([]string, 0, models.Len())
	for _, m := range models.Keys() {
		parts = append(parts, fmt.Sprintf("%s (%d turns)", m, models.Get(m)))
	}
	return strings.Join(parts, ", ")
}

func spendByRuleStr(byRule OrderedFloats, format string) string {
	parts := make([]string, 0, byRule.Len())
	for _, k := range byRule.SortedByValueDesc() {
		parts = append(parts, fmt.Sprintf(format, k, byRule.Get(k)))
	}
	return strings.Join(parts, " | ")
}

// printSessionReport renders the compact or verbose single-session scorecard.
func printSessionReport(data *SessionAnalysis, verbose bool) {
	tokens := data.Tokens
	cache := data.CacheHealth
	tools := data.Tools
	habits := data.UserHabits
	cost := data.Cost

	fmt.Println("\n" + strings.Repeat("=", 68))
	fmt.Println("                PI SESSION AUDIT REPORT")
	fmt.Println(strings.Repeat("=", 68))

	startedStr := "Unknown"
	if data.FirstTS != nil {
		startedStr = data.FirstTS.Format("2006-01-02 15:04:05 MST")
	}
	durStr := formatDuration(data.WallDurationSec)
	fmt.Printf("Session ID  : %s\n", metaString(data.Meta.ID))
	fmt.Printf("Workspace   : %s\n", metaString(data.Meta.Cwd))
	fmt.Printf("Started     : %s (%s elapsed)\n", startedStr, durStr)
	modelsStr := modelSummary(data.Models)
	if modelsStr == "" {
		modelsStr = "None"
	}
	fmt.Printf("Model(s)    : %s\n", modelsStr)
	fmt.Printf("Graph Path  : %d active nodes (%d abandoned turns pruned)\n", data.ActiveBranchEntries, data.AbandonedEntries)

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("1. COST & TOKEN ECONOMICS")
	fmt.Println(strings.Repeat("-", 68))
	fmt.Printf("  Actual Cost      : $%.4f\n", cost.Total)
	if cost.Repriced {
		delta := cost.Total - cost.PIReportedTotal
		sign := ""
		if delta >= 0 {
			sign = "+"
		}
		src := "pricing.json"
		if data.Pricing.File != nil {
			src = baseName(*data.Pricing.File)
		}
		tz := "UTC"
		if data.Pricing.Timezone != nil {
			tz = *data.Pricing.Timezone
		}
		fmt.Printf("  Pi-Reported Cost : $%.4f (re-priced with %s / %s, delta %s$%.4f)\n", cost.PIReportedTotal, src, tz, sign, delta)
		if cost.ByRule.Len() > 0 {
			fmt.Printf("  Spend by Rule    : %s\n", spendByRuleStr(cost.ByRule, "%s: $%.4f"))
		}
	}
	if data.Models.Len() > 1 {
		fmt.Println("  Model Breakdown  :")
		maxTurns := int64(1)
		for _, m := range data.Models.Keys() {
			if v := data.Models.Get(m); v > maxTurns {
				maxTurns = v
			}
		}
		totTurns := int64(0)
		for _, m := range data.Models.Keys() {
			totTurns += data.Models.Get(m)
		}
		if totTurns == 0 {
			totTurns = 1
		}
		for _, m := range data.Models.Keys() {
			count := data.Models.Get(m)
			pct := float64(count) / float64(totTurns) * 100
			mCost := data.CostByModel.Get(m)
			bar := strings.Repeat("#", int(float64(count)/float64(maxTurns)*20))
			fmt.Printf("    * %-20s: %-3d turns (%4.1f%%) | $%-6.4f | %s\n", m, count, pct, mCost, bar)
		}
	}
	if cost.WithoutCache > cost.Total {
		fmt.Printf("  Cost w/o Cache   : $%.4f (Saved $%.4f / %.1f%%)\n", cost.WithoutCache, cost.Savings, cost.SavingsPct)
	}
	if cost.Total > 0 {
		pctCR := cost.CacheRead / cost.Total * 100
		pctIn := cost.FreshInput / cost.Total * 100
		pctOut := cost.Output / cost.Total * 100
		fmt.Printf("  Cost Breakdown   : Context cache: $%.4f (%.1f%%) | Fresh input: $%.4f (%.1f%%) | Output: $%.4f (%.1f%%)\n",
			cost.CacheRead, pctCR, cost.FreshInput, pctIn, cost.Output, pctOut)
	}
	fmt.Printf("  Overall Cache Hit: %.1f%%\n", tokens.HitRatePct)
	fmt.Printf("  Fresh Input      : %s tokens\n", thousands(tokens.FreshInput))
	fmt.Printf("  Cache Read       : %s tokens\n", thousands(tokens.CacheRead))
	fmt.Printf("  Output Generated : %s tokens (incl. %s reasoning / %.1f%%)\n", thousands(tokens.Output), thousands(tokens.Reasoning), tokens.ReasoningPct)
	fmt.Printf("  Total Tokens     : %s\n", thousands(tokens.Total))

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("2. CACHE HEALTH & RATE-LIMIT VELOCITY")
	fmt.Println(strings.Repeat("-", 68))
	velocity := data.Velocity
	if velocity.PeakTPM60s != 0 {
		fmt.Printf("  Peak 60s Velocity: %s tokens/min (at Turn %d) | Peak RPM: %d turns/min\n",
			thousands64(velocity.PeakTPM60s), velocity.PeakTPMTurn, velocity.PeakRPM60s)
	}

	if len(velocity.APIErrors) > 0 {
		apiErrs := velocity.APIErrors
		if !verbose && len(apiErrs) > 8 {
			apiErrs = apiErrs[:8]
		}
		fmt.Printf("  API Rate Errors  : [FAIL] %d provider error(s) detected:\n", len(velocity.APIErrors))
		for _, err := range apiErrs {
			fmt.Printf("                     - Turn %d: %s (rolling 60s was %s tokens across %d turns)\n",
				err.Turn, err.Snippet, thousands64(err.RollingTPM), err.RollingRPM)
		}
		if len(velocity.APIErrors) > len(apiErrs) {
			fmt.Printf("                     ... and %d more provider errors (use --verbose to view all)\n", len(velocity.APIErrors)-len(apiErrs))
		}
	}

	if cache.DurableCustomNodes == 0 {
		fmt.Println("  Graph Pollution  : [PASS] 0 durable custom nodes on active chain.")
	} else {
		cns := cache.CustomNodesDetails
		if !verbose && len(cns) > 5 {
			cns = cns[:5]
		}
		fmt.Printf("  Graph Pollution  : [FAIL] %d custom nodes found!\n", cache.DurableCustomNodes)
		for _, cn := range cns {
			fmt.Printf("                     - type='%s', id=%s\n", cn.CustomType, cn.ID)
		}
		if len(cache.CustomNodesDetails) > len(cns) {
			fmt.Printf("                     ... and %d more (use --verbose to view all)\n", len(cache.CustomNodesDetails)-len(cns))
		}
	}

	if len(cache.CacheBusts) == 0 && len(cache.IdleExpirations) == 0 && len(cache.ModelSwitches) == 0 {
		fmt.Println("  Prefix Integrity : [PASS] No unexpected cache drops detected.")
	} else {
		if len(cache.PrefixBusts) > 0 {
			list := cache.PrefixBusts
			if !verbose && len(list) > 5 {
				list = list[:5]
			}
			fmt.Printf("  Prefix Busts     : [CRITICAL] %d full invalidation events (<5m gap, graph polluted)!\n", len(cache.PrefixBusts))
			for _, b := range list {
				fmt.Printf("                     - Turn %d: %s ctx dropped to 0 cache read (%.1fs gap, cost $%.4f)\n",
					b.Turn, thousands(b.TotalCtx), b.TimeGapSec, b.Cost)
			}
			if len(cache.PrefixBusts) > len(list) {
				fmt.Printf("                     ... and %d more prefix busts (use --verbose to view all)\n", len(cache.PrefixBusts)-len(list))
			}
		}
		if len(cache.ServerEvictions) > 0 {
			list := cache.ServerEvictions
			if !verbose && len(list) > 5 {
				list = list[:5]
			}
			fmt.Printf("  Server Evictions : [WARN] %d cache drops on clean prefix (<5m gap, provider eviction/routing miss)\n", len(cache.ServerEvictions))
			for _, ev := range list {
				fmt.Printf("                     - Turn %d: %s ctx dropped to 0 cache read (%.1fs gap, cost $%.4f)\n",
					ev.Turn, thousands(ev.TotalCtx), ev.TimeGapSec, ev.Cost)
			}
			if len(cache.ServerEvictions) > len(list) {
				fmt.Printf("                     ... and %d more server evictions (use --verbose to view all)\n", len(cache.ServerEvictions)-len(list))
			}
		} else if len(cache.CacheBusts) > 0 && len(cache.PrefixBusts) == 0 && len(cache.ServerEvictions) == 0 {
			list := cache.CacheBusts
			if !verbose && len(list) > 5 {
				list = list[:5]
			}
			fmt.Printf("  Prefix Busts     : [WARN] %d cache invalidation events (<5m gap)!\n", len(cache.CacheBusts))
			for _, b := range list {
				fmt.Printf("                     - Turn %d: %s ctx dropped to 0 cache read (%.1fs gap)\n", b.Turn, thousands(b.TotalCtx), b.TimeGapSec)
			}
			if len(cache.CacheBusts) > len(list) {
				fmt.Printf("                     ... and %d more invalidation events (use --verbose to view all)\n", len(cache.CacheBusts)-len(list))
			}
		}
		if len(cache.ModelSwitches) > 0 {
			list := cache.ModelSwitches
			if !verbose && len(list) > 5 {
				list = list[:5]
			}
			fmt.Printf("  Model Switches   : [INFO] %d cache re-seeds due to in-flight model switches\n", len(cache.ModelSwitches))
			for _, ms := range list {
				fmt.Printf("                     - Turn %d: switched '%s' -> '%s' (re-seeded %s tokens, cost $%.4f)\n",
					ms.Turn, ms.FromModel, ms.ToModel, thousands(ms.FreshInput), ms.Cost)
			}
			if len(cache.ModelSwitches) > len(list) {
				fmt.Printf("                     ... and %d more model switches (use --verbose to view all)\n", len(cache.ModelSwitches)-len(list))
			}
		}
		if len(cache.IdleExpirations) > 0 {
			list := cache.IdleExpirations
			if !verbose && len(list) > 5 {
				list = list[:5]
			}
			fmt.Printf("  Idle Expirations : [INFO] %d cache drops due to user idle timeout (>5m)\n", len(cache.IdleExpirations))
			for _, exp := range list {
				fmt.Printf("                     - Turn %d: idle for %s (re-seeded %s tokens, cost $%.4f)\n",
					exp.Turn, formatDuration(exp.TimeGapSec), thousands(exp.FreshInput), exp.Cost)
			}
			if len(cache.IdleExpirations) > len(list) {
				fmt.Printf("                     ... and %d more idle expirations (use --verbose to view all)\n", len(cache.IdleExpirations)-len(list))
			}
		}
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("3. TOOL EXECUTION & EFFICIENCY")
	fmt.Println(strings.Repeat("-", 68))
	toolParts := make([]string, 0, tools.ByName.Len())
	for _, name := range tools.ByName.SortedByValueDesc() {
		toolParts = append(toolParts, fmt.Sprintf("%s: %d", name, tools.ByName.Get(name)))
	}
	toolSummary := strings.Join(toolParts, ", ")
	if toolSummary == "" {
		toolSummary = "None"
	}
	fmt.Printf("  Calls Breakdown  : %s\n", toolSummary)
	fmt.Printf("  Tool Error Rate  : %d (%.1f%%)\n", tools.ErrorCount, tools.ErrorRatePct)
	if len(tools.Errors) > 0 {
		fmt.Println("  Failed Calls     :")
		errs := tools.Errors
		if !verbose && len(errs) > 4 {
			errs = errs[:4]
		}
		for _, err := range errs {
			fmt.Printf("    * [%s] %s\n", err.Tool, err.Snippet)
		}
		if len(tools.Errors) > len(errs) {
			fmt.Printf("    ... and %d more tool failures (use --verbose to view all)\n", len(tools.Errors)-len(errs))
		}
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("4. TOP CONTEXT SPIKES (Largest Fresh Inputs)")
	fmt.Println(strings.Repeat("-", 68))
	fmt.Printf("  %-6s %-14s %-14s %-10s %s\n", "Turn", "Fresh Input", "Total Ctx", "Cost", "Caused By")
	spikes := data.TopSpikes
	if !verbose && len(spikes) > 5 {
		spikes = spikes[:5]
	}
	for _, spike := range spikes {
		fmt.Printf("  %-6d %-14s %-14s $%-9.4f %s\n", spike.Turn, thousands(spike.FreshInput), thousands(spike.TotalCtx), spike.Cost, spike.Trigger)
	}
	if len(data.TopSpikes) > len(spikes) {
		fmt.Printf("  ... and %d more context spikes omitted (use --verbose to view all)\n", len(data.TopSpikes)-len(spikes))
	}

	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("5. USER HABITS & INTERACTION PACING")
	fmt.Println(strings.Repeat("-", 68))
	fmt.Printf("  User Prompts     : %d\n", data.UserPromptsCount)
	fmt.Printf("  Autonomy Ratio   : %.1f assistant turns per user prompt\n", data.AutonomyRatio)
	fmt.Printf("  Avg Prompt Size  : %.0f characters\n", habits.AvgPromptLen)
	if habits.ThinkTimesCount > 0 {
		fmt.Printf("  Review/Think Time: median %.1fs | avg %.1fs | max %s\n", habits.MedianThinkSec, habits.AvgThinkSec, formatDuration(habits.MaxThinkSec))
	}
	fmt.Println(strings.Repeat("=", 68) + "\n")
}

// printSessionTimeline renders a chronological turn-by-turn stream.
func printSessionTimeline(data *SessionAnalysis, verbose bool) {
	cost := data.Cost
	velocity := data.Velocity
	turns := data.AssistantTurns

	idleTurns := map[int]*CacheEvent{}
	for _, e := range data.CacheHealth.IdleExpirations {
		idleTurns[e.Turn] = e
	}
	evictTurns := map[int]*CacheEvent{}
	for _, e := range data.CacheHealth.ServerEvictions {
		evictTurns[e.Turn] = e
	}
	switchTurns := map[int]*ModelSwitch{}
	for _, e := range data.CacheHealth.ModelSwitches {
		switchTurns[e.Turn] = e
	}

	fmt.Println("\n" + strings.Repeat("=", 80))
	fmt.Println("                      PI SESSION CHRONOLOGICAL TIMELINE")
	fmt.Println(strings.Repeat("=", 80))
	startedStr := "Unknown"
	if data.FirstTS != nil {
		startedStr = data.FirstTS.Format("2006-01-02 15:04:05 MST")
	}
	fmt.Printf("Session ID  : %s\n", metaString(data.Meta.ID))
	fmt.Printf("Workspace   : %s\n", metaString(data.Meta.Cwd))
	fmt.Printf("Started     : %s (%s elapsed)\n", startedStr, formatDuration(data.WallDurationSec))
	modelsStr := modelSummary(data.Models)
	if modelsStr == "" {
		modelsStr = "None"
	}
	fmt.Printf("Model(s)    : %s\n", modelsStr)
	fmt.Printf("Actual Cost : $%.4f (Saved $%.4f / %.1f%% vs $%.4f w/o cache)\n", cost.Total, cost.Savings, cost.SavingsPct, cost.WithoutCache)
	if velocity.PeakTPM60s != 0 {
		fmt.Printf("Peak 60s TPM: %s tokens/min (at Turn %d) | Peak RPM: %d turns/min\n",
			thousands64(velocity.PeakTPM60s), velocity.PeakTPMTurn, velocity.PeakRPM60s)
	}

	apiErrors := velocity.APIErrors
	if len(apiErrors) > 0 {
		fmt.Printf("\nAPI RATE ERRORS DETECTED (%d):\n", len(apiErrors))
		shown := apiErrors
		if !verbose && len(shown) > 6 {
			shown = shown[:6]
		}
		for _, err := range shown {
			errTS := "??"
			if err.TS != nil {
				errTS = err.TS.Format("15:04:05")
			}
			fmt.Printf("  * Turn %d (%s): %s (rolling 60s: %s tokens across %d turns)\n",
				err.Turn, errTS, err.Snippet, thousands64(err.RollingTPM), err.RollingRPM)
		}
		if len(apiErrors) > 6 && !verbose {
			fmt.Printf("    ... and %d more rate errors omitted (use --verbose to view all)\n", len(apiErrors)-6)
		}
	}

	fmt.Println("\n" + strings.Repeat("-", 80))
	fmt.Printf("%-10s %-6s %-18s %-11s %-11s %-13s %s\n", "Time", "Turn", "Model", "Fresh Inp", "Total Ctx", "Rolling TPM", "Note / Tools")
	fmt.Println(strings.Repeat("-", 80))

	var sampleTurns []*Turn
	if verbose || len(turns) <= 40 {
		sampleTurns = turns
	} else {
		key := map[int]bool{}
		for i := 0; i < 10 && i < len(turns); i++ {
			key[turns[i].Turn] = true
		}
		for i := len(turns) - 10; i < len(turns); i++ {
			if i >= 0 {
				key[turns[i].Turn] = true
			}
		}
		for _, t := range turns {
			if t.ErrorMessage != nil || idleTurns[t.Turn] != nil || evictTurns[t.Turn] != nil || switchTurns[t.Turn] != nil || t.FreshInput >= 25000 {
				key[t.Turn] = true
			}
		}
		for _, t := range turns {
			if key[t.Turn] {
				sampleTurns = append(sampleTurns, t)
			}
		}
	}

	var prevRule *string
	for _, t := range sampleTurns {
		note := ""
		switch {
		case t.ErrorMessage != nil:
			if strings.Contains(*t.ErrorMessage, "429") {
				note = "[FAIL] 429 RATE LIMIT"
			} else {
				note = "[FAIL] API ERROR"
			}
		case idleTurns[t.Turn] != nil:
			note = fmt.Sprintf("[IDLE TTL] +%dk fresh", t.FreshInput/1000)
		case evictTurns[t.Turn] != nil:
			note = "[SERVER EVICT] dropped"
		case switchTurns[t.Turn] != nil:
			note = "MODEL SWITCH: " + t.Model
		case t.FreshInput >= 25000:
			note = fmt.Sprintf("SPIKE: +%dk fresh", t.FreshInput/1000)
		case len(t.Tools) > 0:
			n := len(t.Tools)
			if n > 2 {
				n = 2
			}
			note = "`" + strings.Join(t.Tools[:n], ",") + "`"
		default:
			note = t.Trigger
		}

		rule := t.PricingRule
		if rule != nil && (prevRule == nil || *rule != *prevRule) {
			if note != "" {
				note += " "
			}
			note += "[RULE:" + *rule + "]"
		}
		if rule != nil {
			prevRule = rule
		}

		tsStr := "--:--:--"
		if t.TS != nil {
			tsStr = t.TS.Format("15:04:05")
		}
		fmt.Printf("%-10s #%-5d %-18s %-11s %-11s %-13s %s\n", tsStr, t.Turn, truncate(t.Model, 17), thousands(t.FreshInput), thousands(t.TotalCtx), thousands64(t.RollingTPM), note)
	}

	if len(sampleTurns) < len(turns) {
		fmt.Printf("... and %d ordinary turns omitted (use --verbose to view all %d turns)\n", len(turns)-len(sampleTurns), len(turns))
	}
	fmt.Println(strings.Repeat("=", 80) + "\n")
}

func metaString(p *string) string {
	if p == nil || *p == "" {
		return "Unknown"
	}
	return *p
}

func baseName(path string) string {
	if i := strings.LastIndex(path, "/"); i >= 0 {
		return path[i+1:]
	}
	return path
}

// thousands formats an integer with comma separators.
func thousands(n int) string { return humanize.Comma(int64(n)) }

func thousands64(n int64) string { return humanize.Comma(n) }

// thousandsF formats a float with two decimals and thousands separators,
// rounding (not truncating) like a currency value.
func thousandsF(v float64) string {
	s := fmt.Sprintf("%.2f", v)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	dot := strings.IndexByte(s, '.')
	i, _ := strconv.ParseInt(s[:dot], 10, 64)
	out := humanize.Comma(i) + s[dot:]
	if neg {
		return "-" + out
	}
	return out
}

// printJSON writes indented JSON without HTML escaping.
func printJSON(v any) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}
