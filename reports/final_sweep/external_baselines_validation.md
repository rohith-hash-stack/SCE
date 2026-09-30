# External baselines: implementation audit and literature attribution

Branch `test/pagerank-repomap-smoke` (verified with `git branch --show-current`).

**Reference source.** Aider audited at `Aider-AI/aider` commit
`5dc9490bb35f9729ef2c95d00a19ccd30c26339c`, files `aider/repomap.py`,
`aider/models.py` and `aider/coders/base_coder.py`, read directly from
GitHub.

**How each citation was checked** (column "verified" in §4):
- *verified*: checked against the project's own repository or source.
- *from memory*: this environment cannot reach arXiv, ACL Anthology or
  the ACM DL. Check these before submission.

---

## 1. Verdict

`baseline_pagerank_repomap` is **Aider-inspired, not a faithful
replication of Aider's repo map.**

**What it shares with Aider:**
- tree-sitter-derived references;
- networkx PageRank at α = 0.85;
- query-aware biasing;
- a token-budgeted, signatures-only map;
- no knowledge of the seed symbol.

**Where it differs on points that change what gets ranked:**
- graph granularity (symbols, not files);
- how edges are formed (resolved calls, not name matches);
- Aider's edge-weight heuristics (all absent);
- how query mentions enter PageRank (teleport, not edge weight);
- budget size and rendering.

**For the paper:** either describe it as *"a symbol-level personalized-
PageRank repo map in the style of Aider"*, or add a faithful Aider port
before calling it "Aider" (§2.5).

---

## 2. Audit of `benchmarks/baselines/pagerank_repomap.py` against Aider

### 2.1 Graph construction

| aspect | Aider (`repomap.py`) | ours | match |
|---|---|---|---|
| Parser | tree-sitter, via per-language `tags.scm` queries (`get_scm_fname`) that emit `def` and `ref` tags per identifier | tree-sitter, via PRISM's `ConcreteGraphBuilder` (Pass 1 definitions, Pass 2 resolved relations) | parser yes; query mechanism differs |
| Nodes | **files** (`nx.MultiDiGraph`, node = relative filename) | **symbols** (qualified names from the symbol table) | **no** |
| Edges | for each identifier, referencer file → each file *defining that name*. Linking is **by name**, so ambiguity-tolerant: one reference links to all definers | one edge per **resolved** AST relation (CALLS, INSTANTIATES, EXTENDS, IMPLEMENTS, READS_STATE, …) between two symbols; unresolved/dynamic targets dropped | **no** |
| Edge weight | `mul · sqrt(num_refs)`, where `mul` starts at 1 and gets ×10 if the ident is mentioned in chat; ×10 if it is snake/camel/kebab with len ≥ 8; ×0.1 if it starts with `_`; ×0.1 if defined in > 5 files; ×50 if the referencer is a chat file. Self-loop weight 0.1 for defs with no refs | count of parallel relations between the two symbols; none of Aider's multipliers | **no** |
| Rank → symbols | file PageRank is distributed over each file's out-edges in proportion to weight, accumulated per `(definer file, ident)` | PageRank is computed on symbols directly | differs (consequence of node choice) |

### 2.2 PageRank

| aspect | Aider | ours | match |
|---|---|---|---|
| Damping | `nx.pagerank(G, weight="weight", …)` with no `alpha`, so the networkx default **0.85** | `alpha=0.85`, `weight="weight"` | **yes** |
| Personalization targets | **files**: chat files, mentioned filenames, and files whose path components match a mentioned identifier each get `100 / len(fnames)`; also passed as `dangling=` | **symbols** whose bare name matches a query identifier get teleport weight 100, all others 1; no `dangling` vector | partial |
| How query identifiers act | mainly through **edge weights** (×10 on referencing edges); files only through the teleport vector | only through the **teleport vector** | **no** |
| Identifier extraction | `get_ident_mentions`: `re.split(r"\W+", text)` over the chat, case-sensitive, no splitting of compound names | `lexical_tokens`: lower-cased, compound names split into parts; mentions need bare-name length ≥ 3 and exclude a small generic-name list | partial |

### 2.3 Context packing and token budget

| aspect | Aider | ours | match |
|---|---|---|---|
| Budget | `map_tokens` = max_input_tokens/8, clamped to **[1024, 4096]** (default 1024 when unknown). Multiplied by `map_mul_no_files` (RepoMap default 8, CLI default 2) when no files are in chat, capped by the context window | fixed **4,000** tokens | range yes; policy no |
| Selection | **binary search** over the number of top-ranked tags until the rendered map is ≤ budget, accepting within 15% (`ok_err = 0.15`) | **greedy** in rank order, skipping entries that don't fit; stops when < 20 tokens remain, then trims to the exact rendered budget | differs |
| Token counting | the main model's own tokenizer (`main_model.token_count`); a sampled estimate for long text | exact `tiktoken` `cl100k_base` count of the **rendered** context (`prism.slicer.tokenizer.count_tokens`) | measured in both; different tokenizer |
| Rendering | `grep_ast.TreeContext`: per-file tree of lines of interest with enclosing scope lines, `⋮` elision between them, lines cut to 100 chars; chat files excluded | per-symbol XML node whose body is the declaration header (`_signature_stub`); no per-file grouping, no enclosing-scope lines, no `⋮` markers | **no** |
| Scope identifiers | shown through enclosing lines (e.g. `class X:` above `def method`) | shown through the qualified name in the node id (`module.Class.method`) | equivalent information, different form |

