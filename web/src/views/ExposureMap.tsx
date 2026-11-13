import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import { api, type ExposureMap as Graph, type MapSupplier } from "../api";
import { day, filingUrl, pct } from "../format";
import { Failed, Loading } from "../ui";
import { useEvents } from "./Inbox";

const HUB = { x: 360, y: 280 };
const RINGS = { 1: 150, 2: 245 };
const short = (name: string) => (name.length > 22 ? `${name.slice(0, 21)}…` : name);
const key = (s: MapSupplier) => `${s.depth}/${s.supplierCik}/${s.viaCik}`;

interface Placed extends MapSupplier {
  id: string;
  x: number;
  y: number;
  r: number;
}

// Direct suppliers evenly around the hub; second hops fanned out behind the
// supplier they sell to, so each sits next to its link.
function layout(suppliers: MapSupplier[]): Placed[] {
  const direct = suppliers.filter((s) => s.depth === 1);
  const angle = new Map<string, number>();
  const placed: Placed[] = direct.map((s, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(direct.length, 1);
    angle.set(s.supplierCik, a);
    const at = { x: HUB.x + RINGS[1] * Math.cos(a), y: HUB.y + RINGS[1] * Math.sin(a) };
    return { ...s, id: key(s), ...at, r: 9 + Math.sqrt(s.edge.pct) * 1.6 };
  });
  const byVia = new Map<string, MapSupplier[]>();
  for (const s of suppliers.filter((s) => s.depth === 2)) {
    byVia.set(s.viaCik, [...(byVia.get(s.viaCik) ?? []), s]);
  }
  for (const [via, group] of byVia) {
    const base = angle.get(via) ?? 0;
    group.forEach((s, k) => {
      const a = base + (k - (group.length - 1) / 2) * 0.22;
      placed.push({ ...s, id: key(s), x: HUB.x + RINGS[2] * Math.cos(a), y: HUB.y + RINGS[2] * Math.sin(a), r: 11 });
    });
  }
  return placed;
}

function Drawer({ graph, node, nodes }: { graph: Graph; node: Placed | undefined; nodes: Placed[] }) {
  if (!node) {
    const direct = graph.suppliers.filter((s) => s.depth === 1).length;
    return (
      <>
        <h2>{graph.hub.name}</h2>
        <p className="sub">The customer at the center of the map.</p>
        <dl className="kv">
          <dt>Direct suppliers</dt>
          <dd>{direct}</dd>
          <dt>Second hop</dt>
          <dd>{graph.suppliers.length - direct}</dd>
          <dt>As of</dt>
          <dd>{day(graph.asOf)}</dd>
        </dl>
      </>
    );
  }
  const via = nodes.find((n) => n.depth === 1 && n.supplierCik === node.viaCik);
  const to = node.depth === 1 ? graph.hub.name : (via?.name ?? node.viaCik);
  const e = node.edge;
  return (
    <>
      <h2>
        {node.name} sells to {to}
      </h2>
      <div className="big-share num">{pct(e.pct)}</div>
      <p className="muted small" style={{ margin: 0 }}>
        of {node.name}’s revenue comes from {to}
      </p>
      <dl className="kv">
        <dt>Source</dt>
        <dd>
          <a href={filingUrl(node.supplierCik, e.sourceAccession)}>Filing for the period ending {day(e.periodEnd)}</a>
        </dd>
        <dt>Accession</dt>
        <dd className="num">{e.sourceAccession}</dd>
        <dt>XBRL fact</dt>
        <dd>us-gaap:ConcentrationRiskPercentage1 on srt:MajorCustomersAxis, member {e.memberLabel}</dd>
        <dt>Name match</dt>
        <dd className="num">
          {e.extractionMethod.replace(/_/g, " ")}, {e.confidence.toFixed(2)} confidence
        </dd>
        <dt>Reviewed</dt>
        <dd>{e.reviewedAt ? `Approved ${day(e.reviewedAt)}` : "Approved at load, above the 0.85 bar"}</dd>
        <dt>Valid</dt>
        <dd>
          From {day(e.validFrom)} {e.validTo ? `until ${day(e.validTo)}` : "until a later filing replaces it"}
        </dd>
        {node.depth > 1 && via && (
          <>
            <dt>Indirect weight</dt>
            <dd className="num">
              {node.weight.toFixed(2)}% ({pct(via.edge.pct)} × {pct(e.pct)}), a heuristic
            </dd>
          </>
        )}
      </dl>
    </>
  );
}

