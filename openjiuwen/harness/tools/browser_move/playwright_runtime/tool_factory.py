# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent tools built from the surface profile and the driver: one ``CommandTool`` per selected tool spec."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, AsyncIterator

from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput

from ..contract import (
    Action,
    ActionIntercepted,
    ActionResult,
    ActionTimeout,
    ContentBlock,
    ContextRef,
    DriverError,
    DriverInfo,
    DriverRef,
    Effect,
    Locator,
    Observation,
    Observe,
    PointRef,
    Ref,
    RefResolver,
    Renderer,
    Screenshot,
    StaleRef,
    SurfaceContext,
    SurfaceProfile,
    ToolSpec,
)
from ..drivers.middleware import masked
from .driver_session import DriverSession
from .page_state import BrowserPageState

Builder = Callable[[Mapping[str, Any], RefResolver], Action]

_ERROR = "### Error"


@dataclass(frozen=True)
class ToolContext:
    """What the profile's ``extra_tools`` and ``rails`` factories receive; ``driver`` is the session."""

    driver: DriverSession
    page_state: BrowserPageState
    renderer: Renderer
    driver_info: DriverInfo
    settings: Any
    image_support: bool
    workspace: Any = None
    language: str = "cn"

    async def execute(self, action: Action) -> ActionResult:
        """Run ``action`` on the driver session and record it in ``page_state``."""
        try:
            result = await self.driver.execute(action)
        except DriverError as error:
            if error.dispatched:
                self.page_state.note_dispatched()
            raise
        return self.page_state.record(action, result)

    async def observe(self, command: Observe) -> Observation:
        """Observe through the driver session and record the observation in ``page_state``."""
        return self.page_state.record(command, await self.driver.observe(command))


@dataclass(frozen=True)
class ToolFlags:
    """What an extra tool declares for the rails in place of its name."""

    effect: Effect = Effect.READ
    exempt_from_budget: bool = False
    progress: bool = True


@dataclass(frozen=True)
class CommandRecord:
    """What one tool call did, for the rails; ``action`` is masked, an ``Observe`` keeps its elements in ``value``."""

    command: type[Action]
    effect: Effect
    ok: bool
    dispatched: bool
    context_changed: bool = False
    error: str | None = None
    blocked_by: str | None = None
    surface: SurfaceContext | None = None
    value: Any = field(default=None, hash=False)
    generation: str = ""
    action: Action | None = None

    @classmethod
    def of(
        cls, action: Action, result: ActionResult, *, effect: Effect | None = None, value: Any = None
    ) -> CommandRecord:
        """The record of a command the driver ran; ``effect`` overrides the command's own."""
        return cls(
            command=type(action),
            effect=action.effect if effect is None else effect,
            ok=result.ok,
            dispatched=True,
            context_changed=result.context_changed,
            surface=result.surface,
            value=value,
            generation=result.generation,
            action=masked(action),
        )

    @classmethod
    def failed(
        cls, action: Action, error: DriverError, page_state: BrowserPageState, *, effect: Effect | None = None
    ) -> CommandRecord:
        """The record of a command the driver refused or failed."""
        return cls(
            command=type(action),
            effect=action.effect if effect is None else effect,
            ok=False,
            dispatched=error.dispatched,
            error=type(error).__name__,
            blocked_by=error.by if isinstance(error, ActionIntercepted) else None,
            surface=page_state.surface_context,
            generation=page_state.generation_id,
            action=masked(action),
        )

    @property
    def message_metadata(self) -> dict[str, bool]:
        """The tool message metadata the context processors read."""
        # Of the failed commands only a timeout counts as a possible change, as the text classifier counted it.
        mutating = self.effect is not Effect.READ and (self.ok or self.error == ActionTimeout.__name__)
        return {
            "success": self.ok,
            "executed": self.dispatched,
            "state_changed": self.context_changed or mutating,
            "denied": False,
        }


@dataclass(frozen=True)
class AgentTools:
    """``builders`` maps each command tool's model-facing name to its decoder, for the policy rail."""

    tools: tuple[Tool, ...]
    prompt_sections: tuple[str, ...]
    builders: Mapping[str, Builder]


