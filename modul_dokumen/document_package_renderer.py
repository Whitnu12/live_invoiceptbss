from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from modul_dokumen.master_package_config import persistent_defaults
from modul_dokumen.signature_roles import resolve_approval_roles, signer_text
from modul_po.master_po import is_amount_uom

PACKAGE_ORDER = (
    "proforma-invoice",
    "rincian-pekerjaan",
    "bamp",
    "basp",
    "bastb",
    "wcc",
    "tkdn",
    "opname",
)

NAMES = {
    "proforma-invoice": "Proforma Invoice",
    "rincian-pekerjaan": "Rincian Pekerjaan",
    "bamp": "BAMP",
    "basp": "BASP",
    "bastb": "BASTB",
    "wcc": "WCC",
    "tkdn": "TKDN",
    "opname": "Opname",
}

# Dokumen yang memang mempunyai lebar tabel besar.
LANDSCAPE_CODES = {"rincian-pekerjaan", "opname"}


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first_value(row: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    if not isinstance(row, dict):
        return default
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        value = lowered.get(str(key).strip().lower())
        if value not in (None, ""):
            return value
    return default


def _number(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Rp", "").replace(" ", "")
    try:
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif text.count(".") > 1:
            text = text.replace(".", "")
        elif text.count(",") > 1:
            text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        return float(text or 0)
    except (TypeError, ValueError):
        return default


def _date_id(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        months = (
            "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember",
        )
        return f"{value.day:02d} {months[value.month - 1]} {value.year}"
    text = _clean(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            return _date_id(parsed.date())
        except ValueError:
            pass
    return text


def _image_html(value: Any, max_height: str = "75px", max_width: str = "180px") -> str:
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, bytes):
            encoded = base64.b64encode(value).decode("ascii")
            src = f"data:image/png;base64,{encoded}"
        else:
            text = str(value).strip()
            src = text if text.startswith("data:image/") else f"data:image/png;base64,{text}"
        return (
            f'<img src="{_safe(src)}" style="max-height:{max_height};max-width:{max_width};'
            'object-fit:contain;display:block;margin:0 auto;">'
        )
    except Exception:
        return ""


def _strip_page_rules(css: str) -> str:
    # Remove @page blocks from child documents. Orientation is controlled by
    # the package wrapper so one document cannot change another document's page.
    css = re.sub(r"@page\b[^\{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    return css


def _split_document_html(content: str) -> Tuple[str, str]:
    """Return body fragment + reusable CSS from a generated document."""
    raw = str(content or "").strip()
    if not raw:
        return "", ""

    styles = re.findall(r"<style[^>]*>(.*?)</style>", raw, flags=re.I | re.S)
    css = "\n".join(_strip_page_rules(style) for style in styles)

    body_match = re.search(r"<body[^>]*>(.*?)</body>", raw, flags=re.I | re.S)
    if body_match:
        body = body_match.group(1)
    else:
        body = re.sub(r"<!doctype[^>]*>", "", raw, flags=re.I)
        body = re.sub(r"<html[^>]*>|</html>|<head[^>]*>.*?</head>", "", body, flags=re.I | re.S)

    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.I | re.S)
    return body.strip(), css.strip()


def _section(code: str, content: str) -> str:
    orientation = "landscape-page" if code in LANDSCAPE_CODES else "portrait-page"
    return (
        f'<section class="document-package-section package-document {orientation}" '
        f'data-document-code="{_safe(code)}">'
        f'<div class="package-document-label">{_safe(NAMES.get(code, code))}</div>'
        f'<div class="package-document-content">{content}</div>'
        "</section>"
    )


def _cover(contract: str, pi: str, po: str, included: Iterable[str]) -> str:
    rows = "".join(
        f'<tr><td>{i}</td><td>{_safe(NAMES.get(code, code))}</td></tr>'
        for i, code in enumerate(included, 1)
    )
    return f"""
    <section class="document-package-cover portrait-page">
        <div class="package-cover-inner">
            <h1>PAKET DOKUMEN</h1>
            <table class="package-meta">
                <tr><td>Nomor Kontrak</td><td>{_safe(contract)}</td></tr>
                <tr><td>Proforma Invoice</td><td>{_safe(pi)}</td></tr>
                <tr><td>Nomor PO</td><td>{_safe(po or '-')}</td></tr>
                <tr><td>Dibuat</td><td>{datetime.now():%d-%m-%Y %H:%M}</td></tr>
            </table>
            <h3>Dokumen yang dicetak</h3>
            <table class="package-index">
                <thead><tr><th>No.</th><th>Dokumen</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    </section>
    """


def _render_tkdn(
    saved_tkdn: Dict[str, Any],
    transaksi_list: list,
    nomor_kontrak: str,
    pi_no: str,
) -> str:
    """Gunakan generator TKDN Flask yang sudah ada, bukan membuat template kedua."""
    from modul_dokumen.tkdn import generate_tkdn_html

    return generate_tkdn_html(
        transaksi_list=list(transaksi_list or []),
        nomor_kontrak=nomor_kontrak,
        pi_no=pi_no,
        params=dict(saved_tkdn or {}),
    )


def _render_opname(
    saved: Dict[str, Any],
    transaksi_list: list,
    nomor_kontrak: str,
    pi_no: str,
    nomor_po: str,
    master_pi: Optional[Dict[str, Any]] = None,
) -> str:
    """Render Opname tersimpan mengikuti struktur dokumen Opname lama.

    Jika service sudah menyimpan HTML final, HTML tersebut diprioritaskan.
    Jika yang tersimpan hanya parameter, dokumen direkonstruksi dari parameter
    dan snapshot transaksi PI/PO yang sama.
    """
    if not saved:
        raise ValueError("Opname belum memiliki data tersimpan.")

    for key in ("html", "rendered_html", "html_content", "document_html"):
        if isinstance(saved.get(key), str) and saved[key].strip():
            return saved[key]

    rows = list(transaksi_list or [])
    target_po = _clean(nomor_po)
    if target_po:
        po_rows = [
            row for row in rows
            if _clean(_first_value(row, "nomor_po", "Nomor PO", "No PO")) == target_po
        ]
        if po_rows:
            rows = po_rows
    metadata_row = rows[0] if rows else {}

    items = saved.get("items") or {}
    if not isinstance(items, dict):
        items = {}

    # Sumber utama Opname adalah tabel database_opname. Karena satu PI dapat
    # memiliki item yang tersimpan sebagai beberapa row SQL, bentuk rows
    # dokumen langsung dari items SQL. Transaksi PI tetap dipakai untuk
    # metadata seperti judul/CTR/WO/PO jika tersedia.
    if items:
        db_rows = []
        for key, item in sorted(items.items(), key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 999999):
            if not isinstance(item, dict):
                continue
            db_rows.append({
                "item_no": key,
                "kategori": item.get("kategori", ""),
                "deskripsi_pekerjaan": item.get("description", ""),
                "keterangan": item.get("keterangan", ""),
                "uom": item.get("uom", ""),
                "qty": item.get("po_vol", 0),
                "harga_satuan": item.get("unit_price", 0),
                "nomor_po": saved.get("nomor_po", target_po),
            })
        if db_rows:
            rows = db_rows

    if not rows:
        raise ValueError("Opname tersimpan tetapi item database_opname tidak ditemukan.")

    first = metadata_row or rows[0]

    percent_val = _number(_first_value(first, "percent", "Percent", "%", default=100), 100.0)
    active_lokasi = _clean(saved.get("lokasi_office")) or "Luwuk"
    opname_date = _date_id(saved.get("tanggal_opname") or saved.get("opname_date") or saved.get("main_date"))
    if not opname_date:
        opname_date = _date_id(date.today())

    rows_html = []
    sums = [0.0] * 10  # PO vol, base, prev vol, prev total, curr vol, curr total, cum vol, cum total, sisa vol, sisa total

    for idx, row in enumerate(rows, start=1):
        kategori = _clean(_first_value(row, "kategori", "Kategori"))
        deskripsi = _clean(_first_value(row, "deskripsi_pekerjaan", "Deskripsi Pekerjaan", "description"))
        keterangan = _clean(_first_value(row, "keterangan", "Keterangan"))
        raw_harga = _number(_first_value(row, "harga_satuan", "Harga Satuan", "unit_price"))
        qty = _number(_first_value(row, "qty", "Qty", "volume"), 1.0)
        unit = _clean(_first_value(row, "unit", "Unit", "uom")) or ("AU" if "provisional" in kategori.lower() else "Day")

        row_item_no = _first_value(row, "item_no", "Item No", "item_index", default=idx)
        try:
            row_item_no_int = int(row_item_no)
        except (TypeError, ValueError):
            row_item_no_int = idx
        item = items.get(row_item_no_int, items.get(str(row_item_no_int), {}))
        if not isinstance(item, dict):
            item = {}

        is_prov = "provisional" in kategori.lower() or "professional" in kategori.lower()
        is_est = "estimated" in kategori.lower() or "estimasi" in kategori.lower()
        is_amount_based = bool(item.get("is_amount_based")) or (is_prov and is_amount_uom(unit))

        po_vol = _number(item.get("po_vol"), qty)
        unit_price = _number(item.get("unit_price"), raw_harga * (1.15 if is_prov else 1.0))
        prev_vol = _number(item.get("prev_vol"), 0.0)
        current_vol = _number(item.get("current_vol"), qty)
        if is_amount_based:
            # Nominal PS is tracked in currency, not as consumed volume.
            po_vol = prev_vol = current_vol = 0.0

        formula_factor = _number(
            item.get("formula_factor"),
            1.15 if is_prov else 0.90 if is_est else 1.0,
        )
        factor = formula_factor * percent_val / 100.0
        raw_po_total = item.get("po_total")
        base_price = (
            _number(raw_po_total)
            if raw_po_total not in (None, "")
            else po_vol * unit_price * factor
        )
        prev_total = _number(item.get("prev_total"), prev_vol * unit_price * factor)
        curr_total = _number(item.get("current_total"), current_vol * unit_price * factor)
        cum_vol = _number(item.get("cumulative_vol"), prev_vol + current_vol)
        cum_total = _number(item.get("cumulative_total"), prev_total + curr_total)
        sisa_vol = (0.0 if is_amount_based
                    else _number(item.get("remaining_vol"), po_vol - cum_vol))
        sisa_total = _number(item.get("remaining_total"), base_price - cum_total)

        values = [po_vol, base_price, prev_vol, prev_total, current_vol, curr_total, cum_vol, cum_total, sisa_vol, sisa_total]
        sums = [a + b for a, b in zip(sums, values)]

        if is_prov:
            base_at_cost = raw_harga * (percent_val / 100.0)
            fee_15 = base_at_cost * 0.15
            unit_display = (
                f"Rp {base_at_cost:,.2f}<br>"
                f"<span class='small-muted'>+ 15% Fee (Rp {fee_15:,.2f})</span>"
            )
        elif is_est:
            nominal_diskon = unit_price * 0.1
            unit_display = (
                f"{unit_price:,.2f}<br>"
                f"<span class='small-muted'>- 10% Diskon (Rp {nominal_diskon:,.2f})</span>"
            )
        else:
            unit_display = f"{unit_price:,.2f}"

        desc_html = f"<b>{_safe(kategori)}</b><br>{_safe(deskripsi)}"
        if keterangan:
            desc_html += f"<br><span class='small-muted'>{_safe(keterangan)}</span>"

        rows_html.append(f"""
            <tr>
                <td>1.{idx}</td>
                <td class="text-left">{desc_html}</td>
                <td>{_safe(unit)}</td>
                <td>{'-' if is_amount_based else f'{po_vol:,.2f}'}</td>
                <td class="text-right">{unit_display}</td>
                <td class="text-right">{base_price:,.2f}</td>
                <td>{'-' if is_amount_based else f'{prev_vol:,.2f}'}</td>
                <td class="text-right">{prev_total:,.2f}</td>
                <td>{'-' if is_amount_based else f'{current_vol:,.2f}'}</td>
                <td class="text-right">{curr_total:,.2f}</td>
                <td>{'-' if is_amount_based else f'{cum_vol:,.2f}'}</td>
                <td class="text-right">{cum_total:,.2f}</td>
                <td>{'-' if is_amount_based else f'{sisa_vol:,.2f}'}</td>
                <td class="text-right">{sisa_total:,.2f}</td>
            </tr>
        """)

    master = master_pi if isinstance(master_pi, dict) else {}
    wo_title = (
        _clean(saved.get("job_title"))
        or _clean(_first_value(first, "keterangan_wo", "Keterangan WO", "deskripsi_po", "Deskripsi PO"))
        or _clean(_first_value(master, "job_title", "judul_kontrak", "nama_kontrak", "Nama Kontrak"))
        or "General Services"
    )
    ctr = _clean(_first_value(first, "nomor_ctr", "Nomor CTR"))
    wo = _clean(_first_value(first, "nomor_wo", "Nomor WO"))
    po = target_po or _clean(_first_value(first, "nomor_po", "Nomor PO"))
    refs = [x for x in (ctr, wo, po) if x and x != "-"]
    ctr_wo_po = " / ".join(dict.fromkeys(refs)) or "-"

    # Route paket mengirim master PI yang telah dipilih menurut kontrak + PI.
    # Gunakan baris itu sebagai satu sumber nama/jabatan penandatangan.
    signer_source = master_pi if isinstance(master_pi, dict) else first
    prepared_name = signer_text(_first_value(signer_source, "prepared_by_name", "Prepared by Name"))
    prepared_title = signer_text(_first_value(signer_source, "prepared_by_title", "Prepared by Title"))
    app1_name = _first_value(signer_source, "approved_by_1", "Approved by 1")
    app1_title = _first_value(signer_source, "approved_by_title_1", "Approved by Title 1")
    app2_name = _first_value(signer_source, "approved_by_2", "Approved by 2")
    app2_title = _first_value(signer_source, "approved_by_title_2", "Approved by Title 2")
    roles = resolve_approval_roles(app1_name, app1_title, app2_name, app2_title)
    reviewed_name = roles["reviewed_name"]
    reviewed_title = roles["reviewed_title"]
    final_app_name = roles["approved_name"]
    final_app_title = roles["approved_title"]

    logo1 = _image_html(saved.get("logo_1"), "52px", "180px")
    logo2 = _image_html(saved.get("logo_2"), "50px", "210px")
    ttd1 = _image_html(saved.get("ttd_1"), "75px", "180px") or '<div class="signature-space"></div>'
    ttd3 = _image_html(saved.get("ttd_3"), "75px", "180px") or '<div class="signature-space"></div>'
    ttd2 = _image_html(saved.get("ttd_2"), "75px", "180px") or '<div class="signature-space"></div>'

    if reviewed_name:
        sig_html = f"""
        <table class="sig-table"><tr>
            <td>{_safe(active_lokasi)}, {_safe(opname_date)}<br><b>PT Banggai Sentral Sulawesi</b><br>Prepared by,{ttd1}<u><b>{_safe(prepared_name)}</b></u><br>{_safe(prepared_title)}</td>
            <td><br><b>JOB Pertamina - Medco E&P Tomori Sulawesi</b><br>Reviewed by,{ttd2}<u><b>{_safe(reviewed_name)}</b></u><br>{_safe(reviewed_title)}</td>
            <td><br><b>JOB Pertamina - Medco E&P Tomori Sulawesi</b><br>Approved by,{ttd3}<u><b>{_safe(final_app_name)}</b></u><br>{_safe(final_app_title)}</td>
        </tr></table>
        """
    else:
        sig_html = f"""
        <table class="sig-table"><tr>
            <td>{_safe(active_lokasi)}, {_safe(opname_date)}<br><b>PT Banggai Sentral Sulawesi</b><br>Prepared by,{ttd1}<u><b>{_safe(prepared_name)}</b></u><br>{_safe(prepared_title)}</td>
            <td><br><b>JOB Pertamina - Medco E&P Tomori Sulawesi</b><br>Approved by,{ttd3}<u><b>{_safe(final_app_name)}</b></u><br>{_safe(final_app_title)}</td>
        </tr></table>
        """

    return f"""
    <!doctype html><html lang="id"><head><meta charset="utf-8">
    <title>Berita Acara Pekerjaan / Opname - PT BSS</title>
    <style>
      body{{font-family:Arial,sans-serif;margin:0;color:#000;font-size:9.5px;line-height:1.25}}
      .header-table,.info-table,.opname-grid,.sig-table{{width:100%;border-collapse:collapse}}
      .header-table{{border-bottom:2px solid #000;margin-bottom:6px}}
      .header-table td{{border:none;vertical-align:middle;padding:0}}
      .info-table{{margin-bottom:6px}}
      .info-table td{{padding:2px 4px;border:none}}
      .opname-grid{{table-layout:fixed;margin-bottom:6px}}
      .opname-grid th,.opname-grid td{{border:1px solid #000;padding:4px 5px;font-size:8.2px;vertical-align:middle;text-align:center;overflow-wrap:anywhere;word-break:break-word}}
      .opname-grid th{{background:#f1f5f9;font-weight:bold}}
      .opname-grid td:nth-child(n+4){{padding-left:2px;padding-right:2px;font-size:7.2px;white-space:nowrap;overflow-wrap:normal;word-break:normal;font-variant-numeric:tabular-nums}}
      .text-left{{text-align:left!important}} .text-right{{text-align:right!important}}
      .small-muted{{font-size:7.5px;color:#334155}}
      .summary-box{{margin-top:4px;font-size:10px;font-weight:bold}}
      .sig-table{{margin-top:10px;table-layout:fixed;page-break-inside:avoid;break-inside:avoid}}
      .sig-table td{{border:none;vertical-align:top;font-size:9.5px;padding:2px;text-align:center;overflow-wrap:anywhere}}
      .signature-space{{height:75px}}
    </style>
    </head><body>
      <table class="header-table"><tr>
        <td style="width:25%;text-align:left;padding-left:25px">{logo1}</td>
        <td style="width:50%;text-align:center"><h3 style="margin:0;font-size:11px;text-transform:uppercase">BERITA ACARA PEKERJAAN / OPNAME</h3></td>
        <td style="width:25%;text-align:right;padding-right:25px">{logo2}</td>
      </tr></table>
      <table class="info-table">
        <tr><td style="width:18%;font-weight:bold">JOB TITLE / WO / PO</td><td style="width:2%">:</td><td style="width:80%"><b>{_safe(wo_title)}</b></td></tr>
        <tr><td style="font-weight:bold">CTR / WO / PO No.</td><td>:</td><td>{_safe(ctr_wo_po)}</td></tr>
        <tr><td style="font-weight:bold">DATE</td><td>:</td><td>{_safe(opname_date)}</td></tr>
        <tr><td style="font-weight:bold;color:#065f46">PROFORMA INVOICE No.</td><td style="color:#065f46">:</td><td><b>{_safe(pi_no)}</b></td></tr>
      </table>
      <table class="opname-grid">
        <colgroup>
          <col style="width:3%"><col style="width:22%"><col style="width:5%">
          <col style="width:5%"><col style="width:7.5%"><col style="width:7.5%">
          <col style="width:4%"><col style="width:8%">
          <col style="width:4%"><col style="width:8%">
          <col style="width:4%"><col style="width:9%">
          <col style="width:4%"><col style="width:9%">
        </colgroup>
        <thead>
          <tr>
            <th rowspan="2">NO</th><th rowspan="2">ITEM - DESCRIPTION</th><th rowspan="2">UOM</th>
            <th colspan="3">BASE ON CTR / PO</th><th colspan="2">PREVIOUS OPNAME (IDR)</th>
            <th colspan="2">AKTUAL OPNAME (BULAN INI) (IDR)</th><th colspan="2">CUMMULATIVE OPNAME (IDR)</th><th colspan="2">SISA ANGGARAN (DEVIASI) (IDR)</th>
          </tr>
          <tr><th>VOLUME</th><th>UNIT PRICE (IDR)</th><th>TOTAL PRICE (IDR)</th><th>VOLUME</th><th>TOTAL PRICE</th><th>VOLUME</th><th>TOTAL PRICE</th><th>VOLUME</th><th>TOTAL PRICE</th><th>VOLUME</th><th>TOTAL PRICE</th></tr>
        </thead>
        <tbody>{''.join(rows_html)}
          <tr style="background:#fafafa;font-weight:bold">
            <td colspan="3" class="text-right">TOTAL :</td><td>{sums[0]:,.2f}</td><td class="text-right">-</td><td class="text-right">{sums[1]:,.2f}</td>
            <td>{sums[2]:,.2f}</td><td class="text-right">{sums[3]:,.2f}</td><td>{sums[4]:,.2f}</td><td class="text-right">{sums[5]:,.2f}</td>
            <td>{sums[6]:,.2f}</td><td class="text-right">{sums[7]:,.2f}</td><td>{sums[8]:,.2f}</td><td class="text-right">{sums[9]:,.2f}</td>
          </tr>
        </tbody>
      </table>
      <div class="summary-box">Total Akumulasi Penyerapan (Cumulative Opname): Rp {sums[7]:,.2f}<br>Sisa Nilai Anggaran PO (Deviasi): Rp {sums[9]:,.2f}</div>
      {sig_html}
    </body></html>
    """


def render_saved_opname_html(
    data: Dict[str, Any],
    transaksi_list: Optional[list] = None,
    nomor_kontrak: str = "",
    pi_no: str = "",
    nomor_po: str = "",
    master_pi: Optional[Dict[str, Any]] = None,
) -> str:
    return _render_opname(data, list(transaksi_list or []), nomor_kontrak, pi_no, nomor_po, master_pi)


def render_document_package(
    *,
    codes: Optional[Iterable[str]],
    transaksi_list: list,
    nomor_kontrak: str,
    pi_no: str,
    nomor_po: str = "",
    render_generator: Optional[Callable[..., str]] = None,
    saved_tkdn: Optional[Dict[str, Any]] = None,
    saved_opname: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    if render_generator is None:
        from document_engine import render_document as _engine_render_document

        def render_generator(*, code, transaksi_list, nomor_kontrak, pi_no, params=None):
            return _engine_render_document(
                code=code,
                transaksi_list=transaksi_list,
                nomor_kontrak=nomor_kontrak,
                pi_no=pi_no,
                params=params or {},
            )

    package_params = dict(params or {})
    # Keep the legacy permanent logos as defaults, while allowing the current
    # upload controls to override them or explicitly clear them with "".
    legacy_defaults = persistent_defaults()
    for asset_key, asset_value in legacy_defaults.items():
        if asset_key not in package_params:
            package_params[asset_key] = asset_value

    requested = [
        _clean(x).lower()
        for x in (codes or PACKAGE_ORDER)
        if _clean(x).lower() in PACKAGE_ORDER
    ]
    requested = list(dict.fromkeys(requested))

    parts = []
    errors = []
    included = []
    collected_css = []

    source = list(transaksi_list or [])

    for code in requested:
        try:
            if code == "tkdn":
                if not saved_tkdn:
                    continue
                tkdn_payload = dict(saved_tkdn)
                tkdn_payload["logo_1"] = package_params.get("logo_2") or package_params.get("logo_1")
                tkdn_payload["logo_2"] = package_params.get("logo_3") or package_params.get("logo_2")
                if package_params.get("master_pi"):
                    tkdn_payload["master_pi"] = package_params["master_pi"]
                rendered = _render_tkdn(tkdn_payload, source, nomor_kontrak, pi_no)
            elif code == "opname":
                if not saved_opname:
                    continue
                opname_payload = dict(saved_opname)
                if not opname_payload.get("lokasi_office") and package_params.get("lokasi"):
                    opname_payload["lokasi_office"] = package_params["lokasi"]
                opname_assets = dict(package_params)
                if package_params.get("logo_2") or package_params.get("logo_3"):
                    opname_assets["logo_1"] = package_params.get("logo_2")
                    opname_assets["logo_2"] = package_params.get("logo_3")
                for asset_key in ("logo_1", "logo_2", "logo_3", "ttd_1", "ttd_2", "ttd_3"):
                    if not opname_payload.get(asset_key) and opname_assets.get(asset_key):
                        opname_payload[asset_key] = opname_assets[asset_key]
                rendered = render_saved_opname_html(
                    opname_payload,
                    source,
                    nomor_kontrak,
                    pi_no,
                    nomor_po,
                    master_pi=package_params.get("master_pi"),
                )
            else:
                item_params = dict(package_params)
                # Master Paket Batch mempunyai tiga logo: JOB/Pihak Pertama,
                # PT BSS/Pihak Kedua, dan ISO/Pendukung. Dokumen internal
                # memakai PT BSS di kiri dan ISO di kanan; BAMP/BASP/BASTB
                # tetap memakai pasangan Pihak Pertama/Pihak Kedua.
                if code in {"proforma-invoice", "rincian-pekerjaan", "wcc", "tkdn"}:
                    item_params["logo_1"] = package_params.get("logo_2") or package_params.get("logo_1")
                    item_params["logo_2"] = package_params.get("logo_3") or package_params.get("logo_2")
                date_overrides = package_params.get("dates") or {}
                if code in date_overrides and date_overrides.get(code):
                    item_params["main_date"] = date_overrides[code]
                rendered = render_generator(
                    code=code,
                    transaksi_list=source,
                    nomor_kontrak=nomor_kontrak,
                    pi_no=pi_no,
                    params=item_params,
                )

            body, css = _split_document_html(rendered)
            if not body:
                raise ValueError("Generator menghasilkan HTML kosong.")
            if css:
                collected_css.append(css)
            parts.append(_section(code, body))
            included.append(code)
        except Exception as exc:
            errors.append(f"{NAMES.get(code, code)}: {exc}")

    if not parts:
        raise ValueError(
            "Tidak ada dokumen yang dapat dicetak. "
            + ("; ".join(x.strip() for x in errors) if errors else "")
        )

    warning = ""
    if errors:
        warning = (
            '<aside class="package-warning no-print"><b>Dokumen dilewati:</b><ul>'
            + "".join(f"<li>{_safe(x.strip())}</li>" for x in errors)
            + "</ul></aside>"
        )

    embedded_css = "\n".join(collected_css)

    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paket Dokumen - {_safe(pi_no)}</title>
<style>
@page {{ size:A4 portrait; margin:7mm; }}
@page portrait {{ size:A4 portrait; margin:7mm; }}
@page landscape {{ size:A4 landscape; margin:7mm; }}

*,*::before,*::after {{ box-sizing:border-box; }}
html,body {{ margin:0; padding:0; width:100%; max-width:100%; }}
body {{ font-family:Arial,sans-serif; color:#111; background:#fff; overflow:visible; }}

.document-package-cover,
.document-package-section {{
    width:100%; max-width:100%; margin:0; padding:0;
    box-sizing:border-box; overflow:visible;
}}
.document-package-cover {{ page:portrait; break-after:page; page-break-after:always; min-height:270mm; }}
.document-package-cover:last-child {{ break-after:auto; page-break-after:auto; }}
.package-cover-inner {{ padding:20mm 12mm; }}
.package-cover-inner h1 {{ text-align:center; margin:0 0 20mm; font-size:22px; }}
.package-meta,.package-index {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
.package-meta td {{ padding:7px 8px; border-bottom:1px solid #ddd; overflow-wrap:anywhere; word-break:break-word; }}
.package-meta td:first-child {{ width:35%; font-weight:700; }}
.package-index {{ margin-top:8mm; }}
.package-index th,.package-index td {{ border:1px solid #222; padding:6px; font-size:10px; overflow-wrap:anywhere; }}
.package-index th {{ background:#f1f5f9; }}

.document-package-section {{ break-after:page; page-break-after:always; }}
.document-package-section:last-of-type {{ break-after:auto; page-break-after:auto; }}
.portrait-page {{ page:portrait; }}
.landscape-page {{ page:landscape; }}
.package-document-content {{ width:100%; max-width:100%; overflow:visible; }}
.package-document-content table {{ max-width:100%; }}
.package-document-content img {{ max-width:100%; height:auto; }}
.package-document-content td,.package-document-content th {{ overflow-wrap:anywhere; word-break:break-word; white-space:normal; }}
.package-document-content tr {{ break-inside:avoid; page-break-inside:avoid; }}
.package-document-content .signature-space {{ max-height:80px; }}
.package-document-label {{ display:none; }}
.package-warning {{ margin:12px 0; padding:10px; border:1px solid #c00; background:#fff3cd; font-size:10px; }}

{embedded_css}

/* Child documents may carry standalone A4 dimensions on html/body. Inside a
   batch those dimensions would exceed the wrapper's printable area. */
html,body {{ width:100%!important; max-width:100%!important; height:auto!important; min-width:0!important; padding:0!important; }}
.document-package-section,
.package-document-content {{ min-width:0!important; }}
.document-package-section[data-document-code="rincian-pekerjaan"] .info-table,
.document-package-section[data-document-code="rincian-pekerjaan"] .data-table {{
    width:100%!important; max-width:100%!important; table-layout:fixed!important;
}}
.document-package-section[data-document-code="rincian-pekerjaan"] .info-table td,
.document-package-section[data-document-code="rincian-pekerjaan"] .data-table th,
.document-package-section[data-document-code="rincian-pekerjaan"] .data-table td {{
    min-width:0!important; white-space:normal!important;
    overflow-wrap:anywhere!important; word-break:normal!important;
}}
.document-package-section[data-document-code="rincian-pekerjaan"] .data-table td:nth-child(6),
.document-package-section[data-document-code="rincian-pekerjaan"] .data-table td:nth-child(7) {{
    overflow-wrap:normal!important;
}}
.document-package-section .iso-footer-left,
.document-package-section .bamp-document-code,
.document-package-section .basp-document-code,
.document-package-section .bastb-document-code {{
    position:static!important; inset:auto!important; display:block!important;
    clear:both!important; margin-top:8mm!important; padding-top:4px!important;
}}
.document-package-section[data-document-code="opname"] .opname-grid,
.document-package-section[data-document-code="opname"] .doc-grid {{
    width:100%!important; max-width:100%!important; table-layout:fixed!important;
}}
.document-package-section[data-document-code="opname"] .opname-grid th,
.document-package-section[data-document-code="opname"] .doc-grid th {{
    padding:3px 2px!important; font-size:6.8px!important; line-height:1.15!important;
    white-space:normal!important; overflow-wrap:normal!important; word-break:normal!important;
}}
.document-package-section[data-document-code="opname"] .opname-grid td,
.document-package-section[data-document-code="opname"] .doc-grid td {{
    padding:3px 2px!important; font-size:7.2px!important; line-height:1.15!important;
}}
.document-package-section[data-document-code="opname"] .opname-grid td:nth-child(n+4),
.document-package-section[data-document-code="opname"] .doc-grid td:nth-child(n+4) {{
    white-space:nowrap!important; overflow-wrap:normal!important; word-break:normal!important;
    font-size:6.8px!important; font-variant-numeric:tabular-nums;
}}

@media print {{
    html,body {{ width:auto!important; max-width:none!important; height:auto!important; margin:0!important; padding:0!important; background:#fff!important; overflow:visible!important; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    .no-print {{ display:none!important; }}
    .document-package-section {{ width:100%!important; margin:0!important; max-width:none!important; box-shadow:none!important; }}
    .package-document-content {{ width:100%!important; max-width:100%!important; }}
    table {{ max-width:100%!important; }}
    td,th {{ overflow-wrap:anywhere!important; word-break:break-word!important; white-space:normal!important; }}
}}
</style></head><body>
{_cover(nomor_kontrak,pi_no,nomor_po,included)}
{warning}
{''.join(parts)}
<script>
(function() {{
  function waitForImages() {{
    const imgs = Array.from(document.images || []);
    return Promise.all(imgs.map(function(img) {{
      if (img.complete) return Promise.resolve();
      return new Promise(function(resolve) {{ img.addEventListener('load', resolve, {{once:true}}); img.addEventListener('error', resolve, {{once:true}}); }});
    }}));
  }}
  window.addEventListener('load', function() {{
    waitForImages().then(function() {{ document.documentElement.setAttribute('data-print-ready','1'); }});
  }});
}})();
</script>
</body></html>"""
