import ast
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

                    # Desserialização recursiva/segura do campo 'resposta'
                    val_final = val_bruto
                    while isinstance(val_final, str):
                        val_str = val_final.strip()
                        if (val_str.startswith("[") and val_str.endswith("]")) or (
                            val_str.startswith("{") and val_str.endswith("}")
                        ):
                            try:
                                val_final = json.loads(val_str)
                            except Exception:
                                break
                        else:
                            break

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
                        "pontos": float(row["pontos"])
                        if row["pontos"] is not None
                        else 0.0,
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

    # Trata caso o valor venha como dict/list ou como string que precisa ser tratada
    if isinstance(valor, (dict, list)):
        resposta_str = json.dumps(valor, ensure_ascii=False)
    elif isinstance(valor, str):
        # Se já for string JSON, desserializa e grava limpo
        try:
            parsed = json.loads(valor)
            resposta_str = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            resposta_str = valor
    else:
        resposta_str = str(valor) if valor is not None else ""

    detalhes_data = {
        "link": link_final,
        "comentarios": comentarios,
        "status": status,
    }

    registro_id = f"{ano}_{qid}"

    query = """
        INSERT INTO respostas_ifiscal (id, ano, quesito, resposta, pontos, detalhes, atualizado_em)
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
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
            print(
                f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela"
                " respostas_ifiscal!"
            )
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
    if not isinstance(dados_q, dict):
        return []
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
        padrao = (
            "Selecione..."
            if "Selecione..." in opcoes
            else (list(opcoes.keys())[0] if opcoes else "")
        )
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
        ui.label("ℹ Preencha os campos abaixo e clique no botão de salvar.").classes(
            "text-xs text-gray-400 mb-6"
        )

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
                            on_change=make_on_change(opt_key),
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
                ).classes("w-full").props("outlined rows=4").bind_value(state, "opcao")

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
            ).classes("w-full").props("outlined rows=5").bind_value(state, "link")

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

        ui.button("SALVAR RESPOSTA", on_click=salvar_acao).classes(
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

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
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
            ui.notify(f"✅ Questionário de {ano_atual} zerado!", type="positive")
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

    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"

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

                    ui.button("🗑️", on_click=deletar_comentario).props("flat dense")

        input_novo_comentario = (
            ui.textarea(placeholder="Novo comentário...")
            .classes("w-full")
            .props("outlined rows=2")
        )

        def postar_comentario():
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append(
                    {
                        "autor": usuario_atual,
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "texto": txt,
                        "status_definido": status_global,
                    }
                )
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

                # Coluna 2: Formulário Principal (9/12)
                with ui.element("div").classes(
                    "md:col-span-8 lg:col-span-9 flex flex-col gap-4"
                ):
                    with ui.card().classes(
                        "w-full p-6 border rounded-lg shadow-sm bg-white"
                    ):
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

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "1.2 • Número de Cargos de Fiscais/Auditores Tributários Preenchidos"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Qual o número de cargos de fiscais/auditores tributários preenchidos?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: Se efetivos > 0 E comissão = 0 E terceirizados = 0 -> 1,5 pts | Caso contrário -> 0,0 pts"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            with ui.column().classes("gap-3 w-full"):
                                num_efetivo = (
                                    ui.number(
                                        label="Efetivo:",
                                        value=state_1_2["efetivo"],
                                        min=0,
                                        precision=0,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_1_2, "efetivo")
                                )

                                num_comissao = (
                                    ui.number(
                                        label="Em comissão:",
                                        value=state_1_2["comissao"],
                                        min=0,
                                        precision=0,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_1_2, "comissao")
                                )

                                num_terceirizado = (
                                    ui.number(
                                        label="Terceirizado:",
                                        value=state_1_2["terceirizado"],
                                        min=0,
                                        precision=0,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_1_2, "terceirizado")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_1_2["link"],
                                placeholder="Insira o link com a relação de cargos e quadro de pessoal...",
                            ).classes("w-full").props("outlined rows=7").bind_value(
                                state_1_2, "link"
                            )

                        pts_1_2 = calc_pts_1_2()
                        label_impacto_1_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.2: {pts_1_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_1_2(e=None):
                            pts_att = calc_pts_1_2()
                            label_impacto_1_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.2: {pts_att:.1f} pontos"
                            )

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
                                status=dados_1_2.get("status", "Pendente"),
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

                    # ==========================================
                    # QUESITO 5.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.1",
                        titulo="Instrumento Normativo de Aprovação da PGV",
                        pergunta="Informe o Instrumento normativo de aprovação da Planta Genérica de Valores (PGV), Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o documento conforme IP)",
                        tipo_input="text",
                        placeholder_link="Insira o link do instrumento normativo da PGV...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.2 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.2",
                        titulo="Página Eletrônica de Divulgação da PGV",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Instrumento Normativo de aprovação da Planta Genérica de Valores (PGV): (Se não estiver disponível na internet, inserir o texto XYZ)",
                        tipo_input="text",
                        placeholder_link="Insira a página eletrônica / link na internet...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.3 (Radio)
                    # ==========================================
                    opcoes_5_3 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.3",
                        titulo="Previsão de Revisão Periódica Obrigatória da PGV",
                        pergunta="O Código Tributário Municipal ou Lei específica que tenha instituído o IPTU prevê a revisão periódica obrigatória da Planta Genérica de Valores (PGV)?",
                        tipo_input="radio",
                        opcoes=opcoes_5_3,
                        placeholder_link="Insira a lei com a previsão de revisão obrigatória...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.3.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.3.1",
                        titulo="Instrumento Normativo de Revisão da PGV",
                        pergunta="Informe o instrumento normativo de revisão da Planta Genérica de Valores (PGV), Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o documento)",
                        tipo_input="text",
                        placeholder_link="Insira o link da norma de revisão da PGV...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.3.2 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.3.2",
                        titulo="Página Eletrônica de Divulgação da Revisão da PGV",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Instrumento normativo de revisão da Planta Genérica de Valores (PGV): (Se não estiver disponível na internet, inserir o texto XYZ)",
                        tipo_input="text",
                        placeholder_link="Insira o link oficial da divulgação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.3.3 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.3.3",
                        titulo="Data da Última Revisão da PGV",
                        pergunta="Informe a data da última revisão da PGV:",
                        tipo_input="text",
                        placeholder_link="Insira o documento comprobatório da última revisão...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.3.4 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.3.4",
                        titulo="Periodicidade de Revisão da PGV",
                        pergunta="Informe a periodicidade de revisão da PGV (em anos):",
                        tipo_input="text",
                        placeholder_link="Insira a fundamentação normativo-legal da periodicidade...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.4 (Radio)
                    # ==========================================
                    opcoes_5_4 = {
                        "Selecione...": 0.0,
                        "Sim, de forma automática no sistema – 06": 6.0,
                        "Sim, de forma manual – 02": 2.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.4",
                        titulo="Atualização da Base de Cálculo do IPTU",
                        pergunta="Os dados da Planta Genérica de Valores (PGV) e do Cadastro Imobiliário atualizam a base de cálculo do IPTU?",
                        tipo_input="radio",
                        opcoes=opcoes_5_4,
                        placeholder_link="Insira comprovantes do sistema ou rotina de atualização...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 6.0 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_6_0 = {
                        "Alíquotas progressivas em razão do valor do imóvel – 01": 1.0,
                        "Alíquotas diferenciadas em razão da localização do imóvel – 0,5": 0.5,
                        "Alíquotas diferenciadas em razão do uso do imóvel – 0,5": 0.5,
                        "Outros – 00": 0.0,
                        "Não há diferenciação nas alíquotas dos imóveis – -01": -1.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="6.0",
                        titulo="Critérios de Alíquota do IPTU",
                        pergunta="Sobre a alíquota do IPTU, quais critérios o município instituiu para a cobrança do imposto?",
                        tipo_input="checkbox",
                        opcoes=opcoes_6_0,
                        placeholder_link="Insira o trecho do Código Tributário com as alíquotas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 7.0 (Radio)
                    # ==========================================
                    opcoes_7_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.0",
                        titulo="Programa de Isenção do IPTU",
                        pergunta="O município adotou programa de isenção do IPTU?",
                        tipo_input="radio",
                        opcoes=opcoes_7_0,
                        placeholder_link="Insira o link ou comprovante da norma de isenção...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 7.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.1",
                        titulo="Instrumento Normativo da Isenção do IPTU",
                        pergunta="Informe o instrumento normativo de regulamentação do programa de isenção do IPTU, Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o instrumento)",
                        tipo_input="text",
                        placeholder_link="Insira a página ou link do documento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 7.2 (Página Eletrônica com Regra de Pontuação XYZ)
                    # ==========================================
                    dados_7_2 = res_data.get("7.2", {})
                    val_7_2 = str(dados_7_2.get("valor", ""))
                    link_7_2 = dados_7_2.get("link", "")

                    state_7_2 = {
                        "texto": val_7_2,
                        "link": link_7_2,
                    }

                    def calc_pts_7_2():
                        return (
                            -3.0 if state_7_2["texto"].strip().upper() == "XYZ" else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "7.2 • Divulgação da Isenção do IPTU na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação do Instrumento normativo de regulamentação do programa de isenção do IPTU:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir o texto 'XYZ' no campo de resposta. (Regra: Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_7_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_7_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_7_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_7_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_7_2, "link"
                            )

                        pts_7_2 = calc_pts_7_2()
                        label_impacto_7_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 7.2: {pts_7_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_7_2(e=None):
                            pts_att = calc_pts_7_2()
                            label_impacto_7_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 7.2: {pts_att:.1f} pontos"
                            )

                        txt_7_2.on("update:model-value", atualizar_impacto_7_2)

                        def salvar_7_2():
                            pts_final = calc_pts_7_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="7.2",
                                valor=state_7_2["texto"],
                                pontos=pts_final,
                                link=state_7_2["link"],
                                comentarios=dados_7_2.get("comentarios", []),
                                status=dados_7_2.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 7.2 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_7_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("7.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 7.3 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_7_3 = {
                        "Aposentado, pensionista ou beneficiário de renda mensal vitalícia": 0.0,
                        "Não possuir outro imóvel": 0.0,
                        "Utilizar o único imóvel como residência": 0.0,
                        "Rendimento mensal máximo": 0.0,
                        "Valor venal máximo do imóvel": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.3",
                        titulo="Critérios de Concessão de Isenção do IPTU",
                        pergunta="Assinale os critérios estabelecidos para a concessão de isenção total ou parcial do IPTU:",
                        tipo_input="checkbox",
                        opcoes=opcoes_7_3,
                        placeholder_link="Insira o dispositivo legal contendo os critérios...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.0 (Radio)
                    # ==========================================
                    opcoes_8_0 = {
                        "Selecione...": 0.0,
                        "Sim – 01": 1.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.0",
                        titulo="Instituição do ISSQN",
                        pergunta="O Imposto sobre Serviços de Qualquer Natureza (ISSQN) foi instituído no município?",
                        tipo_input="radio",
                        opcoes=opcoes_8_0,
                        placeholder_link="Insira o link da lei de instituição do ISSQN...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.1 (Radio)
                    # ==========================================
                    opcoes_8_1 = {
                        "Selecione...": 0.0,
                        "Sim – 02": 2.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.1",
                        titulo="Adequação à LC 157/2016",
                        pergunta="O Município atualizou sua legislação conforme as novas hipóteses de incidência de ISS previstas na LC 157/2016?",
                        tipo_input="radio",
                        opcoes=opcoes_8_1,
                        placeholder_link="Insira a lei de alteração/adequação ao ISSQN...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.2 (Radio)
                    # ==========================================
                    opcoes_8_2 = {
                        "Selecione...": 0.0,
                        "Sim por meio de sistema automatizado – 15": 15.0,
                        "Sim, manualmente – 08": 8.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.2",
                        titulo="Fiscalização de Omissão ou Queda de NFS-e",
                        pergunta="Houve rotina de fiscalização para detectar contribuintes que deixaram de emitir a Nota Fiscal de Serviços por determinado período ou que apresentaram queda acentuada em suas operações, a fim de detectar o fim das atividades ou a sonegação do ISSQN?",
                        tipo_input="radio",
                        opcoes=opcoes_8_2,
                        placeholder_link="Insira relatórios da rotina de fiscalização...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.3 (Radio)
                    # ==========================================
                    opcoes_8_3 = {
                        "Selecione...": 0.0,
                        "Sim, sem restrição – 00": 0.0,
                        "Sim, com restrição (Ex.: há necessidade de cadastro para acessar o resultado da pesquisa) – -09": -9.0,
                        "Serviço não disponibilizado – -15": -15.0,
                        "Não implantou a NFS-e – -15": -15.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.3",
                        titulo="Autenticidade de NFS-e",
                        pergunta="A pesquisa de autenticidade de notas fiscais eletrônicas está disponível ao público?",
                        tipo_input="radio",
                        opcoes=opcoes_8_3,
                        placeholder_link="Insira o link da ferramenta de consulta de autenticidade...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.0 (Radio)
                    # ==========================================
                    opcoes_9_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.0",
                        titulo="Regulamentação do ITBI",
                        pergunta="O Imposto sobre Transmissão de Bens Imóveis (ITBI) foi regulamentado?",
                        tipo_input="radio",
                        opcoes=opcoes_9_0,
                        placeholder_link="Insira o link ou documento de regulamentação do ITBI...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.1",
                        titulo="Instrumento Normativo do ITBI",
                        pergunta="Informe o instrumento normativo de regulamentação do ITBI, Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o instrumento normativo)",
                        tipo_input="text",
                        placeholder_link="Insira o link para a norma de regulamentação do ITBI...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.2 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_9_2 = res_data.get("9.2", {})
                    val_9_2 = str(dados_9_2.get("valor", ""))
                    link_9_2 = dados_9_2.get("link", "")

                    state_9_2 = {
                        "texto": val_9_2,
                        "link": link_9_2,
                    }

                    def calc_pts_9_2():
                        return (
                            -3.0 if state_9_2["texto"].strip().upper() == "XYZ" else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("9.2 • Divulgação do ITBI na Internet").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação da regulamentação do ITBI:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_9_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_9_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_9_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_9_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_9_2, "link"
                            )

                        pts_9_2 = calc_pts_9_2()
                        label_impacto_9_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 9.2: {pts_9_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_9_2(e=None):
                            pts_att = calc_pts_9_2()
                            label_impacto_9_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 9.2: {pts_att:.1f} pontos"
                            )

                        txt_9_2.on("update:model-value", atualizar_impacto_9_2)

                        def salvar_9_2():
                            pts_final = calc_pts_9_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="9.2",
                                valor=state_9_2["texto"],
                                pontos=pts_final,
                                link=state_9_2["link"],
                                comentarios=dados_9_2.get("comentarios", []),
                                status=dados_9_2.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 9.2 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_9_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("9.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 9.3 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_9_3 = {
                        "Site da Prefeitura": 0.0,
                        "Órgão Fazendário": 0.0,
                        "Cartório autorizado": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.3",
                        titulo="Registro e Emissão da Guia do ITBI",
                        pergunta="Assinale a forma de registro e emissão da guia de recolhimento do ITBI: (Obs: A mera impressão da guia não é considerada forma de emissão)",
                        tipo_input="checkbox",
                        opcoes=opcoes_9_3,
                        placeholder_link="Insira o link ou documento comprobatório dos canais de emissão...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.4 (Radio)
                    # ==========================================
                    opcoes_9_4 = {
                        "Selecione...": 0.0,
                        "Sim – 02": 2.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.4",
                        titulo="Obrigação de Informação pelos Cartórios",
                        pergunta="O município instituiu normativo que obrigue o(s) Cartório(s) de Registro de Imóveis e Distribuidor(es) a informar periodicamente as transmissões imobiliárias realizadas no seu território, para fins de incidência do ITBI?",
                        tipo_input="radio",
                        opcoes=opcoes_9_4,
                        placeholder_link="Insira a lei ou decreto com a exigência aos cartórios...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.4.1 (Radio)
                    # ==========================================
                    opcoes_9_4_1 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.4.1",
                        titulo="Penalidades aos Cartórios",
                        pergunta="O município aplica penalidade ou multa aos Cartórios, quando não cumpridos os termos da lei mencionada na resposta do item anterior?",
                        tipo_input="radio",
                        opcoes=opcoes_9_4_1,
                        placeholder_link="Insira o dispositivo legal da penalidade aos cartórios...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.5 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_9_5 = {
                        "Sistema Bancário": 0.0,
                        "Diretamente no Caixa da Prefeitura": 0.0,
                        "Lotérica": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.5",
                        titulo="Forma de Recolhimento do ITBI",
                        pergunta="Assinale a forma de recolhimento da guia do ITBI:",
                        tipo_input="checkbox",
                        opcoes=opcoes_9_5,
                        placeholder_link="Insira o comprovante ou norma dos locais aceitos para pagamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.6 (Radio - Penalidade Súmula 656 STF)
                    # ==========================================
                    opcoes_9_6 = {
                        "Selecione...": 0.0,
                        "Sim – -30": -30.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.6",
                        titulo="Alíquotas Progressivas no ITBI",
                        pergunta="O município estabelece alíquotas progressivas para o ITBI, com base no valor venal do imóvel? (Atenção: Súmula 656 do STF)",
                        tipo_input="radio",
                        opcoes=opcoes_9_6,
                        placeholder_link="Insira o trecho do Código Tributário sobre as alíquotas do ITBI...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 10.0 (Radio)
                    # ==========================================
                    opcoes_10_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="10.0",
                        titulo="Instituição da CIP/COSIP",
                        pergunta="A Contribuição para Custeio do Serviço de Iluminação Pública (CIP) foi instituída?",
                        tipo_input="radio",
                        opcoes=opcoes_10_0,
                        placeholder_link="Insira o link ou documento de instituição da CIP/COSIP...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 10.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="10.1",
                        titulo="Instrumento Normativo de Instituição da CIP",
                        pergunta="Informe o instrumento normativo de instituição da Contribuição para Custeio do Serviço de Iluminação Pública (CIP), número e data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o instrumento)",
                        tipo_input="text",
                        placeholder_link="Insira o link para a norma de instituição da CIP...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 10.2 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_10_2 = res_data.get("10.2", {})
                    val_10_2 = str(dados_10_2.get("valor", ""))
                    link_10_2 = dados_10_2.get("link", "")

                    state_10_2 = {
                        "texto": val_10_2,
                        "link": link_10_2,
                    }

                    def calc_pts_10_2():
                        return (
                            -3.0
                            if state_10_2["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("10.2 • Divulgação da CIP na Internet").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação do instrumento normativo de instituição da Contribuição para Custeio do Serviço de Iluminação Pública (CIP):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_10_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_10_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_10_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_10_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_10_2, "link"
                            )

                        pts_10_2 = calc_pts_10_2()
                        label_impacto_10_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 10.2: {pts_10_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_10_2(e=None):
                            pts_att = calc_pts_10_2()
                            label_impacto_10_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 10.2: {pts_att:.1f} pontos"
                            )

                        txt_10_2.on("update:model-value", atualizar_impacto_10_2)

                        def salvar_10_2():
                            pts_final = calc_pts_10_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="10.2",
                                valor=state_10_2["texto"],
                                pontos=pts_final,
                                link=state_10_2["link"],
                                comentarios=dados_10_2.get("comentarios", []),
                                status=dados_10_2.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 10.2 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_10_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("10.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 10.3 (Radio)
                    # ==========================================
                    opcoes_10_3 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="10.3",
                        titulo="Movimentação da CIP em Contas Específicas",
                        pergunta="Os recursos da Contribuição para Custeio do Serviço de Iluminação Pública (CIP) foram movimentados em contas específicas?",
                        tipo_input="radio",
                        opcoes=opcoes_10_3,
                        placeholder_link="Insira o extrato bancário ou comprovante da conta específica...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 11.0 (Radio)
                    # ==========================================
                    opcoes_11_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="11.0",
                        titulo="Regulamentação da Retenção de IRRF nas Compras Municipais",
                        pergunta="Houve regulamentação sobre a retenção de IRRF das contratações efetuadas pelo município nas compras de bens e serviços?",
                        tipo_input="radio",
                        opcoes=opcoes_11_0,
                        placeholder_link="Insira o decreto/instrução normativa da retenção de IRRF...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.0 (Radio)
                    # ==========================================
                    opcoes_12_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.0",
                        titulo="Concessão de Benefícios e Incentivos (Renúncia de Receita)",
                        pergunta="No exercício de 2025, foram concedidos benefícios e incentivos de natureza tributária, financeira e creditícia da qual decorram em renúncia de receitas?",
                        tipo_input="radio",
                        opcoes=opcoes_12_0,
                        placeholder_link="Insira o demonstrativo de incentivos/renúncia de receita...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1 (Radio)
                    # ==========================================
                    opcoes_12_1 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -10": -10.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.1",
                        titulo="Normas e Procedimentos de Renúncia de Receita",
                        pergunta="Há normas e procedimentos relativos à renúncia de receita?",
                        tipo_input="radio",
                        opcoes=opcoes_12_1,
                        placeholder_link="Insira a norma com os procedimentos de renúncia...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.1.1",
                        titulo="Instrumento Normativo de Renúncia de Receita",
                        pergunta="Informe o instrumento normativo de regulamentação dos procedimentos relativos à renúncia de receita, Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o instrumento)",
                        tipo_input="text",
                        placeholder_link="Insira o link para a norma de renúncia de receita...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1.2 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_12_1_2 = res_data.get("12.1.2", {})
                    val_12_1_2 = str(dados_12_1_2.get("valor", ""))
                    link_12_1_2 = dados_12_1_2.get("link", "")

                    state_12_1_2 = {
                        "texto": val_12_1_2,
                        "link": link_12_1_2,
                    }

                    def calc_pts_12_1_2():
                        return (
                            -3.0
                            if state_12_1_2["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "12.1.2 • Divulgação da Renúncia de Receita na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação do instrumento normativo de regulamentação dos procedimentos relativos à renúncia de receita:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_12_1_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_12_1_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_12_1_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_12_1_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_12_1_2, "link"
                            )

                        pts_12_1_2 = calc_pts_12_1_2()
                        label_impacto_12_1_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 12.1.2: {pts_12_1_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_12_1_2(e=None):
                            pts_att = calc_pts_12_1_2()
                            label_impacto_12_1_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 12.1.2: {pts_att:.1f} pontos"
                            )

                        txt_12_1_2.on("update:model-value", atualizar_impacto_12_1_2)

                        def salvar_12_1_2():
                            pts_final = calc_pts_12_1_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="12.1.2",
                                valor=state_12_1_2["texto"],
                                pontos=pts_final,
                                link=state_12_1_2["link"],
                                comentarios=dados_12_1_2.get("comentarios", []),
                                status=dados_12_1_2.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 12.1.2 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_12_1_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("12.1.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 12.2 (Radio)
                    # ==========================================
                    opcoes_12_2 = {
                        "Selecione...": 0.0,
                        "Sim, de todas as renúncias de receita – 00": 0.0,
                        "Sim, de parte das renúncias de receita – -02": -2.0,
                        "Não – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.2",
                        titulo="Acompanhamento e Reavaliação das Renúncias de Receita",
                        pergunta="A Prefeitura Municipal realizou acompanhamento e (re)avaliação das renúncias de receita?",
                        tipo_input="radio",
                        opcoes=opcoes_12_2,
                        placeholder_link="Insira os relatórios de acompanhamento e reavaliação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.3 (Radio)
                    # ==========================================
                    opcoes_12_3 = {
                        "Selecione...": 0.0,
                        "Todas as renúncias concedidas estão contidas no demonstrativo – 00": 0.0,
                        "A maior parte das renúncias concedidas estão contidas no demonstrativo – -01": -1.0,
                        "A menor parte das renúncias concedidas estão contidas no demonstrativo – -03": -3.0,
                        "Não há demonstrativo – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.3",
                        titulo="Anexo de Metas Fiscais (LDO) - Renúncia de Receita",
                        pergunta="O Anexo de Metas Fiscais, que integra a LDO, contém demonstrativo da estimativa e compensação da renúncia de receita para o respectivo exercício orçamentário?",
                        tipo_input="radio",
                        opcoes=opcoes_12_3,
                        placeholder_link="Insira a cópia do Anexo de Metas Fiscais da LDO...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.3.1 (Radio)
                    # ==========================================
                    opcoes_12_3_1 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.3.1",
                        titulo="Compatibilidade da Renúncia de Receita com a LDO",
                        pergunta="O valor da renúncia de receita de 2025 está compatível com a estimativa constante no Anexo de Metas Fiscais da Lei de Diretrizes Orçamentárias?",
                        tipo_input="radio",
                        opcoes=opcoes_12_3_1,
                        placeholder_link="Insira o demonstrativo de compatibilidade dos valores...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.4 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.4",
                        titulo="Valor das Renúncias de Receita",
                        pergunta="Informe o valor das renúncias no exercício de 2025:",
                        tipo_input="text",
                        placeholder_link="Insira a memória de cálculo do valor total da renúncia...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.5 (Radio)
                    # ==========================================
                    opcoes_12_5 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -10": -10.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.5",
                        titulo="Transparência dos Benefícios da Renúncia de Receita",
                        pergunta="Houve publicidade e transparência dos benefícios concedidos por Renúncia de Receitas em 2025?",
                        tipo_input="radio",
                        opcoes=opcoes_12_5,
                        placeholder_link="Insira o link das publicações no Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.5.1 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_12_5_1 = {
                        "Valor dos benefícios concedidos": 0.0,
                        "Público beneficiado": 0.0,
                        "Métodos utilizados na sua mensuração": 0.0,
                        "Resultados socioeconômicos alcançados com a renúncia": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.5.1",
                        titulo="Informações Divulgadas sobre a Renúncia de Receitas",
                        pergunta="Assinale as informações divulgadas referente aos benefícios concedidos por Renúncia de Receitas em 2025:",
                        tipo_input="checkbox",
                        opcoes=opcoes_12_5_1,
                        placeholder_link="Insira o documento ou link onde constam essas informações...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.5.2 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_12_5_2 = res_data.get("12.5.2", {})
                    val_12_5_2 = str(dados_12_5_2.get("valor", ""))
                    link_12_5_2 = dados_12_5_2.get("link", "")

                    state_12_5_2 = {
                        "texto": val_12_5_2,
                        "link": link_12_5_2,
                    }

                    def calc_pts_12_5_2():
                        return (
                            -10.0
                            if state_12_5_2["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "12.5.2 • Divulgação dos Benefícios da Renúncia na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação das informações referente aos benefícios concedidos por Renúncia de Receitas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -10,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_12_5_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_12_5_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_12_5_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_12_5_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_12_5_2, "link"
                            )

                        pts_12_5_2 = calc_pts_12_5_2()
                        label_impacto_12_5_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 12.5.2: {pts_12_5_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_12_5_2(e=None):
                            pts_att = calc_pts_12_5_2()
                            label_impacto_12_5_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 12.5.2: {pts_att:.1f} pontos"
                            )

                        txt_12_5_2.on("update:model-value", atualizar_impacto_12_5_2)

                        def salvar_12_5_2():
                            pts_final = calc_pts_12_5_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="12.5.2",
                                valor=state_12_5_2["texto"],
                                pontos=pts_final,
                                link=state_12_5_2["link"],
                                comentarios=dados_12_5_2.get("comentarios", []),
                                status=dados_12_5_2.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 12.5.2 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_12_5_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("12.5.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 13.0 (Radio)
                    # ==========================================
                    opcoes_13_0 = {
                        "Selecione...": 0.0,
                        "Sim – 01": 1.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.0",
                        titulo="Regulamentação da Dívida Ativa",
                        pergunta="O município possui regulamentação sobre dívida ativa?",
                        tipo_input="radio",
                        opcoes=opcoes_13_0,
                        placeholder_link="Insira o link ou documento de regulamentação da dívida ativa...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.1",
                        titulo="Instrumento Normativo da Dívida Ativa",
                        pergunta="Instrumento normativo de regulamentação da dívida ativa, Número e Data da publicação: (Caso não esteja disponível na internet, recomendamos anexar o instrumento)",
                        tipo_input="text",
                        placeholder_link="Insira o link da norma de regulamentação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.2 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_13_2 = res_data.get("13.2", {})
                    val_13_2 = str(dados_13_2.get("valor", ""))
                    link_13_2 = dados_13_2.get("link", "")

                    state_13_2 = {
                        "texto": val_13_2,
                        "link": link_13_2,
                    }

                    def calc_pts_13_2():
                        return (
                            -3.0
                            if state_13_2["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "13.2 • Divulgação da Regulamentação da Dívida Ativa na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação da regulamentação da dívida ativa:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_13_2 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_13_2["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_13_2, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_13_2["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_13_2, "link"
                            )

                        pts_13_2 = calc_pts_13_2()
                        label_impacto_13_2 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 13.2: {pts_13_2:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_13_2(e=None):
                            pts_att = calc_pts_13_2()
                            label_impacto_13_2.set_text(
                                f"📊 Impacto de Pontuação no Quesito 13.2: {pts_att:.1f} pontos"
                            )

                        txt_13_2.on("update:model-value", atualizar_impacto_13_2)

                        def salvar_13_2():
                            pts_final = calc_pts_13_2()
                            save_resposta(
                                ano=ano_sel,
                                qid="13.2",
                                valor=state_13_2["texto"],
                                pontos=pts_final,
                                link=state_13_2["link"],
                                comentarios=dados_13_2.get("comentarios", []),
                                status=dados_13_2.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 13.2 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_13_2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("13.2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 13.3 (Checkbox - Múltipla Escolha com Pontuação)
                    # ==========================================
                    opcoes_13_3 = {
                        "Cobrança administrativa da dívida ativa – 1,5": 1.5,
                        "Parcelamento da dívida ativa – 1,5": 1.5,
                        "Restrição e controle da inadimplência nos parcelamentos da dívida ativa – 1,5": 1.5,
                        "Início do trâmite da execução judicial da dívida ativa – 1,5": 1.5,
                        "Anistia – 1,5": 1.5,
                        "Remissão – 1,5": 1.5,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.3",
                        titulo="Critérios Estabelecidos na Legislação sobre Dívida Ativa",
                        pergunta="Assinale os critérios estabelecidos na legislação sobre dívida ativa:",
                        tipo_input="checkbox",
                        opcoes=opcoes_13_3,
                        placeholder_link="Insira a cópia da lei/decreto onde constam estes critérios...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.0 (Radio)
                    # ==========================================
                    opcoes_14_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.0",
                        titulo="Execução Judicial da Dívida Ativa",
                        pergunta="O Município possui dívida ativa executada de forma judicial em 2025?",
                        tipo_input="radio",
                        opcoes=opcoes_14_0,
                        placeholder_link="Insira o relatório ou certidão das execuções ajuizadas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.1",
                        titulo="Valor Total da Dívida Ativa Executada Judicialmente",
                        pergunta="Informe o valor total da dívida ativa executada de forma judicial no exercício de 2025:",
                        tipo_input="text",
                        placeholder_link="Insira o relatório contendo o somatório das execuções fiscais...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.0 (Radio)
                    # ==========================================
                    opcoes_15_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.0",
                        titulo="Cobrança Extrajudicial da Dívida Ativa",
                        pergunta="A prefeitura realiza cobrança de dívida ativa de forma extrajudicial?",
                        tipo_input="radio",
                        opcoes=opcoes_15_0,
                        placeholder_link="Insira o documento comprobatório da cobrança extrajudicial...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.1",
                        titulo="Valor Total da Dívida Ativa Cobrada Extrajudicialmente",
                        pergunta="Informe o valor total da dívida ativa cobrada de forma extrajudicial no exercício de 2025:",
                        tipo_input="text",
                        placeholder_link="Insira o demonstrativo financeiro dos valores cobrados extrajudicialmente...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.2 (Checkbox - Múltipla Escolha)
                    # ==========================================
                    opcoes_15_2 = {
                        "Protesto Extrajudicial da CDA (Certidão da Dívida Ativa)": 0.0,
                        "Parcelamento": 0.0,
                        "Facilitação do Pagamento": 0.0,
                        "Conciliação extrajudicial": 0.0,
                        "Inclusão do nome do devedor em Cadastro (Ex. Cadastro Informativo Municipal - CADIN)": 0.0,
                        "Inclusão do nome do devedor em serviços de proteção ao crédito": 0.0,
                        "Outros": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.2",
                        titulo="Modalidades de Cobrança Extrajudicial",
                        pergunta="Assinale as modalidades de cobrança extrajudicial da dívida ativa:",
                        tipo_input="checkbox",
                        opcoes=opcoes_15_2,
                        placeholder_link="Insira os convênios, decretos e comprovantes das modalidades utilizadas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.0 (Radio)
                    # ==========================================
                    opcoes_16_0 = {
                        "Selecione...": 0.0,
                        "Sim, houve prescrição ordinária – -10": -10.0,
                        "Sim, houve prescrição intercorrente – 00": 0.0,
                        "Não houve prescrição de dívidas em 2025 – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.0",
                        titulo="Prescrição de Dívidas em 2025",
                        pergunta="No exercício de 2025 houve dívidas prescritas? (Considerar na prescrição ordinária apenas os valores passíveis de cobrança via judicial, conforme regulamento específico local)",
                        tipo_input="radio",
                        opcoes=opcoes_16_0,
                        placeholder_link="Insira o relatório de prescrição ou parecer jurídico...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.1",
                        titulo="Valor da Dívida Ativa Prescrita na Execução Judicial",
                        pergunta="Informe o valor da dívida ativa prescrita na execução judicial em 2025:",
                        tipo_input="text",
                        placeholder_link="Insira a certidão ou demonstrativo contendo o valor prescrito judicialmente...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.2 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.2",
                        titulo="Valor da Dívida Ativa Prescrita Cobrada Extrajudicialmente",
                        pergunta="Informe o valor da dívida ativa cobrada de forma extrajudicial prescrita no exercício de 2025:",
                        tipo_input="text",
                        placeholder_link="Insira o demonstrativo do montante prescrito extrajudicialmente...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.3 (Radio)
                    # ==========================================
                    opcoes_16_3 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.3",
                        titulo="Provisão para Perdas de Dívida Ativa",
                        pergunta="O montante da dívida ativa prescrita cobrada de forma judicial e extrajudicial estava registrado na conta de Provisão para Perdas de Dívida Ativa?",
                        tipo_input="radio",
                        opcoes=opcoes_16_3,
                        placeholder_link="Insira o balancete/razão contábil comprovando o registro da provisão...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.0 (Radio)
                    # ==========================================
                    opcoes_17_0 = {
                        "Selecione...": 0.0,
                        "Sim, de todas as ações – 00": 0.0,
                        "Sim, da maior parte das ações – -01": -1.0,
                        "Sim, da menor parte das ações – -03": -3.0,
                        "Não – -05": -5.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.0",
                        titulo="Controle das Ações Judiciais (Polo Passivo)",
                        pergunta="A Prefeitura possui controle das ações judiciais em que é parte (polo passivo)?",
                        tipo_input="radio",
                        opcoes=opcoes_17_0,
                        placeholder_link="Insira o relatório de acompanhamento do contencioso judicial...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.1 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.1",
                        titulo="Forma de Controle das Ações Judiciais",
                        pergunta="Descreva de que forma é realizado o controle das ações judiciais em que é parte (polo passivo):",
                        tipo_input="text",
                        placeholder_link="Insira o comprovante do sistema de gestão jurídica utilizado...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.2 (Texto Dissertativo)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.2",
                        titulo="Valor Atualizado das Ações Judiciais no Polo Passivo",
                        pergunta="Qual o valor atualizado em 31/12/2025 de todas as ações judiciais em que é parte (polo passivo)?",
                        tipo_input="text",
                        placeholder_link="Insira a planilha ou parecer de risco fiscal e provisões com o valor atualizado...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 18.0 (Radio)
                    # ==========================================
                    opcoes_18_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="18.0",
                        titulo="Divulgação da Transparência Fiscal na Internet",
                        pergunta="Os dados relativos à transparência na gestão fiscal são divulgados na página eletrônica do Município?",
                        tipo_input="radio",
                        opcoes=opcoes_18_0,
                        placeholder_link="Insira o link principal do Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 18.1 (Checkbox - Múltipla Escolha com Pontuação)
                    # ==========================================
                    opcoes_18_1 = {
                        "PPA, LDO e LOA – 2,5": 2.5,
                        "Balanços de exercício – 2,5": 2.5,
                        "Prestação de contas do ano anterior – 2,5": 2.5,
                        "Parecer prévio do TCE – 2,5": 2.5,
                        "Relatório de Gestão Fiscal (RGF) – 2,5": 2.5,
                        "Relatório Resumido da Execução Orçamentária (RREO) – 2,5": 2.5,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="18.1",
                        titulo="Itens Divulgados no Portal da Transparência",
                        pergunta="Assinale os itens que são divulgados na página eletrônica do Município:",
                        tipo_input="checkbox",
                        opcoes=opcoes_18_1,
                        placeholder_link="Insira o link das seções onde esses relatórios e leis estão publicados...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 19.0 (Radio)
                    # ==========================================
                    opcoes_19_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="19.0",
                        titulo="Divulgação das Receitas em Tempo Real",
                        pergunta="Houve divulgação das receitas arrecadadas em tempo real? (Nota: Tempo real é até o 1º dia útil que sucede o do registro contábil)",
                        tipo_input="radio",
                        opcoes=opcoes_19_0,
                        placeholder_link="Insira o link da consulta de receita em tempo real no Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 19.1 (Checkbox - Múltipla Escolha com Pontuação)
                    # ==========================================
                    opcoes_19_1 = {
                        "Categoria econômica – 0,3": 0.3,
                        "Origem – 0,3": 0.3,
                        "Espécie – 0,3": 0.3,
                        "Desdobramento para identificação de peculiaridades – 0,3": 0.3,
                        "Tipo – 0,3": 0.3,
                        "Valor previsto – 0,3": 0.3,
                        "Valor arrecadado – 0,3": 0.3,
                        "Data de arrecadação – 0,3": 0.3,
                        "Recursos extraordinários – 0,3": 0.3,
                        "Outros – 0,3": 0.3,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="19.1",
                        titulo="Itens da Receita Divulgados em Tempo Real",
                        pergunta="Assinale os itens da receita divulgados em tempo real:",
                        tipo_input="checkbox",
                        opcoes=opcoes_19_1,
                        placeholder_link="Insira o link da página do Portal da Transparência onde constam os detalhes da receita...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 20.0 (Radio)
                    # ==========================================
                    opcoes_20_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="20.0",
                        titulo="Divulgação das Despesas em Tempo Real",
                        pergunta="Houve divulgação das despesas executadas em tempo real? (Nota: Tempo real é até o 1º dia útil que sucede o do registro contábil)",
                        tipo_input="radio",
                        opcoes=opcoes_20_0,
                        placeholder_link="Insira o link da consulta de despesas executadas no Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 20.1 (Checkbox - Múltipla Escolha com Pontuação)
                    # ==========================================
                    opcoes_20_1 = {
                        "Valor empenhado – 0,3": 0.3,
                        "Valor liquidado – 0,3": 0.3,
                        "Valor pago – 0,3": 0.3,
                        "Número do processo da execução - nº empenho – 0,3": 0.3,
                        "Unidade Orçamentária - UO – 0,3": 0.3,
                        "Função – 0,3": 0.3,
                        "Subfunção – 0,3": 0.3,
                        "Categoria Econômica da despesa – 0,3": 0.3,
                        "Grupo de Natureza da despesa – 0,3": 0.3,
                        "Modalidade de aplicação – 0,3": 0.3,
                        "Elemento – 0,6": 0.6,
                        "Subelemento – 0,6": 0.6,
                        "Fonte de recurso – 0,3": 0.3,
                        "Favorecido do pagamento – 0,3": 0.3,
                        "Modalidade da licitação – 0,3": 0.3,
                        "Número do processo licitatório – 0,3": 0.3,
                        "Bem fornecido ou serviço prestado – 0,3": 0.3,
                        "Outros – 0,3": 0.3,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="20.1",
                        titulo="Itens das Despesas Divulgados em Tempo Real",
                        pergunta="Assinale os itens das despesas divulgados em tempo real:",
                        tipo_input="checkbox",
                        opcoes=opcoes_20_1,
                        placeholder_link="Insira o link da página do Portal da Transparência onde constam os detalhes das despesas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 21.0 (Radio)
                    # ==========================================
                    opcoes_21_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="21.0",
                        titulo="Divulgação da Remuneração Individualizada",
                        pergunta="Houve divulgação de remuneração individualizada por nome do agente público, contendo dados sobre os vencimentos, descontos, indenizações e valor líquido?",
                        tipo_input="radio",
                        opcoes=opcoes_21_0,
                        placeholder_link="Insira o link da folha de pagamento no Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 21.1 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_21_1 = res_data.get("21.1", {})
                    val_21_1 = str(dados_21_1.get("valor", ""))
                    link_21_1 = dados_21_1.get("link", "")

                    state_21_1 = {
                        "texto": val_21_1,
                        "link": link_21_1,
                    }

                    def calc_pts_21_1():
                        return (
                            -3.0
                            if state_21_1["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "21.1 • Divulgação da Remuneração na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação da remuneração individualizada por nome do agente público:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_21_1 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_21_1["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_21_1, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_21_1["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_21_1, "link"
                            )

                        pts_21_1 = calc_pts_21_1()
                        label_impacto_21_1 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 21.1: {pts_21_1:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_21_1(e=None):
                            pts_att = calc_pts_21_1()
                            label_impacto_21_1.set_text(
                                f"📊 Impacto de Pontuação no Quesito 21.1: {pts_att:.1f} pontos"
                            )

                        txt_21_1.on("update:model-value", atualizar_impacto_21_1)

                        def salvar_21_1():
                            pts_final = calc_pts_21_1()
                            save_resposta(
                                ano=ano_sel,
                                qid="21.1",
                                valor=state_21_1["texto"],
                                pontos=pts_final,
                                link=state_21_1["link"],
                                comentarios=dados_21_1.get("comentarios", []),
                                status=dados_21_1.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 21.1 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_21_1).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("21.1", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 22.0 (Radio)
                    # ==========================================
                    opcoes_22_0 = {
                        "Selecione...": 0.0,
                        "Sim – 03": 3.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="22.0",
                        titulo="Divulgação de Diárias e Passagens",
                        pergunta="Houve divulgação de diárias e passagens por nome de favorecido e constando data, destino, cargo e motivo de viagem?",
                        tipo_input="radio",
                        opcoes=opcoes_22_0,
                        placeholder_link="Insira o link da consulta de diárias e passagens no Portal da Transparência...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 22.1 (Página Eletrônica com Regra XYZ)
                    # ==========================================
                    dados_22_1 = res_data.get("22.1", {})
                    val_22_1 = str(dados_22_1.get("valor", ""))
                    link_22_1 = dados_22_1.get("link", "")

                    state_22_1 = {
                        "texto": val_22_1,
                        "link": link_22_1,
                    }

                    def calc_pts_22_1():
                        return (
                            -3.0
                            if state_22_1["texto"].strip().upper() == "XYZ"
                            else 0.0
                        )

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "22.1 • Divulgação de Diárias e Passagens na Internet"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação de diárias e passagens:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Se não estiver disponível na internet, inserir no campo 'Página eletrônica (link na internet)' o texto 'XYZ'. (Texto XYZ = -3,0 pts | Caso contrário = 0,0 pts)"
                        ).classes("text-xs text-gray-500 mb-6")

                        with ui.grid(columns=2).classes(
                            "w-full gap-6 items-start mb-4"
                        ):
                            txt_22_1 = (
                                ui.textarea(
                                    label="Página Eletrônica / Resposta:",
                                    value=state_22_1["texto"],
                                    placeholder="Digite a URL ou o texto XYZ...",
                                )
                                .classes("w-full")
                                .props("outlined rows=5")
                                .bind_value(state_22_1, "texto")
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_22_1["link"],
                                placeholder="Insira o link de evidência adicional...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_22_1, "link"
                            )

                        pts_22_1 = calc_pts_22_1()
                        label_impacto_22_1 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 22.1: {pts_22_1:.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def atualizar_impacto_22_1(e=None):
                            pts_att = calc_pts_22_1()
                            label_impacto_22_1.set_text(
                                f"📊 Impacto de Pontuação no Quesito 22.1: {pts_att:.1f} pontos"
                            )

                        txt_22_1.on("update:model-value", atualizar_impacto_22_1)

                        def salvar_22_1():
                            pts_final = calc_pts_22_1()
                            save_resposta(
                                ano=ano_sel,
                                qid="22.1",
                                valor=state_22_1["texto"],
                                pontos=pts_final,
                                link=state_22_1["link"],
                                comentarios=dados_22_1.get("comentarios", []),
                                status=dados_22_1.get("status", "Pendente"),
                            )
                            ui.notify(
                                "Quesito 22.1 salvo com sucesso!", type="positive"
                            )
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_22_1).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("22.1", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 23.0 (Radio)
                    # ==========================================
                    opcoes_23_0 = {
                        "Selecione...": 0.0,
                        "Todos os repasses foram dentro do prazo legal – 00": 0.0,
                        "A maior parte dos repasses recolhidos até 30 dias após o vencimento – -04": -4.0,
                        "A maior parte dos repasses recolhidos de 31 a 90 dias do vencimento – -15": -15.0,
                        "A maior parte dos repasses recolhidos acima de 90 dias do vencimento – -21": -21.0,
                        "Os repasses não foram realizados – -30": -30.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="23.0",
                        titulo="Prazo dos Repasses para o RGPS",
                        pergunta="Os repasses para o Regime Geral de Previdência Social (RGPS) da competência de 2025 foram realizados em qual prazo?",
                        tipo_input="radio",
                        opcoes=opcoes_23_0,
                        placeholder_link="Insira os comprovantes de pagamento das guias/GPS do RGPS de 2025...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 24.0 (Radio)
                    # ==========================================
                    opcoes_24_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="24.0",
                        titulo="Adesão a Parcelamento do RGPS",
                        pergunta="A Prefeitura aderiu a algum parcelamento de encargos sociais (Regime Geral de Previdência Social - RGPS)?",
                        tipo_input="radio",
                        opcoes=opcoes_24_0,
                        placeholder_link="Insira o termo de adesão ao parcelamento ou certidão da RFB...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 24.1 (Radio)
                    # ==========================================
                    opcoes_24_1 = {
                        "Selecione...": 0.0,
                        "Todas as parcelas foram recolhidas dentro do prazo legal – 00": 0.0,
                        "A maior parte das parcelas recolhidas até 30 dias após o vencimento – -04": -4.0,
                        "A maior parte das parcelas recolhidas de 31 a 90 dias do vencimento – -15": -15.0,
                        "A maior parte das parcelas recolhidas acima de 90 dias do vencimento – -21": -21.0,
                        "As parcelas não foram recolhidas – -30": -30.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="24.1",
                        titulo="Prazo de Pagamento das Parcelas do RGPS",
                        pergunta="As parcelas referentes ao parcelamento para o Regime Geral de Previdência Social (RGPS) com vencimento em 2025 foram realizados em qual prazo?",
                        tipo_input="radio",
                        opcoes=opcoes_24_1,
                        placeholder_link="Insira os comprovantes de pagamento das parcelas de 2025...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 25.0 (Radio)
                    # ==========================================
                    opcoes_25_0 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="25.0",
                        titulo="Compensação de Encargos Sociais junto à RFB",
                        pergunta="O Município efetuou, no exercício de 2025, compensação de encargos sociais junto à Receita Federal do Brasil?",
                        tipo_input="radio",
                        opcoes=opcoes_25_0,
                        placeholder_link="Insira o relatório de PER/DCOMP ou GFIP/eSocial referente às compensações...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 25.1 (Radio)
                    # ==========================================
                    opcoes_25_1 = {
                        "Selecione...": 0.0,
                        "Sim – 00": 0.0,
                        "Não – -25": -25.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="25.1",
                        titulo="Autorização ou Decisão Judicial para Compensação",
                        pergunta="Houve autorização formal administrativa da Receita Federal do Brasil (RFB) ou decisão judicial para realizar as compensações?",
                        tipo_input="radio",
                        opcoes=opcoes_25_1,
                        placeholder_link="Insira a cópia do despacho administrativo da RFB ou decisão judicial com trânsito em julgado/liminar...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 26.0 (Texto Dissertativo / Feedback)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="26.0",
                        titulo="Impressões, Comentários e Sugestões",
                        pergunta="Gostaria de registrar suas impressões, comentários e sugestões a respeito do presente questionário? Utilize o espaço abaixo para registrar suas observações.",
                        tipo_input="text",
                        placeholder_link="Insira anexos ou documentos complementares, se houver...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO F1 (Análise da Receita - Execução Orçamentária)
                    # ==========================================
                    f1_data = res_data.get("F1", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F1 • Análise da Receita (Execução Orçamentária) – Resultado Consolidado"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Divisão da receita arrecadada (O) pela receita prevista atualizada (P), com base nos dados da LOA (Q = O / P):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador Q", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Q >= 1,5:** Pontuação = **0,0 ponto**
                            * **1,15 < Q < 1,5:** Graduação entre 75 e 0 `((Q - 1,5) * (-1) / 0,35) * 75`
                            * **0,85 <= Q <= 1,15:** Pontuação máxima = **75,0 pontos**
                            * **0,5 < Q < 0,85:** Graduação entre 0 e 75 `((Q - 0,5) / 0,35) * 75`
                            * **Q <= 0,5:** Pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador Q"
                            ).classes("font-bold text-blue-700 mb-2")

                            # Desserialização segura do dicionário salvo
                            val_f1_bruto = f1_data.get("valor", {})
                            while isinstance(val_f1_bruto, str):
                                try:
                                    val_f1_bruto = json.loads(val_f1_bruto)
                                except Exception:
                                    val_f1_bruto = {}
                                    break

                            if not isinstance(val_f1_bruto, dict):
                                val_f1_bruto = {}

                            raw_o = val_f1_bruto.get("O", val_f1_bruto.get("o", 0.0))
                            raw_p = val_f1_bruto.get("P", val_f1_bruto.get("p", 0.0))

                            try:
                                val_o_init = float(raw_o) if raw_o is not None else 0.0
                            except (ValueError, TypeError):
                                val_o_init = 0.0

                            try:
                                val_p_init = float(raw_p) if raw_p is not None else 0.0
                            except (ValueError, TypeError):
                                val_p_init = 0.0

                            state_f1 = {
                                "val_o": val_o_init,
                                "val_p": val_p_init,
                                "link": f1_data.get("link", ""),
                                "pts": float(f1_data.get("pontos", 0.0)),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_o = (
                                    ui.number(
                                        label="Receita Arrecadada (O)", format="%.2f"
                                    )
                                    .bind_value(state_f1, "val_o")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_p = (
                                    ui.number(
                                        label="Receita Prevista Atualizada (P)",
                                        format="%.2f",
                                    )
                                    .bind_value(state_f1, "val_p")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_q = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_q = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_q(_=None):
                                try:
                                    o = (
                                        float(state_f1["val_o"])
                                        if state_f1["val_o"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    o = 0.0

                                try:
                                    p = (
                                        float(state_f1["val_p"])
                                        if state_f1["val_p"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    p = 0.0

                                if p > 0:
                                    q = o / p
                                    if q >= 1.5:
                                        pts = 0.0
                                    elif 1.15 < q < 1.5:
                                        pts = ((q - 1.5) * (-1.0) / 0.35) * 75.0
                                    elif 0.85 <= q <= 1.15:
                                        pts = 75.0
                                    elif 0.5 < q < 0.85:
                                        pts = ((q - 0.5) / 0.35) * 75.0
                                    else:
                                        pts = 0.0

                                    state_f1["pts"] = pts
                                    lbl_q.set_text(f"Resultado Q (O / P): {q:.4f}")
                                    lbl_pts_q.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_q.set_text(
                                        "Resultado Q: Indefinido (A Receita Prevista 'P' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_q.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f1["pts"] = 0.0

                            input_o.on("update:model-value", calcular_q)
                            input_p.on("update:model-value", calcular_q)
                            calcular_q()

                        input_link_f1 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f1["link"],
                                placeholder="Insira o link do balanço orçamentário ou relatório contábil...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f1():
                            calcular_q()

                            val_dict = {
                                "O": float(state_f1["val_o"])
                                if state_f1["val_o"] is not None
                                else 0.0,
                                "P": float(state_f1["val_p"])
                                if state_f1["val_p"] is not None
                                else 0.0,
                            }

                            try:
                                save_resposta(
                                    ano=ano_sel,
                                    qid="F1",
                                    valor=val_dict,
                                    pontos=state_f1["pts"],
                                    link=input_link_f1.value,
                                    comentarios=f1_data.get("comentarios", []),
                                    status=f1_data.get("status", "Pendente"),
                                )

                                res_data["F1"] = {
                                    "valor": val_dict,
                                    "pontos": state_f1["pts"],
                                    "link": input_link_f1.value,
                                    "comentarios": f1_data.get("comentarios", []),
                                    "status": f1_data.get("status", "Pendente"),
                                }

                                ui.notify(
                                    "Quesito F1 salvo com sucesso no Banco de Dados!",
                                    type="positive",
                                )
                                render_conteudo.refresh()
                            except Exception as e:
                                ui.notify(
                                    f"Erro ao salvar no Banco de Dados: {e}",
                                    type="negative",
                                )

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f1).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F1", res_data, render_conteudo.refresh)
                    # ==========================================
                    # QUESITO F2 (Análise da Despesa - Execução Orçamentária)
                    # ==========================================
                    f2_data = res_data.get("F2", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F2 • Análise da Despesa (Execução Orçamentária) – Resultado Consolidado"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Divisão da despesa executada (R) pela despesa fixada final (S), com base nos dados da LOA (T = R / S):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador T", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **T >= 1,1:** Pontuação = **0,0 ponto**
                            * **1,0 < T < 1,1:** Graduação entre 75 e 0 `((T - 1,1) * (-1) / 0,10) * 75`
                            * **0,9 <= T <= 1,0:** Pontuação máxima = **75,0 pontos**
                            * **0,5 < T < 0,9:** Graduação entre 0 e 75 `((T - 0,5) / 0,40) * 75`
                            * **T <= 0,5:** Pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador T"
                            ).classes("font-bold text-blue-700 mb-2")

                            # Desserialização segura do dicionário salvo
                            val_f2_bruto = f2_data.get("valor", {})
                            while isinstance(val_f2_bruto, str):
                                try:
                                    val_f2_bruto = json.loads(val_f2_bruto)
                                except Exception:
                                    val_f2_bruto = {}
                                    break

                            if not isinstance(val_f2_bruto, dict):
                                val_f2_bruto = {}

                            raw_r = val_f2_bruto.get("R", val_f2_bruto.get("r", 0.0))
                            raw_s = val_f2_bruto.get("S", val_f2_bruto.get("s", 0.0))

                            try:
                                val_r_init = float(raw_r) if raw_r is not None else 0.0
                            except (ValueError, TypeError):
                                val_r_init = 0.0

                            try:
                                val_s_init = float(raw_s) if raw_s is not None else 0.0
                            except (ValueError, TypeError):
                                val_s_init = 0.0

                            state_f2 = {
                                "val_r": val_r_init,
                                "val_s": val_s_init,
                                "link": f2_data.get("link", ""),
                                "pts": float(f2_data.get("pontos", 0.0)),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_r = (
                                    ui.number(
                                        label="Despesa Executada (R)", format="%.2f"
                                    )
                                    .bind_value(state_f2, "val_r")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_s = (
                                    ui.number(
                                        label="Despesa Fixada Final (S)", format="%.2f"
                                    )
                                    .bind_value(state_f2, "val_s")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_t = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_t = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_t(_=None):
                                try:
                                    r = (
                                        float(state_f2["val_r"])
                                        if state_f2["val_r"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    r = 0.0

                                try:
                                    s = (
                                        float(state_f2["val_s"])
                                        if state_f2["val_s"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    s = 0.0

                                if s > 0:
                                    t = r / s
                                    if t >= 1.1:
                                        pts = 0.0
                                    elif 1.0 < t < 1.1:
                                        pts = ((t - 1.1) * (-1.0) / 0.10) * 75.0
                                    elif 0.9 <= t <= 1.0:
                                        pts = 75.0
                                    elif 0.5 < t < 0.9:
                                        pts = ((t - 0.5) / 0.40) * 75.0
                                    else:
                                        pts = 0.0

                                    state_f2["pts"] = pts
                                    lbl_t.set_text(f"Resultado T (R / S): {t:.4f}")
                                    lbl_pts_t.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_t.set_text(
                                        "Resultado T: Indefinido (A Despesa Fixada 'S' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_t.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f2["pts"] = 0.0

                            input_r.on("update:model-value", calcular_t)
                            input_s.on("update:model-value", calcular_t)
                            calcular_t()

                        input_link_f2 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f2["link"],
                                placeholder="Insira o link do balanço orçamentário ou relatório contábil...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f2():
                            calcular_t()

                            val_dict = {
                                "R": float(state_f2["val_r"])
                                if state_f2["val_r"] is not None
                                else 0.0,
                                "S": float(state_f2["val_s"])
                                if state_f2["val_s"] is not None
                                else 0.0,
                            }

                            try:
                                save_resposta(
                                    ano=ano_sel,
                                    qid="F2",
                                    valor=val_dict,
                                    pontos=state_f2["pts"],
                                    link=input_link_f2.value,
                                    comentarios=f2_data.get("comentarios", []),
                                    status=f2_data.get("status", "Pendente"),
                                )

                                res_data["F2"] = {
                                    "valor": val_dict,
                                    "pontos": state_f2["pts"],
                                    "link": input_link_f2.value,
                                    "comentarios": f2_data.get("comentarios", []),
                                    "status": f2_data.get("status", "Pendente"),
                                }

                                ui.notify(
                                    "Quesito F2 salvo com sucesso no Banco de Dados!",
                                    type="positive",
                                )
                                render_conteudo.refresh()
                            except Exception as e:
                                ui.notify(
                                    f"Erro ao salvar no Banco de Dados: {e}",
                                    type="negative",
                                )

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f2).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F2", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F3 (Análise do Resultado da Execução Orçamentária)
                    # ==========================================
                    f3_data = res_data.get("F3", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F3 • Análise do Resultado da Execução Orçamentária – Resultado Consolidado"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Relação entre Despesa Executada (R) e Receita Arrecadada (O), considerando a cobertura do déficit por Superávit Financeiro (V = R / O):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador V", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **V >= 1,2:** Pontuação = **0,0 ponto**
                            * **1,1 < V < 1,2 (COM cobertura do déficit):** Graduação entre 100 e 0 `((V - 1,2) * (-1) / 0,10) * 100`
                            * **1,0 < V < 1,2 (SEM cobertura do déficit):** Pontuação = **0,0 ponto**
                            * **1,0 < V <= 1,1 (COM cobertura do déficit):** Pontuação máxima = **100,0 pontos**
                            * **0,9 <= V <= 1,0:** Pontuação máxima = **100,0 pontos**
                            * **0,75 < V < 0,9:** Graduação entre 0 e 100 `((V - 0,75) / 0,15) * 100`
                            * **V <= 0,75:** Pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador V"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f3_bruto = f3_data.get("valor", {})
                            if not isinstance(val_f3_bruto, dict):
                                val_f3_bruto = {}

                            state_f3 = {
                                "val_r": float(val_f3_bruto.get("R", 0.0)),
                                "val_o": float(val_f3_bruto.get("O", 0.0)),
                                "val_superavit": float(
                                    val_f3_bruto.get("superavit", 0.0)
                                ),
                                "link": f3_data.get("link", ""),
                                "pts": float(f3_data.get("pontos", 0.0)),
                            }

                            with ui.grid(columns=3).classes("w-full gap-4"):
                                input_r = (
                                    ui.number(
                                        label="Despesa Executada (R)",
                                        value=state_f3["val_r"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_o = (
                                    ui.number(
                                        label="Receita Arrecadada (O)",
                                        value=state_f3["val_o"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_superavit = (
                                    ui.number(
                                        label="Créditos de Superávit Financeiro",
                                        value=state_f3["val_superavit"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_v = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_cobertura = ui.label().classes(
                                "text-sm font-semibold text-blue-800 mt-1"
                            )
                            lbl_pts_v = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_v(_=None):
                                r = input_r.value or 0.0
                                o = input_o.value or 0.0
                                sup = input_superavit.value or 0.0

                                state_f3["val_r"] = r
                                state_f3["val_o"] = o
                                state_f3["val_superavit"] = sup

                                if o > 0:
                                    v = r / o
                                    deficit = abs(o - r)
                                    tem_cobertura = sup >= deficit

                                    if v >= 1.2:
                                        pts = 0.0
                                        lbl_cobertura.set_text(
                                            "Déficit excessivo (V >= 1,2)"
                                        )
                                    elif 1.1 < v < 1.2:
                                        if tem_cobertura:
                                            pts = ((v - 1.2) * (-1.0) / 0.10) * 100.0
                                            lbl_cobertura.set_text(
                                                f"Déficit (R$ {deficit:,.2f}) COBERTO por Superávit (R$ {sup:,.2f})"
                                            )
                                        else:
                                            pts = 0.0
                                            lbl_cobertura.set_text(
                                                f"Déficit (R$ {deficit:,.2f}) NÃO COBERTO por Superávit (R$ {sup:,.2f})"
                                            )
                                    elif 1.0 < v <= 1.1:
                                        if tem_cobertura:
                                            pts = 100.0
                                            lbl_cobertura.set_text(
                                                f"Déficit (R$ {deficit:,.2f}) COBERTO por Superávit (R$ {sup:,.2f})"
                                            )
                                        else:
                                            pts = 0.0
                                            lbl_cobertura.set_text(
                                                f"Déficit (R$ {deficit:,.2f}) NÃO COBERTO por Superávit (R$ {sup:,.2f})"
                                            )
                                    elif 0.9 <= v <= 1.0:
                                        pts = 100.0
                                        lbl_cobertura.set_text(
                                            "Execução em equilíbrio/superavitária"
                                        )
                                    elif 0.75 < v < 0.9:
                                        pts = ((v - 0.75) / 0.15) * 100.0
                                        lbl_cobertura.set_text(
                                            "Execução abaixo do ideal (Graduação proporcional)"
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_cobertura.set_text(
                                            "Execução criticamente abaixo do planejado (V <= 0,75)"
                                        )

                                    state_f3["pts"] = pts
                                    lbl_v.set_text(f"Resultado V (R / O): {v:.4f}")
                                    lbl_pts_v.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_v.set_text(
                                        "Resultado V: Indefinido (A Receita Arrecadada 'O' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_cobertura.set_text("")
                                    lbl_pts_v.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f3["pts"] = 0.0

                            input_r.on("update:model-value", calcular_v)
                            input_o.on("update:model-value", calcular_v)
                            input_superavit.on("update:model-value", calcular_v)
                            calcular_v()

                        input_link_f3 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f3["link"],
                                placeholder="Insira o link do balanço orçamentário, apuração do superávit ou demonstrativos...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f3():
                            save_resposta(
                                ano=ano_sel,
                                qid="F3",
                                valor={
                                    "R": state_f3["val_r"],
                                    "O": state_f3["val_o"],
                                    "superavit": state_f3["val_superavit"],
                                },
                                pontos=state_f3["pts"],
                                link=input_link_f3.value,
                                comentarios=f3_data.get("comentarios", []),
                                status=f3_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F3 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f3).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F3", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F4 (Análise do Nível de Cancelamento de Restos a Pagar)
                    # ==========================================
                    f4_data = res_data.get("F4", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F4 • Análise do Nível de Cancelamento de Restos a Pagar – Resultado Consolidado"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Divisão dos cancelamentos realizados dos restos a pagar (C) pela sua posição inicial (B), com base na AUDESP (K = C / B):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador K", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **K >= 0,20:** Pontuação = **0,0 ponto**
                            * **0,05 < K < 0,20:** Graduação entre 0 e 25 `((0,20 - K) / 0,15) * 25`
                            * **K <= 0,05:** Pontuação máxima = **25,0 pontos**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador K"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f4_bruto = f4_data.get("valor", {})
                            if not isinstance(val_f4_bruto, dict):
                                val_f4_bruto = {}

                            state_f4 = {
                                "val_c": float(val_f4_bruto.get("C", 0.0) or 0.0),
                                "val_b": float(val_f4_bruto.get("B", 0.0) or 0.0),
                                "link": f4_data.get("link", ""),
                                "pts": float(f4_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_c = (
                                    ui.number(
                                        label="Cancelamentos de Restos a Pagar (C)",
                                        value=state_f4["val_c"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_b = (
                                    ui.number(
                                        label="Posição Inicial dos Restos a Pagar (B)",
                                        value=state_f4["val_b"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_k = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_k = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_k(_=None):
                                try:
                                    c = float(input_c.value or 0.0)
                                    b = float(input_b.value or 0.0)
                                except (ValueError, TypeError):
                                    c, b = 0.0, 0.0

                                state_f4["val_c"] = c
                                state_f4["val_b"] = b

                                if b > 0:
                                    k = c / b
                                    if k >= 0.20:
                                        pts = 0.0
                                    elif 0.05 < k < 0.20:
                                        pts = ((0.20 - k) / 0.15) * 25.0
                                    else:
                                        pts = 25.0

                                    state_f4["pts"] = pts
                                    lbl_k.set_text(f"Resultado K (C / B): {k:.4f}")
                                    lbl_pts_k.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_k.set_text(
                                        "Resultado K: Indefinido (A Posição Inicial 'B' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_k.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f4["pts"] = 0.0

                            input_c.on("update:model-value", calcular_k)
                            input_b.on("update:model-value", calcular_k)
                            calcular_k()

                        input_link_f4 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f4["link"],
                                placeholder="Insira o link ou relatório GF26 do AUDESP referente aos Restos a Pagar...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f4():
                            save_resposta(
                                ano=ano_sel,
                                qid="F4",
                                valor={"C": state_f4["val_c"], "B": state_f4["val_b"]},
                                pontos=state_f4["pts"],
                                link=input_link_f4.value,
                                comentarios=f4_data.get("comentarios", []),
                                status=f4_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F4 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f4).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F4", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F5 (Despesas com Pessoal – Poder Executivo)
                    # ==========================================
                    f5_data = res_data.get("F5", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F5 • Despesas com Pessoal – Poder Executivo").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Índice do comprometimento da receita corrente líquida com despesa de pessoal do Poder Executivo (extraído do item GF27 da AUDESP):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador de Pessoal", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Maior que 0,54 (54% - Limite Máximo LRF):** **Rebaixa 1 faixa do i-Fiscal** (Penalidade máxima / Nota impactada na classificação final)
                            * **Entre 0,513 e 0,54 (51,3% a 54% - Limite Prudencial LRF):** Penalidade de **-20,0 pontos**
                            * **Menor que 0,513 (Abaixo de 51,3%):** Sem penalidade (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática de Despesa com Pessoal"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f5_bruto = f5_data.get("valor", {})
                            if not isinstance(val_f5_bruto, dict):
                                val_f5_bruto = {}

                            state_f5 = {
                                "perc_pessoal": float(
                                    val_f5_bruto.get("perc_pessoal", 0.0) or 0.0
                                ),
                                "link": f5_data.get("link", ""),
                                "pts": float(f5_data.get("pontos", 0.0) or 0.0),
                                "rebaixa": val_f5_bruto.get("rebaixa_faixa", False),
                            }

                            input_pessoal = (
                                ui.number(
                                    label="Índice de Despesa com Pessoal (Ex: 0.52 para 52% ou digite o valor decimal)",
                                    value=state_f5["perc_pessoal"],
                                    format="%.4f",
                                )
                                .classes("w-full")
                                .props("outlined bg-white step=0.001")
                            )

                            lbl_res_pessoal = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_pessoal = ui.label().classes(
                                "text-sm font-bold mt-1"
                            )

                            def calcular_pessoal(_=None):
                                try:
                                    val = float(input_pessoal.value or 0.0)
                                except (ValueError, TypeError):
                                    val = 0.0

                                state_f5["perc_pessoal"] = val

                                if val > 0.54:
                                    pts = 0.0
                                    state_f5["rebaixa_faixa"] = True
                                    lbl_res_pessoal.set_text(
                                        f"Índice Apurado: {val * 100:.2f}% (Acima do Limite Máximo de 54%)"
                                    )
                                    lbl_pts_pessoal.set_text(
                                        "⚠️ ALERTA CRÍTICO: Rebaixa 1 faixa do i-Fiscal!"
                                    )
                                    lbl_pts_pessoal.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )
                                elif 0.513 <= val <= 0.54:
                                    pts = -20.0
                                    state_f5["rebaixa_faixa"] = False
                                    lbl_res_pessoal.set_text(
                                        f"Índice Apurado: {val * 100:.2f}% (Enquadrado no Limite Prudencial - 51,3% a 54%)"
                                    )
                                    lbl_pts_pessoal.set_text(
                                        "📊 Impacto de Pontuação Calculado: -20.00 pontos"
                                    )
                                    lbl_pts_pessoal.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-amber-600",
                                    )
                                else:
                                    pts = 0.0
                                    state_f5["rebaixa_faixa"] = False
                                    lbl_res_pessoal.set_text(
                                        f"Índice Apurado: {val * 100:.2f}% (Dentro do limite regular - Menor que 51,3%)"
                                    )
                                    lbl_pts_pessoal.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    lbl_pts_pessoal.classes(
                                        remove="text-red-600 text-amber-600",
                                        add="text-green-600",
                                    )

                                state_f5["pts"] = pts

                            input_pessoal.on("update:model-value", calcular_pessoal)
                            calcular_pessoal()

                        input_link_f5 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f5["link"],
                                placeholder="Insira o link ou relatório GF27 do AUDESP / Relatório de Gestão Fiscal (RGF)...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f5():
                            save_resposta(
                                ano=ano_sel,
                                qid="F5",
                                valor={
                                    "perc_pessoal": state_f5["perc_pessoal"],
                                    "rebaixa_faixa": state_f5.get(
                                        "rebaixa_faixa", False
                                    ),
                                },
                                pontos=state_f5["pts"],
                                link=input_link_f5.value,
                                comentarios=f5_data.get("comentarios", []),
                                status=f5_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F5 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f5).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F5", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F6 (Despesas com Pessoal – Poder Legislativo)
                    # ==========================================
                    f6_data = res_data.get("F6", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F6 • Despesas com Pessoal – Poder Legislativo"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Relação entre a Despesa de Pessoal do Poder Legislativo (DPPL) e a Receita Corrente Líquida (RCL), extraída do item GF27 da AUDESP (AB = DPPL / RCL):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AB", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AB > 0,06 (Acima de 6,0% - Limite Máximo LRF):** Penalidade máxima (**-10,0 pontos**)
                            * **0,057 < AB <= 0,06 (Graduação Proporcional):** Penalidade calculada por `((AB - 0,057) / 0,003) * -10`
                            * **AB <= 0,057 (Até 5,7%):** Dentro do limite regular (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AB"
                            ).classes("font-bold text-blue-700 mb-2")

                            # Desserialização segura do dicionário salvo
                            val_f6_bruto = f6_data.get("valor", {})
                            while isinstance(val_f6_bruto, str):
                                try:
                                    val_f6_bruto = json.loads(val_f6_bruto)
                                except Exception:
                                    val_f6_bruto = {}
                                    break

                            if not isinstance(val_f6_bruto, dict):
                                val_f6_bruto = {}

                            raw_dppl = val_f6_bruto.get(
                                "DPPL", val_f6_bruto.get("dppl", 0.0)
                            )
                            raw_rcl = val_f6_bruto.get(
                                "RCL", val_f6_bruto.get("rcl", 0.0)
                            )

                            try:
                                val_dppl_init = (
                                    float(raw_dppl) if raw_dppl is not None else 0.0
                                )
                            except (ValueError, TypeError):
                                val_dppl_init = 0.0

                            try:
                                val_rcl_init = (
                                    float(raw_rcl) if raw_rcl is not None else 0.0
                                )
                            except (ValueError, TypeError):
                                val_rcl_init = 0.0

                            state_f6 = {
                                "val_dppl": val_dppl_init,
                                "val_rcl": val_rcl_init,
                                "link": f6_data.get("link", ""),
                                "pts": float(f6_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_dppl = (
                                    ui.number(
                                        label="Despesa de Pessoal - Legislativo (DPPL)",
                                        format="%.2f",
                                    )
                                    .bind_value(state_f6, "val_dppl")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_rcl = (
                                    ui.number(
                                        label="Receita Corrente Líquida (RCL)",
                                        format="%.2f",
                                    )
                                    .bind_value(state_f6, "val_rcl")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_ab = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_ab = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_ab(_=None):
                                try:
                                    dppl = (
                                        float(state_f6["val_dppl"])
                                        if state_f6["val_dppl"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    dppl = 0.0

                                try:
                                    rcl = (
                                        float(state_f6["val_rcl"])
                                        if state_f6["val_rcl"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    rcl = 0.0

                                if rcl > 0:
                                    ab = dppl / rcl
                                    if ab > 0.06:
                                        pts = -10.0
                                        lbl_pts_ab.classes(
                                            remove="text-green-600 text-amber-600",
                                            add="text-red-600",
                                        )
                                    elif 0.057 < ab <= 0.06:
                                        pts = ((ab - 0.057) / 0.003) * -10.0
                                        lbl_pts_ab.classes(
                                            remove="text-green-600 text-red-600",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_ab.classes(
                                            remove="text-red-600 text-amber-600",
                                            add="text-green-600",
                                        )

                                    state_f6["pts"] = pts
                                    lbl_ab.set_text(
                                        f"Resultado AB (DPPL / RCL): {ab:.4f} ({ab * 100:.2f}%)"
                                    )
                                    lbl_pts_ab.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_ab.set_text(
                                        "Resultado AB: Indefinido (A RCL deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_ab.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    lbl_pts_ab.classes(
                                        remove="text-red-600 text-amber-600",
                                        add="text-green-600",
                                    )
                                    state_f6["pts"] = 0.0

                            input_dppl.on("update:model-value", calcular_ab)
                            input_rcl.on("update:model-value", calcular_ab)
                            calcular_ab()

                        input_link_f6 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f6["link"],
                                placeholder="Insira o link do Relatório GF27 do AUDESP ou Relatório de Gestão Fiscal (RGF) da Câmara...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f6():
                            calcular_ab()

                            val_dict = {
                                "DPPL": float(state_f6["val_dppl"])
                                if state_f6["val_dppl"] is not None
                                else 0.0,
                                "RCL": float(state_f6["val_rcl"])
                                if state_f6["val_rcl"] is not None
                                else 0.0,
                            }

                            try:
                                save_resposta(
                                    ano=ano_sel,
                                    qid="F6",
                                    valor=val_dict,
                                    pontos=state_f6["pts"],
                                    link=input_link_f6.value,
                                    comentarios=f6_data.get("comentarios", []),
                                    status=f6_data.get("status", "Pendente"),
                                )

                                res_data["F6"] = {
                                    "valor": val_dict,
                                    "pontos": state_f6["pts"],
                                    "link": input_link_f6.value,
                                    "comentarios": f6_data.get("comentarios", []),
                                    "status": f6_data.get("status", "Pendente"),
                                }

                                ui.notify(
                                    "Quesito F6 salvo com sucesso no Banco de Dados!",
                                    type="positive",
                                )
                                render_conteudo.refresh()
                            except Exception as e:
                                ui.notify(
                                    f"Erro ao salvar no Banco de Dados: {e}",
                                    type="negative",
                                )

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f6).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F6", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F7 (Apuração do Resultado Financeiro – Resultado Consolidado)
                    # ==========================================
                    f7_data = res_data.get("F7", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F7 • Apuração do Resultado Financeiro (Superávit/Déficit) – Resultado Consolidado"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Divisão entre o Ativo Financeiro (AC) e o Passivo Financeiro (AD), extraído do Balanço Patrimonial AUDESP (AE = AC / AD):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AE", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AE >= 1,30 (Superávit Elevado):** Pontuação = **0,0 ponto** *(Economia excessiva)*
                            * **1,10 < AE < 1,30 (Superávit Moderado):** Graduação entre 75 e 0 `((1,30 - AE) / 0,20) * 75`
                            * **1,00 <= AE <= 1,10 (Equilíbrio Ideal):** Pontuação máxima = **75,0 pontos**
                            * **0,75 < AE < 1,00 (Déficit Moderado):** Graduação entre 0 e 75 `((AE - 0,75) / 0,25) * 75`
                            * **AE <= 0,75 (Déficit Elevado):** Pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AE"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f7_bruto = f7_data.get("valor", {})
                            if not isinstance(val_f7_bruto, dict):
                                val_f7_bruto = {}

                            state_f7 = {
                                "val_ac": float(val_f7_bruto.get("AC", 0.0) or 0.0),
                                "val_ad": float(val_f7_bruto.get("AD", 0.0) or 0.0),
                                "link": f7_data.get("link", ""),
                                "pts": float(f7_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_ac = (
                                    ui.number(
                                        label="Ativo Financeiro (AC)",
                                        value=state_f7["val_ac"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_ad = (
                                    ui.number(
                                        label="Passivo Financeiro (AD)",
                                        value=state_f7["val_ad"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_ae = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_ae = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_ae(_=None):
                                try:
                                    ac = float(input_ac.value or 0.0)
                                    ad = float(input_ad.value or 0.0)
                                except (ValueError, TypeError):
                                    ac, ad = 0.0, 0.0

                                state_f7["val_ac"] = ac
                                state_f7["val_ad"] = ad

                                if ad > 0:
                                    ae = ac / ad
                                    if ae >= 1.30:
                                        pts = 0.0
                                    elif 1.10 < ae < 1.30:
                                        pts = ((1.30 - ae) / 0.20) * 75.0
                                    elif 1.00 <= ae <= 1.10:
                                        pts = 75.0
                                    elif 0.75 < ae < 1.00:
                                        pts = ((ae - 0.75) / 0.25) * 75.0
                                    else:
                                        pts = 0.0

                                    state_f7["pts"] = pts
                                    lbl_ae.set_text(f"Resultado AE (AC / AD): {ae:.4f}")
                                    lbl_pts_ae.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_ae.set_text(
                                        "Resultado AE: Indefinido (O Passivo Financeiro 'AD' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_ae.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f7["pts"] = 0.0

                            input_ac.on("update:model-value", calcular_ae)
                            input_ad.on("update:model-value", calcular_ae)
                            calcular_ae()

                        input_link_f7 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f7["link"],
                                placeholder="Insira o link ou relatório do Balanço Patrimonial AUDESP referente ao Ativo e Passivo Financeiro...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f7():
                            save_resposta(
                                ano=ano_sel,
                                qid="F7",
                                valor={
                                    "AC": state_f7["val_ac"],
                                    "AD": state_f7["val_ad"],
                                },
                                pontos=state_f7["pts"],
                                link=input_link_f7.value,
                                comentarios=f7_data.get("comentarios", []),
                                status=f7_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F7 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f7).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F7", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F8 (Apuração da Dívida Fundada - Aumento/Redução)
                    # ==========================================
                    f8_data = res_data.get("F8", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F8 • Apuração da Dívida Fundada (Aumento/Redução)"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Relação entre a Dívida Consolidada Líquida (DCL) e a Receita Corrente Líquida (RCL), extraída do item GF28 da AUDESP (AF = DCL / RCL):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AF", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AF > 1,20 (Acima de 120% da RCL):** Penalidade máxima (**-10,0 pontos**)
                            * **1,10 <= AF <= 1,20 (Graduação Proporcional):** Penalidade calculada por `((AF - 1,10) / 0,10) * -10,0`
                            * **AF < 1,10 (Abaixo de 110% da RCL):** Dentro do limite seguro (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AF"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f8_bruto = f8_data.get("valor", {})
                            if not isinstance(val_f8_bruto, dict):
                                val_f8_bruto = {}

                            state_f8 = {
                                "val_dcl": float(val_f8_bruto.get("DCL", 0.0) or 0.0),
                                "val_rcl": float(val_f8_bruto.get("RCL", 0.0) or 0.0),
                                "link": f8_data.get("link", ""),
                                "pts": float(f8_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_dcl = (
                                    ui.number(
                                        label="Dívida Consolidada Líquida (DCL)",
                                        value=state_f8["val_dcl"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_rcl = (
                                    ui.number(
                                        label="Receita Corrente Líquida (RCL)",
                                        value=state_f8["val_rcl"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_af = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_af = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_af(_=None):
                                try:
                                    dcl = float(input_dcl.value or 0.0)
                                    rcl = float(input_rcl.value or 0.0)
                                except (ValueError, TypeError):
                                    dcl, rcl = 0.0, 0.0

                                state_f8["val_dcl"] = dcl
                                state_f8["val_rcl"] = rcl

                                if rcl > 0:
                                    af = dcl / rcl
                                    if af > 1.20:
                                        pts = -10.0
                                        lbl_pts_af.classes(
                                            remove="text-green-600 text-amber-600",
                                            add="text-red-600",
                                        )
                                    elif 1.10 <= af <= 1.20:
                                        pts = ((af - 1.10) / 0.10) * -10.0
                                        lbl_pts_af.classes(
                                            remove="text-green-600 text-red-600",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_af.classes(
                                            remove="text-red-600 text-amber-600",
                                            add="text-green-600",
                                        )

                                    state_f8["pts"] = pts
                                    lbl_af.set_text(
                                        f"Resultado AF (DCL / RCL): {af:.4f} ({af * 100:.2f}%)"
                                    )
                                    lbl_pts_af.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_af.set_text(
                                        "Resultado AF: Indefinido (A RCL deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_af.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    lbl_pts_af.classes(
                                        remove="text-red-600 text-amber-600",
                                        add="text-green-600",
                                    )
                                    state_f8["pts"] = 0.0

                            input_dcl.on("update:model-value", calcular_af)
                            input_rcl.on("update:model-value", calcular_af)
                            calcular_af()

                        input_link_f8 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f8["link"],
                                placeholder="Insira o link ou relatório GF28 do AUDESP referente à Dívida Consolidada Líquida...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f8():
                            save_resposta(
                                ano=ano_sel,
                                qid="F8",
                                valor={
                                    "DCL": state_f8["val_dcl"],
                                    "RCL": state_f8["val_rcl"],
                                },
                                pontos=state_f8["pts"],
                                link=input_link_f8.value,
                                comentarios=f8_data.get("comentarios", []),
                                status=f8_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F8 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f8).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F8", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F9 (Apuração dos Pagamentos dos Precatórios)
                    # ==========================================
                    f9_data = res_data.get("F9", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F9 • Apuração dos Pagamentos dos Precatórios"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Relação entre o Estoque Final (AG) e o Estoque Inicial (AH) dos Precatórios, extraídos da contabilidade AUDESP (AI = AG / AH):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AI", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AI >= 1,00 (Estoque Mantido ou Aumentado):** Pontuação = **0,0 ponto**
                            * **0,90 < AI < 1,00 (Redução Parcial):** Graduação entre 0 e 75 `((1,00 - AI) / 0,10) * 75,0`
                            * **AI <= 0,90 (Redução de 10% ou mais):** Pontuação máxima = **75,0 pontos**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AI"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f9_bruto = f9_data.get("valor", {})
                            if not isinstance(val_f9_bruto, dict):
                                val_f9_bruto = {}

                            state_f9 = {
                                "val_ag": float(val_f9_bruto.get("AG", 0.0) or 0.0),
                                "val_ah": float(val_f9_bruto.get("AH", 0.0) or 0.0),
                                "link": f9_data.get("link", ""),
                                "pts": float(f9_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_ag = (
                                    ui.number(
                                        label="Estoque Final dos Precatórios (AG)",
                                        value=state_f9["val_ag"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_ah = (
                                    ui.number(
                                        label="Estoque Inicial dos Precatórios (AH)",
                                        value=state_f9["val_ah"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_ai = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_ai = ui.label().classes(
                                "text-sm font-bold text-green-600 mt-1"
                            )

                            def calcular_ai(_=None):
                                try:
                                    ag = float(input_ag.value or 0.0)
                                    ah = float(input_ah.value or 0.0)
                                except (ValueError, TypeError):
                                    ag, ah = 0.0, 0.0

                                state_f9["val_ag"] = ag
                                state_f9["val_ah"] = ah

                                if ah > 0:
                                    ai_val = ag / ah
                                    if ai_val >= 1.0:
                                        pts = 0.0
                                    elif 0.90 < ai_val < 1.0:
                                        pts = ((1.0 - ai_val) / 0.10) * 75.0
                                    else:
                                        pts = 75.0

                                    state_f9["pts"] = pts
                                    lbl_ai.set_text(
                                        f"Resultado AI (AG / AH): {ai_val:.4f}"
                                    )
                                    lbl_pts_ai.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_ai.set_text(
                                        "Resultado AI: Indefinido (O Estoque Inicial 'AH' deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_ai.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos"
                                    )
                                    state_f9["pts"] = 0.0

                            input_ag.on("update:model-value", calcular_ai)
                            input_ah.on("update:model-value", calcular_ai)
                            calcular_ai()

                        input_link_f9 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f9["link"],
                                placeholder="Insira o link ou relatório AUDESP referente ao Estoque de Precatórios...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f9():
                            save_resposta(
                                ano=ano_sel,
                                qid="F9",
                                valor={
                                    "AG": state_f9["val_ag"],
                                    "AH": state_f9["val_ah"],
                                },
                                pontos=state_f9["pts"],
                                link=input_link_f9.value,
                                comentarios=f9_data.get("comentarios", []),
                                status=f9_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F9 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f9).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F9", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F10 (Repasse de Duodécimos às Câmaras)
                    # ==========================================
                    f10_data = res_data.get("F10", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F10 • Repasse de Duodécimos às Câmaras").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Verificação do limite percentual de repasse à Câmara de Vereadores (Transferências à Câmara / Receita Base):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Repasse de Duodécimos", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Maior que 6,00% (Acima do Limite Constitucional):** **REBAIXA IEG-M PARA A FAIXA C** (Penalidade máxima de rebaixamento de faixa)
                            * **Menor ou igual a 6,00% (Dentro do Limite):** Conforme a regra (**0,0 ponto** / Sem penalidade)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Repasse de Duodécimo"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f10_bruto = f10_data.get("valor", {})
                            if not isinstance(val_f10_bruto, dict):
                                val_f10_bruto = {}

                            state_f10 = {
                                "perc_duodecimo": float(
                                    val_f10_bruto.get("perc_duodecimo", 0.0) or 0.0
                                ),
                                "link": f10_data.get("link", ""),
                                "pts": float(f10_data.get("pontos", 0.0) or 0.0),
                                "rebaixa_c": val_f10_bruto.get(
                                    "rebaixa_faixa_c", False
                                ),
                            }

                            input_duodecimo = (
                                ui.number(
                                    label="Percentual de Repasse Apurado (Ex: 0.058 para 5,8% ou digite o valor decimal)",
                                    value=state_f10["perc_duodecimo"],
                                    format="%.4f",
                                )
                                .classes("w-full")
                                .props("outlined bg-white step=0.001")
                            )

                            lbl_res_duodecimo = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_duodecimo = ui.label().classes(
                                "text-sm font-bold mt-1"
                            )

                            def calcular_duodecimo(_=None):
                                try:
                                    val = float(input_duodecimo.value or 0.0)
                                except (ValueError, TypeError):
                                    val = 0.0

                                state_f10["perc_duodecimo"] = val

                                if val > 0.06:
                                    pts = 0.0
                                    state_f10["rebaixa_faixa_c"] = True
                                    lbl_res_duodecimo.set_text(
                                        f"Percentual Repassado: {val * 100:.2f}% (Acima do limite de 6,00%)"
                                    )
                                    lbl_pts_duodecimo.set_text(
                                        "⚠️ ALERTA CRÍTICO: Rebaixa o IEG-M diretamente para a FAIXA C!"
                                    )
                                    lbl_pts_duodecimo.classes(
                                        remove="text-green-600", add="text-red-600"
                                    )
                                else:
                                    pts = 0.0
                                    state_f10["rebaixa_faixa_c"] = False
                                    lbl_res_duodecimo.set_text(
                                        f"Percentual Repassado: {val * 100:.2f}% (Dentro do limite regular - Até 6,00%)"
                                    )
                                    lbl_pts_duodecimo.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos (Sem penalidade)"
                                    )
                                    lbl_pts_duodecimo.classes(
                                        remove="text-red-600", add="text-green-600"
                                    )

                                state_f10["pts"] = pts

                            input_duodecimo.on("update:model-value", calcular_duodecimo)
                            calcular_duodecimo()

                        input_link_f10 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f10["link"],
                                placeholder="Insira o link da demonstração contábil do repasse de duodécimo / relatório de contas do AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f10():
                            save_resposta(
                                ano=ano_sel,
                                qid="F10",
                                valor={
                                    "perc_duodecimo": state_f10["perc_duodecimo"],
                                    "rebaixa_faixa_c": state_f10.get(
                                        "rebaixa_faixa_c", False
                                    ),
                                },
                                pontos=state_f10["pts"],
                                link=input_link_f10.value,
                                comentarios=f10_data.get("comentarios", []),
                                status=f10_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F10 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f10).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F10", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F11 (Pontualidade na Prestação de Contas)
                    # ==========================================
                    f11_data = res_data.get("F11", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F11 • Pontualidade na Prestação de Contas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Avaliação do cumprimento dos prazos na entrega de relatórios, peças contábeis, conciliações e questionários no Sistema AUDESP:"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras de Pontualidade", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Encaminhou no prazo:** Pontuação máxima = **50,0 pontos**
                            * **Encaminhou fora do prazo:** Pontuação parcial = **25,0 pontos**
                            * **Não encaminhou:** Sem pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label("🧮 Seleção de Status do Envio").classes(
                                "font-bold text-blue-700 mb-2"
                            )

                            val_f11_bruto = f11_data.get("valor", {})
                            if not isinstance(val_f11_bruto, dict):
                                val_f11_bruto = {"status_envio": "Encaminhou no prazo"}

                            state_f11 = {
                                "status_envio": val_f11_bruto.get(
                                    "status_envio", "Encaminhou no prazo"
                                ),
                                "link": f11_data.get("link", ""),
                                "pts": float(f11_data.get("pontos", 50.0) or 50.0),
                            }

                            radio_pontualidade = ui.radio(
                                options=[
                                    "Encaminhou no prazo",
                                    "Encaminhou fora do prazo",
                                    "Não encaminhou",
                                ],
                                value=state_f11["status_envio"],
                            ).classes("w-full mb-2")

                            lbl_pts_f11 = ui.label().classes("text-sm font-bold mt-2")

                            def calcular_f11(_=None):
                                opcao = radio_pontualidade.value
                                state_f11["status_envio"] = opcao

                                if opcao == "Encaminhou no prazo":
                                    pts = 50.0
                                    lbl_pts_f11.set_text(
                                        "📊 Impacto de Pontuação Calculado: 50.00 pontos (Pontuação Máxima)"
                                    )
                                    lbl_pts_f11.classes(
                                        remove="text-amber-600 text-red-600",
                                        add="text-green-600",
                                    )
                                elif opcao == "Encaminhou fora do prazo":
                                    pts = 25.0
                                    lbl_pts_f11.set_text(
                                        "📊 Impacto de Pontuação Calculado: 25.00 pontos (Envio com Atraso)"
                                    )
                                    lbl_pts_f11.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-amber-600",
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_f11.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos (Inadimplente / Não Enviado)"
                                    )
                                    lbl_pts_f11.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )

                                state_f11["pts"] = pts

                            radio_pontualidade.on("update:model-value", calcular_f11)
                            calcular_f11()

                        input_link_f11 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f11["link"],
                                placeholder="Insira o link do Relatório de Situação de Entrega do Sistema AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f11():
                            save_resposta(
                                ano=ano_sel,
                                qid="F11",
                                valor={"status_envio": state_f11["status_envio"]},
                                pontos=state_f11["pts"],
                                link=input_link_f11.value,
                                comentarios=f11_data.get("comentarios", []),
                                status=f11_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F11 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f11).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F11", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F12 (Dívida Ativa: Percentual de Recebimento)
                    # ==========================================
                    f12_data = res_data.get("F12", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F12 • Dívida Ativa: Percentual de Recebimento"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Nível de recebimento da Dívida Ativa em relação ao estoque inicial (AL = Valor Arrecadado / Estoque Inicial da Dívida Ativa):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AL", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AL >= 0,10 (Arrecadação >= 10% do estoque):** Pontuação máxima (**50,0 pontos**)
                            * **0,00 < AL < 0,10 (Graduação Proporcional):** Pontuação calculada por `(AL / 0,10) * 50,0`
                            * **AL = 0,00 (Nenhum recebimento):** Sem pontuação (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AL"
                            ).classes("font-bold text-blue-700 mb-2")

                            # Desserialização segura do dicionário salvo
                            val_f12_bruto = f12_data.get("valor", {})
                            while isinstance(val_f12_bruto, str):
                                try:
                                    val_f12_bruto = json.loads(val_f12_bruto)
                                except Exception:
                                    val_f12_bruto = {}
                                    break

                            if not isinstance(val_f12_bruto, dict):
                                val_f12_bruto = {}

                            raw_arr = val_f12_bruto.get(
                                "arrecadado", val_f12_bruto.get("ARRECADADO", 0.0)
                            )
                            raw_est = val_f12_bruto.get(
                                "estoque_inicial",
                                val_f12_bruto.get("ESTOQUE_INICIAL", 0.0),
                            )

                            try:
                                val_arr_init = (
                                    float(raw_arr) if raw_arr is not None else 0.0
                                )
                            except (ValueError, TypeError):
                                val_arr_init = 0.0

                            try:
                                val_est_init = (
                                    float(raw_est) if raw_est is not None else 0.0
                                )
                            except (ValueError, TypeError):
                                val_est_init = 0.0

                            state_f12 = {
                                "val_arrecadado": val_arr_init,
                                "val_estoque": val_est_init,
                                "link": f12_data.get("link", ""),
                                "pts": float(f12_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_arrecadado = (
                                    ui.number(
                                        label="Valor Arrecadado da Dívida Ativa",
                                        format="%.2f",
                                    )
                                    .bind_value(state_f12, "val_arrecadado")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_estoque = (
                                    ui.number(
                                        label="Estoque Inicial da Dívida Ativa",
                                        format="%.2f",
                                    )
                                    .bind_value(state_f12, "val_estoque")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_al = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f12 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f12(_=None):
                                try:
                                    arrecadado = (
                                        float(state_f12["val_arrecadado"])
                                        if state_f12["val_arrecadado"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    arrecadado = 0.0

                                try:
                                    estoque = (
                                        float(state_f12["val_estoque"])
                                        if state_f12["val_estoque"] is not None
                                        else 0.0
                                    )
                                except (ValueError, TypeError):
                                    estoque = 0.0

                                if estoque > 0:
                                    al = arrecadado / estoque
                                    if al >= 0.10:
                                        pts = 50.0
                                        lbl_pts_f12.classes(
                                            remove="text-red-600 text-amber-600 text-gray-500",
                                            add="text-green-600",
                                        )
                                    elif 0.0 < al < 0.10:
                                        pts = (al / 0.10) * 50.0
                                        lbl_pts_f12.classes(
                                            remove="text-green-600 text-red-600 text-gray-500",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_f12.classes(
                                            remove="text-green-600 text-amber-600 text-gray-500",
                                            add="text-red-600",
                                        )

                                    state_f12["pts"] = pts
                                    lbl_al.set_text(
                                        f"Resultado AL (Arrecadado / Estoque): {al:.4f} ({al * 100:.2f}%)"
                                    )
                                    lbl_pts_f12.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_al.set_text(
                                        "Resultado AL: Indefinido (O Estoque Inicial deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f12.set_text(
                                        "⚠️ Informe os valores válidos de Estoque Inicial e Arrecadação."
                                    )
                                    lbl_pts_f12.classes(
                                        remove="text-green-600 text-amber-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f12["pts"] = 0.0

                            input_arrecadado.on("update:model-value", calcular_f12)
                            input_estoque.on("update:model-value", calcular_f12)
                            calcular_f12()

                        input_link_f12 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f12["link"],
                                placeholder="Insira o link do Relatório de Análises Anuais Eletrônicas do Sistema AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f12():
                            calcular_f12()

                            if state_f12["val_estoque"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para o Estoque Inicial da Dívida Ativa!",
                                    type="warning",
                                )
                                return

                            val_dict = {
                                "arrecadado": float(state_f12["val_arrecadado"])
                                if state_f12["val_arrecadado"] is not None
                                else 0.0,
                                "estoque_inicial": float(state_f12["val_estoque"])
                                if state_f12["val_estoque"] is not None
                                else 0.0,
                            }

                            try:
                                save_resposta(
                                    ano=ano_sel,
                                    qid="F12",
                                    valor=val_dict,
                                    pontos=state_f12["pts"],
                                    link=input_link_f12.value,
                                    comentarios=f12_data.get("comentarios", []),
                                    status=f12_data.get("status", "Pendente"),
                                )

                                res_data["F12"] = {
                                    "valor": val_dict,
                                    "pontos": state_f12["pts"],
                                    "link": input_link_f12.value,
                                    "comentarios": f12_data.get("comentarios", []),
                                    "status": f12_data.get("status", "Pendente"),
                                }

                                ui.notify(
                                    "Quesito F12 salvo com sucesso no Banco de Dados!",
                                    type="positive",
                                )
                                render_conteudo.refresh()
                            except Exception as e:
                                ui.notify(
                                    f"Erro ao salvar no Banco de Dados: {e}",
                                    type="negative",
                                )

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f12).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F12", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F13 (Dívida Ativa: Percentual de Cancelamento)
                    # ==========================================
                    f13_data = res_data.get("F13", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F13 • Dívida Ativa: Percentual de Cancelamento"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Nível de cancelamento da Dívida Ativa em relação ao estoque inicial (AM = Valor Cancelado / Estoque Inicial da Dívida Ativa):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador AM", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **AM = 0,00 (Nenhum cancelamento):** Pontuação máxima (**50,0 pontos**)
                            * **0,00 < AM < 0,10 (Graduação Proporcional):** Pontuação calculada por `((AM - 0,10) * (-1) / 0,10) * 50,0`
                            * **AM >= 0,10 (Cancelamento >= 10% do estoque):** Sem pontuação (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador AM"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f13_bruto = f13_data.get("valor", {})
                            if not isinstance(val_f13_bruto, dict):
                                val_f13_bruto = {}

                            state_f13 = {
                                "val_cancelado": float(
                                    val_f13_bruto.get("cancelado", 0.0) or 0.0
                                ),
                                "val_estoque": float(
                                    val_f13_bruto.get("estoque_inicial", 0.0) or 0.0
                                ),
                                "link": f13_data.get("link", ""),
                                "pts": float(f13_data.get("pontos", 50.0) or 50.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_cancelado = (
                                    ui.number(
                                        label="Valor Cancelado da Dívida Ativa",
                                        value=state_f13["val_cancelado"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_estoque = (
                                    ui.number(
                                        label="Estoque Inicial da Dívida Ativa",
                                        value=state_f13["val_estoque"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_am = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f13 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f13(_=None):
                                try:
                                    cancelado = float(input_cancelado.value or 0.0)
                                    estoque = float(input_estoque.value or 0.0)
                                except (ValueError, TypeError):
                                    cancelado, estoque = 0.0, 0.0

                                state_f13["val_cancelado"] = cancelado
                                state_f13["val_estoque"] = estoque

                                if estoque > 0:
                                    am = cancelado / estoque
                                    if am == 0.0:
                                        pts = 50.0
                                        lbl_pts_f13.classes(
                                            remove="text-red-600 text-amber-600 text-gray-500",
                                            add="text-green-600",
                                        )
                                    elif 0.0 < am < 0.10:
                                        pts = ((am - 0.10) * (-1) / 0.10) * 50.0
                                        lbl_pts_f13.classes(
                                            remove="text-green-600 text-red-600 text-gray-500",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_f13.classes(
                                            remove="text-green-600 text-amber-600 text-gray-500",
                                            add="text-red-600",
                                        )

                                    state_f13["pts"] = pts
                                    lbl_am.set_text(
                                        f"Resultado AM (Cancelado / Estoque): {am:.4f} ({am * 100:.2f}%)"
                                    )
                                    lbl_pts_f13.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_am.set_text(
                                        "Resultado AM: Indefinido (O Estoque Inicial deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f13.set_text(
                                        "⚠️ Informe os valores válidos de Estoque Inicial e Cancelamento."
                                    )
                                    lbl_pts_f13.classes(
                                        remove="text-green-600 text-amber-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f13["pts"] = 0.0

                            input_cancelado.on("update:model-value", calcular_f13)
                            input_estoque.on("update:model-value", calcular_f13)
                            calcular_f13()

                        input_link_f13 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f13["link"],
                                placeholder="Insira o link do Relatório de Análises Anuais Eletrônicas do Sistema AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f13():
                            if state_f13["val_estoque"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para o Estoque Inicial da Dívida Ativa!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F13",
                                valor={
                                    "cancelado": state_f13["val_cancelado"],
                                    "estoque_inicial": state_f13["val_estoque"],
                                },
                                pontos=state_f13["pts"],
                                link=input_link_f13.value,
                                comentarios=f13_data.get("comentarios", []),
                                status=f13_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F13 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f13).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F13", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F14 (Alertas do Sistema AUDESP)
                    # ==========================================
                    f14_data = res_data.get("F14", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F14 • Alertas do Sistema AUDESP").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Quantidade total de alertas gerados pelo Sistema AUDESP no exercício:"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Quesito F14", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Até 20 Alertas (<= 20):** Pontuação máxima = **25,0 pontos**
                            * **Entre 21 e 40 Alertas (20 < Alertas < 41):** Pontuação intermediária = **10,0 pontos**
                            * **41 Alertas ou mais (>= 41):** Sem pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label("🧮 Apuração da Quantidade de Alertas").classes(
                                "font-bold text-blue-700 mb-2"
                            )

                            val_f14_bruto = f14_data.get("valor", {})
                            if not isinstance(val_f14_bruto, dict):
                                val_f14_bruto = {}

                            state_f14 = {
                                "qtd_alertas": int(
                                    val_f14_bruto.get("qtd_alertas", 0) or 0
                                ),
                                "link": f14_data.get("link", ""),
                                "pts": float(f14_data.get("pontos", 25.0) or 25.0),
                            }

                            input_alertas = (
                                ui.number(
                                    label="Quantidade de Alertas do AUDESP no Exercício",
                                    value=state_f14["qtd_alertas"],
                                    format="%d",
                                    precision=0,
                                )
                                .classes("w-full")
                                .props("outlined bg-white min=0 step=1")
                            )

                            lbl_pts_f14 = ui.label().classes("text-sm font-bold mt-2")

                            def calcular_f14(_=None):
                                try:
                                    qtd = int(input_alertas.value or 0)
                                except (ValueError, TypeError):
                                    qtd = 0

                                state_f14["qtd_alertas"] = qtd

                                if qtd <= 20:
                                    pts = 25.0
                                    lbl_pts_f14.set_text(
                                        f"Alertas apurados: {qtd} | Pontuação: 25.00 pontos (Faixa Ótima)"
                                    )
                                    lbl_pts_f14.classes(
                                        remove="text-amber-600 text-red-600",
                                        add="text-green-600",
                                    )
                                elif 20 < qtd < 41:
                                    pts = 10.0
                                    lbl_pts_f14.set_text(
                                        f"Alertas apurados: {qtd} | Pontuação: 10.00 pontos (Faixa Intermediária)"
                                    )
                                    lbl_pts_f14.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-amber-600",
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_f14.set_text(
                                        f"Alertas apurados: {qtd} | Pontuação: 0.00 pontos (Faixa Crítica - 41 ou mais alertas)"
                                    )
                                    lbl_pts_f14.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )

                                state_f14["pts"] = pts

                            input_alertas.on("update:model-value", calcular_f14)
                            calcular_f14()

                        input_link_f14 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f14["link"],
                                placeholder="Insira o link ou relatório de consolidação de Alertas emitidos pelo Sistema AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f14():
                            if state_f14["qtd_alertas"] < 0:
                                ui.notify(
                                    "A quantidade de alertas não pode ser um número negativo!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F14",
                                valor={"qtd_alertas": state_f14["qtd_alertas"]},
                                pontos=state_f14["pts"],
                                link=input_link_f14.value,
                                comentarios=f14_data.get("comentarios", []),
                                status=f14_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F14 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f14).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F14", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F15 (Balancetes Rejeitados)
                    # ==========================================
                    f15_data = res_data.get("F15", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F15 • Balancetes Rejeitados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Quantidade média de balancetes rejeitados pelo município no exercício:"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Quesito F15", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Até 1 Balancete Rejeitado (<= 1):** Pontuação máxima = **25,0 pontos**
                            * **Mais de 1 e menos de 18 (1 < Rejeitados < 18):** Pontuação intermediária = **10,0 pontos**
                            * **18 Balancetes Rejeitados ou mais (>= 18):** Sem pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Apuração da Quantidade Média de Balancetes Rejeitados"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f15_bruto = f15_data.get("valor", {})
                            if not isinstance(val_f15_bruto, dict):
                                val_f15_bruto = {}

                            state_f15 = {
                                "qtd_rejeitados": float(
                                    val_f15_bruto.get("qtd_rejeitados", 0.0) or 0.0
                                ),
                                "link": f15_data.get("link", ""),
                                "pts": float(f15_data.get("pontos", 25.0) or 25.0),
                            }

                            input_rejeitados = (
                                ui.number(
                                    label="Quantidade Média de Balancetes Rejeitados no Exercício",
                                    value=state_f15["qtd_rejeitados"],
                                    format="%.2f",
                                    precision=2,
                                )
                                .classes("w-full")
                                .props("outlined bg-white min=0 step=0.1")
                            )

                            lbl_pts_f15 = ui.label().classes("text-sm font-bold mt-2")

                            def calcular_f15(_=None):
                                try:
                                    qtd = float(input_rejeitados.value or 0.0)
                                except (ValueError, TypeError):
                                    qtd = 0.0

                                state_f15["qtd_rejeitados"] = qtd

                                if qtd <= 1.0:
                                    pts = 25.0
                                    lbl_pts_f15.set_text(
                                        f"Balancetes rejeitados: {qtd:.2f} | Pontuação: 25.00 pontos (Faixa Ótima)"
                                    )
                                    lbl_pts_f15.classes(
                                        remove="text-amber-600 text-red-600",
                                        add="text-green-600",
                                    )
                                elif 1.0 < qtd < 18.0:
                                    pts = 10.0
                                    lbl_pts_f15.set_text(
                                        f"Balancetes rejeitados: {qtd:.2f} | Pontuação: 10.00 pontos (Faixa Intermediária)"
                                    )
                                    lbl_pts_f15.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-amber-600",
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_f15.set_text(
                                        f"Balancetes rejeitados: {qtd:.2f} | Pontuação: 0.00 pontos (Faixa Crítica - 18 ou mais)"
                                    )
                                    lbl_pts_f15.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )

                                state_f15["pts"] = pts

                            input_rejeitados.on("update:model-value", calcular_f15)
                            calcular_f15()

                        input_link_f15 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f15["link"],
                                placeholder="Insira o link ou relatório de acompanhamento de balancetes do AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f15():
                            if state_f15["qtd_rejeitados"] < 0:
                                ui.notify(
                                    "A quantidade de balancetes rejeitados não pode ser negativa!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F15",
                                valor={"qtd_rejeitados": state_f15["qtd_rejeitados"]},
                                pontos=state_f15["pts"],
                                link=input_link_f15.value,
                                comentarios=f15_data.get("comentarios", []),
                                status=f15_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F15 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f15).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F15", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F16 (Resultado Primário - Operacional)
                    # ==========================================
                    f16_data = res_data.get("F16", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F16 • Resultado Primário (Operacional)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Capacidade de reduzir o endividamento municipal (RP = Receitas Realizadas [RR] - Despesas Liquidadas [DL] do 6º Bimestre):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Quesito F16", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **Resultado Primário Acima de ZERO (RP > 0):** Pontuação máxima = **75,0 pontos**
                            * **Resultado Primário Igual a ZERO (RP = 0):** Pontuação intermediária = **40,0 pontos**
                            * **Resultado Primário Abaixo de ZERO (RP < 0):** Sem pontuação = **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Apuração do Resultado Primário (RP = RR - DL)"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f16_bruto = f16_data.get("valor", {})
                            if not isinstance(val_f16_bruto, dict):
                                val_f16_bruto = {}

                            state_f16 = {
                                "val_rr": float(
                                    val_f16_bruto.get("receitas_realizadas", 0.0) or 0.0
                                ),
                                "val_dl": float(
                                    val_f16_bruto.get("despesas_liquidadas", 0.0) or 0.0
                                ),
                                "rp": float(
                                    val_f16_bruto.get("resultado_primario", 0.0) or 0.0
                                ),
                                "link": f16_data.get("link", ""),
                                "pts": float(f16_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_rr = (
                                    ui.number(
                                        label="Receitas Realizadas (RR) - RLR",
                                        value=state_f16["val_rr"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_dl = (
                                    ui.number(
                                        label="Despesas Liquidadas (DL) - DPL",
                                        value=state_f16["val_dl"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_rp = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f16 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f16(_=None):
                                try:
                                    rr = float(input_rr.value or 0.0)
                                    dl = float(input_dl.value or 0.0)
                                except (ValueError, TypeError):
                                    rr, dl = 0.0, 0.0

                                state_f16["val_rr"] = rr
                                state_f16["val_dl"] = dl
                                rp = rr - dl
                                state_f16["rp"] = rp

                                if rp > 0.0:
                                    pts = 75.0
                                    lbl_pts_f16.set_text(
                                        "📊 Impacto de Pontuação Calculado: 75.00 pontos (Resultado Primário Superavitário)"
                                    )
                                    lbl_pts_f16.classes(
                                        remove="text-amber-600 text-red-600",
                                        add="text-green-600",
                                    )
                                elif rp == 0.0:
                                    pts = 40.0
                                    lbl_pts_f16.set_text(
                                        "📊 Impacto de Pontuação Calculado: 40.00 pontos (Resultado Primário Equilibrado)"
                                    )
                                    lbl_pts_f16.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-amber-600",
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_f16.set_text(
                                        "📊 Impacto de Pontuação Calculado: 0.00 pontos (Resultado Primário Deficitário)"
                                    )
                                    lbl_pts_f16.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )

                                state_f16["pts"] = pts
                                lbl_rp.set_text(
                                    f"Resultado Primário Calculado (RP): R$ {rp:,.2f}"
                                )

                            input_rr.on("update:model-value", calcular_f16)
                            input_dl.on("update:model-value", calcular_f16)
                            calcular_f16()

                        input_link_f16 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f16["link"],
                                placeholder="Insira o link do Demonstrativo do Resultado Primário do 6º bimestre (Item GF20 AUDESP)...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f16():
                            save_resposta(
                                ano=ano_sel,
                                qid="F16",
                                valor={
                                    "receitas_realizadas": state_f16["val_rr"],
                                    "despesas_liquidadas": state_f16["val_dl"],
                                    "resultado_primario": state_f16["rp"],
                                },
                                pontos=state_f16["pts"],
                                link=input_link_f16.value,
                                comentarios=f16_data.get("comentarios", []),
                                status=f16_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F16 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f16).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F16", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F17 (Índice de Liquidez Imediata)
                    # ==========================================
                    f17_data = res_data.get("F17", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F17 • Índice de Liquidez Imediata").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Capacidade de pagamento com recursos do Ativo Disponível (IL = Disponível [D] / Passivo Circulante [PC]):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador IL", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **IL >= 1,00 (Disponível cobre integralmente o PC):** Pontuação máxima (**75,0 pontos**)
                            * **0,80 < IL < 1,00 (Graduação Proporcional):** Pontuação calculada por `((IL - 0.80) * 75.0) / 0.20`
                            * **IL <= 0,80 (Liquidez Insuficiente):** Sem pontuação (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador IL"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f17_bruto = f17_data.get("valor", {})
                            if not isinstance(val_f17_bruto, dict):
                                val_f17_bruto = {}

                            state_f17 = {
                                "val_disponivel": float(
                                    val_f17_bruto.get("disponivel", 0.0) or 0.0
                                ),
                                "val_passivo_circulante": float(
                                    val_f17_bruto.get("passivo_circulante", 0.0) or 0.0
                                ),
                                "link": f17_data.get("link", ""),
                                "pts": float(f17_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_disponivel = (
                                    ui.number(
                                        label="Ativo Disponível (D)",
                                        value=state_f17["val_disponivel"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_passivo = (
                                    ui.number(
                                        label="Passivo Circulante (PC)",
                                        value=state_f17["val_passivo_circulante"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_il = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f17 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f17(_=None):
                                try:
                                    d = float(input_disponivel.value or 0.0)
                                    pc = float(input_passivo.value or 0.0)
                                except (ValueError, TypeError):
                                    d, pc = 0.0, 0.0

                                state_f17["val_disponivel"] = d
                                state_f17["val_passivo_circulante"] = pc

                                if pc > 0:
                                    il = d / pc
                                    if il >= 1.0:
                                        pts = 75.0
                                        lbl_pts_f17.classes(
                                            remove="text-red-600 text-amber-600 text-gray-500",
                                            add="text-green-600",
                                        )
                                    elif 0.80 < il < 1.0:
                                        pts = ((il - 0.80) * 75.0) / 0.20
                                        lbl_pts_f17.classes(
                                            remove="text-green-600 text-red-600 text-gray-500",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_f17.classes(
                                            remove="text-green-600 text-amber-600 text-gray-500",
                                            add="text-red-600",
                                        )

                                    state_f17["pts"] = pts
                                    lbl_il.set_text(
                                        f"Resultado IL (Disponível / Passivo Circulante): {il:.4f}"
                                    )
                                    lbl_pts_f17.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_il.set_text(
                                        "Resultado IL: Indefinido (O Passivo Circulante deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f17.set_text(
                                        "⚠️ Informe os valores válidos de Disponível e Passivo Circulante."
                                    )
                                    lbl_pts_f17.classes(
                                        remove="text-green-600 text-amber-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f17["pts"] = 0.0

                            input_disponivel.on("update:model-value", calcular_f17)
                            input_passivo.on("update:model-value", calcular_f17)
                            calcular_f17()

                        input_link_f17 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f17["link"],
                                placeholder="Insira o link do Relatório RAAE - Item 4.1 (Capacidade de Pagamento)...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f17():
                            if state_f17["val_passivo_circulante"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para o Passivo Circulante!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F17",
                                valor={
                                    "disponivel": state_f17["val_disponivel"],
                                    "passivo_circulante": state_f17[
                                        "val_passivo_circulante"
                                    ],
                                },
                                pontos=state_f17["pts"],
                                link=input_link_f17.value,
                                comentarios=f17_data.get("comentarios", []),
                                status=f17_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F17 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f17).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F17", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F18 (Limite de Endividamento - Regra de Ouro)
                    # ==========================================
                    f18_data = res_data.get("F18", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F18 • Limite de Endividamento – Regra de Ouro"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Verifica se as Operações de Crédito excedem as Despesas de Capital (RO = Operações de Crédito [OC] - Despesas de Capital [DC] - Créditos Autorizados por Maioria Absoluta [AL]):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador RO (Regra de Ouro)",
                            icon="info",
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **RO <= 0 (Cumpre a Regra de Ouro):** Pontuação neutra (**0,0 ponto**) - Sem penalidade na nota/faixa.
                            * **RO > 0 (Aumentou o endividamento para despesas correntes):** **Rebaixa 1 faixa na classificação do i-Fiscal**.
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Apuração do Indicador Regra de Ouro (RO = OC - DC - AL)"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f18_bruto = f18_data.get("valor", {})
                            if not isinstance(val_f18_bruto, dict):
                                val_f18_bruto = {}

                            state_f18 = {
                                "val_oc": float(
                                    val_f18_bruto.get("operacoes_credito", 0.0) or 0.0
                                ),
                                "val_dc": float(
                                    val_f18_bruto.get("despesas_capital", 0.0) or 0.0
                                ),
                                "val_al": float(
                                    val_f18_bruto.get("creditos_legislativo", 0.0)
                                    or 0.0
                                ),
                                "ro": float(
                                    val_f18_bruto.get("ro_calculado", 0.0) or 0.0
                                ),
                                "rebaixar_faixa": bool(
                                    val_f18_bruto.get("rebaixar_faixa", False)
                                ),
                                "link": f18_data.get("link", ""),
                                "pts": 0.0,
                            }

                            with ui.grid(columns=3).classes("w-full gap-4"):
                                input_oc = (
                                    ui.number(
                                        label="Operações de Crédito (OC)",
                                        value=state_f18["val_oc"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_dc = (
                                    ui.number(
                                        label="Despesas de Capital Liquidadas (DC)",
                                        value=state_f18["val_dc"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_al = (
                                    ui.number(
                                        label="Créditos Aut. Legislativo (AL)",
                                        value=state_f18["val_al"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_ro = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f18 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f18(_=None):
                                try:
                                    oc = float(input_oc.value or 0.0)
                                    dc = float(input_dc.value or 0.0)
                                    al = float(input_al.value or 0.0)
                                except (ValueError, TypeError):
                                    oc, dc, al = 0.0, 0.0, 0.0

                                state_f18["val_oc"] = oc
                                state_f18["val_dc"] = dc
                                state_f18["val_al"] = al

                                ro = oc - dc - al
                                state_f18["ro"] = ro

                                if ro <= 0.0:
                                    state_f18["rebaixar_faixa"] = False
                                    lbl_pts_f18.set_text(
                                        "✅ Cumpre a Regra de Ouro (RO <= 0) - Sem rebaixamento de faixa no i-Fiscal."
                                    )
                                    lbl_pts_f18.classes(
                                        remove="text-red-600 text-amber-600",
                                        add="text-green-600",
                                    )
                                else:
                                    state_f18["rebaixar_faixa"] = True
                                    lbl_pts_f18.set_text(
                                        "⚠️ RO > 0: Descumprimento da Regra de Ouro! O município REBAIARÁ 1 FAIXA no i-Fiscal."
                                    )
                                    lbl_pts_f18.classes(
                                        remove="text-green-600 text-amber-600",
                                        add="text-red-600",
                                    )

                                state_f18["pts"] = 0.0
                                lbl_ro.set_text(f"Resultado RO Calculado: R$ {ro:,.2f}")

                            input_oc.on("update:model-value", calcular_f18)
                            input_dc.on("update:model-value", calcular_f18)
                            input_al.on("update:model-value", calcular_f18)
                            calcular_f18()

                        input_link_f18 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f18["link"],
                                placeholder="Insira o link das demonstrações contábeis e relatórios de Operações de Crédito...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f18():
                            save_resposta(
                                ano=ano_sel,
                                qid="F18",
                                valor={
                                    "operacoes_credito": state_f18["val_oc"],
                                    "despesas_capital": state_f18["val_dc"],
                                    "creditos_legislativo": state_f18["val_al"],
                                    "ro_calculado": state_f18["ro"],
                                    "rebaixar_faixa": state_f18["rebaixar_faixa"],
                                },
                                pontos=state_f18["pts"],
                                link=input_link_f18.value,
                                comentarios=f18_data.get("comentarios", []),
                                status=f18_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F18 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f18).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F18", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F19 (Percentual da Taxa de Investimento)
                    # ==========================================
                    f19_data = res_data.get("F19", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F19 • Percentual da Taxa de Investimento").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Taxa de investimento em relação à receita arrecadada (N = (Despesas Liquidadas em Investimentos [L] + Liquidação de RPNP [F]) / Receita Total Arrecadada [M]):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador N (Taxa de Investimento)",
                            icon="info",
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **N >= 0,15 (Investimento >= 15% da Receita):** Pontuação máxima (**50,0 pontos**)
                            * **0,02 < N < 0,15 (Graduação Proporcional):** Pontuação calculada por `((N - 0,02) / 0,13) * 50,0`
                            * **N <= 0,02 (Investimento <= 2% da Receita):** Sem pontuação (**0,0 ponto**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador N"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f19_bruto = f19_data.get("valor", {})
                            if not isinstance(val_f19_bruto, dict):
                                val_f19_bruto = {}

                            state_f19 = {
                                "val_l": float(
                                    val_f19_bruto.get("desp_investimentos", 0.0) or 0.0
                                ),
                                "val_f": float(
                                    val_f19_bruto.get("liq_rpnp", 0.0) or 0.0
                                ),
                                "val_m": float(
                                    val_f19_bruto.get("receita_total", 0.0) or 0.0
                                ),
                                "link": f19_data.get("link", ""),
                                "pts": float(f19_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=3).classes("w-full gap-4"):
                                input_l = (
                                    ui.number(
                                        label="Despesa Liquidada - Grupo 44 (L)",
                                        value=state_f19["val_l"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_f = (
                                    ui.number(
                                        label="Liquidação de RPNP (F)",
                                        value=state_f19["val_f"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_m = (
                                    ui.number(
                                        label="Receita Total Arrecadada (M)",
                                        value=state_f19["val_m"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_n = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f19 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f19(_=None):
                                try:
                                    l_val = float(input_l.value or 0.0)
                                    f_val = float(input_f.value or 0.0)
                                    m_val = float(input_m.value or 0.0)
                                except (ValueError, TypeError):
                                    l_val, f_val, m_val = 0.0, 0.0, 0.0

                                state_f19["val_l"] = l_val
                                state_f19["val_f"] = f_val
                                state_f19["val_m"] = m_val

                                if m_val > 0:
                                    n_ind = (l_val + f_val) / m_val
                                    if n_ind >= 0.15:
                                        pts = 50.0
                                        lbl_pts_f19.classes(
                                            remove="text-red-600 text-amber-600 text-gray-500",
                                            add="text-green-600",
                                        )
                                    elif 0.02 < n_ind < 0.15:
                                        pts = ((n_ind - 0.02) / 0.13) * 50.0
                                        lbl_pts_f19.classes(
                                            remove="text-green-600 text-red-600 text-gray-500",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_f19.classes(
                                            remove="text-green-600 text-amber-600 text-gray-500",
                                            add="text-red-600",
                                        )

                                    state_f19["pts"] = pts
                                    lbl_n.set_text(
                                        f"Resultado N (Investimento Total / Receita Total): {n_ind:.4f} ({n_ind * 100:.2f}%)"
                                    )
                                    lbl_pts_f19.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_n.set_text(
                                        "Resultado N: Indefinido (A Receita Total deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f19.set_text(
                                        "⚠️ Informe valores válidos para calcular o indicador."
                                    )
                                    lbl_pts_f19.classes(
                                        remove="text-green-600 text-amber-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f19["pts"] = 0.0

                            input_l.on("update:model-value", calcular_f19)
                            input_f.on("update:model-value", calcular_f19)
                            input_m.on("update:model-value", calcular_f19)
                            calcular_f19()

                        input_link_f19 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f19["link"],
                                placeholder="Insira o link das demonstrações contábeis / balanço orçamentário do AUDESP...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f19():
                            if state_f19["val_m"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para a Receita Total!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F19",
                                valor={
                                    "desp_investimentos": state_f19["val_l"],
                                    "liq_rpnp": state_f19["val_f"],
                                    "receita_total": state_f19["val_m"],
                                },
                                pontos=state_f19["pts"],
                                link=input_link_f19.value,
                                comentarios=f19_data.get("comentarios", []),
                                status=f19_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F19 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f19).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F19", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F20 (Relação Despesas Correntes / Receitas Correntes - Art. 167-A)
                    # ==========================================
                    f20_data = res_data.get("F20", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label(
                            "F20 • Relação entre Despesas Correntes e Receitas Correntes"
                        ).classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label(
                            "Verifica o limite do Art. 167-A da CF (LDC = Despesa Corrente Liquidada [DC] / Receita Corrente Arrecadada [RC]):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador LDC (Art. 167-A da CF)",
                            icon="info",
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **LDC <= 0,85 (Gastos Correntes <= 85%):** Sem penalização (**0,0 ponto**)
                            * **0,85 < LDC <= 0,95 (Graduação Proporcional de Penalidade):** Perda de pontos calculada por `((LDC - 0,85) / 0,10) * (-50,0)`
                            * **LDC > 0,95 (Ultrpassou o limite de 95% do Art. 167-A):** Penalidade máxima (**-50,0 pontos**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label(
                                "🧮 Calculadora Automática do Indicador LDC"
                            ).classes("font-bold text-blue-700 mb-2")

                            val_f20_bruto = f20_data.get("valor", {})
                            if not isinstance(val_f20_bruto, dict):
                                val_f20_bruto = {}

                            state_f20 = {
                                "val_dc": float(
                                    val_f20_bruto.get("despesa_corrente", 0.0) or 0.0
                                ),
                                "val_rc": float(
                                    val_f20_bruto.get("receita_corrente", 0.0) or 0.0
                                ),
                                "link": f20_data.get("link", ""),
                                "pts": float(f20_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_dc = (
                                    ui.number(
                                        label="Despesa Corrente Liquidada (DC)",
                                        value=state_f20["val_dc"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_rc = (
                                    ui.number(
                                        label="Receita Corrente Arrecadada (RC)",
                                        value=state_f20["val_rc"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_ldc = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f20 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f20(_=None):
                                try:
                                    dc = float(input_dc.value or 0.0)
                                    rc = float(input_rc.value or 0.0)
                                except (ValueError, TypeError):
                                    dc, rc = 0.0, 0.0

                                state_f20["val_dc"] = dc
                                state_f20["val_rc"] = rc

                                if rc > 0:
                                    ldc = dc / rc
                                    if ldc <= 0.85:
                                        pts = 0.0
                                        lbl_pts_f20.classes(
                                            remove="text-red-600 text-amber-600 text-gray-500",
                                            add="text-green-600",
                                        )
                                    elif 0.85 < ldc <= 0.95:
                                        pts = ((ldc - 0.85) / 0.10) * (-50.0)
                                        lbl_pts_f20.classes(
                                            remove="text-green-600 text-red-600 text-gray-500",
                                            add="text-amber-600",
                                        )
                                    else:
                                        pts = -50.0
                                        lbl_pts_f20.classes(
                                            remove="text-green-600 text-amber-600 text-gray-500",
                                            add="text-red-600",
                                        )

                                    state_f20["pts"] = pts
                                    lbl_ldc.set_text(
                                        f"Resultado LDC (Despesa Corrente / Receita Corrente): {ldc:.4f} ({ldc * 100:.2f}%)"
                                    )
                                    lbl_pts_f20.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_ldc.set_text(
                                        "Resultado LDC: Indefinido (A Receita Corrente deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f20.set_text(
                                        "⚠️ Informe os valores válidos de Despesa e Receita Corrente."
                                    )
                                    lbl_pts_f20.classes(
                                        remove="text-green-600 text-amber-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f20["pts"] = 0.0

                            input_dc.on("update:model-value", calcular_f20)
                            input_rc.on("update:model-value", calcular_f20)
                            calcular_f20()

                        input_link_f20 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f20["link"],
                                placeholder="Insira o link do Relatório de Instrução (Item GF56 Audesp)...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f20():
                            if state_f20["val_rc"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para a Receita Corrente!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F20",
                                valor={
                                    "despesa_corrente": state_f20["val_dc"],
                                    "receita_corrente": state_f20["val_rc"],
                                },
                                pontos=state_f20["pts"],
                                link=input_link_f20.value,
                                comentarios=f20_data.get("comentarios", []),
                                status=f20_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F20 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f20).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F20", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO F21 (Liquidez dos Restos a Pagar - LRP)
                    # ==========================================
                    f21_data = res_data.get("F21", {})

                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("F21 • Liquidez dos Restos a Pagar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Verifica a cobertura financeira do estoque de Restos a Pagar (LRP = RPA / D):"
                        ).classes("text-base font-bold text-black mb-2")

                        with ui.expansion(
                            "ℹ️ Tabela de Regras do Indicador LRP", icon="info"
                        ).classes(
                            "w-full mb-4 bg-gray-50 border border-gray-200 rounded"
                        ):
                            ui.markdown("""
                            * **LRP <= 1,0 (Restos a Pagar <= Disponibilidade de Caixa):** Cobertura suficiente (**0,0 ponto**)
                            * **LRP > 1,0 (Restos a Pagar > Disponibilidade de Caixa):** Insuficiência de caixa para cobrir restos a pagar (**-5,0 pontos**)
                            """).classes("text-sm text-gray-700 p-2")

                        with ui.card().classes(
                            "w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"
                        ):
                            ui.label("🧮 Calculadora do Indicador LRP").classes(
                                "font-bold text-blue-700 mb-2"
                            )

                            val_f21_bruto = f21_data.get("valor", {})
                            if not isinstance(val_f21_bruto, dict):
                                val_f21_bruto = {}

                            state_f21 = {
                                "val_rpa": float(val_f21_bruto.get("rpa", 0.0) or 0.0),
                                "val_d": float(
                                    val_f21_bruto.get("disponivel", 0.0) or 0.0
                                ),
                                "link": f21_data.get("link", ""),
                                "pts": float(f21_data.get("pontos", 0.0) or 0.0),
                            }

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_rpa = (
                                    ui.number(
                                        label="Estoque de Restos a Pagar - Processados e Não Processados (RPA)",
                                        value=state_f21["val_rpa"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_d = (
                                    ui.number(
                                        label="Disponibilidade de Caixa / Disponível (D)",
                                        value=state_f21["val_d"],
                                        format="%.2f",
                                    )
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_lrp = ui.label().classes(
                                "text-sm font-bold text-gray-800 mt-2"
                            )
                            lbl_pts_f21 = ui.label().classes("text-sm font-bold mt-1")

                            def calcular_f21(_=None):
                                try:
                                    rpa = float(input_rpa.value or 0.0)
                                    d = float(input_d.value or 0.0)
                                except (ValueError, TypeError):
                                    rpa, d = 0.0, 0.0

                                state_f21["val_rpa"] = rpa
                                state_f21["val_d"] = d

                                if d > 0:
                                    lrp = rpa / d
                                    if lrp > 1.0:
                                        pts = -5.0
                                        lbl_pts_f21.classes(
                                            remove="text-green-600 text-gray-500",
                                            add="text-red-600",
                                        )
                                    else:
                                        pts = 0.0
                                        lbl_pts_f21.classes(
                                            remove="text-red-600 text-gray-500",
                                            add="text-green-600",
                                        )

                                    state_f21["pts"] = pts
                                    lbl_lrp.set_text(
                                        f"Resultado LRP (RPA / D): {lrp:.4f}"
                                    )
                                    lbl_pts_f21.set_text(
                                        f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos"
                                    )
                                else:
                                    lbl_lrp.set_text(
                                        "Resultado LRP: Indefinido (A Disponibilidade de Caixa deve ser maior que R$ 0,00)"
                                    )
                                    lbl_pts_f21.set_text(
                                        "⚠️ Informe os valores válidos de Restos a Pagar e Disponibilidade."
                                    )
                                    lbl_pts_f21.classes(
                                        remove="text-green-600 text-red-600",
                                        add="text-gray-500",
                                    )
                                    state_f21["pts"] = 0.0

                            input_rpa.on("update:model-value", calcular_f21)
                            input_d.on("update:model-value", calcular_f21)
                            calcular_f21()

                        input_link_f21 = (
                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=state_f21["link"],
                                placeholder="Insira o link do Relatório de Análises Anuais Eletrônicas (RAAE) / Relatório de Instrução (RI)...",
                            )
                            .classes("w-full mb-4")
                            .props("outlined rows=3")
                        )

                        def salvar_f21():
                            if state_f21["val_d"] <= 0:
                                ui.notify(
                                    "Por favor, informe um valor válido para a Disponibilidade de Caixa (D)!",
                                    type="warning",
                                )
                                return

                            save_resposta(
                                ano=ano_sel,
                                qid="F21",
                                valor={
                                    "rpa": state_f21["val_rpa"],
                                    "disponivel": state_f21["val_d"],
                                },
                                pontos=state_f21["pts"],
                                link=input_link_f21.value,
                                comentarios=f21_data.get("comentarios", []),
                                status=f21_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito F21 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_f21).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("F21", res_data, render_conteudo.refresh)

    render_conteudo()

    
import io
import os
import logging
from datetime import datetime
from io import BytesIO

import psycopg2
import psycopg2.extras

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    PageBreak,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart

import asyncio
from nicegui import app, ui
from fastapi import Response

# -----------------------------------------------------------------------------
# 1. MAPEAMENTOS E TETOS DE PONTUAÇÃO DO I-PLAN
# -----------------------------------------------------------------------------
PONTUACOES_MAX_Fiscal = {
    "1.1": 0.5,
    "1.2": 1.5,
    "1.3": 10.0,
    "1.4": 3.0,
    "1.5": 5.0,
    "1.5.1": 5.0,
    "2.0": 4.0,
    "3.0": 30.0,
    "4.3": 5.0,
    "5.0": 3.0,
    "5.3": 3.0,
    "5.4": 6.0,
    "6.0": 2.0,
    "8.0": 1.0,
    "8.1": 2.0,
    "8.2": 15.0,
    "9.4": 2.0,
    "9.4.1": 3.0,
    "11.0": 3.0,
    "13.0": 1.0,
    "13.3": 9.0,
    "18.1": 15.0,
    "19.0": 3.0,
    "19.1": 3.0,
    "20.0": 3.0,
    "20.1": 6.0,
    "21.0": 3.0,
    "22.0": 3.0,
    "F1": 75.0,
    "F2": 75.0,
    "F3": 100.0,
    "F4": 25.0,
    "F5": 25.0,
    "F8": 75.0,
    "F10": 75.0,
    "F12": 50.0,
    "F13": 50.0,
    "F14": 50.0,
    "F15": 25.0,
    "F16": 25.0,
    "F17": 75.0,
    "F18": 75.0,
}

# Aliases de compatibilidade para evitar NameError
PONTUACOES_MAX_IAMB = PONTUACOES_MAX_Fiscal
PONTUACOES_MAX = PONTUACOES_MAX_Fiscal

PENALIDADES_MAX = {
    "7.2": -3.0,
    "8.3": -15.0,
    "9.6": -30.0,
    "10.3": -5.0,
    "12.1": -10.0,
    "12.2": -5.0,
    "12.3": -5.0,
    "12.3.1": -5.0,
    "12.5.2": -10.0,
    "16": -10.0,
    "16.3": -5.0,
    "17.0": -5.0,
    "23.0": -30.0,
    "24.1": -30.0,
    "25.1": -25.0,
    "F6": -20.0,
    "F7": -10.0,
    "F9": -10.0,
    "F21": -50.0,
}

# String de conexão com o PostgreSQL
DATABASE_URL = "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


# -----------------------------------------------------------------------------
# 2. FUNÇÕES AUXILIARES E REGRAS DE NEGÓCIO I-Fiscal
# -----------------------------------------------------------------------------
def get_db_connection():
    """Cria conexão segura com o Neon PostgreSQL usando linhas por nome de coluna."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def calcular_percentual_checklist(resp, total_itens):
    """Calcula a porcentagem de itens atendidos em um checklist."""
    if not resp:
        return 0.0
    if isinstance(resp, list):
        qtd = len(resp)
    elif isinstance(resp, str):
        qtd = len([item for item in resp.split(",") if item.strip()])
    else:
        qtd = 0
    return min((qtd / total_itens) * 100.0, 100.0)


