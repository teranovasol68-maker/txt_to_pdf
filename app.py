"""txt → PDF 변환 웹 서버 (Flask)

실행:   python app.py
접속:   http://127.0.0.1:5000

책 한 권 분량도 처리할 수 있도록 변환은 백그라운드 스레드에서 돌리고,
브라우저는 진행률을 주기적으로 물어본 뒤(폴링) 끝나면 PDF를 내려받습니다.
"""
import os
import re
import tempfile
import threading
import time
import traceback
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory

import converter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 업로드 상한 64MB (책 한 권 txt는 보통 1~5MB)

JOB_DIR = os.path.join(tempfile.gettempdir(), "txt2pdf_jobs")
os.makedirs(JOB_DIR, exist_ok=True)
JOB_TTL = 60 * 60            # 완료된 작업은 1시간 뒤 삭제

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
CONVERT_LOCK = threading.Lock()   # reportlab 글꼴 상태 보호: 한 번에 하나씩 변환

DEFAULTS = {
    "font_size": 10.5,
    "line_spacing": 1.7,
    "para_spacing": 0.5,
    "margin": 18,
    "page_size": "A5",
}


def _cleanup_jobs() -> None:
    now = time.time()
    with JOBS_LOCK:
        for jid, job in list(JOBS.items()):
            if job["state"] in ("done", "error") and now - job["created"] > JOB_TTL:
                JOBS.pop(jid, None)
                try:
                    os.remove(os.path.join(JOB_DIR, jid + ".pdf"))
                except OSError:
                    pass


def _run_job(job_id: str, data: bytes, opt: converter.Options) -> None:
    job = JOBS[job_id]
    pdf_path = os.path.join(JOB_DIR, job_id + ".pdf")

    def progress(frac: float, msg: str = "") -> None:
        job["progress"] = round(frac, 3)
        job["message"] = msg

    try:
        with CONVERT_LOCK:
            job["state"] = "running"
            stats = converter.convert(data, pdf_path, opt, progress)
        job.update(state="done", progress=1.0, message="완료", stats=stats, path=pdf_path)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.update(state="error", error=str(e) or e.__class__.__name__)


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/config")
def api_config():
    return jsonify(
        fonts=[{"id": f["id"], "label": f["label"]} for f in converter.available_fonts()],
        page_sizes=[{"id": k, "label": v[0]} for k, v in converter.PAGE_SIZES.items()],
        defaults=DEFAULTS,
    )


@app.post("/api/convert")
def api_convert():
    _cleanup_jobs()
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="txt 파일을 선택해 주세요."), 400
    data = f.read()
    if not data:
        return jsonify(error="빈 파일입니다."), 400

    form = request.form

    def num(name: str, default: float, lo: float, hi: float) -> float:
        try:
            return min(max(float(form.get(name, default)), lo), hi)
        except (TypeError, ValueError):
            return default

    def flag(name: str, default: bool) -> bool:
        v = form.get(name)
        return default if v is None else v.lower() in ("1", "true", "on", "yes")

    title = (form.get("title") or os.path.splitext(f.filename)[0]).strip()[:200]
    page_size = form.get("page_size", DEFAULTS["page_size"])
    opt = converter.Options(
        title=title,
        author=(form.get("author") or "").strip()[:100],
        font_id=form.get("font", ""),
        font_size=num("font_size", DEFAULTS["font_size"], 6, 24),
        line_spacing=num("line_spacing", DEFAULTS["line_spacing"], 1.0, 3.0),
        para_spacing=num("para_spacing", DEFAULTS["para_spacing"], 0, 2),
        margin_mm=num("margin", DEFAULTS["margin"], 8, 40),
        page_size=page_size if page_size in converter.PAGE_SIZES else DEFAULTS["page_size"],
        indent=flag("indent", True),
        join_lines=flag("join_lines", False),
        keep_words=flag("keep_words", True),
        page_numbers=flag("page_numbers", True),
        header=flag("header", True),
        cover=flag("cover", True),
        chapters=flag("chapters", True),
        chapter_newpage=flag("chapter_newpage", False),
    )

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "state": "queued", "progress": 0.0, "message": "대기 중",
            "created": time.time(), "title": title,
        }
    threading.Thread(target=_run_job, args=(job_id, data, opt), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="작업을 찾을 수 없습니다. (만료되었거나 서버가 재시작됨)"), 404
    return jsonify({k: job[k] for k in ("state", "progress", "message", "stats", "error") if k in job})


@app.get("/api/download/<job_id>")
def api_download(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["state"] != "done":
        return jsonify(error="다운로드할 수 없습니다."), 404
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", job["title"]).strip() or "output"
    return send_file(job["path"], mimetype="application/pdf", as_attachment=True, download_name=name + ".pdf")


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="파일이 너무 큽니다. (최대 64MB)"), 413


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  txt → PDF 변환기  http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
