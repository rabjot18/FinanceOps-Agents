"""FinanceOps owner briefing agent (Milestone 5 step 2): read-only, once a day.

Code gathers the facts (core.briefing_facts: totals, what needs the owner,
the most overdue cases, what happened in the last 24 hours). One Claude call
turns them into a short briefing with a fixed JSON shape (structured
outputs). The model has no tools and cannot change anything; its text is shown
to the owner as plain text only. Case IDs it mentions are kept only if they
appear in the facts it was given.

Model: FINANCEOPS_BRIEFING_MODEL (default claude-haiku-5-5, chosen in the
Milestone 5 plan for cheap summarizing). Its cost counts against the policy's
daily model budget.
"""
import json
import logging
from typing import List

import anthropic
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from core import FinanceCore

log = logging.getLogger("financeops.briefing")
MAX_TOKENS = 4_000

SYSTEM_PROMPT = """\
You write a short daily briefing for the owner of a small service business \
about money owed to them. You get facts as JSON, computed by their system. \
Describe only those facts; do not invent numbers, names or causes. Amounts \
are integer cents in CAD: 150000 means CAD 1,500.00. Write plain sentences, \
no markdown. Lead with what needs the owner today, then notable changes, then \
the slowest payers. Keep it under 150 words in total."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "case_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["text", "case_ids"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "items"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "sections"],
    "additionalProperties": False,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Item(_Strict):
    text: StrictStr = Field(min_length=1, max_length=400)
    case_ids: List[StrictStr] = Field(max_length=10)


class _Section(_Strict):
    title: StrictStr = Field(min_length=1, max_length=80)
    items: List[_Item] = Field(max_length=8)


class _Briefing(_Strict):
    headline: StrictStr = Field(min_length=1, max_length=200)
    sections: List[_Section] = Field(max_length=5)


class BriefingAgent:
    def __init__(self, core: FinanceCore, client, model=None):
        self.core = core
        self.client = client
        self.model = model or FinanceCore.agent_model("briefing")

    def run(self):
        """Write today's briefing if one is due. Returns it, or None."""
        if not self.core.briefing_due():
            return None
        facts = self.core.briefing_facts()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(facts, sort_keys=True)}],
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except anthropic.APIError as exc:
            log.warning("briefing call failed: %s", type(exc).__name__)
            # Count the attempt (no tokens billed) so briefing_due stops after 2.
            self.core.record_model_usage("briefing", self.model, None, 0, 0)
            return None
        usage = response.usage
        self.core.record_model_usage(
            "briefing", response.model, None, usage.input_tokens, usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if response.stop_reason != "end_turn":  # refusal, max_tokens: no briefing today
            log.warning("briefing stopped: %s", response.stop_reason)
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            briefing = _Briefing.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            log.warning("briefing output did not match the schema")
            return None
        known = {c["case_id"] for key in ("needs_owner", "most_overdue") for c in facts[key]}
        sections = [{"title": s.title, "items": [
            {"text": i.text, "case_ids": [c for c in i.case_ids if c in known], "evidence_ids": []}
            for i in s.items]} for s in briefing.sections]
        return self.core.store_briefing(facts["period_start"], facts["period_end"],
                                        briefing.headline, sections)
