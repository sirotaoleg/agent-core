# S_20 Browser Driver Contract

> **DRAFT.** Written during the driver-architecture refactor of `browser_move` (Phase 2). Sections are added as
> the phases land: the conformance suite (Phase 2), the composition root (Phase 3), the surface profile and the
> tool factory (Phase 4). Until the draft mark is removed, the code in `contract/` is the reference.

## Meta

| Item | Value |
|---|---|
| Type | spec |
| Modules | `openjiuwen/harness/tools/browser_move/contract/` (`observation.py` / `refs.py` / `actions.py` / `results.py` / `errors.py` / `driver.py` / `registry.py` / `surface.py` / `testing/`), `openjiuwen/harness/tools/browser_move/drivers/middleware.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/config.py`, `openjiuwen/harness/tools/browser_move/playwright_runtime/service.py`, `openjiuwen/harness/tools/browser_move/provisioning/` (`__init__.py` / `managed_browser.py`), `openjiuwen/harness/tools/browser_move/surfaces/browser/` (`commands.py` / `context.py` / `config.py` / `scene/`) |
| Last revised | 2026-10-10 |
| Related feature | [[F_07_browser-drivers]] |

## Scope

The contract is the only thing the agent core and a driver both know. The core sends commands and reads typed
results and errors; a driver translates commands for one library and declares which commands it handles. As of
this revision the browser-use driver implements the contract and the middleware wraps any driver; no agent code
uses them yet.

