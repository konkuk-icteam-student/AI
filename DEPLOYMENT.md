# AI RAG CI/CD 및 서버 운영 가이드

이 문서는 **현재 GitHub Actions + SSH + Docker Compose 배포 구조**의 운영 절차를 설명합니다. 과거 서버 이력이나 특정 장애 해결 로그는 포함하지 않습니다.

- 서비스 기능과 로컬 실행: [README](README.md)
- 질문 CSV 업로드·답변 생성·결과 게시: [RAG 운영 가이드](rag_evaluation/README.md)
- 로컬/통합 검증: [TEST_MANUAL](TEST_MANUAL.md)

## 1. 브랜치와 CI/CD

```text
feature/* → PR develop → CI → Merge
                         ├─ 코드/설정 변경 → 이미지 빌드 → GHCR → SSH/Compose Staging 배포
                         └─ .md·질문 CSV만 변경 → SSH/Git 소스 동기화 (Docker 재시작 없음)

develop → PR main → CI → Merge → 이미지 빌드 → GHCR → SSH/Compose Production 배포

rag-results/staging → 결과 CSV 보관 (Docker 배포 트리거 없음)
```

| 워크플로 | 트리거 | 기능 |
|---|---|---|
| `ci.yml` | `develop` / `main` 대상 PR | 브랜치 정책, 문법, 테스트, Compose 검증 |
| `deploy-staging.yml` | `develop`의 코드/설정 변경, 수동 실행 | 이미지 빌드·배포 후 소스 체크아웃 동기화 |
| `sync-staging-content.yml` | `develop`의 문서·질문 CSV 변경 | 내용 전용 변경이면 서버 Git 체크아웃만 갱신 |
| `deploy-production.yml` | `main` Push, 수동 실행 | Production 이미지 빌드·배포 |
| `update-regulations.yml` | 예약/수동 실행 | 규정 PDF 변경을 PR로 제출 (인덱싱은 별개) |
| `sync-main-to-develop.yml` | `main` Push | 필요할 때 `develop` 역동기화 PR 생성 |

기타 점검용 워크플로는 `.github/workflows/`를 참조합니다. `main` 대상 PR은 `develop`에서 생성하도록 CI에서 검사합니다. 실제 브랜치 보호는 저장소 ruleset도 함께 설정해야 합니다.

### Staging에서 Docker 배포를 생략하는 조건

다음 파일 **만** 변경된 Push는 서버의 `~/chatbot/staging` 소스를 fast-forward하며 Docker 컨테이너를 수정하지 않습니다.

- Markdown: `**/*.md`
- 질문 CSV: `rag_evaluation/questions/*.csv` 및 하위 디렉터리의 CSV

Python, Dockerfile, Compose, workflow, 프롬프트 등 다른 파일의 변경이 **하나라도 포함되면 일반 Docker 배포**로 처리합니다.

소스 전용 동기화는 `.current-image`에 기록된 실행 중 이미지의 커밋과 목표 커밋을 비교합니다. 중간에 코드 변경이 발견되면 동기화를 중지합니다. 전체 배포와 같은 서버 잠금을 사용해 동시 파일 갱신을 방지합니다. **문서·질문 CSV를 동기화해도 RAG 배치 질문은 자동 시작되지 않습니다.**

`main` Production의 일반 배포 정책은 별개이며, Staging의 내용 전용 필터와 혼동하지 않습니다.

## 2. 서버 디렉터리와 데이터

```text
/opt/chatbot-service/
├── staging/
│   ├── .env                    # 서버 관리, Git 추적 대상 아님
│   ├── compose.yaml
│   ├── .current-image          # 배포 성공한 이미지 SHA
│   └── .deployment.env         # Compose 이미지·마운트 정보
├── prod/                       # Production도 동일한 구조
├── storage/{staging,prod}/documents/
│   ├── text_pdf/
│   └── image_pdf/
├── config/{staging,prod}/
└── releases/{staging,prod}/

/home/kuai/chatbot/
├── staging/                    # develop checkout, 질문/결과 실행
└── prod/                       # main checkout
```

