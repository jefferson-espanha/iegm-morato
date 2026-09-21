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

    # Executa a renderização inicial
    render_conteudo()


# Aliases para compatibilidade com o roteamento do main.py
container_formulario_isaude = container_formulario_saude
mostrar_formulario_saude = container_formulario_saude
main = container_formulario_saude
