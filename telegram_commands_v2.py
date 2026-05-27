"""
SINDICATOBOT — Comandos Telegram Avanzados v4.2
================================================
Agrega estos comandos a main.py justo antes del
bloque  if __name__ == "__main__":

Nuevos comandos:
  /valor   — Apuestas con valor del partido (filtro EV ≥5%)
  /clv     — Reporte CLV (Closing Line Value)
  /roi     — ROI real por mercado y liga
  /sharp   — Historial de movimientos de momios
  /mundial [equipo|grupo] — Fixtures del Mundial 2026
  /apostar — Registrar una apuesta manualmente
  /cierre  — Actualizar momio de cierre de una apuesta
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from advanced_modules import (
    # Filtro de valor
    filtrar_apuestas_valor, formatear_filtro_telegram, FILTRO_CONFIG,
    # CLV
    registrar_apuesta_clv, actualizar_cierre_clv,
    generar_reporte_clv, formatear_clv_telegram, inicializar_clv_tabla,
    # Sharp
    registrar_snapshot_momios, formatear_sharp_telegram,
    # Mundial
    MUNDIAL_2026_FIXTURES, get_fixture_por_equipo,
    get_fixtures_grupo, formatear_fixtures_grupo_telegram,
    # ROI
    registrar_resultado_roi, generar_reporte_roi,
    formatear_roi_telegram, inicializar_roi_tabla,
)
from poisson_kelly import (
    analizar_partido_completo, formatear_para_telegram, analisis_rapido_telegram
)

# Inicializar tablas al arrancar
inicializar_clv_tabla()
inicializar_roi_tabla()


# ══════════════════════════════════════════════════════════════
# /valor — Apuestas con valor del partido
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["valor"])
def cmd_valor(message):
    """Muestra apuestas con EV ≥ 5% usando modelo Poisson + filtro."""
    if not verificar_chat(message): return

    partes = message.text.split(" ", 1)
    termino = partes[1].strip() if len(partes) > 1 else ""
    partido = buscar_partido(termino) if termino else (PARTIDOS_HOY[0] if PARTIDOS_HOY else None)

    if not partido:
        bot.send_message(message.chat.id,
            "❌ No encontré ese partido. Usa /hoy para ver los disponibles.")
        return

    nombre = f"{partido['local']} vs {partido['visita']}"
    bot.send_message(message.chat.id,
        f"⏳ Analizando valor en <b>{nombre}</b>...", parse_mode="HTML")

    # Obtener momios en vivo si es posible
    momios_vivo = obtener_momios(partido) if ODDS_API_KEY else None

    # Usar momios vivos o estáticos
    m = partido.get("momios_static", {})
    if momios_vivo and momios_vivo.get("books"):
        bk = momios_vivo["books"][0]
        h = bk.get("h2h", {}); t = bk.get("totals", {})
        if h.get("local"):  m["local"]  = decimal_to_american(h["local"])
        if h.get("empate"): m["empate"] = decimal_to_american(h["empate"])
        if h.get("visita"): m["visita"] = decimal_to_american(h["visita"])
        if t.get("over"):   m["over"]   = decimal_to_american(t["over"])
        if t.get("under"):  m["under"]  = decimal_to_american(t["under"])

    # Análisis Poisson completo
    sl = partido.get("stats_local",  {})
    sv = partido.get("stats_visita", {})
    xg_l = sl.get("xg", 1.5)
    xg_v = sv.get("xg", 1.4)

    momios_dict = {
        "local":   m.get("local",  "+200"),
        "empate":  m.get("empate", "+280"),
        "visita":  m.get("visita", "+300"),
        "over25":  m.get("over",   "-110"),
        "under25": m.get("under",  "-110"),
        "btts_si": m.get("btts",   "+100"),
    }

    resultado = analizar_partido_completo(
        partido["local"], partido["visita"],
        xg_l, xg_v, momios_dict, bankroll=1000
    )

    # Preparar lista para el filtro
    apuestas_para_filtrar = []
    for ap in resultado.get("apuestas_valor", []):
        k = resultado["mercados"].get(ap["key"], {})
        apuestas_para_filtrar.append({
            "mercado":       ap["label"],
            "momio":         ap["momio"],
            "prob_sistema":  ap["prob_sis"],
            "edge_pct":      ap["edge"],
            "ev_por_100":    ap["ev"],
            "kelly_pct":     ap["kelly_pct"],
            "apuesta":       ap["apuesta"],
        })

    aprobadas, rechazadas, resumen = filtrar_apuestas_valor(apuestas_para_filtrar)

    # Agregar apuesta de aposta a cada aprobada
    texto = (f"🎯 <b>APUESTAS CON VALOR — {nombre}</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"xG {xg_l} + {xg_v} = {xg_l+xg_v:.2f} | "
             f"Marcador probable: {resultado['marcador_probable'][0]}\n\n")

    if aprobadas:
        texto += f"✅ <b>{len(aprobadas)} apuesta(s) superan el filtro (edge ≥5%)</b>\n\n"
        for ap in aprobadas[:3]:
            texto += (f"{ap['emoji']} <b>{ap['mercado']}</b>\n"
                     f"   Momio: <b>{ap['momio']}</b> | "
                     f"Prob: {ap['prob_sistema']:.1f}% | "
                     f"Edge: {ap['edge_pct']:+.1f}%\n"
                     f"   EV por $100: <b>${ap['ev_por_100']:+.2f}</b>\n"
                     f"   Kelly: {ap['kelly_pct']:.1f}% → <b>${ap.get('apuesta',0):.2f}</b> de $1,000\n"
                     f"   Convicción: {ap['conviccion']}\n\n")
        texto += (f"━━━━━━━━━━━━━━━━━━\n"
                 f"Usa /apostar para registrar tu apuesta.")
    else:
        texto += (f"⚠️ Sin apuestas con valor suficiente.\n"
                 f"Analizadas: {resumen['total']} | Rechazadas: {resumen['rechazadas']}\n\n"
                 f"Umbral actual: edge ≥{FILTRO_CONFIG['edge_minimo_pct']}% | "
                 f"EV ≥${FILTRO_CONFIG['ev_minimo_100']}")

    enviar_largo(texto)


# ══════════════════════════════════════════════════════════════
# /mundial — Fixtures del Mundial 2026
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["mundial"])
def cmd_mundial(message):
    """Muestra fixtures del Mundial 2026 por equipo o grupo."""
    if not verificar_chat(message): return

    partes = message.text.split(" ", 1)
    termino = partes[1].strip() if len(partes) > 1 else ""

    if not termino:
        # Menú principal
        markup = types.InlineKeyboardMarkup(row_width=3)
        for g in ["A","B","C","D","E","F","G","H","I","J","K","L"]:
            markup.add(types.InlineKeyboardButton(
                f"Grupo {g}", callback_data=f"mundial_grupo_{g}"))
        markup.add(types.InlineKeyboardButton(
            "🇲🇽 Todos los de México", callback_data="mundial_equipo_mexico"))
        markup.add(types.InlineKeyboardButton(
            "📅 Por fecha (11-13 Jun)", callback_data="mundial_fecha_inicio"))

        texto = (f"🌎 <b>MUNDIAL 2026 — FIXTURES</b>\n"
                 f"━━━━━━━━━━━━━━━━━━\n"
                 f"📅 Inicio: 11 jun 2026 | Final: 19 jul 2026\n"
                 f"⚽ 48 selecciones | 104 partidos | 12 grupos\n"
                 f"🏟 16 estadios en USA, México y Canadá\n\n"
                 f"Selecciona un grupo o usa:\n"
                 f"/mundial mexico — Partidos de México\n"
                 f"/mundial A — Grupo A completo")
        bot.send_message(message.chat.id, texto,
                        parse_mode="HTML", reply_markup=markup)
        return

    # Buscar por equipo
    fixtures = get_fixture_por_equipo(termino)
    if fixtures:
        nombre = termino.capitalize()
        texto  = f"🌎 <b>MUNDIAL 2026 — {nombre.upper()}</b>\n━━━━━━━━━━━━━━━━━━\n"
        for p in fixtures:
            alt = f" | ⛰️ {p['altitud']}m" if p["altitud"] > 1800 else ""
            m   = p["momios"]
            texto += (f"\n⚽ <b>{p['local']} vs {p['visita']}</b>\n"
                     f"   🏆 Grupo {p['grupo']} — {p['fase']}\n"
                     f"   📅 {p['fecha']} · {p['hora_cst']} CST\n"
                     f"   🏟 {p['estadio']}{alt}\n"
                     f"   📺 {p['tv']}\n"
                     f"   💰 {m['local']} / {m['empate']} / {m['visita']}\n"
                     f"   ⚽ O/U {m['ou']}: Over {m['over']} / Under {m['under']}\n")
            if p["xg_l"] > 0:
                texto += f"   📊 xG sistema: {p['xg_l']} vs {p['xg_v']}\n"
        enviar_largo(texto)
        return

    # Buscar por grupo (A-L)
    if termino.upper() in "ABCDEFGHIJKL" and len(termino) == 1:
        texto = formatear_fixtures_grupo_telegram(termino.upper())
        enviar_largo(texto)
        return

    bot.send_message(message.chat.id,
        f"❌ No encontré <b>{termino}</b>.\n\n"
        "Usa: /mundial mexico | /mundial A | /mundial argentina",
        parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# /clv — Closing Line Value
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["clv"])
def cmd_clv(message):
    """Reporte del Closing Line Value del modelo."""
    if not verificar_chat(message): return
    reporte = generar_reporte_clv()
    texto   = formatear_clv_telegram(reporte)
    bot.send_message(message.chat.id, texto, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# /roi — ROI por mercado
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["roi"])
def cmd_roi(message):
    """Reporte de ROI real por mercado y liga."""
    if not verificar_chat(message): return
    reporte = generar_reporte_roi()
    texto   = formatear_roi_telegram(reporte)
    bot.send_message(message.chat.id, texto, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# /apostar — Registrar apuesta + CLV
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["apostar"])
def cmd_apostar(message):
    """
    Registra una apuesta manualmente para tracking.
    Formato: /apostar [equipo] [mercado] [momio] [monto]
    Ejemplo: /apostar Pumas "Over 2.5" -110 150
    """
    if not verificar_chat(message): return

    partes = message.text.split()
    if len(partes) < 5:
        bot.send_message(message.chat.id,
            "📝 <b>REGISTRAR APUESTA</b>\n\n"
            "Formato:\n"
            "<code>/apostar [equipo] [mercado] [momio] [monto]</code>\n\n"
            "Ejemplos:\n"
            "<code>/apostar Pumas over25 -110 150</code>\n"
            "<code>/apostar Mexico local -200 100</code>\n"
            "<code>/apostar Espana btts -115 80</code>\n\n"
            "Mercados: local | empate | visita | over25 | under25 | btts | gol1t",
            parse_mode="HTML")
        return

    equipo   = partes[1]
    mercado  = partes[2]
    momio    = partes[3]
    try:
        monto = float(partes[4])
    except:
        bot.send_message(message.chat.id, "❌ El monto debe ser un número. Ej: 150")
        return

    partido = buscar_partido(equipo)
    if not partido:
        bot.send_message(message.chat.id,
            f"❌ No encontré partido con <b>{equipo}</b> hoy/mañana.",
            parse_mode="HTML")
        return

    nombre = f"{partido['local']} vs {partido['visita']}"
    liga   = partido.get("liga", "")

    # Registrar en CLV tracker
    aid = registrar_apuesta_clv(
        partido["id"], nombre, liga,
        partido.get("fecha", ""), mercado, momio, monto, 1000
    )

    # Calcular EV de la apuesta
    from poisson_kelly import prob_implicita, american_to_decimal
    dec  = american_to_decimal(momio)
    prob = prob_implicita(momio)
    ev_ref = (0.52 * (dec-1) - 0.48) * 100  # con prob sistema 52% estimada

    texto = (f"✅ <b>APUESTA REGISTRADA</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"⚽ Partido: <b>{nombre}</b>\n"
             f"📊 Mercado: <b>{mercado}</b>\n"
             f"💰 Momio: <b>{momio}</b> ({dec}x)\n"
             f"💵 Monto: <b>${monto:.2f}</b>\n"
             f"🏦 Prob casino: {prob*100:.1f}%\n"
             f"📈 Pago potencial: ${monto*dec:.2f}\n\n"
             f"ID de seguimiento: <code>{aid}</code>\n\n"
             f"Cuando el partido cierre, usa:\n"
             f"<code>/cierre {aid} [momio_cierre]</code>\n"
             f"Y para registrar resultado:\n"
             f"<code>/resultado {nombre.split()[0]} X-X</code>")
    bot.send_message(message.chat.id, texto, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# /cierre — Actualizar momio de cierre (CLV)
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["cierre"])
def cmd_cierre(message):
    """
    Actualiza el momio de cierre para calcular CLV.
    Formato: /cierre [id_apuesta] [momio_cierre] [W|L|P]
    Ejemplo: /cierre 3 -130 W
    """
    if not verificar_chat(message): return

    partes = message.text.split()
    if len(partes) < 3:
        bot.send_message(message.chat.id,
            "📝 Formato: <code>/cierre [id] [momio_cierre] [W/L/P]</code>\n"
            "Ejemplo: <code>/cierre 3 -130 W</code>\n\n"
            "W = Ganaste | L = Perdiste | P = Push (devuelto)",
            parse_mode="HTML")
        return

    try:
        apuesta_id = int(partes[1])
    except:
        bot.send_message(message.chat.id, "❌ El ID debe ser un número.")
        return

    momio_cierre = partes[2]
    resultado    = partes[3].upper() if len(partes) > 3 else None

    # Calcular ganancia estimada
    ganancia = None
    if resultado in ["W","L","P"]:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT apuesta_monto, momio_apostado FROM clv_tracker WHERE id=?",
                  (apuesta_id,))
        row = c.fetchone()
        conn.close()
        if row:
            monto, momio_ap = row
            from poisson_kelly import american_to_decimal
            dec = american_to_decimal(momio_ap)
            if resultado == "W":   ganancia = round(monto * (dec-1), 2)
            elif resultado == "L": ganancia = round(-monto, 2)
            else:                  ganancia = 0.0

    clv = actualizar_cierre_clv(apuesta_id, momio_cierre, resultado, ganancia)

    if clv is None:
        bot.send_message(message.chat.id,
            f"❌ No encontré la apuesta con ID {apuesta_id}.")
        return

    clv_emoji = "📈" if clv > 0 else "📉"
    res_texto = {"W":"✅ GANADA","L":"❌ PERDIDA","P":"⚖️ PUSH"}.get(resultado,"⏳ Pendiente")

    texto = (f"📐 <b>CLV ACTUALIZADO</b>\n"
             f"━━━━━━━━━━━━━━━━━━\n"
             f"ID apuesta: {apuesta_id}\n"
             f"Momio cierre: <b>{momio_cierre}</b>\n"
             f"{clv_emoji} CLV: <b>{clv:+.2f}%</b>\n"
             f"Resultado: {res_texto}\n")
    if ganancia is not None:
        texto += f"Ganancia: <b>${ganancia:+.2f}</b>\n"

    if clv > 0:
        texto += "\n✅ <i>CLV positivo — apostaste mejor que el cierre del mercado.</i>"
    else:
        texto += "\n⚠️ <i>CLV negativo — el mercado cerró mejor que tu entrada.</i>"

    texto += "\n\nUsa /clv para ver el reporte completo."
    bot.send_message(message.chat.id, texto, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# /sharp — Historial de movimientos detectados
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["sharp"])
def cmd_sharp(message):
    """Muestra el historial de movimientos de momios detectados."""
    if not verificar_chat(message): return

    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("""SELECT tipo, partido_nombre, mensaje, ts
                     FROM alertas_log WHERE tipo='momios'
                     ORDER BY ts DESC LIMIT 10""")
        rows = c.fetchall()
        conn.close()
    except:
        rows = []

    if not rows:
        bot.send_message(message.chat.id,
            "📈 Sin movimientos de momios registrados aún.\n"
            "Los movimientos se detectan automáticamente cada 5 min.",
            parse_mode="HTML")
        return

    texto = "📈 <b>MOVIMIENTOS DE MOMIOS DETECTADOS</b>\n━━━━━━━━━━━━━━━━━━\n"
    for tipo, partido, mensaje, ts in rows:
        hora = ts[:16] if ts else "N/D"
        texto += f"\n🕐 {hora}\n⚽ {partido}\n{mensaje[:150]}\n"

    enviar_largo(texto)


# ══════════════════════════════════════════════════════════════
# CALLBACKS NUEVOS para Mundial
# ══════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data.startswith("mundial_"))
def callback_mundial(call):
    if str(call.message.chat.id) != str(TELEGRAM_CHAT_ID): return
    data = call.data

    if data.startswith("mundial_grupo_"):
        grupo = data.replace("mundial_grupo_", "")
        texto = formatear_fixtures_grupo_telegram(grupo)
        bot.answer_callback_query(call.id, f"Grupo {grupo}")
        bot.send_message(call.message.chat.id, texto, parse_mode="HTML")

    elif data == "mundial_equipo_mexico":
        fixtures = get_fixture_por_equipo("México")
        bot.answer_callback_query(call.id, "Partidos de México")
        texto = "🇲🇽 <b>MÉXICO — MUNDIAL 2026</b>\n━━━━━━━━━━━━━━━━━━\n"
        for p in fixtures:
            alt = f" ⛰️{p['altitud']}m" if p["altitud"] > 1800 else ""
            texto += (f"\n⚽ {p['local']} vs {p['visita']}\n"
                     f"   {p['fecha']} · {p['hora_cst']} CST{alt}\n"
                     f"   🏟 {p['estadio']}\n"
                     f"   💰 {p['momios']['local']} / {p['momios']['empate']} / {p['momios']['visita']}\n")
        bot.send_message(call.message.chat.id, texto, parse_mode="HTML")

    elif data == "mundial_fecha_inicio":
        bot.answer_callback_query(call.id, "Partidos del 11-13 Jun")
        from advanced_modules import get_fixtures_fecha
        fechas = ["2026-06-11","2026-06-12","2026-06-13"]
        texto  = "📅 <b>INAUGURAL — 11 al 13 Jun 2026</b>\n━━━━━━━━━━━━━━━━━━\n"
        for fecha in fechas:
            ps = get_fixtures_fecha(fecha)
            if ps:
                texto += f"\n<b>{fecha}</b>\n"
                for p in ps:
                    texto += f"  ⚽ {p['local']} vs {p['visita']} ({p['hora_cst']})\n"
        bot.send_message(call.message.chat.id, texto, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# ACTUALIZAR /ayuda CON NUEVOS COMANDOS
# ══════════════════════════════════════════════════════════════

@bot.message_handler(commands=["ayuda","help"])
def cmd_ayuda_final(message):
    if not verificar_chat(message): return
    bot.send_message(message.chat.id,
        "⚡ <b>SINDICATOBOT v4.2 — COMANDOS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📅 PARTIDOS</b>\n"
        "/hoy — Menú principal\n"
        "/agenda — Todas las ligas hoy y mañana\n"
        "/ligas — Seleccionar liga\n"
        "/buscar [equipo] — Buscar en todas las ligas\n"
        "/analisis [equipo] — Análisis IA completo\n"
        "/momios [equipo] — Momios en tiempo real\n\n"
        "<b>🎯 ANÁLISIS ESTADÍSTICO (Poisson + Kelly)</b>\n"
        "/valor [equipo] — Apuestas con valor real (edge ≥5%)\n"
        "/ev [momio] [prob%] — Calculadora EV manual\n"
        "/calcparlay [momios] — Calculadora parlay\n\n"
        "<b>🌎 MUNDIAL 2026</b>\n"
        "/mundial — Menú de fixtures\n"
        "/mundial mexico — Partidos de México\n"
        "/mundial A — Grupo A completo\n"
        "/tabla — Todos los grupos A-L\n\n"
        "<b>📊 TRACKING Y ROI</b>\n"
        "/roi — ROI real por mercado y liga\n"
        "/clv — Closing Line Value del modelo\n"
        "/sharp — Movimientos de momios detectados\n"
        "/apostar [equipo] [mercado] [momio] [monto]\n"
        "/cierre [id] [momio_cierre] [W/L/P]\n\n"
        "<b>📈 RESULTADOS</b>\n"
        "/resultado [Local X-X Visita] — Registrar resultado\n"
        "/reporte — Rendimiento del modelo IA\n"
        "/historial — Últimas predicciones\n\n"
        "<b>🔔 ALERTAS</b>\n"
        "/alerta pre2h [0-1] — Alerta pre-partido\n"
        "/alerta analisis [0-1] — Análisis IA completo\n"
        "/alerta momios [0-1] — Momios actuales\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Alertas automáticas: 2h, 1h y 30min antes.\n"
        "Sharp money: monitoreo cada 5 min.\n"
        "CLV: actualiza al cierre de cada partido.</i>",
        parse_mode="HTML")
