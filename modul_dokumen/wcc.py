"""
Work Completion Certificate (WCC) - Flask/Web

Migrasi dari implementasi Streamlit legacy.
- Tidak menggunakan Streamlit.
- Tidak membaca Excel.
- Tidak menyimpan session/rekap WCC.
- Sumber master: params["master_pi"] dari database_proforma_invoice.
- Sumber detail: transaksi_list dari database_transaksi_rincian.
- Dipanggil melalui document_engine / render_document().
"""

import base64
from datetime import datetime
from html import escape

from modul_dokumen.signature_roles import resolve_approval_roles, signer_text


ALIASES = {
    "PI No.": ["PI No.", "pi_no", "Proforma Invoice No."],
    "Nomor Kontrak": ["Nomor Kontrak", "nomor_kontrak"],
    "Tanggal PI": ["Tanggal Performa Invoice New", "tanggal_performa_invoice_new", "Tanggal PI", "tanggal_pi", "Tanggal Performa Invoice"],
    "Nomor Purchase Order": [
        "Nomor Purchase Order",
        "nomor_po",
        "Nomor PO",
    ],
    "Judul Kontrak": ["Judul Kontrak", "judul_kontrak"],
    "Pihak Pertama": ["Pihak Pertama", "pihak_pertama"],
    "Alamat Pihak Pertama": [
        "Alamat Pihak Pertama",
        "alamat_pihak_pertama",
    ],
    "Pihak Kedua": ["Pihak Kedua", "pihak_kedua"],
    "Alamat Pihak Kedua": [
        "Alamat Pihak Kedua",
        "alamat_pihak_kedua",
    ],
    "Diwakili Oleh": ["Diwakili Oleh", "diwakili_oleh"],
    "Selaku": ["Selaku", "selaku"],
    "Diwakili Oleh (P2)": ["Diwakili Oleh (P2)", "diwakili_oleh_p2"],
    "Selaku (P2)": ["Selaku (P2)", "selaku_p2"],
    "Nomor WCC": ["Nomor WCC", "nomor_wcc"],
    "Tanggal WCC": ["Tanggal WCC New", "tanggal_wcc_new", "Tanggal WCC", "tanggal_wcc"],
    "Nomor WO": ["Nomor WO", "nomor_wo"],
    "Keterangan WO": ["Keterangan WO", "keterangan_wo"],
    "Nomor CTR": ["Nomor CTR", "nomor_ctr"],
    "Progress Pekerjaan": [
        "Progress Pekerjaan",
        "progress_pekerjaan",
    ],
    "Prepared by Name": [
        "Prepared by Name",
        "prepared_by_name",
    ],
    "Prepared by Title": [
        "Prepared by Title",
        "prepared_by_title",
    ],
    "Approved by 1": ["Approved by 1", "approved_by_1"],
    "Approved by Title 1": [
        "Approved by Title 1",
        "approved_by_title_1",
    ],
    "Approved by 2": ["Approved by 2", "approved_by_2"],
    "Approved by Title 2": [
        "Approved by Title 2",
        "approved_by_title_2",
    ],
    "Total Harga": [
        "Total Harga",
        "total_harga",
        "Total",
        "total",
    ],
    "Deskripsi PO": [
        "Deskripsi PO",
        "deskripsi_po",
        "Deskripsi Pekerjaan",
        "deskripsi_pekerjaan",
    ],
}


def get_value(row, key, default=""):
    """Mengambil nilai dengan dukungan nama field legacy + alias Flask."""
    if not isinstance(row, dict):
        return default

    for name in ALIASES.get(key, [key]):
        if name in row and row[name] not in (None, ""):
            return row[name]

    lowered = {
        str(k).strip().lower(): v
        for k, v in row.items()
    }

    for name in ALIASES.get(key, [key]):
        value = lowered.get(str(name).strip().lower())
        if value not in (None, ""):
            return value

    return default


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def esc(value, default="-"):
    value = clean(value)
    if not value:
        value = default
    return escape(value, quote=True)


