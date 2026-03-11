-- VetLayer Database Initialization Script
-- Runs automatically via docker-entrypoint-initdb.d
-- (Role and database are created by docker-compose env vars)

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ═══════════════════════════════════════════════════════════════════
-- CANDIDATES
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE candidates (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(200)    NOT NULL,
    email           VARCHAR(254),
    phone           VARCHAR(30),
    location        VARCHAR(200),
    resume_filename VARCHAR(500)    NOT NULL,
    resume_raw_text TEXT,
    resume_parsed   JSONB,
    years_experience FLOAT,
    education_level VARCHAR(100),
    "current_role"  VARCHAR(300),
    current_company VARCHAR(300),
    source          VARCHAR(100)    DEFAULT 'upload',
    created_at      TIMESTAMPTZ       DEFAULT NOW(),
    updated_at      TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_candidates_name ON candidates(name);
CREATE INDEX idx_candidates_created ON candidates(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════
-- JOBS
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE jobs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title                   VARCHAR(300)    NOT NULL,
    company                 VARCHAR(300),
    department              VARCHAR(200),
    description             TEXT            NOT NULL,
    required_skills         JSONB,
    preferred_skills        JSONB,
    experience_range        JSONB,
    education_requirements  JSONB,
    location                VARCHAR(200),
    remote_policy           VARCHAR(50),
    created_at              TIMESTAMPTZ       DEFAULT NOW(),
    updated_at              TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_jobs_title ON jobs(title);

-- ═══════════════════════════════════════════════════════════════════
-- SKILLS  (Skill → Evidence → Depth pipeline output)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE skills (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id      UUID            NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    name              VARCHAR(200)    NOT NULL,
    category          VARCHAR(100),
    estimated_depth   INTEGER         DEFAULT 1 CHECK (estimated_depth BETWEEN 1 AND 5),
    depth_confidence  FLOAT           DEFAULT 0.5 CHECK (depth_confidence BETWEEN 0 AND 1),
    depth_reasoning   TEXT,
    last_used_year    INTEGER,
    years_of_use      FLOAT,
    raw_mentions      JSONB,
    created_at        TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_skills_candidate ON skills(candidate_id);
CREATE INDEX idx_skills_name ON skills(name);

-- ═══════════════════════════════════════════════════════════════════
-- SKILL EVIDENCE
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE skill_evidence (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_id        UUID            NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    evidence_type   VARCHAR(50)     NOT NULL,
    description     TEXT            NOT NULL,
    source_text     TEXT,
    strength        FLOAT           DEFAULT 0.5 CHECK (strength BETWEEN 0 AND 1),
    context         JSONB,
    created_at      TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_evidence_skill ON skill_evidence(skill_id);

-- ═══════════════════════════════════════════════════════════════════
-- ANALYSIS RESULTS  (candidate × job evaluation)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE analysis_results (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id        UUID            NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    job_id              UUID            NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    overall_score       FLOAT           DEFAULT 0.0,
    skill_match_score   FLOAT           DEFAULT 0.0,
    experience_score    FLOAT           DEFAULT 0.0,
    education_score     FLOAT           DEFAULT 0.0,
    depth_score         FLOAT           DEFAULT 0.0,
    skill_breakdown     JSONB,
    strengths           JSONB,
    gaps                JSONB,
    summary_text        TEXT,
    recommendation      VARCHAR(50),
    recruiter_override  VARCHAR(50),
    recruiter_notes     TEXT,
    is_overridden       BOOLEAN         DEFAULT FALSE,
    llm_model_used      VARCHAR(100),
    processing_time_ms  INTEGER,
    created_at          TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_analysis_candidate ON analysis_results(candidate_id);
CREATE INDEX idx_analysis_job ON analysis_results(job_id);
CREATE INDEX idx_analysis_score ON analysis_results(overall_score DESC);

-- ═══════════════════════════════════════════════════════════════════
-- RISK FLAGS
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE risk_flags (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    analysis_id     UUID            NOT NULL REFERENCES analysis_results(id) ON DELETE CASCADE,
    flag_type       VARCHAR(100)    NOT NULL,
    severity        VARCHAR(20)     DEFAULT 'medium',
    title           VARCHAR(300)    NOT NULL,
    description     TEXT            NOT NULL,
    evidence        TEXT,
    suggestion      TEXT,
    is_dismissed    BOOLEAN         DEFAULT FALSE,
    dismissed_reason TEXT,
    created_at      TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_flags_analysis ON risk_flags(analysis_id);

-- ═══════════════════════════════════════════════════════════════════
-- INTERVIEW QUESTIONS
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE interview_questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    analysis_id     UUID            NOT NULL REFERENCES analysis_results(id) ON DELETE CASCADE,
    category        VARCHAR(100)    NOT NULL,
    question        TEXT            NOT NULL,
    rationale       TEXT            NOT NULL,
    target_skill    VARCHAR(200),
    expected_depth  INTEGER,
    priority        INTEGER         DEFAULT 5,
    follow_ups      JSONB,
    created_at      TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX idx_questions_analysis ON interview_questions(analysis_id);
CREATE INDEX idx_questions_priority ON interview_questions(priority);

-- ═══════════════════════════════════════════════════════════════════
-- Grant permissions
-- ═══════════════════════════════════════════════════════════════════
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO vetlayer;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO vetlayer;

-- Done
SELECT 'VetLayer database initialized successfully!' AS status;
