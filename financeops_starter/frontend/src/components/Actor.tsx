import { ACTORS, type TeamMember } from '../flowMeta';

/** A coloured badge for whoever took a step (an agent, code, the customer, you). */
export function ActorAvatar({ actor, size = 'md' }: { actor: TeamMember; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`actor-avatar actor-${actor} size-${size}`} aria-hidden="true" title={ACTORS[actor].name}>
      {ACTORS[actor].glyph}
    </span>
  );
}
