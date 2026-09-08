import os
import json
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import plotly.graph_objects as go
from nicegui import ui, app

# =============================================================================
# CONSTANTES GLOBAIS E CONFIGURAÇÕES
# =============================================================================

PONTUACOES = {
    "1.0": {"Sim (40 pts)": 40.0, "Não (00 pts)": 0.0},
    "1.3": {"Sim (05 pts)": 5.0, "Não (00 pts)": 0.0},
    "1.4": {"Sim (50 pts)": 50.0, "Não (00 pts)": 0.0},
    "2.0": {"Sim (20 pts)": 20.0, "Não (00 pts)": 0.0},
    "3.0": {"Sim (10 pts)": 10.0, "Não (00 pts)": 0.0},
}

ESTADO = {
    "ano_selecionado": 2026,
    "respostas": {},
    "usuario": "Jefferson Espanha"
}

# =============================================================================
# BANCO DE DADOS (NEON / POSTGRESQL)
# =============================================================================

def get_db_url():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        # Fallback local para desenvolvimento se a variável não estiver no ambiente
        db_url = "postgresql://user:pass@localhost:5432/dbname?sslmode=require"
    if "channel_binding=" in db_url:
        db_url = db_url.split("&channel_binding=")[0].split("?channel_binding=")[0]
    if "sslmode=require" not in db_url and "localhost" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
    return db_url

def get_db_connection():
    return psycopg2.connect(get_db_url())

def carregar_respostas(ano: int) -> dict:
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT id, valor, pontos, link, comentarios FROM respostas WHERE ano = %s", (ano,))
                rows = cursor.fetchall()
                for row in rows:
                    comentarios = row.get("comentarios") or []
                    if isinstance(comentarios, str):
                        try:
                            comentarios = json.loads(comentarios)
                        except Exception:
                            comentarios = []
                    respostas[str(row["id"])] = {
                        "valor": row["valor"] or "",
                        "pontos": float(row["pontos"] or 0.0),
                        "link": row["link"] or "",
                        "comentarios": comentarios
                    }
    except Exception as e:
        logging.error(f"Erro ao carregar do banco: {e}")
    return respostas

