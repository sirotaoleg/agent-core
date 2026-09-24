# Context Migration Document: BU Driver + browser_move Restructure

> Branch: `restore-mcp` @ `989c868a` (pushed to `myfork/restore-mcp`) · Base: `agtai/develop` @ `1c22b9f2` · Date: 2026-09-18

## 1. Goal and Business Context

**Problem.** The browser subagent (`openjiuwen/harness/tools/browser_move/`) was hard-wired to a single automation backend (Playwright MCP): orchestration logic (page state, probe scoring, batching, waiting) and backend specifics (MCP protocol, npx launch) lived in the same modules under `playwright_runtime/`. Adding a second backend — or a future CUA (screenshot + x,y coordinates) backend — required rewriting orchestration.

**Delivered.** A driver abstraction layer: `runtime/` (backend-agnostic orchestration) talks to `backends/contract/` (the `BrowserDriver` protocol) on the driver path. `backends/browser_use/` is the only **registered** `BrowserDriver` implementation (runs `browser-use==0.13.10` in a sidecar process). `backends/playwright_mcp/` is the preserved legacy MCP path — it does **not** implement `BrowserDriver` and its name is explicitly **reserved** in `backends/contract/registry.py` (`RESERVED_BACKEND_NAMES`, per decision D5; registering it raises `ValueError`). Backend selection = env var `BROWSER_DRIVER_BACKEND` (default `browser_use`; resolved by `runtime/config.py:resolve_browser_driver_backend`): any value other than `browser_use` makes `BrowserService.uses_browser_driver()` return `False`, routing execution through the legacy MCP code path instead of a driver. So the switch selects *driver path vs legacy MCP path*, not one of two peer drivers.

**Constraints honored:**

