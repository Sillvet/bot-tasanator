# -*- coding: utf-8 -*-
"""
Colector P2P (USDT) rápido con filtros robustos (Zelle/Bizum), buckets por país
y cálculo de tasas por par (full/público/mayorista + promedios). Incluye
Supabase por módulo propio (supabase_client) o por variables de entorno.

Tablas:
- tasas_p2p: guarda buckets por país (FULL/PUBLICO/MAYORISTA/PROMEDIO)
- tasas: guarda "Tasa full {A - B}", "Tasa público {A - B}", "Tasa mayorista {A - B}"
         y sus "promedio" correspondientes, con fecha_actual en horario Venezuela (UTC-4)

ENV opcionales:
  SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE_P2P (default: tasas_p2p), SUPABASE_TABLE_PARES (default: tasas)
"""

import os
import time
import json
import statistics
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

# =========================
# Configuración general
# =========================
ASSET = "USDT"
BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; P2PBot/1.0)"
}

# Límites/estrategia scraping
ROWS_PER_PAGE = 20
MAX_PAGES_DEFAULT = 3          # mayoría de mercados
MAX_PAGES_USD = 10             # USD (Zelle) suele requerir más páginas
EARLY_STOP_K = 20              # parar al lograr K ofertas filtradas
SLEEP_BETWEEN_CALLS = 0.15     # segundos entre requests (ajusta si te rate-limita)

# Buckets por monto
PUBLIC_AMOUNT = 200            # ~retail
WHOLESALE_AMOUNT = 3000        # ~mayorista
TOP_N_FOR_MEDIAN = 8           # cuántas ofertas para la mediana (si hay menos, usa todas)

# Tablas Supabase
SUPABASE_TABLE_P2P = os.getenv("SUPABASE_TABLE_P2P", "tasas_p2p")
SUPABASE_TABLE_PARES = os.getenv("SUPABASE_TABLE_PARES", "tasas")

# Mercados: BUY (sin BRL) y SELL (incluye BRL)
MARKETS_BUY = [
    {"fiat": "VES", "countries": ["VE"], "method": None, "label": "Venezuela"},
    {"fiat": "COP", "countries": ["CO"], "method": None, "label": "Colombia"},
    {"fiat": "ARS", "countries": ["AR"], "method": None, "label": "Argentina"},
    {"fiat": "PEN", "countries": ["PE"], "method": None, "label": "Perú"},
    # EUR BUY con Bizum (Europa)
    {"fiat": "EUR", "countries": ["ES", "PT", "FR", "DE", "IT"], "method": "BIZUM", "label": "Europa"},
    # USD BUY con Zelle (GLOBAL)
    {"fiat": "USD", "countries": None, "method": "ZELLE", "label": "USA"},
    # USD BUY Ecuador (sin método, sólo país EC)
    {"fiat": "USD", "countries": ["EC"], "method": None, "label": "Ecuador"},
    {"fiat": "MXN", "countries": ["MX"], "method": None, "label": "México"},
    {"fiat": "PAB", "countries": ["PA"], "method": None, "label": "Panamá"},
    {"fiat": "CLP", "countries": ["CL"], "method": None, "label": "Chile"},
]
MARKETS_SELL = [
    {"fiat": "VES", "countries": ["VE"], "method": None, "label": "Venezuela"},
    {"fiat": "COP", "countries": ["CO"], "method": None, "label": "Colombia"},
    {"fiat": "ARS", "countries": ["AR"], "method": None, "label": "Argentina"},
    {"fiat": "PEN", "countries": ["PE"], "method": None, "label": "Perú"},
    {"fiat": "EUR", "countries": ["ES", "PT", "FR", "DE", "IT"], "method": None, "label": "Europa"},
    # USD SELL con Zelle (GLOBAL)
    {"fiat": "USD", "countries": None, "method": "ZELLE", "label": "USA"},
    # Solo SELL para BRL (Pix, sin BUY)
    {"fiat": "BRL", "countries": ["BR"], "method": None, "label": "Brasil"},
]

# Método→alias/keywords para filtros robustos
PAYMENT_METHODS = {
    "ZELLE": {
        "aliases": {"zelle", "zelle transfer", "zelle (bank transfer)"},
        "keywords": {"zelle"},
        "allow_banktransfer_keyword": True
    },
    "BIZUM": {
        "aliases": {"bizum"},
        "keywords": {"bizum", "bizaum", "bizzum"},
        "allow_banktransfer_keyword": True
    }
}

