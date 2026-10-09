# F_07 Browser drivers

## Meta

| Item | Value |
|---|---|
| Type | feature |
| Date | 2026-10-09 |
| Scope | `openjiuwen/harness/tools/browser_move/` (`contract/`, `drivers/`, `provisioning/`, `surfaces/browser/`, `playwright_runtime/config.py`), `tests/unit_tests/harness/tools/browser_move/` |
| Test baseline | see "Verification" in each section |
| Related spec | [[S_20_browser-driver-contract]] |
| Refs | #driver-architecture |

## Background

`browser_move` is being moved onto a driver contract: the agent core sends typed commands and reads typed results
and errors, and each driver (browser-use now, Playwright MCP next) translates them for one library. This document
records the decisions taken while the pieces land, one section per piece; S_20 describes the resulting contract.

## Phases 0–2

- **Preparation.** The open browser PRs were merged first; the browser-use driver was ported from its earlier
  branch with import changes only. WebVoyager baselines were taken on the Playwright MCP path (smoke on this branch)
  and on the browser-use path (on the earlier branch). Every MCP tool and every browser-use tool was mapped onto the
  commands of the contract; the mapping was approved before the contract was written. Outcomes that changed the
  plan: one tool spec may cover several commands with an `action` field (`browser_tabs`); `FillForm` and `Drop`
  became browser commands because both drivers handle them; `RunScript` means page JavaScript on every driver, and
  the unsafe Playwright-code tool gets no command; `Forward`, `Reload` and `GetText` were dropped; the non-core MCP
  tools were not carried over.
- **Safety net.** Before production code moved: architecture tests with allowlists of today's violations (imports,
  driver names, browser words, env reads, module size), golden tool and prompt snapshots per path, a test that pins
  what the WebVoyager runner imports and sets, recorded `@playwright/mcp@0.0.78` answers, and rail tests on a
  command-level fake driver. The env-read test counts reads through `utils/env.py` helpers and key tuples, not only
  `os.getenv` literals. Variables the runner sets but the package never reads (`BROWSER_INSTRUCTION`,
  `BROWSER_LOGS_DIR`) stay in the pinned list, marked as such. The browser-use sidecar is a separate process with
  its own configuration; its env reads are a named exemption.
- **Contract and browser-use on it.** `contract/` (stdlib only; `contract/testing/` may import pytest for the
  conformance plugin), browser commands and `BrowserContext` in `surfaces/browser/`, a conformance suite with a
  local browser scene, `managed_browser` moved to `provisioning/` (old name kept as an alias, `ProvisionedDevice`
  as data), and the browser-use driver rewritten as a `HandlerDriver`. Decisions made on the way: `PolicyDenied` and
  `ActionIntercepted` are raised before dispatch; a stale ref on browser-use is detected while resolving the node,
  before any input; browser-use hit-tests the click point and raises `ActionIntercepted` instead of clicking an
  overlay; sidecar replies report dialogs, popups and navigations (a same-URL reload included); browser-use accepts
  `Locator(css)`. Live conformance on browser-use passes; the MCP goldens did not change.

## Driver middleware

Three wrappers in `drivers/middleware.py`, each a `Driver` around an inner `Driver`, stacked as
`Tracing(RetryOnStale(Timeouts(driver, per_effect)), sink)`. Contract touch-ups that came with them:
`StaleRef.observation`, `ActionIntercepted.default_dispatched = False`, `__sensitive_fields__` on `TypeText`,
`RunScript` and `FormField`, and `ProvisionedDevice.cleanup = None` by default.

### Decisions

1. **Wrappers are drivers.** Each middleware subclasses `Driver` and forwards `info`, `start`, `stop` and `health`,
   so any number of them compose and the composition root sees one `Driver`. They import only `contract/`, and the
   architecture tests scan `drivers/middleware.py` like the core.
2. **Timeouts by effect, expiry counts as dispatched.** The bound comes from the command's `effect` (`observe()`
   is READ); an unlisted effect has no bound. On expiry the inner call is cancelled with `asyncio.timeout`, and
   `ActionTimeout(dispatched=True)` is raised, because a cancelled call may already have reached the screen.
