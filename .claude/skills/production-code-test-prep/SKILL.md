---
name: production-code-test-prep
description: Guidance and instructions for solving coding assessment problems with production-grade engineering practices and strict cheating-prevention compliance.
---

# Production-Grade Coding Test Preparation & Execution Guidelines

## Context & Objectives
This skill serves as a standard operating framework for tackling automated coding assessments (e.g., LeetCode-style platforms with proctoring). High performance requires satisfying two distinct dimensions:
1. **Compliance & Proctoring Safety:** Zero tolerance for tab switches, external windows, or off-screen distractions.
2. **Production-Grade Engineering:** Solutions must go beyond passing basic test cases to demonstrate senior-level software architecture, edge-case resilience, testing coverage, and algorithmic efficiency.

---

## Part 1: Proctoring & Environment Protocols

### Pre-Exam Preparation Checklist
- [ ] **Single Screen Setup:** Disable or disconnect secondary monitors.
- [ ] **Tab & App Clean-up:** Close all background applications, IDEs, chat platforms, and browser tabs. Keep only the test browser window open.
- [ ] **Device Isolation:** Place mobile phones, smartwatches, and secondary devices in another room or out of arm's reach.
- [ ] **Camera & Gaze Alignment:** Ensure the webcam is centered, lighting is adequate, and maintain eye contact with the primary assessment screen.
- [ ] **No Copy-Paste / Focus Changes:** Avoid switching tabs, opening pop-ups, or triggering browser extensions during the session.

---

## Part 2: Code Architecture & Implementation Standards

### 1. Robust Input Validation & Edge Case Handling
Never assume inputs are well-formed. Explicitly validate and handle:
- **Null / Undefined / Empty:** `null`, `undefined`, empty strings `""`, empty arrays `[]`.
- **Numeric Boundaries:** Zero values (prevent divide-by-zero), negative numbers, integer overflow/underflow boundaries.
- **Type & Format Validation:** Invalid data types, malformed strings, out-of-range parameters.
- **Resource Constraints:** Massive inputs leading to high memory or CPU consumption.

### 2. Clean Code & Structural Separation
- **Separation of Concerns:** Separate core business/algorithmic logic from parsing, validation, and I/O handling.
- **Single Responsibility Principle (SRP):** Write small, focused helper functions with descriptive naming.
- **Type Safety & Maintainability:** Use clear type annotations (e.g., TypeScript / Python type hints) and avoid magic numbers/strings.

### 3. Testing & Resiliency (Unit Tests)
Include self-contained unit tests inside the submission or test runner:
- **Happy Path:** Standard positive input validation.
- **Boundary Conditions:** Testing min/max bounds and zero/empty states.
- **Negative / Error Paths:** Verifying proper exception throwing or error handling on invalid input.

### 4. Complexity & Trade-off Analysis
Document time and space complexity explicitly using Big-O notation:
- Aim for optimal time complexity ($O(N)$ or $O(N \log N)$ where applicable).
- Keep space complexity efficient ($O(1)$ auxiliary space when possible).
- Explicitly state architectural trade-offs made due to time constraints.

---

## Part 3: Template Structure for Solutions

```python
"""
===============================================================================
PROBLEM SOLUTION & ENGINEERING DESIGN
===============================================================================

1. Architectural Overview:
   - Modularity: Core logic separated from input handling.
   - Resilience: Explicit edge-case checking and defensive programming.

2. Complexity Analysis:
   - Time Complexity: O(N) - single pass over input.
   - Space Complexity: O(1) - auxiliary storage.

3. Assumptions & Trade-offs:
   - Input fits in memory.
   - Prioritized readability and strict error handling over micro-optimizations.
===============================================================================
"""

from typing import List, Optional, Any


class Solution:
    def process_data(self, data: List[int]) -> List[int]:
        """
        Main execution method for processing input data.

        :param data: List of integers to be processed.
        :return: Transformed integer list.
        :raises ValueError: If input is invalid or improperly formatted.
        """
        # 1. Input Validation & Edge Cases
        if data is None:
            raise ValueError("Input data cannot be None.")
        if not data:
            return []

        # 2. Core Business Logic
        result = []
        for item in data:
            # Handle item-level boundary conditions
            if not isinstance(item, int):
                raise TypeError(f"Invalid element type: expected int, got {type(item).__name__}")

            # Transformation logic
            transformed = item * 2
            result.append(transformed)

        return result


# =============================================================================
# UNIT TESTS & VERIFICATION
# =============================================================================
def run_tests():
    sol = Solution()

    # Happy Path
    assert sol.process_data([1, 2, 3]) == [2, 4, 6], "Failed Happy Path"

    # Edge Cases
    assert sol.process_data([]) == [], "Failed Empty List"

    # Error Handling
    try:
        sol.process_data(None)
        assert False, "Failed Null Input Exception"
    except ValueError:
        pass

    print("All tests passed successfully.")


if __name__ == "__main__":
    run_tests()
```

---

## Addendum — calibrating this against a hard per-problem time budget

Added 2026-08-12, reconciling this skill with a lesson already on file in this workspace
(a saved feedback memory): a prior timed-exam run scored 9+/10 on deliverable quality
but 5/10 on time efficiency because full engineering ceremony was applied uniformly regardless of
the clock. **The same risk applies here** — Parts 2 and 3 above are the right standard for a
take-home or an "AI tools allowed, judged on engineering judgment" exercise (this workspace has
two of those on file: Datadog's live-coding round, Stampli's GTM exercise). They are **the wrong
default** for a hard-proctored, auto-graded, hidden-test-case platform sliced into ~15-minute
problems, where:

- **The harness defines the function signature and expected behavior — don't fight it.** If the
  platform calls `solution(nums)` expecting a return value on every input the test suite sends,
  raising `ValueError`/`TypeError` on inputs the harness never sends is wasted code, and raising on
  inputs it *does* send when a plain return was expected is a wrong answer, not senior judgment.
  Validate only the edge cases the problem statement or constraints section actually implies
  (empty input, a stated bound), not every input class in Part 2.1 by rote.
- **A separate `run_tests()`/`assert` block competes with the clock for no grading credit** — the
  platform's own hidden tests are the grade. A 1-2 line mental/scratch check on the visible sample
  case is worth doing; a full happy-path/boundary/error-path suite inside the submission is not,
  unless the assessment explicitly asks for tests.
- **Keep, cheaply:** a one-line type hint on the signature, a one-line Big-O comment once the
  approach is chosen, and a spoken/commented note on any real trade-off — these cost seconds and
  are the visible trace of the judgment this skill is trying to instill, without the ceremony that
  ate the clock in the prior exam.

**Rule of thumb for this shape of assessment:** spend the time on recognizing the right pattern and
getting a correct, reasonably efficient solution through the hidden tests first; add the one-line
complexity note if time remains; skip the full docstring block, exhaustive validation, and the
self-contained test harness unless the problem or platform explicitly asks for them.
