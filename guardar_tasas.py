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
    "Mercantil": ["Mercantil", "Banco Mercantil"],
    "Mercantil Bank Panama": [
        "MercantilBankPanama", "Mercantil Bank Panama", "Mercantil Bank Panamá", "Mercantil"
    ],
    # Argentina / México (se mantienen como estaban)
    "Bank Transfer AR": ["Bank Transfer (Argentina)", "Bank Transfer", "Transferencia bancaria"],
    "Transferencia bancaria MX": ["Transferencia bancaria", "Bank Transfer"],
}
KEYWORDS_BY_METHOD: Dict[str, List[str]] = {
    "Zelle": ["zelle"],
    "Bizum": ["bizum", "bizzum", "bizaum"],
    "Bancolombia": ["bancolombia"],
    "Banco de Credito": ["banco de credito", "banco de crédito", "bcp"],
    "Banco Pichincha": ["pichincha"],
    "Mercantil Bank Panama": ["mercantil bank panama", "mercantil bank panamá", "mercantil"],
    "Mercantil": ["mercantil", "banco mercantil"],
    "Bank Transfer AR": ["bank transfer", "transferencia bancaria", "argentina"],
    "Transferencia bancaria MX": ["transferencia bancaria", "bank transfer", "mexico", "méxico"],
}

# ------- Mercados -------
BUY_CONFIGS = [
    {"label": "Venezuela", "fiat": "VES", "method": "Mercantil",                 "countries": ["VE"]},
    {"label": "Colombia",  "fiat": "COP", "method": "Bancolombia",               "countries": ["CO"]},
    {"label": "Argentina", "fiat": "ARS", "method": "Bank Transfer AR",          "countries": None},
    {"label": "Perú",      "fiat": "PEN", "method": "Banco de Credito",          "countries": ["PE"]},
    {"label": "Europa",    "fiat": "EUR", "method": "Bizum",                     "countries": ["ES"]},
    {"label": "USA",       "fiat": "USD", "method": "Zelle",                     "countries": ["US"]},
    {"label": "México",    "fiat": "MXN", "method": "Transferencia bancaria MX", "countries": None},
    {"label": "Panamá",    "fiat": "USD", "method": "Mercantil Bank Panama",     "countries": ["PA"]},
    {"label": "Ecuador",   "fiat": "USD", "method": "Banco Pichincha",           "countries": ["EC"]},
    {"label": "Chile",     "fiat": "CLP", "method": None,                        "countries": ["CL"]},
]
SELL_CONFIGS = [
    {"label": "Venezuela", "fiat": "VES", "method": "Mercantil",                 "countries": ["VE"]},
    {"label": "Argentina", "fiat": "ARS", "method": "Bank Transfer AR",          "countries": None},
    {"label": "Brasil",    "fiat": "BRL", "method": None,                        "countries": ["BR"]},
    {"label": "Colombia",  "fiat": "COP", "method": "Bancolombia",               "countries": ["CO"]},
    {"label": "Perú",      "fiat": "PEN", "method": "Banco de Credito",          "countries": ["PE"]},
    {"label": "Europa",    "fiat": "EUR", "method": "Bizum",                     "countries": ["ES"]},
    {"label": "USA",       "fiat": "USD", "method": "Zelle",                     "countries": ["US"]},
    {"label": "México",    "fiat": "MXN", "method": "Transferencia bancaria MX", "countries": None},
    {"label": "Panamá",    "fiat": "USD", "method": "Mercantil Bank Panama",     "countries": ["PA"]},
    {"label": "Ecuador",   "fiat": "USD", "method": "Banco Pichincha",           "countries": ["EC"]},
    {"label": "Chile",     "fiat": "CLP", "method": None,                        "countries": ["CL"]},
]

# ------- Índice base por mercado (BUY) -------
BASE_INDEX_BY_MARKET: Dict[Tuple[str, str], int] = {
    ("Colombia", "BUY"): 10,
    ("Argentina", "BUY"): 10,
    ("México",   "BUY"): 10,
    # otros: TOP_N = 5
}

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

