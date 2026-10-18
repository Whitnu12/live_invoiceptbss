"""Rekonsiliasi rincian PI ke item Master PO dari halaman Opname."""

import hashlib
from decimal import Decimal, InvalidOperation

from db_connection import get_db_connection
from modul_po.master_po import (
    bootstrap_master_po,
    calculate_po_plafon,
    is_amount_uom,
    is_provisional_category,
    po_reference_fingerprint,
    refresh_po_total,
    sync_pi_realization,
)
from .services import _find_master_po, _master_items, _pi_items, clean, dec, row_dict


def _amount(value, name):
    if value is None or clean(value) == "":
        raise ValueError(f"{name} wajib diisi berdasarkan PO yang sebenarnya.")
    try:
        amount = Decimal(clean(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} harus berupa angka yang valid.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} tidak boleh negatif.")
    return amount


def _selection(cur, payload):
    contract = clean(payload.get("nomor_kontrak"))
    pi_no = clean(payload.get("nomor_pi"))
    po = clean(payload.get("nomor_po"))
    item_key = clean(payload.get("item_key"))
    if not contract or not pi_no or not item_key:
        raise ValueError("Kontrak, PI, dan rincian PI wajib dipilih.")
    if not po or len(po) > 100 or po.upper() in {"-", "0", "NONE", "NULL"}:
        raise ValueError("Nomor PO tidak valid.")
    item = next((x for x in _pi_items(cur, contract, pi_no, po)
                 if x.get("item_key") == item_key), None)
    if not item:
        raise ValueError("Rincian PI tidak ditemukan. Muat ulang halaman.")
    source_po = clean(item.get("nomor_po"))
    if source_po and source_po not in {"-", "0"} and source_po != po:
        raise ValueError(f"Rincian PI sudah mencatat PO {source_po}. Pilih PO tersebut sebelum mapping.")
    if not clean(item.get("deskripsi_pekerjaan")):
        raise ValueError("Rincian PI tidak memiliki deskripsi pekerjaan.")
    return contract, pi_no, po, item


def _group_rows(cur, contract, pi_no, po, item):
    """Kelompokkan penggunaan referensi dalam PO yang sama, tanpa mengganti PO lain."""
    fingerprint = po_reference_fingerprint(item.get("kategori"), item.get("deskripsi_pekerjaan"), item.get("uom"), item.get("keterangan"))
    cur.execute("""
        SELECT pi_no, nomor_po, kategori, deskripsi_pekerjaan, keterangan, unit, harga_satuan, po_item_id
        FROM database_transaksi_rincian
        WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s
          AND TRIM(COALESCE(pi_no,''))<>''
          AND (TRIM(CAST(nomor_po AS CHAR))=%s OR TRIM(CAST(pi_no AS CHAR))=%s)
    """, (contract, po, pi_no))
    candidates = [row_dict(cur, raw) for raw in cur.fetchall()]
    rows = [r for r in candidates
            if po_reference_fingerprint(r.get("kategori"), r.get("deskripsi_pekerjaan"), r.get("unit"), r.get("keterangan")) == fingerprint
            and (clean(r.get("nomor_po")) == po or
                 (clean(r.get("pi_no")) == pi_no and clean(r.get("nomor_po")) in {"", "-", "0"}))]
    if not any(clean(r.get("pi_no")) == pi_no for r in rows):
        raise ValueError("Rincian PI tidak dapat ditautkan secara aman ke transaksi sumber.")
    return fingerprint, rows


