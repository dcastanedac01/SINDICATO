"""
╔══════════════════════════════════════════════════════════════╗
║      SINDICATOBOT — main.py COMPLETO INTEGRADO v4.2        ║
║  Un solo archivo. Sin dependencias externas propias.        ║
║                                                              ║
║  Incluye:                                                    ║
║    · Modelo Poisson + Kelly Criterion                        ║
║    · Filtro de Valor (EV ≥ 5%)                             ║
║    · CLV Tracker (Closing Line Value)                        ║
║    · Sharp Money Detector                                    ║
║    · Fixtures Mundial 2026                                   ║
║    · ROI Tracker por mercado                                 ║
║    · 6 Ligas via SofaScore                                   ║
║    · Alertas automáticas Telegram                            ║
║    · Dashboard Flask                                         ║
╚══════════════════════════════════════════════════════════════╝

INSTALACIÓN LOCAL:
  pip install flask pyTelegramBotAPI requests scipy numpy
  python3 main.py

VARIABLES DE ENTORNO (Railway / VPS):
  TELEGRAM_TOKEN     → token de @BotFather
  TELEGRAM_CHAT_ID   → tu chat ID personal
  ODDS_API_KEY       → odds-api.io
  ANTHROPIC_API_KEY  → Anthropic Claude
  OPENWEATHER_API_KEY → OpenWeatherMap (opcional)
  PORT               → Railway lo asigna automáticamente
"""

# ══════════════════════════════════════════════════════════════
# IMPORTACIONES ESTÁNDAR
# ══════════════════════════════════════════════════════════════
from flask import Flask, jsonify
from threading import Thread
import telebot
from telebot import types
import requests
import sqlite3
import json
import time
import logging
import os
import sys
import math
from datetime import datetime, timedelta
from scipy.stats import poisson as scipy_poisson

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — Variables de entorno (Railway) o valores directos
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN",    "8685030239:AAHHgvIGNWEm-GzfOW1d-5ecJ7vn5Eg-Mqw")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID",  "8685030239")
ODDS_API_KEY      = os.environ.get("ODDS_API_KEY",
    "9a8fdf02aa5ec29ae88765c4c8f78576dcc48e8e96da746fad0b3ac183ad42d5")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_API_KEY",
    "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")
OPENWEATHER_KEY   = os.environ.get("OPENWEATHER_API_KEY", "3d0ec65a44abc9f16409f45003b71b5")
DB_PATH           = "sindicatobot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("sindicatobot")
bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

# ══════════════════════════════════════════════════════════════
# MÓDULO POISSON + KELLY (integrado)
# ══════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════
# MÓDULOS AVANZADOS (CLV, ROI, Sharp, Mundial, Filtro)
# ══════════════════════════════════════════════════════════════
import sqlite3
import json
import time
import logging
from datetime import datetime, timedelta

log = logging.getLogger("sindicatobot.advanced")
DB_PATH = "sindicatobot.db"

# ══════════════════════════════════════════════════════════════
# 1. FILTRO DE VALOR — Solo apuestas con edge real
# ══════════════════════════════════════════════════════════════

# Configuración global del filtro (editable)
FILTRO_CONFIG = {
    "edge_minimo_pct":  5.0,   # edge mínimo para recomendar (%)
    "ev_minimo_100":    3.0,   # EV mínimo por $100
    "kelly_minimo_pct": 1.0,   # Kelly mínimo para apostar (%)
    "prob_minima_pct": 10.0,   # No apostar si prob < 10% aunque haya edge
    "max_kelly_pct":   15.0,   # Nunca apostar más del 15% del bankroll
}

def filtrar_apuestas_valor(apuestas_lista, config=None):
    """
    Filtra una lista de análisis de apuestas y retorna
    solo las que cumplen el umbral de valor mínimo.
    
    apuestas_lista: lista de dicts con keys:
        mercado, momio, prob_sistema (%), edge_pct, ev_por_100, kelly_pct
    
    Retorna: (apuestas_aprobadas, apuestas_rechazadas, resumen)
    """
    cfg = config or FILTRO_CONFIG
    aprobadas = []
    rechazadas = []

    for ap in apuestas_lista:
        razones_rechazo = []
        
        edge    = ap.get("edge_pct", 0)
        ev      = ap.get("ev_por_100", 0)
        kelly   = ap.get("kelly_pct", 0)
        prob    = ap.get("prob_sistema", 0)
        
        if edge  < cfg["edge_minimo_pct"]:   razones_rechazo.append(f"Edge {edge:.1f}% < {cfg['edge_minimo_pct']}% mínimo")
        if ev    < cfg["ev_minimo_100"]:      razones_rechazo.append(f"EV ${ev:.2f} < ${cfg['ev_minimo_100']} mínimo")
        if kelly < cfg["kelly_minimo_pct"]:   razones_rechazo.append(f"Kelly {kelly:.1f}% < {cfg['kelly_minimo_pct']}% mínimo")
        if prob  < cfg["prob_minima_pct"]:    razones_rechazo.append(f"Prob {prob:.1f}% muy baja (longshot)")
        
        ap_copia = dict(ap)
        if razones_rechazo:
            ap_copia["razones_rechazo"] = razones_rechazo
            rechazadas.append(ap_copia)
        else:
            # Clasificar por convicción
            if kelly > 8 and edge > 10:
                ap_copia["conviccion"] = "ALTA"
                ap_copia["emoji"] = "🔥"
            elif kelly > 4 and edge > 6:
                ap_copia["conviccion"] = "MEDIA"
                ap_copia["emoji"] = "✅"
            else:
                ap_copia["conviccion"] = "MARGINAL"
                ap_copia["emoji"] = "⚠️"
            aprobadas.append(ap_copia)

    # Ordenar aprobadas por EV descendente
    aprobadas.sort(key=lambda x: x.get("ev_por_100", 0), reverse=True)

    resumen = {
        "total":      len(apuestas_lista),
        "aprobadas":  len(aprobadas),
        "rechazadas": len(rechazadas),
        "config":     cfg,
        "alta_conviccion":   sum(1 for a in aprobadas if a.get("conviccion") == "ALTA"),
        "media_conviccion":  sum(1 for a in aprobadas if a.get("conviccion") == "MEDIA"),
    }
    return aprobadas, rechazadas, resumen


def formatear_filtro_telegram(aprobadas, resumen, partido_nombre):
    """Formatea el resultado del filtro para Telegram."""
    if not aprobadas:
        return (f"📊 <b>{partido_nombre}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Sin apuestas con valor suficiente\n"
                f"Edge mínimo requerido: {resumen['config']['edge_minimo_pct']}%\n"
                f"EV mínimo requerido: ${resumen['config']['ev_minimo_100']}\n\n"
                f"Analizadas: {resumen['total']} apuestas → {resumen['rechazadas']} rechazadas")

    texto = (f"📊 <b>APUESTAS CON VALOR — {partido_nombre}</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"✅ {resumen['aprobadas']} de {resumen['total']} apuestas superan el filtro\n\n")

    for i, ap in enumerate(aprobadas[:3], 1):
        texto += (f"{ap['emoji']} <b>{ap['mercado']}</b>\n"
                  f"   Momio: {ap['momio']} | Prob sistema: {ap['prob_sistema']:.1f}%\n"
                  f"   Edge: {ap['edge_pct']:+.1f}% | EV: ${ap['ev_por_100']:+.2f}/100\n"
                  f"   Kelly: {ap['kelly_pct']:.1f}% → Apostar: ${ap.get('apuesta', 0):.2f}\n"
                  f"   Convicción: <b>{ap['conviccion']}</b>\n\n")
    return texto


# ══════════════════════════════════════════════════════════════
# 2. CLV TRACKER — Closing Line Value
# ══════════════════════════════════════════════════════════════
"""
CLV = diferencia entre el momio al que apostaste y el momio de cierre.
Si apostaste MEJOR que el cierre = tu modelo está funcionando.
Si el mercado siempre cierra mejor = el casino te está ganando.

Ejemplo positivo:
    Apostaste Over 2.5 a -110 (impl 52.4%)
    Cerró a -130 (impl 56.5%)
    CLV = +4.1% → bien, anticipaste el movimiento
    
Ejemplo negativo:
    Apostaste Under 2.5 a +100 (impl 50%)
    Cerró a -115 (impl 53.5%)
    CLV = -3.5% → el mercado se movió en tu contra
"""

def inicializar_clv_tabla():
    """Crea la tabla CLV en SQLite si no existe."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS clv_tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        partido_id TEXT,
        partido_nombre TEXT,
        liga TEXT DEFAULT '',
        fecha_partido TEXT,
        mercado TEXT,
        momio_apostado TEXT,
        prob_apostado REAL,
        momio_cierre TEXT DEFAULT NULL,
        prob_cierre REAL DEFAULT NULL,
        clv_pct REAL DEFAULT NULL,
        resultado TEXT DEFAULT NULL,
        ganancia_real REAL DEFAULT NULL,
        bankroll_usado REAL DEFAULT 0,
        apuesta_monto REAL DEFAULT 0,
        notas TEXT DEFAULT '',
        ts_apuesta TEXT DEFAULT CURRENT_TIMESTAMP,
        ts_cierre TEXT DEFAULT NULL
    )""")
    conn.commit()
    conn.close()


def registrar_apuesta_clv(partido_id, partido_nombre, liga, fecha,
                            mercado, momio_apostado, monto_apostado, bankroll):
    """
    Registra una apuesta al momento de realizarla.
    El CLV se calcula después cuando se actualiza el momio de cierre.
    """
    prob = prob_implicita(momio_apostado)
    
    inicializar_clv_tabla()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO clv_tracker
        (partido_id, partido_nombre, liga, fecha_partido, mercado,
         momio_apostado, prob_apostado, apuesta_monto, bankroll_usado)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (partido_id, partido_nombre, liga, fecha, mercado,
         momio_apostado, prob, monto_apostado, bankroll))
    nuevo_id = c.lastrowid
    conn.commit()
    conn.close()
    log.info(f"CLV registrada: {mercado} @ {momio_apostado} — ID {nuevo_id}")
    return nuevo_id


def actualizar_cierre_clv(apuesta_id, momio_cierre, resultado=None, ganancia=None):
    """
    Actualiza el momio de cierre y calcula el CLV.
    Llamar cuando el partido esté a punto de empezar (momio final).
    """
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT momio_apostado, prob_apostado FROM clv_tracker WHERE id=?", (apuesta_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    
    momio_ap, prob_ap = row
    prob_cierre = prob_implicita(momio_cierre)
    
    # CLV = prob al apostar - prob al cierre
    # Positivo = apostaste mejor que el mercado final
    clv = (prob_ap - prob_cierre) * 100
    
    c.execute("""UPDATE clv_tracker SET
        momio_cierre=?, prob_cierre=?, clv_pct=?,
        resultado=?, ganancia_real=?, ts_cierre=?
        WHERE id=?""",
        (momio_cierre, prob_cierre, round(clv, 2),
         resultado, ganancia, datetime.now().isoformat(), apuesta_id))
    conn.commit()
    conn.close()
    
    log.info(f"CLV actualizado ID {apuesta_id}: {momio_ap} → {momio_cierre} | CLV {clv:+.2f}%")
    return round(clv, 2)


