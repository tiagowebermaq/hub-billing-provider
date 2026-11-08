from datetime import date
from decimal import Decimal

from hub_billing.adapters.email_inbox.adapter import EmailAdapter, MailboxConfig, identify, parse_message
from hub_billing.adapters.registry import load_builtin
from tests.unit.emails import forward, make_email
from tests.unit.invoices import invoice_lines, make_barcode, make_line, make_pdf

load_builtin()


def rge_pdf(account: str = "40012345") -> bytes:
    return make_pdf(["RGE SUL DISTRIBUIDORA DE ENERGIA", *invoice_lines(make_barcode("3", 15_000), account=account)])


def test_le_remetente_assunto_anexo_e_data() -> None:
    email = parse_message(make_email(pdfs=[("fatura.pdf", rge_pdf())]))
    assert (email.sender, email.subject, email.message_id) == ("fatura@rge-rs.com.br", "Sua conta RGE chegou", "<m1@rge>")
    assert email.received_at is not None and email.received_at.year == 2026
    assert [a.filename for a in email.pdfs] == ["fatura.pdf"]
    assert "Segue a fatura." in email.body
    assert len(email.id) == 40 and parse_message(make_email()).id == email.id  # mesmo Message-ID, mesma chave


def test_email_encaminhado_traz_o_remetente_original_e_o_pdf() -> None:
    email = parse_message(forward(make_email(pdfs=[("conta.pdf", rge_pdf())])))
    assert email.sender == "weber@gmail.com"
    assert "fatura@rge-rs.com.br" in email.forwarded_from
    assert [a.filename for a in email.pdfs] == ["conta.pdf"]


def test_email_encaminhado_no_padrao_7bit() -> None:
    from email import policy
    from email.message import EmailMessage
    from email.parser import BytesParser

    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Message-ID"] = "weber@gmail.com", "Fwd", "<f2@gmail>"
    msg.set_content("ver anexo")
    msg.add_attachment(BytesParser(policy=policy.default).parsebytes(make_email(pdfs=[("c.pdf", rge_pdf())])))
    email = parse_message(msg.as_bytes())
    assert "fatura@rge-rs.com.br" in email.forwarded_from and [a.filename for a in email.pdfs] == ["c.pdf"]


def test_pdf_com_senha() -> None:
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(rge_pdf())))
    writer.encrypt("12345")
    buf = io.BytesIO()
    writer.write(buf)
    email = parse_message(make_email(pdfs=[("protegida.pdf", buf.getvalue())]))
    sem = EmailAdapter().extract(email)
    assert sem.bills == [] and "protegida.pdf: PDF protegido por senha" in sem.problems[0]
    com = EmailAdapter(["000", "12345"]).extract(email)
    assert [b.raw.account_number for b in com.bills] == ["40012345"]


def test_identifica_concessionaria_pelo_texto_assunto_e_remetente() -> None:
    plain = parse_message(make_email(sender="weber@gmail.com", subject="conta"))
    assert identify(plain, "RGE SUL DISTRIBUIDORA ... grupo CPFL") == ("cpfl", "rge")
    assert identify(plain, "CPFL PAULISTA") == ("cpfl", "paulista")
    assert identify(plain, "Companhia Riograndense de Saneamento CORSAN - Aegea") == ("aegea", "corsan")
    assert identify(plain, "TELEFONICA BRASIL S.A. - Vivo Fibra") == ("vivo", None)
    vivo = parse_message(make_email(sender="nao-responda@vivo.com.br", subject="Sua fatura digital"))
    assert identify(vivo) == ("vivo", None)
    assert identify(plain, "conta sem marca\n" + make_line(make_barcode("2"))) == ("aegea", None)  # pelo segmento
    assert identify(plain, "nada aqui") is None


def test_extrai_fatura_do_pdf_com_origem_no_email() -> None:
    result = EmailAdapter().extract(parse_message(make_email(pdfs=[("fatura.pdf", rge_pdf())])))
    assert result.problems == [] and result.providers == ["cpfl"]
    [bill] = result.bills
    assert bill.distributor == "rge" and bill.document is not None
    raw = bill.raw
    assert (raw.account_number, raw.reference_month, raw.due_date, raw.amount) == (
        "40012345", "2026-10", date(2026, 11, 20), Decimal("150.00"))
    assert raw.payload["source"] == "email" and raw.payload["email"]["message_id"] == "<m1@rge>"


def test_varios_pdfs_e_concessionarias_no_mesmo_email() -> None:
    corsan = make_pdf(["CORSAN", *invoice_lines(make_barcode("2", 9_990), "Matricula", "778899", "99,90")])
    result = EmailAdapter().extract(parse_message(make_email(
        sender="weber@gmail.com", subject="contas", pdfs=[("rge.pdf", rge_pdf()), ("agua.pdf", corsan)])))
    assert [(b.raw.provider, b.distributor, b.raw.account_number) for b in result.bills] == [
        ("cpfl", "rge", "40012345"), ("aegea", "corsan", "778899")]


def test_linha_digitavel_no_corpo_sem_pdf() -> None:
    body = "Vivo - sua fatura\nNúmero da conta: 0999111\nReferência: 10/2026\nVencimento 20/11/2026\n" + make_line(
        make_barcode("4", 12_000))
    result = EmailAdapter().extract(parse_message(make_email(sender="fatura@vivo.com.br", subject="Fatura Vivo",
                                                             body=body)))
    [bill] = result.bills
    assert (bill.raw.provider, bill.raw.account_number, bill.document, bill.source) == (
        "vivo", "0999111", None, "corpo do e-mail")


def test_html_vira_texto() -> None:
    email = parse_message(make_email(body="texto", html="<p>Valor <b>R$ 10,00</b></p><style>x{}</style>"))
    assert "Valor" in email.body and "<b>" not in email.body and "x{}" not in email.body


def test_problemas_viram_lista_para_revisao() -> None:
    sem_dados = make_pdf(["RGE SUL", "Fatura sem valor nem vencimento"])
    result = EmailAdapter().extract(parse_message(make_email(pdfs=[("a.pdf", sem_dados), ("b.pdf", b"lixo")])))
    assert result.bills == []
    assert any(p.startswith("a.pdf: dados não encontrados") for p in result.problems)
    assert any(p.startswith("b.pdf: PDF ilegível") for p in result.problems)
    anonimo = make_pdf(invoice_lines(make_barcode("9")))  # segmento sem concessionária conhecida
    result = EmailAdapter().extract(parse_message(make_email(sender="x@y.com", subject="oi", pdfs=[("c.pdf", anonimo)])))
    assert result.problems == ["c.pdf: concessionária não identificada (CPFL/RGE, Aegea/Corsan ou Vivo)"]


def test_email_sem_fatura_nao_gera_nada() -> None:
    result = EmailAdapter().extract(parse_message(make_email(subject="Newsletter", body="Promoções")))
    assert result.bills == [] and result.problems == []


def test_config_publica_esconde_senhas() -> None:
    cfg = MailboxConfig(host="imap.x", password="segredo", pdf_passwords=["123"])
    assert cfg.configured and cfg.username == "41u6u31@heimber.com.br"
    pub = cfg.public()
    assert "segredo" not in str(pub) and "123" not in str(pub)
    assert pub["has_password"] is True and pub["pdf_passwords"] == 1
    assert not MailboxConfig().configured
