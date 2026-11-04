# regulation-rag

업무일지(CommuteMate) 규정 + FAQ RAG 서비스

건국대학교 규정 기반 RAG 챗봇을 CommuteMate 챗봇 도입 제안서에 맞게 확장한 버전.

로컬·IntelliJ 테스트 절차는 [`TEST_MANUAL.md`](TEST_MANUAL.md)를 참고한다.

GitHub Actions, `develop -> main` 배포 흐름과 팀 서버 설정은 [`DEPLOYMENT.md`](DEPLOYMENT.md)를 참고한다.

## 브랜치·배포 흐름

```text
feature/*, fix/*, jonghwa -> PR develop -> CI -> staging
develop -> PR main -> CI -> production
```

`develop`과 `main`에는 직접 push하지 않는다. `develop` merge는 staging, `main` merge는 production 자동 배포를 시작한다.

## 구조

기능별 실제 구현 경로를 직접 사용한다. 이전 최상위 호환 모듈은 제거했다.

| 기능 | 실제 구현 경로 |
|---|---|
| FastAPI 진입점 | `app/main.py` (`app.main:app`) |
| 환경설정 | `app/core/config.py` |
| DB 연결·스키마 | `app/infrastructure/database.py` |
| 챗봇 답변 생성 | `app/chat/service.py` |
| 규정·FAQ 검색 | `app/search/service.py` |
| 검색 결과 재정렬 | `app/search/reranker.py` |
| PDF 인덱싱 순서 | `app/ingestion/service.py` |
| 인덱싱 저장·연락처 변환 | `app/ingestion/storage.py`, `app/ingestion/contacts.py` |
| 공통 임베딩 처리 | `app/ingestion/embedder.py` |
| PDF 파싱·OCR·문서 처리 | `app/ingestion/parser.py`, `app/ingestion/ocr.py`, `app/ingestion/records.py` |
| 조문 청킹 | `app/ingestion/chunker.py` |
| FAQ 청킹·저장 | `app/faqs/chunker.py`, `app/faqs/service.py` |

Python 코드에서도 `from app.infrastructure.database import get_conn`처럼 실제 모듈을 직접 import한다.

- **저장소**: 챗봇 전용 PostgreSQL + pgvector (`regulation_chunks` / `faq_chunks` / `phone_contacts`). 연결은 `DATABASE_URL`로 지정하며 업무일지 DB와 구분한다.
- **임베딩**: Ollama `qwen3-embedding:0.6b` (1024차원) — 문서/질문 모두 이 서비스에서 임베딩
- **검색**: 소스별 독립 하이브리드 — 벡터(코사인) + 키워드(`to_tsvector`) → RRF(k=60) → `BAAI/bge-reranker-v2-m3` 재정렬
- **LLM**: Ollama `qwen3:8b` (think 비활성화)
- **챗봇 게이트**: 규정·FAQ 둘 다 근거 0건일 때만 LLM 미호출("관련 내용을 찾을 수 없습니다"). FAQ만 있으면 "규정 근거 없음"을 명시하고 답변. 규정과 FAQ 충돌 시 `CONFLICT` 마커로 감지.

### 관리자가 수정할 수 있는 파일

- 채팅 답변 프롬프트: `resources/prompts/chat_answer.txt`
- 규정 검색 확장 규칙: `resources/search/query_expansions.json`

두 파일은 요청할 때마다 다시 읽으므로 서버를 재시작하지 않아도 변경이 반영된다. 프롬프트의 `{{QUESTION}}`, `{{REGULATION_CONTEXT}}`, `{{FAQ_CONTEXT}}`, `{{FAQ_ONLY_NOTICE}}` 표시자는 삭제하지 말아야 한다.

### 규정 청킹

규정 PDF는 `제1조`, `제1조의2`, `제 1 조` 형식의 조문 머리를 기준으로 청킹한다. 하나의 조문이 여러 페이지로 이어지면 하나의 청크로 유지한다. 조문 표식이 없는 문서는 기존 900자 방식으로 처리한다.

기존 DB에는 이전 900자 청크가 들어 있으므로, 조문 청킹을 적용하려면 배포 후 규정 인제스트를 다시 실행해야 한다.

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

# 3. 환경 변수 (챗봇 전용 DB)
export DATABASE_URL="postgresql://<user>:<password>@localhost:5432/<db>"
export OLLAMA_BASE_URL="http://localhost:11434"

# 4. 규정 PDF 인덱싱 (documents/ 폴더의 PDF → regulation_chunks)
python -m app.ingestion.service

# 5. 서버 실행
uvicorn app.main:app --port 8000
```

스키마(`vector` 확장, 테이블, HNSW/GIN 인덱스)는 서버 시작 또는 ingest 시 자동 생성된다 (`app/infrastructure/database.py`의 `init_schema`).

## 운영 배포 구성

현재 Compose는 API 컨테이너만 실행하며 기본 bridge 네트워크를 사용한다. 챗봇 전용 DB와 호스트 Ollama 주소는 서버 `.env`에 설정하고, 문서·설정·모델 캐시는 기존 마운트로 유지한다.

업무일지와 공유할 네트워크·alias 선언 및 배포 스크립트의 loopback 검사는 아직 정비가 필요하다. 현재 코드만으로 백엔드 연동 또는 운영 배포 완료를 의미하지 않는다. 상세 설정과 미완료 항목은 [DEPLOYMENT.md](DEPLOYMENT.md)를 따른다. 이전 venv 방식은 [참고 기록](docs/legacy-deployment.md)으로 분리했다.

## 자동 테스트

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

테스트는 DB·Ollama를 실제 호출하지 않는다. DB 풀의 동시 초기화와 rollback, 설정 검증, 문서 처리·임베딩 계약, 배포 스크립트를 검증하며 실제 모델 품질·GPU 동작은 별도 통합 검증 대상이다.

## 검증 절차

1. `http://localhost:8000/docs` 접속
2. `POST /api/v1/faqs`로 FAQ 샘플 등록 → `GET /health`에서 `faq_chunks` 증가 확인
3. `GET /api/v1/faqs/search?q=...` — LLM 없이 faq_id 목록 반환 확인
4. `POST /api/v1/chat/query` —
   - 규정에 있는 질문: 규정 근거 인용 답변 + `regulation_sources`
   - FAQ에만 있는 질문: "공식 규정 근거는 확인되지 않았으며..." 서두 확인
   - 둘 다 없는 질문: "관련 내용을 찾을 수 없습니다." (LLM 미호출)
   - 응답 본문에 `CONFLICT:` 마커가 노출되지 않는지 확인
5. CommuteMate(Spring) 연동: `RAG_SERVICE_URL=http://localhost:8000`로 Spring 기동 → FAQ 생성/수정/삭제가 `faq_chunks`에 반영되는지, RAG 중단 상태에서 FAQ 검색이 키워드 검색으로 폴백하는지 확인

## 주의

- 첫 검색 요청 시 재정렬 모델(bge-reranker-v2-m3) 로딩으로 수십 초 지연될 수 있다 (이후 캐시).
- 임베딩 모델을 바꾸면 규정·FAQ 데이터 전체 재임베딩(재인제스트 + FAQ 백필)이 필요하다.
- `MIN_RELEVANCE_SCORE`(기본 -1.0)는 재정렬 raw logit 기준 임계값 — 모델 교체 시 재조정 필요.