# =========================
# Márgenes por par (como tu original)
# =========================
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
pares_sumar_margen = ["Chile - USA", "Colombia - Venezuela"]

# =========================
# Utilidades
# =========================
def parse_float(s: Any) -> Optional[float]:
    try:
        if s is None:
            return None
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None

def get_adv_limits(adv: Dict) -> Tuple[Optional[float], Optional[float]]:
    min_s = adv.get("minSingleTransAmount") or adv.get("minSingleTransAmountString")
    max_s = adv.get("maxSingleTransAmount") or adv.get("maxSingleTransAmountString")
    return parse_float(min_s), parse_float(max_s)

def limit_allows_amount(adv: Dict, amount: Optional[float]) -> bool:
    if amount is None:
        return True
    mn, mx = get_adv_limits(adv)
    if mn is not None and amount < mn:
        return False
    if mx is not None and amount > mx:
        return False
    return True

def extract_methods_lower(adv: Dict) -> set:
    out = set()
    for tm in adv.get("tradeMethods", []) or []:
        for key in ("tradeMethodName", "tradeMethodShortName", "identifier"):
            val = tm.get(key)
            if val:
                out.add(str(val).casefold())
    return out

def text_blobs_for_keywords(adv: Dict, advertiser: Dict) -> str:
    parts = []
    for k in ("advRemark", "remark", "buyerRemarks", "sellerRemarks", "tradeTips"):
        v = adv.get(k)
        if v:
            parts.append(str(v))
    for k in ("userRemark", "remark", "introduce", "desc"):
        v = advertiser.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts).casefold()

def method_filter_ok(adv: Dict, advertiser: Dict, method_key: Optional[str]) -> bool:
    if method_key is None:
        return True
    cfg = PAYMENT_METHODS.get(method_key)
    if not cfg:
        return True
    methods = extract_methods_lower(adv)
    if methods & cfg["aliases"]:
        return True
    blobs = text_blobs_for_keywords(adv, advertiser)
    if any(kw in blobs for kw in cfg["keywords"]):
        if cfg.get("allow_banktransfer_keyword", False):
            if "bank transfer" in methods or "transferencia bancaria" in methods:
                return True
        return True
    return False

def adv_to_offer(adv: Dict, advertiser: Dict) -> Optional[Dict]:
    price = parse_float(adv.get("price"))
    if price is None:
        return None
    methods = sorted({m.title() for m in extract_methods_lower(adv)})
    mn, mx = get_adv_limits(adv)
    return {
        "price": price,
        "seller": advertiser.get("nickName") or advertiser.get("nick_name") or "N/A",
        "methods": methods,
        "min": mn,
        "max": mx,
        "raw_adv": adv,
        "raw_advertiser": advertiser,
    }

def median_price_and_rep(offers: List[Dict], trade_type: str, top_n: int = TOP_N_FOR_MEDIAN) -> Optional[Tuple[float, Dict]]:
    if not offers:
        return None
    reverse = (trade_type == "SELL")  # BUY: asc; SELL: desc
    ordered = sorted(offers, key=lambda x: x["price"], reverse=reverse)
    subset = ordered[: min(len(ordered), max(1, top_n))]
    prices = [o["price"] for o in subset]
    try:
        med = statistics.median(prices)
    except statistics.StatisticsError:
        med = prices[0]
    rep_idx = len(subset) // 2
    rep_offer = subset[rep_idx]
    return med, rep_offer

def best_full_offer(offers: List[Dict], trade_type: str, eligible_min: float = PUBLIC_AMOUNT) -> Optional[Dict]:
    if not offers:
        return None
    eligible = [o for o in offers if (o["min"] is None or o["min"] <= eligible_min)]
    if not eligible:
        eligible = offers
    return (min if trade_type == "BUY" else max)(eligible, key=lambda x: x["price"])

# =========================
# Cliente Binance P2P (con caché)
# =========================
_page_cache_lock = threading.Lock()
_page_cache: Dict[str, List[Dict]] = {}

def cache_key(fiat: str, trade_type: str, page: int, rows: int, countries: Optional[List[str]]) -> str:
    ckey = "GLOBAL" if not countries else ",".join(sorted(countries))
    return f"{fiat}|{trade_type}|p{page}|r{rows}|{ckey}"

