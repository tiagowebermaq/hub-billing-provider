"""Fábrica de códigos de barras e PDFs de fatura sintéticos para os testes (sem dados reais)."""
from hub_billing.domain.barcode import mod10, mod11


def make_barcode(segment: str = "3", amount_cents: int = 15_000, value_id: str = "6", company: str = "0045",
                 free: str = "0" * 25) -> str:
    body = "8" + segment + value_id
    rest = f"{amount_cents:011d}" + company + free
    dv = (mod10 if value_id in "67" else mod11)(body + rest)
    return body + dv + rest


def make_line(barcode: str) -> str:
    dv = mod10 if barcode[2] in "67" else mod11
    return " ".join(b + "-" + dv(b) for b in (barcode[i:i + 11] for i in range(0, 44, 11)))


def make_pdf(lines: list[str]) -> bytes:
    """PDF mínimo de uma página com as linhas de texto (Helvetica)."""
    ops = ["BT", "/F1 10 Tf", "40 800 Td", "14 TL"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({safe}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def invoice_lines(barcode: str, account_label: str = "Seu codigo", account: str = "40012345",
                  total: str = "150,00") -> list[str]:
    return ["CONTA DE CONSUMO", f"{account_label}: {account}", "Referencia: 10/2026", "Vencimento 20/11/2026",
            f"Total a pagar R$ {total}", make_line(barcode)]
