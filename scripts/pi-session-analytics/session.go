package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// expandHome expands a leading "~" into the user's home directory.
func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(path, "~/"))
		}
	}
	return path
}

func sessionsDir() string { return expandHome("~/.pi/agent/sessions") }

func globSessions() []string {
	files, _ := filepath.Glob(filepath.Join(sessionsDir(), "*", "*.jsonl"))
	return files
}

// sortByMtimeDesc orders paths newest-first. Mtimes are read once up front so
// the comparator does not stat repeatedly.
func sortByMtimeDesc(files []string) []string {
	mtimes := make(map[string]float64, len(files))
	for _, f := range files {
		mtimes[f] = mtime(f)
	}
	sort.SliceStable(files, func(i, j int) bool { return mtimes[files[i]] > mtimes[files[j]] })
	return files
}

func mtime(path string) float64 {
	info, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return float64(info.ModTime().UnixNano()) / 1e9
}

// readJSONLines streams a file line by line, skipping blank lines. Long lines
// are handled without an artificial size cap.
func readJSONLines(path string, fn func(line []byte)) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	reader := bufio.NewReaderSize(f, 1<<20)
	for {
		line, err := reader.ReadBytes('\n')
		line = bytes.TrimSpace(line)
		if len(line) > 0 {
			fn(line)
		}
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
	}
}

func readFirstLine(path string) []byte {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	line, _ := bufio.NewReader(f).ReadBytes('\n')
	return bytes.TrimSpace(line)
}

func parseEntry(line []byte) (*Entry, bool) {
	var entry Entry
	if err := json.Unmarshal(line, &entry); err != nil {
		return nil, false
	}
	entry.time = parseTimestamp(entry.Timestamp)
	return &entry, true
}

// loadActiveBranch parses a session JSONL and resolves the active conversation
// branch via parentId pointers.
func loadActiveBranch(path string) ([]*Entry, int, SessionMeta) {
	entries := map[string]*Entry{}
	children := map[string][]string{}
	var order []string
	var lastID string
	var meta SessionMeta
	totalRaw := 0

	_ = readJSONLines(path, func(line []byte) {
		entry, ok := parseEntry(line)
		if !ok {
			return // live file may have a partially written trailing line
		}
		totalRaw++
		if entry.Type == "session" && meta.ID == nil {
			meta = SessionMeta{
				ID:        stringPtr(entry.ID),
				Cwd:       stringPtr(entry.Cwd),
				Timestamp: rawStringPtr(entry.Timestamp),
				Version:   intPtr(entry.Version),
			}
		}
		if entry.ID != "" {
			entries[entry.ID] = entry
			order = append(order, entry.ID)
			lastID = entry.ID
			if entry.ParentID != "" {
				children[entry.ParentID] = append(children[entry.ParentID], entry.ID)
			}
		}
	})

	if len(entries) == 0 {
		return nil, totalRaw, meta
	}

	// Choose the active leaf: the last entry when it is a leaf, otherwise the
	// leaf with the latest timestamp.
	activeLeaf := lastID
	var leaves []string
	for _, id := range order {
		if len(children[id]) == 0 {
			leaves = append(leaves, id)
		}
	}
	_, lastIsLeaf := entries[lastID]
	if lastIsLeaf && len(children[lastID]) == 0 {
		activeLeaf = lastID
	} else if len(leaves) > 0 {
		sort.SliceStable(leaves, func(i, j int) bool { return timeOf(entries[leaves[i]]).After(timeOf(entries[leaves[j]])) })
		activeLeaf = leaves[0]
	}

	var branch []*Entry
	visited := map[string]bool{}
	for curr := activeLeaf; curr != "" && !visited[curr]; {
		entry, ok := entries[curr]
		if !ok {
			break
		}
		visited[curr] = true
		branch = append(branch, entry)
		curr = entry.ParentID
	}
	// Reverse into root-to-leaf order.
	for i, j := 0, len(branch)-1; i < j; i, j = i+1, j-1 {
		branch[i], branch[j] = branch[j], branch[i]
	}
	return branch, totalRaw, meta
}

func timeOf(e *Entry) time.Time {
	if e == nil || e.time == nil {
		return time.Time{}
	}
	return *e.time
}

func stringPtr(s string) *string { return &s }
func intPtr(v int) *int          { return &v }

