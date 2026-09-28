from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

FROZEN_AGENT_FIELDS = ("ai", "session")


def _mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ValueError(f"agent definition must be a mapping: {path}")
    return payload


def _agent_paths(recipe: Path) -> list[Path]:
    root_agent = recipe / "agents" / "agent.yaml"
    if not root_agent.is_file():
        raise ValueError(f"recipe has no root agent definition: {root_agent}")
    return sorted((recipe / "agents").glob("*.yaml"))


def _agent_selector(recipe: Path) -> object:
    package = json.loads((recipe / "package.json").read_text(encoding="utf-8"))
    if not isinstance(package, dict) or not isinstance(package.get("pi"), dict):
        raise ValueError(f"recipe package has no pi configuration: {recipe / 'package.json'}")
    return package["pi"].get("agents")


def stage_agent_configs(recipe: Path) -> None:
    """Remove host-owned agent configuration from the researcher's editable copy."""
    for path in _agent_paths(recipe):
        spec = _mapping(path)
        if any(field in spec for field in FROZEN_AGENT_FIELDS):
            for field in FROZEN_AGENT_FIELDS:
                spec.pop(field, None)
            path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


def materialize_agent_configs(candidate: Path, trusted: Path) -> None:
    """Restore frozen fields in a disposable recipe before checking or promotion."""
    if _agent_selector(candidate) != _agent_selector(trusted):
        raise ValueError("candidate cannot change package.json pi.agents")

    trusted_root = _mapping(trusted / "agents" / "agent.yaml")
    if "ai" not in trusted_root:
        raise ValueError("trusted root agent has no ai configuration")
    assembled: list[tuple[Path, dict[str, Any] | bytes]] = []
    for path in _agent_paths(candidate):
        spec = _mapping(path)
        forbidden = sorted(set(spec).intersection(FROZEN_AGENT_FIELDS))
        if forbidden:
            raise ValueError(f"candidate agent {', '.join(forbidden)} is not editable: {path}")
        trusted_path = trusted / path.relative_to(candidate)
        source = _mapping(trusted_path) if trusted_path.is_file() else trusted_root
        original_mutable = {
            key: value for key, value in source.items() if key not in FROZEN_AGENT_FIELDS
        }
        if trusted_path.is_file() and spec == original_mutable:
            assembled.append((path, trusted_path.read_bytes()))
            continue
        for field in FROZEN_AGENT_FIELDS:
            if field in source:
                spec[field] = deepcopy(source[field])
        assembled.append((path, spec))

    for path, payload in assembled:
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
