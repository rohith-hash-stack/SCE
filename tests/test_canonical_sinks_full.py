"""Verification suite for the v1.1+ full canonical sink taxonomy
(`prism.semantics.canonical_sinks.CANONICAL_SINKS`) and the three-phase
matching logic that consumes it (`prism.semantics.substance`).

Exercises all 6 `Substance` categories (network_io, database_io,
filesystem_io, process_io, time_io, randomness) across Python, Go, and
TypeScript, including the exact call shapes the engineering spec's own
verification list names: `requests.get`, `httpx.post`, `fetch`,
`axios.get`, `net/http.Get`, `database/sql.DB.Query`,
`gorm.io/gorm.DB.Create`, `os.ReadFile`, `os/exec.Command`, `time.Sleep`,
`uuid.uuid4`, `crypto/rand.Read`.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.semantics.bitmask import FeatureBit
from prism.semantics.substance import compute_substance_bits


def _build(tmp_path, filename, source):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / filename).write_text(source)
    return build_pipeline(str(repo))


def _bits_for(tmp_path, filename, source, qualified_name):
    builder, _ = _build(tmp_path, filename, source)
    return compute_substance_bits(builder)[qualified_name]


# --------------------------------------------------------------------- #
# The spec's own explicit assertion list, one test per named call shape.
# --------------------------------------------------------------------- #
def test_requests_get_sets_network_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.py",
        "import requests\n\n\ndef fetch_data(url):\n    return requests.get(url)\n",
        "svc.fetch_data",
    )
    assert bits & int(FeatureBit.SINK_NETWORK_IO)


def test_httpx_post_sets_network_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.py",
        "import httpx\n\n\ndef submit(url, payload):\n    return httpx.post(url, payload)\n",
        "svc.submit",
    )
    assert bits & int(FeatureBit.SINK_NETWORK_IO)


def test_bare_fetch_sets_network_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.ts",
        "function fetchData(url: string) {\n    return fetch(url);\n}\n",
        "svc.fetchData",
    )
    assert bits & int(FeatureBit.SINK_NETWORK_IO)


def test_axios_get_sets_network_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.ts",
        "import axios from 'axios';\n\nfunction fetchData(url: string) {\n    return axios.get(url);\n}\n",
        "svc.fetchData",
    )
    assert bits & int(FeatureBit.SINK_NETWORK_IO)


def test_go_net_http_get_sets_network_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"net/http\"\n\nfunc FetchData(url string) {\n\thttp.Get(url)\n}\n",
        "svc.FetchData",
    )
    assert bits & int(FeatureBit.SINK_NETWORK_IO)


def test_go_database_sql_db_query_sets_database_io(tmp_path):
    """`database/sql.DB.Query` is a receiver-qualified registry entry -
    real Go code never spells it out literally (you call `db.Query(...)`
    on an instance, never `sql.DB.Query(...)` on the type) - exercised
    via Phase 2a local constructor provenance: `db` is bound from the
    Phase-1-matched `sql.Open(...)` call, so `db.Query(...)` inherits its
    sink bit."""
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"database/sql\"\n\n"
        "func FetchOrders() {\n"
        "\tdb, _ := sql.Open(\"postgres\", \"\")\n"
        "\tdb.Query(\"SELECT 1\")\n"
        "}\n",
        "svc.FetchOrders",
    )
    assert bits & int(FeatureBit.SINK_DATABASE_IO)


def test_go_gorm_db_create_sets_database_io(tmp_path):
    """Same Phase 2a mechanism as above: `db` bound from the Phase-1-
    matched `gorm.Open(...)` constructor, so `db.Create(...)` inherits
    `gorm.io/gorm`'s own database_io bit."""
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"gorm.io/gorm\"\n\n"
        "func SaveOrder() {\n"
        "\tdb, _ := gorm.Open(nil, nil)\n"
        "\tdb.Create(&Order{})\n"
        "}\n",
        "svc.SaveOrder",
    )
    assert bits & int(FeatureBit.SINK_DATABASE_IO)


def test_go_os_readfile_sets_filesystem_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"os\"\n\nfunc LoadConfig(path string) {\n\tos.ReadFile(path)\n}\n",
        "svc.LoadConfig",
    )
    assert bits & int(FeatureBit.SINK_FILESYSTEM_IO)


def test_go_os_exec_command_sets_process_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"os/exec\"\n\nfunc RunTool() {\n\texec.Command(\"ls\", \"-la\")\n}\n",
        "svc.RunTool",
    )
    assert bits & int(FeatureBit.SINK_PROCESS_IO)


def test_go_time_sleep_sets_time_io(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"time\"\n\nfunc Backoff() {\n\ttime.Sleep(1)\n}\n",
        "svc.Backoff",
    )
    assert bits & int(FeatureBit.SINK_TIME_IO)


def test_uuid_uuid4_sets_randomness(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.py",
        "import uuid\n\n\ndef new_id():\n    return uuid.uuid4()\n",
        "svc.new_id",
    )
    assert bits & int(FeatureBit.SINK_RANDOMNESS)


