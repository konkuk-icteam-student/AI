#!/usr/bin/env bash
#
# RAG 서비스 실행 스크립트
# regulation-rag 디렉터리 루트에서 실행:
#   bash deploy/run_server.sh          # 서버 실행 (백그라운드)
#   bash deploy/run_server.sh ingest   # 규정 PDF 인덱싱 (최초 1회 / 규정 변경 시)
#   bash deploy/run_server.sh stop     # 서버 중단
#   bash deploy/run_server.sh log      # 로그 확인
#
set -euo pipefail

cd "$(dirname "$0")/.."

# config.py는 환경변수만 읽으므로 .env를 셸에서 로드한다
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

PID_FILE="uvicorn.pid"
LOG_FILE="uvicorn.log"

case "${1:-run}" in
    ingest)
        .venv/bin/python -m app.ingest
        ;;

    stop)
        if [ -f "${PID_FILE}" ]; then
            kill "$(cat "${PID_FILE}")" 2>/dev/null || true
            rm -f "${PID_FILE}"
            echo "서버 중단 완료"
        else
            echo "실행 중인 서버 없음 (${PID_FILE} 없음)"
        fi
        ;;

    log)
        tail -f "${LOG_FILE}"
        ;;

    run)
        if [ -f "${PID_FILE}" ] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
            echo "이미 실행 중 (PID $(cat "${PID_FILE}"))"
            exit 0
        fi

        # 외부(로컬 Spring)에서 접근해야 하므로 0.0.0.0 바인딩
        nohup .venv/bin/uvicorn app.api:app \
            --host 0.0.0.0 \
            --port 8000 \
            > "${LOG_FILE}" 2>&1 &

        echo $! > "${PID_FILE}"
        echo "서버 시작 (PID $(cat "${PID_FILE}"), 로그: ${LOG_FILE})"
        echo "확인: curl http://localhost:8000/health"
        ;;

    *)
        echo "사용법: bash deploy/run_server.sh [run|ingest|stop|log]"
        exit 1
        ;;
esac
