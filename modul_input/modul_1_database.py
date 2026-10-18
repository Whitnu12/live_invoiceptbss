"""
MODUL 1 - DATABASE & MASTER KONTRAK
Flask + MySQL. Tidak menggunakan Streamlit/pandas.

Fitur:
- Master kontrak selalu dibangun dari database_proforma_invoice.
- Nomor PI ditampilkan descending berdasarkan nomor numeriknya.
- Kontrak baru langsung dapat muncul di master setelah disimpan.
- Tanggal kontrak + tanggal berakhir menghasilkan Jangka Waktu Kontrak otomatis.
- Kolom tanggal *_New menjadi sumber tanggal utama sistem.
- Periode Pekerjaan disimpan sebagai tanggal mulai dan tanggal selesai.
- Timestamp Data menjadi metadata sistem dan tidak diinput manual.
- Field tanggal dikirim ke MySQL dalam format YYYY-MM-DD.
- Progress Pekerjaan divalidasi 0-100 persen.
"""

import calendar
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import session

from db_connection import get_db_connection

LOGGER = logging.getLogger(__name__)
TABLE = "database_proforma_invoice"
READ_ONLY_ROLES = {"management", "direksi", "project manager", "pm"}

FIELD_GROUPS = {
    "master": [
        "Nomor Kontrak",
        "Nomor Tender",
        "Judul Kontrak",
        "Tanggal Kontrak",
        "Tanggal Berakhir Kontrak",
        "Jangka Waktu Kontrak",
        "Pihak Pertama",
        "Alamat Pihak Pertama",
        "Pihak Kedua",
        "Alamat Pihak Kedua",
    ],

    "pi": [
        # Urutan mengikuti alur pengisian PI: nomor dokumen, tanggal,
        # personil, lalu detail pekerjaan dan progres.
        "Proforma Invoice No.",
        "Nomor Purchase Order",
        "Nomor WCC",
        "Nomor WO",
        "Nomor CTR",
        "Tanggal Performa Invoice",
        "Tanggal Purchase Order",
        "Tanggal WCC",
        "Diwakili Oleh",
        "Selaku",
        "Diwakili Oleh (P2)",
        "Selaku (P2)",
        "Keterangan WO",
        "Lingkup Pekerjaan",
        "Periode Pekerjaan Mulai",
        "Periode Pekerjaan Selesai",
        "Progress Pekerjaan",
        "Jenis BASTP",
    ],

    "approval": [
        "Prepared by Name",
        "Prepared by Title",
        "Approved by 1",
        "Approved by Title 1",
        "Approved by 2",
        "Approved by Title 2",
    ],
}
ALL_FIELDS = FIELD_GROUPS["master"] + FIELD_GROUPS["pi"] + FIELD_GROUPS["approval"]
DATE_FIELDS = {
    "Tanggal Kontrak",
    "Tanggal Berakhir Kontrak",
    "Tanggal Performa Invoice",
    "Tanggal Purchase Order",
    "Tanggal WCC",
    "Periode Pekerjaan Mulai",
    "Periode Pekerjaan Selesai",
}
NUMERIC_PERCENT_FIELDS = {"Progress Pekerjaan"}

