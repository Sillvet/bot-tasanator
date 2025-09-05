# p2p_ui_rangos_y_metodos_top3.py
# -*- coding: utf-8 -*-

from typing import List, Tuple, Dict, Any, Optional
from playwright.sync_api import sync_playwright

BASE = "https://p2p.binance.com"
LANG = "es"  # usa "en" si prefieres
ASSET = "USDT"
ROWS = 20
TIMEOUT_MS = 60000
TOP_N = 3  # cuántas ofertas mostrar

# ---- Rangos por país (puedes editarlos) ----
RANGO_COP = (100_000, 1_000_000)  # min, max (inclusive)
RANGO_PEN = (353, None)           # None => sin tope superior

def page_url(fiat: str, side: str) -> str:
    s = "buy" if side.upper() == "BUY" else "sell"
    return f"{BASE}/{LANG}/trade/{s}/{ASSET}?fiat={fiat}"

def parse_float(x: Any) -> Optional[float]:
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None

def rango_superpone_anuncio(adv: Dict[str, Any], min_req: Optional[float], max_req: Optional[float]) -> bool:
    """
    True si el rango deseado [min_req, max_req] se solapa con el rango del anuncio [minAdv, maxAdv].
    Si max_req es None => +infinito. Si minAdv/maxAdv vienen vacíos, asumimos 0 / +inf.
    """
    mn_s = adv.get("minSingleTransAmount") or adv.get("minSingleTransAmountString")
    mx_s = adv.get("maxSingleTransAmount") or adv.get("maxSingleTransAmountString")
    mn_adv = parse_float(mn_s) if mn_s is not None else 0.0
    mx_adv = parse_float(mx_s) if mx_s is not None else float("inf")

    mn_req = min_req if min_req is not None else 0.0
    mx_req = max_req if max_req is not None else float("inf")

    # superposición de rangos: [a1, a2] con [b1, b2] si a1 <= b2 y a2 >= b1
    return (mn_adv <= mx_req) and (mx_adv >= mn_req)

# -------------------------------
# CAPTURA “PRIMERA PÁGINA” (UI)
# -------------------------------
def fetch_first_page_ui(fiat: str, side: str) -> List[Dict[str, Any]]:
    """
    Abre la página pública y captura la PRIMERA respuesta que la UI manda a
    /bapi/c2c/v2/friendly/c2c/adv/search (sin filtros).
    """
    url = page_url(fiat, side)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="es-ES",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = context.new_page()

        def cond(resp):
            if "/bapi/c2c/v2/friendly/c2c/adv/search" not in resp.url:
                return False
            pd = resp.request.post_data or ""
            return (f'"fiat":"{fiat}"' in pd) and (f'"tradeType":"{side.upper()}"' in pd)

        with page.expect_response(cond, timeout=TIMEOUT_MS) as info:
            page.goto(url, wait_until="domcontentloaded")
        data = info.value.json()
        browser.close()
    return data.get("data") or []

# ------------------------------------------
# CAPTURA “PRIMERA PÁGINA” CON MÉTODO PAGO
# ------------------------------------------
def fetch_first_page_ui_with_method(fiat: str, side: str, pay_type: str) -> List[Dict[str, Any]]:
    """
    Simula el filtro de método de pago como lo haría la UI:
    hace un POST desde el contexto del navegador con payTypes=[pay_type]
    y devuelve la PRIMERA página resultante.
    """
    url = page_url(fiat, side)
    api = f"{BASE}/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "page": 1,
        "rows": ROWS,
        "asset": ASSET,
        "tradeType": side.upper(),
        "fiat": fiat,
        "publisherType": None,
        "payTypes": [pay_type],  # << filtro de método
        "countries": []
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        data = page.evaluate(
            """async ({api, payload}) => {
                const resp = await fetch(api, {
                  method: 'POST',
                  headers: {'content-type': 'application/json'},
                  body: JSON.stringify(payload)
                });
                return await resp.json();
            }""",
            {"api": api, "payload": payload}  # <-- UN SOLO ARGUMENTO
        )
        browser.close()

    return (data or {}).get("data") or []

