# F_07 Browser drivers

## Meta

| Item | Value |
|---|---|
| Type | feature |
| Date | 2026-10-11 |
| Scope | `openjiuwen/harness/tools/browser_move/` (`contract/`, `drivers/`, `provisioning/`, `surfaces/browser/`, `playwright_runtime/`, `controllers/action.py`, `utils/`, `playwright_runtime_mcp_server.py`), `openjiuwen/harness/subagents/browser_agent.py`, `openjiuwen/core/common/logging/tool_call.py`, `openjiuwen/core/single_agent/agents/react_agent.py`, `tests/unit_tests/harness/tools/browser_move/` |
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

1. Settled in "Environment boundary" (G11): the transport now sends `cancel` when the caller cancels the request.
2. Settled (G10): `RunScript.args` is a sensitive field.
3. Settled for sensitive commands (G10): their error message is not traced. Messages of other commands are traced
   as they are.
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
    logic is private to each config module. (Superseded by "Environment boundary", decision 2: both parsers now share
    the pure helpers of `utils/env.py`.) `playwright_runtime/config.py` still re-exports `parse_command_args`,
    `load_repo_dotenv` and the defaults it always exported.
11. **One allowlisted import.** `playwright_runtime/config.py` imports `surfaces.browser.config`, because the public
    `RuntimeSettings` facade composes `BrowserSettings`; recorded in the import-boundary allowlist as legacy until
    the facade can take the surface settings from the surface registry. The env-read test also learns env names
    from the string constants of the config modules: parsers that read a mapping no longer show up as `os.getenv`
    or `utils/env` calls, and the names must stay known for reads elsewhere to be caught. (The import is gone since
    "Composition root", decision 4.)

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
| `PLAYWRIGHT_RUNTIME_MCP_*` / `BROWSER_RUNTIME_MCP_*` (`ENABLED`, `SERVER_ID`, `SERVER_NAME`, `CLIENT_TYPE`, `SERVER_PATH`, `HOST`, `PORT`, `PATH`, `COMMAND`, `ARGS`, `TIMEOUT_S`), `PLAYWRIGHT_RUNTIME_MCP_AUTO_HTTP_FALLBACK`, `PLAYWRIGHT_RUNTIME_MCP_TRANSPORT`, `PLAYWRIGHT_RUNTIME_MCP_LOG_LEVEL`, `PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP`, `PLAYWRIGHT_RUNTIME_LOG_DIR`, `BROWSER_RUNTIME_LOG_DIR` | `browser_tools.py`, `playwright_runtime_mcp_server.py` | the browser runtime served as an MCP server | various | core (G2) | `runtime_server` (`RuntimeServerSettings`) |
| whole environment copy; `ALL_PROXY`; `PATH`, `SystemRoot`, `TEMP`, `TMP`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `APPDATA`, `HOME`, `TMPDIR`; every `BROWSER_USE_*` | `browser_tools.py`, `drivers/browser_use/transport.py` | environment handed to child processes | — | pure functions of a passed mapping (G3) | — |
| `LOCALAPPDATA`, `ProgramFiles`, `ProgramFiles(x86)`, `ProgramW6432` | `provisioning/managed_browser.py` | where Chrome is installed; default Chrome user-data dir | — | browser (G4) | `browser_binary_candidates`, `local_app_data` |
| `AGENT_QUERY`, `AGENT_SESSION_ID` | `playwright_runtime/main.py` | demo entry point input | `""`, `demo-browser-session` | core reader (G5) | `read_demo_inputs()` |

"Read in" is the state before the environment boundary step; owners of the former question rows were decided in
the wave 1 review (G2–G5). Counts at the settings step: 37 core names, 33 browser names plus the new
`BROWSER_PROVISIONING`, 1 exempt read, 45 names in questions
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
   unchanged. (Settled by "Environment boundary".)
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

## Provisioning

`provisioning/__init__.py` defines the `Provisioner` protocol and registers `managed`, `remote` and `extension`;
`ManagedBrowserProvisioner` implements all three (S_20 "Provisioning"). `BrowserService` no longer builds the
managed profile, resolves the port or starts Chrome: it takes a provisioner from the registry. The MCP path keeps
the same Chrome, flags, ports and profiles.

### Decisions

1. **One class, three registered names.** `ManagedBrowserProvisioner` already exists and keeps the Chrome-handle
   API that the process registry, the heartbeat and reset use (`stop`, `is_endpoint_ready`, `clear`,
   `owns_process`); the deprecated `ManagedBrowserDriver` alias keeps pointing at a class whose old constructor
   still works. Remote and extension are a few lines each.
2. **Synchronous protocol.** The Chrome start is synchronous; the service calls `provision` in a worker thread, as
   it called `start` before.
3. **The provisioner instance is the Chrome handle**, one instance per device. The service keeps it as its managed
   driver, so the process registry still holds the same kind of object. `release(device)` runs the cleanup and is
   what the composition root will call.
4. **Adopting a live Chrome and the healthy-driver check stay in the service**, before provisioning, in the old
   order. The order is observable: an unkeyed service adopts the store's selected live Chrome even while its own
   Chrome is healthy, and stops its own. `provision(settings)` cannot see the service's current driver; moving both
   steps would need the current device as an extra argument (not the protocol) or per-service state in the
   provisioner.
5. **The service passes its profile store** (`create(name, profile_store=...)`). The store caches the file; two
   store objects in one service would overwrite each other's writes. Without a store the provisioner opens
   `profile_store_path` or `mcp_cwd/.browser/profiles.json`, which is what the composition root needs.
6. **Instance overrides are mapped onto `BrowserSettings` in the service** (`dataclasses.replace`). A keyed
   instance without a port gets `managed_port=0`, which the provisioner turns into a free port. The environment
   parser refuses 0, so only this mapping produces it; no `key` field is needed.
7. **Device shapes.** `kind="chrome"` for all three, as in the plan. Extension: no endpoint, `extension=True` and
   the token in `extras`. Remote: the CDP url plus headers and timeout. All three carry `launch_args` (Playwright MCP
   arguments); Chrome's arguments are `managed_args`, following the settings naming.
8. **The service receives `BrowserSettings`** through the new keyword `browser_settings`; when it is absent the
   service builds them once with `from_process_env()` at construction. `mcp_cwd` also comes from them instead of the
   `resolve_playwright_mcp_cwd` facade. The type is imported from `playwright_runtime/config.py`, which already
   imports it (allowlisted), so the core gains no import of the surface.
9. **Chrome install paths from the environment move to settings** (review decision G4):
   `BrowserSettings.browser_binary_candidates` holds the `chrome.exe` paths under `LOCALAPPDATA`, `ProgramFiles`,
   `ProgramFiles(x86)` and `ProgramW6432`, computed in `from_env` (Windows only). The home-based path and the fixed
   macOS / Linux paths stay in the provisioner: `Path.home()` at parse time fails when the home variables are absent
   (Settings decision 9). The lookup order is unchanged.

### Rejected options

- **Three small classes.** More code, and `ManagedBrowserProvisioner` would still be needed as the Chrome handle.
- **Adoption and health check inside `provision`.** Changes the order above, or needs the current device.
- **`provision(settings, current_device)`.** Not the protocol of the plan.
- **A `key` field in `BrowserSettings`.** A setting with no variable behind it; port 0 covers the one keyed rule the
  provisioner needs. The other keyed rule (adopt only the own profile) stays with adoption in the service.
- **Provisioning `remote` / `extension` in the service.** Nothing to start; writing the endpoint again would differ
  from the MCP config builder (device check, browser default).
- **Renaming the `env_map` reads in the service** so the env-read scanner stops seeing them. They touch the MCP
  child's env mapping, not the process environment; hiding them from the scanner proves nothing.

### Verification

- `test_provisioning.py` (+17): registered names; remote and extension devices without a Chrome start; managed
  device, profile reuse and rebuild, free port, kill-existing user-data dir, store fallbacks, release, install paths
  from the settings. `surfaces/test_browser_settings.py` (+3, one skipped per OS): `browser_binary_candidates`.
  `test_service.py` (+8): registry call with the service store, instance overrides, port precedence, no
  provisioning for remote / extension, given settings winning over the environment, environment read once.
- Tests changed because the code they patched or called moved (no assertion weakened): `test_service.py` (three
  tests patch `PROVISIONERS.create` instead of `service.ManagedBrowserProvisioner`; two profile-building tests call
  `_build_profile(service._provisioning_settings())`; the stored-binary test runs the real provisioner with `start`
  patched and builds the service under the cleared environment); `test_browser_instance_isolation.py` (six tests
  patch `managed_browser._allocate_free_port` and call `_build_profile(...)` instead of
  `BrowserService._build_managed_profile`); `drivers/test_conformance_browser_use_live.py` (passes the settings'
  install paths to `_candidate_chrome_binaries`).
- Architecture allowlists: core imports of concrete modules 2 entries -> 1 (`service.py ->
  provisioning.managed_browser` removed); env reads in `service.py` 13 descriptors / 22 reads -> 3 / 3, in
  `provisioning/managed_browser.py` 4 / 5 -> 1 / 1; `service.py` browser words 93 -> 92, driver names 16 -> 14,
  lines 1977 -> 1904.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1360 passed, 60 skipped** (before: 1333 / 59; MCP
  goldens and runner contract unchanged). Harness tests that build the browser subagent: **262 passed**.

### Known leftovers

1. `provisioning/managed_browser.py` still reads `LOCALAPPDATA` for the default Chrome user-data dir (kill-existing
   without a user-data dir); G4 covered the install paths only. (Settled by "Environment boundary":
   `BrowserSettings.local_app_data`.)
2. The three `service.py` entries left in the env-read allowlist touch the Playwright MCP child's env mapping. They
   go when the MCP driver owns its config.
3. The service stops Chrome through the handle API, not `release(device)`: the process registry shares handles
   among services and calls `stop` itself.
4. `service.py` imports `BrowserSettings` through `playwright_runtime/config.py`; when that module stops importing
   the surface (review decision G1), the service needs another way to the type. (Settled by "Composition root",
   decision 4.)
5. Visible differences, all from reading settings once: the browser variables are read at service construction,
   not at Chrome start; a legacy `BROWSER_DRIVER` also warns when the service is built; `BROWSER_DRIVER` set to a
   driver name no longer raises in the service; an invalid `BROWSER_MANAGED_PORT` raises at service construction in
   every mode; the profile-store writes around a start run in the worker thread.
6. `PLAYWRIGHT_MCP_EXTENSION=true` with `remote` or `managed` puts Playwright MCP into extension mode (config
   builder), while the provisioner is chosen by `provisioning` alone and hands out a remote or managed device.
   (Without an explicit provisioning, settled by "Composition root", decision 5.)
7. An invalid instance `driver_mode` still raises "BROWSER_DRIVER must be one of: remote, managed, extension".
   (Settled by "Composition root", decision 6.)
8. Settings leftover 3.3 (Chrome install discovery) is settled by decision 9; Settings leftover 1 no longer applies
   to `service.py`.

## Environment boundary

Only `playwright_runtime/config.py` and `surfaces/browser/config.py` read the process environment (plan invariant
9); every other module receives settings or values from them (S_20 "Environment boundary"). Wave 1 review decisions
G2, G3, G5, G7 are implemented here, together with the two middleware follow-ups G10 and G11.

### Decisions

1. **Settings come from the caller; a missing one is built through the config module.** Entry points (`main.py`,
   the runtime MCP server) build the settings once and pass them down; `BrowserAgentRuntime` and `BrowserService`
   take them as keywords. Public functions that are called without settings today (tests, the harness subagent
   factory, `browser_agent.py`) fall back to `X.from_process_env()` at the moment they read the environment before,
   so the read timing does not move. `browser_agent.py` is not changed in this step.
2. **`utils/env.py` is pure (G7).** It keeps the defaults and one set of parse helpers used by both config
   modules; the duplicated `_first` / `_int` / argument split / truthy sets / provider aliases in the config modules
   are gone. Helpers that read the environment and have no caller left are deleted (`first_non_empty_env`,
   `resolve_int_env`, `resolve_bool_env`, `resolve_model_name`, `resolve_browser_timeout_s`, `resolve_upload_root`,
   `resolve_model_settings`); `load_repo_dotenv` and `resolve_repo_dotenv_path` move to
   `playwright_runtime/config.py`, which already re-exported the former.
3. **`RuntimeServerSettings` nested in `CoreSettings` (G2)**, with two parsers keeping the client and server
   readers apart: the client takes the `BROWSER_RUNTIME_*` aliases, the server does not (except the log dir, which
   it always took). `port` stays a string and `args` a raw string, so an invalid port still fails where it is used,
   and the client's own argument splitting (`posix` by OS, whitespace fallback) is unchanged.
4. **Child-process environments are pure functions in the config modules (G3)**: `runtime_server_child_env(environ)`
   and `runtime_server_environ(environ)`; callers pass `process_environ()`. The old passthrough loop of
   `_build_child_env` copied each listed variable onto a copy of the same environment and is dropped as a no-op. The
   sidecar environment stays in `transport.py`: it copies OS and `BROWSER_USE_*` names from the given mapping and
   takes the upload root from `BrowserSettings.upload_root` (resolved; the sidecar resolves it again).
5. **The runtime MCP server stops writing `os.environ`.** Its tool timeout defaults (`BROWSER_TIMEOUT_S` and
   `PLAYWRIGHT_TOOL_TIMEOUT_S` = 600) are applied to the mapping its settings are parsed from, and the settings go
   to the runtime, the service and the worker; the worker tool timeout under the server stays 600 s. Two
   module-level slots hold the settings `main()` built, next to the server's existing `_runtime` slot; without
   `main()` the runtime reads the process environment as before.
6. **Demo inputs through `read_demo_inputs(environ)` (G5).**
7. **`browser_logging` stays an allowlisted exception.** Configuring the logger from `CoreSettings` at a composition
   point needs process-global state (the plan forbids module-level state), and code that never passes a composition
   point (core `react_agent`, the live driver, tests) would lose `./logs/browser_agent.log`. It moves with the
   logging hook into `openjiuwen.core` (plan Phase 4); done in "Tool-call logging hook".
8. **`BrowserSettings.local_app_data`** carries `LOCALAPPDATA` for the default Chrome user-data dir, next to the
   install paths of G4.
9. **G10**: `RunScript.args` joins `__sensitive_fields__`; `Tracing` leaves `message` out of the error record of a
   sensitive command.
10. **G11**: `SidecarTransport.request` sends `cancel` for its id when it is cancelled, then re-raises.

### Rejected options

- **Configuring `browser_logging` from settings now** (decision 7).
- **Child-environment builders left in `browser_tools.py` / `transport.py` as allowlisted "mapping, not process
  env" entries.** The names they spell are env names; the config modules are where env names are spelled.
- **Leaf defaults read from settings inside `controllers/` or `utils/`.** Both are core; they would import the
  browser surface settings. They take the value; `None` means not configured.
- **A sentinel to tell "not passed" from "not configured" for the upload root.** Every caller in the package
  passes the value.

### Verification

- `test_settings_passing.py` (+34): `RuntimeServerSettings` client and server parsing (alias order, defaults,
  timeout, fallback flag, tri-state stateless flag, nesting); pure environment mappings; `main()` building the
  settings once and passing them to the runtime; `build_runtime_settings`, the runtime, the runtime MCP config, the
  worker agent, the status flag, the selector cache, upload paths, the sidecar environment and the browser-use
  factory using the given values over a conflicting process environment.
- `drivers/test_middleware.py` (+4, G10), `test_browser_use_transport.py` (+1, G11).
- Existing tests changed because a patched symbol or a signature moved (no assertion weakened):
  `test_file_uploads.py` (passes `upload_root` instead of patching `resolve_upload_root`),
  `test_browser_use_upload_validation.py` (same), `test_config.py` (the runtime log test passes
  `RuntimeServerSettings` and an empty environ instead of patching `_build_child_env`),
  `test_browser_probe_cards.py` (the patched `get_selector_cache` takes the path), `test_browser_runtime_rail.py`
  (the bare runtime gets `_core_settings`), `drivers/test_conformance_browser_use_live.py` (passes
  `BrowserSettings.from_process_env()` to the factory, which no longer reads `BROWSER_USE_SIDECAR_PYTHON` itself).
- Architecture allowlists: env reads 14 files / 82 descriptors / 92 reads -> 3 / 9 / 9 (sidecar, `browser_logging`,
  the `service.py` env mapping); `KEY_TUPLE_ENV_NAMES` removed (every name is now spelled in a config module);
  browser words `config.py` 8 -> 9 and `main.py` 4 -> 3 (the demo session id moved); module size `runtime.py`
  6383 -> 6396, `service.py` 1904 -> 1907 (settings keywords).
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1399 passed, 60 skipped** (before: 1360 / 60; MCP
  goldens, tool/prompt snapshots and the runner contract unchanged). Harness tests that build the browser subagent:
  **248 passed**.
- Fix after review: on Windows a `dict` snapshot of `os.environ` keeps only upper-case names, so the sidecar lost
  `SystemRoot` (it exited before its hello; every live browser-use case failed) and the Chrome install roots lost
  `ProgramFiles*`. `utils.env.env_value` looks a name up as given and upper-cased; the env-read rule now counts only
  `utils/env.py` helpers that read the process environment. Live browser-use conformance: **45 passed**.

### Known leftovers

1. `browser_logging` reads the environment (decision 7). Resolved in "Tool-call logging hook".
2. The three `env_map` entries of `service.py` (Provisioning leftover 2).
3. `controllers/action.py` `list_upload_files`: ruff now reports ASYNC240 on its existing `upload_root.exists()`,
   because the root became a typed parameter; behaviour unchanged.
4. Invalid input fails earlier in a few places: `ensure_execute_signature_compat`, `build_browser_worker_agent` and
   `is_browser_subagent_status_log_enabled` without settings build `CoreSettings`, so an unknown `BROWSER_DRIVER`
   raises there too.
5. Behaviour for callers outside the package: `register_builtin_actions()`, `resolve_upload_file_paths()` and a
   `BrowserSelectorCache()` without a path no longer read `BROWSER_UPLOAD_ROOT` / `OPENJIUWEN_BROWSER_SELECTOR_CACHE`;
   `create_browser_use_driver(device, None)` no longer honours `BROWSER_USE_SIDECAR_PYTHON`. `utils` no longer
   exports the deleted helpers.
6. `python -m openjiuwen.harness.tools.browser_move.playwright_runtime_mcp_server` fails before this step's code at
   the clients import, now `from drivers.playwright_mcp.clients.stdio_client import ...` (relative import beyond the
   top-level package); not changed here.

## Composition root

`playwright_runtime/assembly.py` builds the driver stack from registry names: surface, provisioner, device,
driver, middleware, start (S_20 "Composition root"). The built-in drivers, surfaces and provisioners are
registered with lazy strings. The core no longer imports the browser surface. In this step the root ends at a
started driver; `create_browser_agent` and the Playwright MCP path keep their wiring.

### Decisions

1. **The surface factory returns `SurfaceBundle(settings, profile=None)`.** The root needs the surface settings now
   and the profile later. A bundle lets the browser factory add its profile without changing the factory's return
   type or the root's `surface.settings` access. `SurfaceProfile` already has a `settings` field, but it cannot be
   built yet (it needs commands and a renderer), and a profile with placeholder fields would be a stub.
2. **The registry packages re-export their registry.** `from ..drivers import DRIVERS` imports the package that
   registers the built-ins and gives back the filled registry; the root needs no import only for its side effect.
   `drivers/__init__.py` re-exports `DRIVERS`, `surfaces/__init__.py` re-exports `SURFACES` and
   `provisioning/__init__.py` re-exports `PROVISIONERS`.
3. **"Not built yet" lives in `Registry`.** `playwright_mcp` is registered with its final string. Resolving a string
   whose own module is missing raises `ModuleNotFoundError` with that message; a missing dependency inside an
   existing module is raised unchanged. A raising placeholder factory would need an edit when the driver lands, and
   the same message also helps a third-party registration that has a typo in it.
