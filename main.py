from supabase_client import supabase
from datetime import datetime

def guardar_tasa(nombre, valor):
    data = {
        "nombre_tasa": nombre,
        "valor": valor,
        "fecha_hora": datetime.now().isoformat()
    }
    supabase.table("tasas").insert(data).execute()
    print(f"Tasa '{nombre}' guardada con éxito: {valor}")

# Ejemplo: guarda una tasa de prueba
guardar_tasa("leon", 1030.4567)
