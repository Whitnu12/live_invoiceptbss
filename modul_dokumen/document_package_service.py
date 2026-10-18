from __future__ import annotations
import json
import logging
from typing import Any, Dict, Iterable, Optional
from db_connection import get_db_connection
from modul_opname.services import ensure_opname_schema, get_opname_context
from modul_po.master_po import (
    is_amount_uom,
    is_estimated_category,
    is_provisional_category,
)
LOGGER = logging.getLogger(__name__)

def _clean(v: Any) -> str:
    return '' if v is None else str(v).strip()

def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict): return row.get(key)
    try: return row[index]
    except Exception: return None

def load_saved_tkdn(nomor_kontrak: str, pi_no: str) -> Dict[str, Any]:
    nomor_kontrak, pi_no = _clean(nomor_kontrak), _clean(pi_no)
    if not nomor_kontrak or not pi_no: return {}
    with get_db_connection() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute('SELECT params_json FROM database_tkdn WHERE nomor_kontrak=%s AND pi_no=%s LIMIT 1',(nomor_kontrak,pi_no))
            row=cursor.fetchone()
        finally: cursor.close()
    raw=_row_value(row,'params_json',0)
    if not raw: return {}
    try:
        data=json.loads(raw); return data if isinstance(data,dict) else {}
    except (TypeError,ValueError,json.JSONDecodeError):
        LOGGER.exception('[PACKAGE] TKDN params_json invalid'); return {}

def load_saved_opname(nomor_kontrak: str, pi_no: str, nomor_po: str = "") -> Dict[str, Any]:
    """Ambil data Opname langsung dari MariaDB tabel ``database_opname``.

    Kunci pencarian utama adalah Nomor PI. Jika Nomor PO tersedia, batasi
    hasil ke PO tersebut agar snapshot Opname lain pada PI yang sama tidak
    tercampur ke paket.
    """
    nomor_kontrak = _clean(nomor_kontrak)
    pi_no = _clean(pi_no)
    nomor_po = _clean(nomor_po)

    if not pi_no:
        return {}

    # Ensure older installations receive the signature columns before the
    # package renderer reads the saved Opname rows.
    ensure_opname_schema()

    sql = """
        SELECT
            o.id,
            o.nomor_kontrak,
            o.nomor_pi,
            o.nomor_po,
            o.tanggal_opname,
            o.lokasi_office,
            o.item_key,
            o.po_item_id,
            o.item_no,
            o.kategori,
            o.deskripsi_pekerjaan,
            o.uom,
            o.qty_po,
            o.unit_price,
            o.volume_previous,
            o.volume_actual,
            o.volume_cumulative,
            o.volume_remaining,
            o.nilai_previous,
            o.nilai_actual,
            o.nilai_cumulative,
            o.nilai_remaining,
            o.mapping_status,
            o.ttd_1,
            o.ttd_2,
            o.ttd_3,
            mpi.total_plafon AS po_total
        FROM database_opname o
        LEFT JOIN database_master_po_item mpi ON mpi.id=o.po_item_id
        WHERE TRIM(CAST(o.nomor_pi AS CHAR)) = %s
    """
    params = [pi_no]

    # Jika kontrak tersedia, gunakan sebagai filter tambahan. Namun PI tetap
    # merupakan kunci utama sehingga PO tidak boleh membuat data hilang.
    if nomor_kontrak:
        sql += " AND TRIM(CAST(o.nomor_kontrak AS CHAR)) = %s"
        params.append(nomor_kontrak)
    if nomor_po:
        sql += " AND TRIM(COALESCE(CAST(o.nomor_po AS CHAR), '')) = %s"
        params.append(nomor_po)

    sql += " ORDER BY COALESCE(item_no, 999999), id"

    try:
        with get_db_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, tuple(params))
                raw_rows = cursor.fetchall() or []
                description = getattr(cursor, "description", None) or []
                columns = [col[0] for col in description]
            finally:
                cursor.close()

        rows = []
        for raw in raw_rows:
            if isinstance(raw, dict):
                row = dict(raw)
            else:
                row = dict(zip(columns, raw)) if columns else {}
            if row:
                rows.append(row)

        if not rows:
            LOGGER.debug(
                "[PACKAGE] Snapshot Opname tersimpan tidak ditemukan: kontrak=%s PI=%s PO=%s",
                nomor_kontrak,
                pi_no,
                nomor_po or "(semua PO)",
            )
            return {}

        def number(value: Any, default: float = 0.0) -> float:
            if value is None or value == "":
                return default
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def text(value: Any, default: str = "") -> str:
            if value is None:
                return default
            try:
                # Hindari string 'nan' jika driver mengembalikan nilai kosong.
                if str(value).strip().lower() == "nan":
                    return default
            except Exception:
                pass
            return str(value).strip()

        first = rows[0]
        items: Dict[int, Dict[str, Any]] = {}

        for position, row in enumerate(rows, start=1):
            raw_item_no = row.get("item_no")
            try:
                item_no = int(raw_item_no) if raw_item_no is not None else position
            except (TypeError, ValueError):
                item_no = position

            items[item_no] = {
                "po_vol": number(row.get("qty_po")),
                "unit_price": number(row.get("unit_price")),
                "po_total": number(row.get("po_total"), None),
                "formula_factor": (0.90 if is_estimated_category(row.get("kategori"))
                                   else 1.15 if is_provisional_category(row.get("kategori"))
                                   else 1.0),
                "is_amount_based": (is_provisional_category(row.get("kategori"))
                                    and is_amount_uom(row.get("uom"))),
                "prev_vol": number(row.get("volume_previous")),
                "current_vol": number(row.get("volume_actual")),
                "cumulative_vol": number(row.get("volume_cumulative")),
                "remaining_vol": number(row.get("volume_remaining")),
                "prev_total": number(row.get("nilai_previous")),
                "current_total": number(row.get("nilai_actual")),
                "cumulative_total": number(row.get("nilai_cumulative")),
                "remaining_total": number(row.get("nilai_remaining")),
                "description": text(row.get("deskripsi_pekerjaan")),
                "kategori": text(row.get("kategori")),
                "uom": text(row.get("uom")),
                "item_key": text(row.get("item_key")),
                "mapping_status": text(row.get("mapping_status")),
            }

        return {
            "lokasi_office": text(first.get("lokasi_office"), "Luwuk") or "Luwuk",
            "tanggal_opname": text(first.get("tanggal_opname")),
            "nomor_kontrak": text(first.get("nomor_kontrak"), nomor_kontrak),
            "nomor_pi": text(first.get("nomor_pi"), pi_no),
            "nomor_po": text(first.get("nomor_po"), nomor_po),
            "items": items,
            "logo_1": None,
            "logo_2": None,
            "ttd_1": first.get("ttd_1"),
            "ttd_2": first.get("ttd_2"),
            "ttd_3": first.get("ttd_3"),
            "_source": "sql:database_opname",
            "_row_count": len(rows),
        }
    except Exception:
        LOGGER.exception(
            "[PACKAGE] Gagal membaca database_opname untuk PI %s.",
            pi_no,
        )
        return {}


