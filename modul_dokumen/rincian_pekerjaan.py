"""
Rincian Pekerjaan - Flask/Web generator.

Migrasi langsung dari rincian_pekerjaan.py lama:
- Tidak menggunakan Streamlit.
- Tidak menggunakan Excel sebagai database.
- Tidak mengubah layout/template dokumen lama.
- Detail transaksi tetap berasal dari database_transaksi_rincian.
- Dipanggil melalui document_engine.
"""

import base64
from datetime import datetime
from html import escape
from modul_dokumen.document_branding import BRANDING_CSS, standard_brand_header
from modul_dokumen.signature_roles import signer_text


def terbilang(n):
    n = int(n)
    if n < 0:
        return "minus " + terbilang(-n)
    satuan = ["", "Satu", "Dua", "Tiga", "Empat", "Lima", "Enam",
              "Tujuh", "Delapan", "Sembilan", "Sepuluh", "Sebelas"]
    if n < 12:
        return " " + satuan[n]
    elif n < 20:
        return terbilang(n - 10) + " Belas"
    elif n < 100:
        return terbilang(n // 10) + " Puluh" + terbilang(n % 10)
    elif n < 200:
        return " Seratus" + terbilang(n - 100)
    elif n < 1000:
        return terbilang(n // 100) + " Ratus" + terbilang(n % 100)
    elif n < 2000:
        return " Seribu" + terbilang(n - 1000)
    elif n < 1000000:
        return terbilang(n // 1000) + " Ribu" + terbilang(n % 1000)
    elif n < 1000000000:
        return terbilang(n // 1000000) + " Juta" + terbilang(n % 1000000)
    elif n < 1000000000000:
        return terbilang(n // 1000000000) + " Miliar" + terbilang(n % 1000000000)
    else:
        return " Angka terlalu besar"


def format_tanggal_indo(tanggal_str, fallback_context_str=""):
    if not tanggal_str or str(tanggal_str).strip() in ["-", "nan", "None", ""]:
        return "-"

    clean_str = str(tanggal_str).strip().split()[0]

    bulan_indo_map = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
        9: "September", 10: "Oktober", 11: "November", 12: "Desember"
    }

    romawi_bulan = {
        "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
        "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12
    }

    if clean_str.isdigit() and len(clean_str) <= 2:
        hari = int(clean_str)
        bulan_num = 8
        tahun_num = 2026

        combined_ctx = f"{fallback_context_str}".upper()

        for rom, b_val in romawi_bulan.items():
            if f"/{rom}/" in combined_ctx or f"-{rom}-" in combined_ctx:
                bulan_num = b_val
                break

        for t_val in ["2024", "2025", "2026", "2027"]:
            if t_val in combined_ctx:
                tahun_num = int(t_val)
                break

        return f"{hari:02d} {bulan_indo_map[bulan_num]} {tahun_num}"

    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt_obj = datetime.strptime(clean_str, fmt)
            return f"{dt_obj.day:02d} {bulan_indo_map[dt_obj.month]} {dt_obj.year}"
        except Exception:
            continue

    return str(tanggal_str)


ALIASES = {
    "PI No.": ["pi_no", "PI No.", "Proforma Invoice No."],
    "Nomor Kontrak": ["nomor_kontrak", "Nomor Kontrak"],
    "Tanggal PI": ["tanggal_performa_invoice_new", "Tanggal Performa Invoice New", "tanggal_pi", "Tanggal PI", "Tanggal Performa Invoice"],
    "Nomor PO": ["nomor_po", "Nomor PO", "nomor_purchase_order", "Nomor Purchase Order"],
    "Tanggal PO": ["tanggal_purchase_order_new", "Tanggal Purchase Order New", "tanggal_po", "Tanggal PO", "tanggal_purchase_order", "Tanggal Purchase Order"],
    "Kategori": ["kategori", "Kategori"],
    "Deskripsi Pekerjaan": ["deskripsi_pekerjaan", "Deskripsi Pekerjaan", "uraian_pekerjaan", "Uraian Pekerjaan"],
    "Qty": ["qty", "Qty"],
    "Unit": ["unit", "Unit", "satuan", "Satuan"],
    "Tanggal Mulai": ["tanggal_mulai", "Tanggal Mulai"],
    "Tanggal Selesai": ["tanggal_selesai", "Tanggal Selesai"],
    "Harga Satuan": ["harga_satuan", "Harga Satuan", "Harga Satuan (IDR)"],
    "Percent": ["percent", "Percent", "Percent (%)"],
    "Keterangan": ["keterangan", "Keterangan"],
    "Ditujukan Kepada": ["ditujukan_kepada", "Ditujukan Kepada"],
    "Nama Kontrak": ["nama_kontrak", "Nama Kontrak", "judul_kontrak", "Judul Kontrak"],
    "Deskripsi PO": ["deskripsi_po", "Deskripsi PO", "lingkup_pekerjaan", "Lingkup Pekerjaan"],
    "Nomor Tender": ["nomor_tender", "Nomor Tender"],
    "Mata Uang": ["mata_uang", "Mata Uang"],
}


def _value(row, legacy_key, default=""):
    if not isinstance(row, dict):
        return default

    for key in ALIASES.get(legacy_key, [legacy_key]):
        if key in row and row[key] not in (None, ""):
            return row[key]

    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in ALIASES.get(legacy_key, [legacy_key]):
        value = lowered.get(str(key).strip().lower())
        if value not in (None, ""):
            return value

    return default


def _legacy_row(row):
    """Normalisasi record DB Flask ke nama field legacy tanpa mengubah data."""
    return {
        key: _value(row, key, "")
        for key in ALIASES
    }


def _number(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _signature_html(value, empty_height="60px"):
    if not value:
        return f'<div style="height: {empty_height};"></div>'

    if isinstance(value, str):
        if value.startswith("data:image/"):
            src = value
        else:
            src = f"data:image/png;base64,{value}"
    else:
        b64 = base64.b64encode(value).decode("utf-8")
        src = f"data:image/png;base64,{b64}"

    img_style = "max-height: 75px; max-width: 200px; object-fit: contain;"
    return (
        '<div style="margin: 4px auto; height: 75px; display: flex; '
        'align-items: center; justify-content: center;">'
        f'<img src="{src}" style="{img_style}">'
        '</div>'
    )


def generate_rincian_pekerjaan_html(
    transaksi_list,
    nomor_kontrak="",
    pi_no="",
    params=None,
):
    """
    Generate dokumen Rincian Pekerjaan.

    Kontrak + PI sudah ditentukan oleh routes.py.
    Semua detail tetap dibaca dari database_transaksi_rincian.
    """

    params = params or {}

    if not transaksi_list:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Rincian Pekerjaan</title></head>
        <body><p>Belum ada data transaksi rincian pekerjaan yang diproses.</p></body>
        </html>
        """

    target_contract = str(nomor_kontrak or "").strip()
    target_pi = str(pi_no or "").strip()

    matching_mutasi_list = []

    for item in transaksi_list:
        row_contract = str(_value(item, "Nomor Kontrak", "")).strip()
        row_pi = str(_value(item, "PI No.", "")).strip()

        if target_contract and row_contract != target_contract:
            continue
        if target_pi and row_pi != target_pi:
            continue

        matching_mutasi_list.append(_legacy_row(item))

    if not matching_mutasi_list:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Rincian Pekerjaan</title></head>
        <body><p>Tidak ada item transaksi ditemukan untuk PI ini.</p></body>
        </html>
        """

    t_data_ref = matching_mutasi_list[0]
    master_pi = params.get("master_pi") or {}
    master_header = _legacy_row(master_pi)
    for key, value in master_header.items():
        if value not in (None, ""):
            t_data_ref[key] = value
    current_pi_no = str(t_data_ref.get("PI No.", target_pi)).strip()

    # Penandatangan mengikuti baris master PI yang sedang dicetak.
    dibuat_nama = signer_text(master_pi.get("prepared_by_name") or master_pi.get("Prepared by Name"))
    dibuat_jabatan = signer_text(master_pi.get("prepared_by_title") or master_pi.get("Prepared by Title"))
    diperiksa_nama = signer_text(master_pi.get("approved_by_1") or master_pi.get("Approved by 1"))
    diperiksa_jabatan = signer_text(master_pi.get("approved_by_title_1") or master_pi.get("Approved by Title 1"))

    sig_dibuat_value = params.get("sig_dibuat") or params.get("ttd_1")
    sig_diperiksa_value = params.get("sig_diperiksa") or params.get("ttd_2")

    sig_dibuat_html = _signature_html(sig_dibuat_value)
    sig_diperiksa_html = _signature_html(sig_diperiksa_value)

    grand_total = 0.0

    for m in matching_mutasi_list:
        kategori_str = str(m.get("Kategori", "")).lower()
        qty_val = _number(m.get("Qty", 0))
        harga_satuan_val = _number(m.get("Harga Satuan", 0))
        percent_val = _number(m.get("Percent", 100.0), 100.0)

        if "provisional" in kategori_str or "professional" in kategori_str:
            tot_val = (qty_val * harga_satuan_val) * 1.15 * (percent_val / 100.0)
        elif "estimated" in kategori_str or "estimasi" in kategori_str:
            tot_val = (qty_val * harga_satuan_val * 0.9) * (percent_val / 100.0)
        else:
            tot_val = (qty_val * harga_satuan_val) * (percent_val / 100.0)

        grand_total += tot_val

    nomor_wcc_full = current_pi_no
    terbilang_str = terbilang(grand_total).strip() + " Rupiah"

    rows_html = ""

    for idx, m in enumerate(matching_mutasi_list, start=1):
        kategori_str = str(m.get("Kategori", "")).lower()
        qty_val = _number(m.get("Qty", 0))
        harga_satuan_val = _number(m.get("Harga Satuan", 0))
        percent_val = _number(m.get("Percent", 100.0), 100.0)
        kategori_awal = str(m.get("Kategori", "-"))
        keterangan_murni = str(m.get("Keterangan", "-"))

        tgl_mulai_formatted = format_tanggal_indo(
            m.get("Tanggal Mulai", "-"),
            current_pi_no,
        )
        tgl_selesai_formatted = format_tanggal_indo(
            m.get("Tanggal Selesai", "-"),
            current_pi_no,
        )

        if "estimated" in kategori_str or "estimasi" in kategori_str:
            total_harga_val = (
                qty_val * harga_satuan_val * 0.9
            ) * (percent_val / 100.0)

            nominal_diskon = harga_satuan_val * 0.1
            kategori_display = kategori_awal
            harga_satuan_display = (
                f"{harga_satuan_val:,.2f}<br>"
                f"<span style='font-size: 8px; font-weight: normal; "
                f"color: #334155; line-height: 1.2; display: inline-block; "
                f"margin-top: 2px;'>"
                f"- 10% Diskon (Rp {nominal_diskon:,.2f})"
                f"</span>"
            )

        elif "provisional" in kategori_str or "professional" in kategori_str:
            base_at_cost = (
                qty_val * harga_satuan_val
                * (percent_val / 100.0)
            )

            fee_15 = base_at_cost * 0.15
            total_harga_val = base_at_cost * 1.15

            harga_satuan_display = (
                f"Rp {base_at_cost:,.2f}<br>"
                f"<span style='font-size: 8px; font-weight: normal; "
                f"color: #334155;'>"
                f"+ 15% Fee (Rp {fee_15:,.2f})"
                f"</span>"
            )

            kategori_display = kategori_awal

        else:
            total_harga_val = (
                qty_val * harga_satuan_val
            ) * (percent_val / 100.0)

            harga_satuan_display = f"{harga_satuan_val:,.2f}"
            kategori_display = kategori_awal

        rows_html += f"""
            <tr>
                <td style="text-align: center;">{idx}</td>
                <td>{kategori_display}</td>
                <td>{m.get('Deskripsi Pekerjaan', '-')}</td>
                <td style="text-align: center;">{qty_val:,.2f}</td>
                <td style="text-align: center;">{m.get('Unit', '-')}</td>
                <td style="text-align: center;">{tgl_mulai_formatted}</td>
                <td style="text-align: center;">{tgl_selesai_formatted}</td>
                <td style="text-align: right;">{harga_satuan_display}</td>
                <td style="text-align: right;">{total_harga_val:,.2f}</td>
                <td>{keterangan_murni}</td>
            </tr>
        """

    tgl_po_bersih_tampil = format_tanggal_indo(
        t_data_ref.get("Tanggal PO", ""),
        current_pi_no,
    )
    tgl_pi_bersih_tampil = format_tanggal_indo(
        _value(master_pi, "Tanggal PI", "")
        or t_data_ref.get("Tanggal PI", ""),
        current_pi_no,
    )

    brand_header = standard_brand_header(params)
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{
                size: A4 landscape;
                margin: 6mm;
            }}
            @media print {{
                html, body {{
                    width: 297mm;
                    height: 210mm;
                    margin: 0 !important;
                    padding: 6mm !important;
                    background: #fff !important;
                    -webkit-print-color-adjust: exact;
                }}
                @page {{
                    margin: 0;
                }}
            }}
            body {{
                font-family: Arial, sans-serif;
                background-color: #ffffff;
                color: #000000;
                padding: 6mm;
                margin: 0;
                font-size: 10px;
                line-height: 1.3;
            }}
            {BRANDING_CSS}
            .header {{ text-align: center; border-bottom: 2px solid #000; padding-bottom: 6mm; margin-bottom: 10px; }}
            .title {{ text-align: center; font-weight: bold; font-size: 13px; margin-bottom: 12px; text-transform: uppercase; text-decoration: underline; }}
            table.info-table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 10px; border: none; }}
            table.info-table td {{ border: none; padding: 3px 5px; font-size: 10px; vertical-align: top; overflow-wrap: anywhere; word-break: normal; }}
            .label-col {{ font-weight: bold; }}
            .colon-col {{ font-weight: bold; text-align: center; }}
            table.data-table {{ width: 100%; max-width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 6mm; margin-bottom: 0px; }}
            table.data-table th, table.data-table td {{ border: 1px solid #333; padding: 5px 4px; font-size: 9px; line-height: 1.25; text-align: left; overflow-wrap: anywhere; word-break: normal; white-space: normal; }}
            table.data-table th {{ background-color: #f1f5f9; text-align: center; vertical-align: middle; }}
            table.data-table td:nth-child(1),
            table.data-table td:nth-child(4),
            table.data-table td:nth-child(5) {{ text-align: center; }}
            table.data-table td:nth-child(6),
            table.data-table td:nth-child(7) {{ text-align: center; overflow-wrap: normal; }}
            table.data-table td:nth-child(8),
            table.data-table td:nth-child(9) {{ text-align: right; font-variant-numeric: tabular-nums; }}
            
            .total-row td {{
                font-weight: bold;
                background-color: #f8fafc;
                font-size: 10.5px;
            }}
            
            .terbilang-box {{
                margin-top: 8px;
                margin-bottom: 15px;
                font-size: 10px;
            }}

            .sign-table {{ border: none; width: 100%; margin-top: 15px; page-break-inside: avoid; }}
            .sign-table td {{ border: none; text-align: center; width: 50%; font-size: 9.5px; vertical-align: top; }}
            .sign-title {{ font-weight: bold; font-size: 9.5px; text-transform: uppercase; margin-bottom: 8px; }}
            .sign-name {{ font-weight: bold; font-size: 9.5px; text-decoration: underline; margin-top: 4px; }}
            .sign-pos {{ font-size: 8.5px; margin-top: 2px; }}
        </style>
    </head>
    <body>
        {brand_header}
        <div class="title">Rincian Pekerjaan</div>
        
        <table class="info-table">
            <colgroup>
                <col style="width:16%"><col style="width:2%"><col style="width:32%">
                <col style="width:16%"><col style="width:2%"><col style="width:32%">
            </colgroup>
            <tr>
                <td class="label-col">Rincian Pekerjaan</td>
                <td class="colon-col">:</td>
                <td><b>{nomor_wcc_full}</b></td>
                <td class="label-col">Ditujukan Kepada</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Ditujukan Kepada', '')}</td>
            </tr>
            <tr>
                <td class="label-col">Nomor Kontrak</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Nomor Kontrak', '')}</td>
                <td class="label-col">Nomor Purchase Order</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Nomor PO', '')}</td>
            </tr>
            <tr>
                <td class="label-col">Nama Kontrak</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Nama Kontrak', '')}</td>
                <td class="label-col">Lingkup Pekerjaan</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Deskripsi PO', '')}</td>
            </tr>
            <tr>
                <td class="label-col">Nomor Tender</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Nomor Tender', '')}</td>
                <td class="label-col">Tanggal Purchase Order</td>
                <td class="colon-col">:</td>
                <td>{tgl_po_bersih_tampil}</td>
            </tr>
            <tr>
                <td class="label-col">Tanggal Proforma</td>
                <td class="colon-col">:</td>
                <td>{tgl_pi_bersih_tampil}</td>
                <td class="label-col">Mata Uang</td>
                <td class="colon-col">:</td>
                <td>{t_data_ref.get('Mata Uang', 'IDR')}</td>
            </tr>
        </table>
        
        <table class="data-table">
            <colgroup>
                <col style="width:3%"><col style="width:9%"><col style="width:22%">
                <col style="width:5%"><col style="width:6%"><col style="width:9%">
                <col style="width:9%"><col style="width:12%"><col style="width:12%">
                <col style="width:13%">
            </colgroup>
            <tr>
                <th>No.</th>
                <th>Kategori</th>
                <th>Uraian Pekerjaan</th>
                <th>Qty</th>
                <th>Satuan</th>
                <th>Tanggal Mulai</th>
                <th>Tanggal Selesai</th>
                <th>Harga Satuan<br><span style="font-size: 8px; font-weight: normal;">(IDR)</span></th>
                <th>Total Harga<br><span style="font-size: 8px; font-weight: normal;">(IDR)</span></th>
                <th>Keterangan</th>
            </tr>
            {rows_html}
            <tr class="total-row">
                <td colspan="8" style="text-align: right; text-transform: uppercase;">TOTAL TAGIHAN :</td>
                <td style="text-align: right;">{grand_total:,.2f}</td>
                <td></td>
            </tr>
        </table>
        
        <div class="terbilang-box">
            <b>Terbilang :</b> <i>{terbilang_str}</i>
        </div>
        
        <table class="sign-table">
            <tr>
                <td>
                    <div class="sign-title">DIBUAT OLEH</div>
                    {sig_dibuat_html}
                    <div class="sign-name">{escape(dibuat_nama)}</div>
                    <div class="sign-pos">{escape(dibuat_jabatan)}</div>
                </td>
                <td>
                    <div class="sign-title">DIPERIKSA</div>
                    {sig_diperiksa_html}
                    <div class="sign-name">{escape(diperiksa_nama)}</div>
                    <div class="sign-pos">{escape(diperiksa_jabatan)}</div>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return html_content
