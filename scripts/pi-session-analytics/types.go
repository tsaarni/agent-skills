package main

import (
	"encoding/json"
	"time"
)

// --- parsed session log ------------------------------------------------------

// Entry is one JSONL node in a Pi session file.
type Entry struct {
	Type       string          `json:"type"`
	ID         string          `json:"id"`
	ParentID   string          `json:"parentId"`
	Timestamp  json.RawMessage `json:"timestamp"`
	Cwd        string          `json:"cwd"`
	Version    int             `json:"version"`
	ModelID    string          `json:"modelId"`
	Provider   string          `json:"provider"`
	CustomType string          `json:"customType"`
	Message    *Message        `json:"message"`

	time *time.Time // parsed entry timestamp
}

// Message is the payload of a "message" entry.
type Message struct {
	Role         string          `json:"role"`
	Model        string          `json:"model"`
	Provider     string          `json:"provider"`
	Content      json.RawMessage `json:"content"`
	Usage        *Usage          `json:"usage"`
	StopReason   string          `json:"stopReason"`
	ErrorMessage string          `json:"errorMessage"`
	ToolName     string          `json:"toolName"`
	IsError      bool            `json:"isError"`
}

// Usage are the raw token counts Pi logs per assistant turn.
type Usage struct {
	Input        int   `json:"input"`
	Output       int   `json:"output"`
	CacheRead    int   `json:"cacheRead"`
	CacheWrite   int   `json:"cacheWrite"`
	CacheWrite1h int   `json:"cacheWrite1h"`
	Reasoning    int   `json:"reasoning"`
	TotalTokens  int   `json:"totalTokens"`
	Cost         *Cost `json:"cost"`
}

// Cost is Pi's own client-side cost computation.
type Cost struct {
	Input      float64 `json:"input"`
	Output     float64 `json:"output"`
	CacheRead  float64 `json:"cacheRead"`
	CacheWrite float64 `json:"cacheWrite"`
	Total      float64 `json:"total"`
}

// SessionMeta is the root session entry's identifying fields.
type SessionMeta struct {
	ID        *string `json:"id,omitempty"`
	Cwd       *string `json:"cwd,omitempty"`
	Timestamp *string `json:"timestamp,omitempty"`
	Version   *int    `json:"version,omitempty"`
}

// --- analysis result ---------------------------------------------------------

// Turn is one assistant turn on the active branch.
type Turn struct {
	Turn         int        `json:"turn"`
	TS           *time.Time `json:"ts"`
	Model        string     `json:"model"`
	Provider     string     `json:"provider"`
	FreshInput   int        `json:"fresh_input"`
	CacheRead    int        `json:"cache_read"`
	CacheWrite   int        `json:"cache_write"`
	TotalCtx     int        `json:"total_ctx"`
	Output       int        `json:"output"`
	Reasoning    int        `json:"reasoning"`
	Cost         float64    `json:"cost"`
	PICost       float64    `json:"pi_cost"`
	PricingRule  *string    `json:"pricing_rule"`
	CostOutput   float64    `json:"cost_output"`
	Trigger      string     `json:"trigger"`
	Tools        []string   `json:"tools"`
	IsError      bool       `json:"is_error"`
	ErrorMessage *string    `json:"error_msg"`
	RollingTPM   int64      `json:"rolling_tpm"`
	RollingRPM   int        `json:"rolling_rpm"`

	// rates is kept for the uncached-cost counterfactual and is never emitted.
	hasRates bool
	rates    pricedRates
}

type pricedRates struct {
	Input      float64
	Output     float64
	CacheRead  float64
	CacheWrite float64
}

// Tokens aggregates raw token counts.
type Tokens struct {
	FreshInput   int     `json:"fresh_input"`
	CacheRead    int     `json:"cache_read"`
	CacheWrite   int     `json:"cache_write"`
	Output       int     `json:"output"`
	Reasoning    int     `json:"reasoning"`
	ReasoningPct float64 `json:"reasoning_pct"`
	Total        int     `json:"total"`
	HitRatePct   float64 `json:"hit_rate_pct"`
}

// CostSummary aggregates money metrics.
type CostSummary struct {
	Total           float64       `json:"total"`
	PIReportedTotal float64       `json:"pi_reported_total"`
	Repriced        bool          `json:"repriced"`
	FreshInput      float64       `json:"fresh_input"`
	CacheRead       float64       `json:"cache_read"`
	Output          float64       `json:"output"`
	WithoutCache    float64       `json:"without_cache"`
	Savings         float64       `json:"savings"`
	SavingsPct      float64       `json:"savings_pct"`
	ByRule          OrderedFloats `json:"by_rule"`
}

// PricingMeta describes the active pricing book in a serializable form.
type PricingMeta struct {
	Enabled  bool    `json:"enabled"`
	File     *string `json:"file,omitempty"`
	Timezone *string `json:"timezone,omitempty"`
	Currency *string `json:"currency,omitempty"`
	Source   *string `json:"source,omitempty"`
}