def obter_regra_ods_iamb(qid, resp):
    """Mapeia os quesitos do I-Fiscal para as metas da Agenda 2030 (ODS) e seu status."""
    resp_l = str(resp).strip().lower()
    metas = "-"
    status = "Não Atendido"

    if qid in ["1.0", "1.1"]:
        metas = "12.2, 15.2, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "1.1.2":
        metas = "12.8"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "2.0":
        metas = "4.7, 12.8, 15.1"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "3.0":
        metas = "12.2, 16.6, 17.14"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "4.0":
        metas = "12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "5.0":
        metas = "5.0"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "6.0":
        metas = "6.4, 6.b, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "6.2":
        metas = "6.4, 6.5, 6.b, 16.6"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid in ["7.0", "7.3"]:
        metas = "6.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid in ["7.4", "7.5"]:
        metas = "6.2, 6.3"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "7.7.1":
        metas = "6.0, 16.6"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid == "7.8":
        metas = "6.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "7.8.1":
        metas = "6.2, 6.3"
        status = (
            "Atendido"
            if "todas as metas foram cumpridas dentro do prazo" in resp_l
            else "Não Atendido"
        )
    elif qid == "7.9":
        metas = "6.2, 6.3"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid in ["8.0", "8.3", "8.4", "9.0"]:
        metas = "11.6, 12.5"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "8.3.1":
        metas = "11.6, 12.5, 12.4"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid == "8.4.1":
        metas = "11.6, 12.5, 12.4"
        pct = calcular_percentual_checklist(resp, 4)
        status = f"{pct:.1f}% Atendido"
    elif qid in ["10.0", "10.1"]:
        metas = "11.6, 12.5, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "10.2":
        metas = "11.6, 12.5, 16.6"
        status = (
            "Atendido"
            if "todos os bairros do município são atendidos" in resp_l
            else "Não Atendido"
        )
    elif qid == "10.3":
        metas = "11.6, 12.5, 12.4, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "11.0":
        metas = "11.6, 12.4, 12.5, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "12.0":
        metas = "11.6, 12.5, 12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "13.0":
        metas = "11.6, 12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "14.0":
        metas = "11.6, 12.4"
        status = "Atendido" if "não" in resp_l else "Não Atendido"
    elif qid == "15.0":
        metas = "12.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"

    return metas, status


