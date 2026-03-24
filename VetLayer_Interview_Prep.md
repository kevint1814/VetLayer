# VetLayer — Technical Interview Prep Guide

*Comprehensive deep-dive for Kevin — March 23, 2026*

---

## 1. ELEVATOR PITCH (30 seconds)

VetLayer is a **recruiter decision intelligence platform** that replaces gut-feel candidate screening with evidence-based, AI-powered analysis. A recruiter uploads a resume and a job description; VetLayer parses both, classifies the role type, assesses every required skill against resume evidence using LLM-powered depth analysis, computes a weighted composite score, and generates interview questions targeting the candidate's specific gaps. It works across **all professional domains** — tech, finance, HR, operations, consulting — not just software engineering.

**Stack**: FastAPI + PostgreSQL (async) backend, React 19 + TypeScript frontend, Groq (Llama 3.3 70B) primary LLM with OpenAI GPT-4o Mini fallback, reportlab PDF generation, Docker Compose deployment.

---

## 2. ARCHITECTURE OVERVIEW

### 2.1 System Components

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│   React UI   │────▶│  FastAPI Backend (async, company-scoped)         │
│  (Vite/TS)   │◀────│                                                  │
└─────────────┘     │  Routes: auth, candidates, jobs, analysis, admin │
                    │  Services: 20 specialized modules                 │
                    │  Middleware: rate_limit, security_headers          │
                    └───────────┬────────────────────┬─────────────────┘
                                │                    │
                    ┌───────────▼──────┐   ┌────────▼────────┐
                    │  PostgreSQL 16   │   │  LLM Providers   │
                    │  (asyncpg)       │   │  Groq → OpenAI   │
                    └──────────────────┘   └─────────────────┘
