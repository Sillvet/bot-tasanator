# p2p_ui_top5_pares_reglas_integrado_anypage.py
# -*- coding: utf-8 -*-
"""
Colector P2P (USDT) por UI (Playwright), sin requests locales a la API:
- Toma 5 ofertas por país/side usando las mismas llamadas de la web.
- Si hay method (Zelle, Bizum, Bancolombia, BCP, Pichincha, Mercantil), recorre
  páginas hasta conseguir 5 (no solo la primera).
- La 5ª oferta (o última disponible) es la base del país.
- Guarda "USDT en {pais}" / "(venta)" y calcula tasas por pares (full/público/mayorista + promedios).
- Margen especial cuando el ORIGEN es México (público 7% / mayorista 10%) si no hay regla específica.
- Brasil no tiene BUY (solo destino SELL).

Requisitos:
  pip install playwright
  playwright install
  (tu supabase_client.py con `supabase`)
"""

from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal

from playwright.sync_api import sync_playwright
from supabase_client import supabase

# ------- Constantes -------
BASE = "https://p2p.binance.com"
LANG = "es"
ASSET = "USDT"
ROWS = 20
TIMEOUT_MS = 60000
TOP_N = 5
MAX_PAGES_METHOD = 15  # hasta cuántas páginas intentar al buscar por método

# ------- PayTypes + keywords -------
PAYTYPE_IDS: Dict[str, List[str]] = {
    "Zelle": ["Zelle", "Zelle (Bank Transfer)"],
    "Bizum": ["Bizum"],
    "Bancolombia": ["BancolombiaSA", "Bancolombia", "Bancolombia S.A", "Bancolombia S.A."],
    "Banco de Credito": ["BancoDeCredito", "BCP", "Banco de Credito", "Banco de Crédito"],
    "Banco Pichincha": ["BancoPichincha", "Banco Pichincha", "Pichincha"],
    "Mercantil Bank Panama": [
        "MercantilBankPanama",
        "Mercantil Bank Panama",
        "Mercantil Bank Panamá",
        "Mercantil"
    ],
}
KEYWORDS_BY_METHOD: Dict[str, List[str]] = {
    "Zelle": ["zelle"],
    "Bizum": ["bizum", "bizzum", "bizaum"],
    "Bancolombia": ["bancolombia"],
    "Banco de Credito": ["banco de credito", "banco de crédito", "bcp"],
    "Banco Pichincha": ["pichincha"],
    "Mercantil Bank Panama": ["mercantil bank panama", "mercantil bank panamá", "mercantil"],
}

# ------- Mercados -------
BUY_CONFIGS = [
    {"label": "Venezuela", "fiat": "VES", "method": None,                "countries": ["VE"]},
    {"label": "Colombia",  "fiat": "COP", "method": "Bancolombia",       "countries": ["CO"]},
    {"label": "Argentina", "fiat": "ARS", "method": None,                "countries": ["AR"]},
    {"label": "Perú",      "fiat": "PEN", "method": "Banco de Credito",  "countries": ["PE"]},
    {"label": "Europa",    "fiat": "EUR", "method": "Bizum",             "countries": ["ES"]},
    {"label": "USA",       "fiat": "USD", "method": "Zelle",             "countries": ["US"]},
    {"label": "México",    "fiat": "MXN", "method": None,                "countries": ["MX"]},
    {"label": "Panamá",    "fiat": "USD", "method": "Mercantil Bank Panama", "countries": ["PA"]},
    {"label": "Ecuador",   "fiat": "USD", "method": "Banco Pichincha",   "countries": ["EC"]},
    {"label": "Chile",     "fiat": "CLP", "method": None,                "countries": ["CL"]},
]
SELL_CONFIGS = [
    {"label": "Venezuela", "fiat": "VES", "method": None,                "countries": ["VE"]},
    {"label": "Argentina", "fiat": "ARS", "method": None,                "countries": ["AR"]},
    {"label": "Brasil",    "fiat": "BRL", "method": None,                "countries": ["BR"]},
    {"label": "Colombia",  "fiat": "COP", "method": "Bancolombia",       "countries": ["CO"]},
    {"label": "Perú",      "fiat": "PEN", "method": "Banco de Credito",  "countries": ["PE"]},
    {"label": "Europa",    "fiat": "EUR", "method": "Bizum",             "countries": ["ES"]},
    {"label": "USA",       "fiat": "USD", "method": "Zelle",             "countries": ["US"]},
    {"label": "México",    "fiat": "MXN", "method": None,                "countries": ["MX"]},
    {"label": "Panamá",    "fiat": "USD", "method": "Mercantil Bank Panama", "countries": ["PA"]},
    {"label": "Ecuador",   "fiat": "USD", "method": "Banco Pichincha",   "countries": ["EC"]},
    {"label": "Chile",     "fiat": "CLP", "method": None,                "countries": ["CL"]},
]

