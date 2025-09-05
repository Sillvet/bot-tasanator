# -*- coding: utf-8 -*-
"""
Colector P2P (USDT) sin transAmount:
- 5ª oferta por banco específico en Colombia (Bancolombia), USA (Zelle), Europa (Bizum) – resto 1ª.
- Filtro de método robusto (local, alias + keywords), con fallback por payTypes de la API si no hay match.
- Sin BUY para BRL (sólo SELL).
- Imprime vendedor, métodos y URL de la oferta/perfil de vendedor.
- Guarda USDT por país (BUY/SELL) y tasas por par (full/público/mayorista + promedios).

Requisitos:
  pip install requests
  y tu wrapper supabase_client.py con el objeto `supabase`.
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests
from supabase_client import supabase  # tu wrapper

# =========================
# Configuración
# =========================
ASSET = "USDT"
BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; P2PBot/1.0)"
}

ROWS_PER_PAGE = 20
MAX_PAGES_DEFAULT = 3
MAX_PAGES_USD = 12   # subimos un poco para Zelle
SLEEP_BETWEEN_CALLS = 0.12

# mercados: BUY (sin BRL) / SELL (incluye BRL)
MARKETS_BUY = [
    {"fiat": "VES", "countries": ["VE"], "label": "Venezuela", "method": None},
    {"fiat": "COP", "countries": ["CO"], "label": "Colombia",  "method": "BANCOLOMBIA"},  # 5ª
    {"fiat": "ARS", "countries": ["AR"], "label": "Argentina", "method": None},
    {"fiat": "PEN", "countries": ["PE"], "label": "Perú",      "method": None},
    # Europa: Bizum -> España
    {"fiat": "EUR", "countries": ["ES"], "label": "Europa",    "method": "BIZUM"},        # 5ª
    # USA: Zelle -> Estados Unidos
    {"fiat": "USD", "countries": ["US"], "label": "USA",       "method": "ZELLE"},        # 5ª
    {"fiat": "MXN", "countries": ["MX"], "label": "México",    "method": None},
    {"fiat": "USD", "countries": ["PA"], "label": "Panamá",    "method": None},
    {"fiat": "USD", "countries": ["EC"], "label": "Ecuador",   "method": None},
    {"fiat": "CLP", "countries": ["CL"], "label": "Chile",     "method": None},
]
MARKETS_SELL = [
    {"fiat": "VES", "countries": ["VE"], "label": "Venezuela", "method": None},
    {"fiat": "COP", "countries": ["CO"], "label": "Colombia",  "method": "BANCOLOMBIA"},  # 5ª
    {"fiat": "ARS", "countries": ["AR"], "label": "Argentina", "method": None},
    {"fiat": "PEN", "countries": ["PE"], "label": "Perú",      "method": None},
    {"fiat": "BRL", "countries": ["BR"], "label": "Brasil",    "method": None},  # sólo SELL
    {"fiat": "EUR", "countries": ["ES"], "label": "Europa",    "method": "BIZUM"},        # 5ª
    {"fiat": "USD", "countries": ["US"], "label": "USA",       "method": "ZELLE"},        # 5ª
    {"fiat": "MXN", "countries": ["MX"], "label": "México",    "method": None},
    {"fiat": "USD", "countries": ["PA"], "label": "Panamá",    "method": None},
    {"fiat": "USD", "countries": ["EC"], "label": "Ecuador",   "method": None},
    {"fiat": "CLP", "countries": ["CL"], "label": "Chile",     "method": None},
]

# plazas donde tomamos la 5ª oferta
NTH_BY_MARKET = {"Colombia": 5, "USA": 5, "Europa": 5}
DEFAULT_NTH = 1

# Filtro robusto por método (local)
PAYMENT_METHODS = {
    "ZELLE": {
        "aliases": {"zelle", "zelle (bank transfer)", "zelle transfer"},
        "keywords": {"zelle", "transferencia con zelle", "zelle transferencia"},
        "allow_banktransfer_keyword": True
    },
    "BIZUM": {
        "aliases": {"bizum"},
        "keywords": {"bizum", "bizzum", "bizaum"},
        "allow_banktransfer_keyword": True
    },
    "BANCOLOMBIA": {
        "aliases": {"bancolombia", "bancolombia s.a", "bancolombiasa", "bancolombia s.a."},
        "keywords": {"bancolombia"},
        "allow_banktransfer_keyword": True
    }
}

# Fallback por payTypes (API) si el filtro local no encuentra nada
PAYTYPES_API = {
    "ZELLE": ["Zelle"],
    "BIZUM": ["Bizum"],
    "BANCOLOMBIA": ["BancolombiaSA", "Bancolombia S.A", "Bancolombia"]
}

# márgenes por par y pares que “suman” margen
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
# Utils filtro local + URLs
# =========================
def extract_methods_lower(adv: Dict) -> set:
    out = set()
    for tm in adv.get("tradeMethods", []) or []:
        for k in ("tradeMethodName", "tradeMethodShortName", "identifier"):
            v = tm.get(k)
            if v:
                out.add(str(v).casefold())
    return out

def blobs_for_keywords(adv: Dict, advertiser: Dict) -> str:
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
    # 1) match directo en nombres de método
    if methods & cfg["aliases"]:
        return True
    # 2) keywords en remarks + permitir "bank transfer" como comodín si así se configuró
    blob = blobs_for_keywords(adv, advertiser)
    if any(kw in blob for kw in cfg["keywords"]):
        if cfg.get("allow_banktransfer_keyword", False):
            if "bank transfer" in methods or "transferencia bancaria" in methods:
                return True
        return True
    return False

def build_ad_urls(adv: Dict, advertiser: Dict, locale: str = "es") -> Tuple[Optional[str], Optional[str]]:
    """
    Construye URL de la oferta y del perfil del vendedor, si hay identificadores.
    En Binance suelen venir:
      - adv['advNo'] (o 'advertiseNo') para la oferta
      - advertiser['advertiserNo'] o advertiser['userNo'] para el perfil
    """
    adv_no = adv.get("advNo") or adv.get("advertiseNo") or adv.get("advNoString") or adv.get("advSeqNo")
    advertiser_no = advertiser.get("advertiserNo") or advertiser.get("userNo") or advertiser.get("userNoString")

    ad_url = f"https://p2p.binance.com/{locale}/advertiseDetail?advertiseNo={adv_no}" if adv_no else None
    profile_url = f"https://p2p.binance.com/{locale}/advertiserDetail?advertiserNo={advertiser_no}" if advertiser_no else None
    return ad_url, profile_url

def adv_to_info(adv: Dict, advertiser: Dict) -> Dict:
    price = float(adv["price"])
    seller = advertiser.get("nickName") or advertiser.get("nick_name") or "N/A"
    methods = []
    seen = set()
    for tm in (adv.get("tradeMethods") or []):
        name = tm.get("tradeMethodShortName") or tm.get("identifier") or tm.get("tradeMethodName")
        if name and name not in seen:
            seen.add(name)
            methods.append(str(name))
    ad_url, profile_url = build_ad_urls(adv, advertiser, locale="es")
    return {
        "price": price,
        "seller": seller,
        "methods": methods,
        "ad_url": ad_url,
        "profile_url": profile_url,
    }

# =========================
# Cliente Binance con paginación
# =========================
_cache_lock = threading.Lock()
_page_cache: Dict[str, List[Dict]] = {}

def _ckey(fiat: str, side: str, page: int, rows: int, countries: Optional[List[str]], paytypes_key: str = ""):
    c = "GLOBAL" if not countries else ",".join(sorted(countries))
    return f"{fiat}|{side}|p{page}|r{rows}|{c}|{paytypes_key}"

def fetch_page(fiat: str, side: str, page: int, rows: int, countries: Optional[List[str]]) -> List[Dict]:
    key = _ckey(fiat, side, page, rows, countries)
    with _cache_lock:
        if key in _page_cache:
            return _page_cache[key]
    payload = {
        "page": page,
        "rows": rows,
        "asset": ASSET,
        "tradeType": side.upper(),
        "fiat": fiat,
        "publisherType": None,
        "payTypes": [],            # importante: sin filtrar en API (filtro local)
        "countries": countries or []
    }
    try:
        r = requests.post(BINANCE_P2P_URL, headers=HEADERS, data=json.dumps(payload), timeout=15)
        data = r.json()
        items = data.get("data") or []
    except Exception:
        items = []
    with _cache_lock:
        _page_cache[key] = items
    return items

def fetch_page_with_paytypes(fiat: str, side: str, page: int, rows: int, countries: Optional[List[str]], pay_types: List[str]) -> List[Dict]:
    key = _ckey(fiat, side, page, rows, countries, paytypes_key="|".join(sorted(pay_types)))
    with _cache_lock:
        if key in _page_cache:
            return _page_cache[key]
    payload = {
        "page": page,
        "rows": rows,
        "asset": ASSET,
        "tradeType": side.upper(),
        "fiat": fiat,
        "publisherType": None,
        "payTypes": pay_types,     # fallback: filtramos en API por método exacto
        "countries": countries or []
    }
    try:
        r = requests.post(BINANCE_P2P_URL, headers=HEADERS, data=json.dumps(payload), timeout=15)
        data = r.json()
        items = data.get("data") or []
    except Exception:
        items = []
    with _cache_lock:
        _page_cache[key] = items
    return items

def find_nth_offer_without_amount(
    fiat: str, side: str,
    countries: Optional[List[str]],
    method_key: Optional[str],
    nth: int
) -> Optional[Dict]:
    """Recorre páginas, filtra por método (local) y devuelve la n-ésima más barata con URLs."""
    max_pages = MAX_PAGES_USD if (fiat == "USD" and method_key == "ZELLE") else MAX_PAGES_DEFAULT
    filtered: List[Dict] = []

    for page in range(1, max_pages + 1):
        items = fetch_page(fiat, side, page, ROWS_PER_PAGE, countries)
        for it in items:
            adv = it.get("adv") or {}
            advertiser = it.get("advertiser") or {}
            if not method_filter_ok(adv, advertiser, method_key):
                continue
            try:
                info = adv_to_info(adv, advertiser)
            except Exception:
                continue
            filtered.append(info)

        print(f"ℹ️ {fiat} {side}: [countries={countries if countries else 'GLOBAL'}; page={page}] => filtrados={len(filtered)} (api={len(items)}).")

        if len(filtered) >= nth:
            break
        if not items:
            break
        time.sleep(SLEEP_BETWEEN_CALLS)

    if not filtered:
        return None

    filtered.sort(key=lambda x: x["price"])  # ascendente (más barata primero)
    idx = min(max(nth - 1, 0), len(filtered) - 1)
    return filtered[idx]

def find_nth_offer_with_fallback(
    fiat: str, side: str,
    countries: Optional[List[str]],
    method_key: Optional[str],
    nth: int
) -> Optional[Dict]:
    """
    Intenta primero filtro local (alias/keywords).
    Si no encuentra nada y existe mapping payTypes API para el método, hace un intento extra filtrando en API.
    """
    res = find_nth_offer_without_amount(fiat, side, countries, method_key, nth)
    if res is not None:
        return res

    # fallback por payTypes API
    if method_key and method_key in PAYTYPES_API:
        paytypes = PAYTYPES_API[method_key]
        filtered: List[Dict] = []
        max_pages = MAX_PAGES_USD if (fiat == "USD" and method_key == "ZELLE") else MAX_PAGES_DEFAULT

        for page in range(1, max_pages + 1):
            items = fetch_page_with_paytypes(fiat, side, page, ROWS_PER_PAGE, countries, paytypes)
            for it in items:
                adv = it.get("adv") or {}
                advertiser = it.get("advertiser") or {}
                try:
                    info = adv_to_info(adv, advertiser)
                except Exception:
                    continue
                filtered.append(info)

            print(f"ℹ️ {fiat} {side} [fallback payTypes={paytypes}]: [countries={countries if countries else 'GLOBAL'}; page={page}] => filtrados={len(filtered)} (api={len(items)}).")

            if len(filtered) >= nth:
                break
            if not items:
                break
            time.sleep(SLEEP_BETWEEN_CALLS)

        if filtered:
            filtered.sort(key=lambda x: x["price"])
            idx = min(max(nth - 1, 0), len(filtered) - 1)
            return filtered[idx]

    print(f"⚠️ Sin ofertas tras filtrar método para {fiat} {side}.")
    return None

# =========================
# Supabase helpers
# =========================
def guardar_tasa(nombre: str, valor: float, decimales: int = 4):
    try:
        fecha_ve = datetime.utcnow() - timedelta(hours=4)
        res = supabase.table("tasas").insert({
            "nombre_tasa": nombre,
            "valor": round(float(valor), decimales),
            "fecha_actual": fecha_ve.isoformat()
        }).execute()
        if not getattr(res, "data", None):
            print(f"❌ No se guardó {nombre}. Respuesta vacía.")
        else:
            print(f"✅ Tasa guardada: {nombre} = {round(float(valor), decimales)}")
    except Exception as e:
        print(f"❌ Excepción al guardar {nombre}: {e}")

def promedio_tasa(nombre: str) -> Optional[float]:
    try:
        resp = supabase.table("tasas").select("valor")\
                       .eq("nombre_tasa", nombre)\
                       .order("fecha_actual", desc=True).limit(2).execute()
        vals = [Decimal(r["valor"]) for r in (resp.data or [])]
        if len(vals) == 2:
            return float((vals[0] + vals[1]) / 2)
    except Exception as e:
        print(f"⚠️ promedio_tasa error para {nombre}: {e}")
    return None

# =========================
# Proceso principal
# =========================
def actualizar_todas_las_tasas():
    url = os.getenv("SUPABASE_URL")
    if url:
        print(f"Conectado a: {url}")

    print("\n🔁 Ejecutando actualización de tasas...")

    paises_buy = [m["label"] for m in MARKETS_BUY]
    paises_sell = [m["label"] for m in MARKETS_SELL]

    precios_buy: Dict[str, float] = {}
    precios_sell: Dict[str, float] = {}

    # BUY
    for cfg in MARKETS_BUY:
        fiat = cfg["fiat"]
        label = cfg["label"]
        countries = cfg.get("countries")
        method_key = cfg.get("method")
        nth = NTH_BY_MARKET.get(label, DEFAULT_NTH)

        res = find_nth_offer_with_fallback(fiat, "BUY", countries, method_key, nth)
        if not res:
            print(f"⚠️ Sin ofertas para {label} BUY.")
            continue
        price = res["price"]
        seller = res["seller"]
        methods = res["methods"]
        ad_url = res["ad_url"]
        profile_url = res["profile_url"]

        print(f"👤 Oferta usada {label} BUY → vendedor: {seller} | métodos: {', '.join(methods)} | precio: {price}")
        if ad_url:
            print(f"   🔗 Oferta: {ad_url}")
        if profile_url:
            print(f"   🧑 Perfil: {profile_url}")

        precios_buy[label] = price
        guardar_tasa(f"USDT en {label}", price)

    # SELL
    for cfg in MARKETS_SELL:
        fiat = cfg["fiat"]
        label = cfg["label"]
        countries = cfg.get("countries")
        method_key = cfg.get("method")
        nth = NTH_BY_MARKET.get(label, DEFAULT_NTH)

        res = find_nth_offer_with_fallback(fiat, "SELL", countries, method_key, nth)
        if not res:
            print(f"⚠️ Sin ofertas para {label} SELL.")
            continue
        price = res["price"]
        seller = res["seller"]
        methods = res["methods"]
        ad_url = res["ad_url"]
        profile_url = res["profile_url"]

        print(f"👤 Oferta usada {label} SELL → comprador: {seller} | métodos: {', '.join(methods)} | precio: {price}")
        if ad_url:
            print(f"   🔗 Oferta: {ad_url}")
        if profile_url:
            print(f"   🧑 Perfil: {profile_url}")

        precios_sell[label] = price
        guardar_tasa(f"USDT en {label} (venta)", price)

    # Tasas por par (full/público/mayorista + promedios)
    for origen in paises_buy:
        if origen not in precios_buy:
            continue
        for destino in paises_sell:
            if destino == origen or destino not in precios_sell:
                continue

            base = f"{origen} - {destino}"
            p_origen = precios_buy[origen]      # BUY origen
            p_destino = precios_sell[destino]   # SELL destino

            decimales = 5 if (origen == "Chile" and destino in ["Panamá", "Ecuador", "Europa", "Brasil"]) else 4

            # full
            if base in pares_sumar_margen:
                tasa_full = p_origen / p_destino
            else:
                tasa_full = p_destino / p_origen

            margen = margenes_personalizados.get(base, {"publico": 0.07, "mayorista": 0.03})
            if base in pares_sumar_margen:
                tasa_publico = tasa_full * (1 + margen["publico"])
                tasa_mayorista = tasa_full * (1 + margen["mayorista"])
            else:
                tasa_publico = tasa_full * (1 - margen["publico"])
                tasa_mayorista = tasa_full * (1 - margen["mayorista"])

            guardar_tasa(f"Tasa full {base}", tasa_full, decimales)
            guardar_tasa(f"Tasa público {base}", tasa_publico, decimales)
            guardar_tasa(f"Tasa mayorista {base}", tasa_mayorista, decimales)

            # promedios (media móvil de 2)
            pf = promedio_tasa(f"Tasa full {base}")
            pp = promedio_tasa(f"Tasa público {base}")
            pm = promedio_tasa(f"Tasa mayorista {base}")
            if pf is not None:
                guardar_tasa(f"Tasa full promedio {base}", pf, decimales)
            if pp is not None:
                guardar_tasa(f"Tasa público promedio {base}", pp, decimales)
            if pm is not None:
                guardar_tasa(f"Tasa mayorista promedio {base}", pm, decimales)

            print(f"✅ Tasas {base} actualizadas.")

    print("\n✅ Todas las tasas fueron actualizadas correctamente.")

# =========================
# Main
# =========================
if __name__ == "__main__":
    actualizar_todas_las_tasas()
