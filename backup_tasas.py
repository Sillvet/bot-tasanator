from datetime import datetime
import os
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv
from openpyxl import load_workbook

# --- Cargar variables de entorno ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Obtener fecha actual ---
ahora = datetime.now()
fecha_hoy = ahora.date().isoformat()
nombre_hoja = fecha_hoy
nombre_archivo = f"tasas_{ahora.strftime('%B_%Y').lower()}.xlsx"  # ejemplo: tasas_mayo_2025.xlsx

# --- Consultar tasas del día ---
print(f"🔎 Consultando tasas del {fecha_hoy}...")
res = supabase.table("tasas").select("*").execute()
datos = [r for r in res.data if r['fecha'].startswith(fecha_hoy)]

if not datos:
    print("⚠️ No hay tasas registradas hoy. Nada que respaldar.")
    exit()

# --- Convertir a DataFrame ---
df = pd.DataFrame(datos)

# --- Guardar en Excel mensual (hoja por día) ---
if os.path.exists(nombre_archivo):
    with pd.ExcelWriter(nombre_archivo, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        df.to_excel(writer, sheet_name=nombre_hoja, index=False)
else:
    with pd.ExcelWriter(nombre_archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=nombre_hoja, index=False)

print(f"✅ Datos exportados a hoja '{nombre_hoja}' en {nombre_archivo}")

# --- Eliminar tasas del día de Supabase ---
print("🧹 Eliminando tasas del día en Supabase...")
for d in datos:
    supabase.table("tasas").delete().eq("id", d['id']).execute()

print("✅ Limpieza completada. Backup finalizado.")