# ------- Márgenes -------
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
    # México como origen: público 7%, mayorista 10%
    "México - Venezuela": {"publico": 0.07, "mayorista": 0.10},
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
pares_sumar_margen = {"Chile - USA", "Colombia - Venezuela"}

def margen_por_defecto(base: str) -> Dict[str, float]:
    if base.startswith("México - "):
        return {"publico": 0.07, "mayorista": 0.10}
    return {"publico": 0.07, "mayorista": 0.03}

# ------- NUEVO: Decimales dinámicos -------
def decimales_auto(t: float, origen: str, destino: str) -> int:
    """
    Elige cuántos decimales guardar según la magnitud de la tasa,
    preservando tu regla original de Chile (5 decimales en algunos destinos).
    """
    base_rule = 5 if (origen == "Chile" and destino in ["Panamá", "Ecuador", "Europa", "Brasil"]) else 4
    if t < 0.0001:
        mag_rule = 8
    elif t < 0.01:
        mag_rule = 6
    elif t < 1:
        mag_rule = 5
    elif t < 100:
        mag_rule = 4
    elif t < 1000:
        mag_rule = 3
    else:
        mag_rule = 2
    return max(base_rule, mag_rule)

# ------- Utilidades -------
def page_url(fiat: str, side: str) -> str:
    t = "buy" if side.upper() == "BUY" else "sell"
    return f"{BASE}/{LANG}/trade/{t}/{ASSET}?fiat={fiat}"

def parse_price(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def extract_methods(adv: Dict[str, Any]) -> List[str]:
    out, seen = [], set()
    for tm in (adv.get("tradeMethods") or []):
        name = tm.get("tradeMethodShortName") or tm.get("identifier") or tm.get("tradeMethodName")
        if name and name not in seen:
            seen.add(name)
            out.append(str(name))
    return out

def _adv_blob(adv: Dict[str, Any], advertiser: Dict[str, Any]) -> str:
    parts = []
    for k in ("advRemark", "remark", "buyerRemarks", "sellerRemarks", "tradeTips"):
        v = adv.get(k)
        if v:
            parts.append(str(v))
    for k in ("userRemark", "remark", "introduce", "desc"):
        v = advertiser.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts).lower()

def _items_keyword_filter(items, needles: List[str], method_label: Optional[str] = None):
    needles_l = [n.lower() for n in needles if n]
    method_keywords = [k.lower() for k in KEYWORDS_BY_METHOD.get(method_label or "", [])]
    out = []
    for it in items or []:
        adv = it.get("adv") or {}
        advertiser = it.get("advertiser") or {}
        tms = adv.get("tradeMethods") or []
        has_bank_transfer = False
        has_direct_match = False
        for tm in tms:
            txt = (
                (tm.get("tradeMethodShortName") or "") + " " +
                (tm.get("identifier") or "") + " " +
                (tm.get("tradeMethodName") or "")
            ).lower()
            if "bank transfer" in txt or "transferencia bancaria" in txt:
                has_bank_transfer = True
            if any(n in txt for n in needles_l):
                has_direct_match = True
        if has_direct_match:
            out.append(it)
            continue
        if has_bank_transfer and method_keywords:
            blob = _adv_blob(adv, advertiser)
            if any(kw in blob for kw in method_keywords):
                out.append(it)
                continue
    return out

