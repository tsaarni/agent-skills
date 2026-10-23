package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// V2 reader: ~/.kiro/sessions/cli/<uuid>.{json,jsonl}
//
// This file, its test and its fixtures are the only V2-specific code. To drop
// V2 support, delete them and the newV2Source line in source.go.

type v2Source struct{ dir string }

func newV2Source(root string) *v2Source { return &v2Source{dir: filepath.Join(root, "cli")} }

func (s *v2Source) Engine() string { return "v2" }

type v2JSON struct {
	SessionID            string `json:"session_id"`
	Cwd                  string `json:"cwd"`
	CreatedAt            string `json:"created_at"`
	UpdatedAt            string `json:"updated_at"`
	Title                string `json:"title"`
	ParentSessionID      string `json:"parent_session_id"`
	SessionCreatedReason string `json:"session_created_reason"`
	SessionState         struct {
		AgentName            string `json:"agent_name"`
		ConversationMetadata struct {
			UserTurnMetadatas []v2TurnMeta `json:"user_turn_metadatas"`
		} `json:"conversation_metadata"`
	} `json:"session_state"`
}

type v2TurnMeta struct {
	MessageIDs   []string `json:"message_ids"`
	TurnDuration struct {
		Secs  int64 `json:"secs"`
		Nanos int64 `json:"nanos"`
	} `json:"turn_duration"`
	EndTimestamp  string `json:"end_timestamp"`
	Model         string `json:"model"`
	MeteringUsage []struct {
		Value float64 `json:"value"`
	} `json:"metering_usage"`
}

func (s *v2Source) readJSON(path string) (*v2JSON, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var j v2JSON
	if err := json.Unmarshal(data, &j); err != nil {
		return nil, err
	}
	return &j, nil
}

func (s *v2Source) List(f Filter) ([]SessionHeader, error) {
	files, err := filepath.Glob(filepath.Join(s.dir, "*.json"))
	if err != nil {
		return nil, err
	}
	var out []SessionHeader
	for _, p := range files {
		j, err := s.readJSON(p)
		if err != nil || j.SessionID == "" {
			continue
		}
		h := SessionHeader{
			ID:        j.SessionID,
			Engine:    "v2",
			Workspace: j.Cwd,
			Title:     j.Title,
			Agent:     j.SessionState.AgentName,
			Created:   parseTime(j.CreatedAt),
			Updated:   parseTime(j.UpdatedAt),
			Turns:     len(j.SessionState.ConversationMetadata.UserTurnMetadatas),
			Path:      p,
			ParentID:  j.ParentSessionID,
			Reason:    j.SessionCreatedReason,
		}
		// Turn metadata is discarded by /clear and never written for subagent
		// sessions; the log may still hold turns.
		if h.Turns == 0 {
			if st, err := os.Stat(v2LogPath(p)); err == nil && st.Size() > 0 {
				h.Turns = -1
			}
		}
		if f.matchesHeader(h) {
			out = append(out, h)
		}
	}
	return out, nil
}

func v2LogPath(jsonPath string) string { return strings.TrimSuffix(jsonPath, ".json") + ".jsonl" }

// v2Msg is one message of the .jsonl log.
type v2Msg struct {
	kind  string // Prompt | AssistantMessage | ToolResults
	text  []string
	tools []*ToolCall
	model string // AssistantMessage: model ID when the message carries one
}

// v2LogTurn is a turn as seen in the log: a user prompt and the messages that
// follow it until the next user prompt or /clear.
type v2LogTurn struct{ ids []string }

type v2Line struct {
	Kind string          `json:"kind"`
	Data json.RawMessage `json:"data"`
}

type v2Content struct {
	Kind string          `json:"kind"`
	Data json.RawMessage `json:"data"`
}

type v2ToolUse struct {
	ToolUseID string                     `json:"toolUseId"`
	Name      string                     `json:"name"`
	Input     map[string]json.RawMessage `json:"input"`
}

type v2ToolResult struct {
	ToolUseID string          `json:"toolUseId"`
	Status    string          `json:"status"`
	Content   json.RawMessage `json:"content"`
}

