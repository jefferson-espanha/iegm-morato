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


def gerar_relatorio_pdf_bytes(res_data, ano, total_pts, faixa):
    """Gera dados para download do relatório em PDF."""
    conteudo = f"RELATÓRIO TÉCNICO i-Cidade ({ano})\n"
    conteudo += f"Pontuação Total: {total_pts:.1f} pts | Faixa: {faixa}\n\n"
    for qid, dados in res_data.items():
        conteudo += f"Quesito {qid}: {dados.get('valor')} | Pontos: {dados.get('pontos')} | Link: {dados.get('link')}\n"
    return conteudo.encode("utf-8")


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
    # QUESITO 7.3.1 • TIPOS DE SISTEMAS DE ALERTA (Informativo)
    # =============================================================================
    # Nota: Caso a função render_quesito suporte opções com pontuação 0.0 ou múltipla escolha
    opcoes_731 = {
        "Selecione...": 0.0,
        "Alerta via SMS": 0.0,
        "Anúncio por rádio/Televisão": 0.0,
        "Placas de identificação de área de risco": 0.0,
        "Aviso por telefone / Aplicativo de mensagens": 0.0,
        "Aviso por email": 0.0,
        "Aviso aos membros do Nupdec": 0.0,
        "Outro": 0.0
    }
    render_quesito(
        ano=ano_sel,
        res_data=res_data,
        qid="7.3.1",
        titulo="Tipos de Sistemas de Alerta Utilizados",
        pergunta="Assinale os tipos de sistemas de alerta utilizados pelo Município:",
        opcoes=opcoes_731,
        on_save_callback=container_formulario_icidade.refresh
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
# 5. ENTRY POINT PRINCIPAL
# =============================================================================
@ui.page('/')
def mostrar_formulario_icidade():
    container_formulario_icidade()

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Indicador i-Cidade • Defesa Civil", storage_secret="sua_chave_secreta_aqui")
