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
# FUNÇÃO AUXILIAR DE RENDERIZAÇÃO DE QUESITOS (PADRÃO)
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes,
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    dados_q = res_data.get(qid, {})
    valor_atual = dados_q.get("valor", "Selecione...")
    if valor_atual not in opcoes:
        valor_atual = "Selecione..."

    link_atual = dados_q.get("link", "")

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

        def salvar_acao():
            opcao_sel = state["opcao"]
            pts = opcoes.get(opcao_sel, 0.0)
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
            
            # --- PROTEÇÃO CONTRA O ERRO DE REFRESH ---
            if on_save_callback:
                try:
                    on_save_callback()
                except TypeError:
                    ui.run_javascript('window.location.reload()')
                except Exception:
                    ui.run_javascript('window.location.reload()')

        ui.button("Salvar Resposta", on_click=salvar_acao).classes("bg-blue-600 text-white font-bold px-4 py-2")


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
# MÓDULO PRINCIPAL DE REQUISITOS
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

            # Coluna 2: Formulário (9/12)
            with ui.element("div").classes(
                "md:col-span-8 lg:col-span-9 bg-white p-6 border rounded-lg shadow-sm"
            ):
                ui.label(f"📋 Módulo i-cidade — Ano {ano_sel}").classes(
                    "text-xl font-bold mb-4 text-slate-800 border-b pb-2"
                )
                
                                   
            # QUESITO 1.1
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1",
                titulo="Dados do Instrumento Normativo COMPDEC",
                pergunta="Informe o Instrumento normativo, Número e Data da publicação da criação da COMPDEC ou órgão similar:",
                is_text_area=True,
                placeholder_text="Ex: Decreto nº 123 de 01/01/2025",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # QUESITO 1.2
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.2",
                titulo="Endereço Eletrônico do Instrumento Normativo",
                pergunta="Informe a página eletrônica (link na internet) do instrumento normativo que criou a COMPDEC ou órgão similar:",
                is_text_area=True,
                placeholder_text="https://www.municipio.sp.gov.br/legislacao",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # QUESITO 1.3
            opcoes_13 = {
                "Selecione...": 0.0,
                "Gabinete do Prefeito (05 pts)": 5.0,
                "Segurança Pública (00 pts)": 0.0,
                "Controladoria (00 pts)": 0.0,
                "Outra (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.3",
                titulo="Secretaria ou Diretoria de Subordinação",
                pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
                opcoes=opcoes_13,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # QUESITO 1.4
            opcoes_14 = {
                "Selecione...": 0.0,
                "Sim, inclusive com a participação de entidades privadas e da comunidade (50 pts)": 50.0,
                "Sim, com participação de entidades privadas (20 pts)": 20.0,
                "Sim, com participação da comunidade (20 pts)": 20.0,
                "Sim, apenas com representantes da administração municipal (10 pts)": 10.0,
                "Não atuam de forma sistêmica (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.4",
                titulo="Atuação Sistêmica e Articulação da Defesa Civil",
                pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a Política Nacional de Proteção e Defesa Civil - PNPDEC?",
                opcoes=opcoes_14,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 2.0 • CAPACITAÇÃO DA EQUIPE DA COMPDEC
            # =============================================================================
            opcoes_20 = {
                "Selecione...": 0.0,
                "Sim, com curso presencial ou EAD de Proteção e Defesa Civil (10 pts)": 10.0,
                "Não realizou capacitação/treinamento no ano (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.0",
                titulo="2.0 • Capacitação da Equipe da COMPDEC",
                pergunta="Os integrantes da COMPDEC participaram de cursos, treinamentos ou capacitações em Proteção e Defesa Civil no ano de referência?",
                opcoes=opcoes_20,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 2.1 • AÇÕES EDUCATIVAS E PREVENTIVAS
            # =============================================================================
            opcoes_21 = {
                "Selecione...": 0.0,
                "Sim, realizou palestras, oficinas ou campanhas de conscientização (10 pts)": 10.0,
                "Não realizou ações educativas no ano (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.1",
                titulo="2.1 • Ações Educativas e Preventivas na Comunidade",
                pergunta="A COMPDEC promoveu ações educativas, campanhas de sensibilização ou oficinas sobre percepção de risco para a população no ano de referência?",
                opcoes=opcoes_21,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 2.2 • PÚBLICO-ALVO DOS CURSOS E TREINAMENTOS
            # =============================================================================
            opcoes_22 = {
                "Selecione...": 0.0,
                "Para escolas, secretarias/entidades municipais e munícipes/empresas (10 pts)": 10.0,
                "Para escolas e secretarias/entidades municipais (08 pts)": 8.0,
                "Para escolas e munícipes/empresas (07 pts)": 7.0,
                "Para secretarias/entidades municipais e munícipes/empresas (05 pts)": 5.0,
                "Apenas para escolas (05 pts)": 5.0,
                "Apenas para outras secretarias / entidades municipais (03 pts)": 3.0,
                "Apenas para munícipes ou empresas (02 pts)": 2.0,
                "Não ofereceu nenhum curso/treinamento no ano (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.2",
                titulo="2.2 • Público Alvo de Cursos e Treinamentos",
                pergunta="A Prefeitura Municipal ofereceu cursos/treinamento sobre Proteção e Defesa Civil para qual público?",
                opcoes=opcoes_22,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 3.0 • PARTICIPAÇÃO DA SOCIEDADE CIVIL
            # =============================================================================
            opcoes_30 = {
                "Selecione...": 0.0,
                "Sim – 10 pts": 10.0,
                "Não – 00 pts": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.0",
                titulo="3.0 • Sociedade Civil e Entidades",
                pergunta=(
                    "O Município realiza ações para estabelecer a participação de entidades privadas, "
                    "associações de voluntários, clubes de serviços, organizações não governamentais e "
                    "associações de classe e comunitárias nas ações de proteção e defesa civil?"
                ),
                opcoes=opcoes_30,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 3.1 • AÇÕES REALIZADAS PARA PARTICIPAÇÃO DA SOCIEDADE
            # =============================================================================
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
                titulo="3.1 • Ações Realizadas para Participação da Sociedade",
                pergunta="Assinale quais ações foram realizadas para a participação da sociedade:",
                tipo="checkbox",  # <--- Habilita a seleção múltipla via ui.checkbox
                opcoes=opcoes_31,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Caso selecione 'Outros' ou queira detalhar as ações, especifique aqui...",
                on_save_callback=container_formulario_icidade.refresh,
            )
            # =============================================================================
            # QUESITO 3.1.1 • DATA DE TREINAMENTO DINÂMICA
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.1.1",
                titulo="3.1.1 • Data do Último Treinamento de Voluntários",
                pergunta="Qual a data do último treinamento de associações de voluntários?",
                opcoes=None,
                tipo="date",
                calculo_pontos_customizado=calc_pts_311,
                instrucoes_calculo=f"""
                **Fórmula de Cálculo:**
                * 📅 **Até 31/12/{ano_sel - 1}:** 00 pontos.
                * 📅 **A partir de 01/01/{ano_sel}:** 10 pontos.
                * 🚫 **Observação:** Treinamentos em {ano_sel + 1} não pontuam.
                """,
                on_save_callback=container_formulario_icidade.refresh
            )

# =============================================================================
            # QUESITO 4.0 • CARTA GEOTÉCNICA DE SUSCETIBILIDADE
            # =============================================================================
            opcoes_40 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="4.0",
                titulo="Carta Geotécnica de Suscetibilidade",
                pergunta="O Município recebeu a Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco?",
                opcoes=opcoes_40,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 4.1 • AMEAÇAS POTENCIAIS DA CARTA GEOTÉCNICA
            # =============================================================================
            opcoes_41 = {
                "Riscos Geológicos": 0.0,
                "Riscos Hidrológicos": 0.0,
                "Riscos Meteorológicos": 0.0,
                "Riscos Climatológicos": 0.0,
                "Riscos Biológicos": 0.0,
                "Riscos Tecnológicos": 0.0,
                "Outros": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="4.1",
                titulo="Ameaças Potenciais da Carta Geotécnica",
                pergunta="Assinale quais os tipos de ameaças potenciais identificadas na Carta Geotécnica:",
                tipo="checkbox",  # <--- Gera múltiplos ui.checkbox do NiceGUI!
                opcoes=opcoes_41,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Caso selecione 'Outros' ou queira detalhar as ameaças, especifique aqui...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 4.2 • CARTA GEOTÉCNICA NO PLANO DIRETOR
            # =============================================================================
            opcoes_42 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-50 pts)": -50.0,
                "Não se aplica o Plano Diretor (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="4.2",
                titulo="Carta Geotécnica no Plano Diretor",
                pergunta="A Carta Geotécnica de Suscetibilidade, Aptidão à Urbanização e Risco consta no Plano Diretor?",
                opcoes=opcoes_42,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 5.0 • MAPEAMENTO PRÓPRIO DE AMEAÇAS
            # =============================================================================
            opcoes_50 = {
                "Selecione...": 0.0,
                "Sim (200 pts)": 200.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.0",
                titulo="Mapeamento Próprio de Ameaças",
                pergunta="O Município realizou, por conta própria, o mapeamento e identificação das principais ameaças existentes em seu território?",
                opcoes=opcoes_50,
                on_save_callback=container_formulario_icidade.refresh
            )

# =============================================================================
            # QUESITO 5.1 • PRINCIPAIS AMEAÇAS IDENTIFICADAS
            # =============================================================================
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
                titulo="5.1 • Principais Ameaças Identificadas",
                pergunta="Assinale as principais ameaças identificadas no município:",
                tipo="checkbox",  # <--- Habilita a seleção múltipla via ui.checkbox
                opcoes=opcoes_51,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Caso selecione 'Outros' ou queira detalhar as ameaças, especifique aqui...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 5.1.1 • FISCALIZAÇÃO DE ÁREAS DE RISCO
            # =============================================================================
            opcoes_511 = {
                "Selecione...": 0.0,
                "Sim, integralmente (00 pts)": 0.0,
                "Sim, parcialmente (00 pts)": 0.0,
                "Não houve fiscalização (-100 pts)": -100.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.1.1",
                titulo="Fiscalização das Áreas de Risco",
                pergunta="As secretarias setoriais realizaram a fiscalização das áreas de risco?",
                opcoes=opcoes_511,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 5.1.2 • ÁREAS DE RISCO COM RISCO DE INVASÃO
            # =============================================================================
            opcoes_512 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.1.2",
                titulo="Possibilidade de Ocupação/Invasão em Áreas de Risco",
                pergunta="O município possui áreas de risco com possibilidade de ocupação/invasão?",
                opcoes=opcoes_512,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 5.1.2.1 • MECANISMOS CONTRA NOVAS OCUPAÇÕES
            # =============================================================================
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
                titulo="5.1.2.1 • Mecanismos para Vedar Novas Ocupações",
                pergunta="Assinale os mecanismos para vedar novas ocupações nas áreas de riscos:",
                tipo="checkbox",  # <--- Habilita a seleção múltipla via ui.checkbox
                opcoes=opcoes_5121,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Caso selecione 'Outros' ou queira detalhar os mecanismos, especifique aqui...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 5.2 • INFORMAÇÃO À POPULAÇÃO SOBRE AMEAÇAS
            # =============================================================================
            opcoes_52 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Parcialmente (00 pts)": 0.0,
                "Não (-50 pts)": -50.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.2",
                titulo="Informação à População sobre Ameaças",
                pergunta="A população foi informada sobre todas as ameaças identificadas pelo município?",
                opcoes=opcoes_52,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 6.0 • VISTORIAS EM EDIFICAÇÕES VULNERÁVEIS
            # =============================================================================
            opcoes_60 = {
                "Selecione...": 0.0,
                "Sim, de acordo com um cronograma preestabelecido (00 pts)": 0.0,
                "Sim, de acordo com a demanda (00 pts)": 0.0,
                "Não foram vistoriadas (-50 pts)": -50.0,
                "Não houve casos de edificações vulneráveis (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.0",
                titulo="Vistorias em Edificações Vulneráveis",
                pergunta="A Secretaria responsável realizou vistorias em edificações vulneráveis com o objetivo de identificar a necessidade de intervenção preventiva nos imóveis?",
                opcoes=opcoes_60,
                on_save_callback=container_formulario_icidade.refresh
            )

# =============================================================================
            # QUESITO 7.0 • PLANCON DE DEFESA CIVIL
            # =============================================================================
            opcoes_70 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.0",
                titulo="Plano de Contingência Municipal (PLANCON)",
                pergunta="O Município possui Plano de Contingência Municipal – PLANCON de Defesa Civil?",
                opcoes=opcoes_70,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 7.1 • ABRANGÊNCIA DO PLANCON POR AMEAÇA
            # =============================================================================
            opcoes_71 = {
                "Selecione...": 0.0,
                "Sim, cada ameaça mapeada possui um PLANCON diferente (05 pts)": 5.0,
                "Sim, parte das ameaças possuem PLANCON diferentes (03 pts)": 3.0,
                "Existe apenas um PLANCON que abrange todas as ameaças (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.1",
                titulo="Elaboração de PLANCON por Ameaça",
                pergunta="Foi elaborado um PLANCON específico para cada ameaça identificada?",
                opcoes=opcoes_71,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 7.2 • EXERCÍCIOS SIMULADOS DO PLANCON
            # =============================================================================
            opcoes_72 = {
                "Selecione...": 0.0,
                "Sim (80 pts)": 80.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.2",
                titulo="Exercícios Simulados para Contingências",
                pergunta="São realizados regularmente exercícios simulados para as contingências previstas no PLANCON?",
                opcoes=opcoes_72,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 7.3 • SISTEMA DE ALERTA PARA DESASTRES
            # =============================================================================
            opcoes_73 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.3",
                titulo="Sistema de Alerta para Desastres",
                pergunta="O Município possui sistema de alerta para desastres?",
                opcoes=opcoes_73,
                on_save_callback=container_formulario_icidade.refresh
            )

           # =============================================================================
            # QUESITO 7.3.1 • TIPOS DE SISTEMAS DE ALERTA
            # =============================================================================
            opcoes_731 = {
                "Alerta via SMS": 0.0,
                "Anúncio por rádio/Televisão": 0.0,
                "Placas de identificação de área de risco": 0.0,
                "Aviso por telefone / Aplicativo de mensagens": 0.0,
                "Aviso por email": 0.0,
                "Aviso aos membros do Nupdec": 0.0,
                "Outros": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.3.1",
                titulo="7.3.1 • Tipos de Sistemas de Alerta Utilizados",
                pergunta="Assinale os tipos de sistemas de alerta utilizados pelo Município:",
                tipo="checkbox",  # <--- Habilita a seleção múltipla via ui.checkbox
                opcoes=opcoes_731,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Caso selecione 'Outros' ou queira detalhar os sistemas de alerta, especifique aqui...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 7.4 • SISTEMA DE ALARME PARA DESASTRES
            # =============================================================================
            opcoes_74 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.4",
                titulo="Dispositivo ou Sistema de Alarme",
                pergunta="O Município dispõe de sinal, dispositivo ou sistema de alarme para desastres?",
                opcoes=opcoes_74,
                on_save_callback=container_formulario_icidade.refresh
            )

# =============================================================================
            # QUESITO 7.4.1 • TIPOS DE SISTEMAS DE ALARME
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.4.1",
                titulo="Tipos de Sinais ou Alarmes Utilizados",
                pergunta="Assinale os tipos de sinal, dispositivo ou sistema de alarme utilizado:",
                tipo="checkbox",
                opcoes=[
                    "Sinal sonoro (sirene)",
                    "Sinal luminoso",
                    "Carros de emergência com sirenes",
                    "Carros de emergência com alto-falantes",
                    "Aviso aos membros do Nupdec",
                    "Aviso por telefone / Aplicativo de mensagens",
                    "Uso da imprensa (TV, rádio, internet)",
                    "Outro"
                ],
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Detalhamento sobre os tipos de alarme ou insira os links de comprovação...",
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 7.5 • CADASTRO DE ABRIGOS CEPDEC
            # =============================================================================
            opcoes_75 = {
                "Selecione...": 0.0,
                "Sim, atualizado (10 pts)": 10.0,
                "Sim, mas não está atualizado (03 pts)": 3.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.5",
                titulo="Cadastro de Locais para Abrigo (CEPDEC)",
                pergunta="Possui cadastro dos locais para abrigo à população em situação de desastre junto à Coordenadoria Estadual de Proteção e Defesa Civil (CEPDEC)?",
                opcoes=opcoes_75,
                placeholder_link="Descreva as evidências ou insira os links de comprovação do cadastro...",
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 7.6 • FORNECEDORES DE AJUDA HUMANITÁRIA
            # =============================================================================
            opcoes_76 = {
                "Selecione...": 0.0,
                "Sim, atualizado (10 pts)": 10.0,
                "Sim, mas não está atualizado (03 pts)": 3.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.6",
                titulo="Cadastro de Fornecedores de Ajuda Humanitária",
                pergunta="O Município possui cadastro da lista de fornecedores para coleta e distribuição de suprimentos de ajuda humanitária para o caso de desastre?",
                opcoes=opcoes_76,
                placeholder_link="Descreva as evidências ou insira os links de comprovação do cadastro de fornecedores...",
                on_save_callback=container_formulario_icidade.refresh
            )

            

            # =============================================================================
            # QUESITO 7.7 • DATA DA ÚLTIMA ATUALIZAÇÃO DO PLANCON
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.7",
                titulo="Data da Última Atualização do PLANCON",
                pergunta="Qual a data da última atualização do PLANCON? (Se não houve atualização, informar a data do início da vigência)",
                tipo="text_input",
                format_input="date",
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_input="Ex: 15/05/2024",
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 8.0 • CANAL DE ATENDIMENTO DE EMERGÊNCIA
            # =============================================================================
            opcoes_80 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.0",
                titulo="Canal de Atendimento de Emergência",
                pergunta="O Município possui um canal de atendimento de emergência à população para registro de ocorrências de desastres?",
                opcoes=opcoes_80,
                placeholder_link="Ex: Telefone 199, WhatsApp oficial, Site de chamados...",
                on_save_callback=container_formulario_icidade.refresh
            )

           # =============================================================================
            # QUESITO 8.1 • CANAIS DE ATENDIMENTO DISPONÍVEIS
            # =============================================================================
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
                titulo="8.1 • Canais de Comunicação Disponíveis",
                pergunta="Assinale os canais de comunicação que o município possui:",
                tipo="checkbox",  # <--- Habilita a seleção múltipla em NiceGUI
                opcoes=opcoes_81,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Descreva os números, endereços eletrônicos ou insira os links dos canais...",
                on_save_callback=container_formulario_icidade.refresh,
            )

# =============================================================================
            # QUESITO 8.1.1 • UTILIZAÇÃO DO NÚMERO 199
            # =============================================================================
            opcoes_811 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.1.1",
                titulo="8.1.1 • Linha Telefônica 199",
                pergunta="Sobre o número de telefone de emergência, utiliza o número 199 da Defesa Civil?",
                opcoes=opcoes_811,
                pontuacao_maxima=0.0,
                informativo=True,
                placeholder_link="Ex: Decreto de criação, conta telefônica, print do painel...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 8.1.1.1 • DISPONIBILIDADE 24 HORAS DO 199
            # =============================================================================
            opcoes_8111 = {
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.1.1.1",
                titulo="8.1.1.1 • Regime de Operação (24h)",
                pergunta="O telefone 199 tem atendimento 24 horas por dia?",
                opcoes=opcoes_8111,
                pontuacao_maxima=20.0,
                placeholder_link="Ex: Escala de servidores, link do diário oficial...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 8.2 • REGISTRO ELETRÔNICO DE OCORRÊNCIAS
            # =============================================================================
            opcoes_82 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.2",
                titulo="8.2 • Registro Eletrônico",
                pergunta="O Município registra as ocorrências de Defesa Civil de forma eletrônica?",
                opcoes=opcoes_82,
                pontuacao_maxima=50.0,
                placeholder_link="Ex: Link do sistema informatizado, prints das telas de cadastro, decreto de adoção...",
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 9.0 • AVALIAÇÃO ESTRUTURAL DE ESCOLAS E SAÚDE
            # =============================================================================
            opcoes_90 = {
                "Selecione...": 0.0,
                "Sim, em todas as escolas e centros de saúde (100 pts)": 100.0,
                "Sim, na maior parte das escolas e centros de saúde (50 pts)": 50.0,
                "Sim, na menor parte das escolas e centros de saúde (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="9.0",
                titulo="9.0 • Escolas e Saúde",
                pergunta="O Município realizou um estudo de avaliação da estrutura de todas as escolas e unidades de saúde para garantir que, em caso de desastre, esses locais estejam preparados para abrigar e atender a população afetada?",
                opcoes=opcoes_90,
                pontuacao_maxima=100.0,
                placeholder_link="Ex: Link do estudo, relatório estrutural, laudos das edificações...",
                on_save_callback=container_formulario_icidade.refresh,
            )

           # =============================================================================
            # QUESITO 10.0 • PLANO DE MOBILIDADE URBANA
            # =============================================================================
            opcoes_100 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-100 pts)": -100.0,
                "Não se aplica (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="10.0",
                titulo="Plano de Mobilidade Urbana",
                pergunta="O Município elaborou seu Plano de Mobilidade Urbana?",
                opcoes=opcoes_100,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.0 • TRANSPORTE PÚBLICO COLETIVO
            # =============================================================================
            opcoes_110 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.0",
                titulo="Existência de Transporte Público Coletivo",
                pergunta="No Município existe transporte público coletivo?",
                opcoes=opcoes_110,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.1 • METAS DE QUALIDADE E DESEMPENHO
            # =============================================================================
            opts111 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.1",
                titulo="Metas de Qualidade e Desempenho",
                pergunta="Foram estabelecidas metas de qualidade e desempenho para o transporte público coletivo municipal?",
                opcoes=opts111,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.1.1 • ATENDIMENTO DAS METAS
            # =============================================================================
            opts1111 = {
                "Selecione...": 0.0,
                "Todas as metas foram atingidas (00 pts)": 0.0,
                "A maior parte das metas foram atingidas (-05 pts)": -5.0,
                "A menor parte das metas foram atingidas (-10 pts)": -10.0,
                "As metas não foram atingidas (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.1.1",
                titulo="Atingimento de Metas de Desempenho",
                pergunta="As metas de qualidade e desempenho estão sendo atingidas?",
                opcoes=opts1111,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.1.1.1 • APLICAÇÃO DE PENALIDADES
            # =============================================================================
            opcoes_11111 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-50 pts)": -50.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.1.1.1",
                titulo="Aplicação de Sanções Administrativas",
                pergunta="Foi aplicada penalidade pela meta não cumprida?",
                opcoes=opcoes_11111,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.2 • PESQUISA DE SATISFAÇÃO DO USUÁRIO
            # =============================================================================
            ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
            ano_anterior = (
                int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"
            )

            opcoes_112 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.2",
                titulo="Pesquisa de Satisfação dos Usuários",
                pergunta=f"Foi realizada pesquisa de satisfação dos usuários em {ano_anterior}?",
                opcoes=opcoes_112,
                on_save_callback=container_formulario_icidade.refresh,
            )

# =============================================================================
            # QUESITO 11.2.1 • AÇÕES BASEADAS NA PESQUISA DE SATISFAÇÃO
            # =============================================================================
            opcoes_1121 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.2.1",
                titulo="Ações Pós-Pesquisa de Satisfação",
                pergunta="Foram realizadas ações com base nesta pesquisa?",
                opcoes=opcoes_1121,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.3 • RESULTADO FINANCEIRO DO TRANSPORTE
            # =============================================================================
            ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
            ano_anterior = (
                int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"
            )

            opcoes_113 = {
                "Selecione...": 0.0,
                "Déficit ou subsídio tarifário": 0.0,
                "Superávit tarifário": 0.0,
                "Não sabe informar": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.3",
                titulo="Resultado Financeiro do Transporte Público",
                pergunta=f"Quanto ao custo do transporte público (tarifa de remuneração) e o preço de passagem (tarifa pública), informe qual o resultado no ano de {ano_anterior}:",
                opcoes=opcoes_113,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 11.3.1 • TRANSPARÊNCIA TARIFÁRIA
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="11.3.1",
                titulo="Transparência dos Benefícios Tarifários",
                pergunta="Informe a página eletrônica (link na internet) em que os benefícios tarifários foram divulgados. Caso não esteja disponível, informe 'XYZ':",
                opcoes=None,  # Campo textual/link
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 12.0 • TRANSPORTE POR APLICATIVO
            # =============================================================================
            opcoes_120 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.0",
                titulo="Transporte Remunerado Privado Individual (App)",
                pergunta="O Município possui transporte remunerado privado individual (App)?",
                opcoes=opcoes_120,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 12.1 • REGULAMENTAÇÃO DE APP
            # =============================================================================
            opts121 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-50 pts)": -50.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.1",
                titulo="Regulamentação do Transporte por Aplicativo",
                pergunta="O Município regulamentou o transporte remunerado privado individual?",
                opcoes=opts121,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 12.1.1 • IDENTIFICAÇÃO DA REGULAMENTAÇÃO
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.1.1",
                titulo="Identificação do Instrumento Normativo",
                pergunta="Informe o Instrumento normativo, Número e Data da publicação:",
                opcoes=None,  # Campo textual
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 12.1.2 • ENDEREÇO ELETRÔNICO DA REGULAMENTAÇÃO
            # =============================================================================
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.1.2",
                titulo="Endereço Eletrônico da Norma",
                pergunta="Informe a página eletrônica (link na internet) do instrumento:",
                opcoes=None,  # Campo textual/link
                on_save_callback=container_formulario_icidade.refresh,
            )

# =============================================================================
            # QUESITO 12.1.3 • FISCALIZAÇÃO DO SERVIÇO APP
            # =============================================================================
            opcoes_1213 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-50 pts)": -50.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.1.3",
                titulo="Fiscalização Regular do Transporte por Aplicativo",
                pergunta="O Município fiscaliza regularmente o transporte remunerado privado individual de passageiros (táxi por aplicativo)?",
                opcoes=opcoes_1213,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 12.1.3.1 • PERIODICIDADE DA FISCALIZAÇÃO
            # =============================================================================
            opcoes_12131 = {
                "Selecione...": 0.0,
                "Diariamente": 0.0,
                "Semanalmente": 0.0,
                "Mensalmente": 0.0,
                "Anualmente": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="12.1.3.1",
                titulo="Periodicidade e Evidência das Ações",
                pergunta="Informe a periodicidade da fiscalização realizada e anexe o comprovante correspondente:",
                opcoes=opcoes_12131,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 13.0 • MOBILIDADE ATIVA
            # =============================================================================
            ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
            ano_anterior = (
                int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"
            )

            opcoes_130 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="13.0",
                titulo="Estímulo à Mobilidade Ativa e Não Motorizada",
                pergunta=f"Foram realizadas ações para estimular a adoção/uso dos meios de transporte não motorizados em {ano_anterior}?",
                opcoes=opcoes_130,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 13.1 • AÇÕES DE MOBILIDADE ATIVA REALIZADAS (CHECKBOXES)
            # =============================================================================
            ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
            ano_anterior = (
                int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"
            )

            # Opções em formato de dicionário para compatibilidade com o padrão
            opcoes_131 = {
                "Instalação/manutenção de ciclovias ou ciclofaixas": 0.0,
                "Instalação/manutenção de pontos de locação de bicicletas": 0.0,
                "Instalação/manutenção de pontos de locação de patinetes": 0.0,
                "Outras": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="13.1",
                titulo=f"Detalhamento das Ações Realizadas em {ano_anterior}",
                pergunta=f"Assinale as ações realizadas para estimular a adoção/uso dos meios de transporte não motorizados em {ano_anterior}:",
                opcoes=opcoes_131,
                tipo="checkbox",  # <--- Define o tipo de componente para Checkbox
                on_save_callback=container_formulario_icidade.refresh,
            )
            # =============================================================================
            # QUESITO 13.1.1 • CRONOGRAMA DE MANUTENÇÃO
            # =============================================================================
            opcoes_1311 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="13.1.1",
                titulo="Cronograma de Manutenção da Infraestrutura",
                pergunta="Possui um cronograma de manutenção da infraestrutura das ciclovias ou ciclofaixas?",
                opcoes=opcoes_1311,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 13.1.1.1 • CUMPRIMENTO DAS MANUTENÇÕES PREVENTIVAS
            # =============================================================================
            opcoes_13111 = {
                "Selecione...": 0.0,
                "Sim, para todos os trechos (00 pts)": 0.0,
                "Sim, para a maior parte dos trechos (-05 pts)": -5.0,
                "Sim, para a menor parte dos trechos (-10 pts)": -10.0,
                "Não foram realizadas dentro do prazo (-15 pts)": -15.0,
                "Não foram realizadas manutenções preventivas no exercício (-20 pts)": -20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="13.1.1.1",
                titulo="Cumprimento e Execução das Manutenções Preventivas",
                pergunta="As manutenções preventivas da infraestrutura das ciclovias ou ciclofaixas foram realizadas dentro do prazo?",
                opcoes=opcoes_13111,
                on_save_callback=container_formulario_icidade.refresh,
            )
           
            # =============================================================================
            # QUESITO 14.0 • ACESSIBILIDADE EM CALÇAMENTOS PÚBLICOS
            # =============================================================================
            opcoes_140 = {
                "Selecione...": 0.0,
                "Sim, integralmente - Todos os calçamentos públicos (00 pts)": 0.0,
                "Sim, parcialmente - Em parte dos calçamentos públicos (-10 pts)": -10.0,
                "Não possui acessibilidade em calçamentos públicos (-50 pts)": -50.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="14.0",
                titulo="Adequação de Calçamentos Públicos para Acessibilidade",
                pergunta="O Município adequou os calçamentos públicos para acessibilidade (PcD e restrição de mobilidade)?",
                opcoes=opcoes_140,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 14.1 • RECURSOS DE ACESSIBILIDADE OFERECIDOS (CHECKBOXES)
            # =============================================================================
            opcoes_141 = {
                "Calçadas com dimensões mínimas para a circulação": 0.0,
                "Sinalização tátil em pisos": 0.0,
                "Rampas de acesso": 0.0,
                "Escadas com corrimão": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="14.1",
                titulo="Detalhamento dos Recursos de Acessibilidade",
                pergunta="Informe os recursos de acessibilidade oferecidos pela Prefeitura:",
                opcoes=opcoes_141,
                tipo="checkbox",  # <--- Define o tipo de componente para Checkbox
                on_save_callback=container_formulario_icidade.refresh,
            )
            # =============================================================================
            # QUESITO 15.0 • SINALIZAÇÃO VIÁRIA MUNICIPAL
            # =============================================================================
            opcoes_150 = {
                "Selecione...": 0.0,
                "Sim, integralmente - Todas as vias públicas municipais (50 pts)": 50.0,
                "Sim, parcialmente - Em parte das vias municipais (10 pts)": 10.0,
                "Não estão sinalizadas (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="15.0",
                titulo="Condições de Sinalização Vertical e Horizontal",
                pergunta="As vias públicas pavimentadas estão devidamente sinalizadas (vertical e horizontalmente) de forma a garantir as condições adequadas de segurança na circulação?",
                opcoes=opcoes_150,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 16.0 • MANUTENÇÃO DE VIAS PÚBLICAS
            # =============================================================================
            opcoes_160 = {
                "Selecione...": 0.0,
                "Sim, integralmente - Todas as vias públicas municipais (50 pts)": 50.0,
                "Sim, parcialmente - Em parte das vias municipais (10 pts)": 10.0,
                "Não estão adequadas (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="16.0",
                titulo="Condições de Manutenção Viária e Pavimentação",
                pergunta="Há manutenção adequada das vias públicas no Município?",
                opcoes=opcoes_160,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # QUESITO 17.1 • ENCERRAMENTO E FEEDBACK
            # =============================================================================
            opcoes_171 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="17.1",
                titulo="Registro de Impressões e Sugestões",
                pergunta="Utilize o espaço abaixo para registrar suas impressões e sugestões sobre o questionário.",
                opcoes=opcoes_171,
                on_save_callback=container_formulario_icidade.refresh,
            )

            # =============================================================================
            # SEÇÃO: DADOS EXTERNOS DO i-CIDADE
            # =============================================================================
            ui.markdown("## 🌐 DADOS EXTERNOS DO i-CIDADE")

            # =============================================================================
            # QUESITO C1 • ONU MCR2030
            # =============================================================================
            opcoes_c1 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="C1",
                titulo="Programa Construindo Cidades Resilientes (MCR2030) da ONU",
                pergunta="O Município estava inscrito no Programa Construindo Cidades Resilientes 2030 da ONU?",
                opcoes=opcoes_c1,
                on_save_callback=container_formulario_icidade.refresh,
            )

           # Executa a renderização da interface
    render_conteudo()
                
# Exporta referências principais para o aplicativo
mostrar_formulario_iamb = container_formulario_icidade
main = container_formulario_icidade