func (s *v2Source) Load(h SessionHeader, opt LoadOpts) (*Session, error) {
	j, err := s.readJSON(h.Path)
	if err != nil {
		return nil, err
	}
	sess := &Session{
		ID: h.ID, Engine: "v2", Workspace: h.Workspace, Title: h.Title, Agent: h.Agent,
		Created: h.Created, Updated: h.Updated,
	}
	metas := j.SessionState.ConversationMetadata.UserTurnMetadatas

	msgs := map[string]*v2Msg{}
	toolByID := map[string]*ToolCall{}
	promptTime := map[string]time.Time{}
	var logTurns []*v2LogTurn
	var cur *v2LogTurn
	if _, err := os.Stat(v2LogPath(h.Path)); err == nil {
		err := forEachLine(v2LogPath(h.Path), func(line []byte) {
			var l v2Line
			if json.Unmarshal(line, &l) != nil {
				return // half-written line
			}
			switch l.Kind {
			case "Clear":
				cur = nil
			case "Prompt":
				id := parseV2Prompt(l.Data, msgs, promptTime)
				if id == "" {
					return
				}
				// A prompt without text (e.g. a bare tool result) continues the turn.
				if cur == nil || len(msgs[id].text) > 0 {
					cur = &v2LogTurn{}
					logTurns = append(logTurns, cur)
				}
				cur.ids = append(cur.ids, id)
			case "AssistantMessage":
				if id := parseV2Assistant(l.Data, msgs, toolByID); id != "" && cur != nil {
					cur.ids = append(cur.ids, id)
				}
			case "ToolResults":
				parseV2Results(l.Data, toolByID, opt)
			}
		})
		if err != nil {
			return nil, err
		}
	}

	// Turn metadata (credits, model, duration) covers only the turns since the
	// last /clear, and none at all in subagent sessions. Turns are taken from
	// the log in order; those with metadata get it, the rest are unmetered.
	metaOf := map[string]int{}
	for i, m := range metas {
		for _, id := range m.MessageIDs {
			metaOf[id] = i
		}
	}
	emitted := make([]bool, len(metas))
	var turns []Turn
	var firstIDs []string // first message ID per turn, for rewind de-duplication
	lastModel := ""
	for _, lt := range logTurns {
		mi := -1
		for _, id := range lt.ids {
			if i, ok := metaOf[id]; ok {
				mi = i
				break
			}
		}
		var t Turn
		switch {
		case mi >= 0 && emitted[mi]:
			continue // second user prompt within one metered turn
		case mi >= 0:
			emitted[mi] = true
			t = v2MetaTurn(metas[mi], msgs, promptTime)
		default:
			t = v2BuildTurn(lt.ids, msgs, promptTime)
			if t.Model == "" {
				t.Model = lastModel
			}
			if t.Start.IsZero() && len(turns) > 0 {
				t.Start = turns[len(turns)-1].Start
			}
			t.End = t.Start
		}
		if t.Model != "" {
			lastModel = t.Model
		}
		turns = append(turns, t)
		firstIDs = append(firstIDs, lt.ids[0])
	}
	for i, m := range metas { // metadata whose messages are missing from the log
		if !emitted[i] {
			turns = append(turns, v2MetaTurn(m, msgs, promptTime))
			firstIDs = append(firstIDs, firstOf(m.MessageIDs))
		}
	}

	// A rewound session starts as a copy of its parent's history. Skip the
	// copied turns so they are not counted twice.
	inherited := map[string]bool{}
	if h.Reason == "rewind" && h.ParentID != "" {
		parent := filepath.Join(filepath.Dir(h.Path), h.ParentID+".json")
		if pj, err := s.readJSON(parent); err == nil {
			for _, m := range pj.SessionState.ConversationMetadata.UserTurnMetadatas {
				for _, id := range m.MessageIDs {
					inherited[id] = true
				}
			}
		}
		_ = forEachLine(v2LogPath(parent), func(line []byte) {
			var l v2Line
			if json.Unmarshal(line, &l) == nil && l.Kind == "Prompt" {
				var d struct {
					MessageID string `json:"message_id"`
				}
				if json.Unmarshal(l.Data, &d) == nil {
					inherited[d.MessageID] = true
				}
			}
		})
	}

	for i := range turns {
		t := &turns[i]
		t.Index = i + 1
		if t.Start.IsZero() {
			t.Start = h.Created
			t.End = t.Start.Add(t.Duration)
		}
		if inherited[firstIDs[i]] {
			continue
		}
		sess.Turns = append(sess.Turns, *t)
	}
	return sess, nil
}

