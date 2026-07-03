"""Assemble the system prompt: the global state-machine prompt plus the
per-language skill, mirroring how open-WebUI exposed both to the model.
"""

import os

import config


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_system(language: str) -> str:
    system = _read(config.SYSTEM_PROMPT_PATH)

    skill_path = config.LANGUAGE_SKILL_MAP.get(language) or config.LANGUAGE_SKILL_MAP.get(
        config.DEFAULT_LANGUAGE
    )
    skill = ""
    if skill_path and os.path.exists(skill_path):
        skill = _read(skill_path)

    if skill:
        return f"{system}\n\n---\n\n## Active Skill\n\n{skill}"
    return system
