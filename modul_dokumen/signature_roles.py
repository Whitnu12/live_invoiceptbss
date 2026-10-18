"""Resolve the PI approval columns into document signature roles."""


_EMPTY_SIGNERS = {
    "", "nan", "none", "null", "-", "kosong", "tidak ada",
    "tidak ada / kosong", "--- (tidak ada / kosong) ---",
}


def signer_text(value):
    """Return the stored PI value, omitting database empty-value markers."""
    text = str(value or "").strip()
    return text if text.casefold() not in _EMPTY_SIGNERS else ""


def resolve_approval_roles(app1_name, app1_title, app2_name, app2_title):
    """Approved 1 signs alone, or reviews when Approved 2 is present."""
    first_name = signer_text(app1_name)
    second_name = signer_text(app2_name)
    has_second = bool(second_name)
    reviewed_name = first_name if has_second else ""
    return {
        "reviewed_name": reviewed_name,
        "reviewed_title": signer_text(app1_title) if reviewed_name else "",
        "approved_name": second_name if has_second else first_name,
        "approved_title": signer_text(app2_title) if has_second else signer_text(app1_title),
    }