def test_go_crypto_rand_read_sets_randomness(tmp_path):
    bits = _bits_for(
        tmp_path, "svc.go",
        "package main\n\nimport \"crypto/rand\"\n\nfunc FillToken(b []byte) {\n\trand.Read(b)\n}\n",
        "svc.FillToken",
    )
    assert bits & int(FeatureBit.SINK_RANDOMNESS)


# --------------------------------------------------------------------- #
# Full 6-category x 3-language matrix: one representative, realistic
# call shape per cell, proving every category has real coverage in every
# language's own registry, not just the handful the spec named above.
# --------------------------------------------------------------------- #
_MATRIX_CASES = [
    # (case_id, filename, source, qualified_name, expected_bit)
    ("py-network", "svc.py", "import requests\n\n\ndef f(url):\n    return requests.get(url)\n", "svc.f", FeatureBit.SINK_NETWORK_IO),
    ("py-database", "svc.py", "import sqlite3\n\n\ndef f(path):\n    return sqlite3.connect(path)\n", "svc.f", FeatureBit.SINK_DATABASE_IO),
    ("py-filesystem", "svc.py", "def f(path):\n    return open(path)\n", "svc.f", FeatureBit.SINK_FILESYSTEM_IO),
    ("py-process", "svc.py", "import subprocess\n\n\ndef f(cmd):\n    return subprocess.run(cmd)\n", "svc.f", FeatureBit.SINK_PROCESS_IO),
    ("py-time", "svc.py", "import time\n\n\ndef f():\n    time.sleep(1)\n", "svc.f", FeatureBit.SINK_TIME_IO),
    ("py-randomness", "svc.py", "import random\n\n\ndef f():\n    return random.random()\n", "svc.f", FeatureBit.SINK_RANDOMNESS),
    ("go-network", "svc.go", "package main\n\nimport \"net/http\"\n\nfunc F(url string) {\n\thttp.Get(url)\n}\n", "svc.F", FeatureBit.SINK_NETWORK_IO),
    ("go-database", "svc.go", "package main\n\nimport \"database/sql\"\n\nfunc F() {\n\tsql.Open(\"postgres\", \"\")\n}\n", "svc.F", FeatureBit.SINK_DATABASE_IO),
    ("go-filesystem", "svc.go", "package main\n\nimport \"os\"\n\nfunc F(p string) {\n\tos.ReadFile(p)\n}\n", "svc.F", FeatureBit.SINK_FILESYSTEM_IO),
    ("go-process", "svc.go", "package main\n\nimport \"os/exec\"\n\nfunc F() {\n\texec.Command(\"ls\")\n}\n", "svc.F", FeatureBit.SINK_PROCESS_IO),
    ("go-time", "svc.go", "package main\n\nimport \"time\"\n\nfunc F() {\n\ttime.Sleep(1)\n}\n", "svc.F", FeatureBit.SINK_TIME_IO),
    ("go-randomness", "svc.go", "package main\n\nimport \"math/rand\"\n\nfunc F() {\n\trand.Intn(10)\n}\n", "svc.F", FeatureBit.SINK_RANDOMNESS),
    ("ts-network", "svc.ts", "function f(url: string) {\n    return fetch(url);\n}\n", "svc.f", FeatureBit.SINK_NETWORK_IO),
    ("ts-database", "svc.ts", "import { Pool } from 'pg';\n\nfunction f(q: string) {\n    const pool = new Pool();\n    return pool.query(q);\n}\n", "svc.f", FeatureBit.SINK_DATABASE_IO),
    ("ts-filesystem", "svc.ts", "import * as fs from 'fs';\n\nfunction f(path: string) {\n    return fs.readFileSync(path);\n}\n", "svc.f", FeatureBit.SINK_FILESYSTEM_IO),
    ("ts-process", "svc.ts", "import * as child_process from 'child_process';\n\nfunction f(cmd: string) {\n    return child_process.exec(cmd);\n}\n", "svc.f", FeatureBit.SINK_PROCESS_IO),
    ("ts-time", "svc.ts", "function f() {\n    setTimeout(() => {}, 1000);\n}\n", "svc.f", FeatureBit.SINK_TIME_IO),
    ("ts-randomness", "svc.ts", "function f() {\n    return Math.random();\n}\n", "svc.f", FeatureBit.SINK_RANDOMNESS),
]


@pytest.mark.parametrize("case_id,filename,source,qualified_name,expected_bit", _MATRIX_CASES, ids=[c[0] for c in _MATRIX_CASES])
def test_sink_matrix_cell(tmp_path, case_id, filename, source, qualified_name, expected_bit):
    bits = _bits_for(tmp_path, filename, source, qualified_name)
    assert bits & int(expected_bit), f"{case_id}: expected {expected_bit.name} not set (got {bits:#x})"


def test_pure_compute_function_gets_no_sink_bits(tmp_path):
    """The taxonomy expansion must not make every function look like a
    sink - an ordinary pure function still falls through to
    `SINK_PURE_COMPUTE`."""
    bits = _bits_for(
        tmp_path, "svc.py",
        "def add(a, b):\n    return a + b\n",
        "svc.add",
    )
    assert bits == int(FeatureBit.SINK_PURE_COMPUTE)
