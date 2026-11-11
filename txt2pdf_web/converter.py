"""txt → PDF 변환 엔진 (reportlab 기반)

책 한 권 분량(수십만~수백만 자)도 빠르게 처리하려고 Platypus 대신
줄바꿈과 페이지 배치를 직접 계산하는 방식으로 만들었습니다.

주요 기능
- 인코딩 자동 감지 (UTF-8 / UTF-16 / CP949(EUC-KR))
- 한글 어절 단위 줄바꿈 + 금칙 처리(줄 처음에 마침표·닫는 괄호 방지 등)
- 챕터 제목 자동 감지 → PDF 북마크(목차) 생성
- 표지, 머리말(제목), 쪽번호
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
_WIN_FONTS = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")

# ──────────────────────────────────────────────────────────────
# 용지 크기 (mm)
# ──────────────────────────────────────────────────────────────
PAGE_SIZES = {
    "A5": ("A5 (148×210mm)", (148, 210)),
    "SHIN": ("신국판 (152×225mm)", (152, 225)),
    "A4": ("A4 (210×297mm)", (210, 297)),
    "B5": ("B5 (176×250mm)", (176, 250)),
    "A6": ("문고판 A6 (105×148mm)", (105, 148)),
    "LETTER": ("Letter (216×279mm)", (215.9, 279.4)),
}

# ──────────────────────────────────────────────────────────────
# 글꼴
#   reportlab은 TrueType 윤곽(.ttf / .ttc)만 PDF에 넣을 수 있습니다.
#   (Noto Sans CJK 같은 .otf / CFF 계열은 사용할 수 없음)
# ──────────────────────────────────────────────────────────────
FONT_CANDIDATES = [
    ("nanumgothic", "나눔고딕", [
        os.path.join(FONT_DIR, "NanumGothic.ttf"),
        os.path.join(_WIN_FONTS, "NanumGothic.ttf"),
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/Library/Fonts/NanumGothic.ttf",
        os.path.expanduser("~/Library/Fonts/NanumGothic.ttf"),
    ]),
    ("nanummyeongjo", "나눔명조", [
        os.path.join(FONT_DIR, "NanumMyeongjo.ttf"),
        os.path.join(_WIN_FONTS, "NanumMyeongjo.ttf"),
        "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
        "/Library/Fonts/NanumMyeongjo.ttf",
        os.path.expanduser("~/Library/Fonts/NanumMyeongjo.ttf"),
    ]),
    ("malgun", "맑은 고딕", [os.path.join(_WIN_FONTS, "malgun.ttf")]),
    ("batang", "바탕", [os.path.join(_WIN_FONTS, "batang.ttc")]),
    ("applegothic", "애플고딕", [
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
    ]),
    ("wqy", "WenQuanYi Zen Hei", ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]),
]


def available_fonts() -> list[dict]:
    """사용 가능한 한글 글꼴 목록. fonts/ 폴더에 직접 넣은 .ttf/.ttc가 맨 앞에 옵니다."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(fid: str, label: str, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            found.append({"id": fid, "label": label, "path": path})

    # 1) 사용자가 fonts/ 폴더에 넣은 글꼴 (후보 목록에 있는 이름은 아래에서 처리)
    known = {os.path.basename(p).lower() for _, _, ps in FONT_CANDIDATES for p in ps}
    if os.path.isdir(FONT_DIR):
        for name in sorted(os.listdir(FONT_DIR)):
            stem, ext = os.path.splitext(name)
            if ext.lower() in (".ttf", ".ttc") and name.lower() not in known:
                add("user-" + re.sub(r"\W+", "-", stem.lower()), stem, os.path.join(FONT_DIR, name))

    # 2) 알려진 한글 글꼴
    for fid, label, paths in FONT_CANDIDATES:
        for p in paths:
            if os.path.isfile(p):
                add(fid, label, p)
                break
    return found


_registered: dict[str, TTFont] = {}


def _register(font: dict) -> tuple[str, TTFont]:
    name = "F_" + font["id"]
    if name not in _registered:
        tt = TTFont(name, font["path"])  # .ttc는 첫 번째 글꼴(subfontIndex=0)을 사용
        pdfmetrics.registerFont(tt)
        _registered[name] = tt
    return name, _registered[name]