def salvar_resposta_db(qid, valor, pontos, link, comentarios):
    ano = ESTADO["ano_selecionado"]
    comentarios_json = json.dumps(comentarios, ensure_ascii=False)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO respostas (id, ano, valor, pontos, link, comentarios, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (id, ano) DO UPDATE SET
                        valor = EXCLUDED.valor,
                        pontos = EXCLUDED.pontos,
                        link = EXCLUDED.link,
                        comentarios = EXCLUDED.comentarios,
                        atualizado_em = EXCLUDED.atualizado_em;
                """, (str(qid), int(ano), str(valor), float(pontos), str(link), comentarios_json, timestamp))
            conn.commit()
        ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive")
    except Exception as e:
        ui.notify(f"Erro ao salvar quesito {qid}: {e}", type="negative")

def zerar_ano_db(ano: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM respostas WHERE ano = %s", (ano,))
            conn.commit()
        ui.notify(f"Respostas de {ano} foram apagadas.", type="warning")
    except Exception as e:
        ui.notify(f"Erro ao zerar ano: {e}", type="negative")

# =============================================================================
# INTERFACE GRÁFICA (NICEGUI)
# =============================================================================

def calcular_total():
    return sum(v.get("pontos", 0.0) for v.get in ESTADO["respostas"].values())

def obter_faixa_cor(total):
    if total <= 500:   return "C", "#ef4444"
    elif total <= 599: return "C+", "#f97316"
    elif total <= 749: return "B", "#eab308"
    elif total <= 899: return "B+", "#22c55e"
    else:              return "A", "#16a34a"

def recarregar_dados():
    ESTADO["respostas"] = carregar_respostas(ESTADO["ano_selecionado"])

@ui.page("/")
def main_page():
    recarregar_dados()

    # --- SIDEBAR / PAINEL LATERAL ---
    with ui.left_drawer().classes("bg-slate-800 text-white p-4 w-64"):
        ui.label("🛠️ Painel IEG-M").classes("text-xl font-bold mb-4")
        
        ui.label("Ano de Referência:")
        select_ano = ui.select(
            options=[2024, 2025, 2026, 2027, 2028, 2029, 2030],
            value=ESTADO["ano_selecionado"]
        ).classes("w-full bg-white rounded p-1 text-black mb-4")

        # Container do Placar
        placar_container = ui.column().classes("w-full bg-slate-700 p-3 rounded mb-4")
        
        def atualizar_placar():
            placar_container.clear()
            total = calcular_total()
            faixa, cor = obter_faixa_cor(total)
            with placar_container:
                ui.label(f"Pontuação: {total:.1f} pts").classes("text-lg font-bold")
                ui.html(f"Faixa IEG-M: <b style='color:{cor}; font-size:18px;'>{faixa}</b>")

        atualizar_placar()

        def ao_mudar_ano(e):
            ESTADO["ano_selecionado"] = e.value
            recarregar_dados()
            atualizar_placar()
            ui.navigate.reload()

        select_ano.on_value_change(ao_mudar_ano)

        ui.button("🔄 Atualizar Dados", on_click=lambda: ui.navigate.reload()).classes("w-full bg-blue-600 mb-2")
        
        # Diálogo de Confirmação para Zerar
        with ui.dialog() as dialog_zerar, ui.card():
            ui.label("⚠️ Confirmar exclusão?").classes("font-bold text-lg text-red-600")
            ui.label(f"Isso irá apagar permanentemente os dados do ano {ESTADO['ano_selecionado']}.")
            input_senha = ui.input("Senha de Administrador", password=True)
            
            def processar_zerar():
                if input_senha.value == "fidelios":
                    zerar_ano_db(ESTADO["ano_selecionado"])
                    dialog_zerar.close()
                    recarregar_dados()
                    atualizar_placar()
                    ui.navigate.reload()
                else:
                    ui.notify("Senha incorreta!", type="negative")

            with ui.row():
                ui.button("Confirmar", on_click=processar_zerar).classes("bg-red-600 text-white")
                ui.button("Cancelar", on_click=dialog_zerar.close)

        ui.button("🗑️ Zerar Ano", on_click=dialog_zerar.open).classes("w-full bg-red-600 mb-6")

        ui.markdown("""
        <div style="font-size: 11px; text-align: center; color: #94a3b8; margin-top: auto;">
            <b>Desenvolvido por:</b><br>
            Jefferson Espanha<br>
            Procuradoria do Município<br>
            © 2026 • Francisco Morato / SP
        </div>
        """)

    # --- CORPO PRINCIPAL COM ABAS ---
    with ui.column().classes("w-full p-6"):
        ui.label("🏙️ Sistema de Gestão IEG-M").classes("text-3xl font-bold mb-4")

        with ui.tabs().classes("w-full") as tabs:
            tab_quest = ui.tab("📋 Questionário")
            tab_graf = ui.tab("📊 Desempenho")

        with ui.tab_panels(tabs, value=tab_quest).classes("w-full mt-4"):
            
            # --- ABA 1: QUESTIONÁRIO ---
            with ui.tab_panel(tab_quest):
                
                def render_card_quesito(qid, titulo, descricao, opcoes):
                    dados = ESTADO["respostas"].get(qid, {})
                    val_atual = dados.get("valor", list(opcoes.keys())[0])
                    link_atual = dados.get("link", "")
                    comentarios = list(dados.get("comentarios", []))

                    with ui.card().classes("w-full mb-4 border p-4 shadow-sm"):
                        ui.label(f"Quesito {qid} — {titulo}").classes("text-xl font-bold text-slate-800")
                        ui.label(descricao).classes("text-gray-600 mb-2")

                        with ui.row().classes("w-full items-center gap-4"):
                            radio_val = ui.radio(list(opcoes.keys()), value=val_atual).props("inline")
                            input_link = ui.input("Link da Evidência", value=link_atual).classes("grow")

                            def salvar():
                                val = radio_val.value
                                pts = opcoes.get(val, 0.0)
                                salvar_resposta_db(qid, val, pts, input_link.value, comentarios)
                                recarregar_dados()
                                atualizar_placar()

                            ui.button("💾 Salvar", on_click=salvar).classes("bg-green-600 text-white")

                        # Seção de Comentários / Diálogo Interno
                        with ui.expansion("💬 Diálogo Interno / Comentários").classes("w-full mt-2"):
                            container_com = ui.column().classes("w-full my-2")
                            
                            def render_comentarios():
                                container_com.clear()
                                with container_com:
                                    for idx, c in enumerate(comentarios):
                                        if isinstance(c, dict):
                                            with ui.row().classes("w-full bg-slate-100 p-2 rounded items-center justify-between"):
                                                ui.label(f"{c.get('autor')} ({c.get('data')}): {c.get('texto')}").classes("text-sm")
                                                
                                                def deletar(i=idx):
                                                    comentarios.pop(i)
                                                    salvar()
                                                    render_comentarios()

                                                ui.button("🗑️", on_click=deletar).props("flat dense color=red")

                            render_comentarios()
                            
                            input_novo_com = ui.input("Novo comentário...").classes("w-full")
                            
                            def adicionar_comentario():
                                txt = input_novo_com.value.strip()
                                if txt:
                                    comentarios.append({
                                        "autor": ESTADO["usuario"],
                                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                        "texto": txt
                                    })
                                    input_novo_com.value = ""
                                    salvar()
                                    render_comentarios()

                            ui.button("Postar Comentário", on_click=adicionar_comentario).classes("bg-blue-600 text-white mt-1")

                # Lista de Quesitos
                render_card_quesito("1.0", "Criação da COMPDEC / Defesa Civil", "Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil?", PONTUACOES["1.0"])
                render_card_quesito("1.3", "Plano de Contingência", "O município possui Plano de Contingência formalmente instituído?", PONTUACOES["1.3"])
                render_card_quesito("1.4", "Mapeamento de Áreas de Risco", "Existe mapeamento oficial das áreas de risco de desastres?", PONTUACOES["1.4"])
                render_card_quesito("2.0", "Capacitação da Equipe Técnico-Operacional", "Os servidores participaram de treinamentos em gestão de riscos?", PONTUACOES["2.0"])
                render_card_quesito("3.0", "Sistema de Alerta Precoce", "O município conta com sistema para emissão de alertas precoces?", PONTUACOES["3.0"])

            # --- ABA 2: GRÁFICOS ---
            with ui.tab_panel(tab_graf):
                ui.label("Evolução Histórica").classes("text-xl font-bold mb-2")
                
                # Montar gráfico Plotly
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=[str(ESTADO["ano_selecionado"])],
                    y=[calcular_total()],
                    marker_color=obter_faixa_cor(calcular_total())[1],
                    text=[f"{calcular_total():.1f} pts"],
                    textposition="outside"
                ))
                fig.update_layout(
                    title="Pontuação do Exercício Selecionado",
                    yaxis_title="Pontos",
                    xaxis_title="Ano",
                    height=400
                )
                
                ui.plotly(fig).classes("w-full h-96")

# =============================================================================
# INICIALIZAÇÃO DA APLICAÇÃO
# =============================================================================

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Painel IEG-M",
        port=8080,
        reload=False
    )
