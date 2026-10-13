# S_20 Browser Driver Contract

## Meta

| Item | Value |
|---|---|
| Type | spec |
| Modules | `openjiuwen/harness/tools/browser_move/contract/` (`observation.py` / `refs.py` / `actions.py` / `results.py` / `errors.py` / `driver.py` / `registry.py` / `surface.py` / `device.py` / `masking.py` / `testing/`), `openjiuwen/harness/tools/browser_move/drivers/` (`__init__.py` / `middleware.py` / `playwright_mcp/snapshot.py` / `playwright_mcp/result.py` / `playwright_mcp/driver.py` / `playwright_mcp/config.py` / `playwright_mcp/tools.py` / `playwright_mcp/clients/`), `openjiuwen/harness/tools/browser_move/runtime/assembly.py`, `openjiuwen/harness/tools/browser_move/runtime/session.py`, `openjiuwen/harness/tools/browser_move/runtime/topology.py`, `openjiuwen/harness/tools/browser_move/runtime/page_state.py`, `openjiuwen/harness/tools/browser_move/runtime/driver_session.py`, `openjiuwen/harness/tools/browser_move/runtime/rails/action_policy.py`, `openjiuwen/harness/tools/browser_move/runtime/tool_factory.py`, `openjiuwen/harness/tools/browser_move/runtime/rails/repeated_call_guard.py`, `openjiuwen/harness/tools/browser_move/runtime/rails/intercepted_action.py`, `openjiuwen/harness/tools/browser_move/runtime/rails/final_answer_recovery.py`, `openjiuwen/harness/tools/browser_move/runtime/config.py`, `openjiuwen/harness/tools/browser_move/runtime/browser_logging.py`, `openjiuwen/harness/tools/browser_move/runtime/service.py`, `openjiuwen/harness/tools/browser_move/runtime/worker.py`, `openjiuwen/harness/tools/browser_move/runtime/utils/env.py`, `openjiuwen/harness/tools/browser_move/surfaces/browser/offload_recall.py`, `openjiuwen/harness/tools/browser_move/provisioning/` (`__init__.py` / `managed_browser.py` / `profiles.py`), `openjiuwen/harness/tools/browser_move/surfaces/` (`__init__.py`; `browser/`: `commands.py` / `context.py` / `config.py` / `policy.py` / `profile.py` / `tools.py` / `renderer.py` / `capabilities.py` / `site_profiles.py` / `state_processor.py` / `status_logging.py` / `helpers/` / `probes/` / `semantic/` / `working_context/` / `rails/` / `scene/`), `openjiuwen/core/common/logging/tool_call.py`, `openjiuwen/core/context_engine/context/context_utils.py` (`offload_session_dir_name`) |
| Last revised | 2026-10-13 |
| Related feature | [[F_07_browser-drivers]] |

## Scope

The contract is the only thing the agent core and a driver both know. The core sends commands and reads typed
results and errors; a driver translates commands for one library and declares which commands it handles. As of
this revision the browser-use and Playwright MCP drivers implement the contract and pass the conformance suite
live, the middleware wraps any driver and the composition root builds a started driver stack, and an agent over
it, by name; the browser agent (`create_browser_agent`) runs on it: its runtime facade starts the driver
session and builds the factory tools at the first invoke. The WebVoyager worker still runs the old path.

The runtime package is `runtime/`, with `controllers/`, `rails/` (the record rails: the policy rail, the repeated-call
guard, the intercepted-action hint, the final-answer recovery) and `utils/` under it; `playwright_runtime_mcp_server.py`
keeps its name as the entry point and imports the package by its full name (`python -m …playwright_runtime_mcp_server`).
Everything that reads pages, tabs or URLs lives under `surfaces/browser/`: the capability catalog, the site profiles,
the probe scripts (`probes/`), the semantic state (`semantic/`), the working context (`working_context/`), the state
context processor, the status log and the browser-only rails (`rails/`): the main agent's rail is
`rails/runtime_rail.py`, its mixins beside it (`rails/terminal.py`, `rails/results.py`, `probes/results.py`,
`semantic/phases.py`, `semantic/evidence.py`, the shared keys in `rails/keys.py`), and the prompt text (the
worker prompt, the image, date-picker and task-rules sections) is in `tools.py`; the facade stays in `runtime/session.py`, the
worker loop and its progress state are `runtime/worker.py`, the service's lifecycle `runtime/service.py`. The probe
scripts are one module each (`probes/metadata.py`, `probes/interactives.py`, `probes/cards.py` with its template in
the resource file `probes/cards.js`); the browser action specs and the upload listing are `helpers/custom.py`.
The architecture tests hold this layout: no module under `runtime/` or `surfaces/` over 1,500 lines; no
Playwright MCP tool name in the core; driver names in the core only in `runtime/browser_tools.py` (the runtime
server's client config) and `runtime/service.py` (the CDP endpoint of the given server config); `contract/`
imports the standard library and itself; `surfaces/` imports no driver package and no provisioner; the core
reaches the registries (`DRIVERS`, `PROVISIONERS`, `SURFACES`) from `runtime/assembly.py` only, `SURFACES` also
from `runtime/config.py` (the surface settings); the core imports no module of `drivers/<name>/`,
`surfaces/<name>/` or `provisioning/`: the facade, the service, the config and the agent builders read the
hooks, the worker prompt and the capability catalog from the surface profile, and the registry is the only way
from the core to a surface. The browser words left in the core are data keys and attributes (`page`, `url`), the
surface name, identifiers and text the model reads, listed one by one in F_07 ("Phase 9: PR d").
The browser profile store is `provisioning/profiles.py`. The old `playwright_runtime` package and the root module
`offload_recall.py` are removed; callers import `runtime.<module>`, `surfaces.browser.<module>` or
`provisioning.profiles`. The WebVoyager runner uses `runtime.config.build_runtime_settings`,
`runtime.session.BrowserAgentRuntime` and `surfaces.browser.capabilities.CORE_BROWSER_TOOL_NAMES`.

The browser surface profile (tool specs, renderer, capability catalog) is specified under "Browser surface
profile"; the tool factory that turns it and the driver's native tools into model-facing tools under "Tool
factory".

### Dependency rules

Each layer imports only what it needs, and the architecture tests (`architecture/test_import_boundaries.py`) hold
every rule with an allowlist that may only shrink:

- `contract/` imports the standard library and itself. It holds every type more than one layer shares: the
  commands, refs, results and errors, the `Driver` base, the registries, the surface types, `ProvisionedDevice`
  (`device.py`, the argument of every driver factory) and `masked` (`masking.py`, the masking rule of `Tracing`,
  `CommandRecord.action` and the batch helper).
- A driver package `drivers/<name>/` imports the contract, its own package, `drivers/middleware.py`, the `commands`
  and `context` modules of the surfaces it serves (it implements their commands and fills their context type), the
  standard library, third-party packages and `openjiuwen.core`; never `runtime/`, `provisioning/` or any other
  surface module. A driver's own configuration lives in its package (`drivers/playwright_mcp/config.py`); it logs
  through the core logger, which the browser log context routes to the browser agent log. The driver allowlist
  is empty.
- `provisioning/` imports the contract only (plus the standard library, third-party packages and
  `openjiuwen.core`); a provisioner types the settings it reads with its own `Protocol`.
- `runtime/` and `surfaces/` import nothing under `drivers/`, except the composition root `runtime/assembly.py`
  (it wraps the built driver with the middleware) and, inside function bodies only, `runtime/config.py`, whose
  public `playwright_mcp_config` / `build_playwright_mcp_config` / `RuntimeSettings.mcp_cfg` call the driver's
  config builder.
- The core does not reach a concrete driver, surface or provisioner through a module outside the package either:
  a core import of an `openjiuwen` module that itself imports one of them is a finding. One is allowlisted:
  `runtime/topology.py` imports `openjiuwen.harness.subagents.browser_agent` (inside `Direct.start`), whose
  `create_browser_agent` builds the main agent with the browser surface's rails, capability catalog and context
  processors.
- The package root holds `__init__.py` and the runtime server entry point `playwright_runtime_mcp_server.py`;
  every other module lives in a layer.

A surface, driver or provisioner is added with new code and registry entries only. The fake-surface test
(`tests/unit_tests/harness/tools/browser_move/architecture/fake_surface/`) holds this: a screen surface with its own
command, renderer and policy rule, a driver and a provisioner, all defined in the test and registered in
test-scoped registries, run a scripted task through `build_agent` on a real deep agent.

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

What `Driver.observe()` returns: a snapshot of the focused context. A scoped `Observe` (`depth` or `root` set) gives a
partial one: the subtree under `root`, at most `depth` levels deep. It shows some of the page's elements, so the page
state never takes it for the page's observation (see "Page state"); a driver that cannot scope raises
`Unsupported(dispatched=False)` before anything is sent.

| Type | Purpose |
|---|---|
| `Observation` | `context: ContextRef` (focused context), `generation` (stamped by the runtime's state tracker; drivers leave it `""`), `elements`, `screenshot`, `viewport`, `native_text` (the driver's own textual snapshot, shown or not by the renderer), `surface: SurfaceContext \| None`. Keyword-only. |
| `Observation.fingerprint` | 16-hex SHA-256 over the context id and each element's `(ref.ref, role, name)`. Generations, values, screenshots and the surface context are left out, so two observations of an unchanged scene match. A backstop for change detection; the primary signal is `ActionResult.context_changed`. |
| `Element` | `ref: DriverRef`, `role`, `name`, `value`, `bounds` (`(x, y, width, height)` in viewport pixels), `enabled`, `focused`, `extra`. |
| `Screenshot` | `png`, `width`, `height`, `scale` (image pixels per viewport pixel). `PointRef` coordinates are always in the screenshot's pixels. |
| `Viewport` | `width`, `height`. |
| `SurfaceContext` | Frozen base with one neutral member, `identity` (a property, `""` on the base): where the surface is, as the surface names it (`BrowserContext.identity` is its `url`; a phone context would give its package and activity). The core reads only `identity`, to detect a move; it passes the rest through to the surface's renderer, rails and page state. |

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
order), `current_context` (`None` when unknown), `screenshot_generation` (the generation of the last screenshot,
whose pixels a `PointRef` addresses; empty before the first), `resolve(model_id, generation) -> Ref` (the ref behind
a runtime-minted element id; `ValueError` for an id it does not know) and `generation_of(ref) -> str` (the generation
a driver ref was last seen in; the current one for a ref never seen). The page state implements it. A decoder builds
every `PointRef` with `screenshot_generation` (the model sends no generation for a point), so a point taken on an
older screenshot is stale like an element ref; an empty generation is never stale.

Who owns staleness is a property of the dialect, written in the generation its decoder stamps. The page state owns
it for the refs it hands out or the model dates itself: a runtime-minted target (`resolve`), a point on a screenshot
(`screenshot_generation`), the browser dialect's `ref` / `selector` with the model's `generation_id`, and a
browser-use index ref. The Playwright MCP server owns it for the aria refs and css the model sends on its dialect,
which it resolves against the live page: those decode with an empty generation, the page state lets them through,
and the server's "not found" is the `StaleRef` the driver raises (`dispatched=False`), so the model gets the error
with a fresh observation either way.

### Commands (`actions.py`, `surfaces/<name>/commands.py`)

A command is a frozen dataclass subclassing `Action`, with a class-level `effect` (`Effect.READ`, `ACT`,
`NAVIGATE`, `SCRIPT`); middleware chooses timeouts and retry rules by effect.

**Where a command lives.** `contract/actions.py` holds `Action`, `Effect` and the commands that make sense on any
screen:

| Command | Fields | Effect |
|---|---|---|
| `Observe` | `include_screenshot=False`, `full_page=False`, `depth: int \| None = None`, `root: Ref \| None = None`; `scoped` is true when either is set | READ |
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

