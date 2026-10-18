"""
Central Document Engine untuk Modul 2.

Engine ini menjadi registry tunggal untuk seluruh dokumen yang berasal dari
PI/transaksi yang sama. Generator dokumen dimuat secara lazy sehingga satu
dokumen yang bermasalah tidak membuat aplikasi gagal saat startup.
"""

from __future__ import annotations

import html
import importlib
import logging
from typing import Any, Dict, Iterable, List, Mapping

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry dokumen
# ---------------------------------------------------------------------------
# mode=generator : diproses oleh render_document()
# mode=route      : memiliki blueprint/halaman sendiri
# mode=package    : kumpulan generator yang dapat digabungkan
DOCUMENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "rincian-pekerjaan": {
        "name": "Rincian Pekerjaan",
        "description": "Rincian pekerjaan dan mutasi transaksi.",
        "mode": "generator",
        "module": "modul_dokumen.rincian_pekerjaan",
        "function": "generate_rincian_pekerjaan_html",
    },
    "proforma-invoice": {
        "name": "Proforma Invoice",
        "description": "Dokumen Proforma Invoice.",
        "mode": "generator",
        "module": "modul_dokumen.proforma_invoice",
        "function": "generate_proforma_invoice_html",
    },
    "bamp": {
        "name": "BAMP",
        "description": "Berita Acara Mulai Pekerjaan.",
        "mode": "generator",
        "module": "modul_dokumen.bamp",
        "function": "generate_bamp_html",
    },
    "basp": {
        "name": "BASP",
        "description": "Berita Acara Selesai Pekerjaan.",
        "mode": "generator",
        "module": "modul_dokumen.basp",
        "function": "generate_basp_html",
    },
    "wcc": {
        "name": "WCC",
        "description": "Work Completion Certificate.",
        "mode": "generator",
        "module": "modul_dokumen.wcc",
        "function": "generate_wcc_html",
    },
    "bastb": {
        "name": "BASTB",
        "description": "Berita Acara Serah Terima Barang.",
        "mode": "generator",
        "module": "modul_dokumen.bastb",
        "function": "generate_bastb_html",
    },
    "tkdn": {
        "name": "TKDN",
        "description": "Formulir Tingkat Komponen Dalam Negeri.",
        "mode": "route",
        "route": "/modul/invoice-dokumen/tkdn",
    },
    "opname": {
        "name": "Opname",
        "description": "Dokumen opname pekerjaan dari Master PO dan ledger realisasi.",
        "mode": "route",
        "route": "/opname/",
    },
    "paket-dokumen": {
        "name": "Master Paket Batch",
        "description": "Paket seluruh dokumen yang tersedia untuk PI terpilih.",
        "mode": "package",
    },
}


