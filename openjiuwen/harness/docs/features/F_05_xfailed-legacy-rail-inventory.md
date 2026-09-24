# F_05 26 个遗留 xfail 用例清单（文档专项，不改测试）

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature（文档专项） |
| 日期 | 2026-09-18 |
| 范围 | `tests/unit_tests/harness/tools/browser_move/`（仅阅读，零测试代码改动） |
| 测试基线 | `.venv\Scripts\python.exe -m pytest tests/unit_tests/harness/tools/browser_move -q` → 739 passed, 26 xfailed |
| 关联 feature | `F_04_browser-driver-opaque-element-identity.md`（同一批工作的驱动契约变更）；与 `F_02_browser-semantic-neutrality.md` 描述的同一批 rail 行为相关 |

## 背景

26 个测试共享同一段 `pytest.mark.xfail` 理由文本："Superseded by BU driver (Policy A):
asserts base agtai/develop #1147 rail/catalog/semantic behavior replaced by the
browser_use driver. Tracked for later reconciliation."。这段文本对全部 26 个用例逐字
相同，因此从 xfail 理由本身完全看不出每个用例具体断言了什么行为——一个报告 26 个
xfail 的绿色测试套件，实际上是 26 个未被单独记录的行为缺口。

本文档不改变任何测试代码或标记（遵循 D7：先弄清楚要和解的行为是什么，再决定是否
和解；本次仅完成"弄清楚"这一步），只从每个用例的测试体本身提炼它实际断言的行为，
并按主题分组，使每个缺口可以被单独审阅、日后也可以被单独关闭。

## 数据结构

清单来自两条独立命令的交叉核对：

```
.venv\Scripts\python.exe -m pytest tests/unit_tests/harness/tools/browser_move -q -rx
grep -rn "pytest.mark.xfail" tests/unit_tests/harness/tools/browser_move
```

两者给出的按文件分布完全一致：

| 文件 | xfail 数 |
|---|---|
| `test_browser_runtime_rail.py` | 21 |
| `test_browser_probe_interactives.py` | 2 |
| `test_browser_semantic_state.py` | 1 |
| `test_browser_tool_semantics.py` | 1 |
| `test_create_browser_agent.py` | 1 |
| **合计** | **26** |

## 决策：26 个用例的清单（按主题分组）

每一行的描述都来自阅读该用例的测试体（`assert` 语句 + 构造的输入），不是对共享 xfail
理由的转述。

### 主题 1 — MCP 工具目录完整性（3 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_before_invoke_builds_allowlist_from_core_including_screenshot` | `test_browser_runtime_rail.py` | `before_invoke` 在遗留 MCP 路径上把 MCP 工具白名单设为 `CORE_BROWSER_TOOL_NAMES`（其中包含 `browser_take_screenshot`）。 |
| `test_action_class_table_covers_the_whole_catalog` | `test_browser_tool_semantics.py` | `CORE_BROWSER_TOOL_NAMES` + `ADVANCED_CODE_BROWSER_TOOL_NAMES` + `UNSAFE_DEV_BROWSER_TOOL_NAMES` 合并出的完整遗留目录，与预期 action-class 映射表的 key 集合逐一对应（无遗漏、无多余）。 |
| `test_default_factory_forwards_registered_catalog_allowlist` | `test_create_browser_agent.py` | 默认 `create_browser_agent` 工厂转发的 `allowed_tool_names` 恰等于 `BROWSER_CATALOG_RUNTIME_TOOL_NAMES`（是 `CORE_BROWSER_TOOL_NAMES` 的子集），且此前被延后注册的 `browser_drop`/`browser_find`/`browser_handle_dialog`/`browser_hover` 现在都在其中、没有工具仍处于延后状态。 |

### 主题 2 — 直连 MCP 的 target 解析与改写（4 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_direct_mcp_target_id_is_resolved_and_runtime_fields_are_removed` | `test_browser_runtime_rail.py` | `_normalize_playwright_ref_args` 把 PageState 的 `target_id`/`generation_id` 解析回 MCP 原生的 `target`/`element` 选择器字段，剥离运行时专属字段。 |
| `test_evaluate_runtime_field_contract_is_not_forwarded_to_mcp` | `test_browser_runtime_rail.py` | 转发给 MCP 的 `browser_evaluate` 参数中，运行时专属的 `field`/`fields` 键被剥离，只保留 MCP 认得的 `target`/`element`/`function`。 |
| `test_direct_mcp_target_is_refreshed_when_runtime_identity_is_unique` | `test_browser_runtime_rail.py` | 即使 `target_id` 来自已过期的 PageState 世代，只要同一个 selector-hint 身份在当前世代仍唯一，仍能被正确重新解析到同一个选择器。 |
| `test_primary_link_target_is_rewritten_to_direct_navigation` | `test_browser_runtime_rail.py` | 点击一张卡片的 `primary_link_target_id` 会被 rail 改写成对该 URL 的直接 `browser_navigate` 调用。 |

