# Build brief: docs-bot (PR documentation checker, PoC)

You are working in a fork of `GoogleCloudPlatform/microservices-demo`. Build a **proof-of-concept GitHub Action** that runs on every pull request. It detects whether the PR's changes make a component's documentation outdated, then either **updates the doc itself** (factual changes) or **asks the PR author** (behaviour / intent changes).

Keep it small, readable and demo-able. Decisions matter more than completeness. The target is about 300–500 lines of Python plus tests.

---

## 1. Design principles (do not violate)

1. **Facts come from scripts, never from the LLM.** Env vars, defaults, manifest values and RPCs are extracted by parsers.
2. **The LLM only interprets code changes** (did behaviour change? which doc section?) and drafts prose. Every claim it makes must cite `file:line`.
3. **The bot auto-commits only script-owned sections.** LLM-written prose is **never** auto-committed. It is posted as a draft for the author to accept or edit.
4. **Humans own intent.** Sections tagged `[owner]` are never edited by the bot.
5. **Fail open.** The bot must never block a merge or fail the PR check. Any error → a short "docs check skipped" comment, exit code 0.
6. **No noise.** Line-number shifts alone are not a doc change. If nothing relevant changed, stay silent (job summary only).

---

## 2. Files to create

```
.github/workflows/docs-check.yml
docs-bot.yaml                                  # component → paths → doc mapping
docs/components/recommendationservice.md       # seed doc (provided, see §3)
tools/docs_bot/
  __init__.py
  main.py          # orchestration + fail-open wrapper
  config.py        # load docs-bot.yaml
  gitutil.py       # changed files, diffs, `git show <sha>:<path>`
  extract.py       # fact extractors (env vars in Python code, manifest env/ports, proto RPCs)
  docfile.py       # read/replace managed blocks between markers; render tables
  llm.py           # Gemini client: JSON output, temperature 0, retry/backoff on 429/5xx
  verify.py        # citation + facts checks on LLM output
  github.py        # sticky PR comment (create/update), push commit
  metrics.py       # run record JSON + $GITHUB_STEP_SUMMARY
  requirements.txt # google-genai, pyyaml, requests
tests/docs_bot/    # pytest: extractors, table rendering, block replacement, verify
```

`docs-bot.yaml`:

```yaml
settings:
  model_env: GEMINI_MODEL          # model id comes from a repo variable, not the code
  max_diff_chars: 12000            # truncate large diffs before sending to the LLM
  ignore_globs: ["**/*_pb2*.py", "**/genproto/**", "**/*.md"]
components:
  recommendationservice:
    paths: ["src/recommendationservice/**", "kubernetes-manifests/recommendationservice.yaml"]
    code_files: ["src/recommendationservice/recommendation_server.py"]
    manifest: "kubernetes-manifests/recommendationservice.yaml"
    proto: { file: "protos/demo.proto", service: "RecommendationService" }
    doc: "docs/components/recommendationservice.md"
```

The config replaces "Discover components" from the full design. Keep the code generic over components, even though the demo configures only one.

---

## 3. Doc format contract

The seed doc is provided (`recommendationservice.md`). Commit it to `docs/components/`. **First, verify its line references against the current code in this fork and fix any that moved.**

- **Managed blocks (script-owned):** content between `<!-- docs-bot:begin <name> -->` and `<!-- docs-bot:end <name> -->`. Two blocks: `configuration` and `api`. The bot regenerates these deterministically.
- **LLM sections:** headings tagged `[LLM, cited]` ("What it does", "How it works"). The bot may only *propose* changes, in a comment.
- **Owner sections:** tagged `[owner]`. Never touched.
- **Meta line:** `<!-- docs-bot:meta source-commit=<sha> -->`. Update it to the PR head sha whenever the bot commits.

**Configuration table format** (the renderer must reproduce the seed exactly for the same facts, so there is no spurious diff on the first run):

```
| Env var | Default in code | Manifest value | Read at |
| --- | --- | --- | --- |
| `NAME` | `default` or — or — (presence check) | `value` or — | `file.py:LINE` |
```

Sort rows alphabetically by name. **Change detection ignores the "Read at" column.** Only regenerate the table if the set of (name, default, manifest value) changed. When regenerating, refresh the line numbers too.

