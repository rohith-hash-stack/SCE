"""R3 mining: an anchor that gained overloads gets the commit's whole overload group in its start state, never a
bodiless signature (r3a-B-09, r3a-B-29)."""
from harness.experiments.r3.mine import _is_stub, class_start, overload_group

PY = '''class A:
    def get(self) -> int:
        return 1


class B:
    @overload
    def get(self, *, x: Literal[True]) -> Any: ...

    @overload
    def get(self, *, x: Literal[False] = ...) -> Any: ...

    def get(self, *, x: bool = False) -> Any:
        """Doc."""
        return compute(
            x,
        )

    def other(self):
        pass
'''

TS = '''export async function validate<S>(
  fn: S,
): Promise<Out<S>>;
export async function validate<T>(fn: (d: unknown) => T): Promise<T>;
export async function validate<T>(
  fn: Fn<T>,
): Promise<T> {
  if (x) {
    return y;
  }
  return z;
}

function after() {}
'''


def test_python_stub_widens_to_the_group_in_the_right_class():
    stub = "def get(self, *, x: Literal[True]) -> Any: ..."
    g = overload_group(PY, stub, "python", class_start(PY, "mod.B.get"))
    assert g.startswith("    @overload\n    def get(self, *, x: Literal[True])")
    assert g.rstrip().endswith("        )") and "def other" not in g and "return 1" not in g


def test_typescript_signature_widens_through_the_implementation_body():
    stub = "export async function validate<S>(\n  fn: S,\n): Promise<Out<S>>;"
    g = overload_group(TS, stub, "js")
    assert g.startswith("export async function validate<S>(") and g.rstrip().endswith("return z;\n}")
    assert "after()" not in g


def test_a_function_with_a_body_is_not_a_stub():
    assert not _is_stub("def f(x):\n    return x", "python")
    assert not _is_stub("function f() {\n  return 1;\n}", "js")
    assert overload_group(PY, "def get(self) -> int:\n        return 1", "python") is None