```

### 2.2 Data Flow: Single Analysis

1. **Resume Upload** → Two-phase LLM parse (structured extraction + intelligence profile)
2. **Job Creation** → LLM-powered skill extraction from JD text, seniority detection
3. **Analysis Trigger** → Role type detection → Cluster prompt selection → Skill pipeline → Scoring → Risk flags → Interview questions → PDF generation

### 2.3 Data Flow: Batch Analysis

1. Recruiter selects N candidates × M jobs
2. `run_batch_analysis()` returns `batch_id` immediately
3. Background `asyncio.Task` processes all pairs under `Semaphore(8)`
4. Pre-parses job skills once per job (not per pair) — saves 5-15s per extra candidate
5. Deduplicates already-analyzed pairs
6. Real-time progress via in-memory `_batch_store` + DB persistence via `BatchAnalysis` model
7. Results sorted by score descending; final state persisted to DB

---

## 3. THE SCORING ENGINE (This is the core IP)

### 3.1 Formula

```python
overall = (
    skill_match   × 0.35 × w_skill   +
    depth_avg     × 0.22 × w_depth   +
    experience    × 0.18 × w_experience +
    education     × 0.10 × w_education +
    trajectory_bonus        +    # Up to 5%
    soft_skill_bonus        +    # Up to 4%
    preferred_bonus         +    # Up to 5%
    impact_bonus            +    # Up to 3%
    leadership_bonus        +    # Up to 4%
    perfect_match_bonus     -    # 2% if skill_match ≥ 0.95
    experience_penalty           # Up to -15%
)
```

**Base coefficients**: [0.35, 0.22, 0.18, 0.10] = 85% base ceiling.
**Bonus components**: trajectory, soft skills, preferred, impact, leadership, perfect match can add up to ~23%.
**Max theoretical score**: ~100% (but practically capped by evidence quality).

### 3.2 Weight Normalization (Critical Recent Fix)

**The problem**: Role-type multipliers were *reducing* the total weighted sum instead of *redistributing* it. Experience-heavy roles had a structural ceiling of ~71% vs ~91% for skill-heavy roles.

**The fix**: Normalize base-component multipliers so their weighted sum always equals 0.85:

```python
_BASE_COEFFICIENTS = [0.35, 0.22, 0.18, 0.10]  # skill, depth, exp, edu
_raw_mults = [w_skill_raw, w_depth_raw, w_experience_raw, w_education_raw]
_weighted_sum = sum(b * m for b, m in zip(_BASE_COEFFICIENTS, _raw_mults))
_norm_factor = 0.85 / _weighted_sum if _weighted_sum > 0 else 1.0
```

Now all role types target the same 0.85 base ceiling, but the *distribution* within that 85% shifts based on role type.

**Be ready to explain**: "The multipliers redistribute the 85% budget — they don't change the total. A skill-heavy role puts more of the 85% on skill_match and depth; an experience-heavy role puts more on experience and education. But both can reach the same maximum."

### 3.3 Role-Type Adaptive Weights

| Weight | skill_heavy | hybrid | experience_heavy |
|--------|-----------|--------|-----------------|
| skill_match | 1.20 | 1.00 | 0.65 |
| depth | 1.15 | 0.90 | 0.60 |
| experience | 0.85 | 1.10 | 1.30 |
| education | 0.80 | 1.00 | 1.15 |
| trajectory | 0.60 | 1.10 | 1.40 |
| soft_skill_proxy | 0.50 | 0.90 | 1.30 |

### 3.4 Recommendation Thresholds

| Score | Recommendation |
|-------|---------------|
| ≥ 0.75 | strong_yes |
| ≥ 0.60 | yes |
| ≥ 0.40 | maybe |
| ≥ 0.25 | no |
| < 0.25 | strong_no |

**Confidence guard**: If `analysis_confidence < 0.35`, strong recommendations are downgraded one level. If `< 0.25`, forced to "maybe".

### 3.5 Confidence Intervals

```python
interval_half_width = (1 - analysis_confidence) × 0.15
score_low  = max(0, overall - interval_half_width)
score_high = min(1.0, overall + interval_half_width)
```

At 0.9 confidence → ±2% range. At 0.3 confidence → ±12% range. This communicates uncertainty to recruiters.

### 3.6 Individual Score Components

- **skill_match**: Weighted match sum / total weight. Partial credit: if candidate has depth 2 but role requires 3, they get `(2/3) × weight × 0.75` credit (not zero).
- **depth_avg**: Average of `min(effective_depth / required_depth, 1.0) × weight` across required skills. `effective_depth = estimated_depth × recency_factor`.
- **experience_score**: Confidence-weighted average depth across all matched assessments, normalized to 0-1.
- **education_score**: Computed from parsed education data (degrees, relevance).
- **trajectory_bonus**: From `experience_trajectory` module (0-100 raw → normalized × 0.05 × w_trajectory).
- **soft_skill_bonus**: From `soft_skill_detector` module (0-100 raw → normalized × 0.04 × w_soft_skill).
- **impact_bonus**: Count of quantified achievements (metrics, percentages, dollar amounts) × 0.005, capped at 0.03.
- **experience_penalty**: 0.03 per year short of min_years requirement, capped at 0.15.

---

## 4. ROLE TYPE DETECTION

### 4.1 Classification Signals

The `role_type_detector` classifies every job into one of three categories using four signals:

1. **Title patterns** (weight: 0.35) — Regex matching against curated pattern lists
   - skill_heavy: "Software Engineer", "Data Scientist", "DevOps Engineer"
   - experience_heavy: "HR Manager", "CFO", "Finance Controller", "Marketing Director"
   - hybrid: "Engineering Manager", "Product Manager", "Solutions Architect"

2. **Soft skill ratio** (weight: 0.20-0.25) — Proportion of soft skills in requirements
   - ≥60% soft → experience_heavy (+0.25)
   - ≥30% soft → hybrid (+0.20)
   - <30% soft → skill_heavy (+0.20)

3. **Domain profile from skill ontology** (weight: 0.10-0.25) — Uses `compute_domain_profile()` to classify skills by domain without coupling to evidence aliases
   - tech_ratio ≥ 0.5 → skill_heavy (+0.25)
   - professional_ratio ≥ 0.3 → experience_heavy (+0.20)
   - unknown_ratio ≥ 0.5 → experience_heavy (+0.15)

4. **Hard skill count** (weight: 0.10-0.15)
   - ≥6 hard skills → skill_heavy (+0.15)
   - ≥3 → hybrid (+0.10)
   - <3 → experience_heavy (+0.15)

**Confidence** = winner_score / total_scores. Higher confidence means clearer classification.

### 4.2 Design Decision: Why Three Types?

"Two types (tech vs non-tech) is too coarse — an Engineering Manager gets mis-scored. Four+ types adds complexity without proportional accuracy gains. Three types with a hybrid middle ground covers 95% of real-world JDs while keeping the scoring weight tables manageable."

---

## 5. SKILL ASSESSMENT PIPELINE

### 5.1 Cluster Prompts (4 variants)

Instead of one monolithic LLM prompt, VetLayer composes prompts dynamically:

1. **Universal Base** — Depth scale, output format, core rules (shared by all)
2. **Cluster-Specific Section** — Calibration anchors per role type:
   - **tech_ic**: Implied skill rules (React → HTML/CSS/JS ≥3), umbrella term resolution
   - **professional**: Senior title rules (CFO → Financial Reporting ≥3), framework expertise rules
   - **leadership**: Organizational scope as evidence, C-suite implied competencies
   - **hybrid**: Dual assessment (both tech + leadership dimensions)
3. **Domain Overlay** — Proficiency examples from skill_ontology matched to JD domain

**Leadership override**: C-suite titles (CEO, CFO, etc.) always use the leadership cluster regardless of other signals.

### 5.2 SFIA-Inspired Proficiency Scale (0-5)

| Level | Name | Description |
|-------|------|-------------|
| 0 | Not Found | No evidence anywhere on resume |
| 1 | Awareness | Listed/mentioned, no hands-on evidence |
| 2 | Practitioner | Applied in limited/supporting capacity |
| 3 | Professional | 1+ years delivering outcomes independently |
| 4 | Advanced | Led strategy, owned outcomes, mentored others |
| 5 | Expert | Industry-recognized authority, published thought leadership |

Each level has domain-specific behavioral anchors (e.g., Level 3 in finance = "Managed IFRS 9 reporting across multiple legal entities. Owned monthly close.")

### 5.3 Skill Ontology

- **95 skills** across **11 domains** (technology, finance, consulting, operations, HR, marketing, sales, healthcare, legal, leadership, general)
- Graph-based with parent/child/sibling relationships
- Each skill has: canonical ID, display name, domain(s), text variants for matching, contextual phrases, skill_type (hard/soft/tool/certification)
- Replaces the flat `_EVIDENCE_ALIASES` dict for role type classification (decoupled)
- Key functions: `resolve_skill()`, `get_skill_domain()`, `compute_domain_profile()`, `classify_skills_by_domain()`

### 5.4 Dynamic Taxonomy

For skills not in the ontology, VetLayer generates taxonomies on-the-fly via LLM:
1. Check if skill exists in ontology OR in evidence aliases
2. If unknown, call `generate_batch_taxonomies()` with job context
3. Generated taxonomies are cached and used for that analysis

### 5.5 Pipeline Flow

```
JD Skills → Role Type Detection → Cluster Prompt Selection →
LLM Assessment (Groq primary, OpenAI fallback) →
Adjacency Boosts → Recency Weighting → Score Computation
```

---

## 6. SUPPORTING SERVICES

### 6.1 Experience Trajectory (`experience_trajectory.py`)

Scores career progression (0-100) by analyzing:
- **Seniority progression**: Maps titles to numeric levels (intern=1 → CEO=8) with compound title patterns
- **Progression types**: upward, lateral, mixed, stagnant, decline
- **Industry continuity** vs pivots
- Feeds into `trajectory_bonus` in scoring formula

### 6.2 Soft Skill Detector (`soft_skill_detector.py`)

VetLayer rates soft skills at depth 0 (can't assess from resumes). Instead, detects **proxy evidence** using 40+ regex patterns across 5 categories:
- **Leadership**: "managed team of 15", "grew team from 5 to 20"
- **Communication**: "presented to C-suite", "authored whitepaper"
- **Problem-solving**: "reduced churn by 30%", "saved $2M"
- **Collaboration**: "cross-functional initiative", "mentored 5 juniors"
- **Strategic thinking**: "developed roadmap", "P&L responsibility"

Each match has an evidence strength (0.55-0.90). Output: `soft_skill_score` (0-100) + per-category breakdown.

### 6.3 Domain Fit (`domain_fit.py`)

Evaluates industry match between JD and candidate:
- **13 domain keyword taxonomies**: banking, insurance, asset_management, healthcare, technology, retail, manufacturing, consulting, telecommunications, energy, government, education, real_estate
- **Adjacency map**: banking ↔ insurance ↔ asset_management ↔ consulting; consulting is adjacent to everything
- **Domain-critical skills**: banking requires IFRS 9, product control, etc.
- **Match types**: in_domain (base 90), adjacent (base 60), out_of_domain (base 30), domain_agnostic (80)
- **Gap penalty**: -8 per missing domain-critical skill, capped at -30

**Recent fix**: `_build_candidate_text()` crashed on None values in parsed resume fields. Fixed with `" ".join(p for p in parts if p)`.

### 6.4 Intelligence Profile (`intelligence_profile.py`)

LLM-generated narrative brief at resume upload time. Produces 12 fields:
- executive_summary, seniority_level, career_narrative, strengths, considerations
- skill_narrative, skill_categories, culture_signals, ideal_roles, ideal_roles_narrative
- career_timeline_briefs (per-role analyst summaries), talking_points

Domain-aware: "For finance professionals, highlight regulatory knowledge, controllership scope. For tech, highlight architecture decisions and system scale."

### 6.5 Resume Parser (Two-Phase)

1. **Phase 1**: LLM extracts structured data (name, contact, experience[], education[], skills[], certifications[])
2. **Phase 2**: Intelligence profile generation (narrative assessments)

### 6.6 Job Parser (`job_parser.py`)

- `parse_job_requirements()`: LLM-powered extraction of required/preferred skills with min_depth and weight
- `detect_seniority()`: Title + description analysis for seniority level
- `apply_seniority_boost()`: Raises min_depth floors for senior roles

### 6.7 Interview Generator

Generates targeted interview questions based on:
- Skill gaps → depth_probe questions
- Low confidence assessments → skill_verification questions
- Risk flags → red_flag questions
- Role-type-specific behavioral questions
- Categories include: depth_probe, gap_exploration, red_flag, skill_verification, behavioral, domain_specific, leadership, trajectory

### 6.8 Risk Engine

Generates risk flags from analysis results:
- Experience shortfall
- Missing critical skills
- Domain fit gaps
- Stagnant career trajectory
- Low overall confidence

---

## 7. LLM ARCHITECTURE

### 7.1 Multi-Provider Client (`llm_client.py`)

```python
class LLMClient:
    # Provider priority:
    # 1. Primary (LLM_PROVIDER) — Groq (Llama 3.3 70B)
    # 2. Fallback (LLM_FALLBACK_PROVIDER) — OpenAI (GPT-4o Mini)
