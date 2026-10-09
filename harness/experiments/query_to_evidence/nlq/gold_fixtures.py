"""Gold-derived `QuestionInterpretation` fixtures for all 24 questions.

GOLD-DERIVED EVALUATION FIXTURES - NOT EVIDENCE OF NATURAL-LANGUAGE
UNDERSTANDING. They are what a *perfect* interpreter would output, built from
the annotations; they exist so later stages (planner, retrieval, proof) can be
scored in isolation, and so a real interpreter's output can be compared
against them.

Built deterministically from three read-only inputs:
  dataset/questions.json, dataset/gold.json      question text, answerability,
                                                  interpretations, reasons
  design/baseline/requirements_matrix.json       requirement ids, kinds,
                                                  subjects, mandatory flags
plus `ANNOTATIONS` below, the only hand-written part: the verbatim span of
each question that names a requirement's subject, the scope limits the
question states (with the quoted words that state them), and the evidence
source of external requirements. Atomic facts are NOT copied: they stay in
the matrix and are joined by question/requirement id (`load`), which fails if
any matrix requirement lacks an interpretation or vice versa.

    python -m harness.experiments.query_to_evidence.nlq.gold_fixtures --check   # file == builder output
    python -m harness.experiments.query_to_evidence.nlq.gold_fixtures --write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.experiments.query_to_evidence import dataset as D
from harness.experiments.query_to_evidence.nlq import schema as S

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "gold_interpretations.json"
MATRIX = HERE.parent / "design" / "baseline" / "requirements_matrix.json"
LABEL = ("gold-derived evaluation fixture (oracle interpretation built from the gold annotations); "
         "not evidence of natural-language understanding")

_ANY = {"role": "any", "direct_only": False}

#: question id -> requirement id -> {mention, scope?, evidence_source?}
#: `mention` must be a verbatim span of the question. `scope` limits must quote
#: the words that state them. Nothing here names a symbol the gold does not.
ANNOTATIONS: dict[str, dict[str, dict]] = {
    "q01": {"r1": {"mention": "fastapi.dependencies.utils.get_dependant"}},
    "q02": {"r1": {"mention": "fastapi.openapi.utils.get_openapi_security_definitions"}},
    "q03": {"r1": {"mention": "django.utils.http.url_has_allowed_host_and_scheme"}},
    "q04": {"r1": {"mention": "django.http.response.HttpResponse.__init__"}},
    "q05": {"r1": {"mention": "request_params_to_args"}},
    "q06": {"r1": {"mention": "get_openapi_path"}},
    "q07": {"r1": {"mention": "QuerySet.filter"}},
    "q08": {"r1": {"mention": "url_has_allowed_host_and_scheme"}},
    "q09": {"r1": {"mention": "one FastAPI router is mounted inside another"}},
    "q10": {"r1": {"mention": "HTTP Basic authentication"}},
    "q11": {"r1": {"mention": "collect the request parameters of every route"}},
    "q12": {"r1": {"mention": "reads a key whose entry has already expired"}},
    "q13": {"r1": {"mention": "decide whether a user has a particular permission"}},
    "q14": {"r1": {"mention": "work out the host name of an incoming request"}},
    "q15": {"r1": {"mention": "how Django's local-memory cache evicts entries when it is full"},
            # "those code paths" refers to r1's answer; the gold subject is _set, the middle of r1's chain
            "r2": {"mention": "those code paths", "refers_to": "r1"}},
    "q16": {"r1": {"mention": "the helper that checks whether a redirect URL is safe to follow",
                   "scope": {"role": "production", "direct_only": False, "evidence_span": "Which production code"}},
            "r2": {"mention": "that helper",
                   "scope": {"role": "tests", "direct_only": True,
                             "evidence_span": "which tests exercise that helper directly"}}},
    "q17": {"r1": {"mention": "how FastAPI splits an Authorization header into its scheme and credentials"},
            "r2": {"mention": "that splitting function",
                   "scope": {"role": "tests", "direct_only": True,
                             "evidence_span": "which tests call that splitting function directly"}}},
    "q18": {"r1": {"mention": "resolve an endpoint's dependencies and parameters"},
            "r2": {"mention": "that resolution step"}},
    "q19": {"disambiguate": {"mention": "get"}},
    "q20": {"disambiguate": {"mention": "403 Not authenticated"}},
    "q21": {"disambiguate": {"mention": "the cache is full"}},
    "q22": {"external": {"mention": "p99 latency of our /items endpoint in staging last week",
                         "evidence_source": "runtime_telemetry"}},
    "q23": {"external": {"mention": "our customers' production sites",
                         "evidence_source": "deployment_or_business_data"}},
    "q24": {"external": {"mention": "how many milliseconds does a QuerySet.get call take under our production "
                                    "database load", "evidence_source": "runtime_telemetry"}},
}
_ANSWERABILITY = {"answerable": "answerable", "ambiguous": "needs_clarification", "unanswerable": "unanswerable"}


def _matrix() -> dict[str, dict]:
    return {q["query_id"]: q for q in json.loads(MATRIX.read_text())["questions"]}


def build() -> dict:
    questions, gold, matrix = D.load_questions(), D.load_gold(), _matrix()
    out = []
    for q in questions:
        qid, g, m = q["id"], gold[q["id"]], matrix[q["id"]]
        ann = ANNOTATIONS[qid]
        if set(ann) != {r["id"] for r in m["requirements"]}:
            raise ValueError(f"{qid}: annotations {sorted(ann)} != matrix requirements")
        reqs = []
        for r in m["requirements"]:
            a = ann[r["id"]]
            subject = {"mention": a["mention"],
                       "proposed_names": [r["subject"]] if r.get("subject") else []}
            if "refers_to" in a:
                subject["refers_to"] = a["refers_to"]
            reqs.append({"id": r["id"], "kind": r["kind"], "mandatory": r["mandatory"], "subject": subject,
                         "scope": a.get("scope", dict(_ANY)),
                         "evidence_source": a.get("evidence_source", "static_code")})
        rec = {"schema": S.INTERPRETATION, "schema_version": S.SCHEMA_VERSION, "question_id": qid,
               "corpus": q["corpus"], "question_text": q["query"],
               "interpreter": {"kind": "oracle_gold_derived", "note": LABEL},
               "answerability": _ANSWERABILITY[g["answerability"]], "requirements": reqs}
        if g["answerability"] == "ambiguous":
            rec["clarification"] = {
                "interpretations": [{"label": i["label"], "proposed_names": list(i["symbols"]),
                                     # an interpretation with no in-repo symbol is delegated to an external system
                                     "evidence_source": "static_code" if i["symbols"] else "external_service"}
                                    for i in g["ambiguity"]["interpretations"]],
                "resolving_information": g["ambiguity"]["resolving_information"]}
        out.append(rec)
    return {"version": 1, "label": LABEL,
            "inputs": ["dataset/questions.json", "dataset/gold.json", "design/baseline/requirements_matrix.json"],
            "interpretations": out}


def render(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def load(path: Path = FIXTURE) -> list[dict]:
    """Fixtures joined with the matrix: each entry is {interpretation, gold}
    where `gold` is the matrix question entry, unmodified. Raises if the two
    disagree on any question or requirement id, kind, subject or mandatory flag."""
    data = json.loads(path.read_text())
    matrix = _matrix()
    by_id = {r["question_id"]: r for r in data["interpretations"]}
    if set(by_id) != set(matrix):
        raise ValueError(f"fixture/matrix question mismatch: {sorted(set(by_id) ^ set(matrix))}")
    joined = []
    for qid in sorted(matrix):
        problems = join_problems(by_id[qid], matrix[qid])
        if problems:
            raise ValueError(f"{qid}: {problems}")
        joined.append({"interpretation": by_id[qid], "gold": matrix[qid]})
    return joined


def join_problems(interp: dict, gold_q: dict) -> list[str]:
    ir = {r["id"]: r for r in interp["requirements"]}
    gr = {r["id"]: r for r in gold_q["requirements"]}
    p = [] if set(ir) == set(gr) else [f"requirement ids {sorted(ir)} != matrix {sorted(gr)}"]
    for rid in sorted(set(ir) & set(gr)):
        i, g = ir[rid], gr[rid]
        if i["kind"] != g["kind"] or i["mandatory"] != g["mandatory"]:
            p.append(f"{rid}: kind/mandatory differ from the matrix")
        expected = [g["subject"]] if g.get("subject") else []
        if i["subject"]["proposed_names"] != expected:
            p.append(f"{rid}: proposed_names {i['subject']['proposed_names']} != matrix subject {expected}")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build or check the gold-derived interpretation fixtures")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    text = render(build())
    if args.write:
        FIXTURE.write_text(text)
        return 0
    ok = FIXTURE.exists() and FIXTURE.read_text() == text
    print("fixtures up to date" if ok else "fixtures differ from the builder output")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