`RunScript.code` is a JavaScript expression: when its value is a function the driver calls it (with `args`, and the
element first when `target` is set), otherwise the value is the result (as Playwright MCP's `browser_evaluate`).

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
`("code", "args")`, `("value",)`); `masked` (`contract/masking.py`) replaces the listed fields of any dataclass
whose `sensitive` is true, for `Tracing` and for `CommandRecord.action` alike, so a new surface command opts in by
declaring the attribute and a `sensitive` field.

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
  tool_name_prefix, display_name)`. `actions` maps each handled command type to the element ref kinds it accepts (empty for
  commands without refs). `surfaces` lists the surfaces the driver serves; the composition root refuses a driver
  that does not list the selected one. `observes` names the `Observation` fields the driver fills. `tool_hints` are
  optional per-command descriptions. `native_tools` (default empty) maps a tool spec name to a
  `NativeTool(description, schema, build=None)`: a tool the driver offers in its own format, with
  `build(args, resolver) -> Action` decoding its arguments; `build` is `None` for a driver tool no command backs,
  which the tool factory never emits itself (a surface extra tool may take its description and schema). The model
  sees `tool_name_prefix` (default `""`) + the spec name. The Playwright MCP driver fills them after `start()`.
  `display_name` (default `""`) is how a prompt names the driver to the model (`"Playwright MCP"`,
  `"browser-use"`); a reader falls back to `name` when it is empty. It is the only driver-specific text a prompt in
  the core carries, so the core names no driver itself.
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
`factory(environ, instance=None, settings=None) -> SurfaceBundle` (`settings` is used instead of parsing `environ`;
`instance`, the surface's per-agent identity, is applied to the settings last), provisioner
`factory(**kwargs) -> Provisioner`.

`register_driver(name, factory, *, default_topology="direct")` also records the driver's default topology in
`DRIVER_TOPOLOGIES: dict[str, str]`, module state next to `DRIVERS` (the `Registry` class is generic over the three
kinds and carries no driver-only field). `default_topology(name)` returns the recorded string or raises the
`KeyError` of an unknown driver, listing the registered names. A duplicate name is refused before anything is
recorded. `contract/` stores and returns the string without judging it: the settings parser validates the
environment value and the core maps the name to a behaviour.

Nothing is registered in `contract/`; the packages that own the built-ins register them with lazy strings and
re-export the registry, so `from ..drivers import DRIVERS` gives a registry with the built-ins in it:

| Package | Names | Factory | Default topology |
|---|---|---|---|
| `drivers/__init__.py` | `browser_use` | `drivers.browser_use.driver:create_browser_use_driver` | `direct` |
| | `playwright_mcp` | `drivers.playwright_mcp.driver:create_playwright_mcp_driver` | `delegated_worker` |
| `surfaces/__init__.py` | `browser` | `surfaces.browser.profile:create_browser_surface` | — |
| `provisioning/__init__.py` | `managed`, `remote`, `extension` | `provisioning.managed_browser:ManagedBrowserProvisioner` | — |

Importing the package, a registry package or the composition root imports no concrete driver, surface or
provisioner module and not `browser_use`.

### Surface types (`surface.py`)

Data types only; their use is specified with the composition root and the tool factory.

- `SurfaceSettings` (protocol): the settings a surface parses from its own variables. The core reads one attribute,
  `provisioning` (a provisioner registry name), and passes the object on to the provisioner and the driver factory
  unchanged. The core types surface settings with it and never imports a surface's settings class.
- `SurfaceBundle(settings, profile=None)`: what a surface factory returns. A surface without a profile returns its
  settings only; the browser surface returns both.

- `SurfaceProfile`: `name`, `commands`, `renderer: Renderer`, `tool_specs`, `capability_catalog`
  (entry -> `(command, ref kind | None)` pairs; `None` is a call without an element ref), `capability_tools`
  (the same entries -> the names of the tools they select), `extra_tools` and `rails` (each a `ContextFactory`:
  called with the core's `ToolContext`, returns a tool or a rail), `policy_rules`, `timeouts`
  (per `Effect`), `messages`, `settings`, `scene`; and the fields the agent runtime facade and the delegated
  worker read: `capabilities` (the catalog, a tuple of `Capability` in order, its first entry the default one
  every agent gets), `custom_actions(controller, context)`, `page_metadata(context) -> (metadata, error)`,
  `policy_probe(context, source, params)`, `activate(context, address) -> bool`, `semantic_tracker()` (a
  factory) and `worker_prompt(screenshot_subdir, artifacts_subdir, *, tool_names=None, driver_name)`, typed by the
  `Protocol`s `CustomActions`, `Metadata`, `PolicyProbe`, `Activate` and `WorkerPrompt` in `surface.py`. Each is
  `None` (the catalog empty) unless the surface fills it, and each is optional for the facade and the worker:
  without `custom_actions` no custom action is registered; without `page_metadata` the captures use `{}` and no
  error; without `policy_probe` the facade answers `{"ok": False, "error": "surface '<name>' has no policy probe",
  "elements": [], "page_state": ...}`; without `activate` it answers `False`; without `semantic_tracker` it uses a
  tracker that never observes progress (`{"progress": "unknown", "observable_progress": False}`, `latest` `{}`);
  without `worker_prompt` the worker gets `runtime/agents.default_worker_prompt`, which names the driver and the
  output folders and no surface. The unused `native_tools` field is gone (a driver's native tools are
  `DriverInfo.native_tools`).
- `Capability(name, description, tool_names, policy_only=False)`: one catalog entry; `policy_only` marks an
  entry a policy rule alone grants, which the driver has no feature to enable for.
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
- `RunScript` without a target is a bare expression (`document.title = 'script:ran'`); with a target, a function of
  the element.
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

Three wrappers, each a `Driver` around an inner `Driver`, and the masking helper `Tracing` shares with the tool
factory. They know only `contract/` types, so they work for any
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
  - `call` (`"observe"` / `"execute"`), `command` (class name), `args` (the fields of `masked(command)`, nested
    dataclasses as dicts and sequences as lists);
  - then `ok`, `detail`, `context_changed` for a result; `context` (id) and `elements` (count) for an observation,
    whose element values and native text may show what a sensitive command typed; or `error` (class name),
    `message`, `dispatched` for a `DriverError`, which is raised again. `message` is left out when the command is
    sensitive (its `sensitive` is true and it lists `__sensitive_fields__`): a driver error can quote the typed text
    or the script, e.g. a JS exception message.

  `ActionResult.value` and observation contents are never traced. Other exceptions (cancellation included) pass
  through without a record.
- **`masked(value)`** (`contract/masking.py`, re-exported here with `MASK`) returns a copy of a command (or any
  value) with `MASK` = `"***"` in place of each
  `__sensitive_fields__` field of every dataclass in it whose `sensitive` is true (a `FormField` inside a
  `FillForm` included); other values are copied unchanged. It is the one masking rule: `Tracing` traces its
  output and `CommandTool` stores it as `CommandRecord.action`.

## Settings (`runtime/config.py`, `surfaces/browser/config.py`)

Environment variables are parsed into two frozen dataclasses, split by owner. Each has
`from_env(environ: Mapping[str, str])`, which reads only the mapping it is given, and `from_process_env()`, which
is `from_env(os.environ)` and the only place that touches the process environment. Besides the mapping, a parser
reads only the current directory (the default of `BrowserSettings.mcp_cwd`). Which variable feeds which field is
listed in [[F_07_browser-drivers]] ("Settings", inventory).

- **`CoreSettings`** (core): `driver`, `surface` (`"browser"`, a registry name), `topology` (`direct`,
  `delegated_worker`, or `""` = the driver's registered default; see `BROWSER_TOPOLOGY` below), `provider`, `api_key`,
  `api_base`, `model_name`, `worker_temperature`, `worker_top_p`, `tool_timeout_s` (caps the driver stack's
  per-effect timeouts, "Composition root" step 5), `guardrails`
  (`BrowserRunGuardrails`), `log_file` (`None` = no dedicated file), `log_level`, `log_mirror_common`,
  `log_audit_raw`, `status_log`, `runtime_server` (`RuntimeServerSettings`, client side). It has no field that only
  a browser has.
- **`RuntimeServerSettings`** (core): the runtime MCP server, i.e. `browser_move` served over MCP
  (`playwright_runtime_mcp_server.py`); `build_browser_runtime_mcp_config` (`browser_tools.py`) turns the client
  fields into the `McpServerConfig` a host registers through the core (`Runner.resource_mgr.add_mcp_server` and the
  agent's ability manager). `browser_move` patches none of the core's MCP client classes and starts no local copy of
  the server for a host: a host that wants the server running starts the entry point itself. Client fields: `enabled`, `server_id`,
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
| `managed`, `remote`, `extension` (legacy) | `playwright_mcp` (`DEFAULT_DRIVER`) | `BROWSER_PROVISIONING` if set, else the legacy value; one `DeprecationWarning` per parse from `BrowserSettings` (Python's warning registry shows it once per value and call site) |
| any other name | that value | as for unset |

The parser does not check the name: a driver registered from another package (`register_driver`) is selected by
setting `BROWSER_DRIVER` to its name. The driver registry checks it when the driver is built
(`assemble_driver` → `DRIVERS.create`): an unregistered name is `KeyError("unknown driver '<name>'; registered:
<names>")` after provisioning, and the device is released; `topology_for` raises the same error when no topology is
given. `BrowserInstanceConfig.driver` takes any registered name the same way.

Values are compared after trimming and lower-casing. An explicit `BROWSER_PROVISIONING` or a legacy value wins over
`PLAYWRIGHT_MCP_EXTENSION`; without one, the provisioner and the Playwright MCP config agree on extension mode. `BROWSER_DRIVER_BACKEND` is not read.

`BROWSER_TOPOLOGY` selects how the runner's facade runs a task, independently of the driver: `direct` (the main
agent on the facade) or `delegated_worker` (the worker through the browser service), compared after trimming and
lower-casing; `TOPOLOGIES = ("direct", "delegated_worker")` in `config.py` is the allowed set. Unset or blank gives
`CoreSettings.topology == ""`, which means the driver's registered default (`default_topology(driver)`, "Registries").
The parser cannot resolve it: `config.py` imports only `contract` and `surfaces`, and the built-in registrations
happen when the composition root imports `drivers/`, so a `CoreSettings` built alone would see an empty registry.
The resolution happens where the driver is known, in `topology_for(service.core)` at the facade's
`ensure_started()` ("Topology"), so an instance's `driver` override takes its own default.

Errors: `ValueError` for an unknown `BROWSER_TOPOLOGY` or `BROWSER_PROVISIONING`, a `BROWSER_MANAGED_PORT` that is not
a positive integer, and a `PLAYWRIGHT_MCP_ENV_JSON` that is not JSON. Every other invalid value falls back to the
next key or the default. `CoreSettings` keeps an unsupported `MODEL_PROVIDER` as given (the harness path takes its
model from the parent agent and must not fail on it); `build_runtime_settings` rejects it as before.

Public facades; their signatures do not change:

- `RuntimeSettings` keeps its fields; `build_runtime_settings(instance, *, core_settings=None,
  browser_settings=None)` builds it from the given objects: `None` core settings are `CoreSettings.from_process_env()`,
  `None` browser settings are `browser_surface_settings()`. `build_playwright_mcp_config`,
  `build_browser_guardrails` and `resolve_playwright_mcp_cwd` read the same two objects. `playwright_mcp_config`
  (`runtime/config.py`) maps the instance (`cdp_url`, `launch_args`, the sanitized key) onto the driver's builder
  `drivers/playwright_mcp/config.playwright_mcp_config(settings, *, cdp_url, launch_args, key,
  required_capability)`, imported when it is called. The builder's `--caps` list is the driver's
  `SERVER_CAPABILITIES` (the server's capability groups, equal to the catalog's entries after the first that are
  not `policy_only`, in catalog order; a test compares the two), then the caller's `required_capability`.
- `browser_surface(environ=None, settings=None)` is `SURFACES.create("browser", environ, None, settings)` and
  `browser_surface_settings(environ=None)` its `.settings`, with the process
  environment when `environ` is `None`. The core modules (`config.py`, `service.py`, `session.py`) and the entry
  points get the browser settings through it or from their caller and type them as `SurfaceSettings`.
- `BrowserInstanceConfig` keeps its eight positional fields and appends `provisioning` and `driver` (`""` = from
  settings). `driver_mode` is an alias of `provisioning`: whichever is given sets both; two different non-empty
  values raise `ValueError`. `cdp_url` stays: the input for `remote`; for `managed` the endpoint comes from the Chrome that was started. In the
  Playwright MCP config a non-blank `launch_args` replaces `BrowserSettings.launch_args` and a non-blank `cdp_url`
  replaces `BrowserSettings.cdp_url`.
- `BrowserSettings.with_instance(instance)` returns the settings for one isolated browser, the rules the browser
  service applies (below) in one place: `provisioning` = the instance's (lower-cased), else the settings'; any other
  value than `remote`, `managed`, `extension` raises `ValueError` naming `BrowserInstanceConfig.provisioning`;
  `managed_port` = the instance port when above 0, else 0 (a free port) for a keyed instance, else the settings
  port; `profile_name` = the instance profile name, else the sanitized key, else the settings'; `cdp_url`,
  `user_data_dir`, `browser_binary` = the instance's when not blank; `launch_args` = the instance's parsed
  `PLAYWRIGHT_MCP_ARGS`-style string when not blank. `instance.driver` is read by the browser service (below), not
  here. An empty instance returns equal settings. `create_browser_surface(environ, instance, settings)` applies it:
  the bundle's settings and `profile.settings` are the instance-applied settings.
