# VetLayer Dependency Audit Report
**Date:** March 22, 2026
**Scope:** Complete dependency audit of backend (Python) and frontend (Node.js)

---

## Executive Summary

The VetLayer project has **significant dependency issues** that require immediate attention:

### Critical Issues:
1. **6 required packages are NOT installed** (Groq, OpenAI, Anthropic, Alembic, Uvicorn, Pytest-Cov)
2. **Version mismatches** on critical packages (FastAPI, Asyncpg)
3. **1 high-severity security vulnerability** in frontend (flatted package)
4. **129+ unexpected packages** installed beyond requirements
5. **Root-level package.json** has undeclared docx dependency

---

## BACKEND AUDIT (Python)

### 1. Requirements File Analysis
**Location:** `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/requirements.txt`

**Total declared dependencies:** 20 packages across 5 categories:
- Core Framework (FastAPI, Uvicorn, Pydantic): 3 packages
- Database (SQLAlchemy, AsyncPG, Alembic): 3 packages
- LLM Integrations (Groq, OpenAI, Anthropic): 3 packages
- File Processing (PyPDF, Python-Docx, Multipart): 3 packages
- Authentication & Utilities: 5 packages
- Testing: 3 packages

---

### 2. Critical Missing Packages

**Status:** 6 out of 20 required packages are NOT installed

| Package | Required Version | Status | Impact |
|---------|------------------|--------|--------|
| **groq** | >=0.12.0 | MISSING | LLM integration broken |
| **openai** | 1.58.1 | MISSING | LLM integration broken |
| **anthropic** | 0.42.0 | MISSING | LLM integration broken |
| **alembic** | 1.14.1 | MISSING | Database migrations unavailable |
| **uvicorn** | 0.34.0 | MISSING | Web server cannot start |
| **pytest-cov** | 6.0.0 | MISSING | Code coverage testing unavailable |

**Severity:** CRITICAL - The application cannot run without Uvicorn and LLM packages.

**Backend Imports of Missing Packages:**
```
382 occurrences of python-docx imports
LLM client usage in: /sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/utils/llm_client.py
```

---

### 3. Version Mismatches (Installed vs Required)

| Package | Required | Installed | Delta | Status |
|---------|----------|-----------|-------|--------|
| **fastapi** | 0.115.6 | 0.135.1 | +19 minor | MISMATCH - Newer version |
| **asyncpg** | 0.30.0 | 0.31.0 | +1 minor | MINOR MISMATCH |
| **pydantic** | 2.10.4 | 2.12.5 | +2 minor | Acceptable (patch/minor) |
| **httpx** | 0.28.1 | 0.28.1 | Match | OK |
| **passlib** | 1.7.4 | 1.7.4 | Match | OK |
| **pytest** | 8.3.4 | 9.0.2 | +1 minor | MISMATCH - Newer version |
| **pytest-asyncio** | 0.25.0 | 1.3.0 | +1 major | MISMATCH - Major version |

**Severity:** HIGH - FastAPI 0.135.1 is significantly newer than specified 0.115.6. This could introduce breaking changes.

---

### 4. Unexpected/Extra Packages Installed

**Total extra packages:** 129+ packages not in requirements.txt

**Sample of unexpected packages:**
- beautifulsoup4 (4.14.3)
- camelot-py (1.0.9)
- cryptography (46.0.5)
- matplotlib (3.10.8)
- numpy (2.2.6)
- opencv-python (4.13.0.92)
- pandas (2.3.3)
- pdf2image (1.17.0)
- pdfplumber (0.11.9)
- pillow (12.1.1)
- rich (vendored in pip)
- tabula-py (2.10.0)
- Various system packages (cloud-init, ufw, unattended-upgrades)

**Analysis:**
These packages suggest either:
1. System-level dependencies installed outside requirements.txt
2. Unused dependencies left from previous development
3. Indirect dependencies not pinned in requirements.txt

**Recommendation:** Create a clean virtual environment and reinstall only from requirements.txt to validate.

---

### 5. Backend Import Verification

**Files Analyzed:** 20+ Python modules across backend

