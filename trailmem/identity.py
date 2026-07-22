"""Canonical host-independent session identity."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from .errors import ValidationError

SCHEMA_VERSION = 1
MAX_SESSION_ID_LENGTH = 512
CONTEXT_EVENTS = frozenset({"session-start", "session-stop", "tool-context"})
_AGENT_RE = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_SOURCE_RE = re.compile(r"[a-z][a-z0-9_-]{0,79}")


def resolve_agent(agent_type: str | None, env: Mapping[str, str] = os.environ) -> str:
    agent = agent_type or env.get("TRAILMEM_AGENT_TYPE")
    if agent in ("human", "me"):
        agent = "user"
    if not agent:
        raise ValidationError(
            "agent_type could not be determined. Set TRAILMEM_AGENT_TYPE, "
            "pass agent_type explicitly, or install a host adapter.")
    if not _AGENT_RE.fullmatch(agent):
        raise ValidationError(
            "agent_type must be a lowercase slug matching [a-z][a-z0-9_-]{0,39}")
    return agent


def resolve_project(
    project: str | None,
    env: Mapping[str, str] = os.environ,
    *,
    cwd: str | None = None,
) -> str | None:
    explicit = project if project is not None else env.get("TRAILMEM_PROJECT")
    if explicit is not None and not isinstance(explicit, str):
        raise ValidationError("project must be a string, 'global', or null")
    if explicit is not None and explicit != "global" and not os.path.isabs(explicit):
        raise ValidationError(
            f"project must be an absolute path or 'global', got {explicit!r}. "
            "Omit it to use the current directory, or pass the full path.")
    resolved = explicit if explicit is not None else (cwd or os.getcwd())
    if resolved == "global":
        return None
    # Canonicalize so symlink/mount aliases of one repo share one project scope.
    return os.path.realpath(resolved)


def session_key(agent_type: str | None, external_session_id: str | None) -> str | None:
    value = str(external_session_id or "").strip()
    if not value or not agent_type:
        return None
    prefix = f"{agent_type}:"
    return value if value.startswith(prefix) else prefix + value


@dataclass(frozen=True)
class SessionContext:
    agent_type: str
    session_id: str | None
    project: str | None
    event: str | None = None
    source: str = "generic"
    schema_version: int = SCHEMA_VERSION

    @property
    def key(self) -> str | None:
        return session_key(self.agent_type, self.session_id)

    def to_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "agent_type": self.agent_type,
            "session_id": self.session_id,
            "project": self.project,
            "event": self.event,
            "source": self.source,
        }

    @classmethod
    def create(
        cls,
        *,
        agent_type: str | None,
        session_id: str | None = None,
        project: str | None = None,
        event: str | None = None,
        source: str = "generic",
        env: Mapping[str, str] = os.environ,
        cwd: str | None = None,
    ) -> "SessionContext":
        agent = resolve_agent(agent_type, env)
        resolved_project = resolve_project(project, env, cwd=cwd)
        if session_id is not None and not isinstance(session_id, str):
            raise ValidationError("session_context session_id must be a string or null")
        value = session_id.strip() if session_id else None
        if value and len(value) > MAX_SESSION_ID_LENGTH:
            raise ValidationError(
                f"session_context session_id must be at most "
                f"{MAX_SESSION_ID_LENGTH} characters")
        if event is not None and (
                not isinstance(event, str) or event not in CONTEXT_EVENTS):
            raise ValidationError(
                f"session_context event must be null or one of "
                f"{sorted(CONTEXT_EVENTS)}")
        if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
            raise ValidationError(
                "session_context source must be a lowercase slug of at most 80 characters")
        return cls(agent, value or None, resolved_project, event, source)

    @classmethod
    def from_payload(
        cls,
        payload: dict,
        *,
        pinned_agent: str | None = None,
        env: Mapping[str, str] = os.environ,
    ) -> "SessionContext":
        if not isinstance(payload, dict):
            raise ValidationError("session_context must be an object")
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported session_context schema_version {version!r}")
        agent = resolve_agent(payload.get("agent_type") or pinned_agent, env)
        if pinned_agent and agent != pinned_agent:
            raise ValidationError(
                f"session_context agent_type {agent!r} conflicts with "
                f"TRAILMEM_AGENT_TYPE {pinned_agent!r}")
        project = payload.get("project")
        if project is None:
            project = "global"
        source = payload["source"] if "source" in payload else "canonical"
        return cls.create(
            agent_type=agent,
            session_id=payload.get("session_id"),
            project=project,
            event=payload.get("event"),
            source=source,
            env=env,
        )
