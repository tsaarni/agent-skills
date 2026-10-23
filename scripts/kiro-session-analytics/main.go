// kiro-sa: read-only analytics over local Kiro CLI session data.
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/spf13/cobra"
)

// globals are the options shared by all commands.
type globals struct {
	Workspace string

	out io.Writer
	err io.Writer
	now func() time.Time
}

// addFilters registers the time and workspace options on cmd.
func (g *globals) addFilters(cmd *cobra.Command, tf *timeFlags) {
	tf.register(cmd)
	cmd.Flags().StringVar(&g.Workspace, "workspace", "", "limit to one workspace path (default all)")
}

func newRoot(g *globals) *cobra.Command {
	root := &cobra.Command{
		Use:           "kiro-sa",
		Short:         "Spend, journal and recall over local Kiro CLI sessions (read-only)",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.AddCommand(
		newListCmd(g),
		newSpendCmd(g),
		newJournalCmd(g),
		newFindCmd(g),
		newShowCmd(g),
	)
	return root
}

func sessionsDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ".kiro/sessions"
	}
	return filepath.Join(home, ".kiro", "sessions")
}

func main() {
	g := &globals{out: os.Stdout, err: os.Stderr, now: time.Now}
	if err := newRoot(g).Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

// store opens the sessions directory.
func (g *globals) store() (*Store, error) {
	root := sessionsDir()
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return nil, fmt.Errorf("sessions directory %q not found", root)
	}
	return newStore(root), nil
}

// filter builds the header filter for a time window.
func (g *globals) filter(w Window) (Filter, error) {
	f := Filter{Window: w}
	if g.Workspace != "" {
		p, err := filepath.Abs(expandHome(g.Workspace))
		if err != nil {
			return Filter{}, err
		}
		f.Workspace = filepath.Clean(p)
	}
	return f, nil
}

// turnRef points to a turn inside a loaded session.
type turnRef struct {
	S *Session
	T *Turn
}

// turnsIn returns the turns of the sessions that started inside the window,
// oldest first.
func turnsIn(sessions []*Session, w Window) []turnRef {
	var refs []turnRef
	for _, s := range sessions {
		for i := range s.Turns {
			if w.Contains(s.Turns[i].Start) {
				refs = append(refs, turnRef{s, &s.Turns[i]})
			}
		}
	}
	sort.SliceStable(refs, func(i, j int) bool { return refs[i].T.Start.Before(refs[j].T.Start) })
	return refs
}

// loadWindow lists and loads the sessions matching the filter. Sessions
// without turns are dropped.
func (g *globals) loadWindow(w Window, opt LoadOpts) ([]*Session, error) {
	st, err := g.store()
	if err != nil {
		return nil, err
	}
	f, err := g.filter(w)
	if err != nil {
		return nil, err
	}
	hs, err := st.List(f)
	if err != nil {
		return nil, err
	}
	var keep []SessionHeader
	for _, h := range hs {
		if h.Turns != 0 {
			keep = append(keep, h)
		}
	}
	return st.LoadAll(keep, opt, g.err), nil
}
