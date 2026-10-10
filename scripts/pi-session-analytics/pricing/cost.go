package pricing

// CostComponents holds a turn's absolute cost split by billing component.
type CostComponents struct {
	Input      float64
	Output     float64
	CacheRead  float64
	CacheWrite float64
	Total      float64
}

// ComputeCost prices a turn from raw token counts and an absolute per-1M rate
// set. Long-retention cache writes are billed at twice the fresh-input rate,
// the same rule Pi applies.
func ComputeCost(rates Rates, input, cacheRead, cacheWrite, output, cacheWrite1h int) CostComponents {
	if cacheWrite1h < 0 {
		cacheWrite1h = 0
	}
	longWrite := cacheWrite1h
	shortWrite := cacheWrite - longWrite
	if shortWrite < 0 {
		shortWrite = 0
	}
	costInput := rates.Input / 1e6 * float64(input)
	costOutput := rates.Output / 1e6 * float64(output)
	costCacheRead := rates.CacheRead / 1e6 * float64(cacheRead)
	costCacheWrite := (rates.CacheWrite*float64(shortWrite) + rates.Input*2*float64(longWrite)) / 1e6
	return CostComponents{
		Input:      costInput,
		Output:     costOutput,
		CacheRead:  costCacheRead,
		CacheWrite: costCacheWrite,
		Total:      costInput + costOutput + costCacheRead + costCacheWrite,
	}
}

// UncachedCost is the cost of the same turn if every input token were billed
// as fresh input.
func UncachedCost(rates Rates, input, cacheRead, cacheWrite, output int) float64 {
	return ComputeCost(rates, input+cacheRead+cacheWrite, 0, 0, output, 0).Total
}
