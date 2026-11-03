-- Daily split-adjusted closes (Twelve Data default); raw prices stay local, never committed.
CREATE TABLE price_bars (
  ticker text NOT NULL,
  d date NOT NULL,
  close numeric NOT NULL,
  PRIMARY KEY (ticker, d)
);