PACKAGE_ORDER = [
    "proforma-invoice",
    "rincian-pekerjaan",
    "bamp",
    "basp",
    "bastb",
    "wcc",
    "tkdn",
    "opname",
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def get_document_config(code: str) -> Dict[str, Any]:
    code = _clean(code).lower()
    config = DOCUMENT_REGISTRY.get(code)
    if not config:
        raise ValueError(f"Kode dokumen tidak dikenal: {code}")
    return config


def _load_generator(code: str):
    config = get_document_config(code)
    if config.get("mode") != "generator":
        raise ValueError(
            f"Dokumen '{code}' bukan generator langsung; mode={config.get('mode')}"
        )

    module_name = config["module"]
    function_name = config["function"]
    module = importlib.import_module(module_name)
    generator = getattr(module, function_name, None)

    if generator is None or not callable(generator):
        raise AttributeError(
            f"Function '{function_name}' tidak ditemukan atau tidak callable "
            f"pada module '{module_name}'."
        )

    return generator


def is_generator_available(code: str) -> bool:
    """Cek availability generator tanpa membuat import saat startup."""
    try:
        _load_generator(code)
        return True
    except Exception as exc:
        LOGGER.debug("Generator dokumen %s belum tersedia: %s", code, exc)
        return False


def get_document_list(
    jenis_bastp: str = "",
    *,
    include_unavailable: bool = True,
) -> List[Dict[str, Any]]:
    """Mengembalikan manifest dokumen untuk satu PI."""
    jenis = _clean(jenis_bastp).lower()

    if "jasa" in jenis and "barang" not in jenis:
        allowed = {"rincian-pekerjaan", "proforma-invoice", "bamp", "basp", "wcc", "tkdn", "opname", "paket-dokumen"}
    elif "barang" in jenis or "material" in jenis or "provisional" in jenis:
        allowed = {"rincian-pekerjaan", "proforma-invoice", "bastb", "tkdn", "opname", "paket-dokumen"}
    else:
        allowed = set(DOCUMENT_REGISTRY)

    result: List[Dict[str, Any]] = []
    for code, config in DOCUMENT_REGISTRY.items():
        if code not in allowed:
            continue

        mode = config.get("mode", "generator")
        if mode == "generator":
            available = is_generator_available(code)
        else:
            available = True

        item = {
            "code": code,
            "name": config["name"],
            "description": config["description"],
            "available": available,
            "mode": mode,
        }
        if config.get("route"):
            item["route"] = config["route"]

        if include_unavailable or available:
            result.append(item)

    return result


def build_document_manifest(jenis_bastp: str = "") -> Dict[str, Any]:
    """Manifest lengkap yang dipakai API Modul 2."""
    jenis = _clean(jenis_bastp)
    normalized = jenis.lower()
    if "jasa" in normalized and "barang" not in normalized:
        ada_item_jasa, ada_item_barang = True, False
    elif "barang" in normalized or "material" in normalized or "provisional" in normalized:
        ada_item_jasa, ada_item_barang = False, True
    else:
        ada_item_jasa, ada_item_barang = True, True

    return {
        "jenis_bastp": jenis_bastp,
        "ada_item_jasa": ada_item_jasa,
        "ada_item_barang": ada_item_barang,
        "documents": get_document_list(jenis_bastp),
    }


def render_document(
    code: str,
    transaksi_list: Iterable[Mapping[str, Any]],
    nomor_kontrak: str = "",
    pi_no: str = "",
    params: Mapping[str, Any] | None = None,
):
    """Render satu dokumen generator tanpa mengubah transaksi sumber."""
    config = get_document_config(code)
    if config.get("mode") != "generator":
        raise ValueError(
            f"Dokumen '{code}' tidak dirender melalui generator langsung. "
            f"Gunakan handler mode '{config.get('mode')}'."
        )

    generator = _load_generator(code)
    return generator(
        transaksi_list=list(transaksi_list or []),
        nomor_kontrak=nomor_kontrak,
        pi_no=pi_no,
        params=dict(params or {}),
    )


def _render_package_route_document(
    code: str,
    *,
    nomor_kontrak: str,
    pi_no: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    """Render dokumen yang memiliki mode ``route`` untuk kebutuhan package.

    Route TKDN/Opname tetap boleh mempunyai halaman sendiri. Agar package tidak
    perlu mengetahui detail database masing-masing modul, route/service cukup
    menyuplai HTML final melalui ``params['package_documents']``.

    Bentuk yang didukung::

        params = {
            "package_documents": {
                "tkdn": "<div>...</div>",
                "opname": "<div>...</div>",
            }
        }

    Nilai juga boleh berupa mapping dengan salah satu key ``html``,
    ``rendered_html``, ``html_content``, atau ``document_html``.
    """
    payload = dict(params or {})
    package_documents = payload.get("package_documents") or {}

    value = package_documents.get(code)
    if value is None:
        # Backward-compatible shortcut untuk caller yang hanya memiliki HTML
        # masing-masing dokumen.
        value = payload.get(f"{code}_html")

    if value is None:
        raise ValueError(
            f"HTML tersimpan untuk dokumen route '{code}' belum disuplai. "
            f"Isi params['package_documents']['{code}'] dari service/route "
            "dokumen tersebut sebelum mencetak package."
        )

    if isinstance(value, Mapping):
        for key in ("html", "rendered_html", "html_content", "document_html"):
            if value.get(key):
                return str(value[key])
        raise ValueError(
            f"Payload package dokumen '{code}' tidak memiliki field HTML yang dikenal."
        )

    rendered = str(value)
    if not rendered.strip():
        raise ValueError(f"HTML package dokumen '{code}' kosong.")
    return rendered


def _package_codes_default() -> List[str]:
    """Urutan default package; tidak bergantung pada urutan registry."""
    return list(PACKAGE_ORDER)


def render_document_package(
    codes: Iterable[str] | None = None,
    transaksi_list: Iterable[Mapping[str, Any]] | None = None,
    nomor_kontrak: str = "",
    pi_no: str = "",
    params: Mapping[str, Any] | None = None,
    *,
    render_generator=None,
) -> str:
    """Gabungkan seluruh dokumen PI menjadi satu HTML siap-print.

    ``generator`` dirender langsung melalui ``render_document()``.
    ``route`` (TKDN/Opname) dirender dari HTML yang sudah disiapkan oleh
    service/route terkait melalui ``params['package_documents']``.

    Dengan pola ini, engine tetap menjadi pusat package tanpa mengambil alih
    logika penyimpanan masing-masing modul.
    """
    requested = [
        _clean(code).lower()
        for code in (codes or [])
        if _clean(code)
    ]
    if not requested:
        requested = _package_codes_default()

    # Hilangkan duplikasi tanpa mengubah urutan.
    requested = list(dict.fromkeys(requested))

    parts: List[str] = []
    errors: List[str] = []
    source = list(transaksi_list or [])

    for code in requested:
        try:
            config = get_document_config(code)
            mode = config.get("mode", "generator")

            if mode == "generator":
                # Kompatibel dengan route lama yang menyuntikkan
                # ``render_generator`` dari luar engine. Jika tidak diberikan,
                # gunakan generator internal yang lazy-loaded.
                if callable(render_generator):
                    try:
                        rendered = render_generator(
                            code=code,
                            transaksi_list=source,
                            nomor_kontrak=nomor_kontrak,
                            pi_no=pi_no,
                            params=params,
                        )
                    except TypeError:
                        # Kompatibilitas dengan helper lama yang hanya menerima
                        # (code, transaksi_list, nomor_kontrak, pi_no, params).
                        rendered = render_generator(
                            code, source, nomor_kontrak, pi_no, params
                        )
                else:
                    rendered = render_document(
                        code=code,
                        transaksi_list=source,
                        nomor_kontrak=nomor_kontrak,
                        pi_no=pi_no,
                        params=params,
                    )
            elif mode == "route":
                rendered = _render_package_route_document(
                    code,
                    nomor_kontrak=nomor_kontrak,
                    pi_no=pi_no,
                    params=params,
                )
            elif mode == "package":
                # ``paket-dokumen`` adalah entry UI/registry, bukan isi package.
                continue
            else:
                raise ValueError(f"Mode dokumen tidak didukung: {mode}")

            if rendered is None or not str(rendered).strip():
                raise ValueError("Generator menghasilkan HTML kosong.")

            title = html.escape(config["name"])
            parts.append(
                f'<section class="document-package-section" '
                f'data-code="{html.escape(code)}">'
                f'<div class="document-package-title">{title}</div>'
                f'{rendered}</section>'
            )
        except Exception as exc:
            LOGGER.exception("Gagal merender dokumen batch %s", code)
            config = DOCUMENT_REGISTRY.get(code, {"name": code})
            errors.append(f"{config['name']}: {exc}")

    if not parts:
        raise RuntimeError(
            "Tidak ada dokumen yang berhasil dirender ke package. "
            + (" | ".join(errors) if errors else "")
        )

    error_html = ""
    if errors:
        error_html = (
            '<aside class="document-package-errors">'
            '<strong>Dokumen yang tidak masuk package:</strong><ul>'
            + "".join(f"<li>{html.escape(item)}</li>" for item in errors)
            + "</ul></aside>"
        )

    return """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Master Paket Dokumen</title>
<style>
@page{size:A4;margin:0}
html,body{margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f5f6f8;color:#202124}
.document-package-section{
  background:#fff;
  margin:24px auto;
  padding:24px;
  max-width:1200px;
  box-sizing:border-box;
  page-break-after:always;
  break-after:page;
}
.document-package-section:last-of-type{
  page-break-after:auto;
  break-after:auto;
}
.document-package-title{
  font-size:18px;
  font-weight:700;
  margin:0 0 16px;
  padding-bottom:8px;
  border-bottom:1px solid #ddd;
}
.document-package-errors{
  max-width:1200px;
  margin:24px auto;
  padding:16px;
  background:#fff3cd;
  color:#664d03;
  box-sizing:border-box;
}
.document-package-errors ul{margin:8px 0 0 20px;padding:0}
@media print{
  body{background:#fff}
  .document-package-section{
    margin:0;
    max-width:none;
    box-shadow:none;
    padding:0;
  }
  .document-package-title{
    display:none;
  }
  .document-package-errors{
    display:none;
  }
}
</style>
</head>
<body>
""" + error_html + "\n".join(parts) + """
</body>
</html>"""


# Backward-compatible alias.
DOCUMENT_NAMES = {
    code: config["name"]
    for code, config in DOCUMENT_REGISTRY.items()
}

DOCUMENT_GENERATORS = {
    code: (config["module"], config["function"])
    for code, config in DOCUMENT_REGISTRY.items()
    if config.get("mode") == "generator"
}
