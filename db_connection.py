"""Koneksi MySQL untuk aplikasi invoice Flask.

Seluruh kredensial dibaca dari Environment Variables cPanel. Modul ini tidak
bergantung pada Streamlit dan tidak membuka koneksi ketika baru di-import.
"""

import json
import logging
import math
import os
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Sequence, Tuple

from mysql.connector import pooling


LOGGER = logging.getLogger(__name__)
TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_POOL = None


class DatabaseConfigurationError(RuntimeError):
    """Konfigurasi database belum lengkap atau tidak valid."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DatabaseConfigurationError(
            "Environment Variable {} belum diatur.".format(name)
        )
    return value


def _database_port() -> int:
    try:
        return int(os.getenv("DB_PORT", "3306").strip())
    except ValueError as exc:
        raise DatabaseConfigurationError("DB_PORT harus berupa angka.") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _create_pool():
    return pooling.MySQLConnectionPool(
        pool_name="invoice_pool",
        # Master PO membuka beberapa read-only endpoint saat halaman pertama
        # kali dimuat (list, analytics, contracts, reference options, unmapped).
        # Pool tiga koneksi membuat request paralel mendapat "Connection not available".
        pool_size=int(os.getenv("DB_POOL_SIZE", "6")),
        # Pada koneksi stale, reset_session() saat close() dapat memunculkan
        # 2013 dan menutupi hasil query yang sebenarnya sudah selesai.
        # Transaksi tetap dibersihkan dengan rollback best-effort di context
        # manager. Perilaku lama dapat diaktifkan eksplisit melalui env.
        pool_reset_session=_env_bool("DB_POOL_RESET_SESSION", False),
        host=_required_env("DB_HOST"),
        port=_database_port(),
        database=_required_env("DB_NAME"),
        user=_required_env("DB_USER"),
        password=_required_env("DB_PASSWORD"),
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
        connection_timeout=10,
    )


def _get_pool():
    global _POOL
    if _POOL is None:
        _POOL = _create_pool()
    return _POOL


@contextmanager
def get_db_connection() -> Iterator[Any]:
    """Mengambil koneksi dari pool dan selalu mengembalikannya."""
    connection = _get_pool().get_connection()
    try:
        # Koneksi dari pool dapat diputus MySQL setelah idle/wait_timeout.
        # Ping dengan reconnect mencegah query pertama memakai koneksi stale.
        connection.ping(reconnect=True, attempts=2, delay=1)
        yield connection
    except Exception:
        try:
            connection.rollback()
        except Exception:
            LOGGER.warning("Rollback MySQL gagal saat cleanup koneksi.", exc_info=True)
        raise
    finally:
        # Jangan biarkan kegagalan reset/close dari koneksi yang sudah putus
        # mengubah hasil operasi utama atau membuat worker jatuh.
        try:
            connection.rollback()
        except Exception:
            LOGGER.warning("Rollback akhir MySQL gagal saat cleanup koneksi.", exc_info=True)
        try:
            connection.close()
        except Exception:
            LOGGER.warning(
                "Gagal mengembalikan koneksi MySQL ke pool; koneksi kemungkinan stale.",
                exc_info=True,
            )
            try:
                connection.disconnect()
            except Exception:
                LOGGER.debug("Disconnect koneksi MySQL stale juga gagal.", exc_info=True)


def uji_koneksi_database() -> Tuple[bool, str]:
    """Menguji koneksi dan database aktif tanpa menampilkan kredensial."""
    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT DATABASE()")
                row = cursor.fetchone()
            finally:
                cursor.close()

        selected_database = row[0] if row else None
        if selected_database != _required_env("DB_NAME"):
            return False, "Database aktif tidak sesuai dengan DB_NAME."
        return True, "Koneksi MySQL berhasil."
    except Exception as exc:
        LOGGER.exception("Koneksi MySQL gagal.")
        return False, "Koneksi MySQL gagal: {}".format(exc)


def _table_name(value: str) -> str:
    value = str(value or "").strip()
    if not TABLE_NAME_PATTERN.fullmatch(value):
        raise ValueError("Nama tabel tidak valid.")
    return value


def _quoted_table(value: str) -> str:
    return "`{}`".format(_table_name(value))


def _quoted_column(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise DatabaseConfigurationError("Nama kolom database tidak valid.")
    return "`{}`".format(value.replace("`", "``"))


def _table_metadata(connection: Any, table_name: str) -> List[Dict[str, Any]]:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COLUMN_NAME, EXTRA
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (_required_env("DB_NAME"), table_name),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()

    if not rows:
        raise DatabaseConfigurationError(
            "Tabel {} tidak ditemukan.".format(table_name)
        )
    return rows


def _unique_columns(connection: Any, table_name: str) -> List[str]:
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT DISTINCT COLUMN_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND NON_UNIQUE = 0
            """,
            (_required_env("DB_NAME"), table_name),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return [row["COLUMN_NAME"] for row in rows]


