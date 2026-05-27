"""
SINDICATOBOT — Motor de Probabilidades Poisson + Kelly Criterion
================================================================
Módulo independiente que puede usarse desde:
  - main.py (bot Telegram)
  - dashboard HTML (via API endpoint)
  - análisis manual

Uso:
    from poisson_kelly import analizar_partido_completo
    resultado = analizar_partido_completo("México", "Sudáfrica", 1.85, 0.92, momios)
"""

import math
from scipy.stats import poisson

# ══════════════════════════════════════════════════════════════
# MODELO POISSON
# ══════════════════════════════════════════════════════════════

def _tabla_poisson(xg_l, xg_v, max_g=7):
    """Tabla completa de probabilidades de marcadores exactos."""
    tabla = {}
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            tabla[(i, j)] = poisson.pmf(i, xg_l) * poisson.pmf(j, xg_v)
    return tabla


def calcular_probabilidades(xg_local, xg_visita):
    """
    Calcula probabilidades reales de todos los mercados principales
    usando el modelo de Poisson doble independiente.

    Args:
        xg_local:  Expected Goals del equipo local
        xg_visita: Expected Goals del equipo visitante

    Returns:
        dict con probabilidades de cada mercado (0-1)
    """
    if xg_local <= 0:  xg_local  = 0.1
    if xg_visita <= 0: xg_visita = 0.1

    tabla = _tabla_poisson(xg_local, xg_visita)

    # Resultados 1X2
    p_l = sum(p for (g1,g2),p in tabla.items() if g1 > g2)
    p_e = sum(p for (g1,g2),p in tabla.items() if g1 == g2)
    p_v = sum(p for (g1,g2),p in tabla.items() if g2 > g1)

    # Totales de goles
    p_over15 = sum(p for (g1,g2),p in tabla.items() if g1+g2 > 1.5)
    p_over25 = sum(p for (g1,g2),p in tabla.items() if g1+g2 > 2.5)
    p_over35 = sum(p for (g1,g2),p in tabla.items() if g1+g2 > 3.5)
    p_over45 = sum(p for (g1,g2),p in tabla.items() if g1+g2 > 4.5)

    # BTTS
    p_btts = sum(p for (g1,g2),p in tabla.items() if g1 > 0 and g2 > 0)

    # Gol en primer tiempo (aprox 45% del xG ocurre en 1T)
    xg_l_1t = xg_local  * 0.45
    xg_v_1t = xg_visita * 0.45
    p_gol_1t = 1 - poisson.pmf(0, xg_l_1t) * poisson.pmf(0, xg_v_1t)

    # Marcadores exactos top 5
    top_marcadores = sorted(tabla.items(), key=lambda x: x[1], reverse=True)[:5]

    # Double chance
    p_1x = p_l + p_e
    p_x2 = p_e + p_v
    p_12 = p_l + p_v

    return {
        "local":       round(p_l,    4),
        "empate":      round(p_e,    4),
        "visita":      round(p_v,    4),
        "over15":      round(p_over15, 4),
        "under15":     round(1-p_over15, 4),
        "over25":      round(p_over25, 4),
        "under25":     round(1-p_over25, 4),
        "over35":      round(p_over35, 4),
        "under35":     round(1-p_over35, 4),
        "over45":      round(p_over45, 4),
        "btts_si":     round(p_btts,  4),
        "btts_no":     round(1-p_btts, 4),
        "gol_1t":      round(p_gol_1t, 4),
        "dc_1x":       round(p_1x,   4),
        "dc_x2":       round(p_x2,   4),
        "dc_12":       round(p_12,   4),
        "top_marcadores": [(f"{g1}-{g2}", round(p*100,2)) for (g1,g2),p in top_marcadores],
        "xg_local":    xg_local,
        "xg_visita":   xg_visita,
        "xg_total":    round(xg_local + xg_visita, 2),
    }


# ══════════════════════════════════════════════════════════════
# CONVERSORES DE MOMIOS
# ══════════════════════════════════════════════════════════════

def american_to_decimal(ml):
    """Convierte momio americano a decimal."""
    try:
        n = float(str(ml).replace("+", ""))
        return round(n/100+1, 4) if n > 0 else round(100/abs(n)+1, 4)
    except:
        return 2.0

def decimal_to_american(dec):
    """Convierte decimal a formato americano."""
    try:
        d = float(dec)
        return f"+{int((d-1)*100)}" if d >= 2 else f"-{int(100/(d-1))}"
    except:
        return "N/A"

