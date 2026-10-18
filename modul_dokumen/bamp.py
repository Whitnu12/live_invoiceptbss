"""
BAMP - Berita Acara Mulai Pekerjaan

Migrasi Flask/Web dari template BAMP lama.
- Tidak menggunakan Streamlit.
- Tidak mengubah layout/template dokumen lama.
- Tetap menggunakan document_engine sebagai dispatcher.
- Header/master dan detail dibaca langsung dari record transaksi yang diberikan.
- Detail pekerjaan tetap mengambil database_transaksi_rincian.
"""

import base64
from datetime import date, datetime
import html

from modul_dokumen.signature_roles import signer_text

BULAN_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

EXCLUDED_KEYWORDS = [
    "material",
    "barang",
    "safety equipment",
    "pengadaan",
    "alat",
    "sparepart",
    "tools",
]

DEFAULT_UOM = "Day"


def text_value(value, default=""):

    if value is None:
        return default
    try:
        if value != value:
            return default
    except Exception:
        pass
    text = str(value).strip()
    return text if text else default

def esc(value):
    return html.escape(text_value(value, ""), quote=True)

def date_id(value):
    if value in (None, ""):
        return "-"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.day} {BULAN_ID[value.month-1]} {value.year}"
    raw = text_value(value, "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return date_id(datetime.strptime(raw[:19], fmt))
        except ValueError:
            pass
    return raw


def date_short(value):
    """Format tanggal untuk tabel identitas: DD-MM-YYYY."""
    parsed = _parse_date(value, None)
    if parsed is None:
        return "-"
    return f"{parsed.day:02d}-{parsed.month:02d}-{parsed.year}"

ALIASES = {
    "nomor_kontrak": ["nomor_kontrak", "Nomor Kontrak"],
    "pi_no": ["pi_no", "PI No.", "Proforma Invoice No."],
    "tanggal_pi": ["tanggal_pi", "Tanggal PI", "Tanggal Performa Invoice"],
    "kategori": ["kategori", "Kategori"],
    "deskripsi": ["deskripsi_pekerjaan", "Deskripsi Pekerjaan", "uraian_pekerjaan", "Uraian Pekerjaan"],
    "qty": ["qty", "Qty", "jumlah", "Jumlah"],
    "unit": ["unit", "Unit", "satuan", "Satuan"],
    "keterangan": ["keterangan", "Keterangan", "catatan", "Catatan"],
    "tanggal_mulai": ["tanggal_mulai", "Tanggal Mulai"],
    "tanggal_kontrak": ["tanggal_kontrak", "Tanggal Kontrak"],
    "lingkup": ["lingkup_pekerjaan", "Lingkup Pekerjaan", "deskripsi_po", "Deskripsi PO"],
    "nomor_po": ["nomor_purchase_order", "Nomor Purchase Order", "nomor_po", "Nomor PO"],
    "tanggal_po": ["tanggal_purchase_order", "Tanggal Purchase Order", "tanggal_po", "Tanggal PO"],
    "pihak_pertama": ["pihak_pertama", "Pihak Pertama"],
    "alamat_pihak_pertama": ["alamat_pihak_pertama", "Alamat Pihak Pertama"],
    "diwakili_oleh": ["diwakili_oleh", "Diwakili Oleh"],
    "selaku": ["selaku", "Selaku"],
    "pihak_kedua": ["pihak_kedua", "Pihak Kedua"],
    "alamat_pihak_kedua": ["alamat_pihak_kedua", "Alamat Pihak Kedua"],
    "diwakili_oleh_p2": ["diwakili_oleh_p2", "Diwakili Oleh (P2)"],
    "selaku_p2": ["selaku_p2", "Selaku (P2)"],
}

def tx_field(row, key, default=""):
    if not isinstance(row, dict):
        return default
    names = ALIASES.get(key, [key])
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        value = lower.get(str(name).strip().lower())
        if value not in (None, ""):
            return value
    return default

def header_context(rows, nomor_kontrak="", pi_no=""):
    if not rows:
        return {}
    row = rows[0]
    mapping = {
        "Nomor Kontrak": "nomor_kontrak", "Tanggal Kontrak": "tanggal_kontrak",
        "Nomor PI": "pi_no", "Tanggal PI": "tanggal_pi",
        "Lingkup Pekerjaan": "lingkup", "Nomor Purchase Order": "nomor_po",
        "Tanggal Purchase Order": "tanggal_po", "Pihak Pertama": "pihak_pertama",
        "Alamat Pihak Pertama": "alamat_pihak_pertama", "Diwakili Oleh": "diwakili_oleh",
        "Selaku": "selaku", "Pihak Kedua": "pihak_kedua",
        "Alamat Pihak Kedua": "alamat_pihak_kedua", "Diwakili Oleh (P2)": "diwakili_oleh_p2",
        "Selaku (P2)": "selaku_p2",
    }
    return {label: tx_field(row, key, "") for label, key in mapping.items()}


def master_value(master, *keys, default=""):
    """Ambil metadata induk PI dengan dukungan nama field DB dan legacy."""
    if not isinstance(master, dict):
        return default
    for key in keys:
        value = master.get(key)
        if text_value(value, ""):
            return value
    lowered = {str(key).strip().lower(): value for key, value in master.items()}
    for key in keys:
        value = lowered.get(str(key).strip().lower())
        if text_value(value, ""):
            return value
    return default


def _is_excluded(row):
    kategori = text_value(tx_field(row, "kategori", ""), "").lower()
    return any(keyword in kategori for keyword in EXCLUDED_KEYWORDS)


def _filter_jasa(rows):
    return [row for row in rows if not _is_excluded(row)]


def _parse_date(value, fallback=None):
    if fallback is None:
        fallback = date.today()

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw = text_value(value, "")
    if not raw:
        return fallback

    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(raw[:30], fmt).date()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(raw[:19]).date()
    except Exception:
        return fallback


def _minimum_start_date(rows):
    dates = []
    for row in rows:
        raw = tx_field(row, "tanggal_mulai", "")
        if text_value(raw, ""):
            dates.append(_parse_date(raw))
    return min(dates) if dates else date.today()


def _image_html(value, max_height, max_width, margin="auto"):
    if not value:
        return ""

    if isinstance(value, str):
        if value.startswith("data:image/"):
            src = value
        else:
            return ""
    else:
        try:
            src = "data:image/png;base64," + base64.b64encode(value).decode("ascii")
        except Exception:
            return ""

    return (
        f'<img src="{src}" '
        f'style="max-height: {max_height}; max-width: {max_width}; '
        f'object-fit: contain; display: block; margin: {margin};" />'
    )


def _row_value(params, index, key, default):
    if isinstance(params, dict) and isinstance(params.get("bamp_items"), dict):
        # Paket mempunyai rincian jumlah khusus BAMP. Jika nilainya kosong,
        # jangan mengambil Qty dari rincian transaksi sebagai pengganti.
        items = params.get("bamp_items")
    else:
        items = params.get("items") if isinstance(params, dict) else None
    if not isinstance(items, dict):
        return default

    item = items.get(index)
    if item is None:
        item = items.get(str(index))
    if not isinstance(item, dict):
        return default

    value = item.get(key)
    while key == "qty" and isinstance(value, dict) and "qty" in value:
        value = value.get("qty")
    return default if value in (None, "") else value


def _build_rows(rows, params):
    result = []

    for idx, row in enumerate(rows, start=1):
        raw_date = tx_field(row, "tanggal_mulai", "")
        default_date = _parse_date(raw_date)
        row_date = _parse_date(
            _row_value(params, idx, "date", default_date),
            default_date,
        )

        qty = _row_value(params, idx, "qty", 0)
        unit = text_value(
            _row_value(params, idx, "uom", tx_field(row, "unit", DEFAULT_UOM)),
            DEFAULT_UOM,
        )
        note = text_value(
            _row_value(params, idx, "catatan", tx_field(row, "keterangan", "")),
            "",
        )

        row_date_str = date_id(row_date)
        if note:
            note_final = note
        else:
            note_final = f"Mulai Berlaku Tanggal {row_date_str}"

        kategori = text_value(tx_field(row, "kategori", ""), "")
        deskripsi = text_value(tx_field(row, "deskripsi", ""), "")

        if kategori:
            desc_html = f"<b>{esc(kategori)}</b><br>{esc(deskripsi)}"
        else:
            desc_html = esc(deskripsi)

        result.append(
            f"""
            <tr>
                <td style="text-align: center;">{idx}</td>
                <td style="text-align: left;">{desc_html}</td>
                <td style="text-align: center;">{html.escape(str(qty))}</td>
                <td style="text-align: center;">{esc(unit)}</td>
                <td style="text-align: left;"><b>{esc(note_final)}</b></td>
            </tr>
            """
        )

    return "\n".join(result)


def _normalize_match(value):
    """Normalisasi nilai kunci untuk pencocokan kontrak/PI."""
    if value is None:
        return ""
    text = str(value)
    for ch in ("\u00a0", "\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(ch, "")
    return " ".join(text.strip().split()).casefold()


def generate_bamp_html(transaksi_list, nomor_kontrak="", pi_no="", params=None):
    """Generate BAMP HTML menggunakan template lama secara konsisten."""
    params = params or {}
    master_pi = params.get("master_pi") or {}

    rows = list(transaksi_list or [])

    # Data dari routes.py sudah difilter berdasarkan kontrak + PI.
    # Filter ulang hanya sebagai pengaman dengan normalisasi Unicode/whitespace.
    if nomor_kontrak:
        target_kontrak = _normalize_match(nomor_kontrak)
        rows = [
            row
            for row in rows
            if _normalize_match(tx_field(row, "nomor_kontrak", "")) == target_kontrak
        ]

    if pi_no:
        target_pi = _normalize_match(pi_no)
        rows = [
            row
            for row in rows
            if _normalize_match(tx_field(row, "pi_no", "")) == target_pi
        ]

    if not rows:
        return (
            "<!doctype html><html><body>"
            "<h3>Data transaksi tidak tersedia.</h3>"
            "</body></html>"
        )

    # BAMP hanya memuat item jasa/layanan seperti template lama.
    rows = _filter_jasa(rows)
    if not rows:
        return (
            "<!doctype html><html><body>"
            "<h3>Tidak ada item kategori Jasa/Layanan.</h3>"
            "</body></html>"
        )

    # Header PI/master berasal dari database_proforma_invoice melalui engine bersama.
    context = header_context(rows, nomor_kontrak=nomor_kontrak, pi_no=pi_no)

    # Jika engine menemukan field di transaksi, fallback tetap berasal dari transaksi.
    nomor_kontrak_str = text_value(
        master_value(master_pi, "nomor_kontrak", "Nomor Kontrak")
        or context.get("Nomor Kontrak")
        or tx_field(rows[0], "nomor_kontrak", "-"),
        "-",
    )
    pi_no_str = text_value(
        master_value(master_pi, "pi_no", "PI No.", "Proforma Invoice No.")
        or context.get("Nomor PI") or tx_field(rows[0], "pi_no", pi_no or "-"),
        pi_no or "-",
    )
    tgl_pi = date_short(
        master_value(master_pi, "tanggal_performa_invoice_new", "Tanggal Performa Invoice New", "tanggal_pi", "Tanggal PI", "Tanggal Performa Invoice")
        or context.get("Tanggal PI")
        or tx_field(rows[0], "tanggal_pi", "")
    )
    tgl_kontrak = date_short(
        master_value(master_pi, "tanggal_kontrak_new", "Tanggal Kontrak New", "tanggal_kontrak", "Tanggal Kontrak")
        or context.get("Tanggal Kontrak")
        or tx_field(rows[0], "tanggal_kontrak", "")
    )
    lingkup_pekerjaan = text_value(
        master_value(master_pi, "lingkup_pekerjaan", "Lingkup Pekerjaan", "deskripsi_po", "Deskripsi PO")
        or context.get("Lingkup Pekerjaan")
        or tx_field(rows[0], "lingkup", "-"),
        "-",
    )
    no_po_auto = text_value(
        master_value(master_pi, "nomor_po", "Nomor PO", "Nomor Purchase Order")
        or context.get("Nomor Purchase Order")
        or tx_field(rows[0], "nomor_po", "-"),
        "-",
    )
    tgl_po_auto = date_short(
        master_value(master_pi, "tanggal_purchase_order_new", "Tanggal Purchase Order New", "tanggal_po", "Tanggal PO", "Tanggal Purchase Order")
        or context.get("Tanggal Purchase Order")
        or tx_field(rows[0], "tanggal_po", "")
    )

    p1_nama = text_value(
        master_value(master_pi, "pihak_pertama", "Pihak Pertama")
        or context.get("Pihak Pertama"),
        "JOB Pertamina - Medco E&P Tomori Sulawesi",
    )
    p1_alamat = text_value(
        master_value(master_pi, "alamat_pihak_pertama", "Alamat Pihak Pertama")
        or context.get("Alamat Pihak Pertama"),
        "Bidakara Office Tower I 4Th Floor, Jl. Gatot Subroto Kav. 71 - 73, Jakarta 12870, Indonesia",
    )
    p1_wakil_lengkap = signer_text(
        master_value(master_pi, "diwakili_oleh", "Diwakili Oleh")
        if master_pi else context.get("Diwakili Oleh")
    )
    p1_jabatan = signer_text(
        master_value(master_pi, "selaku", "Selaku")
        if master_pi else context.get("Selaku")
    )
    p1_wakil_sign = p1_wakil_lengkap

    p2_nama = text_value(
        master_value(master_pi, "pihak_kedua", "Pihak Kedua")
        or context.get("Pihak Kedua"),
        "PT Banggai Sentral Sulawesi",
    )
    p2_alamat = text_value(
        master_value(master_pi, "alamat_pihak_kedua", "Alamat Pihak Kedua")
        or context.get("Alamat Pihak Kedua"),
        "Jl. Urip Sumoharjo No. 53, Luwuk, Kabupaten Banggai, Provinsi Sulawesi Tengah (94715), Indonesia",
    )
    p2_wakil = signer_text(
        master_value(master_pi, "diwakili_oleh_p2", "Diwakili Oleh (P2)")
        if master_pi else context.get("Diwakili Oleh (P2)")
    )
    p2_jabatan = signer_text(
        master_value(master_pi, "selaku_p2", "Selaku (P2)")
        if master_pi else context.get("Selaku (P2)")
    )

    # Template lama menggunakan tanggal minimum dari seluruh item sebagai default.
    min_date_default = _minimum_start_date(rows)
    if not any(text_value(tx_field(row, "tanggal_mulai", ""), "") for row in rows):
        min_date_default = _parse_date(
            master_value(master_pi, "tanggal_performa_invoice_new", "Tanggal Performa Invoice New", "tanggal_pi", "Tanggal PI", "Tanggal Performa Invoice")
            or context.get("Tanggal PI")
            or tx_field(rows[0], "tanggal_pi", ""),
            min_date_default,
        )
    selected_date = _parse_date(params.get("main_date"), min_date_default)
    bamp_date = date_id(selected_date)

    l1_bytes = params.get("logo_1")
    l2_bytes = params.get("logo_2")
    t1_bytes = params.get("ttd_1")
    t2_bytes = params.get("ttd_2")

    logo1_html = _image_html(l1_bytes, "52px", "180px", "0")
    logo2_html = _image_html(l2_bytes, "50px", "210px", "0 0 0 auto")
    ttd1_html = _image_html(t1_bytes, "60px", "160px", "0")
    if not ttd1_html:
        ttd1_html = '<div style="height: 50px;"></div>'

    ttd2_html = _image_html(t2_bytes, "60px", "160px", "2px 0 2px -15px")
    if not ttd2_html:
        ttd2_html = '<div style="height: 50px;"></div>'

    rows_html = _build_rows(rows, params)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Berita Acara Mulai Pekerjaan</title>
        <style>
            @page {{
                size: A4;
                margin: 8mm;
            }}
            @media print {{
                body {{ -webkit-print-color-adjust: exact; margin: 0; }}
                @page {{ size: A4; margin: 8mm; }}
                .bamp-document-code {{
                    position: static;
                    display: block;
                    clear: both;
                    margin-top: 8mm;
                }}
            }}
            body {{ font-family: Arial, sans-serif; background-color: #ffffff; color: #000000; padding: 10px; margin: 0; font-size: 9.5px; line-height: 1.35; }}
            .header-table {{ width: 100%; border-collapse: collapse; border-bottom: 2px solid #000; padding-bottom: 6px; margin-bottom: 8mm; }}
            .header-table td {{ border: none; vertical-align: middle; padding: 0 4px; }}
            .main-doc-title {{ font-size: 13px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; margin: 0; text-align: center; }}
            .section-title {{ font-weight: bold; font-size: 9.5px; text-transform: uppercase; margin-top: 8px; margin-bottom: 3px; text-decoration: underline; }}
            .info-table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; font-size: 9.5px; }}
            .info-table td {{ padding: 1.5px 3px; border: none; vertical-align: top; }}
            .col-label {{ width: 22%; }}
            .col-colon {{ width: 2%; text-align: center; }}
            .col-value {{ width: 76%; }}
            table.item-grid {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
            table.item-grid th, table.item-grid td {{ border: 1px solid #000; padding: 5px 6px; font-size: 9px; vertical-align: middle; }}
            .th-header {{ background-color: #f1f5f9; font-weight: bold; text-transform: uppercase; text-align: center; }}
            .content-text {{ margin-bottom: 10px; font-size: 9.5px; text-align: justify; }}
            table.sig-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; margin-bottom: 15px; border: none; }}
            table.sig-table td {{ border: none; vertical-align: top; font-size: 9.5px; padding: 6px; }}
            .bamp-document-code {{
                font-size: 8px;
                font-weight: bold;
                text-align: left;
                position: static;
                display: block;
                clear: both;
                margin-top: 8mm;
                padding-top: 4px;
            }}
        </style>
    </head>
    <body>
        <table class="header-table">
            <tr>
                <td style="width: 25%; text-align: left; padding-left:25px;">{logo1_html}</td>
                <td style="width: 50%; text-align: center;">
                    <div class="main-doc-title">BERITA ACARA MULAI PEKERJAAN (BAMP)</div>
                </td>
                <td style="width: 25%; text-align: right; padding-right:25px;">{logo2_html}</td>
            </tr>
        </table>

        <div class="content-text">
            Pada hari ini, tanggal <b>{bamp_date}</b>, yang bertanda tangan di bawah ini:
        </div>

        <div class="section-title">01. PIHAK PERTAMA</div>
        <table class="info-table">
            <tr><td class="col-label">Nama Perusahaan</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p1_nama)}</b></td></tr>
            <tr><td class="col-label">Alamat</td><td class="col-colon">:</td><td class="col-value">{esc(p1_alamat)}</td></tr>
            <tr><td class="col-label">Diwakili oleh</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p1_wakil_lengkap)}</b></td></tr>
            <tr><td class="col-label">Jabatan</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p1_jabatan)}</b></td></tr>
        </table>

        <div class="section-title" style="margin-top: 8px;">02. PIHAK KEDUA</div>
        <table class="info-table">
            <tr><td class="col-label">Nama Perusahaan</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p2_nama)}</b></td></tr>
            <tr><td class="col-label">Alamat</td><td class="col-colon">:</td><td class="col-value">{esc(p2_alamat)}</td></tr>
            <tr><td class="col-label">Diwakili oleh</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p2_wakil)}</b></td></tr>
            <tr><td class="col-label">Jabatan</td><td class="col-colon">:</td><td class="col-value"><b>{esc(p2_jabatan)}</b></td></tr>
        </table>

        <div class="section-title" style="margin-top: 10px;">DASAR PELAKSANAAN PEKERJAAN</div>
        <table class="info-table">
            <tr><td class="col-label" style="font-weight: bold;">Nomor Kontrak</td><td class="col-colon">:</td><td class="col-value"><b>{esc(nomor_kontrak_str)}</b></td></tr>
            <tr><td class="col-label" style="font-weight: bold;">Nomor PI</td><td class="col-colon">:</td><td class="col-value"><b>{esc(pi_no_str)}</b></td></tr>
            <tr><td class="col-label" style="font-weight: bold;">Tanggal PI</td><td class="col-colon">:</td><td class="col-value">{esc(tgl_pi)}</td></tr>
            <tr><td class="col-label" style="font-weight: bold;">Tanggal Kontrak</td><td class="col-colon">:</td><td class="col-value">{esc(tgl_kontrak)}</td></tr>
            <tr><td class="col-label" style="font-weight: bold;">Nomor Purchase Order</td><td class="col-colon">:</td><td class="col-value"><b>{esc(no_po_auto)}</b></td></tr>
            <tr><td class="col-label" style="font-weight: bold;">Tanggal Purchase Order</td><td class="col-colon">:</td><td class="col-value">{esc(tgl_po_auto)}</td></tr>
            <tr><td class="col-label" style="font-weight: bold; vertical-align: top;">Lingkup Pekerjaan</td><td class="col-colon" style="vertical-align: top;">:</td><td class="col-value"><b>{esc(lingkup_pekerjaan)}</b></td></tr>
        </table>

        <div class="content-text" style="margin-top: 8px;">
            Dengan ini <b>PIHAK KEDUA</b> menyatakan mulai melaksanakan seluruh pekerjaan secara baik dan siap terhitung mulai tanggal <b>{bamp_date}</b> dengan rincian sebagai berikut:
        </div>

        <table class="item-grid">
            <tr>
                <th class="th-header" style="width: 8%;">NO</th>
                <th class="th-header" style="width: 42%;">KETERANGAN PEKERJAAN</th>
                <th class="th-header" style="width: 12%;">JUMLAH</th>
                <th class="th-header" style="width: 12%;">SATUAN</th>
                <th class="th-header" style="width: 26%;">CATATAN</th>
            </tr>
            {rows_html}
        </table>

        <div class="content-text">
            Demikian Berita Acara Mulai Pekerjaan ini dibuat dan ditandatangani oleh kedua belah pihak untuk dipergunakan sebagaimana mestinya.
        </div>

        <table class="sig-table">
            <tr>
                <td style="width: 50%; text-align: left; padding-left: 15px;">
                    <b>{esc(p1_nama)}</b><br><b>PIHAK PERTAMA</b>
                    {ttd1_html}
                    <u><b>{esc(p1_wakil_sign)}</b></u><br>{esc(p1_jabatan)}
                </td>
                <td style="width: 50%; text-align: left; padding-left: 15px;">
                    <b>{esc(p2_nama)}</b><br><b>PIHAK KEDUA</b>
                    {ttd2_html}
                    <u><b>{esc(p2_wakil)}</b></u><br>{esc(p2_jabatan)}
                </td>
            </tr>
        </table>

        <div class="bamp-document-code">Dokumen No: FM-GS-14 Rev:03</div>
    </body>
    </html>
    """
