import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type Analysis, type PendingEdge, type User } from "../api";
import { day, filedAt, pct } from "../format";
import { Failed, Loading } from "../ui";

export const canReview = (user: User) => user.role !== "analyst";

export const usePaused = () =>
  useQuery({
    queryKey: ["analyses", "paused"],
    queryFn: () => api.get<Analysis[]>("/analyses?status=paused"),
  });

export const usePendingEdges = (user: User) =>
  useQuery({
    queryKey: ["edges"],
    queryFn: () => api.get<PendingEdge[]>("/edges/review"),
    enabled: canReview(user), // analysts get a 403
  });

interface Props {
  user: User;
  live: boolean;
  toast: (message: string) => void;
}

function BriefCard({ run, allowed, live, toast }: { run: Analysis; allowed: boolean; live: boolean; toast: Props["toast"] }) {
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const confirm = useRef<HTMLDialogElement>(null);
  const decide = useMutation({
    mutationFn: (decision: "approved" | "rejected") => api.post(`/reviews/${run.id}`, { decision, note }),
    onSuccess: (_, decision) => {
      void client.invalidateQueries({ queryKey: ["analyses"] });
      void client.invalidateQueries({ queryKey: ["events"] });
      toast(
        decision === "approved"
          ? "Brief approved. The run resumes from its checkpoint and publishes."
          : "Brief rejected. The run ends without publishing.",
      );
    },
  });
  const result = run.result ?? {};
  const reasons = result.reasons ?? result.gate?.reasons ?? [];
  const disabled = !allowed || decide.isPending;
  return (
    <div className="panel review-card">
      <h3>
        {run.hubName ?? run.input.hubCik} 8-K{run.event ? `, filed ${filedAt(run.event.acceptedAt)} ET` : ""}
      </h3>
      <p className="body-text">Why it needs review: {reasons.join("; ") || "the risk gate asked for a reviewer."}</p>
      <p className="muted small" style={{ margin: 0 }}>
        {result.usage?.llmCalls ?? 0} model calls so far, {result.evidence?.length ?? 0} passages,{" "}
        {result.exposures?.length ?? 0} suppliers. <a href={`#analysis/${run.id}`}>Read the analysis</a>
      </p>
      {live ? (
        <>
          <label className="small" htmlFor={`note-${run.id}`} style={{ display: "block", marginTop: 12, fontWeight: 600 }}>
            Comment (saved with the run)
          </label>
          <textarea id={`note-${run.id}`} placeholder="Optional" value={note} onChange={(e) => setNote(e.target.value)} disabled={!allowed} maxLength={2000} />
          <div className="review-actions">
            <button className="btn btn-primary" type="button" disabled={disabled} onClick={() => decide.mutate("approved")}>
              Approve and publish
            </button>
            <button className="btn btn-danger" type="button" disabled={disabled} onClick={() => confirm.current?.showModal()}>
              Reject
            </button>
          </div>
        </>
      ) : (
        <p className="muted small">Live runs are off, so briefs can’t be approved right now.</p>
      )}
      <dialog ref={confirm} aria-labelledby={`rej-${run.id}`}>
        <div className="dlg">
          <h2 id={`rej-${run.id}`}>Reject this brief?</h2>
          <p className="body-text">The run ends and nothing is published. Your comment is saved with the run.</p>
          <div className="dlg-actions">
            <button className="btn" type="button" onClick={() => confirm.current?.close()}>
              Keep reviewing
            </button>
            <button
              className="btn btn-danger"
              type="button"
              onClick={() => {
                confirm.current?.close();
                decide.mutate("rejected");
              }}
            >
              Reject brief
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}

function EdgeCard({ edge, toast }: { edge: PendingEdge; toast: Props["toast"] }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "approved" | "rejected") => api.post(`/edges/${edge.id}/decision`, { decision }),
    onSuccess: (_, decision) => {
      void client.invalidateQueries({ queryKey: ["edges"] });
      void client.invalidateQueries({ queryKey: ["exposure"] });
      toast(
        decision === "approved"
          ? "Relationship approved. It joins the graph for new runs."
          : "Relationship rejected. It stays out of the graph.",
      );
    },
  });
  return (
    <div className="panel review-card">
      <h3>
        {edge.supplier} sells to {edge.hub}
      </h3>
      <p className="body-text">
        <span className="num">{pct(edge.pct)}</span> of {edge.supplier}’s revenue. Member “{edge.memberLabel}”, period
        ending {day(edge.periodEnd)}. Name match {edge.confidence.toFixed(2)} ({edge.extractionMethod.replace(/_/g, " ")}).{" "}
        <a href={edge.filingUrl}>Open the filing</a>
      </p>
      {(!edge.totalShare || edge.relationshipFlag) && (
        <div className="flags">
          {!edge.totalShare && <span className="pill p-warn">Share of part of revenue</span>}
          {edge.relationshipFlag && <span className="pill p-warn">{edge.relationshipFlag.replace(/_/g, " ")}</span>}
        </div>
      )}
      <div className="review-actions">
        <button className="btn btn-primary" type="button" disabled={decide.isPending} onClick={() => decide.mutate("approved")}>
          Approve
        </button>
        <button className="btn" type="button" disabled={decide.isPending} onClick={() => decide.mutate("rejected")}>
          Reject
        </button>
      </div>
    </div>
  );
}

export function Review({ user, live, toast }: Props) {
  const allowed = canReview(user);
  const paused = usePaused();
  const edges = usePendingEdges(user);
  return (
    <section className="view" aria-labelledby="h-review">
      <div className="page-head">
        <div>
          <h1 id="h-review">Review</h1>
          <p className="sub">
            Briefs that need a second look before they publish, and new supplier relationships from the latest filings.
          </p>
        </div>
      </div>
      {!allowed && <p className="role-note">You’re signed in as an analyst. A reviewer approves or rejects.</p>}
      <div className="review-grid">
        <div>
          <h2>
            Briefs waiting <span className="count num">{paused.data?.length ?? ""}</span>
          </h2>
          {paused.isPending ? (
            <Loading />
          ) : paused.isError ? (
            <Failed error={paused.error} retry={() => void paused.refetch()} />
          ) : paused.data.length === 0 ? (
            <p className="empty">No briefs are waiting. New ones appear here when the risk gate asks for a reviewer.</p>
          ) : (
            paused.data.map((run) => <BriefCard key={run.id} run={run} allowed={allowed} live={live} toast={toast} />)
          )}
        </div>
        <div>
          <h2>
            New relationships <span className="count num">{edges.data?.length ?? ""}</span>
          </h2>
          {!allowed ? (
            <p className="empty">Reviewers see new relationships here.</p>
          ) : edges.isPending ? (
            <Loading />
          ) : edges.isError ? (
            <Failed error={edges.error} retry={() => void edges.refetch()} />
          ) : edges.data.length === 0 ? (
            <p className="empty">No relationships are waiting.</p>
          ) : (
            edges.data.map((edge) => <EdgeCard key={edge.id} edge={edge} toast={toast} />)
          )}
        </div>
      </div>
    </section>
  );
}