function Hubs() {
  const events = useEvents();
  const hubs = new Map((events.data ?? []).map((e) => [e.hubCik, e.hubName ?? e.hubCik]));
  return (
    <section className="view">
      <h1>Exposure map</h1>
      <p className="sub">Pick a customer to see who sells to it.</p>
      {events.isPending ? (
        <Loading />
      ) : (
        <ul>
          {[...hubs].sort((a, b) => a[1].localeCompare(b[1])).map(([cik, name]) => (
            <li key={cik}>
              <a href={`#map/${cik}`}>{name}</a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ExposureMap({ cik, asOf }: { cik: string | null; asOf: string | null }) {
  return cik ? <MapView cik={cik} asOf={asOf} /> : <Hubs />;
}

function MapView({ cik, asOf }: { cik: string; asOf: string | null }) {
  const [depth, setDepth] = useState<1 | 2>(2);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["exposure", cik, asOf],
    queryFn: () => api.get<Graph>(`/companies/${cik}/exposure${asOf ? `?as_of=${asOf}` : ""}`),
  });
  if (query.isPending) return <Loading />;
  if (query.isError) return <Failed error={query.error} retry={() => void query.refetch()} />;
  const graph = query.data;
  const all = layout(graph.suppliers);
  const nodes = all.filter((n) => n.depth <= depth);
  const at = (cikOf: string) => all.find((n) => n.depth === 1 && n.supplierCik === cikOf) ?? HUB;
  const pick = (id: string | null) => (k: KeyboardEvent) => {
    if (k.key === "Enter" || k.key === " ") {
      k.preventDefault();
      setSelected(id);
    }
  };
  const hubLabel = graph.hub.tickers[0] ?? graph.hub.name.slice(0, 4).toUpperCase();

  return (
    <section className="view" aria-labelledby="h-map">
      <div className="page-head">
        <div>
          <h1 id="h-map">Exposure map: {graph.hub.name}</h1>
          <p className="sub">Who sells to {graph.hub.name}, and who sells to them. Only reviewed relationships appear.</p>
        </div>
      </div>
      <div className="map-layout">
        <div className="panel map-panel">
          <div className="map-tools">
            <div className="seg" role="group" aria-label="Depth">
              {([1, 2] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={depth === d}
                  onClick={() => {
                    setDepth(d);
                    if (d === 1 && selected?.startsWith("2/")) setSelected(null);
                  }}
                >
                  {d === 1 ? "1 hop" : "2 hops"}
                </button>
              ))}
            </div>
            <span className="asof">As of {day(graph.asOf)}</span>
          </div>
          {graph.suppliers.length === 0 ? (
            <p className="empty">No reviewed suppliers on this date.</p>
          ) : (
            <svg className="map" viewBox="0 0 720 560" role="group" aria-label="Supplier exposure graph">
              <circle className="ring" cx={HUB.x} cy={HUB.y} r={RINGS[1]} />
              <text className="ring-label" x={HUB.x} y={HUB.y - RINGS[1] - 6} textAnchor="middle">
                Direct suppliers
              </text>
              {depth === 2 && (
                <>
                  <circle className="ring" cx={HUB.x} cy={HUB.y} r={RINGS[2]} />
                  <text className="ring-label" x={HUB.x} y={HUB.y - RINGS[2] - 6} textAnchor="middle">
                    Suppliers of suppliers
                  </text>
                </>
              )}
              {nodes.map((n) => {
                const to = n.depth === 1 ? HUB : at(n.viaCik);
                return (
                  <line
                    key={`e-${n.id}`}
                    className={n.depth === 1 ? "edge" : "edge hop2"}
                    x1={n.x}
                    y1={n.y}
                    x2={to.x}
                    y2={to.y}
                    strokeWidth={(n.edge.pct / (n.depth === 1 ? 3 : 3.4)).toFixed(1)}
                  />
                );
              })}
              <g className="node hub" role="button" tabIndex={0} aria-label={`${graph.hub.name}, the customer`} onClick={() => setSelected(null)} onKeyDown={pick(null)}>
                <circle cx={HUB.x} cy={HUB.y} r={36} />
                <text x={HUB.x} y={HUB.y + 5} textAnchor="middle">
                  {hubLabel}
                </text>
              </g>
              <text x={HUB.x} y={HUB.y + 56} textAnchor="middle" fontSize={12.5} fontWeight={600} fill="var(--ink)">
                {short(graph.hub.name)}
              </text>
              {nodes.map((n) => {
                const above = n.y < HUB.y;
                const sub = n.depth === 1 ? `${pct(n.edge.pct)} of revenue` : `${n.weight.toFixed(2)}% indirect`;
                return (
                  <g
                    key={n.id}
                    className={`node hop${n.depth}${selected === n.id ? " selected" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`${n.name}, ${sub}`}
                    onClick={() => setSelected(n.id)}
                    onKeyDown={pick(n.id)}
                  >
                    <circle className="body" cx={n.x} cy={n.y} r={n.r} />
                    <circle className="fillrate" cx={n.x} cy={n.y} r={n.r - 4} />
                    <text x={n.x} y={above ? n.y - n.r - 22 : n.y + n.r + 17} textAnchor="middle">
                      {short(n.name)}
                    </text>
                    <text className="tick" x={n.x} y={above ? n.y - n.r - 8 : n.y + n.r + 32} textAnchor="middle">
                      {sub}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
          <div className="legend">
            <span>
              <svg width="34" height="10" aria-hidden="true">
                <line x1="2" y1="5" x2="32" y2="5" stroke="var(--edge)" strokeWidth="6" strokeLinecap="round" opacity=".75" />
              </svg>
              Thicker line, larger share of revenue
            </span>
            <span>
              <svg width="34" height="10" aria-hidden="true">
                <line x1="2" y1="5" x2="32" y2="5" stroke="var(--edge-2)" strokeWidth="3" strokeDasharray="2 6" strokeLinecap="round" />
              </svg>
              Second hop
            </span>
          </div>
        </div>
        <aside className="panel panel-pad drawer" aria-live="polite">
          <Drawer graph={graph} node={nodes.find((n) => n.id === selected)} nodes={all} />
        </aside>
      </div>
    </section>
  );
}