4. **G1 / H5: the core types surface settings as `SurfaceSettings`.** This protocol has one member,
   `provisioning`, and lives in `contract/surface.py`. `config.py` gets the browser settings with
   `browser_surface_settings(environ=None)`, which is `SURFACES.create("browser", ...)`. `service.py`, `runtime.py`,
   `main.py` and the MCP server use that function or take the settings from their caller. The private MCP config
   builder `_playwright_mcp_config` takes `Any`: it reads a dozen browser fields, the protocol does not declare them,
   and it moves into the Playwright MCP driver.
5. **H4: `PLAYWRIGHT_MCP_EXTENSION=true` without a provisioning selects `extension`**, in
   `BrowserSettings.from_env`. An explicit `BROWSER_PROVISIONING` wins, and so does a legacy `BROWSER_DRIVER`
   provisioning value, because it is an explicit choice in the deprecated spelling. One visible effect: the
   browser service identity now says `extension` instead of `remote` for that environment.
6. **H6:** the instance validation error names `BrowserInstanceConfig.provisioning (driver_mode)` and
   `BROWSER_PROVISIONING`.
7. **Per-effect timeouts are `core.tool_timeout_s` for every effect.** That is today's per-tool-call bound
   (`PLAYWRIGHT_TOOL_TIMEOUT_S`, default 240 s), so it is longer than the longest `Wait` (G8). The surface profile
   supplies per-effect values once it exists.
8. **Provisioning runs in a worker thread.** The provisioner protocol is synchronous (Provisioning, decision 2), so
   the root calls `provision` and `release` through `asyncio.to_thread`, as the service does.
9. **The trace sink writes to the debug log**, one record per call, already masked by `Tracing`. The root gets
   no sink parameter: no caller needs a different sink yet, and the tests read the stack and the log.
10. **Failure handling.** A failure after provisioning releases the device. A failed `start` also stops the
    stack, because a driver may hold part of its connection. `close()` releases the device even when `stop`
    raises.
11. **`Registries` is a frozen dataclass passed in by keyword.** `None` means the three default registries.
    There is no module-level default object and no other state.

### Rejected options

- **The surface factory returns `SurfaceProfile` with only `settings` filled.** The required fields would need
  placeholders.
- **The surface factory returns the bare settings now and the profile later.** That changes the return type in
  Phase 4, and every caller of the factory would change with it.
- **A placeholder `playwright_mcp` factory that raises.** It is stub code, and it must be edited when the driver
  lands.
- **Side-effect imports (`from .. import drivers  # noqa`) in the root.** Decision 2 makes the dependency explicit.
- **`await provisioner.provision(...)`.** The protocol is synchronous (Provisioning, decision 2).
- **A `trace_sink` parameter on `assemble_driver`.** Speculative; nothing passes one.
- **Typing `_playwright_mcp_config` with `SurfaceSettings`.** It reads attributes the protocol does not declare.

### Verification