def generar_reporte_clv(limite=50):
    """Genera reporte completo del CLV histórico."""
    inicializar_clv_tabla()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Stats generales
    c.execute("SELECT COUNT(*) FROM clv_tracker")
    total = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM clv_tracker WHERE clv_pct IS NOT NULL")
    con_cierre = c.fetchone()[0]
    
    c.execute("SELECT AVG(clv_pct) FROM clv_tracker WHERE clv_pct IS NOT NULL")
    clv_prom = c.fetchone()[0] or 0
    
    c.execute("SELECT COUNT(*) FROM clv_tracker WHERE clv_pct > 0")
    clv_positivo = c.fetchone()[0]
    
    c.execute("SELECT AVG(ganancia_real) FROM clv_tracker WHERE ganancia_real IS NOT NULL")
    ganancia_prom = c.fetchone()[0] or 0
    
    c.execute("SELECT SUM(ganancia_real) FROM clv_tracker WHERE ganancia_real IS NOT NULL")
    ganancia_total = c.fetchone()[0] or 0
    
    # Por mercado
    c.execute("""SELECT mercado, COUNT(*) as n, AVG(clv_pct) as clv_avg,
                 SUM(ganancia_real) as ganancia
                 FROM clv_tracker WHERE clv_pct IS NOT NULL
                 GROUP BY mercado ORDER BY clv_avg DESC""")
    por_mercado = c.fetchall()
    
    # Últimas apuestas
    c.execute("""SELECT partido_nombre, mercado, momio_apostado, momio_cierre,
                 clv_pct, resultado, ganancia_real, ts_apuesta
                 FROM clv_tracker ORDER BY ts_apuesta DESC LIMIT 10""")
    ultimas = c.fetchall()
    
    conn.close()
    
    return {
        "total":           total,
        "con_cierre":      con_cierre,
        "clv_promedio":    round(clv_prom, 2),
        "clv_positivos":   clv_positivo,
        "pct_clv_pos":     round(clv_positivo/con_cierre*100, 1) if con_cierre else 0,
        "ganancia_prom":   round(ganancia_prom, 2),
        "ganancia_total":  round(ganancia_total, 2),
        "por_mercado":     por_mercado,
        "ultimas":         ultimas,
        "modelo_ok":       clv_prom > 0,  # CLV positivo = modelo anticipa el mercado
    }


def formatear_clv_telegram(reporte):
    """Formatea reporte CLV para Telegram."""
    r = reporte
    modelo_emoji = "🟢" if r["modelo_ok"] else "🔴"
    
    texto = (f"📐 <b>CLV TRACKER — SINDICATO</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"Apuestas registradas: {r['total']}\n"
             f"Con momio de cierre: {r['con_cierre']}\n\n"
             f"{modelo_emoji} <b>CLV promedio: {r['clv_promedio']:+.2f}%</b>\n"
             f"CLV positivos: {r['clv_positivos']}/{r['con_cierre']} ({r['pct_clv_pos']:.1f}%)\n\n"
             f"💰 Ganancia total: ${r['ganancia_total']:+.2f}\n"
             f"   Promedio/apuesta: ${r['ganancia_prom']:+.2f}\n\n")
    
    if r["por_mercado"]:
        texto += "<b>CLV por mercado:</b>\n"
        for mercado, n, clv_avg, gan in r["por_mercado"][:5]:
            ic = "✅" if (clv_avg or 0) > 0 else "❌"
            texto += (f"  {ic} {mercado}: CLV {(clv_avg or 0):+.1f}% "
                     f"| ${(gan or 0):+.2f} ({n} aps)\n")
    
    if r["modelo_ok"]:
        texto += "\n🎯 <i>CLV positivo = tu modelo anticipa el mercado correctamente.</i>"
    else:
        texto += "\n⚠️ <i>CLV negativo = el mercado se mueve en tu contra. Revisar el modelo.</i>"
    
    return texto


# ══════════════════════════════════════════════════════════════
# 3. SHARP MONEY DETECTOR — Mejorado
# ══════════════════════════════════════════════════════════════

_historial_momios = {}  # {partido_id: [(ts, momio_l, momio_e, momio_v)]}

def registrar_snapshot_momios(partido_id, partido_nombre, momios_dict):
    """
    Registra un snapshot de momios para detectar movimientos.
    Llamar cada vez que se obtienen momios frescos.
    """
    ts = time.time()
    if partido_id not in _historial_momios:
        _historial_momios[partido_id] = []
    
    _historial_momios[partido_id].append({
        "ts":     ts,
        "hora":   datetime.now().strftime("%H:%M"),
        "momios": dict(momios_dict)
    })
    
    # Guardar solo las últimas 20 snapshots
    if len(_historial_momios[partido_id]) > 20:
        _historial_momios[partido_id].pop(0)
    
    # Analizar movimiento vs snapshot anterior
    historial = _historial_momios[partido_id]
    if len(historial) < 2:
        return None
    
    return analizar_movimiento_sharp(partido_id, partido_nombre, historial)


def analizar_movimiento_sharp(partido_id, partido_nombre, historial):
    """
    Analiza si hay movimiento significativo de momios (sharp money).
    
    Señales de sharp money:
    1. Momio cae rápido (>10 pts en <30 min)
    2. Cae en múltiples casas simultáneamente
    3. El volumen implícito aumenta sin noticias visibles
    """
    if len(historial) < 2:
        return None
    
    snap_old = historial[-2]
    snap_new = historial[-1]
    tiempo_min = (snap_new["ts"] - snap_old["ts"]) / 60
    
    
    alertas = []
    for campo in ["local", "empate", "visita"]:
        ml_old = snap_old["momios"].get(campo)
        ml_new = snap_new["momios"].get(campo)
        if not ml_old or not ml_new:
            continue
        try:
            d_old = american_to_decimal(ml_old)
            d_new = american_to_decimal(ml_new)
            diff_pts = (float(str(ml_new).replace("+","")) - 
                       float(str(ml_old).replace("+","")))
            prob_old = prob_implicita(ml_old) * 100
            prob_new = prob_implicita(ml_new) * 100
            diff_prob = prob_new - prob_old
            
            # Nivel de alerta
            nivel = None
            if abs(diff_pts) >= 20:
                nivel = "🚨 FUERTE"
            elif abs(diff_pts) >= 10:
                nivel = "⚡ MODERADO"
            elif abs(diff_pts) >= 5 and tiempo_min < 10:
                nivel = "⚠️ RÁPIDO"
            
            if nivel:
                direccion = "CRECIMIENTO" if diff_prob > 0 else "CAÍDA"
                etiqueta = {"local":"Local","empate":"Empate","visita":"Visita"}[campo]
                alertas.append({
                    "campo":      campo,
                    "etiqueta":   etiqueta,
                    "ml_old":     ml_old,
                    "ml_new":     ml_new,
                    "diff_pts":   round(diff_pts, 1),
                    "diff_prob":  round(diff_prob, 1),
                    "tiempo_min": round(tiempo_min, 1),
                    "nivel":      nivel,
                    "direccion":  direccion,
                    "interpretacion": _interpretar_movimiento(campo, diff_prob, tiempo_min),
                })
        except:
            continue
    
    if not alertas:
        return None
    
    return {
        "partido_id":   partido_id,
        "partido":      partido_nombre,
        "hora":         snap_new["hora"],
        "alertas":      alertas,
        "n_alertas":    len(alertas),
        "es_sharp":     len(alertas) >= 2 or any(a["nivel"] == "🚨 FUERTE" for a in alertas),
    }


def _interpretar_movimiento(campo, diff_prob, tiempo_min):
    """Interpreta qué significa el movimiento detectado."""
    if campo == "local" and diff_prob > 5:
        return "Dinero entrando al local — posible info de alineación o baja del visitante"
    if campo == "visita" and diff_prob > 5:
        return "Dinero entrando al visitante — revisar posible baja del local"
    if campo == "empate" and diff_prob > 3:
        return "Mercado esperando partido cerrado — posible táctica conservadora"
    if diff_prob < -5:
        return "Dinero alejándose de esta selección — posible baja o problema físico"
    if tiempo_min < 5:
        return "Movimiento muy rápido — posible leak de alineación confirmada"
    return "Movimiento de mercado normal — monitorear próximos snapshots"


def formatear_sharp_telegram(resultado):
    """Formatea alerta de sharp money para Telegram."""
    if not resultado:
        return None
    
    r = resultado
    nivel_max = "🚨" if r["es_sharp"] else "⚡"
    
    texto = (f"{nivel_max} <b>{'SHARP MONEY' if r['es_sharp'] else 'MOVIMIENTO'} DETECTADO</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"⚽ <b>{r['partido']}</b>\n"
             f"🕐 {r['hora']} | {r['n_alertas']} mercado(s) afectados\n\n")
    
    for a in r["alertas"]:
        flecha = "📈" if a["diff_prob"] > 0 else "📉"
        texto += (f"{flecha} <b>{a['etiqueta']}:</b> "
                 f"{a['ml_old']} → <b>{a['ml_new']}</b> "
                 f"({'+' if a['diff_pts']>0 else ''}{a['diff_pts']:.0f}pts)\n"
                 f"   Prob: {'+' if a['diff_prob']>0 else ''}{a['diff_prob']:.1f}% | "
                 f"{a['tiempo_min']:.0f} min | {a['nivel']}\n"
                 f"   💡 {a['interpretacion']}\n\n")
    
    if r["es_sharp"]:
        texto += "🎯 <i>Movimiento sharp confirmado. Revisar valor en mercados opuestos.</i>"
    
    return texto


# ══════════════════════════════════════════════════════════════
# 4. FIXTURES MUNDIAL 2026 COMPLETOS
# ══════════════════════════════════════════════════════════════

