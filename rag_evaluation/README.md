# RAG 질문 CSV 일괄 테스트 및 결과 게시

GitHub `develop`에 질문 CSV를 올리고 서버 Staging 챗봇으로 실행한 뒤, **답변 CSV를 생성하고 결과 전용 GitHub 브랜치에 게시**하는 절차입니다.

## 1. 입력 파일 준비

**GitHub 업로드 경로:** `rag_evaluation/questions/`

```text
rag_evaluation/
├── questions/
│   └── scholarship_questions.csv   # 입력
└── results/                      # 서버가 생성하는 결과 (Git 추적 제외)
```

CSV 인코딩은 UTF-8 또는 UTF-8 BOM을 사용합니다.

| ID | 질문 | 분류 | 기대 정답 |
|---|---|---|---|
| A001 | 지급할 수 있는 장학금 종류는? | 장학금 | 검토용 참고 답변 |
| A002 | 장학금 지급 기준은? | 장학금 | 검토용 참고 답변 |

- 필수 열은 **`질문`**이며 빈 질문은 오류 상태로 남습니다.
- `ID`와 나머지 열은 선택 사항입니다. `ID`를 사용한다면 중복·공백 ID를 만들지 마세요.
- 챗봇에 전달되는 데이터는 **`질문` 열뿐**입니다. 기존 열은 결과 CSV에도 유지됩니다.
- 파일명에는 용도와 날짜를 사용하면 결과를 구분하기 쉽습니다.
- **공개 GitHub 저장소에는 개인정보·사내 비밀·접근 토큰을 포함하지 않습니다.**

## 2. GitHub에서 서버로 질문 CSV 반영

