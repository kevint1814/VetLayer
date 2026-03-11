#!/bin/bash
# ─────────────────────────────────────────────────────────────
# VetLayer — One-command startup script
# Usage: ./start.sh
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PG_DATA="/opt/homebrew/var/postgresql@17"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  VetLayer — Starting up...${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# ── 1. Start PostgreSQL if not running ─────────────────────────
echo -e "\n${YELLOW}[1/4]${NC} Checking PostgreSQL..."
if pg_isready -q 2>/dev/null; then
    echo "  PostgreSQL is already running."
else
    echo "  Starting PostgreSQL..."
    pg_ctl -D "$PG_DATA" start -l "$PG_DATA/server.log" -w
    echo "  PostgreSQL started."
fi

# ── 2. Activate Python venv ────────────────────────────────────
echo -e "\n${YELLOW}[2/4]${NC} Activating Python environment..."
if [ ! -d "$BACKEND_DIR/venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$BACKEND_DIR/venv"
    source "$BACKEND_DIR/venv/bin/activate"
    echo "  Installing dependencies..."
    pip install -r "$BACKEND_DIR/requirements.txt" 2>/dev/null || pip install fastapi uvicorn sqlalchemy asyncpg python-multipart pydantic-settings openai httpx python-dotenv pypdf email-validator
else
    source "$BACKEND_DIR/venv/bin/activate"
fi
echo "  Python venv activated."

# ── 3. Start backend ──────────────────────────────────────────
echo -e "\n${YELLOW}[3/4]${NC} Starting backend on http://127.0.0.1:8000 ..."
cd "$BACKEND_DIR"
python -m uvicorn app.main:app --reload --reload-dir app --port 8000 &
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "  Waiting for backend..."
for i in $(seq 1 15); do
    if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
        echo "  Backend is ready."
        break
    fi
    sleep 1
done

# ── 4. Start frontend ─────────────────────────────────────────
echo -e "\n${YELLOW}[4/4]${NC} Starting frontend on http://localhost:5173 ..."
cd "$FRONTEND_DIR"
if [ ! -d "node_modules" ]; then
    echo "  Installing npm dependencies..."
    npm install
fi
npm run dev &
FRONTEND_PID=$!
echo "  Frontend PID: $FRONTEND_PID"

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  VetLayer is running!${NC}"
echo -e "${GREEN}  Frontend: http://localhost:5173${NC}"
echo -e "${GREEN}  Backend:  http://127.0.0.1:8000${NC}"
echo -e "${GREEN}  API Docs: http://127.0.0.1:8000/api/docs${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "Press ${RED}Ctrl+C${NC} to stop everything."

# Trap Ctrl+C to clean up both processes
cleanup() {
    echo ""
    echo -e "\n${YELLOW}Shutting down VetLayer...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID 2>/dev/null
    wait $FRONTEND_PID 2>/dev/null
    echo -e "${GREEN}VetLayer stopped.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for either process to exit
wait