ALIASES = {
    "Proforma Invoice No.": ["proformainvoiceno", "pino", "pinumber", "nomorproformainvoice"],
    "Nomor Kontrak": ["nomorkontrak", "kontrak", "contractno", "contractnumber"],
    "Nomor Tender": ["nomortender", "tenderno", "tendernumber"],
    "Lingkup Pekerjaan": ["lingkupp pekerjaan", "lingkupp pekerjaan".replace(" ", ""), "lingkuppekerjaan", "scopeofwork"],
    # Kolom *_New menjadi sumber utama. Kolom lama hanya fallback untuk kompatibilitas.
    "Tanggal Kontrak": [
        "Tanggal Kontrak New", "tanggalkontraknew",
        "Tanggal Kontrak", "tanggalkontrak", "contractdate"
    ],
    "Tanggal Berakhir Kontrak": ["tanggalberakhirkontrak", "tanggalakhirkontrak", "contractenddate", "enddate"],
    "Jangka Waktu Kontrak": ["jangkawaktukontrak", "contractduration"],
    "Tanggal Performa Invoice": [
        "Tanggal Performa Invoice New", "tanggalperformainvoicenew",
        "Tanggal Performa Invoice", "tanggalperformainvoice", "tanggalpi", "pidate"
    ],
    "Judul Kontrak": ["judulkontrak", "namakontrak", "contracttitle"],
    "Nomor Purchase Order": ["nomorpurchaseorder", "nomorpo", "nopo", "ponumber", "purchaseorderno"],
    "Tanggal Purchase Order": [
        "Tanggal Purchase Order New", "tanggalpurchaseordernew",
        "Tanggal Purchase Order", "tanggalpurchaseorder", "tanggalpo", "podate"
    ],
    "Pihak Pertama": ["pihakpertama", "firstparty"],
    "Alamat Pihak Pertama": ["alamatpihakpertama", "firstpartyaddress"],
    "Diwakili Oleh": ["diwakilioleh", "wakilpihakpertama", "representative"],
    "Selaku": ["selaku", "jabatanpihakpertama", "firstpartytitle"],
    "Pihak Kedua": ["pihakkedua", "secondparty"],
    "Alamat Pihak Kedua": ["alamatpihakkedua", "secondpartyaddress"],
    "Diwakili Oleh (P2)": ["diwakiliolehp2", "diwakilioleh2", "wakilpihakkedua", "secondpartyrepresentative"],
    "Selaku (P2)": ["selakup2", "selaku2", "jabatanpihakkedua", "secondpartytitle"],
    "Periode Pekerjaan Mulai": [
        "Periode Pekerjaan Mulai", "periodepekerjaanmulai",
        "Tanggal Mulai Periode", "tanggalmulaiperiode"
    ],
    "Periode Pekerjaan Selesai": [
        "Periode Pekerjaan Selesai", "periodepekerjaanselesai",
        "Tanggal Selesai Periode", "tanggalselesaiperiode"
    ],
    "Nomor WCC": ["nomorwcc", "wccno"],
    "Tanggal WCC": [
        "Tanggal WCC New", "tanggalwccnew",
        "Tanggal WCC", "tanggalwcc", "wccdate"
    ],
    "Nomor WO": ["nomorwo", "wono", "workorderno"],
    "Keterangan WO": ["keteranganwo", "workorderdescription", "wodescription"],
    "Nomor CTR": ["nomorctr", "ctrno"],
    "Progress Pekerjaan": ["progresspekerjaan", "progress", "progresspekerjaanpercent"],
    "Jenis BASTP": ["jenis_bastp", "jenisbastp", "jenisbastp", "bastptype"],
    "Prepared by Name": ["preparedbyname", "preparedname"],
    "Prepared by Title": ["preparedbytitle", "preparedtitle"],
    "Approved by 1": ["approvedby1", "approvedname1"],
    "Approved by Title 1": ["approvedbytitle1", "approvedtitle1"],
    "Approved by 2": ["approvedby2", "approvedname2"],
    "Approved by Title 2": ["approvedbytitle2", "approvedtitle2"],
}


def clean(value):
    return "" if value is None else str(value).strip()


def norm(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def role():
    return clean(session.get("current_role", "Staff")).lower()


def can_write():
    return role() not in READ_ONLY_ROLES


def table_columns(connection):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (TABLE,))
        return cursor.fetchall()
    finally:
        cursor.close()


def field_column_map(columns):
    """
    Map logical field -> physical MySQL column.

    Untuk field tanggal, kolom *_New selalu diprioritaskan.
    Jika kolom New belum ada, sistem masih dapat membaca kolom lama
    sehingga deployment dapat dilakukan bertahap.
    """
    by_norm = {norm(c["COLUMN_NAME"]): c["COLUMN_NAME"] for c in columns}
    result = {}

    for field in ALL_FIELDS:
        candidates = ALIASES.get(field, [])
        # ALIASES sudah disusun dari prioritas tertinggi ke terendah.
        ordered = candidates + [field]

        for candidate in ordered:
            key = norm(candidate)
            if key in by_norm:
                result[field] = by_norm[key]
                break

    return result


