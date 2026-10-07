#!/usr/bin/env bash
set -euo pipefail

expected_image_sha="${1:?expected staging image SHA is required}"
document_sha="${2:?verified document source SHA is required}"
for revision in "${expected_image_sha}" "${document_sha}"; do
    [[ "${revision}" =~ ^[a-f0-9]{40}$ ]] || { echo "Invalid revision" >&2; exit 1; }
done
api=chatbot-staging-api-1
postgres=chatbot-staging-postgres-1
runtime=/opt/chatbot-service/staging
documents=/opt/chatbot-service/storage/staging/documents
incoming="/opt/chatbot-service/imports/staging/${document_sha}"

umask 077
exec 9>"${runtime}/.ingest.lock"
flock -n 9 || { echo "Another initial ingestion is running." >&2; exit 1; }
test "$(docker inspect --format '{{.Config.Image}}' "${api}")" = "ghcr.io/konkuk-icteam-student/ai:${expected_image_sha}"
test "$(docker inspect --format '{{.Config.Image}}' "${postgres}")" = pgvector/pgvector:pg17
(cd "${documents}"; sha256sum --quiet -c "${incoming}/SHA256SUMS")

# An initial load must never erase an existing regulation, FAQ, or contact dataset.
docker exec -i "${api}" python - <<'PYTHON'
import json
import os
from pathlib import Path
import pwd
from urllib.parse import urlparse
from app.core.config import DATABASE_URL, EMBEDDING_DIM
from app.infrastructure.database import get_conn

assert os.environ.get("HOME") == "/home/app"
assert Path.home() == Path("/home/app")
assert pwd.getpwuid(os.getuid()).pw_dir == "/home/app"
assert os.access(Path.home() / ".paddlex", os.W_OK)
database = urlparse(DATABASE_URL)
assert database.hostname == "127.0.0.1" and database.port == 15433 and database.path == "/chatbot"
with get_conn() as connection, connection.cursor() as cursor:
    counts = {}
    for table in ["regulation_chunks", "faq_chunks", "phone_contacts"]:
        cursor.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
print("Initial-load data counts:", json.dumps(counts))
assert all(count == 0 for count in counts.values()), "Existing data found; initial ingestion was not started."
for directory, expected in [("text_pdf", 371), ("image_pdf", 2)]:
    files = list((Path("/app/documents") / directory).glob("*.pdf"))
    assert len(files) == expected, (directory, len(files))

from app.ingestion.service import check_ollama, embed_texts
check_ollama()
vectors = embed_texts(["규정 색인을 위한 임베딩 연결 검사"])
assert len(vectors) == 1 and len(vectors[0]) == EMBEDDING_DIM
print("Image home, cache permission, empty database, corpus and embedding guards passed.")
import pymupdf
for path in sorted(Path("/app/documents/image_pdf").glob("*.pdf")):
    with pymupdf.open(path) as document:
        print(f"OCR input pages: {path.name}={len(document)}")
PYTHON

started="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/opt/chatbot-service/backups/staging/${started}"
mkdir -p "${backup_dir}"
docker exec "${postgres}" pg_dump -U postgres -d chatbot -Fc > "${backup_dir}/database.dump"
test -s "${backup_dir}/database.dump"
docker exec -i "${postgres}" pg_restore --list < "${backup_dir}/database.dump" > /dev/null
cp "${incoming}/SHA256SUMS" "${backup_dir}/SHA256SUMS"
cp --reflink=auto "${incoming}/documents.tar.gz" "${backup_dir}/documents.tar.gz"
log_file="${runtime}/ingest-${started}.log"
echo "Backup archive prepared: ${backup_dir}"
echo "Ingestion log: ${log_file}"

if ! docker exec "${api}" python -m app.ingest 2>&1 | tee "${log_file}"; then
    echo "Ingestion command failed. Preserve the log and backup for investigation." >&2
    exit 1
fi
if grep -Eq '\[처리 실패\]|\[처리 실패 파일\]|추출 실패|\[전화번호부 저장 실패\]' "${log_file}"; then
    echo "Ingestion reported extraction or contact failures; inspect ${log_file}." >&2
    exit 1
fi

docker exec -i "${api}" python - <<'PYTHON'
import json
from pathlib import Path
from app.infrastructure.database import get_conn

expected_sources = {path.name for path in Path("/app/documents").glob("*/*.pdf")}
with get_conn() as connection, connection.cursor() as cursor:
    cursor.execute("SELECT source, count(*) FROM regulation_chunks GROUP BY source")
    sources = dict(cursor.fetchall())
    counts = {}
    for table in ["regulation_chunks", "faq_chunks", "phone_contacts"]:
        cursor.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
print("Indexed data counts:", json.dumps(counts))
print(f"Indexed source coverage: {len(sources)}/{len(expected_sources)}")
missing = sorted(expected_sources - sources.keys())
unexpected = sorted(sources.keys() - expected_sources)
if missing:
    print("PDFs missing indexed chunks:", json.dumps(missing, ensure_ascii=False))
if unexpected:
    print("Unexpected indexed sources:", json.dumps(unexpected, ensure_ascii=False))
assert not missing and not unexpected, "Corpus coverage is incomplete."
assert counts["regulation_chunks"] > 0 and counts["phone_contacts"] > 0
assert counts["faq_chunks"] == 0, "Unexpected FAQ changes during initial loading."
PYTHON
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18000/health
echo
echo "Initial staging corpus indexing verified."
