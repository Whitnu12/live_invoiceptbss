"""
MODUL 0 - MASTER REFERENSI
Flask/MySQL helper version.

Sumber database:
- database_master_referensi
- database_master_nama_alat

File ini HANYA berisi database/business logic.
Semua Flask route Modul 0 berada di modul_navigasi/routes.py.
"""

from datetime import datetime
import logging

from db_connection import get_db_connection

LOGGER = logging.getLogger(__name__)

TABLE_REFERENSI = "database_master_referensi"
TABLE_NAMA_ALAT = "database_master_nama_alat"

READ_ONLY_ROLES = {
    "management",
    "direksi",
    "project manager",
    "pm",
}


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _row_to_dict(row, columns=None):
    """Normalisasi hasil cursor MySQL tanpa menggunakan dict(row) pada tuple."""
    if row is None:
        return {}

    if isinstance(row, dict):
        return dict(row)

    if columns and isinstance(row, (tuple, list)):
        return {
            columns[index]: row[index]
            for index in range(min(len(columns), len(row)))
        }

    return {"value": row}


def _json_record(row):
    if not isinstance(row, dict):
        return row

    result = dict(row)

    if result.get("update_terakhir") is not None:
        value = result["update_terakhir"]
        if hasattr(value, "strftime"):
            result["update_terakhir"] = value.strftime("%Y-%m-%d %H:%M:%S")
        else:
            result["update_terakhir"] = str(value)

    result["harga_satuan"] = _safe_float(result.get("harga_satuan"), 0.0)
    return result


def _role_from_session():
    # Import lokal agar helper database tidak bergantung pada Flask saat di-import.
    from flask import session
    return _clean(session.get("current_role", "Staff")).lower()


def can_write_master():
    return _role_from_session() not in READ_ONLY_ROLES


