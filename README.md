# 건국대학교 규정·FAQ RAG 챗봇

건국대학교 규정 PDF와 FAQ를 검색하고 Ollama LLM으로 답변을 생성하는 FastAPI 서비스입니다. CommuteMate 백엔드와의 연동을 위한 FAQ 저장·검색, 카테고리 추천 API를 포함합니다.

## 문서 안내

| 문서 | 대상 및 내용 |
|---|---|
| **[RAG 질문 CSV 운영 가이드](rag_evaluation/README.md)** | 질문 CSV를 GitHub에 올리고, 서버에서 답변을 생성하고, 결과를 GitHub에 게시 |
| **[배포·서버 운영](DEPLOYMENT.md)** | 브랜치/CI/CD, Docker, 서버 디렉터리, 환경 변수, 상태 확인, 장애 복구 |
| **[테스트 가이드](TEST_MANUAL.md)** | 로컬 단위 테스트, 통합 테스트, FAQ/챗봇 API 검증 |

배포와 질문 일괄 테스트는 서로 다른 작업입니다. **질문 CSV 업로드만으로 챗봇 질문이 자동 실행되지는 않습니다.**

## 서비스 구성

- **API:** FastAPI (`app.main:app`)
- **데이터:** PostgreSQL + pgvector (`regulation_chunks`, `faq_chunks`, `phone_contacts`)
- **임베딩:** Ollama `qwen3-embedding:0.6b` (기본 1,024차원)
- **LLM:** Ollama `qwen3:8b` (설정에서 변경 가능)
- **검색:** 벡터·키워드 검색 → RRF 융합 → `BAAI/bge-reranker-v2-m3` 재정렬
- **인덱싱:** PDF 텍스트 추출/OCR → 조문 단위 청킹(조문 표식이 없으면 길이 기준) → 임베딩 저장

실제 모델명과 연결 주소는 환경 변수(`app/core/config.py`)로 설정합니다. 모델을 변경할 때는 DB의 기존 임베딩 차원과 재인덱싱 필요성을 확인해야 합니다.

## 주요 API

| 엔드포인트 | 역할 |
|---|---|
| `POST /api/v1/chat/query` | 규정·FAQ를 검색해 AI 답변과 근거 반환 |
| `GET /api/v1/faqs/search` | FAQ 검색 결과(`faq_id`, 점수) 반환 |
| `POST /api/v1/faqs` | FAQ 생성·수정(upsert), 청킹·임베딩 |
| `DELETE /api/v1/faqs/{faq_id}` | FAQ 청크 삭제 |
| `POST /api/v1/category/recommend` | 프롬프트를 Ollama에 전달하는 카테고리 추천 프록시 |
| `GET /health` | DB 연결 및 규정·FAQ 청크 수 확인 |
| `GET /docs` | FastAPI Swagger UI |

### 질문 요청 예시

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"장학금 지급 기준을 알려줘"}'
```

위 URL은 **로컬에서 FastAPI를 직접 실행하는 경우**의 예시입니다. Docker staging은 기본적으로 API 포트를 외부에 공개하지 않으므로 서버에서는 [CSV 운영 가이드](rag_evaluation/README.md)의 컨테이너 내부 요청 방식을 사용합니다.

## 소스 디렉터리

```text
app/
├── main.py                 # API 앱/health
├── core/                   # 환경 변수
├── chat/                   # 답변 생성·프롬프트
├── search/                 # 하이브리드 검색·reranker
├── faqs/                   # FAQ 저장·검색
├── category/               # 카테고리 API
├── ingestion/              # PDF/OCR/청킹·임베딩
└── infrastructure/         # PostgreSQL 연결·스키마

resources/
├── prompts/chat_answer.txt
└── search/query_expansions.json

rag_evaluation/
├── questions/              # 질문 입력 CSV (GitHub develop에서 관리)
└── results/                # 서버가 생성하는 답변 CSV/JSONL/요약 (Git 추적 제외)

scripts/
├── run_rag_batch.py        # 질문 실행 및 답변 CSV 생성
└── publish_rag_results.py  # 완료된 답변 CSV를 결과 전용 브랜치에 게시

deploy/
├── staging/compose.yaml
├── prod/compose.yaml
└── scripts/                # 배포/원격 소스 동기화

tests/                      # 단위·배포 테스트
```

## 로컬 개발 및 테스트

Python 가상환경과 별도의 **개발용 PostgreSQL/pgvector DB, Ollama**가 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 실제 개발용 DB에 맞게 변경
export DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/DB'
export OLLAMA_BASE_URL='http://localhost:11434'

uvicorn app.main:app --host 127.0.0.1 --port 8000
```

외부 서비스가 필요 없는 단위 테스트만 실행할 경우:

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

상세한 로컬 DB 구성, FAQ/챗봇 통합 테스트는 [TEST_MANUAL.md](TEST_MANUAL.md)를 확인합니다.

## GitHub 브랜치와 배포

| 브랜치/변경 | 동작 |
|---|---|
| 기능 브랜치 → `develop` PR | CI 검사 후 검토·병합 |
| `develop`: Python·설정 등 코드 변경 | Staging Docker 이미지 빌드·배포, 이후 소스 동기화 |
| `develop`: Markdown/질문 CSV만 변경 | Staging의 `~/chatbot/staging` Git 체크아웃만 동기화, Docker 재시작 없음 |
| `develop` → `main` PR | Production 승격 절차 |
| `main` Push | Production 배포 |
| `rag-results/staging` Push | 완료된 답변 CSV 저장; Docker 배포 트리거 없음 |

실제 워크플로는 `.github/workflows/`에 있습니다. 문서·CSV 전용 동기화에서도 **기존 실행 중인 Docker 컨테이너는 변경하지 않으며**, 코드 변경이 포함되면 일반 배포를 수행합니다.

## 운영 설정과 데이터 관리

- **실행 환경:** `/opt/chatbot-service/staging`, `/opt/chatbot-service/prod`
- **배포 서버 소스 체크아웃:** `~/chatbot/staging`, `~/chatbot/prod`
- **환경별 비밀 설정:** `/opt/chatbot-service/<환경>/.env` (GitHub에 커밋 금지)
- **배포 설정/모델 캐시:** Compose와 환경별 Docker 볼륨
- **PDF 자료:** `/opt/chatbot-service/storage/<환경>/documents/`

PDF 인덱싱 명령은 기존 `regulation_chunks`와 `phone_contacts`를 초기화하므로, 데이터 백업과 대상 DB 확인 후 별도로 실행해야 합니다. **일반 코드 배포와 CSV 질문 테스트는 PDF 인덱싱을 자동으로 수행하지 않습니다.**

자세한 설정 및 운영 명령은 [DEPLOYMENT.md](DEPLOYMENT.md)를 참고합니다.
