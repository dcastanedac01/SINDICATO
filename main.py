"""
SINDICATOBOT v4.4
Cambios vs v4.2:
  · CORS habilitado (dashboard funciona online)
  · PARTIDOS_HOY dinámico — sin partido caducado
  · Mundial 2026 clickeable con análisis IA
  · MLB básico con modelo Poisson de carreras
  · Scraper Caliente.mx como fallback de momios
  · Agenda auto 24h via SofaScore
"""

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from threading import Thread
import telebot
from telebot import types
import requests
import sqlite3
import time
import logging
import os
import math
from datetime import datetime, timedelta
from scipy.stats import poisson

# ── CONFIG ─────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",    "8685030239:AAHHgvlGN-WEm-GzfOW1d-5ecJ7vn5Eg-Mqw")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",  "8685030239")
ODDS_API_KEY     = os.environ.get("ODDS_API_KEY",      "9a8fdf02aa5ec29ae88765c4c8f78576dcc48e8e96da746fad0b3ac183ad42d5")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "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")
OPENWEATHER_KEY  = os.environ.get("OPENWEATHER_API_KEY","3d0ec65a44abc9f16409f45003b71b53")
DB_PATH          = "sindicatobot.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sindicatobot")

# RPG HISTORICOS POR EQUIPO MLB (temporada 2025)
MLB_RPG = {
    "New York Yankees":5.4, "Los Angeles Dodgers":5.2,
    "Houston Astros":5.5, "Texas Rangers":4.7,
    "Atlanta Braves":5.1, "Philadelphia Phillies":5.0,
    "Baltimore Orioles":4.9, "Cleveland Guardians":4.6,
    "Minnesota Twins":4.8, "Seattle Mariners":4.5,
    "San Diego Padres":4.6, "Boston Red Sox":4.9,
    "New York Mets":4.7, "Milwaukee Brewers":4.5,
    "Chicago Cubs":4.6, "San Francisco Giants":4.4,
    "Toronto Blue Jays":4.8, "Detroit Tigers":4.5,
    "Tampa Bay Rays":4.7, "Arizona Diamondbacks":4.8,
    "Cincinnati Reds":4.9, "Kansas City Royals":4.6,
    "Pittsburgh Pirates":4.2, "Colorado Rockies":4.5,
    "Oakland Athletics":4.1, "Miami Marlins":4.0,
    "Chicago White Sox":3.9, "Washington Nationals":4.2,
    "Los Angeles Angels":4.3, "St. Louis Cardinals":4.5,
}
class _DummyBot:
    """Bot simulado para cuando no hay TELEGRAM_TOKEN configurado.
    Permite que los decoradores @bot.message_handler funcionen sin crashear."""
    def message_handler(self, **kwargs):
        def decorator(f): return f
        return decorator
    def callback_query_handler(self, **kwargs):
        def decorator(f): return f
        return decorator
    def send_message(self, *a, **kw): pass
    def edit_message_text(self, *a, **kw): pass
    def answer_callback_query(self, *a, **kw): pass
    def remove_webhook(self): pass
    def infinity_polling(self, **kwargs): pass

bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else _DummyBot()

# ══════════════════════════════════════════════════════════════
# MODELO POISSON — FÚTBOL
# ══════════════════════════════════════════════════════════════
def _tabla_poisson(xg_l, xg_v, max_g=7):
    tabla = {}
    for i in range(max_g+1):
        for j in range(max_g+1):
            tabla[(i,j)] = poisson.pmf(i,xg_l) * poisson.pmf(j,xg_v)
    return tabla

def calcular_probabilidades(xg_local, xg_visita):
    if xg_local  <= 0: xg_local  = 0.1
    if xg_visita <= 0: xg_visita = 0.1
    tabla = _tabla_poisson(xg_local, xg_visita)
    p_l   = sum(p for (g1,g2),p in tabla.items() if g1>g2)
    p_e   = sum(p for (g1,g2),p in tabla.items() if g1==g2)
    p_v   = sum(p for (g1,g2),p in tabla.items() if g2>g1)
    p_o15 = sum(p for (g1,g2),p in tabla.items() if g1+g2>1.5)
    p_o25 = sum(p for (g1,g2),p in tabla.items() if g1+g2>2.5)
    p_o35 = sum(p for (g1,g2),p in tabla.items() if g1+g2>3.5)
    p_o45 = sum(p for (g1,g2),p in tabla.items() if g1+g2>4.5)
    p_btts= sum(p for (g1,g2),p in tabla.items() if g1>0 and g2>0)
    p_gol1t = 1 - poisson.pmf(0,xg_local*0.45)*poisson.pmf(0,xg_visita*0.45)
    top = sorted(tabla.items(), key=lambda x:x[1], reverse=True)[:5]
    return {
        "local":round(p_l,4),"empate":round(p_e,4),"visita":round(p_v,4),
        "over15":round(p_o15,4),"under15":round(1-p_o15,4),
        "over25":round(p_o25,4),"under25":round(1-p_o25,4),
        "over35":round(p_o35,4),"under35":round(1-p_o35,4),
        "over45":round(p_o45,4),"btts_si":round(p_btts,4),"btts_no":round(1-p_btts,4),
        "gol_1t":round(p_gol1t,4),
        "dc_1x":round(p_l+p_e,4),"dc_x2":round(p_e+p_v,4),"dc_12":round(p_l+p_v,4),
        "top_marcadores":[(f"{g1}-{g2}",round(p*100,2)) for (g1,g2),p in top],
        "xg_local":xg_local,"xg_visita":xg_visita,"xg_total":round(xg_local+xg_visita,2),
    }

# ══════════════════════════════════════════════════════════════
# MODELO POISSON — MLB BÉISBOL
# ══════════════════════════════════════════════════════════════
def calcular_probabilidades_mlb(rpg_local, rpg_visita):
    if rpg_local  <= 0: rpg_local  = 0.1
    if rpg_visita <= 0: rpg_visita = 0.1
    max_r = 15
    tabla = {}
    for i in range(max_r+1):
        for j in range(max_r+1):
            tabla[(i,j)] = poisson.pmf(i,rpg_local)*poisson.pmf(j,rpg_visita)
    p_l = sum(p for (i,j),p in tabla.items() if i>j)
    p_e = sum(p for (i,j),p in tabla.items() if i==j)
    p_v = sum(p for (i,j),p in tabla.items() if j>i)
    p_l_final = p_l + p_e*0.5
    p_v_final = p_v + p_e*0.5
    p_o75 = sum(p for (i,j),p in tabla.items() if i+j>7.5)
    p_o85 = sum(p for (i,j),p in tabla.items() if i+j>8.5)
    p_o95 = sum(p for (i,j),p in tabla.items() if i+j>9.5)
    p_rl_l = sum(p for (i,j),p in tabla.items() if i-j>=2)
    p_rl_v = sum(p for (i,j),p in tabla.items() if j-i>=2)
    top = sorted(tabla.items(), key=lambda x:x[1], reverse=True)[:5]
    return {
        "local":round(p_l_final,4),"visita":round(p_v_final,4),
        "over75":round(p_o75,4),"under75":round(1-p_o75,4),
        "over85":round(p_o85,4),"under85":round(1-p_o85,4),
        "over95":round(p_o95,4),"under95":round(1-p_o95,4),
        "rl_local":round(p_rl_l,4),"rl_visita":round(p_rl_v,4),
        "total_esperado":round(rpg_local+rpg_visita,2),
        "top_scores":[(f"{i}-{j}",round(p*100,2)) for (i,j),p in top],
        "rpg_local":rpg_local,"rpg_visita":rpg_visita,
    }

# ══════════════════════════════════════════════════════════════
# CONVERSORES DE MOMIOS
# ══════════════════════════════════════════════════════════════
def american_to_decimal(ml):
    try:
        n = float(str(ml).replace("+",""))
        return round(n/100+1,4) if n>0 else round(100/abs(n)+1,4)
    except: return 2.0

def decimal_to_american(dec):
    try:
        d = float(dec)
        return f"+{int((d-1)*100)}" if d>=2 else f"-{int(100/(d-1))}"
    except: return "N/A"

def prob_implicita(ml):
    try:
        n = float(str(ml).replace("+",""))
        return abs(n)/(abs(n)+100) if n<0 else 100/(n+100)
    except: return 0.5

def prob_to_fair_momio(prob):
    if prob<=0 or prob>=1: return "N/A"
    return decimal_to_american(1/prob)

def calcular_ev(prob_sistema, momio_str, umbral=0.04):
    pc  = prob_implicita(momio_str)
    dec = american_to_decimal(momio_str)
    edge = prob_sistema - pc
    ev   = (prob_sistema*(dec-1)-(1-prob_sistema))*100
    return {
        "prob_sistema":round(prob_sistema*100,1),"prob_casino":round(pc*100,1),
        "edge_pct":round(edge*100,2),"ev_por_100":round(ev,2),
        "tiene_valor":edge>=umbral,"momio_justo":prob_to_fair_momio(prob_sistema),"decimal":dec,
    }

def kelly_criterion(prob, momio_str, fraccion=0.25, bankroll=1000):
    dec = american_to_decimal(momio_str)
    b   = dec-1; q = 1-prob
    if b<=0: return {"kelly_fraccional_pct":0,"apuesta_recomendada":0,"clasificacion":"NO APOSTAR"}
    k  = max(0,(b*prob-q)/b)*fraccion
    ap = bankroll*k
    cl = "ALTA CONVENCION" if k>0.10 else "VALOR MODERADO" if k>0.05 else "VALOR MARGINAL" if k>0.02 else "NO APOSTAR"
    return {"kelly_fraccional_pct":round(k*100,2),"apuesta_recomendada":round(ap,2),"clasificacion":cl}

def detectar_arbitraje(momios_1x2):
    p_l = prob_implicita(momios_1x2.get("local","+200"))
    p_e = prob_implicita(momios_1x2.get("empate","+300"))
    p_v = prob_implicita(momios_1x2.get("visita","+250"))
    suma = p_l+p_e+p_v
    margen = (suma-1)*100
    hay_arb = suma<1.0
    return {
        "suma_impl":round(suma*100,2),"margen_casino_pct":round(margen,2),
        "hay_arbitraje":hay_arb,"ganancia_garantizada":round((1/suma-1)*100,2) if hay_arb else 0,
        "probs":{"local":round(p_l*100,1),"empate":round(p_e*100,1),"visita":round(p_v*100,1)},
    }

def analizar_partido_completo(nombre_l, nombre_v, xg_l, xg_v, momios, bankroll=1000, umbral=0.04, fraccion=0.25):
    probs = calcular_probabilidades(xg_l, xg_v)
    MERCADOS = {
        "local":("local","Victoria Local"),"empate":("empate","Empate"),
        "visita":("visita","Victoria Visita"),"over25":("over25","Over 2.5 Goles"),
        "under25":("under25","Under 2.5 Goles"),"over15":("over15","Over 1.5 Goles"),
        "over35":("over35","Over 3.5 Goles"),"btts_si":("btts_si","BTTS (Si)"),
        "gol_1t":("gol_1t","Gol en 1er Tiempo"),
    }
    apuestas_valor = []
    for km,(kp,label) in MERCADOS.items():
        if km not in momios or kp not in probs: continue
        ps = probs[kp]; m = momios[km]
        if m in ("N/A","","TBD"): continue
        ev = calcular_ev(ps,m,umbral); k = kelly_criterion(ps,m,fraccion,bankroll)
        if ev["tiene_valor"]:
            apuestas_valor.append({
                "key":km,"label":label,"momio":m,
                "ev":ev["ev_por_100"],"edge":ev["edge_pct"],"prob_sis":ev["prob_sistema"],
                "kelly_pct":k["kelly_fraccional_pct"],"apuesta":k["apuesta_recomendada"],"clasif":k["clasificacion"],
            })
    apuestas_valor.sort(key=lambda x:x["ev"],reverse=True)
    arb = detectar_arbitraje(momios)
    return {
        "partido":f"{nombre_l} vs {nombre_v}","xg_local":xg_l,"xg_visita":xg_v,
        "xg_total":probs["xg_total"],"probabilidades":probs,
        "apuestas_valor":apuestas_valor,"top3":apuestas_valor[:3],
        "arbitraje":arb,"marcador_probable":probs["top_marcadores"][0] if probs["top_marcadores"] else ("1-0",0),
        "top_marcadores":probs["top_marcadores"],"bankroll":bankroll,
        "total_con_valor":len(apuestas_valor),"margen_casino":arb["margen_casino_pct"],
    }

# ══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════
def inicializar_bd():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    tablas = [
        """CREATE TABLE IF NOT EXISTS partidos (id TEXT PRIMARY KEY, fecha TEXT, local TEXT, visita TEXT,
            liga TEXT DEFAULT '', fase TEXT DEFAULT '', hora_cst TEXT DEFAULT '', estadio TEXT DEFAULT '',
            altitud INTEGER DEFAULT 0, goles_local INTEGER DEFAULT -1, goles_visita INTEGER DEFAULT -1,
            resultado TEXT DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS predicciones (id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT, partido_nombre TEXT, liga TEXT DEFAULT '', mercado TEXT, momio TEXT,
            confianza INTEGER, razonamiento TEXT, ev_estimado REAL DEFAULT 0,
            resultado_real TEXT DEFAULT NULL, fue_correcto INTEGER DEFAULT NULL,
            ganancia_real REAL DEFAULT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP, ts_resultado TEXT DEFAULT NULL)""",
        """CREATE TABLE IF NOT EXISTS alertas_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT, partido_nombre TEXT, mensaje TEXT, enviada INTEGER DEFAULT 0,
            ts TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS resultados (id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT UNIQUE, partido_nombre TEXT, liga TEXT DEFAULT '', fecha TEXT,
            goles_local INTEGER, goles_visita INTEGER, total_goles INTEGER, resultado TEXT,
            over_25 INTEGER, btts INTEGER, registrado_en TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS clv_tracker (id INTEGER PRIMARY KEY AUTOINCREMENT,
            partido_id TEXT, partido_nombre TEXT, liga TEXT DEFAULT '', fecha_partido TEXT,
            mercado TEXT, momio_apostado TEXT, prob_apostado REAL,
            momio_cierre TEXT DEFAULT NULL, prob_cierre REAL DEFAULT NULL, clv_pct REAL DEFAULT NULL,
            resultado TEXT DEFAULT NULL, ganancia_real REAL DEFAULT NULL, bankroll_usado REAL DEFAULT 0,
            apuesta_monto REAL DEFAULT 0, ts_apuesta TEXT DEFAULT CURRENT_TIMESTAMP, ts_cierre TEXT DEFAULT NULL)""",
        """CREATE TABLE IF NOT EXISTS roi_tracker (id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT, partido TEXT, liga TEXT DEFAULT '', mercado TEXT, momio TEXT,
            monto REAL, resultado TEXT, ganancia REAL, bankroll_inicio REAL DEFAULT 0,
            bankroll_fin REAL DEFAULT 0, ts TEXT DEFAULT CURRENT_TIMESTAMP)""",
    ]
    for t in tablas: c.execute(t)
    conn.commit(); conn.close()
    log.info("Base de datos lista")

