"""Pass 1 / Pass 2 linker tests: global definition collection and the
Rule A-D call resolution described in HLD section 4.1.
"""


def test_pass1_collects_every_definition(built_repo):
    builder, _ = built_repo
    names = set(builder.symbol_table.all_qualified_names())

    assert "src.auth.jwt.TokenVerifier" in names
    assert "src.auth.jwt.TokenVerifier.verify" in names
    assert "src.auth.jwt.verify_session" in names
    assert "src.services.billing.PaymentProcessor.charge" in names
    assert "src.repositories.orders.OrderRepository.mark_paid" in names


def test_pass1_classifies_kinds_and_enclosing_class(built_repo):
    builder, _ = built_repo
    table = builder.symbol_table

    cls = table.get("src.auth.jwt.TokenVerifier")
    method = table.get("src.auth.jwt.TokenVerifier.verify")
    function = table.get("src.auth.jwt.verify_session")

    assert cls.kind == "class"
    assert method.kind == "method"
    assert method.enclosing_class == "src.auth.jwt.TokenVerifier"
    assert function.kind == "function"
    assert function.enclosing_class is None


def test_rule_b_bare_call_resolves_via_import(built_repo):
    """`verify_session(token)` inside process_checkout: bare call resolved
    through the file's `from ... import verify_session` (Rule B)."""
    builder, _ = built_repo
    assert builder.graph.has_edge(
        "src.controllers.checkout.CheckoutController.process_checkout",
        "src.auth.jwt.verify_session",
    )


def test_rule_a_instance_binding_resolves_method_call(built_repo):
    """`self.payment_processor.charge(...)`: `self.payment_processor` is
    bound to `PaymentProcessor` in `__init__` (Rule A, extended to
    `self.<attr>` instance bindings), so the call resolves to the class's
    `charge` method rather than staying an opaque attribute access."""
    builder, _ = built_repo
    assert builder.graph.has_edge(
        "src.controllers.checkout.CheckoutController.process_checkout",
        "src.services.billing.PaymentProcessor.charge",
    )
    assert builder.graph.has_edge(
        "src.controllers.checkout.CheckoutController.process_checkout",
        "src.repositories.orders.OrderRepository.mark_paid",
    )


def test_rule_c_self_method_call_resolves_within_class(built_repo):
    """`verifier.verify(token)` inside `verify_session`: `verifier` is a
    locally constructed `TokenVerifier` instance (Rule A, function-scoped
    instance binding)."""
    builder, _ = built_repo
    assert builder.graph.has_edge("src.auth.jwt.verify_session", "src.auth.jwt.TokenVerifier.verify")


def test_constructor_calls_are_recorded_but_not_misbound(built_repo):
    """`CheckoutController.__init__` calling `PaymentProcessor(http_client)`
    is itself a CALLS edge to the class; `charge`'s own `requests.post(...)`
    call must not be misread as constructing a class (regression test for
    the instance-map false-positive on external function calls)."""
    builder, _ = built_repo
    assert builder.graph.has_edge(
        "src.controllers.checkout.CheckoutController.__init__",
        "src.services.billing.PaymentProcessor",
    )
    successors = set(builder.graph.successors("src.services.billing.PaymentProcessor.charge"))
    assert "requests.post" in successors
    assert "requests.post.json" not in successors


def test_unresolvable_call_is_not_added_as_an_edge(built_repo):
    """`self.client.is_healthy()` - `client` is an opaque constructor
    parameter, not something the deterministic linker can resolve - must
    not appear as a target anywhere in the graph."""
    builder, _ = built_repo
    all_targets = {v for _, v in builder.graph.edges()}
    assert not any(target.endswith("is_healthy") for target in all_targets)
