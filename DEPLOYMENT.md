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

## 2. 같은 서버의 Ollama와 AI 전용 DB

서버의 기존 Ollama는 `127.0.0.1:11434`에서 실행되고 `qwen3-embedding:0.6b`, `qwen3:8b`가 설치돼 있다. API 컨테이너는 Linux의 host networking을 사용해 이 Ollama와 루프백 DB에 접근한다. Ollama 설치·모델 파일·서비스 설정은 이 배포가 변경하지 않는다. 실제 GPU 사용과 응답 시간은 질의로 검증한다.

| 환경 | Compose 프로젝트 | API 주소 | AI 전용 DB 주소 |
|---|---|---|---|
| staging | chatbot-staging | 127.0.0.1:18000 | 127.0.0.1:15433 |
| production (`prod`) | chatbot-production | 127.0.0.1:8000 | 127.0.0.1:25433 |

DB는 환경별 `pgvector/pgvector:pg17` named volume을 사용한다. 기존 CommuteMate DB와는 별개다. API와 DB를 외부 공개하지 않으며 같은 서버에서 실행되는 Spring은 운영 AI를 `http://127.0.0.1:8000`으로 연결할 수 있다. 실제 Spring 실행 네트워크와 `RAG_SERVICE_URL`은 운영 전 확인한다. 다른 서버나 bridge 네트워크의 컨테이너에서 사용할 경우 해당 클라이언트의 localhost는 AI 서버를 의미하지 않는다.

## 3. GitHub 설정

Repository secrets:

| 이름 | 내용 |
|---|---|
| DEPLOY_HOST | 배포 서버, 현재 203.252.168.90 |
| DEPLOY_PORT | SSH 포트, 현재 22 |
| DEPLOY_USER | 배포 사용자, 현재 kuai |
| DEPLOY_SSH_PRIVATE_KEY | 암호 없는 배포 개인키 전체 |
| DEPLOY_HOST_KEY | 서버 ED25519 공개키 한 줄, `ssh-ed25519 AAAA...`; IP나 fingerprint 제외 |

실행 시 알려진 서버 키와 엄격히 대조한다. `ssh-keyscan` 결과를 자동 신뢰하거나 host key 검사를 끄지 않는다. 현재 검증한 ED25519 fingerprint는 `SHA256:ABhropRIcPrirvVJcwgf/kTHeS2jLfMkaLwgsM0D4yg`다.

Environments:

- `staging`: 배포 브랜치 develop
- `production`: 배포 브랜치 main
- 선택 Variables: `STAGING_DEPLOY_ROOT`, `PRODUCTION_DEPLOY_ROOT`; 기본 `/opt/chatbot-service`
- required reviewer는 팀의 운영 정책에 맞게 설정

이미지 빌드는 `packages: write`, SSH 배포 job은 `packages: read`를 사용한다. 해당 실행의 `GITHUB_TOKEN`을 암호화된 SSH stdin으로 전달해 서버 GHCR에 로그인한다. 인증 파일은 서버 임시 디렉터리에만 만들고 배포 종료 시 삭제한다. DB 비밀번호는 서버 `.env`에만 보관한다.

규정 업데이트·main→develop 동기화 PR에는 기존 `PR_AUTOMATION_TOKEN` 설정이 필요하다. 현재 main의 예약 작업과 develop의 PR 방식이 다른 경우, main 반영 시 보호 규칙과 토큰 권한을 함께 정리한다.

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

이 스크립트는 64자리 랜덤 hex DB 비밀번호를 생성해 `.env`를 600 권한으로 저장한다. 비밀번호를 출력하지 않으며 기존 `.env`는 덮어쓰지 않는다. 읽기 전용으로 마운트할 문서 폴더도 미리 만들고 컨테이너 사용자가 읽을 수 있게 준비한다.

```text
/opt/chatbot-service/staging/.env
/opt/chatbot-service/storage/staging/documents/text_pdf/
/opt/chatbot-service/storage/staging/documents/image_pdf/
```

API·AI DB 포트가 다른 프로세스에 사용 중인지 확인한다. 현재 API 호스트 모드에 대해 컨테이너 port mapping을 추가하면 안 된다. Ollama API에 연결되고 지정한 두 모델이 설치되어 있어야 배포가 진행된다.

운영 환경 준비는 staging 검증 후 `bash deploy/scripts/prepare-env.sh prod`로 별도로 수행한다. main 병합은 production 배포를 시작할 수 있으므로 운영 준비를 먼저 완료한다.

## 5. 실제 SSH 배포 동작

`deploy-ssh.sh`는 환경·SHA·이미지·접속 인자를 검증하고 개인키를 runner 임시 파일에 저장한다. 서버 `.env` 존재를 확인한 뒤 다음 파일들만 SHA별 release 경로에 전송한다.

- staging/prod Compose와 `.env.example`
- deploy.sh, health-check.sh, deploy-remote.sh
- 프롬프트·검색 확장 설정

PDF, 실제 `.env`, 개인키는 이 압축 파일에 포함하지 않는다. 원격 `deploy-remote.sh`는 단기 GHCR 인증을 준비하고 `deploy.sh`를 실행한다.

`deploy.sh`는 설치된 Ollama 모델을 확인하고 설정 파일을 복사한 뒤 `docker compose pull`과 `up --wait --wait-timeout 180`으로 API와 DB를 시작한다. 실제 API 컨테이너 이미지가 요청한 SHA인지 확인하고 `/health`를 검사한다. 성공 시 `.current-image`와 `.deployment.env`를 기록한다.

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
curl --fail http://127.0.0.1:18000/health
cat .current-image
```

production은 디렉터리 `prod`, API 포트 `8000`이다. `.deployment.env`는 첫 성공 전에는 없을 수 있다. 실제 설정 전체를 로그로 출력하면 비밀번호가 노출될 수 있으므로 공유하지 않는다.

health는 DB 연결과 테이블 조회를 확인하며 모델 이름은 설정값을 보여준다. 청크 0건도 health가 성공할 수 있다. 따라서 실제 규정 질의, Ollama의 GPU/모델 응답, reranker 첫 로딩, FAQ 동기화, Spring 연동은 별도 검증한다.

## 7. PDF·데이터 갱신

원본 PDF는 코드 이미지에 포함하지 않는다. 검토한 전체 PDF 집합을 환경별 documents에 배치하고 DB/PDF를 백업한 다음 해당 환경에서 색인한다.

```bash
dc exec -T api python -m app.ingest
```

이 명령은 `regulation_chunks`와 `phone_contacts`를 초기화한다. 일부 문서 처리 실패나 전화번호 저장 실패가 완료 로그와 함께 발생할 수 있으므로 실패 파일·데이터 수·출처·대표 질의를 확인한다. 일반 코드 배포는 색인을 자동 실행하지 않는다. 기존 운영 FAQ도 새 AI DB로 자동 복제되지 않는다.

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