3. **RetryOnStale re-observes but does not resend.** The plan allows a retry only when the stale ref can be remapped
   through a ref kind the driver declares (`IndexRef` or `Locator`). The middleware sees neither the observation the
   stale ref came from nor a mapping from a `DriverRef` to an index or a selector, and neither built driver offers
   one. So it observes once and raises `StaleRef` with that observation attached, which saves the runtime a second
   `observe()`. Errors with `dispatched=True` pass through untouched.
4. **Masking is declared on the command.** `Tracing` masks every field named in a class-level
   `__sensitive_fields__` when the instance's `sensitive` is true, walking nested dataclasses and sequences (so a
   `FormField` inside `FillForm.fields` is found). A surface command opts in by declaring the attribute; the
   middleware has no list of command types.
5. **Trace the shape, not the content.** Command records carry the masked arguments. Result records carry `ok`,
   `detail` and `context_changed`; observation records carry the context id and the element count; error records
   carry the class, message and `dispatched`. `ActionResult.value`, element values and native text are left out,
   because they can echo what a sensitive command typed.
6. **The sink is a callable taking a dict** (`TraceSink`), so the conformance suite's collector and a logging hook
   both fit without an adapter.
7. **`ActionIntercepted` defaults to not dispatched.** Both drivers raise it before any input is sent; the default
   now says so. `ProvisionedDevice.cleanup` defaults to `None`, since most devices need no cleanup.

### Rejected options

- **Remap by visible text in `RetryOnStale`.** Re-targeting a stale `DriverRef` through `Locator(text=name)` could
  hit a different element with the same name, which breaks "every driver means the same thing by the same command".
- **Remember the last observation in `RetryOnStale` to build a remap.** The wrapper would hold state the runtime's
  page state already owns, for a remap no driver can use.
- **A per-command masking table inside `Tracing`.** Every new surface command would need an edit to the middleware,
  which the plan forbids.
- **Gating the timeout on `Wait.seconds`.** Not done: the profile's READ bound has to be longer than the longest
  `Wait` the model may ask for.

### Verification

- `tests/unit_tests/harness/tools/browser_move/drivers/test_middleware.py`: per-effect bounds, `dispatched=True` and
  cancellation of the inner call, no bound for an unlisted effect; one re-observe whose observation the raised
  `StaleRef` carries, no retry of dispatched errors, one send even when the driver stays stale; masking of
  `TypeText`, `RunScript`, `FormField` and a test-local command with `__sensitive_fields__`; traced errors; the
  stack passing `info`, `start`, `stop`, `health` through; the conformance suite on the fake world through the
  full stack.
- `pytest tests/unit_tests/harness/tools/browser_move/drivers/test_middleware.py -q`: **50 passed, 1 skipped**
  (the `IndexRef` happy path the fake scene does not provide).
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1225 passed, 59 skipped** (live suites without the
  sidecar interpreter, and the browser-use tool snapshot).
- Live browser-use conformance, driver wrapped in `Tracing`
  (`BROWSER_USE_SIDECAR_PYTHON=<browser-use venv python>`): **45 passed, 0 skipped**; the two sensitive-trace
  cases that skipped before now run.

### Known leftovers

1. A timeout cancels the browser-use driver's wait for the sidecar's reply, but the transport sends no `cancel` to
   the sidecar on cancellation (only on its own 30 s timeout). The sidecar task keeps running, the next command can
   run alongside it, and its late reply is dropped by id.
2. `RunScript.args` is traced unmasked even when `sensitive` is true; only `code` is listed.
3. `DriverError` messages are traced as they are; a driver must not put sensitive values in them.
4. The fake-surface end-to-end test expects a stale `PointRef` to be retried once; with no remap there is no retry,
   so that expectation needs a remap rule for points first.

## Settings

`CoreSettings` in `playwright_runtime/config.py` and `BrowserSettings` in `surfaces/browser/config.py` parse the
environment variables, split by owner (S_20 "Settings"). This step adds the parsers and moves the public facades
(`RuntimeSettings`, `build_runtime_settings`, `build_playwright_mcp_config`, `build_browser_guardrails`,
`resolve_playwright_mcp_cwd`, `BrowserInstanceConfig`) onto them; no other module reads them yet.

### Decisions

1. **Parsers take the mapping.** `from_env(environ)` reads only what it is given; `from_process_env()` is the one
   `os.environ` access per class. Tests pass a dict while `os.environ` holds conflicting values.