**Declared Imports Successfully Resolved:**
- ✓ FastAPI ecosystem (fastapi, starlette, uvicorn components)
- ✓ Database layer (sqlalchemy, asyncpg submodules)
- ✓ Pydantic (pydantic, pydantic-settings, pydantic-core)
- ✓ File processing (pypdf, python-docx - 382 occurrences)
- ✓ Authentication (python-jose, passlib, bcrypt, cryptography)
- ✓ Testing (pytest, pytest-asyncio)

**Problematic Imports (Not Installed):**
- ✗ groq (required by llm_client.py)
- ✗ openai (required by llm_client.py)
- ✗ anthropic (required by llm_client.py)
- ✗ alembic (required for migration system)

---

## FRONTEND AUDIT (Node.js)

### 1. Package.json Analysis
**Location:** `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/frontend/package.json`

**Dependencies (Production):**
```json
{
  "react": "^19.0.0",
  "react-dom": "^19.0.0",
  "react-router-dom": "^7.1.0",
  "axios": "^1.7.9",
  "lucide-react": "^0.468.0",
  "clsx": "^2.1.1"
}
```

**Dev Dependencies:**
```json
{
  "@types/react": "^19.0.0",
  "@types/react-dom": "^19.0.0",
  "@vitejs/plugin-react": "^4.3.4",
  "autoprefixer": "^10.4.20",
  "postcss": "^8.4.49",
  "tailwindcss": "^3.4.17",
  "typescript": "^5.7.0",
  "vite": "^6.0.0",
  "eslint": "^9.17.0"
}
```

**Total Dependencies:** 6 production + 9 dev = 15 declared packages

---

### 2. Node Modules Status
**Location:** `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/frontend/node_modules/`

**Status:** ✓ EXISTS and is populated
- **Total modules:** 200+ directories (including dependencies and transitive deps)
- **Size:** Well-established with lock file

---

### 3. Security Audit Results

**Command Executed:** `npm audit`

**SECURITY VULNERABILITY FOUND:**

| Package | Severity | Issue | Advisory |
|---------|----------|-------|----------|
| **flatted** | HIGH | Prototype Pollution via parse() in NodeJS | GHSA-rf6f-7fwh-wjgh |
| **Version:** | <= 3.4.1 | Fix available | `npm audit fix` |

**Impact:** The flatted package is a transitive dependency used for serialization. Prototype pollution could allow attackers to modify object properties.

**Remediation:** Run `npm audit fix` to upgrade to patched version.

---

### 4. TypeScript Import Verification

**Files Analyzed:** 20+ TypeScript/TSX files excluding node_modules

**Declared Imports:**
```
- react (^19.0.0) ✓
- react-dom (^19.0.0) ✓
- react-router-dom (^7.1.0) ✓
- axios (^1.7.9) ✓
- lucide-react (^0.468.0) ✓
- clsx (^2.1.1) ✓
- @types/react (^19.0.0) ✓
- @types/react-dom (^19.0.0) ✓
```

**Path Imports (Internal):**
- @/ alias imports (configured in vite)
- Relative imports (./components, ../services, etc.)

**Status:** All declared imports are present in node_modules. No missing packages detected.

---

### 5. Root-Level Package.json

**Location:** `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/package.json`

```json
{
  "dependencies": {
    "docx": "^9.6.1"
  }
}
```

**Issue:**
- This is unusual to have Node.js docx dependency at root level
- Backend uses python-docx (which is correct)
- Unclear if this is used or stale dependency
- Root-level node_modules exists with this package

**Recommendation:** Verify if this root docx is actually used. If only for doc generation, it should live in backend requirements as python-docx.

---

## Dependency Tree Summary

### Backend Dependencies Hierarchy (Critical Path):

