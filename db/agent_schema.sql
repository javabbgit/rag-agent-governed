-- Agent tables. Idempotent; db/schema.sql is untouched. Applied by `make agent-seed`.

CREATE SCHEMA IF NOT EXISTS agent;

CREATE TABLE IF NOT EXISTS agent.loans (
  loan_number text PRIMARY KEY,
  product_code text NOT NULL,
  stage text NOT NULL CHECK (stage IN ('application_received', 'conditionally_approved',
    'conditions_outstanding', 'funded', 'renewal_pending', 'declined')),
  amount integer NOT NULL,
  rate_pct numeric(5,2) NOT NULL,
  amortization_years integer NOT NULL,
  term_years integer NOT NULL,
  payment_frequency text NOT NULL,
  gross_annual_income integer NOT NULL,
  property_tax_monthly integer NOT NULL,
  condo_fees_monthly integer NOT NULL,
  other_debt_monthly integer NOT NULL,
  outstanding_conditions text[] NOT NULL DEFAULT '{}',
  borrower_ref text NOT NULL,
  broker_id text NOT NULL,
  underwriter_id text NOT NULL);

CREATE TABLE IF NOT EXISTS agent.contacts (
  contact_id text PRIMARY KEY,
  loan_number text NOT NULL REFERENCES agent.loans (loan_number),
  role_label text NOT NULL CHECK (role_label IN ('broker contact', 'borrower')));

CREATE SEQUENCE IF NOT EXISTS agent.exception_request_seq;

CREATE TABLE IF NOT EXISTS agent.exception_requests (
  request_id text PRIMARY KEY
    DEFAULT ('ER-' || lpad(nextval('agent.exception_request_seq')::text, 6, '0')),
  form text NOT NULL DEFAULT 'EXC-100',
  loan_number text NOT NULL REFERENCES agent.loans (loan_number),
  exception_type text NOT NULL
    CHECK (exception_type IN ('gds_tds_ratio', 'ltv', 'income_documents', 'rate_hold')),
  requested_value text NOT NULL,
  justification text NOT NULL,
  justification_sha256 text NOT NULL,
  compensating_factors text[] NOT NULL,
  supporting_chunk_ids text[] NOT NULL,
  current_gds_pct numeric(6,2),
  current_tds_pct numeric(6,2),
  overage_pts numeric(6,2),
  required_authority text NOT NULL,
  prepared_by text NOT NULL,
  run_id text NOT NULL,
  prepared_at timestamptz NOT NULL DEFAULT now(),
  raised_by text,
  raised_at timestamptz,
  decided_by text,
  decided_at timestamptz,
  decided_reason text,
  status text NOT NULL DEFAULT 'awaiting_raise'
    CHECK (status IN ('awaiting_raise', 'pending_approval', 'approved', 'refused', 'withdrawn')),
  CONSTRAINT raised_needed CHECK (
    status NOT IN ('pending_approval', 'approved', 'refused') OR raised_by IS NOT NULL),
  CONSTRAINT decision_needs_human CHECK (
    status NOT IN ('approved', 'refused')
    OR (decided_by IS NOT NULL AND decided_by <> raised_by AND decided_by <> prepared_by)),
  CONSTRAINT reason_needed CHECK (
    status NOT IN ('refused', 'withdrawn') OR decided_reason IS NOT NULL));

CREATE UNIQUE INDEX IF NOT EXISTS exception_requests_one_open
  ON agent.exception_requests (loan_number, exception_type)
  WHERE status IN ('awaiting_raise', 'pending_approval');

CREATE SEQUENCE IF NOT EXISTS agent.draft_seq;

CREATE TABLE IF NOT EXISTS agent.drafts (
  draft_id text PRIMARY KEY DEFAULT ('DR-' || lpad(nextval('agent.draft_seq')::text, 6, '0')),
  loan_number text NOT NULL REFERENCES agent.loans (loan_number),
  recipient_contact_id text NOT NULL REFERENCES agent.contacts (contact_id),
  subject text NOT NULL,
  body text NOT NULL,
  body_sha256 text NOT NULL,
  created_by text NOT NULL,
  run_id text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS agent.policy_events (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  run_id text NOT NULL,
  user_id text NOT NULL,
  role text NOT NULL,
  rule_stage smallint NOT NULL,
  step smallint,
  tool text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('allow', 'deny', 'draft', 'error')),
  reason text,
  reached_executor boolean NOT NULL,
  defenses jsonb NOT NULL,
  args_digest jsonb NOT NULL);
