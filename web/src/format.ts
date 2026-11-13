// Number and date formats from the UI reference: one decimal for shares, a real
// minus sign, and times in New York because that is when markets react.

export const pct = (v: number) => `${v.toFixed(1)}%`;

export const signed = (v: number, digits = 1) =>
  `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;

const eastern = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export const filedAt = (iso: string) =>
  eastern.format(new Date(iso)).replace(" AM", " am").replace(" PM", " pm");

// A calendar date as stored (YYYY-MM-DD), shown without a time zone shift.
export const day = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T12:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });

export const filingUrl = (cik: string, accession: string) =>
  `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accession.replace(/-/g, "")}/`;

export const itemNames: Record<string, string> = {
  "1.01": "Material agreement",
  "1.02": "Agreement ended",
  "1.03": "Bankruptcy",
  "2.01": "Acquisition or disposal",
  "2.02": "Results of operations",
  "2.03": "New debt obligation",
  "2.05": "Exit or disposal costs",
  "2.06": "Material impairment",
  "4.02": "Earlier financials not to be relied on",
  "5.02": "Officer change",
  "5.07": "Shareholder vote",
  "7.01": "Regulation FD disclosure",
  "8.01": "Other events",
  "9.01": "Financial statements and exhibits",
};

export const items = (codes: string[]) =>
  codes.length > 1
    ? `${codes.slice(0, -1).join(", ")} and ${codes[codes.length - 1]}`
    : (codes[0] ?? "");
