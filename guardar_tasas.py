from datetime import datetime, timedelta
from decimal import Decimal
from supabase_client import supabase
import requests

# === Obtener tercera oferta de Binance ===
def get_third_offer(asset, fiat, trade_type, amount=None, pay_types=None):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "asset": asset,
        "fiat": fiat,
        "merchantCheck": False,
        "page": 1,
        "rows": 10,
        "tradeType": trade_type.upper(),
        "payTypes": pay_types if pay_types else [],
        "countries": []
    }
    if amount:
        payload["transAmount"] = str(amount)

    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        if data['code'] == '000000' and len(data['data']) >= 3:
            return float(data['data'][2]['adv']['price'])
        else:
            print(f"⚠️ Menos de 3 ofertas para {fiat} {trade_type} (amount: {amount})")
            return None
    except Exception as e:
        print(f"❌ Error al obtener tercera oferta: {e}")
        return None

# === Guardar tasa en Supabase ===
def guardar_tasa(nombre, valor, decimales=4):
    try:
        fecha_venezuela = datetime.utcnow() - timedelta(hours=4)
        response = supabase.table("tasas").insert({
            "nombre_tasa": nombre,
            "valor": round(valor, decimales),
            "fecha_actual": fecha_venezuela.isoformat()
        }).execute()
        if not response.data:
            print(f"❌ No se guardó {nombre}. Respuesta vacía.")
        else:
            print(f"✅ Tasa guardada: {nombre} = {round(valor, decimales)}")
    except Exception as e:
        print(f"❌ Excepción al guardar {nombre}: {e}")

# === Calcular promedio sin redondear aún ===
def promedio_tasa(nombre):
    response = supabase.table("tasas").select("valor").eq("nombre_tasa", nombre).order("fecha_actual", desc=True).limit(2).execute()
    valores = [Decimal(row["valor"]) for row in response.data]
    if len(valores) == 2:
        return float((valores[0] + valores[1]) / 2)
    return None

# === Márgenes personalizados ===
margenes_personalizados = {
    "Chile - Venezuela": {"publico": 0.055, "mayorista": 0.040},
    "Chile - Colombia": {"publico": 0.06, "mayorista": 0.04},
    "Chile - Argentina": {"publico": 0.07, "mayorista": 0.05},
    "Chile - Perú": {"publico": 0.06, "mayorista": 0.04},
    "Chile - Brasil": {"publico": 0.10, "mayorista": 0.05},
    "Chile - Europa": {"publico": 0.07, "mayorista": 0.05},
    "Chile - USA": {"publico": 0.10, "mayorista": 0.07},
    "Chile - México": {"publico": 0.10, "mayorista": 0.07},
    "Chile - Panamá": {"publico": 0.07, "mayorista": 0.05},
    "Chile - Ecuador": {"publico": 0.07, "mayorista": 0.05},
    "Colombia - Venezuela": {"publico": 0.06, "mayorista": 0.04},
    "Argentina - Venezuela": {"publico": 0.07, "mayorista": 0.04},
    "México - Venezuela": {"publico": 0.10, "mayorista": 0.07},
    "USA - Venezuela": {"publico": 0.10, "mayorista": 0.06},
    "Perú - Venezuela": {"publico": 0.07, "mayorista": 0.04},
    "Brasil - Venezuela": {"publico": 0.10, "mayorista": 0.05},
    "Europa - Venezuela": {"publico": 0.10, "mayorista": 0.05},
    "Panamá - Venezuela": {"publico": 0.07, "mayorista": 0.04},
    "Ecuador - Venezuela": {"publico": 0.07, "mayorista": 0.04},
    "Colombia - Argentina": {"publico": 0.07, "mayorista": 0.04},
    "Colombia - Europa": {"publico": 0.07, "mayorista": 0.04},
    "Argentina - Ecuador": {"publico": 0.07, "mayorista": 0.04},
    "Europa - Ecuador": {"publico": 0.10, "mayorista": 0.05},
    "Colombia - Ecuador": {"publico": 0.07, "mayorista": 0.04},
}

# === Pares que suman margen ===
pares_sumar_margen = [
    "Chile - USA",
    "Colombia - Venezuela"
]

