package main

import (
	"strings"
	"time"
)

// Normalized model. Both engine readers produce these types, and the commands
// only ever see these.

// Tool kinds.
const (
	KindShell    = "shell"
	KindRead     = "read"
	KindWrite    = "write"
	KindSearch   = "search"
	KindMCP      = "mcp"
	KindSubagent = "subagent"
	KindOther    = "other"
)

// Tool statuses.
const (
	StatusOK      = "ok"
	StatusFailed  = "failed"
	StatusDenied  = "denied"
	statusPending = "pending" // internal: call seen, result not (yet) seen
)

type Session struct {
	ID        string
	Engine    string
	Workspace string
	Title     string
	Agent     string
	Created   time.Time
	Updated   time.Time
	Turns     []Turn
}

type Turn struct {
	Index    int // 1-based position within the session
	Start    time.Time
	End      time.Time
	Duration time.Duration
	Model    string
	Credits  float64
	// Metered is false when the engine logged no usage for the turn, so zero
	// credits means "unknown" rather than "free".
	Metered  bool
	Prompt   string
	Response string
	Tools    []ToolCall
}

type ToolCall struct {
	Name    string // raw tool name
	Kind    string // see Kind* constants
	Command string // shell only
	Cwd     string
	Paths   []string // files read or written
	Status  string   // ok | failed | denied
	Output  string   // filled only when requested
}

// SessionHeader is the cheap, metadata-only view of a session.
type SessionHeader struct {
	ID        string
	Engine    string
	Workspace string
	Title     string
	Agent     string
	Created   time.Time
	Updated   time.Time
	Turns     int // number of turns when known cheaply, otherwise -1
	Path      string

	// Engine-specific.
	Model    string // v3: session-level model, used as fallback
	ParentID string // v2
	Reason   string // v2: session_created_reason
}

// LoadOpts controls how much of a session is materialized.
type LoadOpts struct {
	// ToolOutput keeps the output text of every tool call.
	ToolOutput bool
	// OutputFor keeps the output text only for tool calls it returns true for.
	OutputFor func(tc *ToolCall) bool
}

func (o LoadOpts) wantOutput(tc *ToolCall) bool {
	if o.ToolOutput {
		return true
	}
	return o.OutputFor != nil && o.OutputFor(tc)
}

// shortID returns the first 8 characters of a session ID.
func shortID(id string) string {
	if len(id) > 8 {
		return id[:8]
	}
	return id
}

// cleanModel strips provider prefixes such as "qdev::".
func cleanModel(m string) string {
	if i := strings.LastIndex(m, "::"); i >= 0 {
		m = m[i+2:]
	}
	return m
}

// title returns the session title, falling back to the first prompt.
func (s *Session) DisplayTitle() string {
	t := strings.TrimSpace(s.Title)
	if t != "" && !strings.EqualFold(t, "New Session") {
		return t
	}
	for _, turn := range s.Turns {
		if p := strings.TrimSpace(turn.Prompt); p != "" {
			return p
		}
	}
	if t != "" {
		return t
	}
	return "(untitled)"
}

// Credits sums the credits of the session's turns.
func (s *Session) TotalCredits() float64 {
	var c float64
	for _, t := range s.Turns {
		c += t.Credits
	}
	return c
}