func rawStringPtr(raw json.RawMessage) *string {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return nil
	}
	var s string
	if err := json.Unmarshal(trimmed, &s); err != nil {
		return nil
	}
	return &s
}

// findLatestSession locates the most recent session for a workspace, or
// globally across all workspaces.
func findLatestSession(cwd string, globalSearch bool) string {
	all := sortByMtimeDesc(globSessions())
	if len(all) == 0 {
		return ""
	}
	globalNewest := all[0]
	if globalSearch {
		return globalNewest
	}
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	safeName := "--" + strings.ReplaceAll(strings.Trim(cwd, "/"), "/", "-") + "--"
	targetDir := filepath.Join(sessionsDir(), safeName)

	var localLatest string
	localFiles, _ := filepath.Glob(filepath.Join(targetDir, "*.jsonl"))
	if len(localFiles) > 0 {
		localLatest = sortByMtimeDesc(localFiles)[0]
	}
	if localLatest == "" {
		for _, f := range all {
			first := readFirstLine(f)
			if len(first) == 0 {
				continue
			}
			var hdr struct {
				Cwd string `json:"cwd"`
			}
			if json.Unmarshal(first, &hdr) == nil && hdr.Cwd == cwd {
				localLatest = f
				break
			}
		}
	}
	if localLatest != "" {
		localM := mtime(localLatest)
		globalM := mtime(globalNewest)
		if globalNewest != localLatest && (globalM-localM) > 3600 {
			localDT := time.Unix(0, int64(localM*1e9))
			globalDT := time.Unix(0, int64(globalM*1e9))
			globalWS := "another workspace"
			first := readFirstLine(globalNewest)
			var hdr struct {
				Cwd string `json:"cwd"`
			}
			if json.Unmarshal(first, &hdr) == nil && hdr.Cwd != "" {
				globalWS = hdr.Cwd
			}
			fmt.Fprintf(os.Stderr,
				"[INFO] Auditing local workspace session from %s.\n"+
					"       A newer session (%s) exists in '%s'.\n"+
					"       Use --global (-g) to audit the newest session across all workspaces.\n\n",
				localDT.Format("2006-01-02 15:04"), globalDT.Format("2006-01-02 15:04"), globalWS)
		}
		return localLatest
	}
	fmt.Fprintf(os.Stderr, "[INFO] No sessions found for current workspace '%s'. Falling back to newest global session.\n\n", cwd)
	return globalNewest
}

// resolveSessionTarget resolves a file path, UUID prefix or recency rank to a
// session file.
func resolveSessionTarget(target, cwd string, globalSearch bool, recentRank int) string {
	all := sortByMtimeDesc(globSessions())
	if len(all) == 0 {
		return ""
	}
	if recentRank > 0 {
		if recentRank <= len(all) {
			return all[recentRank-1]
		}
		return ""
	}
	if target != "" {
		if info, err := os.Stat(target); err == nil && !info.IsDir() {
			return target
		}
	}
	if target != "" && isAllDigits(target) {
		rank := atoiSafe(target)
		if rank >= 1 && rank <= len(all) {
			return all[rank-1]
		}
	}
	if target != "" {
		clean := strings.TrimSpace(target)
		for _, f := range all {
			if strings.Contains(filepath.Base(f), clean) {
				return f
			}
		}
		for _, f := range all {
			first := readFirstLine(f)
			if len(first) == 0 {
				continue
			}
			var hdr struct {
				ID string `json:"id"`
			}
			if json.Unmarshal(first, &hdr) == nil {
				if strings.HasPrefix(hdr.ID, clean) || strings.Contains(hdr.ID, clean) {
					return f
				}
			}
		}
	}
	return findLatestSession(cwd, globalSearch)
}

func isAllDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func atoiSafe(s string) int {
	n := 0
	for _, r := range s {
		n = n*10 + int(r-'0')
	}
	return n
}

// --- session listing ---------------------------------------------------------

type sessionListItem struct {
	Index          int     `json:"index"`
	ID             string  `json:"id"`
	IDShort        string  `json:"id_short"`
	DateTime       string  `json:"datetime"`
	Timestamp      string  `json:"timestamp"`
	Cwd            string  `json:"cwd"`
	WorkspaceShort string  `json:"workspace_short"`
	Model          string  `json:"model"`
	Turns          int     `json:"turns"`
	Cost           float64 `json:"cost"`
	ErrorCount     int     `json:"error_count"`
	Status         string  `json:"status"`
	Path           string  `json:"path"`
}

