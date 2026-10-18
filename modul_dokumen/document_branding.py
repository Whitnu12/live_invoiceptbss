from __future__ import annotations

import base64
import html
from typing import Any


def image_source(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return value
        return f"data:image/png;base64,{value}"
    try:
        return "data:image/png;base64," + base64.b64encode(value).decode("ascii")
    except Exception:
        return ""


def standard_brand_header(params: dict | None = None, *, title: str = "", subtitle: str = "") -> str:
    """Legacy-compatible 25/50/25 logo header used by Master Paket Batch."""
    params = params or {}
    logo_1 = image_source(params.get("logo_1"))
    logo_2 = image_source(params.get("logo_2"))
    left = (
        f'<img src="{html.escape(logo_1, quote=True)}" '
        'style="height:52px;max-width:180px;object-fit:contain;display:block;" alt="Logo BSS">'
        if logo_1 else '<div style="height:52px;"></div>'
    )
    right = (
        f'<img src="{html.escape(logo_2, quote=True)}" '
        'style="height:50px;max-width:210px;object-fit:contain;display:block;margin-left:auto;" alt="Logo ISO">'
        if logo_2 else '<div style="height:50px;"></div>'
    )
    title_html = html.escape(title or "PT. BANGGAI SENTRAL SULAWESI")
    subtitle_html = html.escape(subtitle or "General Contractor and Suppliers")
    return f"""
<table class="legacy-brand-header">
  <tr>
    <td class="legacy-brand-left">{left}</td>
    <td class="legacy-brand-center">
      <div class="legacy-brand-title">{title_html}</div>
      <div class="legacy-brand-subtitle">{subtitle_html}</div>
      <div class="legacy-brand-address">Jl. Urip Sumoharjo No. 53 Luwuk, Kabupaten Banggai, Propinsi Sulawesi Tengah</div>
    </td>
    <td class="legacy-brand-right">{right}</td>
  </tr>
</table>
<div class="legacy-brand-rule"></div>
"""


BRANDING_CSS = """
.legacy-brand-header{width:100%;margin:5px 0 12px;border-collapse:collapse;table-layout:fixed}
.legacy-brand-header td{border:none;vertical-align:middle;padding:0}
.legacy-brand-left{width:25%;text-align:left;padding-left:25px!important}
.legacy-brand-center{width:50%;text-align:center;line-height:1.4}
.legacy-brand-right{width:25%;text-align:right;padding-right:25px!important}
.legacy-brand-title{font-weight:bold;font-size:14px;letter-spacing:.5px;margin-bottom:2px}
.legacy-brand-subtitle{font-size:11px;font-weight:bold;color:#1f2937;margin-bottom:2px}
.legacy-brand-address{font-size:9.5px;color:#4b5563}
.legacy-brand-rule{border-bottom:2px solid #000;margin-bottom:15px}
"""