**API table:** one row per `rpc` in the configured proto service: `| RPC | Request | Response |`.

---

## 4. Fact extraction (`extract.py`)

Run each extractor on the **base** and **head** versions of the files (`git show <sha>:<path>`), then diff the results.

- **Env vars in Python code** (regex is fine for the PoC; note in the README that production would use tree-sitter). Handle:
  - `os.environ["X"]` → no default
  - `os.environ.get("X", d)` / `os.getenv("X", d)` → default `d` (or none if no second argument)
  - `"X" in os.environ` → presence check
  Record the first line where each name is read.
- **Manifest:** parse the YAML (multiple documents). For the `Deployment`, take container `env` name → value and `containerPort`s.
- **Proto:** find `service <Name> { ... }` and capture `rpc Name(Req) returns (Resp)`.

"Required" is **not** extracted. It's semantic (some reads are wrapped in try/except), so it belongs to the LLM or human sections. This is a deliberate example of the facts-vs-meaning split.

---

## 5. Flow (`main.py`)

1. Read the event (`GITHUB_EVENT_PATH`). Get the PR number, base sha and head sha. **Loop guard:** exit if the head commit message contains `[docs-bot]`.
2. `git diff --name-only base...head`, then map the files to components through `paths` (ignoring `ignore_globs`). No component → write the summary "no documented components touched" and exit.
3. For each component:
   1. Extract facts at base and head; compute the fact diff.
   2. Render the managed blocks from the head facts. If they differ from the doc (using the change rule in §3), this is a **factual update**.
   3. Build the code diff for `code_files` (unified, truncated to `max_diff_chars`). If it's non-empty, make **one LLM call** (§6).
   4. Run `verify.py` on the LLM output (§7).
4. **Act:**
   - Factual updates → write the doc, commit to the PR branch as `docs-bot`, message `docs(<component>): sync configuration/api with code [docs-bot]`, push with `GITHUB_TOKEN`.
   - Behaviour change → do **not** commit. Put it in the sticky comment with the question for the author and the verified draft.
   - Neither → no comment, job summary only.
5. **Sticky comment:** one comment per PR, found and updated through the marker `<!-- docs-bot-comment -->`. Never post duplicates.
6. Write the metrics (§9).

**Example comment:**

```
<!-- docs-bot-comment -->
### 📘 docs-bot — recommendationservice
**Updated automatically (facts):**
- Configuration: added `MAX_RECOMMENDATIONS` (default `5`, manifest `5`) → committed in <sha>

**Needs your input (behaviour change detected):**
> `ListRecommendations` now uses `user_id` to pick recommendations (recommendation_server.py:72-80).
> "How it works" and "What it does" still say `user_id` is ignored.
**Question:** why was this change made, and should results be stable per user?

<details><summary>Suggested draft for "How it works" (verified citations)</summary>
... draft ...
</details>

_Confidence: high · 1 LLM call · docs-bot PoC_
```

---

## 6. LLM call (`llm.py`)

Use the `google-genai` SDK with the model id from env `GEMINI_MODEL` (a free-tier **Flash** model), `temperature=0` and a JSON response. Retry 429/5xx with exponential backoff (3 attempts). If it still fails, **degrade gracefully**: still apply the factual updates, and note "behaviour check skipped (LLM unavailable)" in the comment.

**Input:** the component name, the unified code diff with head line numbers, the current text of "What it does" and "How it works", and the fact diff (as context only).

**Output schema:**

```json
{
  "behaviour_change": true,
  "confidence": "high|medium|low",
  "affected_sections": ["How it works", "What it does"],
  "summary": "one sentence, what changed in behaviour",
  "question_for_author": "one question about intent",
  "draft": {"How it works": "markdown with (file.py:L1-L2) citations"},
  "claims": [{"text": "...", "file": "src/recommendationservice/recommendation_server.py", "lines": [72, 80]}]
}
```

**Prompt rules:** refactors, logging, formatting and comments are **not** behaviour changes. Do not restate facts that are in the managed tables. Every sentence in `draft` must be backed by a `claims` entry.

---

## 7. Verification (`verify.py`)

For each claim:

1. The file exists at head, and the line range is within the file's length.
2. At least one identifier from the claim text (a backticked name, or a word ≥ 4 characters) appears in the cited lines ±2.
3. Any env var name or port mentioned in the draft exists in the head facts.

