import os
import re
from flask import Flask, render_template, request, jsonify, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Kiwipiepy 형태소 분석기 안전 임포트
try:
    from kiwipiepy import Kiwi
    kiwi = Kiwi()
except Exception as e:
    print(f"Kiwi initialization warning: {e}")
    kiwi = None

app = Flask(__name__)

# PDF 폰트 설정 (나눔고딕 또는 시스템 기본 폰트 적용)
FONT_NAME = "NanumGothic"
FONT_PATH = "fonts/NanumGothic.ttf"  # 또는 환경에 맞는 폰트 경로

def register_fonts():
    try:
        if os.path.exists(FONT_PATH):
            pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
            print(f"Successfully registered font from: {FONT_PATH}")
        else:
            # 폰트 파일이 없으면 기본 폰트(Helvetica) 사용
            global FONT_NAME
            FONT_NAME = "Helvetica"
            print(f"Font file not found at {FONT_PATH}, using default font.")
    except Exception as e:
        global FONT_NAME
        FONT_NAME = "Helvetica"
        print(f"Font registration failed: {e}, using default font.")

register_fonts()

def fix_korean_spacing_with_kiwi(text):
    """Kiwi를 이용한 기본적인 띄어쓰기 교정"""
    if not text or not text.strip():
        return text
    if kiwi is None:
        return text.strip()

    try:
        # 안전하게 줄 단위로 분할하여 처리
        lines = text.split('\n')
        corrected_lines = []
        for line in lines:
            if not line.strip():
                corrected_lines.append("")
                continue
            if len(line) > 1000:
                corrected_lines.append(line.strip())
                continue
            try:
                fixed = kiwi.space(line)
                corrected_lines.append(fixed)
            except Exception:
                corrected_lines.append(line.strip())
        
        result = "\n".join(corrected_lines)
        result = re.sub(r'\.{3,}', '……', result)
        result = re.sub(r'\s+([,.\?!])', r'\1', result)
        return result.strip()
    except Exception as e:
        print(f"Spacing error: {e}")
        return text.strip()

def process_novel_text(text, options):
    """옵션에 따른 텍스트 정돈 및 가공"""
    processed = text

    if options.get('fix_spacing', True):
        processed = fix_korean_spacing_with_kiwi(processed)

    if options.get('clean_paragraphs', True):
        # 불필요한 연속 공백 및 빈 줄 정리
        lines = [line.strip() for line in processed.split('\n')]
        cleaned_lines = []
        empty_count = 0
        for line in lines:
            if not line:
                empty_count += 1
                if empty_count <= 2:  # 연속 빈 줄은 최대 2줄까지만 허용
                    cleaned_lines.append("")
            else:
                empty_count = 0
                cleaned_lines.append(line)
        processed = "\n".join(cleaned_lines)

    return processed

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/process', methods=['POST'])
def api_process():
    try:
        data = request.get_json()
        text = data.get('text', '')
        options = {
            'fix_spacing': data.get('fix_spacing', True),
            'clean_paragraphs': data.get('clean_paragraphs', True),
            'separate_dialogue': data.get('separate_dialogue', True),
            'split_sentences': data.get('split_sentences', True)
        }

        processed_text = process_novel_text(text, options)
        return jsonify({'status': 'success', 'processed_text': processed_text})
    except Exception as e:
        print(f"Process API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/export-pdf', methods=['POST'])
def api_export_pdf():
    try:
        data = request.get_json()
        title = data.get('title', '소설 편집 본문')
        text = data.get('text', '')

        pdf_path = f"/tmp/{title}.pdf"
        
        # ReportLab PDF 생성 설정
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            rightMargin=54, leftMargin=54,
            topMargin=54, bottomMargin=54
        )
        
        styles = getSampleStyleSheet()
        story_style = ParagraphStyle(
            'StoryStyle',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=11,
            leading=18,
            textColor='#222222'
        )

        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontName=FONT_NAME,
            fontSize=18,
            leading=24,
            alignment=1, # 가운데 정렬
            spaceAfter=20
        )

        story = []
        if title:
            story.append(Paragraph(title, title_style))
            story.append(Spacer(1, 15))

        paragraphs = text.split('\n')
        for p in paragraphs:
            if not p.strip():
                story.append(Spacer(1, 10))
            else:
                # 특수문자 이스케이프 처리
                safe_p = p.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                story.append(Paragraph(safe_p, story_style))

        doc.build(story)

        return send_file(pdf_path, as_attachment=True, download_name=f"{title}.pdf")
    except Exception as e:
        print(f"PDF Export error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