def prob_implicita(ml):
    """Probabilidad implícita del momio (incluye margen del casino)."""
    try:
        n = float(str(ml).replace("+", ""))
        return abs(n)/(abs(n)+100) if n < 0 else 100/(n+100)
    except:
        return 0.5

def prob_to_fair_momio(prob):
    """Momio justo sin margen a partir de una probabilidad."""
    if prob <= 0 or prob >= 1:
        return "N/A"
    dec = 1 / prob
    return decimal_to_american(dec)


# ══════════════════════════════════════════════════════════════
# EDGE Y VALOR ESPERADO
# ══════════════════════════════════════════════════════════════

def calcular_ev(prob_sistema, momio_str, umbral_edge=0.04):
    """
    Calcula el edge y valor esperado de una apuesta.

    Args:
        prob_sistema: probabilidad del sistema (0-1)
        momio_str:    momio en formato americano ("+200", "-130")
        umbral_edge:  edge mínimo para considerar apuesta con valor (default 4%)

    Returns:
        dict con análisis completo de valor
    """
    prob_casino = prob_implicita(momio_str)
    dec         = american_to_decimal(momio_str)
    edge        = prob_sistema - prob_casino
    ev_pct      = (prob_sistema * (dec-1) - (1-prob_sistema))
    ev_por_100  = ev_pct * 100
    tiene_valor = edge >= umbral_edge

    return {
        "prob_sistema": round(prob_sistema * 100, 1),
        "prob_casino":  round(prob_casino  * 100, 1),
        "edge_pct":     round(edge         * 100, 2),
        "ev_pct":       round(ev_pct       * 100, 2),
        "ev_por_100":   round(ev_por_100,          2),
        "ev_por_500":   round(ev_por_100  * 5,     2),
        "ev_por_1000":  round(ev_por_100  * 10,    2),
        "tiene_valor":  tiene_valor,
        "momio_justo":  prob_to_fair_momio(prob_sistema),
        "decimal":      dec,
    }


# ══════════════════════════════════════════════════════════════
# KELLY CRITERION
# ══════════════════════════════════════════════════════════════

def kelly_criterion(prob_sistema, momio_str, fraccion=0.25, bankroll=1000):
    """
    Calcula el tamaño óptimo de apuesta según el Criterio de Kelly.

    El Kelly completo es agresivo. Se recomienda usar Kelly fraccionado
    al 25% para reducir la volatilidad sin perder mucho del edge.

    Args:
        prob_sistema: probabilidad estimada por el sistema (0-1)
        momio_str:    momio en formato americano
        fraccion:     fracción de Kelly a usar (0.25 = 25%, recomendado)
        bankroll:     capital total disponible en pesos/dólares

    Returns:
        dict con tamaños de apuesta recomendados
    """
    dec = american_to_decimal(momio_str)
    b   = dec - 1  # ganancia neta por unidad apostada
    q   = 1 - prob_sistema

    if b <= 0:
        return {"kelly_pct": 0, "apuesta": 0, "razon": "Momio inválido"}

    # Kelly completo: f* = (b*p - q) / b
    k_completo = (b * prob_sistema - q) / b
    k_completo = max(0, k_completo)  # nunca apostar si EV negativo

    # Kelly fraccionado
    k_fraccional = k_completo * fraccion

    apuesta = bankroll * k_fraccional

    # Clasificación de la apuesta
    if k_fraccional > 0.10:
        clasificacion = "🔥 ALTA CONVICCIÓN"
    elif k_fraccional > 0.05:
        clasificacion = "✅ VALOR MODERADO"
    elif k_fraccional > 0.02:
        clasificacion = "⚠️ VALOR MARGINAL"
    else:
        clasificacion = "❌ NO APOSTAR"

    return {
        "kelly_completo_pct":    round(k_completo   * 100, 2),
        "kelly_fraccional_pct":  round(k_fraccional * 100, 2),
        "apuesta_recomendada":   round(apuesta,             2),
        "bankroll_usado":        bankroll,
        "clasificacion":         clasificacion,
        "por_bankroll": {
            500:    round(bankroll * k_fraccional * (500/bankroll),    2) if bankroll else 0,
            1000:   round(1000  * k_fraccional, 2),
            2000:   round(2000  * k_fraccional, 2),
            5000:   round(5000  * k_fraccional, 2),
            10000:  round(10000 * k_fraccional, 2),
        }
    }


# ══════════════════════════════════════════════════════════════
# DETECTOR DE ARBITRAJE
# ══════════════════════════════════════════════════════════════

