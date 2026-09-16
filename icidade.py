import base64
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# BANCO DE DADOS (NEON)
# =============================================================================
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS respostas_icidade (
                        qid VARCHAR(50) NOT NULL,
                        ano INTEGER NOT NULL,
                        valor TEXT,
                        pontos REAL DEFAULT 0,
                        link TEXT,
                        comentarios JSONB DEFAULT '[]'::jsonb,
                        status VARCHAR(20) DEFAULT 'Pendente',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (ano, qid)
                    );
                """)
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao inicializar tabela respostas_icidade: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_icidade
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()
                for row in rows:
                    respostas[row["qid"]] = {
                        "valor": row["valor"] or "",
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": (
                            row["link"]
                            if row["link"] != "EMPTY_STRING"
                            else ""
                        ),
                        "comentarios": (
                            row["comentarios"]
                            if isinstance(row["comentarios"], list)
                            else []
                        ),
                        "status": row["status"] or "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB: {e}")
    return respostas


def save_resposta(
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    link_final = link.strip() if link else ""

    query = """
        INSERT INTO respostas_icidade (ano, qid, valor, pontos, link, comentarios, status)
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
                        int(ano),
                        str(qid),
                        str(valor),
                        float(pontos),
                        link_final,
                        Json(comentarios),
                        str(status),
                    ),
                )
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao salvar resposta no Neon DB: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_icidade WHERE ano = %s;"
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
# FUNÇÃO AUXILIAR DE RENDERIZAÇÃO DE QUESITOS (ATUALIZADA)
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes=None,
    tipo_input="radio",
    placeholder_texto="Digite sua resposta...",
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    dados_q = res_data.get(qid, {})
    valor_atual = dados_q.get("valor", "")
    link_atual = dados_q.get("link", "")

    # Para checkboxes, a resposta armazenada é uma lista ou string separada por vírgulas
    if tipo_input == "checkbox":
        itens_selecionados = [v.strip() for v in valor_atual.split(",") if v.strip()] if valor_atual else []
        state = {"opcao": itens_selecionados, "link": link_atual}
    else:
        state = {"opcao": valor_atual, "link": link_atual}

    with ui.card().classes(
        "w-full p-6 mb-4 border border-gray-300 rounded-lg shadow-sm bg-white"
    ):
        ui.label(f"{qid} • {titulo}").classes(
            "text-xl font-semibold text-blue-500 mb-3"
        )
        ui.label(pergunta).classes("text-base font-bold text-black mb-1")
        ui.label(
            "ℹ Preencha os campos abaixo e clique no botão de salvar."
        ).classes("text-xs text-gray-400 mb-6")

        # 1. RENDERIZAÇÃO DO CAMPO DE ENTRADA
        if tipo_input == "checkbox":
            if not opcoes:
                opcoes = {}
            
            with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                with ui.element("div").classes("flex flex-col gap-2"):
                    checkboxes = {}
                    for item in opcoes.keys():
                        chk = ui.checkbox(
                            text=item,
                            value=(item in state["opcao"])
                        ).props("color=blue")
                        checkboxes[item] = chk

                ui.textarea(
                    label="Link de Evidência / Documento:",
                    value=state["link"],
                    placeholder=placeholder_link,
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "link"
                )

        elif tipo_input in ["texto", "link"]:
            ui.textarea(
                label="Resposta / Detalhamento:",
                value=state["opcao"],
                placeholder=placeholder_texto if tipo_input == "texto" else placeholder_link,
            ).classes("w-full mb-4").props("outlined rows=3").bind_value(
                state, "opcao"
            )
        else:
            if not opcoes:
                opcoes = {"Selecione...": 0.0}

            if state["opcao"] not in opcoes:
                state["opcao"] = "Selecione..."

            with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
                radio_opcao = (
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
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "link"
                )

            pts_atuais = opcoes.get(state["opcao"], 0.0)
            label_impacto = ui.label(
                f"📊 Impacto de Pontuação no Quesito {qid}: {pts_atuais:.1f} pontos"
            ).classes("text-sm font-bold text-green-600 my-4")

            def ao_mudar_opcao(e):
                novos_pts = opcoes.get(e.value, 0.0)
                label_impacto.set_text(
                    f"📊 Impacto de Pontuação no Quesito {qid}: {novos_pts:.1f} pontos"
                )

            radio_opcao.on("update:model-value", ao_mudar_opcao)

        # 2. BOTÃO DE SALVAR
        def salvar_acao():
            if tipo_input == "checkbox":
                sel = [item for item, chk in checkboxes.items() if chk.value]
                opcao_sel = ", ".join(sel)
                pts = 0.0
            else:
                opcao_sel = state["opcao"]
                pts = opcoes.get(opcao_sel, 0.0) if (opcoes and tipo_input == "radio") else 0.0

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
                try:
                    on_save_callback()
                except Exception:
                    ui.run_javascript("window.location.reload()")

        ui.button("Salvar Resposta", on_click=salvar_acao).classes(
            "bg-blue-600 text-white font-bold px-4 py-2 mb-4"
        )

        ui.separator().classes("my-2")

        bloco_comentarios(
            qid=qid, res_data=res_data, on_save_callback=on_save_callback
        )

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
        ui.label("🛠️ Painel de Controle (icidade)").classes(
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
# MÓDULO PRINCIPAL DE REQUISITOS (LAYOUT CORRIGIDO)
# =============================================================================
def container_formulario_icidade(ano=None):
    if "ano_referencia_global" not in app.storage.user:
        app.storage.user["ano_referencia_global"] = ano if ano else 2026

    @ui.refreshable
    def render_conteudo():
        ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
        res_data = load_respostas(ano_sel)

        def alterar_ano(novo_ano):
            app.storage.user["ano_referencia_global"] = int(novo_ano)
            ui.notify(f"Ano alterado para {novo_ano}", type="info")
            render_conteudo.refresh()

        # Grid principal de 12 colunas
        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-12 gap-6 items-start"
        ):

            # Coluna 1: Painel Lateral (3 colunas)
            with ui.element("div").classes("md:col-span-4 lg:col-span-3"):
                render_painel_controle(
                    ano_atual=ano_sel,
                    on_mudar_ano=alterar_ano,
                    on_refresh=render_conteudo.refresh,
                )

            # Coluna 2: Formulário (9 colunas) — TUDO DEVE FICAR DENTRO DESTE BLOCO
            with ui.element("div").classes(
                "md:col-span-8 lg:col-span-9 bg-white p-6 border rounded-lg shadow-sm flex flex-col gap-6"
            ):
                ui.label(f"📋 Módulo i-cidade — Ano {ano_sel}").classes(
                    "text-xl font-bold mb-2 text-slate-800 border-b pb-2"
                )

                # ==========================================
                # QUESITO 1.0 (Seleção com Pontuação)
                # ==========================================
                opcoes_10 = {
                    "Selecione...": 0.0,
                    "Sim (40.0 pts)": 40.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.0",
                    titulo="Criação da COMPDEC ou Órgão Similar",
                    pergunta="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil - COMPDEC ou órgão similar responsável pela execução, coordenação e mobilização de todas as ações de defesa civil no município?",
                    opcoes=opcoes_10,
                    placeholder_link="Insira o link para a comprovação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.1 (Campo de Texto / Normativo)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.1",
                    titulo="Instrumento Normativo de Criação da COMPDEC",
                    pergunta="Informe o Instrumento normativo, Número e Data da publicação da criação da COMPDEC ou órgão similar:",
                    tipo_input="texto",
                    placeholder_texto="Ex: Lei Municipal nº 1.234, de 10 de janeiro de 2020",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.2 (Link / Página Eletrônica)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.2",
                    titulo="Link do Instrumento Normativo",
                    pergunta="Informe a página eletrônica (link na internet) do instrumento normativo que criou a COMPDEC ou órgão similar (Se não estiver disponível na internet, inserir XYZ no campo de resposta):",
                    tipo_input="link",
                    placeholder_link="https://... ou digite XYZ",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.3 (Seleção com Pontuação)
                # ==========================================
                opcoes_13 = {
                    "Selecione...": 0.0,
                    "Gabinete do Prefeito (5.0 pts)": 5.0,
                    "Secretaria Municipal de Segurança Pública (0.0 pts)": 0.0,
                    "Controladoria (0.0 pts)": 0.0,
                    "Outra (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.3",
                    titulo="Vinculação Subordinativa da COMPDEC",
                    pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
                    opcoes=opcoes_13,
                    placeholder_link="Insira o link do organograma ou norma que comprove a vinculação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.4 (Seleção com Pontuação)
                # ==========================================
                opcoes_14 = {
                    "Selecione...": 0.0,
                    "Sim, inclusive com a participação de entidades privadas e da comunidade (50.0 pts)": 50.0,
                    "Sim, com participação de entidades privadas (20.0 pts)": 20.0,
                    "Sim, com participação da comunidade (20.0 pts)": 20.0,
                    "Sim, apenas com participação dos representantes da administração municipal (10.0 pts)": 10.0,
                    "Não atuam de forma sistêmica (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.4",
                    titulo="Atuação Sistêmica e Articulada",
                    pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a PNPDEC?",
                    opcoes=opcoes_14,
                    placeholder_link="Insira o link do documento, ata ou norma comprovatória...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 1.5 (Campo de Texto / Motivo)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.5",
                    titulo="Motivo da Não Instituição da COMPDEC",
                    pergunta="Informe o motivo de a COMPDEC ou órgão similar ainda não ter sido instituída (Ex: Instrumento normativo em elaboração, enviado para aprovação, falta de estrutura, outros):",
                    tipo_input="texto",
                    placeholder_texto="Detalhe o motivo da não instituição...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.0 (Capacitação de Agentes)
                # ==========================================
                opcoes_20 = {
                    "Selecione...": 0.0,
                    "Sim (20.0 pts)": 20.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.0",
                    titulo="Capacitação de Agentes Municipais",
                    pergunta="Sobre treinamento e capacitação sobre Proteção e Defesa Civil, a Prefeitura capacita seus agentes para ações municipais de Defesa Civil?",
                    opcoes=opcoes_20,
                    placeholder_link="Insira o link do certificado, lista de presença ou registro...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.1 (Data da Última Capacitação)
                # ==========================================
                opcoes_21 = {
                    "Selecione...": 0.0,
                    "Após 31/12/2023 [Até 2025] (30.0 pts)": 30.0,
                    "Até 31/12/2023 ou Sem Capacitação (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.1",
                    titulo="Data da Última Capacitação dos Agentes",
                    pergunta="Qual a data da última capacitação dos agentes municipais para ações de Defesa Civil? (Atenção: Não considerar capacitações do ano corrente de 2026):",
                    opcoes=opcoes_21,
                    placeholder_link="Informe a data no formato DD/MM/AAAA e insira o link da evidência...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 2.2 (Público-Alvo dos Treinamentos)
                # ==========================================
                opcoes_22 = {
                    "Selecione...": 0.0,
                    "Para escolas (5.0 pts)": 5.0,
                    "Para outras secretarias / entidades municipais (3.0 pts)": 3.0,
                    "Para munícipes ou empresas (2.0 pts)": 2.0,
                    "Não ofereceu nenhum curso/treinamento no ano (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="2.2",
                    titulo="Público Alvo dos Cursos e Treinamentos",
                    pergunta="A Prefeitura Municipal ofereceu cursos/treinamento sobre Proteção e Defesa Civil para qual público?",
                    opcoes=opcoes_22,
                    placeholder_link="Insira o link das evidências dos treinamentos oferecidos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.0 (Estímulo à Participação)
                # ==========================================
                opcoes_30 = {
                    "Selecione...": 0.0,
                    "Sim (10.0 pts)": 10.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.0",
                    titulo="Estímulo à Participação da Sociedade Civil",
                    pergunta="O Município realiza ações para estimular a participação de entidades privadas, associações de voluntários, clubes de serviços, organizações não governamentais e associações de classe e comunitárias nas ações de proteção e defesa civil?",
                    opcoes=opcoes_30,
                    placeholder_link="Insira o link para a comprovação das ações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.1 (Ações Realizadas - Checkbox)
                # ==========================================
                opcoes_31 = {
                    "Workshop / Palestra": 0.0,
                    "Reunião": 0.0,
                    "Conferência": 0.0,
                    "Congresso": 0.0,
                    "Discussão na Câmara Municipal": 0.0,
                    "Treinamentos": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.1",
                    titulo="Ações Realizadas com a Sociedade Civil",
                    pergunta="Assinale quais ações foram realizadas:",
                    tipo_input="checkbox",
                    opcoes=opcoes_31,
                    placeholder_link="Insira o link das atas, fotos ou comprovações das ações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 3.1.1 (Data do Último Treinamento)
                # ==========================================
                opcoes_311 = {
                    "Selecione...": 0.0,
                    "Após 31/12/2023 [Até 2025] (10.0 pts)": 10.0,
                    "Até 31/12/2023 ou Sem Treinamento (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="3.1.1",
                    titulo="Data do Último Treinamento de Voluntários",
                    pergunta="Qual a data do último treinamento de associações de voluntários? (Atenção: Não considerar os treinamentos realizados em 2026):",
                    opcoes=opcoes_311,
                    placeholder_link="Informe a data (DD/MM/AAAA) e insira o link do certificado ou ata...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.0 (Carta Geotécnica)
                # ==========================================
                opcoes_40 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.0",
                    titulo="Carta Geotécnica de Suscetibilidade",
                    pergunta="O Município recebeu a Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco? (Disponível em: http://www.defesacivil.sp.gov.br/instrumentos-de-identificacao-de-riscos/):",
                    opcoes=opcoes_40,
                    placeholder_link="Insira o link para a publicação ou recebimento do documento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.1 (Ameaças Potenciais - Checkbox)
                # ==========================================
                opcoes_41 = {
                    "Riscos Geológicos": 0.0,
                    "Riscos Hidrológicos": 0.0,
                    "Riscos Meteorológicos": 0.0,
                    "Riscos Climatológicos": 0.0,
                    "Riscos Biológicos": 0.0,
                    "Riscos Tecnológicos": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.1",
                    titulo="Ameaças Potenciais Identificadas na Carta Geotécnica",
                    pergunta="Assinale quais os tipos de ameaças potenciais identificadas na Carta Geotécnica (Classificação COBRADE):",
                    tipo_input="checkbox",
                    opcoes=opcoes_41,
                    placeholder_link="Insira o link para a documentação ou trecho da Carta Geotécnica...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 4.2 (Carta no Plano Diretor)
                # ==========================================
                opcoes_42 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                    "Não se aplica o Plano Diretor para o município (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="4.2",
                    titulo="Carta Geotécnica no Plano Diretor",
                    pergunta="A Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco consta no Plano Diretor? (Art. 42-A, §1º, §2º e §3º, da Lei Federal nº 10.257/2001):",
                    opcoes=opcoes_42,
                    placeholder_link="Insira o link da Lei do Plano Diretor ou comprovação...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.0 (Mapeamento Próprio de Ameaças)
                # ==========================================
                opcoes_50 = {
                    "Selecione...": 0.0,
                    "Sim (200.0 pts)": 200.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.0",
                    titulo="Mapeamento e Identificação Própria de Ameaças",
                    pergunta="O Município realizou, por conta própria, o mapeamento e identificação das principais ameaças existentes em seu território?",
                    opcoes=opcoes_50,
                    placeholder_link="Insira o link do estudo, relatório ou mapa de ameaças...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1 (Principais Ameaças - Checkbox)
                # ==========================================
                opcoes_51 = {
                    "Epidemias": 0.0,
                    "Estiagem": 0.0,
                    "Incêndios (urbanos e florestais)": 0.0,
                    "Ondas de calor ou ondas de frio": 0.0,
                    "Inundações": 0.0,
                    "Infestações e Pragas": 0.0,
                    "Ameaças radioativas": 0.0,
                    "Deslizamentos": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1",
                    titulo="Principais Ameaças Identificadas",
                    pergunta="Assinale as principais ameaças identificadas no território municipal:",
                    tipo_input="checkbox",
                    opcoes=opcoes_51,
                    placeholder_link="Insira o link para a comprovação ou mapeamento do risco...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.1 (Fiscalização das Áreas de Risco)
                # ==========================================
                opcoes_511 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente (0.0 pts)": 0.0,
                    "Sim, parcialmente (0.0 pts)": 0.0,
                    "Não houve fiscalização (-100.0 pts)": -100.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.1",
                    titulo="Fiscalização das Áreas de Risco",
                    pergunta="As secretarias setoriais realizaram a fiscalização das áreas de risco?",
                    opcoes=opcoes_511,
                    placeholder_link="Insira o link dos relatórios de fiscalização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2 (Possibilidade de Ocupação/Invasão)
                # ==========================================
                opcoes_512 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2",
                    titulo="Possibilidade de Ocupação ou Invasão em Áreas de Risco",
                    pergunta="O município possui áreas de risco com possibilidade de ocupação/invasão?",
                    opcoes=opcoes_512,
                    placeholder_link="Insira o link da documentação de monitoramento ou parecer...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 5.1.1 (Fiscalização de Áreas de Risco)
                # ==========================================
                opcoes_511 = {
                    "Selecione...": 0.0,
                    "Sim, integralmente (0.0 pts)": 0.0,
                    "Sim, parcialmente (0.0 pts)": 0.0,
                    "Não houve fiscalização (-100.0 pts)": -100.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.1",
                    titulo="Fiscalização das Áreas de Risco",
                    pergunta="As secretarias setoriais realizaram a fiscalização das áreas de risco?",
                    opcoes=opcoes_511,
                    placeholder_link="Insira o link dos relatórios de fiscalização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2 (Possibilidade de Ocupação)
                # ==========================================
                opcoes_512 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2",
                    titulo="Áreas de Risco Sujeitas a Ocupação ou Invasão",
                    pergunta="O município possui áreas de risco com possibilidade de ocupação/invasão?",
                    opcoes=opcoes_512,
                    placeholder_link="Insira o link do levantamento ou relatório de monitoramento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.1.2.1 (Mecanismos de Vedação - Checkbox)
                # ==========================================
                opcoes_5121 = {
                    "Aplicação de sanções monetárias (multas)": 0.0,
                    "Monitoramento (fiscalização)": 0.0,
                    "Notificação dos infratores": 0.0,
                    "Interdição do local e remoção das famílias": 0.0,
                    "Demolição das ocupações": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.1.2.1",
                    titulo="Mecanismos para Vedar Novas Ocupações",
                    pergunta="Assinale os mecanismos para vedar novas ocupações nas áreas de riscos:",
                    tipo_input="checkbox",
                    opcoes=opcoes_5121,
                    placeholder_link="Insira o link para a comprovação das ações de fiscalização/coibição...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 5.2 (Informação à População)
                # ==========================================
                opcoes_52 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Parcialmente (0.0 pts)": 0.0,
                    "Não (-50.0 pts)": -50.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="5.2",
                    titulo="Informação da População sobre Ameaças",
                    pergunta="A população foi informada sobre todas as ameaças identificadas pelo município?",
                    opcoes=opcoes_52,
                    placeholder_link="Insira o link das campanhas, boletins ou publicações...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 6.0 (Vistoria em Edificações)
                # ==========================================
                opcoes_60 = {
                    "Selecione...": 0.0,
                    "Sim, de acordo com um cronograma preestabelecido (0.0 pts)": 0.0,
                    "Sim, de acordo com a demanda (0.0 pts)": 0.0,
                    "Não foram vistoriadas (-50.0 pts)": -50.0,
                    "Não houve casos de edificações vulneráveis no Município (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="6.0",
                    titulo="Vistorias em Edificações Vulneráveis",
                    pergunta="A Secretaria responsável realizou vistorias em edificações vulneráveis com o objetivo de identificar a necessidade de intervenção preventiva nos imóveis?",
                    opcoes=opcoes_60,
                    placeholder_link="Insira o link do cronograma, ordens de serviço ou relatórios...",
                    on_save_callback=render_conteudo.refresh,
                )

    # ==========================================
                # QUESITO 7.0 (Plano de Contingência - PLANCON)
                # ==========================================
                opcoes_70 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.0",
                    titulo="Plano de Contingência Municipal (PLANCON)",
                    pergunta="O Município possui Plano de Contingência Municipal – PLANCON de Defesa Civil?",
                    opcoes=opcoes_70,
                    placeholder_link="Insira o link da publicação do PLANCON...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.1 (PLANCON por Ameaça Especificada)
                # ==========================================
                opcoes_71 = {
                    "Selecione...": 0.0,
                    "Sim, cada ameaça mapeada possui um PLANCON diferente (5.0 pts)": 5.0,
                    "Sim, parte das ameaças possuem PLANCON diferentes (3.0 pts)": 3.0,
                    "Existe apenas um PLANCON que abrange todas as ameaças (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.1",
                    titulo="PLANCON Específico por Ameaça",
                    pergunta="Foi elaborado um PLANCON específico para cada ameaça identificada?",
                    opcoes=opcoes_71,
                    placeholder_link="Insira o link para os planos específicos...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.2 (Exercícios Simulados)
                # ==========================================
                opcoes_72 = {
                    "Selecione...": 0.0,
                    "Sim (80.0 pts)": 80.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.2",
                    titulo="Exercícios Simulados do PLANCON",
                    pergunta="São realizados regularmente exercícios simulados para as contingências previstas no PLANCON?",
                    opcoes=opcoes_72,
                    placeholder_link="Insira o link de relatórios, fotos ou atas dos simulados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.3 (Sistema de Alerta)
                # ==========================================
                opcoes_73 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.3",
                    titulo="Sistema de Alerta de Desastres",
                    pergunta="O Município possui sistema de alerta para desastres? (Objetivo: avisar a população vulnerável antes de ocorrer o evento):",
                    opcoes=opcoes_73,
                    placeholder_link="Insira o link do documento ou norma que regulamenta o alerta...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.3.1 (Tipos de Sistema de Alerta - Checkbox)
                # ==========================================
                opcoes_731 = {
                    "Alerta via SMS": 0.0,
                    "Anúncio por rádio/Televisão": 0.0,
                    "Placas de identificação de área de risco": 0.0,
                    "Aviso à comunidade por telefone / Aplicativo de mensagens": 0.0,
                    "Aviso à comunidade por email": 0.0,
                    "Aviso aos membros do Nupdec": 0.0,
                    "Outro": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.3.1",
                    titulo="Tipos de Sistemas de Alerta Utilizados",
                    pergunta="Assinale os tipos de sistemas de alerta utilizados pelo Município (Objetivo: avisar a população vulnerável antes de ocorrer o evento):",
                    tipo_input="checkbox",
                    opcoes=opcoes_731,
                    placeholder_link="Insira o link com fotos, comprovantes de envio ou registros dos alertas...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.4 (Sistema de Alarme)
                # ==========================================
                opcoes_74 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.4",
                    titulo="Sistema de Alarme para Desastres",
                    pergunta="O Município dispõe de sinal, dispositivo ou sistema de alarme para desastres? (Objetivo: avisar a população sobre o evento que está ocorrendo):",
                    opcoes=opcoes_74,
                    placeholder_link="Insira o link da norma, contrato ou registro do sistema de alarme...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.4.1 (Tipos de Sistema de Alarme - Checkbox)
                # ==========================================
                opcoes_741 = {
                    "Sinal sonoro (sirene)": 0.0,
                    "Sinal luminoso": 0.0,
                    "Carros de emergência equipados de sirenes": 0.0,
                    "Carros de emergência com alto-falantes": 0.0,
                    "Aviso aos membros do Nupdec": 0.0,
                    "Aviso à comunidade por telefone / Aplicativo de mensagens": 0.0,
                    "Uso da imprensa (TV, rádio, internet)": 0.0,
                    "Outro": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.4.1",
                    titulo="Tipos de Sinal, Dispositivo ou Sistema de Alarme",
                    pergunta="Assinale os tipos de sinal, dispositivo ou sistema de alarme utilizado pelo Município:",
                    tipo_input="checkbox",
                    opcoes=opcoes_741,
                    placeholder_link="Insira o link das evidências dos dispositivos e alarmes acionados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.5 (Cadastro de Abrigos na CEPDEC)
                # ==========================================
                opcoes_75 = {
                    "Selecione...": 0.0,
                    "Sim, atualizado (10.0 pts)": 10.0,
                    "Sim, mas não está atualizado (3.0 pts)": 3.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.5",
                    titulo="Cadastro de Locais para Abrigo na CEPDEC",
                    pergunta="Possui cadastro dos locais para abrigo à população em situação de desastre junto à Coordenadoria Estadual de Proteção e Defesa Civil (CEPDEC)?",
                    opcoes=opcoes_75,
                    placeholder_link="Insira o link da comprovação de cadastro/atualização junto à CEPDEC...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.6 (Fornecedores de Ajuda Humanitária)
                # ==========================================
                opcoes_76 = {
                    "Selecione...": 0.0,
                    "Sim, atualizado (10.0 pts)": 10.0,
                    "Sim, mas não está atualizado (3.0 pts)": 3.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.6",
                    titulo="Lista de Fornecedores de Ajuda Humanitária",
                    pergunta="O Município possui cadastro da lista de fornecedores para coleta e distribuição de suprimentos de ajuda humanitária para o caso de desastre?",
                    opcoes=opcoes_76,
                    placeholder_link="Insira o link da relação de fornecedores cadastrados...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 7.7 (Data da Última Atualização do PLANCON)
                # ==========================================
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="7.7",
                    titulo="Data da Última Atualização do PLANCON",
                    pergunta="Qual a data da última atualização do PLANCON? (Se não houve atualização, informar a data do início da vigência do PLANCON):",
                    tipo_input="texto",
                    placeholder_texto="Informe a data (DD/MM/AAAA) e observações...",
                    placeholder_link="Insira o link do documento indicando a data de vigência/atualização...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.0 (Canal de Atendimento de Emergência)
                # ==========================================
                opcoes_80 = {
                    "Selecione...": 0.0,
                    "Sim (50.0 pts)": 50.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.0",
                    titulo="Canal de Atendimento de Emergência",
                    pergunta="O Município possui um canal de atendimento de emergência à população para registro de ocorrências de desastres?",
                    opcoes=opcoes_80,
                    placeholder_link="Insira o link comprovando a disponibilização do canal de atendimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.1 (Canais Utilizados - Checkbox)
                # ==========================================
                opcoes_81 = {
                    "Telefone de emergências": 0.0,
                    "Aplicativo de mensagens": 0.0,
                    "Correio eletrônico (e-mail)": 0.0,
                    "Aplicativo da Prefeitura": 0.0,
                    "Site da Prefeitura": 0.0,
                    "Redes sociais": 0.0,
                    "Outros": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.1",
                    titulo="Canais de Contato para Emergências",
                    pergunta="Assinale os canais que possui (Como a população entra em contato com o Município em caso de desastre):",
                    tipo_input="checkbox",
                    opcoes=opcoes_81,
                    placeholder_link="Insira o link ou divulgação dos canais de atendimento...",
                    on_save_callback=render_conteudo.refresh,
                )

                # ==========================================
                # QUESITO 8.1.1 (Utilização do Número 199)
                # ==========================================
                opcoes_811 = {
                    "Selecione...": 0.0,
                    "Sim (0.0 pts)": 0.0,
                    "Não (0.0 pts)": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="8.1.1",
                    titulo="Uso do Número 199 da Defesa Civil",
                    pergunta="Sobre o número de telefone de emergência, utiliza o número 199 da Defesa Civil?",
                    opcoes=opcoes_811,
                    placeholder_link="Insira o link para a linha ou ato normativo do telefone 199...",
                    on_save_callback=render_conteudo.refresh,
                )

    render_conteudo()
