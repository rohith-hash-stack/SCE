"""Option 3 contract axis (`prism.semantics.contract_axis`) and its Turn-1
manifest field."""
from __future__ import annotations

import pytest

from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.context_ops import (
    TURN1_CONTRACT_GUIDANCE,
    annotate_manifest_axes,
    annotate_manifest_contracts,
)
from benchmarks.final_sweep.runner import turn1_system_prompt
from prism.cli import build_pipeline
from prism.semantics.contract_axis import NodeContract, compute_node_contracts, contract_field

FILES = {
    "app/__init__.py": "",
    "app/models.py": '''
class Recorder:
    def mark(self):
        self.applied = True

    def peek(self):
        return self.applied
''',
    "app/crud.py": '''
from sqlmodel import select


def create_user(*, session, user):
    session.add(user)
    session.commit()
    return user


def store(session, item):
    session.add(item)


def get_user(session, user_id):
    return session.get(User, user_id)


def list_users(session):
    statement = select(User)
    return session.exec(statement).all()


def is_admin(user) -> bool:
    return user.role == "admin"
''',
}
FILES["app/routes.py"] = '''
from fastapi import APIRouter, HTTPException

from app import crud

router = APIRouter()


@router.post("/users")
def create_route(session, user):
    if crud.get_user(session, user.id):
        raise HTTPException(status_code=400, detail="exists")
    return crud.create_user(session=session, user=user)


@router.get("/users/{user_id}")
def read_route(session, user_id):
    user = crud.get_user(session, user_id)
    if user is None:
        raise HTTPException(404)
    return user


def positional_route(session, item):
    crud.store(session, item)


def forwards_unmutated(session, user_id):
    return crud.get_user(session, user_id)


def touch(rec):
    rec.mark()


def collect(rows):
    for row in rows:
        yield row
'''

@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text.lstrip("\n"))
    builder = build_pipeline(str(root), use_cache=False)[0]
    return compute_node_contracts(builder)


def test_mutation_on_an_argument(contracts):
    c = contracts["app.crud.create_user"]
    assert c.state == ("MUTATES_ARGS",) and c.mutated_params == {"session"} and not c.delegated


def test_mutation_on_self(contracts):
    assert contracts["app.models.Recorder.mark"].state == ("MUTATES_SELF",)
    assert contracts["app.models.Recorder.peek"].state == ("READ_ONLY",)


def test_read_only_queries(contracts):
    get_user, list_users = contracts["app.crud.get_user"], contracts["app.crud.list_users"]
    assert get_user.state == ("READ_ONLY",) and get_user.ret == "OBJECT"
    assert list_users.state == ("READ_ONLY",) and list_users.ret == "COLLECTION"


def test_explicit_exceptions_with_status_codes(contracts):
    assert contracts["app.routes.read_route"].throws == (("HTTPException", ("404",)),)
    assert contracts["app.routes.create_route"].throws == (("HTTPException", ("400",)),)
    assert contracts["app.crud.get_user"].throws == ()


def test_one_hop_delegation_by_keyword_and_position(contracts):
    route = contracts["app.routes.create_route"]
    assert route.state == ("MUTATES_ARGS",) and route.delegated == ("MUTATES_ARGS",)
    assert contracts["app.routes.positional_route"].state == ("MUTATES_ARGS",)
    # a forwarded parameter the callee only reads gives nothing
    assert contracts["app.routes.forwards_unmutated"].state == ("READ_ONLY",)
    # a self-mutating method called on a forwarded parameter
    assert contracts["app.routes.touch"].state == ("MUTATES_ARGS",)


def test_delegation_is_one_hop_and_can_be_switched_off(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text.lstrip("\n"))
    local_only = compute_node_contracts(build_pipeline(str(tmp_path), use_cache=False)[0], propagate=False)
    assert local_only["app.routes.create_route"].state == ("READ_ONLY",)


def test_return_kinds_and_routes(contracts):
    assert contracts["app.crud.is_admin"].ret == "BOOL"
    assert contracts["app.routes.collect"].ret == "COLLECTION"
    assert contracts["app.routes.positional_route"].ret == "NONE"
    assert contracts["app.routes.create_route"].entry == "ROUTE"
    assert contracts["app.routes.read_route"].entry == "ROUTE"
    assert contracts["app.routes.touch"].entry is None


def test_contract_field_format():
    c = NodeContract(state=("MUTATES_ARGS",), throws=(("HTTPException", ("400", "404")), ("ValueError", ())),
                     ret="OBJECT", entry="ROUTE")
    assert contract_field(c) == "contract=[state=MUTATES_ARGS throws=HTTPException[400,404],ValueError ret=OBJECT entry=ROUTE]"
    assert contract_field(NodeContract(state=("READ_ONLY",), throws=(), ret="NONE")) == \
        "contract=[state=READ_ONLY throws=NOTHROW ret=NONE entry=NONE]"


def test_manifest_annotation_and_prompt():
    manifest = "<candidate_index>\npkg.a|seed|function|def a():|calls=[]\n</candidate_index>"
    with_axes = annotate_manifest_axes(manifest, {}, "no_substance")
    out = annotate_manifest_contracts(with_axes, {"pkg.a": NodeContract(state=("READ_ONLY",), throws=(), ret="NONE")})
    row = out.splitlines()[1]
    assert "S:" not in row and row.endswith("|contract=[state=READ_ONLY throws=NOTHROW ret=NONE entry=NONE]")
    plain, contract = turn1_system_prompt("no_substance"), turn1_system_prompt("no_substance", contract_axis=True)
    assert "|contract=[" not in plain and TURN1_CONTRACT_GUIDANCE.strip() not in plain
    assert TURN1_CONTRACT_GUIDANCE.strip() in contract and "|contract=[" in contract


def test_contract_arm_differs_from_prism_full_only_in_substance_and_the_contract():
    full, arm = C.resolve_arm("prism_full"), C.resolve_arm("prism_contract")
    assert arm.contract_axis and not full.contract_axis and arm.axes == "no_substance" and full.axes == "all"
    assert (arm.kind, arm.anchor, arm.distractor_k, arm.atomic_caps, arm.turn1_rule_callers) == (
        full.kind, full.anchor, full.distractor_k, False, False)
