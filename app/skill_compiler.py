"""
skill_compiler.py
=================
Compiles a synthesized `Procedure` into Anthropic Agent Skill format
(SKILL.md + YAML frontmatter), so any agent that consumes skills (Claude,
Claude Code, or your customer's own harness via MCP) can load it.

This is the agent-facing render of the Company Brain. The synthesis layer
produced agent-AGNOSTIC structured data on purpose; this module is the thin,
swappable compile step on top of it.

Format rules enforced (per the Agent Skill spec):
  * frontmatter `name`: kebab-case, <= 64 chars
  * frontmatter `description`: <= 1024 chars, third-person, carries ALL the
    "when to use" info, written slightly "pushy" to avoid under-triggering
  * body: imperative instructions, output/guardrail templates spelled out
  * a machine-readable `x-krab` frontmatter block + a human-readable
    "Autonomy & trust" section, so an agent harness can refuse to auto-execute
    anything that hasn't earned `autonomous`.

Pure module: no DB/network. Callers pass an ORM `Procedure` (with .steps,
.rules, .sources loaded) and get back a CompiledSkillResult.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.procedure_models import AutonomyLevel, ProcedureStatus, RuleType

NAME_MAX = 64
DESC_MAX = 1024


@dataclass
class CompiledSkillResult:
    """In-memory render result. Not persisted; callers decide what to do with it."""
    name: str                       # kebab-case skill id (== procedure slug)
    description: str                # frontmatter description (<=1024)
    markdown: str                   # full SKILL.md text (frontmatter + body)
    metadata: Dict[str, Any] = field(default_factory=dict)  # structured x-krab
    warnings: List[str] = field(default_factory=list)


def _kebab(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s or "procedure")[:NAME_MAX]


def _autonomy_value(proc) -> str:
    a = proc.autonomy_level
    return a.value if isinstance(a, AutonomyLevel) else str(a)


def _status_value(proc) -> str:
    st = proc.status
    return st.value if isinstance(st, ProcedureStatus) else str(st)


def _rule_value(r) -> str:
    return r.rule_type.value if isinstance(r.rule_type, RuleType) else str(r.rule_type)


def _build_description(proc) -> tuple[str, List[str]]:
    """Compose a triggering description from the procedure's own fields.

    Slightly pushy on purpose: agents under-trigger skills, so we explicitly
    enumerate trigger phrasing. Capped at DESC_MAX.
    """
    warnings: List[str] = []
    base = (proc.description or proc.title or "").strip().rstrip(".")
    trigger = (proc.trigger_condition or "").strip().rstrip(".")
    domain = (proc.domain or "").strip()

    parts = [f"{base}." if base else f"{proc.title}."]
    if trigger:
        parts.append(
            f"Use this whenever {trigger.lower()}, even if the request doesn't "
            f"name this procedure explicitly."
        )
    if domain:
        parts.append(f"Domain: {domain}.")
    desc = " ".join(parts)
    if len(desc) > DESC_MAX:
        desc = desc[: DESC_MAX - 1].rstrip() + "\u2026"
        warnings.append("description truncated to 1024 chars")
    return desc, warnings


def _provenance(ids: Optional[List[int]]) -> str:
    if not ids:
        return ""
    shown = ", ".join(f"#{i}" for i in ids[:8])
    more = "" if len(ids) <= 8 else f" (+{len(ids) - 8} more)"
    return f" _(sources: {shown}{more})_"


def compile_procedure_to_skill(
    proc, *, include_provenance: bool = True
) -> CompiledSkillResult:
    """Render one Procedure ORM object to a CompiledSkillResult."""
    name = _kebab(proc.slug or proc.title)
    description, warnings = _build_description(proc)

    autonomy = _autonomy_value(proc)
    status = _status_value(proc)
    confidence = round(float(proc.synthesis_confidence or 0.0), 3)
    success_rate = proc.success_rate

    steps = sorted(proc.steps, key=lambda s: s.step_order)
    guardrails = [r for r in proc.rules if _rule_value(r) == RuleType.guardrail.value]
    decisions = [r for r in proc.rules if _rule_value(r) == RuleType.decision.value]
    inputs = [r for r in proc.rules if _rule_value(r) == RuleType.required_input.value]

    # ---- machine-readable safety block (frontmatter extension) ---------- #
    x_krab = {
        "procedure_id": proc.id,
        "version": proc.version,
        "status": status,
        "autonomy_level": autonomy,
        "synthesis_confidence": confidence,
        "execution_count": proc.execution_count,
        "success_rate": round(success_rate, 3) if success_rate is not None else None,
        "source_chunk_ids": [s.chunk_id for s in proc.sources],
    }

    # ---- frontmatter ---------------------------------------------------- #
    fm_lines = [
        "---",
        f"name: {name}",
        f"description: {_yaml_scalar(description)}",
        "x-krab:",
        f"  procedure_id: {proc.id}",
        f"  version: {proc.version}",
        f"  status: {status}",
        f"  autonomy_level: {autonomy}",
        f"  synthesis_confidence: {confidence}",
        f"  execution_count: {proc.execution_count}",
        f"  success_rate: {x_krab['success_rate']}",
        "---",
    ]

    # ---- body ----------------------------------------------------------- #
    body: List[str] = [f"# {proc.title}", ""]

    # Autonomy banner first so an agent can gate before doing anything.
    body += _autonomy_section(autonomy, status, confidence, proc, success_rate)

    if proc.trigger_condition:
        body += ["## When to use", proc.trigger_condition.strip(), ""]

    if inputs:
        body.append("## Required inputs")
        body.append("Confirm you have these before starting:")
        for r in inputs:
            body.append(f"- {r.content.strip()}")
        body.append("")

    body.append("## Steps")
    if steps:
        body.append("Follow these in order:")
        for i, s in enumerate(steps, 1):
            prov = _provenance(s.source_chunk_ids) if include_provenance else ""
            body.append(f"{i}. {s.instruction.strip()}{prov}")
    else:
        body.append("_No steps were extracted; do not execute autonomously._")
    body.append("")

    if decisions:
        body.append("## Decision rules")
        body.append("Apply these where they match the situation:")
        for r in decisions:
            prov = _provenance(r.source_chunk_ids) if include_provenance else ""
            body.append(f"- {r.content.strip()}{prov}")
        body.append("")

    body.append("## Guardrails")
    if guardrails:
        body.append("These are hard limits. NEVER cross them:")
        for r in guardrails:
            prov = _provenance(r.source_chunk_ids) if include_provenance else ""
            body.append(f"- {r.content.strip()}{prov}")
    else:
        body.append(
            "No explicit guardrails were extracted. Be conservative and confirm "
            "with a human before any irreversible action."
        )
    body.append("")

    if include_provenance and x_krab["source_chunk_ids"]:
        body.append("## Provenance")
        body.append(
            "This procedure was synthesized from the company's own knowledge "
            f"(source chunk IDs: {', '.join('#'+str(i) for i in x_krab['source_chunk_ids'][:20])}). "
            "If a step seems wrong, the source is the ground truth to check."
        )
        body.append("")

    markdown = "\n".join(fm_lines) + "\n\n" + "\n".join(body).rstrip() + "\n"
    return CompiledSkillResult(
        name=name,
        description=description,
        markdown=markdown,
        metadata=x_krab,
        warnings=warnings,
    )


def _autonomy_section(
    autonomy: str, status: str, confidence: float, proc, success_rate
) -> List[str]:
    if autonomy == AutonomyLevel.autonomous.value:
        directive = (
            "This procedure is APPROVED FOR AUTONOMOUS EXECUTION. It has a proven "
            "track record. Execute it directly, but still respect every guardrail."
        )
    elif autonomy == AutonomyLevel.suggest_only.value:
        directive = (
            "SUGGEST ONLY. Draft the actions and outcome, then REQUIRE A HUMAN to "
            "approve before anything irreversible happens. Do not execute unattended."
        )
    else:  # human_required
        directive = (
            "HUMAN REQUIRED. Do NOT execute. Surface this procedure to a person and "
            "let them drive. It is unverified, low-confidence, or has an open conflict."
        )
    rate = f"{success_rate:.0%}" if success_rate is not None else "no history yet"
    return [
        "## Autonomy & trust",
        f"**Autonomy level: `{autonomy}`** \u2014 {directive}",
        "",
        f"- Status: `{status}`",
        f"- Synthesis confidence: {confidence:.0%}",
        f"- Executions: {proc.execution_count} (success rate: {rate})",
        "",
    ]


def _yaml_scalar(s: str) -> str:
    """Quote a description safely for single-line YAML."""
    if any(ch in s for ch in (":", "#", "\n", '"')) or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


# --------------------------------------------------------------------------- #
# DB persistence — upsert a CompiledSkill ORM row
# --------------------------------------------------------------------------- #
def upsert_compiled_skill(
    db,
    *,
    procedure,
    company_id: str,
    user_id: int,
    commit: bool = True,
):
    """Compile `procedure` and upsert its CompiledSkill DB row.

    Uses compile_procedure_to_skill() for rendering so the DB row and any
    filesystem export always use the same logic. Caller must ensure the
    procedure is verified.
    """
    from app.procedure_models import CompiledSkill as CompiledSkillRow

    result = compile_procedure_to_skill(procedure)

    skill = (
        db.query(CompiledSkillRow)
        .filter(
            CompiledSkillRow.company_id == company_id,
            CompiledSkillRow.slug == procedure.slug,
        )
        .first()
    )

    if skill is None:
        skill = CompiledSkillRow(
            company_id=company_id,
            user_id=user_id,
            procedure_id=procedure.id,
            slug=procedure.slug,
            version=1,
        )
        db.add(skill)
    else:
        skill.version += 1
        skill.procedure_id = procedure.id

    skill.title = procedure.title or procedure.slug
    skill.description = result.description
    skill.skill_md = result.markdown
    skill.autonomy_level = result.metadata.get("autonomy_level", "suggest_only")
    skill.source_procedure_version = getattr(procedure, "version", None)

    if commit:
        db.commit()
        db.refresh(skill)
    return skill


# --------------------------------------------------------------------------- #
# Batch export to a skills/ directory (for Claude Code / filesystem use)
# --------------------------------------------------------------------------- #
def export_skills_to_dir(
    procedures, out_dir: str, *, include_provenance: bool = True
) -> Dict[str, Any]:
    """Write each procedure as out_dir/<name>/SKILL.md and a manifest.json.

    `procedures` is an iterable of ORM Procedure objects. Returns a manifest.
    """
    import json
    import os

    os.makedirs(out_dir, exist_ok=True)
    manifest: List[Dict[str, Any]] = []
    for proc in procedures:
        skill = compile_procedure_to_skill(proc, include_provenance=include_provenance)
        skill_dir = os.path.join(out_dir, skill.name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(skill.markdown)
        manifest.append(
            {
                "name": skill.name,
                "path": os.path.join(skill.name, "SKILL.md"),
                "autonomy_level": skill.metadata.get("autonomy_level"),
                "status": skill.metadata.get("status"),
                "confidence": skill.metadata.get("synthesis_confidence"),
            }
        )
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"skills": manifest}, fh, indent=2)
    return {"out_dir": out_dir, "count": len(manifest), "skills": manifest}
