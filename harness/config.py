"""Every constant the harness uses. Nothing else in `harness/` hard-codes a
threshold, a budget, a path or a model name; it reads it from here.

Arms 0, 1, 5 and the Oracle are real since M1, Arms 2 and 3 since M2, Arm 4
since M3.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_VERSION = "eight-arm-slm/m1"


# --------------------------------------------------------------------------
# Model and sampling
# --------------------------------------------------------------------------
#: q8_0, not the originally specified q4_K_M (owner decision after the
#: d087d1b Kaggle run): q8_0 is what that run actually served, it fits on
#: Kaggle's 2 x T4 (about 10.9 GB peak per GPU observed, split across both
#: GPUs), and it is cached on Kaggle. Every arm uses this one model.
MODEL_NAME = "qwen2.5-coder:14b-instruct-q8_0"
#: Hugging Face id of the tokenizer that matches MODEL_NAME (Qwen2.5 family).
TOKENIZER_HF_ID = "Qwen/Qwen2.5-Coder-14B-Instruct"
#: Optional local override: a directory holding the tokenizer files, or a
#: GGUF file (llama.cpp's `ggml-vocab-qwen2.gguf` carries the same BPE).
TOKENIZER_LOCAL_PATH = os.environ.get("HARNESS_TOKENIZER_PATH")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8080")
TEMPERATURE = 0.4
SEEDS = [42, 43, 44]
REQUEST_TIMEOUT_S = 600


# --------------------------------------------------------------------------
# Token envelope (fixed by the server's 16,384-token context window)
# --------------------------------------------------------------------------
#: 18,432, not 16,384: the generation cap went from 2,500 to 4,096 tokens
#: (degenerate repetition loops hit the old cap). The window grew instead
#: of the retrieval budget shrinking, so every arm keeps its 13,000 tokens.
#: The harness requests this window explicitly (Ollama native API num_ctx).
CONTEXT_WINDOW = 18_432
SYSTEM_PROMPT_TOKENS = 500
TASK_PROMPT_TOKENS = 300
RETRIEVAL_BUDGET = 13_000
GENERATION_RESERVE = 4_096
SAFETY_MARGIN = 536
assert (
    SYSTEM_PROMPT_TOKENS + TASK_PROMPT_TOKENS + RETRIEVAL_BUDGET + GENERATION_RESERVE + SAFETY_MARGIN
    == CONTEXT_WINDOW
), "token envelope must sum to the context window"
#: Declared retrieval budget of the no-retrieval arm.
ARM0_BUDGET = 0
#: HF tokenizer vs llama-server /tokenize: above this relative divergence,
#: token counting falls back to llama-server (run with -ngl 0).
TOKENIZER_PARITY_TOLERANCE = 0.02


# --------------------------------------------------------------------------
# Arms and fidelity
# --------------------------------------------------------------------------
class FidelityGrade(str, Enum):
    HIGH = "HIGH"
    MEDIUM_HIGH = "MEDIUM_HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    #: Reference arms: not replications of anything, so not graded.
    BASELINE = "BASELINE"
    CEILING = "CEILING"


ARM_IDS = ["arm0", "arm1", "arm2", "arm3", "arm4", "arm5", "oracle"]
ARM_NAMES = {
    "arm0": "Parametric baseline (no retrieval)",
    "arm1": "Advanced RAG (AST chunks + BM25 + dense + RRF + cross-encoder)",
    "arm2": "Cursor-style Priompt packing",
    "arm3": "Copilot-style Pyright LSP",
    "arm4": "Claude-Code-style agent loop",
    "arm5": "PRISM two-pass",
    "oracle": "PragmaticOracle (ground-truth universe)",
}
FIDELITY = {
    "arm0": FidelityGrade.BASELINE,
    "arm1": FidelityGrade.HIGH,
    "arm2": FidelityGrade.MEDIUM,
    "arm3": FidelityGrade.MEDIUM,
    "arm4": FidelityGrade.MEDIUM_HIGH,
    "arm5": FidelityGrade.HIGH,
    "oracle": FidelityGrade.CEILING,
}
#: Arms implemented (M1: 0, 1, 5, oracle; M2: 2, 3; M3: 4).
ACTIVE_ARMS = ["arm0", "arm1", "arm2", "arm3", "arm4", "arm5", "oracle"]
STUB_MILESTONE: dict[str, str] = {}
#: Run-time subset of the implemented arms, e.g. HARNESS_ACTIVE_ARMS=arm0,arm5,oracle
#: for a single-arm baseline. Unknown or not-yet-implemented arms are rejected.
_ACTIVE_ARMS_OVERRIDE = os.environ.get("HARNESS_ACTIVE_ARMS")
if _ACTIVE_ARMS_OVERRIDE:
    _requested = [a.strip() for a in _ACTIVE_ARMS_OVERRIDE.split(",") if a.strip()]
    _unknown = [a for a in _requested if a not in ACTIVE_ARMS]
    if _unknown or not _requested:
        raise ValueError(f"HARNESS_ACTIVE_ARMS={_ACTIVE_ARMS_OVERRIDE!r}: {_unknown or 'empty'} not among the "
                         f"implemented arms {ACTIVE_ARMS}")
    ACTIVE_ARMS = _requested


# --------------------------------------------------------------------------
# Corpora and tasks
# --------------------------------------------------------------------------
CORPORA = ["fastapi", "django", "express", "trpc"]
PYTHON_CORPORA = {"fastapi", "django"}
#: Splitter / language-server language per corpus (M4): Arms 1 and 2 chunk
#: the corpus's files of this language; Arm 3 runs Pyright for "python" and
#: typescript-language-server for "typescript" (Express is JavaScript, which
#: the TypeScript grammar and server both cover).
CORPUS_LANGUAGE = {"fastapi": "python", "django": "python", "express": "typescript", "trpc": "typescript"}
TASKS_DIR_TEMPLATE = str(REPO_ROOT / "benchmarks/ground_truth/tasks/{repo}")
TASK_TYPES = ["T1_conceptual", "T2_localization", "T3_codegen", "T4_edit", "T5_blast_radius"]
#: Task types with real tasks in this run. T1 has a real scorer but no
#: tasks (no judge yet); T3/T4 have stub scorers.
ACTIVE_TASK_TYPES = ["T2_localization", "T5_blast_radius"]
#: Real tasks left out of Gate B scoring, with the reason (recorded in the
#: gate report). The task files are unchanged. See docs/m2_closure.md,
#: "Task-set caveats".
GATE_B_EXCLUDED_TASKS = {
    "fastapi_t02_005_request_validation_error_response":
        "gold ValidationException.errors is invoked through the subclass (RequestValidationError.errors, "
        "not a definition); the model names either form, so the strict answer check is ambiguous. "
        "Out of scope until re-designed with accepted alternatives (M4 annotation backlog).",
}
#: How the existing ground-truth task files map onto the taxonomy.
LEGACY_TASK_TYPE_MAP = {"debug": "T2_localization", "blast": "T5_blast_radius"}


# --------------------------------------------------------------------------
# Arm 1 — Advanced RAG
# --------------------------------------------------------------------------
RAG_MAX_CHUNK_TOKENS = 800
RAG_BM25_K1 = 1.5
RAG_BM25_B = 0.75
RAG_EMBED_MODEL = "jinaai/jina-embeddings-v2-base-code"
RAG_EMBED_DEVICE = "cpu"
RAG_EMBED_BATCH = 16
#: jina-v2 supports 8,192, but chunks are capped at 800 (Qwen) tokens; 1,024
#: bounds CPU cost and truncates only the flagged oversized chunks.
RAG_EMBED_MAX_SEQ = 1024
RAG_RRF_K = 60
#: Candidates taken from each of BM25 and dense before fusion.
RAG_FUSION_DEPTH = 100
#: Fused candidates sent to the cross-encoder.
RAG_RERANK_DEPTH = 50
RAG_RERANK_MODEL = "BAAI/bge-reranker-base"
RAG_RERANK_MAX_LEN = 512
RAG_RERANK_BATCH = 16
#: Smoke-test gate: rank 50 (query, chunk) pairs on CPU within this time.
RAG_RERANK_SMOKE_SECONDS = 3.0
#: Where the exported ONNX reranker and cached chunk embeddings live.
RAG_CACHE_DIR = Path(os.environ.get("HARNESS_CACHE_DIR", str(REPO_ROOT / ".benchmarks/cache/harness")))
#: M4 ablation lever, OFF in M1: top-level directories (relative to the
#: corpus root, e.g. ["docs_src", "tests"]) that Arm 1 leaves out of its
#: index. Empty = index the whole checkout, as every arm does. See README
#: "M1 finding: docs_src/tests pollution in Arm 1". Do not enable outside
#: the M4 ablation.
ARM1_EXCLUDE_DIRS: list[str] = []


# --------------------------------------------------------------------------
# Arm 2 — Priompt (M2)
# --------------------------------------------------------------------------
PRIOMPT_SEED_PRIORITY = 1000
PRIOMPT_CHUNK_BASE_PRIORITY = 500
PRIOMPT_CHUNK_RANK_STEP = 10
#: BM25 chunks retrieved; 500 - 10*rank stays positive up to rank 50.
PRIOMPT_RETRIEVE_K = 50
#: A stub outranks its own body by this much, so Priompt's <first> falls
#: back from body to stub as the cutoff rises (inferred; see arm2_priompt).
#: 500 = the width of the retrieved band: a chunk's signature is worth as much
#: as a body 50 ranks higher.
PRIOMPT_STUB_PRIORITY_BONUS = 500
#: Seed-file chunks: 1000 minus their distance (in chunks) from the seed
#: symbol's chunk, floored here so the whole seed file stays above every
#: retrieved chunk (<= 490). A flat 1000 makes the seed file all-or-nothing
#: (inferred, per Priompt's own guidance on long files: priority falls with
#: distance from the point of interest).
PRIOMPT_SEED_PRIORITY_FLOOR = 501


# --------------------------------------------------------------------------
# Arm 5 — PRISM manifest ablation (scaffold; OFF)
# --------------------------------------------------------------------------
#: When True, the harness filters PRISM's Turn-1 candidate manifest before
#: the Turn-1 call: downstream symbols farther than
#: PRISM_MANIFEST_STRICT_MAX_HOPS from the seed (PRISM's own weighted
#: distance, the one its manifest's max_hops=3.0 bound uses) are dropped;
#: the seed and PRISM's upstream callers are kept. Applied in the harness
#: wrapper, not in PRISM. No gold-based exception: arms never see gold.
#: M3 ablation lever; do not enable outside it.
PRISM_MANIFEST_STRICT: bool = False
PRISM_MANIFEST_STRICT_MAX_HOPS = 2.0
#: When True, PRISM's Turn-1 system prompt gains a selection-discipline
#: instruction asking for at most PRISM_TURN1_TARGET_MAX symbols (a soft
#: cap: stated to the model, never enforced on its answer). M3 ablation
#: lever (004 requested 50 names, 8 repeats, from a 41-symbol manifest);
#: do not enable outside it.
PRISM_TURN1_STRICT_PROMPT: bool = False
PRISM_TURN1_TARGET_MAX: int = 20
#: Blast-radius mode for T5 (Design C): PRISM's manifest walks the seed's
#: transitive callers under the arm's token budget instead of admitting at
#: most 3 direct callers. False reproduces the M4 Arm 5 behaviour exactly.
PRISM_BLAST_MODE: bool = True


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if value.strip().lower() in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"{name}={value!r}: expected 1/0, true/false, yes/no or on/off")


#: Experiment (harness/experiments/production_routing, OFF): Arm 5 decides
#: the manifest's direction from the query text alone (intent classifier +
#: pool shaping) - no task-type label is read. Off = the plain Arm 5 path.
PRISM_PRODUCTION_ROUTING: bool = _env_flag("HARNESS_PRISM_PRODUCTION_ROUTING")
#: T5 Turn-1 rule selector (config R0, ON by default): on T5 seeds, Turn 1
#: requests every `caller` row of the manifest instead of calling the model
#: (prompt, manifest, Turn-2 hydration and the answer turn unchanged). Other
#: task types are untouched. Default since the R1 vs R0 ablation
#: (reports/harness_r0r1/R0_vs_R1_T5.md). HARNESS_PRISM_T5_RULE_SELECTOR=0
#: restores the model's Turn-1 selection (config R1). Routing (R2) is a
#: separate config: when it is on and this flag is unset, this one is off.
PRISM_T5_RULE_SELECTOR: bool = _env_flag("HARNESS_PRISM_T5_RULE_SELECTOR", default=not PRISM_PRODUCTION_ROUTING)
if PRISM_PRODUCTION_ROUTING and PRISM_T5_RULE_SELECTOR:
    raise ValueError("HARNESS_PRISM_PRODUCTION_ROUTING and HARNESS_PRISM_T5_RULE_SELECTOR are separate configs; "
                     "enable at most one")


# --------------------------------------------------------------------------
# Arm 3 — Pyright LSP (M2)
# --------------------------------------------------------------------------
#: Readiness (handshake to stable workspace/symbol probes) must finish within this.
ARM3_READY_TIMEOUT_S = 120.0
#: Distinct referenced names in the seed symbol's body whose definition is
#: requested (hop 1); bounds the LSP round trips per task.
ARM3_MAX_DEFINITIONS = 40
#: Charter step 1: the repository must be pip-installed editable (its
#: top-level module imports from the checkout) before Pyright launches.
#: Local development without the install sets HARNESS_ARM3_REQUIRE_EDITABLE=0.
ARM3_REQUIRE_EDITABLE_INSTALL = os.environ.get("HARNESS_ARM3_REQUIRE_EDITABLE", "1") == "1"


# --------------------------------------------------------------------------
# Arm 4 — agent loop (M3)
# --------------------------------------------------------------------------
#: Arm 4's turn cap (M4 tests 15 and 20 against this default) and per-turn call cap.
ARM4_MAX_TURNS: int = 10
ARM4_MAX_CALLS_PER_TURN: int = 5
AGENT_GREP_MAX_LINES = 30
AGENT_READ_MAX_LINES = 150
AGENT_COMPACTION_FRACTION = 0.60
AGENT_PRESERVE_LAST_READS = 3
AGENT_TOOL_TIMEOUT_S = 10


# --------------------------------------------------------------------------
# T1 judge (M4)
# --------------------------------------------------------------------------
#: Held-out judge for T1 (faithfulness, answer relevancy). Never a Qwen
#: model: every arm answers with one. "deepseek" or "gemini", each through
#: its OpenAI-compatible chat-completions endpoint. Without the API key in
#: the environment, T1 scores NaN (judge_status "unavailable"), as before.
T1_JUDGE_PROVIDER: str = os.environ.get("HARNESS_T1_JUDGE_PROVIDER", "deepseek")
T1_JUDGE_PROVIDERS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY",
                 "model": "deepseek-chat"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key_env": "GEMINI_API_KEY",
               "model": "gemini-2.5-flash"},
}
if T1_JUDGE_PROVIDER not in T1_JUDGE_PROVIDERS:
    raise ValueError(f"HARNESS_T1_JUDGE_PROVIDER={T1_JUDGE_PROVIDER!r}: not one of {sorted(T1_JUDGE_PROVIDERS)}")
#: Name of the environment variable that holds the judge's API key.
T1_JUDGE_API_KEY_ENV: str = os.environ.get("HARNESS_T1_JUDGE_API_KEY_ENV",
                                           T1_JUDGE_PROVIDERS[T1_JUDGE_PROVIDER]["api_key_env"])
T1_JUDGE_MODEL: str = os.environ.get("HARNESS_T1_JUDGE_MODEL", T1_JUDGE_PROVIDERS[T1_JUDGE_PROVIDER]["model"])
T1_JUDGE_TEMPERATURE = 0.0
T1_JUDGE_MAX_TOKENS = 1024
T1_JUDGE_TIMEOUT_S = 120
#: waits before each retry on HTTP 429 / 5xx / network errors; then the call fails
T1_JUDGE_BACKOFF_S = (2, 4, 8, 16)
#: Delivered context shown to the judge is capped at this many characters
#: (about the 13,000-token retrieval budget); the cut is recorded.
T1_JUDGE_MAX_CONTEXT_CHARS = 60_000


# --------------------------------------------------------------------------
# Scoring and statistics
# --------------------------------------------------------------------------
TASK_SUCCESS_THRESHOLD = 0.5
#: T2 success is answer-based for every arm. "all": the answer names every
#: gold symbol. "any": it names at least one. In all 90 real T2 tasks the
#: seed symbol is gold and is named in the prompt, so "any" is satisfied by
#: echoing the question; "all" is the default for that reason.
T2_ANSWER_RULE = "all"
RANK_CUTOFFS = (5, 10)
HALLUCINATION_MIN_IDENT_LEN = 4
RIPGREP_TIMEOUT_S = 5
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 42
ALPHA = 0.05


# --------------------------------------------------------------------------
# Noise robustness (infrastructure only; execution deferred)
# --------------------------------------------------------------------------
NOISE_SWEEP_ENABLED = False

#: One-off M1 diagnostic (harness/scoring/arm1_t2_diagnostic.py): dump Arm
#: 1's top-5 chunks per T2 task to arm1_t2_diagnostic.json. Off by default;
#: not for regular runs.
ARM1_T2_DIAGNOSTIC = os.environ.get("HARNESS_ARM1_T2_DIAGNOSTIC", "0") == "1"
NOISE_LEVELS = [0.0, 0.10, 0.25, 0.50]
NOISE_TYPES = ["adjacent", "same_domain", "random"]


# --------------------------------------------------------------------------
# Kaggle / GPU
# --------------------------------------------------------------------------
GPU_MEMORY_ABORT_MB = 14_000
KAGGLE_WORKING = Path(os.environ.get("HARNESS_WORKDIR", "/kaggle/working"))
BUNDLES_DIR = KAGGLE_WORKING / "bundles"
COMPLETIONS_DIR = KAGGLE_WORKING / "completions"
RESULTS_DIR = KAGGLE_WORKING / "results"
