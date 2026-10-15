# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Who runs a task behind the runtime: the main agent on the runtime itself, or a worker through the service."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.harness.image_modality_probe import probe_image_support

from ..contract.registry import default_topology
from .agents import WORKER_LANGUAGE, build_worker
from .config import CoreSettings

if TYPE_CHECKING:
    from .session import AgentRuntime


class Topology(Protocol):
    name: str

    async def start(self, runtime: AgentRuntime) -> None: ...

    async def run(
        self,
        runtime: AgentRuntime,
        task: str,
        session_id: str | None,
        request_id: str | None,
        timeout_s: int | None,
    ) -> dict[str, Any]: ...

    def drop(self) -> None: ...


def _model(service: Any) -> Model:
    core = service.core
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=service.provider, api_key=service.api_key, api_base=service.api_base, verify_ssl=False
        ),
        model_config=ModelRequestConfig(
            model=service.model_name, temperature=core.worker_temperature, top_p=core.worker_top_p
        ),
    )


class Direct:
    """The main agent, built on the runtime, calls the tools itself; one run per task under the guardrails' timeout."""

    name = "direct"

    def __init__(self) -> None:
        self._agent: Any = None

    async def start(self, runtime: AgentRuntime) -> None:
        if self._agent is not None:
            return
        from openjiuwen.harness.subagents.browser_agent import create_browser_agent

        runtime.dedicated_log = False
        service = runtime.service
        core = service.core
        self._agent = create_browser_agent(
            _model(service),
            language=WORKER_LANGUAGE,
            temperature=core.worker_temperature,
            max_iterations=service.guardrails.max_steps,
            workspace=runtime.workspace,
            browser_capabilities=list(runtime.capabilities),
            runtime=runtime,
        )

    async def run(
        self,
        runtime: AgentRuntime,
        task: str,
        session_id: str | None,
        request_id: str | None,
        timeout_s: int | None,
    ) -> dict[str, Any]:
        service = runtime.service
        sid = service.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex
        timeout = int(timeout_s) if timeout_s else service.guardrails.timeout_s
        inputs = {"query": task, "conversation_id": f"{sid}.direct.{rid}.{uuid.uuid4().hex}", "request_id": rid}
        await probe_image_support(_model(service))
        try:
            result = await asyncio.wait_for(Runner.run_agent(self._agent, inputs), timeout=float(timeout))
        except TimeoutError:
            return runtime.main_agent_answer(sid, rid, error=f"task_timeout: exceeded {timeout}s")
        except Exception as exc:  # noqa: BLE001
            return runtime.main_agent_answer(sid, rid, error=str(exc) or repr(exc))
        return runtime.main_agent_answer(sid, rid, result=result if isinstance(result, dict) else {"output": result})

    def drop(self) -> None:
        self._agent = None


class DelegatedWorker:
    """The runtime's caller hands the task to the service, which runs the worker over the session."""

    name = "delegated_worker"

    def __init__(self) -> None:
        self._image_support = False
        self._offered: frozenset[str] = frozenset()
        self._dropped: frozenset[str] = frozenset()

    async def start(self, runtime: AgentRuntime) -> None:
        await runtime.ensure_runtime_ready()
        service = runtime.service
        if service.agent is None:
            session = service.session
            service.agent = await build_worker(
                session,
                session.assembly.surface,
                provider=service.provider,
                api_key=service.api_key,
                api_base=service.api_base,
                model_name=service.model_name,
                max_steps=service.guardrails.max_steps,
                core=service.core,
                capabilities=runtime.capabilities,
                tool_result_observer=service.observe_worker_tool_result,
                screenshot_subdir=service.screenshot_subdir,
                artifacts_subdir=service.artifacts_subdir,
                image_support=self._image_support,
            )
            if self._dropped:
                service.agent.ability_manager.remove_ability(sorted(self._dropped))
                self._dropped = frozenset()
            self._offered = _tool_names(service.agent)

    async def run(
        self,
        runtime: AgentRuntime,
        task: str,
        session_id: str | None,
        request_id: str | None,
        timeout_s: int | None,
    ) -> dict[str, Any]:
        if await probe_image_support(_model(runtime.service)) and not self._image_support:
            self._image_support = True
            started = runtime.service.agent
            if started is not None:
                self._dropped = self._offered - _tool_names(started)
                runtime.service.agent = None

        async def worker() -> Any:
            await self.start(runtime)
            return runtime.service.agent

        return await runtime.service.run_task(
            task=task, session_id=session_id, request_id=request_id, timeout_s=timeout_s, worker=worker
        )

    def drop(self) -> None:
        """The worker lives in the service's slot, which the service clears on reset and shutdown."""


def _tool_names(agent: Any) -> frozenset[str]:
    return frozenset(ability.name for ability in agent.ability_manager.list())


_TOPOLOGIES: dict[str, type[Direct] | type[DelegatedWorker]] = {
    Direct.name: Direct,
    DelegatedWorker.name: DelegatedWorker,
}


def topology_for(core: CoreSettings) -> Topology:
    """The strategy named by ``core.topology``, else the one the driver's registration declares as its default."""
    name = core.topology or default_topology(core.driver)
    if name not in _TOPOLOGIES:
        known = ", ".join(sorted(_TOPOLOGIES))
        raise ValueError(f"driver {core.driver!r} declares the topology {name!r}, which is none of: {known}")
    return _TOPOLOGIES[name]()


__all__ = ["WORKER_LANGUAGE", "DelegatedWorker", "Direct", "Topology", "topology_for"]
