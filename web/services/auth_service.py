from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from web.database.supabase_client import get_supabase_anon, get_supabase_admin
from web.database.repositories.profile_repository import get_profile_by_id, update_profile
from web.database.repositories.event_repository import record_event


def register_user(email: str, password: str, display_name: str = None, role: str = 'student'):
    """
    Registers a new user through Supabase Auth.
    The PostgreSQL trigger on_auth_user_created automatically generates the profile row.
    """
    client = get_supabase_anon()
    if not client:
        return False, "Auth client unavailable"

    meta = {'display_name': display_name or email.split('@')[0]}
    try:
        res = client.auth.sign_up({
            'email': email,
            'password': password,
            'options': {
                'data': meta
            }
        })
        user = res.user
        if not user:
            return False, "Failed to create user account"

        # If user specified role='admin', update it via admin client
        if role in ['admin', 'student']:
            update_profile(user.id, role=role)

        record_event('auth', details={'action': 'register', 'email': email, 'user_id': user.id})
        return True, {
            'id': user.id,
            'email': user.email,
            'display_name': meta['display_name'],
            'role': role
        }
    except Exception as e:
        print(f"[AUTH ERROR: register_user] {e}")
        return False, str(e)


def login_user(email: str, password: str):
    """
    Authenticates user with Supabase Auth and fetches profile.
    """
    client = get_supabase_anon()
    if not client:
        return False, "Auth client unavailable"

    try:
        res = client.auth.sign_in_with_password({
            'email': email,
            'password': password
        })
        user = res.user
        session_obj = res.session
        if not user:
            return False, "Invalid credentials"

        # Fetch profile
        profile = get_profile_by_id(user.id)
        display_name = profile['display_name'] if profile else user.email.split('@')[0]
        role = profile['role'] if profile else 'student'

        record_event('auth', details={'action': 'login', 'email': email, 'user_id': user.id})
        return True, {
            'id': user.id,
            'email': user.email,
            'access_token': session_obj.access_token if session_obj else None,
            'display_name': display_name,
            'role': role
        }
    except Exception as e:
        print(f"[AUTH ERROR: login_user] {e}")
        return False, str(e)


def logout_user():
    """
    Signs out user session.
    """
    client = get_supabase_anon()
    if client:
        try:
            client.auth.sign_out()
        except Exception:
            pass
    record_event('auth', details={'action': 'logout'})
    return True, "Logged out successfully"


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized. Please log in.', 'authenticated': False}), 401
            return redirect(url_for('login_page', next=request.url))
        return f(*args, **kwargs)
    return decorated_function
