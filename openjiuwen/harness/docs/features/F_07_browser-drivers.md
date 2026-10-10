# F_07 Browser drivers

## Meta

| Item | Value |
|---|---|
| Type | feature |
| Date | 2026-10-10 |
| Scope | `openjiuwen/harness/tools/browser_move/` (`contract/`, `drivers/`, `provisioning/`, `surfaces/browser/`, `playwright_runtime/`, `controllers/action.py`, `utils/`, `playwright_runtime_mcp_server.py`), `tests/unit_tests/harness/tools/browser_move/` |
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
   logging hook into `openjiuwen.core` (plan Phase 4).
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

1. `browser_logging` reads the environment (decision 7).
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
2. Per-effect timeouts come from `CoreSettings.tool_timeout_s` until the surface profile supplies them.
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
   `ok=False`. The MCP refusal arrives as `DriverError(dispatched=True)` although nothing was sent. Phase 4 decides whether the runtime stages files into
   the workspace, or the server gets roots or the flag.
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
