import logging
from typing import Optional, Dict, Any

from werkzeug.security import generate_password_hash, check_password_hash

from db_connection import get_db_connection


LOGGER = logging.getLogger(__name__)


# ============================================================
# CARI USER
# ============================================================

def cari_user(username: str) -> Optional[Dict[str, Any]]:
    """
    Mencari user aktif berdasarkan username.
    """

    username = str(username or "").strip().lower()

    if not username:
        return None

    query = """
        SELECT
            id,
            username,
            password_hash,
            nama_lengkap,
            role,
            departemen,
            is_active
        FROM database_users
        WHERE LOWER(username) = %s
          AND is_active = 1
        LIMIT 1
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(query, (username,))
                return cursor.fetchone()
            finally:
                cursor.close()

    except Exception:
        LOGGER.exception("Gagal mencari user.")
        return None


# ============================================================
# AUTENTIKASI USER
# ============================================================

def autentikasi_user(
    username: str,
    password: str,
) -> Optional[Dict[str, Any]]:
    """
    Memvalidasi username dan password.
    Hanya user aktif yang dapat login.
    """

    user = cari_user(username)

    if not user:
        return None

    try:
        password_valid = check_password_hash(
            user["password_hash"],
            password,
        )
    except Exception:
        LOGGER.exception("Gagal memvalidasi password.")
        return None

    if not password_valid:
        return None

    return user


# ============================================================
# PASSWORD
# ============================================================

def buat_password_hash(password: str) -> str:
    """
    Membuat hash password baru.
    """

    password = str(password or "")

    if not password:
        raise ValueError("Password tidak boleh kosong.")

    return generate_password_hash(password)


# ============================================================
# AMBIL SEMUA USER
# ============================================================

def ambil_semua_user() -> list:
    """
    Mengambil seluruh user dari database.

    Password hash tidak dikembalikan ke halaman.
    """

    query = """
        SELECT
            id,
            username,
            nama_lengkap,
            role,
            departemen,
            is_active,
            created_at,
            updated_at
        FROM database_users
        ORDER BY id ASC
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(query)
                return cursor.fetchall()
            finally:
                cursor.close()

    except Exception:
        LOGGER.exception("Gagal mengambil daftar user.")
        return []


# ============================================================
# AMBIL USER BERDASARKAN ID
# ============================================================

def cari_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Mengambil user berdasarkan ID.
    """

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID user tidak valid.") from exc

    query = """
        SELECT
            id,
            username,
            nama_lengkap,
            role,
            departemen,
            is_active,
            created_at,
            updated_at
        FROM database_users
        WHERE id = %s
        LIMIT 1
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(query, (user_id,))
                return cursor.fetchone()
            finally:
                cursor.close()

    except Exception:
        LOGGER.exception(
            "Gagal mengambil user berdasarkan ID %s.",
            user_id,
        )
        raise


# ============================================================
# CEK USERNAME
# ============================================================

