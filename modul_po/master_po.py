import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import Blueprint, jsonify, render_template, request
import hashlib
import re
import logging
import unicodedata
from difflib import SequenceMatcher
from uuid import uuid4
from mysql.connector import Error as MySQLError
from modul_keamanan.decorators import login_required
from modul_input.modul_1_database import can_write
from db_connection import get_db_connection

master_po_bp = Blueprint(
    "master_po",
    __name__,
    url_prefix="/master-po",
    template_folder="templates",
)

_BOOTSTRAPPED = False


@master_po_bp.errorhandler(MySQLError)
def database_api_error(error):
    """Keep API failures JSON and correlate the response with server logs."""
    reference = uuid4().hex[:12]
    logging.getLogger(__name__).exception("Master PO request failed [%s]", reference)
    code = getattr(error, "errno", None)
    message = "Data PO belum dapat dimuat dari database. Silakan coba lagi."
    if code == 1267:
        message = "Database masih memiliki perbedaan collation pada query PO. Periksa log server."
    return jsonify(success=False, message=message,
                   error_code=f"PO-DB-{code or 'UNKNOWN'}-{reference}"), 500


def clean(value):
    return "" if value is None else str(value).strip()


def dec(value, default=Decimal("0")):
    try:
        text = clean(value).replace("Rp", "").replace(" ", "")
        if text.count(".") > 1 and "," not in text:
            text = text.replace(".", "")
        elif "," in text and "." in text and text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
        return Decimal(text or "0")
    except (InvalidOperation, ValueError, TypeError):
        return default


