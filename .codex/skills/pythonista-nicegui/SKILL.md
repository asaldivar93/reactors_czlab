---
name: pythonista-nicegui
description: Use when building UI with NiceGUI, creating components, fixing styling issues. Triggers on "nicegui", "quasar", "tailwind", "ui.row", "ui.column", "ui.card", "ui.dialog", "gap", "spacing", "layout", "modal", "component", "styling", "flexbox", "chart", or when creating/editing UI code.
metadata:
  mcpmarket-version: 1.0.0
---
# NiceGUI Development Best Practices

## Core Philosophy

**Understand the user workflow before building. Same data MUST look the same everywhere. Extract business logic from UI into testable controllers.**

## Key Points From References

1. **No threads for UI** - Only use asyncio (see ui-architecture.md)
2. **Three-layer architecture**: Data fetching → Controller → UI (see ui-architecture.md)
3. **Progressive complexity**: Simple pages → Controllers → State machines (see ui-architecture.md)

## Quick Start

```bash
# In Claude Code, use Grep tool:
# pattern="ui.card" path="src/"
# pattern="ui.dialog" path="src/"

# Check for existing components
ls src/*/ui/components/
```

## Critical Styling Rules

```python
# ALWAYS use inline style for gap (NiceGUI bug #2171)
with ui.row().classes("items-center").style("gap: 0.75rem"):
    ui.icon("info")
    ui.label("Message")

# NEVER use Tailwind gap-* classes - breaks on Ubuntu!
# Gap conversion: gap-2->0.5rem, gap-3->0.75rem, gap-4->1rem

# CORRECT: Explicit height for percentage children
with ui.card().style("height: 80vh"):
    with ui.scroll_area().style("height: 100%"):
        ui.label("Content")

# NEVER use height: 100% inside max-height container (collapses to 0!)

# Side-by-side with charts: use min-width: 0
with ui.element("div").style("display: flex; width: 100%; gap: 24px"):
    with ui.element("div").style("flex: 1; min-width: 0"):
        ui.highchart(options)
```

## Controller Pattern

```python
# Extract business logic to controller
class PageController:
    async def handle_task_change(self, new_task: str) -> PageUpdate:
        data = await self.fetcher.fetch_data(new_task)
        return PageUpdate.refresh_all(data)

# UI layer is thin - delegates to controller
async def on_task_change(e):
    logger.info(f"User selected task: {e.value}")
    update = await controller.handle_task_change(e.value)
    apply_ui_update(update)

# NEVER put business logic in UI handlers
```

## Component Reuse

```python
# BEFORE building any data display, answer:
# 1. Where else does this data type appear?
# 2. Should this be ONE component with modes?
# 3. Can user navigate from reference to source?

# Create reusable component with modes
class ItemReference:
    def __init__(self, item, mode: Literal["LIBRARY", "REFERENCE", "PREVIEW"]):
        if mode == "LIBRARY":
            # Full card with edit actions
        elif mode == "REFERENCE":
            # Compact with navigation to source
```

## Modal/Dialog Button Docking

```python
# Primary actions MUST be always visible
with ui.dialog() as dialog, ui.card().style(
    "height: 85vh; display: flex; flex-direction: column;"
):
    with ui.scroll_area().style("flex: 1; overflow-y: auto;"):
        # ... form content ...

    # Sticky bottom action bar
    with ui.element("div").style(
        "position: sticky; bottom: 0; padding: 1rem;"
    ):
        with ui.row().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close)
            ui.button("Save", on_click=save_handler)
```

## Checklists

### Styling Checklist
- [ ] No `gap-*` Tailwind classes (use inline style)
- [ ] No `height: 100%` inside `max-height` container
- [ ] Side-by-side layouts use `min-width: 0` on flex children
- [ ] Modal buttons are docked (always visible)

### Component Checklist
- [ ] Listed ALL locations where this data appears
- [ ] Designed component with modes for each context
- [ ] Same icon, typography, color coding everywhere

### Architecture Checklist
- [ ] Business logic in controller, not UI handlers
- [ ] Data fetching returns Pydantic models
- [ ] All user actions logged at start of handlers

## Reference Files

- [references/nicegui-styling.md](references/nicegui-styling.md) - Gap spacing, height issues, flexbox
- [references/product-thinking.md](references/product-thinking.md) - Component design, data display
- [references/ui-architecture.md](references/ui-architecture.md) - Controllers, state management

## Related Skills

- [/pythonista-typing](../pythonista-typing/SKILL.md) - Pydantic models for UI data
- [/pythonista-testing](../pythonista-testing/SKILL.md) - Testing controllers
- [/pythonista-async](../pythonista-async/SKILL.md) - Async UI patterns
- [/pythonista-patterning](../pythonista-patterning/SKILL.md) - Component reuse patterns
