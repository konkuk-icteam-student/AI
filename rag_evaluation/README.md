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

Python 3.6 이상, Docker CLI, 실행 중인 RAG 컨테이너의 `curl`이 필요합니다. **호스트 서버**에서 실행하므로 외부 포트를 공개할 필요가 없습니다.

```bash
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

응답의 정답 여부는 자동 채점하지 않습니다. 결과는 실행한 컴퓨터의 저장소 복제본에 기록되며 GitHub에 자동 push되지 않습니다. 공개 전 데이터의 민감정보를 확인하세요.

## 단위 테스트

```bash
python3 -m unittest tests.test_rag_batch_runner -v
```
