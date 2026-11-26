package main

import (
	"fmt"
	"io"
	"os"
)

// ANSI styles used by show.
const (
	styleTurn    = "\x1b[1;97;44m" // bold white on blue
	styleRequest = "\x1b[1;36m"    // bold cyan
	styleCold    = "\x1b[1;33m"    // bold yellow
	styleReset   = "\x1b[0m"
)

// useColor decides whether output written to w is colored. mode is "auto",
// "always" or "never"; auto colors only a terminal and respects NO_COLOR.
func useColor(mode string, w io.Writer) (bool, error) {
	switch mode {
	case "always":
		return true, nil
	case "never":
		return false, nil
	case "auto":
		if os.Getenv("NO_COLOR") != "" {
			return false, nil
		}
		f, ok := w.(*os.File)
		if !ok {
			return false, nil
		}
		st, err := f.Stat()
		return err == nil && st.Mode()&os.ModeCharDevice != 0, nil
	}
	return false, fmt.Errorf("--color must be auto, always or never, not %q", mode)
}

// paint wraps s in style when color is on.
func paint(on bool, style, s string) string {
	if !on {
		return s
	}
	return style + s + styleReset
}
