import os
import logging
import json
import mimetypes
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import (
    Blueprint,
    render_template,
    jsonify,
    request,
    make_response,
    send_file,
    session,
    redirect,
    url_for,
)
from werkzeug.utils import secure_filename
from modul_keamanan.decorators import login_required
from db_connection import get_db_connection
from modul_dokumen.document_engine import (
    build_document_manifest,
    get_document_config,
    get_document_list,
    render_document,
)
from modul_input.modul_0_referensi import (
    can_write_master,
    delete_master,
    get_master_nama_alat,
    get_master_referensi,
    get_options,
    insert_master,
    save_as_master,
    update_master,
    _json_record,
)

from modul_input.modul_1_person_master import (
    can_write_person,
    insert_person,
    list_persons,
    update_person,
    deactivate_person,
)


from modul_dokumen.document_package_service import (
    load_saved_tkdn,
    load_saved_opname,
    load_opname_for_package,
)

from modul_dokumen.document_package_renderer import (
    PACKAGE_ORDER,
    render_document_package,
)
from modul_dokumen.master_package_config import load_config, save_config
from invoice_documents import invoice_context, receipt_context


# Master PO bersifat optional pada instalasi lama.
# Jika modul_po.master_po tersedia, gunakan implementasi utamanya.
# Jika belum tersedia, routes.py tetap dapat start dan menyediakan
# endpoint Master PO dasar sehingga aplikasi tidak berhenti pada import error.
try:
    from modul_po.master_po import (
        bootstrap_master_po,
        sync_pi_realization,
        remap_unmapped_realizations,
    )
except (ImportError, ModuleNotFoundError):
    LOGGER = logging.getLogger(__name__)

    def bootstrap_master_po():
        """Fallback bootstrap untuk instalasi yang belum memiliki modul_po."""
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS database_master_po (
                        id BIGINT NOT NULL AUTO_INCREMENT,
                        nomor_kontrak VARCHAR(255) NOT NULL,
                        nomor_po VARCHAR(255) NOT NULL,
                        tanggal_po DATE NULL,
                        deskripsi_po TEXT NULL,
                        mata_uang VARCHAR(20) NULL DEFAULT 'IDR',
                        status_po VARCHAR(30) NULL DEFAULT 'ACTIVE',
                        total_po DECIMAL(20,2) NULL DEFAULT 0,
                        update_terakhir DATETIME NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        UNIQUE KEY uq_master_po_kontrak_po (nomor_kontrak, nomor_po)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS database_master_po_item (
                        id BIGINT NOT NULL AUTO_INCREMENT,
                        po_id BIGINT NOT NULL,
                        nomor_kontrak VARCHAR(255) NOT NULL,
                        nomor_po VARCHAR(255) NOT NULL,
                        kategori VARCHAR(255) NULL,
                        uraian_pekerjaan TEXT NULL,
                        deskripsi_pekerjaan TEXT NULL,
                        qty DECIMAL(20,4) NULL DEFAULT 0,
                        unit VARCHAR(100) NULL,
                        harga_satuan DECIMAL(20,2) NULL DEFAULT 0,
                        total_harga DECIMAL(20,2) NULL DEFAULT 0,
                        status_item VARCHAR(30) NULL DEFAULT 'ACTIVE',
                        update_terakhir DATETIME NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        KEY idx_master_po_item_po (po_id),
                        CONSTRAINT fk_master_po_item_po
                            FOREIGN KEY (po_id) REFERENCES database_master_po(id)
                            ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def sync_pi_realization(nomor_kontrak="", pi_no=""):
        """Fallback aman: ledger PO tetap dapat diproses tanpa modul master_po."""
        return None

    def remap_unmapped_realizations(nomor_kontrak=""):
        """Fallback aman untuk instalasi tanpa modul_po."""
        return None

from modul_input.modul_1_database import (
    ALL_FIELDS,
    FIELD_GROUPS,
    can_write,
    clean,
    delete_pi,
    find_row,
    insert_pi,
    load_rows,
    normalize_row,
    update_pi,
    get_contract_impact,
    update_contract_master,
    sort_contract_records,
    sort_pi_records,
)



# ============================================================
# BLUEPRINT
# ============================================================

navigation_bp = Blueprint(
    "navigation",
    __name__,
)

LOGGER = logging.getLogger(__name__)


# ============================================================
# KONFIGURASI DATABASE
# ============================================================

NAMA_TABEL_TRANSAKSI = "database_transaksi_rincian"


# ============================================================
# HELPER
# ============================================================

def nilai_bersih(value):
    """
    Membersihkan nilai database agar aman dikirim sebagai JSON.
    """

    if value is None:
        return ""

    return str(value).strip()


def hitung_total_rincian(kategori, qty, harga_satuan, percent=100):
    """Hitung total satu baris tanpa menggabungkan rincian lain."""
    def angka(value, default=0.0):
        try:
            if value in (None, ""):
                return default
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return default

    kategori_lower = nilai_bersih(kategori).lower()
    qty_value = angka(qty)
    harga_value = angka(harga_satuan)
    percent_value = angka(percent, 100.0)

    if "provisional" in kategori_lower or "professional" in kategori_lower:
        faktor = 1.15
    elif "estimated" in kategori_lower or "estimasi" in kategori_lower:
        faktor = 0.90
    else:
        faktor = 1.0

    # Pembulatan wajib dilakukan di boundary aplikasi agar artefak IEEE-754
    # seperti 862499.9999999999 tidak pernah dikirim ke UI/dokumen.
    return round(qty_value * harga_value * faktor * (percent_value / 100.0), 2)


def normalisasi_record(record):
    """
    Membersihkan seluruh field dalam satu record.
    """

    hasil = {}

    for key, value in record.items():
        hasil[str(key).strip()] = nilai_bersih(value)

    # Perbaiki snapshot lama yang pernah menyimpan seluruh Provisional Sum
    # pada baris pertama dan 0 pada baris berikutnya. Perhitungan dilakukan
    # per record, sehingga keterangan dan nominal tiap rincian tetap terpisah.
    if {"kategori", "qty", "harga_satuan"}.issubset(hasil):
        hasil["total_harga"] = str(hitung_total_rincian(
            hasil.get("kategori"),
            hasil.get("qty"),
            hasil.get("harga_satuan"),
            hasil.get("percent") if hasil.get("percent") not in (None, "") else 100,
        ))

    return hasil


def enrich_transactions(transaksi_list, nomor_kontrak="", pi_no=""):
    """Menyiapkan alias field yang dibutuhkan generator dokumen secara lokal."""
    hasil = []
    for source in transaksi_list or []:
        row = dict(source or {})
        aliases = {
            "Nomor Kontrak": row.get("nomor_kontrak", ""),
            "PI No.": row.get("pi_no", ""),
            "Kategori": row.get("kategori", ""),
            "Deskripsi Pekerjaan": row.get("deskripsi_pekerjaan", ""),
            "Qty": row.get("qty", ""),
            "Unit": row.get("unit", ""),
            "Persentase": row.get("percent", ""),
            "Tanggal Mulai": row.get("tanggal_mulai", ""),
            "Tanggal Selesai": row.get("tanggal_selesai", ""),
            "Harga Satuan": row.get("harga_satuan", ""),
            "Total Harga": row.get("total_harga", ""),
            "Keterangan": row.get("keterangan", ""),
            "Nomor PO": row.get("nomor_po", ""),
            "Tanggal PO": row.get("tanggal_po", ""),
            "Deskripsi PO": row.get("deskripsi_po", ""),
            "Jenis BASTP": row.get("jenis_bastp", ""),
            "Nomor WAN / SA": row.get("nomor_wan_sa", ""),
            "Nomor WO": row.get("nomor_wo", ""),
            "Mata Uang": row.get("mata_uang", ""),
            "Bank": row.get("bank_name", ""),
            "Cabang Bank": row.get("bank_branch", ""),
            "Nomor Rekening": row.get("account_no", ""),
            "Atas Nama Rekening": row.get("account_name", ""),
            "Attn": row.get("attn", ""),
        }
        for key, value in aliases.items():
            if key not in row or nilai_bersih(row.get(key)) == "":
                row[key] = value
        hasil.append(row)
    return hasil
    
    



# ============================================================
# DATABASE TRANSAKSI
# ============================================================

NAMA_TABEL_TRANSAKSI = "database_transaksi_rincian"

# Batas default data per request
DEFAULT_PER_PAGE = 50

# Batas maksimum yang boleh diminta browser
MAX_PER_PAGE = 100


def nilai_bersih(value):
    """
    Membersihkan nilai database agar aman dikirim sebagai JSON.
    """

    if value is None:
        return ""

    return str(value).strip()


def normalisasi_record(record):
    """
    Membersihkan seluruh field dalam satu record.
    """

    hasil = {}

    for key, value in record.items():
        hasil[str(key).strip()] = nilai_bersih(value)

    if {"kategori", "qty", "harga_satuan"}.issubset(hasil):
        hasil["total_harga"] = str(hitung_total_rincian(
            hasil.get("kategori"), hasil.get("qty"), hasil.get("harga_satuan"),
            hasil.get("percent") if hasil.get("percent") not in (None, "") else 100,
        ))

    return hasil


def muat_transaksi_terfilter(nomor_kontrak=None, pi_no=None):
    """
    Mengambil seluruh rincian transaksi dari database_transaksi_rincian
    berdasarkan Nomor Kontrak dan, jika diberikan, PI No.

    Fungsi ini berada langsung di routes.py agar seluruh logika
    pengambilan transaksi tetap terpusat pada route module ini.
    """
    nomor_kontrak = nilai_bersih(nomor_kontrak)
    pi_no = nilai_bersih(pi_no)

    if not nomor_kontrak:
        return []

    columns = """
        nomor_kontrak, nama_kontrak, nomor_tender, pi_no, tanggal_pi,
        ditujukan_kepada, alamat_pihak_pertama, jangka_waktu_kontrak,
        nomor_po, po_item_id, deskripsi_po, tanggal_po, mata_uang, kategori,
        deskripsi_pekerjaan, qty, unit, percent, tanggal_mulai,
        tanggal_selesai, harga_satuan, total_harga, bank_name,
        bank_branch, account_no, account_name, attn, keterangan,
        update_terakhir, items,
        COALESCE((
            SELECT `Jenis BASTP`
            FROM database_proforma_invoice p
            WHERE TRIM(CAST(p.`Nomor Kontrak` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.nomor_kontrak AS CHAR))
              AND TRIM(CAST(p.`Proforma Invoice No.` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.pi_no AS CHAR))
            LIMIT 1
        ), jenis_bastp) AS jenis_bastp,
        nomor_wan_sa, nomor_wo
    """

    # Data transaksi pada beberapa versi/import database dapat tersimpan
    # pada kolom utama maupun kolom *_2. Gunakan keduanya agar Module 1
    # tidak menampilkan 0 transaksi hanya karena sumber memakai field kedua.
    query = f"""
        SELECT {columns}
        FROM {NAMA_TABEL_TRANSAKSI}
        WHERE TRIM(CAST(nomor_kontrak AS CHAR)) = %s
    """

    params = [nomor_kontrak]

    if pi_no:
        query += """
            AND TRIM(CAST(pi_no AS CHAR)) = %s
        """
        params.append(pi_no)

    query += """
        ORDER BY kategori ASC, update_terakhir DESC
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                names = [column[0] for column in cursor.description]

                result = []

                for row in rows:
                    if isinstance(row, dict):
                        record = dict(row)
                    else:
                        record = dict(zip(names, row))

                    result.append(normalisasi_record(record))

                return result

            finally:
                cursor.close()

    except Exception:
        LOGGER.exception(
            "[MODUL 2] Gagal mengambil rincian transaksi."
        )
        raise


# ============================================================
# LOGIKA DOKUMEN
# ============================================================

def tentukan_dokumen(jenis_bastp):
    """Satu sumber kebenaran daftar dokumen Modul 2."""
    return build_document_manifest(jenis_bastp)


# ============================================================
# MODUL 0
# ============================================================


@navigation_bp.route(
    "/modul/master-referensi"
)
@login_required
def master_referensi():

    try:
        options = get_options()

        return render_template(
            "modul_0_referensi.html",
            title="Modul 0: Master Referensi Harga & Pekerjaan",
            description="Master referensi harga dan pekerjaan.",
            module_code="MODUL 0",
            options=options,
            can_write=can_write_master(),
            partial=False,
            load_error=None,
        )

    except Exception as error:
        LOGGER.exception("Modul 0 gagal dimuat.")

        return render_template(
            "modul_0_referensi.html",
            title="Modul 0: Master Referensi Harga & Pekerjaan",
            description="Master referensi harga dan pekerjaan.",
            module_code="MODUL 0",
            options={
                "nomor_kontrak": [],
                "kategori": [],
                "unit": [],
            },
            can_write=can_write_master(),
            partial=False,
            load_error=str(error),
        ), 500


@navigation_bp.route(
    "/partial/modul-0"
)
@login_required
def partial_modul_0():

    try:
        options = get_options()

        return render_template(
            "modul_0_referensi.html",
            title="Modul 0: Master Referensi Harga & Pekerjaan",
            description="Master referensi harga dan pekerjaan.",
            module_code="MODUL 0",
            options=options,
            can_write=can_write_master(),
            partial=True,
            load_error=None,
        )

    except Exception as error:
        LOGGER.exception("Partial Modul 0 gagal dimuat.")

        return (
            f"""
            <div class="m0-error">
                <b>Modul 0 gagal dimuat.</b>
                <div>{error}</div>
            </div>
            """,
            500,
        )


@navigation_bp.route(
    "/api/modul-0/referensi",
    methods=["GET"]
)
@login_required
def api_modul_0_referensi():

    try:
        result = get_master_referensi(
            nomor_kontrak=request.args.get("nomor_kontrak", ""),
            kategori=request.args.get("kategori", ""),
            search=request.args.get("search", ""),
            page=request.args.get("page", 1, type=int),
            per_page=request.args.get("per_page", 50, type=int),
        )

        return jsonify({
            "success": True,
            "data": [
                _json_record(row)
                for row in result["rows"]
            ],
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "total_pages": result["total_pages"],
        })

    except Exception as error:
        LOGGER.exception("API master referensi gagal.")

        return jsonify({
            "success": False,
            "message": str(error),
            "data": [],
        }), 500


@navigation_bp.route(
    "/api/modul-0/options",
    methods=["GET"]
)
@login_required
def api_modul_0_options():

    try:
        return jsonify({
            "success": True,
            "data": get_options(),
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route(
    "/api/modul-0/nama-alat",
    methods=["GET"]
)
@login_required
def api_modul_0_nama_alat():

    try:
        rows = get_master_nama_alat()

        return jsonify({
            "success": True,
            "data": rows,
            "total": len(rows),
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "message": str(error),
            "data": [],
        }), 500


@navigation_bp.route(
    "/api/modul-0/referensi",
    methods=["POST"]
)
@login_required
def api_modul_0_create():

    if not can_write_master():
        return jsonify({
            "success": False,
            "message": "Akun ini hanya memiliki akses baca.",
        }), 403

    try:
        insert_master(request.get_json(silent=True) or {})

        return jsonify({
            "success": True,
            "message": "Master Referensi baru berhasil disimpan.",
        }), 201

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        LOGGER.exception("Gagal INSERT master referensi.")

        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route(
    "/api/modul-0/referensi/save-as",
    methods=["POST"]
)
@login_required
def api_modul_0_save_as():

    if not can_write_master():
        return jsonify({
            "success": False,
            "message": "Akun ini hanya memiliki akses baca.",
        }), 403

    try:
        save_as_master(request.get_json(silent=True) or {})

        return jsonify({
            "success": True,
            "message": "Save As berhasil. Record baru dibuat.",
        }), 201

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        LOGGER.exception("Gagal SAVE AS master referensi.")

        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route(
    "/api/modul-0/referensi/update",
    methods=["POST"]
)
@login_required
def api_modul_0_update():

    if not can_write_master():
        return jsonify({
            "success": False,
            "message": "Akun ini hanya memiliki akses baca.",
        }), 403

    try:
        body = request.get_json(silent=True) or {}
        update_master(
            body.get("data") or {},
            body.get("old_data") or {},
        )

        return jsonify({
            "success": True,
            "message": "Master Referensi berhasil di-update.",
        })

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        LOGGER.exception("Gagal UPDATE master referensi.")

        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route(
    "/api/modul-0/referensi/delete",
    methods=["POST"]
)
@login_required
def api_modul_0_delete():

    if not can_write_master():
        return jsonify({
            "success": False,
            "message": "Akun ini hanya memiliki akses baca.",
        }), 403

    try:
        delete_master(
            request.get_json(silent=True) or {}
        )

        return jsonify({
            "success": True,
            "message": "Master Referensi berhasil dihapus.",
        })

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        LOGGER.exception("Gagal DELETE master referensi.")

        return jsonify({
            "success": False,
            "message": str(error),
        }), 500

# ============================================================
# MASTER DATA - MASTER PERSONIL
# ============================================================

@navigation_bp.route("/master/personil", methods=["GET"])
@login_required
def master_personil():
    try:
        return render_template(
            "modul_1_master_personil.html",
            title="Master Personil",
            module_code="MASTER DATA",
            can_write=can_write_person(),
        )
    except Exception as error:
        LOGGER.exception("Gagal membuka Master Personil.")
        return str(error), 500


@navigation_bp.route("/api/modul-1/persons", methods=["GET"])
@login_required
def api_modul_1_persons():
    try:
        rows = list_persons(
            search=request.args.get("search", ""),
            active_only=True,
        )
        return jsonify({
            "success": True,
            "data": rows,
            "total": len(rows),
        })
    except Exception as error:
        LOGGER.exception("API master personil Module 1 gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": [],
        }), 500


@navigation_bp.route("/api/modul-1/persons", methods=["POST"])
@login_required
def api_modul_1_person_create():
    if not can_write_person():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    try:
        result = insert_person(request.get_json(silent=True) or {})
        rows = list_persons(active_only=True)

        return jsonify({
            "success": True,
            "message": (
                "Nama dan jabatan berhasil ditambahkan ke Master Personil."
                if result.get("created")
                else "Nama dan jabatan sudah ada di Master Personil."
            ),
            "data": {
                **result,
                "rows": rows,
            },
        }), 201 if result.get("created") else 200

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal menyimpan master personil Module 1.")
        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route("/api/modul-1/persons/<int:person_id>", methods=["PUT"])
@login_required
def api_modul_1_person_update(person_id):
    if not can_write_person():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        payload = request.get_json(silent=True) or {}
        payload["id"] = person_id
        result = update_person(payload)
        return jsonify({"success": True, "message": "Master Personil berhasil diperbarui.", "data": result})
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal UPDATE master personil Module 1.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-1/persons/<int:person_id>", methods=["DELETE"])
@login_required
def api_modul_1_person_delete(person_id):
    if not can_write_person():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        deactivate_person(person_id)
        return jsonify({"success": True, "message": "Personil dinonaktifkan. Histori PI tetap aman."})
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal NONAKTIFKAN master personil Module 1.")
        return jsonify({"success": False, "message": str(error)}), 500


# ============================================================
# MODUL 1 - DATABASE & MASTER KONTRAK
# ============================================================

def _render_live_module(template_name, **context):
    response = make_response(render_template(template_name, **context))
    response.headers["Cache-Control"] = "no-store"
    return response

@navigation_bp.route("/modul/database-kontrak", methods=["GET"])
@login_required
def database_kontrak():
    try:
        rows,mapping,_=load_rows(); contracts={}
        for row in rows:
            record=normalize_row(row,mapping); key=record.get("Nomor Kontrak","")
            if key: contracts.setdefault(key,record)
        return _render_live_module("modul_1_database.html",title="Modul 1: Database & Master Kontrak",module_code="MODUL 1",contracts=list(contracts.values()),can_write=can_write(),partial=False,field_groups=FIELD_GROUPS,fields=ALL_FIELDS,load_error=None)
    except Exception as error:
        return _render_live_module("modul_1_database.html",title="Modul 1: Database & Master Kontrak",module_code="MODUL 1",contracts=[],can_write=can_write(),partial=False,field_groups=FIELD_GROUPS,fields=ALL_FIELDS,load_error=str(error)),500

@navigation_bp.route("/partial/modul-1", methods=["GET"])
@login_required
def partial_modul_1():
    return database_kontrak()

@navigation_bp.route("/api/modul-1/contracts", methods=["GET"])
@login_required
def api_modul_1_contracts():
    try:
        rows, mapping, _ = load_rows({"search": request.args.get("search", "")})
        seen = {}
        for row in rows:
            record = normalize_row(row, mapping)
            key = clean(record.get("Nomor Kontrak", ""))
            if key and key not in seen:
                seen[key] = record

        data = sort_contract_records(list(seen.values()))
        return jsonify({"success": True, "data": data, "total": len(data)})
    except Exception as error:
        LOGGER.exception("API daftar kontrak Module 1 gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route("/api/modul-1/pi", methods=["GET"])
@login_required
def api_modul_1_pi():
    contract = clean(request.args.get("nomor_kontrak"))
    if not contract:
        return jsonify({
            "success": False,
            "message": "Nomor Kontrak wajib dipilih.",
            "data": []
        }), 400

    try:
        rows, mapping, _ = load_rows({"nomor_kontrak": contract})
        data = [normalize_row(r, mapping) for r in rows]
        data = sort_pi_records(data)

        # Tambahkan ringkasan transaksi untuk setiap PI agar listing Module 1
        # langsung dapat menampilkan informasi transaksi tanpa mengubah sumber
        # data master PI.
        tx_by_pi = {}
        transaksi = muat_transaksi_terfilter(contract)
        for tx in transaksi:
            pi_no = clean(tx.get("pi_no"))
            if not pi_no:
                continue
            tx_by_pi.setdefault(pi_no, []).append(tx)

        for record in data:
            pi_no = clean(record.get("Proforma Invoice No."))
            tx_rows = tx_by_pi.get(pi_no, [])
            record["_transaction_count"] = len(tx_rows)
            record["_transaction_total"] = sum(
                float(str(x.get("total_harga") or 0).replace(",", "") or 0)
                for x in tx_rows
                if str(x.get("total_harga") or "").strip()
            )
            record["_transaction_categories"] = sorted({
                clean(x.get("kategori"))
                for x in tx_rows
                if clean(x.get("kategori"))
            })
            record["_transaction_po"] = next(
                (clean(x.get("nomor_po")) for x in tx_rows if clean(x.get("nomor_po"))),
                ""
            )
            record["_transaction_wo"] = next(
                (clean(x.get("nomor_wo")) for x in tx_rows if clean(x.get("nomor_wo"))),
                ""
            )

        return jsonify({
            "success": True,
            "nomor_kontrak": contract,
            "data": data,
            "total": len(data),
        })
    except Exception as error:
        LOGGER.exception("API daftar PI Module 1 gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": []
        }), 500


@navigation_bp.route("/api/dashboard/analytics", methods=["GET"])
@login_required
def api_dashboard_analytics():
    """Agregasi ringan untuk grafik dashboard.

    Dashboard hanya membaca ringkasan dari database transaksi. Detail edit tetap
    berada di Modul 1/2, sedangkan endpoint ini menjaga agar grafik tidak perlu
    memuat setiap detail PI satu per satu.
    """
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    SELECT
                        COALESCE(NULLIF(TRIM(kategori), ''), 'Tanpa Kategori') AS kategori,
                        COALESCE(SUM(qty), 0) AS total_volume,
                        COALESCE(SUM(total_harga), 0) AS total_biaya,
                        COUNT(*) AS item_count,
                        COUNT(DISTINCT CONCAT(nomor_kontrak, '|', pi_no)) AS pi_count
                    FROM database_transaksi_rincian
                    GROUP BY COALESCE(NULLIF(TRIM(kategori), ''), 'Tanpa Kategori')
                    ORDER BY total_biaya DESC
                """)
                category_rows = cursor.fetchall()
                category_columns = [column[0] for column in cursor.description]

                cursor.execute("""
                    SELECT
                        COALESCE(
                            DATE_FORMAT(NULLIF(tanggal_pi, ''), '%Y-%m'),
                            DATE_FORMAT(update_terakhir, '%Y-%m'),
                            'Tanpa Tanggal'
                        ) AS periode,
                        COUNT(DISTINCT CONCAT(nomor_kontrak, '|', pi_no)) AS pi_count,
                        COUNT(*) AS item_count,
                        COALESCE(SUM(total_harga), 0) AS total_biaya,
                        COALESCE(SUM(qty), 0) AS total_volume
                    FROM database_transaksi_rincian
                    GROUP BY COALESCE(
                        DATE_FORMAT(NULLIF(tanggal_pi, ''), '%Y-%m'),
                        DATE_FORMAT(update_terakhir, '%Y-%m'),
                        'Tanpa Tanggal'
                    )
                    ORDER BY periode ASC
                """)
                trend_rows = cursor.fetchall()
                trend_columns = [column[0] for column in cursor.description]

                cursor.execute("""
                    SELECT
                        COALESCE(NULLIF(TRIM(kategori), ''), 'Tanpa Kategori') AS kategori,
                        COALESCE(NULLIF(TRIM(deskripsi_pekerjaan), ''), 'Tanpa Uraian') AS uraian,
                        COALESCE(NULLIF(TRIM(unit), ''), '-') AS unit,
                        COALESCE(SUM(qty), 0) AS total_volume,
                        COALESCE(SUM(total_harga), 0) AS total_biaya,
                        COUNT(*) AS item_count,
                        COUNT(DISTINCT CONCAT(nomor_kontrak, '|', pi_no)) AS pi_count
                    FROM database_transaksi_rincian
                    GROUP BY
                        COALESCE(NULLIF(TRIM(kategori), ''), 'Tanpa Kategori'),
                        COALESCE(NULLIF(TRIM(deskripsi_pekerjaan), ''), 'Tanpa Uraian'),
                        COALESCE(NULLIF(TRIM(unit), ''), '-')
                    ORDER BY total_biaya DESC
                    LIMIT 500
                """)
                item_rows = cursor.fetchall()
                item_columns = [column[0] for column in cursor.description]
            finally:
                cursor.close()

        def serialise(rows, columns):
            result = []
            for row in rows:
                record = row if isinstance(row, dict) else dict(zip(columns, row))
                clean_record = {}
                for key, value in record.items():
                    if key in {'total_volume', 'total_biaya'}:
                        try:
                            clean_record[key] = float(value or 0)
                        except (TypeError, ValueError):
                            clean_record[key] = 0
                    elif key in {'item_count', 'pi_count'}:
                        try:
                            clean_record[key] = int(value or 0)
                        except (TypeError, ValueError):
                            clean_record[key] = 0
                    else:
                        clean_record[key] = nilai_bersih(value)
                result.append(clean_record)
            return result

        return jsonify({
            "success": True,
            "categories": serialise(category_rows, category_columns),
            "trend": serialise(trend_rows, trend_columns),
            "items": serialise(item_rows, item_columns),
        })
    except Exception as error:
        LOGGER.exception("API analytics dashboard gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "categories": [],
            "trend": [],
            "items": [],
        }), 500


