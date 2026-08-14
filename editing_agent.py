"""Local, auditable editing-rule memory for MyEstatePics Direct V4.0.

This module deliberately has no OpenAI import and no network capability.  It
only selects approved, relevant local rules and appends them beneath the
source-controlled production prompt supplied by the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RULE_DATABASE_VERSION = 1
RULES_FILENAME = "learned_rules.json"
FEEDBACK_FILENAME = "feedback_history.jsonl"
RULE_STATES = {"PROPOSED", "APPROVED", "DISABLED"}
RULE_CATEGORIES = {
    "GLOBAL", "INTERIOR", "EXTERIOR", "WINDOW", "SHEER_CURTAIN", "SKY",
    "WALL", "CEILING", "HARDWOOD", "CARPET", "CABINET", "MIRROR",
    "REFLECTION", "TWILIGHT", "ARTIFACT",
}
# The production prompt owns these safety-critical topics. Local memory may
# record evidence about them, but must not restate or compete with the master
# instruction in the one paid Images Edit request.
MASTER_AUTHORITATIVE_CATEGORIES = frozenset(
    {
        "WINDOW",
        "SKY",
        "SHEER_CURTAIN",
        "WALL",
        "CEILING",
        "HARDWOOD",
        "CARPET",
        "CABINET",
        "MIRROR",
        "REFLECTION",
    }
)
MASTER_DUPLICATE_RULE_IDS = frozenset(
    {
        "GLOBAL_MATERIALS_001",
        "GLOBAL_ARTIFACT_001",
        "INTERIOR_WALLS_001",
        "HARDWOOD_001",
        "MIRROR_REFLECTION_001",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class LearnedRule:
    id: str
    categories: tuple[str, ...]
    description: str
    instruction: str
    status: str = "PROPOSED"
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    approved_at: str | None = None
    times_applied: int = 0
    notes: str = ""
    source_reference: str = ""

    @classmethod
    def from_mapping(cls, value: Any) -> "LearnedRule | None":
        if not isinstance(value, dict):
            return None
        rule_id = str(value.get("id", "")).strip()
        categories = tuple(
            dict.fromkeys(
                str(item).strip().upper()
                for item in value.get("categories", [])
                if str(item).strip().upper() in RULE_CATEGORIES
            )
        )
        status = str(value.get("status", "PROPOSED")).upper().strip()
        instruction = str(value.get("instruction", "")).strip()
        description = str(value.get("description", "")).strip()
        if (
            not re.fullmatch(r"[A-Z0-9_]{3,64}", rule_id)
            or not categories
            or status not in RULE_STATES
            or not description
            or not instruction
            or len(instruction) > 1200
        ):
            return None
        return cls(
            id=rule_id,
            categories=categories,
            description=description,
            instruction=instruction,
            status=status,
            enabled=bool(value.get("enabled", True)),
            created_at=str(value.get("created_at", _now())),
            approved_at=(str(value["approved_at"]) if value.get("approved_at") else None),
            times_applied=max(0, int(value.get("times_applied", 0) or 0)),
            notes=str(value.get("notes", "")),
            source_reference=str(value.get("source_reference", "")),
        )


@dataclass(frozen=True)
class RuleSelection:
    instruction: str
    applied_rule_ids: tuple[str, ...]
    database_hash: str
    database_version: int
    context_categories: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    suppressed_rule_ids: tuple[str, ...] = ()


def _seed_rules() -> list[LearnedRule]:
    """Established contextual lessons, intentionally concise and non-duplicative."""
    approved = _now()
    return [
        LearnedRule("GLOBAL_MATERIALS_001", ("GLOBAL",), "Preserve permanent material identity.",
            "Keep permanent material colors, grain, texture, and identity faithful; never brighten by replacing material appearance.", "APPROVED", True, approved, approved),
        LearnedRule("GLOBAL_ARTIFACT_001", ("GLOBAL",), "Avoid artificial enhancement artifacts.",
            "Avoid crosshatch, checkerboard, fake weave, artificial microtexture, excessive clarity, sharpening halos, and edge ringing.", "APPROVED", True, approved, approved),
        LearnedRule("INTERIOR_WALLS_001", ("INTERIOR", "WALL", "CEILING"), "Preserve natural interior illumination.",
            "Preserve original paint color, natural illumination gradients, and architectural depth; avoid gray patches, wavy patches, flattened walls, and color drift.", "APPROVED", True, approved, approved),
        LearnedRule("HARDWOOD_001", ("HARDWOOD",), "Protect sunlit hardwood.",
            "Preserve original hardwood color, visible grain, sunlight, and natural highlight transitions; do not wash sunlit hardwood toward white or yellow.", "APPROVED", True, approved, approved),
        LearnedRule("MIRROR_REFLECTION_001", ("MIRROR", "REFLECTION"), "Remove equipment only without inventing reflections.",
            "When removing a photographer, camera, tripod, phone, or person reflection, preserve legitimate reflected architecture and never invent architecture to fill the reflection.", "APPROVED", True, approved, approved),
    ]


class EditingAgent:
    """Persistent local rule memory with safe degradation on bad runtime data."""

    def __init__(self, data_dir: Path, logger: logging.Logger | None = None):
        self.data_dir = Path(data_dir)
        self.rules_path = self.data_dir / RULES_FILENAME
        self.feedback_path = self.data_dir / FEEDBACK_FILENAME
        self.logger = logger or logging.getLogger(__name__)

    def _atomic_json_write(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def _document(self, create_if_missing: bool = True) -> dict[str, Any]:
        if not self.rules_path.exists():
            document = {"schema_version": RULE_DATABASE_VERSION, "rules": [asdict(rule) for rule in _seed_rules()]}
            if create_if_missing:
                try:
                    self._atomic_json_write(self.rules_path, document)
                    self._record_event("seeded", details="Created established V4.0 approved rules.")
                except Exception as error:  # Never make the production engine depend on memory.
                    self.logger.warning("Learning memory creation failed; using in-memory seeds: %s", error)
            return document
        try:
            value = json.loads(self.rules_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != RULE_DATABASE_VERSION:
                raise ValueError("unsupported learned-rule schema")
            if not isinstance(value.get("rules"), list):
                raise ValueError("rules must be a list")
            return value
        except Exception as error:
            self.logger.warning("Learning memory ignored; master prompt only: %s", error)
            return {"schema_version": RULE_DATABASE_VERSION, "rules": []}

    def database_hash(self) -> str:
        if not self.rules_path.exists():
            return "missing"
        try:
            return hashlib.sha256(self.rules_path.read_bytes()).hexdigest()
        except OSError:
            return "unreadable"

    def list_rules(self) -> list[LearnedRule]:
        rules = [LearnedRule.from_mapping(value) for value in self._document()["rules"]]
        return sorted((rule for rule in rules if rule), key=lambda rule: rule.id)

    @staticmethod
    def _is_master_conflict(rule: LearnedRule) -> bool:
        lowered = rule.instruction.casefold()
        forbidden = (
            "override master", "ignore master", "create a window", "invent architecture",
            "replace the exterior", "change room geometry", "automatic quality escalation",
        )
        return any(phrase in lowered for phrase in forbidden)

    @staticmethod
    def context_for(input_file: Path) -> tuple[str, ...]:
        """Use only direct, explainable filename signals; uncertainty stays GLOBAL."""
        name = input_file.stem.casefold()
        categories = ["GLOBAL"]
        if any(token in name for token in ("kitchen", "bath", "bed", "living", "dining", "office", "laundry", "hall", "basement", "garage", "interior")):
            categories.append("INTERIOR")
        if any(token in name for token in ("window", "sliding", "glass door", "patio door")):
            categories.append("WINDOW")
        if "sky" in name:
            categories.append("SKY")
        if any(token in name for token in ("sheer", "curtain")):
            categories.append("SHEER_CURTAIN")
        if any(token in name for token in ("mirror", "reflection")):
            categories.extend(("MIRROR", "REFLECTION"))
        if "hardwood" in name or "wood floor" in name:
            categories.append("HARDWOOD")
        return tuple(dict.fromkeys(categories))

    def build_instruction(self, master_prompt: str, input_file: Path) -> RuleSelection:
        context = self.context_for(input_file)
        selected: list[LearnedRule] = []
        conflicts: list[str] = []
        suppressed: list[str] = []
        seen = {master_prompt.casefold()}
        for rule in self.list_rules():
            if rule.status != "APPROVED" or not rule.enabled:
                continue
            if not set(rule.categories).intersection(context):
                continue
            if (
                rule.id in MASTER_DUPLICATE_RULE_IDS
                or set(rule.categories).intersection(MASTER_AUTHORITATIVE_CATEGORIES)
            ):
                suppressed.append(rule.id)
                self.logger.info(
                    "Learned rule suppressed because the master production prompt is authoritative: %s",
                    rule.id,
                )
                continue
            if self._is_master_conflict(rule):
                conflicts.append(rule.id)
                self.logger.warning("Learned rule rejected because master prompt wins: %s", rule.id)
                continue
            normalized = rule.instruction.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            selected.append(rule)
        addition = "\n".join(f"- [{rule.id}] {rule.instruction}" for rule in selected)
        instruction = master_prompt if not addition else f"{master_prompt}\n\nAPPROVED LOCAL EDITING LESSONS\n{addition}"
        return RuleSelection(
            instruction,
            tuple(rule.id for rule in selected),
            self.database_hash(),
            RULE_DATABASE_VERSION,
            context,
            tuple(conflicts),
            tuple(suppressed),
        )

    def _save_rules(self, rules: Iterable[LearnedRule]) -> None:
        self._atomic_json_write(self.rules_path, {"schema_version": RULE_DATABASE_VERSION, "rules": [asdict(rule) for rule in rules]})

    def _record_event(self, event: str, rule_id: str = "", **details: Any) -> None:
        try:
            self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"timestamp": _now(), "event": event, "rule_id": rule_id, **details}
            with self.feedback_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        except Exception as error:
            self.logger.warning("Learning history write failed: %s", error)

    def _replace(self, updated_rule: LearnedRule, event: str) -> None:
        rules = self.list_rules()
        self._save_rules(updated_rule if rule.id == updated_rule.id else rule for rule in rules)
        self._record_event(event, updated_rule.id)

    def approve(self, rule_id: str) -> None:
        for rule in self.list_rules():
            if rule.id == rule_id:
                self._replace(LearnedRule(**{**asdict(rule), "status": "APPROVED", "enabled": True, "approved_at": _now()}), "approved")
                return
        raise KeyError(rule_id)

    def disable(self, rule_id: str) -> None:
        for rule in self.list_rules():
            if rule.id == rule_id:
                self._replace(LearnedRule(**{**asdict(rule), "enabled": False, "status": "DISABLED"}), "disabled")
                return
        raise KeyError(rule_id)

    def enable(self, rule_id: str) -> None:
        for rule in self.list_rules():
            if rule.id == rule_id:
                self._replace(LearnedRule(**{**asdict(rule), "enabled": True, "status": "APPROVED", "approved_at": rule.approved_at or _now()}), "re_enabled")
                return
        raise KeyError(rule_id)

    def delete_proposed(self, rule_id: str) -> None:
        rules = self.list_rules()
        target = next((rule for rule in rules if rule.id == rule_id), None)
        if target is None:
            raise KeyError(rule_id)
        if target.status != "PROPOSED":
            raise ValueError("Only proposed rules may be deleted from Editing Memory.")
        self._save_rules(rule for rule in rules if rule.id != rule_id)
        self._record_event("deleted", rule_id)

    def record_applied(self, rule_ids: Iterable[str], *, filename: str, batch_id: str, quality: str) -> None:
        applied = set(rule_ids)
        if not applied:
            return
        try:
            updated = []
            for rule in self.list_rules():
                if rule.id in applied:
                    updated.append(LearnedRule(**{**asdict(rule), "times_applied": rule.times_applied + 1}))
                    self._record_event("applied", rule.id, filename=filename, batch_id=batch_id, quality=quality)
                else:
                    updated.append(rule)
            self._save_rules(updated)
        except Exception as error:
            self.logger.warning("Learning application audit could not be persisted: %s", error)
