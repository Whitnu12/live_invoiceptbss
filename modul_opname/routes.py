"""
Routes untuk modul Opname standalone.

Struktur package:
    modul_opname/
    ├── __init__.py
    ├── routes.py
    ├── services.py
    └── templates/
        └── opname.html

Blueprint ini sengaja memiliki template_folder sendiri agar modul Opname
sepenuhnya berdiri sendiri dan tidak bergantung pada templates/ milik app utama.
"""

import os

from flask import Blueprint, jsonify, render_template, request

from modul_keamanan.decorators import login_required
from modul_input.modul_1_database import can_write
from .services import (
    ensure_opname_schema,
    list_pi,
    list_po,
    get_opname_context,
    save_opname,
)
from .po_mapping import po_mapping_options, reconcile_po_item


# ============================================================
# STANDALONE BLUEPRINT
# ============================================================

OPNAME_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "templates",
)

if not os.path.isdir(OPNAME_TEMPLATE_DIR):
    raise RuntimeError(
        f"Folder template Opname tidak ditemukan: {OPNAME_TEMPLATE_DIR}"
    )

if not os.path.isfile(os.path.join(OPNAME_TEMPLATE_DIR, "opname.html")):
    raise RuntimeError(
        f"Template Opname tidak ditemukan: "
        f"{os.path.join(OPNAME_TEMPLATE_DIR, 'opname.html')}"
    )

opname_bp = Blueprint(
    "opname",
    __name__,
    url_prefix="/opname",
    template_folder=OPNAME_TEMPLATE_DIR,
)


# ============================================================
# PAGE
# ============================================================

@opname_bp.get("/")
@login_required
def index():
    """
    Halaman utama modul Opname.

    Template diambil dari:
        modul_opname/templates/opname.html

    Bukan dari:
        templates/opname.html
    """
    ensure_opname_schema()

    return render_template(
        "opname.html",
        title="Opname",
        module_code="OPNAME",
        can_write_po=can_write(),
    )


# ============================================================
# API PI
# ============================================================

@opname_bp.get("/api/pi")
@login_required
def api_pi():
    try:
        return jsonify(
            {
                "success": True,
                "data": list_pi(
                    request.args.get("nomor_kontrak", "")
                ),
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
                "data": [],
            }
        ), 500


# ============================================================
# API PO
# ============================================================

@opname_bp.get("/api/po")
@login_required
def api_po():
    contract = request.args.get("nomor_kontrak", "")
    pi_no = request.args.get("nomor_pi", "")

    try:
        return jsonify(
            {
                "success": True,
                "data": list_po(contract, pi_no),
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
                "data": [],
            }
        ), 500


# ============================================================
# API CONTEXT
# ============================================================

@opname_bp.get("/api/context")
@login_required
def api_context():
    contract = request.args.get("nomor_kontrak", "")
    pi_no = request.args.get("nomor_pi", "")
    po = request.args.get("nomor_po", "")

    try:
        return jsonify(
            get_opname_context(
                contract,
                pi_no,
                po,
                request.args.get("tanggal_opname", ""),
            )
        )
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@opname_bp.post("/api/po/add-pi-item")
@login_required
def api_add_pi_item_to_po():
    return jsonify({"success": False, "message": "Gunakan alur rekonsiliasi PO terbaru dan konfirmasi Qty/Harga PO."}), 410


@opname_bp.get("/api/po/mapping-options")
@login_required
def api_po_mapping_options():
    try:
        return jsonify(po_mapping_options(request.args))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@opname_bp.post("/api/po/reconcile")
@login_required
def api_po_reconcile():
    if not can_write():
        return jsonify({"success": False, "message": "Role Anda hanya memiliki akses baca."}), 403
    try:
        return jsonify(reconcile_po_item(request.get_json(silent=True) or {}))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


# ============================================================
# API SAVE
# ============================================================

@opname_bp.post("/api/save")
@login_required
def api_save():
    payload = request.get_json(silent=True) or {}

    try:
        return jsonify(save_opname(payload))
    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400
    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500
