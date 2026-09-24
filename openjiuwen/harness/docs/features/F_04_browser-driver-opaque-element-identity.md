# F_04 浏览器驱动契约：用不透明的驱动铸造句柄替换 CDP 形状的元素身份

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-09-18 |
| 范围 | `openjiuwen/harness/tools/browser_move/backends/contract/base.py`、`backends/browser_use/driver.py`、`backends/browser_use/sidecar/{mapping.py,session_adapter.py}`、`runtime/{runtime.py,page_state.py}`、`tests/unit_tests/harness/tools/browser_move/{fakes/fake_driver.py,test_browser_driver_wire_contract.py,test_browser_id_bridge.py,test_browser_use_mapping.py}` |
| 测试基线 | `.venv\Scripts\python.exe -m pytest tests/unit_tests/harness/tools/browser_move -q` → 739 passed, 26 xfailed（改动前后不变） |
| Refs | 本次任务无外部 issue 编号；关联 `F_05_xfailed-legacy-rail-inventory.md`（同批工作的文档专项）|

## 背景

`BrowserDriver` 契约的设计目标是承载不止一种后端：截图+坐标的 CUA 循环、WebDriver
后端、或是用无障碍引用字符串标识元素的 Playwright-MCP 驱动。但在这次改动之前，元素
身份被写死成一个必填的 Chrome DevTools Protocol 整数：

- `ObservedElement.backend_node_id: int`（必填）
- `ResolvedElement.backend_node_id: int`（必填）
- `NodeRef` 的文档写的是 "CDP backend node id"
- `TabRef.target_id` 的文档字面承认"因为 browser-use 返回的就是这个才这么命名"
- `BrowserDriver` Protocol 的文档字面写"每个方法都一一对应到 sidecar 的一个 wire
  方法名"

一个没有 DOM 的后端根本没有这样一个整数，只能编造一个假的。这既让类型说谎（字段
名叫 backend node id，值却是编的），又让"我没有 node id"这个合法状态无法表达——因为
字段是必填的，不是可选的。每多一个未来的驱动，这个代价就再摊一次，所以要在第二个
驱动出现之前先修好这个形状问题。

改动前必须重读 D5：`IndexRef` 是**易失的**（browser-use 在每次 `observe()` 时都会
重新编号 selector map），`backend_node_id` 是**耐久的**兜底层。canonical 模式是
"先 `stamp(IndexRef)`，捕获 `StaleIndexError`，再用耐久 ref 重试"。本次改动只改变
耐久层**由什么构成**，绝不能改变"易失优先、耐久兜底"这个行为本身。

## 数据结构

新增 `DriverRef`（`backends/contract/base.py`）：

```python
@dataclass(frozen=True)
class DriverRef:
    """Opaque, driver-minted durable element handle.

    The runtime MUST NOT parse ``handle``, compare it structurally, or derive
    any meaning from it. It is a receipt: store it, hand it back. This is the
    backend-neutral counterpart to ``NodeRef`` -- any backend, CDP-based or
    not, mints one for every observed element, so the runtime always has a
    durable fallback to retry with when the ephemeral ``IndexRef`` goes stale
    (see ``StaleIndexError``), regardless of what identity system the backend
    uses internally.
    """

    handle: str
    driver_generation: int
```

`ElementRef` 从 `IndexRef | SelectorRef | TextRef | NodeRef` 扩为
`IndexRef | SelectorRef | TextRef | NodeRef | DriverRef`——`NodeRef` **原样保留**为
CDP 后端的合法 ref，不删除。

`ObservedElement` / `ResolvedElement` 的字段变化（两者对称）：

```python
# 之前：backend_node_id: int（必填）
# 之后：
driver_ref: DriverRef       # 必填，驱动为每个元素铸造
backend_node_id: int | None = None   # 可选，只有 CDP 后端才真正填它
```

驱动侧（browser-use，目前唯一注册的后端）铸造惯例是 `"bnid:<backend_node_id>"`：
`session_adapter._mint_driver_ref` 铸造、`_parse_driver_ref_handle` 解析同一前缀。
这只是**这一个 CDP 后端**的内部编码惯例——协议本身不规定 handle 的内部格式，运行时
也不允许假设它。

运行时侧新增一个纯函数 `_durable_element_ref_from_locator`（`runtime/runtime.py`），
把 PageState locator 字典还原成 `DriverRef`：优先读新写入的 `driver_ref` handle；
只有旧 session 恢复时才回退读遗留的 `backend_node_id` 整数，此时把它包进同一个
`"bnid:<id>"` handle 惯例里合成一个 `DriverRef`（而不是构造 `NodeRef`）。

## 决策

1. **`driver_ref` 必填、`backend_node_id` 可选，而不是反过来。** 运行时能拿到耐久
   身份的唯一途径就是驱动铸造的 `driver_ref`；`backend_node_id` 只是 CDP 后端"恰好
   有"的一个额外事实，值不值得记录取决于后端本身，不该由 dataclass 的必填性替它
   决定。