def q(identifier):
    return "`" + str(identifier).replace("`", "``") + "`"


def parse_date_value(value, field_name="Tanggal"):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean(value)
    # Accept common formats but always return a Python date for MySQL.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field_name} harus berupa tanggal yang valid (YYYY-MM-DD).")


def serialize(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = clean(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def normalize_row(row, mapping):
    result = {
        field: serialize(row.get(column))
        for field, column in mapping.items()
    }

    # Timestamp Data adalah metadata sistem, bukan field input user.
    timestamp_column = next(
        (
            column for column in row.keys()
            if norm(column) == norm("Timestamp Data")
        ),
        None,
    )
    if timestamp_column:
        result["Timestamp Data"] = serialize(row.get(timestamp_column))

    if "id" in row:
        result["_id"] = row["id"]

    return result


def numeric_sort_key(value):
    text = clean(value)
    match = re.search(r"\d+", text)
    number = int(match.group()) if match else -1
    return (number, text.upper())


def sort_contract_records(records):
    return sorted(records, key=lambda r: numeric_sort_key(r.get("Nomor Kontrak")), reverse=True)


def sort_pi_records(records):
    return sorted(records, key=lambda r: numeric_sort_key(r.get("Proforma Invoice No.")), reverse=True)


def calculate_duration(start_value, end_value):
    start = parse_date_value(start_value, "Tanggal Kontrak")
    end = parse_date_value(end_value, "Tanggal Berakhir Kontrak")
    if not start or not end:
        return ""
    if end < start:
        raise ValueError("Tanggal Berakhir Kontrak tidak boleh lebih awal dari Tanggal Kontrak.")

    years = end.year - start.year
    try:
        anchor = start.replace(year=start.year + years)
    except ValueError:
        anchor = start.replace(year=start.year + years, day=28)
    if anchor > end:
        years -= 1
        try:
            anchor = start.replace(year=start.year + years)
        except ValueError:
            anchor = start.replace(year=start.year + years, day=28)

    months = (end.year - anchor.year) * 12 + (end.month - anchor.month)

    def add_months(d, count):
        y = d.year + (d.month - 1 + count) // 12
        m = (d.month - 1 + count) % 12 + 1
        day = min(d.day, calendar.monthrange(y, m)[1])
        return d.replace(year=y, month=m, day=day)

    anchor2 = add_months(anchor, months)
    if anchor2 > end:
        months -= 1
        anchor2 = add_months(anchor, months)
    days = (end - anchor2).days

    parts = []
    if years:
        parts.append(f"{years} Tahun")
    if months:
        parts.append(f"{months} Bulan")
    if days or not parts:
        parts.append(f"{days} Hari")
    return " ".join(parts)


def validate_progress(value):
    text = clean(value)
    if not text:
        return ""
    try:
        number = Decimal(text.replace(",", "."))
    except InvalidOperation:
        raise ValueError("Progress Pekerjaan harus berupa angka persentase.")
    if number < 0 or number > 100:
        raise ValueError("Progress Pekerjaan harus berada di antara 0 dan 100%.")
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def timestamp_column_name(columns):
    """Return the physical Timestamp Data column name, if present."""
    for column in columns:
        if norm(column["COLUMN_NAME"]) == norm("Timestamp Data"):
            return column["COLUMN_NAME"]
    return None


def load_rows(filters=None):
    filters = filters or {}
    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)
        if "Nomor Kontrak" not in mapping or "Proforma Invoice No." not in mapping:
            missing = [f for f in ("Nomor Kontrak", "Proforma Invoice No.") if f not in mapping]
            raise RuntimeError("Kolom wajib tidak ditemukan: " + ", ".join(missing))

        select_parts = [q(c["COLUMN_NAME"]) for c in columns]
        conditions, params = [], []
        if clean(filters.get("nomor_kontrak")):
            conditions.append(f"{q(mapping['Nomor Kontrak'])} = %s")
            params.append(clean(filters["nomor_kontrak"]))
        if clean(filters.get("search")):
            term = f"%{clean(filters['search'])}%"
            searchable = [mapping.get(f) for f in ("Nomor Kontrak", "Proforma Invoice No.", "Judul Kontrak", "Nomor Tender")]
            searchable = [x for x in searchable if x]
            if searchable:
                conditions.append("(" + " OR ".join(f"{q(c)} LIKE %s" for c in searchable) + ")")
                params.extend([term] * len(searchable))

        sql = f"SELECT {', '.join(select_parts)} FROM {q(TABLE)}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        # Ambil record terbaru terlebih dahulu jika Timestamp Data tersedia.
        # Urutan PI tetap diatur oleh sort_pi_records() setelah data dinormalisasi.
        timestamp_column = timestamp_column_name(columns)
        if timestamp_column:
            sql += f" ORDER BY {q(timestamp_column)} DESC"
        else:
            sql += " ORDER BY 1 ASC"
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        finally:
            cursor.close()
    return rows, mapping, columns


def find_row(nomor_kontrak, pi):
    rows, mapping, columns = load_rows({"nomor_kontrak": nomor_kontrak})
    target = next((r for r in rows if clean(r.get(mapping["Proforma Invoice No."])) == clean(pi)), None)
    return target, mapping, columns


def payload_to_db(payload, mapping, columns=None):
    columns = columns or []
    types = {c["COLUMN_NAME"]: clean(c.get("DATA_TYPE")).lower() for c in columns}
    data = {}
    for field in ALL_FIELDS:
        if field not in mapping:
            continue
        value = payload.get(field, "")
        if field in DATE_FIELDS:
            data[mapping[field]] = parse_date_value(value, field)
        elif field in NUMERIC_PERCENT_FIELDS:
            data[mapping[field]] = validate_progress(value)
        elif field == "Jangka Waktu Kontrak":
            data[mapping[field]] = clean(value)
        else:
            data[mapping[field]] = clean(value)

        # Empty values for DATE/DATETIME columns must be NULL, not an empty string.
        if data[mapping[field]] == "" and types.get(mapping[field]) in {"date", "datetime", "timestamp"}:
            data[mapping[field]] = None
    return data


def prepare_payload(payload):
    payload = dict(payload or {})
    if not clean(payload.get("Nomor Kontrak")):
        raise ValueError("Nomor Kontrak wajib diisi.")
    if not clean(payload.get("Proforma Invoice No.")):
        raise ValueError("Proforma Invoice No. wajib diisi.")

    if clean(payload.get("Tanggal Kontrak")) and clean(payload.get("Tanggal Berakhir Kontrak")):
        payload["Tanggal Kontrak"] = parse_date_value(payload["Tanggal Kontrak"], "Tanggal Kontrak").isoformat()
        payload["Tanggal Berakhir Kontrak"] = parse_date_value(payload["Tanggal Berakhir Kontrak"], "Tanggal Berakhir Kontrak").isoformat()
        payload["Jangka Waktu Kontrak"] = calculate_duration(payload["Tanggal Kontrak"], payload["Tanggal Berakhir Kontrak"])
    elif clean(payload.get("Tanggal Kontrak")) or clean(payload.get("Tanggal Berakhir Kontrak")):
        # Validate whichever date was supplied, but don't invent a duration.
        if clean(payload.get("Tanggal Kontrak")):
            payload["Tanggal Kontrak"] = parse_date_value(payload["Tanggal Kontrak"], "Tanggal Kontrak").isoformat()
        if clean(payload.get("Tanggal Berakhir Kontrak")):
            payload["Tanggal Berakhir Kontrak"] = parse_date_value(payload["Tanggal Berakhir Kontrak"], "Tanggal Berakhir Kontrak").isoformat()
    if clean(payload.get("Periode Pekerjaan Mulai")):
        payload["Periode Pekerjaan Mulai"] = parse_date_value(
            payload["Periode Pekerjaan Mulai"],
            "Periode Pekerjaan Mulai",
        ).isoformat()

    if clean(payload.get("Periode Pekerjaan Selesai")):
        payload["Periode Pekerjaan Selesai"] = parse_date_value(
            payload["Periode Pekerjaan Selesai"],
            "Periode Pekerjaan Selesai",
        ).isoformat()

    mulai = clean(payload.get("Periode Pekerjaan Mulai"))
    selesai = clean(payload.get("Periode Pekerjaan Selesai"))

    if mulai and selesai:
        if parse_date_value(selesai, "Periode Pekerjaan Selesai") < parse_date_value(
            mulai, "Periode Pekerjaan Mulai"
        ):
            raise ValueError(
                "Periode Pekerjaan Selesai tidak boleh lebih awal dari "
                "Periode Pekerjaan Mulai."
            )

    payload["Progress Pekerjaan"] = validate_progress(
        payload.get("Progress Pekerjaan")
    )
    return payload


def insert_pi(payload):
    payload = prepare_payload(payload)
    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)
        if "Tanggal Berakhir Kontrak" not in mapping:
            raise RuntimeError("Kolom 'Tanggal Berakhir Kontrak' belum ada di database_proforma_invoice. Tambahkan kolom tersebut terlebih dahulu.")
        data = payload_to_db(payload, mapping, columns)
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s AND {q(mapping['Proforma Invoice No.'])} = %s",
                (clean(payload["Nomor Kontrak"]), clean(payload["Proforma Invoice No."])),
            )
            if int(cursor.fetchone()["total"]) > 0:
                raise ValueError("PI tersebut sudah ada untuk Nomor Kontrak yang dipilih.")
            if not data:
                raise ValueError("Tidak ada kolom Module 1 yang cocok dengan struktur database.")
            names = list(data.keys())
            cursor.execute(
                f"INSERT INTO {q(TABLE)} ({', '.join(q(x) for x in names)}) VALUES ({', '.join(['%s'] * len(names))})",
                tuple(data[x] for x in names),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()



def save_as_pi(contract, old_pi, new_pi):
    """Clone an existing PI into a new PI number within the same contract."""
    contract = clean(contract)
    old_pi = clean(old_pi)
    new_pi = clean(new_pi)

    if not contract or not old_pi or not new_pi:
        raise ValueError("Nomor Kontrak, PI lama, dan PI baru wajib ditentukan.")
    if old_pi == new_pi:
        raise ValueError("Nomor PI baru harus berbeda dari PI sumber.")

    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)

        if "Nomor Kontrak" not in mapping or "Proforma Invoice No." not in mapping:
            raise RuntimeError("Kolom Nomor Kontrak / Proforma Invoice tidak ditemukan.")

        source, _, _ = find_row(contract, old_pi)
        if not source:
            raise ValueError(f"PI sumber tidak ditemukan: kontrak '{contract}', PI '{old_pi}'.")

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM {q(TABLE)} "
                f"WHERE {q(mapping['Nomor Kontrak'])} = %s "
                f"AND {q(mapping['Proforma Invoice No.'])} = %s",
                (contract, new_pi),
            )
            if int(cursor.fetchone()["total"]) > 0:
                raise ValueError("Nomor PI baru sudah digunakan pada kontrak ini.")

            data = {}
            for field in ALL_FIELDS:
                if field not in mapping:
                    continue
                column = mapping[field]
                value = source.get(column)
                if field in DATE_FIELDS:
                    value = parse_date_value(value, field) if clean(value) else None
                elif field == "Progress Pekerjaan":
                    value = validate_progress(value)
                else:
                    value = clean(value)
                data[column] = value

            data[mapping["Nomor Kontrak"]] = contract
            data[mapping["Proforma Invoice No."]] = new_pi

            if not data:
                raise ValueError("Tidak ada field yang cocok dengan struktur database.")

            names = list(data.keys())
            cursor.execute(
                f"INSERT INTO {q(TABLE)} ({', '.join(q(x) for x in names)}) "
                f"VALUES ({', '.join(['%s'] * len(names))})",
                tuple(data[x] for x in names),
            )
            connection.commit()
            return {
                "contract": contract,
                "old_pi": old_pi,
                "new_pi": new_pi,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()


def update_pi(old_contract, old_pi, payload):
    """Update one PI row using its original contract + PI as the stable locator."""
    payload = prepare_payload(payload)
    old_contract = clean(old_contract)
    old_pi = clean(old_pi)
    if not old_contract or not old_pi:
        raise ValueError("Nomor Kontrak lama dan Proforma Invoice lama wajib ditentukan.")

    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)
        if "Tanggal Berakhir Kontrak" not in mapping:
            raise RuntimeError("Kolom 'Tanggal Berakhir Kontrak' belum ada di database_proforma_invoice.")

        target, _, _ = find_row(old_contract, old_pi)
        if not target:
            raise ValueError(f"Data PI lama tidak ditemukan: kontrak '{old_contract}', PI '{old_pi}'.")

        new_contract = clean(payload.get("Nomor Kontrak"))
        new_pi = clean(payload.get("Proforma Invoice No."))
        if not new_contract or not new_pi:
            raise ValueError("Nomor Kontrak dan Proforma Invoice wajib diisi.")

        id_column = next((c["COLUMN_NAME"] for c in columns if norm(c["COLUMN_NAME"]) == "id"), None)
        cursor = connection.cursor(dictionary=True)
        try:
            # Prevent a PI update from silently colliding with another row.
            if new_contract != old_contract or new_pi != old_pi:
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s AND {q(mapping['Proforma Invoice No.'])} = %s",
                    (new_contract, new_pi),
                )
                if int(cursor.fetchone()["total"]) > 0:
                    raise ValueError("Nomor PI tersebut sudah digunakan pada kontrak tujuan.")

            data = payload_to_db(payload, mapping, columns)
            if not data:
                raise ValueError("Tidak ada field yang cocok dengan database.")

            set_sql = ", ".join(f"{q(k)} = %s" for k in data)
            params = list(data.values())
            if id_column and target.get(id_column) is not None:
                where = f"{q(id_column)} = %s"
                params.append(target[id_column])
            else:
                where = f"{q(mapping['Nomor Kontrak'])} = %s AND {q(mapping['Proforma Invoice No.'])} = %s"
                params.extend([old_contract, old_pi])

            cursor.execute(f"UPDATE {q(TABLE)} SET {set_sql} WHERE {where} LIMIT 1", tuple(params))
            if cursor.rowcount == 0:
                # MySQL may report 0 when the submitted values are identical.
                # Verify the row still exists instead of reporting a false failure.
                if id_column and target.get(id_column) is not None:
                    cursor.execute(f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(id_column)} = %s", (target[id_column],))
                else:
                    cursor.execute(f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s AND {q(mapping['Proforma Invoice No.'])} = %s", (new_contract, new_pi))
                if int(cursor.fetchone()["total"]) == 0:
                    raise ValueError("Data PI tidak ditemukan setelah proses update.")

            connection.commit()
            return {"old_contract": old_contract, "old_pi": old_pi, "new_contract": new_contract, "new_pi": new_pi}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

def get_contract_impact(nomor_kontrak):
    """Return the number of PI records affected by a master contract edit."""
    nomor_kontrak = clean(nomor_kontrak)
    if not nomor_kontrak:
        raise ValueError("Nomor Kontrak wajib diisi.")

    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)
        if "Nomor Kontrak" not in mapping:
            raise RuntimeError("Kolom 'Nomor Kontrak' tidak ditemukan.")
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s",
                (nomor_kontrak,),
            )
            total = int(cursor.fetchone()["total"])
            return {"nomor_kontrak": nomor_kontrak, "total_pi": total}
        finally:
            cursor.close()


