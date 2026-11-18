CREATE TABLE IF NOT EXISTS worldrichlist.meta (key text PRIMARY KEY, value text NOT NULL);
ALTER TABLE worldrichlist.meta ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY wrl_bot_meta ON worldrichlist.meta FOR ALL TO wrl_bot USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT SELECT, INSERT, UPDATE ON worldrichlist.meta TO wrl_bot;
ALTER TABLE worldrichlist.users ADD COLUMN IF NOT EXISTS notify boolean NOT NULL DEFAULT true;
