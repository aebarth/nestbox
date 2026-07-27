def test_label_pdf_generation():
    from services.labels import build_labels_pdf

    items = [{"title": f"Record {i}", "line2": "Lot 42", "line3": "Box QA-1",
              "url": f"http://localhost/r/uuid-{i}"} for i in range(35)]
    pdf = build_labels_pdf(items, "avery5160", start_offset=3)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000  # 35 QR codes across 2 pages

    pdf2 = build_labels_pdf(items[:5], "avery5163")
    assert pdf2.startswith(b"%PDF")


def test_crypto_round_trip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "round-trip-secret")
    from services.crypto import decrypt_json, encrypt_json

    blob = encrypt_json({"refresh_token": "sensitive-value"})
    assert b"sensitive-value" not in blob
    assert decrypt_json(blob) == {"refresh_token": "sensitive-value"}
    assert decrypt_json(None) is None
