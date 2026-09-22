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

    # Executa a renderização inicial
    render_conteudo()


# Aliases para compatibilidade com o roteamento do main.py
container_formulario_isaude = container_formulario_saude
mostrar_formulario_saude = container_formulario_saude
main = container_formulario_saude
