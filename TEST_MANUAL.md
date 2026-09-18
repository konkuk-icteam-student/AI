# RAG 서비스 테스트 매뉴얼

이 문서는 `jonghwa` 브랜치의 기능을 IntelliJ IDEA와 로컬 터미널에서 검증하는 절차를 정리한다.

## 1. 테스트 범위

| 기능 | 외부 서비스 | 확인 내용 |
|---|---|---|
| 자동 단위 테스트 | 불필요 | 조문 청킹, 프롬프트 치환, 검색 확장 규칙 |
| 조문 청킹 수동 테스트 | 불필요 | `제1조`, `제1조의2`, 페이지 연결, 폴백 |
| 프롬프트 파일 테스트 | 불필요 | 외부 파일 로드, 필수 표시자 검증 |
| 검색 확장 파일 테스트 | 불필요 | JSON 규칙의 `any`/`all`, 중복 제거 |
| API 기동·상태 테스트 | PostgreSQL | FastAPI 기동, DB 스키마, `/health` |
| 규정 인제스트 | PostgreSQL, Ollama | PDF 파싱, 조문 청킹, 임베딩, DB 저장 |
| FAQ 등록·검색·삭제 | PostgreSQL, Ollama | FAQ 청킹과 검색 API |
| 챗봇 답변 | PostgreSQL, Ollama | 규정·FAQ 검색, LLM 답변, 출처 |
| 운영 파일 무중단 반영 | PostgreSQL, Ollama | 프롬프트·검색 확장 파일 재로드 |

## 2. 사전 확인

IntelliJ 터미널에서 다음을 실행한다.

```bash
pwd
git branch --show-current
git status --short --branch
```

기대 결과:

- 현재 경로의 끝이 `/AI`이다.
- 브랜치가 `jonghwa`다.
- 수정한 파일이 없다면 `git status`에 별도 목록이 나오지 않는다.

## 3. Python 실행 환경

### 3.1 가상환경 생성

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

OCR·PyTorch·PaddlePaddle이 포함되어 있어 최초 설치에 시간이 걸릴 수 있다.

### 3.2 IntelliJ Python Interpreter

1. `Settings` → `Project` → `Python Interpreter`로 이동한다.
2. `Add Interpreter` → `Existing Environment`를 선택한다.
3. `<AI 경로>/.venv/bin/python`을 선택한다.
4. Run Configuration의 Working directory를 `<AI 경로>`로 설정한다.

Working directory가 다르면 `No module named app` 오류가 발생한다.

## 4. 1단계: 외부 서비스 없이 자동 테스트

DB나 Ollama를 실행하지 않고도 가장 중요한 변경을 확인할 수 있다.

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

기대 결과:

```text
Ran 11 tests
OK
```

테스트하는 주요 상황:

- 조문을 조문별로 분리한다.
- `제1조의2`, `제 1 조` 형식을 인식한다.
- 한 조문이 페이지를 넘어가도 하나의 청크로 유지한다.
- `제5조에 따라`를 새 조문으로 잘못 인식하지 않는다.
- 목차의 `제1조 목적 1`을 실제 조문으로 잘못 인식하지 않는다.
- 조문이 없는 문서는 기존 고정 길이 청킹을 사용한다.
- 프롬프트와 검색 확장 JSON 형식을 검증한다.

IntelliJ에서는 `tests` 디렉터리를 우클릭한 뒤 `Run 'Unittests in tests'`를 선택해도 된다.

## 5. 2단계: 조문 청킹기 수동 확인

다음 명령은 실제 DB에 저장하지 않고 순수 청킹 결과만 보여준다.

```bash
python - <<'PY'
from app.ingestion.chunker import PageText, chunk_regulation_pages

pages = [
    PageText(
        page=1,
        text="""1장 총칙
제1조(목적) 이 규정은 테스트를 위한다.
제2조(절차) 첫 번째 절차다.""",
    ),
    PageText(
        page=2,
        text="""두 번째 절차다.
제3조의2(특례) 특례를 정한다.""",
    ),
]

for chunk in chunk_regulation_pages(pages):
    print("-" * 60)
    print("article:", chunk.article)
    print("start page:", chunk.page)
    print(chunk.text)
PY
```

기대 결과:

- 총 3개의 청크가 출력된다.
- `제2조(절차)` 청크에 2페이지의 `두 번째 절차다.`가 포함된다.
- `제2조` 청크의 시작 페이지는 1이다.
- `제3조의2`를 독립된 조문으로 인식한다.

## 6. 3단계: 운영 파일 확인

### 6.1 프롬프트 파일

관리 파일:

```text
resources/prompts/chat_answer.txt
```

필수 표시자:

```text
{{QUESTION}}
{{REGULATION_CONTEXT}}
{{FAQ_CONTEXT}}
{{FAQ_ONLY_NOTICE}}
```

파일 로드와 치환 결과 확인:

```bash
python - <<'PY'
from app.chat.prompts import render_chat_prompt

prompt = render_chat_prompt(
    question="휴학 신청은 어떻게 하나요?",
    regulation_context="[규정 테스트]",
    faq_context="[FAQ 테스트]",
    faq_only_notice="[FAQ 안내 테스트]",
)

print(prompt[-800:])
PY
```

기대 결과:

- 마지막 부분에 질문과 세 가지 테스트 문맥이 모두 출력된다.
- `{{QUESTION}}` 같은 표시자가 그대로 남아 있지 않다.

필수 표시자를 삭제하면 서비스는 잘못된 프롬프트를 LLM에 전송하지 않고 명확한 오류를 발생시킨다.

### 6.2 검색 확장 규칙

관리 파일:

```text
resources/search/query_expansions.json
```

현재 규칙 확인:

```bash
python - <<'PY'
from app.search.query_expansion import expand_regulation_query

for query in [
    "휴학 신청 방법을 알려줘",
    "장애인 가족 장학금이 있나요?",
    "일반 질문",
]:
    print(query)
    for expanded in expand_regulation_query(query):
        print("  -", expanded)
PY
```

기대 결과:

- `휴학` 질문에 휴학 절차·등록금 관련 확장 질의가 추가된다.
- `장애` + `장학` 질문에 두 규칙의 확장 질의가 모두 추가된다.
- 어떤 키워드도 없는 질문은 원본 질문만 반환한다.

규칙을 추가할 때는 다음 형식을 사용한다.

```json
{
  "name": "테스트 규칙",
  "keywords": ["테스트", "예시"],
  "match": "any",
  "expansions": ["테스트 확장 질의"]
}
```

- `match: "any"`: `keywords` 중 하나만 일치해도 적용한다.
- `match: "all"`: `keywords` 모두가 일치해야 적용한다.
- `match`를 생략하면 `any`로 동작한다.

프롬프트와 검색 확장 JSON은 요청할 때마다 다시 읽는다. 실제 파일을 변경해 확인했다면 테스트 후 반드시 변경을 되돌린다.

## 7. 4단계: PostgreSQL·Ollama 통합 환경

> 주의: 규정 인제스트는 `regulation_chunks`와 `phone_contacts`를 초기화한다. 반드시 로컬 테스트 DB에서만 실행한다. staging/prod DB를 연결하지 말아야 한다.

### 7.1 테스트 DB

Docker Desktop을 실행한 뒤:

```bash
docker run -d \
  --name rag-postgres-test \
  -p 127.0.0.1:5433:5432 \
  -e POSTGRES_PASSWORD=ragtest \
  -e POSTGRES_DB=ragtest \
  -v rag_postgres_test_data:/var/lib/postgresql/data \
  pgvector/pgvector:pg17
```

상태 확인:

```bash
docker ps --filter name=rag-postgres-test
docker logs rag-postgres-test --tail 30
```

### 7.2 Ollama

```bash
ollama pull qwen3-embedding:0.6b
ollama pull qwen3:8b
ollama list
```

Ollama 앱이나 `ollama serve`를 이용해 `http://localhost:11434`에서 실행되어야 한다.

### 7.3 환경변수

IntelliJ Run Configuration 또는 터미널에 다음을 설정한다.

```bash
export DATABASE_URL="postgresql://postgres:ragtest@localhost:5433/ragtest"
export OLLAMA_BASE_URL="http://localhost:11434"
```

현재 설정 확인:

```bash
python - <<'PY'
from app.core.config import DATABASE_URL, OLLAMA_BASE_URL

print("DATABASE_URL:", DATABASE_URL.replace("ragtest@", "***@"))
print("OLLAMA_BASE_URL:", OLLAMA_BASE_URL)
PY
```

## 8. 5단계: API 서버 테스트

### 8.1 IntelliJ Run Configuration

1. `Run` → `Edit Configurations`로 이동한다.
2. Python 설정을 새로 만든다.
3. `Module name`은 `uvicorn`으로 설정한다.
4. Parameters에 다음을 입력한다.

```text
app.main:app --host 127.0.0.1 --port 8000 --reload
```

5. Working directory를 `<AI 경로>`로 설정한다.
6. `DATABASE_URL`, `OLLAMA_BASE_URL`을 Environment variables에 추가한다.

터미널에서 실행하려면:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 8.2 상태 확인

브라우저:

```text
http://localhost:8000/docs
```

터미널:

```bash
curl http://localhost:8000/health
```

기대 응답 형식:

```json
{
  "status": "ok",
  "regulation_chunks": 0,
  "faq_chunks": 0,
  "embedding_model": "qwen3-embedding:0.6b",
  "llm_model": "qwen3:8b"
}
```

청크 수는 DB 상태에 따라 0보다 클 수 있다.

## 9. 6단계: 규정 인제스트와 조문 저장 확인

> 전체 PDF 임베딩은 오래 걸릴 수 있다. 처음에는 로컬 테스트 DB인지 다시 확인한다.

```bash
python -m app.ingestion.service
```

성공 기준:

- PDF 개수와 청크 수가 출력된다.
- `인덱싱 완료`가 출력된다.
- 처리 실패 PDF가 있다면 파일명이 별도로 출력된다.

DB에 접속해 조문 경계를 확인한다.

```bash
docker exec -it rag-postgres-test \
  psql -U postgres -d ragtest
```

```sql
SELECT count(*) FROM regulation_chunks;

SELECT
    source,
    page,
    chunk_index,
    length(content) AS content_length,
    left(content, 180) AS content_preview
FROM regulation_chunks
ORDER BY source, chunk_index
LIMIT 30;
```

수동 확인 포인트:

- 청크 시작 부분에 `제1조(목적)` 같은 조문 머리가 보인다.
- 청크 길이가 일괄적으로 900자에서 끊기지 않고 조문 길이에 따라 다르다.
- 이전 청크의 뒷부분과 다음 청크의 앞부분이 150자씩 반복되는 패턴이 사라져야 한다.
- 조문 표식이 없는 PDF만 기존 900자 폴백을 사용한다.

## 10. 7단계: FAQ API

다음 ID는 로컬 테스트용이다.

### 10.1 FAQ 등록

```bash
curl -X POST http://localhost:8000/api/v1/faqs \
  -H 'Content-Type: application/json' \
  -d '{
    "faq_id": 990000001,
    "title": "휴학 신청 테스트",
    "text": "휴학 신청은 학사 일정과 소속 단과대학의 안내를 확인한 뒤 진행합니다.",
    "category_ids": [101],
    "category_names": ["학사"],
    "created_at": "2026-09-18"
  }'
```

기대 응답:

```json
{
  "faq_id": 990000001,
  "chunk_count": 1
}
```

### 10.2 FAQ 검색

```bash
curl --get http://localhost:8000/api/v1/faqs/search \
  --data-urlencode 'q=휴학 신청' \
  --data-urlencode 'category_ids=101' \
  --data-urlencode 'top_k=10'
```

성공 기준:

- `results` 목록에 `faq_id: 990000001`이 포함된다.
- 동일 FAQ의 청크가 여러 개여도 FAQ ID는 한 번만 나온다.

### 10.3 FAQ 수정성 upsert

동일한 `faq_id` 값으로 내용을 바꾸어 `POST /api/v1/faqs`를 다시 호출한다.

성공 기준:

- 이전 청크가 중복 유지되지 않는다.
- DB의 `faq_chunks`에는 수정한 내용만 남는다.

```sql
SELECT faq_id, chunk_index, content
FROM faq_chunks
WHERE faq_id = 990000001
ORDER BY chunk_index;
```

### 10.4 FAQ 삭제

```bash
curl -X DELETE http://localhost:8000/api/v1/faqs/990000001
```

기대 응답:

```json
{
  "faq_id": 990000001,
  "deleted_chunks": 1
}
```

FAQ가 여러 청크로 나뉘어 있었다면 `deleted_chunks`는 1보다 클 수 있다.

## 11. 8단계: 챗봇 답변 API

```bash
curl -X POST http://localhost:8000/api/v1/chat/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"휴학 신청 자격과 절차를 알려줘"}'
```

성공 기준:

- `answer`가 비어 있지 않다.
- `regulation_sources`에 문서명, 페이지, 청크 번호, 점수가 포함된다.
- `faq_sources`는 관련 FAQ가 있을 때만 값이 들어 있다.
- 사용자에게 보여주는 `answer`에 `CONFLICT: yes/no`가 노출되지 않는다.
- 충돌 감지 결과는 `conflict_detected`에 boolean으로 나온다.

첫 검색은 reranker 모델을 로딩하므로 수십 초 이상 걸릴 수 있다.

## 12. 9단계: 프롬프트 무중단 반영

1. 서버를 실행한 상태에서 `resources/prompts/chat_answer.txt`를 연다.
2. 규칙 아래에 다음 한 줄을 임시로 추가한다.

```text
답변의 첫 줄에 [PROMPT-RELOAD-TEST]를 출력하세요.
```

