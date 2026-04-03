# Code Review Protocol

This protocol defines the standard for conducting high-quality automated code reviews. It is inspired by specialized review pipelines used in high-assurance systems, ensuring both technical depth and engineering excellence.

## General Instructions

- **Evaluate, don't narrate**: Do not summarize what the code does. Focus exclusively on what could go wrong, is incorrect, or could be improved.
- **Be constructive**: Every finding MUST include a concrete suggestion for how to fix or improve the identified issue.
- **Prioritize depth over breadth**: Spend more effort on Critical and Improvement findings than on Nitpicks. It is better to surface two well-evidenced Critical issues than ten speculative Nitpicks.

## Review Stages & Focus Areas

Follow this logical progression to ensure every change is analyzed from high-level intent down to documentation:

### 1. Prior Feedback

- **Goal**: Confirm all previous reviewer feedback has been addressed.
- **Check**: Analyze PR metadata to identify unresolved review threads and verify whether the current code addresses them. Avoid duplicating already-resolved feedback.

### 2. Intent & Architectural Alignment

- **Goal**: Verify the change achieves its stated purpose and fits the system architecture.
- **Check**: Does the implementation match the PR description? Is it simpler than the alternative, or is there over-engineering?

### 3. Logic & Implementation Verification

- **Goal**: Ensure the code correctly implements the logic and handles errors robustly.
- **Check**: Look for logic errors, off-by-one errors, and unhandled boundary conditions. Are fallible calls handled? Is state consistent after errors? Are there any instances of **dead code** or unnecessary data copies?

### 4. API Design & Backwards Compatibility

- **Goal**: Maintain public interface consistency and prevent accidental breaking changes.
- **Check**: Are API changes consistent with existing patterns? Do they break external users or data formats? Are there unnecessary public methods or fields? Do the changes over-expose internal implementation? When fields are added to a type, check all places where that type is used as a field in other types and verify the new field is semantically appropriate in every usage context — a shared type may serve different roles in different parent types. Cross-reference the impact analysis to verify no callers are broken by signature or behavioral contract changes.

### 5. Resource Safety, Concurrency & Performance

- **Goal**: Prevent resource leaks, synchronization bugs, and algorithmic inefficiencies.
- **Check**: Is cleanup (defer/RAII) handled correctly? Are there race conditions or deadlocks? Look for **algorithmic inefficiencies** (e.g., O(n²) when O(n) is possible) and **N+1 query problems** in database code.

### 6. Security & Input Validation

- **Goal**: Prevent vulnerabilities at system and trust boundaries.
- **Check**: Check for injections (SQL/XSS/code evaluation), overflows, and hardcoded secrets. Is external input validated before use?

### 7. Testing & Documentation

- **Goal**: Ensure the change is verified and clearly documented.
- **Check**: Are new tests provided? Are the tests actually verifying behavior? Do they cover edge cases? Are READMEs, API docs, and changelogs updated?

### 8. Language Idioms

- **Goal**: Ensure the code follows the idiomatic conventions of the language being used.
- **Check**: Does the code use standard language patterns (e.g., error handling idioms, context propagation in Go, exception safety in C++, mutable default arguments and context managers for resource cleanup in Python, resource management with try-with-resources in Java)? Are there non-idiomatic constructs that would confuse maintainers familiar with the language?

---

## Verification & Noise Reduction

Before finalizing any finding, apply these filters to reduce false positives:

1.  **Concrete Evidence**: Can you point to the exact line(s) of code that cause the issue? If not, omit the finding.
2.  **Contextual Awareness**: Does the surrounding code, the caller, or an upstream layer already handle this concern? If so, omit the finding.
3.  **Deduplication**: Do multiple findings share the same root cause? If so, merge them into a single high-quality report, preferring the finding with the most specific code evidence.

---

## Severity Assessment

- **Critical**: Must fix before merge (Bugs, security holes, breaking changes, major architectural flaws).
- **Improvement**: Recommended (Performance, maintainability, better error handling, missing tests).
- **Nitpick**: Minor suggestion (Naming, formatting, documentation clarity).
