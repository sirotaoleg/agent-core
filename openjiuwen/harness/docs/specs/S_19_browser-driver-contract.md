# S_19 浏览器驱动契约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/tools/browser_move/backends/contract/`（`base.py`、`errors.py`、`registry.py`）、`backends/browser_use/`（driver.py、transport.py、sidecar/）、`runtime/`（PageState/生成代记账消费方）|
| 最近一次修订日期 | 2026-09-18 |
| 关联 feature | `F_04_browser-driver-opaque-element-identity.md` |

## 范围 / 边界

本规约定义 `browser_move` 子系统的 **`BrowserDriver` 协议**——运行时用来驱动一个
具体浏览器自动化后端的最小"眼睛和手"接口——及其元素身份、tab 身份、生成代
（generation）记账的不变量。

具体覆盖：

- `backends/contract/base.py`：`ElementRef` 五态联合（`IndexRef` / `SelectorRef` /
  `TextRef` / `NodeRef` / `DriverRef`）、`ObservedElement` / `ResolvedElement` /
  `TabRef` / `Observation` 值类型、`BrowserDriver` Protocol 本身。
- `backends/browser_use/driver.py` + `sidecar/`：目前**唯一注册**的 `BrowserDriver`
  实现，运行在独立的 sidecar 进程里，通过 `sidecar/wire.py` 定义的 JSON 线协议与
  主进程通信。
- 运行时（`runtime/`）如何**消费**这个协议：只通过 `backends/contract/` 暴露的类型
  和 `driver.*` 方法，从不了解某个具体后端的内部身份表示。

不在本规约范围内：

- `runtime/` 自己的 PageState / generation_id / target_id 记账体系——那是运行时侧
  的编排状态，与驱动侧的 `driver_generation` 是两套独立的记账（见下文不变量 6）。
- 遗留 Playwright-MCP 路径（`backends/playwright_mcp/`）——它不是一个注册的
  `BrowserDriver` 实现，是历史遗留的、与本协议平行的执行路径，由
  `resolve_browser_driver_backend()` 门控（`runtime/config.py`）。
- `backends/contract/registry.py` 的名字注册与保留名机制——注册什么名字对应哪个
  工厂是装配层的事，不是协议本身的不变量。
- 具体某个后端的内部实现细节（例如 CDP 调用如何映射到 Chrome）。

## 不变量

1. **驱动只是眼睛和手，编排永远留在运行时（D1）。** `BrowserDriver` 的任何方法都
   不得接收 PageState 的 `target_id` 或 `generation_id`；驱动只知道它自己的
   `driver_generation` 和元素身份（`ElementRef`）。违反这条会让运行时的编排状态和
   驱动的执行状态重新纠缠——这正是驱动抽象存在的目的所要拆开的耦合。

2. **`ElementRef` 是五态联合，`IndexRef` 易失、其余耐久。**
   `IndexRef | SelectorRef | TextRef | NodeRef | DriverRef`。`IndexRef` 携带
   `driver_generation`；一旦与驱动当前的生成代不一致，驱动**必须**抛
   `StaleIndexError`——拒绝永远优于点错元素。`NodeRef`（CDP 专属）与 `DriverRef`
   （后端中立）都是耐久层，供 `IndexRef` 失效时重试。

3. **`DriverRef.handle` 是不透明的，运行时不得解析它。** `DriverRef` 是一张收据：
   存起来，原样交还。运行时（`runtime/`）不得对 `handle` 做子串解析、结构比较，或
   从中推导任何语义；只有铸造它的那个后端自己知道 `handle` 的内部编码规则（目前
   唯一的后端 browser-use 用 `"bnid:<backend_node_id>"` 的惯例，仅它自己铸造、
   仅它自己解析）。

4. **`driver_ref` 必填、`backend_node_id` 可选。** `ObservedElement` 与
   `ResolvedElement` 都要求驱动为每一个元素铸造一个 `driver_ref`——这是运行时获得
   耐久身份的唯一途径。`backend_node_id: int | None` 只在后端真的是 CDP 后端、真的
   拥有这个整数时才非空；一个没有 DOM 的后端（截图+坐标的 CUA、WebDriver、无障碍
   引用后端）把它留空，绝不允许为了满足类型而编造一个假的整数。

5. **`NodeRef` 不因 `DriverRef` 的引入而退场。** `NodeRef` 对 CDP 后端仍是合法且
   被使用的 ref 类型——sidecar 仍然接受 `kind == "node"` 并直接用其中的
   `backend_node_id` 定位元素。`DriverRef` 是新增的第五个态，不是替代品。

6. **驱动的 `driver_generation` 与运行时的 PageState generation 是两套独立记账，
   不得合并或互相读取。** 驱动的生成代只回答"我的 `IndexRef` 编号是否仍然有效"；
   PageState 的 generation 只回答"运行时的元素目录是否仍然新鲜"。两者语义不同、
   生命周期不同，混用会让 stale 判定失去意义。

