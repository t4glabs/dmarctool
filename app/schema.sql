-- DMARCTool schema
-- Phase 1 (ingestion) populates: domains, reports, report_records, record_auth_results.
-- policy_history / known_senders / action_items / dns_checks / settings are used by later phases
-- but declared now so the full data model is reviewable up front and no migration is needed later.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS domains (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY,
    domain_id     INTEGER NOT NULL REFERENCES domains(id),
    org_name      TEXT,
    email         TEXT,
    report_id     TEXT,
    date_begin    INTEGER NOT NULL,
    date_end      INTEGER NOT NULL,
    policy_domain TEXT,
    policy_adkim  TEXT,
    policy_aspf   TEXT,
    policy_p      TEXT,
    policy_sp     TEXT,
    policy_pct    INTEGER,
    policy_fo     TEXT,
    source_file   TEXT,
    ingested_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(domain_id, org_name, report_id)
);

CREATE INDEX IF NOT EXISTS idx_reports_domain ON reports(domain_id);
CREATE INDEX IF NOT EXISTS idx_reports_daterange ON reports(domain_id, date_begin, date_end);

CREATE TABLE IF NOT EXISTS report_records (
    id            INTEGER PRIMARY KEY,
    report_id     INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    source_ip     TEXT NOT NULL,
    count         INTEGER NOT NULL,
    disposition   TEXT,
    dkim_result   TEXT,
    spf_result    TEXT,
    header_from   TEXT,
    envelope_from TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_report ON report_records(report_id);
CREATE INDEX IF NOT EXISTS idx_records_ip ON report_records(source_ip);

CREATE TABLE IF NOT EXISTS record_auth_results (
    id         INTEGER PRIMARY KEY,
    record_id  INTEGER NOT NULL REFERENCES report_records(id) ON DELETE CASCADE,
    mechanism  TEXT NOT NULL,   -- 'dkim' | 'spf'
    domain     TEXT,
    selector   TEXT,
    scope      TEXT,            -- spf scope: mfrom/helo
    result     TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_record ON record_auth_results(record_id);

-- Phase 3 (analysis engine)
CREATE TABLE IF NOT EXISTS policy_history (
    id             INTEGER PRIMARY KEY,
    domain_id      INTEGER NOT NULL REFERENCES domains(id),
    p              TEXT,
    sp             TEXT,
    pct            INTEGER,
    adkim          TEXT,
    aspf           TEXT,
    observed_from  INTEGER NOT NULL,
    observed_to    INTEGER,
    source         TEXT NOT NULL,   -- 'report' | 'dns_check' | 'manual_log'
    notes          TEXT
);

CREATE INDEX IF NOT EXISTS idx_policy_history_domain ON policy_history(domain_id);

CREATE TABLE IF NOT EXISTS known_senders (
    id              INTEGER PRIMARY KEY,
    domain_id       INTEGER NOT NULL REFERENCES domains(id),
    source_ip       TEXT NOT NULL,
    first_seen      INTEGER NOT NULL,
    last_seen       INTEGER NOT NULL,
    total_msgs      INTEGER NOT NULL DEFAULT 0,
    pass_msgs       INTEGER NOT NULL DEFAULT 0,
    fail_msgs       INTEGER NOT NULL DEFAULT 0,
    classification  TEXT NOT NULL DEFAULT 'unclassified', -- 'ses_pool'|'workspace'|'unclassified'|'ignored'
    label           TEXT,
    UNIQUE(domain_id, source_ip)
);

CREATE INDEX IF NOT EXISTS idx_known_senders_domain ON known_senders(domain_id);

CREATE TABLE IF NOT EXISTS action_items (
    id           INTEGER PRIMARY KEY,
    domain_id    INTEGER NOT NULL REFERENCES domains(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    kind         TEXT NOT NULL,   -- 'system_suggested' | 'manual_log'
    category     TEXT,            -- 'ramp_recommendation'|'new_sender'|'failure_investigation'|'data_stale'|NULL for manual
    ref_key      TEXT,            -- e.g. source_ip; used with (domain_id, category) to dedup open system_suggested items
    title        TEXT NOT NULL,
    detail       TEXT,
    status       TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'done' | 'dismissed'
    resolved_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_items_domain ON action_items(domain_id, status);
CREATE INDEX IF NOT EXISTS idx_action_items_dedup ON action_items(domain_id, category, ref_key, status);

-- DNS drift check
CREATE TABLE IF NOT EXISTS dns_checks (
    id               INTEGER PRIMARY KEY,
    domain_id        INTEGER NOT NULL REFERENCES domains(id),
    checked_at       TEXT NOT NULL DEFAULT (datetime('now')),
    status           TEXT NOT NULL DEFAULT 'ok',  -- 'ok'|'missing'|'multiple'|'lookup_failed'
    txt_value        TEXT,
    parsed_p         TEXT,
    parsed_pct       INTEGER,
    matches_expected INTEGER,   -- 0/1/NULL
    note             TEXT
);

CREATE INDEX IF NOT EXISTS idx_dns_checks_domain ON dns_checks(domain_id, checked_at);

-- DNSBL blocklist checks (IP-scoped, not domain-scoped -- a sending IP can serve multiple domains)
CREATE TABLE IF NOT EXISTS blocklist_checks (
    id           INTEGER PRIMARY KEY,
    source_ip    TEXT NOT NULL,
    checked_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'clean',  -- 'clean'|'listed'|'lookup_failed'
    listed_on    TEXT,   -- comma-separated blocklist names, if status='listed'
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_blocklist_checks_ip ON blocklist_checks(source_ip, checked_at);

-- Gmail sender-guidelines checks (PTR/FCrDNS, SPF DNS-lookup budget, DKIM key length) --
-- all derivable from DNS + already-ingested report data, no external credentials.
CREATE TABLE IF NOT EXISTS ptr_checks (
    id           INTEGER PRIMARY KEY,
    source_ip    TEXT NOT NULL,
    checked_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL,  -- 'confirmed'|'ptr_missing'|'mismatch'|'lookup_failed'
    ptr_hostname TEXT,
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_ptr_checks_ip ON ptr_checks(source_ip, checked_at);

CREATE TABLE IF NOT EXISTS spf_checks (
    id            INTEGER PRIMARY KEY,
    domain_id     INTEGER NOT NULL REFERENCES domains(id),
    spf_domain    TEXT NOT NULL,
    checked_at    TEXT NOT NULL DEFAULT (datetime('now')),
    status        TEXT NOT NULL,  -- 'ok'|'warn'|'over_limit'|'missing'|'lookup_failed'
    lookup_count  INTEGER,
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_spf_checks_domain ON spf_checks(domain_id, spf_domain, checked_at);

CREATE TABLE IF NOT EXISTS dkim_checks (
    id             INTEGER PRIMARY KEY,
    domain_id      INTEGER NOT NULL REFERENCES domains(id),
    signing_domain TEXT NOT NULL,
    selector       TEXT NOT NULL,
    checked_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status         TEXT NOT NULL,  -- 'ok'|'weak'|'missing'|'lookup_failed'
    key_bits       INTEGER,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS idx_dkim_checks_domain ON dkim_checks(domain_id, signing_domain, selector, checked_at);

-- Mailgun reputation + list hygiene (only for domains DMARCTool already tracks --
-- matched dynamically each run against whatever the Mailgun account has, so newly
-- ingested domains pick this up automatically with no extra config).
CREATE TABLE IF NOT EXISTS mailgun_stats (
    id             INTEGER PRIMARY KEY,
    domain_id      INTEGER NOT NULL REFERENCES domains(id),
    mailgun_domain TEXT NOT NULL,
    checked_at     TEXT NOT NULL DEFAULT (datetime('now')),
    window_days    INTEGER NOT NULL,
    accepted       INTEGER,
    delivered      INTEGER,
    failed_perm    INTEGER,
    failed_temp    INTEGER,
    complained     INTEGER,
    unsubscribed   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mailgun_stats_domain ON mailgun_stats(domain_id, checked_at);

CREATE TABLE IF NOT EXISTS mailgun_suppressions (
    id              INTEGER PRIMARY KEY,
    domain_id       INTEGER NOT NULL REFERENCES domains(id),
    mailgun_domain  TEXT NOT NULL,
    email           TEXT NOT NULL,
    kind            TEXT NOT NULL,  -- 'bounce'|'complaint'|'unsubscribe'
    reason          TEXT,
    suppressed_at   TEXT,
    first_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(mailgun_domain, email, kind)
);

CREATE INDEX IF NOT EXISTS idx_mailgun_suppressions_domain ON mailgun_suppressions(domain_id, kind);

-- Google Postmaster Tools v2 (real Gmail-reported spam rate + compliance verdicts) --
-- only for domains DMARCTool tracks and that are VERIFIED in Postmaster Tools,
-- matched dynamically each run the same way as Mailgun.
CREATE TABLE IF NOT EXISTS postmaster_stats (
    id                    INTEGER PRIMARY KEY,
    domain_id             INTEGER NOT NULL REFERENCES domains(id),
    postmaster_domain     TEXT NOT NULL,
    checked_at            TEXT NOT NULL DEFAULT (datetime('now')),
    window_days           INTEGER NOT NULL,
    spam_rate             REAL,
    delivery_error_rate   REAL,
    delivery_error_count  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_postmaster_stats_domain ON postmaster_stats(domain_id, checked_at);

CREATE TABLE IF NOT EXISTS postmaster_compliance (
    id                 INTEGER PRIMARY KEY,
    domain_id          INTEGER NOT NULL REFERENCES domains(id),
    postmaster_domain  TEXT NOT NULL,
    checked_at         TEXT NOT NULL DEFAULT (datetime('now')),
    requirement        TEXT NOT NULL,  -- SPF_AND_DKIM|DMARC_ALIGNMENT|DMARC_POLICY|ENCRYPTION|
                                        -- USER_REPORTED_SPAM_RATE|DNS_RECORDS|ONE_CLICK_UNSUBSCRIBE|
                                        -- HONOR_UNSUBSCRIBE|DELIVERABILITY
    status             TEXT NOT NULL,  -- COMPLIANT|NEEDS_WORK|STATE_UNSPECIFIED
    reason             TEXT
);

CREATE INDEX IF NOT EXISTS idx_postmaster_compliance_domain ON postmaster_compliance(domain_id, requirement, checked_at);

-- Our own accumulated daily spam-rate history, built up one poll at a time
-- since Postmaster Tools' API itself only ever returns a rolling window --
-- upserted so recent days can be corrected as Google backfills/revises them.
CREATE TABLE IF NOT EXISTS postmaster_daily_stats (
    id                INTEGER PRIMARY KEY,
    domain_id         INTEGER NOT NULL REFERENCES domains(id),
    postmaster_domain TEXT NOT NULL,
    day               TEXT NOT NULL,  -- YYYY-MM-DD
    spam_rate         REAL,
    UNIQUE(postmaster_domain, day)
);

CREATE INDEX IF NOT EXISTS idx_postmaster_daily_stats_domain ON postmaster_daily_stats(domain_id, day);

-- Amazon SES bounce/complaint/delivery events, consumed from an SQS queue fed by
-- one SNS topic that every per-domain configuration set publishes to. SES has no
-- per-domain read API -- this is the only way to get separated stats/suppressions.
CREATE TABLE IF NOT EXISTS ses_suppressions (
    id                INTEGER PRIMARY KEY,
    domain_id         INTEGER NOT NULL REFERENCES domains(id),
    configuration_set TEXT NOT NULL,
    email             TEXT NOT NULL,
    kind              TEXT NOT NULL,  -- 'bounce'|'complaint'
    bounce_type       TEXT,           -- 'Permanent'|'Transient'|'Undetermined' (bounce only)
    reason            TEXT,
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(configuration_set, email, kind)
);

CREATE INDEX IF NOT EXISTS idx_ses_suppressions_domain ON ses_suppressions(domain_id, kind);

CREATE TABLE IF NOT EXISTS ses_event_counts (
    id                INTEGER PRIMARY KEY,
    domain_id         INTEGER NOT NULL REFERENCES domains(id),
    configuration_set TEXT NOT NULL,
    day               TEXT NOT NULL,  -- YYYY-MM-DD
    delivered         INTEGER NOT NULL DEFAULT 0,
    bounced           INTEGER NOT NULL DEFAULT 0,
    complained        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(configuration_set, day)
);

CREATE INDEX IF NOT EXISTS idx_ses_event_counts_domain ON ses_event_counts(domain_id, day);

-- Tunable thresholds for the recommendation engine
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
