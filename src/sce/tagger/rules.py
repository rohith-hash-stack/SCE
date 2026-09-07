"""Deterministic AST-to-tag mapping definitions (HLD section 4.2).

Every rule is a pure, static predicate over three signal sources gathered
from a single function/method's own subtree plus its file's import list:

  - decorator text (for `#route_handler` / `#event_consumer`)
  - call-sink identifiers, i.e. the trailing method name of a call chain
    (`.execute()`, `.commit()`, ...)
  - raised exception type names
  - assignment left-hand-side shape (`self.*` / `this.*`)

For call-sink tags that key off generic method names (`.get()`, `.filter()`,
...), a matching import trigger is also required - this mirrors the design
document's own diagram, which merges "Import Inspection" and "Call Sink
Inspection" into a single tag decision, and keeps a bare dict's `.get()`
from being mistaken for `#external_io`. `#auth_guard` is the one exception:
identity/permission checks are common in code that doesn't import any
particular auth library, so its predicates fire on AST shape alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CallSinkRule:
    tag: str
    method_patterns: tuple[str, ...]
    import_triggers: tuple[str, ...] = ()
    require_import: bool = True


@dataclass(frozen=True)
class DecoratorRule:
    tag: str
    decorator_patterns: tuple[str, ...]
    import_triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthGuardRule:
    tag: str = "#auth_guard"
    exception_name_patterns: tuple[str, ...] = ("Auth", "Permission", "Forbidden")
    call_name_patterns: tuple[str, ...] = ("verify", "authenticate")


CALL_SINK_RULES: tuple[CallSinkRule, ...] = (
    CallSinkRule(
        tag="#db_write",
        method_patterns=("commit", "execute", "save", "insert", "update", "delete", "persist"),
        import_triggers=("sqlalchemy", "prisma", "psycopg2", "sql"),
    ),
    CallSinkRule(
        tag="#db_read",
        method_patterns=("find_one", "filter", "query", "select", "all", "first"),
        import_triggers=("sqlalchemy", "prisma", "pymongo"),
    ),
    CallSinkRule(
        tag="#external_io",
        method_patterns=("get", "post", "put", "send", "request", "fetch"),
        import_triggers=("requests", "httpx", "aiohttp", "fetch"),
    ),
    CallSinkRule(
        tag="#event_producer",
        method_patterns=("emit", "publish", "send_message"),
        import_triggers=("celery", "kafka", "pika", "redis"),
    ),
)

DECORATOR_RULES: tuple[DecoratorRule, ...] = (
    DecoratorRule(
        tag="#route_handler",
        decorator_patterns=("app.get", "app.post", "app.put", "app.delete", "app.route", "router."),
        import_triggers=("fastapi", "flask", "express"),
    ),
    DecoratorRule(
        tag="#event_consumer",
        decorator_patterns=("subscribe", "consumer", "app.task"),
        import_triggers=("celery", "kafka", "pika", "redis"),
    ),
)

AUTH_GUARD_RULE = AuthGuardRule()

# Assignment-shape tag: `self.*` / `this.*` on the left-hand side of an
# assignment. Pure syntax primitive, no import corroboration needed.
STATE_MUTATION_TAG = "#state_mutation"


def import_roots(import_module_texts: set[str]) -> set[str]:
    """Reduce a set of raw import specifiers to their top-level package
    name, so `import sqlalchemy.orm` still satisfies an `sqlalchemy` trigger.
    """
    roots = set()
    for text in import_module_texts:
        first = text.split(".")[0].split("/")[0]
        if first:
            roots.add(first.lower())
    return roots
