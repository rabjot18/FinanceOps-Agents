"""Outgoing message text and its fingerprint (Milestones 3 and 6). Pure functions only.

Two ways a message is written:
- a code template from stored facts (routine reminders from the rules), or
- (Milestone 6) text the collections agent wrote. Code checks that text
  (check_agent_text): every amount must be one of the invoice's real amounts,
  no other invoice IDs, no links, e-mail addresses, phone numbers or bank
  details, and no concessions (discounts, credits, waivers, payment plans,
  fees, threats). Code then appends the facts itself: invoice ID, amounts,
  references and the "simulated" label. Evidence text and the agent's
  rationale never go into a message.

The payload hash is the fingerprint an owner approves. If the recipient,
amount, invoice version or policy version changes, the hash changes and the
old approval no longer matches.
"""
import hashlib
import json
import re

SIMULATED_CHANNEL = "simulated_inbox"


def cad(cents):
    return f"CAD {cents / 100:,.2f}"


def canonical_json(value):
    """One exact text form per value: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --- Milestone 6: checks on agent-written text ----------------------------

MAX_SUBJECT = 120
MAX_BODY = 1500
_LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|ca|net|org|io|co|biz|info|app|xyz)\b)",
                   re.IGNORECASE)
_EMAIL_ADDR = re.compile(r"[^@\s]+@[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_BANK = re.compile(r"\b(account\s*(number|no\.?|#)|routing|transit\s*number|iban|swift|"
                   r"bic|sort\s*code|wire\s+(it|the|to|funds)|e-?transfer\s+to|bank\s+details|"
                   r"crypto|bitcoin|gift\s*card)\b", re.IGNORECASE)
# Things only the owner may offer or threaten. The agent has no authority for them.
_CONCESSION = re.compile(
    r"\b(discount\w*|waive\w*|waiver|write[\s-]?off|forgiv\w*|refund\w*|credit\w*|"
    r"rebate\w*|payment\s+plan\w*|instal+ment\w*|settle\s+for|reduc\w*|lower\s+the|"
    r"fee\b|fees\b|penalt\w*|interest|late\s+charge\w*|legal\s+action|lawyer\w*|"
    r"court\b|collections?\s+agency|sue\b|lawsuit)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONEY = re.compile(r"(?:(?:CAD|USD|C?\$)\s?(\d[\d,]*(?:\.\d{1,2})?))|"
                    r"(?:(\d[\d,]*(?:\.\d{1,2})?)\s?(?:CAD|dollars?|\$))", re.IGNORECASE)
_BARE_AMOUNT = re.compile(r"(?<![\w.-])(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})(?![\w.])")
_INVOICE_ID = re.compile(r"\bINV[-_][\w-]+", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
# A reply or document that tries to instruct an AI or the system. Code's own
# check (independent of the model): such cases never get automatic messages.
_INSTRUCTIONS = re.compile(
    r"(ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)|disregard\s+(the\s+|all\s+|your\s+)?"
    r"(previous|prior|above|instructions|rules)|system\s*(prompt|message|note|override)|"
    r"\bas\s+an?\s+(ai|assistant|language model)|\b(ai|assistant|chatbot|bot)\b[^.\n]{0,40}"
    r"\b(must|should|shall|will|now)\b|mark\s+(this|the|invoice)[^.\n]{0,30}\b(paid|"
    r"resolved|closed)|new\s+instructions|developer\s+mode|\bjailbreak|<\s*/?\s*system)",
    re.IGNORECASE)


def looks_like_instructions(text):
    return bool(_INSTRUCTIONS.search(text or ""))


def clean_agent_text(text):
    """Normalize {"subject", "body"} from the agent; raises ValueError if malformed."""
    if not isinstance(text, dict) or set(text) != {"subject", "body"}:
        raise ValueError("expected exactly a subject and a body")
    subject, body = text["subject"], text["body"]
    if type(subject) is not str or type(body) is not str:
        raise ValueError("subject and body must be text")
    subject = " ".join(subject.split())
    body = "\n".join(line.rstrip() for line in body.replace("\r\n", "\n").strip().split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body)
    return {"subject": subject, "body": body}


def _cents(number_text):
    value = number_text.replace(",", "")
    whole, _, frac = value.partition(".")
    return int(whole or 0) * 100 + int((frac + "00")[:2])


def check_agent_text(text, invoice):
    """Raise ValueError (a sentence the agent can act on) unless the text is safe to send."""
    subject, body = text["subject"], text["body"]
    if not 3 <= len(subject) <= MAX_SUBJECT:
        raise ValueError(f"subject must be 3 to {MAX_SUBJECT} characters")
    if not 20 <= len(body) <= MAX_BODY:
        raise ValueError(f"body must be 20 to {MAX_BODY} characters")
    both = f"{subject}\n{body}"
    if _CONTROL.search(both):
        raise ValueError("control characters are not allowed")
    # Dates, amounts and invoice IDs are digits too; take them out before
    # looking for phone numbers.
    digits_checked = _ISO_DATE.sub(" ", _INVOICE_ID.sub(" ", _BARE_AMOUNT.sub(
        " ", _MONEY.sub(" ", both))))
    for pattern, what, where in (
            (_LINK, "links or web addresses", both), (_EMAIL_ADDR, "e-mail addresses", both),
            (_PHONE, "phone numbers", digits_checked),
            (_BANK, "bank or payment-route details", both)):
        if pattern.search(where):
            raise ValueError(f"{what} are not allowed; the system adds what the customer needs")
    concession = _CONCESSION.search(both)
    if concession:
        raise ValueError(f"'{concession.group(0)}' is not allowed: discounts, credits, waivers, "
                         "payment plans, fees and threats need the owner")
    if "%" in both:
        raise ValueError("percentages are not allowed")
    allowed = {invoice["total_cents"], invoice["settled_cents"], invoice["outstanding_cents"]}
    found = [m.group(1) or m.group(2) for m in _MONEY.finditer(both)]
    found += _BARE_AMOUNT.findall(both)
    for number in found:
        if _cents(number) not in allowed:
            raise ValueError(f"amount {number} is not on this invoice; use only the total, "
                             "settled or outstanding amount (or none)")
    for ref in _INVOICE_ID.findall(both):
        if ref.upper() != invoice["id"].upper():
            raise ValueError(f"{ref} is not this case's invoice")
    if looks_like_instructions(both):
        raise ValueError("the text reads like instructions to a system")


def _facts(invoice, refs):
    return (f"Invoice {invoice['id']}: total {cad(invoice['total_cents'])}, received "
            f"{cad(invoice['settled_cents'])}, outstanding {cad(invoice['outstanding_cents'])}.\n"
            f"Our references: {', '.join(refs)}.")


def render(kind, invoice, evidence_ids, contact, policy, agent_text=None):
    """Build the exact message for an action from stored facts.

    contact/policy may be None (nothing on file yet). The payload is still
    built so it can be shown, but the backend refuses to send it.
    agent_text (Milestone 6): {"subject", "body"} already checked by
    check_agent_text; code adds the invoice ID to the subject and the facts.
    """
    refs = sorted(set(evidence_ids))
    invoice_id = invoice["id"]
    outstanding = invoice["outstanding_cents"]
    if kind not in ("collection_reminder", "request_information"):
        raise ValueError(f"No message template for {kind}")
    if agent_text is not None:
        subject = f"Invoice {invoice_id}: {agent_text['subject']}"
        body = (f"{agent_text['body']}\n\n---\n{_facts(invoice, refs)}\n\n"
                "(Simulated message from the FinanceOps demo. No real email was sent.)")
    elif kind == "collection_reminder":
        subject = f"Invoice {invoice_id}: {cad(outstanding)} outstanding"
        body = (
            "Hello,\n\n"
            f"Our records show {cad(invoice['settled_cents'])} received against invoice "
            f"{invoice_id} for {cad(invoice['total_cents'])}, leaving {cad(outstanding)} outstanding.\n"
            f"Our references: {', '.join(refs)}.\n\n"
            "Please arrange payment of the balance, or reply if you believe this is incorrect.\n\n"
            "(Simulated message from the FinanceOps demo. No real email was sent.)"
        )
    elif kind == "request_information":
        subject = f"Invoice {invoice_id}: information needed"
        body = (
            "Hello,\n\n"
            f"Before we take any further step on invoice {invoice_id} "
            f"({cad(outstanding)} unresolved), we need to confirm the facts. "
            "Could you send any records you have about the item in question?\n"
            f"Our references: {', '.join(refs)}.\n\n"
            "We are not requesting further payment until this is resolved.\n\n"
            "(Simulated message from the FinanceOps demo. No real email was sent.)"
        )
    return {
        "simulated": True,
        "channel": contact["channel"] if contact else None,
        "to": contact["address"] if contact else None,
        "subject": subject,
        "body": body,
        "kind": kind,
        "invoice_id": invoice_id,
        "invoice_version": invoice["version"],
        "amount_cents": outstanding,
        "currency": "CAD",
        "evidence_ids": refs,
        "policy_version": policy["version"] if policy else None,
        "written_by": "agent" if agent_text is not None else "code",
    }