- `BrowserSettings.mcp_server: McpServerConfig | None` carries a server config given by a caller (the WebVoyager
  runner's `mcp_cfg`, through the service); the Playwright MCP driver factory keeps its command line (below).

### Environment boundary (invariant 9)

Only the two config modules touch the process environment: `from_process_env()` of each settings class,
`browser_surface_settings()` without a mapping, `process_environ()` (a snapshot dict) and `load_repo_dotenv()`
(loads the repo `.env`), all in
`runtime/config.py` or `surfaces/browser/config.py`. Everything else receives a settings object or a
value from one.

- `runtime/utils/env.py` holds pure parse helpers shared by both config modules (`first_value`, `int_value`,
  `parse_command_args`, `is_truthy_env` / `is_falsy_env`, `normalize_provider`, `infer_provider_from_api_base`)
  and driver-free defaults; it has no environment access. The Playwright MCP launch defaults (`npx`,
  `-y @playwright/mcp@0.0.78`) live in `surfaces/browser/config.py`, their only reader.
- Environments of child processes are pure functions of a mapping the caller passes:
  `runtime_server_child_env(environ)` (the runtime MCP server child: the whole mapping, API key and base copied to
  the provider names, blocked proxies removed) and `runtime_server_environ(environ)` (the server's tool timeout
  defaults, 600 s) in `runtime/config.py`; the browser-use sidecar's minimal environment in
  `drivers/browser_use/transport.py`, built from OS and `BROWSER_USE_*` names of `BrowserSettings.process_env` (the
  mapping the settings were parsed from, hidden from `repr` and equality) plus `BrowserSettings.upload_root`; the
  driver factory passes both through `BrowserUseConfig`.
- Entry points build settings once: `runtime/main.py` from `process_environ()` (its own inputs through
  `read_demo_inputs(environ)`), `playwright_runtime_mcp_server.py` from `runtime_server_environ(process_environ())`.
  They pass `core_settings` / `browser_settings` to `BrowserAgentRuntime`, which hands them to `BrowserService` and
  takes `selector_cache_path` for its selector cache; the custom actions read `upload_root` from the context's
  settings.
- Public functions keep their signatures and gain keyword-only settings or values. Where a caller passes none, the
  function builds them through the config module at the same moment it read the environment before
  (`build_browser_runtime_mcp_config`, `ensure_execute_signature_compat`, `build_browser_worker_agent`,
  `is_browser_subagent_status_log_enabled`).
  Helpers whose value has an obvious "not configured" take the value and treat `None` as not configured:
  `resolve_upload_file_paths(paths, *, upload_root)`, `register_custom_action_specs(controller, *, upload_root)`,
  `get_selector_cache(path)`, `discover_sidecar_python(override)`.
- The env-read architecture test allows two exceptions: the browser-use sidecar (a separate process with its own
  config boundary) and the one `env_map` read in `service.py` (the CDP endpoint of the given server config, part of
  the lifecycle identity), which touches the Playwright MCP child's env mapping, not the process environment.

### Browser-agent log and the tool-call logging hook

`openjiuwen.core` imports nothing from `openjiuwen.harness` for logging. Core owns the hook
(`openjiuwen/core/common/logging/tool_call.py`); the harness registers the browser log with it.

- `register_tool_call_logger(matches, log)` registers `log(message, *args)` for tool names for which
  `matches(name)` is true; registering the same `matches` again replaces its `log`. `tool_call_logger(name)` returns
  the first registered `log` whose matcher accepts `name`, or `None`. The registry is process-wide, like the loggers
  it routes to: agents are built by callers that never see the harness.
- `ReActAgent` writes one line per tool call, `Executing tool: %s with args: %s` (name, redacted arguments), to
  `tool_call_logger(name)` when one is registered and to the core logger otherwise.
- `configure_browser_agent_logging(settings: CoreSettings)` (`runtime/browser_logging.py`) applies
  `log_file`, `log_level`, `log_mirror_common` and `log_audit_raw` to the stdlib logger `openjiuwen.browser_agent`
  and registers browser tool names with the hook: `browser_` anywhere in the name or a `mcp_playwright` prefix,
  case-insensitive. It is called where a browser agent is built: `create_browser_agent` and
  `build_browser_worker_agent`. Without a call, no tool-call line goes to the browser log.
- The logger keeps what it was configured with (it is process-wide): the file handler, level, propagation and the
  audit flag. Until the first `configure_browser_agent_logging`, the first use of the logger configures it from
  `CoreSettings.from_process_env()`, so modules that fetch it at import time keep `./logs/browser_agent.log`.
- Reconfiguring with the same file keeps the open handler (the file is not truncated again); with another file the
  old handler is closed and the new file is opened in write mode; with `log_file=None` there is no file, and the
  lines written through `browser_agent_log_info` / `_warning` / `_error` also go to the common logger. `log_mirror_common` propagates browser lines to the parent
  loggers. Raw audit artifacts are written only with `log_audit_raw`, a log file and the browser log context, next
  to the log file under `browser_agent_audit/`.
- The browser log context (`openjiuwen/core/common/logging/browser_context.py`) is entered by
  `BrowserRuntimeRail.before_invoke` for the invoke when the runtime's `dedicated_log` is true, the default; while it
  is set, the core logger sends every line of the invoke, the model client's usage lines included, to
  `openjiuwen.browser_agent` instead of the process log (mirrored to it only with `log_mirror_common`). The WebVoyager
  runner's Direct agent runs with `dedicated_log=False`, so its model-call lines reach the captured process log as the
  worker's do; its tool-call lines still reach the browser log through the hook, and no raw audit artifact is written
  for it.
- Drivers and their clients import nothing from `runtime/`: they log through the core logger, so inside the browser
  log context their lines (the MCP clients' call lines, the browser-use sidecar's log and stderr lines) land in the
  browser agent log, and outside it in the process log.

## Provisioning (`provisioning/`)

A provisioner makes one device ready for a driver. It is picked by name, so the code that builds the agent never
handles a CDP url or a Chrome process itself.

- `ProvisionedDevice(kind, endpoint, extras, cleanup=None)` (`contract/device.py`, re-exported by
  `provisioning/`): frozen; `cleanup` is a no-argument callable, `None` when there is nothing to stop.
- `Provisioner` (protocol): `provision(surface_settings) -> ProvisionedDevice`, `release(device)`, which runs
  `device.cleanup` if there is one, and `is_alive(device) -> bool`, whether the device still answers (the driver
  session re-provisions a dead one). All three are synchronous; async callers run them in a worker thread.
- `provisioning/__init__.py` registers `managed`, `remote` and `extension` on `PROVISIONERS` as lazy import
  strings. The registry name is the value of `BrowserSettings.provisioning`; `PROVISIONERS.create(name, **kwargs)`
  returns a new provisioner.

### `ManagedBrowserProvisioner` (`provisioning/managed_browser.py`)

All three names resolve to this class; `provision` branches on `settings.provisioning`. `kind` is `"chrome"`. Its
input is typed `ManagedBrowserSettings`, a `Protocol` in the same module listing the fields it reads;
`BrowserSettings` satisfies it structurally, so provisioning imports no surface module.

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

`is_alive(device)`: a device with an endpoint (managed, remote) is alive while `<endpoint>/json/version` answers
with a `webSocketDebuggerUrl` or `Browser` field (1.5 s timeout, the same check as `is_endpoint_ready`); a device
without one (extension, or remote without `cdp_url`, where the driver launches its own browser) is always alive:
there is nothing to probe or to provision again.

`ManagedBrowserDriver` stays a deprecated alias of the class.

### The browser service (`runtime/service.py`, `runtime/worker.py`)

`BrowserService` is `BrowserService(_WorkerLoop)`: `service.py` holds the lifecycle (construction, the lifecycle
identity and the registry lease, the task binding, cancellation, the session, screenshots, `shutdown`);
`worker.py` holds `_WorkerLoop`, a plain mixin with the worker loop (`run_task`, `run_task_once`, the retry and
restart predicates, the resume task, the failure summary, the progress recording), and `BrowserTaskProgressState`,
`MAX_ITERATION_MESSAGE` and the observer context variables; the rail imports the two names from `worker.py`, and
`worker.py` imports nothing from `service.py`. The progress state is reached through `set_progress_state`,
`clear_progress_state`, `should_treat_as_completed`, `build_failure_summary` and `observe_worker_tool_result`;
there are no other public progress methods.

`BrowserService` takes the keywords `browser_settings: SurfaceSettings | None` (`None` means
`browser_surface_settings()`, read once at construction), `core_settings: CoreSettings | None` and
`page_state: BrowserPageState | None` (the facade's; `None` makes one). The service reads no environment variable
itself; `service.core` is the core settings (`None`: `CoreSettings.from_process_env()`, read at first use) with the
instance's `driver`, when set, in place of `core.driver`.

The service holds the `DriverSession` the worker runs on and provisions nothing itself:

- `ensure_runtime_ready()`: without a session, `open_session(service.core, process_environ(), page_state=,
  instance=, settings=)` ("Composition root") with the service settings carrying `mcp_server` (the given `mcp_cfg`)
  and `mcp_cwd` (the config's `cwd`, else the settings'); the session's provisioner is registered with the process
  registry as the identity's managed driver (the lifecycle identity is a service concept, so the registration stays
  here). With a session whose stack was stopped by a task release, `session.start()`. Then `begin_task()`.
  `started` is "the stack runs".
- `run_task(task, session_id=, request_id=, timeout_s=, *, worker)` runs the worker loop (the per-session lock,
  the cancel check, the attempts, the max-iteration resume, the failure context, the JSON contract and the
  final-answer recovery) on the agent `worker()` returns: `run_task_once(..., worker=)` awaits it once per attempt,
  and the recovery call reuses that agent. `_restart` is `reset()` alone (the session closed, the `browser_agent`
  slot cleared), so the next attempt's `worker()` rebuilds the worker where it is built (`DelegatedWorker.start`,
  "Topology"). The service builds no agent and starts none (there is no `ensure_started`); the `browser_agent`
  slot and its setter stay for the runner's helper removal. `observe_worker_tool_result` is the progress recorder a
  worker's builder takes as `tool_result_observer`; `screenshot_subdir` and `artifacts_subdir` are readable.
- `acquire_task_binding()` counts a task and calls `session.begin_task()`; `release_task_binding()` on the last
  binding stops the stack (`session.stop()`, the device and its Chrome stay), forgets the worker and the task
  state. `reset()` closes the session (stop, release) and stops the identity's registered provisioners;
  `detach_lifecycle_resources()` is the per-service part of that. `shutdown()` is `reset()` plus the release.

An instance `provisioning` / `driver_mode` other than `remote`, `managed` or `extension` raises `ValueError` naming
`BrowserInstanceConfig.provisioning` and `BROWSER_PROVISIONING` (the lifecycle identity needs it before a session
exists). A live Chrome is adopted only by the provisioner, through the profile with the settings' name; the
store's selected profile and the legacy store path are not consulted any more.

## Composition root (`runtime/assembly.py`)

`assemble_driver(core: CoreSettings, environ, *, instance=None, registries=None, settings=None) -> DriverAssembly`
builds the driver stack from names only and returns it started. `registries` is `Registries(drivers, surfaces,
provisioners)`; `None` means the three default registries with the built-ins. The module imports no concrete
driver, surface or provisioner, names none, and holds no state. `open_session` (below) calls it for the one session
the main agent and the worker share.

1. `surface = assemble_surface(core, environ, instance=, settings=, registries=)`, which is
   `registries.surfaces.create(core.surface, environ, instance, settings)`, a `SurfaceBundle`: the surface factory
   parses `environ` unless `settings` is given, and applies `instance` (the surface's per-agent identity,
   `BrowserInstanceConfig` for the browser) to the settings, so provisioning, the driver factory and the tool
   context all see it. The composition root never touches the settings. `assemble_surface` is public so a caller
   can hold the profile (its policy rules and messages) before any driver exists; a bundle built that way and the
   session's bundle agree on `messages` (constants of the surface factory) and on what their `policy_rules`
   decide (built from the same settings).
2. (merged into step 1.)
3. `provisioner = registries.provisioners.create(surface.settings.provisioning)`.
4. `device = provisioner.provision(surface.settings)`, run in a worker thread.
5. The stack for the device: `driver = registries.drivers.create(core.driver, device, surface.settings)`; when
   `core.surface not in driver.info.surfaces`, `ValueError` naming the driver, the surface and the surfaces the
   driver serves; `stack = Tracing(RetryOnStale(Timeouts(driver, per_effect)), sink)` with `per_effect[e] =
   min(profile.timeouts[e], core.tool_timeout_s)`. Precedence: the surface sets a bound per effect, and the core's
   tool timeout (`PLAYWRIGHT_TOOL_TIMEOUT_S`, else `PLAYWRIGHT_MCP_TIMEOUT_S`, else `BROWSER_TIMEOUT_S`, default
   240 s) caps every one of them; the delegated worker's tool calls are bounded by the same value once more
   (`ensure_execute_signature_compat`). An effect missing from `profile.timeouts` runs unbounded; the sink writes
   each trace record to the debug log.
6. `await stack.start()`.

A failure in step 5 releases the device; a failure in `start` calls `close()`. A failure in `provision` leaves
nothing to release.

`DriverAssembly(stack, device, provisioner, rebuild=None, surface=None)`: `close()` stops the stack, then releases
the device with `provisioner.release` in a worker thread, also when `stop` raises. `rebuild(device)` is step 5 for
another device (same driver factory, surface check and middleware), not started; `surface` is the bundle of step 2,
whose `settings` are what `provision` was called with. `DriverSession` provisions again from `surface.settings` and
builds the new stack with `rebuild`. Assemblies built by hand (tests) may leave `rebuild` and `surface` out.

`open_session(core, environ, *, page_state, instance=None, settings=None, registries=None) -> DriverSession` is
the one way a session is opened: `assemble_driver(...)`, then `DriverSession(assembly, page_state,
assembly.surface.profile.messages)`. A `ConnectionLost` from that first `assemble_driver` (the stack's first
`start()` lost its connection; the failed assembly has already stopped its stack and released its device) runs
`assemble_driver` once more, a fresh provision; a second `ConnectionLost`, or any other error, propagates. Only the
first open is retried here: once the session exists, a lost connection is the session's own restart ("Driver
session").

### Agent construction

Every agent gets its session, tools and rails from this module and is built by exactly one builder: the main agent
by `create_browser_agent` (`openjiuwen/harness/subagents/browser_agent.py`), which queues
`BrowserRuntimeRail(runtime)` and `runtime.core_rails()` as rails at construction (before the first invoke; the
tools arrive at the first invoke), the worker by `build_worker` (`runtime/agents.py`). The `Direct`
topology builds the main agent through `create_browser_agent(..., runtime=<the facade>)`, `DelegatedWorker` the
worker through `build_worker` ("Topology"). Nothing else creates an agent or opens a session.

`core_rails(profile, builders, page_state) -> (ActionPolicy, RepeatedCallGuardRail, InterceptedActionHintRail)`:
`ActionPolicy(builders, profile.policy_rules, page_state)`, `RepeatedCallGuardRail()`,
`InterceptedActionHintRail(profile.messages["retry"])`, in that order. `builders` is kept as given, not copied
("Policy rail"), so the rails can be built before the driver starts over a mapping that fills later; the inputs
exist before the session: the profile's constants and the page state.

`build_agent_parts(session, bundle, *, capabilities, image_support, language, workspace=None, worker=False) ->
AgentParts` (`tools`, `prompt_sections`, `rails`, `builders`):

- `tools`, `prompt_sections`: `ToolFactory(bundle.profile, session.assembly.stack.info).build(capabilities,
  image_support=, session=, page_state=session.page_state, workspace=, language=, worker=)`; with `worker=True`
  the extra tools flagged `worker=False` are left out.
- `builders`: the factory's `AgentTools.builders`, for the runtime's `tool_kind`.
- `rails`: the profile's only, `factory(context)` for each `profile.rails` entry, `context` being the
  `ToolContext` of the same session, profile, driver info, image support, workspace and language. The core rails are
  not part of the parts: the builder of the agent takes them from `core_rails` and registers them before the
  profile's.

`build_agent(core, environ, *, model, capabilities, image_support, language, system_prompt="", workspace=None,
instance=None, registries=None, **agent_options) -> (DeepAgent, DriverSession)`:

1. `assemble_driver(core, environ, instance=, registries=)`;
2. `DriverSession(assembly, BrowserPageState(), profile.messages)`;
3. `build_agent_parts(session, assembly.surface, ...)`;
4. `create_deep_agent(model, system_prompt=<system_prompt and the prompt sections, blank-line separated>,
   tools=, rails=[*core_rails(profile, parts.builders, session.page_state), *parts.rails], workspace=, language=,
   **agent_options)`.

The driver is started before the agent exists (the Playwright MCP native tools exist only after `start()`). A
failure in steps 2–4 closes the session (stop, release). The caller owns the session: `begin_task()` per task,
`close()` when the agent is done. Neither function names a driver, a surface or a provisioner.