func listSessions(limit int, workspace string, errorsOnly bool, since, until *time.Time, asJSON bool) {
	dir := sessionsDir()
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		fmt.Printf("No session directory found at %s\n", dir)
		return
	}
	all := globSessions()
	if len(all) == 0 {
		fmt.Printf("No sessions found in %s\n", dir)
		return
	}
	sortByMtimeDesc(all)

	var items []sessionListItem
	home, _ := os.UserHomeDir()

	for _, f := range all {
		m := mtime(f)
		fDT := time.Unix(0, int64(m*1e9))
		if since != nil && fDT.Before(*since) {
			continue
		}
		if until != nil && fDT.After(*until) {
			continue
		}

		var sid, cwd string
		var firstTS *time.Time
		asstTurns, errCount := 0, 0
		var cost float64
		model := "unknown"
		var errTypes []string

		_ = readJSONLines(f, func(line []byte) {
			entry, ok := parseEntry(line)
			if !ok {
				return
			}
			switch entry.Type {
			case "session":
				if sid == "" {
					sid = entry.ID
					cwd = entry.Cwd
					firstTS = parseTimestamp(entry.Timestamp)
				}
			case "message":
				if entry.Message == nil || entry.Message.Role != "assistant" {
					return
				}
				asstTurns++
				if entry.Message.Model != "" {
					model = entry.Message.Model
				}
				if entry.Message.Usage != nil && entry.Message.Usage.Cost != nil {
					cost += entry.Message.Usage.Cost.Total
				}
				if entry.Message.StopReason == "error" {
					errCount++
					e := entry.Message.ErrorMessage
					switch {
					case strings.Contains(e, "429") && !contains(errTypes, "429"):
						errTypes = append(errTypes, "429")
					case strings.Contains(e, "503") && !contains(errTypes, "503"):
						errTypes = append(errTypes, "503")
					case strings.Contains(e, "500") && !contains(errTypes, "500"):
						errTypes = append(errTypes, "500")
					}
				}
			}
		})

		if workspace != "" && (cwd == "" || !strings.Contains(cwd, workspace)) {
			continue
		}
		if errorsOnly && errCount == 0 {
			continue
		}

		baseDT := fDT
		if firstTS != nil {
			baseDT = *firstTS
		}
		dtDisplay := baseDT.Format("2006-01-02 15:04")
		wsDisplay := strings.ReplaceAll(cwdOrUnknown(cwd), home, "~")
		if len(wsDisplay) > 28 {
			wsDisplay = "..." + wsDisplay[len(wsDisplay)-25:]
		}
		statusDisplay := "OK"
		if errCount > 0 {
			statusDisplay = fmt.Sprintf("%d errs", errCount)
		}
		if len(errTypes) > 0 {
			n := len(errTypes)
			if n > 2 {
				n = 2
			}
			statusDisplay += fmt.Sprintf(" (%s)", strings.Join(errTypes[:n], ","))
		}

		id := sid
		if id == "" {
			id = filepath.Base(f)
		}
		items = append(items, sessionListItem{
			Index:          len(items) + 1,
			ID:             id,
			IDShort:        shortID(id),
			DateTime:       dtDisplay,
			Timestamp:      baseDT.Format(time.RFC3339),
			Cwd:            cwdOrUnknown(cwd),
			WorkspaceShort: wsDisplay,
			Model:          model,
			Turns:          asstTurns,
			Cost:           cost,
			ErrorCount:     errCount,
			Status:         statusDisplay,
			Path:           f,
		})
		if limit > 0 && len(items) >= limit {
			break
		}
	}

	if len(items) == 0 {
		fmt.Println("No matching sessions found.")
		return
	}

	if asJSON {
		printJSON(items)
		return
	}

	fmt.Println("\n" + strings.Repeat("=", 98))
	fmt.Println("                           PI SESSIONS NAVIGATOR")
	fmt.Println(strings.Repeat("=", 98))
	fmt.Printf("%-6s %-18s %-28s %-18s %5s   %7s  %-14s  %s\n", "Idx", "Date & Time", "Workspace", "Model", "Turns", "Cost", "Status", "Session ID")
	fmt.Println(strings.Repeat("-", 98))
	for _, s := range items {
		fmt.Printf("[%-2d]   %-18s %-28s %-18s %5d   $%6.2f  %-14s  %s\n",
			s.Index, s.DateTime, s.WorkspaceShort, truncate(s.Model, 17), s.Turns, s.Cost, s.Status, s.IDShort)
	}
	fmt.Println(strings.Repeat("=", 98))
	fmt.Println("Tip: Run 'go run . <Idx>' (e.g. 'go run . 2') or 'go run . <Session ID>' to audit.")
	fmt.Println("")
}