def fetch_binance_page(fiat: str, trade_type: str, page: int, rows: int = ROWS_PER_PAGE, countries: Optional[List[str]] = None) -> List[Dict]:
    key = cache_key(fiat, trade_type, page, rows, countries)
    with _page_cache_lock:
        if key in _page_cache:
            return _page_cache[key]
    payload = {
        "page": page,
        "rows": rows,
        "asset": ASSET,
        "tradeType": trade_type,
        "fiat": fiat,
        "publisherType": None,
        "payTypes": [],
        "countries": countries or []
    }
    try:
        resp = requests.post(BINANCE_P2P_URL, headers=HEADERS, data=json.dumps(payload), timeout=15)
        data = resp.json()
        items = data.get("data") or []
        with _page_cache_lock:
            _page_cache[key] = items
        return items
    except Exception:
        with _page_cache_lock:
            _page_cache[key] = []
        return []

def collect_offers(fiat: str, trade_type: str, countries: Optional[List[str]], method_key: Optional[str], amount: Optional[float], max_pages: int) -> List[Dict]:
    total_filtered: List[Dict] = []
    for page in range(1, max_pages + 1):
        items = fetch_binance_page(fiat, trade_type, page, ROWS_PER_PAGE, countries)
        for it in items:
            adv = it.get("adv") or {}
            advertiser = it.get("advertiser") or {}
            if not limit_allows_amount(adv, amount):
                continue
            if not method_filter_ok(adv, advertiser, method_key):
                continue
            offer = adv_to_offer(adv, advertiser)
            if not offer:
                continue
            total_filtered.append(offer)
            if len(total_filtered) >= EARLY_STOP_K:
                break
        print(f"ℹ️ {fiat} {trade_type}: [countries={countries if countries else 'GLOBAL'}; page={page}; rows={ROWS_PER_PAGE}; amount={amount}] => {len(total_filtered)} filtrados (api={len(items)}).")
        if len(total_filtered) >= EARLY_STOP_K:
            break
        if not items:
            break
        time.sleep(SLEEP_BETWEEN_CALLS)
    return total_filtered

# =========================
# Supabase (doble vía)
# =========================
SUPABASE_CLIENT = None  # supabase_client.supabase (tu wrapper)
try:
    from supabase_client import supabase as SUPABASE_CLIENT  # type: ignore
except Exception:
    pass

def supabase_env_client_or_none():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client  # type: ignore
        return create_client(url, key)
    except Exception:
        return None

SUPA_ENV = supabase_env_client_or_none()

if SUPABASE_CLIENT:
    print("Conectado a Supabase (módulo supabase_client).")
elif SUPA_ENV:
    print(f"Conectado a: {os.getenv('SUPABASE_URL')}")
else:
    print("Supabase no configurado (se guardará de forma simulada).")

# ---- Guardado buckets (tabla tasas_p2p) ----
def save_bucket_rate(label: str, fiat: str, side: str, bucket: str, value: float, rep_seller: str, rep_methods: List[str]):
    row = {
        "fecha_utc": datetime.utcnow().isoformat(),
        "pais_label": label,
        "fiat": fiat,
        "lado": side,            # BUY / SELL
        "bucket": bucket,        # FULL / PUBLICO / MAYORISTA / PROMEDIO
        "tasa": value,
        "seller": rep_seller,
        "methods": ", ".join(rep_methods),
        "asset": ASSET
    }
    if SUPABASE_CLIENT:
        try:
            SUPABASE_CLIENT.table(SUPABASE_TABLE_P2P).insert(row).execute()
            print(f"✅ [{label} {side} {bucket}] guardado en {SUPABASE_TABLE_P2P}: {value}")
        except Exception as e:
            print(f"⚠️ Error guardando bucket ({label} {side} {bucket}) en {SUPABASE_TABLE_P2P}: {e}")
    elif SUPA_ENV:
        try:
            SUPA_ENV.table(SUPABASE_TABLE_P2P).insert(row).execute()
            print(f"✅ [{label} {side} {bucket}] guardado en {SUPABASE_TABLE_P2P}: {value}")
        except Exception as e:
            print(f"⚠️ Error guardando bucket ({label} {side} {bucket}) en {SUPABASE_TABLE_P2P}: {e}")
    else:
        print(f"✅ (simulada) [{label} {side} {bucket}] {value} | seller={rep_seller} | métodos={row['methods']}")

