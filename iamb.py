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
                
# Exporta referências principais para o aplicativo
mostrar_formulario_iamb = container_formulario_iamb
main = container_formulario_iamb