class CommandTool(Tool):
    """One model-facing tool: its arguments become a command run on the driver session, its outcome rendered text."""

    def __init__(
        self, card: ToolCard, build: Builder, context: ToolContext, *, renders_observation: bool, image_source: str
    ) -> None:
        super().__init__(card)
        self._build = build
        self._context = context
        self._renders_observation = renders_observation
        self._image_source = image_source

    async def invoke(self, inputs: Mapping[str, Any], **kwargs: Any) -> ToolOutput:
        del kwargs
        page_state = self._context.page_state
        try:
            action = self._build(inputs or {}, page_state)
        except ValueError as error:
            return ToolOutput(success=False, error=f"{_ERROR}\n{error}")
        try:
            try:
                page_state.check(action)
            except StaleRef as stale:
                return self._failure(action, stale, await self._context.observe(Observe()))
            if isinstance(action, Observe):
                return self._observed(action, await self._context.observe(action))
            result = await self._context.execute(action)
        except DriverError as error:
            return self._failure(action, error, None)
        return self._output(action, result, result.value, self._context.renderer.result(action, result))

    async def stream(self, inputs: Mapping[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)

    def _observed(self, action: Observe, observation: Observation) -> ToolOutput:
        result = ActionResult(
            ok=True,
            value=observation.screenshot,
            generation=observation.generation,
            observation=observation,
            surface=observation.surface,
        )
        renderer = self._context.renderer
        blocks = renderer.observation(observation) if self._renders_observation else renderer.result(action, result)
        return self._output(action, result, observation.elements, blocks)

    def _output(self, action: Action, result: ActionResult, value: Any, blocks: tuple[ContentBlock, ...]) -> ToolOutput:
        data = self._data(blocks, CommandRecord.of(action, result, value=value))
        return ToolOutput(success=result.ok, data=data, error=None if result.ok else data["content"])

    def _failure(self, action: Action, error: DriverError, observation: Observation | None) -> ToolOutput:
        record = CommandRecord.failed(action, error, self._context.page_state)
        data = self._data(self._context.renderer.error(action, error, observation), record)
        return ToolOutput(success=False, data=data, error=data["content"])

    def _data(self, blocks: tuple[ContentBlock, ...], record: CommandRecord) -> dict[str, Any]:
        text = "\n".join(block for block in blocks if isinstance(block, str))
        images = [block for block in blocks if isinstance(block, Screenshot)] if self._context.image_support else []
        if not images:
            return {"content": text, "command": record}
        note = f"{len(images)} image(s) attached as multimodal input."
        return {
            "content": f"{text}\n\n{note}" if text else note,
            "multimodal": [self._image_item(image) for image in images],
            "command": record,
        }

    def _image_item(self, image: Screenshot) -> dict[str, str]:
        encoded = base64.b64encode(image.png).decode("ascii")
        return {
            "type": "image",
            "source": self._image_source,
            "source_path": self.card.name,
            "mime_type": "image/png",
            "data_url": f"data:image/png;base64,{encoded}",
        }


def command_record(tool_result: Any) -> CommandRecord | None:
    """The record a ``CommandTool`` left in its output; ``None`` for a call that decoded to no command."""
    data = getattr(tool_result, "data", None)
    record = data.get("command") if isinstance(data, Mapping) else None
    return record if isinstance(record, CommandRecord) else None


class ToolFactory:
    """Selects tool specs by capability and emits each as the driver's native tool or the profile's own form."""

    def __init__(self, profile: SurfaceProfile, driver_info: DriverInfo) -> None:
        self._profile = profile
        self._info = driver_info
        self._specs = {spec.name: spec for spec in profile.tool_specs}
        self._reported: set[str] = set()

    def build(
        self,
        capabilities: Iterable[str],
        *,
        image_support: bool,
        session: DriverSession,
        page_state: BrowserPageState,
        workspace: Any = None,
        language: str = "cn",
    ) -> AgentTools:
        context = ToolContext(
            session,
            page_state,
            self._profile.renderer,
            self._info,
            self._profile.settings,
            image_support,
            workspace,
            language,
        )
        tools: list[Tool] = []
        sections: list[str] = []
        builders: dict[str, Builder] = {}
        for spec, kinds in self._select(capabilities):
            emitted = self._emit(spec, kinds)
            if emitted is None:
                continue
            card, build = emitted
            screenshot_only = spec.commands == (Observe,) and self._builds_screenshot(build)
            if screenshot_only and not image_support:
                continue
            renders_observation = spec.commands == (Observe,) and not screenshot_only
            tools.append(
                CommandTool(
                    card, build, context, renders_observation=renders_observation, image_source=self._profile.name
                )
            )
            builders[card.name] = build
            if spec.prompt_section:
                sections.append(spec.prompt_section)
        tools.extend(factory(context) for factory in self._profile.extra_tools)
        names = {tool.card.name for tool in tools}
        for name in self._unserved(capabilities):
            if name not in names and self._info.tool_name_prefix + name not in names:
                self._report(name, "no tool spec and no extra tool serves it")
        return AgentTools(tuple(tools), tuple(sections), MappingProxyType(builders))

    def _select(self, capabilities: Iterable[str]) -> Iterable[tuple[ToolSpec, frozenset[type]]]:
        seen: set[str] = set()
        for entry in capabilities:
            names = self._profile.capability_tools.get(entry, ())
            if not names:
                self._report(entry, "the capability selects no tool")
            pairs = self._profile.capability_catalog.get(entry, frozenset())
            for name in names:
                spec = self._specs.get(name)
                if spec is None or name in seen:
                    continue
                seen.add(name)
                kinds = self._handled_kinds(spec, pairs)
                if kinds is None:
                    self._report(name, f"{self._info.name} does not handle its commands")
                    continue
                yield spec, kinds

    def _unserved(self, capabilities: Iterable[str]) -> list[str]:
        names = (name for entry in capabilities for name in self._profile.capability_tools.get(entry, ()))
        return [name for name in names if name not in self._specs]

    def _handled_kinds(self, spec: ToolSpec, pairs: frozenset[tuple[type[Action], type | None]]) -> frozenset | None:
        usable: set[type] = set()
        for command in spec.commands:
            if command is Observe:
                continue
            accepted = self._info.actions.get(command)
            if accepted is None:
                return None
            needed = {kind for paired, kind in pairs if paired is command}
            if needed and None not in needed and not needed & accepted:
                return None
            usable |= {kind for kind in needed if kind is not None} & accepted
        return frozenset(usable)

    def _emit(self, spec: ToolSpec, kinds: frozenset) -> tuple[ToolCard, Builder] | None:
        native = self._info.native_tools.get(spec.name)
        if native is not None:
            description, schema, build = native.description, native.schema, native.build
        elif spec.schema is not None and spec.build is not None:
            description, schema, build = self._description(spec), spec.schema, spec.build
        elif len(spec.commands) == 1 and kinds:
            description, schema, build = self._description(spec), _generated_schema(kinds), _generated_build(spec)
        else:
            self._report(spec.name, "no schema, and no single command with ref kinds to generate one from")
            return None
        name = self._info.tool_name_prefix + spec.name
        return ToolCard(id=name, name=name, description=description, input_params=dict(schema)), build

    def _description(self, spec: ToolSpec) -> str:
        if spec.description:
            return spec.description
        hints = [self._info.tool_hints[command] for command in spec.commands if command in self._info.tool_hints]
        return hints[0] if hints else spec.commands[0].__doc__ or spec.commands[0].__name__

    @staticmethod
    def _builds_screenshot(build: Builder) -> bool:
        # Without image input a tool whose plain call takes a screenshot is not offered; the core names no tools.
        try:
            action = build({}, _NO_REFS)
        except ValueError:
            return False
        return isinstance(action, Observe) and action.include_screenshot

    def _report(self, name: str, reason: str) -> None:
        if name not in self._reported:
            self._reported.add(name)
            tool_logger.info("tool %s skipped: %s", name, reason)


class _NoRefs:
    contexts: tuple[ContextRef, ...] = ()
    current_context: ContextRef | None = None
    screenshot_generation = ""

    def resolve(self, model_id: str, generation: str) -> Ref:
        raise ValueError(model_id)

    def generation_of(self, ref: str) -> str:
        return ""


_NO_REFS = _NoRefs()


def _generated_schema(kinds: frozenset) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    if DriverRef in kinds:
        properties["ref"] = {"type": "string"}
    if PointRef in kinds:
        properties["x"] = {"type": "integer"}
        properties["y"] = {"type": "integer"}
    if Locator in kinds:
        properties["css"] = {"type": "string"}
    return {"type": "object", "properties": properties}


def _generated_build(spec: ToolSpec) -> Builder:
    command = spec.commands[0]

    def build(args: Mapping[str, Any], resolver: RefResolver) -> Action:
        if args.get("ref"):
            ref = str(args["ref"])
            return command(DriverRef(ref, resolver.generation_of(ref)))
        if args.get("x") is not None and args.get("y") is not None:
            return command(PointRef(int(args["x"]), int(args["y"]), resolver.screenshot_generation))
        if args.get("css"):
            css = str(args["css"])
            return command(Locator(css=css, text=None, generation=resolver.generation_of(css)))
        return command()

    return build


__all__ = ["AgentTools", "CommandRecord", "CommandTool", "ToolContext", "ToolFactory", "ToolFlags", "command_record"]
