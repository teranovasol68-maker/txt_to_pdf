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

# 1. Kiwi 형태소 분석기 안전 초기화 (메모리 사용량 최소화 및 로딩 에러 방지)
try:
    kiwi = Kiwi(options=0)
except Exception as e:
    print(f"Kiwi initialization error: {e}")
    kiwi = None

def register_korean_font():
    """PDF 출력을 위한 한글 폰트 등록 (프로젝트 내부 fonts 폴더 우선 탐색)"""
    font_paths = [
        "fonts/NanumGothic.ttf",                                # 1순위: 프로젝트 내부에 포함된 폰트 (Render 서버에서 가장 확실함)
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",       # 2순위: 리눅스 시스템 폰트
        "C:\\Windows\\Fonts\\malgun.ttf",                        # 윈도우 로컬 테스트용
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf"    # 맥 로컬 테스트용
    ]
    
    font_name = "NanumGothic"
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
        self.drawString(40, 805, "소설 편집 교정 본문")

        self.line(40, 45, 555, 45)
        page_text = f"페이지 {self._pageNumber} / {page_count}"
        self.drawRightString(555, 30, page_text)
        self.restoreState()

def fix_korean_spacing_with_kiwi(text):
    """
    Kiwi 형태소 분석기를 이용한 단어 사전 기반 한글 띄어쓰기 자동 교정
    """
    if not text.strip():
        return text
    
    if kiwi is None:
        return text.strip()

    try:
        corrected_text = kiwi.space(text)
        corrected_text = re.sub(r'\s+([,.\?!])', r'\1', corrected_text)
        corrected_text = re.sub(r'([\.?!])([^\s"\'\)\]])', r'\1 \2', corrected_text)
        return corrected_text.strip()
    except Exception as e:
        print(f"Spacing error: {e}")
        return text.strip()

def process_novel_text(text, options):
    """
    형태소 분석기 띄어쓰기 교정 + 문장 중간 잘림 연결 + 대화문(" ") 닫는 따옴표 직후 줄바꿈 엔진
    """
    if not text:
        return ""

    lines = text.splitlines()
    merged_lines = []
    buffer_line = ""

    sentence_end_pattern = re.compile(r'[\.?!…"’\'”\)]\s*$')

    # 1. 문장 중간 부자연스럽게 잘린 개행 연결
    for line in lines:
        stripped = line.strip()

        if not stripped:
            if buffer_line:
                merged_lines.append(buffer_line)
                buffer_line = ""
            merged_lines.append("")
            continue

        if buffer_line:
            is_new_dialogue = stripped.startswith(('"', "'", '“', '‘', '「', '『'))
            if not sentence_end_pattern.search(buffer_line) and not is_new_dialogue:
                buffer_line += " " + stripped
            else:
                merged_lines.append(buffer_line)
                buffer_line = stripped
        else:
            buffer_line = stripped

    if buffer_line:
        merged_lines.append(buffer_line)

    # 2. 대화문(" ") 추출 및 닫는 따옴표 직후 줄바꿈 & Kiwi 사전 기반 띄어쓰기 교정
    result_paragraphs = []
    dialogue_pair_pattern = re.compile(r'([“"‘「『][^”"’」』]*[”"’」』])')

    for line in merged_lines:
        if not line:
            result_paragraphs.append("")
            continue

        parts = dialogue_pair_pattern.split(line)
        
        for part in parts:
            part_str = part.strip()
            if not part_str:
                continue

            # 형태소 분석기 기반 띄어쓰기 교정 적용
            if options.get('fix_spacing'):
                part_str = fix_korean_spacing_with_kiwi(part_str)

            is_dialogue = part_str.startswith(('"', "'", '“', '‘', '「', '『'))

            if is_dialogue:
                # 대화문은 통째로 한 줄 배치
                result_paragraphs.append(part_str)
            elif options.get('split_sentences'):
                # 일반 지문은 마침표/물음표/느낌표 기준으로 추가 줄바꿈
                sentences = re.split(r'((?<=[.?!])\s+)', part_str)
                temp = ""
                for s in sentences:
                    if not s:
                        continue
                    temp += s
                    if re.search(r'[.?!]$', temp.strip()):
                        clean_s = temp.strip()
                        if clean_s:
                            result_paragraphs.append(clean_s)
                        temp = ""
                if temp.strip():
                    result_paragraphs.append(temp.strip())
            else:
                result_paragraphs.append(part_str)

    # 3. 중복 빈 줄 정리
    final_output = []
    prev_empty = False
    for p in result_paragraphs:
        if p == "":
            if not prev_empty:
                final_output.append("")
                prev_empty = True
        else:
            final_output.append(p)
            prev_empty = False

    return "\n".join(final_output).strip()

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
            'split_sentences': data.get('split_sentences', True)
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
        title = data.get('title', '소설 편집 본문').strip() or '소설 편집 본문'
        text = data.get('text', '')

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
            'NovelTitle',
            parent=styles['Heading1'],
            fontName=FONT_NAME,
            fontSize=20,
            leading=26,
            alignment=1,
            spaceAfter=25
        )
        
        narrative_style = ParagraphStyle(
            'NovelNarrative',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=17,
            firstLineIndent=14,
            spaceAfter=6,
            textColor='#222222'
        )
        
        dialogue_style = ParagraphStyle(
            'NovelDialogue',
            parent=styles['Normal'],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=17,
            leftIndent=15,
            spaceAfter=8,
            textColor='#003366'
        )

        story = [Paragraph(title, title_style), Spacer(1, 15)]

        lines = text.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                story.append(Spacer(1, 6))
                continue
            
            safe_line = line_str.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            if safe_line.startswith(('"', "'", '“', '‘', '「', '『')):
                story.append(Paragraph(safe_line, dialogue_style))
            else:
                story.append(Paragraph(safe_line, narrative_style))

        doc.build(story, canvasmaker=NumberedCanvas)
        return send_file(pdf_path, as_attachment=True, download_name=f"{title}.pdf")
    except Exception as e:
        print(f"Error exporting PDF: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