# ---- Guardado por par (tabla tasas) ----
def save_named_rate(nombre: str, valor: float, decimales: int = 4):
    payload = {
        "nombre_tasa": nombre,
        "valor": round(float(valor), decimales),
        # fecha en horario Venezuela (UTC-4)
        "fecha_actual": (datetime.utcnow() - timedelta(hours=4)).isoformat()
    }
    if SUPABASE_CLIENT:
        try:
            res = SUPABASE_CLIENT.table(SUPABASE_TABLE_PARES).insert(payload).execute()
            if not getattr(res, "data", None):
                print(f"❌ No se guardó {nombre}. Respuesta vacía.")
            else:
                print(f"✅ Tasa guardada: {nombre} = {payload['valor']}")
        except Exception as e:
            print(f"❌ Excepción al guardar {nombre}: {e}")
    elif SUPA_ENV:
        try:
            res = SUPA_ENV.table(SUPABASE_TABLE_PARES).insert(payload).execute()
            if not getattr(res, "data", None):
                print(f"❌ No se guardó {nombre}. Respuesta vacía.")
            else:
                print(f"✅ Tasa guardada: {nombre} = {payload['valor']}")
        except Exception as e:
            print(f"❌ Excepción al guardar {nombre}: {e}")
    else:
        print(f"✅ (simulada) {nombre} = {payload['valor']}")

def promedio_tasa(nombre: str) -> Optional[float]:
    try:
        if SUPABASE_CLIENT:
            resp = SUPABASE_CLIENT.table(SUPABASE_TABLE_PARES).select("valor,fecha_actual").eq("nombre_tasa", nombre).order("fecha_actual", desc=True).limit(2).execute()
            rows = getattr(resp, "data", []) or []
        elif SUPA_ENV:
            resp = SUPA_ENV.table(SUPABASE_TABLE_PARES).select("valor,fecha_actual").eq("nombre_tasa", nombre).order("fecha_actual", desc=True).limit(2).execute()
            rows = getattr(resp, "data", []) or []
        else:
            return None
        vals = [float(r["valor"]) for r in rows]
        if len(vals) == 2:
            return (vals[0] + vals[1]) / 2.0
    except Exception as e:
        print(f"⚠️ promedio_tasa error para {nombre}: {e}")
    return None

# =========================
# Orquestación por mercado
# =========================
def process_market(label: str, fiat: str, trade_type: str, countries: Optional[List[str]], method_key: Optional[str]) -> Optional[Dict]:
    max_pages = MAX_PAGES_USD if (fiat == "USD" and method_key == "ZELLE") else MAX_PAGES_DEFAULT

    # 1) Ofertas (sin amount) -> conjuntos por bucket
    offers_any = collect_offers(fiat, trade_type, countries, method_key, amount=None, max_pages=max_pages)
    if not offers_any:
        print(f"⚠️ {trade_type} sin precio final para {label}.")
        return None

    offers_public = [o for o in offers_any if limit_allows_amount(o["raw_adv"], PUBLIC_AMOUNT)]
    offers_wholesale = [o for o in offers_any if limit_allows_amount(o["raw_adv"], WHOLESALE_AMOUNT)]

    # FULL
    full_offer = best_full_offer(offers_any, trade_type, eligible_min=PUBLIC_AMOUNT)
    if full_offer:
        print(f"👤 {label} {trade_type} [FULL] → vendedor: {full_offer['seller']} | métodos: {', '.join(full_offer['methods'])} | precio: {full_offer['price']}")
        save_bucket_rate(label, fiat, trade_type, "FULL", full_offer["price"], full_offer["seller"], full_offer["methods"])

    # PÚBLICO
    public_med = median_price_and_rep(offers_public, trade_type, TOP_N_FOR_MEDIAN)
    selected_price = None
    selected_rep = None
    if public_med:
        med_val, rep = public_med
        print(f"👤 {label} {trade_type} [PÚBLICO] → vendedor: {rep['seller']} | métodos: {', '.join(rep['methods'])} | mediana: {med_val}")
        save_bucket_rate(label, fiat, trade_type, "PUBLICO", med_val, rep["seller"], rep["methods"])
        selected_price = med_val
        selected_rep = rep

    # MAYORISTA
    wholesale_med = median_price_and_rep(offers_wholesale, trade_type, TOP_N_FOR_MEDIAN)
    if wholesale_med:
        med_val, rep = wholesale_med
        print(f"👤 {label} {trade_type} [MAYORISTA] → vendedor: {rep['seller']} | métodos: {', '.join(rep['methods'])} | mediana: {med_val}")
        save_bucket_rate(label, fiat, trade_type, "MAYORISTA", med_val, rep["seller"], rep["methods"])

    # PROMEDIO (sin amount)
    any_med = median_price_and_rep(offers_any, trade_type, TOP_N_FOR_MEDIAN)
    if any_med:
        med_val, rep = any_med
        print(f"👤 {label} {trade_type} [PROMEDIO] → vendedor: {rep['seller']} | métodos: {', '.join(rep['methods'])} | mediana: {med_val}")
        save_bucket_rate(label, fiat, trade_type, "PROMEDIO", med_val, rep["seller"], rep["methods"])
        if selected_price is None:
            selected_price = med_val
            selected_rep = rep

    # Si no hubo PÚBLICO ni PROMEDIO, usa FULL para pares
    if selected_price is None and full_offer:
        selected_price = full_offer["price"]
        selected_rep = full_offer

    # Devuelve precio representativo para cálculo por pares
    if selected_price is not None and selected_rep is not None:
        print(f"➡️  Selección para pares {label} {trade_type}: {selected_price} (seller: {selected_rep['seller']})")
        return {
            "price": float(selected_price),
            "seller": selected_rep["seller"],
            "methods": selected_rep["methods"],
            "fiat": fiat
        }
    return None

