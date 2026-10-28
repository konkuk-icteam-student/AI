# 이전 venv 기반 배포 참고

이 문서는 이전 분리 서버 구성의 기록이다. 현재 Docker bridge 배포에는 [DEPLOYMENT.md](../DEPLOYMENT.md)를 사용한다. 아래 명령의 상대 경로는 프로젝트 루트를 기준으로 한다. 기존 서버에서 setup 스크립트를 다시 실행하는 절차가 아니다.

## 이전 원격 서버 배포 (RAG+LLM 분리 구성)

RAG+Ollama+청크DB를 원격 서버에, Spring/프론트/Spring용 DB를 로컬에 두는 구성.
Spring과는 `faq_id`로만 연결되므로 청크 DB는 원격 서버에만 있으면 된다.

```bash
# 1. [로컬] 코드 복사 (.venv/.git 제외)
rsync -av --exclude .venv --exclude .git --exclude __pycache__ \
    "~/Desktop/code/task diary/regulation-rag/" \
    <user>@203.252.168.117:~/regulation-rag/

# 2. [서버] 최초 셋업 — venv, Postgres+pgvector 컨테이너, Ollama+모델, .env
ssh <user>@203.252.168.117
cd ~/regulation-rag
bash deploy/setup_server.sh

# 3. [서버] 규정 인덱싱 후 서버 실행 (0.0.0.0:8000)
bash deploy/run_server.sh ingest
bash deploy/run_server.sh          # nohup 백그라운드, stop/log 서브커맨드 지원

# 4. [로컬] 연결 확인
curl http://203.252.168.117:8000/health

# 5. [로컬] Spring 실행
export RAG_SERVICE_URL="http://203.252.168.117:8000"
# + 로컬 Postgres용 DB_URL/DB_USERNAME/DB_PASSWORD/DB_DRIVER/JPA_DATABASE_PLATFORM
./gradlew bootRun
```

**보안 주의 (테스트 구성 기준)**
- 방화벽에서 **8000 포트만** 외부 개방. Postgres(5432)와 Ollama(11434)는 스크립트가 127.0.0.1에 바인딩하므로 외부 미노출.
- RAG API에 인증이 없으므로 운영 전에 API 키 또는 사내망 제한 필요.
- GPU 없는 서버면 CPU 추론으로 동작은 하지만 챗봇 응답이 매우 느려진다 (qwen3:8b 기준 분 단위).
