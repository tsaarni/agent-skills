package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// V3 reader: ~/.kiro/sessions/<workspace-hash>/sess_<uuid>/{session.json,messages.jsonl}

type v3Source struct{ root string }

func newV3Source(root string) *v3Source { return &v3Source{root: root} }

func (s *v3Source) Engine() string { return "v3" }

type v3SessionJSON struct {
	ID             string   `json:"id"`
	Title          string   `json:"title"`
	AgentMode      string   `json:"agentMode"`
	WorkspacePaths []string `json:"workspacePaths"`
	RootPaths      []string `json:"rootPaths"`
	CreatedAt      string   `json:"createdAt"`
	LastModifiedAt string   `json:"lastModifiedAt"`
	ModelID        string   `json:"modelId"`
}

func (s *v3Source) List(f Filter) ([]SessionHeader, error) {
	dirs, err := filepath.Glob(filepath.Join(s.root, "*", "sess_*", "session.json"))
	if err != nil {
		return nil, err
	}
	var out []SessionHeader
	for _, p := range dirs {
		dir := filepath.Dir(p)
		// "cli" holds the V2 store, not workspace hashes.
		if filepath.Base(filepath.Dir(dir)) == "cli" {
			continue
		}
		h, err := readV3Header(p)
		if err != nil {
			continue
		}
		if f.matchesHeader(h) {
			out = append(out, h)
		}
	}
	return out, nil
}

func readV3Header(sessionJSON string) (SessionHeader, error) {
	data, err := os.ReadFile(sessionJSON)
	if err != nil {
		return SessionHeader{}, err
	}
	var sj v3SessionJSON
	if err := json.Unmarshal(data, &sj); err != nil {
		return SessionHeader{}, err
	}
	ws := ""
	if len(sj.WorkspacePaths) > 0 {
		ws = sj.WorkspacePaths[0]
	} else if len(sj.RootPaths) > 0 {
		ws = sj.RootPaths[0]
	}
	dir := filepath.Dir(sessionJSON)
	id := sj.ID
	if id == "" {
		id = filepath.Base(dir)
	}
	h := SessionHeader{
		ID:        strings.TrimPrefix(id, "sess_"),
		Engine:    "v3",
		Workspace: ws,
		Title:     sj.Title,
		Agent:     sj.AgentMode,
		Created:   parseTime(sj.CreatedAt),
		Updated:   parseTime(sj.LastModifiedAt),
		Turns:     -1,
		Path:      dir,
		Model:     cleanModel(sj.ModelID),
	}
	// Sessions without messages never ran a turn.
	if _, err := os.Stat(filepath.Join(dir, "messages.jsonl")); err != nil {
		h.Turns = 0
	}
	return h, nil
}

type v3Event struct {
	Timestamp string    `json:"timestamp"`
	Payload   v3Payload `json:"payload"`
}

type v3Payload struct {
	Type             string          `json:"type"`
	Content          json.RawMessage `json:"content"`
	OperationType    string          `json:"operationType"`
	ReasoningModelID string          `json:"reasoningModelId"`
	UserMessageTag   string          `json:"userMessageTag"`
	Meta             struct {
		Kiro struct {
			UserMessageTag string `json:"userMessageTag"`
		} `json:"kiro"`
	} `json:"_meta"`

	ToolCallID string          `json:"toolCallId"`
	ToolName   string          `json:"toolName"`
	ActionType string          `json:"actionType"`
	Kind       string          `json:"kind"`
	Status     string          `json:"status"`
	FilePath   string          `json:"filePath"`
	Args       json.RawMessage `json:"args"`
	Success    *bool           `json:"success"`

	PromptTurnSummaries []struct {
		Usage float64 `json:"usage"`
	} `json:"promptTurnSummaries"`
	ElapsedTime float64 `json:"elapsedTime"`
	StopReason  string  `json:"stopReason"`
}

// v3Turn is a turn under construction.
type v3Turn struct {
	Turn
	say   []string
	tools []*ToolCall
	byID  map[string]*ToolCall
	last  time.Time
	timed bool // duration came from usage_summary
}

