import base64
import html
from datetime import date, datetime

from modul_dokumen.signature_roles import signer_text


def _text(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return default
    return text


def _norm(value):
    return " ".join(_text(value).split()).casefold()


def _field(row, *keys, default=""):
    if not isinstance(row, dict):
        return default
    for key in keys:
        if key in row and _text(row.get(key)) != "":
            return row.get(key)
    return default


def _date_value(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return default


def _date_input(value, default=None):
    parsed = _date_value(value, default)
    return parsed


def _date_indo(value):
    parsed = _date_value(value)
    if not parsed:
        return "-"
    bulan = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{parsed.day:02d} {bulan[parsed.month - 1]} {parsed.year}"


def _date_short(value):
    parsed = _date_value(value)
    if not parsed:
        return "-"
    return f"{parsed.day:02d}-{parsed.month:02d}-{parsed.year}"


def _number(value, default=0.0):
    try:
        if value is None or _text(value) == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _safe(value, default="-"):
    return html.escape(_text(value, default), quote=True)


def _decode_image(value):
    if not value:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            return base64.b64decode(raw, validate=False)
        except Exception:
            return None
    return None


def _image_html(value, fallback, style):
    data = _decode_image(value)
    if data:
        encoded = base64.b64encode(data).decode("ascii")
        return f'<img src="data:image/png;base64,{encoded}" style="{style}">'
    return fallback


def _transaction_filter(transaksi_list, nomor_kontrak, pi_no):
    target = []
    contract_norm = _norm(nomor_kontrak)
    pi_norm = _norm(pi_no)
    for row in transaksi_list or []:
        row_contract = _norm(_field(row, "nomor_kontrak", "Nomor Kontrak", "Nomor Kontrak 2"))
        row_pi = _norm(_field(row, "pi_no", "PI No.", "Proforma Invoice No.", "pi_no_2"))
        if contract_norm and row_contract and row_contract != contract_norm:
            continue
        if pi_norm and row_pi and row_pi != pi_norm:
            continue
        if pi_norm and not row_pi:
            continue
        target.append(row)
    return target


def _maximum_finish_date(rows):
    """Tanggal utama BASTB mengikuti tanggal penerimaan paling akhir."""
    dates = []
    for row in rows:
        parsed = _date_value(
            _field(
                row,
                "tanggal_selesai",
                "Tanggal Selesai",
                "tanggal_penerimaan",
                "Tanggal Penerimaan",
            )
        )
        if parsed:
            dates.append(parsed)
    return max(dates) if dates else date.today()


def _has_finish_date(rows):
    """True jika setidaknya satu rincian memiliki tanggal selesai valid."""
    for row in rows:
        value = _field(
            row,
            "tanggal_selesai",
            "Tanggal Selesai",
            "tanggal_penerimaan",
            "Tanggal Penerimaan",
        )
        if _date_value(value):
            return True
    return False


def _is_bastb_item(row):
    kategori = _norm(_field(row, "kategori", "Kategori"))
    jenis = _norm(_field(row, "jenis_bastp", "Jenis BASTP"))
    return bool(kategori) or any(token in kategori for token in ("material", "barang", "safety", "supply")) or "barang / material" in jenis or "gabungan" in jenis


def _saved_item(params, index):
    items = (params or {}).get("items", {})
    if isinstance(items, list):
        if 0 <= index - 1 < len(items):
            return items[index - 1] or {}
        return {}
    if isinstance(items, dict):
        return items.get(index, items.get(str(index), {})) or {}
    return {}


def _master_value(row, aliases, default="-"):
    value = _field(row, *aliases, default=default)
    return _text(value, default)


def _master_date(master, row, aliases):
    """Ambil tanggal dari master PI, lalu fallback ke rincian transaksi."""
    value = _field(master, *aliases, default="")
    if _text(value) == "":
        value = _field(row, *aliases, default="")
    return value


def generate_bastb_html(transaksi_list, nomor_kontrak="", pi_no="", params=None):
    params = params or {}
    master_pi = params.get("master_pi") or {}
    nomor_kontrak = _text(nomor_kontrak)
    pi_no = _text(pi_no)

    filtered = [row for row in (transaksi_list or []) if _is_bastb_item(row)]
    filtered = _transaction_filter(filtered, nomor_kontrak, pi_no)
    if not filtered:
        raise ValueError("Tidak ada item transaksi Barang / Material / Safety / Supply untuk BASTB pada PI yang dipilih.")

    # Model Streamlit menggunakan tanggal penerimaan paling akhir sebagai
    # tanggal utama dokumen BASTB.
    default_main_date = _maximum_finish_date(filtered)
    if not _has_finish_date(filtered):
        pi_date_aliases = ["tanggal_pi", "tanggal_performa_invoice", "Tanggal PI", "Tanggal Performa Invoice"]
        fallback_pi_date = _master_date(master_pi, filtered[0], pi_date_aliases)
        default_main_date = _date_value(fallback_pi_date, default_main_date)
    main_date = _date_value(params.get("main_date"), default_main_date)
    lokasi = _text(params.get("lokasi"), "Luwuk")

    rows_html = []
    rendered_items = []
    uom_options = ["Month", "Day", "AU", "Ls", "Unit", "Trip", "Jam", "Orang", "Set", "Pallet", "Pcs", "Ea", "m3"]
    for idx, row in enumerate(filtered, start=1):
        saved = _saved_item(params, idx)
        default_row_date = _date_value(
            _field(
                row,
                "tanggal_selesai",
                "Tanggal Selesai",
                "tanggal_penerimaan",
                "Tanggal Penerimaan",
            ),
            main_date,
        )
        item_date = _date_value(saved.get("date", saved.get("tanggal", "")), default_row_date)
        qty = _number(saved.get("qty", saved.get("jumlah", _field(row, "qty", "Qty", default=1))), 1.0)
        uom = _text(saved.get("uom", saved.get("satuan", _field(row, "unit", "Unit", default="Unit"))), "Unit")
        note_default = _text(_field(row, "keterangan", "Keterangan"), "Sesuai dan lengkap diterima.")
        note = _text(saved.get("catatan", saved.get("kondisi", "")), note_default) or "Sesuai dan lengkap diterima."
        kategori = _text(_field(row, "kategori", "Kategori"))
        deskripsi = _text(
            _field(
                row,
                "deskripsi_pekerjaan",
                "Deskripsi Pekerjaan",
                "deskripsi",
                "Deskripsi",
                "uraian_pekerjaan",
                "Uraian Pekerjaan",
            )
        )
        spec = f"<b>{_safe(kategori)}</b><br>{_safe(deskripsi)}" if kategori else _safe(deskripsi)
        rows_html.append(f"""
            <tr>
                <td style="text-align: center;">{idx}</td>
                <td style="text-align: left;">{spec}</td>
                <td style="text-align: center;">{qty:.2f}</td>
                <td style="text-align: center;">{_safe(uom)}</td>
                <td style="text-align: left;"><b>{_safe(note)}</b></td>
            </tr>
        """)
        rendered_items.append({"date": item_date, "qty": qty, "uom": uom, "catatan": note})

    first = filtered[0]
    # Field induk menggunakan nama field database terbaru, dengan fallback ke variasi lama.
    nomor_kontrak_str = _text(
        master_pi.get("nomor_kontrak")
        or master_pi.get("Nomor Kontrak")
        or _master_value(first, ["nomor_kontrak", "Nomor Kontrak"], nomor_kontrak or "-"),
        nomor_kontrak or "-",
    )
    tgl_kontrak = _date_short(_master_date(master_pi, first, ["tanggal_kontrak_new", "Tanggal Kontrak New", "tanggal_kontrak", "Tanggal Kontrak"]))
    pi_no_str = _text(
        master_pi.get("pi_no")
        or master_pi.get("PI No.")
        or master_pi.get("Proforma Invoice No.")
        or _master_value(first, ["pi_no", "PI No.", "Proforma Invoice No."], pi_no or "-"),
        pi_no or "-",
    )
    tgl_pi = _date_short(_master_date(master_pi, first, ["tanggal_performa_invoice_new", "Tanggal Performa Invoice New", "tanggal_pi", "tanggal_performa_invoice", "Tanggal PI", "Tanggal Performa Invoice"]))
    lingkup_aliases = ["lingkup_pekerjaan", "Lingkup Pekerjaan", "deskripsi_po", "Deskripsi PO"]
    lingkup = _text(
        _master_value(master_pi, lingkup_aliases, "")
        or _master_value(first, lingkup_aliases, "-"),
        "-",
    )
    no_po = _text(
        master_pi.get("nomor_po")
        or master_pi.get("Nomor PO")
        or master_pi.get("Nomor Purchase Order")
        or _master_value(first, ["nomor_po", "Nomor PO", "Nomor Purchase Order"], "-"),
        "-",
    )
    tgl_po = _date_short(_master_date(master_pi, first, ["tanggal_purchase_order_new", "Tanggal Purchase Order New", "tanggal_po", "tanggal_purchase_order", "Tanggal PO", "Tanggal Purchase Order"]))
    p1_nama = _text(master_pi.get("pihak_pertama") or master_pi.get("Pihak Pertama") or _master_value(first, ["pihak_pertama", "Pihak Pertama"], "JOB Pertamina - Medco E&P Tomori Sulawesi"), "JOB Pertamina - Medco E&P Tomori Sulawesi")
    p1_alamat = _text(master_pi.get("alamat_pihak_pertama") or master_pi.get("Alamat Pihak Pertama") or _master_value(first, ["alamat_pihak_pertama", "Alamat Pihak Pertama"], "Bidakara Office Tower I 4Th Floor, Jl. Gatot Subroto Kav. 71 - 73, Jakarta 12870, Indonesia"), "Bidakara Office Tower I 4Th Floor, Jl. Gatot Subroto Kav. 71 - 73, Jakarta 12870, Indonesia")
    representative_source = master_pi if master_pi else first
    p1_wakil = signer_text(_master_value(representative_source, ["diwakili_oleh", "Diwakili Oleh"], ""))
    p1_jabatan = signer_text(_master_value(representative_source, ["selaku", "Selaku"], ""))
    p2_nama = _text(master_pi.get("pihak_kedua") or master_pi.get("Pihak Kedua") or _master_value(first, ["pihak_kedua", "Pihak Kedua"], "PT Banggai Sentral Sulawesi"), "PT Banggai Sentral Sulawesi")
    p2_alamat = _text(master_pi.get("alamat_pihak_kedua") or master_pi.get("Alamat Pihak Kedua") or _master_value(first, ["alamat_pihak_kedua", "Alamat Pihak Kedua"], "Jl. Urip Sumoharjo No. 53, Luwuk, Kabupaten Banggai, Provinsi Sulawesi Tengah (94715), Indonesia"), "Jl. Urip Sumoharjo No. 53, Luwuk, Kabupaten Banggai, Provinsi Sulawesi Tengah (94715), Indonesia")
    p2_wakil = signer_text(_master_value(representative_source, ["diwakili_oleh_p2", "Diwakili Oleh (P2)"], ""))
    p2_jabatan = signer_text(_master_value(representative_source, ["selaku_p2", "Selaku (P2)"], ""))

    logo1_html = _image_html(params.get("logo_1"), "", "max-height:52px;max-width:180px;object-fit:contain;display:block;margin:0;")
    logo2_html = _image_html(params.get("logo_2"), "", "max-height:50px;max-width:210px;object-fit:contain;display:block;margin:0 0 0 auto;")
    ttd1_html = _image_html(params.get("ttd_1"), '<div style="height:50px;"></div>', "max-height:60px;max-width:160px;object-fit:contain;")
    ttd1_html = f'<div style="height:60px;display:flex;align-items:center;justify-content:flex-start;margin:2px 0;">{ttd1_html}</div>' if params.get("ttd_1") else ttd1_html
    ttd2_html = _image_html(params.get("ttd_2"), '<div style="height:50px;"></div>', "max-height:60px;max-width:160px;object-fit:contain;")
    ttd2_html = f'<div style="height:60px;display:flex;align-items:center;justify-content:flex-start;margin:2px 0 2px -15px;">{ttd2_html}</div>' if params.get("ttd_2") else ttd2_html

    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Berita Acara Serah Terima Barang</title>
<style>
@page {{size:A4;margin:8mm;}}
@media print {{body {{-webkit-print-color-adjust:exact;margin:0;}} @page {{size:A4;margin:8mm;}} .bastb-document-code {{position:static;display:block;clear:both;margin-top:8mm;}}}}
body {{font-family:Arial,sans-serif;background-color:#fff;color:#000;padding:10px;margin:0;font-size:9.5px;line-height:1.35;}}
.header-table {{width:100%;border-collapse:collapse;border-bottom:2px solid #000;padding-bottom:6px;margin-bottom:8mm;}}
.header-table td {{border:none;vertical-align:middle;padding:0 4px;}}
.main-doc-title {{font-size:13px;font-weight:bold;text-transform:uppercase;letter-spacing:.5px;margin:0;text-align:center;}}
.sub-doc-title {{font-size:9px;font-weight:bold;margin-top:3px;text-align:center;text-transform:uppercase;}}
.section-title {{font-weight:bold;font-size:9.5px;text-transform:uppercase;margin-top:8px;margin-bottom:3px;text-decoration:underline;}}
.info-table {{width:100%;border-collapse:collapse;margin-bottom:8px;font-size:9.5px;}}
.info-table td {{padding:1.5px 3px;border:none;vertical-align:top;}}
.col-label {{width:22%;}} .col-colon {{width:2%;text-align:center;}} .col-value {{width:76%;}}
table.item-grid {{width:100%;border-collapse:collapse;margin-bottom:12px;}}
table.item-grid th,table.item-grid td {{border:1px solid #000;padding:5px 6px;font-size:9px;vertical-align:middle;}}
.th-header {{background-color:#f1f5f9;font-weight:bold;text-transform:uppercase;text-align:center;}}
.content-text {{margin-bottom:10px;font-size:9.5px;text-align:justify;}}
table.sig-table {{width:100%;border-collapse:collapse;margin-top:15px;margin-bottom:15px;border:none;}}
table.sig-table td {{border:none;vertical-align:top;font-size:9.5px;padding:6px;}}
.bastb-document-code {{font-size:8px;font-weight:bold;text-align:left;position:static;display:block;clear:both;margin-top:8mm;padding-top:4px;}}
</style></head><body>
<table class="header-table"><tr>
<td style="width:25%;text-align:left;padding-left:25px;">{logo1_html}</td>
<td style="width:50%;text-align:center;"><div class="main-doc-title">BERITA ACARA SERAH TERIMA BARANG (BASTB)</div><div class="sub-doc-title">PENYERAHAN BARANG / MATERIAL</div></td>
<td style="width:25%;text-align:right;padding-right:25px;">{logo2_html}</td></tr></table>
<div class="content-text">Pada hari ini, tanggal <b>{_date_indo(main_date)}</b>, yang bertanda tangan di bawah ini:</div>
<div class="section-title">01. PIHAK PERTAMA</div>
<table class="info-table">
<tr><td class="col-label">Nama Perusahaan</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p1_nama)}</b></td></tr>
<tr><td class="col-label">Alamat</td><td class="col-colon">:</td><td class="col-value">{_safe(p1_alamat)}</td></tr>
<tr><td class="col-label">Diwakili oleh</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p1_wakil)}</b></td></tr>
<tr><td class="col-label">Jabatan</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p1_jabatan)}</b></td></tr></table>
<div class="section-title" style="margin-top:8px;">02. PIHAK KEDUA</div>
<table class="info-table">
<tr><td class="col-label">Nama Perusahaan</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p2_nama)}</b></td></tr>
<tr><td class="col-label">Alamat</td><td class="col-colon">:</td><td class="col-value">{_safe(p2_alamat)}</td></tr>
<tr><td class="col-label">Diwakili oleh</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p2_wakil)}</b></td></tr>
<tr><td class="col-label">Jabatan</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(p2_jabatan)}</b></td></tr></table>
<div class="section-title" style="margin-top:10px;">DASAR PENYERAHAN BARANG / MATERIAL</div>
<table class="info-table">
<tr><td class="col-label" style="font-weight:bold;">Nomor Kontrak</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(nomor_kontrak_str)}</b></td></tr>
<tr><td class="col-label" style="font-weight:bold;">Nomor PI</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(pi_no_str)}</b></td></tr>
<tr><td class="col-label" style="font-weight:bold;">Tanggal PI</td><td class="col-colon">:</td><td class="col-value">{_safe(tgl_pi)}</td></tr>
<tr><td class="col-label" style="font-weight:bold;">Tanggal Kontrak</td><td class="col-colon">:</td><td class="col-value">{_safe(tgl_kontrak)}</td></tr>
<tr><td class="col-label" style="font-weight:bold;">Nomor Purchase Order</td><td class="col-colon">:</td><td class="col-value"><b>{_safe(no_po)}</b></td></tr>
<tr><td class="col-label" style="font-weight:bold;">Tanggal Purchase Order</td><td class="col-colon">:</td><td class="col-value">{_safe(tgl_po)}</td></tr>
<tr><td class="col-label" style="font-weight:bold;vertical-align:top;">Lingkup Pengadaan</td><td class="col-colon" style="vertical-align:top;">:</td><td class="col-value"><b>{_safe(lingkup)}</b></td></tr></table>
<div class="content-text" style="margin-top:8px;">Dengan ini <b>PIHAK KEDUA</b> menyerahkan dan <b>PIHAK PERTAMA</b> menyatakan telah menerima pengadaan barang/material dengan baik, lengkap, dan sesuai spesifikasi terhitung sampai dengan tanggal <b>{_date_indo(main_date)}</b>:</div>
<table class="item-grid"><tr><th class="th-header" style="width:8%;">NO</th><th class="th-header" style="width:42%;">SPESIFIKASI BARANG / MATERIAL</th><th class="th-header" style="width:10%;">QTY</th><th class="th-header" style="width:10%;">SATUAN</th><th class="th-header" style="width:30%;">KONDISI / KETERANGAN</th></tr>{''.join(rows_html)}</table>
<div class="content-text">Demikian Berita Acara Serah Terima Barang (BASTB) ini dibuat dengan sebenarnya dan ditandatangani oleh kedua belah pihak untuk dipergunakan sebagaimana mestinya.</div>
<table class="sig-table"><tr>
<td style="width:50%;text-align:left;padding-left:15px;"><b>{_safe(p1_nama)}</b><br><b>PIHAK PERTAMA</b>{ttd1_html}<u><b>{_safe(p1_wakil)}</b></u><br>{_safe(p1_jabatan)}</td>
<td style="width:50%;text-align:left;padding-left:15px;"><b>{_safe(p2_nama)}</b><br><b>PIHAK KEDUA</b>{ttd2_html}<u><b>{_safe(p2_wakil)}</b></u><br>{_safe(p2_jabatan)}</td>
</tr></table>
<div class="bastb-document-code">Dokumen No: FM-GS-04 Rev:03</div>
</body></html>"""
    return html_content
