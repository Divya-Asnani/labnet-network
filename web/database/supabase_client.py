import os
from pathlib import Path
from dotenv import dotenv_values
from supabase import create_client, Client

_admin_client: Client = None
_anon_client: Client = None
_env_loaded = False
_config = {}


def _load_env():
    global _env_loaded, _config
    if not _env_loaded:
        # Search for .env in repo root or current working dir
        root_dir = Path(__file__).resolve().parent.parent.parent
        env_file = root_dir / '.env'
        if env_file.exists():
            _config = dotenv_values(env_file)
        else:
            _config = dotenv_values('.env')
        _env_loaded = True


def get_config_val(key, default=None):
    _load_env()
    return _config.get(key) or os.getenv(key, default)


def get_supabase_admin() -> Client:
    """
    Returns a singleton Supabase Client authenticated with the SUPABASE_SERVICE_ROLE_KEY.
    Used strictly in trusted backend routines (device registration, heartbeat logging,
    event auditing, session persistence). Never exposed to browsers.
    """
    global _admin_client
    if _admin_client is not None:
        return _admin_client

    _load_env()
    url = get_config_val('SUPABASE_URL')
    key = get_config_val('SUPABASE_SERVICE_ROLE_KEY')

    if not url or not key:
        print("[DATABASE] Warning: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured.")
        return None

    try:
        _admin_client = create_client(url, key)
        return _admin_client
    except Exception as e:
        print(f"[DATABASE] Error initializing Supabase Admin Client: {e}")
        return None


def get_supabase_anon() -> Client:
    """
    Returns a singleton Supabase Client authenticated with the SUPABASE_ANON_KEY.
    Used for user-initiated auth (sign_up, sign_in_with_password).
    """
    global _anon_client
    if _anon_client is not None:
        return _anon_client

    _load_env()
    url = get_config_val('SUPABASE_URL')
    key = get_config_val('SUPABASE_ANON_KEY')

    if not url or not key:
        print("[DATABASE] Warning: SUPABASE_URL or SUPABASE_ANON_KEY not configured.")
        return None

    try:
        _anon_client = create_client(url, key)
        return _anon_client
    except Exception as e:
        print(f"[DATABASE] Error initializing Supabase Anon Client: {e}")
        return None
