import base64
from datetime import datetime
import json
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from nicegui import app, ui

# =============================================================================
# BANCO DE DADOS (NEON - ESTRUTURA REAL RESPOSTAS_IEDUC)
# =============================================================================
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _obter_lista_comentarios(dados_ou_ano, qid=None):
    """Trata chamadas com 1 ou 2 argumentos para evitar TypeError."""
    if qid is not None and isinstance(dados_ou_ano, dict):
        dados_q = dados_ou_ano.get(str(qid), {})
        coms = dados_q.get("comentarios", [])
        return coms if isinstance(coms, list) else []

    if isinstance(dados_ou_ano, dict):
        coms = dados_ou_ano.get("comentarios", [])
        return coms if isinstance(coms, list) else []
    elif isinstance(dados_ou_ano, list):
        return dados_ou_ano

    return []


def load_respostas(ano):
    query = """
        SELECT id, ano, quesito, resposta, pontos, link, comentario
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
                    q_id = str(row["quesito"])
                    val_bruto = row["resposta"] or ""

                    val_final = val_bruto
                    if isinstance(val_bruto, str) and (
                        (val_bruto.startswith("[") and val_bruto.endswith("]"))
                        or (
                            val_bruto.startswith("{")
                            and val_bruto.endswith("}")
                        )
                    ):
                        try:
                            val_final = json.loads(val_bruto)
                        except Exception:
                            val_final = val_bruto

                    link_val = row.get("link") or ""
                    if link_val == "EMPTY_STRING":
                        link_val = ""

                    coment_raw = row.get("comentario") or ""
                    comentarios_val = []
                    if coment_raw and coment_raw != "EMPTY_STRING":
                        try:
                            comentarios_val = json.loads(coment_raw)
                        except Exception:
                            comentarios_val = []

                    respostas[q_id] = {
                        "valor": val_final,
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": link_val,
                        "comentarios": comentarios_val,
                        "status": "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas de respostas_ieduc: {e}")
    return respostas


def save_resposta(
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = _obter_lista_comentarios(dados_atuais)

    link_final = link.strip() if link else "EMPTY_STRING"

    if isinstance(valor, (list, dict)):
        resposta_str = json.dumps(valor, ensure_ascii=False)
    else:
        resposta_str = str(valor) if valor is not None else ""

    comentario_str = (
        json.dumps(comentarios, ensure_ascii=False)
        if comentarios
        else "EMPTY_STRING"
    )

    query = """
        INSERT INTO respostas_ieduc (ano, quesito, resposta, pontos, link, comentario)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (ano, quesito) 
        DO UPDATE SET
            resposta = EXCLUDED.resposta,
            pontos = EXCLUDED.pontos,
            link = EXCLUDED.link,
            comentario = EXCLUDED.comentario;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        int(ano),
                        str(qid),
                        resposta_str,
                        float(pontos),
                        link_final,
                        comentario_str,
                    ),
                )
                conn.commit()
                print(
                    f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela respostas_ieduc!"
                )
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
        print(f"❌ Erro ao zerar questionário no DB: {e}")
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

                    # =============================================================================
                    # QUESITO 1.2.3 (Data da Última Entrega de Brinquedos/Materiais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.2.3 • Última Entrega de Brinquedos/Materiais Pedagógicos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega de brinquedos e/ou materiais pedagógicos:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório / comprobatório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d123 = res_data.get("1.2.3") or {}
                        raw_link_123 = str(d123.get("link") or "")
                        data_val_i = str(d123.get("valor") or "")
                        evidencia_123 = raw_link_123

                        state_123 = {
                            "data": data_val_i,
                            "link": evidencia_123,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_data_123 = (
                                    ui.input(
                                        label="Data da Entrega (DD/MM/AAAA):",
                                        value=data_val_i,
                                        placeholder="Ex: 15/03/2025",
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_123, "data")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_123,
                                placeholder="Insira o comprovante de entrega, nota fiscal ou termo de recebimento...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_123, "link"
                            )

                        def salvar_123():
                            dt_val = state_123["data"].strip()
                            save_resposta(
                                ano=ano_sel,
                                qid="1.2.3",
                                valor=dt_val,
                                pontos=0.0,
                                link=state_123["link"],
                                comentarios=d123.get("comentarios", []),
                                status=d123.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.2.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.2.3", on_click=salvar_123).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.2.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.3 (Espaço por Aluno em Sala de Aula - Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.3 • Espaço por Aluno em Sala de Aula (Creche)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de turmas de Creche em cada faixa de área por aluno (m²):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 10 × (1.0×P1 + 0.5×P2 + 0.25×P3 + 0×P4) | Pmáx = 10 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d13 = res_data.get("1.3") or {}
                        raw_link_13 = str(d13.get("link") or "")

                        f1_i, f2_i, f3_i, f4_i = 0, 0, 0, 0
                        evidencia_13 = raw_link_13

                        if "|LINK:" in raw_link_13:
                            partes_13, evidencia_13 = raw_link_13.split("|LINK:", 1)
                            m_f1 = re.search(r"F1:(\d+)", partes_13)
                            m_f2 = re.search(r"F2:(\d+)", partes_13)
                            m_f3 = re.search(r"F3:(\d+)", partes_13)
                            m_f4 = re.search(r"F4:(\d+)", partes_13)
                            f1_i = int(m_f1.group(1)) if m_f1 else 0
                            f2_i = int(m_f2.group(1)) if m_f2 else 0
                            f3_i = int(m_f3.group(1)) if m_f3 else 0
                            f4_i = int(m_f4.group(1)) if m_f4 else 0

                        state_13 = {
                            "f1": f1_i,
                            "f2": f2_i,
                            "f3": f3_i,
                            "f4": f4_i,
                            "link": evidencia_13,
                        }

                        def calc_pts_13():
                            c1 = int(state_13["f1"] or 0)
                            c2 = int(state_13["f2"] or 0)
                            c3 = int(state_13["f3"] or 0)
                            c4 = int(state_13["f4"] or 0)
                            tot_turmas = c1 + c2 + c3 + c4
                            if tot_turmas <= 0:
                                return 0.0

                            p1 = c1 / tot_turmas
                            p2 = c2 / tot_turmas
                            p3 = c3 / tot_turmas
                            p4 = c4 / tot_turmas

                            n1 = 1.0 * p1
                            n2 = 0.5 * p2
                            n3 = 0.25 * p3
                            n4 = 0.0 * p4

                            return 10.0 * (n1 + n2 + n3 + n4)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_f1 = (
                                    ui.number(
                                        "Turmas com área ≥ 2,30 m²/aluno:",
                                        value=f1_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_13, "f1")
                                )

                                inp_f2 = (
                                    ui.number(
                                        "Turmas com área ≥ 2,00 m² e < 2,30 m²/aluno:",
                                        value=f2_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_13, "f2")
                                )

                                inp_f3 = (
                                    ui.number(
                                        "Turmas com área ≥ 1,50 m² e < 2,00 m²/aluno:",
                                        value=f3_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_13, "f3")
                                )

                                inp_f4 = (
                                    ui.number(
                                        "Turmas com área < 1,50 m²/aluno:",
                                        value=f4_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_13, "f4")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_13,
                                placeholder="Insira o laudo metrológico, levantamento das salas ou relatório de vistoria...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_13, "link"
                            )

                        lbl_pts_13 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.3: {calc_pts_13():.2f} / 10.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_13():
                            lbl_pts_13.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.3: {calc_pts_13():.2f} / 10.0 pontos"
                            )

                        inp_f1.on("update:model-value", att_pts_13)
                        inp_f2.on("update:model-value", att_pts_13)
                        inp_f3.on("update:model-value", att_pts_13)
                        inp_f4.on("update:model-value", att_pts_13)

                        def salvar_13():
                            c1 = int(state_13["f1"] or 0)
                            c2 = int(state_13["f2"] or 0)
                            c3 = int(state_13["f3"] or 0)
                            c4 = int(state_13["f4"] or 0)
                            pts_finais = calc_pts_13()
                            composite = f"F1:{c1},F2:{c2},F3:{c3},F4:{c4}|LINK:{state_13['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.3",
                                valor=f"F1:{c1}/F2:{c2}/F3:{c3}/F4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d13.get("comentarios", []),
                                status=d13.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.3", on_click=salvar_13).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.4 (Formação e Pós-Graduação dos Professores de Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.4 • Titulação e Formação de Professores de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de professores de Creche e a quantidade com Licenciatura e Pós-Graduação:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = N1 (Graduação - máx 11 pts) + N2 (Pós-Graduação - máx 7 pts) | Pmáx = 18 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d14 = res_data.get("1.4") or {}
                        raw_link_14 = str(d14.get("link") or "")

                        tot_prof_i, grad_i, pgrad_i = 0, 0, 0
                        evidencia_14 = raw_link_14

                        if "|LINK:" in raw_link_14:
                            partes_14, evidencia_14 = raw_link_14.split("|LINK:", 1)
                            m_tot = re.search(r"TOT:(\d+)", partes_14)
                            m_grad = re.search(r"GRAD:(\d+)", partes_14)
                            m_pgrad = re.search(r"PGRAD:(\d+)", partes_14)
                            tot_prof_i = int(m_tot.group(1)) if m_tot else 0
                            grad_i = int(m_grad.group(1)) if m_grad else 0
                            pgrad_i = int(m_pgrad.group(1)) if m_pgrad else 0

                        state_14 = {
                            "tot": tot_prof_i,
                            "grad": grad_i,
                            "pgrad": pgrad_i,
                            "link": evidencia_14,
                        }

                        def calc_pts_14():
                            tot = int(state_14["tot"] or 0)
                            grad = int(state_14["grad"] or 0)
                            pgrad = int(state_14["pgrad"] or 0)
                            if tot <= 0:
                                return 0.0

                            g_pct = grad / tot
                            if g_pct >= 1.0:
                                n1 = 11.0
                            elif g_pct >= 0.90:
                                n1 = 7.0
                            elif g_pct >= 0.80:
                                n1 = 3.0
                            elif g_pct >= 0.70:
                                n1 = 1.0
                            else:
                                n1 = 0.0

                            p_pct = pgrad / tot
                            if p_pct >= 0.50:
                                n2 = 7.0
                            elif p_pct >= 0.40:
                                n2 = 5.0
                            elif p_pct >= 0.20:
                                n2 = 3.0
                            else:
                                n2 = 0.0

                            return n1 + n2

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_tot_14 = (
                                    ui.number(
                                        "TOTAL de professores da etapa (Efetivos + Temporários):",
                                        value=tot_prof_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_14, "tot")
                                )

                                inp_grad = (
                                    ui.number(
                                        "Professores com Nível Superior / Licenciatura (GRAD):",
                                        value=grad_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_14, "grad")
                                )

                                inp_pgrad = (
                                    ui.number(
                                        "Professores com Pós-Graduação (PGRAD):",
                                        value=pgrad_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_14, "pgrad")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_14,
                                placeholder="Insira o relatório do Censo Escolar 2025 ou relação de professores e diplomas...",
                            ).classes("w-full").props("outlined rows=8").bind_value(
                                state_14, "link"
                            )

                        lbl_pts_14 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.4: {calc_pts_14():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_14():
                            lbl_pts_14.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.4: {calc_pts_14():.1f} / 18.0 pontos"
                            )

                        inp_tot_14.on("update:model-value", att_pts_14)
                        inp_grad.on("update:model-value", att_pts_14)
                        inp_pgrad.on("update:model-value", att_pts_14)

                        def salvar_14():
                            tot = int(state_14["tot"] or 0)
                            g = int(state_14["grad"] or 0)
                            pg = int(state_14["pgrad"] or 0)
                            pts_finais = calc_pts_14()
                            composite = (
                                f"TOT:{tot},GRAD:{g},PGRAD:{pg}|LINK:{state_14['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="1.4",
                                valor=f"GRAD:{g}/PGRAD:{pg}/TOT:{tot}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d14.get("comentarios", []),
                                status=d14.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.4", on_click=salvar_14).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.4", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 1.5 (Piso Salarial Mensal dos Professores de Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.5 • Piso Salarial Mensal dos Professores de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o piso salarial mensal dos professores de Creche (base para 40h semanais):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se piso < Salário Mínimo Nacional → Perde 20.0 pts (-20.0) | Se piso ≥ Salário Mínimo → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d15 = res_data.get("1.5") or {}
                        raw_link_15 = str(d15.get("link") or "")

                        piso_i = 0.0
                        sal_min_i = 1412.0  # Valor base de referência
                        evidencia_15 = raw_link_15

                        if "|LINK:" in raw_link_15:
                            partes_15, evidencia_15 = raw_link_15.split("|LINK:", 1)
                            m_piso = re.search(r"PISO:([\d\.]+)", partes_15)
                            m_smin = re.search(r"SMIN:([\d\.]+)", partes_15)
                            piso_i = float(m_piso.group(1)) if m_piso else 0.0
                            sal_min_i = float(m_smin.group(1)) if m_smin else 1412.0
                        elif d15.get("valor"):
                            try:
                                piso_i = float(d15.get("valor"))
                            except (ValueError, TypeError):
                                piso_i = 0.0

                        state_15 = {
                            "piso": piso_i,
                            "smin": sal_min_i,
                            "link": evidencia_15,
                        }

                        def calc_pts_15():
                            piso = float(state_15["piso"] or 0.0)
                            smin = float(state_15["smin"] or 0.0)
                            if piso <= 0:
                                return 0.0
                            return -20.0 if piso < smin else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_piso = (
                                    ui.number(
                                        "Piso Salarial do Professor (40h) - R$:",
                                        value=piso_i,
                                        min=0,
                                        step=0.01,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue prefix=R$")
                                    .bind_value(state_15, "piso")
                                )

                                inp_smin = (
                                    ui.number(
                                        "Salário Mínimo Nacional de Referência - R$:",
                                        value=sal_min_i,
                                        min=0,
                                        step=0.01,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue prefix=R$")
                                    .bind_value(state_15, "smin")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_15,
                                placeholder="Insira a Lei Municipal, Plano de Cargos e Salários ou Holerite modelo...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_15, "link"
                            )

                        lbl_pts_15 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.5: {calc_pts_15():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_15():
                            pts = calc_pts_15()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_15.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_15.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.5: {pts:.1f} pontos"
                            )

                        inp_piso.on("update:model-value", att_pts_15)
                        inp_smin.on("update:model-value", att_pts_15)

                        def salvar_15():
                            p_val = float(state_15["piso"] or 0.0)
                            sm_val = float(state_15["smin"] or 0.0)
                            pts_finais = calc_pts_15()
                            composite = f"PISO:{p_val},SMIN:{sm_val}|LINK:{state_15['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.5",
                                valor=f"R$ {p_val:.2f}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d15.get("comentarios", []),
                                status=d15.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.5", on_click=salvar_15).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.6 (Quantidade Total de Dias de Ausência dos Professores - QTA)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.6 • Dias de Ausência de Professores de Creche (QTA)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de dias de ausência dos professores de Creche por categoria (ano de 2025):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório / levantamento de absenteísmo."
                        ).classes("text-xs text-gray-400 mb-6")

                        d16 = res_data.get("1.6") or {}
                        raw_link_16 = str(d16.get("link") or "")

                        finj_i, fjus_i, lmed_i, lmat_i, abo_i, out_i = 0, 0, 0, 0, 0, 0
                        evidencia_16 = raw_link_16

                        if "|LINK:" in raw_link_16:
                            partes_16, evidencia_16 = raw_link_16.split("|LINK:", 1)
                            m_inj = re.search(r"INJ:(\d+)", partes_16)
                            m_jus = re.search(r"JUS:(\d+)", partes_16)
                            m_med = re.search(r"MED:(\d+)", partes_16)
                            m_mat = re.search(r"MAT:(\d+)", partes_16)
                            m_abo = re.search(r"ABO:(\d+)", partes_16)
                            m_out = re.search(r"OUT:(\d+)", partes_16)

                            finj_i = int(m_inj.group(1)) if m_inj else 0
                            fjus_i = int(m_jus.group(1)) if m_jus else 0
                            lmed_i = int(m_med.group(1)) if m_med else 0
                            lmat_i = int(m_mat.group(1)) if m_mat else 0
                            abo_i = int(m_abo.group(1)) if m_abo else 0
                            out_i = int(m_out.group(1)) if m_out else 0

                        state_16 = {
                            "inj": finj_i,
                            "jus": fjus_i,
                            "med": lmed_i,
                            "mat": lmat_i,
                            "abo": abo_i,
                            "out": out_i,
                            "link": evidencia_16,
                        }

                        def calc_tot_ausencias_16():
                            return (
                                int(state_16["inj"] or 0)
                                + int(state_16["jus"] or 0)
                                + int(state_16["med"] or 0)
                                + int(state_16["mat"] or 0)
                                + int(state_16["abo"] or 0)
                                + int(state_16["out"] or 0)
                            )

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_inj = ui.number("Faltas injustificadas (dias):", value=finj_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "inj")
                                inp_jus = ui.number("Faltas justificadas (dias):", value=fjus_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "jus")
                                inp_med = ui.number("Licença médica (dias):", value=lmed_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "med")
                                inp_mat = ui.number("Licença maternidade/paternidade (dias):", value=lmat_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "mat")
                                inp_abo = ui.number("Abonos (dias):", value=abo_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "abo")
                                inp_out = ui.number("Outros / ausências amparadas por lei (dias):", value=out_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_16, "out")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_16,
                                placeholder="Insira o relatório de frequência, sistema de RH ou espelho de ponto...",
                            ).classes("w-full").props("outlined rows=12").bind_value(
                                state_16, "link"
                            )

                        lbl_tot_16 = ui.label(
                            f"📊 Total de Dias de Ausência Registrados: {calc_tot_ausencias_16()} dias"
                        ).classes("text-sm font-bold text-blue-600 my-4")

                        def att_tot_16():
                            lbl_tot_16.set_text(
                                f"📊 Total de Dias de Ausência Registrados: {calc_tot_ausencias_16()} dias"
                            )

                        for inp in [inp_inj, inp_jus, inp_med, inp_mat, inp_abo, inp_out]:
                            inp.on("update:model-value", att_tot_16)

                        def salvar_16():
                            tot = calc_tot_ausencias_16()
                            inj, jus = state_16["inj"], state_16["jus"]
                            med, mat = state_16["med"], state_16["mat"]
                            abo, out = state_16["abo"], state_16["out"]

                            composite = f"INJ:{inj},JUS:{jus},MED:{med},MAT:{mat},ABO:{abo},OUT:{out}|LINK:{state_16['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.6",
                                valor=f"Total: {tot} dias",
                                pontos=0.0,
                                link=composite,
                                comentarios=d16.get("comentarios", []),
                                status=d16.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.6", on_click=salvar_16).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.6", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITOS 1.7 e 1.7.1 (Capacitação de Profissionais da Creche em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.7 / 1.7.1 • Capacitação dos Profissionais de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de profissionais capacitados e o total do quadro em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: PC = (Prof. Capacitados + Apoio Capacitados + Gestores Capacitados) / (Total Geral) | PC ≥ 100%: 7 pts | 70% ≤ PC < 100%: 5 pts | 50% ≤ PC < 70%: 3 pts"
                        ).classes("text-xs text-gray-400 mb-6")

                        d171 = res_data.get("1.7.1") or {}
                        raw_link_171 = str(d171.get("link") or "")

                        prof_cap_i, apoio_cap_i, gest_cap_i = 0, 0, 0
                        tot_prof_i, tot_apoio_i, tot_gest_i = 0, 0, 0
                        evidencia_171 = raw_link_171

                        if "|LINK:" in raw_link_171:
                            partes_171, evidencia_171 = raw_link_171.split("|LINK:", 1)
                            m_pcap = re.search(r"PCAP:(\d+)", partes_171)
                            m_acap = re.search(r"ACAP:(\d+)", partes_171)
                            m_gcap = re.search(r"GCAP:(\d+)", partes_171)
                            m_tprof = re.search(r"TPROF:(\d+)", partes_171)
                            m_tapoi = re.search(r"TAPOI:(\d+)", partes_171)
                            m_tgest = re.search(r"TGEST:(\d+)", partes_171)

                            prof_cap_i = int(m_pcap.group(1)) if m_pcap else 0
                            apoio_cap_i = int(m_acap.group(1)) if m_acap else 0
                            gest_cap_i = int(m_gcap.group(1)) if m_gcap else 0
                            tot_prof_i = int(m_tprof.group(1)) if m_tprof else 0
                            tot_apoio_i = int(m_tapoi.group(1)) if m_tapoi else 0
                            tot_gest_i = int(m_tgest.group(1)) if m_tgest else 0

                        state_171 = {
                            "prof_cap": prof_cap_i,
                            "apoio_cap": apoio_cap_i,
                            "gest_cap": gest_cap_i,
                            "tot_prof": tot_prof_i,
                            "tot_apoio": tot_apoio_i,
                            "tot_gest": tot_gest_i,
                            "link": evidencia_171,
                        }

                        def calc_pts_171():
                            num = int(state_171["prof_cap"] or 0) + int(state_171["apoio_cap"] or 0) + int(state_171["gest_cap"] or 0)
                            den = int(state_171["tot_prof"] or 0) + int(state_171["tot_apoio"] or 0) + int(state_171["tot_gest"] or 0)

                            if den <= 0 or num <= 0:
                                return 0.0

                            pc = num / den
                            if pc >= 1.0:
                                return 7.0
                            elif pc >= 0.70:
                                return 5.0
                            elif pc >= 0.50:
                                return 3.0
                            else:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                ui.label("CAPACITADOS EM 2025:").classes("font-bold text-xs text-blue-800 uppercase")
                                inp_pcap = ui.number("Professores regentes capacitados:", value=prof_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "prof_cap")
                                inp_acap = ui.number("Profissionais de apoio/supervisão capacitados:", value=apoio_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "apoio_cap")
                                inp_gcap = ui.number("Gestores escolares capacitados:", value=gest_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "gest_cap")

                                ui.label("QUADRO TOTAL (ETAPA CRECHE):").classes("font-bold text-xs text-blue-800 uppercase mt-2")
                                inp_tprof = ui.number("TOTAL de professores regentes:", value=tot_prof_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "tot_prof")
                                inp_tapoi = ui.number("TOTAL de profissionais de apoio/supervisão:", value=tot_apoio_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "tot_apoio")
                                inp_tgest = ui.number("TOTAL de gestores escolares de creche:", value=tot_gest_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_171, "tot_gest")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_171,
                                placeholder="Insira as listas de presença, certificados ou relatórios de capacitação...",
                            ).classes("w-full").props("outlined rows=14").bind_value(
                                state_171, "link"
                            )

                        lbl_pts_171 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.7.1: {calc_pts_171():.1f} / 7.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_171():
                            lbl_pts_171.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.7.1: {calc_pts_171():.1f} / 7.0 pontos"
                            )

                        for inp in [inp_pcap, inp_acap, inp_gcap, inp_tprof, inp_tapoi, inp_tgest]:
                            inp.on("update:model-value", att_pts_171)

                        def salvar_171():
                            p_cap, a_cap, g_cap = state_171["prof_cap"], state_171["apoio_cap"], state_171["gest_cap"]
                            t_prof, t_apoi, t_gest = state_171["tot_prof"], state_171["tot_apoio"], state_171["tot_gest"]
                            pts_finais = calc_pts_171()

                            composite = (
                                f"PCAP:{p_cap},ACAP:{a_cap},GCAP:{g_cap},"
                                f"TPROF:{t_prof},TAPOI:{t_apoi},TGEST:{t_gest}|LINK:{state_171['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="1.7.1",
                                valor=f"Capacitados: {p_cap + a_cap + g_cap} / Total: {t_prof + t_apoi + t_gest}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d171.get("comentarios", []),
                                status=d171.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.7.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.7.1", on_click=salvar_171).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.7.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.7.2 (Formas de Capacitação Oferecidas)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.7.2 • Formas de Capacitação Oferecidas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as modalidades de capacitação adotadas:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito qualitativo de seleção múltipla.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d172 = res_data.get("1.7.2") or {}
                        raw_link_172 = str(d172.get("link") or "")
                        val_172_bruto = d172.get("valor") or []

                        if isinstance(val_172_bruto, str):
                            try:
                                sel_172 = json.loads(val_172_bruto)
                            except Exception:
                                sel_172 = [val_172_bruto]
                        elif isinstance(val_172_bruto, list):
                            sel_172 = val_172_bruto
                        else:
                            sel_172 = []

                        state_172 = {
                            "opcoes": sel_172,
                            "link": raw_link_172,
                        }

                        opcoes_172 = [
                            "Presencialmente",
                            "À distância/remotamente",
                            "Por meio de multiplicadores",
                            "Outros",
                        ]

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("gap-2 w-full"):
                                def make_chk_172(opt_text):
                                    def on_chk_change(e):
                                        if e.value and opt_text not in state_172["opcoes"]:
                                            state_172["opcoes"].append(opt_text)
                                        elif not e.value and opt_text in state_172["opcoes"]:
                                            state_172["opcoes"].remove(opt_text)
                                    return on_chk_change

                                for opt in opcoes_172:
                                    ui.checkbox(
                                        text=opt,
                                        value=(opt in state_172["opcoes"]),
                                        on_change=make_chk_172(opt),
                                    ).props("color=blue")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_172,
                                placeholder="Insira o plano de formação, contratos das plataformas de EAD ou relatórios...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_172, "link"
                            )

                        def salvar_172():
                            opts_sel = state_172["opcoes"]
                            save_resposta(
                                ano=ano_sel,
                                qid="1.7.2",
                                valor=opts_sel,
                                pontos=0.0,
                                link=state_172["link"],
                                comentarios=d172.get("comentarios", []),
                                status=d172.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.7.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.7.2", on_click=salvar_172).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.7.2", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 1.8 (Rotatividade do Corpo Docente em Creches)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.8 • Rotatividade de Professores de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de escolas em cada faixa de rotatividade de professores de Creche:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 3.0 × (3×Q1 + 2×Q2 + 1×Q3 + 0×Q4) | Pmáx = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d18 = res_data.get("1.8") or {}
                        raw_link_18 = str(d18.get("link") or "")

                        q1_i, q2_i, q3_i, q4_i = 0, 0, 0, 0
                        evidencia_18 = raw_link_18

                        if "|LINK:" in raw_link_18:
                            partes_18, evidencia_18 = raw_link_18.split("|LINK:", 1)
                            m_q1 = re.search(r"Q1:(\d+)", partes_18)
                            m_q2 = re.search(r"Q2:(\d+)", partes_18)
                            m_q3 = re.search(r"Q3:(\d+)", partes_18)
                            m_q4 = re.search(r"Q4:(\d+)", partes_18)
                            q1_i = int(m_q1.group(1)) if m_q1 else 0
                            q2_i = int(m_q2.group(1)) if m_q2 else 0
                            q3_i = int(m_q3.group(1)) if m_q3 else 0
                            q4_i = int(m_q4.group(1)) if m_q4 else 0

                        state_18 = {
                            "q1": q1_i,
                            "q2": q2_i,
                            "q3": q3_i,
                            "q4": q4_i,
                            "link": evidencia_18,
                        }

                        def calc_pts_18():
                            c1 = int(state_18["q1"] or 0)
                            c2 = int(state_18["q2"] or 0)
                            c3 = int(state_18["q3"] or 0)
                            c4 = int(state_18["q4"] or 0)
                            tot_escolas = c1 + c2 + c3 + c4
                            if tot_escolas <= 0:
                                return 0.0

                            prop1 = c1 / tot_escolas
                            prop2 = c2 / tot_escolas
                            prop3 = c3 / tot_escolas
                            prop4 = c4 / tot_escolas

                            n1 = 3.0 * prop1
                            n2 = 2.0 * prop2
                            n3 = 1.0 * prop3
                            n4 = 0.0 * prop4

                            return min(3.0 * (n1 + n2 + n3 + n4), 3.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_q1 = (
                                    ui.number(
                                        "Escolas com rotatividade MENOR que 20%:",
                                        value=q1_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_18, "q1")
                                )

                                inp_q2 = (
                                    ui.number(
                                        "Escolas com rotatividade entre 20% e 29,9%:",
                                        value=q2_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_18, "q2")
                                )

                                inp_q3 = (
                                    ui.number(
                                        "Escolas com rotatividade entre 30% e 39,9%:",
                                        value=q3_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_18, "q3")
                                )

                                inp_q4 = (
                                    ui.number(
                                        "Escolas com rotatividade MAIOR ou igual a 40%:",
                                        value=q4_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_18, "q4")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_18,
                                placeholder="Insira relatórios de atribuição de aulas, remoção de docentes ou folhas de ponto...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_18, "link"
                            )

                        lbl_pts_18 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.8: {calc_pts_18():.2f} / 3.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_18():
                            lbl_pts_18.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.8: {calc_pts_18():.2f} / 3.0 pontos"
                            )

                        inp_q1.on("update:model-value", att_pts_18)
                        inp_q2.on("update:model-value", att_pts_18)
                        inp_q3.on("update:model-value", att_pts_18)
                        inp_q4.on("update:model-value", att_pts_18)

                        def salvar_18():
                            c1 = int(state_18["q1"] or 0)
                            c2 = int(state_18["q2"] or 0)
                            c3 = int(state_18["q3"] or 0)
                            c4 = int(state_18["q4"] or 0)
                            pts_finais = calc_pts_18()
                            composite = f"Q1:{c1},Q2:{c2},Q3:{c3},Q4:{c4}|LINK:{state_18['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.8",
                                valor=f"Q1:{c1}/Q2:{c2}/Q3:{c3}/Q4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d18.get("comentarios", []),
                                status=d18.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.8 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.8", on_click=salvar_18).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.8", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.9 (Regularidade / Permanência dos Gestores de Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.9 • Regularidade e Permanência dos Gestores de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Indique a quantidade de escolas por tempo de permanência do diretor/gestor de Creche (ao final de 2025):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 0×Q1 + 0.5×Q2 + 1.0×Q3 + 1.5×Q4 + 1.75×Q5 + 2.0×Q6 | Pmáx = 2.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d19 = res_data.get("1.9") or {}
                        raw_link_19 = str(d19.get("link") or "")

                        g1_i, g2_i, g3_i, g4_i, g5_i, g6_i = 0, 0, 0, 0, 0, 0
                        evidencia_19 = raw_link_19

                        if "|LINK:" in raw_link_19:
                            partes_19, evidencia_19 = raw_link_19.split("|LINK:", 1)
                            m_g1 = re.search(r"G1:(\d+)", partes_19)
                            m_g2 = re.search(r"G2:(\d+)", partes_19)
                            m_g3 = re.search(r"G3:(\d+)", partes_19)
                            m_g4 = re.search(r"G4:(\d+)", partes_19)
                            m_g5 = re.search(r"G5:(\d+)", partes_19)
                            m_g6 = re.search(r"G6:(\d+)", partes_19)

                            g1_i = int(m_g1.group(1)) if m_g1 else 0
                            g2_i = int(m_g2.group(1)) if m_g2 else 0
                            g3_i = int(m_g3.group(1)) if m_g3 else 0
                            g4_i = int(m_g4.group(1)) if m_g4 else 0
                            g5_i = int(m_g5.group(1)) if m_g5 else 0
                            g6_i = int(m_g6.group(1)) if m_g6 else 0

                        state_19 = {
                            "g1": g1_i,
                            "g2": g2_i,
                            "g3": g3_i,
                            "g4": g4_i,
                            "g5": g5_i,
                            "g6": g6_i,
                            "link": evidencia_19,
                        }

                        def calc_pts_19():
                            c1 = int(state_19["g1"] or 0)
                            c2 = int(state_19["g2"] or 0)
                            c3 = int(state_19["g3"] or 0)
                            c4 = int(state_19["g4"] or 0)
                            c5 = int(state_19["g5"] or 0)
                            c6 = int(state_19["g6"] or 0)

                            tot = c1 + c2 + c3 + c4 + c5 + c6
                            if tot <= 0:
                                return 0.0

                            q1 = c1 / tot
                            q2 = c2 / tot
                            q3 = c3 / tot
                            q4 = c4 / tot
                            q5 = c5 / tot
                            q6 = c6 / tot

                            n1 = 0.0 * q1
                            n2 = 0.5 * q2
                            n3 = 1.0 * q3
                            n4 = 1.5 * q4
                            n5 = 1.75 * q5
                            n6 = 2.0 * q6

                            return min(n1 + n2 + n3 + n4 + n5 + n6, 2.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_g1 = ui.number("Menor que 1 ano:", value=g1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g1")
                                inp_g2 = ui.number("De 1 ano a 2,9 anos:", value=g2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g2")
                                inp_g3 = ui.number("De 3 anos a 4,9 anos:", value=g3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g3")
                                inp_g4 = ui.number("De 5 anos a 9,9 anos:", value=g4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g4")
                                inp_g5 = ui.number("De 10 anos a 14,9 anos:", value=g5_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g5")
                                inp_g6 = ui.number("Maior ou igual a 15 anos:", value=g6_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_19, "g6")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_19,
                                placeholder="Insira o histórico funcional dos gestores, atos de nomeação ou portarias...",
                            ).classes("w-full").props("outlined rows=12").bind_value(
                                state_19, "link"
                            )

                        lbl_pts_19 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.9: {calc_pts_19():.2f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_19():
                            lbl_pts_19.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.9: {calc_pts_19():.2f} / 2.0 pontos"
                            )

                        for inp in [inp_g1, inp_g2, inp_g3, inp_g4, inp_g5, inp_g6]:
                            inp.on("update:model-value", att_pts_19)

                        def salvar_19():
                            c1, c2, c3 = state_19["g1"], state_19["g2"], state_19["g3"]
                            c4, c5, c6 = state_19["g4"], state_19["g5"], state_19["g6"]
                            pts_finais = calc_pts_19()

                            composite = f"G1:{c1},G2:{c2},G3:{c3},G4:{c4},G5:{c5},G6:{c6}|LINK:{state_19['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.9",
                                valor=f"G1:{c1}/G2:{c2}/G3:{c3}/G4:{c4}/G5:{c5}/G6:{c6}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d19.get("comentarios", []),
                                status=d19.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.9 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.9", on_click=salvar_19).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.9", res_data, render_conteudo.refresh)

                   # =============================================================================
                    # QUESITO 1.10 (Reuniões Periódicas com Pais - Escopo)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.10 • Reuniões Periódicas com Pais dos Alunos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os professores realizam reuniões periódicas com os pais dos alunos de Creche sobre planejamento/projeto escolar e desempenho/desenvolvimento da criança?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Selecione uma das opções abaixo para registrar a pontuação."
                        ).classes("text-xs text-gray-400 mb-6")

                        d110 = res_data.get("1.10") or {}

                        # Mapeamento com a exibição textual das opções incluindo a pontuação ao lado
                        opcoes_110 = {
                            "Selecione...": 0.0,
                            "Sobre planejamento e desempenho da criança (2,0 pontos)": 2.0,
                            "Apenas sobre o projeto político-pedagógico (1,5 pontos)": 1.5,
                            "Apenas sobre o desempenho da criança (1,0 ponto)": 1.0,
                            "Não realiza reuniões periódicas (0,0 pontos)": 0.0,
                        }

                        val_110_bruto = str(d110.get("valor") or "")

                        # Valida se o valor salvo no banco bate com alguma opção da lista
                        val_110_valido = "Selecione..."
                        if val_110_bruto in opcoes_110:
                            val_110_valido = val_110_bruto
                        else:
                            # Tenta mapear valores legados sem o texto da pontuação
                            for chave in opcoes_110.keys():
                                if chave != "Selecione..." and chave.startswith(val_110_bruto):
                                    val_110_valido = chave
                                    break

                        raw_link_110 = str(d110.get("link") or "")

                        state_110 = {
                            "escopo": val_110_valido,
                            "link": raw_link_110,
                        }

                        def calc_pts_110():
                            return float(opcoes_110.get(state_110["escopo"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_110 = ui.radio(
                                    options=list(opcoes_110.keys()),
                                    value=state_110["escopo"],
                                ).props("color=blue").bind_value(state_110, "escopo")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_110,
                                placeholder="Insira atas de reunião com pais, calendário escolar ou convocatórias...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_110, "link"
                            )

                        lbl_pts_110 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.10: {calc_pts_110():.1f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_110():
                            lbl_pts_110.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.10: {calc_pts_110():.1f} / 2.0 pontos"
                            )

                        rad_110.on("update:model-value", att_pts_110)

                        def salvar_110():
                            pts = calc_pts_110()
                            escopo_sel = state_110["escopo"]
                            lnk = state_110["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="1.10",
                                valor=escopo_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d110.get("comentarios", []),
                                status=d110.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.10 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.10", on_click=salvar_110).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.10", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.10.1 (Periodicidade das Reuniões com Pais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.10.1 • Periodicidade das Reuniões com Pais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Qual a periodicidade das reuniões com os pais?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d1101 = res_data.get("1.10.1") or {}

                        opcoes_1101 = [
                            "Mensal",
                            "Bimestral",
                            "Trimestral",
                            "Quadrimestral",
                            "Semestral",
                            "Anual",
                        ]

                        val_1101_bruto = str(d1101.get("valor") or "")
                        val_1101_valido = (
                            val_1101_bruto if val_1101_bruto in opcoes_1101 else "Bimestral"
                        )

                        raw_link_1101 = str(d1101.get("link") or "")

                        state_1101 = {
                            "periodicidade": val_1101_valido,
                            "link": raw_link_1101,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_1101 = ui.radio(
                                    options=opcoes_1101,
                                    value=state_1101["periodicidade"],
                                ).props("color=blue").bind_value(state_1101, "periodicidade")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_1101,
                                placeholder="Insira o calendário escolar ou regulamento com a periodicidade...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_1101, "link"
                            )

                        def salvar_1101():
                            per_sel = state_1101["periodicidade"]
                            lnk = state_1101["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="1.10.1",
                                valor=per_sel,
                                pontos=0.0,
                                link=lnk,
                                comentarios=d1101.get("comentarios", []),
                                status=d1101.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.10.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.10.1", on_click=salvar_1101).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.10.1", res_data, render_conteudo.refresh)

    render_conteudo()
    return main_container

