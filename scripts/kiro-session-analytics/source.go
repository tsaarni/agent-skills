package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

// Filter restricts which sessions a Source lists.
type Filter struct {
	Window    Window
	Workspace string // exact path or parent directory; empty = all
}

// matchesWorkspace reports whether ws equals or lies under the filter path.
func (f Filter) matchesWorkspace(ws string) bool {
	if f.Workspace == "" {
		return true
	}
	return ws == f.Workspace || strings.HasPrefix(ws, strings.TrimRight(f.Workspace, "/")+"/")
}

// matchesHeader applies the cheap pre-filters to a header. A session cannot
// have turns in the window when it was last modified before the window or
// created after it.
func (f Filter) matchesHeader(h SessionHeader) bool {
	if !f.matchesWorkspace(h.Workspace) {
		return false
	}
	if !f.Window.Since.IsZero() && !h.Updated.IsZero() && h.Updated.Before(f.Window.Since) {
		return false
	}
	if !f.Window.Until.IsZero() && !h.Created.IsZero() && !h.Created.Before(f.Window.Until) {
		return false
	}
	return true
}

// Source reads sessions of one engine.
type Source interface {
	Engine() string
	// List returns session headers using metadata only.
	List(f Filter) ([]SessionHeader, error)
	// Load reads the full session.
	Load(h SessionHeader, opt LoadOpts) (*Session, error)
}

// Registered engines. Removing the V2 reader means deleting source_v2.go, its
// test and fixtures, and the v2 line below.
func newSources(root string) []Source {
	return []Source{
		newV3Source(root),
		newV2Source(root),
	}
}

// Store is a set of sources.
type Store struct {
	sources []Source
	byName  map[string]Source
}

func newStore(root string) *Store {
	st := &Store{sources: newSources(root), byName: map[string]Source{}}
	for _, s := range st.sources {
		st.byName[s.Engine()] = s
	}
	return st
}

// List returns the headers of all engines matching f, newest first.
func (st *Store) List(f Filter) ([]SessionHeader, error) {
	var all []SessionHeader
	for _, s := range st.sources {
		hs, err := s.List(f)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", s.Engine(), err)
		}
		all = append(all, hs...)
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].Updated.After(all[j].Updated) })
	return all, nil
}

func (st *Store) Load(h SessionHeader, opt LoadOpts) (*Session, error) {
	src, ok := st.byName[h.Engine]
	if !ok {
		return nil, fmt.Errorf("unknown engine %q", h.Engine)
	}
	return src.Load(h, opt)
}

// LoadAll loads sessions with a bounded worker pool. The result keeps the
// order of headers; sessions that fail to load are reported on warn and
// skipped.
func (st *Store) LoadAll(hs []SessionHeader, opt LoadOpts, warn io.Writer) []*Session {
	out := make([]*Session, len(hs))
	workers := runtime.NumCPU()
	if workers > 8 {
		workers = 8
	}
	if workers < 1 {
		workers = 1
	}
	jobs := make(chan int)
	var wg sync.WaitGroup
	var mu sync.Mutex
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				s, err := st.Load(hs[i], opt)
				if err != nil {
					if warn != nil {
						mu.Lock()
						fmt.Fprintf(warn, "warning: %s %s: %v\n", hs[i].Engine, shortID(hs[i].ID), err)
						mu.Unlock()
					}
					continue
				}
				out[i] = s
			}
		}()
	}
	for i := range hs {
		jobs <- i
	}
	close(jobs)
	wg.Wait()

	res := out[:0]
	for _, s := range out {
		if s != nil {
			res = append(res, s)
		}
	}
	return res
}

// Resolve finds a session by ID prefix across all engines.
func (st *Store) Resolve(prefix string) (SessionHeader, error) {
	prefix = strings.ToLower(strings.TrimPrefix(strings.TrimSpace(prefix), "sess_"))
	if len(prefix) < 4 {
		return SessionHeader{}, fmt.Errorf("session ID prefix %q is too short (need at least 4 characters)", prefix)
	}
	hs, err := st.List(Filter{})
	if err != nil {
		return SessionHeader{}, err
	}
	var matches []SessionHeader
	for _, h := range hs {
		if strings.HasPrefix(h.ID, prefix) {
			matches = append(matches, h)
		}
	}
	switch len(matches) {
	case 0:
		return SessionHeader{}, fmt.Errorf("no session matches %q", prefix)
	case 1:
		return matches[0], nil
	}
	var b strings.Builder
	fmt.Fprintf(&b, "session prefix %q is ambiguous:", prefix)
	for i, m := range matches {
		if i >= 10 {
			fmt.Fprintf(&b, "\n  ... %d more", len(matches)-10)
			break
		}
		fmt.Fprintf(&b, "\n  %s  %s  %s", m.ID, m.Engine, m.Updated.Format("2006-01-02 15:04"))
	}
	return SessionHeader{}, fmt.Errorf("%s", b.String())
}

// Selector is a parsed "<prefix>" or "<prefix>#<turn>" argument.
type Selector struct {
	Prefix string
	Turn   int // 0 = whole session
}

func parseSelector(s string) (Selector, error) {
	prefix, turn, found := strings.Cut(s, "#")
	sel := Selector{Prefix: prefix}
	if found {
		var n int
		if _, err := fmt.Sscanf(turn, "%d", &n); err != nil || n < 1 || fmt.Sprint(n) != turn {
			return Selector{}, fmt.Errorf("invalid turn number in %q", s)
		}
		sel.Turn = n
	}
	if sel.Prefix == "" {
		return Selector{}, fmt.Errorf("empty session selector")
	}
	return sel, nil
}

// --- shared reader helpers ---

// forEachLine calls fn for every complete or trailing line of path. Lines of
// any length are supported. A half-written last line is passed to fn like any
// other; callers ignore lines that fail to parse.
func forEachLine(path string, fn func(line []byte)) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	r := bufio.NewReaderSize(f, 256*1024)
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			fn(line)
		}
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func parseTime(s string) time.Time {
	if s == "" {
		return time.Time{}
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t.Local()
	}
	return time.Time{}
}

func expandHome(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(p, "~"))
		}
	}
	return p
}

// textOf extracts a string from a JSON value that is either a string or
// something else (returned as raw JSON text).
func textOf(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	if raw[0] == '"' {
		var s string
		if json.Unmarshal(raw, &s) == nil {
			return s
		}
	}
	if string(raw) == "null" {
		return ""
	}
	return string(raw)
}
