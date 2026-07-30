#!/usr/bin/env python3
"""Canonical repository and scaffold path anchors."""

from __future__ import annotations

from pathlib import Path

SCAFFOLD_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SCAFFOLD_ROOT.parent
SOURCES_ROOT = REPO_ROOT / "sources"
AGENTS_ROOT = SCAFFOLD_ROOT / ".agents"
REMOTE_DEV_ROOT = SCAFFOLD_ROOT / ".remote-dev"

MOTOR_ROOT = SOURCES_ROOT / "motor"
VLLM_ROOT = SOURCES_ROOT / "vllm"
VLLM_ASCEND_ROOT = SOURCES_ROOT / "vllm-ascend"
