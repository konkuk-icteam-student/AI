# SSH 기반 CI/CD 및 서버 운영

이 브랜치의 배포 코드는 GitHub-hosted runner에서 SSH로 서버에 접속한다. 서버에 GitHub runner를 설치하지 않는다. 기존 `self-hosted/linux/chatbot-deploy`를 기다리던 방식에서 전환한 구성이다.

2026-10-07 확인: `203.252.168.90:22`, 사용자 `kuai`로 GitHub Actions의 키 로그인과 Docker 조회가 [성공](https://github.com/konkuk-icteam-student/AI/actions/runs/37562035974)했다. 실제 AI 서비스 배포와 데이터 검증은 별도 단계다.

## 1. 배포 흐름

```text
기능 브랜치 → develop PR → CI/리뷰 → develop 병합
→ 단위 테스트 → SHA 이미지 빌드/GHCR 업로드
→ SSH로 같은 SHA의 배포 파일 전송 → 서버 GHCR 로그인
→ staging Compose 실행 → 실행 이미지/health 확인
→ staging 실제 질의 검증 → develop → main PR
→ main 병합 → production 이미지 빌드/SSH 배포
```

`main` 대상 PR의 출발 브랜치는 `develop`으로 제한한다. branch ruleset에 PR·리뷰·필수 CI·force push 제한을 적용해야 실제 병합 정책이 강제된다. CI에는 브랜치 정책, Python/셸 문법, 단위 테스트, staging/prod Compose 설정 검사가 있다.

## 2. 현재 컨테이너·DB 구성

`deploy/staging/compose.yaml`과 `deploy/prod/compose.yaml`에는 API 서비스만 있다. `network_mode`를 생략하므로 각 Compose 프로젝트의 기본 bridge 네트워크를 사용한다. PostgreSQL은 이 Compose가 생성·관리하지 않는다. 챗봇 전용 외부 PostgreSQL/pgvector DB에 `DATABASE_URL`로 연결하며 업무일지 DB로 데이터를 이전하지 않는다.

| 환경 | Compose 프로젝트 | API 컨테이너 포트 기본값 |
|---|---|---|
| staging | chatbot-staging | 18000 |
| production (`prod`) | chatbot-production | 8000 |

서버 `.env`에는 다음 값을 직접 설정한다. URL에 들어가는 비밀번호의 특수문자는 URL 인코딩하고, 셸로 읽을 때 값이 변하지 않도록 전체 URL을 작은따옴표로 감싼다.

```dotenv
DATABASE_URL='postgresql://USER:URL_ENCODED_PASSWORD@CHATBOT_DB_HOST:5432/chatbot'
OLLAMA_BASE_URL='http://HOST_REACHABLE_FROM_API:11434'
```

이 값들은 예시 자리표시자다. 실제 주소는 서버의 네트워크에서 확인한다. bridge 컨테이너 내부의 `127.0.0.1`은 해당 컨테이너이므로 호스트 Ollama 주소로 사용할 수 없다. Ollama 설치·모델·서버 설정은 일반 코드 배포가 변경하지 않는다.

현재 남은 네트워크 배포 과제:

- Compose의 기본 API 바인딩과 배포 검사는 아직 `127.0.0.1` 기준이다. 다른 컨테이너에서 접근하려면 수신 바인딩·공통 네트워크·alias를 함께 정비해야 한다.
- 배포 스크립트의 host-side `/health` 검사도 현재 bridge 구성에 맞게 바꿔야 한다. 코드에 없는 수동 네트워크 연결은 재배포 시 보존되지 않을 수 있다.
- staging과 production의 네트워크 이름과 DB 연결은 실제 서버에서 각각 확인한다. 이 문서나 단위 테스트 통과만으로 운영 연결이 검증된 것은 아니다.

API는 `app` 사용자로 실행한다. 문서는 읽기 전용 마운트, Hugging Face 캐시는 `model-cache`, PaddleX OCR 캐시는 `ocr-cache` 볼륨으로 보관한다. 기존 마운트와 챗봇 DB 네트워크를 보존해야 한다. 모델 초기 로딩과 OCR은 `/health` 검사에 포함되지 않는다.

## 3. GitHub 설정

Repository secrets:

| 이름 | 내용 |
|---|---|
| DEPLOY_HOST | 배포 서버, 현재 203.252.168.90 |
| DEPLOY_PORT | SSH 포트, 현재 22 |
| DEPLOY_USER | 배포 사용자, 현재 kuai |
| DEPLOY_SSH_PRIVATE_KEY | 암호 없는 배포 개인키 전체 |
| DEPLOY_HOST_KEY | 서버 ED25519 공개키 한 줄, `ssh-ed25519 AAAA...`; IP나 fingerprint 제외 |
| PR_AUTOMATION_TOKEN | 규정 갱신·main→develop 동기화 PR 생성용 토큰. AI 저장소의 Contents 및 Pull requests 읽기·쓰기 권한 필요 |

실행 시 알려진 서버 키와 엄격히 대조한다. `ssh-keyscan` 결과를 자동 신뢰하거나 host key 검사를 끄지 않는다. 현재 검증한 ED25519 fingerprint는 `SHA256:ABhropRIcPrirvVJcwgf/kTHeS2jLfMkaLwgsM0D4yg`다.

Environments:

- `staging`: 배포 브랜치 develop
- `production`: 배포 브랜치 main
- 선택 Variables: `STAGING_DEPLOY_ROOT`, `PRODUCTION_DEPLOY_ROOT`; 기본 `/opt/chatbot-service`
- required reviewer는 팀의 운영 정책에 맞게 설정

이미지 빌드는 `packages: write`, SSH 배포 job은 `packages: read`를 사용한다. 해당 실행의 `GITHUB_TOKEN`을 암호화된 SSH stdin으로 전달해 서버 GHCR에 로그인한다. 인증 파일은 서버 임시 디렉터리에만 만들고 배포 종료 시 삭제한다. DB 비밀번호는 서버 `.env`에만 보관한다.

### 규정 자동 갱신의 인증 토큰 누락 해결

`update-regulations.yml`은 checkout, 변경 브랜치 push, PR 생성에 `PR_AUTOMATION_TOKEN`을 사용한다. 이 Secret을 사용할 수 없으면 checkout에서 `Input required and not supplied: token` 오류로 종료하며 PDF 수집도 시작되지 않는다. `sync-main-to-develop.yml`도 같은 Secret을 사용한다.

현재 workflow를 유지하는 최소 변경안은 다음과 같다.

1. GitHub 개인 설정의 Developer settings → Personal access tokens → Fine-grained tokens에서 토큰을 생성한다.
2. Resource owner를 `konkuk-icteam-student`, Repository access를 `Only select repositories` → `AI`로 지정한다. Repository permissions는 `Contents: Read and write`, `Pull requests: Read and write`로 제한한다. 만료일을 지정하고 조직 승인이 필요한 경우 승인을 완료한다. 토큰 소유자도 해당 저장소에 필요한 권한이 있어야 한다.
3. AI 저장소의 Settings → Secrets and variables → Actions → New repository secret에서 이름을 정확히 `PR_AUTOMATION_TOKEN`으로 지정하고 토큰을 등록한다. 이 갱신 job은 Environment를 지정하지 않으므로 staging/production Environment에만 등록한 Secret은 사용하지 못한다. 토큰 값은 코드·문서·대화·로그에 남기지 않는다.
4. Actions → Update Regulation PDFs → Run workflow에서 `main`을 선택해 검증한다. 파일 변경이 없으면 PR을 만들지 않고 종료하는 것이 정상이다. 변경이 있으면 갱신 브랜치 push, develop 대상 PR 생성, 해당 PR의 CI 실행까지 확인한다.
5. 이후 예약 실행도 확인하고, 토큰 만료 또는 담당자 권한 변경 시 Secret을 교체한다. 규정 PDF 갱신 성공과 서버 RAG 인덱스 반영은 별도로 검증한다.

`GITHUB_TOKEN`으로 단순 대체하면 인증 관리가 간단해지지만, 그 토큰으로 생성·갱신한 PR의 CI는 승인 대기 상태가 될 수 있다. 현재 자동 PR 흐름을 유지하려면 위 PAT 방식이 최소 변경안이다. 장기 팀 운영에는 AI 저장소에만 설치한 GitHub App이 적합하다. 이 경우 App 자격 증명을 등록하고 workflow에서 실행별 installation token을 발급받도록 변경해야 하며, 단기 installation token을 고정 Secret으로 저장하지 않는다.

공식 참고: [토큰 생성과 권한 범위](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [GITHUB_TOKEN의 workflow 실행 규칙](https://docs.github.com/en/actions/concepts/security/github_token), [GitHub App의 Actions 인증](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow).

## 4. 최초 서버 준비

관리자가 배포 사용자에게 최상위 경로와 Docker 접근 권한을 준비한다. 이 서버의 kuai는 이미 docker 그룹에 추가되어 비대화식 SSH Docker 조회가 검증됐다.

```bash
sudo mkdir -p /opt/chatbot-service
sudo chown kuai:kuai /opt/chatbot-service
```

검토한 코드의 서버 checkout에서 staging 환경을 만든다.

```bash
bash deploy/scripts/prepare-env.sh staging
```

`Prepare staging environment` Actions도 동일한 스크립트를 SSH stdin으로 전달해 staging 환경을 준비한다. 컨테이너를 시작하지 않으며 설정 내용은 로그로 출력하지 않는다.

이 스크립트는 `.env`를 600 권한으로 생성하고 기존 파일은 덮어쓰지 않는다. 새 파일의 `DATABASE_URL`과 `OLLAMA_BASE_URL`은 비어 있으므로 관리자가 실제 값을 설정해야 한다. DB 계정이나 비밀번호를 새로 만들지 않으며 문서 폴더만 미리 준비한다.

```text
/opt/chatbot-service/staging/.env
/opt/chatbot-service/storage/staging/documents/text_pdf/
/opt/chatbot-service/storage/staging/documents/image_pdf/
```

실제 DB·Ollama 접근 주소를 설정하고 2절의 네트워크 과제를 해결한 뒤 배포한다. 관리자가 지정한 두 모델이 설치되어 있어야 한다. 새 환경 파일 생성만으로 DB나 Ollama 연결이 준비되는 것은 아니다.

운영 환경 준비는 staging 검증 후 `bash deploy/scripts/prepare-env.sh prod`로 별도로 수행한다. main 병합은 production 배포를 시작할 수 있으므로 운영 준비를 먼저 완료한다.

## 5. 실제 SSH 배포 동작

`deploy-ssh.sh`는 환경·SHA·이미지·접속 인자를 검증하고 개인키를 runner 임시 파일에 저장한다. 서버 `.env` 존재를 확인한 뒤 다음 파일들만 SHA별 release 경로에 전송한다.

- staging/prod Compose와 `.env.example`
- deploy.sh, health-check.sh, deploy-remote.sh
- 프롬프트·검색 확장 설정

PDF, 실제 `.env`, 개인키는 이 압축 파일에 포함하지 않는다. 원격 `deploy-remote.sh`는 단기 GHCR 인증을 준비하고 `deploy.sh`를 실행한다.

`deploy.sh`는 설치된 Ollama 모델을 확인하고 설정 파일을 복사한 뒤 `docker compose pull`과 `up --wait --wait-timeout 180`으로 API를 시작한다. 실제 API 컨테이너 이미지가 요청한 SHA인지 확인하고 `/health`를 검사한다. 성공 시 `.current-image`와 `.deployment.env`를 기록한다.

동시 배포는 환경별 하나만 실행한다. 진행 중 배포를 새 실행 때문에 취소하지 않는다. 기본 브랜치에 workflow가 없는 동안은 새로운 `workflow_dispatch`를 시작할 수 없으므로 develop push 또는 준비한 검증 브랜치 trigger를 사용한다. 기능 PR을 develop에 병합하면 staging 배포가 시작된다.

## 6. 상태 확인

배포 성공 후 서버의 해당 환경 디렉터리에서 실행한다.

```bash
cd /opt/chatbot-service/staging

dc() {
  docker compose --env-file .env --env-file .deployment.env -f compose.yaml "$@"
}

dc ps -a
dc images api
dc logs --tail=100 api
dc exec -T api curl --fail http://127.0.0.1:18000/health
cat .current-image
```

production은 디렉터리 `prod`, API 포트 `8000`이다. `.deployment.env`는 첫 성공 전에는 없을 수 있다. 실제 설정 전체를 로그로 출력하면 비밀번호가 노출될 수 있으므로 공유하지 않는다.

health는 DB 연결과 테이블 조회를 확인하며 모델 이름은 설정값을 보여준다. 청크 0건도 health가 성공할 수 있다. 따라서 실제 규정 질의, Ollama의 GPU/모델 응답, reranker 첫 로딩, FAQ 동기화, Spring 연동은 별도 검증한다.

## 7. PDF·데이터 갱신

원본 PDF는 코드 이미지에 포함하지 않는다. 검토한 전체 PDF 집합을 환경별 documents에 배치하고 DB/PDF를 백업한 다음 해당 환경에서 색인한다.

```bash
dc exec -T api python -m app.ingestion.service
```

이 명령은 `regulation_chunks`와 `phone_contacts`를 초기화한다. 현재 전체 교체 방식과 트랜잭션 경계는 유지된다. 일부 문서 처리 실패나 전화번호 저장 실패가 완료 로그와 함께 발생할 수 있으므로 실패 파일·데이터 수·출처·대표 질의를 확인한다. 일반 코드 배포는 색인을 자동 실행하지 않는다. 기존 운영 FAQ도 새 AI DB로 자동 복제되지 않는다.

## 8. 복구 범위

배포 실패 시 `.current-image`의 이전 API 이미지로 복구를 시도하고 실행 이미지/health를 다시 확인한다. 첫 배포는 이전 이미지가 없을 수 있다. 복구에 성공해도 workflow는 실패로 표시된다.

현재 복구는 API 이미지에 대한 동작이다. DB, 스키마, PDF, `.env`, 복사한 Compose·프롬프트·검색 설정은 이전 버전으로 되돌리지 않는다. 데이터 갱신 실패는 검증한 백업과 별도 데이터 복구 절차로 처리한다. `docker compose down -v`를 일반 재배포에 사용하지 않는다.

## 9. 검증과 운영 기록

- PR CI 결과와 실제 SSH 배포 성공을 구분한다.
- 실제 서버 Docker 상태·이미지 SHA·health·청크 수를 기록한다.
- 근거가 있는 대표 질문으로 검색·출처·LLM 응답 시간을 확인한다.
- Spring→AI 실제 연결과 FAQ 생성/수정/삭제 동기화를 확인한다.
- DB/PDF 백업을 별도 환경에 실제 복원해 검증한다.
- 현재/이전 정상 SHA 이미지를 GHCR에 보관하고 복구를 연습한다.

로컬 배포 테스트는 Docker·curl·Ollama를 대체해 환경 보호, 실행 이미지 확인, 실패 시 이전 이미지 복구, 단기 registry 인증 제거를 검사한다. 실제 GPU 질의나 서버 배포를 대신하지 않는다.
