package main

import (
	"flag"
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
)

type dirStats struct {
	path       string
	totalSize  int64
	newestTime time.Time // atime (last access)
	fileCount  int64
}

func main() {
	target := flag.String("path", os.Getenv("HOME"), "root directory to scan")
	depth := flag.Int("depth", 2, "aggregation depth relative to root")
	staleDays := flag.Int("not-accessed-days", 180, "mark directories not accessed in this many days")
	topN := flag.Int("top", 40, "number of results to show")
	workers := flag.Int("workers", runtime.NumCPU(), "number of parallel workers")
	minSize := flag.Int64("min-size", 100*1024*1024, "minimum size in bytes to display (default 100MB)")
	flag.Parse()

	cutoff := time.Now().AddDate(0, 0, -*staleDays)
	abs, err := filepath.Abs(*target)
	if err != nil {
		fmt.Fprintf(os.Stderr, "bad path: %v\n", err)
		os.Exit(1)
	}
	*target = abs

	dirs := walkAndAggregate(*target, *depth, *workers)

	sorted := make([]*dirStats, 0, len(dirs))
	for _, d := range dirs {
		if d.totalSize >= *minSize && d.newestTime.Before(cutoff) {
			sorted = append(sorted, d)
		}
	}
	sort.Slice(sorted, func(i, j int) bool {
		return sorted[i].totalSize > sorted[j].totalSize
	})
	if len(sorted) > *topN {
		sorted = sorted[:*topN]
	}

	maxPath := 0
	for _, d := range sorted {
		rel, _ := filepath.Rel(*target, d.path)
		if len(rel) > maxPath {
			maxPath = len(rel)
		}
	}

	fmt.Printf("Target: %s  depth=%d  not-accessed=%dd  cutoff=%s\n\n", *target, *depth, *staleDays, cutoff.Format("2006-01-02"))

	for _, d := range sorted {
		rel, _ := filepath.Rel(*target, d.path)
		fmt.Printf("%10s  %s  %6d files  %s\n",
			humanize.IBytes(uint64(d.totalSize)),
			d.newestTime.Format("2006-01-02"),
			d.fileCount,
			rel,
		)
	}
}

func walkAndAggregate(target string, depth, numWorkers int) map[string]*dirStats {
	var (
		mu   sync.Mutex
		dirs = make(map[string]*dirStats)
		wg   sync.WaitGroup
		ch   = make(chan string, 4096)
	)

	targetLen := len(target)
	bucketKey := func(path string) string {
		rel := path[targetLen:]
		if len(rel) > 0 && rel[0] == '/' {
			rel = rel[1:]
		}
		if rel == "" {
			return target
		}
		count := 0
		for i := 0; i < len(rel); i++ {
			if rel[i] == '/' {
				count++
				if count >= depth {
					return target + "/" + rel[:i]
				}
			}
		}
		return target + "/" + rel
	}

	merge := func(bucket map[string]*dirStats, key string, size int64, atime time.Time) {
		d, ok := bucket[key]
		if !ok {
			d = &dirStats{path: key}
			bucket[key] = d
		}
		d.totalSize += size
		d.fileCount++
		if atime.After(d.newestTime) {
			d.newestTime = atime
		}
	}

	flushLocal := func(localDirs map[string]*dirStats) {
		if len(localDirs) == 0 {
			return
		}
		mu.Lock()
		for key, ld := range localDirs {
			d, ok := dirs[key]
			if !ok {
				d = &dirStats{path: key}
				dirs[key] = d
			}
			d.totalSize += ld.totalSize
			d.fileCount += ld.fileCount
			if ld.newestTime.After(d.newestTime) {
				d.newestTime = ld.newestTime
			}
		}
		mu.Unlock()
	}

	// readDir uses raw getdents64 to read directory entries without sorting.
	// Returns entry names and whether each is a directory (from d_type).
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
				// Find null terminator.
				nameLen := 0
				for nameLen < len(nameBytes) && nameBytes[nameLen] != 0 {
					nameLen++
				}
				name := string(nameBytes[:nameLen])
				offset += int(dirent.Reclen)

				if name == "." || name == ".." {
					continue
				}

				isDir := dirent.Type == syscall.DT_DIR
				entries = append(entries, dirEntry{name: name, isDir: isDir})
			}
		}
		return entries, nil
	}

	var processDir func(dir string, localDirs map[string]*dirStats, flushFn func(map[string]*dirStats))

	processDir = func(dir string, localDirs map[string]*dirStats, flushFn func(map[string]*dirStats)) {
		entries, err := readDir(dir)
		if err != nil {
			return
		}

		for _, entry := range entries {
			fullPath := dir + "/" + entry.name

			if entry.isDir {
				wg.Add(1)
				select {
				case ch <- fullPath:
				default:
					wg.Done()
					processDir(fullPath, localDirs, flushFn)
				}
				continue
			}

			var st syscall.Stat_t
			if syscall.Lstat(fullPath, &st) != nil {
				continue
			}

			// Use atime for staleness.
			atime := time.Unix(st.Atim.Sec, st.Atim.Nsec)
			key := bucketKey(fullPath)
			merge(localDirs, key, st.Size, atime)
		}
	}

	worker := func() {
		localDirs := make(map[string]*dirStats)
		flushCount := 0

		for dir := range ch {
			processDir(dir, localDirs, flushLocal)
			flushCount++
			if flushCount >= 128 {
				flushLocal(localDirs)
				localDirs = make(map[string]*dirStats)
				flushCount = 0
			}
			wg.Done()
		}
		flushLocal(localDirs)
	}

	for i := 0; i < numWorkers; i++ {
		go worker()
	}

	wg.Add(1)
	ch <- target

	wg.Wait()
	close(ch)

	return dirs
}

// nameOffset returns the byte offset of the d_name field in syscall.Dirent.
func nameOffset() int {
	var d syscall.Dirent
	return int(unsafe.Offsetof(d.Name))
}
