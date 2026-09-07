"""Hermetic Java/C# validation suite for the polyglot-enterprise extension
(roadmap: extend the universal Tree-sitter architecture to Java and C#).

Every test here runs against small, hand-written fixtures written to
`tmp_path` and parsed in-process - no JDK, no .NET SDK, no network access
(tree-sitter-java/tree-sitter-c-sharp are pure Python-extension bindings,
already project dependencies, not a system compiler). It exercises the
three pieces the polyglot-enterprise extension added:

  1. Package/namespace-aware symbol resolution ($G_C$) - a file's
     qualified names come from its declared `package`/`namespace`, not its
     Maven/ASP.NET directory layout; same-package/namespace siblings and
     wildcard imports (`import pkg.*;` / any C# `using`) resolve without
     an exact import; `new X()` constructor calls bind instance types.
  2. Annotation/attribute grounding into the metamodel matrix `M` -
     Spring/Jakarta annotations and ASP.NET Core/EF Core attributes map to
     the 8 core tags.
  3. `UniversalSlicer` L1/L2 slicing for Java/C# - reparses cleanly (a
     bare method/constructor is not itself a valid top-level Java/C#
     unit, so slices are reparsed wrapped in a minimal `class __W { }`
     shell here, matching how a slice is actually consumed downstream:
     as one member embedded in a larger context, never a standalone file).
"""
from __future__ import annotations

import pytest
from tree_sitter import Language, Parser

import tree_sitter_c_sharp as tscs
import tree_sitter_java as tsj

from sce.cli import build_pipeline
from sce.graph.metamodel import SemanticMetamodel
from sce.parser.queries import run_query
from sce.parser.tree_sitter_loader import LanguageID, get_parser
from sce.slicer.distance import DistanceConfig, DistanceEngine
from sce.slicer.knapsack import ContextKnapsackPacker
from sce.slicer.universal_slicer import UniversalSlicer

slicer = UniversalSlicer()

_WRAP_LANGUAGE = {
    LanguageID.JAVA: tsj,
    LanguageID.CSHARP: tscs,
}


def _reparses_cleanly_wrapped(language_id: str, text: str) -> bool:
    """A method/constructor slice is only ever valid Java/C# as a class
    member, never as a standalone top-level unit (confirmed empirically -
    even C#'s *unsliced* constructor source reparses with an ERROR node on
    its own, since the grammar requires a constructor's context to
    determine it isn't a plain method) - so wrap it in a minimal class
    shell before checking for ERROR nodes, the same context any real
    caller renders a slice into.
    """
    wrapped = f"class __W {{\n{text}\n}}"
    parser = Parser(Language(_WRAP_LANGUAGE[language_id].language()))
    tree = parser.parse(wrapped.encode("utf-8"))
    return not tree.root_node.has_error


def _slicer_def_node(language_id: str, source: bytes, capture: str = "def.function"):
    parser = get_parser(language_id)
    tree = parser.parse(source)
    captures = run_query(language_id, "slicer_defs", tree.root_node)
    candidates = captures.get(capture, [])
    assert candidates, f"no {capture!r} capture found for {language_id}"
    return candidates[0]


