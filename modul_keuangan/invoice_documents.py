"""Data adapter for the unchanged BSS Project invoice and receipt layouts."""

import base64
import html
import mimetypes
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from markupsafe import Markup, escape


ASSET_DIR = Path(__file__).resolve().parent / "database_penyimpanan_aman"
DEFAULT_BANK = (
    "<b>Bank Name :</b> BANK RAKYAT INDONESIA (PERSERO) Tbk.<br>"
    "<b>Branch :</b> Cabang Luwuk<br>"
    "<b>Account No :</b> 0167 0167 8888 303<br>"
    "<b>Account Name :</b> PT. BANGGAI SENTRAL SULAWESI"
)


def money(value):
    try:
        return Decimal(str(value or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def money_text(value, cents=False):
    number = Decimal(str(value or 0))
    return f"Rp {number:,.2f}" if cents else f"Rp {money(number):,.0f}"


def _image(name, style):
    path = ASSET_DIR / name
    if not path.is_file():
        return Markup('<div style="height: 65px;"></div>') if name.startswith("persistent_ttd_") else Markup("")
    mime = mimetypes.guess_type(name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return Markup(f'<img src="data:{mime};base64,{data}" style="{style}">')


def _bank_html(value):
    # The legacy bank field contains only <b> and <br> formatting.
    source = value or DEFAULT_BANK
    escaped = html.escape(str(source))
    return Markup(re.sub(r"&lt;(/?b|br\s*/?)&gt;", lambda m: "<" + m.group(1) + ">", escaped, flags=re.I))


def _item_row(index, description, details, unit, unit_price, amount, muted=False):
    description = escape(description or "-")
    details = Markup(details)
    unit = escape(unit or "-")
    price = "-" if muted else money_text(unit_price, cents=True)
    total = money_text(amount, cents=not muted)
    return Markup(f'''
        <tr>
            <td style="border: 1px solid #94a3b8; padding: 12px 10px; text-align: center; vertical-align: top !important; font-size: 11.5px; font-weight: bold; background-color: #fafafa;">{index}</td>
            <td style="border: 1px solid #94a3b8; padding: 12px 10px; vertical-align: top !important; word-break: break-word;">
                <div style="font-size: 11.5px; font-weight: bold; color: #0f172a; margin-bottom: 4px;">{description}</div>{details}
            </td>
            <td style="border: 1px solid #94a3b8; padding: 12px 10px; text-align: center; vertical-align: top !important; font-size: 11.5px;">{unit}</td>
            <td style="border: 1px solid #94a3b8; padding: 12px 10px; text-align: right; vertical-align: top !important; font-size: 11.5px;">{price}</td>
            <td style="border: 1px solid #94a3b8; padding: 12px 10px; text-align: right; vertical-align: top !important; font-size: 11.5px; font-weight: bold;">{total}</td>
        </tr>''')


def _summary_row(label, amount, *, total=False, negative=False, subdued=False):
    color = "#b91c1c" if negative else "#475569" if subdued else "#0f172a"
    value = f"({money_text(amount)})" if negative else money_text(amount)
    extra = "background-color: #f1f5f9; font-weight: bold;" if total else ""
    border = "border-top: 2px solid #0f172a;" if total else "border-bottom: 1px solid #cbd5e1;"
    return Markup(f'<tr style="{extra}"><td style="{border} padding: 7px 10px; color: {color};">{escape(label)}</td>'
                  f'<td style="{border} padding: 7px 10px; text-align: right; color: {color};">{value}</td></tr>')


def invoice_context(invoice, transactions):
    """Map normalized records to the BSS Project document, never recalculate saved taxes."""
    record = dict(invoice)
    items = list(transactions or [])
    if record.get("nomor_sa_wan"):
        items = [item for item in items if str(item.get("nomor_wan_sa") or "").strip() == str(record["nomor_sa_wan"]).strip()]

    rows = []
    scope_rows = []
    if record.get("gunakan_professional_sum"):
        note = escape(record.get("keterangan") or "")
        rows.append(_item_row(1, "At Cost", Markup(f'<div style="font-size: 11px; color: #334155; line-height: 1.4;">{note}</div>'), "-", 0, record.get("add_cost"), muted=True))
        rows.append(_item_row(2, "Management Fee / Handling Fee (15%)", Markup('<div style="font-size: 11px; color: #334155; line-height: 1.4;">Layanan manajemen &amp; pengelolaan operasional terkait</div>'), "-", 0, record.get("management_fee"), muted=True))
    elif items:
        for index, item in enumerate(items, 1):
            qty = Decimal(str(item.get("qty") or 0))
            price = Decimal(str(item.get("harga_satuan") or 0))
            detail = Markup("")
            if item.get("kategori"):
                detail += Markup(f'<div style="font-size: 10.5px; color: #475569; margin-bottom: 2px;"><b>Kategori:</b> {escape(item["kategori"])}</div>')
            if item.get("keterangan"):
                detail += Markup(f'<div style="font-size: 10.5px; color: #334155; line-height: 1.35;">{escape(item["keterangan"])}</div>')
            scope_detail = []
            if item.get("kategori"):
                scope_detail.append(Markup('<span class="scope-category">') + escape(item["kategori"]) + Markup('</span>'))
            if item.get("keterangan"):
                scope_detail.append(escape(item["keterangan"]).replace("\n", Markup("<br>")))
            scope_rows.append(
                Markup('<li><strong>') + escape(item.get("deskripsi_pekerjaan") or "Lingkup pekerjaan") + Markup('</strong>')
                + (Markup('<div class="scope-detail">') + Markup('<br>').join(scope_detail) + Markup('</div>') if scope_detail else Markup(""))
                + Markup('</li>')
            )
            rows.append(_item_row(index, item.get("deskripsi_pekerjaan"), detail,
                                  f'{qty:,.2f} {item.get("unit") or ""}', price, qty * price))
    else:
        rows.append(_item_row(1, record.get("keterangan"), Markup(""), "-", 0,
                              record.get("nilai_gross"), muted=True))
        if record.get("keterangan"):
            scope_rows.append(Markup('<li>') + escape(record["keterangan"]).replace("\n", Markup("<br>")) + Markup('</li>'))

    gross = money(record.get("nilai_gross"))
    discount = money(record.get("diskon_nominal"))
    dpp = money(record.get("nilai_dpp"))
    ppn = money(record.get("ppn_nominal"))
    pph = money(record.get("pph_nominal"))
    summary = []
    if discount > 0:
        summary.extend([_summary_row("Gross Subtotal", gross), _summary_row("Discount (10%)", discount, negative=True)])
    summary.append(_summary_row("Total Amount Due", dpp))
    summary.append(_summary_row("DPP Nilai Lain (11/12)", money(dpp * Decimal(11) / Decimal(12)) if ppn > 0 else dpp, subdued=True))
    summary.append(_summary_row("VAT (PPN 11%)", ppn))
    if pph > 0:
        summary.append(_summary_row("Potongan PPh", pph, negative=True))
    summary.append(_summary_row("T O T A L", record.get("total_netto"), total=True))

    record["nomor_kontrak"] = record.get("nomor_kontrak") or "-"
    record["nomor_po"] = record.get("nomor_po") or "-"
    record["tanggal_invoice"] = str(record.get("tanggal_invoice") or "-")
    record["jatuh_tempo"] = str(record.get("jatuh_tempo") or "-")
    if not scope_rows:
        scope_rows.append(Markup('<li>Lingkup pekerjaan sesuai PI yang dirujuk.</li>'))
    pi_total = sum((Decimal(str(item.get("qty") or 0)) * Decimal(str(item.get("harga_satuan") or 0)) for item in items), Decimal(0))
    if not pi_total:
        pi_total = money(record.get("nilai_gross"))
    return {
        "invoice": record, "item_rows": Markup("\n").join(rows),
        "scope_rows": Markup("\n").join(scope_rows), "pi_total": money_text(pi_total),
        "summary_rows": Markup("\n").join(summary),
        "bank_info": _bank_html(record.get("informasi_bank")),
        "logo_bss": _image("persistent_logo_bss.png", "max-height: 70px; max-width: 130px; object-fit: contain;"),
        "logo_iso": _image("persistent_logo_iso.png", "max-height: 75px; max-width: 210px; object-fit: contain;"),
        "signature": _image("persistent_ttd_direktur.png", "max-height: 75px; max-width: 160px; object-fit: contain; display: block; margin: 0 auto;"),
        "printed_at": datetime.now().strftime("%m/%d/%Y, %I:%M %p"),
    }


def terbilang(number):
    """Original BSS Indonesian number spelling, trimmed for document insertion."""
    number = int(number)
    if number < 0:
        return "minus " + terbilang(-number)
    words = ["", "Satu", "Dua", "Tiga", "Empat", "Lima", "Enam", "Tujuh", "Delapan", "Sembilan", "Sepuluh", "Sebelas"]
    if number < 12:
        return " " + words[number]
    if number < 20:
        return terbilang(number - 10) + " Belas"
    if number < 100:
        return terbilang(number // 10) + " Puluh" + terbilang(number % 10)
    if number < 200:
        return " Seratus" + terbilang(number - 100)
    if number < 1000:
        return terbilang(number // 100) + " Ratus" + terbilang(number % 100)
    if number < 2000:
        return " Seribu" + terbilang(number - 1000)
    if number < 1000000:
        return terbilang(number // 1000) + " Ribu" + terbilang(number % 1000)
    if number < 1000000000:
        return terbilang(number // 1000000) + " Juta" + terbilang(number % 1000000)
    if number < 1000000000000:
        return terbilang(number // 1000000000) + " Miliar" + terbilang(number % 1000000000)
    return " Angka terlalu besar"


def receipt_context(invoice):
    record = dict(invoice)
    total = money(record.get("total_netto"))
    reference = str(record.get("nomor_invoice") or "")
    po = str(record.get("nomor_po") or "").strip()
    wan = str(record.get("nomor_sa_wan") or "").strip()
    text = Markup("Pelunasan biaya pekerjaan berdasarkan Invoice No. <b>") + escape(reference) + Markup("</b>")
    if po and po != "-":
        text += Markup(", Nomor PO <b>") + escape(po) + Markup("</b>")
    if wan and wan != "-":
        text += Markup(", dan Nomor WAN/SA <b>") + escape(wan) + Markup("</b>")
    text += Markup(".")
    invoice_date = record.get("tanggal_invoice")
    if isinstance(invoice_date, str):
        try:
            invoice_date = date.fromisoformat(invoice_date)
        except ValueError:
            pass
    receipt_date = invoice_date.strftime("%d %B %Y") if isinstance(invoice_date, (date, datetime)) else str(invoice_date or "-")
    formatted = f"Rp {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return {
        "invoice": record, "reference_no": reference.replace("/", "-"),
        "reference_text": text, "receipt_date": receipt_date,
        "amount_formatted": formatted,
        "terbilang_text": (terbilang(total).strip() + " Rupiah") if total > 0 else "Nol Rupiah",
    }
