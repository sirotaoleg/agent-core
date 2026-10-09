# Recorded `@playwright/mcp@0.0.78` answers

Real answers of the Playwright MCP server, recorded once from a local headless
Chrome against the static pages in `pages/`. They are the input for the Phase 5
snapshot parser and envelope decoder (`drivers/playwright_mcp/`) and for the
Phase 4 rail tests. `test_mcp_fixtures_wellformed.py` only checks that the
files are complete; parsing tests come with the code that parses them.

## Re-recording

Needs Node.js (`npx`) and Google Chrome installed. No internet access is needed
apart from `npx` fetching the pinned server package the first time. From the
repository root:

```bash
uv run python tests/unit_tests/harness/tools/browser_move/fixtures/mcp/record_mcp_fixtures.py
```

The recorder:

- serves `pages/` on a free port of `127.0.0.1`;
- launches the server the way the runtime does: `build_playwright_mcp_config()`
  and `BrowserMoveStdioClient`, with the WebVoyager baseline arguments
  (`-y @playwright/mcp@0.0.78 --isolated --config <baseline config>`) plus
  `--headless`; `--isolated` keeps the profile in memory, so no real profile
  is ever used. The server's working directory is a temporary directory that
  is deleted afterwards;
- calls each tool through `MCPTool.invoke`, which is what `ToolMgr` registers
  for every MCP tool today, and records:
  - `request`: tool name and arguments;
  - `raw_result`: the `CallToolResult` the MCP session returned
    (`model_dump(mode="json", by_alias=True, exclude_none=True)`), before any
    browser_move or core processing;
  - `model_text`: `MCPTool.render_for_llm` of the invoke output, the tool
    message content the model receives before any rail rewrites it. A
    successful call renders as the plain text; a call with `isError: true`
    renders as the JSON envelope `{"result": "<text>"}`.

Run-specific values are replaced: the page server's port by `<PORT>`, the
temporary directory by `<TMP>`, and the timestamps in the server's output-file
names (`page-<TS>.yml`, `console-<TS>.log`) by `<TS>`. With these placeholders,
running the recorder again produces the same files.

## Cases

All cases run in one server session, in this order. The task's item number is
in brackets.

| File | What it records |
|---|---|
| `snapshot_form_page` [1] | `browser_snapshot` of `index.html`: text input, select, checkbox, links, a download link, an iframe (refs `f1e*`), alert/confirm triggers |
| `click_success` [2] | `browser_click` on "Save"; the page text changes |
| `events_console_message` [6] | `browser_click` that runs `console.log`; result has `### Events` with `New console entries: …` |
| `events_download` [6] | `browser_click` on a `download` link; `### Events` reports the download start and the saved file |
| `dialog_open_by_click` [7] | `browser_click` that opens `confirm()`; result has `### Modal state` and no page title |
| `dialog_handle_accept` [7] | `browser_handle_dialog` `accept: true` |
| `navigate_same_url_reload` [8] | `browser_navigate` to the URL the tab is already on |
| `navigate_second_page` [4] | `browser_navigate` to `second.html` |
| `tabs_list` [5] | `browser_tabs` `list` with one tab |
| `tabs_new` [5] | `browser_tabs` `new` (opens `about:blank`) |
| `tabs_select` [5] | `browser_tabs` `select` index 0 |
| `tabs_close` [5] | `browser_tabs` `close` index 1 |
| `click_intercepted_by_overlay` [3] | `browser_click` on a button under a fixed backdrop: `isError`, Playwright's call log with `intercepts pointer events`, ending in the 5000 ms action timeout |
| `click_timeout_not_enabled` [9] | `browser_click` on a disabled button: `isError`, call log with `element is not enabled`, the same 5000 ms action timeout |

## Things to know about the raw format

- Every result is a single `text` content block; no `structuredContent`.
- Element arguments are named `target` in 0.0.78 (not `ref`).
- Action results (`browser_click`, `browser_navigate`) do not inline the
  snapshot: their `### Snapshot` section is a link to a file the server writes
  into its working directory (`.playwright-mcp\page-<TS>.yml` on Windows).
  Only `browser_snapshot` returns the YAML inline.
- Refs are not always bare `e<N>`: refs inside an iframe are `f<N>e<M>`, and
  even the main frame of `blocked.html` (opened after several navigations and
  the tab operations) gets `f5e*` refs. A parser must accept `(f\d+)?e\d+`.
- The intercepted click and the plain timeout share the error header
  `TimeoutError: browserBackend.callTool: Timeout 5000ms exceeded.`; only the
  call log tells them apart. The call log keeps the ANSI dim escapes
  (`\u001b[2m … \u001b[22m`) and folds repeats as `N ×`.