# === Ajustes solicitados de márgenes (sin cambiar nada más) ===
# Zelle → Chile (USA - Chile): Mayorista 10%, Público 7%  (ANTERIOR; quedará sobrescrito por el patch nuevo)
margenes_personalizados["USA - Chile"] = {"publico": 0.07, "mayorista": 0.10}
# Colombia → Chile: Público 7%, Mayorista 4%
margenes_personalizados["Colombia - Chile"] = {"publico": 0.07, "mayorista": 0.04}
# México → Venezuela: Público 10%, Mayorista 7% (reemplaza valor previo)
margenes_personalizados["México - Venezuela"] = {"publico": 0.10, "mayorista": 0.07}
# Argentina → Perú: Público 7%, Mayorista 4%
margenes_personalizados["Argentina - Perú"] = {"publico": 0.07, "mayorista": 0.04}

pares_sumar_margen = {"Chile - USA", "Colombia - Venezuela"}

def margen_por_defecto(base: str) -> Dict[str, float]:
    if base.startswith("México - "):
        return {"publico": 0.07, "mayorista": 0.10}
    return {"publico": 0.07, "mayorista": 0.045}

# ------- Decimales dinámicos -------
def decimales_auto(t: float, origen: str, destino: str) -> int:
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
        s = str(v).replace(",", "").replace("\u00A0", "").strip()
        return float(s)
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

def _sort_items_by_price_asc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _p(it):
        adv = it.get("adv") or {}
        pr = parse_price(adv.get("price"))
        return pr if pr is not None else float("inf")
    return sorted(items or [], key=_p)

# ------- Merchant helpers (verificados + únicos) -------
def _is_verified_merchant(advertiser: Dict[str, Any]) -> bool:
    """
    Heurística segura: ya filtramos publisherType='merchant'.
    Exigimos además alguna señal de verificación/KYC sin romper si faltan campos.
    """
    if not advertiser:
        return False
    if str(advertiser.get("userType", "")).lower() != "merchant":
        return False

    flags = [
        advertiser.get("isMerchantCertified"),
        advertiser.get("isUserCertified"),
        advertiser.get("isTradeCertified"),
        advertiser.get("isVerified"),
        advertiser.get("kycVerify"),
        advertiser.get("certifiedMerchant"),
    ]
    grades = str(advertiser.get("userGrade", "")).lower()
    kyc = str(advertiser.get("userKycType", "")).lower()

    if any(bool(f) for f in flags):
        return True
    if grades in ("verified", "merchant", "gold", "pro"):
        return True
    if kyc in ("kyc", "person", "merchant", "verified"):
        return True
    return True

