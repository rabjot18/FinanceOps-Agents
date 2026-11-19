// The Flow board: every case as a chip in the stage the backend says it is
// in, and on each edge the actor who moves cases there. Polled; edges are
// coloured by the stage they lead into, and an edge whose target stage got new
// steps lights up (glow, flowing dots, "+N") and fades out over HOT_MS. Nothing here
// decides a stage, an amount or who acted: it all comes from GET /flow.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  applyNodeChanges,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCaseFlow, useFlow, useWorkspace } from '../api/queries';
import {
  isNotBuilt,
  type Action,
  type FlowActor,
  type FlowCase,
  type FlowOut,
  type FlowStage,
  type FlowStageOut,
  type FlowStep,
  type Workspace,
} from '../api/client';
import { formatCents, formatTime } from '../format';
import {
  ACTORS,
  DEFAULT_MOVER,
  SIDE_STAGES,
  STAGE_LABEL,
  STAGE_TONE,
  TEAM_ORDER,
  actorLabel,
  type StageTone,
} from '../flowMeta';
import { ActorAvatar } from '../components/Actor';
import { useRunMonth } from '../components/RunMonth';
import { Ago, Avatar, ErrorBox, NotBuiltNotice, PlainText, SimulatedTag, Skeleton } from '../components/common';

// --- layout ------------------------------------------------------------------

const NODE_W = 176;
const GAP = 108; // room for a visible arrow plus its actor label
const SIDE_Y = 360; // below the tallest main-row node (header + CHIP_MAX chips + "more")
const CHIP_MAX = 4;
const HANDLE_TOP = 34; // edges leave and enter at the header row, so the main row stays straight

type Labels = Partial<Record<FlowStage, string>>;

function labelOf(labels: Labels, stage: FlowStage): string {
  return labels[stage] ?? STAGE_LABEL[stage];
}

/** What changed since the previous poll: drives the edge animation and chip glow. */
interface Fresh {
  stages: ReadonlySet<FlowStage>;
  cases: ReadonlySet<string>;
  actors: ReadonlySet<FlowActor>;
}
const NOTHING_FRESH: Fresh = { stages: new Set(), cases: new Set(), actors: new Set() };

function useFresh(flow: FlowOut | undefined, updatedAt: number): Fresh {
  const lastSeq = useRef<number | null>(null);
  const [fresh, setFresh] = useState<Fresh>(NOTHING_FRESH);
  useEffect(() => {
    if (!flow) return;
    const prev = lastSeq.current;
    const top = flow.recent.reduce((m, s) => Math.max(m, s.seq), 0);
    if (prev !== null) {
      const newer = flow.recent.filter((s) => s.seq > prev);
      setFresh(
        newer.length === 0
          ? NOTHING_FRESH
          : {
              stages: new Set(newer.map((s) => s.stage)),
              cases: new Set(newer.map((s) => s.case_id)),
              actors: new Set(newer.map((s) => s.actor)),
            },
      );
    }
    lastSeq.current = Math.max(prev ?? 0, top);
  }, [flow, updatedAt]);
  return fresh;
}

/**
 * How "hot" each stage's incoming edge is: steps that arrived in the last HOT_MS
 * keep the edge lit (fading out), so a move stays visible for several seconds
 * instead of one poll. Display only: it counts steps the backend returned.
 */
const HOT_MS = 9000;
interface Heat {
  count: number; // steps into the stage within the window
  level: number; // 1 = just now, fading to 0
}
type HeatMap = ReadonlyMap<FlowStage, Heat>;

