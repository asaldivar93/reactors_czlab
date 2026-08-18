# Structural Pattern Catalog

Use these patterns as adaptation guides, not templates to copy blindly.

## Contents

- Tagged mapping payloads
- Sequence parsing
- Domain objects
- State transitions
- Cases to leave as if statements
- Semantic traps
- Sources

## Tagged mapping payloads

Replace repeated mapping, key, and tag interrogation:

```python
# Before
if isinstance(message, dict) and message.get("type") == "user.created":
    if "user" not in message or "id" not in message["user"]:
        raise ValueError("missing user id")
    return create_user(message["user"]["id"])
elif isinstance(message, dict) and message.get("type") == "user.deleted":
    return delete_user(message["id"])
else:
    raise ValueError("unsupported message")
```

```python
# After
match message:
    case {"type": "user.created", "user": {"id": user_id}}:
        return create_user(user_id)
    case {"type": "user.deleted", "id": user_id}:
        return delete_user(user_id)
    case _:
        raise ValueError("unsupported or malformed message")
```

Check error compatibility: the refactor intentionally combines malformed and unsupported inputs only if callers do not require distinct errors.

Mapping patterns require the named keys but ignore additional keys. Enforce an exact mapping only when the contract requires it:

```python
match payload:
    case {"id": item_id, "name": name, **rest} if not rest:
        return Item(item_id, name)
    case _:
        raise ValueError("expected exactly id and name")
```

## Sequence parsing

Replace length checks, indexing, and unpacking:

```python
# Before
parts = command.split()
if len(parts) == 1 and parts[0] == "quit":
    return quit_session()
elif len(parts) == 3 and parts[0] == "move":
    return move(parts[1], parts[2])
elif len(parts) >= 2 and parts[0] == "drop":
    return drop(parts[1:])
raise ValueError("unknown command")
```

```python
# After
match command.split():
    case ["quit"]:
        return quit_session()
    case ["move", x, y]:
        return move(x, y)
    case ["drop", first, *rest]:
        return drop([first, *rest])
    case _:
        raise ValueError("unknown command")
```

Use a literal prefix to distinguish variants. A pattern such as `[action, value]` captures any two-item sequence; it does not compare against existing variables named `action` or `value`.

## Domain objects

Replace repeated type and attribute checks across a closed family of domain objects:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Paid:
    invoice_id: str
    cents: int

@dataclass(frozen=True)
class Refunded:
    invoice_id: str
    cents: int


def summarize(event: Paid | Refunded) -> str:
    match event:
        case Paid(invoice_id=invoice_id, cents=cents):
            return f"paid {invoice_id}: {cents}"
        case Refunded(invoice_id=invoice_id, cents=cents) if cents > 0:
            return f"refunded {invoice_id}: {cents}"
        case Refunded():
            raise ValueError("refund must be positive")
```

Prefer keyword class patterns. Positional class patterns depend on `__match_args__`, which can make attribute order part of an accidental API.

Do not replace healthy polymorphism merely to centralize it. If each event naturally owns its behavior, `event.summarize()` may be the better design.

## State transitions

Match a tuple when the combination is the domain decision:

```python
def transition(state: State, event: Event) -> State:
    match state, event:
        case State.IDLE, Start(job_id=job_id):
            return Running(job_id)
        case State.RUNNING, Stop():
            return State.IDLE
        case State.FAILED, Retry() if can_retry():
            return State.RUNNING
        case _, _:
            raise InvalidTransition(state, event)
```

Use qualified enum members. Bare `IDLE` or `RUNNING` would be capture patterns, not constant comparisons.

## Cases to leave as if statements

Keep direct predicates direct:

```python
if temperature < 0:
    warn_freezing()
elif temperature > 35:
    warn_heat()
```

A `match` version would need guards and add no structural clarity.

Keep a simple binary decision direct:

```python
return cache[value] if value in cache else compute(value)
```

Prefer a dispatch table when branches only map stable keys to callables and do not destructure:

```python
HANDLERS = {"start": start, "stop": stop}
handler = HANDLERS.get(action)
if handler is None:
    raise ValueError(f"unknown action: {action}")
return handler()
```

## Semantic traps

### First match wins

Place specific cases before general ones:

```python
match response:
    case {"status": 200, "body": body}:
        return body
    case {"status": status} if 200 <= status < 300:
        return None
    case {"status": status}:
        raise HttpError(status)
```

### Bare names capture

This does not compare with the outer `expected` value:

```python
match status:
    case expected:  # captures status and always matches
        ...
```

Use a guard for a dynamic value or a qualified constant for a stable one:

```python
match status:
    case value if value == expected:
        ...
    case Status.READY:
        ...
```

### Mapping patterns are subsets

`case {"kind": "point", "x": x}:` accepts additional keys. This is often desirable for evolving JSON APIs, but it can silently broaden validation if the old code required exact keys.

### Sequence exclusions

List-like sequence patterns match supported sequence objects but intentionally exclude `str`, `bytes`, and `bytearray`. Normalize text first when parsing tokens.

### OR bindings

Every alternative must bind the same set of names:

```python
case {"id": item_id} | {"item_id": item_id}:
    return item_id
```

### Guards and bindings

Evaluate a guard only after its pattern succeeds. Keep guards free of mutations and do not rely on names left behind by failed alternatives. A successful binding uses the surrounding local scope rather than a case-local scope.

### Equality overlap

Literal patterns generally compare by equality, while `True`, `False`, and `None` use identity. Numeric equality can still create overlap: for example, `True == 1`. Put cases in an intentional order and test such inputs when both booleans and numbers are accepted.

## Sources

- [Python language reference: the match statement](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement)
- [PEP 636: Structural Pattern Matching tutorial](https://peps.python.org/pep-0636/)
- [Inspired Python: Mastering Structural Pattern Matching](https://www.inspiredpython.com/course/pattern-matching/mastering-structural-pattern-matching)
