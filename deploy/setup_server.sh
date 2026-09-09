#!/usr/bin/env bash
#
# 원격 서버(Ubuntu Linux 가정) 최초 셋업 스크립트
# regulation-rag 디렉터리 루트에서 실행:
#   bash deploy/setup_server.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

DB_PASSWORD="${DB_PASSWORD:-ragpassword}"
DB_NAME="${DB_NAME:-rag}"

echo "=== 1. Python 가상환경 + 의존성 설치 ==="
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo "=== 2. Postgres + pgvector 컨테이너 (서버 내부 전용, 127.0.0.1 바인딩) ==="
if ! docker ps -a --format '{{.Names}}' | grep -q '^rag-postgres$'; then
    docker run -d \
        --name rag-postgres \
        --restart unless-stopped \
        -p 127.0.0.1:5432:5432 \
        -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
        -e POSTGRES_DB="${DB_NAME}" \
        -v rag_postgres_data:/var/lib/postgresql/data \
        pgvector/pgvector:pg17
else
    docker start rag-postgres || true
fi

echo "=== 3. Ollama 설치 + 모델 다운로드 ==="
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
# 설치 스크립트가 systemd 서비스로 자동 시작함 (기본 127.0.0.1:11434 바인딩 = 외부 미노출)
ollama pull qwen3-embedding:0.6b
ollama pull qwen3:8b

echo "=== 4. .env 생성 ==="
if [ ! -f .env ]; then
    cat > .env <<EOF
DATABASE_URL=postgresql://postgres:${DB_PASSWORD}@localhost:5432/${DB_NAME}
OLLAMA_BASE_URL=http://localhost:11434
EOF
    echo ".env 생성 완료"
else
    echo ".env 이미 존재 — 건너뜀"
fi

echo ""
echo "=== 셋업 완료 ==="
echo "다음 단계: bash deploy/run_server.sh"
