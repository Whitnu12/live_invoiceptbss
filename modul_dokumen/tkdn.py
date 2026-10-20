"""
TKDN - Flask version
====================

Migrasi dari modul TKDN Streamlit lama ke arsitektur Flask.

Prinsip:
- Tidak menggunakan Streamlit.
- Tidak menggunakan st.session_state / st.form / st.number_input / st.components.
- Perhitungan TKDN tetap mengikuti komposisi dan formula modul lama.
- Data edit dikirim melalui routes.py sebagai `params`.
- Fungsi utama `generate_tkdn_html()` kompatibel dengan document_engine.py.
- Modul ini tidak mengubah routes.py atau database secara otomatis.
"""

import base64
from datetime import date, datetime
from html import escape

from modul_dokumen.document_branding import BRANDING_CSS, standard_brand_header
from modul_dokumen.master_package_config import load_config


DEFAULTS = {
    "lokasi_office": "Luwuk",
    "nama_direktur": "Ir. Ferry Tatimu",
    "p_kdn_1": 15.09,
    "p_kln_1": 1.51,
    "p_kdn_2": 28.26,
    "p_kln_2": 0.0,
    "p_kdn_3": 47.18,
    "p_kln_3": 1.51,
    "p_kdn_4": 1.55,
    "p_kln_4": 0.0,
    "p_non_cost": 4.89,
}


PERCENT_FIELDS = (
    "p_kdn_1",
    "p_kln_1",
    "p_kdn_2",
    "p_kln_2",
    "p_kdn_3",
    "p_kln_3",
    "p_kdn_4",
    "p_kln_4",
    "p_non_cost",
)


