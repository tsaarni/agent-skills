package main

import (
	"bytes"
	"encoding/json"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"pi-session-analytics/pricing"
)

// pricingMetadata builds the serializable description of the active book.
func pricingMetadata(book *pricing.Book) PricingMeta {
	if book == nil {
		return PricingMeta{Enabled: false}
	}
	file, tz, cur, src := book.Path, book.TimezoneName, book.Currency, book.Source
	return PricingMeta{Enabled: true, File: &file, Timezone: &tz, Currency: &cur, Source: &src}
}

// priceTurn resolves one turn's cost. It returns the cost components, the
// matched rule id (nil when Pi's logged cost is kept) and the matched rates.
func priceTurn(book *pricing.Book, provider, model string, ts *time.Time, usage *Usage, isError bool) (pricing.CostComponents, *string, *pricing.Rates) {
	var logged Cost
	if usage != nil && usage.Cost != nil {
		logged = *usage.Cost
	}
	fallback := pricing.CostComponents{
		Input:      logged.Input,
		Output:     logged.Output,
		CacheRead:  logged.CacheRead,
		CacheWrite: logged.CacheWrite,
		Total:      logged.Total,
	}
	if book == nil || isError {
		return fallback, nil, nil
	}
	at := time.Now()
	if ts != nil {
		at = *ts
	}
	rates, ruleID, ok := book.Resolve(provider, model, at)
	if !ok {
		return fallback, nil, nil
	}
	var in, cr, cw, out, cw1h int
	if usage != nil {
		in, cr, cw, out, cw1h = usage.Input, usage.CacheRead, usage.CacheWrite, usage.Output, usage.CacheWrite1h
	}
	return pricing.ComputeCost(rates, in, cr, cw, out, cw1h), &ruleID, &rates
}

func (u *Usage) cost() Cost {
	if u == nil || u.Cost == nil {
		return Cost{}
	}
	return *u.Cost
}

// contentLen counts the characters of a user prompt: the string length, or
// the sum of the text fields when the content is a list of parts.
func contentLen(raw json.RawMessage) int {
	trim := bytes.TrimSpace(raw)
	if len(trim) == 0 || string(trim) == "null" {
		return 0
	}
	if trim[0] == '"' {
		var s string
		if json.Unmarshal(trim, &s) == nil {
			return utf8.RuneCountInString(s)
		}
	}
	if trim[0] == '[' {
		var arr []map[string]any
		if json.Unmarshal(trim, &arr) == nil {
			total := 0
			for _, item := range arr {
				if text, ok := item["text"].(string); ok {
					total += utf8.RuneCountInString(text)
				}
			}
			return total
		}
	}
	return utf8.RuneCountInString(string(trim))
}

func toolCalls(raw json.RawMessage) []string {
	trim := bytes.TrimSpace(raw)
	if len(trim) == 0 || trim[0] != '[' {
		return nil
	}
	var arr []map[string]any
	if json.Unmarshal(trim, &arr) != nil {
		return nil
	}
	var out []string
	for _, item := range arr {
		if typ, _ := item["type"].(string); typ == "toolCall" {
			name, _ := item["name"].(string)
			if name == "" {
				name = "unknown"
			}
			out = append(out, name)
		}
	}
	return out
}

func toolResultSnippet(raw json.RawMessage) string {
	trim := bytes.TrimSpace(raw)
	if len(trim) == 0 {
		return ""
	}
	if trim[0] == '[' {
		var arr []any
		if json.Unmarshal(trim, &arr) == nil && len(arr) > 0 {
			if m, ok := arr[0].(map[string]any); ok {
				if text, ok := m["text"].(string); ok {
					return truncateRunes(text, 120)
				}
			}
			b, _ := json.Marshal(arr[0])
			return truncateRunes(string(b), 120)
		}
	}
	if trim[0] == '"' {
		var s string
		if json.Unmarshal(trim, &s) == nil {
			return truncateRunes(s, 120)
		}
	}
	return truncateRunes(string(trim), 120)
}

func truncateRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	runes := []rune(s)
	return string(runes[:n])
}

