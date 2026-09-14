import ast
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# EXPRESSÕES REGULARES E CONFIGURAÇÃO DO BANCO DE DADOS (NEON)
# =============================================================================
REGEX_PURE_URL = r"https?://[^\s]+"

DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    """Cria e retorna uma conexão ativa com a base de dados do Neon."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def load_respostas(ano):
    """Carrega o dicionário de respostas salvas para o ano selecionado no Neon DB (Tabela IAMB)."""
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iamb
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                rows = cur.fetchall()
                for row in rows:
                    link_val = row["link"]
                    if link_val is None or link_val == "EMPTY_STRING":
                        link_val = ""

                    respostas[row["qid"]] = {
                        "valor": row["valor"] if row["valor"] is not None else "",
                        "pontos": float(row["pontos"])
                        if row["pontos"] is not None
                        else 0.0,
                        "link": link_val,
                        "comentarios": row["comentarios"]
                        if isinstance(row["comentarios"], list)
                        else [],
                        "status": row["status"]
                        if row["status"] is not None
                        else "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do IAMB no Neon DB: {e}")
        ui.notify(f"Erro ao carregar dados do banco Neon: {e}", type="negative")

    return respostas


def save_resposta(
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente"
):
    """Salva a resposta, link, pontos e comentários de um quesito do IAMB no Neon DB."""
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
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
                        ano,
                        str(qid),
                        str(valor),
                        float(pontos),
                        link_final,
                        Json(comentarios_validos),
                        str(status),
                    ),
                )
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao salvar resposta do IAMB no Neon DB: {e}")
        ui.notify(f"Erro ao salvar no banco Neon: {e}", type="negative")


def zerar_questionario_db(ano):
    """Limpa todas as respostas salvas do ano selecionado na tabela respostas_iamb."""
    query = "DELETE FROM respostas_iamb WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário IAMB no Neon DB: {e}")
        ui.notify(f"Erro ao apagar dados no banco Neon: {e}", type="negative")


def _obter_lista_comentarios(dados_banco):
    """Garante retorno de lista válida de comentários."""
    raw = dados_banco.get("comentarios", [])
    if isinstance(raw, str):
        if raw in ["EMPTY_STRING", "", "null", "None"]:
            return []
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, list):
        return raw
    return []


def gerar_relatorio_pdf_bytes(res_data, ano, total_pts, faixa):
    """Gera dados para download do relatório IAMB."""
    conteudo = f"RELATÓRIO TÉCNICO IAMB - MEIO AMBIENTE ({ano})\n"
    conteudo += f"Pontuação Total: {total_pts:.1f} pts | Faixa de Desempenho: {faixa}\n\n"
    for qid, dados in res_data.items():
        conteudo += f"Quesito {qid}: {dados.get('valor')} | Pontos: {dados.get('pontos')} | Link: {dados.get('link')}\n"
    return conteudo.encode("utf-8")


# =============================================================================
# 1. PAINEL LATERAL / CONTROLE (IAMB)
# =============================================================================
def render_painel_controle(on_refresh_callback=None):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_atual = app.storage.user.get("ano_referencia_global", 2026)

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label("🌱 Painel de Controle - IAMB").classes(
            "text-lg font-bold mb-2 text-emerald-900"
        )

        def ao_mudar_ano(e):
            app.storage.user["ano_referencia_global"] = e.value
            ui.notify(f"Ano alterado para {e.value}", type="info")
            if on_refresh_callback:
                on_refresh_callback()

        ui.select(
            options=anos,
            value=ano_atual,
            label="Ano de Referência:",
            on_change=ao_mudar_ano,
        ).classes("w-full mb-4")

        # Busca dados e faz o cálculo
        res_data = load_respostas(ano_atual)
        total_pts = sum(
            float(item.get("pontos", 0)) for item in res_data.values()
        )

        # Regras de Faixa Ambientais (IAMB)
        if total_pts < 40.0:
            faixa, cor = "C (Insuficiente)", "text-red-600"
        elif total_pts < 60.0:
            faixa, cor = "B (Mediano)", "text-orange-500"
        elif total_pts < 80.0:
            faixa, cor = "B+ (Bom)", "text-yellow-600"
        else:
            faixa, cor = "A (Excelência)", "text-emerald-700"

        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("Índice Ambiental").classes(
                "text-xs text-gray-500 font-bold uppercase"
            )
            ui.label(f"{total_pts:.1f} pts").classes(
                "text-2xl font-black text-gray-800"
            )

            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Nível:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-sm font-bold {cor}")

        ui.separator().classes("my-2")
        ui.label("⚙️ Gerenciamento").classes("font-bold text-sm mb-2")

        def atualizar_dados():
            ui.notify(
                "Métricas ambientais atualizadas!", type="positive", icon="refresh"
            )
            if on_refresh_callback:
                on_refresh_callback()

        ui.button(
            "🔄 Atualizar Indicadores", on_click=atualizar_dados
        ).classes("w-full bg-emerald-700 text-white mb-2")
        ui.separator().classes("my-2")

        # Modal de Zerapagem
        with ui.dialog() as dialog_zerar, ui.card().classes("w-96 p-4"):
            ui.label("🔒 Confirmar Exclusão").classes(
                "text-lg font-bold text-red-600"
            )
            ui.label(
                f"Você irá apagar os dados do IAMB de {ano_atual}. Ação irreversível!"
            ).classes("text-sm my-2")

            input_senha = ui.input(
                "Senha de Administrador:", password=True
            ).classes("w-full mb-4")

            def executar_zerar():
                if input_senha.value == "fidelios":
                    zerar_questionario_db(ano_atual)
                    ui.notify(
                        f"✅ Registros do IAMB {ano_atual} foram zerados!",
                        type="positive",
                    )
                    dialog_zerar.close()
                    if on_refresh_callback:
                        on_refresh_callback()
                else:
                    ui.notify("❌ Senha incorreta!", type="negative")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancelar", on_click=dialog_zerar.close).props("flat")
                ui.button(
                    "Confirmar e Zerar", on_click=executar_zerar
                ).classes("bg-red-600 text-white")

        with ui.row().classes("w-full gap-2 no-wrap"):
            pdf_bytes = gerar_relatorio_pdf_bytes(
                res_data, ano_atual, total_pts, faixa
            )
            ui.button(
                "📄 Relatório",
                on_click=lambda: ui.download(
                    pdf_bytes, f"Relatorio_IAMB_{ano_atual}.pdf"
                ),
            ).classes("flex-1 bg-emerald-800 text-white")
            ui.button("🗑️ Zerar", on_click=dialog_zerar.open).classes(
                "flex-1 bg-red-700 text-white"
            )

        ui.separator().classes("my-4")
        ui.html("""
            <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 12px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
        """).classes("w-full")


# =============================================================================
# 2. BLOCO DE COMENTÁRIOS INTERNOS
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
        f"💬 Análise do Quesito {qid} | Status: {badge_status}",
        value=(status_global == "Pendente"),
    ).classes("w-full border rounded p-2 mt-3 bg-gray-50"):

        def alterar_status(e):
            novo_st = e.value
            log = {
                "autor": "Sistema / " + usuario_atual,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "texto": f"ℹ️ Alterou o status ambiental para: **{novo_st.upper()}**.",
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
                    ui.notify("Registro excluído.", type="warning")
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
                            f"""<div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #059669; border: 1px solid #e0e0e0; width: 100%;">
                                <span style="font-size: 11px; color: #059669; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>"""
                        ).classes("w-full")

                    ui.button("🗑️", on_click=deletar_comentario).props(
                        "flat dense"
                    )

        input_novo_comentario = (
            ui.textarea(placeholder="Adicionar parecer / nota técnica...")
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
                ui.notify("Apontamento registrado!", type="positive")
                if on_save_callback:
                    on_save_callback()

        ui.button("Inserir Parecer", on_click=postar_comentario).classes(
            "bg-emerald-700 text-white mt-2"
        )


# =============================================================================
# 3. RENDERIZADOR DE QUESITO
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes=None,
    on_save_callback=None,
    tipo="radio",
    informativo=False,
    is_text_area=False,
    placeholder_text="Descreva a ação ou informe os dados...",
    placeholder_link="Link para o Portal / Documento / Lei:",
    pontuacao_maxima=None,
    **kwargs,
):
    d_data = res_data.get(qid, {})

    with ui.card().classes("w-full mb-4 p-4 border rounded-lg shadow-sm"):
        with ui.expansion(f"🌱 Quesito {qid} - {titulo}", value=True).classes(
            "w-full font-bold"
        ):
            ui.label(f"{qid} • {titulo}").classes("text-h6 text-emerald-800 mt-2")
            ui.label(pergunta).classes("text-body1 font-bold my-2")
            ui.label(
                "ℹ Preencha as comprovações ambientais e salve."
            ).classes("text-caption text-grey-6 mb-4")

            with ui.row().classes("w-full gap-4 items-start"):
                with ui.column().classes("flex-1"):
                    checkbox_dict = {}
                    input_valor = None

                    if tipo == "checkbox" and opcoes:
                        lista_opcoes = (
                            list(opcoes.keys())
                            if isinstance(opcoes, dict)
                            else opcoes
                        )
                        v_salvo = d_data.get("valor", "[]")

                        if isinstance(v_salvo, str):
                            try:
                                sel_list = ast.literal_eval(v_salvo)
                                if not isinstance(sel_list, list):
                                    sel_list = []
                            except Exception:
                                sel_list = []
                        elif isinstance(v_salvo, list):
                            sel_list = v_salvo
                        else:
                            sel_list = []

                        for opt in lista_opcoes:
                            chk = ui.checkbox(
                                opt, value=(opt in sel_list)
                            ).classes("mb-1")
                            checkbox_dict[opt] = chk

                    elif opcoes:
                        lista_opcoes = (
                            list(opcoes.keys())
                            if isinstance(opcoes, dict)
                            else opcoes
                        )
                        v_salvo = d_data.get("valor", "Selecione...")
                        valor_inicial = (
                            v_salvo
                            if v_salvo in lista_opcoes
                            else (lista_opcoes[0] if lista_opcoes else "")
                        )

                        input_valor = ui.radio(
                            options=lista_opcoes, value=valor_inicial
                        ).classes("gap-2")
                    else:
                        input_valor = ui.textarea(
                            label="Comprovação / Detalhes:",
                            placeholder=placeholder_text,
                            value=d_data.get("valor", ""),
                        ).classes("w-full").props("outlined rows=3")

                # Links / Documentação
                with ui.column().classes("flex-1"):
                    input_link = ui.textarea(
                        label="Link da Evidência Ambiental:",
                        value=d_data.get("link", ""),
                        placeholder=placeholder_link,
                    ).classes("w-full").props("outlined rows=3")

                    container_links = ui.row().classes("mt-1")

                    def atualizar_links_visuais():
                        container_links.clear()
                        val_txt = ""
                        if input_valor and hasattr(input_valor, "value"):
                            val_txt = (
                                input_valor.value
                                if (is_text_area and input_valor.value)
                                else ""
                            )

                        lnk_txt = input_link.value or ""
                        txt_total = f"{val_txt} {lnk_txt}"

                        links = re.findall(REGEX_PURE_URL, txt_total)
                        if links:
                            with container_links:
                                ui.label("Evidências Ativas: ").classes(
                                    "font-bold text-caption"
                                )
                                for url in links:
                                    ui.link(url, target=url, new_tab=True).classes(
                                        "text-caption text-emerald-700 mr-2"
                                    )

                    input_link.on(
                        "update:model-value", atualizar_links_visuais
                    )
                    if is_text_area and input_valor:
                        input_valor.on(
                            "update:model-value", atualizar_links_visuais
                        )

                    atualizar_links_visuais()

            lbl_pontos = ui.html().classes("mt-3 font-bold")

            def atualizar_label_pontos(pts, val):
                if informativo or pontuacao_maxima == 0.0 or not opcoes:
                    lbl_pontos.set_content(
                        f"<span style='color:#6c757d;'>📊 Impacto no IAMB: 0.0 pts (Informativo)</span>"
                    )
                else:
                    cor = (
                        "#059669"
                        if pts > 0
                        else (
                            "#dc3545" if val != "Selecione..." else "#6c757d"
                        )
                    )
                    lbl_pontos.set_content(
                        f"<span style='color:{cor};'>📊 Impacto no IAMB: {pts:.1f} pontos</span>"
                    )

            atualizar_label_pontos(
                d_data.get("pontos", 0.0), d_data.get("valor", "")
            )

            def salvar():
                if tipo == "checkbox" and opcoes:
                    selecionados = [
                        opt
                        for opt, chk_obj in checkbox_dict.items()
                        if chk_obj.value
                    ]
                    val = str(selecionados)
                    pts = 0.0
                elif opcoes:
                    val = input_valor.value if input_valor else ""
                    pts = (
                        opcoes.get(val, 0.0)
                        if isinstance(opcoes, dict)
                        else 0.0
                    )
                else:
                    val = input_valor.value if input_valor else ""
                    pts = 0.0

                link = input_link.value or ""
                st = d_data.get("status", "Pendente")
                comms = d_data.get("comentarios", [])

                save_resposta(
                    ano=ano,
                    qid=qid,
                    valor=val,
                    pontos=pts,
                    link=link,
                    comentarios=comms,
                    status=st,
                )
                atualizar_label_pontos(pts, val)
                ui.notify(
                    f"Quesito {qid} salvo com sucesso!",
                    type="positive",
                    icon="check_circle",
                )

                if on_save_callback:
                    on_save_callback()

            ui.button(f"💾 Salvar Quesito {qid}", on_click=salvar).classes(
                "bg-emerald-800 text-white mt-4"
            )

            bloco_comentarios(qid, res_data, on_save_callback=on_save_callback)