def _clean(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat"}:
        return default
    return text


def _number(value, default=0.0):
    if value is None or value == "":
        return float(default)

    if isinstance(value, str):
        value = value.strip()
        # Accept both 100.50 and Indonesian-style 100,50.
        if "," in value and "." not in value:
            value = value.replace(",", ".")
        elif "," in value and "." in value:
            value = value.replace(".", "").replace(",", ".")

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _first(rows):
    return (rows or [{}])[0] or {}


def _field(row, *names, default=""):
    row = row or {}
    for name in names:
        if name in row:
            value = row.get(name)
            if value is not None and _clean(value) != "":
                return value
    return default


def _date_obj(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = _clean(value)
    if not text:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def _format_date_id(value):
    parsed = _date_obj(value)
    if not parsed:
        return _clean(value)

    bulan = {
        1: "Januari",
        2: "Februari",
        3: "Maret",
        4: "April",
        5: "Mei",
        6: "Juni",
        7: "Juli",
        8: "Agustus",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Desember",
    }

    return f"{parsed.day:02d} {bulan[parsed.month]} {parsed.year}"


def _to_data_image(value):
    if not value:
        return ""

    if isinstance(value, bytes):
        encoded = base64.b64encode(value).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    text = str(value).strip()

    if text.startswith("data:image/"):
        return text

    try:
        base64.b64decode(text, validate=True)
        return f"data:image/png;base64,{text}"
    except Exception:
        return ""


def _safe(value):
    return escape(_clean(value))


def get_tkdn_defaults():
    """
    Menghasilkan default editor TKDN tanpa state Streamlit.
    """
    return {
        "lokasi_office": DEFAULTS["lokasi_office"],
        "tanggal_dokumen": date.today().isoformat(),
        "nama_direktur": DEFAULTS["nama_direktur"],
        "total_tagihan": 0.0,
        **{
            field: DEFAULTS[field]
            for field in PERCENT_FIELDS
        },
        "ttd_direktur": None,
    }


def normalize_tkdn_params(params=None):
    """
    Normalisasi payload dari request.form atau request.json.

    Tidak menyimpan data. Fungsi ini hanya memastikan tipe data editor
    konsisten sebelum diberikan ke generator.
    """
    incoming = dict(params or {})
    normalized = get_tkdn_defaults()

    for key in ("lokasi_office", "nama_direktur", "tanggal_dokumen"):
        if key in incoming:
            normalized[key] = _clean(incoming.get(key), normalized[key])

    if "total_tagihan" in incoming:
        normalized["total_tagihan"] = max(
            0.0,
            _number(incoming.get("total_tagihan"), 0.0),
        )

    for field in PERCENT_FIELDS:
        if field in incoming:
            normalized[field] = min(
                100.0,
                max(0.0, _number(incoming.get(field), DEFAULTS[field])),
            )

    if "ttd_direktur" in incoming:
        normalized["ttd_direktur"] = incoming.get("ttd_direktur")

    return normalized


def validate_tkdn_params(params=None):
    """
    Validasi editor TKDN.

    Returns:
        (True, []) bila valid
        (False, [pesan...]) bila tidak valid

    Catatan:
    - Distribusi bobot tetap harus 100%, sesuai modul lama.
    - Tidak mengubah formula TKDN.
    """
    data = normalize_tkdn_params(params)

    errors = []

    if not _clean(data.get("lokasi_office")):
        errors.append("Lokasi Office wajib diisi.")

    if not _clean(data.get("nama_direktur")):
        errors.append("Nama Direktur wajib diisi.")

    total_tagihan = _number(data.get("total_tagihan"))
    if total_tagihan < 0:
        errors.append("Rujukan Total Tagihan tidak boleh negatif.")

    total_bobot = sum(
        _number(data.get(field))
        for field in PERCENT_FIELDS
    )

    if abs(total_bobot - 100.0) > 0.01:
        errors.append(
            f"Total distribusi bobot harus 100%. "
            f"Saat ini {total_bobot:.2f}%."
        )

    return len(errors) == 0, errors


def calculate_tkdn(transaksi_list, params=None):
    """
    Menghitung seluruh nilai TKDN.

    Formula dipertahankan dari modul TKDN sebelumnya.
    """
    params = normalize_tkdn_params(params)
    rows = list(transaksi_list or [])

    pi_no = _clean(params.get("pi_no"))

    if pi_no:
        selected_rows = [
            row for row in rows
            if _clean(_field(row, "pi_no", "PI No.", "Proforma Invoice No."))
            == pi_no
        ]
    else:
        selected_rows = rows

    if not selected_rows:
        selected_rows = rows

    aktual_total_tagihan = sum(
        _number(_field(row, "total_harga", "Total Harga"))
        for row in selected_rows
    )

    active_tagihan = _number(
        params.get("total_tagihan"),
        aktual_total_tagihan,
    )

    if active_tagihan <= 0:
        active_tagihan = aktual_total_tagihan

    def pct(field):
        return _number(params.get(field), DEFAULTS[field])

    p_kdn_1 = pct("p_kdn_1")
    p_kln_1 = pct("p_kln_1")
    p_kdn_2 = pct("p_kdn_2")
    p_kln_2 = pct("p_kln_2")
    p_kdn_3 = pct("p_kdn_3")
    p_kln_3 = pct("p_kln_3")
    p_kdn_4 = pct("p_kdn_4")
    p_kln_4 = pct("p_kln_4")
    p_non_cost = pct("p_non_cost")

    kdn_1 = (p_kdn_1 / 100.0) * active_tagihan
    kln_1 = (p_kln_1 / 100.0) * active_tagihan
    kdn_2 = (p_kdn_2 / 100.0) * active_tagihan
    kln_2 = (p_kln_2 / 100.0) * active_tagihan
    kdn_3 = (p_kdn_3 / 100.0) * active_tagihan
    kln_3 = (p_kln_3 / 100.0) * active_tagihan
    kdn_4 = (p_kdn_4 / 100.0) * active_tagihan
    kln_4 = (p_kln_4 / 100.0) * active_tagihan
    komponen_bukan_biaya = (p_non_cost / 100.0) * active_tagihan

    tot_kdn_biaya = kdn_1 + kdn_2 + kdn_3 + kdn_4
    tot_kln_biaya = kln_1 + kln_2 + kln_3 + kln_4

    tot_biaya_1 = kdn_1 + kln_1
    tot_biaya_2 = kdn_2 + kln_2
    tot_biaya_3 = kdn_3 + kln_3
    tot_biaya_4 = kdn_4 + kln_4

    jumlah_biaya_total = (
        tot_biaya_1
        + tot_biaya_2
        + tot_biaya_3
        + tot_biaya_4
    )

    jumlah_nilai_total = (
        jumlah_biaya_total
        + komponen_bukan_biaya
    )

    persen_tkdn_akhir = (
        (tot_kdn_biaya / jumlah_nilai_total) * 100
        if jumlah_nilai_total > 0
        else 0
    )

    total_persen_akumulasi = sum(
        [
            p_kdn_1, p_kln_1,
            p_kdn_2, p_kln_2,
            p_kdn_3, p_kln_3,
            p_kdn_4, p_kln_4,
            p_non_cost,
        ]
    )

    return {
        "total_tagihan": active_tagihan,
        "aktual_total_tagihan": aktual_total_tagihan,
        "p_kdn_1": p_kdn_1,
        "p_kln_1": p_kln_1,
        "p_kdn_2": p_kdn_2,
        "p_kln_2": p_kln_2,
        "p_kdn_3": p_kdn_3,
        "p_kln_3": p_kln_3,
        "p_kdn_4": p_kdn_4,
        "p_kln_4": p_kln_4,
        "p_non_cost": p_non_cost,
        "kdn_1": kdn_1,
        "kln_1": kln_1,
        "kdn_2": kdn_2,
        "kln_2": kln_2,
        "kdn_3": kdn_3,
        "kln_3": kln_3,
        "kdn_4": kdn_4,
        "kln_4": kln_4,
        "komponen_bukan_biaya": komponen_bukan_biaya,
        "tot_kdn_biaya": tot_kdn_biaya,
        "tot_kln_biaya": tot_kln_biaya,
        "tot_biaya_1": tot_biaya_1,
        "tot_biaya_2": tot_biaya_2,
        "tot_biaya_3": tot_biaya_3,
        "tot_biaya_4": tot_biaya_4,
        "jumlah_biaya_total": jumlah_biaya_total,
        "jumlah_nilai_total": jumlah_nilai_total,
        "persen_tkdn_akhir": persen_tkdn_akhir,
        "total_persen_akumulasi": total_persen_akumulasi,
        "selisih_bobot": 100.0 - total_persen_akumulasi,
    }


def generate_tkdn_html(
    transaksi_list,
    nomor_kontrak="",
    pi_no="",
    params=None,
):
    """
    Generator dokumen TKDN untuk Flask.

    Signature ini sengaja kompatibel dengan:
        document_engine.render_document()

    params dapat berisi:
        lokasi_office
        tanggal_dokumen
        nama_direktur
        total_tagihan
        p_kdn_1 / p_kln_1
        p_kdn_2 / p_kln_2
        p_kdn_3 / p_kln_3
        p_kdn_4 / p_kln_4
        p_non_cost
        ttd_direktur
        logo_1 (perusahaan) / logo_2 (ISO)
    """
    incoming_params = dict(params or {})
    master_pi = incoming_params.get("master_pi") or {}
    params = normalize_tkdn_params(incoming_params)
    params["pi_no"] = pi_no

    row = _first(transaksi_list)

    kontrak = _clean(
        _field(master_pi, "nomor_kontrak", "Nomor Kontrak")
    ) or _clean(
        nomor_kontrak
    ) or _clean(
        _field(row, "nomor_kontrak", "Nomor Kontrak")
    )

    pi = _clean(
        _field(master_pi, "pi_no", "PI No.", "Proforma Invoice No.")
    ) or _clean(
        pi_no
    ) or _clean(
        _field(row, "pi_no", "PI No.", "Proforma Invoice No.")
    )

    nomor_po = _clean(
        _field(master_pi, "nomor_po", "Nomor PO", "Nomor Purchase Order")
    ) or _clean(
        _field(
            row,
            "nomor_po",
            "Nomor PO",
            "Nomor Purchase Order",
        ),
        "-",
    )

    judul_kontrak = _clean(
        _field(master_pi, "judul_kontrak", "Judul Kontrak", "nama_kontrak", "Nama Kontrak")
    ) or _clean(
        _field(
            row,
            "nama_kontrak",
            "Nama Kontrak",
            "judul_kontrak",
            "Judul Kontrak",
        ),
        "Penyediaan General Services Untuk Mendukung Kegiatan Pengboran, "
        "Kerja Ulang Dan Perawatan Sumur di Blok Senoro - Toili",
    )

    mata_uang = _clean(
        _field(master_pi, "mata_uang", "Mata Uang")
    ) or _clean(
        _field(row, "mata_uang", "Mata Uang"),
        "IDR",
    )

    raw_po_date = _field(
        master_pi,
        "tanggal_purchase_order_new",
        "Tanggal Purchase Order New",
        "tanggal_po",
        "Tanggal PO",
        "tanggal_purchase_order",
        "Tanggal Purchase Order",
    ) or _field(
        row,
        "tanggal_po",
        "Tanggal PO",
        "tanggal_purchase_order",
        "Tanggal Purchase Order",
    )

    calc = calculate_tkdn(transaksi_list, params)

    lokasi = _clean(
        params.get("lokasi_office"),
        DEFAULTS["lokasi_office"],
    )

    nama_direktur = _clean(
        params.get("nama_direktur"),
        DEFAULTS["nama_direktur"],
    )

    tanggal_dokumen = (
        params.get("tanggal_dokumen")
        or _field(
            master_pi,
            "tanggal_performa_invoice_new",
            "Tanggal Performa Invoice New",
            "tanggal_pi",
            "Tanggal PI",
            "Tanggal Performa Invoice",
        )
        or date.today()
    )

    tgl_dokumen = _format_date_id(tanggal_dokumen)
    tgl_po_dokumen = _format_date_id(raw_po_date) or tgl_dokumen

    ttd_data = _to_data_image(params.get("ttd_direktur"))

    if ttd_data:
        ttd_html = (
            '<div style="margin:4px auto;height:90px;display:flex;'
            'align-items:center;justify-content:center;">'
            f'<img src="{ttd_data}" style="max-height:85px;'
            'max-width:220px;object-fit:contain;">'
            '</div>'
        )
    else:
        ttd_html = '<div style="height:80px;"></div>'

    kdn_1 = calc["kdn_1"]
    kln_1 = calc["kln_1"]
    kdn_2 = calc["kdn_2"]
    kln_2 = calc["kln_2"]
    kdn_3 = calc["kdn_3"]
    kln_3 = calc["kln_3"]
    kdn_4 = calc["kdn_4"]
    kln_4 = calc["kln_4"]

    package_config = load_config(kontrak, pi) if any(
        key not in incoming_params for key in ("logo_1", "logo_2")
    ) else {}
    branding_params = {
        "logo_1": incoming_params.get("logo_1") if "logo_1" in incoming_params
        else package_config.get("logo_2") or package_config.get("logo_1"),
        "logo_2": incoming_params.get("logo_2") if "logo_2" in incoming_params
        else package_config.get("logo_3") or package_config.get("logo_2"),
    }
    brand_header = standard_brand_header(branding_params)

    html_content = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Formulir TKDN - Permen ESDM No. 15 Tahun 2013</title>
<style>
@page {{ size:A4; margin:10mm; }}
@media print {{
    body {{ -webkit-print-color-adjust:exact; }}
    @page {{ margin:0; }}
    body {{ margin:10mm; }}
    header, footer, .no-print {{ display:none !important; }}
}}
body {{
    font-family:Arial,sans-serif;
    background:#fff;
    color:#000;
    padding:25px;
    margin:0;
    font-size:10px;
    line-height:1.3;
}}
{BRANDING_CSS}
.legacy-brand-header {{ break-inside:avoid; page-break-inside:avoid; }}
.title {{
    text-align:center;
    font-weight:bold;
    font-size:12px;
    margin-bottom:5px;
    text-transform:uppercase;
}}
.subtitle {{
    text-align:center;
    font-weight:bold;
    font-size:10px;
    margin-bottom:15px;
    text-transform:uppercase;
}}
table.info-table {{
    width:100%;
    border-collapse:collapse;
    margin-bottom:10px;
    font-size:10px;
}}
table.info-table td {{
    border:none;
    padding:2px 4px;
    vertical-align:top;
}}
table.tkdn-grid {{
    width:100%;
    border-collapse:collapse;
    margin-bottom:10px;
}}
table.tkdn-grid th,
table.tkdn-grid td {{
    border:1px solid #000;
    padding:5px 6px;
    font-size:9px;
    vertical-align:middle;
}}
.th-header {{
    background:#f1f5f9;
    font-weight:bold;
    text-align:center;
    text-transform:uppercase;
}}
.col-yellow {{
    background:#fef08a !important;
}}
.footer-notes {{
    font-size:9px;
    margin-top:15px;
    line-height:1.4;
}}
.sign-section {{
    width:100%;
    border-collapse:collapse;
    margin-top:20px;
    page-break-inside:avoid;
}}
.sign-section td {{
    border:none;
    font-size:10px;
    vertical-align:top;
}}
</style>
</head>
<body>

{brand_header}

<div class="title">
    TABEL PERHITUNGAN TINGKAT KOMPONEN DALAM NEGERI - JASA
</div>
<div class="subtitle">
    SELF - ASSESSMENT (PERMEN ESDM NO. 15 TAHUN 2013)
</div>

<table class="info-table">
<tr>
<td style="width:20%;font-weight:bold;">Nama Penyedia Jasa</td>
<td style="width:2%;">:</td>
<td style="width:48%;"><b>PT BANGGAI SENTRAL SULAWESI</b></td>
<td style="width:15%;font-weight:bold;">Nomor Kontrak</td>
<td style="width:2%;">:</td>
<td style="width:13%;"><b>{_safe(kontrak)}</b></td>
</tr>
<tr>
<td style="font-weight:bold;">Judul Kontrak</td>
<td>:</td>
<td>{_safe(judul_kontrak)}</td>
<td style="font-weight:bold;">Nomor PO</td>
<td>:</td>
<td><b>{_safe(nomor_po)}</b></td>
</tr>
<tr>
<td style="font-weight:bold;">Mata Uang</td>
<td>:</td>
<td>{_safe(mata_uang)}</td>
<td style="font-weight:bold;">Tanggal</td>
<td>:</td>
<td>{_safe(tgl_po_dokumen)}</td>
</tr>
</table>

<table class="tkdn-grid">
<tr>
<th class="th-header" style="width:30%;">A. Komponen Biaya<br>(Cost Component)</th>
<th class="th-header" style="width:8%;">Mata Uang</th>
<th class="th-header" style="width:14%;">KDN<br>(a)</th>
<th class="th-header" style="width:14%;">KLN<br>(b)</th>
<th class="th-header" style="width:14%;">TOTAL<br>(c = a+b)</th>
<th class="th-header" style="width:10%;">% Nilai TKDN<br>(d = a/c)</th>
<th class="th-header" style="width:10%;">Nilai TKDN<br>(e = c x d)</th>
</tr>

<tr>
<td rowspan="2"><b>I. Biaya Bahan (Material) Terpakai</b><br>
<span style="font-size:7px;color:#555;">(material used cost)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td class="col-yellow" style="text-align:right;">Rp {kdn_1:,.2f}</td>
<td class="col-yellow" style="text-align:right;">Rp {kln_1:,.2f}</td>
<td style="text-align:right;">Rp {calc["tot_biaya_1"]:,.2f}</td>
<td style="text-align:center;">
{(kdn_1/calc["tot_biaya_1"]*100) if calc["tot_biaya_1"] > 0 else 0:.2f}%
</td>
<td style="text-align:right;">Rp {kdn_1:,.2f}</td>
</tr>
<tr>
<td style="text-align:center;"><i>US$</i></td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:center;">0.00%</td>
<td style="text-align:right;">0.00</td>
</tr>

<tr>
<td rowspan="2"><b>II. Biaya Tenaga Kerja &amp; Konsultan</b><br>
<span style="font-size:7px;color:#555;">(personnel &amp; consultant cost)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td class="col-yellow" style="text-align:right;">Rp {kdn_2:,.2f}</td>
<td class="col-yellow" style="text-align:right;">Rp {kln_2:,.2f}</td>
<td style="text-align:right;">Rp {calc["tot_biaya_2"]:,.2f}</td>
<td style="text-align:center;">
{(kdn_2/calc["tot_biaya_2"]*100) if calc["tot_biaya_2"] > 0 else 0:.2f}%
</td>
<td style="text-align:right;">Rp {kdn_2:,.2f}</td>
</tr>
<tr>
<td style="text-align:center;"><i>US$</i></td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:center;">0.00%</td>
<td style="text-align:right;">0.00</td>
</tr>

<tr>
<td rowspan="2"><b>III. Biaya Alat Kerja / Fasilitas Kerja</b><br>
<span style="font-size:7px;color:#555;">(equipment &amp; work facility cost)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td class="col-yellow" style="text-align:right;">Rp {kdn_3:,.2f}</td>
<td class="col-yellow" style="text-align:right;">Rp {kln_3:,.2f}</td>
<td style="text-align:right;">Rp {calc["tot_biaya_3"]:,.2f}</td>
<td style="text-align:center;">
{(kdn_3/calc["tot_biaya_3"]*100) if calc["tot_biaya_3"] > 0 else 0:.2f}%
</td>
<td style="text-align:right;">Rp {kdn_3:,.2f}</td>
</tr>
<tr>
<td style="text-align:center;"><i>US$</i></td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:center;">0.00%</td>
<td style="text-align:right;">0.00</td>
</tr>

<tr>
<td rowspan="2"><b>IV. Biaya Jasa Umum</b><br>
<span style="font-size:7px;color:#555;">(other services cost)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td class="col-yellow" style="text-align:right;">Rp {kdn_4:,.2f}</td>
<td class="col-yellow" style="text-align:right;">Rp {kln_4:,.2f}</td>
<td style="text-align:right;">Rp {calc["tot_biaya_4"]:,.2f}</td>
<td style="text-align:center;">
{(kdn_4/calc["tot_biaya_4"]*100) if calc["tot_biaya_4"] > 0 else 0:.2f}%
</td>
<td style="text-align:right;">Rp {kdn_4:,.2f}</td>
</tr>
<tr>
<td style="text-align:center;"><i>US$</i></td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:center;">0.00%</td>
<td style="text-align:right;">0.00</td>
</tr>

<tr style="background:#f8fafc;font-weight:bold;">
<td rowspan="2">V. JUMLAH BIAYA (Σ I s/d IV)<br>
<span style="font-size:7px;color:#555;">(Total Cost)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td style="text-align:right;">Rp {calc["tot_kdn_biaya"]:,.2f}</td>
<td style="text-align:right;">Rp {calc["tot_kln_biaya"]:,.2f}</td>
<td style="text-align:right;">Rp {calc["jumlah_biaya_total"]:,.2f}</td>
<td style="text-align:center;">
{(calc["tot_kdn_biaya"]/calc["jumlah_biaya_total"]*100) if calc["jumlah_biaya_total"] > 0 else 0:.2f}%
</td>
<td style="text-align:right;">Rp {calc["tot_kdn_biaya"]:,.2f}</td>
</tr>
<tr style="background:#f8fafc;font-weight:bold;">
<td style="text-align:center;"><i>US$</i></td>
<td style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:right;">0.00</td>
<td style="text-align:center;">0.00%</td>
<td style="text-align:right;">0.00</td>
</tr>

<tr>
<td rowspan="2"><b>B. KOMPONEN BUKAN BIAYA</b><br>
<span style="font-size:7px;color:#555;">(Non-cost Component)</span></td>
<td style="text-align:center;"><b>Rp</b></td>
<td colspan="2"></td>
<td class="col-yellow" style="text-align:right;">
<b>Rp {calc["komponen_bukan_biaya"]:,.2f}</b>
</td>
<td colspan="2"></td>
</tr>
<tr>
<td style="text-align:center;"><i>US$</i></td>
<td colspan="2"></td>
<td class="col-yellow" style="text-align:right;">0.00</td>
<td colspan="2"></td>
</tr>

<tr style="background:#f1f5f9;font-weight:bold;font-size:10px;">
<td rowspan="2">C. JUMLAH NILAI TOTAL (A + B)</td>
<td style="text-align:center;"><b>Rp</b></td>
<td colspan="3" style="text-align:right;">
<b>Rp {calc["jumlah_nilai_total"]:,.2f}</b>
</td>
<td colspan="2"></td>
</tr>
<tr style="background:#f1f5f9;font-weight:bold;font-size:10px;">
<td style="text-align:center;"><i>US$</i></td>
<td colspan="3" style="text-align:right;">0.00</td>
<td colspan="2"></td>
</tr>

<tr style="background:#e2e8f0;font-weight:bold;font-size:11px;color:#065f46;">
<td colspan="5">CAPAIAN PERSENTASE TKDN AKHIR (%)</td>
<td colspan="2" style="text-align:right;font-size:12px;">
{calc["persen_tkdn_akhir"]:.2f} %
</td>
</tr>
</table>

<div class="footer-notes">
<b>Catatan:</b><br>
&bull; Isi hanya pada kolom yang berwarna kuning pastel.<br>
&bull; Formulasi perhitungan mengacu pada Permen ESDM No. 15 Tahun 2013.
</div>

<table class="sign-section">
<tr>
<td style="width:50%;"></td>
<td style="width:50%;text-align:center;padding-left:20px;">
{_safe(lokasi)}, {_safe(tgl_dokumen)}<br>
<b>PT. Banggai Sentral Sulawesi</b>
{ttd_html}
<u><b>{_safe(nama_direktur)}</b></u><br>
Direktur
</td>
</tr>
</table>

</body>
</html>
"""

    return html_content


__all__ = [
    "DEFAULTS",
    "PERCENT_FIELDS",
    "get_tkdn_defaults",
    "normalize_tkdn_params",
    "validate_tkdn_params",
    "calculate_tkdn",
    "generate_tkdn_html",
]