func cleanSnippet(s string) string {
	// Substitute newlines and trim; do not collapse runs of spaces.
	return strings.TrimSpace(strings.ReplaceAll(s, "\n", " "))
}

// analyzeSession computes comprehensive metrics along the active branch.
func analyzeSession(branch []*Entry, totalRawEntries int, meta SessionMeta, book *pricing.Book) *SessionAnalysis {
	var (
		freshInput, cacheRead, cacheWrite int
		outputTokens, reasoningTokens     int
		totalCost, piReportedCost         float64
		costWithoutCache                  float64
		costFreshInput, costCacheRead     float64
		costOutput                        float64
		repricedTurns                     int
		models                            OrderedInts
		costByModel                       OrderedFloats
		costByRule                        OrderedFloats
		toolCounts                        OrderedInts
		toolErrors                        []ToolError
		customNodes                       []CustomNode
		promptLengths                     []int
		thinkTimes                        []float64
		cacheBusts, prefixBusts           []*CacheEvent
		serverEvictions, idleExpirations  []*CacheEvent
		modelSwitches                     []*ModelSwitch
		assistantTurns                    []*Turn
	)

	var (
		lastAssistantEndTS        *time.Time
		lastSuccessfulAssistantTS *time.Time
		lastSuccessfulModel       string
		hasLastSuccessfulModel    bool
		lastSuccessfulTotalCtx    int
		prevAssistantTS           *time.Time
		prevAssistantModel        string
		hasPrevAssistantModel     bool
		prevTotalCtx              int
	)

	var firstTS *time.Time
	if meta.Timestamp != nil {
		firstTS = parseTimestampString(*meta.Timestamp)
	}
	var lastTS *time.Time

	// trigger tracks the most recent message/model_change/custom node.
	trigger := "none"

	for _, entry := range branch {
		ts := entry.time
		if ts != nil {
			if firstTS == nil {
				firstTS = ts
			}
			lastTS = ts
		}

		switch entry.Type {
		case "custom":
			customNodes = append(customNodes, CustomNode{
				ID:         entry.ID,
				CustomType: entry.CustomType,
				Timestamp:  rawToString(entry.Timestamp),
			})
			trigger = "custom:" + entry.CustomType
			continue
		case "model_change":
			trigger = "model_change:" + entry.ModelID
			continue
		case "message":
			// handled below
		default:
			continue
		}

		msg := entry.Message
		if msg == nil {
			continue
		}
		role := msg.Role

		switch role {
		case "user":
			promptLengths = append(promptLengths, contentLen(msg.Content))
			if lastAssistantEndTS != nil && ts != nil {
				if delta := ts.Sub(*lastAssistantEndTS).Seconds(); delta >= 0 {
					thinkTimes = append(thinkTimes, delta)
				}
			}
			trigger = "user"

		case "assistant":
			model := msg.Model
			if model == "" {
				model = "unknown"
			}
			models.Inc(model)

			usage := msg.Usage
			var inp, cr, cw, out, reas int
			if usage != nil {
				inp, cr, cw, out, reas = usage.Input, usage.CacheRead, usage.CacheWrite, usage.Output, usage.Reasoning
			}
			isError := msg.StopReason == "error" || msg.ErrorMessage != ""
			provider := msg.Provider

			comp, ruleID, rates := priceTurn(book, provider, model, ts, usage, isError)
			effCost := comp.Total
			effOut := comp.Output
			piTotal := usage.cost().Total
			piReportedCost += piTotal
			costFreshInput += comp.Input
			costCacheRead += comp.CacheRead
			costOutput += effOut

			if rates != nil {
				costWithoutCache += pricing.UncachedCost(*rates, inp, cr, cw, out)
			} else {
				logged := usage.cost()
				legacyRate := 0.0
				if inp > 0 && logged.Input > 0 {
					legacyRate = logged.Input / float64(inp)
				}
				costWithoutCache += float64(inp+cr)*legacyRate + effOut
			}

			if ruleID != nil {
				repricedTurns++
				costByRule.Add(*ruleID, effCost)
			}

			freshInput += inp
			cacheRead += cr
			cacheWrite += cw
			outputTokens += out
			reasoningTokens += reas
			totalCost += effCost
			costByModel.Add(model, effCost)

			totCtx := inp + cr
			turnIdx := len(assistantTurns) + 1

			// Evaluate cache transitions against the last primed warm context.
			warmCtx := prevTotalCtx
			if lastSuccessfulTotalCtx > 0 {
				warmCtx = lastSuccessfulTotalCtx
			}
			var refModel string
			switch {
			case hasLastSuccessfulModel:
				refModel = lastSuccessfulModel
			case hasPrevAssistantModel:
				refModel = prevAssistantModel
			}
			var refTS *time.Time
			if lastSuccessfulAssistantTS != nil {
				refTS = lastSuccessfulAssistantTS
			} else {
				refTS = prevAssistantTS
			}
			timeSincePrime := 0.0
			if ts != nil && refTS != nil {
				timeSincePrime = ts.Sub(*refTS).Seconds()
			}

			if !isError && totCtx > 0 {
				switch {
				case refModel != "" && refModel != model:
					modelSwitches = append(modelSwitches, &ModelSwitch{
						Turn:       turnIdx,
						FromModel:  refModel,
						ToModel:    model,
						FreshInput: inp,
						Cost:       effCost,
						TotalCtx:   totCtx,
					})
				case warmCtx >= 32768 && (cr == 0 || float64(cr) < 0.1*float64(totCtx)) && totCtx >= 32768:
					event := &CacheEvent{
						Turn:       turnIdx,
						TotalCtx:   totCtx,
						FreshInput: inp,
						Cost:       effCost,
						TimeGapSec: timeSincePrime,
					}
					if timeSincePrime > 300.0 {
						idleExpirations = append(idleExpirations, event)
					} else {
						cacheBusts = append(cacheBusts, event)
						if len(customNodes) > 0 {
							event.Reason = "graph_pollution"
							prefixBusts = append(prefixBusts, event)
						} else {
							event.Reason = "clean_prefix_eviction"
							serverEvictions = append(serverEvictions, event)
						}
					}
				}
			}

			toolsInTurn := toolCalls(msg.Content)
			for _, name := range toolsInTurn {
				toolCounts.Inc(name)
			}

			turn := &Turn{
				Turn:         turnIdx,
				TS:           ts,
				Model:        model,
				Provider:     provider,
				FreshInput:   inp,
				CacheRead:    cr,
				CacheWrite:   cw,
				TotalCtx:     totCtx,
				Output:       out,
				Reasoning:    reas,
				Cost:         effCost,
				PICost:       piTotal,
				PricingRule:  ruleID,
				CostOutput:   effOut,
				Trigger:      trigger,
				Tools:        nonNilStrings(toolsInTurn),
				IsError:      isError,
				ErrorMessage: errorMessagePtr(msg, isError),
			}
			assistantTurns = append(assistantTurns, turn)
			trigger = "assistant"

			if !isError && totCtx > 0 {
				lastSuccessfulTotalCtx = totCtx
				lastSuccessfulAssistantTS = ts
				lastSuccessfulModel = model
				hasLastSuccessfulModel = true
			}
			prevTotalCtx = totCtx
			prevAssistantTS = ts
			prevAssistantModel = model
			hasPrevAssistantModel = true
			lastAssistantEndTS = ts

		case "toolResult":
			if msg.IsError {
				toolErrors = append(toolErrors, ToolError{
					Tool:    orUnknown(msg.ToolName),
					Snippet: cleanSnippet(toolResultSnippet(msg.Content)),
				})
			}
			trigger = "toolResult:" + orUnknown(msg.ToolName)

		default:
			trigger = role
		}
	}

	// Rolling 60-second throughput, computed with a sliding window.
	computeRolling(assistantTurns)
	var apiErrors []APIError
	var peakTPM int64
	peakTPMTurn := 1
	peakRPM := 0
	for _, t := range assistantTurns {
		if t.RollingTPM > peakTPM {
			peakTPM = t.RollingTPM
			peakTPMTurn = t.Turn
		}
		if t.RollingRPM > peakRPM {
			peakRPM = t.RollingRPM
		}
		if t.ErrorMessage != nil {
			apiErrors = append(apiErrors, APIError{
				Turn:       t.Turn,
				TS:         t.TS,
				TotalCtx:   t.TotalCtx,
				RollingTPM: t.RollingTPM,
				RollingRPM: t.RollingRPM,
				Snippet:    errorSnippet(*t.ErrorMessage),
				RawError:   *t.ErrorMessage,
			})
		}
	}
	if apiErrors == nil {
		apiErrors = []APIError{}
	}

	if costWithoutCache < totalCost {
		costWithoutCache = totalCost
	}
	savings := costWithoutCache - totalCost
	savingsPct := 0.0
	if costWithoutCache > 0 {
		savingsPct = savings / costWithoutCache * 100
	}
	reasoningPct := 0.0
	if outputTokens > 0 {
		reasoningPct = float64(reasoningTokens) / float64(outputTokens) * 100
	}

	sortedSpikes := append([]*Turn(nil), assistantTurns...)
	sort.SliceStable(sortedSpikes, func(i, j int) bool { return sortedSpikes[i].FreshInput > sortedSpikes[j].FreshInput })
	topSpikes := sortedSpikes
	if len(topSpikes) > 15 {
		topSpikes = topSpikes[:15]
	}
	if topSpikes == nil {
		topSpikes = []*Turn{}
	}
	recentTurns := lastN(assistantTurns, 5)
	if recentTurns == nil {
		recentTurns = []*Turn{}
	}
	if assistantTurns == nil {
		assistantTurns = []*Turn{}
	}

	wallDuration := 0.0
	if firstTS != nil && lastTS != nil {
		wallDuration = lastTS.Sub(*firstTS).Seconds()
	}
	totalTokens := freshInput + cacheRead + outputTokens
	totIn := freshInput + cacheRead
	hitRate := 0.0
	if totIn > 0 {
		hitRate = float64(cacheRead) / float64(totIn) * 100
	}

	totalCalls := 0
	for _, k := range toolCounts.Keys() {
		totalCalls += int(toolCounts.Get(k))
	}
	errRate := 0.0
	if totalCalls > 0 {
		errRate = float64(len(toolErrors)) / float64(totalCalls) * 100
	}
	if customNodes == nil {
		customNodes = []CustomNode{}
	}
	if toolErrors == nil {
		toolErrors = []ToolError{}
	}
	if cacheBusts == nil {
		cacheBusts = []*CacheEvent{}
	}
	if prefixBusts == nil {
		prefixBusts = []*CacheEvent{}
	}
	if serverEvictions == nil {
		serverEvictions = []*CacheEvent{}
	}
	if idleExpirations == nil {
		idleExpirations = []*CacheEvent{}
	}
	if modelSwitches == nil {
		modelSwitches = []*ModelSwitch{}
	}

	return &SessionAnalysis{
		Meta:                meta,
		FirstTS:             firstTS,
		LastTS:              lastTS,
		WallDurationSec:     wallDuration,
		TotalRawEntries:     totalRawEntries,
		ActiveBranchEntries: len(branch),
		AbandonedEntries:    totalRawEntries - len(branch),
		Models:              models,
		AssistantTurnsCount: len(assistantTurns),
		UserPromptsCount:    len(promptLengths),
		AutonomyRatio:       autonomyRatio(len(assistantTurns), len(promptLengths)),
		Tokens: Tokens{
			FreshInput:   freshInput,
			CacheRead:    cacheRead,
			CacheWrite:   cacheWrite,
			Output:       outputTokens,
			Reasoning:    reasoningTokens,
			ReasoningPct: reasoningPct,
			Total:        totalTokens,
			HitRatePct:   hitRate,
		},
		Cost: CostSummary{
			Total:           totalCost,
			PIReportedTotal: piReportedCost,
			Repriced:        repricedTurns > 0,
			FreshInput:      costFreshInput,
			CacheRead:       costCacheRead,
			Output:          costOutput,
			WithoutCache:    costWithoutCache,
			Savings:         savings,
			SavingsPct:      savingsPct,
			ByRule:          costByRule,
		},
		Pricing:     pricingMetadata(book),
		CostByModel: costByModel,
		Velocity: Velocity{
			PeakTPM60s:  peakTPM,
			PeakTPMTurn: peakTPMTurn,
			PeakRPM60s:  peakRPM,
			APIErrors:   apiErrors,
		},
		CacheHealth: CacheHealth{
			DurableCustomNodes: len(customNodes),
			CustomNodesDetails: customNodes,
			CacheBusts:         cacheBusts,
			PrefixBusts:        prefixBusts,
			ServerEvictions:    serverEvictions,
			IdleExpirations:    idleExpirations,
			ModelSwitches:      modelSwitches,
		},
		Tools: Tools{
			TotalCalls:   totalCalls,
			ByName:       toolCounts,
			ErrorCount:   len(toolErrors),
			ErrorRatePct: errRate,
			Errors:       toolErrors,
		},
		UserHabits:     userHabits(promptLengths, thinkTimes),
		TopSpikes:      topSpikes,
		RecentTurns:    recentTurns,
		AssistantTurns: assistantTurns,
	}
}

