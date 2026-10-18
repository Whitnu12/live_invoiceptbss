"""
Flask application for Passenger/cPanel.

Production bootstrap for invoice.ptbsssatu.id.

Blueprint:
- Authentication
- Navigation
- Master PO
- Opname
"""

import logging
import os
import secrets
import time
from typing import Tuple

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
    redirect,
    url_for,
)

from werkzeug.middleware.proxy_fix import ProxyFix

from modul_keamanan.routes import auth_bp
from modul_navigasi.routes import navigation_bp
from modul_dokumen.archive_routes import archive_bp
from modul_dokumen.archive_service import (
    MAX_ARCHIVE_FILE_BYTES,
    bootstrap_document_archive,
)
from modul_keamanan.decorators import login_required

# ============================================================
# MASTER PO
# IMPORTANT:
# Import langsung dari modul_po.master_po
# agar tidak bergantung pada export modul_po/__init__.py
# ============================================================

from modul_po.master_po import (
    master_po_bp,
    bootstrap_master_po,
)

# ============================================================
# OPNAME
# ============================================================

from modul_opname.routes import opname_bp


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

APP_VERSION = "1.0.1"
APP_NAME = "invoice-ptbsssatu"

APP_ROOT = os.path.dirname(
    os.path.abspath(__file__)
)

LOG_LEVEL_NAME = (
    os.getenv(
        "LOG_LEVEL",
        "INFO",
    )
    .strip()
    .upper()
)

LOG_LEVEL = getattr(
    logging,
    LOG_LEVEL_NAME,
    logging.INFO,
)

logging.basicConfig(
    level=LOG_LEVEL,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s: "
        "%(message)s"
    ),
)

LOGGER = logging.getLogger(__name__)


