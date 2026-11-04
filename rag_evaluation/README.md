# 규정 RAG 질문 일괄 테스트

이 디렉터리는 RAG 검증용 질문지와 실행 결과를 분리해 저장합니다. DB를 수정하지 않습니다.

```text
rag_evaluation/
├── questions/  # 입력 CSV (날짜·시간을 포함한 파일명)
└── results/    # 답변 CSV, 원본 JSONL, 상태 요약 JSON
scripts/run_rag_batch.py  # 일괄 실행 스크립트
```

## 질문 CSV

- 필수 열: `질문`
- 선택 열: `ID`, `분류`, `기대 정답 / 기대 응답`, `근거 규정`, `근거 조항` 등
- 인코딩: UTF-8 또는 UTF-8 BOM (`utf-8-sig`). 질문지의 나머지 열도 결과 CSV에 유지합니다.
- **AI 요청에는 `질문`만 전달합니다.** 기대 정답과 판정 기준은 전달하지 않습니다.
- 새 질문지는 `questions/YYYYMMDD_HHMMSS_설명.csv` 형식의 이름을 권장합니다.

## 서버에서 실행

`develop` 배포가 성공하면 `/home/kuai/chatbot/staging`에 이 저장소가 자동으로 동기화됩니다. `main` 배포가 성공하면 `/home/kuai/chatbot/prod`에 동기화됩니다. 두 폴더는 Git 브랜치를 추적하며 결과 파일은 유지합니다. Docker 배포 경로 `/opt/chatbot-service`와는 별개입니다.

Python 3.6 이상, Docker CLI, 실행 중인 RAG 컨테이너의 `curl`이 필요합니다. **호스트 서버**에서 실행하므로 외부 포트를 공개할 필요가 없습니다.

```bash
# staging 서버에서 소스 브랜치 확인
cd ~/chatbot/staging
git branch --show-current  # develop
# 질문 CSV 중 3문항만 먼저 테스트
python3 scripts/run_rag_batch.py --input rag_evaluation/questions/20261104_161326_regulations_1-0-1_1-0-9.csv --limit 3

# questions/ 폴더의 모든 CSV 전체 실행
python3 scripts/run_rag_batch.py

# 중단된 실행 이어하기 (처음 출력된 run-id 입력)
python3 scripts/run_rag_batch.py --run-id 20261104_154500

# 컨테이너/컨테이너 내부 API 주소 변경
python3 scripts/run_rag_batch.py --container chatbot-staging-api-1 --url http://127.0.0.1:18000/api/v1/chat/query
```

기본 API는 `chatbot-staging-api-1` 컨테이너의 `http://127.0.0.1:18000/api/v1/chat/query`입니다.
새 실행 시 `Asia/Seoul`의 현재 시각을 사용한 실행 ID (`YYYYMMDD_HHMMSS`)가 생성됩니다.

## 결과 파일

입력 파일당 `rag_evaluation/results/`에 다음 세 파일이 생성됩니다.

- `YYYYMMDD_HHMMSS_질문파일명_answers.csv` — 원본 열 + AI 답변·규정 출처·FAQ 출처·응답시간·상태
- `YYYYMMDD_HHMMSS_질문파일명_raw.jsonl` — 요청별 원본 JSON 응답 (진행 중에도 기록)
- `YYYYMMDD_HHMMSS_질문파일명_summary.json` — 성공·미처리·오류 상태별 개수

`--limit 3` 이후 **같은 결과 파일에 이어서 실행**하려면 첫 실행의 `--run-id`를 재사용하세요. 성공 처리된 ID/질문은 재실행 시 건너뜁니다. 기본 실행은 항상 새 결과 파일을 만듭니다.

응답의 정답 여부는 자동 채점하지 않습니다. 기본 실행은 서버에만 저장되며, `--publish-results`를 지정하면 완료된 답변 CSV를 GitHub 결과 브랜치에 업로드합니다. 공개 전 데이터의 민감정보를 확인하세요.