```

- **Groq**: Llama 3.3 70B via Groq LPU inference. Fast, strong instruction-following. Rate-limit retry with exponential backoff (2s, 4s).
- **OpenAI**: GPT-4o Mini. Reliable JSON mode. Automatic fallback.
- **Anthropic**: Claude Sonnet. Available but not default.

### 7.2 JSON Repair

LLM responses can get truncated at token limits. The client has a two-strategy repair:
1. Close unterminated strings, arrays, objects
2. Binary search from end to find valid JSON prefix (trim up to 500 chars)

### 7.3 Fallback Logic

Three trigger conditions for fallback:
1. Primary provider throws exception (network, rate limit after retries)
2. Primary returns empty/null response
3. Primary returns unparseable JSON even after repair attempt

---

## 8. DATABASE & MODELS

### 8.1 Core Tables

| Model | Key Fields |
|-------|-----------|
| **User** | id, username, email, role (super_admin/company_admin/recruiter), company_id, is_active, failed_login_count, locked_until |
| **Company** | id, name, domain, settings |
| **Candidate** | id, name, email, resume_raw, resume_parsed (JSONB), intelligence_profile (JSONB), company_id |
| **Job** | id, title, description, required_skills (JSONB), preferred_skills (JSONB), experience_range (JSONB), company_id |
| **AnalysisResult** | id, candidate_id, job_id, overall_score, skill_match_score, depth_score, experience_score, education_score, skill_breakdown (JSONB), strengths, gaps, recommendation, processing_time_ms, company_id |
| **Skill** | id, candidate_id, name, category, estimated_depth, depth_confidence, depth_reasoning, last_used_year, years_of_use, raw_mentions (JSONB), company_id |
| **SkillEvidence** | id, skill_id, evidence_type, description, source_text, strength |
| **RiskFlag** | id, analysis_id, flag_type, severity, title, description, evidence, suggestion |
| **InterviewQuestion** | id, analysis_id, category, question, rationale, target_skill, expected_depth, priority |
| **BatchAnalysis** | batch_id, company_id, candidate_ids, job_ids, status, total, completed, failed, cached, elapsed_ms, results (JSONB), avg_score, top_recommendation |
| **AuditLog** | id, user_id, action, entity_type, entity_id, details, ip_address |

### 8.2 Multi-Tenancy

- `company_id` on all data tables (Candidate, Job, Analysis, Skill, BatchAnalysis)
- Enforced in auth middleware via `require_company()` and `get_user_company_id()`
- `super_admin` sees all companies; `company_admin` and `recruiter` see only their company's data

### 8.3 Migrations

Alembic with 3 migration files:
- 003: Add batch analyses table
- 004: Add processing_status fields
- 005: Add multi-tenancy (company_id columns + foreign keys)

---

## 9. SECURITY

### 9.1 Authentication

- **JWT tokens**: HS256, access (30 min) + refresh (7 days)
- **Password**: bcrypt hashing via passlib
- **Password policy**: NIST-aligned (8+ chars, upper, lower, digit, special)
- **Account lockout**: 5 failed attempts → 15 min lockout
- **Force password change**: New users must change default password on first login

### 9.2 Authorization

Three roles: `super_admin`, `company_admin`, `recruiter`
- super_admin: cross-company access, user management
- company_admin: company-scoped admin operations
- recruiter: standard analysis operations within company

### 9.3 Production Safety

- `SECRET_KEY` and `ADMIN_PASSWORD` defaults **block startup** in production mode (DEBUG=false)
- Startup validation prevents deploying with insecure defaults

### 9.4 Middleware

- **Rate limiting**: Per-IP request throttling
- **Security headers**: Standard HTTP security headers (HSTS, X-Frame-Options, etc.)
- **CORS**: Configured for localhost:5173 and localhost:3000

---

## 10. FRONTEND

### 10.1 Tech Stack

React 19 + TypeScript + Vite 6 + Tailwind CSS 3.4 + Lucide React icons

### 10.2 Pages (11 routes)

| Route | Page | Purpose |
|-------|------|---------|
| / | DashboardPage | Overview metrics and recent activity |
| /candidates | CandidatesPage | Candidate list with bulk actions |
| /candidates/:id | CandidateDetailPage | Resume, intelligence profile, analysis history |
| /jobs | JobsPage | Job listings management |
| /analysis/:id | AnalysisPage | Full analysis result with scores, skills, risks, questions |
| /batch | BatchAnalysisPage | Multi-candidate batch processing with progress tracking |
| /ranked/:jobId | RankedResultsPage | Stack-ranked candidates for a job |
| /settings | SettingsPage | User preferences |
| /admin | AdminPage | User/company management (admin only) |
| /login | LoginPage | Authentication |
| /change-password | ChangePasswordPage | Forced password change |

### 10.3 Key Frontend Features

- **AuthContext**: JWT management, auto-refresh, force password change detection
- **Uncertain Skills Display**: Skills where LLM gave high depth but low confidence — shown with amber badges and "verify in interview" flags
- **Confidence Intervals**: Score range visualization based on analysis confidence
- **Bulk Actions**: Multi-select candidates for batch operations via `useMultiSelect` hook
- **Glass-card design system**: Tailwind-based with custom CSS classes (glass-card, glass-card-solid)

### 10.4 API Client (`services/api.ts`)

Axios-based with JWT interceptor for automatic token attachment and 401 handling.

---

## 11. PDF GENERATION

### 11.1 Two PDF Types

1. **Intelligence Brief** (`pdf_intelligence_brief.py`) — Single candidate analysis
2. **Batch Analysis Brief** (`pdf_batch_brief.py`) — Multi-candidate comparison

### 11.2 Design

- Luxury monochrome palette: `COLOR_BLACK = HexColor("#1a1a2e")`, `COLOR_ACCENT = HexColor("#6366f1")`
- Letter size: 612 × 792 pts, MARGIN=56, COL_W=235
- reportlab with `Paragraph`, `Table`, `Frame`, `PageTemplate`

### 11.3 Recent Fixes

- **Text truncation**: `wordWrap="CJK"` on paragraph styles prevents clipping in 235pt columns
- **Company casing**: `_fix_company_casing()` with regex word-boundary matching for PwC, KPMG, EY, IBM, etc.
- **Sanitize artifacts**: `_sanitize()` handles spaced emdashes ("Director – AC" → "Director, Acceleration Centers") without producing "Director , AC"
- **Grammar**: Singular/plural handling ("1 CANDIDATE EVALUATED" vs "15 CANDIDATES EVALUATED")
- **Category labels**: `_format_category()` maps raw enums ("gap_exploration") to human-readable ("GAP EXPLORATION")
- **Confidence intervals**: Displayed with colored confidence level and score range

---

## 12. TESTING

### 12.1 Test Suite

34 tests across 11 test files:
- `test_benchmark_roles.py` — Scoring normalization, weight distribution, role type boundaries
- `test_role_type_detector.py` — Classification accuracy across role types
- `test_domain_fit.py` — Domain detection, adjacency, critical skills
- `test_soft_skill_detector.py` — Proxy pattern matching
- `test_experience_trajectory.py` — Seniority mapping, progression scoring
- `test_implied_skills.py` — Framework → foundation skill inference
- `test_security.py` — Password validation, JWT creation/decoding, lockout
- `test_ats_integration.py` — ATS webhook payload handling
- `test_health.py` — Health check endpoint
- `test_date_utils.py` — Date parsing utilities
- `test_resume_postprocess.py` — Resume cleanup and normalization

### 12.2 CI/CD

GitHub Actions pipeline (`ci.yml`):
- Python test suite with pytest
- Linting / type checking
- Frontend build verification

---

## 13. ATS INTEGRATION

Webhook-based integration with 4 ATS platforms:
- **Greenhouse** — Candidate and application webhooks
- **Lever** — Opportunity stage change hooks
- **Ashby** — Application webhooks
- **Workday** — Candidate import

Receives candidate data → Creates Candidate record → Triggers auto-analysis if job is mapped.

---

## 14. BATCH RUNNER DEEP DIVE

### 14.1 Concurrency Model

```python
MAX_CONCURRENCY = 8  # asyncio.Semaphore — limits concurrent LLM calls
MAX_STORED_BATCHES = 50  # In-memory cap with LRU eviction of completed batches
```

### 14.2 Optimizations

1. **Shared job pre-parsing**: Parse job skills once per job, not per (candidate, job) pair
2. **Existing analysis dedup**: Skip pairs already analyzed (unless force_reanalyze=True)
3. **Pre-fetch all entities**: Single DB query for all candidates and jobs before processing
4. **Background task with error callback**: `asyncio.create_task()` + `add_done_callback()` for unhandled error logging

### 14.3 Status States

`processing` → `completed` | `partial_failure` | `failed`

Persisted to both in-memory store (for live polling) and DB (for history).

---

## 15. LIKELY INTERVIEW QUESTIONS & ANSWERS

### Architecture

**Q: Why FastAPI over Django/Flask?**
A: Async-first (critical for concurrent LLM calls), automatic OpenAPI docs, Pydantic validation built-in, excellent performance for I/O-bound workloads. Django's ORM is sync-first which would require hacky workarounds for our concurrent pipeline.

**Q: Why PostgreSQL with asyncpg?**
A: JSONB support is essential — skill_breakdown, resume_parsed, intelligence_profile are all stored as JSONB for flexible schema within structured tables. asyncpg gives true async I/O, not thread-pool faking.

**Q: How do you handle LLM rate limits?**
A: Three layers: (1) Semaphore(8) caps concurrent calls, (2) Exponential backoff retry (2s, 4s) for Groq rate limits, (3) Automatic provider fallback — if Groq fails after retries, transparently switches to OpenAI.

### Scoring

**Q: Why not just use the LLM to score candidates directly?**
A: LLM scores are non-deterministic, non-comparable across runs, and can't be explained to recruiters. Our approach: LLM does the *evidence extraction* (what it's good at), then a deterministic formula computes the score (explainable, reproducible, auditable). The formula's weights are transparent and adjustable.

**Q: How do you prevent bias in scoring across role types?**
A: Weight normalization. The raw multipliers from role_type_detector would create different scoring ceilings per role type (71% for experience-heavy vs 91% for skill-heavy). We normalize so all role types share the same 0.85 base ceiling, then redistribute *within* that budget based on what matters for each role type.

**Q: How do you handle skills outside your ontology?**
A: Three-layer fallback: (1) Check skill_ontology (95 skills, 11 domains), (2) Check _EVIDENCE_ALIASES for tech-specific variants, (3) Generate dynamic taxonomy via LLM with job context. Unknown skills still get assessed — they just use generic calibration instead of cluster-specific anchors.

### Data

**Q: How do you handle multi-tenancy?**
A: `company_id` foreign key on all data tables. Enforced at the route level: `require_company(user)` extracts company_id from JWT, all queries filter by it. Super_admin bypasses the filter for cross-company operations. No row-level security in Postgres yet — enforced in application layer.

**Q: What happens if the LLM returns invalid JSON?**
A: Two-strategy repair: (1) Close unterminated strings/arrays/objects, (2) Binary search trim from end to find valid JSON prefix. If repair fails and fallback is enabled, transparently retry with the fallback provider. If everything fails, the analysis fails gracefully with an error message — never a silent wrong result.

### Scale

**Q: How does batch analysis scale?**
A: Semaphore(8) bounds memory/rate-limit exposure. Job skills are pre-parsed once per unique job. Existing analyses are skipped via dedup. In-memory store is capped at 50 batches with LRU eviction. For true scale, you'd move to a task queue (Celery/Dramatiq) with Redis broker — current design is optimized for single-server deployment.

**Q: What's the bottleneck?**
A: LLM inference time. Each skill assessment takes 2-8 seconds depending on provider and resume complexity. Batch of 15 candidates × 1 job with 8 concurrency ≈ 45-90 seconds total. The semaphore prevents overwhelming the LLM API, and pre-parsed job skills eliminate redundant calls.

### Domain Expertise

**Q: How do you score a finance professional differently from a software engineer?**
A: Role type detector classifies the JD into skill_heavy/hybrid/experience_heavy using title patterns, soft skill ratio, and domain profile from the skill ontology. Each type gets different weight multipliers — skill_heavy emphasizes depth (1.15×) and skill_match (1.20×); experience_heavy emphasizes trajectory (1.40×) and soft_skill_proxy (1.30×). The cluster prompt also changes — finance roles get professional-domain calibration rules ("CFO + IFRS = minimum depth 3") instead of tech rules ("React implies HTML ≥3").

**Q: How does the skill ontology differ from a simple skills list?**
A: It's a graph with relationships. Each SkillNode has: canonical ID, display name, primary + secondary domains, text variants for evidence matching, parent/child/sibling relationships, contextual phrases that imply the skill, and skill_type (hard/soft/tool/cert). This lets us do things like "IFRS is a sibling of GAAP" (related but distinct), "React is a child of JavaScript" (implies the parent), and "seeing 'Django' in a resume implies Python presence" (contextual inference).

---

## 16. KNOWN LIMITATIONS & FUTURE WORK

1. **Row-level security**: Currently app-layer only. Postgres RLS would add defense-in-depth for multi-tenancy.
2. **Task queue**: Batch runner uses asyncio tasks. For production scale (100+ concurrent batches), needs Celery/Dramatiq with Redis.
3. **Caching**: LLM responses aren't cached across analyses. Adding a content-addressed cache (hash of prompt + resume) would reduce redundant calls.
4. **Real-time updates**: Batch progress polling is pull-based. WebSocket would give true real-time updates.
5. **Skill ontology coverage**: 95 skills is good but not exhaustive. Dynamic taxonomy generation covers the gap but is slower than pre-built ontology lookups.

---

## 17. KEY NUMBERS TO REMEMBER

- **95** skills in the ontology across **11** domains
- **0-5** depth scale (SFIA-inspired)
- **0.85** base scoring ceiling (35% + 22% + 18% + 10%)
- **8** max concurrent LLM calls (semaphore)
- **50** max stored batch states (in-memory)
- **4** cluster prompt variants (tech_ic, professional, leadership, hybrid)
- **13** industry domains in domain_fit
- **40+** soft skill proxy regex patterns across 5 categories
- **34** tests across 11 test files
- **3** LLM providers (Groq primary, OpenAI fallback, Anthropic available)
- **5** recommendation tiers (strong_yes → strong_no)
- **4** ATS integrations (Greenhouse, Lever, Ashby, Workday)

---

## 18. CONFIGURATION QUICK REFERENCE

```
LLM_PROVIDER=groq               # Primary provider
GROQ_MODEL=llama-3.3-70b-versatile
OPENAI_MODEL=gpt-4o-mini        # Fallback
LLM_FALLBACK_ENABLED=true
LLM_MAX_TOKENS=8000
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_ALGORITHM=HS256
MAX_FAILED_LOGIN_ATTEMPTS=5
LOCKOUT_DURATION_MINUTES=15
DATABASE_URL=postgresql+asyncpg://...
```

---

*Good luck Kevin — you've built something seriously impressive. Own it.*