def _desserializar_resposta(valor):
    """Converte respostas JSON armazenadas como texto sem perder textos comuns."""
    resultado = valor
    while isinstance(resultado, str):
        texto = resultado.strip()
        if not (
            (texto.startswith("[") and texto.endswith("]"))
            or (texto.startswith("{") and texto.endswith("}"))
        ):
            break
        try:
            resultado = json.loads(texto)
        except (TypeError, ValueError):
            break
    return resultado


def get_all_years_data():
    """Busca a série histórica do I-Fiscal no PostgreSQL Neon DB."""
    all_data = {}
    query = """
        SELECT ano, quesito, resposta, pontos, detalhes
        FROM respostas_ifiscal
        ORDER BY ano ASC;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()

                for row in rows:
                    ano = int(row["ano"])
                    qid = str(row["quesito"]).strip()
                    valor = row["resposta"] or ""
                    pontos = (
                        float(row["pontos"]) if row["pontos"] is not None else 0.0
                    )
                    detalhes = row["detalhes"] or {}
                    if isinstance(detalhes, str):
                        try:
                            detalhes = json.loads(detalhes)
                        except Exception:
                            detalhes = {}

                    if ano not in all_data:
                        all_data[ano] = {}

                    all_data[ano][qid] = {
                        "valor": _desserializar_resposta(valor),
                        "pontos": pontos,
                        "link": detalhes.get("link", ""),
                        "comentarios": detalhes.get("comentarios", [])
                        if isinstance(detalhes.get("comentarios", []), list)
                        else [],
                        "status": detalhes.get("status", "Pendente"),
                    }
    except Exception as e:
        print(f"❌ Erro ao buscar série histórica I-Fiscal no Neon DB: {e}")

    return all_data


def load_respostas(ano):
    """Carrega as respostas de um ano específico do PostgreSQL Neon DB."""
    respostas = {}
    query = """
        SELECT id, ano, quesito, resposta, pontos, detalhes
        FROM respostas_ifiscal
        WHERE ano = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()

                for row in rows:
                    qid = str(row["quesito"]).strip()
                    valor = row["resposta"] or ""
                    pontos = (
                        float(row["pontos"]) if row["pontos"] is not None else 0.0
                    )
                    detalhes = row["detalhes"] or {}
                    if isinstance(detalhes, str):
                        try:
                            detalhes = json.loads(detalhes)
                        except Exception:
                            detalhes = {}

                    respostas[qid] = {
                        "valor": _desserializar_resposta(valor),
                        "pontos": pontos,
                        "link": detalhes.get("link", ""),
                        "comentarios": detalhes.get("comentarios", [])
                        if isinstance(detalhes.get("comentarios", []), list)
                        else [],
                        "status": detalhes.get("status", "Pendente"),
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do ano {ano} no Neon DB: {e}")

    return respostas


def converter_para_float(val):
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", ".").strip())
    except (ValueError, TypeError):
        return 0.0


def converter_pontos_em_faixa_iegm(pontos):
    pts = float(pontos)
    if pts < 500.0:
        return "C"
    elif 500.0 <= pts <= 599.9:
        return "C+"
    elif 600.0 <= pts <= 749.9:
        return "B"
    elif 750.0 <= pts <= 899.9:
        return "B+"
    else:
        return "A"


# -----------------------------------------------------------------------------
# 3. GERADOR DE RELATÓRIO PDF COMPLETO (REPORTLAB)
# -----------------------------------------------------------------------------
def gerar_relatorio_pdf(dados, ano, total, faixa, todos_dados=None):
    """Gere o relatório completo e não-resumido do I-Fiscal em PDF."""

    lista_alvo_iplan = [
        "1.0",
        "1.1",
        "5.0",
        "7.0",
        "7.6",
        "7.6.1",
        "8.0",
        "9.0",
        "10.3",
        "11.0",
        "11.1",
        "11.6",
        "11.6.1",
        "12.0",
    ]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30,
    )
    elements = []
    styles = getSampleStyleSheet()

    # Definindo e registrando estilos estilizados do relatório
    styles.add(
        ParagraphStyle(
            "TitleCapa",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=24,
            textColor=colors.HexColor("#1b4f72"),
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "SubTitleCapa",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=14,
            textColor=colors.HexColor("#5D6D7E"),
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "ItemEsq",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=colors.HexColor("#2C3E50"),
        )
    )
    styles.add(
        ParagraphStyle(
            "PagDir",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=colors.HexColor("#1B4F72"),
            alignment=TA_RIGHT,
        )
    )
    styles.add(
        ParagraphStyle(
            "ThStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.white,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "TdStyle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            "TdCenter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "CellLink",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#1A5276"),
        )
    )

    ano_normalizado = int(str(ano).strip()[:4])
    ano_ant = ano_normalizado - 1
    todos_dados = todos_dados or {}

    # -------------------------------------------------------------------------
    # CAPA
    # -------------------------------------------------------------------------
    elements.append(Spacer(1, 40))
    logo_path = "iegm.png"
    if os.path.exists(logo_path):
        try:
            logo = Image(logo_path, width=350, height=160)
            logo.hAlign = "CENTER"
            elements.append(logo)
        except Exception:
            elements.append(
                Paragraph("<b>[IEGM - Planejamento]</b>", styles["TitleCapa"])
            )
    else:
        elements.append(Paragraph("<b>[IEGM - Planejamento]</b>", styles["TitleCapa"]))

    elements.append(Spacer(1, 40))

    # Título limpo em linha única
    elements.append(Paragraph("<b>Relatório I-PLAN</b>", styles["TitleCapa"]))
    elements.append(Spacer(1, 15))

    # Subtítulo com Ano de Referência
    elements.append(
        Paragraph(
            f"Exercício de Referência: <b>{ano_normalizado}</b>", styles["SubTitleCapa"]
        )
    )
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # SUMÁRIO
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>SUMÁRIO DE CONTEÚDO</b>", styles["Heading1"]))
    elements.append(Spacer(1, 20))

    itens_sumario = [
        [
            Paragraph("1. Resumo Executivo e Evolução Comparativa", styles["ItemEsq"]),
            Paragraph("Pág. 3", styles["PagDir"]),
        ],
        [
            Paragraph(
                "2. Análise Detalhada de Desempenho por Quesito", styles["ItemEsq"]
            ),
            Paragraph("Pág. 3", styles["PagDir"]),
        ],
        [
            Paragraph(
                "3. Quadro de Penalidades e Impactos Negativos", styles["ItemEsq"]
            ),
            Paragraph("Pág. 4", styles["PagDir"]),
        ],
        [
            Paragraph("4. Diagnóstico de Reincidências de Fracasso", styles["ItemEsq"]),
            Paragraph("Pág. 4", styles["PagDir"]),
        ],
        [
            Paragraph(
                "5. Alinhamento com a Agenda 2030 (Metas ODS)", styles["ItemEsq"]
            ),
            Paragraph("Pág. 5", styles["PagDir"]),
        ],
        [
            Paragraph(
                "6. Evolução Temporal da Série Histórica (I-PLAN)", styles["ItemEsq"]
            ),
            Paragraph("Pág. 5", styles["PagDir"]),
        ],
        [
            Paragraph(
                "7. Quesitos de Conformidade Operacional (Sem Pontuação Direta)",
                styles["ItemEsq"],
            ),
            Paragraph("Pág. 6", styles["PagDir"]),
        ],
    ]

    tabela_sumario = Table(itens_sumario, colWidths=[380, 100])
    tabela_sumario.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
            ]
        )
    )
    elements.append(tabela_sumario)
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 1. RESUMO EXECUTIVO
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph(
            "<b>1. RESUMO EXECUTIVO E EVOLUÇÃO COMPARATIVA</b>", styles["Heading2"]
        )
    )
    elements.append(Spacer(1, 8))

    nota_atual = converter_para_float(total)
    dados_ano_anterior = todos_dados.get(ano_ant) or todos_dados.get(str(ano_ant)) or {}
    nota_anterior = 0.0

    if isinstance(dados_ano_anterior, dict):
        for qid_ant, info_ant in dados_ano_anterior.items():
            if str(qid_ant).startswith("COM_"):
                continue
            pts = (
                info_ant.get("pontos", 0.0) if isinstance(info_ant, dict) else info_ant
            )
            nota_anterior += converter_para_float(pts)

    faixa_anterior = converter_pontos_em_faixa_iegm(nota_anterior)
    faixa_atual = faixa if faixa else converter_pontos_em_faixa_iegm(nota_atual)
    variacao_nominal = nota_atual - nota_anterior

    if nota_anterior > 0:
        variacao_pct = (variacao_nominal / nota_anterior) * 100.0
        str_pct = f"{variacao_pct:+.2f}%"
    else:
        str_pct = f"{variacao_nominal:+.1f} pts"

    cor_var = (
        colors.HexColor("#27AE60")
        if variacao_nominal >= 0
        else colors.HexColor("#C0392B")
    )
    simbolo = "▲" if variacao_nominal > 0 else ("▼" if variacao_nominal < 0 else "■")

    style_var_cell = ParagraphStyle(
        "VarCell",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=cor_var,
        alignment=TA_CENTER,
    )

    dados_exec = [
        [
            Paragraph("Exercício", styles["ThStyle"]),
            Paragraph("Pontuação Obteve", styles["ThStyle"]),
            Paragraph("Faixa IEGM", styles["ThStyle"]),
            Paragraph("Variação Nominal", styles["ThStyle"]),
            Paragraph("Variação %", styles["ThStyle"]),
        ],
        [
            Paragraph(str(ano_ant), styles["TdCenter"]),
            Paragraph(f"{nota_anterior:.1f} pts", styles["TdCenter"]),
            Paragraph(faixa_anterior, styles["TdCenter"]),
            Paragraph("-", styles["TdCenter"]),
            Paragraph("-", styles["TdCenter"]),
        ],
        [
            Paragraph(str(ano_normalizado), styles["TdCenter"]),
            Paragraph(f"{nota_atual:.1f} pts", styles["TdCenter"]),
            Paragraph(faixa_atual, styles["TdCenter"]),
            Paragraph(f"{simbolo} {variacao_nominal:+.1f}", style_var_cell),
            Paragraph(f"{simbolo} {str_pct}", style_var_cell),
        ],
    ]

    t_exec = Table(dados_exec, colWidths=[80, 100, 90, 105, 105])
    t_exec.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4F72")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(t_exec)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 2. DESEMPENHO POR QUESITO (FORTES E FRACOS)
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph(
            "<b>2. ANÁLISE DETALHADA DE DESEMPENHO POR QUESITO</b>", styles["Heading2"]
        )
    )
    elements.append(Spacer(1, 6))

    pontos_fortes, pontos_fracos, reincidencias = [], [], []

    for qid_raw, info in dados.items():
        if str(qid_raw).startswith("COM_"):
            continue

        qid = str(qid_raw).replace("Q_", "").strip()
        pts_max = PONTUACOES_MAX_IPLAN.get(qid, 0.0)

        if pts_max <= 0:
            continue

        if isinstance(info, dict):
            pts_obt = converter_para_float(info.get("pontos", 0.0))
            resp_val = str(info.get("valor", ""))
            link_ev = str(info.get("link", ""))
        else:
            pts_obt = converter_para_float(info)
            resp_val = str(info)
            link_ev = ""

        eficiencia = (pts_obt / pts_max) * 100.0
        item = {
            "qid": qid,
            "pts": pts_obt,
            "max": pts_max,
            "efic": eficiencia,
            "resp": resp_val,
            "link": link_ev,
        }

        if eficiencia >= 70.0:
            pontos_fortes.append(item)
        else:
            pontos_fracos.append(item)

            # Teste de reincidência
            info_ant = dados_ano_anterior.get(qid_raw) or dados_ano_anterior.get(qid)
            if info_ant:
                pts_ant = converter_para_float(
                    info_ant.get("pontos") if isinstance(info_ant, dict) else info_ant
                )
                if pts_ant < pts_max and pts_obt < pts_max:
                    reincidencias.append(
                        {"qid": qid, "max": pts_max, "ant": pts_ant, "atual": pts_obt}
                    )

    # Tabela Pontos Fortes
    if pontos_fortes:
        elements.append(
            Paragraph("<b>✅ Pontos Fortes (Eficiência ≥ 70%)</b>", styles["Heading3"])
        )
        df_fortes = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for f in sorted(pontos_fortes, key=lambda x: x["efic"], reverse=True):
            lnk = f"<br/><a href='{f['link']}'>{f['link']}</a>" if f["link"] else ""
            evid = f"<b>{f['resp']}</b>{lnk}"
            df_fortes.append(
                [
                    f["qid"],
                    f"{f['pts']:.1f} / {f['max']:.1f}",
                    f"{f['efic']:.1f}%",
                    Paragraph(evid, styles["CellLink"]),
                ]
            )

        tf = Table(df_fortes, colWidths=[65, 75, 65, 275])
        tf.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27AE60")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(tf)
        elements.append(Spacer(1, 10))

    # Tabela Pontos Fracos
    if pontos_fracos:
        elements.append(
            Paragraph("<b>⚠️ Pontos Fracos (Eficiência < 70%)</b>", styles["Heading3"])
        )
        df_fracos = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for fr in sorted(pontos_fracos, key=lambda x: x["efic"]):
            lnk = f"<br/><a href='{fr['link']}'>{fr['link']}</a>" if fr["link"] else ""
            evid = f"<b>{fr['resp']}</b>{lnk}"
            df_fracos.append(
                [
                    fr["qid"],
                    f"{fr['pts']:.1f} / {fr['max']:.1f}",
                    f"{fr['efic']:.1f}%",
                    Paragraph(evid, styles["CellLink"]),
                ]
            )

        tfr = Table(df_fracos, colWidths=[65, 75, 65, 275])
        tfr.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E67E22")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(tfr)

    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 3. PENALIDADES E IMPACTOS NEGATIVOS
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph("<b>3. ANÁLISE DE IMPACTO E PENALIDADES</b>", styles["Heading2"])
    )
    elements.append(Spacer(1, 6))

    data_penal = [["Quesito", "Penalidade Máxima", "Aplicada?", "Valor Aplicado"]]
    tem_penal = False

    for qid_p, p_max in PENALIDADES_MAX.items():
        info_p = dados.get(qid_p) or dados.get(f"Q_{qid_p}")
        val_p = 0.0
        aplicada = "Não"

        if info_p:
            pts_p = converter_para_float(
                info_p.get("pontos") if isinstance(info_p, dict) else info_p
            )
            if pts_p < 0:
                val_p = pts_p
                aplicada = "Sim"
                tem_penal = True

        data_penal.append([qid_p, f"{p_max:.1f} pts", aplicada, f"{val_p:.1f} pts"])

    t_penal = Table(data_penal, colWidths=[100, 120, 110, 150])
    t_penal.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#C0392B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    elements.append(t_penal)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 4. DIAGNÓSTICO DE REINCIDÊNCIAS
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph("<b>4. DIAGNÓSTICO DE REINCIDÊNCIAS</b>", styles["Heading2"])
    )
    elements.append(Spacer(1, 6))

    if reincidencias:
        data_reinc = [
            [
                "Quesito",
                "Teto Máximo",
                f"Pontos {ano_ant}",
                f"Pontos {ano_normalizado}",
                "Situação",
            ]
        ]
        for r in reincidencias:
            data_reinc.append(
                [
                    r["qid"],
                    f"{r['max']:.1f}",
                    f"{r['ant']:.1f}",
                    f"{r['atual']:.1f}",
                    "Reincidente",
                ]
            )
        tr = Table(data_reinc, colWidths=[80, 80, 95, 95, 130])
        tr.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D35400")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ]
            )
        )
        elements.append(tr)
    else:
        elements.append(
            Paragraph(
                "<i>Nenhuma reincidência de pontuação insatisfatória foi detectada entre os exercícios analisados.</i>",
                styles["TdStyle"],
            )
        )

    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU) - I-AMB
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph(
            "<b>5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)</b>", styles["h2"]
        )
    )
    elements.append(Spacer(1, 6))

    def calcular_percentual_checklist(
        resposta_bruta, total_itens, ignorar_filtros=False
    ):
        if not resposta_bruta:
            return 0.0

        if str(resposta_bruta).startswith("["):
            try:
                itens_lista = ast.literal_eval(str(resposta_bruta))
                if isinstance(itens_lista, list):
                    if ignorar_filtros:
                        itens_validos = [str(i).strip() for i in itens_lista if i]
                    else:
                        itens_validos = [
                            str(i).strip().lower()
                            for i in itens_lista
                            if i
                            and "outros" not in str(i).lower()
                            and "não" not in str(i).lower()
                        ]
                    return (
                        min((len(itens_validos) / total_itens) * 100.0, 100.0)
                        if total_itens > 0
                        else 0.0
                    )
            except Exception:
                pass

        itens = [i.strip() for i in str(resposta_bruta).split(",") if i.strip()]
        if not ignorar_filtros:
            itens = [
                i for i in itens if "outros" not in i.lower() and "não" not in i.lower()
            ]
        return (
            min((len(itens) / total_itens) * 100.0, 100.0) if total_itens > 0 else 0.0
        )

    def obter_regra_ods_ifiscal(qid, resp):
        """Mapeia os quesitos do I-Fiscal para as metas da Agenda 2030 (ODS) e seu status."""
        resp_l = str(resp).strip().lower()
        metas = "-"
        status = "Não Atendido"

        if qid in ["1.0", "1.1"]:
            metas = "12.2, 15.2, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "1.1.2":
            metas = "12.8"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "2.0":
            metas = "4.7, 12.8, 15.1"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "3.0":
            metas = "12.2, 16.6, 17.14"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "4.0":
            metas = "12.4"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "5.0":
            metas = "5.0"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "6.0":
            metas = "6.4, 6.b, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "6.2":
            metas = "6.4, 6.5, 6.b, 16.6"
            pct = calcular_percentual_checklist(resp, 3)
            status = f"{pct:.1f}% Atendido"
        elif qid in ["7.0", "7.3"]:
            metas = "6.0, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["7.4", "7.5"]:
            metas = "6.2, 6.3"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "7.7.1":
            metas = "6.0, 16.6"
            pct = calcular_percentual_checklist(resp, 3)
            status = f"{pct:.1f}% Atendido"
        elif qid == "7.8":
            metas = "6.0, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "7.8.1":
            metas = "6.2, 6.3"
            status = (
                "Atendido"
                if "todas as metas foram cumpridas dentro do prazo" in resp_l
                else "Não Atendido"
            )
        elif qid == "7.9":
            metas = "6.2, 6.3"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["8.0", "8.3", "8.4", "9.0"]:
            metas = "11.6, 12.5"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "8.3.1":
            metas = "11.6, 12.5, 12.4"
            pct = calcular_percentual_checklist(resp, 3)
            status = f"{pct:.1f}% Atendido"
        elif qid == "8.4.1":
            metas = "11.6, 12.5, 12.4"
            pct = calcular_percentual_checklist(resp, 4)
            status = f"{pct:.1f}% Atendido"
        elif qid in ["10.0", "10.1"]:
            metas = "11.6, 12.5, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "10.2":
            metas = "11.6, 12.5, 16.6"
            status = (
                "Atendido"
                if "todos os bairros do município são atendidos" in resp_l
                else "Não Atendido"
            )
        elif qid == "10.3":
            metas = "11.6, 12.5, 12.4, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "11.0":
            metas = "11.6, 12.4, 12.5, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "12.0":
            metas = "11.6, 12.5, 12.4"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "13.0":
            metas = "11.6, 12.4"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "14.0":
            metas = "11.6, 12.4"
            status = "Atendido" if "não" in resp_l else "Não Atendido"
        elif qid == "15.0":
            metas = "12.0, 16.6"
            status = "Atendido" if "sim" in resp_l else "Não Atendido"

        return metas, status

    # Lista de Quesitos do I-Fiscal mapeados na regra ODS
    QUESITOS_IFISCAL_ODS = [
        "1.0",
        "1.1",
        "1.1.2",
        "2.0",
        "3.0",
        "4.0",
        "5.0",
        "6.0",
        "6.2",
        "7.0",
        "7.3",
        "7.4",
        "7.5",
        "7.7.1",
        "7.8",
        "7.8.1",
        "7.9",
        "8.0",
        "8.3",
        "8.3.1",
        "8.4",
        "8.4.1",
        "9.0",
        "10.0",
        "10.1",
        "10.2",
        "10.3",
        "11.0",
        "12.0",
        "13.0",
        "14.0",
        "15.0",
    ]

    analise_ods = []
    dados_reference = dados if isinstance(dados, dict) else {}

    for qid in QUESITOS_IFISCAL_ODS:
        info = (
            dados_reference.get(qid, {})
            if isinstance(dados_reference, dict)
            else {"valor": "Não Respondido"}
        )
        if not isinstance(info, dict):
            info = {"valor": str(info)}

        resp = str(info.get("valor", "")).strip()
        resp_l = resp.lower()

        if not resp or resp_l == "não respondido" or resp == "[]":
            continue

        # Obtém metas e status calculados dinamicamente
        metas, status = obter_regra_ods_iamb(qid, resp)

        exibicao_resp = resp
        if exibicao_resp.startswith("["):
            exibicao_resp = (
                exibicao_resp.replace("[", "")
                .replace("]", "")
                .replace("'", "")
                .replace('"', "")
            )

        analise_ods.append(
            {
                "qid": qid,
                "status": status,
                "metas": metas,
                "resp": exibicao_resp[:45] + "..."
                if len(exibicao_resp) > 45
                else exibicao_resp,
            }
        )

    if analise_ods:
        data_ods = [
            [
                "Quesito",
                "Resposta Informada",
                "Vínculo Metas ODS",
                "Status de Cumprimento",
            ]
        ]
        style_td_ods = ParagraphStyle(
            "TdOds",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            alignment=1,
        )

        def chave_ordenacao_ods(item):
            partes = []
            for p in item["qid"].split("."):
                if p.isdigit():
                    partes.append(int(p))
                else:
                    partes.append(sum(ord(char) for char in p))
            return partes

        for item in sorted(analise_ods, key=chave_ordenacao_ods):
            st_txt = item["status"]

            if "Não Atendido" in st_txt:
                st_p = Paragraph(
                    f"<font color='#dc3545'><b>{st_txt}</b></font>", style_td_ods
                )
            elif "Atendido" in st_txt and "%" not in st_txt:
                st_p = Paragraph(
                    f"<font color='#28a745'><b>{st_txt}</b></font>", style_td_ods
                )
            else:
                st_p = Paragraph(
                    f"<font color='#007bff'><b>{st_txt}</b></font>", style_td_ods
                )

            data_ods.append(
                [
                    item["qid"],
                    Paragraph(item["resp"], styles["Normal"]),
                    item["metas"],
                    st_p,
                ]
            )

        tabela_ods = Table(data_ods, colWidths=[60, 200, 115, 110])
        tabela_ods.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#0f9d58")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.whitesmoke),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (2, 0), (3, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#0f9d58")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.append(tabela_ods)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 6. SÉRIE HISTÓRICA DO I-FISCAL
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph("<b>6. SÉRIE HISTÓRICA DO I-PLAN</b>", styles["Heading2"])
    )
    elements.append(Spacer(1, 6))

    data_hist = [["Exercício / Ano", "Pontuação Acumulada", "Faixa / Conceito"]]
    anos_ordenados = sorted(todos_dados.keys())

    anos_labels = []
    pontos_valores = []

    for a in anos_ordenados:
        sub_d = todos_dados[a]
        tot_a = 0.0
        if isinstance(sub_d, dict):
            for k_a, v_a in sub_d.items():
                if str(k_a).startswith("COM_"):
                    continue
                tot_a += converter_para_float(
                    v_a.get("pontos") if isinstance(v_a, dict) else v_a
                )

        f_a = converter_pontos_em_faixa_iegm(tot_a)
        data_hist.append([str(a), f"{tot_a:.1f} pts", f_a])

        # Coleta dados para o gráfico
        anos_labels.append(str(a))
        pontos_valores.append(tot_a)

    # 1. Tabela
    t_hist = Table(data_hist, colWidths=[120, 180, 180])
    t_hist.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    elements.append(t_hist)
    elements.append(Spacer(1, 15))

    # 2. Gráfico de Barras Vertical
    if pontos_valores:
        d = Drawing(480, 180)

        bc = VerticalBarChart()
        bc.x = 40
        bc.y = 25
        bc.height = 135
        bc.width = 410
        bc.data = [pontos_valores]

        # Eixo X (Anos)
        bc.categoryAxis.categoryNames = anos_labels
        bc.categoryAxis.labels.fontSize = 9
        bc.categoryAxis.labels.dy = -10

        # Eixo Y (Pontuação)
        max_v = max(pontos_valores) if pontos_valores else 100
        bc.valueAxis.valueMin = 0
        bc.valueAxis.valueMax = max(max_v * 1.15, 100)
        bc.valueAxis.valueStep = 20
        bc.valueAxis.labels.fontSize = 8

        # Estilo das Barras
        bc.bars[0].fillColor = colors.HexColor("#2980B9")
        bc.bars[0].strokeColor = colors.HexColor("#1B4F72")

        d.add(bc)
        elements.append(d)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 7. QUESITOS SEM PONTUAÇÃO DIRETA (I-FISCAL - CONFORMIDADE OPERACIONAL)
    # -------------------------------------------------------------------------
    elements.append(
        Paragraph(
            "<b>7. QUESITOS SEM PONTUAÇÃO DIRETA (I-FISCAL - CONFORMIDADE OPERACIONAL)</b>",
            styles["Heading2"],
        )
    )
    elements.append(Spacer(1, 6))

    data_sp = [["Quesito Target", "Resposta Apresentada", "Situação de Conformidade"]]

    for qsp in lista_alvo_iplan:
        info_sp = dados.get(qsp) or dados.get(f"Q_{qsp}")
        resp_sp = "-"
        status_sp = "Não Informado"

        if info_sp:
            resp_sp = (
                info_sp.get("valor", "") if isinstance(info_sp, dict) else str(info_sp)
            )
            status_sp = (
                "Em Conformidade"
                if "sim" in str(resp_sp).lower()
                else "Fora de Conformidade"
            )

        data_sp.append([qsp, Paragraph(str(resp_sp), styles["TdStyle"]), status_sp])

    t_sp = Table(data_sp, colWidths=[100, 250, 130])
    t_sp.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ]
        )
    )
    elements.append(t_sp)

    # Construção do Documento PDF
    doc.build(elements)
    return buffer.getvalue()