var v3SavedOutput = regexp.MustCompile(`saved to a file under '([^']+)'`)

const maxSavedOutput = 32 << 20

func (s *v3Source) Load(h SessionHeader, opt LoadOpts) (*Session, error) {
	sess := &Session{
		ID: h.ID, Engine: "v3", Workspace: h.Workspace, Title: h.Title, Agent: h.Agent,
		Created: h.Created, Updated: h.Updated,
	}
	msgPath := filepath.Join(h.Path, "messages.jsonl")
	if _, err := os.Stat(msgPath); err != nil {
		return sess, nil
	}

	var turns []*v3Turn
	var cur *v3Turn
	pendingUser := map[string]string{}
	lastUser := ""
	lastModel := h.Model

	err := forEachLine(msgPath, func(line []byte) {
		var ev v3Event
		if json.Unmarshal(line, &ev) != nil {
			return // half-written line or unknown shape
		}
		p := &ev.Payload
		ts := parseTime(ev.Timestamp)
		if cur != nil && ts.After(cur.last) {
			cur.last = ts
		}
		switch p.Type {
		case "user":
			text := textOf(p.Content)
			lastUser = text
			if tag := p.Meta.Kiro.UserMessageTag; tag != "" {
				pendingUser[tag] = text
			}
		case "turn_start":
			prompt, ok := pendingUser[p.UserMessageTag]
			if !ok {
				prompt = lastUser
			}
			cur = &v3Turn{byID: map[string]*ToolCall{}, last: ts}
			cur.Index = len(turns) + 1
			cur.Start = ts
			cur.Prompt = prompt
			cur.Model = lastModel
			turns = append(turns, cur)
		case "assistant":
			if cur == nil {
				return
			}
			switch p.OperationType {
			case "Reasoning":
				if p.ReasoningModelID != "" {
					cur.Model = cleanModel(p.ReasoningModelID)
					lastModel = cur.Model
				}
			case "Say":
				if t := textOf(p.Content); t != "" {
					cur.say = append(cur.say, t)
				}
			}
		case "tool_call":
			if cur == nil {
				return
			}
			tc := cur.byID[p.ToolCallID]
			if tc == nil {
				tc = &ToolCall{}
				cur.tools = append(cur.tools, tc)
				if p.ToolCallID != "" {
					cur.byID[p.ToolCallID] = tc
				}
			}
			fillV3Call(tc, p)
		case "tool_result":
			if cur == nil {
				return
			}
			tc := cur.byID[p.ToolCallID]
			if tc == nil {
				return
			}
			applyV3Result(tc, p, opt)
		case "usage_summary":
			if cur == nil {
				return
			}
			for _, sm := range p.PromptTurnSummaries {
				cur.Credits += sm.Usage
				cur.Metered = true
			}
			if p.ElapsedTime > 0 {
				cur.Duration = time.Duration(p.ElapsedTime * float64(time.Millisecond))
				cur.timed = true
			}
		case "turn_end":
			if cur == nil {
				return
			}
			cur.End = ts
		}
	})
	if err != nil {
		return nil, err
	}

	for _, t := range turns {
		if t.End.IsZero() {
			t.End = t.last // turn still running or truncated log
		}
		if !t.timed {
			t.Duration = t.End.Sub(t.Start)
		}
		t.Response = strings.TrimSpace(strings.Join(t.say, "\n\n"))
		for _, tc := range t.tools {
			if tc.Status == statusPending {
				tc.Status = StatusFailed
			}
			t.Tools = append(t.Tools, *tc)
		}
		sess.Turns = append(sess.Turns, t.Turn)
	}
	return sess, nil
}

