"""Test-framework repositories: Playwright/Jest-style JS/TS test blocks,
Robot Framework suites and keyword libraries, and the `prism.blast_radius`
MCP tool, on the fixture repo `tests/fixtures/test_frameworks`."""
import os
import shutil
import textwrap
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError

from prism.cli import build_pipeline
from prism.graph.robot_framework import parse_robot_file
from prism.graph.symbol_table import SymbolRole
from prism.packer.candidate_index import build_candidate_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "test_frameworks"

UI = "ui.tests.login.spec"
LOGIN = "ui.pages.LoginPage.LoginPage"
DASH = "ui.pages.DashboardPage.DashboardPage"
SUITE = "api.tests.users"
RES = "api.resources.common"
LIB = "api.libraries.UserApi.UserApi"
AUTH = "api.libraries.auth_helpers"


def _copy(tmp_path) -> str:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    return str(repo)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    repo = _copy(tmp_path_factory.mktemp("tf"))
    builder, _ = build_pipeline(repo, use_cache=False)
    return repo, builder


def _calls(builder, caller):
    return {v for v in builder.graph.successors(caller)
            if builder.graph.edges[caller, v].get("relation") in ("CALLS", "INSTANTIATES")}


# ---------------------------------------------------------------- Playwright
def test_playwright_tests_hooks_and_fixtures_are_verification_symbols(built):
    _repo, builder = built
    expected = {
        f"{UI}.login.beforeEach", f"{UI}.login.valid_user_sees_greeting", f"{UI}.login.explicit_page_object",
        f"{UI}.logout_returns_to_login", "ui.fixtures.base.fixture_loginPage", "ui.fixtures.base.fixture_dashboardPage",
    }
    for qname in expected:
        info = builder.symbol_table.get(qname)
        assert info is not None and info.kind == "function" and info.role == SymbolRole.VERIFICATION, qname
        assert builder.def_node(qname) is not None


def test_playwright_calls_resolve_through_fixtures_new_and_hooks(built):
    _repo, builder = built
    assert _calls(builder, f"{UI}.login.beforeEach") == {f"{LOGIN}.goto"}
    assert {f"{LOGIN}.login", f"{DASH}.greeting"} <= _calls(builder, f"{UI}.login.valid_user_sees_greeting")
    assert _calls(builder, f"{UI}.logout_returns_to_login") == {f"{LOGIN}.login", f"{DASH}.logout"}
    assert _calls(builder, f"{UI}.login.explicit_page_object") == {LOGIN, f"{LOGIN}.goto", f"{LOGIN}.login"}
    assert _calls(builder, "ui.fixtures.base.fixture_loginPage") == {LOGIN}
    for caller in (f"{UI}.login.valid_user_sees_greeting", f"{UI}.logout_returns_to_login"):
        assert "kind" not in builder.graph.edges[caller, f"{LOGIN}.login"]       # typed by fixture: confident


def test_call_on_another_object_is_not_a_self_recursion_guess(built):
    _repo, builder = built
    # LoginPage.goto calls `this.page.goto(...)` (Playwright's Page.goto)
    assert not builder.graph.has_edge(f"{LOGIN}.goto", f"{LOGIN}.goto")


def test_typed_typescript_parameter_binds_its_class(tmp_path):
    repo = _copy(tmp_path)
    Path(repo, "ui", "helpers.ts").write_text(textwrap.dedent("""
        import { LoginPage } from './pages/LoginPage';
        export async function submitWith(lp: LoginPage) {
          await lp.submit();
        }
    """))
    builder, _ = build_pipeline(repo, use_cache=False)
    assert builder.graph.has_edge("ui.helpers.submitWith", f"{LOGIN}.submit")