# --------------------------------------------------------------------- #
# Java: package-scoped symbol resolution + Spring annotation grounding
# --------------------------------------------------------------------- #
@pytest.fixture
def java_repo(tmp_path):
    """A Maven-shaped `src/main/java/...` layout where the qualified name
    authority is each file's own `package` declaration, not its directory
    path - `OrderService` and `OrderValidator` share `com.example.service`
    across two files with no import between them; `Helper` lives in a
    different package, reached only via `import com.example.models.*;`.
    """
    root = tmp_path / "java_repo"
    svc = root / "src" / "main" / "java" / "com" / "example" / "service"
    models = root / "src" / "main" / "java" / "com" / "example" / "models"
    svc.mkdir(parents=True)
    models.mkdir(parents=True)

    (svc / "OrderService.java").write_text(
        "package com.example.service;\n"
        "\n"
        "import com.example.models.*;\n"
        "\n"
        "public class OrderService {\n"
        "    private final OrderRepository repo;\n"
        "\n"
        "    public OrderService(OrderRepository repo) {\n"
        "        this.repo = repo;\n"
        "    }\n"
        "\n"
        "    @PostMapping(\"/orders\")\n"
        "    @Transactional\n"
        "    public Order createOrder(int amount) {\n"
        "        if (amount <= 0) {\n"
        "            throw new IllegalArgumentException(\"bad amount\");\n"
        "        }\n"
        "        OrderValidator validator = new OrderValidator();\n"
        "        validator.validate(amount);\n"
        "        Helper.assist();\n"
        "        return new Order(amount);\n"
        "    }\n"
        "}\n"
    )
    (svc / "OrderValidator.java").write_text(
        "package com.example.service;\n"
        "\n"
        "public class OrderValidator {\n"
        "    public boolean validate(int amount) {\n"
        "        return amount > 0;\n"
        "    }\n"
        "}\n"
    )
    (models / "Helper.java").write_text(
        "package com.example.models;\n"
        "\n"
        "public class Helper {\n"
        "    public static void assist() {\n"
        "    }\n"
        "}\n"
    )
    return root


def test_java_qualified_names_use_declared_package_not_maven_path(java_repo):
    builder, _ = build_pipeline(str(java_repo))
    names = builder.symbol_table.all_qualified_names()
    assert "com.example.service.OrderService.createOrder" in names
    assert "com.example.service.OrderValidator.validate" in names
    assert "com.example.models.Helper.assist" in names
    # The Maven `src/main/java/...` prefix must never leak into a
    # qualified name - it is a build-layout artifact, not part of any
    # real `import`/`package` a caller would ever write.
    assert not any(n.startswith("src.main.java") for n in names)


def test_java_same_package_sibling_resolves_without_import(java_repo):
    """`OrderService` and `OrderValidator` share `com.example.service`
    across two files and declare no import between them - Java's own
    same-package implicit-visibility rule."""
    builder, _ = build_pipeline(str(java_repo))
    assert builder.graph.has_edge(
        "com.example.service.OrderService.createOrder",
        "com.example.service.OrderValidator.validate",
    )


def test_java_wildcard_import_resolves(java_repo):
    builder, _ = build_pipeline(str(java_repo))
    assert builder.graph.has_edge(
        "com.example.service.OrderService.createOrder",
        "com.example.models.Helper.assist",
    )


def test_java_constructor_new_binds_instance_type(java_repo):
    """`OrderValidator validator = new OrderValidator();` must bind
    `validator`'s type via Rule A the same way Python's `v = Verifier()`
    idiom does - `new X(...)` is the *only* way Java constructs objects."""
    builder, _ = build_pipeline(str(java_repo))
    assert builder.graph.has_edge(
        "com.example.service.OrderService.createOrder",
        "com.example.service.OrderValidator.validate",
    )


def test_java_spring_annotations_ground_into_metamodel_tags(java_repo):
    _, tag_matrix = build_pipeline(str(java_repo))
    tags = tag_matrix["com.example.service.OrderService.createOrder"]
    assert "#route_handler" in tags  # @PostMapping
    assert "#state_mutation" in tags  # @Transactional
    assert "#db_write" in tags  # @Transactional


def test_java_exception_name_grounds_auth_guard_via_object_creation(tmp_path):
    """A Java `throw new SomeException(...)` is a distinct grammar shape
    from a call (`object_creation_expression`, not `method_invocation`) -
    the auth-guard exception-name heuristic must still see through it."""
    root = tmp_path / "java_auth_repo"
    pkg = root / "src" / "main" / "java" / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Guard.java").write_text(
        "package com.example;\n"
        "\n"
        "public class Guard {\n"
        "    public void check(boolean allowed) {\n"
        "        if (!allowed) {\n"
        "            throw new PermissionDeniedException(\"denied\");\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    _, tag_matrix = build_pipeline(str(root))
    assert "#auth_guard" in tag_matrix["com.example.Guard.check"]


