"""Генерация PNG-изображения структуры проекта (как файл дерева в gitingest)."""
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "C:\\Windows\\Fonts\\consola.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


BG = (13, 17, 23)        # тёмный фон как на github
FG = (201, 209, 217)     # основной текст
TITLE = (88, 166, 255)   # заголовок
DIM = (110, 118, 129)    # второстепенный текст

#: Максимум строк дерева в одной картинке: дерево на 100k файлов даёт PNG
#: высотой в мегапиксели и падает по памяти (аудит H-6).
MAX_TREE_LINES = 4000


def tree_to_image(tree_str: str, title: str, summary: str) -> bytes:
    """Рисуем дерево каталогов в PNG и возвращаем байты."""
    font = _load_font(16)
    title_font = _load_font(20)
    small_font = _load_font(13)

    all_lines = tree_str.splitlines()
    lines = all_lines[:MAX_TREE_LINES]
    if len(all_lines) > MAX_TREE_LINES:
        lines = lines + [
            f"... (ещё строк обрезано: {len(all_lines) - MAX_TREE_LINES})"
        ]
    pad = 24
    line_h = 22
    title_h = 34
    summary_h = 26

    measurer = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    max_w = measurer.textlength(title, font=title_font)
    for ln in lines:
        max_w = max(max_w, measurer.textlength(ln, font=font))
    max_w = max(max_w, measurer.textlength(summary, font=small_font))

    width = int(max_w) + pad * 2
    height = pad * 2 + title_h + summary_h + line_h * len(lines) + 20

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    y = pad
    d.text((pad, y), title, font=title_font, fill=TITLE)
    y += title_h
    d.text((pad, y), summary, font=small_font, fill=DIM)
    y += summary_h + 8

    for ln in lines:
        d.text((pad, y), ln, font=font, fill=FG)
        y += line_h

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
