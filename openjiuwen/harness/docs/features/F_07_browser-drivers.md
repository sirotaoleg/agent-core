# F_07 Browser drivers

## Meta

| Item | Value |
|---|---|
| Type | feature |
| Date | 2026-10-09 |
| Scope | `openjiuwen/harness/tools/browser_move/` (`contract/`, `drivers/`, `provisioning/`, `surfaces/browser/`), `tests/unit_tests/harness/tools/browser_move/` |
| Test baseline | see "Verification" in each section |
| Related spec | [[S_20_browser-driver-contract]] |
| Refs | #driver-architecture |

## Background

`browser_move` is being moved onto a driver contract: the agent core sends typed commands and reads typed results
and errors, and each driver (browser-use now, Playwright MCP next) translates them for one library. This document
records the decisions taken while the pieces land, one section per piece; S_20 describes the resulting contract.

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
