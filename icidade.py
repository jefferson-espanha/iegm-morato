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

# Connection string configurada para o seu cluster no Neon
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    """Cria e retorna uma conexão ativa com a base de dados do Neon."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def load_respostas(ano):
    """Carrega o dicionário de respostas salvas para o ano selecionado no Neon DB."""
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_icidade
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
                        "valor": (
                            row["valor"] if row["valor"] is not None else ""
                        ),
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": link_val,
                        "comentarios": (
                            row["comentarios"]
                            if isinstance(row["comentarios"], list)
                            else []
                        ),
                        "status": (
                            row["status"]
                            if row["status"] is not None
                            else "Pendente"
                        ),
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB: {e}")
        ui.notify(f"Erro ao carregar dados do banco Neon: {e}", type="negative")

    return respostas


def save_resposta(
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente"
):
    """Salva a resposta, link, pontos e o histórico de comentários de um quesito no Neon DB.

    Utiliza UPSERT (ON CONFLICT) para atualizar se já existir.
    """
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
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
        print(f"❌ Erro ao salvar resposta no Neon DB: {e}")
        ui.notify(f"Erro ao salvar no banco Neon: {e}", type="negative")


def zerar_questionario_db(ano):
    """Limpa todas as respostas salvas do ano selecionado na tabela do Neon DB."""
    query = "DELETE FROM respostas_icidade WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")
        ui.notify(f"Erro ao apagar dados no banco Neon: {e}", type="negative")


def _obter_lista_comentarios(dados_banco):
    """Garante que o retorno de 'comentarios' seja sempre uma lista Python válida."""
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

# =============================================================================
# 1. PAINEL LATERAL / CONTROLE
# =============================================================================
def render_painel_controle(on_refresh_callback=None):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_atual = app.storage.user.get("ano_referencia_global", 2026)

    with ui.card().classes(
        "w-full bg-slate-100 p-4 border rounded-lg shadow-sm"
    ):
        ui.label("🛠️ Painel de Controle").classes(
            "text-lg font-bold mb-2 text-blue-900"
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

        # Cálculo de Pontuação e Faixa (busca direto do Neon)
        res_data = load_respostas(ano_atual)
        total_pts = sum(
            float(item.get("pontos", 0)) for item in res_data.values()
        )

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

        # Card de Pontuação
        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("Pontuação Total").classes(
                "text-xs text-gray-500 font-bold uppercase"
            )
            ui.label(f"{total_pts:.1f} pts").classes(
                "text-2xl font-black text-gray-800"
            )

            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        ui.separator().classes("my-2")
        ui.label("⚙️ Gerenciamento").classes("font-bold text-sm mb-2")

        def atualizar_dados():
            ui.notify(
                "Questionário atualizado!", type="positive", icon="refresh"
            )
            if on_refresh_callback:
                on_refresh_callback()

        ui.button("🔄 Atualizar Questionário", on_click=atualizar_dados).classes(
            "w-full bg-blue-700 text-white mb-2"
        )
        ui.separator().classes("my-2")

        # Modal de Confirmação para Zerar
        with ui.dialog() as dialog_zerar, ui.card().classes("w-96 p-4"):
            ui.label("🔒 Confirmação de Segurança").classes(
                "text-lg font-bold text-red-600"
            )
            ui.label(
                f"Você está prestes a apagar todas as respostas de {ano_atual}. Esta ação é irreversível!"
            ).classes("text-sm my-2")

            input_senha = ui.input(
                "Digite a senha de administrador:", password=True
            ).classes("w-full mb-4")

            def executar_zerar():
                if input_senha.value == "fidelios":
                    zerar_questionario_db(ano_atual)
                    ui.notify(
                        f"✅ Questionário de {ano_atual} foi zerado!",
                        type="positive",
                    )
                    dialog_zerar.close()
                    if on_refresh_callback:
                        on_refresh_callback()
                else:
                    ui.notify("❌ Senha incorreta!", type="negative")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancelar", on_click=dialog_zerar.close).props("flat")
                ui.button("Confirmar e Zerar", on_click=executar_zerar).classes(
                    "bg-red-600 text-white"
                )

        with ui.row().classes("w-full gap-2 no-wrap"):
            pdf_bytes = gerar_relatorio_pdf_bytes(
                res_data, ano_atual, total_pts, faixa
            )
            ui.button(
                "📄 Relatório",
                on_click=lambda: ui.download(
                    pdf_bytes, f"Relatorio_iCidade_{ano_atual}.pdf"
                ),
            ).classes("flex-1 bg-green-700 text-white")
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
    tipo="radio",  # 'radio', 'checkbox', ou 'text'
    informativo=False,
    is_text_area=False,
    placeholder_text="Cole os links ou informações aqui...",
    placeholder_link="Link de Evidência / Documento:",
    pontuacao_maxima=None,
    **kwargs,
):
    d_data = res_data.get(qid, {})

    with ui.card().classes("w-full mb-4 p-4 border rounded-lg shadow-sm"):
        with ui.expansion(f"📌 Quesito {qid} - {titulo}", value=True).classes(
            "w-full font-bold"
        ):
            ui.label(f"{qid} • {titulo}").classes("text-h6 text-primary mt-2")
            ui.label(pergunta).classes("text-body1 font-bold my-2")
            ui.label(
                "ℹ Preencha os campos abaixo e clique no botão de salvar."
            ).classes("text-caption text-grey-6 mb-4")

            with ui.row().classes("w-full gap-4 items-start"):
                # Coluna das Opções / Entrada
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
                            label="Dados do quesito:",
                            placeholder=placeholder_text,
                            value=d_data.get("valor", ""),
                        ).classes("w-full").props("outlined rows=3")

                # Coluna de Links / Evidências
                with ui.column().classes("flex-1"):
                    input_link = ui.textarea(
                        label="Link de Evidência / Documento:",
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
                                ui.label("Links Ativos: ").classes(
                                    "font-bold text-caption"
                                )
                                for url in links:
                                    ui.link(url, target=url, new_tab=True).classes(
                                        "text-caption text-blue-6 mr-2"
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
                        f"<span style='color:#6c757d;'>📊 Impacto de Pontuação no Quesito {qid}: 0.0 pontos (Informativo)</span>"
                    )
                else:
                    cor = (
                        "#28a745"
                        if pts > 0
                        else (
                            "#dc3545" if val != "Selecione..." else "#6c757d"
                        )
                    )
                    lbl_pontos.set_content(
                        f"<span style='color:{cor};'>📊 Impacto de Pontuação no Quesito {qid}: {pts:.1f} pontos</span>"
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
                "bg-blue-800 text-white mt-4"
            )

            # Renderiza o bloco de comentários
            bloco_comentarios(qid, res_data, on_save_callback=on_save_callback)

# =============================================================================
# 4. CONTAINER PRINCIPAL REFRESHABLE
# =============================================================================
@ui.refreshable
def container_formulario_icidade():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes('w-full gap-6 items-start'):
        
        # Coluna da Esquerda (Painel de Controle)
        with ui.column().classes('col-span-1 w-full'):
            render_painel_controle(on_refresh_callback=container_formulario_icidade.refresh)

        # Coluna da Direita (Quesitos do Formulário)
        with ui.column().classes('col-span-3 w-full'):
            ui.label(f"Formulário COMPDEC - Defesa Civil ({ano_sel})").classes('text-h4 mb-1 font-bold text-blue-900')
            ui.label("Preencha as evidências e questões do indicador i-Cidade.").classes('text-gray-600 mb-6')

            # QUESITO 1.0
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Criação da COMPDEC ou Órgão Similar",
                pergunta="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil-COMPDEC ou órgão similar responsável pela execução, coordenação e mobilização de todas as ações de defesa civil no município?",
                opcoes=opcoes_10,
                on_save_callback=container_formulario_icidade.refresh
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
                on_save_callback=container_formulario_icidade.refresh
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
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.3
            opcoes_13 = {
                "Selecione...": 0.0,
                "Gabinete do Prefeito (05 pts)": 5.0,
                "Segurança Pública (00 pts)": 0.0,
                "Controladoria (00 pts)": 0.0,
                "Outra (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.3",
                titulo="Secretaria ou Diretoria de Subordinação",
                pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
                opcoes=opcoes_13,
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.4
            opcoes_14 = {
                "Selecione...": 0.0,
                "Sim, inclusive com a participação de entidades privadas e da comunidade (50 pts)": 50.0,
                "Sim, com participação de entidades privadas (20 pts)": 20.0,
                "Sim, com participação da comunidade (20 pts)": 20.0,
                "Sim, apenas com representantes da administração municipal (10 pts)": 10.0,
                "Não atuam de forma sistêmica (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.4",
                titulo="Atuação Sistêmica e Articulação da Defesa Civil",
                pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a Política Nacional de Proteção e Defesa Civil - PNPDEC?",
                opcoes=opcoes_14,
                on_save_callback=container_formulario_icidade.refresh
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
                titulo="• Capacitação da Equipe da COMPDEC",
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
                titulo="• Ações Educativas e Preventivas na Comunidade",
                pergunta="A COMPDEC promoveu ações educativas, campanhas de sensibilização ou oficinas sobre percepção de risco para a população no ano de referência?",
                opcoes=opcoes_21,
                on_save_callback=container_formulario_icidade.refresh
            )

            # =============================================================================
            # QUESITO 2.2 • PÚBLICO-ALVO DOS CURSOS E TREINAMENTOS
            # =============================================================================
            opcoes_22 = {
                "Apenas para escolas (05 pts)": 5.0,
                "Apenas para outras secretarias / entidades municipais (03 pts)": 3.0,
                "Apenas para munícipes ou empresas (02 pts)": 2.0,
                "Não ofereceu nenhum curso/treinamento no ano (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.2",
                titulo="• Público Alvo de Cursos e Treinamentos",
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
    # QUESITO 3.1.1 • DATA DE TREINAMENTO
    # =============================================================================
    # Caso a render_quesito não suporte seletores de data ou callbacks customizados,
    # mapeamos as faixas/regras diretamente no dicionário de opções:
    opcoes_311 = {
        "Selecione...": 0.0,
        f"A partir de 01/01/{ano_sel} (10 pts)": 10.0,
        f"Até 31/12/{ano_sel - 1} ou sem treinamento (00 pts)": 0.0
    }

    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="3.1.1",
        titulo="3.1.1 • Data do Último Treinamento de Voluntários",
        pergunta="Qual a data do último treinamento de associações de voluntários?",
        opcoes=opcoes_311,
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
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="8.1",
        titulo="Canais de Comunicação Disponíveis",
        pergunta="Assinale os canais que o município possui:",
        tipo="checkbox",
        opcoes=[
            "Telefone de emergências",
            "Aplicativo de mensagens",
            "Correio eletrônico (e-mail)",
            "Aplicativo da Prefeitura",
            "Site da Prefeitura",
            "Redes sociais",
            "Outros"
        ],
        pontuacao_maxima=0.0,
        informativo=True,
        placeholder_link="Descreva os números, endereços eletrônicos ou insira os links dos canais...",
        on_save_callback=container_formulario_icidade.refresh
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
        titulo="10.0 • Mobilidade Urbana",
        pergunta="O Município elaborou seu Plano de Mobilidade Urbana?",
        opcoes=opcoes_100,
        pontuacao_maxima=0.0,
        placeholder_link="Ex: Link do plano publicado, lei municipal ou justificativa legal de não aplicabilidade...",
        on_save_callback=container_formulario_icidade.refresh,
    )

    # =============================================================================
    # QUESITO 11.0 • TRANSPORTE PÚBLICO COLETIVO
    # =============================================================================
    opcoes_110 = {
        "Selecione...": 0.0,
        "Sim": 0.0,
        "Não": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.0",
        titulo="Existência de Transporte Público Coletivo",
        pergunta="No Município existe transporte público coletivo?",
        opcoes=opcoes_110,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 11.1 • METAS DE QUALIDADE E DESEMPENHO
    # =============================================================================
    opts111 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.1",
        titulo="Metas de Qualidade e Desempenho",
        pergunta="Foram estabelecidas metas de qualidade e desempenho para o transporte público coletivo municipal?",
        opcoes=opts111,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 11.1.1 • ATENDIMENTO DAS METAS
    # =============================================================================
    opts1111 = {
        "Selecione...": 0.0,
        "Todas as metas foram atingidas (00 pts)": 0.0,
        "A maior parte das metas foram atingidas (-05 pts)": -5.0,
        "A menor parte das metas foram atingidas (-10 pts)": -10.0,
        "As metas não foram atingidas (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.1.1",
        titulo="Atingimento de Metas de Desempenho",
        pergunta="As metas de qualidade e desempenho estão sendo atingidas?",
        opcoes=opts1111,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 11.1.1.1 • APLICAÇÃO DE PENALIDADES
    # =============================================================================
    opcoes_11111 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-50 pts)": -50.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.1.1.1",
        titulo="Aplicação de Sanções Administrativas",
        pergunta="Foi aplicada penalidade pela meta não cumprida?",
        opcoes=opcoes_11111,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 11.2 • PESQUISA DE SATISFAÇÃO DO USUÁRIO
    # =============================================================================
    ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
    ano_anterior = int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"

    opcoes_112 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.2",
        titulo="Pesquisa de Satisfação dos Usuários",
        pergunta=f"Foi realizada pesquisa de satisfação dos usuários em {ano_anterior}?",
        opcoes=opcoes_112,
        on_save_callback=container_formulario_icidade.refresh
    )

# =============================================================================
    # QUESITO 11.2.1 • AÇÕES BASEADAS NA PESQUISA DE SATISFAÇÃO
    # =============================================================================
    opcoes_1121 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.2.1",
        titulo="Ações Pós-Pesquisa de Satisfação",
        pergunta="Foram realizadas ações com base nesta pesquisa?",
        opcoes=opcoes_1121,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 11.3 • RESULTADO FINANCEIRO DO TRANSPORTE
    # =============================================================================
    ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
    ano_anterior = int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"

    opcoes_113 = {
        "Selecione...": 0.0,
        "Déficit ou subsídio tarifário": 0.0,
        "Superávit tarifário": 0.0,
        "Não sabe informar": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="11.3",
        titulo="Resultado Financeiro do Transporte Público",
        pergunta=f"Quanto ao custo do transporte público (tarifa de remuneração) e o preço de passagem (tarifa pública), informe qual o resultado no ano de {ano_anterior}:",
        opcoes=opcoes_113,
        on_save_callback=container_formulario_icidade.refresh
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
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 12.0 • TRANSPORTE POR APLICATIVO
    # =============================================================================
    opcoes_120 = {
        "Selecione...": 0.0,
        "Sim": 0.0,
        "Não": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="12.0",
        titulo="Transporte Remunerado Privado Individual (App)",
        pergunta="O Município possui transporte remunerado privado individual (App)?",
        opcoes=opcoes_120,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 12.1 • REGULAMENTAÇÃO DE APP
    # =============================================================================
    opts121 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-50 pts)": -50.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="12.1",
        titulo="Regulamentação do Transporte por Aplicativo",
        pergunta="O Município regulamentou o transporte remunerado privado individual?",
        opcoes=opts121,
        on_save_callback=container_formulario_icidade.refresh
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
        on_save_callback=container_formulario_icidade.refresh
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
        on_save_callback=container_formulario_icidade.refresh
    )

# =============================================================================
    # QUESITO 12.1.3 • FISCALIZAÇÃO DO SERVIÇO APP
    # =============================================================================
    opcoes_1213 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-50 pts)": -50.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="12.1.3",
        titulo="Fiscalização Regular do Transporte por Aplicativo",
        pergunta="O Município fiscaliza regularmente o transporte remunerado privado individual de passageiros (táxi por aplicativo)?",
        opcoes=opcoes_1213,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 12.1.3.1 • PERIODICIDADE DA FISCALIZAÇÃO
    # =============================================================================
    opcoes_12131 = {
        "Selecione...": 0.0,
        "Diariamente": 0.0,
        "Semanalmente": 0.0,
        "Mensalmente": 0.0,
        "Anualmente": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="12.1.3.1",
        titulo="Periodicidade e Evidência das Ações",
        pergunta="Informe a periodicidade da fiscalização realizada e anexe o comprovante correspondente:",
        opcoes=opcoes_12131,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 13.0 • MOBILIDADE ATIVA
    # =============================================================================
    ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
    ano_anterior = int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"

    opcoes_130 = {
        "Selecione...": 0.0,
        "Sim": 0.0,
        "Não": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="13.0",
        titulo="Estímulo à Mobilidade Ativa e Não Motorizada",
        pergunta=f"Foram realizadas ações para estimular a adoção/uso dos meios de transporte não motorizados em {ano_anterior}?",
        opcoes=opcoes_130,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 13.1 • AÇÕES DE MOBILIDADE ATIVA REALIZADAS
    # =============================================================================
    ano_puro = "".join([c for c in str(ano_sel) if c.isdigit()])[:4]
    ano_anterior = int(ano_puro) - 1 if ano_puro.isdigit() else "anterior"

    # Opções em formato de dicionário para compatibilidade com o padrão
    opcoes_131 = {
        "Instalação/manutenção de ciclovias ou ciclofaixas": 0.0,
        "Instalação/manutenção de pontos de locação de bicicletas": 0.0,
        "Instalação/manutenção de pontos de locação de patinetes": 0.0,
        "Outras": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="13.1",
        titulo=f"Detalhamento das Ações Realizadas em {ano_anterior}",
        pergunta=f"Assinale as ações realizadas para estimular a adoção/uso dos meios de transporte não motorizados em {ano_anterior}:",
        opcoes=opcoes_131,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 13.1.1 • CRONOGRAMA DE MANUTENÇÃO
    # =============================================================================
    opcoes_1311 = {
        "Selecione...": 0.0,
        "Sim (00 pts)": 0.0,
        "Não (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="13.1.1",
        titulo="Cronograma de Manutenção da Infraestrutura",
        pergunta="Possui um cronograma de manutenção da infraestrutura das ciclovias ou ciclofaixas?",
        opcoes=opcoes_1311,
        on_save_callback=container_formulario_icidade.refresh
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
        "Não foram realizadas manutenções preventivas no exercício (-20 pts)": -20.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="13.1.1.1",
        titulo="Cumprimento e Execução das Manutenções Preventivas",
        pergunta="As manutenções preventivas da infraestrutura das ciclovias ou ciclofaixas foram realizadas dentro do prazo?",
        opcoes=opcoes_13111,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 14.0 • ACESSIBILIDADE EM CALÇAMENTOS PÚBLICOS
    # =============================================================================
    opcoes_140 = {
        "Selecione...": 0.0,
        "Sim, integralmente - Todos os calçamentos públicos (00 pts)": 0.0,
        "Sim, parcialmente - Em parte dos calçamentos públicos (-10 pts)": -10.0,
        "Não possui acessibilidade em calçamentos públicos (-50 pts)": -50.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="14.0",
        titulo="Adequação de Calçamentos Públicos para Acessibilidade",
        pergunta="O Município adequou os calçamentos públicos para acessibilidade (PcD e restrição de mobilidade)?",
        opcoes=opcoes_140,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 14.1 • RECURSOS DE ACESSIBILIDADE OFERECIDOS
    # =============================================================================
    opcoes_141 = {
        "Calçadas com dimensões mínimas para a circulação": 0.0,
        "Sinalização tátil em pisos": 0.0,
        "Rampas de acesso": 0.0,
        "Escadas com corrimão": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="14.1",
        titulo="Detalhamento dos Recursos de Acessibilidade",
        pergunta="Informe os recursos de acessibilidade oferecidos pela Prefeitura:",
        opcoes=opcoes_141,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 15.0 • SINALIZAÇÃO VIÁRIA MUNICIPAL
    # =============================================================================
    opcoes_150 = {
        "Selecione...": 0.0,
        "Sim, integralmente - Todas as vias públicas municipais (50 pts)": 50.0,
        "Sim, parcialmente - Em parte das vias municipais (10 pts)": 10.0,
        "Não estão sinalizadas (00 pts)": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="15.0",
        titulo="Condições de Sinalização Vertical e Horizontal",
        pergunta="As vias públicas pavimentadas estão devidamente sinalizadas (vertical e horizontalmente) de forma a garantir as condições adequadas de segurança na circulação?",
        opcoes=opcoes_150,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 16.0 • MANUTENÇÃO DE VIAS PÚBLICAS
    # =============================================================================
    opcoes_160 = {
        "Selecione...": 0.0,
        "Sim, integralmente - Todas as vias públicas municipais (50 pts)": 50.0,
        "Sim, parcialmente - Em parte das vias municipais (10 pts)": 10.0,
        "Não estão adequadas (00 pts)": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="16.0",
        titulo="Condições de Manutenção Viária e Pavimentação",
        pergunta="Há manutenção adequada das vias públicas no Município?",
        opcoes=opcoes_160,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO 17.1 • ENCERRAMENTO E FEEDBACK
    # =============================================================================
    opcoes_171 = {
        "Selecione...": 0.0,
        "Sim": 0.0,
        "Não": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="17.1",
        titulo="Registro de Impressões e Sugestões",
        pergunta="Utilize o espaço abaixo para registrar suas impressões e sugestões sobre o questionário.",
        opcoes=opcoes_171,
        on_save_callback=container_formulario_icidade.refresh
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
        "Não": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="C1",
        titulo="Programa Construindo Cidades Resilientes (MCR2030) da ONU",
        pergunta="O Município estava inscrito no Programa Construindo Cidades Resilientes 2030 da ONU?",
        opcoes=opcoes_c1,
        on_save_callback=container_formulario_icidade.refresh
    )

    # =============================================================================
    # QUESITO C1.1 • ESTÁGIO MCR2030 DA ONU
    # =============================================================================
    opcoes_c11 = {
        "Selecione...": 0.0,
        "Etapa A (10 pts)": 10.0,
        "Etapa B (20 pts)": 20.0,
        "Etapa C (50 pts)": 50.0,
        "Não classificada (00 pts)": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="C1.1",
        titulo="Estágio de Classificação no Programa MCR2030",
        pergunta="O Município foi classificado em qual estágio do Programa?",
        opcoes=opcoes_c11,
        on_save_callback=container_formulario_icidade.refresh
    )

    import os
    from io import BytesIO
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    )


    def gerar_relatorio_pdf_bytes(dados, ano, total, faixa):
        """Ponte para o NiceGUI: Chama o ReportLab e retorna os bytes do PDF."""
        buffer = gerar_relatorio_pdf(dados, ano, total, faixa)
        return buffer.getvalue()


# =============================================================================
# 3. GERADOR DO RELATÓRIO PDF (INDENTAÇÃO DE 4 ESPAÇOS)
# =============================================================================
def gerar_relatorio_pdf(dados, ano, total, faixa):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30,
    )
    elements = []
    styles = getSampleStyleSheet()

    # Função auxiliar de higienização de texto
    def tratar_texto(val):
        if isinstance(val, list):
            val = ", ".join(map(str, val))
        val_str = str(val) if val is not None else ""
        return (
            val_str.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .strip()
        )

        # -------------------------------------------------------------------------
        # FOLHA 1: CAPA
        # -------------------------------------------------------------------------
        elements.append(Spacer(1, 100))
        
        logo_path = "iegm.png"
        if os.path.exists(logo_path):
            try:
                from reportlab.platypus import Image
                logo = Image(logo_path, width=380, height=180)
                logo.hAlign = 'CENTER'
                elements.append(logo)
            except Exception:
                elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
        else:
            elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
            
        elements.append(Spacer(1, 50))
        
        style_titulo_capa = ParagraphStyle(
            'TituloCapa', 
            parent=styles['Normal'], 
            fontName='Helvetica-Bold', 
            fontSize=24, 
            textColor=colors.HexColor("#2c3e50"), 
            alignment=1  # Centralizado
        )

        elements.append(Paragraph("Relatório I-Cidade", style_titulo_capa))
        elements.append(Spacer(1, 15))
        
        style_ano_capa = ParagraphStyle('AnoCapa', parent=styles['Normal'], fontName='Helvetica', fontSize=16, textColor=colors.HexColor("#7f8c8d"), alignment=1)
        elements.append(Paragraph(str(ano), style_ano_capa))
        elements.append(PageBreak())

        # -------------------------------------------------------------------------
        # FOLHA 2: SUMÁRIO
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>SUMÁRIO</b>", styles["h1"]))
        elements.append(Spacer(1, 30))

        style_item_esquerda = ParagraphStyle('ItemEsq', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor("#2c3e50"))
        style_pag_direita = ParagraphStyle('PagDir', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor("#1b4f72"), alignment=2)

        dados_sumario = [
            [Paragraph("1. Resumo Executivo (Análise Comparativa)", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
            [Paragraph("2. Análise de Desempenho por Quesito", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
            [Paragraph("3. Análise de Impacto e Penalidades", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
            [Paragraph("4. Diagnóstico de Reincidências", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
            [Paragraph("5. Alinhamento com a Agenda 2030 (ODS)", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
            [Paragraph("6. Série Histórica do I-cidade", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
            [Paragraph("7. Quesitos Sem Pontuação Direta", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
        ]
        
        tabela_sumario = Table(dados_sumario, colWidths=[400, 90])
        tabela_sumario.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7"), 1, (2, 4)), 
        ]))
        elements.append(tabela_sumario)
        elements.append(PageBreak())

        # -------------------------------------------------------------------------
        # 1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA DE EXERCÍCIOS)
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA)</b>", styles["h2"]))
        elements.append(Spacer(1, 8))

        nota_atual = float(total) if total else 0.0
        ano_atual = int(str(ano).strip()[:4])
        ano_ant = ano_atual - 1

        def converter_pontos_em_faixa_iegm(pontos):
            pts = float(pontos)
            if pts < 500.0:
                return "C"
            elif 500.0 <= pts <= 599.9:
                return "C+"
            elif 600.0 <= pts <= 749.9:
                return "B"
            elif 750.0 <= pts <= 899.9:
                return "B+"
            else:
                return "A"

        all_data = {}
        try:
            all_data = get_all_years_data()  # Presume-se a existência da função externa
        except Exception:
            all_data = {}

        dados_ano_anterior = all_data.get(ano_ant, {})
        nota_anterior = 0.0
        if ano_ant in all_data:
            nota_anterior = float(sum(
                info_ant.get("pontos", 0) 
                for qid_ant, info_ant in dados_ano_anterior.items() 
                if isinstance(info_ant, dict) and not qid_ant.startswith("COM_")
            ))

        faixa_anterior = converter_pontos_em_faixa_iegm(nota_anterior)
        faixa_real_atual = faixa if faixa else converter_pontos_em_faixa_iegm(nota_atual)

        variacao_pontos = nota_atual - nota_anterior
        if nota_anterior > 0:
            variacao_percentual = (variacao_pontos / nota_anterior) * 100
            texto_percentual = f"{variacao_percentual:+.2f}%"
        else:
            texto_percentual = "0.00%"

        if variacao_pontos > 0:
            cor_variacao = colors.HexColor("#28a745")
            seta_tendencia = "▲"
        elif variacao_pontos < 0:
            cor_variacao = colors.HexColor("#dc3545")
            seta_tendencia = "▼"
        else:
            cor_variacao = colors.HexColor("#6c757d")
            seta_tendencia = "■"

        style_th = ParagraphStyle('Th', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.whitesmoke, alignment=1)
        style_td_ano = ParagraphStyle('TdAno', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#2c3e50"), alignment=1)
        style_td_pts = ParagraphStyle('TdPts', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1)
        style_td_faixa = ParagraphStyle('TdFaixa', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor("#1b4f72"), alignment=1)
        style_td_var = ParagraphStyle('TdVar', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=cor_variacao, alignment=1)

        dados_comparativos = [
            [Paragraph("Exercício", style_th), Paragraph("Pontuação Obtida", style_th), Paragraph("Faixa / Conceito", style_th), Paragraph("Variação Nominal", style_th), Paragraph("Variação Percentual", style_th)],
            [Paragraph(str(ano_ant), style_td_ano), Paragraph(f"{nota_anterior:.1f} pts", style_td_pts), Paragraph(str(faixa_anterior), style_td_faixa), Paragraph("-", style_td_var), Paragraph("-", style_td_var)],
            [Paragraph(str(ano_atual), style_td_ano), Paragraph(f"{nota_atual:.1f} pts", style_td_pts), Paragraph(str(faixa_real_atual), style_td_faixa), Paragraph(f"{seta_tendencia} {variacao_pontos:+.1f} pts", style_td_var), Paragraph(f"{seta_tendencia} {texto_percentual}", style_td_var)]
        ]

        tabela_comp = Table(dados_comparativos, colWidths=[80, 105, 95, 105, 105])
        tabela_comp.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")), 
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8f9fa")),
            ("BACKGROUND", (0, 2), (-1, 2), colors.whitesmoke),          
        ]))
        elements.append(tabela_comp)
        elements.append(Spacer(1, 12))

        style_analise = ParagraphStyle('Analise', parent=styles['Normal'], fontSize=10, leading=14)
        if variacao_pontos > 0:
            texto_analise = f"<b>Análise de Tendência:</b> O município registrou uma evolução de desempenho com incremento de <b>{texto_percentual}</b> na sua pontuação global comparado ao exercício de {ano_ant}."
        elif variacao_pontos < 0:
            texto_analise = f"<b>Análise de Tendência:</b> <font color='#dc3545'><b>Alerta de Retrocesso:</b></font> Foi identificada uma redução de <b>{texto_percentual}</b> na eficiência dos indicadores em relação a {ano_ant}."
        else:
            texto_analise = f"<b>Análise de Tendência:</b> O município apresentou estagnação absoluta (0.00%) no seu índice geral de conformidade."

        elements.append(Paragraph(texto_analise, style_analise))
        elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 2. ANÁLISE DE DESEMPENHO POR QUESITO
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>2. ANÁLISE DE DESEMPENHO POR QUESITO</b>", styles["h2"]))
        elements.append(Spacer(1, 6))

        lista_pontos_fortes = []
        lista_pontos_fracos = []
        reincidencias_detectadas = []

        pontuacoes_max = globals().get('PONTUACOES_MAX', {})

        for qid, info in dados.items():
            if qid.startswith("COM_") or not isinstance(info, dict):
                continue
            pts_obtidos = float(info.get("pontos", 0))
            valor_resposta = tratar_texto(info.get("valor", ""))
            link_evidencia = tratar_texto(info.get("link", ""))
            pts_maximo = float(pontuacoes_max.get(qid, 0))
            
            if pts_maximo > 0:
                eficiencia = (pts_obtidos / pts_maximo) * 100
                item_data = {"qid": qid, "pts_obtidos": pts_obtidos, "pts_maximo": pts_maximo, "eficiencia": eficiencia, "valor": valor_resposta, "link": link_evidencia}
                if eficiencia >= 70.0: 
                    lista_pontos_fortes.append(item_data)
                elif eficiencia < 50.0:
                    lista_pontos_fracos.append(item_data)
                    if qid in dados_ano_anterior:
                        info_ant = dados_ano_anterior[qid]
                        pts_anterior = float(info_ant.get("pontos", 0))
                        if pts_obtidos == pts_anterior:
                            reincidencias_detectadas.append({"qid": qid, "tipo": "Ponto Fraco", "detalhe": "Eficiência Crítica", "ant": f"{pts_anterior:.1f} pts", "atual": f"{pts_obtidos:.1f} pts"})

        if lista_pontos_fortes:
            elements.append(Paragraph("<b>✅ Pontos Fortes:</b>", styles["h3"]))
            data_fortes = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
            for item in sorted(lista_pontos_fortes, key=lambda x: x["pts_obtidos"], reverse=True):
                evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
                data_fortes.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
            tabela_fortes = Table(data_fortes, colWidths=[65, 75, 65, 285])
            tabela_fortes.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#28a745")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#28a745")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            elements.append(tabela_fortes)
            elements.append(Spacer(1, 12))

        if lista_pontos_fracos:
            elements.append(Paragraph("<b>⚠️ Pontos Fracos Geral:</b>", styles["h3"]))
            data_fracos = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
            for item in sorted(lista_pontos_fracos, key=lambda x: x["pts_obtidos"]):
                evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
                data_fracos.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
            tabela_fracos = Table(data_fracos, colWidths=[65, 75, 65, 285])
            tabela_fracos.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e67e22")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e67e22")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            elements.append(tabela_fracos)
            elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 3. ANÁLISE DE IMPACTO E PENALIDADES
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>3. ANÁLISE DE IMPACTO E PENALIDADES (EFICIÊNCIA PREVENTIVA)</b>", styles["h2"]))
        elements.append(Spacer(1, 6))

        PENALIDADES_MAX = {"4.2": -50.0, "5.1.1": -100.0, "5.2": -50.0, "6.0": -50.0, "10": -100.0, "10.0": -100.0, "11.1": -20.0, "11.2": -20.0, "11.2.1": -20.0, "12.1.3": -50.0, "14.0": -50.0}

        lista_penalidades = []
        for qid, pen_max in PENALIDADES_MAX.items():
            if qid in dados:
                info = dados[qid]
                nota_real = float(info.get("pontos", 0))
                nota_risco = nota_real if nota_real <= 0 else 0.0
                eficiencia_preventiva = (1.0 - (nota_risco / pen_max)) * 100.0
                lista_penalidades.append({"qid": qid, "nota_real": nota_real, "pen_max": pen_max, "eficiencia": eficiencia_preventiva, "valor": tratar_texto(info.get("valor", "")), "link": tratar_texto(info.get("link", ""))})
                if eficiencia_preventiva < 100.0 and qid in dados_ano_anterior:
                    info_ant = dados_ano_anterior[qid]
                    nota_real_ant = float(info_ant.get("pontos", 0))
                    if nota_real == nota_real_ant:
                        reincidencias_detectadas.append({"qid": qid, "tipo": "Penalidade Aplicada", "detalhe": f"Impacto Recorrente de {nota_real:.1f} pts", "ant": f"{nota_real_ant:.1f} pts", "atual": f"{nota_real:.1f} pts"})

        if lista_penalidades:
            data_penalidades = [["Quesito", "Penalidade Aplicada", "Pior Cenário", "Eficiência Preventiva", "Status de Risco"]]
            for item in sorted(lista_penalidades, key=lambda x: x["eficiencia"]):
                nota_txt = f"{item['nota_real']:.1f} pts"; teto_txt = f"{item['pen_max']:.1f} pts"; ef_txt = f"{item['eficiencia']:.1f}%"
                if item['eficiencia'] == 100.0:
                    status = "<font color='#28a745'><b>Risco Mitigado</b></font>"
                elif item['eficiencia'] <= 0.0:
                    status = "<font color='#dc3545'><b>Impacto Máximo</b></font>"
                else:
                    status = "<font color='#ffc107'><b>Impacto Parcial</b></font>"
                data_penalidades.append([item['qid'], nota_txt, teto_txt, ef_txt, Paragraph(status, styles["Normal"])])
            tabela_pen = Table(data_penalidades, colWidths=[65, 110, 80, 115, 120])
            tabela_pen.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b4f72")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#1b4f72")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            elements.append(tabela_pen)
            elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 4. DIAGNÓSTICO DE REINCIDÊNCIAS 
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>4. DIAGNÓSTICO DE REINCIDÊNCIAS </b>", styles["h2"]))
        elements.append(Spacer(1, 6))
        if reincidencias_detectadas:
            data_reinc = [["Quesito", "Origem da Falha", "Impacto Histórico", "Exercício Anterior", "Exercício Atual"]]
            for reinc in reincidencias_detectadas:
                data_reinc.append([reinc["qid"], reinc["tipo"], Paragraph(f"<b>{reinc['detalhe']}</b>", styles["Normal"]), reinc["ant"], reinc["atual"]])
            tabela_reinc = Table(data_reinc, colWidths=[65, 115, 170, 75, 65])
            tabela_reinc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0392b")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            elements.append(tabela_reinc)
        else: 
            elements.append(Paragraph("<font color='#28a745'><b>Nenhuma reincidência ativa detectada.</b></font>", styles["Normal"]))
        elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)</b>", styles["h2"]))
        elements.append(Spacer(1, 6))
        
        def calcular_percentual_checklist(resposta_bruta, total_itens):
            if not resposta_bruta:
                return 0.0
            resp_clean = tratar_texto(resposta_bruta)
            itens = [i.strip().lower() for i in resp_clean.split(",") if i.strip()]
            itens_validos = [i for i in itens if "outros" not in i]
            return min((len(itens_validos) / total_itens) * 100.0, 100.0) if total_itens > 0 else 0.0

        def chave_ordenacao_qid(qid_str):
            partes = []
            for p in str(qid_str).split('.'):
                p_limpo = ''.join(filter(str.isdigit, p))
                partes.append(int(p_limpo) if p_limpo else 0)
            return partes

        analise_ods = []
        for qid, info in dados.items():
            if qid.startswith("COM_") or not isinstance(info, dict):
                continue
            resp = tratar_texto(info.get("valor", ""))
            resp_l = resp.lower()
            metas = ""
            status = ""

            if qid == "1.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "1.4": metas = "11.5, 16.6"; status = "Não Atendido" if "não atuam de forma sistêmica" in resp_l else "Atendido"
            elif qid == "2.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "3.0": metas = "11.5, 16.7, 16.10, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "3.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
            elif qid == "4.0": metas = "1.5, 11.5, 11b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "5.0": metas = "1.5, 11.5, 16b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "5.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 8):.1f}% Atendido"
            elif qid == "5.1.1": metas = "11b, 11.5, 16.6, 16.10"; status = "Atendido" if ("sim, integralmente" in resp_l or "sim, parcialmente" in resp_l) else "Não Atendido"
            elif qid == "5.1.1.1": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "5.1.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if "não" in resp_l else "Não Atendido"
            elif qid == "5.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if ("sim" in resp_l or "parcialmente" in resp_l) else "Não Atendido"
            elif qid == "7.0": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "7.3": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "7.3.1": metas = "11b, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
            elif qid == "7.4": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "7.4.1": metas = "11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
            elif qid == "7.5": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
            elif qid == "7.6": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
            elif qid in ["8", "8.0"]: metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "8.1": metas = "1.5, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
            elif qid == "8.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "8.1.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "8.2": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "9.0": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("todas as escolas" in resp_l or "maior parte" in resp_l) else "Não Atendido"
            elif qid in ["10", "10.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid in ["11", "11.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "11.1": metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "12.0": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "12.1.3": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid in ["13", "13.0"]: metas = "11.2, 11.7, 12.5"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid == "14.0": metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid in ["15", "15.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
            elif qid in ["16", "16.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"

            if metas:
                analise_ods.append({"qid": qid, "status": status, "metas": metas, "resp": resp[:50]})

        if analise_ods:
            data_ods = [["Quesito", "Resposta Informada", "Vínculo Metas ODS", "Status de Cumprimento"]]
            style_td_ods = ParagraphStyle('TdOds', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=1)
            for item in sorted(analise_ods, key=lambda x: chave_ordenacao_qid(x['qid'])):
                st_txt = item["status"]
                if "Não Atendido" in st_txt:
                    st_p = Paragraph(f"<font color='#dc3545'><b>{st_txt}</b></font>", style_td_ods)
                elif "Atendido" in st_txt and "%" not in st_txt:
                    st_p = Paragraph(f"<font color='#28a745'><b>{st_txt}</b></font>", style_td_ods)
                else:
                    st_p = Paragraph(f"<font color='#007bff'><b>{st_txt}</b></font>", style_td_ods)
                data_ods.append([item["qid"], Paragraph(item["resp"], styles["Normal"]), item["metas"], st_p])
            tabela_ods = Table(data_ods, colWidths=[60, 200, 115, 110])
            tabela_ods.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f9d58")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (0, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#0f9d58")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            elements.append(tabela_ods)
            elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 6. SÉRIE HISTÓRICA DO I-CIDADE
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>6. SÉRIE HISTÓRICA DO I-CIDADE</b>", styles["h2"]))
        elements.append(Spacer(1, 10))

        anos_serie = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
        valores_serie = []
        for a in anos_serie:
            if a == ano_atual: 
                valores_serie.append(nota_atual)
            elif a in all_data:
                valores_serie.append(float(sum(info_h.get("pontos", 0) for qid_h, info_h in all_data[a].items() if isinstance(info_h, dict) and not qid_h.startswith("COM_"))))
            else: 
                valores_serie.append(0.0)

        # Configuração do Gráfico
        desenho_grafico = Drawing(480, 165)
        bc = VerticalBarChart()
        bc.x = 45; bc.y = 25; bc.height = 110; bc.width = 410
        bc.data = [valores_serie]
        bc.categoryAxis.categoryNames = [str(a) for a in anos_serie]
        bc.categoryAxis.labels.fontSize = 9; bc.categoryAxis.labels.fontName = 'Helvetica-Bold'; bc.categoryAxis.labels.dy = -10
        
        bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = 1000; bc.valueAxis.valueStep = 200; bc.valueAxis.labels.fontSize = 8
        
        bc.barLabels.nudge = 8
        bc.barLabels.fontSize = 8
        bc.barLabels.fontName = 'Helvetica-Bold'
        bc.barLabelFormat = '%.1f'
        
        bc.bars[0].fillColor = colors.HexColor("#1b4f72")
        bc.bars[0].strokeColor = colors.HexColor("#2c3e50")
        bc.bars[0].strokeWidth = 0.5

        desenho_grafico.add(String(240, 150, "Série Histórica do I-cidade", textAnchor='middle', fontName='Helvetica-Bold', fontSize=12, fillColor=colors.HexColor("#2c3e50")))
        desenho_grafico.add(bc)
        
        elements.append(desenho_grafico)
        elements.append(Spacer(1, 15))

        # -------------------------------------------------------------------------
        # 7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)
        # -------------------------------------------------------------------------
        elements.append(Paragraph("<b>7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)</b>", styles["h2"]))
        elements.append(Spacer(1, 6))

        lista_alvo_sp = [
            "4.0", "11.0", "12.0", "13.0", "4.1", "5.1", 
            "5.1.2", "5.1.2.1", "7.3.1", "8.4.1", "8.1", "8.1.1", "12.1.3.1", "14.1"
        ]

        analise_sp = []
        
        for qid in lista_alvo_sp:
            info = dados.get(qid) or dados.get(f"Q_{qid}") or {}
            
            if isinstance(info, dict):
                resp = tratar_texto(info.get("valor", ""))
            else:
                resp = tratar_texto(info)

            resp_l = resp.lower()
            is_adequado = False

            if qid in ["4.0", "11.0", "12.0", "13.0", "8.1.1"]:
                if any(x == resp_l or x in resp_l for x in ["sim", "1", "s", "true", "adequado"]):
                    is_adequado = True

            elif qid == "4.1":
                opcoes = ["riscos geológicos", "riscos hidrológicos", "riscos meteorológicos", "riscos biológicos"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "5.1":
                opcoes = ["epidemias", "estiagem", "incêndios", "ondas de calor ou ondas de frio", "inundações"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "5.1.2":
                if any(x == resp_l or x in resp_l for x in ["não", "nao", "0", "n", "false"]):
                    is_adequado = True

            elif qid == "5.1.2.1":
                opcoes = ["aplicação de sanções monetárias (multas)", "monitoramento (fiscalização)", "notificação dos infratores", "demolição das ocupações"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "7.3.1":
                opcoes = ["alerta via sms", "aviso por telefone", "aviso por email", "anúncio por rádio/televisão"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "7.4.1":
                opcoes = ["sinal sonoro (sirene)", "sinal luminoso", "carros de emergência com sirenes", "avisos aos membros do nupdec"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "8.1":
                opcoes = ["telefone de emergências", "aplicativo de mensagens", "site da prefeitura", "redes sociais"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            elif qid == "12.1.3.1":
                if "diariamente" in resp_l:
                    is_adequado = True

            elif qid == "14.1":
                opcoes = ["calçadas com dimensões mínimas para circulação", "sinalização tátil em pisos", "rampas de acesso", "escadas com corrimão"]
                if any(opt in resp_l for opt in opcoes):
                    is_adequado = True

            status_txt = "Adequado" if is_adequado else "Inadequado"

            analise_sp.append({
                "qid": qid,
                "resp": resp if resp else "Não Informado",
                "status": status_txt
            })

        if analise_sp:
            style_td_sp = ParagraphStyle('TdSp', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, alignment=1)
            
            data_sp = [[
                Paragraph("Quesito", style_th), 
                Paragraph("Resposta Informada no Sistema", style_th), 
                Paragraph("Situação / Conformidade", style_th)
            ]]

            for item in analise_sp:
                if item["status"] == "Adequado":
                    st_p = Paragraph("<font color='#28a745'><b>✅ Adequado</b></font>", style_td_sp)
                else:
                    st_p = Paragraph("<font color='#dc3545'><b>❌ Inadequado</b></font>", style_td_sp)

                data_sp.append([
                    Paragraph(f"<b>{item['qid']}</b>", style_td_sp),
                    Paragraph(item["resp"], styles["Normal"]),
                    st_p
                ])

            tabela_sp.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ])
            )
            elements.append(tabela_sp)
            elements.append(Spacer(1, 15))
# =============================================================================
# 5. ENTRY POINT PRINCIPAL
# =============================================================================
@ui.page('/')
def mostrar_formulario_icidade():
    container_formulario_icidade()

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Indicador i-Cidade • Defesa Civil", storage_secret="sua_chave_secreta_aqui")