3. 서버를 재시작하지 않고 챗봇 API를 다시 호출한다.
4. `answer`의 첫 줄에 `[PROMPT-RELOAD-TEST]`가 포함되는지 확인한다.
5. 테스트 문장을 삭제해 원복한다.
6. `git diff -- resources/prompts/chat_answer.txt`의 출력이 없는지 확인한다.

LLM이 지시를 따르지 않는 한 번의 응답만으로 실패를 판정하지 말고 2~3회 확인한다.

## 13. 10단계: 검색 확장 규칙 무중단 반영

1. `resources/search/query_expansions.json`의 `rules` 목록에 다음 규칙을 임시로 추가한다.

```json
{
  "name": "재로드 테스트",
  "keywords": ["재로드테스트"],
  "expansions": ["휴학 신청 자격 절차 기간"]
}
```

2. 서버 재시작 없이 다음을 실행한다.

```bash
python - <<'PY'
from app.search.query_expansion import expand_regulation_query
print(expand_regulation_query("재로드테스트"))
PY
```

3. 출력에 `휴학 신청 자격 절차 기간`이 포함되는지 확인한다.
4. 임시 규칙을 삭제해 원복한다.
5. `python -m json.tool resources/search/query_expansions.json` 명령이 성공하는지 확인한다.

## 14. 카테고리 추천 LLM 프록시

```bash
curl -X POST http://localhost:8000/api/v1/category/recommend \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"다음 질문의 카테고리를 한 단어로 답하세요: 휴학 신청 방법"}'
```

성공 기준:

- HTTP 200이다.
- `response`가 비어 있지 않다.

이 API는 Spring이 전달한 프롬프트를 Ollama에 전달하는 프록시이므로 정확한 카테고리 형식 검증은 Spring 연동 테스트에서 한다.

## 15. 오류별 확인 방법

| 증상 | 가능성이 큰 원인 | 확인·해결 |
|---|---|---|
| `No module named app` | Working directory 오류 | IntelliJ Working directory를 `AI` 루트로 설정 |
| `No module named fastapi` | 다른 Python Interpreter 사용 | `.venv/bin/python`이 선택됐는지 확인 |
| DB connection refused | Docker 미기동 또는 포트 오류 | `docker ps`, `DATABASE_URL`, 5433 포트 확인 |
| `relation ... does not exist` | 스키마 초기화 실패 | 서버 시작 로그와 DB 계정의 CREATE 권한 확인 |
| Ollama connection refused | Ollama 미기동 | Ollama 앱 또는 `ollama serve` 실행 |
| 임베딩 모델 없음 | 모델 미설치 | `ollama pull qwen3-embedding:0.6b` |
| 첫 검색이 매우 느림 | reranker 최초 로딩 | 첫 요청은 대기하고 두 번째 요청 속도와 비교 |
| JSON 형식 오류 | 쉼표·따옴표 오류 | `python -m json.tool resources/search/query_expansions.json` |
| 프롬프트 필수 표시자 오류 | `{{...}}` 삭제 | Git diff로 원복하고 4개 표시자 확인 |
| 인제스트 후 검색 결과 없음 | PDF 추출·Ollama·DB 저장 실패 | 인제스트 로그, `regulation_chunks` 개수 확인 |

## 16. 최종 체크리스트

- [ ] `jonghwa` 브랜치에서 테스트했다.
- [ ] 자동 테스트 11개가 모두 통과했다.
- [ ] `/health`가 HTTP 200을 반환했다.
- [ ] 인제스트 후 청크가 고정 900자가 아닌 조문 길이로 저장됐다.
- [ ] `제1조의2` 형태의 조문이 독립 청크로 저장됐다.
- [ ] FAQ 등록·검색·수정·삭제가 동작했다.
- [ ] 챗봇 응답에 출처가 포함됐다.
- [ ] `CONFLICT:` 마커가 사용자 답변에 노출되지 않았다.
- [ ] 프롬프트 변경이 서버 재시작 없이 반영됐다.
- [ ] 검색 확장 JSON 변경이 서버 재시작 없이 반영됐다.
- [ ] 임시 FAQ와 임시 운영 파일 변경을 모두 정리했다.
- [ ] `git status --short` 결과에 의도하지 않은 변경이 없다.

## 17. 테스트 후 로컬 DB 정리

테스트 컨테이너를 멈추기만 하려면:

```bash
docker stop rag-postgres-test
```

다음 테스트에서 다시 사용하려면:

```bash
docker start rag-postgres-test
```

컨테이너와 테스트 DB 데이터를 모두 폐기하려면 다음 대상이 `rag-postgres-test`, `rag_postgres_test_data`가 맞는지 먼저 확인한 뒤 정리한다.

```bash
docker rm -f rag-postgres-test
docker volume rm rag_postgres_test_data
```