# -----------------------------------------------------------------------------
# 4. CARD E EVENTOS DE EMISSÃO DO RELATÓRIO PDF (NICEGUI) - CORRIGIDO
# -----------------------------------------------------------------------------
def renderizar_card_relatorio_iplan(res_data=None, ano_sel=2026):
    """
    Componente NiceGUI para renderizar o Card de Emissão do PDF do I-PLAN.
    """
    res_data = res_data or {}

    with ui.card().classes(
        "w-full p-6 my-6 border border-blue-200 rounded-lg shadow-sm bg-blue-50"
    ):
        ui.label("📄 Emissão de Relatório Analítico - I-Fiscal").classes(
            "text-xl font-bold text-blue-900 mb-1"
        )
        ui.label(
            "Gere o relatório completo em formato PDF contendo análises de tendência, diagnóstico de reincidências e metas ODS da Agenda 2030."
        ).classes("text-sm text-gray-700 mb-4")

        async def baixar_pdf():
            n = ui.notify("Gerando PDF do I-Fiscal, aguarde...", type="info", timeout=0)

            try:
                await asyncio.sleep(0.1)

                dados_locais = res_data or {}
                ano_alvo = int(ano_sel)

                total_pts = float(
                    sum(
                        v.get("pontos", 0)
                        for k, v in dados_locais.items()
                        if isinstance(v, dict) and not str(k).startswith("COM_")
                    )
                )

                faixa = converter_pontos_em_faixa_iegm(total_pts)
                historico_todos_anos = get_all_years_data() or {}

                # Gera o relatório em memória (bytes)
                pdf_bytes = gerar_relatorio_pdf(
                    dados=dados_locais,
                    ano=ano_alvo,
                    total=total_pts,
                    faixa=faixa,
                    todos_dados=historico_todos_anos,
                )

                # Dispara o download direto do buffer em memória pelo NiceGUI
                ui.download(
                    pdf_bytes,
                    filename=f"relatorio_ifiscal_{ano_alvo}.pdf"
                )

                ui.notify("Relatório I-PLAN gerado e baixado com sucesso!", type="positive")

            except Exception as e:
                print(f"ERRO CRÍTICO AO GERAR PDF I-FISCAL: {e}")
                logging.exception("Erro no PDF I-Fiscal:")
                ui.notify(
                    f"Erro ao gerar o PDF: {e}", type="negative", close_button=True
                )

            finally:
                if n is not None:
                    try:
                        n.dismiss()
                    except Exception:
                        pass

        ui.button(
            "📥 GERAR E ABRIR RELATÓRIO PDF (I-FISCAL)", on_click=baixar_pdf
        ).classes("bg-blue-700 text-white font-bold my-2")