function useHeat(flow: FlowOut | undefined, updatedAt: number): HeatMap {
  const lastSeq = useRef<number | null>(null);
  const [events, setEvents] = useState<{ stage: FlowStage; at: number }[]>([]);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!flow) return;
    const prev = lastSeq.current;
    const top = flow.recent.reduce((m, s) => Math.max(m, s.seq), 0);
    if (prev !== null) {
      const newer = flow.recent.filter((s) => s.seq > prev);
      if (newer.length) {
        const at = Date.now();
        setNow(at);
        setEvents((old) => [...old.filter((e) => at - e.at < HOT_MS), ...newer.map((s) => ({ stage: s.stage, at }))]);
      }
    }
    lastSeq.current = Math.max(prev ?? 0, top);
  }, [flow, updatedAt]);
  const active = events.length > 0;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      const t = Date.now();
      setNow(t);
      setEvents((old) => {
        const kept = old.filter((e) => t - e.at < HOT_MS);
        return kept.length === old.length ? old : kept;
      });
    }, 500);
    return () => clearInterval(timer);
  }, [active]);
  return useMemo(() => {
    const map = new Map<FlowStage, Heat>();
    for (const e of events) {
      const level = Math.max(0, 1 - (now - e.at) / HOT_MS);
      if (level <= 0) continue;
      const h = map.get(e.stage);
      map.set(e.stage, { count: (h?.count ?? 0) + 1, level: Math.max(h?.level ?? 0, level) });
    }
    return map;
  }, [events, now]);
}

/** A concrete colour per tone, for SVG arrowheads (marker defs do not inherit --tone). */
const TONE_COLOR: Record<StageTone, string> = {
  neutral: 'var(--muted)',
  accent: 'var(--accent)',
  money: 'var(--money)',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
};

/** The actor who most often took the recent steps into `stage` (ties: the latest), else the usual one. */
function moverInto(stage: FlowStage, recent: FlowStep[], fallback: FlowActor | undefined): FlowActor | undefined {
  const tally = new Map<FlowActor, { n: number; last: number }>();
  for (const step of recent) {
    if (step.stage !== stage) continue;
    const t = tally.get(step.actor) ?? { n: 0, last: 0 };
    tally.set(step.actor, { n: t.n + 1, last: Math.max(t.last, step.seq) });
  }
  let best: FlowActor | undefined;
  let bestT = { n: 0, last: 0 };
  for (const [actor, t] of tally) {
    if (t.n > bestT.n || (t.n === bestT.n && t.last > bestT.last)) {
      best = actor;
      bestT = t;
    }
  }
  return best ?? fallback;
}

interface StageData extends Record<string, unknown> {
  stage: FlowStageOut;
  tone: StageTone;
  cases: FlowCase[];
  fresh: boolean;
  freshCases: ReadonlySet<string>;
  onOpen: (caseId: string) => void;
}
type StageNodeT = Node<StageData, 'stage'>;

interface ActorEdgeData extends Record<string, unknown> {
  actor?: FlowActor;
  tone: StageTone;
  heat: number; // 0..1, see useHeat
  moves: number; // recent steps along this edge
  used: boolean; // the target stage holds cases or saw recent steps
  side: boolean; // a vertical edge down to needs_you / disputed
}
type ActorEdgeT = Edge<ActorEdgeData, 'actor'>;