def po_mapping_options(payload):
    bootstrap_master_po()
    with get_db_connection() as conn:
        cur = conn.cursor()
        try:
            contract, pi_no, po, item = _selection(cur, payload)
            fingerprint, rows = _group_rows(cur, contract, pi_no, po, item)
            master = _find_master_po(cur, contract, po)
            items = _master_items(cur, master["id"]) if master else []
            suggested = [x["id"] for x in items
                         if po_reference_fingerprint(x.get("kategori"), x.get("deskripsi_pekerjaan"), x.get("uom"), x.get("keterangan")) == fingerprint]
            return {
                "success": True, "master_po": master, "items": items,
                "is_provisional": (is_provisional_category(item.get("kategori"))
                                   and is_amount_uom(item.get("uom"))),
                "is_provisional_category": is_provisional_category(item.get("kategori")),
                "pi_item": item, "suggested_item_ids": suggested,
                "affected_pi_count": len({clean(r.get("pi_no")) for r in rows}),
                "affected_pi_numbers": sorted({clean(r.get("pi_no")) for r in rows}),
                "affected_row_count": len(rows),
                "current_pi_row_count": sum(clean(r.get("pi_no")) == pi_no for r in rows),
                "price_variants": sorted({str(dec(r.get("harga_satuan"))) for r in rows}),
                "already_mapped_count": sum(bool(r.get("po_item_id")) for r in rows),
            }
        finally:
            cur.close()


