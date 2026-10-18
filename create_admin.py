from db_connection import get_db_connection
from modul_keamanan.auth_service import buat_password_hash


username = "admin"
password = "admin2026"
nama_lengkap = "Administrator Utama"
role = "Super Admin"
departemen = "IT & System Control"


password_hash = buat_password_hash(
    password
)


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
ON DUPLICATE KEY UPDATE
    password_hash = VALUES(password_hash),
    nama_lengkap = VALUES(nama_lengkap),
    role = VALUES(role),
    departemen = VALUES(departemen),
    is_active = 1
"""


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

        print(
            "User administrator berhasil dibuat."
        )

    finally:

        cursor.close()