def username_sudah_terdaftar(
    username: str,
    exclude_user_id: Optional[int] = None,
) -> bool:
    """
    Mengecek apakah username sudah digunakan.

    exclude_user_id digunakan ketika edit user agar username
    milik user yang sedang diedit tidak dianggap duplikat.
    """

    username = str(username or "").strip().lower()

    if not username:
        return False

    if exclude_user_id is not None:
        try:
            exclude_user_id = int(exclude_user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("ID user tidak valid.") from exc

    query = """
        SELECT id
        FROM database_users
        WHERE LOWER(username) = %s
    """

    params = [username]

    if exclude_user_id is not None:
        query += """
            AND id <> %s
        """
        params.append(exclude_user_id)

    query += """
        LIMIT 1
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(query, tuple(params))
                return cursor.fetchone() is not None
            finally:
                cursor.close()

    except Exception:
        LOGGER.exception("Gagal mengecek username.")
        raise


# ============================================================
# TAMBAH USER
# ============================================================

def tambah_user(
    username: str,
    password: str,
    nama_lengkap: str,
    role: str,
    departemen: str,
) -> bool:
    """
    Membuat user baru.
    """

    username = str(username or "").strip()
    nama_lengkap = str(nama_lengkap or "").strip()
    role = str(role or "").strip()
    departemen = str(departemen or "").strip() or "Umum"

    if not username:
        raise ValueError("Username wajib diisi.")

    if not password:
        raise ValueError("Password wajib diisi.")

    if not nama_lengkap:
        raise ValueError("Nama lengkap wajib diisi.")

    if not role:
        raise ValueError("Role wajib dipilih.")

    if username_sudah_terdaftar(username):
        raise ValueError("Username sudah terdaftar.")

    password_hash = buat_password_hash(password)

    query = """
        INSERT INTO database_users
        (
            username,
            password_hash,
            nama_lengkap,
            role,
            departemen,
            is_active
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            1
        )
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(
                    query,
                    (
                        username,
                        password_hash,
                        nama_lengkap,
                        role,
                        departemen,
                    ),
                )

                connection.commit()

            except Exception:
                connection.rollback()
                raise

            finally:
                cursor.close()

        return True

    except Exception:
        LOGGER.exception(
            "Gagal menambahkan user %s.",
            username,
        )
        raise


# ============================================================
# EDIT USER
# ============================================================

def edit_user(
    user_id: int,
    username: str,
    nama_lengkap: str,
    role: str,
    departemen: str,
) -> bool:
    """
    Mengubah data user tanpa mengubah password.

    Password memiliki fungsi terpisah melalui
    ubah_password_user().
    """

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID user tidak valid.") from exc

    username = str(username or "").strip()
    nama_lengkap = str(nama_lengkap or "").strip()
    role = str(role or "").strip()
    departemen = str(departemen or "").strip() or "Umum"

    if not username:
        raise ValueError("Username wajib diisi.")

    if not nama_lengkap:
        raise ValueError("Nama lengkap wajib diisi.")

    if not role:
        raise ValueError("Role wajib dipilih.")

    if username_sudah_terdaftar(
        username,
        exclude_user_id=user_id,
    ):
        raise ValueError("Username sudah digunakan user lain.")

    user = cari_user_by_id(user_id)

    if not user:
        raise ValueError("User tidak ditemukan.")

    query = """
        UPDATE database_users
        SET
            username = %s,
            nama_lengkap = %s,
            role = %s,
            departemen = %s
        WHERE id = %s
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(
                    query,
                    (
                        username,
                        nama_lengkap,
                        role,
                        departemen,
                        user_id,
                    ),
                )

                connection.commit()

            except Exception:
                connection.rollback()
                raise

            finally:
                cursor.close()

        return True

    except Exception:
        LOGGER.exception(
            "Gagal mengedit user ID %s.",
            user_id,
        )
        raise


# ============================================================
# UBAH STATUS USER
# ============================================================

def ubah_status_user(
    user_id: int,
    is_active: bool,
) -> bool:
    """
    Mengubah status user:

    True  = aktif
    False = nonaktif

    User admin utama tidak dapat dinonaktifkan.
    """

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID user tidak valid.") from exc

    user = cari_user_by_id(user_id)

    if not user:
        raise ValueError("User tidak ditemukan.")

    username = str(
        user.get("username", "")
    ).strip().lower()

    if username == "admin" and not is_active:
        raise ValueError(
            "User admin utama tidak boleh dinonaktifkan."
        )

    query = """
        UPDATE database_users
        SET is_active = %s
        WHERE id = %s
    """

    active_value = 1 if bool(is_active) else 0

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(
                    query,
                    (
                        active_value,
                        user_id,
                    ),
                )

                connection.commit()

            except Exception:
                connection.rollback()
                raise

            finally:
                cursor.close()

        return True

    except Exception:
        LOGGER.exception(
            "Gagal mengubah status user ID %s.",
            user_id,
        )
        raise


# ============================================================
# UBAH PASSWORD USER
# ============================================================

def ubah_password_user(
    user_id: int,
    password_baru: str,
) -> bool:
    """
    Mengubah password user.
    """

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID user tidak valid.") from exc

    password_baru = str(password_baru or "")

    if not password_baru:
        raise ValueError("Password baru wajib diisi.")

    user = cari_user_by_id(user_id)

    if not user:
        raise ValueError("User tidak ditemukan.")

    password_hash = buat_password_hash(password_baru)

    query = """
        UPDATE database_users
        SET password_hash = %s
        WHERE id = %s
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(
                    query,
                    (
                        password_hash,
                        user_id,
                    ),
                )

                connection.commit()

            except Exception:
                connection.rollback()
                raise

            finally:
                cursor.close()

        return True

    except Exception:
        LOGGER.exception(
            "Gagal mengubah password user ID %s.",
            user_id,
        )
        raise


# ============================================================
# HAPUS USER
# ============================================================

def hapus_user(user_id: int) -> bool:
    """
    Menghapus user berdasarkan ID.

    User admin utama tidak boleh dihapus.
    """

    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("ID user tidak valid.") from exc

    query_find = """
        SELECT
            id,
            username
        FROM database_users
        WHERE id = %s
        LIMIT 1
    """

    query_delete = """
        DELETE FROM database_users
        WHERE id = %s
    """

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor(dictionary=True)

            try:
                cursor.execute(
                    query_find,
                    (user_id,),
                )

                user = cursor.fetchone()

                if not user:
                    raise ValueError(
                        "User tidak ditemukan."
                    )

                username = str(
                    user["username"]
                ).strip().lower()

                if username == "admin":
                    raise ValueError(
                        "User admin utama tidak boleh dihapus."
                    )

                cursor.execute(
                    query_delete,
                    (user_id,),
                )

                connection.commit()

            except Exception:
                connection.rollback()
                raise

            finally:
                cursor.close()

        return True

    except Exception:
        LOGGER.exception(
            "Gagal menghapus user ID %s.",
            user_id,
        )
        raise