# Alias de compatibilidade caso seja chamado com nome antigo
renderizar_card_relatorio_iamb = renderizar_card_relatorio_iplan


def container_formulario_ifiscal(ano=2026):
    """
    Renderiza os campos do formulário do I-Fiscal na interface NiceGUI.
    """
    with ui.column().classes("w-full p-4"):
        ui.label(f"Formulário do I-Fiscal - Exercício {ano}").classes(
            "text-2xl font-bold text-gray-800 mb-4"
        )
        ui.label("Preencha os dados abaixo para calcular o I-PLAN:").classes(
            "text-gray-600 mb-2"
        )


def container_formulario_iplan(ano=None, res_data=None, ano_sel=2026):
    """
    Ponto de entrada público do módulo I-PLAN no NiceGUI.
    """
    ano_inicial = ano if ano is not None else ano_sel
    
    with ui.container().classes("w-full max-w-5xl mx-auto p-4"):
        # 1. Renderiza o formulário I-Fiscal
        container_formulario_ifiscal(ano=ano_inicial)

        # 2. Carrega as respostas do banco de dados com segurança
        dados_relatorio = res_data
        if dados_relatorio is None:
            try:
                ano_relatorio = ano_inicial
                if hasattr(app, "storage") and hasattr(app.storage, "user"):
                    ano_relatorio = int(
                        app.storage.user.get("ano_referencia_global", ano_inicial)
                    )
                dados_relatorio = load_respostas(ano_relatorio)
            except Exception as err:
                print(f"⚠️ Aviso ao carregar respostas do DB: {err}")
                dados_relatorio = {}
                ano_relatorio = ano_inicial
        else:
            ano_relatorio = ano_inicial

        # 3. Renderiza o card do relatório PDF
        renderizar_card_relatorio_iplan(
            res_data=dados_relatorio,
            ano_sel=ano_relatorio,
        )


@ui.page("/")
def pagina_principal():
    container_formulario_iplan()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        storage_secret=os.getenv(
            "NICEGUI_STORAGE_SECRET", "chave-local-altere-em-producao"
        )
    )
