"""Cross-language parity test for Prism v1.1's Substance axis
(`prism.semantics.substance`): equivalent HTTP-call and SQL-call
functions in Python, TypeScript, and Go must all produce the exact same
`FeatureBit.SINK_NETWORK_IO`/`FeatureBit.SINK_DATABASE_IO` bits - the
CANONICAL_SINKS registry (`prism.semantics.substance.CANONICAL_SINKS`)
is keyed per-language specifically so this holds, not an accident of one
language's own stdlib naming happening to line up with another's.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.semantics.bitmask import FeatureBit
from prism.semantics.substance import compute_substance_bits


def _build(tmp_path, filename, source):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / filename).write_text(source)
    return build_pipeline(str(repo))


# --------------------------------------------------------------------- #
# Network I/O parity
# --------------------------------------------------------------------- #
def test_python_network_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.py",
        "import requests\n\n\ndef fetch_data(url):\n    return requests.get(url)\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.fetch_data"] & int(FeatureBit.SINK_NETWORK_IO)


def test_typescript_network_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.ts",
        "import axios from 'axios';\n\nfunction fetchData(url: string) {\n    return axios.get(url);\n}\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.fetchData"] & int(FeatureBit.SINK_NETWORK_IO)


def test_go_network_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.go",
        "package main\n\nimport \"net/http\"\n\nfunc FetchData(url string) {\n\thttp.Get(url)\n}\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.FetchData"] & int(FeatureBit.SINK_NETWORK_IO)


def test_all_three_languages_agree_on_network_io_bit(tmp_path):
    py_builder, _ = _build(tmp_path / "py", "svc.py", "import requests\n\n\ndef fetch_data(url):\n    return requests.get(url)\n")
    ts_builder, _ = _build(tmp_path / "ts", "svc.ts", "import axios from 'axios';\n\nfunction fetchData(url: string) {\n    return axios.get(url);\n}\n")
    go_builder, _ = _build(tmp_path / "go", "svc.go", "package main\n\nimport \"net/http\"\n\nfunc FetchData(url string) {\n\thttp.Get(url)\n}\n")

    py_bits = compute_substance_bits(py_builder)["svc.fetch_data"]
    ts_bits = compute_substance_bits(ts_builder)["svc.fetchData"]
    go_bits = compute_substance_bits(go_builder)["svc.FetchData"]

    assert (py_bits & int(FeatureBit.SINK_NETWORK_IO)) == (ts_bits & int(FeatureBit.SINK_NETWORK_IO)) == (go_bits & int(FeatureBit.SINK_NETWORK_IO)) == int(FeatureBit.SINK_NETWORK_IO)


# --------------------------------------------------------------------- #
# Database I/O parity
# --------------------------------------------------------------------- #
def test_python_database_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.py",
        "import sqlalchemy\n\n\ndef store_row(data):\n    return sqlalchemy.create_engine('x').execute(data)\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.store_row"] & int(FeatureBit.SINK_DATABASE_IO)


def test_typescript_database_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.ts",
        "import * as pg from 'pg';\n\nfunction storeRow(data: string) {\n    return pg.query(data);\n}\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.storeRow"] & int(FeatureBit.SINK_DATABASE_IO)


def test_go_database_io_sink(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.go",
        "package main\n\nimport \"database/sql\"\n\nfunc StoreRow(data string) {\n\tdb, _ := sql.Open(\"postgres\", \"\")\n\tdb.Exec(data)\n}\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.StoreRow"] & int(FeatureBit.SINK_DATABASE_IO)


def test_all_three_languages_agree_on_database_io_bit(tmp_path):
    py_builder, _ = _build(
        tmp_path / "py", "svc.py",
        "import sqlalchemy\n\n\ndef store_row(data):\n    return sqlalchemy.create_engine('x').execute(data)\n",
    )
    ts_builder, _ = _build(
        tmp_path / "ts", "svc.ts",
        "import * as pg from 'pg';\n\nfunction storeRow(data: string) {\n    return pg.query(data);\n}\n",
    )
    go_builder, _ = _build(
        tmp_path / "go", "svc.go",
        "package main\n\nimport \"database/sql\"\n\nfunc StoreRow(data string) {\n\tdb, _ := sql.Open(\"postgres\", \"\")\n\tdb.Exec(data)\n}\n",
    )

    py_bits = compute_substance_bits(py_builder)["svc.store_row"]
    ts_bits = compute_substance_bits(ts_builder)["svc.storeRow"]
    go_bits = compute_substance_bits(go_builder)["svc.StoreRow"]

    assert (py_bits & int(FeatureBit.SINK_DATABASE_IO)) == (ts_bits & int(FeatureBit.SINK_DATABASE_IO)) == (go_bits & int(FeatureBit.SINK_DATABASE_IO)) == int(FeatureBit.SINK_DATABASE_IO)


# --------------------------------------------------------------------- #
# Combined: one function per language calling both HTTP and SQL
# --------------------------------------------------------------------- #
def test_combined_network_and_database_function_matches_across_languages(tmp_path):
    py_builder, _ = _build(
        tmp_path / "py", "svc.py",
        "import requests\nimport sqlalchemy\n\n\n"
        "def sync_remote_data(url):\n"
        "    data = requests.get(url)\n"
        "    return sqlalchemy.create_engine('x').execute(data)\n",
    )
    ts_builder, _ = _build(
        tmp_path / "ts", "svc.ts",
        "import axios from 'axios';\nimport * as pg from 'pg';\n\n"
        "function syncRemoteData(url: string) {\n"
        "    const data = axios.get(url);\n"
        "    return pg.query(data);\n"
        "}\n",
    )
    go_builder, _ = _build(
        tmp_path / "go", "svc.go",
        "package main\n\nimport (\n\t\"net/http\"\n\t\"database/sql\"\n)\n\n"
        "func SyncRemoteData(url string) {\n"
        "\thttp.Get(url)\n"
        "\tdb, _ := sql.Open(\"postgres\", \"\")\n"
        "\tdb.Exec(url)\n"
        "}\n",
    )

    expected = int(FeatureBit.SINK_NETWORK_IO | FeatureBit.SINK_DATABASE_IO)
    py_bits = compute_substance_bits(py_builder)["svc.sync_remote_data"]
    ts_bits = compute_substance_bits(ts_builder)["svc.syncRemoteData"]
    go_bits = compute_substance_bits(go_builder)["svc.SyncRemoteData"]

    assert py_bits & expected == expected
    assert ts_bits & expected == expected
    assert go_bits & expected == expected