2. **Same precedence and quirks as the code they replace.** Each field reads the same key tuple in the same order,
   with the same defaults. Kept on purpose: `PLAYWRIGHT_MCP_ARGS=""` means no arguments (not the default); the
   worker tool timeout takes the first *set* key and falls back to 240 s if that value is invalid, without trying
   the next key; the log mirror and raw-audit flags are on for any value but a false word;
   `OPENJIUWEN_BROWSER_AGENT_LOG_FILE` set to an empty string means the current directory.
3. **One parse path.** The facades call `from_process_env()` instead of keeping their own reads, so the existing
   config tests, the MCP goldens and the runner contract test exercise the parsers.
4. **`BROWSER_DRIVER` warns in one place.** Both classes read it: `CoreSettings` treats a legacy value as "default
   driver" silently, `BrowserSettings` maps it to `provisioning` and warns. Building both gives one warning.
5. **`CoreSettings` does not reject an unsupported `MODEL_PROVIDER`.** The harness path takes its model from the
   parent agent and calls only `build_browser_guardrails` / `build_playwright_mcp_config`; a `MODEL_PROVIDER` meant
   for another tool must not break it. `build_runtime_settings` raises as before.
6. **`topology` is derived from `driver`**, a property, not a field read from the environment: the default per
   driver is decided (delegated worker for Playwright MCP, direct for browser-use) and no variable exists; the
   choice is measured in the benchmark phase.
7. **No `tracing` field.** No variable switches tracing; the composition root passes the `Tracing` sink. The
   existing raw-audit switch is `log_audit_raw`.
8. **`driver_mode` stays a field.** `BrowserInstanceConfig` appends `provisioning` and `driver` after its eight
   positional fields; `__post_init__` copies whichever of `driver_mode` / `provisioning` is set into the other and
   refuses two different values. `BrowserInstanceConfig(driver_mode=...)` and the dict form used by the teams
   manifest keep working.
9. **Lazy where the old code was lazy.** `selector_cache_path=None` means the default under the home directory, and
   `log_file` defaults to a relative path the logger resolves: computing `Path.home()` at parse time fails when the
   home variables are absent (the existing tests clear the environment).
10. **The parsers do not call `utils/env.py`.** Its helpers keep serving the callers not switched yet; the parsing
    logic is private to each config module. `playwright_runtime/config.py` still re-exports `parse_command_args`,
    `load_repo_dotenv` and the defaults it always exported.
11. **One allowlisted import.** `playwright_runtime/config.py` imports `surfaces.browser.config`, because the public
    `RuntimeSettings` facade composes `BrowserSettings`; recorded in the import-boundary allowlist as legacy until
    the facade can take the surface settings from the surface registry. The env-read test also learns env names
    from the string constants of the config modules: parsers that read a mapping no longer show up as `os.getenv`
    or `utils/env` calls, and the names must stay known for reads elsewhere to be caught.

### Rejected options

- **One settings class with a prefix per owner.** A second surface would edit it.
- **`CoreSettings` importing the provisioning names** to recognise legacy `BROWSER_DRIVER` values: the core would
  depend on the browser surface; it keeps the three legacy names as a private literal.
- **`driver_mode` as a read-only property over `provisioning`.** Breaks the keyword argument and the dict coercion
  that callers outside the package use.
- **New `BROWSER_TOPOLOGY` / tracing variables.** Not in the plan and nothing would read them.
- **Moving the runtime-MCP-server and child-process variables now.** Their owner is not clear (questions below).

### Inventory

Every variable the package reads today. Owner: **core** (`CoreSettings`), **browser** (`BrowserSettings`),
**exempt** (the browser-use sidecar process), **question** (not parsed yet). "Read in" is the module that reads it
today, relative to `browser_move/`.