2. **`DriverRef.handle` 是不透明字符串，运行时永不解析。** `runtime/` 里唯一读取
   `handle` 内容的地方，是为了**合成**一个 `DriverRef`（从遗留 `backend_node_id`
   回退路径），从未反向拆解一个已经存在的 `driver_ref.handle` 去读它的语义。真正
   知道 `"bnid:"` 前缀含义的代码只有 `backends/browser_use/sidecar/session_adapter.py`
   自己（它是铸造者，也是唯一的解析者）。

3. **`NodeRef` 不删除、`ElementRef` 扩成五态联合。** `NodeRef` 对 CDP 后端仍然是
   合法且必要的 ref 类型——sidecar 的 `_resolve_ref_to_backend_node_id` 仍然接受
   `kind == "node"`。`DriverRef` 是新增的第五态，不是替代。

4. **运行时改用 `driver.resolve(ref)` 取得具体元素事实（box/tag/visible）**，不再
   直接假设一个整数就能表达这些。`_materialize_ax_target` 的 stale 兜底改成先试
   `IndexRef`，`StaleIndexError` 时用 `_durable_element_ref_from_locator` 产出的
   `DriverRef` 重试——`NodeRef` 完全退出这条路径。

5. **向后兼容读路径只读不写。** 新 locator 一律写 `driver_ref` handle；旧 session
   恢复时读到的纯 `backend_node_id` 整数被合成成 `DriverRef`，但从不重新写回旧
   格式,也不在这条回退路径里构造 `NodeRef`。

## 拒绝的方案

**没有直接删除 `backend_node_id` 字段。** browser-use 的 sidecar 是一个真实的 CDP
后端——`sidecar/cdp.py` 真的把 `backendNodeId` 发给 Chrome（`DOM.resolveNode`、
文件上传的 `DOM.setFileInputFiles` 都需要它）。删掉这个字段会让这个真实存在的后端
无法表达它确实拥有的信息，纯粹是为了理论纯粹性而丢失事实。让它变成可选字段，而
不是删除，才是对"有些后端真的有这个整数、有些没有"这一事实的诚实建模。

**没有把 `DriverRef.handle` 做成按后端区分的类型化联合**（例如
`CdpHandle | WebDriverHandle | CuaCoordinateHandle` 的联合类型）。这看起来能提供
更强的类型安全，但代价是把"这个后端用什么身份系统"这个知识重新搬回运行时——运行时
就不得不为每一种后端形状写一个分支去解包。这正是 D1（驱动只是"眼睛和手"，编排全部
留在运行时；驱动不得让运行时知道它内部的身份系统是什么）明确禁止的耦合方向。一个
不透明字符串 + "只存不解析"的契约，才能让运行时对未来任何后端的内部身份表示保持
零知识。

## 验证

- `.venv\Scripts\python.exe -m pytest tests/unit_tests/harness/tools/browser_move -q`
  → 739 passed, 26 xfailed（与改动前完全一致，见 Step 0 基线）。
- `grep -rn "backend_node_id" openjiuwen/harness/tools/browser_move/runtime` 只命中
  向后兼容读路径（注释 + `.get("backend_node_id")` 读取），零处构造 `NodeRef`。
- `grep -rn "NodeRef(" openjiuwen/harness/tools/browser_move/runtime` 无匹配。
- `tests/.../fakes/fake_driver.py` 新增了 `backend_node_id=None` 的观测场景
  （`_default_observation(backend_node_id=None)`），并驱动了一次完整的
  observe → act → stale → 重新解析的循环，证明非 CDP 后端形状是可行的（这正是本次
  改动要支持的场景）。
- `test_browser_driver_wire_contract.py` 扩展验证 `DriverRef` 能通过 `_ref_to_wire`
  与 sidecar 自己的铸造惯例正确往返。
- Ruff：改动的每个文件相对 `HEAD` 版本零新增违规（预先存在的 E501/I001 不在本次
  修复范围）。

## 已知遗留

以下条目属于本次任务明确划定的 OUT OF SCOPE，未在本次改动中处理，留作后续工作：

- 约 25 处 `_uses_browser_driver()` / `uses_browser_driver()` 分支点——本次改动前后
  数量不变（`runtime.py` 11 处，与 `HEAD` 版本一致）。
- 拆分的工具目录（MCP 路径 3 个工具 / 驱动路径 20 个工具的差异）。
- 重复的后端谓词（`runtime.py:549`、`runtime_tools.py:1964`、
  `controllers/action.py:603`，及两处内联 `getattr` 副本）。
- `backends/contract/registry.py` 与保留名机制。

以上四项均为更大规模工作的一部分，本次改动刻意未触碰。