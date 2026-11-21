# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 4 / 23 | 4 | 20 | 1.0 | 0.75 | 4 / 4 |
| all | PRISM | 20 | 9 / 23 | 9 | 18 | 0.48 | 0.63 | 6 / 9 |
| all | PRISM_BREAKS | 20 | 9 / 23 | 9 | 15 | 0.55 | 0.58 | 7 / 9 |
| easy | FILE | 16 | 4 / 17 | 4 | 17 | 1.0 | 0.75 | 4 / 4 |
| easy | PRISM | 16 | 7 / 17 | 7 | 18 | 0.55 | 0.59 | 5 / 7 |
| easy | PRISM_BREAKS | 16 | 7 / 17 | 7 | 15 | 0.62 | 0.6 | 6 / 7 |
| medium | FILE | 3 | 0 / 3 | 0 | 2 | None | None | 0 / 0 |
| medium | PRISM | 3 | 2 / 3 | 2 | 0 | 0.25 | 0.75 | 1 / 2 |
| medium | PRISM_BREAKS | 3 | 2 / 3 | 2 | 0 | 0.33 | 0.5 | 1 / 2 |
| hard | FILE | 1 | 0 / 3 | 0 | 1 | None | None | 0 / 0 |
| hard | PRISM | 1 | 0 / 3 | 0 | 0 | None | None | 0 / 0 |
| hard | PRISM_BREAKS | 1 | 0 / 3 | 0 | 0 | None | None | 0 / 0 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| mocha-forward-001 | easy | mocha | `lib.reporters.base.pluralizeTest` | 1 | 0 | 1 | 1 |
| mocha-forward-002 | easy | mocha | `lib.reporters.xunit.test` | 1 | 1 | 0 | 1 |
| mocha-forward-003 | easy | mocha | `lib.reporters.base.inlineDiff` | 1 | 0 | 0 | 0 |
| mocha-forward-004 | easy | mocha | `mocha.errorDiff` | 1 | 0 | 1 | 1 |
| mocha-forward-005 | easy | mocha | `lib.runner.runTests` | 1 | 0 | 0 | 0 |
| mocha-forward-006 | easy | mocha | `lib.nodejs.esm-utils.formattedImport` | 1 | 0 | 1 | 0 |
| mocha-forward-007 | easy | mocha | `lib.reporters.base.inlineDiff` | 1 | 0 | 1 | 0 |
| mocha-forward-008 | medium | mocha | `lib.test.run` | 1 | 0 | 0 | 0 |
| mocha-forward-009 | medium | mocha | `lib.cli.node-flags.isNodeFlag` | 1 | 0 | 1 | 1 |
| mocha-forward-010 | medium | mocha | `lib.cli.run-helpers.handleRequires` | 1 | 0 | 1 | 1 |
| mocha-forward-011 | hard | mocha | `lib.runner.next` | 3 | 0 | 0 | 0 |
| mocha-forward-012 | easy | mocha | `mocha.reporter` | 1 | 1 | 1 | 1 |
| mocha-forward-013 | easy | mocha | `lib.runner.filterLeaks` | 1 | 0 | 0 | 0 |
| mocha-forward-014 | easy | mocha | `lib.runner.fail` | 2 | 0 | 0 | 0 |
| mocha-forward-015 | easy | mocha | `lib.mocha.reporter` | 1 | 1 | 1 | 1 |
| mocha-forward-016 | easy | mocha | `lib.runner.checkGlobals` | 1 | 1 | 1 | 1 |
| mocha-forward-017 | easy | mocha | `lib.runner.done` | 1 | 0 | 0 | 1 |
| mocha-forward-018 | easy | mocha | `mocha.growl` | 1 | 0 | 0 | 0 |
| mocha-forward-019 | easy | mocha | `lib.reporters.nyan.drawNyanCat` | 1 | 0 | 0 | 0 |
| mocha-forward-020 | easy | mocha | `lib.interfaces.qunit.only` | 1 | 0 | 0 | 0 |
