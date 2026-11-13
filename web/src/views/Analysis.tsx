import { useQuery } from "@tanstack/react-query";
import { api, type Analysis as Run, type RunResult, type StudyRow } from "../api";
import { day, filedAt, items, pct, signed } from "../format";
import { Failed, Loading, RunPill, TriagePill } from "../ui";

export const useAnalysis = (id: string) =>
  useQuery({
    queryKey: ["analyses", id],
    queryFn: () => api.get<Run>(`/analyses/${id}`),
    // The worker finishes runs in the background; paused ones wait on a reviewer,
    // often for hours, so check those less often.
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "queued" ? 3000 : status === "paused" ? 30_000 : false;
    },
  });

function jump(n: number) {
  const li = document.getElementById(`ev-${n}`);
  if (!li) return;
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  li.scrollIntoView?.({ behavior: still ? "auto" : "smooth", block: "center" });
  li.classList.remove("flash");
  void li.offsetWidth; // restart the highlight
  li.classList.add("flash");
}

function Cite({ n }: { n: number }) {
  return (
    <button type="button" className="cite" aria-label={`Source ${n}`} onClick={() => jump(n)}>
      {n}
    </button>
  );
}

function Brief({ run, source }: { run: Run; source: Map<number, number> }) {
  const brief = run.result?.brief;
  if (brief) {
    return (
      <div className="panel panel-pad">
        <h2>Brief</h2>
        {brief.claims.map((claim, i) => (
          <p key={i} className={i === 0 ? "lead" : "body-text"}>
            {claim.text}
            {claim.citations.flatMap((c) => (source.has(c) ? [<Cite key={c} n={source.get(c) ?? 0} />] : []))}
          </p>
        ))}
        {brief.gaps.length > 0 && <p className="body-text">Not covered: {brief.gaps.join(" ")}</p>}
        <p className="footnote">
          Shares are what each supplier’s own filing discloses about this customer. They measure
          exposure, not expected loss. Not investment advice.
        </p>
      </div>
    );
  }
  const { errors, plan, triage } = run.result ?? {};
  let ended = "The run ended without a brief.";
  if (errors?.length) ended = `The run stopped early: ${errors.join("; ")}`;
  else if (plan && plan.suppliers.length === 0) {
    ended = `No reviewed suppliers were linked to ${run.hubName ?? "this customer"} on ${day(plan.asOf)}, so there was nothing to analyze.`;
  } else if (triage) ended = `Triage ended the run: ${triage.reason}`;
  const text: Record<Run["status"], string> = {
    queued: "Queued. The run starts within a minute.",
    paused: "The brief is written after a reviewer approves.",
    rejected: "A reviewer rejected this run, so nothing was published.",
    ended,
    published: "",
  };
  return (
    <div className="panel panel-pad">
      <h2>Draft brief</h2>
      <p className="body-text">{text[run.status]}</p>
    </div>
  );
}