def detectar_arbitraje(momios_1x2):
    """
    Detecta si hay oportunidad de arbitraje entre los momios 1X2.
    Arbitraje existe cuando la suma de probabilidades implícitas < 100%.

    Args:
        momios_1x2: dict {"local": "+200", "empate": "+300", "visita": "+250"}

    Returns:
        dict con análisis de arbitraje
    """
    p_l = prob_implicita(momios_1x2.get("local",  "+200"))
    p_e = prob_implicita(momios_1x2.get("empate", "+300"))
    p_v = prob_implicita(momios_1x2.get("visita", "+250"))
    suma = p_l + p_e + p_v

    # Margen del casino (overround)
    margen = (suma - 1) * 100

    hay_arb = suma < 1.0

    if hay_arb:
        # Calcular apuestas óptimas para garantizar ganancia
        ganancia_garantizada = (1/suma - 1) * 100
    else:
        ganancia_garantizada = 0

    return {
        "suma_impl":             round(suma * 100, 2),
        "margen_casino_pct":     round(margen,     2),
        "hay_arbitraje":         hay_arb,
        "ganancia_garantizada":  round(ganancia_garantizada, 2),
        "probs": {
            "local":  round(p_l*100, 1),
            "empate": round(p_e*100, 1),
            "visita": round(p_v*100, 1),
        }
    }


# ══════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL — ANÁLISIS COMPLETO DE UN PARTIDO
# ══════════════════════════════════════════════════════════════

MERCADOS_CONFIG = {
    "local":    ("local",   "Victoria Local",      "1X2"),
    "empate":   ("empate",  "Empate",              "1X2"),
    "visita":   ("visita",  "Victoria Visita",     "1X2"),
    "over25":   ("over25",  "Over 2.5 Goles",      "Totales"),
    "under25":  ("under25", "Under 2.5 Goles",     "Totales"),
    "over15":   ("over15",  "Over 1.5 Goles",      "Totales"),
    "over35":   ("over35",  "Over 3.5 Goles",      "Totales"),
    "btts_si":  ("btts_si", "BTTS (Sí)",           "BTTS"),
    "btts_no":  ("btts_no", "BTTS (No)",           "BTTS"),
    "gol_1t":   ("gol_1t",  "Gol en 1er Tiempo",   "Especial"),
    "dc_1x":    ("dc_1x",   "Doble Oportunidad 1X","DC"),
    "dc_x2":    ("dc_x2",   "Doble Oportunidad X2","DC"),
}

def analizar_partido_completo(
    nombre_local, nombre_visita,
    xg_local, xg_visita,
    momios_casino,
    bankroll=1000,
    umbral_edge=0.04,
    fraccion_kelly=0.25
):
    """
    Análisis estadístico completo de un partido.

    Args:
        nombre_local, nombre_visita: nombres de los equipos
        xg_local, xg_visita:        Expected Goals del partido
        momios_casino:               dict con momios en formato americano
        bankroll:                    capital disponible para calcular Kelly
        umbral_edge:                 edge mínimo para recomendar apuesta (default 4%)
        fraccion_kelly:              fracción de Kelly a usar (default 25%)

    Returns:
        dict completo con probabilidades, edges, EVs y Kelly por mercado
    """
    # 1. Calcular probabilidades con Poisson
    probs = calcular_probabilidades(xg_local, xg_visita)

    # 2. Analizar cada mercado disponible
    apuestas_valor = []
    analisis_mercados = {}

    for key_momio, (key_prob, label, categoria) in MERCADOS_CONFIG.items():
        if key_momio not in momios_casino:
            continue
        if key_prob not in probs:
            continue

        prob_sis = probs[key_prob]
        momio    = momios_casino[key_momio]
        dec      = american_to_decimal(momio)

        ev_data  = calcular_ev(prob_sis, momio, umbral_edge)
        k_data   = kelly_criterion(prob_sis, momio, fraccion_kelly, bankroll)

        analisis_mercados[key_momio] = {
            "label":      label,
            "categoria":  categoria,
            "momio":      momio,
            "decimal":    dec,
            **ev_data,
            "kelly":      k_data,
        }

        if ev_data["tiene_valor"]:
            apuestas_valor.append({
                "key":      key_momio,
                "label":    label,
                "momio":    momio,
                "ev":       ev_data["ev_por_100"],
                "edge":     ev_data["edge_pct"],
                "prob_sis": ev_data["prob_sistema"],
                "kelly_pct": k_data["kelly_fraccional_pct"],
                "apuesta":  k_data["apuesta_recomendada"],
                "clasif":   k_data["clasificacion"],
            })

    # Ordenar por EV descendente
    apuestas_valor.sort(key=lambda x: x["ev"], reverse=True)

    # 3. Verificar arbitraje
    arb_data = detectar_arbitraje(momios_casino)

    # 4. Resumen ejecutivo
    top3 = apuestas_valor[:3]

    return {
        "partido":          f"{nombre_local} vs {nombre_visita}",
        "xg_local":         xg_local,
        "xg_visita":        xg_visita,
        "xg_total":         probs["xg_total"],
        "probabilidades":   probs,
        "mercados":         analisis_mercados,
        "apuestas_valor":   apuestas_valor,
        "top3":             top3,
        "arbitraje":        arb_data,
        "marcador_probable": probs["top_marcadores"][0] if probs["top_marcadores"] else ("1-0", 0),
        "top_marcadores":   probs["top_marcadores"],
        "bankroll":         bankroll,
        "total_con_valor":  len(apuestas_valor),
        "margen_casino":    arb_data["margen_casino_pct"],
    }


