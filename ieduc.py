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

    # =============================================================================
                    # QUESITO 1.11 (Entrega do Kit Escolar às Creches em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.11 • Entrega do Kit Escolar às Creches em 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do Kit escolar às Creches municipais no ano de 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Kit escolar = material escolar e pedagógico."
                        ).classes("text-xs text-gray-400 mb-6")

                        d111 = res_data.get("1.11") or {}

                        opcoes_111 = {
                            "Selecione...": 0.0,
                            "Sim (0,0 pontos)": 0.0,
                            "O kit escolar permanece no almoxarifado da escola e é retirado no momento do uso pelos alunos (18,0 pontos)": 18.0,
                            "Não (0,0 pontos)": 0.0,
                        }

                        val_111_bruto = str(d111.get("valor") or "")
                        val_111_valido = "Selecione..."
                        if val_111_bruto in opcoes_111:
                            val_111_valido = val_111_bruto
                        else:
                            for chave in opcoes_111.keys():
                                if chave != "Selecione..." and chave.startswith(val_111_bruto):
                                    val_111_valido = chave
                                    break

                        raw_link_111 = str(d111.get("link") or "")

                        state_111 = {
                            "opcao": val_111_valido,
                            "link": raw_link_111,
                        }

                        def calc_pts_111():
                            return float(opcoes_111.get(state_111["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_111 = ui.radio(
                                    options=list(opcoes_111.keys()),
                                    value=state_111["opcao"],
                                ).props("color=blue").bind_value(state_111, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_111,
                                placeholder="Insira o comprovante de distribuição, fotos ou termo de entrega...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_111, "link"
                            )

                        lbl_pts_111 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.11: {calc_pts_111():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_111():
                            lbl_pts_111.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.11: {calc_pts_111():.1f} / 18.0 pontos"
                            )

                        rad_111.on("update:model-value", att_pts_111)

                        def salvar_111():
                            pts = calc_pts_111()
                            opt_sel = state_111["opcao"]
                            lnk = state_111["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="1.11",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d111.get("comentarios", []),
                                status=d111.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.11 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.11", on_click=salvar_111).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.11", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.11.1 (Data da Última Entrega do Kit Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.11.1 • Data da Última Entrega do Kit Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega e a data de início das aulas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: ≤ Início Aulas = 18.0 pts | < Início + 15 dias = 9.0 pts | ≥ Início + 15 dias = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d1111 = res_data.get("1.11.1") or {}
                        raw_link_1111 = str(d1111.get("link") or "")

                        dt_entrega_i, dt_inicio_i = "", "05/02/2025"
                        evidencia_1111 = raw_link_1111

                        if "|LINK:" in raw_link_1111:
                            partes_1111, evidencia_1111 = raw_link_1111.split("|LINK:", 1)
                            m_ent = re.search(r"ENTREGA:([\d/]+)", partes_1111)
                            m_ini = re.search(r"INICIO:([\d/]+)", partes_1111)
                            dt_entrega_i = m_ent.group(1) if m_ent else ""
                            dt_inicio_i = m_ini.group(1) if m_ini else "05/02/2025"
                        elif d1111.get("valor"):
                            dt_entrega_i = str(d1111.get("valor"))

                        state_1111 = {
                            "dt_entrega": dt_entrega_i,
                            "dt_inicio": dt_inicio_i,
                            "link": evidencia_1111,
                        }

                        def calc_pts_1111():
                            try:
                                ent = datetime.strptime(state_1111["dt_entrega"].strip(), "%d/%m/%Y")
                                ini = datetime.strptime(state_1111["dt_inicio"].strip(), "%d/%m/%Y")
                                diff_dias = (ent - ini).days

                                if diff_dias <= 0:
                                    return 18.0
                                elif diff_dias < 15:
                                    return 9.0
                                else:
                                    return 3.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_dt_ent_1111 = ui.input(
                                    label="Data da última entrega (DD/MM/AAAA):",
                                    value=dt_entrega_i,
                                    placeholder="Ex: 03/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1111, "dt_entrega")

                                inp_dt_ini_1111 = ui.input(
                                    label="Data de início das aulas (DD/MM/AAAA):",
                                    value=dt_inicio_i,
                                    placeholder="Ex: 05/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1111, "dt_inicio")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_1111,
                                placeholder="Insira o protocolo de entrega nas escolas ou calendário escolar oficial...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_1111, "link"
                            )

                        lbl_pts_1111 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.11.1: {calc_pts_1111():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_1111():
                            lbl_pts_1111.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.11.1: {calc_pts_1111():.1f} / 18.0 pontos"
                            )

                        inp_dt_ent_1111.on("update:model-value", att_pts_1111)
                        inp_dt_ini_1111.on("update:model-value", att_pts_1111)

                        def salvar_1111():
                            pts = calc_pts_1111()
                            ent_v = state_1111["dt_entrega"].strip()
                            ini_v = state_1111["dt_inicio"].strip()
                            composite = f"ENTREGA:{ent_v},INICIO:{ini_v}|LINK:{state_1111['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.11.1",
                                valor=ent_v,
                                pontos=pts,
                                link=composite,
                                comentarios=d1111.get("comentarios", []),
                                status=d1111.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.11.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.11.1", on_click=salvar_1111).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.11.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.11.2 (Motivo da Não Entrega do Kit Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.11.2 • Motivo da Não Entrega do Kit Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Caso o Kit Escolar não tenha sido entregue, informe o motivo:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito justificativo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d1112 = res_data.get("1.11.2") or {}
                        motivo_i = str(d1112.get("valor") or "")
                        link_1112_i = str(d1112.get("link") or "")

                        state_1112 = {
                            "motivo": motivo_i,
                            "link": link_1112_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_i,
                                placeholder="Descreva os problemas de licitação, atraso de fornecedores ou entraves operacionais...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_1112, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_1112_i,
                                placeholder="Insira processos administrativos, pareceres ou justificativa oficial...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_1112, "link"
                            )

                        def salvar_1112():
                            save_resposta(
                                ano=ano_sel,
                                qid="1.11.2",
                                valor=state_1112["motivo"],
                                pontos=0.0,
                                link=state_1112["link"],
                                comentarios=d1112.get("comentarios", []),
                                status=d1112.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.11.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.11.2", on_click=salvar_1112).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.11.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.12 (Entrega de Material Didático às Creches em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.12 • Entrega de Material Didático às Creches em 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do material didático (livros, apostilas, etc.) às Creches municipais em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Apostilas/livros fornecidos pelo Município, Estado ou Governo Federal."
                        ).classes("text-xs text-gray-400 mb-6")

                        d112 = res_data.get("1.12") or {}

                        opcoes_112 = {
                            "Selecione...": 0.0,
                            "Sim (0,0 pontos)": 0.0,
                            "Não (0,0 pontos)": 0.0,
                            "O material didático é elaborado na própria escola (18,0 pontos)": 18.0,
                        }

                        val_112_bruto = str(d112.get("valor") or "")
                        val_112_valido = "Selecione..."
                        if val_112_bruto in opcoes_112:
                            val_112_valido = val_112_bruto
                        else:
                            for chave in opcoes_112.keys():
                                if chave != "Selecione..." and chave.startswith(val_112_bruto):
                                    val_112_valido = chave
                                    break

                        raw_link_112 = str(d112.get("link") or "")

                        state_112 = {
                            "opcao": val_112_valido,
                            "link": raw_link_112,
                        }

                        def calc_pts_112():
                            return float(opcoes_112.get(state_112["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_112 = ui.radio(
                                    options=list(opcoes_112.keys()),
                                    value=state_112["opcao"],
                                ).props("color=blue").bind_value(state_112, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_112,
                                placeholder="Insira o comprovante de recebimento do PNLD, notas de entrega ou projeto pedagógico autorautoral...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_112, "link"
                            )

                        lbl_pts_112 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.12: {calc_pts_112():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_112():
                            lbl_pts_112.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.12: {calc_pts_112():.1f} / 18.0 pontos"
                            )

                        rad_112.on("update:model-value", att_pts_112)

                        def salvar_112():
                            pts = calc_pts_112()
                            opt_sel = state_112["opcao"]
                            lnk = state_112["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="1.12",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d112.get("comentarios", []),
                                status=d112.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.12 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.12", on_click=salvar_112).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.12", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.12.1 (Data da Última Entrega do Material Didático)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.12.1 • Data da Última Entrega do Material Didático").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega e a data de início das aulas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: ≤ Início Aulas = 18.0 pts | < Início + 15 dias = 9.0 pts | ≥ Início + 15 dias = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d1121 = res_data.get("1.12.1") or {}
                        raw_link_1121 = str(d1121.get("link") or "")

                        dt_entrega_mat_i, dt_inicio_mat_i = "", "05/02/2025"
                        evidencia_1121 = raw_link_1121

                        if "|LINK:" in raw_link_1121:
                            partes_1121, evidencia_1121 = raw_link_1121.split("|LINK:", 1)
                            m_ent = re.search(r"ENTREGA:([\d/]+)", partes_1121)
                            m_ini = re.search(r"INICIO:([\d/]+)", partes_1121)
                            dt_entrega_mat_i = m_ent.group(1) if m_ent else ""
                            dt_inicio_mat_i = m_ini.group(1) if m_ini else "05/02/2025"
                        elif d1121.get("valor"):
                            dt_entrega_mat_i = str(d1121.get("valor"))

                        state_1121 = {
                            "dt_entrega": dt_entrega_mat_i,
                            "dt_inicio": dt_inicio_mat_i,
                            "link": evidencia_1121,
                        }

                        def calc_pts_1121():
                            try:
                                ent = datetime.strptime(state_1121["dt_entrega"].strip(), "%d/%m/%Y")
                                ini = datetime.strptime(state_1121["dt_inicio"].strip(), "%d/%m/%Y")
                                diff_dias = (ent - ini).days

                                if diff_dias <= 0:
                                    return 18.0
                                elif diff_dias < 15:
                                    return 9.0
                                else:
                                    return 3.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_dt_ent_1121 = ui.input(
                                    label="Data da última entrega (DD/MM/AAAA):",
                                    value=dt_entrega_mat_i,
                                    placeholder="Ex: 02/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1121, "dt_entrega")

                                inp_dt_ini_1121 = ui.input(
                                    label="Data de início das aulas (DD/MM/AAAA):",
                                    value=dt_inicio_mat_i,
                                    placeholder="Ex: 05/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_1121, "dt_inicio")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_1121,
                                placeholder="Insira as guias de remessa do FNDE, termo de recebimento nas creches...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_1121, "link"
                            )

                        lbl_pts_1121 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.12.1: {calc_pts_1121():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_1121():
                            lbl_pts_1121.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.12.1: {calc_pts_1121():.1f} / 18.0 pontos"
                            )

                        inp_dt_ent_1121.on("update:model-value", att_pts_1121)
                        inp_dt_ini_1121.on("update:model-value", att_pts_1121)

                        def salvar_1121():
                            pts = calc_pts_1121()
                            ent_v = state_1121["dt_entrega"].strip()
                            ini_v = state_1121["dt_inicio"].strip()
                            composite = f"ENTREGA:{ent_v},INICIO:{ini_v}|LINK:{state_1121['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.12.1",
                                valor=ent_v,
                                pontos=pts,
                                link=composite,
                                comentarios=d1121.get("comentarios", []),
                                status=d1121.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.12.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.12.1", on_click=salvar_1121).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.12.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 1.12.2 (Motivo da Não Entrega do Material Didático)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.12.2 • Motivo da Não Entrega do Material Didático").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Caso o material didático não tenha sido entregue, informe o motivo:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito justificativo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d1122 = res_data.get("1.12.2") or {}
                        motivo_mat_i = str(d1122.get("valor") or "")
                        link_1122_i = str(d1122.get("link") or "")

                        state_1122 = {
                            "motivo": motivo_mat_i,
                            "link": link_1122_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_mat_i,
                                placeholder="Descreva os problemas de compra, adesão ao PNLD ou atraso de distribuição...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_1122, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_1122_i,
                                placeholder="Insira relatórios administrativos, comunicação oficial ou justificativa...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_1122, "link"
                            )

                        def salvar_1122():
                            save_resposta(
                                ano=ano_sel,
                                qid="1.12.2",
                                valor=state_1122["motivo"],
                                pontos=0.0,
                                link=state_1122["link"],
                                comentarios=d1122.get("comentarios", []),
                                status=d1122.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.12.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.12.2", on_click=salvar_1122).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.12.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.13 (Pesquisa/Estudo sobre Demanda por Creches em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.13 • Pesquisa/Estudo de Demanda por Vagas de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura municipal fez uma pesquisa/estudo para levantar o número de crianças que necessitavam de Creches em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuação: Sim = 50.0 pts | Não = 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d113 = res_data.get("1.13") or {}

                        opcoes_113 = {
                            "Selecione...": 0.0,
                            "Sim (50,0 pontos)": 50.0,
                            "Não (0,0 pontos)": 0.0,
                        }

                        val_113_bruto = str(d113.get("valor") or "")
                        val_113_valido = "Selecione..."
                        if val_113_bruto in opcoes_113:
                            val_113_valido = val_113_bruto
                        else:
                            for chave in opcoes_113.keys():
                                if chave != "Selecione..." and chave.startswith(val_113_bruto):
                                    val_113_valido = chave
                                    break

                        raw_link_113 = str(d113.get("link") or "")

                        state_113 = {
                            "opcao": val_113_valido,
                            "link": raw_link_113,
                        }

                        def calc_pts_113():
                            return float(opcoes_113.get(state_113["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_113 = ui.radio(
                                    options=list(opcoes_113.keys()),
                                    value=state_113["opcao"],
                                ).props("color=blue").bind_value(state_113, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_113,
                                placeholder="Insira a cópia do estudo de demanda, busca ativa ou relatório de mapeamento...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_113, "link"
                            )

                        lbl_pts_113 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.13: {calc_pts_113():.1f} / 50.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_113():
                            lbl_pts_113.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.13: {calc_pts_113():.1f} / 50.0 pontos"
                            )

                        rad_113.on("update:model-value", att_pts_113)

                        def salvar_113():
                            pts = calc_pts_113()
                            opt_sel = state_113["opcao"]
                            lnk = state_113["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="1.13",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d113.get("comentarios", []),
                                status=d113.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.13 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.13", on_click=salvar_113).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.13", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.13.1 (Descrição do Estudo de Demanda)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.13.1 • Descrição da Pesquisa / Estudo de Demanda").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Descreva a metodologia e os resultados da pesquisa/estudo realizada:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito descritivo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d1131 = res_data.get("1.13.1") or {}
                        desc_i = str(d1131.get("valor") or "")
                        link_1131_i = str(d1131.get("link") or "")

                        state_1131 = {
                            "descricao": desc_i,
                            "link": link_1131_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Descrição do Estudo / Pesquisa:",
                                value=desc_i,
                                placeholder="Descreva como foi feito o mapeamento, órgãos envolvidos e conclusões...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_1131, "descricao"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_1131_i,
                                placeholder="Insira o link do documento da pesquisa, ato normativo ou publicação oficial...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_1131, "link"
                            )

                        def salvar_1131():
                            save_resposta(
                                ano=ano_sel,
                                qid="1.13.1",
                                valor=state_1131["descricao"],
                                pontos=0.0,
                                link=state_1131["link"],
                                comentarios=d1131.get("comentarios", []),
                                status=d1131.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.13.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.13.1", on_click=salvar_1131).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.13.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 1.14 (Demanda vs. Oferta de Vagas de Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.14 • Demanda Manifesta vs. Oferta de Vagas de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de solicitações de vagas (0 a 3 anos) até 31/12/2025 e o total de vagas ofertadas:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se Solicitações (Demanda) > Vagas Ofertadas (Oferta) → Perde 50.0 pts (-50.0) | Caso contrário → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d114 = res_data.get("1.14") or {}
                        raw_link_114 = str(d114.get("link") or "")

                        demanda_i, oferta_i = 0, 0
                        evidencia_114 = raw_link_114

                        if "|LINK:" in raw_link_114:
                            partes_114, evidencia_114 = raw_link_114.split("|LINK:", 1)
                            m_dem = re.search(r"DEMANDA:(\d+)", partes_114)
                            m_ofe = re.search(r"OFERTA:(\d+)", partes_114)
                            demanda_i = int(m_dem.group(1)) if m_dem else 0
                            oferta_i = int(m_ofe.group(1)) if m_ofe else 0

                        state_114 = {
                            "demanda": demanda_i,
                            "oferta": oferta_i,
                            "link": evidencia_114,
                        }

                        def calc_pts_114():
                            dem = int(state_114["demanda"] or 0)
                            ofe = int(state_114["oferta"] or 0)
                            if dem <= 0 and ofe <= 0:
                                return 0.0
                            return -50.0 if dem > ofe else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_demanda = (
                                    ui.number(
                                        "Nº de crianças (0-3 anos) que solicitaram vaga até 31/12/2025:",
                                        value=demanda_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_114, "demanda")
                                )

                                inp_oferta = (
                                    ui.number(
                                        "Nº de vagas de creche OFERTADAS em 2025:",
                                        value=oferta_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_114, "oferta")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_114,
                                placeholder="Insira a lista de espera unificada, relatório de matrículas do Censo ou sistema municipal...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_114, "link"
                            )

                        lbl_pts_114 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.14: {calc_pts_114():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_114():
                            pts = calc_pts_114()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_114.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_114.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.14: {pts:.1f} pontos"
                            )

                        inp_demanda.on("update:model-value", att_pts_114)
                        inp_oferta.on("update:model-value", att_pts_114)

                        def salvar_114():
                            d_val = int(state_114["demanda"] or 0)
                            o_val = int(state_114["oferta"] or 0)
                            pts_finais = calc_pts_114()
                            composite = f"DEMANDA:{d_val},OFERTA:{o_val}|LINK:{state_114['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.14",
                                valor=f"Solicitadas: {d_val} / Ofertadas: {o_val}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d114.get("comentarios", []),
                                status=d114.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 1.14 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.14", on_click=salvar_114).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.14", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 1.15 (Quantidade de Alunos por Turma de Creche)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("1.15 • Distribuição de Alunos por Turma de Creche").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de turmas de Creche em cada faixa de número de alunos:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 10.0 × (1.0×P1 + 0.5×P2 + 0.25×P3 + 0×P4) | Pmáx = 10.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d115 = res_data.get("1.15") or {}
                        raw_link_115 = str(d115.get("link") or "")

                        t1_i, t2_i, t3_i, t4_i = 0, 0, 0, 0
                        evidencia_115 = raw_link_115

                        if "|LINK:" in raw_link_115:
                            partes_115, evidencia_115 = raw_link_115.split("|LINK:", 1)
                            m_t1 = re.search(r"T1:(\d+)", partes_115)
                            m_t2 = re.search(r"T2:(\d+)", partes_115)
                            m_t3 = re.search(r"T3:(\d+)", partes_115)
                            m_t4 = re.search(r"T4:(\d+)", partes_115)
                            t1_i = int(m_t1.group(1)) if m_t1 else 0
                            t2_i = int(m_t2.group(1)) if m_t2 else 0
                            t3_i = int(m_t3.group(1)) if m_t3 else 0
                            t4_i = int(m_t4.group(1)) if m_t4 else 0

                        state_115 = {
                            "t1": t1_i,
                            "t2": t2_i,
                            "t3": t3_i,
                            "t4": t4_i,
                            "link": evidencia_115,
                        }

                        def calc_pts_115():
                            c1 = int(state_115["t1"] or 0)
                            c2 = int(state_115["t2"] or 0)
                            c3 = int(state_115["t3"] or 0)
                            c4 = int(state_115["t4"] or 0)
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

                            return min(10.0 * (n1 + n2 + n3 + n4), 10.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_t1 = ui.number("Turmas com até 13 alunos:", value=t1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_115, "t1")
                                inp_t2 = ui.number("Turmas de 14 a 20 alunos:", value=t2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_115, "t2")
                                inp_t3 = ui.number("Turmas de 21 a 25 alunos:", value=t3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_115, "t3")
                                inp_t4 = ui.number("Turmas acima de 25 alunos:", value=t4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_115, "t4")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_115,
                                placeholder="Insira o relatório de turmas/enturmação do Censo Escolar ou sistema de gestão...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_115, "link"
                            )

                        lbl_pts_115 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 1.15: {calc_pts_115():.2f} / 10.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_115():
                            lbl_pts_115.set_text(
                                f"📊 Impacto de Pontuação no Quesito 1.15: {calc_pts_115():.2f} / 10.0 pontos"
                            )

                        for inp in [inp_t1, inp_t2, inp_t3, inp_t4]:
                            inp.on("update:model-value", att_pts_115)

                        def salvar_115():
                            c1, c2, c3, c4 = state_115["t1"], state_115["t2"], state_115["t3"], state_115["t4"]
                            pts_finais = calc_pts_115()
                            composite = f"T1:{c1},T2:{c2},T3:{c3},T4:{c4}|LINK:{state_115['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="1.15",
                                valor=f"T1:{c1}/T2:{c2}/T3:{c3}/T4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d115.get("comentarios", []),
                                status=d115.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 1.15 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 1.15", on_click=salvar_115).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("1.15", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.0 (Ofertamento de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.0 • Oferta de Pré-escola no Município").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura municipal oferece Pré-escola?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório / condicional de etapa.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d20 = res_data.get("2.0") or {}
                        opcoes_20 = ["Selecione...", "Sim", "Não"]

                        val_20_bruto = str(d20.get("valor") or "")
                        val_20_valido = val_20_bruto if val_20_bruto in opcoes_20 else "Selecione..."
                        raw_link_20 = str(d20.get("link") or "")

                        state_20 = {
                            "opcao": val_20_valido,
                            "link": raw_link_20,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_20 = ui.radio(
                                options=opcoes_20,
                                value=state_20["opcao"],
                            ).props("color=blue").bind_value(state_20, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_20,
                                placeholder="Insira o decreto de criação da rede, atos da secretaria ou cadastro oficial...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_20, "link"
                            )

                        def salvar_20():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.0",
                                valor=state_20["opcao"],
                                pontos=0.0,
                                link=state_20["link"],
                                comentarios=d20.get("comentarios", []),
                                status=d20.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.0", on_click=salvar_20).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.1 (Presença de Brinquedos no Pátio de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.1 • Brinquedos no Pátio Infantil em Pré-escolas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Algum estabelecimento que oferece Pré-escola possui brinquedos no Pátio Infantil?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito triagem.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d21 = res_data.get("2.1") or {}
                        opcoes_21 = ["Selecione...", "Sim", "Não"]

                        val_21_bruto = str(d21.get("valor") or "")
                        val_21_valido = val_21_bruto if val_21_bruto in opcoes_21 else "Selecione..."
                        raw_link_21 = str(d21.get("link") or "")

                        state_21 = {
                            "opcao": val_21_valido,
                            "link": raw_link_21,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_21 = ui.radio(
                                options=opcoes_21,
                                value=state_21["opcao"],
                            ).props("color=blue").bind_value(state_21, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_21,
                                placeholder="Insira fotos, relatórios de vistoria ou tombamento de parque infantil...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_21, "link"
                            )

                        def salvar_21():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.1",
                                valor=state_21["opcao"],
                                pontos=0.0,
                                link=state_21["link"],
                                comentarios=d21.get("comentarios", []),
                                status=d21.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.1", on_click=salvar_21).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.1.1 (Brinquedos no Pátio Infantil - BPI Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.1.1 • Cobertura de Brinquedos em Pré-escolas (BPI)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe quantos estabelecimentos que oferecem Pré-escola possuem Brinquedos no Pátio e o total do município:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: BPI = (Nº Pré-escolas com brinquedos / Nº Total Pré-escolas) × 2.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d211 = res_data.get("2.1.1") or {}
                        raw_link_211 = str(d211.get("link") or "")

                        bpi_pre_i, tot_pre_i = 0, 0
                        evidencia_211 = raw_link_211

                        if "|LINK:" in raw_link_211:
                            partes_211, evidencia_211 = raw_link_211.split("|LINK:", 1)
                            m_bpi = re.search(r"BPI:(\d+)", partes_211)
                            m_tot = re.search(r"TOT:(\d+)", partes_211)
                            bpi_pre_i = int(m_bpi.group(1)) if m_bpi else 0
                            tot_pre_i = int(m_tot.group(1)) if m_tot else 0

                        state_211 = {
                            "bpi": bpi_pre_i,
                            "total": tot_pre_i,
                            "link": evidencia_211,
                        }

                        def calc_pts_211():
                            tot = int(state_211["total"] or 0)
                            bpi = int(state_211["bpi"] or 0)
                            if tot <= 0 or bpi <= 0:
                                return 0.0
                            prop = min(bpi / tot, 1.0)
                            return prop * 2.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_bpi_211 = ui.number("Nº de pré-escolas COM brinquedos no pátio:", value=bpi_pre_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_211, "bpi")
                                inp_tot_211 = ui.number("TOTAL de pré-escolas no município:", value=tot_pre_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_211, "total")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_211,
                                placeholder="Insira a relação de escolas com parques, vistorias ou tombamento...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_211, "link"
                            )

                        lbl_pts_211 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.1.1: {calc_pts_211():.2f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_211():
                            lbl_pts_211.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.1.1: {calc_pts_211():.2f} / 2.0 pontos"
                            )

                        inp_bpi_211.on("update:model-value", att_pts_211)
                        inp_tot_211.on("update:model-value", att_pts_211)

                        def salvar_211():
                            b_val = int(state_211["bpi"] or 0)
                            t_val = int(state_211["total"] or 0)
                            pts_finais = calc_pts_211()
                            composite = f"BPI:{b_val},TOT:{t_val}|LINK:{state_211['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.1.1",
                                valor=f"{b_val}/{t_val}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d211.get("comentarios", []),
                                status=d211.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.1.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.1.1", on_click=salvar_211).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.1.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.1.2 (Manutenção Preventiva dos Brinquedos - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.1.2 • Manutenção dos Brinquedos do Pátio em Pré-escolas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a distribuição das pré-escolas quanto ao cumprimento do cronograma de manutenção:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: P = P1 + P2 + P3 (CRON = 3.0 pts | NCRON = 1.0 pt | SOLIC = 0.0 pts | NMANU = -2.0 pts)"
                        ).classes("text-xs text-gray-400 mb-6")

                        d212 = res_data.get("2.1.2") or {}
                        raw_link_212 = str(d212.get("link") or "")

                        cron_p_i, ncron_p_i, solic_p_i, nmanu_p_i = 0, 0, 0, 0
                        evidencia_212 = raw_link_212

                        if "|LINK:" in raw_link_212:
                            partes_212, evidencia_212 = raw_link_212.split("|LINK:", 1)
                            m_cron = re.search(r"CRON:(\d+)", partes_212)
                            m_ncron = re.search(r"NCRON:(\d+)", partes_212)
                            m_solic = re.search(r"SOLIC:(\d+)", partes_212)
                            m_nmanu = re.search(r"NMANU:(\d+)", partes_212)

                            cron_p_i = int(m_cron.group(1)) if m_cron else 0
                            ncron_p_i = int(m_ncron.group(1)) if m_ncron else 0
                            solic_p_i = int(m_solic.group(1)) if m_solic else 0
                            nmanu_p_i = int(m_nmanu.group(1)) if m_nmanu else 0

                        state_212 = {
                            "cron": cron_p_i,
                            "ncron": ncron_p_i,
                            "solic": solic_p_i,
                            "nmanu": nmanu_p_i,
                            "link": evidencia_212,
                        }

                        def calc_pts_212():
                            c = int(state_212["cron"] or 0)
                            nc = int(state_212["ncron"] or 0)
                            s = int(state_212["solic"] or 0)
                            nm = int(state_212["nmanu"] or 0)

                            total_resp = c + nc + s + nm
                            if total_resp <= 0:
                                return 0.0

                            p1 = (nm / total_resp) * (-2.0)
                            p2 = (nc / total_resp) * 1.0
                            p3 = (c / total_resp) * 3.0

                            return p1 + p2 + p3

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_cron_p = ui.number("Possuem e CUMPRIRAM o cronograma (CRON):", value=cron_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_212, "cron")
                                inp_ncron_p = ui.number("Possuem e NÃO cumpriram o cronograma (NCRON):", value=ncron_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_212, "ncron")
                                inp_solic_p = ui.number("Manutenção SOMENTE por solicitação (SOLIC):", value=solic_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_212, "solic")
                                inp_nmanu_p = ui.number("NÃO realizam manutenção/troca (NMANU):", value=nmanu_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_212, "nmanu")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_212,
                                placeholder="Insira o cronograma de manutenção, ordens de serviço ou contratos...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_212, "link"
                            )

                        lbl_pts_212 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.1.2: {calc_pts_212():.2f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_212():
                            pts = calc_pts_212()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_212.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_212.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.1.2: {pts:.2f} pontos"
                            )

                        for inp in [inp_cron_p, inp_ncron_p, inp_solic_p, inp_nmanu_p]:
                            inp.on("update:model-value", att_pts_212)

                        def salvar_212():
                            c = int(state_212["cron"] or 0)
                            nc = int(state_212["ncron"] or 0)
                            s = int(state_212["solic"] or 0)
                            nm = int(state_212["nmanu"] or 0)

                            pts_finais = calc_pts_212()
                            composite = f"CRON:{c},NCRON:{nc},SOLIC:{s},NMANU:{nm}|LINK:{state_212['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.1.2",
                                valor=f"CRON:{c}/NCRON:{nc}/SOLIC:{s}/NMANU:{nm}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d212.get("comentarios", []),
                                status=d212.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.1.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.1.2", on_click=salvar_212).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.1.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.2 (Disponibilização de Brinquedos/Materiais em Pré-escolas)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.2 • Disponibilização de Brinquedos e Materiais Pedagógicos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura disponibiliza brinquedos/materiais pedagógicos para as crianças em TODOS os estabelecimentos de Pré-escola do município?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d22 = res_data.get("2.2") or {}
                        opcoes_22 = ["Selecione...", "Sim", "Não"]

                        val_22_bruto = str(d22.get("valor") or "")
                        val_22_valido = val_22_bruto if val_22_bruto in opcoes_22 else "Selecione..."
                        raw_link_22 = str(d22.get("link") or "")

                        state_22 = {
                            "opcao": val_22_valido,
                            "link": raw_link_22,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_22 = ui.radio(
                                options=opcoes_22,
                                value=state_22["opcao"],
                            ).props("color=blue").bind_value(state_22, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_22,
                                placeholder="Insira o inventário das escolas, atas de distribuição ou termo de entrega...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_22, "link"
                            )

                        def salvar_22():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.2",
                                valor=state_22["opcao"],
                                pontos=0.0,
                                link=state_22["link"],
                                comentarios=d22.get("comentarios", []),
                                status=d22.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.2", on_click=salvar_22).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.2", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 2.2.1 (Higienização dos Brinquedos/Materiais Pedagógicos)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.2.1 • Higienização dos Brinquedos e Materiais Pedagógicos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Realiza higienização dos brinquedos/materiais pedagógicos na Pré-escola?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito triagem.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d221 = res_data.get("2.2.1") or {}
                        opcoes_221 = ["Selecione...", "Sim", "Não"]

                        val_221_bruto = str(d221.get("valor") or "")
                        val_221_valido = val_221_bruto if val_221_bruto in opcoes_221 else "Selecione..."
                        raw_link_221 = str(d221.get("link") or "")

                        state_221 = {
                            "opcao": val_221_valido,
                            "link": raw_link_221,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_221 = ui.radio(
                                options=opcoes_221,
                                value=state_221["opcao"],
                            ).props("color=blue").bind_value(state_221, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_221,
                                placeholder="Insira o protocolo de higienização, regimento de limpeza ou orientações técnicas...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_221, "link"
                            )

                        def salvar_221():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.2.1",
                                valor=state_221["opcao"],
                                pontos=0.0,
                                link=state_221["link"],
                                comentarios=d221.get("comentarios", []),
                                status=d221.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.2.1", on_click=salvar_221).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.2.1.1 (Frequência de Higienização)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.2.1.1 • Frequência de Higienização dos Brinquedos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Qual a frequência de higienização aplicada na maior parte dos estabelecimentos de Pré-escola?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuação: Diária (5.0 pts) | A cada 2 dias (4.0 pts) | A cada 3 dias (3.0 pts) | Semanal (2.0 pts) | Mensal (1.0 pt) | > 30 dias (0.0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d2211 = res_data.get("2.2.1.1") or {}

                        opcoes_2211 = {
                            "Selecione...": 0.0,
                            "Diária (5,0 pontos)": 5.0,
                            "A cada 2 dias (4,0 pontos)": 4.0,
                            "A cada 3 dias (3,0 pontos)": 3.0,
                            "Semanal (2,0 pontos)": 2.0,
                            "Mensal (1,0 ponto)": 1.0,
                            "> 30 dias (0,0 pontos)": 0.0,
                        }

                        val_2211_bruto = str(d2211.get("valor") or "")
                        val_2211_valido = "Selecione..."
                        if val_2211_bruto in opcoes_2211:
                            val_2211_valido = val_2211_bruto
                        else:
                            for chave in opcoes_2211.keys():
                                if chave != "Selecione..." and chave.startswith(val_2211_bruto):
                                    val_2211_valido = chave
                                    break

                        raw_link_2211 = str(d2211.get("link") or "")

                        state_2211 = {
                            "opcao": val_2211_valido,
                            "link": raw_link_2211,
                        }

                        def calc_pts_2211():
                            return float(opcoes_2211.get(state_2211["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_2211 = ui.radio(
                                    options=list(opcoes_2211.keys()),
                                    value=state_2211["opcao"],
                                ).props("color=blue").bind_value(state_2211, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_2211,
                                placeholder="Insira a rotina de limpeza fixada nas creches/escolas ou escala do pessoal de apoio...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_2211, "link"
                            )

                        lbl_pts_2211 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.2.1.1: {calc_pts_2211():.1f} / 5.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_2211():
                            lbl_pts_2211.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.2.1.1: {calc_pts_2211():.1f} / 5.0 pontos"
                            )

                        rad_2211.on("update:model-value", att_pts_2211)

                        def salvar_2211():
                            pts = calc_pts_2211()
                            opt_sel = state_2211["opcao"]
                            lnk = state_2211["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.2.1.1",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d2211.get("comentarios", []),
                                status=d2211.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.2.1.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.2.1.1", on_click=salvar_2211).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.2.1.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.2.2 (Cronograma de Compra de Brinquedos/Materiais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.2.2 • Cronograma para Compra de Brinquedos/Materiais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Possui cronograma/planejamento para compra de brinquedos/materiais pedagógicos para cada estabelecimento de ensino?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuação: Sim = 5.0 pts | Não = 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d222 = res_data.get("2.2.2") or {}

                        opcoes_222 = {
                            "Selecione...": 0.0,
                            "Sim (5,0 pontos)": 5.0,
                            "Não (0,0 pontos)": 0.0,
                        }

                        val_222_bruto = str(d222.get("valor") or "")
                        val_222_valido = "Selecione..."
                        if val_222_bruto in opcoes_222:
                            val_222_valido = val_222_bruto
                        else:
                            for chave in opcoes_222.keys():
                                if chave != "Selecione..." and chave.startswith(val_222_bruto):
                                    val_222_valido = chave
                                    break

                        raw_link_222 = str(d222.get("link") or "")

                        state_222 = {
                            "opcao": val_222_valido,
                            "link": raw_link_222,
                        }

                        def calc_pts_222():
                            return float(opcoes_222.get(state_222["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_222 = ui.radio(
                                    options=list(opcoes_222.keys()),
                                    value=state_222["opcao"],
                                ).props("color=blue").bind_value(state_222, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_222,
                                placeholder="Insira o plano anual de compras, cronograma físico-financeiro ou processo licitatório...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_222, "link"
                            )

                        lbl_pts_222 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.2.2: {calc_pts_222():.1f} / 5.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_222():
                            lbl_pts_222.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.2.2: {calc_pts_222():.1f} / 5.0 pontos"
                            )

                        rad_222.on("update:model-value", att_pts_222)

                        def salvar_222():
                            pts = calc_pts_222()
                            opt_sel = state_222["opcao"]
                            lnk = state_222["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.2.2",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d222.get("comentarios", []),
                                status=d222.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.2.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.2.2", on_click=salvar_222).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.2.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.2.3 (Data da Última Entrega em Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.2.3 • Última Entrega de Brinquedos/Materiais na Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega de brinquedos e/ou materiais pedagógicos na Pré-escola:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d223 = res_data.get("2.2.3") or {}
                        raw_link_223 = str(d223.get("link") or "")
                        data_val_223 = str(d223.get("valor") or "")
                        evidencia_223 = raw_link_223

                        state_223 = {
                            "data": data_val_223,
                            "link": evidencia_223,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_data_223 = (
                                    ui.input(
                                        label="Data da Entrega (DD/MM/AAAA):",
                                        value=data_val_223,
                                        placeholder="Ex: 20/02/2025",
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_223, "data")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_223,
                                placeholder="Insira o comprovante de recebimento, guia de transporte ou nota fiscal...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_223, "link"
                            )

                        def salvar_223():
                            dt_val = state_223["data"].strip()
                            save_resposta(
                                ano=ano_sel,
                                qid="2.2.3",
                                valor=dt_val,
                                pontos=0.0,
                                link=state_223["link"],
                                comentarios=d223.get("comentarios", []),
                                status=d223.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.2.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.2.3", on_click=salvar_223).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.2.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.3 (Espaço por Aluno em Sala de Aula - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.3 • Espaço por Aluno em Sala de Aula (Pré-escola)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de turmas de Pré-escola em cada faixa de área por aluno (m²):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 10.0 × (1.0×P1 + 0.5×P2 + 0.25×P3 + 0×P4) | Pmáx = 10.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d23 = res_data.get("2.3") or {}
                        raw_link_23 = str(d23.get("link") or "")

                        pf1_i, pf2_i, pf3_i, pf4_i = 0, 0, 0, 0
                        evidencia_23 = raw_link_23

                        if "|LINK:" in raw_link_23:
                            partes_23, evidencia_23 = raw_link_23.split("|LINK:", 1)
                            m_pf1 = re.search(r"F1:(\d+)", partes_23)
                            m_pf2 = re.search(r"F2:(\d+)", partes_23)
                            m_pf3 = re.search(r"F3:(\d+)", partes_23)
                            m_pf4 = re.search(r"F4:(\d+)", partes_23)
                            pf1_i = int(m_pf1.group(1)) if m_pf1 else 0
                            pf2_i = int(m_pf2.group(1)) if m_pf2 else 0
                            pf3_i = int(m_pf3.group(1)) if m_pf3 else 0
                            pf4_i = int(m_pf4.group(1)) if m_pf4 else 0

                        state_23 = {
                            "f1": pf1_i,
                            "f2": pf2_i,
                            "f3": pf3_i,
                            "f4": pf4_i,
                            "link": evidencia_23,
                        }

                        def calc_pts_23():
                            c1 = int(state_23["f1"] or 0)
                            c2 = int(state_23["f2"] or 0)
                            c3 = int(state_23["f3"] or 0)
                            c4 = int(state_23["f4"] or 0)
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

                            return min(10.0 * (n1 + n2 + n3 + n4), 10.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_pf1 = ui.number("Turmas com área ≥ 1,36 m²/aluno:", value=pf1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_23, "f1")
                                inp_pf2 = ui.number("Turmas com área ≥ 1,10 m² e < 1,36 m²/aluno:", value=pf2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_23, "f2")
                                inp_pf3 = ui.number("Turmas com área ≥ 0,90 m² e < 1,10 m²/aluno:", value=pf3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_23, "f3")
                                inp_pf4 = ui.number("Turmas com área < 0,90 m²/aluno:", value=pf4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_23, "f4")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_23,
                                placeholder="Insira o laudo metrológico das salas, medição física ou relatório técnico...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_23, "link"
                            )

                        lbl_pts_23 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.3: {calc_pts_23():.2f} / 10.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_23():
                            lbl_pts_23.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.3: {calc_pts_23():.2f} / 10.0 pontos"
                            )

                        for inp in [inp_pf1, inp_pf2, inp_pf3, inp_pf4]:
                            inp.on("update:model-value", att_pts_23)

                        def salvar_23():
                            c1, c2, c3, c4 = state_23["f1"], state_23["f2"], state_23["f3"], state_23["f4"]
                            pts_finais = calc_pts_23()
                            composite = f"F1:{c1},F2:{c2},F3:{c3},F4:{c4}|LINK:{state_23['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.3",
                                valor=f"F1:{c1}/F2:{c2}/F3:{c3}/F4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d23.get("comentarios", []),
                                status=d23.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.3", on_click=salvar_23).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.4 (Formação e Pós-Graduação dos Professores de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.4 • Titulação e Formação de Professores de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de professores de Pré-escola e a quantidade com Licenciatura e Pós-Graduação:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = N1 (Graduação - máx 11 pts) + N2 (Pós-Graduação - máx 7 pts) | Pmáx = 18 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d24 = res_data.get("2.4") or {}
                        raw_link_24 = str(d24.get("link") or "")

                        tot_prof_p_i, grad_p_i, pgrad_p_i = 0, 0, 0
                        evidencia_24 = raw_link_24

                        if "|LINK:" in raw_link_24:
                            partes_24, evidencia_24 = raw_link_24.split("|LINK:", 1)
                            m_tot = re.search(r"TOT:(\d+)", partes_24)
                            m_grad = re.search(r"GRAD:(\d+)", partes_24)
                            m_pgrad = re.search(r"PGRAD:(\d+)", partes_24)
                            tot_prof_p_i = int(m_tot.group(1)) if m_tot else 0
                            grad_p_i = int(m_grad.group(1)) if m_grad else 0
                            pgrad_p_i = int(m_pgrad.group(1)) if m_pgrad else 0

                        state_24 = {
                            "tot": tot_prof_p_i,
                            "grad": grad_p_i,
                            "pgrad": pgrad_p_i,
                            "link": evidencia_24,
                        }

                        def calc_pts_24():
                            tot = int(state_24["tot"] or 0)
                            grad = int(state_24["grad"] or 0)
                            pgrad = int(state_24["pgrad"] or 0)
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
                                inp_tot_24 = ui.number("TOTAL de professores da etapa (Efetivos + Temporários):", value=tot_prof_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_24, "tot")
                                inp_grad_p = ui.number("Professores com Nível Superior / Licenciatura (GRAD):", value=grad_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_24, "grad")
                                inp_pgrad_p = ui.number("Professores com Pós-Graduação (PGRAD):", value=pgrad_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_24, "pgrad")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_24,
                                placeholder="Insira os relatórios do Censo Escolar 2025 ou relação de professores da pré-escola...",
                            ).classes("w-full").props("outlined rows=8").bind_value(
                                state_24, "link"
                            )

                        lbl_pts_24 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.4: {calc_pts_24():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_24():
                            lbl_pts_24.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.4: {calc_pts_24():.1f} / 18.0 pontos"
                            )

                        inp_tot_24.on("update:model-value", att_pts_24)
                        inp_grad_p.on("update:model-value", att_pts_24)
                        inp_pgrad_p.on("update:model-value", att_pts_24)

                        def salvar_24():
                            tot = int(state_24["tot"] or 0)
                            g = int(state_24["grad"] or 0)
                            pg = int(state_24["pgrad"] or 0)
                            pts_finais = calc_pts_24()
                            composite = f"TOT:{tot},GRAD:{g},PGRAD:{pg}|LINK:{state_24['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.4",
                                valor=f"GRAD:{g}/PGRAD:{pg}/TOT:{tot}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d24.get("comentarios", []),
                                status=d24.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.4", on_click=salvar_24).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.4", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 2.5 (Piso Salarial Mensal dos Professores de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.5 • Piso Salarial Mensal dos Professores de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o piso salarial mensal dos professores de Pré-escola (base para 40h semanais):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se piso < Salário Mínimo Nacional → Perde 20.0 pts (-20.0) | Se piso ≥ Salário Mínimo → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d25 = res_data.get("2.5") or {}
                        raw_link_25 = str(d25.get("link") or "")

                        piso_pre_i = 0.0
                        sal_min_i = 1412.0
                        evidencia_25 = raw_link_25

                        if "|LINK:" in raw_link_25:
                            partes_25, evidencia_25 = raw_link_25.split("|LINK:", 1)
                            m_piso = re.search(r"PISO:([\d\.]+)", partes_25)
                            m_smin = re.search(r"SMIN:([\d\.]+)", partes_25)
                            piso_pre_i = float(m_piso.group(1)) if m_piso else 0.0
                            sal_min_i = float(m_smin.group(1)) if m_smin else 1412.0
                        elif d25.get("valor"):
                            try:
                                piso_pre_i = float(d25.get("valor"))
                            except (ValueError, TypeError):
                                piso_pre_i = 0.0

                        state_25 = {
                            "piso": piso_pre_i,
                            "smin": sal_min_i,
                            "link": evidencia_25,
                        }

                        def calc_pts_25():
                            piso = float(state_25["piso"] or 0.0)
                            smin = float(state_25["smin"] or 0.0)
                            if piso <= 0:
                                return 0.0
                            return -20.0 if piso < smin else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_piso_25 = (
                                    ui.number(
                                        "Piso Salarial do Professor (40h) - R$:",
                                        value=piso_pre_i,
                                        min=0,
                                        step=0.01,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue prefix=R$")
                                    .bind_value(state_25, "piso")
                                )

                                inp_smin_25 = (
                                    ui.number(
                                        "Salário Mínimo Nacional de Referência - R$:",
                                        value=sal_min_i,
                                        min=0,
                                        step=0.01,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue prefix=R$")
                                    .bind_value(state_25, "smin")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_25,
                                placeholder="Insira a Lei Municipal, Plano de Cargos e Salários ou Holerite modelo...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_25, "link"
                            )

                        lbl_pts_25 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.5: {calc_pts_25():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_25():
                            pts = calc_pts_25()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_25.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_25.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.5: {pts:.1f} pontos"
                            )

                        inp_piso_25.on("update:model-value", att_pts_25)
                        inp_smin_25.on("update:model-value", att_pts_25)

                        def salvar_25():
                            p_val = float(state_25["piso"] or 0.0)
                            sm_val = float(state_25["smin"] or 0.0)
                            pts_finais = calc_pts_25()
                            composite = f"PISO:{p_val},SMIN:{sm_val}|LINK:{state_25['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.5",
                                valor=f"R$ {p_val:.2f}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d25.get("comentarios", []),
                                status=d25.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.5", on_click=salvar_25).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.6 (Ausência dos Professores por Faltas - Pré-escola QTA)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.6 • Dias de Ausência de Professores de Pré-escola (QTA)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de dias de ausência dos professores de Pré-escola por categoria (ano de 2025):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório / levantamento de absenteísmo."
                        ).classes("text-xs text-gray-400 mb-6")

                        d26 = res_data.get("2.6") or {}
                        raw_link_26 = str(d26.get("link") or "")

                        finj_p_i, fjus_p_i, lmed_p_i, lmat_p_i, abo_p_i, out_p_i = 0, 0, 0, 0, 0, 0
                        evidencia_26 = raw_link_26

                        if "|LINK:" in raw_link_26:
                            partes_26, evidencia_26 = raw_link_26.split("|LINK:", 1)
                            m_inj = re.search(r"INJ:(\d+)", partes_26)
                            m_jus = re.search(r"JUS:(\d+)", partes_26)
                            m_med = re.search(r"MED:(\d+)", partes_26)
                            m_mat = re.search(r"MAT:(\d+)", partes_26)
                            m_abo = re.search(r"ABO:(\d+)", partes_26)
                            m_out = re.search(r"OUT:(\d+)", partes_26)

                            finj_p_i = int(m_inj.group(1)) if m_inj else 0
                            fjus_p_i = int(m_jus.group(1)) if m_jus else 0
                            lmed_p_i = int(m_med.group(1)) if m_med else 0
                            lmat_p_i = int(m_mat.group(1)) if m_mat else 0
                            abo_p_i = int(m_abo.group(1)) if m_abo else 0
                            out_p_i = int(m_out.group(1)) if m_out else 0

                        state_26 = {
                            "inj": finj_p_i,
                            "jus": fjus_p_i,
                            "med": lmed_p_i,
                            "mat": lmat_p_i,
                            "abo": abo_p_i,
                            "out": out_p_i,
                            "link": evidencia_26,
                        }

                        def calc_tot_ausencias_26():
                            return (
                                int(state_26["inj"] or 0)
                                + int(state_26["jus"] or 0)
                                + int(state_26["med"] or 0)
                                + int(state_26["mat"] or 0)
                                + int(state_26["abo"] or 0)
                                + int(state_26["out"] or 0)
                            )

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_inj_26 = ui.number("Faltas injustificadas (dias):", value=finj_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "inj")
                                inp_jus_26 = ui.number("Faltas justificadas (dias):", value=fjus_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "jus")
                                inp_med_26 = ui.number("Licença médica (dias):", value=lmed_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "med")
                                inp_mat_26 = ui.number("Licença maternidade/paternidade (dias):", value=lmat_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "mat")
                                inp_abo_26 = ui.number("Abonos (dias):", value=abo_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "abo")
                                inp_out_26 = ui.number("Outros / ausências amparadas por lei (dias):", value=out_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_26, "out")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_26,
                                placeholder="Insira o relatório de frequência, sistema de RH ou espelho de ponto...",
                            ).classes("w-full").props("outlined rows=12").bind_value(
                                state_26, "link"
                            )

                        lbl_tot_26 = ui.label(
                            f"📊 Total de Dias de Ausência Registrados: {calc_tot_ausencias_26()} dias"
                        ).classes("text-sm font-bold text-blue-600 my-4")

                        def att_tot_26():
                            lbl_tot_26.set_text(
                                f"📊 Total de Dias de Ausência Registrados: {calc_tot_ausencias_26()} dias"
                            )

                        for inp in [inp_inj_26, inp_jus_26, inp_med_26, inp_mat_26, inp_abo_26, inp_out_26]:
                            inp.on("update:model-value", att_tot_26)

                        def salvar_26():
                            tot = calc_tot_ausencias_26()
                            inj, jus = state_26["inj"], state_26["jus"]
                            med, mat = state_26["med"], state_26["mat"]
                            abo, out = state_26["abo"], state_26["out"]

                            composite = f"INJ:{inj},JUS:{jus},MED:{med},MAT:{mat},ABO:{abo},OUT:{out}|LINK:{state_26['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.6",
                                valor=f"Total: {tot} dias",
                                pontos=0.0,
                                link=composite,
                                comentarios=d26.get("comentarios", []),
                                status=d26.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.6", on_click=salvar_26).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.6", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.7 (Participação em Capacitação - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.7 • Capacitação dos Profissionais de Pré-escola em 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os profissionais de Pré-escola da rede municipal participaram de cursos de capacitação durante o ano de 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito triagem.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d27 = res_data.get("2.7") or {}
                        opcoes_27 = ["Selecione...", "Sim", "Não"]

                        val_27_bruto = str(d27.get("valor") or "")
                        val_27_valido = val_27_bruto if val_27_bruto in opcoes_27 else "Selecione..."
                        raw_link_27 = str(d27.get("link") or "")

                        state_27 = {
                            "opcao": val_27_valido,
                            "link": raw_link_27,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_27 = ui.radio(
                                options=opcoes_27,
                                value=state_27["opcao"],
                            ).props("color=blue").bind_value(state_27, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_27,
                                placeholder="Insira o plano de formação anual, declarações ou relatórios da secretaria...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_27, "link"
                            )

                        def salvar_27():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.7",
                                valor=state_27["opcao"],
                                pontos=0.0,
                                link=state_27["link"],
                                comentarios=d27.get("comentarios", []),
                                status=d27.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.7 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.7", on_click=salvar_27).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.7", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.7.1 (Quantidade e Cálculo de Profissionais Capacitados - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.7.1 • Proporção de Profissionais de Pré-escola Capacitados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de profissionais de Pré-escola capacitados e o total do quadro em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: PC = (Prof. Capacitados + Apoio Capacitados + Gestores Capacitados) / (Total Geral) | PC = 100%: 7 pts | 70% ≤ PC < 100%: 5 pts | 50% ≤ PC < 70%: 3 pts"
                        ).classes("text-xs text-gray-400 mb-6")

                        d271 = res_data.get("2.7.1") or {}
                        raw_link_271 = str(d271.get("link") or "")

                        prof_cap_p_i, apoio_cap_p_i, gest_cap_p_i = 0, 0, 0
                        tot_prof_p_i, tot_apoio_p_i, tot_gest_p_i = 0, 0, 0
                        evidencia_271 = raw_link_271

                        if "|LINK:" in raw_link_271:
                            partes_271, evidencia_271 = raw_link_271.split("|LINK:", 1)
                            m_pcap = re.search(r"PCAP:(\d+)", partes_271)
                            m_acap = re.search(r"ACAP:(\d+)", partes_271)
                            m_gcap = re.search(r"GCAP:(\d+)", partes_271)
                            m_tprof = re.search(r"TPROF:(\d+)", partes_271)
                            m_tapoi = re.search(r"TAPOI:(\d+)", partes_271)
                            m_tgest = re.search(r"TGEST:(\d+)", partes_271)

                            prof_cap_p_i = int(m_pcap.group(1)) if m_pcap else 0
                            apoio_cap_p_i = int(m_acap.group(1)) if m_acap else 0
                            gest_cap_p_i = int(m_gcap.group(1)) if m_gcap else 0
                            tot_prof_p_i = int(m_tprof.group(1)) if m_tprof else 0
                            tot_apoio_p_i = int(m_tapoi.group(1)) if m_tapoi else 0
                            tot_gest_p_i = int(m_tgest.group(1)) if m_tgest else 0

                        state_271 = {
                            "prof_cap": prof_cap_p_i,
                            "apoio_cap": apoio_cap_p_i,
                            "gest_cap": gest_cap_p_i,
                            "tot_prof": tot_prof_p_i,
                            "tot_apoio": tot_apoio_p_i,
                            "tot_gest": tot_gest_p_i,
                            "link": evidencia_271,
                        }

                        def calc_pts_271():
                            num = int(state_271["prof_cap"] or 0) + int(state_271["apoio_cap"] or 0) + int(state_271["gest_cap"] or 0)
                            den = int(state_271["tot_prof"] or 0) + int(state_271["tot_apoio"] or 0) + int(state_271["tot_gest"] or 0)

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
                                inp_pcap_27 = ui.number("Professores regentes capacitados:", value=prof_cap_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "prof_cap")
                                inp_acap_27 = ui.number("Profissionais de apoio/supervisão capacitados:", value=apoio_cap_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "apoio_cap")
                                inp_gcap_27 = ui.number("Gestores escolares capacitados:", value=gest_cap_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "gest_cap")

                                ui.label("QUADRO TOTAL (ETAPA PRÉ-ESCOLA):").classes("font-bold text-xs text-blue-800 uppercase mt-2")
                                inp_tprof_27 = ui.number("TOTAL de professores regentes:", value=tot_prof_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "tot_prof")
                                inp_tapoi_27 = ui.number("TOTAL de profissionais de apoio/supervisão:", value=tot_apoio_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "tot_apoio")
                                inp_tgest_27 = ui.number("TOTAL de gestores escolares de pré-escola:", value=tot_gest_p_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_271, "tot_gest")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_271,
                                placeholder="Insira as listas de presença, certificados ou relatórios de capacitação...",
                            ).classes("w-full").props("outlined rows=14").bind_value(
                                state_271, "link"
                            )

                        lbl_pts_271 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.7.1: {calc_pts_271():.1f} / 7.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_271():
                            lbl_pts_271.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.7.1: {calc_pts_271():.1f} / 7.0 pontos"
                            )

                        for inp in [inp_pcap_27, inp_acap_27, inp_gcap_27, inp_tprof_27, inp_tapoi_27, inp_tgest_27]:
                            inp.on("update:model-value", att_pts_271)

                        def salvar_271():
                            p_cap, a_cap, g_cap = state_271["prof_cap"], state_271["apoio_cap"], state_271["gest_cap"]
                            t_prof, t_apoi, t_gest = state_271["tot_prof"], state_271["tot_apoio"], state_271["tot_gest"]
                            pts_finais = calc_pts_271()

                            composite = (
                                f"PCAP:{p_cap},ACAP:{a_cap},GCAP:{g_cap},"
                                f"TPROF:{t_prof},TAPOI:{t_apoi},TGEST:{t_gest}|LINK:{state_271['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="2.7.1",
                                valor=f"Capacitados: {p_cap + a_cap + g_cap} / Total: {t_prof + t_apoi + t_gest}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d271.get("comentarios", []),
                                status=d271.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.7.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.7.1", on_click=salvar_271).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.7.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.7.2 (Formas de Capacitação Oferecidas em Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.7.2 • Formas de Capacitação Oferecidas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as modalidades de capacitação adotadas:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito qualitativo de seleção múltipla.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d272 = res_data.get("2.7.2") or {}
                        raw_link_272 = str(d272.get("link") or "")
                        val_272_bruto = d272.get("valor") or []

                        if isinstance(val_272_bruto, str):
                            try:
                                sel_272 = json.loads(val_272_bruto)
                            except Exception:
                                sel_272 = [val_272_bruto]
                        elif isinstance(val_272_bruto, list):
                            sel_272 = val_272_bruto
                        else:
                            sel_272 = []

                        state_272 = {
                            "opcoes": sel_272,
                            "link": raw_link_272,
                        }

                        opcoes_272 = [
                            "Presencialmente",
                            "À distância/remotamente",
                            "Por meio de multiplicadores",
                            "Outros",
                        ]

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("gap-2 w-full"):
                                def make_chk_272(opt_text):
                                    def on_chk_change(e):
                                        if e.value and opt_text not in state_272["opcoes"]:
                                            state_272["opcoes"].append(opt_text)
                                        elif not e.value and opt_text in state_272["opcoes"]:
                                            state_272["opcoes"].remove(opt_text)
                                    return on_chk_change

                                for opt in opcoes_272:
                                    ui.checkbox(
                                        text=opt,
                                        value=(opt in state_272["opcoes"]),
                                        on_change=make_chk_272(opt),
                                    ).props("color=blue")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_272,
                                placeholder="Insira o plano de formação, contratos das plataformas de EAD ou relatórios...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_272, "link"
                            )

                        def salvar_272():
                            opts_sel = state_272["opcoes"]
                            save_resposta(
                                ano=ano_sel,
                                qid="2.7.2",
                                valor=opts_sel,
                                pontos=0.0,
                                link=state_272["link"],
                                comentarios=d272.get("comentarios", []),
                                status=d272.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.7.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.7.2", on_click=salvar_272).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.7.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.8 (Rotatividade do Corpo Docente em Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.8 • Rotatividade de Professores de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de escolas em cada faixa de rotatividade de professores de Pré-escola:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 3.0 × (3×Q1 + 2×Q2 + 1×Q3 + 0×Q4) | Pmáx = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d28 = res_data.get("2.8") or {}
                        raw_link_28 = str(d28.get("link") or "")

                        pq1_i, pq2_i, pq3_i, pq4_i = 0, 0, 0, 0
                        evidencia_28 = raw_link_28

                        if "|LINK:" in raw_link_28:
                            partes_28, evidencia_28 = raw_link_28.split("|LINK:", 1)
                            m_q1 = re.search(r"Q1:(\d+)", partes_28)
                            m_q2 = re.search(r"Q2:(\d+)", partes_28)
                            m_q3 = re.search(r"Q3:(\d+)", partes_28)
                            m_q4 = re.search(r"Q4:(\d+)", partes_28)
                            pq1_i = int(m_q1.group(1)) if m_q1 else 0
                            pq2_i = int(m_q2.group(1)) if m_q2 else 0
                            pq3_i = int(m_q3.group(1)) if m_q3 else 0
                            pq4_i = int(m_q4.group(1)) if m_q4 else 0

                        state_28 = {
                            "q1": pq1_i,
                            "q2": pq2_i,
                            "q3": pq3_i,
                            "q4": pq4_i,
                            "link": evidencia_28,
                        }

                        def calc_pts_28():
                            c1 = int(state_28["q1"] or 0)
                            c2 = int(state_28["q2"] or 0)
                            c3 = int(state_28["q3"] or 0)
                            c4 = int(state_28["q4"] or 0)
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
                                inp_pq1 = (
                                    ui.number(
                                        "Escolas com rotatividade MENOR que 20%:",
                                        value=pq1_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_28, "q1")
                                )

                                inp_pq2 = (
                                    ui.number(
                                        "Escolas com rotatividade entre 20% e 29,9%:",
                                        value=pq2_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_28, "q2")
                                )

                                inp_pq3 = (
                                    ui.number(
                                        "Escolas com rotatividade entre 30% e 39,9%:",
                                        value=pq3_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_28, "q3")
                                )

                                inp_pq4 = (
                                    ui.number(
                                        "Escolas com rotatividade MAIOR ou igual a 40%:",
                                        value=pq4_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_28, "q4")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_28,
                                placeholder="Insira relatórios de atribuição de aulas, remoção de docentes ou folhas de ponto...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_28, "link"
                            )

                        lbl_pts_28 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.8: {calc_pts_28():.2f} / 3.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_28():
                            lbl_pts_28.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.8: {calc_pts_28():.2f} / 3.0 pontos"
                            )

                        inp_pq1.on("update:model-value", att_pts_28)
                        inp_pq2.on("update:model-value", att_pts_28)
                        inp_pq3.on("update:model-value", att_pts_28)
                        inp_pq4.on("update:model-value", att_pts_28)

                        def salvar_28():
                            c1 = int(state_28["q1"] or 0)
                            c2 = int(state_28["q2"] or 0)
                            c3 = int(state_28["q3"] or 0)
                            c4 = int(state_28["q4"] or 0)
                            pts_finais = calc_pts_28()
                            composite = f"Q1:{c1},Q2:{c2},Q3:{c3},Q4:{c4}|LINK:{state_28['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.8",
                                valor=f"Q1:{c1}/Q2:{c2}/Q3:{c3}/Q4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d28.get("comentarios", []),
                                status=d28.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.8 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.8", on_click=salvar_28).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.8", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 2.9 (Regularidade e Permanência dos Gestores de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.9 • Regularidade e Permanência dos Gestores de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Indique a quantidade de escolas por tempo de permanência do diretor/gestor de Pré-escola (ao final de 2025):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 0×Q1 + 0.5×Q2 + 1.0×Q3 + 1.5×Q4 + 1.75×Q5 + 2.0×Q6 | Pmáx = 2.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d29 = res_data.get("2.9") or {}
                        raw_link_29 = str(d29.get("link") or "")

                        pg1_i, pg2_i, pg3_i, pg4_i, pg5_i, pg6_i = 0, 0, 0, 0, 0, 0
                        evidencia_29 = raw_link_29

                        if "|LINK:" in raw_link_29:
                            partes_29, evidencia_29 = raw_link_29.split("|LINK:", 1)
                            m_g1 = re.search(r"G1:(\d+)", partes_29)
                            m_g2 = re.search(r"G2:(\d+)", partes_29)
                            m_g3 = re.search(r"G3:(\d+)", partes_29)
                            m_g4 = re.search(r"G4:(\d+)", partes_29)
                            m_g5 = re.search(r"G5:(\d+)", partes_29)
                            m_g6 = re.search(r"G6:(\d+)", partes_29)

                            pg1_i = int(m_g1.group(1)) if m_g1 else 0
                            pg2_i = int(m_g2.group(1)) if m_g2 else 0
                            pg3_i = int(m_g3.group(1)) if m_g3 else 0
                            pg4_i = int(m_g4.group(1)) if m_g4 else 0
                            pg5_i = int(m_g5.group(1)) if m_g5 else 0
                            pg6_i = int(m_g6.group(1)) if m_g6 else 0

                        state_29 = {
                            "g1": pg1_i,
                            "g2": pg2_i,
                            "g3": pg3_i,
                            "g4": pg4_i,
                            "g5": pg5_i,
                            "g6": pg6_i,
                            "link": evidencia_29,
                        }

                        def calc_pts_29():
                            c1 = int(state_29["g1"] or 0)
                            c2 = int(state_29["g2"] or 0)
                            c3 = int(state_29["g3"] or 0)
                            c4 = int(state_29["g4"] or 0)
                            c5 = int(state_29["g5"] or 0)
                            c6 = int(state_29["g6"] or 0)

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
                                inp_pg1 = ui.number("Menor que 1 ano:", value=pg1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g1")
                                inp_pg2 = ui.number("De 1 ano a 2,9 anos:", value=pg2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g2")
                                inp_pg3 = ui.number("De 3 anos a 4,9 anos:", value=pg3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g3")
                                inp_pg4 = ui.number("De 5 anos a 9,9 anos:", value=pg4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g4")
                                inp_pg5 = ui.number("De 10 anos a 14,9 anos:", value=pg5_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g5")
                                inp_pg6 = ui.number("Maior ou igual a 15 anos:", value=pg6_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_29, "g6")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_29,
                                placeholder="Insira o histórico funcional dos gestores, atos de nomeação ou portarias...",
                            ).classes("w-full").props("outlined rows=12").bind_value(
                                state_29, "link"
                            )

                        lbl_pts_29 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.9: {calc_pts_29():.2f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_29():
                            lbl_pts_29.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.9: {calc_pts_29():.2f} / 2.0 pontos"
                            )

                        for inp in [inp_pg1, inp_pg2, inp_pg3, inp_pg4, inp_pg5, inp_pg6]:
                            inp.on("update:model-value", att_pts_29)

                        def salvar_29():
                            c1, c2, c3 = state_29["g1"], state_29["g2"], state_29["g3"]
                            c4, c5, c6 = state_29["g4"], state_29["g5"], state_29["g6"]
                            pts_finais = calc_pts_29()

                            composite = f"G1:{c1},G2:{c2},G3:{c3},G4:{c4},G5:{c5},G6:{c6}|LINK:{state_29['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.9",
                                valor=f"G1:{c1}/G2:{c2}/G3:{c3}/G4:{c4}/G5:{c5}/G6:{c6}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d29.get("comentarios", []),
                                status=d29.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.9 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.9", on_click=salvar_29).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.9", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.10 (Reuniões Periódicas com Pais - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.10 • Reuniões Periódicas com Pais na Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os professores realizam reuniões periódicas com os pais dos alunos de Pré-escola sobre planejamento/projeto escolar e desempenho/desenvolvimento da criança?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Selecione uma das opções abaixo para registrar a pontuação."
                        ).classes("text-xs text-gray-400 mb-6")

                        d210 = res_data.get("2.10") or {}

                        opcoes_210 = {
                            "Selecione...": 0.0,
                            "Sobre planejamento e desempenho da criança (2,0 pontos)": 2.0,
                            "Apenas sobre o projeto político-pedagógico (1,5 pontos)": 1.5,
                            "Apenas sobre o desempenho da criança (1,0 ponto)": 1.0,
                            "Não realiza reuniões periódicas (0,0 pontos)": 0.0,
                        }

                        val_210_bruto = str(d210.get("valor") or "")
                        val_210_valido = "Selecione..."
                        if val_210_bruto in opcoes_210:
                            val_210_valido = val_210_bruto
                        else:
                            for chave in opcoes_210.keys():
                                if chave != "Selecione..." and chave.startswith(val_210_bruto):
                                    val_210_valido = chave
                                    break

                        raw_link_210 = str(d210.get("link") or "")

                        state_210 = {
                            "escopo": val_210_valido,
                            "link": raw_link_210,
                        }

                        def calc_pts_210():
                            return float(opcoes_210.get(state_210["escopo"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_210 = ui.radio(
                                    options=list(opcoes_210.keys()),
                                    value=state_210["escopo"],
                                ).props("color=blue").bind_value(state_210, "escopo")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_210,
                                placeholder="Insira atas de reunião com pais, calendário escolar ou convocatórias...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_210, "link"
                            )

                        lbl_pts_210 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.10: {calc_pts_210():.1f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_210():
                            lbl_pts_210.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.10: {calc_pts_210():.1f} / 2.0 pontos"
                            )

                        rad_210.on("update:model-value", att_pts_210)

                        def salvar_210():
                            pts = calc_pts_210()
                            escopo_sel = state_210["escopo"]
                            lnk = state_210["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.10",
                                valor=escopo_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d210.get("comentarios", []),
                                status=d210.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.10 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.10", on_click=salvar_210).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.10", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.10.1 (Periodicidade das Reuniões com Pais - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.10.1 • Periodicidade das Reuniões na Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Qual a periodicidade das reuniões com os pais na Pré-escola?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d2101 = res_data.get("2.10.1") or {}

                        opcoes_2101 = [
                            "Selecione...",
                            "Mensal",
                            "Bimestral",
                            "Trimestral",
                            "Quadrimestral",
                            "Semestral",
                            "Anual",
                        ]

                        val_2101_bruto = str(d2101.get("valor") or "")
                        val_2101_valido = (
                            val_2101_bruto if val_2101_bruto in opcoes_2101 else "Selecione..."
                        )

                        raw_link_2101 = str(d2101.get("link") or "")

                        state_2101 = {
                            "periodicidade": val_2101_valido,
                            "link": raw_link_2101,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_2101 = ui.radio(
                                    options=opcoes_2101,
                                    value=state_2101["periodicidade"],
                                ).props("color=blue").bind_value(state_2101, "periodicidade")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_2101,
                                placeholder="Insira o calendário escolar ou regulamento da unidade...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_2101, "link"
                            )

                        def salvar_2101():
                            per_sel = state_2101["periodicidade"]
                            lnk = state_2101["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.10.1",
                                valor=per_sel,
                                pontos=0.0,
                                link=lnk,
                                comentarios=d2101.get("comentarios", []),
                                status=d2101.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.10.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.10.1", on_click=salvar_2101).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.10.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.11 (Entrega do Kit Escolar às Pré-escolas em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.11 • Entrega do Kit Escolar às Pré-escolas em 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do Kit escolar às Pré-Escolas municipais em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Kit escolar = material escolar e pedagógico."
                        ).classes("text-xs text-gray-400 mb-6")

                        d211 = res_data.get("2.11") or {}

                        opcoes_211 = {
                            "Selecione...": 0.0,
                            "Sim (0,0 pontos)": 0.0,
                            "O kit escolar permanece no almoxarifado da escola e é retirado no momento do uso pelos alunos (18,0 pontos)": 18.0,
                            "Não (0,0 pontos)": 0.0,
                        }

                        val_211_bruto = str(d211.get("valor") or "")
                        val_211_valido = "Selecione..."
                        if val_211_bruto in opcoes_211:
                            val_211_valido = val_211_bruto
                        else:
                            for chave in opcoes_211.keys():
                                if chave != "Selecione..." and chave.startswith(val_211_bruto):
                                    val_211_valido = chave
                                    break

                        raw_link_211_m = str(d211.get("link") or "")

                        state_211_m = {
                            "opcao": val_211_valido,
                            "link": raw_link_211_m,
                        }

                        def calc_pts_211_m():
                            return float(opcoes_211.get(state_211_m["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_211_m = ui.radio(
                                    options=list(opcoes_211.keys()),
                                    value=state_211_m["opcao"],
                                ).props("color=blue").bind_value(state_211_m, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_211_m,
                                placeholder="Insira o comprovante de distribuição, fotos ou termo de entrega...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_211_m, "link"
                            )

                        lbl_pts_211_m = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.11: {calc_pts_211_m():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_211_m():
                            lbl_pts_211_m.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.11: {calc_pts_211_m():.1f} / 18.0 pontos"
                            )

                        rad_211_m.on("update:model-value", att_pts_211_m)

                        def salvar_211_m():
                            pts = calc_pts_211_m()
                            opt_sel = state_211_m["opcao"]
                            lnk = state_211_m["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.11",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d211.get("comentarios", []),
                                status=d211.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.11 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.11", on_click=salvar_211_m).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.11", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.11.1 (Data da Última Entrega do Kit Escolar - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.11.1 • Data da Última Entrega do Kit Escolar na Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega e a data de início das aulas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: ≤ Início Aulas = 18.0 pts | < Início + 15 dias = 9.0 pts | ≥ Início + 15 dias = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d2111 = res_data.get("2.11.1") or {}
                        raw_link_2111 = str(d2111.get("link") or "")

                        dt_entrega_p_i, dt_inicio_p_i = "", "05/02/2025"
                        evidencia_2111 = raw_link_2111

                        if "|LINK:" in raw_link_2111:
                            partes_2111, evidencia_2111 = raw_link_2111.split("|LINK:", 1)
                            m_ent = re.search(r"ENTREGA:([\d/]+)", partes_2111)
                            m_ini = re.search(r"INICIO:([\d/]+)", partes_2111)
                            dt_entrega_p_i = m_ent.group(1) if m_ent else ""
                            dt_inicio_p_i = m_ini.group(1) if m_ini else "05/02/2025"
                        elif d2111.get("valor"):
                            dt_entrega_p_i = str(d2111.get("valor"))

                        state_2111 = {
                            "dt_entrega": dt_entrega_p_i,
                            "dt_inicio": dt_inicio_p_i,
                            "link": evidencia_2111,
                        }

                        def calc_pts_2111():
                            try:
                                ent = datetime.strptime(state_2111["dt_entrega"].strip(), "%d/%m/%Y")
                                ini = datetime.strptime(state_2111["dt_inicio"].strip(), "%d/%m/%Y")
                                diff_dias = (ent - ini).days

                                if diff_dias <= 0:
                                    return 18.0
                                elif diff_dias < 15:
                                    return 9.0
                                else:
                                    return 3.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_dt_ent_2111 = ui.input(
                                    label="Data da última entrega (DD/MM/AAAA):",
                                    value=dt_entrega_p_i,
                                    placeholder="Ex: 03/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_2111, "dt_entrega")

                                inp_dt_ini_2111 = ui.input(
                                    label="Data de início das aulas (DD/MM/AAAA):",
                                    value=dt_inicio_p_i,
                                    placeholder="Ex: 05/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_2111, "dt_inicio")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_2111,
                                placeholder="Insira o protocolo de entrega nas escolas ou calendário escolar oficial...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_2111, "link"
                            )

                        lbl_pts_2111 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.11.1: {calc_pts_2111():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_2111():
                            lbl_pts_2111.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.11.1: {calc_pts_2111():.1f} / 18.0 pontos"
                            )

                        inp_dt_ent_2111.on("update:model-value", att_pts_2111)
                        inp_dt_ini_2111.on("update:model-value", att_pts_2111)

                        def salvar_2111():
                            pts = calc_pts_2111()
                            ent_v = state_2111["dt_entrega"].strip()
                            ini_v = state_2111["dt_inicio"].strip()
                            composite = f"ENTREGA:{ent_v},INICIO:{ini_v}|LINK:{state_2111['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.11.1",
                                valor=ent_v,
                                pontos=pts,
                                link=composite,
                                comentarios=d2111.get("comentarios", []),
                                status=d2111.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.11.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.11.1", on_click=salvar_2111).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.11.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 2.11.2 (Motivo da Não Entrega do Kit Escolar - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.11.2 • Motivo da Não Entrega do Kit Escolar (Pré-escola)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Caso o Kit Escolar não tenha sido entregue, informe o motivo:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito justificativo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d2112 = res_data.get("2.11.2") or {}
                        motivo_2112_i = str(d2112.get("valor") or "")
                        link_2112_i = str(d2112.get("link") or "")

                        state_2112 = {
                            "motivo": motivo_2112_i,
                            "link": link_2112_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_2112_i,
                                placeholder="Descreva os problemas de licitação, atraso de fornecedores ou entraves operacionais...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_2112, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_2112_i,
                                placeholder="Insira processos administrativos, pareceres ou justificativa oficial...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_2112, "link"
                            )

                        def salvar_2112():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.11.2",
                                valor=state_2112["motivo"],
                                pontos=0.0,
                                link=state_2112["link"],
                                comentarios=d2112.get("comentarios", []),
                                status=d2112.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.11.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.11.2", on_click=salvar_2112).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.11.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.12 (Entrega de Material Didático - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.12 • Entrega de Material Didático na Pré-escola em 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do material didático (livros, apostilas, etc.) às Pré-Escolas municipais em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Apostilas/livros fornecidos pelo Município, Estado ou Governo Federal."
                        ).classes("text-xs text-gray-400 mb-6")

                        d212 = res_data.get("2.12") or {}

                        opcoes_212_m = {
                            "Selecione...": 0.0,
                            "Sim (0,0 pontos)": 0.0,
                            "Não (0,0 pontos)": 0.0,
                            "O material didático é elaborado na própria escola (18,0 pontos)": 18.0,
                        }

                        val_212_bruto = str(d212.get("valor") or "")
                        val_212_valido = "Selecione..."
                        if val_212_bruto in opcoes_212_m:
                            val_212_valido = val_212_bruto
                        else:
                            for chave in opcoes_212_m.keys():
                                if chave != "Selecione..." and chave.startswith(val_212_bruto):
                                    val_212_valido = chave
                                    break

                        raw_link_212_m = str(d212.get("link") or "")

                        state_212_m = {
                            "opcao": val_212_valido,
                            "link": raw_link_212_m,
                        }

                        def calc_pts_212_m():
                            return float(opcoes_212_m.get(state_212_m["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_212_m = ui.radio(
                                    options=list(opcoes_212_m.keys()),
                                    value=state_212_m["opcao"],
                                ).props("color=blue").bind_value(state_212_m, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_212_m,
                                placeholder="Insira o comprovante de recebimento do PNLD, notas de entrega ou projeto pedagógico...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_212_m, "link"
                            )

                        lbl_pts_212_m = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.12: {calc_pts_212_m():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_212_m():
                            lbl_pts_212_m.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.12: {calc_pts_212_m():.1f} / 18.0 pontos"
                            )

                        rad_212_m.on("update:model-value", att_pts_212_m)

                        def salvar_212_m():
                            pts = calc_pts_212_m()
                            opt_sel = state_212_m["opcao"]
                            lnk = state_212_m["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.12",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d212.get("comentarios", []),
                                status=d212.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.12 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.12", on_click=salvar_212_m).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.12", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.12.1 (Data da Última Entrega do Material Didático - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.12.1 • Data da Última Entrega do Material Didático na Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega e a data de início das aulas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: ≤ Início Aulas = 18.0 pts | < Início + 15 dias = 9.0 pts | ≥ Início + 15 dias = 3.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d2121 = res_data.get("2.12.1") or {}
                        raw_link_2121 = str(d2121.get("link") or "")

                        dt_entrega_p2_i, dt_inicio_p2_i = "", "05/02/2025"
                        evidencia_2121 = raw_link_2121

                        if "|LINK:" in raw_link_2121:
                            partes_2121, evidencia_2121 = raw_link_2121.split("|LINK:", 1)
                            m_ent = re.search(r"ENTREGA:([\d/]+)", partes_2121)
                            m_ini = re.search(r"INICIO:([\d/]+)", partes_2121)
                            dt_entrega_p2_i = m_ent.group(1) if m_ent else ""
                            dt_inicio_p2_i = m_ini.group(1) if m_ini else "05/02/2025"
                        elif d2121.get("valor"):
                            dt_entrega_p2_i = str(d2121.get("valor"))

                        state_2121 = {
                            "dt_entrega": dt_entrega_p2_i,
                            "dt_inicio": dt_inicio_p2_i,
                            "link": evidencia_2121,
                        }

                        def calc_pts_2121():
                            try:
                                ent = datetime.strptime(state_2121["dt_entrega"].strip(), "%d/%m/%Y")
                                ini = datetime.strptime(state_2121["dt_inicio"].strip(), "%d/%m/%Y")
                                diff_dias = (ent - ini).days

                                if diff_dias <= 0:
                                    return 18.0
                                elif diff_dias < 15:
                                    return 9.0
                                else:
                                    return 3.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_dt_ent_2121 = ui.input(
                                    label="Data da última entrega (DD/MM/AAAA):",
                                    value=dt_entrega_p2_i,
                                    placeholder="Ex: 02/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_2121, "dt_entrega")

                                inp_dt_ini_2121 = ui.input(
                                    label="Data de início das aulas (DD/MM/AAAA):",
                                    value=dt_inicio_p2_i,
                                    placeholder="Ex: 05/02/2025",
                                ).classes("w-full").props("outlined color=blue").bind_value(state_2121, "dt_inicio")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_2121,
                                placeholder="Insira as guias de remessa do FNDE, termo de recebimento nas pré-escolas...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_2121, "link"
                            )

                        lbl_pts_2121 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.12.1: {calc_pts_2121():.1f} / 18.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_2121():
                            lbl_pts_2121.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.12.1: {calc_pts_2121():.1f} / 18.0 pontos"
                            )

                        inp_dt_ent_2121.on("update:model-value", att_pts_2121)
                        inp_dt_ini_2121.on("update:model-value", att_pts_2121)

                        def salvar_2121():
                            pts = calc_pts_2121()
                            ent_v = state_2121["dt_entrega"].strip()
                            ini_v = state_2121["dt_inicio"].strip()
                            composite = f"ENTREGA:{ent_v},INICIO:{ini_v}|LINK:{state_2121['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.12.1",
                                valor=ent_v,
                                pontos=pts,
                                link=composite,
                                comentarios=d2121.get("comentarios", []),
                                status=d2121.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.12.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.12.1", on_click=salvar_2121).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.12.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.12.2 (Motivo da Não Entrega do Material Didático - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.12.2 • Motivo da Não Entrega do Material Didático (Pré-escola)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Caso o material didático não tenha sido entregue, informe o motivo:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito justificativo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d2122 = res_data.get("2.12.2") or {}
                        motivo_2122_i = str(d2122.get("valor") or "")
                        link_2122_i = str(d2122.get("link") or "")

                        state_2122 = {
                            "motivo": motivo_2122_i,
                            "link": link_2122_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_2122_i,
                                placeholder="Descreva os problemas de compra, adesão ao PNLD ou atraso de distribuição...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_2122, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_2122_i,
                                placeholder="Insira relatórios administrativos, comunicação oficial ou justificativa...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_2122, "link"
                            )

                        def salvar_2122():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.12.2",
                                valor=state_2122["motivo"],
                                pontos=0.0,
                                link=state_2122["link"],
                                comentarios=d2122.get("comentarios", []),
                                status=d2122.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.12.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.12.2", on_click=salvar_2122).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.12.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.13 (Pesquisa/Estudo sobre Demanda por Pré-escola em 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.13 • Pesquisa/Estudo de Demanda por Vagas de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura municipal fez uma pesquisa/estudo para levantar o número de crianças que necessitavam de Pré-escola em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuação: Sim = 50.0 pts | Não = 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d213 = res_data.get("2.13") or {}

                        opcoes_213 = {
                            "Selecione...": 0.0,
                            "Sim (50,0 pontos)": 50.0,
                            "Não (0,0 pontos)": 0.0,
                        }

                        val_213_bruto = str(d213.get("valor") or "")
                        val_213_valido = "Selecione..."
                        if val_213_bruto in opcoes_213:
                            val_213_valido = val_213_bruto
                        else:
                            for chave in opcoes_213.keys():
                                if chave != "Selecione..." and chave.startswith(val_213_bruto):
                                    val_213_valido = chave
                                    break

                        raw_link_213 = str(d213.get("link") or "")

                        state_213 = {
                            "opcao": val_213_valido,
                            "link": raw_link_213,
                        }

                        def calc_pts_213():
                            return float(opcoes_213.get(state_213["opcao"], 0.0))

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_213 = ui.radio(
                                    options=list(opcoes_213.keys()),
                                    value=state_213["opcao"],
                                ).props("color=blue").bind_value(state_213, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_213,
                                placeholder="Insira a cópia do estudo de demanda, busca ativa ou relatório de mapeamento...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_213, "link"
                            )

                        lbl_pts_213 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.13: {calc_pts_213():.1f} / 50.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_213():
                            lbl_pts_213.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.13: {calc_pts_213():.1f} / 50.0 pontos"
                            )

                        rad_213.on("update:model-value", att_pts_213)

                        def salvar_213():
                            pts = calc_pts_213()
                            opt_sel = state_213["opcao"]
                            lnk = state_213["link"]

                            save_resposta(
                                ano=ano_sel,
                                qid="2.13",
                                valor=opt_sel,
                                pontos=pts,
                                link=lnk,
                                comentarios=d213.get("comentarios", []),
                                status=d213.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.13 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.13", on_click=salvar_213).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.13", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.13.1 (Descrição do Estudo de Demanda - Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.13.1 • Descrição da Pesquisa / Estudo de Demanda (Pré-escola)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Descreva a metodologia e os resultados da pesquisa/estudo realizada:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label("ℹ Quesito descritivo / declaratório.").classes(
                            "text-xs text-gray-400 mb-6"
                        )

                        d2131 = res_data.get("2.13.1") or {}
                        desc_2131_i = str(d2131.get("valor") or "")
                        link_2131_i = str(d2131.get("link") or "")

                        state_2131 = {
                            "descricao": desc_2131_i,
                            "link": link_2131_i,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Descrição do Estudo / Pesquisa:",
                                value=desc_2131_i,
                                placeholder="Descreva como foi feito o mapeamento, órgãos envolvidos e conclusões...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_2131, "descricao"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=link_2131_i,
                                placeholder="Insira o link do documento da pesquisa, ato normativo ou publicação oficial...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_2131, "link"
                            )

                        def salvar_2131():
                            save_resposta(
                                ano=ano_sel,
                                qid="2.13.1",
                                valor=state_2131["descricao"],
                                pontos=0.0,
                                link=state_2131["link"],
                                comentarios=d2131.get("comentarios", []),
                                status=d2131.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.13.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.13.1", on_click=salvar_2131).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.13.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.14 (Demanda vs. Oferta de Vagas de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.14 • Demanda Manifesta vs. Oferta de Vagas de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de solicitações de vagas (4 a 5 anos) até 31/12/2025 e o total de vagas ofertadas:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se Solicitações (Demanda) > Vagas Ofertadas (Oferta) → Perde 50.0 pts (-50.0) | Caso contrário → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d214 = res_data.get("2.14") or {}
                        raw_link_214 = str(d214.get("link") or "")

                        demanda_214_i, oferta_214_i = 0, 0
                        evidencia_214 = raw_link_214

                        if "|LINK:" in raw_link_214:
                            partes_214, evidencia_214 = raw_link_214.split("|LINK:", 1)
                            m_dem = re.search(r"DEMANDA:(\d+)", partes_214)
                            m_ofe = re.search(r"OFERTA:(\d+)", partes_214)
                            demanda_214_i = int(m_dem.group(1)) if m_dem else 0
                            oferta_214_i = int(m_ofe.group(1)) if m_ofe else 0

                        state_214 = {
                            "demanda": demanda_214_i,
                            "oferta": oferta_214_i,
                            "link": evidencia_214,
                        }

                        def calc_pts_214():
                            dem = int(state_214["demanda"] or 0)
                            ofe = int(state_214["oferta"] or 0)
                            if dem <= 0 and ofe <= 0:
                                return 0.0
                            return -50.0 if dem > ofe else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_demanda_214 = (
                                    ui.number(
                                        "Nº de crianças (4-5 anos) que solicitaram vaga até 31/12/2025:",
                                        value=demanda_214_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_214, "demanda")
                                )

                                inp_oferta_214 = (
                                    ui.number(
                                        "Nº de vagas de pré-escola OFERTADAS em 2025:",
                                        value=oferta_214_i,
                                        min=0,
                                        step=1,
                                    )
                                    .classes("w-full")
                                    .props("outlined color=blue")
                                    .bind_value(state_214, "oferta")
                                )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_214,
                                placeholder="Insira a lista de espera unificada, relatório de matrículas do Censo ou sistema municipal...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_214, "link"
                            )

                        lbl_pts_214 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.14: {calc_pts_214():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_214():
                            pts = calc_pts_214()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_214.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_214.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.14: {pts:.1f} pontos"
                            )

                        inp_demanda_214.on("update:model-value", att_pts_214)
                        inp_oferta_214.on("update:model-value", att_pts_214)

                        def salvar_214():
                            d_val = int(state_214["demanda"] or 0)
                            o_val = int(state_214["oferta"] or 0)
                            pts_finais = calc_pts_214()
                            composite = f"DEMANDA:{d_val},OFERTA:{o_val}|LINK:{state_214['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.14",
                                valor=f"Solicitadas: {d_val} / Ofertadas: {o_val}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d214.get("comentarios", []),
                                status=d214.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 2.14 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.14", on_click=salvar_214).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.14", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 2.15 (Quantidade de Alunos por Turma de Pré-escola)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("2.15 • Distribuição de Alunos por Turma de Pré-escola").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de turmas de Pré-escola em cada faixa de número de alunos:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Cálculo: NF = 10.0 × (1.0×P1 + 0.5×P2 + 0.25×P3 + 0×P4) | Pmáx = 10.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d215 = res_data.get("2.15") or {}
                        raw_link_215 = str(d215.get("link") or "")

                        pt1_i, pt2_i, pt3_i, pt4_i = 0, 0, 0, 0
                        evidencia_215 = raw_link_215

                        if "|LINK:" in raw_link_215:
                            partes_215, evidencia_215 = raw_link_215.split("|LINK:", 1)
                            m_t1 = re.search(r"T1:(\d+)", partes_215)
                            m_t2 = re.search(r"T2:(\d+)", partes_215)
                            m_t3 = re.search(r"T3:(\d+)", partes_215)
                            m_t4 = re.search(r"T4:(\d+)", partes_215)
                            pt1_i = int(m_t1.group(1)) if m_t1 else 0
                            pt2_i = int(m_t2.group(1)) if m_t2 else 0
                            pt3_i = int(m_t3.group(1)) if m_t3 else 0
                            pt4_i = int(m_t4.group(1)) if m_t4 else 0

                        state_215 = {
                            "t1": pt1_i,
                            "t2": pt2_i,
                            "t3": pt3_i,
                            "t4": pt4_i,
                            "link": evidencia_215,
                        }

                        def calc_pts_215():
                            c1 = int(state_215["t1"] or 0)
                            c2 = int(state_215["t2"] or 0)
                            c3 = int(state_215["t3"] or 0)
                            c4 = int(state_215["t4"] or 0)
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

                            return min(10.0 * (n1 + n2 + n3 + n4), 10.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_pt1 = ui.number("Turmas com até 22 alunos:", value=pt1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_215, "t1")
                                inp_pt2 = ui.number("Turmas de 23 a 25 alunos:", value=pt2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_215, "t2")
                                inp_pt3 = ui.number("Turmas de 26 a 30 alunos:", value=pt3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_215, "t3")
                                inp_pt4 = ui.number("Turmas acima de 30 alunos:", value=pt4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_215, "t4")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_215,
                                placeholder="Insira o relatório de turmas/enturmação do Censo Escolar ou sistema de gestão...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_215, "link"
                            )

                        lbl_pts_215 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 2.15: {calc_pts_215():.2f} / 10.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_215():
                            lbl_pts_215.set_text(
                                f"📊 Impacto de Pontuação no Quesito 2.15: {calc_pts_215():.2f} / 10.0 pontos"
                            )

                        for inp in [inp_pt1, inp_pt2, inp_pt3, inp_pt4]:
                            inp.on("update:model-value", att_pts_215)

                        def salvar_215():
                            c1, c2, c3, c4 = state_215["t1"], state_215["t2"], state_215["t3"], state_215["t4"]
                            pts_finais = calc_pts_215()
                            composite = f"T1:{c1},T2:{c2},T3:{c3},T4:{c4}|LINK:{state_215['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="2.15",
                                valor=f"T1:{c1}/T2:{c2}/T3:{c3}/T4:{c4}",
                                pontos=pts_finais,
                                link=composite,
                                comentarios=d215.get("comentarios", []),
                                status=d215.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 2.15 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 2.15", on_click=salvar_215).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("2.15", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 3.0 (Oferta dos Anos Iniciais do Ensino Fundamental)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.0 • Oferta dos Anos Iniciais do Ensino Fundamental (1º ao 5º ano)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura Municipal oferece os Anos Iniciais do Ensino Fundamental (1º ao 5º ano)?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório e condicional para o Bloco 3."
                        ).classes("text-xs text-gray-400 mb-6")

                        d30 = res_data.get("3.0") or {}

                        opcoes_30 = {
                            "Selecione...": 0.0,
                            "Sim": 0.0,
                            "Não": 0.0,
                        }

                        val_30_bruto = str(d30.get("valor") or "")
                        val_30_valido = "Selecione..."
                        if val_30_bruto in opcoes_30:
                            val_30_valido = val_30_bruto
                        else:
                            for chave in opcoes_30.keys():
                                if chave != "Selecione..." and chave.startswith(val_30_bruto):
                                    val_30_valido = chave
                                    break

                        raw_link_30 = str(d30.get("link") or "")

                        state_30 = {
                            "opcao": val_30_valido,
                            "link": raw_link_30,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_30 = ui.radio(
                                    options=list(opcoes_30.keys()),
                                    value=state_30["opcao"],
                                ).props("color=blue").bind_value(state_30, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_30,
                                placeholder="Insira o ato de criação das escolas, dados do Censo Escolar ou decreto...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_30, "link"
                            )

                        def salvar_30():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.0",
                                valor=state_30["opcao"],
                                pontos=0.0,
                                link=state_30["link"],
                                comentarios=d30.get("comentarios", []),
                                status=d30.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.0", on_click=salvar_30).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.1 (Espaço por Aluno em Sala de Aula - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.1 • Área por Aluno em Sala de Aula (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de turmas dos Anos Iniciais por faixa de área disponível por aluno (m²):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: NF = 10.0×P1 + 5.0×P2 + 2.5×P3 + 0×P4 | Pmáx = 10.0 pontos."
                        ).classes("text-xs text-gray-400 mb-6")

                        d31 = res_data.get("3.1") or {}
                        raw_link_31 = str(d31.get("link") or "")

                        f1_i, f2_i, f3_i, f4_i = 0, 0, 0, 0
                        evidencia_31 = raw_link_31

                        if "|LINK:" in raw_link_31:
                            partes_31, evidencia_31 = raw_link_31.split("|LINK:", 1)
                            m_f1 = re.search(r"F1:(\d+)", partes_31)
                            m_f2 = re.search(r"F2:(\d+)", partes_31)
                            m_f3 = re.search(r"F3:(\d+)", partes_31)
                            m_f4 = re.search(r"F4:(\d+)", partes_31)
                            f1_i = int(m_f1.group(1)) if m_f1 else 0
                            f2_i = int(m_f2.group(1)) if m_f2 else 0
                            f3_i = int(m_f3.group(1)) if m_f3 else 0
                            f4_i = int(m_f4.group(1)) if m_f4 else 0

                        state_31 = {
                            "f1": f1_i,
                            "f2": f2_i,
                            "f3": f3_i,
                            "f4": f4_i,
                            "link": evidencia_31,
                        }

                        def calc_pts_31():
                            c1 = int(state_31["f1"] or 0)
                            c2 = int(state_31["f2"] or 0)
                            c3 = int(state_31["f3"] or 0)
                            c4 = int(state_31["f4"] or 0)
                            tot = c1 + c2 + c3 + c4
                            if tot <= 0:
                                return 0.0

                            p1 = c1 / tot
                            p2 = c2 / tot
                            p3 = c3 / tot
                            p4 = c4 / tot

                            return min(10.0 * p1 + 5.0 * p2 + 2.5 * p3, 10.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_f1_31 = ui.number("Superior ou igual a 1,875 m²:", value=f1_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_31, "f1")
                                inp_f2_31 = ui.number("Superior ou igual a 1,20 m² e < 1,875 m²:", value=f2_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_31, "f2")
                                inp_f3_31 = ui.number("Superior ou igual a 1,00 m² e < 1,20 m²:", value=f3_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_31, "f3")
                                inp_f4_31 = ui.number("Inferior a 1,00 m²:", value=f4_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_31, "f4")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_31,
                                placeholder="Insira a planta baixa das salas, laudo de medição ou inventário das unidades...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_31, "link"
                            )

                        lbl_pts_31 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.1: {calc_pts_31():.2f} / 10.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_31():
                            lbl_pts_31.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.1: {calc_pts_31():.2f} / 10.0 pontos"
                            )

                        for inp in [inp_f1_31, inp_f2_31, inp_f3_31, inp_f4_31]:
                            inp.on("update:model-value", att_pts_31)

                        def salvar_31():
                            c1, c2, c3, c4 = state_31["f1"], state_31["f2"], state_31["f3"], state_31["f4"]
                            pts = calc_pts_31()
                            composite = f"F1:{c1},F2:{c2},F3:{c3},F4:{c4}|LINK:{state_31['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.1",
                                valor=f"F1:{c1}/F2:{c2}/F3:{c3}/F4:{c4}",
                                pontos=pts,
                                link=composite,
                                comentarios=d31.get("comentarios", []),
                                status=d31.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.1", on_click=salvar_31).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.2 (Qualificação dos Professores - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.2 • Qualificação Acadêmica dos Professores (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de professores regentes dos Anos Iniciais e a quantidade com Licenciatura e Pós-graduação:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Graduação (Grad): 100%=12pts | 90-99%=7pts | 80-89%=3pts | 70-79%=1pt | <70%=0pt.\n"
                            "ℹ Pós-Graduação (Pgrad): ≥50%=7pts | 40-49%=5pts | 20-39%=3pts | <20%=0pt. Pmáx = 19.0 pontos."
                        ).classes("text-xs text-gray-400 mb-6 whitespace-pre-line")

                        d32 = res_data.get("3.2") or {}
                        raw_link_32 = str(d32.get("link") or "")

                        tot_prof_32_i, grad_32_i, pgrad_32_i = 0, 0, 0
                        evidencia_32 = raw_link_32

                        if "|LINK:" in raw_link_32:
                            partes_32, evidencia_32 = raw_link_32.split("|LINK:", 1)
                            m_tot = re.search(r"TOT:(\d+)", partes_32)
                            m_grad = re.search(r"GRAD:(\d+)", partes_32)
                            m_pgrad = re.search(r"PGRAD:(\d+)", partes_32)
                            tot_prof_32_i = int(m_tot.group(1)) if m_tot else 0
                            grad_32_i = int(m_grad.group(1)) if m_grad else 0
                            pgrad_32_i = int(m_pgrad.group(1)) if m_pgrad else 0

                        state_32 = {
                            "tot_prof": tot_prof_32_i,
                            "grad": grad_32_i,
                            "pgrad": pgrad_32_i,
                            "link": evidencia_32,
                        }

                        def calc_pts_32():
                            tot = int(state_32["tot_prof"] or 0)
                            grd = int(state_32["grad"] or 0)
                            pgrd = int(state_32["pgrad"] or 0)

                            if tot <= 0:
                                return 0.0

                            g_pct = (grd / tot) * 100.0
                            p_pct = (pgrd / tot) * 100.0

                            # Pontuação Graduação (N1)
                            if g_pct >= 100.0:
                                n1 = 12.0
                            elif g_pct >= 90.0:
                                n1 = 7.0
                            elif g_pct >= 80.0:
                                n1 = 3.0
                            elif g_pct >= 70.0:
                                n1 = 1.0
                            else:
                                n1 = 0.0

                            # Pontuação Pós-Graduação (N2)
                            if p_pct >= 50.0:
                                n2 = 7.0
                            elif p_pct >= 40.0:
                                n2 = 5.0
                            elif p_pct >= 20.0:
                                n2 = 3.0
                            else:
                                n2 = 0.0

                            return min(n1 + n2, 19.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_tot_32 = ui.number("Total de professores regentes:", value=tot_prof_32_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_32, "tot_prof")
                                inp_grad_32 = ui.number("Professores com Licenciatura (GRAD):", value=grad_32_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_32, "grad")
                                inp_pgrad_32 = ui.number("Professores com Pós-Graduação (PGRAD):", value=pgrad_32_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_32, "pgrad")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_32,
                                placeholder="Insira os relatórios do Censo Escolar 2025, diplomas ou dados do RH...",
                            ).classes("w-full").props("outlined rows=8").bind_value(
                                state_32, "link"
                            )

                        lbl_pts_32 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.2: {calc_pts_32():.1f} / 19.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_32():
                            lbl_pts_32.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.2: {calc_pts_32():.1f} / 19.0 pontos"
                            )

                        inp_tot_32.on("update:model-value", att_pts_32)
                        inp_grad_32.on("update:model-value", att_pts_32)
                        inp_pgrad_32.on("update:model-value", att_pts_32)

                        def salvar_32():
                            tot, grd, pgrd = state_32["tot_prof"], state_32["grad"], state_32["pgrad"]
                            pts = calc_pts_32()
                            composite = f"TOT:{tot},GRAD:{grd},PGRAD:{pgrd}|LINK:{state_32['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.2",
                                valor=f"Total: {tot} | GRAD: {grd} | PGRAD: {pgrd}",
                                pontos=pts,
                                link=composite,
                                comentarios=d32.get("comentarios", []),
                                status=d32.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.2", on_click=salvar_32).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.3 (Piso Salarial dos Professores - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.3 • Piso Salarial dos Professores (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o valor do piso salarial mensal dos professores dos Anos Iniciais (base 40h/semanais) e o salário mínimo legal:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se Piso < Salário Mínimo → Penalidade de -20.0 pts | Se Piso ≥ Salário Mínimo → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d33 = res_data.get("3.3") or {}
                        raw_link_33 = str(d33.get("link") or "")

                        piso_33_i, sm_33_i = 0.0, 1518.00
                        evidencia_33 = raw_link_33

                        if "|LINK:" in raw_link_33:
                            partes_33, evidencia_33 = raw_link_33.split("|LINK:", 1)
                            m_piso = re.search(r"PISO:([\d\.]+)", partes_33)
                            m_sm = re.search(r"SM:([\d\.]+)", partes_33)
                            piso_33_i = float(m_piso.group(1)) if m_piso else 0.0
                            sm_33_i = float(m_sm.group(1)) if m_sm else 1518.00

                        state_33 = {
                            "piso": piso_33_i,
                            "sm": sm_33_i,
                            "link": evidencia_33,
                        }

                        def calc_pts_33():
                            piso_v = float(state_33["piso"] or 0.0)
                            sm_v = float(state_33["sm"] or 0.0)
                            if piso_v <= 0:
                                return 0.0
                            return -20.0 if piso_v < sm_v else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_piso_33 = ui.number("Piso Salarial (40h) R$:", value=piso_33_i, min=0.0, step=50.0, format="%.2f").classes("w-full").props("outlined color=blue").bind_value(state_33, "piso")
                                inp_sm_33 = ui.number("Salário Mínimo de Referência R$:", value=sm_33_i, min=0.0, step=10.0, format="%.2f").classes("w-full").props("outlined color=blue").bind_value(state_33, "sm")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_33,
                                placeholder="Insira a lei municipal do plano de cargos e carreiras, holerite modelo ou tabela salarial...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_33, "link"
                            )

                        lbl_pts_33 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.3: {calc_pts_33():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_33():
                            pts = calc_pts_33()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_33.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_33.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.3: {pts:.1f} pontos"
                            )

                        inp_piso_33.on("update:model-value", att_pts_33)
                        inp_sm_33.on("update:model-value", att_pts_33)

                        def salvar_33():
                            p_v = float(state_33["piso"] or 0.0)
                            sm_v = float(state_33["sm"] or 0.0)
                            pts = calc_pts_33()
                            composite = f"PISO:{p_v:.2f},SM:{sm_v:.2f}|LINK:{state_33['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.3",
                                valor=f"Piso: R$ {p_v:.2f} (SM: R$ {sm_v:.2f})",
                                pontos=pts,
                                link=composite,
                                comentarios=d33.get("comentarios", []),
                                status=d33.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.3", on_click=salvar_33).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.4 (Ausência de Professores - Anos Iniciais - QTA)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.4 • Quantidade Total de Ausências de Professores (QTA) - Anos Iniciais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade total de dias de ausência dos professores dos Anos Iniciais em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito quantitativo e declaratório para composição do indicador de faltas/licenças."
                        ).classes("text-xs text-gray-400 mb-6")

                        d34 = res_data.get("3.4") or {}
                        raw_link_34 = str(d34.get("link") or "")

                        injust_i, just_i, med_i, mat_i, abon_i, out_i = 0, 0, 0, 0, 0, 0
                        evidencia_34 = raw_link_34

                        if "|LINK:" in raw_link_34:
                            partes_34, evidencia_34 = raw_link_34.split("|LINK:", 1)
                            m_in = re.search(r"INJUST:(\d+)", partes_34)
                            m_ju = re.search(r"JUST:(\d+)", partes_34)
                            m_me = re.search(r"MED:(\d+)", partes_34)
                            m_ma = re.search(r"MAT:(\d+)", partes_34)
                            m_ab = re.search(r"ABON:(\d+)", partes_34)
                            m_ou = re.search(r"OUT:(\d+)", partes_34)

                            injust_i = int(m_in.group(1)) if m_in else 0
                            just_i = int(m_ju.group(1)) if m_ju else 0
                            med_i = int(m_me.group(1)) if m_me else 0
                            mat_i = int(m_ma.group(1)) if m_ma else 0
                            abon_i = int(m_ab.group(1)) if m_ab else 0
                            out_i = int(m_ou.group(1)) if m_ou else 0

                        state_34 = {
                            "injustificadas": injust_i,
                            "justificadas": just_i,
                            "medica": med_i,
                            "maternidade": mat_i,
                            "abonos": abon_i,
                            "outros": out_i,
                            "link": evidencia_34,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_inj_34 = ui.number("Faltas injustificadas (dias):", value=injust_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "injustificadas")
                                inp_jus_34 = ui.number("Faltas justificadas (dias):", value=just_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "justificadas")
                                inp_med_34 = ui.number("Licença médica (dias):", value=med_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "medica")
                                inp_mat_34 = ui.number("Licença maternidade/paternidade (dias):", value=mat_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "maternidade")
                                inp_abo_34 = ui.number("Abonos (dias):", value=abon_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "abonos")
                                inp_out_34 = ui.number("Outros afastamentos legais (dias):", value=out_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_34, "outros")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_34,
                                placeholder="Insira os relatórios consolidados do RH, mapas de frequência ou registros do sistema...",
                            ).classes("w-full").props("outlined rows=14").bind_value(
                                state_34, "link"
                            )

                        def salvar_34():
                            v_inj = int(state_34["injustificadas"] or 0)
                            v_jus = int(state_34["justificadas"] or 0)
                            v_med = int(state_34["medica"] or 0)
                            v_mat = int(state_34["maternidade"] or 0)
                            v_abo = int(state_34["abonos"] or 0)
                            v_out = int(state_34["outros"] or 0)
                            tot_dias = v_inj + v_jus + v_med + v_mat + v_abo + v_out

                            composite = (
                                f"INJUST:{v_inj},JUST:{v_jus},MED:{v_med},"
                                f"MAT:{v_mat},ABON:{v_abo},OUT:{v_out}|LINK:{state_34['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="3.4",
                                valor=f"Total: {tot_dias} dias ausentes",
                                pontos=0.0,
                                link=composite,
                                comentarios=d34.get("comentarios", []),
                                status=d34.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.4", on_click=salvar_34).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.4", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.5 (Capacitação dos Profissionais - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.5 • Participação em Cursos de Capacitação (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os profissionais dos Anos Iniciais da rede municipal participaram de cursos de capacitação durante o ano de 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório."
                        ).classes("text-xs text-gray-400 mb-6")

                        d35 = res_data.get("3.5") or {}

                        opcoes_35 = {
                            "Selecione...": 0.0,
                            "Sim": 0.0,
                            "Não": 0.0,
                        }

                        val_35_bruto = str(d35.get("valor") or "")
                        val_35_valido = "Selecione..."
                        if val_35_bruto in opcoes_35:
                            val_35_valido = val_35_bruto
                        else:
                            for chave in opcoes_35.keys():
                                if chave != "Selecione..." and chave.startswith(val_35_bruto):
                                    val_35_valido = chave
                                    break

                        raw_link_35 = str(d35.get("link") or "")

                        state_35 = {
                            "opcao": val_35_valido,
                            "link": raw_link_35,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_35 = ui.radio(
                                    options=list(opcoes_35.keys()),
                                    value=state_35["opcao"],
                                ).props("color=blue").bind_value(state_35, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_35,
                                placeholder="Insira listas de presença, certificados, programa dos cursos ou relatórios de formação...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_35, "link"
                            )

                        def salvar_35():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.5",
                                valor=state_35["opcao"],
                                pontos=0.0,
                                link=state_35["link"],
                                comentarios=d35.get("comentarios", []),
                                status=d35.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.5", on_click=salvar_35).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.5", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 3.5.1 (Proporção de Profissionais Capacitados - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.5.1 • Proporção de Profissionais Capacitados (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de profissionais capacitados e o total de profissionais (Professores, Apoio e Gestores):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: PC = (Prof. Cap. + Apoio Cap. + Gestores Cap.) / (Total Prof. + Total Apoio + Total Gestores)\n"
                            "ℹ Pontuação: PC = 100% -> 7,0 pts | 70% <= PC < 100% -> 5,0 pts | 50% <= PC < 70% -> 3,0 pts | PC < 50% -> 0,0 pts."
                        ).classes("text-xs text-gray-400 mb-6 whitespace-pre-line")

                        d351 = res_data.get("3.5.1") or {}
                        raw_link_351 = str(d351.get("link") or "")

                        prof_cap_i, apo_cap_i, ges_cap_i = 0, 0, 0
                        tot_prof_351_i, tot_apo_351_i, tot_ges_351_i = 0, 0, 0
                        evidencia_351 = raw_link_351

                        if "|LINK:" in raw_link_351:
                            partes_351, evidencia_351 = raw_link_351.split("|LINK:", 1)
                            m_pc = re.search(r"PROF_CAP:(\d+)", partes_351)
                            m_ac = re.search(r"APO_CAP:(\d+)", partes_351)
                            m_gc = re.search(r"GES_CAP:(\d+)", partes_351)
                            m_tp = re.search(r"TOT_PROF:(\d+)", partes_351)
                            m_ta = re.search(r"TOT_APO:(\d+)", partes_351)
                            m_tg = re.search(r"TOT_GES:(\d+)", partes_351)

                            prof_cap_i = int(m_pc.group(1)) if m_pc else 0
                            apo_cap_i = int(m_ac.group(1)) if m_ac else 0
                            ges_cap_i = int(m_gc.group(1)) if m_gc else 0
                            tot_prof_351_i = int(m_tp.group(1)) if m_tp else 0
                            tot_apo_351_i = int(m_ta.group(1)) if m_ta else 0
                            tot_ges_351_i = int(m_tg.group(1)) if m_tg else 0

                        state_351 = {
                            "prof_cap": prof_cap_i,
                            "apo_cap": apo_cap_i,
                            "ges_cap": ges_cap_i,
                            "tot_prof": tot_prof_351_i,
                            "tot_apo": tot_apo_351_i,
                            "tot_ges": tot_ges_351_i,
                            "link": evidencia_351,
                        }

                        def calc_pts_351():
                            num = int(state_351["prof_cap"] or 0) + int(state_351["apo_cap"] or 0) + int(state_351["ges_cap"] or 0)
                            den = int(state_351["tot_prof"] or 0) + int(state_351["tot_apo"] or 0) + int(state_351["tot_ges"] or 0)

                            if den <= 0:
                                return 0.0

                            pc = (num / den) * 100.0

                            if pc >= 100.0:
                                return 7.0
                            elif pc >= 70.0:
                                return 5.0
                            elif pc >= 50.0:
                                return 3.0
                            else:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_pc = ui.number("Professores regentes capacitados:", value=prof_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "prof_cap")
                                inp_ac = ui.number("Profissionais de apoio capacitados:", value=apo_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "apo_cap")
                                inp_gc = ui.number("Gestores escolares capacitados:", value=ges_cap_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "ges_cap")
                                ui.separator().classes("my-1")
                                inp_tp = ui.number("Total de professores regentes:", value=tot_prof_351_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "tot_prof")
                                inp_ta = ui.number("Total de profissionais de apoio:", value=tot_apo_351_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "tot_apo")
                                inp_tg = ui.number("Total de gestores escolares:", value=tot_ges_351_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_351, "tot_ges")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_351,
                                placeholder="Insira relatórios de formação continuada, listas de presença com CPF ou declarações do setor...",
                            ).classes("w-full").props("outlined rows=16").bind_value(
                                state_351, "link"
                            )

                        lbl_pts_351 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.5.1: {calc_pts_351():.1f} / 7.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_351():
                            lbl_pts_351.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.5.1: {calc_pts_351():.1f} / 7.0 pontos"
                            )

                        for input_comp in [inp_pc, inp_ac, inp_gc, inp_tp, inp_ta, inp_tg]:
                            input_comp.on("update:model-value", att_pts_351)

                        def salvar_351():
                            p_cap, a_cap, g_cap = state_351["prof_cap"], state_351["apo_cap"], state_351["ges_cap"]
                            t_prof, t_apo, t_ges = state_351["tot_prof"], state_351["tot_apo"], state_351["tot_ges"]
                            pts = calc_pts_351()

                            composite = (
                                f"PROF_CAP:{p_cap},APO_CAP:{a_cap},GES_CAP:{g_cap},"
                                f"TOT_PROF:{t_prof},TOT_APO:{t_apo},TOT_GES:{t_ges}|LINK:{state_351['link']}"
                            )

                            save_resposta(
                                ano=ano_sel,
                                qid="3.5.1",
                                valor=f"Capacitados: {p_cap+a_cap+g_cap} / Total: {t_prof+t_apo+t_ges}",
                                pontos=pts,
                                link=composite,
                                comentarios=d351.get("comentarios", []),
                                status=d351.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.5.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.5.1", on_click=salvar_351).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.5.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.5.2 (Forma de Capacitação - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.5.2 • Forma de Capacitação (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as modalidades de capacitação utilizadas em 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório de múltipla escolha."
                        ).classes("text-xs text-gray-400 mb-6")

                        d352 = res_data.get("3.5.2") or {}
                        val_352_bruto = str(d352.get("valor") or "")
                        raw_link_352 = str(d352.get("link") or "")

                        state_352 = {
                            "presencial": "Presencial" in val_352_bruto,
                            "ead": "EAD" in val_352_bruto or "distância" in val_352_bruto,
                            "multiplicadores": "Multiplicadores" in val_352_bruto,
                            "outros": "Outros" in val_352_bruto,
                            "link": raw_link_352,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-2"):
                                ui.checkbox("Presencialmente").bind_value(state_352, "presencial")
                                ui.checkbox("À distância / Remotamente").bind_value(state_352, "ead")
                                ui.checkbox("Por meio de multiplicadores").bind_value(state_352, "multiplicadores")
                                ui.checkbox("Outros").bind_value(state_352, "outros")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_352,
                                placeholder="Insira o plano de formação, links de plataformas EAD ou relatórios das oficinas...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_352, "link"
                            )

                        def salvar_352():
                            selecionados = []
                            if state_352["presencial"]:
                                selecionados.append("Presencial")
                            if state_352["ead"]:
                                selecionados.append("À distância/remotamente")
                            if state_352["multiplicadores"]:
                                selecionados.append("Por meio de multiplicadores")
                            if state_352["outros"]:
                                selecionados.append("Outros")

                            val_str = ", ".join(selecionados) if selecionados else "Nenhuma selecionada"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.5.2",
                                valor=val_str,
                                pontos=0.0,
                                link=state_352["link"],
                                comentarios=d352.get("comentarios", []),
                                status=d352.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.5.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.5.2", on_click=salvar_352).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.5.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.6 (Rotatividade de Professores - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.6 • Rotatividade do Corpo Docente (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de escolas distribuídas por faixa de rotatividade de professores:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: NF = (3×Q1 + 2×Q2 + 1×Q3 + 0×Q4) | Pmáx = 3.0 pontos.\n"
                            "ℹ Qi = Proporção de escolas na faixa de rotatividade correspondente."
                        ).classes("text-xs text-gray-400 mb-6 whitespace-pre-line")

                        d36 = res_data.get("3.6") or {}
                        raw_link_36 = str(d36.get("link") or "")

                        q1_36_i, q2_36_i, q3_36_i, q4_36_i = 0, 0, 0, 0
                        evidencia_36 = raw_link_36

                        if "|LINK:" in raw_link_36:
                            partes_36, evidencia_36 = raw_link_36.split("|LINK:", 1)
                            m_q1 = re.search(r"Q1:(\d+)", partes_36)
                            m_q2 = re.search(r"Q2:(\d+)", partes_36)
                            m_q3 = re.search(r"Q3:(\d+)", partes_36)
                            m_q4 = re.search(r"Q4:(\d+)", partes_36)

                            q1_36_i = int(m_q1.group(1)) if m_q1 else 0
                            q2_36_i = int(m_q2.group(1)) if m_q2 else 0
                            q3_36_i = int(m_q3.group(1)) if m_q3 else 0
                            q4_36_i = int(m_q4.group(1)) if m_q4 else 0

                        state_36 = {
                            "q1": q1_36_i,
                            "q2": q2_36_i,
                            "q3": q3_36_i,
                            "q4": q4_36_i,
                            "link": evidencia_36,
                        }

                        def calc_pts_36():
                            c1 = int(state_36["q1"] or 0)
                            c2 = int(state_36["q2"] or 0)
                            c3 = int(state_36["q3"] or 0)
                            c4 = int(state_36["q4"] or 0)
                            tot = c1 + c2 + c3 + c4

                            if tot <= 0:
                                return 0.0

                            prop1 = c1 / tot
                            prop2 = c2 / tot
                            prop3 = c3 / tot
                            prop4 = c4 / tot

                            return min(3.0 * prop1 + 2.0 * prop2 + 1.0 * prop3 + 0.0 * prop4, 3.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_q1_36 = ui.number("Menor que 20% dos professores (Q1):", value=q1_36_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_36, "q1")
                                inp_q2_36 = ui.number("De 20% a menor que 30% dos professores (Q2):", value=q2_36_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_36, "q2")
                                inp_q3_36 = ui.number("De 30% a menor que 40% dos professores (Q3):", value=q3_36_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_36, "q3")
                                inp_q4_36 = ui.number("Maior ou igual a 40% dos professores (Q4):", value=q4_36_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_36, "q4")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_36,
                                placeholder="Insira relatórios de remoção/lotação docente, histórico de transferência do RH...",
                            ).classes("w-full").props("outlined rows=10").bind_value(
                                state_36, "link"
                            )

                        lbl_pts_36 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.6: {calc_pts_36():.2f} / 3.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_36():
                            lbl_pts_36.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.6: {calc_pts_36():.2f} / 3.0 pontos"
                            )

                        for inp_comp in [inp_q1_36, inp_q2_36, inp_q3_36, inp_q4_36]:
                            inp_comp.on("update:model-value", att_pts_36)

                        def salvar_36():
                            c1, c2, c3, c4 = state_36["q1"], state_36["q2"], state_36["q3"], state_36["q4"]
                            pts = calc_pts_36()
                            composite = f"Q1:{c1},Q2:{c2},Q3:{c3},Q4:{c4}|LINK:{state_36['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.6",
                                valor=f"Q1:{c1} | Q2:{c2} | Q3:{c3} | Q4:{c4}",
                                pontos=pts,
                                link=composite,
                                comentarios=d36.get("comentarios", []),
                                status=d36.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.6", on_click=salvar_36).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.6", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.7 (Permanência do Diretor/Gestor Escolar - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.7 • Permanência do Diretor/Gestor na Mesma Unidade (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Indique a quantidade de escolas municipais divididas pelo tempo de permanência do Diretor/Gestor ao final de 2025:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: NF = (0×Q1 + 0,5×Q2 + 1,0×Q3 + 1,5×Q4 + 1,75×Q5 + 2,0×Q6) | Pmáx = 2.0 pontos.\n"
                            "ℹ Qi = Proporção de escolas na faixa de permanência correspondente."
                        ).classes("text-xs text-gray-400 mb-6 whitespace-pre-line")

                        d37 = res_data.get("3.7") or {}
                        raw_link_37 = str(d37.get("link") or "")

                        q1_37_i, q2_37_i, q3_37_i, q4_37_i, q5_37_i, q6_37_i = 0, 0, 0, 0, 0, 0
                        evidencia_37 = raw_link_37

                        if "|LINK:" in raw_link_37:
                            partes_37, evidencia_37 = raw_link_37.split("|LINK:", 1)
                            m_q1 = re.search(r"Q1:(\d+)", partes_37)
                            m_q2 = re.search(r"Q2:(\d+)", partes_37)
                            m_q3 = re.search(r"Q3:(\d+)", partes_37)
                            m_q4 = re.search(r"Q4:(\d+)", partes_37)
                            m_q5 = re.search(r"Q5:(\d+)", partes_37)
                            m_q6 = re.search(r"Q6:(\d+)", partes_37)

                            q1_37_i = int(m_q1.group(1)) if m_q1 else 0
                            q2_37_i = int(m_q2.group(1)) if m_q2 else 0
                            q3_37_i = int(m_q3.group(1)) if m_q3 else 0
                            q4_37_i = int(m_q4.group(1)) if m_q4 else 0
                            q5_37_i = int(m_q5.group(1)) if m_q5 else 0
                            q6_37_i = int(m_q6.group(1)) if m_q6 else 0

                        state_37 = {
                            "q1": q1_37_i,
                            "q2": q2_37_i,
                            "q3": q3_37_i,
                            "q4": q4_37_i,
                            "q5": q5_37_i,
                            "q6": q6_37_i,
                            "link": evidencia_37,
                        }

                        def calc_pts_37():
                            c1 = int(state_37["q1"] or 0)
                            c2 = int(state_37["q2"] or 0)
                            c3 = int(state_37["q3"] or 0)
                            c4 = int(state_37["q4"] or 0)
                            c5 = int(state_37["q5"] or 0)
                            c6 = int(state_37["q6"] or 0)
                            tot = c1 + c2 + c3 + c4 + c5 + c6

                            if tot <= 0:
                                return 0.0

                            p1, p2, p3 = c1 / tot, c2 / tot, c3 / tot
                            p4, p5, p6 = c4 / tot, c5 / tot, c6 / tot

                            return min(0.0*p1 + 0.5*p2 + 1.0*p3 + 1.5*p4 + 1.75*p5 + 2.0*p6, 2.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_q1_37 = ui.number("Menor que 1 ano (Q1):", value=q1_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q1")
                                inp_q2_37 = ui.number("De 1 ano a menor que 3 anos (Q2):", value=q2_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q2")
                                inp_q3_37 = ui.number("De 3 anos a menor que 5 anos (Q3):", value=q3_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q3")
                                inp_q4_37 = ui.number("De 5 anos a menor que 10 anos (Q4):", value=q4_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q4")
                                inp_q5_37 = ui.number("De 10 anos a menor que 15 anos (Q5):", value=q5_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q5")
                                inp_q6_37 = ui.number("Maior ou igual a 15 anos (Q6):", value=q6_37_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_37, "q6")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_37,
                                placeholder="Insira portarias de designação/nomeação dos gestores, histórico funcional ou atos oficiais...",
                            ).classes("w-full").props("outlined rows=14").bind_value(
                                state_37, "link"
                            )

                        lbl_pts_37 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.7: {calc_pts_37():.2f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_37():
                            lbl_pts_37.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.7: {calc_pts_37():.2f} / 2.0 pontos"
                            )

                        for inp_comp in [inp_q1_37, inp_q2_37, inp_q3_37, inp_q4_37, inp_q5_37, inp_q6_37]:
                            inp_comp.on("update:model-value", att_pts_37)

                        def salvar_37():
                            c1, c2, c3 = state_37["q1"], state_37["q2"], state_37["q3"]
                            c4, c5, c6 = state_37["q4"], state_37["q5"], state_37["q6"]
                            pts = calc_pts_37()

                            composite = f"Q1:{c1},Q2:{c2},Q3:{c3},Q4:{c4},Q5:{c5},Q6:{c6}|LINK:{state_37['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.7",
                                valor=f"Q1:{c1}|Q2:{c2}|Q3:{c3}|Q4:{c4}|Q5:{c5}|Q6:{c6}",
                                pontos=pts,
                                link=composite,
                                comentarios=d37.get("comentarios", []),
                                status=d37.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.7 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.7", on_click=salvar_37).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.7", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 3.8 (Reuniões Periódicas com Pais - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.8 • Reuniões Periódicas com Pais (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os professores realizam reuniões periódicas com os pais dos alunos sobre planejamento/projeto escolar e desempenho/desenvolvimento da criança?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuações: Planejamento e Desempenho (2,0 pts) | Apenas Projeto Político-Pedagógico (1,5 pts) | Apenas Desempenho (1,0 pt) | Não realiza (0,0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d38 = res_data.get("3.8") or {}

                        opcoes_38 = {
                            "Selecione...": 0.0,
                            "Sobre planejamento e desempenho da criança": 2.0,
                            "Apenas sobre o projeto político-pedagógico": 1.5,
                            "Apenas sobre o desempenho da criança": 1.0,
                            "Não realiza reuniões periódicas": 0.0,
                        }

                        val_38_bruto = str(d38.get("valor") or "")
                        val_38_valido = "Selecione..."
                        if val_38_bruto in opcoes_38:
                            val_38_valido = val_38_bruto
                        else:
                            for chave in opcoes_38.keys():
                                if chave != "Selecione..." and chave.startswith(val_38_bruto):
                                    val_38_valido = chave
                                    break

                        raw_link_38 = str(d38.get("link") or "")

                        state_38 = {
                            "opcao": val_38_valido,
                            "link": raw_link_38,
                        }

                        def calc_pts_38():
                            return opcoes_38.get(state_38["opcao"], 0.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_38 = ui.radio(
                                    options=list(opcoes_38.keys()),
                                    value=state_38["opcao"],
                                ).props("color=blue").bind_value(state_38, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_38,
                                placeholder="Insira atas de reuniões, calendários escolares ou convocatórias das escolas...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_38, "link"
                            )

                        lbl_pts_38 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.8: {calc_pts_38():.1f} / 2.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_38():
                            lbl_pts_38.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.8: {calc_pts_38():.1f} / 2.0 pontos"
                            )

                        rad_38.on("update:model-value", att_pts_38)

                        def salvar_38():
                            pts = calc_pts_38()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.8",
                                valor=state_38["opcao"],
                                pontos=pts,
                                link=state_38["link"],
                                comentarios=d38.get("comentarios", []),
                                status=d38.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.8 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.8", on_click=salvar_38).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.8", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.8.1 (Periodicidade das Reuniões com Pais - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.8.1 • Periodicidade das Reuniões (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Qual a periodicidade das reuniões com pais nos Anos Iniciais?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito declaratório associado ao quesito 3.8."
                        ).classes("text-xs text-gray-400 mb-6")

                        d381 = res_data.get("3.8.1") or {}

                        opcoes_381 = {
                            "Selecione...": 0.0,
                            "Mensal": 0.0,
                            "Bimestral": 0.0,
                            "Trimestral": 0.0,
                            "Quadrimestral": 0.0,
                            "Semestral": 0.0,
                            "Anual": 0.0,
                        }

                        val_381_bruto = str(d381.get("valor") or "")
                        val_381_valido = "Selecione..."
                        if val_381_bruto in opcoes_381:
                            val_381_valido = val_381_bruto
                        else:
                            for chave in opcoes_381.keys():
                                if chave != "Selecione..." and chave.startswith(val_381_bruto):
                                    val_381_valido = chave
                                    break

                        raw_link_381 = str(d381.get("link") or "")

                        state_381 = {
                            "opcao": val_381_valido,
                            "link": raw_link_381,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_381 = ui.radio(
                                    options=list(opcoes_381.keys()),
                                    value=state_381["opcao"],
                                ).props("color=blue").bind_value(state_381, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_381,
                                placeholder="Insira o calendário escolar homologado ou regimento interno...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_381, "link"
                            )

                        def salvar_381():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.8.1",
                                valor=state_381["opcao"],
                                pontos=0.0,
                                link=state_381["link"],
                                comentarios=d381.get("comentarios", []),
                                status=d381.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.8.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.8.1", on_click=salvar_381).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.8.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.9 (Média de Dias Letivos - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.9 • Média de Dias Letivos no Ano (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Qual a média de dias letivos do ano de 2025 para as turmas dos Anos Iniciais?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Regra: Se Dias Letivos < 200 → Penalidade de -50.0 pts | Se Dias Letivos ≥ 200 → 0.0 pts."
                        ).classes("text-xs text-gray-400 mb-6")

                        d39 = res_data.get("3.9") or {}
                        raw_link_39 = str(d39.get("link") or "")

                        dias_39_i = 200
                        evidencia_39 = raw_link_39

                        if "|LINK:" in raw_link_39:
                            partes_39, evidencia_39 = raw_link_39.split("|LINK:", 1)
                            m_dias = re.search(r"DIAS:(\d+)", partes_39)
                            dias_39_i = int(m_dias.group(1)) if m_dias else 200
                        elif d39.get("valor") and str(d39.get("valor")).isdigit():
                            dias_39_i = int(d39.get("valor"))

                        state_39 = {
                            "dias": dias_39_i,
                            "link": evidencia_39,
                        }

                        def calc_pts_39():
                            d_v = int(state_39["dias"] or 0)
                            return -50.0 if d_v < 200 else 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                inp_dias_39 = ui.number("Média de dias letivos cumpridos:", value=dias_39_i, min=0, step=1).classes("w-full").props("outlined color=blue").bind_value(state_39, "dias")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=evidencia_39,
                                placeholder="Insira o relatório de cumprimento do calendário escolar homologado...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_39, "link"
                            )

                        lbl_pts_39 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.9: {calc_pts_39():.1f} pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_39():
                            pts = calc_pts_39()
                            cor = "text-red-600" if pts < 0 else "text-green-600"
                            lbl_pts_39.classes(remove="text-red-600 text-green-600", add=cor)
                            lbl_pts_39.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.9: {pts:.1f} pontos"
                            )

                        inp_dias_39.on("update:model-value", att_pts_39)

                        def salvar_39():
                            d_v = int(state_39["dias"] or 0)
                            pts = calc_pts_39()
                            composite = f"DIAS:{d_v}|LINK:{state_39['link']}"

                            save_resposta(
                                ano=ano_sel,
                                qid="3.9",
                                valor=f"{d_v} dias letivos",
                                pontos=pts,
                                link=composite,
                                comentarios=d39.get("comentarios", []),
                                status=d39.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.9 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.9", on_click=salvar_39).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.9", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.10 (Programa de Competências de Leitura e Escrita - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.10 • Programa de Leitura e Escrita (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município utilizou algum programa/atividade/projeto específico que desenvolveu as competências de leitura e escrita de seus alunos na rede municipal?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Pontuação: Sim (12,0 pts) | Não (0,0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d310 = res_data.get("3.10") or {}

                        opcoes_310 = {
                            "Selecione...": 0.0,
                            "Sim": 12.0,
                            "Não": 0.0,
                        }

                        val_310_bruto = str(d310.get("valor") or "")
                        val_310_valido = "Selecione..."
                        if val_310_bruto in opcoes_310:
                            val_310_valido = val_310_bruto
                        else:
                            for chave in opcoes_310.keys():
                                if chave != "Selecione..." and chave.startswith(val_310_bruto):
                                    val_310_valido = chave
                                    break

                        raw_link_310 = str(d310.get("link") or "")

                        state_310 = {
                            "opcao": val_310_valido,
                            "link": raw_link_310,
                        }

                        def calc_pts_310():
                            return opcoes_310.get(state_310["opcao"], 0.0)

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_310 = ui.radio(
                                    options=list(opcoes_310.keys()),
                                    value=state_310["opcao"],
                                ).props("color=blue").bind_value(state_310, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_310,
                                placeholder="Insira o decreto de instituição do programa, material estruturado ou ato da SEMED...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_310, "link"
                            )

                        lbl_pts_310 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.10: {calc_pts_310():.1f} / 12.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_310():
                            lbl_pts_310.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.10: {calc_pts_310():.1f} / 12.0 pontos"
                            )

                        rad_310.on("update:model-value", att_pts_310)

                        def salvar_310():
                            pts = calc_pts_310()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.10",
                                valor=state_310["opcao"],
                                pontos=pts,
                                link=state_310["link"],
                                comentarios=d310.get("comentarios", []),
                                status=d310.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.10 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.10", on_click=salvar_310).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.10", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.10.1 (Descrição do Programa de Leitura e Escrita - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.10.1 • Descrição do Programa/Projeto de Alfabetização").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Descreva o programa/atividade/projeto específico de competências de leitura e escrita desenvolvido:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito descritivo e qualitativo associado ao quesito 3.10."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3101 = res_data.get("3.10.1") or {}
                        desc_3101_i = str(d3101.get("valor") or "")
                        raw_link_3101 = str(d3101.get("link") or "")

                        state_3101 = {
                            "descricao": desc_3101_i,
                            "link": raw_link_3101,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Descrição detalhada do programa/projeto:",
                                value=desc_3101_i,
                                placeholder="Descreva os objetivos, metodologia, público-alvo, materiais didáticos e resultados de alfabetização...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_3101, "descricao"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_3101,
                                placeholder="Insira o projeto pedagógico, material do programa ou relatório de avaliação...",
                            ).classes("w-full").props("outlined rows=6").bind_value(
                                state_3101, "link"
                            )

                        def salvar_3101():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.10.1",
                                valor=state_3101["descricao"],
                                pontos=0.0,
                                link=state_3101["link"],
                                comentarios=d3101.get("comentarios", []),
                                status=d3101.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.10.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.10.1", on_click=salvar_3101).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.10.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.12 (Entrega do Kit Escolar - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.12 • Entrega do Kit Escolar (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do Kit Escolar nas escolas dos Anos Iniciais em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Kit escolar = material escolar e pedagógico."
                        ).classes("text-xs text-gray-400 mb-6")

                        d312 = res_data.get("3.12") or {}
                        d3121 = res_data.get("3.121") or res_data.get("3.12.1") or {}

                        opcoes_312 = {
                            "Selecione...": 0.0,
                            "Sim": 0.0,
                            "O kit escolar permanece no almoxarifado da escola e é retirado no momento do uso pelos alunos": 20.0,
                            "Não": 0.0,
                        }

                        val_312_bruto = str(d312.get("valor") or "")
                        val_312_valido = "Selecione..."
                        if val_312_bruto in opcoes_312:
                            val_312_valido = val_312_bruto
                        else:
                            for chave in opcoes_312.keys():
                                if chave != "Selecione..." and chave.startswith(val_312_bruto):
                                    val_312_valido = chave
                                    break

                        raw_link_312 = str(d312.get("link") or "")

                        state_312 = {
                            "opcao": val_312_valido,
                            "link": raw_link_312,
                        }

                        def calc_pts_312():
                            op = state_312["opcao"]
                            if op == "O kit escolar permanece no almoxarifado da escola e é retirado no momento do uso pelos alunos":
                                return 20.0
                            elif op == "Sim":
                                dt_entrega_str = str(d3121.get("valor") or "")
                                dt_link_str = str(d3121.get("link") or "")

                                dt_aulas_str = "2025-02-03"
                                if "AULAS:" in dt_link_str:
                                    dt_aulas_str = dt_link_str.replace("AULAS:", "").strip()

                                try:
                                    d_aulas = datetime.strptime(dt_aulas_str, "%Y-%m-%d")
                                    d_ent = datetime.strptime(dt_entrega_str, "%Y-%m-%d")
                                    d_limite = d_aulas + timedelta(days=15)

                                    if d_ent <= d_aulas:
                                        return 20.0
                                    elif d_ent < d_limite:
                                        return 10.0
                                    else:
                                        return 4.0
                                except Exception:
                                    return 20.0
                            return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_312 = ui.radio(
                                    options=list(opcoes_312.keys()),
                                    value=state_312["opcao"],
                                ).props("color=blue").bind_value(state_312, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_312,
                                placeholder="Insira termos de recebimento, notas fiscais de entrega ou relatórios de distribuição...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_312, "link"
                            )

                        lbl_pts_312 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.12: {calc_pts_312():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_312():
                            lbl_pts_312.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.12: {calc_pts_312():.1f} / 20.0 pontos"
                            )

                        rad_312.on("update:model-value", att_pts_312)

                        def salvar_312():
                            pts = calc_pts_312()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.12",
                                valor=state_312["opcao"],
                                pontos=pts,
                                link=state_312["link"],
                                comentarios=d312.get("comentarios", []),
                                status=d312.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.12 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.12", on_click=salvar_312).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.12", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.12.1 (Data da Última Entrega do Kit Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.12.1 • Data da Última Entrega do Kit Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega na escola:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: ≤ Início das Aulas (20,0 pts) | < Início + 15 dias (10,0 pts) | ≥ Início + 15 dias (4,0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3121 = res_data.get("3.121") or res_data.get("3.12.1") or {}
                        raw_link_3121 = str(d3121.get("link") or "")

                        dt_aulas_3121 = "2025-02-03"
                        if "AULAS:" in raw_link_3121:
                            dt_aulas_3121 = raw_link_3121.replace("AULAS:", "").strip()

                        dt_entrega_3121 = str(d3121.get("valor") or "2025-02-03")

                        state_3121 = {
                            "dt_aulas": dt_aulas_3121,
                            "dt_entrega": dt_entrega_3121,
                        }

                        def calc_pts_3121():
                            try:
                                d_aulas = datetime.strptime(state_3121["dt_aulas"], "%Y-%m-%d")
                                d_ent = datetime.strptime(state_3121["dt_entrega"], "%Y-%m-%d")
                                d_limite = d_aulas + timedelta(days=15)

                                if d_ent <= d_aulas:
                                    return 20.0
                                elif d_ent < d_limite:
                                    return 10.0
                                else:
                                    return 4.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            inp_dt_aulas_3121 = ui.input(
                                "Data de início das aulas:",
                                value=state_3121["dt_aulas"]
                            ).props("type=date outlined color=blue").bind_value(state_3121, "dt_aulas")

                            inp_dt_entrega_3121 = ui.input(
                                "Data da última entrega do kit escolar:",
                                value=state_3121["dt_entrega"]
                            ).props("type=date outlined color=blue").bind_value(state_3121, "dt_entrega")

                        lbl_pts_3121 = ui.label(
                            f"📊 Impacto Estimado na Pontuação: {calc_pts_3121():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_3121():
                            lbl_pts_3121.set_text(
                                f"📊 Impacto Estimado na Pontuação: {calc_pts_3121():.1f} / 20.0 pontos"
                            )

                        inp_dt_aulas_3121.on("update:model-value", att_pts_3121)
                        inp_dt_entrega_3121.on("update:model-value", att_pts_3121)

                        def salvar_3121():
                            pts = calc_pts_3121()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.12.1",
                                valor=state_3121["dt_entrega"],
                                pontos=pts,
                                link=f"AULAS:{state_3121['dt_aulas']}",
                                comentarios=d3121.get("comentarios", []),
                                status=d3121.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.12.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.12.1", on_click=salvar_3121).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.12.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.12.2 (Motivo da Não Entrega do Kit Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.12.2 • Motivo da Não Entrega do Kit Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o motivo de não ter sido entregue o kit escolar:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito descritivo preenchido quando o quesito 3.12 é respondido como 'Não'."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3122 = res_data.get("3.122") or res_data.get("3.12.2") or {}
                        motivo_3122_i = str(d3122.get("valor") or "")
                        raw_link_3122 = str(d3122.get("link") or "")

                        state_3122 = {
                            "motivo": motivo_3122_i,
                            "link": raw_link_3122,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_3122_i,
                                placeholder="Ex: Atraso no processo licitatório, atraso na logística do fornecedor ou problemas na entrega dos insumos...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3122, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_3122,
                                placeholder="Insira notificações de atraso, pareceres de licitação ou justificativas oficiais...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3122, "link"
                            )

                        def salvar_3122():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.12.2",
                                valor=state_3122["motivo"],
                                pontos=0.0,
                                link=state_3122["link"],
                                comentarios=d3122.get("comentarios", []),
                                status=d3122.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.12.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.12.2", on_click=salvar_3122).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.12.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.13 (Entrega do Material Didático - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.13 • Entrega do Material Didático (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do material didático (livros, apostilas, etc.) nas escolas dos Anos Iniciais em 2025?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Apostilas/livros fornecidos pelo Município e/ou Estado e/ou Governo Federal (PNLD)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d313 = res_data.get("3.13") or {}
                        d3131 = res_data.get("3.13.1") or {}

                        opcoes_313 = {
                            "Selecione...": 0.0,
                            "Sim": 0.0,
                            "Não": 0.0,
                        }

                        val_313_bruto = str(d313.get("valor") or "")
                        val_313_valido = "Selecione..."
                        if val_313_bruto in opcoes_313:
                            val_313_valido = val_313_bruto
                        else:
                            for chave in opcoes_313.keys():
                                if chave != "Selecione..." and chave.startswith(val_313_bruto):
                                    val_313_valido = chave
                                    break

                        raw_link_313 = str(d313.get("link") or "")

                        state_313 = {
                            "opcao": val_313_valido,
                            "link": raw_link_313,
                        }

                        def calc_pts_313():
                            if state_313["opcao"] == "Sim":
                                dt_entrega_str = str(d3131.get("valor") or "")
                                dt_link_str = str(d3131.get("link") or "")

                                dt_aulas_str = "2025-02-03"
                                if "AULAS:" in dt_link_str:
                                    dt_aulas_str = dt_link_str.replace("AULAS:", "").strip()

                                try:
                                    d_aulas = datetime.strptime(dt_aulas_str, "%Y-%m-%d")
                                    d_ent = datetime.strptime(dt_entrega_str, "%Y-%m-%d")
                                    d_limite = d_aulas + timedelta(days=15)

                                    if d_ent <= d_aulas:
                                        return 20.0
                                    elif d_ent < d_limite:
                                        return 10.0
                                    else:
                                        return 4.0
                                except Exception:
                                    return 20.0
                            return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            with ui.column().classes("w-full gap-3"):
                                rad_313 = ui.radio(
                                    options=list(opcoes_313.keys()),
                                    value=state_313["opcao"],
                                ).props("color=blue").bind_value(state_313, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_313,
                                placeholder="Insira comprovantes de entrega do FNDE, guiamento de distribuição ou protocolo das unidades...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_313, "link"
                            )

                        lbl_pts_313 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.13: {calc_pts_313():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_313():
                            lbl_pts_313.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.13: {calc_pts_313():.1f} / 20.0 pontos"
                            )

                        rad_313.on("update:model-value", att_pts_313)

                        def salvar_313():
                            pts = calc_pts_313()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.13",
                                valor=state_313["opcao"],
                                pontos=pts,
                                link=state_313["link"],
                                comentarios=d313.get("comentarios", []),
                                status=d313.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.13 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.13", on_click=salvar_313).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.13", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.13.1 (Data da Última Entrega do Material Didático)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.13.1 • Data da Última Entrega do Material Didático").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega na escola:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: ≤ Início das Aulas (20,0 pts) | < Início + 15 dias (10,0 pts) | ≥ Início + 15 dias (4,0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3131 = res_data.get("3.131") or res_data.get("3.13.1") or {}
                        raw_link_3131 = str(d3131.get("link") or "")

                        dt_aulas_3131 = "2025-02-03"
                        if "AULAS:" in raw_link_3131:
                            dt_aulas_3131 = raw_link_3131.replace("AULAS:", "").strip()

                        dt_entrega_3131 = str(d3131.get("valor") or "2025-02-03")

                        state_3131 = {
                            "dt_aulas": dt_aulas_3131,
                            "dt_entrega": dt_entrega_3131,
                        }

                        def calc_pts_3131():
                            try:
                                d_aulas = datetime.strptime(state_3131["dt_aulas"], "%Y-%m-%d")
                                d_ent = datetime.strptime(state_3131["dt_entrega"], "%Y-%m-%d")
                                d_limite = d_aulas + timedelta(days=15)

                                if d_ent <= d_aulas:
                                    return 20.0
                                elif d_ent < d_limite:
                                    return 10.0
                                else:
                                    return 4.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            inp_dt_aulas_3131 = ui.input(
                                "Data de início das aulas:",
                                value=state_3131["dt_aulas"]
                            ).props("type=date outlined color=blue").bind_value(state_3131, "dt_aulas")

                            inp_dt_entrega_3131 = ui.input(
                                "Data da última entrega do material didático:",
                                value=state_3131["dt_entrega"]
                            ).props("type=date outlined color=blue").bind_value(state_3131, "dt_entrega")

                        lbl_pts_3131 = ui.label(
                            f"📊 Impacto Estimado na Pontuação: {calc_pts_3131():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_3131():
                            lbl_pts_3131.set_text(
                                f"📊 Impacto Estimado na Pontuação: {calc_pts_3131():.1f} / 20.0 pontos"
                            )

                        inp_dt_aulas_3131.on("update:model-value", att_pts_3131)
                        inp_dt_entrega_3131.on("update:model-value", att_pts_3131)

                        def salvar_3131():
                            pts = calc_pts_3131()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.13.1",
                                valor=state_3131["dt_entrega"],
                                pontos=pts,
                                link=f"AULAS:{state_3131['dt_aulas']}",
                                comentarios=d3131.get("comentarios", []),
                                status=d3131.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.13.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.13.1", on_click=salvar_3131).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.13.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.13.2 (Motivo da Não Entrega do Material Didático)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.13.2 • Motivo da Não Entrega do Material Didático").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o motivo de não ter sido entregue o material didático:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito descritivo preenchido quando o quesito 3.13 é respondido como 'Não'."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3132 = res_data.get("3.132") or res_data.get("3.13.2") or {}
                        motivo_3132_i = str(d3132.get("valor") or "")
                        raw_link_3132 = str(d3132.get("link") or "")

                        state_3132 = {
                            "motivo": motivo_3132_i,
                            "link": raw_link_3132,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_3132_i,
                                placeholder="Ex: Atraso na distribuição do FNDE/PNLD ou problemas no processo licitatório de apostilas municipais...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3132, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_3132,
                                placeholder="Insira relatórios de pendência do FNDE, pareceres ou justificativas oficiais...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3132, "link"
                            )

                        def salvar_3132():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.13.2",
                                valor=state_3132["motivo"],
                                pontos=0.0,
                                link=state_3132["link"],
                                comentarios=d3132.get("comentarios", []),
                                status=d3132.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.13.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.13.2", on_click=salvar_3132).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.13.2", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 3.14 (Entrega de Uniforme Escolar - Anos Iniciais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.14 • Entrega do Uniforme Escolar (Anos Iniciais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Houve entrega do uniforme escolar nas escolas dos Anos Iniciais em 2025?"
                        ).classes("text-base font-bold text-black mb-6")

                        d314 = res_data.get("3.14") or {}
                        d3141 = res_data.get("3.141") or res_data.get("3.14.1") or {}

                        opcoes_314 = {
                            "Selecione...": 0.0,
                            "Sim": 0.0,
                            "Não": 0.0,
                        }

                        val_314_bruto = str(d314.get("valor") or "")
                        val_314_valido = "Selecione..."
                        if val_314_bruto in opcoes_314:
                            val_314_valido = val_314_bruto

                        raw_link_314 = str(d314.get("link") or "")

                        state_314 = {
                            "opcao": val_314_valido,
                            "link": raw_link_314,
                        }

                        def calc_pts_314():
                            op = state_314["opcao"]
                            if op == "Sim":
                                dt_entrega_str = str(d3141.get("valor") or "")
                                dt_link_str = str(d3141.get("link") or "")

                                dt_aulas_str = "2025-02-03"
                                if "AULAS:" in dt_link_str:
                                    dt_aulas_str = dt_link_str.replace("AULAS:", "").strip()

                                try:
                                    d_aulas = datetime.strptime(dt_aulas_str, "%Y-%m-%d")
                                    d_ent = datetime.strptime(dt_entrega_str, "%Y-%m-%d")
                                    d_limite = d_aulas + timedelta(days=60)

                                    if d_ent <= d_aulas:
                                        return 20.0
                                    elif d_ent < d_limite:
                                        return 10.0
                                    else:
                                        return 4.0
                                except Exception:
                                    return 20.0
                            return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_314 = ui.radio(
                                options=list(opcoes_314.keys()),
                                value=state_314["opcao"],
                            ).props("color=blue").bind_value(state_314, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_314,
                                placeholder="Insira termos de recebimento ou relatórios de distribuição de uniforme...",
                            ).classes("w-full").props("outlined rows=3").bind_value(
                                state_314, "link"
                            )

                        lbl_pts_314 = ui.label(
                            f"📊 Impacto de Pontuação no Quesito 3.14: {calc_pts_314():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_314():
                            lbl_pts_314.set_text(
                                f"📊 Impacto de Pontuação no Quesito 3.14: {calc_pts_314():.1f} / 20.0 pontos"
                            )

                        rad_314.on("update:model-value", att_pts_314)

                        def salvar_314():
                            pts = calc_pts_314()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.14",
                                valor=state_314["opcao"],
                                pontos=pts,
                                link=state_314["link"],
                                comentarios=d314.get("comentarios", []),
                                status=d314.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.14 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.14", on_click=salvar_314).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.14", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.14.1 (Data da Última Entrega do Uniforme Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.14.1 • Data da Última Entrega do Uniforme Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a data da última entrega na escola:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Fórmula: ≤ Início das Aulas (20,0 pts) | < Início + 60 dias (10,0 pts) | ≥ Início + 60 dias (4,0 pts)."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3141 = res_data.get("3.141") or res_data.get("3.14.1") or {}
                        raw_link_3141 = str(d3141.get("link") or "")

                        dt_aulas_3141 = "2025-02-03"
                        if "AULAS:" in raw_link_3141:
                            dt_aulas_3141 = raw_link_3141.replace("AULAS:", "").strip()

                        dt_entrega_3141 = str(d3141.get("valor") or "2025-02-03")

                        state_3141 = {
                            "dt_aulas": dt_aulas_3141,
                            "dt_entrega": dt_entrega_3141,
                        }

                        def calc_pts_3141():
                            try:
                                d_aulas = datetime.strptime(state_3141["dt_aulas"], "%Y-%m-%d")
                                d_ent = datetime.strptime(state_3141["dt_entrega"], "%Y-%m-%d")
                                d_limite = d_aulas + timedelta(days=60)

                                if d_ent <= d_aulas:
                                    return 20.0
                                elif d_ent < d_limite:
                                    return 10.0
                                else:
                                    return 4.0
                            except Exception:
                                return 0.0

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            inp_dt_aulas_3141 = ui.input(
                                "Data de início das aulas:",
                                value=state_3141["dt_aulas"]
                            ).props("type=date outlined color=blue").bind_value(state_3141, "dt_aulas")

                            inp_dt_entrega_3141 = ui.input(
                                "Data da última entrega do uniforme:",
                                value=state_3141["dt_entrega"]
                            ).props("type=date outlined color=blue").bind_value(state_3141, "dt_entrega")

                        lbl_pts_3141 = ui.label(
                            f"📊 Impacto Estimado na Pontuação: {calc_pts_3141():.1f} / 20.0 pontos"
                        ).classes("text-sm font-bold text-green-600 my-4")

                        def att_pts_3141():
                            lbl_pts_3141.set_text(
                                f"📊 Impacto Estimado na Pontuação: {calc_pts_3141():.1f} / 20.0 pontos"
                            )

                        inp_dt_aulas_3141.on("update:model-value", att_pts_3141)
                        inp_dt_entrega_3141.on("update:model-value", att_pts_3141)

                        def salvar_3141():
                            pts = calc_pts_3141()
                            save_resposta(
                                ano=ano_sel,
                                qid="3.14.1",
                                valor=state_3141["dt_entrega"],
                                pontos=pts,
                                link=f"AULAS:{state_3141['dt_aulas']}",
                                comentarios=d3141.get("comentarios", []),
                                status=d3141.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.14.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.14.1", on_click=salvar_3141).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.14.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.14.2 (Motivo da Não Entrega do Uniforme Escolar)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.14.2 • Motivo da Não Entrega do Uniforme Escolar").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o motivo de não ter sido entregue o uniforme escolar:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Quesito descritivo preenchido quando o quesito 3.14 é respondido como 'Não'."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3142 = res_data.get("3.142") or res_data.get("3.14.2") or {}
                        motivo_3142_i = str(d3142.get("valor") or "")
                        raw_link_3142 = str(d3142.get("link") or "")

                        state_3142 = {
                            "motivo": motivo_3142_i,
                            "link": raw_link_3142,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Motivo da não entrega:",
                                value=motivo_3142_i,
                                placeholder="Ex: Atraso no processo licitatório, fracasso na licitação...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_3142, "motivo"
                            )

                            ui.textarea(
                                label="Link de Evidência / Documento:",
                                value=raw_link_3122,
                                placeholder="Insira notificações de atraso, processos administrativos ou justificativas...",
                            ).classes("w-full").props("outlined rows=4").bind_value(
                                state_3142, "link"
                            )

                        def salvar_3142():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.14.2",
                                valor=state_3142["motivo"],
                                pontos=0.0,
                                link=state_3142["link"],
                                comentarios=d3142.get("comentarios", []),
                                status=d3142.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.14.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.14.2", on_click=salvar_3142).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.14.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.15 (Indicador Próprio de Qualidade de Ensino)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.15 • Indicador Próprio de Qualidade de Ensino").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O Município possui seu próprio indicador de qualidade de ensino?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Indicador próprio é o indicador desenvolvido pela própria prefeitura com a colaboração "
                            "de profissionais de grande conhecimento sobre escolas públicas e políticas educacionais, "
                            "utilizado para diagnóstico educacional. Não é válido IDEB (Prova Brasil) nem IDESP (SARESP)."
                        ).classes("text-xs text-gray-500 mb-6")

                        d315 = res_data.get("3.15") or {}

                        opcoes_315 = {
                            "Selecione...": 0.0,
                            "Sim": 10.0,
                            "Não": 0.0,
                        }

                        val_315_bruto = str(d315.get("valor") or "")
                        val_315_valido = "Selecione..."
                        if val_315_bruto in opcoes_315:
                            val_315_valido = val_315_bruto

                        raw_link_315 = str(d315.get("link") or "")

                        state_315 = {
                            "opcao": val_315_valido,
                            "link": raw_link_315,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_315 = ui.radio(
                                options=list(opcoes_315.keys()),
                                value=state_315["opcao"],
                            ).props("color=blue").bind_value(state_315, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Legislação / Decreto:",
                                value=raw_link_315,
                                placeholder="Link do ato normativo, publicação oficial ou documento de instituição do indicador...",
                            ).classes("w-full").props("outlined rows=3").bind_value(
                                state_315, "link"
                            )

                        def salvar_315():
                            pts = opcoes_315.get(state_315["opcao"], 0.0)
                            save_resposta(
                                ano=ano_sel,
                                qid="3.15",
                                valor=state_315["opcao"],
                                pontos=pts,
                                link=state_315["link"],
                                comentarios=d315.get("comentarios", []),
                                status=d315.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.15 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.15", on_click=salvar_315).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.15", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.15.1 (Composição e Avaliação do Indicador Próprio)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.15.1 • Composição e Forma de Avaliação do Indicador Próprio").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Especifique, descrevendo sua composição e forma de avaliação:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Descreva as métricas, disciplinas contempladas, periodicidade e como os dados são consolidados."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3151 = res_data.get("3.151") or res_data.get("3.15.1") or {}
                        desc_3151_i = str(d3151.get("valor") or "")
                        raw_link_3151 = str(d3151.get("link") or "")

                        state_3151 = {
                            "descricao": desc_3151_i,
                            "link": raw_link_3151,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            ui.textarea(
                                label="Composição e forma de avaliação do indicador próprio:",
                                value=desc_3151_i,
                                placeholder="Detalhe os componentes (ex: testes de fluência leitora, provas de matemática), metodologias e pesos...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3151, "descricao"
                            )

                            ui.textarea(
                                label="Link de Evidência / Metodologia / Documento:",
                                value=raw_link_3151,
                                placeholder="Link do documento técnico, manual do indicador ou portaria de diretrizes...",
                            ).classes("w-full").props("outlined rows=5").bind_value(
                                state_3151, "link"
                            )

                        def salvar_3151():
                            save_resposta(
                                ano=ano_sel,
                                qid="3.15.1",
                                valor=state_3151["descricao"],
                                pontos=0.0,
                                link=state_3151["link"],
                                comentarios=d3151.get("comentarios", []),
                                status=d3151.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.15.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.15.1", on_click=salvar_3151).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.15.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 3.15.2 (Classificação de Alunos por Nível de Desempenho)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("3.15.2 • Classificação por Nível de Desempenho").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O indicador próprio de qualidade de ensino do Município classifica os alunos por nível de desempenho?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "ℹ Exemplo de níveis: Abaixo do Básico, Básico, Adequado e Avançado."
                        ).classes("text-xs text-gray-400 mb-6")

                        d3152 = res_data.get("3.152") or res_data.get("3.15.2") or {}

                        opcoes_3152 = {
                            "Selecione...": 0.0,
                            "Sim": 5.0,
                            "Não": 0.0,
                        }

                        val_3152_bruto = str(d3152.get("valor") or "")
                        val_3152_valido = "Selecione..."
                        if val_3152_bruto in opcoes_3152:
                            val_3152_valido = val_3152_bruto

                        raw_link_3152 = str(d3152.get("link") or "")

                        state_3152 = {
                            "opcao": val_3152_valido,
                            "link": raw_link_3152,
                        }

                        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                            rad_3152 = ui.radio(
                                options=list(opcoes_3152.keys()),
                                value=state_3152["opcao"],
                            ).props("color=blue").bind_value(state_3152, "opcao")

                            ui.textarea(
                                label="Link de Evidência / Relatório com Níveis:",
                                value=raw_link_3152,
                                placeholder="Link de boletins pedagógicos, relatórios consolidados por escala de proficiência...",
                            ).classes("w-full").props("outlined rows=3").bind_value(
                                state_3152, "link"
                            )

                        def salvar_3152():
                            pts = opcoes_3152.get(state_3152["opcao"], 0.0)
                            save_resposta(
                                ano=ano_sel,
                                qid="3.15.2",
                                valor=state_3152["opcao"],
                                pontos=pts,
                                link=state_3152["link"],
                                comentarios=d3152.get("comentarios", []),
                                status=d3152.get("status", "Pendente"),
                            )

                            ui.notify("Quesito 3.15.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 3.15.2", on_click=salvar_3152).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("3.15.2", res_data, render_conteudo.refresh)

    render_conteudo()
    return main_container