@navigation_bp.route("/api/dashboard/document-readiness", methods=["GET"])
@login_required
def api_dashboard_document_readiness():
    """Ringkasan dokumen TKDN/Opname tersimpan tanpa mengubah skema."""
    try:
        result = {"tkdn_saved_pi": None, "opname_saved_pi": None}
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    SELECT TABLE_NAME
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME IN ('database_tkdn', 'database_opname')
                """)
                available = {str(row[0]) for row in cursor.fetchall()}
                if "database_tkdn" in available:
                    cursor.execute("""
                        SELECT COUNT(DISTINCT CONCAT(nomor_kontrak, '|', pi_no))
                        FROM database_tkdn
                        WHERE TRIM(COALESCE(nomor_kontrak, '')) <> ''
                          AND TRIM(COALESCE(pi_no, '')) <> ''
                    """)
                    result["tkdn_saved_pi"] = int((cursor.fetchone() or [0])[0] or 0)
                if "database_opname" in available:
                    cursor.execute("""
                        SELECT COUNT(DISTINCT CONCAT(nomor_kontrak, '|', nomor_pi))
                        FROM database_opname
                        WHERE TRIM(COALESCE(nomor_kontrak, '')) <> ''
                          AND TRIM(COALESCE(nomor_pi, '')) <> ''
                    """)
                    result["opname_saved_pi"] = int((cursor.fetchone() or [0])[0] or 0)
            finally:
                cursor.close()
        return jsonify({"success": True, "data": result})
    except Exception as error:
        LOGGER.exception("Ringkasan kesiapan dokumen dashboard gagal.")
        return jsonify({"success": False, "message": str(error), "data": {}}), 500


@navigation_bp.route("/api/modul-1/pi/detail", methods=["GET"])
@login_required
def api_modul_1_pi_detail():
    contract = clean(request.args.get("nomor_kontrak"))
    pi = clean(request.args.get("pi"))

    if not contract or not pi:
        return jsonify({
            "success": False,
            "message": "Kontrak dan PI wajib dipilih.",
            "data": {}
        }), 400

    try:
        row, mapping, _ = find_row(contract, pi)
        if not row:
            return jsonify({
                "success": False,
                "message": "Data PI tidak ditemukan.",
                "data": {}
            }), 404

        master = normalize_row(row, mapping)
        transaksi = enrich_transactions(
            muat_transaksi_terfilter(contract, pi),
            contract,
            pi,
        )

        return jsonify({
            "success": True,
            "nomor_kontrak": contract,
            "pi": pi,
            "data": master,
            "transactions": transaksi,
            "transaction_count": len(transaksi),
            "transaction_total": sum(
                float(str(x.get("total_harga") or 0).replace(",", "") or 0)
                for x in transaksi
                if str(x.get("total_harga") or "").strip()
            ),
        })
    except Exception as error:
        LOGGER.exception("API detail PI Module 1 gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": {}
        }), 500


@navigation_bp.route("/api/modul-1/contract/impact", methods=["GET"])
@login_required
def api_modul_1_contract_impact():
    contract = clean(request.args.get("nomor_kontrak"))
    if not contract:
        return jsonify({
            "success": False,
            "message": "Nomor Kontrak wajib dipilih."
        }), 400

    try:
        return jsonify({
            "success": True,
            "data": get_contract_impact(contract),
        })
    except Exception as error:
        LOGGER.exception("API impact kontrak Module 1 gagal.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


@navigation_bp.route("/api/modul-1/contract/update", methods=["POST"])
@login_required
def api_modul_1_contract_update():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca."
        }), 403

    payload = request.get_json(silent=True) or {}
    old_contract = clean(payload.pop("_old_contract", ""))

    if not old_contract:
        return jsonify({
            "success": False,
            "message": "Nomor Kontrak lama wajib ditentukan."
        }), 400

    try:
        result = update_contract_master(old_contract, payload)

        # Kembalikan key baru secara eksplisit agar frontend dapat
        # memindahkan state setelah rename kontrak.
        return jsonify({
            "success": True,
            "message": (
                f"Kontrak berhasil diperbarui. "
                f"{result.get('affected_pi', 0)} PI ikut diperbarui."
            ),
            "data": {
                **result,
                "old_contract": old_contract,
                "new_contract": clean(
                    result.get("new_contract")
                    or payload.get("Nomor Kontrak")
                    or old_contract
                ),
            },
        })
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal update master kontrak Module 1.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


@navigation_bp.route("/api/modul-1/pi", methods=["POST"])
@login_required
def api_modul_1_pi_create():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca."
        }), 403

    try:
        payload = request.get_json(silent=True) or {}
        insert_pi(payload)
        sync_pi_realization(
            clean(payload.get("Nomor Kontrak") or payload.get("nomor_kontrak")),
            clean(payload.get("Proforma Invoice No.") or payload.get("pi_no") or payload.get("pi")),
        )
        return jsonify({
            "success": True,
            "message": "PI baru berhasil disimpan."
        }), 201
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal membuat PI Module 1.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


@navigation_bp.route("/api/modul-1/pi/update", methods=["POST"])
@login_required
def api_modul_1_pi_update():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca."
        }), 403

    payload = request.get_json(silent=True) or {}
    old_contract = clean(payload.pop("_old_contract", ""))
    old_pi = clean(payload.pop("_old_pi", ""))

    if not old_contract or not old_pi:
        return jsonify({
            "success": False,
            "message": "Data PI lama wajib ditentukan."
        }), 400

    try:
        result = update_pi(old_contract, old_pi, payload)

        new_contract = clean(
            (result or {}).get("new_contract")
            if isinstance(result, dict)
            else payload.get("Nomor Kontrak")
        ) or clean(payload.get("Nomor Kontrak") or old_contract)
        new_pi = clean(
            (result or {}).get("new_pi")
            if isinstance(result, dict)
            else payload.get("Proforma Invoice No.")
        ) or clean(payload.get("Proforma Invoice No.") or old_pi)

        if old_contract != new_contract or old_pi != new_pi:
            # Rebuild old/new ledgers so renamed PI data cannot remain stale.
            try:
                with get_db_connection() as _conn:
                    _cur = _conn.cursor()
                    try:
                        _cur.execute(
                            "DELETE FROM database_po_realisasi WHERE nomor_kontrak=%s AND nomor_pi=%s",
                            (old_contract, old_pi),
                        )
                        _conn.commit()
                    finally:
                        _cur.close()
            except Exception:
                LOGGER.exception("Gagal membersihkan histori PO PI lama.")
        sync_pi_realization(new_contract, new_pi)

        return jsonify({
            "success": True,
            "message": "Data PI berhasil diperbarui.",
            "data": {
                **(result if isinstance(result, dict) else {}),
                "old_contract": old_contract,
                "old_pi": old_pi,
                "new_contract": clean(
                    (result or {}).get("new_contract")
                    if isinstance(result, dict)
                    else payload.get("Nomor Kontrak")
                ) or clean(payload.get("Nomor Kontrak") or old_contract),
                "new_pi": clean(
                    (result or {}).get("new_pi")
                    if isinstance(result, dict)
                    else payload.get("Proforma Invoice No.")
                ) or clean(payload.get("Proforma Invoice No.") or old_pi),
            },
        })
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal update PI Module 1.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


@navigation_bp.route("/api/modul-1/pi/save-as", methods=["POST"])
@login_required
def api_modul_1_pi_save_as():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca."
        }), 403

    try:
        payload = request.get_json(silent=True) or {}
        insert_pi(payload)
        sync_pi_realization(
            clean(payload.get("Nomor Kontrak") or payload.get("nomor_kontrak")),
            clean(payload.get("Proforma Invoice No.") or payload.get("pi_no") or payload.get("pi")),
        )
        return jsonify({
            "success": True,
            "message": "PI baru berhasil dibuat dari Save As."
        }), 201
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal Save As PI Module 1.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


@navigation_bp.route("/api/modul-1/pi/delete", methods=["POST"])
@login_required
def api_modul_1_pi_delete():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca."
        }), 403

    payload = request.get_json(silent=True) or {}
    contract = clean(payload.get("nomor_kontrak"))
    pi = clean(payload.get("pi"))

    if not contract or not pi:
        return jsonify({
            "success": False,
            "message": "Kontrak dan PI wajib dipilih."
        }), 400

    try:
        delete_pi(contract, pi)
        return jsonify({
            "success": True,
            "message": "PI berhasil dihapus."
        })
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400
    except Exception as error:
        LOGGER.exception("Gagal menghapus PI Module 1.")
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500


# ============================================================


# ============================================================
# MODUL 2 SUPPORT HELPERS (MERGED)
# ============================================================

def muat_pi_master(nomor_kontrak=None, pi_no=None):
    """Mengambil master PI dari database_proforma_invoice.

    Gunakan loader Modul 1 agar pembacaan tetap kompatibel dengan schema lama
    maupun schema baru. Query sebelumnya menyebut kolom *_New secara langsung,
    sehingga Modul 2 gagal seluruhnya bila kolom tersebut belum dibuat di
    database produksi.
    """
    nomor_kontrak = nilai_bersih(nomor_kontrak)
    pi_no = nilai_bersih(pi_no)
    if not nomor_kontrak:
        return []

    rows, mapping, _ = load_rows({"nomor_kontrak": nomor_kontrak})
    field_names = {
        "Proforma Invoice No.": "pi_no",
        "Nomor Kontrak": "nomor_kontrak",
        "Nomor Tender": "nomor_tender",
        "Lingkup Pekerjaan": "lingkup_pekerjaan",
        "Tanggal Kontrak": "tanggal_kontrak",
        "Tanggal Berakhir Kontrak": "tanggal_berakhir_kontrak",
        "Jangka Waktu Kontrak": "jangka_waktu_kontrak",
        "Tanggal Performa Invoice": "tanggal_pi",
        "Judul Kontrak": "judul_kontrak",
        "Nomor Purchase Order": "nomor_po",
        "Tanggal Purchase Order": "tanggal_po",
        "Pihak Pertama": "pihak_pertama",
        "Alamat Pihak Pertama": "alamat_pihak_pertama",
        "Diwakili Oleh": "diwakili_oleh",
        "Selaku": "selaku",
        "Pihak Kedua": "pihak_kedua",
        "Alamat Pihak Kedua": "alamat_pihak_kedua",
        "Diwakili Oleh (P2)": "diwakili_oleh_p2",
        "Selaku (P2)": "selaku_p2",
        "Periode Pekerjaan Mulai": "periode_pekerjaan_mulai",
        "Periode Pekerjaan Selesai": "periode_pekerjaan_selesai",
        "Nomor WCC": "nomor_wcc",
        "Tanggal WCC": "tanggal_wcc",
        "Nomor WO": "nomor_wo",
        "Keterangan WO": "keterangan_wo",
        "Nomor CTR": "nomor_ctr",
        "Progress Pekerjaan": "progress_pekerjaan",
        "Prepared by Name": "prepared_by_name",
        "Prepared by Title": "prepared_by_title",
        "Approved by 1": "approved_by_1",
        "Approved by Title 1": "approved_by_title_1",
        "Approved by 2": "approved_by_2",
        "Approved by Title 2": "approved_by_title_2",
        "Jenis BASTP": "jenis_bastp",
    }

    result = []
    for row in rows:
        normalized = normalize_row(row, mapping)
        record = {
            target: normalized.get(source, "")
            for source, target in field_names.items()
        }
        if pi_no and nilai_bersih(record.get("pi_no")) != pi_no:
            continue
        result.append(normalisasi_record(record))

    result.sort(key=lambda row: nilai_bersih(row.get("pi_no")).lower())
    return result


def master_to_transaction_context(master):
    """Normalisasi master PI menjadi metadata yang dipakai transaksi/dokumen."""
    row = dict(master or {})
    return {
        "nomor_kontrak": row.get("nomor_kontrak", ""),
        "nama_kontrak": row.get("judul_kontrak", ""),
        "nomor_tender": row.get("nomor_tender", ""),
        "pi_no": row.get("pi_no", ""),
        "tanggal_pi": row.get("tanggal_pi", ""),
        "ditujukan_kepada": row.get("pihak_pertama", ""),
        "alamat_pihak_pertama": row.get("alamat_pihak_pertama", ""),
        "jangka_waktu_kontrak": row.get("jangka_waktu_kontrak", ""),
        "nomor_po": row.get("nomor_po", ""),
        "deskripsi_po": row.get("lingkup_pekerjaan", ""),
        "tanggal_po": row.get("tanggal_po", ""),
        "nomor_wo": row.get("nomor_wo", ""),
        "jenis_bastp": row.get("jenis_bastp", ""),
    }
    
    



# ============================================================
# DATABASE TRANSAKSI
# ============================================================

NAMA_TABEL_TRANSAKSI = "database_transaksi_rincian"

# Batas default data per request
DEFAULT_PER_PAGE = 50

# Batas maksimum yang boleh diminta browser
MAX_PER_PAGE = 100


def nilai_bersih(value):
    """
    Membersihkan nilai database agar aman dikirim sebagai JSON.
    """

    if value is None:
        return ""

    return str(value).strip()


def normalisasi_record(record):
    """
    Membersihkan seluruh field dalam satu record.
    """

    hasil = {}

    for key, value in record.items():
        hasil[str(key).strip()] = nilai_bersih(value)

    if {"kategori", "qty", "harga_satuan"}.issubset(hasil):
        hasil["total_harga"] = str(hitung_total_rincian(
            hasil.get("kategori"), hasil.get("qty"), hasil.get("harga_satuan"),
            hasil.get("percent") if hasil.get("percent") not in (None, "") else 100,
        ))

    return hasil


def muat_data_transaksi(page=1, per_page=DEFAULT_PER_PAGE, search=None):
    """Mengambil data transaksi untuk tabel umum Modul 2 dengan pagination."""
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    offset = (page - 1) * per_page
    search = str(search or '').strip()

    columns = """
        nomor_kontrak, nama_kontrak, nomor_tender, pi_no, tanggal_pi,
        ditujukan_kepada, alamat_pihak_pertama, jangka_waktu_kontrak,
        nomor_po, po_item_id, deskripsi_po, tanggal_po, mata_uang, kategori,
        deskripsi_pekerjaan, qty, unit, percent, tanggal_mulai,
        tanggal_selesai, harga_satuan, total_harga, bank_name,
        bank_branch, account_no, account_name, attn, keterangan,
        update_terakhir, items,
        COALESCE((
            SELECT `Jenis BASTP`
            FROM database_proforma_invoice p
            WHERE TRIM(CAST(p.`Nomor Kontrak` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.nomor_kontrak AS CHAR))
              AND TRIM(CAST(p.`Proforma Invoice No.` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.pi_no AS CHAR))
            LIMIT 1
        ), jenis_bastp) AS jenis_bastp,
        nomor_wan_sa, nomor_wo
    """
    query = f"SELECT {columns} FROM {NAMA_TABEL_TRANSAKSI}"
    params = []
    if search:
        query += " WHERE nomor_kontrak LIKE %s OR nama_kontrak LIKE %s OR nomor_tender LIKE %s OR pi_no LIKE %s OR nomor_po LIKE %s OR nomor_wo LIKE %s"
        keyword = f"%{search}%"
        params.extend([keyword] * 6)
    query += " ORDER BY update_terakhir DESC LIMIT %s OFFSET %s"
    params.extend([per_page, offset])

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                names = [column[0] for column in cursor.description]
                result = []
                for row in rows:
                    record = dict(row) if isinstance(row, dict) else dict(zip(names, row))
                    result.append(normalisasi_record(record))
                return result
            finally:
                cursor.close()
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil data transaksi.")
        return []


def muat_transaksi_terfilter(nomor_kontrak=None, pi_no=None):
    """Mengambil SELURUH rincian transaksi yang cocok, tanpa pagination."""
    nomor_kontrak = nilai_bersih(nomor_kontrak)
    pi_no = nilai_bersih(pi_no)
    if not nomor_kontrak:
        return []

    columns = """
        nomor_kontrak, nama_kontrak, nomor_tender, pi_no, tanggal_pi,
        ditujukan_kepada, alamat_pihak_pertama, jangka_waktu_kontrak,
        nomor_po, po_item_id, deskripsi_po, tanggal_po, mata_uang, kategori,
        deskripsi_pekerjaan, qty, unit, percent, tanggal_mulai,
        tanggal_selesai, harga_satuan, total_harga, bank_name,
        bank_branch, account_no, account_name, attn, keterangan,
        update_terakhir, items,
        COALESCE((
            SELECT `Jenis BASTP`
            FROM database_proforma_invoice p
            WHERE TRIM(CAST(p.`Nomor Kontrak` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.nomor_kontrak AS CHAR))
              AND TRIM(CAST(p.`Proforma Invoice No.` AS CHAR)) = TRIM(CAST(database_transaksi_rincian.pi_no AS CHAR))
            LIMIT 1
        ), jenis_bastp) AS jenis_bastp,
        nomor_wan_sa, nomor_wo
    """
    query = f"""
        SELECT {columns}
        FROM {NAMA_TABEL_TRANSAKSI}
        WHERE TRIM(CAST(nomor_kontrak AS CHAR)) = %s
    """
    params = [nomor_kontrak]
    if pi_no:
        query += """
            AND TRIM(CAST(pi_no AS CHAR)) = %s
        """
        params.append(pi_no)
    query += " ORDER BY kategori ASC, update_terakhir DESC"

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                names = [column[0] for column in cursor.description]
                result = []
                for row in rows:
                    record = dict(row) if isinstance(row, dict) else dict(zip(names, row))
                    result.append(normalisasi_record(record))
                return result
            finally:
                cursor.close()
    except Exception:
        LOGGER.exception("[MODUL 2] Gagal mengambil rincian transaksi.")
        raise

# ============================================================
# LOGIKA DOKUMEN
# ============================================================

def tentukan_dokumen(jenis_bastp):
    """Satu sumber kebenaran daftar dokumen Modul 2."""
    return build_document_manifest(jenis_bastp)

# MODUL 2
# ============================================================

@navigation_bp.route("/modul/invoice-dokumen")
@login_required
def invoice_dokumen():
    return _render_live_module(
        "dashboard.html",
        active_menu="modul-2",
        title="Modul 2: Invoice & Dokumen Turunan",
        module_code="MODUL 2",
    )

# ============================================================
# PARTIAL MODUL 2
# ============================================================

@navigation_bp.route(
    "/partial/modul-2"
)
@login_required
def partial_modul_2():

    return _render_live_module(
        "modul_2.html",
        title="Modul 2: Invoice & Dokumen Turunan",
        module_code="MODUL 2",
    )
# ============================================================
# API MODUL 2
# ============================================================

@navigation_bp.route(
    "/api/modul-2/kontrak",
    methods=["GET"]
)
@login_required
def modul_2_kontrak():
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT DISTINCT TRIM(CAST(`Nomor Kontrak` AS CHAR)) AS nomor_kontrak "
                    "FROM database_proforma_invoice "
                    "WHERE `Nomor Kontrak` IS NOT NULL "
                    "AND TRIM(CAST(`Nomor Kontrak` AS CHAR)) <> '' "
                    "ORDER BY `Nomor Kontrak` ASC"
                )
                rows = cursor.fetchall()
                kontrak_list = []
                for row in rows:
                    value = row.get("nomor_kontrak") if isinstance(row, dict) else row[0]
                    value = nilai_bersih(value)
                    if value and value not in kontrak_list:
                        kontrak_list.append(value)
            finally:
                cursor.close()
        return jsonify({"success": True, "data": kontrak_list, "total": len(kontrak_list)})
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil daftar kontrak dari master PI.")
        return jsonify({"success": False, "message": str(error), "data": [], "total": 0}), 500


# ============================================================
# API PI BERDASARKAN KONTRAK
# ============================================================

@navigation_bp.route(
    "/api/modul-2/pi",
    methods=["GET"]
)
@login_required
def modul_2_pi():
    nomor_kontrak = nilai_bersih(request.args.get("nomor_kontrak", ""))
    if not nomor_kontrak:
        return jsonify({"success": False, "message": "Nomor kontrak belum dipilih.", "data": []}), 400
    try:
        master_rows = muat_pi_master(nomor_kontrak)
        pi_list = sorted({nilai_bersih(row.get("pi_no")) for row in master_rows if nilai_bersih(row.get("pi_no"))}, key=lambda x: x.lower())
        return jsonify({"success": True, "nomor_kontrak": nomor_kontrak, "data": pi_list, "total": len(pi_list)})
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil daftar PI dari master PI.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


# ============================================================
# API DETAIL TRANSAKSI
# ============================================================

@navigation_bp.route(
    "/api/modul-2/detail",
    methods=["GET"]
)
@login_required
def modul_2_detail():
    nomor_kontrak = nilai_bersih(request.args.get("nomor_kontrak", ""))
    pi_no = nilai_bersih(request.args.get("pi", ""))
    if not nomor_kontrak:
        return jsonify({"success": False, "message": "Nomor kontrak belum dipilih."}), 400
    if not pi_no:
        return jsonify({"success": False, "message": "PI belum dipilih."}), 400

    try:
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        if not master_rows:
            return jsonify({"success": False, "message": "Data PI tidak ditemukan di master PI.", "data": []}), 404

        master = master_rows[0]
        target = muat_transaksi_terfilter(nomor_kontrak, pi_no)
        jenis_bastp = nilai_bersih(master.get("jenis_bastp"))
        dokumen = tentukan_dokumen(jenis_bastp)

        # Bila belum ada detail transaksi, PI tetap valid dan editor Modul 2
        # akan dibuka dalam kondisi kosong.
        return jsonify({
            "success": True,
            "nomor_kontrak": nomor_kontrak,
            "pi": pi_no,
            "jenis_bastp": jenis_bastp,
            "master_pi": master,
            "document_info": dokumen,
            "transaction_count": len(target),
            "data": target,
        })
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil detail PI.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


# ============================================================
# API MASTER BANK MODUL 2
# ============================================================

@navigation_bp.route(
    "/api/modul-2/banks",
    methods=["GET", "POST"]
)
@login_required
def modul_2_banks():
    """
    Master rekening bank Modul 2.

    Sumber awal diambil dari database_transaksi_rincian agar rekening lama
    langsung tersedia. Rekening baru disimpan pada JSON kecil di folder
    modul_navigasi sehingga tidak memerlukan perubahan struktur database.
    """
    master_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "database_master_bank.json",
    )

    def load_json_master():
        try:
            if not os.path.exists(master_file):
                return []
            import json
            with open(master_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except Exception:
            LOGGER.exception("[MODUL 2] Gagal membaca master bank JSON.")
            return []

    def save_json_master(rows):
        import json
        temp_file = master_file + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        os.replace(temp_file, master_file)

    if request.method == "POST":
        if not can_write():
            return jsonify({
                "success": False,
                "message": "Role Anda hanya memiliki akses baca.",
            }), 403

        payload = request.get_json(silent=True) or {}
        record = {
            "bank_name": nilai_bersih(payload.get("bank_name")),
            "bank_branch": nilai_bersih(payload.get("bank_branch")),
            "account_no": nilai_bersih(payload.get("account_no")),
            "account_name": nilai_bersih(payload.get("account_name")),
            "attn": nilai_bersih(payload.get("attn")),
        }
        if not record["bank_name"] or not record["account_no"]:
            return jsonify({
                "success": False,
                "message": "Nama Bank dan Nomor Rekening wajib diisi.",
            }), 400

        rows = load_json_master()
        normalized_account = record["account_no"].replace(" ", "").lower()
        replaced = False
        for index, existing in enumerate(rows):
            same_bank = nilai_bersih(existing.get("bank_name")).lower() == record["bank_name"].lower()
            same_account = nilai_bersih(existing.get("account_no")).replace(" ", "").lower() == normalized_account
            if same_bank and same_account:
                rows[index] = record
                replaced = True
                break
        if not replaced:
            rows.append(record)

        try:
            save_json_master(rows)
        except Exception as error:
            LOGGER.exception("[MODUL 2] Gagal menyimpan master bank JSON.")
            return jsonify({
                "success": False,
                "message": f"Master Bank tidak dapat disimpan: {error}",
            }), 500

    try:
        db_rows = []
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""
                    SELECT DISTINCT
                        TRIM(CAST(bank_name AS CHAR)) AS bank_name,
                        TRIM(CAST(bank_branch AS CHAR)) AS bank_branch,
                        TRIM(CAST(account_no AS CHAR)) AS account_no,
                        TRIM(CAST(account_name AS CHAR)) AS account_name,
                        TRIM(CAST(attn AS CHAR)) AS attn
                    FROM {NAMA_TABEL_TRANSAKSI}
                    WHERE bank_name IS NOT NULL
                      AND TRIM(CAST(bank_name AS CHAR)) <> ''
                      AND account_no IS NOT NULL
                      AND TRIM(CAST(account_no AS CHAR)) <> ''
                    ORDER BY bank_name ASC, account_no ASC
                    """
                )
                rows = cursor.fetchall()
                names = [column[0] for column in cursor.description]
                for row in rows:
                    db_rows.append(
                        dict(row) if isinstance(row, dict) else dict(zip(names, row))
                    )
            finally:
                cursor.close()

        combined = []
        seen = set()
        for source in db_rows + load_json_master():
            record = {
                "bank_name": nilai_bersih(source.get("bank_name")),
                "bank_branch": nilai_bersih(source.get("bank_branch")),
                "account_no": nilai_bersih(source.get("account_no")),
                "account_name": nilai_bersih(source.get("account_name")),
                "attn": nilai_bersih(source.get("attn")),
            }
            if not record["bank_name"] or not record["account_no"]:
                continue
            key = (
                record["bank_name"].lower(),
                record["account_no"].replace(" ", "").lower(),
            )
            if key in seen:
                # Master JSON adalah sumber terakhir; izinkan datanya menggantikan
                # snapshot transaksi yang lebih lama.
                for i, existing in enumerate(combined):
                    existing_key = (
                        existing["bank_name"].lower(),
                        existing["account_no"].replace(" ", "").lower(),
                    )
                    if existing_key == key:
                        combined[i] = record
                        break
                continue
            seen.add(key)
            combined.append(record)

        combined.sort(key=lambda x: (x["bank_name"].lower(), x["account_no"]))
        return jsonify({
            "success": True,
            "data": combined,
            "total": len(combined),
        })
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil daftar master bank.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": [],
            "total": 0,
        }), 500