| Variables (precedence order) | Read in | Meaning | Default | Owner | Field |
|---|---|---|---|---|---|
| `BROWSER_DRIVER` | `playwright_runtime/config.py`, `service.py` | driver; legacy values = provisioning | `playwright_mcp` | core (browser reads legacy values) | `driver` |
| `MODEL_PROVIDER`, `MODEL_CLIENT_PROVIDER` | `utils/env.py`, `browser_tools.py` | model provider | inferred from base URL, then keys, then `openai` | core | `provider` |
| `API_KEY`, `MODEL_API_KEY`, then per provider `OPENROUTER_API_KEY` / `SILICONFLOW_API_KEY` / `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` | `utils/env.py`, `browser_tools.py` | model API key | `""` | core | `api_key` |
| `API_BASE`, `MODEL_API_BASE`, then per provider `OPENROUTER_BASE_URL`, `OPENROUTER_API_BASE`, `SILICONFLOW_BASE_URL`, `SILICONFLOW_API_BASE`, `DASHSCOPE_BASE_URL`, `DASHSCOPE_API_BASE`, `OPENAI_BASE_URL`, `OPENAI_API_BASE` | `utils/env.py`, `browser_tools.py` | model API base; also infers the provider | the provider's public URL | core | `api_base` |
| `MODEL_NAME` | `utils/env.py` | model name | `anthropic/claude-sonnet-4.5` | core | `model_name` |
| `BROWSER_WORKER_TEMPERATURE`, `BROWSER_MODEL_TEMPERATURE`, `MODEL_TEMPERATURE` | `agents.py` | worker sampling, 0–2 | `0.2` | core | `worker_temperature` |
| `BROWSER_WORKER_TOP_P`, `BROWSER_MODEL_TOP_P`, `MODEL_TOP_P` | `agents.py` | worker sampling, 0–1 | `0.1` | core | `worker_top_p` |
| `PLAYWRIGHT_TOOL_TIMEOUT_S`, `PLAYWRIGHT_MCP_TIMEOUT_S`, `BROWSER_TIMEOUT_S` | `agents.py` | worker per-tool-call timeout, s | `240` | core | `tool_timeout_s` |
| `BROWSER_TIMEOUT_S`, `PLAYWRIGHT_TOOL_TIMEOUT_S` | `utils/env.py`, `playwright_runtime_mcp_server.py` | task timeout, s (≥ 1) | `600` | core | `guardrails.timeout_s` |
| `BROWSER_GUARDRAIL_MAX_STEPS` | `playwright_runtime/config.py` | step limit (≥ 1) | `100` | core | `guardrails.max_steps` |
| `BROWSER_GUARDRAIL_MAX_FAILURES` | `playwright_runtime/config.py` | failure limit (≥ 0) | `4` | core | `guardrails.max_failures` |
| `BROWSER_GUARDRAIL_RETRY_ONCE` | `playwright_runtime/config.py` | retry once | `true` | core | `guardrails.retry_once` |
| `BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS` | `playwright_runtime/config.py` | resume at the step limit | `false` | core | `guardrails.resume_on_max_iterations` |
| `OPENJIUWEN_BROWSER_AGENT_LOG_FILE` | `browser_logging.py` | agent log file; false word = none | `logs/browser_agent.log` | core | `log_file` |
| `OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL` | `browser_logging.py` | agent log level | `INFO` | core | `log_level` |
| `OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON` | `browser_logging.py` | also log to the common logger | `false` | core | `log_mirror_common` |
| `OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW` | `browser_logging.py` | store raw audit payloads | `false` | core | `log_audit_raw` |
| `BROWSER_SUBAGENT_STATUS_LOG` | `status_logging.py` | subagent status log | on | core | `status_log` |
| `BROWSER_PROVISIONING` (new), legacy `BROWSER_DRIVER` | — | where the browser comes from | `remote` | browser | `provisioning` |
| `PLAYWRIGHT_MCP_CDP_ENDPOINT`, `PLAYWRIGHT_CDP_URL` | `playwright_runtime/config.py` | CDP endpoint | `""` | browser | `cdp_url` |
| `PLAYWRIGHT_MCP_CDP_HEADERS`, `PLAYWRIGHT_CDP_HEADERS` | `playwright_runtime/config.py` | CDP headers | `""` | browser | `cdp_headers` |
| `PLAYWRIGHT_MCP_CDP_TIMEOUT`, `PLAYWRIGHT_CDP_TIMEOUT_MS` | `playwright_runtime/config.py` | CDP connect timeout | `""` | browser | `cdp_timeout` |
| `BROWSER_MANAGED_HOST` | `service.py` | managed Chrome host | `127.0.0.1` | browser | `managed_host` |
| `BROWSER_MANAGED_PORT` | `service.py` | managed Chrome debug port | `9333` | browser | `managed_port` |
| `BROWSER_MANAGED_KILL_EXISTING` | `service.py` | kill a running Chrome, use its default profile | `false` | browser | `managed_kill_existing` |
| `BROWSER_MANAGED_ARGS` | `service.py` | extra Chrome arguments (JSON list or shell string) | none | browser | `managed_args` |
| `BROWSER_MANAGED_USER_DATA_DIR` | `service.py` | Chrome user-data dir | per profile | browser | `user_data_dir` |
| `BROWSER_PROFILE_NAME` | `service.py` | profile name | `jiuwenclaw` | browser | `profile_name` |
| `BROWSER_PROFILE_STORE_PATH` | `service.py` | profile store file | `<mcp_cwd>/.browser/profiles.json` | browser | `profile_store_path` |
| `BROWSER_MANAGED_BINARY` | `service.py` | Chrome binary | discovered | browser | `browser_binary` |
| `PLAYWRIGHT_MCP_ARGS` | `playwright_runtime/config.py` | Playwright MCP arguments | `-y @playwright/mcp@0.0.78` | browser | `launch_args` |
| `PLAYWRIGHT_MCP_COMMAND` | `playwright_runtime/config.py` | Playwright MCP command | `npx` | browser | `mcp_command` |
| `PLAYWRIGHT_MCP_TIMEOUT_S`, `BROWSER_TIMEOUT_S` | `playwright_runtime/config.py` | MCP client timeout, s (≥ 1) | `600` | browser | `mcp_timeout_s` |
| `PLAYWRIGHT_RUNTIME_MCP_CWD`, `BROWSER_RUNTIME_MCP_CWD`, `PLAYWRIGHT_RUNTIME_WORKDIR`, `BROWSER_RUNTIME_WORKDIR` | `playwright_runtime/config.py` | Playwright MCP working dir, profile root | current dir | browser | `mcp_cwd` |
| `PLAYWRIGHT_MCP_BROWSER` | `playwright_runtime/config.py` | Playwright browser name | `""` (`chrome` with CDP) | browser | `mcp_browser` |
| `PLAYWRIGHT_MCP_DEVICE` | `playwright_runtime/config.py`, `service.py` | device emulation (not with CDP) | `""` | browser | `mcp_device` |
| `PLAYWRIGHT_MCP_EXTENSION` | `playwright_runtime/config.py` | extension mode | `false` | browser | `mcp_extension` |
| `PLAYWRIGHT_MCP_EXTENSION_TOKEN` | `playwright_runtime/config.py` | extension token | `""` | browser | `mcp_extension_token` |
| `PLAYWRIGHT_BROWSERS_PATH`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `PLAYWRIGHT_MCP_ENV_JSON` | `playwright_runtime/config.py` | Playwright MCP child env (JSON wins) | `{}` | browser | `mcp_env` |
| `BROWSER_UPLOAD_ROOT` | `utils/env.py`, `drivers/browser_use/transport.py` | upload sandbox root | none | browser | `upload_root` |
| `BROWSER_USE_SIDECAR_PYTHON` | `drivers/browser_use/transport.py` | sidecar interpreter | `.venvs/browser-use` | browser | `sidecar_python` |
| `OPENJIUWEN_BROWSER_SELECTOR_CACHE` | `site_profiles.py` | site selector cache | `~/.openjiuwen/browser_selector_cache.json` | browser (prefix: question 5) | `selector_cache_path` |
| `BROWSER_UPLOAD_ROOT` | `drivers/browser_use/sidecar/session_adapter.py` | upload root inside the sidecar | none | exempt | — |
| `PLAYWRIGHT_RUNTIME_MCP_*` / `BROWSER_RUNTIME_MCP_*` (`ENABLED`, `SERVER_ID`, `SERVER_NAME`, `CLIENT_TYPE`, `SERVER_PATH`, `HOST`, `PORT`, `PATH`, `COMMAND`, `ARGS`, `TIMEOUT_S`), `PLAYWRIGHT_RUNTIME_MCP_AUTO_HTTP_FALLBACK`, `PLAYWRIGHT_RUNTIME_MCP_TRANSPORT`, `PLAYWRIGHT_RUNTIME_MCP_LOG_LEVEL`, `PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP`, `PLAYWRIGHT_RUNTIME_LOG_DIR`, `BROWSER_RUNTIME_LOG_DIR` | `browser_tools.py`, `playwright_runtime_mcp_server.py` | the browser runtime served as an MCP server | various | question 1 | — |
| whole environment copy; `ALL_PROXY`; `PATH`, `SystemRoot`, `TEMP`, `TMP`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `APPDATA`, `HOME`, `TMPDIR`; every `BROWSER_USE_*` | `browser_tools.py`, `drivers/browser_use/transport.py` | environment handed to child processes | — | question 2 | — |
| `LOCALAPPDATA`, `ProgramFiles`, `ProgramFiles(x86)`, `ProgramW6432` | `provisioning/managed_browser.py` | where Chrome is installed | — | question 3 | — |
| `AGENT_QUERY`, `AGENT_SESSION_ID` | `playwright_runtime/main.py` | demo entry point input | `""`, `demo-browser-session` | question 4 | — |

