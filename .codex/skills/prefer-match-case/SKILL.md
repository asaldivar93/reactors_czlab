---
name: prefer-match-case
description: Prefer and apply Python structural pattern matching when writing, reviewing, or refactoring branching code that repeatedly inspects one subject's type, shape, keys, attributes, literals, or nested contents. Use for complex if/elif chains, nested isinstance/key/length checks, parsing structured data, tagged messages or API payloads, state/event transitions, sequence and path decomposition, and heterogeneous dataclass or domain-object handling. Preserve ordinary if statements when predicates are simple, unrelated, range-heavy, or clearer without destructuring. Require Python 3.10 or newer.
---

# Prefer Match/Case

Use `match` to state structural alternatives and bind their useful parts in one place. Prefer it when it removes an ad hoc pattern matcher made from nested `if`, `isinstance`, membership, indexing, and attribute checks. Do not convert branching mechanically.

## Decide

Choose `match` when most of these are true:

- Branch on the same subject or a meaningful tuple of subjects.
- Distinguish alternatives by literals, types, sequence length, mapping keys, object attributes, or nested structure.
- Extract values only after confirming their structure.
- Replace at least three alternatives or materially reduce nested checks.
- Express cases as recognizable domain forms such as commands, events, payload variants, syntax-tree nodes, or file-path layouts.

Keep `if`/`elif` when any of these dominate:

- Handle one simple binary decision.
- Test unrelated conditions rather than one coherent subject.
- Compare ranges or combine arbitrary boolean predicates.
- Need many guards because structure does not drive the decision.
- A lookup table, polymorphic method, `singledispatch`, validation model, or exception handler expresses the design more directly.
- Support Python older than 3.10.

Treat these as design signals, not quotas. Leave clear existing code alone when `match` would merely change syntax.

## Refactor

1. Confirm the project's minimum Python version and local style.
2. Preserve behavior with existing tests or add focused characterization tests before changing ambiguous logic.
3. Identify the subject and write down each accepted structural alternative.
4. Order cases from most specific to most general because only the first successful case runs.
5. Express structure in patterns; reserve guards for constraints that patterns cannot express clearly.
6. Bind only values the selected case uses. Return or assign explicitly inside each case.
7. Preserve the old fallback behavior with a final `case _:` when unmatched input must be handled. Omit it only when a no-op is intentional.
8. Test every case, overlapping cases, boundary values, malformed shapes, extra mapping keys, and the fallback.

Prefer small case bodies. Extract substantial effects or algorithms into named functions so the `match` block remains a readable dispatcher.

## Select Patterns

- Use literal and OR patterns for tagged alternatives: `case "start" | "resume":`.
- Use sequence patterns to validate length and unpack simultaneously: `case ["move", x, y]:`.
- Use mapping patterns for required keys and nested payload shapes: `case {"type": "paid", "invoice": {"id": invoice_id}}:`.
- Use class patterns for typed domain variants. Prefer keyword attributes, such as `case Retry(delay=delay):`, over positional patterns tied to `__match_args__`.
- Use `as` to retain the whole matched value as well as its parts.
- Use a guard after a structural match for a non-structural constraint: `case Order(total=total) if total > limit:`.
- Match `(state, event)` when the pair is the actual domain subject for a transition.

Read [references/pattern-catalog.md](references/pattern-catalog.md) for before/after examples and semantic traps when performing a non-trivial refactor.

## Guard Correctness

- Never use a bare name as a constant pattern. `case expected:` captures every subject. Use a literal or qualified value such as `case Status.READY:`.
- Put capture-only and wildcard cases last; they are irrefutable.
- Remember that mapping patterns accept extra keys. Capture `**rest` and guard with `if not rest` only when exact keys are required.
- Remember that sequence patterns do not match `str`, `bytes`, or `bytearray`.
- Make every OR alternative bind the same names.
- Avoid side effects in subjects, properties used by class patterns, and guards. Keep matching declarative.
- Do not depend on bindings from a failed pattern or failed guard. Do not assume `match` provides exhaustiveness checking.
- Watch overlapping equality, especially numeric values and booleans, and place/test cases deliberately.
- Avoid positional class patterns for externally owned classes unless their `__match_args__` contract is intentional and stable.

## Review the Result

Reject or simplify a refactor when it introduces duplicated case bodies, opaque nesting, guard-heavy logic, surprising captures, or broader accepted input than before. Prefer explicit errors for malformed external data. Run the narrowest relevant formatter, type checker, and tests available in the repository.