`build_worker(session, bundle, *, provider, api_key, api_base, model_name, max_steps, core, capabilities,
tool_result_observer, screenshot_subdir, artifacts_subdir, image_support=False) -> ReActAgent` (`runtime/agents.py`,
async) is the worker's builder, called by `DelegatedWorker.start` with the default `False` ("Topology"):
`build_agent_parts(session, bundle, capabilities=, image_support=, language=WORKER_LANGUAGE, worker=True)`, so
image support decides both the screenshot-only tools (left out without it) and the image blocks in the tool results;
the worker prompt names `browser_take_screenshot` only as "when it is present"; `build_browser_worker_agent(provider=, api_key=, api_base=, model_name=,
max_steps=, tools=parts.tools, screenshot_subdir=, artifacts_subdir=, tool_result_observer=, settings=core,
driver_name=<the driver's display name>)`; then `register_rail` for `core_rails(bundle.profile, parts.builders,
session.page_state)`, the parts' rails and `FinalAnswerRecoveryRail()`, in that order. The model identity and
`max_steps` are the service's own values, not `core`'s: `core` gives the worker temperature, top-p, tool timeout
and logging. `WORKER_LANGUAGE` (`"en"`, written once in `agents.py`, re-exported by `topology.py`) is the language of
every agent the facade's entry points build: the worker, and the main agent in the `Direct` topology.

The task rules (`browser_task_rules()` in `surfaces/browser/tools.py`: `{"en": ..., "cn": ...}`, nine rules under
"Task rules:", the first giving today's local date as `YYYY-MM-DD`) are one block both agents read: the worker
prompt renders the English block before its final-output contract, and the main agent's runtime rail adds it as the
`browser_task_rules` prompt section (priority 85, after the date-picker section) on every model call. Invariants: the
date is the one at render time (per task for the worker, per model call for the main agent); the block stays under
15 lines; the prompt goldens show the date as `<DATE>`.

The worker prompt (the profile's `worker_prompt`, `build_browser_worker_system_prompt(..., driver_name)` in
`surfaces/browser/tools.py`; `build_worker` hands it to `build_browser_worker_agent(..., worker_prompt=)` and the
loop re-renders it per task by `refresh_browser_worker_system_prompt(agent, worker_prompt=, ..., driver_name)`)
names the driver in two sentences, "with
`<driver_name>` tools and approved runtime helper tools only" and "required by a `<driver_name>` action", from
`worker_driver_name(info)`: `DriverInfo.display_name`, else `name`. On Playwright MCP the prompt reads exactly as
before (the `runner` golden); on browser-use it says "browser-use tools" / "a browser-use action". The prompt's
tool names (`browser_snapshot`, `browser_fill_form`, `browser_evaluate`, `browser_tabs`) are the same on both
drivers.

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
| `messages` | `driver_restarted`: "the browser restarted; take a fresh observation"; `driver_lost`: "the browser connection was lost again; the task cannot continue"; `context_changed`: "Call browser_snapshot again and use refs from generation {generation}." (a format string); `retry`: "Then retry the action (take a fresh browser_snapshot if the page changed)." (the last line of the intercepted-action hint) |
| `extra_tools` | `helpers.EXTRA_TOOLS`: `ProbeInteractivesTool`, `ProbeCardsTool`, `BatchTool`, `FindTool`, `RecallOffloadTool` (see "Helpers") |
| `capabilities` | `DEFAULT_BROWSER_CAPABILITIES` (`capabilities.py`) |
| `custom_actions`, `page_metadata`, `policy_probe`, `activate` | `helpers.register_custom_actions`, `helpers.page_metadata`, `helpers.probe_for_policy`, `helpers.activate_page` |
| `semantic_tracker` | `SemanticStateTracker` (`semantic/state.py`) |
| `worker_prompt` | `tools.build_browser_worker_system_prompt` |
| `policy_rules` | `policy.policy_rules(settings)`: the clipboard rules (`POLICY_RULES`) and the upload confinement to the settings' folders (see "Policy rail") |
| `rails` | empty |
| `scene` | `None` (the conformance suite takes `browser_scene()` directly) |

`BrowserContext` also carries `tab_urls` (parallel to `tabs`) and `current_tab` (index of the focused tab); both are
empty when the driver does not report them. The Playwright MCP driver fills them from every header; browser-use does
not yet.

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
`browser_network_requests` (`GetNetwork`). `browser_find` is not a spec: it is the `FindTool` extra tool over the
current observation (see "Helpers").

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

`CAPABILITY_TOOLS` maps every entry of `DEFAULT_BROWSER_CAPABILITIES` (the catalog, in the same module;
unchanged public API) to those of its tool names that a spec or the `browser_find` extra tool serves: `core` equals
`CORE_BROWSER_TOOL_NAMES`, which includes `browser_take_screenshot`; `vision` keeps the four `xy`/wheel tools;
`extended_interaction` keeps
`browser_close`, `browser_drag`, `browser_drop`; `pdf`, `devtools`, `network` keep one tool each; `advanced_code`,
`unsafe_dev`, `config`, `storage`, `testing` are empty. `CAPABILITY_CATALOG` gives each entry the
`(command, ref kind)` pairs of its specs: element tools take `DriverRef` and `Locator`, `browser_file_upload` and
`browser_evaluate` also `None`, the three `xy` tools `PointRef`, all others `None`. `DEFAULT_BROWSER_CAPABILITIES`
is a tuple of the contract's `Capability` (`BrowserCapability` is that type under its old name); its
`advanced_code`, `extended_interaction` and `unsafe_dev` entries are `policy_only`, and
`POLICY_ONLY_BROWSER_CAPABILITY_NAMES` is derived from them. The profile's `capabilities` is this tuple.
Invariants: no two entries share a tool name; `browser_take_screenshot` is in `core` and reaches an agent exactly
when the agent has image support (the factory's screenshot rule, "Tool factory"), so the WebVoyager runner, which
grants `CORE_BROWSER_TOOL_NAMES`, gets it with image input and not without.

### Helpers (`surfaces/browser/helpers/`)

Browser tools and functions built on the driver session through a `ToolContext` (see "Tool factory"); every command
they send goes through `context.execute` / `context.observe`, which record it in the page state. The factory appends
the five tools (`EXTRA_TOOLS`: both probes, the batch, find, recall) to a main-agent build, so the main agent gets
them at its first invoke; a worker build (`worker=True`) leaves out the tools flagged `worker=False` (recall), so
the worker gets both probes, the batch and find; the runtime facade calls `probe_for_policy`, `activate_page` and
`page_metadata` through the profile (`policy_probe`, `activate`, `page_metadata`).
`CUSTOM_ACTIONS` are not tools: they are the controller's custom actions as functions of the context.
`register_custom_actions(controller, context)`, the profile's `custom_actions`, registers them on the controller
when the runtime's session starts, for `browser_custom_action`: first `register_custom_action_specs(controller,
upload_root=context.settings.upload_root)` (the
specs of the three actions and the `list_upload_files` action with its spec over the upload root, `None` = not
configured), then the three handlers bound to the context. The server entry registers the specs on its default
controller the same way, with no upload root, after the builtin actions. The builtin actions (`ping`, `echo`,
`browser_task`, `run_browser_task`) stay in `runtime/controllers/action.py`, which names no browser spec.

Each helper tool declares `flags: ToolFlags` (class attribute): the probes `ToolFlags(requires=(RunScript,))` (`READ`,
not exempt from budgets, progress, offered to a worker), find `ToolFlags()` (it only observes), the batch
`ToolFlags(effect=ACT, requires=(Click, TypeText, PressKey, SelectOption, FillForm, Navigate, Wait, SwitchContext,
RunScript))` (every command its steps build), the recall tool `ToolFlags(exempt_from_budget=True, worker=False)`. A probe or find result is
`ToolOutput(success, data={"content": <payload>, "command": CommandRecord}, error)`: the model reads `content` as
before (a probe's payload dict is rendered as JSON, find's text as is), and `command_record` finds the record. The
record's `effect` is the declared one (`READ`), not the command's: a probe runs `RunScript` but changes nothing the
rails count.

| Helper | Commands | Contract |
|---|---|---|
| `ProbeInteractivesTool` (`browser_probe_interactives`) | `RunScript(code=<page function>, args=<params>)`, a second time if the value is not a JSON object | card equals the runtime helper's; `max_items` 1–30 (default 30); the probe page function of `probes/interactives.py` without its `async (page) => page.evaluate(...)` wrapper; result dict as the runtime helper's (`ok`, `error`, `url`, `title`, `generation_id`, `count`, `elements`, `page_state`, `diagnostics`, `audit`) |
| `ProbeCardsTool` (`browser_probe_cards`) | same | card equals the runtime helper's; `max_cards` 1–12 (default 12); the page function of `probes/cards.py` (its script is the resource `cards.js`, the parameters inlined at build time); selector cache records go in and out as before; primary links registered in `selector_primary_links` |
| `BatchTool` (`browser_batch_interact`) | one command for a step that maps to one; otherwise page scripts and one command per step | see below |
| `FindTool` (`browser_find`) | none, or one `Observe` when the page state has no fresh observation | see below |
| `RecallOffloadTool` (`browser_recall_offload`) | none | `BrowserOffloadRecallTool(context.workspace, language=context.language)`: card and behaviour of `surfaces/browser/offload_recall.py` (the old `browser_move.offload_recall` path is a deprecated alias of the same module); it reads `<workspace>/context/<ContextUtils.offload_session_dir_name(session id)>_context/offload/`, the directory the core's tool-result offloader writes (a path-safe id is its own name; any other id maps to one safe component plus a digest); no record |
| `probe_for_policy(context, source, params)` | `RunScript(code=source, args=params)`, up to two | the F_05 result: elements become targets, `generation_id` added; failure envelope `{"ok": False, "error", "elements": [], "page_state"}` on a `DriverError`, a refused script or a result with `ok: false` |
| `activate_page(context, url) -> bool` | `Observe` when no context is known, then `SwitchContext(<tab whose URL is url, else the current context>)` | `False` on a `DriverError` or no context; the switch is recorded, so the generation advances |
| `CUSTOM_ACTIONS["browser_get_element_coordinates"]` | `Navigate` when `url` is given; one `RunScript` unless only the four coordinates are given | the controller's parameters and aliases; `querySelector` and the bounding box, else the element whose text matches (a Playwright `:has-text("...")` contributes its text); `{ok, source, target, error}` with the controller's error texts |
| `CUSTOM_ACTIONS["browser_drag_and_drop"]` | `Navigate` when `url` is given; elements: one `RunScript` that stamps both, then `Drag(Locator, Locator)` on the stamps; points: `Drag(PointRef, PointRef)` of the last screenshot | `{ok, message, source, target, steps, delay_ms, error}`; offsets only move the reported points, `steps` and `delay_ms` are echoed, not applied |
| `CUSTOM_ACTIONS["browser_set_input_files"]` | `UploadFile(Locator(css), paths)` when `driver_info.actions[UploadFile]` accepts `Locator`; else `Click(Locator(css))` then `UploadFile(None, paths)` | selector default `input[type="file"]`; `{ok, selector, paths, error}`, plus `hint` on a strict-mode error; a `DriverError` in any custom action is `{ok: False, error: "<action> failed: ...", session_id, request_id}` |
| `page_metadata(context) -> (dict, error)` | one `RunScript` (the page-measure function of the metadata script, no arguments) | `url`, `title` from the page state; `tabs` (`index`, `current`, `url`, `title`) from the last `BrowserContext` (current tab from `current_tab`, else the page state's current context; a tab URL from `tab_urls`, else the surface URL for the current tab); `page_position`, `semantic_state` from the script; `({}, error)` on a `DriverError` or an unparsable value |

A probe result whose `url` differs from the page state's (non-empty) URL resets the page state (new generation, no
contexts) before its targets are registered: a script result does not report the address on every driver. Every
dict in the result with `selector_hint` or `generation_id` gets the current generation id. A `DriverError` becomes
the failure envelope (`browser_probe_* failed: <message>`) with `CommandRecord.failed` of the script; a task failure
from the session propagates. The record of a result describes the last script sent (`value` = its raw value).

`BatchTool` (`batch.py`; card and step contract in `batch_steps.py`): card equal to the runtime tool's (golden byte
for byte). Before anything runs: the steps are validated (the controller's per-op contract, then the target contract:
a mutating op needs a `target_id`, a read op exactly one of `target_id` / `selector`; failure `batch_validation_failed:
<first error>` with `validation_errors`, no record); a stale `generation_id` refreshes probe targets (equivalent
current target, else one `RunScript` that checks the stale target's selector) and refuses snapshot refs and
model-written selectors outside conditions; targets resolve through the page state (`resolve_target`; a mutating op
refuses a probe target that is not visible, enabled and actionable). A failure there is
`batch_target_validation_failed: <message>` with `candidate_fresh_targets`.

One step that maps to a command runs as that command alone, on the target's own ref (`DriverRef` of a snapshot
target, `Locator(css)` of a probe target): `click` -> `Click` (a card's primary link -> `Navigate`), `fill` / `type` ->
`TypeText` (`slowly` for `type` or a `delay_ms`), `select_option` with values -> `SelectOption`, `press` without a
target -> `PressKey`, `wait_for_text` -> `Wait(until_text)`, `sleep` -> `Wait(seconds)`, `screenshot` ->
`Observe(include_screenshot, full_page)`; `execution_mode: "primitive"`, `metrics.command` the command type name.

Several steps (or one with no single command) run as a loop (`execution_mode: "commands"`):

1. snapshot targets are stamped on the page (`RunScript` on the `DriverRef`, attribute `data-openjiuwen-target-id`)
   and their page-state locator becomes the stamp selector;
2. preflight: one `RunScript` checks every target of every step (the primary target of click, fill, type,
   autocomplete, select_option, set_checked, the extractions and a press with a locator; the option of
   select_visible_text and of an autocomplete given by target): exactly one match (more fails at once), visible,
   enabled unless an extraction, polled until the step timeout. A failure is `batch_preflight_failed: steps[i]
   <message>` and nothing acts;
3. each step locates its target again with one `RunScript` that finds it (CSS, or page-side stand-ins for role +
   name, label, placeholder, test id and text, with `exact`), checks the same conditions (polling while it is
   missing or hidden), stamps it with a fresh mark and, when asked, focuses it or reads its text, its value or the
   value of the option at an index; the step then sends its command on `Locator(css=<stamp>)`: `Click` (one retry
   when the error reads detached, intercept, not stable or outside of the viewport), `TypeText`; for `type` and
   `autocomplete` a `Click`, `TypeText("")` and `TypeText(slowly)` (typing key by key does not clear); `SelectOption`;
   `FillForm` with a checkbox field for `set_checked`; `PressKey` after focusing. Waits poll a page script (selector
   state, text, load state, first card title, sort state, result count, DOM text change, stable text, with
   `stable_ms`) or, for `wait_for_url` / `wait_for_tab`, `Observe` (tabs from `BrowserContext`), then switch with
   `SwitchContext`; `sleep` is `Wait(seconds)`; `screenshot` is `Observe(include_screenshot)`;
4. a failed step stops the batch unless the step is `optional` or `continue_on_error` is set; a `ConnectionLost`
   always stops it. The batch has the controller's `global_timeout_ms`, checked before each step.

The result keeps the controller's keys (`ok`, `status` completed / partial / failed, `error`, `action`,
`execution_mode`, `session_id`, `request_id`, `generation_id`, `steps` with `index`, `op`, `ok`, `status`,
`elapsed_ms`, `phase`, `error`, `extracted`, `field_provenance`, `conditions` with `observed`, `metrics`), then
`generation_id` (current), `generation_recovered_from` and `page_state`; extracted fields join the page state's field
coverage. The record: one `CommandRecord` for the batch, `command` and `action` of the last command sent, `effect`
the strongest of the commands that ran (page scripts and observations count as `READ`; `READ` < `SCRIPT` < `ACT` <
`NAVIGATE`), `ok` as the payload, `dispatched` / `context_changed` when any command was dispatched / changed the
context, `value` the payload; no record when nothing was sent.

`FindTool`: name `driver_info.tool_name_prefix + "browser_find"`; description and schema from
`driver_info.native_tools["browser_find"]` when present (Playwright MCP: the server's, byte for byte), else the
browser-dialect schema (`query`, `regex: bool`, `limit` 1–100 default 20, `generation_id`). Arguments: `text`
(case-insensitive substring) or `regex` (a pattern, or `/pattern/flags` with `i`, `m`, `s`; `g` dropped), exactly
one; in the browser-dialect form `query` is the text, or the pattern when `regex` is `true`, and a stale
`generation_id` is refused. A bad argument is `### Error`, a newline and the message, and sends nothing (no
record). The search runs over `page_state.fresh_observation`, or a new `Observe` when there is none, over its
`native_text`, inside its fenced `yaml` block when it has one. The answer is `### Result`, a newline and the
server's text: `No matches found for <query>.`, or `Found N match(es) for <query>:` and one snippet per group of
matches (each match with 3 lines of context, merged when they touch, under the lines of its ancestors, `...` where
lines are skipped off the match paths), snippets separated by `----`; `<query>` is `"text"` or `/pattern/flags`.
The record: `command=Observe`, `effect=READ`, `ok=True`, `dispatched` = whether it observed, `value` = the answer
text; a `DriverError` while observing is the same error text with `CommandRecord.failed`.

