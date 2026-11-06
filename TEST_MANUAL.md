# AI RAG 테스트 가이드

단위 테스트와 **로컬 통합 테스트**를 구분한 반복 가능한 검증 절차입니다. 서버 배포는 [DEPLOYMENT.md](DEPLOYMENT.md), Staging 질문 CSV 배치 실행은 [rag_evaluation/README.md](rag_evaluation/README.md)를 참고합니다.

## 1. 테스트 대상과 준비물

| 검증 영역 | PostgreSQL | Ollama | 설명 |
|---|---|---|---|
| Python 단위·배포 스크립트 테스트 | 불필요 | 불필요 | 조문 청킹·검색·프롬프트·DB 로직 및 CI/CD 가드 |
| API 기동·`/health` | 필요 | 설정 필요 | DB 스키마·청크 개수 확인 |
| FAQ 생성·검색·삭제 | 필요 | 필요 | 임베딩, 인덱스, 검색 |
| 규정 PDF 인제스트 | 필요 | 필요 | OCR/청킹/임베딩/DB 기록 |
| 챗봇·카테고리 응답 | 필요 | 필요 | 검색, LLM, 출처·응답 확인 |

각 명령어는 저장소 루트에서 실행합니다. **인제스트는 대상 DB의 `regulation_chunks`와 `phone_contacts`를 초기화하므로**, 로컬 전용 DB가 아닌 Staging/Production에 테스트 인제스트를 실행하지 않습니다.

## 2. 외부 서비스 없는 단위 테스트

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

검증 대상에는 `app/`의 검색·청킹·프롬프트·FAQ 로직과 `deploy/scripts/`의 검증·복구·소스 동기화가 포함됩니다. CI도 이 테스트를 실행합니다. 외부 DB, API 서버 또는 GPU를 호출하지 않으므로 **실제 RAG 답변 품질 검증과는 구분**해야 합니다.

IntelliJ 사용 시 Python 인터프리터를 저장소의 `.venv`로, Working directory를 저장소 루트로 설정합니다.

## 3. 로컬 통합 테스트 환경

### PostgreSQL/pgvector 준비

Docker가 실행 중인 로컬 개발 PC에서 **테스트 전용** 컨테이너를 생성합니다.

```bash
docker run -d \
  --name rag-postgres-test \
  -p 127.0.0.1:5433:5432 \
  -e POSTGRES_DB=ragtest \
  -e POSTGRES_PASSWORD=ragtest \
  -v rag_postgres_test_data:/var/lib/postgresql/data \
  pgvector/pgvector:pg17

docker ps --filter name=rag-postgres-test
```

이 명령은 같은 이름의 컨테이너가 이미 없을 때만 실행합니다. 기존 테스트 컨테이너가 중지된 경우 `docker start rag-postgres-test`를 사용합니다. **이 DB는 운영 데이터와 분리**해야 합니다.

### Ollama 및 Python 실행

```bash
ollama pull qwen3-embedding:0.6b
ollama pull qwen3:8b
ollama list

source .venv/bin/activate
python -m pip install -r requirements.txt

export DATABASE_URL='postgresql://postgres:ragtest@127.0.0.1:5433/ragtest'
export OLLAMA_BASE_URL='http://127.0.0.1:11434'
export EMBEDDING_MODEL='qwen3-embedding:0.6b'
export LLM_MODEL='qwen3:8b'

uvicorn app.main:app --host 127.0.0.1 --port 8000
```

위 `ragtest` DB 비밀번호는 **로컬 개발 예시 전용**입니다. 운영 DB에 그대로 사용하지 않습니다. Ollama는 `11434` 포트에서 실행 중이어야 합니다. 로컬 Python 서버에는 Docker 내부 호스트명 대신 로컬 주소를 사용합니다.

## 4. API 기본 상태

다른 터미널에서:

```bash
curl --fail http://127.0.0.1:8000/health
```

Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

`/health`는 DB의 규정·FAQ 청크 수와 모델 설정을 반환합니다. 청크가 0개라도 DB 연결이 정상이면 HTTP 200이 나올 수 있으므로, 검색 결과와 LLM 응답도 따로 확인합니다.

## 5. FAQ API 통합 테스트

**로컬 테스트 DB에서만** 다음 데이터를 등록합니다.

```bash
curl --fail -X POST http://127.0.0.1:8000/api/v1/faqs \
  -H 'Content-Type: application/json' \
  -d '{
    "faq_id": 990000001,
    "title": "휴학 신청 예시",
    "text": "휴학 신청 절차는 학사 공지와 소속 대학 안내를 확인합니다.",
    "category_ids": [101],
    "category_names": ["학사"]
  }'
```

검색:

```bash
curl --get --fail http://127.0.0.1:8000/api/v1/faqs/search \
  --data-urlencode 'q=휴학 신청' \
  --data-urlencode 'category_ids=101' \
  --data-urlencode 'top_k=10'
```

