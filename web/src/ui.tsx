import type { RunStatus, Triage } from "./api";

const RUN: Record<RunStatus, [string, string]> = {
  queued: ["p-info", "Queued"],
  paused: ["p-warn", "Waiting for review"],
  published: ["p-ok", "Published"],
  rejected: ["p-bad", "Rejected"],
  ended: ["p-neu", "Ended at triage"],
};

export function RunPill({ status }: { status: RunStatus }) {
  const [tone, text] = RUN[status];
  return <span className={`pill ${tone}`}>{text}</span>;
}

export function TriagePill({ triage }: { triage: Triage }) {
  switch (triage.label) {
    case "MATERIAL_NEGATIVE":
      return <span className="pill p-info">Material, negative</span>;
    case "MATERIAL_POSITIVE":
      return <span className="pill p-info">Material, positive</span>;
    case "MONITOR":
      return <span className="pill p-warn">Monitor</span>;
    case "NOT_SUPPLY_CHAIN":
      return <span className="pill p-neu">Not supply chain</span>;
    case "ROUTINE":
      return <span className="pill p-neu">Routine</span>;
  }
}

export function Loading() {
  return <p className="empty">Loading…</p>;
}

export function Failed({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="notice" role="alert">
      <p className="error-text">{error.message}</p>
      <div className="notice-actions">
        <button className="btn" type="button" onClick={retry}>
          Retry
        </button>
      </div>
    </div>
  );
}