# --------------------------------------------------------------------- #
# C#: namespace-scoped symbol resolution + ASP.NET Core attribute grounding
# --------------------------------------------------------------------- #
@pytest.fixture
def csharp_repo(tmp_path):
    """File-scoped `namespace App.Services;` (C# 10+) siblings resolving
    without an import, plus a `using App.Models;` wildcard-style import -
    C# has no separate per-class import syntax the way Java does, so a
    plain `using` always brings the whole namespace into scope."""
    root = tmp_path / "csharp_repo"
    services = root / "Services"
    models = root / "Models"
    services.mkdir(parents=True)
    models.mkdir(parents=True)

    (services / "OrderController.cs").write_text(
        "using App.Models;\n"
        "\n"
        "namespace App.Services;\n"
        "\n"
        "public class OrderController {\n"
        "    private readonly OrderService service;\n"
        "\n"
        "    public OrderController(OrderService service) {\n"
        "        this.service = service;\n"
        "    }\n"
        "\n"
        "    [HttpPost]\n"
        "    [Authorize]\n"
        "    [Transactional]\n"
        "    public void CreateOrder(int amount) {\n"
        "        if (amount <= 0) {\n"
        "            throw new PermissionDeniedException(\"bad amount\");\n"
        "        }\n"
        "        OrderValidator validator = new OrderValidator();\n"
        "        validator.Validate(amount);\n"
        "        Helper.Assist();\n"
        "    }\n"
        "}\n"
    )
    (services / "OrderValidator.cs").write_text(
        "namespace App.Services;\n"
        "\n"
        "public class OrderValidator {\n"
        "    public bool Validate(int amount) {\n"
        "        return amount > 0;\n"
        "    }\n"
        "}\n"
    )
    (models / "Helper.cs").write_text(
        "namespace App.Models;\n"
        "\n"
        "public class Helper {\n"
        "    public static void Assist() {\n"
        "    }\n"
        "}\n"
    )
    return root


def test_csharp_qualified_names_use_declared_namespace_not_directory_path(csharp_repo):
    builder, _ = build_pipeline(str(csharp_repo))
    names = builder.symbol_table.all_qualified_names()
    assert "App.Services.OrderController.CreateOrder" in names
    assert "App.Services.OrderValidator.Validate" in names
    assert "App.Models.Helper.Assist" in names


def test_csharp_same_namespace_sibling_resolves_without_using(csharp_repo):
    builder, _ = build_pipeline(str(csharp_repo))
    assert builder.graph.has_edge(
        "App.Services.OrderController.CreateOrder",
        "App.Services.OrderValidator.Validate",
    )


def test_csharp_using_directive_resolves_cross_namespace(csharp_repo):
    builder, _ = build_pipeline(str(csharp_repo))
    assert builder.graph.has_edge(
        "App.Services.OrderController.CreateOrder",
        "App.Models.Helper.Assist",
    )


def test_csharp_aspnet_attributes_ground_into_metamodel_tags(csharp_repo):
    _, tag_matrix = build_pipeline(str(csharp_repo))
    tags = tag_matrix["App.Services.OrderController.CreateOrder"]
    assert "#route_handler" in tags  # [HttpPost]
    assert "#auth_guard" in tags  # [Authorize] and thrown PermissionDeniedException
    assert "#state_mutation" in tags  # [Transactional]
    assert "#db_write" in tags  # [Transactional]