def _has_id_column(connection):
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = 'id'
            """,
            (TABLE_REFERENSI,),
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False
    finally:
        cursor.close()


def get_master_referensi(
    nomor_kontrak="",
    kategori="",
    search="",
    page=1,
    per_page=50,
):
    """
    Mengambil Master Referensi dengan pagination MySQL.

    Tidak mengambil seluruh 453+ record ke Python.
    COUNT(*) hanya menghitung total; SELECT data menggunakan LIMIT/OFFSET.
    """
    try:
        page = max(int(page or 1), 1)
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(per_page or 50)
    except (TypeError, ValueError):
        per_page = 50

    per_page = min(max(per_page, 20), 100)
    offset = (page - 1) * per_page

    with get_db_connection() as connection:
        has_id = _has_id_column(connection)

        conditions = [
            """
            NOT (
                LOWER(TRIM(COALESCE(nomor_kontrak, ''))) IN
                    ('nomor kontrak', 'no kontrak', 'kontrak')
                AND
                LOWER(TRIM(COALESCE(kategori, ''))) IN
                    ('kategori', 'kategori pekerjaan')
            )
            """
        ]
        params = []

        if _clean(nomor_kontrak):
            # Data impor lama dapat memiliki spasi tambahan atau perbedaan
            # kapitalisasi. Normalisasi di SQL agar referensi kontrak tetap
            # ditemukan saat dipanggil dari editor Master PO.
            conditions.append("LOWER(TRIM(nomor_kontrak)) = LOWER(TRIM(%s))")
            params.append(_clean(nomor_kontrak))

        if _clean(kategori):
            conditions.append("LOWER(TRIM(kategori)) = LOWER(TRIM(%s))")
            params.append(_clean(kategori))

        if _clean(search):
            term = f"%{_clean(search)}%"
            conditions.append(
                """
                (
                    nomor_kontrak LIKE %s
                    OR kategori LIKE %s
                    OR uraian_pekerjaan LIKE %s
                    OR unit LIKE %s
                )
                """
            )
            params.extend([term, term, term, term])

        where_sql = " WHERE " + " AND ".join(conditions)

        # 1. COUNT
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {TABLE_REFERENSI}
                {where_sql}
                """,
                tuple(params),
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row else 0
        finally:
            cursor.close()

        total_pages = (
            (total + per_page - 1) // per_page
            if total > 0
            else 1
        )

        if page > total_pages and total > 0:
            page = total_pages
            offset = (page - 1) * per_page

        # 2. DATA PAGE SAJA
        id_sql = "id, " if has_id else ""

        query = f"""
            SELECT
                {id_sql}
                nomor_kontrak,
                kategori,
                uraian_pekerjaan,
                unit,
                harga_satuan,
                update_terakhir
            FROM {TABLE_REFERENSI}
            {where_sql}
            ORDER BY
                nomor_kontrak ASC,
                kategori ASC,
                uraian_pekerjaan ASC
            LIMIT %s OFFSET %s
        """

        data_params = list(params) + [per_page, offset]

        cursor = connection.cursor()
        try:
            cursor.execute(query, tuple(data_params))
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
        finally:
            cursor.close()

    return {
        "rows": [_row_to_dict(row, columns) for row in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def get_master_nama_alat():
    """Mengambil master nama alat."""
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""
                    SELECT nama_alat
                    FROM {TABLE_NAMA_ALAT}
                    ORDER BY nama_alat ASC
                    """
                )
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                return [_row_to_dict(row, columns) for row in rows]
            finally:
                cursor.close()
    except Exception:
        LOGGER.exception("Gagal mengambil database_master_nama_alat.")
        raise


def get_options():
    """Mengambil opsi dropdown dari database_master_referensi."""
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""
                    SELECT DISTINCT nomor_kontrak
                    FROM {TABLE_REFERENSI}
                    WHERE nomor_kontrak IS NOT NULL
                      AND TRIM(nomor_kontrak) <> ''
                    ORDER BY nomor_kontrak ASC
                    """
                )
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                contracts = [
                    _clean(_row_to_dict(row, columns).get("nomor_kontrak"))
                    for row in rows
                ]

                cursor.execute(
                    f"""
                    SELECT DISTINCT kategori
                    FROM {TABLE_REFERENSI}
                    WHERE kategori IS NOT NULL
                      AND TRIM(kategori) <> ''
                    ORDER BY kategori ASC
                    """
                )
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                categories = [
                    _clean(_row_to_dict(row, columns).get("kategori"))
                    for row in rows
                ]

                cursor.execute(
                    f"""
                    SELECT DISTINCT unit
                    FROM {TABLE_REFERENSI}
                    WHERE unit IS NOT NULL
                      AND TRIM(unit) <> ''
                    ORDER BY unit ASC
                    """
                )
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                units = [
                    _clean(_row_to_dict(row, columns).get("unit"))
                    for row in rows
                ]
            finally:
                cursor.close()

        return {
            "nomor_kontrak": contracts,
            "kategori": categories,
            "unit": units,
        }
    except Exception:
        LOGGER.exception("Gagal mengambil opsi Modul 0.")
        raise


def _normalize_payload(payload):
    payload = payload or {}

    nomor_kontrak = _clean(payload.get("nomor_kontrak"))
    kategori = _clean(payload.get("kategori")).upper()
    uraian = _clean(payload.get("uraian_pekerjaan"))
    unit = _clean(payload.get("unit"))
    harga_raw = payload.get("harga_satuan")

    if not nomor_kontrak:
        raise ValueError("Nomor Kontrak wajib diisi.")
    if not kategori:
        raise ValueError("Kategori wajib diisi.")
    if not uraian:
        raise ValueError("Uraian Pekerjaan wajib diisi.")
    if not unit:
        raise ValueError("Unit wajib diisi.")

    try:
        harga = float(harga_raw or 0)
    except (TypeError, ValueError):
        raise ValueError("Harga Satuan harus berupa angka.")

    if harga < 0:
        raise ValueError("Harga Satuan tidak boleh negatif.")

    return {
        "nomor_kontrak": nomor_kontrak,
        "kategori": kategori,
        "uraian_pekerjaan": uraian,
        "unit": unit,
        "harga_satuan": harga,
    }


def _where_identity(payload):
    fields = [
        "nomor_kontrak",
        "kategori",
        "uraian_pekerjaan",
        "unit",
        "harga_satuan",
    ]
    return (
        " AND ".join(f"{field} <=> %s" for field in fields),
        tuple(payload.get(field) for field in fields),
    )


def insert_master(payload):
    data = _normalize_payload(payload)

    with get_db_connection() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                INSERT INTO {TABLE_REFERENSI}
                (
                    nomor_kontrak,
                    kategori,
                    uraian_pekerjaan,
                    unit,
                    harga_satuan,
                    update_terakhir
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    data["nomor_kontrak"],
                    data["kategori"],
                    data["uraian_pekerjaan"],
                    data["unit"],
                    data["harga_satuan"],
                    datetime.now(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


def save_as_master(payload):
    """SAVE AS selalu INSERT record baru."""
    insert_master(payload)


def update_master(payload, old_data=None):
    """UPDATE record yang sedang dipanggil."""
    data = _normalize_payload(payload)

    with get_db_connection() as connection:
        has_id = _has_id_column(connection)
        cursor = connection.cursor()
        try:
            if has_id and old_data and old_data.get("id") is not None:
                cursor.execute(
                    f"""
                    UPDATE {TABLE_REFERENSI}
                    SET
                        nomor_kontrak = %s,
                        kategori = %s,
                        uraian_pekerjaan = %s,
                        unit = %s,
                        harga_satuan = %s,
                        update_terakhir = %s
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (
                        data["nomor_kontrak"],
                        data["kategori"],
                        data["uraian_pekerjaan"],
                        data["unit"],
                        data["harga_satuan"],
                        datetime.now(),
                        int(old_data["id"]),
                    ),
                )
            else:
                where_sql, where_params = _where_identity(
                    _normalize_payload(old_data or {})
                )
                cursor.execute(
                    f"""
                    UPDATE {TABLE_REFERENSI}
                    SET
                        nomor_kontrak = %s,
                        kategori = %s,
                        uraian_pekerjaan = %s,
                        unit = %s,
                        harga_satuan = %s,
                        update_terakhir = %s
                    WHERE {where_sql}
                    LIMIT 1
                    """,
                    (
                        data["nomor_kontrak"],
                        data["kategori"],
                        data["uraian_pekerjaan"],
                        data["unit"],
                        data["harga_satuan"],
                        datetime.now(),
                        *where_params,
                    ),
                )

            if cursor.rowcount == 0:
                raise ValueError("Data master yang akan di-update tidak ditemukan.")

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


def delete_master(record):
    with get_db_connection() as connection:
        has_id = _has_id_column(connection)
        cursor = connection.cursor()
        try:
            if has_id and record.get("id") is not None:
                cursor.execute(
                    f"DELETE FROM {TABLE_REFERENSI} WHERE id = %s LIMIT 1",
                    (int(record["id"]),),
                )
            else:
                where_sql, where_params = _where_identity(
                    _normalize_payload(record)
                )
                cursor.execute(
                    f"""
                    DELETE FROM {TABLE_REFERENSI}
                    WHERE {where_sql}
                    LIMIT 1
                    """,
                    where_params,
                )

            if cursor.rowcount == 0:
                raise ValueError("Data master tidak ditemukan.")

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


def _safe_float(value, default=0.0):
    if value is None:
        return default

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return default

    try:
        return float(text)
    except (TypeError, ValueError):
        pass

    try:
        text = (
            text
            .replace("Rp", "")
            .replace("rp", "")
            .replace(" ", "")
        )

        if "." in text and "," in text:
            text = text.replace(".", "")
            text = text.replace(",", ".")
        elif "." in text:
            parts = text.split(".")
            if all(len(part) == 3 for part in parts[1:]):
                text = "".join(parts)
        elif "," in text:
            parts = text.split(",")
            if all(len(part) == 3 for part in parts[1:]):
                text = "".join(parts)
            else:
                text = text.replace(",", ".")

        return float(text)
    except (TypeError, ValueError):
        return default