function buildGraph(flow: FlowOut, fresh: Fresh, heat: HeatMap, onOpen: (caseId: string) => void) {
  const byStage = new Map<FlowStage, FlowCase[]>();
  for (const c of flow.cases) byStage.set(c.stage, [...(byStage.get(c.stage) ?? []), c]);
  // Most recently moved first (display order only).
  for (const list of byStage.values()) list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  const main = flow.stages.filter((s) => !SIDE_STAGES[s.id]);
  const side = flow.stages.filter((s) => SIDE_STAGES[s.id]);
  const mainIndex = new Map(main.map((s, i) => [s.id, i]));

  const node = (stage: FlowStageOut, x: number, y: number): StageNodeT => ({
    id: stage.id,
    type: 'stage',
    position: { x, y },
    draggable: false,
    selectable: false,
    connectable: false,
    data: {
      stage,
      tone: STAGE_TONE[stage.id],
      cases: byStage.get(stage.id) ?? [],
      fresh: fresh.stages.has(stage.id) || (heat.get(stage.id)?.level ?? 0) > 0.35,
      freshCases: fresh.cases,
      onOpen,
    },
  });

  const nodes: StageNodeT[] = main.map((s, i) => node(s, i * (NODE_W + GAP), 0));
  let extra = main.length;
  for (const s of side) {
    const from = SIDE_STAGES[s.id]?.from;
    const i = from !== undefined ? mainIndex.get(from) : undefined;
    nodes.push(node(s, (i ?? extra++) * (NODE_W + GAP), SIDE_Y));
  }

  const edge = (source: FlowStage, target: FlowStage, sideEdge: boolean, fallback?: FlowActor): ActorEdgeT => {
    const h = heat.get(target);
    const live = fresh.stages.has(target) || (h?.level ?? 0) > 0;
    const tone = STAGE_TONE[target];
    const used = (byStage.get(target)?.length ?? 0) > 0 || flow.recent.some((s) => s.stage === target);
    return {
      id: `${source}->${target}`,
      source,
      target,
      sourceHandle: sideEdge ? 'bottom' : 'right',
      targetHandle: sideEdge ? 'top' : 'left',
      type: 'actor',
      animated: live,
      focusable: false,
      selectable: false,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: live ? 20 : 16,
        height: live ? 20 : 16,
        color: live || used ? TONE_COLOR[tone] : 'var(--border-strong)',
      },
      data: {
        actor: moverInto(target, flow.recent, fallback),
        tone,
        heat: live ? Math.max(h?.level ?? 0, 0.6) : 0,
        moves: h?.count ?? 0,
        used,
        side: sideEdge,
      },
    };
  };

  const edges: ActorEdgeT[] = [];
  for (let i = 1; i < main.length; i++) {
    const a = main[i - 1] as FlowStageOut;
    const b = main[i] as FlowStageOut;
    edges.push(edge(a.id, b.id, false, DEFAULT_MOVER[b.id]));
  }
  for (const s of side) {
    const meta = SIDE_STAGES[s.id];
    if (meta && mainIndex.has(meta.from)) edges.push(edge(meta.from, s.id, true, meta.actor));
  }
  return { nodes, edges };
}

// --- custom node and edge ---------------------------------------------------------

function StageNode({ data }: NodeProps<StageNodeT>) {
  const { stage, tone, cases, fresh, freshCases, onOpen } = data;
  const shown = cases.slice(0, CHIP_MAX);
  const more = cases.length - shown.length;
  return (
    <section
      className={`stage-node tone-${tone}${fresh ? ' fresh' : ''}`}
      // React Flow turns pointer events off on non-selectable nodes; the chips need them.
      style={{ width: NODE_W, pointerEvents: 'all' }}
      aria-label={`${stage.label}: ${stage.count} ${stage.count === 1 ? 'case' : 'cases'}`}
    >
      <Handle type="target" id="left" position={Position.Left} style={{ top: HANDLE_TOP }} isConnectable={false} />
      <Handle type="source" id="right" position={Position.Right} style={{ top: HANDLE_TOP }} isConnectable={false} />
      <Handle type="target" id="top" position={Position.Top} isConnectable={false} />
      <Handle type="source" id="bottom" position={Position.Bottom} isConnectable={false} />
      <header className="stage-head">
        <span className="stage-label">{stage.label}</span>
        <span className="stage-count" data-testid={`count-${stage.id}`}>
          {stage.count}
        </span>
      </header>
      {cases.length === 0 ? (
        <p className="stage-empty">No cases</p>
      ) : (
        <ul className="stage-chips">
          {shown.map((c) => (
            <li key={c.case_id}>
              <button
                type="button"
                className={`case-chip nodrag nopan${freshCases.has(c.case_id) ? ' fresh' : ''}${c.needs_owner ? ' needs-owner' : ''}`}
                title={`${c.customer} · ${c.invoice_id}`}
                onClick={() => onOpen(c.case_id)}
              >
                <span className="chip-customer">{c.customer}</span>
                <span className="chip-amount">{formatCents(c.outstanding_cents, c.currency)}</span>
              </button>
            </li>
          ))}
          {more > 0 && <li className="chip-more">+{more} more</li>}
        </ul>
      )}
    </section>
  );
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false);
}