### 2.4 Intentional differences, and whether they're justified

1. **Symbol nodes, resolved edges.** This reuses the same AST index as
   every other arm, so the comparison varies only the ranking policy.
   Defensible as a controlled ablation, but it is not Aider.
2. **No Aider edge heuristics.** These heuristics (length, privacy,
   ubiquity and chat multipliers) are part of Aider's ranking quality, so
   leaving them out likely *understates* Aider.
3. **Budget of 4,000 tokens.** This is Aider's maximum. Aider's default is
   1024, and its effective size depends on the model window and whether
   files are in chat. Other arms use 8,000; this is disclosed.
4. **Rendering.** A per-symbol signature list instead of a file tree with
   `⋮` elision. Content is similar (headers); layout is not.
5. **No chat-file state.** A benchmark cell has no files "in chat", so
   Aider's strongest personalization signal (chat files, ×50 edges) never
   fires. That also applies to a real Aider run with no files added.

### 2.5 Recommended action

Add a faithful port as a second experimental arm, `baseline_aider_repomap`:
- file-level `MultiDiGraph` with name-matched def→ref edges;
- Aider's exact `mul`/`sqrt` weighting, with personalization and
  `dangling`;
- rank distributed to `(file, ident)`;
- binary-search packing with `grep_ast.TreeContext` rendering and
  `map_tokens` sized by Aider's own rule for the model's context window.

Keep `baseline_pagerank_repomap` as the symbol-level ablation. Only the
port should be called "Aider" in the paper.

---

## 3. The three external paradigms

### 3.1 Graph centrality / AST PageRank (Aider repo map)

- **Primary sources:**
  - Aider-AI/aider, `aider/repomap.py` (audited at `5dc9490`).
  - P. Gauthier, "Building a better repository map with tree sitter",
    aider.chat blog, 22 Oct 2023 (source file
    `aider/website/_posts/2023-10-22-repomap.md` at the same commit).
- **Mechanism:**
  1. tree-sitter `tags.scm` queries produce def/ref tags;
  2. a file-level reference graph with heuristic edge weights;
  3. personalized PageRank (α = 0.85);
  4. rank distributed to definitions;
  5. binary-search packing of a `⋮`-elided signature tree into
     `map_tokens` (1024–4096).
- **Why it's the primary static comparison:** it is the most widely used
  open-source *static, deterministic, query-aware* repository summarizer.
  Like PRISM it needs no embeddings and no multi-turn exploration, so the
  comparison isolates the ranking and selection policy. Unlike PRISM, it
  ranks by global graph centrality rather than following a causal chain
  from an anchor. Our FastAPI dry run shows the consequence: the seed
  ranks 10–25, but downstream pipeline stages rank about 70–250 and fall
  outside the budget (`smoke_pagerank_repomap.md`).

### 3.2 Hybrid dense + lexical RAG (Cursor, LangChain, Sourcegraph)

- **References:**
  - Lexical: S. Robertson and H. Zaragoza, "The Probabilistic Relevance
    Framework: BM25 and Beyond", *Foundations and Trends in Information
    Retrieval* 3(4), 2009 (from memory).
  - Fusion: G. V. Cormack, C. L. A. Clarke and S. Büttcher, "Reciprocal
    Rank Fusion outperforms Condorcet and individual rank learning
    methods", SIGIR 2009 (verified via LangChain's source, which cites
    `cormacksigir09-rrf.pdf`). RRF score(d) = Σ_r 1/(k + rank_r(d)), with
    k = 60.
  - Dense code encoders:
    - Z. Feng et al., "CodeBERT: A Pre-Trained Model for Programming and
      Natural Languages", Findings of EMNLP 2020 (from memory).
    - D. Guo et al., "GraphCodeBERT", ICLR 2021 (from memory).
    - D. Guo et al., "UniXcoder: Unified Cross-Modal Pre-training for
      Code Representation", ACL 2022 (arXiv:2203.03850; verified from
      `microsoft/CodeBERT/UniXcoder/README.md`).

    **Correction to the brief:** CodeBERT is Feng et al., not Guo et al.
  - Retrieval-augmented generation: P. Lewis et al., NeurIPS 2020 (from
    memory).
  - Repository-level code RAG: F. Zhang et al., "RepoCoder", EMNLP 2023
    (from memory).