Counts: 37 core names, 33 browser names plus the new `BROWSER_PROVISIONING`, 1 exempt read, 45 names in questions
(28 runtime-MCP-server, 12 child-process plus the `BROWSER_USE_*` family, 3 more OS install paths, 2 demo). Names
read by both objects: `BROWSER_DRIVER`, `BROWSER_TIMEOUT_S`, `PLAYWRIGHT_MCP_TIMEOUT_S`.

### Verification

- `tests/unit_tests/harness/tools/browser_move/test_core_settings.py`, `surfaces/test_browser_settings.py`: every
  parsed name with its default, an explicit value and, where one exists, an invalid value; the `BROWSER_DRIVER`
  table, one `DeprecationWarning`, `BROWSER_PROVISIONING` winning; `from_env` ignoring a conflicting `os.environ`.
- `tests/unit_tests/harness/tools/browser_move/test_settings_facades.py`: `BrowserInstanceConfig` field order and the
  `driver_mode` alias; `build_runtime_settings` equal to the two settings objects; unsupported provider still
  rejected by it and not by the guardrail / MCP builders; extension provisioning reaching the MCP config.
- The three new files: **108 passed**. `pytest tests/unit_tests/harness/tools/browser_move -q`: **1333 passed, 59
  skipped** (same skips as before; goldens and runner contract unchanged). Harness tests that build the browser
  subagent (`subagent_runtime/`, `task_loop/`, `test_deep_agent*.py`, `test_subagent_rail.py`, `tools/`): **248
  passed**.