def update_contract_master(old_contract, payload):
    """
    Update master contract fields across every PI belonging to the contract.

    This operation is transactional: either every affected PI receives the
    new master data, or the whole change is rolled back. PI-specific fields
    are intentionally excluded.
    """
    old_contract = clean(old_contract)
    payload = dict(payload or {})
    if not old_contract:
        raise ValueError("Nomor Kontrak lama wajib diisi.")
    new_contract = clean(payload.get("Nomor Kontrak"))
    if not new_contract:
        raise ValueError("Nomor Kontrak baru wajib diisi.")

    master_fields = FIELD_GROUPS["master"]
    # Contract duration is derived when both dates are supplied.
    if clean(payload.get("Tanggal Kontrak")) and clean(payload.get("Tanggal Berakhir Kontrak")):
        start = parse_date_value(payload["Tanggal Kontrak"], "Tanggal Kontrak")
        end = parse_date_value(payload["Tanggal Berakhir Kontrak"], "Tanggal Berakhir Kontrak")
        if end < start:
            raise ValueError("Tanggal Berakhir Kontrak tidak boleh lebih awal dari Tanggal Kontrak.")
        payload["Tanggal Kontrak"] = start.isoformat()
        payload["Tanggal Berakhir Kontrak"] = end.isoformat()
        payload["Jangka Waktu Kontrak"] = calculate_duration(payload["Tanggal Kontrak"], payload["Tanggal Berakhir Kontrak"])
    else:
        for field in ("Tanggal Kontrak", "Tanggal Berakhir Kontrak"):
            if clean(payload.get(field)):
                payload[field] = parse_date_value(payload[field], field).isoformat()

    with get_db_connection() as connection:
        columns = table_columns(connection)
        mapping = field_column_map(columns)
        if "Nomor Kontrak" not in mapping:
            raise RuntimeError("Kolom 'Nomor Kontrak' tidak ditemukan.")

        data = {}
        for field in master_fields:
            if field in mapping:
                value = payload.get(field, "")
                if field in DATE_FIELDS:
                    value = parse_date_value(value, field) if clean(value) else None
                elif field == "Jangka Waktu Kontrak":
                    value = clean(value)
                else:
                    value = clean(value)
                data[mapping[field]] = value

        # Never modify technical Timestamp Data through the request.
        if not data:
            raise ValueError("Tidak ada field master yang cocok dengan struktur database.")

        id_column = next((c["COLUMN_NAME"] for c in columns if norm(c["COLUMN_NAME"]) == "id"), None)
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s",
                (old_contract,),
            )
            affected = int(cursor.fetchone()["total"])
            if affected == 0:
                raise ValueError("Kontrak yang akan diubah tidak ditemukan.")

            if new_contract != old_contract:
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s",
                    (new_contract,),
                )
                if int(cursor.fetchone()["total"]) > 0:
                    raise ValueError("Nomor Kontrak baru sudah digunakan oleh kontrak lain.")

            set_sql = ", ".join(f"{q(column)} = %s" for column in data)
            params = list(data.values()) + [old_contract]
            cursor.execute(
                f"UPDATE {q(TABLE)} SET {set_sql} WHERE {q(mapping['Nomor Kontrak'])} = %s",
                tuple(params),
            )
            changed_rows = cursor.rowcount
            if changed_rows <= 0:
                # UPDATE can return 0 when all submitted values are already equal.
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s",
                    (new_contract,),
                )
                if int(cursor.fetchone()["total"]) != affected:
                    raise ValueError("Data kontrak tidak ditemukan setelah proses update.")
            connection.commit()
            return {"old_contract": old_contract, "new_contract": new_contract, "affected_pi": affected, "changed_rows": changed_rows}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

def delete_pi(contract, pi):
    contract, pi = clean(contract), clean(pi)
    if not contract or not pi:
        raise ValueError("Kontrak dan PI wajib dipilih.")
    with get_db_connection() as connection:
        columns = table_columns(connection); mapping = field_column_map(columns)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DELETE FROM {q(TABLE)} WHERE {q(mapping['Nomor Kontrak'])} = %s AND {q(mapping['Proforma Invoice No.'])} = %s LIMIT 1", (contract, pi))
            if cursor.rowcount == 0:
                raise ValueError("Data PI tidak ditemukan.")
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            cursor.close()