def reconcile_po_item(payload):
    """Tautkan PI ke item PO; Provisional Sum memakai satu bucket nilai per PO."""
    action = clean(payload.get("action"))
    if action not in {"link", "create", "adjust_qty", "adjust_budget"}:
        raise ValueError("Pilih tindakan mapping PO yang valid.")
    scope = clean(payload.get("scope")) or "current"
    if scope not in {"current", "all"}:
        raise ValueError("Cakupan mapping tidak valid.")
    bootstrap_master_po()

    contract = clean(payload.get("nomor_kontrak"))
    po = clean(payload.get("nomor_po"))
    lock_name = "opname-po:" + hashlib.sha1(f"{contract}\0{po}".encode("utf-8")).hexdigest()
    with get_db_connection() as conn:
        cur = conn.cursor()
        locked = False
        try:
            cur.execute("SELECT GET_LOCK(%s, 10) AS acquired", (lock_name,))
            locked = int(row_dict(cur, cur.fetchone()).get("acquired") or 0) == 1
            if not locked:
                raise ValueError("PO sedang diperbarui pengguna lain. Silakan coba lagi.")

            contract, pi_no, po, item = _selection(cur, payload)
            provisional_category = is_provisional_category(item.get("kategori"))
            provisional = provisional_category and is_amount_uom(item.get("uom"))
            if (provisional and action == "adjust_qty") or (not provisional and action == "adjust_budget"):
                raise ValueError("Tindakan revisi tidak sesuai dengan jenis item PO.")
            qty = (Decimal("1") if provisional else _amount(payload.get("qty_po"), "Qty PO")) if action in {"create", "adjust_qty"} else None
            price = _amount(payload.get("unit_price"), "Nilai At Cost Provisional Sum" if provisional else "Harga satuan PO") if action in {"create", "adjust_budget"} else None
            if provisional and price is not None and price <= 0:
                raise ValueError("Nilai at cost Provisional Sum wajib lebih dari nol sesuai dokumen PO.")
            fingerprint, rows = _group_rows(cur, contract, pi_no, po, item)
            if scope == "current":
                source_price = dec(item.get("unit_price"))
                current_rows = []
                for row in rows:
                    if clean(row.get("pi_no")) != pi_no:
                        continue
                    if not provisional and dec(row.get("harga_satuan")) != source_price:
                        continue
                    current_rows.append(row)
                rows = current_rows
                if not rows:
                    raise ValueError("Rincian PI pada harga ini tidak ditemukan. Muat ulang halaman.")
            master = _find_master_po(cur, contract, po)
            master_items = _master_items(cur, master["id"]) if master else []
            if action == "create":
                if any(po_reference_fingerprint(x.get("kategori"), x.get("deskripsi_pekerjaan"), x.get("uom"), x.get("keterangan")) == fingerprint
                       for x in master_items):
                    raise ValueError("Item serupa sudah ada di PO. Pilih 'Tautkan ke item PO' agar tidak duplikat.")
                if master is None:
                    cur.execute("""
                        SELECT COALESCE(MAX(version_no),0)+1 AS next_version
                        FROM database_master_po WHERE nomor_kontrak=%s AND nomor_po=%s
                    """, (contract, po))
                    version = int(row_dict(cur, cur.fetchone()).get("next_version") or 1)
                    cur.execute("""
                        INSERT INTO database_master_po
                            (nomor_kontrak,nomor_po,status_po,version_no,deleted_at,keterangan)
                        VALUES (%s,%s,'DRAFT',%s,NULL,%s)
                    """, (contract, po, version, f"Draft dari rincian PI {pi_no}; Qty/harga PO perlu diverifikasi."))
                    po_id = cur.lastrowid
                else:
                    po_id = master["id"]
                cur.execute("SELECT COALESCE(MAX(item_no),0)+1 AS next_item_no FROM database_master_po_item WHERE po_id=%s", (po_id,))
                item_no = int(row_dict(cur, cur.fetchone()).get("next_item_no") or 1)
                cur.execute("""
                    INSERT INTO database_master_po_item
                        (po_id,item_no,kategori,deskripsi_pekerjaan,uom,qty_po,unit_price,total_plafon,keterangan,status_item)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                """, (po_id, item_no, "PROVISIONAL SUM" if provisional_category else clean(item.get("kategori")),
                      ("Provisional Sum (At Cost + Fee 15%)" if provisional else clean(item.get("deskripsi_pekerjaan"))),
                      ("AU" if provisional else clean(item.get("uom"))), qty, price,
                      calculate_po_plafon(qty, price, "PROVISIONAL SUM" if provisional_category else item.get("kategori"),
                                          "AU" if provisional else item.get("uom")),
                      clean(item.get("keterangan"))))
                item_id = cur.lastrowid
                refresh_po_total(cur, po_id)
            else:
                if not master:
                    raise ValueError("Master PO belum ada. Pilih 'Buat item PO baru' untuk membuat draft PO.")
                po_id = master["id"]
                try:
                    item_id = int(payload.get("po_item_id"))
                except (ValueError, TypeError) as exc:
                    raise ValueError("Pilih item PO yang valid.") from exc
                target = next((x for x in master_items if int(x["id"]) == item_id), None)
                if not target:
                    raise ValueError("Item PO tidak aktif atau bukan milik nomor PO yang dipilih.")
                if is_provisional_category(target.get("kategori")) != provisional_category:
                    raise ValueError("Rincian Provisional Sum hanya boleh ditautkan ke pos Provisional Sum pada PO yang sama.")
                if provisional and action == "link" and dec(target.get("total_plafon")) <= 0:
                    raise ValueError("Isi plafon Provisional Sum pada Draft Master PO terlebih dahulu sebelum mapping.")
                if action == "adjust_qty":
                    cur.execute("SELECT COALESCE(SUM(qty_realisasi),0) AS used FROM database_po_realisasi WHERE po_item_id=%s", (item_id,))
                    used = dec(row_dict(cur, cur.fetchone()).get("used"))
                    if qty < used:
                        raise ValueError(f"Qty PO tidak boleh kurang dari realisasi yang sudah tercatat ({used}).")
                    update_qty_sql = (
                        "UPDATE database_master_po_item "
                        "SET qty_po=%s, total_plafon=%s "
                        "WHERE id=%s AND po_id=%s"
                    )
                    cur.execute(update_qty_sql, (
                        qty,
                        calculate_po_plafon(qty, dec(target.get("unit_price")), target.get("kategori"), target.get("uom")),
                        item_id,
                        po_id,
                    ))
                    refresh_po_total(cur, po_id)
                elif action == "adjust_budget":
                    cur.execute("SELECT COALESCE(SUM(nilai_realisasi),0) AS used FROM database_po_realisasi WHERE po_item_id=%s", (item_id,))
                    used = dec(row_dict(cur, cur.fetchone()).get("used"))
                    calculated_budget = calculate_po_plafon(Decimal("1"), price, target.get("kategori"), target.get("uom"))
                    if calculated_budget < used:
                        raise ValueError(f"Plafon Provisional Sum setelah fee 15% tidak boleh kurang dari realisasi tercatat ({used}).")
                    update_budget_sql = (
                        "UPDATE database_master_po_item "
                        "SET qty_po=1, unit_price=%s, total_plafon=%s "
                        "WHERE id=%s AND po_id=%s"
                    )
                    cur.execute(update_budget_sql, (
                        price,
                        calculated_budget,
                        item_id,
                        po_id,
                    ))
                    refresh_po_total(cur, po_id)

            source_link = item.get("pi_po_item_id")
            if source_link and str(source_link) != str(item_id):
                raise ValueError("Rincian PI ini sudah tertaut ke item PO lain. Periksa mapping lama sebelum mengubahnya.")

            if scope == "all":
                cur.execute("""
                    SELECT po_item_id FROM database_po_mapping_rule
                    WHERE nomor_kontrak=%s AND nomor_po=%s AND fingerprint=%s FOR UPDATE
                """, (contract, po, fingerprint))
                existing = cur.fetchone()
                if existing and int(row_dict(cur, existing)["po_item_id"]) != item_id:
                    raise ValueError("Kelompok rincian ini sudah punya aturan mapping ke item PO lain. Periksa terlebih dahulu.")
                if not existing:
                    cur.execute("""
                        INSERT INTO database_po_mapping_rule
                            (nomor_kontrak,nomor_po,fingerprint,kategori,deskripsi_pekerjaan,uom,po_item_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (contract, po, fingerprint, clean(item.get("kategori")),
                          clean(item.get("deskripsi_pekerjaan")), clean(item.get("uom")), item_id))

            changed = 0
            affected_pi = set()
            skipped_conflicts = 0
            seen = set()
            for row in rows:
                prior_id = row.get("po_item_id")
                if prior_id and str(prior_id) != str(item_id):
                    skipped_conflicts += 1
                    continue
                if prior_id:
                    continue
                raw_po = row.get("nomor_po")
                identity = (row.get("pi_no"), raw_po, row.get("kategori"),
                            row.get("deskripsi_pekerjaan"), row.get("unit"), row.get("harga_satuan"))
                if identity in seen:
                    continue
                seen.add(identity)
                cur.execute("""
                    UPDATE database_transaksi_rincian
                    SET nomor_po=%s, po_item_id=%s
                    WHERE TRIM(CAST(nomor_kontrak AS CHAR))=%s
                      AND TRIM(CAST(pi_no AS CHAR))=%s
                      AND nomor_po <=> %s AND kategori <=> %s
                      AND deskripsi_pekerjaan <=> %s AND unit <=> %s
                      AND harga_satuan <=> %s
                      AND po_item_id IS NULL
                """, (po, item_id, contract, clean(row.get("pi_no")), raw_po,
                      row.get("kategori"), row.get("deskripsi_pekerjaan"), row.get("unit"),
                      row.get("harga_satuan")))
                if cur.rowcount:
                    changed += cur.rowcount
                    affected_pi.add(clean(row.get("pi_no")))
            if not changed and not any(clean(r.get("pi_no")) == pi_no and str(r.get("po_item_id")) == str(item_id) for r in rows):
                raise ValueError("Tidak ada rincian PI yang berhasil ditautkan; perubahan PO dibatalkan.")
            if not affected_pi:
                affected_pi.add(pi_no)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if locked:
                # mysql.connector uses an unbuffered cursor here. Even the
                # RELEASE_LOCK result must be consumed before cursor.close(),
                # otherwise a successful commit is reported as "Unread result found".
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cur.fetchone()
            cur.close()

    warnings = []
    for affected in sorted(affected_pi):
        try:
            sync_pi_realization(contract, affected, remap_other=False)
        except Exception as exc:
            warnings.append(f"Ledger PI {affected} perlu disinkronkan ulang: {exc}")
    return {"success": True, "po_id": po_id, "item_id": item_id,
            "mapped_rows": changed, "affected_pi_count": len(affected_pi),
            "skipped_conflicts": skipped_conflicts,
            "warning": " ".join(warnings),
            "message": (f"Item PO #{item_id} siap. {changed} rincian dari {len(affected_pi)} PI ditautkan ke PO {po}."
                        + (f" {skipped_conflicts} rincian yang sudah tertaut ke item lain dilewati." if skipped_conflicts else ""))}