def test_block_names_are_unique_and_jest_style_blocks_are_found(tmp_path):
    repo = _copy(tmp_path)
    Path(repo, "ui", "tests", "cart.test.ts").write_text(textwrap.dedent("""
        import { LoginPage } from '../pages/LoginPage';
        describe('cart', () => {
          beforeEach(() => {});
          beforeEach(() => {});
          it('adds an item', async () => {
            const p = new LoginPage(null as any);
            await p.submit();
          });
          it.skip('adds an item', () => {});
        });
    """))
    builder, _ = build_pipeline(repo, use_cache=False)
    names = {s.qualified_name for s in builder.symbol_table if s.module == "ui.tests.cart.test"}
    assert names == {"ui.tests.cart.test.cart.beforeEach", "ui.tests.cart.test.cart.beforeEach_2",
                     "ui.tests.cart.test.cart.adds_an_item", "ui.tests.cart.test.cart.adds_an_item_2"}
    assert builder.graph.has_edge("ui.tests.cart.test.cart.adds_an_item", f"{LOGIN}.submit")


def test_cache_hit_reproduces_test_blocks_and_robot_edges(tmp_path):
    repo = _copy(tmp_path)
    cold, _ = build_pipeline(repo)
    warm, _ = build_pipeline(repo)
    assert set(warm.graph.edges) == set(cold.graph.edges)
    blocks = [s.qualified_name for s in cold.symbol_table if s.language_id == "typescript" and s.role == SymbolRole.VERIFICATION]
    assert blocks and all(warm.def_node(q) is not None for q in blocks)
    assert all(warm.def_node(q).start_byte == cold.def_node(q).start_byte for q in blocks)


def test_editing_a_robot_file_invalidates_the_cache(tmp_path):
    repo = _copy(tmp_path)
    build_pipeline(repo)
    suite = Path(repo, "api", "tests", "users.robot")
    suite.write_text(suite.read_text() + "\nBrand New Test\n    Create Test User    zed\n")
    rebuilt, _ = build_pipeline(repo)
    assert rebuilt.graph.has_edge(f"{SUITE}.Brand_New_Test", f"{RES}.Create_Test_User")


# ----------------------------------------------------------- Robot Framework
def test_robot_symbols_and_roles(built):
    _repo, builder = built
    for qname in (f"{SUITE}.Create_And_Fetch_User", f"{SUITE}.Delete_User", f"{SUITE}.Templated_Creation"):
        assert builder.symbol_table.get(qname).role == SymbolRole.VERIFICATION
    for qname in (f"{RES}.Create_Test_User", f"{RES}.User_name_Should_Exist", f"{RES}.Clean_Up_Users"):
        info = builder.symbol_table.get(qname)
        assert info.role == SymbolRole.IMPLEMENTATION and info.language_id == "robot"


def test_robot_keyword_calls_link_user_keywords_and_python_libraries(built):
    _repo, builder = built
    assert _calls(builder, f"{RES}.Create_Test_User") == {f"{LIB}.create_user"}
    assert _calls(builder, f"{RES}.Authenticated_Header_For") == {f"{AUTH}.get_auth_token", f"{AUTH}.build_auth_header"}
    assert _calls(builder, f"{RES}.User_name_Should_Exist") == {f"{LIB}.get_user"}       # @keyword("Fetch User By Id")
    assert _calls(builder, f"{RES}.Clean_Up_Users") == {f"{LIB}.delete_user"}            # inside FOR ... END
    assert _calls(builder, f"{SUITE}.Create_And_Fetch_User") == {
        f"{RES}.Authenticated_Header_For",      # [Setup]
        f"{RES}.Create_Test_User", f"{LIB}.get_user",
        f"{RES}.User_name_Should_Exist",        # embedded argument: User alice Should Exist
        f"{RES}.Clean_Up_Users",                # Test Teardown in Settings
    }
    assert _calls(builder, f"{SUITE}.Delete_User") == {
        f"{RES}.Create_Test_User", f"{LIB}.delete_user", f"{RES}.Clean_Up_Users",   # UserApi.Delete User
    }
    assert _calls(builder, f"{SUITE}.Auth_Header_Works") == {
        f"{RES}.Authenticated_Header_For",
        f"{RES}.Create_Test_User",              # Run Keyword If ... ELSE on a continuation line
        f"{LIB}.get_user",                      # Wait Until Keyword Succeeds
        f"{RES}.Clean_Up_Users",
    }
    assert _calls(builder, f"{SUITE}.Templated_Creation") == {f"{RES}.Create_Test_User", f"{RES}.Clean_Up_Users"}


