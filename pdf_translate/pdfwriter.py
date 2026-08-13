def make_pdf(image_paths, output_path):
    from PIL import Image

    bodies = {}

    def add_obj(obj_id, body):
        bodies[obj_id] = body

    add_obj(1, "<< /Type /Pages /Kids [] /Count 0 >>")

    next_id = 2
    page_ids = []
    for img_path in image_paths:
        with Image.open(img_path) as im:
            width, height = im.size
            dpi_x, dpi_y = im.info.get("dpi", (72.0, 72.0))
            if not dpi_x or not dpi_y:
                dpi_x = dpi_y = 72.0
        page_w = width * 72.0 / dpi_x
        page_h = height * 72.0 / dpi_y
        with open(img_path, "rb") as f:
            data = f.read()
        xobj_id = next_id
        next_id += 1
        content_id = next_id
        next_id += 1
        page_id = next_id
        next_id += 1
        add_obj(
            xobj_id,
            f"<< /Type /XObject /Subtype /Image /Width {width} "
            f"/Height {height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\n"
            f"stream\n{_as_latin(data)}\nendstream",
        )
        content = f"q\n{page_w:.2f} 0 0 {page_h:.2f} 0 0 cm\n/Im0 Do\nQ"
        add_obj(
            content_id,
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        )
        add_obj(
            page_id,
            f"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 {page_w:.2f} {page_h:.2f}] "
            f"/Resources << /XObject << /Im0 {xobj_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>",
        )
        page_ids.append(page_id)

    kids_str = " ".join(f"{k} 0 R" for k in page_ids)
    bodies[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_ids)} >>"
    catalog_id = next_id
    next_id += 1
    add_obj(catalog_id, "<< /Type /Catalog /Pages 1 0 R >>")

    out = ["%PDF-1.4\n"]
    offsets = {}
    for obj_id in sorted(bodies):
        offsets[obj_id] = len("".join(out))
        out.append(f"{obj_id} 0 obj\n{bodies[obj_id]}\nendobj\n")
    xref_pos = len("".join(out))
    size = len(bodies) + 1
    out.append(f"xref\n0 {size}\n0000000000 65535 f \n")
    for obj_id in sorted(bodies):
        out.append(f"{offsets[obj_id]:010d} 00000 n \n")
    out.append(
        f"trailer\n<< /Size {size} /Root {catalog_id} 0 R >>\n"
        "startxref\n"
        f"{xref_pos}\n"
        "%%EOF"
    )
    with open(output_path, "wb") as f:
        f.write("".join(out).encode("latin-1"))


def _as_latin(data):
    return data.decode("latin-1")