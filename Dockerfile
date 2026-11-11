FROM python:3.12-slim

# 한글 글꼴(나눔고딕·나눔명조)을 설치합니다. converter.py가 아래 경로를 자동으로 찾습니다.
#   /usr/share/fonts/truetype/nanum/NanumGothic.ttf
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1

# 워커는 반드시 1개: 진행률/작업 목록을 프로세스 메모리에 보관하기 때문입니다.
# 동시 접속은 스레드로 처리합니다. Render가 PORT 환경변수를 넣어 줍니다.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 300 -b 0.0.0.0:${PORT:-10000} app:app"]
