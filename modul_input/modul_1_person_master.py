# ============================================================
# MASTER PERSONIL
# modul_input/modul_1_person_master.py
# ============================================================

from db_connection import get_db_connection


TABLE = "database_master_personil"


# ============================================================
# UTILITIES
# ============================================================

def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def can_write_person():
    """
    Master Personil hanya dapat diubah oleh role yang memiliki
    hak tulis.
    """
    try:
        from flask import session

        role = clean(session.get("current_role")).lower()

        read_only_roles = {
            "management",
            "direksi",
            "project manager",
            "pm",
        }

        return role not in read_only_roles

    except Exception:
        return False


# ============================================================
# LIST
# ============================================================

def list_persons(search="", active_only=True):
    """
    Mengambil daftar Master Personil.

    Return:
        [
            {
                "id": 1,
                "nama": "...",
                "jabatan": "...",
                "aktif": 1,
                "created_at": "...",
                "updated_at": "..."
            }
        ]
    """

    search = clean(search)

    with get_db_connection() as connection:
        cursor = connection.cursor(dictionary=True)

        try:
            sql = f"""
                SELECT
                    id,
                    nama,
                    jabatan,
                    aktif,
                    created_at,
                    updated_at
                FROM `{TABLE}`
                WHERE 1 = 1
            """

            params = []

            if active_only:
                sql += " AND aktif = 1"

            if search:
                sql += """
                    AND (
                        nama LIKE %s
                        OR jabatan LIKE %s
                    )
                """

                keyword = f"%{search}%"
                params.extend([keyword, keyword])

            sql += """
                ORDER BY
                    nama ASC,
                    jabatan ASC
            """

            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()

            for row in rows:
                if row.get("created_at") is not None:
                    row["created_at"] = row["created_at"].isoformat(
                        sep=" ",
                        timespec="seconds",
                    )

                if row.get("updated_at") is not None:
                    row["updated_at"] = row["updated_at"].isoformat(
                        sep=" ",
                        timespec="seconds",
                    )

            return rows

        finally:
            cursor.close()


# ============================================================
# CREATE
# ============================================================

def insert_person(payload):
    """
    Tambahkan personil baru.

    Jika nama + jabatan sudah ada:
    - Jika aktif -> tidak membuat duplicate.
    - Jika tidak aktif -> aktifkan kembali.
    """

    payload = payload or {}

    nama = clean(payload.get("nama"))
    jabatan = clean(payload.get("jabatan"))

    if not nama:
        raise ValueError("Nama personil wajib diisi.")

    if not jabatan:
        raise ValueError("Jabatan wajib diisi.")

    with get_db_connection() as connection:
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"""
                SELECT
                    id,
                    nama,
                    jabatan,
                    aktif
                FROM `{TABLE}`
                WHERE nama = %s
                  AND jabatan = %s
                LIMIT 1
                """,
                (nama, jabatan),
            )

            existing = cursor.fetchone()

            if existing:
                if int(existing.get("aktif") or 0) == 1:
                    return {
                        "created": False,
                        "reactivated": False,
                        "id": existing["id"],
                        "nama": existing["nama"],
                        "jabatan": existing["jabatan"],
                    }

                cursor.execute(
                    f"""
                    UPDATE `{TABLE}`
                    SET aktif = 1
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (existing["id"],),
                )

                connection.commit()

                return {
                    "created": False,
                    "reactivated": True,
                    "id": existing["id"],
                    "nama": nama,
                    "jabatan": jabatan,
                }

            cursor.execute(
                f"""
                INSERT INTO `{TABLE}`
                    (nama, jabatan, aktif)
                VALUES
                    (%s, %s, 1)
                """,
                (nama, jabatan),
            )

            person_id = cursor.lastrowid

            connection.commit()

            return {
                "created": True,
                "reactivated": False,
                "id": person_id,
                "nama": nama,
                "jabatan": jabatan,
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()


# ============================================================
# UPDATE
# ============================================================

def update_person(payload):
    """
    Update nama dan jabatan personil berdasarkan ID.
    """

    payload = payload or {}

    person_id = payload.get("id")
    nama = clean(payload.get("nama"))
    jabatan = clean(payload.get("jabatan"))

    if not person_id:
        raise ValueError("ID personil wajib diisi.")

    try:
        person_id = int(person_id)
    except (TypeError, ValueError):
        raise ValueError("ID personil tidak valid.")

    if not nama:
        raise ValueError("Nama personil wajib diisi.")

    if not jabatan:
        raise ValueError("Jabatan wajib diisi.")

    with get_db_connection() as connection:
        cursor = connection.cursor(dictionary=True)

        try:
            # ------------------------------------------------
            # Pastikan data yang akan diubah ada
            # ------------------------------------------------
            cursor.execute(
                f"""
                SELECT
                    id,
                    nama,
                    jabatan,
                    aktif
                FROM `{TABLE}`
                WHERE id = %s
                LIMIT 1
                """,
                (person_id,),
            )

            existing = cursor.fetchone()

            if not existing:
                raise ValueError(
                    "Personil yang akan diperbarui tidak ditemukan."
                )

            # ------------------------------------------------
            # Cegah duplicate nama + jabatan
            # ------------------------------------------------
            cursor.execute(
                f"""
                SELECT
                    id
                FROM `{TABLE}`
                WHERE nama = %s
                  AND jabatan = %s
                  AND id <> %s
                LIMIT 1
                """,
                (
                    nama,
                    jabatan,
                    person_id,
                ),
            )

            duplicate = cursor.fetchone()

            if duplicate:
                raise ValueError(
                    "Nama dan jabatan tersebut sudah digunakan "
                    "oleh personil lain."
                )

            # ------------------------------------------------
            # Update
            # ------------------------------------------------
            cursor.execute(
                f"""
                UPDATE `{TABLE}`
                SET
                    nama = %s,
                    jabatan = %s
                WHERE id = %s
                LIMIT 1
                """,
                (
                    nama,
                    jabatan,
                    person_id,
                ),
            )

            connection.commit()

            return {
                "id": person_id,
                "nama": nama,
                "jabatan": jabatan,
                "aktif": existing.get("aktif", 1),
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()


# ============================================================
# DEACTIVATE
# ============================================================

def deactivate_person(person_id):
    """
    Menonaktifkan personil.

    Data tidak dihapus secara permanen agar histori PI
    tetap aman.
    """

    try:
        person_id = int(person_id)
    except (TypeError, ValueError):
        raise ValueError("ID personil tidak valid.")

    with get_db_connection() as connection:
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"""
                SELECT
                    id,
                    nama,
                    jabatan,
                    aktif
                FROM `{TABLE}`
                WHERE id = %s
                LIMIT 1
                """,
                (person_id,),
            )

            existing = cursor.fetchone()

            if not existing:
                raise ValueError(
                    "Personil yang akan dinonaktifkan tidak ditemukan."
                )

            cursor.execute(
                f"""
                UPDATE `{TABLE}`
                SET aktif = 0
                WHERE id = %s
                LIMIT 1
                """,
                (person_id,),
            )

            connection.commit()

            return {
                "id": person_id,
                "nama": existing["nama"],
                "jabatan": existing["jabatan"],
                "aktif": 0,
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()