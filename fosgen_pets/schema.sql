-- Executed by PostgresPetStore inside an advisory-locked migration transaction.
-- Additive upgrades preserve every pre-existing owner, DNA and saved portrait.
CREATE TABLE IF NOT EXISTS fosgen_pet_opt_ins (
    user_id BIGINT PRIMARY KEY,
    opted_in_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    qualifying_count INTEGER NOT NULL DEFAULT 0 CHECK (qualifying_count BETWEEN 0 AND 100),
    last_counted_at TIMESTAMPTZ,
    message_digest_key BYTEA
);
ALTER TABLE fosgen_pet_opt_ins ADD COLUMN IF NOT EXISTS qualifying_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fosgen_pet_opt_ins ADD COLUMN IF NOT EXISTS last_counted_at TIMESTAMPTZ;
ALTER TABLE fosgen_pet_opt_ins ADD COLUMN IF NOT EXISTS message_digest_key BYTEA;

CREATE TABLE IF NOT EXISTS fosgen_pet_message_events (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    counted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    counted BOOLEAN NOT NULL DEFAULT FALSE,
    message_digest CHAR(64),
    PRIMARY KEY (chat_id, message_id)
);
-- Foundation events were all counted. New writes always pass counted explicitly.
ALTER TABLE fosgen_pet_message_events ADD COLUMN IF NOT EXISTS counted BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE fosgen_pet_message_events ADD COLUMN IF NOT EXISTS message_digest CHAR(64);
CREATE INDEX IF NOT EXISTS fosgen_pet_message_events_owner_idx
    ON fosgen_pet_message_events (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS fosgen_pet_message_digest_idx
    ON fosgen_pet_message_events (user_id, message_digest) WHERE counted AND message_digest IS NOT NULL;
CREATE INDEX IF NOT EXISTS fosgen_pet_rejected_retention_idx
    ON fosgen_pet_message_events (user_id, counted_at DESC, chat_id DESC, message_id DESC) WHERE NOT counted;
DELETE FROM fosgen_pet_message_events AS events USING (
    SELECT chat_id, message_id, ROW_NUMBER() OVER (
        PARTITION BY user_id ORDER BY counted_at DESC, chat_id DESC, message_id DESC
    ) AS position FROM fosgen_pet_message_events WHERE NOT counted
) AS old_rejections
WHERE events.chat_id=old_rejections.chat_id AND events.message_id=old_rejections.message_id
    AND old_rejections.position > 256;
UPDATE fosgen_pet_opt_ins AS owners SET
    qualifying_count = GREATEST(owners.qualifying_count, old_events.total),
    last_counted_at = GREATEST(owners.last_counted_at, old_events.last_counted_at)
FROM (SELECT user_id, LEAST(100, COUNT(*))::INTEGER AS total, MAX(counted_at) AS last_counted_at
      FROM fosgen_pet_message_events WHERE counted GROUP BY user_id) AS old_events
WHERE owners.user_id = old_events.user_id;

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
    portrait_svg TEXT,
    portrait_png BYTEA,
    portrait_sha256 CHAR(64),
    portrait_pixel_hash CHAR(64),
    renderer_version TEXT,
    origin_story TEXT NOT NULL DEFAULT 'Этот питомец нашел свой дом и верного друга.',
    mood TEXT NOT NULL DEFAULT 'calm',
    energy INTEGER NOT NULL DEFAULT 70 CHECK (energy BETWEEN 1 AND 100),
    satiety INTEGER NOT NULL DEFAULT 70 CHECK (satiety BETWEEN 1 AND 100),
    CHECK (asset_version > 0)
);
-- Nullable art is intentional for old foundation rows: never silently rerender.
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS portrait_svg TEXT;
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS portrait_png BYTEA;
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS portrait_sha256 CHAR(64);
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS portrait_pixel_hash CHAR(64);
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS renderer_version TEXT;
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS origin_story TEXT NOT NULL DEFAULT 'Этот питомец нашел свой дом и верного друга.';
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS mood TEXT NOT NULL DEFAULT 'calm';
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS energy INTEGER NOT NULL DEFAULT 70;
ALTER TABLE fosgen_pets ADD COLUMN IF NOT EXISTS satiety INTEGER NOT NULL DEFAULT 70;
CREATE UNIQUE INDEX IF NOT EXISTS fosgen_pet_portrait_pixel_hash_idx
    ON fosgen_pets (portrait_pixel_hash) WHERE portrait_pixel_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS fosgen_pet_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pet_id BIGINT NOT NULL REFERENCES fosgen_pets(pet_id),
    event_type TEXT NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fosgen_pet_events_pet_idx ON fosgen_pet_events (pet_id, created_at);
CREATE TABLE IF NOT EXISTS fosgen_pet_care_requests (
    pet_id BIGINT NOT NULL REFERENCES fosgen_pets(pet_id),
    request_id VARCHAR(128) NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('feed', 'play', 'sleep')),
    applied BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pet_id, request_id)
);
CREATE INDEX IF NOT EXISTS fosgen_pet_care_cooldown_idx
    ON fosgen_pet_care_requests (pet_id, action, created_at DESC) WHERE applied;

