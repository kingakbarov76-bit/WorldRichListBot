CREATE SCHEMA IF NOT EXISTS worldrichlist;
CREATE SEQUENCE IF NOT EXISTS worldrichlist.donor_seq;

CREATE TABLE IF NOT EXISTS worldrichlist.users (
    user_id      bigint PRIMARY KEY,
    username     text,
    full_name    text NOT NULL DEFAULT '',
    total_stars  bigint NOT NULL DEFAULT 0,
    is_anonymous boolean NOT NULL DEFAULT false,
    donor_no     integer,
    referrer_id  bigint,
    country_code char(2),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worldrichlist.payments (
    charge_id    text PRIMARY KEY,
    user_id      bigint NOT NULL REFERENCES worldrichlist.users(user_id),
    amount       integer NOT NULL CHECK (amount > 0),
    country_code char(2),
    ts           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wrl_payments_ts      ON worldrichlist.payments (ts);
CREATE INDEX IF NOT EXISTS idx_wrl_payments_country ON worldrichlist.payments (country_code, ts);
CREATE INDEX IF NOT EXISTS idx_wrl_payments_user    ON worldrichlist.payments (user_id);
CREATE INDEX IF NOT EXISTS idx_wrl_users_total      ON worldrichlist.users (total_stars DESC);
CREATE INDEX IF NOT EXISTS idx_wrl_users_country    ON worldrichlist.users (country_code);
CREATE INDEX IF NOT EXISTS idx_wrl_users_referrer   ON worldrichlist.users (referrer_id);

ALTER TABLE worldrichlist.users    ENABLE ROW LEVEL SECURITY;
ALTER TABLE worldrichlist.payments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA worldrichlist FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA worldrichlist FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA worldrichlist FROM anon, authenticated;
