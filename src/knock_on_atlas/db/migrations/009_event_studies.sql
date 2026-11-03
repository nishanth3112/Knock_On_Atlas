-- One market-model event study per (8-K, company): the filer at depth 0, its suppliers at 1-2.
CREATE TABLE event_studies (
  accession text NOT NULL,
  cik text NOT NULL,
  ticker text,
  depth int NOT NULL,
  day0 date,
  status text NOT NULL
    CHECK (status IN ('pending', 'complete', 'insufficient_history', 'no_ticker')),
  car_0_1 double precision,
  car_m1_5 double precision,
  t_0_1 double precision,
  t_m1_5 double precision,
  alpha double precision,
  beta double precision,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (accession, cik)
);
