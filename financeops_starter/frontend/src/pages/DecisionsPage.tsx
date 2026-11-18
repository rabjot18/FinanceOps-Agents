// "Your decisions": only what the agents could not decide alone. Escalations
// lead with the agent's recommendation; approvals show the exact message.
// The approve/reject/queue and resolve logic is the shared one (409-safe).
import { useAttention } from '../api/queries';
import { ApprovalQueue } from '../components/ApprovalQueue';
import { EscalationList } from '../components/Escalations';
import { ErrorBox, Skeleton } from '../components/common';

export function DecisionsPage() {
  const attention = useAttention();
  const data = attention.data;
  const total = data ? data.approvals.length + data.escalations.length : 0;

  return (
    <div className="page-stack calm">
      <div className="page-head">
        <h2>Your decisions</h2>
        <p className="muted">
          Only what the agents could not decide alone. Everything else runs under your policy.
        </p>
      </div>

      {attention.isPending && <Skeleton label="Loading your decisions" rows={4} />}
      {attention.error && <ErrorBox error={attention.error} onRetry={() => void attention.refetch()} />}

      {data && total === 0 && (
        <div className="calm-empty">
          <span className="calm-empty-mark" aria-hidden="true">
            ✓
          </span>
          <strong>Nothing needs you. The agents are handling it.</strong>
          <p className="muted">When an agent needs a decision only you can make, it lands here with its recommendation.</p>
        </div>
      )}

      {data && data.escalations.length > 0 && (
        <section className="decision-section" aria-labelledby="dec-questions">
          <h3 id="dec-questions">
            Questions from the agents <span className="count">{data.escalations.length}</span>
          </h3>
          <EscalationList items={data.escalations} showCase />
        </section>
      )}

      {data && data.approvals.length > 0 && (
        <section className="decision-section" aria-labelledby="dec-approvals">
          <h3 id="dec-approvals">
            Messages waiting for your approval <span className="count">{data.approvals.length}</span>
          </h3>
          <ApprovalQueue approvals={data.approvals} expanded />
        </section>
      )}
    </div>
  );
}