## 완료된 답변 CSV를 GitHub에 자동 Push

**배포를 재실행하지 않도록** CSV 결과는 코드 브랜치(develop/main)가 아니라
`rag-results/staging` 또는 `rag-results/prod` 브랜치로만 업로드합니다.
업로드되는 것은 *모든 질문에 HTTP 200으로 답변한* `*_answers.csv` 파일뿐입니다.
미완료(--limit 3 포함), 요청 실패가 남은 CSV, 원본 JSONL, 요약 JSON은 업로드하지 않습니다.
결과 업로드로 Docker CI/CD가 다시 시작되지 않습니다.

**주의: 이 저장소는 공개 저장소입니다.** GitHub에 Push된 질문/AI 답변/출처는
누구나 열람할 수 있습니다. 비공개 정보가 섞인 결과는 업로드하지 마세요.

### 처음 한 번: 서버에서 쓰기 전용 배포 키 준비

기존 CI/CD가 서버에 접속하는 SSH 키와 **별개**로, 결과 Push용 GitHub
Deploy Key를 만들어 사용합니다. 개인키를 GitHub 저장소나 대화에 붙여 넣지 않습니다.

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/ai-rag-results -N '' -C 'AI RAG results'
chmod 600 ~/.ssh/ai-rag-results
cat ~/.ssh/ai-rag-results.pub
```

출력된 **공개키(`*.pub`)만** GitHub
**AI 저장소 → Settings → Deploy keys → Add deploy key**에 등록하고
**Allow write access**를 켭니다. 이 키는 AI 저장소에만 Push할 수 있어야 합니다.

다음으로 SSH 호스트 키를 확인한 뒤 GitHub 접속을 한 번 검증합니다.
GitHub의 공식 SSH 호스트 키 지문과 화면에 표시된 지문이 일치할 때만 신뢰하세요.

```bash
ssh -i ~/.ssh/ai-rag-results -o IdentitiesOnly=yes -T git@github.com
```

GitHub의 "successfully authenticated" 문구가 보이면 권한 확인이 된 것입니다.
서버의 `~/.ssh/known_hosts`에 GitHub의 검증된 호스트 키가 있어야
업로드 도중 호스트 키 검증이 실패하지 않습니다.

### 질문 테스트와 업로드를 한 번에

```bash
cd ~/chatbot/staging
python3 scripts/run_rag_batch.py --publish-results

# 기존에 이미 완료된 결과 CSV만 Push (다시 질문하지 않음)
python3 scripts/publish_rag_results.py --environment staging

# 실제 업로드 전에 공개될 파일 목록만 미리 확인
python3 scripts/publish_rag_results.py --environment staging --dry-run

# 특정 테스트 실행만 게시
python3 scripts/publish_rag_results.py --environment staging --run-id 20261104_165000
```

Production 결과라면 실행 인수에서 `--environment prod`를 사용합니다.
API 테스트 실행과 연동할 때는 `--publish-environment prod`를 사용하세요.
Staging/Production을 혼동하지 않도록 테스트 대상 컨테이너와 URL도 함께 확인해야 합니다.

서버에서 결과 폴더는 여전히 Git 무시 대상이며, 업로드 코드는 임시 작업 폴더를
사용해 결과 브랜치에 파일을 커밋합니다. **현재 develop/main 체크아웃을 변경하지 않습니다.**
업로드가 실패해도 서버의 CSV는 삭제되지 않습니다. 같은 명령으로 재시도할 수 있으며
이미 GitHub에 올라간 동일한 CSV는 중복 커밋하지 않습니다.

GitHub 저장 위치:
- `rag-results/staging` 브랜치 → `rag_evaluation/results/staging/*_answers.csv`
- `rag-results/prod` 브랜치 → `rag_evaluation/results/prod/*_answers.csv`

## 단위 테스트

```bash
python3 -m unittest tests.test_rag_batch_runner -v
```
