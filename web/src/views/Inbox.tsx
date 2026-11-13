import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type EventRow, type RunStatus, type TriageLabel } from "../api";
import { filedAt, itemNames } from "../format";
import { Failed, Loading, RunPill, TriagePill } from "../ui";

type Filter = "all" | "material" | "monitor" | "routine";

const CATEGORY: Record<TriageLabel, Filter> = {
  MATERIAL_NEGATIVE: "material",
  MATERIAL_POSITIVE: "material",
  MONITOR: "monitor",
  ROUTINE: "routine",
  NOT_SUPPLY_CHAIN: "routine",
};

export const useEvents = () =>
  useQuery({
    queryKey: ["events"],
    queryFn: () => api.get<EventRow[]>("/events"),
    // Runs move on in the background: keep polling while one is queued.
    refetchInterval: (q) => (q.state.data?.some((e) => e.run?.status === "queued") ? 3000 : false),
  });

export function useStart(toast: (message: string) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (e: { hubCik: string; accession: string }) =>
      api.post<{ id: string; status: RunStatus }>("/analyses", e),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["events"] });
      toast(run.status === "queued" ? "Analysis queued." : "This filing already has a run.");
      location.hash = `#analysis/${run.id}`;
    },
  });
}

interface Props {
  live: boolean;
  toast: (message: string) => void;
}

export function Inbox({ live, toast }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const events = useEvents();
  const start = useStart(toast);
  const rows = (events.data ?? []).filter(
    (e) => filter === "all" || (e.run?.triage && CATEGORY[e.run.triage.label] === filter),
  );
  const open = (e: EventRow) => {
    if (e.run) location.hash = `#analysis/${e.run.id}`;
  };

  return (
    <section className="view" aria-labelledby="h-inbox">
      <div className="page-head">
        <div>
          <h1 id="h-inbox">Inbox</h1>
          <p className="sub">New 8-K filings from the customers you watch, and what triage decided.</p>
        </div>
        <div className="seg" role="group" aria-label="Filter by triage result">
          {(["all", "material", "monitor", "routine"] as const).map((f) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {events.isPending ? (
        <Loading />
      ) : events.isError ? (
        <Failed error={events.error} retry={() => void events.refetch()} />
      ) : (
        <div className="panel table-wrap">
          <table>
            <thead>
              <tr>
                <th>Filed (ET)</th>
                <th>Company</th>
                <th>8-K items</th>
                <th>Triage</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    No filings match this filter. Choose All to see every filing.
                  </td>
                </tr>
              )}
              {rows.map((e) => (
                <tr
                  key={e.accession}
                  className={e.run ? "row-link" : ""}
                  tabIndex={e.run ? 0 : undefined}
                  onClick={() => open(e)}
                  onKeyDown={(k) => {
                    if (k.key === "Enter" || k.key === " ") {
                      k.preventDefault();
                      open(e);
                    }
                  }}
                >
                  <td className="num small nowrap">{filedAt(e.acceptedAt)}</td>
                  <td>
                    <div className="company">{e.hubName ?? e.hubCik}</div>
                    <div className="muted small num">
                      {e.run?.triage?.reason ?? `Accession ${e.accession}`}
                    </div>
                  </td>
                  <td>
                    <div className="items">
                      {e.items.map((i) => (
                        <span key={i} className="item-code num" title={itemNames[i] ?? ""}>
                          {i}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>{e.run?.triage ? <TriagePill triage={e.run.triage} /> : <span className="muted small">Not triaged</span>}</td>
                  <td>
                    {e.run ? (
                      <>
                        <RunPill status={e.run.status} />{" "}
                        <a href={`#analysis/${e.run.id}`} className="small nowrap" style={{ marginLeft: 6 }}>
                          Open run
                        </a>
                      </>
                    ) : live ? (
                      <button
                        className="btn"
                        type="button"
                        disabled={start.isPending}
                        onClick={(c) => {
                          c.stopPropagation();
                          start.mutate({ hubCik: e.hubCik, accession: e.accession });
                        }}
                      >
                        Analyze
                      </button>
                    ) : (
                      <span className="muted small">Not analyzed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
