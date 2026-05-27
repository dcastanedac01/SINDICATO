"""
SINDICATOBOT — Módulos Avanzados v4.2
======================================
1. Filtro de Valor (EV mínimo configurable)
2. CLV Tracker (Closing Line Value)
3. Sharp Money Detector mejorado
4. Fixtures Mundial 2026 completos
5. ROI Tracker por mercado
"""

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
    from poisson_kelly import prob_implicita
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
    from poisson_kelly import prob_implicita
    
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
    
    from poisson_kelly import american_to_decimal, prob_implicita
    
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
    from poisson_kelly import american_to_decimal
    
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