### Known leftovers

1. Every module outside the two config modules still reads the environment itself; the env-read allowlist is
   unchanged.
2. Facades now fail earlier on some invalid input: an unknown `BROWSER_DRIVER` raises when the guardrails or the
   runtime settings are built (before: when the browser service started, and not at all when the instance carried a
   `driver_mode`); a `BROWSER_MANAGED_PORT` that is not a positive integer raises whenever `BrowserSettings` is
   built (before: only when managed Chrome started without a key or an explicit port). A legacy `BROWSER_DRIVER`
   now warns from `build_playwright_mcp_config`, `build_runtime_settings` and `resolve_playwright_mcp_cwd`.
3. Questions on the names not parsed yet:
   1. The runtime-MCP-server variables configure `browser_move` served as an MCP server to another agent (client
      registration in `browser_tools.py`, server entry point). Prefixes say browser, meaning says entry point. The
      client reads both `PLAYWRIGHT_RUNTIME_*` and `BROWSER_RUNTIME_*`, the server only `PLAYWRIGHT_RUNTIME_*`, with
      different defaults (path, log dir); one field per name pair would let the server honour the aliases.
   2. Child-process environments copy the whole environment or a list of OS variables. Proposal: they become pure
      functions of the mapping the entry point passed to `from_env`, not settings fields.
   3. Chrome install discovery reads OS install paths; it belongs with the managed provisioner.
   4. `AGENT_QUERY` / `AGENT_SESSION_ID` are inputs of the demo entry point, not settings.
   5. `OPENJIUWEN_BROWSER_SELECTOR_CACHE` is browser by meaning but outside `ENV_PREFIXES`; core names also start
      with `BROWSER_` (`BROWSER_DRIVER`, guardrails, timeouts), so the prefixes declare a namespace, not ownership.