// APIError is a provider error observed during a turn.
type APIError struct {
	Turn       int        `json:"turn"`
	TS         *time.Time `json:"ts"`
	TotalCtx   int        `json:"total_ctx"`
	RollingTPM int64      `json:"rolling_tpm"`
	RollingRPM int        `json:"rolling_rpm"`
	Snippet    string     `json:"snippet"`
	RawError   string     `json:"raw_error"`
}

// Velocity captures rate-limit and throughput metrics.
type Velocity struct {
	PeakTPM60s  int64      `json:"peak_tpm_60s"`
	PeakTPMTurn int        `json:"peak_tpm_turn"`
	PeakRPM60s  int        `json:"peak_rpm_60s"`
	APIErrors   []APIError `json:"api_errors"`
}

// CacheEvent is an idle expiration, prefix bust or server eviction.
type CacheEvent struct {
	Turn       int     `json:"turn"`
	TotalCtx   int     `json:"total_ctx"`
	FreshInput int     `json:"fresh_input"`
	Cost       float64 `json:"cost"`
	TimeGapSec float64 `json:"time_gap_sec"`
	Reason     string  `json:"reason,omitempty"`
}

// ModelSwitch records an in-flight model change that re-seeded the cache.
type ModelSwitch struct {
	Turn       int     `json:"turn"`
	FromModel  string  `json:"from_model"`
	ToModel    string  `json:"to_model"`
	FreshInput int     `json:"fresh_input"`
	Cost       float64 `json:"cost"`
	TotalCtx   int     `json:"total_ctx"`
}

// CustomNode is a durable custom entry on the active branch.
type CustomNode struct {
	ID         string `json:"id"`
	CustomType string `json:"customType"`
	Timestamp  string `json:"timestamp"`
}

// CacheHealth groups cache-integrity findings.
type CacheHealth struct {
	DurableCustomNodes int            `json:"durable_custom_nodes"`
	CustomNodesDetails []CustomNode   `json:"custom_nodes_details"`
	CacheBusts         []*CacheEvent  `json:"cache_busts"`
	PrefixBusts        []*CacheEvent  `json:"prefix_busts"`
	ServerEvictions    []*CacheEvent  `json:"server_evictions"`
	IdleExpirations    []*CacheEvent  `json:"idle_expirations"`
	ModelSwitches      []*ModelSwitch `json:"model_switches"`
}

// ToolError is a failed tool invocation.
type ToolError struct {
	Tool    string `json:"tool"`
	Snippet string `json:"snippet"`
}

// Tools aggregates tool usage and failures.
type Tools struct {
	TotalCalls   int         `json:"total_calls"`
	ByName       OrderedInts `json:"by_name"`
	ErrorCount   int         `json:"error_count"`
	ErrorRatePct float64     `json:"error_rate_pct"`
	Errors       []ToolError `json:"errors"`
}

// UserHabits captures interaction pacing.
type UserHabits struct {
	AvgPromptLen    float64 `json:"avg_prompt_len"`
	ThinkTimesCount int     `json:"think_times_count"`
	AvgThinkSec     float64 `json:"avg_think_sec"`
	MedianThinkSec  float64 `json:"median_think_sec"`
	MaxThinkSec     float64 `json:"max_think_sec"`
}

// SessionAnalysis is the complete single-session result.
type SessionAnalysis struct {
	Meta                SessionMeta   `json:"meta"`
	FirstTS             *time.Time    `json:"first_ts"`
	LastTS              *time.Time    `json:"last_ts"`
	WallDurationSec     float64       `json:"wall_duration_sec"`
	TotalRawEntries     int           `json:"total_raw_entries"`
	ActiveBranchEntries int           `json:"active_branch_entries"`
	AbandonedEntries    int           `json:"abandoned_entries"`
	Models              OrderedInts   `json:"models"`
	AssistantTurnsCount int           `json:"assistant_turns_count"`
	UserPromptsCount    int           `json:"user_prompts_count"`
	AutonomyRatio       float64       `json:"autonomy_ratio"`
	Tokens              Tokens        `json:"tokens"`
	Cost                CostSummary   `json:"cost"`
	Pricing             PricingMeta   `json:"pricing"`
	CostByModel         OrderedFloats `json:"cost_by_model"`
	Velocity            Velocity      `json:"velocity"`
	CacheHealth         CacheHealth   `json:"cache_health"`
	Tools               Tools         `json:"tools"`
	UserHabits          UserHabits    `json:"user_habits"`
	TopSpikes           []*Turn       `json:"top_spikes"`
	RecentTurns         []*Turn       `json:"recent_turns"`

	// AssistantTurns is the full stream; only emitted as "turns" on demand.
	AssistantTurns []*Turn  `json:"-"`
	Turns          *[]*Turn `json:"turns,omitempty"`
}
