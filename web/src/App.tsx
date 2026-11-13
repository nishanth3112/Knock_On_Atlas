import { MutationCache, QueryCache, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { type RefObject, useEffect, useRef, useState } from "react";
import { ApiError, api, setToken, type Health, type User } from "./api";
import { Login } from "./Login";
import { Analysis } from "./views/Analysis";
import { ExposureMap } from "./views/ExposureMap";
import { Inbox, useEvents, useStart } from "./views/Inbox";
import { Review, usePaused, usePendingEdges } from "./views/Review";

type Route =
  | { view: "inbox" }
  | { view: "analysis"; id: string | null }
  | { view: "map"; cik: string | null; asOf: string | null }
  | { view: "review" };

// #analysis/<id>, #map/<cik>?as_of=<date>, #review; anything else is the inbox.
export function parse(hash: string): Route {
  const [path, query] = hash.replace(/^#/, "").split("?");
  const [view, arg] = path.split("/");
  if (view === "analysis") return { view, id: arg || null };
  if (view === "map") return { view, cik: arg || null, asOf: new URLSearchParams(query).get("as_of") };
  if (view === "review") return { view };
  return { view: "inbox" };
}

function useRoute(): Route {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const changed = () => {
      setHash(location.hash);
      window.scrollTo?.(0, 0);
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  return parse(hash);
}

const sentence = (message: string) => message[0].toUpperCase() + message.slice(1) + (message.endsWith(".") ? "" : ".");

function Brand() {
  return (
    <a className="brand" href="#inbox" aria-label="Knock-On Atlas, go to inbox">
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <circle cx="14" cy="14" r="4.5" fill="var(--primary)" />
        <circle cx="14" cy="14" r="8.5" fill="none" stroke="var(--primary)" strokeWidth="1.6" opacity=".65" />
        <circle cx="14" cy="14" r="12.5" fill="none" stroke="var(--primary)" strokeWidth="1.2" strokeDasharray="2 3" opacity=".5" />
      </svg>
      <span className="brand-name">Knock-On Atlas</span>
    </a>
  );
}

function NewAnalysis({ dialog, toast }: { dialog: RefObject<HTMLDialogElement | null>; toast: (m: string) => void }) {
  const [accession, setAccession] = useState("");
  const [error, setError] = useState<string | null>(null);
  const events = useEvents();
  const start = useStart(toast);
  return (
    <dialog ref={dialog} aria-labelledby="dlgTitle">
      <form
        className="dlg"
        onSubmit={(e) => {
          e.preventDefault();
          // The API needs the filing's hub; the inbox already knows it.
          const event = events.data?.find((x) => x.accession === accession.trim());
          if (!event) {
            setError("That filing isn’t in the inbox. Start from a filing listed there.");
            return;
          }
          setError(null);
          dialog.current?.close();
          start.mutate({ hubCik: event.hubCik, accession: event.accession });
        }}
      >
        <h2 id="dlgTitle">New analysis</h2>
        <div className="field">
          <label htmlFor="target">8-K accession number</label>
          <input type="text" id="target" placeholder="0000037996-25-000238" autoComplete="off" value={accession} onChange={(e) => setAccession(e.target.value)} />
        </div>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="dlg-actions">
          <button className="btn" type="button" onClick={() => dialog.current?.close()}>
            Cancel
          </button>
          <button className="btn btn-primary" type="submit">
            Start analysis
          </button>
        </div>
      </form>
    </dialog>
  );
}

function Shell({ user, signOut, toast }: { user: User; signOut: () => void; toast: (m: string) => void }) {
  const route = useRoute();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health"), refetchInterval: 30_000 });
  const live = health.data?.live ?? false;
  const events = useEvents();
  const paused = usePaused();
  const edges = usePendingEdges(user);
  const dialog = useRef<HTMLDialogElement>(null);
  const waiting = (paused.data?.length ?? 0) + (edges.data?.length ?? 0);
  const hubs = new Set(events.data?.map((e) => e.hubCik)).size;
  const nav: [Route["view"], string, string][] = [
    ["inbox", "#inbox", "Inbox"],
    ["analysis", "#analysis", "Analysis"],
    ["map", "#map", "Exposure map"],
    ["review", "#review", "Review"],
  ];

  return (
    <>
      <header className="topbar">
        <Brand />
        <div className="topbar-spacer" />
        <span className="role">
          <span>Signed in as</span> {user.email} ({user.role})
        </span>
        <button className="btn btn-quiet" type="button" onClick={signOut}>
          Sign out
        </button>
        {live && (
          <button className="btn btn-primary" type="button" onClick={() => dialog.current?.showModal()}>
            New analysis
          </button>
        )}
      </header>
      <div className="shell">
        <nav className="sidenav" aria-label="Main">
          {nav.map(([view, href, label]) => (
            <a key={view} href={href} aria-current={route.view === view ? "page" : undefined}>
              {label}
              {view === "inbox" && events.data && <span className="count num"> {events.data.length}</span>}
              {view === "review" && <span className={`count num${waiting > 0 ? " alert" : ""}`}> {waiting}</span>}
            </a>
          ))}
          <p className="sidenav-foot">
            {events.data ? `${events.data.length} 8-K filings from ${hubs} large customers.` : ""}
          </p>
        </nav>
        <main>
          {health.data && !live && (
            <div className="notice" role="status">
              <p>Live runs are off. Published analyses can still be read.</p>
            </div>
          )}
          {route.view === "inbox" && <Inbox live={live} toast={toast} />}
          {route.view === "analysis" && <Analysis id={route.id} />}
          {route.view === "map" && <ExposureMap cik={route.cik} asOf={route.asOf} />}
          {route.view === "review" && <Review user={user} live={live} toast={toast} />}
        </main>
      </div>
      <NewAnalysis dialog={dialog} toast={toast} />
    </>
  );
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toast = (text: string) => {
    setMessage(text);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(null), 3200);
  };
  const signOut = (why: string | null = null) => {
    setToken(null);
    client.clear();
    setUser(null);
    setNotice(why);
  };
  // The cache handlers outlive renders, so they call the latest functions.
  const latest = useRef({ signOut, toast });
  latest.current = { signOut, toast };
  const [client] = useState(() => {
    const failed = (error: Error) => {
      if (!(error instanceof ApiError)) return;
      if (error.status === 401 && !error.message.startsWith("wrong")) {
        latest.current.signOut("Your session ended. Sign in again.");
      }
    };
    return new QueryClient({
      defaultOptions: { queries: { retry: false } },
      queryCache: new QueryCache({ onError: failed }),
      mutationCache: new MutationCache({
        onError: (error) => {
          failed(error);
          // The login form shows its own errors; everything else is a toast.
          if (error instanceof ApiError && error.status !== 401) {
            latest.current.toast(sentence(error.message));
            if (error.status === 503) void client.invalidateQueries({ queryKey: ["health"] });
          }
        },
      }),
    });
  });

  return (
    <QueryClientProvider client={client}>
      {user ? (
        <Shell user={user} signOut={() => signOut()} toast={toast} />
      ) : (
        <Login
          notice={notice}
          onSignedIn={(u) => {
            setNotice(null);
            setUser(u);
          }}
        />
      )}
      <div className={`toast${message ? " show" : ""}`} role="status" aria-live="polite">
        {message}
      </div>
    </QueryClientProvider>
  );
}
