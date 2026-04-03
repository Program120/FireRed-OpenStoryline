"""
Skill discovery and loading from filesystem.

Scans three directories in priority order:
  1. skills/custom/      (user-defined, highest priority)
  2. skills/installed/   (from registry)
  3. skills/builtin/     (shipped with project, lowest priority)

Each skill directory must contain a SKILL.md with YAML frontmatter.
Optionally contains handler.py with a Handler(SkillHandler) class.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from open_storyline.core.skill import (
    ConcurrencyMeta,
    PipelineMeta,
    SkillHandler,
    SkillMeta,
)
from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)

# Directories scanned in priority order (last wins on conflict)
_SKILL_SOURCE_DIRS = [
    ("builtin", "skills/builtin"),
    ("installed", "skills/installed"),
    ("custom", "skills/custom"),
]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from SKILL.md content.

    Returns (frontmatter_dict, markdown_body).
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    yaml_str = text[3:end_idx].strip()
    body = text[end_idx + 3:].strip()

    try:
        fm = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"Failed to parse SKILL.md frontmatter: {exc}")
        fm = {}

    return fm, body


def _build_pipeline_meta(raw: Any) -> PipelineMeta:
    if not isinstance(raw, dict):
        return PipelineMeta(skill_id="")
    return PipelineMeta(
        skill_id=raw.get("skill_id", ""),
        display_name=raw.get("display_name", ""),
        node_kind=raw.get("node_kind", ""),
        depends_on=raw.get("depends_on") or [],
        next_skills=raw.get("next_skills") or [],
    )


def _build_concurrency_meta(raw: Any) -> ConcurrencyMeta:
    if not isinstance(raw, dict):
        return ConcurrencyMeta()
    return ConcurrencyMeta(
        supports_batching=bool(raw.get("supports_batching", False)),
        batch_key=raw.get("batch_key", ""),
        default_batch_size=int(raw.get("default_batch_size", 1)),
        max_concurrency=int(raw.get("max_concurrency", 1)),
        allow_partial=bool(raw.get("allow_partial", False)),
        max_retries=int(raw.get("max_retries", 2)),
    )


def _load_handler_class(skill_dir: Path, handler_file: str) -> Optional[type]:
    """
    Dynamically import handler.py and return the Handler class.
    """
    handler_path = skill_dir / handler_file
    if not handler_path.exists():
        logger.warning(f"handler file not found: {handler_path}")
        return None

    module_name = f"skill_handler_{skill_dir.name.replace('-', '_')}"

    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        logger.error(f"Cannot create module spec for {handler_path}")
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.error(f"Failed to load handler {handler_path}: {exc}")
        del sys.modules[module_name]
        return None

    handler_cls = getattr(module, "Handler", None)
    if handler_cls is None:
        logger.warning(f"No 'Handler' class found in {handler_path}")
        return None

    if not (isinstance(handler_cls, type) and issubclass(handler_cls, SkillHandler)):
        logger.warning(f"Handler in {handler_path} does not extend SkillHandler")
        return None

    return handler_cls


def parse_skill_md(skill_dir: Path, source: str = "builtin") -> Optional[SkillMeta]:
    """
    Parse a single SKILL.md file and return SkillMeta.

    Args:
        skill_dir: Directory containing SKILL.md
        source:    Origin label (builtin / installed / custom)

    Returns:
        SkillMeta or None if parsing fails.
    """
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return None

    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Cannot read {skill_md_path}: {exc}")
        return None

    fm, body = _parse_frontmatter(content)
    if not fm.get("name"):
        logger.warning(f"SKILL.md in {skill_dir} has no 'name' in frontmatter, skipping")
        return None

    handler_file = fm.get("handler")

    return SkillMeta(
        name=fm["name"],
        description=fm.get("description", ""),
        version=fm.get("version", "0.0.0"),
        source=source,
        local_path=skill_dir,
        handler_file=handler_file,
        pipeline=_build_pipeline_meta(fm.get("pipeline")),
        concurrency=_build_concurrency_meta(fm.get("concurrency")),
        body=body,
    )


class SkillLoader:
    """
    Discovers and loads skills from the filesystem.

    Usage:
        loader = SkillLoader(project_root=Path("."))
        loader.scan()

        meta = loader.get_meta("load-media")
        handler = loader.get_handler("load-media")
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        # skill_id -> SkillMeta  (later sources override earlier ones)
        self._skills: Dict[str, SkillMeta] = {}
        # skill_id -> Handler class (lazy-loaded)
        self._handler_classes: Dict[str, Optional[type]] = {}
        # skill_id -> Handler instance (lazy-instantiated)
        self._handler_instances: Dict[str, Optional[SkillHandler]] = {}

    def scan(self, extra_dirs: Optional[List[tuple[str, str]]] = None) -> int:
        """
        Scan all skill directories and populate the registry.

        Args:
            extra_dirs: Additional (source, relative_path) pairs to scan.

        Returns:
            Number of skills discovered.
        """
        dirs_to_scan = list(_SKILL_SOURCE_DIRS)
        if extra_dirs:
            dirs_to_scan.extend(extra_dirs)

        for source, rel_path in dirs_to_scan:
            base_dir = self.project_root / rel_path
            if not base_dir.is_dir():
                continue

            for child in sorted(base_dir.iterdir()):
                if not child.is_dir():
                    continue
                if child.name.startswith(".") or child.name.startswith("_"):
                    continue

                meta = parse_skill_md(child, source=source)
                if meta is None:
                    continue

                skill_id = meta.pipeline.skill_id or meta.name
                self._skills[skill_id] = meta
                # Clear cached handler on rescan
                self._handler_classes.pop(skill_id, None)
                self._handler_instances.pop(skill_id, None)
                logger.info(f"Loaded skill: {skill_id} ({source}) from {child}")

        logger.info(f"Total skills loaded: {len(self._skills)}")
        return len(self._skills)

    @property
    def skill_ids(self) -> List[str]:
        return list(self._skills.keys())

    def get_meta(self, skill_id: str) -> Optional[SkillMeta]:
        return self._skills.get(skill_id)

    def get_all_meta(self) -> Dict[str, SkillMeta]:
        return dict(self._skills)

    def get_handler(self, skill_id: str) -> Optional[SkillHandler]:
        """
        Get (or lazily create) a SkillHandler instance for the given skill.

        Returns None if the skill has no handler.py or loading fails.
        """
        if skill_id in self._handler_instances:
            return self._handler_instances[skill_id]

        meta = self._skills.get(skill_id)
        if meta is None:
            logger.warning(f"Skill '{skill_id}' not found")
            return None

        if meta.handler_file is None:
            # Pure-prompt skill, no Python handler
            self._handler_instances[skill_id] = None
            return None

        if meta.local_path is None:
            logger.warning(f"Skill '{skill_id}' has handler_file but no local_path")
            self._handler_instances[skill_id] = None
            return None

        # Load handler class
        if skill_id not in self._handler_classes:
            cls = _load_handler_class(meta.local_path, meta.handler_file)
            self._handler_classes[skill_id] = cls

        cls = self._handler_classes[skill_id]
        if cls is None:
            self._handler_instances[skill_id] = None
            return None

        # Instantiate and inject meta
        try:
            instance = cls()
            instance.meta = meta
            self._handler_instances[skill_id] = instance
            return instance
        except Exception as exc:
            logger.error(f"Failed to instantiate handler for '{skill_id}': {exc}")
            self._handler_instances[skill_id] = None
            return None

    def has_handler(self, skill_id: str) -> bool:
        meta = self._skills.get(skill_id)
        return meta is not None and meta.handler_file is not None
