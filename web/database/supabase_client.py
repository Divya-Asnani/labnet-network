import os
import threading
from pathlib import Path
from dotenv import dotenv_values
from supabase import create_client, Client

_thread_local = threading.local()
_env_lock = threading.Lock()
_env_loaded = False
_config = {}


def _load_env():
    global _env_loaded, _config
    if not _env_loaded:
        with _env_lock:
            if not _env_loaded:
                root_dir = Path(__file__).resolve().parent.parent.parent
                env_file = root_dir / ".env"
                if env_file.exists():
                    _config = dotenv_values(env_file)
                else:
                    _config = dotenv_values(".env")
                _env_loaded = True


def get_config_val(key: str, default=None):
    _load_env()
    val = _config.get(key)
    if val is None or val == "":
        val = os.getenv(key, default)
    return val


def get_supabase_admin() -> Client:
    """
    Returns a thread-local Supabase Client authenticated with SUPABASE_SERVICE_ROLE_KEY.
    Thread-local isolation prevents concurrent Flask/Socket.IO worker threads from
    colliding over a shared httpx HTTP/2 connection pool.
    Used strictly in trusted backend routines; never exposed to browsers.
    """
    client = getattr(_thread_local, "admin_client", None)
    if client is not None:
        return client

    _load_env()
    url = get_config_val("SUPABASE_URL")
    key = get_config_val("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("[DATABASE] Warning: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured.")
        return None

    try:
        client = create_client(url, key)
        _thread_local.admin_client = client
        return client
    except Exception as e:
        print(f"[DATABASE] Error initializing Supabase Admin Client: {e}")
        return None


def reset_thread_admin_client():
    """Clears the current thread's admin client so the next call establishes a fresh connection."""
    _thread_local.admin_client = None


def create_fresh_anon_client() -> Client:
    """
    Creates an isolated Supabase Anon Client per authentication request so concurrent
    user sign-in/sign-up/sign-out requests never mutate shared GoTrue session state.
    """
    _load_env()
    url = get_config_val("SUPABASE_URL")
    key = get_config_val("SUPABASE_ANON_KEY")

    if not url or not key:
        print("[DATABASE] Warning: SUPABASE_URL or SUPABASE_ANON_KEY not configured.")
        return None

    try:
        return create_client(url, key)
    except Exception as e:
        print(f"[DATABASE] Error creating Supabase Anon Client: {e}")
        return None


def get_supabase_anon() -> Client:
    """Alias returning an isolated anon client for backwards compatibility."""
    return create_fresh_anon_client()


def create_user_scoped_client(access_token: str) -> Client:
    """
    Creates an isolated Supabase client authenticated with a specific user's JWT
    so all PostgREST queries run strictly under PostgreSQL Row Level Security (RLS)
    as `auth.uid() = <user_id>`.
    """
    client = create_fresh_anon_client()
    if client and access_token:
        client.postgrest.auth(access_token)
    return client