def calcular_tasas_por_pares(precios_buy: Dict[str, Dict], precios_sell: Dict[str, Dict]):
    # Lista de países de interés (usa labels de MARKETS_*)
    labels_buy = [cfg["label"] for cfg in MARKETS_BUY]
    labels_sell = [cfg["label"] for cfg in MARKETS_SELL]

    for origen in labels_buy:
        if origen not in precios_buy:
            continue  # p.ej., Brasil no tiene BUY
        for destino in labels_sell:
            if origen == destino:
                continue
            if destino not in precios_sell:
                continue
            precio_origen = precios_buy[origen]["price"]       # BUY
            precio_destino = precios_sell[destino]["price"]    # SELL
            base = f"{origen} - {destino}"

            # Regla de decimales (como tu original)
            decimales = 5 if (origen == "Chile" and destino in ["Panamá", "Ecuador", "Europa", "Brasil"]) else 4

            # Tasa full (según pares_sumar_margen)
            if base in pares_sumar_margen:
                tasa_full = precio_origen / precio_destino
            else:
                tasa_full = precio_destino / precio_origen

            margen = margenes_personalizados.get(base, {"publico": 0.07, "mayorista": 0.03})

            if base in pares_sumar_margen:
                tasa_publico = tasa_full * (1 + margen["publico"])
                tasa_mayorista = tasa_full * (1 + margen["mayorista"])
            else:
                tasa_publico = tasa_full * (1 - margen["publico"])
                tasa_mayorista = tasa_full * (1 - margen["mayorista"])

            # Guardar
            save_named_rate(f"Tasa full {base}", tasa_full, decimales)
            save_named_rate(f"Tasa público {base}", tasa_publico, decimales)
            save_named_rate(f"Tasa mayorista {base}", tasa_mayorista, decimales)

            # Promedios (media móvil 2)
            prom_full = promedio_tasa(f"Tasa full {base}")
            prom_pub = promedio_tasa(f"Tasa público {base}")
            prom_may = promedio_tasa(f"Tasa mayorista {base}")

            if prom_full is not None:
                save_named_rate(f"Tasa full promedio {base}", prom_full, decimales)
            if prom_pub is not None:
                save_named_rate(f"Tasa público promedio {base}", prom_pub, decimales)
            if prom_may is not None:
                save_named_rate(f"Tasa mayorista promedio {base}", prom_may, decimales)

            print(f"✅ Tasas {base} actualizadas.")

def run_all():
    print("\n🔁 Ejecutando actualización de tasas...\n")

    precios_buy: Dict[str, Dict] = {}
    precios_sell: Dict[str, Dict] = {}

    # BUY (sin BRL)
    for cfg in MARKETS_BUY:
        fiat = cfg["fiat"]
        label = cfg["label"]
        countries = cfg.get("countries")
        method_key = cfg.get("method")
        print(f"— Procesando BUY {label} ({fiat}) —")
        res = process_market(label, fiat, "BUY", countries, method_key)
        if res:
            precios_buy[label] = res
        print()

    # SELL (incluye BRL)
    for cfg in MARKETS_SELL:
        fiat = cfg["fiat"]
        label = cfg["label"]
        countries = cfg.get("countries")
        method_key = cfg.get("method")
        print(f"— Procesando SELL {label} ({fiat}) —")
        res = process_market(label, fiat, "SELL", countries, method_key)
        if res:
            precios_sell[label] = res
        print()

    # Cálculo por pares (usa BUY origen y SELL destino)
    calcular_tasas_por_pares(precios_buy, precios_sell)

    print("\n✅ Proceso finalizado.")

# =========================
# Main
# =========================
if __name__ == "__main__":
    run_all()
