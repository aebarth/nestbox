"""Label PDF generation (reportlab + qrcode).

Formats: Avery 5160 (30 address labels/sheet) and Avery 5163 (10 shipping
labels/sheet). start_offset skips already-used positions on a partial sheet.
QR encodes {APP_BASE_URL}/r/{record_uuid} so scanning a box label with a phone
opens the record.
"""
import io
import os
from dataclasses import dataclass

import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


@dataclass
class LabelFormat:
    cols: int
    rows: int
    width: float
    height: float
    left: float
    top: float
    x_pitch: float
    y_pitch: float


FORMATS = {
    "avery5160": LabelFormat(cols=3, rows=10, width=2.625 * inch, height=1.0 * inch,
                             left=0.1875 * inch, top=0.5 * inch,
                             x_pitch=2.75 * inch, y_pitch=1.0 * inch),
    "avery5163": LabelFormat(cols=2, rows=5, width=4.0 * inch, height=2.0 * inch,
                             left=0.15625 * inch, top=0.5 * inch,
                             x_pitch=4.1875 * inch, y_pitch=2.0 * inch),
}


def _qr_image(url: str) -> ImageReader:
    qr = qrcode.QRCode(border=1, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _truncate(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> str:
    while text and c.stringWidth(text, font, size) > max_w:
        text = text[:-1]
    return text


def build_labels_pdf(items: list[dict], fmt_name: str = "avery5160", start_offset: int = 0) -> bytes:
    """items: [{title, line2, line3, url}] — one label each."""
    fmt = FORMATS.get(fmt_name, FORMATS["avery5160"])
    base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    page_w, page_h = letter
    per_page = fmt.cols * fmt.rows

    pos = start_offset
    for item in items:
        if pos >= per_page:
            c.showPage()
            pos = 0
        row, col = divmod(pos, fmt.cols)
        x = fmt.left + col * fmt.x_pitch
        y = page_h - fmt.top - row * fmt.y_pitch - fmt.height

        pad = 0.07 * inch
        qr_side = fmt.height - 2 * pad
        url = item.get("url") or f"{base_url}/r/{item.get('uuid', '')}"
        c.drawImage(_qr_image(url), x + pad, y + pad, qr_side, qr_side)

        text_x = x + qr_side + 2 * pad
        text_w = fmt.width - qr_side - 3 * pad
        big = fmt_name == "avery5163"
        title_size, body_size = (14, 10) if big else (8.5, 7)
        ty = y + fmt.height - pad - title_size
        c.setFont("Helvetica-Bold", title_size)
        c.drawString(text_x, ty, _truncate(c, item.get("title", ""), "Helvetica-Bold", title_size, text_w))
        c.setFont("Helvetica", body_size)
        for line in (item.get("line2", ""), item.get("line3", ""), item.get("line4", "")):
            if not line:
                continue
            ty -= body_size + 2
            if ty < y + pad:
                break
            c.drawString(text_x, ty, _truncate(c, line, "Helvetica", body_size, text_w))
        pos += 1

    c.save()
    return buf.getvalue()


def record_label_item(record, base_url: str) -> dict:
    parts = [p for p in (
        record.record_type.code if record.record_type else "",
        record.lot_number and f"Lot {record.lot_number}",
        record.part_number and f"P/N {record.part_number}",
    ) if p]
    return {
        "title": record.title or record.name,
        "line2": " · ".join(parts),
        "line3": f"Box {record.box.box_number}" if record.box else "",
        "line4": (record.project.code if record.project else ""),
        "url": f"{base_url}/r/{record.uuid}",
    }