```
fastapi (0.135.1) [MISMATCH: 0.115.6 required]
├── starlette (0.52.1)
├── pydantic (2.12.5)
│   └── pydantic-core (2.41.5)
└── uvicorn [MISSING: 0.34.0]

sqlalchemy (2.0.48) [MISMATCH: 2.0.36 required]
├── asyncpg (0.31.0) [MISMATCH: 0.30.0 required]
└── alembic [MISSING: 1.14.1]

LLM Providers [ALL MISSING]:
├── groq [MISSING]
├── openai [MISSING]
└── anthropic [MISSING]

File Processing:
├── pypdf (3.17.4) ✓
├── python-docx (1.2.0) [MISMATCH: 1.1.2 required]
└── reportlab (4.4.10) ✓

Testing [PARTIAL]:
├── pytest (9.0.2) [MISMATCH: 8.3.4 required]
├── pytest-asyncio (1.3.0) [MISMATCH: 0.25.0 required]
└── pytest-cov [MISSING: 6.0.0]
```

### Frontend Dependencies Tree:

```
vite (6.0.0) ✓
├── esbuild (vendored)
└── @vitejs/plugin-react (4.3.4) ✓

react (19.0.0) ✓
├── react-dom (19.0.0) ✓
├── react-router-dom (7.1.0) ✓
└── axios (1.7.9) ✓

UI/Styling:
├── tailwindcss (3.4.17) ✓
├── autoprefixer (10.4.20) ✓
├── postcss (8.4.49) ✓
└── lucide-react (0.468.0) ✓

Utilities:
├── clsx (2.1.1) ✓
└── flatted (<=3.4.1) [SECURITY: HIGH - prototype pollution]

Type Checking:
├── typescript (5.7.0) ✓
├── @types/react (19.0.0) ✓
└── @types/react-dom (19.0.0) ✓

Linting:
└── eslint (9.17.0) ✓
```

---

## Detailed Findings

### Issue 1: Missing LLM Provider Packages (CRITICAL)

**Packages:** groq, openai, anthropic

**Impact:**
- Backend cannot initialize LLM integrations
- `/app/utils/llm_client.py` will fail on import
- All analysis endpoints depending on LLM calls will crash

**Location of Usage:**
```
/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/utils/llm_client.py
/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/services/ (multiple analysis services)
```

**Fix:**
```bash
pip install groq>=0.12.0 openai==1.58.1 anthropic==0.42.0
```

---

### Issue 2: Missing Uvicorn Server (CRITICAL)

**Package:** uvicorn[standard]==0.34.0

**Impact:**
- Application cannot start as web server
- FastAPI application cannot be served
- Error will occur immediately on startup

**Fix:**
```bash
pip install "uvicorn[standard]==0.34.0"
```

---

### Issue 3: Missing Database Migration Tool (HIGH)

**Package:** alembic==1.14.1

**Impact:**
- Database schema migrations cannot be applied
- Development and production deployments may fail
- Schema management commands won't work

**Fix:**
```bash
pip install alembic==1.14.1
```

---

### Issue 4: FastAPI Version Mismatch (HIGH)

**Details:**
- Required: 0.115.6
- Installed: 0.135.1
- Difference: 20 minor versions newer

**Risk:**
- Breaking API changes between versions
- Starlette version mismatch (0.52.1 installed)
- May break request handling or OpenAPI schema generation

**Recommendation:**
```bash
pip install fastapi==0.115.6
```

---

### Issue 5: Test Coverage Package Missing (MEDIUM)

**Package:** pytest-cov==6.0.0

**Impact:**
- Coverage reporting unavailable
- CI/CD pipelines expecting coverage metrics will fail
- Test suite can still run but coverage cannot be measured

**Fix:**
```bash
pip install pytest-cov==6.0.0
```

---

### Issue 6: Frontend Security Vulnerability (HIGH)

**Package:** flatted <= 3.4.1

**CVE:** GHSA-rf6f-7fwh-wjgh (Prototype Pollution)

**Description:** The parse() function in flatted has a prototype pollution vulnerability that could allow attackers to pollute object prototypes and modify behavior at runtime.

**Fix:**
```bash
cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer/frontend
npm audit fix
```

This will upgrade flatted to a patched version automatically.

---

### Issue 7: 129+ Unexpected Packages Installed

**Root Cause Unknown** - Possible causes:
1. System-wide Python installation with accumulated dependencies
2. Previous development environments not cleaned
3. Hidden requirements not captured in requirements.txt
4. Indirect dependencies from multiple package sources

