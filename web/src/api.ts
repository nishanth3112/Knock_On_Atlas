// The API client and the shapes it returns. The API speaks snake_case JSON; the
// app uses camelCase, converted here and nowhere else.

export type Role = "analyst" | "reviewer" | "admin";
export type RunStatus = "queued" | "paused" | "published" | "rejected" | "ended";
export type TriageLabel =
  | "MATERIAL_NEGATIVE"
  | "MATERIAL_POSITIVE"
  | "ROUTINE"
  | "NOT_SUPPLY_CHAIN"
  | "MONITOR";

export interface User {
  id: number;
  email: string;
  role: Role;
}

export interface Health {
  ok: boolean;
  live: boolean;
}

export interface Triage {
  label: TriageLabel;
  method: string;
  probability: number | null;
  reason: string;
}

export interface EventRow {
  accession: string;
  hubCik: string;
  hubName: string | null;
  acceptedAt: string;
  items: string[];
  url: string;
  run: { id: string; status: RunStatus; triage: Triage | null } | null;
}

export interface ExposureEdge {
  supplierCik: string;
  name: string;
  depth: number;
  weight: number;
  note: string;
}

export interface Evidence {
  supplierCik: string;
  chunkId: number;
  quote: string;
  url: string;
}

export interface StudyRow {
  cik: string;
  depth: number;
  status: string;
  day0: string | null;
  car01: number | null;
  carM15: number | null;
  t01: number | null;
  tM15: number | null;
}

export interface Claim {
  text: string;
  supplierCiks: string[];
  citations: number[];
}

export interface RunResult {
  hubName?: string;
  triage?: Triage;
  plan?: {
    hubCik: string;
    asOf: string;
    suppliers: { cik: string; viaCik: string; depth: number; weight: number }[];
  };
  exposures?: ExposureEdge[];
  evidence?: Evidence[];
  market?: StudyRow[];
  verification?: { ok: boolean; failedSuppliers: string[] };
  gate?: { route: "AUTO_PUBLISH" | "HUMAN_REVIEW"; reasons: string[] };
  usage?: { llmCalls: number; toolCalls: number; tokens: number };
  review?: { decision: "approved" | "rejected"; reviewer: string; note: string };
  brief?: { headline: string; claims: Claim[]; gaps: string[] };
  reasons?: string[];
  errors?: string[];
}

export interface Analysis {
  id: string;
  status: RunStatus;
  input: { hubCik: string; accession: string };
  result: RunResult | null;
  createdAt: string;
  completedAt: string | null;
  hubName: string | null;
  event: { acceptedAt: string; items: string[]; url: string } | null;
}

export interface MapEdge {
  id: number;
  pct: number;
  memberLabel: string;
  sourceAccession: string;
  confidence: number;
  periodEnd: string;
  validFrom: string;
  validTo: string | null;
  reviewedAt: string | null;
  extractionMethod: string;
}

export interface MapSupplier {
  supplierCik: string;
  viaCik: string;
  weight: number;
  depth: number;
  edgeIds: number[];
  name: string;
  tickers: string[];
  edge: MapEdge;
}

export interface ExposureMap {
  hub: { cik: string; name: string; tickers: string[] };
  asOf: string;
  suppliers: MapSupplier[];
}

export interface PendingEdge {
  id: number;
  supplier: string;
  hub: string;
  memberLabel: string;
  pct: number;
  totalShare: boolean;
  relationshipFlag: string | null;
  extractionMethod: string;
  confidence: number;
  periodEnd: string;
  filingUrl: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// In memory only: a reload means signing in again, and tokens last 15 minutes.
let token: string | null = null;

export function setToken(value: string | null): void {
  token = value;
}

const camel = (key: string) => key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
const snake = (key: string) => key.replace(/[A-Z]/g, (c) => "_" + c.toLowerCase());

function convertKeys(value: unknown, convert: (key: string) => string): unknown {
  if (Array.isArray(value)) return value.map((v) => convertKeys(v, convert));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [convert(k), convertKeys(v, convert)]),
    );
  }
  return value;
}

async function request<T>(method: string, path: string, body?: object): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(convertKeys(body, snake)) : undefined,
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    // FastAPI sends a string for our errors and a list for validation errors.
    const message = typeof detail === "string" ? detail : "The request was not accepted.";
    throw new ApiError(response.status, message);
  }
  return convertKeys(data, camel) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: object) => request<T>("POST", path, body),
};
