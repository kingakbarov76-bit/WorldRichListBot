ALTER TABLE worldrichlist.users
    ADD COLUMN IF NOT EXISTS region_code  text,
    ADD COLUMN IF NOT EXISTS activated_at timestamptz;

ALTER TABLE worldrichlist.payments
    ADD COLUMN IF NOT EXISTS region_code text;

-- A referral counts once the invited person has picked a country ("activated").
UPDATE worldrichlist.users SET activated_at = created_at
 WHERE country_code IS NOT NULL AND activated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_wrl_users_region     ON worldrichlist.users (region_code);
CREATE INDEX IF NOT EXISTS idx_wrl_users_ref_active ON worldrichlist.users (referrer_id, activated_at);
CREATE INDEX IF NOT EXISTS idx_wrl_payments_region  ON worldrichlist.payments (region_code, ts);
