import os
import re
from flask import Flask, render_template, request, send_file, jsonify
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from kiwipiepy import Kiwi

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 1. Kiwi 형태소 분석기 안전 초기화
try:
    kiwi = Kiwi()
except Exception as e:

def register_korean_font():
    """KoPub 바탕체 PDF 출력을 위한 폰트 등록 (프로젝트 내부 fonts 폴더 우선 탐색)"""
    font_paths = [
        "fonts/KoPub Batang Medium.ttf",                         # 1순위: 프로젝트 내부 KoPub 바탕체 (Render 배포용)
        "fonts/NanumGothic.ttf",                                # 백업용 기존 폰트
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",       # 2순위: 리눅스 시스템 폰트
        "/usr/share/fonts/nanum/NanumGothic.ttf",
        "C:\\Windows\\Fonts\\malgun.ttf",                        # 윈도우 로컬
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf"    # 맥 로컬
    ]
    
    font_name = "KoPubBatang"
    registered = False
    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                registered = True
                print(f"Successfully registered font from: {path}")
                break
            except Exception as e:
                print(f"Font loading error for {path}: {e}")
    
    if not registered:
        print("WARNING: Korean font not found! Fallback to Helvetica.")
        font_name = "Helvetica"
    return font_name

FONT_NAME = register_korean_font()

class NumberedCanvas(canvas.Canvas):
    """PDF 하단 페이지 번호 및 상단 구분선 생성"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont(FONT_NAME if FONT_NAME != "Helvetica" else "Helvetica", 9)
        self.setFillColorRGB(0.4, 0.4, 0.4)
        
        self.setLineWidth(0.5)
        self.setStrokeColorRGB(0.8, 0.8, 0.8)
        self.line(40, 800, 555, 800)
        self.drawString(40, 805, "도서/출판 편집 교정 본문")

        self.line(40, 45, 555, 45)
        page_text = f"페이지 {self._pageNumber} / {page_count}"
        self.drawRightString(555, 30, page_text)
        self.restoreState()

def fix_korean_spacing_with_kiwi(text):
    """Kiwi 형태소 분석기를 이용한 단어 사전 기반 한글 띄어쓰기 및 문장부호 정돈"""
    if not text.strip():
        return text
    
    if kiwi is None:
        return text.strip()

    try:
        corrected_text = kiwi.space(text)
        corrected_text = re.sub(r'\.{3,}', '……', corrected_text)
        corrected_text = re.sub(r'\s+([,.\?!])', r'\1', corrected_text)
        corrected_text = re.sub(r'([\.?!])([^\s"\'\)\]…])', r'\1 \2', corrected_text)
        return corrected_text.strip()
    except Exception as e:
        print(f"Spacing error: {e}")
        return text.strip()

def process_novel_text(text, options):
    """텍스트 메타데이터 보호 및 줄바꿈/문단 정돈 엔진"""
    if not text:
        return ""

    lines = text.splitlines()
    processed_lines = []
    
    metadata_buffer = []
    body_lines = []
    
    is_capturing_meta = True
    for line in lines:
        stripped = line.strip()
        if is_capturing_meta and (not stripped or "지은이" in stripped or "출판사" in stripped or len(metadata_buffer) < 3):
            if stripped:
                metadata_buffer.append(stripped)
            elif metadata_buffer and not stripped:
                is_capturing_meta = False
        else:
            is_capturing_meta = False
            body_lines.append(line)

    if metadata_buffer:
        combined_meta = "  |  ".join(metadata_buffer)
        processed_lines.append(combined_meta)
        processed_lines.append("")

    merged_body = []
    buffer_line = ""
    sentence_end_pattern = re.compile(r'[\.?!…"’\'”\)]\s*$')

    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            if buffer_line:
                merged_body.append(buffer_line)
                buffer_line = ""
            merged_body.append("")
            continue

        if buffer_line:
            is_new_dialogue = stripped.startswith(('"', "'", '“', '‘', '「', '『'))
            if not sentence_end_pattern.search(buffer_line) and not is_new_dialogue:
                buffer_line += " " + stripped
            else:
                merged_body.append(buffer_line)
                buffer_line = stripped
        else:
            buffer_line = stripped

    if buffer_line:
        merged_body.append(buffer_line)

    for line in merged_body:
        if not line:
            processed_lines.append("")
            continue
        
        part_str = line.strip()
        if options.get('fix_spacing'):
            part_str = fix_korean_spacing_with_kiwi(part_str)
        processed_lines.append(part_str)

    return "\n".join(processed_lines).strip()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/process', methods=['POST'])
def process():
    try:
        data = request.json or {}
        raw_text = data.get('text', '')
        options = {
            'fix_spacing': data.get('fix_spacing', True),
            'clean_paragraphs': data.get('clean_paragraphs', True),
            'separate_dialogue': data.get('separate_dialogue', True),
            'split_sentences': data.get('split_sentences', False)
        }
        
        result_text = process_novel_text(raw_text, options)
        return jsonify({'status': 'success', 'processed_text': result_text})
    except Exception as e:
        print(f"Error processing text: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/export-pdf', methods=['POST'])
def export_pdf():
    try:
        data = request.json or {}
        text = data.get('text', '')

        book_title = "출판 교정 본문"
        author_name = ""
        
        lines = text.splitlines()
        meta_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                meta_lines.append(stripped)
                if len(meta_lines) >= 3:
                    break

        if len(meta_lines) > 0:
            book_title = meta_lines[0]
        for meta in meta_lines:
            if "지은이" in meta:
                parts = meta.split(":")
                if len(parts) > 1:
                    author_name = parts[1].strip()
                else:
                    author_name = meta.replace("지은이", "").strip()

        if author_name:
            filename = f"{book_title} - {author_name}.pdf"
        else:
            filename = f"{book_title}.pdf"
            
        filename = re.sub(r'[\\/*?:"<>|]', "", filename).strip()

        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], 'novel_export.pdf')
        
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=40,
            rightMargin=40,
            topMargin=50,
            bottomMargin=50
        )

        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle(
            'BookTitle',
            parent=styles['Heading1'],
            fontName=FONT_NAME,
            fontSize=18,
            leading=24,
            alignment=1,
            spaceAfter=20
        )
        
        meta_style = ParagraphStyle(
            'BookMeta',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=11,
            leading=16,
            alignment=1,
            spaceAfter=25,
            textColor='#555555'
        )

        narrative_style = ParagraphStyle(
            'BookNarrative',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=18,
            firstLineIndent=14,  # 문단 첫 줄 들여쓰기 적용
            spaceAfter=8,
            textColor='#222222'
        )
        
        dialogue_style = ParagraphStyle(
            'BookDialogue',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=18,
            leftIndent=15,
            spaceAfter=8,
            textColor='#003366'
        )

        story = []
        for line in lines:
            line_str = line.strip()
            if not line_str:
                story.append(Spacer(1, 8))
                continue
            
            safe_line = line_str.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            if line_str == meta_lines[0] if meta_lines else False:
                story.append(Paragraph(safe_line, title_style))
                continue
                
            if "지은이" in safe_line or "출판사" in safe_line:
                story.append(Paragraph(safe_line, meta_style))
                continue

            if safe_line.startswith(('"', "'", '“', '‘', '「', '『')):
                story.append(Paragraph(safe_line, dialogue_style))
            else:
                story.append(Paragraph(safe_line, narrative_style))

        doc.build(story, canvasmaker=NumberedCanvas)
        return send_file(pdf_path, as_attachment=True, download_name=filename)
    except Exception as e:
        print(f"Error exporting PDF: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