### 主题 3 — 阶段规划与共享截止时间（2 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_phase_plan_uses_explicit_completion_conditions_and_large_budgets` | `test_browser_runtime_rail.py` | `_build_phase_state` 把多步任务分类为 "complex"，依次分配 navigation/form/filtering/extraction 四个阶段、各自的固定尝试预算，且每个阶段都带有显式的完成条件。 |
| `test_resume_keeps_shared_deadline_and_gets_a_fresh_invocation_slice` | `test_browser_runtime_rail.py` | `_resume_task_state` 在恢复一个因调用切片耗尽而中断的任务时，保留原有的共享截止时间不变、清除该项 blocker，并把 `resume_count` 加一。 |

### 主题 4 — `browser_evaluate` 结果到结构化证据的映射（6 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_comparison_evidence_requires_distinct_bilibili_sort_slots` | `test_browser_runtime_rail.py` | `_record_structured_evidence` 把"综合排序"与"最新排序"两种卡片结果放进各自独立的证据槽位，在只拿到一种时把另一种标记为缺失。 |
| `test_evaluate_result_becomes_compact_evidence_before_action_windowing` | `test_browser_runtime_rail.py` | 一次 `browser_evaluate` 结果被压缩成一条 `targeted_evaluate` 结构化证据（目标摘要用哈希代替原始表达式），且这一压缩发生在最近动作窗口把它截断之前。 |
| `test_targeted_evaluate_maps_only_requested_fields_with_provenance` | `test_browser_runtime_rail.py` | `_record_structured_evidence` 只把调用方在 `_runtime_evidence_fields` 里显式要求的字段从 `browser_evaluate` 结果映射出来并附带逐字段 provenance，丢弃不相关的字段。 |
| `test_ambiguous_evaluate_alias_requires_explicit_target_contract` | `test_browser_runtime_rail.py` | 针对一个含糊目标（如 `document`）的 evaluate 结果不会被采纳为证据，也不产生任何字段覆盖记录。 |
| `test_missing_evaluate_value_closes_slot_as_unavailable` | `test_browser_runtime_rail.py` | 某个被请求字段的 evaluate 结果为 `null` 时，对应证据槽位被关闭为 "missing" 状态，同时仍记录来源工具与生成代（provenance）。 |
| `test_interactive_probe_sort_evidence_keeps_selection_source` | `test_browser_runtime_rail.py` | `_interactive_probe_evidence` 从被选中的排序标签页里提取出 `sort_state` 文本，并在 provenance 中保留该选中状态是如何被判定的（`selection_source`）。 |

### 主题 5 — 探测结果契约与降级策略（1 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_probe_contract_exposes_result_count_and_bounds_conflict_fallback` | `test_browser_runtime_rail.py` | `_enrich_probe_result_contract` 汇报实际观测数量与请求数量的对比；当两次探测对同一结果的 kind/region 分类冲突时，给出推荐降级策略并倒数剩余的精确探测降级次数。 |

### 主题 6 — 批量执行的部分成功处理（1 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_partial_batch_keeps_successful_extraction_without_marking_batch_success` | `test_browser_runtime_rail.py` | 一次部分失败的 `browser_batch_interact` 仍把成功抽取到的字段计入 `field_coverage`，同时整批结果依然被记为不成功。 |

### 主题 7 — 模型工具调用协议的重试处理（1 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_unfinished_text_tool_intent_retries_once_in_same_task` | `test_browser_runtime_rail.py` | 模型只在纯文本里声称要调用工具、却没有真正发出 tool_call 时，第一次触发一次 steering 重试；第二次再次发生同样情况时，以 `terminal_reason="model_tool_protocol_error"` 强制结束。 |

