# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Who runs a task behind the facade: the main agent on the facade itself, or a worker through the service."""

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
    from .session import BrowserAgentRuntime


class Topology(Protocol):
    name: str

    async def start(self, runtime: BrowserAgentRuntime) -> None: ...

    async def run(
        self,
        runtime: BrowserAgentRuntime,
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
    """The main agent, built on the facade, calls the tools itself; one run per task under the guardrails' timeout."""

    name = "direct"

    def __init__(self) -> None:
        self._agent: Any = None

    async def start(self, runtime: BrowserAgentRuntime) -> None:
        if self._agent is not None:
            return
        from openjiuwen.harness.subagents.browser_agent import create_browser_agent

        # The runner reads model usage from the process log, where the worker's model calls land.
        runtime.dedicated_log = False
        service = runtime.service
        core = service.core
        model = _model(service)
        # The runner runs one asyncio.run per task, which a background probe would not outlive.
        await probe_image_support(model)
        self._agent = create_browser_agent(
            model,
            language=WORKER_LANGUAGE,
            temperature=core.worker_temperature,
            max_iterations=service.guardrails.max_steps,
            workspace=runtime.workspace,
            browser_capabilities=list(runtime.capabilities),
            runtime=runtime,
        )

    async def run(
        self,
        runtime: BrowserAgentRuntime,
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
        try:
            result = await asyncio.wait_for(Runner.run_agent(self._agent, inputs), timeout=float(timeout))
        except TimeoutError:
            return runtime.main_agent_answer(sid, rid, error=f"task_timeout: exceeded {timeout}s")
        except Exception as exc:  # noqa: BLE001 - the runner reads the error from the answer
            return runtime.main_agent_answer(sid, rid, error=str(exc) or repr(exc))
        return runtime.main_agent_answer(sid, rid, result=result if isinstance(result, dict) else {"output": result})

    def drop(self) -> None:
        self._agent = None


class DelegatedWorker:
    """The facade's caller hands the task to the service, which runs the worker over the session."""

    name = "delegated_worker"

    async def start(self, runtime: BrowserAgentRuntime) -> None:
        await runtime.ensure_runtime_ready()
        service = runtime.service
        if service.browser_agent is None:
            session = service.session
            image_support = await probe_image_support(_model(service)) is True
            service.browser_agent = await build_worker(
                session,
                session.assembly.surface,
                provider=service.provider,
                api_key=service.api_key,
                api_base=service.api_base,
                model_name=service.model_name,
                max_steps=service.guardrails.max_steps,
                core=service.core,
                capabilities=runtime.capabilities,
                image_support=image_support,
                tool_result_observer=service.observe_worker_tool_result,
                screenshot_subdir=service.screenshot_subdir,
                artifacts_subdir=service.artifacts_subdir,
            )
        runtime.register_runtime_tools()

    async def run(
        self,
        runtime: BrowserAgentRuntime,
        task: str,
        session_id: str | None,
        request_id: str | None,
        timeout_s: int | None,
    ) -> dict[str, Any]:
        async def worker() -> Any:
            await self.start(runtime)
            return runtime.service.browser_agent

        return await runtime.service.run_task(
            task=task, session_id=session_id, request_id=request_id, timeout_s=timeout_s, worker=worker
        )

    def drop(self) -> None:
        """The worker lives in the service's slot, which the service clears on reset and shutdown."""


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