def test_library_decorator_limits_keywords_to_keyword_methods(built):
    _repo, builder = built
    # `Reset Session` is a call in Delete User, but UserApi is `@library`
    # and `reset_session` has no `@keyword`: not a keyword, not linked.
    assert not any(builder.graph.has_edge(f"{SUITE}.Delete_User", v) for v in (f"{LIB}.reset_session",))


def test_robot_edges_record_return_binding_and_variable_arguments(built):
    _repo, builder = built
    edge = builder.graph.edges[f"{SUITE}.Create_And_Fetch_User", f"{LIB}.get_user"]
    assert edge["robot_binds_return"] and edge["robot_args"]
    edge = builder.graph.edges[f"{SUITE}.Create_And_Fetch_User", f"{RES}.Clean_Up_Users"]
    assert not edge["robot_binds_return"] and not edge["robot_args"]


def test_robot_pipe_separated_format_and_unresolved_imports(tmp_path):
    path = tmp_path / "pipes.robot"
    path.write_text(textwrap.dedent("""\
        | *** Settings *** |
        | Library | NotInThisRepo |
        | *** Test Cases *** |
        | Pipe Test | Log | hi |
        |           | ${x}= | My Keyword | ${1} |
        | *** Keywords *** |
        | My Keyword | [Arguments] | ${n} |
        |            | Log | ${n} |
    """))
    robot = parse_robot_file(str(path), str(tmp_path))
    by_name = {b.name: b for b in robot.blocks}
    assert [c.name for c in by_name["Pipe Test"].calls] == ["Log", "My Keyword"]
    assert by_name["Pipe Test"].calls[1].binds_return
    assert robot.libraries == [("NotInThisRepo", 2)]


# ------------------------------------------------------------- MCP tool
@pytest.fixture()
def mcp_repo(tmp_path):
    from prism.mcp import server
    server._cache.clear()
    yield _copy(tmp_path), server
    server._cache.clear()


def test_blast_radius_tool_lists_test_callers_with_hops(mcp_repo):
    repo, server = mcp_repo
    out = server.prism_blast_radius(repo_path=repo, seed_symbol=f"{LIB}.create_user", budget_tokens=4000)
    hops = {c["symbol"]: (c["hop"], c["is_test"], c["language"]) for c in out["callers"]}
    assert hops == {
        f"{RES}.Create_Test_User": (1, False, "robot"),
        f"{SUITE}.Create_And_Fetch_User": (2, True, "robot"), f"{SUITE}.Delete_User": (2, True, "robot"),
        f"{SUITE}.Auth_Header_Works": (2, True, "robot"), f"{SUITE}.Templated_Creation": (2, True, "robot"),
    }
    assert out["callers_total"] == 5 and not out["callers_truncated"] and not out["truncated"]
    assert set(out["callers_in_context"]) == set(hops)
    assert 'id="api.tests.users.Delete_User"' in out["envelope"] and "UserApi.Delete User" in out["envelope"]


def test_blast_radius_tool_playwright_and_production_only(mcp_repo):
    repo, server = mcp_repo
    out = server.prism_blast_radius(repo_path=repo, seed_symbol=f"{LOGIN}.login", budget_tokens=4000)
    assert {c["symbol"] for c in out["callers"]} == {
        f"{UI}.login.valid_user_sees_greeting", f"{UI}.login.explicit_page_object", f"{UI}.logout_returns_to_login"}
    assert all(c["file"] == "ui/tests/login.spec.ts" for c in out["callers"])
    prod = server.prism_blast_radius(repo_path=repo, seed_symbol=f"{LIB}.create_user", include_tests=False)
    assert [c["symbol"] for c in prod["callers"]] == [f"{RES}.Create_Test_User"]


