# Kaggle pilot execution workflow

This describes how to run the Prism pilot on Kaggle.

## Branch

Clone the `pilot-execution` branch:

```
git clone --branch pilot-execution --single-branch <repo-url>
```

This branch carries no engine, benchmark, or test changes relative to
`claude/sce-mcp-local-setup-5qo69f` - it exists only to host this
Kaggle workflow doc.

Pinned commit for the pilot run: 41082d8

After cloning, the Kaggle cell should `git reset --hard 41082d8` -
this ensures the run uses exactly this revision regardless of later
pushes to `pilot-execution`.

## Where results go

Push pilot results (checkpoint, reports) to the `pilot-results` branch.
Do not push results to `pilot-execution` - keep the workflow branch and
the results branch separate so re-running the notebook never conflicts
with, or overwrites, a prior run's committed output.

## Notebook environment

The notebook must run with **GPU T4 x2** enabled (Kaggle accelerator
setting) - this is what hosts the local Ollama instance the pilot
calls against.

## Environment variables

The pilot reads its LLM client configuration from three env vars
(`benchmarks/tsr/client.py`):

- `LLM_BASE_URL` - set by the notebook to point at the local Ollama
  instance running on the Kaggle GPU node (e.g. `http://localhost:11434/v1`).
- `LLM_MODEL` - set by the notebook to the Ollama model tag being
  pinned for this run (e.g. `qwen2.5:7b-instruct-q8_0`).
- `LLM_API_KEY_ENV` - names the env var the API key is read from. Local
  Ollama needs no real key; the notebook sets this to an env var name
  it also sets to any placeholder value, since `DeepSeekClient.__init__`
  always reads a key through this indirection.

All three are resolved inside `DeepSeekClient.__init__` at construction
time, not at module import time, so setting them in the notebook before
constructing the client is sufficient - no code changes are needed.
