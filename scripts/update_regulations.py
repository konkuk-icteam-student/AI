import hashlib
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://rule.konkuk.ac.kr"

SEARCH_URL = (
    BASE_URL
    + "/lmxsrv/search/searchEngineTotal.do"
)

DOWNLOAD_URL = (
    BASE_URL
    + "/lmxsrv/fileDown.do"
)

DOCUMENTS_DIR = Path("documents") / "text_pdf"


session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
})


def sanitize_filename(name):
    """
    파일명에 사용할 수 없는 문자 제거
    """

    name = name.strip()

    name = re.sub(
        r'[\\/:*?"<>|]',
        "_",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    )

    return name


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def get_search_page(page):
    """
    규정 전체검색 페이지 가져오기
    """

    data = {
        "SHOW_YN": "Y",
        "currentYN": "1",
        "dateStart": "",
        "dateEnd": "",
        "gubun": "",
        "search_type": "lawName",
        "search_keyword": "",
        "search_page": str(page),
        "sort_val": "",
        "sort_type": "",
    }

    response = session.post(
        SEARCH_URL,
        data=data,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL,
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.text


def parse_regulations(html):
    """
    검색 결과 HTML에서

    규정번호
    제목
    SEQ
    SEQ_HISTORY

    추출
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    regulations = []

    for row in soup.select(
        "table.tb002 tbody tr"
    ):

        cells = row.find_all("td")

        if len(cells) < 4:
            continue

        regulation_number = (
            cells[1].get_text(
                " ",
                strip=True
            )
        )

        title_cell = cells[2]

        link = title_cell.find("a")

        if not link:
            continue

        title = link.get(
            "title",
            ""
        ).strip()

        if not title:
            title = link.get_text(
                " ",
                strip=True
            )

            # [규정], [요강] 등 제거
            title = re.sub(
                r"^\[[^\]]+\]\s*",
                "",
                title
            )

        href = link.get(
            "href",
            ""
        )

        seq_match = re.search(
            r"SEQ=(\d+)",
            href
        )

        history_match = re.search(
            r"SEQ_HISTORY=(\d+)",
            href
        )

        if not seq_match:
            continue

        if not history_match:
            continue

        seq = seq_match.group(1)

        seq_history = (
            history_match.group(1)
        )

        revision_date = (
            cells[3].get_text(
                " ",
                strip=True
            )
        )

        regulations.append({
            "number": regulation_number,
            "title": title,
            "seq": seq,
            "seq_history": seq_history,
            "revision_date": revision_date,
        })

    return regulations


def get_total_pages(html):
    """
    페이지: 1/37
    에서 37 추출
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    page_element = soup.select_one(
        ".page_num"
    )

    if not page_element:
        return 1

    text = page_element.get_text(
        strip=True
    )

    match = re.search(
        r"\d+\s*/\s*(\d+)",
        text
    )

    if not match:
        return 1

    return int(
        match.group(1)
    )


def download_pdf(regulation):
    """
    SEQ_HISTORY를 FILE_SEQ로 사용하여
    원본 PDF 다운로드
    """

    file_seq = regulation[
        "seq_history"
    ]

    data = {
        "FILE_SEQ": file_seq,
        "FILE_TYPE": "oriPdf",
    }

    referer = (
        BASE_URL
        + "/lmxsrv/law/"
        + "lawDetail_areaC.do"
        + f"?SEQ={regulation['seq']}"
        + "&LAWGROUP=1"
        + "&PAGE=1"
        + "&SEQ_HISTORY=0"
    )

    response = session.post(
        DOWNLOAD_URL,
        data=data,
        headers={
            "Origin": BASE_URL,
            "Referer": referer,
        },
        timeout=60,
    )

    response.raise_for_status()

    content = response.content

    # 오류 HTML 등을 PDF로 저장하는 것 방지
    if not content.startswith(
        b"%PDF"
    ):
        raise RuntimeError(
            "PDF가 아닌 응답입니다. "
            f"FILE_SEQ={file_seq}, "
            f"Content-Type="
            f"{response.headers.get('Content-Type')}"
        )

    return content


def save_pdf(
    regulation,
    content
):
    """
    기존 PDF와 SHA-256 비교.

    동일 → 변경 없음
    다름 → 교체
    없음 → 신규
    """

    filename = (
        regulation["number"]
        + " "
        + regulation["title"]
        + ".pdf"
    )

    filename = sanitize_filename(
        filename
    )

    path = (
        DOCUMENTS_DIR
        / filename
    )

    new_hash = sha256_bytes(
        content
    )

    if path.exists():

        old_hash = sha256_file(
            path
        )

        if old_hash == new_hash:

            print(
                f"[UNCHANGED] "
                f"{filename}"
            )

            return False

        print(
            f"[UPDATED] "
            f"{filename}"
        )

    else:

        print(
            f"[NEW] "
            f"{filename}"
        )

    path.write_bytes(
        content
    )

    return True


def main():

    DOCUMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        "=== Konkuk Regulation Update ==="
    )

    # ------------------------------
    # 첫 페이지
    # ------------------------------

    first_html = get_search_page(1)

    total_pages = get_total_pages(
        first_html
    )

    print(
        f"[INFO] total pages: "
        f"{total_pages}"
    )

    all_regulations = []

    # ------------------------------
    # 전체 페이지 순회
    # ------------------------------

    for page in range(
        1,
        total_pages + 1
    ):

        print(
            f"[SEARCH] page "
            f"{page}/{total_pages}"
        )

        if page == 1:
            html = first_html
        else:
            html = get_search_page(
                page
            )

        regulations = (
            parse_regulations(
                html
            )
        )

        print(
            f"         found "
            f"{len(regulations)}"
        )

        all_regulations.extend(
            regulations
        )

        time.sleep(0.3)

    # 중복 제거
    unique = {}

    for regulation in all_regulations:

        key = (
            regulation["seq"],
            regulation["seq_history"]
        )

        unique[key] = regulation

    regulations = list(
        unique.values()
    )

    print()
    print(
        f"[INFO] regulations: "
        f"{len(regulations)}"
    )
    print()

    # ------------------------------
    # PDF 다운로드
    # ------------------------------

    new_count = 0
    changed_count = 0
    unchanged_count = 0
    failed_count = 0

    for index, regulation in enumerate(
        regulations,
        start=1
    ):

        print(
            f"[{index}/"
            f"{len(regulations)}] "
            f"{regulation['number']} "
            f"{regulation['title']}"
        )

        try:

            filename = sanitize_filename(
                regulation["number"]
                + " "
                + regulation["title"]
                + ".pdf"
            )

            path = (
                DOCUMENTS_DIR
                / filename
            )

            existed_before = (
                path.exists()
            )

            content = download_pdf(
                regulation
            )

            changed = save_pdf(
                regulation,
                content
            )

            if not changed:

                unchanged_count += 1

            elif existed_before:

                changed_count += 1

            else:

                new_count += 1

        except Exception as error:

            failed_count += 1

            print(
                "[ERROR]",
                regulation["seq"],
                regulation[
                    "seq_history"
                ],
                error
            )

        # 사이트에 과도한 요청 방지
        time.sleep(0.5)

    print()
    print("=" * 50)
    print(
        f"신규       : {new_count}"
    )
    print(
        f"업데이트   : {changed_count}"
    )
    print(
        f"변경 없음  : {unchanged_count}"
    )
    print(
        f"실패       : {failed_count}"
    )
    print("=" * 50)


if __name__ == "__main__":
    main()

