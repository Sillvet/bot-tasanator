from supabase import create_client, Client
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import requests
import os
from dotenv import load_dotenv

# --- Cargar variables de entorno ---
load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)
print("Conectado a:", url)

# --- Limpiar tabla de tasas ---
print("🧹 Eliminando TODAS las tasas anteriores...")
supabase.table("tasas").delete().neq("id", 0).execute()

# --- EXTRAER USDT DESDE KANDUI ---
email = "gerenciavip22@gmail.com"
password = "Gerencia12!"

options = webdriver.ChromeOptions()
options.add_argument("--headless")
options.add_argument("--disable-gpu")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

print("\U0001F310 Abriendo Kandui...")
driver.get("https://www.kandui.cl/ingreso")

try:
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "email")))
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "password")))

    driver.find_element(By.NAME, "email").send_keys(email)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.NAME, "password").send_keys(Keys.RETURN)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, '//p[contains(text(), "USDT")]/following-sibling::p'))
    )

    usdt_valor = driver.find_element(By.XPATH, '//p[contains(text(), "USDT")]/following-sibling::p')
    valor_texto = usdt_valor.text.strip().replace("$", "").replace(",", ".")
    valor_kandui = float(valor_texto)
    print(f"\U0001F4C8 Valor USDT Kandui: {valor_kandui}")

    supabase.table("tasas").insert({
        "nombre_tasa": "USDT_KANDUI",
        "valor": round(valor_kandui, 4),
        "fecha": datetime.now().isoformat()
    }).execute()

except Exception as e:
    print("❌ Error extrayendo USDT Kandui:", e)
    driver.quit()
    exit()

finally:
    driver.quit()

# --- FUNCIONES PARA BINANCE ---
def get_p2p_data(asset, fiat, trade_type, rows=20, pay_types=None, countries=None):
    url = 'https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search'
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    payload = {
        "asset": asset, "fiat": fiat, "merchantCheck": False,
        "page": 1, "rows": rows, "tradeType": trade_type,
        "payTypes": pay_types if pay_types else [],
        "countries": countries if countries else []
    }
    response = requests.post(url, headers=headers, json=payload)
    data = response.json()
    if response.status_code == 200 and data['code'] == '000000':
        return data['data']
    else:
        print(f"Error al obtener datos: {data['message']}")
        return None

# --- DEFINIR CONFIGURACIÓN DE CADA PAÍS ---
paises_config = {
    "Venezuela": {"fiat": "VES", "porcentaje": 0.065},
    "Colombia": {"fiat": "COP", "porcentaje": 0.07},
    "Argentina": {"fiat": "ARS", "porcentaje": 0.065},
    "Perú": {"fiat": "PEN", "porcentaje": 0.12},
    "Brasil": {"fiat": "BRL", "porcentaje": 0.10},
    "Euro": {"fiat": "EUR", "pay_type": "Bizum", "porcentaje": 0.10},
    "USA": {"fiat": "USD", "pay_type": "Zelle", "porcentaje": 0.10},
    "México": {"fiat": "MXN", "porcentaje": 0.10},
    "Panamá": {"fiat": "PAB", "porcentaje": 0.07},
    "Ecuador": {"fiat": "USD", "country": "EC", "porcentaje": 0.10},
}

fecha_actual = datetime.now().isoformat()

for pais, config in paises_config.items():
    fiat = config["fiat"]
    porcentaje = config["porcentaje"]
    pay_type = config.get("pay_type")
    country = config.get("country")

    filtros = {
        "pay_types": [pay_type] if pay_type else None,
        "countries": [country] if country else None
    }

    datos = get_p2p_data("USDT", fiat, "SELL", **filtros)
    if datos and len(datos) >= 3:
        precio = float(datos[2]['adv']['price'])  # tercera oferta

        supabase.table("tasas").insert({
            "nombre_tasa": f"USDT_SELL_{pais}",
            "valor": round(precio, 4),
            "fecha": fecha_actual
        }).execute()

        tasa_full = round(precio / valor_kandui, 6)
        tasa_publico = round(tasa_full * (1 - porcentaje), 6)

        supabase.table("tasas").insert([
            {"nombre_tasa": f"Tasa full Chile-{pais}", "valor": round(tasa_full, 4), "fecha": fecha_actual},
            {"nombre_tasa": f"Tasa público Chile-{pais}", "valor": round(tasa_publico, 4), "fecha": fecha_actual},
        ]).execute()