**Notable unexpected packages:**
- Data Science: numpy, pandas, matplotlib, seaborn
- PDF Tools: pdfplumber, pdf2image, camelot-py, tabula-py
- Vision: opencv-python, pillow, imageio, magika
- System utilities: cloud-init, psutil, distro, netifaces

**Recommendation:**
Create a clean virtual environment:
```bash
cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend
python3 -m venv venv_clean
source venv_clean/bin/activate
pip install -r requirements.txt
```

Then compare the installed packages to identify which ones are truly needed.

---

### Issue 8: Python-Docx Version Mismatch (LOW)

**Details:**
- Required: 1.1.2
- Installed: 1.2.0
- Difference: 0.1.0 patch version

**Analysis:** This is a minor version bump (patch level). Likely safe as 1.2.0 should be backward compatible with 1.1.2.

**Status:** Not critical but consider pinning exact version if strict compatibility is needed.

---

### Issue 9: Async Test Runner Version Mismatch (MEDIUM)

**Details:**
- Required: pytest-asyncio 0.25.0
- Installed: pytest-asyncio 1.3.0
- Difference: Major version bump (1.3.0)

**Impact:** API changes in async test fixtures and decorators. Tests may fail.

**Fix:**
```bash
pip install pytest-asyncio==0.25.0
```

---

## Backend Files with Critical Import Paths

Files that will fail due to missing packages:

1. `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/utils/llm_client.py`
   - Requires: groq, openai, anthropic

2. `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/main.py`
   - Requires: uvicorn (for server startup)

3. `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/alembic/` (directory)
   - Requires: alembic

4. All services under `/sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend/app/services/`
   - Depend on working llm_client.py

---

## Remediation Priority

### Priority 1 - IMMEDIATE (Cannot run application)
1. Install uvicorn[standard]==0.34.0
2. Install groq>=0.12.0
3. Install openai==1.58.1
4. Install anthropic==0.42.0

### Priority 2 - HIGH (Breaking features)
1. Downgrade fastapi to 0.115.6 (or verify 0.135.1 compatibility)
2. Install alembic==1.14.1
3. Fix frontend security vulnerability (npm audit fix)
4. Install pytest-cov==6.0.0

### Priority 3 - MEDIUM (Quality/Testing)
1. Downgrade pytest-asyncio to 0.25.0
2. Verify test suite passes with exact versions

### Priority 4 - LOW (Cleanup)
1. Identify and remove 129+ unnecessary packages
2. Create clean virtual environment
3. Document actual vs declared dependencies

---

## Quick Fix Commands

### Backend - Install Missing Packages:
```bash
cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer
pip install groq>=0.12.0 openai==1.58.1 anthropic==0.42.0
pip install "uvicorn[standard]==0.34.0"
pip install alembic==1.14.1
pip install pytest-cov==6.0.0
```

### Backend - Downgrade Mismatched Versions:
```bash
pip install fastapi==0.115.6
pip install pytest-asyncio==0.25.0
```

### Frontend - Fix Security Vulnerability:
```bash
cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer/frontend
npm audit fix
npm audit  # Verify fix
```

---

## Verification Steps

After applying fixes, verify:

1. **Backend startup:**
   ```bash
   cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer/backend
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

2. **LLM imports:**
   ```bash
   python -c "from app.utils.llm_client import LLMClient; print('OK')"
   ```

3. **Database migrations:**
   ```bash
   alembic current
   alembic upgrade head
   ```

4. **Test suite:**
   ```bash
   pytest --cov=app tests/
   ```

5. **Frontend build:**
   ```bash
   cd /sessions/upbeat-serene-ptolemy/mnt/vetlayer/frontend
   npm run build
   npm audit
   ```

---

## Conclusion

The VetLayer project has critical dependency issues that will prevent it from running:
- **6 required packages missing**
- **1 high-severity security vulnerability**
- **Multiple version mismatches** on core packages
- **Bloated environment** with 129+ unexpected packages

**Estimated fix time:** 30-45 minutes

**Risk level:** CRITICAL - Application cannot start in current state

All issues are fixable with the commands provided above. A clean virtual environment installation is recommended for long-term maintainability.