# ══════════════════════════════════════════════════════════════
# FORMATO TELEGRAM
# ══════════════════════════════════════════════════════════════

def formatear_para_telegram(resultado):
    """Formatea el análisis completo para enviar por Telegram."""
    r   = resultado
    top = r["top3"]
    probs = r["probabilidades"]

    texto = (
        f"📊 <b>ANÁLISIS POISSON — {r['partido']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚽ xG: {r['xg_local']} (local) vs {r['xg_visita']} (visita) = {r['xg_total']} total\n\n"
        f"<b>Probabilidades del sistema:</b>\n"
        f"  Local:  <b>{probs['local']*100:.1f}%</b>  |  "
        f"Empate: <b>{probs['empate']*100:.1f}%</b>  |  "
        f"Visita: <b>{probs['visita']*100:.1f}%</b>\n"
        f"  Over 2.5: <b>{probs['over25']*100:.1f}%</b>  |  "
        f"BTTS: <b>{probs['btts_si']*100:.1f}%</b>  |  "
        f"Gol 1T: <b>{probs['gol_1t']*100:.1f}%</b>\n\n"
        f"🎯 <b>Marcador más probable: {r['marcador_probable'][0]}</b> ({r['marcador_probable'][1]:.1f}%)\n\n"
    )

    if top:
        texto += f"🔥 <b>TOP APUESTAS CON VALOR ({r['total_con_valor']} detectadas):</b>\n━━━━━━━━━━━━━━━━━━\n"
        for i, ap in enumerate(top, 1):
            texto += (
                f"\n<b>{i}. {ap['label']}</b>\n"
                f"   Momio: <b>{ap['momio']}</b> | Prob sistema: {ap['prob_sis']}% | Edge: {ap['edge']:+.1f}%\n"
                f"   EV por $100: <b>${ap['ev']:+.2f}</b>\n"
                f"   Kelly {ap['kelly_pct']:.1f}% → Apostar: <b>${ap['apuesta']:.2f}</b>\n"
                f"   {ap['clasif']}\n"
            )
    else:
        texto += "⚠️ Sin apuestas con valor suficiente (edge ≥ 4%)\n"

    margen = r["margen_casino"]
    texto += f"\n📐 Margen del casino: {margen:.1f}% | "
    if r["arbitraje"]["hay_arbitraje"]:
        texto += f"🚨 ARBITRAJE DETECTADO: +{r['arbitraje']['ganancia_garantizada']:.1f}% garantizado"
    else:
        texto += "Sin arbitraje"

    return texto


# ══════════════════════════════════════════════════════════════
# FUNCIÓN PARA EL BOT — llamada directa desde main.py
# ══════════════════════════════════════════════════════════════

def analisis_rapido_telegram(partido_data):
    """
    Wrapper para usar desde main.py con los datos del partido.
    partido_data: dict del formato PARTIDOS_HOY de main.py
    """
    sl = partido_data.get("stats_local",  {})
    sv = partido_data.get("stats_visita", {})
    m  = partido_data.get("momios_static", {})

    xg_l = sl.get("xg", 1.5)
    xg_v = sv.get("xg", 1.4)

    momios = {
        "local":   m.get("local",   "+200"),
        "empate":  m.get("empate",  "+280"),
        "visita":  m.get("visita",  "+300"),
        "over25":  m.get("over",    "-110"),
        "under25": m.get("under",   "-110"),
        "btts_si": m.get("btts",    "+100"),
    }

    resultado = analizar_partido_completo(
        partido_data.get("local",  "Local"),
        partido_data.get("visita", "Visita"),
        xg_l, xg_v, momios
    )

    return formatear_para_telegram(resultado), resultado
