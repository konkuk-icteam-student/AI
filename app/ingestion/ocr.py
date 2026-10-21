"""
스캔 PDF OCR 파이프라인.

주의:
이 모듈의 헤더/컬럼 검출 로직(_detect_headers, _find_vertical_boundaries 등)은
초록색 헤더 박스를 사용하는 특정 조직도형 전화번호부 서식
(건국대학교 소속 조직 전화번호부 PDF) 전용 휴리스틱이다.
HSV 초록색 임계값, "KONKUK"/"UNIVERSITY" 노이즈 필터 등이 모두
그 서식에 맞춰져 있다.

이 서식이 아닌 일반 스캔 PDF(초록 헤더가 없는 문서)에 대해서는
초록색 마스크가 비어 있어 헤더/조직 구조화 없이
페이지 전체를 컬럼 1개로 처리하는 폴백 경로로만 동작한다.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pymupdf
from paddleocr import PaddleOCR

logger = logging.getLogger(__name__)


# ============================================================
# 기본 설정
# ============================================================

OCR_DPI = 200

GREEN_H_MIN = 55
GREEN_H_MAX = 80
GREEN_S_MIN = 30

# 진한 초록
DARK_GREEN_V_MIN = 70
DARK_GREEN_V_MAX = 125

# 연한 초록
LIGHT_GREEN_V_MIN = 126
LIGHT_GREEN_V_MAX = 210

# 모든 초록색
ALL_GREEN_V_MIN = 70
ALL_GREEN_V_MAX = 240


# ============================================================
# Header 탐색 범위
# ============================================================

HEADER_TOP_RATIO = 0.075
HEADER_BOTTOM_RATIO = 0.965


# ============================================================
# OCR / Header 설정
# ============================================================

MIN_ROW_HEIGHT = 8

MIN_OCR_WIDTH = 160
MIN_OCR_HEIGHT = 64

HEADER_GROUP_X_GAP = 22
HEADER_GROUP_Y_GAP = 22

MIN_GROUP_WIDTH = 22
MIN_GROUP_HEIGHT = 14

# 조직/부서명은 이 길이를 넘지 않는다.
# 이보다 길면 header box 오검출(본문 영역을
# header로 잘못 묶은 경우)로 보고 버린다.
MAX_HEADER_TEXT_LENGTH = 40


# ============================================================
# PaddleOCR
# ============================================================

_ocr: PaddleOCR | None = None
_ocr_lock = threading.Lock()


def get_ocr() -> PaddleOCR:
    global _ocr

    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                logger.info("PaddleOCR 모델 초기화")

                _ocr = PaddleOCR(
                    lang="korean",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    device="cpu",
                    enable_mkldnn=False,
                )

    return _ocr


# ============================================================
# HSV
# ============================================================

def _to_hsv(
    image: np.ndarray,
) -> np.ndarray:

    return cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV,
    )


# ============================================================
# 진한 초록
# ============================================================

def _dark_green_mask(
    image: np.ndarray,
) -> np.ndarray:

    hsv = _to_hsv(image)

    return cv2.inRange(
        hsv,
        np.array(
            [
                GREEN_H_MIN,
                GREEN_S_MIN,
                DARK_GREEN_V_MIN,
            ],
            dtype=np.uint8,
        ),
        np.array(
            [
                GREEN_H_MAX,
                255,
                DARK_GREEN_V_MAX,
            ],
            dtype=np.uint8,
        ),
    )


# ============================================================
# 연한 초록
# ============================================================

def _light_green_mask(
    image: np.ndarray,
) -> np.ndarray:

    hsv = _to_hsv(image)

    return cv2.inRange(
        hsv,
        np.array(
            [
                GREEN_H_MIN,
                GREEN_S_MIN,
                LIGHT_GREEN_V_MIN,
            ],
            dtype=np.uint8,
        ),
        np.array(
            [
                GREEN_H_MAX,
                255,
                LIGHT_GREEN_V_MAX,
            ],
            dtype=np.uint8,
        ),
    )


# ============================================================
# 전체 초록
# ============================================================

def _all_green_mask(
    image: np.ndarray,
) -> np.ndarray:

    hsv = _to_hsv(image)

    return cv2.inRange(
        hsv,
        np.array(
            [
                GREEN_H_MIN,
                GREEN_S_MIN,
                ALL_GREEN_V_MIN,
            ],
            dtype=np.uint8,
        ),
        np.array(
            [
                GREEN_H_MAX,
                255,
                ALL_GREEN_V_MAX,
            ],
            dtype=np.uint8,
        ),
    )


# ============================================================
# PaddleOCR 결과
# ============================================================

def _get_result_dict(
    result: Any,
) -> dict:

    try:
        data = result.json

        if callable(data):
            data = data()

    except Exception:
        return {}

    if not isinstance(
        data,
        dict,
    ):
        return {}

    if (
        "res" in data
        and isinstance(
            data["res"],
            dict,
        )
    ):
        data = data["res"]

    return data


# ============================================================
# 작은 이미지 확대
# ============================================================

def _prepare_ocr_image(
    image: np.ndarray,
) -> np.ndarray:

    if image.size == 0:
        return image

    height, width = image.shape[:2]

    scale = 1.0

    if width < MIN_OCR_WIDTH:
        scale = max(
            scale,
            MIN_OCR_WIDTH / max(
                width,
                1,
            ),
        )

    if height < MIN_OCR_HEIGHT:
        scale = max(
            scale,
            MIN_OCR_HEIGHT / max(
                height,
                1,
            ),
        )

    scale = min(
        scale,
        4.0,
    )

    if scale <= 1.0:
        return image

    return cv2.resize(
        image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )


# ============================================================
# OCR 실행
# ============================================================

def _ocr_image(
    image: np.ndarray,
    label: str,
) -> list[str]:

    if image is None:
        return []

    if image.size == 0:
        return []

    image = _prepare_ocr_image(
        image
    )

    height, width = image.shape[:2]

    logger.debug(
        "%s %dx%d",
        label,
        width,
        height,
    )

    image_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=False,
        ) as tmp:
            image_path = Path(
                tmp.name
            )

        cv2.imwrite(
            str(image_path),
            image,
        )

        results = get_ocr().predict(
            str(image_path)
        )

        texts: list[str] = []

        for result in results:
            data = _get_result_dict(
                result
            )

            rec_texts = data.get(
                "rec_texts",
                [],
            )

            if not isinstance(
                rec_texts,
                list,
            ):
                continue

            for value in rec_texts:
                value = str(
                    value
                ).strip()

                if value:
                    texts.append(
                        value
                    )

        # PII(이름/전화번호 등)가 포함될 수 있으므로 DEBUG에서만 노출
        logger.debug(
            "[OCR RAW] %s: %s",
            label,
            texts,
        )

        return texts

    finally:
        if image_path is not None:
            image_path.unlink(
                missing_ok=True
            )


# ============================================================
# 기호 정규화
# ============================================================

def _normalize_symbols(
    text: str,
) -> str:

    replacements = {
        "–": "-",
        "—": "-",
        "−": "-",
        "‐": "-",
        "∼": "~",
        "～": "~",
        "˜": "~",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new,
        )

    return text.strip()


# ============================================================
# 전화번호 판별
# ============================================================

PHONE_PATTERN = re.compile(
    r"""
    ^
    (?:
        \d{2,3}\)\d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{2,3}-\d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{3,4}~\d{1,4}
        |
        \d{3,4}(?:,\s*\d{3,4})+
        |
        \d{4}
    )
    $
    """,
    re.VERBOSE,
)


def _is_phone(
    text: str,
) -> bool:

    text = _normalize_symbols(
        text
    )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return bool(
        PHONE_PATTERN.fullmatch(
            text
        )
    )


# ============================================================
# OCR token → 항목 | 번호
# ============================================================

def _reconstruct_lines(
    texts: list[str],
) -> list[str]:

    lines: list[str] = []

    pending: list[str] = []

    for raw_text in texts:
        text = _normalize_symbols(
            raw_text
        )

        if not text:
            continue

        if _is_phone(
            text
        ):

            if pending:
                label = " ".join(
                    pending
                ).strip()

                lines.append(
                    f"{label} | {text}"
                )

                pending = []

            else:
                lines.append(
                    text
                )

        else:
            pending.append(
                text
            )

    if pending:
        lines.append(
            " ".join(
                pending
            )
        )

    return lines


# ============================================================
# Header box 병합
# ============================================================

def _merge_box_group(
    boxes: list[
        tuple[int, int, int, int]
    ],
) -> tuple[
    int,
    int,
    int,
    int,
]:

    return (
        min(
            box[0]
            for box in boxes
        ),
        min(
            box[1]
            for box in boxes
        ),
        max(
            box[2]
            for box in boxes
        ),
        max(
            box[3]
            for box in boxes
        ),
    )


def _should_group_header_boxes(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> bool:

    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    aw = max(
        1,
        ax1 - ax0,
    )
    ah = max(
        1,
        ay1 - ay0,
    )
    bw = max(
        1,
        bx1 - bx0,
    )
    bh = max(
        1,
        by1 - by0,
    )

    dx = max(
        0,
        max(
            ax0,
            bx0,
        )
        - min(
            ax1,
            bx1,
        ),
    )

    dy = max(
        0,
        max(
            ay0,
            by0,
        )
        - min(
            ay1,
            by1,
        ),
    )

    x_overlap = max(
        0,
        min(
            ax1,
            bx1,
        )
        - max(
            ax0,
            bx0,
        ),
    )

    y_overlap = max(
        0,
        min(
            ay1,
            by1,
        )
        - max(
            ay0,
            by0,
        ),
    )

    x_overlap_ratio = (
        x_overlap
        / max(
            1,
            min(
                aw,
                bw,
            ),
        )
    )

    y_overlap_ratio = (
        y_overlap
        / max(
            1,
            min(
                ah,
                bh,
            ),
        )
    )

    vertical_neighbors = (
        x_overlap_ratio >= 0.30
        and dy <= HEADER_GROUP_Y_GAP
    )

    horizontal_neighbors = (
        y_overlap_ratio >= 0.30
        and dx <= HEADER_GROUP_X_GAP
    )

    return (
        vertical_neighbors
        or horizontal_neighbors
    )


def _group_header_boxes(
    boxes: list[
        tuple[int, int, int, int]
    ],
) -> list[
    tuple[int, int, int, int]
]:

    if not boxes:
        return []

    remaining = list(
        boxes
    )

    groups = []

    while remaining:
        group = [
            remaining.pop(0)
        ]

        changed = True

        while changed:
            changed = False

            group_box = (
                _merge_box_group(
                    group
                )
            )

            next_remaining = []

            for candidate in remaining:
                if _should_group_header_boxes(
                    group_box,
                    candidate,
                ):
                    group.append(
                        candidate
                    )

                    group_box = (
                        _merge_box_group(
                            group
                        )
                    )

                    changed = True

                else:
                    next_remaining.append(
                        candidate
                    )

            remaining = (
                next_remaining
            )

        groups.append(
            group
        )

    merged = [
        _merge_box_group(
            group
        )
        for group in groups
    ]

    merged = [
        box
        for box in merged
        if (
            box[2] - box[0]
            >= MIN_GROUP_WIDTH
        )
        and (
            box[3] - box[1]
            >= MIN_GROUP_HEIGHT
        )
    ]

    merged.sort(
        key=lambda box: (
            box[0],
            box[1],
        )
    )

    return merged


# ============================================================
# Header box 검출
# ============================================================

def _find_filled_boxes(
    mask: np.ndarray,
    image_width: int,
) -> list[
    tuple[int, int, int, int]
]:

    kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (3, 3),
        )
    )

    cleaned = (
        cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=1,
        )
    )

    contours, _ = (
        cv2.findContours(
            cleaned,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
    )

    raw_boxes = []

    for contour in contours:
        x, y, w, h = (
            cv2.boundingRect(
                contour
            )
        )

        if w < 3 or h < 3:
            continue

        if (
            w
            > image_width * 0.95
        ):
            continue

        if h > max(
            40,
            w * 12,
        ):
            continue

        area = (
            w * h
        )

        if area <= 0:
            continue

        contour_area = (
            cv2.contourArea(
                contour
            )
        )

        fill_ratio = (
            contour_area
            / area
        )

        if fill_ratio < 0.12:
            continue

        raw_boxes.append(
            (
                x,
                y,
                x + w,
                y + h,
            )
        )

    raw_boxes.sort(
        key=lambda box: (
            box[0],
            box[1],
        )
    )

    grouped = (
        _group_header_boxes(
            raw_boxes
        )
    )

    logger.debug(
        "header fragments=%d grouped=%d",
        len(raw_boxes),
        len(grouped),
    )

    return grouped


# ============================================================
# Header OCR
# ============================================================

def _ocr_header(
    image: np.ndarray,
    box: tuple[int, int, int, int],
    label: str,
) -> str:

    x0, y0, x1, y1 = box

    height, width = (
        image.shape[:2]
    )

    margin_x = 6
    margin_y = 4

    x0 = max(
        0,
        x0 - margin_x,
    )

    x1 = min(
        width,
        x1 + margin_x,
    )

    y0 = max(
        0,
        y0 - margin_y,
    )

    y1 = min(
        height,
        y1 + margin_y,
    )

    crop = image[
        y0:y1,
        x0:x1,
    ]

    texts = _ocr_image(
        crop,
        label,
    )

    if not texts:
        return ""

    return "".join(
        texts
    ).replace(
        " ",
        "",
    )


# ============================================================
# Header 검증
# ============================================================

_HEADER_NOISE_PATTERNS = (
    "KONKUK",
    "UNIVERSITY",
)


def _clean_header_text(
    text: str,
) -> str:

    text = _normalize_symbols(
        text
    )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return text.strip(
        "|[](){}<>_-—–·ㆍ.,:;"
    )


def _is_valid_header_text(
    text: str,
    header_type: str,
) -> bool:

    text = _clean_header_text(
        text
    )

    if not text:
        return False

    if len(text) > MAX_HEADER_TEXT_LENGTH:
        return False

    upper = text.upper()

    if any(
        pattern in upper
        for pattern
        in _HEADER_NOISE_PATTERNS
    ):
        return False

    if not re.search(
        r"[가-힣A-Za-z]",
        text,
    ):
        return False

    korean_count = len(
        re.findall(
            r"[가-힣]",
            text,
        )
    )

    english_count = len(
        re.findall(
            r"[A-Za-z]",
            text,
        )
    )

    if (
        header_type == "major"
        and korean_count < 2
    ):
        return False

    if (
        header_type == "minor"
        and korean_count < 2
        and english_count < 2
    ):
        return False

    return True


# ============================================================
# MAJOR / MINOR 검출
# ============================================================

def _detect_headers(
    image: np.ndarray,
) -> list[
    dict[str, Any]
]:

    height, width = (
        image.shape[:2]
    )

    body_top = int(
        height
        * HEADER_TOP_RATIO
    )

    body_bottom = int(
        height
        * HEADER_BOTTOM_RATIO
    )

    logger.debug(
        "header body range: y=%d:%d",
        body_top,
        body_bottom,
    )

    # ========================================================
    # Mask
    # ========================================================

    dark_mask = (
        _dark_green_mask(
            image
        )
    )

    light_mask = (
        _light_green_mask(
            image
        )
    )

    # ========================================================
    # 핵심 수정
    #
    # 진한 초록 MAJOR 영역을
    # 연한 초록 MINOR mask에서 제거한다.
    # ========================================================

    major_kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (9, 9),
        )
    )

    expanded_dark_mask = (
        cv2.dilate(
            dark_mask,
            major_kernel,
            iterations=1,
        )
    )

    light_mask = (
        cv2.bitwise_and(
            light_mask,
            cv2.bitwise_not(
                expanded_dark_mask
            ),
        )
    )

    # ========================================================
    # 페이지 상단/하단 제외
    # ========================================================

    dark_mask[
        :body_top,
        :
    ] = 0

    dark_mask[
        body_bottom:,
        :
    ] = 0

    light_mask[
        :body_top,
        :
    ] = 0

    light_mask[
        body_bottom:,
        :
    ] = 0

    # ========================================================
    # MAJOR
    # ========================================================

    dark_boxes = (
        _find_filled_boxes(
            dark_mask,
            width,
        )
    )

    headers: list[
        dict[str, Any]
    ] = []

    for index, box in enumerate(
        dark_boxes,
        start=1,
    ):

        center_y = (
            box[1]
            + box[3]
        ) / 2

        if not (
            body_top
            <= center_y
            <= body_bottom
        ):
            continue

        text = _ocr_header(
            image,
            box,
            f"MAJOR-{index}",
        )

        text = (
            _clean_header_text(
                text
            )
        )

        if not _is_valid_header_text(
            text,
            "major",
        ):
            logger.debug(
                "rejected MAJOR: %r",
                text,
            )
            continue

        headers.append(
            {
                "type": "major",
                "text": text,
                "box": box,
            }
        )

        logger.debug(
            "[MAJOR] x=%d y=%d %s",
            box[0],
            box[1],
            text,
        )

    # ========================================================
    # MINOR
    # ========================================================

    light_boxes = (
        _find_filled_boxes(
            light_mask,
            width,
        )
    )

    for index, box in enumerate(
        light_boxes,
        start=1,
    ):

        center_y = (
            box[1]
            + box[3]
        ) / 2

        if not (
            body_top
            <= center_y
            <= body_bottom
        ):
            continue

        text = _ocr_header(
            image,
            box,
            f"MINOR-{index}",
        )

        text = (
            _clean_header_text(
                text
            )
        )

        if not _is_valid_header_text(
            text,
            "minor",
        ):
            logger.debug(
                "rejected MINOR: %r",
                text,
            )
            continue

        headers.append(
            {
                "type": "minor",
                "text": text,
                "box": box,
            }
        )

        logger.debug(
            "[MINOR] x=%d y=%d %s",
            box[0],
            box[1],
            text,
        )

    major_count = sum(
        1
        for header in headers
        if header["type"]
        == "major"
    )

    minor_count = sum(
        1
        for header in headers
        if header["type"]
        == "minor"
    )

    logger.info(
        "detected major=%d minor=%d",
        major_count,
        minor_count,
    )

    return headers


# ============================================================
# 위치 병합
# ============================================================

def _merge_positions(
    positions: np.ndarray,
    max_gap: int = 4,
) -> list[int]:

    if len(
        positions
    ) == 0:
        return []

    groups = [
        [
            int(
                positions[0]
            )
        ]
    ]

    for value in positions[1:]:
        value = int(
            value
        )

        if (
            value
            - groups[-1][-1]
            <= max_gap
        ):
            groups[-1].append(
                value
            )

        else:
            groups.append(
                [
                    value
                ]
            )

    return [
        int(
            np.mean(
                group
            )
        )
        for group in groups
    ]


# ============================================================
# Column 경계
# ============================================================

def _find_vertical_boundaries(
    image: np.ndarray,
) -> list[int]:

    mask = _all_green_mask(
        image
    )

    height, width = (
        mask.shape
    )

    y0 = int(
        height * 0.08
    )

    y1 = int(
        height * 0.97
    )

    roi = mask[
        y0:y1,
        :
    ]

    projection = (
        roi > 0
    ).sum(
        axis=0
    )

    threshold = (
        roi.shape[0]
        * 0.08
    )

    positions = np.where(
        projection
        >= threshold
    )[0]

    raw = _merge_positions(
        positions,
        max_gap=8,
    )

    logger.debug(
        "raw vertical boundaries: %s",
        raw,
    )

    if len(raw) < 2:
        return [
            0,
            width,
        ]

    diffs = [
        raw[index + 1]
        - raw[index]
        for index in range(
            len(raw) - 1
        )
    ]

    large_diffs = [
        value
        for value in diffs
        if value
        >= width * 0.09
    ]

    if not large_diffs:
        return [
            raw[0],
            raw[-1],
        ]

    estimated_width = float(
        np.median(
            large_diffs
        )
    )

    total_width = (
        raw[-1]
        - raw[0]
    )

    column_count = int(
        round(
            total_width
            / estimated_width
        )
    )

    column_count = max(
        1,
        min(
            9,
            column_count,
        ),
    )

    expected = np.linspace(
        raw[0],
        raw[-1],
        column_count + 1,
    )

    tolerance = (
        estimated_width
        * 0.30
    )

    boundaries: list[int] = []

    for target in expected:
        nearest = min(
            raw,
            key=lambda value:
            abs(
                value
                - target
            ),
        )

        if (
            abs(
                nearest
                - target
            )
            <= tolerance
        ):
            chosen = nearest

        else:
            chosen = int(
                round(
                    target
                )
            )

        if (
            not boundaries
            or chosen
            > boundaries[-1]
        ):
            boundaries.append(
                chosen
            )

    boundaries[0] = (
        raw[0]
    )

    boundaries[-1] = (
        raw[-1]
    )

    logger.debug(
        "vertical boundaries: %s",
        boundaries,
    )

    logger.info(
        "columns detected: %d",
        len(boundaries) - 1,
    )

    return boundaries


# ============================================================
# 가로선
# ============================================================

def _find_horizontal_lines(
    image: np.ndarray,
) -> list[int]:

    mask = _all_green_mask(
        image
    )

    height, width = (
        mask.shape
    )

    kernel_width = max(
        20,
        int(
            width * 0.25
        ),
    )

    kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (
                kernel_width,
                1,
            ),
        )
    )

    horizontal = (
        cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            kernel,
        )
    )

    projection = (
        horizontal > 0
    ).sum(
        axis=1
    )

    threshold = max(
        10,
        int(
            width * 0.20
        ),
    )

    positions = np.where(
        projection
        >= threshold
    )[0]

    return _merge_positions(
        positions,
        max_gap=3,
    )


# ============================================================
# Body OCR
# ============================================================

def _ocr_body(
    image: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    label: str,
) -> list[str]:

    height, width = (
        image.shape[:2]
    )

    x0 = max(
        0,
        x0,
    )

    x1 = min(
        width,
        x1,
    )

    y0 = max(
        0,
        y0,
    )

    y1 = min(
        height,
        y1,
    )

    if (
        x1 <= x0
        or y1 <= y0
    ):
        return []

    crop = image[
        y0:y1,
        x0:x1,
    ]

    texts = _ocr_image(
        crop,
        label,
    )

    lines = (
        _reconstruct_lines(
            texts
        )
    )

    horizontal_lines = (
        _find_horizontal_lines(
            crop
        )
    )

    logger.debug(
        "%s horizontal lines=%d",
        label,
        len(horizontal_lines),
    )

    return lines


# ============================================================
# Column Header
# ============================================================

def _headers_in_column(
    headers: list[
        dict[str, Any]
    ],
    x0: int,
    x1: int,
) -> list[
    dict[str, Any]
]:

    result = []

    for header in headers:

        box = header[
            "box"
        ]

        center_x = (
            box[0]
            + box[2]
        ) / 2

        if (
            x0
            <= center_x
            <= x1
        ):
            result.append(
                header
            )

    result.sort(
        key=lambda header:
        header["box"][1]
    )

    return result


# ============================================================
# Column 처리
# ============================================================

def _process_column(
    image: np.ndarray,
    x0: int,
    x1: int,
    headers: list[
        dict[str, Any]
    ],
    column_index: int,
    current_major: str | None,
    current_minor: str | None,
) -> tuple[
    list[str],
    str | None,
    str | None,
]:

    image_height = (
        image.shape[0]
    )

    column_headers = (
        _headers_in_column(
            headers,
            x0,
            x1,
        )
    )

    output: list[str] = []

    # ========================================================
    # Header가 없는 Column
    # ========================================================

    if not column_headers:

        lines = _ocr_body(
            image,
            x0,
            0,
            x1,
            image_height,
            f"COLUMN-{column_index}",
        )

        if lines:

            if current_minor:

                if current_major:
                    output.append(
                        (
                            "[ORGANIZATION] "
                            f"{current_major}"
                            " > "
                            f"{current_minor}"
                        )
                    )

                else:
                    output.append(
                        (
                            "[ORGANIZATION] "
                            f"{current_minor}"
                        )
                    )

            output.extend(
                lines
            )

        return (
            output,
            current_major,
            current_minor,
        )

    # ========================================================
    # 첫 Header 위쪽
    # ========================================================

    first_y = (
        column_headers[0][
            "box"
        ][1]
    )

    if first_y > 20:

        pre_lines = (
            _ocr_body(
                image,
                x0,
                0,
                x1,
                first_y,
                (
                    f"COLUMN-"
                    f"{column_index}"
                    "-PRE"
                ),
            )
        )

        if pre_lines:

            if current_minor:

                if current_major:
                    output.append(
                        (
                            "[ORGANIZATION] "
                            f"{current_major}"
                            " > "
                            f"{current_minor}"
                        )
                    )

                else:
                    output.append(
                        (
                            "[ORGANIZATION] "
                            f"{current_minor}"
                        )
                    )

            output.extend(
                pre_lines
            )

    # ========================================================
    # Header 처리
    # ========================================================

    for index, header in enumerate(
        column_headers
    ):

        header_type = (
            header["type"]
        )

        header_text = (
            header["text"]
        )

        box = (
            header["box"]
        )

        # ----------------------------------------------------
        # MAJOR
        # ----------------------------------------------------

        if (
            header_type
            == "major"
        ):

            current_major = (
                header_text
            )

            current_minor = None

            output.append(
                (
                    "[MAJOR] "
                    f"{current_major}"
                )
            )

        # ----------------------------------------------------
        # MINOR
        # ----------------------------------------------------

        else:

            current_minor = (
                header_text
            )

            if current_major:
                output.append(
                    (
                        "[ORGANIZATION] "
                        f"{current_major}"
                        " > "
                        f"{current_minor}"
                    )
                )

            else:
                output.append(
                    (
                        "[ORGANIZATION] "
                        f"{current_minor}"
                    )
                )

        body_y0 = (
            box[3]
        )

        if (
            index + 1
            < len(column_headers)
        ):
            body_y1 = (
                column_headers[
                    index + 1
                ]["box"][1]
            )

        else:
            body_y1 = (
                image_height
            )

        if (
            body_y1
            - body_y0
            < MIN_ROW_HEIGHT
        ):
            continue

        if current_minor:

            if current_major:
                organization = (
                    f"{current_major}"
                    " > "
                    f"{current_minor}"
                )

            else:
                organization = (
                    current_minor
                )

        else:
            organization = (
                current_major
                or "UNKNOWN"
            )

        logger.debug(
            "[OCR ORGANIZATION] %s x=%d:%d y=%d:%d",
            organization,
            x0,
            x1,
            body_y0,
            body_y1,
        )

        lines = (
            _ocr_body(
                image,
                x0,
                body_y0,
                x1,
                body_y1,
                (
                    f"C{column_index}"
                    f"-ORG-{index + 1}"
                ),
            )
        )

        output.extend(
            lines
        )

    return (
        output,
        current_major,
        current_minor,
    )


# ============================================================
# 전체 페이지 처리
# ============================================================

def _process_page_image(
    image: np.ndarray,
) -> str:

    logger.info(
        "image size: %s",
        image.shape,
    )

    boundaries = (
        _find_vertical_boundaries(
            image
        )
    )

    headers = (
        _detect_headers(
            image
        )
    )

    output: list[str] = []

    current_major: str | None = None
    current_minor: str | None = None

    for index in range(
        len(boundaries) - 1
    ):

        x0 = (
            boundaries[index]
            + 2
        )

        x1 = (
            boundaries[
                index + 1
            ]
            - 2
        )

        if x1 <= x0:
            continue

        logger.debug(
            "[OCR COLUMN] %d/%d x=%d:%d",
            index + 1,
            len(boundaries) - 1,
            x0,
            x1,
        )

        (
            column_output,
            current_major,
            current_minor,
        ) = _process_column(
            image,
            x0,
            x1,
            headers,
            index + 1,
            current_major,
            current_minor,
        )

        output.extend(
            column_output
        )

    logger.info(
        "[OCR FINAL] %d lines",
        len(output),
    )

    # 최종 OCR 결과(PII 포함 가능) 전체는 DEBUG에서만 노출
    for line in output:
        logger.debug(line)

    return "\n".join(
        output
    )


# ============================================================
# PyMuPDF Page -> OCR
# ============================================================

def ocr_pdf_page(
    page: pymupdf.Page,
    dpi: int = OCR_DPI,
) -> str:

    pix = page.get_pixmap(
        dpi=dpi,
        alpha=False,
    )

    image = np.frombuffer(
        pix.samples,
        dtype=np.uint8,
    )

    image = image.reshape(
        pix.height,
        pix.width,
        pix.n,
    )

    if pix.n == 4:

        image = cv2.cvtColor(
            image,
            cv2.COLOR_RGBA2BGR,
        )

    else:

        image = cv2.cvtColor(
            image,
            cv2.COLOR_RGB2BGR,
        )

    return _process_page_image(
        image
    )