function ActorEdge(props: EdgeProps<ActorEdgeT>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, animated, markerEnd } = props;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 18,
  });
  const actor = data?.actor;
  const tone = data?.tone ?? 'neutral';
  const heat = animated ? (data?.heat ?? 1) : 0;
  const moves = data?.moves ?? 0;
  const state = animated ? 'live' : data?.used ? 'used' : 'idle';
  const motion = animated && !prefersReducedMotion();
  // --heat (0..1) fades the glow out over the hot window.
  const vars = { '--heat': heat.toFixed(2) } as CSSProperties;
  return (
    <>
      <g className={`edge-group tone-${tone} ${state}`} style={vars}>
        {animated && <path d={path} className="edge-glow" fill="none" />}
        <BaseEdge id={id} path={path} markerEnd={markerEnd} className={`actor-edge ${state}`} />
        {motion && <path d={path} className="edge-comet" fill="none" />}
        {motion &&
          [0, 0.55, 1.1].map((begin) => (
            <circle key={begin} r={begin === 0 ? 6 : 4} className="edge-dot">
              <animateMotion dur="1.65s" begin={`${begin}s`} repeatCount="indefinite" path={path} />
            </circle>
          ))}
      </g>
      {actor && (
        <EdgeLabelRenderer>
          <div
            className={`edge-actor tone-${tone} ${state}`}
            // Beside vertical edges and above horizontal ones, so the arrow itself stays visible.
            style={{
              ...vars,
              transform: data?.side
                ? `translate(14px, -50%) translate(${labelX}px, ${labelY}px)`
                : `translate(-50%, -100%) translate(${labelX}px, ${labelY - 12}px)`,
            }}
          >
            <ActorAvatar actor={actor} size="sm" />
            <span className="edge-actor-name">{actorLabel(actor)}</span>
            {animated && moves > 0 && <span className="edge-moves">+{moves}</span>}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { stage: StageNode };
const edgeTypes = { actor: ActorEdge };

function FlowCanvas({ flow, fresh, heat, onOpen }: { flow: FlowOut; fresh: Fresh; heat: HeatMap; onOpen: (caseId: string) => void }) {
  const graph = useMemo(() => buildGraph(flow, fresh, heat, onOpen), [flow, fresh, heat, onOpen]);
  // React Flow measures each node and reports it as a change; the node only
  // shows (and its edges only draw) once that measurement is back in our state.
  const [nodes, setNodes] = useState<StageNodeT[]>(graph.nodes);
  useEffect(() => {
    setNodes((prev) => {
      const measured = new Map(prev.map((n) => [n.id, n.measured]));
      return graph.nodes.map((n) => ({ ...n, measured: measured.get(n.id) }));
    });
  }, [graph.nodes]);
  const onNodesChange = useCallback(
    (changes: NodeChange<StageNodeT>[]) => setNodes((ns) => applyNodeChanges(changes, ns)),
    [],
  );
  const edges = graph.edges;
  return (
    <div className="flow-canvas" aria-label="Case flow board" role="region">
      <ReactFlow<StageNodeT, ActorEdgeT>
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
      >
        <Background gap={24} size={1.3} />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
    </div>
  );
}

// --- page ----------------------------------------------------------------------

export function FlowPage() {
  const flow = useFlow();
  const fresh = useFresh(flow.data, flow.dataUpdatedAt);
  const heat = useHeat(flow.data, flow.dataUpdatedAt);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const onOpen = useCallback((caseId: string) => setOpenCase(caseId), []);
  const close = useCallback(() => setOpenCase(null), []);
  const data = flow.data;
  const labels = useMemo<Labels>(
    () => Object.fromEntries((data?.stages ?? []).map((s) => [s.id, s.label])),
    [data?.stages],
  );

  return (
    <div className="page-stack flow-page">
      <div className="page-head flow-head">
        <div>
          <h2>Flow</h2>
          <p className="muted">
            Every case, stage by stage. The agents move them along; you step in only where it says Needs you.
          </p>
        </div>
        {data && (
          <div className="flow-meta">
            <span className="live">
              <span className="live-dot" aria-hidden="true" /> Live
            </span>
            <span className="muted small">
              {data.cases.length} {data.cases.length === 1 ? 'case' : 'cases'} · {formatTime(data.now)}
            </span>
            {data.simulated_time && <SimulatedTag>Simulated clock</SimulatedTag>}
          </div>
        )}
      </div>

      {flow.isPending && <Skeleton label="Loading the flow" rows={4} />}
      {flow.error &&
        (isNotBuilt(flow.error) ? (
          <NotBuiltNotice what="The flow board" error={flow.error} />
        ) : (
          <ErrorBox error={flow.error} onRetry={() => void flow.refetch()} />
        ))}

      {data && data.cases.length === 0 && <EmptyFlow />}

      {data && data.cases.length > 0 && (
        <>
          <FlowCanvas flow={data} fresh={fresh} heat={heat} onOpen={onOpen} />
          <div className="flow-below">
            <LatestSteps flow={data} labels={labels} fresh={fresh} onOpen={onOpen} />
            <AgentTeam active={fresh.actors} />
          </div>
        </>
      )}

      {openCase && <CaseDrawer caseId={openCase} labels={labels} now={data?.now} onClose={close} />}
    </div>
  );
}

function EmptyFlow() {
  const runMonth = useRunMonth();
  return (
    <div className="hero-empty">
      <div className="hero-empty-art" aria-hidden="true">
        {(['intake', 'rules', 'collections', 'customer', 'verifier'] as const).map((a) => (
          <ActorAvatar key={a} actor={a} size="lg" />
        ))}
      </div>
      <strong>No cases yet. Let the agents run a month.</strong>
      <p className="muted">
        Synthetic customers get invoices, the clock plays, and you watch each case move from stage to stage: who
        reminded whom, who replied, who paid. You only step in when an agent asks.
      </p>
      <button type="button" className="run-month big" onClick={runMonth}>
        <span aria-hidden="true">▶</span> Run the month
      </button>
    </div>
  );
}

// --- latest steps and the team -------------------------------------------------

function LatestSteps({
  flow,
  labels,
  fresh,
  onOpen,
}: {
  flow: FlowOut;
  labels: Labels;
  fresh: Fresh;
  onOpen: (caseId: string) => void;
}) {
  const steps = useMemo(() => [...flow.recent].sort((a, b) => b.seq - a.seq).slice(0, 12), [flow.recent]);
  const customers = useMemo(() => new Map(flow.cases.map((c) => [c.case_id, c.customer])), [flow.cases]);
  return (
    <section className="panel" aria-labelledby="latest-title">
      <div className="panel-head">
        <h3 id="latest-title">Latest steps</h3>
      </div>
      {steps.length === 0 ? (
        <p className="muted">Nothing has moved yet.</p>
      ) : (
        <ol className="latest-steps" aria-label="Latest steps">
          {steps.map((s) => (
            <li key={s.seq} className={fresh.cases.has(s.case_id) && fresh.stages.has(s.stage) ? 'fresh' : undefined}>
              <button type="button" className="step-row" onClick={() => onOpen(s.case_id)}>
                <ActorAvatar actor={s.actor} />
                <span className="step-text">
                  <span className="step-top">
                    <strong>{ACTORS[s.actor].name}</strong>
                    <span className={`stage-pill tone-${STAGE_TONE[s.stage]}`}>{labelOf(labels, s.stage)}</span>
                    {customers.get(s.case_id) && <span className="muted small">{customers.get(s.case_id)}</span>}
                  </span>
                  <span className="step-summary">{s.summary}</span>
                </span>
                <span className="muted small step-time">
                  <Ago iso={s.created_at} now={flow.now} />
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function AgentTeam({ active }: { active: ReadonlySet<FlowActor> }) {
  return (
    <section className="panel" aria-labelledby="team-title">
      <div className="panel-head">
        <h3 id="team-title">Agent team</h3>
      </div>
      <ul className="team" aria-label="Agent team">
        {TEAM_ORDER.map((m) => (
          <li key={m} className={m !== 'reporter' && active.has(m) ? 'active' : undefined}>
            <ActorAvatar actor={m} />
            <span className="team-text">
              <span className="team-name">
                {ACTORS[m].name} <span className={`team-kind kind-${m}`}>{ACTORS[m].kind}</span>
              </span>
              <span className="muted small">{ACTORS[m].does}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// --- case drawer -----------------------------------------------------------------

function writerName(action: Action): string {
  return action.proposed_by.includes('disputes') ? 'Disputes agent' : 'Collections agent';
}

function StepAttachment({ step, ws }: { step: FlowStep; ws: Workspace | undefined }) {
  if (!step.ref || !ws) return null;
  const action = ws.actions.find((a) => a.id === step.ref);
  if (action?.payload) {
    const p = action.payload;
    return (
      <div className="step-message" aria-label="Message sent">
        <div className="step-message-bar">
          <span className="small muted">Exact message · simulated delivery</span>
          {p.written_by === 'agent' && <span className="badge-agent">Written by {writerName(action)}</span>}
        </div>
        <p className="step-subject">{p.subject}</p>
        <PlainText text={p.body} />
      </div>
    );
  }
  const evidence = ws.evidence.find((e) => e.id === step.ref);
  if (evidence) {
    return (
      <div className="step-reply">
        <span className="tag-untrusted">{step.actor === 'customer' ? 'Customer reply · untrusted' : 'Evidence · untrusted'}</span>
        <PlainText text={evidence.body} />
      </div>
    );
  }
  return null;
}

function CaseDrawer({
  caseId,
  labels,
  now,
  onClose,
}: {
  caseId: string;
  labels: Labels;
  now: string | undefined;
  onClose: () => void;
}) {
  const path = useCaseFlow(caseId);
  const ws = useWorkspace(caseId);
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => closeRef.current?.focus(), [caseId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const data = path.data;
  const invoice = ws.data?.invoice;

  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="drawer-head">
          <Avatar name={data?.customer ?? caseId} large />
          <div className="drawer-title">
            <h2 id={titleId}>{data?.customer ?? caseId}</h2>
            <p className="muted small">
              {data?.invoice_id ?? ''}
              {invoice && (
                <>
                  {' '}
                  · {formatCents(invoice.outstanding_cents, invoice.currency)} outstanding of{' '}
                  {formatCents(invoice.total_cents, invoice.currency)}
                </>
              )}
            </p>
          </div>
          <button ref={closeRef} type="button" className="icon" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {data && (
          <p className="drawer-stage">
            Now: <span className={`stage-pill tone-${STAGE_TONE[data.stage]}`}>{labelOf(labels, data.stage)}</span>
          </p>
        )}

        {path.isPending && <Skeleton label="Loading the case path" rows={5} />}
        {path.error && <ErrorBox error={path.error} onRetry={() => void path.refetch()} />}
        {ws.error && <p className="muted small">The messages for this case could not be loaded: {ws.error.detail}</p>}

        {data && (
          <ol className="path-steps" aria-label="Case path">
            {data.steps.map((s) => (
              <li key={s.seq} className={`tone-${STAGE_TONE[s.stage]}`}>
                <span className="path-dot" aria-hidden="true" />
                <div className="path-body">
                  <div className="path-top">
                    <strong>{labelOf(labels, s.stage)}</strong>
                    <span className="path-actor">
                      <ActorAvatar actor={s.actor} size="sm" /> {ACTORS[s.actor].name}
                    </span>
                    <span className="muted small path-time">
                      {now ? <Ago iso={s.created_at} now={now} /> : formatTime(s.created_at)}
                    </span>
                  </div>
                  <p className="path-summary">{s.summary}</p>
                  <StepAttachment step={s} ws={ws.data} />
                </div>
              </li>
            ))}
          </ol>
        )}

        <footer className="drawer-foot">
          <Link to={`/cases/${encodeURIComponent(caseId)}`} className="drawer-link">
            Open the full case →
          </Link>
        </footer>
      </aside>
    </div>
  );
}