MUNDIAL_2026_FIXTURES = [
    # ── GRUPO A — México ──────────────────────────────────────
    {"id":"A1", "grupo":"A", "fase":"J1", "fecha":"2026-06-11", "hora_cst":"13:00",
     "local":"México",    "visita":"Sudáfrica",   "estadio":"Estadio Azteca",
     "ciudad":"Ciudad de México, MX", "altitud":2240, "tv":"Azteca/TUDN/Peacock",
     "xg_l":1.85, "xg_v":0.92,
     "momios":{"local":"-200","empate":"+340","visita":"+550","over":"-118","under":"+100","ou":"2.5","btts":"+120"}},
    {"id":"A2", "grupo":"A", "fase":"J2", "fecha":"2026-06-17", "hora_cst":"TBD",
     "local":"México",    "visita":"Corea del Sur","estadio":"SoFi Stadium",
     "ciudad":"Inglewood, CA", "altitud":30, "tv":"Azteca/TUDN",
     "xg_l":1.72, "xg_v":1.15,
     "momios":{"local":"-165","empate":"+310","visita":"+450","over":"-120","under":"+100","ou":"2.5","btts":"-105"}},
    {"id":"A3", "grupo":"A", "fase":"J3", "fecha":"2026-06-23", "hora_cst":"TBD",
     "local":"Rep. Checa","visita":"México",       "estadio":"Por confirmar",
     "ciudad":"TBD", "altitud":0, "tv":"Por confirmar",
     "xg_l":1.20, "xg_v":1.68,
     "momios":{"local":"+200","empate":"+260","visita":"-130","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    # ── GRUPO J — Argentina ────────────────────────────────────
    {"id":"J1", "grupo":"J", "fase":"J1", "fecha":"2026-06-12", "hora_cst":"TBD",
     "local":"Argentina", "visita":"Argelia",      "estadio":"AT&T Stadium",
     "ciudad":"Arlington, TX", "altitud":185, "tv":"TelevisaUnivision",
     "xg_l":2.20, "xg_v":0.78,
     "momios":{"local":"-280","empate":"+400","visita":"+750","over":"-130","under":"+110","ou":"2.5","btts":"+130"}},
    {"id":"J2", "grupo":"J", "fase":"J2", "fecha":"2026-06-18", "hora_cst":"TBD",
     "local":"Argentina", "visita":"Austria",      "estadio":"Levi's Stadium",
     "ciudad":"Santa Clara, CA", "altitud":14, "tv":"TelevisaUnivision",
     "xg_l":2.05, "xg_v":0.92,
     "momios":{"local":"-250","empate":"+380","visita":"+680","over":"-120","under":"+100","ou":"2.5","btts":"+110"}},
    # ── GRUPO H — España ────────────────────────────────────────
    {"id":"H1", "grupo":"H", "fase":"J1", "fecha":"2026-06-12", "hora_cst":"TBD",
     "local":"España",    "visita":"Uruguay",      "estadio":"Gillette Stadium",
     "ciudad":"Foxborough, MA", "altitud":12, "tv":"Telemundo",
     "xg_l":2.10, "xg_v":1.05,
     "momios":{"local":"-180","empate":"+320","visita":"+480","over":"-105","under":"-115","ou":"2.5","btts":"-110"}},
    {"id":"H2", "grupo":"H", "fase":"J2", "fecha":"2026-06-18", "hora_cst":"TBD",
     "local":"Arabia S.", "visita":"España",       "estadio":"Hard Rock Stadium",
     "ciudad":"Miami, FL", "altitud":2, "tv":"Telemundo",
     "xg_l":0.85, "xg_v":2.25,
     "momios":{"local":"+500","empate":"+360","visita":"-220","over":"-115","under":"-105","ou":"2.5","btts":"+130"}},
    # ── GRUPO D — USA ──────────────────────────────────────────
    {"id":"D1", "grupo":"D", "fase":"J1", "fecha":"2026-06-12", "hora_cst":"TBD",
     "local":"USA",       "visita":"Bosnia-H",     "estadio":"MetLife Stadium",
     "ciudad":"East Rutherford, NJ", "altitud":6, "tv":"Fox/Telemundo",
     "xg_l":1.72, "xg_v":1.05,
     "momios":{"local":"-140","empate":"+320","visita":"+380","over":"-115","under":"-105","ou":"2.5","btts":"-120"}},
    # ── GRUPO C — Brasil ───────────────────────────────────────
    {"id":"C1", "grupo":"C", "fase":"J1", "fecha":"2026-06-13", "hora_cst":"TBD",
     "local":"Brasil",    "visita":"Marruecos",    "estadio":"Levi's Stadium",
     "ciudad":"Santa Clara, CA", "altitud":14, "tv":"TelevisaUnivision",
     "xg_l":2.00, "xg_v":0.95,
     "momios":{"local":"-175","empate":"+350","visita":"+480","over":"-110","under":"-110","ou":"2.5","btts":"-120"}},
    # ── GRUPO L — Inglaterra ───────────────────────────────────
    {"id":"L1", "grupo":"L", "fase":"J1", "fecha":"2026-06-13", "hora_cst":"TBD",
     "local":"Inglaterra","visita":"Croacia",      "estadio":"Lincoln Financial Field",
     "ciudad":"Filadelfia, PA", "altitud":12, "tv":"Fox/Telemundo",
     "xg_l":2.05, "xg_v":1.00,
     "momios":{"local":"-185","empate":"+330","visita":"+500","over":"-115","under":"-105","ou":"2.5","btts":"-115"}},
    # ── GRUPO F — Portugal ─────────────────────────────────────
    {"id":"F1", "grupo":"F", "fase":"J1", "fecha":"2026-06-14", "hora_cst":"TBD",
     "local":"Portugal",  "visita":"Polonia",      "estadio":"Arrowhead Stadium",
     "ciudad":"Kansas City, MO", "altitud":315, "tv":"Telemundo",
     "xg_l":1.95, "xg_v":1.10,
     "momios":{"local":"-180","empate":"+330","visita":"+480","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    # ── GRUPO G — Italia ───────────────────────────────────────
    {"id":"G1", "grupo":"G", "fase":"J1", "fecha":"2026-06-15", "hora_cst":"TBD",
     "local":"Italia",    "visita":"Colombia",     "estadio":"NRG Stadium",
     "ciudad":"Houston, TX", "altitud":15, "tv":"Telemundo",
     "xg_l":1.75, "xg_v":1.45,
     "momios":{"local":"-145","empate":"+300","visita":"+400","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    # ── GRUPO E — Países Bajos ─────────────────────────────────
    {"id":"E1", "grupo":"E", "fase":"J1", "fecha":"2026-06-15", "hora_cst":"TBD",
     "local":"Países Bajos","visita":"Suecia",     "estadio":"SoFi Stadium",
     "ciudad":"Inglewood, CA", "altitud":30, "tv":"Fox/Telemundo",
     "xg_l":1.90, "xg_v":1.05,
     "momios":{"local":"-160","empate":"+330","visita":"+440","over":"-110","under":"-110","ou":"2.5","btts":"-110"}},
    # ── GRUPO B — Canadá ───────────────────────────────────────
    {"id":"B1", "grupo":"B", "fase":"J1", "fecha":"2026-06-13", "hora_cst":"TBD",
     "local":"Canadá",    "visita":"Bosnia-H",     "estadio":"BC Place",
     "ciudad":"Vancouver, Canadá", "altitud":5, "tv":"TSN/Telemundo",
     "xg_l":1.45, "xg_v":1.20,
     "momios":{"local":"-125","empate":"+280","visita":"+340","over":"-115","under":"-105","ou":"2.5","btts":"-115"}},
    # ── FASE FINAL (proyección) ────────────────────────────────
    {"id":"R16_1", "grupo":"—", "fase":"Dieciseisavos", "fecha":"2026-06-29", "hora_cst":"TBD",
     "local":"1ro Grupo A","visita":"3ro Mejor",   "estadio":"MetLife Stadium",
     "ciudad":"East Rutherford, NJ", "altitud":6, "tv":"Fox/Telemundo",
     "xg_l":0, "xg_v":0,
     "momios":{"local":"-110","empate":"+275","visita":"+280","over":"-110","under":"-110","ou":"2.5","btts":"-110"}},
    {"id":"FINAL", "grupo":"—", "fase":"Final", "fecha":"2026-07-19", "hora_cst":"TBD",
     "local":"Por definir","visita":"Por definir", "estadio":"MetLife Stadium",
     "ciudad":"East Rutherford, NJ", "altitud":6, "tv":"Fox/Telemundo/Azteca",
     "xg_l":0, "xg_v":0,
     "momios":{"local":"-110","empate":"+275","visita":"+280","over":"-110","under":"-110","ou":"2.5","btts":"-110"}},
]

def get_fixtures_grupo(grupo):
    return [f for f in MUNDIAL_2026_FIXTURES if f["grupo"] == grupo]

def get_fixtures_fecha(fecha_str):
    return [f for f in MUNDIAL_2026_FIXTURES if f["fecha"] == fecha_str]

def get_fixture_por_equipo(nombre_equipo):
    nombre_lower = nombre_equipo.lower()
    return [f for f in MUNDIAL_2026_FIXTURES
            if nombre_lower in f["local"].lower() or nombre_lower in f["visita"].lower()]

def formatear_fixtures_grupo_telegram(grupo):
    partidos = get_fixtures_grupo(grupo)
    if not partidos:
        return f"Sin fixtures para Grupo {grupo}"
    
    texto = f"🌎 <b>GRUPO {grupo} — Mundial 2026</b>\n━━━━━━━━━━━━━━━━━━\n"
    for p in partidos:
        alt = f" | ⛰️{p['altitud']}m" if p["altitud"] > 1800 else ""
        texto += (f"\n⚽ <b>{p['local']} vs {p['visita']}</b>\n"
                 f"   📅 {p['fecha']} · {p['hora_cst']} CST\n"
                 f"   🏟 {p['estadio']}{alt}\n"
                 f"   💰 {p['momios']['local']} / {p['momios']['empate']} / {p['momios']['visita']}\n")
    return texto


# ══════════════════════════════════════════════════════════════
# 5. ROI TRACKER POR MERCADO
# ══════════════════════════════════════════════════════════════

def inicializar_roi_tabla():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS roi_tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        partido TEXT,
        liga TEXT DEFAULT '',
        mercado TEXT,
        momio TEXT,
        monto REAL,
        resultado TEXT,
        ganancia REAL,
        bankroll_inicio REAL DEFAULT 0,
        bankroll_fin REAL DEFAULT 0,
        notas TEXT DEFAULT '',
        ts TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()


def registrar_resultado_roi(partido, liga, mercado, momio, monto, resultado, bankroll_inicio):
    """
    Registra el resultado de una apuesta en el tracker de ROI.
    resultado: 'W' (ganó), 'L' (perdió), 'P' (push/void)
    """
    
    dec = american_to_decimal(momio)
    if resultado == "W":
        ganancia = monto * (dec - 1)
    elif resultado == "L":
        ganancia = -monto
    else:  # Push
        ganancia = 0
    
    bankroll_fin = bankroll_inicio + ganancia
    
    inicializar_roi_tabla()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO roi_tracker
        (fecha, partido, liga, mercado, momio, monto, resultado, ganancia,
         bankroll_inicio, bankroll_fin)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().strftime("%Y-%m-%d"), partido, liga, mercado, momio,
         monto, resultado, round(ganancia, 2), bankroll_inicio, round(bankroll_fin, 2)))
    conn.commit()
    conn.close()
    
    log.info(f"ROI: {partido} | {mercado} @ {momio} | {resultado} | ${ganancia:+.2f}")
    return round(ganancia, 2)


def generar_reporte_roi():
    """Genera reporte completo de ROI por mercado."""
    inicializar_roi_tabla()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Stats generales
    c.execute("SELECT COUNT(*), SUM(ganancia), SUM(monto) FROM roi_tracker")
    total, gan_total, monto_total = c.fetchone()
    total = total or 0
    gan_total = gan_total or 0
    monto_total = monto_total or 0
    
    c.execute("SELECT COUNT(*) FROM roi_tracker WHERE resultado='W'")
    ganadas = c.fetchone()[0] or 0
    
    # ROI por mercado
    c.execute("""SELECT mercado,
                 COUNT(*) as n,
                 COUNT(CASE WHEN resultado='W' THEN 1 END) as wins,
                 SUM(ganancia) as gan,
                 SUM(monto) as inv
                 FROM roi_tracker
                 GROUP BY mercado ORDER BY gan DESC""")
    por_mercado = c.fetchall()
    
    # ROI por liga
    c.execute("""SELECT liga,
                 COUNT(*) as n,
                 SUM(ganancia) as gan,
                 SUM(monto) as inv
                 FROM roi_tracker WHERE liga != ''
                 GROUP BY liga ORDER BY gan DESC""")
    por_liga = c.fetchall()
    
    # Últimas 10 apuestas
    c.execute("""SELECT fecha, partido, mercado, momio, monto, resultado, ganancia
                 FROM roi_tracker ORDER BY ts DESC LIMIT 10""")
    ultimas = c.fetchall()
    
    # Mejor racha actual
    c.execute("SELECT resultado FROM roi_tracker ORDER BY ts DESC LIMIT 20")
    results = [r[0] for r in c.fetchall()]
    racha = 0
    for r in results:
        if r == "W": racha += 1
        else: break
    
    conn.close()
    
    roi_pct = (gan_total / monto_total * 100) if monto_total > 0 else 0
    pct_acierto = (ganadas / total * 100) if total > 0 else 0
    
    return {
        "total":         total,
        "ganadas":       ganadas,
        "pct_acierto":   round(pct_acierto, 1),
        "monto_total":   round(monto_total, 2),
        "ganancia_total":round(gan_total, 2),
        "roi_pct":       round(roi_pct, 2),
        "por_mercado":   por_mercado,
        "por_liga":      por_liga,
        "ultimas":       ultimas,
        "racha_actual":  racha,
    }


def formatear_roi_telegram(reporte):
    """Formatea reporte ROI para Telegram."""
    r = reporte
    roi_color = "🟢" if r["roi_pct"] > 0 else "🔴"
    
    # Barra visual de ROI
    roi_abs = min(abs(r["roi_pct"]), 30)
    barras = "█" * int(roi_abs / 3) + "░" * (10 - int(roi_abs / 3))
    
    texto = (f"📊 <b>ROI TRACKER — SINDICATO</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"Total apuestas: <b>{r['total']}</b>\n"
             f"Ganadas: <b>{r['ganadas']}</b> ({r['pct_acierto']:.1f}%)\n"
             f"Invertido: <b>${r['monto_total']:.2f}</b>\n"
             f"Ganancia neta: <b>${r['ganancia_total']:+.2f}</b>\n\n"
             f"{roi_color} <b>ROI: {r['roi_pct']:+.2f}%</b>\n"
             f"[{barras}]\n")
    
    if r["racha_actual"] > 1:
        texto += f"🔥 Racha actual: <b>{r['racha_actual']} victorias</b>\n"
    
    if r["por_mercado"]:
        texto += f"\n<b>ROI por mercado:</b>\n"
        for mercado, n, wins, gan, inv in r["por_mercado"][:6]:
            roi_m = (gan/inv*100) if inv else 0
            ic = "✅" if roi_m > 0 else "❌"
            pct_w = (wins/n*100) if n else 0
            texto += (f"  {ic} {mercado}: ROI {roi_m:+.1f}% "
                     f"| {wins}/{n} ({pct_w:.0f}%) "
                     f"| ${(gan or 0):+.2f}\n")
    
    if r["por_liga"]:
        texto += f"\n<b>ROI por liga:</b>\n"
        for liga, n, gan, inv in r["por_liga"][:4]:
            roi_l = (gan/inv*100) if inv else 0
            ic = "✅" if roi_l > 0 else "❌"
            texto += f"  {ic} {liga}: ROI {roi_l:+.1f}% | ${(gan or 0):+.2f}\n"
    
    return texto


# ══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════
def inicializar_bd():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    tablas = [
        """CREATE TABLE IF NOT EXISTS partidos (
            id TEXT PRIMARY KEY, fecha TEXT, local TEXT, visita TEXT,
            liga TEXT DEFAULT '', fase TEXT DEFAULT '', hora_cst TEXT DEFAULT '',
            estadio TEXT DEFAULT '', altitud INTEGER DEFAULT 0,
            goles_local INTEGER DEFAULT -1, goles_visita INTEGER DEFAULT -1,
            arbitro TEXT DEFAULT '', resultado TEXT DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS predicciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT, partido_nombre TEXT, liga TEXT DEFAULT '',
            mercado TEXT, momio TEXT, confianza INTEGER, razonamiento TEXT,
            ev_estimado REAL DEFAULT 0, resultado_real TEXT DEFAULT NULL,
            fue_correcto INTEGER DEFAULT NULL, ganancia_real REAL DEFAULT NULL,
            ts TEXT DEFAULT CURRENT_TIMESTAMP, ts_resultado TEXT DEFAULT NULL)""",
        """CREATE TABLE IF NOT EXISTS alertas_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT, partido_nombre TEXT, mensaje TEXT,
            enviada INTEGER DEFAULT 0, ts TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT UNIQUE, partido_nombre TEXT, liga TEXT DEFAULT '',
            fecha TEXT, goles_local INTEGER, goles_visita INTEGER,
            total_goles INTEGER, resultado TEXT,
            over_25 INTEGER, btts INTEGER,
            registrado_en TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS momios_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT, partido_nombre TEXT, bookmaker TEXT,
            local TEXT, empate TEXT, visita TEXT,
            over_val TEXT, under_val TEXT, ou_linea REAL,
            nota TEXT DEFAULT '', ts TEXT DEFAULT CURRENT_TIMESTAMP)""",
    ]
    for t in tablas:
        c.execute(t)
    conn.commit()
    conn.close()
    log.info("Base de datos lista")


# ══════════════════════════════════════════════════════════════
# LIGAS Y PARTIDOS
# ══════════════════════════════════════════════════════════════
LIGAS = {
    "liga_mx":   {"nombre":"Liga MX",          "emoji":"MX","sofa_id":11621,"odds_sport":"soccer_mexico_ligamx"},
    "la_liga":   {"nombre":"La Liga",          "emoji":"ES","sofa_id":8,    "odds_sport":"soccer_spain_la_liga"},
    "champions": {"nombre":"Champions League", "emoji":"CL","sofa_id":7,    "odds_sport":"soccer_uefa_champs_league"},
    "europa":    {"nombre":"Europa League",    "emoji":"EL","sofa_id":679,  "odds_sport":"soccer_uefa_europa_league"},
    "serie_a":   {"nombre":"Serie A",          "emoji":"IT","sofa_id":23,   "odds_sport":"soccer_italy_serie_a"},
    "premier":   {"nombre":"Premier League",   "emoji":"EN","sofa_id":17,   "odds_sport":"soccer_epl"},
}

PARTIDOS_HOY = [
    {
        "id":"final_clausura_26",
        "liga":"Liga MX — Clausura 2026","liga_key":"liga_mx",
        "fase":"GRAN FINAL",
        "local":"Pumas UNAM","visita":"Cruz Azul",
        "estadio":"Por confirmar","ciudad":"Por confirmar","altitud":0,
        "hora_cst":"Por confirmar","fecha":"2026-06-01",
        "fecha_label":"Próximamente","tv":"Azteca / Canal 5",
        "odds_sport":"soccer_mexico_ligamx",
        "contexto":"Pumas clasificó tras vencer 1-0 a Pachuca (Carrillo 55'). Cruz Azul eliminó a Chivas. Final inédita del Clausura 2026.",
        "momios_static":{"local":"-120","empate":"+280","visita":"+290","over":"-110","under":"-110","ou":"2.5","btts":"-115"},
        "h2h":{"local":3,"empate":2,"visita":4,"prom_goles":2.8},
        "stats_local": {"xg":1.88,"goles_f":1.83,"goles_c":1.0,"corners":5.1,"tarjetas":1.8,"posesion":52},
        "stats_visita":{"xg":1.95,"goles_f":2.2,"goles_c":1.0,"corners":5.8,"tarjetas":2.4,"posesion":52},
        "arbitro":{"nombre":"Por confirmar","amarillas":4.5,"rojas":0.2,"penales":0.3},
        "bajas":[],"forma_local":["G","G","E","G","G"],"forma_visita":["G","G","P","G","G"],
    },
]


# ══════════════════════════════════════════════════════════════
# SOFASCORE — AGENDA MULTI-LIGA
# ══════════════════════════════════════════════════════════════
SOFA_H = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}
_agenda_cache = {"ts": 0, "data": []}

def sofa_get(path):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/{path}", headers=SOFA_H, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def get_agenda_multiliga():
    ahora = time.time()
    if ahora - _agenda_cache["ts"] < 900 and _agenda_cache["data"]:
        return _agenda_cache["data"]
    resultado = []
    hoy = datetime.now()
    for delta in range(2):
        fecha = (hoy + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            data = sofa_get(f"sport/football/scheduled-events/{fecha}")
            eventos = data.get("events", []) if data else []
            for lk, info in LIGAS.items():
                filtrados = [e for e in eventos
                             if e.get("tournament",{}).get("uniqueTournament",{}).get("id") == info["sofa_id"]]
                for ev in filtrados:
                    home = ev.get("homeTeam",{}); away = ev.get("awayTeam",{})
                    ts_ev = ev.get("startTimestamp", 0)
                    try:
                        dt = datetime.utcfromtimestamp(ts_ev) - timedelta(hours=6)
                        hora_cst = dt.strftime("%H:%M"); fecha_p = dt.strftime("%Y-%m-%d")
                        dias = (dt.date() - datetime.now().date()).days
                        label = "Hoy" if dias==0 else "Mañana" if dias==1 else dt.strftime("%d %b")
                    except:
                        hora_cst="00:00"; fecha_p=fecha; label="Próximo"
                    rnd = ev.get("roundInfo",{})
                    fase = rnd.get("name") or f"J{rnd.get('round','')}"
                    resultado.append({
                        "id": f"{lk}_{ev['id']}","sofa_event_id": ev["id"],
                        "liga": info["nombre"],"liga_key": lk,"fase": fase,
                        "local": home.get("name","?"),"visita": away.get("name","?"),
                        "hora_cst": hora_cst,"fecha": fecha_p,"fecha_label": label,
                        "estadio": ev.get("venue",{}).get("name",""),"ciudad":"","altitud":0,
                        "odds_sport": info["odds_sport"],
                        "contexto": f"{info['nombre']} — {fase}",
                        "momios_static":{"local":"N/A","empate":"N/A","visita":"N/A",
                                         "over":"N/A","under":"N/A","ou":"2.5","btts":"N/A"},
                        "arbitro":{"nombre":"Por confirmar","amarillas":4.2,"rojas":0.2,"penales":0.2},
                        "bajas":[],"h2h":{"local":0,"empate":0,"visita":0,"prom_goles":2.5},
                        "stats_local": {"xg":1.5,"goles_f":1.5,"goles_c":1.2,"corners":5.0,"tarjetas":2.0,"posesion":50},
                        "stats_visita":{"xg":1.4,"goles_f":1.4,"goles_c":1.3,"corners":4.8,"tarjetas":2.1,"posesion":50},
                        "forma_local":[],"forma_visita":[],
                    })
            time.sleep(0.3)
        except Exception as e:
            log.error(f"SofaScore {fecha}: {e}")
    _agenda_cache.update({"ts": ahora, "data": resultado})
    log.info(f"Agenda: {len(resultado)} partidos")
    return resultado

def todos_los_partidos():
    agenda = get_agenda_multiliga()
    ids_fijos = {p["id"] for p in PARTIDOS_HOY}
    return PARTIDOS_HOY + [p for p in agenda if p.get("id") not in ids_fijos]

def buscar_partido(termino):
    tl = termino.lower()
    for p in todos_los_partidos():
        if (tl in p.get("local","").lower() or
            tl in p.get("visita","").lower() or
            tl in p.get("liga","").lower()):
            return p
    return None


# ══════════════════════════════════════════════════════════════
# ODDS API — MOMIOS EN TIEMPO REAL
# ══════════════════════════════════════════════════════════════
_cache_odds = {}

def obtener_momios(partido, forzar=False):
    pid = partido.get("id","")
    ahora = time.time()
    cached = _cache_odds.get(pid)
    if cached and not forzar and ahora - cached["ts"] < 300:
        return cached["data"]
    sport = partido.get("odds_sport","soccer_epl")
    if not ODDS_API_KEY:
        return None
    try:
        r = requests.get(
            f"https://api.odds-api.io/v4/sports/{sport}/odds",
            params={"apiKey":ODDS_API_KEY,"regions":"us,eu,latam",
                    "markets":"h2h,totals","oddsFormat":"american"},
            timeout=12)
        if r.status_code != 200:
            return None
        eventos = r.json()
        if not isinstance(eventos, list):
            return None
        pals_l = [w for w in partido["local"].lower().split() if len(w)>3]
        pals_v = [w for w in partido["visita"].lower().split() if len(w)>3]
        ev_enc = next((e for e in eventos
                       if any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in pals_l)
                       and any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in pals_v)), None)
        if not ev_enc:
            return None
        resultado = {"books":[],"home_team":ev_enc.get("home_team"),"away_team":ev_enc.get("away_team")}
        movimientos = []
        prev = _cache_odds.get(pid,{}).get("data")
        for book in ev_enc.get("bookmakers",[])[:6]:
            bd = {"nombre":book.get("title",""),"h2h":{},"totals":{}}
            for mkt in book.get("markets",[]):
                out = mkt.get("outcomes",[])
                if mkt["key"]=="h2h":
                    for o in out:
                        if o["name"]==ev_enc.get("home_team"):   bd["h2h"]["local"]=o["price"]
                        elif o["name"]=="Draw":                   bd["h2h"]["empate"]=o["price"]
                        elif o["name"]==ev_enc.get("away_team"):  bd["h2h"]["visita"]=o["price"]
                elif mkt["key"]=="totals":
                    for o in out:
                        if o["name"]=="Over":  bd["totals"]["over"]=o["price"]; bd["totals"]["linea"]=o.get("point")
                        elif o["name"]=="Under": bd["totals"]["under"]=o["price"]
            resultado["books"].append(bd)
        if prev and prev.get("books") and resultado["books"]:
            pb=prev["books"][0].get("h2h",{}); cb=resultado["books"][0].get("h2h",{})
            for campo,label in [("local","Local"),("empate","Empate"),("visita","Visita")]:
                vo=pb.get(campo); vn=cb.get(campo)
                if vo and vn:
                    diff=float(vn)-float(vo)
                    if abs(diff)>=10:
                        movimientos.append(f"{'📈' if diff>0 else '📉'} {label}: {decimal_to_american(vo)} → {decimal_to_american(vn)} ({'+' if diff>0 else ''}{int(diff)}pts)")
        if movimientos:
            resultado["movimientos"]=movimientos
            _log_alerta("momios",f"{partido['local']} vs {partido['visita']}","\n".join(movimientos))
        _cache_odds[pid]={"ts":ahora,"data":resultado}
        return resultado
    except Exception as e:
        log.error(f"Odds API: {e}")
        return None

def fmt_momios(partido, momios_data):
    local=partido["local"]; visita=partido["visita"]
    m=partido.get("momios_static",{})
    t=f"💰 <b>MOMIOS — {local} vs {visita}</b>\n🏟 {partido.get('estadio','')} · {partido.get('hora_cst','')} CST\n━━━━━━━━━━━━━━━━━━\n"
    if momios_data and momios_data.get("books"):
        t+=f"🟢 <b>EN VIVO — {len(momios_data['books'])} casas</b>\n\n"
        for bk in momios_data["books"][:4]:
            h=bk.get("h2h",{}); tt=bk.get("totals",{})
            lp=decimal_to_american(h["local"])  if h.get("local")  else m.get("local","N/A")
            ep=decimal_to_american(h["empate"]) if h.get("empate") else m.get("empate","N/A")
            vp=decimal_to_american(h["visita"]) if h.get("visita") else m.get("visita","N/A")
            op=decimal_to_american(tt["over"])  if tt.get("over")  else m.get("over","N/A")
            up=decimal_to_american(tt["under"]) if tt.get("under") else m.get("under","N/A")
            lin=tt.get("linea") or m.get("ou","2.5")
            t+=f"<b>{bk['nombre']}</b>\n  1X2: {lp} / {ep} / {vp}\n  O/U {lin}: {op} / {up}\n\n"
    else:
        t+=f"📊 <i>Momios referencia (sin API en vivo)</i>\n"
        t+=f"1X2: {local} {m.get('local','N/A')} / Empate {m.get('empate','N/A')} / {visita} {m.get('visita','N/A')}\n"
        t+=f"O/U {m.get('ou','2.5')}: Over {m.get('over','N/A')} / Under {m.get('under','N/A')} | BTTS: {m.get('btts','N/A')}\n\n"
    t+=f"📐 {local}: {prob_implicita(m.get('local','0')):.1f}% | Empate: {prob_implicita(m.get('empate','0')):.1f}% | {visita}: {prob_implicita(m.get('visita','0')):.1f}%"
    return t


# ══════════════════════════════════════════════════════════════
# CLIMA
# ══════════════════════════════════════════════════════════════
COORDS = {
    "Ciudad de México, MX":(19.43,-99.13),"Guadalajara, MX":(20.65,-103.35),
    "Monterrey, MX":(25.69,-100.32),"Madrid, España":(40.42,-3.70),
    "Barcelona, España":(41.39,2.17),"Londres, Inglaterra":(51.51,-0.13),
    "Milán, Italia":(45.47,9.19),"Roma, Italia":(41.90,12.50),
    "East Rutherford, NJ":(40.81,-74.07),"Arlington, TX":(32.74,-97.11),
    "Inglewood, CA":(33.95,-118.34),"Miami, FL":(25.77,-80.19),
    "Houston, TX":(29.76,-95.37),"Kansas City, MO":(39.10,-94.58),
}
CLIMA_EMOJI={"Clear":"☀️","Clouds":"⛅","Rain":"🌧️","Drizzle":"🌦️",
             "Thunderstorm":"⛈️","Snow":"❄️","Mist":"🌫️","Fog":"🌫️"}

def obtener_clima(ciudad):
    if not OPENWEATHER_KEY: return None
    lat,lon=COORDS.get(ciudad,(0,0))
    if not lat: return None
    try:
        r=requests.get("http://api.openweathermap.org/data/2.5/weather",
            params={"lat":lat,"lon":lon,"appid":OPENWEATHER_KEY,"units":"metric","lang":"es"},
            timeout=8).json()
        return {"temp":round(r["main"]["temp"]),"humedad":r["main"]["humidity"],
                "viento":r["wind"]["speed"],"desc":r["weather"][0]["description"].capitalize(),
                "icono":r["weather"][0]["main"]}
    except: return None


# ══════════════════════════════════════════════════════════════
# ANÁLISIS IA — CLAUDE
# ══════════════════════════════════════════════════════════════
def generar_analisis_ia(partido, momios_data=None, tipo="completo"):
    if not ANTHROPIC_KEY:
        return _analisis_fallback(partido)
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    m=partido.get("momios_static",{}); arb=partido.get("arbitro",{})
    bajas="\n".join(partido.get("bajas",[])) or "Sin bajas confirmadas"
    xg_total=sl.get("xg",1.5)+sv.get("xg",1.4)
    momios_str=f"Local {m.get('local','N/A')} / Empate {m.get('empate','N/A')} / Visita {m.get('visita','N/A')}"
    if momios_data and momios_data.get("books"):
        libros=[f"{bk['nombre']}:{decimal_to_american(bk['h2h'].get('local',0))}/{decimal_to_american(bk['h2h'].get('visita',0))}"
                for bk in momios_data["books"][:3] if bk.get("h2h")]
        if libros: momios_str="VIVO — "+" | ".join(libros)
    max_tok=600 if tipo=="alerta" else 3500
    if tipo=="alerta":
        prompt=(f"Analista senior del Sindicato. Análisis PRE-PARTIDO conciso (150-200 palabras) para Telegram.\n"
                f"{partido['local']} vs {partido['visita']} — {partido.get('fase','')} · {partido.get('hora_cst','')} CST\n"
                f"Liga: {partido.get('liga','')}\nContexto: {partido.get('contexto','')}\n"
                f"xG: {sl.get('xg',1.5)} vs {sv.get('xg',1.4)} | Momios: {momios_str}\n"
                f"Árbitro: {arb.get('nombre','N/D')} ({arb.get('amarillas',4.2)} am/p)\nBajas: {bajas}\n\n"
                f"Estructura:\n▸ FACTOR CLAVE (con dato numérico)\n"
                f"▸ APUESTA RECOMENDADA: [mercado] [momio] — prob sistema X%\n"
                f"▸ RIESGO PRINCIPAL\n▸ BANKROLL: X% del capital\nSin asteriscos. Español.")
    else:
        prompt=(f"Eres el analista senior del Sindicato de Apuestas. Análisis EXTENSO y COMPLETO. Mínimo 700 palabras.\n\n"
                f"{partido['local']} vs {partido['visita']}\n"
                f"Liga: {partido.get('liga','')} | Fase: {partido.get('fase','')}\n"
                f"Estadio: {partido.get('estadio','')} — {partido.get('ciudad','')}\n"
                f"Altitud: {partido.get('altitud',0)}m | Árbitro: {arb.get('nombre','N/D')} ({arb.get('amarillas',4.2)} am/p)\n"
                f"Contexto: {partido.get('contexto','')}\nMomios: {momios_str}\n"
                f"H2H: {partido['local']} {partido['h2h'].get('local',0)}V / {partido['h2h'].get('empate',0)}E / {partido['visita']} {partido['h2h'].get('visita',0)}V\n\n"
                f"{partido['local']}: xG {sl.get('xg',1.5)} | GF/p {sl.get('goles_f',1.5)} | GC/p {sl.get('goles_c',1.2)} | Forma: {'-'.join(partido.get('forma_local',[]))}\n"
                f"{partido['visita']}: xG {sv.get('xg',1.4)} | GF/p {sv.get('goles_f',1.4)} | GC/p {sv.get('goles_c',1.3)} | Forma: {'-'.join(partido.get('forma_visita',[]))}\n"
                f"BAJAS: {bajas}\n\n"
                f"8 secciones separadas con ================:\n"
                f"1 CONTEXTO 2 TACTICO 3 BAJAS 4 ALTITUD {partido.get('altitud',0)}m\n"
                f"5 MOMIOS Y EV 6 MERCADOS (1X2/O-U {m.get('ou','2.5')}/BTTS/Corners/Tarjetas/Gol1T)\n"
                f"7 ARBITRAL 8 TOP 3 APUESTAS (Mercado/Momio/Prob/EV-100/Kelly/Bankroll)\n"
                f"Sin asteriscos. Español.")
    try:
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":max_tok,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=50)
        if r.status_code==200:
            texto=r.json()["content"][0]["text"]
            _guardar_prediccion(partido,texto)
            return texto
        log.error(f"Anthropic HTTP {r.status_code}")
        return _analisis_fallback(partido)
    except Exception as e:
        log.error(f"Claude: {e}")
        return _analisis_fallback(partido)

def _analisis_fallback(partido):
    m=partido.get("momios_static",{}); sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    xg=sl.get("xg",1.5)+sv.get("xg",1.4)
    return (f"📊 <b>ANÁLISIS — {partido['local']} vs {partido['visita']}</b>\n"
            f"▸ xG combinado: {xg:.2f} (línea: {m.get('ou','2.5')})\n"
            f"▸ Árbitro: {partido.get('arbitro',{}).get('nombre','N/D')}\n\n"
            f"<i>Configura ANTHROPIC_API_KEY para análisis completo.</i>")

def _guardar_prediccion(partido, texto):
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        m=partido.get("momios_static",{})
        c.execute("INSERT INTO predicciones (partido_id,partido_nombre,liga,mercado,momio,confianza,razonamiento) VALUES(?,?,?,?,?,?,?)",
                  (partido["id"],f"{partido['local']} vs {partido['visita']}",
                   partido.get("liga",""),"Análisis IA",m.get("over","N/A"),75,texto[:2500]))
        conn.commit(); conn.close()
    except: pass


# ══════════════════════════════════════════════════════════════
# HELPERS TELEGRAM
# ══════════════════════════════════════════════════════════════
def verificar_chat(message):
    return str(message.chat.id)==str(TELEGRAM_CHAT_ID)

def enviar_telegram(texto, chat_id=None):
    if not TELEGRAM_TOKEN: return False
    cid=chat_id or TELEGRAM_CHAT_ID
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":cid,"text":texto,"parse_mode":"HTML","disable_web_page_preview":True},
            timeout=10)
        return r.json().get("ok",False)
    except Exception as e:
        log.error(f"Telegram: {e}"); return False

def enviar_largo(texto, chat_id=None):
    if len(texto)<=3900:
        enviar_telegram(texto,chat_id); return
    partes=[]; actual=""
    for linea in texto.split("\n"):
        if len(actual)+len(linea)>3700: partes.append(actual); actual=""
        actual+=linea+"\n"
    if actual: partes.append(actual)
    for i,p in enumerate(partes,1):
        time.sleep(0.5)
        enviar_telegram(p+f"\n<i>({i}/{len(partes)})</i>",chat_id)

def _ya_enviada(tipo,pid,horas=12):
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT COUNT(*) FROM alertas_log WHERE tipo=? AND partido_nombre LIKE ? AND enviada=1 AND ts>datetime('now',?)",
                  (tipo,f"%{pid[:12]}%",f"-{horas} hours"))
        n=c.fetchone()[0]; conn.close(); return n>0
    except: return False