Out of scope here: the composition root, rendering and the model-facing tool schemas. They consume
these types and get their own sections.

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
| `Wait` | `seconds=None`, `until_text=None`, at least one set: `seconds` alone sleeps; `until_text` waits until the text appears, at most `seconds` (`None`: the driver's default), then raises `ActionTimeout` | READ |
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
or logs. Each of the three names that field in a class-level `__sensitive_fields__` (`("text",)`, `("code",)`,
`("value",)`); `Tracing` masks the listed fields of any dataclass whose `sensitive` is true, so a new surface
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
- `DriverInfo(name, surfaces: frozenset[str], versions, actions, observes, tool_hints)`. `actions` maps each handled
  command type to the element ref kinds it accepts (empty for commands without refs). `surfaces` lists the
  surfaces the driver serves; the composition root refuses a driver that does not list the selected one.
  `observes` names the `Observation` fields the driver fills. `tool_hints` are optional per-command descriptions.
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
`KeyError` listing the registered ones; `names()` keeps registration order. Three default registries — `DRIVERS`,
`SURFACES`, `PROVISIONERS` — with `register_driver`, `register_surface`, `register_provisioner`, so a driver,
surface or provisioner can live in another repository. Nothing is registered in `contract/`; the packages that
own the built-ins register them. A driver factory has the signature `factory(device, surface_settings) -> Driver`.

### Surface profile types (`surface.py`)

Dataclasses only; their use is specified with the composition root and the tool factory.

- `SurfaceProfile`: `name`, `commands`, `renderer`, `tool_specs`, `native_tools`, `capability_catalog`
  (entry -> `(command, ref kind | None)` pairs), `extra_tools`, `rails`, `policy_rules`, `timeouts` (per
  `Effect`), `messages`, `settings`, `scene`.
- `ToolSpec(name, commands, description, prompt_section)`: one model-facing tool; it may cover several commands.
- `PolicyRule(command_type, check)`: `check(action)` returns the reason to deny, or `None`.
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
  `check(before, after, result)` tells whether the command under test took effect.

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
| context change, one per `context_changes` entry | `result.context_changed` | yes |
| intercepted | `ActionIntercepted` with a non-empty `by` and `dispatched=False` | yes (`intercepted`) |
| timeout | the scene's case ends in `Wait(until_text=..., seconds=...)` for a text that never appears; it raises `ActionTimeout` with `dispatched=True`; a driver that retries into success fails here | yes (`timeout`) |
| unhandled command | the suite's own `UnhandledCommand` is not in `info.actions` and raises `Unsupported(dispatched=False)` | no |
| fingerprint | two `observe()` calls on the unchanged scene give the same `fingerprint` | no |
| sensitive text, trace, one per sensitive case: `type-text` and each `sensitive` entry | the secret leaves no text in the trace sink; `type-text` is `TypeText(sensitive=True)` built from the `TypeText` happy path; a case whose command does not carry the secret fails | yes (`happy`, `sensitive`) |
| sensitive text, logs, one per sensitive case | the same secret is in no log record of the process, captured at DEBUG including non-propagating loggers | yes (`happy`, `sensitive`) |

A case the scene cannot provide is skipped with the scene's reason; a case needing a command (or ref kind) the driver
does not handle is skipped naming it. Nothing passes silently. The trace case skips when the factory writes nothing
to the sink; the test modules build their driver as `Tracing(driver, sink)`, so it runs.

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
  `popup-click` (`window.open`), `switch-tab`, `close-tab`, `dialog` (a `confirm()`).

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
    `message`, `dispatched` for a `DriverError`, which is raised again.

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
  `log_audit_raw`, `status_log`. It has no field that only a browser has.
- **`BrowserSettings`** (browser surface): `provisioning`, `cdp_url`, `cdp_headers`, `cdp_timeout`, `managed_host`,
  `managed_port`, `managed_kill_existing`, `managed_args`, `user_data_dir`, `profile_name`, `profile_store_path`,
  `browser_binary`, `browser_binary_candidates` (Chrome install paths under `LOCALAPPDATA`, `ProgramFiles`,
  `ProgramFiles(x86)`, `ProgramW6432` on Windows; empty elsewhere and by default), `launch_args` (Playwright MCP
  arguments; Chrome's own arguments are `managed_args`), `mcp_command`, `mcp_timeout_s`, `mcp_cwd`,
  `mcp_browser`, `mcp_device`, `mcp_extension`, `mcp_extension_token`, `mcp_env` (read-only mapping),
  `upload_root`, `sidecar_python`, `selector_cache_path` (`None` = the default under the home directory). The module
  declares `ENV_PREFIXES = ("BROWSER_", "PLAYWRIGHT_")`. Another surface brings its own `surfaces/<name>/config.py`
  with its own settings class and prefixes; `CoreSettings` does not change.

`BROWSER_DRIVER` keeps its name and now selects the driver:

| `BROWSER_DRIVER` | `CoreSettings.driver` | `BrowserSettings.provisioning` |
|---|---|---|
| unset or empty | `playwright_mcp` | `BROWSER_PROVISIONING`, default `remote` |
| `playwright_mcp`, `browser_use` | that value | `BROWSER_PROVISIONING`, default `remote` |
| `managed`, `remote`, `extension` (legacy) | `playwright_mcp` | `BROWSER_PROVISIONING` if set, else the legacy value; one `DeprecationWarning` from `BrowserSettings` |
| anything else | `ValueError` | `BROWSER_PROVISIONING`, default `remote` |

Values are compared after trimming and lower-casing. `BROWSER_DRIVER_BACKEND` is not read.

Errors: `ValueError` for an unknown `BROWSER_DRIVER` or `BROWSER_PROVISIONING`, a `BROWSER_MANAGED_PORT` that is not
a positive integer, and a `PLAYWRIGHT_MCP_ENV_JSON` that is not JSON. Every other invalid value falls back to the
next key or the default. `CoreSettings` keeps an unsupported `MODEL_PROVIDER` as given (the harness path takes its
model from the parent agent and must not fail on it); `build_runtime_settings` rejects it as before.

Public facades; their signatures do not change:

- `RuntimeSettings` keeps its fields; `build_runtime_settings(instance)` builds it from
  `CoreSettings.from_process_env()` and `BrowserSettings.from_process_env()`. `build_playwright_mcp_config`,
  `build_browser_guardrails` and `resolve_playwright_mcp_cwd` read the same two objects.
- `BrowserInstanceConfig` keeps its eight positional fields and appends `provisioning` and `driver` (`""` = from
  settings). `driver_mode` is an alias of `provisioning`: whichever is given sets both; two different non-empty
  values raise `ValueError`. `cdp_url` stays: the input for `remote`; for `managed` the endpoint comes from the Chrome that was started. In the
  Playwright MCP config a non-blank `launch_args` replaces `BrowserSettings.launch_args` and a non-blank `cdp_url`
  replaces `BrowserSettings.cdp_url`.

Until every caller receives a settings object, modules outside the two config modules still read the environment;
the env-read architecture test lists them (invariant 9).

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

`BrowserService` takes the keyword `browser_settings: BrowserSettings | None`; `None` means
`BrowserSettings.from_process_env()`, read once at construction. The service reads no environment variable itself.
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
extension flags.

## Drivers

### browser-use (`drivers/browser_use/`)

`BrowserUseDriver(HandlerDriver)` runs commands through the browser-use sidecar: a separate Python process
(`sidecar/`, its own venv; the agent-core process never imports `browser_use`) reached over NDJSON on stdio
(`transport.py`). `info`: `name="browser_use"`, `surfaces={"browser"}`, `observes={elements, screenshot,
native_text, surface}`, `versions` from the sidecar's `connect` reply.

- Factory: `create_browser_use_driver(device, surface_settings)` builds `BrowserUseConfig(cdp_url=device.endpoint)`;
  `start(config=None)` spawns the sidecar and attaches to that CDP endpoint (an explicit config overrides).
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
  from `document.title`, because browser-use's own title lags behind it; the sidecar's title is kept only when the
  page runs no script. The focused context is the first tab whose URL is the page URL. `generation` stays empty.
- Element and keyboard commands run in the sidecar as one action each (wire version 2). The action resolves its
  ref, then sends its input as a task and watches the page while it runs:
  - Dialogs. The sidecar owns `Page.javascriptDialogOpening` (it takes it back from browser-use's auto-accepting
    popups watchdog before every action and observation). When a dialog opens that `HandleDialog` did not arm,
    Chrome holds the input until the dialog closes; the action returns at once with `dialog_opened` (type and
    message) and its input waits in the background. `HandleDialog` answers the dialog on the session that reported
    it. While a dialog is open, `observe()`, screenshots and page scripts fail at once (`DriverError`) instead of
    blocking, and any other action returns `ok=False` without sending input.
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
  become `opened_contexts`). `detail` names an opened dialog.
- Errors: each sidecar error code maps to one typed error. `dispatched=False` only for errors the sidecar raises
  before any input reaches the page: `StaleIndexError` and `StaleNodeError` (→ `StaleRef`; a node the CDP says is
  not in the document, "does not belong to the document" / "No node with given id"), `ElementNotFound`,
  `ActionIntercepted` (with `by`), `DriverNotConnected` (→ `ConnectionLost`) and `DriverUnsupported` (→
  `Unsupported`). Each action marks where its input starts; a stale or missing node after that point reaches the
  driver as a plain `DriverError(dispatched=True)`. Codes it cannot classify become `DriverError(dispatched=True)`
  and are logged raw.
- Cancellation. One request is in flight at a time; after 30 s without a reply the transport sends the sidecar a
  `cancel` for it and raises `ActionTimeout`. When the caller cancels instead (`Timeouts`), no `cancel` is sent: the
  sidecar task runs on, the next request can run alongside it, and its late reply is dropped by id.

## Relation to other specs

- `S_05` (tools contract): the tools built from these commands are registered like every harness tool and go
  through the permission engine.
- `S_08` (security engine): the browser security rail checks `PolicyRule`s on commands before middleware and the
  driver, and raises `PolicyDenied`.
- `S_18` (subagents): `create_browser_agent` is the public entry point; its signature does not change.