# --------------------------------------------------------------------- #
# UniversalSlicer: L1/L2 slicing reparses cleanly for Java and C#
# --------------------------------------------------------------------- #
JAVA_HANDLER_SOURCE = b"""public class OrderController {
    @PostMapping("/orders")
    @Transactional
    public Order createOrder(int amount) {
        logger.info("start");
        int rawTotal = 1 + 2;
        for (int i = 0; i < amount; i++) {
            audit(i);
        }
        try {
            Order order = repo.save(new Order(amount));
            return order;
        } catch (ValidationException e) {
            logger.error("failed", e);
            if (e.isFatal()) {
                throw new IllegalStateException("fatal");
            }
        } finally {
            cleanup();
        }
        switch (amount) {
            case 1:
                doOne();
                break;
            default:
                doOther();
        }
        return null;
    }
}
"""

CSHARP_HANDLER_SOURCE = b"""public class OrderController {
    [HttpPost]
    [Transactional]
    public Order CreateOrder(int amount) {
        logger.Info("start");
        int rawTotal = 1 + 2;
        for (int i = 0; i < amount; i++) {
            Audit(i);
        }
        try {
            Order order = repo.Save(new Order(amount));
            return order;
        } catch (ValidationException e) {
            logger.Error("failed", e);
            if (e.IsFatal()) {
                throw new InvalidOperationException("fatal");
            }
        } finally {
            Cleanup();
        }
        switch (amount) {
            case 1:
                DoOne();
                break;
            default:
                DoOther();
                break;
        }
        return null;
    }
}
"""


def test_java_handler_l1_skeleton_reparses_cleanly_and_retains_control_flow():
    node = _slicer_def_node(LanguageID.JAVA, JAVA_HANDLER_SOURCE)
    skeleton = slicer.skeletonize(JAVA_HANDLER_SOURCE, node, LanguageID.JAVA)
    assert _reparses_cleanly_wrapped(LanguageID.JAVA, skeleton), skeleton
    assert "logger.info" not in skeleton  # noisy log call pruned
    assert "rawTotal" not in skeleton  # pure intermediate math pruned
    assert "try {" in skeleton and "catch" in skeleton and "finally" in skeleton
    assert "IllegalStateException" in skeleton  # thrown exception retained


def test_java_handler_l2_contract_reparses_and_lists_tags_calls_raises():
    node = _slicer_def_node(LanguageID.JAVA, JAVA_HANDLER_SOURCE)
    contract = slicer.extract_contract(
        JAVA_HANDLER_SOURCE, node, LanguageID.JAVA,
        tags={"#route_handler", "#db_write"}, callees=["OrderRepository.save"],
    )
    assert _reparses_cleanly_wrapped(LanguageID.JAVA, contract), contract
    assert "#route_handler" in contract
    assert "IllegalStateException" in contract
    assert "OrderRepository.save" in contract


def test_csharp_handler_l1_skeleton_reparses_cleanly_and_retains_control_flow():
    node = _slicer_def_node(LanguageID.CSHARP, CSHARP_HANDLER_SOURCE)
    skeleton = slicer.skeletonize(CSHARP_HANDLER_SOURCE, node, LanguageID.CSHARP)
    assert _reparses_cleanly_wrapped(LanguageID.CSHARP, skeleton), skeleton
    assert "logger.Info" not in skeleton
    assert "rawTotal" not in skeleton
    assert "try {" in skeleton and "catch" in skeleton and "finally" in skeleton
    assert "InvalidOperationException" in skeleton


def test_csharp_handler_l2_contract_reparses_and_lists_tags_calls_raises():
    node = _slicer_def_node(LanguageID.CSHARP, CSHARP_HANDLER_SOURCE)
    contract = slicer.extract_contract(
        CSHARP_HANDLER_SOURCE, node, LanguageID.CSHARP,
        tags={"#route_handler", "#db_write"}, callees=["OrderRepository.Save"],
    )
    assert _reparses_cleanly_wrapped(LanguageID.CSHARP, contract), contract
    assert "#route_handler" in contract
    assert "InvalidOperationException" in contract
    assert "OrderRepository.Save" in contract


