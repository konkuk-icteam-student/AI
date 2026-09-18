# CI/CD 및 자체 서버 배포 매뉴얼

## 1. 브랜치 흐름

```text
feature/*, fix/*, jonghwa
        -> PR to develop
        -> CI + review + staging deployment
        -> PR from develop to main
        -> CI + review
        -> merge to main
        -> production deployment
```

`develop`과 `main`에는 직접 push하지 않는다. `main`을 대상으로 하는 PR은 CI가 출발 브랜치가 `develop`인지 검사한다.

## 2. GitHub 저장소 설정

### Environments

`Settings -> Environments`에서 다음 환경을 만든다.

- `staging`: deployment branch를 `develop`으로 제한
- `production`: deployment branch를 `main`으로 제한

완전 자동 배포를 사용하려면 required reviewer는 지정하지 않는다. 운영 배포 전 승인이 필요해지면 `production` 환경에 reviewer를 추가한다.

### Branch rulesets

`develop`과 `main`에 다음 규칙을 적용한다.

- Require a pull request before merging
- Require at least one approval
- Require conversation resolution before merging
- Require status checks to pass
- Require branches to be up to date before merging
- Block force pushes and branch deletion

필수 상태 검사는 아래 다섯 개다.

- `CI / Validate release source`
- `CI / Python syntax`
- `CI / Unit tests`
- `CI / Docker Compose validation (staging)`
- `CI / Docker Compose validation (prod)`

### 자동 PR용 Secret

GitHub의 기본 `GITHUB_TOKEN`으로 만든 PR은 다른 workflow를 새로 실행시키지 않는다. 규정집 갱신 PR과 `main -> develop` 동기화 PR에서도 CI가 실행되도록 저장소 쓰기 권한과 PR 쓰기 권한을 가진 fine-grained PAT 또는 GitHub App token을 다음 Secret으로 등록한다.

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
PR_AUTOMATION_TOKEN=<token>
```

개인 PAT보다 팀 소유 GitHub App token이 장기 운영에는 더 적합하다. PAT를 사용한다면 만료일과 교체 담당자를 정한다.

## 3. Self-hosted runner 준비

PR에서 실행되는 CI와 이미지 빌드는 GitHub-hosted runner가 담당한다. 팀 서버의 self-hosted runner는 검증된 이미지의 배포에만 사용한다.

팀 서버에 Docker Engine, Docker Compose plugin, curl을 설치하고 GitHub 안내에 따라 runner를 등록한다. runner에는 다음 label을 추가한다.

```text
self-hosted
linux
chatbot-deploy
```

runner 전용 사용자가 배포 디렉터리와 Docker를 사용할 수 있어야 한다.

```bash
sudo mkdir -p /opt/chatbot-service
sudo chown -R <runner-user>:<runner-user> /opt/chatbot-service
sudo usermod -aG docker <runner-user>
```

그룹 변경 후 runner 서비스를 재시작하거나 서버 사용자를 다시 로그인한다.

GitHub Environment variable은 필요할 때 다음과 같이 설정한다.

```text
staging.STAGING_DEPLOY_ROOT=/opt/chatbot-service
production.PRODUCTION_DEPLOY_ROOT=/opt/chatbot-service
```

## 4. 최초 배포 환경 파일

첫 자동 배포는 환경별 `.env` 예제 파일을 서버에 만든 뒤 의도적으로 실패한다. 서버 관리자가 비밀번호를 바꾼 다음 Actions를 다시 실행한다.

```text
/opt/chatbot-service/staging/.env
/opt/chatbot-service/prod/.env
```

최소 설정은 다음과 같다.

```dotenv
POSTGRES_DB=chatbot
POSTGRES_PASSWORD=<long-random-password>
API_BIND_ADDRESS=0.0.0.0
API_PORT=18000
EMBEDDING_MODEL=qwen3-embedding:0.6b
LLM_MODEL=qwen3:8b
```

production은 기본 포트가 `8000`이다. `.env` 파일은 Git에 올리지 않는다.

## 5. 자동 배포 동작

1. GitHub-hosted runner가 merge commit SHA를 태그로 Docker 이미지를 빌드한다.
2. 이미지를 GitHub Container Registry(GHCR)에 push한다.
3. self-hosted runner가 이미지를 pull한다.
4. 환경별 Compose stack을 갱신한다.
5. `/health`가 정상 응답할 때까지 확인한다.
6. 실패하면 `.current-image`에 기록된 이전 이미지로 rollback한다.

동시 배포는 environment별 하나만 실행되며 진행 중인 운영 배포는 새 실행이 생겨도 취소하지 않는다.

## 6. 데이터와 설정 위치

서버의 영속 데이터는 checkout 디렉터리 밖에 둔다.

```text
/opt/chatbot-service/
|-- staging/.env
|-- prod/.env
|-- storage/staging/documents/
|-- storage/prod/documents/
|-- config/staging/
`-- config/prod/
```

프롬프트와 검색 확장 설정은 배포할 때 각 환경의 `config` 디렉터리에 복사되어 컨테이너에 read-only로 연결된다.

규정 PDF는 자동 배포 이미지에 포함하지 않는다. 검증된 PDF를 환경별 `documents` 디렉터리에 배치한 다음 별도의 백업과 재색인 절차를 수행해야 한다. 현재 전체 규정 인제스트는 `regulation_chunks`를 초기화하므로 일반 코드 배포 과정에서는 자동 실행하지 않는다.

## 7. 장애 확인

```bash
cd /opt/chatbot-service/prod
docker compose --env-file .env --env-file .deployment.env ps
docker compose --env-file .env --env-file .deployment.env logs --tail=200 api
curl --fail http://127.0.0.1:8000/health
```

배포 스크립트가 rollback에 성공해도 `main`의 코드는 자동으로 되돌아가지 않는다. 원인을 수정한 hotfix를 `develop`에 반영한 뒤 다시 `develop -> main` PR을 만들거나 문제 merge commit을 revert해야 한다.
