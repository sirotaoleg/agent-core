# S_20 Browser Driver Contract

> **DRAFT.** Written during the driver-architecture refactor of `browser_move` (Phase 2). Sections are added as
> the phases land: the conformance suite (Phase 2), the composition root (Phase 3), the surface profile and the
> tool factory (Phase 4). Until the draft mark is removed, the code in `contract/` is the reference.

## Meta

| Item | Value |
|---|---|
| Type | spec |
| Modules | `openjiuwen/harness/tools/browser_move/contract/` (`observation.py` / `refs.py` / `actions.py` / `results.py` / `errors.py` / `driver.py` / `registry.py` / `surface.py` / `testing/`), `openjiuwen/harness/tools/browser_move/drivers/` (`__init__.py` / `middleware.py` / `playwright_mcp/snapshot.py` / `playwright_mcp/result.py` / `playwright_mcp/driver.py` / `playwright_mcp/clients/`), `openjiuwen/harness/tools/browser_move/playwright_runtime/assembly.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/page_state.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/driver_session.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/action_policy.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/config.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/browser_logging.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/service.py`, `openjiuwen/harness/tools/browser_move/utils/env.py`, `openjiuwen/harness/tools/browser_move/provisioning/` (`__init__.py` / `managed_browser.py`), `openjiuwen/harness/tools/browser_move/surfaces/` (`__init__.py`; `browser/`: `commands.py` / `context.py` / `config.py` / `profile.py` / `tools.py` / `renderer.py` / `capabilities.py` / `scene/`), `openjiuwen/core/common/logging/tool_call.py` |
| Last revised | 2026-10-10 |
| Related feature | [[F_07_browser-drivers]] |

## Scope

The contract is the only thing the agent core and a driver both know. The core sends commands and reads typed
results and errors; a driver translates commands for one library and declares which commands it handles. As of
this revision the browser-use and Playwright MCP drivers implement the contract and pass the conformance suite
live, the middleware wraps any driver and the composition root builds a started driver stack by name; no agent code
uses them yet.

The browser surface profile (tool specs, renderer, capability catalog) is specified under "Browser surface
profile"; the tool factory that turns it into model-facing tools gets its own section when it lands.

## Contract types

### Rules that hold for every type

1. **`contract/` imports only the standard library and itself.** No `browser_move` module, no third-party package.
   It contains no browser word (`browser`, `page`, `tab`, `url`) in names, strings or comments. Both rules are
   enforced by `tests/unit_tests/harness/tools/browser_move/architecture/`.
2. **Values are frozen dataclasses.** Mapping fields (`Element.extra`, `DriverInfo.versions` / `actions` /
   `tool_hints`, profile maps) use `field(default_factory=dict)` and are left out of the hash; no default is shared
   between instances.
3. **Surface-neutral core.** A field one driver would have to fake does not belong in `Observation` or
   `ActionResult`. Per-surface data lives in a `SurfaceContext` subclass owned by the surface.
4. **No module-level state** except the three default registries in `registry.py`.

### Observation (`observation.py`)

What `Driver.observe()` returns: a snapshot of the focused context.

| Type | Purpose |
|---|---|
| `Observation` | `context: ContextRef` (focused context), `generation` (stamped by the runtime's state tracker; drivers leave it `""`), `elements`, `screenshot`, `viewport`, `native_text` (the driver's own textual snapshot, shown or not by the renderer), `surface: SurfaceContext \| None`. Keyword-only. |
| `Observation.fingerprint` | 16-hex SHA-256 over the context id and each element's `(ref.ref, role, name)`. Generations, values, screenshots and the surface context are left out, so two observations of an unchanged scene match. A backstop for change detection; the primary signal is `ActionResult.context_changed`. |
| `Element` | `ref: DriverRef`, `role`, `name`, `value`, `bounds` (`(x, y, width, height)` in viewport pixels), `enabled`, `focused`, `extra`. |
| `Screenshot` | `png`, `width`, `height`, `scale` (image pixels per viewport pixel). `PointRef` coordinates are always in the screenshot's pixels. |
| `Viewport` | `width`, `height`. |
| `SurfaceContext` | Empty frozen base. The core passes it through to the surface's renderer and rails and never reads a field of it. |

### Refs (`refs.py`)

| Type | Purpose |
|---|---|
| `DriverRef(ref, generation)` | The model-facing element ref (`e57`). |
| `PointRef(x, y, generation)` | A point in that generation's screenshot pixels. |
| `IndexRef(index, generation)` | Driver-internal index; never shown to the model. |
| `Locator(css, text, generation)` | A selector and/or visible text. |
| `ContextRef(id, kind, title)` | A context the driver can focus; `kind` is surface-defined. Not an element ref. |

`Ref` is the union of the four element ref kinds; `REF_KINDS` is the same set as a tuple. One command takes several
ref kinds (one `Click`, not `ClickElement` / `ClickPoint`); which kinds a driver accepts is part of its
`DriverInfo.actions`.

`RefResolver` (protocol) is what a tool's argument decoder asks the runtime: `contexts` (the open contexts, in
order), `current_context` (`None` when unknown), `resolve(model_id, generation) -> Ref` (the ref behind a
runtime-minted element id; `ValueError` for an id it does not know) and `generation_of(ref) -> str` (the generation
a driver ref was last seen in; the current one for a ref never seen). The page state implements it.

### Commands (`actions.py`, `surfaces/<name>/commands.py`)

A command is a frozen dataclass subclassing `Action`, with a class-level `effect` (`Effect.READ`, `ACT`,
`NAVIGATE`, `SCRIPT`); middleware chooses timeouts and retry rules by effect.

**Where a command lives.** `contract/actions.py` holds `Action`, `Effect` and the commands that make sense on any
screen:

| Command | Fields | Effect |
|---|---|---|
| `Observe` | `include_screenshot=False`, `full_page=False` | READ |
| `Click` | `target: Ref`, `button="left"`, `modifiers=()`, `count=1` | ACT |
| `TypeText` | `target: Ref \| None`, `text`, `submit=False`, `sensitive=False`, `clear=True`, `slowly=False` | ACT |
| `PressKey` | `key`, `target: Ref \| None = None` | ACT |
| `Scroll` | `target: Ref \| None = None`, `dx=0`, `dy=0` | ACT |
| `Hover` | `target: Ref` | ACT |
| `Drag` | `source: Ref`, `destination: Ref` | ACT |
| `Wait` | `seconds=None`, `until_text=None`, at least one set: `seconds` alone sleeps; `until_text` waits until the text appears, at most `seconds` (`None`: the driver's default; a driver whose library has no deadline for a text wait may use its own), then raises `ActionTimeout` | READ |
| `SwitchContext` / `CloseContext` | `context: ContextRef` | NAVIGATE |
| `HandleDialog` | `accept`, `prompt_text=None` | ACT |
| `RunScript` | `code`, `args=None`, `sensitive=False`, `target: Ref \| None = None` | SCRIPT |

A surface defines its own commands as `Action` subclasses in `surfaces/<name>/commands.py`. The browser surface
has `Navigate(url)`, `Back`, `OpenContext(url=None)` (NAVIGATE); `SelectOption(target, values)`,
`UploadFile(target: Ref | None, paths=())`, `FillForm(fields: tuple[FormField, ...])` with
`FormField(target, kind, value, sensitive=False)`,
`Drop(target, paths=(), data=())` (ACT); `GetPdf(filename=None)`, `GetConsole(level="info", all=False)`,
`GetNetwork(static=False, filter=None)` (READ). Drivers handle both kinds the same way; the core does not care
where a command class is defined, and a new surface adds its commands (`Tap`, `Swipe`) without editing
`contract/`. A command is added only when a driver handles a tool the model uses today.

`sensitive=True` on `TypeText`, `RunScript` and a `FormField` means its text, code or value must never reach traces
or logs. Each of the three names its fields in a class-level `__sensitive_fields__` (`("text",)`,
`("code", "args")`, `("value",)`); `Tracing` masks the listed fields of any dataclass whose `sensitive` is true, so a new surface
command opts in by declaring the attribute and a `sensitive` field.

### ActionResult (`results.py`)

`ok`, `value`, `detail`; `context_changed` (set by the driver when a new document loaded, including a reload of
the same address, the address changed, a dialog opened, or a context was opened, closed or switched);
`generation` (filled by the runtime after the call); `observation` (a fresh observation the driver got for free,
so the runtime skips a second `observe()`); `source_context` and `opened_contexts` (context ownership);
`surface` (the surface context after the command). Keyword-only.

### Errors and the dispatched rule (`errors.py`)

Every error is a `DriverError` with `dispatched: bool`: whether the command may have reached the screen.

- **An error with `dispatched=True` is never retried automatically.** A retry may only follow an error raised
  before anything was sent.
- **Unknown means dispatched.** When the driver cannot tell, `dispatched` is `True`; an unrecognized library error
  becomes `DriverError(dispatched=True)`.
- `StaleRef` (the ref's generation is old) is raised only before dispatch and defaults to `dispatched=False`.
  Its optional `observation` is a fresh observation taken after the error (`RetryOnStale` attaches it; drivers
  leave it `None`). `Unsupported` (no handler, or a ref kind the handler does not accept) and `PolicyDenied`
  (raised by the security rail before anything is sent, never by a driver) also default to `False`.
- `ActionIntercepted(by=...)` (`by` names the element that would receive the input) defaults to `False`: every
  driver raises it before the input is sent, so it is not a `StaleRef` and is not retried either. The conformance
  suite asserts it.
- All others default to `True`: `ElementNotFound`, `AmbiguousElement`, `ActionTimeout`, `NavigationError`,
  `ConnectionLost`.

Every constructor accepts an explicit `dispatched=` to override the default.

### Driver (`driver.py`)

- `Driver` (ABC): `info -> DriverInfo`, and async `start(config)`, `stop()`, `health() -> Health`,
  `observe(Observe) -> Observation`, `execute(action) -> ActionResult`. The type of `config` belongs to the driver;
  its factory builds it.
- `DriverInfo(name, surfaces: frozenset[str], versions, actions, observes, tool_hints, native_tools,
  tool_name_prefix)`. `actions` maps each handled command type to the element ref kinds it accepts (empty for
  commands without refs). `surfaces` lists the surfaces the driver serves; the composition root refuses a driver
  that does not list the selected one. `observes` names the `Observation` fields the driver fills. `tool_hints` are
  optional per-command descriptions. `native_tools` (default empty) maps a tool spec name to a
  `NativeTool(description, schema, build)`: a tool the driver offers in its own format, with
  `build(args, resolver) -> Action` decoding its arguments. The model sees `tool_name_prefix` (default `""`) +
  the spec name. No built-in driver fills them yet.
- `@handles(Command, refs={DriverRef, ...})` marks a method as the handler of a command; marks stack.
  `refs` accepts only element ref kinds.
- `HandlerDriver(Driver)`: `execute` dispatches by `type(action)` to the marked method. `info` is
  `describe()` with `actions` replaced by the marks, so the supported commands cannot drift from the code.
  A command with no handler, or a ref of a kind the handler does not accept (also inside nested values such as
  `FillForm.fields`), raises `Unsupported(dispatched=False)` before any handler runs. Subclasses inherit handlers
  and may re-mark a command.

### Registries (`registry.py`)

`Registry[T]`: `register(name, factory)` refuses a duplicate name; `factory` is a callable or a lazy import string
`"pkg.module:attr"` that is imported on the first `create(name, *args, **kwargs)`; an unknown name raises
`KeyError` listing the registered ones; `names()` keeps registration order. A lazy string whose module does not
exist raises `ModuleNotFoundError` "<kind> '<name>' is registered but not built yet"; a missing module imported
*by* that module is raised unchanged. Three default registries — `DRIVERS`, `SURFACES`, `PROVISIONERS` — with
`register_driver`, `register_surface`, `register_provisioner`, so a driver, surface or provisioner can live in
another repository. Factory signatures: driver `factory(device, surface_settings) -> Driver`, surface
`factory(environ) -> SurfaceBundle`, provisioner `factory(**kwargs) -> Provisioner`.

Nothing is registered in `contract/`; the packages that own the built-ins register them with lazy strings and
re-export the registry, so `from ..drivers import DRIVERS` gives a registry with the built-ins in it:

| Package | Names | Factory |
|---|---|---|
| `drivers/__init__.py` | `browser_use` | `drivers.browser_use.driver:create_browser_use_driver` |
| | `playwright_mcp` | `drivers.playwright_mcp.driver:create_playwright_mcp_driver` (module not written yet: "not built yet") |
| `surfaces/__init__.py` | `browser` | `surfaces.browser.profile:create_browser_surface` |
| `provisioning/__init__.py` | `managed`, `remote`, `extension` | `provisioning.managed_browser:ManagedBrowserProvisioner` |

Importing the package, a registry package or the composition root imports no concrete driver, surface or
provisioner module and not `browser_use`.

### Surface types (`surface.py`)

Data types only; their use is specified with the composition root and the tool factory.

- `SurfaceSettings` (protocol): the settings a surface parses from its own variables. The core reads one attribute,
  `provisioning` (a provisioner registry name), and passes the object on to the provisioner and the driver factory
  unchanged. The core types surface settings with it and never imports a surface's settings class.
- `SurfaceBundle(settings, profile=None)`: what a surface factory returns. A surface without a profile returns its
  settings only; the browser surface returns both.

- `SurfaceProfile`: `name`, `commands`, `renderer: Renderer`, `tool_specs`, `native_tools`, `capability_catalog`
  (entry -> `(command, ref kind | None)` pairs; `None` is a call without an element ref), `capability_tools`
  (the same entries -> the names of the tools they select), `extra_tools`, `rails`, `policy_rules`, `timeouts`
  (per `Effect`), `messages`, `settings`, `scene`.
- `ToolSpec(name, commands, description, prompt_section, schema=None, build=None)`: one model-facing tool; it may
  cover several commands. `schema` is the JSON schema of its arguments; `build(args, resolver) -> Action` is the
  pure decoder (raises `ValueError` for invalid arguments). A spec without either is served only by a driver's
  native tool or a generated schema.
- `Renderer` (protocol): `observation(obs)`, `result(action, result)`, `error(action, error, observation)`, each
  returning a tuple of `ContentBlock`s; `ContentBlock` is `str` (text) or `Screenshot` (an image).
- `PolicyRule(command_type, check)`: `check(action, context)` returns the reason to deny, or `None`; `context` is
  the current surface context, `None` before the first observation.
- `Scene`: what the conformance suite runs a driver in — `surface`, `start` / `stop`, `enter()` (commands into the
  initial state) and the case builders `happy(command, ref_kind, actions)`, `stale(actions)`,
  `intercepted(actions)`, `timeout(actions)`, `context_changes` (name -> builder) and `sensitive` (name ->
  `builder(actions, secret)`, a surface command that carries the secret marked sensitive). A builder gets the
  driver's `info.actions` and returns a `SceneCase`, or the reason the scene cannot provide the case as a string; an
  omitted builder returns a generic reason. See "Conformance suite".
- `SceneCase(steps, check, observe_between, cleanup)`: the suite observes once, then each step builds one command
  from the observations taken so far; the last step is the command under test, the ones before it are setup steps.
  With `observe_between=False` no observation is taken between the steps, so every step sees the first one (for a
  setup step that opens a dialog, or one that changes the screen after the command under test has taken its refs).
  `check(before, after, result)` tells whether the command under test took effect; in a context-change case it is
  checked on an observation taken right after the command, before the cleanup.

### FakeDriver (`contract/testing/fake.py`)

A scripted `Driver` for tests: records every call as `Call(kind, payload)`, answers `observe` / `execute` from
queues in order (filled by the constructor and by `script(observations=..., results=...)`, which appends), raises
`Unsupported(dispatched=False)` for commands or ref kinds missing from its `info.actions`
(default: every shared command except `Observe`, all ref kinds; default surfaces `{"fake"}`), raises a scripted
`DriverError(dispatched=...)` or any exception via `fail_next`, and raises `ConnectionLost` from the n-th call
until the next `start()` via `lose_connection_on(n)`. Running out of script fails with `AssertionError`.

## Conformance suite (`contract/testing/conformance.py`)

A pytest plugin that holds invariants 6 and 7 of the plan: every driver means the same thing by the same command, and
a dispatched command is never repeated. It is surface-neutral; everything surface-specific comes from the `Scene`,
so a new driver or surface adds a test module and a scene, never an edit to the suite.

Use: a test module sets `pytest_plugins = ["openjiuwen.harness.tools.browser_move.contract.testing.conformance"]`
and subclasses `DriverConformance` as `Test...` with `scene` (not started; the plugin starts it once per class),
`driver_actions` (what the happy paths are parametrized with when tests are collected; for a `HandlerDriver`,
`handled_actions()`) and a `conformance_driver_factory` fixture returning `factory(sink) -> Driver` (a new driver
per case; `start()` must need no argument). Every case starts a new driver in its own event loop, runs `enter()`,
plays its `SceneCase` and stops the driver. The plugin registers the `live` marker for runs against a real driver;
`pyproject.toml` registers it too, because a test module that marks itself `live` before loading the plugin would
otherwise get `PytestUnknownMarkWarning`.

| Case | Passes when | Scene-dependent |
|---|---|---|
| surface | `scene.surface in info.surfaces` | no |
| declared actions | `info.actions` of the running driver equals `driver_actions` | no |
| happy path, one per `(command, ref kind)` of `info.actions` (`None` when the command takes no ref) | the command returns `ok=True` and `check(before, after, result)` holds | yes (`happy`) |
| stale ref | `StaleRef` with `dispatched=False` | yes (`stale`) |
| context change, one per `context_changes` entry | `result.context_changed`; with a `check`, an `observe()` right after the command (before the cleanup) passes it | yes |
| intercepted | `ActionIntercepted` with a non-empty `by` and `dispatched=False` | yes (`intercepted`) |
| timeout | the scene's case ends in `Wait(until_text=..., seconds=...)` for a text that never appears; it raises `ActionTimeout` with `dispatched=True`; a driver that retries into success fails here | yes (`timeout`) |
| unhandled command | the suite's own `UnhandledCommand` is not in `info.actions` and raises `Unsupported(dispatched=False)` | no |
| fingerprint | two `observe()` calls on the unchanged scene give the same `fingerprint` | no |
| sensitive text, trace, one per sensitive case: `type-text` and each `sensitive` entry | the secret leaves no text in the trace sink; `type-text` is `TypeText(sensitive=True)` built from the `TypeText` happy path; a case whose command does not carry the secret fails | yes (`happy`, `sensitive`) |
| sensitive text, logs, one per sensitive case | the same secret is in no log record of the process, captured at DEBUG including non-propagating loggers | yes (`happy`, `sensitive`) |

A case the scene cannot provide is skipped with the scene's reason; a case needing a command (or ref kind) the driver
does not handle is skipped naming it. Nothing passes silently. The trace case skips when the factory writes nothing
to the sink; the test modules build their driver as `Tracing(driver, sink)`, so it runs.

Live modules: `drivers/test_conformance_browser_use_live.py` (needs `BROWSER_USE_SIDECAR_PYTHON` and Chrome) and
`drivers/playwright_mcp/test_conformance_playwright_mcp_live.py` (needs the MCP command, `npx` by default, and
Chrome; `npx` fetches `@playwright/mcp@0.0.78` on first use). Both build the driver with its registry factory over
one headless Chrome with a fresh profile on a free CDP port, which the test starts and kills.

### The browser scene (`surfaces/browser/scene/`)

`browser_scene()` returns a `Scene(surface="browser")`. `start()` serves `scene/pages/` (`index.html`,
`second.html`, `data.json`; shipped as package data) with a stdlib HTTP server on `127.0.0.1:<free port>` and creates a temporary upload
file; `enter()` is `Navigate(index.html)`. Every element writes what happened to it into `document.title`
(`clicked`, `form:3:true`, `key:Enter`, ...), so checks read `BrowserContext.title`, `url` and `tabs`. Elements are
found by accessible name; a `Locator` uses the element's CSS id; a `PointRef` is the centre of `Element.bounds`
scaled to the screenshot. An `IndexRef` cannot be built (skipped).

- Happy paths: every shared command and every browser command (`Navigate`, `Back`, `OpenContext`, `SelectOption`,
  `UploadFile`, `FillForm`, `Drop`, `GetPdf`, `GetConsole`, `GetNetwork`) with `DriverRef`, `Locator` or `PointRef`.
- Stale ref: a `DriverRef` from the start document clicked after `Navigate(second.html)`.
- Intercepted: the "Covered button" is visible when the suite observes the page, so a driver whose observation drops
  occluded elements still gets a ref. A setup `RunScript` then calls the page's `cover()`, which puts an overlay over
  it, and the click uses the ref from that first observation (`observe_between=False`). Only with `DriverRef` or
  `Locator`; needs `RunScript`.
- Timeout: `Wait(until_text=<a text on no scene page>, seconds=2.0)`; needs `Wait`.
- Sensitive: `form-field`, a `FillForm` that fills the "Full name" textbox with the secret, `sensitive=True`.
- Context changes: `navigate`, `same-url-reload` (`Navigate` to the current address), `link-click`, `open-tab`,
  `popup-click` (`window.open`), `switch-tab`, `close-tab`, `dialog` (a `confirm()`; its check wants
  `BrowserContext.dialog == "confirm: Proceed?"` in the click's result and in an `observe()` while the dialog is
  open).

## Middleware (`drivers/middleware.py`)

Three wrappers, each a `Driver` around an inner `Driver`. They know only `contract/` types, so they work for any
driver and any surface; the architecture tests scan the module like the core (no browser words, no driver names, no
concrete imports). `info`, `start`, `stop` and `health` pass through to the inner driver unchanged. The stack order
is `Tracing(RetryOnStale(Timeouts(driver, per_effect)), sink)`: the trace sees the error a timeout or a stale ref
finally produced, and the re-observe of `RetryOnStale` runs under the READ bound.

- **`Timeouts(inner, per_effect: Mapping[Effect, float])`** bounds each `execute(action)` by `action.effect` and
  each `observe()` by `READ`. An effect missing from the map is not bounded. On expiry the inner call is cancelled
  and `ActionTimeout(dispatched=True)` is raised: the command may already have taken effect. A `Wait` longer than
  the READ bound is cut by it.
- **`RetryOnStale(inner)`** catches `StaleRef` with `dispatched=False`, observes once (`Observe()`) and raises a new
  `StaleRef` carrying that observation, chained to the original. It never resends the command: neither built driver
  can remap a stale ref through another ref kind it declares, so there is nothing to retry with. An error with
  `dispatched=True`, a `StaleRef` included, passes through untouched, without an observation (invariant 7).
- **`Tracing(inner, sink)`** writes one dict per `observe` / `execute` call to `sink` (`TraceSink`: any callable
  taking that dict), after the call returns or raises a `DriverError`:
  - `call` (`"observe"` / `"execute"`), `command` (class name), `args` (the command's fields, nested dataclasses as
    dicts and sequences as lists, with `MASK` = `"***"` in place of each `__sensitive_fields__` field of a dataclass
    whose `sensitive` is true);
  - then `ok`, `detail`, `context_changed` for a result; `context` (id) and `elements` (count) for an observation,
    whose element values and native text may show what a sensitive command typed; or `error` (class name),
    `message`, `dispatched` for a `DriverError`, which is raised again. `message` is left out when the command is
    sensitive (its `sensitive` is true and it lists `__sensitive_fields__`): a driver error can quote the typed text
    or the script, e.g. a JS exception message.

  `ActionResult.value` and observation contents are never traced. Other exceptions (cancellation included) pass
  through without a record.

## Settings (`playwright_runtime/config.py`, `surfaces/browser/config.py`)

Environment variables are parsed into two frozen dataclasses, split by owner. Each has
`from_env(environ: Mapping[str, str])`, which reads only the mapping it is given, and `from_process_env()`, which
is `from_env(os.environ)` and the only place that touches the process environment. Besides the mapping, a parser
reads only the current directory (the default of `BrowserSettings.mcp_cwd`). Which variable feeds which field is
listed in [[F_07_browser-drivers]] ("Settings", inventory).

- **`CoreSettings`** (core): `driver`, `surface` (`"browser"`, a registry name), `topology` (derived from
  `driver`: `delegated_worker` for `playwright_mcp`, `direct` for `browser_use`), `provider`, `api_key`,
  `api_base`, `model_name`, `worker_temperature`, `worker_top_p`, `tool_timeout_s`, `guardrails`
  (`BrowserRunGuardrails`), `log_file` (`None` = no dedicated file), `log_level`, `log_mirror_common`,
  `log_audit_raw`, `status_log`, `runtime_server` (`RuntimeServerSettings`, client side). It has no field that only
  a browser has.
- **`RuntimeServerSettings`** (core): the runtime MCP server, i.e. `browser_move` served over MCP
  (`playwright_runtime_mcp_server.py`) and registered by `browser_tools.py`. Client fields: `enabled`, `server_id`,
  `server_name`, `client_type` (as given; normalised by the caller), `server_path` (`""` = derived from host, port
  and path), `command` (default: the current interpreter), `args` (raw string), `timeout_s` (`0` = no timeout, also
  for a value that is not an integer), `host`, `port` (a string; parsed where it is used), `path`, `log_dir`
  (`None` = `mcp_cwd/logs`), `auto_http_fallback`. Server fields: `transport`, `log_level`, `stateless_http`
  (`None` = decided by the transport). Two parsers keep the two old readers apart: `from_env` (client) reads
  `PLAYWRIGHT_RUNTIME_*` and then the `BROWSER_RUNTIME_*` alias; `server_from_env` (server entry point) reads only
  `PLAYWRIGHT_RUNTIME_*` with the server's defaults (path `""`, log dir `<cwd>/logs`), except the log dir, which also
  takes `BROWSER_RUNTIME_LOG_DIR` as before.
- **`BrowserSettings`** (browser surface, a `SurfaceSettings`): `provisioning`, `cdp_url`, `cdp_headers`, `cdp_timeout`, `managed_host`,
  `managed_port`, `managed_kill_existing`, `managed_args`, `user_data_dir`, `profile_name`, `profile_store_path`,
  `browser_binary`, `browser_binary_candidates` (Chrome install paths under `LOCALAPPDATA`, `ProgramFiles`,
  `ProgramFiles(x86)`, `ProgramW6432` on Windows; empty elsewhere and by default), `launch_args` (Playwright MCP
  arguments; Chrome's own arguments are `managed_args`), `mcp_command`, `mcp_timeout_s`, `mcp_cwd`,
  `mcp_browser`, `mcp_device`, `mcp_extension`, `mcp_extension_token`, `mcp_env` (read-only mapping),
  `upload_root`, `sidecar_python`, `selector_cache_path` (`None` = the default under the home directory),
  `local_app_data` (Windows `LOCALAPPDATA`, for the default Chrome user-data dir). The module
  declares `ENV_PREFIXES = ("BROWSER_", "PLAYWRIGHT_")`. Another surface brings its own `surfaces/<name>/config.py`
  with its own settings class and prefixes; `CoreSettings` does not change.

`BROWSER_DRIVER` keeps its name and now selects the driver:

| `BROWSER_DRIVER` | `CoreSettings.driver` | `BrowserSettings.provisioning` |
|---|---|---|
| unset or empty | `playwright_mcp` | `BROWSER_PROVISIONING`, default `remote` (`extension` when `PLAYWRIGHT_MCP_EXTENSION` is true) |
| `playwright_mcp`, `browser_use` | that value | as for unset |
| `managed`, `remote`, `extension` (legacy) | `playwright_mcp` | `BROWSER_PROVISIONING` if set, else the legacy value; one `DeprecationWarning` from `BrowserSettings` |
| anything else | `ValueError` | as for unset |

Values are compared after trimming and lower-casing. An explicit `BROWSER_PROVISIONING` or a legacy value wins over
`PLAYWRIGHT_MCP_EXTENSION`; without one, the provisioner and the Playwright MCP config agree on extension mode. `BROWSER_DRIVER_BACKEND` is not read.

Errors: `ValueError` for an unknown `BROWSER_DRIVER` or `BROWSER_PROVISIONING`, a `BROWSER_MANAGED_PORT` that is not
a positive integer, and a `PLAYWRIGHT_MCP_ENV_JSON` that is not JSON. Every other invalid value falls back to the
next key or the default. `CoreSettings` keeps an unsupported `MODEL_PROVIDER` as given (the harness path takes its
model from the parent agent and must not fail on it); `build_runtime_settings` rejects it as before.

Public facades; their signatures do not change:

- `RuntimeSettings` keeps its fields; `build_runtime_settings(instance, *, core_settings=None,
  browser_settings=None)` builds it from the given objects: `None` core settings are `CoreSettings.from_process_env()`,
  `None` browser settings are `browser_surface_settings()`. `build_playwright_mcp_config`,
  `build_browser_guardrails` and `resolve_playwright_mcp_cwd` read the same two objects.
- `browser_surface_settings(environ=None)` is `SURFACES.create("browser", environ).settings`, with the process
  environment when `environ` is `None`. The core modules (`config.py`, `service.py`, `runtime.py`) and the entry
  points get the browser settings through it or from their caller and type them as `SurfaceSettings`.
- `BrowserInstanceConfig` keeps its eight positional fields and appends `provisioning` and `driver` (`""` = from
  settings). `driver_mode` is an alias of `provisioning`: whichever is given sets both; two different non-empty
  values raise `ValueError`. `cdp_url` stays: the input for `remote`; for `managed` the endpoint comes from the Chrome that was started. In the
  Playwright MCP config a non-blank `launch_args` replaces `BrowserSettings.launch_args` and a non-blank `cdp_url`
  replaces `BrowserSettings.cdp_url`.

### Environment boundary (invariant 9)

Only the two config modules touch the process environment: `from_process_env()` of each settings class,
`browser_surface_settings()` without a mapping, `process_environ()` (a snapshot dict) and `load_repo_dotenv()`
(loads the repo `.env`), all in
`playwright_runtime/config.py` or `surfaces/browser/config.py`. Everything else receives a settings object or a
value from one.

- `utils/env.py` holds pure parse helpers shared by both config modules (`first_value`, `int_value`,
  `parse_command_args`, `is_truthy_env` / `is_falsy_env`, `normalize_provider`, `infer_provider_from_api_base`)
  and defaults; it has no environment access.
- Environments of child processes are pure functions of a mapping the caller passes:
  `runtime_server_child_env(environ)` (the runtime MCP server child: the whole mapping, API key and base copied to
  the provider names, blocked proxies removed) and `runtime_server_environ(environ)` (the server's tool timeout
  defaults, 600 s) in `playwright_runtime/config.py`; the browser-use sidecar's minimal environment in
  `drivers/browser_use/transport.py`, built from OS and `BROWSER_USE_*` names of the given mapping plus
  `BrowserSettings.upload_root`.
- Entry points build settings once: `playwright_runtime/main.py` from `process_environ()` (its own inputs through
  `read_demo_inputs(environ)`), `playwright_runtime_mcp_server.py` from `runtime_server_environ(process_environ())`.
  They pass `core_settings` / `browser_settings` to `BrowserAgentRuntime`, which hands them to `BrowserService` and
  takes `upload_root` and `selector_cache_path` for its builtin actions and selector cache.
- Public functions keep their signatures and gain keyword-only settings or values. Where a caller passes none, the
  function builds them through the config module at the same moment it read the environment before
  (`build_browser_runtime_mcp_config`, `register_browser_runtime_mcp_server`, `restart_local_browser_runtime_server`,
  `ensure_execute_signature_compat`, `build_browser_worker_agent`, `is_browser_subagent_status_log_enabled`).
  Helpers whose value has an obvious "not configured" take the value and treat `None` as not configured:
  `resolve_upload_file_paths(paths, *, upload_root)`, `register_builtin_actions(controller, *, upload_root)`,
  `get_selector_cache(path)`, `discover_sidecar_python(override)`.
- The env-read architecture test allows two exceptions: the browser-use sidecar (a separate process with its own
  config boundary) and the three `env_map` accesses in `service.py`, which touch the Playwright MCP child's env
  mapping, not the process environment.

### Browser-agent log and the tool-call logging hook

`openjiuwen.core` imports nothing from `openjiuwen.harness` for logging. Core owns the hook
(`openjiuwen/core/common/logging/tool_call.py`); the harness registers the browser log with it.

- `register_tool_call_logger(matches, log)` registers `log(message, *args)` for tool names for which
  `matches(name)` is true; registering the same `matches` again replaces its `log`. `tool_call_logger(name)` returns
  the first registered `log` whose matcher accepts `name`, or `None`. The registry is process-wide, like the loggers
  it routes to: agents are built by callers that never see the harness.
- `ReActAgent` writes one line per tool call, `Executing tool: %s with args: %s` (name, redacted arguments), to
  `tool_call_logger(name)` when one is registered and to the core logger otherwise.
- `configure_browser_agent_logging(settings: CoreSettings)` (`playwright_runtime/browser_logging.py`) applies
  `log_file`, `log_level`, `log_mirror_common` and `log_audit_raw` to the stdlib logger `openjiuwen.browser_agent`
  and registers browser tool names with the hook: `browser_` anywhere in the name or a `mcp_playwright` prefix,
  case-insensitive. It is called where a browser agent is built or given browser tools: `create_browser_agent`,
  `build_browser_worker_agent` and `register_browser_runtime_mcp_server`. Without a call, no tool-call line goes to
  the browser log.
- The logger keeps what it was configured with (it is process-wide): the file handler, level, propagation and the
  audit flag. Until the first `configure_browser_agent_logging`, the first use of the logger configures it from
  `CoreSettings.from_process_env()`, so modules that fetch it at import time keep `./logs/browser_agent.log`.
- Reconfiguring with the same file keeps the open handler (the file is not truncated again); with another file the
  old handler is closed and the new file is opened in write mode; with `log_file=None` there is no file, and the
  lines written through `browser_agent_log_info` / `_warning` / `_error` also go to the common logger. `log_mirror_common` propagates browser lines to the parent
  loggers. Raw audit artifacts are written only with `log_audit_raw`, a log file and the browser log context, next
  to the log file under `browser_agent_audit/`.

## Provisioning (`provisioning/`)

A provisioner makes one device ready for a driver. It is picked by name, so the code that builds the agent never
handles a CDP url or a Chrome process itself.

- `ProvisionedDevice(kind, endpoint, extras, cleanup=None)`: frozen; `cleanup` is a no-argument callable, `None`
  when there is nothing to stop.
- `Provisioner` (protocol): `provision(surface_settings) -> ProvisionedDevice` and `release(device)`, which runs
  `device.cleanup` if there is one. Both are synchronous; async callers run them in a worker thread.
- `provisioning/__init__.py` registers `managed`, `remote` and `extension` on `PROVISIONERS` as lazy import
  strings. The registry name is the value of `BrowserSettings.provisioning`; `PROVISIONERS.create(name, **kwargs)`
  returns a new provisioner.

### `ManagedBrowserProvisioner` (`provisioning/managed_browser.py`)

All three names resolve to this class; `provision` branches on `settings.provisioning`. `kind` is `"chrome"`.

| `provisioning` | Starts Chrome | `endpoint` | `extras` | `cleanup` |
|---|---|---|---|---|
| `managed` | yes, unless a Chrome already answers on the port | `http://<managed_host>:<port>` | `profile` (`BrowserProfile`), `port`, `user_data_dir`, `managed_args`, `launch_args` | `stop` |
| `remote` | no | `cdp_url` (`""`: Playwright MCP launches its own browser) | `cdp_headers`, `cdp_timeout`, `launch_args` | `None` |
| `extension` | no | `""` | `extension=True`, `extension_token`, `launch_args` | `None` |

Managed provisioning:

1. Profile store: the one given to the constructor (`profile_store=`), else `profile_store_path`, else
   `mcp_cwd/.browser/profiles.json`.
2. The stored profile named `profile_name` is reused if it is managed, has a port above 0, a user-data dir and the
   same Chrome arguments (`managed_args`). Otherwise a profile is built: port `managed_port`, where 0 means a free
   port (bind to port 0); user-data dir `user_data_dir`, else the OS default Chrome profile when
   `managed_kill_existing`, else `mcp_cwd/.browser-profiles/<profile_name>`.
3. `browser_binary` replaces the stored binary. `""` means auto-detect: `chrome`, `google-chrome`,
   `google-chrome-stable` on `PATH`, then `browser_binary_candidates`, then the per-user and fixed install paths of
   the OS. A configured binary that is not Chrome or does not exist is an error, with no fallback.
4. The profile is stored and selected before the start and stored again with the endpoint after it. With
   `managed_kill_existing`, Chrome processes using the user-data dir are killed first. The start waits up to 30 s
   for `/json/version`; a Chrome that already answers on the port is used without being owned (`stop` leaves it
   running).
5. The instance that provisioned a managed device owns its Chrome: `stop`, `is_endpoint_ready`, `clear` and
   `owns_process` act on it. One instance per device.

`ManagedBrowserDriver` stays a deprecated alias of the class.

### The browser service (`playwright_runtime/service.py`)

`BrowserService` takes the keywords `browser_settings: SurfaceSettings | None` (`None` means
`browser_surface_settings()`, read once at construction) and `core_settings: CoreSettings | None`, which it
hands to the worker agent (`None`: the worker reads `CoreSettings.from_process_env()` when it is built). The service
reads no environment variable itself.
In managed mode, before it registers or refreshes the Playwright MCP server, it:

1. adopts a live Chrome recorded in its profile store (and in the legacy store when no store path is configured)
   whose arguments and binary match the settings: the selected profile first, then the one with its own name; a
   keyed instance adopts only the profile with its own name;
2. otherwise keeps its managed driver while that driver's endpoint answers;
3. otherwise calls `PROVISIONERS.create("managed", profile_store=<its store>).provision(settings)`, writes the
   endpoint into the Playwright MCP config, keeps the provisioner as its managed driver and registers it with the
   process registry.

The settings handed to the provisioner are the service settings with the instance overrides applied:
`provisioning` (instance `provisioning` / `driver_mode`), `profile_name` (instance profile name, else the key),
`managed_port` (instance port; a keyed instance without one gets 0; else the settings port), `user_data_dir` and
`browser_binary` (instance first), `mcp_cwd` (the MCP server config's `cwd`, else the settings). With `remote` and
`extension` the service provisions nothing: the Playwright MCP config already carries the CDP endpoint or the
extension flags. An instance `provisioning` / `driver_mode` other than `remote`, `managed` or `extension` raises
`ValueError` naming `BrowserInstanceConfig.provisioning` and `BROWSER_PROVISIONING`.

## Composition root (`playwright_runtime/assembly.py`)

`assemble_driver(core: CoreSettings, environ, *, registries=None) -> DriverAssembly` builds the driver stack from
names only and returns it started. `registries` is `Registries(drivers, surfaces, provisioners)`; `None` means the
three default registries with the built-ins. The module imports no concrete driver, surface or provisioner and
holds no state. `create_browser_agent` and the Playwright MCP path do not call it yet.

1. `surface = registries.surfaces.create(core.surface, environ)`, a `SurfaceBundle`.
2. `provisioner = registries.provisioners.create(surface.settings.provisioning)`.
3. `device = provisioner.provision(surface.settings)`, run in a worker thread.
4. `driver = registries.drivers.create(core.driver, device, surface.settings)`.
5. When `core.surface not in driver.info.surfaces`: `ValueError` naming the driver, the surface and the surfaces
   the driver serves.
6. `stack = Tracing(RetryOnStale(Timeouts(driver, per_effect)), sink)`: every `Effect` is bounded by
   `core.tool_timeout_s` (the per-tool-call timeout); the sink writes each trace record to the debug log.
7. `await stack.start()`.

A failure in steps 4–6 releases the device; a failure in `start` calls `close()`. A failure in `provision` leaves
nothing to release.

`DriverAssembly(stack, device, provisioner, surface_settings)`: `close()` stops the stack, then releases the device
with `provisioner.release` in a worker thread, also when `stop` raises. `surface_settings` is what `provision` was
called with; `DriverSession` provisions again from it.

## Browser surface profile (`surfaces/browser/`)

`create_browser_surface(environ)` returns `SurfaceBundle(settings, profile)`; `profile.settings` is the same
`BrowserSettings`. No driver name appears in `surfaces/` (architecture test); tool names do.

| Field | Value |
|---|---|
| `commands` | `Navigate`, `Back`, `OpenContext`, `SelectOption`, `UploadFile`, `FillForm`, `Drop`, `GetPdf`, `GetConsole`, `GetNetwork` |
| `renderer` | `BrowserRenderer` |
| `tool_specs` | `BROWSER_TOOL_SPECS` (`tools.py`) |
| `capability_catalog`, `capability_tools` | `capabilities.py` |
| `timeouts` | 240 s for every `Effect` |
| `messages` | `driver_restarted`: "the browser restarted; take a fresh observation"; `driver_lost`: "the browser connection was lost again; the task cannot continue"; `context_changed`: "Call browser_snapshot again and use refs from generation {generation}." (a format string) |
| `policy_rules`, `rails`, `extra_tools`, `native_tools` | empty |
| `scene` | `None` (the conformance suite takes `browser_scene()` directly) |

`BrowserContext` also carries `tab_urls` (parallel to `tabs`) and `current_tab` (index of the focused tab); both are
empty when the driver does not report them. Neither built-in driver fills them yet.

### Tool specs (`tools.py`)

The browser dialect: 17 specs whose name, description and schema equal the browser-use golden
(`snapshots/golden/browser_use/*.json`), except `browser_tabs`, whose `action` enum adds `new`. Element arguments
are `generation_id` (required) plus one of `target_id` (page-state target, through `RefResolver.resolve`), `ref`
(`DriverRef(ref, generation_id)`) or `selector` (`Locator(css, None, generation_id)`), in that precedence;
`browser_drag` uses the `source_` / `target_` prefixed forms. Boolean arguments accept booleans and the strings
`1/true/yes` and `0/false/no`.

| Tool | Command |
|---|---|
| `browser_navigate` | `Navigate(url)`; `wait_until`, `timeout_ms` accepted and ignored |
| `browser_navigate_back` | `Back` |
| `browser_click` | `Click(target, button, count=max(1, click_count))` |
| `browser_type` | `TypeText(target, text, submit=press_enter, sensitive, clear)` |
| `browser_press_key` | `PressKey(key)` |
| `browser_take_screenshot` | `Observe(include_screenshot=True, full_page)` |
| `browser_snapshot` | `Observe(include_screenshot)` |
| `browser_tabs` | `list` (default) → `Observe()`; `new` → `OpenContext()`; `select` → `SwitchContext(contexts[index])`; `close` → `CloseContext(contexts[index])`, or the current context without `index` |
| `browser_close` | `CloseContext(current_context)` |
| `browser_select_option` | `SelectOption(target, (value or label,))` |
| `browser_evaluate` | `RunScript(function, args)` |
| `browser_drag` | `Drag(source, destination)`; `steps` checked and ignored |
| `browser_file_upload` | `UploadFile(target, paths)` |
| `browser_hover` | `Hover(target)` |
| `browser_handle_dialog` | `HandleDialog(accept, promptText or prompt_text)` |
| `browser_drop` | `Drop(target, paths, data=((mimeType or mime_type, data), ...))` |
| `browser_fill_form` | `FillForm(fields)`: `textbox`/`spinbutton`/`searchbox` → textbox, `slider`, `combobox`/`listbox`/`select` (value, else label), `checkbox`/`switch` → checkbox, `radio`; checked values `1/true/yes/on/checked` → `"true"` |

Invalid arguments raise `ValueError` with the wording of the tools they replace (`'generation_id' is required`,
`exactly one of target_id, ref, or selector is required`, `'click_count' must be an integer`, ...); a tab index
outside `contexts` and a close with no current context are `ValueError` too. Seven specs have no schema and no
decoder: `browser_mouse_click_xy` (`Click`), `browser_mouse_move_xy` (`Hover`), `browser_mouse_drag_xy` (`Drag`),
`browser_mouse_wheel` (`Scroll`), `browser_pdf_save` (`GetPdf`), `browser_console_messages` (`GetConsole`),
`browser_network_requests` (`GetNetwork`). `browser_find` is not a spec (it becomes an extra tool over the last
observation).

### Renderer (`renderer.py`)

| Method | Output |
|---|---|
| `observation(obs)` | `native_text` verbatim, then the screenshot as an image block |
| `result(action, result)` with `ok=False` | `detail`, prefixed with `### Error` and a newline unless it starts with it |
| `result(RunScript, …)` | `### Result`, the value, then `detail`; a string as is, `None` as `undefined`, anything else `json.dumps(indent=2, ensure_ascii=False)` |
| `result(Observe() without screenshot, …)` | `### Result` and the tab list, then `detail` |
| `result(OpenContext / SwitchContext / CloseContext, …)` | `detail`; the tab list when `detail` is empty |
| `result(other, …)` | `detail` |
| `error(action, error, observation)` | `### Error` and the message; with an observation (the argument, or the one a `StaleRef` carries) a blank line and its text, then its image |

The tab list is one line per tab of the result's surface context (or its observation's): `- <i>: ` + `(current) `
for `current_tab` + `[<title>]` + `(<url>)` when known (`tab_urls[i]`, or the surface URL for the current tab).
A screenshot in `result.value` becomes an image block; free observations in a result are not rendered. Empty
text produces no block.

### Capability catalog (`capabilities.py`)

`CAPABILITY_TOOLS` maps every entry of `DEFAULT_BROWSER_CAPABILITIES` (`playwright_runtime/browser_capabilities.py`,
unchanged public API) to those of its tool names that a spec or the `browser_find` extra tool serves: `core` equals
`CORE_BROWSER_TOOL_NAMES`; `vision` keeps the screenshot and the four `xy`/wheel tools; `extended_interaction` keeps
`browser_close`, `browser_drag`, `browser_drop`; `pdf`, `devtools`, `network` keep one tool each; `advanced_code`,
`unsafe_dev`, `config`, `storage`, `testing` are empty. `CAPABILITY_CATALOG` gives each entry the
`(command, ref kind)` pairs of its specs: element tools take `DriverRef` and `Locator`, `browser_file_upload` and
`browser_evaluate` also `None`, the three `xy` tools `PointRef`, all others `None`.

## Runtime state and recovery (`playwright_runtime/`)

Three pieces sit between the model-facing tools and the driver stack. None is wired into an agent yet; they are
reached only from their tests until the agent switches to commands.

### Page state (`page_state.py`, `BrowserPageState`)

The existing generation and target registry, extended for contract types. Generations keep the `g<n>` format and
one counter; the text-snapshot methods (`register_ax_snapshot`, `replace_ax_snapshot`, probes) are unchanged.

- `record(action, outcome) -> outcome`: `outcome` is an `ActionResult`, or the `Observation` of an `observe()`. A new
  generation starts when `result.context_changed`, when the observation's context id differs from the current one,
  when the Jaccard distance between the `(ref, role)` sets of the last and the new observation is above
  `element_change_threshold` (constructor parameter, default `0.5`), or when the surface context's address differs
  from the known one without the flag (backstop). A new generation drops `last_observation`, `surface_context`,
  `contexts` and `current_context`: context ids are valid only until the next context change. The returned value is
  stamped: `ActionResult.generation`, `Observation.generation` and every `Element.ref.generation`. Observation
  elements replace the current generation's AX targets (same targets as a complete text snapshot). The surface
  context's `tabs` become `contexts`; `observation.context` becomes `current_context`.
- `free_observations` counts observations that came with an `ActionResult` of a command other than `Observe`.
- `check(action)`: every ref in `iter_refs(action)` whose generation is older than the current one →
  `StaleRef(dispatched=False)`, one message per stale ref (`Stale AX ref e1 belongs to g0; current generation is
  g1.`). Refs with an empty generation and unknown refs of the current generation pass; the driver decides.
- `RefResolver`: `contexts`, `current_context`, `generation_of(ref)` (the generation the ref was last registered in,
  the current one for a ref never seen) and `resolve(target_id, generation)` (`resolve_target`, then `DriverRef` for
  an AX target, `Locator(css)` for a probe target with a validated selector, `ValueError` otherwise).
- `reset()`: a new generation and no contexts or last observation.

### Driver session (`driver_session.py`, `DriverSession`)

`DriverSession(assembly, page_state, messages)` forwards `execute` and `observe` to `assembly.stack`; `messages` must
hold `driver_restarted` and `driver_lost` (read at construction).

| Event | Behaviour |
|---|---|
| first `ConnectionLost` in a task (any `dispatched`) | `stop()` (driver errors ignored); `start()`; if `start()` raises a `DriverError`: `provisioner.release(device)`, `provisioner.provision(surface_settings)`, `start()`; then `page_state.reset()` and the call raises `ConnectionLost(driver_restarted, dispatched=<original>)` |
| restart fails | task failure: `BaseError` `DEEPAGENT_RUNTIME_ERROR` with `driver_lost`, the restart error as cause |
| second `ConnectionLost` in the same task | task failure, no restart |
| `begin_task()` | resets the loss counter |
| `close()` | `assembly.close()` with the current (possibly re-provisioned) device |

The failed command is never sent again. `health()` is not polled.

### Policy rail (`action_policy.py`, `ActionPolicy`)

`ActionPolicy(builders, rules, page_state)` is a `BaseSecurityRail` on `BEFORE_TOOL_CALL`. `builders` maps a
model-facing tool name to the pure argument decoder of its spec (`ToolSpec.build` / `NativeTool.build`). For a tool
in `builders` it parses the arguments (a JSON string or a mapping), decodes the command with the page state as
`RefResolver` and calls `rule.check(action, page_state.surface_context)` for each rule whose `command_type` matches.
A reason, or a `ValueError` from parsing or decoding, denies the call:

- `extra["_skip_tool_calls"][tool_call_id] = True` (per call; the ability manager returns the prepared result
  without running the tool);
- `tool_msg`: content `### Error\n<reason>`, metadata `executed=False`, `state_changed=False`, `denied=True`;
- `tool_result`: `PolicyDenied(reason)` (`dispatched=False`).

Tools without a decoder pass unchecked.

## Drivers

Both drivers fill `BrowserContext` (`surfaces/browser/context.py`). While a JavaScript dialog blocks the page,
`dialog` is `"<type>: <message>"` (`"confirm: Proceed?"`) in every observation and in every action result
that reports it, and `observe()` returns what it can read without the page (the context, tabs, URL, the dialog;
no elements, no screenshot) instead of raising. Outside a dialog `dialog` is `None`.

### browser-use (`drivers/browser_use/`)

`BrowserUseDriver(HandlerDriver)` runs commands through the browser-use sidecar: a separate Python process
(`sidecar/`, its own venv; the agent-core process never imports `browser_use`) reached over NDJSON on stdio
(`transport.py`). `info`: `name="browser_use"`, `surfaces={"browser"}`, `observes={elements, screenshot,
native_text, surface}`, `versions` from the sidecar's `connect` reply.

- Factory: `create_browser_use_driver(device, surface_settings)` builds `BrowserUseConfig(cdp_url=device.endpoint,
  sidecar_python=..., upload_root=...)`, the last two from the `BrowserSettings` (`None` settings: neither);
  `start(config=None)` spawns the sidecar with that interpreter (`None`: `.venvs/browser-use` in the repo) and
  attaches to that CDP endpoint (an explicit config overrides). `UploadFile` and `Drop` resolve relative paths
  against `upload_root`.
- Handlers with `refs={DriverRef, Locator}`: `Click`, `TypeText` (target required, no `slowly`), `Hover`, `Drag`,
  `SelectOption` (exactly one value), `UploadFile` (target required), `Drop`, `FillForm` (each field's
  `sensitive` goes to the sidecar's `type_text`). With `refs={DriverRef}`: `Scroll` (one axis per command),
  `RunScript` (page JS; with a target the element is stamped and passed as the first argument). Without refs:
  `PressKey`, `HandleDialog`, `Wait` (sleep `seconds`, or poll page text for `until_text` up to `seconds`, default
  10 s), `Navigate`, `Back`, `OpenContext`, `SwitchContext`, `CloseContext`. Any other form raises
  `Unsupported(dispatched=False)` before anything is sent.
- `DriverRef.ref` is the handle the sidecar mints in `observe()` (`bnid:<backend node id>`). A `Locator` is
  accepted by CSS only, for the commands whose browser-use tools take a `selector`; it goes to the sidecar as its
  selector ref, which resolves to the first match in the current document. A `Locator` with `text` raises
  `Unsupported(dispatched=False)` (for `FillForm`, before any field is filled). The sidecar's index refs
  (`IndexRef`) stay internal; no handler accepts them.
- `observe()` fills `elements`, `native_text` (browser-use's own page text), `screenshot` (a separate full-page
  capture when `full_page`) and `BrowserContext` (url, title, tabs, PDF viewer, scroll pixels). The title is read
  from `document.title`, because browser-use's own title lags behind it; the sidecar's title is kept when the
  page runs no script. While a dialog is open the sidecar answers `observe` from browser-use's cached target info
  only (URL, title, tabs) with `dialog` and no elements; the driver then runs no script and takes no screenshot.
  The focused context is the first tab whose URL is the page URL. `generation` stays empty.
- Element and keyboard commands run in the sidecar as one action each (wire version 3). The action resolves its
  ref, then sends its input as a task and watches the page while it runs:
  - Dialogs. The sidecar owns `Page.javascriptDialogOpening` (it takes it back from browser-use's auto-accepting
    popups watchdog before every action and observation). When a dialog opens that `HandleDialog` did not arm,
    Chrome holds the input until the dialog closes; the action returns at once with `dialog_opened` (type and
    message) and its input waits in the background. `HandleDialog` answers the dialog on the session that reported
    it. While a dialog is open, `observe()` returns the page as above, screenshots and page scripts fail at once
    (`DriverError`) instead of blocking, and any other action returns `ok=False` without sending input. Every act
    reply carries the page URL and `dialog`, the dialog still open when the reply is built.
  - Popups. A page that opens a popup can wait for the debugger until told to run, which held the click for 30 s.
    When a page target is created during the action, the sidecar resumes the current page
    (`Runtime.runIfWaitingForDebugger`) and reports the new pages as `new_targets`.
  - Navigation. A navigation of the page's main frame that the action started (`Page.frameRequestedNavigation`,
    current tab) gets up to 1 s (`_NAVIGATION_COMMIT_WAIT_S`) to commit (`Page.frameNavigated`). A committed one
    is a `document_changed`, a same-URL reload from a click or a submit included; one that does not commit in
    time counts only if the URL changed.
  - Intercepted input. Before the mouse is used for `Click`, `Hover` and the `Drag` source, the sidecar hit-tests
    the point with `elementFromPoint`. If another element is on top (not the target or inside it; a `<label>` of
    the target is allowed), it raises `ActionIntercepted` with `by` = `tag#id.class "name"` of that element, and
    nothing is sent. The `Drag` destination, `Drop` and the focusing click of `TypeText` are not hit-tested.
- `context_changed`: `Navigate`, `Back`, `OpenContext` always (a same-URL reload too); `SwitchContext` and
  `CloseContext` always; other commands when the sidecar reports `document_changed`, a dialog, or new pages (which
  become `opened_contexts`). `detail` names an opened dialog. `surface` is `BrowserContext(url, dialog)` when a
  dialog is open after the command (one it opened, or one that refused it), else `None`.
- Errors: each sidecar error code maps to one typed error. `dispatched=False` only for errors the sidecar raises
  before any input reaches the page: `StaleIndexError` and `StaleNodeError` (→ `StaleRef`; a node the CDP says is
  not in the document, "does not belong to the document" / "No node with given id"), `ElementNotFound`,
  `ActionIntercepted` (with `by`), `DriverNotConnected` (→ `ConnectionLost`) and `DriverUnsupported` (→
  `Unsupported`). Each action marks where its input starts; a stale or missing node after that point reaches the
  driver as a plain `DriverError(dispatched=True)`. Codes it cannot classify become `DriverError(dispatched=True)`
  and are logged raw.
- Cancellation. One request is in flight at a time; after 30 s without a reply the transport sends the sidecar a
  `cancel` for it and raises `ActionTimeout`. When the caller cancels the request instead (`Timeouts`), the
  transport sends the same `cancel` and re-raises the cancellation, so the sidecar does not run the action alongside
  the next one. A late reply is dropped by id.

### Playwright MCP (`drivers/playwright_mcp/`)

`snapshot.py` and `result.py` are the only code that reads `@playwright/mcp` text. Both are pure functions over
one tool result; they were written against `@playwright/mcp@0.0.78` and its recorded answers
(`tests/unit_tests/harness/tools/browser_move/fixtures/mcp/`).

- **Sections.** A result is one or more `text` blocks (plus an `image` block for screenshots) made of `### Name`
  sections: `Ran Playwright code`, `Open tabs`, `Page`, `Modal state`, `Snapshot`, `Result`, `Events`, `Error`.
  `split_sections` keeps their order.
- **Boxes.** `without_boxes(text)` removes the ` [box=x,y,width,height]` annotations of a snapshot taken with
  `boxes`; the result is byte for byte the text the server gives without `boxes` (checked live on the scene pages).
- **Header** (`PageHeader(url, title, tabs, focused)`). From `### Page` (`- Page URL:`, `- Page Title:`; the title
  is missing while a dialog is open) and `### Open tabs`, or from a `browser_tabs` result list (`parse_tabs`). A
  tab is `ContextRef(id=<index>, kind="tab", title)`; the `(current)` one is `focused`. The server lists tabs only
  when more than one is open, so a `### Page` without `### Open tabs` means one tab, `"0"`. `tabs` always includes
  the focused tab.
- **Elements** (`parse_elements`). One `Element` per YAML node with a ref matching `ARIA_REF` = `(f\d+)?e\d+`
  (iframe refs and the frame-prefixed refs of later pages included); nodes without a ref (`text:`, `option`) are
  not elements. `DriverRef(ref, generation="")`, `role`, `name` (JSON-unescaped), `focused` from `[active]`,
  `enabled` unless `[disabled]`, `bounds` from `[box=x,y,width,height]` (only when the snapshot was taken with
  `boxes`). `value` only for `textbox`, `searchbox`, `combobox`, `spinbutton`, `slider`: the inline text, a child
  `text:` line or, for a combobox, the `[selected]` option. Everything else goes to `extra`: the remaining
  attributes (`level`, `cursor`, `checked`, …; flags as `True`), `/key: value` property lines (`url`,
  `placeholder`) and other nodes' inline text (`text`). YAML-quoted lines (`'button "Note: read me" [ref=…]'`,
  `: "42"`) are unquoted.
- **`decode(tool_name, raw_result, *, output_dir, before=None, sensitive=False) -> ActionResult`.** `raw_result`
  is the `CallToolResult` as JSON (`content`, `isError`). `output_dir` is the server's working directory: file links
  in the text are relative to it. `before` is the `BrowserContext` before the call. `sensitive` drops
  `### Ran Playwright code`, which echoes typed text and scripts, before anything else is read.
  - `value`: the screenshot from an `image` block (`Screenshot`, size from the PNG header); else `### Result`
    parsed as JSON when it is JSON (`undefined` → `None`), the text otherwise; `None` for a `browser_tabs` list,
    which goes to `surface`.
  - `detail`: the remaining sections as text, without `### Result`, `### Snapshot` and `### Events`; `None` when
    nothing remains.
  - `surface`: `BrowserContext(url, title, tabs, dialog)` from the header; `None` when the result has no
    `### Page` and is not a tab list (`browser_fill_form`, `browser_evaluate`, `browser_press_key`). `dialog` is
    `"<type>: <message>"` from a `### Modal state` line `- ["<type>" dialog with message "<message>"]: can be
    handled by …` (the server does not escape the message; it ends at the first `"]: can be handled by`).
  - `observation`: from the inline `### Snapshot` YAML, or from the linked `page-*.yml` read under `output_dir`.
    A link that resolves outside `output_dir`, a missing or unreadable file and an empty file (the server writes
    one while a dialog blocks the snapshot) give `None`; the runtime then observes. `native_text` is the YAML
    verbatim, `context` the focused tab, `surface` as above, `generation` empty.
  - `context_changed`: always for `browser_navigate`, `browser_navigate_back` and `browser_tabs` (a same-URL
    reload included); when `### Modal state` reports a dialog (a file chooser is not one); and, with `before`,
    when the URL differs or the number of tabs changed. `opened_contexts`: the tabs past `len(before.tabs)`.
    Without `before`, a click that navigates or opens a tab reports no change.
- **Errors.** `decode` raises; the message is the `### Error` text without ANSI escapes. Checked in this order:

  | Signal | Raised | `dispatched` |
  |---|---|---|
  | `Ref … not found in the current page snapshot` | `StaleRef` | `False` |
  | `File access denied: … is outside allowed roots` (the server checks file paths before it touches the page) | `DriverError` | `False` |
  | `Target page, context or browser has been closed`, `Failed to open a new tab` | `ConnectionLost` | `True` |
  | call log shows `performing <x> action` or `<x> action done` | `ActionTimeout` on a timeout, else `DriverError` | `True` |
  | call log has `… intercepts pointer events` | `ActionIntercepted(by=tag#id.class "text")` of the last blocker (`… in <container>` for a `from … subtree` blocker) | `False` |
  | `browser_wait_for` timed out (the wait is the command, though its log reads like an actionability wait) | `ActionTimeout` | `True` |
  | `TimeoutError:` / `Timeout Nms exceeded` | `ActionTimeout` | `False` if every call-log line is an actionability wait, else `True` |
  | anything else | `DriverError` | `True` |

  Call-log lines are read without ANSI escapes and without the `N ×` fold. A call refused because a dialog is open
  (`Tool "<name>" does not handle the modal state`) is not raised: it returns `ok=False` with the error and the
  modal state in `detail`, as browser-use does for an action while a dialog is open; with `before` and a dialog,
  `surface` is `before` with that `dialog` (nothing was sent, so the page is the one before the call).

#### The driver (`drivers/playwright_mcp/driver.py`)

- `create_playwright_mcp_driver(device, surface_settings)` builds the `@playwright/mcp` server config with
  `playwright_runtime.config.playwright_mcp_config` (the same builder `build_playwright_mcp_config` uses), with
  `device.endpoint` as the CDP endpoint. The composition root never sees the command line.
- `start()` owns one MCP client (`BrowserMoveStdioClient`, or `BrowserMoveStreamableHttpClient` for
  `streamable-http`) and calls it directly, never through `ToolMgr`; it reads `tools/list` once and keeps each
  mapped tool's description as `DriverInfo.tool_hints`. `health()` pings the session.
- `observe()` calls `browser_snapshot` with `boxes: true` (and `browser_take_screenshot` when asked), so every
  element has `bounds` and a `PointRef` can be built from an observation. `native_text` is the full text of that
  result (header, open tabs, YAML) without the boxes (`without_boxes`): what the model reads today. Tabs come from
  the snapshot header, never from a `browser_tabs list`, except while a modal state (a dialog or a file chooser)
  makes the server refuse `browser_snapshot` and every other page tool: then one `browser_tabs list` (not a page
  tool) gives `context` and `surface` (URL, tabs, the dialog; the title is empty), `elements` are empty, no
  screenshot is taken and `native_text` is the refusal text. An observation that comes free with an action
  result (the linked snapshot file) has no bounds.
- **One attempt per tool call.** The driver calls `call_tool_once` on its client, never the retrying `call_tool`
  the runtime path uses: a call that fails is not sent again. A transport timeout (`mcp_timeout_s`) is
  `ActionTimeout(dispatched=True)`; no connection is `ConnectionLost(dispatched=False)`; any other transport failure
  is `ConnectionLost(dispatched=True)`.
- **Sensitive commands.** `TypeText(sensitive=True)`, `RunScript(sensitive=True)` and a `FillForm` with a sensitive
  field are decoded with `sensitive=True`, so their `detail` has no echoed code; the error message of a failed
  sensitive call can still quote it, and `Tracing` does not record that message.
- **Files.** The server reads upload and drop files only under its working directory and its output directory;
  any other path is refused with `File access denied: … is outside allowed roots` (decoded as
  `DriverError(dispatched=False)`), unless the server runs with `--allow-unrestricted-file-access`.
- Every handler sends one tool call and decodes it with `decode(..., before=<last BrowserContext>)` in a worker
  thread (the decoder may read the linked snapshot file). Commands and tools: Click → `browser_click` /
  `browser_mouse_click_xy`; TypeText → `browser_type`; PressKey → `browser_press_key`; Hover → `browser_hover` /
  `browser_mouse_move_xy`; Drag → `browser_drag` / `browser_mouse_drag_xy`; Scroll → `browser_mouse_wheel`;
  SelectOption, FillForm, Drop, UploadFile (no target: answers the open file chooser), HandleDialog, Wait
  (`browser_wait_for`), RunScript (`browser_evaluate`, page JavaScript), Navigate, Back, Open/Switch/CloseContext
  (`browser_tabs`), GetPdf, GetConsole, GetNetwork. Element refs: `DriverRef`, `Locator(css)`; `PointRef` for the
  coordinate tools.
- **Context ids are valid until the next context change.** On this driver they are tab positions (`"0"`, `"1"`),
  which shift when an earlier tab closes; every open, switch and close reports `context_changed`.

## Relation to other specs

- `S_05` (tools contract): the tools built from these commands are registered like every harness tool and go
  through the permission engine.
- `S_08` (security engine): `ActionPolicy` checks `PolicyRule`s on commands before middleware and the driver and
  denies with a `PolicyDenied` result.
- `S_18` (subagents): `create_browser_agent` is the public entry point; its signature does not change.
