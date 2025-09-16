import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Asegura que las variables del .env estén cargadas aunque te importen antes
load_dotenv(override=True)

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    raise RuntimeError(
        "SUPABASE_URL o SUPABASE_KEY no están definidos. "
        "Verifica tu .env y que load_dotenv() se ejecute antes."
    )

# Cliente global reutilizable
supabase: Client = create_client(url, key)
