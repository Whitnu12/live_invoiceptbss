from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    render_template_string,
)

from .auth_service import (
    autentikasi_user,
    ambil_semua_user,
    tambah_user,
    edit_user,
    ubah_status_user,
    ubah_password_user,
    hapus_user,
)

from .decorators import (
    login_required,
    role_required,
)

auth_bp = Blueprint(
    "auth",
    __name__,
)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    # Jika sudah login, langsung ke dashboard
    if session.get("logged_in"):
        return redirect(url_for("index"))

    if request.method == "POST":

        username = request.form.get(
            "username",
            "",
        ).strip()

        password = request.form.get(
            "password",
            "",
        )

        if not username or not password:
            flash(
                "Username dan password wajib diisi.",
                "error",
            )

            return render_template(
                "login.html"
            )

        user = autentikasi_user(
            username,
            password,
        )

        if not user:
            flash(
                "Username atau password salah.",
                "error",
            )

            return render_template(
                "login.html"
            )

        # Bersihkan session lama
        session.clear()

        # Simpan identitas user
        session["logged_in"] = True
        session["user_id"] = user["id"]
        session["current_user"] = user["username"]
        session["current_role"] = user["role"]
        session["nama_lengkap"] = user["nama_lengkap"]
        session["departemen"] = user["departemen"]

        session.permanent = True

        return redirect(
            url_for("index")
        )

    return render_template(
        "login.html"
    )


@auth_bp.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("auth.login")
    )
    
    
@auth_bp.route(
    "/security/users",
    methods=["GET", "POST"]
)
@login_required
@role_required("Super Admin")
def users():

    """
    Endpoint backend untuk Manajemen Akun.

    Halaman UI tetap berada di:
        /dashboard?menu=users

    Endpoint ini hanya menangani proses:
        - tambah user
        - edit user
        - aktif/nonaktif user
        - reset password
        - hapus user
    """

    # ========================================================
    # GET
    # ========================================================

    if request.method == "GET":

        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # POST
    # ========================================================

    action = (
        request.form.get(
            "action",
            ""
        )
        .strip()
        .lower()
    )


    # ========================================================
    # TAMBAH USER
    # ========================================================

    if action == "add":

        username = (
            request.form.get(
                "username",
                ""
            )
            .strip()
        )

        password = request.form.get(
            "password",
            ""
        )

        nama_lengkap = (
            request.form.get(
                "nama_lengkap",
                ""
            )
            .strip()
        )

        role = (
            request.form.get(
                "role",
                ""
            )
            .strip()
        )

        departemen = (
            request.form.get(
                "departemen",
                ""
            )
            .strip()
        )


        try:

            tambah_user(
                username=username,
                password=password,
                nama_lengkap=nama_lengkap,
                role=role,
                departemen=departemen,
            )

            flash(
                f"User '{username}' berhasil ditambahkan.",
                "success",
            )

        except ValueError as exc:

            flash(
                str(exc),
                "error",
            )

        except Exception:

            flash(
                "Gagal menambahkan user.",
                "error",
            )


        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # EDIT USER
    # ========================================================

    if action == "edit":

        user_id = request.form.get(
            "user_id",
            ""
        )

        username = (
            request.form.get(
                "username",
                ""
            )
            .strip()
        )

        nama_lengkap = (
            request.form.get(
                "nama_lengkap",
                ""
            )
            .strip()
        )

        role = (
            request.form.get(
                "role",
                ""
            )
            .strip()
        )

        departemen = (
            request.form.get(
                "departemen",
                ""
            )
            .strip()
        )


        try:

            edit_user(
                user_id=user_id,
                username=username,
                nama_lengkap=nama_lengkap,
                role=role,
                departemen=departemen,
            )

            flash(
                f"Data user '{username}' berhasil diperbarui.",
                "success",
            )

        except ValueError as exc:

            flash(
                str(exc),
                "error",
            )

        except Exception:

            flash(
                "Gagal memperbarui data user.",
                "error",
            )


        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # AKTIF / NONAKTIF USER
    # ========================================================

    if action == "toggle_status":

        user_id = request.form.get(
            "user_id",
            ""
        )

        is_active_raw = request.form.get(
            "is_active",
            "0"
        )


        is_active = (
            str(is_active_raw)
            .strip()
            == "1"
        )


        try:

            ubah_status_user(
                user_id=user_id,
                is_active=is_active,
            )


            if is_active:

                flash(
                    "User berhasil diaktifkan.",
                    "success",
                )

            else:

                flash(
                    "User berhasil dinonaktifkan.",
                    "success",
                )


        except ValueError as exc:

            flash(
                str(exc),
                "error",
            )

        except Exception:

            flash(
                "Gagal mengubah status user.",
                "error",
            )


        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # RESET PASSWORD
    # ========================================================

    if action == "reset_password":

        user_id = request.form.get(
            "user_id",
            ""
        )

        password_baru = request.form.get(
            "password_baru",
            ""
        )


        try:

            ubah_password_user(
                user_id=user_id,
                password_baru=password_baru,
            )

            flash(
                "Password user berhasil diubah.",
                "success",
            )

        except ValueError as exc:

            flash(
                str(exc),
                "error",
            )

        except Exception:

            flash(
                "Gagal mengubah password user.",
                "error",
            )


        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # HAPUS USER
    # ========================================================

    if action == "delete":

        user_id = request.form.get(
            "user_id",
            ""
        )


        try:

            hapus_user(
                user_id=user_id
            )

            flash(
                "User berhasil dihapus.",
                "success",
            )

        except ValueError as exc:

            flash(
                str(exc),
                "error",
            )

        except Exception:

            flash(
                "Gagal menghapus user.",
                "error",
            )


        return redirect(
            url_for(
                "dashboard",
                menu="users"
            )
        )


    # ========================================================
    # ACTION TIDAK DIKENAL
    # ========================================================

    flash(
        "Permintaan Manajemen Akun tidak dikenali.",
        "error",
    )

    return redirect(
        url_for(
            "dashboard",
            menu="users"
        )
    )
    users_data = ambil_semua_user()

    return render_template(
        "security/users.html",
        users=users_data,
    )