# === Lógica de actualización ===
def actualizar_todas_las_tasas():
    print("\n🔁 Ejecutando actualización de tasas...")

    paises = ["Venezuela", "Colombia", "Argentina", "Perú", "Brasil", "Europa", "USA", "México", "Panamá", "Ecuador", "Chile"]
    fiats = {
        "Venezuela": "VES", "Colombia": "COP", "Argentina": "ARS", "Perú": "PEN", "Brasil": "BRL", "Europa": "EUR",
        "USA": "USD", "México": "MXN", "Panamá": "USD", "Ecuador": "USD", "Chile": "CLP"
    }

    precios_usdt = {}

    # === Precios de compra ===
    for pais in paises:
        fiat = fiats[pais]
        pay_types = ["Bizum"] if pais == "Europa" else []

        if pais == "Venezuela":
            paso_1 = get_third_offer("USDT", fiat, "BUY")
            if not paso_1: continue
            monto = paso_1 * 300
            buy_price = get_third_offer("USDT", fiat, "BUY", monto)
        else:
            paso_1 = get_third_offer("USDT", fiat, "BUY", None, pay_types)
            if not paso_1: continue
            monto = paso_1 * 100
            buy_price = get_third_offer("USDT", fiat, "BUY", monto, pay_types)

        if buy_price:
            precios_usdt[pais] = buy_price
            guardar_tasa(f"USDT en {pais}", buy_price)

    # === Precios de venta ===
    for pais in paises:
        fiat = fiats[pais]
        pay_types = ["Bizum"] if pais == "Europa" else []

        if pais == "Venezuela":
            paso_1 = get_third_offer("USDT", fiat, "SELL")
            if not paso_1: continue
            monto = paso_1 * 300
            sell_price = get_third_offer("USDT", fiat, "SELL", monto)
        else:
            paso_1 = get_third_offer("USDT", fiat, "SELL", None, pay_types)
            if not paso_1: continue
            monto = paso_1 * 100
            sell_price = get_third_offer("USDT", fiat, "SELL", monto, pay_types)

        if sell_price:
            precios_usdt[f"{pais}_SELL"] = sell_price
            guardar_tasa(f"USDT en {pais} (venta)", sell_price)

    # === Cálculo de tasas ===
    for origen in paises:
        for destino in paises:
            if origen == destino:
                continue

            precio_origen = precios_usdt.get(origen)
            precio_destino = precios_usdt.get(f"{destino}_SELL")
            if not precio_origen or not precio_destino:
                print(f"❌ Faltan precios para {origen} - {destino}")
                continue

            base = f"{origen} - {destino}"
            decimales = 5 if origen == "Chile" and destino in ["Panamá", "Ecuador", "Europa", "Brasil"] else 4


            # === Cálculo de tasa base ===
            if base in ["Colombia - Venezuela", "Chile - USA"]:
                tasa_full = precio_origen / precio_destino
            else:
                tasa_full = precio_destino / precio_origen

            # === Márgenes personalizados ===
            margen = margenes_personalizados.get(base, {"publico": 0.07, "mayorista": 0.03})

            # Si el par está en la lista, sumamos margen; si no, restamos
            if base in pares_sumar_margen:
                tasa_publico = tasa_full * (1 + margen["publico"])
                tasa_mayorista = tasa_full * (1 + margen["mayorista"])
            else:
                tasa_publico = tasa_full * (1 - margen["publico"])
                tasa_mayorista = tasa_full * (1 - margen["mayorista"])

            # Guardar tasas
            guardar_tasa(f"Tasa full {base}", tasa_full, decimales)
            guardar_tasa(f"Tasa público {base}", tasa_publico, decimales)
            guardar_tasa(f"Tasa mayorista {base}", tasa_mayorista, decimales)

            # Promedios
            promedio_full = promedio_tasa(f"Tasa full {base}")
            promedio_pub = promedio_tasa(f"Tasa público {base}")
            promedio_may = promedio_tasa(f"Tasa mayorista {base}")

            if promedio_full:
                guardar_tasa(f"Tasa full promedio {base}", promedio_full, decimales)
            if promedio_pub:
                guardar_tasa(f"Tasa público promedio {base}", promedio_pub, decimales)
            if promedio_may:
                guardar_tasa(f"Tasa mayorista promedio {base}", promedio_may, decimales)

            print(f"✅ Tasas {base} actualizadas.")

    print("\n✅ Todas las tasas fueron actualizadas correctamente.")


if __name__ == "__main__":
    actualizar_todas_las_tasas()