# ============================================================
# API UPDATE RINCIAN PI
# ============================================================

@navigation_bp.route(
    "/api/modul-2/update",
    methods=["PUT", "POST"]
)
@login_required
def modul_2_update():
    """Insert/update seluruh rincian transaksi untuk satu PI."""
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403

    payload = request.get_json(silent=True) or {}
    nomor_kontrak = nilai_bersih(payload.get("nomor_kontrak"))
    pi_no = nilai_bersih(payload.get("pi"))
    header = payload.get("header") or {}
    items = payload.get("rows") or []

    if not nomor_kontrak or not pi_no:
        return jsonify({"success": False, "message": "Nomor kontrak dan PI wajib diisi."}), 400
    if not isinstance(items, list):
        return jsonify({"success": False, "message": "Format rincian transaksi tidak valid."}), 400

    try:
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        if not master_rows:
            return jsonify({"success": False, "message": "PI tidak ditemukan di database_proforma_invoice."}), 404
        master = master_rows[0]

        filtered_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kategori = nilai_bersih(item.get("kategori"))
            deskripsi = nilai_bersih(item.get("deskripsi_pekerjaan"))
            if kategori or deskripsi:
                filtered_items.append(item)

        # PI tanpa rincian transaksi tetap boleh disimpan sebagai PI master;
        # Jenis BASTP sudah ditetapkan lebih dulu dari Modul 1.

        allowed_header = {
            "nomor_po", "tanggal_po", "nomor_wo", "nomor_wan_sa", "mata_uang",
            "bank_name", "bank_branch", "account_no", "account_name", "attn",
            "deskripsi_po"
        }
        header = {k: nilai_bersih(v) for k, v in header.items() if k in allowed_header}

        columns = [
            "nomor_kontrak", "nama_kontrak", "nomor_tender", "pi_no", "tanggal_pi",
            "ditujukan_kepada", "alamat_pihak_pertama", "jangka_waktu_kontrak",
            "nomor_po", "po_item_id", "deskripsi_po", "tanggal_po", "mata_uang", "kategori",
            "deskripsi_pekerjaan", "qty", "unit", "percent", "tanggal_mulai",
            "tanggal_selesai", "harga_satuan", "total_harga", "bank_name", "bank_branch",
            "account_no", "account_name", "attn", "keterangan", "update_terakhir",
            "items", "jenis_bastp", "nomor_wan_sa", "nomor_wo"
        ]
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = f"INSERT INTO {NAMA_TABEL_TRANSAKSI} ({', '.join(columns)}) VALUES ({placeholders})"

        # Persentase sekarang dapat berbeda untuk setiap rincian.
        # Header tetap menjadi fallback apabila baris tidak memiliki percent.
        try:
            global_percent = float(header.get("percent") or payload.get("percent") or 100)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Persentase tagihan harus berupa angka."}), 400
        if global_percent < 0 or global_percent > 100:
            return jsonify({"success": False, "message": "Persentase tagihan harus berada di antara 0 dan 100%."}), 400

        # Snapshot master PI menjadi sumber metadata transaksi.
        base = master_to_transaction_context(master)

        # Jenis BASTP adalah master PI dan hanya boleh diubah dari Modul 1.
        # Modul 2 selalu menggunakan nilai terbaru dari master tersebut.
        jenis_bastp = nilai_bersih(master.get("jenis_bastp"))
        if jenis_bastp not in {
            "Pekerjaan Jasa",
            "Pekerjaan Barang / Material",
            "Pekerjaan Gabungan (Barang & Jasa)",
        }:
            return jsonify({
                "success": False,
                "message": "Jenis BASTP pada master PI belum dipilih atau tidak valid."
            }), 400

        waktu_aksi = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Formula legacy: provisional subtotal x 115%; estimated x 90%; normal x 100%,
        # seluruhnya dikalikan persentase tagihan.
        def num(value):
            try:
                text = str(value or "").strip().replace("Rp", "").replace(" ", "")
                if text.count(".") > 0 and text.count(",") == 1 and text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                elif text.count(".") > 1 and text.count(",") == 0:
                    text = text.replace(".", "")
                elif text.count(",") > 1 and text.count(".") == 0:
                    text = text.replace(",", "")
                else:
                    text = text.replace(",", "")
                return float(text or 0)
            except (TypeError, ValueError):
                return 0.0

        def category_type(item):
            c = nilai_bersih(item.get("kategori")).lower()
            return (
                "provisional" if "provisional" in c or "professional" in c
                else "estimated" if "estimated" in c or "estimasi" in c
                else "normal"
            )

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                # Hapus snapshot transaksi lama lalu masukkan versi editor.
                cursor.execute(
                    f"DELETE FROM {NAMA_TABEL_TRANSAKSI} WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s AND TRIM(CAST(pi_no AS CHAR))=%s",
                    (nomor_kontrak, pi_no)
                )

                for index, item in enumerate(filtered_items):
                    kategori = nilai_bersih(item.get("kategori"))
                    deskripsi = nilai_bersih(item.get("deskripsi_pekerjaan"))
                    qty = num(item.get("qty"))
                    harga = num(item.get("harga_satuan"))

                    # Percent tiap baris bebas berbeda. Jika kosong, gunakan
                    # persentase header sebagai fallback.
                    try:
                        item_percent = float(item.get("percent")) if str(item.get("percent") or "").strip() != "" else global_percent
                    except (TypeError, ValueError):
                        return jsonify({
                            "success": False,
                            "message": f"Persentase pada rincian ke-{index + 1} harus berupa angka."
                        }), 400

                    if item_percent < 0 or item_percent > 100:
                        return jsonify({
                            "success": False,
                            "message": f"Persentase pada rincian ke-{index + 1} harus berada di antara 0 dan 100%."
                        }), 400

                    # Mapping Master PO bersifat eksplisit.
                    # Jika frontend mengirim po_item_id, pastikan item tersebut
                    # memang milik kontrak + nomor PO yang sama dan masih ACTIVE.
                    raw_po_item_id = nilai_bersih(item.get("po_item_id"))
                    po_item_id = None

                    row_nomor_po = nilai_bersih(
                        header.get("nomor_po", master.get("nomor_po", ""))
                    )

                    if raw_po_item_id:
                        try:
                            po_item_id_candidate = int(raw_po_item_id)
                        except (TypeError, ValueError):
                            return jsonify({
                                "success": False,
                                "message": f"Item Master PO pada rincian ke-{index + 1} tidak valid."
                            }), 400

                        if po_item_id_candidate <= 0:
                            return jsonify({
                                "success": False,
                                "message": f"Item Master PO pada rincian ke-{index + 1} tidak valid."
                            }), 400

                        with get_db_connection() as po_conn:
                            po_cur = po_conn.cursor()
                            try:
                                po_cur.execute(
                                    """
                                    SELECT i.id
                                    FROM database_master_po_item i
                                    JOIN database_master_po h ON h.id = i.po_id
                                    WHERE i.id = %s
                                      AND i.status_item = 'ACTIVE'
                                      AND TRIM(CAST(h.nomor_kontrak AS CHAR)) = %s
                                      AND TRIM(CAST(h.nomor_po AS CHAR)) = %s
                                    LIMIT 1
                                    """,
                                    (
                                        po_item_id_candidate,
                                        nomor_kontrak,
                                        row_nomor_po,
                                    )
                                )
                                matched = po_cur.fetchone()
                            finally:
                                po_cur.close()

                        if not matched:
                            return jsonify({
                                "success": False,
                                "message": (
                                    f"Item Master PO #{po_item_id_candidate} pada "
                                    f"rincian ke-{index + 1} tidak cocok dengan "
                                    f"Kontrak {nomor_kontrak} / PO {row_nomor_po}."
                                )
                            }), 400

                        po_item_id = po_item_id_candidate

                    tipe = category_type(item)
                    if tipe == "provisional":
                        # Setiap baris Provisional Sum adalah rincian berbeda
                        # dan memiliki keterangan/nominal sendiri. Fee 15%
                        # dihitung per baris, bukan digabung ke baris pertama.
                        total_harga = qty * harga * 1.15 * (item_percent / 100.0)
                    elif tipe == "estimated":
                        total_harga = qty * harga * 0.90 * (item_percent / 100.0)
                    else:
                        total_harga = qty * harga * (item_percent / 100.0)

                    values = [
                        nomor_kontrak, master.get("judul_kontrak", ""), master.get("nomor_tender", ""),
                        pi_no, master.get("tanggal_pi", ""), master.get("pihak_pertama", ""),
                        master.get("alamat_pihak_pertama", ""), master.get("jangka_waktu_kontrak", ""),
                        header.get("nomor_po", master.get("nomor_po", "")),
                        po_item_id,
                        header.get("deskripsi_po", master.get("lingkup_pekerjaan", "")),
                        header.get("tanggal_po", master.get("tanggal_po", "")),
                        header.get("mata_uang", "IDR"), kategori, deskripsi, qty,
                        nilai_bersih(item.get("unit")), item_percent,
                        nilai_bersih(item.get("tanggal_mulai")), nilai_bersih(item.get("tanggal_selesai")),
                        harga, total_harga, header.get("bank_name", ""), header.get("bank_branch", ""),
                        header.get("account_no", ""), header.get("account_name", ""), header.get("attn", ""),
                        nilai_bersih(item.get("keterangan")), waktu_aksi, "",
                        jenis_bastp, header.get("nomor_wan_sa", ""), header.get("nomor_wo", master.get("nomor_wo", "")),
                    ]
                    cursor.execute(insert_sql, tuple(values))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        # Sinkronkan ledger PO setelah snapshot rincian PI berhasil disimpan.
        sync_pi_realization(nomor_kontrak, pi_no)

        try:
            remap_unmapped_realizations(nomor_kontrak)
        except Exception:
            LOGGER.exception("Gagal melakukan remap histori PO setelah update Modul 2.")

        message = (
            f"Master PI {pi_no} berhasil diperbarui."
            if not filtered_items
            else f"Rincian PI {pi_no} berhasil disimpan."
        )
        return jsonify({
            "success": True,
            "message": message,
            "nomor_kontrak": nomor_kontrak,
            "pi": pi_no,
            "jenis_bastp": jenis_bastp,
            "transaction_count": len(filtered_items),
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal memperbarui rincian PI.")
        return jsonify({"success": False, "message": str(error)}), 500


# ============================================================
# API DAFTAR DOKUMEN
# ============================================================

@navigation_bp.route(
    "/api/modul-2/documents",
    methods=["GET"]
)
@login_required
def modul_2_documents():
    nomor_kontrak = nilai_bersih(request.args.get("nomor_kontrak", ""))
    pi_no = nilai_bersih(request.args.get("pi", ""))
    if not nomor_kontrak or not pi_no:
        return jsonify({"success": False, "message": "Kontrak dan PI wajib dipilih.", "data": []}), 400
    try:
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        if not master_rows:
            return jsonify({"success": False, "message": "Data PI tidak ditemukan.", "data": []}), 404
        info = tentukan_dokumen(master_rows[0].get("jenis_bastp", ""))
        target = muat_transaksi_terfilter(nomor_kontrak, pi_no)
        return jsonify({
            "success": True,
            "nomor_kontrak": nomor_kontrak,
            "pi": pi_no,
            "data": info.get("documents", []),
            "document_info": info,
            "transaction_count": len(target),
        })
    except Exception as error:
        LOGGER.exception("[MODUL 2] Gagal mengambil daftar dokumen.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route(
    "/api/modul-2/document/<code>",
    methods=["GET", "POST"]
)
@login_required
def modul_2_open_document(code):
    """
    Endpoint generik dokumen Modul 2.

    Untuk TKDN, endpoint ini juga menjadi satu-satunya API path yang dipakai
    editor Flask:
      GET  /api/modul-2/document/tkdn                 -> load data/default
      POST /api/modul-2/document/tkdn?action=save     -> validasi + simpan
      POST /api/modul-2/document/tkdn?action=preview  -> preview HTML
      GET  /api/modul-2/document/tkdn?action=download -> download final

    Dokumen Modul 2 lain tetap menggunakan perilaku generik sebelumnya.
    """
    code = nilai_bersih(code).lower()

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
    else:
        payload = {}

    nomor_kontrak = nilai_bersih(
        payload.get("nomor_kontrak", request.args.get("nomor_kontrak", ""))
    )
    pi_no = nilai_bersih(
        payload.get("pi", request.args.get("pi", request.args.get("pi_no", "")))
    )

    if not nomor_kontrak or not pi_no:
        return jsonify({
            "success": False,
            "message": "Nomor kontrak dan PI wajib dipilih.",
        }), 400

    try:
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        if not master_rows:
            return jsonify({
                "success": False,
                "message": "Data PI tidak ditemukan di master PI."
            }), 404

        master = master_rows[0]

        # --------------------------------------------------------
        # TKDN: satu endpoint untuk load / save / preview / download
        # --------------------------------------------------------
        if code == "tkdn":
            action = nilai_bersih(
                request.args.get("action", "")
            ).lower()

            # GET default/load parameter TKDN.
            if request.method == "GET" and action != "download":
                from modul_dokumen.tkdn import get_tkdn_defaults

                saved = _tkdn_load_saved(nomor_kontrak, pi_no)
                data = saved if saved else get_tkdn_defaults()

                transaksi = muat_transaksi_terfilter(nomor_kontrak, pi_no)
                total_tagihan = sum(
                    float(row.get("total_harga") or 0)
                    for row in transaksi
                )

                if not data.get("total_tagihan"):
                    data["total_tagihan"] = total_tagihan

                data["pi_no"] = pi_no
                data["nomor_kontrak"] = nomor_kontrak
                data["nomor_po"] = nilai_bersih(
                    master.get("nomor_po")
                    or master.get("Nomor Purchase Order")
                )

                return jsonify({
                    "success": True,
                    "data": data,
                })

            # GET download parameter terakhir yang tersimpan.
            if request.method == "GET" and action == "download":
                saved = _tkdn_load_saved(nomor_kontrak, pi_no)
                if not saved:
                    return jsonify({
                        "success": False,
                        "message": "Parameter TKDN belum disimpan. Edit dan simpan TKDN terlebih dahulu.",
                    }), 400

                transaksi = muat_transaksi_terfilter(nomor_kontrak, pi_no)
                from modul_dokumen.tkdn import generate_tkdn_html

                html = generate_tkdn_html(
                    transaksi_list=transaksi,
                    nomor_kontrak=nomor_kontrak,
                    pi_no=pi_no,
                    params=saved,
                )

                filename = (
                    f"TKDN_{nomor_kontrak}_{pi_no}"
                    .replace("/", "_")
                    .replace("\\", "_")
                    .replace(" ", "_")
                ) + ".html"

                response = make_response(html)
                response.headers["Content-Type"] = "text/html; charset=utf-8"
                response.headers["Content-Disposition"] = (
                    f'attachment; filename="{filename}"'
                )
                return response

            # POST save / preview.
            action = action or "preview"

            if action in {"save", "preview"}:
                from modul_dokumen.tkdn import (
                    normalize_tkdn_params,
                    validate_tkdn_params,
                )

                body_contract = nilai_bersih(payload.get("nomor_kontrak", ""))
                if body_contract and body_contract != nomor_kontrak:
                    return jsonify({
                        "success": False,
                        "message": "Nomor kontrak pada body tidak sama dengan nomor kontrak pada URL/query API.",
                    }), 400

                params = normalize_tkdn_params(payload)
                valid, errors = validate_tkdn_params(params)

                if not valid:
                    return jsonify({
                        "success": False,
                        "message": "Parameter TKDN belum valid.",
                        "errors": errors,
                    }), 400

                if action == "save":
                    _tkdn_save_saved(
                        nomor_kontrak,
                        pi_no,
                        params,
                    )

                    return jsonify({
                        "success": True,
                        "message": f"Parameter TKDN PI {pi_no} berhasil disimpan.",
                        "data": params,
                    })

                transaksi = muat_transaksi_terfilter(nomor_kontrak, pi_no)
                from modul_dokumen.tkdn import generate_tkdn_html

                html = generate_tkdn_html(
                    transaksi_list=transaksi,
                    nomor_kontrak=nomor_kontrak,
                    pi_no=pi_no,
                    params=params,
                )

                return jsonify({
                    "success": True,
                    "html": html,
                })

            return jsonify({
                "success": False,
                "message": f"Action TKDN tidak dikenal: {action}",
            }), 400

        # --------------------------------------------------------
        # Dokumen route-only / batch package
        # --------------------------------------------------------
        if code == "opname":
            return jsonify({
                "success": True,
                "mode": "route",
                "code": "opname",
                "url": (
                    "/opname/?nomor_kontrak="
                    + nomor_kontrak
                    + "&nomor_pi="
                    + pi_no
                ),
            })

        if code == "paket-dokumen":
            target = enrich_transactions(
                muat_transaksi_terfilter(nomor_kontrak, pi_no),
                nomor_kontrak,
                pi_no,
            )

            # Master Paket menggabungkan generator dan dokumen route.
            saved_tkdn = load_saved_tkdn(nomor_kontrak, pi_no)

            nomor_po = nilai_bersih(
                master.get("nomor_po")
                or master.get("Nomor Purchase Order")
                or payload.get("nomor_po")
                or (target[0].get("nomor_po") if target else "")
                or (target[0].get("Nomor PO") if target else "")
            )
            saved_opname = load_opname_for_package(
                nomor_kontrak,
                pi_no,
                nomor_po,
                master_pi=master,
            )

            package_config = load_config(nomor_kontrak, pi_no)
            document_params = {
                "main_date": nilai_bersih(payload.get("main_date")),
                "lokasi": nilai_bersih(payload.get("lokasi")),
                "catatan": nilai_bersih(payload.get("catatan")),
                "logo_1": payload.get("logo_1") or package_config.get("logo_1"),
                "logo_2": payload.get("logo_2") or package_config.get("logo_2"),
                "logo_3": payload.get("logo_3") or package_config.get("logo_3"),
                "ttd_1": payload.get("ttd_1") or package_config.get("ttd_1"),
                "ttd_2": payload.get("ttd_2") or package_config.get("ttd_2"),
                "ttd_3": payload.get("ttd_3") or package_config.get("ttd_3"),
                "dates": package_config.get("dates", {}),
                "items": payload.get("items") or {},
                "bamp_items": payload.get("bamp_items") or package_config.get("bamp_items") or {},
                "jenis_bastp": nilai_bersih(master.get("jenis_bastp")),
                "master_pi": master,
            }

            manifest_codes = [
                item.get("code")
                for item in tentukan_dokumen(
                    master.get("jenis_bastp", "")
                ).get("documents", [])
                if item.get("code")
                and item.get("available", True)
                and item.get("code") != "paket-dokumen"
            ]
            selected_codes = set(package_config.get("selected_documents") or PACKAGE_ORDER)

            # Pertahankan urutan resmi PACKAGE_ORDER.
            codes = [
                code_item
                for code_item in PACKAGE_ORDER
                if code_item in manifest_codes and code_item in selected_codes
            ]

            # TKDN dan Opname adalah mode=route; hanya masuk bila sudah
            # memiliki data tersimpan.
            if saved_tkdn and "tkdn" in selected_codes and "tkdn" not in codes:
                codes.append("tkdn")
            if saved_opname and "opname" in selected_codes and "opname" not in codes:
                codes.append("opname")

            if not saved_tkdn and "tkdn" in codes:
                codes.remove("tkdn")
            if not saved_opname and "opname" in codes:
                codes.remove("opname")

            if not codes:
                raise ValueError(
                    "Master Paket tidak memiliki dokumen yang dapat dicetak."
                )

            html = render_document_package(
                codes=codes,
                transaksi_list=target,
                nomor_kontrak=nomor_kontrak,
                pi_no=pi_no,
                nomor_po=nomor_po,
                params=document_params,
                render_generator=render_document,
                saved_tkdn=saved_tkdn,
                saved_opname=saved_opname,
            )
            return html, 200, {
                "Content-Type": "text/html; charset=utf-8"
            }

        # --------------------------------------------------------
        # Dokumen Modul 2 selain TKDN: perilaku generik lama
        # --------------------------------------------------------
        target = muat_transaksi_terfilter(nomor_kontrak, pi_no)
        if not target:
            return jsonify({
                "success": False,
                "message": "PI belum memiliki rincian transaksi. Simpan rincian pekerjaan terlebih dahulu.",
            }), 400

        document_params = {
            "main_date": nilai_bersih(payload.get("main_date")),
            "lokasi": nilai_bersih(payload.get("lokasi")),
            "inputter": nilai_bersih(payload.get("inputter")),
            "jabatan_inputter": nilai_bersih(
                payload.get("jabatan_inputter")
            ),
            "catatan": nilai_bersih(payload.get("catatan")),
            "logo_1": payload.get("logo_1"),
            "logo_2": payload.get("logo_2"),
            "ttd_1": payload.get("ttd_1"),
            "ttd_2": payload.get("ttd_2"),
            "items": payload.get("items") or {},
            "jenis_bastp": nilai_bersih(master.get("jenis_bastp")),
            "master_pi": master,
        }

        html = render_document(
            code=code,
            transaksi_list=target,
            nomor_kontrak=nomor_kontrak,
            pi_no=pi_no,
            params=document_params,
        )

        return html, 200, {
            "Content-Type": "text/html; charset=utf-8"
        }

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        LOGGER.exception(
            "[MODUL 2] Gagal membuka dokumen %s.",
            code,
        )
        return jsonify({
            "success": False,
            "message": str(error),
        }), 500



# ============================================================
# TKDN - FLASK EDITOR / PREVIEW / SAVE / DOWNLOAD
# ============================================================

TKDN_TABLE = "database_tkdn"


def _tkdn_ensure_table(cursor):
    """
    Menyiapkan tabel penyimpanan parameter TKDN per PI.

    Parameter disimpan sebagai JSON agar struktur field TKDN dapat
    berkembang tanpa mengubah struktur tabel transaksi.
    """
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TKDN_TABLE} (
            id BIGINT NOT NULL AUTO_INCREMENT,
            nomor_kontrak VARCHAR(255) NOT NULL,
            pi_no VARCHAR(255) NOT NULL,
            params_json LONGTEXT NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_tkdn_kontrak_pi (nomor_kontrak, pi_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _tkdn_load_saved(nomor_kontrak, pi_no):
    """
    Mengambil parameter TKDN tersimpan untuk kombinasi kontrak + PI.
    """
    with get_db_connection() as connection:
        cursor = connection.cursor()
        _tkdn_ensure_table(cursor)

        cursor.execute(
            f"""
            SELECT params_json
            FROM {TKDN_TABLE}
            WHERE nomor_kontrak = %s
              AND pi_no = %s
            LIMIT 1
            """,
            (nomor_kontrak, pi_no),
        )

        row = cursor.fetchone()

    if not row:
        return {}

    raw = row[0] if isinstance(row, (tuple, list)) else row.get("params_json")
    if not raw:
        return {}

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        LOGGER.exception("[TKDN] JSON parameter rusak untuk PI %s.", pi_no)
        return {}


def _tkdn_save_saved(nomor_kontrak, pi_no, params):
    """
    Menyimpan parameter TKDN per kontrak + PI.
    """
    serialized = json.dumps(
        params,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with get_db_connection() as connection:
        cursor = connection.cursor()
        _tkdn_ensure_table(cursor)

        cursor.execute(
            f"""
            INSERT INTO {TKDN_TABLE}
                (nomor_kontrak, pi_no, params_json)
            VALUES
                (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                params_json = VALUES(params_json),
                updated_at = CURRENT_TIMESTAMP
            """,
            (nomor_kontrak, pi_no, serialized),
        )

        try:
            connection.commit()
        except AttributeError:
            # Be compatible with connection wrappers that autocommit.
            pass


def _tkdn_master_context(nomor_kontrak, pi_no):
    """
    Mengambil master PI + transaksi sebagai sumber dokumen TKDN.
    """
    master_rows = muat_pi_master(nomor_kontrak, pi_no)

    if not master_rows:
        raise ValueError("Data PI tidak ditemukan.")

    transaksi = muat_transaksi_terfilter(nomor_kontrak, pi_no)

    return master_rows[0], transaksi


def _resolve_tkdn_contract(pi_no, candidate=""):
    """
    Legacy helper untuk kompatibilitas internal. Endpoint TKDN aktif sekarang
    menggunakan pasangan nomor kontrak + PI langsung pada URL API, sehingga
    tidak ada lagi proses menebak nomor kontrak dari PI.
    """
    pi_no = nilai_bersih(pi_no)
    candidate = nilai_bersih(candidate)

    if pi_no:
        query = (
            "SELECT `Nomor Kontrak` AS nomor_kontrak "
            "FROM database_proforma_invoice "
            "WHERE TRIM(CAST(`Proforma Invoice No.` AS CHAR)) = %s "
            "AND TRIM(CAST(`Nomor Kontrak` AS CHAR)) <> '' "
            "ORDER BY `Nomor Kontrak` ASC LIMIT 1"
        )

        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(query, (pi_no,))
            row = cursor.fetchone()
            if row:
                if isinstance(row, dict):
                    resolved = nilai_bersih(row.get("nomor_kontrak"))
                else:
                    resolved = nilai_bersih(row[0])
                if resolved:
                    return resolved
        finally:
            cursor.close()
            try:
                connection.close()
            except Exception:
                pass

    return candidate


@navigation_bp.route(
    "/modul/invoice-dokumen/tkdn",
    methods=["GET"],
)
@login_required
def tkdn_editor_page():
    """
    Halaman editor TKDN Flask.
    """
    pi_no = nilai_bersih(request.args.get("pi", request.args.get("pi_no", "")))
    nomor_kontrak = nilai_bersih(request.args.get("nomor_kontrak", ""))

    if not nomor_kontrak or not pi_no:
        return jsonify({
            "success": False,
            "message": "Nomor kontrak dan PI wajib dipilih.",
        }), 400

    try:
        master, transaksi = _tkdn_master_context(nomor_kontrak, pi_no)

        total_tagihan = sum(
            float(row.get("total_harga") or 0)
            for row in transaksi
        )
        if not total_tagihan:
            try:
                total_tagihan = float(master.get("total_harga") or 0)
            except (TypeError, ValueError):
                total_tagihan = 0.0

        return render_template(
            "tkdn_editor.html",
            title="Editor TKDN",
            module_code="MODUL 2",
            nomor_kontrak=nomor_kontrak,
            pi_no=pi_no,
            nomor_po=nilai_bersih(
                master.get("nomor_po")
                or master.get("Nomor Purchase Order")
            ),
            total_tagihan=total_tagihan,
        )

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 404

    except Exception as error:
        LOGGER.exception("[TKDN] Gagal membuka editor TKDN.")
        return jsonify({"success": False, "message": str(error)}), 500




# ============================================================
# MODUL 3
# ============================================================

def _invoice_json_value(value):
    """Membuat nilai MySQL invoice aman dikirim sebagai JSON."""
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _invoice_json_row(row):
    return {key: _invoice_json_value(value) for key, value in dict(row).items()}


def _finance_archive_dir():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    path = os.path.join(root, "database_penyimpanan_aman", "arsip_invoice_tax")
    os.makedirs(path, exist_ok=True)
    return path


def _record_finance_audit(action, entity_type, entity_id=None, detail=None):
    """Audit best-effort; kegagalan audit tidak boleh membatalkan transaksi utama."""
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO database_invoice_tax_audit_log
                    (actor_username, actor_role, action, entity_type, entity_id, detail)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(session.get("current_user", "") or "")[:150] or None,
                        str(session.get("current_role", "") or "")[:80] or None,
                        action,
                        entity_type,
                        entity_id,
                        json.dumps(detail or {}, ensure_ascii=False),
                    ),
                )
                connection.commit()
            finally:
                cursor.close()
    except Exception:
        LOGGER.warning("Audit finance gagal dicatat: %s", action, exc_info=True)


def _invoice_decimal(payload, key, default="0"):
    value = payload.get(key, default)
    if value in (None, ""):
        return Decimal("0")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Field {key} harus berupa angka.") from error
    if parsed < 0:
        raise ValueError(f"Field {key} tidak boleh negatif.")
    return parsed


def _invoice_date(payload, key):
    value = payload.get(key)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as error:
        raise ValueError(f"Field {key} harus memakai format YYYY-MM-DD.") from error


def _invoice_bool(payload, key, default=False):
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _invoice_money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _invoice_calculate_values(data):
    """Kalkulasi resmi invoice menggunakan Decimal dan pembulatan bisnis."""
    use_professional = bool(data["gunakan_professional_sum"])
    use_estimated = bool(data["gunakan_estimasi_sum"])
    at_cost = data["add_cost"]
    management_fee = data["management_fee"]
    gross_input = data["nilai_gross"]

    if use_professional:
        gross = at_cost + management_fee
        discount = Decimal("0")
    elif use_estimated:
        gross = gross_input
        discount = _invoice_money(gross * Decimal("0.10"))
    else:
        gross = gross_input
        discount = Decimal("0")

    dpp = _invoice_money(gross - discount)
    ppn = _invoice_money(dpp * Decimal("0.11")) if data["kena_ppn"] else Decimal("0")
    pph_base = management_fee if use_professional else dpp
    pph = _invoice_money(pph_base * data["tarif_pph"] / Decimal("100")) if data["kena_pph"] else Decimal("0")
    total_netto = _invoice_money(dpp + ppn - pph)

    return {
        "nilai_gross": _invoice_money(gross),
        "diskon_nominal": _invoice_money(discount),
        "nilai_dpp": dpp,
        "ppn_nominal": ppn,
        "pph_nominal": pph,
        "total_netto": total_netto,
        "basis_pph": _invoice_money(pph_base),
    }


def _invoice_payload(payload, existing=None):
    """Normalisasi payload API invoice tanpa menghitung pajak tahap UI."""
    source = dict(existing or {})
    source.update(payload or {})
    nomor_invoice = str(source.get("nomor_invoice", "")).strip()
    kontrak_id = source.get("kontrak_id")
    if not nomor_invoice:
        raise ValueError("Nomor invoice wajib diisi.")
    try:
        kontrak_id = int(kontrak_id)
    except (TypeError, ValueError) as error:
        raise ValueError("kontrak_id wajib berupa angka.") from error
    if kontrak_id <= 0:
        raise ValueError("kontrak_id wajib lebih besar dari 0.")

    tarif_pph = _invoice_decimal(source, "tarif_pph")
    if tarif_pph > Decimal("100"):
        raise ValueError("tarif_pph tidak boleh lebih besar dari 100%.")

    result = {
        "kontrak_id": kontrak_id,
        "nomor_invoice": nomor_invoice,
        "nomor_sa_wan": str(source.get("nomor_sa_wan", "") or "").strip() or None,
        "pi_no": str(source.get("pi_no", "") or "").strip() or None,
        "nomor_po": str(source.get("nomor_po", "") or "").strip() or None,
        "customer": str(source.get("customer", "") or "").strip() or None,
        "alamat_customer": str(source.get("alamat_customer", "") or "").strip() or None,
        "npwp_customer": str(source.get("npwp_customer", "") or "").strip() or None,
        "informasi_bank": str(source.get("informasi_bank", "") or "").strip() or None,
        "tanggal_invoice": _invoice_date(source, "tanggal_invoice"),
        "jatuh_tempo": _invoice_date(source, "jatuh_tempo"),
        "nilai_dpp": _invoice_decimal(source, "nilai_dpp"),
        "nilai_gross": _invoice_decimal(source, "nilai_gross"),
        "diskon_nominal": _invoice_decimal(source, "diskon_nominal"),
        "add_cost": _invoice_decimal(source, "add_cost"),
        "management_fee": _invoice_decimal(source, "management_fee"),
        "ppn_nominal": _invoice_decimal(source, "ppn_nominal"),
        "pph_nominal": _invoice_decimal(source, "pph_nominal"),
        "total_netto": _invoice_decimal(source, "total_netto"),
        "gunakan_professional_sum": 1 if _invoice_bool(source, "gunakan_professional_sum") else 0,
        "gunakan_estimasi_sum": 1 if _invoice_bool(source, "gunakan_estimasi_sum") else 0,
        "kena_ppn": 1 if _invoice_bool(source, "kena_ppn", True) else 0,
        "kena_pph": 1 if _invoice_bool(source, "kena_pph") else 0,
        "tarif_pph": tarif_pph,
        "status_invoice": str(source.get("status_invoice", "TERBIT") or "TERBIT").strip().upper(),
        "keterangan": str(source.get("keterangan", "") or "").strip() or None,
    }
    result.update({key: value for key, value in _invoice_calculate_values(result).items() if key != "basis_pph"})
    return result


def _invoice_source_context(nomor_kontrak, pi_no=None, nomor_sa_wan=None):
    """Validasi dan ambil konteks sumber invoice seperti alur Modul 3 legacy."""
    nomor_kontrak = nilai_bersih(nomor_kontrak)
    pi_no = nilai_bersih(pi_no)
    nomor_sa_wan = nilai_bersih(nomor_sa_wan)
    if not nomor_kontrak:
        raise ValueError("Nomor kontrak wajib dipilih dari master PI.")
    if not pi_no:
        raise ValueError("Nomor PI wajib dipilih dari master PI.")

    master_rows = muat_pi_master(nomor_kontrak, pi_no)
    if not master_rows:
        raise ValueError("Kombinasi nomor kontrak dan nomor PI tidak ditemukan di database_proforma_invoice.")

    transactions = muat_transaksi_terfilter(nomor_kontrak, pi_no)
    if nomor_sa_wan:
        available_wan = {
            nilai_bersih(row.get("nomor_wan_sa"))
            for row in transactions
            if nilai_bersih(row.get("nomor_wan_sa")) not in {"", "-"}
        }
        if available_wan and nomor_sa_wan not in available_wan:
            raise ValueError("Nomor WAN/SA tidak sesuai dengan PI yang dipilih.")

    master = master_rows[0]
    return {
        "nomor_kontrak": nomor_kontrak,
        "pi_no": pi_no,
        "nomor_sa_wan": nomor_sa_wan or None,
        "master": master,
        "transactions": transactions,
    }


@navigation_bp.route("/api/modul-3/invoice-sources", methods=["GET"])
@login_required
def api_invoice_sources():
    """Sumber input invoice resmi dari proforma invoice dan transaksi BSS."""
    try:
        nomor_kontrak = nilai_bersih(request.args.get("nomor_kontrak", ""))
        pi_no = nilai_bersih(request.args.get("pi_no", ""))
        if not nomor_kontrak:
            with get_db_connection() as connection:
                cursor = connection.cursor(dictionary=True)
                try:
                    cursor.execute(
                        """
                        SELECT DISTINCT TRIM(CAST(`Nomor Kontrak` AS CHAR)) AS nomor_kontrak
                        FROM database_proforma_invoice
                        WHERE `Nomor Kontrak` IS NOT NULL
                          AND TRIM(CAST(`Nomor Kontrak` AS CHAR)) NOT IN ('', '-', '0', 'Nomor Kontrak')
                        ORDER BY nomor_kontrak
                        """
                    )
                    contracts = [nilai_bersih(row.get("nomor_kontrak")) for row in cursor.fetchall()]
                finally:
                    cursor.close()
            contracts = [item for item in contracts if item]
            return jsonify({"success": True, "data": [{"nomor_kontrak": item} for item in contracts], "total": len(contracts)})

        if not pi_no:
            with get_db_connection() as connection:
                cursor = connection.cursor(dictionary=True)
                try:
                    cursor.execute(
                        """
                        SELECT DISTINCT
                            TRIM(CAST(`Proforma Invoice No.` AS CHAR)) AS pi_no,
                            TRIM(CAST(`Nomor Purchase Order` AS CHAR)) AS nomor_po,
                            `Pihak Pertama` AS customer,
                            `Alamat Pihak Pertama` AS alamat_customer,
                            COALESCE(`Tanggal Performa Invoice New`, `Tanggal Performa Invoice`) AS tanggal_pi,
                            `Lingkup Pekerjaan` AS keterangan
                        FROM database_proforma_invoice
                        WHERE CONVERT(`Nomor Kontrak` USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                              CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND `Proforma Invoice No.` IS NOT NULL
                          AND TRIM(CAST(`Proforma Invoice No.` AS CHAR)) NOT IN ('', '-', '0', 'Proforma Invoice No.')
                        ORDER BY pi_no
                        """,
                        (nomor_kontrak,),
                    )
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
            result = [{
                "nomor_kontrak": nomor_kontrak,
                "pi_no": nilai_bersih(row.get("pi_no")),
                "nomor_po": nilai_bersih(row.get("nomor_po")),
                "customer": nilai_bersih(row.get("customer")),
                "alamat_customer": nilai_bersih(row.get("alamat_customer")),
                "tanggal_pi": nilai_bersih(row.get("tanggal_pi")),
                "keterangan": nilai_bersih(row.get("keterangan")),
            } for row in rows if nilai_bersih(row.get("pi_no"))]
            return jsonify({"success": True, "data": result, "total": len(result)})

        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        master = master_rows[0] if master_rows else None
        transactions = muat_transaksi_terfilter(nomor_kontrak, pi_no)
        wan_sa = sorted({
            nilai_bersih(row.get("nomor_wan_sa"))
            for row in transactions
            if nilai_bersih(row.get("nomor_wan_sa")) not in {"", "-"}
        })
        gross = sum((_invoice_decimal(row, "total_harga") for row in transactions), Decimal("0"))
        return jsonify({
            "success": True,
            "data": {
                "nomor_kontrak": nomor_kontrak,
                "pi_no": pi_no,
                "nomor_po": nilai_bersih(master.get("nomor_po")) if master else "",
                "customer": nilai_bersih(master.get("pihak_pertama")) if master else "",
                "alamat_customer": nilai_bersih(master.get("alamat_pihak_pertama")) if master else "",
                "tanggal_pi": nilai_bersih(master.get("tanggal_pi")) if master else "",
                "keterangan": nilai_bersih(master.get("lingkup_pekerjaan")) if master else "",
                "wan_sa_options": wan_sa,
                "nilai_gross_sumber": _invoice_json_value(gross),
                "transactions": transactions,
            },
        })
    except Exception as error:
        LOGGER.exception("API sumber invoice gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


def _invoice_insert(connection, payload):
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO database_master_invoice
            (
                kontrak_id, nomor_invoice, nomor_sa_wan, pi_no, nomor_po,
                customer, alamat_customer, npwp_customer, informasi_bank,
                tanggal_invoice, jatuh_tempo, nilai_dpp, nilai_gross,
                diskon_nominal, add_cost, management_fee, ppn_nominal,
                pph_nominal, total_netto, gunakan_professional_sum,
                gunakan_estimasi_sum, kena_ppn, kena_pph, tarif_pph,
                status_invoice, keterangan
            )
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            tuple(payload.values()),
        )
        return cursor.lastrowid
    finally:
        cursor.close()


@navigation_bp.route("/api/modul-3/invoices/calculate", methods=["POST"])
@login_required
def api_invoice_calculate():
    """Preview kalkulasi pajak tanpa menyimpan invoice."""
    try:
        raw_payload = request.get_json(silent=True) or {}
        raw_payload.setdefault("kontrak_id", 1)
        raw_payload.setdefault("nomor_invoice", "CALCULATION-PREVIEW")
        normalized = _invoice_payload(raw_payload)
        calculated = _invoice_calculate_values(normalized)
        return jsonify({
            "success": True,
            "data": {key: _invoice_json_value(value) for key, value in calculated.items()},
            "formula": {
                "ppn": "DPP x 11% jika kena_ppn aktif",
                "pph": "Management Fee x tarif jika Professional Sum, selain itu DPP x tarif",
                "total_netto": "DPP + PPN - PPh",
            },
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API kalkulasi invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/invoices", methods=["GET"])
@login_required
def api_invoice_list():
    """Daftar invoice normalized dengan filter kontrak/status/search."""
    try:
        conditions = []
        params = []
        kontrak_id = request.args.get("kontrak_id", "").strip()
        status = request.args.get("status", "").strip().upper()
        search = request.args.get("search", "").strip()
        if kontrak_id:
            conditions.append("i.kontrak_id = %s")
            params.append(int(kontrak_id))
        if status:
            conditions.append("i.status_invoice = %s")
            params.append(status)
        if search:
            conditions.append(
                "(i.nomor_invoice LIKE %s OR i.pi_no LIKE %s OR i.customer LIKE %s)"
            )
            term = f"%{search}%"
            params.extend([term, term, term])
        where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"""
                    SELECT i.*, k.nomor_kontrak
                    FROM database_master_invoice i
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    {where_sql}
                    ORDER BY i.tanggal_invoice DESC, i.id DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return jsonify({"success": True, "data": [_invoice_json_row(row) for row in rows], "total": len(rows)})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error), "data": []}), 400
    except Exception as error:
        LOGGER.exception("API daftar invoice gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route("/api/modul-3/invoices/<int:invoice_id>", methods=["GET"])
@login_required
def api_invoice_detail(invoice_id):
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT i.*, k.nomor_kontrak
                    FROM database_master_invoice i
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    WHERE i.id = %s
                    LIMIT 1
                    """,
                    (invoice_id,),
                )
                invoice = cursor.fetchone()
                if not invoice:
                    return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
                cursor.execute(
                    "SELECT * FROM database_invoice_allocation WHERE invoice_id = %s ORDER BY id",
                    (invoice_id,),
                )
                allocations = cursor.fetchall()
                cursor.execute(
                    "SELECT * FROM database_invoice_payment WHERE invoice_id = %s ORDER BY id",
                    (invoice_id,),
                )
                payments = cursor.fetchall()
                cursor.execute(
                    "SELECT * FROM database_tax_invoice_reference WHERE invoice_id = %s ORDER BY id",
                    (invoice_id,),
                )
                tax_references = cursor.fetchall()
            finally:
                cursor.close()
        return jsonify({
            "success": True,
            "data": _invoice_json_row(invoice),
            "allocations": [_invoice_json_row(row) for row in allocations],
            "payments": [_invoice_json_row(row) for row in payments],
            "tax_references": [_invoice_json_row(row) for row in tax_references],
        })
    except Exception as error:
        LOGGER.exception("API detail invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/invoices", methods=["POST"])
@login_required
def api_invoice_create():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        raw_payload = request.get_json(silent=True) or {}
        source = _invoice_source_context(
            raw_payload.get("nomor_kontrak"),
            raw_payload.get("pi_no"),
            raw_payload.get("nomor_sa_wan"),
        )
        payload = _invoice_payload(raw_payload)
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id FROM master_kontrak
                    WHERE CONVERT(nomor_kontrak USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                          CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_ci
                    LIMIT 1
                    """,
                    (source["nomor_kontrak"],),
                )
                contract = cursor.fetchone()
                if not contract:
                    raise ValueError("Nomor kontrak belum tersedia di master_kontrak.")
                payload["kontrak_id"] = contract[0] if not isinstance(contract, dict) else contract["id"]
                invoice_id = _invoice_insert(connection, payload)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("CREATE", "INVOICE", invoice_id, {"nomor_invoice": payload["nomor_invoice"]})
        return jsonify({"success": True, "message": "Invoice berhasil dibuat.", "data": {"id": invoice_id}}), 201
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API pembuatan invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/invoices/<int:invoice_id>", methods=["PUT", "PATCH"])
@login_required
def api_invoice_update(invoice_id):
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        raw_payload = request.get_json(silent=True) or {}
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT i.*, k.nomor_kontrak
                    FROM database_master_invoice i
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    WHERE i.id = %s LIMIT 1
                    """,
                    (invoice_id,),
                )
                existing = cursor.fetchone()
            finally:
                cursor.close()
            if not existing:
                return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
            _invoice_source_context(
                raw_payload.get("nomor_kontrak") or existing.get("nomor_kontrak"),
                raw_payload.get("pi_no") or existing.get("pi_no"),
                raw_payload.get("nomor_sa_wan") or existing.get("nomor_sa_wan"),
            )
            payload = _invoice_payload(raw_payload, existing)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE database_master_invoice SET
                        kontrak_id=%s, nomor_invoice=%s, nomor_sa_wan=%s, pi_no=%s,
                        nomor_po=%s, customer=%s, alamat_customer=%s, npwp_customer=%s,
                        informasi_bank=%s, tanggal_invoice=%s, jatuh_tempo=%s,
                        nilai_dpp=%s, nilai_gross=%s, diskon_nominal=%s, add_cost=%s,
                        management_fee=%s, ppn_nominal=%s, pph_nominal=%s,
                        total_netto=%s, gunakan_professional_sum=%s,
                        gunakan_estimasi_sum=%s, kena_ppn=%s, kena_pph=%s,
                        tarif_pph=%s, status_invoice=%s, keterangan=%s
                    WHERE id=%s
                    LIMIT 1
                    """,
                    tuple(payload.values()) + (invoice_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("UPDATE", "INVOICE", invoice_id, {"nomor_invoice": payload["nomor_invoice"]})
        return jsonify({"success": True, "message": "Invoice berhasil diperbarui."})
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API update invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/invoices/<int:invoice_id>/save-as", methods=["POST"])
@login_required
def api_invoice_save_as(invoice_id):
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT i.*, k.nomor_kontrak
                    FROM database_master_invoice i
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    WHERE i.id = %s LIMIT 1
                    """,
                    (invoice_id,),
                )
                existing = cursor.fetchone()
            finally:
                cursor.close()
            if not existing:
                return jsonify({"success": False, "message": "Invoice sumber tidak ditemukan."}), 404
            raw_payload = request.get_json(silent=True) or {}
            _invoice_source_context(
                raw_payload.get("nomor_kontrak") or existing.get("nomor_kontrak"),
                raw_payload.get("pi_no") or existing.get("pi_no"),
                raw_payload.get("nomor_sa_wan") or existing.get("nomor_sa_wan"),
            )
            payload = _invoice_payload(raw_payload, existing)
            cursor = connection.cursor()
            try:
                new_id = _invoice_insert(connection, payload)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("SAVE_AS", "INVOICE", new_id, {"source_invoice_id": invoice_id, "nomor_invoice": payload["nomor_invoice"]})
        return jsonify({"success": True, "message": "Invoice berhasil dibuat dengan Save As.", "data": {"id": new_id}}), 201
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API save-as invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/invoices/<int:invoice_id>", methods=["DELETE"])
@login_required
def api_invoice_delete(invoice_id):
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE database_master_invoice SET status_invoice = 'DIBATALKAN' WHERE id = %s LIMIT 1",
                    (invoice_id,),
                )
                if cursor.rowcount == 0:
                    return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("CANCEL", "INVOICE", invoice_id, {})
        return jsonify({"success": True, "message": "Invoice dibatalkan."})
    except Exception as error:
        LOGGER.exception("API pembatalan invoice gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/tax-references", methods=["GET"])
@login_required
def api_tax_reference_list():
    try:
        invoice_id = request.args.get("invoice_id", "").strip()
        conditions = []
        params = []
        if invoice_id:
            conditions.append("t.invoice_id = %s")
            params.append(int(invoice_id))
        where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"""
                    SELECT t.*, i.nomor_invoice, k.nomor_kontrak
                    FROM database_tax_invoice_reference t
                    LEFT JOIN database_master_invoice i ON i.id = t.invoice_id
                    LEFT JOIN master_kontrak k ON k.id = t.kontrak_id
                    {where_sql}
                    ORDER BY t.updated_at DESC, t.id DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return jsonify({"success": True, "data": [_invoice_json_row(row) for row in rows], "total": len(rows)})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error), "data": []}), 400
    except Exception as error:
        LOGGER.exception("API daftar referensi faktur pajak gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route("/api/modul-3/tax-references", methods=["POST"])
@login_required
def api_tax_reference_upsert():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    payload = request.get_json(silent=True) or {}
    try:
        invoice_id = int(payload.get("invoice_id"))
        if invoice_id <= 0:
            raise ValueError("invoice_id wajib diisi.")
        nsfp = str(payload.get("nsfp", "") or "").strip() or None
        if not nsfp:
            raise ValueError("Nomor Seri Faktur Pajak/NSFP wajib diisi.")
        tanggal_fp = _invoice_date(payload, "tanggal_fp")
        nilai_dpp = _invoice_decimal(payload, "nilai_dpp")
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT id, kontrak_id, nomor_invoice, customer,
                           alamat_customer, npwp_customer, nilai_dpp
                    FROM database_master_invoice
                    WHERE id = %s LIMIT 1
                    """,
                    (invoice_id,),
                )
                invoice = cursor.fetchone()
            finally:
                cursor.close()
            if not invoice:
                return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO database_tax_invoice_reference
                    (invoice_id, kontrak_id, nomor_invoice, nsfp, tanggal_fp,
                     nilai_dpp, customer_name, customer_address, customer_npwp,
                     keterangan, sumber_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'FLASK')
                    ON DUPLICATE KEY UPDATE
                        nsfp=VALUES(nsfp), tanggal_fp=VALUES(tanggal_fp),
                        nilai_dpp=VALUES(nilai_dpp), customer_name=VALUES(customer_name),
                        customer_address=VALUES(customer_address), customer_npwp=VALUES(customer_npwp),
                        keterangan=VALUES(keterangan), sumber_data='FLASK'
                    """,
                    (
                        invoice_id,
                        invoice["kontrak_id"],
                        invoice["nomor_invoice"],
                        nsfp,
                        tanggal_fp,
                        nilai_dpp,
                        str(payload.get("customer_name", "") or invoice.get("customer") or "").strip() or None,
                        str(payload.get("customer_address", "") or invoice.get("alamat_customer") or "").strip() or None,
                        str(payload.get("customer_npwp", "") or invoice.get("npwp_customer") or "").strip() or None,
                        str(payload.get("keterangan", "") or "").strip() or None,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("UPSERT", "TAX_REFERENCE", invoice_id, {"nsfp": nsfp})
        return jsonify({"success": True, "message": "Referensi faktur pajak berhasil disimpan."})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API simpan referensi faktur pajak gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/tax-references/<int:reference_id>", methods=["DELETE"])
@login_required
def api_tax_reference_delete(reference_id):
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("DELETE FROM database_tax_invoice_reference WHERE id = %s LIMIT 1", (reference_id,))
                if cursor.rowcount == 0:
                    return jsonify({"success": False, "message": "Referensi faktur pajak tidak ditemukan."}), 404
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("DELETE", "TAX_REFERENCE", reference_id, {})
        return jsonify({"success": True, "message": "Referensi faktur pajak dihapus."})
    except Exception as error:
        LOGGER.exception("API hapus referensi faktur pajak gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


def _payment_status(value):
    normalized = str(value or "BELUM DIBAYAR").strip().upper()
    aliases = {
        "LUNAS": "DIBAYAR",
        "DIBAYAR": "DIBAYAR",
        "PAID": "DIBAYAR",
        "PROSES": "PROSES",
        "DIPROSES": "PROSES",
        "PROCESS": "PROSES",
        "BELUM DIBAYAR": "BELUM DIBAYAR",
        "UNPAID": "BELUM DIBAYAR",
    }
    result = aliases.get(normalized, normalized)
    if result not in {"BELUM DIBAYAR", "PROSES", "DIBAYAR"}:
        raise ValueError("Status pembayaran harus BELUM DIBAYAR, PROSES, atau DIBAYAR.")
    return result


def _payment_invoice_charge(invoice):
    """Tagihan bruto sesuai invoice: DPP + PPN, sebelum potongan pembayaran."""
    dpp = Decimal(str(invoice.get("invoice_nilai_dpp", invoice.get("nilai_dpp")) or 0))
    ppn = Decimal(str(invoice.get("invoice_ppn_nominal", invoice.get("ppn_nominal")) or 0))
    gross = dpp + ppn
    if gross > 0:
        return _invoice_money(gross)
    return _invoice_money(Decimal(str(invoice.get("invoice_total_netto", invoice.get("total_netto")) or 0)))


def _payment_derived_status(tagihan, dibayar, pph, wapu, tanggal_penyerahan):
    if tagihan > 0 and tagihan - dibayar - pph - wapu <= Decimal("0.01"):
        return "DIBAYAR"
    if dibayar > 0 or tanggal_penyerahan:
        return "PROSES"
    return "BELUM DIBAYAR"


def _payment_event_delete_allowed():
    """Hanya pemilik fungsi keuangan atau admin yang boleh membatalkan pembayaran."""
    role = str(session.get("current_role") or "").strip().lower()
    return role in {"finance & tax", "finance", "super admin", "admin"}


def _payment_json_row(row):
    result = _invoice_json_row(row)
    tagihan_tercatat = Decimal(str(row.get("nominal_tagihan") or 0))
    tagihan_invoice = _payment_invoice_charge(row)
    tagihan = tagihan_tercatat if tagihan_tercatat > 0 else tagihan_invoice
    dibayar = Decimal(str(row.get("nominal_dibayar") or 0))
    pph = Decimal(str(row.get("potongan_pph") or 0))
    wapu = Decimal(str(row.get("potongan_ppn_wapu") or 0))
    saldo = max(Decimal("0"), tagihan - dibayar - pph - wapu)
    result["nominal_tagihan"] = _invoice_json_value(tagihan)
    result["nominal_tagihan_tercatat"] = _invoice_json_value(tagihan_tercatat)
    result["tagihan_invoice_bruto"] = _invoice_json_value(tagihan_invoice)
    result["tagihan_dari_invoice"] = tagihan_tercatat <= 0
    result["tagihan_berbeda_invoice"] = (
        tagihan_tercatat > 0 and tagihan_invoice > 0
        and abs(tagihan_tercatat - tagihan_invoice) > Decimal("0.01")
    )
    result["saldo_piutang"] = _invoice_json_value(saldo)
    result["netto_bank_seharusnya"] = _invoice_json_value(max(Decimal("0"), tagihan - pph - wapu))
    result["perlu_verifikasi"] = (
        row.get("status_pembayaran") == "DIBAYAR" and (tagihan <= 0 or saldo > Decimal("0.01"))
    )
    jatuh_tempo = row.get("tanggal_jatuh_tempo") or row.get("invoice_jatuh_tempo")
    if tagihan <= 0:
        result["aging_status"] = "TAGIHAN BELUM ADA"
    elif saldo <= Decimal("0.01"):
        result["aging_status"] = "LUNAS"
    elif jatuh_tempo and jatuh_tempo < date.today():
        result["aging_status"] = "OVERDUE"
    else:
        result["aging_status"] = "BELUM JATUH TEMPO"
    return result


@navigation_bp.route("/api/modul-3/payment-monitoring", methods=["GET"])
@login_required
def api_payment_monitoring_list():
    try:
        conditions = []
        params = []
        invoice_id = request.args.get("invoice_id", "").strip()
        kontrak_id = request.args.get("kontrak_id", "").strip()
        status = request.args.get("status", "").strip().upper()
        if invoice_id:
            conditions.append("p.invoice_id = %s")
            params.append(int(invoice_id))
        if kontrak_id:
            conditions.append("i.kontrak_id = %s")
            params.append(int(kontrak_id))
        if status:
            status = _payment_status(status)
            conditions.append("p.status_pembayaran = %s")
            params.append(status)
        where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"""
                    SELECT p.*, i.nomor_invoice, i.kontrak_id,
                           i.customer, i.nilai_dpp AS invoice_nilai_dpp,
                           i.ppn_nominal AS invoice_ppn_nominal,
                           i.total_netto AS invoice_total_netto,
                           i.jatuh_tempo AS invoice_jatuh_tempo,
                           k.nomor_kontrak
                    FROM database_invoice_payment p
                    JOIN database_master_invoice i ON i.id = p.invoice_id
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    {where_sql}
                    ORDER BY p.tanggal_jatuh_tempo ASC, p.id DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        data = [_payment_json_row(row) for row in rows]
        summary = {
            "total": len(data),
            "belum_dibayar": sum(1 for row in data if row.get("status_pembayaran") == "BELUM DIBAYAR"),
            "proses": sum(1 for row in data if row.get("status_pembayaran") == "PROSES"),
            "dibayar": sum(1 for row in data if row.get("status_pembayaran") == "DIBAYAR"),
            "overdue": sum(1 for row in data if row.get("aging_status") == "OVERDUE"),
            "total_piutang": format(sum(Decimal(str(row.get("saldo_piutang", "0"))) for row in data), ".2f"),
        }
        return jsonify({"success": True, "data": data, "summary": summary})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error), "data": []}), 400
    except Exception as error:
        LOGGER.exception("API monitoring pembayaran gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route("/api/modul-3/payment-events", methods=["GET"])
@login_required
def api_payment_events_list():
    try:
        invoice_id = int(request.args.get("invoice_id", ""))
        if invoice_id <= 0:
            raise ValueError("invoice_id wajib diisi.")
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT id FROM database_master_invoice WHERE id = %s LIMIT 1",
                    (invoice_id,),
                )
                if not cursor.fetchone():
                    return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
                cursor.execute(
                    """SELECT nominal_dibayar, potongan_pph, potongan_ppn_wapu,
                              tanggal_pelunasan
                       FROM database_invoice_payment
                       WHERE invoice_id = %s ORDER BY id DESC LIMIT 1""",
                    (invoice_id,),
                )
                snapshot = cursor.fetchone() or {}
                cursor.execute(
                    """SELECT id, invoice_id, tanggal_pembayaran, nominal_diterima,
                              potongan_pph, potongan_ppn_wapu, catatan,
                              created_by, created_at, deleted_at, deleted_by, deleted_role
                       FROM database_invoice_payment_event
                       WHERE invoice_id = %s
                       ORDER BY tanggal_pembayaran DESC, id DESC""",
                    (invoice_id,),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        active_rows = [row for row in rows if not row.get("deleted_at")]
        can_delete = _payment_event_delete_allowed()
        event_totals = {
            key: sum((Decimal(str(row.get(key) or 0)) for row in active_rows), Decimal("0"))
            for key in ("nominal_diterima", "potongan_pph", "potongan_ppn_wapu")
        }
        opening = {
            "nominal_diterima": Decimal(str(snapshot.get("nominal_dibayar") or 0)) - event_totals["nominal_diterima"],
            "potongan_pph": Decimal(str(snapshot.get("potongan_pph") or 0)) - event_totals["potongan_pph"],
            "potongan_ppn_wapu": Decimal(str(snapshot.get("potongan_ppn_wapu") or 0)) - event_totals["potongan_ppn_wapu"],
        }
        inconsistent = any(amount < Decimal("-0.01") for amount in opening.values())
        return jsonify({
            "success": True,
            "data": [_invoice_json_row(row) for row in (rows if can_delete else active_rows)],
            "can_delete": can_delete,
            "deleted_count": len(rows) - len(active_rows) if can_delete else 0,
            "opening": {key: _invoice_json_value(max(Decimal("0"), value)) for key, value in opening.items()},
            "legacy_paid_date": _invoice_json_value(snapshot.get("tanggal_pelunasan")),
            "reconciliation_warning": inconsistent,
        })
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API riwayat pembayaran gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/payment-events", methods=["POST"])
@login_required
def api_payment_event_create():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    payload = request.get_json(silent=True) or {}
    try:
        invoice_id = int(payload.get("invoice_id"))
        if invoice_id <= 0:
            raise ValueError("invoice_id wajib diisi.")
        paid_date = _invoice_date(payload, "tanggal_pembayaran")
        submitted_handover = _invoice_date(payload, "tanggal_penyerahan")
        received = _invoice_money(_invoice_decimal(payload, "nominal_diterima"))
        pph = _invoice_money(_invoice_decimal(payload, "potongan_pph"))
        wapu = _invoice_money(_invoice_decimal(payload, "potongan_ppn_wapu"))
        if min(received, pph, wapu) < 0:
            raise ValueError("Nominal pembayaran dan potongan tidak boleh negatif.")
        has_payment = received + pph + wapu > 0
        if has_payment and paid_date is None:
            raise ValueError("Tanggal pembayaran wajib diisi jika ada penerimaan atau potongan.")
        if paid_date and paid_date > date.today():
            raise ValueError("Tanggal pembayaran tidak boleh di masa depan.")
        if not has_payment and submitted_handover is None:
            raise ValueError("Isi nominal pembayaran atau tanggal penyerahan.")
        note = str(payload.get("catatan") or "").strip()
        if len(note) > 500:
            raise ValueError("Catatan maksimal 500 karakter.")
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """SELECT id, nilai_dpp, ppn_nominal, total_netto, jatuh_tempo
                       FROM database_master_invoice WHERE id = %s LIMIT 1 FOR UPDATE""",
                    (invoice_id,),
                )
                invoice = cursor.fetchone()
                if not invoice:
                    return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
                cursor.execute(
                    """SELECT * FROM database_invoice_payment
                       WHERE invoice_id = %s ORDER BY id DESC LIMIT 1 FOR UPDATE""",
                    (invoice_id,),
                )
                snapshot = cursor.fetchone()
                cursor.execute(
                    """SELECT MAX(tanggal_pembayaran) AS latest_date
                       FROM database_invoice_payment_event
                       WHERE invoice_id = %s AND deleted_at IS NULL""",
                    (invoice_id,),
                )
                latest_row = cursor.fetchone() or {}
            finally:
                cursor.close()
            charge = Decimal(str((snapshot or {}).get("nominal_tagihan") or 0))
            if charge <= 0:
                charge = _payment_invoice_charge(invoice)
            if charge <= 0:
                raise ValueError("Nilai tagihan invoice belum tersedia.")
            total_received = Decimal(str((snapshot or {}).get("nominal_dibayar") or 0)) + received
            total_pph = Decimal(str((snapshot or {}).get("potongan_pph") or 0)) + pph
            total_wapu = Decimal(str((snapshot or {}).get("potongan_ppn_wapu") or 0)) + wapu
            if total_received + total_pph + total_wapu > charge + Decimal("0.01"):
                raise ValueError("Total pembayaran dan potongan melebihi tagihan invoice.")
            handover = submitted_handover or (snapshot or {}).get("tanggal_penyerahan")
            status = _payment_derived_status(charge, total_received, total_pph, total_wapu, handover)
            due_date = (snapshot or {}).get("tanggal_jatuh_tempo") or invoice.get("jatuh_tempo")
            latest_date = max(filter(None, (latest_row.get("latest_date"), paid_date)), default=None)
            settled_date = latest_date if status == "DIBAYAR" else None
            if status == "DIBAYAR" and settled_date is None:
                settled_date = (snapshot or {}).get("tanggal_pelunasan")
            if handover and settled_date and settled_date < handover:
                raise ValueError("Tanggal penyerahan tidak boleh setelah tanggal pelunasan.")
            duration = (settled_date - handover).days if settled_date and handover else None
            difference = _invoice_money(total_received - max(Decimal("0"), charge - total_pph - total_wapu))
            writer = connection.cursor()
            event_id = None
            payment_id = (snapshot or {}).get("id")
            try:
                if has_payment:
                    writer.execute(
                        """INSERT INTO database_invoice_payment_event
                           (invoice_id, tanggal_pembayaran, nominal_diterima,
                            potongan_pph, potongan_ppn_wapu, catatan, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (invoice_id, paid_date, received, pph, wapu, note or None,
                         str(session.get("current_user") or "")[:150] or None),
                    )
                    event_id = writer.lastrowid
                if snapshot:
                    writer.execute(
                        """UPDATE database_invoice_payment SET
                           nominal_tagihan=%s, nominal_dibayar=%s,
                           potongan_pph=%s, potongan_ppn_wapu=%s,
                           selisih_lainnya=%s, status_pembayaran=%s,
                           tanggal_pelunasan=%s, durasi_riil_hari=%s,
                           tanggal_penyerahan=%s
                           WHERE id=%s LIMIT 1""",
                        (charge, total_received, total_pph, total_wapu,
                         difference, status, settled_date, duration, handover, snapshot["id"]),
                    )
                else:
                    writer.execute(
                        """INSERT INTO database_invoice_payment
                           (invoice_id, tanggal_penyerahan, tanggal_jatuh_tempo, tanggal_pelunasan,
                            nominal_tagihan, nominal_dibayar, potongan_pph,
                            potongan_ppn_wapu, selisih_lainnya, status_pembayaran)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (invoice_id, handover, due_date, settled_date, charge, total_received,
                         total_pph, total_wapu, difference, status),
                    )
                    payment_id = writer.lastrowid
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                writer.close()
        if has_payment:
            _record_finance_audit("CREATE", "PAYMENT_EVENT", event_id, {"invoice_id": invoice_id})
            return jsonify({"success": True, "message": "Pembayaran dicatat dalam riwayat.", "data": {"id": event_id}}), 201
        _record_finance_audit("UPSERT", "PAYMENT", payment_id, {"invoice_id": invoice_id})
        return jsonify({"success": True, "message": "Tanggal penyerahan berhasil disimpan."}), 200
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API catat riwayat pembayaran gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/payment-events/<int:event_id>", methods=["DELETE"])
@login_required
def api_payment_event_delete(event_id):
    if not _payment_event_delete_allowed():
        return jsonify({"success": False, "message": "Hanya role Finance/Finance & Tax atau Admin yang dapat menghapus riwayat pembayaran."}), 403
    actor = str(session.get("current_user") or "").strip()[:150]
    role = str(session.get("current_role") or "").strip()[:80]
    if not actor:
        return jsonify({"success": False, "message": "Identitas pengguna tidak tersedia. Login ulang sebelum menghapus."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute("SELECT invoice_id FROM database_invoice_payment_event WHERE id = %s", (event_id,))
                reference = cursor.fetchone()
                if not reference:
                    return jsonify({"success": False, "message": "Riwayat pembayaran tidak ditemukan."}), 404
                invoice_id = reference["invoice_id"]
                cursor.execute(
                    """SELECT id, nilai_dpp, ppn_nominal, total_netto, jatuh_tempo
                       FROM database_master_invoice WHERE id = %s LIMIT 1 FOR UPDATE""",
                    (invoice_id,),
                )
                invoice = cursor.fetchone()
                if not invoice:
                    return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
                cursor.execute(
                    """SELECT * FROM database_invoice_payment
                       WHERE invoice_id = %s ORDER BY id DESC LIMIT 1 FOR UPDATE""",
                    (invoice_id,),
                )
                snapshot = cursor.fetchone()
                if not snapshot:
                    return jsonify({"success": False, "message": "Ringkasan pembayaran tidak ditemukan; penghapusan dibatalkan."}), 409
                cursor.execute(
                    """SELECT * FROM database_invoice_payment_event
                       WHERE id = %s AND invoice_id = %s FOR UPDATE""",
                    (event_id, invoice_id),
                )
                event = cursor.fetchone()
                if not event or event.get("deleted_at"):
                    return jsonify({"success": False, "message": "Riwayat sudah dihapus atau tidak ditemukan."}), 409
                cursor.execute(
                    """SELECT MAX(tanggal_pembayaran) AS latest_date
                       FROM database_invoice_payment_event
                       WHERE invoice_id = %s AND id <> %s AND deleted_at IS NULL""",
                    (invoice_id, event_id),
                )
                latest_date = (cursor.fetchone() or {}).get("latest_date")
            finally:
                cursor.close()

            charge = Decimal(str(snapshot.get("nominal_tagihan") or 0))
            if charge <= 0:
                charge = _payment_invoice_charge(invoice)
            amounts = {
                "nominal_dibayar": Decimal(str(snapshot.get("nominal_dibayar") or 0)) - Decimal(str(event.get("nominal_diterima") or 0)),
                "potongan_pph": Decimal(str(snapshot.get("potongan_pph") or 0)) - Decimal(str(event.get("potongan_pph") or 0)),
                "potongan_ppn_wapu": Decimal(str(snapshot.get("potongan_ppn_wapu") or 0)) - Decimal(str(event.get("potongan_ppn_wapu") or 0)),
            }
            if any(value < Decimal("-0.01") for value in amounts.values()):
                return jsonify({"success": False, "message": "Ringkasan dan riwayat tidak sesuai. Verifikasi data sebelum menghapus."}), 409
            amounts = {key: max(Decimal("0"), _invoice_money(value)) for key, value in amounts.items()}
            paid = amounts["nominal_dibayar"]
            pph = amounts["potongan_pph"]
            wapu = amounts["potongan_ppn_wapu"]
            handover = snapshot.get("tanggal_penyerahan")
            status = _payment_derived_status(charge, paid, pph, wapu, handover)
            settled_date = latest_date if status == "DIBAYAR" else None
            duration = (settled_date - handover).days if settled_date and handover else None
            difference = _invoice_money(paid - max(Decimal("0"), charge - pph - wapu))
            writer = connection.cursor()
            try:
                writer.execute(
                    """UPDATE database_invoice_payment_event
                       SET deleted_at = CURRENT_TIMESTAMP, deleted_by = %s, deleted_role = %s
                       WHERE id = %s AND deleted_at IS NULL LIMIT 1""",
                    (actor, role, event_id),
                )
                if writer.rowcount != 1:
                    connection.rollback()
                    return jsonify({"success": False, "message": "Riwayat sudah dihapus."}), 409
                writer.execute(
                    """UPDATE database_invoice_payment SET
                       nominal_tagihan = %s, nominal_dibayar = %s,
                       potongan_pph = %s, potongan_ppn_wapu = %s,
                       selisih_lainnya = %s, status_pembayaran = %s,
                       tanggal_pelunasan = %s, durasi_riil_hari = %s
                       WHERE id = %s LIMIT 1""",
                    (charge, paid, pph, wapu, difference, status,
                     settled_date, duration, snapshot["id"]),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                writer.close()
        _record_finance_audit("SOFT_DELETE", "PAYMENT_EVENT", event_id, {"invoice_id": invoice_id, "deleted_by": actor, "deleted_role": role})
        return jsonify({"success": True, "message": "Riwayat disembunyikan, jejak penghapusan tetap tersimpan.", "data": {"id": event_id, "invoice_id": invoice_id}})
    except Exception as error:
        LOGGER.exception("API hapus riwayat pembayaran gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/payment-monitoring", methods=["POST"])
@login_required
def api_payment_monitoring_upsert():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    payload = request.get_json(silent=True) or {}
    try:
        invoice_id = int(payload.get("invoice_id"))
        if invoice_id <= 0:
            raise ValueError("invoice_id wajib diisi.")
        tanggal_penyerahan = _invoice_date(payload, "tanggal_penyerahan")
        tanggal_jatuh_tempo = _invoice_date(payload, "tanggal_jatuh_tempo")
        tanggal_pelunasan = _invoice_date(payload, "tanggal_pelunasan")
        nominal_dibayar = _invoice_decimal(payload, "nominal_dibayar")
        potongan_pph = _invoice_decimal(payload, "potongan_pph")
        potongan_ppn_wapu = _invoice_decimal(payload, "potongan_ppn_wapu")
        if min(nominal_dibayar, potongan_pph, potongan_ppn_wapu) < 0:
            raise ValueError("Nominal pembayaran dan potongan tidak boleh negatif.")
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT id, nilai_dpp, ppn_nominal, total_netto, jatuh_tempo FROM database_master_invoice WHERE id = %s LIMIT 1 FOR UPDATE",
                    (invoice_id,),
                )
                invoice = cursor.fetchone()
                cursor.execute(
                    """SELECT id, nomor_faktur_pajak, nominal_dibayar,
                              potongan_pph, potongan_ppn_wapu
                       FROM database_invoice_payment
                       WHERE invoice_id = %s ORDER BY id DESC LIMIT 1""",
                    (invoice_id,),
                )
                existing = cursor.fetchone()
                cursor.execute(
                    """SELECT COUNT(*) AS total, MAX(tanggal_pembayaran) AS latest_date
                       FROM database_invoice_payment_event
                       WHERE invoice_id = %s AND deleted_at IS NULL""",
                    (invoice_id,),
                )
                event_summary = cursor.fetchone() or {}
                has_events = bool(event_summary.get("total"))
            finally:
                cursor.close()
            if not invoice:
                return jsonify({"success": False, "message": "Invoice tidak ditemukan."}), 404
            nominal_tagihan = _invoice_decimal(payload, "nominal_tagihan", _payment_invoice_charge(invoice))
            if nominal_tagihan <= 0:
                nominal_tagihan = _payment_invoice_charge(invoice)
            if nominal_tagihan <= 0:
                raise ValueError("Nilai tagihan invoice belum tersedia; lengkapi invoice sebelum mencatat pembayaran.")
            if has_events and (
                not existing or any(
                    _invoice_money(Decimal(str((existing or {}).get(key) or 0))) != value
                    for key, value in (
                        ("nominal_dibayar", nominal_dibayar),
                        ("potongan_pph", potongan_pph),
                        ("potongan_ppn_wapu", potongan_ppn_wapu),
                    )
                )
            ):
                raise ValueError("Nominal pembayaran setelah ada riwayat hanya boleh ditambah lewat Catat Pembayaran.")
            if tanggal_jatuh_tempo is None:
                tanggal_jatuh_tempo = invoice.get("jatuh_tempo")
            status_input = str(payload.get("status_pembayaran") or "AUTO").strip().upper()
            if status_input == "AUTO":
                status = _payment_derived_status(
                    nominal_tagihan, nominal_dibayar, potongan_pph,
                    potongan_ppn_wapu, tanggal_penyerahan,
                )
                selisih_lainnya = _invoice_money(
                    nominal_dibayar - max(Decimal("0"), nominal_tagihan - potongan_pph - potongan_ppn_wapu)
                )
            else:
                status = _payment_status(status_input)
                selisih_lainnya = _invoice_decimal(payload, "selisih_lainnya")
            if has_events:
                tanggal_pelunasan = event_summary.get("latest_date") if status == "DIBAYAR" else None
            if status_input == "AUTO":
                if status == "DIBAYAR" and tanggal_pelunasan is None:
                    raise ValueError("Tanggal pelunasan wajib diisi saat tagihan telah lunas.")
                if status != "DIBAYAR" and tanggal_pelunasan is not None:
                    raise ValueError("Tanggal pelunasan hanya diisi setelah tagihan lunas.")
            if tanggal_penyerahan and tanggal_pelunasan and tanggal_pelunasan < tanggal_penyerahan:
                raise ValueError("Tanggal pelunasan tidak boleh sebelum tanggal penyerahan.")
            durasi_riil_hari = (
                (tanggal_pelunasan - tanggal_penyerahan).days
                if tanggal_penyerahan and tanggal_pelunasan else None
            )
            values = (
                invoice_id,
                str(payload.get("nomor_faktur_pajak") or (existing or {}).get("nomor_faktur_pajak") or "").strip() or None,
                tanggal_penyerahan,
                tanggal_jatuh_tempo,
                tanggal_pelunasan,
                nominal_tagihan,
                nominal_dibayar,
                potongan_pph,
                potongan_ppn_wapu,
                selisih_lainnya,
                status,
                durasi_riil_hari,
                str(payload.get("catatan", "") or "").strip() or None,
            )
            cursor = connection.cursor()
            try:
                if existing:
                    cursor.execute(
                        """
                        UPDATE database_invoice_payment SET
                            nomor_faktur_pajak=%s, tanggal_penyerahan=%s,
                            tanggal_jatuh_tempo=%s, tanggal_pelunasan=%s,
                            nominal_tagihan=%s, nominal_dibayar=%s,
                            potongan_pph=%s, potongan_ppn_wapu=%s,
                            selisih_lainnya=%s, status_pembayaran=%s,
                            durasi_riil_hari=%s, catatan=%s
                        WHERE id=%s LIMIT 1
                        """,
                        values[1:] + (existing["id"],),
                    )
                    payment_id = existing["id"]
                else:
                    cursor.execute(
                        """
                        INSERT INTO database_invoice_payment
                        (invoice_id, nomor_faktur_pajak, tanggal_penyerahan,
                         tanggal_jatuh_tempo, tanggal_pelunasan, nominal_tagihan,
                         nominal_dibayar, potongan_pph, potongan_ppn_wapu,
                         selisih_lainnya, status_pembayaran, durasi_riil_hari, catatan)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        values,
                    )
                    payment_id = cursor.lastrowid
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("UPSERT", "PAYMENT", payment_id, {"invoice_id": invoice_id, "status": status})
        return jsonify({"success": True, "message": "Monitoring pembayaran berhasil disimpan.", "data": {"id": payment_id}})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("API simpan monitoring pembayaran gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/archives", methods=["GET"])
@login_required
def api_finance_archive_list():
    try:
        invoice_id = request.args.get("invoice_id", "").strip()
        params = []
        where_sql = "WHERE a.status = 'ACTIVE'"
        if invoice_id:
            where_sql += " AND a.invoice_id = %s"
            params.append(int(invoice_id))
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    f"""
                    SELECT a.id, a.invoice_id, a.document_type, a.original_filename,
                           a.mime_type, a.file_size, a.note, a.uploaded_by,
                           a.uploaded_at, i.nomor_invoice, k.nomor_kontrak
                    FROM database_invoice_document_archive a
                    JOIN database_master_invoice i ON i.id = a.invoice_id
                    LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                    {where_sql}
                    ORDER BY a.uploaded_at DESC, a.id DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return jsonify({"success": True, "data": [_invoice_json_row(row) for row in rows], "total": len(rows)})
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "message": str(error), "data": []}), 400
    except Exception as error:
        LOGGER.exception("API daftar arsip finance gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


@navigation_bp.route("/api/modul-3/archives", methods=["POST"])
@login_required
def api_finance_archive_upload():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    allowed_types = {
        "INVOICE RESMI", "FAKTUR PAJAK", "SSP PPN", "SSP PPH", "BUKTI POTONG", "DOKUMEN PENDUKUNG"
    }
    allowed_extensions = {"pdf", "png", "jpg", "jpeg", "xlsx", "xls", "docx", "doc"}
    file_obj = request.files.get("file")
    try:
        invoice_id = int(request.form.get("invoice_id", "0"))
        document_type = str(request.form.get("document_type", "")).strip().upper()
        if invoice_id <= 0:
            raise ValueError("invoice_id wajib diisi.")
        if document_type not in allowed_types:
            raise ValueError("Jenis dokumen tidak valid.")
        if not file_obj or not file_obj.filename:
            raise ValueError("File dokumen wajib diunggah.")
        original_name = secure_filename(file_obj.filename)
        if not original_name:
            raise ValueError("Nama file tidak valid.")
        extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        if extension not in allowed_extensions:
            raise ValueError("Ekstensi file tidak diizinkan.")
        file_bytes = file_obj.read(15 * 1024 * 1024 + 1)
        if len(file_bytes) > 15 * 1024 * 1024:
            raise ValueError("Ukuran file maksimal 15 MB.")
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT id FROM database_master_invoice WHERE id = %s LIMIT 1", (invoice_id,))
                if not cursor.fetchone():
                    raise ValueError("Invoice tidak ditemukan.")
                stored_name = f"{uuid.uuid4().hex}.{extension}"
                storage_path = os.path.join(_finance_archive_dir(), stored_name)
                with open(storage_path, "wb") as output:
                    output.write(file_bytes)
                try:
                    cursor.execute(
                        """
                        INSERT INTO database_invoice_document_archive
                        (invoice_id, document_type, original_filename, stored_filename,
                         storage_path, mime_type, file_size, note, uploaded_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            invoice_id, document_type, original_name, stored_name,
                            storage_path, file_obj.mimetype or mimetypes.guess_type(original_name)[0],
                            len(file_bytes), str(request.form.get("note", "") or "").strip() or None,
                            str(session.get("current_user", "") or "")[:150] or None,
                        ),
                    )
                    archive_id = cursor.lastrowid
                    connection.commit()
                except Exception:
                    connection.rollback()
                    if os.path.exists(storage_path):
                        os.remove(storage_path)
                    raise
            finally:
                cursor.close()
        _record_finance_audit("UPLOAD", "ARCHIVE", archive_id, {"invoice_id": invoice_id, "document_type": document_type, "filename": original_name})
        return jsonify({"success": True, "message": "Dokumen berhasil diarsipkan.", "data": {"id": archive_id}}), 201
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Upload arsip finance gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/archives/<int:archive_id>/download", methods=["GET"])
@login_required
def api_finance_archive_download(archive_id):
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT original_filename, storage_path FROM database_invoice_document_archive WHERE id = %s AND status = 'ACTIVE' LIMIT 1",
                    (archive_id,),
                )
                archive = cursor.fetchone()
            finally:
                cursor.close()
        if not archive:
            return jsonify({"success": False, "message": "Arsip tidak ditemukan."}), 404
        archive_root = os.path.realpath(_finance_archive_dir())
        file_path = os.path.realpath(archive["storage_path"])
        if os.path.commonpath([archive_root, file_path]) != archive_root or not os.path.isfile(file_path):
            return jsonify({"success": False, "message": "File arsip tidak tersedia."}), 404
        _record_finance_audit("DOWNLOAD", "ARCHIVE", archive_id, {})
        return send_file(file_path, as_attachment=True, download_name=archive["original_filename"])
    except Exception as error:
        LOGGER.exception("Download arsip finance gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/archives/<int:archive_id>", methods=["DELETE"])
@login_required
def api_finance_archive_delete(archive_id):
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE database_invoice_document_archive SET status='DELETED', deleted_at=CURRENT_TIMESTAMP WHERE id=%s AND status='ACTIVE' LIMIT 1",
                    (archive_id,),
                )
                if cursor.rowcount == 0:
                    return jsonify({"success": False, "message": "Arsip tidak ditemukan."}), 404
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        _record_finance_audit("DELETE", "ARCHIVE", archive_id, {})
        return jsonify({"success": True, "message": "Arsip dinonaktifkan."})
    except Exception as error:
        LOGGER.exception("Hapus arsip finance gagal.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route("/api/modul-3/audit-log", methods=["GET"])
@login_required
def api_finance_audit_log():
    role = str(session.get("current_role", "")).strip().lower()
    if role not in {"super admin", "management", "direksi", "finance", "finance & tax", "tax"}:
        return jsonify({"success": False, "message": "Audit log hanya dapat diakses role Finance/Management/Admin."}), 403
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT id, actor_username, actor_role, action, entity_type, entity_id, detail, created_at FROM database_invoice_tax_audit_log ORDER BY created_at DESC, id DESC LIMIT 200"
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        return jsonify({"success": True, "data": [_invoice_json_row(row) for row in rows], "total": len(rows)})
    except Exception as error:
        LOGGER.exception("API audit log finance gagal.")
        return jsonify({"success": False, "message": str(error), "data": []}), 500


def _invoice_document_record(invoice_id):
    with get_db_connection() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT i.*, k.nomor_kontrak
                FROM database_master_invoice i
                LEFT JOIN master_kontrak k ON k.id = i.kontrak_id
                WHERE i.id = %s LIMIT 1
                """,
                (invoice_id,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()


def _render_official_document(invoice_id, kind):
    invoice = _invoice_document_record(invoice_id)
    if not invoice:
        return "Invoice tidak ditemukan.", 404
    if kind == "invoice":
        transactions = muat_transaksi_terfilter(invoice.get("nomor_kontrak"), invoice.get("pi_no")) if invoice.get("pi_no") else []
        try:
            pi_master = muat_pi_master(invoice.get("nomor_kontrak"), invoice.get("pi_no")) if invoice.get("pi_no") else []
        except Exception:
            LOGGER.exception("Master PI tidak dapat dimuat untuk lingkup invoice %s.", invoice.get("pi_no"))
            pi_master = []
        if pi_master:
            invoice["lingkup_pekerjaan"] = pi_master[0].get("lingkup_pekerjaan", "")
        template = "invoice_official.html"
        context = invoice_context(invoice, transactions)
        context["compact"] = request.args.get("format") == "ringkas"
    else:
        template = "invoice_receipt.html"
        context = receipt_context(invoice)
    response = make_response(render_template(template, **context))
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    if request.args.get("download") == "1":
        filename = secure_filename(f'{"Invoice" if kind == "invoice" else "Kuitansi"}_{invoice["nomor_invoice"].replace("/", "-")}.html')
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@navigation_bp.route("/modul/invoice-tax/invoice/<int:invoice_id>")
@login_required
def invoice_official(invoice_id):
    try:
        return _render_official_document(invoice_id, "invoice")
    except Exception as error:
        LOGGER.exception("Dokumen invoice resmi gagal dibuat.")
        return "Dokumen invoice resmi gagal dibuat: {}".format(error), 500


@navigation_bp.route("/modul/invoice-tax/receipt/<int:invoice_id>")
@login_required
def invoice_receipt(invoice_id):
    try:
        return _render_official_document(invoice_id, "receipt")
    except Exception as error:
        LOGGER.exception("Dokumen kuitansi gagal dibuat.")
        return "Dokumen kuitansi gagal dibuat: {}".format(error), 500

@navigation_bp.route(
    "/modul/invoice-tax"
)
@login_required
def invoice_tax():
    return render_template(
        "invoice_tax.html",
        title=(
            "Modul 3: Invoice & Tax Management"
        ),
        description=(
            "Invoice resmi, faktur pajak "
            "dan proses pembayaran."
        ),
        module_code="MODUL 3",
        can_write=can_write(),
    )



# ============================================================
# MASTER PO
# ============================================================

def _master_po_clean(value):
    return "" if value is None else str(value).strip()


def _master_po_row_dict(row, columns):
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(columns, row))


@navigation_bp.route(
    "/legacy/master-po",
    methods=["GET"],
)
@navigation_bp.route(
    "/legacy/master/master-po",
    methods=["GET"],
)
@login_required
def master_po():
    """
    Alias route Master PO.
    Kedua URL disediakan agar layout lama maupun baru tetap kompatibel.
    """
    try:
        # Template utama jika tersedia.
        return render_template(
            "master_po.html",
            title="Master Purchase Order",
            module_code="MASTER PO",
            can_write=can_write(),
        )
    except Exception as error:
        LOGGER.exception("Gagal membuka halaman Master PO.")
        return jsonify({
            "success": False,
            "message": f"Halaman Master PO tidak dapat dimuat: {error}",
        }), 500


@navigation_bp.route(
    "/legacy/partial/master-po",
    methods=["GET"],
)
@login_required
def partial_master_po():
    return master_po()


@navigation_bp.route(
    "/api/legacy/master-po",
    methods=["GET"],
)
@navigation_bp.route(
    "/api/legacy/modul-po/master",
    methods=["GET"],
)
@login_required
def api_master_po_list():
    """
    GET daftar header Master PO.
    Mendukung filter nomor_kontrak, nomor_po, search dan status.
    """
    try:
        bootstrap_master_po()

        nomor_kontrak = _master_po_clean(request.args.get("nomor_kontrak"))
        nomor_po = _master_po_clean(request.args.get("nomor_po"))
        search = _master_po_clean(request.args.get("search"))
        status = _master_po_clean(request.args.get("status"))

        conditions = []
        params = []

        if nomor_kontrak:
            conditions.append("TRIM(CAST(h.nomor_kontrak AS CHAR)) = %s")
            params.append(nomor_kontrak)

        if nomor_po:
            conditions.append("TRIM(CAST(h.nomor_po AS CHAR)) = %s")
            params.append(nomor_po)

        if status:
            conditions.append("h.status_po = %s")
            params.append(status)

        if search:
            conditions.append(
                "(h.nomor_kontrak LIKE %s OR h.nomor_po LIKE %s OR h.deskripsi_po LIKE %s)"
            )
            term = f"%{search}%"
            params.extend([term, term, term])

        where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""
                    SELECT
                        h.id,
                        h.nomor_kontrak,
                        h.nomor_po,
                        h.tanggal_po,
                        h.deskripsi_po,
                        h.mata_uang,
                        h.status_po,
                        h.total_po,
                        h.update_terakhir,
                        COUNT(i.id) AS item_count
                    FROM database_master_po h
                    LEFT JOIN database_master_po_item i
                        ON i.po_id = h.id
                        AND i.status_item = 'ACTIVE'
                    {where_sql}
                    GROUP BY
                        h.id, h.nomor_kontrak, h.nomor_po, h.tanggal_po,
                        h.deskripsi_po, h.mata_uang, h.status_po,
                        h.total_po, h.update_terakhir
                    ORDER BY h.update_terakhir DESC, h.id DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
            finally:
                cursor.close()

        data = [_master_po_row_dict(row, columns) for row in rows]

        return jsonify({
            "success": True,
            "data": data,
            "total": len(data),
        })
    except Exception as error:
        LOGGER.exception("API daftar Master PO gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": [],
            "total": 0,
        }), 500


@navigation_bp.route(
    "/api/legacy/master-po/<int:po_id>",
    methods=["GET"],
)
@login_required
def api_master_po_detail(po_id):
    try:
        bootstrap_master_po()

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        id, nomor_kontrak, nomor_po, tanggal_po,
                        deskripsi_po, mata_uang, status_po, total_po,
                        update_terakhir
                    FROM database_master_po
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (po_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return jsonify({
                        "success": False,
                        "message": "Master PO tidak ditemukan.",
                        "data": {},
                        "items": [],
                    }), 404

                columns = [column[0] for column in cursor.description]
                master = _master_po_row_dict(row, columns)

                cursor.execute(
                    """
                    SELECT
                        id, po_id, nomor_kontrak, nomor_po, kategori,
                        uraian_pekerjaan, deskripsi_pekerjaan, qty, unit,
                        harga_satuan, total_harga, status_item,
                        update_terakhir
                    FROM database_master_po_item
                    WHERE po_id = %s
                      AND status_item = 'ACTIVE'
                    ORDER BY id ASC
                    """,
                    (po_id,),
                )
                item_rows = cursor.fetchall()
                item_columns = [column[0] for column in cursor.description]
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "data": master,
            "items": [
                _master_po_row_dict(row, item_columns)
                for row in item_rows
            ],
        })
    except Exception as error:
        LOGGER.exception("API detail Master PO gagal.")
        return jsonify({
            "success": False,
            "message": str(error),
            "data": {},
            "items": [],
        }), 500


@navigation_bp.route(
    "/api/legacy/master-po",
    methods=["POST"],
)
@login_required
def api_master_po_create():
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    payload = request.get_json(silent=True) or {}
    nomor_kontrak = _master_po_clean(payload.get("nomor_kontrak"))
    nomor_po = _master_po_clean(payload.get("nomor_po"))

    if not nomor_kontrak or not nomor_po:
        return jsonify({
            "success": False,
            "message": "Nomor Kontrak dan Nomor PO wajib diisi.",
        }), 400

    try:
        bootstrap_master_po()

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO database_master_po
                    (
                        nomor_kontrak, nomor_po, tanggal_po, deskripsi_po,
                        mata_uang, status_po, total_po
                    )
                    VALUES (%s, %s, NULLIF(%s, ''), %s, %s, %s, %s)
                    """,
                    (
                        nomor_kontrak,
                        nomor_po,
                        _master_po_clean(payload.get("tanggal_po")),
                        _master_po_clean(payload.get("deskripsi_po")),
                        _master_po_clean(payload.get("mata_uang")) or "IDR",
                        _master_po_clean(payload.get("status_po")) or "ACTIVE",
                        float(payload.get("total_po") or 0),
                    ),
                )
                po_id = cursor.lastrowid
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Master PO berhasil dibuat.",
            "data": {"id": po_id},
        }), 201

    except Exception as error:
        LOGGER.exception("Gagal membuat Master PO.")
        return jsonify({
            "success": False,
            "message": str(error),
        }), 500


@navigation_bp.route(
    "/api/legacy/master-po/<int:po_id>",
    methods=["PUT", "PATCH"],
)
@login_required
def api_master_po_update(po_id):
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    payload = request.get_json(silent=True) or {}

    try:
        bootstrap_master_po()

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE database_master_po
                    SET
                        nomor_kontrak = %s,
                        nomor_po = %s,
                        tanggal_po = NULLIF(%s, ''),
                        deskripsi_po = %s,
                        mata_uang = %s,
                        status_po = %s,
                        total_po = %s
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (
                        _master_po_clean(payload.get("nomor_kontrak")),
                        _master_po_clean(payload.get("nomor_po")),
                        _master_po_clean(payload.get("tanggal_po")),
                        _master_po_clean(payload.get("deskripsi_po")),
                        _master_po_clean(payload.get("mata_uang")) or "IDR",
                        _master_po_clean(payload.get("status_po")) or "ACTIVE",
                        float(payload.get("total_po") or 0),
                        po_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Master PO tidak ditemukan.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Master PO berhasil diperbarui.",
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal update Master PO.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route(
    "/api/legacy/master-po/<int:po_id>",
    methods=["DELETE"],
)
@login_required
def api_master_po_delete(po_id):
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    try:
        bootstrap_master_po()

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "DELETE FROM database_master_po WHERE id = %s LIMIT 1",
                    (po_id,),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Master PO tidak ditemukan.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Master PO berhasil dihapus.",
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal hapus Master PO.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route(
    "/api/legacy/master-po/<int:po_id>/items",
    methods=["POST"],
)
@login_required
def api_master_po_item_create(po_id):
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    payload = request.get_json(silent=True) or {}

    try:
        bootstrap_master_po()

        kategori = _master_po_clean(payload.get("kategori"))
        uraian = _master_po_clean(
            payload.get("uraian_pekerjaan")
            or payload.get("deskripsi_pekerjaan")
        )
        qty = float(payload.get("qty") or 0)
        harga = float(payload.get("harga_satuan") or 0)
        total = (
            float(payload.get("total_harga"))
            if payload.get("total_harga") not in (None, "")
            else qty * harga
        )

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT nomor_kontrak, nomor_po
                    FROM database_master_po
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (po_id,),
                )
                master = cursor.fetchone()
                if not master:
                    raise ValueError("Master PO tidak ditemukan.")

                if isinstance(master, dict):
                    nomor_kontrak = master.get("nomor_kontrak", "")
                    nomor_po = master.get("nomor_po", "")
                else:
                    nomor_kontrak, nomor_po = master

                cursor.execute(
                    """
                    INSERT INTO database_master_po_item
                    (
                        po_id, nomor_kontrak, nomor_po, kategori,
                        uraian_pekerjaan, deskripsi_pekerjaan, qty, unit,
                        harga_satuan, total_harga, status_item
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE')
                    """,
                    (
                        po_id, nomor_kontrak, nomor_po, kategori,
                        uraian, uraian, qty,
                        _master_po_clean(payload.get("unit")),
                        harga, total,
                    ),
                )
                item_id = cursor.lastrowid
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Item Master PO berhasil ditambahkan.",
            "data": {"id": item_id, "po_id": po_id},
        }), 201
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal menambah item Master PO.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route(
    "/api/legacy/master-po/items/<int:item_id>",
    methods=["PUT", "PATCH"],
)
@login_required
def api_master_po_item_update(item_id):
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    payload = request.get_json(silent=True) or {}

    try:
        bootstrap_master_po()

        qty = float(payload.get("qty") or 0)
        harga = float(payload.get("harga_satuan") or 0)
        total = (
            float(payload.get("total_harga"))
            if payload.get("total_harga") not in (None, "")
            else qty * harga
        )

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE database_master_po_item
                    SET
                        kategori = %s,
                        uraian_pekerjaan = %s,
                        deskripsi_pekerjaan = %s,
                        qty = %s,
                        unit = %s,
                        harga_satuan = %s,
                        total_harga = %s,
                        status_item = %s
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (
                        _master_po_clean(payload.get("kategori")),
                        _master_po_clean(payload.get("uraian_pekerjaan") or payload.get("deskripsi_pekerjaan")),
                        _master_po_clean(payload.get("deskripsi_pekerjaan") or payload.get("uraian_pekerjaan")),
                        qty,
                        _master_po_clean(payload.get("unit")),
                        harga,
                        total,
                        _master_po_clean(payload.get("status_item")) or "ACTIVE",
                        item_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Item Master PO tidak ditemukan.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Item Master PO berhasil diperbarui.",
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal update item Master PO.")
        return jsonify({"success": False, "message": str(error)}), 500


@navigation_bp.route(
    "/api/legacy/master-po/items/<int:item_id>",
    methods=["DELETE"],
)
@login_required
def api_master_po_item_delete(item_id):
    if not can_write():
        return jsonify({
            "success": False,
            "message": "Role Anda hanya memiliki akses baca.",
        }), 403

    try:
        bootstrap_master_po()

        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE database_master_po_item
                    SET status_item = 'INACTIVE'
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (item_id,),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Item Master PO tidak ditemukan.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        return jsonify({
            "success": True,
            "message": "Item Master PO berhasil dihapus.",
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        LOGGER.exception("Gagal hapus item Master PO.")
        return jsonify({"success": False, "message": str(error)}), 500


# ============================================================
# REKAP PO
# ============================================================

@navigation_bp.route(
    "/modul/rekap-po"
)
@login_required
def rekap_po():
    """Kompatibilitas URL lama; laporan resmi sekarang berada di Master PO."""
    return redirect(url_for("master_po.index"))


# ============================================================
# ARSIP
# ============================================================

@navigation_bp.route('/api/modul-2/document-package', methods=['GET'])
@login_required
def modul_2_document_package():
    nomor_kontrak = nilai_bersih(request.args.get('nomor_kontrak', ''))
    pi_no = nilai_bersih(request.args.get('pi', ''))
    nomor_po = nilai_bersih(request.args.get('nomor_po', ''))

    if not nomor_kontrak or not pi_no:
        return jsonify({'success': False, 'message': 'Nomor kontrak dan PI wajib dipilih.'}), 400

    try:
        target = muat_transaksi_terfilter(nomor_kontrak, pi_no)
        if not target:
            return jsonify({'success': False, 'message': 'Data transaksi tidak ditemukan.'}), 404

        # Jika PO tidak dikirim dari frontend, ambil langsung dari Master PI.
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        master = master_rows[0] if master_rows else {}
        nomor_po = nilai_bersih(
            nomor_po
            or master.get('nomor_po')
            or master.get('Nomor Purchase Order')
            or target[0].get('nomor_po')
            or target[0].get('Nomor PO')
        )

        saved_tkdn = load_saved_tkdn(nomor_kontrak, pi_no)
        saved_opname = load_opname_for_package(
            nomor_kontrak,
            pi_no,
            nomor_po,
            master_pi=master,
        )
        package_config = load_config(nomor_kontrak, pi_no)

        raw_codes = request.args.get('codes', '')
        codes = [x.strip() for x in raw_codes.split(',') if x.strip()] if raw_codes else PACKAGE_ORDER

        html = render_document_package(
            codes=codes,
            transaksi_list=target,
            nomor_kontrak=nomor_kontrak,
            pi_no=pi_no,
            nomor_po=nomor_po,
            render_generator=render_document,
            saved_tkdn=saved_tkdn,
            saved_opname=saved_opname,
            params={
                'master_pi': master,
                'nomor_po': nomor_po,
                'jenis_bastp': nilai_bersih(master.get('jenis_bastp')),
                **package_config,
            },
        )

        response = make_response(html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response

    except ValueError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as error:
        LOGGER.exception('[MODUL 2] Gagal mencetak paket dokumen.')
        return jsonify({'success': False, 'message': str(error)}), 500


@navigation_bp.route('/api/modul-2/document-package/config', methods=['GET', 'POST'])
@login_required
def modul_2_document_package_config():
    """Load/save the persistent visual configuration for Master Paket Batch."""
    body = request.get_json(silent=True) or {}
    nomor_kontrak = nilai_bersih(
        body.get('nomor_kontrak', request.args.get('nomor_kontrak', ''))
    )
    pi_no = nilai_bersih(
        body.get('pi', request.args.get('pi', request.args.get('pi_no', '')))
    )
    if not nomor_kontrak or not pi_no:
        return jsonify({'success': False, 'message': 'Nomor kontrak dan PI wajib dipilih.'}), 400

    try:
        if request.method == 'POST':
            config = save_config(nomor_kontrak, pi_no, body)
            message = 'Konfigurasi Master Paket Batch berhasil disimpan.'
        else:
            config = load_config(nomor_kontrak, pi_no)
            message = 'Konfigurasi Master Paket Batch berhasil dimuat.'
        return jsonify({
            'success': True,
            'message': message,
            'nomor_kontrak': nomor_kontrak,
            'pi': pi_no,
            'data': config,
        })
    except ValueError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except Exception as error:
        LOGGER.exception('[MODUL 2] Gagal membaca/menyimpan konfigurasi Master Paket Batch.')
        return jsonify({'success': False, 'message': str(error)}), 500

@navigation_bp.route('/api/modul-2/document-package/status', methods=['GET'])
@login_required
def modul_2_document_package_status():
    nomor_kontrak = nilai_bersih(request.args.get('nomor_kontrak', ''))
    pi_no = nilai_bersih(request.args.get('pi', ''))
    nomor_po = nilai_bersih(request.args.get('nomor_po', ''))

    if not nomor_kontrak or not pi_no:
        return jsonify({'success': False, 'message': 'Nomor kontrak dan PI wajib dipilih.'}), 400

    try:
        master_rows = muat_pi_master(nomor_kontrak, pi_no)
        master = master_rows[0] if master_rows else {}
        nomor_po = nilai_bersih(
            nomor_po
            or master.get('nomor_po')
            or master.get('Nomor Purchase Order')
        )

        tkdn = load_saved_tkdn(nomor_kontrak, pi_no)
        # Gunakan sumber data yang sama dengan renderer paket. Opname dapat
        # tersedia dari rincian PI walaupun snapshot belum disimpan manual.
        effective_opname = load_opname_for_package(
            nomor_kontrak,
            pi_no,
            nomor_po,
            master_pi=master,
        )
        opname_available = bool(effective_opname and effective_opname.get('items'))
        opname_saved = bool(effective_opname.get('_saved_snapshot_exists'))

        return jsonify({
            'success': True,
            'data': {
                'tkdn_saved': bool(tkdn),
                'opname_saved': opname_saved,
                'opname_available': opname_available,
                'package_ready': bool(tkdn or opname_available),
                'nomor_po': nomor_po,
            },
        })
    except Exception as error:
        LOGGER.exception('[MODUL 2] Gagal membaca status paket dokumen.')
        return jsonify({'success': False, 'message': str(error)}), 500