# ======================================================
# AJUSTE AUTOMATICO DE XG
# ======================================================
def _init_xg_tabla():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS xg_ajuste (
        equipo TEXT UNIQUE, xg_base REAL DEFAULT 1.5,
        ajuste_acum REAL DEFAULT 0, partidos_n INTEGER DEFAULT 0,
        ultima_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

def ajustar_xg_post_resultado(equipo_local, equipo_visita, xg_l_pred, xg_v_pred, goles_l, goles_v):
    _init_xg_tabla()
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    for equipo, xg_pred, goles_real in [(equipo_local, xg_l_pred, goles_l),(equipo_visita, xg_v_pred, goles_v)]:
        diferencia = goles_real - xg_pred
        c.execute("SELECT xg_base, ajuste_acum, partidos_n FROM xg_ajuste WHERE equipo=?", (equipo,))
        row = c.fetchone()
        if row:
            xg_base, ajuste_acum, n = row
            nuevo = ajuste_acum + diferencia * 0.10
            c.execute("UPDATE xg_ajuste SET ajuste_acum=?, partidos_n=?, ultima_actualizacion=? WHERE equipo=?",
                      (round(nuevo,3), n+1, datetime.now().isoformat(), equipo))
        else:
            c.execute("INSERT INTO xg_ajuste (equipo,xg_base,ajuste_acum,partidos_n) VALUES(?,?,?,?)",
                      (equipo, xg_pred, round(diferencia*0.10,3), 1))
    conn.commit(); conn.close()
    log.info(f"xG ajustado: {equipo_local}({goles_l}g/{xg_l_pred}xG) | {equipo_visita}({goles_v}g/{xg_v_pred}xG)")

def obtener_xg_ajustado(equipo, xg_base):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT ajuste_acum, partidos_n FROM xg_ajuste WHERE equipo=?", (equipo,))
        row = c.fetchone(); conn.close()
        if row and row[1] >= 3:
            return round(max(0.1, xg_base + row[0]), 2)
    except: pass
    return xg_base


# ══════════════════════════════════════════════════════════════
# FIXTURES MUNDIAL 2026
# ══════════════════════════════════════════════════════════════
MUNDIAL_2026_FIXTURES = [
    {"id":"A1","grupo":"A","fase":"J1","fecha":"2026-06-11","hora_cst":"13:00","local":"Mexico","visita":"Sudafrica","estadio":"Estadio Azteca","ciudad":"Ciudad de Mexico, MX","altitud":2240,"tv":"Azteca/TUDN","xg_l":1.85,"xg_v":0.92,"momios":{"local":"-200","empate":"+340","visita":"+550","over":"-118","under":"+100","ou":"2.5","btts":"+120"}},
    {"id":"A2","grupo":"A","fase":"J2","fecha":"2026-06-17","hora_cst":"TBD","local":"Mexico","visita":"Corea del Sur","estadio":"SoFi Stadium","ciudad":"Inglewood, CA","altitud":30,"tv":"Azteca/TUDN","xg_l":1.72,"xg_v":1.15,"momios":{"local":"-165","empate":"+310","visita":"+450","over":"-120","under":"+100","ou":"2.5","btts":"-105"}},
    {"id":"A3","grupo":"A","fase":"J3","fecha":"2026-06-23","hora_cst":"TBD","local":"Rep. Checa","visita":"Mexico","estadio":"Por confirmar","ciudad":"TBD","altitud":0,"tv":"Por confirmar","xg_l":1.20,"xg_v":1.68,"momios":{"local":"+200","empate":"+260","visita":"-130","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    {"id":"J1","grupo":"J","fase":"J1","fecha":"2026-06-12","hora_cst":"TBD","local":"Argentina","visita":"Argelia","estadio":"AT&T Stadium","ciudad":"Arlington, TX","altitud":185,"tv":"TelevisaUnivision","xg_l":2.20,"xg_v":0.78,"momios":{"local":"-280","empate":"+400","visita":"+750","over":"-130","under":"+110","ou":"2.5","btts":"+130"}},
    {"id":"J2","grupo":"J","fase":"J2","fecha":"2026-06-18","hora_cst":"TBD","local":"Argentina","visita":"Austria","estadio":"Levi's Stadium","ciudad":"Santa Clara, CA","altitud":14,"tv":"TelevisaUnivision","xg_l":2.05,"xg_v":0.92,"momios":{"local":"-250","empate":"+380","visita":"+680","over":"-120","under":"+100","ou":"2.5","btts":"+110"}},
    {"id":"H1","grupo":"H","fase":"J1","fecha":"2026-06-12","hora_cst":"TBD","local":"Espana","visita":"Uruguay","estadio":"Gillette Stadium","ciudad":"Foxborough, MA","altitud":12,"tv":"Telemundo","xg_l":2.10,"xg_v":1.05,"momios":{"local":"-180","empate":"+320","visita":"+480","over":"-105","under":"-115","ou":"2.5","btts":"-110"}},
    {"id":"H2","grupo":"H","fase":"J2","fecha":"2026-06-18","hora_cst":"TBD","local":"Arabia S.","visita":"Espana","estadio":"Hard Rock Stadium","ciudad":"Miami, FL","altitud":2,"tv":"Telemundo","xg_l":0.85,"xg_v":2.25,"momios":{"local":"+500","empate":"+360","visita":"-220","over":"-115","under":"-105","ou":"2.5","btts":"+130"}},
    {"id":"D1","grupo":"D","fase":"J1","fecha":"2026-06-12","hora_cst":"TBD","local":"USA","visita":"Bosnia-H","estadio":"MetLife Stadium","ciudad":"East Rutherford, NJ","altitud":6,"tv":"Fox/Telemundo","xg_l":1.72,"xg_v":1.05,"momios":{"local":"-140","empate":"+320","visita":"+380","over":"-115","under":"-105","ou":"2.5","btts":"-120"}},
    {"id":"C1","grupo":"C","fase":"J1","fecha":"2026-06-13","hora_cst":"TBD","local":"Brasil","visita":"Marruecos","estadio":"Levi's Stadium","ciudad":"Santa Clara, CA","altitud":14,"tv":"TelevisaUnivision","xg_l":2.00,"xg_v":0.95,"momios":{"local":"-175","empate":"+350","visita":"+480","over":"-110","under":"-110","ou":"2.5","btts":"-120"}},
    {"id":"L1","grupo":"L","fase":"J1","fecha":"2026-06-13","hora_cst":"TBD","local":"Inglaterra","visita":"Croacia","estadio":"Lincoln Financial Field","ciudad":"Filadelfia, PA","altitud":12,"tv":"Fox/Telemundo","xg_l":2.05,"xg_v":1.00,"momios":{"local":"-185","empate":"+330","visita":"+500","over":"-115","under":"-105","ou":"2.5","btts":"-115"}},
    {"id":"F1","grupo":"F","fase":"J1","fecha":"2026-06-14","hora_cst":"TBD","local":"Portugal","visita":"Polonia","estadio":"Arrowhead Stadium","ciudad":"Kansas City, MO","altitud":315,"tv":"Telemundo","xg_l":1.95,"xg_v":1.10,"momios":{"local":"-180","empate":"+330","visita":"+480","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    {"id":"G1","grupo":"G","fase":"J1","fecha":"2026-06-15","hora_cst":"TBD","local":"Italia","visita":"Colombia","estadio":"NRG Stadium","ciudad":"Houston, TX","altitud":15,"tv":"Telemundo","xg_l":1.75,"xg_v":1.45,"momios":{"local":"-145","empate":"+300","visita":"+400","over":"-110","under":"-110","ou":"2.5","btts":"-115"}},
    {"id":"E1","grupo":"E","fase":"J1","fecha":"2026-06-15","hora_cst":"TBD","local":"Paises Bajos","visita":"Suecia","estadio":"SoFi Stadium","ciudad":"Inglewood, CA","altitud":30,"tv":"Fox/Telemundo","xg_l":1.90,"xg_v":1.05,"momios":{"local":"-160","empate":"+330","visita":"+440","over":"-110","under":"-110","ou":"2.5","btts":"-110"}},
    {"id":"B1","grupo":"B","fase":"J1","fecha":"2026-06-13","hora_cst":"TBD","local":"Canada","visita":"Bosnia-H","estadio":"BC Place","ciudad":"Vancouver, Canada","altitud":5,"tv":"TSN/Telemundo","xg_l":1.45,"xg_v":1.20,"momios":{"local":"-125","empate":"+280","visita":"+340","over":"-115","under":"-105","ou":"2.5","btts":"-115"}},
    {"id":"FINAL","grupo":"--","fase":"Final","fecha":"2026-07-19","hora_cst":"TBD","local":"Por definir","visita":"Por definir","estadio":"MetLife Stadium","ciudad":"East Rutherford, NJ","altitud":6,"tv":"Fox/Telemundo/Azteca","xg_l":0,"xg_v":0,"momios":{"local":"-110","empate":"+275","visita":"+280","over":"-110","under":"-110","ou":"2.5","btts":"-110"}},
]

def get_fixtures_grupo(grupo):
    return [f for f in MUNDIAL_2026_FIXTURES if f["grupo"]==grupo]

def get_fixture_por_equipo(nombre):
    nl = nombre.lower()
    return [f for f in MUNDIAL_2026_FIXTURES if nl in f["local"].lower() or nl in f["visita"].lower()]

def formatear_fixtures_grupo_telegram(grupo):
    partidos = get_fixtures_grupo(grupo)
    if not partidos: return f"Sin fixtures para Grupo {grupo}"
    texto = f"GRUPO {grupo} Mundial 2026\n"
    for p in partidos:
        alt = f" | Alt {p['altitud']}m" if p["altitud"]>1800 else ""
        texto += (f"\n{p['local']} vs {p['visita']}\n"
                 f"  {p['fecha']} {p['hora_cst']} CST\n"
                 f"  {p['estadio']}{alt}\n"
                 f"  {p['momios']['local']} / {p['momios']['empate']} / {p['momios']['visita']}\n")
    return texto

# ══════════════════════════════════════════════════════════════

# ======================================================
# AMISTOSOS INTERNACIONALES — PARTIDOS DE PRUEBA
# ======================================================
# Actualizar fechas y equipos segun el calendario real
PARTIDOS_AMISTOSOS = [
    {
        "id":"amist_mex_uru_jun","liga":"Amistoso Internacional","liga_key":"amistosos",
        "fase":"Amistoso","local":"Mexico","visita":"Uruguay",
        "estadio":"Estadio Azteca","ciudad":"Ciudad de Mexico, MX","altitud":2240,
        "hora_cst":"20:00","fecha":"2026-06-05","fecha_label":"5 Jun","tv":"Azteca/TUDN",
        "deporte":"futbol","odds_sport":"soccer_internation_friendlies",
        "contexto":"Amistoso previo al Mundial 2026. Mexico se prepara en casa con altitud de 2,240m.",
        "momios_static":{"local":"-150","empate":"+290","visita":"+380","over":"-110","under":"-110","ou":"2.5","btts":"-115"},
        "h2h":{"local":5,"empate":3,"visita":4,"prom_goles":2.6},
        "stats_local": {"xg":1.75,"goles_f":1.8,"goles_c":1.1,"corners":5.2,"tarjetas":1.9,"posesion":54},
        "stats_visita":{"xg":1.35,"goles_f":1.6,"goles_c":1.2,"corners":4.5,"tarjetas":2.3,"posesion":46},
        "arbitro":{"nombre":"Por confirmar","amarillas":4.0,"rojas":0.2,"penales":0.2},
        "bajas":[],"forma_local":["G","G","E","G","P"],"forma_visita":["G","E","G","P","G"],
    },
    {
        "id":"amist_arg_bol","liga":"Amistoso Internacional","liga_key":"amistosos",
        "fase":"Amistoso","local":"Argentina","visita":"Bolivia",
        "estadio":"Estadio Monumental","ciudad":"Buenos Aires, Argentina","altitud":25,
        "hora_cst":"18:00","fecha":"2026-06-06","fecha_label":"6 Jun","tv":"TelevisaUnivision",
        "deporte":"futbol","odds_sport":"soccer_internation_friendlies",
        "contexto":"Argentina afina forma antes del Mundial. Bolivia como sparring.",
        "momios_static":{"local":"-350","empate":"+420","visita":"+900","over":"-130","under":"+108","ou":"2.5","btts":"+150"},
        "h2h":{"local":8,"empate":2,"visita":1,"prom_goles":3.2},
        "stats_local": {"xg":2.30,"goles_f":2.5,"goles_c":0.6,"corners":6.8,"tarjetas":1.4,"posesion":68},
        "stats_visita":{"xg":0.65,"goles_f":0.8,"goles_c":2.2,"corners":3.1,"tarjetas":2.8,"posesion":32},
        "arbitro":{"nombre":"Por confirmar","amarillas":3.8,"rojas":0.1,"penales":0.3},
        "bajas":[],"forma_local":["G","G","G","G","E"],"forma_visita":["P","P","E","P","G"],
    },
    {
        "id":"amist_esp_col","liga":"Amistoso Internacional","liga_key":"amistosos",
        "fase":"Amistoso","local":"Espana","visita":"Colombia",
        "estadio":"Estadio de La Cartuja","ciudad":"Sevilla, Espana","altitud":7,
        "hora_cst":"13:00","fecha":"2026-06-07","fecha_label":"7 Jun","tv":"Telemundo",
        "deporte":"futbol","odds_sport":"soccer_internation_friendlies",
        "contexto":"Amistoso europeo. Espana campeona de Europa vs Colombia semifinalista Copa America.",
        "momios_static":{"local":"-160","empate":"+310","visita":"+420","over":"-108","under":"-112","ou":"2.5","btts":"-108"},
        "h2h":{"local":4,"empate":2,"visita":2,"prom_goles":2.4},
        "stats_local": {"xg":2.05,"goles_f":2.0,"goles_c":0.8,"corners":6.0,"tarjetas":1.6,"posesion":61},
        "stats_visita":{"xg":1.30,"goles_f":1.5,"goles_c":1.2,"corners":4.2,"tarjetas":2.1,"posesion":39},
        "arbitro":{"nombre":"Por confirmar","amarillas":4.1,"rojas":0.2,"penales":0.2},
        "bajas":[],"forma_local":["G","G","G","E","G"],"forma_visita":["G","G","P","G","G"],
    },
    {
        "id":"amist_usa_bra","liga":"Amistoso Internacional","liga_key":"amistosos",
        "fase":"Amistoso","local":"USA","visita":"Brasil",
        "estadio":"MetLife Stadium","ciudad":"East Rutherford, NJ","altitud":6,
        "hora_cst":"19:30","fecha":"2026-06-08","fecha_label":"8 Jun","tv":"Fox/Telemundo",
        "deporte":"futbol","odds_sport":"soccer_internation_friendlies",
        "contexto":"USA en casa ante Brasil. El Tri no juega pero es el partido de mayor interes en suelo americano.",
        "momios_static":{"local":"+195","empate":"+240","visita":"-130","over":"-112","under":"-108","ou":"2.5","btts":"-105"},
        "h2h":{"local":1,"empate":3,"visita":8,"prom_goles":2.8},
        "stats_local": {"xg":1.45,"goles_f":1.5,"goles_c":1.3,"corners":4.8,"tarjetas":2.0,"posesion":46},
        "stats_visita":{"xg":1.95,"goles_f":2.1,"goles_c":0.9,"corners":5.8,"tarjetas":1.7,"posesion":54},
        "arbitro":{"nombre":"Por confirmar","amarillas":4.3,"rojas":0.2,"penales":0.3},
        "bajas":[],"forma_local":["G","E","G","P","G"],"forma_visita":["G","G","G","E","G"],
    },
]

# PARTIDOS DE PRUEBA — FUTBOL Y MLB
# ══════════════════════════════════════════════════════════════
PARTIDOS_PRUEBA_FUTBOL = [
    # Solo se usan si SofaScore no retorna partidos
    {
        "id":"prueba_mex_saf","liga":"Mundial 2026 - Grupo A","liga_key":"amistosos","fase":"Jornada 1",
        "local":"Mexico","visita":"Sudafrica","estadio":"Estadio Azteca",
        "ciudad":"Ciudad de Mexico, MX","altitud":2240,"hora_cst":"13:00",
        "fecha":"2026-06-11","fecha_label":"11 Jun 2026","tv":"Azteca/TUDN","deporte":"futbol",
        "odds_sport":"soccer_internation_friendlies",
        "contexto":"Partido inaugural Mundial 2026. Mexico en el Azteca a 2,240m. La altura favorece historicamente al Tri.",
        "momios_static":{"local":"-200","empate":"+340","visita":"+550","over":"-118","under":"+100","ou":"2.5","btts":"+120"},
        "h2h":{"local":4,"empate":2,"visita":1,"prom_goles":2.5},
        "stats_local": {"xg":1.85,"goles_f":1.9,"goles_c":0.8,"corners":5.5,"tarjetas":1.8,"posesion":56},
        "stats_visita":{"xg":0.92,"goles_f":1.1,"goles_c":1.5,"corners":4.0,"tarjetas":2.2,"posesion":44},
        "arbitro":{"nombre":"Por confirmar","amarillas":4.2,"rojas":0.2,"penales":0.2},
        "bajas":[],"forma_local":["G","G","E","G","G"],"forma_visita":["P","G","P","E","G"],
    },
]

PARTIDOS_MLB_PRUEBA = [
    {
        "id":"mlb_nyy_lad","liga":"MLB","liga_key":"mlb","fase":"Temporada Regular",
        "local":"New York Yankees","visita":"Los Angeles Dodgers",
        "estadio":"Yankee Stadium","ciudad":"Nueva York, NY","altitud":5,
        "hora_cst":"18:05","fecha":datetime.now().strftime("%Y-%m-%d"),"fecha_label":"Hoy","tv":"ESPN",
        "odds_sport":"baseball_mlb","deporte":"beisbol",
        "contexto":"Partido de alto perfil. Las dos franquicias mas valiosas de MLB. Yankees en casa.",
        "momios_static":{"local":"-130","visita":"+110","over":"-110","under":"-110","ou":"8.5","rl_local":"+140","rl_visita":"-160"},
        "stats_local": {"rpg":5.2,"era_rival":3.8,"ba":0.261,"ops":0.785,"era_propio":4.1},
        "stats_visita":{"rpg":4.9,"era_rival":3.5,"ba":0.258,"ops":0.771,"era_propio":3.7},
        "forma_local":["G","P","G","G","P"],"forma_visita":["G","G","G","P","G"],
    },
    {
        "id":"mlb_hou_tex","liga":"MLB","liga_key":"mlb","fase":"Temporada Regular",
        "local":"Houston Astros","visita":"Texas Rangers",
        "estadio":"Minute Maid Park","ciudad":"Houston, TX","altitud":15,
        "hora_cst":"19:10","fecha":datetime.now().strftime("%Y-%m-%d"),"fecha_label":"Hoy","tv":"Apple TV+",
        "odds_sport":"baseball_mlb","deporte":"beisbol",
        "contexto":"Clasico de Texas. Rivalidad de division AL Oeste.",
        "momios_static":{"local":"-145","visita":"+125","over":"-115","under":"-105","ou":"9.0","rl_local":"+115","rl_visita":"-135"},
        "stats_local": {"rpg":5.5,"era_rival":3.6,"ba":0.265,"ops":0.798,"era_propio":3.9},
        "stats_visita":{"rpg":4.7,"era_rival":4.1,"ba":0.252,"ops":0.758,"era_propio":4.4},
        "forma_local":["G","G","G","P","G"],"forma_visita":["P","G","P","P","G"],
    },
]

# ══════════════════════════════════════════════════════════════

# ======================================================
# MLB — API OFICIAL + ODDS EN VIVO
# ======================================================

def calcular_momios_mlb(rpg_local, rpg_visita):
    """
    Calcula momios justos para MLB basados en Poisson cuando la Odds API no esta disponible.
    Incluye margen de 4.5% (vig tipico de MLB).
    """
    probs = calcular_probabilidades_mlb(rpg_local, rpg_visita)
    VIG = 1.045  # margen tipico de sportsbook MLB

    p_l = probs["local"] * VIG
    p_v = probs["visita"] * VIG

    # Moneyline
    ml_l = prob_to_fair_momio(probs["local"])
    ml_v = prob_to_fair_momio(probs["visita"])

    # Run Line -1.5 (favorito gana por 2+)
    p_rl_l = probs["rl_local"]
    p_rl_v = probs["rl_visita"]
    rl_l = prob_to_fair_momio(p_rl_l) if p_rl_l > 0.05 else "N/A"
    rl_v = prob_to_fair_momio(p_rl_v) if p_rl_v > 0.05 else "N/A"

    # Totales O/U
    total_esp = probs["total_esperado"]
    # Buscar linea mas cercana al total esperado (7.5, 8.0, 8.5, 9.0, 9.5)
    lineas = [7.5, 8.0, 8.5, 9.0, 9.5]
    linea = min(lineas, key=lambda x: abs(x - total_esp))

    # Calcular prob de over/under para esa linea
    max_r = 20
    p_over = sum(
        calcular_probabilidades_mlb.__wrapped__(i, j, rpg_local, rpg_visita)
        if i + j > linea else 0
        for i in range(max_r+1) for j in range(max_r+1)
    ) if False else probs.get(f"over{str(linea).replace('.','').replace('5','').replace('0','')}5", probs["over85"])

    # Simple: use over85 as proxy
    p_over_linea = probs["over85"] if linea == 8.5 else                    probs["over75"] if linea < 8.5 else probs["over95"]
    p_under_linea = 1 - p_over_linea

    over_ml = prob_to_fair_momio(p_over_linea)
    under_ml = prob_to_fair_momio(p_under_linea)

    return {
        "local": ml_l, "visita": ml_v,
        "rl_local": rl_l, "rl_visita": rl_v,
        "over": over_ml, "under": under_ml,
        "ou": str(linea),
        "fuente": "Modelo Poisson (sin Odds API)"
    }

_mlb_cache = {"ts": 0, "data": []}

def get_mlb_hoy():
    ahora = time.time()
    if ahora - _mlb_cache["ts"] < 1800 and _mlb_cache["data"]:
        return _mlb_cache["data"]
    resultado = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.mlb.com/",
        "Origin": "https://www.mlb.com",
    }
    for delta in range(2):
        fecha = (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                params={"sportId":"1","date":fecha,"hydrate":"team,linescore,venue,probablePitcher"},
                headers=headers, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
            for fecha_data in data.get("dates", []):
                for juego in fecha_data.get("games", []):
                    estado = juego.get("status", {}).get("abstractGameState", "")
                    home   = juego.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                    away   = juego.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                    venue  = juego.get("venue", {}).get("name", "")
                    game_pk = juego.get("gamePk", 0)
                    game_dt_str = juego.get("gameDate", "")
                    try:
                        game_dt = datetime.strptime(game_dt_str, "%Y-%m-%dT%H:%MZ") - timedelta(hours=6)
                        hora_cst = game_dt.strftime("%H:%M")
                        fecha_p  = game_dt.strftime("%Y-%m-%d")
                        dias = (game_dt.date() - datetime.now().date()).days
                        label = "Hoy" if dias==0 else "Manana" if dias==1 else game_dt.strftime("%d %b")
                    except:
                        hora_cst = "TBD"; fecha_p = fecha; label = "Proximo"
                    ls = juego.get("linescore", {})
                    sh = ls.get("teams", {}).get("home", {}).get("runs", "")
                    sa = ls.get("teams", {}).get("away", {}).get("runs", "")
                    score_str = f"{sa}-{sh}" if sh != "" else ""
                    ph = juego.get("teams",{}).get("home",{}).get("probablePitcher",{}).get("fullName","TBD")
                    pa = juego.get("teams",{}).get("away",{}).get("probablePitcher",{}).get("fullName","TBD")
                    inning = ls.get("currentInning", "")
                    inning_half = ls.get("inningHalf", "")
                    rpg_home = MLB_RPG.get(home, 4.5)
                    rpg_away = MLB_RPG.get(away, 4.3)
                    resultado.append({
                        "id": f"mlb_{game_pk}", "liga": "MLB", "liga_key": "mlb",
                        "fase": estado, "deporte": "beisbol", "local": home, "visita": away,
                        "hora_cst": hora_cst, "fecha": fecha_p, "fecha_label": label,
                        "estadio": venue, "ciudad": "", "altitud": 0,
                        "odds_sport": "baseball_mlb",
                        "contexto": f"MLB - {away} @ {home} | Pitchers: {pa} vs {ph}",
                        "momios_static": {"local":"N/A","visita":"N/A","over":"N/A","under":"N/A","ou":"8.5","rl_local":"N/A","rl_visita":"N/A"},
                        "stats_local":  {"rpg":rpg_home,"era_propio":4.0,"ba":0.255,"ops":0.750,"era_rival":3.9},
                        "stats_visita": {"rpg":rpg_away,"era_propio":4.2,"ba":0.250,"ops":0.740,"era_rival":4.1},
                        "forma_local": [], "forma_visita": [],
                        "score": score_str, "pitcher_local": ph, "pitcher_visita": pa,
                        "inning": inning, "inning_half": inning_half, "game_pk": game_pk,
                        "arbitro": {"nombre":"N/A","amarillas":0,"rojas":0,"penales":0},
                        "bajas": [], "h2h": {"local":0,"empate":0,"visita":0,"prom_goles":0},
                    })
            time.sleep(0.3)
        except Exception as e:
            log.error(f"MLB Stats API {fecha}: {e}")

    # Enriquecer con momios de Odds API
    if resultado and ODDS_API_KEY:
        try:
            r = requests.get("https://api.odds-api.io/v4/sports/baseball_mlb/odds",
                params={"apiKey":ODDS_API_KEY,"regions":"us","markets":"h2h,totals","oddsFormat":"american"},
                timeout=12)
            if r.status_code == 200:
                odds_data = r.json()
                if isinstance(odds_data, list):
                    for partido in resultado:
                        hn = [w for w in partido["local"].lower().split() if len(w)>3]
                        an = [w for w in partido["visita"].lower().split() if len(w)>3]
                        ev = next((e for e in odds_data
                            if any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in hn)
                            and any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in an)), None)
                        if ev:
                            for book in ev.get("bookmakers", [])[:1]:
                                for mkt in book.get("markets", []):
                                    out = mkt.get("outcomes", [])
                                    if mkt["key"] == "h2h":
                                        for o in out:
                                            if o["name"] == ev.get("home_team"): partido["momios_static"]["local"] = decimal_to_american(o["price"])
                                            elif o["name"] == ev.get("away_team"): partido["momios_static"]["visita"] = decimal_to_american(o["price"])
                                    elif mkt["key"] == "totals":
                                        for o in out:
                                            if o["name"] == "Over": partido["momios_static"]["over"] = decimal_to_american(o["price"]); partido["momios_static"]["ou"] = str(o.get("point","8.5"))
                                            elif o["name"] == "Under": partido["momios_static"]["under"] = decimal_to_american(o["price"])
        except Exception as e:
            log.warning(f"Odds API MLB: {e}")

    # Calcular momios justos con Poisson para partidos sin momios de Odds API
    for partido in resultado:
        m = partido["momios_static"]
        if m.get("local") == "N/A":
            rpg_l = partido["stats_local"].get("rpg", 4.5)
            rpg_v = partido["stats_visita"].get("rpg", 4.3)
            probs = calcular_probabilidades_mlb(rpg_l, rpg_v)
            partido["momios_static"]["local"]     = prob_to_fair_momio(probs["local"])
            partido["momios_static"]["visita"]    = prob_to_fair_momio(probs["visita"])
            partido["momios_static"]["rl_local"]  = prob_to_fair_momio(probs["rl_local"]) if probs["rl_local"] > 0.05 else "N/A"
            partido["momios_static"]["rl_visita"] = prob_to_fair_momio(probs["rl_visita"]) if probs["rl_visita"] > 0.05 else "N/A"
            # Over/Under basado en total esperado
            total = probs["total_esperado"]
            if total < 8.0:
                partido["momios_static"]["ou"]    = "7.5"
                partido["momios_static"]["over"]  = prob_to_fair_momio(probs["over75"])
                partido["momios_static"]["under"] = prob_to_fair_momio(1-probs["over75"])
            elif total < 9.0:
                partido["momios_static"]["ou"]    = "8.5"
                partido["momios_static"]["over"]  = prob_to_fair_momio(probs["over85"])
                partido["momios_static"]["under"] = prob_to_fair_momio(1-probs["over85"])
            else:
                partido["momios_static"]["ou"]    = "9.5"
                partido["momios_static"]["over"]  = prob_to_fair_momio(probs["over95"])
                partido["momios_static"]["under"] = prob_to_fair_momio(1-probs["over95"])
            partido["momios_poisson"] = True  # flag para mostrar en UI

    if not resultado:
        log.warning("MLB API sin datos, usando fallback")
        resultado = [{
            "id":"mlb_fallback_1","liga":"MLB","liga_key":"mlb","fase":"Preview","deporte":"beisbol",
            "local":"New York Yankees","visita":"Los Angeles Dodgers","hora_cst":"18:05",
            "fecha":datetime.now().strftime("%Y-%m-%d"),"fecha_label":"Hoy","estadio":"Yankee Stadium",
            "ciudad":"Nueva York, NY","altitud":5,"odds_sport":"baseball_mlb",
            "contexto":"MLB - Datos de respaldo (API temporalmente no disponible).",
            "momios_static":{"local":"-130","visita":"+110","over":"-110","under":"-110","ou":"8.5","rl_local":"+140","rl_visita":"-160"},
            "stats_local": {"rpg":5.4,"era_propio":4.1,"ba":0.261,"ops":0.785,"era_rival":3.8},
            "stats_visita":{"rpg":5.2,"era_propio":3.7,"ba":0.258,"ops":0.771,"era_rival":3.5},
            "forma_local":["G","P","G","G","P"],"forma_visita":["G","G","G","P","G"],
            "score":"","pitcher_local":"TBD","pitcher_visita":"TBD","inning":"","inning_half":"","game_pk":0,
            "arbitro":{"nombre":"N/A","amarillas":0,"rojas":0,"penales":0},
            "bajas":[],"h2h":{"local":0,"empate":0,"visita":0,"prom_goles":0},
        }]

    _mlb_cache.update({"ts": ahora, "data": resultado})
    log.info(f"MLB: {len(resultado)} partidos")
    return resultado

# SOFASCORE — AGENDA 24H
# ══════════════════════════════════════════════════════════════
LIGAS = {
    "liga_mx":   {"nombre":"Liga MX","sofa_id":11621,"odds_sport":"soccer_mexico_ligamx","deporte":"futbol"},
    "la_liga":   {"nombre":"La Liga","sofa_id":8,"odds_sport":"soccer_spain_la_liga","deporte":"futbol"},
    "champions": {"nombre":"Champions League","sofa_id":7,"odds_sport":"soccer_uefa_champs_league","deporte":"futbol"},
    "premier":   {"nombre":"Premier League","sofa_id":17,"odds_sport":"soccer_epl","deporte":"futbol"},
    "serie_a":   {"nombre":"Serie A","sofa_id":23,"odds_sport":"soccer_italy_serie_a","deporte":"futbol"},
    "bundesliga": {"nombre":"Bundesliga","sofa_id":35,"odds_sport":"soccer_germany_bundesliga","deporte":"futbol"},
    "ligue_1":   {"nombre":"Ligue 1","sofa_id":34,"odds_sport":"soccer_france_ligue_1","deporte":"futbol"},
    "amistosos": {"nombre":"Amistosos Int.","sofa_id":11161,"odds_sport":"soccer_internation_friendlies","deporte":"futbol"},
}
SOFA_H = {"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)","Referer":"https://www.sofascore.com/","Accept":"application/json"}
_agenda_cache = {"ts":0,"data":[]}

def sofa_get(path):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/{path}",headers=SOFA_H,timeout=10)
        return r.json() if r.status_code==200 else None
    except: return None

def get_agenda_24h():
    ahora = time.time()
    if ahora-_agenda_cache["ts"]<900 and _agenda_cache["data"]:
        return _agenda_cache["data"]
    resultado = list(PARTIDOS_PRUEBA_FUTBOL)
    ids_vistos = {p["id"] for p in resultado}
    hoy = datetime.now()
    for delta in range(2):
        fecha = (hoy+timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            data = sofa_get(f"sport/football/scheduled-events/{fecha}")
            eventos = data.get("events",[]) if data else []
            for lk,info in LIGAS.items():
                filtrados = [e for e in eventos
                             if e.get("tournament",{}).get("uniqueTournament",{}).get("id")==info["sofa_id"]]
                for ev in filtrados:
                    eid = f"{lk}_{ev['id']}"
                    if eid in ids_vistos: continue
                    home=ev.get("homeTeam",{}); away=ev.get("awayTeam",{})
                    ts_ev=ev.get("startTimestamp",0)
                    try:
                        dt=(datetime.utcfromtimestamp(ts_ev)-timedelta(hours=6))
                        hora_cst=dt.strftime("%H:%M"); fecha_p=dt.strftime("%Y-%m-%d")
                        dias=(dt.date()-datetime.now().date()).days
                        label="Hoy" if dias==0 else "Manana" if dias==1 else dt.strftime("%d %b")
                    except: hora_cst="00:00"; fecha_p=fecha; label="Proximo"
                    rnd=ev.get("roundInfo",{}); fase=rnd.get("name") or f"J{rnd.get('round','')}"
                    p={
                        "id":eid,"sofa_event_id":ev["id"],"liga":info["nombre"],"liga_key":lk,
                        "fase":fase,"deporte":"futbol","local":home.get("name","?"),"visita":away.get("name","?"),
                        "hora_cst":hora_cst,"fecha":fecha_p,"fecha_label":label,
                        "estadio":ev.get("venue",{}).get("name",""),"ciudad":"","altitud":0,
                        "odds_sport":info["odds_sport"],"contexto":f"{info['nombre']} - {fase}",
                        "momios_static":{"local":"N/A","empate":"N/A","visita":"N/A","over":"N/A","under":"N/A","ou":"2.5","btts":"N/A"},
                        "arbitro":{"nombre":"Por confirmar","amarillas":4.2,"rojas":0.2,"penales":0.2},
                        "bajas":[],"h2h":{"local":0,"empate":0,"visita":0,"prom_goles":2.5},
                        "stats_local": {"xg":1.5,"goles_f":1.5,"goles_c":1.2,"corners":5.0,"tarjetas":2.0,"posesion":50},
                        "stats_visita":{"xg":1.4,"goles_f":1.4,"goles_c":1.3,"corners":4.8,"tarjetas":2.1,"posesion":50},
                        "forma_local":[],"forma_visita":[],
                    }
                    resultado.append(p); ids_vistos.add(eid)
            time.sleep(0.3)
        except Exception as e: log.error(f"SofaScore {fecha}: {e}")

    _agenda_cache.update({"ts":ahora,"data":resultado})
    log.info(f"Agenda 24h: {len(resultado)} partidos")
    return resultado

def todos_los_partidos():
    agenda = get_agenda_24h()
    ids_vistos = {p["id"] for p in agenda}
    # Agregar fixtures del Mundial como partidos clickeables
    for f in MUNDIAL_2026_FIXTURES:
        fid = f"mundial_{f['id']}"
        if fid in ids_vistos: continue
        p = {
            "id":fid,"liga":f"Mundial 2026 - Grupo {f['grupo']}","liga_key":"amistosos",
            "fase":f["fase"],"deporte":"futbol","local":f["local"],"visita":f["visita"],
            "hora_cst":f["hora_cst"],"fecha":f["fecha"],"fecha_label":f["fecha"],
            "estadio":f["estadio"],"ciudad":f["ciudad"],"altitud":f["altitud"],
            "odds_sport":"soccer_internation_friendlies",
            "contexto":f"Mundial 2026 - Grupo {f['grupo']} - {f['fase']}",
            "momios_static":f["momios"],
            "arbitro":{"nombre":"Por confirmar","amarillas":4.2,"rojas":0.2,"penales":0.2},
            "bajas":[],"h2h":{"local":0,"empate":0,"visita":0,"prom_goles":2.4},
            "stats_local": {"xg":f.get("xg_l",1.5),"goles_f":1.5,"goles_c":1.2,"corners":5.0,"tarjetas":2.0,"posesion":50},
            "stats_visita":{"xg":f.get("xg_v",1.4),"goles_f":1.4,"goles_c":1.3,"corners":4.8,"tarjetas":2.1,"posesion":50},
            "forma_local":[],"forma_visita":[],
        }
        agenda.append(p); ids_vistos.add(fid)
    # Agregar amistosos internacionales
    for a in PARTIDOS_AMISTOSOS:
        if a["id"] not in ids_vistos:
            agenda.append(a); ids_vistos.add(a["id"])
    # Agregar MLB real (API oficial)
    for m in get_mlb_hoy():
        if m["id"] not in ids_vistos:
            agenda.append(m); ids_vistos.add(m["id"])
    return agenda

def buscar_partido(termino):
    tl = termino.lower()
    for p in todos_los_partidos():
        if (tl in p.get("local","").lower() or tl in p.get("visita","").lower()
                or tl in p.get("liga","").lower()):
            return p
    return None

# ══════════════════════════════════════════════════════════════
# ODDS API + SCRAPER CALIENTE
# ══════════════════════════════════════════════════════════════
_cache_odds = {}

def obtener_momios(partido, forzar=False):
    pid=partido.get("id",""); ahora=time.time()
    cached=_cache_odds.get(pid)
    if cached and not forzar and ahora-cached["ts"]<300: return cached["data"]
    sport=partido.get("odds_sport","soccer_epl")
    if not ODDS_API_KEY: return None
    try:
        r=requests.get(f"https://api.odds-api.io/v4/sports/{sport}/odds",
            params={"apiKey":ODDS_API_KEY,"regions":"us,eu,latam","markets":"h2h,totals","oddsFormat":"american"},
            timeout=12)
        if r.status_code!=200: return None
        eventos=r.json()
        if not isinstance(eventos,list): return None
        pals_l=[w for w in partido["local"].lower().split() if len(w)>3]
        pals_v=[w for w in partido["visita"].lower().split() if len(w)>3]
        ev=next((e for e in eventos
                 if any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in pals_l)
                 and any(p in (e.get("home_team","")+e.get("away_team","")).lower() for p in pals_v)),None)
        if not ev: return None
        resultado={"books":[],"home_team":ev.get("home_team"),"away_team":ev.get("away_team")}
        for book in ev.get("bookmakers",[])[:6]:
            bd={"nombre":book.get("title",""),"h2h":{},"totals":{}}
            for mkt in book.get("markets",[]):
                out=mkt.get("outcomes",[])
                if mkt["key"]=="h2h":
                    for o in out:
                        if o["name"]==ev.get("home_team"):   bd["h2h"]["local"]=o["price"]
                        elif o["name"]=="Draw":              bd["h2h"]["empate"]=o["price"]
                        elif o["name"]==ev.get("away_team"): bd["h2h"]["visita"]=o["price"]
                elif mkt["key"]=="totals":
                    for o in out:
                        if o["name"]=="Over":   bd["totals"]["over"]=o["price"]; bd["totals"]["linea"]=o.get("point")
                        elif o["name"]=="Under": bd["totals"]["under"]=o["price"]
            resultado["books"].append(bd)
        _cache_odds[pid]={"ts":ahora,"data":resultado}
        return resultado
    except Exception as e: log.error(f"Odds API: {e}"); return None

_caliente_cache={"ts":0,"data":{}}
def obtener_momios_caliente(equipo_local, equipo_visita):
    ahora=time.time()
    ck=f"{equipo_local}_{equipo_visita}".lower().replace(" ","_")
    if ahora-_caliente_cache["ts"]<3600 and ck in _caliente_cache["data"]:
        return _caliente_cache["data"][ck]
    try:
        headers={"User-Agent":"Mozilla/5.0","Accept":"application/json","Referer":"https://www.caliente.mx/"}
        r=requests.get("https://www.caliente.mx/api/sb/sports/events",
            params={"sportId":"1","limit":"50"},headers=headers,timeout=10)
        if r.status_code==200:
            data=r.json()
            eventos=data.get("data",{}).get("events",[]) if isinstance(data,dict) else []
            el=equipo_local.lower(); ev_v=equipo_visita.lower()
            for ev in eventos:
                hn=ev.get("homeTeam","").lower(); an=ev.get("awayTeam","").lower()
                if any(w in hn for w in el.split()) and any(w in an for w in ev_v.split()):
                    odds=ev.get("odds",{})
                    result={"local":odds.get("1","N/A"),"empate":odds.get("X","N/A"),"visita":odds.get("2","N/A"),"fuente":"Caliente.mx"}
                    _caliente_cache["data"][ck]=result; _caliente_cache["ts"]=ahora
                    return result
    except Exception as e: log.warning(f"Caliente: {e}")
    return None

def fmt_momios(partido, momios_data):
    local=partido["local"]; visita=partido["visita"]; m=partido.get("momios_static",{})
    t=(f"<b>MOMIOS - {local} vs {visita}</b>\n{partido.get('estadio','')} | {partido.get('hora_cst','')} CST\n\n")
    if momios_data and momios_data.get("books"):
        t+=f"EN VIVO - {len(momios_data['books'])} casas\n\n"
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
        cal=obtener_momios_caliente(local,visita)
        if cal: t+=f"<b>Caliente.mx</b>\n  1X2: {cal.get('local','N/A')} / {cal.get('empate','N/A')} / {cal.get('visita','N/A')}\n\n"
        t+=(f"Referencia: {local} {m.get('local','N/A')} / Empate {m.get('empate','N/A')} / {visita} {m.get('visita','N/A')}\n"
            f"O/U {m.get('ou','2.5')}: Over {m.get('over','N/A')} / Under {m.get('under','N/A')}\n\n")
    t+=f"Impl: {local} {prob_implicita(m.get('local','0'))*100:.1f}% | Empate {prob_implicita(m.get('empate','0'))*100:.1f}% | {visita} {prob_implicita(m.get('visita','0'))*100:.1f}%"
    return t

# ══════════════════════════════════════════════════════════════
# ANALISIS IA — CLAUDE
# ══════════════════════════════════════════════════════════════
def _get_analisis_cache(partido_id):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT razonamiento, ts FROM predicciones WHERE partido_id=? ORDER BY ts DESC LIMIT 1", (partido_id,))
        row = c.fetchone(); conn.close()
        if row:
            texto, ts_str = row
            ts = datetime.fromisoformat(ts_str)
            if (datetime.now() - ts).total_seconds() < 7200:
                return texto
    except: pass
    return None

def generar_analisis_ia(partido, momios_data=None, tipo="completo"):
    if not ANTHROPIC_KEY: return _analisis_fallback(partido)
    # Cache 2h para analisis completo
    if tipo == "completo":
        cached = _get_analisis_cache(partido["id"])
        if cached:
            log.info(f"Analisis IA desde cache: {partido['id']}")
            return cached
    deporte=partido.get("deporte","futbol")
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    m=partido.get("momios_static",{}); arb=partido.get("arbitro",{})
    bajas="\n".join(partido.get("bajas",[])) or "Sin bajas confirmadas"
    momios_str=f"Local {m.get('local','N/A')} / Empate {m.get('empate','N/A')} / Visita {m.get('visita','N/A')}"
    if momios_data and momios_data.get("books"):
        libros=[f"{bk['nombre']}:{decimal_to_american(bk['h2h'].get('local',0))}/{decimal_to_american(bk['h2h'].get('visita',0))}"
                for bk in momios_data["books"][:3] if bk.get("h2h")]
        if libros: momios_str="VIVO - "+" | ".join(libros)
    max_tok=600 if tipo=="alerta" else 3500

    if deporte=="beisbol":
        prompt=(f"Analista MLB del Sindicato. Analisis {'conciso 150 palabras' if tipo=='alerta' else 'COMPLETO minimo 600 palabras'}.\n\n"
                f"{partido['local']} vs {partido['visita']} | {partido.get('liga','')} | {partido.get('estadio','')}\n"
                f"Contexto: {partido.get('contexto','')}\nMomios: {momios_str} | O/U: {m.get('ou','8.5')}\n\n"
                f"{partido['local']}: {sl.get('rpg',4.5)} R/J | ERA: {sl.get('era_propio',4.0)} | BA: {sl.get('ba',0.260)}\n"
                f"{partido['visita']}: {sv.get('rpg',4.3)} R/J | ERA: {sv.get('era_propio',4.2)} | BA: {sv.get('ba',0.255)}\n\n"
                f"Secciones: 1 PITCHEO 2 OFENSIVA 3 TENDENCIAS 4 MOMIOS Y EV 5 TOP 3 APUESTAS\nEspanol.")
    elif tipo=="alerta":
        prompt=(f"Analista senior del Sindicato. Analisis PRE-PARTIDO 150-200 palabras.\n"
                f"{partido['local']} vs {partido['visita']} - {partido.get('fase','')} {partido.get('hora_cst','')} CST\n"
                f"Liga: {partido.get('liga','')} | xG: {sl.get('xg',1.5)} vs {sv.get('xg',1.4)}\n"
                f"Momios: {momios_str} | Arbitro: {arb.get('nombre','N/D')} | Bajas: {bajas}\n\n"
                f"Estructura: FACTOR CLAVE | APUESTA RECOMENDADA [mercado] [momio] prob X% | RIESGO | BANKROLL X%\nEspanol.")
    else:
        prompt=(f"Analista senior del Sindicato. Analisis COMPLETO minimo 700 palabras.\n\n"
                f"{partido['local']} vs {partido['visita']}\n"
                f"Liga: {partido.get('liga','')} | Fase: {partido.get('fase','')}\n"
                f"Estadio: {partido.get('estadio','')} | Altitud: {partido.get('altitud',0)}m\n"
                f"Arbitro: {arb.get('nombre','N/D')} ({arb.get('amarillas',4.2)} am/p)\n"
                f"Contexto: {partido.get('contexto','')}\nMomios: {momios_str}\n"
                f"H2H: {partido['local']} {partido.get('h2h',{}).get('local',0)}V / {partido.get('h2h',{}).get('empate',0)}E / {partido['visita']} {partido.get('h2h',{}).get('visita',0)}V\n"
                f"{partido['local']}: xG {sl.get('xg',1.5)} | GF/p {sl.get('goles_f',1.5)} | GC/p {sl.get('goles_c',1.2)} | Forma: {'-'.join(partido.get('forma_local',[]))}\n"
                f"{partido['visita']}: xG {sv.get('xg',1.4)} | GF/p {sv.get('goles_f',1.4)} | GC/p {sv.get('goles_c',1.3)} | Forma: {'-'.join(partido.get('forma_visita',[]))}\n"
                f"BAJAS: {bajas}\n\n"
                f"8 secciones: 1 CONTEXTO 2 TACTICO 3 BAJAS 4 ALTITUD 5 MOMIOS Y EV 6 MERCADOS 7 ARBITRAL 8 TOP 3 APUESTAS\nEspanol.")
    try:
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":max_tok,"messages":[{"role":"user","content":prompt}]},
            timeout=50)
        if r.status_code==200:
            texto=r.json()["content"][0]["text"]
            _guardar_prediccion(partido,texto)
            return texto
        log.error(f"Anthropic HTTP {r.status_code}")
        return _analisis_fallback(partido)
    except Exception as e: log.error(f"Claude: {e}"); return _analisis_fallback(partido)

def _analisis_fallback(partido):
    m=partido.get("momios_static",{}); sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    return (f"<b>ANALISIS - {partido['local']} vs {partido['visita']}</b>\n"
            f"xG: {sl.get('xg',sl.get('rpg',1.5))} + {sv.get('xg',sv.get('rpg',1.4))}\n"
            f"<i>Configura ANTHROPIC_API_KEY para analisis completo.</i>")

def _guardar_prediccion(partido, texto):
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        m=partido.get("momios_static",{})
        c.execute("INSERT INTO predicciones (partido_id,partido_nombre,liga,mercado,momio,confianza,razonamiento) VALUES(?,?,?,?,?,?,?)",
                  (partido["id"],f"{partido['local']} vs {partido['visita']}",
                   partido.get("liga",""),"Analisis IA",m.get("over","N/A"),75,texto[:2500]))
        conn.commit(); conn.close()
    except: pass

# ══════════════════════════════════════════════════════════════
# MODULOS AVANZADOS (CLV, ROI, Sharp)
# ══════════════════════════════════════════════════════════════
def registrar_apuesta_clv(partido_id, partido_nombre, liga, fecha, mercado, momio, monto, bankroll):
    prob=prob_implicita(momio)
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    c.execute("INSERT INTO clv_tracker (partido_id,partido_nombre,liga,fecha_partido,mercado,momio_apostado,prob_apostado,apuesta_monto,bankroll_usado) VALUES(?,?,?,?,?,?,?,?,?)",
              (partido_id,partido_nombre,liga,fecha,mercado,momio,prob,monto,bankroll))
    nid=c.lastrowid; conn.commit(); conn.close()
    return nid

def actualizar_cierre_clv(apuesta_id, momio_cierre, resultado=None, ganancia=None):
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    c.execute("SELECT momio_apostado,prob_apostado FROM clv_tracker WHERE id=?",(apuesta_id,))
    row=c.fetchone()
    if not row: conn.close(); return None
    momio_ap,prob_ap=row; prob_cierre=prob_implicita(momio_cierre)
    clv=(prob_ap-prob_cierre)*100
    c.execute("UPDATE clv_tracker SET momio_cierre=?,prob_cierre=?,clv_pct=?,resultado=?,ganancia_real=?,ts_cierre=? WHERE id=?",
              (momio_cierre,prob_cierre,round(clv,2),resultado,ganancia,datetime.now().isoformat(),apuesta_id))
    conn.commit(); conn.close()
    return round(clv,2)

def generar_reporte_clv():
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    c.execute("SELECT COUNT(*) FROM clv_tracker"); total=c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM clv_tracker WHERE clv_pct IS NOT NULL"); con_cierre=c.fetchone()[0]
    c.execute("SELECT AVG(clv_pct) FROM clv_tracker WHERE clv_pct IS NOT NULL"); clv_prom=c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM clv_tracker WHERE clv_pct>0"); clv_pos=c.fetchone()[0]
    c.execute("SELECT SUM(ganancia_real) FROM clv_tracker WHERE ganancia_real IS NOT NULL"); gan_total=c.fetchone()[0] or 0
    c.execute("SELECT mercado,COUNT(*),AVG(clv_pct),SUM(ganancia_real) FROM clv_tracker WHERE clv_pct IS NOT NULL GROUP BY mercado ORDER BY AVG(clv_pct) DESC"); por_mercado=c.fetchall()
    conn.close()
    return {"total":total,"con_cierre":con_cierre,"clv_promedio":round(clv_prom,2),
            "clv_positivos":clv_pos,"pct_clv_pos":round(clv_pos/con_cierre*100,1) if con_cierre else 0,
            "ganancia_total":round(gan_total,2),"por_mercado":por_mercado,"modelo_ok":clv_prom>0}

def formatear_clv_telegram(r):
    modelo_emoji="OK" if r["modelo_ok"] else "REVISAR"
    texto=(f"<b>CLV TRACKER</b>\nApuestas: {r['total']} | Con cierre: {r['con_cierre']}\n"
           f"CLV promedio: {r['clv_promedio']:+.2f}% ({modelo_emoji})\n"
           f"CLV positivos: {r['clv_positivos']}/{r['con_cierre']} ({r['pct_clv_pos']:.1f}%)\n"
           f"Ganancia total: ${r['ganancia_total']:+.2f}\n\n")
    if r["por_mercado"]:
        texto+="<b>Por mercado:</b>\n"
        for mercado,n,clv_avg,gan in r["por_mercado"][:5]:
            ic="+" if (clv_avg or 0)>0 else "-"
            texto+=f"  {ic} {mercado}: CLV {(clv_avg or 0):+.1f}% | ${(gan or 0):+.2f} ({n})\n"
    return texto

def generar_reporte_roi():
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    c.execute("SELECT COUNT(*),SUM(ganancia),SUM(monto) FROM roi_tracker"); row=c.fetchone()
    total,gan_total,monto_total=row[0] or 0,row[1] or 0,row[2] or 0
    c.execute("SELECT COUNT(*) FROM roi_tracker WHERE resultado='W'"); ganadas=c.fetchone()[0] or 0
    c.execute("SELECT mercado,COUNT(*),COUNT(CASE WHEN resultado='W' THEN 1 END),SUM(ganancia),SUM(monto) FROM roi_tracker GROUP BY mercado ORDER BY SUM(ganancia) DESC"); por_mercado=c.fetchall()
    c.execute("SELECT liga,COUNT(*),SUM(ganancia),SUM(monto) FROM roi_tracker WHERE liga!='' GROUP BY liga ORDER BY SUM(ganancia) DESC"); por_liga=c.fetchall()
    c.execute("SELECT resultado FROM roi_tracker ORDER BY ts DESC LIMIT 20"); results=[r[0] for r in c.fetchall()]
    conn.close()
    racha=0
    for r in results:
        if r=="W": racha+=1
        else: break
    roi_pct=(gan_total/monto_total*100) if monto_total>0 else 0
    pct_acierto=(ganadas/total*100) if total>0 else 0
    return {"total":total,"ganadas":ganadas,"pct_acierto":round(pct_acierto,1),
            "monto_total":round(monto_total,2),"ganancia_total":round(gan_total,2),
            "roi_pct":round(roi_pct,2),"por_mercado":por_mercado,"por_liga":por_liga,"racha_actual":racha}

def formatear_roi_telegram(r):
    roi_color="+" if r["roi_pct"]>0 else "-"
    texto=(f"<b>ROI TRACKER</b>\nTotal: {r['total']} | Ganadas: {r['ganadas']} ({r['pct_acierto']:.1f}%)\n"
           f"Invertido: ${r['monto_total']:.2f} | P&L: ${r['ganancia_total']:+.2f}\n"
           f"ROI: {roi_color}{abs(r['roi_pct']):.2f}%\n")
    if r["racha_actual"]>1: texto+=f"Racha: {r['racha_actual']} victorias\n"
    if r["por_mercado"]:
        texto+="\n<b>Por mercado:</b>\n"
        for mercado,n,wins,gan,inv in r["por_mercado"][:5]:
            roi_m=(gan/inv*100) if inv else 0
            pct_w=(wins/n*100) if n else 0
            texto+=f"  {mercado}: ROI {roi_m:+.1f}% | {wins}/{n} ({pct_w:.0f}%) | ${(gan or 0):+.2f}\n"
    return texto

_historial_momios={}
def registrar_snapshot_momios(partido_id, partido_nombre, momios_dict):
    ts=time.time()
    if partido_id not in _historial_momios: _historial_momios[partido_id]=[]
    _historial_momios[partido_id].append({"ts":ts,"hora":datetime.now().strftime("%H:%M"),"momios":dict(momios_dict)})
    if len(_historial_momios[partido_id])>20: _historial_momios[partido_id].pop(0)
    historial=_historial_momios[partido_id]
    if len(historial)<2: return None
    snap_old=historial[-2]; snap_new=historial[-1]
    tiempo_min=(snap_new["ts"]-snap_old["ts"])/60
    alertas=[]
    for campo in ["local","empate","visita"]:
        ml_old=snap_old["momios"].get(campo); ml_new=snap_new["momios"].get(campo)
        if not ml_old or not ml_new: continue
        try:
            diff_pts=float(str(ml_new).replace("+",""))-float(str(ml_old).replace("+",""))
            diff_prob=(prob_implicita(ml_new)-prob_implicita(ml_old))*100
            nivel=None
            if abs(diff_pts)>=20: nivel="FUERTE"
            elif abs(diff_pts)>=10: nivel="MODERADO"
            elif abs(diff_pts)>=5 and tiempo_min<10: nivel="RAPIDO"
            if nivel:
                alertas.append({"campo":campo,"ml_old":ml_old,"ml_new":ml_new,
                                 "diff_pts":round(diff_pts,1),"diff_prob":round(diff_prob,1),
                                 "tiempo_min":round(tiempo_min,1),"nivel":nivel})
        except: continue
    if not alertas: return None
    return {"partido_id":partido_id,"partido":partido_nombre,"hora":snap_new["hora"],
            "alertas":alertas,"n_alertas":len(alertas),
            "es_sharp":len(alertas)>=2 or any(a["nivel"]=="FUERTE" for a in alertas)}

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
            json={"chat_id":cid,"text":texto,"parse_mode":"HTML","disable_web_page_preview":True},timeout=10)
        return r.json().get("ok",False)
    except Exception as e: log.error(f"Telegram: {e}"); return False

def enviar_largo(texto, chat_id=None):
    if len(texto)<=3900: enviar_telegram(texto,chat_id); return
    partes=[]; actual=""
    for linea in texto.split("\n"):
        if len(actual)+len(linea)>3700: partes.append(actual); actual=""
        actual+=linea+"\n"
    if actual: partes.append(actual)
    for i,p in enumerate(partes,1):
        time.sleep(0.5); enviar_telegram(p+f"\n({i}/{len(partes)})",chat_id)

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
# COMANDOS TELEGRAM
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start","menu","hoy"])
def cmd_menu(message):
    if not verificar_chat(message): return
    markup=types.InlineKeyboardMarkup(row_width=1)
    partidos=todos_los_partidos()[:8]
    texto="<b>SINDICATOBOT v4.4 - AGENDA 24H</b>\n\n"
    for p in partidos:
        nombre=f"{p['local']} vs {p['visita']}"
        emoji="B" if p.get("deporte")=="beisbol" else "F"
        texto+=f"[{emoji}] <b>{nombre}</b>\n   {p.get('fecha_label','')} {p.get('hora_cst','TBD')} CST | {p.get('liga','')}\n\n"
        markup.add(types.InlineKeyboardButton(f"{nombre}",callback_data=f"p_{p['id']}"))
    markup.add(types.InlineKeyboardButton("Todas las ligas",callback_data="todas_ligas"))
    markup.add(types.InlineKeyboardButton("Mundial 2026",callback_data="mundial_menu"))
    markup.add(types.InlineKeyboardButton("Partidos MLB",callback_data="mlb_menu"))
    markup.add(types.InlineKeyboardButton("Reporte ROI",callback_data="roi"))
    bot.send_message(message.chat.id,texto,parse_mode="HTML",reply_markup=markup)

@bot.message_handler(commands=["agenda"])
def cmd_agenda(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,"Cargando agenda 24h...")
    partidos=todos_los_partidos()
    texto="<b>AGENDA 24H</b>\n\n"
    por_liga={}
    for p in partidos: por_liga.setdefault(p.get("liga","Otros"),[]).append(p)
    for liga,ps in list(por_liga.items())[:10]:
        texto+=f"<b>{liga}</b>\n"
        for p in ps[:3]:
            emoji="B" if p.get("deporte")=="beisbol" else "F"
            texto+=f"  [{emoji}] {p['local']} vs {p['visita']} - {p.get('hora_cst','TBD')}\n"
        texto+="\n"
    enviar_largo(texto)

@bot.message_handler(commands=["analisis"])
def cmd_analisis(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else todos_los_partidos()[0]
    if not partido: bot.send_message(message.chat.id,f"No encontre: {termino}"); return
    bot.send_message(message.chat.id,f"Analizando <b>{partido['local']} vs {partido['visita']}</b>... (20-30s)","HTML")
    momios=obtener_momios(partido)
    enviar_largo(generar_analisis_ia(partido,momios,tipo="completo"))

@bot.message_handler(commands=["mlb","beisbol"])
def cmd_beisbol(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else PARTIDOS_MLB_PRUEBA[0]
    if not partido or partido.get("deporte")!="beisbol": partido=PARTIDOS_MLB_PRUEBA[0]
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    rpg_l=sl.get("rpg",4.5); rpg_v=sv.get("rpg",4.3)
    probs=calcular_probabilidades_mlb(rpg_l,rpg_v)
    m=partido.get("momios_static",{})
    nombre=f"{partido['local']} vs {partido['visita']}"
    texto=(f"<b>MLB ANALISIS - {nombre}</b>\n{partido.get('estadio','')} | {partido.get('hora_cst','')} CST\n\n"
           f"<b>Modelo Poisson (carreras):</b>\n"
           f"  {partido['local']}: {rpg_l} R/J\n  {partido['visita']}: {rpg_v} R/J\n"
           f"  Total esperado: {probs['total_esperado']} | Linea O/U: {m.get('ou','8.5')}\n\n"
           f"<b>Probabilidades:</b>\n"
           f"  Local gana: <b>{probs['local']*100:.1f}%</b>\n"
           f"  Visita gana: <b>{probs['visita']*100:.1f}%</b>\n"
           f"  Over {m.get('ou','8.5')}: <b>{probs['over85']*100:.1f}%</b>\n"
           f"  Run Line Local -1.5: <b>{probs['rl_local']*100:.1f}%</b>\n\n"
           f"<b>Momios:</b>\n"
           f"  ML: {partido['local']} {m.get('local','N/A')} / {partido['visita']} {m.get('visita','N/A')}\n"
           f"  O/U {m.get('ou','8.5')}: Over {m.get('over','N/A')} / Under {m.get('under','N/A')}\n"
           f"  Run Line: {m.get('rl_local','N/A')} / {m.get('rl_visita','N/A')}\n\n"
           f"<b>Top marcadores:</b>\n")
    for score,pct in probs["top_scores"][:3]: texto+=f"  {score}: {pct:.1f}%\n"
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["valor"])
def cmd_valor(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else todos_los_partidos()[0]
    if not partido: bot.send_message(message.chat.id,f"No encontre: {termino}"); return
    nombre=f"{partido['local']} vs {partido['visita']}"
    bot.send_message(message.chat.id,f"Calculando valor en <b>{nombre}</b>...","HTML")
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    m=partido.get("momios_static",{})
    momios_dict={"local":m.get("local","+200"),"empate":m.get("empate","+280"),
                 "visita":m.get("visita","+300"),"over25":m.get("over","-110"),
                 "under25":m.get("under","-110"),"btts_si":m.get("btts","+100")}
    resultado=analizar_partido_completo(partido["local"],partido["visita"],
               sl.get("xg",1.5),sv.get("xg",1.4),momios_dict,bankroll=1000)
    pr=resultado["probabilidades"]
    texto=(f"<b>APUESTAS CON VALOR - {nombre}</b>\n"
           f"xG: {sl.get('xg',1.5)} + {sv.get('xg',1.4)} = {sl.get('xg',1.5)+sv.get('xg',1.4):.2f}\n"
           f"Marcador probable: {resultado['marcador_probable'][0]}\n"
           f"Probs: L {pr['local']*100:.1f}% | E {pr['empate']*100:.1f}% | V {pr['visita']*100:.1f}%\n\n")
    aprobadas=[a for a in resultado.get("apuestas_valor",[]) if a["edge"]>=5]
    if aprobadas:
        texto+=f"<b>{len(aprobadas)} apuesta(s) con edge >= 5%</b>\n\n"
        for ap in aprobadas[:3]:
            emoji="ALTA" if ap["edge"]>10 else "MEDIA" if ap["edge"]>6 else "MARGINAL"
            texto+=(f"[{emoji}] <b>{ap['label']}</b>\n"
                   f"  Momio: {ap['momio']} | Prob: {ap['prob_sis']}% | Edge: {ap['edge']:+.1f}%\n"
                   f"  EV/$100: ${ap['ev']:+.2f} | Kelly {ap['kelly_pct']:.1f}% -> ${ap.get('apuesta',0):.2f}\n\n")
        texto+="Usa /apostar para registrar."
    else:
        texto+=f"Sin apuestas con edge >= 5%.\nAnalizadas: {len(resultado.get('apuestas_valor',[]))} mercados."
    enviar_largo(texto)

@bot.message_handler(commands=["momios"])
def cmd_momios(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    partido=buscar_partido(termino) if termino else todos_los_partidos()[0]
    if not partido: bot.send_message(message.chat.id,"No encontre ese partido."); return
    bot.send_message(message.chat.id,"Consultando momios...")
    momios=obtener_momios(partido,forzar=True)
    bot.send_message(message.chat.id,fmt_momios(partido,momios),parse_mode="HTML")

@bot.message_handler(commands=["mundial"])
def cmd_mundial(message):
    if not verificar_chat(message): return
    partes=message.text.split(" ",1)
    termino=partes[1].strip() if len(partes)>1 else ""
    if not termino:
        markup=types.InlineKeyboardMarkup(row_width=4)
        botones=[types.InlineKeyboardButton(f"G.{g}",callback_data=f"mundial_grupo_{g}") for g in "ABCDEFGHIJKL"]
        for i in range(0,len(botones),4): markup.add(*botones[i:i+4])
        markup.add(types.InlineKeyboardButton("Mexico",callback_data="mundial_equipo_mexico"))
        markup.add(types.InlineKeyboardButton("Inaugurales 11-13 Jun",callback_data="mundial_fecha_inicio"))
        bot.send_message(message.chat.id,
            "<b>MUNDIAL 2026 - FIXTURES</b>\n11 Jun - 19 Jul | 48 selecciones | 104 partidos",
            parse_mode="HTML",reply_markup=markup); return
    fixtures=get_fixture_por_equipo(termino)
    if fixtures:
        texto=f"<b>MUNDIAL 2026 - {termino.upper()}</b>\n"
        for p in fixtures:
            alt=f" Alt:{p['altitud']}m" if p["altitud"]>1800 else ""
            pm=p["momios"]
            texto+=(f"\n<b>{p['local']} vs {p['visita']}</b>\n"
                   f"  Grupo {p['grupo']} - {p['fase']} | {p['fecha']} {p['hora_cst']} CST{alt}\n"
                   f"  {p['estadio']}\n  {pm['local']} / {pm['empate']} / {pm['visita']}\n")
        texto+="\n\nUsa /analisis [equipo] para analisis IA."
        enviar_largo(texto); return
    if termino.upper() in "ABCDEFGHIJKL" and len(termino)==1:
        enviar_largo(formatear_fixtures_grupo_telegram(termino.upper())); return
    bot.send_message(message.chat.id,f"No encontre: {termino}")

@bot.message_handler(commands=["ev"])
def cmd_ev(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3: bot.send_message(message.chat.id,"Uso: /ev [momio] [prob%]  Ej: /ev -130 55"); return
    try:
        ml=partes[1]; prob=float(partes[2])
        ev_data=calcular_ev(prob/100,ml,0.04)
        texto=(f"<b>CALCULADORA EV</b>\nMomio: {ml} ({ev_data['decimal']}x)\n"
               f"Prob casino: {ev_data['prob_casino']}% | Prob sistema: {prob}%\n"
               f"Edge: {ev_data['edge_pct']:+.1f}%\nEV/$100: ${ev_data['ev_por_100']:+.2f}\n"
               +("CON VALOR" if ev_data["ev_por_100"]>0 else "SIN VALOR"))
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e: bot.send_message(message.chat.id,f"Error: {e}")

@bot.message_handler(commands=["calcparlay"])
def cmd_calcparlay(message):
    if not verificar_chat(message): return
    partes=message.text.split()[1:]
    if not partes: bot.send_message(message.chat.id,"Uso: /calcparlay -130 +280 -115"); return
    try:
        dec_total=1.0
        for ml in partes: dec_total*=american_to_decimal(ml)
        prob=1.0
        for ml in partes: prob*=prob_implicita(ml)
        mlP=decimal_to_american(dec_total); ev=(prob*(dec_total-1)-(1-prob))*100
        texto=(f"<b>PARLAY - {len(partes)} piernas</b>\nMomios: {' | '.join(partes)}\n"
               f"Combinado: {mlP} ({round(dec_total,3)}x) | Prob: {round(prob*100,1)}%\n"
               f"$100 -> ${100*dec_total:.2f} | EV/$100: ${ev:+.2f}")
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e: bot.send_message(message.chat.id,f"Error: {e}")

@bot.message_handler(commands=["apostar"])
def cmd_apostar(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<5:
        bot.send_message(message.chat.id,
            "Formato: <code>/apostar [equipo] [mercado] [momio] [monto]</code>\n"
            "Ej: <code>/apostar Mexico over25 -110 150</code>","HTML"); return
    equipo=partes[1]; mercado=partes[2]; momio=partes[3]
    try: monto=float(partes[4])
    except: bot.send_message(message.chat.id,"Monto invalido."); return
    partido=buscar_partido(equipo)
    if not partido: bot.send_message(message.chat.id,f"No encontre: {equipo}"); return
    nombre=f"{partido['local']} vs {partido['visita']}"
    aid=registrar_apuesta_clv(partido["id"],nombre,partido.get("liga",""),partido.get("fecha",""),mercado,momio,monto,1000)
    dec=american_to_decimal(momio)
    texto=(f"<b>APUESTA REGISTRADA</b>\n{nombre}\n{mercado} | {momio} ({dec}x)\n"
           f"${monto:.2f} -> Pago potencial: ${monto*dec:.2f}\n\n"
           f"ID: <code>{aid}</code>\nCierre: <code>/cierre {aid} [momio_cierre] W/L/P</code>")
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["cierre"])
def cmd_cierre(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3:
        bot.send_message(message.chat.id,"Formato: <code>/cierre [id] [momio_cierre] [W/L/P]</code>","HTML"); return
    try: aid=int(partes[1])
    except: bot.send_message(message.chat.id,"ID invalido."); return
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
    if clv is None: bot.send_message(message.chat.id,f"No encontre ID {aid}."); return
    res_txt={"W":"GANADA","L":"PERDIDA","P":"PUSH"}.get(res,"Pendiente")
    texto=(f"<b>CLV ACTUALIZADO</b>\nID: {aid} | Cierre: {momio_cierre}\n"
           f"CLV: {clv:+.2f}% | {res_txt}\n"
           +(f"Ganancia: ${ganancia:+.2f}\n" if ganancia is not None else "")
           +("CLV positivo: apostaste mejor que el cierre." if clv>0 else "CLV negativo: el mercado cerro mejor."))
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
    if not rows: bot.send_message(message.chat.id,"Sin movimientos detectados."); return
    texto="<b>MOVIMIENTOS DETECTADOS</b>\n"
    for _,partido,mensaje,ts in rows: texto+=f"\n{ts[:16]}\n{partido}\n{mensaje[:120]}\n"
    enviar_largo(texto)

@bot.message_handler(commands=["resultado"])
def cmd_resultado(message):
    if not verificar_chat(message): return
    partes=message.text.split()
    if len(partes)<3: bot.send_message(message.chat.id,"Formato: <code>/resultado [Local] [X-X] [Visita]</code>","HTML"); return
    mi=next((i for i,p in enumerate(partes) if "-" in p and p.replace("-","").isdigit()),None)
    if mi is None: bot.send_message(message.chat.id,"No encontre el marcador."); return
    local=" ".join(partes[1:mi]); visita=" ".join(partes[mi+1:])
    try: gl,gv=[int(x) for x in partes[mi].split("-")]
    except: bot.send_message(message.chat.id,"Marcador invalido."); return
    total=gl+gv
    p_enc=buscar_partido(local) or buscar_partido(visita)
    pid=p_enc["id"] if p_enc else f"manual_{local[:5]}_{visita[:5]}"
    liga=p_enc.get("liga","Manual") if p_enc else "Manual"
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    res="L" if gl>gv else "V" if gv>gl else "E"
    c.execute("INSERT OR REPLACE INTO resultados (partido_id,partido_nombre,liga,fecha,goles_local,goles_visita,total_goles,resultado,over_25,btts) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (pid,f"{local} vs {visita}",liga,datetime.now().strftime("%Y-%m-%d"),gl,gv,total,res,1 if total>2.5 else 0,1 if gl>0 and gv>0 else 0))
    # Actualizar predicciones contra resultado real
    c.execute("UPDATE predicciones SET resultado_real=?,ts_resultado=? WHERE partido_id=? AND fue_correcto IS NULL",
              (f"{gl}-{gv}",datetime.now().isoformat(),pid))
    conn.commit(); conn.close()
    # Ajuste automatico xG
    if p_enc:
        sl=p_enc.get("stats_local",{}); sv=p_enc.get("stats_visita",{})
        xg_l_pred=sl.get("xg",sl.get("rpg",0)); xg_v_pred=sv.get("xg",sv.get("rpg",0))
        if xg_l_pred>0: ajustar_xg_post_resultado(local,visita,xg_l_pred,xg_v_pred,gl,gv)
    texto=(f"<b>RESULTADO REGISTRADO</b>\n{local} {gl} - {gv} {visita}\n"
           f"{'Local gana' if gl>gv else 'Visitante gana' if gv>gl else 'Empate'} | {total} goles | "
           f"{'Over' if total>2.5 else 'Under'} 2.5 | BTTS: {'Si' if gl>0 and gv>0 else 'No'}\n\n"
           f"xG ajustado para proximos analisis.\nUsa /reporte para ver rendimiento.")
    bot.send_message(message.chat.id,texto,parse_mode="HTML")

@bot.message_handler(commands=["reporte","historial"])
def cmd_reporte(message):
    if not verificar_chat(message): return
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()
        c.execute("SELECT COUNT(*) FROM predicciones"); total=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM predicciones WHERE fue_correcto IS NOT NULL"); res=c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM predicciones WHERE fue_correcto=1"); ok=c.fetchone()[0]
        c.execute("SELECT partido_nombre,mercado,fue_correcto,ts FROM predicciones ORDER BY ts DESC LIMIT 8")
        preds=c.fetchall(); conn.close()
        prec=round(ok/res*100,1) if res>0 else 0
        color="BIEN" if prec>=60 else "REGULAR" if prec>=50 else "MAL"
        texto=(f"<b>REPORTE IA</b>\nTotal: {total} | Resueltas: {res} | Correctas: {ok}\n"
               f"Precision: <b>{prec}%</b> ({color})\n\n<b>Ultimas:</b>\n")
        for nombre,merc,correcto,ts in preds:
            ic="OK" if correcto==1 else "FAIL" if correcto==0 else "..."
            texto+=f"[{ic}] {nombre[:28]} | {merc or 'N/A'} | {ts[:10]}\n"
        bot.send_message(message.chat.id,texto,parse_mode="HTML")
    except Exception as e: bot.send_message(message.chat.id,f"Error: {e}")

@bot.message_handler(commands=["tabla"])
def cmd_tabla(message):
    if not verificar_chat(message): return
    grupos={
        "A":["Mexico","Sudafrica","Corea del Sur","Rep. Checa"],
        "B":["Canada","Bosnia-H","Suiza","Angola"],
        "C":["Brasil","Marruecos","Escocia","Haiti"],
        "D":["USA","Alemania","Ecuador","Costa Marfil"],
        "E":["Paises Bajos","Suecia","Turquia","Zambia"],
        "F":["Francia","Belgica","Rumania","Irak"],
        "G":["Italia","Portugal","Colombia","R.D.Congo"],
        "H":["Espana","Uruguay","Arabia S.","Cabo Verde"],
        "I":["Francia","Australia","Dinamarca","Tunez"],
        "J":["Argentina","Argelia","Austria","Jordania"],
        "K":["Portugal","Polonia","Ucrania","Serbia"],
        "L":["Inglaterra","Croacia","Panama","Ghana"],
    }
    bot.send_message(message.chat.id,"<b>MUNDIAL 2026 - GRUPOS</b>\n(Top 2 clasifican)","HTML")
    letras=list(grupos.keys())
    for i in range(0,len(letras),3):
        bloque=""
        for letra in letras[i:i+3]:
            bloque+=f"\n<b>GRUPO {letra}</b>\n"
            for j,n in enumerate(grupos[letra],1):
                ic="[C]" if j<=2 else "[ ]"
                bloque+=f"<code>{ic} {j}. {n:<16} 0J 0pts</code>\n"
        time.sleep(0.4); bot.send_message(message.chat.id,bloque,parse_mode="HTML")

@bot.message_handler(func=lambda m: True)
def cmd_ayuda(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,
        "<b>SINDICATOBOT v4.4 - COMANDOS</b>\n\n"
        "<b>PARTIDOS (24H AUTO)</b>\n/hoy /agenda /analisis [equipo] /momios [equipo]\n\n"
        "<b>ANALISIS ESTADISTICO</b>\n/valor [equipo] - edge >= 5%\n/ev [momio] [prob%]\n/calcparlay [momios]\n\n"
        "<b>MLB BEISBOL</b>\n/mlb - partidos del dia\n/beisbol [equipo] - Poisson carreras\n\n"
        "<b>MUNDIAL 2026</b>\n/mundial - menu completo\n/mundial mexico | /mundial A | /tabla\n\n"
        "<b>TRACKING</b>\n/roi /clv /sharp\n/apostar [equipo] [mercado] [momio] [monto]\n"
        "/cierre [id] [momio_cierre] [W/L/P]\n/resultado [Local X-X Visita]\n/reporte\n\n"
        "<i>Alertas auto: 2h, 1h y 30min antes del partido.</i>","HTML")

# ── CALLBACKS ─────────────────────────────────────────────────
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
        deporte=partido.get("deporte","futbol")
        if deporte=="beisbol":
            markup.add(
                types.InlineKeyboardButton("Analisis MLB",callback_data=f"mlb_{pid}"),
                types.InlineKeyboardButton("Momios",      callback_data=f"momios_{pid}"),
                types.InlineKeyboardButton("Menu",        callback_data="menu"))
        else:
            markup.add(
                types.InlineKeyboardButton("Analisis IA",    callback_data=f"ia_{pid}"),
                types.InlineKeyboardButton("Momios",          callback_data=f"momios_{pid}"),
                types.InlineKeyboardButton("Valor (Poisson)", callback_data=f"valor_{pid}"),
                types.InlineKeyboardButton("Menu",            callback_data="menu"))
        m=partido.get("momios_static",{})
        alt=f"\nAltitud: {partido.get('altitud',0)}m" if partido.get("altitud",0)>1800 else ""
        bot.edit_message_text(
            f"<b>{nombre}</b>\n{partido.get('liga','')} - {partido.get('fase','')}\n"
            f"{partido.get('estadio','')} | {partido.get('hora_cst','TBD')} CST{alt}\n\n"
            f"{partido.get('contexto','')}\n\n"
            f"Momios: {m.get('local','N/A')} / {m.get('empate','N/A')} / {m.get('visita','N/A')}",
            call.message.chat.id,call.message.message_id,parse_mode="HTML",reply_markup=markup)
    elif data.startswith("ia_"):
        pid=data.replace("ia_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: return
        bot.answer_callback_query(call.id,"Generando analisis IA...")
        bot.send_message(call.message.chat.id,f"Analizando <b>{partido['local']} vs {partido['visita']}</b>...","HTML")
        enviar_largo(generar_analisis_ia(partido,obtener_momios(partido),"completo"))
    elif data.startswith("mlb_"):
        pid=data.replace("mlb_","")
        partido=next((p for p in todos_los_partidos() if p.get("id")==pid),None)
        if not partido: return
        bot.answer_callback_query(call.id,"Analisis MLB...")
        class FakeMsg:
            text=f"/beisbol {partido['local']}"
            class chat: id=TELEGRAM_CHAT_ID
        cmd_beisbol(FakeMsg())
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
            class chat: id=TELEGRAM_CHAT_ID
        cmd_valor(FakeMsg())
    elif data=="todas_ligas":
        bot.answer_callback_query(call.id,"Cargando agenda...")
        class FakeMsg:
            text="/agenda"
            class chat: id=TELEGRAM_CHAT_ID
        cmd_agenda(FakeMsg())
    elif data=="mundial_menu":
        bot.answer_callback_query(call.id,"Mundial 2026")
        class FakeMsg:
            text="/mundial"
            class chat: id=TELEGRAM_CHAT_ID
        cmd_mundial(FakeMsg())
    elif data.startswith("mundial_grupo_"):
        grupo=data.replace("mundial_grupo_","")
        bot.answer_callback_query(call.id,f"Grupo {grupo}")
        bot.send_message(call.message.chat.id,formatear_fixtures_grupo_telegram(grupo),parse_mode="HTML")
    elif data=="mundial_equipo_mexico":
        bot.answer_callback_query(call.id,"Mexico")
        fixtures=get_fixture_por_equipo("Mexico")
        texto="<b>MEXICO - MUNDIAL 2026</b>\n"
        for p in fixtures:
            alt=f" Alt:{p['altitud']}m" if p["altitud"]>1800 else ""
            texto+=f"\n{p['local']} vs {p['visita']}\n  {p['fecha']} {p['hora_cst']} {p['estadio']}{alt}\n  {p['momios']['local']} / {p['momios']['empate']} / {p['momios']['visita']}\n"
        bot.send_message(call.message.chat.id,texto,parse_mode="HTML")
    elif data=="mundial_fecha_inicio":
        bot.answer_callback_query(call.id,"Inaugurales")
        texto="<b>INAUGURAL 11-13 Jun 2026</b>\n"
        for fecha in ["2026-06-11","2026-06-12","2026-06-13"]:
            ps=[f for f in MUNDIAL_2026_FIXTURES if f["fecha"]==fecha]
            if ps:
                texto+=f"\n<b>{fecha}</b>\n"
                for p in ps: texto+=f"  {p['local']} vs {p['visita']} ({p['hora_cst']})\n"
        bot.send_message(call.message.chat.id,texto,parse_mode="HTML")
    elif data=="mlb_menu":
        bot.answer_callback_query(call.id,"MLB")
        markup=types.InlineKeyboardMarkup(row_width=1)
        texto="<b>MLB - PARTIDOS HOY</b>\n\n"
        for p in PARTIDOS_MLB_PRUEBA:
            nombre=f"{p['local']} vs {p['visita']}"
            texto+=f"{nombre}\nHoy {p.get('hora_cst','TBD')} CST\n\n"
            markup.add(types.InlineKeyboardButton(nombre,callback_data=f"p_{p['id']}"))
        bot.send_message(call.message.chat.id,texto,parse_mode="HTML",reply_markup=markup)
    elif data=="roi":
        bot.answer_callback_query(call.id,"ROI")
        bot.send_message(call.message.chat.id,formatear_roi_telegram(generar_reporte_roi()),parse_mode="HTML")
    elif data=="menu":
        cmd_menu(call.message)

# ══════════════════════════════════════════════════════════════
# ALERTAS AUTOMATICAS
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
    texto=(f"<b>ALERTA - 2 HORAS PARA EL INICIO</b>\n{nombre}\n"
           f"{partido.get('liga','')} - {partido.get('fase','')}\n"
           f"{partido.get('estadio','')} | {partido.get('hora_cst','')} CST\n\n"
           f"{partido.get('contexto','')}\n\n"
           f"Momios: {partido['local']} {_ml('local')} / Empate {_ml('empate')} / {partido['visita']} {_ml('visita')}\n"
           f"O/U {m.get('ou','2.5')}: Over {m.get('over','N/A')} / Under {m.get('under','N/A')}\n")
    if partido.get("altitud",0)>1800: texto+=f"\nALTITUD {partido['altitud']}m - Factor critico\n"
    texto+=(f"\nArbitro: {arb.get('nombre','N/D')} ({arb.get('amarillas',4.2)} am/p)\n\n"
            f"<b>Analisis IA:</b>\n{analisis[:600]}\n\n/analisis {partido['local']} para informe completo")
    if enviar_telegram(texto): _log_alerta("pre2h",nombre,texto[:200])

def alerta_1h(partido):
    nombre=f"{partido['local']} vs {partido['visita']}"
    if _ya_enviada("analisis",partido["id"],20): return
    momios=obtener_momios(partido)
    texto_ia=generar_analisis_ia(partido,momios,tipo="completo")
    header=f"<b>ANALISIS IA COMPLETO - SINDICATO</b>\n{nombre}\n{partido.get('liga','')} | {partido.get('hora_cst','')} CST\n\n"
    enviar_largo(header+texto_ia)
    _log_alerta("analisis",nombre,"Analisis completo")

def alerta_30min(partido):
    nombre=f"{partido['local']} vs {partido['visita']}"
    if _ya_enviada("pre30min",partido["id"]): return
    momios=obtener_momios(partido,forzar=True); m=partido.get("momios_static",{})
    def _ml(side):
        if momios and momios.get("books") and momios["books"][0].get("h2h",{}).get(side):
            return decimal_to_american(momios["books"][0]["h2h"][side])
        return m.get(side,"N/A")
    texto=(f"<b>30 MINUTOS PARA EL INICIO</b>\n{nombre}\n{partido.get('liga','')} | {partido.get('hora_cst','')} CST\n\n"
           f"Momios finales: {partido['local']} <b>{_ml('local')}</b> / {partido['visita']} <b>{_ml('visita')}</b>\n")
    if partido.get("bajas"): texto+=f"Bajas: {' | '.join(partido['bajas'])}\n"
    texto+="\nEl Sindicato esta listo."
    if enviar_telegram(texto): _log_alerta("pre30min",nombre,texto[:200])

# ══════════════════════════════════════════════════════════════
# MONITOR AUTOMATICO
# ══════════════════════════════════════════════════════════════

def alerta_resultado_mlb():
    mlb = get_mlb_hoy()
    for p in mlb:
        if p.get("fase") in ("Final","F","Game Over") and p.get("score"):
            nombre = f"{p['local']} vs {p['visita']}"
            if _ya_enviada("resultado_mlb", p["id"], 24): continue
            texto = (f"<b>RESULTADO MLB</b>\n{nombre}\n"
                     f"Marcador final: <b>{p['score']}</b>\n"
                     f"{p.get('estadio','')} | {p.get('hora_cst','')} CST\n\n"
                     f"Usa /resultado para registrar en el tracker.")
            if enviar_telegram(texto): _log_alerta("resultado_mlb", nombre, texto[:200])

def monitor_alertas():
    log.info("Monitor de alertas iniciado")
    while True:
        try:
            ahora=datetime.now()
            for p in todos_los_partidos():
                try:
                    hora_cst=p.get("hora_cst","00:00")
                    if hora_cst in ("TBD","Por confirmar","","N/A"): continue
                    dt=datetime.strptime(f"{p['fecha']} {hora_cst}","%Y-%m-%d %H:%M")
                    mins=(dt-ahora).total_seconds()/60
                    if 115<=mins<=125: alerta_2h(p)
                    elif 55<=mins<=65: alerta_1h(p)
                    elif 28<=mins<=33: alerta_30min(p)
                    elif 0<mins<360 and ODDS_API_KEY:
                        datos=obtener_momios(p)
                        if datos and datos.get("books"):
                            h=datos["books"][0].get("h2h",{})
                            snap={}
                            if h.get("local"):  snap["local"] =decimal_to_american(h["local"])
                            if h.get("empate"): snap["empate"]=decimal_to_american(h["empate"])
                            if h.get("visita"): snap["visita"]=decimal_to_american(h["visita"])
                            if snap: registrar_snapshot_momios(p["id"],f"{p['local']} vs {p['visita']}",snap)
                except: pass
        except Exception as e: log.error(f"Monitor: {e}")
        # Notificar resultados MLB terminados
        alerta_resultado_mlb()
        time.sleep(300)

# ══════════════════════════════════════════════════════════════
# FLASK — CORS HABILITADO + DASHBOARD
# ══════════════════════════════════════════════════════════════
app=Flask(__name__,static_folder=".",static_url_path="")
CORS(app)

@app.route("/")
def index():
    try: return send_from_directory(".","sindicatobot_dashboard.html")
    except:
        return ("<h1 style='font-family:monospace;color:#00FF87;background:#050A07;padding:30px'>"
                "SindicatoBot v4.4 Online</h1>")

@app.route("/health")
def health():
    return jsonify({"status":"online","time":datetime.now().isoformat(),
                    "partidos":len(todos_los_partidos()),"version":"4.4"})

@app.route("/api/agenda")
def api_agenda():
    return jsonify([{"id":p.get("id",""),"nombre":f"{p['local']} vs {p['visita']}",
                     "hora":p.get("hora_cst",""),"liga":p.get("liga",""),
                     "fase":p.get("fase",""),"fecha":p.get("fecha",""),
                     "deporte":p.get("deporte","futbol"),"estadio":p.get("estadio","")}
                    for p in todos_los_partidos()[:30]])

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
        c.execute("SELECT tipo,partido_nombre,enviada,ts FROM alertas_log ORDER BY ts DESC LIMIT 20")
        rows=c.fetchall(); conn.close()
        return jsonify([{"tipo":r[0],"partido":r[1],"enviada":r[2],"hora":r[3][:16]} for r in rows])
    except: return jsonify([])

@app.route("/api/roi")
def api_roi():
    try: return jsonify(generar_reporte_roi())
    except: return jsonify({})

@app.route("/api/poisson/<path:partido_id>")
def api_poisson(partido_id):
    partido=next((p for p in todos_los_partidos() if p.get("id")==partido_id),None)
    if not partido: return jsonify({"error":"No encontrado"}),404
    sl=partido.get("stats_local",{}); sv=partido.get("stats_visita",{})
    probs=calcular_probabilidades(sl.get("xg",1.5),sv.get("xg",1.4))
    return jsonify(probs)


@app.route("/api/analisis/<path:partido_id>")
def api_analisis(partido_id):
    partido = next((p for p in todos_los_partidos() if p.get("id")==partido_id), None)
    if not partido: return jsonify({"error":"Partido no encontrado"}), 404
    cached = _get_analisis_cache(partido_id)
    if cached:
        return jsonify({"analisis":cached,"cached":True,
                        "partido":f"{partido['local']} vs {partido['visita']}",
                        "deporte":partido.get("deporte","futbol")})
    momios = obtener_momios(partido)
    texto  = generar_analisis_ia(partido, momios, tipo="completo")
    return jsonify({"analisis":texto,"cached":False,
                    "partido":f"{partido['local']} vs {partido['visita']}",
                    "deporte":partido.get("deporte","futbol")})

@app.route("/api/mlb")
def api_mlb():
    """Retorna partidos MLB con probabilidades Poisson."""
    partidos = get_mlb_hoy()
    result = []
    for p in partidos:
        sl=p.get("stats_local",{}); sv=p.get("stats_visita",{})
        rpg_l=sl.get("rpg",4.5); rpg_v=sv.get("rpg",4.3)
        probs=calcular_probabilidades_mlb(rpg_l,rpg_v)
        result.append({
            "id":p.get("id",""),"nombre":f"{p['local']} vs {p['visita']}",
            "local":p.get("local",""),"visita":p.get("visita",""),
            "hora":p.get("hora_cst",""),"estadio":p.get("estadio",""),
            "fase":p.get("fase",""),"score":p.get("score",""),
            "pitcher_local":p.get("pitcher_local","TBD"),
            "pitcher_visita":p.get("pitcher_visita","TBD"),
            "inning":p.get("inning",""),"inning_half":p.get("inning_half",""),
            "rpg_local":rpg_l,"rpg_visita":rpg_v,
            "momios":p.get("momios_static",{}),
            "momios_poisson":p.get("momios_poisson",False),
            "probabilidades":{
                "local":round(probs["local"]*100,1),
                "visita":round(probs["visita"]*100,1),
                "over85":round(probs["over85"]*100,1),
                "over75":round(probs["over75"]*100,1),
                "over95":round(probs["over95"]*100,1),
                "rl_local":round(probs["rl_local"]*100,1),
                "rl_visita":round(probs["rl_visita"]*100,1),
                "total_esperado":probs["total_esperado"],
            }
        })
    return jsonify(result)


@app.route("/api/partido/<path:partido_id>")
def api_partido(partido_id):
    partido = next((p for p in todos_los_partidos() if p.get("id")==partido_id), None)
    if not partido: return jsonify({"error":"No encontrado"}), 404
    sl = partido.get("stats_local",{}); sv = partido.get("stats_visita",{})
    if partido.get("deporte") == "beisbol":
        probs = calcular_probabilidades_mlb(sl.get("rpg",4.5), sv.get("rpg",4.3))
    else:
        xg_l = obtener_xg_ajustado(partido["local"], sl.get("xg",1.5))
        xg_v = obtener_xg_ajustado(partido["visita"], sv.get("xg",1.4))
        probs = calcular_probabilidades(xg_l, xg_v)
    return jsonify({"partido":partido,"probabilidades":probs,"momios":partido.get("momios_static",{})})

@app.route("/api/xg_ajustes")
def api_xg_ajustes():
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT equipo,xg_base,ajuste_acum,partidos_n,ultima_actualizacion FROM xg_ajuste ORDER BY abs(ajuste_acum) DESC LIMIT 20")
        rows = c.fetchall(); conn.close()
        return jsonify([{"equipo":r[0],"xg_base":r[1],"ajuste":r[2],"partidos":r[3],
                         "xg_actual":round(r[1]+r[2],2),"fecha":r[4][:10]} for r in rows])
    except: return jsonify([])


@app.route("/api/debug")
def api_debug():
    """Endpoint de diagnostico para verificar configuracion."""
    return jsonify({
        "version": "4.4",
        "anthropic_key": "OK" if ANTHROPIC_KEY and len(ANTHROPIC_KEY) > 20 else "FALTA o VACIA",
        "anthropic_key_len": len(ANTHROPIC_KEY) if ANTHROPIC_KEY else 0,
        "odds_key": "OK" if ODDS_API_KEY else "FALTA",
        "telegram_token": "OK" if TELEGRAM_TOKEN else "FALTA",
        "telegram_chat_id": "OK" if TELEGRAM_CHAT_ID else "FALTA",
        "openweather": "OK" if OPENWEATHER_KEY else "FALTA",
        "partidos_futbol": len([p for p in todos_los_partidos() if p.get("deporte")!="beisbol"]),
        "partidos_mlb": len(get_mlb_hoy()),
    })

def run_web():
    port=int(os.environ.get("PORT",5000))
    log.info(f"Web en puerto {port}")
    app.run(host="0.0.0.0",port=port,use_reloader=False)

# ══════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════
def run_bot():
    if not TELEGRAM_TOKEN:
        log.warning("Falta TELEGRAM_TOKEN — bot desactivado. Dashboard sigue funcionando.")
        return
    log.info("Esperando 15s..."); time.sleep(15)
    while True:
        try:
            bot.remove_webhook(); time.sleep(2)
            log.info("Bot conectado OK")
            bot.infinity_polling(timeout=30,long_polling_timeout=30,
                                 skip_pending=True,allowed_updates=["message","callback_query"])
        except Exception as e:
            espera=30 if "409" in str(e) else 10
            log.error(f"Bot error: {str(e)[:60]} - retry {espera}s"); time.sleep(espera)

if __name__=="__main__":
    log.info("="*55)
    log.info("  SINDICATOBOT v4.4 - ARRANCANDO")
    log.info(f"  Telegram:  {'OK' if TELEGRAM_TOKEN    else 'FALTA'}")
    log.info(f"  Chat ID:   {'OK' if TELEGRAM_CHAT_ID  else 'FALTA'}")
    log.info(f"  Odds API:  {'OK' if ODDS_API_KEY      else 'sin momios en vivo'}")
    log.info(f"  Claude AI: {'OK' if ANTHROPIC_KEY     else 'analisis basico'}")
    log.info("="*55)
    inicializar_bd()
    Thread(target=monitor_alertas, daemon=True).start()
    if TELEGRAM_TOKEN:
        Thread(target=run_bot, daemon=True).start()
    else:
        log.warning("Sin TELEGRAM_TOKEN - solo dashboard activo")
    # Flask en hilo principal: Railway no mata el proceso
    run_web()
