import re
from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from web.database.supabase_client import create_fresh_anon_client, get_supabase_admin
from web.database.repositories.profile_repository import get_profile_by_id
from web.database.repositories.event_repository import record_event

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _classify_auth_error(err: Exception, context: str = "login") -> tuple:
    """
    Translates raw Supabase Auth / network exceptions into clear, accurate
    user-facing messages and HTTP status codes without leaking internals.
    Returns: (error_code, user_message, http_status)
    """
    raw = str(err)
    low = raw.lower()

    if "email not confirmed" in low:
        return (
            "email_not_confirmed",
            "Your email address has not been confirmed yet. Please check your inbox for the verification link before signing in.",
            403
        )
    if "invalid login credentials" in low or "invalid_credentials" in low:
        return (
            "invalid_credentials",
            "Invalid email or password. Please verify your credentials and try again.",
            401
        )
    if "user already registered" in low or "already exists" in low or "already been registered" in low:
        return (
            "account_exists",
            "An account with this email address already exists. Please sign in instead.",
            409
        )
    if "password should be at least" in low or "weak_password" in low:
        return (
            "weak_password",
            "Password must be at least 6 characters long.",
            400
        )
    if "rate limit" in low or "too many requests" in low or "over_email_send_rate_limit" in low:
        return (
            "rate_limited",
            "Too many authentication attempts. Please wait a moment and try again.",
            429
        )
    if any(k in low for k in ["connecterror", "timeout", "name or service not known", "temporary failure", "network"]):
        return (
            "network_error",
            "Unable to reach the Supabase authentication service. Check internet/network connectivity.",
            503
        )
    if "invalid api key" in low or "unauthorized" in low:
        return (
            "config_error",
            "Authentication service configuration error. Contact the LabNet administrator.",
            500
        )

    fallback = "Authentication failed. Please try again." if context == "login" else "Registration failed. Please check your details and try again."
    return ("auth_error", fallback, 400)


def register_user(email: str, password: str, display_name: str = None):
    """
    Registers a new student user through Supabase Auth.
    SECURITY: Public registration NEVER grants administrator privileges.
    The existing PostgreSQL trigger `on_auth_user_created` automatically inserts
    the profile row with role='student'. Do not manually duplicate profile rows.
    """
    normalized_email = (email or "").strip().lower()
    clean_name = (display_name or "").strip()

    if not normalized_email or not EMAIL_REGEX.match(normalized_email):
        return False, {"code": "invalid_email", "error": "Please enter a valid email address."}, 400

    if not password or len(password) < 6:
        return False, {"code": "weak_password", "error": "Password must be at least 6 characters long."}, 400

    if not clean_name:
        clean_name = normalized_email.split("@")[0]

    client = create_fresh_anon_client()
    if not client:
        return False, {"code": "config_error", "error": "Supabase authentication client is not configured."}, 503

    try:
        res = client.auth.sign_up({
            "email": normalized_email,
            "password": password,
            "options": {
                "data": {
                    "display_name": clean_name
                }
            }
        })
        user = res.user
        auth_session = res.session

        if not user:
            return False, {"code": "signup_failed", "error": "Unable to create user account."}, 400

        # Supabase returns an empty identities list when an email is already registered
        # and email enumeration protection is active
        identities = getattr(user, "identities", None)
        if identities is not None and len(identities) == 0:
            return False, {
                "code": "account_exists",
                "error": "An account with this email address already exists. Please sign in instead."
            }, 409

        profile = get_profile_by_id(user.id)
        final_role = profile["role"] if profile else "student"
        final_name = profile["display_name"] if profile else clean_name

        record_event(
            "auth",
            protocol="HTTP",
            source_ip=request.remote_addr if request else None,
            details={
                "action": "register",
                "email": normalized_email,
                "user_id": user.id,
                "role": final_role,
                "requires_confirmation": auth_session is None
            }
        )

        # If email confirmation is required, auth_session will be None.
        # Do NOT log the user into the Flask session prematurely.
        if auth_session is None:
            return True, {
                "requires_confirmation": True,
                "message": "Account created! Please check your email and confirm your address before signing in.",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "display_name": final_name,
                    "role": final_role
                }
            }, 201

        # Email autoconfirm is enabled: return deliberate registration result
        # so the user transitions cleanly without confusing duplicate logins.
        return True, {
            "requires_confirmation": False,
            "message": "Account created successfully! Signing you in...",
            "user": {
                "id": user.id,
                "email": user.email,
                "access_token": auth_session.access_token,
                "display_name": final_name,
                "role": final_role
            }
        }, 201

    except Exception as e:
        code, msg, status = _classify_auth_error(e, context="register")
        print(f"[AUTH REGISTER] ({code}) for {normalized_email}")
        return False, {"code": code, "error": msg}, status