## Runtime state and recovery (`runtime/`)

Three pieces sit between the model-facing tools and the driver stack; the runtime facade (below) wires them into
the main agent.

### Page state (`runtime/page_state.py`, `PageState`; `surfaces/browser/page_state.py`, `BrowserPageState`)

The core's `PageState` is the surface-neutral generation and target registry; it names no browser field. Generations
keep the `g<n>` format and one counter. A surface that keeps more state subclasses it and hands the class to the
core as `SurfaceProfile.page_state`; `assembly.new_page_state(profile)` builds the profile's (the core's own when
`None`), for `build_agent` and, at first use, for `BrowserService.page_state`, which the facade reads. No core
module imports a surface's page state.

The core keeps: `page_id` (prefix `state-`), `generation` / `generation_id`, `identity`, `last_observation` /
`fresh_observation` / `note_dispatched`, `free_observations`, `screenshot_generation`, `surface_context`,
`contexts`, `current_context`, the ref and selector target registry (`Target`: id, generation, source, locator, ref,
selector, role, name, text, visibility and action flags), `record`, `check`, `generation_of`, `resolve`,
`resolve_target`, `get_target`, `validate_generation`, `advance`, `reset`, and `export()` (`page_id`,
`generation_id`, `interactives`). Hooks a subclass overrides: `_note_surface(surface)` (after each recorded surface
context), `_on_advance()`, `_on_observation(observation)` (after each registered observation, partial ones
included), `_forget_target(target)` (a target trimmed from the history); class attributes `target_type`,
`id_prefix`, and the two hints of the stale-generation and unknown-ref messages (`observe_again`, `snapshot_again`).

