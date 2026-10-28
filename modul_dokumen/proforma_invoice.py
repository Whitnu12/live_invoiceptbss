"""
Proforma Invoice - Flask/Web

Migrasi langsung dari proforma_invoice.py legacy.
- Tidak menggunakan Streamlit.
- Template HTML Proforma Invoice dipertahankan.
- Perhitungan Grand Total dipertahankan.
- Data transaksi dibaca dari database_transaksi_rincian melalui routes.py.
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

    satuan = [
        "", "Satu", "Dua", "Tiga", "Empat", "Lima",
        "Enam", "Tujuh", "Delapan", "Sembilan",
        "Sepuluh", "Sebelas"
    ]

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


def format_tanggal_indo_konsisten(tanggal_val):
    if not tanggal_val or str(tanggal_val).strip() in ["-", "nan", "None", ""]:
        return "-"

    clean_str = str(tanggal_val).strip().split()[0]

    bulan_indo_map = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
        9: "September", 10: "Oktober", 11: "November", 12: "Desember"
    }

    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt_obj = datetime.strptime(clean_str, fmt)
            return f"{dt_obj.day:02d} {bulan_indo_map[dt_obj.month]} {dt_obj.year}"
        except Exception:
            continue

    return str(tanggal_val)


ALIASES = {
    "PI No.": ["PI No.", "pi_no", "Proforma Invoice No."],
    "Nomor Kontrak": ["Nomor Kontrak", "nomor_kontrak"],
    "Tanggal PI": ["Tanggal Performa Invoice New", "tanggal_performa_invoice_new", "Tanggal PI", "Tanggal Performa Invoice", "tanggal_pi"],
    "Nomor PO": ["Nomor PO", "nomor_po", "Nomor Purchase Order"],
    "Ditujukan Kepada": ["Ditujukan Kepada", "ditujukan_kepada"],
    "Alamat Pihak Pertama": ["Alamat Pihak Pertama", "alamat_pihak_pertama"],
    "Attn": ["Attn", "attn"],
    "Jangka Waktu Kontrak": ["Jangka Waktu Kontrak", "jangka_waktu_kontrak"],
    "Kategori": ["Kategori", "kategori"],
    "Deskripsi Pekerjaan": ["Deskripsi Pekerjaan", "deskripsi_pekerjaan"],
    "Qty": ["Qty", "qty"],
    "Unit": ["Unit", "unit", "Satuan", "satuan"],
    "Harga Satuan": ["Harga Satuan", "harga_satuan", "Harga Satuan (IDR)"],
    "Percent": ["Percent", "percent", "Percent (%)"],
    "Keterangan": ["Keterangan", "keterangan"],
    "Bank Name": ["Bank Name", "bank_name"],
    "Bank Branch": ["Bank Branch", "bank_branch"],
    "Account No": ["Account No", "account_no"],
    "Account Name": ["Account Name", "account_name"],
    "Nama PT Sign": ["Nama PT Sign", "nama_pt_sign"],
    "Penandatangan Nama": ["Penandatangan Nama", "penandatangan_nama"],
    "Penandatangan Jabatan": ["Penandatangan Jabatan", "penandatangan_jabatan"],
    "Prepared by Name": ["Prepared by Name", "prepared_by_name"],
    "Prepared by Title": ["Prepared by Title", "prepared_by_title"],
}


def get_value(row, key, default=""):
    if not isinstance(row, dict):
        return default

    for name in ALIASES.get(key, [key]):
        if name in row and row[name] not in (None, ""):
            return row[name]

    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in ALIASES.get(key, [key]):
        value = lowered.get(str(name).strip().lower())
        if value not in (None, ""):
            return value

    return default


def number(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def make_legacy_row(row):
    """Normalisasi record DB Flask menjadi nama field yang dipakai template legacy."""
    return {
        "PI No.": get_value(row, "PI No."),
        "Nomor Kontrak": get_value(row, "Nomor Kontrak"),
        "Tanggal PI": get_value(row, "Tanggal PI"),
        "Nomor PO": get_value(row, "Nomor PO"),
        "Ditujukan Kepada": get_value(row, "Ditujukan Kepada"),
        "Alamat Pihak Pertama": get_value(row, "Alamat Pihak Pertama"),
        "Attn": get_value(row, "Attn"),
        "Jangka Waktu Kontrak": get_value(row, "Jangka Waktu Kontrak"),
        "Kategori": get_value(row, "Kategori"),
        "Deskripsi Pekerjaan": get_value(row, "Deskripsi Pekerjaan"),
        "Qty": get_value(row, "Qty"),
        "Unit": get_value(row, "Unit"),
        "Harga Satuan": get_value(row, "Harga Satuan"),
        "Percent": get_value(row, "Percent"),
        "Keterangan": get_value(row, "Keterangan"),
        "Bank Name": get_value(row, "Bank Name"),
        "Bank Branch": get_value(row, "Bank Branch"),
        "Account No": get_value(row, "Account No"),
        "Account Name": get_value(row, "Account Name"),
        "Nama PT Sign": get_value(row, "Nama PT Sign"),
        "Penandatangan Nama": get_value(row, "Penandatangan Nama"),
        "Penandatangan Jabatan": get_value(row, "Penandatangan Jabatan"),
    }


def get_signature_html(params):
    params = params or {}

    ttd = (
        params.get("ttd_bytes")
        or params.get("ttd_1")
        or params.get("signature")
        or params.get("signature_bytes")
    )

    if not ttd:
        return "<br><br><br><br>"

    if isinstance(ttd, str):
        if ttd.startswith("data:image/"):
            img_src = ttd
        else:
            img_src = ttd
    else:
        img_b64 = base64.b64encode(ttd).decode("utf-8")
        img_src = f"data:image/png;base64,{img_b64}"

    return f"""
        <div style="height: 70px; display: flex; align-items: center; justify-content: center; margin: 4px 0;">
            <img src="{img_src}" style="max-height: 70px; max-width: 180px; object-fit: contain;">
        </div>
    """


def generate_proforma_invoice_html(
    transaksi_list,
    nomor_kontrak="",
    pi_no="",
    params=None,
):
    """
    Generator Flask pengganti tampilkan_proforma_invoice().

    Selection PI dilakukan oleh Modul 2/routes.py.
    Generator ini hanya menghasilkan dokumen dari transaksi yang sudah
    dipilih, sehingga tidak ada perubahan pada database transaksi.
    """

    params = params or {}

    if not transaksi_list:
        return """
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Proforma Invoice</title>
        </head>
        <body>
            <p>Belum ada data transaksi rincian pekerjaan yang diproses.</p>
        </body>
        </html>
        """

    # Filter kembali berdasarkan kontrak + PI sebagai pengaman.
    target_kontrak = str(nomor_kontrak or "").strip()
    target_pi = str(pi_no or "").strip()

    mutasi_terpilih = []

    for row in transaksi_list:
        row_kontrak = str(get_value(row, "Nomor Kontrak", "")).strip()
        row_pi = str(get_value(row, "PI No.", "")).strip()

        if target_kontrak and row_kontrak != target_kontrak:
            continue

        if target_pi and row_pi != target_pi:
            continue

        mutasi_terpilih.append(make_legacy_row(row))

    if not mutasi_terpilih:
        return """
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Proforma Invoice</title>
        </head>
        <body>
            <p>Tidak ada item mutasi ditemukan untuk PI ini.</p>
        </body>
        </html>
        """

    t_data_utama = mutasi_terpilih[0]
    master_pi = params.get("master_pi") or {}
    master_header = make_legacy_row(master_pi)
    for key, value in master_header.items():
        if value not in (None, ""):
            t_data_utama[key] = value

    # --------------------------------------------------------
    # SIGNATURE
    # --------------------------------------------------------

    ttd_html_element = get_signature_html(params)

    # --------------------------------------------------------
    # FORMAT TANGGAL PI
    # --------------------------------------------------------

    raw_tgl_pi = str(
        get_value(master_pi, "Tanggal PI", "Tanggal Performa Invoice")
        or t_data_utama.get("Tanggal PI", "")
    )
    tanggal_pi_bersih = format_tanggal_indo_konsisten(raw_tgl_pi)

    # --------------------------------------------------------
    # GRAND TOTAL
    # LOGIKA PERSIS DARI LEGACY
    # --------------------------------------------------------

    grand_total_pi = 0.0

    for m in mutasi_terpilih:
        kategori_str = str(m.get("Kategori", "")).lower()
        qty_val = number(m.get("Qty", 0.0))
        unit_price = number(m.get("Harga Satuan", 0.0))
        percent_val = number(m.get("Percent", 100.0), 100.0)

        if "provisional" in kategori_str or "professional" in kategori_str:
            tot_item = (
                qty_val
                * unit_price
                * 1.15
                * (percent_val / 100.0)
            )
        elif "estimated" in kategori_str or "estimasi" in kategori_str:
            tot_item = (
                qty_val
                * unit_price
                * 0.9
                * (percent_val / 100.0)
            )
        else:
            tot_item = (
                qty_val
                * unit_price
                * (percent_val / 100.0)
            )

        grand_total_pi += tot_item

    terbilang_str = terbilang(grand_total_pi).strip() + " Rupiah"

    nama_pt_sign = get_value(
        t_data_utama,
        "Nama PT Sign",
        "PT. BANGGAI SENTRAL SULAWESI",
    )
    
    # Penandatangan khusus PI tetap diutamakan bila ada. Tanpa kolom khusus,
    # nama/jabatan mengikuti Prepared by pada master PI yang dipilih.
    explicit_signer = signer_text(get_value(t_data_utama, "Penandatangan Nama"))
    if explicit_signer:
        nama_pejabat = explicit_signer
        jabatan_pejabat = signer_text(get_value(t_data_utama, "Penandatangan Jabatan"))
    else:
        nama_pejabat = signer_text(get_value(master_pi, "Prepared by Name"))
        jabatan_pejabat = signer_text(get_value(master_pi, "Prepared by Title"))

    # --------------------------------------------------------
    # TABLE ROWS
    # LOGIKA PERSIS DARI LEGACY
    # --------------------------------------------------------

    rows_html = ""

    for idx, m in enumerate(mutasi_terpilih, start=1):
        kategori_str = str(m.get("Kategori", "")).lower()
        qty_val = number(m.get("Qty", 0.0))
        unit_price = number(m.get("Harga Satuan", 0.0))
        percent_val = number(m.get("Percent", 100.0), 100.0)
        kategori_awal = str(
            m.get("Kategori", "MONTHLY BASIS")
        )

        if "provisional" in kategori_str or "professional" in kategori_str:
            base_at_cost = (
                qty_val
                * unit_price
                * (percent_val / 100.0)
            )
            fee_15 = base_at_cost * 0.15
            total_item = base_at_cost * 1.15

            unit_price_display = (
                f"Rp {base_at_cost:,.2f}"
                f"<br><span style='font-size: 8px; "
                f"font-weight: normal; color: #334155;'>"
                f"+ 15% Fee (Rp {fee_15:,.2f})"
                f"</span>"
            )
            kategori_display = kategori_awal

        elif "estimated" in kategori_str or "estimasi" in kategori_str:
            total_item = (
                qty_val
                * unit_price
                * 0.9
                * (percent_val / 100.0)
            )
            nominal_diskon = unit_price * 0.1
            kategori_display = kategori_awal
            unit_price_display = (
                f"{unit_price:,.2f}<br>"
                f"<span style='font-size: 8px; font-weight: normal; "
                f"color: #334155; line-height: 1.2; display: inline-block; "
                f"margin-top: 2px;'>"
                f"- 10% Diskon (Rp {nominal_diskon:,.2f})"
                f"</span>"
            )

        else:
            total_item = (
                qty_val
                * unit_price
                * (percent_val / 100.0)
            )
            unit_price_display = f"{unit_price:,.2f}"
            kategori_display = kategori_awal

        desc_text = (
            f"<b>{kategori_display}</b><br>"
            f"{m.get('Deskripsi Pekerjaan', '-')}"
        )

        if m.get("Keterangan"):
            desc_text += (
                f"<br><span style='font-size: 10px; "
                f"color: #334155;'>"
                f"{m.get('Keterangan')}"
                f"</span>"
            )

        unit_val = str(m.get("Unit", "Unit"))

        rows_html += f"""
            <tr>
                <td style="text-align: center;">{idx}</td>
                <td>{desc_text}</td>
                <td style="text-align: center;">{qty_val:,.2f}</td>
                <td style="text-align: center;">{unit_val}</td>
                <td style="text-align: right;">{unit_price_display}</td>
                <td style="text-align: right;">{total_item:,.2f}</td>
            </tr>
        """

    brand_header = standard_brand_header(params)
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Proforma Invoice - PT BSS</title>
        <style>
            @page {{
                size: A4;
                margin: 5mm 10mm 5mm 10mm;
            }}
            @media print {{
                body {{
                    -webkit-print-color-adjust: exact;
                }}
                @page {{
                    size: A4;
                    margin: 5mm 10mm 5mm 10mm;
                }}
            }}
            body {{ font-family: Arial, sans-serif; background-color: #ffffff; color: #000000; padding: 15px; margin: 0; font-size: 11px; }}
            .header {{ text-align: center; border-bottom: 2px solid #000; padding-bottom: 8px; margin-bottom: 15px; }}
            .header-title {{ font-size: 15px; font-weight: bold; margin-bottom: 15px; text-transform: uppercase; text-align: center; }}
            {BRANDING_CSS}
            table.two-col {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; border: none; }}
            table.two-col td {{ border: none; padding: 2px 0; vertical-align: top; font-size: 11px; }}
            table.data-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 0px; }}
            table.data-table th, table.data-table td {{ border: 1px solid #333; padding: 6px 8px; font-size: 11px; text-align: left; }}
            table.data-table th {{ background-color: #f1f5f9; text-align: center; vertical-align: middle; }}

            .total-row td {{
                font-weight: bold;
                background-color: #f8fafc;
                font-size: 11.5px;
            }}

            .terbilang-box {{
                margin-top: 8px;
                margin-bottom: 15px;
                font-size: 11px;
            }}

            .bank-section {{ font-size: 11px; line-height: 1.4; }}
        </style>
    </head>
    <body>
        {brand_header}

        <div class="header-title">PROFORMA INVOICE</div>

        <table class="two-col">
            <tr>
                <td style="width: 52%;">
                    <b>TO :</b><br>
                    <b>{t_data_utama.get('Ditujukan Kepada', 'JOB Pertamina - Medco E&P Tomori Sulawesi')}</b><br>
                    {t_data_utama.get('Alamat Pihak Pertama', 'Bidakara Office Tower I 4Th Floor, Jl. Gatot Subroto Kav. 71 - 73, Jakarta 12870, Indonesia')}<br><br>
                    <b>Attn. :</b> {t_data_utama.get('Attn', 'Accounts Payable - Finance Department')}
                </td>
                <td style="width: 48%;">
                    <table style="width: 100%; border-collapse: collapse; border: none;">
                        <tr>
                            <td style="border: none; width: 45%; font-weight: bold;">Proforma Invoice No.</td>
                            <td style="border: none; width: 5%; text-align: center;">:</td>
                            <td style="border: none; width: 50%;">{t_data_utama['PI No.']}</td>
                        </tr>
                        <tr>
                            <td style="border: none; font-weight: bold;">Tanggal Performa Invoice</td>
                            <td style="border: none; text-align: center;">:</td>
                            <td style="border: none;">{tanggal_pi_bersih}</td>
                        </tr>
                        <tr>
                            <td style="border: none; font-weight: bold;">Nomor Kontrak</td>
                            <td style="border: none; text-align: center;">:</td>
                            <td style="border: none;">{t_data_utama['Nomor Kontrak']}</td>
                        </tr>
                        <tr>
                            <td style="border: none; font-weight: bold;">Nomor PO</td>
                            <td style="border: none; text-align: center;">:</td>
                            <td style="border: none;">{escape(str(t_data_utama.get('Nomor PO') or '-'))}</td>
                        </tr>
                        <tr>
                            <td style="border: none; font-weight: bold;">Jangka Waktu Kontrak</td>
                            <td style="border: none; text-align: center;">:</td>
                            <td style="border: none;">{t_data_utama.get('Jangka Waktu Kontrak', '24 Month')}</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>

        <table class="data-table">
            <tr>
                <th style="width: 6%;">Item</th>
                <th style="width: 42%;">Description</th>
                <th style="width: 10%;">Qty</th>
                <th style="width: 10%;">Satuan</th>
                <th style="width: 16%;">Unit Price<br><span style="font-size: 8.5px; font-weight: normal;">(IDR)</span></th>
                <th style="width: 16%;">TOTAL<br><span style="font-size: 8.5px; font-weight: normal;">(IDR)</span></th>
            </tr>
            {rows_html}
            <tr class="total-row">
                <td colspan="5" style="text-align: right; text-transform: uppercase;">GRAND TOTAL :</td>
                <td style="text-align: right;">{grand_total_pi:,.2f}</td>
            </tr>
        </table>

        <div class="terbilang-box">
            <b>Terbilang :</b> <i>{terbilang_str}</i>
        </div>

        <table style="width: 100%; border: none; margin-top: 10px;">
            <tr>
                <td style="border: none; width: 55%; vertical-align: top;">
                    <div class="bank-section">
                        <b>PAYMENT INSTRUCTION</b><br>
                        Please remit to our bank:<br>
                        <b>Bank Name :</b> {t_data_utama.get('Bank Name', 'BANK RAKYAT INDONESIA (PERSERO) Tbk.')}<br>
                        <b>Branch :</b> {t_data_utama.get('Bank Branch', 'Cabang Luwuk')}<br>
                        <b>Account No :</b> {t_data_utama.get('Account No', '0167 0167 8888 303')}<br>
                        <b>Account Name :</b> {t_data_utama.get('Account Name', 'PT. BANGGAI SENTRAL SULAWESI')}
                    </div>
                </td>
                <td style="border: none; width: 45%; text-align: right; vertical-align: top;">
                    <div class="bank-section" style="margin-top: 20px; text-align: center; display: inline-block; min-width: 220px;">
                        <b>{nama_pt_sign}</b><br>
                        {ttd_html_element}
                        <u>{nama_pejabat}</u><br>
                        {jabatan_pejabat}
                    </div>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return html_content