def number(value, default=0.0):
    if value in (None, ""):
        return default

    if isinstance(value, (int, float)):
        return float(value)

    try:
        raw = str(value).strip().replace(",", "")
        return float(raw)
    except (TypeError, ValueError):
        return default


def terbilang(n):
    n = int(n)

    if n < 0:
        return "minus " + terbilang(-n)

    satuan = [
        "",
        "Satu",
        "Dua",
        "Tiga",
        "Empat",
        "Lima",
        "Enam",
        "Tujuh",
        "Delapan",
        "Sembilan",
        "Sepuluh",
        "Sebelas",
    ]

    if n < 12:
        return " " + satuan[n]
    if n < 20:
        return terbilang(n - 10) + " Belas"
    if n < 100:
        return (
            terbilang(n // 10)
            + " Puluh"
            + terbilang(n % 10)
        )
    if n < 200:
        return " Seratus" + terbilang(n - 100)
    if n < 1000:
        return (
            terbilang(n // 100)
            + " Ratus"
            + terbilang(n % 100)
        )
    if n < 2000:
        return " Seribu" + terbilang(n - 1000)
    if n < 1000000:
        return (
            terbilang(n // 1000)
            + " Ribu"
            + terbilang(n % 1000)
        )
    if n < 1000000000:
        return (
            terbilang(n // 1000000)
            + " Juta"
            + terbilang(n % 1000000)
        )
    if n < 1000000000000:
        return (
            terbilang(n // 1000000000)
            + " Miliar"
            + terbilang(n % 1000000000)
        )

    return " Angka terlalu besar"


def format_tanggal_indo(value):
    raw = clean(value)

    if not raw or raw.lower() in {"nan", "none", "-"}:
        return "-"

    clean_raw = raw.split()[0]

    for fmt in (
        "%Y-%m-%d",
        "%d %b %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d %B %Y",
    ):
        try:
            dt = datetime.strptime(clean_raw, fmt)
            bulan = [
                "",
                "Januari",
                "Februari",
                "Maret",
                "April",
                "Mei",
                "Juni",
                "Juli",
                "Agustus",
                "September",
                "Oktober",
                "November",
                "Desember",
            ]
            return (
                f"{dt.day:02d} "
                f"{bulan[dt.month]} "
                f"{dt.year}"
            )
        except ValueError:
            continue

    return raw


def image_html(data, max_height=85, max_width=200, height=90):
    """Mengubah bytes/base64/data URI menjadi HTML image."""
    if not data:
        return f'<div style="height:{height}px;"></div>'

    if isinstance(data, bytes):
        src = (
            "data:image/png;base64,"
            + base64.b64encode(data).decode("utf-8")
        )
    else:
        src = str(data).strip()
        if not src:
            return f'<div style="height:{height}px;"></div>'

        if not src.startswith("data:image/"):
            src = "data:image/png;base64," + src

    return f"""
        <div style="
            margin: 6px auto;
            height: {height}px;
            display: flex;
            align-items: center;
            justify-content: center;
        ">
            <img
                src="{escape(src, quote=True)}"
                style="
                    max-height: {max_height}px;
                    max-width: {max_width}px;
                    object-fit: contain;
                "
            >
        </div>
    """


def _resolve_master(params):
    master = (params or {}).get("master_pi")
    return master if isinstance(master, dict) else {}


def _resolve_signature_values(master):
    """Follow the same two or three signer roles as the Opname document."""

    prepared_name = signer_text(get_value(
        master,
        "Prepared by Name",
        "",
    ))
    prepared_title = signer_text(get_value(
        master,
        "Prepared by Title",
        "",
    ))

    app1_name = get_value(
        master,
        "Approved by 1",
        "",
    )
    app1_title = get_value(
        master,
        "Approved by Title 1",
        "",
    )

    app2_name = get_value(master, "Approved by 2", "")
    app2_title = get_value(
        master,
        "Approved by Title 2",
        "",
    )

    roles = resolve_approval_roles(app1_name, app1_title, app2_name, app2_title)

    return {
        "prepared_name": prepared_name,
        "prepared_title": prepared_title,
        "reviewed_name": roles["reviewed_name"],
        "reviewed_title": roles["reviewed_title"],
        "final_app_name": roles["approved_name"],
        "final_app_title": roles["approved_title"],
        "reviewed_is_valid": bool(roles["reviewed_name"]),
    }


def generate_wcc_html(
    transaksi_list,
    nomor_kontrak="",
    pi_no="",
    params=None,
):
    """
    Generator Flask untuk Work Completion Certificate.

    Tidak melakukan:
    - query database
    - pembacaan Excel
    - session state
    - penyimpanan JSON/Excel

    Semua data master diberikan melalui params["master_pi"] oleh
    routes.py, sedangkan transaksi diberikan oleh document_engine.
    """

    params = params or {}
    rows = list(transaksi_list or [])
    master = _resolve_master(params)

    if not rows:
        return """
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>WCC</title>
        </head>
        <body>
            <p>Belum ada data transaksi yang diproses.</p>
        </body>
        </html>
        """

    target_kontrak = clean(nomor_kontrak)
    target_pi = clean(pi_no)

    filtered = []

    for row in rows:
        row_kontrak = clean(
            get_value(row, "Nomor Kontrak", "")
        )
        row_pi = clean(
            get_value(row, "PI No.", "")
        )

        if target_kontrak and row_kontrak != target_kontrak:
            continue

        if target_pi and row_pi != target_pi:
            continue

        filtered.append(row)

    if not filtered:
        return """
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>WCC</title>
        </head>
        <body>
            <p>Tidak ada item transaksi ditemukan untuk WCC ini.</p>
        </body>
        </html>
        """

    # --------------------------------------------------------
    # MASTER / HEADER
    # --------------------------------------------------------

    nomor_kontrak_str = (
        clean(get_value(master, "Nomor Kontrak"))
        or clean(nomor_kontrak)
        or clean(get_value(filtered[0], "Nomor Kontrak"))
        or "-"
    )

    pi_no_str = (
        clean(get_value(master, "PI No."))
        or clean(pi_no)
        or clean(get_value(filtered[0], "PI No."))
        or "-"
    )

    wcc_no = clean(
        params.get("wcc_no")
        or get_value(master, "Nomor WCC", "")
        or pi_no_str
    )

    raw_date = (
        params.get("wcc_date")
        or get_value(master, "Tanggal WCC", "")
        or get_value(master, "Tanggal PI", "")
        or get_value(filtered[0], "Tanggal PI", "")
    )
    wcc_date = format_tanggal_indo(raw_date)

    wo_no = clean(
        params.get("wo_no")
        or get_value(master, "Nomor WO", "")
        or "-"
    )

    ctr_no = clean(
        params.get("ctr_no")
        or get_value(master, "Nomor CTR", "")
        or "-"
    )

    wo_title = clean(
        params.get("wo_title")
        or get_value(master, "Keterangan WO", "")
        or get_value(master, "Deskripsi PO", "")
        or "General Services"
    )

    progress_val = clean(
        params.get("progress")
        or get_value(master, "Progress Pekerjaan", "")
        or "100%"
    )

    progress_desc = (
        f"[&#10003;] {esc(progress_val)} - "
        "Penyelesaian Pekerjaan"
    )

    signatures = _resolve_signature_values(master)

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    grand_total_wcc = sum(
        number(get_value(row, "Total Harga", 0))
        for row in filtered
    )

    terbilang_str = (
        terbilang(grand_total_wcc).strip()
        + " Rupiah"
    )

    # --------------------------------------------------------
    # LOGO / SIGNATURE
    # --------------------------------------------------------

    logo1 = (
        params.get("logo_1")
        or params.get("logo1")
    )
    logo2 = (
        params.get("logo_2")
        or params.get("logo2")
    )

    ttd1 = (
        params.get("ttd_1")
        or params.get("ttd1")
    )
    ttd2 = (
        params.get("ttd_2")
        or params.get("ttd2")
    )
    ttd3 = (
        params.get("ttd_3")
        or params.get("ttd3")
    )

    logo1_html = (
        image_html(
            logo1,
            max_height=50,
            max_width=180,
            height=55,
        )
        if logo1
        else ""
    )

    logo2_html = (
        image_html(
            logo2,
            max_height=50,
            max_width=210,
            height=55,
        )
        if logo2
        else ""
    )

    ttd1_html = image_html(ttd1)
    ttd2_html = image_html(ttd2)
    ttd3_html = image_html(ttd3)

    lokasi = clean(
        params.get("lokasi")
        or "Luwuk"
    )

    # --------------------------------------------------------
    # SIGNATURE TABLE
    # --------------------------------------------------------

    prepared_block = f"""
        {esc(lokasi)}, {esc(wcc_date)}<br>
        <b>PT Banggai Sentral Sulawesi</b><br>
        Prepared by,
        {ttd1_html}
        <u><b>{esc(signatures["prepared_name"])}</b></u><br>
        {esc(signatures["prepared_title"])}
    """

    approved_block = f"""
        <br>
        <b>JOB Pertamina - Medco E&amp;P Tomori Sulawesi</b><br>
        Approved by,
        {ttd3_html}
        <u><b>{esc(signatures["final_app_name"])}</b></u><br>
        {esc(signatures["final_app_title"])}
    """

    if not signatures["reviewed_is_valid"]:
        sig_table_html = f"""
        <table class="sig-table">
            <tr>
                <td style="
                    width:50%;
                    vertical-align:top;
                    text-align:center;
                    padding:0 20px;
                ">
                    {prepared_block}
                </td>

                <td style="
                    width:50%;
                    vertical-align:top;
                    text-align:center;
                    padding:0 20px;
                ">
                    {approved_block}
                </td>
            </tr>
        </table>
        """
    else:
        reviewed_block = f"""
            <br>
            <b>JOB Pertamina - Medco E&amp;P Tomori Sulawesi</b><br>
            Reviewed by,
            {ttd2_html}
            <u><b>{esc(signatures["reviewed_name"])}</b></u><br>
            {esc(signatures["reviewed_title"])}
        """

        sig_table_html = f"""
        <table class="sig-table">
            <tr>
                <td style="
                    width:33.3%;
                    vertical-align:top;
                    text-align:center;
                    padding:0 10px;
                ">
                    {prepared_block}
                </td>

                <td style="
                    width:33.3%;
                    vertical-align:top;
                    text-align:center;
                    padding:0 10px;
                ">
                    {reviewed_block}
                </td>

                <td style="
                    width:33.3%;
                    vertical-align:top;
                    text-align:center;
                    padding:0 10px;
                ">
                    {approved_block}
                </td>
            </tr>
        </table>
        """

    # --------------------------------------------------------
    # HTML DOCUMENT
    # --------------------------------------------------------

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Work Completion Certificate - PT BSS</title>

        <style>
            @page {{
                size: A4;
                margin: 10mm;
            }}

            @media print {{
                body {{
                    -webkit-print-color-adjust: exact;
                }}

                @page {{
                    margin: 0;
                }}

                body {{
                    margin: 10mm;
                }}

                header,
                footer,
                .no-print {{
                    display: none !important;
                }}
            }}

            body {{
                font-family: Arial, sans-serif;
                background-color: #ffffff;
                color: #000000;
                padding: 25px;
                margin: 0;
                font-size: 11px;
                line-height: 1.4;
            }}

            .header-table {{
                width: 100%;
                border-collapse: collapse;
                border-bottom: 2px solid #000;
                padding-bottom: 10mm;
                margin-bottom: 15px;
            }}

            .header-table td {{
                border: none;
                vertical-align: middle;
                padding: 0 10px;
            }}

            .title-box {{
                background-color: #dbeafe;
                border: 1px solid #000;
                text-align: center;
                font-weight: bold;
                font-size: 13px;
                padding: 6px;
                margin-bottom: 4px;
                text-transform: uppercase;
            }}

            .cert-box {{
                background-color: #f1f5f9;
                border: 1px solid #000;
                text-align: center;
                font-weight: bold;
                font-size: 12px;
                padding: 6px;
                margin-bottom: 20px;
            }}

            table.grid-table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 15px;
            }}

            table.grid-table th,
            table.grid-table td {{
                border: 1px solid #000;
                padding: 8px 10px;
                font-size: 11px;
                vertical-align: middle;
            }}

            .col-label {{
                width: 25%;
                font-weight: bold;
                background-color: #fafafa;
            }}

            .col-colon {{
                width: 3%;
                text-align: center;
                font-weight: bold;
            }}

            .col-val {{
                width: 72%;
            }}

            .content-text {{
                margin-bottom: 15px;
                font-size: 11px;
            }}

            table.sig-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 25px;
                border: none;
                table-layout: fixed;
            }}

            table.sig-table td {{
                border: none;
                font-size: 11px;
                word-wrap: break-word;
            }}
        </style>
    </head>

    <body>

        <table class="header-table">
            <tr>
                <td style="
                    width:25%;
                    text-align:left;
                    padding-left:25px;
                ">
                    {logo1_html}
                </td>

                <td style="
                    width:50%;
                    text-align:center;
                ">
                    <h3 style="
                        margin:0;
                        font-size:12px;
                        font-weight:bold;
                        text-transform:uppercase;
                        letter-spacing:0.5px;
                    ">
                        JASA SEWA ALAT BERAT PENDUKUNG
                        OPERASIONAL SENORO &amp; TIAKA
                    </h3>

                    <p style="
                        margin:4px 0 0 0;
                        font-size:11px;
                        font-weight:bold;
                    ">
                        Contract No. {esc(nomor_kontrak_str)}
                    </p>
                </td>

                <td style="
                    width:25%;
                    text-align:right;
                    padding-right:25px;
                ">
                    {logo2_html}
                </td>
            </tr>
        </table>

        <div class="title-box">
            WORK COMPLETION CERTIFICATE
        </div>

        <div class="cert-box">
            CERTIFICATE NO : {esc(wcc_no)}
        </div>

        <div class="content-text">
            On the date of <b>{esc(wcc_date)}</b> we on behalf of
            <b>PT Banggai Sentral Sulawesi</b> have completed
            the following job:
        </div>

        <table class="grid-table">

            <tr>
                <td class="col-label">
                    WORK ORDER NUMBER
                </td>
                <td class="col-colon">:</td>
                <td class="col-val">
                    <b>{esc(wo_no)}</b>
                </td>
            </tr>

            <tr>
                <td class="col-label">
                    WORK ORDER TITLE
                </td>
                <td class="col-colon">:</td>
                <td class="col-val">
                    {esc(wo_title)}
                </td>
            </tr>

            <tr>
                <td class="col-label">
                    CTR NUMBER
                </td>
                <td class="col-colon">:</td>
                <td class="col-val">
                    <b>{esc(ctr_no)}</b>
                </td>
            </tr>

            <tr>
                <td class="col-label">
                    DESCRIPTION
                </td>
                <td class="col-colon">:</td>
                <td class="col-val">
                    <table style="width:100%; border:none;">
                        <tr>
                            <td style="
                                border:none;
                                padding:0;
                                width:65%;
                            ">
                                {progress_desc}
                            </td>

                            <td style="
                                border:none;
                                padding:0;
                                width:35%;
                                text-align:right;
                                font-weight:bold;
                            ">
                                Rp {grand_total_wcc:,.2f}
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>

            <tr>
                <td class="col-label">
                    AMOUNT TOTAL
                </td>
                <td class="col-colon">:</td>
                <td class="col-val">
                    <b>{esc(terbilang_str)}</b>
                </td>
            </tr>

        </table>

        <div class="content-text">
            The work has been properly completed as per requirement,
            witnessed and accepted by
            <b>JOB Pertamina - Medco E&amp;P Tomori Sulawesi</b>.
        </div>

        {sig_table_html}

    </body>
    </html>
    """

    return html_content


# Alias opsional untuk kompatibilitas bila engine memakai nama generate_wcc.
generate_wcc = generate_wcc_html
