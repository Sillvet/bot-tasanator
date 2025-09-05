# -*- coding: utf-8 -*-
"""
Actualizador de tasas P2P con reglas especiales para:
- USD/Zelle (BUY con fallback a SELL como proxy)
- EUR/Bizum (prioritario)
- BRL BUY eliminado (solo SELL BRL)
Imprime el ofertante utilizado en cada tasa guardada.

Requisitos:
- requests
- (opcional) supabase: pip install supabase
- Variables de entorno para Supabase:
  SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE (p.ej. "tasas_p2p")
"""

import os
import sys
import time
import math
import json
import traceback
from typing import Any, Dict, List, Optional, Tuple

import requests

# =========================
# Configuración Supabase (opcional)
# =========================
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "tasas_p2p").strip()

_supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"Conectado a: {SUPABASE_URL}\n")
    except Exception as e:
        print("⚠️ No se pudo inicializar Supabase. Continuaré sin guardar en DB.")
        print("   Detalle:", str(e))
        _supabase = None
else:
    print("ℹ️ Supabase no configurado (sin SUPABASE_URL/KEY). Se omitirá guardado en DB.\n")

# =========================
# Utilidades de impresión
# =========================
def info(msg: str):
    print(f"ℹ️ {msg}")

def dbg(msg: str):
    print(f"🔎 {msg}")

def ok(msg: str):
    print(f"✅ {msg}")

def warn(msg: str):
    print(f"⚠️ {msg}")

def used_offer_line(side: str, fiat_label: str, actor_label: str, nickname: str, methods: List[str], price: float, extra: str = ""):
    methods_txt = ", ".join(methods) if methods else "-"
    if extra:
        print(f"👤 Oferta usada {fiat_label} {side} → {actor_label}: {nickname} | métodos: {methods_txt} | precio: {price} {extra}")
    else:
        print(f"👤 Oferta usada {fiat_label} {side} → {actor_label}: {nickname} | métodos: {methods_txt} | precio: {price}")

