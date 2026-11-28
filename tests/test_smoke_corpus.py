"""CPU smoke test scoped to the session's corpus (`--corpus`): the Arm 3
readiness checks target that corpus's checkout and editable install, and the
check that does not apply to its language reports SKIP."""
import pytest

from harness import smoke_test_cpu as S


class Words:
    name = "words"
    def count(self, t): return len(t.split())


def test_corpus_django_checks_djangos_editable_install(monkeypatch, tmp_path):
    """corpus=django: Arm 3 indexes Django's checkout, and its editable-install
    check asks where `django` imports from (not fastapi)."""
    import harness.arms.arm3_lsp as A
    import benchmarks.corpora.resolver as R
    root = tmp_path / "django"
    root.mkdir()
    asked = []
    monkeypatch.setattr(R, "resolve", lambda name: (asked.append(("resolve", name)), root)[1])
    monkeypatch.setattr(A, "module_origin", lambda module, *a, **k: (asked.append(("origin", module)),
                                                                     "/usr/lib/python3/dist-packages/django")[1])
    monkeypatch.setattr(A.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(S, "TARGET_CORPUS", "django")
    arm_cls = A.Arm3PyrightLSP
    monkeypatch.setattr(A, "Arm3PyrightLSP", lambda tokenizer=None: arm_cls(tokenizer=tokenizer, require_editable=True))
    with pytest.raises(A.EditableInstallError, match=str(root)):
        S.check_arm3_lsp_ready(Words())
    assert ("resolve", "django") in asked and ("origin", "django") in asked
    assert ("resolve", "fastapi") not in asked and ("origin", "fastapi") not in asked


@pytest.mark.parametrize("corpus", ["express", "trpc"])
def test_pyright_check_skips_typescript_corpora(monkeypatch, corpus):
    monkeypatch.setattr(S, "TARGET_CORPUS", corpus)
    with pytest.raises(S.SkipCheck, match="TypeScript corpus"):
        S.check_arm3_lsp_ready(Words())


@pytest.mark.parametrize("corpus", ["fastapi", "django"])
def test_ts_check_skips_python_corpora(monkeypatch, corpus):
    monkeypatch.setattr(S, "TARGET_CORPUS", corpus)
    with pytest.raises(S.SkipCheck, match="Python corpus"):
        S.check_arm3_ts_lsp_ready(Words())


def test_skip_is_reported_and_never_fails_the_run(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(S, "CHECKS", [("arm3_ts_lsp_ready", S.check_arm3_ts_lsp_ready),
                                      ("ok", lambda tok: (True, "fine"))])
    monkeypatch.setattr("harness.tokenizer.get_tokenizer", lambda: Words())
    out = tmp_path / "smoke.json"
    assert S.main(["--corpus", "django", "--json", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["corpus"] == "django" and rep["counts"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 0, "SKIP": 1}
    assert rep["rows"][0]["status"] == "SKIP"
