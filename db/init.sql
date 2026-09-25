CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    repository TEXT NOT NULL,
    branch TEXT,
    ticket_id TEXT,
    event_timestamp TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_repository ON events (repository);
CREATE INDEX IF NOT EXISTS idx_events_skill_name ON events (skill_name);