def row_dict(cursor, row):
    if isinstance(row, dict):
        return dict(row)
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _norm_text(value):
    text = clean(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def reference_category(value):
    """Canonical category key shared by Master Referensi and transactions."""
    text = _norm_text(value)
    if re.search(r"\bprofessional\s+sum\b", text):
        text = re.sub(r"\bprofessional\s+sum\b", "provisional sum", text)
    return text


def reference_uom(value):
    """Canonical UOM key used for automatic reference matching."""
    text = _norm_text(value).replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    aliases = {
        "bulan": "month", "months": "month", "month": "month",
        "hari": "day", "days": "day", "day": "day",
        "man day": "day", "man days": "day", "manday": "day",
        "ea": "ea", "each": "ea", "unit": "ea", "pcs": "ea",
        "ls": "ls", "lumpsum": "ls", "lump sum": "ls",
    }
    return aliases.get(text, text)


def reference_description(value, category=""):
    """Normalize harmless naming differences without changing business meaning."""
    text = unicodedata.normalize("NFKC", clean(value)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Older PO uploads appended the UOM to personnel descriptions.
    if _norm_text(category) == "personel on call":
        text = re.sub(r"\s+man\s+days?$", "", text)
    return text


def reference_key(category, description, uom):
    """Return the stable identity of a Master Referensi line."""
    category_key = reference_category(category)
    if is_provisional_category(category_key):
        return ("provisional sum", "", "")
    return (category_key, reference_description(description, category_key), reference_uom(uom))


def reference_note(value):
    """Canonical key for the business context of a Provisional Sum line."""
    text = unicodedata.normalize("NFKC", clean(value)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def reference_note_similarity(left, right):
    left_key, right_key = reference_note(left), reference_note(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def is_provisional_category(value):
    """Professional Sum is a legacy spelling of the same special category."""
    return bool(re.search(r"\b(?:provisional|professional)\s+sum\b", _norm_text(value)))


def is_estimated_category(value):
    """Return true for Estimated Sum categories (English or Indonesian)."""
    return bool(re.search(r"\b(?:estimated|estimasi)\s+sum\b", _norm_text(value)))


def is_amount_uom(value):
    """Identify UOMs whose Provisional Sum plafon is already a currency amount.

    ``LS`` is intentionally treated as a countable lump-sum unit; ``AU`` is
    the application's nominal/amount unit.
    """
    key = re.sub(r"[^a-z0-9]+", "", _norm_text(value))
    return key in {"au", "amount", "nominal", "idr", "rp", "rupiah"}


def po_plafon_factor(category, uom=""):
    """Return the PO budget factor used consistently by Master PO and Opname."""
    if is_estimated_category(category):
        return Decimal("0.90")
    if is_provisional_category(category):
        return Decimal("1.15")
    return Decimal("1")


def calculate_po_plafon(qty, unit_price, category, uom=""):
    """Calculate the PO ceiling from quantity, at-cost price, and category rule."""
    amount = dec(qty) * dec(unit_price) * po_plafon_factor(category, uom)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _schema_sql():
    return """
    CREATE TABLE IF NOT EXISTS database_master_po (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        kontrak_id BIGINT UNSIGNED NULL,
        nomor_kontrak VARCHAR(100) NOT NULL,
        nomor_po VARCHAR(100) NOT NULL,
        tanggal_po DATE NULL,
        status_po VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
        version_no INT NOT NULL DEFAULT 1,
        deleted_at DATETIME NULL,
        total_plafon DECIMAL(20,2) NOT NULL DEFAULT 0,
        keterangan TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_master_po_kontrak_po (nomor_kontrak, nomor_po),
        KEY idx_master_po_version (nomor_kontrak, nomor_po, version_no),
        KEY idx_master_po_status (status_po),
        KEY idx_master_po_deleted (deleted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    CREATE TABLE IF NOT EXISTS database_master_po_item (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        po_id BIGINT UNSIGNED NOT NULL,
        item_no INT NOT NULL,
        kategori VARCHAR(255) NULL,
        deskripsi_pekerjaan TEXT NOT NULL,
        uom VARCHAR(50) NULL,
        qty_po DECIMAL(20,6) NOT NULL DEFAULT 0,
        unit_price DECIMAL(20,2) NOT NULL DEFAULT 0,
        total_plafon DECIMAL(20,2) NOT NULL DEFAULT 0,
        keterangan TEXT NULL,
        status_item VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_po_item_no (po_id, item_no),
        KEY idx_po_item_po (po_id),
        CONSTRAINT fk_po_item_po FOREIGN KEY (po_id) REFERENCES database_master_po(id)
            ON UPDATE CASCADE ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    CREATE TABLE IF NOT EXISTS database_po_realisasi (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        kontrak_id BIGINT UNSIGNED NULL,
        po_item_id BIGINT UNSIGNED NULL,
        nomor_kontrak VARCHAR(100) NOT NULL,
        nomor_po VARCHAR(100) NOT NULL,
        nomor_pi VARCHAR(100) NOT NULL,
        tanggal_pi VARCHAR(50) NULL,
        qty_realisasi DECIMAL(20,6) NOT NULL DEFAULT 0,
        unit_price DECIMAL(20,2) NOT NULL DEFAULT 0,
        nilai_realisasi DECIMAL(20,2) NOT NULL DEFAULT 0,
        sumber_transaksi_id VARCHAR(255) NULL,
        kategori VARCHAR(255) NULL,
        deskripsi_pekerjaan TEXT NULL,
        uom VARCHAR(50) NULL,
        mapping_status VARCHAR(30) NOT NULL DEFAULT 'MAPPED',
        keterangan TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_po_realisasi_item (po_item_id),
        KEY idx_po_realisasi_pi (nomor_pi),
        CONSTRAINT fk_po_realisasi_item FOREIGN KEY (po_item_id) REFERENCES database_master_po_item(id)
            ON UPDATE CASCADE ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    CREATE TABLE IF NOT EXISTS database_po_mapping_rule (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        kontrak_id BIGINT UNSIGNED NULL,
        nomor_kontrak VARCHAR(100) NOT NULL,
        nomor_po VARCHAR(100) NOT NULL,
        fingerprint CHAR(64) NOT NULL,
        kategori VARCHAR(255) NULL,
        deskripsi_pekerjaan TEXT NOT NULL,
        uom VARCHAR(50) NULL,
        po_item_id BIGINT UNSIGNED NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_po_mapping_rule (nomor_kontrak, nomor_po, fingerprint),
        KEY idx_po_mapping_item (po_item_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """


def ensure_master_po_schema(conn):
    cur = conn.cursor()
    try:
        for statement in _schema_sql().split(";\n\n"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
        # Existing installations may still have the old UNIQUE(contract, PO)
        # constraint. Versioned Master PO needs the same nomor_po to be reused
        # after an old Master PO is archived/deleted.
        try:
            cur.execute("ALTER TABLE database_master_po DROP INDEX uq_master_po_kontrak_po")
        except Exception as exc:
            msg = str(exc).lower()
            if "check that column/key exists" not in msg and "can't drop" not in msg and "unknown key" not in msg:
                raise
        for ddl in [
            "ALTER TABLE database_master_po ADD COLUMN version_no INT NOT NULL DEFAULT 1 AFTER status_po",
            "ALTER TABLE database_master_po ADD COLUMN deleted_at DATETIME NULL AFTER version_no",
            "ALTER TABLE database_master_po ADD COLUMN kontrak_id BIGINT UNSIGNED NULL AFTER id",
            "ALTER TABLE database_master_po_item ADD COLUMN keterangan TEXT NULL AFTER total_plafon",
        ]:
            try:
                cur.execute(ddl)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        for table in ("database_po_realisasi", "database_po_mapping_rule"):
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN kontrak_id BIGINT UNSIGNED NULL AFTER id")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        # Existing rows become version 1.
        cur.execute("UPDATE database_master_po SET version_no=1 WHERE version_no IS NULL OR version_no < 1")

        # Existing transaction table has no PK/id in the current dump, so the
        # PO relation is deliberately nullable and does not use a FK to a row id.
        try:
            cur.execute("ALTER TABLE database_transaksi_rincian ADD COLUMN po_item_id BIGINT UNSIGNED NULL AFTER nomor_po")
        except Exception as exc:
            if "Duplicate column" not in str(exc) and "duplicate column" not in str(exc).lower():
                raise
        for ddl in [
            "ALTER TABLE database_po_realisasi MODIFY COLUMN po_item_id BIGINT UNSIGNED NULL",
            # Older installations may have created this source column as a
            # numeric transaction ID. The ledger now stores a stable SHA-256
            # source fingerprint; a numeric column coerces every fingerprint
            # to 0 and causes duplicate keys such as `57-0` for shared PO items.
            "ALTER TABLE database_po_realisasi MODIFY COLUMN sumber_transaksi_id VARCHAR(255) NULL",
            "ALTER TABLE database_po_realisasi ADD COLUMN kategori VARCHAR(255) NULL AFTER sumber_transaksi_id",
            "ALTER TABLE database_po_realisasi ADD COLUMN deskripsi_pekerjaan TEXT NULL AFTER sumber_transaksi_id",
            "ALTER TABLE database_po_realisasi ADD COLUMN uom VARCHAR(50) NULL AFTER deskripsi_pekerjaan",
            "ALTER TABLE database_po_realisasi ADD COLUMN mapping_status VARCHAR(30) NOT NULL DEFAULT 'MAPPED' AFTER uom",
        ]:
            try:
                cur.execute(ddl)
            except Exception as exc:
                msg = str(exc).lower()
                if not any(x in msg for x in ("duplicate column", "duplicate key", "already exists")):
                    # MODIFY can be safely ignored if table is not present yet; schema creation above handles it.
                    if "database_po_realisasi" not in msg and "unknown table" not in msg:
                        raise

        try:
            cur.execute("ALTER TABLE database_transaksi_rincian ADD KEY idx_transaksi_po_item (po_item_id)")
        except Exception as exc:
            if "Duplicate key" not in str(exc) and "duplicate key" not in str(exc).lower():
                raise
        # Selaraskan PO lama dengan rumus kategori yang sama dengan form,
        # mapping Opname, dan render dokumen.
        cur.execute("""
            SELECT i.id, i.kategori, i.uom, i.qty_po, i.unit_price, i.total_plafon
            FROM database_master_po_item i
            JOIN database_master_po h ON h.id=i.po_id
            WHERE i.status_item='ACTIVE' AND h.status_po IN ('DRAFT','ACTIVE')
        """)
        legacy_items = [row_dict(cur, raw) for raw in cur.fetchall()]
        for item in legacy_items:
            expected = calculate_po_plafon(
                item.get("qty_po"), item.get("unit_price"),
                item.get("kategori"), item.get("uom"),
            )
            if abs(dec(item.get("total_plafon")) - expected) > Decimal("0.005"):
                cur.execute(
                    "UPDATE database_master_po_item SET total_plafon=%s WHERE id=%s",
                    (expected, item.get("id")),
                )
        cur.execute("""
            UPDATE database_master_po h
            LEFT JOIN (
                SELECT po_id, COALESCE(SUM(total_plafon),0) AS total
                FROM database_master_po_item WHERE status_item='ACTIVE' GROUP BY po_id
            ) x ON x.po_id=h.id
            SET h.total_plafon=COALESCE(x.total,0)
            WHERE h.status_po IN ('DRAFT','ACTIVE')
              AND ABS(h.total_plafon-COALESCE(x.total,0))>0.005
        """)
        conn.commit()
    finally:
        cur.close()


def bootstrap_master_po(force=False):
    """Initialize Master PO tables directly in MySQL.

    No Excel, CSV, or local file is used here. The online application uses
    MySQL as the only source of truth for Master PO, PO items, and realization
    history.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED and not force:
        return

    with get_db_connection() as conn:
        ensure_master_po_schema(conn)

    _BOOTSTRAPPED = True


@master_po_bp.get("/")
@login_required
def index():
    bootstrap_master_po()
    return render_template("master_po.html", title="Master PO", module_code="MASTER PO", can_write=can_write())


@master_po_bp.get("/api")
@login_required
def api_list():
    bootstrap_master_po()
    q = clean(request.args.get("q"))
    contract = clean(request.args.get("nomor_kontrak"))
    status = clean(request.args.get("status_po"))
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 25, type=int), 1), 100)
    offset = (page - 1) * per_page

    # Alias h wajib dipakai karena query list melakukan JOIN ke master_kontrak
    # yang juga memiliki kolom nomor_kontrak.
    # Filter utama memakai alias h karena query data melakukan JOIN.
    # Filter COUNT dibuat terpisah tanpa alias agar tetap valid pada tabel tunggal.
    where, count_where, params = [
        "LOWER(TRIM(COALESCE(h.nomor_kontrak, ''))) <> 'nomor kontrak'"
    ], [
        "LOWER(TRIM(COALESCE(nomor_kontrak, ''))) <> 'nomor kontrak'"
    ], []
    if q:
        # Kolom pencarian halaman Master PO khusus untuk nomor PO.
        # Filter kontrak dikirim terpisah melalui nomor_kontrak.
        where.append("h.nomor_po LIKE %s")
        count_where.append("nomor_po LIKE %s")
        params.append(f"%{q}%")
    if contract:
        where.append("h.nomor_kontrak = %s")
        count_where.append("nomor_kontrak = %s")
        params.append(contract)
    if status:
        where.append("h.status_po = %s")
        count_where.append("status_po = %s")
        params.append(status)
    else:
        where.append("h.status_po NOT IN ('DELETED','CANCELLED')")
        count_where.append("status_po NOT IN ('DELETED','CANCELLED')")

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            base = " AND ".join(where)
            count_base = " AND ".join(count_where)
            cur.execute(f"SELECT COUNT(*) AS total FROM database_master_po WHERE {count_base}", tuple(params))
            total = row_dict(cur, cur.fetchone())["total"]
            cur.execute(f"""
                SELECT h.id, h.kontrak_id, h.nomor_kontrak, h.nomor_po, h.tanggal_po,
                       h.status_po, h.version_no, h.deleted_at, h.total_plafon,
                       COALESCE(h.total_plafon, 0) AS total_plafon_kontrak,
                       (SELECT COUNT(*)
                        FROM database_master_po h2
                        WHERE h2.nomor_kontrak = h.nomor_kontrak
                          AND h2.status_po NOT IN ('DELETED','CANCELLED')) AS jumlah_po_kontrak,
                       (SELECT COUNT(*) FROM database_master_po_item i
                        WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS jumlah_item,
                       (SELECT COALESCE(SUM(i.qty_po),0) FROM database_master_po_item i
                        WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS total_qty,
                       (SELECT COALESCE(SUM(r.nilai_realisasi),0)
                        FROM database_po_realisasi r
                        JOIN database_master_po_item i ON i.id=r.po_item_id
                        WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS nilai_realisasi
                FROM database_master_po h
                WHERE {base}
                ORDER BY h.id DESC
                LIMIT %s OFFSET %s
            """, tuple(params + [per_page, offset]))
            rows = [row_dict(cur, r) for r in cur.fetchall()]
            return jsonify({"success": True, "data": rows, "pagination": {
                "page": page, "per_page": per_page, "total": total,
                "pages": (int(total) + per_page - 1) // per_page
            }})
        finally:
            cur.close()


@master_po_bp.get("/api/analytics")
@login_required
def api_analytics():
    """Rekap penyerapan PO sebagai data analitik resmi Master PO.

    Endpoint ini menggantikan perhitungan laporan legacy di sisi UI. Semua
    angka plafon dan realisasi dihitung dari item aktif dan realisasi yang
    sudah terhubung ke item tersebut.
    """
    bootstrap_master_po()
    contract = clean(request.args.get("nomor_kontrak"))
    po_number = clean(request.args.get("nomor_po"))
    category = clean(request.args.get("kategori"))
    status = clean(request.args.get("status_po"))

    where = ["h.status_po <> 'DELETED'" if status else "h.status_po NOT IN ('DELETED','CANCELLED')"]
    params = []
    search = clean(request.args.get("q"))
    if search:
        where.append("h.nomor_po LIKE %s")
        params.append(f"%{search}%")
    if contract:
        where.append("TRIM(h.nomor_kontrak) = %s")
        params.append(contract)
    if po_number:
        where.append("TRIM(h.nomor_po) = %s")
        params.append(po_number)
    if category:
        where.append("LOWER(TRIM(COALESCE(i.kategori,''))) = LOWER(TRIM(%s))")
        params.append(category)
    if status:
        where.append("h.status_po = %s")
        params.append(status)

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT
                    h.nomor_kontrak, h.nomor_po, h.tanggal_po, h.status_po,
                    i.id AS po_item_id, i.item_no, i.kategori,
                    i.deskripsi_pekerjaan, i.uom, i.qty_po, i.unit_price,
                    i.total_plafon,
                    COALESCE(SUM(CASE WHEN r.mapping_status='MAPPED'
                                      THEN r.qty_realisasi ELSE 0 END),0) AS qty_realisasi,
                    COALESCE(SUM(CASE WHEN r.mapping_status='MAPPED'
                                      THEN r.nilai_realisasi ELSE 0 END),0) AS nilai_realisasi,
                    COUNT(DISTINCT CASE WHEN r.mapping_status='MAPPED'
                                        THEN r.nomor_pi END) AS jumlah_pi
                FROM database_master_po h
                JOIN database_master_po_item i
                  ON i.po_id=h.id AND i.status_item='ACTIVE'
                LEFT JOIN database_po_realisasi r ON r.po_item_id=i.id
                WHERE {' AND '.join(where)}
                GROUP BY h.nomor_kontrak, h.nomor_po, h.tanggal_po, h.status_po,
                         i.id, i.item_no, i.kategori, i.deskripsi_pekerjaan,
                         i.uom, i.qty_po, i.unit_price, i.total_plafon
                ORDER BY h.nomor_kontrak, h.nomor_po, i.item_no
            """, tuple(params))
            rows = [row_dict(cur, r) for r in cur.fetchall()]
        finally:
            cur.close()

    def number(value):
        return float(value or 0)

    for row in rows:
        plafond = number(row.get("total_plafon"))
        qty_po = number(row.get("qty_po"))
        nilai = number(row.get("nilai_realisasi"))
        qty = number(row.get("qty_realisasi"))
        row["qty_po"] = qty_po
        row["total_plafon"] = plafond
        row["qty_realisasi"] = qty
        row["nilai_realisasi"] = nilai
        row["sisa_qty"] = qty_po - qty
        row["sisa_nilai"] = plafond - nilai
        row["persen_qty"] = round((qty / qty_po * 100) if qty_po else 0, 2)
        row["persen_nilai"] = round((nilai / plafond * 100) if plafond else 0, 2)

    summary = {
        "jumlah_po": len({(r.get("nomor_kontrak"), r.get("nomor_po")) for r in rows}),
        "jumlah_item": len(rows),
        "total_plafon": round(sum(number(r.get("total_plafon")) for r in rows), 2),
        "total_realisasi": round(sum(number(r.get("nilai_realisasi")) for r in rows), 2),
        "total_qty_po": round(sum(number(r.get("qty_po")) for r in rows), 6),
        "total_qty_realisasi": round(sum(number(r.get("qty_realisasi")) for r in rows), 6),
    }
    summary["sisa_nilai"] = round(summary["total_plafon"] - summary["total_realisasi"], 2)
    summary["sisa_qty"] = round(summary["total_qty_po"] - summary["total_qty_realisasi"], 6)
    summary["persen_nilai"] = round((summary["total_realisasi"] / summary["total_plafon"] * 100)
                                    if summary["total_plafon"] else 0, 2)
    summary["persen_qty"] = round((summary["total_qty_realisasi"] / summary["total_qty_po"] * 100)
                                  if summary["total_qty_po"] else 0, 2)

    return jsonify({"success": True, "summary": summary, "data": rows})


@master_po_bp.get("/api/contracts")
@login_required
def api_contract_summary():
    """Ringkasan kontrak sebagai induk Master PO."""
    bootstrap_master_po()
    q = clean(request.args.get("q"))
    where, params = [
        "TRIM(COALESCE(k.nomor_kontrak, '')) <> ''",
        "LOWER(TRIM(k.nomor_kontrak)) <> 'nomor kontrak'"
    ], []
    if q:
        where.append("(k.nomor_kontrak LIKE %s OR k.judul_kontrak LIKE %s OR k.customer LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT
                    k.id AS kontrak_id,
                    k.nomor_kontrak,
                    k.nomor_tender,
                    k.judul_kontrak,
                    k.customer,
                    k.tanggal_mulai,
                    k.tanggal_selesai,
                    k.plafon_kontrak,
                    k.status_kontrak,
                    (SELECT COUNT(*)
                     FROM database_master_po p
                     WHERE (p.kontrak_id=k.id OR
                            CONVERT(TRIM(p.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                            CONVERT(TRIM(k.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci)
                       AND p.status_po NOT IN ('DELETED','CANCELLED')) AS jumlah_po,
                    (SELECT COALESCE(SUM(p.total_plafon),0)
                     FROM database_master_po p
                     WHERE (p.kontrak_id=k.id OR
                            CONVERT(TRIM(p.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                            CONVERT(TRIM(k.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci)
                       AND p.status_po NOT IN ('DELETED','CANCELLED')) AS total_plafon_po,
                    (SELECT COALESCE(SUM(r.nilai_realisasi),0)
                     FROM database_po_realisasi r
                     WHERE (r.kontrak_id=k.id OR
                            CONVERT(TRIM(r.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                            CONVERT(TRIM(k.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci)
                       AND r.mapping_status='MAPPED') AS total_realisasi,
                    (SELECT COALESCE(SUM(i.total_netto),0)
                     FROM database_master_invoice i
                     WHERE i.kontrak_id=k.id
                       AND i.status_invoice <> 'DIBATALKAN') AS total_invoice,
                    (SELECT COALESCE(SUM(pay.nominal_dibayar),0)
                     FROM database_invoice_payment pay
                     JOIN database_master_invoice i ON i.id=pay.invoice_id
                     WHERE i.kontrak_id=k.id) AS total_dibayar
                FROM master_kontrak k
                WHERE {' AND '.join(where)}
                ORDER BY k.nomor_kontrak ASC
            """, tuple(params))
            rows = [row_dict(cur, r) for r in cur.fetchall()]
            return jsonify({"success": True, "data": rows})
        finally:
            cur.close()



@master_po_bp.get("/api/reference-options")
@login_required
def api_reference_options():
    """Master pilihan untuk item PO.

    Sumber utama:
    database_master_referensi (Modul 0).
    UOM juga menggabungkan unit yang sudah muncul pada transaksi agar data
    lama tetap bisa dipilih.
    """
    bootstrap_master_po()

    category = clean(request.args.get("kategori"))

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT DISTINCT TRIM(kategori) AS kategori
                FROM database_master_referensi
                WHERE kategori IS NOT NULL AND TRIM(kategori) <> ''
                ORDER BY kategori ASC
            """)
            categories = [
                clean(row_dict(cur, r).get("kategori"))
                for r in cur.fetchall()
            ]
            if not any(_norm_text(x) == "provisional sum" for x in categories):
                categories.append("PROVISIONAL SUM")

            if category:
                cur.execute("""
                    SELECT
                        TRIM(uraian_pekerjaan) AS deskripsi_pekerjaan,
                        TRIM(unit) AS uom,
                        harga_satuan
                    FROM database_master_referensi
                    WHERE LOWER(TRIM(kategori)) = LOWER(TRIM(%s))
                    ORDER BY uraian_pekerjaan ASC
                """, (category,))
            else:
                cur.execute("""
                    SELECT
                        TRIM(uraian_pekerjaan) AS deskripsi_pekerjaan,
                        TRIM(unit) AS uom,
                        harga_satuan,
                        TRIM(kategori) AS kategori
                    FROM database_master_referensi
                    WHERE uraian_pekerjaan IS NOT NULL
                      AND TRIM(uraian_pekerjaan) <> ''
                    ORDER BY kategori ASC, uraian_pekerjaan ASC
                """)

            descriptions = [row_dict(cur, r) for r in cur.fetchall()]
            if is_provisional_category(category) and not descriptions:
                descriptions = [{"deskripsi_pekerjaan": "Provisional Sum (At Cost + Fee 15%)",
                                 "uom": "AU", "harga_satuan": 0}]

            cur.execute("""
                SELECT DISTINCT uom
                FROM (
                    SELECT TRIM(unit) AS uom
                    FROM database_master_referensi
                    WHERE unit IS NOT NULL AND TRIM(unit) <> ''

                    UNION

                    SELECT TRIM(unit) AS uom
                    FROM database_transaksi_rincian
                    WHERE unit IS NOT NULL AND TRIM(unit) <> ''
                ) x
                WHERE uom IS NOT NULL AND uom <> ''
                ORDER BY uom ASC
            """)
            uoms = [
                clean(row_dict(cur, r).get("uom"))
                for r in cur.fetchall()
            ]

            # UOM standar tetap tersedia walaupun belum pernah ada di master.
            for value in (
                "Month", "Day", "Ls", "Unit", "Trip",
                "Jam", "AU", "Kwh", "Orang", "Set",
                "Pallet", "Pcs", "Ea", "m3"
            ):
                if value not in uoms:
                    uoms.append(value)

            return jsonify({
                "success": True,
                "data": {
                    "kategori": [x for x in categories if x],
                    "deskripsi_pekerjaan": descriptions,
                    "uom": sorted(set(x for x in uoms if x), key=str.lower),
                }
            })
        finally:
            cur.close()


@master_po_bp.get("/api/reference-descriptions")
@login_required
def api_reference_descriptions():
    """Deskripsi pekerjaan berdasarkan kategori."""
    bootstrap_master_po()
    category = clean(request.args.get("kategori"))

    if not category:
        return jsonify({
            "success": False,
            "message": "Kategori wajib diberikan."
        }), 400

    if is_provisional_category(category):
        return jsonify({"success": True, "data": [{
            "deskripsi_pekerjaan": "Provisional Sum (At Cost + Fee 15%)",
            "uom": "AU", "harga_satuan": 0,
        }]})

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT
                    TRIM(uraian_pekerjaan) AS deskripsi_pekerjaan,
                    TRIM(unit) AS uom,
                    harga_satuan
                FROM database_master_referensi
                WHERE LOWER(TRIM(kategori)) = LOWER(TRIM(%s))
                  AND uraian_pekerjaan IS NOT NULL
                  AND TRIM(uraian_pekerjaan) <> ''
                ORDER BY uraian_pekerjaan ASC
            """, (category,))
            return jsonify({
                "success": True,
                "data": [row_dict(cur, r) for r in cur.fetchall()]
            })
        finally:
            cur.close()


@master_po_bp.get("/api/<int:po_id>")
@login_required
def api_detail(po_id):
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            header = get_po_header(cur, po_id)
            if not header:
                return jsonify({"success": False, "message": "PO tidak ditemukan."}), 404
            cur.execute("""
                SELECT i.id, i.item_no, i.kategori, i.deskripsi_pekerjaan,
                       i.uom, i.qty_po, i.unit_price, i.total_plafon, i.keterangan,
                       i.status_item,
                       COALESCE(SUM(r.qty_realisasi),0) AS qty_used,
                       COALESCE(SUM(r.nilai_realisasi),0) AS nilai_used
                FROM database_master_po_item i
                LEFT JOIN database_po_realisasi r ON r.po_item_id=i.id
                WHERE i.po_id=%s
                GROUP BY i.id, i.item_no, i.kategori, i.deskripsi_pekerjaan,
                         i.uom, i.qty_po, i.unit_price, i.total_plafon, i.keterangan, i.status_item
                ORDER BY i.item_no ASC
            """, (po_id,))
            items = [row_dict(cur, r) for r in cur.fetchall()]

            # Kompatibilitas data lama: sebelum keterangan menjadi kolom item
            # Master PO, nilainya hanya ada pada rincian transaksi PI.
            cur.execute("SELECT nomor_kontrak, nomor_po FROM database_master_po WHERE id=%s", (po_id,))
            po_header_row = cur.fetchone()
            po_header = row_dict(cur, po_header_row) if po_header_row else {}
            for item in items:
                if clean(item.get("keterangan")):
                    continue
                cur.execute("""
                    SELECT GROUP_CONCAT(DISTINCT NULLIF(TRIM(t.keterangan),'') SEPARATOR ' | ') AS keterangan
                    FROM database_transaksi_rincian t
                    WHERE TRIM(CAST(t.nomor_kontrak AS CHAR))=%s
                      AND TRIM(CAST(t.nomor_po AS CHAR))=%s
                      AND LOWER(TRIM(COALESCE(t.kategori,'')))=LOWER(TRIM(COALESCE(%s,'')))
                      AND LOWER(TRIM(COALESCE(t.deskripsi_pekerjaan,'')))=LOWER(TRIM(COALESCE(%s,'')))
                      AND LOWER(TRIM(COALESCE(t.unit,'')))=LOWER(TRIM(COALESCE(%s,'')))
                      AND TRIM(COALESCE(t.keterangan,''))<>''
                """, (po_header.get("nomor_kontrak"), po_header.get("nomor_po"),
                      item.get("kategori"), item.get("deskripsi_pekerjaan"), item.get("uom")))
                fallback = row_dict(cur, cur.fetchone() or {}).get("keterangan")
                if clean(fallback):
                    item["keterangan"] = fallback
            return jsonify({"success": True, "data": {"header": header, "items": items}})
        finally:
            cur.close()


@master_po_bp.get("/api/item/<int:item_id>/history")
@login_required
def api_item_history(item_id):
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT r.id, r.nomor_kontrak, r.nomor_pi, r.tanggal_pi, r.qty_realisasi,
                       r.unit_price, r.nilai_realisasi, r.sumber_transaksi_id,
                       r.keterangan
                FROM database_po_realisasi r
                WHERE r.po_item_id=%s
                ORDER BY r.tanggal_pi ASC, r.id ASC
            """, (item_id,))
            rows = [row_dict(cur, r) for r in cur.fetchall()]
            return jsonify({"success": True, "data": rows})
        finally:
            cur.close()


@master_po_bp.get("/api/unmapped")
@login_required
def api_unmapped():
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur=conn.cursor()
        try:
            cur.execute("""
                SELECT nomor_kontrak, nomor_po, MIN(nomor_pi) AS sample_pi,
                       COUNT(*) AS jumlah_realisasi,
                       COUNT(DISTINCT nomor_pi) AS jumlah_pi,
                       COALESCE(SUM(qty_realisasi),0) AS total_qty,
                       COALESCE(SUM(nilai_realisasi),0) AS total_nilai
                FROM database_po_realisasi
                WHERE mapping_status='UNMAPPED'
                GROUP BY nomor_kontrak, nomor_po
                ORDER BY MAX(id) DESC
            """)
            return jsonify({"success":True,"data":[row_dict(cur,r) for r in cur.fetchall()]})
        finally: cur.close()


@master_po_bp.post("/api/register-from-history")
@login_required
def api_register_from_history():
    """Buat Draft Master PO sebagai versi baru dari referensi transaksi terbaru.

    Master PO lama tidak dihidupkan kembali. Jika nomor PO yang sama pernah
    dihapus/diarsipkan, sistem membuat version_no berikutnya dan mempertahankan
    seluruh histori PI yang sudah terhubung ke versi lama.
    """
    bootstrap_master_po()
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    payload = request.get_json(silent=True) or {}
    kontrak = clean(payload.get("nomor_kontrak"))
    nomor_po = clean(payload.get("nomor_po"))
    if not kontrak:
        return jsonify({"success": False, "message": "Nomor kontrak wajib diisi."}), 400
    if not _is_real_po(nomor_po):
        return jsonify({"success": False, "message": "Nomor PO tidak valid."}), 400

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            # Jika masih ada Master PO aktif/draft untuk nomor yang sama,
            # gunakan kembali. Jangan membuat duplikasi aktif.
            cur.execute("""
                SELECT id, status_po, version_no
                FROM database_master_po
                WHERE nomor_kontrak=%s AND nomor_po=%s
                  AND status_po IN ('DRAFT','ACTIVE')
                ORDER BY version_no DESC, id DESC
                LIMIT 1
            """, (kontrak, nomor_po))
            active_row = cur.fetchone()
            if active_row:
                active = row_dict(cur, active_row)
                return jsonify({
                    "success": True,
                    "po_id": active["id"],
                    "status": active["status_po"],
                    "version_no": active.get("version_no", 1),
                    "reused": True,
                    "inserted_items": 0,
                    "message": f"Master PO {nomor_po} versi {active.get('version_no', 1)} masih aktif dan digunakan kembali."
                })

            # Tentukan nomor versi baru.
            cur.execute("""
                SELECT COALESCE(MAX(version_no),0) AS max_version
                FROM database_master_po
                WHERE nomor_kontrak=%s AND nomor_po=%s
            """, (kontrak, nomor_po))
            version_no = int(row_dict(cur, cur.fetchone())["max_version"] or 0) + 1

            # Ambil snapshot dari transaksi terbaru. Satu kombinasi item
            # hanya mengambil baris referensi paling baru, bukan menjumlahkan
            # seluruh PI lama. Ini mencegah qty PO baru membesar karena histori.
            cur.execute("""
                SELECT
                    TRIM(COALESCE(kategori, '')) AS kategori,
                    TRIM(COALESCE(deskripsi_pekerjaan, '')) AS deskripsi_pekerjaan,
                    TRIM(COALESCE(unit, '')) AS uom,
                    COALESCE(qty, 0) AS qty_po_awal,
                    COALESCE(harga_satuan, 0) AS unit_price,
                    COALESCE(total_harga, 0) AS nilai_transaksi,
                    TRIM(COALESCE(keterangan, '')) AS keterangan,
                    tanggal_po,
                    update_terakhir,
                    tanggal_pi
                FROM database_transaksi_rincian
                WHERE TRIM(nomor_kontrak)=%s
                  AND TRIM(nomor_po)=%s
                  AND TRIM(COALESCE(deskripsi_pekerjaan,''))<>''
                ORDER BY COALESCE(update_terakhir, tanggal_pi, tanggal_po) DESC
            """, (kontrak, nomor_po))
            raw_rows = [row_dict(cur, r) for r in cur.fetchall()]

            # Deduplikasi berdasarkan item; baris pertama adalah referensi terbaru.
            latest_items = {}
            for row in raw_rows:
                provisional = is_provisional_category(row.get("kategori"))
                key = (("provisional sum", clean(row.get("keterangan")).lower(),
                        clean(row.get("deskripsi_pekerjaan")).lower()) if provisional else (
                    clean(row.get("kategori")).lower(),
                    clean(row.get("deskripsi_pekerjaan")).lower(),
                    clean(row.get("uom")).lower(),
                ))
                if (provisional or key[1]) and key not in latest_items:
                    latest_items[key] = row

            latest_items = list(latest_items.values())
            if not latest_items:
                return jsonify({
                    "success": False,
                    "message": f"Tidak ditemukan referensi transaksi untuk PO {nomor_po}."
                }), 404

            latest_header_date = next((clean(r.get("tanggal_po")) for r in latest_items if clean(r.get("tanggal_po"))), None)

            cur.execute("""
                INSERT INTO database_master_po
                    (nomor_kontrak, nomor_po, tanggal_po, status_po, version_no, deleted_at, keterangan)
                VALUES (%s,%s,%s,'DRAFT',%s,NULL,%s)
            """, (
                kontrak, nomor_po, latest_header_date,
                version_no,
                f"Draft versi {version_no} dibuat dari referensi transaksi terbaru."
            ))
            po_id = cur.lastrowid

            inserted_items = 0
            provisional_added = False
            for index, row in enumerate(latest_items, 1):
                kategori = clean(row.get("kategori"))
                desc = clean(row.get("deskripsi_pekerjaan"))
                uom = clean(row.get("uom"))
                keterangan = clean(row.get("keterangan"))
                qty = dec(row.get("qty_po_awal"))
                price = dec(row.get("unit_price"))
                if is_provisional_category(kategori):
                    # Nilai awal dapat diedit sesuai dokumen PO. Pertahankan
                    # uraian, UOM, dan keterangan PI agar beberapa PS tetap
                    # dapat dibedakan.
                    kategori, desc, uom = "PROVISIONAL SUM", desc or "Provisional Sum (At Cost + Fee 15%)", uom or "AU"
                    provisional_added = True
                total = calculate_po_plafon(qty, price, kategori, uom)
                cur.execute("""
                    INSERT INTO database_master_po_item
                        (po_id,item_no,kategori,deskripsi_pekerjaan,uom,qty_po,unit_price,total_plafon,keterangan,status_item)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                """, (po_id,index,kategori,desc,uom,qty,price,total,keterangan))
                inserted_items += 1

            refresh_po_total(cur, po_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    # Mapping histori lama tetap dilakukan tanpa mengubah referensi versi lama.
    mapping_warning = ("Periksa Qty dan Harga Satuan Provisional Sum sesuai dokumen PO sebelum mapping; "
                       "keterangan PI sudah dibawa ke item Master PO.") if provisional_added else None
    remapped = 0
    try:
        remapped = remap_unmapped_realizations(kontrak, nomor_po)
    except Exception as exc:
        mapping_warning = (mapping_warning + " " if mapping_warning else "") + "Draft berhasil dibuat, tetapi pemetaan histori belum selesai: " + str(exc)

    return jsonify({
        "success": True,
        "po_id": po_id,
        "status": "DRAFT",
        "version_no": version_no,
        "reused": False,
        "inserted_items": inserted_items,
        "remapped": remapped,
        "warning": mapping_warning,
        "message": (
            f"Draft Master PO {nomor_po} versi {version_no} berhasil dibuat dari referensi transaksi terbaru. "
            f"{inserted_items} item otomatis diisi dan tetap dapat diedit sebelum final."
            + (" Isi plafon Provisional Sum sesuai dokumen PO sebelum mapping."
               if provisional_added else "")
        )
    })


@master_po_bp.get("/api/<int:po_id>/delete-impact")
@login_required
def api_delete_impact(po_id):
    """Kembalikan dampak arsip PO sebelum pengguna melakukan konfirmasi."""
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, nomor_kontrak, nomor_po, version_no, status_po
                FROM database_master_po WHERE id=%s LIMIT 1
            """, (po_id,))
            header = cur.fetchone()
            if not header:
                return jsonify({"success": False, "message": "Master PO tidak ditemukan."}), 404
            target = row_dict(cur, header)
            contract, po = target.get("nomor_kontrak"), target.get("nomor_po")

            # Gunakan pasangan kontrak + nomor PO yang memiliki index pada
            # tabel transaksi, realisasi, dan opname. Query berbasis OR
            # po_item_id sebelumnya memaksa full scan pada data production
            # dan dapat memutus koneksi MySQL saat tabel sudah besar.
            identity_where = "nomor_kontrak=%s AND nomor_po=%s"
            identity_params = (contract, po)
            cur.execute(f"""
                SELECT COUNT(*) AS jumlah, COUNT(DISTINCT NULLIF(TRIM(CAST(pi_no AS CHAR)),'')) AS jumlah_pi
                FROM database_transaksi_rincian
                WHERE {identity_where}
            """, identity_params)
            transactions = row_dict(cur, cur.fetchone() or {})

            cur.execute(f"""
                SELECT DISTINCT TRIM(CAST(pi_no AS CHAR)) AS pi_no,
                       TRIM(CAST(nomor_po AS CHAR)) AS nomor_po, COUNT(*) AS jumlah_baris
                FROM database_transaksi_rincian
                WHERE {identity_where}
                  AND TRIM(COALESCE(pi_no,''))<>''
                GROUP BY TRIM(CAST(pi_no AS CHAR)), TRIM(CAST(nomor_po AS CHAR))
                ORDER BY pi_no
                LIMIT 1000
            """, identity_params)
            affected_pis = [row_dict(cur, r) for r in cur.fetchall()]

            def count_table(table, pi_column=None):
                pi_select = f", COUNT(DISTINCT NULLIF(TRIM(CAST({pi_column} AS CHAR)),'')) AS jumlah_pi" if pi_column else ""
                cur.execute(f"""
                    SELECT COUNT(*) AS jumlah{pi_select}
                    FROM {table}
                    WHERE {identity_where}
                """, identity_params)
                return row_dict(cur, cur.fetchone() or {})

            realizations = count_table("database_po_realisasi", "nomor_pi")
            opnames = count_table("database_opname", "nomor_pi")

            cur.execute("""
                SELECT id, nomor_po, version_no, status_po
                FROM database_master_po
                WHERE nomor_kontrak=%s AND nomor_po=%s
                ORDER BY version_no DESC, id DESC
            """, (contract, po))
            affected_pos = [row_dict(cur, r) for r in cur.fetchall()]

            return jsonify({"success": True, "data": {
                "target": target,
                "counts": {
                    "transaction_rows": int(transactions.get("jumlah") or 0),
                    "realization_rows": int(realizations.get("jumlah") or 0),
                    "opname_rows": int(opnames.get("jumlah") or 0),
                    "pi_count": len(affected_pis),
                },
                "affected_pis": affected_pis,
                "affected_pos": affected_pos,
            }})
        finally:
            cur.close()


@master_po_bp.post("/api/<int:po_id>/delete")
@login_required
def api_delete(po_id):
    """Arsipkan Master PO tanpa menghapus histori item/PI."""
    bootstrap_master_po()
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, nomor_kontrak, nomor_po, version_no, status_po FROM database_master_po WHERE id=%s LIMIT 1", (po_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"success": False, "message": "Master PO tidak ditemukan."}), 404
            data = row_dict(cur, row)
            if data["status_po"] == "DELETED":
                return jsonify({"success": True, "message": "Master PO sudah diarsipkan.", "po_id": po_id})

            cur.execute("""
                UPDATE database_master_po
                SET status_po='DELETED', deleted_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
            """, (po_id,))
            conn.commit()
            return jsonify({
                "success": True,
                "po_id": po_id,
                "message": (
                    f"Master PO {data['nomor_po']} versi {data.get('version_no',1)} diarsipkan. "
                    "PI yang sudah menggunakannya tetap tersimpan dan tidak ikut terhapus."
                )
            })
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "message": str(exc)}), 400
        finally:
            cur.close()


@master_po_bp.post("/api")
@login_required
def api_save():
    bootstrap_master_po()
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    payload = request.get_json(silent=True) or {}
    po_id = payload.get("po_id") or payload.get("id")
    nomor_kontrak = clean(payload.get("nomor_kontrak"))
    nomor_po = clean(payload.get("nomor_po"))
    tanggal_po = clean(payload.get("tanggal_po")) or None
    status_po = clean(payload.get("status_po")) or "ACTIVE"
    keterangan = clean(payload.get("keterangan"))
    items = payload.get("items") or []
    if not nomor_kontrak or not nomor_po:
        return jsonify({"success": False, "message": "Nomor Kontrak dan Nomor PO wajib diisi."}), 400
    if not items:
        return jsonify({"success": False, "message": "Minimal satu item PO wajib diisi."}), 400

    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            if po_id:
                cur.execute("SELECT id, status_po, nomor_kontrak, nomor_po FROM database_master_po WHERE id=%s LIMIT 1", (int(po_id),))
                existing = cur.fetchone()
                if not existing:
                    return jsonify({"success": False, "message": "Master PO yang diedit tidak ditemukan."}), 404
                existing = row_dict(cur, existing)
                if existing["status_po"] == "DELETED":
                    return jsonify({"success": False, "message": "Master PO sudah diarsipkan dan tidak dapat diedit. Buat versi baru dari referensi terbaru."}), 409
                target_id = int(existing["id"])
                if (clean(existing["nomor_kontrak"]) != nomor_kontrak or clean(existing["nomor_po"]) != nomor_po):
                    cur.execute("""
                        SELECT COUNT(*) AS used
                        FROM database_master_po_item i
                        WHERE i.po_id=%s AND (
                            EXISTS (SELECT 1 FROM database_transaksi_rincian t WHERE t.po_item_id=i.id)
                            OR EXISTS (SELECT 1 FROM database_po_realisasi r WHERE r.po_item_id=i.id)
                            OR EXISTS (SELECT 1 FROM database_po_mapping_rule m WHERE m.po_item_id=i.id)
                        )
                    """, (target_id,))
                    if int(row_dict(cur, cur.fetchone()).get("used") or 0):
                        raise ValueError("Nomor kontrak/PO sudah dipakai PI atau realisasi dan tidak boleh diubah langsung. Koreksi tautannya terlebih dahulu.")
                cur.execute("""
                    UPDATE database_master_po
                    SET nomor_kontrak=%s, nomor_po=%s, tanggal_po=%s,
                        status_po=%s, keterangan=%s, deleted_at=NULL
                    WHERE id=%s
                """, (nomor_kontrak, nomor_po, tanggal_po, status_po, keterangan, target_id))
            else:
                # Manual create: jika nomor PO sudah punya versi aktif, edit versi aktif.
                cur.execute("""
                    SELECT id FROM database_master_po
                    WHERE nomor_kontrak=%s AND nomor_po=%s
                      AND status_po IN ('DRAFT','ACTIVE')
                    ORDER BY version_no DESC, id DESC LIMIT 1
                """, (nomor_kontrak, nomor_po))
                row = cur.fetchone()
                if row:
                    target_id = row_dict(cur, row)["id"]
                    cur.execute("""
                        UPDATE database_master_po
                        SET tanggal_po=%s,status_po=%s,keterangan=%s
                        WHERE id=%s
                    """, (tanggal_po,status_po,keterangan,target_id))
                else:
                    cur.execute("SELECT COALESCE(MAX(version_no),0)+1 AS next_version FROM database_master_po WHERE nomor_kontrak=%s AND nomor_po=%s", (nomor_kontrak,nomor_po))
                    next_version = int(row_dict(cur, cur.fetchone())["next_version"] or 1)
                    cur.execute("""
                        INSERT INTO database_master_po
                            (nomor_kontrak,nomor_po,tanggal_po,status_po,version_no,keterangan)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, (nomor_kontrak,nomor_po,tanggal_po,status_po,next_version,keterangan))
                    target_id = cur.lastrowid

            kept_ids = []
            for index, item in enumerate(items, 1):
                item_no = int(item.get("item_no") or index)
                kategori = clean(item.get("kategori"))
                desc = clean(item.get("deskripsi_pekerjaan"))
                uom = clean(item.get("uom"))
                qty = dec(item.get("qty_po"))
                price = dec(item.get("unit_price"))
                if not qty.is_finite() or not price.is_finite() or qty < 0 or price < 0:
                    raise ValueError(f"Qty dan harga item PO #{item_no} harus angka tidak negatif.")
                if is_provisional_category(kategori):
                    desc = desc or "Provisional Sum (At Cost + Fee 15%)"
                    uom = uom or "AU"
                keterangan = clean(item.get("keterangan"))
                total = calculate_po_plafon(qty, price, kategori, uom)
                if not desc:
                    raise ValueError(f"Deskripsi item #{item_no} wajib diisi.")
                raw_item_id = item.get("id")
                if raw_item_id:
                    item_id = int(raw_item_id)
                    cur.execute("""
                        UPDATE database_master_po_item
                        SET item_no=%s,kategori=%s,deskripsi_pekerjaan=%s,uom=%s,
                            qty_po=%s,unit_price=%s,total_plafon=%s,keterangan=%s,status_item='ACTIVE'
                        WHERE id=%s AND po_id=%s
                    """, (item_no,kategori,desc,uom,qty,price,total,keterangan,item_id,target_id))
                    if not cur.rowcount:
                        cur.execute("SELECT id FROM database_master_po_item WHERE id=%s AND po_id=%s", (item_id,target_id))
                        if not cur.fetchone():
                            raise ValueError(f"Item PO #{item_id} bukan milik PO yang diedit.")
                else:
                    cur.execute("SELECT id,status_item FROM database_master_po_item WHERE po_id=%s AND item_no=%s", (target_id,item_no))
                    occupied = cur.fetchone()
                    if occupied:
                        existing_no = row_dict(cur, occupied)
                        if existing_no.get("status_item") == "ACTIVE":
                            raise ValueError(f"Nomor item PO #{item_no} sudah dipakai. Pilih nomor lain.")
                        cur.execute("SELECT COALESCE(MAX(item_no),0)+1 AS next_no FROM database_master_po_item WHERE po_id=%s", (target_id,))
                        item_no = int(row_dict(cur, cur.fetchone()).get("next_no") or 1)
                    cur.execute("""
                        INSERT INTO database_master_po_item
                            (po_id,item_no,kategori,deskripsi_pekerjaan,uom,qty_po,unit_price,total_plafon,keterangan,status_item)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                    """, (target_id,item_no,kategori,desc,uom,qty,price,total,keterangan))
                    item_id = cur.lastrowid
                kept_ids.append(item_id)

            placeholders = ",".join(["%s"] * len(kept_ids))
            cur.execute(f"""
                SELECT id FROM database_master_po_item
                WHERE po_id=%s AND status_item='ACTIVE' AND id NOT IN ({placeholders})
            """, tuple([target_id] + kept_ids))
            removed_ids = [row_dict(cur, r)["id"] for r in cur.fetchall()]
            if removed_ids:
                removed_placeholders = ",".join(["%s"] * len(removed_ids))
                for source in ("database_po_realisasi", "database_transaksi_rincian", "database_po_mapping_rule"):
                    cur.execute(f"SELECT COUNT(*) AS used FROM {source} WHERE po_item_id IN ({removed_placeholders})", tuple(removed_ids))
                    if int(row_dict(cur, cur.fetchone()).get("used") or 0):
                        raise ValueError("Item PO yang sudah dipakai PI/realisasi tidak dapat dihapus. Arsipkan atau koreksi mapping dahulu.")
                cur.execute(f"UPDATE database_master_po_item SET status_item='INACTIVE' WHERE id IN ({removed_placeholders})", tuple(removed_ids))

            refresh_po_total(cur, target_id)
            conn.commit()
            mapping_warning = None
            try:
                remap_transaction_pi_items(nomor_kontrak, nomor_po)
                remap_unmapped_realizations(nomor_kontrak, nomor_po)
            except Exception as exc:
                mapping_warning = f"Master PO tersimpan, tetapi sinkronisasi mapping perlu diulang: {exc}"
            return jsonify({"success": True, "po_id": target_id, "warning": mapping_warning})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "message": str(exc)}), 400
        finally:
            cur.close()


def get_po_header(cur, po_id):
    """Ambil header Master PO beserta ringkasan realisasi."""
    cur.execute("""
        SELECT
            h.id,
            h.nomor_kontrak,
            h.nomor_po,
            h.tanggal_po,
            h.status_po,
            h.version_no,
            h.deleted_at,
            h.total_plafon,
            COALESCE(k.plafon_kontrak, 0) AS total_plafon_kontrak,
            h.keterangan,
            h.created_at,
            h.updated_at,
            (SELECT COUNT(*) FROM database_master_po_item i
             WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS jumlah_item,
            (SELECT COALESCE(SUM(i.qty_po),0) FROM database_master_po_item i
             WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS total_qty,
            (SELECT COALESCE(SUM(r.qty_realisasi),0) FROM database_po_realisasi r
             JOIN database_master_po_item i ON i.id=r.po_item_id
             WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS qty_realisasi,
            (SELECT COALESCE(SUM(r.nilai_realisasi),0) FROM database_po_realisasi r
             JOIN database_master_po_item i ON i.id=r.po_item_id
             WHERE i.po_id=h.id AND i.status_item='ACTIVE') AS nilai_realisasi
        FROM database_master_po h
        LEFT JOIN master_kontrak k
          ON k.id=h.kontrak_id OR (h.kontrak_id IS NULL AND
             CONVERT(TRIM(k.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
             CONVERT(TRIM(h.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci)
        WHERE h.id = %s
        LIMIT 1
    """, (po_id,))
    row = cur.fetchone()
    return row_dict(cur, row) if row else None


def refresh_po_total(cur, po_id):
    """Hitung ulang total plafon header dari seluruh item aktif."""
    cur.execute("""
        SELECT id, kategori, uom, qty_po, unit_price, total_plafon
        FROM database_master_po_item
        WHERE po_id = %s AND status_item = 'ACTIVE'
    """, (po_id,))
    active_items = [row_dict(cur, raw) for raw in cur.fetchall()]
    for item in active_items:
        expected = calculate_po_plafon(
            item.get("qty_po"), item.get("unit_price"),
            item.get("kategori"), item.get("uom"),
        )
        if abs(dec(item.get("total_plafon")) - expected) > Decimal("0.005"):
            cur.execute(
                "UPDATE database_master_po_item SET total_plafon=%s WHERE id=%s AND po_id=%s",
                (expected, item.get("id"), po_id),
            )
    cur.execute("""
        SELECT COALESCE(SUM(total_plafon), 0) AS total_plafon
        FROM database_master_po_item
        WHERE po_id = %s
          AND status_item = 'ACTIVE'
    """, (po_id,))
    row = cur.fetchone()
    value = row_dict(cur, row)["total_plafon"] if row else Decimal("0")

    cur.execute("""
        UPDATE database_master_po
        SET total_plafon = %s
        WHERE id = %s
    """, (value, po_id))
    return value


def _is_real_po(value):
    value = clean(value)
    return bool(value and value not in {"-", "0", "NONE", "NULL"})


def po_reference_fingerprint(category, description, uom, keterangan=None):
    """Identitas rincian referensi, including PS business context when present."""
    if is_provisional_category(category):
        # A note identifies the business purpose of a PS line.  Keep the
        # no-note fallback compatible with older category-level rules.
        note = reference_note(keterangan)
        value = "po-category\x1fprovisional-sum"
        if note:
            value += "\x1f" + note
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    value = "\x1f".join((_norm_text(category), _norm_text(description), _norm_text(uom)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _legacy_reference_fingerprint(category, description, uom):
    value = "\x1f".join((_norm_text(category), _norm_text(description), _norm_text(uom)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _find_reference_po_item(cur, nomor_kontrak, nomor_po, row):
    """Resolve a transaction to one PO item through Master Referensi naming.

    ``po_item_id`` remains the stored accounting link, but it is no longer
    required as an input.  The decision is made from the canonical reference
    key and only succeeds when exactly one active PO item is possible.
    """
    contract, po = clean(nomor_kontrak), clean(nomor_po)
    if not contract or not po or not _is_real_po(po):
        return None
    source_key = reference_key(row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit") or row.get("uom"))

    cur.execute("""
        SELECT kategori, uraian_pekerjaan, unit
        FROM database_master_referensi
        WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s
    """, (contract,))
    reference_keys = set()
    for raw in cur.fetchall():
        ref = raw if isinstance(raw, dict) else {
            "kategori": raw[0], "uraian_pekerjaan": raw[1], "unit": raw[2]
        }
        reference_keys.add(reference_key(ref.get("kategori"), ref.get("uraian_pekerjaan"), ref.get("unit")))
    if source_key not in reference_keys and not is_provisional_category(row.get("kategori")):
        return None

    cur.execute("""
        SELECT i.id, i.kategori, i.deskripsi_pekerjaan, i.uom, i.keterangan
        FROM database_master_po h
        JOIN database_master_po_item i ON i.po_id=h.id
        WHERE TRIM(h.nomor_kontrak)=%s AND TRIM(h.nomor_po)=%s
          AND h.status_po IN ('DRAFT','ACTIVE') AND h.deleted_at IS NULL
          AND i.status_item='ACTIVE'
    """, (contract, po))
    candidates = []
    for raw in cur.fetchall():
        item = raw if isinstance(raw, dict) else dict(zip([c[0] for c in cur.description], raw))
        item_key = reference_key(item.get("kategori"), item.get("deskripsi_pekerjaan"), item.get("uom"))
        same_line = item_key == source_key or (
            not clean(item.get("kategori")) and item_key[1:] == source_key[1:]
        )
        if same_line or (is_provisional_category(row.get("kategori")) and is_provisional_category(item.get("kategori"))):
            candidates.append(item)

    if len(candidates) > 1 and is_provisional_category(row.get("kategori")):
        note = reference_note(row.get("keterangan"))
        exact = [x for x in candidates if note and reference_note(x.get("keterangan")) == note]
        if len(exact) == 1:
            candidates = exact
        elif note:
            ranked = sorted(
                ((reference_note_similarity(note, x.get("keterangan")), x) for x in candidates),
                key=lambda pair: pair[0], reverse=True,
            )
            if ranked and ranked[0][0] >= 0.72 and (
                len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.10
            ):
                candidates = [ranked[0][1]]

    if len(candidates) == 1:
        return candidates[0].get("id")
    return None


def apply_po_mapping_rules(nomor_kontrak, pi_no):
    """Pulihkan tautan PO saat Modul 2 menulis ulang rincian suatu PI."""
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT m.nomor_po, m.fingerprint, m.po_item_id
                FROM database_po_mapping_rule m
                JOIN database_master_po_item i ON i.id=m.po_item_id AND i.status_item='ACTIVE'
                JOIN database_master_po h ON h.id=i.po_id
                  AND h.status_po IN ('DRAFT','ACTIVE') AND h.deleted_at IS NULL
                WHERE m.nomor_kontrak=%s AND h.nomor_kontrak=%s AND h.nomor_po=m.nomor_po
            """, (clean(nomor_kontrak), clean(nomor_kontrak)))
            rules = {(clean(r["nomor_po"]), r["fingerprint"]): r["po_item_id"]
                     for raw in cur.fetchall() for r in [row_dict(cur, raw)]}
            if not rules:
                return 0

            cur.execute("""
                SELECT nomor_po, kategori, deskripsi_pekerjaan, unit, po_item_id
                FROM database_transaksi_rincian
                WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s
                  AND TRIM(CAST(pi_no AS CHAR))=%s
            """, (clean(nomor_kontrak), clean(pi_no)))
            rows = [row_dict(cur, raw) for raw in cur.fetchall()]
            changed = 0
            seen = set()
            for row in rows:
                po = clean(row.get("nomor_po"))
                if not _is_real_po(po) or row.get("po_item_id"):
                    continue
                fingerprint = po_reference_fingerprint(row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit"), row.get("keterangan"))
                item_id = rules.get((po, fingerprint))
                if item_id is None and is_provisional_category(row.get("kategori")):
                    # Preserve rules confirmed before the category-level bucket existed.
                    old_key = _legacy_reference_fingerprint(row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit"))
                    item_id = rules.get((po, old_key))
                identity = (po, row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit"))
                if not item_id or identity in seen:
                    continue
                seen.add(identity)
                cur.execute("""
                    UPDATE database_transaksi_rincian SET po_item_id=%s
                    WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s
                      AND TRIM(CAST(pi_no AS CHAR))=%s
                      AND TRIM(CAST(nomor_po AS CHAR))=%s
                      AND kategori <=> %s AND deskripsi_pekerjaan <=> %s AND unit <=> %s
                      AND po_item_id IS NULL
                """, (item_id, clean(nomor_kontrak), clean(pi_no), po,
                      row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit")))
                changed += cur.rowcount
            conn.commit()
            return changed
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def remap_transaction_pi_items(nomor_kontrak=None, nomor_po=None):
    """Link PI detail rows to Master PO Items.

    The PI detail is the source row. When a Master PO exists, map the row by:
    1) its explicit po_item_id when valid for the same contract+PO;
    2) otherwise a unique category + description + UOM match.

    Unit price is not an identity field because a PI price may legitimately
    differ from the PO price. Provisional Sum rows use keterangan as an
    additional discriminator when several PS rows have the same category,
    description, and UOM.
    Ambiguous matches are left untouched rather than guessed.
    """
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            clauses = ["TRIM(COALESCE(t.nomor_po,'')) NOT IN ('','-','0')"]
            params = []
            if nomor_kontrak:
                clauses.append("TRIM(t.nomor_kontrak)=%s")
                params.append(clean(nomor_kontrak))
            if nomor_po:
                clauses.append("TRIM(t.nomor_po)=%s")
                params.append(clean(nomor_po))

            cur.execute(f"""
                SELECT t.nomor_kontrak, t.nomor_po, t.po_item_id, t.kategori,
                       t.deskripsi_pekerjaan, t.keterangan, t.unit, t.harga_satuan
                FROM database_transaksi_rincian t
                WHERE {' AND '.join(clauses)}
            """, tuple(params))
            rows = cur.fetchall()
            names = [c[0] for c in cur.description]
            changed = 0

            for raw in rows:
                r = dict(raw) if isinstance(raw, dict) else dict(zip(names, raw))
                contract = clean(r.get('nomor_kontrak'))
                po = clean(r.get('nomor_po'))
                existing = r.get('po_item_id')
                valid_id = None

                if existing:
                    cur.execute("""
                        SELECT i.id
                        FROM database_master_po h
                        JOIN database_master_po_item i ON i.po_id=h.id
                        WHERE i.id=%s
                          AND CONVERT(TRIM(h.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                              CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND CONVERT(TRIM(h.nomor_po) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                              CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND i.status_item='ACTIVE'
                        LIMIT 1
                    """, (existing, contract, po))
                    found = cur.fetchone()
                    if found:
                        valid_id = found[0] if not isinstance(found, dict) else found['id']

                if valid_id is None:
                    # First resolve against Master Referensi.  This handles
                    # harmless naming differences before the legacy exact
                    # category/description/UOM comparison below.
                    valid_id = _find_reference_po_item(cur, contract, po, r)

                if valid_id is None:
                    cur.execute("""
                        SELECT i.id, i.kategori, i.deskripsi_pekerjaan, i.keterangan, i.uom
                        FROM database_master_po h
                        JOIN database_master_po_item i ON i.po_id=h.id
                        WHERE CONVERT(TRIM(h.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND CONVERT(TRIM(h.nomor_po) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND h.status_po IN ('DRAFT','ACTIVE')
                          AND h.deleted_at IS NULL
                          AND i.status_item='ACTIVE'
                    """, (contract, po))
                    matches = cur.fetchall()
                    names_match = [c[0] for c in cur.description]
                    candidates = []
                    pi_category = r.get('kategori')
                    pi_key = reference_key(pi_category, r.get('deskripsi_pekerjaan'), r.get('unit'))
                    for raw_match in matches:
                        m = raw_match if isinstance(raw_match, dict) else dict(zip(names_match, raw_match))
                        m_key = reference_key(m.get('kategori'), m.get('deskripsi_pekerjaan'), m.get('uom'))
                        category_match = m_key == pi_key or (
                            not clean(m.get('kategori')) and m_key[1:] == pi_key[1:]
                        )
                        if category_match:
                            candidates.append(m)
                    if is_provisional_category(pi_category) and len(candidates) > 1 and clean(r.get('keterangan')):
                        note = _norm_text(r.get('keterangan'))
                        noted = [m for m in candidates if _norm_text(m.get('keterangan')) == note]
                        if noted:
                            candidates = noted
                    if len(candidates) == 1:
                        valid_id = candidates[0].get('id')

                if valid_id is not None and str(valid_id) != str(existing or ''):
                    cur.execute("""
                        UPDATE database_transaksi_rincian
                        SET po_item_id=%s
                        WHERE CONVERT(TRIM(nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND CONVERT(TRIM(COALESCE(nomor_po,'')) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND COALESCE(po_item_id,0)=COALESCE(%s,0)
                          AND LOWER(TRIM(COALESCE(kategori,'')))=LOWER(TRIM(COALESCE(%s,'')))
                          AND LOWER(TRIM(COALESCE(deskripsi_pekerjaan,'')))=LOWER(TRIM(COALESCE(%s,'')))
                          AND LOWER(TRIM(COALESCE(unit,'')))=LOWER(TRIM(COALESCE(%s,'')))
                          AND keterangan <=> %s
                        LIMIT 1
                    """, (valid_id, contract, po, existing, clean(r.get('kategori')), clean(r.get('deskripsi_pekerjaan')), clean(r.get('unit')), r.get('keterangan')))
                    changed += cur.rowcount

            conn.commit()
            return changed
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def remap_unmapped_realizations(nomor_kontrak=None, nomor_po=None):
    """Map pending PO realizations after a Master PO/item is created or edited."""
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            clauses = ["r.mapping_status='UNMAPPED'"]
            params = []
            if nomor_kontrak:
                clauses.append("TRIM(r.nomor_kontrak)=%s"); params.append(clean(nomor_kontrak))
            if nomor_po:
                clauses.append("TRIM(r.nomor_po)=%s"); params.append(clean(nomor_po))
            cur.execute(f"""
                SELECT r.id, r.nomor_kontrak, r.nomor_po, r.kategori,
                       r.deskripsi_pekerjaan, r.uom
                FROM database_po_realisasi r
                WHERE {' AND '.join(clauses)}
            """, tuple(params))
            rows = [row_dict(cur, raw) for raw in cur.fetchall()]
            changed = 0
            for row in rows:
                item_id = _find_reference_po_item(cur, row.get("nomor_kontrak"),
                                                  row.get("nomor_po"), row)
                if item_id is None:
                    continue
                cur.execute("""
                    UPDATE database_po_realisasi
                    SET po_item_id=%s, mapping_status='MAPPED'
                    WHERE id=%s AND mapping_status='UNMAPPED'
                """, (item_id, row.get("id")))
                changed += cur.rowcount
            conn.commit()
            return changed
        except Exception:
            conn.rollback(); raise
        finally:
            cur.close()


def sync_pi_realization(nomor_kontrak, pi_no, remap_other=True):
    """Rebuild PO usage history for one PI.

    If the PO/item is not yet registered, the usage is still preserved as UNMAPPED.
    It can later be linked automatically when the Master PO is completed.
    """
    bootstrap_master_po()
    nomor_kontrak, pi_no = clean(nomor_kontrak), clean(pi_no)
    apply_po_mapping_rules(nomor_kontrak, pi_no)
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM database_po_realisasi WHERE nomor_kontrak=%s AND nomor_pi=%s", (nomor_kontrak, pi_no))
            cur.execute("""SELECT nomor_kontrak,nomor_po,pi_no,tanggal_pi,qty,harga_satuan,total_harga,keterangan,po_item_id,kategori,deskripsi_pekerjaan,unit
                          FROM database_transaksi_rincian
                          WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s AND TRIM(CAST(pi_no AS CHAR))=%s""", (nomor_kontrak, pi_no))
            rows=cur.fetchall(); names=[c[0] for c in cur.description]
            inserted=0
            for idx, raw in enumerate(rows,1):
                r=dict(raw) if isinstance(raw,dict) else dict(zip(names,raw))
                nomor_po=clean(r.get('nomor_po'))
                if not _is_real_po(nomor_po):
                    continue
                qty=dec(r.get('qty')); price=dec(r.get('harga_satuan')); nilai=dec(r.get('total_harga'),qty*price)
                po_item_id = None
                raw_item_id = r.get('po_item_id')
                if raw_item_id and _is_real_po(nomor_po):
                    cur.execute("""
                        SELECT i.id
                        FROM database_master_po h
                        JOIN database_master_po_item i ON i.po_id=h.id
                        WHERE i.id=%s
                          AND CONVERT(TRIM(h.nomor_kontrak) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND CONVERT(TRIM(h.nomor_po) USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONVERT(TRIM(%s) USING utf8mb4) COLLATE utf8mb4_unicode_ci
                          AND i.status_item='ACTIVE'
                        LIMIT 1
                    """, (raw_item_id, nomor_kontrak, nomor_po))
                    match = cur.fetchone()
                    if match:
                        po_item_id = match[0] if not isinstance(match, dict) else match['id']
                if po_item_id is None:
                    po_item_id = _find_reference_po_item(cur, nomor_kontrak, nomor_po, r)
                mapping='MAPPED' if po_item_id else 'UNMAPPED'
                source=hashlib.sha256('|'.join([clean(r.get('nomor_kontrak')),nomor_po,clean(r.get('pi_no')),clean(r.get('deskripsi_pekerjaan')),clean(r.get('unit')),str(qty),str(price),str(idx)]).encode()).hexdigest()
                cur.execute("""INSERT INTO database_po_realisasi
                    (po_item_id,nomor_kontrak,nomor_po,nomor_pi,tanggal_pi,
                     qty_realisasi,unit_price,nilai_realisasi,sumber_transaksi_id,
                     kategori,deskripsi_pekerjaan,uom,mapping_status,keterangan)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        po_item_id,
                        clean(r.get('nomor_kontrak')),
                        nomor_po,
                        clean(r.get('pi_no')),
                        clean(r.get('tanggal_pi')) or None,
                        qty,
                        price,
                        nilai,
                        source,
                        clean(r.get('kategori')),
                        clean(r.get('deskripsi_pekerjaan')),
                        clean(r.get('unit')),
                        mapping,
                        clean(r.get('keterangan')),
                    ))
                inserted += 1
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally: cur.close()
    remapped = 0
    if remap_other:
        remap_transaction_pi_items(nomor_kontrak, None)
        remapped = remap_unmapped_realizations(nomor_kontrak, None)
    return {'success':True,'rows':inserted,'remapped':remapped}
