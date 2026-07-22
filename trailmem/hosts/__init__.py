"""Auto-discovered host adapters.

Adding a host means adding one module that exports ``HOST``. Native session
fields, hook/config lifecycle, and detection stay owned by that module.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from typing import Mapping

from ..errors import ValidationError
from ..identity import SessionContext
from ._util import Artifact, Host  # noqa: F401 - public re-exports


def _discover() -> list[Host]:
    found = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        globals()[info.name] = module
        host = getattr(module, "HOST", None)
        if isinstance(host, Host):
            found.append(host)
    return found


HOSTS = _discover()
HOST_BY_AGENT = {host.agent: host for host in HOSTS}
if len(HOST_BY_AGENT) != len(HOSTS):
    raise RuntimeError("host adapters must use unique agent slugs")


def resolve_context(
    *,
    agent_type: str | None = None,
    payload: dict | None = None,
    canonical: dict | None = None,
    session_id: str | None = None,
    project: str | None = None,
    event: str | None = None,
    env: Mapping[str, str] = os.environ,
    required: bool = True,
) -> SessionContext | None:
    """Resolve native host input once, then return only canonical context."""
    selected_agent = agent_type or env.get("TRAILMEM_AGENT_TYPE")
    try:
        if canonical is not None:
            return SessionContext.from_payload(
                canonical, pinned_agent=selected_agent, env=env)
        host = HOST_BY_AGENT.get(selected_agent or "")
        if host:
            return host.resolve_context(
                payload, env, event=event, session_id=session_id, project=project)
        return SessionContext.create(
            agent_type=selected_agent,
            session_id=session_id or env.get("TRAILMEM_SESSION_ID"),
            project=project,
            event=event,
            source="generic-adapter",
            env=env,
        )
    except ValidationError:
        if required or canonical is not None:
            raise
        return None