func firstOf(ids []string) string {
	if len(ids) == 0 {
		return ""
	}
	return ids[0]
}

// v2MetaTurn builds a turn from its metadata entry.
func v2MetaTurn(m v2TurnMeta, msgs map[string]*v2Msg, promptTime map[string]time.Time) Turn {
	t := v2BuildTurn(m.MessageIDs, msgs, promptTime)
	if m.Model != "" {
		t.Model = m.Model
	}
	for _, u := range m.MeteringUsage {
		t.Credits += u.Value
		t.Metered = true
	}
	t.Duration = time.Duration(m.TurnDuration.Secs)*time.Second + time.Duration(m.TurnDuration.Nanos)
	t.End = parseTime(m.EndTimestamp)
	if !t.End.IsZero() && t.Duration > 0 {
		t.Start = t.End.Add(-t.Duration)
	}
	if t.End.IsZero() && !t.Start.IsZero() {
		t.End = t.Start.Add(t.Duration)
	}
	return t
}

// v2BuildTurn fills prompt, response, tools, start time and model from the
// logged messages of a turn.
func v2BuildTurn(ids []string, msgs map[string]*v2Msg, promptTime map[string]time.Time) Turn {
	var t Turn
	var prompts, replies []string
	for _, id := range ids {
		msg := msgs[id]
		if msg == nil {
			continue
		}
		switch msg.kind {
		case "Prompt":
			prompts = append(prompts, msg.text...)
			if t.Start.IsZero() {
				t.Start = promptTime[id]
			}
		case "AssistantMessage":
			replies = append(replies, msg.text...)
			if msg.model != "" {
				t.Model = cleanModel(msg.model)
			}
			for _, tc := range msg.tools {
				if tc.Status == statusPending {
					tc.Status = StatusFailed
				}
				t.Tools = append(t.Tools, *tc)
			}
		}
	}
	t.Prompt = strings.TrimSpace(strings.Join(prompts, "\n\n"))
	t.Response = strings.TrimSpace(strings.Join(replies, "\n\n"))
	return t
}

func parseV2Prompt(data json.RawMessage, msgs map[string]*v2Msg, times map[string]time.Time) string {
	var d struct {
		MessageID string      `json:"message_id"`
		Content   []v2Content `json:"content"`
		Meta      struct {
			Timestamp int64 `json:"timestamp"`
		} `json:"meta"`
	}
	if json.Unmarshal(data, &d) != nil || d.MessageID == "" {
		return ""
	}
	m := &v2Msg{kind: "Prompt"}
	for _, c := range d.Content {
		if c.Kind == "text" {
			if t := textOf(c.Data); strings.TrimSpace(t) != "" {
				m.text = append(m.text, t)
			}
		}
	}
	msgs[d.MessageID] = m
	if d.Meta.Timestamp > 0 {
		times[d.MessageID] = time.Unix(d.Meta.Timestamp, 0).Local()
	}
	return d.MessageID
}

func parseV2Assistant(data json.RawMessage, msgs map[string]*v2Msg, toolByID map[string]*ToolCall) string {
	var d struct {
		MessageID string      `json:"message_id"`
		Content   []v2Content `json:"content"`
	}
	if json.Unmarshal(data, &d) != nil || d.MessageID == "" {
		return ""
	}
	m := &v2Msg{kind: "AssistantMessage"}
	for _, c := range d.Content {
		switch c.Kind {
		case "text":
			if t := strings.TrimSpace(textOf(c.Data)); t != "" {
				m.text = append(m.text, t)
			}
		case "toolUse":
			var tu v2ToolUse
			if json.Unmarshal(c.Data, &tu) != nil {
				continue
			}
			tc := &ToolCall{Name: tu.Name, Status: statusPending}
			fillV2Call(tc, &tu)
			m.tools = append(m.tools, tc)
			if tu.ToolUseID != "" {
				toolByID[tu.ToolUseID] = tc
			}
		default: // e.g. reasoning blocks, which name the model
			var r struct {
				ModelID string `json:"modelId"`
			}
			if json.Unmarshal(c.Data, &r) == nil && r.ModelID != "" {
				m.model = r.ModelID
			}
		}
	}
	msgs[d.MessageID] = m
	return d.MessageID
}