def login_user(email: str, password: str):
    """
    Authenticates an existing user via Supabase Auth and loads their authoritative
    profile and role from PostgreSQL `profiles`.
    """
    normalized_email = (email or "").strip().lower()
    if not normalized_email or not password:
        return False, {"code": "missing_fields", "error": "Email and password are required."}, 400

    client = create_fresh_anon_client()
    if not client:
        return False, {"code": "config_error", "error": "Supabase authentication client is not configured."}, 503

    try:
        res = client.auth.sign_in_with_password({
            "email": normalized_email,
            "password": password
        })
        user = res.user
        auth_session = res.session

        if not user or not auth_session:
            return False, {"code": "invalid_credentials", "error": "Invalid email or password."}, 401

        profile = get_profile_by_id(user.id)
        display_name = profile["display_name"] if profile else normalized_email.split("@")[0]
        role = profile["role"] if profile else "student"

        record_event(
            "auth",
            protocol="HTTP",
            source_ip=request.remote_addr if request else None,
            details={
                "action": "login",
                "email": normalized_email,
                "user_id": user.id,
                "role": role
            }
        )

        return True, {
            "id": user.id,
            "email": user.email,
            "access_token": auth_session.access_token,
            "display_name": display_name,
            "role": role
        }, 200

    except Exception as e:
        code, msg, status = _classify_auth_error(e, context="login")
        print(f"[AUTH LOGIN] ({code}) for {normalized_email}")
        return False, {"code": code, "error": msg}, status


def send_password_reset(email: str, redirect_url: str = None):
    """Requests a Supabase password reset email if supported by the project."""
    normalized_email = (email or "").strip().lower()
    if not normalized_email or not EMAIL_REGEX.match(normalized_email):
        return False, "Please enter a valid email address.", 400

    client = create_fresh_anon_client()
    if not client:
        return False, "Authentication service unavailable.", 503

    try:
        opts = {"redirect_to": redirect_url} if redirect_url else {}
        client.auth.reset_password_for_email(normalized_email, opts)
        return True, "If an account exists for this email, a password reset link has been sent.", 200
    except Exception as e:
        _, msg, status = _classify_auth_error(e, context="reset")
        return False, msg, status


def logout_user(user_info: dict = None):
    """Logs out cleanly and records audit event."""
    record_event(
        "auth",
        protocol="HTTP",
        source_ip=request.remote_addr if request else None,
        details={
            "action": "logout",
            "user_id": user_info.get("id") if user_info else None,
            "email": user_info.get("email") if user_info else None
        }
    )
    return True, "Logged out successfully"


def refresh_session_user():
    """
    Syncs the current Flask session user's role & display_name against the live
    Supabase `profiles` table so role changes take effect immediately.
    """
    user = session.get("user")
    if not user or not user.get("id"):
        return None
    profile = get_profile_by_id(user["id"])
    if profile:
        user["display_name"] = profile.get("display_name", user.get("display_name"))
        user["role"] = profile.get("role", "student")
        session["user"] = user
    return user


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get("user")
        if not user or not user.get("id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required. Please sign in.", "authenticated": False}), 401
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Enforces administrator authorization on backend routes and APIs by checking
    both session state and the authoritative `profiles.role` record in Supabase.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get("user")
        if not user or not user.get("id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required.", "authenticated": False}), 401
            return redirect(url_for("login_page", next=request.path))

        # Authoritative check against database profile
        profile = get_profile_by_id(user["id"])
        role = profile["role"] if profile else user.get("role", "student")
        user["role"] = role
        session["user"] = user

        if role != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Administrator privileges required.", "code": "forbidden"}), 403
            return redirect(url_for("index"))

        return f(*args, **kwargs)
    return decorated_function
