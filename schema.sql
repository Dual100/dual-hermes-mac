-- ============================================================================
-- DualHermes Hunter — Complete DDL
-- All tables across V1+V2+V3+V4+V5
-- ============================================================================
-- Deploy as: psql -U postgres -d dual_creator_bot -f schema.sql
-- Safe to re-run (IF NOT EXISTS everywhere).
-- ============================================================================

-- ============================================================================
-- V1: CORE SIGNAL INGESTION & CONVERGENCE
-- ============================================================================

-- Append-only fila de TODOS os sinais de TODAS as fontes.
-- Processada pelo convergence engine.
CREATE TABLE IF NOT EXISTS hunter_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    source_weight REAL NOT NULL,
    token_address TEXT,
    chain TEXT NOT NULL DEFAULT 'base',
    event_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    processed INT DEFAULT 0,
    convergence_batch_id BIGINT
);
CREATE INDEX IF NOT EXISTS idx_hs_token_created ON hunter_signals (token_address, created_at DESC)
    WHERE token_address IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_hs_source_created ON hunter_signals (source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hs_unprocessed ON hunter_signals (processed, created_at)
    WHERE processed = 0;

-- Runs do convergence engine (cada 30s)
CREATE TABLE IF NOT EXISTS convergence_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    window_minutes INT NOT NULL,
    tokens_analyzed INT,
    high_signals_emitted INT
);

