import io


def _pdf_with_text(text: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 720
    for line in text.split("\n"):
        c.drawString(72, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


def test_embedded_text_extraction():
    from services.ocr import extract_text

    text, method = extract_text(_pdf_with_text(
        "Device History Record\nLot A42 Part 100-200\n" + "traceability content " * 20
    ))
    assert method == "embedded"
    assert "Lot A42" in text


def test_fingerprint_normalization_washes_out_ocr_noise():
    from services.ocr import make_fingerprint

    a = make_fingerprint("Calibration  Record #2026-003!\nResult: PASS.")
    b = make_fingerprint("calibration record 2026 003\nresult pass")
    assert a == b


def test_folder_name_suggestions():
    from routers.onboarding import _suggest_type

    class RT:
        def __init__(self, id, code, name):
            self.id, self.code, self.name = id, code, name

    types = [
        RT(1, "CAL", "Calibration Record"),
        RT(2, "TRN", "Training Record"),
        RT(3, "DHR", "Device History Record"),
    ]
    assert _suggest_type("2026/Calibration Records", types)[0] == 1
    assert _suggest_type("Training", types)[0] == 2
    assert _suggest_type("scans/DHR", types)[0] == 3
    assert _suggest_type("Miscellaneous stuff", types)[0] is None
