package main

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"github.com/dustin/go-humanize"
	"github.com/spf13/cobra"
)

// dirNode is a node in the directory tree with aggregated stats.
type dirNode struct {
	name         string
	path         string
	ownSize      int64     // size of files directly in this directory
	ownFiles     int64     // count of files directly in this directory
	ownTime      time.Time // newest atime of files directly in this directory
	totalSize    int64     // ownSize + all descendants
	newestTime   time.Time // newest atime across all descendants
	fileCount    int64     // ownFiles + all descendants
	staleSize    int64     // total size of stale content in this subtree
	ownStaleSize int64     // size of stale files directly in this directory
	children     []*dirNode
}

var (
	staleDays  int
	topN       int
	workers    int
	minSizeStr string
	drillRatio float64
)

func main() {
	cmd := &cobra.Command{
		Use:   "diskrot [path]",
		Short: "Find large and stale directories",
		Long:  "Parallel filesystem walker that shows where disk space is used, with auto drill-down into dominant subdirectories.",
		Args:  cobra.MaximumNArgs(1),
		RunE:  run,
	}
	cmd.Flags().IntVar(&staleDays, "stale-days", 0, "only show directories not accessed in this many days (0 = show all)")
	cmd.Flags().IntVar(&topN, "top", 20, "number of top-level results to show")
	cmd.Flags().IntVar(&workers, "workers", runtime.NumCPU(), "number of parallel workers")
	cmd.Flags().StringVar(&minSizeStr, "min-size", "500MB", "minimum size to display (e.g. 100MB, 1GB)")
	cmd.Flags().Float64Var(&drillRatio, "min-ratio", 0.3, "minimum ratio of parent size to show a subdirectory")
	cmd.SilenceUsage = true

	if err := cmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func run(cmd *cobra.Command, args []string) error {
	target := os.Getenv("HOME")
	if len(args) > 0 {
		target = args[0]
	}

	abs, err := filepath.Abs(target)
	if err != nil {
		return fmt.Errorf("bad path: %w", err)
	}
	target = abs

	minBytesU, err := humanize.ParseBytes(minSizeStr)
	if err != nil {
		return fmt.Errorf("bad min-size: %w", err)
	}
	minBytes := int64(minBytesU)

	var cutoff time.Time
	staleMode := staleDays > 0
	if staleMode {
		cutoff = time.Now().AddDate(0, 0, -staleDays)
	}

	// Print scan info immediately.
	fmt.Printf("Scanning: %s\n", target)
	fmt.Printf("Filters:  min-size=%s  top=%d  min-ratio=%.0f%%", minSizeStr, topN, drillRatio*100)
	if staleMode {
		fmt.Printf("  stale-days=%d (cutoff=%s)", staleDays, cutoff.Format("2006-01-02"))
	}
	fmt.Printf("\n")

	// Phase 1: walk and collect per-directory stats.
	start := time.Now()
	dirMap := walkAll(target, workers, cutoff)
	elapsed := time.Since(start)

	// Phase 2: build tree from flat map.
	root := buildTree(target, dirMap)
	if root == nil {
		return fmt.Errorf("no data found")
	}

	// Phase 3: if stale filter, compute stale size per subtree.
	if staleMode {
		computeStaleSize(root)
	}

	fmt.Printf("Scanned %s in %s (%d files)\n\n",
		humanize.IBytes(uint64(root.totalSize)),
		elapsed.Round(time.Millisecond),
		root.fileCount)

	// Phase 4: get top-level children, filter by size and sort.
	candidates := root.children
	filtered := make([]*dirNode, 0)
	for _, c := range candidates {
		if staleMode {
			if c.staleSize >= minBytes {
				filtered = append(filtered, c)
			}
		} else {
			if c.totalSize >= minBytes {
				filtered = append(filtered, c)
			}
		}
	}
	candidates = filtered

	if staleMode {
		sort.Slice(candidates, func(i, j int) bool {
			return candidates[i].staleSize > candidates[j].staleSize
		})
	} else {
		sort.Slice(candidates, func(i, j int) bool {
			return candidates[i].totalSize > candidates[j].totalSize
		})
	}
	if len(candidates) > topN {
		candidates = candidates[:topN]
	}

	// Phase 5: print results.
	if staleMode {
		fmt.Printf("%9s  %9s  %10s  %9s  %s\n", "STALE", "TOTAL", "ACCESSED", "FILES", "PATH")
	} else {
		fmt.Printf("%9s  %10s  %9s  %s\n", "SIZE", "ACCESSED", "FILES", "PATH")
	}

	for _, c := range candidates {
		printNode(c, target, drillRatio, minBytes, staleMode)
	}
	return nil
}

func printNode(n *dirNode, root string, drillRatio float64, minBytes int64, staleMode bool) {
	rel, _ := filepath.Rel(root, n.path)
	date := n.newestTime.Format("2006-01-02")
	if staleMode {
		fmt.Printf("%9s  %9s  %10s  %9d  %s/\n",
			humanize.IBytes(uint64(n.staleSize)),
			humanize.IBytes(uint64(n.totalSize)),
			date, n.fileCount, rel)
	} else {
		fmt.Printf("%9s  %10s  %9d  %s/\n",
			humanize.IBytes(uint64(n.totalSize)),
			date, n.fileCount, rel)
	}

	// Find children worth drilling into.
	sizeFunc := func(c *dirNode) int64 {
		if staleMode {
			return c.staleSize
		}
		return c.totalSize
	}

	parentSize := sizeFunc(n)
	drillThreshold := int64(float64(parentSize) * drillRatio)
	var drillChildren []*dirNode
	for _, c := range n.children {
		cs := sizeFunc(c)
		if cs >= drillThreshold && cs >= minBytes {
			drillChildren = append(drillChildren, c)
		}
	}

	if len(drillChildren) == 0 {
		return
	}

	sort.Slice(drillChildren, func(i, j int) bool {
		return sizeFunc(drillChildren[i]) > sizeFunc(drillChildren[j])
	})

	for _, c := range drillChildren {
		printNode(c, root, drillRatio, minBytes, staleMode)
	}
}

// computeStaleSize propagates stale sizes up the tree.
// ownStaleSize is already computed during the walk (per-file check).
func computeStaleSize(n *dirNode) {
	n.staleSize = n.ownStaleSize
	for _, c := range n.children {
		computeStaleSize(c)
		n.staleSize += c.staleSize
	}
}

// buildTree constructs a directory tree from the flat per-directory stats map.
func buildTree(root string, dirMap map[string]*dirNode) *dirNode {
	rootNode, ok := dirMap[root]
	if !ok {
		rootNode = &dirNode{name: filepath.Base(root), path: root}
		dirMap[root] = rootNode
	}

	// Link children to parents.
	for path, node := range dirMap {
		if path == root {
			continue
		}
		parentPath := filepath.Dir(path)
		parent, ok := dirMap[parentPath]
		if !ok {
			continue
		}
		parent.children = append(parent.children, node)
	}

	// Propagate sizes up: children stats bubble up to parents.
	propagate(rootNode)

	return rootNode
}

// propagate recursively computes total size, file count, and newest time
// by summing own stats plus all children.
func propagate(n *dirNode) {
	n.totalSize = n.ownSize
	n.fileCount = n.ownFiles
	n.newestTime = n.ownTime
	for _, c := range n.children {
		propagate(c)
		n.totalSize += c.totalSize
		n.fileCount += c.fileCount
		if c.newestTime.After(n.newestTime) {
			n.newestTime = c.newestTime
		}
	}
}

// walkAll walks the entire directory tree and returns per-directory stats.
// Each directory entry contains only the files directly in that directory
// (not recursively summed — that happens in propagate).
func walkAll(target string, numWorkers int, cutoff time.Time) map[string]*dirNode {
	var (
		mu     sync.Mutex
		result = make(map[string]*dirNode)
		wg     sync.WaitGroup
		ch     = make(chan string, 4096)
	)

	type dirEntry struct {
		name  string
		isDir bool
	}

	readDir := func(dirPath string) ([]dirEntry, error) {
		fd, err := syscall.Open(dirPath, syscall.O_RDONLY|syscall.O_DIRECTORY, 0)
		if err != nil {
			return nil, err
		}
		defer syscall.Close(fd)

		var entries []dirEntry
		buf := make([]byte, 32768)

		for {
			n, err := syscall.Getdents(fd, buf)
			if err != nil {
				return entries, err
			}
			if n <= 0 {
				break
			}

			offset := 0
			for offset < n {
				dirent := (*syscall.Dirent)(unsafe.Pointer(&buf[offset]))
				nameBytes := buf[offset+nameOffset() : offset+int(dirent.Reclen)]
				nameLen := 0
				for nameLen < len(nameBytes) && nameBytes[nameLen] != 0 {
					nameLen++
				}
				name := string(nameBytes[:nameLen])
				offset += int(dirent.Reclen)

				if name == "." || name == ".." {
					continue
				}

				entries = append(entries, dirEntry{name: name, isDir: dirent.Type == syscall.DT_DIR})
			}
		}
		return entries, nil
	}

	var processDir func(dir string, localResult map[string]*dirNode)

	processDir = func(dir string, localResult map[string]*dirNode) {
		entries, err := readDir(dir)
		if err != nil {
			return
		}

		node, ok := localResult[dir]
		if !ok {
			node = &dirNode{name: filepath.Base(dir), path: dir}
			localResult[dir] = node
		}

		for _, entry := range entries {
			fullPath := dir + "/" + entry.name

			if entry.isDir {
				// Ensure child dir exists in local map so tree building works.
				if _, ok := localResult[fullPath]; !ok {
					localResult[fullPath] = &dirNode{name: entry.name, path: fullPath}
				}
				wg.Add(1)
				select {
				case ch <- fullPath:
				default:
					wg.Done()
					processDir(fullPath, localResult)
				}
				continue
			}

			var st syscall.Stat_t
			if syscall.Lstat(fullPath, &st) != nil {
				continue
			}

			atime := time.Unix(st.Atim.Sec, st.Atim.Nsec)
			node.ownSize += st.Size
			node.ownFiles++
			if atime.After(node.ownTime) {
				node.ownTime = atime
			}
			if !cutoff.IsZero() && atime.Before(cutoff) {
				node.ownStaleSize += st.Size
			}
		}
	}

	flushLocal := func(localResult map[string]*dirNode) {
		if len(localResult) == 0 {
			return
		}
		mu.Lock()
		for path, ln := range localResult {
			n, ok := result[path]
			if !ok {
				result[path] = &dirNode{
					name:         ln.name,
					path:         ln.path,
					ownSize:      ln.ownSize,
					ownFiles:     ln.ownFiles,
					ownTime:      ln.ownTime,
					ownStaleSize: ln.ownStaleSize,
				}
			} else {
				n.ownSize += ln.ownSize
				n.ownFiles += ln.ownFiles
				if ln.ownTime.After(n.ownTime) {
					n.ownTime = ln.ownTime
				}
				n.ownStaleSize += ln.ownStaleSize
			}
		}
		mu.Unlock()
	}

	var workerWg sync.WaitGroup

	worker := func() {
		localResult := make(map[string]*dirNode)
		flushCount := 0

		for dir := range ch {
			processDir(dir, localResult)
			flushCount++
			if flushCount >= 128 {
				flushLocal(localResult)
				localResult = make(map[string]*dirNode)
				flushCount = 0
			}
			wg.Done()
		}
		flushLocal(localResult)
		workerWg.Done()
	}

	for i := 0; i < numWorkers; i++ {
		workerWg.Add(1)
		go worker()
	}

	wg.Add(1)
	ch <- target

	wg.Wait()
	close(ch)
	workerWg.Wait()

	return result
}

func nameOffset() int {
	var d syscall.Dirent
	return int(unsafe.Offsetof(d.Name))
}