# =============================================================================
# 4. CONTAINER PRINCIPAL IAMB
# =============================================================================
@ui.refreshable
def container_formulario_icidade():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes("w-full gap-6 items-start"):

        # Painel Lateral
        with ui.column().classes("col-span-1 w-full"):
            render_painel_controle(
                on_refresh_callback=container_formulario_icidade.refresh
            )

        # Quesitos Ambientais (IAMB)
        with ui.column().classes("col-span-3 w-full"):
            ui.label(
                f"Formulário IAMB - Gestão Ambiental ({ano_sel})"
            ).classes("text-h4 mb-1 font-bold text-emerald-900")
            ui.label(
                "Índice de Avaliação do Meio Ambiente - Registro de Evidências e Metas."
            ).classes("text-gray-600 mb-6")

            # EIXO 1: ESTRUTURA E POLÍTICA MUNICIPAL DE MEIO AMBIENTE
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim, com Conselho e Fundo Municipal ativos (20 pts)": 20.0,
                "Sim, apenas Conselho ativo (10 pts)": 10.0,
                "Não possui estrutura regulamentada (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Estrutura de Gestão Ambiental e Conselho",
                pergunta="O município possui Órgão Ambiental estruturado, Conselho Municipal de Meio Ambiente (COMDEMA) e Fundo Municipal ativos?",
                opcoes=opcoes_10,
                on_save_callback=container_formulario_icidade.refresh,
            )

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1",
                titulo="Legislação e Política Municipal de Meio Ambiente",
                pergunta="Informe a Lei/Instrumento Normativo da Política Municipal de Meio Ambiente e data de publicação:",
                is_text_area=True,
                placeholder_text="Ex: Lei Municipal nº 4.567 de 12/03/2018",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # EIXO 2: RESÍDUOS SÓLIDOS E COLETA SELETIVA
            opcoes_20 = {
                "Selecione...": 0.0,
                "Possui Coleta Seletiva em 100% da área urbana (25 pts)": 25.0,
                "Coleta Seletiva parcial (acima de 50% dos bairros) (15 pts)": 15.0,
                "Coleta Seletiva incipiente (menos de 50%) (05 pts)": 5.0,
                "Não possui serviço de Coleta Seletiva (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.0",
                titulo="Plano e Operação de Coleta Seletiva",
                pergunta="Qual é a cobertura do serviço de Coleta Seletiva e Triagem de Resíduos Sólidos Recicláveis no município?",
                opcoes=opcoes_20,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # EIXO 3: LICENCIAMENTO E FISCALIZAÇÃO AMBIENTAL
            opcoes_30 = {
                "Selecione...": 0.0,
                "Realiza Licenciamento e Fiscalização com equipe própria (20 pts)": 20.0,
                "Realiza apenas Fiscalização com equipe própria (10 pts)": 10.0,
                "Depende exclusivamente do Estado (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.0",
                titulo="Capacidade de Licenciamento e Fiscalização",
                pergunta="O município exerce a atribuição de Licenciamento e/ou Fiscalização de Atividades de Impacto Local (LC 140/2011)?",
                opcoes=opcoes_30,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # EIXO 4: EDUCAÇÃO AMBIENTAL
            opcoes_40 = {
                "Selecione...": 0.0,
                "Programa contínuo na rede escolar e comunidade (15 pts)": 15.0,
                "Ações pontuais em datas comemorativas (05 pts)": 5.0,
                "Não realiza ações de Educação Ambiental (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="4.0",
                titulo="Programa Municipal de Educação Ambiental (PMEA)",
                pergunta="Há programa contínuo de Educação Ambiental executado junto à rede pública de ensino e munícipes?",
                opcoes=opcoes_40,
                on_save_callback=container_formulario_icidade.refresh,
            )
