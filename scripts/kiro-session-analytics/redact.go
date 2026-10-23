package main

import "regexp"

const redacted = "[redacted]"

// Session files have been seen to contain real credentials. These patterns
// cover the formats that showed up; they are deliberately narrow so ordinary
// text is not mangled.
var redactPatterns = []struct {
	re   *regexp.Regexp
	repl string
}{
	{regexp.MustCompile(`(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)`), redacted},
	{regexp.MustCompile(`(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}`), "${1} " + redacted},
	{regexp.MustCompile(`\bgh[pousr]_[A-Za-z0-9]{20,}`), redacted},
	{regexp.MustCompile(`\bgithub_pat_[A-Za-z0-9_]{20,}`), redacted},
	{regexp.MustCompile(`\baoa[A-Za-z0-9_-]{20,}`), redacted},
	{regexp.MustCompile(`\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`), redacted},
}

func redact(s string) string {
	for _, p := range redactPatterns {
		s = p.re.ReplaceAllString(s, p.repl)
	}
	return s
}

// redactSession masks secrets in all text fields of the session, in place.
func redactSession(s *Session) {
	s.Title = redact(s.Title)
	for i := range s.Turns {
		t := &s.Turns[i]
		t.Prompt = redact(t.Prompt)
		t.Response = redact(t.Response)
		for j := range t.Tools {
			tc := &t.Tools[j]
			tc.Command = redact(tc.Command)
			tc.Output = redact(tc.Output)
		}
	}
}