- `test_assembly.py` (+20): call order with fake registries (surface with the given environ, provisioner by the
  settings' provisioning, provision, driver with device and settings, start); middleware order; every `Effect`
  bounded by `tool_timeout_s` (`ActionTimeout`, dispatched); refusal of a driver that does not serve the surface,
  with the device released and the driver not started; the device released when the driver factory, `start` or
  `stop` fails; no driver when `provision` fails; trace records in the debug log. Also the built-in names, the
  browser surface returning its settings and no profile, the default registries reaching `playwright_mcp` ("not
  built yet"), and a subprocess check that importing the package and then the root loads no `browser_use`, browser-use
  or Playwright MCP driver, browser surface or managed-browser module.
- `contract/test_registry.py` (+2): "not built yet", and a missing dependency raised unchanged.
  `surfaces/test_browser_settings.py` (+4, H4). `test_settings_facades.py` (+1, G1: the facades take the settings
  from the surface registry, once per call, with the process environment). `test_service.py` (+1, H6).
- `test_assembly_live.py` (+1, `live`): managed Chrome from the provisioner (headless, free port, temporary
  profile and store) and the browser-use driver started through `assemble_driver`; `observe()` on `about:blank`;
  after `close()` the CDP endpoint is gone and no Chrome process uses the profile. A probe run saw 11 Chrome
  processes before `close()` and 0 after it.
- Tests changed for moved symbols (no assertion weakened): `test_service.py` imports `BrowserSettings` from
  `surfaces/browser/config.py`.
- Architecture allowlists: core imports of concrete modules 1 entry -> 0 (`config.py -> surfaces.browser.config`
  removed); module size `service.py` 1907 -> 1910 (H6 message); browser words, driver names and env reads
  unchanged; the root itself has no browser word, driver name or env read.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1429 passed, 61 skipped** (before: 1401 / 60; the new
  skip is the live test without the sidecar interpreter; MCP goldens, tool/prompt snapshots and the runner
  contract unchanged). Harness tests that build the browser subagent: **262 passed**. Live, with
  `BROWSER_USE_SIDECAR_PYTHON` set: browser-use conformance **45 passed**, `test_assembly_live.py` **1 passed**.

### Known leftovers

1. `create_browser_agent`, the browser service and the runtime MCP server do not build through the root yet. The
   service still provisions managed Chrome itself and adopts a live Chrome from the profile store (H1); the root
   does not adopt.
2. Per-effect timeouts come from `CoreSettings.tool_timeout_s` until the surface profile supplies them. Done: see
   "Agent construction, instance settings and restart on a dead device".
3. `mypy` on `config.py`, `service.py` and `runtime.py` reports 13 more errors than before (26 instead of 13). They
   are `attr-defined` (and one `type-var` on `dataclasses.replace`) where browser code in the core reads browser
   fields on a `SurfaceSettings`. `make type-check` is advisory. The errors go away when that code moves to the
   surface and the MCP driver, or earlier if the stored settings are typed `Any`.
4. A legacy `BROWSER_DRIVER=remote|managed` wins over `PLAYWRIGHT_MCP_EXTENSION=true` (decision 5), so in that
   environment the device and the MCP config still disagree, as before.

## Playwright MCP result decoding

`drivers/playwright_mcp/snapshot.py` parses `@playwright/mcp` text (sections, page header, tab lists, ARIA YAML)
and `drivers/playwright_mcp/result.py` decodes one tool result into an `ActionResult` or a typed error (S_20
"Playwright MCP"). They are pure functions; the driver class that calls them comes next. The recorded fixtures
grew from 14 to 33 cases so that every decoding rule has a real server answer behind it.

### Decisions

1. **`decode` takes `before`, the `BrowserContext` before the call.** The server marks neither a navigation by
   click nor a popup: the result of a link click differs from a plain click only in the URL under `### Page`, and a
   new tab only shows up as a longer `### Open tabs` list. A pure decoder cannot see a change without the previous
   state, so the caller passes it; without it, only navigate, back, tab commands and dialogs set
   `context_changed`. The task named the signature `decode(tool_name, raw_result, *, output_dir)`; `before` is a
   keyword-only addition with a `None` default.
2. **New tabs are the tabs past `len(before.tabs)`.** The server numbers tabs by position and appends new ones;
   `browser_tabs new` and a `target="_blank"` click both add the last index. A tab list is shown only with more
   than one tab, so a result with `### Page` and no list means one tab with id `"0"`, and `tabs` always contains
   the focused tab.
3. **Every `browser_tabs` result is a context change**, `list` included. The driver uses `browser_tabs` only for
   open, switch and close (observing reads the tabs from the snapshot), so telling `list` apart would need the
   arguments for no gain.
4. **I4: the linked snapshot file is read under `output_dir`**, the server's working directory, because the links
   are relative to it. The resolved path must stay inside it; a missing, unreadable or empty file gives
   `observation=None`. The server writes the file empty while a dialog blocks the snapshot, so an empty file is
   not an empty page.
5. **I5 on the recorded call logs.** `performing click action` / `click action done` → `ActionTimeout(dispatched=True)`
   (recorded: a click on a link whose server never answers times out waiting for the navigation). A log of only
   actionability waits (`waiting for locator(…)`, `locator resolved to`, `attempting/retrying … action`,
   `waiting for element to be …`, `element is …`, scrolling, `waiting Nms`, `waiting for getBy…(…) to be visible`)
   → `dispatched=False`; any other line → `True`. "Performed" is checked before "intercepted": a click retried past
   an overlay and then performed was dispatched.
6. **`ConnectionLost` on two wordings.** After the browser died during a call, the next call fails with
   `Protocol error (Target.createTarget): Failed to open a new tab` and the one after with
   `Target page, context or browser has been closed`. Both mean the browser is gone, and a restart is the recovery.
7. **A call refused by an open dialog returns `ok=False`** (`Tool "…" does not handle the modal state`, with the
   `### Modal state` in `detail`). The server sends nothing in that case; browser-use returns `ok=False` for an
   action while a dialog is open, and the two drivers should agree. Not a listed error rule.
8. **`detail` drops `### Result`, `### Snapshot` and `### Events`.** The result is in `value` (or `surface` for a
   tab list), the snapshot in `observation`; events are not reported (the loop guard used to strip them). The
   remaining text, `### Ran Playwright code`, `### Page`, `### Open tabs` and `### Modal state` included, is what the
   model sees today.
9. **A file chooser is not a context change.** It is a modal state but not a dialog: refs stay valid and
   `HandleDialog` does not answer it. It stays visible in `detail`.
10. **`ActionIntercepted.by` uses the browser-use form** `tag#id.class "text"`, built from the element preview in
    the call log, so a rail reads one format from both drivers.
11. **Element values only for form controls.** `value` is filled for textbox, searchbox, combobox, spinbutton and
    slider, from the inline text, a child `text:` line (how a filled textbox appears) or the `[selected]` option.
    A paragraph's text goes to `extra["text"]`, because `value` means a field value on browser-use.

### Rejected options

- **The driver compares the URL and the tabs itself.** It would put part of the decoding rules in the driver;
  `before` keeps them in one place and testable on fixtures.
- **Passing the `browser_tabs` arguments to tell `list` from `new`/`select`/`close`.** Not needed (decision 3).
- **Reading `### Snapshot` file links with any path.** The text comes from a subprocess; the read is confined to
  `output_dir`.
- **`DriverError(dispatched=True)` for the modal-state refusal.** Correct by the catch-all rule but it would make
  the two drivers disagree on the same situation.
- **Treating every timeout as dispatched.** The recorded logs separate an action that never started from one that
  ran, and I5 asks for that split.

### Verification

- New fixtures, recorded with the extended `record_mcp_fixtures.py` (three new pages, `popup.html`,
  `upload.html`, `slow.html`, and a `/hang` route that answers after 30 s): `click_stale_ref`,
  `snapshot_with_boxes`, `fill_form`, `snapshot_after_fill_form`, `select_option`, `evaluate_result`,
  `evaluate_error`, `screenshot_element`, `wait_for_timeout`, `click_blocked_by_dialog`, `click_opens_tab`,
  `snapshot_two_tabs`, `click_navigates`, `navigate_back`, `file_chooser_open_by_click`, `file_upload`,
  `click_timeout_after_action`, `evaluate_new_tab_failed`, `evaluate_browser_closed`. The new cases run after the
  old ones, which re-record byte-identical; two full re-recordings produced identical files, the screenshot PNG
  included.
- `drivers/playwright_mcp/test_snapshot.py` (13) and `test_result.py` (58): every fixture through the parser or
  the decoder with its outcome (ok, `context_changed`, or the error class and `dispatched`); the form page refs
  `e1`–`e18` plus `f1e1`–`f1e3`; boxes, filled values, checked state, YAML quoting, tab lists; `### Events` and file
  links gone from `detail`; same-URL reload, link click and popup as context changes with `opened_contexts`; a
  linked snapshot read from a temporary `output_dir`, `None` when missing, empty or outside it; the blocker
  description; screenshot value and size.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1538 passed, 61 skipped** (before: 1429 / 61; +71
  parser and decoder tests, +38 fixture well-formedness cases).

### Known leftovers

1. The driver class, its handlers and the factory are not built; nothing calls the decoder yet.
2. Tabs are identified by position. A command that closes one tab and opens another in the same call keeps the
   count and is not seen as a change; a tab closed before the focused one shifts the ids.
3. The decoder reads the linked snapshot file synchronously; the driver should call it off the event loop.
4. No `ConnectionLost` wording for a dead MCP server process: that is a transport error, not text, and belongs to
   the driver. A browser closed by a tool call between calls is not an error at all: the server starts a new
   browser on the next call (a stale ref is what the model then sees).
5. `last_status` and the scroll pixels of `BrowserContext` stay at their defaults: the server reports neither.

## Playwright MCP driver

`drivers/playwright_mcp/driver.py`: `PlaywrightMcpDriver(HandlerDriver)` over a `@playwright/mcp` server it starts
and owns, plus the registry factory `create_playwright_mcp_driver`. Nothing outside tests uses it yet; the agent
still runs the old Playwright MCP path.

### Decisions

1. **The factory reuses today's server config builder.** `_playwright_mcp_config` became the public
   `playwright_mcp_config(settings, instance, required_capability)`; the factory passes the device endpoint as the
   instance CDP URL. One builder, so the driver launches the same server the runtime does.
2. **The clients return the raw `CallToolResult`.** `call_tool_result` is the transport part of the old
   `call_tool`; `call_tool` keeps its text extraction on top, so the runtime path is unchanged. The decoder needs the
   content blocks and `isError`, not the joined text. The driver itself calls the single-attempt `call_tool_once`
   (see "Playwright MCP live conformance").
3. **Tool hints come from the server.** `start()` reads `tools/list` once and keeps the description of each mapped
   tool; whether they or our own texts are better is a benchmark question.
4. **`native_text` of `observe()` is the whole `browser_snapshot` text**, so a renderer can reproduce today's model
   input exactly; an observation read from a linked file carries the file's YAML.
5. **The driver keeps the last `BrowserContext`** from observe and every result and passes it to `decode`; that is how
   a navigating click and a popup become context changes.
6. **Coordinates are screenshot pixels at scale 1** (screenshots are taken at CSS scale), so `PointRef` goes to the
   coordinate tools unchanged.
7. **`count=2` is `doubleClick`; more is `Unsupported`.** `TypeText` without `clear` must be `slowly` (the tool
   replaces the content otherwise).

### Rejected options

- **A second copy of the MCP command-line logic in the driver.** Two builders would drift; decision 1.
- **Going through `ToolMgr` and the client patch.** The driver owns its client; the patch dies with the old path.

### Verification

- `drivers/playwright_mcp/test_driver.py` (81) on a fake MCP session: pinned `info.actions`; one case per handler
  with the exact tool and arguments; `Unsupported` cases send nothing; observe with and without screenshots;
  start/stop/health; context tracking across calls; the factory's config equals `build_playwright_mcp_config` for
  remote, managed and extension devices.
- `test_assembly.py`: the `playwright_mcp` registry name now builds a `PlaywrightMcpDriver`.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1619 passed, 61 skipped**; live browser-use
  conformance and assembly: 46 passed.

### Known leftovers

1. Fixed with the live conformance run: the re-sent tool call after a timeout or transport error, and every client
   exception read as `ConnectionLost`. Live conformance now runs (next section).

## Playwright MCP live conformance

The conformance suite runs live on `PlaywrightMcpDriver`
(`drivers/playwright_mcp/test_conformance_playwright_mcp_live.py`): the registry factory over a headless Chrome with
a fresh profile, `@playwright/mcp@0.0.78` attached over CDP, `Tracing` around the driver, the browser scene. Every
failure of the first run was a driver or decoder bug or the test's server setup; the suite and the scene are
unchanged.

### Decisions

1. **One attempt per tool call.** The clients' `call_tool_result` reconnects and sends the call again after a
   timeout or a transport error, so a click could run twice (invariant 7). The clients got `call_tool_once` (no
   reconnect, no retry) and a `connected` property; the driver uses only those. Transport timeout →
   `ActionTimeout(dispatched=True)`; no connection → `ConnectionLost(dispatched=False)`, nothing sent; any other
   failure → `ConnectionLost(dispatched=True)`. The retrying `call_tool` stays for the runtime path.
2. **`observe()` asks for boxes and strips them from the text.** Without `boxes` the snapshot has no bounds, so no
   `PointRef` can be built from an observation, although the driver offers the coordinate tools. With `boxes` and
   ` [box=…]` removed, the text is byte for byte today's `browser_snapshot` text (checked live on both scene pages),
   so the model input does not change.
3. **A `browser_wait_for` timeout is dispatched.** Its call log (`waiting for getByText(…) to be visible`) reads
   like an actionability wait, which made it `dispatched=False`; but the wait is the command itself.
4. **Sensitive commands drop the echoed code from `detail`.** The server echoes what it ran
   (`### Ran Playwright code`, e.g. `fill('<typed text>')`), and `Tracing` records `detail`. `decode(...,
   sensitive=True)` leaves the section out for `TypeText` and `RunScript` with `sensitive=True` and for a `FillForm`
   with a sensitive field. Other commands keep it: it is part of what the model reads today.
5. **The live test starts the server with `--allow-unrestricted-file-access`.** The server reads upload and drop
   files only under its working directory and its output directory; the scene keeps its upload file in its own
   temporary directory. The driver's production command line is unchanged (Known leftovers 1).

### Rejected options

- **Masking the typed text inside `detail`.** The server quotes it as a JavaScript string literal, so escaped forms
  would slip through; dropping the code section is exact.
- **Dropping `PointRef` from the driver's handled refs.** Today's MCP agent clicks by coordinates in vision mode.
- **Adding `--allow-unrestricted-file-access` to the driver's command line.** It also opens `file://` navigation; a
  security choice, not a driver fix.
- **A server working directory that contains the scene's files** (the system temp directory). The server would write
  its output files into a shared directory the test cannot clean up.

### Verification

- `drivers/playwright_mcp/test_conformance_playwright_mcp_live.py`: first run 9 failed, 42 passed (the three
  `PointRef` happy paths: no bounds; `Drop` ×2 and `UploadFile`: file outside the server roots; the timeout case:
  `dispatched=False`; sensitive text in the trace ×2). After the fixes: **51 passed** in two runs; no
  `@playwright/mcp` node process left after either run.
- `test_assembly_live.py`: `BROWSER_DRIVER=playwright_mcp` over a managed Chrome: `assemble_driver` starts the
  server, `observe()` works, `close()` leaves no process the test started (`npx`, the server, Chrome) and frees the
  CDP port. Both assembly cases: 2 passed.
- New unit tests, each failing on the code before its fix: a call that times out on a fake session is sent once and
  raises `ActionTimeout(dispatched=True)`; a closed transport is not re-sent; no connection sends nothing; observe
  sends `boxes` and returns bounds with box-free text; `without_boxes`; `wait_for_timeout` is dispatched; a
  sensitive call's `detail` has no code (decoder; `TypeText`, `FillForm`, `RunScript` through the driver).
- Browser-use live conformance: **45 passed** in two runs. `pytest tests/unit_tests/harness/tools/browser_move -q`:
  **1681 passed, 61 skipped** (the MCP live module and the MCP assembly case included; goldens unchanged).

### Known leftovers (differences between the drivers for Phase 4)

1. **Upload and drop files must be under the MCP server's working directory** (`mcp_cwd`) or its output directory;
   browser-use reads any path (only under `upload_root` when that is set) and answers a refused path with
   `ok=False`. The MCP refusal is `DriverError(dispatched=False)` ("Open dialogs and refused file access"). Phase 4
   decides whether the runtime stages files into the workspace, or the server gets roots or the flag.
2. **`Wait(until_text, seconds)` ignores `seconds` on MCP**: `browser_wait_for` takes no timeout for a text, the
   server's own applies (5 s on 0.0.78). browser-use waits up to `seconds`.
3. **Context ids are tab positions on MCP** and shift when an earlier tab closes; browser-use ids are target ids.
4. **Bounds only from `observe()`**: the observation that comes free with an MCP action result has none, so a
   `PointRef` is built from an `observe()`.
5. **`detail` differs**: MCP carries `### Ran Playwright code`, `### Page` and `### Modal state` there; browser-use
   none of them. A sensitive MCP call's `detail` has no code section.
6. **An open dialog blocks every other MCP tool** (`ok=False`, the modal state in `detail`).
7. **An MCP transport timeout comes after `mcp_timeout_s`** (600 s by default); the `Timeouts` middleware
   (`tool_timeout_s`) normally ends the call first, also as `ActionTimeout(dispatched=True)`.
8. **`observe()` with boxes costs one box per element** on large pages; measured in the benchmark phase.
9. **The live MCP test runs wherever `npx` and Chrome exist** (gated on them, not on an environment variable) and
   needs network the first time `npx` fetches the server.

### Live test gating

The live Playwright MCP cases (conformance and assembly) run only with `BROWSER_MOVE_LIVE_MCP=1`, as the browser-use
live cases need `BROWSER_USE_SIDECAR_PYTHON`. Without the gate they ran in every unit run on a host with `npx` and
Chrome (about 2.5 minutes, network access for `npx`). `Wait(until_text, seconds)` on this driver uses the server's own
text-wait deadline (5 s); S_20 allows a driver without a deadline for a text wait to use its own.

## Open dialogs and refused file access

`BrowserContext.dialog`, an `observe()` that works while a JavaScript dialog blocks the page (both drivers), and
the Playwright MCP file-access refusal counted as not dispatched. Decision E13 of the Phase 2 review and K2 of the
Phase 5 close.

### Decisions

1. **`dialog` is a string on `BrowserContext`, `"<type>: <message>"`.** It is browser data, so it lives in the
   surface context, not in `Observation` or `ActionResult` (S_20, rule 3). It is set in every observation and every
   action result taken while a dialog blocks the page, and `None` otherwise. A dialog armed by an earlier
   `HandleDialog` is answered at once and never shows.
2. **browser-use: the sidecar answers `observe` from cached target info.** browser-use keeps URL, title and tabs as
   cached target data, which needs no CDP call and works while the dialog holds the page; the DOM, the screenshot
   and the `document.title` script do not. The reply has `dialog` and no elements, and the driver then runs no
   script and takes no screenshot. Wire version 3: act replies also carry `url` and `dialog` (the dialog still open
   when the reply is built), so a click that opens a dialog and an action a dialog refused both get
   `surface = BrowserContext(url, dialog)`.
3. **Playwright MCP: the tab list stands in for the snapshot.** While a modal state is open the server refuses
   `browser_snapshot` like every page tool (`Tool "browser_snapshot" does not handle the modal state` plus
   `### Modal state`; no `### Page`, no snapshot file; recorded as `snapshot_during_dialog`). `browser_tabs` is not
   a page tool and still answers, with the URL, the tabs, an empty title (the server cannot read it) and
   `### Modal state` (`tabs_list_during_dialog`). So `observe()` sends one `browser_tabs list` after a refused
   snapshot; `native_text` is the refusal text, which is what the model reads today when it asks for a snapshot
   during a dialog. A file chooser takes the same path (empty elements, no `dialog`).
4. **The decoder reads the dialog from `### Modal state`**: `["<type>" dialog with message "<message>"]: can be
   handled by …`. The server does not escape the message, so it ends at the first `"]: can be handled by`. A call a
   dialog refuses stays `ok=False` and gets `surface` = `before` with the dialog: nothing was sent, so the page is
   the one before the call.
5. **The conformance `dialog` case checks an observation.** `check_context_change` observes right after the
   command when the case has a `check`, before the cleanup answers the dialog. The browser scene's check wants
   `"confirm: Proceed?"` in the click's result surface and in that observation; the suite stays surface-neutral.
6. **A refused file access is `DriverError(dispatched=False)`.** `@playwright/mcp` 0.0.78 checks every upload and
   drop path before it touches the page; the decoder matches `File access denied: … is outside allowed roots`
   right after the stale-ref rule. The fixture `drop_outside_roots` comes from the recorder's server, which runs
   without `--allow-unrestricted-file-access`: a drop of a file in a second temporary directory (`<OUTSIDE>`).

### Rejected options

- **MCP: the blocked observation from the last known context.** No extra call, but it misses a navigation that
  happened with the dialog (a dialog on load) and is not the server's own view; the tab list is a real read.
- **MCP: the linked snapshot file while a dialog is open.** `browser_snapshot` returns no link then; the empty
  linked file belongs to action results, which already give `observation=None`.
- **browser-use: keep `ObserveError` and let the runtime build the dialog view from the last act reply.** Every
  caller would need that special case; the driver knows the dialog.
- **A `dialog` field on `Observation` or `ActionResult`.** Browser-only data that a non-browser driver would fake.

### Verification

- Unit tests, each new one failing on the code before the change (15 failed on the code before):
  - sidecar (`test_browser_use_sidecar.py`): `observe` during a dialog returns URL, title, tabs, the dialog and no
    elements and sends nothing to the page; act replies carry `url` and the open `dialog` (opened, refused, handled);
    an armed dialog leaves `dialog` empty. The `observe` row of "page reads fail at once" is gone.
  - browser-use driver: the act result surface with an open dialog (opened, refused); no dialog, no surface;
    `observe` during a dialog sends only `observe`.
  - MCP decoder: `dialog` in the surface of `dialog_open_by_click` and `tabs_list_during_dialog`, `before` plus the
    dialog for a refused click, none for a file chooser, a message with quotes and a line break;
    `drop_outside_roots` is `DriverError(dispatched=False)`.
  - MCP driver: `observe` during a dialog sends `browser_snapshot` then `browser_tabs list`, no screenshot.
  - scene: the `dialog` check needs the dialog in both the result and the observation. Conformance self-test: a
    driver whose observation hides the dialog fails the case.
- Fixtures: three new cases, recorded live; the 33 earlier fixtures re-recorded byte-identical.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1653 passed, 113 skipped**.
- Live, with the new `dialog` check: browser-use **45 passed**; Playwright MCP **51 passed**.

### Known leftovers

1. **browser-use navigation replies carry no `dialog`.** A page that opens a dialog on load shows it in the next
   `observe()` or action result; the MCP navigation result has it (`### Modal state`).
2. **The MCP title is empty during a dialog** (the server cannot read it); browser-use reports its cached title.
3. **An MCP file chooser gives an observation without elements and without `dialog`**; the modal state is only in
   `native_text`. browser-use has no file-chooser state.
4. **Where upload and drop files may live on MCP is unchanged** ("Playwright MCP live conformance", Known leftovers
   1); only the refusal is now `dispatched=False`.

## Tool-call logging hook

`core/single_agent/agents/react_agent.py` imported `browser_logging` from the harness to send browser tool-call
lines to `browser_agent.log` (core → harness, the wrong direction). The hook now lives in core
(`openjiuwen/core/common/logging/tool_call.py`), the harness registers the browser log with it, and
`browser_logging` is configured from `CoreSettings` instead of the environment (S_20 "Browser-agent log and the
tool-call logging hook"). This resolves Environment boundary decision 7 and its Known leftover 1.

### Decisions

1. **Core asks for a logger by tool name.** `tool_call_logger(name)` returns the registered `log(message, *args)`
   whose matcher accepts the name, or `None`; `ReActAgent` falls back to the core logger. The line
   (`Executing tool: %s with args: %s`) and the argument redaction stay in core.
2. **The registry is one module-level dict in core.** Agents are built by callers that never see the harness (the
   DeepAgent, the worker, an application's own agent given the runtime MCP tools); routing per agent would need each
   of them to pass the logger. Registering the same matcher again replaces its entry, so repeated agent builds do
   not stack.
3. **`configure_browser_agent_logging(settings)` applies the four logging fields and registers the browser
   matcher.** Called by `create_browser_agent` (`CoreSettings.from_process_env()`, as that factory has no core
   settings), `build_browser_worker_agent` (its settings) and `register_browser_runtime_mcp_server`
   (`CoreSettings.from_env(environ)`): the three places that give an agent browser tools in this repo. The matcher
   is the one core used (`browser_` anywhere or a `mcp_playwright` prefix, case-insensitive).
4. **The applied settings live on the stdlib logger, not in a new module variable.** The logger
   `openjiuwen.browser_agent` is already process-wide; level, propagation and the file handler were already set on
   it. The audit flag is stored on it too, and "no log file" is read from the absence of the marked file handler.
5. **Until the first configuration, the first use configures from `CoreSettings.from_process_env()`.** The
   browser-use driver and transport fetch the logger at import time; the MCP clients, the runtime and tests log
   without passing a composition point. Defaults alone would drop an env-configured path (or create
   `./logs/browser_agent.log` when the file is disabled); the env read stays in the config module.
6. **Reconfiguration replaces the file handler.** Same path: the handler stays (no second truncation). Other path or
   none: the old handler is closed. Before, a changed path added a second handler and both files were written.
7. **`task_tool.py` keeps its import.** It is harness importing harness (the `browser_agent` invoke line); not in
   scope.

### Rejected options

- **Passing the tool-call logger into each agent (config field or constructor).** No module state, but agents built
  outside the harness would stop routing browser lines; a behaviour change for the runtime MCP topology.
- **Registering at import of `browser_logging`.** Import side effect, and it could not take settings.
- **Defaults (`CoreSettings()`) before the first configuration** (decision 5).
- **Passing `audit_raw` from `runtime.py` to `write_browser_agent_audit_artifact`.** Removes the flag from the logger
  but changes five call sites in `runtime.py`, which Phase 4 rewrites.

### Verification

- New tests: `tests/unit_tests/core/common/log/test_tool_call.py` (3: nothing registered, matching names only,
  re-registration replaces), `tests/unit_tests/agent/react_agent/test_react_agent_tool_call_logging.py` (2: without a
  registered logger every line goes to the core logger; with one, browser lines go to it and the rest to core),
  `test_browser_logging.py` (7: file, level and mirroring from settings; reconfiguring moves the file; no file falls
  back to the common logger; an unconfigured logger takes the process settings; configuring routes `browser_*` and
  `mcp_playwright*` lines to the browser file; the two audit tests, moved from
  `test_browser_subagent_status_logging.py` and switched from environment variables to settings).
- Architecture allowlists: `KNOWN_CORE_TO_HARNESS_IMPORTS` without `react_agent.py`; `KNOWN_ENV_READS` without
  `browser_logging.py` (3 files / 9 reads -> 2 / 4). Browser-word counts and module sizes unchanged.
- `pytest tests/unit_tests/harness/tools/browser_move -q`: **1653 passed, 113 skipped** (goldens and runner contract
  unchanged). Harness tests that mention `react_agent` or `browser_agent`, `tests/unit_tests/agent/react_agent/` and
  `tests/unit_tests/core/common/log/`: **759 passed, 2 skipped**.
- Full unit suite (without `agent_teams/cli/` and `agent_rl/online/test_ppo_executor.py`, which need
  `prompt_toolkit` / `omegaconf`): 101 failed, 20959 passed, 23 errors; every failure and error also fails on the
  commit before this change (Windows paths and symlinks, `grep`, missing `chromadb`, Pulsar).
- Live: browser-use conformance **45 passed**, Playwright MCP conformance **51 passed**.

### Known leftovers

1. `CoreSettings.from_process_env()` raises on an unknown `BROWSER_DRIVER`; the import-time fetch in
   `drivers/browser_use/driver.py` and `transport.py` now runs it, so such a value fails at import of the
   browser-use driver instead of later.
2. Core keeps its own `_is_browser_tool_name` for the argument redaction in `_summarize_tool_args_for_log`; the
   routing matcher is a copy in `browser_logging.py`.
3. A process that logs browser tool calls through a `ReActAgent` without passing one of the three registration
   points now writes them to the core log.
4. The registry and the logger state are process-wide; tests reset them (`_TOOL_CALL_LOGGERS`, the marked handler and
   the audit attribute).

### Logger fallback reads only the log settings

Before anything configures it, the browser-agent logger takes its settings from the process environment on first
use. It used to parse the whole `CoreSettings`, so an invalid unrelated value (an unknown `BROWSER_DRIVER`) raised at
import time in every module that fetches the logger (`drivers/browser_use/driver.py`, `transport.py`).
`CoreSettings.logging_from_process_env()` reads only the four log fields; everything else keeps its default.

## Page state, driver session and policy rail

Phase 4 items 3, 4 and 6 and the security rail of item 5: `BrowserPageState` learns contract types,
`DriverSession` recovers from a lost connection, `ActionPolicy` checks commands before the driver (S_20 "Runtime
state and recovery"). Nothing in `runtime.py`, `service.py`, `agents.py` or `browser_agent.py` calls them yet; the
agent switch wires them.

### Decisions

1. **One page state, extended in place.** `record`, `check`, `generation_of`, `resolve`, `reset` and the
   `RefResolver` attributes sit next to the text-snapshot API, which the runtime still uses. The per-ref body of
   `register_ax_snapshot` became `_register_ax_ref`, shared by text snapshots and typed elements; the regex path is
   deleted only when the runtime stops reading snapshot text.
2. **New-generation triggers, in order:** `context_changed`; an address change without the flag (today's
   backstop); a context id change; an element-set change. The element change is the Jaccard distance over
   `(ref, role)` pairs, strictly above `element_change_threshold` (default `0.5`, constructor parameter, no env
   var). `Observation.fingerprint` is not used: it hashes names too, so any text change would bump.
3. **A context change forgets every context.** `last_observation`, `surface_context`, `contexts` and
   `current_context` are dropped, then refilled from the outcome (J8: MCP context ids are positions). Without the
   drop, the next observation would be compared with the previous document and could bump a second time.
4. **`record` takes an `Observation` too.** `observe()` returns an `Observation`, not an `ActionResult`; passing it
   directly keeps one entry point and lets `free_observations` count only observations that came with the result
   of another command (plan item 4).
5. **Observations replace the current AX targets.** An `Observation` is complete, like the full snapshot of
   `capture_browser_state`; refs missing from it leave the current generation.
6. **Stamping includes element refs.** `Element.ref.generation` gets the generation too, so every ref in a stamped
   observation can go through `check`.
7. **`check` reports every stale ref at once** (as `validate_reference_values` does), with today's AX-ref and
   selector wording; point and index refs get the same shape. A ref with an empty generation passes.
8. **`resolve(target_id, generation)` returns `DriverRef` for AX targets and `Locator(css)` for validated probe
   selectors;** a link-only target is a `ValueError` (the primary-link rail turns those into `Navigate`).
9. **The session restarts the same driver object.** Re-provisioning gives a new `ProvisionedDevice`, but the
   driver is not rebuilt: the provisioners normally hand back the same endpoint (remote: the configured `cdp_url`;
   managed: the port stored in the profile; extension: none). `DriverAssembly` gained `surface_settings` so the session can call
   `provision` again; the session replaces its assembly with the new device so `close()` releases it.
10. **Re-provisioning is triggered by `start()` raising a `DriverError`;** `health()` is not consulted.
11. **The failed call raises `ConnectionLost(driver_restarted)` with the original `dispatched`;** the command
    itself is never re-sent. The second loss in a task, or a failed restart, raises a `BaseError`
    (`DEEPAGENT_RUNTIME_ERROR`, `driver_lost`), which is not a `DriverError`, so a tool cannot render it as an
    ordinary tool error. `begin_task()` resets the counter.
12. **`ActionPolicy` takes `builders` keyed by the model-facing tool name.** The tool factory does not exist yet;
    a name → decoder map is what it produces, whatever prefix the driver declares.
13. **Denial as in `BrowserRuntimeRail._deny_tool_call`, with the Phase 4 text.** Per-call `_skip_tool_calls`
    (the base rail's `_skip_tool` is invocation-wide and parallel calls share `ctx.extra`), message
    `### Error\n<reason>`, metadata `executed/state_changed/denied`, `PolicyDenied` as the tool result. The runtime
    payload of today's denial (`runtime_state`, codes) belongs to the browser rail, not to the policy.
14. **A `ValueError` from parsing or decoding is a denial,** so a malformed `browser_tabs` index never reaches the
    driver (J8).

### Rejected options

- **A new `PageState` module next to the old one.** The plan says extend; two registries would disagree on the
  generation.
- **Rebuilding the driver from the registry after re-provisioning.** Needs the driver name, the registries and the
  middleware chain in the session (a second composition root) for a case no provisioner produces today.
- **`health()` after a restart to detect a dead device.** The MCP server answers `ping` with Chrome gone, so it
  would not decide anything there; the next command reports the loss instead.
- **Using the base `_apply_reject`.** It sets the invocation-wide `_skip_tool`.
- **Letting the rail's `PolicyDenied` propagate as an exception.** The harness would treat it as a tool failure
  after the call started; the plan wants it before anything runs.

### Verification

- New tests: `test_browser_page_state_record.py` (18: each trigger incl. a same-address reload via
  `context_changed`, the threshold and its parameter, a role change, stamping, AX targets from elements, stale refs
  nested in `FillForm`, stale point/locator refs, unknown current refs, contexts replaced on change, the
  free-observation counter, `resolve`, `reset`, the page state as a decoder's `RefResolver`),
  `test_driver_session.py` (8, `FakeDriver`: restart with message and reset, the command sent once, second loss
  fails, `begin_task`, re-provisioning, failed restart, `observe`, `close` after re-provisioning),
  `test_action_policy.py` (7: callback registered, denial message/metadata/skip/`PolicyDenied`, allow, command and
  surface context passed (D15), other commands and unknown tools, decode error, no driver call through the ability
  manager).
- Existing `test_browser_page_state.py` (25) unchanged and green.
- Browser-word allowlist: `playwright_runtime/page_state.py` 26 → 29 (the address backstop); the two new modules
  have none.
- `pytest tests/unit_tests/harness/tools/browser_move tests/unit_tests/harness/rails
  tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` (live assembly deselected): **2762 passed, 111
  skipped** (architecture, goldens and runner contract included).
- Live: `test_assembly_live.py` **2 passed** (`DriverAssembly` field).

### Known leftovers

1. Nothing calls `record`/`check`/`DriverSession`/`ActionPolicy` yet; `capture_browser_state` still observes after
   actions, so the saved observations are counted only once the tools use `record`.
2. The address backstop and `contexts` read `url`, `title` and `tabs` from the surface context by attribute name:
   the core cannot import `BrowserContext`. They move with `page_state.py` into the browser surface (Phase 9).
3. A re-provisioned device with a different endpoint would need a rebuilt driver (decision 9). Done: see "Agent
   construction, instance settings and restart on a dead device" (decisions 1–3 replace decisions 9 and 10).
4. The browser profile's `messages` (`driver_restarted`, `driver_lost`) and `policy_rules` come with the profile
   task; the session reads the two messages at construction. Done: see "Browser surface profile".

## Browser surface profile

Phase 4 item 1 (the profile half): the contract gains what a model-facing tool needs (native tools, tool specs with
a schema and a decoder, a ref resolver, a renderer, contextual policy rules), and `create_browser_surface` returns a
filled `SurfaceProfile` (S_20 "Surface types", "Browser surface profile"). The tool factory that turns the profile
into tools comes next; nothing in `runtime.py`, `service.py`, `agents.py` or `browser_agent.py` reads the profile
yet.

### Decisions

1. **Native tools live in `DriverInfo`, not in the profile (Q1).** `native_tools` (spec name → `NativeTool`) and
   `tool_name_prefix` come from the driver, so `surfaces/` never names a driver and the MCP schemas follow the
   server version. `SurfaceProfile.native_tools` stays as the plan's extension point for surface-level formats
   (a computer-use tool) and is empty for the browser.
2. **`RefResolver` sits in `contract/refs.py`** next to the ref types it returns, so `driver.py` (`NativeTool`)
   and `surface.py` (`ToolSpec`) can both use it without importing each other. It names `contexts` and
   `current_context` as well as `resolve` and `generation_of`: `browser_close` and a close without an index need
   the focused context.
3. **`ContentBlock` is `str | Screenshot`.** The only image a browser tool returns is a screenshot; a separate
   image type would duplicate `Screenshot`.
4. **`PolicyRule.check(action, context)` (D15)** stays a dataclass field holding a callable, so `rule.check(...)`
   reads the same as a method.
5. **`SurfaceProfile.capability_tools` next to `capability_catalog`** (decided with Aleh, 2026-10-10). The pairs
   (C26) cannot say which tools an entry selects (`browser_click` and `browser_mouse_click_xy` are both `Click`),
   and the factory in the core cannot import `surfaces/browser/capabilities.py`. The factory reads the names from
   `capability_tools` and a spec's ref kinds from the entry's pairs.
6. **`BrowserContext.tab_urls` and `current_tab`** (decided with Aleh, 2026-10-10). The tab list the design asks
   for (`- i: (current) [title](url)`) needs every tab's URL and the focused tab; `ContextRef` carries neither and
   the MCP decoder drops both. The renderer uses the fields now; the drivers fill them later. Until then a line
   shows a URL and `(current)` only when known.
7. **One tool spec module for the browser dialect.** The 17 browser-use schemas are copied verbatim from
   857239db; the golden is the oracle. `browser_tabs` adds `new` to its enum (Q6) and keeps the description. The
   vision, pdf, console and network tools get specs without schema or decoder: the catalog needs their commands,
   and only a driver's native tool serves them today.
8. **Decoders map ids without asking the driver.** `target_id` goes through `RefResolver.resolve`, `ref` becomes
   `DriverRef(ref, generation_id)`, `selector` becomes `Locator(css)`, in that precedence and at the generation the
   model sent; staleness is the page state's `check`. Ignored arguments (`wait_until`, `timeout_ms`, drag `steps`)
   are accepted (C16, C30); `steps` and `timeout_ms`-like integers are still validated where today's tool did.
   Error texts keep today's wording.
9. **A close without an index closes the current tab** (design §2, MCP semantics); 857239db required an index.
   A select needs an index; one outside `contexts` is a `ValueError` (J8).
10. **`renderer.result(Observe(), r)` is the tab list.** `browser_snapshot` renders through `observation()`;
    `result()` with a plain `Observe` only answers `browser_tabs list`, so the renderer needs no flag. An `Observe`
    with a screenshot renders the detail and the image.
11. **A failed result renders as an error.** `ActionResult(ok=False)` shows `detail`, prefixed with `### Error`
    unless it already starts with it (the MCP modal refusal does; browser-use failure details do not).
12. **Messages:** `driver_restarted` is the plan's wording; `context_changed` is today's stale-ref sentence
    (`runtime.py`, as a format string with `{generation}`); `driver_lost` has no wording today and is new.
13. **The catalog keeps only served names.** An entry lists the public tool names that a spec or the
    `browser_find` extra tool serves; entries with no command (`advanced_code`, `unsafe_dev`, `config`, `storage`,
    `testing`) stay as keys with no names and no pairs, so the factory can log them once (C25).
14. **`scene` stays `None`.** The conformance suite takes `browser_scene()` directly; importing the scene's HTTP
    server with every profile is not needed yet.

### Rejected options

- **Copying the MCP schemas into the profile keyed by driver name.** Needs a driver name in `surfaces/` and drifts
  with the server.
- **Replacing the catalog pairs by names and adding ref kinds to `ToolSpec`.** A larger contract change, and C26
  pairs are what the skip-and-log rule reads.
- **Choosing the current tab by title.** Ambiguous (two tabs with the same title in the fixtures) and loses the
  other tabs' URLs.
- **A tab-list flag on `Observe`.** A model-facing detail in a shared command; the renderer method already tells
  the two cases apart.
- **Rendering the free observation of a result.** Q14: kept for the page state, not shown.

### Verification

- Contract: `contract/test_tool_types.py` (9): native tools and prefix defaults, `HandlerDriver.info` keeping them,
  decoders with a resolver, the renderer protocol on a profile, `PolicyRule.check` with the context,
  `capability_tools` default.
- `surfaces/test_browser_tools.py` (252): each of the 17 dialect specs equals its entry in all 11 browser_use
  golden views (187 comparisons), the golden tools without a spec are exactly cancel, clear-cancel, find and
  recall, 33 decoder cases, 25 invalid-argument cases.
- `surfaces/test_browser_renderer.py` (57): the recorded MCP answers through the result decoder (14 action
  results, 5 tab lists, 9 errors, 2 refusals, 3 snapshots) and FakeDriver results; one test name per expected
  difference of design §4 (snapshot link, Events, plain error, stale ref with observation, intercepted message,
  RunScript values, tab lists, element description, boxes, modal refusal).
- `surfaces/test_browser_profile.py` (13): fields, timeouts, messages, no driver names, catalog entries and
  pairs, `core` equal to `CORE_BROWSER_TOOL_NAMES`.
- `test_assembly.py`: the browser surface now returns its profile (one assertion changed).
- `pytest tests/unit_tests/harness/tools/browser_move`: **2018 passed, 113 skipped** (architecture, goldens and
  runner contract included).
- Live: browser-use conformance **45 passed**, Playwright MCP conformance **51 passed**.

### Known leftovers

1. MCP part done with the tool factory (the MCP decoder fills `tab_urls`/`current_tab`). Browser-use
   `SwitchContext` and `CloseContext` results carry no surface at all, so their tab list is empty until the
   browser-use helpers task.
2. Done with the tool factory: the MCP driver fills `DriverInfo.native_tools` at `start()`.
3. `browser_find` has no spec; its description and schema come from the native tool (MCP) or the 857239db text
   (browser-use) when it becomes an extra tool.
4. Browser-use results have no `detail` for most commands, so many render as no text; the browser-use wire
   fixtures show what the model reads there.

## Tool factory and Playwright MCP native tools

Phase 4 item 1 (the factory half): the Playwright MCP driver offers its server tools as native tools, and
`ToolFactory` turns the browser profile plus a driver's `DriverInfo` into model-facing `CommandTool`s (S_20 "Tool
factory", "Native tools"). The tool-list goldens are rebuilt from the factory alone. No agent uses the factory yet;
nothing in `runtime.py`, `service.py`, `agents.py` or `browser_agent.py` changed.

### Decisions

1. **MCP decoders are the inverse of the handlers.** `drivers/playwright_mcp/tools.py` holds one decoder per server
   tool a capability can select (24). The test decodes every argument set the driver's handler test sends and checks
   that the driver sends exactly those arguments again. A `target` matching `ARIA_REF` is a `DriverRef` at the
   generation the page state last saw it in, anything else a `Locator(css)`; `doubleClick` is `count=2`;
   `slowly` types without clearing (`clear=not slowly`), which is what the server does.
2. **Cosmetic and dropped arguments are accepted and ignored** (C11, C12, C16): `element` everywhere, `filename`,
   the snapshot's `target/depth/boxes`, the screenshot's `target/element/type/scale` (element screenshots are
   dropped), the click's `delay`. Points carry no generation (`PointRef(x, y, "")`): the model sends none and the
   page-state check lets an empty generation pass.
3. **Native tools come from `tools/list` at `start()`.** Only tools with a decoder become `NativeTool`s; the schema
   is the card the client already lists (`sanitize_json_schema`, the same path the goldens were captured through),
   the description is verbatim. `tool_name_prefix` is `mcp_model_tool_prefix(server_name)`, the function the
   ability manager uses (Q2: no instance-key suffix).
4. **The MCP result decoder fills `tab_urls` and `current_tab`** (Q-B) from every header: `PageHeader` gains
   `tab_urls`; a page without `### Open tabs` has its own URL as the one entry.
5. **`build(capabilities, *, image_support, session, page_state)`.** The task wrote `build(capabilities, *,
   image_support)`; a `CommandTool` needs the session and the page state at call time and the `ToolContext` for
   extra tools is made of them, so they are arguments of `build`. `ToolFactory(profile, driver_info)` stays as
   designed and selection does not depend on them.
6. **`Observe` counts as handled on every driver**: it is `Driver.observe`, never in `info.actions`.
7. **Rule 4 (generated schema) needs ref kinds.** Without that restriction browser-use got a `browser_mouse_wheel`
   tool with an empty schema (`Scroll` is handled, the catalog pairs it with `None`), against C21 (no `Scroll` tool
   on browser-use). A spec with no schema and no ref kinds to generate from is skipped and logged.
8. **Screenshot gate without tool names.** The core may not name tools (architecture tests). Without image support
   the factory drops a spec whose only command is `Observe` and whose decoder, called with no arguments, asks for a
   screenshot: `browser_take_screenshot` on both dialects; `browser_snapshot` and `browser_tabs` are untouched.
9. **Observe outcomes render by the tool.** A tool whose only command is `Observe` renders `renderer.observation()`
   (the snapshot text); `browser_tabs` (several commands) renders `renderer.result()`, the tab list.
10. **`CommandRecord.surface` instead of `url`/`title` fields.** `url` is a browser word, forbidden in the core;
    the typed `SurfaceContext` carries both, and the browser rail reads `record.surface.url` / `.title`.
    `command` is the command type, not the instance, so no typed text or script sits in the record.
11. **The record carries the message metadata** (decided with Aleh, 2026-10-10). A tool cannot set
    `ToolMessage.metadata`: the ability manager builds the message from `render_for_llm` after `invoke`
    (`ability_manager.py`, "Build ToolMessage for successful execution"). `CommandRecord.message_metadata` gives
    `success`, `executed`, `state_changed`, `denied` with today's meaning (`state_changed` for a success that may
    change the screen, a timeout, or a context change; `executed=False` for anything stopped before the page, which
    the context processors treat like today's denial); the rail copies it in `after_tool_call`.
12. **Stale before the driver.** A `StaleRef` from `page_state.check` costs one `observe()`; the observation is
    recorded and rendered under the error (G9). If that observe fails, its own error is what the model reads.
13. **`browser_find` stays out of this task** (decided with Aleh, 2026-10-10): it has no command (C10) and becomes
    an extra tool with the browser helpers. The factory goldens compare the 13 command tools and assert that
    `browser_find` is the only MCP tool missing.
14. **Skips are logged once per factory**, keyed by name: an entry that selects no tool, a spec the driver cannot
    run, a selected name with no spec that no extra tool serves.
15. **Images** use the multimodal shape MCP tools deliver; `source` is the driver name (today `mcp`).
16. **Tool-result golden at the renderer level**, as the design states: each recorded answer decoded with the result
    decoder, the command with the native decoder, rendered with `result()` / `error()`; `browser_snapshot` answers
    render as the observation the driver makes of them. `regenerate --results` rewrites the files and prints each
    one's diff against the fixture's recorded `model_text`. The files are `.txt`, which the repository ignores, so
    they are added with `git add -f`.

### Rejected options

- **A core hook copying tool-output metadata onto the tool message.** Touches `openjiuwen/core`; the record is
  enough for the rail (Aleh chose the record).
- **Building `browser_find` now** to make `runner` byte for byte: it is the helpers task's extra tool.
- **A screenshot flag on `ToolSpec`, or a tool-name list in the factory.** A contract change, or tool names in the
  core.
- **Probing every decoder with empty arguments.** Generated decoders raise `TypeError` without a ref; only
  `Observe`-only specs can be screenshot tools.
- **`ToolFactory(profile, ToolContext)`.** The context would have to exist before selection, which needs neither
  the session nor the page state.
- **Rendering the result golden through `CommandTool` on a fake server.** Snapshot and screenshot answers are
  consumed inside `observe()`; a per-fixture golden would need invented second answers.

### Golden comparison (factory alone, `image_support=False`)

| View | Result |
|---|---|
| MCP `runner` | the 13 command tools equal the golden byte for byte; missing only `browser_find` |
| MCP `core`, `advanced_code`, `extended_interaction`, `pdf` | equal, minus the 4 helpers and `browser_find` |
| MCP `vision` | equal minus helpers, `browser_find`, `browser_mouse_down`, `browser_mouse_up` (C20) |
| MCP `unsafe_dev` | minus helpers, `browser_find`, `browser_run_code_unsafe` (C7) |
| MCP `devtools` / `network` / `storage` / `testing` / `config` | minus helpers, `browser_find` and the 12 / 5 / 17 / 5 / 1 tools with no command (C25) |
| browser-use, every view | equal except: `browser_cancel_run`, `browser_clear_cancel` dropped (Q3); `browser_close`, `browser_drag`, `browser_drop` only with `extended_interaction` (Q3); `browser_take_screenshot` only with image support (the MCP rule, now on both drivers); `browser_find`, `browser_recall_offload` not yet (extra tools); `browser_tabs` enum adds `new` (Q6) |

### Verification

- `drivers/playwright_mcp/test_tools.py` (78): round trip over the driver's 35 decodable handler cases, every spec
  the server offers has a decoder and nothing else does, target and generation rules, ignored arguments, tabs,
  16 invalid-argument cases.
- `drivers/playwright_mcp/test_driver.py` (+3): native tools after `start()` (description, sanitized schema,
  decoder), empty before, prefix. `test_result.py`: surfaces carry `tab_urls` / `current_tab` (expectations
  updated). `surfaces/test_browser_renderer.py`: tab lists now come straight from the decoder (the helper that
  filled the fields by hand is gone).
- `test_tool_factory.py` (32): selection order, ref-kind and command skips, logging once, native over profile form,
  description fallbacks, generated schema, screenshot gate, extra tools from the `ToolContext`, builders for
  `ActionPolicy` (a denial through the rail), the pipeline (decode, execute, record, render), stale ref before the
  driver with one observation, free observation recorded not rendered, observation vs result rendering, image
  blocks, typed errors, refused results, undecodable arguments, restart message, task failure, metadata table.
- `snapshots/test_tool_factory_golden.py` (37) and `snapshots/test_tool_result_golden.py` (37; 36 fixtures, 11
  identical to the recorded model text, the rest differing only by design §4 items 1, 2, 3, 9 and the screenshot
  link).
- Fail-before: the changed decoder tests fail on the old decoder (14 failed); the new modules' tests fail at import.
- `pytest tests/unit_tests/harness/tools/browser_move`: **2205 passed, 113 skipped** (old MCP goldens, runner
  contract, architecture included).
- Live: Playwright MCP conformance **51 passed**, browser-use conformance **45 passed**.

### Known leftovers

1. `browser_find` and the helper tools (probes, batch, recall) come as extra tools with the helpers task; the factory
   goldens then compare all 14 / 18 tools. (Done: `core` 18 / 18; see "Batch and custom actions over commands".)
2. Nothing copies `CommandRecord.message_metadata` onto the tool message yet (the switch's rail work).
3. A screenshot tool renders through `observe()`: on MCP that is the snapshot text and the image (two server calls);
   today the model gets the image and a file link. Only vision with image support sees it. (Rendering done: a
   screenshot-only tool now shows the image only; see "Rails on command records".)
4. browser-use does not fill `tab_urls` / `current_tab` yet (helpers task). (Done; see "Helper infrastructure".)
5. `profile.rails` factories are not called by the factory; the rails task registers them with the `ToolContext`.
6. The browser-use tool-result golden needs the browser-use wire fixtures (helpers task).

## Browser helpers over commands (probes, page metadata, find)

Phase 4 helpers, first half: the page probes, the page metadata, `browser_find`, the decision-policy probe and
`activate_page` run as commands on the driver session (S_20 "Helpers"). They are new code in
`surfaces/browser/helpers/`, registered as the browser profile's `extra_tools`; the runtime's helpers (MCP code
runner) still serve the agent and are unchanged, as are their tests. The switch points the agent at the new ones.

### Decisions

1. **Built next to the old helpers** (O3, wave-3 review). The runtime keeps its MCP path until the switch; the new
   tools reach the agent only through `profile.extra_tools` factories taking the `ToolContext`.
2. **Probes are page JavaScript** (C7). The page function is cut out of the existing builders'
   `async (page) => { const params = …; return await page.evaluate((params) => {…}, params); }` with one anchored
   regex, and run as `RunScript(code=<function>, args=<params>)`; the builders (clamping, site profiles, selector
   cache records, generation id) stay the single source, untouched. Two attempts, retried when the value is not a
   JSON object, as before. The result dict the model reads keeps today's keys.
3. **Every helper command is recorded** with `page_state.record`, as `CommandTool` does: the page state sees the
   surface a driver reports with the script result. A probe result whose `url` differs from the page state's URL
   resets the page state (advance and forget contexts, the rule `record` applies to a new address), because
   browser-use reports no address with a script result; the old runtime only advanced the generation.
4. **Page metadata splits** into what the page state already has (URL, title, tabs from the last `BrowserContext`)
   and one `RunScript` for page position and semantic state, the page-measure function cut out of the metadata
   script. When a driver gives no `current_tab` (browser-use), the focused tab is the page state's current context.
5. **`activate_page` is `SwitchContext`** to the tab whose URL matches, else the current context, observing first
   when no context is known. The Playwright MCP server's tab select calls `bringToFront()`, which is what the old
   script did. The switch is recorded, so the generation advances (the old script did not touch the page state);
   a policy probes again after activating, so its targets are fresh.
6. **`browser_find` searches the last observation** (C10) and ports the server's algorithm (`find.ts` of
   `@playwright/mcp` 0.0.78): case-insensitive substring or `/pattern/flags`, 3 context lines, merged windows,
   ancestor path, `...` off the match paths, `----` between snippets, the same `Found N matches for …` /
   `No matches found for …` text. On Playwright MCP it searches the fenced YAML of `native_text`. With no last
   observation it takes one (`Observe`), where the server would snapshot.
7. **One find tool for both dialects** (O3). Name, description and schema come from
   `driver_info.native_tools["browser_find"]` when the driver lists it (Playwright MCP: byte-identical golden
   entry), else the 857239db browser-use schema (byte-identical to the browser-use golden). The browser-dialect
   form (`query`, `regex: bool`, `limit`, `generation_id`) is decoded into the same search; `limit` caps the matches.
8. **`NativeTool.build` is optional** (O3). The Playwright MCP driver lists `browser_find` with `build=None`; no tool
   spec is named `browser_find`, so the factory never reaches it.
9. **Runner view = factory tools minus the probes.** The factory appends every extra tool to every build; the worker
   removes the helpers after the build today, so the `runner` golden is compared with the probes removed.

### Rejected options

- **Refactoring `probes.py` into separate page-function constants.** The interactive probe is a 600-line f-string
  with doubled braces; extracting it touches old code the runtime still uses. Cutting the function out of the
  builder output keeps the old helpers byte-identical.
- **Re-reading URL, title and tabs inside the metadata script.** The design takes them from the last
  `BrowserContext`; only position and semantic state need the page.
- **A fresh observation on every find**, as the server does: C10 searches the last observation and saves a snapshot
  per call.
- **Not recording `SwitchContext`** to keep the generation: a real tab change must advance it, and the policy
  probes after activating anyway.
- **The 857239db error for find without a snapshot** ("call browser_snapshot first"): costs the model a turn the
  server never costs.

### Golden comparison (factory alone, `image_support=False`)

| View | Result |
|---|---|
| MCP `runner` | 14 / 14 byte for byte (factory tools with the two probes removed, as the worker does) |
| MCP `core` | 16 / 18 byte for byte: the 13 command tools, `browser_find`, both probes; `browser_batch_interact` and `browser_recall_offload` not built yet |
| MCP other views | equal minus `browser_batch_interact`, `browser_recall_offload` and the tools with no command (C7, C20, C25) |
| browser-use, every view | as before, plus both probes (C7), and `browser_find` identical; `browser_recall_offload` not yet |

### `browser_find` result shape

Live on the scene with `@playwright/mcp` 0.0.78, the tool's text equals the server's `### Result` section for
`text`, a `/regex/i` and a query with no match. Remaining differences: the server's answer also carries its page
header sections (not part of this tool's text); it searches a fresh snapshot, this tool the last observation (a page
changed by a script since is not seen); JavaScript regex syntax is read by Python `re` (flags `i`, `m`, `s`; `u`,
`y`, `d`, `v` ignored); a `/` inside a pattern is shown unescaped. On browser-use the text replaces the 857239db JSON
(`matches` with `target_id/ref`, `count`, `page_state`), and the search runs over browser-use's own snapshot text,
indented with tabs.

### Verification

- `surfaces/test_browser_helpers.py` (29, FakeDriver): probe cards equal the runtime helper cards; each probe sends
  one `RunScript` with the page function and the clamped params, retries once on a malformed value, reports an
  unparsable value, a refused script and a `DriverError`; a new address advances the generation; cards register
  primary links; `probe_for_policy` sends the source and params verbatim and registers targets, failure envelopes;
  `activate_page` picks the tab by URL, falls back to the current tab, observes first, returns `False` on errors;
  page metadata sends one script and builds the tab list (with and without `current_tab`); find: card from the
  driver or the browser-dialect schema, snippet with ancestor path, last observation reused, merged windows, no
  match, four refused argument sets that send nothing, the `query` form with `limit`.
- `surfaces/test_browser_helpers_live.py` (3, `live`, gated like the suites): the interactive probe finds the scene's
  save button on Playwright MCP and on browser-use; on Playwright MCP find equals the server's own answer for three
  queries.
- `snapshots/test_tool_factory_golden.py` (38): `runner` 14 / 14 and `core` 16 / 18 byte for byte, the other views
  with the listed differences. `drivers/playwright_mcp/test_driver.py` (+1): `browser_find` is a native tool with
  no decoder. `surfaces/test_browser_profile.py`: `extra_tools`. `contract/test_tool_types.py` (+1).
- `pytest tests/unit_tests/harness/tools/browser_move`: **2237 passed, 116 skipped** (the old helper, policy-probe
  and runtime tests untouched and green).
- Live: Playwright MCP conformance **51 passed**, browser-use conformance **45 passed**, helpers **3 passed**.

### Known leftovers

1. `browser_batch_interact` and `browser_recall_offload` are not extra tools yet, so `core` is 16 / 18. The batch tool
   is the second helpers task; the recall tool needs the workspace, which `ToolContext` does not carry. (Recall done,
   `core` 17 / 18; see "Helper infrastructure". Batch done, `core` 18 / 18; see "Batch and custom actions over commands".)
2. The helper tools return no `CommandRecord`, so nothing gives their tool messages metadata; the rails treat a
   message without a record as a call that sent nothing (O6). A probe is a `RunScript` (`Effect.SCRIPT`), which
   `message_metadata` would count as state-changing. (Done: records with the declared `READ` effect.)
3. The design's per-tool `effect`, `exempt_from_budget` and `progress` flags for extra tools are not declared yet.
   (Done: `ToolFlags`.)
4. The factory does not check `NativeTool.build is None` itself; it is safe only because no spec is named
   `browser_find`.
5. The probe page functions are cut out of the builder output by a regex; the switch can move them into their own
   constants when the old runtime helpers are deleted.
6. browser-use still does not fill `tab_urls` / `current_tab`; `page_metadata` and `activate_page` fall back to the
   page state's current context there (a tab cannot be picked by URL on browser-use). (Done; live test.)

## Rails on command records

Phase 4 item 5: the worker rails that read tool text get typed versions that read `CommandRecord`s (S_20 "Rails on
command records"), built next to the old ones like the helpers. The record gains what they need. The old rails, their
tests and the service registering them are unchanged; the switch registers the new classes, deletes the old ones and
gives the new ones the old names.

### Decisions

1. **`CommandRecord.blocked_by`** (O1) carries `ActionIntercepted.by`; the hint names the blocker from it instead of
   parsing the Playwright call log.
2. **`CommandRecord.action`** (O2): the command with its sensitive fields masked. The rule moved out of `Tracing` into
   `masked` in `drivers/middleware.py` (no browser words, already importable from the core); `Tracing` traces
   `masked(command)`, so both share one rule. The guard compares commands, not tool arguments.
3. **An `Observe` record keeps its observed elements as `value`** (decided with Aleh, 2026-10-10). The old guard
   compared snapshot text, so a loading page whose progress bar moves is not a repeat; the record had nothing that
   changes with it (the generation advances only on large changes). The elements are typed, so no text is compared;
   an unchanged screen is still a repeat.
4. **A call without a record compares by tool name and arguments** (decided with Aleh, 2026-10-10). O6 says such a
   call is a failed call that sent nothing; keying it on the call keeps today's behaviour (identical undecodable calls
   form a streak) and reads no tool text. The hint rail leaves it alone.
5. **"Identical" = same masked command and same result** `(ok, error type, value, surface context)`. A script keeps
   the old normalisation: whitespace-free code with digits masked, value with digits masked, so counter variants
   (`() => 'done1'`, `'done2'` …) form one streak as before. `detail` is not compared, so the `### Events` and
   console counts the old fingerprint stripped play no part.
6. **Same behaviour list** (D6): note from the 3rd identical call, tools withheld for the next model call after the
   5th, reset on a different call, no-op scripts as one streak, same-turn calls counted in order (F1 stays), state
   reset per invoke. Only the model-facing message changes: the note is appended to plain text, as there is no
   `{"result": …}` envelope any more.
7. **The typed guard subclasses the old one** and overrides `after_tool_call`; `before_invoke` / `before_model_call`
   are shared, so the withholding and the finish section cannot drift. The switch folds them into one class.
8. **Hint wording** per design §4 item 5: `Click not delivered: e57 is covered by <blocker>.` The verb is the command
   type (`Click`, `Hover`, `TypeText` …) and the target is the ref the model used, not Playwright's resolved
   element. The consent hint is keyed on `blocked_by`; the hints and the retry sentence are the old ones (one shared
   constant). Messages that are not strings are left alone, as before.
9. **`FinalAnswerRecoveryRail` is unchanged**: it reads the model output and the recovery flag, never tool text.
   Its tests run again with the snapshot tool as a `CommandTool`.
10. **A screenshot-only `Observe` tool renders the image only** (O4): `renderer.result` on the screenshot, where it
    showed the snapshot text and the image. A tool that only sometimes takes a screenshot still renders the
    observation.
11. **The image block is labelled with the surface name** (O8): `profile.name` (`browser`) instead of the driver name
    (`playwright_mcp`; the old path wrote `mcp`). `ToolContext` is unchanged (the helper tests build it
    positionally); `CommandTool` takes the label.
12. **`command_record(tool_result)`** in the tool factory is the one way rails read a record.

### Rejected options

- **Observe identity by a new `Observation.fingerprint` field** on the record: it covers ref, role and name only, so
  a progress value change would count as a repeat.
- **Observe never counted as a repeat**: five identical snapshots would no longer withhold tools.
- **A call without a record resetting the streak**: a loop on undecodable arguments would never be stopped.
- **Carrying the error object in the record**: one string is what the hint needs; the record stays plain data.
- **Rewording the retry sentence** to drop the tool name: model-visible text change beyond the design's item 5.
- **A `surface` field on `ToolContext`** for the image label: the helper tests construct it positionally.
- **Standalone typed guard** duplicating `before_invoke` / `before_model_call`: two copies of the withholding logic
  until the switch.

### Verification

- `test_browser_repeated_call_guard_records.py` (52 passed, 1 strict xfail): every case of the old guard tests under
  the same name, on records from `CommandTool` over `FakeDriver` with Playwright MCP tool names and arguments
  (`command_tools.py`), plus one case showing the surface context is part of the result. Worker loops end with tools
  withheld; same-turn press-key batch still withholds (F1 pinned); no extra model round trip with and without the
  rail. Mutation checks: dropping value/surface from the identity fails 5 cases, dropping the script normalisation
  fails 3.
- `test_browser_intercepted_action_hint_records.py` (21 passed, 1 strict xfail): every old case under the same name
  (blockers from the old fixtures in the form the drivers report, ref / locator / hover targets, untouched errors,
  observer keeps the raw error, no extra model round trip).
- `test_browser_final_answer_recovery_records.py` (15): every old case with the snapshot tool as a `CommandTool`.
- The two service-registration cases are strict xfails: they turn into failures once the service registers the
  typed rails, so the switch has to remove the marks.
- `test_tool_factory.py` (36): one test runs a sensitive `TypeText` through `Tracing` and a `CommandTool` and finds
  the mask in both; `blocked_by`, the `Observe` value, the image-only screenshot tool, the surface-name label,
  `command_record`. Six of them fail on the previous code.
- `pytest tests/unit_tests/harness/tools/browser_move`: **2329 passed, 116 skipped, 2 xfailed** (old rail tests,
  factory and tool-result goldens, runner contract and architecture included; tool-result goldens unchanged: they
  render at the renderer level, where O4 and O8 do not apply).
- Live (after the record change): Playwright MCP conformance **51 passed**, browser-use conformance **45 passed**.

### Known leftovers

1. The service still registers the text-based rails; the switch registers `CommandRepeatGuardRail` and
   `CommandInterceptedHintRail` (removing the two xfail marks), deletes the old classes and renames the new ones.
2. The retry sentence names `browser_snapshot`, a tool name in a core module (allowlisted with the old rail); a
   surface-provided wording would remove it.
3. The hint verb is the command type name (`TypeText not delivered`); a surface may want friendlier verbs.
4. Helper tools (probes, find) return no record, so the guard compares their calls by tool name and arguments.
   (Done: they return records; see "Helper infrastructure".)
5. F1 (same-turn batches counted as a loop) is unchanged, as decided; a fix belongs to the benchmark phase.

## Helper infrastructure (records, workspace, tabs, fresh observation, points, upload staging)

Phase 4 helpers, the infrastructure the batch tool builds on (wave-4 review P4–P7, P9, P11; wave-1 L3; Q9): helper
results carry command records, one shared step runs and records a command, the recall tool joins the extra tools,
browser-use reports tab addresses and dialogs after navigation, `browser_find` searches a current observation,
points carry the generation of their screenshot, and the Playwright MCP driver stages upload files. The old agent
path is unchanged.

### Decisions

1. **`ToolFlags(effect, exempt_from_budget, progress)`** (P4, design §6) in `tool_factory.py`, declared by each extra
   tool as its `flags` class attribute: probes and find `READ`, not exempt, progress (today's
   `_is_browser_progress_tool` counts every `browser_*` helper); recall `exempt_from_budget=True` (today's
   `_is_replan_exempt_tool`). The flags stay on the tools; the record carries only the effect.
2. **Helper results carry a `CommandRecord` with the declared effect** (P4). A probe is a `RunScript`
   (`Effect.SCRIPT`), but its record says `READ`, so `message_metadata` does not count it as a state change. The
   payload moved under `data["content"]`; `render_payload_text` renders a non-string `content` as the JSON it
   rendered before, so the model reads the same text. Find's record is an `Observe` with `dispatched` = whether it
   observed and `value` = its answer; an argument error leaves no record (nothing was sent).
3. **One shared step** (P7): `ToolContext.execute(action)` / `observe(command)` run through the session and record in
   the page state. `CommandTool`, the probes and the page helpers use them; the batch tool will. `CommandRecord.of` /
   `failed` build the records for `CommandTool` and the helpers alike.
4. **`ToolContext.workspace` and `language`** (P5), keyword arguments of `ToolFactory.build` with defaults, so the
   existing callers keep working. `language` is not in the review's wording; the recall tool's description is
   language-dependent and the goldens are English, and `build_agent_parts` takes a `language` already. The default
   `"cn"` is the recall tool's and `resolve_language`'s.
5. **The recall tool is a subclass** (`RecallOffloadTool(BrowserOffloadRecallTool)`) taking the `ToolContext`; the old
   tool and its module are untouched. It sends no command, so it has no record. The worker's golden has no recall
   tool: the runner removes it with the probes.
6. **browser-use tab fields from the tab rows the sidecar already sends** (P6): `tab_urls` from each row's `url`,
   `current_tab` = the first row marked active (the sidecar's URL heuristic). No wire change for tabs. `OpenContext`
   already listed the tabs; `SwitchContext` and `CloseContext` now list them too (one more request), so every tab
   command reports the new tab list. A failure of that list after the switch is `dispatched=True`: the switch ran.
7. **Navigation replies carry the open dialog** (L3): the sidecar's `_nav_result` adds `dialog` (the same
   `_open_dialog()` the act replies use, cached target info only), the driver puts it in the result surface. Wire
   version 3 → 4.
8. **Fresh observation** (P9): `PageState.fresh_observation` is the last observation while it is of the current
   generation and no command was recorded after it without a free observation, nor noted as dispatched and failed
   (`note_dispatched`, called by `ToolContext.execute`). Any command counts, a `READ` one too: a probe is page
   JavaScript whatever it declares. `browser_find` searches the fresh observation and observes when there is none.
9. **Points carry the generation of the last screenshot** (P11 / O7): the page state records it whenever an
   observation with a screenshot is recorded and keeps it across generations; `RefResolver.screenshot_generation`
   hands it to the decoders (MCP point tools, generated point schemas). `PageState.check` then refuses a point on an
   older screenshot like a stale element ref. Before the first screenshot the generation is empty and nothing is
   checked, as before.
10. **Upload staging inside the MCP driver** (Q9): `UploadFile` / `Drop` copy an existing file outside the server's
    working directory to `output_dir/uploads/<12 hex>/<name>` (a directory per file keeps the original name the page
    sees) and send the copy. Files inside, missing files and directories go as given: the server's refusal
    (`DriverError(dispatched=False)`, K2) stays for them. The live suite runs without
    `--allow-unrestricted-file-access`, so its upload and drop cases go through staging.

### Rejected options

- **Flags in `AgentTools` or in `CommandRecord`**: a map in `AgentTools` duplicates what the tools carry; record
  fields would be defaults on every command tool, and the recall tool has no record.
- **The record inside the probe payload dict**: the JSON the model reads would then contain it.
- **Stamping points in `CommandTool` after decoding**: the policy rail and the batch tool decode too; the resolver is
  the one place every decoder asks.
- **Only non-`READ` commands outdating the observation**: probes run page JavaScript though declared `READ`; P9
  speaks of any dispatched command.
- **A new wire field for tab URLs**: the sidecar's tab rows already carry `url` and `active`.
- **Staging into the server's `.playwright-mcp` output directory**: the driver's `output_dir` is the server's working
  directory, an allowed root on every configuration; `uploads/` under it needs no knowledge of `--output-dir`.
- **Deleting staged copies after the call**: a drop or file chooser may still read them; they live in the driver's
  own output directory.
- **A server flag** (`--allow-unrestricted-file-access`): Q9 keeps the server's file check.

### Golden comparison (factory alone, `image_support=False`, `language="en"`)

| View | Result |
|---|---|
| MCP `runner` | 14 / 14 byte for byte (factory tools with the probes and recall removed, as the worker does) |
| MCP `core` | **17 / 18** byte for byte: only `browser_batch_interact` missing |
| MCP other views | equal minus `browser_batch_interact` and the tools with no command (C7, C20, C25) |
| browser-use, every view | as before plus both probes (C7); `browser_find` and `browser_recall_offload` identical |

### Verification

- Fail-before: each new test was run against the code before its feature commit in a separate worktree (no
  stash): items 1–3 26 failures (the helper test module did not import), item 4 17, items 5–6 9, item 7 2 unit
  failures and, live, the three upload / drop conformance cases refused with `File access denied`.
- `surfaces/test_browser_helpers.py` (+11): flags, `ToolContext.execute` / `observe`, probe records (result,
  failure, unchanged model text), recall over the context's workspace and language, find records (observed, reused,
  failed), find observing again after a command without an observation and after a new generation.
- `test_tool_factory.py` (+3): workspace and language reach extra tools; a point on an older screenshot never
  reaches the driver; a dispatched failure outdates the observation.
- `test_browser_page_state_record.py` (+4): freshness, `note_dispatched`, older generation, screenshot generation.
- `drivers/playwright_mcp/test_tools.py` (+1), `drivers/playwright_mcp/test_driver.py` (+3),
  `drivers/test_browser_use_driver.py` (+3, 6 updated), `drivers/test_browser_use_sidecar.py` (+8: every navigation
  reply with and without a dialog), `snapshots/test_tool_factory_golden.py` (core 17 / 18).
- `pytest tests/unit_tests/harness/tools/browser_move`: **2372 passed, 117 skipped, 2 xfailed**.
- Live: Playwright MCP conformance **51 passed** (without unrestricted file access), browser-use conformance
  **45 passed**, helpers **4 passed** (new: `activate_page` fronts a tab by its address on browser-use).

### Known leftovers

1. `browser_batch_interact` (next helpers task) makes `core` 18 / 18; it should run its steps through
   `ToolContext.execute` and declare its flags. Done: see "Batch and custom actions over commands".
2. No rail reads `ToolFlags` yet; the switch moves `_is_browser_progress_tool`, `_is_replan_exempt_tool` and the
   state-change checks onto them.
3. The recall tool leaves no record; a rail sees its message as a call that sent nothing.
4. Staged upload copies stay under `output_dir/uploads/`. Done: `stop()` removes them; see "Batch and custom actions over commands".
5. browser-use's `current_tab` is the first tab whose URL is the page URL; two tabs at one URL are not told apart.

## Agent construction, instance settings and restart on a dead device

Phase 4 item 6 and the composition-root part of plan section 3 ("One composition root"): `build_agent_parts` and
`build_agent` in `assembly.py`, `BrowserInstanceConfig` applied to the surface settings before anything is built,
per-effect timeouts from the profile, and the two restart follow-ups of the wave-2 review (N1 liveness before a
restart, N2 a new driver for a re-provisioned device). The fake-surface end-to-end test of plan section 4.3 runs
on it. Only tests reach `build_agent`; `create_browser_agent`, the service and the runtime are unchanged.

### Decisions

1. **The provisioner answers liveness** (N1): `Provisioner.is_alive(device)`. Managed and remote probe
   `<endpoint>/json/version` (the check `is_endpoint_ready` already did, now one module function); a device without
   an endpoint (extension; remote without `cdp_url`, where the driver launches its own browser) is always alive,
   because provisioning it again returns the same empty device.
2. **Restart order:** `stop()` the old stack in every case, then ask the provisioner. Alive → `start()` the same
   stack. Dead → release, provision, rebuild, start the new stack. A live device whose stack cannot start is a task
   failure: the former fallback "re-provision when `start()` fails" is gone, since a new Chrome does not fix a driver
   that cannot start, and on Playwright MCP `start()` succeeds over a dead Chrome anyway.
3. **The assembly rebuilds** (N2): `DriverAssembly.rebuild(device)` is the same closure that built the first stack
   (driver factory, surface check, middleware with the profile timeouts), so stacks are built in one place and the
   session holds no registry. The session swaps stack and device in its assembly; `close()` stops and releases the
   new ones. A failing rebuild releases the new device before the task fails.
4. **`DriverAssembly` grows by appended defaults** (`rebuild=None`, `surface=None`); `surface_settings` stays.
   Assemblies built by hand in the factory, helper and golden tests keep their four positional arguments. The tool
   factory tests' hand-built assembly now has a provisioner that answers alive (decided with Aleh in-session); the
   other hand-built assemblies never restart.
5. **The instance is applied before provisioning and the driver factory:** `assemble_driver(..., instance=)` calls
   `surface.settings.with_instance(instance)` and puts the result into the bundle and into `profile.settings` (the
   tool context reads the profile's). `BrowserSettings.with_instance` holds the rules of the browser service's
   `_provisioning_settings` plus the two Playwright MCP overrides (`launch_args`, `cdp_url`), so
   `test_browser_instance_isolation.py` semantics hold on the new path (distinct keys → distinct free ports and
   profiles, explicit port and profile name win, no instance → environment values).
6. **The core passes the instance through untyped** (`Any`); `with_instance` is a method of the browser settings,
   not of the `SurfaceSettings` protocol, so `contract/` is unchanged.
7. **No key in the MCP server name** (Q2): the Playwright MCP driver factory hands `playwright_mcp_config` only the
   device endpoint, never the instance, so the server name and the tool prefix stay `mcp_playwright-official_` for
   keyed instances. The old path's `build_playwright_mcp_config(instance)` still suffixes, until the switch.
8. **Per-effect timeouts from `profile.timeouts`** (I2's one line, now inside the stack builder). The browser
   profile says 240 s for every effect, today's default; `PLAYWRIGHT_TOOL_TIMEOUT_S` no longer bounds driver calls
   made through the root (it still bounds the worker's tool calls on the old path). An effect the profile leaves out
   runs unbounded.
9. **`build_agent_parts(session, bundle, *, capabilities, image_support, language, workspace=None)`**: tools and
   prompt sections from `ToolFactory.build` (workspace and language go to the tool context); rails = `ActionPolicy`
   (the factory's builders, `profile.policy_rules`, the session's page state), `CommandRepeatGuardRail`,
   `CommandInterceptedHintRail`, then each `profile.rails` factory called with the `ToolContext` of the same session.
   The factory does not return its context, so the root builds an equal one.
10. **`build_agent` is async and returns `(agent, session)`.** It starts the driver before the agent exists (the MCP
    native tools appear at `start()`); the caller owns `begin_task()` and `close()`. A failing construction closes
    the session. The system prompt is the caller's text followed by the prompt sections, blank-line separated;
    every other keyword goes to `create_deep_agent`.
11. **`DriverSession.page_state`** is readable, so the parts can be built from a session alone. `driver_session.py`
    imports `DriverAssembly` for annotations only (the root now imports the session through the tool factory).
12. **The fake surface writes its own driver** (a `HandlerDriver` with `@handles(Click / Poke, refs={PointRef})`), not
    `contract.testing.FakeDriver`: the plan derives the handled commands from the marks, and per-command scripted
    outcomes make the scenario readable. The model is a `Model` subclass with a scripted `invoke`; the agent is a real
    `DeepAgent` from `create_deep_agent`. The stale point comes from the page state: a click whose result changes
    the context advances the generation, and the next point still carries the generation of the earlier screenshot.

### Rejected options

- **Liveness from `driver.health()`**: the MCP server answers with Chrome gone; the provisioner knows the device.
- **Rebuilding from the registries inside the session**: a second composition root (the reason it was rejected in
  "Page state, driver session and policy rail"); the closure keeps one.
- **Keeping "re-provision when `start()` fails" next to the probe**: two triggers for one decision, and the probe
  already covers the case.
- **Treating an assembly without a provisioner as alive** (to leave the tool factory test untouched): a branch for a
  state only hand-built tests create.
- **`with_instance` on the `SurfaceSettings` protocol**: a contract change for one surface's identity type.
- **Registering `session.close` as the deep agent's task-resource cleanup**: that runs after every subagent task and
  would stop the browser between tasks.
- **Returning only the agent**: the caller could neither close the device nor start a task.
- **The Phase 3 per-effect bound `core.tool_timeout_s`**: the plan puts timeouts in the profile so a slower surface
  needs no core edit.

### Fake-surface end-to-end test

Six assertions on one scripted run (`look`, an off-board `poke`, `click`, a `poke` on the old screenshot, `look`,
`click` with a lost connection, then an answer):

| Plan item | What the test sees |
|---|---|
| tool list | the model is offered `look`, `click` and `poke`; `click` and `poke` take `x, y` integers (schemas generated from `refs={PointRef}`) |
| image block | the request after `look` carries the screenshot as an `image_url` (the renderer's `Screenshot` block) |
| policy | `poke(500, 5)`: tool result `PolicyDenied`, the model reads `### Error\n(500, 5) is off the board`, no `Poke` reaches the driver |
| stale point | `poke(10, 10)` on the g0 screenshot after the generation moved to g1: exactly one `Observe()` and no `Poke` on the driver, record error `StaleRef`, the model reads the error with the fresh observation |
| lost connection | the driver sees `stop`, `start` once after the failing `click` (the device is alive, nothing provisioned again), the model reads `driver_restarted` |
| registries only | surface, driver and provisioner are registered in test-scoped `Registry` objects; no package file was changed for the test |

**No seam needed a change:** nothing in `playwright_runtime/` besides the two files of this task, nothing in
`contract/` or `drivers/middleware.py`.

### Verification

- Fail-before (old `driver_session.py` in a separate worktree, new assembly and tests): 6 of the 12 session tests
  fail, among them the lazily starting driver over a dead device.
- `test_driver_session.py` (12, rewritten around `is_alive` and `rebuild`), `test_assembly.py` (+4: rebuild, a
  rebuild that does not serve the surface, the instance before provisioning and the driver, the plain MCP prefix for
  a keyed instance; the timeout cases now come from the profile), `test_provisioning.py` (+4 functions, 5 cases:
  liveness against a local CDP stub, a closed port, a managed device, devices without an endpoint),
  `surfaces/test_browser_settings.py` (+7: `with_instance`), `test_build_agent.py` (6),
  `architecture/fake_surface/` (6), `test_tool_factory.py` (hand-built assembly with a live provisioner).
- `pytest tests/unit_tests/harness/tools/browser_move`: **2393 passed, 117 skipped, 2 xfailed** (architecture,
  old goldens, factory goldens and the runner contract included); the 12 harness test files outside it that use the
  browser agent: **262 passed**.
- Live: `test_assembly_live.py` **3 passed** (managed Chrome with browser-use and with Playwright MCP through the
  root, and new: a managed Chrome killed under Playwright MCP is detected dead, provisioned again, and the next
  observation runs on a rebuilt driver); browser-use conformance **45 passed**.

### Known leftovers

1. **browser-use does not report a dead Chrome as `ConnectionLost`.** Killing the managed Chrome under browser-use
   makes the next `observe` fail with a plain `DriverError` ("CDP still not connected"), so the session never
   restarts. The driver's error mapping needs the connection case (checked live, not in a test). Done:
   see "Batch and custom actions over commands".
2. A `StaleRef` raised by a driver reaches `CommandTool` with the fresh observation on the error
   (`RetryOnStale`); the tool passes `observation=None` to `renderer.error` and does not record that observation in
   the page state. The fake surface goes through the page-state path, which records it. Done: see "Batch and custom actions over commands".
3. `build_agent` registers no task hook: `begin_task()` and `close()` are the caller's until the switch wires the
   facade's acquire/release.
4. `BrowserInstanceConfig.driver` is not read by the root; `core.driver` picks the driver.
5. `DriverAssembly.surface_settings` duplicates `surface.settings`; it stays while hand-built assemblies pass it
   positionally.

## The switch, step 1: the main agent on the driver session

The first of the three switch sessions (design sections 1(c), 1(d), 2, 6 and 7; decisions O5, P8, Q14 and S1 of
wave 6; `switch-plan.md` S1), on the branch `p4-switch`. `create_browser_agent` keeps its signature; the agent it
builds runs on a `DriverSession` through the composition root, the factory tools and the record rails, and the old
MCP path of `runtime.py` is gone. The WebVoyager worker (`service.py`, `agents.py`) still runs the old path until S2.

### Decisions

1. **The facade keeps its signature and owns the session.** `BrowserAgentRuntime.ensure_runtime_ready` calls
   `assemble_driver` with the given `CoreSettings` (else the process environment) and the instance, makes the
   `DriverSession` and a `ToolContext`, and registers `CUSTOM_ACTIONS` on the controller. `mcp_cfg` stays a
   constructor argument for the worker path; honouring it on the session is S2 (Q11). Two keyword-only arguments
   were added, `workspace` and `language`, because the recall tool and the tool context need them.
2. **Everything arrives at the first invoke.** The design put the extra tools at construction; `browser_find` takes
   its description from the driver's native tools, which exist only after `start()`, so the factory build (command
   tools, extra tools, rails) runs in `BrowserRuntimeRail.before_invoke` where the MCP tools were added before.
   `create_deep_agent` gets no browser tools; a caller listing tools before the first invoke sees its own only.
   The `core` golden is byte-identical, which is what the rule protects.
3. **The parts' rails register dynamically**, through `agent.register_rail`, once per agent. The main agent thus
   runs `ActionPolicy`, `CommandRepeatGuardRail` and `CommandInterceptedHintRail` as `build_agent_parts` returns
   them; the design table named only `ActionPolicy` for the main agent, `build_agent_parts` (P4-T6) decided
   otherwise, and one list serves both topologies.
4. **`ToolKind`**: what the rail knows before a call. It decodes the command with the factory's builder (one more
   decode next to `ActionPolicy`'s and the tool's; all pure) and carries the schema's field names; after the call the
   record's action and effect replace it. The classifiers (phase, action class, intent, read-only recovery, replan
   exemption, progress, bounds, page-state attachment) read the kind; the strategy signature keeps the tool name
   as a label so direct and batch clicks still compare equal.
5. **Capabilities from the allowlist.** The factory selects by capability entry; the facade derives the entries
   whose tools are all in `allowed_tool_names`, the inverse of `resolve_browser_capabilities`, so the runner's
   `allowed_tool_names=CORE_BROWSER_TOOL_NAMES` still means `core`.
6. **Argument normalisation by dialect.** Only a tool whose schema addresses elements by `target` (the MCP dialect)
   gets the ref aliases, `[ref=e12]`, `target_id` and `generation_id` rewrites; the browser dialect decodes
   `target_id` itself and needs its `generation_id`. The card primary-link rewrite is gated on the decoded `Click`.
7. **`field`, `fields` and `element` are dropped by schema.** All 69 server schemas of 0.0.78 forbid extra
   properties, and the rail validates arguments against them; the keys are dropped only where the schema lacks them
   (the evidence hints are read first). For `browser_drag` this also drops `element`, which the old path forwarded.
8. **Observation reuse (Q14):** `capture_browser_state` takes `page_state.fresh_observation` and observes only
   otherwise; the free observation of the last mutating command is what the next capture reads. `observations_reused`
   counts them for P4-T9. The metadata script runs after the observation and outdates it for `browser_find`, which
   is fine: the next capture follows the next mutation, whose own free observation is fresh again.
9. **Capture errors.** A `DriverError` on the observation becomes `dom_error` (`page observation failed: …`); the
   session's task failure after a second lost connection propagates instead of being folded into the state.
10. **The core imports the browser helpers.** `runtime.py` imports `CUSTOM_ACTIONS`, `probe_for_policy`,
    `activate_page` and `page_metadata` from `surfaces/browser/helpers`, as the switch plan says, against plan
    invariant 2 (core never imports a concrete surface). Phase 9's done-criterion is where the invariant must hold
    ("`runtime/` never imports `surfaces/` except through the registry"); the import-boundary allowlist carries the
    one entry (count 4) with that reason. The alternative, a hooks slot on `SurfaceProfile`, is a contract change
    for Aleh to decide.
11. **URLs come from the record.** `_update_last_page`, `_evidence_variant_and_url` and the navigation completion
    evidence read `url` from the record's surface context (`extract_result_url` is gone with the text parsing).
12. **A partial batch with a successful mutating step still counts as a state change** in the rail (the batch
    record says `ok=False`, so its metadata alone would not refresh the browser state).
13. **The old goldens of the seven views with no-command tools are regenerated** (unsafe_dev, vision, devtools,
    config, network, storage, testing): exactly the C7/C20/C25 removals; `core`, `runner`, `advanced_code`,
    `extended_interaction` and `pdf` are byte-identical. The factory golden test compares byte for byte again and
    keeps the removed names as the record of the decision.
14. **Batch typing on a driver without key-by-key typing (S1):** `TypeText(slowly=True)` refused with
    `Unsupported(dispatched=False)` is sent again as `TypeText(clear=False)`, in the single-step path and the loop
    (where the field was emptied first). A refusal the driver dispatched is not retried.
15. **`AgentParts.builders`** (appended, default empty) hands the factory's decoders to the runtime.

### Rejected options

- **Extra tools at construction with a lazily started session**: a `ToolContext` needs the started driver, and
  `browser_find`'s native description is byte-identical only from the driver's tool list.
- **Keeping the name-based classifiers with prefix stripping**: the point of the switch; the kind carries the
  command type and the effect on both dialects.
- **Normalising `target_id` for every tool**: breaks the browser dialect, whose decoders take `target_id` and need
  `generation_id`.
- **Dropping `element` everywhere**: `browser_click` declares it; the schema decides.
- **A hooks slot on the profile for the policy and metadata helpers**: a contract change outside this session's
  mandate; recorded as the open question.
- **Replacing `inputs.tool_result` with a dict in `after_tool_call`** (the old normalisation): the record rails
  after this one need the `ToolOutput` with its record.

### Golden status

| View | Result |
|---|---|
| `core`, `runner`, `advanced_code`, `extended_interaction`, `pdf` | byte for byte |
| `unsafe_dev`, `vision`, `devtools`, `config`, `network`, `storage`, `testing` | regenerated: the 43 no-command tools of C7/C20/C25 removed, the prompt unchanged |
| runner contract (`test_wv_runner_contract.py`) | green |
| factory goldens | `core` 18/18, `runner` 14/14, every view byte for byte |

### Verification

- `uv run pytest tests/unit_tests/harness/tools/browser_move -q` on `p4-switch`: **2406 passed, 123 skipped,
  2 xfailed** (architecture, old goldens, factory goldens and the runner contract included), before the batch
  fallback; `surfaces/test_browser_batch.py` 56 after it.
- The ten harness test files outside `browser_move` that build the browser agent: **234 passed**.
- Live: `test_assembly_live.py` 4 and Playwright MCP conformance 51 (**55 passed**); browser-use conformance
  **45 passed**; live helpers on both drivers after the batch change **8 passed**.
- Deleted tests, each covered elsewhere or testing removed behaviour: the canonicalisation of tool names (gone);
  the MCP text classifier, the RPC envelope and `extract_result_url` (records carry ok, error, value and surface);
  the runtime's probe, batch, code-executor and MCP lookup cases (`surfaces/test_browser_helpers.py`,
  `surfaces/test_browser_batch.py`); the single-step batch primitive through the client and rail; the controller's
  LLM-fallback and script-builder cases (not ported, C28); the status-logging test that read the batch script.

### Known leftovers

1. S2: the worker still builds on `mcp_cfg` through the service (`ensure_started`, heartbeat, client patch);
   `runtime_tools.py` helper classes call deleted runtime methods and serve only the worker's tool cards until the
   worker gets the factory tools; `test_browser_runtime_tools.py` cases that invoke them through a mock runtime
   still pass. `runtime_health` reports the service's flags, not `session.health()`.
2. S3: the context processors and `status_logging.py` still key on tool names; the old rail classes and their
   tests stay next to the record rails; the client patch is still called in the facade constructor (row 4 of the
   P5-T1 inventory).
3. A `browser_settings` given to the facade reaches the service only; the session's surface settings come from
   the process environment through the surface registry.
4. The core import of the browser helpers (decision 10) is allowlisted until Phase 9.
5. `release_task_resources` keeps the session (and its browser) across tasks; the old path dropped the MCP
   registration between tasks.

## Batch and custom actions over commands

The last helpers task before the switch (design section 6 and Q7, Q8; phase-0 decisions C28, C29; wave-5 review R1,
R5 to R8): `browser_batch_interact` becomes an extra tool that runs its steps as commands, the three Playwright-only
custom actions become functions of the tool context, and four small fixes land. The old agent path (runtime, service,
agents, controller, runtime tools) is unchanged; it still serves the agent until the switch.

### Decisions

1. **One step, one command** (design section 6 table). A step that maps to a command runs as that command on the
   target's own ref: the `DriverRef` of a snapshot target is used as is (the old path first stamped it with
   `browser_evaluate`), a probe target is `Locator(css)`. A card's primary link clicked alone is `Navigate`. Steps
   with no single command (extractions, `set_checked`, `autocomplete`, the other waits, `press` with a locator) take
   the loop, as the old path fell back to the script.
2. **Several steps run as a Python loop** (Q7): snapshot targets are stamped first, one page script checks every
   target before anything acts (preflight), and each step locates its element again with one page script that
   stamps it with a fresh mark, then acts on `Locator(css='[data-openjiuwen-target-id="<mark>"]')`. Re-locating at
   each step keeps what the Playwright locators did (they were lazy queries, re-resolved on each action), at one
   page script per step.
3. **Locator strategies in page JavaScript**: CSS, and stand-ins for Playwright's `getByRole` (explicit and implicit
   roles, accessible name from `aria-labelledby`, `aria-label`, labels, placeholder, alt or text; hidden elements
   left out), `getByLabel`, `getByPlaceholder`, `getByTestId` (`data-testid`) and `getByText` (the innermost elements
   whose text matches), with `exact`. Strictness as before: more than one match fails at once.
4. **A missing or hidden target is waited for within the step timeout** at every check. The old script failed at
   once on zero matches and waited only for visibility; the loop polls both, because a target can appear after an
   earlier step.
5. **`type` and `autocomplete` empty the field first** (`Click`, `TypeText("")`, then `TypeText(slowly=True)`):
   typing key by key does not clear on Playwright MCP (`pressSequentially`), and the old script cleared with
   select-all and Backspace. `fill` is one `TypeText`.
6. **Waits**: `wait_for_url` and `wait_for_tab` poll `Observe` (the tab list and addresses are in
   `BrowserContext`) and switch with `SwitchContext`; the other waits poll one page script each, with the old
   `stable_ms` rule. `sleep` is `Wait(seconds)`; the pauses after a step stay local sleeps.
7. **Validation, stale-target refresh, preflight and the result keys are the old ones.** The controller's step
   checks and the runtime's target contract run in that order, so the first error the model reads is unchanged. The
   card and step contract live in `batch_steps.py`, the runner in `batch.py`: one module would pass the 1,500-line
   limit. The read-selector check of the old runtime (exactly one visible match) is part of the preflight.
8. **The batch record** (R6): one `CommandRecord` for the call, `command` and `action` of the last command sent,
   `effect` the strongest of what ran with page scripts and observations counted as `READ` (they only find, mark and
   read) and the order `READ` < `SCRIPT` < `ACT` < `NAVIGATE`, `ok` as the payload says, `dispatched` and
   `context_changed` from any command, `value` the payload. The tool declares `ToolFlags(effect=ACT)`.
9. **A lost connection ends the batch** even with `continue_on_error`: the page state was reset by the restart.
10. **The batch card keeps "MCP/Playwright-style" in one property description**: the card is golden byte for byte,
    so `batch_steps.py` is allowlisted in the driver-name scan with that reason (decided with Aleh).
11. **Custom actions** (Q8, C28): `CUSTOM_ACTIONS` maps the controller names to functions of the tool context with the
    controller's parameters, aliases, error texts and result keys; the switch registers them on the controller.
    Coordinates are one page script (`querySelector` and the bounding box, else the element with the visible text);
    drag stamps both elements and sends `Drag` between the stamps, or between two points of the last screenshot;
    file inputs get `UploadFile` on the element when the driver accepts a `Locator` for it (browser-use), else a
    `Click` that opens the chooser and `UploadFile(None, paths)` answering it (Playwright MCP, which stages the
    files). The choice reads `driver_info.actions`, so no driver is named. Script values are read as values: the
    executor path no longer reads the result envelope (F11).
12. **R1**: an unclassified browser-use sidecar error that says `not connected` or `Reconnection wait timed out` is
    `ConnectionLost(dispatched=True)`. browser-use raises a plain `ConnectionError` from its watchdogs once its
    reconnect gives up, which the sidecar sends as the generic error code.
13. **R5**: `CommandTool` records the observation a driver's `StaleRef` carries and renders the recorded one.
14. **R7**: `message_metadata.executed` is `ok` for a `READ` record, `dispatched` otherwise.
15. **R8, narrowed**: the Playwright MCP driver removes on `stop()` the staging folders it created and then
    `uploads/` only if empty. The working directory defaults to the process directory, where `uploads/` may be the
    user's own folder; removing it whole, as R8 reads, was rejected with Aleh.

### Rejected options

- **One page script per batch** (the old shape): the design keeps one command per step so the rails, the middleware
  and the page state see each action.
- **Re-using the preflight stamps for execution**: a stamp is lost when an earlier step re-renders the element; the
  old locators were re-resolved too.
- **Stamping a snapshot target for a single step**: one command was the rule, and the driver resolves its own refs.
- **Typing `type` steps without clearing** (`TypeText(slowly=True)` alone): appends to a filled field on Playwright
  MCP.
- **Polling `wait_for_url` with `location.href` only**: it cannot see or switch to another tab.
- **Applying drag offsets and `steps` / `delay_ms`**: `Drag` takes two refs; the offsets only move the reported
  points and the two numbers are echoed.
- **Porting the LLM fallbacks** of the custom actions (`_build_run_code_task`): dead on 0.0.78, and they sent page
  code to the worker.
- **Rewording the card text** to drop "MCP/": the core golden would no longer be byte for byte.

### Step to command

| Step | One step | In a loop |
|---|---|---|
| `click` | `Click(ref)`; card link: `Navigate(href)` | locate + `Click(stamp)`, one retry on a transient error |
| `fill` | `TypeText(ref, value)` | locate + `TypeText(stamp, value)` |
| `type` (or `fill` with `mode: type`) | `TypeText(ref, value, slowly=True)` | locate + `Click`, `TypeText("")`, `TypeText(value, clear=False, slowly=True)` |
| `autocomplete` | loop | locate + as `type` (or `Click`, `TypeText` with `fill_first`), pause, locate option + `Click` |
| `select_visible_text` | loop | locate option + `Click` |
| `press` | without a locator: `PressKey(key)` | with a locator: locate and focus, `PressKey(key)` |
| `select_option` | values / value: `SelectOption(ref, values)` | locate (option value at `index`) + `SelectOption(stamp, values)` |
| `set_checked` | loop | locate + `FillForm((FormField(stamp, CHECKBOX, "true" / "false"),))` |
| `wait_for_text` | `Wait(until_text=text)` | page script polled (body text, or an element whose text equals it with `exact`) |
| `wait_for_selector`, `_load_state`, `_first_card_title`, `_sort_state`, `_result_count`, `_dom_text_change`, `_stable` | loop | page script polled, `stable_ms` where the old step had it |
| `wait_for_url` | loop | `Observe` polled over the focused tab, then the others from the newest; `SwitchContext` to a match |
| `wait_for_tab` | loop | `Observe` polled (tab count and match); `SwitchContext` unless `activate: false` |
| `sleep` | `Wait(seconds=ms / 1000)` | `Wait(seconds=ms / 1000)` |
| `extract_text`, `extract_value` | loop | locate and read `innerText` / `value` in the same page script |
| `screenshot` | `Observe(include_screenshot=True, full_page)` | the same |

### Ported and dropped batch tests

`surfaces/test_browser_batch.py` keeps the old names where the case still applies: rejects more than 25 steps,
rejects empty or non-list steps, validates the schema and the known bad cases before anything runs, the target
contract for mutations, the transient click retry, extraction provenance, partial results, compact condition
observations, `wait_for_tab` activating the new tab, `wait_for_url` switching to it, preflight rejecting a later
ambiguous target, the read-selector check; the Playwright form stub became the realistic booking flow over commands.
Dropped, with what they checked: the script text (`page.getByLabel`, `waitForTimeout`, fail-fast wording, the
retry loop source), the code executor call and missing executor, the compact-RPC envelope and its metrics, and the
drag script text (custom actions). The custom-action cases for the code executor and the LLM fallback go with those
paths; the fenced coordinate result, empty paths and the default file input are ported.

### Differences from the old path

- The model reads `execution_mode: "commands"` for a multi-step batch (was `compact_rpc`) and `metrics` with
  `command_count`, preflight and step times (was script build and transport numbers); one step reports
  `metrics.command` (was `tool_name`).
- A read-selector that matches more than one element fails as `batch_preflight_failed: ...` (was
  `batch_target_validation_failed: Read-only batch selector must match exactly one visible element ...`).
- `screenshot` no longer writes a file at `path`; it is an observation with a screenshot.
- `delay_ms` does not slow typing (no delay in `TypeText`); `wait_for_load_state` `networkidle` is `complete`.
- `session_id` / `request_id` come from the arguments only (the old tool also read the parent task's context).
- Custom actions: Playwright selector syntax other than `:has-text("...")` is not understood (CSS plus the text
  fallback); drag offsets and steps are not applied; error texts say `<action> failed: ...` instead of
  `browser_run_code failed: ...`.
- On browser-use `type` and `autocomplete` steps fail at the slow typing (`Unsupported`: the driver cannot type key
  by key) after the click and the clearing; `fill` works on both drivers.

### Golden comparison (factory alone, `image_support=False`, `language="en"`)

| View | Result |
|---|---|
| MCP `runner` | 14 / 14 byte for byte (the worker removes both probes, the batch and recall) |
| MCP `core` | **18 / 18** byte for byte |
| MCP other views | equal minus the tools with no command (C7, C20, C25) |
| browser-use, every view | as before plus both probes and the batch (C7) |

### Verification

- Fail-before (a separate worktree at the commit before the fixes, new tests copied in): R5 1 failure, R7 2, R8 2,
  R1 3. The batch and custom-action tests import modules that did not exist.
- `surfaces/test_browser_batch.py` (53), `surfaces/test_browser_custom_actions.py` (19),
  `surfaces/test_browser_helpers.py` (extra tools and flags), `snapshots/test_tool_factory_golden.py` (core
  18 / 18, the runner removing the batch), `architecture/test_driver_names.py` (one allowlisted card text),
  `test_tool_factory.py` (+3: the driver's stale observation, two read records), `drivers/playwright_mcp/test_driver.py`
  (+2, one changed to read the copy before `stop()`), `drivers/test_browser_use_driver.py` (+3).
- `pytest tests/unit_tests/harness/tools/browser_move`: **2473 passed, 123 skipped, 2 xfailed** (architecture, old
  goldens, factory goldens and the runner contract included); the 11 harness test files outside it that use the
  browser agent: **248 passed**.
- Live: helpers 8 (new: a fill, check, click, wait and read-back batch per driver on the scene; a file uploaded and
  an element dragged per driver), assembly 4 (new: managed Chrome killed under browser-use is found dead, provisioned
  again, and the next observation runs on a rebuilt driver), browser-use conformance 45, Playwright MCP conformance
  51.

### Known leftovers

1. The batch and the custom actions serve no agent yet: the switch registers `CUSTOM_ACTIONS` on the controller and
   drops the old batch path (the runtime tool, the controller action and its script).
2. `select_visible_text` cannot pass validation (it needs a `target_id` but takes no primary locator), as before.
3. The controller's own `browser_batch_interact` action (reachable through `browser_custom_action`) skips the
   runtime's target contract on the old path; nothing replaces it here.
4. `type` and `autocomplete` steps need a driver that types key by key (not browser-use).
5. `wait_for_url` and `wait_for_tab` observe at every poll (a full snapshot on Playwright MCP).

## The switch, step 2: the worker and the runner facade on the driver session

The second switch session (design section 7; decisions P1, R2, R3, R9, Q10, Q11, Q12, F7; the S1 review's T2, T3,
T4; `switch-plan.md` S2), on `p4-switch`. The WebVoyager worker runs on the same `DriverSession` as the main
agent, built from the factory; `BrowserService` holds that session and provisions nothing itself; the facade
serves the runner on both drivers.

### Decisions

1. **One session, owned by the service.** `BrowserService` takes the facade's `BrowserPageState` and opens the
   session in `ensure_runtime_ready` through `assemble_driver`; the facade asks the service for its session and
   builds its tool context on it. The S1 interim state (a session on the facade and an MCP server registered by the
   service, two servers during a runner task) is gone (T4).
2. **Task release stops the stack and keeps the device (T2).** `DriverSession.stop()` / `start()`: the last task
   binding stops the driver stack (the MCP server with its `--isolated` context, the browser-use sidecar); the next
   task starts it again, re-provisioning first when the device died. `begin_task()` runs in
   `acquire_task_binding`. The worker is built again after a release (its tools are bound to the session, which
   stays).
3. **The surface factory takes the instance and a settings override (R2, T3).** `factory(environ, instance=None,
   settings=None)`; `assemble_driver(..., settings=)`; the composition root stops calling `with_instance`. A caller's
   `browser_settings` reach the session, with the given `mcp_cfg` as `BrowserSettings.mcp_server` and the config's
   `cwd` as `mcp_cwd`.
4. **A given `mcp_cfg` is honoured (Q11) without changing the prefix (Q2).** The Playwright MCP factory keeps the
   given command line (`params`, `client_type`) under its own server name and id and the device environment.
5. **The instance's `driver` overrides `core.driver` (R3)** in `service.core`, which the facade uses to pick the
   topology.
6. **Worker tools and rails (R9, P1).** `build_agent_parts(..., worker=True)`: the `core` catalog tools, both probes,
   the batch and find; `browser_recall_offload` is flagged `ToolFlags(worker=False)` and stays main-agent only, so the
   runner's fixed removal list keeps the worker at the golden's 14 tools. Rails: `ActionPolicy`,
   `CommandRepeatGuardRail`, `CommandInterceptedHintRail` (the parts' order), then `FinalAnswerRecoveryRail`; the
   two strict xfails flipped. `build_browser_worker_agent` takes `tools` and no MCP config or allowlist.
7. **The browser-use facade is the main agent (Q12).** `ensure_started()` on `direct` builds the agent through
   `create_browser_agent(model, runtime=self, ...)` (new keyword: an existing facade to build on), the model from
   the facade's settings with the core worker temperature and top-p; `run_browser_task` runs it once under the
   guardrails' timeout and maps the answer (`ok`, `final`, `page` from the page state, `screenshot=None`, `error`,
   `status` / `progress` from the rail's terminal payload). Option B (phase-0 T4-Q1) is `create_browser_agent` plus
   `Runner.run_agent`, so the runner's browser-use path is that same agent, not a bare `build_agent` DeepAgent.
8. **Heartbeat, health (Q10).** The heartbeat loop, `_check_connection`, the client-patch call and the MCP
   registration are gone from the service; `runtime_health` reports `session.health()` and `started`; the MCP
   server's `browser_runtime_health` reads it.
9. **`shutdown()` closes the model clients (F7)** after the service: `OpenAIModelClient.aclose()`.
10. **The service no longer provisions.** `_ensure_managed_driver_started`, the profile-store adoption, the
    managed-driver bookkeeping and `_validate_mcp_command` are gone; the provisioner (through the root) provisions
    and adopts the profile with the settings' name. The registry keeps coordinating per identity: the session's
    provisioner is its managed driver, so `reset_managed_browser_runtime` still stops a preserved Chrome.
11. `runtime_tools.py` keeps only the cancel, clear-cancel, custom-action, list-actions and health tools; the probe
    and batch classes and `build_browser_runtime_tools` are gone (the factory's extra tools serve the worker).
    `capabilities_of` moved next to the catalog.

### Rejected options

- **Direct = `build_agent` without `BrowserRuntimeRail`**: no `<browser_progress>` protocol, no page-state
  context, and not the option-B agent the smoke compares with.
- **Per-agent extra-tool selection by name in the service**: a browser word in core and a name list to maintain;
  the tool declares it.
- **Keeping the service's own provisioning next to the root's**: two provisioning paths for one device.
- **Stopping the stack only when the registry's binding count drops** (the old MCP-binding rule): every service
  now has its own stack.
- **Closing the session between tasks**: the old path kept Chrome (T2).

### Behaviour changes to know

- An unkeyed service no longer adopts the store's globally selected live Chrome when its own profile name differs
  (the Phase 3 H1 order); the provisioner reuses the profile with its own name only, and the legacy store path
  (`~/.jiuwenclaw/browser-move/.browser/profiles.json`) is not read.
- `runtime_health` has `detail` instead of `last_heartbeat_ok`.
- The worker's helper cards come from the factory (`language="en"` as before); `browser_custom_action` and
  `browser_list_custom_actions` are still registered as runtime tools only.

### Golden status

| View | Result |
|---|---|
| every MCP view, prompts included | byte for byte (`test_tool_prompt_snapshot.py`) |
| runner contract (`test_wv_runner_contract.py`) | green: 17 tools before removal (14 core, probes, batch), 14 after, answer dict unchanged |
| factory goldens | `core` 18/18, `runner` 14/14 |

### Verification

- `uv run pytest tests/unit_tests/harness/tools/browser_move -q` on `p4-switch`: **2359 passed, 123 skipped,
  25 failed**; the 25 are the three old-rail test files S3 deletes (`test_browser_repeated_call_guard.py`,
  `test_browser_intercepted_action_hint.py`, `test_browser_final_answer_recovery.py`), which build the worker with
  the old signature and mock `ensure_runtime_ready` without a session. No xfail remains.
- The ten harness test files outside `browser_move` that build the browser agent: **234 passed**.
- Live: see the report (`.hq/reports/P4-T8-S2.md`).
- Rewritten under their names: `test_service.py` (the session in place of the managed driver),
  `test_browser_service_registry.py`, `test_browser_instance_isolation.py` (the instance through the surface
  factory and the provisioner), `test_browser_worker_allowlist.py`, `test_browser_prompt_tool_surface.py` (the
  worker from the service), the three records tests (`assemble_driver` patched instead of `ensure_runtime_ready`).
- Deleted tests: the service's MCP command validation, registration, refresh and heartbeat cases (no MCP
  registration, no heartbeat); its managed-driver and profile-adoption cases (the provisioner's, covered by
  `test_provisioning.py` and the surface settings tests); the runtime-tool probe and batch cases and the two card
  parity tests (the factory goldens pin the cards); `test_legacy_instance_still_adopts_selected_profile` (behaviour
  dropped, above).

### Known leftovers

1. S3: the context processors and `status_logging.py` still key on tool names; the old rail classes and their
   three test files remain (red on `p4-switch`); the client patch is still called in the facade constructor.
2. `main.py` lists the worker's tools through `Runner.resource_mgr.get_mcp_tool_infos`, which now returns none.
3. The registry's heartbeat-owner bookkeeping is unused (every service stops its own stack).
4. `OpenAIModelClient.aclose()` on `shutdown()` closes every cached client in the process; a host that keeps model
   clients alive across a facade shutdown rebuilds them on the next call.

## The switch, step 3: processors, logging, dead code, renames

The third switch session (design sections 1(e) and 8; decisions P1, P2, R4; the S2 review's U4, U5, U6;
`switch-plan.md` S3), on `p4-switch`. Nothing in the agent keys on a tool name or parses result text any more; the
old rails and the last client-patch consumers are gone; the browser-use golden is compared for real.

### Decisions

1. **The rail writes the effect onto the tool message.** `BrowserRuntimeRail.after_tool_call` adds `effect` (the
   `Effect` value) to the metadata of the surface's budgeted tools (factory tools and non-exempt helpers); a tool
   outside the surface, or an exempt one (`browser_recall_offload`), gets none. The 46-name refresh set and the
   4-name observation set of `BrowserStateContextProcessor` become "effect other than `READ`" and "`READ`"; the
   JSON content parsing of `success`/`ok`/`state_changed`/`denied` goes, metadata alone decides.
2. **The working context trusts the metadata** for tool errors (`success is False` → the payload's `error` or the
   text); the harness's own execution-error prefixes stay (they carry no metadata); the `ok`/`success`/`isError`
   markers and the `success=false`/`error=` scan go.
3. **The status log is keyed by command type.** `BrowserSubagentStatusLogger(command_of=)` gets the rail's decoder
   for `tool_start` (`RunScript` → code redacted, `Navigate` → URL without query) and reads the command record for
   `tool_end` (`kind="command"`, command, effect, ok, dispatched, error, text length, line count, script value
   kind). `browser_batch_interact` keeps its step summaries by name: it is the surface's own extra tool, not a
   driver name (design 1(c) row 24). The command summary carries no URL: that would put a browser word into core.
4. **Old rails deleted, new rails take the old names (P1).** `RepeatedCallGuardRail` and `InterceptedActionHintRail`
   on MCP text (call-log parsing, `### Events` masking, evaluate name checks) are gone; `CommandRepeatGuardRail` and
   `CommandInterceptedHintRail` are renamed to those names, the guard no longer a subclass. The three old test
   files are deleted; their records twins, which hold every old test name, take the old file names.
5. **The retry sentence is the surface's (P2).** `profile.messages["retry"]` = "Then retry the action (take a fresh
   browser_snapshot if the page changed)."; `InterceptedActionHintRail(retry)` and `explain_blocked_command(record,
   retry)` take it; `build_agent_parts` passes the profile's. The fake surface says "Then poke again.".
6. **`DriverAssembly.surface_settings` dropped (R4)**; the session re-provisions from `assembly.surface.settings`.
7. **Client patch consumer 4 gone**: the facade constructor no longer calls `ensure_browser_runtime_client_patch`;
   `test_heartbeat.py` (one test, the stdio client patch) is deleted. The patch itself (`browser_tools.py`) and the
   module-level copies in `main.py` and the MCP server entry stay for Phase 7 (J1, J3).
8. **Registry heartbeat ownership deleted (U6)**: `heartbeat_owner`, `is_heartbeat_owner`, `_live_owner` and
   `BrowserServiceRelease` (`close_mcp_binding`, `next_heartbeat_owner`) are gone; `activate_binding` and `release`
   return nothing. Every service stops its own stack on its last release, so nothing read them since S2.
9. **`main.py` lists the factory tools (U5)**: `runtime.agent_parts(image_support=False).tools` after
   `ensure_started()`, instead of `Runner.resource_mgr.get_mcp_tool_infos` (empty since S2); the `Runner` import
   goes. The listing is informational and stays.
10. **A given MCP config keeps the old env semantics (U4).** `create_playwright_mcp_driver` keeps the given env,
    drops `PLAYWRIGHT_MCP_DEVICE`, sets the CDP endpoint and defaults `PLAYWRIGHT_MCP_BROWSER` to `chrome` only when
    the device has an endpoint, as `_inject_cdp_endpoint` did; the surface-computed env no longer overrides a given
    `PLAYWRIGHT_MCP_BROWSER`.
11. **The browser-use golden is a real comparison.** The snapshot builder builds a view on browser-use
    (`build_view(view, workdir, driver="browser_use")`, `BROWSER_DRIVER` set, the sidecar transport stubbed with a
    blank page as the MCP transport is). `test_tool_prompt_snapshot.py` asserts, per capability view, the tools equal
    the 857239db golden except the one catalog's differences (Q3: run control, screenshot, close, drag and drop
    missing; the probes and the batch added; Q6: `browser_tabs` gains `new`) and the system prompt equals the
    Playwright MCP golden's (Q4: one prompt). The browser-use `runner` golden has no agents (the old runner never ran
    browser-use), so the runner view is not compared; the WebVoyager smoke on browser-use is P4-T9's.

### Rejected options

- **A processor rule on `state_changed` alone**: the rail sets it `True` for any unknown tool that ran (a file write
  on the main agent would refresh the browser state); the `effect` key marks the surface's tools.
- **Counting exempt tools (`browser_recall_offload`) as observations**: a recall-only turn would run the compact
  capture and advance the semantic no-progress count; they were in neither name set before.
- **Keying the status log by argument shape** (`code`/`url` keys): names the dialect instead of the command.
- **Keeping `_normalise_call_key`'s evaluate handling** for no-record calls: a call that decoded to no command has
  no script to mask; tool name and canonical arguments are enough.
- **Fixing the heartbeat wording of the `browser_runtime_health` tool description**: model-facing text of a runtime
  tool; left for a benchmark-phase prompt review.

### Behaviour changes to know

- The browser-state processor refreshes after the surface's non-`READ` tools only; `browser_custom_action`
  (a runtime tool the factory does not know, so it carries no `effect`) no longer triggers a refresh, where the old
  name set listed it. `GetConsole`/`GetNetwork` results (`READ` commands) now count as observations and run the
  compact merge; the old observation set did not list them.
- A helper's failed JSON payload is retained in the working context by its `error` field, as before, but only when
  the rail marked the message `success=False`.
- The status log's `tool_end` summary of a command tool changed shape: `kind="command"` with the record's fields
  instead of `{"kind": "dict", "keys": ["command", "content"]}` (which S1 had left it at).

### Golden status

| Check | Result |
|---|---|
| every MCP view, prompts included (`test_tool_prompt_snapshot.py`) | byte for byte, no golden touched |
| runner contract (`test_wv_runner_contract.py`) | green |
| factory goldens (`test_tool_factory_golden.py`) | `core` 18/18, `runner` 14/14 |
| browser-use views through the agent | tools = golden minus Q3 plus the helpers, tabs + `new` (Q6); prompt = MCP golden (Q4), all 11 capability views |

### Verification

- `uv run pytest tests/unit_tests/harness/tools/browser_move -q` on `p4-switch`: **2287 passed, 111 skipped**
  (whole suite green; the three old-rail files gone, 22 browser-use golden tests added, the 12 browser-use
  well-formedness tests no longer skipped). One timing test,
  `surfaces/test_browser_batch.py::test_a_condition_waits_until_its_value_holds_still_for_the_stable_time`, failed
  twice out of about ten runs ("no scripted answer left for `Wait`"; the stable-time loop polled once more on a slow
  run); untouched by this step.
- Architecture tests: the allowlists shrank for every removed entry (`intercepted_action.py`,
  `repeated_call_guard.py`, `service_registry.py`, `status_logging.py` driver names; `browser_state_context_processor.py`
  and `intercepted_action.py` MCP names; `runtime.py` lowered to its actual counts). No entry grew.
- The ten harness test files outside `browser_move` that build the browser agent: **234 passed**.
- Live: MCP conformance + live assembly + live helpers **63 passed**; browser-use conformance + live helpers + live
  assembly **56 passed, 1 failed** (the MCP half of the assembly test counted two OneDrive updater processes that
  started during its window as Chrome children; alone, both halves pass **4/4**).
- Rewritten under their names: `test_browser_state_context_processor.py` (effects on the tool messages; the 46-name
  case is the three mutating effects), `test_browser_subagent_status_logging.py` (commands and a record),
  `test_browser_runtime_rail.py` (the `effect` key), `test_browser_result_contracts.py` (a decodable mock runtime),
  `test_browser_service_registry.py`, `test_settings_passing.py` (`main.py` lists the facade's parts),
  `test_browser_profile.py` untouched (the new message is additive). Added: a working-context error from metadata, a
  command result summary, the args summary keyed by the decoded command, a given MCP env keeping its browser, the
  browser-use view comparisons.
- Deleted tests: the three MCP-text rail files (89 tests; every name lives on in the renamed twins);
  `test_registry_assigns_one_heartbeat_owner_and_transfers_it`; `test_heartbeat.py`.

### Known leftovers

1. `BrowserRuntimeRail._emit_status` logs a status-logger failure with `exc_info=True`, which
   `browser_agent_log_warning` does not accept; a logger exception would surface as a `TypeError` from the rail.
   Pre-existing, found when a test's mock runtime could not decode a command; not changed here.
2. `browser_runtime_health`'s card description still says "heartbeat status".
3. `main.py` and `playwright_runtime_mcp_server.py` keep their module-level client-patch copies; `browser_tools.py`
   keeps the patch and the public `register_browser_runtime_mcp_server` wrapper (Phase 7).
4. The browser-use `runner` golden has no agents: no golden pins the runner's browser-use (Direct) view.

## The switch, step 4: scoped snapshots

The fourth switch session, on `p4-switch`: the model's `browser_snapshot` `depth` and `target` reach the server
again, and a scoped snapshot is a partial observation of the page.

### Background

The MCP smoke after step 3 cost 60% more than its reference ($1.33 against $0.83 for the same three tasks). Every
`browser_snapshot` call decoded to `Observe()` and the driver always sent `{"boxes": true}`, so `{"depth": 1}` on
Allrecipes returned 56,933 characters where the old path returned 361. The model reads pages cheaply this way: in the
559-task baseline, 215 tasks (38%) passed snapshot arguments (`depth` 208, `target` 25, `filename` 6, `boxes` 2).
The Phase 0 decision to accept and ignore these arguments is reversed.

### Decisions

1. **`Observe` gains `depth: int | None = None` and `root: Ref | None = None`**; `scoped` is true when either is
   set. A scoped observation is the subtree under `root`, at most `depth` levels deep: partial by contract.
   `include_screenshot` and `full_page` stay as they were. The root is a ref like any other, so `page_state.check`
   refuses a scoped observe on a stale ref before the driver sees it.
2. **The MCP dialect decodes `depth` → `depth` and `target` → `root`**, the target with the same rule as every
   other decoder (`ARIA_REF` → `DriverRef`, else `Locator(css)`), the depth rounded like every number. `boxes`,
   `filename` and `element` stay accepted and ignored (baseline counts: `boxes` 2, `filename` 6; `element` is the
   model's own label of the target).
3. **The MCP driver sends a scoped snapshot exactly as the model asked, without `boxes`.** A full `observe()` keeps
   `{"boxes": true}` for the bounds a `PointRef` needs. The recorded fixtures show `boxes` adds only the `[box=…]`
   tokens (`snapshot_with_boxes` without them equals `snapshot_form_page` up to the frame counter), so the text
   would be the same either way; the model's arguments verbatim need no stripping and the answer is the server's
   byte for byte. Two fixtures were recorded with `@playwright/mcp@0.0.78` on the fixture page, `snapshot_depth_1`
   (`{"depth": 1}`, 442 characters) and `snapshot_target` (`{"target": "e3"}`, the navigation, 362 characters); the
   rendered golden of each equals its recorded `model_text`. A `PointRef` or a text `Locator` root is
   `Unsupported(dispatched=False)` before the call; the subtree's elements have no bounds.
4. **The page state treats a scoped observation as partial.** No generation check (a subtree against the last full
   observation would trip the Jaccard rule on an unchanged page), and `last_observation`, `fresh_observation`,
   `current_context`, `contexts` and the address are left alone; the refs it shows join the current generation and
   the refs it does not show stay valid. This is the old split: a model's `browser_snapshot` went through
   `register_ax_snapshot` (add), only the runtime's own capture through `replace_ax_snapshot`. So
   `capture_browser_state` observes afresh after a scoped snapshot unless a full observation is still fresh.
5. **browser-use refuses a scoped `Observe`** with `Unsupported(dispatched=False)` before anything is sent. The
   browser dialect's `browser_snapshot` schema has no `depth` or `target`, so nothing on that driver builds one; the
   refusal is for the contract.
6. **Model-facing schemas do not change.** The MCP `browser_snapshot` card is the server's own (it always listed
   `depth` and `target`); the browser dialect's has `include_screenshot` only. Every snapshot golden stays byte for
   byte.

### Rejected options

- **`boxes: true` on scoped snapshots too**, for the subtree's bounds: the text would be identical after
  `without_boxes`, but nothing reads the bounds of a partial observation (points come from the fresh full one), and
  the model's arguments verbatim are the simplest proof that the model reads what it read before.
- **A separate `ScopedObserve` command**: two READ commands for one server tool; the dialect, the factory and the
  renderer would all branch on it. Two optional fields and a `scoped` property do the same.
- **Recording a scoped observation as the page's observation**: the next full observation's element distance against
  a subtree would start a generation on an unchanged page, and `browser_find` and the compact capture would search a
  subtree.
- **Updating the address and title from a scoped snapshot's header**: a later full observation would then miss a
  navigation the model saw only through a scoped snapshot (the address backstop compares against the known one).
- **A conformance case "scoped observe returns only the subtree"**: not added, see Known leftovers 1.

### Golden status

| Check | Result |
|---|---|
| every MCP view, prompts included (`test_tool_prompt_snapshot.py`) | byte for byte, no golden touched |
| factory goldens (`test_tool_factory_golden.py`) `core` / `runner`, browser-use views | unchanged |
| tool-result goldens (`test_tool_result_golden.py`) | `snapshot_depth_1.txt` and `snapshot_target.txt` added, each equal to the recorded model text; the other 36 unchanged |

### Verification

- `uv run pytest tests/unit_tests/harness/tools/browser_move -q`: **2307 passed, 111 skipped** (2287 before this
  step). New: the contract's `scoped` property and `iter_refs` over the root; the dialect's `depth` / `target`
  decoding and the non-numeric `depth` error; the driver's four scoped-observe cases (depth alone, a `DriverRef`
  root, a css root with a depth, a `PointRef` root refused unsent); the page state's four rules (generation and last
  observation kept, refs added and kept, an outdated observation not made fresh, a stale root refused);
  browser-use's refusal; the facade's `capture_browser_state` reusing a full snapshot and observing afresh after a
  scoped one; the two fixtures in the well-formedness, decoder and result-golden suites.
- `fixtures/mcp/record_mcp_fixtures.py` records the two new cases right after `snapshot_form_page` (same page,
  same refs); re-recording `snapshot_depth_1` twice gave identical files.
- Live, `BROWSER_MOVE_LIVE_MCP=1`: MCP conformance + live assembly + live helpers **57 passed, 6 skipped** (the six
  browser-use-gated cases of those files, no sidecar interpreter set in that run). Live, `BROWSER_USE_SIDECAR_PYTHON`
  set: browser-use conformance **45 passed**.
- A scripted run through the factory's `browser_snapshot` on the live MCP scene (managed Chrome, `@playwright/mcp@0.0.78`):
  the full snapshot is 1,104 characters, `{"depth": 1}` 327 characters (the root's children, subtrees cut), and a
  `browser_click` on the "Save order" ref taken from the earlier full snapshot still succeeds afterwards (title
  `clicked`); the generation stays `g1` across the scoped snapshot and the click.

### Known leftovers

1. **No conformance case for scoped observations.** The suite decides what a driver handles from `info.actions`,
   which `HandlerDriver` fills from the `@handles` marks of `execute` handlers; `Observe` runs through `observe()`
   and is never listed there (the factory treats it as handled by every driver), and a `depth`-only scope carries no
   ref kind for `ensure_supported` to check. Listing `Observe` in `info.actions` would also make the suite's
   `_pairs` generate happy-path cases that `execute` an `Observe`. Saying "this driver scopes" needs a decision
   (`Observe` in `info.actions` with the suite skipping its happy path, a new `DriverInfo` field, or something
   else) before the case can expect `Unsupported` on browser-use and a subtree on Playwright MCP.
2. `depth` is rounded like every numeric argument (`round(float(depth))`); the server's schema says `number`, so a
   fractional depth from the model is rounded rather than refused.

## The switch, step 5: the server judges the refs the model sends

The fifth switch session, on `p4-switch`: on the Playwright MCP dialect a `target` the model sends carries no
generation, so the page state never refuses it before the driver; the server's own "not found" is the stale answer.

### Background

The MCP smoke after step 4 passed 3/3 at $1.28 against the reference's $0.83. Allrecipes and Amazon came in under
the reference; Google Flights cost $1.17 against $0.58. The dialect's `_target` stamped every aria ref and css
selector the model sent with `generation_of(target)`, the generation the page state last saw it in, and `check`
refused it with `Stale AX ref f1e149 belongs to g3; current generation is g4.` before the driver was called;
`CommandTool` then rendered the error plus a fresh full observation (about 18,000 characters). On a single-page
application the generation advances often (DOM churn, address changes), so refs the server would still resolve were
refused: six distinct refs in the Google Flights run, none in the reference run. The old worker sent refs straight to
`@playwright/mcp`, which resolves `aria-ref` and css against the live page and answers `Ref ... not found in the
current page snapshot` only when the element is really gone (the driver decodes that to `StaleRef(dispatched=False)`,
fixture `click_stale_ref`).

### Decisions

1. **Who owns staleness is a property of the dialect, written in the generation its decoder stamps.** The page
   state owns it for the refs it hands out or the model dates itself: `resolve`d targets, points on a screenshot,
   the browser dialect's `ref` / `selector` with the model's `generation_id`, browser-use index refs. The Playwright
   MCP server owns it for the aria refs and css the model sends on its dialect. S_20 says this once under Refs; the
   MCP dialect, which lives in the driver package, expresses it; no core code learns about MCP.
2. **`_target` builds `DriverRef(target, "")` and `Locator(css=target, text=None, generation="")`.** `check`
   already lets an empty generation pass (the rule a point used before the first screenshot), so the page state does
   not change. `PointRef` keeps `screenshot_generation`, the browser dialect keeps the model's `generation_id`,
   `resolve` keeps the target's generation. This reverses decision 1 of "Tool factory and Playwright MCP native
   tools" (a `DriverRef` at the generation the page state last saw it in).
3. **The server's refusal stays the driver-raised `StaleRef`.** `RetryOnStale` observes once and attaches the
   fresh observation; `CommandTool` renders the error with it and records the observation, as before. The model
   reads the same shape of answer as the pre-refusal gave, now only for an element that is really gone.
4. **Nothing downstream needed the generation of an MCP ref.** `RetryOnStale` reads only `dispatched`; `resolve`
   and the batch / find / custom helpers stamp `page_state.generation_id` themselves; the policy probe checks the
   command against the surface context; `CommandRecord.generation` and `message_metadata` come from the result and
   the page state, not from the refs. The factory's builder for a spec without a schema (`_generated_build`) still
   stamps `generation_of`; no MCP tool goes through it, and it was left alone.
5. **Two runtime-rail tests that asserted the pre-refusal on the MCP path were rewritten**, not deleted: after a
   navigation the click on the old ref now reaches the driver and the server's `StaleRef` renders with its fresh
   observation; after a URL change the click on the old ref reaches the driver and succeeds.

### Rejected options

- **Teaching `check` about the MCP dialect** (a flag on the page state or on `DriverInfo` that skips the check for
  aria refs): the page state would learn a driver's name, and the generation on the ref would say something untrue.
- **A forgiving page-state rule** (accept a ref one generation old): the server already does the only lookup that
  matters, against the live page; a forgiving rule would still refuse refs that survived two generations and still
  pass refs of elements that are gone.
- **Keeping the pre-refusal and shrinking the observation it renders**: cheaper, but still a wrong answer for an
  element that exists; the cost was the symptom.

### Golden status

| Check | Result |
|---|---|
| every MCP view, prompts included (`test_tool_prompt_snapshot.py`) | byte for byte, no golden touched |
| factory goldens (`test_tool_factory_golden.py`) `core` / `runner`, browser-use views | unchanged |
| tool-result goldens (`test_tool_result_golden.py`), `click_stale_ref` included | unchanged (the stale wording is the server's, the rendered error never carried a generation) |

### Verification

- `uv run pytest tests/unit_tests/harness/tools/browser_move -q`: **2309 passed, 112 skipped** (2307 before this step; one wall-clock batch test,
  `wait_for_stable`, failed once while the live suite ran beside it and passed alone). New: the dialect's
  target without a generation (an aria ref and a css selector, the resolver reporting an older one); the page state
  passing an MCP click and hover after `reset()`; the factory's `browser_click` reaching the `FakeDriver` after the
  generation moved (`Click(DriverRef("e1", ""))`, the record stamped `g1`) and the driver's `StaleRef` rendering with
  the observation it carries; the two rewritten rail tests. The `PointRef` pre-refusal test is unchanged and green.
- Live, `BROWSER_MOVE_LIVE_MCP=1`: MCP conformance + live assembly + live helpers **58 passed, 6 skipped** (57 before; the six browser-use-gated cases of those files). New live case
  (`test_browser_helpers_live.py`): a full snapshot, `browser_click` on "Open popup" (a second tab, the generation
  moves), `browser_tabs` `select 0` (moves again, nothing re-registered), then `browser_click` on the "Last button"
  ref from the first snapshot: the page state reports the ref in an older generation than the current one (the
  popup click's answer snapshot re-registered the first tab's refs once, the tab select did not), the click reaches the
  server and the title becomes `last`.
- Live, `BROWSER_USE_SIDECAR_PYTHON` set: browser-use conformance **45 passed**.

### Known leftovers

1. `_generated_build` (a spec without a schema) still stamps `generation_of`; no built driver serves an element ref
   through it today.
2. The live case proves the server resolves a ref the page state's generation moved past; it does not measure the
   smoke. The next MCP smoke tells whether Google Flights returns to the reference cost.
3. The import block of `test_browser_runtime_rail.py` was unsorted before this step (`ruff --select I`); it was left
   as found.

## The switch: verification baseline

The verification session of the switch, on `p4-switch` after step 5: the small fixes the step reviews left to it, the
Phase 4 done-when items with their evidence, and the numbers the merge is judged against.

### Decisions

1. **A status-logger failure stays inside the rail.** `BrowserRuntimeRail._emit_status` logged a status-logger
   exception with `exc_info=True`, which `browser_agent_log_warning(message, *args)` does not take, so the except
   branch itself raised `TypeError` out of the rail (step 3's leftover 1). The failure is now one warning line,
   `[BROWSER_SUBAGENT_ERROR] method=<hook> error=<text>`, and the invoke goes on; the helper's signature is unchanged.
2. **The runner's Direct agent writes to the process log.** The core logger sends every line of an invoke to
   `openjiuwen.browser_agent` while `BrowserRuntimeRail` holds the browser log context, which `before_invoke` entered
   for every invoke. So on the browser-use runner path the model client's `CompletionUsage(...)` lines and the
   agent's `tokens={...}` line ended in `browser_agent.log`, while the worker, which has no such rail, writes them to
   stdout, where the runner's `budget.py`, `analyze.py` and `run_one_task` read them. `BrowserAgentRuntime.dedicated_log`
   (default `True`) is `False` for the agent the facade builds for itself in `_direct_agent`, and the rail enters the
   context only when it is `True`. Nothing the model sees changes; `create_browser_agent`'s own agents are unchanged.
3. **The repeat guard starts at the first model call too.** A `DeepAgent` fires `before_invoke` and `after_invoke`
   on its outer callback context only; its inner `ReActAgent` runs the model and tool hooks on a context of its own,
   with another `extra` dict. `RepeatedCallGuardRail` kept its per-invoke state in `ctx.extra` from `before_invoke`,
   so on every deep agent (`build_agent`'s, and the main agent, which is the browser-use Direct path) the hooks found
   no state and the guard counted nothing, while the worker, a `ReActAgent`, was guarded. The live module caught it:
   five identical `browser_evaluate` calls through `build_agent` on Playwright MCP left the tools on the seventh
   request. The state now also starts in the first `before_model_call` that finds none (the first model call precedes
   every tool call, and the model-call context is the inner invoke's own), where the finish section is cleared as
   `before_invoke` does. The worker path is unchanged.

### Rejected options

- **Leaving the context around the model call only** (off in `before_model_call`, on in `after_model_call`): the
  context processors run after the before hooks and before the model call, so the browser capture and its raw audit
  would run outside the context anyway, and the agent logs its `tokens={...}` line after the after hooks, inside it.
- **A `Model` wrapper that drops the context inside `invoke`**: `create_browser_agent` copies the model into a plain
  `Model` for the browser temperature, so the wrapper would not survive construction.
- **`OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON` in the runner's launcher**: every browser line into the runner's
  stdout, not only the model calls, and a change outside `browser_move`.

### Behaviour changes to know

- On the runner's Direct path no raw audit artifact (`OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW`) is written: the audit needs
  the browser log context. Tool-call lines still reach the browser log through the hook, as the worker's do.

### Verification

- `test_browser_runtime_facade.py`: the Direct runtime reports `dedicated_log=False` and its rail's `before_invoke`
  leaves the browser log context unset; the default runtime enters it.
- `test_browser_repeated_call_guard.py`: a run whose `before_invoke` fired on another context is guarded from its
  first model call (tools withheld after the fifth identical call, a stale finish section cleared).

- `test_browser_runtime_rail.py`: a status logger that raises is logged with the hook name and the error and the
  rail returns.