# --- Petición dentro del contexto de la página (como la UI) ---
def fetch_ui_page(page, fiat: str, side: str, countries: Optional[List[str]], pay_types: Optional[List[str]], page_no: int):
    api = f"{BASE}/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "page": page_no,
        "rows": ROWS,
        "asset": ASSET,
        "tradeType": side.upper(),
        "fiat": fiat,
        "publisherType": None,
        "payTypes": pay_types or [],
        "countries": countries or []
    }
    data = page.evaluate(
        """async ({api, payload}) => {
            const r = await fetch(api, {
              method: 'POST',
              headers: {'content-type':'application/json'},
              body: JSON.stringify(payload)
            });
            return await r.json();
        }""",
        {"api": api, "payload": payload}
    )
    return (data or {}).get("data") or []

def capture_first_page(fiat: str, side: str, countries: Optional[List[str]]) -> List[Dict[str, Any]]:
    url = page_url(fiat, side)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded")
        items = fetch_ui_page(pg, fiat, side, countries, None, 1)
        browser.close()
    return items

def capture_method_topN_any_page(fiat: str, side: str, method_label: str,
                                 countries: Optional[List[str]], need_n: int = TOP_N) -> List[Dict[str, Any]]:
    """
    Reúne hasta need_n ofertas para un método, recorriendo páginas.
    Estrategia:
      1) payTypes con countries=[], luego countries indicados (si hay).
      2) Fallback sin payTypes + filtro local por método/keywords.
    """
    method_ids = PAYTYPE_IDS.get(method_label, [])
    url = page_url(fiat, side)

    # países a intentar (primero GLOBAL para no perder ofertas)
    country_sets: List[Optional[List[str]]] = [None]  # None -> []
    if countries:
        country_sets.append(countries)

    collected: List[Dict[str, Any]] = []
    seen_advnos = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded")

        # 1) Intento con payTypes
        for cset in country_sets:
            for page_no in range(1, MAX_PAGES_METHOD + 1):
                arr = fetch_ui_page(pg, fiat, side, cset, method_ids, page_no)
                for it in arr:
                    adv = it.get("adv") or {}
                    advno = adv.get("advNo") or (adv.get("price"), (it.get("advertiser") or {}).get("nickName"))
                    if advno in seen_advnos:
                        continue
                    seen_advnos.add(advno)
                    collected.append(it)
                    if len(collected) >= need_n:
                        break
                if len(collected) >= need_n or not arr:
                    break
            if len(collected) >= need_n:
                break

        # 2) Fallback local si faltan
        if len(collected) < need_n:
            for cset in country_sets:
                for page_no in range(1, MAX_PAGES_METHOD + 1):
                    arr = fetch_ui_page(pg, fiat, side, cset, None, page_no)
                    arr = _items_keyword_filter(arr, method_ids, method_label=method_label)
                    for it in arr:
                        adv = it.get("adv") or {}
                        advno = adv.get("advNo") or (adv.get("price"), (it.get("advertiser") or {}).get("nickName"))
                        if advno in seen_advnos:
                            continue
                        seen_advnos.add(advno)
                        collected.append(it)
                        if len(collected) >= need_n:
                            break
                    if len(collected) >= need_n or not arr:
                        break
                if len(collected) >= need_n:
                    break

        browser.close()

    # Orden por precio ascendente (coincide con la UI por “Ordenar por precio”)
    collected.sort(key=lambda it: parse_price((it.get("adv") or {}).get("price")) or 0.0)
    return collected[:need_n]

