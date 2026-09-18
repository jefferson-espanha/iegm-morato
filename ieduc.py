import base64
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# BANCO DE DADOS (NEON - ESTRUTURA REAL RESPOSTAS_IEDUC)
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
        FROM respostas_ieduc
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
        print(f"❌ Erro ao carregar respostas de respostas_ieduc: {e}")
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

    # Gera um ID único simples combinando Ano e Quesito
    registro_id = f"{ano}_{qid}"

    query = """
        INSERT INTO respostas_ieduc (id, ano, quesito, resposta, pontos, detalhes, atualizado_em)
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
                print(f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela respostas_ieduc!")
    except Exception as e:
        print(f"❌ Erro ao salvar resposta na tabela respostas_ieduc: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_ieduc WHERE ano = %s;"
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

    # Tratamento inicial do valor armazenado
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
    elif tipo_input == "calculo_1_1_1":
        valor_atual = dados_q.get("valor", {"bpi": 0, "total": 0})
        if not isinstance(valor_atual, dict):
            valor_atual = {"bpi": 0, "total": 0}
    elif tipo_input == "calculo_1_1_2":
        valor_atual = dados_q.get("valor", {"cron": 0, "ncron": 0, "solic": 0, "nmanu": 0})
        if not isinstance(valor_atual, dict):
            valor_atual = {"cron": 0, "ncron": 0, "solic": 0, "nmanu": 0}
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

            elif tipo_input == "calculo_1_1_1":
                with ui.column().classes("gap-3 w-full"):
                    in_bpi = ui.number(
                        label="Nº de creches com BPI:",
                        value=state["opcao"].get("bpi", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    in_total = ui.number(
                        label="Nº total de creches no município:",
                        value=state["opcao"].get("total", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    def sync_1_1_1(e=None):
                        state["opcao"] = {
                            "bpi": float(in_bpi.value or 0),
                            "total": float(in_total.value or 0),
                        }
                        atualizar_impacto()

                    in_bpi.on("update:model-value", sync_1_1_1)
                    in_total.on("update:model-value", sync_1_1_1)

            elif tipo_input == "calculo_1_1_2":
                with ui.column().classes("gap-3 w-full"):
                    in_cron = ui.number(
                        label="Cumpriram o cronograma (CRON - Pmáx: +3):",
                        value=state["opcao"].get("cron", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    in_ncron = ui.number(
                        label="NÃO cumpriram o cronograma (NCRON - Pmáx: +1):",
                        value=state["opcao"].get("ncron", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    in_solic = ui.number(
                        label="Apenas por solicitação (SOLIC - Pmáx: 0):",
                        value=state["opcao"].get("solic", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    in_nmanu = ui.number(
                        label="NÃO realizam manutenção (NMANU - Perde 2 pts):",
                        value=state["opcao"].get("nmanu", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    def sync_1_1_2(e=None):
                        state["opcao"] = {
                            "cron": float(in_cron.value or 0),
                            "ncron": float(in_ncron.value or 0),
                            "solic": float(in_solic.value or 0),
                            "nmanu": float(in_nmanu.value or 0),
                        }
                        atualizar_impacto()

                    in_cron.on("update:model-value", sync_1_1_2)
                    in_ncron.on("update:model-value", sync_1_1_2)
                    in_solic.on("update:model-value", sync_1_1_2)
                    in_nmanu.on("update:model-value", sync_1_1_2)

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
            elif tipo_input == "calculo_1_1_1":
                if isinstance(opcao_sel, dict):
                    bpi = float(opcao_sel.get("bpi", 0) or 0)
                    total = float(opcao_sel.get("total", 0) or 0)
                    if total > 0 and bpi <= total:
                        return (bpi / total) * 2.0
                return 0.0
            elif tipo_input == "calculo_1_1_2":
                if isinstance(opcao_sel, dict):
                    cron = float(opcao_sel.get("cron", 0) or 0)
                    ncron = float(opcao_sel.get("ncron", 0) or 0)
                    solic = float(opcao_sel.get("solic", 0) or 0)
                    nmanu = float(opcao_sel.get("nmanu", 0) or 0)
                    total = cron + ncron + solic + nmanu
                    if total > 0:
                        p1 = (nmanu / total) * (-2.0)
                        p2 = (ncron / total) * 1.0
                        p3 = (cron / total) * 3.0
                        return p1 + p2 + p3
                return 0.0
            elif isinstance(opcao_sel, list):
                return sum(float(opcoes.get(opt, 0.0)) for opt in opcao_sel)
            
            val = opcoes.get(opcao_sel, 0.0)
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        pts_atuais = calcular_pontos(state["opcao"])
        label_impacto = ui.label(
            f"📊 Impacto de Pontuação no Quesito {qid}: {float(pts_atuais):.2f} pontos"
        ).classes("text-sm font-bold text-green-600 my-4")

        def atualizar_impacto(e=None):
            novos_pts = calcular_pontos(state["opcao"])
            label_impacto.set_text(
                f"📊 Impacto de Pontuação no Quesito {qid}: {float(novos_pts):.2f} pontos"
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
        ui.label("🛠️ Painel de Controle (iEduc)").classes(
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
# MÓDULO PRINCIPAL DE REQUISITOS (IEDUC)
# =============================================================================
def container_formulario_ieduc(ano=None):
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
                        ui.label(f"📋 Módulo i-Educ — Ano {ano_sel}").classes(
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
                        titulo="Oferta de Creche",
                        pergunta="A Prefeitura municipal oferece Creche?",
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
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.1",
                        titulo="Infraestrutura de Creche",
                        pergunta="Algum estabelecimento que oferece Creche possui brinquedos no Pátio Infantil?",
                        tipo_input="radio",
                        opcoes=opcoes_1_1,
                        placeholder_link="Insira o link ou documento de comprovação...",
                        on_save_callback=render_conteudo.refresh,
                    )
import re
                    from nicegui import ui

                    # =============================================================================
                    # QUESITO 1.1.1 (Brinquedos no Pátio Infantil - BPI)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.1.1 • Brinquedos no Pátio Infantil (BPI)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de creches com brinquedos no pátio e o total de creches no município:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: (Creches com brinquedos / Total de creches) × 2.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d111 = res_data.get("1.1.1") or {}
                        raw_link_111 = str(d111.get("link") or "")

                        bpi_com_i, total_creches_i = 0, 0
                        evidencia_111 = raw_link_111

                        if "|LINK:" in raw_link_111:
                            partes_111, evidencia_111 = raw_link_111.split("|LINK:", 1)
                            match_bpi = re.search(r"BPI:(\d+)", partes_111)
                            match_tot = re.search(r"TOT:(\d+)", partes_111)
                            bpi_com_i = int(match_bpi.group(1)) if match_bpi else 0
                            total_creches_i = int(match_tot.group(1)) if match_tot else 0

                        state_111 = {
                            "bpi": bpi_com_i,
                            "total": total_creches_i,
                            "link": evidencia_111,
                        }

                        def calc_pts_111():
                            tot = int(state_111["total"] or 0)
                            bpi = int(state_111["bpi"] or 0)
                            if tot <= 0 or bpi <= 0:
                                return 0.0
                            prop = min(bpi / tot, 1.0)
                            return prop * 2.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_bpi = (
                                    ui.number(
                                        "Nº de creches COM brinquedos no pátio:",
                                        value=bpi_com_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_111, "bpi")
                                )

                                inp_tot_111 = (
                                    ui.number(
                                        "TOTAL de creches no município:",
                                        value=total_creches_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_111, "total")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_111,
                                placeholder="Insira a lista de creches, relatórios de vistoria ou fotos...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_111, "link"
                            )

                        lbl_pts_111 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.1.1: {calc_pts_111():.2f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_111():
                            lbl_pts_111.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.1.1: {calc_pts_111():.2f} / 2.0 pontos"
                            )

                        inp_bpi.on("update:model-value", att_pts_111)
                        inp_tot_111.on("update:model-value", att_pts_111)

                        def salvar_111():
                            b_val = int(state_111["bpi"] or 0)
                            t_val = int(state_111["total"] or 0)
                            pts_finais = calc_pts_111()
                            composite = f"BPI:{b_val},TOT:{t_val}|LINK:{state_111['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.1.1",
                                valor=f"{b_val}/{t_val}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d111.get("comentarios", []),
                                status=d111.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.1.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.1.1", on_click=salvar_111).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.1.1", res_data, render_conteudo.refresh)


                    # =============================================================================
                    # QUESITO 1.1.2 (Manutenção Preventiva / Troca de Brinquedos)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.1.2 • Manutenção Preventiva dos Brinquedos do Pátio").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a distribuição das creches quanto à realização e cumprimento do cronograma de manutenção:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: P = P1 + P2 + P3 (CRON = 3.0 pts | NCRON = 1.0 pt | SOLIC = 0.0 pts | NMANU = -2.0 pts)"
                        ).classes("text-xs text-gray-400 mb-6")

                        d112 = res_data.get("1.1.2") or {}
                        raw_link_112 = str(d112.get("link") or "")

                        cron_i, ncron_i, solic_i, nmanu_i = 0, 0, 0, 0
                        evidencia_112 = raw_link_112

                        if "|LINK:" in raw_link_112:
                            partes_112, evidencia_112 = raw_link_112.split("|LINK:", 1)
                            m_cron = re.search(r"CRON:(\d+)", partes_112)
                            m_ncron = re.search(r"NCRON:(\d+)", partes_112)
                            m_solic = re.search(r"SOLIC:(\d+)", partes_112)
                            m_nmanu = re.search(r"NMANU:(\d+)", partes_112)

                            cron_i = int(m_cron.group(1)) if m_cron else 0
                            ncron_i = int(m_ncron.group(1)) if m_ncron else 0
                            solic_i = int(m_solic.group(1)) if m_solic else 0
                            nmanu_i = int(m_nmanu.group(1)) if m_nmanu else 0

                        state_112 = {
                            "cron": cron_i,
                            "ncron": ncron_i,
                            "solic": solic_i,
                            "nmanu": nmanu_i,
                            "link": evidencia_112,
                        }

                        def calc_pts_112():
                            c = int(state_112["cron"] or 0)
                            nc = int(state_112["ncron"] or 0)
                            s = int(state_112["solic"] or 0)
                            nm = int(state_112["nmanu"] or 0)

                            total_resp = c + nc + s + nm
                            if total_resp <= 0:
                                return 0.0

                            p1 = (nm / total_resp) * (-2.0)
                            p2 = (nc / total_resp) * 1.0
                            p3 = (c / total_resp) * 3.0

                            return p1 + p2 + p3

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_cron = (
                                    ui.number(
                                        "Possuem e CUMPRIRAM o cronograma (CRON):",
                                        value=cron_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_112, "cron")
                                )

                                inp_ncron = (
                                    ui.number(
                                        "Possuem e NÃO cumpriram o cronograma (NCRON):",
                                        value=ncron_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_112, "ncron")
                                )

                                inp_solic = (
                                    ui.number(
                                        "Manutenção SOMENTE por solicitação (SOLIC):",
                                        value=solic_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_112, "solic")
                                )

                                inp_nmanu = (
                                    ui.number(
                                        "NÃO realizam manutenção/troca (NMANU):",
                                        value=nmanu_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined")
                                    .bind_value(state_112, "nmanu")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_112,
                                placeholder="Insira os relatórios de manutenção, cronogramas ou ordem de serviço...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_112, "link"
                            )

                        lbl_pts_112 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.1.2: {calc_pts_112():.2f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_112():
                            pts = calc_pts_112()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_112.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_112.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.1.2: {pts:.2f} pontos"
                            )

                        inp_cron.on("update:model-value", att_pts_112)
                        inp_ncron.on("update:model-value", att_pts_112)
                        inp_solic.on("update:model-value", att_pts_112)
                        inp_nmanu.on("update:model-value", att_pts_112)

                        def salvar_112():
                            c = int(state_112["cron"] or 0)
                            nc = int(state_112["ncron"] or 0)
                            s = int(state_112["solic"] or 0)
                            nm = int(state_112["nmanu"] or 0)

                            pts_finais = calc_pts_112()
                            composite = (
                                f"CRON:{c},NCRON:{nc},SOLIC:{s},NMANU:{nm}|LINK:{state_112['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="1.1.2",
                                valor=f"CRON:{c}/NCRON:{nc}/SOLIC:{s}/NMANU:{nm}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d112.get("comentarios", []),
                                status=d112.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.1.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.1.2", on_click=salvar_112).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.1.2", res_data, render_conteudo.refresh)
                    # ==========================================
                    # QUESITO 1.2
                    # ==========================================
                    opcoes_1_2 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.2",
                        titulo="Materiais Pedagógicos",
                        pergunta="A Prefeitura disponibiliza brinquedos/materiais pedagógicos para as crianças em todos os estabelecimentos de Creche do município?",
                        tipo_input="radio",
                        opcoes=opcoes_1_2,
                        placeholder_link="Insira o link ou documento de distribuição...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.2.1
                    # ==========================================
                    opcoes_1_2_1 = {
                        "Selecione...": 0.0,
                        "Sim": 0.0,
                        "Não": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.2.1",
                        titulo="Higienização de Materiais",
                        pergunta="Realiza higienização dos brinquedos/materiais pedagógicos?",
                        tipo_input="radio",
                        opcoes=opcoes_1_2_1,
                        placeholder_link="Insira o link ou protocolo de higienização...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.2.1.1
                    # ==========================================
                    opcoes_1_2_1_1 = {
                        "Selecione...": 0.0,
                        "Diária – 05": 5.0,
                        "A cada 2 dias – 04": 4.0,
                        "A cada 3 dias – 03": 3.0,
                        "Semanal – 02": 2.0,
                        "Mensal – 01": 1.0,
                        "> 30 dias – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.2.1.1",
                        titulo="Frequência de Higienização",
                        pergunta="Qual a frequência de higienização aplicada na maior parte dos estabelecimentos que oferecem creche?",
                        tipo_input="radio",
                        opcoes=opcoes_1_2_1_1,
                        placeholder_link="Insira o link do registro/relatório de higienização...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.2.2
                    # ==========================================
                    opcoes_1_2_2 = {
                        "Selecione...": 0.0,
                        "Sim – 05": 5.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.2.2",
                        titulo="Planejamento de Aquisição",
                        pergunta="Possui cronograma para compra de brinquedos/materiais pedagógicos para cada estabelecimento de ensino?",
                        tipo_input="radio",
                        opcoes=opcoes_1_2_2,
                        placeholder_link="Insira o link ou documento do planejamento de compra...",
                        on_save_callback=render_conteudo.refresh,
                    )

    render_conteudo()
    return main_container