1. [AI 저장소](https://github.com/konkuk-icteam-student/AI)에서 브랜치를 `develop`으로 선택합니다.
2. `rag_evaluation/questions/` 폴더에 CSV를 업로드합니다.
3. 저장소 규칙에 따라 Commit 또는 PR을 통해 `develop`에 변경을 반영합니다.
4. [GitHub Actions](https://github.com/konkuk-icteam-student/AI/actions)의 **Sync staging documentation and question CSVs** 성공을 확인합니다.
5. 서버의 `~/chatbot/staging/rag_evaluation/questions/`에 파일이 생겼는지 확인합니다.

`develop` 변경이 **문서·질문 CSV에만 한정**되면 Docker 재빌드 없이 Git 체크아웃만 동기화됩니다. Python·설정 파일 등이 함께 변경되면 **Deploy staging**이 이미지를 빌드하고 배포 완료 후 소스를 동기화합니다.

```bash
cd ~/chatbot/staging
git branch --show-current       # develop
ls -lh rag_evaluation/questions/
```

**중요:** CSV 업로드/서버 소스 동기화는 질문 실행을 시작하지 않습니다. 아래 명령어를 서버에서 직접 실행해야 합니다.

## 3. 질문 수행과 답변 CSV 생성

서버 `kuai` 계정, Python 3.6 이상, Docker CLI와 정상 실행 중인 `chatbot-staging-api-1` 컨테이너가 필요합니다. 포트를 호스트에 공개할 필요는 없습니다.

**특정 CSV 3문항 시험 실행:**

```bash
cd ~/chatbot/staging
python3 scripts/run_rag_batch.py \
  --input rag_evaluation/questions/scholarship_questions.csv \
  --limit 3
```

**특정 CSV의 모든 문항 실행:**

```bash
python3 scripts/run_rag_batch.py \
  --input rag_evaluation/questions/scholarship_questions.csv
```

**`questions/`에 있는 모든 CSV 일괄 실행:**

```bash
python3 scripts/run_rag_batch.py
```

`--input` 없이 실행하면 `rag_evaluation/questions/*.csv`를 파일명 순서대로 모두 처리합니다. 각 질문은 아래 경로로 전송됩니다.

```text
run_rag_batch.py
  → docker exec -i chatbot-staging-api-1 curl
  → POST http://127.0.0.1:18000/api/v1/chat/query
  → RAG 검색 + LLM 답변
  → 결과 저장
```

**질문 실행과 답변 CSV 생성은 동일 명령어(`run_rag_batch.py`)에서 수행됩니다.** 별도의 답변 생성 명령은 없습니다.

실행 ID는 한국 시간 기준 `YYYYMMDD_HHMMSS`입니다. `--limit 3` 실행에서는 나머지 문항이 `NOT_RUN`으로 기록됩니다. 중단한 결과를 이어서 처리하려면 같은 ID를 지정합니다.

```bash
python3 scripts/run_rag_batch.py \
  --input rag_evaluation/questions/scholarship_questions.csv \
  --run-id YYYYMMDD_HHMMSS
```

실제 실행 ID로 `YYYYMMDD_HHMMSS`를 교체하세요. 같은 질문/ID가 `OK`인 경우 다시 실행하지 않습니다. ID를 생략하면 새로운 결과 파일을 생성합니다.

## 4. 생성되는 결과 파일

**서버 경로:** `/home/kuai/chatbot/staging/rag_evaluation/results/`

입력 `scholarship_questions.csv`가 실행 ID `YYYYMMDD_HHMMSS`로 처리되면 다음 파일이 생성됩니다.

| 파일명 | 역할 |
|---|---|
| `YYYYMMDD_HHMMSS_scholarship_questions_answers.csv` | **최종 검토용**: 원본 열 + AI 답변, 규정/FAQ 근거, 충돌 감지, 응답 시간, API/HTTP 상태, 오류, 처리 시각 |
| `YYYYMMDD_HHMMSS_scholarship_questions_raw.jsonl` | 요청별 API 원본 응답을 누적 기록; 중단 후 이어하기에 사용 |
| `YYYYMMDD_HHMMSS_scholarship_questions_summary.json` | 전체·성공·미처리 및 상태별 건수 |

결과 CSV와 요약 파일은 질문을 처리할 때마다 갱신됩니다. `API 상태=OK`는 API가 정상 응답했다는 뜻이며 **답변의 사실적 정확성을 보증하지 않습니다.** 출처와 예상 답변을 별도 검토하세요.

```bash
# 최근 생성된 결과
ls -lht rag_evaluation/results/

# 해당 실행의 요약 확인 (실제 파일명으로 변경)
cat rag_evaluation/results/*_summary.json
```

## 5. GitHub에 완성된 답변 CSV 게시

코드 브랜치인 `develop`에 자동으로 결과를 Push하지 않습니다. **`rag-results/staging` 결과 전용 브랜치**로만 게시하므로 Docker 배포를 유발하지 않습니다.

| 구분 | GitHub 브랜치 | 경로 |
|---|---|---|
| 질문 입력 | `develop` | `rag_evaluation/questions/` |
| 성공적으로 완료된 답변 CSV | `rag-results/staging` | `rag_evaluation/results/staging/` |
| Raw JSONL·요약 JSON·미완료 답변 | 자동 게시 안 함 | 서버 `rag_evaluation/results/`에 보관 |

**기존 완성 결과 게시:**

```bash
# 업로드 대상 파일 확인
python3 scripts/publish_rag_results.py --environment staging --dry-run

# 실제 게시
python3 scripts/publish_rag_results.py --environment staging
```

**다음부터 질문 실행·답변 저장·완성 결과 게시를 한 번에:**

```bash
python3 scripts/run_rag_batch.py --publish-results

# 특정 입력 파일만 실행하고 완료 결과 게시
python3 scripts/run_rag_batch.py \
  --input rag_evaluation/questions/scholarship_questions.csv \
  --publish-results
```

게시 도구는 해당 실행의 **모든 문항이 `OK`이고 미처리가 0개**일 때만 `*_answers.csv`를 게시합니다. 부분 실행·오류가 남은 파일은 올리지 않습니다. 게시 실패 시 서버 원본 파일은 유지되며 같은 명령으로 재시도할 수 있습니다.

결과 위치: [GitHub RAG Staging 답변](https://github.com/konkuk-icteam-student/AI/tree/rag-results/staging/rag_evaluation/results/staging)

### GitHub 게시용 SSH 인증 (서버 계정당 준비)

질문 실행 자체에는 SSH 게시 키가 필요하지 않습니다. 결과 게시에는 **쓰기 권한이 있는 GitHub Deploy Key**가 필요합니다.

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
# 이미 ai-rag-results 키가 있으면 다시 생성하지 마세요.
ssh-keygen -t ed25519 -f ~/.ssh/ai-rag-results -N '' -C 'AI RAG results'
chmod 600 ~/.ssh/ai-rag-results

# GitHub Settings → Deploy keys에 .pub 파일 한 줄을 등록, Allow write access
cat ~/.ssh/ai-rag-results.pub

# GitHub 공식 호스트 키를 확인해 known_hosts에 신뢰 등록한 뒤 인증 시험
ssh -i ~/.ssh/ai-rag-results -o IdentitiesOnly=yes -T git@github.com
```

개인키(`~/.ssh/ai-rag-results`)는 절대로 공유하거나 Git에 커밋하지 않습니다. 이미 키를 등록했다면 생성 단계는 건너뜁니다.

## 6. 오류 확인

| 상황 | 확인 |
|---|---|
| GitHub에 올렸으나 서버에 CSV가 없음 | `develop` 반영 여부 및 Actions 동기화/배포 성공 확인 |
| `CSV에 질문 열이 없습니다` | 열 이름이 정확히 `질문`인지 확인 |
| Docker/API 연결 오류 | `docker ps --filter name=chatbot-staging-api-1` 및 컨테이너 health 확인 |
| `NOT_RUN` | `--limit` 사용 여부와 실행 ID 확인 |
| 결과 게시 대상 없음 | `summary.json`에 `success=total`, `not_run=0`인지 확인 |
| SSH 게시 오류 | Deploy Key 쓰기 권한, 개인키 경로·권한, GitHub 호스트 키 확인 |

자동 테스트는 `python3 -m unittest tests.test_rag_batch_runner tests.test_rag_results_publisher -v`로 실행할 수 있습니다.
