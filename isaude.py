import base64
from datetime import datetime
import json
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from nicegui import app, ui

# =============================================================================
# BANCO DE DADOS (NEON - ESTRUTURA REAL RESPOSTAS_ISAUDE)
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
        FROM respostas_isaude
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
        print(f"❌ Erro ao carregar respostas de respostas_isaude: {e}")
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
        INSERT INTO respostas_isaude (ano, quesito, resposta, pontos, link, comentario)
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
                    f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela respostas_isaude!"
                )
    except Exception as e:
        print(f"❌ Erro ao salvar resposta na tabela respostas_isaude: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_isaude WHERE ano = %s;"
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
                        label="Nº de unidades com BPI:",
                        value=state["opcao"].get("bpi", 0),
                        min=0,
                    ).classes("w-full").props("outlined color=blue")

                    in_total = ui.number(
                        label="Nº total de unidades no município:",
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
        ui.label("🛠️ Painel de Controle (i-Saúde)").classes(
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
# MÓDULO PRINCIPAL DE REQUISITOS (ISAUDE)
# =============================================================================
def container_formulario_saude(ano=None):
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
                        ui.label(f"📋 Módulo i-Saúde — Ano {ano_sel}").classes(
                            "text-xl font-bold text-slate-800 border-b pb-2"
                        )


# Aliases para compatibilidade com o roteamento no main.py
container_formulario_isaude = container_formulario_saude
mostrar_formulario_saude = container_formulario_saude
main = container_formulario_saude