def _unique_verified_merchants(items: List[Dict[str, Any]], max_n: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in items or []:
        advertiser = (it.get("advertiser") or {})
        if not _is_verified_merchant(advertiser):
            continue
        key = advertiser.get("userNo") or advertiser.get("nickName")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= max_n:
            break
    return out

# === Filtro "Solo anuncios comerciables"
def _filter_tradable(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items or []:
        adv = it.get("adv") or {}
        if "tradable" in adv:
            if bool(adv.get("tradable")):
                out.append(it)
        else:
            out.append(it)
    return out

# --- Petición dentro del contexto de la página (como la UI) ---
def fetch_ui_page(page, fiat: str, side: str, countries: Optional[List[str]],
                  pay_types: Optional[List[str]], page_no: int,
                  publisher_type: Optional[str] = None):
    """
    publisher_type:
      - None → sin filtro
      - "merchant" → Solo anuncios de comerciantes
    """
    api = f"{BASE}/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "page": page_no,
        "rows": ROWS,
        "asset": ASSET,
        "tradeType": side.upper(),
        "fiat": fiat,
        "publisherType": publisher_type,
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

        # Forzar merchants (publisherType="merchant")
        def _try(cset):
            return fetch_ui_page(pg, fiat, side, cset, None, 1, publisher_type="merchant")

        if fiat == "CLP":
            items = _try(None)
            if not items:
                items = _try(["CL"])
            if not items and countries is not None:
                items = _try(countries)
        else:
            items = _try(countries)

        browser.close()

    # Solo comerciables + verificados únicos
    items = _filter_tradable(items)
    items = _unique_verified_merchants(items, max_n=50)

    if side.upper() == "SELL":
        items = _sort_items_by_price_asc(items)
    return items

def capture_method_page_exact(
    fiat: str,
    side: str,
    method_label: str,
    page_no: int = 1,
    need_n: int = 10,
    countries: Optional[List[str]] = None,
    merchant_only: bool = False,
    dedupe_verified: bool = False,
) -> List[Dict[str, Any]]:
    method_ids = PAYTYPE_IDS.get(method_label, [])
    url = page_url(fiat, side)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded")

        items = fetch_ui_page(
            pg, fiat, side, countries, method_ids, page_no,
            publisher_type=("merchant")
        )
        browser.close()

    items = _filter_tradable(items)

    if dedupe_verified:
        items = _unique_verified_merchants(items, need_n)
    else:
        items = _unique_verified_merchants(items, max_n=50)

    if side.upper() == "SELL":
        items = _sort_items_by_price_asc(items)
    return items[:need_n]

def capture_method_topN_any_page(fiat: str, side: str, method_label: str,
                                 countries: Optional[List[str]], need_n: int = TOP_N) -> List[Dict[str, Any]]:
    method_ids = PAYTYPE_IDS.get(method_label, [])
    url = page_url(fiat, side)

    country_sets: List[Optional[List[str]]] = [None]
    if countries:
        country_sets.append(countries)

    collected: List[Dict[str, Any]] = []
    seen_advnos = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded")

        # 1) Con payTypes (publisherType="merchant")
        for cset in country_sets:
            for page_no in range(1, MAX_PAGES_METHOD + 1):
                arr = fetch_ui_page(pg, fiat, side, cset, method_ids, page_no, publisher_type="merchant")
                arr = _filter_tradable(arr)
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

        # 2) Fallback sin payTypes + filtro local (merchant + comerciables)
        if len(collected) < need_n:
            for cset in country_sets:
                for page_no in range(1, MAX_PAGES_METHOD + 1):
                    arr = fetch_ui_page(pg, fiat, side, cset, None, page_no, publisher_type="merchant")
                    arr = _filter_tradable(arr)
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

    collected = _unique_verified_merchants(collected, need_n)

    if side.upper() == "SELL":
        collected = _sort_items_by_price_asc(collected)

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
        ms = ", ".join(o['methods']) if o.get('methods') else ""
        print(f"[{i:02d}] precio={o['price']} | vendedor={o['seller']} | métodos={ms}")

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
    side_u = side.upper()

    # SELL: siempre mostrar 10 y guardar la #1 (la más barata, ya ordenado asc)
    if side_u == "SELL":
        base_idx = 1
        need_n = 10
    else:
        base_idx = BASE_INDEX_BY_MARKET.get((label, side_u), TOP_N)
        need_n = base_idx

    # Caso especial: USD + Zelle (ambos lados)
    if method == "Zelle" and fiat == "USD":
        items = capture_method_page_exact(
            fiat=f"{fiat}",
            side=side_u,
            method_label="Zelle",
            page_no=1,
            need_n=10,
            countries=None,          # GLOBAL
            merchant_only=True,      # Solo comerciantes (se fuerza igual adentro)
            dedupe_verified=True     # Verificados + sin repetidos
        )
    else:
        # Comportamiento por defecto
        if method:
            items = capture_method_topN_any_page(fiat, side_u, method, countries, need_n=need_n)
        else:
            items = capture_first_page(fiat, side_u, countries)

    offers = topN_from_items(items, 10 if (method == "Zelle" and fiat == "USD") else need_n)
    print_block(label, fiat, side_u, offers)
    if not offers:
        return None

    # Selección base:
    if method == "Zelle" and fiat == "USD":
        if side_u == "SELL":
            base = min(offers, key=lambda o: (o["price"] if o["price"] is not None else float("inf")))
        else:  # BUY
            base = max(offers, key=lambda o: (o["price"] if o["price"] is not None else -float("inf")))
    else:
        if side_u == "SELL":
            base = offers[0]  # ya ordenado ascendente
        elif (label, side_u) in {("Colombia", "BUY"), ("Argentina", "BUY"), ("México", "BUY")}:
            base = max(offers, key=lambda o: (o["price"] if o["price"] is not None else -float("inf")))
        else:
            base = offers[base_idx - 1] if len(offers) >= base_idx else offers[-1]

    precio_base = base["price"]
    vendedor = base["seller"]
    metodos = base["methods"]

    quien = "comprador" if side_u == "SELL" else "vendedor"
    ms = ", ".join(metodos) if metodos else ""
    print(f"➡️  Base para {label} {side_u}: precio={precio_base} | {quien}={vendedor} | métodos={ms}")

    nombre = f"USDT en {label}" + (" (venta)" if side_u == "SELL" else "")
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

            # ===== REGLA USA (destino Zelle) =====
            # Siempre invertida y con márgenes por RESTA + 6 decimales.
            if destino == "USA":
                tasa_full = p_dest / p_origen        # inversa para que sea ~0.0009xx
                decimales = 6
                margen = margenes_personalizados.get(base, margen_por_defecto(base))
                tasa_publico   = tasa_full * (1 - margen["publico"])
                tasa_mayorista = tasa_full * (1 - margen["mayorista"])
            else:
                # lógica previa intacta
                if base in pares_sumar_margen:
                    tasa_full = p_origen / p_dest
                else:
                    tasa_full = p_dest / p_origen
                decimales = decimales_auto(tasa_full, origen, destino)
                margen = margenes_personalizados.get(base, margen_por_defecto(base))
                if base in pares_sumar_margen:
                    tasa_publico   = tasa_full * (1 + margen["publico"])
                    tasa_mayorista = tasa_full * (1 + margen["mayorista"])
                else:
                    tasa_publico   = tasa_full * (1 - margen["publico"])
                    tasa_mayorista = tasa_full * (1 - margen["mayorista"])

            guardar_tasa(f"Tasa full {base}", tasa_full, decimales)
            guardar_tasa(f"Tasa público {base}", tasa_publico, decimales)
            guardar_tasa(f"Tasa mayorista {base}", tasa_mayorista, decimales)

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
    print("\n🔁 Ejecutando actualización (SELL=10 guarda #1; BUY: CO/AR/MX=10 y toma mayor; Zelle/USD=merchant-only verificados únicos p1; filtros Comerciables y Verificados; destino USA invertida 6 decimales con márgenes por resta)…")

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

# --- compatibilidad para el bot ---
def actualizar_todas_las_tasas():
    return main()

# =========================
# === PATCH DE MÁRGENES ===
# (Se aplica al final para sobrescribir cualquier valor previo
#  SOLO en los pares pedidos; lo demás queda igual)
margenes_personalizados.update({
    # Zelle = USA -> destino
    "USA - Chile":     {"publico": 0.10, "mayorista": 0.07},
    "USA - Colombia":  {"publico": 0.10, "mayorista": 0.07},
    "USA - Argentina": {"publico": 0.10, "mayorista": 0.07},
    "USA - Venezuela": {"publico": 0.10, "mayorista": 0.07},

    # Direcciones específicas indicadas
    "Argentina - Chile":     {"publico": 0.07, "mayorista": 0.04},
    "Venezuela - Argentina": {"publico": 0.07, "mayorista": 0.04},
    "Venezuela - USA":       {"publico": 0.07, "mayorista": 0.04},  # "Venezuela Zelle"
    "Venezuela - Chile":     {"publico": 0.07, "mayorista": 0.04},
    "Venezuela - Perú":      {"publico": 0.07, "mayorista": 0.04},
    "Venezuela - Colombia":  {"publico": 0.07, "mayorista": 0.04},

    "Colombia - Perú":   {"publico": 0.07, "mayorista": 0.04},
    "Colombia - México": {"publico": 0.07, "mayorista": 0.04},

    # México especificados
    "México - Argentina": {"publico": 0.10, "mayorista": 0.07},
    "México - Colombia":  {"publico": 0.10, "mayorista": 0.07},
})
# =========================

if __name__ == "__main__":
    main()