def test_blast_radius_tool_errors(mcp_repo):
    repo, server = mcp_repo
    with pytest.raises(MCPError) as exc:
        server.prism_blast_radius(repo_path=repo, seed_symbol=f"{LIB}.create_usr")
    assert exc.value.error.code == -32002
    with pytest.raises(MCPError) as exc:
        server.prism_blast_radius(repo_path=repo, seed_symbol=f"{LIB}.create_user", budget_tokens=100)
    assert exc.value.error.code == -32602


def test_harness_manifest_still_excludes_tests_by_default(built):
    _repo, builder = built
    manifest, universe = build_candidate_manifest(builder, f"{LIB}.create_user", direction="both", budget_tokens=13000)
    assert f"{RES}.Create_Test_User" in universe
    assert not any(q.startswith(SUITE) for q in universe)
    _m, with_tests = build_candidate_manifest(builder, f"{LIB}.create_user", direction="both", budget_tokens=13000,
                                              include_tests=True)
    assert f"{SUITE}.Delete_User" in with_tests


# ------------------------------------------------- output.xml scoring script
def test_robot_output_xml_scoring_against_prism_callers(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rbc", Path(__file__).parents[1] / "scripts" / "robot_blast_radius_check.py")
    rbc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rbc)
    repo = _copy(tmp_path)
    suite = os.path.join(repo, "api", "tests", "users.robot")
    # What a real run logged (Robot 7 `owner`): Auth Header Works took the
    # IF branch, so `Create Test User` (the ELSE branch) never ran there.
    xml = f"""<robot><suite name="Repo" source="{repo}"><suite name="Users" source="{suite}">
      <test name="Create And Fetch User">
        <kw name="Authenticated Header For" owner="common" type="SETUP"><kw name="Get Auth Token" owner="auth_helpers"/></kw>
        <kw name="Create Test User" owner="common"><kw name="Create User" owner="UserApi"/></kw>
        <kw name="Fetch User By Id" owner="UserApi"/>
      </test>
      <test name="Delete User">
        <kw name="Create Test User" owner="common"><kw name="Create User" owner="UserApi"/></kw>
        <kw name="Delete User" library="UserApi"/>
      </test>
      <test name="Auth Header Works">
        <kw name="Authenticated Header For" owner="common"/>
        <kw name="Run Keyword If" owner="BuiltIn"><kw name="Log" owner="BuiltIn"/></kw>
      </test>
      <test name="Templated Creation">
        <kw name="common.Create Test User"><kw name="UserApi.Create User"/></kw>
      </test>
    </suite></suite></robot>"""
    out = tmp_path / "output.xml"
    out.write_text(xml)
    [row] = rbc.score(repo, str(out), [("UserApi.Create User", f"{LIB}.create_user")])
    assert row["gold"] == 3 and row["predicted"] == 4
    assert row["recall"] == 1.0 and row["precision"] == 0.75
    assert row["extra"] == [f"{SUITE}.Auth_Header_Works"] and row["missed"] == []


def test_test_titles_never_become_call_targets(tmp_path):
    repo = _copy(tmp_path)
    Path(repo, "ui", "tests", "helpers.spec.ts").write_text(textwrap.dedent("""
        import { LoginPage } from '../pages/LoginPage';
        async function loginAs(p: LoginPage) {
          await p.login('a', 'b');
        }
        test('loginAs', async ({ loginPage }) => {
          await loginAs(loginPage);
        });
        test('submit', async () => {});
    """))
    builder, _ = build_pipeline(repo, use_cache=False)
    module = "ui.tests.helpers.spec"
    # the test titled like the helper still calls the helper, not itself
    assert builder.graph.has_edge(f"{module}.loginAs#2", f"{module}.loginAs")
    assert builder.graph.has_edge(f"{module}.loginAs", f"{LOGIN}.login")
    # a test titled `submit` does not make the page object's `submit` ambiguous
    assert [c.qualified_name for c in builder.symbol_table.candidates_for_simple_name("submit")] == [f"{LOGIN}.submit"]
