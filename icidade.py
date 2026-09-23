import base64
from datetime import date, datetime
import json
import logging
import os
import re
from io import BytesIO

from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# Importações do ReportLab
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# =============================================================================
# BANCO DE DADOS (NEON)
# =============================================================================
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS respostas_icidade (
                        qid VARCHAR(50) NOT NULL,
                        ano INTEGER NOT NULL,
                        valor TEXT,
                        pontos REAL DEFAULT 0,
                        link TEXT,
                        comentarios JSONB DEFAULT '[]'::jsonb,
                        status VARCHAR(20) DEFAULT 'Pendente',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (ano, qid)
                    );
                """)
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao inicializar tabela respostas_icidade: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_icidade
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()
                for row in rows:
                    respostas[row["qid"]] = {
                        "valor": row["valor"] or "",
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": (
                            row["link"]
                            if row["link"] != "EMPTY_STRING"
                            else ""
                        ),
                        "comentarios": (
                            row["comentarios"]
                            if isinstance(row["comentarios"], list)
                            else []
                        ),
                        "status": row["status"] or "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB: {e}")
    return respostas


def save_resposta(
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    link_final = link.strip() if link else ""

    query = """
        INSERT INTO respostas_icidade (ano, qid, valor, pontos, link, comentarios, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ano, qid) 
        DO UPDATE SET
            valor = EXCLUDED.valor,
            pontos = EXCLUDED.pontos,
            link = EXCLUDED.link,
            comentarios = EXCLUDED.comentarios,
            status = EXCLUDED.status,
            updated_at = CURRENT_TIMESTAMP;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        int(ano),
                        str(qid),
                        str(valor),
                        float(pontos),
                        link_final,
                        Json(comentarios),
                        str(status),
                    ),
                )
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao salvar resposta no Neon DB: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_icidade WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")


def _obter_lista_comentarios(dados_q):
    coms = dados_q.get("comentarios", [])
    return coms if isinstance(coms, list) else []


# =============================================================================
# FUNÇÃO AUXILIAR DE RENDERIZAÇÃO DE QUESITOS (ATUALIZADA)
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes=None,
    tipo_input="radio",
    placeholder_texto="Digite sua resposta...",
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    dados_q = res_data.get(qid, {})
    valor_atual = dados_q.get("valor", "")
    link_atual = dados_q.get("link", "")

    # Para checkboxes, a resposta armazenada é uma lista ou string separada por vírgulas
    if tipo_input == "checkbox":
        itens_selecionados = [v.strip() for v in valor_atual.split(",") if v.strip()] if valor_atual else []
        state = {"opcao": itens_selecionados, "link": link_atual}
    else:
        state = {"opcao": valor_atual, "link": link_atual}

    with ui.card().classes(
        "w-full p-6 mb-4 border border-gray-300 rounded-lg shadow-sm bg-white"
    ):
        ui.label(f"{qid} • {titulo}").classes(
            "text-xl font-semibold text-blue-500 mb-3"
        )
        ui.label(pergunta).classes("text-base font-bold text-black mb-1")
        ui.label(
            "ℹ Preencha os campos abaixo e clique no botão de salvar."
        ).classes("text-xs text-gray-400 mb-6")

        # 1. RENDERIZAÇÃO DO CAMPO DE ENTRADA
        if tipo_input == "checkbox":
            if not opcoes:
                opcoes = {}
            
            with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                with ui.element("div").classes("flex flex-col gap-2"):
                    checkboxes = {}
                    for item in opcoes.keys():
                        chk = ui.checkbox(
                            text=item,
                            value=(item in state["opcao"])
                        ).props("color=blue")
                        checkboxes[item] = chk

                ui.textarea(
                    label="Link de Evidência / Documento:",
                    value=state["link"],
                    placeholder=placeholder_link,
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "link"
                )

        elif tipo_input in ["texto", "link"]:
            ui.textarea(
                label="Resposta / Detalhamento:",
                value=state["opcao"],
                placeholder=placeholder_texto if tipo_input == "texto" else placeholder_link,
            ).classes("w-full mb-4").props("outlined rows=3").bind_value(
                state, "opcao"
            )
        else:
            if not opcoes:
                opcoes = {"Selecione...": 0.0}

            if state["opcao"] not in opcoes:
                state["opcao"] = "Selecione..."

            with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                radio_opcao = (
                    ui.radio(
                        options=list(opcoes.keys()),
                        value=state["opcao"],
                    )
                    .props("color=blue")
                    .bind_value(state, "opcao")
                )

                ui.textarea(
                    label="Link de Evidência / Documento:",
                    value=state["link"],
                    placeholder=placeholder_link,
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "link"
                )

            pts_atuais = opcoes.get(state["opcao"], 0.0)
            label_impacto = ui.label(
                f"📊 Impacto de Pontuação no Quesito {qid}: {pts_atuais:.1f} pontos"
            ).classes("text-sm font-bold text-green-600 my-4")

            def ao_mudar_opcao(e):
                novos_pts = opcoes.get(e.value, 0.0)
                label_impacto.set_text(
                    f"📊 Impacto de Pontuação no Quesito {qid}: {novos_pts:.1f} pontos"
                )

            radio_opcao.on("update:model-value", ao_mudar_opcao)

        # 2. BOTÃO DE SALVAR
        def salvar_acao():
            if tipo_input == "checkbox":
                sel = [item for item, chk in checkboxes.items() if chk.value]
                opcao_sel = ", ".join(sel)
                pts = 0.0
            else:
                opcao_sel = state["opcao"]
                pts = opcoes.get(opcao_sel, 0.0) if (opcoes and tipo_input == "radio") else 0.0

            lnk = state["link"]

            save_resposta(
                ano=ano,
                qid=qid,
                valor=opcao_sel,
                pontos=pts,
                link=lnk,
                comentarios=dados_q.get("comentarios", []),
                status=dados_q.get("status", "Pendente"),
            )
            ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive")

            if on_save_callback:
                try:
                    on_save_callback()
                except Exception:
                    ui.run_javascript("window.location.reload()")

        ui.button("Salvar Resposta", on_click=salvar_acao).classes(
            "bg-blue-600 text-white font-bold px-4 py-2 mb-4"
        )

        ui.separator().classes("my-2")

        bloco_comentarios(
            qid=qid, res_data=res_data, on_save_callback=on_save_callback
        )

# =============================================================================
# PAINEL DE CONTROLE LATERAL
# =============================================================================
def render_painel_controle(ano_atual, on_mudar_ano, on_refresh):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    res_data = load_respostas(ano_atual)
    total_pts = sum(float(item.get("pontos", 0)) for item in res_data.values())

    if total_pts <= 500:
        faixa, cor = "C", "text-red-600"
    elif total_pts <= 599:
        faixa, cor = "C+", "text-orange-500"
    elif total_pts <= 749:
        faixa, cor = "B", "text-yellow-600"
    elif total_pts <= 899:
        faixa, cor = "B+", "text-green-500"
    else:
        faixa, cor = "A", "text-green-700"

    with ui.card().classes(
        "w-full bg-slate-100 p-4 border rounded-lg shadow-sm"
    ):
        ui.label("🛠️ Painel de Controle (icidade)").classes(
            "text-lg font-bold mb-2 text-blue-900"
        )

        ui.select(
            options=anos,
            value=int(ano_atual),
            label="Ano de Referência:",
            on_change=lambda e: on_mudar_ano(e.value),
        ).classes("w-full mb-4 bg-white")

        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("PONTUAÇÃO TOTAL").classes(
                "text-xs text-gray-500 font-bold uppercase tracking-wider"
            )
            ui.label(f"{total_pts:.1f} pts").classes(
                "text-3xl font-black text-gray-800 my-1"
            )
            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        def zerar_acao():
            zerar_questionario_db(ano_atual)
            ui.notify(
                f"✅ Questionário de {ano_atual} zerado!", type="positive"
            )
            on_refresh()

        ui.button("🔄 ATUALIZAR UI", on_click=on_refresh).classes(
            "w-full bg-blue-600 text-white font-bold mb-2"
        )
        ui.button("🗑️ ZERAR ANO", on_click=zerar_acao).classes(
            "w-full bg-red-600 text-white font-bold"
        )

        ui.separator().classes("my-4")
        ui.html("""
            <div style="text-align: center; color: #333; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 12px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
        """).classes("w-full")