func cwdOrUnknown(cwd string) string {
	if cwd == "" {
		return "unknown"
	}
	return cwd
}

func shortID(id string) string {
	if len(id) > 8 {
		return id[:8]
	}
	return id
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// --- workspace listing -------------------------------------------------------

type workspaceItem struct {
	Cwd                string  `json:"cwd"`
	WorkspaceShort     string  `json:"workspace_short"`
	SessionsCount      int     `json:"sessions_count"`
	LatestMtime        float64 `json:"latest_mtime"`
	LatestActivity     string  `json:"latest_activity"`
	LatestSessionID    string  `json:"latest_session_id"`
	LatestSessionShort string  `json:"latest_session_short"`
}

func listWorkspaces(asJSON bool) {
	dir := sessionsDir()
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		fmt.Printf("No session directory found at %s\n", dir)
		return
	}
	entries, _ := os.ReadDir(dir)
	var workspaces []workspaceItem
	workspaces = []workspaceItem{}
	home, _ := os.UserHomeDir()

	for _, de := range entries {
		if !de.IsDir() {
			continue
		}
		files, _ := filepath.Glob(filepath.Join(dir, de.Name(), "*.jsonl"))
		if len(files) == 0 {
			continue
		}
		newest := sortByMtimeDesc(files)[0]
		m := mtime(newest)
		var cwd, sid string
		first := readFirstLine(newest)
		if len(first) > 0 {
			var hdr struct {
				Cwd string `json:"cwd"`
				ID  string `json:"id"`
			}
			if json.Unmarshal(first, &hdr) == nil {
				cwd, sid = hdr.Cwd, hdr.ID
			}
		}
		if cwd == "" {
			rawName := strings.Trim(de.Name(), "-")
			cwd = "/" + strings.ReplaceAll(rawName, "-", "/")
		}
		wsDisplay := strings.ReplaceAll(cwd, home, "~")
		dtStr := time.Unix(0, int64(m*1e9)).Format("2006-01-02 15:04")
		latestID := sid
		if latestID == "" {
			latestID = "unknown"
		}
		workspaces = append(workspaces, workspaceItem{
			Cwd:                cwd,
			WorkspaceShort:     wsDisplay,
			SessionsCount:      len(files),
			LatestMtime:        m,
			LatestActivity:     dtStr,
			LatestSessionID:    latestID,
			LatestSessionShort: shortIDOrEmpty(sid),
		})
	}

	sort.SliceStable(workspaces, func(i, j int) bool { return workspaces[i].LatestMtime > workspaces[j].LatestMtime })

	if asJSON {
		printJSON(workspaces)
		return
	}

	fmt.Println("\n" + strings.Repeat("=", 82))
	fmt.Println("                          PI WORKSPACES INDEX")
	fmt.Println(strings.Repeat("=", 82))
	fmt.Printf("%-5s %-44s %8s   %-17s %s\n", "Idx", "Workspace", "Sessions", "Latest Activity", "Latest ID")
	fmt.Println(strings.Repeat("-", 82))
	for idx, w := range workspaces {
		wsName := w.WorkspaceShort
		if len(wsName) > 44 {
			wsName = "..." + wsName[len(wsName)-41:]
		}
		fmt.Printf("[%-2d] %-44s %8d   %-17s %s\n", idx+1, wsName, w.SessionsCount, w.LatestActivity, w.LatestSessionShort)
	}
	fmt.Println(strings.Repeat("=", 82))
	fmt.Println("Tip: Run 'go run . --list --workspace <name>' to filter sessions by workspace.")
	fmt.Println("")
}

func shortIDOrEmpty(sid string) string {
	if len(sid) > 8 {
		return sid[:8]
	}
	return sid
}