def topN_from_items(items: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    out = []
    for it in items[:max(0, n)]:
        adv = it.get("adv") or {}
        advertiser = it.get("advertiser") or {}
        price = parse_price(adv.get("price"))
        seller = advertiser.get("nickName") or advertiser.get("nick_name") or "N/A"
        methods = extract_methods(adv)
        out.append({"price": price, "seller": seller, "methods": methods})
    return out

def print_block(label: str, fiat: str, side: str, offers: List[Dict[str, Any]]):
    print("\n===============================")
    print(f"= {label} | {fiat} | {side} =")
    print("===============================")
    if not offers:
        print("Sin resultados.")
        return
    for i, o in enumerate(offers, 1):
        print(f"[{i:02d}] precio={o['price']} | vendedor={o['seller']} | métodos={', '.join(o['methods'])}")

# ------- Supabase -------
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

# ------- Orquestación -------
def tomar_base_y_guardar(label: str, fiat: str, side: str,
                         method: Optional[str], countries: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    # Si hay method, buscamos hasta conseguir 5 aunque no estén en la primera página.
    if method:
        items = capture_method_topN_any_page(fiat, side, method, countries, need_n=TOP_N)
    else:
        items = capture_first_page(fiat, side, countries)

    offers = topN_from_items(items, TOP_N)
    print_block(label, fiat, side, offers)
    if not offers:
        return None

    base = offers[TOP_N - 1] if len(offers) >= TOP_N else offers[-1]
    precio_base = base["price"]
    vendedor = base["seller"]
    metodos = base["methods"]

    quien = "comprador" if side.upper() == "SELL" else "vendedor"
    print(f"➡️  Base para {label} {side}: precio={precio_base} | {quien}={vendedor} | métodos={', '.join(metodos)}")

    nombre = f"USDT en {label}" + (" (venta)" if side.upper() == "SELL" else "")
    guardar_tasa(nombre, precio_base)

    return {"price": float(precio_base), "seller": vendedor, "methods": metodos, "fiat": fiat}

def calcular_pares(precios_buy: Dict[str, Dict[str, Any]],
                   precios_sell: Dict[str, Dict[str, Any]]):
    for origen, odata in precios_buy.items():
        for destino, ddata in precios_sell.items():
            if origen == destino:
                continue
            base = f"{origen} - {destino}"
            p_origen = odata["price"]
            p_dest   = ddata["price"]

            # FULL (según pares_sumar_margen)
            if base in pares_sumar_margen:
                tasa_full = p_origen / p_dest
            else:
                tasa_full = p_dest / p_origen

            # ⬇️ NUEVO: decimales según magnitud (más la regla especial de Chile)
            decimales = decimales_auto(tasa_full, origen, destino)

            # Márgenes
            margen = margenes_personalizados.get(base, margen_por_defecto(base))
            if base in pares_sumar_margen:
                tasa_publico   = tasa_full * (1 + margen["publico"])
                tasa_mayorista = tasa_full * (1 + margen["mayorista"])
            else:
                tasa_publico   = tasa_full * (1 - margen["publico"])
                tasa_mayorista = tasa_full * (1 - margen["mayorista"])

            # Guardar
            guardar_tasa(f"Tasa full {base}", tasa_full, decimales)
            guardar_tasa(f"Tasa público {base}", tasa_publico, decimales)
            guardar_tasa(f"Tasa mayorista {base}", tasa_mayorista, decimales)

            # Promedios (media móvil 2)
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

def main():
    print("\n🔁 Ejecutando actualización (UI → top 5; base=5ª oferta; method=multi-página)...")

    precios_buy: Dict[str, Dict[str, Any]] = {}
    precios_sell: Dict[str, Dict[str, Any]] = {}

    # BUY (sin Brasil)
    for cfg in BUY_CONFIGS:
        res = tomar_base_y_guardar(cfg["label"], cfg["fiat"], "BUY", cfg.get("method"), cfg.get("countries"))
        if res:
            precios_buy[cfg["label"]] = res

    # SELL (incluye Brasil)
    for cfg in SELL_CONFIGS:
        res = tomar_base_y_guardar(cfg["label"], cfg["fiat"], "SELL", cfg.get("method"), cfg.get("countries"))
        if res:
            precios_sell[cfg["label"]] = res

    calcular_pares(precios_buy, precios_sell)
    print("\n✅ Proceso finalizado.")

if __name__ == "__main__":
    main()