### 主题 8 — 工具调用结果的结局分类（2 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_after_tool_call_does_not_advance_state_for_string_mcp_error` | `test_browser_runtime_rail.py` | 字符串形式的 MCP 错误结果被归一化为 `ok=False`，工具消息标记为"已执行但失败、状态未改变"，记录一次 "failed" 结局，且不影响字段覆盖。 |
| `test_after_tool_call_marks_mutating_timeout_as_ambiguous_for_reconciliation` | `test_browser_runtime_rail.py` | 一次 mutating 工具调用超时会被记为 "ambiguous" 结局（`semantic_delta="awaiting_observation"`），而不是直接判定失败，留待后续和解。 |

### 主题 9 — 达到最大迭代数时的终态渲染（1 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_after_invoke_renders_authoritative_max_iteration_result` | `test_browser_runtime_rail.py` | `after_invoke` 把原始的"达到最大迭代数"提示改写成结构化的 `authoritative_browser_result`（`status="partial"`、`terminal_reason="max_iterations_reached"`、缺失字段列表），并在顶层设置 `error` 错误码。 |

### 主题 10 — 动态进度提示词注入（2 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_before_model_call_skips_dynamic_progress_without_attachment_manager` | `test_browser_runtime_rail.py` | 没有 attachment manager 可用来渲染动态进度时，`before_model_call` 不注入 `<browser_progress>` 提示词小节，也不泄露进度细节。 |
| `test_before_model_call_initializes_runtime_task_state_without_progress_attachment` | `test_browser_runtime_rail.py` | 首次调用时 `before_model_call` 初始化全新的阶段预算任务状态（目标 + `status="in_progress"`）并重置语义追踪器，同时不产生 `<browser_progress>` 占位符。 |

### 主题 11 — 语义状态中立性记账（1 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_neutral_observation_keeps_every_latest_payload_key` | `test_browser_semantic_state.py` | 一次语义中立的 `observe(..., mutating=False)` 调用返回的负载，仍包含普通（mutating）观测负载的全部 key，不因为中立而丢字段。 |

### 主题 12 — 交互探测的 JS 执行路径（2 个）

| 用例 | 文件 | 断言的行为 |
|---|---|---|
| `test_runtime_probe_interactives_uses_code_executor_and_parses_json` | `test_browser_probe_interactives.py` | `probe_interactives()` 在驱动路径上通过 `_evaluate_page_js` 执行 JS 探测，并给每个返回的可交互元素打上 `target_id`/`generation_id`。 |
| `test_runtime_probe_interactives_recovers_one_malformed_json_result` | `test_browser_probe_interactives.py` | `probe_interactives()` 在 `_code_executor` 首次返回无法解析的 JSON 时重试一次，并在诊断信息里报告 `parse_retry_count`。 |

## 拒绝的方案

未考虑改写共享 xfail 理由文本或调整 strict 标记——本任务明确要求"仅文档，零测试代码
改动"；理由文本本身该不该改是和解每个具体行为缺口时才该做的事（D7），不该在纯盘点阶段
顺手动手。

## 验证

- 枚举命令与 grep 交叉核对，两者按文件分布的计数完全一致（21 / 2 / 1 / 1 / 1 = 26）。
- 全部 26 条描述均从阅读对应测试体的 `assert` 语句与构造输入得出，未转述共享的 xfail
  理由文本。
- 零测试代码改动；`git diff` 对 `tests/` 下与本文档相关的 5 个文件均无实际差异（本文档
  独立新增，不修改任何已有文件）。

## 已知遗留

以下 12 个行为主题仍待逐一和解（每条和解都需要按 D7 补齐真实行为，而不是删除标记）：

1. MCP 工具目录完整性（3 个用例）——驱动路径的等价目录/action-class 覆盖尚未建立。
2. 直连 MCP 的 target 解析与改写（4 个用例）——`target_id`/`generation_id` 到驱动身份
   （`DriverRef`/`IndexRef`）的等价物尚未在这些具体断言点验证。
3. 阶段规划与共享截止时间（2 个用例）。
4. `browser_evaluate` 结构化证据映射（6 个用例）。
5. 探测结果契约与降级策略（1 个用例）。
6. 批量执行部分成功处理（1 个用例）。
7. 模型工具调用协议重试（1 个用例）。
8. 工具调用结果结局分类（2 个用例）。
9. 最大迭代数终态渲染（1 个用例）。
10. 动态进度提示词注入（2 个用例）。
11. 语义状态中立性记账（1 个用例）。
12. 交互探测 JS 执行路径（2 个用例）。

本文档不判断这些主题该如何在驱动路径上重新实现，只负责让每一个都可以被单独审阅、
单独排期、单独关闭。
