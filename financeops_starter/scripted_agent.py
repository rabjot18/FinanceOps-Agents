"""A free, deterministic stand-in for Claude (Milestone 6): no network, no cost.

It speaks the same Messages API shapes as the real SDK (like the fakes in
test_agent.py and eval_run.py) and plays both agents with simple rules:

- Collections agent: reads the invoice, evidence and policy; then, from the
  latest customer reply, writes a reminder, asks for remittance details,
  waits for a promised date (schedule_follow_up), or escalates a dispute or
  injected instructions to the owner with a recommendation.
- Disputes agent: reads the case and leaves a recommendation for the owner.

Used by tests, by `eval_run.py --reactive`, and by `worker.py --scripted-agents`
so the whole demo runs without an API key. Labelled clearly: its usage is
recorded as zero tokens, so it never counts toward the spend caps.
"""
import json
import re

from messages import looks_like_instructions

MODEL = "scripted-agent"


def _tool_use(tool_id, name, args=None):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args or {}}


def _results(history):
    """Tool results of the last user turn, by tool_use_id."""
    out = {}
    for block in history[-1]["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error"):
            try:
                out[block["tool_use_id"]] = json.loads(block["content"])
            except (TypeError, ValueError):
                pass
    return out


def _cad(cents):
    return f"CAD {cents / 100:,.2f}"


def _reply_number(evidence_id):
    match = re.search(r"reply-(\d+)$", evidence_id)
    return int(match.group(1)) if match else 0


class ScriptedAgentClient:
    def __init__(self):
        self.beta = self
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):
        from anthropic.types.beta import BetaMessage
        self.calls += 1
        history = kwargs["messages"]
        disputes = kwargs.get("system", "").startswith("You are the Disputes agent")
        turns = sum(1 for m in history if m["role"] == "assistant")
        if turns == 0:
            content = [_tool_use("t_inv", "get_invoice"), _tool_use("t_ev", "get_evidence"),
                       _tool_use("t_pol", "get_policy")]
            stop = "tool_use"
        elif turns == 1:
            results = _results(history)
            content = self._decide(results, disputes)
            stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        else:
            content, stop = [{"type": "text", "text": "Done."}], "end_turn"
        return BetaMessage.model_validate({
            "id": f"msg_scripted_{self.calls}", "type": "message", "role": "assistant",
            "model": MODEL, "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}})

    def _decide(self, results, disputes):
        invoice = results.get("t_inv")
        evidence = results.get("t_ev")
        policy = results.get("t_pol") or {}
        if not invoice or not evidence:
            return [{"type": "text", "text": "Could not read the case."}]
        docs = evidence["documents"]
        ids = [d["evidence_id"] for d in docs][:20]
        if not ids:
            return [{"type": "text", "text": "No evidence on file; nothing to cite."}]
        replies = sorted((d for d in docs if _reply_number(d["evidence_id"])),
                         key=lambda d: _reply_number(d["evidence_id"]))
        latest = replies[-1]["untrusted_content"].lower() if replies else ""
        latest_id = replies[-1]["evidence_id"] if replies else ids[0]
        outstanding = invoice["outstanding_cents"]

        if disputes:
            return [_tool_use("t_rec", "recommend_to_owner", {
                "recommendation": "The customer says two visits were not done to the agreed "
                                  "standard. The contract on file covers the service but no "
                                  "visit log was found. I recommend checking the crew's visit "
                                  "records with the customer before any further reminder.",
                "evidence_ids": ids})]

        def escalate(reason, question, recommendation):
            return [_tool_use("t_esc", "escalate_to_owner", {
                "reason": reason, "question": question, "recommendation": recommendation,
                "evidence_ids": [latest_id]})]

        def propose(kind, subject, body, summary, follow_days):
            return [_tool_use("t_prop", "propose_action", {
                "kind": kind, "evidence_ids": ids, "summary": summary,
                "findings": [{"statement": f"{_cad(outstanding)} is still outstanding.",
                              "evidence_ids": [ids[0]]}],
                "unknowns": [], "message": {"subject": subject, "body": body}}),
                _tool_use("t_fu", "schedule_follow_up",
                          {"days": follow_days, "reason": "check for payment"})]

        def wait(days, why):
            return [_tool_use("t_fu", "schedule_follow_up", {"days": days, "reason": why})]

        if invoice.get("disputed"):
            return [{"type": "text", "text": "Disputed: the Disputes agent and the owner handle it."}]
        if latest and looks_like_instructions(latest):
            return escalate("instructions_in_document",
                            "The customer's reply contains instructions aimed at the system "
                            "(to mark the invoice paid). Nothing on record shows a payment.",
                            "Do not mark it paid. Contact the customer directly to confirm "
                            "whether a payment was made.")
        if any(w in latest for w in ("won't be paying", "dispute", "not completed")):
            return escalate("dispute", "The customer disputes the work on this invoice.",
                            "Review the visit records before any further reminder.")
        if outstanding == 0:
            return [{"type": "text", "text": "Paid in full; nothing to do."}]
        sent = policy.get("reminders_already_sent", 0)
        if sent >= policy.get("max_reminders", 99):
            return escalate("reminder_limit",
                            "The reminder limit is reached and the invoice is still unpaid.",
                            "Call the customer before anything else is sent.")
        if "bounced" in latest or "on its way" in latest or "paying" in latest \
                or "going out today" in latest:
            return wait(3, "customer said payment is on its way")
        if "rest by" in latest or "cash is tight" in latest:
            return wait(6, "customer promised the rest by a date")
        if "paid" in latest:
            return propose(
                "request_information", "Payment details",
                "Hello,\n\nThank you for your reply. We have not yet received a payment "
                "against this invoice. Could you send the payment date and the reference or "
                "remittance details, so we can find it on our side?\n\nKind regards,\n"
                "Accounts receivable",
                "The customer says it is paid but no payment is on record: ask for details.", 4)
        firm = "a second reminder" if sent else "a friendly reminder"
        return propose(
            "collection_reminder", "Payment reminder" if not sent else "Second reminder",
            f"Hello,\n\nThis is {firm} that {_cad(outstanding)} is now past due. "
            "Please arrange payment at your earliest convenience, or reply if anything "
            "is unclear.\n\nKind regards,\nAccounts receivable",
            f"Overdue with nothing disputing it: {firm}.", 6)
