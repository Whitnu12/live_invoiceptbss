"""Persistent configuration for the Flask Master Paket Batch.

The legacy Streamlit package used persistent logo assets.  The Flask package
keeps the same visual inputs, but stores their selected values per Contract +
PI in a small JSON sidecar so no database schema change is required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "database_penyimpanan_aman")
CONFIG_PATH = os.path.join(DATA_DIR, "master_paket_batch_config.json")
LEGACY_LOGO_DIR = os.path.join(DATA_DIR, "pengaturan_logo_permanen")
# Recognize the previous default without overriding a custom or cleared ISO logo.
LEGACY_ISO_SHA256 = "e72ef84c5d068683cab4f32ec2d1d5feff518a94e5e30801c9722a962b33299a"
PACKAGE_DOCUMENT_CODES = (
    "proforma-invoice",
    "rincian-pekerjaan",
    "bamp",
    "basp",
    "bastb",
    "wcc",
    "tkdn",
    "opname",
)


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _asset_from_file(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        if not raw:
            return ""
        ext = os.path.splitext(path)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except OSError:
        return ""


def _legacy_txt_asset(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        return value if value.startswith("data:image/") else ""
    except OSError:
        return ""


def persistent_defaults() -> Dict[str, Any]:
    """Return the legacy permanent images as package defaults."""
    return {
        "logo_1": _legacy_txt_asset(os.path.join(LEGACY_LOGO_DIR, "logo_p1_perm.txt"))
        or _asset_from_file(os.path.join(DATA_DIR, "persistent_logo_bss.png")),
        "logo_2": _asset_from_file(os.path.join(DATA_DIR, "persistent_logo_p2.png"))
        or _legacy_txt_asset(os.path.join(LEGACY_LOGO_DIR, "logo_p2_perm.txt")),
        "logo_3": _asset_from_file(os.path.join(DATA_DIR, "persistent_logo_iso.png"))
        or _legacy_txt_asset(os.path.join(LEGACY_LOGO_DIR, "logo_iso_perm.txt")),
        "ttd_1": "",
        "ttd_2": "",
        # Penandatangan Approved by mengikuti PI; gambar direktur lama
        # tidak aman untuk dipasang otomatis pada orang yang berbeda.
        "ttd_3": "",
    }


def _read_all() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _key(nomor_kontrak: Any, pi_no: Any) -> str:
    return f"{_clean(nomor_kontrak)}::{_clean(pi_no)}"


def load_config(nomor_kontrak: Any, pi_no: Any) -> Dict[str, Any]:
    """Load saved package config, falling back to legacy permanent assets."""
    saved = _read_all().get(_key(nomor_kontrak, pi_no), {})
    saved = dict(saved) if isinstance(saved, dict) else {}
    defaults = persistent_defaults()
    saved.setdefault("lokasi", "Luwuk")
    for name, value in defaults.items():
        if name not in saved:
            saved[name] = value
    saved_iso = saved.get("logo_3")
    if isinstance(saved_iso, str) and saved_iso.startswith("data:image/") and defaults.get("logo_3"):
        try:
            previous_iso = base64.b64decode(saved_iso.split(",", 1)[1], validate=True)
        except (ValueError, IndexError):
            previous_iso = b""
        if hashlib.sha256(previous_iso).hexdigest() == LEGACY_ISO_SHA256:
            saved["logo_3"] = defaults["logo_3"]
    saved.setdefault("dates", {})
    if not isinstance(saved["dates"], dict):
        saved["dates"] = {}
    saved.setdefault("bamp_items", {})
    if not isinstance(saved["bamp_items"], dict):
        saved["bamp_items"] = {}
    selected_documents = saved.get("selected_documents", PACKAGE_DOCUMENT_CODES)
    if not isinstance(selected_documents, list):
        selected_documents = list(PACKAGE_DOCUMENT_CODES)
    saved["selected_documents"] = [
        code for code in PACKAGE_DOCUMENT_CODES if code in selected_documents
    ]
    return saved


def save_config(nomor_kontrak: Any, pi_no: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist only supported Master Paket Batch fields."""
    all_configs = _read_all()
    incoming = dict(payload or {})
    config_key = _key(nomor_kontrak, pi_no)
    previous = all_configs.get(config_key, {})
    if not isinstance(previous, dict):
        previous = {}
    clean_config = {
        "lokasi": _clean(incoming.get("lokasi") or "Luwuk"),
        "dates": {
            code: _clean((incoming.get("dates") or {}).get(code, ""))
            for code in ("bamp", "basp", "bastb")
        },
        "selected_documents": [
            code for code in PACKAGE_DOCUMENT_CODES
            if code in (incoming.get("selected_documents")
                        if isinstance(incoming.get("selected_documents"), list)
                        else PACKAGE_DOCUMENT_CODES)
        ],
    }
    incoming_bamp_items = incoming.get("bamp_items", previous.get("bamp_items", {}))
    if isinstance(incoming_bamp_items, dict):
        clean_bamp_items = {}
        for item_no, item in incoming_bamp_items.items():
            if not isinstance(item, dict):
                continue
            raw_qty = item.get("qty", 0)
            while isinstance(raw_qty, dict) and "qty" in raw_qty:
                raw_qty = raw_qty.get("qty")
            try:
                qty = float(raw_qty) if raw_qty not in (None, "") else 0.0
            except (TypeError, ValueError):
                qty = 0.0
            clean_bamp_items[str(item_no)] = {
                "qty": qty if qty >= 0 else 0.0,
                "date": _clean(item.get("date")),
                "uom": _clean(item.get("uom") or "Day"),
                "catatan": _clean(item.get("catatan")),
            }
        clean_config["bamp_items"] = clean_bamp_items
    if not clean_config["selected_documents"]:
        raise ValueError("Pilih minimal satu dokumen untuk paket cetak.")
    # Empty values mean "use the permanent legacy default" on the next load.
    for asset_key in ("logo_1", "logo_2", "logo_3", "ttd_1", "ttd_2", "ttd_3"):
        value = incoming.get(asset_key)
        if value:
            clean_config[asset_key] = value
    os.makedirs(DATA_DIR, exist_ok=True)
    all_configs[config_key] = clean_config
    temp_path = CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(all_configs, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, CONFIG_PATH)
    return load_config(nomor_kontrak, pi_no)