# ──────────────────────────────────────────────────────────────
# 텍스트 읽기·정리
# ──────────────────────────────────────────────────────────────
def decode_text(data: bytes) -> tuple[str, str]:
    """바이트 → 문자열. (문자열, 감지한 인코딩 이름) 반환."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "UTF-8 (BOM)"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace"), "UTF-16"
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp949"), "CP949 (EUC-KR)"
    except UnicodeDecodeError:
        pass
    # 둘 다 엄격하게는 실패 → 깨진 글자가 더 적은 쪽을 선택
    as_utf8 = data.decode("utf-8", "replace")
    as_cp949 = data.decode("cp949", "replace")
    if as_utf8.count("\ufffd") < as_cp949.count("\ufffd"):
        return as_utf8, "UTF-8 (일부 깨진 글자 대체)"
    return as_cp949, "CP949 (일부 깨진 글자 대체)"


_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    text = text.replace("\t", "    ").replace("\u00a0", " ").replace("\u3000", " ")
    return _CONTROL_RE.sub("", text)


HEADING_RE = re.compile(
    r"^(제\s*[0-9一二三四五六七八九十百]+\s*[장화편부권회막절]"
    r"|chapter\s*(?:[0-9]+|[ivxlc]+\b)"
    r"|part\s*(?:[0-9]+|[ivxlc]+\b)"
    r"|프롤로그|에필로그|prologue|epilogue"
    r"|[0-9]{1,4}\s*[장화](?![가-힣]))",
    re.IGNORECASE,
)


_TERMINATORS = (".", "?", "!", "…", "。", '"', "”", "’", "」", "』", ")")


def _p95_line_length(text: str) -> int:
    lens = sorted(len(ln.strip()) for ln in text.split("\n") if ln.strip())
    return lens[int(len(lens) * 0.95)] if len(lens) >= 30 else 0


def detect_hard_wrap(text: str) -> bool:
    """줄이 일정한 너비(약 20~100자)에서 강제로 끊겨 있는 txt인지 추정한다.

    - 대부분의 줄이 비슷한 길이(최대 길이의 80% 이상)이고
    - 그런 줄들이 문장 끝(. ? ! 등)으로 끝나는 경우가 드물면 '강제 줄바꿈'으로 본다.
    한 줄이 한 문단인 일반 소설 txt는 줄 길이가 들쭉날쭉해서 해당되지 않는다.
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    longest = _p95_line_length(text)
    if len(lines) < 30 or not (20 <= longest <= 100):
        return False
    full = [ln for ln in lines if len(ln) >= longest * 0.8]
    if len(full) < len(lines) * 0.5:
        return False
    return sum(1 for ln in full if ln.endswith(_TERMINATORS)) < len(full) * 0.5


def build_items(text: str, join_lines: bool, detect_chapters: bool) -> list[tuple[str, Optional[str]]]:
    """('p', 문단) / ('h', 제목) / ('b', None=빈 줄) 목록으로 나눈다.

    join_lines=True 이면 연속된 줄을 한 문단으로 합친다. 문단은 다음 경우에 나뉜다:
    빈 줄 / 들여쓰기로 시작하는 줄 / 짧은 줄(최대 길이의 70% 미만)이 문장 끝으로 끝난 직후
    """
    items: list[tuple[str, Optional[str]]] = []
    buf: list[str] = []
    longest = _p95_line_length(text)
    last_short_end = False

    def flush() -> None:
        if buf:
            items.append(("p", " ".join(buf)))
            buf.clear()

    last_blank = True
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            flush()
            if not join_lines and not last_blank:
                items.append(("b", None))
            last_blank = True
            last_short_end = False
            continue
        last_blank = False
        if detect_chapters and len(s) <= 50 and HEADING_RE.match(s):
            flush()
            last_short_end = False
            items.append(("h", s))
        elif join_lines:
            indented = len(raw) - len(raw.lstrip()) >= 2
            if buf and (indented or last_short_end):
                flush()
            buf.append(s)
            last_short_end = bool(longest) and len(s) < longest * 0.7 and s.endswith(_TERMINATORS)
        else:
            items.append(("p", s))
    flush()
    return items