func fillV3Call(tc *ToolCall, p *v3Payload) {
	tc.Name = p.ToolName
	args := parseArgs(p.Args)

	switch p.Kind {
	case "execute":
		tc.Kind = KindShell
	case "read":
		tc.Kind = KindRead
	case "edit":
		tc.Kind = KindWrite
	case "search":
		tc.Kind = KindSearch
	default:
		a := strings.ToLower(p.ActionType + " " + p.ToolName)
		switch {
		case strings.Contains(a, "subagent"):
			tc.Kind = KindSubagent
		case strings.HasPrefix(p.ActionType, "mcp_") || strings.HasPrefix(p.ToolName, "mcp_") || args["tool_id"] != nil:
			tc.Kind = KindMCP
		default:
			tc.Kind = KindOther
		}
	}

	switch tc.Kind {
	case KindShell:
		tc.Command = argString(args, "command")
		tc.Cwd = argString(args, "cwd")
	case KindRead, KindWrite:
		if p.FilePath != "" {
			tc.Paths = append(tc.Paths, p.FilePath)
		}
		for _, k := range []string{"path", "file_path", "filePath"} {
			if v := argString(args, k); v != "" && !contains(tc.Paths, v) {
				tc.Paths = append(tc.Paths, v)
			}
		}
		for _, v := range argStrings(args, "paths") {
			if !contains(tc.Paths, v) {
				tc.Paths = append(tc.Paths, v)
			}
		}
		for _, v := range argOperationPaths(args) {
			if !contains(tc.Paths, v) {
				tc.Paths = append(tc.Paths, v)
			}
		}
	}

	switch p.Status {
	case "completed":
		tc.Status = StatusOK
	case "denied":
		tc.Status = StatusDenied
	case "failed":
		tc.Status = StatusFailed
	default: // approved: outcome is decided by the result
		if tc.Status == "" {
			tc.Status = statusPending
		}
	}
}

func applyV3Result(tc *ToolCall, p *v3Payload, opt LoadOpts) {
	if tc.Status != StatusDenied {
		if p.Success != nil && !*p.Success {
			tc.Status = StatusFailed
		} else if tc.Status == statusPending {
			tc.Status = StatusOK
		}
	}
	if !opt.wantOutput(tc) {
		return
	}
	out := textOf(p.Content)
	if m := v3SavedOutput.FindStringSubmatch(out); m != nil {
		if full := readCapped(m[1], maxSavedOutput); full != "" {
			out = full
		}
	}
	if tc.Kind == KindShell {
		out = strings.TrimPrefix(out, "Output:\n")
	}
	tc.Output = out
}

func readCapped(path string, max int64) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	buf := make([]byte, max)
	n, _ := f.Read(buf)
	return strings.TrimPrefix(string(buf[:n]), "Output:\n")
}

// parseArgs decodes tool arguments, which are either a JSON object or a JSON
// string containing an object.
func parseArgs(raw json.RawMessage) map[string]json.RawMessage {
	if len(raw) == 0 {
		return nil
	}
	if raw[0] == '"' {
		var s string
		if json.Unmarshal(raw, &s) != nil {
			return nil
		}
		raw = json.RawMessage(s)
	}
	var m map[string]json.RawMessage
	if json.Unmarshal(raw, &m) != nil {
		return nil
	}
	return m
}

func argString(m map[string]json.RawMessage, key string) string {
	raw, ok := m[key]
	if !ok || len(raw) == 0 || raw[0] != '"' {
		return ""
	}
	var s string
	_ = json.Unmarshal(raw, &s)
	return s
}

func argStrings(m map[string]json.RawMessage, key string) []string {
	raw, ok := m[key]
	if !ok {
		return nil
	}
	var out []string
	_ = json.Unmarshal(raw, &out)
	return out
}

// argOperationPaths extracts paths from "operations": [{"path": ...}].
func argOperationPaths(m map[string]json.RawMessage) []string {
	raw, ok := m["operations"]
	if !ok {
		return nil
	}
	var ops []struct {
		Path string `json:"path"`
		Mode string `json:"mode"`
	}
	if json.Unmarshal(raw, &ops) != nil {
		return nil
	}
	var out []string
	for _, o := range ops {
		if o.Path != "" && o.Mode != "Directory" {
			out = append(out, o.Path)
		}
	}
	return out
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
