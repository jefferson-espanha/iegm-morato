import base64
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# BANCO DE DADOS (NEON - ESTRUTURA REAL RESPOSTAS_IFISCAL)
# =============================================================================
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def load_respostas(ano):
    query = """
        SELECT id, ano, quesito, resposta, pontos, detalhes
        FROM respostas_ifiscal
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()
                for row in rows:
                    q_id = row["quesito"]
                    val_bruto = row["resposta"] or ""

                    # Tenta converter resposta de JSON caso seja dicionário/lista salvos como string
                    val_final = val_bruto
                    if isinstance(val_bruto, str) and (
                        (val_bruto.startswith("[") and val_bruto.endswith("]")) or 
                        (val_bruto.startswith("{") and val_bruto.endswith("}"))
                    ):
                        try:
                            val_final = json.loads(val_bruto)
                        except Exception:
                            val_final = val_bruto

                    # Extrai link e comentarios dentro do campo JSONB 'detalhes'
                    detalhes_obj = row["detalhes"] or {}
                    if isinstance(detalhes_obj, str):
                        try:
                            detalhes_obj = json.loads(detalhes_obj)
                        except Exception:
                            detalhes_obj = {}

                    link_val = detalhes_obj.get("link", "")
                    if link_val == "EMPTY_STRING":
                        link_val = ""

                    comentarios_val = detalhes_obj.get("comentarios", [])
                    if not isinstance(comentarios_val, list):
                        comentarios_val = []

                    status_val = detalhes_obj.get("status", "Pendente")

                    respostas[q_id] = {
                        "valor": val_final,
                        "pontos": float(row["pontos"]) if row["pontos"] is not None else 0.0,
                        "link": link_val,
                        "comentarios": comentarios_val,
                        "status": status_val,
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas de respostas_ifiscal: {e}")
    return respostas


def save_resposta(
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    link_final = link.strip() if link else ""

    # Serialização do campo 'resposta'
    if isinstance(valor, (list, dict)):
        resposta_str = json.dumps(valor, ensure_ascii=False)
    else:
        resposta_str = str(valor) if valor is not None else ""

    # Estrutura do objeto 'detalhes' (JSONB)
    detalhes_data = {
        "link": link_final,
        "comentarios": comentarios,
        "status": status,
    }

    # Gera um ID único simples combinando Ano e Quesito se necessário
    registro_id = f"{ano}_{qid}"

    query = """
        INSERT INTO respostas_ifiscal (id, ano, quesito, resposta, pontos, detalhes, atualizado_em)
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (ano, quesito) 
        DO UPDATE SET
            resposta = EXCLUDED.resposta,
            pontos = EXCLUDED.pontos,
            detalhes = EXCLUDED.detalhes,
            atualizado_em = CURRENT_TIMESTAMP;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        registro_id,
                        int(ano),
                        str(qid),
                        resposta_str,
                        float(pontos),
                        Json(detalhes_data),
                    ),
                )
                conn.commit()
                print(f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela respostas_ifiscal!")
    except Exception as e:
        print(f"❌ Erro ao salvar resposta na tabela respostas_ifiscal: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_ifiscal WHERE ano = %s;"
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
    opcoes=None,
    tipo_input="radio",
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    if opcoes is None:
        opcoes = {}

    dados_q = res_data.get(qid, {})
    link_atual = dados_q.get("link", "")

    if tipo_input == "checkbox":
        valor_bruto = dados_q.get("valor", [])
        if isinstance(valor_bruto, list):
            valor_atual = valor_bruto
        elif valor_bruto in opcoes:
            valor_atual = [valor_bruto]
        else:
            valor_atual = []
    elif tipo_input in ["number", "float"]:
        valor_bruto = dados_q.get("valor", 0.0)
        try:
            valor_atual = float(valor_bruto)
        except (ValueError, TypeError):
            valor_atual = 0.0
    elif tipo_input == "text":
        valor_atual = str(dados_q.get("valor", ""))
    else:  # radio
        padrao = "Selecione..." if "Selecione..." in opcoes else (list(opcoes.keys())[0] if opcoes else "")
        valor_atual = dados_q.get("valor", padrao)
        if valor_atual not in opcoes and opcoes:
            valor_atual = padrao

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
            if tipo_input == "checkbox":
                with ui.column().classes("gap-2 w-full"):
                    chk_states = {}

                    def make_on_change(opt):
                        def on_change(e):
                            if e.value and opt not in state["opcao"]:
                                state["opcao"].append(opt)
                            elif not e.value and opt in state["opcao"]:
                                state["opcao"].remove(opt)
                            atualizar_impacto()
                        return on_change

                    for opt_key in opcoes.keys():
                        is_checked = opt_key in state["opcao"]
                        chk = ui.checkbox(
                            text=opt_key,
                            value=is_checked,
                            on_change=make_on_change(opt_key)
                        ).props("color=blue")
                        chk_states[opt_key] = chk

            elif tipo_input in ["number", "float"]:
                input_num = (
                    ui.number(
                        label="Pontuação do Quesito:",
                        value=state["opcao"],
                        min=0,
                        max=250,
                        step=0.1,
                    )
                    .classes("w-full")
                    .props("outlined color=blue")
                    .bind_value(state, "opcao")
                )

            elif tipo_input == "text":
                ui.textarea(
                    label="Resposta / Comentário:",
                    value=state["opcao"],
                    placeholder="Digite sua resposta...",
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "opcao"
                )

            else:  # radio
                input_radio = (
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
            ).classes("w-full").props("outlined rows=5").bind_value(
                state, "link"
            )

        def calcular_pontos(opcao_sel):
            if tipo_input in ["number", "float"]:
                try:
                    return float(opcao_sel)
                except (ValueError, TypeError):
                    return 0.0
            elif tipo_input == "text":
                return 0.0
            elif isinstance(opcao_sel, list):
                return sum(opcoes.get(opt, 0.0) for opt in opcao_sel)
            return opcoes.get(opcao_sel, 0.0)

        pts_atuais = calcular_pontos(state["opcao"])
        label_impacto = ui.label(
            f"📊 Impacto de Pontuação no Quesito {qid}: {pts_atuais:.1f} pontos"
        ).classes("text-sm font-bold text-green-600 my-4")

        def atualizar_impacto(e=None):
            novos_pts = calcular_pontos(state["opcao"])
            label_impacto.set_text(
                f"📊 Impacto de Pontuação no Quesito {qid}: {novos_pts:.1f} pontos"
            )

        if tipo_input == "radio":
            input_radio.on("update:model-value", atualizar_impacto)
        elif tipo_input in ["number", "float"]:
            input_num.on("update:model-value", atualizar_impacto)

        def salvar_acao():
            opcao_sel = state["opcao"]
            pts = calcular_pontos(opcao_sel)
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
            "SALVAR RESPOSTA", on_click=salvar_acao
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
        ui.label("🛠️ Painel de Controle (iFiscal)").classes(
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
# MÓDULO PRINCIPAL DE REQUISITOS (IFISCAL)
# =============================================================================
def container_formulario_ifiscal(ano=None):
    if "ano_referencia_global" not in app.storage.user:
        app.storage.user["ano_referencia_global"] = ano if ano else 2026

    main_container = ui.element("div").classes("w-full")

    @ui.refreshable
    def render_conteudo():
        main_container.clear()
        with main_container:
            ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
            res_data = load_respostas(ano_sel)

            def alterar_ano(novo_ano):
                app.storage.user["ano_referencia_global"] = int(novo_ano)
                ui.notify(f"Ano alterado para {novo_ano}", type="info")
                render_conteudo.refresh()

            with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-12 gap-6 items-start"):
                # Coluna 1: Painel Lateral (3/12)
                with ui.element("div").classes("md:col-span-4 lg:col-span-3"):
                    render_painel_controle(
                        ano_atual=ano_sel,
                        on_mudar_ano=alterar_ano,
                        on_refresh=render_conteudo.refresh,
                    )

                # Coluna 2: Formulário Principal (9/12)
                with ui.element("div").classes("md:col-span-8 lg:col-span-9 flex flex-col gap-4"):
                    with ui.card().classes("w-full p-6 border rounded-lg shadow-sm bg-white"):
                        ui.label(f"📋 Módulo i-Fiscal — Ano {ano_sel}").classes(
                            "text-xl font-bold text-slate-800 border-b pb-2"
                        )
                        
                    # ==========================================
                    # QUESITO 1.0
                    # ==========================================
                    opcoes_1_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.0",
                        titulo="Estrutura Administrativa",
                        pergunta="Há estrutura administrativa voltada para a administração tributária?",
                        tipo_input="radio",
                        opcoes=opcoes_1_0,
                        placeholder_link="Insira o link ou documento de comprovação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.1
                    # ==========================================
                    opcoes_1_1 = {
                        "Selecione...": 0.0,
                        "Sim – 0,5": 0.5,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.1",
                        titulo="Lei de Estrutura Organizacional",
                        pergunta="O Município possui lei que defina a estrutura organizacional da Administração Tributária?",
                        tipo_input="radio",
                        opcoes=opcoes_1_1,
                        placeholder_link="Insira o link ou documento da lei...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.2 (Cargos Preenchidos - Quantitativo)
                    # ==========================================
                    dados_1_2 = res_data.get("1.2", {})
                    val_1_2 = dados_1_2.get("valor", {})
                    if not isinstance(val_1_2, dict):
                        val_1_2 = {"efetivo": 0, "comissao": 0, "terceirizado": 0}

                    state_1_2 = {
                        "efetivo": int(val_1_2.get("efetivo", 0)),
                        "comissao": int(val_1_2.get("comissao", 0)),
                        "terceirizado": int(val_1_2.get("terceirizado", 0)),
                        "link": dados_1_2.get("link", ""),
                    }

                    def calc_pts_1_2():
                        ef = state_1_2["efetivo"]
                        co = state_1_2["comissao"]
                        ter = state_1_2["terceirizado"]
                        if ef > 0 and co == 0 and ter == 0:
                            return 1.5
                        return 0.0

                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("1.2 • Número de Cargos de Fiscais/Auditores Tributários Preenchidos").classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label("Qual o número de cargos de fiscais/auditores tributários preenchidos?").classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Fórmula: Se efetivos > 0 E comissão = 0 E terceirizados = 0 -> 1,5 pts | Caso contrário -> 0,0 pts").classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("gap-3 w-full"):
                                num_efetivo = ui.number(
                                    label="Efetivo:",
                                    value=state_1_2["efetivo"],
                                    min=0,
                                    precision=0
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1_2, "efetivo")

                                num_comissao = ui.number(
                                    label="Em comissão:",
                                    value=state_1_2["comissao"],
                                    min=0,
                                    precision=0
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1_2, "comissao")

                                num_terceirizado = ui.number(
                                    label="Terceirizado:",
                                    value=state_1_2["terceirizado"],
                                    min=0,
                                    precision=0
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1_2, "terceirizado")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_1_2["link"],
                                placeholder="Insira o link com a relação de cargos e quadro de pessoal..."
                            ).classes("w-full").props("outlined rows=7").bind_value(state_1_2, "link")

                        pts_1_2 = calc_pts_1_2()
                        label_impacto_1_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.2: {pts_1_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_1_2(e=None):
                            pts_att = calc_pts_1_2()
                            label_impacto_1_2.set_text(f"📊 Impacto de Pontuação no Quesito 1.2: {pts_att:.1f} pontos")

                        num_efetivo.on("update:model-value", atualizar_impacto_1_2)
                        num_comissao.on("update:model-value", atualizar_impacto_1_2)
                        num_terceirizado.on("update:model-value", atualizar_impacto_1_2)

                        def salvar_1_2():
                            valor_dict = {
                                "efetivo": state_1_2["efetivo"],
                                "comissao": state_1_2["comissao"],
                                "terceirizado": state_1_2["terceirizado"],
                            }
                            pts_final = calc_pts_1_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="1.2",
                                valor=valor_dict,
                                pontos=pts_final,
                                link=state_1_2["link"],
                                comentarios=dados_1_2.get("comentarios", []),
                                status=dados_1_2.get("status", "Pendente")
                            )
                            ui.notify("Quesito 1.2 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_1_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 1.3
                    # ==========================================
                    opcoes_1_3 = {
                        "Selecione...": 0.0,
                        "Sim – 10": 10.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.3",
                        titulo="Treinamento de Fiscais Tributários",
                        pergunta="Os fiscais tributários recebem treinamento específico para execução das atividades inerentes ao cargo? (Treinamento periódico pelo menos 1 vez ao ano)",
                        tipo_input="radio",
                        opcoes=opcoes_1_3,
                        placeholder_link="Insira o link ou certificado dos treinamentos...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.4
                    # ==========================================
                    opcoes_1_4 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.4",
                        titulo="Plano de Cargos e Salários Específico",
                        pergunta="O Município possui Plano de Cargos e Salários específico para seus fiscais tributários? (Obs: PCCS geral dos servidores públicos do município não é PCCS específico para os fiscais tributários)",
                        tipo_input="radio",
                        opcoes=opcoes_1_4,
                        placeholder_link="Insira a lei do PCCS específico da categoria...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 1.4.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.4.1",
                        titulo="Instrumento Normativo do PCCS",
                        pergunta="Informe o instrumento normativo de regulamentação do Plano de Cargos e Salários específico para seus fiscais tributários, Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o documento)",
                        tipo_input="text",
                        placeholder_link="Insira o link do documento do PCCS...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.4.2 (Texto / Link)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.4.2",
                        titulo="Divulgação do PCCS na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Plano de Cargos e Salários específico para os fiscais tributários: (Se não estiver disponível na internet, inserir no campo o texto XYZ)",
                        tipo_input="text",
                        placeholder_link="Insira a página eletrônica oficial...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.5 (Radio)
                    # ==========================================
                    opcoes_1_5 = {
                        "Selecione...": 0.0,
                        "Sim – 05": 5.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.5",
                        titulo="Segregação de Funções Tributárias",
                        pergunta="Há segregação de funções entre os setores de lançadoria, arrecadação, fiscalização e contabilidade?",
                        tipo_input="radio",
                        opcoes=opcoes_1_5,
                        placeholder_link="Insira o link ou organograma comprovando a segregação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.5.1 (Radio)
                    # ==========================================
                    opcoes_1_5_1 = {
                        "Selecione...": 0.0,
                        "Sim – 05": 5.0,
                        "Não – 00": 0.0,
                        "Não possui sistema (software) para lançamento, arrecadação ou fiscalização dos tributos – -03": -3.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.5.1",
                        titulo="Segregação de Permissões no Sistema",
                        pergunta="Há segregação nas permissões de acesso do sistema, com identificação do usuário e registro das transações efetuadas?",
                        tipo_input="radio",
                        opcoes=opcoes_1_5_1,
                        placeholder_link="Insira o link ou relatório de perfis/logs do sistema...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 2.0 (Radio)
                    # ==========================================
                    opcoes_2_0 = {
                        "Selecione...": 0.0,
                        "Sim – 04": 4.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="2.0",
                        titulo="Contabilidade - Servidor Efetivo",
                        pergunta="O servidor responsável pela contabilidade do município é ocupante de cargo de provimento efetivo?",
                        tipo_input="radio",
                        opcoes=opcoes_2_0,
                        placeholder_link="Insira o termo de posse ou portaria do contador...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.0 (Radio)
                    # ==========================================
                    opcoes_3_0 = {
                        "Selecione...": 0.0,
                        "Sim – 30": 30.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.0",
                        titulo="Medidas para Aumento da Arrecadação",
                        pergunta="O Município adotou medidas efetivas para aumento da arrecadação?",
                        tipo_input="radio",
                        opcoes=opcoes_3_0,
                        placeholder_link="Insira o link das leis ou relatórios das medidas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.1 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_3_1 = {
                        "Recadastramento de Imóveis": 0.0,
                        "Programas de Recuperação Fiscal": 0.0,
                        "Implementação de Nota Fiscal Eletrônica": 0.0,
                        "Convênios com a União e o Estado para compartilhamento de Informações": 0.0,
                        "Parceria/Convênio com os tabelionatos de notas e Registros de Imóveis": 0.0,
                        "Protesto da Certidão de Dívida Ativa": 0.0,
                        "Convênios com órgãos de proteção ao crédito": 0.0,
                        "Convênio com o Governo Federal para a cobrança do ITR": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.1",
                        titulo="Medidas Implementadas para Arrecadação",
                        pergunta="Assinale as medidas implementadas para aumento da arrecadação:",
                        tipo_input="checkbox",
                        opcoes=opcoes_3_1,
                        placeholder_link="Insira o link dos convênios, decretos e leis que comprovem as medidas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.0 (Radio)
                    # ==========================================
                    opcoes_4_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.0",
                        titulo="Revisão do Cadastro Imobiliário",
                        pergunta="Foi instituído procedimento de revisão do cadastro imobiliário estabelecendo a sua periodicidade? (Obs.: a mera atualização cadastral por solicitação do contribuinte realizada de forma pontual e esporádica não será considerada na questão como revisão periódica e geral).",
                        tipo_input="radio",
                        opcoes=opcoes_4_0,
                        placeholder_link="Insira o link ou documento do procedimento de revisão...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1",
                        titulo="Instrumento Normativo de Revisão do Cadastro",
                        pergunta="Informe o instrumento normativo (número e data da aprovação) e endereço eletrônico de divulgação do procedimento de revisão do cadastro imobiliário:",
                        tipo_input="text",
                        placeholder_link="Insira a página eletrônica / link do instrumento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.2 (Radio)
                    # ==========================================
                    opcoes_4_2 = {
                        "Selecione...": 0.0,
                        "Menor ou igual a 1 ano": 0.0,
                        "Maior que 1 e menor ou igual a 4 anos": 0.0,
                        "Maior que 4 e menor ou igual a 8 anos": 0.0,
                        "Maior que 8 anos": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.2",
                        titulo="Periodicidade da Revisão do Cadastro Imobiliário",
                        pergunta="Qual a periodicidade da revisão geral do Cadastro Imobiliário?",
                        tipo_input="radio",
                        opcoes=opcoes_4_2,
                        placeholder_link="Insira o documento informando a periodicidade...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.3 (Radio)
                    # ==========================================
                    opcoes_4_3 = {
                        "Selecione...": 0.0,
                        "Sim – 05": 5.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.3",
                        titulo="Atualização da Revisão do Cadastro Imobiliário",
                        pergunta="O cadastro imobiliário está com a revisão periódica ou geral atualizada? (Obs.: a mera atualização cadastral por solicitação do contribuinte realizada de forma pontual e esporádica não será considerada na questão).",
                        tipo_input="radio",
                        opcoes=opcoes_4_3,
                        placeholder_link="Insira a evidência da atualização periódica...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.0 (Radio)
                    # ==========================================
                    opcoes_5_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.0",
                        titulo="Planta Genérica de Valores (PGV)",
                        pergunta="O instrumento da Planta Genérica de Valores (PGV) foi aprovado por lei, conforme previsto no Código Tributário Nacional (CTN)?",
                        tipo_input="radio",
                        opcoes=opcoes_5_0,
                        placeholder_link="Insira o link ou documento da lei da PGV...",
                        on_save_callback=render_conteudo.refresh,
                    )

                  
    render_conteudo()