# ──────────────────────────────────────────────────────────────
# 줄바꿈
# ──────────────────────────────────────────────────────────────
NO_START = set(".,!?;:)]}%’”」』）〉》】〕…·、。，！？：；")  # 줄 처음에 오면 안 되는 글자
NO_END = set("([{‘“「『（〈《【〔")                               # 줄 끝에 오면 안 되는 글자


def wrap(text: str, width: float, cw: Callable[[str], float],
         first_indent: float = 0.0, keep_words: bool = True) -> list[tuple[str, bool]]:
    """text를 width(pt) 안에 들어가도록 줄로 나눈다. [(줄, 문단 첫 줄 여부), ...]"""
    lines: list[tuple[str, bool]] = []
    n = len(text)
    start = 0
    first = True
    while start < n:
        limit = width - (first_indent if first else 0.0)
        x = 0.0
        i = start
        brk = -1
        while i < n:
            ch = text[i]
            w = cw(ch)
            if x + w > limit + 0.01:
                break
            x += w
            if ch == " ":
                brk = i
            i += 1
        if i >= n:
            lines.append((text[start:n], first))
            break
        if text[i] == " ":                       # 넘친 글자가 공백이면 거기서 줄바꿈
            lines.append((text[start:i], first))
            start = i + 1
        elif keep_words and brk > start:         # 어절 단위로 줄바꿈
            lines.append((text[start:brk], first))
            start = brk + 1
        else:                                    # 글자 단위로 줄바꿈 (+ 금칙 처리)
            cut = max(i, start + 1)
            if cut < n and text[cut] in NO_START and cut - 1 > start:
                cut -= 1
            if cut - 1 > start and text[cut - 1] in NO_END:
                cut -= 1
            lines.append((text[start:cut], first))
            start = cut
        while start < n and text[start] == " ":
            start += 1
        first = False
    return [(t, f) for t, f in lines if t.strip()]


def _width_func(font_name: str, size: float) -> Callable[[str], float]:
    cache: dict[str, float] = {}
    string_width = pdfmetrics.stringWidth

    def cw(ch: str) -> float:
        w = cache.get(ch)
        if w is None:
            w = cache[ch] = string_width(ch, font_name, size)
        return w

    return cw


# ──────────────────────────────────────────────────────────────
# 페이지 배치
# ──────────────────────────────────────────────────────────────
def _draw_line(c: canvas.Canvas, x: float, y: float, line: str, avail: float,
               cw: Callable[[str], float], size: float, justify: bool) -> None:
    """한 줄을 그린다. justify면 줄 폭(avail)에 맞게 어절 간격(+글자 간격)을 늘려 양쪽을 맞춘다."""
    if not justify:
        c.drawString(x, y, line)
        return
    n = len(line)
    extra = avail - sum(cw(ch) for ch in line)
    if extra <= 0.05 or n < 2:
        c.drawString(x, y, line)
        return
    words = line.split(" ")
    gaps = len(words) - 1
    gap_extra = min(extra / gaps, size * 0.4) if gaps else 0.0   # 어절 사이에 먼저 분배
    rest = extra - gap_extra * gaps
    char_space = rest / (n - 1) if rest > 0.01 else 0.0          # 남는 만큼은 글자 사이에 분배
    if char_space > size * 0.3:                                  # 너무 듬성듬성해지면 정렬하지 않음
        c.drawString(x, y, line)
        return
    if not gaps:
        c.drawString(x, y, line, charSpace=char_space)
        return
    space_w = cw(" ")
    cx = x
    for w in words:
        if w:
            c.drawString(cx, y, w, charSpace=char_space)
        cx += sum(cw(ch) for ch in w) + char_space * len(w) + space_w + char_space + gap_extra


