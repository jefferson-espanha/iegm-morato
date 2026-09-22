import base64
from datetime import datetime
import json
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from nicegui import app, ui

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
    """Carrega as respostas do banco Neon utilizando a estrutura real com a coluna 'detalhes' (JSONB)."""
    query = """
        SELECT id, ano, quesito, resposta, pontos, detalhes
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

                    # Extração dos dados contidos na coluna JSONB 'detalhes'
                    detalhes_raw = row.get("detalhes") or {}
                    if isinstance(detalhes_raw, str):
                        try:
                            detalhes_raw = json.loads(detalhes_raw)
                        except Exception:
                            detalhes_raw = {}
                    elif not isinstance(detalhes_raw, dict):
                        detalhes_raw = {}

                    link_val = detalhes_raw.get("link", "")
                    
                    # Trata variações de nomenclatura nos comentários dentro do JSON
                    comentarios_val = detalhes_raw.get("comentarios", detalhes_raw.get("comentario", []))
                    if isinstance(comentarios_val, str):
                        try:
                            comentarios_val = json.loads(comentarios_val)
                        except Exception:
                            comentarios_val = []
                    if not isinstance(comentarios_val, list):
                        comentarios_val = []

                    status_val = detalhes_raw.get("status", "Pendente")

                    respostas[q_id] = {
                        "valor": val_final,
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": link_val,
                        "comentarios": comentarios_val,
                        "status": status_val,
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas de respostas_isaude: {e}")
    return respostas


def save_resposta(
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    """Salva/Atualiza a resposta no banco Neon na coluna 'detalhes' (JSONB)."""
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = _obter_lista_comentarios(dados_atuais)

    link_final = link.strip() if link else ""

    if isinstance(valor, (list, dict)):
        resposta_str = json.dumps(valor, ensure_ascii=False)
    else:
        resposta_str = str(valor) if valor is not None else ""

    detalhes_obj = {
        "link": link_final,
        "comentarios": comentarios if isinstance(comentarios, list) else [],
        "status": status
    }
    detalhes_json = json.dumps(detalhes_obj, ensure_ascii=False)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Tenta atualizar se o registro já existir
                cur.execute(
                    """
                    UPDATE respostas_isaude
                    SET resposta = %s, pontos = %s, detalhes = %s
                    WHERE ano = %s AND quesito = %s;
                    """,
                    (resposta_str, float(pontos), detalhes_json, int(ano), str(qid)),
                )
                
                # 2. Se não atualizou nenhuma linha, faz o INSERT
                if cur.rowcount == 0:
                    cur.execute(
                        """
                        INSERT INTO respostas_isaude (ano, quesito, resposta, pontos, detalhes)
                        VALUES (%s, %s, %s, %s, %s);
                        """,
                        (int(ano), str(qid), resposta_str, float(pontos), detalhes_json),
                    )
                conn.commit()
                print(
                    f"✅ Quesito {qid} ({ano}) salvo com sucesso na tabela respostas_isaude!"
                )
                return True
    except Exception as e:
        print(f"❌ Erro ao salvar resposta na tabela respostas_isaude: {e}")
        ui.notify(f"❌ Erro no Banco de Dados: {e}", type="negative")
        return False


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_isaude WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no DB: {e}")


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
                        ui.checkbox(
                            text=opt_key,
                            value=is_checked,
                            on_change=make_on_change(opt_key)
                        ).props("color=blue")

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

            sucesso = save_resposta(
                ano=ano,
                qid=qid,
                valor=opcao_sel,
                pontos=pts,
                link=lnk,
                comentarios=dados_q.get("comentarios", []),
                status=dados_q.get("status", "Pendente"),
            )
            if sucesso:
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
                            "text-xl font-bold text-slate-800 border-b pb-2 mb-4"
                        )

                        # ==========================================
                        # QUESITO 1.0
                        # ==========================================
                        opcoes_1_0 = {
                            "Selecione...": 0.0,
                            "Sim, com propostas para construção das diretrizes e metas da saúde municipal – 05": 5.0,
                            "Sim, apenas aprovando as propostas da gestão (Secretaria Municipal) – 02": 2.0,
                            "Não – 00": 0.0,
                        }

                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            qid="1.0",
                            titulo="Elaboração do Plano Municipal de Saúde",
                            pergunta="O Conselho Municipal de Saúde participou da elaboração do Plano Municipal de Saúde 2026-2029?",
                            tipo_input="radio",
                            opcoes=opcoes_1_0,
                            placeholder_link="Insira o link da ata da reunião do CMS ou documento comprovatório...",
                            on_save_callback=render_conteudo.refresh,
                        )

                        # ==========================================
                        # QUESITO 2.0
                        # ==========================================
                        opcoes_2_0 = {
                            "Selecione...": 0.0,
                            "Até prazo de envio à Câmara Municipal do projeto de lei sobre PPA 2026-2029 – 10": 10.0,
                            "Aprovado após prazo de envio à Câmara Municipal do projeto de lei sobre o PPA 2026-2029, mas antes da aprovação do PPA 2026-2029 pela Câmara Municipal – 07": 7.0,
                            "Aprovado após a aprovação do PPA 2026-2029 pela Câmara Municipal – 03": 3.0,
                            "Não aprovado – 00": 0.0,
                        }

                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            qid="2.0",
                            titulo="Aprovação do Plano Municipal de Saúde",
                            pergunta="Quando ocorreu a aprovação do Plano Municipal de Saúde 2026-2029 pelo Conselho Municipal da Saúde?",
                            tipo_input="radio",
                            opcoes=opcoes_2_0,
                            placeholder_link="Insira o link da resolução de aprovação do CMS ou ata...",
                            on_save_callback=render_conteudo.refresh,
                        )

                        # ==========================================
                        # QUESITO 3.0
                        # ==========================================
                        opcoes_3_0 = {
                            "Selecione...": 0.0,
                            "Até prazo de envio à Câmara Municipal do projeto de lei de diretrizes orçamentárias 2025 – 10": 10.0,
                            "Aprovado após prazo de envio à Câmara Municipal do projeto de lei de diretrizes orçamentárias 2025, mas antes da aprovação da LDO 2025 pela Câmara Municipal – 07": 7.0,
                            "Aprovado após a aprovação da LDO 2025 pela Câmara Municipal – 03": 3.0,
                            "Não aprovado – 00": 0.0,
                        }

                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            qid="3.0",
                            titulo="Aprovação da Programação Anual de Saúde",
                            pergunta="Quando ocorreu a aprovação da Programação Anual de Saúde de 2025 pelo Conselho Municipal de Saúde?",
                            tipo_input="radio",
                            opcoes=opcoes_3_0,
                            placeholder_link="Insira o link do ato de aprovação/resolução do CMS...",
                            on_save_callback=render_conteudo.refresh,
                        )

                        # ==========================================
                        # QUESITO 3.1
                        # ==========================================
                        opcoes_3_1 = {
                            "Selecione...": 0.0,
                            "Sim, todas as ações foram executadas – 04": 4.0,
                            "Sim, a maior parte das ações foram executadas – 02": 2.0,
                            "Sim, a menor parte das ações foram executadas – 01": 1.0,
                            "Nenhuma ação foi executada – 00": 0.0,
                        }

                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            qid="3.1",
                            titulo="Execução da Programação Anual de Saúde",
                            pergunta="As ações previstas na Programação Anual de Saúde de 2025 foram executadas?",
                            tipo_input="radio",
                            opcoes=opcoes_3_1,
                            placeholder_link="Insira o link do Relatório Anual de Gestão (RAG) ou monitoramento...",
                            on_save_callback=render_conteudo.refresh,
                        )

                        # ==========================================
                        # QUESITO 3.2
                        # ==========================================
                        opcoes_3_2 = {
                            "Selecione...": 0.0,
                            "Sim, todas as metas foram atingidas – 04": 4.0,
                            "Sim, a maior parte das metas foram atingidas – 02": 2.0,
                            "Sim, a menor parte das metas foram atingidas – 01": 1.0,
                            "Não – 00": 0.0,
                        }

                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            qid="3.2",
                            titulo="Metas dos Indicadores da PAS",
                            pergunta="As metas previstas para os indicadores foram atingidas na Programação Anual de Saúde de 2025?",
                            tipo_input="radio",
                            opcoes=opcoes_3_2,
                            placeholder_link="Insira o link da avaliação de indicadores / RAG 2025...",
                            on_save_callback=render_conteudo.refresh,
                        )

    # ==========================================
                    # QUESITO 4.0
                    # ==========================================
                    opcoes_4_0 = {
                        "Para escolas – 2,5": 2.5,
                        "Para outras secretarias / entidades municipais – 01": 1.0,
                        "Para membros do Conselho Municipal de Saúde – 01": 1.0,
                        "Para munícipes ou empresas – 1,5": 1.5,
                        "Não ofereceu nenhum curso/treinamento no ano – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.0",
                        titulo="Cursos e Treinamentos em Saúde",
                        pergunta="A Secretaria Municipal de Saúde ou similar ofereceu cursos/treinamento sobre saúde para qual público?",
                        tipo_input="checkbox",
                        opcoes=opcoes_4_0,
                        placeholder_link="Insira o link da comprovação dos cursos/treinamentos oferecidos...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.0
                    # ==========================================
                    opcoes_5_0 = {
                        "Selecione...": 0.0,
                        "Sim – 04": 4.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.0",
                        titulo="Movimentação em Contas Bancárias Próprias",
                        pergunta="Os recursos financeiros municipais (fonte 1) destinados ao Sistema Único de Saúde (SUS) são movimentados em contas bancárias próprias?",
                        tipo_input="radio",
                        opcoes=opcoes_5_0,
                        placeholder_link="Insira o link do extrato ou documento comprobatório...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.1
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.1",
                        titulo="Dados das Contas Bancárias do SUS",
                        pergunta="Informe o Banco, Agência e nº da conta:",
                        tipo_input="text",
                        opcoes={},
                        placeholder_link="Insira o link de comprovantes bancários ou extratos...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 6.0
                    # ==========================================
                    opcoes_6_0 = {
                        "Selecione...": 0.0,
                        "Sim, com responsabilidade específica do setor de saúde e com recursos movimentados exclusivamente pelo Fundo – 05": 5.0,
                        "Sim, com responsabilidade específica do setor de saúde, mas não houve movimentação de recursos exclusivamente pelo Fundo – 03": 3.0,
                        "Sim, com recursos movimentados exclusivamente pelo Fundo, mas sem responsabilidade específica do setor de saúde – 01": 1.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="6.0",
                        titulo="Aplicação de Recursos Próprios em Saúde",
                        pergunta="As despesas consideradas, para fins de apuração do mínimo constitucional de aplicação de recursos próprios em saúde, foram de responsabilidade específica do setor de saúde e com recursos municipais movimentados somente pelo Fundo Municipal de Saúde?",
                        tipo_input="radio",
                        opcoes=opcoes_6_0,
                        placeholder_link="Insira o link de relatórios contábeis ou demonstrativo de saúde...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 7.0
                    # ==========================================
                    opcoes_7_0 = {
                        "Relatório do 1º Quadrimestre - até o final do mês de maio de 2025 – 01": 1.0,
                        "Relatório do 2º Quadrimestre - até o final do mês de setembro de 2025 – 01": 1.0,
                        "Relatório do 3º Quadrimestre - até o final do mês de fevereiro de 2026 – 01": 1.0,
                        "Não apresentou nenhum relatório quadrimestral dentro de prazo – 00": 0.0,
                        "Não apresentou nenhum relatório quadrimestral em audiência pública na Câmara Municipal – -01": -1.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.0",
                        titulo="Apresentação de Relatórios Quadrimestrais",
                        pergunta="O gestor municipal de saúde apresentou quais Relatórios Quadrimestrais de 2025 previstos no art. 36 da Lei Complementar 141/2012 em audiência pública na Câmara Municipal?",
                        tipo_input="checkbox",
                        opcoes=opcoes_7_0,
                        placeholder_link="Insira o link da ata da audiência pública ou edital...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.0
                    # ==========================================
                    opcoes_8_0 = {
                        "Selecione...": 0.0,
                        "Sim, meio eletrônico – 02": 2.0,
                        "Sim, meio físico – 02": 2.0,
                        "Não – 00": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.0",
                        titulo="Encaminhamento do RAG 2025 ao CMS",
                        pergunta="O Relatório Anual de Gestão de 2025 foi encaminhado ao Conselho Municipal de Saúde até 30/03/2026 (ano seguinte ao da execução financeira)?",
                        tipo_input="radio",
                        opcoes=opcoes_8_0,
                        placeholder_link="Insira o link do protocolo ou comprovante de envio ao CMS...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.0
                    # ==========================================
                    opcoes_9_0 = {
                        "Selecione...": 0.0,
                        "Aprovado sem ressalvas – 18": 18.0,
                        "Aprovado com ressalvas – 10": 10.0,
                        "Irregular/Não aprovado – 00": 0.0,
                        "Não apreciado – -10": -10.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.0",
                        titulo="Parecer Conclusivo sobre o RAG 2024",
                        pergunta="O Parecer Conclusivo sobre o Relatório Anual de Gestão 2024 foi 'aprovado sem ressalvas', 'aprovado com ressalvas' ou 'irregular/não aprovado'?",
                        tipo_input="radio",
                        opcoes=opcoes_9_0,
                        placeholder_link="Insira o link da resolução ou parecer do CMS...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.1
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.1",
                        titulo="Publicação do Parecer Conclusivo do RAG 2024",
                        pergunta="Informe a forma e Data da publicação do Parecer Conclusivo sobre o Relatório Anual de Gestão 2024:",
                        tipo_input="text",
                        opcoes={},
                        placeholder_link="Insira o link da publicação do Diário Oficial ou portal...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # =============================================================================
                    # QUESITO 9.2 (Parecer Conclusivo RAG 2024)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("9.2 • Parecer Conclusivo sobre o Relatório Anual de Gestão (RAG 2024)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação do Parecer Conclusivo sobre o Relatório Anual de Gestão 2024:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Se não estiver disponível na internet, inserir no campo Página eletrônica (link na internet) o texto XYZ. (Com XYZ = 00 pts | Diferente de XYZ = 05 pts)."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d92 = res_data.get("9.2") or res_data.get("9_2") or {}
                        raw_link_92 = str(d92.get("link") or "")

                        state_92 = {
                            "link": raw_link_92,
                        }

                        # --- Campo Link / Página Eletrônica ---
                        ui.textarea(
                            label="Página eletrônica (link na internet) do Parecer Conclusivo RAG 2024:",
                            value=raw_link_92,
                            placeholder="Insira o link completo ou o texto XYZ...",
                        ).classes("w-full mb-2").props("outlined rows=2").bind_value(
                            state_92, "link"
                        )

                        lbl_pts_92 = ui.label("Nota Quesito 9.2: 0.0 / 5.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        # --- Lógica de Cálculo de Pontuação (9.2) ---
                        def recalc_pontos_92():
                            link_val = state_92["link"].strip()
                            if link_val.upper() == "XYZ" or not link_val:
                                pts = 0.0
                            else:
                                pts = 5.0

                            lbl_pts_92.set_text(f"📊 Nota Quesito 9.2: {pts:.2f} / 5.0 pontos")
                            return pts

                        # Reação à digitação no campo
                        ui.timer(0.1, recalc_pontos_92, once=True)

                        def salvar_92():
                            pts_totais = recalc_pontos_92()

                            save_resposta(
                                ano=ano_sel,
                                qid="9.2",
                                valor={"link_informado": state_92["link"]},
                                pontos=pts_totais,
                                link=state_92["link"],
                                comentarios=d92.get("comentarios", []),
                                status=d92.get("status", "Pendente"),
                            )

                            ui.notify(f"Quesito 9.2 salvo com sucesso! (Pontuação: {pts_totais:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 9.2", on_click=salvar_92).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("9.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 10.0 (Estabelecimentos de Saúde sob Gestão Municipal)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("10.0 • Estabelecimentos de Saúde sob Gestão Municipal").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Sobre os estabelecimentos de saúde sob gestão municipal, em dezembro de 2025, informe:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Considerar estabelecimentos com atendimento direto à população."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d100 = res_data.get("10.0") or res_data.get("10") or {}
                        raw_val_100 = d100.get("valor") or {}

                        if not isinstance(raw_val_100, dict):
                            raw_val_100 = {}

                        raw_link_100 = str(d100.get("link") or "")

                        state_100 = {
                            "total_estabelecimentos": str(raw_val_100.get("total_estabelecimentos", 0)),
                            "qtd_avcb": str(raw_val_100.get("qtd_avcb", 0)),
                            "qtd_visa": str(raw_val_100.get("qtd_visa", 0)),
                            "qtd_reparos": str(raw_val_100.get("qtd_reparos", 0)),
                            "qtd_interrompidos": str(raw_val_100.get("qtd_interrompidos", 0)),
                            "link": raw_link_100,
                        }

                        # --- Campo Base: Total de Estabelecimentos ---
                        with ui.row().classes("w-full items-center mb-6 bg-blue-50 p-4 rounded-lg border border-blue-200"):
                            ui.label("Quantidade de estabelecimentos de saúde sob gestão municipal (Dez/2025):").classes(
                                "text-sm font-bold text-blue-900 w-2/3"
                            )
                            inp_total_10 = ui.input(
                                value=state_100["total_estabelecimentos"]
                            ).props("type=number outlined dense color=blue").classes("w-1/3 bg-white")
                            inp_total_10.bind_value(state_100, "total_estabelecimentos")

                        ui.separator().classes("mb-6")

                        # --- Item 145: AVCB (Pmáx = 50 pts) ---
                        ui.label("1. Quantidade de estabelecimentos de saúde sob gestão municipal com AVCB:").classes(
                            "text-sm font-bold text-gray-800 mb-2"
                        )
                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Quantidade com AVCB:").classes("text-sm text-gray-700 w-1/2")
                            inp_avcb_10 = ui.input(
                                value=state_100["qtd_avcb"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_avcb_10.bind_value(state_100, "qtd_avcb")

                        lbl_pts_avcb_10 = ui.label("Nota AVCB: 0.0 / 50.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        # --- Item 146: Licença da Vigilância Sanitária (Pmáx = 25 pts) ---
                        ui.label("2. Quantidade de estabelecimentos de saúde sob gestão municipal com licença da Vigilância Sanitária:").classes(
                            "text-sm font-bold text-gray-800 mb-2"
                        )
                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Quantidade com Licença VISA:").classes("text-sm text-gray-700 w-1/2")
                            inp_visa_10 = ui.input(
                                value=state_100["qtd_visa"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_visa_10.bind_value(state_100, "qtd_visa")

                        lbl_pts_visa_10 = ui.label("Nota Licença VISA: 0.0 / 25.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        # --- Item 3: Reparos (Pmáx = 25 pts) ---
                        ui.label("3. Quantidade de estabelecimentos de saúde sob gestão municipal que necessitavam de reparos:").classes(
                            "text-sm font-bold text-gray-800 mb-2"
                        )
                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Quantidade com necessidade de reparos:").classes("text-sm text-gray-700 w-1/2")
                            inp_reparos_10 = ui.input(
                                value=state_100["qtd_reparos"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_reparos_10.bind_value(state_100, "qtd_reparos")

                        lbl_pts_reparos_10 = ui.label("Nota Conservação/Reparos: 0.0 / 25.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        # --- Item 4: Interrupção de Funcionamento (Penalidade Pmáx = -50 pts) ---
                        ui.label("4. Quantidade de estabelecimentos de saúde sob gestão municipal que tiveram seu funcionamento interrompido no ano:").classes(
                            "text-sm font-bold text-gray-800 mb-2"
                        )
                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Quantidade de unidades interrompidas:").classes("text-sm text-gray-700 w-1/2")
                            inp_interrompidos_10 = ui.input(
                                value=state_100["qtd_interrompidos"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_interrompidos_10.bind_value(state_100, "qtd_interrompidos")

                        lbl_pts_interrompidos_10 = ui.label("Penalização Interrupções: 0.0 / -50.0 pontos").classes(
                            "text-sm font-bold text-red-600 mb-6"
                        )

                        # --- Lógica de Cálculo de Pontuações Finais (10.0) ---
                        def recalc_pontos_100():
                            try:
                                total = float(state_100["total_estabelecimentos"])
                            except ValueError:
                                total = 0.0

                            try:
                                qtd_avcb = float(state_100["qtd_avcb"])
                            except ValueError:
                                qtd_avcb = 0.0

                            try:
                                qtd_visa = float(state_100["qtd_visa"])
                            except ValueError:
                                qtd_visa = 0.0

                            try:
                                qtd_reparos = float(state_100["qtd_reparos"])
                            except ValueError:
                                qtd_reparos = 0.0

                            try:
                                qtd_interrompidos = float(state_100["qtd_interrompidos"])
                            except ValueError:
                                qtd_interrompidos = 0.0

                            if total > 0:
                                # Cálculo AVCB: NF = P * 50
                                p_avcb = min(max(qtd_avcb / total, 0.0), 1.0)
                                nf_avcb = p_avcb * 50.0

                                # Cálculo VISA: NF = P * 25
                                p_visa = min(max(qtd_visa / total, 0.0), 1.0)
                                nf_visa = p_visa * 25.0

                                # Cálculo Reparos: NF = (1 - P) * 25
                                p_reparos = min(max(qtd_reparos / total, 0.0), 1.0)
                                nf_reparos = (1.0 - p_reparos) * 25.0

                                # Cálculo Interrupção: N = P * (-50)
                                p_interrompido = min(max(qtd_interrompidos / total, 0.0), 1.0)
                                nf_interrompido = p_interrompido * -50.0
                            else:
                                p_avcb = p_visa = p_reparos = p_interrompido = 0.0
                                nf_avcb = nf_visa = nf_reparos = nf_interrompido = 0.0

                            lbl_pts_avcb_10.set_text(f"📊 Nota AVCB: {nf_avcb:.2f} / 50.0 pontos (Proporção: {(p_avcb*100):.1f}%)")
                            lbl_pts_visa_10.set_text(f"📊 Nota Licença VISA: {nf_visa:.2f} / 25.0 pontos (Proporção: {(p_visa*100):.1f}%)")
                            lbl_pts_reparos_10.set_text(f"📊 Nota Conservação/Reparos: {nf_reparos:.2f} / 25.0 pontos (Proporção com reparos: {(p_reparos*100):.1f}%)")
                            lbl_pts_interrompidos_10.set_text(f"⚠️ Penalidade Interrupções: {nf_interrompido:.2f} / -50.0 pontos (Proporção interrompida: {(p_interrompido*100):.1f}%)")

                            return round(nf_avcb + nf_visa + nf_reparos + nf_interrompido, 2)

                        inp_total_10.on("update:model-value", recalc_pontos_100)
                        inp_avcb_10.on("update:model-value", recalc_pontos_100)
                        inp_visa_10.on("update:model-value", recalc_pontos_100)
                        inp_reparos_10.on("update:model-value", recalc_pontos_100)
                        inp_interrompidos_10.on("update:model-value", recalc_pontos_100)

                        # Inicializar os textos informativos de notas
                        recalc_pontos_100()

                        ui.textarea(
                            label="Link de Evidência / Laudos Sanitários / Laudos do Corpo de Bombeiros / Relatórios de Manutenção:",
                            value=raw_link_100,
                            placeholder="Link das pastas com AVCBs, licenças da Vigilância Sanitária e relatórios de funcionamento...",
                        ).classes("w-full mb-4").props("outlined rows=3").bind_value(
                            state_100, "link"
                        )

                        def salvar_100():
                            pts_totais = recalc_pontos_100()

                            dados_salvar = {
                                "total_estabelecimentos": int(state_100["total_estabelecimentos"]) if state_100["total_estabelecimentos"].isdigit() else 0,
                                "qtd_avcb": int(state_100["qtd_avcb"]) if state_100["qtd_avcb"].isdigit() else 0,
                                "qtd_visa": int(state_100["qtd_visa"]) if state_100["qtd_visa"].isdigit() else 0,
                                "qtd_reparos": int(state_100["qtd_reparos"]) if state_100["qtd_reparos"].isdigit() else 0,
                                "qtd_interrompidos": int(state_100["qtd_interrompidos"]) if state_100["qtd_interrompidos"].isdigit() else 0,
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="10.0",
                                valor=dados_salvar,
                                pontos=pts_totais,
                                link=state_100["link"],
                                comentarios=d100.get("comentarios", []),
                                status=d100.get("status", "Pendente"),
                            )

                            ui.notify(f"Quesito 10.0 salvo com sucesso! (Pontuação acumulada: {pts_totais:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 10.0", on_click=salvar_100).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("10.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 11.0 (Existência de PCCS Específico da Saúde)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("11.0 • PCCS Específico para Profissionais de Saúde").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui Plano de Carreira, Cargos e Salários (PCCS) específico elaborado e implantado para seus profissionais de saúde?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Atenção: PCCS geral dos servidores públicos não é considerado PCCS específico da saúde. (Sim = 10 pts | Não = 00 pts)"
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d110 = res_data.get("11.0") or res_data.get("11") or {}
                        raw_val_110 = d110.get("valor") or {}
                        if not isinstance(raw_val_110, dict):
                            raw_val_110 = {}

                        val_pccs_110 = str(raw_val_110.get("pccs_especifico", "Não"))

                        state_110 = {"pccs_especifico": val_pccs_110 if val_pccs_110 in ["Sim", "Não"] else "Não"}

                        opt_pccs_110 = ui.radio(
                            options=["Sim", "Não"],
                            value=state_110["pccs_especifico"]
                        ).props("inline color=blue").classes("mb-2")
                        opt_pccs_110.bind_value(state_110, "pccs_especifico")

                        lbl_pts_110 = ui.label("Nota Quesito 11.0: 0.0 / 10.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        def recalc_pontos_110():
                            pts = 10.0 if state_110["pccs_especifico"] == "Sim" else 0.0
                            lbl_pts_110.set_text(f"📊 Nota Quesito 11.0: {pts:.2f} / 10.0 pontos")
                            return pts

                        opt_pccs_110.on("update:model-value", recalc_pontos_110)
                        ui.timer(0.1, recalc_pontos_110, once=True)

                        def salvar_110():
                            pts_totais = recalc_pontos_110()
                            save_resposta(
                                ano=ano_sel,
                                qid="11.0",
                                valor={"pccs_especifico": state_110["pccs_especifico"]},
                                pontos=pts_totais,
                                link=d110.get("link", ""),
                                comentarios=d110.get("comentarios", []),
                                status=d110.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 11.0 salvo com sucesso! (Nota: {pts_totais:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 11.0", on_click=salvar_110).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("11.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 11.1 (Norma de Regulamentação do PCCS Específico)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("11.1 • Instrumento Normativo do PCCS da Saúde").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o instrumento normativo de regulamentação do Plano de Carreira, Cargos e Salários (PCCS) específico para os profissionais da saúde, Número e Data da publicação:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Anexar o documento em PDF por meio do botão de ANEXO."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d111 = res_data.get("11.1") or res_data.get("11_1") or {}
                        raw_val_111 = d111.get("valor") or {}
                        if isinstance(raw_val_111, dict):
                            val_norma = str(raw_val_111.get("norma_num_data", ""))
                        else:
                            val_norma = str(raw_val_111)

                        state_111 = {"norma_num_data": val_norma}

                        inp_norma_111 = ui.input(
                            label="Número e Data da Publicação:",
                            value=state_111["norma_num_data"],
                            placeholder="Ex: Lei Complementar nº 123, de 15/03/2021"
                        ).props("outlined dense color=blue").classes("w-full mb-6")
                        inp_norma_111.bind_value(state_111, "norma_num_data")

                        def salvar_111():
                            save_resposta(
                                ano=ano_sel,
                                qid="11.1",
                                valor={"norma_num_data": state_111["norma_num_data"]},
                                pontos=0.0,
                                link=d111.get("link", ""),
                                comentarios=d111.get("comentarios", []),
                                status=d111.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 11.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 11.1", on_click=salvar_111).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("11.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 11.2 (Divulgação do PCCS da Saúde na Internet)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("11.2 • Divulgação do PCCS da Saúde na Internet").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a página eletrônica (link na internet) de divulgação do Plano de Carreira, Cargos e Salários (PCCS) específico para os profissionais de saúde:"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Se não estiver disponível na internet, inserir no campo o texto XYZ. (XYZ = 00 pts | Diferente de XYZ = 02 pts)"
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d112 = res_data.get("11.2") or res_data.get("11_2") or {}
                        raw_link_112 = str(d112.get("link") or "")

                        state_112 = {"link": raw_link_112}

                        inp_link_112 = ui.textarea(
                            label="Página eletrônica (link na internet) ou XYZ:",
                            value=raw_link_112,
                            placeholder="Insira o link completo ou o texto XYZ...",
                        ).classes("w-full mb-2").props("outlined rows=2").bind_value(state_112, "link")

                        lbl_pts_112 = ui.label("Nota Quesito 11.2: 0.0 / 2.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        def recalc_pontos_112():
                            link_val = state_112["link"].strip()
                            pts = 0.0 if (link_val.upper() == "XYZ" or not link_val) else 2.0
                            lbl_pts_112.set_text(f"📊 Nota Quesito 11.2: {pts:.2f} / 2.0 pontos")
                            return pts

                        ui.timer(0.1, recalc_pontos_112, once=True)

                        def salvar_112():
                            pts_totais = recalc_pontos_112()
                            save_resposta(
                                ano=ano_sel,
                                qid="11.2",
                                valor={"link_informado": state_112["link"]},
                                pontos=pts_totais,
                                link=state_112["link"],
                                comentarios=d112.get("comentarios", []),
                                status=d112.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 11.2 salvo com sucesso! (Nota: {pts_totais:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 11.2", on_click=salvar_112).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("11.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 12.0 (Adoção da Estratégia de Saúde da Família)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("12.0 • Adoção da Estratégia de Saúde da Família (ESF)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município adotou a Estratégia de Saúde da Família em sua rede de serviços como a estratégia prioritária de organização da Atenção Básica?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "Sim – 10 pontos | Não – 00 pontos"
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d120 = res_data.get("12.0") or res_data.get("12") or {}
                        raw_val_120 = d120.get("valor") or {}

                        if isinstance(raw_val_120, dict):
                            val_esf = str(raw_val_120.get("adotou_esf", "Não"))
                        else:
                            val_esf = str(raw_val_120) if raw_val_120 else "Não"

                        raw_link_120 = str(d120.get("link") or "")

                        state_120 = {
                            "adotou_esf": val_esf if val_esf in ["Sim", "Não"] else "Não",
                            "link": raw_link_120,
                        }

                        opt_esf = ui.radio(
                            options=["Sim", "Não"],
                            value=state_120["adotou_esf"]
                        ).props("inline color=blue").classes("mb-2")
                        opt_esf.bind_value(state_120, "adotou_esf")

                        lbl_pts_120 = ui.label("Nota Quesito 12.0: 0.0 / 10.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        def recalc_pontos_120():
                            pts = 10.0 if state_120["adotou_esf"] == "Sim" else 0.0
                            lbl_pts_120.set_text(f"📊 Nota Quesito 12.0: {pts:.2f} / 10.0 pontos")
                            return pts

                        opt_esf.on("update:model-value", recalc_pontos_120)
                        ui.timer(0.1, recalc_pontos_120, once=True)

                        ui.textarea(
                            label="Link de Evidência / Plano Municipal de Saúde / Portarias ESF:",
                            value=raw_link_120,
                            placeholder="Insira o link para verificação da adoção da ESF...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(state_120, "link")

                        def salvar_120():
                            pts_totais = recalc_pontos_120()
                            save_resposta(
                                ano=ano_sel,
                                qid="12.0",
                                valor={"adotou_esf": state_120["adotou_esf"]},
                                pontos=pts_totais,
                                link=state_120["link"],
                                comentarios=d120.get("comentarios", []),
                                status=d120.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 12.0 salvo com sucesso! (Nota: {pts_totais:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 12.0", on_click=salvar_120).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("12.0", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 12.0 (Equipes de Saúde da Família e Atenção Primária)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("12.0 • Equipes de Saúde da Família e Atenção Primária").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Atenção Básica: Informe os dados sobre as equipes e a população cadastrada."
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Equipe completa eSF = Médico, Enfermeiro, Aux./Téc. de Enfermagem e ACS | Equipe completa eAP = Médico e Enfermeiro."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d120 = res_data.get("12.0") or res_data.get("12") or {}
                        raw_val_120 = d120.get("valor") or {}

                        if not isinstance(raw_val_120, dict):
                            raw_val_120 = {}

                        raw_link_120 = str(d120.get("link") or "")

                        state_120 = {
                            "esf_completas": str(raw_val_120.get("esf_completas", 0)),
                            "eap_completas": str(raw_val_120.get("eap_completas", 0)),
                            "esf_incompletas": str(raw_val_120.get("esf_incompletas", 0)),
                            "eap_incompletas": str(raw_val_120.get("eap_incompletas", 0)),
                            "pop_esf": str(raw_val_120.get("pop_esf", 0)),
                            "pop_eap": str(raw_val_120.get("pop_eap", 0)),
                            "link": raw_link_120,
                        }

                        # --- QUESITO 12.1: Informações de Equipes ---
                        ui.label("12.1 • Informe o total de equipes (eSF + eAP):").classes(
                            "text-sm font-bold text-blue-900 mb-3"
                        )

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de eSF completas:").classes("text-sm text-gray-700 w-1/2")
                            inp_esf_c = ui.input(
                                value=state_120["esf_completas"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_esf_c.bind_value(state_120, "esf_completas")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de eAP completas:").classes("text-sm text-gray-700 w-1/2")
                            inp_eap_c = ui.input(
                                value=state_120["eap_completas"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_eap_c.bind_value(state_120, "eap_completas")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de eSF incompletas:").classes("text-sm text-gray-700 w-1/2")
                            inp_esf_i = ui.input(
                                value=state_120["esf_incompletas"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_esf_i.bind_value(state_120, "esf_incompletas")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de eAP incompletas:").classes("text-sm text-gray-700 w-1/2")
                            inp_eap_i = ui.input(
                                value=state_120["eap_incompletas"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_eap_i.bind_value(state_120, "eap_incompletas")

                        lbl_pts_121 = ui.label("Nota 12.1 (Composição): 0.0 / 50.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        ui.separator().classes("mb-6")

                        # --- QUESITO 12.2: População Cadastrada ---
                        ui.label("12.2 • Informe a população cadastrada nas equipes:").classes(
                            "text-sm font-bold text-blue-900 mb-3"
                        )

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de pessoas cadastradas na eSF:").classes("text-sm text-gray-700 w-1/2")
                            inp_pop_esf = ui.input(
                                value=state_120["pop_esf"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_pop_esf.bind_value(state_120, "pop_esf")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Nº de pessoas cadastradas na eAP:").classes("text-sm text-gray-700 w-1/2")
                            inp_pop_eap = ui.input(
                                value=state_120["pop_eap"]
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_pop_eap.bind_value(state_120, "pop_eap")

                        lbl_pts_122 = ui.label("Nota 12.2 (População/Equipe): 0.0 / 40.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-6"
                        )

                        # --- Lógica de Cálculo das Pontuações Finais (12.1 e 12.2) ---
                        def recalc_pontos_120():
                            def to_float(val):
                                try:
                                    return float(val)
                                except ValueError:
                                    return 0.0

                            esf_c = to_float(state_120["esf_completas"])
                            eap_c = to_float(state_120["eap_completas"])
                            esf_i = to_float(state_120["esf_incompletas"])
                            eap_i = to_float(state_120["eap_incompletas"])

                            pop_esf = to_float(state_120["pop_esf"])
                            pop_eap = to_float(state_120["pop_eap"])

                            # Cálculo Quesito 12.1
                            ec = esf_c + eap_c
                            ei = esf_i + eap_i
                            total_equipes = ec + ei

                            if total_equipes > 0:
                                p_121 = ec / total_equipes
                                nf_121 = p_121 * 50.0
                            else:
                                p_121 = 0.0
                                nf_121 = 0.0

                            lbl_pts_121.set_text(
                                f"📊 Nota 12.1: {nf_121:.2f} / 50.0 pontos "
                                f"(EC: {int(ec)} | EI: {int(ei)} | Proporção de Completas: {(p_121*100):.1f}%)"
                            )

                            # Cálculo Quesito 12.2
                            pop_total = pop_esf + pop_eap
                            if total_equipes > 0:
                                media_pop_equipe = pop_total / total_equipes
                                if 2000 <= media_pop_equipe <= 4000:
                                    nf_122 = 40.0
                                else:
                                    nf_122 = 0.0
                            else:
                                media_pop_equipe = 0.0
                                nf_122 = 0.0

                            lbl_pts_122.set_text(
                                f"📊 Nota 12.2: {nf_122:.2f} / 40.0 pontos "
                                f"(Média de cadastrados/equipe: {media_pop_equipe:.1f} hab/equipe)"
                            )

                            return round(nf_121 + nf_122, 2)

                        # Registro dos eventos de atualização de entrada
                        for input_field in [inp_esf_c, inp_eap_c, inp_esf_i, inp_eap_i, inp_pop_esf, inp_pop_eap]:
                            input_field.on("update:model-value", recalc_pontos_120)

                        # Inicializar os rótulos de notas ao carregar a página
                        recalc_pontos_120()

                        ui.textarea(
                            label="Link de Evidência / Relatórios do e-Gestor AB / Sistema de Informação da Atenção Básica (SISAB):",
                            value=raw_link_120,
                            placeholder="Link dos relatórios comprobatórios de equipes e relatórios de população cadastrada...",
                        ).classes("w-full mb-4").props("outlined rows=3").bind_value(
                            state_120, "link"
                        )

                        def salvar_120():
                            pts_totais = recalc_pontos_120()

                            def to_int(val):
                                return int(val) if str(val).isdigit() else 0

                            dados_salvar = {
                                "esf_completas": to_int(state_120["esf_completas"]),
                                "eap_completas": to_int(state_120["eap_completas"]),
                                "esf_incompletas": to_int(state_120["esf_incompletas"]),
                                "eap_incompletas": to_int(state_120["eap_incompletas"]),
                                "pop_esf": to_int(state_120["pop_esf"]),
                                "pop_eap": to_int(state_120["pop_eap"]),
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="12.0",
                                valor=dados_salvar,
                                pontos=pts_totais,
                                link=state_120["link"],
                                comentarios=d120.get("comentarios", []),
                                status=d120.get("status", "Pendente"),
                            )

                            ui.notify(
                                f"Quesito 12.0 salvo com sucesso! (Pontuação acumulada: {pts_totais:.2f} pts)",
                                type="positive",
                            )
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 12.0", on_click=salvar_120).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("12.0", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 13.0 (Registro Eletrônico de Frequência)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("13.0 • Registro Eletrônico de Frequência dos Profissionais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura registra a frequência dos profissionais de saúde da Atenção Básica de forma eletrônica?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Obs: O encaminhamento de planilhas de ponto não será considerado como modalidade de registro eletrônico."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d130 = res_data.get("13.0") or res_data.get("13") or {}
                        raw_val_130 = str(d130.get("valor", "none"))
                        raw_link_130 = str(d130.get("link") or "")

                        state_130 = {
                            "opcao": raw_val_130 if raw_val_130 in ["05", "03", "01", "00"] else "none",
                            "link": raw_link_130,
                        }

                        opts_130 = {
                            "none": "Selecione uma opção...",
                            "05": "Sim, para todos os profissionais da saúde – 05 pts",
                            "03": "Sim, para a maior parte dos profissionais da saúde – 03 pts",
                            "01": "Sim, para a menor parte dos profissionais da saúde – 01 pt",
                            "00": "Não houve registro eletrônico de nenhum profissional de saúde – 00 pt",
                        }

                        rad_130 = ui.radio(
                            options=opts_130,
                            value=state_130["opcao"]
                        ).classes("mb-4")
                        rad_130.bind_value(state_130, "opcao")

                        lbl_pts_130 = ui.label("Nota 13.0: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_130():
                            val = state_130["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_130.set_text(f"📊 Nota 13.0: {pts:.1f} / 5.0 pontos")
                            return pts

                        rad_130.on("update:model-value", recalc_130)
                        recalc_130()

                        ui.textarea(
                            label="Link de Evidência / Sistema de Ponto Eletrônico:",
                            value=raw_link_130,
                            placeholder="Link do relatório do sistema de ponto eletrônico ou portaria...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_130, "link"
                        )

                        def salvar_130():
                            pts = recalc_130()
                            save_resposta(
                                ano=ano_sel,
                                qid="13.0",
                                valor=state_130["opcao"],
                                pontos=pts,
                                link=state_130["link"],
                                comentarios=d130.get("comentarios", []),
                                status=d130.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 13.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 13.0", on_click=salvar_130).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("13.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 13.1 (Jornada de Trabalho Médica)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("13.1 • Cumprimento da Jornada de Trabalho Médica").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os médicos da Atenção Básica cumprem integralmente sua jornada de trabalho?"
                        ).classes("text-base font-bold text-black mb-6")

                        d131 = res_data.get("13.1") or {}
                        raw_val_131 = str(d131.get("valor", "none"))
                        raw_link_131 = str(d131.get("link") or "")

                        state_131 = {
                            "opcao": raw_val_131 if raw_val_131 in ["15", "08", "05", "02", "00"] else "none",
                            "link": raw_link_131,
                        }

                        opts_131 = {
                            "none": "Selecione uma opção...",
                            "15": "Sim, todos cumprem integralmente a jornada de trabalho – 15 pts",
                            "08": "Sim, a maior parte cumpre integralmente a jornada de trabalho – 08 pts",
                            "05": "Sim, todos permanecem apenas nas consultas agendadas – 05 pts",
                            "02": "Sim, a maior parte permanece apenas nas consultas agendadas – 02 pts",
                            "00": "Não – 00 pt",
                        }

                        rad_131 = ui.radio(
                            options=opts_131,
                            value=state_131["opcao"]
                        ).classes("mb-4")
                        rad_131.bind_value(state_131, "opcao")

                        lbl_pts_131 = ui.label("Nota 13.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_131():
                            val = state_131["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_131.set_text(f"📊 Nota 13.1: {pts:.1f} / 15.0 pontos")
                            return pts

                        rad_131.on("update:model-value", recalc_131)
                        recalc_131()

                        ui.textarea(
                            label="Link de Evidência / Escalas e Cumprimento de Jornada:",
                            value=raw_link_131,
                            placeholder="Link das folhas de ponto, relatórios de produtividade médica...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_131, "link"
                        )

                        def salvar_131():
                            pts = recalc_131()
                            save_resposta(
                                ano=ano_sel,
                                qid="13.1",
                                valor=state_131["opcao"],
                                pontos=pts,
                                link=state_131["link"],
                                comentarios=d131.get("comentarios", []),
                                status=d131.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 13.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 13.1", on_click=salvar_131).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("13.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 14.0 (Intervalo de Agendamento das Consultas Médicas)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.0 • Intervalo de Agendamento de Consultas Médicas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale o intervalo de agendamento das consultas médicas na Atenção Básica:"
                        ).classes("text-base font-bold text-black mb-6")

                        d140 = res_data.get("14.0") or res_data.get("14") or {}
                        raw_val_140 = str(d140.get("valor", "none"))
                        raw_link_140 = str(d140.get("link") or "")

                        state_140 = {
                            "opcao": raw_val_140 if raw_val_140 in ["01_pa", "01_15m", "00_menos15m", "00_multi"] else "none",
                            "link": raw_link_140,
                        }

                        opts_140 = {
                            "none": "Selecione uma opção...",
                            "01_pa": "Não há agendamento de consultas, pois todos os atendimentos são de pronto atendimento – 01 pt",
                            "01_15m": "Agendamento de cada paciente em horário único com, no mínimo, 15 minutos de atendimento – 01 pt",
                            "00_menos15m": "Agendamento de cada paciente em horário único com menos de 15 minutos de atendimento – 00 pt",
                            "00_multi": "Agendamento de 2 ou mais pacientes no mesmo horário – 00 pt",
                        }

                        rad_140 = ui.radio(
                            options=opts_140,
                            value=state_140["opcao"]
                        ).classes("mb-4")
                        rad_140.bind_value(state_140, "opcao")

                        lbl_pts_140 = ui.label("Nota 14.0: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_140():
                            val = state_140["opcao"]
                            pts = 1.0 if val in ["01_pa", "01_15m"] else 0.0
                            lbl_pts_140.set_text(f"📊 Nota 14.0: {pts:.1f} / 1.0 ponto")
                            return pts

                        rad_140.on("update:model-value", recalc_140)
                        recalc_140()

                        ui.textarea(
                            label="Link de Evidência / Agenda Médica do Prontuário Eletrônico:",
                            value=raw_link_140,
                            placeholder="Link das agendas das UBS no PEC/e-SUS...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_140, "link"
                        )

                        def salvar_140():
                            pts = recalc_140()
                            save_resposta(
                                ano=ano_sel,
                                qid="14.0",
                                valor=state_140["opcao"],
                                pontos=pts,
                                link=state_140["link"],
                                comentarios=d140.get("comentarios", []),
                                status=d140.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 14.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.0", on_click=salvar_140).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 14.1 (Serviço de Agendamento Remoto)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.1 • Agendamento Remoto para Consulta Médica").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município disponibilizou serviço de agendamento remoto para consulta médica na Atenção Básica?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "Exemplos de Agendamento Remoto: por telefone, internet, aplicativo, VoIP etc."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d141 = res_data.get("14.1") or {}
                        raw_val_141 = str(d141.get("valor", "none"))
                        raw_link_141 = str(d141.get("link") or "")

                        state_141 = {
                            "opcao": raw_val_141 if raw_val_141 in ["10", "00"] else "none",
                            "link": raw_link_141,
                        }

                        opts_141 = {
                            "none": "Selecione uma opção...",
                            "10": "Sim – 10 pts",
                            "00": "Não – 00 pt",
                        }

                        rad_141 = ui.radio(
                            options=opts_141,
                            value=state_141["opcao"]
                        ).classes("mb-4")
                        rad_141.bind_value(state_141, "opcao")

                        lbl_pts_141 = ui.label("Nota 14.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_141():
                            val = state_141["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_141.set_text(f"📊 Nota 14.1: {pts:.1f} / 10.0 pontos")
                            return pts

                        rad_141.on("update:model-value", recalc_141)
                        recalc_141()

                        ui.textarea(
                            label="Link de Evidência / Canal de Agendamento Remoto:",
                            value=raw_link_141,
                            placeholder="Link do portal de agendamento, aplicativo ou canal telefônico...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_141, "link"
                        )

                        def salvar_141():
                            pts = recalc_141()
                            save_resposta(
                                ano=ano_sel,
                                qid="14.1",
                                valor=state_141["opcao"],
                                pontos=pts,
                                link=state_141["link"],
                                comentarios=d141.get("comentarios", []),
                                status=d141.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 14.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.1", on_click=salvar_141).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 14.2 (Controle de Absenteísmo)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.2 • Controle de Absenteísmo nas Consultas Médicas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui controle de absenteísmo para as consultas médicas da Atenção Básica?"
                        ).classes("text-base font-bold text-black mb-6")

                        d142 = res_data.get("14.2") or {}
                        raw_val_142 = str(d142.get("valor", "none"))
                        raw_link_142 = str(d142.get("link") or "")

                        state_142 = {
                            "opcao": raw_val_142 if raw_val_142 in ["02", "01", "0.5", "00"] else "none",
                            "link": raw_link_142,
                        }

                        opts_142 = {
                            "none": "Selecione uma opção...",
                            "02": "Sim, para todas as consultas – 02 pts",
                            "01": "Sim, para a maior parte das consultas – 01 pt",
                            "0.5": "Sim, para a menor parte das consultas – 0,5 pt",
                            "00": "Não – 00 pt",
                        }

                        rad_142 = ui.radio(
                            options=opts_142,
                            value=state_142["opcao"]
                        ).classes("mb-4")
                        rad_142.bind_value(state_142, "opcao")

                        lbl_pts_142 = ui.label("Nota 14.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_142():
                            val = state_142["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_142.set_text(f"📊 Nota 14.2: {pts:.1f} / 2.0 pontos")
                            return pts

                        rad_142.on("update:model-value", recalc_142)
                        recalc_142()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo e Faltas:",
                            value=raw_link_142,
                            placeholder="Link dos relatórios gerenciais de faltas e lembretes de consultas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_142, "link"
                        )

                        def salvar_142():
                            pts = recalc_142()
                            save_resposta(
                                ano=ano_sel,
                                qid="14.2",
                                valor=state_142["opcao"],
                                pontos=pts,
                                link=state_142["link"],
                                comentarios=d142.get("comentarios", []),
                                status=d142.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 14.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.2", on_click=salvar_142).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.2", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 14.2.1 (Taxa de Absenteísmo de Consulta Médica)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.2.1 • Taxa de Absenteísmo em Consultas Médicas nas UBSs").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a taxa de absenteísmo de consulta médica nas UBSs (%):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Regra: Se a Taxa de 2025 (TA) for menor ou igual à média dos 2 anos anteriores (2023 e 2024) = 10 pts. Se for maior = 00 pt."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d1421 = res_data.get("14.2.1") or {}
                        raw_val_1421 = d1421.get("valor") or {}

                        if not isinstance(raw_val_1421, dict):
                            raw_val_1421 = {}

                        raw_link_1421 = str(d1421.get("link") or "")

                        state_1421 = {
                            "ta_2023": str(raw_val_1421.get("ta_2023", "")),
                            "ta_2024": str(raw_val_1421.get("ta_2024", "")),
                            "ta_2025": str(raw_val_1421.get("ta_2025", "")),
                            "link": raw_link_1421,
                        }

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2023 (TA-2) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2023 = ui.input(
                                value=state_1421["ta_2023"],
                                placeholder="Ex: 15.5"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2023.bind_value(state_1421, "ta_2023")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2024 (TA-1) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2024 = ui.input(
                                value=state_1421["ta_2024"],
                                placeholder="Ex: 14.2"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2024.bind_value(state_1421, "ta_2024")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2025 (TA) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2025 = ui.input(
                                value=state_1421["ta_2025"],
                                placeholder="Ex: 12.0"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2025.bind_value(state_1421, "ta_2025")

                        lbl_pts_1421 = ui.label("Nota 14.2.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1421():
                            def parse_val(v):
                                try:
                                    return float(v.replace(",", "."))
                                except (ValueError, AttributeError):
                                    return None

                            v23 = parse_val(state_1421["ta_2023"])
                            v24 = parse_val(state_1421["ta_2024"])
                            v25 = parse_val(state_1421["ta_2025"])

                            if v23 is not None and v24 is not None and v25 is not None:
                                media_anteriores = (v23 + v24) / 2.0
                                if v25 <= media_anteriores:
                                    pts = 10.0
                                else:
                                    pts = 0.0
                                lbl_pts_1421.set_text(
                                    f"📊 Nota 14.2.1: {pts:.1f} / 10.0 pontos "
                                    f"(Média 2023-2024: {media_anteriores:.2f}% | 2025: {v25:.2f}%)"
                                )
                            else:
                                pts = 0.0
                                lbl_pts_1421.set_text("📊 Nota 14.2.1: 0.0 pontos (Preencha as taxas de 2023, 2024 e 2025)")

                            return pts

                        for inp in [inp_ta_2023, inp_ta_2024, inp_ta_2025]:
                            inp.on("update:model-value", recalc_1421)

                        recalc_1421()

                        ui.textarea(
                            label="Link de Evidência / Relatório com a Série Histórica das Taxas de Absenteísmo:",
                            value=raw_link_1421,
                            placeholder="Link das planilhas ou relatórios gerenciais...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1421, "link"
                        )

                        def salvar_1421():
                            pts = recalc_1421()
                            save_resposta(
                                ano=ano_sel,
                                qid="14.2.1",
                                valor={
                                    "ta_2023": state_1421["ta_2023"],
                                    "ta_2024": state_1421["ta_2024"],
                                    "ta_2025": state_1421["ta_2025"],
                                },
                                pontos=pts,
                                link=state_1421["link"],
                                comentarios=d1421.get("comentarios", []),
                                status=d1421.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 14.2.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.2.1", on_click=salvar_1421).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 14.2.2 (Medidas para Redução da Taxa de Absenteísmo)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.2.2 • Medidas para Redução da Taxa de Absenteísmo").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município realiza medidas para a redução desta taxa de absenteísmo?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1422 = res_data.get("14.2.2") or {}
                        raw_val_1422 = str(d1422.get("valor", "none"))
                        raw_link_1422 = str(d1422.get("link") or "")

                        state_1422 = {
                            "opcao": raw_val_1422 if raw_val_1422 in ["00", "-02"] else "none",
                            "link": raw_link_1422,
                        }

                        opts_1422 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim – 00 pt (não perde pontos)",
                            "-02": "Não – -02 pts (perde 02 pontos)",
                        }

                        rad_1422 = ui.radio(
                            options=opts_1422,
                            value=state_1422["opcao"]
                        ).classes("mb-4")
                        rad_1422.bind_value(state_1422, "opcao")

                        lbl_pts_1422 = ui.label("Nota 14.2.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1422():
                            val = state_1422["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_1422.set_text(f"📊 Nota 14.2.2: {pts:.1f} pontos")
                            return pts

                        rad_1422.on("update:model-value", recalc_1422)
                        recalc_1422()

                        ui.textarea(
                            label="Link de Evidência / Documentos Comprobatórios de Medidas:",
                            value=raw_link_1422,
                            placeholder="Link das ações e comprovantes de medidas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1422, "link"
                        )

                        def salvar_1422():
                            pts = recalc_1422()
                            save_resposta(
                                ano=ano_sel,
                                qid="14.2.2",
                                valor=state_1422["opcao"],
                                pontos=pts,
                                link=state_1422["link"],
                                comentarios=d1422.get("comentarios", []),
                                status=d1422.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 14.2.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.2.2", on_click=salvar_1422).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.2.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 14.2.2.1 (Medidas Utilizadas para Redução)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("14.2.2.1 • Tipos de Medidas Utilizadas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as medidas utilizadas para a redução da taxa de absenteísmo de consultas médicas na Atenção Básica:"
                        ).classes("text-base font-bold text-black mb-4")

                        d14221 = res_data.get("14.2.2.1") or {}
                        raw_val_14221 = d14221.get("valor") or {}

                        if not isinstance(raw_val_14221, dict):
                            raw_val_14221 = {}

                        raw_link_14221 = str(d14221.get("link") or "")

                        state_14221 = {
                            "sensibilizacao": bool(raw_val_14221.get("sensibilizacao", False)),
                            "central": bool(raw_val_14221.get("central", False)),
                            "ligacao": bool(raw_val_14221.get("ligacao", False)),
                            "busca_ativa": bool(raw_val_14221.get("busca_ativa", False)),
                            "campanhas": bool(raw_val_14221.get("campanhas", False)),
                            "outros": bool(raw_val_14221.get("outros", False)),
                            "outros_desc": str(raw_val_14221.get("outros_desc", "")),
                            "link": raw_link_14221,
                        }

                        chk_1 = ui.checkbox("Informar e sensibilizar as equipes/profissionais a respeito do absenteísmo e promover capacitações", value=state_14221["sensibilizacao"])
                        chk_1.bind_value(state_14221, "sensibilizacao")

                        chk_2 = ui.checkbox("Criação de Central de relacionamento para usuário SUS, com disponibilização de canal direto de comunicação", value=state_14221["central"])
                        chk_2.bind_value(state_14221, "central")

                        chk_3 = ui.checkbox("Ligação telefônica ou outro meio de comunicação para confirmação da consulta e presença do paciente", value=state_14221["ligacao"])
                        chk_3.bind_value(state_14221, "ligacao")

                        chk_4 = ui.checkbox("Orientação das famílias e busca ativa dos faltosos pelos Agentes Comunitários de Saúde", value=state_14221["busca_ativa"])
                        chk_4.bind_value(state_14221, "busca_ativa")

                        chk_5 = ui.checkbox("Promoção de campanhas de conscientização", value=state_14221["campanhas"])
                        chk_5.bind_value(state_14221, "campanhas")

                        chk_6 = ui.checkbox("Outros", value=state_14221["outros"])
                        chk_6.bind_value(state_14221, "outros")

                        inp_outros = ui.input(
                            label="Especifique 'Outros':",
                            value=state_14221["outros_desc"]
                        ).props("outlined dense color=blue").classes("w-full mb-4 ml-6")
                        inp_outros.bind_value(state_14221, "outros_desc")
                        inp_outros.bind_visibility_from(chk_6, "value")

                        ui.textarea(
                            label="Link de Evidência / Material Informativo ou Campanhas:",
                            value=raw_link_14221,
                            placeholder="Link com folders, fotos, registos de busca ativa ou centrais...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_14221, "link"
                        )

                        def salvar_14221():
                            save_resposta(
                                ano=ano_sel,
                                qid="14.2.2.1",
                                valor={
                                    "sensibilizacao": state_14221["sensibilizacao"],
                                    "central": state_14221["central"],
                                    "ligacao": state_14221["ligacao"],
                                    "busca_ativa": state_14221["busca_ativa"],
                                    "campanhas": state_14221["campanhas"],
                                    "outros": state_14221["outros"],
                                    "outros_desc": state_14221["outros_desc"],
                                },
                                pontos=0.0,
                                link=state_14221["link"],
                                comentarios=d14221.get("comentarios", []),
                                status=d14221.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 14.2.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 14.2.2.1", on_click=salvar_14221).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.2.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 15.0 (Controle de Absenteísmo em Exames Laboratoriais)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("15.0 • Controle de Absenteísmo para Exames Laboratoriais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura Municipal possui controle de absenteísmo para os exames laboratoriais realizados sob sua gestão?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "Exemplos de exames laboratoriais: triglicérides, colesterol total e fração, hemograma, glicemia em jejum, hemoglobina glicada e controle de eletrólitos."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d150 = res_data.get("15.0") or res_data.get("15") or {}
                        raw_val_150 = str(d150.get("valor", "none"))
                        raw_link_150 = str(d150.get("link") or "")

                        state_150 = {
                            "opcao": raw_val_150 if raw_val_150 in ["02_pa", "02_todos", "01", "0.5", "00"] else "none",
                            "link": raw_link_150,
                        }

                        opts_150 = {
                            "none": "Selecione uma opção...",
                            "02_pa": "Todos os exames laboratoriais são de pronto atendimento – 02 pts",
                            "02_todos": "Sim, para todos os exames – 02 pts",
                            "01": "Sim, para a maior parte dos exames – 01 pt",
                            "0.5": "Sim, para a menor parte dos exames – 0,5 pt",
                            "00": "Não – 00 pt",
                        }

                        rad_150 = ui.radio(
                            options=opts_150,
                            value=state_150["opcao"]
                        ).classes("mb-4")
                        rad_150.bind_value(state_150, "opcao")

                        lbl_pts_150 = ui.label("Nota 15.0: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_150():
                            val = state_150["opcao"]
                            if val in ["02_pa", "02_todos"]:
                                pts = 2.0
                            elif val == "01":
                                pts = 1.0
                            elif val == "0.5":
                                pts = 0.5
                            else:
                                pts = 0.0

                            lbl_pts_150.set_text(f"📊 Nota 15.0: {pts:.1f} / 2.0 pontos")
                            return pts

                        rad_150.on("update:model-value", recalc_150)
                        recalc_150()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo Laboratorial:",
                            value=raw_link_150,
                            placeholder="Link do sistema de regulação laboratorial ou relatórios de absenteísmo...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_150, "link"
                        )

                        def salvar_150():
                            pts = recalc_150()
                            save_resposta(
                                ano=ano_sel,
                                qid="15.0",
                                valor=state_150["opcao"],
                                pontos=pts,
                                link=state_150["link"],
                                comentarios=d150.get("comentarios", []),
                                status=d150.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 15.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 15.0", on_click=salvar_150).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("15.0", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 15.1 (Taxa de Absenteísmo de Exames Médicos da Atenção Básica)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("15.1 • Taxa de Absenteísmo em Exames Médicos da Atenção Básica").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a taxa de absenteísmo de exame médico da Atenção Básica (%):"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "⚠️ Regra: Se a Taxa de 2025 (TA) for menor ou igual à média dos 2 anos anteriores (2023 e 2024) = 07 pts. Se for maior = 00 pt."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d151 = res_data.get("15.1") or {}
                        raw_val_151 = d151.get("valor") or {}

                        if not isinstance(raw_val_151, dict):
                            raw_val_151 = {}

                        raw_link_151 = str(d151.get("link") or "")

                        state_151 = {
                            "ta_2023": str(raw_val_151.get("ta_2023", "")),
                            "ta_2024": str(raw_val_151.get("ta_2024", "")),
                            "ta_2025": str(raw_val_151.get("ta_2025", "")),
                            "link": raw_link_151,
                        }

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2023 (TA-2) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2023_151 = ui.input(
                                value=state_151["ta_2023"],
                                placeholder="Ex: 18.5"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2023_151.bind_value(state_151, "ta_2023")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2024 (TA-1) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2024_151 = ui.input(
                                value=state_151["ta_2024"],
                                placeholder="Ex: 16.2"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2024_151.bind_value(state_151, "ta_2024")

                        with ui.row().classes("w-full items-center mb-2 gap-4"):
                            ui.label("Taxa em 2025 (TA) (%):").classes("text-sm text-gray-700 w-1/2")
                            inp_ta_2025_151 = ui.input(
                                value=state_151["ta_2025"],
                                placeholder="Ex: 15.0"
                            ).props("type=number outlined dense color=blue").classes("w-1/2")
                            inp_ta_2025_151.bind_value(state_151, "ta_2025")

                        lbl_pts_151 = ui.label("Nota 15.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_151():
                            def parse_val(v):
                                try:
                                    return float(v.replace(",", "."))
                                except (ValueError, AttributeError):
                                    return None

                            v23 = parse_val(state_151["ta_2023"])
                            v24 = parse_val(state_151["ta_2024"])
                            v25 = parse_val(state_151["ta_2025"])

                            if v23 is not None and v24 is not None and v25 is not None:
                                media_anteriores = (v23 + v24) / 2.0
                                if v25 <= media_anteriores:
                                    pts = 7.0
                                else:
                                    pts = 0.0
                                lbl_pts_151.set_text(
                                    f"📊 Nota 15.1: {pts:.1f} / 7.0 pontos "
                                    f"(Média 2023-2024: {media_anteriores:.2f}% | 2025: {v25:.2f}%)"
                                )
                            else:
                                pts = 0.0
                                lbl_pts_151.set_text("📊 Nota 15.1: 0.0 pontos (Preencha as taxas de 2023, 2024 e 2025)")

                            return pts

                        for inp in [inp_ta_2023_151, inp_ta_2024_151, inp_ta_2025_151]:
                            inp.on("update:model-value", recalc_151)

                        recalc_151()

                        ui.textarea(
                            label="Link de Evidência / Relatório com a Série Histórica de Absenteísmo em Exames:",
                            value=raw_link_151,
                            placeholder="Link do sistema ou relatórios gerenciais...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_151, "link"
                        )

                        def salvar_151():
                            pts = recalc_151()
                            save_resposta(
                                ano=ano_sel,
                                qid="15.1",
                                valor={
                                    "ta_2023": state_151["ta_2023"],
                                    "ta_2024": state_151["ta_2024"],
                                    "ta_2025": state_151["ta_2025"],
                                },
                                pontos=pts,
                                link=state_151["link"],
                                comentarios=d151.get("comentarios", []),
                                status=d151.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 15.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 15.1", on_click=salvar_151).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("15.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 15.2 (Medidas para Redução do Absenteísmo em Exames)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("15.2 • Medidas para Redução do Absenteísmo em Exames").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município realiza medidas para a redução desta taxa de absenteísmo?"
                        ).classes("text-base font-bold text-black mb-6")

                        d152 = res_data.get("15.2") or {}
                        raw_val_152 = str(d152.get("valor", "none"))
                        raw_link_152 = str(d152.get("link") or "")

                        state_152 = {
                            "opcao": raw_val_152 if raw_val_152 in ["00", "-02"] else "none",
                            "link": raw_link_152,
                        }

                        opts_152 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim – 00 pt (não perde pontos)",
                            "-02": "Não – -02 pts (perde 02 pontos)",
                        }

                        rad_152 = ui.radio(
                            options=opts_152,
                            value=state_152["opcao"]
                        ).classes("mb-4")
                        rad_152.bind_value(state_152, "opcao")

                        lbl_pts_152 = ui.label("Nota 15.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_152():
                            val = state_152["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_152.set_text(f"📊 Nota 15.2: {pts:.1f} pontos")
                            return pts

                        rad_152.on("update:model-value", recalc_152)
                        recalc_152()

                        ui.textarea(
                            label="Link de Evidência / Comprovantes das Medidas:",
                            value=raw_link_152,
                            placeholder="Link dos comprovantes e ações adotadas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_152, "link"
                        )

                        def salvar_152():
                            pts = recalc_152()
                            save_resposta(
                                ano=ano_sel,
                                qid="15.2",
                                valor=state_152["opcao"],
                                pontos=pts,
                                link=state_152["link"],
                                comentarios=d152.get("comentarios", []),
                                status=d152.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 15.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 15.2", on_click=salvar_152).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("15.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 15.2.1 (Tipos de Medidas para Exames Médicos)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("15.2.1 • Tipos de Medidas Utilizadas (Exames Médicos)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as medidas utilizadas para a redução da taxa de absenteísmo de exames médicos na Atenção Básica:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1521 = res_data.get("15.2.1") or {}
                        raw_val_1521 = d1521.get("valor") or {}

                        if not isinstance(raw_val_1521, dict):
                            raw_val_1521 = {}

                        raw_link_1521 = str(d1521.get("link") or "")

                        state_1521 = {
                            "sensibilizacao": bool(raw_val_1521.get("sensibilizacao", False)),
                            "central": bool(raw_val_1521.get("central", False)),
                            "ligacao": bool(raw_val_1521.get("ligacao", False)),
                            "busca_ativa": bool(raw_val_1521.get("busca_ativa", False)),
                            "campanhas": bool(raw_val_1521.get("campanhas", False)),
                            "outros": bool(raw_val_1521.get("outros", False)),
                            "outros_desc": str(raw_val_1521.get("outros_desc", "")),
                            "link": raw_link_1521,
                        }

                        chk_1 = ui.checkbox("Informar e sensibilizar as equipes/profissionais a respeito do absenteísmo e promover capacitações", value=state_1521["sensibilizacao"])
                        chk_1.bind_value(state_1521, "sensibilizacao")

                        chk_2 = ui.checkbox("Criação de Central de relacionamento para usuário SUS, com disponibilização de canal direto de comunicação", value=state_1521["central"])
                        chk_2.bind_value(state_1521, "central")

                        chk_3 = ui.checkbox("Ligação telefônica ou outro meio de comunicação para confirmação do exame e presença do paciente", value=state_1521["ligacao"])
                        chk_3.bind_value(state_1521, "ligacao")

                        chk_4 = ui.checkbox("Orientação das famílias e busca ativa dos faltosos pelos Agentes Comunitários de Saúde", value=state_1521["busca_ativa"])
                        chk_4.bind_value(state_1521, "busca_ativa")

                        chk_5 = ui.checkbox("Promoção de campanhas de conscientização", value=state_1521["campanhas"])
                        chk_5.bind_value(state_1521, "campanhas")

                        chk_6 = ui.checkbox("Outros", value=state_1521["outros"])
                        chk_6.bind_value(state_1521, "outros")

                        inp_outros_1521 = ui.input(
                            label="Especifique 'Outros':",
                            value=state_1521["outros_desc"]
                        ).props("outlined dense color=blue").classes("w-full mb-4 ml-6")
                        inp_outros_1521.bind_value(state_1521, "outros_desc")
                        inp_outros_1521.bind_visibility_from(chk_6, "value")

                        ui.textarea(
                            label="Link de Evidência / Material Informativo ou Campanhas:",
                            value=raw_link_1521,
                            placeholder="Link das fotos, relatórios ou registros de buscas ativas...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_1521, "link"
                        )

                        def salvar_1521():
                            save_resposta(
                                ano=ano_sel,
                                qid="15.2.1",
                                valor={
                                    "sensibilizacao": state_1521["sensibilizacao"],
                                    "central": state_1521["central"],
                                    "ligacao": state_1521["ligacao"],
                                    "busca_ativa": state_1521["busca_ativa"],
                                    "campanhas": state_1521["campanhas"],
                                    "outros": state_1521["outros"],
                                    "outros_desc": state_1521["outros_desc"],
                                },
                                pontos=0.0,
                                link=state_1521["link"],
                                comentarios=d1521.get("comentarios", []),
                                status=d1521.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 15.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 15.2.1", on_click=salvar_1521).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("15.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 16.0 (Implantação do PEP na Atenção Básica)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("16.0 • Prontuário Eletrônico do Paciente (PEP) na Atenção Básica").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município implantou o Prontuário Eletrônico do Paciente na Atenção Básica?"
                        ).classes("text-base font-bold text-black mb-6")

                        d160 = res_data.get("16.0") or res_data.get("16") or {}
                        raw_val_160 = str(d160.get("valor", "none"))
                        raw_link_160 = str(d160.get("link") or "")

                        state_160 = {
                            "opcao": raw_val_160 if raw_val_160 in ["10", "07", "03", "00"] else "none",
                            "link": raw_link_160,
                        }

                        opts_160 = {
                            "none": "Selecione uma opção...",
                            "10": "Sim, para todos os procedimentos da saúde – 10 pts",
                            "07": "Sim, para a maior parte dos procedimentos da saúde – 07 pts",
                            "03": "Sim, para a menor parte dos procedimentos da saúde – 03 pts",
                            "00": "Não – 00 pt",
                        }

                        rad_160 = ui.radio(
                            options=opts_160,
                            value=state_160["opcao"]
                        ).classes("mb-4")
                        rad_160.bind_value(state_160, "opcao")

                        lbl_pts_160 = ui.label("Nota 16.0: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_160():
                            val = state_160["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_160.set_text(f"📊 Nota 16.0: {pts:.1f} / 10.0 pontos")
                            return pts

                        rad_160.on("update:model-value", recalc_160)
                        recalc_160()

                        ui.textarea(
                            label="Link de Evidência / Declaração de Implantação do PEP:",
                            value=raw_link_160,
                            placeholder="Link do contrato, relatório do sistema PEC/e-SUS ou equivalente...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_160, "link"
                        )

                        def salvar_160():
                            pts = recalc_160()
                            save_resposta(
                                ano=ano_sel,
                                qid="16.0",
                                valor=state_160["opcao"],
                                pontos=pts,
                                link=state_160["link"],
                                comentarios=d160.get("comentarios", []),
                                status=d160.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 16.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 16.0", on_click=salvar_160).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("16.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 16.1 (Serviços Inseridos no PEP)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("16.1 • Serviços da Atenção Básica Inseridos no PEP").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale os serviços da Atenção Básica inseridos no Prontuário Eletrônico do Paciente:"
                        ).classes("text-base font-bold text-black mb-4")

                        d161 = res_data.get("16.1") or {}
                        raw_val_161 = d161.get("valor") or {}

                        if not isinstance(raw_val_161, dict):
                            raw_val_161 = {}

                        raw_link_161 = str(d161.get("link") or "")

                        state_161 = {
                            "esf": bool(raw_val_161.get("esf", False)),
                            "consultas": bool(raw_val_161.get("consultas", False)),
                            "exames": bool(raw_val_161.get("exames", False)),
                            "terapias": bool(raw_val_161.get("terapias", False)),
                            "medicamentos": bool(raw_val_161.get("medicamentos", False)),
                            "outros": bool(raw_val_161.get("outros", False)),
                            "outros_desc": str(raw_val_161.get("outros_desc", "")),
                            "link": raw_link_161,
                        }

                        chk_esf = ui.checkbox("Atendimento pela ESF – 01 pt", value=state_161["esf"])
                        chk_esf.bind_value(state_161, "esf")

                        chk_consultas = ui.checkbox("Consultas médicas em Atenção Primária – 01 pt", value=state_161["consultas"])
                        chk_consultas.bind_value(state_161, "consultas")

                        chk_exames = ui.checkbox("Exames laboratoriais – 01 pt", value=state_161["exames"])
                        chk_exames.bind_value(state_161, "exames")

                        chk_terapias = ui.checkbox("Terapias / tratamentos – 01 pt", value=state_161["terapias"])
                        chk_terapias.bind_value(state_161, "terapias")

                        chk_medicamentos = ui.checkbox("Medicamentos – 01 pt", value=state_161["medicamentos"])
                        chk_medicamentos.bind_value(state_161, "medicamentos")

                        chk_outros_161 = ui.checkbox("Outros – 00 pt", value=state_161["outros"])
                        chk_outros_161.bind_value(state_161, "outros")

                        inp_outros_161 = ui.input(
                            label="Especifique 'Outros':",
                            value=state_161["outros_desc"]
                        ).props("outlined dense color=blue").classes("w-full mb-4 ml-6")
                        inp_outros_161.bind_value(state_161, "outros_desc")
                        inp_outros_161.bind_visibility_from(chk_outros_161, "value")

                        lbl_pts_161 = ui.label("Nota 16.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_161():
                            pts = 0.0
                            if state_161["esf"]:
                                pts += 1.0
                            if state_161["consultas"]:
                                pts += 1.0
                            if state_161["exames"]:
                                pts += 1.0
                            if state_161["terapias"]:
                                pts += 1.0
                            if state_161["medicamentos"]:
                                pts += 1.0
                            lbl_pts_161.set_text(f"📊 Nota 16.1: {pts:.1f} / 5.0 pontos")
                            return pts

                        for chk in [chk_esf, chk_consultas, chk_exames, chk_terapias, chk_medicamentos, chk_outros_161]:
                            chk.on("update:model-value", recalc_161)

                        recalc_161()

                        ui.textarea(
                            label="Link de Evidência / Capturas de Tela dos Módulos Inseridos no PEP:",
                            value=raw_link_161,
                            placeholder="Link dos manuais, relatórios do sistema ou telas dos módulos...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_161, "link"
                        )

                        def salvar_161():
                            pts = recalc_161()
                            save_resposta(
                                ano=ano_sel,
                                qid="16.1",
                                valor={
                                    "esf": state_161["esf"],
                                    "consultas": state_161["consultas"],
                                    "exames": state_161["exames"],
                                    "terapias": state_161["terapias"],
                                    "medicamentos": state_161["medicamentos"],
                                    "outros": state_161["outros"],
                                    "outros_desc": state_161["outros_desc"],
                                },
                                pontos=pts,
                                link=state_161["link"],
                                comentarios=d161.get("comentarios", []),
                                status=d161.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 16.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 16.1", on_click=salvar_161).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("16.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.0 (Atendimento de Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.0 • Atendimento de Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui atendimento de Atenção Especializada (média e/ou alta complexidade)?"
                        ).classes("text-base font-bold text-black mb-6")

                        d170 = res_data.get("17.0") or res_data.get("17") or {}
                        raw_val_170 = str(d170.get("valor", "none"))
                        raw_link_170 = str(d170.get("link") or "")

                        state_170 = {
                            "opcao": raw_val_170 if raw_val_170 in ["mun", "est", "mun_est", "enc"] else "none",
                            "link": raw_link_170,
                        }

                        opts_170 = {
                            "none": "Selecione uma opção...",
                            "mun": "Sim, sob gestão municipal",
                            "est": "Sim, sob gestão estadual",
                            "mun_est": "Sim, sob gestão municipal e sob gestão estadual",
                            "enc": "Não, somente encaminhamento para outro município",
                        }

                        rad_170 = ui.radio(
                            options=opts_170,
                            value=state_170["opcao"]
                        ).classes("mb-4")
                        rad_170.bind_value(state_170, "opcao")

                        lbl_pts_170 = ui.label("Nota 17.0: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_170():
                            # Quesito declaratório / informativo (sem pontuação atribuída no formulário)
                            pts = 0.0
                            lbl_pts_170.set_text("📊 Nota 17.0: Informativo (0.0 pontos)")
                            return pts

                        rad_170.on("update:model-value", recalc_170)
                        recalc_170()

                        ui.textarea(
                            label="Link de Evidência / Relatório da Rede de Atenção Especializada:",
                            value=raw_link_170,
                            placeholder="Link de documentos ou relatórios CNES da rede especializada...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_170, "link"
                        )

                        def salvar_170():
                            pts = recalc_170()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.0",
                                valor=state_170["opcao"],
                                pontos=pts,
                                link=state_170["link"],
                                comentarios=d170.get("comentarios", []),
                                status=d170.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.0", on_click=salvar_170).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.1 (Registro Eletrônico de Frequência na Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.1 • Registro Eletrônico de Frequência do Pessoal da Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os profissionais de saúde da Atenção Especializada sob gestão municipal registram sua frequência de forma eletrônica?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "Obs.: O encaminhamento de planilhas de ponto NÃO será considerado como modalidade de registro eletrônico."
                        ).classes("text-xs font-semibold text-amber-600 mb-6")

                        d171 = res_data.get("17.1") or {}
                        raw_val_171 = str(d171.get("valor", "none"))
                        raw_link_171 = str(d171.get("link") or "")

                        state_171 = {
                            "opcao": raw_val_171 if raw_val_171 in ["00", "-01", "-02", "-03"] else "none",
                            "link": raw_link_171,
                        }

                        opts_171 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, para todos os profissionais da saúde – 00 pt (não perde pontos)",
                            "-01": "Sim, para a maior parte dos profissionais da saúde – -01 pt (perde 01 ponto)",
                            "-02": "Sim, para a menor parte dos profissionais da saúde – -02 pts (perde 02 pontos)",
                            "-03": "Não houve registro eletrônico de nenhum profissional de saúde – -03 pts (perde 03 pontos)",
                        }

                        rad_171 = ui.radio(
                            options=opts_171,
                            value=state_171["opcao"]
                        ).classes("mb-4")
                        rad_171.bind_value(state_171, "opcao")

                        lbl_pts_171 = ui.label("Nota 17.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_171():
                            val = state_171["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_171.set_text(f"📊 Nota 17.1: {pts:.1f} pontos")
                            return pts

                        rad_171.on("update:model-value", recalc_171)
                        recalc_171()

                        ui.textarea(
                            label="Link de Evidência / Relatórios do Ponto Eletrônico:",
                            value=raw_link_171,
                            placeholder="Link do sistema de ponto eletrônico ou relatórios biométricos...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_171, "link"
                        )

                        def salvar_171():
                            pts = recalc_171()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.1",
                                valor=state_171["opcao"],
                                pontos=pts,
                                link=state_171["link"],
                                comentarios=d171.get("comentarios", []),
                                status=d171.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.1", on_click=salvar_171).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.1.1 (Cumprirem Jornada Médicos Ambulatoriais da Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.1.1 • Cumprirem Jornada Médicos Ambulatoriais da Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os médicos ambulatoriais da Atenção Especializada sob gestão municipal cumprem integralmente sua jornada de trabalho?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1711 = res_data.get("17.1.1") or {}
                        raw_val_1711 = str(d1711.get("valor", "none"))
                        raw_link_1711 = str(d1711.get("link") or "")

                        state_1711 = {
                            "opcao": raw_val_1711 if raw_val_1711 in ["00", "-01", "-04", "-03", "-05"] else "none",
                            "link": raw_link_1711,
                        }

                        opts_1711 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, todos cumprem integralmente a jornada de trabalho – 00 pt (não perde pontos)",
                            "-01": "Sim, a maior parte cumpre integralmente a jornada de trabalho – -01 pt (perde 01 ponto)",
                            "-04": "Sim, todos permanecem apenas nas consultas agendadas – -04 pts (perde 04 pontos)",
                            "-03": "Sim, a maior parte permanece apenas nas consultas agendadas – -03 pts (perde 03 pontos)",
                            "-05": "Não – -05 pts (perde 05 pontos)",
                        }

                        rad_1711 = ui.radio(
                            options=opts_1711,
                            value=state_1711["opcao"]
                        ).classes("mb-4")
                        rad_1711.bind_value(state_1711, "opcao")

                        lbl_pts_1711 = ui.label("Nota 17.1.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1711():
                            val = state_1711["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_1711.set_text(f"📊 Nota 17.1.1: {pts:.1f} pontos")
                            return pts

                        rad_1711.on("update:model-value", recalc_1711)
                        recalc_1711()

                        ui.textarea(
                            label="Link de Evidência / Escala Médica e Folhas/Registros de Ponto:",
                            value=raw_link_1711,
                            placeholder="Link das folhas de ponto, relatórios de produtividade ou auditoria...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1711, "link"
                        )

                        def salvar_1711():
                            pts = recalc_1711()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.1.1",
                                valor=state_1711["opcao"],
                                pontos=pts,
                                link=state_1711["link"],
                                comentarios=d1711.get("comentarios", []),
                                status=d1711.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.1.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.1.1", on_click=salvar_1711).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.1.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.1.2 (Jornada dos Médicos Plantonistas da Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.1.2 • Cumprimento de Jornada dos Médicos Plantonistas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Os médicos plantonistas da Atenção Especializada sob gestão municipal cumprem integralmente sua jornada de trabalho?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1712 = res_data.get("17.1.2") or {}
                        raw_val_1712 = str(d1712.get("valor", "none"))
                        raw_link_1712 = str(d1712.get("link") or "")

                        state_1712 = {
                            "opcao": raw_val_1712 if raw_val_1712 in ["00_todos", "-01", "-03", "-05", "00_np"] else "none",
                            "link": raw_link_1712,
                        }

                        opts_1712 = {
                            "none": "Selecione uma opção...",
                            "00_todos": "Sim, todos cumprem integralmente a jornada de trabalho – 00 pt (não perde pontos)",
                            "-01": "Sim, a maior parte dos médicos cumpre a jornada de trabalho – -01 pt (perde 01 ponto)",
                            "-03": "Sim, a menor parte dos médicos cumprem a jornada de trabalho – -03 pts (perde 03 pontos)",
                            "-05": "Não – -05 pts (perde 05 pontos)",
                            "00_np": "Não possui médicos plantonistas – 00 pt (não perde pontos)",
                        }

                        rad_1712 = ui.radio(
                            options=opts_1712,
                            value=state_1712["opcao"]
                        ).classes("mb-4")
                        rad_1712.bind_value(state_1712, "opcao")

                        lbl_pts_1712 = ui.label("Nota 17.1.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1712():
                            val = state_1712["opcao"]
                            if val in ["00_todos", "00_np"]:
                                pts = 0.0
                            elif val != "none":
                                pts = float(val)
                            else:
                                pts = 0.0
                            lbl_pts_1712.set_text(f"📊 Nota 17.1.2: {pts:.1f} pontos")
                            return pts

                        rad_1712.on("update:model-value", recalc_1712)
                        recalc_1712()

                        ui.textarea(
                            label="Link de Evidência / Escalas de Plantão e Registros de Ponto:",
                            value=raw_link_1712,
                            placeholder="Link com escalas mensais, folhas de ponto ou auditoria...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1712, "link"
                        )

                        def salvar_1712():
                            pts = recalc_1712()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.1.2",
                                valor=state_1712["opcao"],
                                pontos=pts,
                                link=state_1712["link"],
                                comentarios=d1712.get("comentarios", []),
                                status=d1712.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.1.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.1.2", on_click=salvar_1712).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.1.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.2 (Intervalo de Agendamento das Consultas Médicas)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.2 • Intervalo de Agendamento das Consultas Médicas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale o intervalo de agendamento das consultas médicas da Atenção Especializada sob gestão municipal:"
                        ).classes("text-base font-bold text-black mb-4")

                        d172 = res_data.get("17.2") or {}
                        raw_val_172 = d172.get("valor") or {}

                        if not isinstance(raw_val_172, dict):
                            raw_val_172 = {}

                        raw_link_172 = str(d172.get("link") or "")

                        state_172 = {
                            "pronto_atendimento": bool(raw_val_172.get("pronto_atendimento", False)),
                            "horario_15min": bool(raw_val_172.get("horario_15min", False)),
                            "horario_menos_15min": bool(raw_val_172.get("horario_menos_15min", False)),
                            "mesmo_horario": bool(raw_val_172.get("mesmo_horario", False)),
                            "link": raw_link_172,
                        }

                        chk_pa = ui.checkbox("Não há agendamento de consultas da Atenção Especializada, pois todas são de pronto atendimento – 00 pt", value=state_172["pronto_atendimento"])
                        chk_pa.bind_value(state_172, "pronto_atendimento")

                        chk_15 = ui.checkbox("Agendamento de cada paciente em horário único com, no mínimo, 15 minutos de atendimento – 00 pt", value=state_172["horario_15min"])
                        chk_15.bind_value(state_172, "horario_15min")

                        chk_menos_15 = ui.checkbox("Agendamento de cada paciente em horário único com menos de 15 minutos de atendimento – -0,5 pt (perde 0,5 ponto)", value=state_172["horario_menos_15min"])
                        chk_menos_15.bind_value(state_172, "horario_menos_15min")

                        chk_mesmo = ui.checkbox("Agendamento de 2 ou mais pacientes no mesmo horário – -0,5 pt (perde 0,5 ponto)", value=state_172["mesmo_horario"])
                        chk_mesmo.bind_value(state_172, "mesmo_horario")

                        lbl_pts_172 = ui.label("Nota 17.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_172():
                            pts = 0.0
                            if state_172["horario_menos_15min"]:
                                pts -= 0.5
                            if state_172["mesmo_horario"]:
                                pts -= 0.5
                            lbl_pts_172.set_text(f"📊 Nota 17.2: {pts:.1f} pontos")
                            return pts

                        for chk in [chk_pa, chk_15, chk_menos_15, chk_mesmo]:
                            chk.on("update:model-value", recalc_172)

                        recalc_172()

                        ui.textarea(
                            label="Link de Evidência / Grade de Agendamento das Especialidades:",
                            value=raw_link_172,
                            placeholder="Link do sistema de agendamento ou parâmetro de consultas...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_172, "link"
                        )

                        def salvar_172():
                            pts = recalc_172()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.2",
                                valor={
                                    "pronto_atendimento": state_172["pronto_atendimento"],
                                    "horario_15min": state_172["horario_15min"],
                                    "horario_menos_15min": state_172["horario_menos_15min"],
                                    "mesmo_horario": state_172["mesmo_horario"],
                                },
                                pontos=pts,
                                link=state_172["link"],
                                comentarios=d172.get("comentarios", []),
                                status=d172.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.2", on_click=salvar_172).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.3 (Controle de Absenteísmo de Consultas na Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.3 • Controle de Absenteísmo na Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui controle de absenteísmo de consultas médicas da Atenção Especializada sob gestão municipal?"
                        ).classes("text-base font-bold text-black mb-6")

                        d173 = res_data.get("17.3") or {}
                        raw_val_173 = str(d173.get("valor", "none"))
                        raw_link_173 = str(d173.get("link") or "")

                        state_173 = {
                            "opcao": raw_val_173 if raw_val_173 in ["00", "-01", "-02", "-03"] else "none",
                            "link": raw_link_173,
                        }

                        opts_173 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, para todas as consultas médicas – 00 pt (não perde pontos)",
                            "-01": "Sim, para a maior parte das consultas médicas – -01 pt (perde 01 ponto)",
                            "-02": "Sim, para a menor parte das consultas médicas – -02 pts (perde 02 pontos)",
                            "-03": "Não – -03 pts (perde 03 pontos)",
                        }

                        rad_173 = ui.radio(
                            options=opts_173,
                            value=state_173["opcao"]
                        ).classes("mb-4")
                        rad_173.bind_value(state_173, "opcao")

                        lbl_pts_173 = ui.label("Nota 17.3: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_173():
                            val = state_173["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_173.set_text(f"📊 Nota 17.3: {pts:.1f} pontos")
                            return pts

                        rad_173.on("update:model-value", recalc_173)
                        recalc_173()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo Especializado:",
                            value=raw_link_173,
                            placeholder="Link das planilhas ou relatórios de absenteísmo em especialidades...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_173, "link"
                        )

                        def salvar_173():
                            pts = recalc_173()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.3",
                                valor=state_173["opcao"],
                                pontos=pts,
                                link=state_173["link"],
                                comentarios=d173.get("comentarios", []),
                                status=d173.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.3 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.3", on_click=salvar_173).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.3.1 (Taxa de Absenteísmo na Atenção Especializada: 2023, 2024, 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.3.1 • Taxa de Absenteísmo de Consultas Médicas Especializadas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a taxa de absenteísmo de consulta médica da Atenção Especializada sob gestão municipal:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Regra de pontuação: Se TA (2025) > média(2023, 2024) -> perde 2 pontos (-02). Caso contrário -> 00 ponto."
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1731 = res_data.get("17.3.1") or {}
                        raw_val_1731 = d1731.get("valor") or {}
                        if not isinstance(raw_val_1731, dict):
                            raw_val_1731 = {}

                        raw_link_1731 = str(d1731.get("link") or "")

                        state_1731 = {
                            "ta_2023": str(raw_val_1731.get("ta_2023", "")),
                            "ta_2024": str(raw_val_1731.get("ta_2024", "")),
                            "ta_2025": str(raw_val_1731.get("ta_2025", "")),
                            "link": raw_link_1731,
                        }

                        with ui.row().classes("w-full gap-4 mb-4"):
                            inp_2023 = ui.input(
                                label="Taxa 2023 (TA-2) (%)",
                                placeholder="Ex: 15.5",
                                value=state_1731["ta_2023"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2023.bind_value(state_1731, "ta_2023")

                            inp_2024 = ui.input(
                                label="Taxa 2024 (TA-1) (%)",
                                placeholder="Ex: 14.0",
                                value=state_1731["ta_2024"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2024.bind_value(state_1731, "ta_2024")

                            inp_2025 = ui.input(
                                label="Taxa 2025 (TA) (%)",
                                placeholder="Ex: 12.8",
                                value=state_1731["ta_2025"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2025.bind_value(state_1731, "ta_2025")

                        lbl_pts_1731 = ui.label("Nota 17.3.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1731():
                            try:
                                t23 = float(state_1731["ta_2023"].replace(",", ".")) if state_1731["ta_2023"] else None
                                t24 = float(state_1731["ta_2024"].replace(",", ".")) if state_1731["ta_2024"] else None
                                t25 = float(state_1731["ta_2025"].replace(",", ".")) if state_1731["ta_2025"] else None

                                if t23 is not None and t24 is not None and t25 is not None:
                                    media_anteriores = (t23 + t24) / 2.0
                                    if t25 > media_anteriores:
                                        pts = -2.0
                                    else:
                                        pts = 0.0
                                    lbl_pts_1731.set_text(
                                        f"📊 Nota 17.3.1: {pts:.1f} pontos (Média 23-24: {media_anteriores:.2f}% | TA 2025: {t25:.2f}%)"
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_1731.set_text("📊 Nota 17.3.1: Informe todas as taxas para calcular")
                            except ValueError:
                                pts = 0.0
                                lbl_pts_1731.set_text("📊 Nota 17.3.1: Valores inválidos informados")
                            return pts

                        for inp in [inp_2023, inp_2024, inp_2025]:
                            inp.on("update:model-value", recalc_1731)

                        recalc_1731()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo Ano a Ano:",
                            value=raw_link_1731,
                            placeholder="Link para memórias de cálculo, planilhas do e-SUS/Regulação...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1731, "link"
                        )

                        def salvar_1731():
                            pts = recalc_1731()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.3.1",
                                valor={
                                    "ta_2023": state_1731["ta_2023"],
                                    "ta_2024": state_1731["ta_2024"],
                                    "ta_2025": state_1731["ta_2025"],
                                },
                                pontos=pts,
                                link=state_1731["link"],
                                comentarios=d1731.get("comentarios", []),
                                status=d1731.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.3.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.3.1", on_click=salvar_1713 if False else salvar_1731).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.3.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.3.2 (Medidas para Redução do Absenteísmo em Consultas)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.3.2 • Medidas para Redução da Taxa de Absenteísmo").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município realiza medidas para a redução desta taxa de absenteísmo?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1732 = res_data.get("17.3.2") or {}
                        raw_val_1732 = str(d1732.get("valor", "none"))
                        raw_link_1732 = str(d1732.get("link") or "")

                        state_1732 = {
                            "opcao": raw_val_1732 if raw_val_1732 in ["00", "-02"] else "none",
                            "link": raw_link_1732,
                        }

                        opts_1732 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim – 00 pt (não perde pontos)",
                            "-02": "Não – -02 pts (perde 02 pontos)",
                        }

                        rad_1732 = ui.radio(
                            options=opts_1732,
                            value=state_1732["opcao"]
                        ).classes("mb-4")
                        rad_1732.bind_value(state_1732, "opcao")

                        lbl_pts_1732 = ui.label("Nota 17.3.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1732():
                            val = state_1732["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_1732.set_text(f"📊 Nota 17.3.2: {pts:.1f} pontos")
                            return pts

                        rad_1732.on("update:model-value", recalc_1732)
                        recalc_1732()

                        ui.textarea(
                            label="Link de Evidência / Planos de Ação e Ações de Redução:",
                            value=raw_link_1732,
                            placeholder="Link de documentos comprovando as medidas adotadas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1732, "link"
                        )

                        def salvar_1732():
                            pts = recalc_1732()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.3.2",
                                valor=state_1732["opcao"],
                                pontos=pts,
                                link=state_1732["link"],
                                comentarios=d1732.get("comentarios", []),
                                status=d1732.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.3.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.3.2", on_click=salvar_1732).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.3.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.3.2.1 (Medidas Utilizadas para Redução de Absenteísmo)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.3.2.1 • Tipos de Medidas Utilizadas para Redução do Absenteísmo").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as medidas utilizadas para a redução da taxa de absenteísmo:"
                        ).classes("text-base font-bold text-black mb-4")

                        d17321 = res_data.get("17.3.2.1") or {}
                        raw_val_17321 = d17321.get("valor") or {}
                        if not isinstance(raw_val_17321, dict):
                            raw_val_17321 = {}

                        raw_link_17321 = str(d17321.get("link") or "")

                        state_17321 = {
                            "sensibilizacao": bool(raw_val_17321.get("sensibilizacao", False)),
                            "central_relacionamento": bool(raw_val_17321.get("central_relacionamento", False)),
                            "busca_ativa": bool(raw_val_17321.get("busca_ativa", False)),
                            "campanhas": bool(raw_val_17321.get("campanhas", False)),
                            "outros": bool(raw_val_17321.get("outros", False)),
                            "outros_texto": str(raw_val_17321.get("outros_texto", "")),
                            "link": raw_link_17321,
                        }

                        chk_sens = ui.checkbox("Informar e sensibilizar as equipes/profissionais a respeito do absenteísmo e promover capacitações", value=state_17321["sensibilizacao"])
                        chk_sens.bind_value(state_17321, "sensibilizacao")

                        chk_cent = ui.checkbox("Criação de Central de relacionamento para usuário SUS, com disponibilização de canal direto de comunicação", value=state_17321["central_relacionamento"])
                        chk_cent.bind_value(state_17321, "central_relacionamento")

                        chk_busc = ui.checkbox("Orientação das famílias e busca ativa dos faltosos", value=state_17321["busca_ativa"])
                        chk_busc.bind_value(state_17321, "busca_ativa")

                        chk_camp = ui.checkbox("Promoção de campanhas de conscientização", value=state_17321["campanhas"])
                        chk_camp.bind_value(state_17321, "campanhas")

                        chk_outr = ui.checkbox("Outros", value=state_17321["outros"])
                        chk_outr.bind_value(state_17321, "outros")

                        inp_outros_txt = ui.input(
                            label="Especifique 'Outros':",
                            value=state_17321["outros_texto"]
                        ).classes("w-full my-2").props("outlined density=compact")
                        inp_outros_txt.bind_value(state_17321, "outros_texto")

                        def toggle_outros_txt():
                            inp_outros_txt.set_visibility(state_17321["outros"])

                        chk_outr.on("update:model-value", toggle_outros_txt)
                        toggle_outros_txt()

                        lbl_pts_17321 = ui.label("Nota 17.3.2.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-2"
                        )

                        def recalc_17321():
                            pts = 0.0
                            lbl_pts_17321.set_text("📊 Nota 17.3.2.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência das Medidas Mencionadas:",
                            value=raw_link_17321,
                            placeholder="Link de cartilhas, fotos de campanhas, prints da central de comunicação...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_17321, "link"
                        )

                        def salvar_17321():
                            pts = recalc_17321()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.3.2.1",
                                valor={
                                    "sensibilizacao": state_17321["sensibilizacao"],
                                    "central_relacionamento": state_17321["central_relacionamento"],
                                    "busca_ativa": state_17321["busca_ativa"],
                                    "campanhas": state_17321["campanhas"],
                                    "outros": state_17321["outros"],
                                    "outros_texto": state_17321["outros_texto"],
                                },
                                pontos=pts,
                                link=state_17321["link"],
                                comentarios=d17321.get("comentarios", []),
                                status=d17321.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.3.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.3.2.1", on_click=salvar_17321).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.3.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.4 (Controle de Absenteísmo para Exames Médicos da Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.4 • Controle de Absenteísmo para Exames Médicos Especializados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Prefeitura Municipal possui controle de absenteísmo para os exames médicos da Atenção Especializada sob sua gestão?"
                        ).classes("text-base font-bold text-black mb-6")

                        d174 = res_data.get("17.4") or {}
                        raw_val_174 = str(d174.get("valor", "none"))
                        raw_link_174 = str(d174.get("link") or "")

                        state_174 = {
                            "opcao": raw_val_174 if raw_val_174 in ["00", "-01", "-02", "-03"] else "none",
                            "link": raw_link_174,
                        }

                        opts_174 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, para todos os exames – 00 pt (não perde pontos)",
                            "-01": "Sim, para a maior parte dos exames – -01 pt (perde 01 ponto)",
                            "-02": "Sim, para a menor parte dos exames – -02 pts (perde 02 pontos)",
                            "-03": "Não – -03 pts (perde 03 pontos)",
                        }

                        rad_174 = ui.radio(
                            options=opts_174,
                            value=state_174["opcao"]
                        ).classes("mb-4")
                        rad_174.bind_value(state_174, "opcao")

                        lbl_pts_174 = ui.label("Nota 17.4: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_174():
                            val = state_174["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_174.set_text(f"📊 Nota 17.4: {pts:.1f} pontos")
                            return pts

                        rad_174.on("update:model-value", recalc_174)
                        recalc_174()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo em Exames:",
                            value=raw_link_174,
                            placeholder="Link das planilhas ou relatórios de absenteísmo para exames especializados...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_174, "link"
                        )

                        def salvar_174():
                            pts = recalc_174()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.4",
                                valor=state_174["opcao"],
                                pontos=pts,
                                link=state_174["link"],
                                comentarios=d174.get("comentarios", []),
                                status=d174.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.4 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.4", on_click=salvar_174).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.4", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.4.1 (Taxa de Absenteísmo para Exames Médicos: 2023, 2024, 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.4.1 • Taxa de Absenteísmo de Exames Médicos Especializados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a taxa de absenteísmo de exame médico da Atenção Especializada sob gestão municipal:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Regra de pontuação: Se TA (2025) > média(2023, 2024) -> perde 2 pontos (-02). Caso contrário -> 00 ponto."
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1741 = res_data.get("17.4.1") or {}
                        raw_val_1741 = d1741.get("valor") or {}
                        if not isinstance(raw_val_1741, dict):
                            raw_val_1741 = {}

                        raw_link_1741 = str(d1741.get("link") or "")

                        state_1741 = {
                            "ta_2023": str(raw_val_1741.get("ta_2023", "")),
                            "ta_2024": str(raw_val_1741.get("ta_2024", "")),
                            "ta_2025": str(raw_val_1741.get("ta_2025", "")),
                            "link": raw_link_1741,
                        }

                        with ui.row().classes("w-full gap-4 mb-4"):
                            inp_2023 = ui.input(
                                label="Taxa 2023 (TA-2) (%)",
                                placeholder="Ex: 99,9%",
                                value=state_1741["ta_2023"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2023.bind_value(state_1741, "ta_2023")

                            inp_2024 = ui.input(
                                label="Taxa 2024 (TA-1) (%)",
                                placeholder="Ex: 99,9%",
                                value=state_1741["ta_2024"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2024.bind_value(state_1741, "ta_2024")

                            inp_2025 = ui.input(
                                label="Taxa 2025 (TA) (%)",
                                placeholder="Ex: 99,9%",
                                value=state_1741["ta_2025"]
                            ).classes("w-1/3").props("outlined density=compact")
                            inp_2025.bind_value(state_1741, "ta_2025")

                        lbl_pts_1741 = ui.label("Nota 17.4.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1741():
                            try:
                                t23 = float(state_1741["ta_2023"].replace("%", "").replace(",", ".").strip()) if state_1741["ta_2023"] else None
                                t24 = float(state_1741["ta_2024"].replace("%", "").replace(",", ".").strip()) if state_1741["ta_2024"] else None
                                t25 = float(state_1741["ta_2025"].replace("%", "").replace(",", ".").strip()) if state_1741["ta_2025"] else None

                                if t23 is not None and t24 is not None and t25 is not None:
                                    media_anteriores = (t23 + t24) / 2.0
                                    if t25 > media_anteriores:
                                        pts = -2.0
                                    else:
                                        pts = 0.0
                                    lbl_pts_1741.set_text(
                                        f"📊 Nota 17.4.1: {pts:.1f} pontos (Média 23-24: {media_anteriores:.2f}% | TA 2025: {t25:.2f}%)"
                                    )
                                else:
                                    pts = 0.0
                                    lbl_pts_1741.set_text("📊 Nota 17.4.1: Informe todas as taxas para calcular")
                            except ValueError:
                                pts = 0.0
                                lbl_pts_1741.set_text("📊 Nota 17.4.1: Valores inválidos informados")
                            return pts

                        for inp in [inp_2023, inp_2024, inp_2025]:
                            inp.on("update:model-value", recalc_1741)

                        recalc_1741()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Absenteísmo de Exames Ano a Ano:",
                            value=raw_link_1741,
                            placeholder="Link para memórias de cálculo, relatórios do sistema de regulação/SISMAMA/SISCOLO...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1741, "link"
                        )

                        def salvar_1741():
                            pts = recalc_1741()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.4.1",
                                valor={
                                    "ta_2023": state_1741["ta_2023"],
                                    "ta_2024": state_1741["ta_2024"],
                                    "ta_2025": state_1741["ta_2025"],
                                },
                                pontos=pts,
                                link=state_1741["link"],
                                comentarios=d1741.get("comentarios", []),
                                status=d1741.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.4.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.4.1", on_click=salvar_1741).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.4.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.4.2 (Medidas para Redução da Taxa de Absenteísmo em Exames)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.4.2 • Medidas para Redução do Absenteísmo em Exames").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município realiza medidas para a redução desta taxa de absenteísmo?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1742 = res_data.get("17.4.2") or {}
                        raw_val_1742 = str(d1742.get("valor", "none"))
                        raw_link_1742 = str(d1742.get("link") or "")

                        state_1742 = {
                            "opcao": raw_val_1742 if raw_val_1742 in ["00", "-02"] else "none",
                            "link": raw_link_1742,
                        }

                        opts_1742 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim – 00 pt (não perde pontos)",
                            "-02": "Não – -02 pts (perde 02 pontos)",
                        }

                        rad_1742 = ui.radio(
                            options=opts_1742,
                            value=state_1742["opcao"]
                        ).classes("mb-4")
                        rad_1742.bind_value(state_1742, "opcao")

                        lbl_pts_1742 = ui.label("Nota 17.4.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1742():
                            val = state_1742["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_1742.set_text(f"📊 Nota 17.4.2: {pts:.1f} pontos")
                            return pts

                        rad_1742.on("update:model-value", recalc_1742)
                        recalc_1742()

                        ui.textarea(
                            label="Link de Evidência / Plano de Ação para Redução de Absenteísmo em Exames:",
                            value=raw_link_1742,
                            placeholder="Link de documentos comprovando as ações promovidas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1742, "link"
                        )

                        def salvar_1742():
                            pts = recalc_1742()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.4.2",
                                valor=state_1742["opcao"],
                                pontos=pts,
                                link=state_1742["link"],
                                comentarios=d1742.get("comentarios", []),
                                status=d1742.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.4.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.4.2", on_click=salvar_1742).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.4.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.4.2.1 (Medidas Utilizadas para Redução do Absenteísmo em Exames)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.4.2.1 • Tipos de Medidas Utilizadas em Exames Médicos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as medidas utilizadas para a redução da taxa de absenteísmo de exames médicos da Atenção Especializada sob gestão municipal:"
                        ).classes("text-base font-bold text-black mb-4")

                        d17421 = res_data.get("17.4.2.1") or {}
                        raw_val_17421 = d17421.get("valor") or {}
                        if not isinstance(raw_val_17421, dict):
                            raw_val_17421 = {}

                        raw_link_17421 = str(d17421.get("link") or "")

                        state_17421 = {
                            "sensibilizacao": bool(raw_val_17421.get("sensibilizacao", False)),
                            "central_relacionamento": bool(raw_val_17421.get("central_relacionamento", False)),
                            "confirmacao_telefone": bool(raw_val_17421.get("confirmacao_telefone", False)),
                            "busca_ativa": bool(raw_val_17421.get("busca_ativa", False)),
                            "campanhas": bool(raw_val_17421.get("campanhas", False)),
                            "outros": bool(raw_val_17421.get("outros", False)),
                            "outros_texto": str(raw_val_17421.get("outros_texto", "")),
                            "link": raw_link_17421,
                        }

                        chk_sens = ui.checkbox("Informar e sensibilizar as equipes/ profissionais a respeito do absenteísmo e promover capacitações", value=state_17421["sensibilizacao"])
                        chk_sens.bind_value(state_17421, "sensibilizacao")

                        chk_cent = ui.checkbox("Criação de Central de relacionamento para usuário SUS, com disponibilização de canal direto de comunicação", value=state_17421["central_relacionamento"])
                        chk_cent.bind_value(state_17421, "central_relacionamento")

                        chk_conf = ui.checkbox("Ligação telefônica ou outro meio de comunicação para confirmação do exame e presença do paciente", value=state_17421["confirmacao_telefone"])
                        chk_conf.bind_value(state_17421, "confirmacao_telefone")

                        chk_busc = ui.checkbox("Orientação das famílias e busca ativa dos faltosos", value=state_17421["busca_ativa"])
                        chk_busc.bind_value(state_17421, "busca_ativa")

                        chk_camp = ui.checkbox("Promoção de campanhas de conscientização", value=state_17421["campanhas"])
                        chk_camp.bind_value(state_17421, "campanhas")

                        chk_outr = ui.checkbox("Outros", value=state_17421["outros"])
                        chk_outr.bind_value(state_17421, "outros")

                        inp_outros_txt = ui.input(
                            label="Especifique 'Outros':",
                            value=state_17421["outros_texto"]
                        ).classes("w-full my-2").props("outlined density=compact")
                        inp_outros_txt.bind_value(state_17421, "outros_texto")

                        def toggle_outros_txt():
                            inp_outros_txt.set_visibility(state_17421["outros"])

                        chk_outr.on("update:model-value", toggle_outros_txt)
                        toggle_outros_txt()

                        lbl_pts_17421 = ui.label("Nota 17.4.2.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-2"
                        )

                        def recalc_17421():
                            pts = 0.0
                            lbl_pts_17421.set_text("📊 Nota 17.4.2.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência das Medidas Mencionadas para Exames:",
                            value=raw_link_17421,
                            placeholder="Link dos comprovantes de chamadas/mensagens, folhetos ou registros do Call Center...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_17421, "link"
                        )

                        def salvar_17421():
                            pts = recalc_17421()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.4.2.1",
                                valor={
                                    "sensibilizacao": state_17421["sensibilizacao"],
                                    "central_relacionamento": state_17421["central_relacionamento"],
                                    "confirmacao_telefone": state_17421["confirmacao_telefone"],
                                    "busca_ativa": state_17421["busca_ativa"],
                                    "campanhas": state_17421["campanhas"],
                                    "outros": state_17421["outros"],
                                    "outros_texto": state_17421["outros_texto"],
                                },
                                pontos=pts,
                                link=state_17421["link"],
                                comentarios=d17421.get("comentarios", []),
                                status=d17421.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.4.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.4.2.1", on_click=salvar_17421).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.4.2.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.5 (Sistema Informatizado de Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5 • Sistema Informatizado de Regulação na Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município utiliza sistema informatizado de regulação com oferta dos serviços da Atenção Especializada sob gestão municipal?"
                        ).classes("text-base font-bold text-black mb-1")
                        ui.label(
                            "Obs.: Refere-se ao Município como Unidade Demandada - Central de Regulação."
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d175 = res_data.get("17.5") or {}
                        raw_val_175 = str(d175.get("valor", "none"))
                        raw_link_175 = str(d175.get("link") or "")

                        state_175 = {
                            "opcao": raw_val_175 if raw_val_175 in ["00", "-01", "-03", "-05"] else "none",
                            "link": raw_link_175,
                        }

                        opts_175 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, todos os serviços – 00 pt (não perde pontos)",
                            "-01": "Sim, a maior parte dos serviços – -01 pt (perde 01 ponto)",
                            "-03": "Sim, a menor parte dos serviços – -03 pts (perde 03 pontos)",
                            "-05": "Não – -05 pts (perde 05 pontos)",
                        }

                        rad_175 = ui.radio(
                            options=opts_175,
                            value=state_175["opcao"]
                        ).classes("mb-4")
                        rad_175.bind_value(state_175, "opcao")

                        lbl_pts_175 = ui.label("Nota 17.5: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_175():
                            val = state_175["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_175.set_text(f"📊 Nota 17.5: {pts:.1f} pontos")
                            return pts

                        rad_175.on("update:model-value", recalc_175)
                        recalc_175()

                        ui.textarea(
                            label="Link de Evidência / Sistema de Regulação Utilizado:",
                            value=raw_link_175,
                            placeholder="Link das telas do sistema de regulação ou declaração oficial...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_175, "link"
                        )

                        def salvar_175():
                            pts = recalc_175()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5",
                                valor=state_175["opcao"],
                                pontos=pts,
                                link=state_175["link"],
                                comentarios=d175.get("comentarios", []),
                                status=d175.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.5 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5", on_click=salvar_175).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.1 (Sistemas Utilizados pela Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.1 • Sistemas Utilizados pela Regulação").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale os sistemas utilizados pela regulação:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1751 = res_data.get("17.5.1") or {}
                        raw_val_1751 = d1751.get("valor") or {}
                        if not isinstance(raw_val_1751, dict):
                            raw_val_1751 = {}

                        raw_link_1751 = str(d1751.get("link") or "")

                        state_1751 = {
                            "cross_siresp": bool(raw_val_1751.get("cross_siresp", False)),
                            "siga": bool(raw_val_1751.get("siga", False)),
                            "sisreg": bool(raw_val_1751.get("sisreg", False)),
                            "outros": bool(raw_val_1751.get("outros", False)),
                            "outros_texto": str(raw_val_1751.get("outros_texto", "")),
                            "link": raw_link_1751,
                        }

                        chk_cross = ui.checkbox("Portal Cross/SIRESP", value=state_1751["cross_siresp"])
                        chk_cross.bind_value(state_1751, "cross_siresp")

                        chk_siga = ui.checkbox("SIGA", value=state_1751["siga"])
                        chk_siga.bind_value(state_1751, "siga")

                        chk_sisreg = ui.checkbox("SISREG", value=state_1751["sisreg"])
                        chk_sisreg.bind_value(state_1751, "sisreg")

                        chk_outr = ui.checkbox("Outros", value=state_1751["outros"])
                        chk_outr.bind_value(state_1751, "outros")

                        inp_outros_txt = ui.input(
                            label="Especifique 'Outros':",
                            value=state_1751["outros_texto"]
                        ).classes("w-full my-2").props("outlined density=compact")
                        inp_outros_txt.bind_value(state_1751, "outros_texto")

                        def toggle_outros_txt_1751():
                            inp_outros_txt.set_visibility(state_1751["outros"])

                        chk_outr.on("update:model-value", toggle_outros_txt_1751)
                        toggle_outros_txt_1751()

                        lbl_pts_1751 = ui.label("Nota 17.5.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-2"
                        )

                        def recalc_1751():
                            pts = 0.0
                            lbl_pts_1751.set_text("📊 Nota 17.5.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Prints ou Portarias dos Sistemas de Regulação:",
                            value=raw_link_1751,
                            placeholder="Link das telas, contratos de software ou regulamentos...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_1751, "link"
                        )

                        def salvar_1751():
                            pts = recalc_1751()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.1",
                                valor={
                                    "cross_siresp": state_1751["cross_siresp"],
                                    "siga": state_1751["siga"],
                                    "sisreg": state_1751["sisreg"],
                                    "outros": state_1751["outros"],
                                    "outros_texto": state_1751["outros_texto"],
                                },
                                pontos=pts,
                                link=state_1751["link"],
                                comentarios=d1751.get("comentarios", []),
                                status=d1751.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.1", on_click=salvar_1751).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2 (Conhecer Lista de Espera Nominal e Tempos de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2 • Transparência e Acesso à Lista de Espera Nominal").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O sistema informatizado de regulação utilizado pelo município permite conhecer a lista de espera (relação nominal de pacientes com tempo de espera) dos serviços da Atenção Especializada sob gestão municipal?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1752 = res_data.get("17.5.2") or {}
                        raw_val_1752 = str(d1752.get("valor", "none"))
                        raw_link_1752 = str(d1752.get("link") or "")

                        state_1752 = {
                            "opcao": raw_val_1752 if raw_val_1752 in ["00", "-01", "-03", "-05"] else "none",
                            "link": raw_link_1752,
                        }

                        opts_1752 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, todos os serviços – 00 pt (não perde pontos)",
                            "-01": "Sim, a maior parte dos serviços – -01 pt (perde 01 ponto)",
                            "-03": "Sim, a menor parte dos serviços – -03 pts (perde 03 pontos)",
                            "-05": "Não – -05 pts (perde 05 pontos)",
                        }

                        rad_1752 = ui.radio(
                            options=opts_1752,
                            value=state_1752["opcao"]
                        ).classes("mb-4")
                        rad_1752.bind_value(state_1752, "opcao")

                        lbl_pts_1752 = ui.label("Nota 17.5.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1752():
                            val = state_1752["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_1752.set_text(f"📊 Nota 17.5.2: {pts:.1f} pontos")
                            return pts

                        rad_1752.on("update:model-value", recalc_1752)
                        recalc_1752()

                        ui.textarea(
                            label="Link de Evidência / Relatórios ou Portal da Transparência de Filas:",
                            value=raw_link_1752,
                            placeholder="Link do relatório com relação nominal de espera e tempos de fila...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1752, "link"
                        )

                        def salvar_1752():
                            pts = recalc_1752()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2",
                                valor=state_1752["opcao"],
                                pontos=pts,
                                link=state_1752["link"],
                                comentarios=d1752.get("comentarios", []),
                                status=d1752.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.5.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2", on_click=salvar_1752).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2.1 (Serviços Inseridos no Sistema de Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1 • Serviços da Atenção Especializada Inseridos na Regulação").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale os serviços da Atenção Especializada inseridos no sistema de regulação:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: Perde 1 ponto (-1) para cada item principal NÃO assinalado (entre 0 e -5 pts). 'Outros' não pontua."
                        ).classes("text-xs font-semibold text-gray-600 mb-4")

                        d17521 = res_data.get("17.5.2.1") or {}
                        raw_val_17521 = d17521.get("valor") or {}
                        if not isinstance(raw_val_17521, dict):
                            raw_val_17521 = {}

                        raw_link_17521 = str(d17521.get("link") or "")

                        state_17521 = {
                            "consultas": bool(raw_val_17521.get("consultas", False)),
                            "exames": bool(raw_val_17521.get("exames", False)),
                            "terapias": bool(raw_val_17521.get("terapias", False)),
                            "opm": bool(raw_val_17521.get("opm", False)),
                            "cirurgias_eletivas": bool(raw_val_17521.get("cirurgias_eletivas", False)),
                            "outros": bool(raw_val_17521.get("outros", False)),
                            "outros_texto": str(raw_val_17521.get("outros_texto", "")),
                            "link": raw_link_17521,
                        }

                        chk_cons = ui.checkbox("Consultas por especialidade", value=state_17521["consultas"])
                        chk_cons.bind_value(state_17521, "consultas")

                        chk_exam = ui.checkbox("Exames", value=state_17521["exames"])
                        chk_exam.bind_value(state_17521, "exames")

                        chk_terap = ui.checkbox("Terapias / tratamentos", value=state_17521["terapias"])
                        chk_terap.bind_value(state_17521, "terapias")

                        chk_opm = ui.checkbox("OPM (Órteses, Próteses e Materiais Especiais)", value=state_17521["opm"])
                        chk_opm.bind_value(state_17521, "opm")

                        chk_cirurg = ui.checkbox("Cirurgias eletivas", value=state_17521["cirurgias_eletivas"])
                        chk_cirurg.bind_value(state_17521, "cirurgias_eletivas")

                        chk_outr = ui.checkbox("Outros", value=state_17521["outros"])
                        chk_outr.bind_value(state_17521, "outros")

                        inp_outros_txt = ui.input(
                            label="Especifique 'Outros':",
                            value=state_17521["outros_texto"]
                        ).classes("w-full my-2").props("outlined density=compact")
                        inp_outros_txt.bind_value(state_17521, "outros_texto")

                        def toggle_outros_txt_17521():
                            inp_outros_txt.set_visibility(state_17521["outros"])

                        chk_outr.on("update:model-value", toggle_outros_txt_17521)
                        toggle_outros_txt_17521()

                        lbl_pts_17521 = ui.label("Nota 17.5.2.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-2"
                        )

                        def recalc_17521():
                            itens_principais = [
                                state_17521["consultas"],
                                state_17521["exames"],
                                state_17521["terapias"],
                                state_17521["opm"],
                                state_17521["cirurgias_eletivas"],
                            ]
                            nao_marcados = sum(1 for item in itens_principais if not item)
                            pts = -1.0 * nao_marcados
                            pts = max(pts, -5.0)
                            lbl_pts_17521.set_text(f"📊 Nota 17.5.2.1: {pts:.1f} pontos")
                            return pts

                        for chk in [chk_cons, chk_exam, chk_terap, chk_opm, chk_cirurg]:
                            chk.on("update:model-value", recalc_17521)

                        recalc_17521()

                        ui.textarea(
                            label="Link de Evidência dos Serviços Regulados:",
                            value=raw_link_17521,
                            placeholder="Link dos relatórios por serviço do sistema de regulação...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_17521, "link"
                        )

                        def salvar_17521():
                            pts = recalc_17521()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1",
                                valor={
                                    "consultas": state_17521["consultas"],
                                    "exames": state_17521["exames"],
                                    "terapias": state_17521["terapias"],
                                    "opm": state_17521["opm"],
                                    "cirurgias_eletivas": state_17521["cirurgias_eletivas"],
                                    "outros": state_17521["outros"],
                                    "outros_texto": state_17521["outros_texto"],
                                },
                                pontos=pts,
                                link=state_17521["link"],
                                comentarios=d17521.get("comentarios", []),
                                status=d17521.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.5.2.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1", on_click=salvar_17521).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.5.2.1.1 (Consultas Médicas com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.1 • Maior Tempo de Espera - Consultas Médicas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe as 3 consultas médicas com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175211 = res_data.get("17.5.2.1.1") or {}
                        raw_val_175211 = d175211.get("valor") or {}
                        if not isinstance(raw_val_175211, dict):
                            raw_val_175211 = {}

                        raw_link_175211 = str(d175211.get("link") or "")

                        state_175211 = {
                            "esp_1": str(raw_val_175211.get("esp_1", "")),
                            "tempo_1": raw_val_175211.get("tempo_1", None),
                            "esp_2": str(raw_val_175211.get("esp_2", "")),
                            "tempo_2": raw_val_175211.get("tempo_2", None),
                            "esp_3": str(raw_val_175211.get("esp_3", "")),
                            "tempo_3": raw_val_175211.get("tempo_3", None),
                            "link": raw_link_175211,
                        }

                        # Item 1
                        ui.label("1ª Consulta Médica:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da especialidade médica:",
                                value=state_175211["esp_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175211, "esp_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175211["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175211, "tempo_1"
                            )

                        # Item 2
                        ui.label("2ª Consulta Médica:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da especialidade médica:",
                                value=state_175211["esp_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175211, "esp_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175211["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175211, "tempo_2"
                            )

                        # Item 3
                        ui.label("3ª Consulta Médica:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da especialidade médica:",
                                value=state_175211["esp_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175211, "esp_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175211["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175211, "tempo_3"
                            )

                        lbl_pts_175211 = ui.label("Nota 17.5.2.1.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório do Sistema de Regulação:",
                            value=raw_link_175211,
                            placeholder="Link do relatório com o tempo de espera das consultas...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175211, "link"
                        )

                        def salvar_175211():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.1",
                                valor={
                                    "esp_1": state_175211["esp_1"],
                                    "tempo_1": state_175211["tempo_1"],
                                    "esp_2": state_175211["esp_2"],
                                    "tempo_2": state_175211["tempo_2"],
                                    "esp_3": state_175211["esp_3"],
                                    "tempo_3": state_175211["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175211["link"],
                                comentarios=d175211.get("comentarios", []),
                                status=d175211.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.1", on_click=salvar_175211).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2.1.2 (Exames Médicos com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.2 • Maior Tempo de Espera - Exames Médicos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe os 3 exames médicos com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175212 = res_data.get("17.5.2.1.2") or {}
                        raw_val_175212 = d175212.get("valor") or {}
                        if not isinstance(raw_val_175212, dict):
                            raw_val_175212 = {}

                        raw_link_175212 = str(d175212.get("link") or "")

                        state_175212 = {
                            "exame_1": str(raw_val_175212.get("exame_1", "")),
                            "tempo_1": raw_val_175212.get("tempo_1", None),
                            "exame_2": str(raw_val_175212.get("exame_2", "")),
                            "tempo_2": raw_val_175212.get("tempo_2", None),
                            "exame_3": str(raw_val_175212.get("exame_3", "")),
                            "tempo_3": raw_val_175212.get("tempo_3", None),
                            "link": raw_link_175212,
                        }

                        # Item 1
                        ui.label("1º Exame Médico:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do exame médico:",
                                value=state_175212["exame_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175212, "exame_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175212["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175212, "tempo_1"
                            )

                        # Item 2
                        ui.label("2º Exame Médico:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do exame médico:",
                                value=state_175212["exame_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175212, "exame_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175212["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175212, "tempo_2"
                            )

                        # Item 3
                        ui.label("3º Exame Médico:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do exame médico:",
                                value=state_175212["exame_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175212, "exame_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175212["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175212, "tempo_3"
                            )

                        lbl_pts_175212 = ui.label("Nota 17.5.2.1.2: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório de Exames:",
                            value=raw_link_175212,
                            placeholder="Link do relatório com o tempo de espera dos exames...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175212, "link"
                        )

                        def salvar_175212():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.2",
                                valor={
                                    "exame_1": state_175212["exame_1"],
                                    "tempo_1": state_175212["tempo_1"],
                                    "exame_2": state_175212["exame_2"],
                                    "tempo_2": state_175212["tempo_2"],
                                    "exame_3": state_175212["exame_3"],
                                    "tempo_3": state_175212["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175212["link"],
                                comentarios=d175212.get("comentarios", []),
                                status=d175212.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.2", on_click=salvar_175212).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2.1.3 (Terapias/Tratamentos Médicos com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.3 • Maior Tempo de Espera - Terapias e Tratamentos").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe as 3 terapias/tratamentos médicos com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175213 = res_data.get("17.5.2.1.3") or {}
                        raw_val_175213 = d175213.get("valor") or {}
                        if not isinstance(raw_val_175213, dict):
                            raw_val_175213 = {}

                        raw_link_175213 = str(d175213.get("link") or "")

                        state_175213 = {
                            "terapia_1": str(raw_val_175213.get("terapia_1", "")),
                            "tempo_1": raw_val_175213.get("tempo_1", None),
                            "terapia_2": str(raw_val_175213.get("terapia_2", "")),
                            "tempo_2": raw_val_175213.get("tempo_2", None),
                            "terapia_3": str(raw_val_175213.get("terapia_3", "")),
                            "tempo_3": raw_val_175213.get("tempo_3", None),
                            "link": raw_link_175213,
                        }

                        # Item 1
                        ui.label("1ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da terapia/tratamento médico:",
                                value=state_175213["terapia_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175213, "terapia_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175213["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175213, "tempo_1"
                            )

                        # Item 2
                        ui.label("2ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da terapia/tratamento médico:",
                                value=state_175213["terapia_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175213, "terapia_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175213["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175213, "tempo_2"
                            )

                        # Item 3
                        ui.label("3ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da terapia/tratamento médico:",
                                value=state_175213["terapia_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175213, "terapia_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175213["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175213, "tempo_3"
                            )

                        lbl_pts_175213 = ui.label("Nota 17.5.2.1.3: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório de Terapias:",
                            value=raw_link_175213,
                            placeholder="Link do relatório com o tempo de espera das terapias/tratamentos...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175213, "link"
                        )

                        def salvar_175213():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.3",
                                valor={
                                    "terapia_1": state_175213["terapia_1"],
                                    "tempo_1": state_175213["tempo_1"],
                                    "terapia_2": state_175213["terapia_2"],
                                    "tempo_2": state_175213["tempo_2"],
                                    "terapia_3": state_175213["terapia_3"],
                                    "tempo_3": state_175213["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175213["link"],
                                comentarios=d175213.get("comentarios", []),
                                status=d175213.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.3", on_click=salvar_175213).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.3", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.5.2.1.4 (OPM com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.4 • Maior Tempo de Espera - OPM (Órteses, Próteses e Materiais)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe as 3 OPM com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175214 = res_data.get("17.5.2.1.4") or {}
                        raw_val_175214 = d175214.get("valor") or {}
                        if not isinstance(raw_val_175214, dict):
                            raw_val_175214 = {}

                        raw_link_175214 = str(d175214.get("link") or "")

                        state_175214 = {
                            "opm_1": str(raw_val_175214.get("opm_1", "")),
                            "tempo_1": raw_val_175214.get("tempo_1", None),
                            "opm_2": str(raw_val_175214.get("opm_2", "")),
                            "tempo_2": raw_val_175214.get("tempo_2", None),
                            "opm_3": str(raw_val_175214.get("opm_3", "")),
                            "tempo_3": raw_val_175214.get("tempo_3", None),
                            "link": raw_link_175214,
                        }

                        # Item 1
                        ui.label("1ª OPM:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da OPM:",
                                value=state_175214["opm_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175214, "opm_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175214["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175214, "tempo_1"
                            )

                        # Item 2
                        ui.label("2ª OPM:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da OPM:",
                                value=state_175214["opm_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175214, "opm_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175214["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175214, "tempo_2"
                            )

                        # Item 3
                        ui.label("3ª OPM:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da OPM:",
                                value=state_175214["opm_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175214, "opm_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175214["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175214, "tempo_3"
                            )

                        lbl_pts_175214 = ui.label("Nota 17.5.2.1.4: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório de Fila de OPM:",
                            value=raw_link_175214,
                            placeholder="Link do relatório com o tempo de espera de OPM...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175214, "link"
                        )

                        def salvar_175214():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.4",
                                valor={
                                    "opm_1": state_175214["opm_1"],
                                    "tempo_1": state_175214["tempo_1"],
                                    "opm_2": state_175214["opm_2"],
                                    "tempo_2": state_175214["tempo_2"],
                                    "opm_3": state_175214["opm_3"],
                                    "tempo_3": state_175214["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175214["link"],
                                comentarios=d175214.get("comentarios", []),
                                status=d175214.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.4", on_click=salvar_175214).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.4", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2.1.5 (Cirurgias Eletivas com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.5 • Maior Tempo de Espera - Cirurgias Eletivas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe as 3 cirurgias eletivas com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175215 = res_data.get("17.5.2.1.5") or {}
                        raw_val_175215 = d175215.get("valor") or {}
                        if not isinstance(raw_val_175215, dict):
                            raw_val_175215 = {}

                        raw_link_175215 = str(d175215.get("link") or "")

                        state_175215 = {
                            "cirurgia_1": str(raw_val_175215.get("cirurgia_1", "")),
                            "tempo_1": raw_val_175215.get("tempo_1", None),
                            "cirurgia_2": str(raw_val_175215.get("cirurgia_2", "")),
                            "tempo_2": raw_val_175215.get("tempo_2", None),
                            "cirurgia_3": str(raw_val_175215.get("cirurgia_3", "")),
                            "tempo_3": raw_val_175215.get("tempo_3", None),
                            "link": raw_link_175215,
                        }

                        # Item 1
                        ui.label("1ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da cirurgia eletiva:",
                                value=state_175215["cirurgia_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175215, "cirurgia_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175215["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175215, "tempo_1"
                            )

                        # Item 2
                        ui.label("2ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da cirurgia eletiva:",
                                value=state_175215["cirurgia_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175215, "cirurgia_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175215["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175215, "tempo_2"
                            )

                        # Item 3
                        ui.label("3ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição da cirurgia eletiva:",
                                value=state_175215["cirurgia_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175215, "cirurgia_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175215["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175215, "tempo_3"
                            )

                        lbl_pts_175215 = ui.label("Nota 17.5.2.1.5: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório de Cirurgias Eletivas:",
                            value=raw_link_175215,
                            placeholder="Link do relatório com o tempo de espera das cirurgias eletivas...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175215, "link"
                        )

                        def salvar_175215():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.5",
                                valor={
                                    "cirurgia_1": state_175215["cirurgia_1"],
                                    "tempo_1": state_175215["tempo_1"],
                                    "cirurgia_2": state_175215["cirurgia_2"],
                                    "tempo_2": state_175215["tempo_2"],
                                    "cirurgia_3": state_175215["cirurgia_3"],
                                    "tempo_3": state_175215["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175215["link"],
                                comentarios=d175215.get("comentarios", []),
                                status=d175215.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.5", on_click=salvar_175215).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.5.2.1.6 (Outros Serviços da Atenção Especializada com Maior Tempo de Espera)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.5.2.1.6 • Maior Tempo de Espera - Outros Serviços Especializados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe os 3 Outros serviços da Atenção Especializada sob gestão municipal com maior tempo de espera:"
                        ).classes("text-base font-bold text-black mb-4")

                        d175216 = res_data.get("17.5.2.1.6") or {}
                        raw_val_175216 = d175216.get("valor") or {}
                        if not isinstance(raw_val_175216, dict):
                            raw_val_175216 = {}

                        raw_link_175216 = str(d175216.get("link") or "")

                        state_175216 = {
                            "servico_1": str(raw_val_175216.get("servico_1", "")),
                            "tempo_1": raw_val_175216.get("tempo_1", None),
                            "servico_2": str(raw_val_175216.get("servico_2", "")),
                            "tempo_2": raw_val_175216.get("tempo_2", None),
                            "servico_3": str(raw_val_175216.get("servico_3", "")),
                            "tempo_3": raw_val_175216.get("tempo_3", None),
                            "link": raw_link_175216,
                        }

                        # Item 1
                        ui.label("1º Outro Serviço:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do Serviço da Atenção Especializada:",
                                value=state_175216["servico_1"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175216, "servico_1"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175216["tempo_1"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175216, "tempo_1"
                            )

                        # Item 2
                        ui.label("2º Outro Serviço:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do Serviço da Atenção Especializada:",
                                value=state_175216["servico_2"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175216, "servico_2"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175216["tempo_2"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175216, "tempo_2"
                            )

                        # Item 3
                        ui.label("3º Outro Serviço:").classes("text-sm font-bold text-gray-700 mt-2")
                        with ui.row().classes("w-full gap-4 items-center"):
                            ui.input(
                                label="Descrição do Serviço da Atenção Especializada:",
                                value=state_175216["servico_3"]
                            ).classes("flex-1").props("outlined density=compact").bind_value(
                                state_175216, "servico_3"
                            )
                            ui.number(
                                label="Tempo médio de espera (dias):",
                                value=state_175216["tempo_3"],
                                min=0,
                                precision=0
                            ).classes("w-64").props("outlined density=compact").bind_value(
                                state_175216, "tempo_3"
                            )

                        lbl_pts_175216 = ui.label("Nota 17.5.2.1.6: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório de Outros Serviços Especializados:",
                            value=raw_link_175216,
                            placeholder="Link do relatório com o tempo de espera dos demais serviços...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_175216, "link"
                        )

                        def salvar_175216():
                            save_resposta(
                                ano=ano_sel,
                                qid="17.5.2.1.6",
                                valor={
                                    "servico_1": state_175216["servico_1"],
                                    "tempo_1": state_175216["tempo_1"],
                                    "servico_2": state_175216["servico_2"],
                                    "tempo_2": state_175216["tempo_2"],
                                    "servico_3": state_175216["servico_3"],
                                    "tempo_3": state_175216["tempo_3"],
                                },
                                pontos=0.0,
                                link=state_175216["link"],
                                comentarios=d175216.get("comentarios", []),
                                status=d175216.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.5.2.1.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.5.2.1.6", on_click=salvar_175216).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.5.2.1.6", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.6 (Prontuário Eletrônico do Paciente - PEP na Atenção Especializada)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.6 • Prontuário Eletrônico do Paciente (PEP) na Atenção Especializada").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município implantou o Prontuário Eletrônico do Paciente na Atenção Especializada sob sua gestão?"
                        ).classes("text-base font-bold text-black mb-6")

                        d176 = res_data.get("17.6") or {}
                        raw_val_176 = str(d176.get("valor", "none"))
                        raw_link_176 = str(d176.get("link") or "")

                        state_176 = {
                            "opcao": raw_val_176 if raw_val_176 in ["00", "-01", "-03", "-05"] else "none",
                            "link": raw_link_176,
                        }

                        opts_176 = {
                            "none": "Selecione uma opção...",
                            "00": "Sim, para todos os procedimentos da saúde – 00 pt (não perde pontos)",
                            "-01": "Sim, para a maior parte dos procedimentos da saúde – -01 pt (perde 01 ponto)",
                            "-03": "Sim, para a menor parte dos procedimentos da saúde – -03 pts (perde 03 pontos)",
                            "-05": "Não – -05 pts (perde 05 pontos)",
                        }

                        rad_176 = ui.radio(
                            options=opts_176,
                            value=state_176["opcao"]
                        ).classes("mb-4")
                        rad_176.bind_value(state_176, "opcao")

                        lbl_pts_176 = ui.label("Nota 17.6: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_176():
                            val = state_176["opcao"]
                            pts = float(val) if val != "none" else 0.0
                            lbl_pts_176.set_text(f"📊 Nota 17.6: {pts:.1f} pontos")
                            return pts

                        rad_176.on("update:model-value", recalc_176)
                        recalc_176()

                        ui.textarea(
                            label="Link de Evidência / Sistema PEP:",
                            value=raw_link_176,
                            placeholder="Link do contrato, sistema PEP ou telas comprobatórias...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_176, "link"
                        )

                        def salvar_176():
                            pts = recalc_176()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.6",
                                valor=state_176["opcao"],
                                pontos=pts,
                                link=state_176["link"],
                                comentarios=d176.get("comentarios", []),
                                status=d176.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.6 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.6", on_click=salvar_176).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.6", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.6.1 (Serviços Inseridos no PEP)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.6.1 • Serviços da Atenção Especializada Inseridos no PEP").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale os serviços da Atenção Especializada inseridos no Prontuário Eletrônico do Paciente:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: Perde 0,35 pt para cada item base não assinalado. 'Medicamentos' não assinalado perde 0,40 pt. 'Outros' não pontua. Nota de 0 a -2.5 pts."
                        ).classes("text-xs font-semibold text-gray-600 mb-4")

                        d1761 = res_data.get("17.6.1") or {}
                        raw_val_1761 = d1761.get("valor") or {}
                        if not isinstance(raw_val_1761, dict):
                            raw_val_1761 = {}

                        raw_link_1761 = str(d1761.get("link") or "")

                        state_1761 = {
                            "consultas": bool(raw_val_1761.get("consultas", False)),
                            "exames_lab": bool(raw_val_1761.get("exames_lab", False)),
                            "exames_rad": bool(raw_val_1761.get("exames_rad", False)),
                            "terapias": bool(raw_val_1761.get("terapias", False)),
                            "medicamentos": bool(raw_val_1761.get("medicamentos", False)),
                            "opm": bool(raw_val_1761.get("opm", False)),
                            "cirurgias_eletivas": bool(raw_val_1761.get("cirurgias_eletivas", False)),
                            "outros": bool(raw_val_1761.get("outros", False)),
                            "outros_texto": str(raw_val_1761.get("outros_texto", "")),
                            "link": raw_link_1761,
                        }

                        chk_cons = ui.checkbox("Consultas médicas por especialidade", value=state_1761["consultas"])
                        chk_cons.bind_value(state_1761, "consultas")

                        chk_lab = ui.checkbox("Exames laboratoriais", value=state_1761["exames_lab"])
                        chk_lab.bind_value(state_1761, "exames_lab")

                        chk_rad = ui.checkbox("Exames radiológicos e por imagem", value=state_1761["exames_rad"])
                        chk_rad.bind_value(state_1761, "exames_rad")

                        chk_terap = ui.checkbox("Terapias / tratamentos", value=state_1761["terapias"])
                        chk_terap.bind_value(state_1761, "terapias")

                        chk_med = ui.checkbox("Medicamentos (perde 0,40 se não marcado)", value=state_1761["medicamentos"])
                        chk_med.bind_value(state_1761, "medicamentos")

                        chk_opm = ui.checkbox("OPM (Órteses, Próteses e Materiais Especiais)", value=state_1761["opm"])
                        chk_opm.bind_value(state_1761, "opm")

                        chk_cirurg = ui.checkbox("Cirurgias eletivas", value=state_1761["cirurgias_eletivas"])
                        chk_cirurg.bind_value(state_1761, "cirurgias_eletivas")

                        chk_outr = ui.checkbox("Outros", value=state_1761["outros"])
                        chk_outr.bind_value(state_1761, "outros")

                        inp_outros_txt = ui.input(
                            label="Especifique 'Outros':",
                            value=state_1761["outros_texto"]
                        ).classes("w-full my-2").props("outlined density=compact")
                        inp_outros_txt.bind_value(state_1761, "outros_texto")

                        def toggle_outros_txt_1761():
                            inp_outros_txt.set_visibility(state_1761["outros"])

                        chk_outr.on("update:model-value", toggle_outros_txt_1761)
                        toggle_outros_txt_1761()

                        lbl_pts_1761 = ui.label("Nota 17.6.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4 mt-2"
                        )

                        def recalc_1761():
                            itens_35 = [
                                state_1761["consultas"],
                                state_1761["exames_lab"],
                                state_1761["exames_rad"],
                                state_1761["terapias"],
                                state_1761["opm"],
                                state_1761["cirurgias_eletivas"],
                            ]
                            nao_marcados_35 = sum(1 for item in itens_35 if not item)
                            perda_35 = nao_marcados_35 * 0.35
                            
                            perda_med = 0.40 if not state_1761["medicamentos"] else 0.0
                            
                            pts = -1.0 * (perda_35 + perda_med)
                            pts = max(pts, -2.5)
                            lbl_pts_1761.set_text(f"📊 Nota 17.6.1: {pts:.2f} pontos")
                            return pts

                        for chk in [chk_cons, chk_lab, chk_rad, chk_terap, chk_med, chk_opm, chk_cirurg]:
                            chk.on("update:model-value", recalc_1761)

                        recalc_1761()

                        ui.textarea(
                            label="Link de Evidência dos Módulos do PEP:",
                            value=raw_link_1761,
                            placeholder="Link com relatórios ou telas demonstrando os módulos do PEP...",
                        ).classes("w-full mb-4 mt-2").props("outlined rows=2").bind_value(
                            state_1761, "link"
                        )

                        def salvar_1761():
                            pts = recalc_1761()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.6.1",
                                valor={
                                    "consultas": state_1761["consultas"],
                                    "exames_lab": state_1761["exames_lab"],
                                    "exames_rad": state_1761["exames_rad"],
                                    "terapias": state_1761["terapias"],
                                    "medicamentos": state_1761["medicamentos"],
                                    "opm": state_1761["opm"],
                                    "cirurgias_eletivas": state_1761["cirurgias_eletivas"],
                                    "outros": state_1761["outros"],
                                    "outros_texto": state_1761["outros_texto"],
                                },
                                pontos=pts,
                                link=state_1761["link"],
                                comentarios=d1761.get("comentarios", []),
                                status=d1761.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.6.1 salvo! ({pts:.2f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.6.1", on_click=salvar_1761).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.6.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.7 (Mamógrafos na Rede Própria)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.7 • Equipamentos de Mamografia na Rede Própria").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui estabelecimentos de saúde da rede própria com mamógrafos?"
                        ).classes("text-base font-bold text-black mb-6")

                        d177 = res_data.get("17.7") or {}
                        raw_val_177 = str(d177.get("valor", "none"))
                        raw_link_177 = str(d177.get("link") or "")

                        state_177 = {
                            "opcao": raw_val_177 if raw_val_177 in ["sim", "nao"] else "none",
                            "link": raw_link_177,
                        }

                        opts_177 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_177 = ui.radio(
                            options=opts_177,
                            value=state_177["opcao"]
                        ).classes("mb-4")
                        rad_177.bind_value(state_177, "opcao")

                        lbl_pts_177 = ui.label("Nota 17.7: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_177():
                            pts = 0.0
                            lbl_pts_177.set_text("📊 Nota 17.7: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES dos Equipamentos:",
                            value=raw_link_177,
                            placeholder="Link da ficha CNES ou inventário de mamógrafos...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_177, "link"
                        )

                        def salvar_177():
                            pts = recalc_177()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.7",
                                valor=state_177["opcao"],
                                pontos=pts,
                                link=state_177["link"],
                                comentarios=d177.get("comentarios", []),
                                status=d177.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.7 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.7", on_click=salvar_177).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.7", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.7.1 (Produtividade dos Mamógrafos na Rede Própria)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.7.1 • Produtividade dos Mamógrafos da Rede Própria").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de exames realizados e a quantidade de mamógrafos em 2025:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: P = EX / MM. Se P >= 6.758 exames/ano -> 0,0 ponto | Se P < 6.758 exames/ano -> -5,0 pontos"
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1771 = res_data.get("17.7.1") or {}
                        raw_val_1771 = d1771.get("valor") or {}
                        if not isinstance(raw_val_1771, dict):
                            raw_val_1771 = {}

                        raw_link_1771 = str(d1771.get("link") or "")

                        state_1771 = {
                            "exames": int(raw_val_1771.get("exames", 0)),
                            "mamografos": int(raw_val_1771.get("mamografos", 0)),
                            "link": raw_link_1771,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            num_exames = ui.number(
                                label="Quantidade de exames de mamógrafo realizados em 2025 (EX):",
                                value=state_1771["exames"],
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined density=compact")
                            num_exames.bind_value(state_1771, "exames")

                            num_mamografos = ui.number(
                                label="Quantidade de mamógrafos da rede própria em 2025 (MM):",
                                value=state_1771["mamografos"],
                                min=0,
                                step=1,
                            ).classes("w-full").props("outlined density=compact")
                            num_mamografos.bind_value(state_1771, "mamografos")

                        lbl_prod_1771 = ui.label("Média: 0.00 exames/mamógrafo/ano").classes(
                            "text-sm font-semibold text-gray-700 mb-1"
                        )
                        lbl_pts_1771 = ui.label("Nota 17.7.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1771():
                            ex = int(state_1771["exames"] or 0)
                            mm = int(state_1771["mamografos"] or 0)

                            if mm > 0:
                                prod = ex / mm
                            else:
                                prod = 0.0

                            if prod >= 6758.0 and mm > 0:
                                pts = 0.0
                                lbl_pts_1771.classes(replace="text-sm font-bold text-green-600 mb-4")
                            else:
                                pts = -5.0
                                lbl_pts_1771.classes(replace="text-sm font-bold text-red-600 mb-4")

                            lbl_prod_1771.set_text(
                                f"📈 Produtividade (P): {prod:,.2f} exames/mamógrafo/ano (Meta: ≥ 6.758)"
                            )
                            lbl_pts_1771.set_text(f"📊 Nota 17.7.1: {pts:.1f} pontos")
                            return pts

                        num_exames.on("update:model-value", recalc_1771)
                        num_mamografos.on("update:model-value", recalc_1771)
                        recalc_1771()

                        ui.textarea(
                            label="Link de Evidência / Relatório SIA-SUS ou CNES:",
                            value=raw_link_1771,
                            placeholder="Link dos relatórios do SIA-SUS, SISMAMM ou fichas de equipamentos do CNES...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1771, "link"
                        )

                        def salvar_1771():
                            pts = recalc_1771()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.7.1",
                                valor={
                                    "exames": state_1771["exames"],
                                    "mamografos": state_1771["mamografos"],
                                },
                                pontos=pts,
                                link=state_1771["link"],
                                comentarios=d1771.get("comentarios", []),
                                status=d1771.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.7.1 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.7.1", on_click=salvar_1771).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.7.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.8 (Equipamentos de Ultrassom na Rede Própria)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.8 • Equipamentos de Ultrassom Convencional").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui estabelecimentos de saúde da rede própria com equipamentos de ultrassom convencional?"
                        ).classes("text-base font-bold text-black mb-6")

                        d178 = res_data.get("17.8") or {}
                        raw_val_178 = str(d178.get("valor", "none"))
                        raw_link_178 = str(d178.get("link") or "")

                        state_178 = {
                            "opcao": raw_val_178 if raw_val_178 in ["sim", "nao"] else "none",
                            "link": raw_link_178,
                        }

                        opts_178 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_178 = ui.radio(
                            options=opts_178,
                            value=state_178["opcao"]
                        ).classes("mb-4")
                        rad_178.bind_value(state_178, "opcao")

                        lbl_pts_178 = ui.label("Nota 17.8: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_178():
                            pts = 0.0
                            lbl_pts_178.set_text("📊 Nota 17.8: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES dos Equipamentos:",
                            value=raw_link_178,
                            placeholder="Link da ficha CNES ou inventário de equipamentos de ultrassom...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_178, "link"
                        )

                        def salvar_178():
                            pts = recalc_178()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.8",
                                valor=state_178["opcao"],
                                pontos=pts,
                                link=state_178["link"],
                                comentarios=d178.get("comentarios", []),
                                status=d178.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.8 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.8", on_click=salvar_178).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.8", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.8.1 (Comparativo de Produtividade dos Ultrassons)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.8.1 • Desempenho e Produtividade dos Exames de Ultrassom").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de exames e de equipamentos de ultrassom nos anos de 2023, 2024 e 2025:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: Se EX_2025 / EQ_2025 >= (EX_2023 + EX_2024) / (EQ_2023 + EQ_2024) -> 0,0 ponto | Caso contrário -> -5,0 pontos"
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1781 = res_data.get("17.8.1") or {}
                        raw_val_1781 = d1781.get("valor") or {}
                        if not isinstance(raw_val_1781, dict):
                            raw_val_1781 = {}

                        raw_link_1781 = str(d1781.get("link") or "")

                        state_1781 = {
                            "ex_2023": int(raw_val_1781.get("ex_2023", 0)),
                            "ex_2024": int(raw_val_1781.get("ex_2024", 0)),
                            "ex_2025": int(raw_val_1781.get("ex_2025", 0)),
                            "eq_2023": int(raw_val_1781.get("eq_2023", 0)),
                            "eq_2024": int(raw_val_1781.get("eq_2024", 0)),
                            "eq_2025": int(raw_val_1781.get("eq_2025", 0)),
                            "link": raw_link_1781,
                        }

                        ui.label("Quantidade de Exames Realizados (EX):").classes("text-sm font-bold text-gray-700 mt-2 mb-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            num_ex_2023 = ui.number(
                                label="Exames em 2023 (EXAA-2):",
                                value=state_1781["ex_2023"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_ex_2023.bind_value(state_1781, "ex_2023")

                            num_ex_2024 = ui.number(
                                label="Exames em 2024 (EXAA-1):",
                                value=state_1781["ex_2024"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_ex_2024.bind_value(state_1781, "ex_2024")

                            num_ex_2025 = ui.number(
                                label="Exames em 2025 (EXAA):",
                                value=state_1781["ex_2025"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_ex_2025.bind_value(state_1781, "ex_2025")

                        ui.label("Quantidade de Equipamentos (EQ):").classes("text-sm font-bold text-gray-700 mt-2 mb-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            num_eq_2023 = ui.number(
                                label="Equipamentos em 2023 (EQAA-2):",
                                value=state_1781["eq_2023"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_eq_2023.bind_value(state_1781, "eq_2023")

                            num_eq_2024 = ui.number(
                                label="Equipamentos em 2024 (EQAA-1):",
                                value=state_1781["eq_2024"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_eq_2024.bind_value(state_1781, "eq_2024")

                            num_eq_2025 = ui.number(
                                label="Equipamentos em 2025 (EQAA):",
                                value=state_1781["eq_2025"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_eq_2025.bind_value(state_1781, "eq_2025")

                        lbl_calc_info = ui.label("Produtividade 2025: 0.00 | Média Histórica (2023-2024): 0.00").classes(
                            "text-sm font-semibold text-gray-700 mb-1"
                        )
                        lbl_pts_1781 = ui.label("Nota 17.8.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1781():
                            ex23 = int(state_1781["ex_2023"] or 0)
                            ex24 = int(state_1781["ex_2024"] or 0)
                            ex25 = int(state_1781["ex_2025"] or 0)

                            eq23 = int(state_1781["eq_2023"] or 0)
                            eq24 = int(state_1781["eq_2024"] or 0)
                            eq25 = int(state_1781["eq_2025"] or 0)

                            prod_2025 = (ex25 / eq25) if eq25 > 0 else 0.0
                            
                            soma_ex_hist = ex23 + ex24
                            soma_eq_hist = eq23 + eq24
                            prod_hist = (soma_ex_hist / soma_eq_hist) if soma_eq_hist > 0 else 0.0

                            if prod_2025 >= prod_hist and eq25 > 0:
                                pts = 0.0
                                lbl_pts_1781.classes(replace="text-sm font-bold text-green-600 mb-4")
                            else:
                                pts = -5.0
                                lbl_pts_1781.classes(replace="text-sm font-bold text-red-600 mb-4")

                            lbl_calc_info.set_text(
                                f"📈 Produtividade 2025: {prod_2025:,.2f} exames/eq | Média Anterior (23-24): {prod_hist:,.2f} exames/eq"
                            )
                            lbl_pts_1781.set_text(f"📊 Nota 17.8.1: {pts:.1f} pontos")
                            return pts

                        for element in [num_ex_2023, num_ex_2024, num_ex_2025, num_eq_2023, num_eq_2024, num_eq_2025]:
                            element.on("update:model-value", recalc_1781)

                        recalc_1781()

                        ui.textarea(
                            label="Link de Evidência / Relatório SIA-SUS e CNES:",
                            value=raw_link_1781,
                            placeholder="Link dos relatórios com séries históricas do SIA-SUS e cadastros do CNES...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1781, "link"
                        )

                        def salvar_1781():
                            pts = recalc_1781()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.8.1",
                                valor={
                                    "ex_2023": state_1781["ex_2023"],
                                    "ex_2024": state_1781["ex_2024"],
                                    "ex_2025": state_1781["ex_2025"],
                                    "eq_2023": state_1781["eq_2023"],
                                    "eq_2024": state_1781["eq_2024"],
                                    "eq_2025": state_1781["eq_2025"],
                                },
                                pontos=pts,
                                link=state_1781["link"],
                                comentarios=d1781.get("comentarios", []),
                                status=d1781.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.8.1 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.8.1", on_click=salvar_1781).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.8.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.9 (Gestão de Hospital ou Santa Casa)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.9 • Gestão Hospitalar / Santa Casa").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui hospital ou Santa Casa sob sua gestão?"
                        ).classes("text-base font-bold text-black mb-6")

                        d179 = res_data.get("17.9") or {}
                        raw_val_179 = str(d179.get("valor", "none"))
                        raw_link_179 = str(d179.get("link") or "")

                        state_179 = {
                            "opcao": raw_val_179 if raw_val_179 in ["sim", "nao"] else "none",
                            "link": raw_link_179,
                        }

                        opts_179 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_179 = ui.radio(
                            options=opts_179,
                            value=state_179["opcao"]
                        ).classes("mb-4")
                        rad_179.bind_value(state_179, "opcao")

                        lbl_pts_179 = ui.label("Nota 17.9: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_179():
                            pts = 0.0
                            lbl_pts_179.set_text("📊 Nota 17.9: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES do Hospital:",
                            value=raw_link_179,
                            placeholder="Link da ficha CNES ou ato de contratualização/gestão hospitalar...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_179, "link"
                        )

                        def salvar_179():
                            pts = recalc_179()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.9",
                                valor=state_179["opcao"],
                                pontos=pts,
                                link=state_179["link"],
                                comentarios=d179.get("comentarios", []),
                                status=d179.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 17.9 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.9", on_click=salvar_179).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.9", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 17.9.1 (Taxa de Ocupação Hospitalar Geral)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.9.1 • Taxa de Ocupação Hospitalar (TO)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o total de pacientes-dia e o número total de leitos-dia em 2025:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: TO = PA / LE. Faixa ideal: 75% a 90% (0,0 ponto). Se TO < 75% ou TO > 90% -> -5,0 pontos."
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1791 = res_data.get("17.9.1") or {}
                        raw_val_1791 = d1791.get("valor") or {}
                        if not isinstance(raw_val_1791, dict):
                            raw_val_1791 = {}

                        raw_link_1791 = str(d1791.get("link") or "")

                        state_1791 = {
                            "pa": int(raw_val_1791.get("pa", 0)),
                            "le": int(raw_val_1791.get("le", 0)),
                            "link": raw_link_1791,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            num_pa = ui.number(
                                label="Total de pacientes-dia em 2025 (PA):",
                                value=state_1791["pa"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_pa.bind_value(state_1791, "pa")

                            num_le = ui.number(
                                label="Total de leitos-dia em 2025 (LE):",
                                value=state_1791["le"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_le.bind_value(state_1791, "le")

                        lbl_to_1791 = ui.label("Taxa de Ocupação (TO): 0.00%").classes(
                            "text-sm font-semibold text-gray-700 mb-1"
                        )
                        lbl_pts_1791 = ui.label("Nota 17.9.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1791():
                            pa = int(state_1791["pa"] or 0)
                            le = int(state_1791["le"] or 0)

                            to_pct = (pa / le * 100.0) if le > 0 else 0.0

                            # Faixa ideal entre 75% e 90%
                            if 75.0 <= to_pct <= 90.0 and le > 0:
                                pts = 0.0
                                lbl_pts_1791.classes(replace="text-sm font-bold text-green-600 mb-4")
                            else:
                                pts = -5.0
                                lbl_pts_1791.classes(replace="text-sm font-bold text-red-600 mb-4")

                            lbl_to_1791.set_text(
                                f"📈 Taxa de Ocupação (TO): {to_pct:.2f}% (Meta Ideal: 75,00% a 90,00%)"
                            )
                            lbl_pts_1791.set_text(f"📊 Nota 17.9.1: {pts:.1f} pontos")
                            return pts

                        num_pa.on("update:model-value", recalc_1791)
                        num_le.on("update:model-value", recalc_1791)
                        recalc_1791()

                        ui.textarea(
                            label="Link de Evidência / Relatório SIHD-SUS ou Censo Hospitalar:",
                            value=raw_link_1791,
                            placeholder="Link do relatório com dados do Censo Hospitalar e movimentação de leitos...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1791, "link"
                        )

                        def salvar_1791():
                            pts = recalc_1791()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.9.1",
                                valor={
                                    "pa": state_1791["pa"],
                                    "le": state_1791["le"],
                                },
                                pontos=pts,
                                link=state_1791["link"],
                                comentarios=d1791.get("comentarios", []),
                                status=d1791.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.9.1 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.9.1", on_click=salvar_1791).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.9.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 17.9.2 (Hospitais com Taxa de Ocupação Superior a 100%)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("17.9.2 • Hospitais Sob Gestão Municipal com Superlotação (> 100%)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe o número de hospitais da rede própria que tiveram taxa de ocupação superior a 100%:"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "Fórmula: Se TOAA <= (TOAA-2 + TOAA-1) / 2 -> 0,0 ponto | Se TOAA > (TOAA-2 + TOAA-1) / 2 -> -5,0 pontos"
                        ).classes("text-xs font-semibold text-gray-600 mb-6")

                        d1792 = res_data.get("17.9.2") or {}
                        raw_val_1792 = d1792.get("valor") or {}
                        if not isinstance(raw_val_1792, dict):
                            raw_val_1792 = {}

                        raw_link_1792 = str(d1792.get("link") or "")

                        state_1792 = {
                            "to_2023": int(raw_val_1792.get("to_2023", 0)),
                            "to_2024": int(raw_val_1792.get("to_2024", 0)),
                            "to_2025": int(raw_val_1792.get("to_2025", 0)),
                            "link": raw_link_1792,
                        }

                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            num_to_2023 = ui.number(
                                label="Nº Hospitais > 100% em 2023 (TOAA-2):",
                                value=state_1792["to_2023"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_to_2023.bind_value(state_1792, "to_2023")

                            num_to_2024 = ui.number(
                                label="Nº Hospitais > 100% em 2024 (TOAA-1):",
                                value=state_1792["to_2024"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_to_2024.bind_value(state_1792, "to_2024")

                            num_to_2025 = ui.number(
                                label="Nº Hospitais > 100% em 2025 (TOAA):",
                                value=state_1792["to_2025"],
                                min=0,
                                precision=0
                            ).classes("w-full").props("outlined density=compact")
                            num_to_2025.bind_value(state_1792, "to_2025")

                        lbl_comp_1792 = ui.label("Nº em 2025: 0 | Média (2023-2024): 0.00").classes(
                            "text-sm font-semibold text-gray-700 mb-1"
                        )
                        lbl_pts_1792 = ui.label("Nota 17.9.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1792():
                            t23 = int(state_1792["to_2023"] or 0)
                            t24 = int(state_1792["to_2024"] or 0)
                            t25 = int(state_1792["to_2025"] or 0)

                            media_hist = (t23 + t24) / 2.0

                            if t25 <= media_hist:
                                pts = 0.0
                                lbl_pts_1792.classes(replace="text-sm font-bold text-green-600 mb-4")
                            else:
                                pts = -5.0
                                lbl_pts_1792.classes(replace="text-sm font-bold text-red-600 mb-4")

                            lbl_comp_1792.set_text(
                                f"📈 Nº Hospitais com >100% em 2025: {t25} | Média dos Anos Anteriores: {media_hist:.2f}"
                            )
                            lbl_pts_1792.set_text(f"📊 Nota 17.9.2: {pts:.1f} pontos")
                            return pts

                        for element in [num_to_2023, num_to_2024, num_to_2025]:
                            element.on("update:model-value", recalc_1792)

                        recalc_1792()

                        ui.textarea(
                            label="Link de Evidência / Relatório Censo Hospitalar:",
                            value=raw_link_1792,
                            placeholder="Link do relatório comprobatório das taxas de ocupação por hospital...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1792, "link"
                        )

                        def salvar_1792():
                            pts = recalc_1792()
                            save_resposta(
                                ano=ano_sel,
                                qid="17.9.2",
                                valor={
                                    "to_2023": state_1792["to_2023"],
                                    "to_2024": state_1792["to_2024"],
                                    "to_2025": state_1792["to_2025"],
                                },
                                pontos=pts,
                                link=state_1792["link"],
                                comentarios=d1792.get("comentarios", []),
                                status=d1792.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 17.9.2 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 17.9.2", on_click=salvar_1792).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("17.9.2", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 18.0 (Demanda por Assistência em Saúde Mental / RAPS)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.0 • Demanda de Ações e Serviços de Saúde Mental").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "No município, há demanda de ações e de serviços voltados para a assistência aos portadores de transtornos mentais, bem como para usuários de substâncias psicoativas?"
                        ).classes("text-base font-bold text-black mb-6")

                        d180 = res_data.get("18.0") or {}
                        raw_val_180 = str(d180.get("valor", "none"))
                        raw_link_180 = str(d180.get("link") or "")

                        state_180 = {
                            "opcao": raw_val_180 if raw_val_180 in ["sim", "nao"] else "none",
                            "link": raw_link_180,
                        }

                        opts_180 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_180 = ui.radio(
                            options=opts_180,
                            value=state_180["opcao"]
                        ).classes("mb-4")
                        rad_180.bind_value(state_180, "opcao")

                        lbl_pts_180 = ui.label("Nota 18.0: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_180():
                            pts = 0.0
                            lbl_pts_180.set_text("📊 Nota 18.0: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Diagnóstico de Saúde Mental / PMS:",
                            value=raw_link_180,
                            placeholder="Link do Plano Municipal de Saúde, dados epidemiológicos ou diagnóstico da RAPS...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_180, "link"
                        )

                        def salvar_180():
                            pts = recalc_180()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.0",
                                valor=state_180["opcao"],
                                pontos=pts,
                                link=state_180["link"],
                                comentarios=d180.get("comentarios", []),
                                status=d180.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.0", on_click=salvar_180).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.1 (Plano de Ação para Inclusão na RAPS)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.1 • Plano de Ação Municipal RAPS").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Realizou Plano de Ação municipal para inclusão do município à sua RAPS?"
                        ).classes("text-base font-bold text-black mb-6")

                        d181 = res_data.get("18.1") or {}
                        raw_val_181 = str(d181.get("valor", "none"))
                        raw_link_181 = str(d181.get("link") or "")

                        state_181 = {
                            "opcao": raw_val_181 if raw_val_181 in ["sim", "nao"] else "none",
                            "link": raw_link_181,
                        }

                        opts_181 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-10,0 pontos)",
                        }

                        rad_181 = ui.radio(
                            options=opts_181,
                            value=state_181["opcao"]
                        ).classes("mb-4")
                        rad_181.bind_value(state_181, "opcao")

                        lbl_pts_181 = ui.label("Nota 18.1: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_181():
                            val = state_181["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_181.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -10.0
                                lbl_pts_181.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_181.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_181.set_text(f"📊 Nota 18.1: {pts:.1f} pontos")
                            return pts

                        rad_181.on("update:model-value", recalc_181)
                        recalc_181()

                        ui.textarea(
                            label="Link de Evidência / Plano de Ação RAPS Aprovado:",
                            value=raw_link_181,
                            placeholder="Link do Plano de Ação da RAPS pactuado na CIB/CIR ou Diário Oficial...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_181, "link"
                        )

                        def salvar_181():
                            pts = recalc_181()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.1",
                                valor=state_181["opcao"],
                                pontos=pts,
                                link=state_181["link"],
                                comentarios=d181.get("comentarios", []),
                                status=d181.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 18.1 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.1", on_click=salvar_181).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.2 (Integração Intersetorial da Saúde Mental)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.2 • Integração com Outros Órgãos Municipais").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Secretaria Municipal de Saúde (ou equivalente) está integrada com os outros órgãos municipais de forma a ampliar a oferta de ações e de serviços voltados para a assistência aos portadores de transtornos mentais?"
                        ).classes("text-base font-bold text-black mb-6")

                        d182 = res_data.get("18.2") or {}
                        raw_val_182 = str(d182.get("valor", "none"))
                        raw_link_182 = str(d182.get("link") or "")

                        state_182 = {
                            "opcao": raw_val_182 if raw_val_182 in ["sim", "nao"] else "none",
                            "link": raw_link_182,
                        }

                        opts_182 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-5,0 pontos)",
                        }

                        rad_182 = ui.radio(
                            options=opts_182,
                            value=state_182["opcao"]
                        ).classes("mb-4")
                        rad_182.bind_value(state_182, "opcao")

                        lbl_pts_182 = ui.label("Nota 18.2: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_182():
                            val = state_182["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_182.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -5.0
                                lbl_pts_182.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_182.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_182.set_text(f"📊 Nota 18.2: {pts:.1f} pontos")
                            return pts

                        rad_182.on("update:model-value", recalc_182)
                        recalc_182()

                        ui.textarea(
                            label="Link de Evidência / Atas e Acordos de Cooperação Intersetorial:",
                            value=raw_link_182,
                            placeholder="Link de decretos, comitês intersetoriais, atas de reuniões com Assistência Social/Educação...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_182, "link"
                        )

                        def salvar_182():
                            pts = recalc_182()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.2",
                                valor=state_182["opcao"],
                                pontos=pts,
                                link=state_182["link"],
                                comentarios=d182.get("comentarios", []),
                                status=d182.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 18.2 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.2", on_click=salvar_182).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.2.1 (Formas de Integração dos Órgãos)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.2.1 • Formas de Integração Intersetorial").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale as formas de integração estabelecidas entre os órgãos:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1821 = res_data.get("18.2.1") or {}
                        raw_val_1821 = d1821.get("valor") or []
                        if not isinstance(raw_val_1821, list):
                            raw_val_1821 = []

                        raw_link_1821 = str(d1821.get("link") or "")

                        state_1821 = {
                            "opcoes": raw_val_1821,
                            "link": raw_link_1821,
                        }

                        chk_options = [
                            ("acoes_estabelecidas", "Ações estabelecidas"),
                            ("papeis_definidos", "Papéis definidos"),
                            ("metas_estabelecidas", "Metas estabelecidas"),
                            ("prazos", "Prazos"),
                            ("normas_complementares", "Normas complementares firmadas entre órgãos"),
                            ("outros", "Outros"),
                        ]

                        for key, label_text in chk_options:
                            chk = ui.checkbox(
                                text=label_text,
                                value=(key in state_1821["opcoes"])
                            ).classes("mb-1")

                            def make_on_change(k=key):
                                def on_change(e):
                                    if e.value and k not in state_1821["opcoes"]:
                                        state_1821["opcoes"].append(k)
                                    elif not e.value and k in state_1821["opcoes"]:
                                        state_1821["opcoes"].remove(k)
                                return on_change

                            chk.on("update:model-value", make_on_change(key))

                        ui.label("Nota 18.2.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Documentação Regimental:",
                            value=raw_link_1821,
                            placeholder="Link do fluxo intersetorial pactuado, protocolos ou resoluções...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1821, "link"
                        )

                        def salvar_1821():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="18.2.1",
                                valor=state_1821["opcoes"],
                                pontos=pts,
                                link=state_1821["link"],
                                comentarios=d1821.get("comentarios", []),
                                status=d1821.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.2.1", on_click=salvar_1821).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.2.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.2.1.1 (Atingimento de Metas de Saúde Mental 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.2.1.1 • Atingimento de Metas no Exercício 2025").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "As metas estabelecidas para o exercício 2025 foram atingidas?"
                        ).classes("text-base font-bold text-black mb-6")

                        d18211 = res_data.get("18.2.1.1") or {}
                        raw_val_18211 = str(d18211.get("valor", "none"))
                        raw_link_18211 = str(d18211.get("link") or "")

                        state_18211 = {
                            "opcao": raw_val_18211 if raw_val_18211 in ["todas", "maior_parte", "menor_parte", "nao"] else "none",
                            "link": raw_link_18211,
                        }

                        opts_18211 = {
                            "none": "Selecione uma opção...",
                            "todas": "Sim, todas as metas foram atingidas",
                            "maior_parte": "Sim, a maior parte das metas foram atingidas",
                            "menor_parte": "Sim, a menor parte das metas foram atingidas",
                            "nao": "Não",
                        }

                        rad_18211 = ui.radio(
                            options=opts_18211,
                            value=state_18211["opcao"]
                        ).classes("mb-4")
                        rad_18211.bind_value(state_18211, "opcao")

                        lbl_pts_18211 = ui.label("Nota 18.2.1.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_18211():
                            pts = 0.0
                            lbl_pts_18211.set_text("📊 Nota 18.2.1.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Relatório Anual de Gestão (RAG) 2025:",
                            value=raw_link_18211,
                            placeholder="Link do RAG 2025 ou relatório de monitoramento das metas intersetoriais...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_18211, "link"
                        )

                        def salvar_18211():
                            pts = recalc_18211()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.2.1.1",
                                valor=state_18211["opcao"],
                                pontos=pts,
                                link=state_18211["link"],
                                comentarios=d18211.get("comentarios", []),
                                status=d18211.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.2.1.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.2.1.1", on_click=salvar_18211).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.2.1.1", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 18.3 (Adesão ao Programa Recomeço)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.3 • Programa Recomeço").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O Município formalizou termo de adesão com o Programa Recomeço (Art. 7º, Decreto nº 61.674/2015) ou outro programa que venha a substituí-lo?"
                        ).classes("text-base font-bold text-black mb-6")

                        d183 = res_data.get("18.3") or {}
                        raw_val_183 = str(d183.get("valor", "none"))
                        raw_link_183 = str(d183.get("link") or "")

                        state_183 = {
                            "opcao": raw_val_183 if raw_val_183 in ["sim", "nao"] else "none",
                            "link": raw_link_183,
                        }

                        opts_183 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_183 = ui.radio(
                            options=opts_183,
                            value=state_183["opcao"]
                        ).classes("mb-4")
                        rad_183.bind_value(state_183, "opcao")

                        lbl_pts_183 = ui.label("Nota 18.3: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_183():
                            pts = 0.0
                            lbl_pts_183.set_text("📊 Nota 18.3: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Termo de Adesão ou Publicação Oficial:",
                            value=raw_link_183,
                            placeholder="Link do termo de adesão assinado, convênio ou publicação no Diário Oficial...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_183, "link"
                        )

                        def salvar_183():
                            pts = recalc_183()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.3",
                                valor=state_183["opcao"],
                                pontos=pts,
                                link=state_183["link"],
                                comentarios=d183.get("comentarios", []),
                                status=d183.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.3", on_click=salvar_183).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.4 (Indicadores Específicos para Atenção Psicossocial)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.4 • Indicadores da Atenção Psicossocial").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui indicadores específicos para a Atenção Psicossocial?"
                        ).classes("text-base font-bold text-black mb-6")

                        d184 = res_data.get("18.4") or {}
                        raw_val_184 = str(d184.get("valor", "none"))
                        raw_link_184 = str(d184.get("link") or "")

                        state_184 = {
                            "opcao": raw_val_184 if raw_val_184 in ["sim", "nao"] else "none",
                            "link": raw_link_184,
                        }

                        opts_184 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-5,0 pontos)",
                        }

                        rad_184 = ui.radio(
                            options=opts_184,
                            value=state_184["opcao"]
                        ).classes("mb-4")
                        rad_184.bind_value(state_184, "opcao")

                        lbl_pts_184 = ui.label("Nota 18.4: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_184():
                            val = state_184["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_184.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -5.0
                                lbl_pts_184.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_184.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_184.set_text(f"📊 Nota 18.4: {pts:.1f} pontos")
                            return pts

                        rad_184.on("update:model-value", recalc_184)
                        recalc_184()

                        ui.textarea(
                            label="Link de Evidência / Matriz ou Painel de Indicadores da RAPS:",
                            value=raw_link_184,
                            placeholder="Link do documento com a definição, cadernos de indicadores ou painéis de monitoramento...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_184, "link"
                        )

                        def salvar_184():
                            pts = recalc_184()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.4",
                                valor=state_184["opcao"],
                                pontos=pts,
                                link=state_184["link"],
                                comentarios=d184.get("comentarios", []),
                                status=d184.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 18.4 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.4", on_click=salvar_184).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.4", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.4.1 (Tipos de Indicadores da Atenção Psicossocial)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.4.1 • Tipos de Indicadores Utilizados").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Assinale os tipos de indicadores da Atenção Psicossocial:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1841 = res_data.get("18.4.1") or {}
                        raw_val_1841 = d1841.get("valor") or []
                        if not isinstance(raw_val_1841, list):
                            raw_val_1841 = []

                        raw_link_1841 = str(d1841.get("link") or "")

                        state_1841 = {
                            "opcoes": raw_val_1841,
                            "link": raw_link_1841,
                        }

                        chk_options_1841 = [
                            ("drogas", "Para Drogas (transtornos mentais incluindo aqueles relacionados ao uso de substâncias psicoativas)"),
                            ("saude_mental", "Para Saúde Mental (transtornos mentais graves e persistentes)"),
                            ("lacos_sociais", "Para outras situações clínicas que impossibilitem estabelecer laços sociais e realizar projetos de vida"),
                            ("infantil", "Para Drogas e/ou Saúde Mental para crianças em específico"),
                            ("outros", "Outros"),
                        ]

                        for key, label_text in chk_options_1841:
                            chk = ui.checkbox(
                                text=label_text,
                                value=(key in state_1841["opcoes"])
                            ).classes("mb-1")

                            def make_on_change(k=key):
                                def on_change(e):
                                    if e.value and k not in state_1841["opcoes"]:
                                        state_1841["opcoes"].append(k)
                                    elif not e.value and k in state_1841["opcoes"]:
                                        state_1841["opcoes"].remove(k)
                                return on_change

                            chk.on("update:model-value", make_on_change(key))

                        ui.label("Nota 18.4.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Fichas Técnicas dos Indicadores:",
                            value=raw_link_1841,
                            placeholder="Link do relatório com o detalhamento e resultados dos indicadores assinalados...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1841, "link"
                        )

                        def salvar_1841():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="18.4.1",
                                valor=state_1841["opcoes"],
                                pontos=pts,
                                link=state_1841["link"],
                                comentarios=d1841.get("comentarios", []),
                                status=d1841.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.4.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.4.1", on_click=salvar_1841).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.4.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5 (Porte Populacional > 15 mil habitantes - IBGE 2025)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5 • População Municipal (IBGE 2025)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município possui população superior a 15 mil habitantes (conforme dados do IBGE 2025)?"
                        ).classes("text-base font-bold text-black mb-6")

                        d185 = res_data.get("18.5") or {}
                        raw_val_185 = str(d185.get("valor", "none"))
                        raw_link_185 = str(d185.get("link") or "")

                        state_185 = {
                            "opcao": raw_val_185 if raw_val_185 in ["sim", "nao"] else "none",
                            "link": raw_link_185,
                        }

                        opts_185 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_185 = ui.radio(
                            options=opts_185,
                            value=state_185["opcao"]
                        ).classes("mb-4")
                        rad_185.bind_value(state_185, "opcao")

                        lbl_pts_185 = ui.label("Nota 18.5: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_185():
                            pts = 0.0
                            lbl_pts_185.set_text("📊 Nota 18.5: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Estimativa Populacional IBGE 2025:",
                            value=raw_link_185,
                            placeholder="Link da certidão ou publicação oficial do IBGE 2025...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_185, "link"
                        )

                        def salvar_185():
                            pts = recalc_185()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5",
                                valor=state_185["opcao"],
                                pontos=pts,
                                link=state_185["link"],
                                comentarios=d185.get("comentarios", []),
                                status=d185.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5", on_click=salvar_185).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.1 (Dimensionamento da Rede CAPS / Unidades de Acolhimento)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.1 • Adequação da Rede CAPS e Unidades de Acolhimento").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A quantidade de CAPS e Unidades de Acolhimento Adulto e Infanto-Juvenil segundo a totalidade de habitantes do município é adequada?"
                        ).classes("text-base font-bold text-black mb-6")

                        d1851 = res_data.get("18.5.1") or {}
                        raw_val_1851 = str(d1851.get("valor", "none"))
                        raw_link_1851 = str(d1851.get("link") or "")

                        state_1851 = {
                            "opcao": raw_val_1851 if raw_val_1851 in ["sim", "nao"] else "none",
                            "link": raw_link_1851,
                        }

                        opts_1851 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_1851 = ui.radio(
                            options=opts_1851,
                            value=state_1851["opcao"]
                        ).classes("mb-4")
                        rad_1851.bind_value(state_1851, "opcao")

                        lbl_pts_1851 = ui.label("Nota 18.5.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1851():
                            pts = 0.0
                            lbl_pts_1851.set_text("📊 Nota 18.5.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES ou Relatório da RAPS:",
                            value=raw_link_1851,
                            placeholder="Link do CNES das unidades CAPS/UAA/UAI ou plano de expansão da RAPS...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1851, "link"
                        )

                        def salvar_1851():
                            pts = recalc_1851()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.1",
                                valor=state_1851["opcao"],
                                pontos=pts,
                                link=state_1851["link"],
                                comentarios=d1851.get("comentarios", []),
                                status=d1851.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.5.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.1", on_click=salvar_1851).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.2 (Quantidade de Estabelecimentos da RAPS)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.2 • Quantidade de Estabelecimentos da Rede").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de estabelecimentos do município por modalidade:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1852 = res_data.get("18.5.2") or {}
                        raw_val_1852 = d1852.get("valor") or {}
                        if not isinstance(raw_val_1852, dict):
                            raw_val_1852 = {}

                        raw_link_1852 = str(d1852.get("link") or "")

                        tipos_estab_1852 = [
                            ("caps_i", "I - CAPS I"),
                            ("caps_ii", "II - CAPS II"),
                            ("caps_iii", "III - CAPS III"),
                            ("caps_ad", "IV - CAPS AD"),
                            ("caps_ad_ii", "V - CAPS AD II"),
                            ("caps_ad_iii", "VI - CAPS AD III"),
                            ("caps_ij", "VII - CAPS i"),
                            ("caps_ij_ii", "VIII - CAPS i II"),
                            ("caps_ad_iv", "IX - CAPS AD IV"),
                            ("uaa", "X - Unidade de Acolhimento Adulto"),
                            ("uai", "XI - Unidade de Acolhimento Infantil"),
                        ]

                        state_1852 = {
                            "qtdes": {
                                k: int(raw_val_1852.get(k, 0)) for k, _ in tipos_estab_1852
                            },
                            "link": raw_link_1852,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            for key, label_text in tipos_estab_1852:
                                num_input = ui.number(
                                    label=label_text,
                                    value=state_1852["qtdes"][key],
                                    min=0,
                                    precision=0,
                                ).classes("w-full").props("outlined dense")
                                num_input.bind_value(state_1852["qtdes"], key)

                        ui.label("Nota 18.5.2: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Consulta CNES ou Cadastro Oficial:",
                            value=raw_link_1852,
                            placeholder="Link das fichas CNES dos estabelecimentos de saúde mental...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1852, "link"
                        )

                        def salvar_1852():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.2",
                                valor=state_1852["qtdes"],
                                pontos=pts,
                                link=state_1852["link"],
                                comentarios=d1852.get("comentarios", []),
                                status=d1852.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.5.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.2", on_click=salvar_1852).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.3 (Integração dos Serviços com Sistema de Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.3 • Regulação dos Serviços e Vagas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Todos os serviços assistenciais ofertados pelo CAPS e Unidades de Acolhimento (vagas) estão disponibilizados no sistema de regulação?"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "*(Pode estar cadastrado no sistema de regulação municipal e/ou estadual)*"
                        ).classes("text-sm italic text-gray-600 mb-6")

                        d1853 = res_data.get("18.5.3") or {}
                        raw_val_1853 = str(d1853.get("valor", "none"))
                        raw_link_1853 = str(d1853.get("link") or "")

                        state_1853 = {
                            "opcao": raw_val_1853 if raw_val_1853 in ["sim", "nao"] else "none",
                            "link": raw_link_1853,
                        }

                        opts_1853 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-10,0 pontos)",
                        }

                        rad_1853 = ui.radio(
                            options=opts_1853,
                            value=state_1853["opcao"]
                        ).classes("mb-4")
                        rad_1853.bind_value(state_1853, "opcao")

                        lbl_pts_1853 = ui.label("Nota 18.5.3: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1853():
                            val = state_1853["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_1853.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -10.0
                                lbl_pts_1853.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_1853.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_1853.set_text(f"📊 Nota 18.5.3: {pts:.1f} pontos")
                            return pts

                        rad_1853.on("update:model-value", recalc_1853)
                        recalc_1853()

                        ui.textarea(
                            label="Link de Evidência / Extrato do Sistema de Regulação (SISREG/CROSS):",
                            value=raw_link_1853,
                            placeholder="Link do relatório do sistema de regulação onde os serviços e vagas estão ofertados...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1853, "link"
                        )

                        def salvar_1853():
                            pts = recalc_1853()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.3",
                                valor=state_1853["opcao"],
                                pontos=pts,
                                link=state_1853["link"],
                                comentarios=d1853.get("comentarios", []),
                                status=d1853.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 18.5.3 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.3", on_click=salvar_1853).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.3.1 (Quantidade de Vagas Cadastradas no Sistema de Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.3.1 • Quantidade de Vagas Reguladas").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de vagas cadastradas no sistema de regulação (municipal e/ou estadual):"
                        ).classes("text-base font-bold text-black mb-4")

                        d18531 = res_data.get("18.5.3.1") or {}
                        raw_val_18531 = d18531.get("valor") or {}
                        if not isinstance(raw_val_18531, dict):
                            raw_val_18531 = {}

                        raw_link_18531 = str(d18531.get("link") or "")

                        tipos_vagas_18531 = [
                            ("caps_i", "I - CAPS I"),
                            ("caps_ii", "II - CAPS II"),
                            ("caps_iii", "III - CAPS III"),
                            ("caps_ad", "IV - CAPS AD"),
                            ("caps_ad_ii", "V - CAPS AD II"),
                            ("caps_ad_iii", "VI - CAPS AD III"),
                            ("caps_ij", "VII - CAPS i"),
                            ("caps_ij_ii", "VIII - CAPS i II"),
                            ("caps_ad_iv", "IX - CAPS AD IV"),
                            ("uaa", "X - Unidade de Acolhimento Adulto"),
                            ("uai", "XI - Unidade de Acolhimento Infantil"),
                        ]

                        state_18531 = {
                            "vagas": {
                                k: int(raw_val_18531.get(k, 0)) for k, _ in tipos_vagas_18531
                            },
                            "link": raw_link_18531,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            for key, label_text in tipos_vagas_18531:
                                num_input = ui.number(
                                    label=label_text,
                                    value=state_18531["vagas"][key],
                                    min=0,
                                    precision=0,
                                ).classes("w-full").props("outlined dense")
                                num_input.bind_value(state_18531["vagas"], key)

                        ui.label("Nota 18.5.3.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Relatório da Grade de Vagas no Sistema de Regulação:",
                            value=raw_link_18531,
                            placeholder="Link da tela ou relatório do sistema de regulação demonstrando a oferta de vagas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_18531, "link"
                        )

                        def salvar_18531():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.3.1",
                                valor=state_18531["vagas"],
                                pontos=pts,
                                link=state_18531["link"],
                                comentarios=d18531.get("comentarios", []),
                                status=d18531.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.5.3.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.3.1", on_click=salvar_18531).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.3.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.4 (Suficiência da Oferta de Vagas dos CAPS)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.4 • Suficiência das Vagas nos CAPS").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A quantidade de vagas dos CAPS é suficiente para demanda da população que apresenta prioritariamente, intenso sofrimento psíquico decorrente de transtornos mentais graves e persistentes, incluindo aqueles relacionados ao uso de substâncias psicoativas, e outras situações clínicas?"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "*(Informar adequação por CAPS, a depender da existência dos equipamentos em cada município)*"
                        ).classes("text-sm italic text-gray-600 mb-6")

                        d1854 = res_data.get("18.5.4") or {}
                        raw_val_1854 = str(d1854.get("valor", "none"))
                        raw_link_1854 = str(d1854.get("link") or "")

                        state_1854 = {
                            "opcao": raw_val_1854 if raw_val_1854 in ["sim", "nao"] else "none",
                            "link": raw_link_1854,
                        }

                        opts_1854 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-10,0 pontos)",
                        }

                        rad_1854 = ui.radio(
                            options=opts_1854,
                            value=state_1854["opcao"]
                        ).classes("mb-4")
                        rad_1854.bind_value(state_1854, "opcao")

                        lbl_pts_1854 = ui.label("Nota 18.5.4: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_1854():
                            val = state_1854["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_1854.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -10.0
                                lbl_pts_1854.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_1854.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_1854.set_text(f"📊 Nota 18.5.4: {pts:.1f} pontos")
                            return pts

                        rad_1854.on("update:model-value", recalc_1854)
                        recalc_1854()

                        ui.textarea(
                            label="Link de Evidência / Relatório Demanda x Capacidade da RAPS:",
                            value=raw_link_1854,
                            placeholder="Link do diagnóstico territorial, fila de espera ou relatório técnico da rede de atenção psicossocial...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1854, "link"
                        )

                        def salvar_1854():
                            pts = recalc_1854()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.4",
                                valor=state_1854["opcao"],
                                pontos=pts,
                                link=state_1854["link"],
                                comentarios=d1854.get("comentarios", []),
                                status=d1854.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 18.5.4 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.4", on_click=salvar_1854).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.4", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.5.5 (Quantidade de Vagas Ofertadas pelo Município)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.5.5 • Vagas Ofertadas na Rede").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de vagas ofertadas pelo município por modalidade:"
                        ).classes("text-base font-bold text-black mb-4")

                        d1855 = res_data.get("18.5.5") or {}
                        raw_val_1855 = d1855.get("valor") or {}
                        if not isinstance(raw_val_1855, dict):
                            raw_val_1855 = {}

                        raw_link_1855 = str(d1855.get("link") or "")

                        tipos_vagas_1855 = [
                            ("caps_i", "I - CAPS I"),
                            ("caps_ii", "II - CAPS II"),
                            ("caps_iii", "III - CAPS III"),
                            ("caps_ad", "IV - CAPS AD"),
                            ("caps_ad_ii", "V - CAPS AD II"),
                            ("caps_ad_iii", "VI - CAPS AD III"),
                            ("caps_ij", "VII - CAPS i"),
                            ("caps_ij_ii", "VIII - CAPS i II"),
                            ("caps_ad_iv", "IX - CAPS AD IV"),
                            ("uaa", "X - Unidade de Acolhimento Adulto"),
                            ("uai", "XI - Unidade de Acolhimento Infantil"),
                        ]

                        state_1855 = {
                            "vagas": {
                                k: int(raw_val_1855.get(k, 0)) for k, _ in tipos_vagas_1855
                            },
                            "link": raw_link_1855,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            for key, label_text in tipos_vagas_1855:
                                num_input = ui.number(
                                    label=label_text,
                                    value=state_1855["vagas"][key],
                                    min=0,
                                    precision=0,
                                ).classes("w-full").props("outlined dense")
                                num_input.bind_value(state_1855["vagas"], key)

                        ui.label("Nota 18.5.5: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Quadro Demostrativo de Oferta de Vagas:",
                            value=raw_link_1855,
                            placeholder="Link do plano municipal de saúde, portarias de credenciamento ou relatório de oferta...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1855, "link"
                        )

                        def salvar_1855():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="18.5.5",
                                valor=state_1855["vagas"],
                                pontos=pts,
                                link=state_1855["link"],
                                comentarios=d1855.get("comentarios", []),
                                status=d1855.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.5.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.5.5", on_click=salvar_1855).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.5.5", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 18.6 (Adesão ao Programa De Volta para Casa - PVC)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("18.6 • Programa De Volta para Casa (PVC)").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "O município aderiu formalmente ao programa “De Volta para Casa” (PVC)?"
                        ).classes("text-base font-bold text-black mb-6")

                        d186 = res_data.get("18.6") or {}
                        raw_val_186 = str(d186.get("valor", "none"))
                        raw_link_186 = str(d186.get("link") or "")

                        state_186 = {
                            "opcao": raw_val_186 if raw_val_186 in ["sim", "nao"] else "none",
                            "link": raw_link_186,
                        }

                        opts_186 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_186 = ui.radio(
                            options=opts_186,
                            value=state_186["opcao"]
                        ).classes("mb-4")
                        rad_186.bind_value(state_186, "opcao")

                        lbl_pts_186 = ui.label("Nota 18.6: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_186():
                            pts = 0.0
                            lbl_pts_186.set_text("📊 Nota 18.6: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Termo de Adesão ao PVC ou Publicação Oficial:",
                            value=raw_link_186,
                            placeholder="Link do termo de adesão ao Programa De Volta para Casa ou cadastro no Ministério da Saúde...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_186, "link"
                        )

                        def salvar_186():
                            pts = recalc_186()
                            save_resposta(
                                ano=ano_sel,
                                qid="18.6",
                                valor=state_186["opcao"],
                                pontos=pts,
                                link=state_186["link"],
                                comentarios=d186.get("comentarios", []),
                                status=d186.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 18.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 18.6", on_click=salvar_186).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("18.6", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.0 (Demanda de Moradia / Serviços Residenciais Terapêuticos - SRT)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.0 • Demanda de Moradia e Longa Permanência").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "No município, há demanda de moradia para portadores de transtornos mentais crônicos com necessidade de cuidados de longa permanência, prioritariamente egressos de internações psiquiátricas e de hospitais de custódia, que não possuam suporte financeiro, social e/ou laços familiares que permitam outra forma de reinserção?"
                        ).classes("text-base font-bold text-black mb-6")

                        d190 = res_data.get("19.0") or {}
                        raw_val_190 = str(d190.get("valor", "none"))
                        raw_link_190 = str(d190.get("link") or "")

                        state_190 = {
                            "opcao": raw_val_190 if raw_val_190 in ["sim", "nao"] else "none",
                            "link": raw_link_190,
                        }

                        opts_190 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_190 = ui.radio(
                            options=opts_190,
                            value=state_190["opcao"]
                        ).classes("mb-4")
                        rad_190.bind_value(state_190, "opcao")

                        lbl_pts_190 = ui.label("Nota 19.0: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_190():
                            pts = 0.0
                            lbl_pts_190.set_text("📊 Nota 19.0: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Mapeamento de Demanda Desinstitucionalização (SRT):",
                            value=raw_link_190,
                            placeholder="Link do relatório da equipe de desinstitucionalização, lista de egressos ou plano de SRT...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_190, "link"
                        )

                        def salvar_190():
                            pts = recalc_190()
                            save_resposta(
                                ano=ano_sel,
                                qid="19.0",
                                valor=state_190["opcao"],
                                pontos=pts,
                                link=state_186["link"],
                                comentarios=d190.get("comentarios", []),
                                status=d190.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 19.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.0", on_click=salvar_190).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.0", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.1 (Adequação da Oferta e Distribuição Geográfica de SRT)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.1 • Adequação da Oferta de SRTs").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Quantidade de SRTs ofertadas é adequada, inclusive quanto à distribuição geográfica, para a demanda de moradia para portadores de transtornos mentais crônicos com necessidade de cuidados de longa permanência, prioritariamente egressos de internações psiquiátricas e de hospitais de custódia, que não possuam suporte financeiro, social e/ou laços familiares que permitam outra forma de reinserção?"
                        ).classes("text-base font-bold text-black mb-6")

                        d191 = res_data.get("19.1") or {}
                        raw_val_191 = str(d191.get("valor", "none"))
                        raw_link_191 = str(d191.get("link") or "")

                        state_191 = {
                            "opcao": raw_val_191 if raw_val_191 in ["sim", "nao"] else "none",
                            "link": raw_link_191,
                        }

                        opts_191 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        rad_191 = ui.radio(
                            options=opts_191,
                            value=state_191["opcao"]
                        ).classes("mb-4")
                        rad_191.bind_value(state_191, "opcao")

                        lbl_pts_191 = ui.label("Nota 19.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_191():
                            pts = 0.0
                            lbl_pts_191.set_text("📊 Nota 19.1: Informativo (0.0 pontos)")
                            return pts

                        ui.textarea(
                            label="Link de Evidência / Diagnóstico de Cobertura das SRTs:",
                            value=raw_link_191,
                            placeholder="Link do documento de mapeamento territorial, cobertura das residências terapêuticas...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_191, "link"
                        )

                        def salvar_191():
                            pts = recalc_191()
                            save_resposta(
                                ano=ano_sel,
                                qid="19.1",
                                valor=state_191["opcao"],
                                pontos=pts,
                                link=state_191["link"],
                                comentarios=d191.get("comentarios", []),
                                status=d191.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 19.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.1", on_click=salvar_191).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.2 (Quantidade de Unidades de SRT por Tipo)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.2 • Quantidade de Unidades de SRT").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de unidades de Serviços Residenciais Terapêuticos:"
                        ).classes("text-base font-bold text-black mb-4")

                        d192 = res_data.get("19.2") or {}
                        raw_val_192 = d192.get("valor") or {}
                        if not isinstance(raw_val_192, dict):
                            raw_val_192 = {}

                        raw_link_192 = str(d192.get("link") or "")

                        tipos_srt_192 = [
                            ("srt_tipo_1", "Para SRT tipo I"),
                            ("srt_tipo_2", "Para SRT tipo II"),
                            ("equivalente", "Equivalente"),
                        ]

                        state_192 = {
                            "qtdes": {
                                k: int(raw_val_192.get(k, 0)) for k, _ in tipos_srt_192
                            },
                            "link": raw_link_192,
                        }

                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            for key, label_text in tipos_srt_192:
                                num_input = ui.number(
                                    label=label_text,
                                    value=state_192["qtdes"][key],
                                    min=0,
                                    precision=0,
                                ).classes("w-full").props("outlined dense")
                                num_input.bind_value(state_192["qtdes"], key)

                        ui.label("Nota 19.2: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES ou Portarias das Unidades:",
                            value=raw_link_192,
                            placeholder="Link das fichas CNES ou portarias de habilitação das SRTs...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_192, "link"
                        )

                        def salvar_192():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="19.2",
                                valor=state_192["qtdes"],
                                pontos=pts,
                                link=state_192["link"],
                                comentarios=d192.get("comentarios", []),
                                status=d192.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 19.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.2", on_click=salvar_192).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.2", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.3 (Regulação de Vagas das SRTs)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.3 • Regulação de Vagas em SRT").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "As vagas dos Serviços Residenciais Terapêuticos ou equivalente para os residentes do município estão cadastradas no sistema de informação de regulação?"
                        ).classes("text-base font-bold text-black mb-2")
                        ui.label(
                            "*(Pode estar cadastrado no sistema de regulação municipal e/ou estadual)*"
                        ).classes("text-sm italic text-gray-600 mb-6")

                        d193 = res_data.get("19.3") or {}
                        raw_val_193 = str(d193.get("valor", "none"))
                        raw_link_193 = str(d193.get("link") or "")

                        state_193 = {
                            "opcao": raw_val_193 if raw_val_193 in ["sim", "nao"] else "none",
                            "link": raw_link_193,
                        }

                        opts_193 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-10,0 pontos)",
                        }

                        rad_193 = ui.radio(
                            options=opts_193,
                            value=state_193["opcao"]
                        ).classes("mb-4")
                        rad_193.bind_value(state_193, "opcao")

                        lbl_pts_193 = ui.label("Nota 19.3: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_193():
                            val = state_193["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_193.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -10.0
                                lbl_pts_193.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_193.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_193.set_text(f"📊 Nota 19.3: {pts:.1f} pontos")
                            return pts

                        rad_193.on("update:model-value", recalc_193)
                        recalc_193()

                        ui.textarea(
                            label="Link de Evidência / Comprovante do Sistema de Regulação:",
                            value=raw_link_193,
                            placeholder="Link do sistema de regulação comprovando o cadastro das vagas de SRT...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_193, "link"
                        )

                        def salvar_193():
                            pts = recalc_193()
                            save_resposta(
                                ano=ano_sel,
                                qid="19.3",
                                valor=state_193["opcao"],
                                pontos=pts,
                                link=state_193["link"],
                                comentarios=d193.get("comentarios", []),
                                status=d193.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 19.3 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.3", on_click=salvar_193).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.3", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.3.1 (Quantidade de Vagas em SRT no Sistema de Regulação)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.3.1 • Quantidade de Vagas Reguladas em SRT").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe a quantidade de vagas cadastradas no sistema de regulação (municipal e/ou estadual):"
                        ).classes("text-base font-bold text-black mb-4")

                        d1931 = res_data.get("19.3.1") or {}
                        raw_val_1931 = d1931.get("valor") or {}
                        if not isinstance(raw_val_1931, dict):
                            raw_val_1931 = {}

                        raw_link_1931 = str(d1931.get("link") or "")

                        tipos_vagas_1931 = [
                            ("srt_tipo_1", "Para SRT tipo I"),
                            ("srt_tipo_2", "Para SRT tipo II"),
                            ("equivalente", "Equivalente"),
                        ]

                        state_1931 = {
                            "vagas": {
                                k: int(raw_val_1931.get(k, 0)) for k, _ in tipos_vagas_1931
                            },
                            "link": raw_link_1931,
                        }

                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            for key, label_text in tipos_vagas_1931:
                                num_input = ui.number(
                                    label=label_text,
                                    value=state_1931["vagas"][key],
                                    min=0,
                                    precision=0,
                                ).classes("w-full").props("outlined dense")
                                num_input.bind_value(state_1931["vagas"], key)

                        ui.label("Nota 19.3.1: Informativo (0.0 pontos)").classes(
                            "text-sm font-bold text-green-600 my-4"
                        )

                        ui.textarea(
                            label="Link de Evidência / Extrato de Vagas Reguladas:",
                            value=raw_link_1931,
                            placeholder="Link do relatório ou extrato da oferta de vagas do sistema de regulação...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_1931, "link"
                        )

                        def salvar_1931():
                            pts = 0.0
                            save_resposta(
                                ano=ano_sel,
                                qid="19.3.1",
                                valor=state_1931["vagas"],
                                pontos=pts,
                                link=state_1931["link"],
                                comentarios=d1931.get("comentarios", []),
                                status=d1931.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 19.3.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.3.1", on_click=salvar_1931).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.3.1", res_data, render_conteudo.refresh)

                    # =============================================================================
                    # QUESITO 19.4 (Rotinas de Acompanhamento, Supervisão e Controle das SRTs)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.4 • Gestão e Supervisão Qualitativa das SRTs").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "A Secretaria Municipal de Saúde (ou equivalente), com apoio técnico do Ministério da Saúde, tem rotinas estabelecidas de acompanhamento, supervisão, controle e avaliação para a garantia do funcionamento com qualidade dos Serviços Residenciais Terapêuticos em Saúde Mental?"
                        ).classes("text-base font-bold text-black mb-6")

                        d194 = res_data.get("19.4") or {}
                        raw_val_194 = str(d194.get("valor", "none"))
                        raw_link_194 = str(d194.get("link") or "")

                        state_194 = {
                            "opcao": raw_val_194 if raw_val_194 in ["sim", "nao"] else "none",
                            "link": raw_link_194,
                        }

                        opts_194 = {
                            "none": "Selecione uma opção...",
                            "sim": "Sim (0,0 ponto)",
                            "nao": "Não (-5,0 pontos)",
                        }

                        rad_194 = ui.radio(
                            options=opts_194,
                            value=state_194["opcao"]
                        ).classes("mb-4")
                        rad_194.bind_value(state_194, "opcao")

                        lbl_pts_194 = ui.label("Nota 19.4: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-4"
                        )

                        def recalc_194():
                            val = state_194["opcao"]
                            if val == "sim":
                                pts = 0.0
                                lbl_pts_194.classes(replace="text-sm font-bold text-green-600 mb-4")
                            elif val == "nao":
                                pts = -5.0
                                lbl_pts_194.classes(replace="text-sm font-bold text-red-600 mb-4")
                            else:
                                pts = 0.0
                                lbl_pts_194.classes(replace="text-sm font-bold text-gray-600 mb-4")

                            lbl_pts_194.set_text(f"📊 Nota 19.4: {pts:.1f} pontos")
                            return pts

                        rad_194.on("update:model-value", recalc_194)
                        recalc_194()

                        ui.textarea(
                            label="Link de Evidência / Relatórios de Monitoramento ou Protocolos de Supervisão:",
                            value=raw_link_194,
                            placeholder="Link do protocolo de supervisão técnica, relatórios periódicos de visita técnica ou reuniões de alinhamento...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_194, "link"
                        )

                        def salvar_194():
                            pts = recalc_194()
                            save_resposta(
                                ano=ano_sel,
                                qid="19.4",
                                valor=state_194["opcao"],
                                pontos=pts,
                                link=state_194["link"],
                                comentarios=d194.get("comentarios", []),
                                status=d194.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 19.4 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.4", on_click=salvar_194).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.4", res_data, render_conteudo.refresh)

    # =============================================================================
                    # QUESITO 19.5 (Acompanhamento do Processo de Desinstitucionalização)
                    # =============================================================================
                    with ui.card().classes(
                        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
                    ):
                        ui.label("19.5 • Evolução dos Leitos e Vagas de SRT").classes(
                            "text-xl font-semibold text-blue-500 mb-3"
                        )
                        ui.label(
                            "Informe os dados de leitos de internação psiquiátrica prolongada e vagas de SRTs (Ano anterior x Ano atual):"
                        ).classes("text-base font-bold text-black mb-4")

                        d195 = res_data.get("19.5") or {}
                        raw_val_195 = d195.get("valor") or {}
                        if not isinstance(raw_val_195, dict):
                            raw_val_195 = {}

                        raw_link_195 = str(d195.get("link") or "")

                        state_195 = {
                            "la_minus_1": int(raw_val_195.get("la_minus_1", 0)), # Leitos 2024
                            "la": int(raw_val_195.get("la", 0)),                 # Leitos 2025
                            "va_minus_1": int(raw_val_195.get("va_minus_1", 0)), # Vagas SRT 2024
                            "va": int(raw_val_195.get("va", 0)),                 # Vagas SRT 2025
                            "link": raw_link_195,
                        }

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            inp_la_m1 = ui.number(
                                label="Nº de Leitos de Internação Psiquiátrica Prolongada (2024 - LA-1)",
                                value=state_195["la_minus_1"],
                                min=0,
                                precision=0,
                            ).classes("w-full").props("outlined dense")
                            inp_la_m1.bind_value(state_195, "la_minus_1")

                            inp_la = ui.number(
                                label="Nº de Leitos de Internação Psiquiátrica Prolongada (2025 - LA)",
                                value=state_195["la"],
                                min=0,
                                precision=0,
                            ).classes("w-full").props("outlined dense")
                            inp_la.bind_value(state_195, "la")

                            inp_va_m1 = ui.number(
                                label="Nº de Vagas Disponibilizadas em SRT (2024 - VA-1)",
                                value=state_195["va_minus_1"],
                                min=0,
                                precision=0,
                            ).classes("w-full").props("outlined dense")
                            inp_va_m1.bind_value(state_195, "va_minus_1")

                            inp_va = ui.number(
                                label="Nº de Vagas Disponibilizadas em SRT (2025 - VA)",
                                value=state_195["va"],
                                min=0,
                                precision=0,
                            ).classes("w-full").props("outlined dense")
                            inp_va.bind_value(state_195, "va")

                        lbl_pts_195 = ui.label("Nota 19.5: 0.0 pontos").classes(
                            "text-sm font-bold text-green-600 mb-2"
                        )
                        lbl_motivo_195 = ui.label("").classes("text-xs text-gray-600 mb-4")

                        def recalc_195():
                            # Busca a quantidade total de SRTs preenchidas no quesito 19.2
                            d192 = res_data.get("19.2") or {}
                            val_192 = d192.get("valor") or {}
                            soma_srt_192 = 0
                            if isinstance(val_192, dict):
                                soma_srt_192 = sum(int(v) for v in val_192.values() if str(v).isdigit())

                            la_m1 = int(state_195["la_minus_1"])
                            la = int(state_195["la"])
                            va_m1 = int(state_195["va_minus_1"])
                            va = int(state_195["va"])

                            # Regras de Penalidade (-15,0 pontos):
                            # 1. Sem SRTs (soma_srt_192 == 0 ou va == 0)
                            # 2. Aumento de leitos (la > la_m1)
                            # 3. Diminuição de vagas (va < va_m1)
                            # 4. Redução de leitos maior do que a criação de novas vagas SRT: (la_m1 - la) > (va - va_m1)
                            
                            motivos = []
                            if soma_srt_192 == 0 or va == 0:
                                motivos.append("Sem SRTs cadastradas ou sem vagas ativas no ano corrente")
                            if la > la_m1:
                                motivos.append("Houve aumento no número de leitos de internação psiquiátrica prolongada")
                            if va < va_m1:
                                motivos.append("Houve redução no número de vagas em SRT")
                            if (la_m1 - la) > (va - va_m1):
                                motivos.append("A redução de leitos psiquiátricos foi superior ao aumento de vagas SRT")

                            if motivos:
                                pts = -15.0
                                lbl_pts_195.classes(replace="text-sm font-bold text-red-600 mb-2")
                                lbl_motivo_195.set_text("⚠️ Justificativa da Penalidade: " + " | ".join(motivos))
                            else:
                                pts = 0.0
                                lbl_pts_195.classes(replace="text-sm font-bold text-green-600 mb-2")
                                lbl_motivo_195.set_text("✅ Processo de substituição de leitos por SRTs adequado.")

                            lbl_pts_195.set_text(f"📊 Nota 19.5: {pts:.1f} pontos")
                            return pts

                        inp_la_m1.on("update:model-value", recalc_195)
                        inp_la.on("update:model-value", recalc_195)
                        inp_va_m1.on("update:model-value", recalc_195)
                        inp_va.on("update:model-value", recalc_195)
                        recalc_195()

                        ui.textarea(
                            label="Link de Evidência / Cadastro CNES ou Relatórios de Leitos/SRTs:",
                            value=raw_link_195,
                            placeholder="Link do CNES comprovando o quantitativo de leitos psiquiátricos e vagas de SRTs...",
                        ).classes("w-full mb-4").props("outlined rows=2").bind_value(
                            state_195, "link"
                        )

                        def salvar_195():
                            pts = recalc_195()
                            save_resposta(
                                ano=ano_sel,
                                qid="19.5",
                                valor=state_195,
                                pontos=pts,
                                link=state_195["link"],
                                comentarios=d195.get("comentarios", []),
                                status=d195.get("status", "Pendente"),
                            )
                            ui.notify(f"Quesito 19.5 salvo com sucesso! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 19.5", on_click=salvar_195).classes(
                            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
                        )
                        ui.separator().classes("my-2")
                        bloco_comentarios("19.5", res_data, render_conteudo.refresh)

    # =============================================================================
                    # MÓDULO DE VIGILÂNCIA EM SAÚDE - QUESITOS 20.0 A 20.3
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 20.0 (Tipos de Insumos Geridos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("20.0 • Insumos sob Gestão da Vigilância em Saúde").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Sobre Vigilância em Saúde, a Prefeitura realiza gestão de quais tipos de insumos?").classes("text-sm text-gray-700 mb-4")

                        d200 = res_data.get("20.0") or {}
                        raw_val_200 = d200.get("valor") or {}
                        if not isinstance(raw_val_200, dict):
                            raw_val_200 = {}

                        state_200 = {
                            "imunobiologicos": bool(raw_val_200.get("imunobiologicos", False)),
                            "diagnostico": bool(raw_val_200.get("diagnostico", False)),
                            "vetores": bool(raw_val_200.get("vetores", False)),
                            "link": str(d200.get("link") or ""),
                        }

                        chk_imuno = ui.checkbox("Imunobiológicos (soros, vacinas e imunoglobulinas)", value=state_200["imunobiologicos"])
                        chk_imuno.bind_value(state_200, "imunobiologicos")

                        chk_diag = ui.checkbox("Meios de diagnóstico laboratorial para as doenças sob monitoramento epidemiológico (sangue, fluidos orgânicos, etc.)", value=state_200["diagnostico"])
                        chk_diag.bind_value(state_200, "diagnostico")

                        chk_vetores = ui.checkbox("Controle de vetores (inseticidas, larvicidas)", value=state_200["vetores"])
                        chk_vetores.bind_value(state_200, "vetores")

                        ui.textarea(label="Link / Evidências:", value=state_200["link"]).classes("w-full my-3").props("outlined dense rows=2").bind_value(state_200, "link")

                        def salvar_200():
                            save_resposta(
                                ano=ano_sel,
                                qid="20.0",
                                valor=state_200,
                                pontos=0.0,  # Informativo
                                link=state_200["link"],
                                comentarios=d200.get("comentarios", []),
                                status=d200.get("status", "Pendente")
                            )
                            ui.notify("Quesito 20.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 20.0", on_click=salvar_200).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("20.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 20.1 (Uso de Frigobar para Imunobiológicos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("20.1 • Armazenamento e Refrigeração de Imunobiológicos").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("A Prefeitura utiliza frigobar para refrigeração, manutenção, monitoramento e controle da temperatura dos imunobiológicos (soros, vacinas e imunoglobulinas)?").classes("text-sm text-gray-700 mb-2")
                        ui.label("Obs: Frigobar é um refrigerador com dimensões reduzidas, projetado para conservação doméstica/alimentos.").classes("text-xs text-gray-500 italic mb-4")

                        d201 = res_data.get("20.1") or {}
                        opts_201 = {
                            "nao": "Não",
                            "menor_parte": "Sim, na menor parte dos estabelecimentos de saúde sob gestão municipal",
                            "maior_parte": "Sim, na maior parte dos estabelecimentos de saúde sob gestão municipal",
                            "todos": "Sim, em todos os estabelecimentos de saúde sob gestão municipal",
                        }
                        
                        val_201_init = d201.get("valor") if d201.get("valor") in opts_201 else "nao"
                        link_201_init = str(d201.get("link") or "")

                        state_201 = {"opcao": val_201_init, "link": link_201_init}

                        rad_201 = ui.radio(opts_201, value=state_201["opcao"]).classes("mb-3")
                        rad_201.bind_value(state_201, "opcao")

                        lbl_pts_201 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_201():
                            op = state_201["opcao"]
                            mapa_pts = {
                                "nao": 0.0,
                                "menor_parte": -1.0,
                                "maior_parte": -3.0,
                                "todos": -5.0
                            }
                            pts = mapa_pts.get(op, 0.0)
                            cor = "text-green-600" if pts == 0.0 else "text-red-600"
                            lbl_pts_201.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_201.set_text(f"📊 Pontuação Quesito 20.1: {pts:.1f} pontos")
                            return pts

                        rad_201.on("update:model-value", recalc_201)
                        recalc_201()

                        ui.textarea(label="Link / Comprovação da Rede de Frio:", value=state_201["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_201, "link")

                        def salvar_201():
                            pts = recalc_201()
                            save_resposta(
                                ano=ano_sel,
                                qid="20.1",
                                valor=state_201["opcao"],
                                pontos=pts,
                                link=state_201["link"],
                                comentarios=d201.get("comentarios", []),
                                status=d201.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 20.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 20.1", on_click=salvar_201).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("20.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 20.2 (Materiais para Coleta de Diagnóstico Laboratorial)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("20.2 • Materiais de Coleta para Diagnóstico Epidemiológico").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("A Prefeitura disponibilizou os materiais necessários para a coleta dos meios de diagnóstico laboratorial para as doenças sob monitoramento epidemiológico (sangue, saliva, secreção, suor, urina, fezes)?").classes("text-sm text-gray-700 mb-4")

                        d202 = res_data.get("20.2") or {}
                        opts_202 = {
                            "todas": "Sim, para todas as amostras",
                            "maior_parte": "Sim, para a maior parte das amostras",
                            "menor_parte": "Sim, para a menor parte das amostras",
                            "nao": "Não",
                        }

                        val_202_init = d202.get("valor") if d202.get("valor") in opts_202 else "todas"
                        link_202_init = str(d202.get("link") or "")

                        state_202 = {"opcao": val_202_init, "link": link_202_init}

                        rad_202 = ui.radio(opts_202, value=state_202["opcao"]).classes("mb-3")
                        rad_202.bind_value(state_202, "opcao")

                        lbl_pts_202 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_202():
                            op = state_202["opcao"]
                            mapa_pts = {
                                "todas": 0.0,
                                "maior_parte": -1.0,
                                "menor_parte": -3.0,
                                "nao": -5.0
                            }
                            pts = mapa_pts.get(op, 0.0)
                            cor = "text-green-600" if pts == 0.0 else "text-red-600"
                            lbl_pts_202.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_202.set_text(f"📊 Pontuação Quesito 20.2: {pts:.1f} pontos")
                            return pts

                        rad_202.on("update:model-value", recalc_202)
                        recalc_202()

                        ui.textarea(label="Link / Documento comprobatório de suprimentos/insumos:", value=state_202["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_202, "link")

                        def salvar_202():
                            pts = recalc_202()
                            save_resposta(
                                ano=ano_sel,
                                qid="20.2",
                                valor=state_202["opcao"],
                                pontos=pts,
                                link=state_202["link"],
                                comentarios=d202.get("comentarios", []),
                                status=d202.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 20.2 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 20.2", on_click=salvar_202).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("20.2", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 20.3 (EPIs para Controle de Vetores)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("20.3 • Equipamentos de Proteção Individual (EPIs) - Controle de Vetores").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("A Prefeitura disponibilizou todos os equipamentos de proteção individual (EPIs) para o manuseio dos insumos para controle de vetores (inseticidas e pesticidas)?").classes("text-sm text-gray-700 mb-4")

                        d203 = res_data.get("20.3") or {}
                        opts_203 = {
                            "todos": "Sim, para todos os profissionais",
                            "maior_parte": "Sim, para a maior parte dos profissionais",
                            "menor_parte": "Sim, para a menor parte dos profissionais",
                            "nao": "Não",
                        }

                        val_203_init = d203.get("valor") if d203.get("valor") in opts_203 else "todos"
                        link_203_init = str(d203.get("link") or "")

                        state_203 = {"opcao": val_203_init, "link": link_203_init}

                        rad_203 = ui.radio(opts_203, value=state_203["opcao"]).classes("mb-3")
                        rad_203.bind_value(state_203, "opcao")

                        lbl_pts_203 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_203():
                            op = state_203["opcao"]
                            mapa_pts = {
                                "todos": 0.0,
                                "maior_parte": -1.0,
                                "menor_parte": -3.0,
                                "nao": -5.0
                            }
                            pts = mapa_pts.get(op, 0.0)
                            cor = "text-green-600" if pts == 0.0 else "text-red-600"
                            lbl_pts_203.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_203.set_text(f"📊 Pontuação Quesito 20.3: {pts:.1f} pontos")
                            return pts

                        rad_203.on("update:model-value", recalc_203)
                        recalc_203()

                        ui.textarea(label="Link / Relatório de entrega de EPIs (Ficha de EPI/Ordem de Fornecimento):", value=state_203["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_203, "link")

                        def salvar_203():
                            pts = recalc_203()
                            save_resposta(
                                ano=ano_sel,
                                qid="20.3",
                                valor=state_203["opcao"],
                                pontos=pts,
                                link=state_203["link"],
                                comentarios=d203.get("comentarios", []),
                                status=d203.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 20.3 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 20.3", on_click=salvar_203).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("20.3", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE VIGILÂNCIA DE ARBOVIROSES - QUESITOS 21.0 A 23.1
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 21.0 (Análise Semanal de Tendência de Arboviroses)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("21.0 • Análise Semanal de Arboviroses").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município analisa semanalmente os dados de casos de arboviroses, acompanhando a tendência dos casos e verificando as variações entre as semanas epidemiológicas?").classes("text-sm text-gray-700 mb-4")

                        d210 = res_data.get("21.0") or {}
                        opts_210 = {
                            "sim": "Sim (10,0 pontos)",
                            "nao": "Não (0,0 ponto)",
                            "sem_casos": "Não houve casos de arboviroses (10,0 pontos)",
                        }

                        val_210_init = d210.get("valor") if d210.get("valor") in opts_210 else "sim"
                        link_210_init = str(d210.get("link") or "")

                        state_210 = {"opcao": val_210_init, "link": link_210_init}

                        rad_210 = ui.radio(opts_210, value=state_210["opcao"]).classes("mb-3")
                        rad_210.bind_value(state_210, "opcao")

                        lbl_pts_210 = ui.label("Pontuação: 10.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_210():
                            op = state_210["opcao"]
                            pts = 10.0 if op in ["sim", "sem_casos"] else 0.0
                            cor = "text-green-600" if pts == 10.0 else "text-red-600"
                            lbl_pts_210.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_210.set_text(f"📊 Pontuação Quesito 21.0: {pts:.1f} pontos")
                            return pts

                        rad_210.on("update:model-value", recalc_210)
                        recalc_210()

                        ui.textarea(label="Link / Boletim Epidemiológico Semanal:", value=state_210["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_210, "link")

                        def salvar_210():
                            pts = recalc_210()
                            save_resposta(
                                ano=ano_sel,
                                qid="21.0",
                                valor=state_210["opcao"],
                                pontos=pts,
                                link=state_210["link"],
                                comentarios=d210.get("comentarios", []),
                                status=d210.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 21.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 21.0", on_click=salvar_210).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("21.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 22.0 (Investigação de Casos, Surtos e Óbitos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("22.0 • Investigação Epidemiológica de Arboviroses").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município investiga casos notificados, surtos e óbitos de arboviroses?").classes("text-sm text-gray-700 mb-4")

                        d220 = res_data.get("22.0") or {}
                        opts_220 = {
                            "todos": "Sim, investiga todos os casos (30,0 pontos)",
                            "parte": "Sim, investiga parte dos casos (15,0 pontos)",
                            "sem_casos": "Não houve casos em 2025 (30,0 pontos)",
                            "nao": "Não investiga (0,0 ponto)",
                        }

                        val_220_init = d220.get("valor") if d220.get("valor") in opts_220 else "todos"
                        link_220_init = str(d220.get("link") or "")

                        state_220 = {"opcao": val_220_init, "link": link_220_init}

                        rad_220 = ui.radio(opts_220, value=state_220["opcao"]).classes("mb-3")
                        rad_220.bind_value(state_220, "opcao")

                        lbl_pts_220 = ui.label("Pontuação: 30.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_220():
                            op = state_220["opcao"]
                            mapa_pts = {
                                "todos": 30.0,
                                "sem_casos": 30.0,
                                "parte": 15.0,
                                "nao": 0.0,
                            }
                            pts = mapa_pts.get(op, 0.0)
                            cor = "text-green-600" if pts > 0 else "text-red-600"
                            lbl_pts_220.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_220.set_text(f"📊 Pontuação Quesito 22.0: {pts:.1f} pontos")
                            return pts

                        rad_220.on("update:model-value", recalc_220)
                        recalc_220()

                        ui.textarea(label="Link / Relatório de Investigação do SINAN:", value=state_220["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_220, "link")

                        def salvar_220():
                            pts = recalc_220()
                            save_resposta(
                                ano=ano_sel,
                                qid="22.0",
                                valor=state_220["opcao"],
                                pontos=pts,
                                link=state_220["link"],
                                comentarios=d220.get("comentarios", []),
                                status=d220.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 22.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 22.0", on_click=salvar_220).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("22.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 23.0 (Atribuições de Vigilância Entomológica e Controle Vetorial)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("23.0 • Exercício do Controle Vetorial em 2025").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município exerceu as atribuições relacionadas à vigilância entomológica e controle vetorial em 2025?").classes("text-sm text-gray-700 mb-4")

                        d230 = res_data.get("23.0") or {}
                        opts_230 = {
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        val_230_init = d230.get("valor") if d230.get("valor") in opts_230 else "sim"
                        link_230_init = str(d230.get("link") or "")

                        state_230 = {"opcao": val_230_init, "link": link_230_init}

                        rad_230 = ui.radio(opts_230, value=state_230["opcao"]).classes("mb-3")
                        rad_230.bind_value(state_230, "opcao")

                        ui.label("Nota 23.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 mb-2")

                        ui.textarea(label="Link / Relatório de Ações de Controle Vetorial:", value=state_230["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_230, "link")

                        def salvar_230():
                            save_resposta(
                                ano=ano_sel,
                                qid="23.0",
                                valor=state_230["opcao"],
                                pontos=0.0,
                                link=state_230["link"],
                                comentarios=d230.get("comentarios", []),
                                status=d230.get("status", "Pendente")
                            )
                            ui.notify("Quesito 23.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 23.0", on_click=salvar_230).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("23.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 23.1 (Detalhamento das Atribuições Entomológicas)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("23.1 • Atribuições de Vigilância Entomológica Executadas").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Assinale as atribuições da vigilância entomológica e controle vetorial efetivamente realizadas pelo município:").classes("text-sm text-gray-700 mb-4")

                        d231 = res_data.get("23.1") or {}
                        raw_val_231 = d231.get("valor") or {}
                        if not isinstance(raw_val_231, dict):
                            raw_val_231 = {}

                        atribuicoes_231 = [
                            ("visan", "Incluir a vigilância sanitária municipal como suporte às ações de controle vetorial exigindo cumprimento da legislação sanitária (3,0 pts)"),
                            ("integracao_esf", "Integrar as equipes de saúde da família nas atividades de controle vetorial, unificando territórios de ACS e ACE (3,0 pts)"),
                            ("indicadores", "Realizar o levantamento de indicadores entomológicos (LIRAa/LIA, etc.) (3,0 pts)"),
                            ("controle_triplice", "Executar as ações de controle mecânico, químico e biológico do mosquito (3,0 pts)"),
                            ("envio_dados", "Enviar os dados entomológicos ao nível estadual, dentro dos prazos estabelecidos (3,0 pts)"),
                            ("estoque", "Gerenciar os estoques municipais de inseticidas e biolarvicidas (3,0 pts)"),
                            ("vestimentas", "Adquirir as vestimentas e equipamentos necessários à rotina de controle vetorial (3,0 pts)"),
                            ("epis", "Adquirir os equipamentos de EPI recomendados para a aplicação de inseticidas e biolarvicidas (3,0 pts)"),
                            ("colinesterase", "Coletar e enviar ao laboratório de referência amostras de sangue dos trabalhadores para dosagem de colinesterase (3,0 pts)"),
                            ("comite_intersetorial", "Possuir Comitê Gestor Intersetorial, sob coordenação da SMS, com interface para o problema da dengue (3,0 pts)"),
                            ("outros", "Outros (0,0 pts)"),
                        ]

                        state_231 = {
                            "itens": {k: bool(raw_val_231.get(k, False)) for k, _ in atribuicoes_231},
                            "link": str(d231.get("link") or ""),
                        }

                        chks_231 = {}
                        for k, label_text in atribuicoes_231:
                            chk = ui.checkbox(label_text, value=state_231["itens"][k]).classes("mb-1")
                            chk.bind_value(state_231["itens"], k)
                            chks_231[k] = chk

                        lbl_pts_231 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 my-3")

                        def recalc_231():
                            pts = 0.0
                            for k, _ in atribuicoes_231:
                                if k != "outros" and state_231["itens"][k]:
                                    pts += 3.0
                            
                            lbl_pts_231.set_text(f"📊 Pontuação Quesito 23.1: {pts:.1f} pontos")
                            return pts

                        for chk in chks_231.values():
                            chk.on("update:model-value", recalc_231)

                        recalc_231()

                        ui.textarea(label="Link / Atas do Comitê, LIRAa, Fichas de Exames ou Ordens de Compra:", value=state_231["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_231, "link")

                        def salvar_231():
                            pts = recalc_231()
                            save_resposta(
                                ano=ano_sel,
                                qid="23.1",
                                valor=state_231["itens"],
                                pontos=pts,
                                link=state_231["link"],
                                comentarios=d231.get("comentarios", []),
                                status=d231.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 23.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 23.1", on_click=salvar_231).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("23.1", res_data, render_conteudo.refresh)

# -----------------------------------------------------------------------------
                    # QUESITO 24.0 (Execução de Atividades de Educação em Saúde)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("24.0 • Educação em Saúde").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município executou atividades de Educação em Saúde?").classes("text-sm text-gray-700 mb-4")

                        d240 = res_data.get("24.0") or {}
                        opts_240 = {
                            "sim": "Sim",
                            "nao": "Não",
                        }

                        val_240_init = d240.get("valor") if d240.get("valor") in opts_240 else "sim"
                        link_240_init = str(d240.get("link") or "")

                        state_240 = {"opcao": val_240_init, "link": link_240_init}

                        rad_240 = ui.radio(opts_240, value=state_240["opcao"]).classes("mb-3")
                        rad_240.bind_value(state_240, "opcao")

                        ui.label("Nota 24.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 mb-2")

                        ui.textarea(label="Link / Relatório Geral de Ações Educativas:", value=state_240["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_240, "link")

                        def salvar_240():
                            save_resposta(
                                ano=ano_sel,
                                qid="24.0",
                                valor=state_240["opcao"],
                                pontos=0.0,
                                link=state_240["link"],
                                comentarios=d240.get("comentarios", []),
                                status=d240.get("status", "Pendente")
                            )
                            ui.notify("Quesito 24.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 24.0", on_click=salvar_240).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("24.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 24.1 (Campanhas de Educação em Saúde Realizadas em 2025)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("24.1 • Campanhas Realizadas em 2025").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Assinale as campanhas de educação em saúde realizadas no município em 2025:").classes("text-sm text-gray-700 mb-4")

                        d241 = res_data.get("24.1") or {}
                        raw_val_241 = d241.get("valor") or {}
                        if not isinstance(raw_val_241, dict):
                            raw_val_241 = {}

                        campanhas_241 = [
                            ("planejamento_familiar", "Planejamento familiar - concepção e contracepção (Prevenção à Gravidez) (0,5 pt)", 0.5),
                            ("pre_natal", "Pré-Natal (0,5 pt)", 0.5),
                            ("assistencia_parto", "Assistência ao parto, ao puerpério e ao neonato, incluindo aleitamento materno e doação de leite materno (0,5 pt)", 0.5),
                            ("ist", "Prevenção às IST - Infecção Sexualmente Transmissível (0,5 pt)", 0.5),
                            ("canceres", "Prevenção dos cânceres do colo do útero, de mama e da saúde do homem (0,5 pt)", 0.5),
                            ("vacinacao", "Vacinação (0,5 pt)", 0.5),
                            ("hipertensao", "Hipertensão (0,5 pt)", 0.5),
                            ("diabetes", "Diabetes (0,5 pt)", 0.5),
                            ("hanseniase", "Hanseníase (0,5 pt)", 0.5),
                            ("hepatite", "Hepatite (0,5 pt)", 0.5),
                            ("covid", "Coronavírus - COVID19 (0,5 pt)", 0.5),
                            ("tuberculose", "Tuberculose (0,5 pt)", 0.5),
                            ("chagas", "Doença de Chagas (0,5 pt)", 0.5),
                            ("arboviroses", "Dengue/Zika/Chikungunya/Febre Amarela/Malária (Arboviroses) (0,5 pt)", 0.5),
                            ("tabaco", "Tabaco (0,5 pt)", 0.5),
                            ("drogas", "Drogas e entorpecentes (0,5 pt)", 0.5),
                            ("saude_bucal", "Saúde Bucal (0,5 pt)", 0.5),
                            ("doacao_sangue", "Doação de Sangue (0,5 pt)", 0.5),
                            ("doacao_orgaos", "Doação de Órgãos (0,5 pt)", 0.5),
                            ("depressao_suicidio", "Prevenção à Depressão e ao Suicídio (0,5 pt)", 0.5),
                            ("hiv_aids", "HIV/Aids (0,0 pt)", 0.0),
                            ("falciforme", "Doença Falciforme (0,0 pt)", 0.0),
                            ("outros", "Outros (0,0 pt)", 0.0),
                        ]

                        state_241 = {
                            "itens": {k: bool(raw_val_241.get(k, False)) for k, _, _ in campanhas_241},
                            "link": str(d241.get("link") or ""),
                        }

                        chks_241 = {}
                        for k, label_text, _ in campanhas_241:
                            chk = ui.checkbox(label_text, value=state_241["itens"][k]).classes("mb-1")
                            chk.bind_value(state_241["itens"], k)
                            chks_241[k] = chk

                        lbl_pts_241 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 my-3")

                        def recalc_241():
                            pts = sum(peso for k, _, peso in campanhas_241 if state_241["itens"][k])
                            lbl_pts_241.set_text(f"📊 Pontuação Quesito 24.1: {pts:.1f} pontos")
                            return pts

                        for chk in chks_241.values():
                            chk.on("update:model-value", recalc_241)

                        recalc_241()

                        ui.textarea(label="Link / Fotos, Folderes, Registros de Campanhas e Mídias Sociais:", value=state_241["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_241, "link")

                        def salvar_241():
                            pts = recalc_241()
                            save_resposta(
                                ano=ano_sel,
                                qid="24.1",
                                valor=state_241["itens"],
                                pontos=pts,
                                link=state_241["link"],
                                comentarios=d241.get("comentarios", []),
                                status=d241.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 24.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 24.1", on_click=salvar_241).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("24.1", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE REGULAÇÃO EM SAÚDE E FILA DE ESPERA - QUESITOS 25.0 A 28.0
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 25.0 (Ações Reguladoras e Complexo Regulador)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("25.0 • Ações Reguladoras e Complexo Regulador").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município desenvolve ações reguladoras em seu território, operacionalizando por meio de complexo regulador municipal e/ou participando em co-gestão da operacionalização dos Complexos Reguladores Regionais?").classes("text-sm text-gray-700 mb-4")

                        d250 = res_data.get("25.0") or {}
                        opts_250 = {
                            "sim": "Sim (5,0 pontos)",
                            "nao": "Não (0,0 ponto)",
                        }

                        val_250_init = d250.get("valor") if d250.get("valor") in opts_250 else "sim"
                        link_250_init = str(d250.get("link") or "")

                        state_250 = {"opcao": val_250_init, "link": link_250_init}

                        rad_250 = ui.radio(opts_250, value=state_250["opcao"]).classes("mb-3")
                        rad_250.bind_value(state_250, "opcao")

                        lbl_pts_250 = ui.label("Pontuação: 5.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_250():
                            op = state_250["opcao"]
                            pts = 5.0 if op == "sim" else 0.0
                            cor = "text-green-600" if pts == 5.0 else "text-red-600"
                            lbl_pts_250.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_250.set_text(f"📊 Pontuação Quesito 25.0: {pts:.1f} pontos")
                            return pts

                        rad_250.on("update:model-value", recalc_250)
                        recalc_250()

                        ui.textarea(label="Link / Portaria ou Documento de Pactuação do Complexo Regulador:", value=state_250["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_250, "link")

                        def salvar_250():
                            pts = recalc_250()
                            save_resposta(
                                ano=ano_sel,
                                qid="25.0",
                                valor=state_250["opcao"],
                                pontos=pts,
                                link=state_250["link"],
                                comentarios=d250.get("comentarios", []),
                                status=d250.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 25.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 25.0", on_click=salvar_250).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("25.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 26.0 (Protocolos de Regulação de Acesso Formalizados)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("26.0 • Protocolos de Regulação de Acesso").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município elaborou os protocolos de regulação de acesso formalizados?").classes("text-sm text-gray-700 mb-4")

                        d260 = res_data.get("26.0") or {}
                        opts_260 = {
                            "sim": "Sim (10,0 pontos)",
                            "nao": "Não (0,0 ponto)",
                        }

                        val_260_init = d260.get("valor") if d260.get("valor") in opts_260 else "sim"
                        link_260_init = str(d260.get("link") or "")

                        state_260 = {"opcao": val_260_init, "link": link_260_init}

                        rad_260 = ui.radio(opts_260, value=state_260["opcao"]).classes("mb-3")
                        rad_260.bind_value(state_260, "opcao")

                        lbl_pts_260 = ui.label("Pontuação: 10.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_260():
                            op = state_260["opcao"]
                            pts = 10.0 if op == "sim" else 0.0
                            cor = "text-green-600" if pts == 10.0 else "text-red-600"
                            lbl_pts_260.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_260.set_text(f"📊 Pontuação Quesito 26.0: {pts:.1f} pontos")
                            return pts

                        rad_260.on("update:model-value", recalc_260)
                        recalc_260()

                        ui.textarea(label="Link / Publicação dos Protocolos de Regulação em Diário Oficial:", value=state_260["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_260, "link")

                        def salvar_260():
                            pts = recalc_260()
                            save_resposta(
                                ano=ano_sel,
                                qid="26.0",
                                valor=state_260["opcao"],
                                pontos=pts,
                                link=state_260["link"],
                                comentarios=d260.get("comentarios", []),
                                status=d260.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 26.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 26.0", on_click=salvar_260).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("26.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 27.0 (Regulação da Referência Intermunicipal)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("27.0 • Regulação da Referência Intermunicipal").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município regula a referência a ser realizada em outros municípios, de acordo com a programação pactuada e integrada, integrando-se aos fluxos regionais estabelecidos?").classes("text-sm text-gray-700 mb-4")

                        d270 = res_data.get("27.0") or {}
                        opts_270 = {
                            "sim": "Sim (5,0 pontos)",
                            "nao": "Não (0,0 ponto)",
                        }

                        val_270_init = d270.get("valor") if d270.get("valor") in opts_270 else "sim"
                        link_270_init = str(d270.get("link") or "")

                        state_270 = {"opcao": val_270_init, "link": link_270_init}

                        rad_270 = ui.radio(opts_270, value=state_270["opcao"]).classes("mb-3")
                        rad_270.bind_value(state_270, "opcao")

                        lbl_pts_270 = ui.label("Pontuação: 5.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_270():
                            op = state_270["opcao"]
                            pts = 5.0 if op == "sim" else 0.0
                            cor = "text-green-600" if pts == 5.0 else "text-red-600"
                            lbl_pts_270.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_270.set_text(f"📊 Pontuação Quesito 27.0: {pts:.1f} pontos")
                            return pts

                        rad_270.on("update:model-value", recalc_270)
                        recalc_270()

                        ui.textarea(label="Link / Documentação da PPI ou Termos de Pactuação Regional:", value=state_270["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_270, "link")

                        def salvar_270():
                            pts = recalc_270()
                            save_resposta(
                                ano=ano_sel,
                                qid="27.0",
                                valor=state_270["opcao"],
                                pontos=pts,
                                link=state_270["link"],
                                comentarios=d270.get("comentarios", []),
                                status=d270.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 27.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 27.0", on_click=salvar_270).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("27.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.0 (Controle de Fila de Espera na Atenção Especializada)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.0 • Controle da Fila de Espera na Atenção Especializada").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município possui controle da fila de espera para os atendimentos da Atenção Especializada que não foram inseridos no sistema de regulação do governo estadual (Portal CROSS)?").classes("text-sm text-gray-700 mb-2")
                        ui.label("Refere-se ao Município como Unidade Solicitante.").classes("text-xs text-gray-500 italic mb-4")

                        d280 = res_data.get("28.0") or {}
                        opts_280 = {
                            "todos": "Sim, com a relação nominal de pacientes e tempo de espera para todos os serviços da Atenção Especializada com fila de espera (5,0 pontos)",
                            "maior_parte": "Sim, com a relação nominal de pacientes e tempo de espera para a maior parte dos serviços da Atenção Especializada com fila de espera (2,0 pontos)",
                            "menor_parte": "Sim, com a relação nominal de pacientes e tempo de espera para a menor parte dos serviços da Atenção Especializada com fila de espera (1,0 ponto)",
                            "nao": "Não possui controle da fila de espera (0,0 ponto)",
                            "somente_cross": "Não possui fila de espera além da inserida no sistema de regulação do governo estadual (Portal CROSS) (5,0 pontos)",
                        }

                        val_280_init = d280.get("valor") if d280.get("valor") in opts_280 else "todos"
                        link_280_init = str(d280.get("link") or "")

                        state_280 = {"opcao": val_280_init, "link": link_280_init}

                        rad_280 = ui.radio(opts_280, value=state_280["opcao"]).classes("mb-3")
                        rad_280.bind_value(state_280, "opcao")

                        lbl_pts_280 = ui.label("Pontuação: 5.0").classes("text-sm font-bold text-green-600 mb-2")

                        def recalc_280():
                            op = state_280["opcao"]
                            mapa_pts = {
                                "todos": 5.0,
                                "somente_cross": 5.0,
                                "maior_parte": 2.0,
                                "menor_parte": 1.0,
                                "nao": 0.0,
                            }
                            pts = mapa_pts.get(op, 0.0)
                            cor = "text-green-600" if pts > 0 else "text-red-600"
                            lbl_pts_280.classes(replace=f"text-sm font-bold {cor} mb-2")
                            lbl_pts_280.set_text(f"📊 Pontuação Quesito 28.0: {pts:.1f} pontos")
                            return pts

                        rad_280.on("update:model-value", recalc_280)
                        recalc_280()

                        ui.textarea(label="Link / Relatório do Sistema Próprio de Fila de Espera ou Extrato CROSS:", value=state_280["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_280, "link")

                        def salvar_280():
                            pts = recalc_280()
                            save_resposta(
                                ano=ano_sel,
                                qid="28.0",
                                valor=state_280["opcao"],
                                pontos=pts,
                                link=state_280["link"],
                                comentarios=d280.get("comentarios", []),
                                status=d280.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 28.0 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.0", on_click=salvar_280).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.0", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE DETALHAMENTO DAS FILAS DE ESPERA - QUESITOS 28.1 A 28.2.2
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.1 (Tipo de Controle da Lista de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.1 • Tipo de Controle da Lista de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Assinale o tipo de controle da lista de espera para os atendimentos da Atenção Especializada que não foram inseridos no sistema de regulação do governo estadual:").classes("text-sm text-gray-700 mb-2")
                        ui.label("Obs: Planilha eletrônica (Excel, Calc, etc.) não é considerada sistema informatizado.").classes("text-xs text-amber-700 font-medium mb-4")

                        d281 = res_data.get("28.1") or {}
                        raw_val_281 = d281.get("valor") or {}
                        if not isinstance(raw_val_281, dict):
                            raw_val_281 = {}

                        state_281 = {
                            "informatizado": bool(raw_val_281.get("informatizado", False)),
                            "manual": bool(raw_val_281.get("manual", False)),
                            "link": str(d281.get("link") or ""),
                        }

                        chk_inf = ui.checkbox("Em sistema informatizado (5,0 pontos)", value=state_281["informatizado"])
                        chk_inf.bind_value(state_281, "informatizado")

                        chk_man = ui.checkbox("De forma manual (-5,0 pontos)", value=state_281["manual"])
                        chk_man.bind_value(state_281, "manual")

                        lbl_pts_281 = ui.label("Pontuação: 0.0").classes("text-sm font-bold text-green-600 my-2")

                        def recalc_281():
                            pts = 0.0
                            if state_281["informatizado"]:
                                pts += 5.0
                            if state_281["manual"]:
                                pts -= 5.0
                            cor = "text-green-600" if pts >= 0 else "text-red-600"
                            lbl_pts_281.classes(replace=f"text-sm font-bold {cor} my-2")
                            lbl_pts_281.set_text(f"📊 Pontuação Quesito 28.1: {pts:.1f} pontos")
                            return pts

                        chk_inf.on("update:model-value", recalc_281)
                        chk_man.on("update:model-value", recalc_281)
                        recalc_281()

                        ui.textarea(label="Link / Telas do Sistema Informatizado ou Registros Manuais:", value=state_281["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_281, "link")

                        def salvar_281():
                            pts = recalc_281()
                            save_resposta(
                                ano=ano_sel,
                                qid="28.1",
                                valor=state_281,
                                pontos=pts,
                                link=state_281["link"],
                                comentarios=d281.get("comentarios", []),
                                status=d281.get("status", "Pendente")
                            )
                            ui.notify(f"Quesito 28.1 salvo! ({pts:.1f} pts)", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.1", on_click=salvar_281).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2 (Serviços com Lista de Espera Fora do Portal CROSS)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2 • Serviços Especializados com Lista de Espera Própria").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Assinale os serviços da Atenção Especializada com lista de espera que não foram inseridos no sistema de regulação do governo estadual (Portal CROSS):").classes("text-sm text-gray-700 mb-4")

                        d282 = res_data.get("28.2") or {}
                        raw_val_282 = d282.get("valor") or {}
                        if not isinstance(raw_val_282, dict):
                            raw_val_282 = {}

                        servicos_282 = [
                            ("consultas", "Consultas por especialidade"),
                            ("exames", "Exames"),
                            ("terapias", "Terapias / tratamentos"),
                            ("medicamentos", "Medicamentos"),
                            ("opm", "OPM (Órteses, Próteses e Materiais Especiais)"),
                            ("cirurgias", "Cirurgias eletivas"),
                            ("outros", "Outros"),
                        ]

                        state_282 = {
                            "itens": {k: bool(raw_val_282.get(k, False)) for k, _ in servicos_282},
                            "link": str(d282.get("link") or ""),
                        }

                        for k, label_text in servicos_282:
                            chk = ui.checkbox(label_text, value=state_282["itens"][k]).classes("mb-1")
                            chk.bind_value(state_282["itens"], k)

                        ui.label("Nota 28.2: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relação de Serviços com Fila Própria:", value=state_282["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_282, "link")

                        def salvar_282():
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2",
                                valor=state_282["itens"],
                                pontos=0.0,
                                link=state_282["link"],
                                comentarios=d282.get("comentarios", []),
                                status=d282.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2", on_click=salvar_282).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2", res_data, render_conteudo.refresh)

                    # Helper para conversão segura de número sem quebrar com string vazia
                    def parse_num(val):
                        if val is None or val == "":
                            return None
                        try:
                            return int(val)
                        except (ValueError, TypeError):
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return None

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.1 (Consultas Médicas com Maior Tempo de Espera) - CORRIGIDO
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.1 • Consultas Médicas com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe as 3 consultas médicas com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2821 = res_data.get("28.2.1") or {}
                        raw_val_2821 = d2821.get("valor") or {}
                        if not isinstance(raw_val_2821, dict):
                            raw_val_2821 = {}

                        state_2821 = {
                            "esp_1": str(raw_val_2821.get("esp_1") or ""),
                            "dias_1": parse_num(raw_val_2821.get("dias_1")),
                            "esp_2": str(raw_val_2821.get("esp_2") or ""),
                            "dias_2": parse_num(raw_val_2821.get("dias_2")),
                            "esp_3": str(raw_val_2821.get("esp_3") or ""),
                            "dias_3": parse_num(raw_val_2821.get("dias_3")),
                            "link": str(d2821.get("link") or ""),
                        }

                        ui.label("1ª Consulta Médica:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da especialidade médica", value=state_2821["esp_1"]).classes("w-2/3").bind_value(state_2821, "esp_1")
                            ui.number("Tempo médio (dias)", value=state_2821["dias_1"]).classes("w-1/3").bind_value(state_2821, "dias_1")

                        ui.label("2ª Consulta Médica:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da especialidade médica", value=state_2821["esp_2"]).classes("w-2/3").bind_value(state_2821, "esp_2")
                            ui.number("Tempo médio (dias)", value=state_2821["dias_2"]).classes("w-1/3").bind_value(state_2821, "dias_2")

                        ui.label("3ª Consulta Médica:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da especialidade médica", value=state_2821["esp_3"]).classes("w-2/3").bind_value(state_2821, "esp_3")
                            ui.number("Tempo médio (dias)", value=state_2821["dias_3"]).classes("w-1/3").bind_value(state_2821, "dias_3")

                        ui.label("Nota 28.2.1: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial de Tempos de Espera (Consultas):", value=state_2821["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2821, "link")

                        def salvar_2821():
                            payload_2821 = {
                                "esp_1": state_2821["esp_1"],
                                "dias_1": parse_num(state_2821["dias_1"]),
                                "esp_2": state_2821["esp_2"],
                                "dias_2": parse_num(state_2821["dias_2"]),
                                "esp_3": state_2821["esp_3"],
                                "dias_3": parse_num(state_2821["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.1",
                                valor=payload_2821,
                                pontos=0.0,
                                link=state_2821["link"],
                                comentarios=d2821.get("comentarios", []),
                                status=d2821.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.1", on_click=salvar_2821).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.2 (Exames Médicos com Maior Tempo de Espera) - CORRIGIDO
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.2 • Exames Médicos com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe os 3 exames médicos com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2822 = res_data.get("28.2.2") or {}
                        raw_val_2822 = d2822.get("valor") or {}
                        if not isinstance(raw_val_2822, dict):
                            raw_val_2822 = {}

                        state_2822 = {
                            "exame_1": str(raw_val_2822.get("exame_1") or ""),
                            "dias_1": parse_num(raw_val_2822.get("dias_1")),
                            "exame_2": str(raw_val_2822.get("exame_2") or ""),
                            "dias_2": parse_num(raw_val_2822.get("dias_2")),
                            "exame_3": str(raw_val_2822.get("exame_3") or ""),
                            "dias_3": parse_num(raw_val_2822.get("dias_3")),
                            "link": str(d2822.get("link") or ""),
                        }

                        ui.label("1º Exame Médico:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do exame médico", value=state_2822["exame_1"]).classes("w-2/3").bind_value(state_2822, "exame_1")
                            ui.number("Tempo médio (dias)", value=state_2822["dias_1"]).classes("w-1/3").bind_value(state_2822, "dias_1")

                        ui.label("2º Exame Médico:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do exame médico", value=state_2822["exame_2"]).classes("w-2/3").bind_value(state_2822, "exame_2")
                            ui.number("Tempo médio (dias)", value=state_2822["dias_2"]).classes("w-1/3").bind_value(state_2822, "dias_2")

                        ui.label("3º Exame Médico:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do exame médico", value=state_2822["exame_3"]).classes("w-2/3").bind_value(state_2822, "exame_3")
                            ui.number("Tempo médio (dias)", value=state_2822["dias_3"]).classes("w-1/3").bind_value(state_2822, "dias_3")

                        ui.label("Nota 28.2.2: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial de Tempos de Espera (Exames):", value=state_2822["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2822, "link")

                        def salvar_2822():
                            payload_2822 = {
                                "exame_1": state_2822["exame_1"],
                                "dias_1": parse_num(state_2822["dias_1"]),
                                "exame_2": state_2822["exame_2"],
                                "dias_2": parse_num(state_2822["dias_2"]),
                                "exame_3": state_2822["exame_3"],
                                "dias_3": parse_num(state_2822["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.2",
                                valor=payload_2822,
                                pontos=0.0,
                                link=state_2822["link"],
                                comentarios=d2822.get("comentarios", []),
                                status=d2822.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.2", on_click=salvar_2822).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.2", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE DETALHAMENTO DE TEMPOS DE ESPERA - QUESITOS 28.2.3 A 28.2.7
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.3 (Terapias/Tratamentos Médicos com Maior Tempo de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.3 • Terapias/Tratamentos Médicos com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe as 3 terapias/tratamentos médicos com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2823 = res_data.get("28.2.3") or {}
                        raw_val_2823 = d2823.get("valor") or {}
                        if not isinstance(raw_val_2823, dict):
                            raw_val_2823 = {}

                        state_2823 = {
                            "terapia_1": str(raw_val_2823.get("terapia_1") or ""),
                            "dias_1": parse_num(raw_val_2823.get("dias_1")),
                            "terapia_2": str(raw_val_2823.get("terapia_2") or ""),
                            "dias_2": parse_num(raw_val_2823.get("dias_2")),
                            "terapia_3": str(raw_val_2823.get("terapia_3") or ""),
                            "dias_3": parse_num(raw_val_2823.get("dias_3")),
                            "link": str(d2823.get("link") or ""),
                        }

                        ui.label("1ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da terapia/tratamento médico", value=state_2823["terapia_1"]).classes("w-2/3").bind_value(state_2823, "terapia_1")
                            ui.number("Tempo médio (dias)", value=state_2823["dias_1"]).classes("w-1/3").bind_value(state_2823, "dias_1")

                        ui.label("2ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da terapia/tratamento médico", value=state_2823["terapia_2"]).classes("w-2/3").bind_value(state_2823, "terapia_2")
                            ui.number("Tempo médio (dias)", value=state_2823["dias_2"]).classes("w-1/3").bind_value(state_2823, "dias_2")

                        ui.label("3ª Terapia / Tratamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da terapia/tratamento médico", value=state_2823["terapia_3"]).classes("w-2/3").bind_value(state_2823, "terapia_3")
                            ui.number("Tempo médio (dias)", value=state_2823["dias_3"]).classes("w-1/3").bind_value(state_2823, "dias_3")

                        ui.label("Nota 28.2.3: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial (Terapias/Tratamentos):", value=state_2823["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2823, "link")

                        def salvar_2823():
                            payload_2823 = {
                                "terapia_1": state_2823["terapia_1"],
                                "dias_1": parse_num(state_2823["dias_1"]),
                                "terapia_2": state_2823["terapia_2"],
                                "dias_2": parse_num(state_2823["dias_2"]),
                                "terapia_3": state_2823["terapia_3"],
                                "dias_3": parse_num(state_2823["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.3",
                                valor=payload_2823,
                                pontos=0.0,
                                link=state_2823["link"],
                                comentarios=d2823.get("comentarios", []),
                                status=d2823.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.3", on_click=salvar_2823).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.3", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.4 (Medicamentos com Maior Tempo de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.4 • Medicamentos com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe os 3 medicamentos com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2824 = res_data.get("28.2.4") or {}
                        raw_val_2824 = d2824.get("valor") or {}
                        if not isinstance(raw_val_2824, dict):
                            raw_val_2824 = {}

                        state_2824 = {
                            "med_1": str(raw_val_2824.get("med_1") or ""),
                            "dias_1": parse_num(raw_val_2824.get("dias_1")),
                            "med_2": str(raw_val_2824.get("med_2") or ""),
                            "dias_2": parse_num(raw_val_2824.get("dias_2")),
                            "med_3": str(raw_val_2824.get("med_3") or ""),
                            "dias_3": parse_num(raw_val_2824.get("dias_3")),
                            "link": str(d2824.get("link") or ""),
                        }

                        ui.label("1º Medicamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do medicamento", value=state_2824["med_1"]).classes("w-2/3").bind_value(state_2824, "med_1")
                            ui.number("Tempo médio (dias)", value=state_2824["dias_1"]).classes("w-1/3").bind_value(state_2824, "dias_1")

                        ui.label("2º Medicamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do medicamento", value=state_2824["med_2"]).classes("w-2/3").bind_value(state_2824, "med_2")
                            ui.number("Tempo médio (dias)", value=state_2824["dias_2"]).classes("w-1/3").bind_value(state_2824, "dias_2")

                        ui.label("3º Medicamento:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do medicamento", value=state_2824["med_3"]).classes("w-2/3").bind_value(state_2824, "med_3")
                            ui.number("Tempo médio (dias)", value=state_2824["dias_3"]).classes("w-1/3").bind_value(state_2824, "dias_3")

                        ui.label("Nota 28.2.4: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial (Medicamentos):", value=state_2824["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2824, "link")

                        def salvar_2824():
                            payload_2824 = {
                                "med_1": state_2824["med_1"],
                                "dias_1": parse_num(state_2824["dias_1"]),
                                "med_2": state_2824["med_2"],
                                "dias_2": parse_num(state_2824["dias_2"]),
                                "med_3": state_2824["med_3"],
                                "dias_3": parse_num(state_2824["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.4",
                                valor=payload_2824,
                                pontos=0.0,
                                link=state_2824["link"],
                                comentarios=d2824.get("comentarios", []),
                                status=d2824.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.4", on_click=salvar_2824).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.4", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.5 (OPM com Maior Tempo de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.5 • OPM (Órteses, Próteses e Materiais) com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe as 3 OPM com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2825 = res_data.get("28.2.5") or {}
                        raw_val_2825 = d2825.get("valor") or {}
                        if not isinstance(raw_val_2825, dict):
                            raw_val_2825 = {}

                        state_2825 = {
                            "opm_1": str(raw_val_2825.get("opm_1") or ""),
                            "dias_1": parse_num(raw_val_2825.get("dias_1")),
                            "opm_2": str(raw_val_2825.get("opm_2") or ""),
                            "dias_2": parse_num(raw_val_2825.get("dias_2")),
                            "opm_3": str(raw_val_2825.get("opm_3") or ""),
                            "dias_3": parse_num(raw_val_2825.get("dias_3")),
                            "link": str(d2825.get("link") or ""),
                        }

                        ui.label("1ª OPM:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da OPM", value=state_2825["opm_1"]).classes("w-2/3").bind_value(state_2825, "opm_1")
                            ui.number("Tempo médio (dias)", value=state_2825["dias_1"]).classes("w-1/3").bind_value(state_2825, "dias_1")

                        ui.label("2ª OPM:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da OPM", value=state_2825["opm_2"]).classes("w-2/3").bind_value(state_2825, "opm_2")
                            ui.number("Tempo médio (dias)", value=state_2825["dias_2"]).classes("w-1/3").bind_value(state_2825, "dias_2")

                        ui.label("3ª OPM:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da OPM", value=state_2825["opm_3"]).classes("w-2/3").bind_value(state_2825, "opm_3")
                            ui.number("Tempo médio (dias)", value=state_2825["dias_3"]).classes("w-1/3").bind_value(state_2825, "dias_3")

                        ui.label("Nota 28.2.5: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial (OPM):", value=state_2825["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2825, "link")

                        def salvar_2825():
                            payload_2825 = {
                                "opm_1": state_2825["opm_1"],
                                "dias_1": parse_num(state_2825["dias_1"]),
                                "opm_2": state_2825["opm_2"],
                                "dias_2": parse_num(state_2825["dias_2"]),
                                "opm_3": state_2825["opm_3"],
                                "dias_3": parse_num(state_2825["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.5",
                                valor=payload_2825,
                                pontos=0.0,
                                link=state_2825["link"],
                                comentarios=d2825.get("comentarios", []),
                                status=d2825.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.5", on_click=salvar_2825).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.5", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.6 (Cirurgias Eletivas com Maior Tempo de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.6 • Cirurgias Eletivas com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe as 3 cirurgias eletivas da Atenção Especializada com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2826 = res_data.get("28.2.6") or {}
                        raw_val_2826 = d2826.get("valor") or {}
                        if not isinstance(raw_val_2826, dict):
                            raw_val_2826 = {}

                        state_2826 = {
                            "cirurgia_1": str(raw_val_2826.get("cirurgia_1") or ""),
                            "dias_1": parse_num(raw_val_2826.get("dias_1")),
                            "cirurgia_2": str(raw_val_2826.get("cirurgia_2") or ""),
                            "dias_2": parse_num(raw_val_2826.get("dias_2")),
                            "cirurgia_3": str(raw_val_2826.get("cirurgia_3") or ""),
                            "dias_3": parse_num(raw_val_2826.get("dias_3")),
                            "link": str(d2826.get("link") or ""),
                        }

                        ui.label("1ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da cirurgia eletiva", value=state_2826["cirurgia_1"]).classes("w-2/3").bind_value(state_2826, "cirurgia_1")
                            ui.number("Tempo médio (dias)", value=state_2826["dias_1"]).classes("w-1/3").bind_value(state_2826, "dias_1")

                        ui.label("2ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da cirurgia eletiva", value=state_2826["cirurgia_2"]).classes("w-2/3").bind_value(state_2826, "cirurgia_2")
                            ui.number("Tempo médio (dias)", value=state_2826["dias_2"]).classes("w-1/3").bind_value(state_2826, "dias_2")

                        ui.label("3ª Cirurgia Eletiva:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição da cirurgia eletiva", value=state_2826["cirurgia_3"]).classes("w-2/3").bind_value(state_2826, "cirurgia_3")
                            ui.number("Tempo médio (dias)", value=state_2826["dias_3"]).classes("w-1/3").bind_value(state_2826, "dias_3")

                        ui.label("Nota 28.2.6: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial (Cirurgias Eletivas):", value=state_2826["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2826, "link")

                        def salvar_2826():
                            payload_2826 = {
                                "cirurgia_1": state_2826["cirurgia_1"],
                                "dias_1": parse_num(state_2826["dias_1"]),
                                "cirurgia_2": state_2826["cirurgia_2"],
                                "dias_2": parse_num(state_2826["dias_2"]),
                                "cirurgia_3": state_2826["cirurgia_3"],
                                "dias_3": parse_num(state_2826["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.6",
                                valor=payload_2826,
                                pontos=0.0,
                                link=state_2826["link"],
                                comentarios=d2826.get("comentarios", []),
                                status=d2826.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.6", on_click=salvar_2826).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.6", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 28.2.7 (Outros Serviços da Atenção Especializada com Maior Tempo de Espera)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("28.2.7 • Outros Serviços da Atenção Especializada com Maior Tempo de Espera").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe os 3 outros serviços da Atenção Especializada com maior tempo médio de espera no município:").classes("text-sm text-gray-700 mb-4")

                        d2827 = res_data.get("28.2.7") or {}
                        raw_val_2827 = d2827.get("valor") or {}
                        if not isinstance(raw_val_2827, dict):
                            raw_val_2827 = {}

                        state_2827 = {
                            "servico_1": str(raw_val_2827.get("servico_1") or ""),
                            "dias_1": parse_num(raw_val_2827.get("dias_1")),
                            "servico_2": str(raw_val_2827.get("servico_2") or ""),
                            "dias_2": parse_num(raw_val_2827.get("dias_2")),
                            "servico_3": str(raw_val_2827.get("servico_3") or ""),
                            "dias_3": parse_num(raw_val_2827.get("dias_3")),
                            "link": str(d2827.get("link") or ""),
                        }

                        ui.label("1º Outro Serviço:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do Serviço", value=state_2827["servico_1"]).classes("w-2/3").bind_value(state_2827, "servico_1")
                            ui.number("Tempo médio (dias)", value=state_2827["dias_1"]).classes("w-1/3").bind_value(state_2827, "dias_1")

                        ui.label("2º Outro Serviço:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do Serviço", value=state_2827["servico_2"]).classes("w-2/3").bind_value(state_2827, "servico_2")
                            ui.number("Tempo médio (dias)", value=state_2827["dias_2"]).classes("w-1/3").bind_value(state_2827, "dias_2")

                        ui.label("3º Outro Serviço:").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Descrição do Serviço", value=state_2827["servico_3"]).classes("w-2/3").bind_value(state_2827, "servico_3")
                            ui.number("Tempo médio (dias)", value=state_2827["dias_3"]).classes("w-1/3").bind_value(state_2827, "dias_3")

                        ui.label("Nota 28.2.7: Informativo (0.0 pontos)").classes("text-sm font-bold text-green-600 my-2")

                        ui.textarea(label="Link / Relatório Gerencial (Outros Serviços):", value=state_2827["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_2827, "link")

                        def salvar_2827():
                            payload_2827 = {
                                "servico_1": state_2827["servico_1"],
                                "dias_1": parse_num(state_2827["dias_1"]),
                                "servico_2": state_2827["servico_2"],
                                "dias_2": parse_num(state_2827["dias_2"]),
                                "servico_3": state_2827["servico_3"],
                                "dias_3": parse_num(state_2827["dias_3"]),
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="28.2.7",
                                valor=payload_2827,
                                pontos=0.0,
                                link=state_2827["link"],
                                comentarios=d2827.get("comentarios", []),
                                status=d2827.get("status", "Pendente")
                            )
                            ui.notify("Quesito 28.2.7 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 28.2.7", on_click=salvar_2827).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("28.2.7", res_data, render_conteudo.refresh)

    # =============================================================================
                    # MÓDULO DE CADASTRO, REGULAÇÃO E ATENÇÃO PRÉ-HOSPITALAR - QUESITOS 29 A 31
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 29.0 (Cadastro CNES)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("29.0 • Atualização do CNES").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município mantém atualizado o Cadastro de Estabelecimentos e Profissionais de Saúde (CNES)?").classes("text-sm text-gray-700 mb-4")

                        d290 = res_data.get("29.0") or {}
                        state_290 = {
                            "opcao": d290.get("valor") if isinstance(d290.get("valor"), str) else "Não",
                            "link": str(d290.get("link") or "")
                        }

                        opts_290 = [
                            "SIM, os cadastros de estabelecimentos e de profissionais estão atualizados",
                            "Sim, somente o cadastro de estabelecimentos está atualizado",
                            "Sim, somente o cadastro de profissionais está atualizado",
                            "Não"
                        ]

                        lbl_pontos_290 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_290(v):
                            if v == "SIM, os cadastros de estabelecimentos e de profissionais estão atualizados":
                                return 15.0
                            elif v in ["Sim, somente o cadastro de estabelecimentos está atualizado", "Sim, somente o cadastro de profissionais está atualizado"]:
                                return 5.0
                            return 0.0

                        def atualizar_pontos_290():
                            pts = calc_pontos_290(state_290["opcao"])
                            lbl_pontos_290.set_text(f"Pontuação Calculada: {pts:.1f} / 15.0 pontos")

                        radio_290 = ui.radio(opts_290, value=state_290["opcao"]).classes("mb-3").bind_value(state_290, "opcao")
                        radio_290.on("update:model-value", lambda: atualizar_pontos_290())

                        ui.textarea(label="Link / Comprovação de Atualização CNES:", value=state_290["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_290, "link")

                        atualizar_pontos_290()

                        def salvar_290():
                            pts = calc_pontos_290(state_290["opcao"])
                            save_resposta(
                                ano=ano_sel,
                                qid="29.0",
                                valor=state_290["opcao"],
                                pontos=pts,
                                link=state_290["link"],
                                comentarios=d290.get("comentarios", []),
                                status=d290.get("status", "Pendente")
                            )
                            ui.notify("Quesito 29.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 29.0", on_click=salvar_290).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("29.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 30.0 (Complexo Regulador Municipal)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("30.0 • Complexo Regulador Municipal").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município possui Complexo Regulador Municipal?").classes("text-sm text-gray-700 mb-4")

                        d300 = res_data.get("30.0") or {}
                        state_300 = {
                            "opcao": d300.get("valor") if isinstance(d300.get("valor"), str) else "Não",
                            "link": str(d300.get("link") or "")
                        }

                        lbl_pontos_300 = ui.label("Nota 30.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                        radio_300 = ui.radio(["Sim", "Não"], value=state_300["opcao"]).classes("mb-3").bind_value(state_300, "opcao")

                        ui.textarea(label="Link / Legislação / Ato de Criação do Complexo Regulador:", value=state_300["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_300, "link")

                        def salvar_300():
                            save_resposta(
                                ano=ano_sel,
                                qid="30.0",
                                valor=state_300["opcao"],
                                pontos=0.0,
                                link=state_300["link"],
                                comentarios=d300.get("comentarios", []),
                                status=d300.get("status", "Pendente")
                            )
                            ui.notify("Quesito 30.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 30.0", on_click=salvar_300).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("30.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 30.1 (Central de Regulação) - Condicionado a 30.0 = Sim
                    # -----------------------------------------------------------------------------
                    if state_300["opcao"] == "Sim":
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("30.1 • Central de Regulação").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("O Complexo Regulador Municipal possui Central de Regulação?").classes("text-sm text-gray-700 mb-4")

                            d301 = res_data.get("30.1") or {}
                            state_301 = {
                                "opcao": d301.get("valor") if isinstance(d301.get("valor"), str) else "Não",
                                "link": str(d301.get("link") or "")
                            }

                            lbl_pontos_301 = ui.label("Nota 30.1: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                            radio_301 = ui.radio(["Sim", "Não"], value=state_301["opcao"]).classes("mb-3").bind_value(state_301, "opcao")

                            ui.textarea(label="Link / Documento da Central de Regulação:", value=state_301["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_301, "link")

                            def salvar_301():
                                save_resposta(
                                    ano=ano_sel,
                                    qid="30.1",
                                    valor=state_301["opcao"],
                                    pontos=0.0,
                                    link=state_301["link"],
                                    comentarios=d301.get("comentarios", []),
                                    status=d301.get("status", "Pendente")
                                )
                                ui.notify("Quesito 30.1 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 30.1", on_click=salvar_301).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("30.1", res_data, render_conteudo.refresh)

                        # -------------------------------------------------------------------------
                        # QUESITO 30.1.1 (Tipos de Central de Regulação) - Condicionado a 30.1 = Sim
                        # -------------------------------------------------------------------------
                        if state_301["opcao"] == "Sim":
                            with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-8"):
                                ui.label("30.1.1 • Tipos de Central de Regulação").classes("text-xl font-semibold text-blue-600 mb-2")
                                ui.label("Assinale os tipos de central de regulação municipal ou regional utilizados pelo município:").classes("text-sm text-gray-700 mb-4")

                                d3011 = res_data.get("30.1.1") or {}
                                raw_val_3011 = d3011.get("valor") or []
                                if not isinstance(raw_val_3011, list):
                                    raw_val_3011 = []

                                state_3011 = {
                                    "urgencia": "Central de Urgência" in raw_val_3011,
                                    "internacoes": "Central de Internações" in raw_val_3011,
                                    "consultas": "Central de Consultas e Serviços de Apoio Diagnóstico e Terapêutico" in raw_val_3011,
                                    "link": str(d3011.get("link") or "")
                                }

                                lbl_pontos_3011 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                                def calc_pontos_3011():
                                    p = 0.0
                                    if state_3011["urgencia"]:
                                        p += 3.0
                                    if state_3011["internacoes"]:
                                        p += 3.0
                                    if state_3011["consultas"]:
                                        p += 3.0
                                    return p

                                def atualizar_pontos_3011():
                                    pts = calc_pontos_3011()
                                    lbl_pontos_3011.set_text(f"Pontuação Calculada: {pts:.1f} / 9.0 pontos")

                                cb_urg = ui.checkbox("Central de Urgência (3,0 pontos)", value=state_3011["urgencia"]).bind_value(state_3011, "urgencia")
                                cb_int = ui.checkbox("Central de Internações (3,0 pontos)", value=state_3011["internacoes"]).bind_value(state_3011, "internacoes")
                                cb_con = ui.checkbox("Central de Consultas e Serviços de Apoio Diagnóstico e Terapêutico (3,0 pontos)", value=state_3011["consultas"]).bind_value(state_3011, "consultas")

                                cb_urg.on("change", lambda: atualizar_pontos_3011())
                                cb_int.on("change", lambda: atualizar_pontos_3011())
                                cb_con.on("change", lambda: atualizar_pontos_3011())

                                ui.textarea(label="Link / Portaria de Habilitação das Centrais:", value=state_3011["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_3011, "link")

                                atualizar_pontos_3011()

                                def salvar_3011():
                                    sel_list = []
                                    if state_3011["urgencia"]:
                                        sel_list.append("Central de Urgência")
                                    if state_3011["internacoes"]:
                                        sel_list.append("Central de Internações")
                                    if state_3011["consultas"]:
                                        sel_list.append("Central de Consultas e Serviços de Apoio Diagnóstico e Terapêutico")

                                    pts = calc_pontos_3011()
                                    save_resposta(
                                        ano=ano_sel,
                                        qid="30.1.1",
                                        valor=sel_list,
                                        pontos=pts,
                                        link=state_3011["link"],
                                        comentarios=d3011.get("comentarios", []),
                                        status=d3011.get("status", "Pendente")
                                    )
                                    ui.notify("Quesito 30.1.1 salvo com sucesso!", type="positive")
                                    if render_conteudo.refresh:
                                        render_conteudo.refresh()

                                ui.button("💾 SALVAR QUESITO 30.1.1", on_click=salvar_3011).classes("bg-blue-600 text-white font-bold my-2")
                                ui.separator().classes("my-2")
                                bloco_comentarios("30.1.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 31.0 (Atenção Pré-Hospitalar e Central SAMU 192)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("31.0 • Atenção Pré-Hospitalar e Central SAMU 192").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município possui serviços de atenção pré-hospitalar e Central Samu 192 ou integra Central Samu 192 de abrangência regional?").classes("text-sm text-gray-700 mb-4")

                        d310 = res_data.get("31.0") or {}
                        state_310 = {
                            "opcao": d310.get("valor") if isinstance(d310.get("valor"), str) else "Não",
                            "link": str(d310.get("link") or "")
                        }

                        lbl_pontos_310 = ui.label("Nota 31.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                        radio_310 = ui.radio(["Sim", "Não"], value=state_310["opcao"]).classes("mb-3").bind_value(state_310, "opcao")

                        ui.textarea(label="Link / Termo de Adesão / Portaria SAMU 192:", value=state_310["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_310, "link")

                        def salvar_310():
                            save_resposta(
                                ano=ano_sel,
                                qid="31.0",
                                valor=state_310["opcao"],
                                pontos=0.0,
                                link=state_310["link"],
                                comentarios=d310.get("comentarios", []),
                                status=d310.get("status", "Pendente")
                            )
                            ui.notify("Quesito 31.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 31.0", on_click=salvar_310).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("31.0", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE SAMU, COMPOSIÇÃO DE EQUIPES E ESTOQUE DE INSUMOS - QUESITOS 31.1 A 32.0
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 31.1 (Tempo de Resposta em Minutos dos Atendimentos do SAMU) - CORRIGIDO
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("31.1 • Tempo de Resposta dos Atendimentos do SAMU (ou equivalente)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe os tempos de resposta (em minutos) para os anos de 2023, 2024 e 2025:").classes("text-sm text-gray-700 mb-4")

                        d311 = res_data.get("31.1") or {}
                        raw_val_311 = d311.get("valor") or {}
                        if not isinstance(raw_val_311, dict):
                            raw_val_311 = {}

                        state_311 = {
                            # Tempos Médios Principais
                            "tmr_2023": parse_num(raw_val_311.get("tmr_2023")),
                            "tmr_2024": parse_num(raw_val_311.get("tmr_2024")),
                            "tmr_2025": parse_num(raw_val_311.get("tmr_2025")),
                            # Detalhamento Mín/Med/Máx 2023
                            "min_2023": parse_num(raw_val_311.get("min_2023")),
                            "med_2023": parse_num(raw_val_311.get("med_2023")),
                            "max_2023": parse_num(raw_val_311.get("max_2023")),
                            # Detalhamento Mín/Med/Máx 2024
                            "min_2024": parse_num(raw_val_311.get("min_2024")),
                            "med_2024": parse_num(raw_val_311.get("med_2024")),
                            "max_2024": parse_num(raw_val_311.get("max_2024")),
                            # Detalhamento Mín/Med/Máx 2025
                            "min_2025": parse_num(raw_val_311.get("min_2025")),
                            "med_2025": parse_num(raw_val_311.get("med_2025")),
                            "max_2025": parse_num(raw_val_311.get("max_2025")),
                            "link": str(d311.get("link") or ""),
                        }

                        lbl_pontos_311 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_311():
                            # Trata None convertendo para float 0.0 com segurança
                            t23 = float(parse_num(state_311["tmr_2023"]) or 0)
                            t24 = float(parse_num(state_311["tmr_2024"]) or 0)
                            t25 = float(parse_num(state_311["tmr_2025"]) or 0)

                            # Se os três anos estiverem preenchidos com valores válidos (>0)
                            if t23 > 0 and t24 > 0 and t25 > 0:
                                media_historica = (t23 + t24) / 2.0
                                if t25 > media_historica:
                                    return -5.0  # Perde 5 pontos se o tempo aumentou
                            return 0.0

                        def atualizar_pontos_311():
                            pts = calc_pontos_311()
                            if pts < 0:
                                lbl_pontos_311.set_text(f"Pontuação Calculada: {pts:.1f} pontos (Penalidade por aumento no tempo de resposta)")
                                lbl_pontos_311.classes(remove="text-green-600", add="text-red-600")
                            else:
                                lbl_pontos_311.set_text("Pontuação Calculada: 0.0 pontos (Sem penalidade)")
                                lbl_pontos_311.classes(remove="text-red-600", add="text-green-600")

                        ui.label("Tempos Médios de Resposta (Gerais):").classes("text-sm font-bold text-gray-800 mt-2")
                        with ui.row().classes("w-full gap-4"):
                            inp_t23 = ui.number("2023 - Tempo Médio (min)", value=state_311["tmr_2023"]).classes("w-1/3").bind_value(state_311, "tmr_2023")
                            inp_t24 = ui.number("2024 - Tempo Médio (min)", value=state_311["tmr_2024"]).classes("w-1/3").bind_value(state_311, "tmr_2024")
                            inp_t25 = ui.number("2025 - Tempo Médio (min)", value=state_311["tmr_2025"]).classes("w-1/3").bind_value(state_311, "tmr_2025")

                        inp_t23.on("update:model-value", lambda: atualizar_pontos_311())
                        inp_t24.on("update:model-value", lambda: atualizar_pontos_311())
                        inp_t25.on("update:model-value", lambda: atualizar_pontos_311())

                        ui.separator().classes("my-3")
                        ui.label("Detalhamento de Tempos por Ano (Mínimo, Médio e Máximo em Minutos):").classes("text-sm font-bold text-gray-800")

                        ui.label("Ano 2023:").classes("text-xs font-semibold text-gray-600 mt-1")
                        with ui.row().classes("w-full gap-4"):
                            ui.number("2023 - Mínimo", value=state_311["min_2023"]).classes("w-1/3").bind_value(state_311, "min_2023")
                            ui.number("2023 - Médio", value=state_311["med_2023"]).classes("w-1/3").bind_value(state_311, "med_2023")
                            ui.number("2023 - Máximo", value=state_311["max_2023"]).classes("w-1/3").bind_value(state_311, "max_2023")

                        ui.label("Ano 2024:").classes("text-xs font-semibold text-gray-600 mt-1")
                        with ui.row().classes("w-full gap-4"):
                            ui.number("2024 - Mínimo", value=state_311["min_2024"]).classes("w-1/3").bind_value(state_311, "min_2024")
                            ui.number("2024 - Médio", value=state_311["med_2024"]).classes("w-1/3").bind_value(state_311, "med_2024")
                            ui.number("2024 - Máximo", value=state_311["max_2024"]).classes("w-1/3").bind_value(state_311, "max_2024")

                        ui.label("Ano 2025:").classes("text-xs font-semibold text-gray-600 mt-1")
                        with ui.row().classes("w-full gap-4"):
                            ui.number("2025 - Mínimo", value=state_311["min_2025"]).classes("w-1/3").bind_value(state_311, "min_2025")
                            ui.number("2025 - Médio", value=state_311["med_2025"]).classes("w-1/3").bind_value(state_311, "med_2025")
                            ui.number("2025 - Máximo", value=state_311["max_2025"]).classes("w-1/3").bind_value(state_311, "max_2025")

                        atualizar_pontos_311()

                        ui.textarea(label="Link / Relatório do Sistema de Gestão de Urgências:", value=state_311["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_311, "link")

                        def salvar_311():
                            payload_311 = {
                                "tmr_2023": parse_num(state_311["tmr_2023"]),
                                "tmr_2024": parse_num(state_311["tmr_2024"]),
                                "tmr_2025": parse_num(state_311["tmr_2025"]),
                                "min_2023": parse_num(state_311["min_2023"]),
                                "med_2023": parse_num(state_311["med_2023"]),
                                "max_2023": parse_num(state_311["max_2023"]),
                                "min_2024": parse_num(state_311["min_2024"]),
                                "med_2024": parse_num(state_311["med_2024"]),
                                "max_2024": parse_num(state_311["max_2024"]),
                                "min_2025": parse_num(state_311["min_2025"]),
                                "med_2025": parse_num(state_311["med_2025"]),
                                "max_2025": parse_num(state_311["max_2025"]),
                            }
                            pts = calc_pontos_311()
                            save_resposta(
                                ano=ano_sel,
                                qid="31.1",
                                valor=payload_311,
                                pontos=pts,
                                link=state_311["link"],
                                comentarios=d311.get("comentarios", []),
                                status=d311.get("status", "Pendente")
                            )
                            ui.notify("Quesito 31.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 31.1", on_click=salvar_311).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("31.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 31.2 (Composição Mínima das Equipes da Central de Regulação)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("31.2 • Composição das Equipes da Central de Regulação das Urgências").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("As equipes da Central de Regulação das Urgências tiveram ao menos a composição mínima estipulada na legislação no decorrer do exercício?").classes("text-sm text-gray-700 mb-4")

                        d312 = res_data.get("31.2") or {}
                        state_312 = {
                            "opcao": d312.get("valor") if isinstance(d312.get("valor"), str) else "Todas as equipes tinham composição mínima",
                            "link": str(d312.get("link") or "")
                        }

                        opts_312 = [
                            "Todas as equipes tinham composição mínima",
                            "A maior parte das equipes tinham composição mínima",
                            "A menor parte das equipes tinham composição mínima",
                            "Nenhuma equipe tinha composição mínima"
                        ]

                        lbl_pontos_312 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_312(v):
                            if v == "A maior parte das equipes tinham composição mínima":
                                return -3.0
                            elif v == "A menor parte das equipes tinham composição mínima":
                                return -7.0
                            elif v == "Nenhuma equipe tinha composição mínima":
                                return -10.0
                            return 0.0

                        def atualizar_pontos_312():
                            pts = calc_pontos_312(state_312["opcao"])
                            if pts < 0:
                                lbl_pontos_312.set_text(f"Pontuação Calculada: {pts:.1f} pontos (Penalidade aplicável)")
                                lbl_pontos_312.classes(remove="text-green-600", add="text-red-600")
                            else:
                                lbl_pontos_312.set_text("Pontuação Calculada: 0.0 pontos")
                                lbl_pontos_312.classes(remove="text-red-600", add="text-green-600")

                        radio_312 = ui.radio(opts_312, value=state_312["opcao"]).classes("mb-3").bind_value(state_312, "opcao")
                        radio_312.on("update:model-value", lambda: atualizar_pontos_312())

                        ui.textarea(label="Link / Relatório de Escala de Plantão (Central de Regulação):", value=state_312["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_312, "link")

                        atualizar_pontos_312()

                        def salvar_312():
                            pts = calc_pontos_312(state_312["opcao"])
                            save_resposta(
                                ano=ano_sel,
                                qid="31.2",
                                valor=state_312["opcao"],
                                pontos=pts,
                                link=state_312["link"],
                                comentarios=d312.get("comentarios", []),
                                status=d312.get("status", "Pendente")
                            )
                            ui.notify("Quesito 31.2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 31.2", on_click=salvar_312).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("31.2", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 31.3 (Composição Mínima das Equipes das Unidades Móveis)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("31.3 • Composição das Equipes das Unidades Móveis (Ambulâncias/USA/USB)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("As equipes das Unidades Móveis tiveram ao menos a composição mínima estipulada na legislação no decorrer do exercício?").classes("text-sm text-gray-700 mb-4")

                        d313 = res_data.get("31.3") or {}
                        state_313 = {
                            "opcao": d313.get("valor") if isinstance(d313.get("valor"), str) else "Todas as equipes tinham composição mínima",
                            "link": str(d313.get("link") or "")
                        }

                        opts_313 = [
                            "Todas as equipes tinham composição mínima",
                            "A maior parte das equipes tinham composição mínima",
                            "A menor parte das equipes tinham composição mínima",
                            "Nenhuma equipe tinha composição mínima"
                        ]

                        lbl_pontos_313 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_313(v):
                            if v == "A maior parte das equipes tinham composição mínima":
                                return -10.0
                            elif v == "A menor parte das equipes tinham composição mínima":
                                return -15.0
                            elif v == "Nenhuma equipe tinha composição mínima":
                                return -20.0
                            return 0.0

                        def atualizar_pontos_313():
                            pts = calc_pontos_313(state_313["opcao"])
                            if pts < 0:
                                lbl_pontos_313.set_text(f"Pontuação Calculada: {pts:.1f} pontos (Penalidade aplicável)")
                                lbl_pontos_313.classes(remove="text-green-600", add="text-red-600")
                            else:
                                lbl_pontos_313.set_text("Pontuação Calculada: 0.0 pontos")
                                lbl_pontos_313.classes(remove="text-red-600", add="text-green-600")

                        radio_313 = ui.radio(opts_313, value=state_313["opcao"]).classes("mb-3").bind_value(state_313, "opcao")
                        radio_313.on("update:model-value", lambda: atualizar_pontos_313())

                        ui.textarea(label="Link / Relatório de Escala de Plantão (Unidades Móveis):", value=state_313["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_313, "link")

                        atualizar_pontos_313()

                        def salvar_313():
                            pts = calc_pontos_313(state_313["opcao"])
                            save_resposta(
                                ano=ano_sel,
                                qid="31.3",
                                valor=state_313["opcao"],
                                pontos=pts,
                                link=state_313["link"],
                                comentarios=d313.get("comentarios", []),
                                status=d313.get("status", "Pendente")
                            )
                            ui.notify("Quesito 31.3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 31.3", on_click=salvar_313).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("31.3", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 32.0 (Sistema Informatizado para Estoque de Materiais e Insumos Médicos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("32.0 • Sistema Informatizado de Gerenciamento de Estoque de Insumos").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município utiliza sistema informatizado para gerenciar o estoque de materiais e insumos médicos?").classes("text-sm text-gray-700 mb-4")

                        d320 = res_data.get("32.0") or {}
                        state_320 = {
                            "opcao": d320.get("valor") if isinstance(d320.get("valor"), str) else "Não",
                            "link": str(d320.get("link") or "")
                        }

                        ui.label("Nota 32.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                        radio_320 = ui.radio(["Sim", "Não"], value=state_320["opcao"]).classes("mb-3").bind_value(state_320, "opcao")

                        ui.textarea(label="Link / Comprovação do Sistema de Insumos Médicos:", value=state_320["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_320, "link")

                        def salvar_320():
                            save_resposta(
                                ano=ano_sel,
                                qid="32.0",
                                valor=state_320["opcao"],
                                pontos=0.0,
                                link=state_320["link"],
                                comentarios=d320.get("comentarios", []),
                                status=d320.get("status", "Pendente")
                            )
                            ui.notify("Quesito 32.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 32.0", on_click=salvar_320).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("32.0", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE INSUMOS, OUVIDORIA E AUDITORIA (SNA) - QUESITOS 32.1 A 35.2
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 32.1 (Funções do Sistema de Gestão de Estoque de Insumos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("32.1 • Funções do Sistema de Gestão de Estoque").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Assinale as funções do sistema de gestão de estoque de materiais e insumos médicos:").classes("text-sm text-gray-700 mb-4")

                        d321 = res_data.get("32.1") or {}
                        raw_val_321 = d321.get("valor") or []
                        if not isinstance(raw_val_321, list):
                            raw_val_321 = []

                        state_321 = {
                            "posicao": "Fornece a posição de estoque, movimentação de entrada e saída, lote e validade" in raw_val_321,
                            "compras": "Gerenciar o processo de compras dos insumos/materiais de saúde, desde o planejamento até a entrega e o recebimento da nota fiscal" in raw_val_321,
                            "reposicao": "Gerenciar a reposição dos insumos/materiais de saúde por estabelecimento de saúde" in raw_val_321,
                            "outros": "Outros" in raw_val_321,
                            "link": str(d321.get("link") or "")
                        }

                        lbl_pontos_321 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_321():
                            p = 0.0
                            if state_321["posicao"]:
                                p += 15.0
                            if state_321["compras"]:
                                p += 15.0
                            if state_321["reposicao"]:
                                p += 15.0
                            return p

                        def atualizar_pontos_321():
                            pts = calc_pontos_321()
                            lbl_pontos_321.set_text(f"Pontuação Calculada: {pts:.1f} / 45.0 pontos")

                        cb_pos = ui.checkbox("Fornece a posição de estoque, movimentação de entrada e saída, lote e validade (15,0 pontos)", value=state_321["posicao"]).bind_value(state_321, "posicao")
                        cb_com = ui.checkbox("Gerenciar o processo de compras dos insumos/materiais de saúde, desde o planejamento até a entrega e nota fiscal (15,0 pontos)", value=state_321["compras"]).bind_value(state_321, "compras")
                        cb_rep = ui.checkbox("Gerenciar a reposição dos insumos/materiais de saúde por estabelecimento de saúde (15,0 pontos)", value=state_321["reposicao"]).bind_value(state_321, "reposicao")
                        cb_out = ui.checkbox("Outros (0,0 ponto)", value=state_321["outros"]).bind_value(state_321, "outros")

                        cb_pos.on("change", lambda: atualizar_pontos_321())
                        cb_com.on("change", lambda: atualizar_pontos_321())
                        cb_rep.on("change", lambda: atualizar_pontos_321())
                        cb_out.on("change", lambda: atualizar_pontos_321())

                        ui.textarea(label="Link / Comprovação das Funcionalidades do Sistema:", value=state_321["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_321, "link")

                        atualizar_pontos_321()

                        def salvar_321():
                            sel_list = []
                            if state_321["posicao"]:
                                sel_list.append("Fornece a posição de estoque, movimentação de entrada e saída, lote e validade")
                            if state_321["compras"]:
                                sel_list.append("Gerenciar o processo de compras dos insumos/materiais de saúde, desde o planejamento até a entrega e o recebimento da nota fiscal")
                            if state_321["reposicao"]:
                                sel_list.append("Gerenciar a reposição dos insumos/materiais de saúde por estabelecimento de saúde")
                            if state_321["outros"]:
                                sel_list.append("Outros")

                            pts = calc_pontos_321()
                            save_resposta(
                                ano=ano_sel,
                                qid="32.1",
                                valor=sel_list,
                                pontos=pts,
                                link=state_321["link"],
                                comentarios=d321.get("comentarios", []),
                                status=d321.get("status", "Pendente")
                            )
                            ui.notify("Quesito 32.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 32.1", on_click=salvar_321).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("32.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 33.0 (Ouvidoria da Saúde Implantada)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("33.0 • Ouvidoria da Saúde").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município possui Ouvidoria da Saúde implantada?").classes("text-sm text-gray-700 mb-4")

                        d330 = res_data.get("33.0") or {}
                        state_330 = {
                            "opcao": d330.get("valor") if isinstance(d330.get("valor"), str) else "Não",
                            "link": str(d330.get("link") or "")
                        }

                        ui.label("Nota 33.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                        radio_330 = ui.radio(["Sim", "Não"], value=state_330["opcao"]).classes("mb-3").bind_value(state_330, "opcao")

                        ui.textarea(label="Link / Ato de Criação da Ouvidoria da Saúde:", value=state_330["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_330, "link")

                        def salvar_330():
                            save_resposta(
                                ano=ano_sel,
                                qid="33.0",
                                valor=state_330["opcao"],
                                pontos=0.0,
                                link=state_330["link"],
                                comentarios=d330.get("comentarios", []),
                                status=d330.get("status", "Pendente")
                            )
                            ui.notify("Quesito 33.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 33.0", on_click=salvar_330).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("33.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 33.1 (Características da Ouvidoria) - Condicionado a 33.0 = Sim
                    # -----------------------------------------------------------------------------
                    if state_330["opcao"] == "Sim":
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("33.1 • Características da Ouvidoria da Saúde").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Assinale as características da Ouvidoria da Saúde:").classes("text-sm text-gray-700 mb-4")

                            d331 = res_data.get("33.1") or {}
                            raw_val_331 = d331.get("valor") or []
                            if not isinstance(raw_val_331, list):
                                raw_val_331 = []

                            state_331 = {
                                "ato_formal": "Instituída por ato formal no organograma da secretaria de saúde ou equivalente" in raw_val_331,
                                "estrutura_fisica": "Possui estrutura física" in raw_val_331,
                                "equipe": "Possui equipe ou profissional designado" in raw_val_331,
                                "outros": "Outros" in raw_val_331,
                                "link": str(d331.get("link") or "")
                            }

                            lbl_pontos_331 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                            def calc_pontos_331():
                                p = 0.0
                                if state_331["ato_formal"]:
                                    p += 3.0
                                if state_331["estrutura_fisica"]:
                                    p += 2.0
                                if state_331["equipe"]:
                                    p += 5.0
                                return p

                            def atualizar_pontos_331():
                                pts = calc_pontos_331()
                                lbl_pontos_331.set_text(f"Pontuação Calculada: {pts:.1f} / 10.0 pontos")

                            cb_ato = ui.checkbox("Instituída por ato formal no organograma da secretaria ou equivalente (3,0 pontos)", value=state_331["ato_formal"]).bind_value(state_331, "ato_formal")
                            cb_est = ui.checkbox("Possui estrutura física (2,0 pontos)", value=state_331["estrutura_fisica"]).bind_value(state_331, "estrutura_fisica")
                            cb_eqp = ui.checkbox("Possui equipe ou profissional designado (5,0 pontos)", value=state_331["equipe"]).bind_value(state_331, "equipe")
                            cb_out33 = ui.checkbox("Outros (0,0 ponto)", value=state_331["outros"]).bind_value(state_331, "outros")

                            cb_ato.on("change", lambda: atualizar_pontos_331())
                            cb_est.on("change", lambda: atualizar_pontos_331())
                            cb_eqp.on("change", lambda: atualizar_pontos_331())
                            cb_out33.on("change", lambda: atualizar_pontos_331())

                            ui.textarea(label="Link / Portaria de Designação / Fotos da Estrutura:", value=state_331["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_331, "link")

                            atualizar_pontos_331()

                            def salvar_331():
                                sel_list = []
                                if state_331["ato_formal"]:
                                    sel_list.append("Instituída por ato formal no organograma da secretaria de saúde ou equivalente")
                                if state_331["estrutura_fisica"]:
                                    sel_list.append("Possui estrutura física")
                                if state_331["equipe"]:
                                    sel_list.append("Possui equipe ou profissional designado")
                                if state_331["outros"]:
                                    sel_list.append("Outros")

                                pts = calc_pontos_331()
                                save_resposta(
                                    ano=ano_sel,
                                    qid="33.1",
                                    valor=sel_list,
                                    pontos=pts,
                                    link=state_331["link"],
                                    comentarios=d331.get("comentarios", []),
                                    status=d331.get("status", "Pendente")
                                )
                                ui.notify("Quesito 33.1 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 33.1", on_click=salvar_331).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("33.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 34.0 (Sistema OuvidorSUS ou Equivalente)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("34.0 • Sistema OuvidorSUS ou Equivalente").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município utiliza o Sistema OuvidorSUS ou sistema equivalente que, além de permitir a disseminação de informações, o registro e o encaminhamento das manifestações dos cidadãos, possibilita troca de informações entre os órgãos responsáveis pela gestão do SUS?").classes("text-sm text-gray-700 mb-4")

                        d340 = res_data.get("34.0") or {}
                        state_340 = {
                            "opcao": d340.get("valor") if isinstance(d340.get("valor"), str) else "Não",
                            "link": str(d340.get("link") or "")
                        }

                        lbl_pontos_340 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_340(v):
                            return 5.0 if v == "Sim" else 0.0

                        def atualizar_pontos_340():
                            pts = calc_pontos_340(state_340["opcao"])
                            lbl_pontos_340.set_text(f"Pontuação Calculada: {pts:.1f} / 5.0 pontos")

                        radio_340 = ui.radio(["Sim", "Não"], value=state_340["opcao"]).classes("mb-3").bind_value(state_340, "opcao")
                        radio_340.on("update:model-value", lambda: atualizar_pontos_340())

                        ui.textarea(label="Link / Comprovação de Uso do OuvidorSUS:", value=state_340["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_340, "link")

                        atualizar_pontos_340()

                        def salvar_340():
                            pts = calc_pontos_340(state_340["opcao"])
                            save_resposta(
                                ano=ano_sel,
                                qid="34.0",
                                valor=state_340["opcao"],
                                pontos=pts,
                                link=state_340["link"],
                                comentarios=d340.get("comentarios", []),
                                status=d340.get("status", "Pendente")
                            )
                            ui.notify("Quesito 34.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 34.0", on_click=salvar_340).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("34.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 35.0 (Componente Municipal do Sistema Nacional de Auditoria - SNA)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("35.0 • Componente Municipal do SNA").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município possui o componente municipal do Sistema Nacional de Auditoria?").classes("text-sm text-gray-700 mb-4")

                        d350 = res_data.get("35.0") or {}
                        state_350 = {
                            "opcao": d350.get("valor") if isinstance(d350.get("valor"), str) else "Não",
                            "link": str(d350.get("link") or "")
                        }

                        ui.label("Nota 35.0: Informativo (0.0 pontos)").classes("text-sm font-bold text-gray-600 my-2")

                        radio_350 = ui.radio(["Sim", "Não"], value=state_350["opcao"]).classes("mb-3").bind_value(state_350, "opcao")

                        ui.textarea(label="Link / Lei ou Ato Instituidor do SNA Municipal:", value=state_350["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_350, "link")

                        def salvar_350():
                            save_resposta(
                                ano=ano_sel,
                                qid="35.0",
                                valor=state_350["opcao"],
                                pontos=0.0,
                                link=state_350["link"],
                                comentarios=d350.get("comentarios", []),
                                status=d350.get("status", "Pendente")
                            )
                            ui.notify("Quesito 35.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 35.0", on_click=salvar_350).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("35.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 35.1 E 35.2 - Condicionados a 35.0 = Sim
                    # -----------------------------------------------------------------------------
                    if state_350["opcao"] == "Sim":
                        # -------------------------------------------------------------------------
                        # QUESITO 35.1 (Características do SNA Municipal)
                        # -------------------------------------------------------------------------
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("35.1 • Características do Componente Municipal do SNA").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Assinale as características do componente municipal do Sistema Nacional de Auditoria - SNA:").classes("text-sm text-gray-700 mb-4")

                            d351 = res_data.get("35.1") or {}
                            raw_val_351 = d351.get("valor") or []
                            if not isinstance(raw_val_351, list):
                                raw_val_351 = []

                            state_351 = {
                                "ato_formal": "Instituído por ato formal no organograma da secretaria de saúde ou equivalente" in raw_val_351,
                                "estrutura_fisica": "Possui estrutura física" in raw_val_351,
                                "equipe_med_enf": "Possui equipe com ao menos um médico e um enfermeiro" in raw_val_351,
                                "outros": "Outros" in raw_val_351,
                                "link": str(d351.get("link") or "")
                            }

                            lbl_pontos_351 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                            def calc_pontos_351():
                                p = 0.0
                                if state_351["ato_formal"]:
                                    p += 3.0
                                if state_351["estrutura_fisica"]:
                                    p += 2.0
                                if state_351["equipe_med_enf"]:
                                    p += 10.0
                                return p

                            def atualizar_pontos_351():
                                pts = calc_pontos_351()
                                lbl_pontos_351.set_text(f"Pontuação Calculada: {pts:.1f} / 15.0 pontos")

                            cb_ato35 = ui.checkbox("Instituído por ato formal no organograma da secretaria ou equivalente (3,0 pontos)", value=state_351["ato_formal"]).bind_value(state_351, "ato_formal")
                            cb_est35 = ui.checkbox("Possui estrutura física (2,0 pontos)", value=state_351["estrutura_fisica"]).bind_value(state_351, "estrutura_fisica")
                            cb_eqp35 = ui.checkbox("Possui equipe com ao menos um médico e um enfermeiro (10,0 pontos)", value=state_351["equipe_med_enf"]).bind_value(state_351, "equipe_med_enf")
                            cb_out35 = ui.checkbox("Outros (0,0 ponto)", value=state_351["outros"]).bind_value(state_351, "outros")

                            cb_ato35.on("change", lambda: atualizar_pontos_351())
                            cb_est35.on("change", lambda: atualizar_pontos_351())
                            cb_eqp35.on("change", lambda: atualizar_pontos_351())
                            cb_out35.on("change", lambda: atualizar_pontos_351())

                            ui.textarea(label="Link / Decreto de Organograma / Portaria de Nomeação dos Auditores:", value=state_351["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_351, "link")

                            atualizar_pontos_351()

                            def salvar_351():
                                sel_list = []
                                if state_351["ato_formal"]:
                                    sel_list.append("Instituído por ato formal no organograma da secretaria de saúde ou equivalente")
                                if state_351["estrutura_fisica"]:
                                    sel_list.append("Possui estrutura física")
                                if state_351["equipe_med_enf"]:
                                    sel_list.append("Possui equipe com ao menos um médico e um enfermeiro")
                                if state_351["outros"]:
                                    sel_list.append("Outros")

                                pts = calc_pontos_351()
                                save_resposta(
                                    ano=ano_sel,
                                    qid="35.1",
                                    valor=sel_list,
                                    pontos=pts,
                                    link=state_351["link"],
                                    comentarios=d351.get("comentarios", []),
                                    status=d351.get("status", "Pendente")
                                )
                                ui.notify("Quesito 35.1 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 35.1", on_click=salvar_351).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("35.1", res_data, render_conteudo.refresh)

                        # -------------------------------------------------------------------------
                        # QUESITO 35.2 (Publicação das Auditorias Concluídas de 2025)
                        # -------------------------------------------------------------------------
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("35.2 • Divulgação dos Relatórios de Auditoria de 2025").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("As auditorias concluídas (encerradas) do exercício de 2025 pelo componente municipal do Sistema Nacional de Auditoria do SUS - SNA estão disponibilizadas em site para consulta?").classes("text-sm text-gray-700 mb-4")

                            d352 = res_data.get("35.2") or {}
                            state_352 = {
                                "opcao": d352.get("valor") if isinstance(d352.get("valor"), str) else "Não",
                                "link": str(d352.get("link") or "")
                            }

                            lbl_pontos_352 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                            def calc_pontos_352(v):
                                return 10.0 if v == "Sim" else 0.0

                            def atualizar_pontos_352():
                                pts = calc_pontos_352(state_352["opcao"])
                                lbl_pontos_352.set_text(f"Pontuação Calculada: {pts:.1f} / 10.0 pontos")

                            radio_352 = ui.radio(["Sim", "Não"], value=state_352["opcao"]).classes("mb-3").bind_value(state_352, "opcao")
                            radio_352.on("update:model-value", lambda: atualizar_pontos_352())

                            ui.textarea(label="Link / Portal do SNA com Relatórios de Auditoria publicados:", value=state_352["link"]).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_352, "link")

                            atualizar_pontos_352()

                            def salvar_352():
                                pts = calc_pontos_352(state_352["opcao"])
                                save_resposta(
                                    ano=ano_sel,
                                    qid="35.2",
                                    valor=state_352["opcao"],
                                    pontos=pts,
                                    link=state_352["link"],
                                    comentarios=d352.get("comentarios", []),
                                    status=d352.get("status", "Pendente")
                                )
                                ui.notify("Quesito 35.2 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 35.2", on_click=salvar_352).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("35.2", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE AUDITORIA E SISTEMA DE MEDICAMENTOS - QUESITOS 35.2.1 A 36.1
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 35.2.1 (Página Eletrônica das Auditorias Concluídas em 2025)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                        ui.label("35.2.1 • Divulgação das Auditorias de 2025").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Informe a página eletrônica (site) de divulgação dos resultados das auditorias concluídas (encerradas) em 2025:").classes("text-sm text-gray-700 mb-4")

                        d3521 = res_data.get("35.2.1") or {}
                        state_3521 = {
                            "link": str(d3521.get("link") or d3521.get("valor") or "")
                        }

                        ui.textarea(
                            label="URL / Página eletrônica dos resultados das auditorias (2025):", 
                            value=state_3521["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_3521, "link")

                        def salvar_3521():
                            save_resposta(
                                ano=ano_sel,
                                qid="35.2.1",
                                valor=state_3521["link"],
                                pontos=0.0,
                                link=state_3521["link"],
                                comentarios=d3521.get("comentarios", []),
                                status=d3521.get("status", "Pendente")
                            )
                            ui.notify("Quesito 35.2.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 35.2.1", on_click=salvar_3521).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("35.2.1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 36.0 (Sistema Informatizado de Estoque de Medicamentos)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("36.0 • Gestão do Estoque de Medicamentos").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("O município utiliza sistema informatizado para gerenciar o estoque de itens de medicamentos?").classes("text-sm text-gray-700 mb-4")

                        d360 = res_data.get("36.0") or {}
                        OPCOES_360 = [
                            "Sim, utiliza o Sistema Hórus",
                            "Sim, utiliza Sistema Próprio",
                            "Não"
                        ]
                        
                        val_inicial_360 = d360.get("valor") if d360.get("valor") in OPCOES_360 else "Não"

                        state_360 = {
                            "opcao": val_inicial_360,
                            "link": str(d360.get("link") or "")
                        }

                        lbl_pontos_360 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                        def calc_pontos_360(v):
                            if v == "Sim, utiliza o Sistema Hórus":
                                return 40.0
                            return 0.0

                        def atualizar_pontos_360():
                            pts = calc_pontos_360(state_360["opcao"])
                            lbl_pontos_360.set_text(f"Pontuação Calculada: {pts:.1f} / 40.0 pontos")

                        radio_360 = ui.radio(OPCOES_360, value=state_360["opcao"]).classes("mb-3").bind_value(state_360, "opcao")
                        radio_360.on("update:model-value", lambda: atualizar_pontos_360())

                        ui.textarea(
                            label="Link / Comprovação da utilização do sistema:", 
                            value=state_360["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_360, "link")

                        atualizar_pontos_360()

                        def salvar_360():
                            pts = calc_pontos_360(state_360["opcao"])
                            save_resposta(
                                ano=ano_sel,
                                qid="36.0",
                                valor=state_360["opcao"],
                                pontos=pts,
                                link=state_360["link"],
                                comentarios=d360.get("comentarios", []),
                                status=d360.get("status", "Pendente")
                            )
                            ui.notify("Quesito 36.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 36.0", on_click=salvar_360).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("36.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 36.1 (Funções do Sistema Próprio de Gestão de Estoque de Medicamentos)
                    # Exibido/Pontuado principalmente quando utilizado Sistema Próprio em 36.0
                    # -----------------------------------------------------------------------------
                    if state_360["opcao"] == "Sim, utiliza Sistema Próprio":
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("36.1 • Funcionalidades do Sistema Próprio de Medicamentos").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Assinale as funções existentes no sistema próprio de gestão de estoque de medicamentos:").classes("text-sm text-gray-700 mb-4")

                            d361 = res_data.get("36.1") or {}
                            raw_val_361 = d361.get("valor") or []
                            if not isinstance(raw_val_361, list):
                                raw_val_361 = []

                            state_361 = {
                                "posicao_estoque": "Fornecer a posição de estoque, movimentação de entrada e saída, lote e validade" in raw_val_361,
                                "rastreabilidade": "Permitir a rastreabilidade dos medicamentos dispensados aos pacientes" in raw_val_361,
                                "compras": "Gerenciar o processo de compras de itens de medicamentos, desde o planejamento até a entrega e o recebimento da nota fiscal" in raw_val_361,
                                "reposicao": "Gerenciar a reposição de itens de medicamentos por estabelecimento de saúde" in raw_val_361,
                                "bnafar": "Integrado à Base Nacional de Dados de Ações e Serviços da Assistência Farmacêutica (BNAFAR)" in raw_val_361,
                                "outros": "Outros" in raw_val_361,
                                "link": str(d361.get("link") or "")
                            }

                            lbl_pontos_361 = ui.label("").classes("text-sm font-bold text-green-600 my-2")

                            def calc_pontos_361():
                                p = 0.0
                                if state_361["posicao_estoque"]:
                                    p += 10.0
                                if state_361["rastreabilidade"]:
                                    p += 10.0
                                if state_361["compras"]:
                                    p += 10.0
                                if state_361["reposicao"]:
                                    p += 10.0
                                # bnafar e outros valem 0.0 pontos
                                return p

                            def atualizar_pontos_361():
                                pts = calc_pontos_361()
                                lbl_pontos_361.set_text(f"Pontuação Calculada: {pts:.1f} / 40.0 pontos")

                            cb1 = ui.checkbox(
                                "Fornecer a posição de estoque, movimentação de entrada e saída, lote e validade (10,0 pts)", 
                                value=state_361["posicao_estoque"]
                            ).bind_value(state_361, "posicao_estoque")
                            
                            cb2 = ui.checkbox(
                                "Permitir a rastreabilidade dos medicamentos dispensados aos pacientes (10,0 pts)", 
                                value=state_361["rastreabilidade"]
                            ).bind_value(state_361, "rastreabilidade")
                            
                            cb3 = ui.checkbox(
                                "Gerenciar o processo de compras de itens de medicamentos, desde o planejamento até a entrega e o recebimento da nota fiscal (10,0 pts)", 
                                value=state_361["compras"]
                            ).bind_value(state_361, "compras")
                            
                            cb4 = ui.checkbox(
                                "Gerenciar a reposição de itens de medicamentos por estabelecimento de saúde (10,0 pts)", 
                                value=state_361["reposicao"]
                            ).bind_value(state_361, "reposicao")
                            
                            cb5 = ui.checkbox(
                                "Integrado à Base Nacional de Dados de Ações e Serviços da Assistência Farmacêutica (BNAFAR) (0,0 pts)", 
                                value=state_361["bnafar"]
                            ).bind_value(state_361, "bnafar")
                            
                            cb6 = ui.checkbox(
                                "Outros (0,0 pts)", 
                                value=state_361["outros"]
                            ).bind_value(state_361, "outros")

                            cb1.on("change", lambda: atualizar_pontos_361())
                            cb2.on("change", lambda: atualizar_pontos_361())
                            cb3.on("change", lambda: atualizar_pontos_361())
                            cb4.on("change", lambda: atualizar_pontos_361())
                            cb5.on("change", lambda: atualizar_pontos_361())
                            cb6.on("change", lambda: atualizar_pontos_361())

                            ui.textarea(
                                label="Link / Documentação técnica do sistema próprio:", 
                                value=state_361["link"]
                            ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_361, "link")

                            atualizar_pontos_361()

                            def salvar_361():
                                sel_list = []
                                if state_361["posicao_estoque"]:
                                    sel_list.append("Fornecer a posição de estoque, movimentação de entrada e saída, lote e validade")
                                if state_361["rastreabilidade"]:
                                    sel_list.append("Permitir a rastreabilidade dos medicamentos dispensados aos pacientes")
                                if state_361["compras"]:
                                    sel_list.append("Gerenciar o processo de compras de itens de medicamentos, desde o planejamento até a entrega e o recebimento da nota fiscal")
                                if state_361["reposicao"]:
                                    sel_list.append("Gerenciar a reposição de itens de medicamentos por estabelecimento de saúde")
                                if state_361["bnafar"]:
                                    sel_list.append("Integrado à Base Nacional de Dados de Ações e Serviços da Assistência Farmacêutica (BNAFAR)")
                                if state_361["outros"]:
                                    sel_list.append("Outros")

                                pts = calc_pontos_361()
                                save_resposta(
                                    ano=ano_sel,
                                    qid="36.1",
                                    valor=sel_list,
                                    pontos=pts,
                                    link=state_361["link"],
                                    comentarios=d361.get("comentarios", []),
                                    status=d361.get("status", "Pendente")
                                )
                                ui.notify("Quesito 36.1 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 36.1", on_click=salvar_361).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("36.1", res_data, render_conteudo.refresh)

# =============================================================================
                    # QUESITO 37.0 - DESABASTECIMENTO DE MEDICAMENTOS DA ASSISTÊNCIA FARMACÊUTICA
                    # =============================================================================
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("37.0 • Índice de Desabastecimento do Componente Básico da Assistência Farmacêutica").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o número de medicamentos da REMUME em falta por mais de 1 mês em 2025 (MD) "
                            "e o total de medicamentos cadastrados na REMUME (TM) para cálculo da proporção (Pd = MD / TM):"
                        ).classes("text-sm text-gray-700 mb-4")

                        d370 = res_data.get("37.0") or {}
                        val_370 = d370.get("valor") if isinstance(d370.get("valor"), dict) else {}

                        state_370 = {
                            "md": float(val_370.get("md", 0.0)),
                            "tm": float(val_370.get("tm", 0.0)),
                            "link": str(d370.get("link") or "")
                        }

                        lbl_pd = ui.label("").classes("text-base font-semibold text-blue-800 mb-1")
                        lbl_pontos_370 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_pontos_370(md, tm):
                            if tm <= 0:
                                return 0.0, 0.0  # Evita divisão por zero
                            
                            pd = (md / tm) * 100.0  # Em porcentagem

                            if pd == 0:
                                pts = 90.0
                            elif 0 < pd <= 5.0:
                                pts = 75.0
                            elif 5.0 < pd <= 10.0:
                                pts = 50.0
                            elif 10.0 < pd <= 15.0:
                                pts = 25.0
                            else:  # pd > 15%
                                pts = 0.0

                            return pd, pts

                        def atualizar_calculo_370():
                            md_val = float(inp_md.value or 0)
                            tm_val = float(inp_tm.value or 0)
                            
                            pd, pts = calc_pontos_370(md_val, tm_val)

                            if tm_val > 0:
                                lbl_pd.set_text(f"Proporção de Desabastecimento (Pd): {pd:.2f}% (MD: {int(md_val)} / TM: {int(tm_val)})")
                            else:
                                lbl_pd.set_text("Proporção de Desabastecimento (Pd): Infórme o Total de Medicamentos (TM)")

                            lbl_pontos_370.set_text(f"Pontuação Calculada: {pts:.1f} / 90.0 pontos")

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            inp_md = ui.number(
                                label="Nº de itens com desabastecimento > 1 mês em 2025 (MD):",
                                value=state_370["md"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_tm = ui.number(
                                label="Total de itens do Componente Básico na REMUME (TM):",
                                value=state_370["tm"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        inp_md.on("update:model-value", lambda: atualizar_calculo_370())
                        inp_tm.on("update:model-value", lambda: atualizar_calculo_370())

                        ui.textarea(
                            label="Link / Comprovação do controle de estoque e lista REMUME:",
                            value=state_370["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_370, "link")

                        atualizar_calculo_370()

                        def salvar_370():
                            md_val = float(inp_md.value or 0)
                            tm_val = float(inp_tm.value or 0)
                            pd, pts = calc_pontos_370(md_val, tm_val)

                            dados_finais = {
                                "md": md_val,
                                "tm": tm_val,
                                "pd": round(pd, 4)
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="37.0",
                                valor=dados_finais,
                                pontos=pts,
                                link=state_370["link"],
                                comentarios=d370.get("comentarios", []),
                                status=d370.get("status", "Pendente")
                            )
                            ui.notify("Quesito 37.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 37.0", on_click=salvar_370).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("37.0", res_data, render_conteudo.refresh)

    # =============================================================================
                    # MÓDULO DE TELEMEDICINA E COMENTÁRIOS FINAIS - QUESITOS 38.0 A 39.0
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO 38.0 (Disponibilização do Serviço de Telemedicina)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("38.0 • Disponibilização de Serviços de Telemedicina").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label("Houve a disponibilização do serviço de telemedicina em 2025?").classes("text-sm text-gray-700 mb-4")

                        d380 = res_data.get("38.0") or {}
                        state_380 = {
                            "opcao": d380.get("valor") if isinstance(d380.get("valor"), str) else "Não",
                            "link": str(d380.get("link") or "")
                        }

                        radio_380 = ui.radio(["Sim", "Não"], value=state_380["opcao"]).classes("mb-3").bind_value(state_380, "opcao")

                        ui.textarea(
                            label="Link / Comprovação da oferta de telemedicina:",
                            value=state_380["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_380, "link")

                        def salvar_380():
                            save_resposta(
                                ano=ano_sel,
                                qid="38.0",
                                valor=state_380["opcao"],
                                pontos=0.0,
                                link=state_380["link"],
                                comentarios=d380.get("comentarios", []),
                                status=d380.get("status", "Pendente")
                            )
                            ui.notify("Quesito 38.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 38.0", on_click=salvar_380).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("38.0", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # BLOCO TELEMEDICINA (38.1 a 38.3) - Exibido apenas se 38.0 == "Sim"
                    # -----------------------------------------------------------------------------
                    if state_380["opcao"] == "Sim":

                        # -------------------------------------------------------------------------
                        # QUESITO 38.1 (Serviços Disponibilizados de Telemedicina)
                        # -------------------------------------------------------------------------
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("38.1 • Serviços Disponibilizados").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Assinale os serviços disponibilizados:").classes("text-sm text-gray-700 mb-4")

                            d381 = res_data.get("38.1") or {}
                            raw_val_381 = d381.get("valor") or []
                            if not isinstance(raw_val_381, list):
                                raw_val_381 = []

                            state_381 = {
                                "teleconsulta": "Teleconsulta" in raw_val_381,
                                "teleinterconsulta": "Teleinterconsulta" in raw_val_381,
                                "telediagnostico": "Telediagnóstico" in raw_val_381,
                                "teletriagem": "Teletriagem" in raw_val_381,
                                "telemonitoramento": "Telemonitoramento" in raw_val_381,
                                "teleconsultoria": "Teleconsultoria" in raw_val_381,
                                "outros": "Outros" in raw_val_381,
                                "link": str(d381.get("link") or "")
                            }

                            ui.checkbox("Teleconsulta", value=state_381["teleconsulta"]).bind_value(state_381, "teleconsulta")
                            ui.checkbox("Teleinterconsulta", value=state_381["teleinterconsulta"]).bind_value(state_381, "teleinterconsulta")
                            ui.checkbox("Telediagnóstico", value=state_381["telediagnostico"]).bind_value(state_381, "telediagnostico")
                            ui.checkbox("Teletriagem", value=state_381["teletriagem"]).bind_value(state_381, "teletriagem")
                            ui.checkbox("Telemonitoramento", value=state_381["telemonitoramento"]).bind_value(state_381, "telemonitoramento")
                            ui.checkbox("Teleconsultoria", value=state_381["teleconsultoria"]).bind_value(state_381, "teleconsultoria")
                            ui.checkbox("Outros", value=state_381["outros"]).bind_value(state_381, "outros")

                            ui.textarea(
                                label="Link / Comprovação dos serviços de telemedicina:",
                                value=state_381["link"]
                            ).classes("w-full mb-3 mt-3").props("outlined dense rows=2").bind_value(state_381, "link")

                            def salvar_381():
                                sel_list = []
                                if state_381["teleconsulta"]: sel_list.append("Teleconsulta")
                                if state_381["teleinterconsulta"]: sel_list.append("Teleinterconsulta")
                                if state_381["telediagnostico"]: sel_list.append("Telediagnóstico")
                                if state_381["teletriagem"]: sel_list.append("Teletriagem")
                                if state_381["telemonitoramento"]: sel_list.append("Telemonitoramento")
                                if state_381["teleconsultoria"]: sel_list.append("Teleconsultoria")
                                if state_381["outros"]: sel_list.append("Outros")

                                save_resposta(
                                    ano=ano_sel,
                                    qid="38.1",
                                    valor=sel_list,
                                    pontos=0.0,
                                    link=state_381["link"],
                                    comentarios=d381.get("comentarios", []),
                                    status=d381.get("status", "Pendente")
                                )
                                ui.notify("Quesito 38.1 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 38.1", on_click=salvar_381).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("38.1", res_data, render_conteudo.refresh)

                        # -------------------------------------------------------------------------
                        # QUESITO 38.2 (Sistema Informatizado para Prescrição Eletrônica)
                        # -------------------------------------------------------------------------
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("38.2 • Prescrição e Atestados Eletrônicos").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Foi utilizado sistema informatizado para prescrição eletrônica, que possibilitasse a emissão de receitas e atestados, assinados eletronicamente?").classes("text-sm text-gray-700 mb-4")

                            d382 = res_data.get("38.2") or {}
                            state_382 = {
                                "opcao": d382.get("valor") if isinstance(d382.get("valor"), str) else "Não",
                                "link": str(d382.get("link") or "")
                            }

                            radio_382 = ui.radio(["Sim", "Não"], value=state_382["opcao"]).classes("mb-3").bind_value(state_382, "opcao")

                            ui.textarea(
                                label="Link / Comprovação do sistema de prescrição eletrônica:",
                                value=state_382["link"]
                            ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_382, "link")

                            def salvar_382():
                                save_resposta(
                                    ano=ano_sel,
                                    qid="38.2",
                                    valor=state_382["opcao"],
                                    pontos=0.0,
                                    link=state_382["link"],
                                    comentarios=d382.get("comentarios", []),
                                    status=d382.get("status", "Pendente")
                                )
                                ui.notify("Quesito 38.2 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 38.2", on_click=salvar_382).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("38.2", res_data, render_conteudo.refresh)

                        # -------------------------------------------------------------------------
                        # QUESITO 38.2.1 (Ferramentas de Prescrição Utilizadas) - Se 38.2 == Sim
                        # -------------------------------------------------------------------------
                        if state_382["opcao"] == "Sim":
                            with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-8"):
                                ui.label("38.2.1 • Ferramenta Utilizada para Prescrição e Assinatura Eletrônica").classes("text-xl font-semibold text-blue-600 mb-2")
                                ui.label("Assinale a ferramenta utilizada para prescrição e assinatura eletrônica:").classes("text-sm text-gray-700 mb-4")

                                d3821 = res_data.get("38.2.1") or {}
                                raw_val_3821 = d3821.get("valor") or []
                                if not isinstance(raw_val_3821, list):
                                    raw_val_3821 = []

                                state_3821 = {
                                    "ms": "Consultório Virtual da Família do Ministério da Saúde" in raw_val_3821,
                                    "cfm": "Prescrição Eletrônica do Conselho Federal de Medicina" in raw_val_3821,
                                    "outras": "Outras" in raw_val_3821,
                                    "link": str(d3821.get("link") or "")
                                }

                                ui.checkbox("Consultório Virtual da Família do Ministério da Saúde", value=state_3821["ms"]).bind_value(state_3821, "ms")
                                ui.checkbox("Prescrição Eletrônica do Conselho Federal de Medicina", value=state_3821["cfm"]).bind_value(state_3821, "cfm")
                                ui.checkbox("Outras", value=state_3821["outras"]).bind_value(state_3821, "outras")

                                ui.textarea(
                                    label="Link / Especificação da plataforma utilizada:",
                                    value=state_3821["link"]
                                ).classes("w-full mb-3 mt-3").props("outlined dense rows=2").bind_value(state_3821, "link")

                                def salvar_3821():
                                    sel_list = []
                                    if state_3821["ms"]: sel_list.append("Consultório Virtual da Família do Ministério da Saúde")
                                    if state_3821["cfm"]: sel_list.append("Prescrição Eletrônica do Conselho Federal de Medicina")
                                    if state_3821["outras"]: sel_list.append("Outras")

                                    save_resposta(
                                        ano=ano_sel,
                                        qid="38.2.1",
                                        valor=sel_list,
                                        pontos=0.0,
                                        link=state_3821["link"],
                                        comentarios=d3821.get("comentarios", []),
                                        status=d3821.get("status", "Pendente")
                                    )
                                    ui.notify("Quesito 38.2.1 salvo com sucesso!", type="positive")
                                    if render_conteudo.refresh:
                                        render_conteudo.refresh()

                                ui.button("💾 SALVAR QUESITO 38.2.1", on_click=salvar_3821).classes("bg-blue-600 text-white font-bold my-2")
                                ui.separator().classes("my-2")
                                bloco_comentarios("38.2.1", res_data, render_conteudo.refresh)

                        # -------------------------------------------------------------------------
                        # QUESITO 38.3 (Modalidades de Consultas e Registros Realizados)
                        # -------------------------------------------------------------------------
                        with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white ml-4"):
                            ui.label("38.3 • Modalidades de Consultas e Registros de Telemedicina").classes("text-xl font-semibold text-blue-600 mb-2")
                            ui.label("Assinale as modalidades de consultas e registros realizados referentes aos serviços de telemedicina:").classes("text-sm text-gray-700 mb-4")

                            d383 = res_data.get("38.3") or {}
                            raw_val_383 = d383.get("valor") or []
                            if not isinstance(raw_val_383, list):
                                raw_val_383 = []

                            state_383 = {
                                "iniciais": "Consultas iniciais (primeiro atendimento)" in raw_val_383,
                                "acompanhamento": "Consultas de acompanhamento/monitoramento" in raw_val_383,
                                "urgencia": "Consultas em caráter de urgência" in raw_val_383,
                                "supervisao": "Consultas de supervisão. Ex.: troca de experiências entre profissionais da saúde" in raw_val_383,
                                "pec": "Prontuário Eletrônico do Cidadão (PEC)" in raw_val_383,
                                "cds": "Fichas de Coletas de Dados Simplificados (CDS)" in raw_val_383,
                                "outros": "Outros" in raw_val_383,
                                "sem_registro": "Não houve registro" in raw_val_383,
                                "link": str(d383.get("link") or "")
                            }

                            ui.checkbox("Consultas iniciais (primeiro atendimento)", value=state_383["iniciais"]).bind_value(state_383, "iniciais")
                            ui.checkbox("Consultas de acompanhamento/monitoramento", value=state_383["acompanhamento"]).bind_value(state_383, "acompanhamento")
                            ui.checkbox("Consultas em caráter de urgência", value=state_383["urgencia"]).bind_value(state_383, "urgencia")
                            ui.checkbox("Consultas de supervisão. Ex.: troca de experiências entre profissionais da saúde", value=state_383["supervisao"]).bind_value(state_383, "supervisao")
                            ui.checkbox("Prontuário Eletrônico do Cidadão (PEC)", value=state_383["pec"]).bind_value(state_383, "pec")
                            ui.checkbox("Fichas de Coletas de Dados Simplificados (CDS)", value=state_383["cds"]).bind_value(state_383, "cds")
                            ui.checkbox("Outros", value=state_383["outros"]).bind_value(state_383, "outros")
                            ui.checkbox("Não houve registro", value=state_383["sem_registro"]).bind_value(state_383, "sem_registro")

                            ui.textarea(
                                label="Link / Comprovação dos registros de telemedicina:",
                                value=state_383["link"]
                            ).classes("w-full mb-3 mt-3").props("outlined dense rows=2").bind_value(state_383, "link")

                            def salvar_383():
                                sel_list = []
                                if state_383["iniciais"]: sel_list.append("Consultas iniciais (primeiro atendimento)")
                                if state_383["acompanhamento"]: sel_list.append("Consultas de acompanhamento/monitoramento")
                                if state_383["urgencia"]: sel_list.append("Consultas em caráter de urgência")
                                if state_383["supervisao"]: sel_list.append("Consultas de supervisão. Ex.: troca de experiências entre profissionais da saúde")
                                if state_383["pec"]: sel_list.append("Prontuário Eletrônico do Cidadão (PEC)")
                                if state_383["cds"]: sel_list.append("Fichas de Coletas de Dados Simplificados (CDS)")
                                if state_383["outros"]: sel_list.append("Outros")
                                if state_383["sem_registro"]: sel_list.append("Não houve registro")

                                save_resposta(
                                    ano=ano_sel,
                                    qid="38.3",
                                    valor=sel_list,
                                    pontos=0.0,
                                    link=state_383["link"],
                                    comentarios=d383.get("comentarios", []),
                                    status=d383.get("status", "Pendente")
                                )
                                ui.notify("Quesito 38.3 salvo com sucesso!", type="positive")
                                if render_conteudo.refresh:
                                    render_conteudo.refresh()

                            ui.button("💾 SALVAR QUESITO 38.3", on_click=salvar_383).classes("bg-blue-600 text-white font-bold my-2")
                            ui.separator().classes("my-2")
                            bloco_comentarios("38.3", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO 39.0 (Espaço para Impressões, Comentários e Sugestões do Questionário)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("39.0 • Impressões, Comentários e Sugestões").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Gostaria de registrar suas impressões, comentários e sugestões a respeito do presente questionário?\n"
                            "Utilize o espaço abaixo para registrar suas impressões, comentários e sugestões a respeito do presente questionário."
                        ).classes("text-sm text-gray-700 mb-4 whitespace-pre-line")

                        d390 = res_data.get("39.0") or {}
                        state_390 = {
                            "texto": str(d390.get("valor") or "")
                        }

                        ui.textarea(
                            label="Comentários e sugestões sobre o questionário:",
                            value=state_390["texto"]
                        ).classes("w-full mb-3").props("outlined dense rows=5").bind_value(state_390, "texto")

                        def salvar_390():
                            save_resposta(
                                ano=ano_sel,
                                qid="39.0",
                                valor=state_390["texto"],
                                pontos=0.0,
                                link="",
                                comentarios=d390.get("comentarios", []),
                                status=d390.get("status", "Pendente")
                            )
                            ui.notify("Quesito 39.0 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO 39.0", on_click=salvar_390).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("39.0", res_data, render_conteudo.refresh)

    # =============================================================================
                    # MÓDULO DE INDICADORES SUPLEMENTARES E SISTÊMICOS - QUESITOS S1 E S2
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO S1 (Mínimo Constitucional em Saúde - Art. 198 CF / LC 141)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S1 • Aplicação do Mínimo Constitucional em Saúde").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe as despesas aplicadas em saúde com recursos próprios e a receita total de impostos. "
                            "A proporção mínima exigida por lei é de 15%."
                        ).classes("text-sm text-gray-700 mb-4")

                        ds1 = res_data.get("S1") or {}
                        val_s1 = ds1.get("valor") if isinstance(ds1.get("valor"), dict) else {}

                        state_s1 = {
                            "despesa": float(val_s1.get("despesa", 0.0)),
                            "receita": float(val_s1.get("receita", 0.0)),
                            "link": str(ds1.get("link") or "")
                        }

                        lbl_ps = ui.label("").classes("text-base font-semibold text-blue-800 mb-1")
                        lbl_resultado_s1 = ui.label("").classes("text-base font-bold mb-4")

                        def calc_s1(despesa, receita):
                            if receita <= 0:
                                return 0.0, False
                            
                            ps = (despesa / receita) * 100.0
                            atingiu_minimo = ps >= 15.0
                            return ps, atingiu_minimo

                        def atualizar_calculo_s1():
                            desp_val = float(inp_despesa.value or 0)
                            rec_val = float(inp_receita.value or 0)
                            
                            ps, atingiu = calc_s1(desp_val, rec_val)

                            if rec_val > 0:
                                lbl_ps.set_text(f"Proporção Aplicada em Saúde (PS): {ps:.2f}%")
                                if atingiu:
                                    lbl_resultado_s1.set_text("Status: Atingiu o mínimo constitucional (≥ 15%) — Sem rebaixamento de faixa (0,0 pt)")
                                    lbl_resultado_s1.classes(remove="text-red-600", add="text-green-600")
                                else:
                                    lbl_resultado_s1.set_text("Status: NÃO atingiu o mínimo constitucional (< 15%) — REBAIXAR 1 FAIXA DO i-Saúde!")
                                    lbl_resultado_s1.classes(remove="text-green-600", add="text-red-600")
                            else:
                                lbl_ps.set_text("Proporção Aplicada em Saúde (PS): Informe a Receita de Impostos")
                                lbl_resultado_s1.set_text("")

                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            inp_despesa = ui.number(
                                label="Despesa aplicada em Saúde com recursos próprios (R$):",
                                value=state_s1["despesa"],
                                min=0,
                                format="%.2f"
                            ).classes("w-full").props("outlined dense")

                            inp_receita = ui.number(
                                label="Receita de Impostos (Saúde) (R$):",
                                value=state_s1["receita"],
                                min=0,
                                format="%.2f"
                            ).classes("w-full").props("outlined dense")

                        inp_despesa.on("update:model-value", lambda: atualizar_calculo_s1())
                        inp_receita.on("update:model-value", lambda: atualizar_calculo_s1())

                        ui.textarea(
                            label="Link / Comprovação SIOPS ou Prestação de Contas:",
                            value=state_s1["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s1, "link")

                        atualizar_calculo_s1()

                        def salvar_s1():
                            desp_val = float(inp_despesa.value or 0)
                            rec_val = float(inp_receita.value or 0)
                            ps, atingiu = calc_s1(desp_val, rec_val)

                            dados_finais = {
                                "despesa": desp_val,
                                "receita": rec_val,
                                "ps": round(ps, 4),
                                "rebaixar_faixa": not atingiu
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="S1",
                                valor=dados_finais,
                                pontos=0.0,
                                link=state_s1["link"],
                                comentarios=ds1.get("comentarios", []),
                                status=ds1.get("status", "Pendente")
                            )
                            ui.notify("Quesito S1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S1", on_click=salvar_s1).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S1", res_data, render_conteudo.refresh)

                    # -----------------------------------------------------------------------------
                    # QUESITO S2 (Consultas Médicas por Habitante - SIA/SUS e IBGE)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S2 • Evolução das Consultas Médicas per Capita (SIA/SUS e IBGE)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o total de consultas médicas e a população estimada dos exercícios de 2023, 2024 e 2025 "
                            "para comparar o indicador do ano atual (PAA) com a média do biênio anterior (MAA):"
                        ).classes("text-sm text-gray-700 mb-4")

                        ds2 = res_data.get("S2") or {}
                        val_s2 = ds2.get("valor") if isinstance(ds2.get("valor"), dict) else {}

                        state_s2 = {
                            "cmaa_2": float(val_s2.get("cmaa_2", 0.0)),
                            "cmaa_1": float(val_s2.get("cmaa_1", 0.0)),
                            "cmaa": float(val_s2.get("cmaa", 0.0)),
                            "pop_2": float(val_s2.get("pop_2", 0.0)),
                            "pop_1": float(val_s2.get("pop_1", 0.0)),
                            "pop": float(val_s2.get("pop", 0.0)),
                            "link": str(ds2.get("link") or "")
                        }

                        lbl_paa_maa = ui.label("").classes("text-base font-semibold text-blue-800 mb-1")
                        lbl_pontos_s2 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_s2(c2, c1, c, p2, p1, p):
                            paa = (c / p) if p > 0 else 0.0
                            soma_c_ant = c2 + c1
                            soma_p_ant = p2 + p1
                            maa = (soma_c_ant / soma_p_ant) if soma_p_ant > 0 else 0.0

                            # Se o valor per capita atual for igual ou maior que a média histórica
                            pts = 20.0 if (p > 0 and soma_p_ant > 0 and paa >= maa) else 0.0
                            return paa, maa, pts

                        def atualizar_calculo_s2():
                            c2 = float(inp_cmaa_2.value or 0)
                            c1 = float(inp_cmaa_1.value or 0)
                            c = float(inp_cmaa.value or 0)
                            p2 = float(inp_pop_2.value or 0)
                            p1 = float(inp_pop_1.value or 0)
                            p = float(inp_pop.value or 0)

                            paa, maa, pts = calc_s2(c2, c1, c, p2, p1, p)

                            if p > 0 and (p2 + p1) > 0:
                                lbl_paa_maa.set_text(f"PAA (2025): {paa:.4f} consultas/hab | MAA (Média 23-24): {maa:.4f} consultas/hab")
                            else:
                                lbl_paa_maa.set_text("Preencha as populações e consultas para calcular PAA e MAA")

                            lbl_pontos_s2.set_text(f"Pontuação Calculada: {pts:.1f} / 20.0 pontos")

                        ui.label("Consultas Médicas (SIA/SUS):").classes("font-semibold text-gray-800 mt-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            inp_cmaa_2 = ui.number(
                                label="Consultas em 2023 (CMAA-2):",
                                value=state_s2["cmaa_2"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_cmaa_1 = ui.number(
                                label="Consultas em 2024 (CMAA-1):",
                                value=state_s2["cmaa_1"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_cmaa = ui.number(
                                label="Consultas em 2025 (CMAA):",
                                value=state_s2["cmaa"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        ui.label("População Estimada (IBGE):").classes("font-semibold text-gray-800 mt-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            inp_pop_2 = ui.number(
                                label="População 2023 (PopAA-2):",
                                value=state_s2["pop_2"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_pop_1 = ui.number(
                                label="População 2024 (PopAA-1):",
                                value=state_s2["pop_1"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_pop = ui.number(
                                label="População 2025 (PopAA):",
                                value=state_s2["pop"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        inp_cmaa_2.on("update:model-value", lambda: atualizar_calculo_s2())
                        inp_cmaa_1.on("update:model-value", lambda: atualizar_calculo_s2())
                        inp_cmaa.on("update:model-value", lambda: atualizar_calculo_s2())
                        inp_pop_2.on("update:model-value", lambda: atualizar_calculo_s2())
                        inp_pop_1.on("update:model-value", lambda: atualizar_calculo_s2())
                        inp_pop.on("update:model-value", lambda: atualizar_calculo_s2())

                        ui.textarea(
                            label="Link / Comprovação dos dados do SIA/SUS e IBGE:",
                            value=state_s2["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s2, "link")

                        atualizar_calculo_s2()

                        def salvar_s2():
                            c2 = float(inp_cmaa_2.value or 0)
                            c1 = float(inp_cmaa_1.value or 0)
                            c = float(inp_cmaa.value or 0)
                            p2 = float(inp_pop_2.value or 0)
                            p1 = float(inp_pop_1.value or 0)
                            p = float(inp_pop.value or 0)

                            paa, maa, pts = calc_s2(c2, c1, c, p2, p1, p)

                            dados_finais = {
                                "cmaa_2": c2,
                                "cmaa_1": c1,
                                "cmaa": c,
                                "pop_2": p2,
                                "pop_1": p1,
                                "pop": p,
                                "paa": round(paa, 6),
                                "maa": round(maa, 6)
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="S2",
                                valor=dados_finais,
                                pontos=pts,
                                link=state_s2["link"],
                                comentarios=ds2.get("comentarios", []),
                                status=ds2.get("status", "Pendente")
                            )
                            ui.notify("Quesito S2 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S2", on_click=salvar_s2).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S2", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE INDICADORES SUPLEMENTARES - QUESITO S3 (SISAB - PRÉ-NATAL)
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO S3 (Acompanhamento de Gestantes / Pré-Natal - SISAB 2025)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S3 • Cobertura e Qualidade do Pré-Natal (SISAB)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o número de gestantes com pelo menos 6 consultas de pré-natal "
                            "(com a 1ª consulta até a 12ª semana) e o total de gestantes cadastradas por quadrimestre em 2025:"
                        ).classes("text-sm text-gray-700 mb-4")

                        ds3 = res_data.get("S3") or {}
                        val_s3 = ds3.get("valor") if isinstance(ds3.get("valor"), dict) else {}

                        state_s3 = {
                            "g1q": float(val_s3.get("g1q", 0.0)),
                            "g2q": float(val_s3.get("g2q", 0.0)),
                            "g3q": float(val_s3.get("g3q", 0.0)),
                            "tg1q": float(val_s3.get("tg1q", 0.0)),
                            "tg2q": float(val_s3.get("tg2q", 0.0)),
                            "tg3q": float(val_s3.get("tg3q", 0.0)),
                            "link": str(ds3.get("link") or "")
                        }

                        lbl_p_s3 = ui.label("").classes("text-base font-semibold text-blue-800 mb-1")
                        lbl_pontos_s3 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_s3(g1, g2, g3, tg1, tg2, tg3):
                            soma_g = g1 + g2 + g3
                            soma_tg = tg1 + tg2 + tg3

                            if soma_tg <= 0:
                                return 0.0, 0.0

                            p = (soma_g / soma_tg) * 100.0

                            if p >= 100.0:
                                pts = 25.0
                            elif p >= 45.0:
                                pts = 15.0
                            elif p >= 31.0:
                                pts = 10.0
                            elif p >= 18.0:
                                pts = 5.0
                            else:
                                pts = 0.0

                            return p, pts

                        def atualizar_calculo_s3():
                            g1 = float(inp_g1q.value or 0)
                            g2 = float(inp_g2q.value or 0)
                            g3 = float(inp_g3q.value or 0)
                            tg1 = float(inp_tg1q.value or 0)
                            tg2 = float(inp_tg2q.value or 0)
                            tg3 = float(inp_tg3q.value or 0)

                            p, pts = calc_s3(g1, g2, g3, tg1, tg2, tg3)

                            soma_tg = tg1 + tg2 + tg3
                            if soma_tg > 0:
                                lbl_p_s3.set_text(f"Proporção Alcançada (P): {p:.2f}%")
                            else:
                                lbl_p_s3.set_text("Proporção Alcançada (P): Informe os totais de gestantes")

                            lbl_pontos_s3.set_text(f"Pontuação Calculada: {pts:.1f} / 25.0 pontos")

                        ui.label("1. Gestantes com ≥ 6 Consultas (1ª até 12ª Semana):").classes("font-semibold text-gray-800 mt-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            inp_g1q = ui.number(
                                label="1º Quadrimestre 2025 (G1Q):",
                                value=state_s3["g1q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_g2q = ui.number(
                                label="2º Quadrimestre 2025 (G2Q):",
                                value=state_s3["g2q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_g3q = ui.number(
                                label="3º Quadrimestre 2025 (G3Q):",
                                value=state_s3["g3q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        ui.label("2. Total de Gestantes Registradas:").classes("font-semibold text-gray-800 mt-2")
                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            inp_tg1q = ui.number(
                                label="Total 1º Quadrimestre 2025 (TG1Q):",
                                value=state_s3["tg1q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_tg2q = ui.number(
                                label="Total 2º Quadrimestre 2025 (TG2Q):",
                                value=state_s3["tg2q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_tg3q = ui.number(
                                label="Total 3º Quadrimestre 2025 (TG3Q):",
                                value=state_s3["tg3q"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        inp_g1q.on("update:model-value", lambda: atualizar_calculo_s3())
                        inp_g2q.on("update:model-value", lambda: atualizar_calculo_s3())
                        inp_g3q.on("update:model-value", lambda: atualizar_calculo_s3())
                        inp_tg1q.on("update:model-value", lambda: atualizar_calculo_s3())
                        inp_tg2q.on("update:model-value", lambda: atualizar_calculo_s3())
                        inp_tg3q.on("update:model-value", lambda: atualizar_calculo_s3())

                        ui.textarea(
                            label="Link / Comprovação dos dados do SISAB:",
                            value=state_s3["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s3, "link")

                        atualizar_calculo_s3()

                        def salvar_s3():
                            g1 = float(inp_g1q.value or 0)
                            g2 = float(inp_g2q.value or 0)
                            g3 = float(inp_g3q.value or 0)
                            tg1 = float(inp_tg1q.value or 0)
                            tg2 = float(inp_tg2q.value or 0)
                            tg3 = float(inp_tg3q.value or 0)

                            p, pts = calc_s3(g1, g2, g3, tg1, tg2, tg3)

                            dados_finais = {
                                "g1q": g1,
                                "g2q": g2,
                                "g3q": g3,
                                "tg1q": tg1,
                                "tg2q": tg2,
                                "tg3q": tg3,
                                "p": round(p, 4)
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="S3",
                                valor=dados_finais,
                                pontos=pts,
                                link=state_s3["link"],
                                comentarios=ds3.get("comentarios", []),
                                status=ds3.get("status", "Pendente")
                            )
                            ui.notify("Quesito S3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S3", on_click=salvar_s3).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S3", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE INDICADORES SUPLEMENTARES - QUESITO S4 (TABWIN - EXAMES PRÉ-NATAL)
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO S4 (Exames de Sífilis e HIV no Pré-Natal - Dados TABWIN)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S4 • Exames de Pré-Natal Realizados (TABWIN)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o número de exames (teste não treponêmico para sífilis e teste rápido para HIV) "
                            "e a quantidade de gestantes com o primeiro atendimento de pré-natal (TG):"
                        ).classes("text-sm text-gray-700 mb-4")

                        ds4 = res_data.get("S4") or {}
                        val_s4 = ds4.get("valor") if isinstance(ds4.get("valor"), dict) else {}

                        state_s4 = {
                            "ts": float(val_s4.get("ts", 0.0)),
                            "tr": float(val_s4.get("tr", 0.0)),
                            "tg": float(val_s4.get("tg", 0.0)),
                            "link": str(ds4.get("link") or "")
                        }

                        lbl_razao_ts = ui.label("").classes("text-sm font-semibold text-gray-800 mb-1")
                        lbl_razao_tr = ui.label("").classes("text-sm font-semibold text-gray-800 mb-1")
                        lbl_pontos_s4 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_s4(ts, tr, tg):
                            if tg <= 0:
                                return 0.0, 0.0, 0.0

                            razao_ts = ts / tg
                            razao_tr = tr / tg

                            pts_ts = 10.0 if razao_ts >= 2.0 else 0.0
                            pts_tr = 10.0 if razao_tr >= 2.0 else 0.0

                            pts_total = pts_ts + pts_tr
                            return razao_ts, razao_tr, pts_total

                        def atualizar_calculo_s4():
                            ts_val = float(inp_ts.value or 0)
                            tr_val = float(inp_tr.value or 0)
                            tg_val = float(inp_tg.value or 0)

                            r_ts, r_tr, pts = calc_s4(ts_val, tr_val, tg_val)

                            if tg_val > 0:
                                lbl_razao_ts.set_text(
                                    f"• Razão Sífilis (TS / TG): {r_ts:.2f} "
                                    f"({'≥ 2.0 ➔ +10,0 pts' if r_ts >= 2.0 else '< 2.0 ➔ 0,0 pt'})"
                                )
                                lbl_razao_tr.set_text(
                                    f"• Razão HIV (TR / TG): {r_tr:.2f} "
                                    f"({'≥ 2.0 ➔ +10,0 pts' if r_tr >= 2.0 else '< 2.0 ➔ 0,0 pt'})"
                                )
                            else:
                                lbl_razao_ts.set_text("• Razão Sífilis (TS / TG): Informe o total de gestantes (TG)")
                                lbl_razao_tr.set_text("• Razão HIV (TR / TG): Informe o total de gestantes (TG)")

                            lbl_pontos_s4.set_text(f"Pontuação Calculada: {pts:.1f} / 20.0 pontos")

                        with ui.grid(columns=1).classes("w-full gap-4 mb-4"):
                            inp_ts = ui.number(
                                label="Nº de exames realizados - Teste não treponêmico p/ detecção de sífilis em gestantes (TS):",
                                value=state_s4["ts"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_tr = ui.number(
                                label="Nº de exames realizados - Teste rápido para detecção de HIV na gestante (TR):",
                                value=state_s4["tr"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_tg = ui.number(
                                label="Nº de Gestantes com o primeiro atendimento de pré-natal (TG):",
                                value=state_s4["tg"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        inp_ts.on("update:model-value", lambda: atualizar_calculo_s4())
                        inp_tr.on("update:model-value", lambda: atualizar_calculo_s4())
                        inp_tg.on("update:model-value", lambda: atualizar_calculo_s4())

                        ui.textarea(
                            label="Link / Comprovação dos dados extraídos do TABWIN:",
                            value=state_s4["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s4, "link")

                        atualizar_calculo_s4()

                        def salvar_s4():
                            ts_val = float(inp_ts.value or 0)
                            tr_val = float(inp_tr.value or 0)
                            tg_val = float(inp_tg.value or 0)

                            r_ts, r_tr, pts = calc_s4(ts_val, tr_val, tg_val)

                            dados_finais = {
                                "ts": ts_val,
                                "tr": tr_val,
                                "tg": tg_val,
                                "razao_ts": round(r_ts, 4),
                                "razao_tr": round(r_tr, 4)
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="S4",
                                valor=dados_finais,
                                pontos=pts,
                                link=state_s4["link"],
                                comentarios=ds4.get("comentarios", []),
                                status=ds4.get("status", "Pendente")
                            )
                            ui.notify("Quesito S4 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S4", on_click=salvar_s4).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S4", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE INDICADORES SUPLEMENTARES - QUESITO S5 (SIA/SUS - INSPEÇÕES SANITÁRIAS)
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO S5 (Inspeções Sanitárias - Vigilância Sanitária no SIA/SUS)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S5 • Número de Inspeções Sanitárias (SIA/SUS)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o número de inspeções sanitárias realizadas em 2023, 2024 e 2025. "
                            "O indicador avalia o desempenho de 2025 em relação à média do biênio anterior:"
                        ).classes("text-sm text-gray-700 mb-4")

                        ds5 = res_data.get("S5") or {}
                        val_s5 = ds5.get("valor") if isinstance(ds5.get("valor"), dict) else {}

                        state_s5 = {
                            "ni_2": float(val_s5.get("ni_2", 0.0)),
                            "ni_1": float(val_s5.get("ni_1", 0.0)),
                            "ni": float(val_s5.get("ni", 0.0)),
                            "link": str(ds5.get("link") or "")
                        }

                        lbl_comparativo_s5 = ui.label("").classes("text-base font-semibold text-blue-800 mb-1")
                        lbl_pontos_s5 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_s5(ni2, ni1, ni):
                            media_historica = (ni2 + ni1) / 2.0
                            pts = 10.0 if ni >= media_historica else 0.0
                            return media_historica, pts

                        def atualizar_calculo_s5():
                            ni2_val = float(inp_ni2.value or 0)
                            ni1_val = float(inp_ni1.value or 0)
                            ni_val = float(inp_ni.value or 0)

                            mni, pts = calc_s5(ni2_val, ni1_val, ni_val)

                            lbl_comparativo_s5.set_text(
                                f"• Média Biênio 2023-2024: {mni:.1f} inspeções | Realizado em 2025: {ni_val:.0f} inspeções"
                            )

                            if ni_val >= mni:
                                lbl_pontos_s5.classes(remove="text-red-600", add="text-green-600")
                                lbl_pontos_s5.set_text(f"Pontuação Calculada: {pts:.1f} / 10.0 pontos (Meta atingida ou superada)")
                            else:
                                lbl_pontos_s5.classes(remove="text-green-600", add="text-red-600")
                                lbl_pontos_s5.set_text(f"Pontuação Calculada: {pts:.1f} / 10.0 pontos (Abaixo da média dos anos anteriores)")

                        with ui.grid(columns=3).classes("w-full gap-4 mb-4"):
                            inp_ni2 = ui.number(
                                label="Nº Inspeções em 2023 (NI-2):",
                                value=state_s5["ni_2"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_ni1 = ui.number(
                                label="Nº Inspeções em 2024 (NI-1):",
                                value=state_s5["ni_1"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                            inp_ni = ui.number(
                                label="Nº Inspeções em 2025 (NI):",
                                value=state_s5["ni"],
                                min=0,
                                format="%.0f"
                            ).classes("w-full").props("outlined dense")

                        inp_ni2.on("update:model-value", lambda: atualizar_calculo_s5())
                        inp_ni1.on("update:model-value", lambda: atualizar_calculo_s5())
                        inp_ni.on("update:model-value", lambda: atualizar_calculo_s5())

                        ui.textarea(
                            label="Link / Comprovação dos relatórios do SIA/SUS:",
                            value=state_s5["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s5, "link")

                        atualizar_calculo_s5()

                        def salvar_s5():
                            ni2_val = float(inp_ni2.value or 0)
                            ni1_val = float(inp_ni1.value or 0)
                            ni_val = float(inp_ni.value or 0)

                            mni, pts = calc_s5(ni2_val, ni1_val, ni_val)

                            dados_finais = {
                                "ni_2": ni2_val,
                                "ni_1": ni1_val,
                                "ni": ni_val,
                                "media_historica": round(mni, 2)
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="S5",
                                valor=dados_finais,
                                pontos=pts,
                                link=state_s5["link"],
                                comentarios=ds5.get("comentarios", []),
                                status=ds5.get("status", "Pendente")
                            )
                            ui.notify("Quesito S5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S5", on_click=salvar_s5).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S5", res_data, render_conteudo.refresh)

# =============================================================================
                    # MÓDULO DE INDICADORES SUPLEMENTARES - QUESITO S6 (VACINAÇÃO - DATASUS/PNI)
                    # =============================================================================

                    # -----------------------------------------------------------------------------
                    # QUESITO S6 (Cobertura Vacinal do Calendário Nacional de Vacinação)
                    # -----------------------------------------------------------------------------
                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("S6 • Cobertura Vacinal (Calendário Nacional de Vacinação / PNI)").classes("text-xl font-semibold text-blue-600 mb-2")
                        ui.label(
                            "Informe o percentual (%) de cobertura para cada vacina conforme dados do TABNET/DATASUS. "
                            "O cálculo aplicará a pontuação integral para metas atingidas ou proporcional quando abaixo da meta."
                        ).classes("text-sm text-gray-700 mb-4")

                        ds6 = res_data.get("S6") or {}
                        val_s6 = ds6.get("valor") if isinstance(ds6.get("valor"), dict) else {}

                        # Configuração dos Imunizantes (Meta PNI e Pontuação Máxima)
                        VACINAS_CONFIG = [
                            {"id": "bcg", "nome": "BCG (Bacilo Calmette-Guérin)", "meta": 90.0, "p_max": 5.0},
                            {"id": "rotavirus", "nome": "Rotavírus Humano (2ª dose)", "meta": 90.0, "p_max": 5.0},
                            {"id": "hepatite_b", "nome": "Hepatite B (3ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "meningo_c", "nome": "Meningocócica C (conjugada - 2ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "penta", "nome": "Vacina Pentavalente (3ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "pneumo_10", "nome": "Vacina Pneumocócica 10-valente (2ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "polio", "nome": "Vacina Poliomielite (3ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "febre_amarela", "nome": "Febre Amarela", "meta": 95.0, "p_max": 10.0},
                            {"id": "triplice_viral", "nome": "Vacina Tríplice Viral (1ª dose)", "meta": 95.0, "p_max": 10.0},
                            {"id": "hepatite_a", "nome": "Hepatite A", "meta": 95.0, "p_max": 10.0},
                            {"id": "tetra_viral", "nome": "Tetra Viral", "meta": 95.0, "p_max": 10.0},
                        ]

                        state_s6 = {
                            v["id"]: float(val_s6.get(v["id"], 0.0)) for v in VACINAS_CONFIG
                        }
                        state_s6["link"] = str(ds6.get("link") or "")

                        inputs_vacinas = {}
                        labels_pts_vacinas = {}

                        lbl_pontos_s6 = ui.label("").classes("text-base font-bold text-green-600 mb-4")

                        def calc_s6(dados_cob):
                            total_pts = 0.0
                            detalhes = {}

                            for v in VACINAS_CONFIG:
                                vid = v["id"]
                                meta = v["meta"]
                                p_max = v["p_max"]
                                cob = float(dados_cob.get(vid, 0.0))

                                if cob >= meta:
                                    p_obtido = p_max
                                else:
                                    p_obtido = (cob / meta) * p_max if meta > 0 else 0.0

                                # Garantir que o valor não seja negativo e não ultrapasse o máximo
                                p_obtido = max(0.0, min(p_max, p_obtido))
                                total_pts += p_obtido
                                detalhes[vid] = {"cob": cob, "pontos": p_obtido}

                            return total_pts, detalhes

                        def atualizar_calculo_s6():
                            cob_atual = {v["id"]: float(inputs_vacinas[v["id"]].value or 0) for v in VACINAS_CONFIG}
                            total_pts, detalhes = calc_s6(cob_atual)

                            for v in VACINAS_CONFIG:
                                vid = v["id"]
                                pts = detalhes[vid]["pontos"]
                                meta = v["meta"]
                                p_max = v["p_max"]
                                labels_pts_vacinas[vid].set_text(
                                    f"Meta: {meta:.0f}% ➔ {pts:.2f} / {p_max:.0f} pts"
                                )

                            lbl_pontos_s6.set_text(f"Pontuação Total Calculada: {total_pts:.2f} / 100.0 pontos")

                        # Interface Dinâmica para Inserção de Cobertura Vacinal
                        with ui.grid(columns=2).classes("w-full gap-4 mb-4"):
                            for v in VACINAS_CONFIG:
                                vid = v["id"]
                                with ui.column().classes("w-full border p-3 rounded bg-gray-50"):
                                    ui.label(v["nome"]).classes("font-semibold text-gray-800 text-sm")
                                    
                                    with ui.row().classes("w-full items-center justify-between"):
                                        inp = ui.number(
                                            label="Cobertura (%):",
                                            value=state_s6[vid],
                                            min=0,
                                            max=100,
                                            format="%.2f"
                                        ).classes("w-1/2").props("outlined dense")
                                        
                                        lbl_p = ui.label("").classes("text-xs font-bold text-blue-700")
                                        
                                        inputs_vacinas[vid] = inp
                                        labels_pts_vacinas[vid] = lbl_p

                                        inp.on("update:model-value", lambda: atualizar_calculo_s6())

                        ui.textarea(
                            label="Link / Comprovação dos dados extraídos do TABNET/DATASUS:",
                            value=state_s6["link"]
                        ).classes("w-full mb-3").props("outlined dense rows=2").bind_value(state_s6, "link")

                        atualizar_calculo_s6()

                        def salvar_s6():
                            cob_atual = {v["id"]: float(inputs_vacinas[v["id"]].value or 0) for v in VACINAS_CONFIG}
                            total_pts, detalhes = calc_s6(cob_atual)

                            dados_finais = {v["id"]: cob_atual[v["id"]] for v in VACINAS_CONFIG}
                            dados_finais["detalhes_pontuacao"] = {v["id"]: round(detalhes[v["id"]]["pontos"], 4) for v in VACINAS_CONFIG}

                            save_resposta(
                                ano=ano_sel,
                                qid="S6",
                                valor=dados_finais,
                                pontos=total_pts,
                                link=state_s6["link"],
                                comentarios=ds6.get("comentarios", []),
                                status=ds6.get("status", "Pendente")
                            )
                            ui.notify("Quesito S6 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("💾 SALVAR QUESITO S6", on_click=salvar_s6).classes("bg-blue-600 text-white font-bold my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("S6", res_data, render_conteudo.refresh)

    # Executa a renderização inicial
    render_conteudo()


# Aliases para compatibilidade com o roteamento do main.py
container_formulario_isaude = container_formulario_saude
mostrar_formulario_saude = container_formulario_saude
main = container_formulario_saude