# =============================================================================
# BLOCO DE COMENTÁRIOS INTERNOS
# =============================================================================
def bloco_comentarios(qid, res_data, on_save_callback=None):
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")

    dados_q = res_data.get(qid, {})
    historico = _obter_lista_comentarios(dados_q)

    status_global = dados_q.get("status", "Pendente")
    for com in reversed(historico):
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            break

    badge_status = (
        "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    )

    with ui.expansion(
        f"💬 Diálogo Interno {qid} | Status: {badge_status}",
        value=(status_global == "Pendente"),
    ).classes("w-full border rounded p-2 mt-3 bg-gray-50"):

        def alterar_status(e):
            novo_st = e.value
            log = {
                "autor": "Sistema / " + usuario_atual,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "texto": f"ℹ️ Alterou o status do quesito para: **{novo_st.upper()}**.",
                "status_definido": novo_st,
            }
            historico.append(log)
            save_resposta(
                ano=ano_sel,
                qid=qid,
                valor=dados_q.get("valor", ""),
                pontos=dados_q.get("pontos", 0.0),
                link=dados_q.get("link", ""),
                comentarios=historico,
                status=novo_st,
            )
            ui.notify(f"Status alterado para {novo_st}", type="info")
            if on_save_callback:
                on_save_callback()

        ui.radio(
            ["Resolvido", "Pendente"],
            value=status_global,
            on_change=alterar_status,
        ).props("inline")

        if historico:
            for idx, com in enumerate(historico):
                if isinstance(com, str):
                    com = {"autor": "Usuário", "data": "", "texto": com}

                autor = com.get("autor", "Anônimo")
                data_com = com.get("data", "")
                texto_com = com.get("texto", "")

                def deletar_comentario(i=idx):
                    historico.pop(i)
                    save_resposta(
                        ano=ano_sel,
                        qid=qid,
                        valor=dados_q.get("valor", ""),
                        pontos=dados_q.get("pontos", 0.0),
                        link=dados_q.get("link", ""),
                        comentarios=historico,
                        status=status_global,
                    )
                    ui.notify("Comentário removido.", type="warning")
                    if on_save_callback:
                        on_save_callback()

                with ui.row().classes(
                    "w-full items-center justify-between no-wrap mb-2"
                ):
                    if "Sistema /" in autor:
                        ui.html(
                            f"""<div style="background-color: #f1f3f5; padding: 6px 12px; border-radius: 6px; border-left: 3px solid #ced4da; width: 100%;">
                                <span style="font-size: 11px; color: #6c757d; font-style: italic;">{autor} - {data_com}</span>
                                <p style="margin: 2px 0 0 0; font-size: 12px; color: #495057;">{texto_com}</p>
                            </div>"""
                        ).classes("w-full")
                    else:
                        ui.html(
                            f"""<div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #1e88e5; border: 1px solid #e0e0e0; width: 100%;">
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>"""
                        ).classes("w-full")

                    ui.button("🗑️", on_click=deletar_comentario).props(
                        "flat dense"
                    )

        input_novo_comentario = (
            ui.textarea(placeholder="Novo comentário...")
            .classes("w-full")
            .props("outlined rows=2")
        )

        def postar_comentario():
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append({
                    "autor": usuario_atual,
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": txt,
                    "status_definido": status_global,
                })
                save_resposta(
                    ano=ano_sel,
                    qid=qid,
                    valor=dados_q.get("valor", ""),
                    pontos=dados_q.get("pontos", 0.0),
                    link=dados_q.get("link", ""),
                    comentarios=historico,
                    status=status_global,
                )
                ui.notify("Comentário publicado!", type="positive")
                if on_save_callback:
                    on_save_callback()

        ui.button("Postar Comentário", on_click=postar_comentario).classes(
            "bg-blue-600 text-white mt-2"
        )


# =============================================================================
# MÓDULO PRINCIPAL DE REQUISITOS (LAYOUT CORRIGIDO)
# =============================================================================
def container_formulario_icidade(ano=None):
    if "ano_referencia_global" not in app.storage.user:
        app.storage.user["ano_referencia_global"] = ano if ano else 2026

    @ui.refreshable
    def render_conteudo():
        ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
        res_data = load_respostas(ano_sel)

        def alterar_ano(novo_ano):
            app.storage.user["ano_referencia_global"] = int(novo_ano)
            ui.notify(f"Ano alterado para {novo_ano}", type="info")
            render_conteudo.refresh()

        # Grid principal de 12 colunas
        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-12 gap-6 items-start"
        ):

            # Coluna 1: Painel Lateral (3 colunas)
            with ui.element("div").classes("md:col-span-4 lg:col-span-3"):
                render_painel_controle(
                    ano_atual=ano_sel,
                    on_mudar_ano=alterar_ano,
                    on_refresh=render_conteudo.refresh,
                )

            # Coluna 2: Formulário (9 colunas) — TUDO DEVE FICAR DENTRO DESTE BLOCO
            with ui.element("div").classes(
                "md:col-span-8 lg:col-span-9 bg-white p-6 border rounded-lg shadow-sm flex flex-col gap-6"
            ):
                ui.label(f"📋 Módulo i-cidade — Ano {ano_sel}").classes(
                    "text-xl font-bold mb-2 text-slate-800 border-b pb-2"
                )

                # ==========================================
                # QUESITO 1.0 (Seleção com Pontuação)
                # ==========================================
                opcoes_10 = {
                    "Selecione...": 0.0,
                    "Sim (40.0 pts)": 40.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.0",
                    titulo="Criação da COMPDEC ou Órgão Similar",
                    pergunta="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil - COMPDEC ou órgão similar responsável pela execução, coordenação e mobilização de todas as ações de defesa civil no município?",
                    opcoes=opcoes_10,
                    placeholder_link="Insira o link para a comprovação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.1 (Campo de Texto / Normativo)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.1",
                    titulo="Instrumento Normativo de Criação da COMPDEC",
                    pergunta="Informe o Instrumento normativo, Número e Data da publicação da criação da COMPDEC ou órgão similar:",
                    tipo_input="texto",
                    placeholder_texto="Ex: Lei Municipal nº 1.234, de 10 de janeiro de 2020",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.2 (Link / Página Eletrônica)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.2",
                    titulo="Link do Instrumento Normativo",
                    pergunta="Informe a página eletrônica (link na internet) do instrumento normativo que criou a COMPDEC ou órgão similar (Se não estiver disponível na internet, inserir XYZ no campo de resposta):",
                    tipo_input="link",
                    placeholder_link="https://... ou digite XYZ",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.3 (Seleção com Pontuação)
                # ==========================================
                opcoes_13 = {
                    "Selecione...": 0.0,
                    "Gabinete do Prefeito (5.0 pts)": 5.0,
                    "Secretaria Municipal de Segurança Pública (0.0 pts)": 0.0,
                    "Controladoria (0.0 pts)": 0.0,
                    "Outra (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.3",
                    titulo="Vinculação Subordinativa da COMPDEC",
                    pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
                    opcoes=opcoes_13,
                    placeholder_link="Insira o link do organograma ou norma que comprove a vinculação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.4 (Seleção com Pontuação)
                # ==========================================
                opcoes_14 = {
                    "Selecione...": 0.0,
                    "Sim, inclusive com a participação de entidades privadas e da comunidade (50.0 pts)": 50.0,
                    "Sim, com participação de entidades privadas (20.0 pts)": 20.0,
                    "Sim, com participação da comunidade (20.0 pts)": 20.0,
                    "Sim, apenas com participação dos representantes da administração municipal (10.0 pts)": 10.0,
                    "Não atuam de forma sistêmica (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.4",
                    titulo="Atuação Sistêmica e Articulada",
                    pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a PNPDEC?",
                    opcoes=opcoes_14,
                    placeholder_link="Insira o link do documento, ata ou norma comprovatória...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.5 (Campo de Texto / Motivo)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.5",
                    titulo="Motivo da Não Instituição da COMPDEC",
                    pergunta="Informe o motivo de a COMPDEC ou órgão similar ainda não ter sido instituída (Ex: Instrumento normativo em elaboração, enviado para aprovação, falta de estrutura, outros):",
                    tipo_input="texto",
                    placeholder_texto="Detalhe o motivo da não instituição...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.0 (Capacitação de Agentes)
                # ==========================================
                opcoes_20 = {
                    "Selecione...": 0.0,
                    "Sim (20.0 pts)": 20.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.0",
                    titulo="Capacitação de Agentes Municipais",
                    pergunta="Sobre treinamento e capacitação sobre Proteção e Defesa Civil, a Prefeitura capacita seus agentes para ações municipais de Defesa Civil?",
                    opcoes=opcoes_20,
                    placeholder_link="Insira o link do certificado, lista de presença ou registro...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.1 (Data da Última Capacitação)
                # ==========================================
                opcoes_21 = {
                    "Selecione...": 0.0,
                    "Após 31/12/2023 [Até 2025] (30.0 pts)": 30.0,
                    "Até 31/12/2023 ou Sem Capacitação (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.1",
                    titulo="Data da Última Capacitação dos Agentes",
                    pergunta="Qual a data da última capacitação dos agentes municipais para ações de Defesa Civil? (Atenção: Não considerar capacitações do ano corrente de 2026):",
                    opcoes=opcoes_21,
                    placeholder_link="Informe a data no formato DD/MM/AAAA e insira o link da evidência...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.2 (Público-Alvo dos Treinamentos)
                # ==========================================
                opcoes_22 = {
                    "Selecione...": 0.0,
                    "Para escolas (5.0 pts)": 5.0,
                    "Para outras secretarias / entidades municipais (3.0 pts)": 3.0,
                    "Para munícipes ou empresas (2.0 pts)": 2.0,
                    "Não ofereceu nenhum curso/treinamento no ano (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.2",
                    titulo="Público Alvo dos Cursos e Treinamentos",
                    pergunta="A Prefeitura Municipal ofereceu cursos/treinamento sobre Proteção e Defesa Civil para qual público?",
                    opcoes=opcoes_22,
                    placeholder_link="Insira o link das evidências dos treinamentos oferecidos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.0 (Estímulo à Participação)
                # ==========================================
                opcoes_30 = {
                    "Selecione...": 0.0,
                    "Sim (10.0 pts)": 10.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.0",
                    titulo="Estímulo à Participação da Sociedade Civil",
                    pergunta="O Município realiza ações para estimular a participação de entidades privadas, associações de voluntários, clubes de serviços, organizações não governamentais e associações de classe e comunitárias nas ações de proteção e defesa civil?",
                    opcoes=opcoes_30,
                    placeholder_link="Insira o link para a comprovação das ações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.1 (Ações Realizadas - Checkbox)
                # ==========================================
                opcoes_31 = {
                    "Workshop / Palestra": 0.0,
                    "Reunião": 0.0,
                    "Conferência": 0.0,
                    "Congresso": 0.0,
                    "Discussão na Câmara Municipal": 0.0,
                    "Treinamentos": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.1",
                    titulo="Ações Realizadas com a Sociedade Civil",
                    pergunta="Assinale quais ações foram realizadas:",
                    tipo_input="checkbox",
                    opcoes=opcoes_31,
                    placeholder_link="Insira o link das atas, fotos ou comprovações das ações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.1.1 (Data do Último Treinamento)
                # ==========================================
                opcoes_311 = {
                    "Selecione...": 0.0,
                    "Após 31/12/2023 [Até 2025] (10.0 pts)": 10.0,
                    "Até 31/12/2023 ou Sem Treinamento (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.1.1",
                    titulo="Data do Último Treinamento de Voluntários",
                    pergunta="Qual a data do último treinamento de associações de voluntários? (Atenção: Não considerar os treinamentos realizados em 2026):",
                    opcoes=opcoes_311,
                    placeholder_link="Informe a data (DD/MM/AAAA) e insira o link do certificado ou ata...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.0 (Carta Geotécnica)
                # ==========================================
                opcoes_40 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.0",
                    titulo="Carta Geotécnica de Suscetibilidade",
                    pergunta="O Município recebeu a Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco? (Disponível em: http://www.defesacivil.sp.gov.br/instrumentos-de-identificacao-de-riscos/):",
                    opcoes=opcoes_40,
                    placeholder_link="Insira o link para a publicação ou recebimento do documento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.1 (Ameaças Potenciais - Checkbox)
                # ==========================================
                opcoes_41 = {
                    "Riscos Geológicos": 0.0,
                    "Riscos Hidrológicos": 0.0,
                    "Riscos Meteorológicos": 0.0,
                    "Riscos Climatológicos": 0.0,
                    "Riscos Biológicos": 0.0,
                    "Riscos Tecnológicos": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.1",
                    titulo="Ameaças Potenciais Identificadas na Carta Geotécnica",
                    pergunta="Assinale quais os tipos de ameaças potenciais identificadas na Carta Geotécnica (Classificação COBRADE):",
                    tipo_input="checkbox",
                    opcoes=opcoes_41,
                    placeholder_link="Insira o link para a documentação ou trecho da Carta Geotécnica...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.2 (Carta no Plano Diretor)
                # ==========================================
                opcoes_42 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                    "Não se aplica o Plano Diretor para o município (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.2",
                    titulo="Carta Geotécnica no Plano Diretor",
                    pergunta="A Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco consta no Plano Diretor? (Art. 42-A, §1º, §2º e §3º, da Lei Federal nº 10.257/2001):",
                    opcoes=opcoes_42,
                    placeholder_link="Insira o link da Lei do Plano Diretor ou comprovação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.0 (Mapeamento Próprio de Ameaças)
                # ==========================================
                opcoes_50 = {
                    "Selecione...": 0.0,
                    "Sim (200.0 pts)": 200.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.0",
                    titulo="Mapeamento e Identificação Própria de Ameaças",
                    pergunta="O Município realizou, por conta própria, o mapeamento e identificação das principais ameaças existentes em seu território?",
                    opcoes=opcoes_50,
                    placeholder_link="Insira o link do estudo, relatório ou mapa de ameaças...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1 (Principais Ameaças - Checkbox)
                # ==========================================
                opcoes_51 = {
                    "Epidemias": 0.0,
                    "Estiagem": 0.0,
                    "Incêndios (urbanos e florestais)": 0.0,
                    "Ondas de calor ou ondas de frio": 0.0,
                    "Inundações": 0.0,
                    "Infestações e Pragas": 0.0,
                    "Ameaças radioativas": 0.0,
                    "Deslizamentos": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1",
                    titulo="Principais Ameaças Identificadas",
                    pergunta="Assinale as principais ameaças identificadas no território municipal:",
                    tipo_input="checkbox",
                    opcoes=opcoes_51,
                    placeholder_link="Insira o link para a comprovação ou mapeamento do risco...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.1 (Fiscalização das Áreas de Risco)
                # ==========================================
                opcoes_511 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente (0.0 pts)": 0.0,
                    "Sim, parcialmente (0.0 pts)": 0.0,
                    "Não houve fiscalização (-100.0 pts)": -100.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.1",
                    titulo="Fiscalização das Áreas de Risco",
                    pergunta="As secretarias setoriais realizaram a fiscalização das áreas de risco?",
                    opcoes=opcoes_511,
                    placeholder_link="Insira o link dos relatórios de fiscalização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2 (Possibilidade de Ocupação/Invasão)
                # ==========================================
                opcoes_512 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2",
                    titulo="Possibilidade de Ocupação ou Invasão em Áreas de Risco",
                    pergunta="O município possui áreas de risco com possibilidade de ocupação/invasão?",
                    opcoes=opcoes_512,
                    placeholder_link="Insira o link da documentação de monitoramento ou parecer...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 5.1.1 (Fiscalização de Áreas de Risco)
                # ==========================================
                opcoes_511 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente (0.0 pts)": 0.0,
                    "Sim, parcialmente (0.0 pts)": 0.0,
                    "Não houve fiscalização (-100.0 pts)": -100.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.1",
                    titulo="Fiscalização das Áreas de Risco",
                    pergunta="As secretarias setoriais realizaram a fiscalização das áreas de risco?",
                    opcoes=opcoes_511,
                    placeholder_link="Insira o link dos relatórios de fiscalização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2 (Possibilidade de Ocupação)
                # ==========================================
                opcoes_512 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2",
                    titulo="Áreas de Risco Sujeitas a Ocupação ou Invasão",
                    pergunta="O município possui áreas de risco com possibilidade de ocupação/invasão?",
                    opcoes=opcoes_512,
                    placeholder_link="Insira o link do levantamento ou relatório de monitoramento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2.1 (Mecanismos de Vedação - Checkbox)
                # ==========================================
                opcoes_5121 = {
                    "Aplicação de sanções monetárias (multas)": 0.0,
                    "Monitoramento (fiscalização)": 0.0,
                    "Notificação dos infratores": 0.0,
                    "Interdição do local e remoção das famílias": 0.0,
                    "Demolição das ocupações": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2.1",
                    titulo="Mecanismos para Vedar Novas Ocupações",
                    pergunta="Assinale os mecanismos para vedar novas ocupações nas áreas de riscos:",
                    tipo_input="checkbox",
                    opcoes=opcoes_5121,
                    placeholder_link="Insira o link para a comprovação das ações de fiscalização/coibição...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.2 (Informação à População)
                # ==========================================
                opcoes_52 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Parcialmente (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.2",
                    titulo="Informação da População sobre Ameaças",
                    pergunta="A população foi informada sobre todas as ameaças identificadas pelo município?",
                    opcoes=opcoes_52,
                    placeholder_link="Insira o link das campanhas, boletins ou publicações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 6.0 (Vistoria em Edificações)
                # ==========================================
                opcoes_60 = {
                    "Selecione...": 0.0,
                    "Sim, de acordo com um cronograma preestabelecido (0.0 pts)": 0.0,
                    "Sim, de acordo com a demanda (0.0 pts)": 0.0,
                    "Não foram vistoriadas (-50.0 pts)": -50.0,
                    "Não houve casos de edificações vulneráveis no Município (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="6.0",
                    titulo="Vistorias em Edificações Vulneráveis",
                    pergunta="A Secretaria responsável realizou vistorias em edificações vulneráveis com o objetivo de identificar a necessidade de intervenção preventiva nos imóveis?",
                    opcoes=opcoes_60,
                    placeholder_link="Insira o link do cronograma, ordens de serviço ou relatórios...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 7.0 (Plano de Contingência - PLANCON)
                # ==========================================
                opcoes_70 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.0",
                    titulo="Plano de Contingência Municipal (PLANCON)",
                    pergunta="O Município possui Plano de Contingência Municipal – PLANCON de Defesa Civil?",
                    opcoes=opcoes_70,
                    placeholder_link="Insira o link da publicação do PLANCON...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.1 (PLANCON por Ameaça Especificada)
                # ==========================================
                opcoes_71 = {
                    "Selecione...": 0.0,
                    "Sim, cada ameaça mapeada possui um PLANCON diferente (5.0 pts)": 5.0,
                    "Sim, parte das ameaças possuem PLANCON diferentes (3.0 pts)": 3.0,
                    "Existe apenas um PLANCON que abrange todas as ameaças (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.1",
                    titulo="PLANCON Específico por Ameaça",
                    pergunta="Foi elaborado um PLANCON específico para cada ameaça identificada?",
                    opcoes=opcoes_71,
                    placeholder_link="Insira o link para os planos específicos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.2 (Exercícios Simulados)
                # ==========================================
                opcoes_72 = {
                    "Selecione...": 0.0,
                    "Sim (80.0 pts)": 80.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.2",
                    titulo="Exercícios Simulados do PLANCON",
                    pergunta="São realizados regularmente exercícios simulados para as contingências previstas no PLANCON?",
                    opcoes=opcoes_72,
                    placeholder_link="Insira o link de relatórios, fotos ou atas dos simulados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.3 (Sistema de Alerta)
                # ==========================================
                opcoes_73 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.3",
                    titulo="Sistema de Alerta de Desastres",
                    pergunta="O Município possui sistema de alerta para desastres? (Objetivo: avisar a população vulnerável antes de ocorrer o evento):",
                    opcoes=opcoes_73,
                    placeholder_link="Insira o link do documento ou norma que regulamenta o alerta...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.3.1 (Tipos de Sistema de Alerta - Checkbox)
                # ==========================================
                opcoes_731 = {
                    "Alerta via SMS": 0.0,
                    "Anúncio por rádio/Televisão": 0.0,
                    "Placas de identificação de área de risco": 0.0,
                    "Aviso à comunidade por telefone / Aplicativo de mensagens": 0.0,
                    "Aviso à comunidade por email": 0.0,
                    "Aviso aos membros do Nupdec": 0.0,
                    "Outro": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.3.1",
                    titulo="Tipos de Sistemas de Alerta Utilizados",
                    pergunta="Assinale os tipos de sistemas de alerta utilizados pelo Município (Objetivo: avisar a população vulnerável antes de ocorrer o evento):",
                    tipo_input="checkbox",
                    opcoes=opcoes_731,
                    placeholder_link="Insira o link com fotos, comprovantes de envio ou registros dos alertas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.4 (Sistema de Alarme)
                # ==========================================
                opcoes_74 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.4",
                    titulo="Sistema de Alarme para Desastres",
                    pergunta="O Município dispõe de sinal, dispositivo ou sistema de alarme para desastres? (Objetivo: avisar a população sobre o evento que está ocorrendo):",
                    opcoes=opcoes_74,
                    placeholder_link="Insira o link da norma, contrato ou registro do sistema de alarme...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.4.1 (Tipos de Sistema de Alarme - Checkbox)
                # ==========================================
                opcoes_741 = {
                    "Sinal sonoro (sirene)": 0.0,
                    "Sinal luminoso": 0.0,
                    "Carros de emergência equipados de sirenes": 0.0,
                    "Carros de emergência com alto-falantes": 0.0,
                    "Aviso aos membros do Nupdec": 0.0,
                    "Aviso à comunidade por telefone / Aplicativo de mensagens": 0.0,
                    "Uso da imprensa (TV, rádio, internet)": 0.0,
                    "Outro": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.4.1",
                    titulo="Tipos de Sinal, Dispositivo ou Sistema de Alarme",
                    pergunta="Assinale os tipos de sinal, dispositivo ou sistema de alarme utilizado pelo Município:",
                    tipo_input="checkbox",
                    opcoes=opcoes_741,
                    placeholder_link="Insira o link das evidências dos dispositivos e alarmes acionados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.5 (Cadastro de Abrigos na CEPDEC)
                # ==========================================
                opcoes_75 = {
                    "Selecione...": 0.0,
                    "Sim, atualizado (10.0 pts)": 10.0,
                    "Sim, mas não está atualizado (3.0 pts)": 3.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.5",
                    titulo="Cadastro de Locais para Abrigo na CEPDEC",
                    pergunta="Possui cadastro dos locais para abrigo à população em situação de desastre junto à Coordenadoria Estadual de Proteção e Defesa Civil (CEPDEC)?",
                    opcoes=opcoes_75,
                    placeholder_link="Insira o link da comprovação de cadastro/atualização junto à CEPDEC...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.6 (Fornecedores de Ajuda Humanitária)
                # ==========================================
                opcoes_76 = {
                    "Selecione...": 0.0,
                    "Sim, atualizado (10.0 pts)": 10.0,
                    "Sim, mas não está atualizado (3.0 pts)": 3.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.6",
                    titulo="Lista de Fornecedores de Ajuda Humanitária",
                    pergunta="O Município possui cadastro da lista de fornecedores para coleta e distribuição de suprimentos de ajuda humanitária para o caso de desastre?",
                    opcoes=opcoes_76,
                    placeholder_link="Insira o link da relação de fornecedores cadastrados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.7 (Data da Última Atualização do PLANCON)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.7",
                    titulo="Data da Última Atualização do PLANCON",
                    pergunta="Qual a data da última atualização do PLANCON? (Se não houve atualização, informar a data do início da vigência do PLANCON):",
                    tipo_input="texto",
                    placeholder_texto="Informe a data (DD/MM/AAAA) e observações...",
                    placeholder_link="Insira o link do documento indicando a data de vigência/atualização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.0 (Canal de Atendimento de Emergência)
                # ==========================================
                opcoes_80 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.0",
                    titulo="Canal de Atendimento de Emergência",
                    pergunta="O Município possui um canal de atendimento de emergência à população para registro de ocorrências de desastres?",
                    opcoes=opcoes_80,
                    placeholder_link="Insira o link comprovando a disponibilização do canal de atendimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.1 (Canais Utilizados - Checkbox)
                # ==========================================
                opcoes_81 = {
                    "Telefone de emergências": 0.0,
                    "Aplicativo de mensagens": 0.0,
                    "Correio eletrônico (e-mail)": 0.0,
                    "Aplicativo da Prefeitura": 0.0,
                    "Site da Prefeitura": 0.0,
                    "Redes sociais": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.1",
                    titulo="Canais de Contato para Emergências",
                    pergunta="Assinale os canais que possui (Como a população entra em contato com o Município em caso de desastre):",
                    tipo_input="checkbox",
                    opcoes=opcoes_81,
                    placeholder_link="Insira o link ou divulgação dos canais de atendimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.1.1 (Utilização do Número 199)
                # ==========================================
                opcoes_811 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.1.1",
                    titulo="Uso do Número 199 da Defesa Civil",
                    pergunta="Sobre o número de telefone de emergência, utiliza o número 199 da Defesa Civil?",
                    opcoes=opcoes_811,
                    placeholder_link="Insira o link para a linha ou ato normativo do telefone 199...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 8.1.1.1 (Atendimento 24h do Telefone 199)
                # ==========================================
                opcoes_8111 = {
                    "Selecione...": 0.0,
                    "Sim (20.0 pts)": 20.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.1.1.1",
                    titulo="Atendimento 24 Horas do Telefone 199",
                    pergunta="O telefone 199 tem atendimento 24 horas por dia?",
                    opcoes=opcoes_8111,
                    placeholder_link="Insira o link da escala de plantão, decreto ou documento comprobatório...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.2 (Registro Eletrônico de Ocorrências)
                # ==========================================
                opcoes_82 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.2",
                    titulo="Registro Eletrônico de Ocorrências",
                    pergunta="O Município registra as ocorrências de Defesa Civil de forma eletrônica? (Registro eletrônico refere-se ao sistema auditável sem alteração/exclusão sem log de usuário e data/hora):",
                    opcoes=opcoes_82,
                    placeholder_link="Insira o link do sistema, tela do software ou termo de uso...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 9.0 (Estudo de Estrutura de Escolas/Saúde)
                # ==========================================
                opcoes_90 = {
                    "Selecione...": 0.0,
                    "Sim, em todas as escolas e centros de saúde (100.0 pts)": 100.0,
                    "Sim, na maior parte das escolas e centros de saúde (50.0 pts)": 50.0,
                    "Sim, na menor parte das escolas e centros de saúde (20.0 pts)": 20.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="9.0",
                    titulo="Avaliação Estrutural de Escolas e Unidades de Saúde",
                    pergunta="O Município realizou um estudo de avaliação da estrutura de todas as escolas e unidades de saúde para garantir abrigo/atendimento em caso de desastre? (Atualizado: até 5 anos, entre 2021 e 2025):",
                    opcoes=opcoes_90,
                    placeholder_link="Insira o link do relatório de engenharia ou laudo de vistoria...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 10.0 (Plano de Mobilidade Urbana)
                # ==========================================
                opcoes_100 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-100.0 pts)": -100.0,
                    "Não se aplica (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.0",
                    titulo="Plano de Mobilidade Urbana",
                    pergunta="O Município elaborou seu Plano de Mobilidade Urbana?",
                    opcoes=opcoes_100,
                    placeholder_link="Insira o link da Lei do Plano de Mobilidade Urbana...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.0 (Transporte Público Coletivo)
                # ==========================================
                opcoes_110 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.0",
                    titulo="Existência de Transporte Público Coletivo",
                    pergunta="No Município existe transporte público coletivo? (Atenção: Não considerar transporte intermunicipal, interestadual ou internacional):",
                    opcoes=opcoes_110,
                    placeholder_link="Insira o link do contrato de concessão, linhas urbanas ou decreto...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 11.1 (Metas de Qualidade do Transporte)
                # ==========================================
                opcoes_111 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.1",
                    titulo="Metas de Qualidade e Desempenho do Transporte Público",
                    pergunta="Foram estabelecidas metas de qualidade e desempenho para o transporte público coletivo municipal?",
                    opcoes=opcoes_111,
                    placeholder_link="Insira o link do contrato, decreto ou edital com as metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.1.1 (Cumprimento das Metas)
                # ==========================================
                opcoes_1111 = {
                    "Selecione...": 0.0,
                    "Todas as metas foram atingidas (0.0 pts)": 0.0,
                    "A maior parte das metas foram atingidas (-5.0 pts)": -5.0,
                    "A menor parte das metas foram atingidas (-10.0 pts)": -10.0,
                    "As metas não foram atingidas (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.1.1",
                    titulo="Atingimento das Metas de Qualidade e Desempenho",
                    pergunta="As metas de qualidade e desempenho do transporte público coletivo estão sendo atingidas?",
                    opcoes=opcoes_1111,
                    placeholder_link="Insira o link do relatório de fiscalização ou monitoramento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.1.1.1 (Aplicação de Penalidades)
                # ==========================================
                opcoes_11111 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.1.1.1",
                    titulo="Aplicação de Penalidade por Meta Não Cumprida",
                    pergunta="Foi aplicada penalidade pela meta não cumprida?",
                    opcoes=opcoes_11111,
                    placeholder_link="Insira o link do processo administrativo ou auto de infração...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.2 (Pesquisa de Satisfação)
                # ==========================================
                opcoes_112 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.2",
                    titulo="Pesquisa de Satisfação dos Usuários (2025)",
                    pergunta="Foi realizada pesquisa de satisfação dos usuários do transporte público coletivo em 2025?",
                    opcoes=opcoes_112,
                    placeholder_link="Insira o link do relatório da pesquisa realizada...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.2.1 (Ações Decorrentes da Pesquisa)
                # ==========================================
                opcoes_1121 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.2.1",
                    titulo="Ações com Base na Pesquisa de Satisfação",
                    pergunta="Foram realizadas ações com base nesta pesquisa?",
                    opcoes=opcoes_1121,
                    placeholder_link="Insira o link do plano de ação ou melhorias implementadas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.3 (Resultado Tarifário 2025)
                # ==========================================
                opcoes_113 = {
                    "Selecione...": 0.0,
                    "Déficit ou subsídio tarifário (0.0 pts)": 0.0,
                    "Superávit tarifário (0.0 pts)": 0.0,
                    "Não sabe informar (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.3",
                    titulo="Equilíbrio / Resultado Tarifário em 2025",
                    pergunta="Quanto ao custo do transporte público (tarifa de remuneração) e o preço de passagem (tarifa pública), informe qual o resultado no ano de 2025:",
                    opcoes=opcoes_113,
                    placeholder_link="Insira o link do balanço financeiro ou estudo tarifário...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 11.3.1 (Link Benefícios Tarifários)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.3.1",
                    titulo="Divulgação dos Benefícios Tarifários",
                    pergunta="Informe a página eletrônica (link na internet) em que os benefícios tarifários concedidos no valor das tarifas do transporte público foram divulgados (Se não estiver disponível na internet, inserir XYZ no campo de resposta):",
                    tipo_input="link",
                    placeholder_link="https://... ou digite XYZ",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 12.0 (Transporte Remunerado Privado / Aplicativos)
                # ==========================================
                opcoes_120 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.0",
                    titulo="Transporte Remunerado Privado Individual (Táxi / Aplicativos)",
                    pergunta="O Município possui transporte remunerado privado individual de passageiros (táxi por aplicativos)?",
                    opcoes=opcoes_120,
                    placeholder_link="Insira o link da regulamentação ou lei municipal...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 12.1 (Regulamentação do Transporte por Aplicativo)
                # ==========================================
                opcoes_121 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.1",
                    titulo="Regulamentação do Transporte Remunerado Privado Individual",
                    pergunta="O Município regulamentou o transporte remunerado privado individual de passageiros (táxi por aplicativo como Uber, 99 e similares)?",
                    opcoes=opcoes_121,
                    placeholder_link="Insira o link para o decreto ou lei municipal...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 12.1.1 (Normativo de Regulamentação)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.1.1",
                    titulo="Instrumento Normativo de Regulamentação",
                    pergunta="Informe o Instrumento normativo, Número e Data da publicação:",
                    tipo_input="texto",
                    placeholder_texto="Ex: Lei Municipal nº 2.456, de 15 de maio de 2021",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 12.1.2 (Link do Normativo)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.1.2",
                    titulo="Link do Instrumento Normativo",
                    pergunta="Informe a página eletrônica (link na internet) do instrumento normativo de transporte remunerado privado individual de passageiros (Se não estiver disponível na internet, inserir XYZ no campo de resposta):",
                    tipo_input="link",
                    placeholder_link="https://... ou digite XYZ",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 12.1.3 (Fiscalização Regular)
                # ==========================================
                opcoes_1213 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.1.3",
                    titulo="Fiscalização do Transporte por Aplicativo",
                    pergunta="O Município fiscaliza regularmente o transporte remunerado privado individual de passageiros (táxi por aplicativo como Uber, 99 e similares)?",
                    opcoes=opcoes_1213,
                    placeholder_link="Insira o link para relatórios de fiscalização ou autos de infração...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 12.1.3.1 (Periodicidade da Fiscalização)
                # ==========================================
                opcoes_12131 = {
                    "Selecione...": 0.0,
                    "Diariamente (0.0 pts)": 0.0,
                    "Semanalmente (0.0 pts)": 0.0,
                    "Mensalmente (0.0 pts)": 0.0,
                    "Anualmente (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.1.3.1",
                    titulo="Periodicidade da Fiscalização",
                    pergunta="Informe a periodicidade da fiscalização realizada pelo município:",
                    opcoes=opcoes_12131,
                    placeholder_link="Insira o link do cronograma ou relatórios periódicos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 13.0 (Transporte Não Motorizado - 2025)
                # ==========================================
                opcoes_130 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.0",
                    titulo="Incentivo ao Transporte Não Motorizado em 2025",
                    pergunta="Foram realizadas ações para estimular a adoção/uso dos meios de transporte não motorizados em 2025?",
                    opcoes=opcoes_130,
                    placeholder_link="Insira o link comprovando campanhas, ciclovias ou eventos promovidos em 2025...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 13.1 (Ações para Transporte Não Motorizado - Checkbox)
                # ==========================================
                opcoes_131 = {
                    "Instalação/manutenção de ciclovias ou ciclofaixas": 0.0,
                    "Instalação/manutenção de pontos de locação de bicicletas": 0.0,
                    "Instalação/manutenção de pontos de locação de patinetes": 0.0,
                    "Outras": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.1",
                    titulo="Ações Realizadas para Transporte Não Motorizado em 2025",
                    pergunta="Assinale as ações realizadas para estimular a adoção/uso dos meios de transporte não motorizados em 2025:",
                    tipo_input="checkbox",
                    opcoes=opcoes_131,
                    placeholder_link="Insira o link das evidências das ações ou fotos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 13.1.1 (Cronograma de Manutenção)
                # ==========================================
                opcoes_1311 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.1.1",
                    titulo="Cronograma de Manutenção de Ciclovias e Ciclofaixas",
                    pergunta="Possui um cronograma de manutenção da infraestrutura das ciclovias ou ciclofaixas?",
                    opcoes=opcoes_1311,
                    placeholder_link="Insira o link do cronograma oficial de manutenção...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 13.1.1.1 (Prazo das Manutenções Preventivas)
                # ==========================================
                opcoes_13111 = {
                    "Selecione...": 0.0,
                    "Sim, para todos os trechos (0.0 pts)": 0.0,
                    "Sim, para a maior parte dos trechos (-5.0 pts)": -5.0,
                    "Sim, para a menor parte dos trechos (-10.0 pts)": -10.0,
                    "Não foram realizadas dentro do prazo (-15.0 pts)": -15.0,
                    "Não foram realizadas manutenções preventivas no exercício (-20.0 pts)": -20.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.1.1.1",
                    titulo="Cumprimento do Prazo de Manutenção Preventiva",
                    pergunta="As manutenções preventivas da infraestrutura das ciclovias ou ciclofaixas foram realizadas dentro do prazo?",
                    opcoes=opcoes_13111,
                    placeholder_link="Insira o link dos relatórios ou ordens de serviço executadas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 14.0 (Acessibilidade de Calçamentos Públicos)
                # ==========================================
                opcoes_140 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente - Todos os calçamentos públicos (0.0 pts)": 0.0,
                    "Sim, parcialmente - Em parte dos calçamentos públicos (-10.0 pts)": -10.0,
                    "Não possui acessibilidade em calçamentos públicos (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="14.0",
                    titulo="Acessibilidade em Calçamentos Públicos",
                    pergunta="O Município adequou os calçamentos públicos para acessibilidade das pessoas com deficiência e restrição de mobilidade? (Calçamento público é no entorno de prédios públicos e locais de grande circulação):",
                    opcoes=opcoes_140,
                    placeholder_link="Insira o link das vistorias, laudos de acessibilidade ou relatórios de obras...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 14.1 (Recursos de Acessibilidade - Checkbox)
                # ==========================================
                opcoes_141 = {
                    "Calçadas com dimensões mínimas para a circulação": 0.0,
                    "Sinalização tátil em pisos": 0.0,
                    "Rampas de acesso": 0.0,
                    "Escadas com corrimão": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="14.1",
                    titulo="Recursos de Acessibilidade Oferecidos",
                    pergunta="Informe os recursos de acessibilidade oferecidos pela Prefeitura:",
                    tipo_input="checkbox",
                    opcoes=opcoes_141,
                    placeholder_link="Insira o link para relatórios, fotos ou projetos de acessibilidade...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 15.0 (Sinalização de Vias Públicas)
                # ==========================================
                opcoes_150 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente - Todas as vias públicas municipais (50.0 pts)": 50.0,
                    "Sim, parcialmente - Em parte das vias municipais (10.0 pts)": 10.0,
                    "Não estão sinalizadas (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="15.0",
                    titulo="Sinalização de Vias Públicas Pavimentadas",
                    pergunta="As vias públicas pavimentadas estão devidamente sinalizadas (vertical e horizontalmente) de forma a garantir as condições adequadas de segurança na circulação?",
                    opcoes=opcoes_150,
                    placeholder_link="Insira o link para o plano de sinalização, ordens de serviço ou fotos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 16.0 (Manutenção de Vias Públicas)
                # ==========================================
                opcoes_160 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente - Todas as vias públicas municipais (50.0 pts)": 50.0,
                    "Sim, parcialmente - Em parte das vias municipais (10.0 pts)": 10.0,
                    "Não estão adequadas (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="16.0",
                    titulo="Manutenção de Vias Públicas Municipais",
                    pergunta="Há manutenção adequada das vias públicas no Município? (De acordo com os Manuais do DNIT):",
                    opcoes=opcoes_160,
                    placeholder_link="Insira o link para relatórios de operação tapa-buracos, recapeamento ou certidões...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 17.1 (Impressões e Sugestões Finais)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="17.1",
                    titulo="Impressões, Comentários e Sugestões",
                    pergunta="Gostaria de registrar suas impressões, comentários e sugestões a respeito do presente questionário?",
                    tipo_input="texto",
                    placeholder_texto="Registre aqui suas impressões, críticas ou sugestões sobre a avaliação...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # DADOS EXTERNOS DO i-CIDADE: QUESITO C1
                # ==========================================
                opcoes_c1 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="C1",
                    titulo="Inscrição no Programa Cidades Resilientes 2030 (MCR2030)",
                    pergunta="O Município estava inscrito no Programa Construindo Cidades Resilientes 2030 da ONU?",
                    opcoes=opcoes_c1,
                    placeholder_link="Insira o link de comprovação de inscrição ou página no portal MCR2030...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # DADOS EXTERNOS DO i-CIDADE: QUESITO C1.1
                # ==========================================
                opcoes_c11 = {
                    "Selecione...": 0.0,
                    "Etapa A (10.0 pts)": 10.0,
                    "Etapa B (20.0 pts)": 20.0,
                    "Etapa C (50.0 pts)": 50.0,
                    "Não classificada (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="C1.1",
                    titulo="Estágio do Município no Programa MCR2030",
                    pergunta="O Município foi classificado em qual estágio do Programa Construindo Cidades Resilientes 2030 da ONU?",
                    opcoes=opcoes_c11,
                    placeholder_link="Insira o link para a certidão ou relatório de classificação da ONU/MCR2030...",
                    on_save_callback=render_conteudo.refresh,
                )

                # String de conexão com o PostgreSQL
                DATABASE_URL = "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

                # Dicionário global de Pontuações Máximas (Tetos) dos Quesitos
                PONTUACOES_MAX = {
                    "1.0": 10.0, "1.1": 10.0, "1.2": 10.0, "1.3": 10.0, "1.4": 10.0,
                    "2.0": 15.0, "3.0": 20.0, "3.1": 30.0, "4.0": 20.0, "4.2": 50.0,
                    "5.0": 25.0, "5.1": 40.0, "5.1.1": 100.0, "5.1.1.1": 20.0, "5.1.2": 50.0,
                    "5.2": 50.0, "6.0": 50.0, "7.0": 15.0, "7.1": 15.0, "7.2": 15.0,
                    "7.3": 15.0, "7.3.1": 25.0, "7.4": 15.0, "7.4.1": 25.0, "7.5": 20.0,
                    "7.6": 20.0, "8": 20.0, "8.0": 20.0, "8.1": 30.0, "8.1.1": 15.0,
                    "8.1.1.1": 15.0, "8.2": 20.0, "9.0": 30.0, "10": 100.0, "10.0": 100.0,
                    "11": 20.0, "11.0": 20.0, "11.1": 20.0, "11.2": 20.0, "11.2.1": 20.0,
                    "12.0": 25.0, "12.1.3": 50.0, "13": 20.0, "13.0": 20.0, "14.0": 50.0,
                    "15": 20.0, "15.0": 20.0, "16": 20.0, "16.0": 20.0, "C1.1": 50.0
                }

                def get_db_connection():
                    """Cria conexão segura com o Neon PostgreSQL."""
                    return psycopg2.connect(DATABASE_URL)

                def get_all_years_data():
                    """
                    Busca todas as respostas do banco agrupadas por ano com depuração avançada.
                    """
                    all_data = {}
                    try:
                        conn = get_db_connection()
                        cur = conn.cursor(cursor_factory=RealDictCursor)
                        cur.execute("SELECT * FROM respostas")
                        rows = cur.fetchall()
                        cur.close()
                        conn.close()

                        print(f"\n[DEBUG DATABASE] Total de linhas retornadas da tabela 'respostas': {len(rows)}")
                        
                        if rows:
                            # Imprime as chaves do primeiro registro para conferir o nome exato das colunas no terminal
                            print(f"[DEBUG DATABASE] Colunas disponíveis na tabela: {list(dict(rows[0]).keys())}")

                        for row in rows:
                            row_dict = dict(row)
                            
                            # 1. Tenta identificar o ANO procurando qualquer chave com nome parecido
                            raw_ano = None
                            for key, val in row_dict.items():
                                if key.lower() in ["ano", "exercicio", "data", "created_at", "data_criacao", "ano_exercicio", "criado_em"]:
                                    if val is not None:
                                        raw_ano = val
                                        break

                            ano = None
                            if isinstance(raw_ano, (int, float)):
                                ano = int(raw_ano)
                            elif isinstance(raw_ano, (date, datetime)):
                                ano = raw_ano.year
                            elif isinstance(raw_ano, str) and raw_ano.strip():
                                match = re.search(r'\b(20\d{2})\b', raw_ano)
                                if match:
                                    ano = int(match.group(1))

                            # Se não achou ano na coluna, define um fallback (ex: 2026) para os dados não sumirem
                            if not ano:
                                ano = 2026

                            if ano not in all_data:
                                all_data[ano] = {}

                            # 2. Processa o JSON ou as linhas individuais
                            dados_obj = None
                            for key in ["dados", "resposta_json", "dados_json", "conteudo", "respostas"]:
                                if key in row_dict and row_dict[key]:
                                    dados_obj = row_dict[key]
                                    break

                            if isinstance(dados_obj, str):
                                try:
                                    dados_obj = json.loads(dados_obj)
                                except Exception:
                                    dados_obj = None

                            if isinstance(dados_obj, dict):
                                all_data[ano].update(dados_obj)
                            else:
                                qid = str(
                                    row_dict.get("quesito_id") or 
                                    row_dict.get("qid") or 
                                    row_dict.get("quesito") or 
                                    row_dict.get("questao_id") or ""
                                ).strip()
                                
                                if qid:
                                    pts = float(row_dict.get("pontos") or row_dict.get("pontuacao") or row_dict.get("nota") or 0)
                                    val = str(row_dict.get("resposta") or row_dict.get("valor") or "")
                                    lnk = str(row_dict.get("link") or row_dict.get("evidencia") or "")
                                    all_data[ano][qid] = {"pontos": pts, "valor": val, "link": lnk}

                        print(f"[DEBUG DATABASE] Anos carregados com sucesso: {list(all_data.keys())}")
                        for a, conteudos in all_data.items():
                            print(f"[DEBUG DATABASE] -> Ano {a}: {len(conteudos)} quesitos encontrados.")

                    except Exception as e:
                        print(f"\n[ERRO BANCO DE DADOS] Falha ao consultar Neon PostgreSQL: {e}")
                        logging.exception("Erro detalhado no banco:")

                    return all_data

                # =============================================================================
                # 3. GERADOR DO RELATÓRIO PDF
                # =============================================================================

                def gerar_relatorio_pdf(dados, ano, total, faixa):
                    buffer = BytesIO()
                    doc = SimpleDocTemplate(
                        buffer, 
                        pagesize=A4, 
                        rightMargin=30, 
                        leftMargin=30, 
                        topMargin=30, 
                        bottomMargin=30
                    )
                    elements = []
                    styles = getSampleStyleSheet()

                    # -------------------------------------------------------------------------
                    # FOLHA 1: CAPA
                    # -------------------------------------------------------------------------
                    elements.append(Spacer(1, 100))
                    
                    logo_path = "iegm.png"
                    if os.path.exists(logo_path):
                        try:
                            logo = Image(logo_path, width=380, height=180)
                            logo.hAlign = 'CENTER'
                            elements.append(logo)
                        except Exception:
                            elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
                    else:
                        elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
                        
                    elements.append(Spacer(1, 50))
                    
                    style_titulo_capa = ParagraphStyle(
                        'TituloCapa', 
                        parent=styles['Normal'], 
                        fontName='Helvetica-Bold', 
                        fontSize=24, 
                        textColor=colors.HexColor("#2c3e50"), 
                        alignment=1
                    )

                    elements.append(Paragraph("Relatório I-Cidade", style_titulo_capa))
                    elements.append(Spacer(1, 15))
                    
                    style_ano_capa = ParagraphStyle(
                        'AnoCapa', 
                        parent=styles['Normal'], 
                        fontName='Helvetica', 
                        fontSize=16, 
                        textColor=colors.HexColor("#7f8c8d"), 
                        alignment=1
                    )
                    elements.append(Paragraph(str(ano), style_ano_capa))
                    elements.append(PageBreak())

                    # -------------------------------------------------------------------------
                    # FOLHA 2: SUMÁRIO
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>SUMÁRIO</b>", styles["h1"]))
                    elements.append(Spacer(1, 30))

                    style_item_esquerda = ParagraphStyle(
                        'ItemEsq', 
                        parent=styles['Normal'], 
                        fontName='Helvetica-Bold', 
                        fontSize=11, 
                        textColor=colors.HexColor("#2c3e50")
                    )
                    style_pag_direita = ParagraphStyle(
                        'PagDir', 
                        parent=styles['Normal'], 
                        fontName='Helvetica-Bold', 
                        fontSize=11, 
                        textColor=colors.HexColor("#1b4f72"), 
                        alignment=2
                    )

                    dados_sumario = [
                        [Paragraph("1. Resumo Executivo (Análise Comparativa)", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
                        [Paragraph("2. Análise de Desempenho por Quesito", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
                        [Paragraph("3. Análise de Impacto e Penalidades", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
                        [Paragraph("4. Diagnóstico de Reincidências", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
                        [Paragraph("5. Alinhamento com a Agenda 2030 (ODS)", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
                        [Paragraph("6. Série Histórica do I-cidade", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
                        [Paragraph("7. Quesitos Sem Pontuação Direta", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
                    ]
                    
                    tabela_sumario = Table(dados_sumario, colWidths=[400, 90])
                    tabela_sumario.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                        ('TOPPADDING', (0, 0), (-1, -1), 12),
                        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7"), 1, (2, 4)), 
                    ]))
                    elements.append(tabela_sumario)
                    elements.append(PageBreak())

                    # -------------------------------------------------------------------------
                    # 1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA DE EXERCÍCIOS)
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA)</b>", styles["h2"]))
                    elements.append(Spacer(1, 8))

                    nota_atual = float(total)
                    ano_atual = int(str(ano).strip()[:4])
                    ano_ant = ano_atual - 1

                    def converter_pontos_em_faixa_iegm(pontos):
                        pts = float(pontos)
                        if pts < 500.0:             return "C"
                        elif 500.0 <= pts <= 599.9:  return "C+"
                        elif 600.0 <= pts <= 749.9:  return "B"
                        elif 750.0 <= pts <= 899.9:  return "B+"
                        else:                        return "A"

                    all_data = {}
                    try:
                        all_data = get_all_years_data()
                    except Exception:
                        all_data = {}

                    dados_ano_anterior = all_data.get(ano_ant, {})
                    nota_anterior = 0.0
                    if ano_ant in all_data:
                        nota_anterior = float(sum(
                            info_ant.get("pontos", 0) 
                            for qid_ant, info_ant in dados_ano_anterior.items() 
                            if isinstance(info_ant, dict) and not qid_ant.startswith("COM_")
                        ))

                    faixa_anterior = converter_pontos_em_faixa_iegm(nota_anterior)
                    faixa_real_atual = faixa if faixa else converter_pontos_em_faixa_iegm(nota_atual)

                    variacao_pontos = nota_atual - nota_anterior
                    if nota_anterior > 0:
                        variacao_percentual = (variacao_pontos / nota_anterior) * 100
                        texto_percentual = f"{variacao_percentual:+.2f}%"
                    else:
                        texto_percentual = "0.00%"

                    if variacao_pontos > 0:
                        cor_variacao = colors.HexColor("#28a745")
                        seta_tendencia = "▲"
                    elif variacao_pontos < 0:
                        cor_variacao = colors.HexColor("#dc3545")
                        seta_tendencia = "▼"
                    else:
                        cor_variacao = colors.HexColor("#6c757d")
                        seta_tendencia = "■"

                    style_th = ParagraphStyle('Th', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.whitesmoke, alignment=1)
                    style_td_ano = ParagraphStyle('TdAno', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#2c3e50"), alignment=1)
                    style_td_pts = ParagraphStyle('TdPts', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1)
                    style_td_faixa = ParagraphStyle('TdFaixa', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor("#1b4f72"), alignment=1)
                    style_td_var = ParagraphStyle('TdVar', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=cor_variacao, alignment=1)

                    dados_comparativos = [
                        [Paragraph("Exercício", style_th), Paragraph("Pontuação Obtida", style_th), Paragraph("Faixa / Conceito", style_th), Paragraph("Variação Nominal", style_th), Paragraph("Variação Percentual", style_th)],
                        [Paragraph(str(ano_ant), style_td_ano), Paragraph(f"{nota_anterior:.1f} pts", style_td_pts), Paragraph(str(faixa_anterior), style_td_faixa), Paragraph("-", style_td_var), Paragraph("-", style_td_var)],
                        [Paragraph(str(ano_atual), style_td_ano), Paragraph(f"{nota_atual:.1f} pts", style_td_pts), Paragraph(str(faixa_real_atual), style_td_faixa), Paragraph(f"{seta_tendencia} {variacao_pontos:+.1f} pts", style_td_var), Paragraph(f"{seta_tendencia} {texto_percentual}", style_td_var)]
                    ]

                    tabela_comp = Table(dados_comparativos, colWidths=[80, 105, 95, 105, 105])
                    tabela_comp.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")), 
                        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8f9fa")), ("BACKGROUND", (0, 2), (-1, 2), colors.whitesmoke),          
                    ]))
                    elements.append(tabela_comp)
                    elements.append(Spacer(1, 12))

                    style_analise = ParagraphStyle('Analise', parent=styles['Normal'], fontSize=10, leading=14)
                    if variacao_pontos > 0:
                        texto_analise = f"<b>Análise de Tendência:</b> O município registrou uma evolução de desempenho com incremento de <b>{texto_percentual}</b> na sua pontuação global comparado ao exercício de {ano_ant}."
                    elif variacao_pontos < 0:
                        texto_analise = f"<b>Análise de Tendência:</b> <font color='#dc3545'><b>Alerta de Retrocesso:</b></font> Foi identificada uma redução de <b>{texto_percentual}</b> na eficiência dos indicadores em relação a {ano_ant}."
                    else:
                        texto_analise = f"<b>Análise de Tendência:</b> O município apresentou estagnação absoluta (0.00%) no seu índice geral de conformidade."

                    elements.append(Paragraph(texto_analise, style_analise))
                    elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 2. ANÁLISE DE DESEMPENHO POR QUESITO
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>2. ANÁLISE DE DESEMPENHO POR QUESITO</b>", styles["h2"]))
                    elements.append(Spacer(1, 6))

                    lista_pontos_fortes = []
                    lista_pontos_fracos = []
                    reincidencias_detectadas = []

                    for qid, info in dados.items():
                        if qid.startswith("COM_") or not isinstance(info, dict): continue
                        pts_obtidos = float(info.get("pontos", 0))
                        valor_resposta = info.get("valor", "")
                        link_evidencia = info.get("link", "")
                        pts_maximo = float(PONTUACOES_MAX.get(qid, 0))
                        
                        if pts_maximo > 0:
                            eficiencia = (pts_obtidos / pts_maximo) * 100
                            item_data = {"qid": qid, "pts_obtidos": pts_obtidos, "pts_maximo": pts_maximo, "eficiencia": eficiencia, "valor": valor_resposta, "link": link_evidencia}
                            if eficiencia >= 70.0: lista_pontos_fortes.append(item_data)
                            elif eficiencia < 50.0:
                                lista_pontos_fracos.append(item_data)
                                if qid in dados_ano_anterior:
                                    info_ant = dados_ano_anterior[qid]
                                    pts_anterior = float(info_ant.get("pontos", 0))
                                    if pts_obtidos == pts_anterior:
                                        reincidencias_detectadas.append({"qid": qid, "tipo": "Ponto Fraco", "detalhe": "Eficiência Crítica", "ant": f"{pts_anterior:.1f} pts", "atual": f"{pts_obtidos:.1f} pts"})

                    if lista_pontos_fortes:
                        elements.append(Paragraph("<b>✅ Pontos Fortes:</b>", styles["h3"]))
                        data_fortes = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
                        for item in sorted(lista_pontos_fortes, key=lambda x: x["pts_obtidos"], reverse=True):
                            evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
                            data_fortes.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
                        tabela_fortes = Table(data_fortes, colWidths=[65, 75, 65, 285])
                        tabela_fortes.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#28a745")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#28a745")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                        elements.append(tabela_fortes)
                        elements.append(Spacer(1, 12))

                    if lista_pontos_fracos:
                        elements.append(Paragraph("<b>⚠️ Pontos Fracos Geral:</b>", styles["h3"]))
                        data_fracos = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
                        for item in sorted(lista_pontos_fracos, key=lambda x: x["pts_obtidos"]):
                            evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
                            data_fracos.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
                        tabela_fracos = Table(data_fracos, colWidths=[65, 75, 65, 285])
                        tabela_fracos.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e67e22")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e67e22")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                        elements.append(tabela_fracos)
                        elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 3. ANÁLISE DE IMPACTO E PENALIDADES
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>3. ANÁLISE DE IMPACTO E PENALIDADES (EFICIÊNCIA PREVENTIVA)</b>", styles["h2"]))
                    elements.append(Spacer(1, 6))

                    PENALIDADES_MAX = {"4.2": -50.0, "5.1.1": -100.0, "5.2": -50.0, "6.0": -50.0, "10": -100.0, "10.0": -100.0, "11.1": -20.0, "11.2": -20.0, "11.2.1": -20.0, "12.1.3": -50.0, "14.0": -50.0}

                    lista_penalidades = []
                    for qid, pen_max in PENALIDADES_MAX.items():
                        if qid in dados:
                            info = dados[qid]
                            nota_real = float(info.get("pontos", 0))
                            nota_risco = nota_real if nota_real <= 0 else 0.0
                            eficiencia_preventiva = (1.0 - (nota_risco / pen_max)) * 100.0
                            lista_penalidades.append({"qid": qid, "nota_real": nota_real, "pen_max": pen_max, "eficiencia": eficiencia_preventiva, "valor": info.get("valor", ""), "link": info.get("link", "")})
                            if eficiencia_preventiva < 100.0 and qid in dados_ano_anterior:
                                info_ant = dados_ano_anterior[qid]
                                nota_real_ant = float(info_ant.get("pontos", 0))
                                if nota_real == nota_real_ant:
                                    reincidencias_detectadas.append({"qid": qid, "tipo": "Penalidade Aplicada", "detalhe": f"Impacto Recorrente de {nota_real:.1f} pts", "ant": f"{nota_real_ant:.1f} pts", "atual": f"{nota_real:.1f} pts"})

                    if lista_penalidades:
                        data_penalidades = [["Quesito", "Penalidade Aplicada", "Pior Cenário", "Eficiência Preventiva", "Status de Risco"]]
                        for item in sorted(lista_penalidades, key=lambda x: x["eficiencia"]):
                            nota_txt = f"{item['nota_real']:.1f} pts"; teto_txt = f"{item['pen_max']:.1f} pts"; ef_txt = f"{item['eficiencia']:.1f}%"
                            if item['eficiencia'] == 100.0: status = "<font color='#28a745'><b>Risco Mitigado</b></font>"
                            elif item['eficiencia'] <= 0.0: status = "<font color='#dc3545'><b>Impacto Máximo</b></font>"
                            else: status = "<font color='#ffc107'><b>Impacto Parcial</b></font>"
                            data_penalidades.append([item['qid'], nota_txt, teto_txt, ef_txt, Paragraph(status, styles["Normal"])])
                        tabela_pen = Table(data_penalidades, colWidths=[65, 110, 80, 115, 120])
                        tabela_pen.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b4f72")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#1b4f72")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
                        elements.append(tabela_pen)
                        elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 4. DIAGNÓSTICO DE REINCIDÊNCIAS 
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>4. DIAGNÓSTICO DE REINCIDÊNCIAS </b>", styles["h2"]))
                    elements.append(Spacer(1, 6))
                    if reincidencias_detectadas:
                        data_reinc = [["Quesito", "Origem da Falha", "Impacto Histórico", "Exercício Anterior", "Exercício Atual"]]
                        for reinc in reincidencias_detectadas: data_reinc.append([reinc["qid"], reinc["tipo"], Paragraph(f"<b>{reinc['detalhe']}</b>", styles["Normal"]), reinc["ant"], reinc["atual"]])
                        tabela_reinc = Table(data_reinc, colWidths=[65, 115, 170, 75, 65])
                        tabela_reinc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0392b")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
                        elements.append(tabela_reinc)
                    else: 
                        elements.append(Paragraph("<font color='#28a745'><b>Nenhuma reincidência ativa detectada.</b></font>", styles["Normal"]))
                    elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)</b>", styles["h2"]))
                    elements.append(Spacer(1, 6))
                    
                    def calcular_percentual_checklist(resposta_bruta, total_itens):
                        if not resposta_bruta: return 0.0
                        itens = [i.strip().lower() for i in str(resposta_bruta).split(",") if i.strip()]
                        itens_validos = [i for i in itens if "outros" not in i]
                        return min((len(itens_validos) / total_itens) * 100.0, 100.0) if total_itens > 0 else 0.0

                    analise_ods = []
                    for qid, info in dados.items():
                        if qid.startswith("COM_") or not isinstance(info, dict): continue
                        resp = str(info.get("valor", "")).strip(); resp_l = resp.lower(); metas = ""; status = ""
                        if qid == "1.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "1.4": metas = "11.5, 16.6"; status = "Não Atendido" if "não atuam de forma sistêmica" in resp_l else "Atendido"
                        elif qid == "2.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "3.0": metas = "11.5, 16.7, 16.10, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "3.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
                        elif qid == "4.0": metas = "1.5, 11.5, 11b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "5.0": metas = "1.5, 11.5, 16b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "5.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 8):.1f}% Atendido"
                        elif qid == "5.1.1": metas = "11b, 11.5, 16.6, 16.10"; status = "Atendido" if ("sim, integralmente" in resp_l or "sim, parcialmente" in resp_l) else "Não Atendido"
                        elif qid == "5.1.1.1": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "5.1.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if "não" in resp_l else "Não Atendido"
                        elif qid == "5.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if ("sim" in resp_l or "parcialmente" in resp_l) else "Não Atendido"
                        elif qid == "7.0": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "7.3": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "7.3.1": metas = "11b, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
                        elif qid == "7.4": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "7.4.1": metas = "11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
                        elif qid == "7.5": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
                        elif qid == "7.6": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
                        elif qid in ["8", "8.0"]: metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "8.1": metas = "1.5, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
                        elif qid == "8.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "8.1.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "8.2": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "9.0": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("todas as escolas" in resp_l or "maior parte" in resp_l) else "Não Atendido"
                        elif qid in ["10", "10.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid in ["11", "11.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "11.1": metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "12.0": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "12.1.3": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid in ["13", "13.0"]: metas = "11.2, 11.7, 12.5"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid == "14.0": metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid in ["15", "15.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
                        elif qid in ["16", "16.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"

                        if metas: analise_ods.append({"qid": qid, "status": status, "metas": metas, "resp": resp[:50]})

                    if analise_ods:
                        data_ods = [["Quesito", "Resposta Informada", "Vínculo Metas ODS", "Status de Cumprimento"]]
                        style_td_ods = ParagraphStyle('TdOds', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=1)
                        for item in sorted(analise_ods, key=lambda x: [float(i) if i.replace('.','',1).isdigit() else 999 for i in x['qid'].split('.')]):
                            st_txt = item["status"]
                            if "Não Atendido" in st_txt: st_p = Paragraph(f"<font color='#dc3545'><b>{st_txt}</b></font>", style_td_ods)
                            elif "Atendido" in st_txt and "%" not in st_txt: st_p = Paragraph(f"<font color='#28a745'><b>{st_txt}</b></font>", style_td_ods)
                            else: st_p = Paragraph(f"<font color='#007bff'><b>{st_txt}</b></font>", style_td_ods)
                            data_ods.append([item["qid"], Paragraph(item["resp"], styles["Normal"]), item["metas"], st_p])
                        tabela_ods = Table(data_ods, colWidths=[60, 200, 115, 110])
                        tabela_ods.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f9d58")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (0, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#0f9d58")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
                        elements.append(tabela_ods)
                        elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 6. SÉRIE HISTÓRICA DO I-CIDADE
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>6. SÉRIE HISTÓRICA DO I-CIDADE</b>", styles["h2"]))
                    elements.append(Spacer(1, 10))

                    anos_serie = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
                    valores_serie = []
                    for a in anos_serie:
                        if a == ano_atual: 
                            valores_serie.append(nota_atual)
                        elif a in all_data:
                            valores_serie.append(float(sum(info_h.get("pontos", 0) for qid_h, info_h in all_data[a].items() if isinstance(info_h, dict) and not qid_h.startswith("COM_"))))
                        else: 
                            valores_serie.append(0.0)

                    # Configuração do Gráfico
                    desenho_grafico = Drawing(480, 165)
                    bc = VerticalBarChart()
                    bc.x = 45; bc.y = 25; bc.height = 110; bc.width = 410
                    bc.data = [valores_serie]
                    bc.categoryAxis.categoryNames = [str(a) for a in anos_serie]
                    bc.categoryAxis.labels.fontSize = 9; bc.categoryAxis.labels.fontName = 'Helvetica-Bold'; bc.categoryAxis.labels.dy = -10
                    
                    bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = 1000; bc.valueAxis.valueStep = 200; bc.valueAxis.labels.fontSize = 8
                    
                    # Rótulos (Pontuação em cima da barra)
                    bc.barLabels.nudge = 8
                    bc.barLabels.fontSize = 8
                    bc.barLabels.fontName = 'Helvetica-Bold'
                    bc.barLabelFormat = '%.1f'
                    
                    bc.bars[0].fillColor = colors.HexColor("#1b4f72")
                    bc.bars[0].strokeColor = colors.HexColor("#2c3e50")
                    bc.bars[0].strokeWidth = 0.5

                    desenho_grafico.add(String(240, 150, "Série Histórica do I-cidade", textAnchor='middle', fontName='Helvetica-Bold', fontSize=12, fillColor=colors.HexColor("#2c3e50")))
                    desenho_grafico.add(bc)
                    
                    elements.append(desenho_grafico)
                    elements.append(Spacer(1, 15))

                    # -------------------------------------------------------------------------
                    # 7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)
                    # -------------------------------------------------------------------------
                    elements.append(Paragraph("<b>7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)</b>", styles["h2"]))
                    elements.append(Spacer(1, 6))

                    lista_alvo_sp = [
                        "4.0", "11.0", "12.0", "13.0", "4.1", "5.1", 
                        "5.1.2", "5.1.2.1", "7.3.1", "8.4.1", "8.1", "8.1.1", "12.1.3.1", "14.1"
                    ]

                    analise_sp = []
                    
                    for qid in lista_alvo_sp:
                        info = dados.get(qid) or dados.get(f"Q_{qid}") or {}
                        
                        if isinstance(info, dict):
                            resp = str(info.get("valor", "")).strip()
                        else:
                            resp = str(info).strip()

                        resp_l = resp.lower()
                        is_adequado = False

                        if qid in ["4.0", "11.0", "12.0", "13.0", "8.1.1"]:
                            if any(x == resp_l or x in resp_l for x in ["sim", "1", "s", "true", "adequado"]):
                                is_adequado = True

                        elif qid == "4.1":
                            opcoes = ["riscos geológicos", "riscos hidrológicos", "riscos meteorológicos", "riscos biológicos"]
                            if any(opt in resp_l for opt in opcoes):
                                is_adequado = True

                        elif qid == "5.1":
                            opcoes = ["epidemias", "estiagem", "incêndios", "ondas de calor ou ondas de frio", "inundações"]
                            if any(opt in resp_l for opt in opcoes):
                                is_adequado = True

                        elif qid == "5.1.2":
                            if any(x == resp_l or x in resp_l for x in ["não", "nao", "0", "n", "false"]):
                                is_adequado = True

                        elif qid == "5.1.2.1":
                            opcoes = ["tecnologias corporativas", "plano de continuidade de negócios", "mecanismo de redundância de dados", "treinamento contínuo das equipes"]
                            if any(opt in resp_l for opt in opcoes):
                                is_adequado = True

                        elif qid == "7.3.1":
                            opcoes = ["alertas por SMS", "sirenes", "redes sociais", "comunicação por rádio", "carro de som"]
                            if any(opt in resp_l for opt in opcoes):
                                is_adequado = True

                        elif qid == "8.4.1":
                            if any(x == resp_l or x in resp_l for x in ["sim", "parcialmente"]):
                                is_adequado = True

                        elif qid == "8.1":
                            opcoes = ["abrigos temporários", "rotas de fuga", "mapeamento de vulnerabilidades", "sistema de resgate"]
                            if any(opt in resp_l for opt in opcoes):
                                is_adequado = True

                        elif qid == "12.1.3.1":
                            if any(x == resp_l or x in resp_l for x in ["sim", "em andamento"]):
                                is_adequado = True

                        elif qid == "14.1":
                            if any(x == resp_l or x in resp_l for x in ["sim", "integralmente"]):
                                is_adequado = True

                        status_sp = "Adequado" if is_adequado else "Inadequado"
                        analise_sp.append({"qid": qid, "resp": resp if resp else "Sem Resposta", "status": status_sp})

                    if analise_sp:
                        data_sp = [["Quesito", "Resposta Informada", "Avaliação Operacional"]]
                        style_td_sp = ParagraphStyle('TdSp', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=1)
                        for item in analise_sp:
                            if item["status"] == "Adequado":
                                st_p = Paragraph("<font color='#28a745'><b>Conforme / Adequado</b></font>", style_td_sp)
                            else:
                                st_p = Paragraph("<font color='#dc3545'><b>Inconforme / Inadequado</b></font>", style_td_sp)
                            data_sp.append([item["qid"], Paragraph(item["resp"], styles["Normal"]), st_p])

                        tabela_sp = Table(data_sp, colWidths=[70, 260, 155])
                        tabela_sp.setStyle(TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                            ("ALIGN", (0, 0), (0, -1), "CENTER"),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#34495e")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
                        ]))
                        elements.append(tabela_sp)

                    # -------------------------------------------------------------------------
                    # CONSTRUÇÃO DO DOCUMENTO PDF (COM NÚMERO DE PÁGINAS)
                    # -------------------------------------------------------------------------
                    def adicionar_rodape(canvas, doc):
                        if doc.page > 1:
                            canvas.saveState()
                            canvas.setFont('Helvetica', 8)
                            canvas.setFillColor(colors.HexColor("#7f8c8d"))
                            canvas.drawString(30, 20, f"Relatório I-Cidade — Exercício {ano}")
                            canvas.drawRightString(A4[0] - 30, 20, f"Página {doc.page}")
                            canvas.setStrokeColor(colors.HexColor("#bdc3c7"))
                            canvas.setLineWidth(0.5)
                            canvas.line(30, 32, A4[0] - 30, 32)
                            canvas.restoreState()

                    doc.build(elements, onFirstPage=adicionar_rodape, onLaterPages=adicionar_rodape)
                    
                    pdf = buffer.getvalue()
                    buffer.close()
                    return pdf

                # =============================================================================
                # CARD DE EMISSÃO DO RELATÓRIO PDF (INTERFACE NICEGUI)
                # =============================================================================
                with ui.card().classes('w-full p-6 my-6 border border-blue-200 rounded-lg shadow-sm bg-blue-50'):
                    ui.label("📄 Emissão de Relatório Analítico - iCidade").classes("text-xl font-bold text-blue-900 mb-1")
                    ui.label("Gere o relatório completo em formato PDF contendo análises de tendência, diagnóstico de reincidências e metas ODS da Agenda 2030.").classes("text-sm text-gray-700 mb-4")

                    async def emitir_pdf(ano_base: int):
                        n = ui.notify(f"Gerando PDF ({ano_base} vs {ano_base - 1}), aguarde...", type="info", timeout=0)
                        await ui.run_javascript('new Promise(resolve => setTimeout(resolve, 100))')

                        try:
                            # Tenta carregar dados específicos do ano base se não forem os da tela atual
                            all_data = get_all_years_data()
                            dados_ano = all_data.get(ano_base, res_data)

                            total_pts = float(sum(
                                v.get("pontos", 0) 
                                for k, v in dados_ano.items() 
                                if isinstance(v, dict) and not str(k).startswith("COM_")
                            ))

                            if total_pts < 500.0: faixa = "C"
                            elif total_pts < 600.0: faixa = "C+"
                            elif total_pts < 750.0: faixa = "B"
                            elif total_pts < 900.0: faixa = "B+"
                            else: faixa = "A"

                            pdf_bytes = gerar_relatorio_pdf(
                                dados=dados_ano,
                                ano=ano_base,
                                total=total_pts,
                                faixa=faixa
                            )

                            rota_pdf = f"/relatorio_temp_{ano_base}_{ano_base - 1}.pdf"
                            
                            @app.get(rota_pdf)
                            def relatorio_endpoint():
                                from fastapi import Response
                                return Response(content=pdf_bytes, media_type="application/pdf")

                            ui.run_javascript(f"window.open('{rota_pdf}', '_blank');")

                            n.dismiss()
                            ui.notify(f"Relatório {ano_base}-{ano_base - 1} aberto com sucesso!", type="positive")

                        except Exception as e:
                            n.dismiss()
                            print(f"ERRO CRÍTICO AO GERAR PDF: {e}")
                            logging.exception("Erro no PDF:")
                            ui.notify(f"Erro ao gerar o PDF ({ano_base}-{ano_base - 1}): {e}", type="negative", close_button=True)

                    # Botões lado a lado para selecionar o relatório comparativo desejado
                    with ui.row().classes('gap-4 my-2'):
                        ui.button("📥 RELATÓRIO 2025 vs 2024", on_click=lambda: emitir_pdf(2025)).classes("bg-blue-700 text-white font-bold")
                        ui.button("📥 RELATÓRIO 2026 vs 2025", on_click=lambda: emitir_pdf(2026)).classes("bg-indigo-700 text-white font-bold")

    render_conteudo()
