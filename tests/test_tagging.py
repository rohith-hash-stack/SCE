"""Stage 3 deterministic tagging tests (HLD section 4.2)."""


def test_auth_guard_tagged_on_raise_and_call_name(built_repo):
    _, tag_matrix = built_repo
    assert "#auth_guard" in tag_matrix["src.auth.jwt.TokenVerifier.verify"]
    assert "#auth_guard" in tag_matrix["src.auth.jwt.verify_session"]


def test_db_write_requires_call_pattern_and_import_trigger(built_repo):
    _, tag_matrix = built_repo
    assert "#db_write" in tag_matrix["src.repositories.orders.OrderRepository.mark_paid"]
    assert "#db_read" in tag_matrix["src.repositories.orders.OrderRepository.mark_paid"]
    assert "#db_read" in tag_matrix["src.repositories.orders.OrderRepository.find_open_orders"]


def test_external_io_tagged_on_requests_call(built_repo):
    _, tag_matrix = built_repo
    assert "#external_io" in tag_matrix["src.services.billing.PaymentProcessor.charge"]


def test_route_handler_tagged_via_decorator(built_repo):
    _, tag_matrix = built_repo
    assert "#route_handler" in tag_matrix["src.controllers.checkout.checkout_endpoint"]


def test_state_mutation_tagged_on_self_attribute_assignment(built_repo):
    _, tag_matrix = built_repo
    assert "#state_mutation" in tag_matrix["src.controllers.checkout.CheckoutController.__init__"]
    assert "#state_mutation" in tag_matrix["src.auth.jwt.TokenVerifier.__init__"]


def test_generic_dict_like_calls_do_not_false_positive_without_import(built_repo):
    """`find_open_orders` calls `.filter(...).all()` on `self.session`
    (sqlalchemy-style) and IS expected to tag #db_read because the file
    imports sqlalchemy. This test locks in the *reasoning*: db_read/db_write
    require a corroborating import, so a bare dict `.get()` elsewhere in a
    file with no persistence import would not be tagged - verified here by
    checking the billing module (which imports `requests`, not a DB driver)
    never produces a #db_read/#db_write tag anywhere in it."""
    _, tag_matrix = built_repo
    billing_symbols = [name for name in tag_matrix if name.startswith("src.services.billing.")]
    for name in billing_symbols:
        assert "#db_read" not in tag_matrix[name]
        assert "#db_write" not in tag_matrix[name]
