// Display names and colours for the flow board. Presentation only: which
// stage a case is in, and who moved it, always comes from the backend.
import type { FlowActor, FlowStage } from './api/client';

/** Everyone who can move a case, plus the read-only reporter (legend only). */
export type TeamMember = FlowActor | 'reporter';

export interface ActorInfo {
  name: string;
  /** Short role, e.g. "Claude" or "code". */
  kind: string;
  /** What they do, one line. */
  does: string;
  glyph: string;
}

export const ACTORS: Record<TeamMember, ActorInfo> = {
  intake: { name: 'Intake', kind: 'code', does: 'Opens a case for every new invoice', glyph: 'In' },
  rules: { name: 'Rules', kind: 'code', does: 'Watches due dates and your policy', glyph: 'Ru' },
  collections: { name: 'Collections agent', kind: 'Claude', does: 'Writes and sends reminders', glyph: 'Co' },
  disputes: { name: 'Disputes agent', kind: 'Claude', does: 'Reads disputes, recommends a way out', glyph: 'Di' },
  customer: { name: 'Customer', kind: 'simulated · Haiku', does: 'Synthetic customers who reply and pay', glyph: 'Cu' },
  verifier: { name: 'Verifier', kind: 'code', does: 'Checks payments against the invoice', glyph: 'Ve' },
  owner: { name: 'Owner', kind: 'you', does: 'Only the decisions the agents cannot make', glyph: 'You' },
  reporter: { name: 'Reporter', kind: 'Haiku', does: 'Writes the plain-language summary', glyph: 'Re' },
  stripe: { name: 'Stripe', kind: 'test mode', does: 'Payment links and settlements', glyph: 'St' },
};

export const TEAM_ORDER: TeamMember[] = [
  'intake',
  'rules',
  'collections',
  'disputes',
  'customer',
  'verifier',
  'owner',
  'reporter',
  'stripe',
];

/** How an actor is named on an edge ("who moves cases here"). */
export function actorLabel(actor: FlowActor): string {
  if (actor === 'customer') return 'Customer (simulated)';
  if (actor === 'owner') return 'You';
  if (actor === 'stripe') return 'Stripe (test)';
  return ACTORS[actor].name;
}

/** Fallback stage names when the backend list is not at hand (outcomes table). */
export const STAGE_LABEL: Record<FlowStage, string> = {
  issued: 'Issued',
  monitoring: 'Monitoring',
  overdue: 'Overdue',
  contacted: 'Contacted',
  customer_replied: 'Customer replied',
  payment_received: 'Payment received',
  closed_verified: 'Closed · verified',
  needs_you: 'Needs you',
  disputed: 'Disputed',
};

export type StageTone = 'neutral' | 'accent' | 'money' | 'warn' | 'danger';

export const STAGE_TONE: Record<FlowStage, StageTone> = {
  issued: 'neutral',
  monitoring: 'neutral',
  overdue: 'neutral',
  contacted: 'accent',
  customer_replied: 'accent',
  payment_received: 'money',
  closed_verified: 'money',
  needs_you: 'warn',
  disputed: 'danger',
};

/** Side stages sit below the main row, under the stage cases usually leave from. */
export const SIDE_STAGES: Partial<Record<FlowStage, { from: FlowStage; actor: FlowActor }>> = {
  needs_you: { from: 'contacted', actor: 'collections' },
  disputed: { from: 'customer_replied', actor: 'disputes' },
};

/** Who usually moves a case into each main stage, when no recent step says otherwise. */
export const DEFAULT_MOVER: Partial<Record<FlowStage, FlowActor>> = {
  monitoring: 'intake',
  overdue: 'rules',
  contacted: 'collections',
  customer_replied: 'customer',
  payment_received: 'customer',
  closed_verified: 'verifier',
};