def load_opname_for_package(
    nomor_kontrak: str,
    pi_no: str,
    nomor_po: str = "",
    master_pi: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load the saved Opname for this PI/PO or build its initial PI snapshot.

    Master Paket Batch should contain the same Opname data source as the
    standalone Opname page. When the user has not saved an Opname yet, the
    standalone page still displays PI quantities and Previous from the live
    context; mirror those values here instead of silently omitting Opname.
    """
    try:
        context = get_opname_context(nomor_kontrak, pi_no, nomor_po)
    except Exception:
        LOGGER.exception(
            "[PACKAGE] Gagal membangun snapshot Opname dari PI %s / PO %s.",
            pi_no,
            nomor_po,
        )
        # If the live PI context is unavailable, retain the saved snapshot as
        # a fallback. Normally we use the live context so Previous is
        # recalculated with the same date/month rules as the Opname page.
        fallback = load_saved_opname(nomor_kontrak, pi_no, nomor_po)
        if fallback:
            fallback["_saved_snapshot_exists"] = True
        return fallback

    # The PI detail is authoritative when the package request did not include
    # a PO. get_opname_context resolves it when all PI rows point to one PO;
    # use the same PO to locate the saved snapshot and label the package.
    nomor_po = _clean(context.get("nomor_po")) or _clean(nomor_po)
    saved = load_saved_opname(nomor_kontrak, pi_no, nomor_po)

    source_items = context.get("items") or []
    if not source_items:
        if saved:
            saved["_saved_snapshot_exists"] = True
        return saved

    def number(value: Any, default: float = 0.0) -> float:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    items: Dict[int, Dict[str, Any]] = {}
    for position, source in enumerate(source_items, start=1):
        try:
            item_no = int(source.get("item_no") or position)
        except (TypeError, ValueError):
            item_no = position

        category = str(source.get("kategori") or "")
        is_provisional = bool(source.get("is_provisional"))
        is_formula_quantity = bool(source.get("is_fee_based_quantity"))
        formula_factor = number(source.get("formula_factor"),
                                 0.90 if is_estimated_category(category)
                                 else 1.15 if is_provisional_category(category) else 1.0)
        saved_item = source.get("saved") or {}
        item_key = str(source.get("item_key") or "")
        # The live context is authoritative for Previous after resolving the
        # PI-month cutoff. A stale persisted snapshot must not replace a valid
        # zero or an updated cumulative history.
        context_previous = number(source.get("qty_previous"))
        previous = context_previous
        actual = number(source.get("qty_pi"))
        if saved_item:
            actual = number(saved_item.get("volume_actual"), actual)

        unit_price = number(
            saved_item.get("unit_price") if saved_item.get("unit_price") not in (None, "") else source.get("unit_price")
        )
        context_previous_total = number(source.get("nilai_previous"))
        previous_total = (context_previous_total if is_provisional or is_formula_quantity
                          else previous * unit_price)
        actual_total = (
            number(saved_item.get("nilai_actual"), number(source.get("nilai_pi")))
            if is_provisional or is_formula_quantity
            else actual * unit_price
        )
        po_volume = number(source.get("qty_po"), 1.0 if is_provisional else actual)
        po_total = number(source.get("po_plafon"), po_volume * unit_price * formula_factor)
        cumulative = previous + actual
        cumulative_total = previous_total + actual_total
        remaining = po_volume - cumulative if not is_provisional else 0.0
        remaining_total = (
            po_total - cumulative_total
        )

        items[item_no] = {
            "po_vol": po_volume,
            "unit_price": unit_price,
            "po_total": po_total,
            "formula_factor": formula_factor,
            "is_amount_based": is_provisional,
            "is_formula_quantity": is_formula_quantity,
            "prev_vol": 0.0 if is_provisional else previous,
            "current_vol": 0.0 if is_provisional else actual,
            "cumulative_vol": 0.0 if is_provisional else cumulative,
            "remaining_vol": None if is_provisional else remaining,
            "prev_total": previous_total,
            "current_total": actual_total,
            "cumulative_total": cumulative_total,
            "remaining_total": remaining_total,
            "description": str(source.get("deskripsi_pekerjaan") or ""),
            "keterangan": str(source.get("keterangan") or ""),
            "kategori": category,
            "uom": str(source.get("uom") or ""),
            "item_key": item_key,
            "mapping_status": str(source.get("mapping_status") or ""),
        }

    master = master_pi if isinstance(master_pi, dict) else context.get("master_pi") or {}
    opname_date = (
        context.get("tanggal_selesai")
        or context.get("tanggal_pi")
        or ""
    )
    snapshot = {
        "lokasi_office": "Luwuk",
        "tanggal_opname": opname_date,
        "nomor_kontrak": nomor_kontrak,
        "nomor_pi": pi_no,
        "nomor_po": nomor_po,
        "items": items,
        "logo_1": None,
        "logo_2": None,
        "ttd_1": None,
        "ttd_2": None,
        "ttd_3": None,
        "_source": "context:database_transaksi_rincian",
        "_row_count": len(items),
        "_saved_snapshot_exists": bool(saved),
        "job_title": (
            str(context.get("document", {}).get("job_title") or "")
            or str(master.get("job_title") or master.get("judul_kontrak") or "")
        ),
    }
    if saved:
        # Keep user-entered document settings and saved actuals/signatures,
        # but preserve the freshly calculated Previous values in snapshot.
        for key in (
            "lokasi_office", "logo_1", "logo_2", "ttd_1", "ttd_2", "ttd_3",
        ):
            if saved.get(key) not in (None, ""):
                snapshot[key] = saved[key]
        if saved.get("tanggal_opname") and not snapshot.get("tanggal_opname"):
            snapshot["tanggal_opname"] = saved["tanggal_opname"]
    return snapshot

