-- Foundation only: no migration is run automatically.
-- Keep owner and DNA unique across all participating chats.
CREATE TABLE IF NOT EXISTS fosgen_pet_message_events (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    counted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS fosgen_pet_message_events_owner_idx
    ON fosgen_pet_message_events (user_id);

CREATE TABLE IF NOT EXISTS fosgen_pet_opt_ins (
    user_id BIGINT PRIMARY KEY,
    opted_in_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fosgen_pets (
    pet_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id BIGINT NOT NULL UNIQUE REFERENCES fosgen_pet_opt_ins(user_id),
    species TEXT NOT NULL,
    appearance_dna JSONB NOT NULL,
    appearance_fingerprint CHAR(64) NOT NULL UNIQUE,
    asset_version INTEGER NOT NULL DEFAULT 1,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    portrait_file_id TEXT,
    CHECK (asset_version > 0)
);
CREATE TABLE IF NOT EXISTS fosgen_pet_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pet_id BIGINT NOT NULL REFERENCES fosgen_pets(pet_id),
    event_type TEXT NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fosgen_pet_events_pet_idx ON fosgen_pet_events (pet_id, created_at);

CREATE TABLE IF NOT EXISTS fosgen_pet_publication_permissions (
    owner_user_id BIGINT PRIMARY KEY REFERENCES fosgen_pet_opt_ins(user_id),
    public_profile BOOLEAN NOT NULL DEFAULT FALSE,
    literary_use BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