- 검색 응답은 `results` 목록의 `faq_id`·점수로 표시됩니다.
- 같은 `faq_id`에 수정된 본문을 다시 POST하면 FAQ 청크가 upsert되어야 합니다.
- 테스트 데이터 삭제:

```bash
curl --fail -X DELETE http://127.0.0.1:8000/api/v1/faqs/990000001
```

## 6. 챗봇 및 카테고리 응답

규정 문서가 인덱싱되지 않은 상태라면 근거가 없다는 응답이 정상일 수 있습니다.

```bash
curl --fail -X POST http://127.0.0.1:8000/api/v1/chat/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"휴학 신청 기준을 알려줘"}'
```

응답에 `answer`, `regulation_sources`, `faq_sources`, `conflict_detected`가 포함되는지 확인합니다. 출처가 실제 문서·페이지와 일치하는지는 사람이 검토해야 합니다.

```bash
curl --fail -X POST http://127.0.0.1:8000/api/v1/category/recommend \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"다음 질문을 한 단어로 분류해 줘: 휴학 신청 방법"}'
```

LLM 응답은 비결정적이므로 예시 문장과 한 글자씩 일치하는지보다 형식·근거·실패 처리를 검증합니다. 첫 요청은 reranker 모델 로딩으로 지연될 수 있습니다.

## 7. 조문 청킹·프롬프트·검색 확장 확인

DB 없이 확인할 수 있는 테스트는 `tests/`의 자동 테스트에서 우선 실행합니다. 각 기능의 현재 설정 파일과 구현은 다음과 같습니다.

| 확인 항목 | 코드/설정 |
|---|---|
| 조문 형식·페이지 연결·폴백 | `app/ingestion/chunker.py` |
| 프롬프트 표시자·치환 | `app/chat/prompts.py`, `resources/prompts/chat_answer.txt` |
| 검색 확장·규칙 파일 파싱 | `app/search/query_expansion.py`, `resources/search/query_expansions.json` |
| RAG 일괄 CSV 파싱·출력 | `scripts/run_rag_batch.py`, `tests/test_rag_batch_runner.py` |

프롬프트에 필요한 `{{QUESTION}}`, `{{REGULATION_CONTEXT}}`, `{{FAQ_CONTEXT}}`, `{{FAQ_ONLY_NOTICE}}` 표시는 삭제하지 않습니다.

검색 확장 JSON 문법 검사:

```bash
python -m json.tool resources/search/query_expansions.json > /dev/null
```

관리형 프롬프트·검색 확장 파일의 실시간 반영을 시험할 때는 **로컬 복제본에서만** 작업하고, 테스트 후 변경을 원복합니다. 운영 서버의 설정 파일을 테스트 목적으로 직접 수정하지 않습니다.

## 8. 규정 PDF 인제스트 (독립 테스트 DB에 한정)

`documents/text_pdf/` 또는 `documents/image_pdf/`에 허가된 테스트 PDF를 배치한 뒤 로컬 DB 연결 문자열을 다시 확인합니다.

```bash
# 실제 대상 DB가 ragtest인지 확인 (비밀번호 전체를 출력하지 않음)
docker exec rag-postgres-test \
  psql -U postgres -d ragtest -c 'SELECT current_database();'

# 기존 청크를 교체하므로 로컬 테스트 전용으로만 실행
python -m app.ingestion.service

# 결과 확인
docker exec rag-postgres-test \
  psql -U postgres -d ragtest \
  -c 'SELECT count(*) AS regulation_chunks FROM regulation_chunks;'
```

파일별 처리 실패 로그·최종 데이터 건수·대표 질의의 출처를 확인합니다. 인제스트 명령은 일부 파일이 실패해도 다른 파일을 계속 처리할 수 있습니다. 전체 DB 교체·백업·검증 방법은 [배포 가이드](DEPLOYMENT.md)를 참고합니다.

## 9. 문제 해결

| 현상 | 확인 |
|---|---|
| `No module named app` | 저장소 루트에서 실행하는지 확인 |
| `No module named fastapi` | 활성화한 가상환경/의존성 설치 확인 |
| DB 연결 실패 | `docker ps`와 `DATABASE_URL`의 호스트·포트 확인 |
| `/health` 503 | DB 연결/스키마/테이블 조회 오류 확인 |
| Ollama 모델 오류 | `ollama list`에서 `EMBEDDING_MODEL`·`LLM_MODEL` 확인 |
| 첫 검색만 느림 | reranker 로딩 및 모델 캐시 확인 |
| 검색 결과 없음 | PDF/FAQ 저장 건수, 검색 기준, 임베딩 모델 확인 |
| 프롬프트/JSON 오류 | 표시자와 JSON 문법·최근 Git 변경 검토 |

테스트가 끝나면 `docker stop rag-postgres-test`로 DB를 중지할 수 있습니다. 테스트 데이터를 **의도적으로 폐기할 경우에만** 컨테이너와 `rag_postgres_test_data` 볼륨을 삭제합니다. 운영 환경 볼륨에는 이 명령을 사용하지 않습니다.
