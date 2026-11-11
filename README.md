# TXT → PDF 변환 웹페이지 (Python)

책 한 권 분량의 `.txt`를 골라서 PDF로 저장하는 로컬 웹앱입니다. (Flask + reportlab)

## 실행

```bash
pip install -r requirements.txt
python app.py
```

브라우저에서 http://127.0.0.1:5000 을 열고, txt 파일을 끌어다 놓고 **PDF로 변환**을 누르면 됩니다.
다른 기기에서도 접속하려면 `HOST=0.0.0.0 python app.py` (Windows: `set HOST=0.0.0.0` 후 실행).

## 한글 글꼴 (중요)

reportlab은 **TrueType(.ttf / .ttc)** 글꼴만 PDF에 넣을 수 있습니다.
(`NotoSansCJK-*.otf` 같은 OpenType/CFF 계열은 사용할 수 없습니다.)

앱이 아래 순서로 글꼴을 자동으로 찾습니다.

1. `app.py` 옆 `fonts/` 폴더에 직접 넣은 `.ttf` / `.ttc`
2. 나눔고딕 · 나눔명조 · 맑은 고딕(Windows) · 바탕(Windows) · 애플고딕(macOS)

Windows는 맑은 고딕이 기본으로 있어서 그대로 동작합니다.
macOS/Linux는 [나눔글꼴](https://hangeul.naver.com/font)의 `NanumGothic.ttf`(또는 `NanumMyeongjo.ttf`)를
`fonts/` 폴더에 넣고 서버를 다시 시작하세요. 글꼴 목록에 자동으로 나타납니다.

## 주요 기능

- 인코딩 자동 감지: UTF-8 / UTF-16 / CP949(EUC-KR)
- 어절 단위 줄바꿈 + 금칙 처리(줄 처음에 `.` `)` `」` 등이 오지 않게)
- 챕터 제목 자동 감지(`제1장`, `1화`, `Chapter 3`, `프롤로그` …) → 큰 제목 + PDF 북마크(목차)
- 표지, 머리말(제목), 쪽번호, 첫 줄 들여쓰기
- 용지: A5 / 신국판 / A4 / B5 / 문고판 / Letter, 글자 크기·여백·줄 간격 조절
- 진행률 표시 (60만 자, 약 650쪽 분량이 1초 안팎)

## 옵션 팁

- 줄이 40자쯤마다 강제로 끊겨 있는 txt → **빈 줄 기준으로 문단 합치기** 켜기
- 챕터 제목이 아닌 줄이 제목으로 잡히면 **챕터 제목 감지** 끄기
  (패턴 수정은 `converter.py`의 `HEADING_RE`)
- PDF에서 `?`로 보이는 글자가 있으면 글꼴에 그 글자가 없는 것이니 다른 글꼴을 선택하세요.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `app.py` | Flask 서버 (업로드, 백그라운드 변환, 진행률, 다운로드) |
| `converter.py` | 변환 엔진 (인코딩 감지, 줄바꿈, 페이지 배치, 북마크) |
| `index.html` | 웹 화면 |
| `fonts/` | (직접 만들기) 사용할 한글 `.ttf` 글꼴 |

변환된 PDF는 서버의 임시 폴더에 1시간 동안만 보관되고 자동 삭제됩니다.
