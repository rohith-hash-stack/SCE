# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 24 / 48 | 7 | 23 | 0.62 | 0.89 | 23 / 24 |
| all | PRISM | 20 | 44 / 48 | 18 | 12 | 0.64 | 0.9 | 44 / 44 |
| all | PRISM_BREAKS | 20 | 46 / 48 | 18 | 11 | 0.55 | 0.84 | 45 / 46 |
| easy | FILE | 7 | 5 / 9 | 4 | 9 | 0.3 | 0.7 | 5 / 5 |
| easy | PRISM | 7 | 8 / 9 | 6 | 6 | 0.44 | 0.81 | 8 / 8 |
| easy | PRISM_BREAKS | 7 | 8 / 9 | 6 | 5 | 0.44 | 0.81 | 8 / 8 |
| medium | FILE | 6 | 3 / 10 | 0 | 7 | 0.56 | 1.0 | 3 / 3 |
| medium | PRISM | 6 | 10 / 10 | 6 | 1 | 0.6 | 0.72 | 10 / 10 |
| medium | PRISM_BREAKS | 6 | 10 / 10 | 6 | 1 | 0.33 | 0.57 | 9 / 10 |
| hard | FILE | 7 | 16 / 29 | 3 | 7 | 0.74 | 0.93 | 15 / 16 |
| hard | PRISM | 7 | 26 / 29 | 6 | 5 | 0.71 | 1.0 | 26 / 26 |
| hard | PRISM_BREAKS | 7 | 28 / 29 | 6 | 5 | 0.66 | 0.94 | 28 / 28 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| marshmallow-forward-001 | easy | marshmallow | `src.marshmallow.validate.RegexMemoizer._regex_generator` | 1 | 0 | 0 | 1 |
| marshmallow-forward-002 | easy | marshmallow | `src.marshmallow.schema._get_fields` | 1 | 1 | 1 | 1 |
| marshmallow-forward-003 | easy | marshmallow | `marshmallow.marshalling.ErrorStore.store_error` | 2 | 0 | 2 | 1 |
| marshmallow-forward-004 | easy | marshmallow | `marshmallow.fields.Unmarshaller._validate` | 1 | 1 | 1 | 1 |
| marshmallow-forward-005 | easy | marshmallow | `marshmallow.decorators.tag_processor` | 1 | 1 | 1 | 1 |
| marshmallow-forward-006 | easy | marshmallow | `src.marshmallow.schema._get_fields` | 1 | 1 | 1 | 1 |
| marshmallow-forward-007 | easy | marshmallow | `marshmallow.marshalling.ErrorStore.store_validation_error` | 2 | 1 | 2 | 2 |
| marshmallow-forward-008 | medium | marshmallow | `src.marshmallow.types.RenderModule.loads` | 1 | 0 | 1 | 1 |
| marshmallow-forward-009 | medium | marshmallow | `marshmallow.base.FieldABC.output` | 2 | 0 | 2 | 2 |
| marshmallow-forward-010 | medium | marshmallow | `marshmallow.serializer.BaseSerializer._update_fields` | 2 | 1 | 2 | 2 |
| marshmallow-forward-011 | medium | marshmallow | `marshmallow.fields.Field.deserialize` | 1 | 0 | 1 | 1 |
| marshmallow-forward-012 | medium | marshmallow | `marshmallow.schema.BaseSchema.load` | 2 | 1 | 2 | 2 |
| marshmallow-forward-013 | medium | marshmallow | `marshmallow.serializer.BaseSerializer._update_fields` | 2 | 1 | 2 | 2 |
| marshmallow-forward-014 | hard | marshmallow | `src.marshmallow.fields.Number._validated` | 3 | 3 | 3 | 3 |
| marshmallow-forward-015 | hard | marshmallow | `marshmallow.fields.Nested._serialize` | 5 | 2 | 5 | 5 |
| marshmallow-forward-016 | hard | marshmallow | `src.marshmallow.utils.UTC.localize` | 3 | 0 | 0 | 2 |
| marshmallow-forward-017 | hard | marshmallow | `marshmallow.fields.Arbitrary._validated` | 4 | 2 | 4 | 4 |
| marshmallow-forward-018 | hard | marshmallow | `src.marshmallow.fields.Field._bind_to_schema` | 5 | 0 | 5 | 5 |
| marshmallow-forward-019 | hard | marshmallow | `marshmallow.decorators.tag_processor` | 5 | 5 | 5 | 5 |
| marshmallow-forward-020 | hard | marshmallow | `src.marshmallow.decorators.set_hook` | 4 | 4 | 4 | 4 |