function Chart({ rows, name }: { rows: StudyRow[]; name: (cik: string) => string }) {
  const done = rows.filter((r) => r.car01 !== null).sort((a, b) => (a.car01 ?? 0) - (b.car01 ?? 0));
  if (done.length === 0) return <p className="body-text">No returns yet. They fill in after each close.</p>;
  const values = done.map((r) => (r.car01 ?? 0) * 100);
  const maxAbs = Math.max(2, Math.ceil(Math.max(...values.map(Math.abs)) / 2) * 2);
  const left = 200, zeroX = 410, rowH = 38, top = 20;
  const scale = (zeroX - left - 10) / maxAbs;
  const bottom = top + done.length * rowH;
  const ticks = [];
  for (let v = -maxAbs; v <= maxAbs; v += 2) ticks.push(v);
  return (
    <svg viewBox={`0 0 640 ${bottom + 24}`} role="img" aria-label="Two-day abnormal returns by supplier" style={{ width: "100%", height: "auto", display: "block" }}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={zeroX + v * scale} y1={8} x2={zeroX + v * scale} y2={bottom} stroke={v === 0 ? "var(--ink-3)" : "var(--rule-2)"} strokeWidth={1} />
          <text x={zeroX + v * scale} y={bottom + 16} textAnchor="middle" fontSize={12} fill="var(--ink-3)">
            {signed(v, 0)}%
          </text>
        </g>
      ))}
      {done.map((r, i) => {
        const v = values[i];
        const y = top + i * rowH, w = Math.abs(v) * scale, x = v < 0 ? zeroX - w : zeroX;
        return (
          <g key={r.cik} data-testid="car-bar">
            <text x={0} y={y + 19} fontSize={13} fontWeight={600} fill="var(--ink)">
              {name(r.cik).slice(0, 26)}
            </text>
            <rect x={x} y={y + 6} width={w} height={20} rx={4} fill={v < 0 ? "var(--neg)" : "var(--pos)"} opacity={0.85} />
            <text x={v < 0 ? x - 6 : x + w + 6} y={y + 21} textAnchor={v < 0 ? "end" : "start"} fontSize={12.5} fontWeight={600} fill="var(--ink-2)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {signed(v)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

type Step = { title: string; text: string; st: "done" | "warn" | "wait" | "todo" };

function steps(run: Run, result: RunResult): (Step | Step[])[] {
  const out: (Step | Step[])[] = [];
  const { triage, plan, exposures, evidence, market, verification, gate, review, brief } = result;
  if (triage) {
    const p = triage.probability === null ? "" : ` (${triage.probability.toFixed(2)})`;
    out.push({ title: "Triage", text: `${triage.label.replace(/_/g, " ").toLowerCase()}${p}: ${triage.reason}`, st: "done" });
  }
  if (plan) out.push({ title: "Planner", text: `The graph as of ${day(plan.asOf)} has ${plan.suppliers.length} suppliers to study.`, st: "done" });
  if (exposures || evidence || market) {
    const complete = (market ?? []).filter((m) => m.status === "complete").length;
    out.push([
      { title: "Exposure analyst", text: `Pulled ${exposures?.length ?? 0} relationships.`, st: "done" },
      { title: "Evidence researcher", text: `Found ${evidence?.length ?? 0} passages.`, st: "done" },
      { title: "Event study", text: `${complete} of ${market?.length ?? 0} returns computed.`, st: "done" },
    ]);
  }
  if (verification) {
    out.push(
      verification.ok
        ? { title: "Verifier", text: "Every claim traces to a cited passage.", st: "done" }
        : { title: "Verifier", text: `${verification.failedSuppliers.length} suppliers failed the checks.`, st: "warn" },
    );
  }
  if (gate) {
    out.push(
      gate.route === "HUMAN_REVIEW"
        ? { title: "Risk gate", text: `Needs a reviewer: ${gate.reasons.join("; ")}`, st: "warn" }
        : { title: "Risk gate", text: "Cleared to publish without review.", st: "done" },
    );
  }
  if (run.status === "paused") {
    out.push({ title: "Waiting for review", text: "Paused. The state is saved in a checkpoint, so the run resumes after a restart.", st: "wait" });
  }
  if (review) {
    const note = review.note ? ` “${review.note}”` : "";
    out.push({ title: `Reviewer ${review.decision}`, text: `${review.reviewer}${note}`, st: "done" });
  }
  if (brief) out.push({ title: "Brief synthesizer", text: `Wrote the brief with ${brief.claims.length} claims. Published.`, st: "done" });
  else if (run.status === "paused") out.push({ title: "Brief synthesizer", text: "Runs after a reviewer approves.", st: "todo" });
  return out;
}

const LABEL = { done: "Done", warn: "Needs attention", wait: "Paused", todo: "Not started" };

function Timeline({ run, result }: { run: Run; result: RunResult }) {
  return (
    <ol className="timeline">
      {steps(run, result).map((step, i) => {
        const st = Array.isArray(step) ? "done" : step.st;
        const mark = st === "done" ? "✓" : st === "wait" ? "II" : String(i + 1);
        return (
          <li key={i} className={`st-${st}`}>
            <span className="step-dot" aria-label={LABEL[st]}>
              {mark}
            </span>
            {Array.isArray(step) ? (
              <>
                <div className="step-title">Three branches</div>
                <div className="parallel">
                  <div className="parallel-label">Ran in parallel</div>
                  {step.map((p) => (
                    <div key={p.title}>
                      <div className="step-title">{p.title}</div>
                      <div className="step-text">{p.text}</div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="step-title">{step.title}</div>
                <div className="step-text">{step.text}</div>
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function Analysis({ id }: { id: string | null }) {
  if (!id) {
    return (
      <section className="view">
        <h1>Analysis</h1>
        <p className="empty">Open a run from the inbox to see its analysis.</p>
      </section>
    );
  }
  return <Detail id={id} />;
}

function Detail({ id }: { id: string }) {
  const query = useAnalysis(id);
  if (query.isPending) return <Loading />;
  if (query.isError) return <Failed error={query.error} retry={() => void query.refetch()} />;
  const run = query.data;
  const result = run.result ?? {};
  const names = new Map((result.exposures ?? []).map((e) => [e.supplierCik, e.name]));
  const name = (cik: string) => names.get(cik) ?? cik;
  const evidence = result.evidence ?? [];
  const source = new Map(evidence.map((e, i) => [e.chunkId, i + 1]));
  const firstSource = (cik: string) => evidence.findIndex((e) => e.supplierCik === cik) + 1;
  const market = new Map((result.market ?? []).map((m) => [m.cik, m]));
  const via = new Map((result.plan?.suppliers ?? []).map((s) => [`${s.cik}/${s.depth}`, s.viaCik]));
  const exposures = [...(result.exposures ?? [])].sort((a, b) => a.depth - b.depth || b.weight - a.weight);
  const hub = run.hubName ?? run.input.hubCik;
  const asOf = result.plan?.asOf;

  return (
    <section className="view" aria-labelledby="h-analysis">
      <div className="event-head">
        <div>
          <h1 id="h-analysis">{result.brief?.headline ?? `${hub} 8-K`}</h1>
          <p className="event-meta">
            {run.event
              ? `8-K items ${items(run.event.items)}, filed ${filedAt(run.event.acceptedAt)} ET. `
              : `Accession ${run.input.accession}. `}
            Event analysis, 2 hops{asOf ? `, as of ${day(asOf)}` : ""}.{" "}
            {run.event && <a href={run.event.url}>Open the 8-K</a>}
            {run.event && " · "}
            <a href={`#map/${run.input.hubCik}${asOf ? `?as_of=${asOf}` : ""}`}>Exposure map</a>
          </p>
        </div>
        <RunPill status={run.status} />
      </div>

      <div className="analysis">
        <div>
          <Brief run={run} source={source} />

          <div className="section">
            <h2>Suppliers exposed</h2>
            {exposures.length === 0 ? (
              <p className="empty">No reviewed supplier relationships for this customer yet.</p>
            ) : (
              <div className="panel table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Supplier</th>
                      <th>Hop</th>
                      <th className="r">Share of revenue</th>
                      <th className="r">Abnormal return, 2 days</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exposures.map((e) => {
                      const study = market.get(e.supplierCik);
                      const n = firstSource(e.supplierCik);
                      const car = study?.car01 == null ? null : study.car01 * 100;
                      return (
                        <tr key={`${e.supplierCik}/${e.depth}`} className={e.depth > 1 ? "indirect" : ""}>
                          <td>
                            <div className="company">{e.name}</div>
                            {e.depth > 1 && <div className="small">via {name(via.get(`${e.supplierCik}/${e.depth}`) ?? "")}</div>}
                            {n > 0 && (
                              <div className="small">
                                <button className="cite" type="button" style={{ verticalAlign: "baseline", fontSize: ".875rem", padding: 0 }} onClick={() => jump(n)}>
                                  Source {n}
                                </button>
                              </div>
                            )}
                          </td>
                          <td className="num">{e.depth}</td>
                          <td className="r num">
                            {e.depth > 1 ? `${e.weight.toFixed(2)}%` : pct(e.weight)}
                            {e.depth > 1 && <div className="small">indirect, a heuristic</div>}
                          </td>
                          <td className={`r num ${car !== null && car < 0 ? "neg" : ""}`}>
                            {car === null ? (study?.status === "pending" ? "Pending" : "—") : `${signed(car)}%`}
                            {study?.t01 != null && <div className="muted small">t {signed(study.t01, 2)}</div>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="section">
            <h2>Market reaction</h2>
            <div className="panel panel-pad">
              <Chart rows={(result.market ?? []).filter((m) => m.depth > 0)} name={name} />
              <p className="footnote">
                Abnormal returns over trading days 0 and +1 against a market model fitted on days −250
                to −31 versus SPY. Day 0 is the first session after the 8-K. Descriptive statistics,
                not a trading signal.
              </p>
            </div>
          </div>

          <div className="section">
            <h2>Evidence</h2>
            <div className="panel panel-pad">
              {evidence.length === 0 ? (
                <p className="empty">No passages yet.</p>
              ) : (
                <ol className="evidence">
                  {evidence.map((e, i) => (
                    <li key={e.chunkId} id={`ev-${i + 1}`}>
                      <span className="n num">{i + 1}</span>
                      <div>
                        <div>
                          <strong>{name(e.supplierCik)}</strong>
                        </div>
                        <div className="src">
                          <a href={e.url}>Open the filing</a>
                        </div>
                        <blockquote>“{e.quote}”</blockquote>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        </div>

        <aside className="side" aria-label="Run">
          <div className="panel panel-pad">
            <h2>How this run went</h2>
            {run.status === "queued" ? <p className="empty">Not started yet.</p> : <Timeline run={run} result={result} />}
          </div>
          {result.usage && (
            <div className="panel panel-pad">
              <h2>Budget used</h2>
              <div className="budget num">
                <span>Model calls</span>
                <span>{result.usage.llmCalls}</span>
                <span>Tool calls</span>
                <span>{result.usage.toolCalls}</span>
                <span>Tokens</span>
                <span>{result.usage.tokens.toLocaleString("en-US")}</span>
              </div>
            </div>
          )}
          <div className="panel panel-pad">
            <details className="manifest">
              <summary>Run details</summary>
              <dl className="kv">
                <dt>Run</dt>
                <dd className="num">{run.id}</dd>
                <dt>Accession</dt>
                <dd className="num">{run.input.accession}</dd>
                {result.triage && (
                  <>
                    <dt>Triage</dt>
                    <dd>
                      <TriagePill triage={result.triage} /> by {result.triage.method}
                    </dd>
                  </>
                )}
                <dt>Started</dt>
                <dd>{filedAt(run.createdAt)} ET</dd>
                {run.completedAt && (
                  <>
                    <dt>Finished</dt>
                    <dd>{filedAt(run.completedAt)} ET</dd>
                  </>
                )}
                <dt>Tools</dt>
                <dd>Each agent can only call its own tools. No tool can change the supplier graph.</dd>
              </dl>
            </details>
          </div>
        </aside>
      </div>
    </section>
  );
}