# =========================
# Cliente Binance P2P
# =========================
BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "User-Agent": "Mozilla/5.0 (compatible; RateBot/1.0)"
    }
    # Pequeño retraso para no saturar
    time.sleep(0.2)
    r = requests.post(BINANCE_P2P_URL, headers=headers, data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    return r.json()

def search_ads(
    fiat: str,
    trade_type: str,  # "BUY" o "SELL"
    page: int = 1,
    rows: int = 20,
    countries: Optional[List[str]] = None,
    amount: Optional[float] = None,
    pay_types: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Retorna (anuncios, total_en_api_sin_filtrar)
    """
    payload = {
        "asset": "USDT",
        "fiat": fiat,
        "tradeType": trade_type,       # BUY: tú compras USDT | SELL: tú vendes USDT
        "page": page,
        "rows": rows,
        "payTypes": pay_types or [],
        "publisherType": None,
        "classifies": ["mass"],        # suele funcionar mejor para inventario general
    }
    # Estos 2 campos pueden no estar soportados en todos los despliegues; se han usado con éxito antes
    if countries:
        payload["countries"] = countries
    if amount:
        payload["transAmount"] = str(amount)

    data = _post(payload)
    if not data or "data" not in data:
        return [], 0
    raw_ads = data.get("data") or []
    total = len(raw_ads)
    return raw_ads, total

# =========================
# Normalización / filtros
# =========================
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def extract_price(ad: Dict[str, Any]) -> Optional[float]:
    try:
        return float(ad["adv"]["price"])
    except Exception:
        return None

def extract_methods(ad: Dict[str, Any]) -> List[str]:
    methods = []
    try:
        for tm in (ad.get("adv", {}).get("tradeMethods") or []):
            # Nombre visible suele venir en 'tradeMethodName' y/o 'identifier'
            name = tm.get("tradeMethodName") or tm.get("identifier") or tm.get("payType") or ""
            if name:
                methods.append(name)
    except Exception:
        pass
    return methods

def extract_text_fields(ad: Dict[str, Any]) -> str:
    adv = ad.get("adv", {}) or {}
    advertiser = ad.get("advertiser", {}) or {}
    text_parts = [
        adv.get("remark") or "",
        adv.get("advRemark") or "",
        advertiser.get("userRemark") or "",
        advertiser.get("nickName") or "",
    ]
    return _norm(" | ".join([str(x) for x in text_parts if x]))

def extract_nickname(ad: Dict[str, Any]) -> str:
    advz = ad.get("advertiser", {}) or {}
    return advz.get("nickName") or advz.get("monthOrderCount") or "Desconocido"

def match_methods(
    ad: Dict[str, Any],
    alias_ok: List[str],
    keyword_ok: Optional[str] = None,
    allow_bank_transfer_if_keyword: bool = False
) -> bool:
    """
    Devuelve True si el anuncio cumple método por alias o keyword.
    Si allow_bank_transfer_if_keyword=True, acepta "Bank Transfer" sólo si aparece la keyword en el texto.
    """
    norm_alias = set(_norm(a) for a in alias_ok)
    methods = extract_methods(ad)
    methods_norm = set(_norm(m) for m in methods)
    text_blob = extract_text_fields(ad)

    # 1) alias directo
    if any(m in norm_alias for m in methods_norm):
        return True

    # 2) keyword en texto
    if keyword_ok:
        kw = _norm(keyword_ok)
        if kw and kw in text_blob:
            return True

    # 3) Bank Transfer + keyword (para Zelle mal-etiquetada)
    if allow_bank_transfer_if_keyword and ("bank transfer" in methods_norm):
        if keyword_ok and _norm(keyword_ok) in text_blob:
            return True

    return False

def pick_best(ads: List[Dict[str, Any]], trade_type: str) -> Optional[Dict[str, Any]]:
    # BUY: se busca menor precio | SELL: mayor precio
    ads_with_price = [(extract_price(x), x) for x in ads if extract_price(x) is not None]
    if not ads_with_price:
        return None
    if trade_type.upper() == "BUY":
        ads_with_price.sort(key=lambda t: t[0])  # menor primero
    else:
        ads_with_price.sort(key=lambda t: t[0], reverse=True)  # mayor primero
    return ads_with_price[0][1]

# =========================
# Búsquedas de alto nivel
# =========================
def find_rate_generic(
    fiat: str, trade_type: str, countries: Optional[List[str]],
    label: str, pages: int = 8, rows: int = 20,
    amounts: Optional[List[float]] = None
) -> Optional[Tuple[float, Dict[str, Any]]]:
    """
    Busca sin método. Retorna (precio, ad_usado)
    """
    found: List[Dict[str, Any]] = []
    for amt in (amounts or [None]):
        for p in range(1, pages + 1):
            ads, total = search_ads(fiat, trade_type, page=p, rows=rows, countries=countries, amount=amt)
            info(f"{fiat} {trade_type}: [countries={countries if countries else 'GLOBAL'}; page={p}; rows={rows}; amount={amt}] => {len(found)} filtrados (api={total}).")
            found.extend(ads)
            if total == 0:
                break
    best = pick_best(found, trade_type)
    if not best:
        return None
    price = extract_price(best)
    return (price, best)

def find_rate_with_method(
    fiat: str,
    trade_type: str,
    label: str,
    alias_ok: List[str],
    keyword_ok: Optional[str],
    countries: Optional[List[str]],
    allow_bank_transfer_if_keyword: bool = False,
    pages: int = 8,
    rows: int = 20,
    amounts: Optional[List[float]] = None
) -> Optional[Tuple[float, Dict[str, Any]]]:
    """
    Filtra por método (alias o keyword). Retorna (precio, ad_usado)
    """
    filtered: List[Dict[str, Any]] = []
    for amt in (amounts or [None]):
        for p in range(1, pages + 1):
            ads, total = search_ads(fiat, trade_type, page=p, rows=rows, countries=countries, amount=amt)
            # No pasamos payTypes en el payload: filtramos localmente
            got = []
            for ad in ads:
                if match_methods(ad, alias_ok, keyword_ok, allow_bank_transfer_if_keyword):
                    got.append(ad)
            info(f"{fiat} {trade_type}: [countries={countries if countries else 'GLOBAL'}; page={p}; rows={rows}; amount={amt}] => {len(got)} filtrados (api={total}).")
            filtered.extend(got)
            if total == 0:
                break
    best = pick_best(filtered, trade_type)
    if not best:
        return None
    price = extract_price(best)
    return (price, best)

# =========================
# Guardado
# =========================
def save_rate_to_db(country_label: str, side_label: str, price: float):
    """
    Inserta en Supabase. Si no está configurado, sólo imprime.
    """
    if _supabase is None:
        ok(f"Tasa guardada (simulada): USDT en {country_label} {side_label} = {price}")
        return
    try:
        payload = {
            "pais": country_label,
            "lado": side_label,           # "compra" o "venta"
            "asset": "USDT",
            "precio": price,
            "ts": int(time.time())
        }
        _supabase.table(SUPABASE_TABLE).insert(payload).execute()
        ok(f"Tasa guardada: USDT en {country_label} {side_label} = {price}")
    except Exception as e:
        warn(f"No se pudo guardar en DB: {e}")
        ok(f"Tasa (no guardada): USDT en {country_label} {side_label} = {price}")

# =========================
# Flujo principal
# =========================
def main():
    print("🔁 Ejecutando actualización de tasas...\n")

    # ---- BUY genéricos (sin método forzado) ----
    buy_generic = [
        # (fiat, countries, label_visible)
        ("VES", ["VE"], "Venezuela"),
        ("COP", ["CO"], "Colombia"),
        ("ARS", ["AR"], "Argentina"),
        ("PEN", ["PE"], "Perú"),
        # BRL BUY eliminado a pedido: no agregar aquí
    ]

    for fiat, countries, label in buy_generic:
        try:
            res = find_rate_generic(fiat, "BUY", countries, label, pages=5, rows=20, amounts=[None, 150, 200, 300])
            if res:
                price, ad = res
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("BUY", label, "vendedor", nickname, methods, price)
                save_rate_to_db(label, "compra", price)
            else:
                warn(f"BUY sin precio final para {label}.")
        except Exception:
            warn(f"Error en {fiat} BUY {label}:\n{traceback.format_exc()}")

    # ---- EUR BUY con Bizum prioritario ----
    try:
        # Busco GLOBAL para captar Bizum aunque el vendedor esté en ES
        eur_bizum = find_rate_with_method(
            fiat="EUR", trade_type="BUY", label="Europa",
            alias_ok=["bizum"], keyword_ok="bizum",
            countries=None,  # GLOBAL
            allow_bank_transfer_if_keyword=False,
            pages=5, rows=20, amounts=[None]
        )
        if eur_bizum:
            price, ad = eur_bizum
            nickname = extract_nickname(ad)
            methods = extract_methods(ad)
            used_offer_line("BUY", "Europa", "vendedor", nickname, methods, price)
            save_rate_to_db("Europa", "compra", price)
        else:
            # Fallback genérico si no hay Bizum
            res = find_rate_generic("EUR", "BUY", ["ES", "PT", "FR", "DE", "IT"], "Europa", pages=5, rows=20)
            if res:
                price, ad = res
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("BUY", "Europa", "vendedor", nickname, methods, price, extra="(FALLBACK SIN MÉTODO)")
                save_rate_to_db("Europa", "compra", price)
            else:
                warn("BUY sin precio final para Europa.")
    except Exception:
        warn(f"Error en EUR BUY (Bizum):\n{traceback.format_exc()}")

    # ---- USD BUY con Zelle: GLOBAL + alias/keyword; si no hay => usar SELL como proxy ----
    try:
        usd_zelle_buy = find_rate_with_method(
            fiat="USD", trade_type="BUY", label="USA",
            alias_ok=["zelle", "zelle transfer", "zelle (bank transfer)", "chase quickpay/zelle", "zelle pay"],
            keyword_ok="zelle",
            countries=None,                  # IMPORTANTE: no usar ['US']
            allow_bank_transfer_if_keyword=True,
            pages=10, rows=20, amounts=[None, 200, 300, 500]
        )
        if usd_zelle_buy:
            price, ad = usd_zelle_buy
            nickname = extract_nickname(ad)
            methods = extract_methods(ad)
            used_offer_line("BUY", "USA", "vendedor", nickname, methods, price)
            save_rate_to_db("USA", "compra", price)
        else:
            # Proxy desde SELL (compradores con Zelle)
            usd_zelle_sell = find_rate_with_method(
                fiat="USD", trade_type="SELL", label="USA (proxy BUY)",
                alias_ok=["zelle", "zelle transfer", "zelle (bank transfer)", "chase quickpay/zelle", "zelle pay"],
                keyword_ok="zelle",
                countries=None,
                allow_bank_transfer_if_keyword=True,
                pages=10, rows=20, amounts=[None, 200, 300, 500]
            )
            if usd_zelle_sell:
                price, ad = usd_zelle_sell
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("SELL", "USA", "comprador", nickname, methods, price, extra="(PROXY para BUY/Zelle)")
                # puedes aplicar un ajuste/spread si lo consideras necesario:
                proxy_buy_price = price  # sin ajuste
                ok(f"Tasa derivada de SELL/Zelle para BUY: {proxy_buy_price}")
                save_rate_to_db("USA", "compra", proxy_buy_price)
            else:
                # último fallback (sin método)
                res = find_rate_generic("USD", "BUY", None, "USA", pages=8, rows=20, amounts=[None, 200, 300, 500])
                if res:
                    price, ad = res
                    nickname = extract_nickname(ad)
                    methods = extract_methods(ad)
                    used_offer_line("BUY", "USA", "vendedor", nickname, methods, price, extra="(FALLBACK SIN MÉTODO)")
                    save_rate_to_db("USA", "compra", price)
                else:
                    warn("BUY sin precio final para USA (ni Zelle BUY ni Zelle SELL proxy).")
    except Exception:
        warn(f"Error en USD BUY (Zelle):\n{traceback.format_exc()}")

    # ---- Otros BUY específicos (ejemplos que ya usabas) ----
    special_buys = [
        ("USD", ["EC"], "Ecuador"),    # USD en Ecuador
        ("MXN", ["MX"], "México"),     # México
        ("PAB", ["PA"], "Panamá"),     # Panamá
        ("CLP", ["CL"], "Chile"),      # Chile
    ]
    for fiat, countries, label in special_buys:
        try:
            res = find_rate_generic(fiat, "BUY", countries, label, pages=5, rows=20)
            if res:
                price, ad = res
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("BUY", label, "vendedor", nickname, methods, price)
                save_rate_to_db(label, "compra", price)
            else:
                warn(f"BUY sin precio final para {label}.")
        except Exception:
            warn(f"Error en {fiat} BUY {label}:\n{traceback.format_exc()}")

    # ---- SELL genéricos ----
    sell_generic = [
        ("VES", ["VE"], "Venezuela"),
        ("COP", ["CO"], "Colombia"),
        ("ARS", ["AR"], "Argentina"),
        ("PEN", ["PE"], "Perú"),
        ("BRL", ["BR"], "Brasil"),       # BRL SELL se mantiene
        ("EUR", ["ES", "PT", "FR", "DE", "IT"], "Europa"),
    ]
    for fiat, countries, label in sell_generic:
        try:
            res = find_rate_generic(fiat, "SELL", countries, label, pages=5, rows=20)
            if res:
                price, ad = res
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("SELL", label, "comprador", nickname, methods, price)
                save_rate_to_db(label, "venta", price)
            else:
                warn(f"SELL sin precio final para {label}.")
        except Exception:
            warn(f"Error en {fiat} SELL {label}:\n{traceback.format_exc()}")

    # ---- USD SELL con Zelle (por si quieres mantenerlo explícito) ----
    try:
        usd_sell_zelle = find_rate_with_method(
            fiat="USD", trade_type="SELL", label="USA/Zelle",
            alias_ok=["zelle", "zelle transfer", "zelle (bank transfer)", "chase quickpay/zelle", "zelle pay"],
            keyword_ok="zelle",
            countries=None,
            allow_bank_transfer_if_keyword=True,
            pages=10, rows=20, amounts=[None, 200, 300, 500]
        )
        if usd_sell_zelle:
            price, ad = usd_sell_zelle
            nickname = extract_nickname(ad)
            methods = extract_methods(ad)
            used_offer_line("SELL", "USA", "comprador", nickname, methods, price)
            save_rate_to_db("USA", "venta", price)
        else:
            # Fallback genérico SELL
            res = find_rate_generic("USD", "SELL", None, "USA", pages=8, rows=20)
            if res:
                price, ad = res
                nickname = extract_nickname(ad)
                methods = extract_methods(ad)
                used_offer_line("SELL", "USA", "comprador", nickname, methods, price, extra="(FALLBACK SIN MÉTODO)")
                save_rate_to_db("USA", "venta", price)
            else:
                warn("SELL sin precio final para USA.")
    except Exception:
        warn(f"Error en USD SELL (Zelle):\n{traceback.format_exc()}")

    print("\n✅ Proceso finalizado.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrumpido por usuario.")
    except Exception:
        print("⛔ Error fatal:\n" + traceback.format_exc())