`BrowserPageState(PageState)` adds what only the browser has: `url` (the core's `identity` under its browser name)
and `title`, the tab list (`contexts` from `BrowserContext.tabs`), probe and card targets (`BrowserTarget` adds
`href`, `region`, `kind`, `field_name`, `extractable`, `selected`), `register_interactives`, `register_cards`,
`CARD_EVIDENCE_FIELDS` and the field coverage, the requested fields, the captcha/login/access blockers, the primary
links, target refresh and rebind (`refresh_target_id`, `rebind_target`, `recovery_candidates`,
`update_target_locator`), the Playwright text-snapshot registration (`register_ax_snapshot`,
`replace_ax_snapshot`), `observe(url=, title=)`, `advance(url=, title=)`, and `export()` / `export_summary()` with
`url`, `title`, `cards`, `field_coverage`, `blockers` in their former key order. Its messages are the ones the model
read before the split.

- `record(action, outcome) -> outcome`: `outcome` is an `ActionResult`, or the `Observation` of an `observe()`. A new
  generation starts when `result.context_changed`, when the observation's context id differs from the current one,
  when the Jaccard distance between the `(ref, role)` sets of the last and the new observation is above
  `element_change_threshold` (constructor parameter, default `0.5`), or when the surface context's non-empty
  `identity` differs from the known non-empty one without the flag (backstop; for the browser, the URL). A new generation drops `last_observation`, `surface_context`,
  `contexts` and `current_context`: context ids are valid only until the next context change. The returned value is
  stamped: `ActionResult.generation`, `Observation.generation` and every `Element.ref.generation`. Observation
  elements replace the current generation's AX targets (same targets as a complete text snapshot). A non-empty
  `identity` becomes the known one; the browser page state takes the context's `tabs` as `contexts` and its title;
  `observation.context` becomes `current_context`.
- A scoped `Observe` (`action.scoped`) with an `Observation` outcome is partial: no generation check, no change to
  `last_observation`, `current_context`, `contexts`, the address or `fresh_observation`. The refs it shows join the
  current generation (new AX targets; existing ones are kept), the refs it does not show stay valid, as a model
  snapshot registered them before the switch. The returned observation is stamped like any other.
- `free_observations` counts observations that came with an `ActionResult` of a command other than `Observe`.
- `fresh_observation`: `last_observation` while it is of the current generation and no command was recorded after
  it without an observation, nor noted with `note_dispatched()` (a command that reached the surface and failed);
  else `None`. A recorded observation (from `observe()` or free with a result) makes it fresh again.
- `screenshot_generation`: the generation of the last recorded observation with a screenshot (`""` before the first);
  kept across generations, so `check` refuses a point on an older screenshot.
- `check(action)`: every ref in `iter_refs(action)` whose generation is older than the current one →
  `StaleRef(dispatched=False)`, one message per stale ref (`Stale AX ref e1 belongs to g0; current generation is
  g1.`). Refs with an empty generation and unknown refs of the current generation pass; the driver decides. On the
  Playwright MCP dialect every element target the model sends is such a ref (Refs).
- `RefResolver`: `contexts`, `current_context`, `screenshot_generation`, `generation_of(ref)` (the generation the ref
  was last registered in, the current one for a ref never seen) and `resolve(target_id, generation)`
  (`resolve_target`, then `DriverRef` for an AX target, `Locator(css)` for a probe target with a validated selector,
  `ValueError` otherwise).
- `reset()`: a new generation and no contexts or last observation.

### Driver session (`driver_session.py`, `DriverSession`)

`DriverSession(assembly, page_state, messages)` forwards `execute` and `observe` to `assembly.stack`; `messages` must
hold `driver_restarted` and `driver_lost` (read at construction). `page_state` is readable. Sessions are opened by
`assembly.open_session`, which retries the first open once on `ConnectionLost` ("Composition root"); the table
below is what the session does with a connection lost after that.

| Event | Behaviour |
|---|---|
| first `ConnectionLost` in a task (any `dispatched`) | `stop()` (driver errors ignored); then `provisioner.is_alive(device)`: alive → `start()` the same stack; dead → `provisioner.release(device)`, `provisioner.provision(surface_settings)`, `assembly.rebuild(new device)` (a failure here releases the new device), the session's assembly takes the new stack and device, `start()` the new stack; then `page_state.reset()` and the call raises `ConnectionLost(driver_restarted, dispatched=<original>)` |
| restart fails (`start`, `provision`, `rebuild`) | task failure: `BaseError` `DEEPAGENT_RUNTIME_ERROR` with `driver_lost`, the restart error as cause |
| second `ConnectionLost` in the same task | task failure, no restart |
| `begin_task()` | resets the loss counter |
| `stop()` | `stack.stop()`; the device stays (between tasks) |
| `start()` | after `stop()`: `provisioner.is_alive(device)` → alive: `stack.start()`; dead: release, provision again, `rebuild`, start the new stack (the restart above without the stop). A `ConnectionLost` from that start gets the same single restart (stop, liveness, re-provision and rebuild when dead, start) and `page_state.reset()`; a second failure is the task failure above (`driver_lost`, the second error as cause). The loss counter is not touched |
| `health()` | `stack.health()` |
| `close()` | `assembly.close()` with the current (possibly rebuilt) stack and device |

The failed command is never sent again. `health()` is not polled: the facade's `runtime_health` asks it. Liveness is
asked of the provisioner, not of the driver: a driver may start without touching its device (Playwright MCP
connects over CDP lazily).

### Policy rail (`runtime/rails/action_policy.py`, `ActionPolicy`)

`ActionPolicy(builders, rules, page_state)` is a `BaseSecurityRail` on `BEFORE_TOOL_CALL`. `builders` maps a
model-facing tool name to the pure argument decoder of its spec (`ToolSpec.build` / `NativeTool.build`); the rail
keeps the mapping it is given and reads it per call, so a decoder added after construction (the Playwright MCP
native tools exist only after `start()`) is enforced from then on. `BaseSecurityRail.tool_names` is not passed:
it only feeds `get_tools()`, never a check. For a tool in `builders` it parses the arguments (a JSON string or a
mapping), decodes the command with the page state as `RefResolver` and calls `rule.check(action,
page_state.surface_context)` for each rule whose `command_type` matches.
A reason, or a `ValueError` from parsing or decoding, denies the call:

- `extra["_skip_tool_calls"][tool_call_id] = True` (per call; the ability manager returns the prepared result
  without running the tool);
- `tool_msg`: content `### Error\n<reason>`, metadata `executed=False`, `state_changed=False`, `denied=True`;
- `tool_result`: `PolicyDenied(reason)` (`dispatched=False`).

Tools without a decoder pass unchecked.

The browser surface's rules (`surfaces/browser/policy.py`, `POLICY_RULES`) keep the host's clipboard out of pages:
the managed Chrome shares the OS clipboard. Invariants: a `PressKey` whose last token is `v` with a
control/ctrl/meta/cmd/command/ControlOrMeta modifier, or `Insert` with shift, is denied; a `RunScript` whose code
reads the clipboard (`clipboard.read`/`readText`, dotted or bracketed, or `execCommand('paste')`) is denied; the
reason is `CLIPBOARD_DENIED`, which tells the model to read the value from the page and type it. `TypeText` has no
rule: both drivers insert its text literally.

`upload_rules(upload_root, workspace_root)` confine the files a page receives: an `UploadFile` or a `Drop` with any
path outside `BrowserSettings.upload_root` (`BROWSER_UPLOAD_ROOT`) and `BrowserSettings.workspace_root` is denied;
with neither set, any path outside the process cwd is. Wrapping quotes are stripped and `~` expanded; an absolute
path is resolved (symlinks followed) and must lie inside a root; a relative name passes when, joined to some root
and resolved, it lies inside that same root, so `..` cannot climb out of one root into another. A `Drop` without paths passes. The reason names the paths and
the allowed folders and points the model at `list_upload_files`. `workspace_root` is the agent's workspace: the
runtime facade sets it on its settings (`dataclasses.replace`) when it has a workspace and the settings have the
field, so the main agent, the worker, the batch and the custom actions judge with the same folders; an agent built
through `build_agent` alone has none. `policy_rules(settings)` is `POLICY_RULES` plus these two. Relative names are
resolved under the upload root by the browser-use driver only; Playwright MCP resolves them against its own working
directory, and its staging copy only ever sees paths the rule allowed.

Helpers that execute commands without the rail check the same rules: the batch calls
`policy_denial(action, page_state.surface_context, policy_rules(context.settings))` before every command and fails
the step with the reason, so the command never reaches the driver (it has no upload or drop step today);
`set_input_files` checks its `UploadFile` the same way and answers `{"ok": False, "error": <reason>}` without a
command.

## Tool factory (`runtime/tool_factory.py`)

`ToolFactory(profile, driver_info).build(capabilities, *, image_support, session, page_state, workspace=None,
language="cn") -> AgentTools` turns the selected tool specs into one `CommandTool` each; `workspace` and `language`
only reach the `ToolContext`. `capabilities` are resolved entry names (the caller adds `core`, as
`resolve_browser_capabilities` does). The module names no surface, driver or tool (architecture tests).

### Selection and the four rules

For each entry, `profile.capability_tools[entry]` gives the spec names in order (a name is emitted once) and
`profile.capability_catalog[entry]` the `(command, ref kind)` pairs. Per selected spec:

1. **Handled.** Every command of the spec is in `driver_info.actions` (`Observe` always is: every driver
   observes), and when the entry pairs that command only with ref kinds, the driver accepts at least one of them.
   Otherwise the spec is skipped.
2. **Native.** `driver_info.native_tools[spec.name]` exists → its description, schema and `build`.
3. **Profile form.** Else `spec.schema` and `spec.build`, with `spec.description`, else the first
   `driver_info.tool_hints` entry of the spec's commands, else the command's docstring.
4. **Generated.** Else, for a spec with one command and ref kinds the driver accepts: a schema with `ref: string`
   (`DriverRef`), `x`, `y: integer` (`PointRef`, with the resolver's `screenshot_generation`), `css: string`
   (`Locator`), and a decoder that builds the command from the one given. A spec with no ref kinds to generate from
   is skipped.

The tool name is `driver_info.tool_name_prefix + spec.name`. Without `image_support`, a spec whose only command is
`Observe` and whose decoder, called with no arguments, asks for a screenshot is not emitted (today's rule for
`browser_take_screenshot`, without a tool name in the core); with image support that spec is a screenshot-only
tool. Each emitted spec's `prompt_section` is collected. Then
each `profile.extra_tools` factory is called with the `ToolContext` and its tool appended, unless its
`flags.requires` names a command missing from `DriverInfo.actions` (rule: the model is offered only tools the driver
can execute, for extra tools as for specs). An entry that selects no tool, a skipped spec, a skipped extra tool
(logged under the factory's name with the missing commands), and a selected name with no spec that no extra tool
serves (under its name or the prefixed one) are logged once per factory on `tool_logger`.

`AgentTools(tools, prompt_sections, builders)`: `builders` maps each command tool's model-facing name to its
decoder; it is the map `ActionPolicy` takes.

### `CommandTool`

One instance per emitted tool, a core `Tool` (`ToolCard(id=name, name, description, input_params=schema)`).
`invoke(args)`:

1. `action = build(args, page_state)`; a `ValueError` returns the failure `### Error\n<message>` and nothing is sent;
2. `page_state.check(action)`; a `StaleRef` makes one `session.observe(Observe())`, recorded, and the error is
   rendered with that fresh observation; the driver never sees the stale command;
3. `context.execute(action)`, or `context.observe(action)` for an `Observe` (see `ToolContext`): the session runs it;
4. the same call records it with `page_state.record(action, outcome)`;
5. render: an `ActionResult` with `renderer.result`; an observation with `renderer.observation` when the tool's only
   command is `Observe` and it is not a screenshot-only tool, else with `renderer.result` on `ActionResult(ok=True,
   value=<screenshot>, observation, surface)` (the tab list of `browser_tabs list`; the image alone for a
   screenshot-only tool); a `DriverError` with `renderer.error(action, error, None)`, except a `StaleRef` the driver
   raised with a fresh observation (its retry observed afresh): that observation is recorded
   (`page_state.record(Observe(), observation)`, so its refs get the current generation) and the recorded one is
   passed to `renderer.error`.

The output is a `ToolOutput`: `success = ok`; `data = {"content": <text blocks joined by newlines>, "command":
CommandRecord}`; on failure `error` is the same text. With `image_support`, image blocks go to `data["multimodal"]`
as `{"type": "image", "source": <profile name>, "source_path": <tool name>, "mime_type": "image/png", "data_url"}`
and the content gets the note `N image(s) attached as multimodal input.`, the shape MCP tools deliver; without it
image blocks are dropped. A task failure from the session (`BaseError`, second lost connection) propagates.

### `CommandRecord`

Frozen; never parsed from text.

| Field | Value |
|---|---|
| `command` | the command type |
| `effect` | its `Effect`; an extra tool may give its declared one instead |
| `ok` | `ActionResult.ok`; `False` for an error |
| `dispatched` | `True` for a result; `error.dispatched` for an error |
| `context_changed` | `ActionResult.context_changed` |
| `error` | the `DriverError` type name, or `None` |
| `blocked_by` | `ActionIntercepted.by` for that error, else `None` |
| `surface` | the result's surface context; for an error, the page state's |
| `value` | `ActionResult.value`; for an `Observe`, the observed elements (stamped `Element`s) |
| `generation` | the generation stamped by `record` (the page state's current one for an error) |
| `action` | `masked(command)`: the command with its sensitive fields masked |

`message_metadata` gives the tool-message metadata keys the context processors read: `success = ok`, `executed =
ok` for a `READ` record (a find over a current observation sent nothing but answered, as the server call did) and
`dispatched` for any other effect, `state_changed = context_changed or (effect != READ and (ok or error == "ActionTimeout"))`, `denied =
False` (denials come from `ActionPolicy`). A tool cannot set `ToolMessage.metadata` (core builds the message from
`render_for_llm` after `invoke`); a rail copies `message_metadata` onto it in `after_tool_call`. `BrowserRuntimeRail`
adds `effect` (the kind's `Effect` value) for the surface's budgeted tools, that is the factory tools and the extra
tools without `exempt_from_budget`; a message without `effect` belongs to a tool the context processors leave alone.

`CommandRecord.of(action, result, *, effect=None, value=None)` builds the record of a result,
`CommandRecord.failed(action, error, page_state, *, effect=None)` the record of a `DriverError`; `CommandTool` and the
helpers use both. `command_record(tool_result)` returns the record from a tool output (`ToolCallInputs.tool_result`
in a rail), or `None`: arguments that decoded to no command, a tool that sends no command (recall), or a tool that
leaves no record. A rail treats a tool message without a record as a failed call that sent nothing.

### `ToolContext`

`ToolContext(driver, page_state, renderer, driver_info, settings, image_support, workspace=None, language="cn")`:
what `profile.extra_tools` and `profile.rails` factories receive; `driver` is the `DriverSession`. The factory builds
it from its arguments, `profile.renderer` and `profile.settings`.

- `await context.execute(action)`: `driver.execute`, then `page_state.record`; a `DriverError` with
  `dispatched=True` is noted (`page_state.note_dispatched()`) before it propagates. The one way a tool or helper runs
  a command (the batch tool too).
- `await context.observe(command)`: `driver.observe`, then `page_state.record`.

`ToolFlags(effect=READ, exempt_from_budget=False, progress=True, worker=True, requires=())`: what an extra tool
declares, as its `flags` attribute, for the rails that today decide by tool name (state change, replan budgets,
progress) and for the factory (`worker`: offered to a delegated worker too; `requires`: the command types the tool
sends, all of which the driver must handle for the tool to be offered).

## Rails on command records

Two rails read `CommandRecord`s, never tool text; the text-based rails of the same names are gone.
`build_agent_parts` hands them to the main agent (registered at its first invoke) and to the worker. Neither adds a
model call: each rewrites the tool message in `after_tool_call`, or empties the tools of the next model call
(invariant: a rail never adds a model round trip).

### `RepeatedCallGuardRail` (`runtime/rails/repeated_call_guard.py`)

State reset per invoke (`before_invoke`, and the first `before_model_call` that finds none: a deep agent fires
`before_invoke` on its outer callback context, and its inner agent's model and tool hooks share another `extra`),
the streak counted in `after_tool_call`, the finish section and the withholding in `before_model_call`.

- **Identity.** Two calls are identical when their commands and their results are: `record.action` (masked) and
  `(ok, error, value, surface)`. A `RunScript` compares its code without whitespace and with digits replaced by
  `#`, and its value as sorted JSON with digits replaced; an `Observe` compares its observed elements. A message
  without a record compares by tool name and arguments (canonical JSON; non-JSON text as is).
- **No-op scripts.** A `RunScript` whose whitespace-free code only returns literals, the document title or its
  address (the same pattern as before) counts toward a no-op streak, whatever its result.
- **Counting.** The 3rd identical (or no-op) call in a row appends `### Repeated call` to the tool message, once;
  after the 5th the next model call gets no tools and a finish instruction. Any other call resets the streak.
  Calls of one model turn are counted in the order their `after_tool_call` runs, like separate turns.

### `FinalAnswerRecoveryRail` (`runtime/rails/final_answer_recovery.py`)

When the delegated worker ends without the final JSON, the worker loop asks once more in the same conversation
(`recovery_message(reason)`) and the rail stops that invoke after one model call, running none of its tool calls.
Invariants: the recovery request keeps the run's tool list (dropping it changes the prompt prefix and misses the
cache for the whole conversation); `non_json_output` (a prose ending) gets `RESTATE_MESSAGE`: restate the answer
already given as the JSON, without re-checking, changing it or adding caveats, `ok=false` only if that reply said
the task could not be completed; `empty_output` gets `RECOVERY_MESSAGE` and `max_iterations_reached` the step-limit
sentence plus `RECOVERY_MESSAGE` (no answer to restate: report what was found and what is missing).

### `InterceptedActionHintRail` (`runtime/rails/intercepted_action.py`)

`InterceptedActionHintRail(retry)` takes the surface's retry sentence (`profile.messages["retry"]`; the core names
no browser tool). For a record with `error == "ActionIntercepted"` and a non-empty `blocked_by`, and a string tool
message, it replaces the message with `explain_blocked_command(record, retry)`:

```
### Error
<command type> not delivered: <target> is covered by <blocked_by>.
<hint>
<retry>
```

`<target>` is the `DriverRef` id, the `Locator` css (or text), `(x, y)` for a `PointRef`, else `the target`.
`<hint>` is the cookie-consent hint when `blocked_by` matches the consent pattern, else the generic one; `<retry>` is
the sentence given at construction. The tool
output (`tool_result`) keeps the driver's own error. Other records and messages without a record are left alone.

## The browser agent runtime (`runtime/session.py`)

`BrowserAgentRuntime` is the facade `create_browser_agent` builds and the WebVoyager runner constructs the same way
(`provider, api_key, api_base, model_name, mcp_cfg, guardrails, instance=None, allowed_tool_names=None, *,
core_settings=None, browser_settings=None, workspace=None, language="cn"`). It owns one `BrowserPageState` for its
lifetime, hands it to its `BrowserService`, and from `ensure_runtime_ready()` on works on the service's
`DriverSession`: one session for the main agent and the worker. `mcp_cfg` reaches the session through the service
(`BrowserSettings.mcp_server`); `browser_settings` reach it as the surface settings override.

- `ensure_runtime_ready()`: `service.ensure_runtime_ready()`; when the service's session is a new one, a
  `ToolContext` for the runtime's own calls (no image support, the given workspace and language), the controller's
  builtin actions registered, the profile's `custom_actions(controller, context)` called, and the cached parts
  dropped.
- `ensure_started()` and `run_browser_task(task, session_id=, request_id=, timeout_s=)`, the runner's entry points,
  delegate to the topology: `ensure_started()` picks it once, `topology_for(service.core)` ("Topology",
  `BROWSER_TOPOLOGY`), and calls its `start(self)`; `run_browser_task` is `ensure_started()` then
  `topology.run(self, task, session_id, request_id, timeout_s)`. The facade holds no agent: `DelegatedWorker` puts
  the worker in `service.browser_agent`, `Direct` keeps the main agent itself.
- `surface -> SurfaceBundle`: `assemble_surface(service.core, process_environ(), instance=, settings=<the browser
  settings>)`, built once at first use, before any driver, so the rails' profile constants exist at the main agent's
  construction. Invariant: it agrees with the session's bundle on `messages` and on what `policy_rules` decide
  (both come from the surface factory and the same settings, the workspace included); the session's bundle, which also carries the MCP config, is what the worker and the
  parts are built from.
- `builders -> Mapping[str, Builder]`: the decoders of every factory tool built so far, one live mapping updated by
  `agent_parts`; `ActionPolicy` reads it per call ("Policy rail"), `tool_kind` too.
- `core_rails() -> (ActionPolicy, RepeatedCallGuardRail, InterceptedActionHintRail)`: `assembly.core_rails(surface.profile,
  builders, page_state)` ("Agent construction"); `create_browser_agent` queues them at construction. A policy built
  before the session exists enforces the tools built after it, through the live mapping.
- `register_runtime_tools()`: `browser_custom_action` and `browser_list_custom_actions` in `Runner.resource_mgr`
  (tag `agent.playwright.runtime`), once; a tool that already exists there is accepted. `DelegatedWorker.start`
  calls it; the main agent never holds these tools. They are the only `Tool` wrappers of the facade
  (`runtime_tools.py`); cancellation and health are reached through the facade's methods, as the server entry point
  does. Neither card names a driver; the worker's `AgentCard` renders the session driver's display name.
- `main_agent_answer(session_id, request_id, *, result=None, error=None)`: the runner's answer dict from a main-agent
  run, for `Direct.run`: `ok` (no error, `result_type` not `"error"`), `final` (the output), `page` from
  `page_state.surface_context`, `screenshot=None`, `error`, and `status` / `progress` from the rail's
  `authoritative_browser_result` / `progress_state` when present. It lives on the facade because the answer's
  keys are the facade's run contract and its `page` comes from the facade's page state.
- `dedicated_log` is settable: `Direct.start` sets it `False` so the runner's agent writes to the process log, where
  the runner reads model usage; agents from `create_browser_agent` keep `True`.
- `runtime_health()`: `session.health()` when the stack runs, else `ok=False, detail="not started"`, with
  `started` and the model settings.
- `shutdown()`: `service.shutdown()`, the facade's session view dropped, the topology dropped (`drop()`, so a
  `Direct` agent is forgotten and the next `ensure_started` picks and starts a topology again), then
  `OpenAIModelClient.aclose()` so the next event loop (the runner runs one per task) gets fresh clients.
- Capabilities are derived from `allowed_tool_names` on first read: the entries of the profile's catalog whose tool
  names are all allowed (the inverse of `resolve_browser_capabilities`); with no allowlist, the default entry's
  tools, and the service then holds `allowed_tool_names=None`. The semantic-state tracker is the profile's
  `semantic_tracker()`, made on first use; the three captures call `observe(state, action_group_id=..., changed=)`
  with `changed` true when the generation or the last observation moved since the previous call. The browser
  tracker scores a repeated state as `progress` when `changed` (a dialog or a cart has no semantic field), and a
  revisit of an earlier state as a revisit whatever `changed` says.
- `agent_parts(*, image_support, language)`: `build_agent_parts(session, assembly.surface, ..., language=)`, built
  once per `(image_support, language)`; the receiving agent decides the language ("one language per agent": the
  rail passes the agent's own, `build_worker` passes `WORKER_LANGUAGE`), and the facade's constructor `language`
  (default `cn`) serves only its own tool context (custom actions, the policy probes). The parts hold no core
  rails. The facade merges the parts' `builders` into its live mapping and keeps each extra tool's `flags` to
  answer `tool_kind`.
- `tool_kind(tool_name, tool_args) -> ToolKind`: for a factory command tool, the command decoded from the arguments
  with the page state as resolver (`action`, `effect = action.effect`, `fields` = the schema's property names;
  arguments that do not decode leave `action` `None`, `effect` `ACT`); for an extra tool its `ToolFlags`
  (`helper=True`); for an unknown tool `ToolKind()` (`known=False`, `ACT`). `command` and `read_only` are derived.
  `progress` is `True` for every factory tool and as declared for a helper.
- `capture_browser_state(action_group_id="")`, the browser state processor's provider: `page_state.fresh_observation`,
  else one `Observe()` through the context (a `DriverError` becomes `dom_error`, `page observation failed: …`);
  `observations_reused` counts the reuses; then the profile's `page_metadata(context)`. The result keeps its keys (`ok`, `error`,
  `url`, `title`, `tabs`, `page_position`, `semantic_state`, `semantic_progress`, `field_coverage`, `page_state`,
  `dom=""`, `dom_error`, `audit`). `capture_reconciliation_browser_state` runs `page_metadata` only;
  `capture_compact_browser_state` sends nothing. A task failure from the session propagates.
- `probe_for_policy(source, params)` and `activate_page(url)`, the `DecisionPolicyModel` hooks, call the profile's
  `policy_probe` and `activate` on the context; `run_custom_action` binds the runtime to the controller and dispatches.
- `resolve_model_target_id`, `target_recovery_state`, `resolve_primary_link`, `export_page_state`,
  `set_task_requested_fields`: the page-state views the rail reads.
- `acquire_task_resources`: the service binding (which calls `session.begin_task()`); `release_task_resources`:
  the service release (the last binding stops the driver stack and keeps the device) and `page_state.reset()`;
  `reset()` closes the session through the service and resets the page state.

Timing: the driver starts and the tools are added at the first invoke, in `BrowserRuntimeRail.before_invoke`,
where the MCP tools were added before; the deep agent is built with no browser tools and gets both the command
tools and the extra tools there (`browser_find` takes its description from the driver's native tools, which exist
only after `start()`). The rails are queued at construction (`create_browser_agent`), so every rail, the
repeated-call guard included, sees the first invoke's `before_invoke`.

### Topology (`runtime/topology.py`)

Who runs a task behind the facade's entry points. `Topology` is a `Protocol`: `name`, `async start(runtime)`,
`async run(runtime, task, session_id, request_id, timeout_s) -> dict`, `drop()`. `topology_for(core)` maps
`core.topology or default_topology(core.driver)` to a strategy and raises `ValueError` naming the driver and the
name for a registered default no strategy implements. Neither strategy names a driver; the module carries no
browser word (it is a core module under the browser-word ratchet), which is why the answer mapping stays on the
facade (`main_agent_answer`).

- `Direct` (`name="direct"`, the browser-use default): `start` builds the main agent once, through
  `create_browser_agent(model, language=WORKER_LANGUAGE, temperature=core.worker_temperature,
  max_iterations=guardrails.max_steps, workspace=runtime.workspace, browser_capabilities=runtime.capabilities,
  runtime=runtime)` with a `Model` from the service's provider, key, base and model name (`temperature` / `top_p`
  from the core settings' worker values), after setting `runtime.dedicated_log = False`; `start` makes no model
  call ("Invariant (image probe)"). No session is opened:
  it opens at the agent's first invoke (`BrowserRuntimeRail.before_invoke`), as for any `create_browser_agent`
  agent. `run`: `service.session_new(session_id)`, a request id, `await probe_image_support(<the same Model>)`
  (the agent reads the verdict from the probe cache per model call, "`BrowserRuntimeRail`"), then
  `Runner.run_agent(agent, {"query": task,
  "conversation_id": "<sid>.direct.<rid>.<uuid>", "request_id": rid})` under `asyncio.wait_for(timeout_s or the
  guardrails' timeout)`; the result, a timeout (`task_timeout: exceeded <n>s`) or an exception becomes the answer
  through `runtime.main_agent_answer`. One attempt per task: the worker loop's retries are worker guardrails.
  `drop` forgets the agent. Invariant: the conversation id is the context engine's session id and names the offload
  directory, so its separators are path-legal on every OS (`:` is not on Windows).
- `DelegatedWorker` (`name="delegated_worker"`, the Playwright MCP default): `start` is
  `runtime.ensure_runtime_ready()` (the session opens here), then, when `service.browser_agent` is `None`,
  `service.browser_agent = await build_worker(session, session.assembly.surface, provider=, api_key=, api_base=,
  model_name=, max_steps=guardrails.max_steps, core=service.core, capabilities=runtime.capabilities,
  tool_result_observer=service.observe_worker_tool_result, screenshot_subdir=, artifacts_subdir=,
  image_support=<the strategy's verdict, False until a probe said yes>)`, records the tool names the worker was built
  with, then `runtime.register_runtime_tools()`. `run` first awaits `probe_image_support` (as `Direct.run` does);
  on the first yes it sets image support and empties the slot, remembering the tools the caller removed from the
  started worker (the runner drops the helpers between `ensure_started()` and the task), so the worker is rebuilt
  with the screenshot tool and image results and without those tools. Then `service.run_task(task=, session_id=,
  request_id=, timeout_s=, worker=)` with `worker` an async callable that runs `start(runtime)` and returns
  `service.browser_agent`: after a `reset()` the slot is empty and the next attempt rebuilds the worker on a fresh
  session. `drop` does nothing:
  the worker lives in the service's slot, which the service clears on reset and shutdown.

The worker's answer carries `screenshot` only as something a vision model reads as an image: an http(s) link, a
data URL whose bytes are an image, or a local file (resolved under the runtime working directory, then the process
cwd) that is a PNG, JPEG, GIF or WebP, turned into a data URL with the MIME type its bytes show
(`service._normalize_screenshot_value`). Anything else, a path that names no file included, becomes `None`: callers
(the WebVoyager judge) send the value to a model as an image.

Invariant (image probe): `start` makes no model call on either strategy. The runner calls `ensure_started()` before it
redirects stdout into the task log, and the first model call binds the model logger to the stdout of that moment, so a
call in `start` sends every usage line of the task to the process stdout. `Direct.run` awaits `probe_image_support`
before the agent runs: a background probe (`schedule_image_support_probe`) dies with its event loop, and the runner
runs one `asyncio.run` per task. A conclusive verdict is cached per `(api_base, model_name)` for the process, so later
tasks pay nothing; an inconclusive probe (`None`: timeout, call failure, empty answer) is not cached, counts as no
image support, and is tried again at the next run.

The runner reads `runtime._service.browser_agent` between `ensure_started()` and `run_browser_task` to remove the
helpers; on `Direct` the slot is `None` and its guard skips the removal, so the main agent keeps the helpers it is
built with. `create_browser_agent` outside the facade's entry points is not governed by the setting: that agent
always calls the tools itself.

### `BrowserRuntimeRail` (`surfaces/browser/rails/runtime_rail.py`)

The main agent's rail, built with the runtime and registered by `create_browser_agent`. It reads `ToolKind`s before
a call and `CommandRecord`s after it, never tool text. The class holds the hooks, the lifecycle and the target
normalisation; the rest of its methods are five plain mixin classes it inherits, in MRO order `_TerminalMixin`
(`rails/terminal.py`: model-protocol recovery, the terminal result), `_ResultsMixin` (`rails/results.py`:
tool-message rewriting), `_ProbeResultsMixin` (`probes/results.py`), `_PhaseMixin` (`semantic/phases.py`: phases,
strategy, completion) and `_EvidenceMixin` (`semantic/evidence.py`), then `AgentRail`. Invariants: no mixin
defines a hook name (`before_*`, `after_*`, `on_*`), so `AgentRail`'s dispatch sees the class alone; a mixin never
imports `runtime_rail.py` (the constants two of these modules share live in `rails/keys.py`, which imports nothing
from the package); the rail imports the facade (`runtime/session.py`), never the reverse.

- Image support: `should_enable_read_image_multimodal` (`openjiuwen/harness/rails/_multimodal.py`: the agent's
  `enable_read_image_multimodal`, else the model's `supports_vision`, else the probe cache, `None` counting as
  unsupported) of the agent `before_invoke` saw. Invariant: the three readers (the `agent_parts` key in
  `before_invoke`, the image-capability prompt section in `before_model_call`, the batch screenshot gate in
  `before_tool_call`) resolve against that one agent. On a deep agent only `before_invoke` / `after_invoke` fire on
  the `DeepAgent`; the model and tool hooks fire on its inner `ReActAgent`, which has no `deep_config`, so resolving
  `ctx.agent` there always answered "unsupported". A rail whose hooks run with no `before_invoke` first resolves
  `ctx.agent`.
- `before_invoke`: the browser log context for the invoke when `runtime.dedicated_log` is true (reset in
  `after_invoke`), `ensure_runtime_ready()`, then `agent_parts(image_support=<the agent's read-image support>,
  language=<the agent's own, resolve_language(agent.deep_config.language)>)`: a session that cannot start (or
  restart) there raises core's `AbortError` with the start error as `cause`, so the invoke fails and the caller
  sees that error (the callback framework re-raises the cause; the runner's Direct answer is `ok=False` with its
  text); no tool is added and no model call is made. Any other callback failure the framework logs and swallows,
  and the invoke would go on with no tools: every tool through `ability_manager.add_ability(card, tool)` on every
  invoke (a re-add refreshes the instance). The rail registers no rail: the core rails are queued at the agent's
  construction (`create_browser_agent`), so `RepeatedCallGuardRail.before_invoke` runs at the first invoke; the
  guard's first-model-call fallback stays for a guard registered later (the worker's `register_rail` path).
- `before_tool_call`: a factory tool whose schema addresses elements by `target`, `startTarget` or `endTarget`
  (the Playwright MCP dialect) has its arguments normalised: the `ref`, `startRef` and `endRef` aliases renamed,
  `[ref=e12]` and `ref=e12` reduced to the ref, a `target_id` resolved through the page state to the target's ref
  or selector (with `element` filled from its text), `generation_id` dropped, and `field`, `fields` and `element`
  dropped when the schema does not declare them (the evidence hints are read first; every server schema forbids
  extra properties). A `Click` whose target is a card's primary link becomes the navigation tool (`browser_click`
  → `browser_navigate` in the name) with `{"url": <link>}`; a `t_g…` target left without a locator is denied. The
  browser-dialect tools (`target_id`, `ref`, `selector`, a required `generation_id`) are left to their decoders.
  Then the schema check, the batch screenshot gate without image support, and the phase budget as before, with the
  kind in place of the name.
- `after_tool_call`: a `PolicyDenied` result or the rail's own denial sets `executed=False, denied=True`. Otherwise
  `command_record(tool_result)`: with a record the tool message gets `record.message_metadata` (O5), a partial
  batch with a successful mutating step counting as `state_changed`, and `effect` for a budgeted kind; without one,
  a known tool sent nothing
  (`executed=False`) and an unknown tool is read from `ToolOutput.success`. The evidence machinery reads one dict:
  a helper's payload as is, else `{ok, error, result, url, title, generation_id, page_state}` with `result` the
  script value of a `RunScript` or the rendered text, and `url`/`title` from `record.surface`. An `Observe` that is
  not a screenshot replaces the message content with the compact page state; a `NAVIGATE` effect appends
  `<browser_page_state>`; `Observe` and `RunScript` results and read-only helpers that count against budgets are
  bounded at 12,000 characters. Progress is persisted for tools with `progress`.
- Classifiers by kind: `NAVIGATE` → navigation; `TypeText`, `PressKey`, `SelectOption`, `FillForm`, `UploadFile` →
  form; `RunScript` → script, its intent from the code as before; a read-only tool that counts against budgets →
  extraction; a batch by its ops; `Scroll` → viewport exploration; `Observe` and read-only helpers → target
  discovery (`browser_probe_cards` → structured extraction); `Click`, `Hover`, `Drag`, `Drop` → interaction;
  `exempt_from_budget` → no phase or replan budget; read-only recovery for `READ`, `SwitchContext`, a script that
  does not mutate, a batch of read-only ops.
- Phase budgets are charged on what each command did. Invariants: a known `ACT` command that is not a form or
  filter command keeps the current phase, except while extracting, when it goes to `form` (created on demand); an
  `ACT` or `NAVIGATE` command whose record is ok and shows `context_changed`, or a generation other than the one
  stored when the call started, gets its attempt back (`_record_phase_result(..., progressed=True)`); in the
  extraction phase a `RunScript` costs one attempt per distinct code (a sha256 prefix kept in the phase's
  `scripts`), and a successful read that adds evidence (a new `structured_evidence` entry, such as a page
  observation from another source or with other text, or a newly covered field) gets its attempt back. A refund
  only returns an attempt the call was charged: the rail stores `charged` per call (the phase attempts grew in
  `_consume_phase_budget`) and passes it as `_record_phase_result(..., charged=...)`, so a repeated script is
  never refunded. Failed reads, reads that add nothing and stalled actions count; a mutating page script records
  no page observation, so it counts unless it returns a requested field. They still exhaust their phase, and the budget check stays before the call. Exhaustion is terminal: status
  `partial`, blocker `<phase>_phase_budget_exhausted`.

### Context processors and the status log

They read tool-message metadata and command records, never tool names or result text. All three live under
`surfaces/browser/`. The two processors register themselves with the context engine at import
(`@ContextEngine.register_processor()` on the class); the registration point is the import of
`state_processor.py` and `working_context/processor.py`, which `browser_agent.py` performs. `surfaces/browser/__init__.py`
stays side-effect free: importing them there would pull `openjiuwen.core.context_engine` and the working-context
store into every `SURFACES.create("browser")`, `browser_surface_settings()` included.

- `BrowserStateContextProcessor` (`state_processor.py`) groups the completed tool calls of one model
  turn by their messages' `effect`: a call with an effect other than `READ` whose message has `state_changed` not
  `False` is a mutation (a full capture after the group, or the reconciliation capture when its `success` is
  `False`); a group of `READ` effects alone with `success` not `False` is an observation (the compact capture, no
  browser round trip). Messages without `effect`, with `denied`, or with `executed=False` are skipped. The name sets
  and the JSON content parsing are gone.
- `BrowserWorkingContextStore` (`working_context/store.py`) retains a tool error from the message when the metadata
  says `success` is `False` (the `error` field of a JSON payload, else the text) or the content starts with one of
  the harness's execution-error prefixes; it no longer scans for `ok`, `success`, `isError` or `success=false` text.
- `BrowserSubagentStatusLogger` (`status_logging.py`) takes `command_of(tool_name, tool_args) -> str`, the rail's
  decoder (the command type name, `""` for a helper or an unknown tool), and shapes `tool_start` arguments by the
  command (`RunScript`: code length only; `Navigate`: the URL without its query) and `tool_end` results by the
  command record (`kind="command"`, `command`, `effect`, `ok`, `dispatched`, `error`, the text length, `line_count`
  for `Observe`, the value kind and length for `RunScript`). A helper's dict payload is shaped by its keys;
  `browser_batch_interact`, the surface's own extra tool, keeps its step summaries by name.

## Drivers

Both drivers fill `BrowserContext` (`surfaces/browser/context.py`). While a JavaScript dialog blocks the page,
`dialog` is `"<type>: <message>"` (`"confirm: Proceed?"`) in every observation and in every action result
that reports it, and `observe()` returns what it can read without the page (the context, tabs, URL, the dialog;
no elements, no screenshot) instead of raising. Outside a dialog `dialog` is `None`.

### browser-use (`drivers/browser_use/`)

`BrowserUseDriver(HandlerDriver)` runs commands through the browser-use sidecar: a separate Python process
(`sidecar/`, its own venv; the agent-core process never imports `browser_use`) reached over NDJSON on stdio
(`transport.py`). `info`: `name="browser_use"`, `display_name="browser-use"`, `surfaces={"browser"}`,
`observes={elements, screenshot, native_text, surface}`, `versions` from the sidecar's `connect` reply.

- Factory: `create_browser_use_driver(device, surface_settings)` builds `BrowserUseConfig(cdp_url=device.endpoint,
  sidecar_python=..., upload_root=...)`, the last two from the `BrowserSettings` (`None` settings: neither);
  `start(config=None)` spawns the sidecar with that interpreter (`None`: `.venvs/browser-use` in the repo) and
  attaches to that CDP endpoint (an explicit config overrides). `UploadFile` and `Drop` resolve relative paths
  against `upload_root` with `drivers/browser_use/upload_paths.resolve_upload_file_paths`; the Playwright MCP
  driver does not (its server resolves against its own roots), so relative upload names resolve on browser-use
  only. No surface code rewrites upload paths; confining them is a surface policy rule's job.
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
  capture when `full_page`) and `BrowserContext` (url, title, tabs, `tab_urls`, `current_tab`, PDF viewer, scroll
  pixels). A scoped `Observe` is `Unsupported(dispatched=False)`: browser-use observes whole pages, and the browser
  dialect's `browser_snapshot` has no `depth` or `target`. The title is read from `document.title`, because
  browser-use's own title lags behind it; the sidecar's
  title is kept when the page runs no script. While a dialog is open the sidecar answers `observe` from
  browser-use's cached target info only (URL, title, tabs) with `dialog` and no elements; the driver then runs no
  script and takes no screenshot.
  The focused context (`current_tab`) is the first tab whose URL is the page URL; with none, `current_tab` is
  `None` and the context has an empty id. `generation` stays empty.