7. **`BrowserDriver` 协议是后端中立的，不得假设任何一个具体后端的线协议形状。**
   目前唯一注册的后端 browser-use 的 sidecar wire 方法名恰好与协议方法一一对应，
   但这个对应关系的方向是"协议 → sidecar 照抄"，不是反过来。新增后端实现同一组
   async 方法即可，完全不需要说任何 wire 格式、甚至完全不需要跑在独立进程里。

8. **`TabRef.target_id` 是每后端各自的不透明 tab 句柄。** 对 browser-use 而言它
   恰好是字面的 CDP target id（sidecar 就是这么返回的），但这只是这一个后端的实现
   细节，不是协议对 tab 身份形状的规定。

9. **sidecar 进程边界是安全边界，不是本协议的可选项。** browser-use 后端运行在
   独立 Python 进程里（`backends/browser_use/transport.py` 的 `_sidecar_env()`
   构造一个只含 PATH 与少量系统路径的最小环境）；LLM 凭证绝不允许到达 sidecar。
   这条边界属于 browser-use 这一个具体实现，但任何未来的"跑在独立进程里"的后端都
   应当遵循同样的最小环境原则。

## 接口契约

```python
@runtime_checkable
class BrowserDriver(Protocol):
    async def connect(self, *, cdp_url: str, timeout_s: float = 30.0) -> DriverInfo: ...
    async def observe(self, ...) -> Observation: ...
    async def resolve(self, ref: ElementRef) -> ResolvedElement: ...
    async def stamp(self, ref: ElementRef, *, attribute: str, value: str) -> str: ...
    async def evaluate(self, source: str, *, args: Any = None) -> Any: ...
    # ...（其余方法见 base.py，均遵循不变量 1：不接收 PageState 概念）
```

- `observe(...)` 返回的 `Observation.elements` 中每个 `ObservedElement` 必须携带
  非空 `driver_ref`（不变量 4）。
- `resolve(ref: ElementRef)` 接受五态联合中的任意一个，`IndexRef` 生成代不匹配时
  抛 `StaleIndexError`（不变量 2）；返回的 `ResolvedElement` 同样携带 `driver_ref`。
- `stamp(ref, ...)` 是 D5 canonical 重试模式的落地点：调用方先试 `IndexRef`，捕获
  `StaleIndexError` 后用 `DriverRef`（或 `NodeRef`，对 CDP 后端）重试。

运行时侧的消费契约（`runtime/runtime.py`）：

- PageState locator 字典里存的是驱动铸造的 `driver_ref` handle 字符串（新写入路径），
  不是原始的 `backend_node_id` 整数。
- 向后兼容读路径：旧 session 恢复时读到的 locator 可能只有遗留的
  `backend_node_id` 整数，此时运行时把它包进 `"bnid:<id>"` 惯例合成一个
  `DriverRef`（不变量 3 的例外仅限于这一条读路径，且合成后从不重新写回旧格式）。
  这条路径永远不构造 `NodeRef`。

## 数据结构

```python
@dataclass(frozen=True)
class NodeRef:
    backend_node_id: int
    frame_id: str | None = None

@dataclass(frozen=True)
class DriverRef:
    handle: str
    driver_generation: int

ElementRef = IndexRef | SelectorRef | TextRef | NodeRef | DriverRef

@dataclass(frozen=True)
class ObservedElement:
    index: int
    driver_ref: DriverRef                 # 必填
    frame_id: str | None
    tag: str
    role: str | None
    name: str | None
    value: str | None
    backend_node_id: int | None = None    # 可选
    attributes: dict[str, str] = field(default_factory=dict)
    box: Box | None = None
    visible: bool = False
    scrollable: bool = False

@dataclass(frozen=True)
class ResolvedElement:
    driver_ref: DriverRef                 # 必填
    frame_id: str | None
    box: Box | None
    visible: bool
    tag: str
    backend_node_id: int | None = None    # 可选
    attributes: dict[str, str] = field(default_factory=dict)
```

`driver_ref` 与 `backend_node_id` 的必填/可选关系（不变量 4）是本 spec 的核心
数据形状约束，任何新增字段都不得反转这一顺序。

## 与其它 spec 的关系

- `S_05_tools-contract.md`：`browser_move` 下注册的工具（`browser_click` /
  `browser_type` / ...）是本协议的**调用方**之一，通过 `runtime/` 间接使用
  `BrowserDriver`，不直接持有驱动引用。
- 本 spec 不覆盖遗留 Playwright-MCP 路径的工具目录差异或后端选择逻辑（那是
  `runtime/config.py:resolve_browser_driver_backend()` 与
  `backends/contract/registry.py` 的职责，二者当前均在既定的重构范围之外，见
  `F_04` 的"已知遗留"）。