Claims that fail are dropped, and any draft sentence citing them is removed. If more than half the claims fail, drop the draft entirely and only ask the question. Record the counts for the metrics.

---

## 8. Workflow (`.github/workflows/docs-check.yml`)

- Trigger: `pull_request` (types `opened`, `synchronize`, `reopened`), paths `src/**`, `kubernetes-manifests/**`, `protos/**`.
- `permissions: contents: write, pull-requests: write`
- `concurrency: docs-check-${{ github.event.pull_request.number }}`, with `cancel-in-progress: true`
- Only run for PRs from this repo: `if: github.event.pull_request.head.repo.full_name == github.repository`
- Steps:
  1. Check out the PR head branch with `fetch-depth: 0`.
  2. Set up Python 3.12.
  3. `pip install -r tools/docs_bot/requirements.txt`
  4. `python -m tools.docs_bot.main` with env `GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}`, `GEMINI_MODEL: ${{ vars.GEMINI_MODEL }}`, `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`
  5. Upload `docs-bot-run.json` as an artifact (`if: always()`).
- The job must always succeed (fail-open). Pushes made with `GITHUB_TOKEN` don't re-trigger workflows; keep the `[docs-bot]` guard anyway.

---

## 9. Metrics (`metrics.py`)

Write `docs-bot-run.json` and a short table to `$GITHUB_STEP_SUMMARY`:

`pr`, `components_touched`, `fact_changes` (list), `factual_update_committed` (bool), `llm_called`, `llm_latency_ms`, `llm_attempts`, `tokens` (if the SDK returns usage), `behaviour_change`, `confidence`, `claims_total`, `claims_dropped`, `action` (`commit|ask|commit+ask|none|skipped`), `error` (if any), `duration_ms`.

These feed the presentation answers on accuracy, cost and failure rate.

---

## 10. Tests (pytest, no network)

- Env var extractor on the real `recommendation_server.py`: it should find the 6 variables from the seed table, with correct defaults and kinds.
- Manifest extractor on the real manifest.
- Proto extractor finds `ListRecommendations`.
- The table renderer reproduces the seed's configuration block exactly.
- Change detection ignores pure line shifts.
- Verify drops a claim whose cited lines don't contain its identifier.
- The LLM client is mocked.

---

## 11. Demo PRs (create after the bot works; each on its own branch in this fork)

**PR 1: "Make recommendation count configurable + personalise by user"** (shows both paths):

- `recommendation_server.py`: replace `max_responses = 5` with `max_responses = int(os.environ.get("MAX_RECOMMENDATIONS", "5"))`, and make the selection deterministic per user, e.g. `rng = random.Random(request.user_id)` → `rng.sample(...)`.
- `kubernetes-manifests/recommendationservice.yaml`: add env `MAX_RECOMMENDATIONS: "5"`.
- **Expected:** the bot commits a new Configuration row, and the comment asks about the `user_id` behaviour with a draft for "How it works" / "What it does".

**PR 2: "Tidy logging"** (control, which shows precision):

- Change only the log message text in `ListRecommendations`.
- **Expected:** no commit, no comment; the summary says no doc change.

(Optional) **PR 3:** add a new RPC to `RecommendationService` in the proto. Expected: an API table update.

---

## 12. README (`tools/docs_bot/README.md`)

Keep it short. Include:

- What it does, with a diagram of the flow.
- How it maps to the full design: GitHub Action = PoC form of the org-wide GitHub App; `docs-bot.yaml` replaces Discover; only PR-time "keep fresh" is implemented.
- Setup.
- Metrics.
- Failure behaviour.
- Future improvements: tree-sitter instead of regex, more languages, "Where it fits" from the cross-repo graph, GitHub suggestion blocks with one-click apply, an eval set of labelled historical PRs for precision and recall, running inside the GitHub App.

---

## Definition of done

- [ ] `pytest` passes locally.
- [ ] Opening PR 1 in the fork produces a bot commit updating the Configuration table **and** one sticky comment asking about the `user_id` change.
- [ ] PR 2 produces no commit and no comment.
- [ ] Removing `GEMINI_API_KEY` still lets PR 1 get the factual commit, with "behaviour check skipped" and a green check.
- [ ] `docs-bot-run.json` is uploaded for each run.