func userHabits(promptLengths []int, thinkTimes []float64) UserHabits {
	h := UserHabits{}
	if len(promptLengths) > 0 {
		sum := 0
		for _, length := range promptLengths {
			sum += length
		}
		h.AvgPromptLen = float64(sum) / float64(len(promptLengths))
	}
	h.ThinkTimesCount = len(thinkTimes)
	if len(thinkTimes) > 0 {
		sum := 0.0
		for _, t := range thinkTimes {
			sum += t
		}
		sorted := append([]float64(nil), thinkTimes...)
		sort.Float64s(sorted)
		h.AvgThinkSec = sum / float64(len(thinkTimes))
		h.MedianThinkSec = sorted[len(sorted)/2]
		h.MaxThinkSec = sorted[len(sorted)-1]
	}
	return h
}

func autonomyRatio(turns, prompts int) float64 {
	if prompts == 0 {
		return 0
	}
	return float64(turns) / float64(prompts)
}

// computeRolling fills RollingTPM/RollingRPM for chronologically ordered turns
// using an O(n) sliding window instead of the reference implementation's O(n²)
// nested loop.
func computeRolling(turns []*Turn) {
	valid := make([]*Turn, 0, len(turns))
	for _, t := range turns {
		if t.TS != nil {
			valid = append(valid, t)
		}
	}
	left := 0
	var windowSum int64
	for i, t := range valid {
		cutoff := t.TS.Add(-60 * time.Second)
		for left < i && valid[left].TS.Before(cutoff) {
			windowSum -= int64(valid[left].TotalCtx)
			left++
		}
		t.RollingTPM = windowSum
		t.RollingRPM = i - left
		windowSum += int64(t.TotalCtx)
	}
}

func errorMessagePtr(msg *Message, isError bool) *string {
	if !isError {
		return nil
	}
	s := msg.ErrorMessage
	return &s
}

func errorSnippet(raw string) string {
	switch {
	case bytes.Contains([]byte(raw), []byte("429")):
		return "429 Too Many Requests (Rate Limit)"
	case bytes.Contains([]byte(raw), []byte("503")):
		return "503 Service Unavailable"
	case bytes.Contains([]byte(raw), []byte("500")):
		return "500 Internal Server Error"
	case bytes.Contains([]byte(raw), []byte("401")):
		return "401 Unauthorized"
	default:
		return truncateRunes(cleanSnippet(raw), 120)
	}
}

func orUnknown(s string) string {
	if s == "" {
		return "unknown"
	}
	return s
}

func nonNilStrings(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}

func lastN(turns []*Turn, n int) []*Turn {
	if len(turns) <= n {
		return turns
	}
	return turns[len(turns)-n:]
}

func rawToString(raw json.RawMessage) string {
	trim := bytes.TrimSpace(raw)
	if len(trim) == 0 || string(trim) == "null" {
		return ""
	}
	var s string
	if json.Unmarshal(trim, &s) == nil {
		return s
	}
	return string(trim)
}