func fillV2Call(tc *ToolCall, tu *v2ToolUse) {
	switch tu.Name {
	case "shell":
		tc.Kind = KindShell
		tc.Command = argString(tu.Input, "command")
		tc.Cwd = argString(tu.Input, "working_dir")
	case "read":
		tc.Kind = KindRead
		tc.Paths = argOperationPaths(tu.Input)
		if p := argString(tu.Input, "path"); p != "" {
			tc.Paths = append(tc.Paths, p)
		}
	case "write":
		tc.Kind = KindWrite
		if p := argString(tu.Input, "path"); p != "" {
			tc.Paths = []string{p}
		}
	case "grep", "glob", "code":
		tc.Kind = KindSearch
	case "subagent":
		tc.Kind = KindSubagent
	default:
		tc.Kind = KindOther
	}
}

func parseV2Results(data json.RawMessage, toolByID map[string]*ToolCall, opt LoadOpts) {
	var d struct {
		Content []v2Content `json:"content"`
		Results map[string]struct {
			Tool struct {
				Kind struct {
					Mcp json.RawMessage `json:"Mcp"`
				} `json:"kind"`
			} `json:"tool"`
			Result json.RawMessage `json:"result"`
		} `json:"results"`
	}
	if json.Unmarshal(data, &d) != nil {
		return
	}
	for _, c := range d.Content {
		if c.Kind != "toolResult" {
			continue
		}
		var tr v2ToolResult
		if json.Unmarshal(c.Data, &tr) != nil {
			continue
		}
		tc := toolByID[tr.ToolUseID]
		if tc == nil {
			continue
		}
		res, hasRes := d.Results[tr.ToolUseID]
		if hasRes && len(res.Tool.Kind.Mcp) > 0 {
			tc.Kind = KindMCP
		}
		if hasRes && len(res.Result) > 0 {
			tc.Status = v2Status(res.Result, tc.Kind == KindShell)
		} else if tr.Status == "success" {
			tc.Status = StatusOK
		} else {
			tc.Status = StatusFailed
		}
		if opt.wantOutput(tc) {
			tc.Output = v2Output(tr.Content, tc.Kind == KindShell)
		}
	}
}

var v2ExitOK = []byte(`"exit status: 0"`)

// v2Status classifies a result object without decoding it fully.
func v2Status(result json.RawMessage, shell bool) string {
	r := bytes.TrimSpace(result)
	switch {
	case bytes.Contains(r[:min(len(r), 16)], []byte("Cancelled")):
		return StatusDenied
	case bytes.HasPrefix(r, []byte(`{"Success"`)):
		if shell {
			if i := bytes.Index(r, []byte(`"exit_status"`)); i >= 0 {
				rest := r[i+len(`"exit_status"`):]
				rest = bytes.TrimLeft(rest, ": ")
				if !bytes.HasPrefix(rest, v2ExitOK) {
					return StatusFailed
				}
			}
		}
		return StatusOK
	case bytes.HasPrefix(r, []byte(`{"Error"`)):
		head := r[:min(len(r), 200)]
		if bytes.Contains(head, []byte("denied")) || bytes.Contains(head, []byte("Cancelled")) {
			return StatusDenied
		}
		return StatusFailed
	}
	return StatusFailed
}

// v2Output renders the content items of a tool result as text.
func v2Output(raw json.RawMessage, shell bool) string {
	var items []v2Content
	if json.Unmarshal(raw, &items) != nil {
		return ""
	}
	var parts []string
	for _, it := range items {
		switch it.Kind {
		case "text":
			parts = append(parts, textOf(it.Data))
		case "json":
			if shell {
				var sh struct {
					Stdout string `json:"stdout"`
					Stderr string `json:"stderr"`
				}
				if json.Unmarshal(it.Data, &sh) == nil && (sh.Stdout != "" || sh.Stderr != "") {
					parts = append(parts, sh.Stdout+sh.Stderr)
					continue
				}
			}
			parts = append(parts, string(it.Data))
		}
	}
	return strings.Join(parts, "\n")
}
