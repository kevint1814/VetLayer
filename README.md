# VetLayer

**Recruiter Decision Intelligence System** — AI-powered candidate analysis that goes beyond resume parsing to deliver skill-depth assessments, risk detection, and ranked recommendations.

VetLayer helps recruitment teams make faster, evidence-based hiring decisions by running candidates through a multi-stage intelligence pipeline that evaluates skill depth, identifies risk flags, generates interview questions, and produces exportable PDF briefs.

![Stack](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Stack](https://img.shields.io/badge/React_19-61DAFB?style=flat&logo=react&logoColor=black)
![Stack](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)
![Stack](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white)
![Stack](https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=flat&logo=tailwindcss&logoColor=white)

---

## Features

**Candidate Intelligence Pipeline**
- Resume parsing (PDF & DOCX) with structured data extraction
- Multi-dimensional skill depth analysis — not just keyword matching, but assessed proficiency levels with confidence scores
- Risk flag detection engine identifying gaps, inconsistencies, and concerns
- AI-generated interview questions tailored to each candidate's profile and the target role
- Intelligence profiles synthesized from parsed resume data and analysis results

**Batch Analysis**
- Analyze multiple candidates against a job simultaneously
- Ranked results with overall scores, skill match, depth scores, and recommendation tiers (Strong Yes → Strong No)
- Pool-level insights: aggregate strengths, development areas, and hiring recommendations
- Exportable PDF batch briefs with candidate rankings, deep-dives, and dedicated interview preparation pages

**Job Management**
- Parse job descriptions to extract required skills, experience levels, and qualifications
- Capability engine mapping job requirements to candidate assessments
- Multi-job support for comparing candidate fit across roles

**Security & Administration**
- JWT authentication with refresh tokens
- Role-based access (admin / recruiter)
- Account lockout after failed login attempts
- Audit logging for key operations
- Admin panel for user management
- Force password change on first login

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 19, TypeScript, Vite 6, Tailwind CSS, React Router 7, Axios |
| **Backend** | FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic |
| **Database** | PostgreSQL 16 |
| **LLM** | OpenAI (GPT-4o Mini for dev) or Anthropic (Claude for production) — configurable |
| **PDF** | ReportLab (generation), PyPDF (parsing) |
| **Auth** | python-jose (JWT), passlib + bcrypt |
| **Infra** | Docker Compose, Uvicorn |

---

## Project Structure

```
vetlayer/
├── backend/
│   ├── app/
│   │   ├── api/routes/          # FastAPI route handlers
│   │   │   ├── analysis.py      # Analysis, ranking, batch, export endpoints
│   │   │   ├── auth.py          # Login, refresh, password management
│   │   │   ├── admin.py         # User management (admin only)
│   │   │   ├── candidates.py    # Candidate CRUD and resume upload
│   │   │   ├── jobs.py          # Job CRUD and description parsing
│   │   │   └── health.py        # Health check
│   │   ├── models/              # SQLAlchemy ORM models
│   │   │   ├── analysis.py      # AnalysisResult, RiskFlag, BatchAnalysis, InterviewQuestion
│   │   │   ├── candidate.py     # Candidate
│   │   │   ├── job.py           # Job
│   │   │   ├── user.py          # User
│   │   │   ├── skill.py         # Skill
│   │   │   └── audit_log.py     # AuditLog
│   │   ├── services/            # Business logic
│   │   │   ├── skill_pipeline.py        # Multi-stage skill depth analysis
│   │   │   ├── capability_engine.py     # Job-candidate capability matching
│   │   │   ├── risk_engine.py           # Risk flag detection
│   │   │   ├── interview_generator.py   # AI interview question generation
│   │   │   ├── intelligence_profile.py  # Candidate intelligence profiles
│   │   │   ├── resume_parser.py         # PDF/DOCX resume extraction
│   │   │   ├── job_parser.py            # Job description parsing
│   │   │   ├── batch_runner.py          # Async batch analysis orchestration
│   │   │   ├── pdf_batch_brief.py       # Batch analysis PDF export
│   │   │   ├── pdf_intelligence_brief.py # Individual candidate PDF
│   │   │   └── audit.py                 # Audit logging service
│   │   └── core/                # App configuration
│   │       ├── config.py        # Settings (env-based)
│   │       ├── database.py      # Async engine & session
│   │       └── security.py      # JWT & password hashing
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── pages/               # Route-level page components
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── CandidatesPage.tsx
│   │   │   ├── CandidateDetailPage.tsx
│   │   │   ├── JobsPage.tsx
│   │   │   ├── BatchAnalysisPage.tsx
│   │   │   ├── RankedResultsPage.tsx
│   │   │   ├── AnalysisPage.tsx
│   │   │   ├── AdminPage.tsx
│   │   │   ├── LoginPage.tsx
│   │   │   └── ChangePasswordPage.tsx
│   │   ├── components/          # Reusable UI components
│   │   │   ├── common/          # Shared (ScoreBadge, DepthBar, RecommendationBadge, etc.)
│   │   │   ├── analysis/
│   │   │   ├── candidates/
│   │   │   ├── dashboard/
│   │   │   └── jobs/
│   │   ├── services/api.ts      # Axios API client
│   │   └── types/               # TypeScript interfaces
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── start.sh                     # One-command local startup
└── scripts/
    └── init_db.sql
```

---

## Getting Started

### Prerequisites

- **Node.js** 18+ and npm
- **Python** 3.11+
- **PostgreSQL** 16+
- An **OpenAI** or **Anthropic** API key

### Option 1: Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/your-username/vetlayer.git
cd vetlayer

# Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env — add your API key and set SECRET_KEY

# Start everything
docker compose up --build
```

The app will be available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/api/docs

### Option 2: Local Development

```bash
# 1. Start PostgreSQL (ensure it's running on port 5432)

# 2. Create the database
createdb vetlayer

# 3. Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env with your settings

# 4. Run everything with the startup script
chmod +x start.sh
./start.sh
```

Or start each service manually:

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (in a separate terminal)
cd frontend
npm install
npm run dev
```

### Default Admin Account

On first startup, VetLayer seeds an admin account:
- **Username**: `admin`
- **Password**: `Admin@123`

You'll be prompted to change the password on first login. Change these defaults in your `.env` before deploying.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://vetlayer:vetlayer@localhost:5432/vetlayer` | PostgreSQL connection string |
| `LLM_PROVIDER` | `openai` | LLM backend: `openai` or `anthropic` |
| `OPENAI_API_KEY` | — | Required if using OpenAI |
| `ANTHROPIC_API_KEY` | — | Required if using Anthropic |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | Anthropic model name |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key — **must change for production** |
| `DEBUG` | `false` | Enable debug mode (exposes error details) |
| `ADMIN_USERNAME` | `admin` | Default admin username |
| `ADMIN_PASSWORD` | `Admin@123` | Default admin password — **must change for production** |
| `MAX_UPLOAD_SIZE_MB` | `10` | Maximum resume file size |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Allowed CORS origins |

---

## API Overview

All endpoints are prefixed with `/api`. Full interactive docs available at `/api/docs` when the server is running.

| Endpoint Group | Description |
|----------------|-------------|
| `POST /api/auth/login` | Authenticate and receive JWT tokens |
| `POST /api/auth/refresh` | Refresh an expired access token |
| `GET /api/candidates` | List candidates with pagination |
| `POST /api/candidates` | Create candidate with resume upload (PDF/DOCX) |
| `GET /api/candidates/{id}` | Candidate detail with parsed resume data |
| `GET /api/jobs` | List jobs |
| `POST /api/jobs` | Create job with description parsing |
| `POST /api/analysis/run` | Run single candidate-job analysis |
| `POST /api/analysis/batch` | Launch batch analysis (multiple candidates × jobs) |
| `GET /api/analysis/batch/{id}/status` | Poll batch progress |
| `GET /api/analysis/ranked/{job_id}` | Get candidates ranked for a job |
| `GET /api/analysis/batch/{id}/export` | Export batch brief as PDF |
| `GET /api/admin/users` | List users (admin only) |

---

## Analysis Pipeline

When a candidate is analyzed against a job, VetLayer runs a multi-stage pipeline:

1. **Resume Parsing** — Extracts structured data (skills, experience, education, projects) from uploaded PDF/DOCX files
2. **Job Parsing** — Extracts required skills, experience levels, and qualifications from job descriptions
3. **Skill Depth Analysis** — Evaluates proficiency level for each required skill using LLM-powered assessment with evidence extraction
4. **Capability Matching** — Scores alignment between candidate capabilities and job requirements
5. **Risk Detection** — Identifies red/yellow flags (employment gaps, skill mismatches, inconsistencies)
6. **Interview Generation** — Creates targeted interview questions based on the candidate's profile and identified areas to probe
7. **Intelligence Profile** — Synthesizes all findings into a comprehensive candidate brief

---

## PDF Export

Batch analysis results can be exported as professional PDF briefs containing:

- Executive summary with pool-level metrics
- Candidate ranking table with scores and recommendation tiers
- Individual candidate deep-dives (strengths, gaps, skill breakdown, risk flags)
- Dedicated interview preparation pages with categorized questions and rationale

---

## Development

```bash
# Run backend tests
cd backend
pytest --cov=app

# Type-check frontend
cd frontend
npx tsc --noEmit

# Lint frontend
npm run lint

# Build frontend for production
npm run build
```

---

## License

This project is proprietary. All rights reserved.