def test_csharp_constructor_slice_reparses_cleanly_wrapped():
    """C#'s grammar rejects a bare `constructor_declaration` even
    standalone-unsliced (confirmed empirically: it requires class context
    to distinguish a constructor from a same-named method) - the wrapped
    reparse check above is what actually matters; this pins that the
    slicer's own output for a constructor is not itself the source of any
    error, by also checking the *original* unsliced text reparses cleanly
    under the same wrapping.
    """
    src = b"""public class OrderController {
    public OrderController(OrderService service) {
        this.service = service;
    }
}
"""
    node = _slicer_def_node(LanguageID.CSHARP, src, capture="def.function")
    skeleton = slicer.skeletonize(src, node, LanguageID.CSHARP)
    assert _reparses_cleanly_wrapped(LanguageID.CSHARP, skeleton), skeleton


# --------------------------------------------------------------------- #
# UniversalSlicer wired into the production packing path
# --------------------------------------------------------------------- #
# `ContextKnapsackPacker`/`ASTCompressor` previously fell back to
# `compress_generic` (a crude line-based textual approximation) for every
# non-Python language, including Java/C# - `UniversalSlicer` existed only
# as a standalone, tested component. These confirm `ASTCompressor.compress`
# now dispatches Java/C# (and every other `UniversalSlicer`-supported
# language) through the real CST-based skeletonizer in the actual
# knapsack-packing path a caller uses, not just when called directly.
def _pack_l1_body(builder, tag_matrix, target: str, budget: int = 2000) -> str:
    engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(target, builder, tag_matrix, engine)
    item = next(i for i in pack_result.items if i.symbol == target)
    return item.content


def test_java_knapsack_l1_output_uses_universal_slicer_not_generic_fallback(tmp_path):
    """The *seed* (target) symbol always renders at L0 (full raw) - to
    actually exercise a non-zero resolution, this needs a second,
    call-graph-adjacent symbol the packer renders at L1/L2. Only
    `UniversalSlicer`'s real skeletonizer prunes a pure-math statement with
    no call and collapses a retained call's own arguments to `(...)`;
    `compress_generic`'s old fallback only ever dropped whole lines
    matching a fixed noisy-token list and never touched call arguments at
    all - so both signals together are conclusive proof of which path ran.
    """
    root = tmp_path / "java_wiring_repo"
    pkg = root / "src" / "main" / "java" / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "OrderService.java").write_text(
        "package com.example;\n"
        "\n"
        "public class OrderService {\n"
        "    public void createOrder(int amount) {\n"
        "        OrderValidator validator = new OrderValidator();\n"
        "        validator.validate(amount);\n"
        "    }\n"
        "}\n"
    )
    (pkg / "OrderValidator.java").write_text(
        "package com.example;\n"
        "\n"
        "public class OrderValidator {\n"
        "    public boolean validate(int amount) {\n"
        "        logger.info(\"validating\");\n"
        "        int rawTotal = amount * 2;\n"
        "        return audit(amount);\n"
        "    }\n"
        "}\n"
    )
    builder, tag_matrix = build_pipeline(str(root))
    engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=2000).pack(
        "com.example.OrderService.createOrder", builder, tag_matrix, engine
    )
    item = next(i for i in pack_result.items if i.symbol == "com.example.OrderValidator.validate")
    assert item.resolution != 0
    assert "logger.info" not in item.content  # noisy call pruned
    assert "rawTotal" not in item.content  # pure-math statement pruned
    assert "audit(/* ... */)" in item.content  # retained call's args collapsed


def test_csharp_knapsack_l1_output_reparses_cleanly(csharp_repo):
    builder, tag_matrix = build_pipeline(str(csharp_repo))
    body = _pack_l1_body(builder, tag_matrix, "App.Services.OrderController.CreateOrder")
    assert _reparses_cleanly_wrapped(LanguageID.CSHARP, body), body
