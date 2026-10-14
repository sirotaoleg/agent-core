# Recorded `@playwright/mcp@0.0.78` answers

Real answers of the Playwright MCP server, recorded once from a local headless
Chrome against the static pages in `pages/`. They are the input for the
snapshot parser and envelope decoder (`drivers/playwright_mcp/`) and for the
rail tests. `test_mcp_fixtures_wellformed.py` only checks that the
files are complete; `drivers/playwright_mcp/test_snapshot.py` and
`test_result.py` parse them.

## Re-recording

Needs Node.js (`npx`) and Google Chrome installed. No internet access is needed
apart from `npx` fetching the pinned server package the first time. From the
repository root:

```bash
uv run python tests/unit_tests/harness/tools/browser_move/fixtures/mcp/record_mcp_fixtures.py
```

The recorder:

- serves `pages/` on a free port of `127.0.0.1`, plus `/hang`, which answers
  only after 30 s;
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
temporary directory by `<TMP>`, a second temporary directory outside the
server's roots by `<OUTSIDE>`, and the timestamps in the server's output-file
names (`page-<TS>.yml`, `console-<TS>.log`) by `<TS>`. With these placeholders,
running the recorder again produces the same files.

## Cases

All cases run in one server session, in this order. The task's item number is
in brackets.

| File | What it records |
|---|---|
| `snapshot_form_page` [1] | `browser_snapshot` of `index.html`: text input, select, checkbox, links, a download link, an iframe (refs `f1e*`), alert/confirm triggers |
| `snapshot_depth_1` | `browser_snapshot` `depth: 1` of `index.html`: the root and its children only; the navigation, the form container and the iframe appear without their subtrees |
| `snapshot_target` | `browser_snapshot` `target: <navigation ref>` of `index.html`: the navigation subtree alone, under the usual `### Page` header |
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
| `click_stale_ref` | `browser_click` with a ref of `blocked.html` after navigating away: `Ref … not found in the current page snapshot` |
| `snapshot_with_boxes` | `browser_snapshot` `boxes: true` of `index.html`: every ref line has `[box=x,y,width,height]` |
| `fill_form` | `browser_fill_form` with a textbox, a checkbox and a combobox: only `### Ran Playwright code`, no page or snapshot |
| `snapshot_after_fill_form` | `browser_snapshot` after it: the textbox value is a child `text:` line, the combobox value its `[selected]` option, the checkbox `[checked]` |
| `select_option` | `browser_select_option` `values: ["Large"]` |
| `evaluate_result` | `browser_evaluate` returning an object: `### Result` with pretty-printed JSON before `### Ran Playwright code` |
| `evaluate_error` | `browser_evaluate` whose function throws: `isError`, the JS error and its stack |
| `screenshot_element` | `browser_take_screenshot` of the heading: a `### Result` file link plus an `image/png` content block |
| `wait_for_timeout` | `browser_wait_for` text that never appears: `TimeoutError: Timeout 5000ms exceeded.`, call log with only the visibility wait |
| `click_blocked_by_dialog` | `browser_click` while a `confirm()` is open: `Tool "browser_click" does not handle the modal state.` plus `### Modal state` |
| `click_opens_tab` | `browser_click` on a `target="_blank"` link of `popup.html`: `### Open tabs` lists both tabs, the focused tab is unchanged |
| `snapshot_two_tabs` | `browser_snapshot` with two tabs: `### Open tabs` before `### Page`; YAML quoting (`'button "Note: read me" …'`, `paragraph …: "42"`) |
| `click_navigates` | `browser_click` on a same-tab link: only the URL in `### Page` shows the navigation |
| `navigate_back` | `browser_navigate_back` |
| `file_chooser_open_by_click` | `browser_click` on a file input of `upload.html`: `### Modal state` with `[File chooser]` |
| `file_upload` | `browser_file_upload` of a file in the server's working directory |
| `click_timeout_after_action` | `browser_click` on a link to `/hang` (the page server answers after 30 s): call log with `performing click action`, `click action done`, then the 5000 ms timeout while waiting for the navigation |
| `snapshot_during_dialog` | `browser_snapshot` `boxes: true` while a `confirm()` is open: refused like any other page tool (`does not handle the modal state` plus `### Modal state`), no `### Page` |
| `tabs_list_during_dialog` | `browser_tabs` `list` while the `confirm()` is open: not a page tool, so it answers, with an empty title and `### Modal state` |
| `drop_outside_roots` | `browser_drop` of a file outside the server's working and output directories: `File access denied: … is outside allowed roots`, refused before the page is touched |
| `evaluate_new_tab_failed` | first `browser_evaluate` after the browser was closed during a call (set up with `browser_run_code_unsafe`): `Failed to open a new tab` |
| `evaluate_browser_closed` | the next `browser_evaluate`: `Target page, context or browser has been closed` |

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
- The linked snapshot file holds only the YAML. While a dialog is open the
  server writes it empty.
- While a dialog or a file chooser is open, the server refuses every page
  tool, `browser_snapshot` and `browser_take_screenshot` included;
  `browser_tabs` still answers (the title it cannot read is empty).
- Nothing marks a navigation by click or a new tab: only the URL in
  `### Page` and the `### Open tabs` list (shown only with more than one tab)
  differ from the result before.
- Closing the browser with a tool call does not break the session: the server
  starts a new one on the next call. Only a close during a running call leaves
  it broken, which is how the two `evaluate_*` closed cases are set up.
- The temporary directory also appears with doubled backslashes inside the JS
  code lines; both spellings become `<TMP>`.