def _log_alerta(tipo,nombre,msg,enviada=1):
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("INSERT INTO alertas_log (tipo,partido_nombre,mensaje,enviada) VALUES(?,?,?,?)",
                  (tipo,nombre,msg[:300],enviada))
        conn.commit(); conn.close()
    except: pass


# ══════════════════════════════════════════════════════════════
# ALERTAS AUTOMÁTICAS
# ══════════════════════════════════════════════════════════════
def alerta_2h(partido):
    nombre=f"{partido['local']} vs {partido['visita']}"
    if _ya_enviada("pre2h",partido["id"]): return
    momios=obtener_momios(partido); m=partido.get("momios_static",{})
    analisis=generar_analisis_ia(partido,momios,tipo="alerta")
    def _ml(side):
        if momios and momios.get("books") and momios["books"][0].get("h2h",{}).get(side):
            return decimal_to_american(momios["books"][0]["h2h"][side])
        return m.get(side,"N/A")
    arb=partido.get("arbitro",{})
    texto=(f"⚡ <b>ALERTA — 2 HORAS PARA EL INICIO</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"⚽ <b>{nombre}</b>\n🏆 {partido.get('liga','')} — {partido.get('fase','')}\n"
           f"🏟 {partido.get('estadio','')} · {partido.get('hora_cst','')} CST\n\n"
           f"📋 {partido.get('contexto','')}\n\n"
           f"💰 {partido['local']} {_ml('local')} / Empate {_ml('empate')} / {partido['visita']} {_ml('visita')}\n"
           f"O/U {m.get('ou','2.5')}: Over {m.get('over','N/A')} / Under {m.get('under','N/A')}\n\n")
    if partido.get("altitud",0)>1800:
        texto+=f"⛰️ <b>ALTITUD {partido['altitud']}m</b> — Factor crítico\n\n"
    texto+=(f"⚖️ Árbitro: {arb.get('nombre','N/D')} ({arb.get('amarillas',4.2)} am/p)\n\n"
            f"🤖 <b>Análisis rápido IA:</b>\n{analisis[:600]}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n/analisis para informe completo | /valor para edge")
    if enviar_telegram(texto): _log_alerta("pre2h",nombre,texto[:200])