# -------------------------
# PICKERS (UI -> top N)
# -------------------------
def topN_from_first_page(items: List[Dict[str, Any]], n: int) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    for it in items[:max(0, n)]:
        adv = it.get("adv") or {}
        advertiser = it.get("advertiser") or {}
        price = parse_float(adv.get("price"))
        seller = advertiser.get("nickName") or advertiser.get("nick_name") or "N/A"
        out.append((price, seller))
    return out

def topN_in_range_first_page(items: List[Dict[str, Any]], n: int,
                             min_req: Optional[float], max_req: Optional[float]) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    for it in items:
        if len(out) >= n:
            break
        adv = it.get("adv") or {}
        advertiser = it.get("advertiser") or {}
        if not rango_superpone_anuncio(adv, min_req, max_req):
            continue
        price = parse_float(adv.get("price"))
        seller = advertiser.get("nickName") or advertiser.get("nick_name") or "N/A"
        out.append((price, seller))
    return out

# -------------------------
# PRINT helper
# -------------------------
def imprimir(titulo: str, fiat: str, side: str, ofertas: List[Tuple[float, str]]):
    print("\n========================")
    print(f"= {titulo} | {fiat} | {side.upper()} =")
    print("========================")
    if not ofertas:
        print("Sin resultados en la primera página con esos criterios.")
        return
    for i, (price, seller) in enumerate(ofertas, 1):
        print(f"[{i:02d}] precio={price} | vendedor={seller}")

# -------------------------
# MAIN
# -------------------------
def main():
    # -------- Colombia (rango COP) --------
    items = fetch_first_page_ui("COP", "BUY")
    col_buy = topN_in_range_first_page(items, TOP_N, RANGO_COP[0], RANGO_COP[1])
    imprimir("Colombia (rango)", "COP", "BUY", col_buy)

    items = fetch_first_page_ui("COP", "SELL")
    col_sell = topN_in_range_first_page(items, TOP_N, RANGO_COP[0], RANGO_COP[1])
    imprimir("Colombia (rango)", "COP", "SELL", col_sell)

    # -------- Perú (rango PEN) --------
    items = fetch_first_page_ui("PEN", "BUY")
    pe_buy = topN_in_range_first_page(items, TOP_N, RANGO_PEN[0], RANGO_PEN[1])
    imprimir("Perú (rango)", "PEN", "BUY", pe_buy)

    items = fetch_first_page_ui("PEN", "SELL")
    pe_sell = topN_in_range_first_page(items, TOP_N, RANGO_PEN[0], RANGO_PEN[1])
    imprimir("Perú (rango)", "PEN", "SELL", pe_sell)

    # -------- USA (Zelle) -> primero filtra por método --------
    items = fetch_first_page_ui_with_method("USD", "BUY", "Zelle")
    us_buy = topN_from_first_page(items, TOP_N)
    imprimir("USA (Zelle)", "USD", "BUY", us_buy)

    items = fetch_first_page_ui_with_method("USD", "SELL", "Zelle")
    us_sell = topN_from_first_page(items, TOP_N)
    imprimir("USA (Zelle)", "USD", "SELL", us_sell)

    # -------- Europa (Bizum) -> primero filtra por método --------
    items = fetch_first_page_ui_with_method("EUR", "BUY", "Bizum")
    eu_buy = topN_from_first_page(items, TOP_N)
    imprimir("Europa (Bizum)", "EUR", "BUY", eu_buy)

    items = fetch_first_page_ui_with_method("EUR", "SELL", "Bizum")
    eu_sell = topN_from_first_page(items, TOP_N)
    imprimir("Europa (Bizum)", "EUR", "SELL", eu_sell)

if __name__ == "__main__":
    main()
