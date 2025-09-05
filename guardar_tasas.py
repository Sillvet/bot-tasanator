import requests
import time
from datetime import datetime
from supabase import create_client, Client
import os
from dotenv import load_dotenv

# =============================
# CONFIG
# =============================
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BINANCE_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

PAISES = ["VES", "COP", "PEN", "ARS", "CLP", "BRL", "MXN", "USD", "EUR"]

# =============================
# FUNCIONES
# =============================

def descubrir_metodos(moneda):
    """
    Devuelve los métodos de pago detectados dinámicamente en Binance para BUY y SELL.
    """
    metodos = {"BUY": [], "SELL": []}
    for side in ["BUY", "SELL"]:
        payload = {
            "asset": "USDT",
            "fiat": moneda,
            "tradeType": side,
            "page": 1,
            "rows": 20
        }
        try:
            r = requests.post(BINANCE_URL, json=payload, timeout=10)
            data = r.json()
            if "data" in data and data["data"]:
                for adv in data["data"]:
                    for metodo in adv["adv"]["tradeMethods"]:
                        if metodo["identifier"] not in metodos[side]:
                            metodos[side].append(metodo["identifier"])
            else:
                print(f"⚠️ No se encontraron métodos para {moneda} {side}")
        except Exception as e:
            print(f"❌ Error en descubrimiento {moneda} {side}: {e}")
    return metodos


def obtener_precio(moneda, side, metodo=None, amount=None):
    """
    Devuelve el precio de USDT en una moneda para un side (BUY/SELL),
    usando un método específico si se pasa.
    """
    payload = {
        "asset": "USDT",
        "fiat": moneda,
        "tradeType": side,
        "page": 1,
        "rows": 10
    }
    if metodo:
        payload["payTypes"] = [metodo]
    if amount:
        payload["transAmount"] = str(amount)

    try:
        r = requests.post(BINANCE_URL, json=payload, timeout=10)
        data = r.json()
        if "data" in data and len(data["data"]) >= 3:
            return float(data["data"][2]["adv"]["price"])  # Tercera oferta
    except Exception as e:
        print(f"❌ Error al obtener precio {moneda} {side}: {e}")

    return None


def guardar_tasa(origen, destino, tasa, tipo):
    """
    Guarda la tasa en Supabase.
    """
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    supabase.table("tasas").insert({
        "origen": origen,
        "destino": destino,
        "tasa": tasa,
        "tipo": tipo,
        "fecha_actual": fecha
    }).execute()


def actualizar_todas_las_tasas():
    print("🔁 Ejecutando actualización de tasas...")

    for origen in PAISES:
        metodos_origen = descubrir_metodos(origen)
        if not metodos_origen["BUY"]:
            print(f"⚠️ Sin métodos de compra detectados para {origen}")
            continue
        if not metodos_origen["SELL"]:
            print(f"⚠️ Sin métodos de venta detectados para {origen}")
            continue

        # Ejemplo: tomar el primer método válido
        metodo_buy = metodos_origen["BUY"][0]
        metodo_sell = metodos_origen["SELL"][0]

        precio_buy = obtener_precio(origen, "BUY", metodo_buy)
        precio_sell = obtener_precio(origen, "SELL", metodo_sell)

        if precio_buy and precio_sell:
            tasa_full = precio_sell / precio_buy
            tasa_publico = tasa_full * 0.94
            tasa_mayorista = tasa_full * 0.97

            print(f"✅ {origen} - FULL={tasa_full:.4f}, PUBLICO={tasa_publico:.4f}, MAYORISTA={tasa_mayorista:.4f}")

            guardar_tasa(origen, "USDT", tasa_full, "FULL")
            guardar_tasa(origen, "USDT", tasa_publico, "PUBLICO")
            guardar_tasa(origen, "USDT", tasa_mayorista, "MAYORISTA")
        else:
            print(f"⚠️ No se pudo calcular tasa para {origen}")

    print("✅ Todas las tasas fueron actualizadas correctamente.")


# =============================
# MAIN
# =============================
if __name__ == "__main__":
    actualizar_todas_las_tasas()
