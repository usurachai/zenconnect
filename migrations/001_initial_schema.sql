CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subdomain TEXT UNIQUE NOT NULL,
    sunco_app_id TEXT NOT NULL,
    sunco_key_id TEXT NOT NULL,
    sunco_key_secret TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id TEXT UNIQUE NOT NULL,
    tenant_id UUID REFERENCES tenants(id),
    app_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    agent_mode TEXT NOT NULL DEFAULT 'ai',
    user_id TEXT NOT NULL,
    external_id TEXT,
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    is_first_msg_sent BOOLEAN NOT NULL DEFAULT FALSE,
    human_requested_at TIMESTAMPTZ,
    last_replied_at TIMESTAMPTZ,
    last_message_received_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id TEXT UNIQUE NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    author_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS message_buffer (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    message_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT UNIQUE NOT NULL,
    conversation_id TEXT,
    raw_payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