def _load_or_create_secret_key() -> str:
    """Use a stable key so Passenger workers can validate the same session."""
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if secret_key:
        return secret_key

    key_path = os.getenv(
        "SECRET_KEY_FILE",
        os.path.join(APP_ROOT, "instance", "session_secret_key"),
    ).strip()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(key_path)), mode=0o700, exist_ok=True)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            for _ in range(20):
                with open(key_path, "r", encoding="utf-8") as key_file:
                    secret_key = key_file.read().strip()
                if secret_key:
                    return secret_key
                time.sleep(0.025)
            raise RuntimeError(f"Flask session key file is empty: {key_path}")

        secret_key = secrets.token_hex(32)
        try:
            os.write(fd, secret_key.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        LOGGER.warning("No SECRET_KEY configured; created persistent key at %s", key_path)
        return secret_key
    except OSError as exc:
        LOGGER.exception("Unable to load or create a persistent Flask session key")
        raise RuntimeError(
            "Set SECRET_KEY or make SECRET_KEY_FILE writable so Passenger workers "
            "share one stable session key."
        ) from exc


# ============================================================
# APPLICATION FACTORY
# ============================================================

def create_app() -> Flask:

    # Gunakan folder templates utama secara eksplisit.
    # Ini memastikan layout.html tetap dapat digunakan oleh
    # blueprint standalone seperti modul_opname.
    flask_app = Flask(
        __name__,
        template_folder=os.path.join(APP_ROOT, "templates"),
    )

    # --------------------------------------------------------
    # SECRET KEY
    # --------------------------------------------------------

    secret_key = _load_or_create_secret_key()

    # --------------------------------------------------------
    # FLASK CONFIGURATION
    # --------------------------------------------------------

    flask_app.config.update(

        SECRET_KEY=secret_key,

        # Allow multipart headers in addition to a 25 MB archive file.
        MAX_CONTENT_LENGTH=MAX_ARCHIVE_FILE_BYTES + (1024 * 1024),

        SESSION_COOKIE_HTTPONLY=True,

        SESSION_COOKIE_SECURE=True,

        SESSION_COOKIE_SAMESITE="Lax",
    )

    # --------------------------------------------------------
    # PROXY FIX
    # --------------------------------------------------------

    flask_app.wsgi_app = ProxyFix(
        flask_app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

    # ========================================================
    # REGISTER BLUEPRINT
    # ========================================================

    # Authentication
    flask_app.register_blueprint(
        auth_bp
    )

    # Navigation / Modul existing
    flask_app.register_blueprint(
        navigation_bp
    )

    # Central customer and supporting document archive
    flask_app.register_blueprint(
        archive_bp
    )

    # Master PO
    flask_app.register_blueprint(
        master_po_bp
    )

    # Opname
    flask_app.register_blueprint(
        opname_bp
    )

    # ========================================================
    # DATABASE / MASTER PO BOOTSTRAP
    # ========================================================

    try:

        bootstrap_master_po()

        LOGGER.info(
            "Master PO database bootstrap berhasil."
        )

    except Exception:

        LOGGER.exception(
            "Master PO database bootstrap gagal."
        )

        # Jangan membuat Flask gagal start hanya
        # karena database belum tersedia.
        #
        # Endpoint /health/database tetap dapat
        # digunakan untuk memeriksa database.

    try:

        bootstrap_document_archive()

        LOGGER.info(
            "Document archive database bootstrap berhasil."
        )

    except Exception:

        LOGGER.exception(
            "Document archive database bootstrap gagal."
        )

    # ========================================================
    # TEMPLATE CONTEXT
    # ========================================================

    register_template_context(
        flask_app
    )

    # ========================================================
    # APPLICATION ROUTES
    # ========================================================

    register_routes(
        flask_app
    )

    # ========================================================
    # ERROR HANDLERS
    # ========================================================

    register_error_handlers(
        flask_app
    )

    # ========================================================
    # RESPONSE HEADERS
    # ========================================================

    register_response_headers(
        flask_app
    )

    LOGGER.info(
        "%s v%s berhasil dibuat.",
        APP_NAME,
        APP_VERSION,
    )

    return flask_app


# ============================================================
# TEMPLATE CONTEXT
# ============================================================

def register_template_context(
    flask_app: Flask,
) -> None:

    @flask_app.context_processor
    def inject_template_context():

        user = current_user_data()

        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "user": user,
            "permissions": role_permissions(
                user["role"]
            ),
        }


# ============================================================
# DATABASE
# ============================================================

def database_status() -> Tuple[bool, str]:
    """
    Memeriksa koneksi database tanpa membuat
    aplikasi Flask gagal start.
    """

    try:

        from db_connection import (
            uji_koneksi_database
        )

        success, message = (
            uji_koneksi_database()
        )

        if success:

            return (
                True,
                "MySQL terhubung",
            )

        LOGGER.error(
            "Pemeriksaan database gagal: %s",
            message,
        )

        return (
            False,
            "MySQL belum terhubung",
        )

    except Exception:

        LOGGER.exception(
            "db_connection.py atau "
            "koneksi database gagal dimuat."
        )

        return (
            False,
            "MySQL belum terhubung",
        )


# ============================================================
# USER / SESSION
# ============================================================

def user_is_logged_in():
    """
    Memeriksa apakah user sudah login.
    """

    return bool(
        session.get("logged_in")
        and session.get("current_user")
    )


def current_user_data():
    """
    Mengambil informasi user aktif
    dari Flask session.
    """

    return {
        "username": session.get(
            "current_user",
            "",
        ),

        "role": session.get(
            "current_role",
            "Staff",
        ),

        "nama": session.get(
            "nama_lengkap",
            "",
        ),
    }


# ============================================================
# ROLE PERMISSIONS
# ============================================================

def role_permissions(role):

    """
    Menentukan permission berdasarkan role.

    Mengikuti struktur role aplikasi lama.
    """

    role = str(
        role or "Staff"
    ).strip().lower()

    is_management = role in [
        "management",
        "direksi",
    ]

    is_finance_tax = role in [
        "finance & tax",
        "finance",
        "tax",
    ]

    is_project_manager = role in [
        "project manager",
        "pm",
    ]

    is_project_support = role in [
        "project support",
    ]

    is_admin_support = role in [
        "admin support",
    ]

    is_super_admin = role in [
        "super admin",
        "admin",
    ]

    return {

        "management":
            is_management,

        "finance_tax":
            is_finance_tax,

        "project_manager":
            is_project_manager,

        "project_support":
            is_project_support,

        "admin_support":
            is_admin_support,

        "super_admin":
            is_super_admin,

        "privileged": (
            is_super_admin
            or is_finance_tax
            or is_project_manager
            or is_management
        ),
    }


# ============================================================
# ROUTES
# ============================================================

def register_routes(
    flask_app: Flask,
) -> None:

    # ========================================================
    # HOME
    # ========================================================

    @flask_app.get("/")
    @flask_app.get("/index.html")
    def index():

        if not user_is_logged_in():

            return redirect(
                url_for("auth.login")
            )

        return redirect(
            url_for("dashboard")
        )

    # ========================================================
    # BASIC HEALTH CHECK
    # ========================================================

    @flask_app.get("/health")
    def health():

        """
        Tidak menyentuh MySQL.

        Jika endpoint ini HTTP 200,
        Flask + Passenger berjalan.
        """

        return jsonify(
            {
                "application":
                    APP_NAME,

                "status":
                    "ok",

                "version":
                    APP_VERSION,
            }
        )

    # ========================================================
    # DATABASE HEALTH CHECK
    # ========================================================

    @flask_app.get(
        "/health/database"
    )
    def health_database():

        db_connected, db_message = (
            database_status()
        )

        status_code = (
            200
            if db_connected
            else 503
        )

        return (
            jsonify(
                {
                    "application":
                        APP_NAME,

                    "database": (
                        "connected"
                        if db_connected
                        else "unavailable"
                    ),

                    "status": (
                        "ok"
                        if db_connected
                        else "error"
                    ),

                    "message":
                        db_message,
                }
            ),
            status_code,
        )

    # ========================================================
    # PASSENGER TEST
    # ========================================================

    @flask_app.get(
        "/test-invoice-ptbsssatu"
    )
    def test_invoice():

        return jsonify(
            {
                "application":
                    APP_NAME,

                "status":
                    "FLASK_ACTIVE",

                "message":
                    "app.py berhasil dijalankan "
                    "oleh Passenger",

                "version":
                    APP_VERSION,

                "app_root":
                    APP_ROOT,
            }
        )

    # ========================================================
    # APPLICATION INFO
    # ========================================================

    @flask_app.get(
        "/api/status"
    )
    def api_status():

        return jsonify(
            {
                "application":
                    APP_NAME,

                "version":
                    APP_VERSION,

                "status":
                    "running",

                "python":
                    os.sys.version.split()[0],
            }
        )

    # ========================================================
    # DASHBOARD
    # ========================================================

    @flask_app.get(
        "/dashboard"
    )
    def dashboard():

        if not user_is_logged_in():

            return redirect(
                url_for("auth.login")
            )

        user = current_user_data()

        permissions = role_permissions(
            user["role"]
        )

        # ----------------------------------------------------
        # MENU AKTIF
        # ----------------------------------------------------

        active_menu = request.args.get(
            "menu",
            "dashboard",
        ).strip().lower()

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        context = {

            "app_name":
                APP_NAME,

            "app_version":
                APP_VERSION,

            "user":
                user,

            "permissions":
                permissions,

            "active_menu":
                active_menu,
        }

        # ----------------------------------------------------
        # DASHBOARD
        # ----------------------------------------------------

        if active_menu == "dashboard":

            return render_template(
                "dashboard.html",
                **context,
            )

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        if active_menu == "users":

            if not permissions.get(
                "super_admin",
                False,
            ):

                return (
                    "Anda tidak memiliki "
                    "hak akses.",
                    403,
                )

            try:

                from modul_keamanan.auth_service import (
                    ambil_semua_user,
                )

                users_data = (
                    ambil_semua_user()
                )

            except Exception as error:

                LOGGER.exception(
                    "Gagal mengambil "
                    "data user."
                )

                return (
                    f"Gagal mengambil "
                    f"data pengguna: {error}",
                    500,
                )

            context["users"] = users_data

            return render_template(
                "dashboard.html",
                **context,
            )

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------

        context[
            "active_menu"
        ] = "dashboard"

        return render_template(
            "dashboard.html",
            **context,
        )

    # ========================================================
    # DEBUG ROUTES
    # ========================================================

    @flask_app.get(
        "/debug/routes"
    )
    def debug_routes():

        routes = []

        for rule in flask_app.url_map.iter_rules():

            routes.append(
                {
                    "endpoint":
                        rule.endpoint,

                    "methods":
                        sorted(
                            rule.methods
                        ),

                    "path":
                        str(rule),
                }
            )

        return jsonify(routes)


# ============================================================
# ERROR HANDLERS
# ============================================================

def register_error_handlers(
    flask_app: Flask,
) -> None:

    # ========================================================
    # 404
    # ========================================================

    @flask_app.errorhandler(404)
    def not_found(error):

        # API menerima JSON.
        if (
            request.path.startswith(
                "/api/"
            )
            or request.path.startswith(
                "/health"
            )
            or request.path.startswith(
                "/test-"
            )
        ):

            return (
                jsonify(
                    {
                        "status":
                            "error",

                        "message":
                            "Endpoint tidak ditemukan.",
                    }
                ),
                404,
            )

        return (
            jsonify(
                {
                    "status":
                        "error",

                    "message":
                        "Halaman tidak ditemukan.",
                }
            ),
            404,
        )

    # ========================================================
    # 500
    # ========================================================

    @flask_app.errorhandler(500)
    def internal_error(error):

        LOGGER.exception(
            "Internal server error: %s",
            error,
        )

        return (
            jsonify(
                {
                    "status":
                        "error",

                    "message":
                        "Terjadi kesalahan pada server.",
                }
            ),
            500,
        )


# ============================================================
# SECURITY / RESPONSE HEADERS
# ============================================================

def register_response_headers(
    flask_app: Flask,
) -> None:

    @flask_app.after_request
    def set_security_headers(
        response
    ):

        response.headers.setdefault(
            "X-Content-Type-Options",
            "nosniff",
        )

        response.headers.setdefault(
            "X-Frame-Options",
            "SAMEORIGIN",
        )

        response.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )

        # ----------------------------------------------------
        # CACHE POLICY
        # ----------------------------------------------------

        if (
            response.mimetype
            not in [
                "text/css",
                "application/javascript",
            ]
        ):

            response.headers.setdefault(
                "Cache-Control",
                "no-store",
            )

        return response


# ============================================================
# FLASK APPLICATION
# ============================================================

app = create_app()

application = app


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    app.run(

        host=os.getenv(
            "APP_HOST",
            "127.0.0.1",
        ),

        port=int(
            os.getenv(
                "APP_PORT",
                "5000",
            )
        ),

        debug=(
            os.getenv(
                "FLASK_DEBUG",
                "0",
            )
            == "1"
        ),
    )