- **Industry practice:**
  - **LangChain** `EnsembleRetriever` implements *weighted* RRF with
    `c = 60` (verified in source).
  - **Cursor and Sourcegraph Cody** have no peer-reviewed specification of
    their retrieval. Cite them only as industry practice, not as a
    specific algorithm.
- **Mechanism:**
  1. chunk the repository (by file window or AST node);
  2. embed chunks with a code encoder; also build a BM25 index;
  3. retrieve top-k from each for the query;
  4. fuse with RRF and pack top chunks to budget.
- **Limitation compared to PRISM (to test, not assert):**
  - Retrieval is by *textual or semantic similarity to the query*, so
    code with low lexical overlap with the task text can be missed. Route
    dispatchers, factories and transitive type dependencies are examples.
  - Chunking can split a call chain across chunks without linking them.
  - PRISM follows resolved call edges from an anchor instead.
- **Status in this repository:** only a BM25 arm exists
  (`benchmarks/engines/baseline_rag.py`, not in the final sweep). There is
  **no dense or hybrid RRF arm**, so no measured claim about hybrid RAG
  can be made yet.

### 3.3 Multi-turn interactive tool agents (SWE-agent / ACI)

- **References (verified from project repositories):**
  - J. Yang, C. E. Jimenez, A. Wettig, K. Lieret, S. Yao, K. R. Narasimhan
    and O. Press, "SWE-agent: Agent-Computer Interfaces Enable Automated
    Software Engineering", NeurIPS 2024, arXiv:2405.15793.
  - C. E. Jimenez et al., "SWE-bench: Can Language Models Resolve
    Real-world GitHub Issues?", ICLR 2024.
  - C. S. Xia, Y. Deng, S. Dunn and L. Zhang, "Agentless: Demystifying
    LLM-based Software Engineering Agents", arXiv preprint 2024. This is
    the closest non-agent counterpart to PRISM's fixed-step localization.
  - S. Yao et al., "ReAct", ICLR 2023 (from memory).
- **Mechanism:** the LLM alternates reasoning and actions through a
  purpose-built agent-computer interface (file viewer with a window,
  search, edit and shell commands) over many turns, until it submits or
  hits a step or cost limit.
- **Cost and latency contrast:**
  - Agents make many LLM calls per task, each carrying the growing
    trajectory.
  - PRISM makes exactly **2 calls per cell** (1 for single-pass arms) with
    deterministic retrieval between them. Measured in our sweep: gpt-4o-mini
    tRPC `prism_full` averaged **4.35K prompt tokens per cell** across its 2
    calls.
  - The brief's "~50K tokens, multi-minute latency" for SWE-agent is **not
    verified here**. Quote per-instance cost and turn counts from the
    SWE-agent paper's own tables, or measure them, rather than using a
    round estimate.
- **Status in this repository:** no agent arm. Related work only, unless
  run.

---

## 4. Citation table for Related Work / Baseline Methodology

| paradigm | cite | key parameters to state | verified | measured in this work |
|---|---|---|---|---|
| AST PageRank repo map | Aider-AI/aider `aider/repomap.py` @ `5dc9490`; Gauthier 2023 blog | tree-sitter tags; file-level MultiDiGraph; weight = mul·√refs (×10 mentioned, ×10 long ident, ×0.1 `_`-private, ×0.1 defined in > 5 files, ×50 chat referencer); PageRank α = 0.85 with file personalization 100/N and `dangling`; map_tokens = clamp(max_input/8, 1024, 4096); binary-search packing, 15% tolerance | verified (source) | symbol-level variant only (`baseline_pagerank_repomap`); faithful port recommended |
| BM25 | Robertson & Zaragoza 2009 | k1, b (rank-bm25 defaults k1 = 1.5, b = 0.75) | from memory | BM25 used for `ablation_lexical_anchors` anchor selection; `baseline_rag` (BM25 chunks) exists but not in sweep |
| Reciprocal Rank Fusion | Cormack, Clarke & Büttcher, SIGIR 2009 | k = 60 | verified (LangChain source) | not implemented |
| Dense code encoders | Feng et al. 2020 (CodeBERT); Guo et al. 2021 (GraphCodeBERT); Guo et al. 2022 (UniXcoder) | encoder, chunking, top-k | UniXcoder verified; others from memory | not implemented |
| RAG / repo-level RAG | Lewis et al. 2020; Zhang et al. 2023 (RepoCoder) | — | from memory | — |
| Tool agents | Yang et al., NeurIPS 2024 (SWE-agent); Jimenez et al., ICLR 2024 (SWE-bench); Yao et al., ICLR 2023 (ReAct) | turn/cost limits and ACI commands as reported in the SWE-agent paper | SWE-agent and SWE-bench verified; ReAct from memory | not implemented |
| Pipeline (non-agent) localization | Xia et al. 2024 (Agentless), arXiv | hierarchical localization, then repair | verified (repo) | — |

**Constraint for the paper:** only arms actually run may appear in
results tables. Hybrid RAG and tool agents are related work unless
implemented and measured.