- `/opt/chatbot-service`: **실행 중 서비스·데이터**를 관리합니다.
- `~/chatbot/staging`: **Git 소스·질문 CSV·테스트 결과**를 관리합니다. 이 폴더에서 Python 배치를 실행합니다.
- `rag_evaluation/results/`는 Git 무시 대상입니다. 소스 동기화 후에도 답변 CSV·JSONL·요약 파일이 유지됩니다.
- Docker 캐시와 DB 영구 데이터는 Compose 설정과 실제 볼륨 구성에 따라 별도로 관리합니다.

## 3. 서버 환경 변수와 네트워크

각 환경의 설정은 관리자가 `/opt/chatbot-service/<환경>/.env`에 저장합니다.

```dotenv
DATABASE_URL='postgresql://USER:URL_ENCODED_PASSWORD@DB_HOST:5432/DB_NAME'
OLLAMA_BASE_URL='http://OLLAMA_HOST_REACHABLE_FROM_CONTAINER:11434'
EMBEDDING_MODEL=qwen3-embedding:0.6b
LLM_MODEL=qwen3:8b
API_BIND_ADDRESS=127.0.0.1
API_PORT=18000
```

위 `API_PORT=18000`은 **Staging 예시**이고 Production 기본값은 `8000`입니다. `DATABASE_URL`의 특수문자 비밀번호는 URL 인코딩합니다. 실제 DB와 모델은 환경별로 확인하세요.

**DB 구성의 기준은 해당 브랜치의 `deploy/<환경>/compose.yaml`입니다.** 현재 환경의 DB가 Compose 내부 서비스인지 별도 컨테이너인지 확인한 뒤 호스트명을 지정합니다. Staging과 Production은 DB 계정·데이터를 분리하고 업무일지 DB를 재사용하지 않습니다.

`OLLAMA_BASE_URL`은 **API 컨테이너 내부에서 접근 가능한** 주소여야 합니다. 컨테이너 안의 `127.0.0.1`은 호스트 서버를 가리키지 않습니다. 호스트 Ollama를 쓰는 경우 Docker의 `host.docker.internal` 매핑 또는 접근 가능한 호스트 네트워크 주소와 실제 listen 설정을 검증합니다.

API 포트는 기본적으로 호스트에 publish하지 않습니다. API 바인딩/네트워크를 바꾸려면 Compose·배포 사전 검사·호출 클라이언트를 함께 검토해야 합니다. 단순히 외부 포트를 열지 않습니다.

관리자 준비:

```bash
# /opt/chatbot-service 쓰기 권한은 배포 계정에 부여되어 있어야 함
bash deploy/scripts/prepare-env.sh staging
bash deploy/scripts/prepare-env.sh prod
```

`prepare-env.sh`는 비어 있는 환경설정을 생성하지만 **기존 .env 파일을 덮어쓰지 않습니다.** 실행 후 DB·Ollama 주소와 설치 모델을 관리자가 설정해야 합니다. 실제 `.env`와 SSH 개인키는 커밋하지 않습니다.

## 4. GitHub Actions 설정

배포용 GitHub Secrets(Repository 또는 Environment에 실제 workflow와 맞춰 배치):

| Secret / Variable | 역할 |
|---|---|
| `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER` | SSH 서버 주소·포트·계정 |
| `DEPLOY_SSH_PRIVATE_KEY` | Actions → 서버 접속용 개인키 |
| `DEPLOY_HOST_KEY` | 신뢰할 수 있는 서버 ED25519 공개키 |
| `PR_AUTOMATION_TOKEN` | 자동 규정 PR 및 main→develop 동기화 PR 생성 |
| `STAGING_DEPLOY_ROOT`, `PRODUCTION_DEPLOY_ROOT` | 선택 변수; 기본 `/opt/chatbot-service` |