- Element and keyboard commands run in the sidecar as one action each (wire version 4). The action resolves its
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
- Navigation replies (`Navigate`, `Back`, and the sidecar's `go_forward` / `reload`) carry `dialog`, the dialog open
  when the reply is built; `surface` is `BrowserContext(url, title, dialog)`. `OpenContext`, `SwitchContext` and
  `CloseContext` list the tabs after the command (`list_tabs`, one more request) and report `tabs`, `tab_urls` and
  `current_tab` in `surface` (switch and close: the title of the active tab); a failure of that list after a switch
  or close is `dispatched=True`, since the tab command already ran.
- Errors: each sidecar error code maps to one typed error. `dispatched=False` only for errors the sidecar raises
  before any input reaches the page: `StaleIndexError` and `StaleNodeError` (→ `StaleRef`; a node the CDP says is
  not in the document, "does not belong to the document" / "No node with given id"), `ElementNotFound`,
  `ActionIntercepted` (with `by`), `DriverNotConnected` (→ `ConnectionLost`) and `DriverUnsupported` (→
  `Unsupported`). Each action marks where its input starts; a stale or missing node after that point reaches the
  driver as a plain `DriverError(dispatched=True)`. A browser that went away reaches the sidecar as browser-use's
  own error once its reconnect gives up ("Reconnection failed - CDP still not connected", "Reconnection wait timed
  out", "browser not connected"), and a target that detached under it fails the connect with "No valid agent
  focus available - target may have detached"; an unclassified error whose message says `not connected`,
  `Reconnection wait timed out`, `No valid agent focus available` or `target may have detached` becomes
  `ConnectionLost(dispatched=True)`, so the driver session restarts (at `start()` too). Other codes it cannot
  classify become `DriverError(dispatched=True)` and are logged raw. A script error carries the first line of the
  JavaScript error (`TypeError: …`), not CDP's bare "Uncaught".
- Script source. The page-wide `evaluate` and the element-scoped script evaluate `(code)` and call the value only
  when it is a function (the element-scoped one with the element and `args`; the element is also in scope for an
  expression).
- Locators. A `Locator` goes to the sidecar as a CSS selector for `querySelector`; Playwright-only syntax
  (`:has-text()`, `:text()`, `:text-is()`, `:visible`, `>>`, an engine prefix such as `text=` or `xpath=`) is
  `Unsupported(dispatched=False)` with a message naming the supported forms (a ref or `target_id`, plain CSS).
- A hung page. Two transport timeouts in a row (requests that got no reply in 30 s) raise
  `ConnectionLost(dispatched=True)` instead of the second `ActionTimeout`, so the driver session makes its single
  restart; any reply, or `start()`, resets the count, and the driver's own `Wait` timeout does not count.
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
- **Header** (`PageHeader(url, title, tabs, focused, tab_urls)`). From `### Page` (`- Page URL:`, `- Page Title:`; the title
  is missing while a dialog is open) and `### Open tabs`, or from a `browser_tabs` result list (`parse_tabs`). A
  tab is `ContextRef(id=<index>, kind="tab", title)`; the `(current)` one is `focused`. The server lists tabs only
  when more than one is open, so a `### Page` without `### Open tabs` means one tab, `"0"`. `tabs` always includes
  the focused tab; `tab_urls` runs parallel to it (the page URL for the single tab).
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
  - `surface`: `BrowserContext(url, title, tabs, tab_urls, current_tab, dialog)` from the header (`current_tab` is
    the position of the focused tab); `None` when the result has no
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
  `drivers/playwright_mcp/config.playwright_mcp_config` (the builder `runtime/config.py`'s public functions call),
  with `device.endpoint` as the CDP endpoint. A config in `surface_settings.mcp_server` keeps its `params` (command,
  args, cwd, timeout, env) and `client_type`; the factory's server name and id win, so the tool prefix never depends
  on the caller. In the given env `PLAYWRIGHT_MCP_DEVICE` is dropped (no device emulation over CDP) and, when the
  device has an endpoint, `PLAYWRIGHT_MCP_CDP_ENDPOINT` is set and `PLAYWRIGHT_MCP_BROWSER` defaults to `chrome`, as
  the old service injection did. The composition root never sees the command line.
- `start()` owns one MCP client (`BrowserMoveStdioClient`, or `BrowserMoveStreamableHttpClient` for
  `streamable-http`) and calls it directly, never through `ToolMgr`; it reads `tools/list` once and keeps each
  mapped tool's description as `DriverInfo.tool_hints`, and every listed tool that has a decoder (`tools.py`) as
  `DriverInfo.native_tools[name] = NativeTool(description, schema, build)`: the description verbatim, the schema
  as the client lists it (`inputSchema` through the stdio client's `sanitize_json_schema`, so without `$schema`);
  `browser_find` is listed the same way with `build=None` (no command backs it; the surface's `FindTool` takes its
  description and schema). Before
  `start()` `native_tools` is empty. `tool_name_prefix` is `mcp_model_tool_prefix(server_name)`
  (`mcp_playwright-official_`), the prefix the ability manager gives MCP tools; an instance key never changes it.
  `display_name` is `"Playwright MCP"`. `health()` pings the session.
- `observe()` calls `browser_snapshot` with `boxes: true` (and `browser_take_screenshot` when asked), so every
  element has `bounds` and a `PointRef` can be built from an observation. `native_text` is the full text of that
  result (header, open tabs, YAML) without the boxes (`without_boxes`): what the model reads today. Tabs come from
  the snapshot header, never from a `browser_tabs list`, except while a modal state (a dialog or a file chooser)
  makes the server refuse `browser_snapshot` and every other page tool: then one `browser_tabs list` (not a page
  tool) gives `context` and `surface` (URL, tabs, the dialog; the title is empty), `elements` are empty, no
  screenshot is taken and `native_text` is the refusal text. An observation that comes free with an action
  result (the linked snapshot file) has no bounds.
  A scoped `Observe` is sent exactly as the model asked and without `boxes`: `depth` → `depth`, `root` → `target`
  (`DriverRef.ref` or the `Locator`'s css; a `PointRef` or a text `Locator` is `Unsupported(dispatched=False)` before
  the call), so `native_text` is the server's answer byte for byte (fixtures `snapshot_depth_1`, `snapshot_target`)
  and the elements of the subtree have no bounds.
- **One attempt per tool call.** The driver calls `call_tool_once` on its client, never the retrying `call_tool`
  the runtime path uses: a call that fails is not sent again. A transport timeout (`mcp_timeout_s`) is
  `ActionTimeout(dispatched=True)`; no connection is `ConnectionLost(dispatched=False)`; a transport failure (the
  client's `is_transport_error`: closed or broken streams, a closed connection, read/write and broken-pipe errors,
  a terminated session) is `ConnectionLost(dispatched=True)`. Any other client exception (a JSON-RPC error, a
  serialization error) is `DriverError(dispatched=False)` with the exception type and message, so one bad argument
  never restarts the browser.
- **Sensitive commands.** `TypeText(sensitive=True)`, `RunScript(sensitive=True)` and a `FillForm` with a sensitive
  field are decoded with `sensitive=True`, so their `detail` has no echoed code; the error message of a failed
  sensitive call can still quote it, and `Tracing` does not record that message.
- **Files.** The server reads upload and drop files only under its working directory and its output directory.
  `UploadFile` and `Drop` stage each path that is an existing file outside the working directory (`output_dir`,
  relative paths resolved against it): it is copied to `output_dir/uploads/<12 hex>/<file name>` (the page sees the
  original name) and the copy's path is sent. Paths inside the working directory, missing files and directories go
  as given; the server refuses those outside with `File access denied: … is outside allowed roots` (decoded as
  `DriverError(dispatched=False)`). `stop()` removes the `uploads/<12 hex>/` folders this driver created, then
  `uploads/` itself only when nothing else is left in it: the working directory defaults to the process directory,
  which may hold a user's own `uploads/` folder.
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

#### Native tools (`drivers/playwright_mcp/tools.py`)

`DECODERS` maps each server tool a capability can select to the pure decoder of its arguments, the inverse of the
driver's handler: decoding a call the driver sent and sending the command again gives the same arguments. A
`target` that matches `ARIA_REF` becomes `DriverRef(target, "")`, any other target
`Locator(css=target, text=None, generation="")`: the server resolves both against the live page and judges
staleness itself, so the page state never refuses them (Refs). Coordinates become
`PointRef(round(x), round(y), resolver.screenshot_generation)` (the model sends no generation for a point, and the
page state refuses one on an older screenshot).

| Server tool | Command |
|---|---|
| `browser_click` | `Click(target, button, modifiers, count=2 if doubleClick else 1)` |
| `browser_type` | `TypeText(target, text, submit, clear=not slowly, slowly)` |
| `browser_press_key` | `PressKey(key)` |
| `browser_hover` / `browser_drag` | `Hover(target)` / `Drag(startTarget, endTarget)` |
| `browser_select_option` | `SelectOption(target, values)` (at least one) |
| `browser_fill_form` | `FillForm(fields)`: each `FormField(target, FieldKind(type), value)` |
| `browser_drop` | `Drop(target, paths, data=<MIME type, value pairs>)` |
| `browser_file_upload` | `UploadFile(None, paths)` (answers the open file chooser) |
| `browser_handle_dialog` | `HandleDialog(accept, promptText)` |
| `browser_evaluate` | `RunScript(function, target=<target or None>)` |
| `browser_navigate` / `browser_navigate_back` | `Navigate(url)` / `Back()` |
| `browser_snapshot` | `Observe(depth=round(depth), root=<target or None>)`; `Observe()` without them |
| `browser_take_screenshot` | `Observe(include_screenshot=True, full_page=fullPage)` |
| `browser_tabs` | `list` → `Observe()`; `new` → `OpenContext(url)`; `select` → `SwitchContext(contexts[index])`; `close` → `CloseContext(contexts[index])`, the current context without `index` |
| `browser_close` | `CloseContext(current_context)` |
| `browser_mouse_click_xy` / `_move_xy` / `_drag_xy` | `Click(point, button, count=clickCount)` / `Hover(point)` / `Drag(start, end)` |
| `browser_mouse_wheel` | `Scroll(None, deltaX, deltaY)` |
| `browser_pdf_save` / `browser_console_messages` / `browser_network_requests` | `GetPdf(filename)` / `GetConsole(level, all)` / `GetNetwork(static, filter)` |

Arguments that do not change the command are accepted and ignored: `element` (every tool), `filename`, the
snapshot's `boxes`, the screenshot's `target`, `element`, `type`, `scale`, and the click's `delay`. A missing
required argument, a wrong type (a non-numeric `depth` included), an unknown field type or tab action, a `select`
without index and an index outside `contexts` raise `ValueError`. `browser_find` (served by the surface's `FindTool`) and
`browser_wait_for` (not model-facing) have no decoder.

## Relation to other specs

- `S_05` (tools contract): the tools built from these commands are registered like every harness tool and go
  through the permission engine.
- `S_08` (security engine): `ActionPolicy` checks `PolicyRule`s on commands before middleware and the driver and
  denies with a `PolicyDenied` result.
- `S_18` (subagents): `create_browser_agent` is the public entry point; its signature does not change.