-- Tokens analisados por cada run
CREATE TABLE IF NOT EXISTS convergence_tokens (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES convergence_runs(id),
    token_address TEXT NOT NULL,
    signal_count INT NOT NULL,
    unique_sources INT NOT NULL,
    convergence_score REAL NOT NULL,
    sources_list TEXT[],
    first_signal_at TIMESTAMPTZ,
    last_signal_at TIMESTAMPTZ,
    emitted_alert INT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ct_run_score ON convergence_tokens (run_id, convergence_score DESC);
CREATE INDEX IF NOT EXISTS idx_ct_token ON convergence_tokens (token_address, run_id DESC);

-- Alertas enviados (persistente)
CREATE TABLE IF NOT EXISTS hunter_alerts (
    id BIGSERIAL PRIMARY KEY,
    alerted_at TIMESTAMPTZ DEFAULT NOW(),
    token_address TEXT NOT NULL,
    chain TEXT,
    convergence_score REAL,
    hermes_final_score REAL,
    narrative TEXT,
    narrative_stage TEXT,              -- EMERGING/GROWING/PEAK/COOLING
    trigger_path TEXT,                 -- HOT/FAST/STANDARD
    action TEXT,                       -- ALERT/WATCH/SKIP
    message_text TEXT,
    telegram_message_id BIGINT,
    user_action TEXT,                  -- BUY/SKIP/RESEARCH_MORE/null
    user_action_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ha_token_alerted ON hunter_alerts (token_address, alerted_at DESC);
CREATE INDEX IF NOT EXISTS idx_ha_action ON hunter_alerts (action, alerted_at DESC);

-- Outcome tracking (feedback loop)
CREATE TABLE IF NOT EXISTS hunter_outcomes (
    id BIGSERIAL PRIMARY KEY,
    alert_id BIGINT REFERENCES hunter_alerts(id),
    interval_label TEXT,               -- 1h/6h/24h/7d
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    price_at_alert NUMERIC,
    price_now NUMERIC,
    mcap_at_alert NUMERIC,
    mcap_now NUMERIC,
    roi_pct REAL,
    max_roi_interval REAL,
    is_hit INT                         -- 1 if ROI >= 50% at interval
);
CREATE INDEX IF NOT EXISTS idx_ho_alert ON hunter_outcomes (alert_id, interval_label);


-- ============================================================================
-- V2: NARRATIVE ENGINE
-- ============================================================================

CREATE TABLE IF NOT EXISTS narratives (
    id BIGSERIAL PRIMARY KEY,
    theme TEXT NOT NULL,
    description TEXT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    mention_count INT DEFAULT 0,
    unique_sources INT DEFAULT 0,
    velocity REAL,
    peak_velocity REAL,
    related_handles TEXT[],
    related_keywords TEXT[],
    status TEXT,                       -- emerging/growing/peak/cooling/dead
    stage_transitions JSONB,           -- history of stage changes
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nr_theme ON narratives (theme);
CREATE INDEX IF NOT EXISTS idx_nr_emerging ON narratives (velocity DESC) WHERE status = 'emerging';

CREATE TABLE IF NOT EXISTS narrative_mentions (
    id BIGSERIAL PRIMARY KEY,
    narrative_id BIGINT REFERENCES narratives(id),
    source TEXT,                       -- twitter/telegram/news/polymarket
    source_id TEXT,
    author TEXT,
    author_tier TEXT,                  -- MEGA/ALPHA/RISING/PLATFORM/MEDIA/NOISE
    engagement INT,
    content_excerpt TEXT,
    created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_nm_narr ON narrative_mentions (narrative_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nm_source ON narrative_mentions (source, created_at DESC);

-- Matching token ↔ narrativa
CREATE TABLE IF NOT EXISTS token_narrative_matches (
    id BIGSERIAL PRIMARY KEY,
    token_address TEXT NOT NULL,
    chain TEXT DEFAULT 'base',
    narrative_id BIGINT REFERENCES narratives(id),
    match_strength REAL,
    match_method TEXT,                 -- keyword/embedding/llm
    matched_at TIMESTAMPTZ DEFAULT NOW(),
    reasoning TEXT,
    age_rank INT,                      -- position when sorted by age (1 = oldest among matches)
    is_primary_candidate INT DEFAULT 0 -- 1 if this is the top-ranked match for this narrative
);
CREATE INDEX IF NOT EXISTS idx_tnm_token ON token_narrative_matches (token_address, chain);
CREATE INDEX IF NOT EXISTS idx_tnm_narrative ON token_narrative_matches (narrative_id, match_strength DESC);


-- ============================================================================
-- V3: MONITORED ACCOUNTS (MEGA/ALPHA/RISING/NOISE tiers)
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitored_accounts (
    handle TEXT NOT NULL,
    platform TEXT NOT NULL,            -- twitter/telegram
    tier TEXT NOT NULL,                -- MEGA/ALPHA/RISING/PLATFORM/MEDIA/NOISE
    follower_count INT,
    verified INT,
    specialty TEXT[],
    avg_pump_when_posts REAL,
    signal_quality_score REAL,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    added_by TEXT,                     -- 'manual' or 'auto_discovered'
    last_checked_at TIMESTAMPTZ,
    notes TEXT,
    PRIMARY KEY (handle, platform)
);
CREATE INDEX IF NOT EXISTS idx_ma_tier ON monitored_accounts (tier, signal_quality_score DESC);


-- ============================================================================
-- V4: CASCADE, PATTERNS, CHAIN HOTNESS
-- ============================================================================

-- Cross-chain cascade patterns (ETH→Base, ETH→Sol, etc.)
CREATE TABLE IF NOT EXISTS cascade_patterns (
    id BIGSERIAL PRIMARY KEY,
    primary_chain TEXT,
    secondary_chain TEXT,
    ticker TEXT,
    primary_token_address TEXT,
    secondary_token_address TEXT,
    primary_pump_start TIMESTAMPTZ,
    primary_peak_pct REAL,
    secondary_pump_start TIMESTAMPTZ,
    secondary_peak_pct REAL,
    cascade_delay_minutes INT,
    cascade_success INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cp_chains ON cascade_patterns (primary_chain, secondary_chain);
CREATE INDEX IF NOT EXISTS idx_cp_ticker ON cascade_patterns (ticker, created_at DESC);

-- Anatomia dos pumps históricos (pattern library)
CREATE TABLE IF NOT EXISTS pump_patterns (
    id BIGSERIAL PRIMARY KEY,
    pattern_type TEXT,                 -- platform_driven/kol_driven/narrative_driven/cascade/smart_money_first/organic
    signature_features JSONB,          -- features characterizing the pattern
    occurrence_count INT DEFAULT 1,
    avg_roi_24h REAL,
    avg_time_to_peak_min INT,
    precision_rate REAL,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);

-- Instâncias de pumps analisados
CREATE TABLE IF NOT EXISTS pump_instances (
    id BIGSERIAL PRIMARY KEY,
    token_address TEXT NOT NULL,
    chain TEXT,
    pattern_id BIGINT REFERENCES pump_patterns(id),
    pump_start_at TIMESTAMPTZ,
    pump_peak_at TIMESTAMPTZ,
    pump_peak_pct REAL,
    first_signal_source TEXT,
    first_signal_at TIMESTAMPTZ,
    smart_money_entry_at TIMESTAMPTZ,
    time_from_first_signal_to_smart_money_min INT,
    narrative TEXT,
    catalyst_type TEXT,                -- platform/kol/news/organic
    catalyst_source TEXT,
    catalyst_url TEXT,
    analyzed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pi_token ON pump_instances (token_address, pump_start_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_pattern ON pump_instances (pattern_id);

-- Chain hotness
CREATE TABLE IF NOT EXISTS chain_hotness (
    date DATE,
    chain TEXT,
    volume_24h NUMERIC,
    pump_count_24h INT,
    new_tokens_24h INT,
    avg_pump_magnitude REAL,
    hotness_score REAL,                -- 0-100
    rank INT,
    PRIMARY KEY (date, chain)
);
CREATE INDEX IF NOT EXISTS idx_ch_rank ON chain_hotness (date DESC, rank);


-- ============================================================================
-- V5: POLYMARKET + X COMMUNITIES (new sources)
-- ============================================================================

CREATE TABLE IF NOT EXISTS polymarket_events (
    id BIGSERIAL PRIMARY KEY,
    polymarket_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    slug TEXT,
    category TEXT,
    volume_usd NUMERIC,
    liquidity_usd NUMERIC,
    open_interest_usd NUMERIC,
    is_crypto_related INT DEFAULT 0,
    matched_keywords TEXT[],
    extracted_tickers TEXT[],
    outcomes JSONB,
    top_outcome TEXT,
    top_outcome_odds REAL,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ DEFAULT NOW(),
    end_date TIMESTAMPTZ,
    trending_rank INT,
    trending_velocity REAL
);
CREATE INDEX IF NOT EXISTS idx_pm_crypto ON polymarket_events (is_crypto_related, last_updated_at DESC)
    WHERE is_crypto_related = 1;
CREATE INDEX IF NOT EXISTS idx_pm_tickers ON polymarket_events USING GIN (extracted_tickers);
CREATE INDEX IF NOT EXISTS idx_pm_volume ON polymarket_events (volume_usd DESC);

-- X Communities monitoring
CREATE TABLE IF NOT EXISTS monitored_x_communities (
    community_id TEXT PRIMARY KEY,
    community_url TEXT,
    name TEXT,
    tier TEXT,                         -- TIER_1/TIER_2
    added_at TIMESTAMPTZ DEFAULT NOW(),
    last_scraped_at TIMESTAMPTZ,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS x_community_signals (
    id BIGSERIAL PRIMARY KEY,
    community_id TEXT REFERENCES monitored_x_communities(community_id),
    post_id TEXT,
    author TEXT,
    content TEXT,
    posted_at TIMESTAMPTZ,
    mentioned_tokens TEXT[],
    mentioned_tickers TEXT[],
    engagement INT,
    sentiment TEXT,
    is_shill INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_xcs_community ON x_community_signals (community_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xcs_tickers ON x_community_signals USING GIN (mentioned_tickers);

-- Telegram signals (already defined in telegram_group_monitor.py, duplicated here for completeness)
CREATE TABLE IF NOT EXISTS telegram_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    tier INT NOT NULL DEFAULT 2,
    sender_id TEXT,
    sender_handle TEXT,
    message_id BIGINT,
    message_text TEXT NOT NULL,
    reply_to_id BIGINT,
    forward_from TEXT,
    token_address TEXT,
    ticker TEXT,
    chain TEXT,
    mentions_json JSONB,
    sentiment TEXT,
    urgency_score REAL,
    is_shill INT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tg_signals_created ON telegram_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tg_signals_token ON telegram_signals (token_address)
    WHERE token_address IS NOT NULL;


-- ============================================================================
-- VIEWS (for Hermes MCP queries)
-- ============================================================================

-- Active emerging narratives (last 6h)
CREATE OR REPLACE VIEW v_active_narratives AS
SELECT
    theme,
    status,
    velocity,
    mention_count,
    unique_sources,
    related_keywords,
    related_handles,
    last_seen_at,
    EXTRACT(EPOCH FROM (NOW() - first_seen_at)) / 60 AS age_minutes
FROM narratives
WHERE status IN ('emerging', 'growing', 'peak')
  AND last_seen_at > NOW() - INTERVAL '6 hours'
ORDER BY velocity DESC;

-- Crypto-related Polymarket trending
CREATE OR REPLACE VIEW v_polymarket_crypto_trending AS
SELECT
    polymarket_id,
    title,
    slug,
    volume_usd,
    liquidity_usd,
    extracted_tickers,
    top_outcome,
    top_outcome_odds,
    trending_rank,
    last_updated_at
FROM polymarket_events
WHERE is_crypto_related = 1
  AND last_updated_at > NOW() - INTERVAL '24 hours'
ORDER BY volume_usd DESC;

-- Recent high alerts (for Hermes context)
CREATE OR REPLACE VIEW v_recent_alerts AS
SELECT
    ha.id,
    ha.alerted_at,
    ha.token_address,
    ha.chain,
    ha.convergence_score,
    ha.hermes_final_score,
    ha.narrative,
    ha.narrative_stage,
    ha.trigger_path,
    ha.action,
    ha.user_action,
    (SELECT roi_pct FROM hunter_outcomes ho
       WHERE ho.alert_id = ha.id AND ho.interval_label = '24h' LIMIT 1) AS roi_24h,
    (SELECT roi_pct FROM hunter_outcomes ho
       WHERE ho.alert_id = ha.id AND ho.interval_label = '7d' LIMIT 1) AS roi_7d
FROM hunter_alerts ha
WHERE ha.alerted_at > NOW() - INTERVAL '30 days'
ORDER BY ha.alerted_at DESC;

-- Token cross-references (helps the age-ranking decision)
CREATE OR REPLACE VIEW v_token_cross_chain AS
SELECT
    tnm.narrative_id,
    n.theme AS narrative,
    tnm.token_address,
    tnm.chain,
    tnm.match_strength,
    tnm.age_rank,
    tnm.is_primary_candidate
FROM token_narrative_matches tnm
JOIN narratives n ON n.id = tnm.narrative_id
WHERE n.status IN ('emerging', 'growing')
ORDER BY n.theme, tnm.age_rank;


-- ============================================================================
-- READ-ONLY ROLE FOR HERMES
-- ============================================================================

-- Create role only if it doesn't exist (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hermes_readonly') THEN
        CREATE ROLE hermes_readonly LOGIN PASSWORD 'CHANGEME_AT_DEPLOY';
    END IF;
END $$;

-- Grant read-only on tables hermes needs
GRANT USAGE ON SCHEMA public TO hermes_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_readonly;

-- Allow Hermes to WRITE to hunter_* tables (these are its own)
-- NOTE: We split read vs write roles carefully. Hermes needs write on its own tables.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hermes_writer') THEN
        CREATE ROLE hermes_writer LOGIN PASSWORD 'CHANGEME_AT_DEPLOY';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO hermes_writer;
GRANT SELECT, INSERT, UPDATE ON hunter_signals, convergence_runs, convergence_tokens,
    hunter_alerts, hunter_outcomes, narratives, narrative_mentions, token_narrative_matches,
    monitored_accounts, cascade_patterns, pump_patterns, pump_instances, chain_hotness,
    polymarket_events, monitored_x_communities, x_community_signals, telegram_signals
    TO hermes_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hermes_writer;

-- hermes_writer can READ everything else (for research)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_writer;

-- DENY writes to crypto bot's sensitive tables
-- (wallet_learning, claude_decisions, trades — these are in different schemas usually)
-- If they're in public, explicitly revoke:
REVOKE INSERT, UPDATE, DELETE ON TABLE
    -- trade data (if they exist in public schema)
    -- positions, trades, agent_decisions
    -- ignore errors if tables don't exist
    -- wrap in DO block for safety
    FROM hermes_writer;