class _Book:
    def __init__(self, c: canvas.Canvas, pw: float, ph: float, margin: float, font: str,
                 size: float, line_spacing: float, header_text: str, page_numbers: bool):
        self.c, self.pw, self.ph = c, pw, ph
        self.ml = self.mr = self.mt = self.mb = margin
        self.font, self.size = font, size
        self.lh = size * line_spacing
        self.header_text, self.page_numbers = header_text, page_numbers
        self.bottom = self.mb + size * 0.25
        self.pages = 0          # 본문 쪽수
        self.is_open = False
        self.empty = True       # 현재 쪽에 아직 아무것도 안 그렸는가
        self.y = 0.0

    @property
    def width(self) -> float:
        return self.pw - self.ml - self.mr

    def _start(self) -> None:
        self.pages += 1
        self.is_open = True
        self.empty = True
        self.c.setFont(self.font, self.size)
        self.c.setFillGray(0)

    def _end(self) -> None:
        c = self.c
        c.setFont(self.font, self.size * 0.75)
        c.setFillGray(0.45)
        if self.header_text:
            c.drawString(self.ml, self.ph - self.mt * 0.55, self.header_text)
        if self.page_numbers:
            c.drawCentredString(self.pw / 2, self.mb * 0.45, str(self.pages))
        c.showPage()
        self.is_open = False

    def line_y(self, size: float, lh: float, extra: float = 0.0, need: float = 0.0) -> float:
        """다음 줄의 baseline y. 쪽이 모자라면 새 쪽을 연다."""
        if not self.is_open:
            self._start()
        if self.empty:
            y = self.ph - self.mt - size * 0.88
        else:
            y = self.y - extra - lh
            if y - need < self.bottom:
                self._end()
                self._start()
                y = self.ph - self.mt - size * 0.88
        self.y = y
        self.empty = False
        return y

    def new_page(self) -> None:
        if self.is_open and not self.empty:
            self._end()

    def finish(self) -> None:
        if self.is_open:
            self._end()


ProgressFn = Callable[[float, str], None]


@dataclass
class Options:
    title: str = ""
    author: str = ""
    font_id: str = ""
    font_size: float = 10.5
    line_spacing: float = 1.7
    para_spacing: float = 0.5        # 문단 간격 (글자 크기 배수)
    margin_mm: float = 18.0
    page_size: str = "A5"
    indent: bool = True              # 문단 첫 줄 들여쓰기
    join_lines: str = "auto"         # 원문 줄바꿈 처리: auto(자동 감지) / keep(그대로) / join(문단으로 합침)
    justify: bool = True             # 양쪽 정렬 (좌우 여백 맞춤)
    keep_words: bool = True          # 어절 단위 줄바꿈
    page_numbers: bool = True
    header: bool = True
    cover: bool = True
    chapters: bool = True            # 챕터 제목 감지 → 큰 제목 + 북마크
    chapter_newpage: bool = False