서버 호스트 키는 검증된 지문과 대조하며 `StrictHostKeyChecking`을 해제하지 않습니다. `GITHUB_TOKEN`은 CI 빌드의 GHCR 인증에 사용합니다. 규정 갱신·PR 동기화 워크플로가 사용할 `PR_AUTOMATION_TOKEN`은 저장소 범위를 제한하고 만료/권한을 관리해야 합니다.

**RAG 답변 CSV 게시용 SSH Deploy Key**는 위 서버 배포 키와 별개입니다. 해당 키 설정은 [RAG 운영 가이드](rag_evaluation/README.md)를 참고합니다.

## 5. Docker 배포 절차

일반 코드 Push 시의 흐름:

1. GitHub Actions에서 단위 테스트를 실행하고 커밋 SHA로 Docker 이미지를 빌드합니다.
2. GHCR에 이미지를 게시하고 SSH로 필요한 Compose/설정 파일만 해당 SHA의 release 디렉터리에 전달합니다.
3. 서버에서 모델 존재·환경 변수 등을 확인한 뒤 Docker Compose로 이미지를 가져와 시작합니다.
4. `/health` 및 실제 이미지 태그를 검사하여 성공을 확정합니다.
5. `.current-image`·`.deployment.env`를 기록하고 소스 체크아웃을 같은 SHA로 fast-forward합니다.

실제 비밀번호·PDF 원본·GitHub 개인키는 release 아카이브에 포함하지 않습니다. CI가 성공해도 서버 네트워크, Ollama 품질, 검색 정확성까지 보장하는 것은 아닙니다.

### 상태 확인 (Staging)

```bash
cd /opt/chatbot-service/staging
docker ps --filter name=chatbot-staging

dc() {
  docker compose --env-file .env --env-file .deployment.env -f compose.yaml "$@"
}

dc ps
dc logs --tail=100 api
dc exec -T api curl --fail http://127.0.0.1:18000/health
cat .current-image

# 서버의 develop 소스 동기화 확인
git -C ~/chatbot/staging branch --show-current
git -C ~/chatbot/staging rev-parse --short HEAD
```

Production은 `/opt/chatbot-service/prod`와 API 내부 포트 `8000`을 사용합니다. 첫 배포 전에는 `.deployment.env`와 `.current-image`가 없을 수 있습니다. 설정 전체를 로그에 출력해 DB 비밀번호를 유출하지 마세요.

### 실패 시 확인 순서

1. GitHub Actions의 해당 빌드·배포 job에서 **어느 단계가 실패했는지** 확인합니다.
2. 서버에서 `docker ps`, `docker logs`, `.current-image`, Compose health 상태를 확인합니다.
3. SSH/배포 실패와 소스 체크아웃 동기화 실패를 구분합니다. 소스 동기화가 실패해도 이미 건강하게 기동한 Docker 컨테이너가 유지될 수 있습니다.
4. 설정 변경이 필요하면 서버 `.env`를 수정하고 해당 배포 워크플로를 재실행합니다.

## 6. PDF 인덱싱·복구 원칙

PDF 인덱싱은 배포와 독립된 작업입니다. 환경별 문서 폴더 및 DB 백업을 확인한 뒤 승인된 작업 시간에 실행합니다.

```bash
cd /opt/chatbot-service/staging
# 위에서 정의한 dc() 함수 사용
dc exec -T api python -m app.ingestion.service
```

**이 명령은 기존 `regulation_chunks`와 `phone_contacts`를 비우고 새 데이터를 저장**하므로 대상 DB·PDF 목록·백업을 먼저 확인합니다. 부분적인 PDF 처리 실패가 있더라도 인덱싱 완료 로그가 나올 수 있으므로 실패 목록·청크 수·출처를 따로 검증합니다.

배포 오류 시 배포 스크립트는 이전 API 이미지로 복구를 시도합니다. **DB·PDF·환경설정·스키마까지 자동 롤백하지는 않습니다.** Docker 볼륨 삭제(`docker compose down -v`, `docker volume prune`)는 일반 배포·장애 복구 절차에 포함하지 않습니다.
