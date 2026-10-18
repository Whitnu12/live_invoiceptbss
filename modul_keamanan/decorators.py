from functools import wraps

from flask import (
    session,
    redirect,
    url_for,
    request,
)


def login_required(view_function):

    @wraps(view_function)
    def wrapped_view(*args, **kwargs):

        if not session.get("logged_in"):
            return redirect(
                url_for(
                    "auth.login",
                    next=request.path,
                )
            )

        return view_function(
            *args,
            **kwargs,
        )

    return wrapped_view


def role_required(*allowed_roles):

    normalized_roles = {
        str(role).strip().lower()
        for role in allowed_roles
    }

    def decorator(view_function):

        @wraps(view_function)
        def wrapped_view(*args, **kwargs):

            if not session.get("logged_in"):
                return redirect(
                    url_for("auth.login")
                )

            current_role = str(
                session.get(
                    "current_role",
                    "",
                )
            ).strip().lower()

            if current_role not in normalized_roles:
                return (
                    "Anda tidak memiliki hak akses.",
                    403,
                )

            return view_function(
                *args,
                **kwargs,
            )

        return wrapped_view

    return decorator