def alerta_1h(partido):
    nombre=f"{partido['local']} vs {partido['visita']}"
    if _ya_enviada("analisis",partido["id"],20): return
    momios=obtener_momios(partido)
    texto_ia=generar_analisis_ia(partido,momios,tipo="completo")
    header=(f"🧠 <b>ANÁLISIS IA COMPLETO — SINDICATO</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"⚽ <b>{nombre}</b>\n{partido.get('liga','')} · {partido.get('hora_cst','')} CST\n━━━━━━━━━━━━━━━━━━\n\n")
    enviar_largo(header+texto_ia)
    _log_alerta("analisis",nombre,"Análisis completo")

def alerta_30min(partido):
    nombre=f"{partido['local']} vs {partido['visita']}"
    if _ya_enviada("pre30min",partido["id"]): return
    momios=obtener_momios(partido,forzar=True); m=partido.get("momios_static",{})
    def _ml(side):
        if momios and momios.get("books") and momios["books"][0].get("h2h",{}).get(side):
            return decimal_to_american(momios["books"][0]["h2h"][side])
        return m.get(side,"N/A")
    texto=(f"🔔 <b>¡30 MINUTOS PARA EL INICIO!</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"⚽ <b>{nombre}</b>\n{partido.get('liga','')} · {partido.get('hora_cst','')} CST\n\n"
           f"💰 Momios finales: {partido['local']} <b>{_ml('local')}</b> / {partido['visita']} <b>{_ml('visita')}</b>\n")
    if partido.get("bajas"): texto+=f"⚠️ Bajas: {' | '.join(partido['bajas'])}\n"
    texto+="\n🎯 ¡El Sindicato está listo!"
    if enviar_telegram(texto): _log_alerta("pre30min",nombre,texto[:200])


# ══════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ══════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start","menu","hoy"])
def cmd_menu(message):
    if not verificar_chat(message): return
    markup=types.InlineKeyboardMarkup(row_width=1)
    texto="⚡ <b>SINDICATOBOT v4.2 — AGENDA</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for p in PARTIDOS_HOY:
        nombre=f"{p['local']} vs {p['visita']}"
        texto+=(f"⚽ <b>{nombre}</b>\n   {p['fecha_label']} · {p['hora_cst']} CST\n"
                f"   {p['liga']} · {p['estadio']}\n\n")
        markup.add(types.InlineKeyboardButton(f"📊 {nombre}",callback_data=f"p_{p['id']}"))
    markup.add(types.InlineKeyboardButton("🌍 Todas las ligas",callback_data="todas_ligas"))
    markup.add(types.InlineKeyboardButton("🌎 Mundial 2026",   callback_data="mundial_menu"))
    markup.add(types.InlineKeyboardButton("📊 Reporte ROI",    callback_data="roi"))
    bot.send_message(message.chat.id,texto,parse_mode="HTML",reply_markup=markup)

@bot.message_handler(commands=["agenda"])
def cmd_agenda(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,"⏳ Cargando todas las ligas...","HTML")
    agenda=get_agenda_multiliga()
    por_liga={}
    for p in agenda: por_liga.setdefault(p["liga_key"],[]).append(p)
    texto="📅 <b>AGENDA COMPLETA</b>\nHoy y Mañana\n━━━━━━━━━━━━━━━━━━\n\n"
    for lk,info in LIGAS.items():
        ps=por_liga.get(lk,[])
        if not ps: continue
        texto+=f"{info['emoji']} <b>{info['nombre'].upper()}</b>\n"
        for p in ps[:4]:
            texto+=f"  ⚽ {p['local']} vs {p['visita']} · {p['hora_cst']}\n"
        texto+="\n"
    enviar_largo(texto)

@bot.message_handler(commands=["analisis"])
def cmd_analisis(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else PARTIDOS_HOY[0]
    if not partido:
        bot.send_message(message.chat.id,f"❌ No encontré <b>{termino}</b>.","HTML"); return
    bot.send_message(message.chat.id,
        f"⏳ Analizando <b>{partido['local']} vs {partido['visita']}</b>...\n(20-30 segundos)","HTML")
    momios=obtener_momios(partido)
    enviar_largo(generar_analisis_ia(partido,momios,tipo="completo"))

@bot.message_handler(commands=["valor"])
def cmd_valor(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else PARTIDOS_HOY[0]
    if not partido:
        bot.send_message(message.chat.id,f"❌ No encontré <b>{termino}</b>.","HTML"); return
    nombre=f"{partido['local']} vs {partido['visita']}"
    bot.send_message(message.chat.id,f"⏳ Calculando valor en <b>{nombre}</b>...","HTML")
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    m=partido.get("momios_static",{})
    momios_dict={"local":m.get("local","+200"),"empate":m.get("empate","+280"),
                 "visita":m.get("visita","+300"),"over25":m.get("over","-110"),
                 "under25":m.get("under","-110"),"btts_si":m.get("btts","+100")}
    resultado=analizar_partido_completo(partido["local"],partido["visita"],
               sl.get("xg",1.5),sv.get("xg",1.4),momios_dict,bankroll=1000)
    apuestas_para_filtrar=[{"mercado":ap["label"],"momio":ap["momio"],
        "prob_sistema":ap["prob_sis"],"edge_pct":ap["edge"],
        "ev_por_100":ap["ev"],"kelly_pct":ap["kelly_pct"],"apuesta":ap.get("apuesta",0)}
        for ap in resultado.get("apuestas_valor",[])]
    aprobadas,rechazadas,resumen=filtrar_apuestas_valor(apuestas_para_filtrar)
    pr=resultado["probabilidades"]
    texto=(f"🎯 <b>APUESTAS CON VALOR — {nombre}</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"xG {sl.get('xg',1.5)} + {sv.get('xg',1.4)} = {sl.get('xg',1.5)+sv.get('xg',1.4):.2f}\n"
           f"Probs: Local {pr['local']*100:.1f}% | Empate {pr['empate']*100:.1f}% | Visita {pr['visita']*100:.1f}%\n"
           f"Marcador probable: {resultado['marcador_probable'][0]}\n\n")
    if aprobadas:
        texto+=f"✅ <b>{len(aprobadas)} apuesta(s) con edge ≥5%</b>\n\n"
        for ap in aprobadas[:3]:
            texto+=(f"{ap['emoji']} <b>{ap['mercado']}</b>\n"
                   f"   Momio: <b>{ap['momio']}</b> | Prob: {ap['prob_sistema']:.1f}% | Edge: {ap['edge_pct']:+.1f}%\n"
                   f"   EV/$100: <b>${ap['ev_por_100']:+.2f}</b>\n"
                   f"   Kelly {ap['kelly_pct']:.1f}% → <b>${ap.get('apuesta',0):.2f}</b> de $1,000\n"
                   f"   Convicción: {ap['conviccion']}\n\n")
        texto+="Usa /apostar para registrar tu apuesta."
    else:
        texto+="⚠️ Sin apuestas con valor suficiente (edge <5%)."
    enviar_largo(texto)

@bot.message_handler(commands=["momios"])
def cmd_momios(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else PARTIDOS_HOY[0]
    if not partido:
        bot.send_message(message.chat.id,"❌ No encontré ese partido."); return
    bot.send_message(message.chat.id,"⏳ Consultando momios en tiempo real...")
    momios=obtener_momios(partido,forzar=True)
    bot.send_message(message.chat.id,fmt_momios(partido,momios),parse_mode="HTML")

@bot.message_handler(commands=["mundial"])
def cmd_mundial(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    if not termino:
        markup=types.InlineKeyboardMarkup(row_width=4)
        botones=[types.InlineKeyboardButton(f"G.{g}",callback_data=f"mundial_grupo_{g}")
                 for g in "ABCDEFGHIJKL"]
        for i in range(0,len(botones),4): markup.add(*botones[i:i+4])
        markup.add(types.InlineKeyboardButton("🇲🇽 México",callback_data="mundial_equipo_mexico"))
        markup.add(types.InlineKeyboardButton("📅 Inaugurales",callback_data="mundial_fecha_inicio"))
        bot.send_message(message.chat.id,
            "🌎 <b>MUNDIAL 2026 — FIXTURES</b>\n━━━━━━━━━━━━━━━━━━\n"
            "📅 Inicio: 11 Jun 2026 | Final: 19 Jul\n"
            "⚽ 48 selecciones | 104 partidos | 12 grupos\n\n"
            "Selecciona un grupo o escribe:\n/mundial mexico | /mundial A",
            parse_mode="HTML",reply_markup=markup); return
    fixtures=get_fixture_por_equipo(termino)
    if fixtures:
        texto=f"🌎 <b>MUNDIAL 2026 — {termino.upper()}</b>\n━━━━━━━━━━━━━━━━━━\n"
        for p in fixtures:
            alt=f" ⛰️{p['altitud']}m" if p["altitud"]>1800 else ""
            pm=p["momios"]
            texto+=(f"\n⚽ <b>{p['local']} vs {p['visita']}</b>\n"
                   f"   Grupo {p['grupo']} — {p['fase']} | {p['fecha']} {p['hora_cst']} CST{alt}\n"
                   f"   🏟 {p['estadio']}\n"
                   f"   💰 {pm['local']} / {pm['empate']} / {pm['visita']}\n")
        enviar_largo(texto); return
    if termino.upper() in "ABCDEFGHIJKL" and len(termino)==1:
        enviar_largo(formatear_fixtures_grupo_telegram(termino.upper())); return
    bot.send_message(message.chat.id,f"❌ No encontré <b>{termino}</b>.","HTML")

@bot.message_handler(commands=["tabla"])
def cmd_tabla(message):
    if not verificar_chat(message): return
    grupos={
        "A":[("México",0,0,0,0),("Sudáfrica",0,0,0,0),("Corea del Sur",0,0,0,0),("Rep. Checa",0,0,0,0)],
        "B":[("Canadá",0,0,0,0),("Bosnia-H",0,0,0,0),("Suiza",0,0,0,0),("Angola",0,0,0,0)],
        "C":[("Brasil",0,0,0,0),("Marruecos",0,0,0,0),("Escocia",0,0,0,0),("Haití",0,0,0,0)],
        "D":[("USA",0,0,0,0),("Alemania",0,0,0,0),("Ecuador",0,0,0,0),("Costa Marfil",0,0,0,0)],
        "E":[("P. Bajos",0,0,0,0),("Suecia",0,0,0,0),("Turquía",0,0,0,0),("Zambia",0,0,0,0)],
        "F":[("Francia",0,0,0,0),("Bélgica",0,0,0,0),("Rumania",0,0,0,0),("Irak",0,0,0,0)],
        "G":[("Italia",0,0,0,0),("Portugal",0,0,0,0),("Colombia",0,0,0,0),("R.D.Congo",0,0,0,0)],
        "H":[("España",0,0,0,0),("Uruguay",0,0,0,0),("Arabia S.",0,0,0,0),("Cabo Verde",0,0,0,0)],
        "I":[("Francia",0,0,0,0),("Australia",0,0,0,0),("Dinamarca",0,0,0,0),("Túnez",0,0,0,0)],
        "J":[("Argentina",0,0,0,0),("Argelia",0,0,0,0),("Austria",0,0,0,0),("Jordania",0,0,0,0)],
        "K":[("Portugal",0,0,0,0),("Polonia",0,0,0,0),("Ucrania",0,0,0,0),("Serbia",0,0,0,0)],
        "L":[("Inglaterra",0,0,0,0),("Croacia",0,0,0,0),("Panamá",0,0,0,0),("Ghana",0,0,0,0)],
    }
    bot.send_message(message.chat.id,"🌎 <b>MUNDIAL 2026 — GRUPOS A-L</b>\n━━━━━━━━━━━━━━━━━━\n🟢 Top 2 clasifican a dieciseisavos","HTML")
    letras=list(grupos.keys())
    for i in range(0,len(letras),3):
        bloque=""
        for letra in letras[i:i+3]:
            bloque+=f"\n<b>GRUPO {letra}</b>\n"
            for j,(n,pts,pj,gf,gc) in enumerate(grupos[letra],1):
                ic="🟢" if j<=2 else "⚪"
                bloque+=f"<code>{ic} {j}. {n:<14} {pj}J {gf}-{gc}  {pts}pts</code>\n"
        time.sleep(0.4)
        bot.send_message(message.chat.id,bloque,parse_mode="HTML")

@bot.message_handler(commands=["clima"])
def cmd_clima(message):
    if not verificar_chat(message): return
    texto="🌡️ <b>CONDICIONES DE LOS ESTADIOS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for p in PARTIDOS_HOY:
        cl=obtener_clima(p["ciudad"])
        alt=p["altitud"]
        nota=f" ⛰️{alt}m CRÍTICO" if alt>1800 else f" ⛰️{alt}m" if alt>500 else ""
        if cl:
            ic=CLIMA_EMOJI.get(cl["icono"],"🌡️")
            texto+=(f"⚽ <b>{p['local']} vs {p['visita']}</b>\n📍 {p['estadio']}{nota}\n"
                   f"{ic} {cl['temp']}°C · {cl['desc']}\n💨 {cl['viento']}m/s · 💧{cl['humedad']}%\n\n")
        else:
            texto+=(f"⚽ <b>{p['local']} vs {p['visita']}</b>\n📍 {p['estadio']}{nota}\n"
                   f"⚠️ Configura OPENWEATHER_API_KEY para clima\n\n")
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["ev"])
def cmd_ev(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3:
        bot.send_message(message.chat.id,"📝 Uso: /ev [momio] [prob%]\nEj: /ev -130 55"); return
    try:
        ml=partes[1]; prob=float(partes[2])
        dec=american_to_decimal(ml); p=prob/100
        ev=(p*(dec-1)-(1-p))*100; pc=prob_implicita(ml)*100; edge=prob-pc
        texto=(f"🎯 <b>CALCULADORA EV</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"Momio: <b>{ml}</b> ({dec}x)\nProb casino: <b>{pc:.1f}%</b>\n"
               f"Prob sistema: <b>{prob}%</b>\nEdge: <b>{edge:+.1f}%</b>\n\n"
               f"EV por $50:  ${ev*0.5:+.2f}\nEV por $100: ${ev:+.2f}\nEV por $200: ${ev*2:+.2f}\n\n"
               +("🔥 <b>CON VALOR</b>" if ev>10 else "✅ EV positivo" if ev>0 else "❌ <b>SIN VALOR</b>"))
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id,f"❌ Error: {e}")

@bot.message_handler(commands=["calcparlay"])
def cmd_calcparlay(message):
    if not verificar_chat(message): return
    partes=message.text.split()[1:]
    if not partes:
        bot.send_message(message.chat.id,"Uso: /calcparlay -130 +280 -115"); return
    try:
        dec_total=1.0
        for ml in partes: dec_total*=american_to_decimal(ml)
        prob=1.0
        for ml in partes: prob*=prob_implicita(ml)
        mlP=decimal_to_american(dec_total); ev=(prob*(dec_total-1)-(1-prob))*100
        texto=(f"🎰 <b>PARLAY — {len(partes)} piernas</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"Momios: {' | '.join(partes)}\n\n"
               f"Momio combinado: <b>{mlP}</b> ({round(dec_total,3)}x)\n"
               f"Prob combinada:  <b>{round(prob*100,1)}%</b>\n\n"
               f"$50  → ${50*dec_total:.2f}\n$100 → ${100*dec_total:.2f}\n$200 → ${200*dec_total:.2f}\n\n"
               f"EV/$100: <b>${ev:+.2f}</b> — {'🔥 CON VALOR' if ev>0 else '⚠️ SIN VALOR'}")
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id,f"❌ Error: {e}")

@bot.message_handler(commands=["apostar"])
def cmd_apostar(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<5:
        bot.send_message(message.chat.id,
            "📝 <b>REGISTRAR APUESTA</b>\n\nFormato:\n<code>/apostar [equipo] [mercado] [momio] [monto]</code>\n\n"
            "Ejemplos:\n<code>/apostar Pumas over25 -110 150</code>\n<code>/apostar Mexico local -200 100</code>",
            parse_mode="HTML"); return
    equipo=partes[1]; mercado=partes[2]; momio=partes[3]
    try: monto=float(partes[4])
    except:
        bot.send_message(message.chat.id,"❌ El monto debe ser un número."); return
    partido=buscar_partido(equipo)
    if not partido:
        bot.send_message(message.chat.id,f"❌ No encontré partido con <b>{equipo}</b>.","HTML"); return
    nombre=f"{partido['local']} vs {partido['visita']}"
    aid=registrar_apuesta_clv(partido["id"],nombre,partido.get("liga",""),
                               partido.get("fecha",""),mercado,momio,monto,1000)
    dec=american_to_decimal(momio)
    texto=(f"✅ <b>APUESTA REGISTRADA</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"⚽ {nombre}\n📊 {mercado} | 💰 {momio} ({dec}x)\n"
           f"💵 ${monto:.2f} → Pago potencial: ${monto*dec:.2f}\n\n"
           f"ID seguimiento: <code>{aid}</code>\n"
           f"Al cerrar usa: <code>/cierre {aid} [momio_cierre] W/L/P</code>")
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["cierre"])
def cmd_cierre(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3:
        bot.send_message(message.chat.id,
            "📝 Formato: <code>/cierre [id] [momio_cierre] [W/L/P]</code>\n"
            "Ejemplo: <code>/cierre 3 -130 W</code>","HTML"); return
    try: aid=int(partes[1])
    except:
        bot.send_message(message.chat.id,"❌ El ID debe ser un número."); return
    momio_cierre=partes[2]; res=partes[3].upper() if len(partes)>3 else None
    ganancia=None
    if res in ["W","L","P"]:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT apuesta_monto,momio_apostado FROM clv_tracker WHERE id=?",(aid,))
        row=c.fetchone(); conn.close()
        if row:
            monto,momio_ap=row; dec=american_to_decimal(momio_ap)
            ganancia=round(monto*(dec-1),2) if res=="W" else round(-monto,2) if res=="L" else 0.0
    clv=actualizar_cierre_clv(aid,momio_cierre,res,ganancia)
    if clv is None:
        bot.send_message(message.chat.id,f"❌ No encontré la apuesta ID {aid}."); return
    res_txt={"W":"✅ GANADA","L":"❌ PERDIDA","P":"⚖️ PUSH"}.get(res,"⏳ Pendiente")
    texto=(f"📐 <b>CLV ACTUALIZADO</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"ID: {aid} | Cierre: <b>{momio_cierre}</b>\nCLV: <b>{clv:+.2f}%</b> | {res_txt}\n"
           +(f"Ganancia: <b>${ganancia:+.2f}</b>\n" if ganancia is not None else "")
           +("\n✅ CLV positivo — tu entrada fue mejor que el cierre." if clv>0
             else "\n⚠️ CLV negativo — el mercado cerró mejor que tu entrada."))
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["clv"])
def cmd_clv(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,formatear_clv_telegram(generar_reporte_clv()),parse_mode="HTML")

@bot.message_handler(commands=["roi"])
def cmd_roi(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,formatear_roi_telegram(generar_reporte_roi()),parse_mode="HTML")

@bot.message_handler(commands=["sharp"])
def cmd_sharp(message):
    if not verificar_chat(message): return
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT tipo,partido_nombre,mensaje,ts FROM alertas_log WHERE tipo='momios' ORDER BY ts DESC LIMIT 10")
        rows=c.fetchall(); conn.close()
    except: rows=[]
    if not rows:
        bot.send_message(message.chat.id,"📈 Sin movimientos detectados aún. Monitoreo cada 5 min.","HTML"); return
    texto="📈 <b>MOVIMIENTOS DETECTADOS</b>\n━━━━━━━━━━━━━━━━━━\n"
    for _,partido,mensaje,ts in rows:
        texto+=f"\n🕐 {ts[:16]}\n⚽ {partido}\n{mensaje[:120]}\n"
    enviar_largo(texto)

@bot.message_handler(commands=["resultado"])
def cmd_resultado(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3:
        bot.send_message(message.chat.id,
            "📝 Formato: <code>/resultado [Local] [X-X] [Visita]</code>\n"
            "Ejemplo: <code>/resultado Pumas 2-0 CruzAzul</code>","HTML"); return
    mi=next((i for i,p in enumerate(partes) if "-" in p and p.replace("-","").isdigit()),None)
    if mi is None:
        bot.send_message(message.chat.id,"❌ No encontré el marcador. Usa formato: 2-1"); return
    local=" ".join(partes[1:mi]); visita=" ".join(partes[mi+1:])
    try: gl,gv=[int(x) for x in partes[mi].split("-")]
    except:
        bot.send_message(message.chat.id,"❌ Marcador inválido."); return
    total=gl+gv
    p_enc=buscar_partido(local) or buscar_partido(visita)
    pid=p_enc["id"] if p_enc else f"manual_{local[:5]}_{visita[:5]}"
    liga=p_enc.get("liga","Manual") if p_enc else "Manual"
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    res="L" if gl>gv else "V" if gv>gl else "E"
    c.execute("INSERT OR REPLACE INTO resultados (partido_id,partido_nombre,liga,fecha,goles_local,goles_visita,total_goles,resultado,over_25,btts) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (pid,f"{local} vs {visita}",liga,datetime.now().strftime("%Y-%m-%d"),gl,gv,total,res,1 if total>2.5 else 0,1 if gl>0 and gv>0 else 0))
    conn.commit(); conn.close()
    texto=(f"✅ <b>RESULTADO REGISTRADO</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"⚽ <b>{local} {gl} - {gv} {visita}</b>\n\n"
           f"{'Local gana' if gl>gv else 'Visitante gana' if gv>gl else 'Empate'} | {total} goles | "
           f"{'Over' if total>2.5 else 'Under'} 2.5 | BTTS: {'Sí' if gl>0 and gv>0 else 'No'}\n\n"
           f"Usa /roi para ver el rendimiento.")
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["reporte","historial"])
def cmd_reporte(message):
    if not verificar_chat(message): return
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT COUNT(*) FROM predicciones"); total=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM predicciones WHERE fue_correcto IS NOT NULL"); res=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM predicciones WHERE fue_correcto=1"); ok=c.fetchone()[0]
        c.execute("SELECT partido_nombre,mercado,confianza,fue_correcto FROM predicciones ORDER BY ts DESC LIMIT 8")
        preds=c.fetchall(); conn.close()
        prec=round(ok/res*100,1) if res>0 else 0
        barra="█"*int(prec//10)+"░"*(10-int(prec//10))
        color="🟢" if prec>=60 else "🟡" if prec>=50 else "🔴"
        texto=(f"📊 <b>REPORTE IA — SINDICATO</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"Total: {total} | Resueltas: {res} | Correctas: {ok}\n"
               f"Precisión: <b>{prec}%</b>\n{color} [{barra}]\n\n<b>Últimas:</b>\n")
        for nombre,merc,conf,correcto in preds:
            ic="✅" if correcto==1 else "❌" if correcto==0 else "⏳"
            texto+=f"{ic} {nombre[:28]} · {merc or 'N/A'}\n"
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id,f"❌ Error: {e}")

@bot.message_handler(commands=["alerta"])
def cmd_alerta_manual(message):
    if not verificar_chat(message): return
    partes=message.text.split(); tipo=partes[1].lower() if len(partes)>1 else "pre2h"
    idx=min(int(partes[2]) if len(partes)>2 and partes[2].isdigit() else 0, len(PARTIDOS_HOY)-1)
    p=PARTIDOS_HOY[idx]; nombre=f"{p['local']} vs {p['visita']}"
    bot.send_message(message.chat.id,f"⏳ Generando alerta <b>{tipo}</b> para <b>{nombre}</b>...","HTML")
    if tipo=="pre2h":       alerta_2h(p)
    elif tipo=="analisis":  alerta_1h(p)
    elif tipo=="pre30min":  alerta_30min(p)
    elif tipo=="momios":    bot.send_message(message.chat.id,fmt_momios(p,obtener_momios(p,True)),parse_mode="HTML")
    else: bot.send_message(message.chat.id,"Tipos: pre2h | analisis | pre30min | momios")

@bot.message_handler(func=lambda m: True)
def cmd_ayuda(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,
        "⚡ <b>SINDICATOBOT v4.2</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📅 PARTIDOS</b>\n"
        "/hoy /agenda /analisis [equipo]\n/momios [equipo] /clima\n\n"
        "<b>🎯 ANÁLISIS POISSON + KELLY</b>\n"
        "/valor [equipo] — apuestas con edge ≥5%\n"
        "/ev [momio] [prob%] — calculadora EV\n"
        "/calcparlay [momios] — calculadora parlay\n\n"
        "<b>🌎 MUNDIAL 2026</b>\n"
        "/mundial — fixtures con menú\n"
        "/mundial mexico — partidos de México\n"
        "/mundial A — Grupo A completo\n"
        "/tabla — todos los grupos A-L\n\n"
        "<b>📊 TRACKING</b>\n"
        "/roi — ROI por mercado\n/clv — Closing Line Value\n"
        "/sharp — movimientos de momios\n"
        "/apostar [equipo] [mercado] [momio] [monto]\n"
        "/cierre [id] [momio_cierre] [W/L/P]\n\n"
        "<b>📈 RESULTADOS</b>\n"
        "/resultado [Local X-X Visita]\n/reporte\n\n"
        "<b>🔔 ALERTAS</b>\n"
        "/alerta pre2h [0-1] | analisis [0-1] | momios [0-1]\n\n"
        "<i>Alertas auto: 2h, 1h y 30min antes del partido.</i>",
        parse_mode="HTML")

# ── CALLBACKS ──────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    if str(call.message.chat.id)!=str(TELEGRAM_CHAT_ID): return
    data=call.data
    if data.startswith("p_"):
        pid=data.replace("p_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: bot.answer_callback_query(call.id,"No encontrado"); return
        nombre=f"{partido['local']} vs {partido['visita']}"
        markup=types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🤖 Análisis IA",    callback_data=f"ia_{pid}"),
            types.InlineKeyboardButton("💰 Momios",          callback_data=f"momios_{pid}"),
            types.InlineKeyboardButton("🎯 Valor (Poisson)",callback_data=f"valor_{pid}"),
            types.InlineKeyboardButton("🔙 Menú",            callback_data="menu"),)
        m=partido.get("momios_static",{})
        bot.edit_message_text(
            f"📋 <b>{nombre}</b>\n{partido.get('liga','')} — {partido.get('fase','')}\n"
            f"🏟 {partido.get('estadio','')} · {partido.get('hora_cst','')} CST\n\n"
            f"📋 {partido.get('contexto','')}\n\n"
            f"💰 {partido['local']} {m.get('local','N/A')} / Empate {m.get('empate','N/A')} / {partido['visita']} {m.get('visita','N/A')}",
            call.message.chat.id,call.message.message_id,parse_mode="HTML",reply_markup=markup)
    elif data.startswith("ia_"):
        pid=data.replace("ia_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: return
        bot.answer_callback_query(call.id,"Generando análisis...")
        bot.send_message(call.message.chat.id,f"⏳ Analizando <b>{partido['local']} vs {partido['visita']}</b>...","HTML")
        enviar_largo(generar_analisis_ia(partido,obtener_momios(partido),"completo"))
    elif data.startswith("momios_"):
        pid=data.replace("momios_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: return
        bot.answer_callback_query(call.id,"Consultando momios...")
        bot.send_message(call.message.chat.id,fmt_momios(partido,obtener_momios(partido,True)),parse_mode="HTML")
    elif data.startswith("valor_"):
        pid=data.replace("valor_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: return
        bot.answer_callback_query(call.id,"Calculando valor...")
        class FakeMsg:
            text=f"/valor {partido['local']}"
            class chat:
                id=TELEGRAM_CHAT_ID
        cmd_valor(FakeMsg())
    elif data=="todas_ligas":
        bot.answer_callback_query(call.id,"Cargando agenda...")
        cmd_agenda(call.message)
    elif data=="mundial_menu":
        bot.answer_callback_query(call.id,"Mundial 2026")
        class FakeMsg:
            text="/mundial"
            class chat:
                id=TELEGRAM_CHAT_ID
        cmd_mundial(FakeMsg())
    elif data.startswith("mundial_grupo_"):
        grupo=data.replace("mundial_grupo_","")
        bot.answer_callback_query(call.id,f"Grupo {grupo}")
        bot.send_message(call.message.chat.id,formatear_fixtures_grupo_telegram(grupo),parse_mode="HTML")
    elif data=="mundial_equipo_mexico":
        bot.answer_callback_query(call.id,"México")
        fixtures=get_fixture_por_equipo("México")
        texto="🇲🇽 <b>MÉXICO — MUNDIAL 2026</b>\n━━━━━━━━━━━━━━━━━━\n"
        for p in fixtures:
            alt=f" ⛰️{p['altitud']}m" if p["altitud"]>1800 else ""
            texto+=f"\n⚽ {p['local']} vs {p['visita']}{alt}\n   {p['fecha']} · {p['hora_cst']} · {p['estadio']}\n   {p['momios']['local']} / {p['momios']['empate']} / {p['momios']['visita']}\n"
        bot.send_message(call.message.chat.id,texto,parse_mode="HTML")
    elif data=="mundial_fecha_inicio":
        bot.answer_callback_query(call.id,"Partidos inaugurales")
        texto="📅 <b>MUNDIAL 2026 — INAUGURALES</b>\n━━━━━━━━━━━━━━━━━━\n"
        for fecha in ["2026-06-11","2026-06-12","2026-06-13"]:
            ps=get_fixtures_fecha(fecha)
            if ps:
                texto+=f"\n<b>{fecha}</b>\n"
                for p in ps: texto+=f"  ⚽ {p['local']} vs {p['visita']} ({p['hora_cst']})\n"
        bot.send_message(call.message.chat.id,texto,parse_mode="HTML")
    elif data=="roi":
        bot.answer_callback_query(call.id,"Generando ROI...")
        bot.send_message(call.message.chat.id,formatear_roi_telegram(generar_reporte_roi()),parse_mode="HTML")
    elif data=="menu":
        cmd_menu(call.message)


# ══════════════════════════════════════════════════════════════
# MONITOR AUTOMÁTICO
# ══════════════════════════════════════════════════════════════
def monitor_alertas():
    log.info("Monitor de alertas iniciado")
    while True:
        try:
            ahora=datetime.now()
            for p in PARTIDOS_HOY:
                try:
                    dt=datetime.strptime(f"{p['fecha']} {p['hora_cst']}","%Y-%m-%d %H:%M")
                    mins=(dt-ahora).total_seconds()/60
                    if 115<=mins<=125:    alerta_2h(p)
                    elif 55<=mins<=65:    alerta_1h(p)
                    elif 28<=mins<=33:    alerta_30min(p)
                    elif 0<mins<360 and ODDS_API_KEY:
                        datos=obtener_momios(p)
                        if datos and datos.get("movimientos"):
                            snap={}
                            if datos.get("books"):
                                h=datos["books"][0].get("h2h",{})
                                if h.get("local"):  snap["local"]=decimal_to_american(h["local"])
                                if h.get("empate"): snap["empate"]=decimal_to_american(h["empate"])
                                if h.get("visita"): snap["visita"]=decimal_to_american(h["visita"])
                            if snap: registrar_snapshot_momios(p["id"],f"{p['local']} vs {p['visita']}",snap)
                except: pass
        except Exception as e:
            log.error(f"Monitor: {e}")
        time.sleep(300)

# ══════════════════════════════════════════════════════════════
# DASHBOARD FLASK
# ══════════════════════════════════════════════════════════════
app=Flask(__name__)

@app.route("/")
def index():
    return ("<h1 style='font-family:monospace;color:#ffc840;background:#0c0c10;padding:30px'>"
            "⚡ SindicatoBot v4.2 — Online</h1>"
            "<p style='font-family:monospace;color:#aaa;padding:0 30px'>"
            "Bot de Telegram activo. Dashboard: abre sindicatobot_dashboard.html en tu navegador.</p>")

@app.route("/health")
def health():
    return jsonify({"status":"online","time":datetime.now().isoformat(),"partidos":len(PARTIDOS_HOY)})

@app.route("/api/agenda")
def api_agenda():
    return jsonify([{"nombre":f"{p['local']} vs {p['visita']}","hora":p.get("hora_cst",""),
                     "liga":p.get("liga",""),"fase":p.get("fase","")} for p in todos_los_partidos()[:20]])

@app.route("/api/predicciones")
def api_predicciones():
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT partido_nombre,mercado,confianza,fue_correcto FROM predicciones ORDER BY ts DESC LIMIT 10")
        rows=c.fetchall(); conn.close()
        return jsonify([{"partido":r[0],"mercado":r[1],"confianza":r[2],"correcto":r[3]} for r in rows])
    except: return jsonify([])

@app.route("/api/alertas")
def api_alertas():
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT tipo,partido_nombre,enviada,ts FROM alertas_log ORDER BY ts DESC LIMIT 15")
        rows=c.fetchall(); conn.close()
        return jsonify([{"tipo":r[0],"partido":r[1],"enviada":r[2],"hora":r[3][:16]} for r in rows])
    except: return jsonify([])

def run_web():
    port=int(os.environ.get("PORT",5000))
    log.info(f"Web en puerto {port}")
    app.run(host="0.0.0.0",port=port,use_reloader=False)

# ══════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════
def run_bot():
    if not bot:
        log.warning("Falta TELEGRAM_TOKEN"); return
    log.info("Esperando 15s..."); time.sleep(15)
    while True:
        try:
            bot.remove_webhook(); time.sleep(2)
            log.info("Bot conectado OK")
            bot.infinity_polling(timeout=30,long_polling_timeout=30,
                                 skip_pending=True,allowed_updates=["message","callback_query"])
        except Exception as e:
            espera=30 if "409" in str(e) else 10
            log.error(f"Bot error: {str(e)[:60]} — retry {espera}s")
            time.sleep(espera)

if __name__=="__main__":
    log.info("="*55)
    log.info("  SINDICATOBOT v4.2 — ARRANCANDO")
    log.info(f"  Telegram:    {'OK' if TELEGRAM_TOKEN   else 'FALTA — configura TELEGRAM_TOKEN'}")
    log.info(f"  Chat ID:     {'OK' if TELEGRAM_CHAT_ID else 'FALTA — configura TELEGRAM_CHAT_ID'}")
    log.info(f"  Odds API:    {'OK' if ODDS_API_KEY     else 'sin momios en vivo'}")
    log.info(f"  Claude AI:   {'OK' if ANTHROPIC_KEY    else 'análisis básico'}")
    log.info(f"  OpenWeather: {'OK' if OPENWEATHER_KEY  else 'sin clima'}")
    log.info(f"  Partidos:    {len(PARTIDOS_HOY)}")
    log.info("="*55)
    inicializar_bd()
    inicializar_clv_tabla()
    inicializar_roi_tabla()
    Thread(target=run_web,        daemon=True).start()
    Thread(target=monitor_alertas,daemon=True).start()
    run_bot()