def _normalise_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def muat_data_from_db(nama_tabel: str) -> List[Dict[str, Any]]:
    """Mengambil seluruh data tabel sebagai list of dictionaries."""
    table_name = _table_name(nama_tabel)
    try:
        with get_db_connection() as connection:
            _table_metadata(connection, table_name)
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM {}".format(_quoted_table(table_name)))
                return cursor.fetchall()
            finally:
                cursor.close()
    except Exception:
        LOGGER.exception("Gagal membaca tabel %s.", table_name)
        return []


def simpan_data_to_db(
    nama_tabel: str, data_list: Sequence[Mapping[str, Any]]
) -> bool:
    """Menyimpan data memakai INSERT ... ON DUPLICATE KEY UPDATE."""
    if not data_list:
        return False

    table_name = _table_name(nama_tabel)
    try:
        with get_db_connection() as connection:
            metadata = _table_metadata(connection, table_name)
            ordered_columns = [row["COLUMN_NAME"] for row in metadata]
            allowed_columns = set(ordered_columns)
            auto_columns = {
                row["COLUMN_NAME"]
                for row in metadata
                if "auto_increment" in (row.get("EXTRA") or "").lower()
            }
            unique_columns = set(_unique_columns(connection, table_name))
            cursor = connection.cursor()

            try:
                for raw_row in data_list:
                    if not isinstance(raw_row, Mapping):
                        raise TypeError("Setiap baris harus berupa dictionary.")

                    unknown_columns = set(raw_row.keys()) - allowed_columns
                    if unknown_columns:
                        raise DatabaseConfigurationError(
                            "Kolom tidak tersedia pada tabel {}: {}".format(
                                table_name,
                                ", ".join(sorted(str(c) for c in unknown_columns)),
                            )
                        )

                    columns = [
                        column
                        for column in ordered_columns
                        if column in raw_row and column not in auto_columns
                    ]
                    if not columns:
                        raise DatabaseConfigurationError(
                            "Tidak ada kolom yang dapat disimpan."
                        )

                    column_sql = ", ".join(_quoted_column(c) for c in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    values = [_normalise_value(raw_row[c]) for c in columns]
                    update_columns = [c for c in columns if c not in unique_columns]

                    if update_columns:
                        update_sql = ", ".join(
                            "{0} = VALUES({0})".format(_quoted_column(c))
                            for c in update_columns
                        )
                    else:
                        first_column = _quoted_column(columns[0])
                        update_sql = "{0} = {0}".format(first_column)

                    query = (
                        "INSERT INTO {table} ({columns}) VALUES ({values}) "
                        "ON DUPLICATE KEY UPDATE {updates}"
                    ).format(
                        table=_quoted_table(table_name),
                        columns=column_sql,
                        values=placeholders,
                        updates=update_sql,
                    )
                    cursor.execute(query, values)

                connection.commit()
                return True
            finally:
                cursor.close()
    except Exception:
        LOGGER.exception("Gagal menyimpan data ke tabel %s.", table_name)
        return False
