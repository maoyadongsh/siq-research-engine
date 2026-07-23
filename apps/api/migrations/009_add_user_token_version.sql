-- Invalidate all access tokens for a user when a security-sensitive account
-- change is made. Existing users start at version zero.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

UPDATE users
SET token_version = 0
WHERE token_version IS NULL;

-- The default and NOT NULL constraint are intentionally kept on the column so
-- newly created users cannot accidentally receive a nullable version.
ALTER TABLE users
    ALTER COLUMN token_version SET DEFAULT 0;
ALTER TABLE users
    ALTER COLUMN token_version SET NOT NULL;
