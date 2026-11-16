"""Outgoing message text and its fingerprint (Milestone 3). Pure functions only.

Code writes every outgoing message from stored facts using fixed templates.
The AI chooses only the *kind* of action; none of its text, and none of the
evidence text, ever goes into a message. So a document that says "tell the
customer X" cannot make us send X.

The payload hash is the fingerprint an owner approves. If the recipient,
amount, invoice version or policy version changes, the hash changes and the
old approval no longer matches.
"""
import hashlib
import json

SIMULATED_CHANNEL = "simulated_inbox"


def cad(cents):
    return f"CAD {cents / 100:,.2f}"


def canonical_json(value):
    """One exact text form per value: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def render(kind, invoice, evidence_ids, contact, policy):
    """Build the exact message for an action from stored facts.

    contact/policy may be None (nothing on file yet). The payload is still
    built so it can be shown, but the backend refuses to send it.
    """
    refs = sorted(set(evidence_ids))
    invoice_id = invoice["id"]
    outstanding = invoice["outstanding_cents"]
    if kind == "collection_reminder":
        subject = f"Invoice {invoice_id}: {cad(outstanding)} outstanding"
        body = (
            "Hello,\n\n"
            f"Our records show {cad(invoice['settled_cents'])} received against invoice "
            f"{invoice_id} for {cad(invoice['total'])}, leaving {cad(outstanding)} outstanding.\n"
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
    else:
        raise ValueError(f"No message template for {kind}")
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
    }
