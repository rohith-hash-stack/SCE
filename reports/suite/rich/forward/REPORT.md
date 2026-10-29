# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 10 / 22 | 8 | 29 | 0.43 | 0.53 | 9 / 10 |
| all | PRISM | 20 | 19 / 22 | 17 | 5 | 0.31 | 0.61 | 18 / 19 |
| all | PRISM_BREAKS | 20 | 20 / 22 | 18 | 2 | 0.22 | 0.6 | 19 / 20 |
| easy | FILE | 13 | 9 / 14 | 8 | 13 | 0.37 | 0.47 | 8 / 9 |
| easy | PRISM | 13 | 12 / 14 | 11 | 2 | 0.37 | 0.63 | 11 / 12 |
| easy | PRISM_BREAKS | 13 | 12 / 14 | 11 | 2 | 0.32 | 0.57 | 11 / 12 |
| medium | FILE | 7 | 1 / 8 | 0 | 16 | 1.0 | 1.0 | 1 / 1 |
| medium | PRISM | 7 | 7 / 8 | 6 | 3 | 0.22 | 0.58 | 7 / 7 |
| medium | PRISM_BREAKS | 7 | 8 / 8 | 7 | 0 | 0.08 | 0.63 | 8 / 8 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| rich-forward-001 | easy | rich | `rich.markdown.MarkdownElement.on_leave` | 1 | 0 | 1 | 1 |
| rich-forward-002 | easy | rich | `rich.pretty._Line.expand` | 1 | 0 | 1 | 1 |
| rich-forward-003 | easy | rich | `rich.table.Table._get_cells` | 2 | 2 | 2 | 2 |
| rich-forward-004 | easy | rich | `rich.progress.Progress.open` | 1 | 0 | 0 | 0 |
| rich-forward-005 | easy | rich | `rich.progress.TaskProgressColumn.render_speed` | 1 | 1 | 1 | 1 |
| rich-forward-006 | easy | rich | `rich.table.Table.add_padding` | 1 | 1 | 1 | 1 |
| rich-forward-007 | easy | rich | `rich.tui.message_pump.MessagePump.dispatch_message` | 1 | 0 | 1 | 1 |
| rich-forward-008 | medium | rich | `rich.box.Box.get_bottom` | 1 | 0 | 1 | 1 |
| rich-forward-009 | medium | rich | `rich.jupyter.display` | 1 | 0 | 1 | 1 |
| rich-forward-010 | medium | rich | `rich.cells.chop_cells` | 1 | 0 | 1 | 1 |
| rich-forward-011 | medium | rich | `rich.text.Text.render` | 2 | 1 | 2 | 2 |
| rich-forward-012 | medium | rich | `rich.box.Box.get_row` | 1 | 0 | 1 | 1 |
| rich-forward-013 | medium | rich | `rich.console.Console.set_live` | 1 | 0 | 1 | 1 |
| rich-forward-014 | medium | rich | `rich.console.Console.print_json` | 1 | 0 | 0 | 1 |
| rich-forward-015 | easy | rich | `rich._inspect.Inspect._get_signature` | 1 | 1 | 1 | 1 |
| rich-forward-016 | easy | rich | `rich.syntax.Syntax._apply_stylized_ranges` | 1 | 0 | 1 | 1 |
| rich-forward-017 | easy | rich | `rich.pretty.append_text` | 1 | 1 | 0 | 0 |
| rich-forward-018 | easy | rich | `rich.bar.Bar._get_pulse_segments` | 1 | 1 | 1 | 1 |
| rich-forward-019 | easy | rich | `rich.live._LiveRender.position_cursor` | 1 | 1 | 1 | 1 |
| rich-forward-020 | easy | rich | `rich.console.Console._render_buffer` | 1 | 1 | 1 | 1 |
