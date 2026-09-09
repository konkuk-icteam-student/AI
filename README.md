# regulation-rag

업무일지(CommuteMate) 규정 + FAQ RAG 서비스

건국대학교 규정 기반 RAG 챗봇을 CommuteMate 챗봇 도입 제안서에 맞게 확장한 버전.

## 구조

- **저장소**: Postgres + pgvector (CommuteMate와 동일 인스턴스 공유, `regulation_chunks` / `faq_chunks`)
- **임베딩**: Ollama `qwen3-embedding:0.6b` (1024차원) — 문서/질문 모두 이 서비스에서 임베딩
- **검색**: 소스별 독립 하이브리드 — 벡터(코사인) + 키워드(`to_tsvector`) → RRF(k=60) → `BAAI/bge-reranker-v2-m3` 재정렬
- **LLM**: Ollama `qwen3:8b` (think 비활성화)
- **챗봇 게이트**: 규정·FAQ 둘 다 근거 0건일 때만 LLM 미호출("관련 규정을 찾을 수 없습니다"). FAQ만 있으면 "규정 근거 없음"을 명시하고 답변. 규정과 FAQ 충돌 시 `CONFLICT` 마커로 감지.

## API

| 엔드포인트 | 설명 |
|---|---|
| `POST /api/v1/chat/query` | 규정+FAQ 근거 챗봇 답변 (LLM 호출) |
| `GET /api/v1/faqs/search` | FAQ 하이브리드 검색, faq_id+점수 목록만 반환 (LLM 미호출) |
| `POST /api/v1/faqs` | FAQ 청킹·임베딩·저장 (Spring이 생성/수정 시 호출, upsert) |
| `DELETE /api/v1/faqs/{faq_id}` | FAQ 삭제 시 청크 제거 |
| `POST /api/v1/category/recommend` | 카테고리 추천용 LLM 프록시 (프롬프트 구성/파싱은 Spring 담당) |
| `GET /health` | DB·청크 상태 확인 |

## 실행

```bash
# 1. 의존성 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Ollama 모델 준비
ollama pull qwen3-embedding:0.6b
ollama pull qwen3:8b

# 3. 환경 변수 (CommuteMate Postgres 공유)
export DATABASE_URL="postgresql://<user>:<password>@localhost:5432/<db>"
export OLLAMA_BASE_URL="http://localhost:11434"

# 4. 규정 PDF 인덱싱 (documents/ 폴더의 PDF → regulation_chunks)
python -m app.ingest

# 5. 서버 실행
uvicorn app.api:app --port 8000
```

스키마(`vector` 확장, 테이블, HNSW/GIN 인덱스)는 서버 시작 또는 ingest 시 자동 생성된다 (`app/db.py`의 `init_schema`).

## 원격 서버 배포 (RAG+LLM 분리 구성)

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

## 검증 절차

1. `http://localhost:8000/docs` 접속
2. `POST /api/v1/faqs`로 FAQ 샘플 등록 → `GET /health`에서 `faq_chunks` 증가 확인
3. `GET /api/v1/faqs/search?q=...` — LLM 없이 faq_id 목록 반환 확인
4. `POST /api/v1/chat/query` —
   - 규정에 있는 질문: 규정 근거 인용 답변 + `regulation_sources`
   - FAQ에만 있는 질문: "공식 규정 근거는 확인되지 않았으며..." 서두 확인
   - 둘 다 없는 질문: "관련 규정을 찾을 수 없습니다." (LLM 미호출)
   - 응답 본문에 `CONFLICT:` 마커가 노출되지 않는지 확인
5. CommuteMate(Spring) 연동: `RAG_SERVICE_URL=http://localhost:8000`로 Spring 기동 → FAQ 생성/수정/삭제가 `faq_chunks`에 반영되는지, RAG 중단 상태에서 FAQ 검색이 키워드 검색으로 폴백하는지 확인

## 주의

- 첫 검색 요청 시 재정렬 모델(bge-reranker-v2-m3) 로딩으로 수십 초 지연될 수 있다 (이후 캐시).
- 임베딩 모델을 바꾸면 규정·FAQ 데이터 전체 재임베딩(재인제스트 + FAQ 백필)이 필요하다.
- `MIN_RELEVANCE_SCORE`(기본 0.0)는 재정렬 raw logit 기준 임계값 — 모델 교체 시 재조정 필요.
