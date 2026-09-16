import base64
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

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
                    CREATE TABLE IF NOT EXISTS respostas_iamb (
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
        print(f"❌ Erro ao inicializar tabela respostas_iamb: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iamb
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
        INSERT INTO respostas_iamb (ano, qid, valor, pontos, link, comentarios, status)
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
    query = "DELETE FROM respostas_iamb WHERE ano = %s;"
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
# FUNÇÃO AUXILIAR DE RENDERIZAÇÃO DE QUESITOS (PADRÃO)
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes,
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    dados_q = res_data.get(qid, {})
    valor_atual = dados_q.get("valor", "Selecione...")
    if valor_atual not in opcoes:
        valor_atual = "Selecione..."

    link_atual = dados_q.get("link", "")

    state = {
        "opcao": valor_atual,
        "link": link_atual,
    }

    with ui.card().classes(
        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
    ):
        ui.label(f"{qid} • {titulo}").classes(
            "text-xl font-semibold text-blue-500 mb-3"
        )
        ui.label(pergunta).classes("text-base font-bold text-black mb-1")
        ui.label(
            "ℹ Preencha os campos abaixo e clique no botão de salvar."
        ).classes("text-xs text-gray-400 mb-6")

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

        def salvar_acao():
            opcao_sel = state["opcao"]
            pts = opcoes.get(opcao_sel, 0.0)
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
                on_save_callback()

        ui.button(
            f"💾 SALVAR QUESITO {qid}", on_click=salvar_acao
        ).classes(
            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
        )

        ui.separator().classes("my-2")
        bloco_comentarios(qid, res_data, on_save_callback)


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
        ui.label("🛠️ Painel de Controle (iAmb)").classes(
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
# MÓDULO PRINCIPAL DE REQUISITOS
# =============================================================================
def container_formulario_iamb(ano=None):
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

        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-12 gap-6 items-start"
        ):

            # Coluna 1: Painel Lateral (3/12)
            with ui.element("div").classes("md:col-span-4 lg:col-span-3"):
                render_painel_controle(
                    ano_atual=ano_sel,
                    on_mudar_ano=alterar_ano,
                    on_refresh=render_conteudo.refresh,
                )

            # Coluna 2: Formulário (9/12)
            with ui.element("div").classes(
                "md:col-span-8 lg:col-span-9 bg-white p-6 border rounded-lg shadow-sm"
            ):
                ui.label(f"📋 Módulo i-Amb — Ano {ano_sel}").classes(
                    "text-xl font-bold mb-4 text-slate-800 border-b pb-2"
                )

                # QUESITO 1.0
                opcoes_10 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.0",
                    titulo="Estrutura Organizacional de Meio Ambiente",
                    pergunta="A prefeitura possui alguma estrutura organizacional para tratar de assuntos ligados ao Meio Ambiente Municipal?",
                    opcoes=opcoes_10,
                    placeholder_link="Insira o link da lei da estrutura administrativa ou organograma...",
                    on_save_callback=render_conteudo.refresh,
                )

                # QUESITO 1.1.1
                with ui.card().classes(
                    "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                ):
                    ui.label(
                        "1.1.1 • Detalhamento do Quantitativo de Pessoal"
                    ).classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label(
                        "Informe o quantitativo de servidores por categoria:"
                    ).classes("text-base font-bold text-black mb-1")
                    ui.label(
                        "ℹ Preencha os campos abaixo e clique no botão de salvar."
                    ).classes("text-xs text-gray-400 mb-6")

                    d111 = res_data.get("1.1.1") or {}
                    raw_link = str(d111.get("link") or "")

                    v_efet_i, v_comi_i, v_terc_i = 0, 0, 0
                    evidencia_111 = raw_link

                    if "|LINK:" in raw_link:
                        contadores_part, evidencia_111 = raw_link.split(
                            "|LINK:", 1
                        )
                        match_e = re.search(r"E:(\d+)", contadores_part)
                        match_co = re.search(r"Co:(\d+)", contadores_part)
                        match_t = re.search(r"T:(\d+)", contadores_part)

                        v_efet_i = int(match_e.group(1)) if match_e else 0
                        v_comi_i = int(match_co.group(1)) if match_co else 0
                        v_terc_i = int(match_t.group(1)) if match_t else 0

                    state_111 = {
                        "efet": v_efet_i,
                        "comi": v_comi_i,
                        "terc": v_terc_i,
                        "link": evidencia_111,
                    }

                    with ui.grid(columns=2).classes(
                        "w-full gap-6 items-start mb-4"
                    ):
                        with ui.column().classes("w-full gap-3"):
                            ui.number(
                                "Nº de efetivos:", value=v_efet_i, min=0, step=1
                            ).classes("w-full").props("outlined").bind_value(
                                state_111, "efet"
                            )
                            ui.number(
                                "Nº de comissionados:",
                                value=v_comi_i,
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined").bind_value(
                                state_111, "comi"
                            )
                            ui.number(
                                "Nº de terceirizados/contratados:",
                                value=v_terc_i,
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined").bind_value(
                                state_111, "terc"
                            )

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=evidencia_111,
                            placeholder="Insira a folha de pagamento simplificada, relatório de RH ou declaração...",
                        ).classes("w-full").props("outlined rows=6").bind_value(
                            state_111, "link"
                        )

                    def salvar_111():
                        ef_val = int(state_111["efet"] or 0)
                        co_val = int(state_111["comi"] or 0)
                        te_val = int(state_111["terc"] or 0)
                        total = ef_val + co_val + te_val
                        composite = f"E:{ef_val},Co:{co_val},T:{te_val}|LINK:{state_111['link']}"

                        save_resposta(
                            ano=ano_sel,
                            qid="1.1.1",
                            valor=str(total),
                            pontos=0.0,
                            link=composite,
                            comentarios=d111.get("comentarios", []),
                            status=d111.get("status", "Pendente"),
                        )
                        ui.notify(
                            "Quesito 1.1.1 salvo com sucesso!", type="positive"
                        )
                        if render_conteudo.refresh:
                            render_conteudo.refresh()

                    ui.button(
                        "💾 SALVAR QUESITO 1.1.1", on_click=salvar_111
                    ).classes(
                        "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                    )

                    ui.separator().classes("my-2")
                    bloco_comentarios(
                        "1.1.1", res_data, render_conteudo.refresh
                    )

                # =============================================================================
                # QUESITO 1.1.2 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_112 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.1.2",
                    titulo="Treinamento dos Servidores de Meio Ambiente",
                    pergunta=f"Os servidores responsáveis pelo Meio Ambiente receberam treinamento específico voltado ao Meio Ambiente em {ano_sel}?",
                    opcoes=opcoes_112,
                    placeholder_link="Insira o link do certificado, lista de presença ou relatório do treinamento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 1.1.3 (Seleção Múltipla - Checkboxes)
                # =============================================================================
                with ui.card().classes(
                    "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                ):
                    ui.label(
                        "1.1.3 • Público do Treinamento/Cursos em Educação Ambiental"
                    ).classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label(
                        "A Secretaria Municipal de Meio Ambiente ou similar ofereceu cursos/treinamento sobre educação ambiental para qual público?"
                    ).classes("text-base font-bold text-black mb-1")
                    ui.label(
                        "ℹ Selecione todas as opções aplicáveis e clique no botão de salvar."
                    ).classes("text-xs text-gray-400 mb-6")

                    # Recupera dados salvos
                    d113 = res_data.get("1.1.3") or {}
                    
                    # Trata o valor recuperado (pode ser uma lista em formato string JSON ou legada)
                    try:
                        selecionados_salvos = json.loads(d113.get("valor", "[]"))
                        if not isinstance(selecionados_salvos, list):
                            selecionados_salvos = []
                    except Exception:
                        selecionados_salvos = []

                    mapa_opcoes_113 = {
                        "escolas": ("Para escolas (+5 pts)", 5.0),
                        "secretarias": ("Para outras secretarias / entidades municipais (+2 pts)", 2.0),
                        "municipes": ("Para munícipes ou empresas (+3 pts)", 3.0),
                        "nenhum": ("Não ofereceu nenhum curso/treinamento no ano (0 pts)", 0.0),
                    }

                    state_113 = {
                        "escolas": "escolas" in selecionados_salvos,
                        "secretarias": "secretarias" in selecionados_salvos,
                        "municipes": "municipes" in selecionados_salvos,
                        "nenhum": "nenhum" in selecionados_salvos or not selecionados_salvos,
                        "link": d113.get("link", ""),
                    }

                    # Cálculo dinâmico da pontuação
                    def calcular_pts_113():
                        if state_113["nenhum"]:
                            return 0.0
                        pts = 0.0
                        if state_113["escolas"]: pts += 5.0
                        if state_113["secretarias"]: pts += 2.0
                        if state_113["municipes"]: pts += 3.0
                        return pts

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        # Coluna da esquerda: Checkboxes com regras de dependência
                        with ui.column().classes("w-full gap-2"):
                            cb_escolas = ui.checkbox(mapa_opcoes_113["escolas"][0]).bind_value(state_113, "escolas")
                            cb_sec = ui.checkbox(mapa_opcoes_113["secretarias"][0]).bind_value(state_113, "secretarias")
                            cb_mun = ui.checkbox(mapa_opcoes_113["municipes"][0]).bind_value(state_113, "municipes")
                            cb_nenhum = ui.checkbox(mapa_opcoes_113["nenhum"][0]).bind_value(state_113, "nenhum")

                        # Coluna da direita: Link de evidência
                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_113["link"],
                            placeholder="Insira o link das fotos, listas de presença, certificados ou materiais dos cursos...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_113, "link")

                    label_pts_113 = ui.label(
                        f"📊 Impacto de Pontuação no Quesito 1.1.3: {calcular_pts_113():.1f} pontos"
                    ).classes("text-sm font-bold text-green-600 my-4")

                    def atualizar_estado_113(fonte):
                        if fonte == "nenhum" and state_113["nenhum"]:
                            state_113["escolas"] = False
                            state_113["secretarias"] = False
                            state_113["municipes"] = False
                        elif fonte in ["escolas", "secretarias", "municipes"] and state_113[fonte]:
                            state_113["nenhum"] = False

                        label_pts_113.set_text(
                            f"📊 Impacto de Pontuação no Quesito 1.1.3: {calcular_pts_113():.1f} pontos"
                        )

                    cb_escolas.on("update:model-value", lambda: atualizar_estado_113("escolas"))
                    cb_sec.on("update:model-value", lambda: atualizar_estado_113("secretarias"))
                    cb_mun.on("update:model-value", lambda: atualizar_estado_113("municipes"))
                    cb_nenhum.on("update:model-value", lambda: atualizar_estado_113("nenhum"))

                    def salvar_113():
                        selecionados = []
                        if state_113["nenhum"]:
                            selecionados = ["nenhum"]
                        else:
                            if state_113["escolas"]: selecionados.append("escolas")
                            if state_113["secretarias"]: selecionados.append("secretarias")
                            if state_113["municipes"]: selecionados.append("municipes")
                            if not selecionados: selecionados = ["nenhum"]

                        pts_finais = calcular_pts_113()

                        save_resposta(
                            ano=ano_sel,
                            qid="1.1.3",
                            valor=json.dumps(selecionados),
                            pontos=pts_finais,
                            link=state_113["link"],
                            comentarios=d113.get("comentarios", []),
                            status=d113.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 1.1.3 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh:
                            render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 1.1.3", on_click=salvar_113).classes(
                        "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                    )

                    ui.separator().classes("my-2")
                    bloco_comentarios("1.1.3", res_data, render_conteudo.refresh)

               # =============================================================================
                # QUESITO 1.2 (Seleção Múltipla - Checkboxes)
                # =============================================================================
                with ui.card().classes(
                    "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                ):
                    ui.label(
                        "1.2 • Recursos Disponibilizados para Meio Ambiente"
                    ).classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label(
                        "Assinale os recursos disponibilizados para a operacionalização das atividades de meio ambiente (Não considerar Recursos Humanos e Estrutura Física):"
                    ).classes("text-base font-bold text-black mb-1")
                    ui.label(
                        "ℹ Selecione as opções aplicáveis e clique no botão de salvar."
                    ).classes("text-xs text-gray-400 mb-6")

                    d12 = res_data.get("1.2") or {}
                    try:
                        sel_12_salvos = json.loads(d12.get("valor", "[]"))
                        if not isinstance(sel_12_salvos, list):
                            sel_12_salvos = []
                    except Exception:
                        sel_12_salvos = []

                    state_12 = {
                        "tec": "tec" in sel_12_salvos,
                        "orc": "orc" in sel_12_salvos,
                        "mat": "mat" in sel_12_salvos,
                        "out": "out" in sel_12_salvos,
                        "link": d12.get("link", ""),
                    }

                    def calc_pts_12():
                        pts = 0.0
                        if state_12["tec"]: pts += 5.0
                        if state_12["orc"]: pts += 5.0
                        if state_12["mat"]: pts += 5.0
                        if state_12["out"]: pts += 5.0
                        return pts

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-2"):
                            cb_tec = ui.checkbox("Recursos Tecnológicos (+5 pts)").bind_value(state_12, "tec")
                            cb_orc = ui.checkbox("Recursos Orçamentários (+5 pts)").bind_value(state_12, "orc")
                            cb_mat = ui.checkbox("Recursos Materiais (+5 pts)").bind_value(state_12, "mat")
                            cb_out = ui.checkbox("Outros (+5 pts)").bind_value(state_12, "out")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_12["link"],
                            placeholder="Insira notas fiscais, extratos orçamentários, inventário de materiais...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_12, "link")

                    lbl_pts_12 = ui.label(
                        f"📊 Impacto de Pontuação no Quesito 1.2: {calc_pts_12():.1f} pontos"
                    ).classes("text-sm font-bold text-green-600 my-4")

                    def att_pts_12():
                        lbl_pts_12.set_text(f"📊 Impacto de Pontuação no Quesito 1.2: {calc_pts_12():.1f} pontos")

                    cb_tec.on("update:model-value", att_pts_12)
                    cb_orc.on("update:model-value", att_pts_12)
                    cb_mat.on("update:model-value", att_pts_12)
                    cb_out.on("update:model-value", att_pts_12)

                    def salvar_12():
                        selecionados = [k for k, v in state_12.items() if k != "link" and v]
                        save_resposta(
                            ano=ano_sel,
                            qid="1.2",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_12(),
                            link=state_12["link"],
                            comentarios=d12.get("comentarios", []),
                            status=d12.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 1.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 1.2", on_click=salvar_12).classes(
                        "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                    )
                    ui.separator().classes("my-2")
                    bloco_comentarios("1.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 2.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_20 = {
                    "Selecione...": 0.0,
                    "Sim – 10 pts": 10.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.0",
                    titulo="Programa de Educação Ambiental",
                    pergunta="O Município participa de algum Programa de Educação Ambiental?",
                    opcoes=opcoes_20,
                    placeholder_link="Insira o link da lei, decreto, convênio ou projeto do programa...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 2.1 (Cálculo Proporcional - Fórmula Pmáx=50)
                # =============================================================================
                with ui.card().classes(
                    "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                ):
                    ui.label(
                        "2.1 • Ação de Educação Ambiental na Rede Escolar Municipal"
                    ).classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label(
                        "Informe o número de escolas dos Anos Iniciais (1º ao 5º ano) que adotam programa ou ação de educação ambiental e o total de escolas:"
                    ).classes("text-base font-bold text-black mb-1")
                    ui.label(
                        "ℹ O cálculo é automático: N = (Escolas com Programa / Total Escolas) × 50 pts."
                    ).classes("text-xs text-gray-400 mb-6")

                    d21 = res_data.get("2.1") or {}
                    raw_link_21 = str(d21.get("link") or "")
                    
                    n_com_prog_i, n_total_esc_i = 0, 0
                    evidencia_21 = raw_link_21

                    if "|LINK:" in raw_link_21:
                        partes_21, evidencia_21 = raw_link_21.split("|LINK:", 1)
                        match_prog = re.search(r"PROG:(\d+)", partes_21)
                        match_tot = re.search(r"TOT:(\d+)", partes_21)
                        n_com_prog_i = int(match_prog.group(1)) if match_prog else 0
                        n_total_esc_i = int(match_tot.group(1)) if match_tot else 0

                    state_21 = {
                        "prog": n_com_prog_i,
                        "total": n_total_esc_i,
                        "link": evidencia_21,
                    }

                    def calc_pts_21():
                        tot = int(state_21["total"] or 0)
                        prog = int(state_21["prog"] or 0)
                        if tot <= 0 or prog <= 0:
                            return 0.0
                        prop = min(prog / tot, 1.0) # Limita a 100%
                        return prop * 50.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            inp_prog = ui.number(
                                "Escolas COM programa (Anos Iniciais):",
                                value=n_com_prog_i,
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined").bind_value(state_21, "prog")

                            inp_tot = ui.number(
                                "TOTAL de escolas dos Anos Iniciais (i-Educ = E3.3):",
                                value=n_total_esc_i,
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined").bind_value(state_21, "total")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=evidencia_21,
                            placeholder="Insira a relação de escolas, projetos pedagógicos ou declaração da Secretaria de Educação...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_21, "link")

                    lbl_pts_21 = ui.label(
                        f"📊 Impacto de Pontuação no Quesito 2.1: {calc_pts_21():.1f} / 50.0 pontos"
                    ).classes("text-sm font-bold text-green-600 my-4")

                    def att_pts_21():
                        lbl_pts_21.set_text(
                            f"📊 Impacto de Pontuação no Quesito 2.1: {calc_pts_21():.1f} / 50.0 pontos"
                        )

                    inp_prog.on("update:model-value", att_pts_21)
                    inp_tot.on("update:model-value", att_pts_21)

                    def salvar_21():
                        p_val = int(state_21["prog"] or 0)
                        t_val = int(state_21["total"] or 0)
                        pts_finais = calc_pts_21()
                        composite = f"PROG:{p_val},TOT:{t_val}|LINK:{state_21['link']}"

                        save_resposta(
                            ano=ano_sel,
                            qid="2.1",
                            valor=f"{p_val}/{t_val}",
                            pontos=pts_finais,
                            link=composite,
                            comentarios=d21.get("comentarios", []),
                            status=d21.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 2.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 2.1", on_click=salvar_21).classes(
                        "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                    )
                    ui.separator().classes("my-2")
                    bloco_comentarios("2.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 3.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_30 = {
                    "Selecione...": 0.0,
                    "Sim, para todos os órgãos e entidades – 10 pts": 10.0,
                    "Parcialmente – 03 pts": 3.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.0",
                    titulo="Uso Racional de Recursos Naturais nos Órgãos Públicos",
                    pergunta="A prefeitura municipal estimula entre seus órgãos e entidades de sua responsabilidade projetos e/ou ações que promovam o uso racional de recursos naturais? (Ex.: implantação de dispositivos para uso racional da água, coleta seletiva, reuso/reciclagem)",
                    opcoes=opcoes_30,
                    placeholder_link="Insira o link das portarias, fotos das instalações, comprovantes de coleta seletiva nos prédios públicos...",
                    on_save_callback=render_conteudo.refresh,
                )

    # =============================================================================
                # QUESITO 3.1 (Seleção Múltipla - Checkboxes com Pontuação Fracionada)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("3.1 • Tipos de Ações para Uso Racional de Recursos Naturais").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais tipos de ações são realizadas pela Prefeitura:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as ações aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d31 = res_data.get("3.1") or {}
                    try:
                        sel_31_salvos = json.loads(d31.get("valor", "[]"))
                        if not isinstance(sel_31_salvos, list): sel_31_salvos = []
                    except Exception:
                        sel_31_salvos = []

                    mapa_31 = {
                        "coleta": ("Coleta seletiva (+1,5 pts)", 1.5),
                        "agua": ("Uso racional da água (+1,5 pts)", 1.5),
                        "energia": ("Uso racional de energia elétrica (+1,5 pts)", 1.5),
                        "reuso_mat": ("Reúso de materiais (+1,5 pts)", 1.5),
                        "horta": ("Horta coletiva (+1,5 pts)", 1.5),
                        "compostagem": ("Compostagem (+1,5 pts)", 1.5),
                        "bicicletarios": ("Instalação de bicicletários e vestiários (+1,5 pts)", 1.5),
                        "caixas_acopladas": ("Caixas acopladas nos vasos sanitários (+1,5 pts)", 1.5),
                        "led": ("Substituição de lâmpadas por LED (+1,5 pts)", 1.5),
                        "chuva": ("Captura de água de chuva (+1,5 pts)", 1.5),
                        "torneiras": ("Torneiras com redutores de pressão (+1,5 pts)", 1.5),
                        "descartaveis": ("Substituição de material descartável (+1,5 pts)", 1.5),
                        "logistica_reversa": ("Logística reversa (pilhas/baterias/eletrônicos) (+1,5 pts)", 1.5),
                        "outros": ("Outros (+0,5 pts)", 0.5),
                    }

                    state_31 = {k: k in sel_31_salvos for k in mapa_31.keys()}
                    state_31["link"] = d31.get("link", "")

                    def calc_pts_31():
                        pts = sum(peso for k, (_, peso) in mapa_31.items() if state_31.get(k))
                        return min(pts, 20.0)

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col = [ui.checkbox(rotulo).bind_value(state_31, k) for k, (rotulo, _) in list(mapa_31.items())[:7]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col2 = [ui.checkbox(rotulo).bind_value(state_31, k) for k, (rotulo, _) in list(mapa_31.items())[7:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_31["link"],
                        placeholder="Insira relatórios, ordens de serviço, fotos das instalações...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_31, "link")

                    lbl_pts_31 = ui.label(f"📊 Impacto de Pontuação no Quesito 3.1: {calc_pts_31():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_31():
                        lbl_pts_31.set_text(f"📊 Impacto de Pontuação no Quesito 3.1: {calc_pts_31():.1f} pontos")

                    for cb in cb_col + cb_col2:
                        cb.on("update:model-value", att_pts_31)

                    def salvar_31():
                        selecionados = [k for k in mapa_31.keys() if state_31.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_31(),
                            link=state_31["link"],
                            comentarios=d31.get("comentarios", []),
                            status=d31.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 3.1", on_click=salvar_31).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("3.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 4.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_40 = {
                    "Selecione...": 0.0,
                    "Sim, com medição da densidade colorimétrica da Escala Ringelmann ou equivalente – 20 pts": 20.0,
                    "Sim, através de outra forma de medição – 15 pts": 15.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.0",
                    titulo="Fiscalização de Emissão de Poluentes na Frota",
                    pergunta="O município fiscalizou a emissão de poluentes de combustíveis fósseis (diesel) na frota da Prefeitura Municipal?",
                    opcoes=opcoes_40,
                    placeholder_link="Insira laudos de medição, relatórios de medição de opacidade ou certificados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 5.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_50 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.0",
                    titulo="Contrato de Prestação de Serviço de Poda e Corte de Árvores",
                    pergunta="A Prefeitura Municipal possui contrato de prestação de serviço de poda e corte de árvores, arbustos e outras plantas lenhosas em áreas urbanas?",
                    opcoes=opcoes_50,
                    placeholder_link="Insira o link do contrato administrativo ou termo de homologação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 5.1 (Campos de Texto para Informações do Contrato)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("5.1 • Detalhamento do Contrato de Poda/Corte").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o número do contrato e o prestador de serviço:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Preencha os dados e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d51 = res_data.get("5.1") or {}
                    raw_val_51 = str(d51.get("valor") or "")
                    
                    num_contrato_i, prestador_i = "", ""
                    if "|PRESTADOR:" in raw_val_51:
                        num_contrato_i, prestador_i = raw_val_51.split("|PRESTADOR:", 1)

                    state_51 = {
                        "contrato": num_contrato_i,
                        "prestador": prestador_i,
                        "link": d51.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input("Número do Contrato:", value=num_contrato_i, placeholder="Ex: Contrato nº 042/2024").classes("w-full").props("outlined").bind_value(state_51, "contrato")
                            ui.input("Prestador de Serviço (Razão Social / CNPJ):", value=prestador_i, placeholder="Ex: Empresa X Ltda - CNPJ 00.000.000/0001-00").classes("w-full").props("outlined").bind_value(state_51, "prestador")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_51["link"],
                            placeholder="Insira o link da publicação do extrato no Diário Oficial ou cópia do contrato...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_51, "link")

                    def salvar_51():
                        composite_val = f"{state_51['contrato']}|PRESTADOR:{state_51['prestador']}"
                        save_resposta(
                            ano=ano_sel,
                            qid="5.1",
                            valor=composite_val,
                            pontos=0.0,
                            link=state_51["link"],
                            comentarios=d51.get("comentarios", []),
                            status=d51.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 5.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 5.1", on_click=salvar_51).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("5.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 5.2 (Seleção Única com Pontuação Negativa / Penalização)
                # =============================================================================
                opcoes_52 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não tem uma periodicidade – (-10 pts)": -10.0,
                    "Somente por solicitação – (-10 pts)": -10.0,
                    "Não realiza poda e/ou corte de árvores – (-15 pts)": -15.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.2",
                    titulo="Periodicidade de Poda e Manutenção das Árvores",
                    pergunta="A Prefeitura mantém uma periodicidade de poda/manutenção das árvores?",
                    opcoes=opcoes_52,
                    placeholder_link="Insira o plano de arborização, cronograma de poda ou ordens de serviço...",
                    on_save_callback=render_conteudo.refresh,
                )

    # =============================================================================
                # QUESITO 5.2.1 (Seleção Múltipla com Pontuação Dinâmica e Penalização)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("5.2.1 • Destinação dos Resíduos das Podas de Árvores").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a destinação dos resíduos das podas de árvores?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ 3 ou mais opções válidas = 20 pts | 2 opções = 10 pts | 1 opção = 5 pts | Aterro = -5 pts").classes("text-xs text-gray-400 mb-6")

                    d521 = res_data.get("5.2.1") or {}
                    try:
                        sel_521_salvos = json.loads(d521.get("valor", "[]"))
                        if not isinstance(sel_521_salvos, list): sel_521_salvos = []
                    except Exception:
                        sel_521_salvos = []

                    mapa_521 = {
                        "moveis": "Reaproveitamento para produzir móveis, brinquedos, utensílios ou objetos de decoração",
                        "compostagem": "Compostagem para produção de mudas, na jardinagem e arborização da cidade",
                        "queima": "Queima para aquecimento e cocção",
                        "energia": "Geração de energia",
                        "construcao": "Uso na construção civil",
                        "aterro": "Envio para aterro sanitário (-05 pts)",
                        "armazenamento": "Armazenamento dos resíduos das podas",
                    }

                    state_521 = {k: k in sel_521_salvos for k in mapa_521.keys()}
                    state_521["link"] = d521.get("link", "")

                    def calc_pts_521():
                        # Opções que contam pontuação positiva
                        opcoes_validas = ["moveis", "compostagem", "queima", "energia", "construcao"]
                        qtd_validas = sum(1 for k in opcoes_validas if state_521.get(k))

                        pts = 0.0
                        if qtd_validas >= 3:
                            pts = 20.0
                        elif qtd_validas == 2:
                            pts = 10.0
                        elif qtd_validas == 1:
                            pts = 5.0

                        # Aplica a penalização caso vá para aterro sanitário
                        if state_521.get("aterro"):
                            pts -= 5.0

                        return pts

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_a = [ui.checkbox(rotulo).bind_value(state_521, k) for k, rotulo in list(mapa_521.items())[:4]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_b = [ui.checkbox(rotulo).bind_value(state_521, k) for k, rotulo in list(mapa_521.items())[4:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_521["link"],
                        placeholder="Insira notas de envio, controle de compostagem, contratos de destinação de resíduos...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_521, "link")

                    lbl_pts_521 = ui.label(f"📊 Impacto de Pontuação no Quesito 5.2.1: {calc_pts_521():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_521():
                        lbl_pts_521.set_text(f"📊 Impacto de Pontuação no Quesito 5.2.1: {calc_pts_521():.1f} pontos")

                    for cb in cb_col_a + cb_col_b:
                        cb.on("update:model-value", att_pts_521)

                    def salvar_521():
                        selecionados = [k for k in mapa_521.keys() if state_521.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="5.2.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_521(),
                            link=state_521["link"],
                            comentarios=d521.get("comentarios", []),
                            status=d521.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 5.2.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 5.2.1", on_click=salvar_521).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("5.2.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 5.3 (Seleção Única - Radio Button com Penalização)
                # =============================================================================
                opcoes_53 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – (-10 pts)": -10.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.3",
                    titulo="Capacitação da Equipe para Poda de Árvores",
                    pergunta="O pessoal da prefeitura responsável por manutenção das árvores é devidamente orientado/treinado para realizar a poda de maneira correta?",
                    opcoes=opcoes_53,
                    placeholder_link="Insira certificados de treinamento da equipe, listas de presença ou certificados de cursos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 6.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_60 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="6.0",
                    titulo="Medidas Preventivas de Contingenciamento para Estiagem",
                    pergunta="Existem ações e medidas preventivas de contingenciamento para os períodos de estiagem executados pela Prefeitura? (Estiagem é um período prolongado de baixa pluviosidade ou sua ausência, no qual a perda de umidade do solo é superior à sua reposição).",
                    opcoes=opcoes_60,
                    placeholder_link="Insira o Plano de Contingência para Estiagem, Decretos de emergência hídrica ou campanhas de racionamento...",
                    on_save_callback=render_conteudo.refresh,
                )

    # =============================================================================
                # QUESITO 6.1 (Seleção Múltipla com Pontuação Cumulativa Específica)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("6.1 • Ações e Medidas Preventivas para Períodos de Estiagem").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale as ações e medidas preventivas de contingenciamento executadas pela Prefeitura:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as ações aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d61 = res_data.get("6.1") or {}
                    try:
                        sel_61_salvos = json.loads(d61.get("valor", "[]"))
                        if not isinstance(sel_61_salvos, list): sel_61_salvos = []
                    except Exception:
                        sel_61_salvos = []

                    mapa_61 = {
                        "plano_emergencial": ("Plano emergencial ou de contingenciamento (+30 pts)", 30.0),
                        "manobra_agua": ("Manejo/manobras de água entre os reservatórios (0 pts)", 0.0),
                        "campanha": ("Campanha de conscientização da população (+5 pts)", 5.0),
                        "fontes_alt": ("Busca de fontes alternativas (poços artesianos) (0 pts)", 0.0),
                        "racionamento": ("Uso racional da distribuição de água (racionamento) (0 pts)", 0.0),
                        "rodizio": ("Implantação de rodízio de fornecimento de água (0 pts)", 0.0),
                        "reduc_pressao": ("Redução da pressão no abastecimento de água (0 pts)", 0.0),
                        "multa": ("Multa em caso de desperdício de água (0 pts)", 0.0),
                        "tarifa_dif": ("Tarifa/taxa diferenciada para aumento de consumo (0 pts)", 0.0),
                        "caminhao_pipa": ("Fornecimento de caminhões pipa (0 pts)", 0.0),
                        "drenagem": ("Drenagem pluvial (0 pts)", 0.0),
                        "reuso": ("Incentivo à instalação de sistema para água de reúso (+5 pts)", 5.0),
                        "perdas": ("Redução das perdas na distribuição de água (0 pts)", 0.0),
                        "desassoreamento": ("Desassoreamento (0 pts)", 0.0),
                        "divulgacao": ("Divulgação dos resultados obtidos e situação dos mananciais (+10 pts)", 10.0),
                    }

                    state_61 = {k: k in sel_61_salvos for k in mapa_61.keys()}
                    state_61["link"] = d61.get("link", "")

                    def calc_pts_61():
                        pts = sum(peso for k, (_, peso) in mapa_61.items() if state_61.get(k))
                        return min(pts, 50.0)

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_61a = [ui.checkbox(rotulo).bind_value(state_61, k) for k, (rotulo, _) in list(mapa_61.items())[:8]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_61b = [ui.checkbox(rotulo).bind_value(state_61, k) for k, (rotulo, _) in list(mapa_61.items())[8:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_61["link"],
                        placeholder="Insira o Plano de Contingência, materiais educativos, Decretos ou relatórios...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_61, "link")

                    lbl_pts_61 = ui.label(f"📊 Impacto de Pontuação no Quesito 6.1: {calc_pts_61():.1f} / 50.0 pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_61():
                        lbl_pts_61.set_text(f"📊 Impacto de Pontuação no Quesito 6.1: {calc_pts_61():.1f} / 50.0 pontos")

                    for cb in cb_col_61a + cb_col_61b:
                        cb.on("update:model-value", att_pts_61)

                    def salvar_61():
                        selecionados = [k for k in mapa_61.keys() if state_61.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="6.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_61(),
                            link=state_61["link"],
                            comentarios=d61.get("comentarios", []),
                            status=d61.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 6.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 6.1", on_click=salvar_61).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("6.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 6.2 (Seleção Múltipla por Setor)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("6.2 • Setores com Contingenciamento para Provisão de Água Potável").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Em quais setores existem ações e medidas de contingenciamento específicos para provisão de água potável?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione os setores contemplados e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d62 = res_data.get("6.2") or {}
                    try:
                        sel_62_salvos = json.loads(d62.get("valor", "[]"))
                        if not isinstance(sel_62_salvos, list): sel_62_salvos = []
                    except Exception:
                        sel_62_salvos = []

                    mapa_62 = {
                        "educacao": ("Rede Municipal de Educação (+10 pts)", 10.0),
                        "saude": ("Rede Municipal da Atenção Básica da Saúde (+10 pts)", 10.0),
                        "outro": ("Outro setor (+5 pts)", 5.0),
                    }

                    state_62 = {k: k in sel_62_salvos for k in mapa_62.keys()}
                    state_62["link"] = d62.get("link", "")

                    def calc_pts_62():
                        return sum(peso for k, (_, peso) in mapa_62.items() if state_62.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-2"):
                            cb_edu = ui.checkbox(mapa_62["educacao"][0]).bind_value(state_62, "educacao")
                            cb_sau = ui.checkbox(mapa_62["saude"][0]).bind_value(state_62, "saude")
                            cb_out = ui.checkbox(mapa_62["outro"][0]).bind_value(state_62, "outro")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_62["link"],
                            placeholder="Insira ordens de serviço, relatórios setoriais ou protocolos de abastecimento de emergência...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_62, "link")

                    lbl_pts_62 = ui.label(f"📊 Impacto de Pontuação no Quesito 6.2: {calc_pts_62():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_62():
                        lbl_pts_62.set_text(f"📊 Impacto de Pontuação no Quesito 6.2: {calc_pts_62():.1f} pontos")

                    cb_edu.on("update:model-value", att_pts_62)
                    cb_sau.on("update:model-value", att_pts_62)
                    cb_out.on("update:model-value", att_pts_62)

                    def salvar_62():
                        selecionados = [k for k in mapa_62.keys() if state_62.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="6.2",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_62(),
                            link=state_62["link"],
                            comentarios=d62.get("comentarios", []),
                            status=d62.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 6.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 6.2", on_click=salvar_62).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("6.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_70 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.0",
                    titulo="Plano Municipal ou Regional de Saneamento Básico",
                    pergunta="O município possui seu Plano Municipal ou Regional de Saneamento Básico instituído?",
                    opcoes=opcoes_70,
                    placeholder_link="Insira o link da Lei Municipal, Decreto ou publicação oficial do Plano de Saneamento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.1 (Campos de Texto para Instrumento Normativo)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.1 • Instrumento Normativo do Plano de Saneamento Básico").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Preencha as informações do ato legal e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d71 = res_data.get("7.1") or {}
                    raw_val_71 = str(d71.get("valor") or "")
                    
                    norma_i, num_i, data_i = "", "", ""
                    if "|NUM:" in raw_val_71 and "|DATA:" in raw_val_71:
                        partes_71 = raw_val_71.split("|NUM:")
                        norma_i = partes_71[0]
                        num_i, data_i = partes_71[1].split("|DATA:")

                    state_71 = {
                        "norma": norma_i,
                        "numero": num_i,
                        "data": data_i,
                        "link": d71.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input("Instrumento Normativo (Ex: Lei Municipal, Decreto):", value=norma_i, placeholder="Ex: Lei Municipal").classes("w-full").props("outlined").bind_value(state_71, "norma")
                            ui.input("Número do Instrumento:", value=num_i, placeholder="Ex: nº 1.234/2020").classes("w-full").props("outlined").bind_value(state_71, "numero")
                            ui.input("Data da Publicação:", value=data_i, placeholder="Ex: 15/03/2020").classes("w-full").props("outlined").bind_value(state_71, "data")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_71["link"],
                            placeholder="Insira a cópia do Diário Oficial ou link do documento na íntegra...",
                        ).classes("w-full").props("outlined rows=6").bind_value(state_71, "link")

                    def salvar_71():
                        composite_val = f"{state_71['norma']}|NUM:{state_71['numero']}|DATA:{state_71['data']}"
                        save_resposta(
                            ano=ano_sel,
                            qid="7.1",
                            valor=composite_val,
                            pontos=0.0,
                            link=state_71["link"],
                            comentarios=d71.get("comentarios", []),
                            status=d71.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.1", on_click=salvar_71).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 7.2 (Validação de Link vs Texto XYZ)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.2 • Link do Plano Municipal/Regional de Saneamento Básico").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a página eletrônica (link na internet) do Plano Municipal ou Regional de Saneamento Básico:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Se não estiver disponível na internet, insira 'XYZ' no campo. (Link válido = 2 pts | XYZ = 0 pts)").classes("text-xs text-gray-400 mb-6")

                    d72 = res_data.get("7.2") or {}
                    val_72_i = str(d72.get("valor") or "").strip()

                    state_72 = {
                        "link_plano": val_72_i if val_72_i else "XYZ",
                        "link_evid": d72.get("link", ""),
                    }

                    def calc_pts_72():
                        txt = state_72["link_plano"].strip()
                        if not txt or txt.upper() == "XYZ":
                            return 0.0
                        return 2.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Link do Plano na Internet (ou digite XYZ):",
                                value=state_72["link_plano"],
                                placeholder="https://... ou XYZ",
                            ).classes("w-full").props("outlined").bind_value(state_72, "link_plano")

                        ui.textarea(
                            label="Link de Evidência / Documento Complementar:",
                            value=state_72["link_evid"],
                            placeholder="Insira o link da página do portal da transparência ou publicação...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_72, "link_evid")

                    lbl_pts_72 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.2: {calc_pts_72():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_72():
                        lbl_pts_72.set_text(f"📊 Impacto de Pontuação no Quesito 7.2: {calc_pts_72():.1f} pontos")

                    def salvar_72():
                        pts = calc_pts_72()
                        save_resposta(
                            ano=ano_sel,
                            qid="7.2",
                            valor=state_72["link_plano"],
                            pontos=pts,
                            link=state_72["link_evid"],
                            comentarios=d72.get("comentarios", []),
                            status=d72.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.2", on_click=salvar_72).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.3 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_73 = {
                    "Selecione...": 0.0,
                    "Sim – 10 pts": 10.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.3",
                    titulo="Metas de Abastecimento de Água Potável",
                    pergunta="O Plano Municipal ou Regional de Saneamento Básico possui metas de abastecimento de água potável?",
                    opcoes=opcoes_73,
                    placeholder_link="Insira o capítulo/página do plano contendo as metas de abastecimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.3.1 (Seleção Múltipla de Metas)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.3.1 • Metas Estabelecidas sobre Abastecimento de Água").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais as metas estabelecidas sobre abastecimento de água potável:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as metas presentes no Plano e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d731 = res_data.get("7.3.1") or {}
                    try:
                        sel_731_salvos = json.loads(d731.get("valor", "[]"))
                        if not isinstance(sel_731_salvos, list): sel_731_salvos = []
                    except Exception:
                        sel_731_salvos = []

                    mapa_731 = {
                        "expansao": ("Metas de expansão do serviço de abastecimento de água (0 pts)", 0.0),
                        "perdas": ("Metas de redução de perdas na distribuição de água tratada (+2,5 pts)", 2.5),
                        "qualidade": ("Metas de qualidade na prestação do serviço (+2,5 pts)", 2.5),
                        "eficiencia": ("Metas de eficiência e de uso racional da água (+2,5 pts)", 2.5),
                        "volume_percapita": ("Estabelecimento de volume mínimo de abastecimento per capita (+2,5 pts)", 2.5),
                        "direitos_deveres": ("Estabelecimento de direitos e deveres dos usuários (+2,5 pts)", 2.5),
                        "universalizacao_2033": ("Meta de universalização do abastecimento até 31/12/2033 (+2,5 pts)", 2.5),
                        "cronograma": ("Estabelecimento de cronograma para atingimento das metas (+5,0 pts)", 5.0),
                    }

                    state_731 = {k: k in sel_731_salvos for k in mapa_731.keys()}
                    state_731["link"] = d731.get("link", "")

                    def calc_pts_731():
                        return sum(peso for k, (_, peso) in mapa_731.items() if state_731.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_731a = [ui.checkbox(rotulo).bind_value(state_731, k) for k, (rotulo, _) in list(mapa_731.items())[:4]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_731b = [ui.checkbox(rotulo).bind_value(state_731, k) for k, (rotulo, _) in list(mapa_731.items())[4:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_731["link"],
                        placeholder="Insira o link das páginas/tabelas do plano onde constam as metas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_731, "link")

                    lbl_pts_731 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.3.1: {calc_pts_731():.1f} / 20.0 pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_731():
                        lbl_pts_731.set_text(f"📊 Impacto de Pontuação no Quesito 7.3.1: {calc_pts_731():.1f} / 20.0 pontos")

                    for cb in cb_col_731a + cb_col_731b:
                        cb.on("update:model-value", att_pts_731)

                    def salvar_731():
                        selecionados = [k for k in mapa_731.keys() if state_731.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="7.3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_731(),
                            link=state_731["link"],
                            comentarios=d731.get("comentarios", []),
                            status=d731.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.3.1", on_click=salvar_731).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.3.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.3.2 (Data Prevista para Universalização e Penalização)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.3.2 • Data Prevista para Universalização do Abastecimento").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a data prevista para universalização do abastecimento de água potável no município?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Caso já tenha sido universalizado, informe: 01/01/2001. Se Data > 31/12/2033 = Perde 5 pontos.").classes("text-xs text-gray-400 mb-6")

                    d732 = res_data.get("7.3.2") or {}
                    val_732_i = str(d732.get("valor") or "31/12/2033")

                    state_732 = {
                        "data_univ": val_732_i,
                        "link": d732.get("link", ""),
                    }

                    def calc_pts_732():
                        dt_str = state_732["data_univ"].strip()
                        try:
                            # Tenta parsear no formato dd/mm/YYYY ou YYYY-mm-dd
                            if "/" in dt_str:
                                partes = dt_str.split("/")
                                ano_num = int(partes[2]) if len(partes) == 3 else 2033
                            elif "-" in dt_str:
                                partes = dt_str.split("-")
                                ano_num = int(partes[0]) if len(partes) == 3 else 2033
                            else:
                                ano_num = int(dt_str)
                            
                            if ano_num > 2033:
                                return -5.0
                        except Exception:
                            pass
                        return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Data Prevista (DD/MM/AAAA):",
                                value=state_732["data_univ"],
                                placeholder="Ex: 31/12/2033 ou 01/01/2001",
                            ).classes("w-full").props("outlined").bind_value(state_732, "data_univ")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_732["link"],
                            placeholder="Insira o link do trecho do Plano de Saneamento que comprova a data...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_732, "link")

                    lbl_pts_732 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.3.2: {calc_pts_732():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_732():
                        lbl_pts_732.set_text(f"📊 Impacto de Pontuação no Quesito 7.3.2: {calc_pts_732():.1f} pontos")

                    def salvar_732():
                        pts = calc_pts_732()
                        save_resposta(
                            ano=ano_sel,
                            qid="7.3.2",
                            valor=state_732["data_univ"],
                            pontos=pts,
                            link=state_732["link"],
                            comentarios=d732.get("comentarios", []),
                            status=d732.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.3.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.3.2", on_click=salvar_732).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.3.2", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 7.4 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_74 = {
                    "Selecione...": 0.0,
                    "Sim – 10 pts": 10.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.4",
                    titulo="Metas de Coleta de Esgoto",
                    pergunta="O Plano Municipal ou Regional de Saneamento Básico possui metas de coleta de esgoto?",
                    opcoes=opcoes_74,
                    placeholder_link="Insira o capítulo/página do plano contendo as metas de coleta de esgoto...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.4.1 (Seleção Múltipla de Metas de Esgoto)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.4.1 • Metas Estabelecidas sobre Coleta de Esgoto").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais as metas estabelecidas sobre coleta de esgoto:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as metas presentes no Plano e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d741 = res_data.get("7.4.1") or {}
                    try:
                        sel_741_salvos = json.loads(d741.get("valor", "[]"))
                        if not isinstance(sel_741_salvos, list): sel_741_salvos = []
                    except Exception:
                        sel_741_salvos = []

                    mapa_741 = {
                        "expansao_esgoto": ("Metas de expansão do serviço de coleta de esgoto (0 pts)", 0.0),
                        "qualidade_esgoto": ("Metas de qualidade na prestação do serviço (+3,5 pts)", 3.5),
                        "reuso_efluentes": ("Meta do reúso de efluentes sanitários (+3,5 pts)", 3.5),
                        "direitos_deveres_esgoto": ("Estabelecimento de direitos e deveres dos usuários (+3,5 pts)", 3.5),
                        "universalizacao_esgoto_2033": ("Meta de universalização da coleta de esgoto até 31/12/2033 (+3,5 pts)", 3.5),
                        "cronograma_esgoto": ("Estabelecimento de cronograma para atingimento das metas (+6,0 pts)", 6.0),
                    }

                    state_741 = {k: k in sel_741_salvos for k in mapa_741.keys()}
                    state_741["link"] = d741.get("link", "")

                    def calc_pts_741():
                        return sum(peso for k, (_, peso) in mapa_741.items() if state_741.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_741a = [ui.checkbox(rotulo).bind_value(state_741, k) for k, (rotulo, _) in list(mapa_741.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_741b = [ui.checkbox(rotulo).bind_value(state_741, k) for k, (rotulo, _) in list(mapa_741.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_741["link"],
                        placeholder="Insira o link das páginas/tabelas do plano onde constam as metas de coleta...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_741, "link")

                    lbl_pts_741 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.4.1: {calc_pts_741():.1f} / 20.0 pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_741():
                        lbl_pts_741.set_text(f"📊 Impacto de Pontuação no Quesito 7.4.1: {calc_pts_741():.1f} / 20.0 pontos")

                    for cb in cb_col_741a + cb_col_741b:
                        cb.on("update:model-value", att_pts_741)

                    def salvar_741():
                        selecionados = [k for k in mapa_741.keys() if state_741.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="7.4.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_741(),
                            link=state_741["link"],
                            comentarios=d741.get("comentarios", []),
                            status=d741.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.4.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.4.1", on_click=salvar_741).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.4.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.4.2 (Data Universalização Coleta de Esgoto e Penalização)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.4.2 • Data Prevista para Universalização da Coleta de Esgoto").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a data prevista para universalização da coleta de esgoto no município?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Caso já tenha sido universalizado, informe: 01/01/2001. Se Data > 31/12/2033 = Perde 5 pontos.").classes("text-xs text-gray-400 mb-6")

                    d742 = res_data.get("7.4.2") or {}
                    val_742_i = str(d742.get("valor") or "31/12/2033")

                    state_742 = {
                        "data_univ": val_742_i,
                        "link": d742.get("link", ""),
                    }

                    def calc_pts_742():
                        dt_str = state_742["data_univ"].strip()
                        try:
                            if "/" in dt_str:
                                partes = dt_str.split("/")
                                ano_num = int(partes[2]) if len(partes) == 3 else 2033
                            elif "-" in dt_str:
                                partes = dt_str.split("-")
                                ano_num = int(partes[0]) if len(partes) == 3 else 2033
                            else:
                                ano_num = int(dt_str)
                            
                            if ano_num > 2033:
                                return -5.0
                        except Exception:
                            pass
                        return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Data Prevista (DD/MM/AAAA):",
                                value=state_742["data_univ"],
                                placeholder="Ex: 31/12/2033 ou 01/01/2001",
                            ).classes("w-full").props("outlined").bind_value(state_742, "data_univ")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_742["link"],
                            placeholder="Insira o link do trecho do Plano que comprova a data...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_742, "link")

                    lbl_pts_742 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.4.2: {calc_pts_742():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_742():
                        lbl_pts_742.set_text(f"📊 Impacto de Pontuação no Quesito 7.4.2: {calc_pts_742():.1f} pontos")

                    def salvar_742():
                        pts = calc_pts_742()
                        save_resposta(
                            ano=ano_sel,
                            qid="7.4.2",
                            valor=state_742["data_univ"],
                            pontos=pts,
                            link=state_742["link"],
                            comentarios=d742.get("comentarios", []),
                            status=d742.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.4.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.4.2", on_click=salvar_742).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.4.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.5 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_75 = {
                    "Selecione...": 0.0,
                    "Sim – 30 pts": 30.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.5",
                    titulo="Metas de Tratamento de Esgoto",
                    pergunta="O Plano Municipal ou Regional de Saneamento Básico possui metas de tratamento de esgoto?",
                    opcoes=opcoes_75,
                    placeholder_link="Insira a página do plano que comprova as metas de tratamento de esgoto...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.5.1 (Data Universalização Tratamento de Esgoto e Penalização)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.5.1 • Data Prevista para Universalização do Tratamento de Esgoto").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a data prevista para universalização do tratamento de esgoto no município?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Caso já tenha sido universalizado, informe: 01/01/2001. Se Data > 31/12/2033 = Perde 5 pontos.").classes("text-xs text-gray-400 mb-6")

                    d751 = res_data.get("7.5.1") or {}
                    val_751_i = str(d751.get("valor") or "31/12/2033")

                    state_751 = {
                        "data_univ": val_751_i,
                        "link": d751.get("link", ""),
                    }

                    def calc_pts_751():
                        dt_str = state_751["data_univ"].strip()
                        try:
                            if "/" in dt_str:
                                partes = dt_str.split("/")
                                ano_num = int(partes[2]) if len(partes) == 3 else 2033
                            elif "-" in dt_str:
                                partes = dt_str.split("-")
                                ano_num = int(partes[0]) if len(partes) == 3 else 2033
                            else:
                                ano_num = int(dt_str)
                            
                            if ano_num > 2033:
                                return -5.0
                        except Exception:
                            pass
                        return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Data Prevista (DD/MM/AAAA):",
                                value=state_751["data_univ"],
                                placeholder="Ex: 31/12/2033 ou 01/01/2001",
                            ).classes("w-full").props("outlined").bind_value(state_751, "data_univ")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_751["link"],
                            placeholder="Insira o link do documento do plano que comprova a data...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_751, "link")

                    lbl_pts_751 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.5.1: {calc_pts_751():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_751():
                        lbl_pts_751.set_text(f"📊 Impacto de Pontuação no Quesito 7.5.1: {calc_pts_751():.1f} pontos")

                    def salvar_751():
                        pts = calc_pts_751()
                        save_resposta(
                            ano=ano_sel,
                            qid="7.5.1",
                            valor=state_751["data_univ"],
                            pontos=pts,
                            link=state_751["link"],
                            comentarios=d751.get("comentarios", []),
                            status=d751.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.5.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.5.1", on_click=salvar_751).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.5.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 7.6 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_76 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.6",
                    titulo="Metas de Drenagem e Manejo de Águas Pluviais Urbanas",
                    pergunta="O Plano Municipal ou Regional de Saneamento Básico possui metas de drenagem e manejo de águas pluviais urbanas?",
                    opcoes=opcoes_76,
                    placeholder_link="Insira a página do plano que comprova as metas de drenagem pluvial...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.6.1 (Seleção Múltipla de Metas de Drenagem Pluvial)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.6.1 • Metas Estabelecidas sobre Drenagem e Águas Pluviais").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais as metas estabelecidas sobre drenagem e manejo de águas pluviais urbanas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as metas presentes no Plano e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d761 = res_data.get("7.6.1") or {}
                    try:
                        sel_761_salvos = json.loads(d761.get("valor", "[]"))
                        if not isinstance(sel_761_salvos, list): sel_761_salvos = []
                    except Exception:
                        sel_761_salvos = []

                    mapa_761 = {
                        "expansao_drenagem": ("Metas de expansão do serviço de drenagem e manejo de águas pluviais", 0.0),
                        "qualidade_drenagem": ("Metas de qualidade na prestação do serviço de drenagem e manejo", 0.0),
                        "aproveitamento_chuva": ("Metas de aproveitamento de águas da chuva", 0.0),
                        "direitos_deveres_drenagem": ("Estabelecimento de direitos e deveres dos usuários", 0.0),
                        "cronograma_drenagem": ("Estabelecimento de cronograma para o atingimento das metas", 0.0),
                    }

                    state_761 = {k: k in sel_761_salvos for k in mapa_761.keys()}
                    state_761["link"] = d761.get("link", "")

                    def calc_pts_761():
                        return sum(peso for k, (_, peso) in mapa_761.items() if state_761.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_761a = [ui.checkbox(rotulo).bind_value(state_761, k) for k, (rotulo, _) in list(mapa_761.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_761b = [ui.checkbox(rotulo).bind_value(state_761, k) for k, (rotulo, _) in list(mapa_761.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_761["link"],
                        placeholder="Insira o link do capítulo/páginas do plano contendo as metas de drenagem...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_761, "link")

                    def salvar_761():
                        selecionados = [k for k in mapa_761.keys() if state_761.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="7.6.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_761(),
                            link=state_761["link"],
                            comentarios=d761.get("comentarios", []),
                            status=d761.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.6.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.6.1", on_click=salvar_761).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.6.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.7 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_77 = {
                    "Selecione...": 0.0,
                    "Sim – 30 pts": 30.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.7",
                    titulo="Monitoramento e Avaliação das Ações e Metas",
                    pergunta="Realiza monitoramento e avaliação das ações e metas relacionadas ao abastecimento de água potável e esgotamento sanitário?",
                    opcoes=opcoes_77,
                    placeholder_link="Insira o link dos relatórios de monitoramento ou da comissão/equipe de acompanhamento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.7.1 (Seleção Múltipla de Formas de Monitoramento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.7.1 • Formas de Monitoramento e Avaliação Executadas").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("De que forma é realizado o monitoramento e avaliação relacionadas ao abastecimento de água potável e esgotamento sanitário?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d771 = res_data.get("7.7.1") or {}
                    try:
                        sel_771_salvos = json.loads(d771.get("valor", "[]"))
                        if not isinstance(sel_771_salvos, list): sel_771_salvos = []
                    except Exception:
                        sel_771_salvos = []

                    mapa_771 = {
                        "relatorios_anuais": ("Relatórios anuais discutidos e/ou publicados", 0.0),
                        "indicadores": ("Indicadores de eficácia e eficiência", 0.0),
                        "recursos": ("Avaliação de recursos aplicados", 0.0),
                        "outro": ("Outro", 0.0),
                    }

                    state_771 = {k: k in sel_771_salvos for k in mapa_771.keys()}
                    state_771["link"] = d771.get("link", "")

                    def calc_pts_771():
                        return sum(peso for k, (_, peso) in mapa_771.items() if state_771.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_771a = [ui.checkbox(rotulo).bind_value(state_771, k) for k, (rotulo, _) in list(mapa_771.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_771b = [ui.checkbox(rotulo).bind_value(state_771, k) for k, (rotulo, _) in list(mapa_771.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_771["link"],
                        placeholder="Insira o link das atas de reuniões, relatórios de indicadores ou publicações de avaliação...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_771, "link")

                    def salvar_771():
                        selecionados = [k for k in mapa_771.keys() if state_771.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="7.7.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_771(),
                            link=state_771["link"],
                            comentarios=d771.get("comentarios", []),
                            status=d771.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.7.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.7.1", on_click=salvar_771).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.7.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 7.8 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_78 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.8",
                    titulo="Cronograma de Metas do Plano de Saneamento",
                    pergunta="O Plano Municipal ou Regional de Saneamento Básico possui cronograma com as metas a serem cumpridas?",
                    opcoes=opcoes_78,
                    placeholder_link="Insira o link da seção/tabela do plano que apresenta o cronograma das metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.8.1 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_781 = {
                    "Selecione...": 0.0,
                    "Todas as metas foram cumpridas dentro do prazo – 50 pts": 50.0,
                    "A maior parte das metas foram cumpridas dentro do prazo – 30 pts": 30.0,
                    "A menor parte das metas foram cumpridas dentro do prazo – 10 pts": 10.0,
                    "As metas não foram cumpridas dentro do prazo – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.8.1",
                    titulo="Cumprimento das Metas de Água e Esgoto",
                    pergunta="As metas do Plano relacionadas ao abastecimento de água potável e esgotamento sanitário estão sendo cumpridas no prazo estipulado?",
                    opcoes=opcoes_781,
                    placeholder_link="Insira relatórios de acompanhamento, pareceres técnicos ou atas de avaliação das metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.8.1.1 (Seleção Múltipla de Motivos para Descumprimento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.8.1.1 • Motivos do Não Cumprimento das Metas").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale os motivos pelos quais as metas relacionadas ao abastecimento de água potável e esgotamento sanitário não estão sendo cumpridas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione os motivos aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d7811 = res_data.get("7.8.1.1") or {}
                    try:
                        sel_7811_salvos = json.loads(d7811.get("valor", "[]"))
                        if not isinstance(sel_7811_salvos, list): sel_7811_salvos = []
                    except Exception:
                        sel_7811_salvos = []

                    mapa_7811 = {
                        "recursos": ("Falta de recursos orçamentários", 0.0),
                        "legislativo": ("Falta de aprovação legislativa", 0.0),
                        "atraso_licitacao": ("Atraso na licitação", 0.0),
                        "nao_licitou": ("Não realizou licitação necessária", 0.0),
                        "pessoal": ("Falta de pessoal qualificado", 0.0),
                        "consenso_consorcio": ("Falta de consenso no consórcio intermunicipal", 0.0),
                        "outros": ("Outros motivos", 0.0),
                    }

                    state_7811 = {k: k in sel_7811_salvos for k in mapa_7811.keys()}
                    state_7811["link"] = d7811.get("link", "")

                    def calc_pts_7811():
                        return sum(peso for k, (_, peso) in mapa_7811.items() if state_7811.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_7811a = [ui.checkbox(rotulo).bind_value(state_7811, k) for k, (rotulo, _) in list(mapa_7811.items())[:4]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_7811b = [ui.checkbox(rotulo).bind_value(state_7811, k) for k, (rotulo, _) in list(mapa_7811.items())[4:]]

                    ui.textarea(
                        label="Link de Evidência / Justificativa Documentada:",
                        value=state_7811["link"],
                        placeholder="Insira o link de relatórios, justificativas oficiais ou registros do conselho...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_7811, "link")

                    def salvar_7811():
                        selecionados = [k for k in mapa_7811.keys() if state_7811.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="7.8.1.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_7811(),
                            link=state_7811["link"],
                            comentarios=d7811.get("comentarios", []),
                            status=d7811.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.8.1.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.8.1.1", on_click=salvar_7811).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.8.1.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 7.9 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_79 = {
                    "Selecione...": 0.0,
                    "Sim – 03 pts": 3.0,
                    "Não – 00 pts": 0.0,
                    "Não há áreas prioritárias/críticas no município – 03 pts": 3.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.9",
                    titulo="Previsão para Áreas Prioritárias / Críticas",
                    pergunta="Possui previsão para áreas prioritárias/críticas de abastecimento de água potável e esgotamento sanitário do município? (Ex: habitações precárias, mananciais degradados, áreas vulneráveis).",
                    opcoes=opcoes_79,
                    placeholder_link="Insira a página do plano ou mapa técnico identificando as áreas prioritárias...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 7.10 (Data da Última Revisão e Penalização de Idade do Plano)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("7.10 • Data da Última Revisão do Plano de Saneamento Básico").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a data da última revisão do Plano Municipal ou Regional de Saneamento Básico?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Se não houve revisão, informe a data do início de vigência. Se Data <= 31/12/2014 = Perde 30 pontos.").classes("text-xs text-gray-400 mb-6")

                    d710 = res_data.get("7.10") or {}
                    val_710_i = str(d710.get("valor") or "31/12/2020")

                    state_710 = {
                        "data_rev": val_710_i,
                        "link": d710.get("link", ""),
                    }

                    def calc_pts_710():
                        dt_str = state_710["data_rev"].strip()
                        try:
                            if "/" in dt_str:
                                partes = dt_str.split("/")
                                ano_num = int(partes[2]) if len(partes) == 3 else 2020
                            elif "-" in dt_str:
                                partes = dt_str.split("-")
                                ano_num = int(partes[0]) if len(partes) == 3 else 2020
                            else:
                                ano_num = int(dt_str)
                            
                            if ano_num <= 2014:
                                return -30.0
                        except Exception:
                            pass
                        return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Data da Revisão/Vigência (DD/MM/AAAA):",
                                value=state_710["data_rev"],
                                placeholder="Ex: 15/06/2020",
                            ).classes("w-full").props("outlined").bind_value(state_710, "data_rev")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_710["link"],
                            placeholder="Insira o link da Lei/Decreto de revisão do plano ou publicação oficial...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_710, "link")

                    lbl_pts_710 = ui.label(f"📊 Impacto de Pontuação no Quesito 7.10: {calc_pts_710():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_710():
                        lbl_pts_710.set_text(f"📊 Impacto de Pontuação no Quesito 7.10: {calc_pts_710():.1f} pontos")

                    def salvar_710():
                        pts = calc_pts_710()
                        save_resposta(
                            ano=ano_sel,
                            qid="7.10",
                            valor=state_710["data_rev"],
                            pontos=pts,
                            link=state_710["link"],
                            comentarios=d710.get("comentarios", []),
                            status=d710.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 7.10 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 7.10", on_click=salvar_710).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("7.10", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 8.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_80 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.0",
                    titulo="Plano Municipal/Regional de Gestão Integrada de Resíduos Sólidos",
                    pergunta="Foi elaborado o Plano Municipal ou Regional de Gestão Integrada de Resíduos Sólidos, conforme Lei nº 12.305/2010?",
                    opcoes=opcoes_80,
                    placeholder_link="Insira a cópia ou página de publicação do PMGIRS...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 8.1 (Campos de Texto para Instrumento Normativo)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.1 • Instrumento Normativo do PMGIRS").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Preencha as informações do ato legal e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d81 = res_data.get("8.1") or {}
                    raw_val_81 = str(d81.get("valor") or "")
                    
                    norma_i, num_i, data_i = "", "", ""
                    if "|NUM:" in raw_val_81 and "|DATA:" in raw_val_81:
                        partes_81 = raw_val_81.split("|NUM:")
                        norma_i = partes_81[0]
                        num_i, data_i = partes_81[1].split("|DATA:")

                    state_81 = {
                        "norma": norma_i,
                        "numero": num_i,
                        "data": data_i,
                        "link": d81.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input("Instrumento Normativo (Ex: Lei Municipal, Decreto):", value=norma_i, placeholder="Ex: Lei Municipal").classes("w-full").props("outlined").bind_value(state_81, "norma")
                            ui.input("Número do Instrumento:", value=num_i, placeholder="Ex: nº 4.567/2018").classes("w-full").props("outlined").bind_value(state_81, "numero")
                            ui.input("Data da Publicação:", value=data_i, placeholder="Ex: 20/10/2018").classes("w-full").props("outlined").bind_value(state_81, "data")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_81["link"],
                            placeholder="Insira a cópia do Diário Oficial ou link do documento na íntegra...",
                        ).classes("w-full").props("outlined rows=6").bind_value(state_81, "link")

                    def salvar_81():
                        composite_val = f"{state_81['norma']}|NUM:{state_81['numero']}|DATA:{state_81['data']}"
                        save_resposta(
                            ano=ano_sel,
                            qid="8.1",
                            valor=composite_val,
                            pontos=0.0,
                            link=state_81["link"],
                            comentarios=d81.get("comentarios", []),
                            status=d81.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.1", on_click=salvar_81).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 8.2 (Validação de Link vs Texto XYZ)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.2 • Link do Instrumento Normativo do PMGIRS").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a página eletrônica (link na internet) do instrumento normativo do PMGIRS:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Se não estiver disponível na internet, insira 'XYZ' no campo. (Link válido = 2 pts | XYZ = 0 pts)").classes("text-xs text-gray-400 mb-6")

                    d82 = res_data.get("8.2") or {}
                    val_82_i = str(d82.get("valor") or "").strip()

                    state_82 = {
                        "link_plano": val_82_i if val_82_i else "XYZ",
                        "link_evid": d82.get("link", ""),
                    }

                    def calc_pts_82():
                        txt = state_82["link_plano"].strip()
                        if not txt or txt.upper() == "XYZ":
                            return 0.0
                        return 2.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Link na Internet (ou digite XYZ):",
                                value=state_82["link_plano"],
                                placeholder="https://... ou XYZ",
                            ).classes("w-full").props("outlined").bind_value(state_82, "link_plano")

                        ui.textarea(
                            label="Link de Evidência / Documento Complementar:",
                            value=state_82["link_evid"],
                            placeholder="Insira o link da página no portal da transparência ou diário oficial...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_82, "link_evid")

                    lbl_pts_82 = ui.label(f"📊 Impacto de Pontuação no Quesito 8.2: {calc_pts_82():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_82():
                        lbl_pts_82.set_text(f"📊 Impacto de Pontuação no Quesito 8.2: {calc_pts_82():.1f} pontos")

                    def salvar_82():
                        pts = calc_pts_82()
                        save_resposta(
                            ano=ano_sel,
                            qid="8.2",
                            valor=state_82["link_plano"],
                            pontos=pts,
                            link=state_82["link_evid"],
                            comentarios=d82.get("comentarios", []),
                            status=d82.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.2", on_click=salvar_82).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 8.3 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_83 = {
                    "Selecione...": 0.0,
                    "Sim – 10 pts": 10.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.3",
                    titulo="Caracterização Qualitativa e Quantitativa dos Resíduos Sólidos",
                    pergunta="A Prefeitura realizou a caracterização qualitativa e quantitativa dos resíduos sólidos urbanos gerados no município, identificando ainda sua origem?",
                    opcoes=opcoes_83,
                    placeholder_link="Insira o estudo de gravimetria ou relatório de caracterização dos resíduos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 8.3.1 (Seleção Múltipla - Formas de Caracterização)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.3.1 • Forma Utilizada para Caracterizar os Resíduos Sólidos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale a forma utilizada para caracterizar os resíduos sólidos do município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d831 = res_data.get("8.3.1") or {}
                    try:
                        sel_831_salvos = json.loads(d831.get("valor", "[]"))
                        if not isinstance(sel_831_salvos, list): sel_831_salvos = []
                    except Exception:
                        sel_831_salvos = []

                    mapa_831 = {
                        "estimativa_secundarios": ("Estimativa com base em dados secundários", 0.0),
                        "estudo_gravimetrico": ("Realização de estudo gravimétrico, por amostragem", 0.0),
                        "dados_primarios": ("Pesquisa de dados primários com medição direta", 0.0),
                        "outros": ("Outros", 0.0),
                    }

                    state_831 = {k: k in sel_831_salvos for k in mapa_831.keys()}
                    state_831["link"] = d831.get("link", "")

                    def calc_pts_831():
                        return sum(peso for k, (_, peso) in mapa_831.items() if state_831.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_831a = [ui.checkbox(rotulo).bind_value(state_831, k) for k, (rotulo, _) in list(mapa_831.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_831b = [ui.checkbox(rotulo).bind_value(state_831, k) for k, (rotulo, _) in list(mapa_831.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_831["link"],
                        placeholder="Insira o link das planilhas, relatórios técnicos ou dados primários...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_831, "link")

                    def salvar_831():
                        selecionados = [k for k in mapa_831.keys() if state_831.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="8.3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_831(),
                            link=state_831["link"],
                            comentarios=d831.get("comentarios", []),
                            status=d831.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.3.1", on_click=salvar_831).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.3.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 8.4 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_84 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.4",
                    titulo="Cronograma com Metas de Resíduos Sólidos",
                    pergunta="Possui cronograma com as metas a serem cumpridas de resíduos sólidos?",
                    opcoes=opcoes_84,
                    placeholder_link="Insira o link da seção do plano que apresenta o cronograma de metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 8.4.1 (Seleção Múltipla de Metas de Resíduos Sólidos)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.4.1 • Metas Estabelecidas sobre Resíduos Sólidos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais as metas estabelecidas sobre resíduos sólidos:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as metas presentes no Plano e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d841 = res_data.get("8.4.1") or {}
                    try:
                        sel_841_salvos = json.loads(d841.get("valor", "[]"))
                        if not isinstance(sel_841_salvos, list): sel_841_salvos = []
                    except Exception:
                        sel_841_salvos = []

                    mapa_841 = {
                        "reducao_fonte": ("Metas de redução da geração de resíduos sólidos na fonte (+2,5 pts)", 2.5),
                        "coleta_seletiva": ("Metas de coleta seletiva (+2,0 pts)", 2.0),
                        "reducao_secos_aterro": ("Metas de redução de resíduos sólidos secos dispostos em aterros (+2,5 pts)", 2.5),
                        "reducao_umidos_aterro": ("Metas de redução de resíduos sólidos úmidos dispostos em aterros (+2,5 pts)", 2.5),
                        "outro": ("Outro (+0,5 pts)", 0.5),
                    }

                    state_841 = {k: k in sel_841_salvos for k in mapa_841.keys()}
                    state_841["link"] = d841.get("link", "")

                    def calc_pts_841():
                        return sum(peso for k, (_, peso) in mapa_841.items() if state_841.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_841a = [ui.checkbox(rotulo).bind_value(state_841, k) for k, (rotulo, _) in list(mapa_841.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_841b = [ui.checkbox(rotulo).bind_value(state_841, k) for k, (rotulo, _) in list(mapa_841.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_841["link"],
                        placeholder="Insira o link do trecho do plano ou tabelas com o detalhamento das metas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_841, "link")

                    lbl_pts_841 = ui.label(f"📊 Impacto de Pontuação no Quesito 8.4.1: {calc_pts_841():.1f} / 10.0 pontos").classes("text-sm font-bold text-green-600 my-2")

                    def att_pts_841():
                        lbl_pts_841.set_text(f"📊 Impacto de Pontuação no Quesito 8.4.1: {calc_pts_841():.1f} / 10.0 pontos")

                    for cb in cb_col_841a + cb_col_841b:
                        cb.on("update:model-value", att_pts_841)

                    def salvar_841():
                        selecionados = [k for k in mapa_841.keys() if state_841.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="8.4.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_841(),
                            link=state_841["link"],
                            comentarios=d841.get("comentarios", []),
                            status=d841.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.4.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.4.1", on_click=salvar_841).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.4.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 8.4.2 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_842 = {
                    "Selecione...": 0.0,
                    "Sim – 30 pts": 30.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.4.2",
                    titulo="Monitoramento e Avaliação das Metas de Resíduos Sólidos",
                    pergunta="Realiza monitoramento e avaliação das ações e metas de resíduos sólidos?",
                    opcoes=opcoes_842,
                    placeholder_link="Insira o link dos relatórios de acompanhamento ou pareceres do conselho...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 8.4.2.1 (Seleção Múltipla - Formas de Monitoramento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.4.2.1 • Formas de Monitoramento e Avaliação de Resíduos Sólidos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("De que forma é realizado o monitoramento e avaliação das ações e metas de resíduos sólidos?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d8421 = res_data.get("8.4.2.1") or {}
                    try:
                        sel_8421_salvos = json.loads(d8421.get("valor", "[]"))
                        if not isinstance(sel_8421_salvos, list): sel_8421_salvos = []
                    except Exception:
                        sel_8421_salvos = []

                    mapa_8421 = {
                        "relatorios_anuais": ("Relatórios anuais discutidos e/ou publicados", 0.0),
                        "indicadores": ("Indicadores de eficácia e eficiência", 0.0),
                        "recursos": ("Avaliação de recursos aplicados", 0.0),
                        "outro": ("Outro", 0.0),
                    }

                    state_8421 = {k: k in sel_8421_salvos for k in mapa_8421.keys()}
                    state_8421["link"] = d8421.get("link", "")

                    def calc_pts_8421():
                        return sum(peso for k, (_, peso) in mapa_8421.items() if state_8421.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_8421a = [ui.checkbox(rotulo).bind_value(state_8421, k) for k, (rotulo, _) in list(mapa_8421.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_8421b = [ui.checkbox(rotulo).bind_value(state_8421, k) for k, (rotulo, _) in list(mapa_8421.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_8421["link"],
                        placeholder="Insira o link das atas de reuniões, relatórios publicados ou sistemas de monitoramento...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_8421, "link")

                    def salvar_8421():
                        selecionados = [k for k in mapa_8421.keys() if state_8421.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="8.4.2.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_8421(),
                            link=state_8421["link"],
                            comentarios=d8421.get("comentarios", []),
                            status=d8421.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.4.2.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.4.2.1", on_click=salvar_8421).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.4.2.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 8.4.3 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_843 = {
                    "Selecione...": 0.0,
                    "Todas as metas foram cumpridas dentro do prazo – 50 pts": 50.0,
                    "A maior parte das metas foram cumpridas dentro do prazo – 30 pts": 30.0,
                    "A menor parte das metas foram cumpridas dentro do prazo – 10 pts": 10.0,
                    "As metas não foram cumpridas dentro do prazo – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.4.3",
                    titulo="Cumprimento das Metas do PMGIRS",
                    pergunta="As metas do Plano Municipal ou Regional de Gestão Integrada de Resíduos Sólidos estão sendo cumpridas no prazo estipulado?",
                    opcoes=opcoes_843,
                    placeholder_link="Insira o relatório de acompanhamento, parecer técnico ou documento que comprove o status de cumprimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 8.4.3.1 (Seleção Múltipla - Motivos para Descumprimento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.4.3.1 • Motivos do Não Cumprimento das Metas do PMGIRS").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale os motivos pelos quais as metas do PMGIRS não estão sendo cumpridas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione os motivos aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d8431 = res_data.get("8.4.3.1") or {}
                    try:
                        sel_8431_salvos = json.loads(d8431.get("valor", "[]"))
                        if not isinstance(sel_8431_salvos, list): sel_8431_salvos = []
                    except Exception:
                        sel_8431_salvos = []

                    mapa_8431 = {
                        "recursos": ("Falta de recursos orçamentários", 0.0),
                        "legislativo": ("Falta de aprovação legislativa", 0.0),
                        "atraso_licitacao": ("Atraso na licitação", 0.0),
                        "nao_licitou": ("Não realizou licitação necessária", 0.0),
                        "pessoal": ("Falta de pessoal qualificado", 0.0),
                        "consenso_consorcio": ("Falta de consenso no consórcio intermunicipal", 0.0),
                        "outros": ("Outros", 0.0),
                    }

                    state_8431 = {k: k in sel_8431_salvos for k in mapa_8431.keys()}
                    state_8431["link"] = d8431.get("link", "")

                    def calc_pts_8431():
                        return sum(peso for k, (_, peso) in mapa_8431.items() if state_8431.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_8431a = [ui.checkbox(rotulo).bind_value(state_8431, k) for k, (rotulo, _) in list(mapa_8431.items())[:4]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_8431b = [ui.checkbox(rotulo).bind_value(state_8431, k) for k, (rotulo, _) in list(mapa_8431.items())[4:]]

                    ui.textarea(
                        label="Link de Evidência / Justificativa Documentada:",
                        value=state_8431["link"],
                        placeholder="Insira o link de relatórios, pareceres oficiais ou atas que justificam os motivos...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_8431, "link")

                    def salvar_8431():
                        selecionados = [k for k in mapa_8431.keys() if state_8431.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="8.4.3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_8431(),
                            link=state_8431["link"],
                            comentarios=d8431.get("comentarios", []),
                            status=d8431.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 8.4.3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.4.3.1", on_click=salvar_8431).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.4.3.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 8.4.4 (Data da Última Revisão/Vigência - Perda de Pontos)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("8.4.4 • Data da Última Revisão do PMGIRS").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Qual a data da última revisão do Plano Municipal ou Regional de Gestão Integrada de Resíduos Sólidos?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Se não houve revisão, informar a data do início de vigência do plano.").classes("text-xs text-gray-500 mb-1")
                    ui.label("⚠️ Regra de Pontuação: Se Data <= 31/12/2014 perde 30 pontos (-30.0). Se Data > 31/12/2014 não perde pontos (0.0).").classes("text-xs font-semibold text-red-500 mb-6")

                    d844 = res_data.get("8.4.4") or {}
                    data_salva_844 = d844.get("valor", "")

                    with ui.grid(columns=2).classes("w-full gap-6 items-center mb-4"):
                        input_data_844 = ui.input(
                            label="Data (DD/MM/AAAA):",
                            value=data_salva_844,
                            placeholder="Ex: 15/08/2018"
                        ).classes("w-full").props("outlined mask='##/##/####'")

                    ui.textarea(
                        label="Link de Evidência / Documento do Plano:",
                        value=d844.get("link", ""),
                        placeholder="Insira o link da publicação da lei, decreto ou publicação oficial do plano/revisão..."
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(d844, "link")

                    def calc_pts_844(data_str):
                        if not data_str or len(data_str) < 10:
                            return 0.0
                        try:
                            partes = data_str.split("/")
                            if len(partes) == 3:
                                dia, mes, ano = int(partes[0]), int(partes[1]), int(partes[2])
                                # Data limite: 31/12/2014
                                if (ano < 2014) or (ano == 2014 and mes <= 12 and dia <= 31):
                                    return -30.0
                                else:
                                    return 0.0
                        except Exception:
                            pass
                        return 0.0

                    def salvar_844():
                        val_data = input_data_844.value or ""
                        pts = calc_pts_844(val_data)
                        save_resposta(
                            ano=ano_sel,
                            qid="8.4.4",
                            valor=val_data,
                            pontos=pts,
                            link=d844.get("link", ""),
                            comentarios=d844.get("comentarios", []),
                            status=d844.get("status", "Pendente")
                        )
                        ui.notify(f"Quesito 8.4.4 salvo! Pontuação/Penalidade calculada: {pts} pts", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 8.4.4", on_click=salvar_844).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("8.4.4", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 9.0 (Seleção Única - Realiza Coleta Seletiva)
                # =============================================================================
                opcoes_90 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="9.0",
                    titulo="Realização de Coleta Seletiva",
                    pergunta="A prefeitura municipal realiza a coleta seletiva de resíduos sólidos?",
                    opcoes=opcoes_90,
                    placeholder_link="Insira o link da página do serviço, contrato ou decreto da coleta seletiva...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 9.1 (Seleção Única - Coleta Programada)
                # =============================================================================
                opcoes_91 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – -30 pts (perde 30 pontos)": -30.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="9.1",
                    titulo="Programação da Coleta Seletiva",
                    pergunta="A coleta seletiva ocorre de forma programada (determinados os horários e dias da semana)?",
                    opcoes=opcoes_91,
                    placeholder_link="Insira o link do cronograma oficial de coleta seletiva divulgado...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 9.2 (Seleção Única - Abrangência Territorial)
                # =============================================================================
                opcoes_92 = {
                    "Selecione...": 0.0,
                    "Todos os bairros do município são atendidos – 100 pts": 100.0,
                    "A maior parte dos bairros são atendidos – 50 pts": 50.0,
                    "A menor parte dos bairros são atendidos – 10 pts": 10.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="9.2",
                    titulo="Abrangência da Coleta Seletiva",
                    pergunta="Todas as regiões do município são atendidas pela coleta seletiva? (Inclusive zona rural e periferia)",
                    opcoes=opcoes_92,
                    placeholder_link="Insira o link do mapa de rotas, relatório de cobertura ou rotas da coleta seletiva...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 9.3 (Seleção Única - Incentivo e Campanhas)
                # =============================================================================
                opcoes_93 = {
                    "Selecione...": 0.0,
                    "Sim – 05 pts": 5.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="9.3",
                    titulo="Incentivo e Campanhas sobre Coleta Seletiva",
                    pergunta="A Prefeitura incentiva e orienta a população por meio de Ações e/ou Campanhas sobre a importância da coleta seletiva? (Não considerar ações/campanhas nas escolas)",
                    opcoes=opcoes_93,
                    placeholder_link="Insira o link de relatórios ou publicações das campanhas institucionais...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 9.3.1 (Seleção Múltipla com Pontuações Individuais)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("9.3.1 • Tipos de Ações e/ou Campanhas Realizadas").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais Ações e/ou Campanhas foram realizadas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções realizadas para somar as pontuações correspondentes e clique em salvar.").classes("text-xs text-gray-400 mb-6")

                    d931 = res_data.get("9.3.1") or {}
                    try:
                        sel_931_salvos = json.loads(d931.get("valor", "[]"))
                        if not isinstance(sel_931_salvos, list): sel_931_salvos = []
                    except Exception:
                        sel_931_salvos = []

                    mapa_931 = {
                        "redes_sociais": ("Divulgações em redes sociais e/ou site da prefeitura", 1.0),
                        "educacao_ambiental": ("Ações de educação ambiental", 0.5),
                        "sinalizacoes_impressos": ("Campanhas de conscientização por meio de sinalizações, folders, cartazes, propagandas e materiais impressos", 1.0),
                        "projetos_incentivo": ("Projetos de incentivo", 1.0),
                        "workshops_palestras": ("Workshops / Palestras", 0.5),
                        "lixeiras_sacolas": ("Instalação de lixeiras seletivas e distribuição de sacolas retornáveis para separação dos resíduos recicláveis", 1.0),
                    }

                    state_931 = {k: k in sel_931_salvos for k in mapa_931.keys()}
                    state_931["link"] = d931.get("link", "")

                    def calc_pts_931():
                        return sum(peso for k, (_, peso) in mapa_931.items() if state_931.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_931a = [ui.checkbox(f"{rotulo} (+{peso} pts)").bind_value(state_931, k) for k, (rotulo, peso) in list(mapa_931.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_931b = [ui.checkbox(f"{rotulo} (+{peso} pts)").bind_value(state_931, k) for k, (rotulo, peso) in list(mapa_931.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Registros Fotográficos / Publicações:",
                        value=state_931["link"],
                        placeholder="Insira o link das divulgações, fotos de eventos, folders ou material de comunicação...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_931, "link")

                    def salvar_931():
                        selecionados = [k for k in mapa_931.keys() if state_931.get(k)]
                        pts = calc_pts_931()
                        save_resposta(
                            ano=ano_sel,
                            qid="9.3.1",
                            valor=json.dumps(selecionados),
                            pontos=pts,
                            link=state_931["link"],
                            comentarios=d931.get("comentarios", []),
                            status=d931.get("status", "Pendente"),
                        )
                        ui.notify(f"Quesito 9.3.1 salvo com sucesso! ({pts} pts)", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 9.3.1", on_click=salvar_931).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("9.3.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 10.0 (Seleção Única - Realização da Coleta de Lixo Doméstico)
                # =============================================================================
                opcoes_100 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – -100 pts (perde 100 pontos)": -100.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.0",
                    titulo="Coleta de Lixo Doméstico (Resíduos Domiciliares)",
                    pergunta="É realizada a coleta de lixo doméstico (resíduos domiciliares)? (Resíduos originários de atividades domésticas em residências urbanas)",
                    opcoes=opcoes_100,
                    placeholder_link="Insira o link do contrato, edital ou página oficial sobre o serviço de coleta...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 10.1 (Seleção Única - Programação da Coleta)
                # =============================================================================
                opcoes_101 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – -30 pts (perde 30 pontos)": -30.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.1",
                    titulo="Programação da Coleta Doméstica",
                    pergunta="A coleta de lixo doméstico (resíduos domiciliares) ocorre de forma programada (determinados os horários e dias da semana)?",
                    opcoes=opcoes_101,
                    placeholder_link="Insira o link do cronograma/itinerário oficial da coleta divulgado à população...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 10.2 (Seleção Única - Abrangência do Atendimento)
                # =============================================================================
                opcoes_102 = {
                    "Selecione...": 0.0,
                    "Todos os bairros do município são atendidos – 00 pts": 0.0,
                    "A maior parte dos bairros são atendidos – -10 pts (perde 10 pontos)": -10.0,
                    "A menor parte dos bairros são atendidos – -30 pts (perde 30 pontos)": -30.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.2",
                    titulo="Abrangência da Coleta Doméstica",
                    pergunta="Todas as regiões do município são atendidas pela coleta de lixo doméstico? (Inclusive zona rural e periferia)",
                    opcoes=opcoes_102,
                    placeholder_link="Insira o link de mapas de rotas ou relatórios de cobertura do serviço...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 10.3 (Seleção Única - Existência de ATT)
                # =============================================================================
                opcoes_103 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.3",
                    titulo="Área de Transbordo e Triagem (ATT)",
                    pergunta="Existe Área de Transbordo e Triagem (ATT) para os Resíduos Sólidos Urbanos no município?",
                    opcoes=opcoes_103,
                    placeholder_link="Insira o link de cadastro ou comprovação da ATT no município...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 10.3.1 (Seleção Única - Licença CETESB para ATT)
                # =============================================================================
                opcoes_1031 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – -50 pts (perde 50 pontos)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="10.3.1",
                    titulo="Licença de Operação CETESB da ATT",
                    pergunta="Existe licença de operação da CETESB para a Área de Transbordo e Triagem (ATT) de Resíduos Sólidos Urbanos?",
                    opcoes=opcoes_1031,
                    placeholder_link="Insira o link do documento da Licença de Operação emitida pela CETESB...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 10.3.1.1 (Validade da Licença CETESB da ATT)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("10.3.1.1 • Prazo de Validade da Licença de Operação da ATT").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o prazo de validade da licença de operação da ATT:").classes("text-base font-bold text-black mb-1")
                    ui.label("⚠️ Regra de Pontuação: Se Data <= 31/12/2024 perde 50 pontos (-50.0). Se Data > 31/12/2024 não perde pontos (0.0).").classes("text-xs font-semibold text-red-500 mb-6")

                    d10311 = res_data.get("10.3.1.1") or {}
                    data_salva_10311 = d10311.get("valor", "")

                    with ui.grid(columns=2).classes("w-full gap-6 items-center mb-4"):
                        input_data_10311 = ui.input(
                            label="Data de Validade (DD/MM/AAAA):",
                            value=data_salva_10311,
                            placeholder="Ex: 30/06/2025"
                        ).classes("w-full").props("outlined mask='##/##/####'")

                    ui.textarea(
                        label="Link de Evidência / Documento da Licença CETESB:",
                        value=d10311.get("link", ""),
                        placeholder="Insira o link da licença em PDF ou no sistema de consulta da CETESB..."
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(d10311, "link")

                    def calc_pts_10311(data_str):
                        if not data_str or len(data_str) < 10:
                            return 0.0
                        try:
                            partes = data_str.split("/")
                            if len(partes) == 3:
                                dia, mes, ano = int(partes[0]), int(partes[1]), int(partes[2])
                                # Data limite: 31/12/2024
                                if (ano < 2024) or (ano == 2024 and mes <= 12 and dia <= 31):
                                    return -50.0
                                else:
                                    return 0.0
                        except Exception:
                            pass
                        return 0.0

                    def salvar_10311():
                        val_data = input_data_10311.value or ""
                        pts = calc_pts_10311(val_data)
                        save_resposta(
                            ano=ano_sel,
                            qid="10.3.1.1",
                            valor=val_data,
                            pontos=pts,
                            link=d10311.get("link", ""),
                            comentarios=d10311.get("comentarios", []),
                            status=d10311.get("status", "Pendente")
                        )
                        ui.notify(f"Quesito 10.3.1.1 salvo! Impacto de pontuação: {pts} pts", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 10.3.1.1", on_click=salvar_10311).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("10.3.1.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 11.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_110 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.0",
                    titulo="Plano de Gerenciamento de Resíduos da Construção Civil (PGRCC)",
                    pergunta="A prefeitura possui Plano de Gerenciamento de Resíduos da Construção Civil (PGRCC) elaborado e implantado de acordo com a resolução CONAMA 307/2002 e suas alterações?",
                    opcoes=opcoes_110,
                    placeholder_link="Insira a cópia ou página de publicação do PGRCC...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.1 (Campos de Texto para Instrumento Normativo)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.1 • Instrumento Normativo do PGRCC").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Preencha as informações do ato legal e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d111 = res_data.get("11.1") or {}
                    raw_val_111 = str(d111.get("valor") or "")
                    
                    norma_i, num_i, data_i = "", "", ""
                    if "|NUM:" in raw_val_111 and "|DATA:" in raw_val_111:
                        partes_111 = raw_val_111.split("|NUM:")
                        norma_i = partes_111[0]
                        num_i, data_i = partes_111[1].split("|DATA:")

                    state_111 = {
                        "norma": norma_i,
                        "numero": num_i,
                        "data": data_i,
                        "link": d111.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input("Instrumento Normativo (Ex: Lei Municipal, Decreto):", value=norma_i, placeholder="Ex: Decreto Municipal").classes("w-full").props("outlined").bind_value(state_111, "norma")
                            ui.input("Número do Instrumento:", value=num_i, placeholder="Ex: nº 1.234/2021").classes("w-full").props("outlined").bind_value(state_111, "numero")
                            ui.input("Data da Publicação:", value=data_i, placeholder="Ex: 10/05/2021").classes("w-full").props("outlined").bind_value(state_111, "data")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_111["link"],
                            placeholder="Insira a cópia do Diário Oficial ou link do documento na íntegra...",
                        ).classes("w-full").props("outlined rows=6").bind_value(state_111, "link")

                    def salvar_111():
                        composite_val = f"{state_111['norma']}|NUM:{state_111['numero']}|DATA:{state_111['data']}"
                        save_resposta(
                            ano=ano_sel,
                            qid="11.1",
                            valor=composite_val,
                            pontos=0.0,
                            link=state_111["link"],
                            comentarios=d111.get("comentarios", []),
                            status=d111.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.1", on_click=salvar_111).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 11.2 (Validação de Link vs Texto XYZ)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.2 • Link do Instrumento Normativo do PGRCC").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a página eletrônica (link na internet) do Plano de Gerenciamento de Resíduos da Construção Civil (PGRCC):").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Se não estiver disponível na internet, insira 'XYZ' no campo. (Link válido = 2 pts | XYZ = 0 pts)").classes("text-xs text-gray-400 mb-6")

                    d112 = res_data.get("11.2") or {}
                    val_112_i = str(d112.get("valor") or "").strip()

                    state_112 = {
                        "link_pgrcc": val_112_i if val_112_i else "XYZ",
                        "link_evid": d112.get("link", ""),
                    }

                    def calc_pts_112():
                        txt = state_112["link_pgrcc"].strip()
                        if not txt or txt.upper() == "XYZ":
                            return 0.0
                        return 2.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Link na Internet (ou digite XYZ):",
                                value=state_112["link_pgrcc"],
                                placeholder="https://... ou XYZ",
                            ).classes("w-full").props("outlined").bind_value(state_112, "link_pgrcc")

                        ui.textarea(
                            label="Link de Evidência / Documento Complementar:",
                            value=state_112["link_evid"],
                            placeholder="Insira o link da página no portal da transparência ou diário oficial...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_112, "link_evid")

                    lbl_pts_112 = ui.label(f"📊 Impacto de Pontuação no Quesito 11.2: {calc_pts_112():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def salvar_112():
                        pts = calc_pts_112()
                        save_resposta(
                            ano=ano_sel,
                            qid="11.2",
                            valor=state_112["link_pgrcc"],
                            pontos=pts,
                            link=state_112["link_evid"],
                            comentarios=d112.get("comentarios", []),
                            status=d112.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.2", on_click=salvar_112).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 11.3 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_113 = {
                    "Selecione...": 0.0,
                    "Sim – 30 pts": 30.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.3",
                    titulo="Cronograma com Metas do PGRCC",
                    pergunta="Possui cronograma com as metas a serem cumpridas?",
                    opcoes=opcoes_113,
                    placeholder_link="Insira o link do trecho do PGRCC que contém o cronograma de metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.3.1 (Seleção Múltipla - Metas Previstas)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.3.1 • Metas Previstas no PGRCC").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe quais metas estão previstas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as metas previstas e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d1131 = res_data.get("11.3.1") or {}
                    try:
                        sel_1131_salvos = json.loads(d1131.get("valor", "[]"))
                        if not isinstance(sel_1131_salvos, list): sel_1131_salvos = []
                    except Exception:
                        sel_1131_salvos = []

                    mapa_1131 = {
                        "pev": ("Aumento/melhoria dos Pontos de Entrega Voluntária - PEV", 0.0),
                        "att": ("Aumento/melhoria de Áreas de Transbordo e Triagem - ATT", 0.0),
                        "pontos_viciados": ("Realização de operações de coleta de Resíduos da Construção Civil em 'pontos viciados'", 0.0),
                        "transportadores": ("Cadastro de transportadores de Resíduos da Construção Civil", 0.0),
                        "outro": ("Outro", 0.0),
                    }

                    state_1131 = {k: k in sel_1131_salvos for k in mapa_1131.keys()}
                    state_1131["link"] = d1131.get("link", "")

                    def calc_pts_1131():
                        return sum(peso for k, (_, peso) in mapa_1131.items() if state_1131.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_1131a = [ui.checkbox(rotulo).bind_value(state_1131, k) for k, (rotulo, _) in list(mapa_1131.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_1131b = [ui.checkbox(rotulo).bind_value(state_1131, k) for k, (rotulo, _) in list(mapa_1131.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_1131["link"],
                        placeholder="Insira o link das tabelas ou capítulos do plano que comprovam as metas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_1131, "link")

                    def salvar_1131():
                        selecionados = [k for k in mapa_1131.keys() if state_1131.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="11.3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_1131(),
                            link=state_1131["link"],
                            comentarios=d1131.get("comentarios", []),
                            status=d1131.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.3.1", on_click=salvar_1131).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.3.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 11.3.2 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_1132 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.3.2",
                    titulo="Monitoramento e Avaliação do PGRCC",
                    pergunta="Realiza monitoramento e avaliação das ações e metas?",
                    opcoes=opcoes_1132,
                    placeholder_link="Insira o link de relatórios ou atas do conselho que comprovem o monitoramento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.3.2.1 (Seleção Múltipla - Formas de Monitoramento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.3.2.1 • Formas de Monitoramento e Avaliação do PGRCC").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("De que forma é realizado o monitoramento e avaliação?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d11321 = res_data.get("11.3.2.1") or {}
                    try:
                        sel_11321_salvos = json.loads(d11321.get("valor", "[]"))
                        if not isinstance(sel_11321_salvos, list): sel_11321_salvos = []
                    except Exception:
                        sel_11321_salvos = []

                    mapa_11321 = {
                        "relatorios_anuais": ("Relatórios anuais discutidos e/ou publicados", 0.0),
                        "indicadores": ("Indicadores de eficácia e eficiência", 0.0),
                        "recursos": ("Avaliação de recursos aplicados", 0.0),
                        "outro": ("Outro", 0.0),
                    }

                    state_11321 = {k: k in sel_11321_salvos for k in mapa_11321.keys()}
                    state_11321["link"] = d11321.get("link", "")

                    def calc_pts_11321():
                        return sum(peso for k, (_, peso) in mapa_11321.items() if state_11321.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_11321a = [ui.checkbox(rotulo).bind_value(state_11321, k) for k, (rotulo, _) in list(mapa_11321.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_11321b = [ui.checkbox(rotulo).bind_value(state_11321, k) for k, (rotulo, _) in list(mapa_11321.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_11321["link"],
                        placeholder="Insira o link dos relatórios de indicadores, publicações ou avaliações de recursos...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_11321, "link")

                    def salvar_11321():
                        selecionados = [k for k in mapa_11321.keys() if state_11321.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="11.3.2.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_11321(),
                            link=state_11321["link"],
                            comentarios=d11321.get("comentarios", []),
                            status=d11321.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.3.2.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.3.2.1", on_click=salvar_11321).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.3.2.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 11.3.3 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_1133 = {
                    "Selecione...": 0.0,
                    "Todas as metas foram cumpridas dentro do prazo – 40 pts": 40.0,
                    "A maior parte das metas foram cumpridas dentro do prazo – 30 pts": 30.0,
                    "A menor parte das metas foram cumpridas dentro do prazo – 10 pts": 10.0,
                    "As metas não foram cumpridas dentro do prazo – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.3.3",
                    titulo="Cumprimento das Metas do PGRCC",
                    pergunta="As metas do Plano estão sendo cumpridas no prazo estipulado?",
                    opcoes=opcoes_1133,
                    placeholder_link="Insira o link de evidências ou relatórios do cumprimento das metas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.3.3.1 (Seleção Múltipla - Motivos do Não Cumprimento)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.3.3.1 • Motivos do Não Cumprimento das Metas").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale os motivos pelos quais as metas não estão sendo cumpridas:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d11331 = res_data.get("11.3.3.1") or {}
                    try:
                        sel_11331_salvos = json.loads(d11331.get("valor", "[]"))
                        if not isinstance(sel_11331_salvos, list): sel_11331_salvos = []
                    except Exception:
                        sel_11331_salvos = []

                    mapa_11331 = {
                        "recursos": ("Falta de recursos orçamentários", 0.0),
                        "aprovacao_legislativa": ("Falta de aprovação legislativa", 0.0),
                        "atraso_licitacao": ("Atraso na licitação", 0.0),
                        "nao_realizou_licitacao": ("Não realizou licitação necessária", 0.0),
                        "pessoal": ("Falta de pessoal qualificado", 0.0),
                        "consenso_consorcio": ("Falta de consenso no consórcio intermunicipal", 0.0),
                        "outros": ("Outros", 0.0),
                    }

                    state_11331 = {k: k in sel_11331_salvos for k in mapa_11331.keys()}
                    state_11331["link"] = d11331.get("link", "")

                    def calc_pts_11331():
                        return sum(peso for k, (_, peso) in mapa_11331.items() if state_11331.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_11331a = [ui.checkbox(rotulo).bind_value(state_11331, k) for k, (rotulo, _) in list(mapa_11331.items())[:4]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_11331b = [ui.checkbox(rotulo).bind_value(state_11331, k) for k, (rotulo, _) in list(mapa_11331.items())[4:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_11331["link"],
                        placeholder="Insira o link de documentos ou justificativas referentes aos motivos...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_11331, "link")

                    def salvar_11331():
                        selecionados = [k for k in mapa_11331.keys() if state_11331.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="11.3.3.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_11331(),
                            link=state_11331["link"],
                            comentarios=d11331.get("comentarios", []),
                            status=d11331.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.3.3.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.3.3.1", on_click=salvar_11331).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.3.3.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 11.4 (Seleção Única - Radio Button com Penalidades)
                # =============================================================================
                opcoes_114 = {
                    "Selecione...": 0.0,
                    "Gerador dos resíduos – 00 pts": 0.0,
                    "Prefeitura – -10 pts": -10.0,
                    "Outros – -10 pts": -10.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.4",
                    titulo="Responsável pela Triagem dos Resíduos",
                    pergunta="Quem é o responsável pela triagem dos resíduos da construção civil?",
                    opcoes=opcoes_114,
                    placeholder_link="Insira o link da norma ou documento sobre a responsabilidade da triagem...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.5 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_115 = {
                    "Selecione...": 0.0,
                    "Sim – 10 pts": 10.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.5",
                    titulo="Fiscalização das Atividades de Gerenciamento",
                    pergunta="A Prefeitura realiza fiscalizações das atividades envolvidas no gerenciamento dos resíduos da construção civil?",
                    opcoes=opcoes_115,
                    placeholder_link="Insira o link com relatórios de fiscalização ou atas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.5.1 (Seleção Múltipla - Atividades Fiscalizadas)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.5.1 • Atividades Fiscalizadas").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Em quais atividades são realizadas essas fiscalizações?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as atividades e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d1151 = res_data.get("11.5.1") or {}
                    try:
                        sel_1151_salvos = json.loads(d1151.get("valor", "[]"))
                        if not isinstance(sel_1151_salvos, list): sel_1151_salvos = []
                    except Exception:
                        sel_1151_salvos = []

                    mapa_1151 = {
                        "coleta": ("Coleta", 0.0),
                        "acondicionamento": ("Acondicionamento", 0.0),
                        "transporte": ("Transporte", 0.0),
                        "destinacao_final": ("Destinação / disposição final", 0.0),
                    }

                    state_1151 = {k: k in sel_1151_salvos for k in mapa_1151.keys()}
                    state_1151["link"] = d1151.get("link", "")

                    def calc_pts_1151():
                        return sum(peso for k, (_, peso) in mapa_1151.items() if state_1151.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_1151a = [ui.checkbox(rotulo).bind_value(state_1151, k) for k, (rotulo, _) in list(mapa_1151.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_1151b = [ui.checkbox(rotulo).bind_value(state_1151, k) for k, (rotulo, _) in list(mapa_1151.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_1151["link"],
                        placeholder="Insira o link das evidências das fiscalizações realizadas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_1151, "link")

                    def salvar_1151():
                        selecionados = [k for k in mapa_1151.keys() if state_1151.get(k)]
                        save_resposta(
                            ano=ano_sel,
                            qid="11.5.1",
                            valor=json.dumps(selecionados),
                            pontos=calc_pts_1151(),
                            link=state_1151["link"],
                            comentarios=d1151.get("comentarios", []),
                            status=d1151.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.5.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.5.1", on_click=salvar_1151).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.5.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 11.6 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_116 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.6",
                    titulo="Área de Transbordo e Triagem (ATT)",
                    pergunta="Existe Área de Transbordo e Triagem (ATT) para os Resíduos da Construção Civil no município?",
                    opcoes=opcoes_116,
                    placeholder_link="Insira o link com comprovações ou dados da ATT...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.6.1 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_1161 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="11.6.1",
                    titulo="Licença de Operação da CETESB para ATT",
                    pergunta="Existe licença de operação da CETESB para a Área de Transbordo e Triagem (ATT) de Resíduos da Construção Civil?",
                    opcoes=opcoes_1161,
                    placeholder_link="Insira o link do documento da licença da CETESB...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 11.6.1.1 (Campo de Texto para Validade da Licença)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("11.6.1.1 • Prazo de Validade da Licença CETESB").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o prazo de validade da licença:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite a data de validade da licença e anexe o link de comprovação.").classes("text-xs text-gray-400 mb-6")

                    d11611 = res_data.get("11.6.1.1") or {}
                    val_11611_i = str(d11611.get("valor") or "").strip()

                    state_11611 = {
                        "validade": val_11611_i,
                        "link": d11611.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Prazo de Validade:",
                                value=state_11611["validade"],
                                placeholder="Ex: 15/12/2026",
                            ).classes("w-full").props("outlined").bind_value(state_11611, "validade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_11611["link"],
                            placeholder="Insira o link da licença ambiental ou documento comprobatório...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_11611, "link")

                    def salvar_11611():
                        save_resposta(
                            ano=ano_sel,
                            qid="11.6.1.1",
                            valor=state_11611["validade"],
                            pontos=0.0,
                            link=state_11611["link"],
                            comentarios=d11611.get("comentarios", []),
                            status=d11611.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 11.6.1.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 11.6.1.1", on_click=salvar_11611).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("11.6.1.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 12.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_120 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="12.0",
                    titulo="Processamento de Resíduos Antes do Aterro",
                    pergunta="Antes de aterrar o lixo, o município realiza algum tipo de processamento de resíduos (reciclagem, compostagem, reutilização ou outra forma)?",
                    opcoes=opcoes_120,
                    placeholder_link="Insira o link com comprovações do processamento prévio dos resíduos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 12.1 (Seleção Múltipla - Forma de Processamento de Resíduos)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("12.1 • Forma de Processamento de Resíduos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale qual a forma realizada de processamento de resíduos:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções praticadas e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d121 = res_data.get("12.1") or {}
                    try:
                        sel_121_salvos = json.loads(d121.get("valor", "[]"))
                        if not isinstance(sel_121_salvos, list): sel_121_salvos = []
                    except Exception:
                        sel_121_salvos = []

                    mapa_121 = {
                        "reciclagem": ("Reciclagem – 04 pts", 4.0),
                        "compostagem": ("Compostagem – 20 pts", 20.0),
                        "reutilizacao": ("Reutilização – 20 pts", 20.0),
                        "logistica_reversa": ("Sistema de Logística Reversa – 10 pts", 10.0),
                        "outro": ("Outro – 00 pts", 0.0),
                    }

                    state_121 = {k: k in sel_121_salvos for k in mapa_121.keys()}
                    state_121["link"] = d121.get("link", "")

                    def calc_pts_121():
                        return sum(peso for k, (_, peso) in mapa_121.items() if state_121.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_121a = [ui.checkbox(rotulo).bind_value(state_121, k) for k, (rotulo, _) in list(mapa_121.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_121b = [ui.checkbox(rotulo).bind_value(state_121, k) for k, (rotulo, _) in list(mapa_121.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_121["link"],
                        placeholder="Insira o link das comprovações das formas de processamento selecionadas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_121, "link")

                    lbl_pts_121 = ui.label(f"📊 Impacto de Pontuação no Quesito 12.1: {calc_pts_121():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def salvar_121():
                        selecionados = [k for k in mapa_121.keys() if state_121.get(k)]
                        pts = calc_pts_121()
                        save_resposta(
                            ano=ano_sel,
                            qid="12.1",
                            valor=json.dumps(selecionados),
                            pontos=pts,
                            link=state_121["link"],
                            comentarios=d121.get("comentarios", []),
                            status=d121.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 12.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 12.1", on_click=salvar_121).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("12.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 13.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_130 = {
                    "Selecione...": 0.0,
                    "Sim": 0.0,
                    "Não": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.0",
                    titulo="Aterro para Resíduos Sólidos Urbanos no Município",
                    pergunta="Existe aterro para os resíduos sólidos urbanos (lixo doméstico e limpeza urbana) no município?",
                    opcoes=opcoes_130,
                    placeholder_link="Insira o link comprobatório referente à presença ou ausência do aterro no município...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 13.1 (Seleção Múltipla com Pontuação Penalizativa / Desconto)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("13.1 • Características do Local de Destinação Final dos RSU (Aterro)").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale as características do local de destinação final dos resíduos sólidos urbanos do município (aterro):").classes("text-base font-bold text-black mb-1")
                    ui.label("⚠️ Regra de Pontuação: Para cada opção NÃO assinalada (exceto 'Outros'), perde-se 5 pontos (Pontuação Máxima de Perda: -110 pts).").classes("text-xs text-amber-600 font-semibold mb-6")

                    d131 = res_data.get("13.1") or {}
                    try:
                        sel_131_salvos = json.loads(d131.get("valor", "[]"))
                        if not isinstance(sel_131_salvos, list): sel_131_salvos = []
                    except Exception:
                        sel_131_salvos = []

                    # Dicionário mapeando chave: (Rótulo, Penalizável_se_não_marcado)
                    mapa_131 = {
                        "local_planejado": ("Local da instalação foi planejado", True),
                        "capacidade_definida": ("Capacidade do local é definida", True),
                        "celulas_individuais": ("Há desenvolvimento de células individuais", True),
                        "impermeabilizacao_solo": ("Impermeabilização do solo", True),
                        "gestao_chorume": ("Total gestão do chorume", True),
                        "gestao_gases": ("Total gestão dos gases", True),
                        "cobertura_solo": ("Aplicação diária de camadas intermediárias e finais - cobertura do solo", True),
                        "compactacao_residuos": ("Há compactação dos resíduos", True),
                        "protecao_vegetal": ("Há proteção vegetal (manutenção do paisagismo sobre as células de resíduos)", True),
                        "vias_acesso": ("Há desenvolvimento e manutenção das vias de acesso do aterro", True),
                        "cercas_muros": ("Há cercas/muros ao redor do local do aterro", True),
                        "controle_acesso": ("Há controle de acesso ao local do aterro", True),
                        "controle_quantitativo": ("Controle total do quantitativo de resíduos que entram no aterro", True),
                        "controle_procedencia": ("Controle total da procedência dos resíduos que entram no aterro", True),
                        "controle_composicao": ("Controle total da composição dos resíduos que entram no aterro", True),
                        "sem_catadores": ("Não há coleta de resíduos por catadores dentro do aterro", True),
                        "sem_comercio": ("Não há comércio de resíduos dentro do aterro", True),
                        "sem_animais": ("Não há presença de animais domésticos e/ou animais silvestres (urubus, garças, etc.)", True),
                        "sem_odores_moscas": ("Não há odores nem presença de moscas", True),
                        "sem_queima": ("Não há queima de resíduos dentro do aterro", True),
                        "data_fechamento": ("Conhecimento da data provável de fechamento do aterro", True),
                        "previsao_pos_fechamento": ("Previsão de gerenciamento do aterro pós-fechamento", True),
                        "outros": ("Outros", False),
                    }

                    state_131 = {k: k in sel_131_salvos for k in mapa_131.keys()}
                    state_131["link"] = d131.get("link", "")

                    def calc_pts_131():
                        # Conta quantas opções penalizáveis NÃO foram assinaladas
                        nao_marcadas = sum(
                            1 for k, (_, penalizavel) in mapa_131.items()
                            if penalizavel and not state_131.get(k)
                        )
                        return -(nao_marcadas * 5.0)

                    # Divisão das opções em duas colunas verticais
                    itens_131 = list(mapa_131.items())
                    meio_131 = (len(itens_131) + 1) // 2

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            for k, (rotulo, _) in itens_131[:meio_131]:
                                ui.checkbox(rotulo).bind_value(state_131, k)
                        with ui.column().classes("w-full gap-1"):
                            for k, (rotulo, _) in itens_131[meio_131:]:
                                ui.checkbox(rotulo).bind_value(state_131, k)

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_131["link"],
                        placeholder="Insira o link das comprovações das características do aterro...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_131, "link")

                    lbl_pts_131 = ui.label(f"📊 Pontuação de Penalização / Impacto no Quesito 13.1: {calc_pts_131():.1f} pontos").classes("text-sm font-bold text-red-600 my-2")

                    def salvar_131():
                        selecionados = [k for k in mapa_131.keys() if state_131.get(k)]
                        pts = calc_pts_131()
                        save_resposta(
                            ano=ano_sel,
                            qid="13.1",
                            valor=json.dumps(selecionados),
                            pontos=pts,
                            link=state_131["link"],
                            comentarios=d131.get("comentarios", []),
                            status=d131.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 13.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 13.1", on_click=salvar_131).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("13.1", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 13.1.1 (Campo de Texto - Data de Fechamento do Aterro)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("13.1.1 • Data Provável de Fechamento do Aterro").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a data provável de fechamento do aterro:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite a data ou previsão e forneça o link da evidência.").classes("text-xs text-gray-400 mb-6")

                    d1311 = res_data.get("13.1.1") or {}
                    val_1311_i = str(d1311.get("valor") or "").strip()

                    state_1311 = {
                        "data_fechamento": val_1311_i,
                        "link": d1311.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Data Provável de Fechamento:",
                                value=state_1311["data_fechamento"],
                                placeholder="Ex: 31/12/2030",
                            ).classes("w-full").props("outlined").bind_value(state_1311, "data_fechamento")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1311["link"],
                            placeholder="Insira o link de relatórios ou estudos de vida útil do aterro...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1311, "link")

                    def salvar_1311():
                        save_resposta(
                            ano=ano_sel,
                            qid="13.1.1",
                            valor=state_1311["data_fechamento"],
                            pontos=0.0,
                            link=state_1311["link"],
                            comentarios=d1311.get("comentarios", []),
                            status=d1311.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 13.1.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 13.1.1", on_click=salvar_1311).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("13.1.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 13.2 (Seleção Única - Radio Button com Penalidade)
                # =============================================================================
                opcoes_132 = {
                    "Selecione...": 0.0,
                    "Sim – 00 pts": 0.0,
                    "Não – -50 pts": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="13.2",
                    titulo="Licença de Operação da CETESB para o Aterro",
                    pergunta="Existe licença de operação da CETESB para a área de aterro?",
                    opcoes=opcoes_132,
                    placeholder_link="Insira o link da licença de operação da CETESB...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 13.2.1 (Campo de Texto - Validade da Licença com Regra de Pontuação)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("13.2.1 • Prazo de Validade da Licença do Aterro").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o prazo de validade da licença:").classes("text-base font-bold text-black mb-1")
                    ui.label("⚠️ Regra de Pontuação: Se Data <= 31/12/2024 -> perde 50 pontos (-50 pts). Se Data > 31/12/2024 -> 00 pts.").classes("text-xs text-amber-600 font-semibold mb-6")

                    d1321 = res_data.get("13.2.1") or {}
                    val_1321_i = str(d1321.get("valor") or "").strip()

                    state_1321 = {
                        "validade": val_1321_i,
                        "link": d1321.get("link", ""),
                    }

                    def calc_pts_1321(data_str):
                        try:
                            # Tenta fazer o parse da data no formato DD/MM/AAAA
                            partes = data_str.strip().split("/")
                            if len(partes) == 3:
                                dt = datetime.date(int(partes[2]), int(partes[1]), int(partes[0]))
                                dt_limite = datetime.date(2024, 12, 31)
                                if dt <= dt_limite:
                                    return -50.0
                                else:
                                    return 0.0
                        except Exception:
                            pass
                        return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Prazo de Validade (DD/MM/AAAA):",
                                value=state_1321["validade"],
                                placeholder="Ex: 31/12/2025",
                            ).classes("w-full").props("outlined").bind_value(state_1321, "validade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1321["link"],
                            placeholder="Insira o link da licença ambiental...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1321, "link")

                    def salvar_1321():
                        pts = calc_pts_1321(state_1321["validade"])
                        save_resposta(
                            ano=ano_sel,
                            qid="13.2.1",
                            valor=state_1321["validade"],
                            pontos=pts,
                            link=state_1321["link"],
                            comentarios=d1321.get("comentarios", []),
                            status=d1321.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 13.2.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 13.2.1", on_click=salvar_1321).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("13.2.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 14.0 (Seleção Única - Radio Button com Penalidade)
                # =============================================================================
                opcoes_140 = {
                    "Selecione...": 0.0,
                    "Sim – -30 pts": -30.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="14.0",
                    titulo="Pontos de Descarte Irregular de Lixo",
                    pergunta="Existem pontos de descarte irregular de lixo no município (lixo doméstico, saúde e/ou construção civil)?",
                    opcoes=opcoes_140,
                    placeholder_link="Insira o link de relatórios, mapeamentos ou denúncias...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 14.1 (Campo de Texto - Quantidade de Pontos Identificados)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("14.1 • Quantidade de Pontos Identificados").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a quantidade de pontos identificados:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Informe o número total de pontos de descarte irregular mapeados.").classes("text-xs text-gray-400 mb-6")

                    d141 = res_data.get("14.1") or {}
                    val_141_i = str(d141.get("valor") or "").strip()

                    state_141 = {
                        "quantidade": val_141_i,
                        "link": d141.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Quantidade de Pontos:",
                                value=state_141["quantidade"],
                                placeholder="Ex: 5",
                            ).classes("w-full").props("outlined").bind_value(state_141, "quantidade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_141["link"],
                            placeholder="Insira o link com o mapeamento dos pontos...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_141, "link")

                    def salvar_141():
                        save_resposta(
                            ano=ano_sel,
                            qid="14.1",
                            valor=state_141["quantidade"],
                            pontos=0.0,
                            link=state_141["link"],
                            comentarios=d141.get("comentarios", []),
                            status=d141.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 14.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 14.1", on_click=salvar_141).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("14.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 14.2 (Campo de Texto Extenso - Endereço dos Locais)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("14.2 • Endereço dos Locais Identificados").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o endereço dos locais identificados:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Liste os endereços ou coordenadas dos pontos de descarte irregular.").classes("text-xs text-gray-400 mb-6")

                    d142 = res_data.get("14.2") or {}
                    val_142_i = str(d142.get("valor") or "").strip()

                    state_142 = {
                        "enderecos": val_142_i,
                        "link": d142.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        ui.textarea(
                            label="Endereços dos Locais:",
                            value=state_142["enderecos"],
                            placeholder="Ex: Rua A, Bairro X; Av. B, próximo ao nº 100...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_142, "enderecos")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_142["link"],
                            placeholder="Insira o link das fotos, mapa ou relatório...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_142, "link")

                    def salvar_142():
                        save_resposta(
                            ano=ano_sel,
                            qid="14.2",
                            valor=state_142["enderecos"],
                            pontos=0.0,
                            link=state_142["link"],
                            comentarios=d142.get("comentarios", []),
                            status=d142.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 14.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 14.2", on_click=salvar_142).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("14.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 14.3 (Seleção Múltipla - Ações de Combate ao Descarte Irregular)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("14.3 • Ações de Combate ao Descarte Irregular").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale as ações promovidas pela Prefeitura para combater o descarte irregular de lixo no ano:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as ações realizadas e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d143 = res_data.get("14.3") or {}
                    try:
                        sel_143_salvos = json.loads(d143.get("valor", "[]"))
                        if not isinstance(sel_143_salvos, list): sel_143_salvos = []
                    except Exception:
                        sel_143_salvos = []

                    mapa_143 = {
                        "campanhas": ("Campanhas de conscientização – 05 pts", 5.0),
                        "mobilizacao_bairro": ("Mobilização de grupos de bairro – 05 pts", 5.0),
                        "retirada_caminhoes": ("Retirada dos resíduos sólidos por caminhões – 05 pts", 5.0),
                        "sinalizacao_proibicao": ("Sinalização no local sobre a proibição de descarte naquele local – 05 pts", 5.0),
                        "plantio_arvores": ("Plantio de árvores em áreas que não deveriam receber lixo ou entulho – 05 pts", 5.0),
                        "notificacoes_multas": ("Notificações e multas aos responsáveis – 05 pts", 5.0),
                    }

                    state_143 = {k: k in sel_143_salvos for k in mapa_143.keys()}
                    state_143["link"] = d143.get("link", "")

                    def calc_pts_143():
                        return sum(peso for k, (_, peso) in mapa_143.items() if state_143.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_143a = [ui.checkbox(rotulo).bind_value(state_143, k) for k, (rotulo, _) in list(mapa_143.items())[:3]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_143b = [ui.checkbox(rotulo).bind_value(state_143, k) for k, (rotulo, _) in list(mapa_143.items())[3:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_143["link"],
                        placeholder="Insira o link de fotos, relatórios ou ordens de serviço das ações realizadas...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_143, "link")

                    lbl_pts_143 = ui.label(f"📊 Impacto de Pontuação no Quesito 14.3: {calc_pts_143():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def salvar_143():
                        selecionados = [k for k in mapa_143.keys() if state_143.get(k)]
                        pts = calc_pts_143()
                        save_resposta(
                            ano=ano_sel,
                            qid="14.3",
                            valor=json.dumps(selecionados),
                            pontos=pts,
                            link=state_143["link"],
                            comentarios=d143.get("comentarios", []),
                            status=d143.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 14.3 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 14.3", on_click=salvar_143).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("14.3", res_data, render_conteudo.refresh)

    # =============================================================================
                # QUESITO 15.0 (Seleção Única - Radio Button)
                # =============================================================================
                opcoes_150 = {
                    "Selecione...": 0.0,
                    "Sim – 02 pts": 2.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="15.0",
                    titulo="Entidade Responsável pela Regulação e Fiscalização",
                    pergunta="O Município definiu a entidade responsável pela regulação e fiscalização dos serviços públicos de saneamento básico?",
                    opcoes=opcoes_150,
                    placeholder_link="Insira o link da norma, decreto ou convênio de delegação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 15.1 (Seleção Múltipla - Serviços com Entidade Reguladora)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("15.1 • Serviços com Entidade Responsável pela Regulação e Fiscalização").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Assinale quais os serviços que possuem entidade responsável pela regulação e fiscalização:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Selecione as opções aplicáveis e clique no botão de salvar.").classes("text-xs text-gray-400 mb-6")

                    d151 = res_data.get("15.1") or {}
                    try:
                        sel_151_salvos = json.loads(d151.get("valor", "[]"))
                        if not isinstance(sel_151_salvos, list): sel_151_salvos = []
                    except Exception:
                        sel_151_salvos = []

                    mapa_151 = {
                        "agua_potavel": ("Abastecimento de água potável – 01 pt", 1.0),
                        "esgotamento_sanitario": ("Esgotamento sanitário – 01 pt", 1.0),
                        "limpeza_residuos": ("Limpeza urbana e manejo de resíduos sólidos – 01 pt", 1.0),
                        "drenagem_aguas_pluviais": ("Drenagem e manejo das águas pluviais urbanas – 00 pts", 0.0),
                    }

                    state_151 = {k: k in sel_151_salvos for k in mapa_151.keys()}
                    state_151["link"] = d151.get("link", "")

                    def calc_pts_151():
                        return sum(peso for k, (_, peso) in mapa_151.items() if state_151.get(k))

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-1"):
                            cb_col_151a = [ui.checkbox(rotulo).bind_value(state_151, k) for k, (rotulo, _) in list(mapa_151.items())[:2]]
                        with ui.column().classes("w-full gap-1"):
                            cb_col_151b = [ui.checkbox(rotulo).bind_value(state_151, k) for k, (rotulo, _) in list(mapa_151.items())[2:]]

                    ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=state_151["link"],
                        placeholder="Insira o link das comprovações da regulação dos serviços...",
                    ).classes("w-full mb-2").props("outlined rows=4").bind_value(state_151, "link")

                    lbl_pts_151 = ui.label(f"📊 Impacto de Pontuação no Quesito 15.1: {calc_pts_151():.1f} pontos").classes("text-sm font-bold text-green-600 my-2")

                    def salvar_151():
                        selecionados = [k for k in mapa_151.keys() if state_151.get(k)]
                        pts = calc_pts_151()
                        save_resposta(
                            ano=ano_sel,
                            qid="15.1",
                            valor=json.dumps(selecionados),
                            pontos=pts,
                            link=state_151["link"],
                            comentarios=d151.get("comentarios", []),
                            status=d151.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 15.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 15.1", on_click=salvar_151).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("15.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 15.1.1 (Campo de Texto - Entidade de Água Potável)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("15.1.1 • Entidade Reguladora de Abastecimento de Água Potável").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a entidade responsável pela regulação e fiscalização do abastecimento de água potável do município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite o nome/sigla da entidade e insira o link de comprovação.").classes("text-xs text-gray-400 mb-6")

                    d1511 = res_data.get("15.1.1") or {}
                    val_1511_i = str(d1511.get("valor") or "").strip()

                    state_1511 = {
                        "entidade": val_1511_i,
                        "link": d1511.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Entidade Responsável:",
                                value=state_1511["entidade"],
                                placeholder="Ex: ARSESP, ARES-PCJ, etc.",
                            ).classes("w-full").props("outlined").bind_value(state_1511, "entidade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1511["link"],
                            placeholder="Insira o link de contrato ou convênio com a agência...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1511, "link")

                    def salvar_1511():
                        save_resposta(
                            ano=ano_sel,
                            qid="15.1.1",
                            valor=state_1511["entidade"],
                            pontos=0.0,
                            link=state_1511["link"],
                            comentarios=d1511.get("comentarios", []),
                            status=d1511.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 15.1.1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 15.1.1", on_click=salvar_1511).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("15.1.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 15.1.2 (Campo de Texto - Entidade de Esgotamento Sanitário)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("15.1.2 • Entidade Reguladora de Esgotamento Sanitário").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a entidade responsável pela regulação e fiscalização do esgotamento sanitário do município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite o nome/sigla da entidade e insira o link de comprovação.").classes("text-xs text-gray-400 mb-6")

                    d1512 = res_data.get("15.1.2") or {}
                    val_1512_i = str(d1512.get("valor") or "").strip()

                    state_1512 = {
                        "entidade": val_1512_i,
                        "link": d1512.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Entidade Responsável:",
                                value=state_1512["entidade"],
                                placeholder="Ex: ARSESP, ARES-PCJ, etc.",
                            ).classes("w-full").props("outlined").bind_value(state_1512, "entidade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1512["link"],
                            placeholder="Insira o link de contrato ou convênio com a agência...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1512, "link")

                    def salvar_1512():
                        save_resposta(
                            ano=ano_sel,
                            qid="15.1.2",
                            valor=state_1512["entidade"],
                            pontos=0.0,
                            link=state_1512["link"],
                            comentarios=d1512.get("comentarios", []),
                            status=d1512.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 15.1.2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 15.1.2", on_click=salvar_1512).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("15.1.2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 15.1.3 (Campo de Texto - Entidade de Limpeza Urbana e Resíduos)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("15.1.3 • Entidade Reguladora de Limpeza Urbana e Resíduos Sólidos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a entidade responsável pela regulação e fiscalização de limpeza urbana e manejo de resíduos sólidos do município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite o nome/sigla da entidade e insira o link de comprovação.").classes("text-xs text-gray-400 mb-6")

                    d1513 = res_data.get("15.1.3") or {}
                    val_1513_i = str(d1513.get("valor") or "").strip()

                    state_1513 = {
                        "entidade": val_1513_i,
                        "link": d1513.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Entidade Responsável:",
                                value=state_1513["entidade"],
                                placeholder="Ex: Secretaria Municipal de Meio Ambiente, Consórcio, etc.",
                            ).classes("w-full").props("outlined").bind_value(state_1513, "entidade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1513["link"],
                            placeholder="Insira o link do documento legal de regulação...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1513, "link")

                    def salvar_1513():
                        save_resposta(
                            ano=ano_sel,
                            qid="15.1.3",
                            valor=state_1513["entidade"],
                            pontos=0.0,
                            link=state_1513["link"],
                            comentarios=d1513.get("comentarios", []),
                            status=d1513.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 15.1.3 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 15.1.3", on_click=salvar_1513).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("15.1.3", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 15.1.4 (Campo de Texto - Entidade de Drenagem e Águas Pluviais)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("15.1.4 • Entidade Reguladora de Drenagem e Águas Pluviais").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe a entidade responsável pela regulação e fiscalização de drenagem e manejo das águas pluviais urbanas do município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Digite o nome/sigla da entidade e insira o link de comprovação.").classes("text-xs text-gray-400 mb-6")

                    d1514 = res_data.get("15.1.4") or {}
                    val_1514_i = str(d1514.get("valor") or "").strip()

                    state_1514 = {
                        "entidade": val_1514_i,
                        "link": d1514.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Entidade Responsável:",
                                value=state_1514["entidade"],
                                placeholder="Ex: Secretaria Municipal de Obras/Serviços Urbanos, etc.",
                            ).classes("w-full").props("outlined").bind_value(state_1514, "entidade")

                        ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=state_1514["link"],
                            placeholder="Insira o link do documento legal de atribuição...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_1514, "link")

                    def salvar_1514():
                        save_resposta(
                            ano=ano_sel,
                            qid="15.1.4",
                            valor=state_1514["entidade"],
                            pontos=0.0,
                            link=state_1514["link"],
                            comentarios=d1514.get("comentarios", []),
                            status=d1514.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 15.1.4 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 15.1.4", on_click=salvar_1514).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("15.1.4", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 16.0 (Campo de Texto Extenso - Impressões, Comentários e Sugestões)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("16.0 • Impressões, Comentários e Sugestões").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Gostaria de registrar suas impressões, comentários e sugestões a respeito do presente questionário?").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Utilize o espaço abaixo para registrar suas impressões, comentários e sugestões.").classes("text-xs text-gray-400 mb-6")

                    d160 = res_data.get("16.0") or {}
                    val_160_i = str(d160.get("valor") or "").strip()

                    state_160 = {
                        "comentarios_sugestoes": val_160_i,
                        "link": d160.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        ui.textarea(
                            label="Impressões / Comentários / Sugestões:",
                            value=state_160["comentarios_sugestoes"],
                            placeholder="Insira aqui seus comentários e sugestões a respeito do questionário...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_160, "comentarios_sugestoes")

                        ui.textarea(
                            label="Link de Evidência / Anexo (Opcional):",
                            value=state_160["link"],
                            placeholder="Insira o link de documentos complementares se houver...",
                        ).classes("w-full").props("outlined rows=5").bind_value(state_160, "link")

                    def salvar_160():
                        save_resposta(
                            ano=ano_sel,
                            qid="16.0",
                            valor=state_160["comentarios_sugestoes"],
                            pontos=0.0,
                            link=state_160["link"],
                            comentarios=d160.get("comentarios", []),
                            status=d160.get("status", "Pendente"),
                        )
                        ui.notify("Quesito 16.0 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO 16.0", on_click=salvar_160).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("16.0", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO A1 (Indicador ICTEM - CETESB)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("A1 • ICTEM - Indicador de Coleta e Tratabilidade de Esgoto").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o ICTEM - Indicador de Coleta de Tratabilidade de Esgoto da População Urbana do Município:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Dados da CETESB. Não sujeitos à validação.").classes("text-xs text-gray-500 font-semibold mb-1")
                    ui.label("⚠️ Regra de Pontuação: ICTEM >= 7,5 -> 0 pts | 5,0 < ICTEM < 7,5 -> -50 pts | 2,5 < ICTEM <= 5,0 -> -150 pts | ICTEM <= 2,5 -> -200 pts").classes("text-xs text-amber-600 font-semibold mb-6")

                    dA1 = res_data.get("A1") or {}
                    val_A1_i = str(dA1.get("valor") or "").strip()

                    state_A1 = {
                        "ictem": val_A1_i,
                        "link": dA1.get("link", ""),
                    }

                    def calc_pts_A1(valor_str):
                        try:
                            # Converte vírgula para ponto caso o usuário digite no formato brasileiro
                            val = float(valor_str.replace(",", "."))
                            if val >= 7.5:
                                return 0.0
                            elif val > 5.0:
                                return -50.0
                            elif val > 2.5:
                                return -150.0
                            else:
                                return -200.0
                        except Exception:
                            return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Valor do ICTEM (ex: 8.5):",
                                value=state_A1["ictem"],
                                placeholder="Digite o valor do ICTEM...",
                            ).classes("w-full").props("outlined").bind_value(state_A1, "ictem")

                        ui.textarea(
                            label="Link de Evidência / Fonte CETESB:",
                            value=state_A1["link"],
                            placeholder="Insira o link da publicação/relatório da CETESB...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_A1, "link")

                    lbl_pts_A1 = ui.label(f"📊 Impacto de Pontuação no Quesito A1: {calc_pts_A1(state_A1['ictem']):.1f} pontos").classes("text-sm font-bold text-red-600 my-2")

                    def salvar_A1():
                        pts = calc_pts_A1(state_A1["ictem"])
                        save_resposta(
                            ano=ano_sel,
                            qid="A1",
                            valor=state_A1["ictem"],
                            pontos=pts,
                            link=state_A1["link"],
                            comentarios=dA1.get("comentarios", []),
                            status=dA1.get("status", "Pendente"),
                        )
                        ui.notify("Quesito A1 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO A1", on_click=salvar_A1).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("A1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO A2 (Índice IQR - Qualidade de Aterro de Resíduos - CETESB)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("A2 • IQR - Índice de Qualidade de Aterro de Resíduos").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Utilização do IQR (Índice de Qualidade de Aterro de Resíduos):").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Dados da CETESB. Não sujeitos à validação.").classes("text-xs text-gray-500 font-semibold mb-1")
                    ui.label("⚠️ Regra de Pontuação: Condições adequadas -> 00 pts | Condições inadequadas -> Rebaixar i-Amb 1 Faixa").classes("text-xs text-amber-600 font-semibold mb-6")

                    dA2 = res_data.get("A2") or {}
                    val_A2_i = str(dA2.get("valor") or "Selecione...").strip()

                    opcoes_A2 = {
                        "Selecione...": 0.0,
                        "Condições adequadas - 00 pts": 0.0,
                        "Condições inadequadas - Rebaixar i-Amb 1 Faixa": 0.0,  # A penalidade de faixa é tratada na consolidação dos índices
                    }

                    state_A2 = {
                        "valor": val_A2_i if val_A2_i in opcoes_A2 else "Selecione...",
                        "link": dA2.get("link", ""),
                    }

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.select(
                                label="Condição do IQR:",
                                options=list(opcoes_A2.keys()),
                                value=state_A2["valor"],
                            ).classes("w-full").props("outlined").bind_value(state_A2, "valor")

                        ui.textarea(
                            label="Link de Evidência / Fonte CETESB:",
                            value=state_A2["link"],
                            placeholder="Insira o link da avaliação do IQR da CETESB...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_A2, "link")

                    def salvar_A2():
                        pts = opcoes_A2.get(state_A2["valor"], 0.0)
                        save_resposta(
                            ano=ano_sel,
                            qid="A2",
                            valor=state_A2["valor"],
                            pontos=pts,
                            link=state_A2["link"],
                            comentarios=dA2.get("comentarios", []),
                            status=dA2.get("status", "Pendente"),
                        )
                        ui.notify("Quesito A2 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO A2", on_click=salvar_A2).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("A2", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO A3 (Índice IQT - Estações de Transbordo - CETESB)
                # =============================================================================
                with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                    ui.label("A3 • IQT - Índice de Qualidade de Estações de Transbordo").classes("text-xl font-semibold text-blue-500 mb-3")
                    ui.label("Informe o IQT - Índice de Qualidade de Estações de Transbordo:").classes("text-base font-bold text-black mb-1")
                    ui.label("ℹ Dados da CETESB. Não sujeitos à validação.").classes("text-xs text-gray-500 font-semibold mb-1")
                    ui.label("⚠️ Regra de Pontuação: De 7,1 a 10,0 (Condições adequadas) -> 00 pts | De 0,0 a 7,0 (Condições inadequadas) -> -50 pts").classes("text-xs text-amber-600 font-semibold mb-6")

                    dA3 = res_data.get("A3") or {}
                    val_A3_i = str(dA3.get("valor") or "").strip()

                    state_A3 = {
                        "iqt": val_A3_i,
                        "link": dA3.get("link", ""),
                    }

                    def calc_pts_A3(valor_str):
                        try:
                            val = float(valor_str.replace(",", "."))
                            if val >= 7.1:
                                return 0.0
                            else:
                                return -50.0
                        except Exception:
                            return 0.0

                    with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                        with ui.column().classes("w-full gap-3"):
                            ui.input(
                                "Valor do IQT (0.0 a 10.0):",
                                value=state_A3["iqt"],
                                placeholder="Digite a nota/índice do IQT...",
                            ).classes("w-full").props("outlined").bind_value(state_A3, "iqt")

                        ui.textarea(
                            label="Link de Evidência / Fonte CETESB:",
                            value=state_A3["link"],
                            placeholder="Insira o link do relatório IQT da CETESB...",
                        ).classes("w-full").props("outlined rows=4").bind_value(state_A3, "link")

                    lbl_pts_A3 = ui.label(f"📊 Impacto de Pontuação no Quesito A3: {calc_pts_A3(state_A3['iqt']):.1f} pontos").classes("text-sm font-bold text-red-600 my-2")

                    def salvar_A3():
                        pts = calc_pts_A3(state_A3["iqt"])
                        save_resposta(
                            ano=ano_sel,
                            qid="A3",
                            valor=state_A3["iqt"],
                            pontos=pts,
                            link=state_A3["link"],
                            comentarios=dA3.get("comentarios", []),
                            status=dA3.get("status", "Pendente"),
                        )
                        ui.notify("Quesito A3 salvo com sucesso!", type="positive")
                        if render_conteudo.refresh: render_conteudo.refresh()

                    ui.button("💾 SALVAR QUESITO A3", on_click=salvar_A3).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                    ui.separator().classes("my-2")
                    bloco_comentarios("A3", res_data, render_conteudo.refresh)
    
    # Executa a renderização da interface
    render_conteudo()
                
# Exporta referências principais para o aplicativo
mostrar_formulario_iamb = container_formulario_iamb
main = container_formulario_iamb
