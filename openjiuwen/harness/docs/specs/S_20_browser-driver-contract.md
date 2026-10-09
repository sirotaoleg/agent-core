# S_20 Browser Driver Contract

> **DRAFT.** Written during the driver-architecture refactor of `browser_move` (Phase 2). Sections are added as
> the phases land: the conformance suite (Phase 2), the composition root (Phase 3), the surface profile and the
> tool factory (Phase 4). Until the draft mark is removed, the code in `contract/` is the reference.

## Meta

| Item | Value |
|---|---|
| Type | spec |
| Modules | `openjiuwen/harness/tools/browser_move/contract/` (`observation.py` / `refs.py` / `actions.py` / `results.py` / `errors.py` / `driver.py` / `registry.py` / `surface.py` / `testing/`), `openjiuwen/harness/tools/browser_move/surfaces/browser/` (`commands.py` / `context.py`) |
| Last revised | 2026-10-09 |
| Related feature | — (archived when the refactor lands) |

## Scope

The contract is the only thing the agent core and a driver both know. The core sends commands and reads typed
results and errors; a driver translates commands for one library and declares which commands it handles. As of
this revision no driver and no agent code uses the contract; only its tests and `FakeDriver` do.

Out of scope here: middleware (timeouts, retry, tracing), the composition root, provisioning, rendering and the
model-facing tool schemas. They consume these types and get their own sections.

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
| `Wait` | exactly one of `seconds`, `until_text` | READ |
| `SwitchContext` / `CloseContext` | `context: ContextRef` | NAVIGATE |
| `HandleDialog` | `accept`, `prompt_text=None` | ACT |
| `RunScript` | `code`, `args=None`, `sensitive=False`, `target: Ref \| None = None` | SCRIPT |

A surface defines its own commands as `Action` subclasses in `surfaces/<name>/commands.py`. The browser surface
has `Navigate(url)`, `Back`, `OpenContext(url=None)` (NAVIGATE); `SelectOption(target, values)`,
`UploadFile(target: Ref | None, paths=())`, `FillForm(fields: tuple[FormField, ...])`,
`Drop(target, paths=(), data=())` (ACT); `GetPdf(filename=None)`, `GetConsole(level="info", all=False)`,
`GetNetwork(static=False, filter=None)` (READ). Drivers handle both kinds the same way; the core does not care
where a command class is defined, and a new surface adds its commands (`Tap`, `Swipe`) without editing
`contract/`. A command is added only when a driver handles a tool the model uses today.

`sensitive=True` on `TypeText` and `RunScript` means the text or code must never reach traces or logs.

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
  `Unsupported` (no handler, or a ref kind the handler does not accept) also defaults to `False`.
- All others default to `True`: `ElementNotFound`, `AmbiguousElement`, `ActionIntercepted(by=...)` (`by` names
  the element that received the input), `ActionTimeout`, `NavigationError`, `ConnectionLost`, `PolicyDenied`
  (raised by the security rail, never by a driver).

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
  initial state), `target(observation)` (what to click), `changed(before, after)` (what must change),
  `context_changes()` (commands that must set `context_changed`).

### FakeDriver (`contract/testing/fake.py`)

A scripted `Driver` for tests: records every call as `Call(kind, payload)`, answers `observe` / `execute` from
queues in order, raises `Unsupported(dispatched=False)` for commands or ref kinds missing from its `info.actions`
(default: every shared command except `Observe`, all ref kinds; default surfaces `{"fake"}`), raises a scripted
`DriverError(dispatched=...)` or any exception via `fail_next`, and raises `ConnectionLost` from the n-th call
until the next `start()` via `lose_connection_on(n)`. Running out of script fails with `AssertionError`.

## Drivers

### browser-use (`drivers/browser_use/`)

`BrowserUseDriver(HandlerDriver)` runs commands through the browser-use sidecar: a separate Python process
(`sidecar/`, its own venv; the agent-core process never imports `browser_use`) reached over NDJSON on stdio
(`transport.py`). `info`: `name="browser_use"`, `surfaces={"browser"}`, `observes={elements, screenshot,
native_text, surface}`, `versions` from the sidecar's `connect` reply.

- Factory: `create_browser_use_driver(device, surface_settings)` builds `BrowserUseConfig(cdp_url=device.endpoint)`;
  `start(config=None)` spawns the sidecar and attaches to that CDP endpoint (an explicit config overrides).
- Handlers, all with `refs={DriverRef}` where the command has a target: `Click`, `TypeText` (target required, no
  `slowly`), `Scroll` (one axis per command), `Hover`, `Drag`, `SelectOption` (exactly one value), `UploadFile`
  (target required), `Drop`, `FillForm`, `RunScript` (page JS; with a target the element is stamped and passed as
  the first argument). Without refs: `PressKey`, `HandleDialog`, `Wait` (sleep, or polling page text for 10 s),
  `Navigate`, `Back`, `OpenContext`, `SwitchContext`, `CloseContext`. Any other form raises
  `Unsupported(dispatched=False)` before anything is sent.
- `DriverRef.ref` is the handle the sidecar mints in `observe()` (`bnid:<backend node id>`). The sidecar's index
  refs (`IndexRef`) stay internal; no handler accepts them.
- `observe()` fills `elements`, `native_text` (browser-use's own page text), `screenshot` (a separate full-page
  capture when `full_page`) and `BrowserContext` (url, title, tabs, PDF viewer, scroll pixels). The focused context is
  the first tab whose URL is the page URL. `generation` stays empty.
- `context_changed`: `Navigate`, `Back`, `OpenContext` always (a same-URL reload too); `SwitchContext` and
  `CloseContext` always; other commands when the sidecar reports a URL change.
- Errors: each sidecar error code maps to one typed error. `dispatched=False` only for `StaleIndexError` (→
  `StaleRef`), `ElementNotFound`, `DriverNotConnected` (→ `ConnectionLost`) and `DriverUnsupported` (→
  `Unsupported`), which the sidecar raises before any input reaches the page. Codes it cannot classify become
  `DriverError(dispatched=True)` and are logged raw.

## Relation to other specs

- `S_05` (tools contract): the tools built from these commands are registered like every harness tool and go
  through the permission engine.
- `S_08` (security engine): the browser security rail checks `PolicyRule`s on commands before middleware and the
  driver, and raises `PolicyDenied`.
- `S_18` (subagents): `create_browser_agent` is the public entry point; its signature does not change.