def convert(data: bytes, out_path: str, opt: Options, progress: Optional[ProgressFn] = None) -> dict:
    t0 = time.time()
    report: ProgressFn = progress or (lambda f, m="": None)

    fonts = available_fonts()
    if not fonts:
        raise RuntimeError(
            "한글 글꼴(.ttf)을 찾지 못했습니다. app.py 옆 fonts 폴더에 NanumGothic.ttf 같은 "
            "TrueType 한글 글꼴을 넣어 주세요."
        )
    font = next((f for f in fonts if f["id"] == opt.font_id), fonts[0])
    font_name, tt = _register(font)
    cmap = tt.face.charToGlyph
    if ord("한") not in cmap:
        raise RuntimeError(f"선택한 글꼴({font['label']})에는 한글이 없습니다. 다른 글꼴을 선택해 주세요.")

    report(0.01, "텍스트 읽는 중")
    text, encoding = decode_text(data)
    text = clean_text(text)
    if not text.strip():
        raise ValueError("텍스트가 비어 있습니다.")

    # 글꼴에 없는 글자는 '?'로 바꿔 둔다 (PDF에서 빈칸/네모로 깨지는 것 방지)
    missing = {ch for ch in set(text) if ch != "\n" and ord(ch) not in cmap}
    missing_count = 0
    if missing:
        missing_count = sum(text.count(ch) for ch in missing)
        text = text.translate({ord(ch): "?" for ch in missing})

    def fix(s: str) -> str:
        return s.translate({ord(ch): "?" for ch in s if ord(ch) not in cmap and ch != "\n"})

    title = fix((opt.title or "").strip()) or "제목 없음"
    author = fix((opt.author or "").strip())

    pw_mm, ph_mm = PAGE_SIZES.get(opt.page_size, PAGE_SIZES["A5"])[1]
    pw, ph = pw_mm * mm, ph_mm * mm
    size = opt.font_size
    margin = opt.margin_mm * mm

    c = canvas.Canvas(out_path, pagesize=(pw, ph), pageCompression=1)
    c.setTitle(title)
    if author:
        c.setAuthor(author)
    c.setCreator("txt2pdf web")

    cw = _width_func(font_name, size)

    # 머리말: 제목이 너무 길면 줄임표로 자른다
    header_text = ""
    if opt.header:
        small = _width_func(font_name, size * 0.75)
        limit = pw - 2 * margin
        header_text = title
        if sum(small(ch) for ch in header_text) > limit:
            while header_text and sum(small(ch) for ch in header_text + "…") > limit:
                header_text = header_text[:-1]
            header_text += "…"

    # 표지
    if opt.cover:
        tsize = size * 2.4
        tcw = _width_func(font_name, tsize)
        tlines = [t for t, _ in wrap(title, pw - 2 * margin - 10 * mm, tcw, 0.0, True)]
        y = ph * 0.62
        c.setFont(font_name, tsize)
        for ln in tlines:
            c.drawCentredString(pw / 2, y, ln)
            y -= tsize * 1.4
        if author:
            c.setFont(font_name, size * 1.2)
            c.setFillGray(0.35)
            c.drawCentredString(pw / 2, y - size * 1.5, author)
        c.showPage()

    report(0.03, "문단 나누는 중")
    if opt.join_lines == "join":
        join, linebreak = True, "문단으로 합침"
    elif opt.join_lines == "keep":
        join, linebreak = False, "원문 그대로"
    elif detect_hard_wrap(text):
        join, linebreak = True, "문단으로 합침(자동 감지)"
    else:
        join, linebreak = False, "원문 그대로(자동 감지)"
    items = build_items(text, join, opt.chapters)

    book = _Book(c, pw, ph, margin, font_name, size, opt.line_spacing, header_text, opt.page_numbers)
    lh = book.lh
    para_gap = opt.para_spacing * size
    indent = size if opt.indent else 0.0
    hsize = size * 1.35
    hcw = _width_func(font_name, hsize)
    width = book.width

    chapter_count = 0
    pending = 0.0                      # 빈 줄이 만든 추가 간격
    total = len(items)

    for idx, (kind, s) in enumerate(items):
        if idx % 300 == 0:
            report(0.05 + 0.90 * idx / max(total, 1), f"페이지 배치 중 ({book.pages}쪽)")

        if kind == "b":
            pending += lh * 0.8
            continue

        if kind == "h":
            chapter_count += 1
            if opt.chapter_newpage:
                book.new_page()
            hlines = [t for t, _ in wrap(s, width, hcw, 0.0, True)]
            for k, ln in enumerate(hlines):
                y = book.line_y(hsize, hsize * 1.5,
                                extra=(lh * 1.4 if k == 0 else 0.0),
                                need=(lh * 3 if k == 0 else 0.0))
                if k == 0:
                    key = f"ch{chapter_count}"
                    c.bookmarkHorizontal(key, 0, y + hsize * 1.4)
                    c.addOutlineEntry(fix(s), key, 0)
                c.setFont(font_name, hsize)
                c.drawString(book.ml, y, ln)
                c.setFont(font_name, size)
            pending = 0.0
            continue

        plines = wrap(s, width, cw, indent, opt.keep_words)
        last = len(plines) - 1
        for k, (ln, is_first) in enumerate(plines):
            extra = (para_gap + pending) if k == 0 else 0.0
            y = book.line_y(size, lh, extra=extra)
            off = indent if is_first else 0.0
            # 문단의 마지막 줄은 왼쪽 정렬, 나머지 줄은 양쪽 정렬
            _draw_line(c, book.ml + off, y, ln, width - off, cw, size, opt.justify and k < last)
        pending = 0.0

    book.finish()
    report(0.97, "PDF 저장 중")
    c.save()

    return {
        "pages": book.pages + (1 if opt.cover else 0),
        "chars": len(text),
        "encoding": encoding,
        "font": font["label"],
        "chapters": chapter_count,
        "linebreak": linebreak,
        "missing_glyphs": missing_count,
        "seconds": round(time.time() - t0, 1),
    }
