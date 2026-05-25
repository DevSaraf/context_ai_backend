"""
synthesis_prompts.py
====================
Prompt templates for the synthesis layer, isolated so they can be tuned and
versioned without touching pipeline code.

The extraction prompt is the single most important quality lever in the whole
Company Brain. It must turn a pile of related chunks into ONE structured,
executable procedure -- or correctly decide the cluster is just reference facts
and not a procedure at all.
"""

# Bumping this when you change the prompt lets you correlate output quality
# with prompt versions in your logs.
EXTRACTION_PROMPT_VERSION = "v1"

# The model must return ONLY this JSON object. `source_hints` reference the
# [n] index of a chunk within the batch we send, so we can map a step/rule
# back to the exact knowledge_chunk it came from (provenance).
EXTRACTION_SYSTEM_PROMPT = """\
You convert a company's scattered internal knowledge into ONE executable \
procedure that an AI agent can follow safely and consistently.

You are given numbered source excerpts ([0], [1], ...) that were grouped \
because they appear related. Your job is to decide whether they describe a \
repeatable PROCEDURE (how something gets DONE: e.g. how refunds are handled, \
how pricing exceptions are approved, how an incident is triaged) and, if so, \
to express it as structured steps, decision rules, and guardrails.

CRITICAL RULES:
- Use ONLY information present in the sources. Do NOT invent steps, numbers, \
thresholds, names, or systems. If the sources don't say it, leave it out.
- If the sources are just facts/definitions/marketing and do NOT describe a \
repeatable process, set "is_procedure" to false and stop.
- For every step, decision rule, and guardrail, list the source index numbers \
it came from in "source_hints".
- Guardrails are hard limits an agent must NEVER cross (phrase them as \
"NEVER ..." or "ALWAYS ..."). Prefer to be conservative: when sources imply a \
limit, capture it as a guardrail.
- If two sources disagree on a value or rule (e.g. "30 days" vs "14 days"), \
do NOT silently pick one. Record it in "internal_conflicts".
- "extraction_confidence" reflects how clearly and completely the sources \
define the procedure (1.0 = unambiguous and complete, 0.3 = fragmentary).

Return ONLY a JSON object with EXACTLY these keys and no prose, no markdown \
fences:
{
  "is_procedure": boolean,
  "title": string,
  "slug": string,                  // kebab-case, e.g. "process-customer-refund"
  "description": string,           // one sentence: "Use when ..."
  "domain": string,                // e.g. "support", "billing", "engineering"
  "trigger_condition": string,     // the situation that starts this procedure
  "steps": [
    { "instruction": string, "source_hints": [int, ...] }
  ],
  "decision_rules": [
    { "content": string, "source_hints": [int, ...] }
  ],
  "guardrails": [
    { "content": string, "source_hints": [int, ...] }
  ],
  "required_inputs": [string, ...],
  "internal_conflicts": [
    { "description": string, "source_a": int, "source_b": int,
      "type": "value_mismatch" | "contradiction" | "outdated" | "ambiguous" }
  ],
  "extraction_confidence": number, // 0.0 - 1.0
  "coverage_note": string
}
"""


def build_extraction_user_prompt(chunk_texts: list[str]) -> str:
    """Render the numbered source block for one cluster."""
    blocks = []
    for i, text in enumerate(chunk_texts):
        snippet = text.strip()
        if len(snippet) > 1800:  # keep token cost bounded; steps rarely need more
            snippet = snippet[:1800] + " ...[truncated]"
        blocks.append(f"[{i}]\n{snippet}")
    sources = "\n\n---\n\n".join(blocks)
    return (
        "SOURCE EXCERPTS (grouped because they appear related):\n\n"
        f"{sources}\n\n"
        "Produce the single best executable procedure these sources describe, "
        "following the system instructions exactly. Return ONLY the JSON object."
    )


# Cross-procedure conflict pass: given short summaries of two procedures that
# look similar, decide if they actually contradict each other.
CONFLICT_SYSTEM_PROMPT = """\
You compare two company procedures and decide whether they CONTRADICT each \
other (an agent following one would violate the other) or are merely related \
but compatible.

Return ONLY this JSON:
{
  "conflict": boolean,
  "type": "value_mismatch" | "contradiction" | "outdated" | "ambiguous",
  "description": string
}
Use ONLY the provided text. If they are compatible or simply about different \
situations, return conflict=false.
"""


def build_conflict_user_prompt(proc_a: str, proc_b: str) -> str:
    return (
        f"PROCEDURE A:\n{proc_a}\n\n"
        f"PROCEDURE B:\n{proc_b}\n\n"
        "Do these contradict? Return ONLY the JSON object."
    )