CREATE TABLE IF NOT EXISTS fosgen_pet_publication_permissions (
    owner_user_id BIGINT PRIMARY KEY REFERENCES fosgen_pet_opt_ins(user_id),
    public_profile BOOLEAN NOT NULL DEFAULT FALSE,
    literary_use BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION fosgen_pet_guard_permanence() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'Permanent pets cannot be removed' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.portrait_svg IS NULL OR LENGTH(NEW.portrait_svg) = 0
           OR NEW.portrait_png IS NULL OR OCTET_LENGTH(NEW.portrait_png) = 0
           OR NEW.portrait_sha256 IS NULL OR NEW.portrait_sha256 !~ '^[0-9a-f]{64}$'
           OR NEW.portrait_pixel_hash IS NULL OR NEW.portrait_pixel_hash !~ '^[0-9a-f]{64}$'
           OR NEW.renderer_version IS NULL OR LENGTH(NEW.renderer_version) = 0 THEN
            RAISE EXCEPTION 'New pets require permanently saved portrait assets' USING ERRCODE = '23514';
        END IF;
    ELSIF ROW(NEW.pet_id, NEW.owner_user_id, NEW.species, NEW.appearance_dna,
              NEW.appearance_fingerprint, NEW.asset_version, NEW.name, NEW.created_at,
              NEW.portrait_svg, NEW.portrait_png, NEW.portrait_sha256,
              NEW.portrait_pixel_hash, NEW.renderer_version, NEW.origin_story)
          IS DISTINCT FROM
          ROW(OLD.pet_id, OLD.owner_user_id, OLD.species, OLD.appearance_dna,
              OLD.appearance_fingerprint, OLD.asset_version, OLD.name, OLD.created_at,
              OLD.portrait_svg, OLD.portrait_png, OLD.portrait_sha256,
              OLD.portrait_pixel_hash, OLD.renderer_version, OLD.origin_story) THEN
        RAISE EXCEPTION 'Permanent pet identity and portrait are immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.energy NOT BETWEEN 1 AND 100 OR NEW.satiety NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Pet care must remain healthy' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS fosgen_pet_permanence ON fosgen_pets;
CREATE TRIGGER fosgen_pet_permanence BEFORE INSERT OR UPDATE OR DELETE ON fosgen_pets
    FOR EACH ROW EXECUTE FUNCTION fosgen_pet_guard_permanence();
DROP TRIGGER IF EXISTS fosgen_pet_no_truncate ON fosgen_pets;
CREATE TRIGGER fosgen_pet_no_truncate BEFORE TRUNCATE ON fosgen_pets
    FOR EACH STATEMENT EXECUTE FUNCTION fosgen_pet_guard_permanence();

-- Independent private games never alter the permanent animal or portrait.
CREATE TABLE IF NOT EXISTS fosgen_pet_draughts_games (
    owner_user_id BIGINT PRIMARY KEY REFERENCES fosgen_pet_opt_ins(user_id),
    pet_id BIGINT NOT NULL REFERENCES fosgen_pets(pet_id),
    game_id VARCHAR(12) NOT NULL UNIQUE CHECK (game_id ~ '^[0-9a-f]{12}$'),
    board SMALLINT[] NOT NULL CHECK (cardinality(board)=64 AND board <@ ARRAY[-2,-1,0,1,2]::smallint[]),
    side SMALLINT NOT NULL DEFAULT 1 CHECK (side IN (-1,1)),
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    selected SMALLINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(selected)<=32 AND 0 <= ALL(selected) AND 63 >= ALL(selected)),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','white_won','black_won','draw')),
    no_progress INTEGER NOT NULL DEFAULT 0 CHECK (no_progress BETWEEN 0 AND 10000),
    repetitions JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(repetitions)='object' AND octet_length(repetitions::text)<=131072),
    chat_id BIGINT,
    message_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((chat_id IS NULL AND message_id IS NULL) OR (chat_id=owner_user_id AND message_id>0))
);

CREATE OR REPLACE FUNCTION fosgen_pet_guard_draughts() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM fosgen_pets
                   WHERE pet_id=NEW.pet_id AND owner_user_id=NEW.owner_user_id) THEN
        RAISE EXCEPTION 'Game requires the owner permanent pet' USING ERRCODE = '23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.owner_user_id, NEW.pet_id) IS DISTINCT FROM ROW(OLD.owner_user_id, OLD.pet_id) THEN
            RAISE EXCEPTION 'Game owner and pet binding are immutable' USING ERRCODE = '23514';
        END IF;
        IF (NEW.game_id=OLD.game_id AND NEW.revision<>OLD.revision+1)
           OR (NEW.game_id<>OLD.game_id AND NEW.revision<>0) THEN
            RAISE EXCEPTION 'Game revision must advance, or reset with a new game' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS fosgen_pet_draughts_guard ON fosgen_pet_draughts_games;
CREATE TRIGGER fosgen_pet_draughts_guard BEFORE INSERT OR UPDATE ON fosgen_pet_draughts_games
    FOR EACH ROW EXECUTE FUNCTION fosgen_pet_guard_draughts();