- Fork workflow: `sirotaoleg/agent-core`, branch `restore-mcp` → upstream `agtai/agent-core` `develop`. **Standing policy: never push without explicit user permission.**
- Supervisor's merge gate: no merge until behavior regression evidence exists (BU vs Playwright performance; WebArena Infinity unavailable, so a scripted task list is the fallback).
- Supervisor's review gate: 18k LOC / 118 files judged unreviewable → PR was merged prematurely by the user, then reverted (upstream PR #4); a new reviewable PR must be produced.
- Windows dev machine; sidecar venv is uv-managed at `C:\Users\olegs\agent-core\.venvs\browser-use` (Python 3.12.14, `browser-use 0.13.10`, no pip inside). LLM credentials must never enter the sidecar process.
- Base moved forward in parallel: 23 commits on each side since merge-base `fd30965b`; two of them (`1e143347` = #987 pinned `@playwright/mcp@0.0.78`; `05db3252` = #1147 tool pruning / sync-task-tool routing) rewrote the same files the branch restructured.

## 2. Architectural Decisions (ADRs)

**ADR-1: Driver = "eyes and hands only".** The `BrowserDriver` protocol observes and acts; it never sees planning state (`target_id`, `PageState.generation_id`). Element addressing via value types `IndexRef` (carries `driver_generation`; mismatch ⇒ `StaleIndexError`, never a wrong click), `SelectorRef`, `TextRef`, `NodeRef`. Rejected alternative: letting drivers hold page-state — would re-tangle the layers the refactor exists to separate.

**ADR-2: Sidecar process for browser-use.** Separate Python process + JSON wire protocol (`sidecar/wire.py`), interpreter discovered via `BROWSER_USE_SIDECAR_PYTHON` / `transport.py`. Rationale: (a) browser-use's heavy dependency tree stays out of the main harness env; (b) credential isolation — only browser-related env vars cross the boundary. Rejected: in-process import of browser-use.

**ADR-3: Merge via `git merge --no-commit --no-ff agtai/develop`, not rebase.** 34 renames + parallel rewrites made rebase (replaying 23 commits one-by-one across renames) impractical. Policy confirmed by user: **keep new layout (Policy A), port useful base commits, resolve conflicts only for the PR.**

**ADR-4: Union integration for the `runtime.py` double-rewrite.** Both sides rewrote runtime; base's tests depend on base-only methods. Options: (a) HEAD-only — breaks base tests; (b) base-only — loses the BU driver; (c) **union** — chosen. Executed programmatically: AST-driven extraction of base-only constants/methods (`BrowserAgentRuntime`, `BrowserRuntimeRail`, module functions) and insertion into HEAD's `runtime/runtime.py`. The `_merge_tools/` scripts used for this were deleted after completion.

**ADR-5: xfail instead of delete for 26 base tests.** They assert rail/catalog/semantic behavior superseded by the driver design. Each carries `@pytest.mark.xfail(reason=..., strict=False)` with a documented reason so the reviewer can veto individually. Rejected: deleting them (hides the architectural disagreement from review).

**ADR-6: `selectionSourceFromUrl` ported, not deferred.** Initially xfailed as "deferred port", investigation showed HEAD's `runtime/probe_js.py` *called* the function (line ~687) without defining it — a latent bug, not a porting choice. Ported the definition from base's `playwright_runtime/probes.py` (lines 598–630) into `probe_js.py` before its call site, converting `{{ }}` → `{ }` for HEAD's raw-string style; validated with `node --check`; xfail removed; test passes.

**ADR-7: Satellite-file resolutions (contracts to preserve):**

- `service.py`: HEAD's `uses_browser_driver()` gate kept; npx existence check replaced by base's `_validate_mcp_command`. MCP validation only runs on the non-driver path.
- `browser_capabilities.py`: union — HEAD's `BROWSER_DRIVER_DEFERRED_CORE_TOOL_NAMES` + base's `EXTENDED_INTERACTION_BROWSER_TOOL_NAMES` both kept; `browser_take_screenshot` stays in `VISION_BROWSER_TOOL_NAMES`.
- `page_state.py`: union — HEAD's `_bind_snapshot_ref` (BU-specific) + base's `set_requested_fields`.
- `browser_state_context_processor.py`: HEAD's `mutating` backbone + base's `reconciliation` logic merged.
- `subagents/browser_agent.py`: HEAD's BU-oriented system prompts kept; base's processor-based working-context wiring accepted; unused `BrowserWorkingContextRail` import dropped (rail superseded by processor).
- `runtime_tools.py`: base's #1147 5-tool pruning ported into the **new** location, applied to the MCP (non-driver) path only.

**ADR-8: PR recovery via revert-of-revert.** Premature merge → revert PR #4 merged → GitHub shows "Nothing to compare" for `restore-mcp` (commits already in history). Decision: create the new reviewable PR by reverting PR #4 (restores the changes as a new diff). Alternative (cherry-pick onto a fresh branch) considered heavier; not yet executed either way.

## 3. Map of Changes

**Layout (34 pure renames, review with `git diff -M`):** `playwright_runtime/` split → `runtime/` (agnostic brain) + `backends/playwright_mcp/` (backend-specific); `utils/env.py` → `shared/env.py`. Import invariant (precise form): **driver-path** code in `runtime/` talks only to `backends/contract/`, never a concrete backend. The preserved **legacy MCP path** still imports `backends.playwright_mcp` directly — `runtime/service.py` (two lazy imports inside `uses_browser_driver()`-gated branches), `runtime/main.py` (stdio/HTTP clients), `runtime/runtime.py` (`ensure_browser_runtime_client_patch`), and `runtime/__init__.py` (lazy `import_module` re-exports). These are all on the non-driver path; `backends/browser_use/` is never imported from `runtime/` outside the contract.

**New code:**

- `backends/contract/` — `base.py` (protocol + ElementRef types), `errors.py` (`StaleIndexError`, `DriverUnsupported`, …), `registry.py` (env → driver class).
- `backends/browser_use/` — `driver.py`, `transport.py`, `sidecar/` (`main.py`, `wire.py`, `session_adapter.py`, `mapping.py`, `cdp.py`, `keys.py`).
- `chrome/managed_browser.py`, `lab/run_bu_prompt.py` (manual prompt runner; resolves query from `BU_QUERY`/`BU_QUERY_FILE`, CDP from `BROWSER_CDP_URL` — user runs Chrome on port **9333**).

**Modified in `runtime/`:** `runtime.py` (union, see ADR-4; ruff went 3→1 errors), `probe_js.py` (ADR-6), `browser_capabilities.py`, `page_state.py`, `service.py`, `browser_state_context_processor.py`, `runtime_tools.py` (all per ADR-7).

**Outside `browser_move/` (blast radius):** one-liners in `react_agent.py`, `subagent_rail.py`, `task_tool.py`; `_multimodal.py` (~52/52 lines); `subagents/browser_agent.py`; `.gitignore` (+1); two harness feature docs (`openjiuwen/harness/docs/features/F_02_browser-semantic-neutrality.md`, `F_03_script-intent-casing-and-classifier-guards.md`); EN/ZH doc pages (doc kept HEAD layout + base's pinned `@0.0.78`). Public API `create_browser_agent(...)` signature unchanged; **old `browser_move.playwright_runtime.*` import paths are gone with no shims.**

**Tests:** `test_service.py` patch targets remapped `playwright_runtime.*` → `runtime.*` / `backends.playwright_mcp.*`; MCP-registration tests forced into MCP mode via `monkeypatch.setenv("BROWSER_DRIVER_BACKEND", "playwright_mcp")` (because `_make_service()` defaults to the BU driver, which skips MCP registration). 26 xfail markers, distributed: `test_browser_runtime_rail.py` (21), `test_browser_probe_interactives.py` (2), `test_browser_semantic_state.py` (1), `test_browser_tool_semantics.py` (1), `test_create_browser_agent.py` (1). None remain in `test_browser_runtime_tools.py` or `test_browser_site_profiles.py`.

**Verified baseline:** browser_move unit suite **739 passed / 26 xfailed** (re-verified 2026-09-18 on `989c868a`); full unit suite green excluding `tests/unit_tests/agent_evolving/agent_rl` (optional `omegaconf` dep missing); ruff adds 0 new violations (remaining `E501`/`I001` are pre-existing from base); probe JS passes `node --check`.

**Git state now:** `restore-mcp` @ `989c868a` "Merge agtai/develop into restore-mcp (BU driver + ported #987/#1147)", pushed to `myfork/restore-mcp`. Upstream `agtai/develop` @ `1c22b9f2`. Working tree: only untracked junk + CRLF-stat noise on 3 test files (empty diffs).

## 4. Unresolved Issues and Technical Debt

1. **Junk file still tracked:** `document.querySelector('.figure` is in `git ls-files`. The planned `git rm` + `chore` commit was never executed. Must be removed before any PR.
2. **New PR not created.** Revert-of-revert (ADR-8) was decided but not performed. Until then, upstream has no open PR with this work.
3. **26 xfailed tests are deferred decisions**, not fixes. Each represents base-asserted behavior (tool catalog counts — base expects fewer tools than HEAD's 20; rail semantics; prompt assertions) that the reviewer must either accept as superseded or demand a port for.
4. **No regression harness** — the supervisor's explicit merge blocker. Nothing measures BU-vs-Playwright task performance. Fallback plan (scripted task list run against both backends via the env-var flip) was proposed, never built.
5. **No import shims / mapping table** for `playwright_runtime.*` consumers. A mapping table was promised to the supervisor; producing it (or temporary re-export modules) is open.
6. **PR split not executed.** Proposed stack: moves → contract → BU driver → runtime integration → tests/docs. Supervisor's "lots of things touched that don't need to be touched" remains unanswered by action.
7. **Untracked working-dir junk:** `auto_harness/`, `sub_agents/`, a file literally named `tatus --short` (mistyped redirect). Not ignored, will pollute future `git status`.
8. **CDP port defaults diverge from the user's environment (9333):** `lab/run_bu_prompt.py` itself has no hardcoded port (it reads `BROWSER_CDP_URL`/`PLAYWRIGHT_CDP_URL` from env, empty when unset), but `browser_move/.env.template` and `lab/set_mcp_driver.cmd` both default to `http://127.0.0.1:9222`. Caused one debugging session already — worth aligning to 9333 or documenting.
9. **Sidecar env has no pip** (uv-managed); any dependency change must go through `uv`, not `pip install`.

## 5. Instructions for the New Agent

Work in English with the user. **Never push or merge without explicit permission.** Current branch: `restore-mcp`; base: `agtai/develop`.

Immediate sequence:

1. `git rm "document.querySelector('.figure"` + commit (`chore: remove accidentally committed junk file`). Consider adding `auto_harness/`, `sub_agents/` to `.gitignore` in the same pass (ask the user first — `sub_agents/` may be tool output they want).
2. Get the review vehicle open: either revert-of-revert of upstream PR #4 (fast) or, if the supervisor prefers small PRs, build the 5-PR stack from `989c868a` (moves-only PR first — it's 34 renames, near-zero review cost with `git diff -M`).
3. Build the regression harness — this is the actual merge blocker. Minimal viable version: a fixed list of scripted browser tasks in `lab/`, run twice (`BROWSER_DRIVER_BACKEND=browser_use` vs `playwright_mcp`) against the same Chrome (`--remote-debugging-port=9333`), comparing success/steps. Reuse `lab/run_bu_prompt.py` as the skeleton.
4. Produce the `playwright_runtime.* → runtime.* / backends.playwright_mcp.*` mapping table for the PR description (derivable from `git diff -M --name-status fd30965b..HEAD`).
5. Re-verify before any commit: `pytest tests/unit_tests/harness/tools/browser_move` must stay at 739 passed / 26 xfailed; ruff on touched files must add nothing.

Key invariants to not break: `runtime/` must never import a concrete backend; driver never receives `target_id`/`generation_id`; `IndexRef.driver_generation` mismatch must raise `StaleIndexError`; LLM credentials must not reach the sidecar; xfail markers must not be silently deleted — each removal needs the underlying behavior actually reconciled (the `selectionSourceFromUrl` port is the template for doing this right).
