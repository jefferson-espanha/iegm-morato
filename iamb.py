import ast
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# CONFIGURAÇÕES E BANCO DE DADOS (NEON)
# =============================================================================
REGEX_PURE_URL = r"https?://[^\s]+"

DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db(tabela_nome="respostas_iamb_oficial"):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {tabela_nome} (
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
        print(f"❌ Erro ao inicializar tabela {tabela_nome}: {e}")


def load_respostas(ano, tabela_nome="respostas_iamb_oficial"):
    try:
        ano = int(ano)
    except (TypeError, ValueError):
        ano = 2026

    query = f"""
        SELECT qid, valor, pontos, link, comentarios, status
        FROM {tabela_nome}
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

                    respostas[str(row["qid"])] = {
                        "valor": row["valor"] if row["valor"] is not None else "",
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
        print(f"❌ Erro ao carregar respostas de {tabela_nome}: {e}")

    return respostas


def save_resposta(
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente", tabela_nome="respostas_iamb_oficial"
):
    try:
        ano = int(ano)
    except (TypeError, ValueError):
        raise ValueError(f"Ano inválido: {ano!r}")

    if comentarios is None:
        dados_atuais = load_respostas(ano, tabela_nome).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
    link_final = link.strip() if link else ""

    query = f"""
        INSERT INTO {tabela_nome} (ano, qid, valor, pontos, link, comentarios, status)
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
        print(f"❌ Erro ao salvar resposta em {tabela_nome}: {e}")
        raise


def zerar_questionario_db(ano, tabela_nome="respostas_iamb_oficial"):
    try:
        ano = int(ano)
    except (TypeError, ValueError):
        raise ValueError(f"Ano inválido: {ano!r}")

    query = f"DELETE FROM {tabela_nome} WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário em {tabela_nome}: {e}")


def _obter_lista_comentarios(dados_banco):
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


def gerar_relatorio_pdf_bytes(res_data, ano, total_pts, faixa, nome_indicador="iAMB"):
    nome_indicador_str = str(nome_indicador).upper()
    conteudo = f"RELATÓRIO TÉCNICO {nome_indicador_str} ({ano})\n"
    conteudo += f"Pontuação Total: {total_pts:.1f} pts | Faixa: {faixa}\n\n"
    for qid, dados in res_data.items():
        conteudo += f"Quesito {qid}: {dados.get('valor')} | Pontos: {dados.get('pontos')} | Link: {dados.get('link')}\n"
    return conteudo.encode("utf-8")


# =============================================================================
# 1. PAINEL LATERAL
# =============================================================================
def render_painel_controle(
    on_refresh_callback=None,
    nome_indicador="iAMB",
    tabela_nome="respostas_iamb_oficial"
):
    init_db(tabela_nome)
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    try:
        ano_atual = int(app.storage.user.get("ano_referencia_global", 2026))
    except (TypeError, ValueError):
        ano_atual = 2026
    nome_indicador_str = str(nome_indicador)

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label(f"🛠️ Painel de Controle ({nome_indicador_str})").classes(
            "text-lg font-bold mb-2 text-blue-900"
        )

        def ao_mudar_ano(e):
            novo_ano = int(e.value)
            app.storage.user["ano_referencia_global"] = novo_ano
            ui.notify(f"Ano alterado para {novo_ano}", type="info")
            if on_refresh_callback:
                on_refresh_callback()

        ui.select(
            options=anos,
            value=ano_atual,
            label="Ano de Referência:",
            on_change=ao_mudar_ano,
        ).classes("w-full mb-4")

        # --- BLOCO DINÂMICO DE PONTUAÇÃO ---
        @ui.refreshable
        def render_bloco_pontuacao():
            ano_ref = int(app.storage.user.get("ano_referencia_global", 2026))
            res_data_local = load_respostas(ano_ref, tabela_nome)
            total_pts_local = sum(
                float(item.get("pontos", 0)) for item in res_data_local.values()
            )

            if total_pts_local <= 500:
                faixa_l, cor_l = "C", "text-red-600"
            elif total_pts_local <= 599:
                faixa_l, cor_l = "C+", "text-orange-500"
            elif total_pts_local <= 749:
                faixa_l, cor_l = "B", "text-yellow-600"
            elif total_pts_local <= 899:
                faixa_l, cor_l = "B+", "text-green-500"
            else:
                faixa_l, cor_l = "A", "text-green-700"

            with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
                ui.label("Pontuação Total").classes(
                    "text-xs text-gray-500 font-bold uppercase"
                )
                ui.label(f"{total_pts_local:.1f} pts").classes(
                    "text-2xl font-black text-gray-800"
                )

                with ui.row().classes("items-center gap-1 mt-1"):
                    ui.label("Faixa:").classes("font-bold text-sm")
                    ui.label(faixa_l).classes(f"text-xl font-bold {cor_l}")

        render_bloco_pontuacao()

        ui.separator().classes("my-2")
        ui.label("⚙️ Gerenciamento").classes("font-bold text-sm mb-2")

        def atualizar_dados():
            ui.notify("Questionário atualizado!", type="positive", icon="refresh")
            if on_refresh_callback:
                on_refresh_callback()

        ui.button("🔄 ATUALIZAR QUESTIONÁRIO", on_click=atualizar_dados).classes(
            "w-full bg-blue-600 text-white mb-2"
        )

        with ui.dialog() as dialog_zerar, ui.card().classes("w-96 p-4"):
            ui.label("🔒 Confirmação de Segurança").classes(
                "text-lg font-bold text-red-600"
            )
            ui.label(
                f"Você está prestes a apagar todas as respostas de {ano_atual} em {nome_indicador_str}. Esta ação é irreversível!"
            ).classes("text-sm my-2")

            input_senha = ui.input(
                "Digite a senha de administrador:", password=True
            ).classes("w-full mb-4")

            def executar_zerar():
                ano_zerar = int(app.storage.user.get("ano_referencia_global", 2026))
                if input_senha.value == "fidelios":
                    zerar_questionario_db(ano_zerar, tabela_nome)
                    ui.notify(
                        f"✅ Questionário de {ano_zerar} foi zerado!",
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
            res_data_rel = load_respostas(ano_atual, tabela_nome)
            pts_rel = sum(float(i.get("pontos", 0)) for i in res_data_rel.values())
            faixa_rel = (
                "C" if pts_rel <= 500 else
                "C+" if pts_rel <= 599 else
                "B" if pts_rel <= 749 else
                "B+" if pts_rel <= 899 else "A"
            )
            pdf_bytes = gerar_relatorio_pdf_bytes(
                res_data_rel, ano_atual, pts_rel, faixa_rel, nome_indicador_str
            )
            ui.button(
                "📄 RELATÓRIO",
                on_click=lambda: ui.download(
                    pdf_bytes, f"Relatorio_{nome_indicador_str}_{ano_atual}.pdf"
                ),
            ).classes("flex-1 bg-blue-500 text-white")
            ui.button("🗑️ ZERAR", on_click=dialog_zerar.open).classes(
                "flex-1 bg-blue-500 text-white"
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
# 2. BLOCO DE COMENTÁRIOS
# =============================================================================
def bloco_comentarios(qid, res_data, on_save_callback=None, tabela_nome="respostas_iamb_oficial"):
    try:
        ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
    except (TypeError, ValueError):
        ano_sel = 2026
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")

    dados_q = res_data.get(str(qid), {})
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
            ano_ativo = int(app.storage.user.get("ano_referencia_global", ano_sel))
            novo_st = str(e.value)
            log = {
                "autor": "Sistema / " + str(usuario_atual),
                "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "texto": f"ℹ️ Alterou o status do quesito para: **{novo_st.upper()}**.",
                "status_definido": novo_st,
            }
            historico.append(log)
            save_resposta(
                ano=ano_ativo,
                qid=qid,
                valor=dados_q.get("valor", ""),
                pontos=dados_q.get("pontos", 0.0),
                link=dados_q.get("link", ""),
                comentarios=historico,
                status=novo_st,
                tabela_nome=tabela_nome,
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
                    ano_ativo = int(app.storage.user.get("ano_referencia_global", ano_sel))
                    historico.pop(i)
                    save_resposta(
                        ano=ano_ativo,
                        qid=qid,
                        valor=dados_q.get("valor", ""),
                        pontos=dados_q.get("pontos", 0.0),
                        link=dados_q.get("link", ""),
                        comentarios=historico,
                        status=status_global,
                        tabela_nome=tabela_nome,
                    )
                    ui.notify("Comentário removido.", type="warning")
                    if on_save_callback:
                        on_save_callback()

                with ui.row().classes(
                    "w-full items-center justify-between no-wrap mb-2"
                ):
                    if "Sistema /" in str(autor):
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
            ano_ativo = int(app.storage.user.get("ano_referencia_global", ano_sel))
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append({
                    "autor": str(usuario_atual),
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": txt,
                    "status_definido": status_global,
                })
                save_resposta(
                    ano=ano_ativo,
                    qid=qid,
                    valor=dados_q.get("valor", ""),
                    pontos=dados_q.get("pontos", 0.0),
                    link=dados_q.get("link", ""),
                    comentarios=historico,
                    status=status_global,
                    tabela_nome=tabela_nome,
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
    tipo="radio",
    informativo=False,
    is_text_area=False,
    placeholder_text="Cole os links ou informações aqui...",
    placeholder_link="Link de Evidência / Documento:",
    pontuacao_maxima=None,
    tabela_nome="respostas_iamb_oficial",
    **kwargs,
):
    d_data = res_data.get(str(qid), {})

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
                                str(input_valor.value)
                                if (is_text_area and input_valor.value)
                                else ""
                            )

                        lnk_txt = str(input_link.value or "")
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
                ano_ativo = int(app.storage.user.get("ano_referencia_global", ano))

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

                dados_atuais_db = load_respostas(ano_ativo, tabela_nome).get(str(qid), {})
                st = dados_atuais_db.get("status", "Pendente")
                comms = dados_atuais_db.get("comentarios", [])

                save_resposta(
                    ano=ano_ativo,
                    qid=qid,
                    valor=val,
                    pontos=pts,
                    link=link,
                    comentarios=comms,
                    status=st,
                    tabela_nome=tabela_nome,
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

            bloco_comentarios(
                qid, res_data, on_save_callback=on_save_callback, tabela_nome=tabela_nome
            )


# =============================================================================
# 4. CONTAINER PRINCIPAL DO IAMB (SEM DECORADOR REFRESHABLE AQUI)
# =============================================================================
def container_formulario_iamb(quesitos_lista=None):
    container_pai = ui.container().classes("w-full")

    @ui.refreshable
    def render_conteudo():
        try:
            ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
        except (TypeError, ValueError):
            ano_sel = 2026
        app.storage.user["ano_referencia_global"] = ano_sel
        tabela = "respostas_iamb_oficial"
        res_data = load_respostas(ano_sel, tabela)

        with ui.grid(columns=4).classes("w-full gap-6 items-start"):
            with ui.column().classes("col-span-1 w-full"):
                render_painel_controle(
                    on_refresh_callback=render_conteudo.refresh,
                    nome_indicador="iAMB",
                    tabela_nome=tabela,
                )

            with ui.column().classes("col-span-3 w-full"):
                ui.label(
                    f"Formulário iAMB ({ano_sel})"
                ).classes("text-h4 mb-1 font-bold text-blue-900")
                ui.label(
                    "Preencha as evidências e questões ambientais do município."
                ).classes("text-gray-600 mb-6")

                if quesitos_lista:
                    for q in quesitos_lista:
                        render_quesito(
                            ano=ano_sel,
                            res_data=res_data,
                            tabela_nome=tabela,
                            on_save_callback=render_conteudo.refresh,
                            **q
                        )

    with container_pai:
        render_conteudo()
            # =============================================================================
            # QUESITO 1.0 • ESTRUTURA ORGANIZACIONAL
            # =============================================================================
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Estrutura Organizacional de Meio Ambiente",
                pergunta="A prefeitura possui alguma estrutura organizacional para tratar de assuntos ligados ao Meio Ambiente Municipal?",
                opcoes=opcoes_10,
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 1.1 • RECURSOS HUMANOS
            # =============================================================================
            opcoes_11 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1",
                titulo="Recursos Humanos para Meio Ambiente",
                pergunta="A Prefeitura possui recursos humanos para operacionalização dos assuntos ligados ao Meio Ambiente?",
                opcoes=opcoes_11,
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 1.1.1 • QUANTITATIVO DE RECURSOS HUMANOS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 1.1.1 - Quantitativo de Recursos Humanos").classes("text-lg font-bold text-green-900 mb-1")
                ui.label("Informe o quantitativo de Recursos Humanos atuantes na área ambiental:").classes("text-base font-semibold text-gray-800 mt-2 mb-3")

                d111 = res_data.get("1.1.1") or {}
                if not isinstance(d111, dict):
                    d111 = {"valor": "{}", "pontos": 0.0, "link": ""}

                val_raw = d111.get("valor", "{}")
                try:
                    val_json = json.loads(val_raw) if isinstance(val_raw, str) else val_raw
                except Exception:
                    val_json = {}

                evidencia_111_salva = str(d111.get("link") or "")

                state_111 = {
                    "efetivos": val_json.get("efetivos", 0),
                    "comissionados": val_json.get("comissionados", 0),
                    "terceirizados": val_json.get("terceirizados", 0),
                    "link": evidencia_111_salva
                }

                def salvar_111():
                    try:
                        res_dict = {
                            "efetivos": int(state_111["efetivos"] or 0),
                            "comissionados": int(state_111["comissionados"] or 0),
                            "terceirizados": int(state_111["terceirizados"] or 0)
                        }
                        val_str = json.dumps(res_dict)
                        lnk_val = str(state_111["link"] or "").strip()

                        save_resposta(
                            ano=ano_sel,
                            qid="1.1.1",
                            valor=val_str,
                            pontos=0.0,
                            link=lnk_val,
                            comentarios=_obter_lista_comentarios(d111),
                            status=d111.get("status", "Pendente"),
                            tabela_nome=tabela,
                        )
                        res_data["1.1.1"] = {"valor": val_str, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 1.1.1 salvo com sucesso!", type="positive")
                        container_formulario_iamb.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.1.1: {err}", type="negative")

                with ui.row().classes("w-full gap-4 mb-4"):
                    ui.number("Nº de efetivos", min=0).classes("w-1/4").bind_value(state_111, "efetivos")
                    ui.number("Nº de comissionados", min=0).classes("w-1/4").bind_value(state_111, "comissionados")
                    ui.number("Nº de terceirizados/contratados", min=0).classes("w-1/4").bind_value(state_111, "terceirizados")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência):",
                    value=evidencia_111_salva,
                    placeholder="Cole aqui o link do ato de nomeação, organograma ou documento comprobatório..."
                ).classes("w-full mb-4").bind_value(state_111, "link")

                with ui.row().classes("w-full justify-end items-center mb-2"):
                    ui.button("Salvar Quesito 1.1.1", on_click=salvar_111, icon="save").classes("bg-green-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios(
                    "1.1.1",
                    res_data,
                    on_save_callback=container_formulario_iamb.refresh,
                    tabela_nome=tabela,
                )

            # =============================================================================
            # QUESITO 1.1.2 • TREINAMENTO ESPECÍFICO DOS SERVIDORES
            # =============================================================================
            opcoes_112 = {
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1.2",
                titulo="Treinamento Específico em Meio Ambiente",
                pergunta="Os servidores responsáveis pelo Meio Ambiente receberam treinamento específico voltado ao Meio Ambiente no ano de referência?",
                opcoes=opcoes_112,
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 1.1.3 • PÚBLICO DOS CURSOS/TREINAMENTOS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 1.1.3 - Cursos e Treinamentos de Educação Ambiental").classes("text-lg font-bold text-green-900 mb-1")
                ui.label("A Secretaria Municipal de Meio Ambiente ou similar ofereceu cursos/treinamento sobre educação ambiental para qual público?").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-green-50 border-l-4 border-green-600 rounded-r-md shadow-none"):
                    ui.label("Critérios de pontuação:").classes("text-xs font-bold text-green-900 uppercase tracking-wide")
                    ui.label(
                        "• Para escolas — 05 pontos\n"
                        "• Para outras secretarias / entidades municipais — 02 pontos\n"
                        "• Para munícipes ou empresas — 03 pontos\n"
                        "• Não ofereceu nenhum curso/treinamento no ano — 00 pontos"
                    ).classes("text-xs text-green-900 font-mono mt-1")

                d113 = res_data.get("1.1.3") or {}
                if not isinstance(d113, dict):
                    d113 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_113 = str(d113.get("valor") or "")
                itens_salvos_113 = [i.strip() for i in valor_salvo_113.split(",") if i.strip()]
                evidencia_113_salva = str(d113.get("link") or "")

                state_113 = {
                    "escolas": "Para escolas" in itens_salvos_113,
                    "secretarias": "Para outras secretarias / entidades municipais" in itens_salvos_113,
                    "municipes": "Para munícipes ou empresas" in itens_salvos_113,
                    "nenhum": "Não ofereceu nenhum curso/treinamento no ano" in itens_salvos_113,
                    "link": evidencia_113_salva
                }

                def salvar_113():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        if state_113["nenhum"]:
                            marcados = ["Não ofereceu nenhum curso/treinamento no ano"]
                            pts_acumulados = 0.0
                        else:
                            if state_113["escolas"]:
                                marcados.append("Para escolas")
                                pts_acumulados += 5.0
                            if state_113["secretarias"]:
                                marcados.append("Para outras secretarias / entidades municipais")
                                pts_acumulados += 2.0
                            if state_113["municipes"]:
                                marcados.append("Para munícipes ou empresas")
                                pts_acumulados += 3.0

                        val_str = ", ".join(marcados)
                        lnk_val = str(state_113["link"] or "").strip()

                        save_resposta(
                            ano=ano_sel,
                            qid="1.1.3",
                            valor=val_str,
                            pontos=pts_acumulados,
                            link=lnk_val,
                            comentarios=_obter_lista_comentarios(d113),
                            status=d113.get("status", "Pendente"),
                            tabela_nome=tabela,
                        )
                        res_data["1.1.3"] = {"valor": val_str, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 1.1.3 salvo com sucesso!", type="positive")
                        container_formulario_iamb.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.1.3: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Para escolas (05 pts)").bind_value(state_113, "escolas")
                    ui.checkbox("Para outras secretarias / entidades municipais (02 pts)").bind_value(state_113, "secretarias")
                    ui.checkbox("Para munícipes ou empresas (03 pts)").bind_value(state_113, "municipes")
                    ui.checkbox("Não ofereceu nenhum curso/treinamento no ano (00 pts)").bind_value(state_113, "nenhum")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência):",
                    value=evidencia_113_salva,
                    placeholder="Link de fotos, certificados, listas de presença ou publicações..."
                ).classes("w-full mb-4").bind_value(state_113, "link")

                pts_atuais_113 = float(d113.get("pontos") or 0.0)
                cor_txt_113 = "text-green-600" if pts_atuais_113 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Selecionados: {valor_salvo_113 if valor_salvo_113 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Pontuação no Quesito 1.1.3: +{pts_atuais_113:.1f} pontos").classes(f"text-sm font-bold {cor_txt_113}")

                    ui.button("Salvar Quesito 1.1.3", on_click=salvar_113, icon="save").classes("bg-green-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios(
                    "1.1.3",
                    res_data,
                    on_save_callback=container_formulario_iamb.refresh,
                    tabela_nome=tabela,
                )

            # =============================================================================
            # QUESITO 1.2 • RECURSOS DISPONIBILIZADOS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 1.2 - Recursos Disponibilizados").classes("text-lg font-bold text-green-900 mb-1")
                ui.label("Assinale os recursos disponibilizados para a operacionalização das atividades de meio ambiente (Desconsiderar Recursos Humanos e Estrutura Física nesta questão):").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-green-50 border-l-4 border-green-600 rounded-r-md shadow-none"):
                    ui.label("Critérios de pontuação:").classes("text-xs font-bold text-green-900 uppercase tracking-wide")
                    ui.label(
                        "• Recursos Tecnológicos — 05 pontos\n"
                        "• Recursos Orçamentários — 05 pontos\n"
                        "• Recursos Materiais — 05 pontos\n"
                        "• Outros — 05 pontos"
                    ).classes("text-xs text-green-900 font-mono mt-1")

                d12 = res_data.get("1.2") or {}
                if not isinstance(d12, dict):
                    d12 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_12 = str(d12.get("valor") or "")
                itens_salvos_12 = [i.strip() for i in valor_salvo_12.split(",") if i.strip()]
                evidencia_12_salva = str(d12.get("link") or "")

                state_12 = {
                    "tecnologicos": "Recursos Tecnológicos" in itens_salvos_12,
                    "orcamentarios": "Recursos Orçamentários" in itens_salvos_12,
                    "materiais": "Recursos Materiais" in itens_salvos_12,
                    "outros": "Outros" in itens_salvos_12,
                    "link": evidencia_12_salva
                }

                def salvar_12():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        if state_12["tecnologicos"]:
                            marcados.append("Recursos Tecnológicos")
                            pts_acumulados += 5.0
                        if state_12["orcamentarios"]:
                            marcados.append("Recursos Orçamentários")
                            pts_acumulados += 5.0
                        if state_12["materiais"]:
                            marcados.append("Recursos Materiais")
                            pts_acumulados += 5.0
                        if state_12["outros"]:
                            marcados.append("Outros")
                            pts_acumulados += 5.0

                        val_str = ", ".join(marcados)
                        lnk_val = str(state_12["link"] or "").strip()

                        save_resposta(
                            ano=ano_sel,
                            qid="1.2",
                            valor=val_str,
                            pontos=pts_acumulados,
                            link=lnk_val,
                            comentarios=_obter_lista_comentarios(d12),
                            status=d12.get("status", "Pendente"),
                            tabela_nome=tabela,
                        )
                        res_data["1.2"] = {"valor": val_str, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 1.2 salvo com sucesso!", type="positive")
                        container_formulario_iamb.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.2: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Recursos Tecnológicos (05 pts)").bind_value(state_12, "tecnologicos")
                    ui.checkbox("Recursos Orçamentários (05 pts)").bind_value(state_12, "orcamentarios")
                    ui.checkbox("Recursos Materiais (05 pts)").bind_value(state_12, "materiais")
                    ui.checkbox("Outros (05 pts)").bind_value(state_12, "outros")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência):",
                    value=evidencia_12_salva,
                    placeholder="Cole o link comprovando a alocação de recursos..."
                ).classes("w-full mb-4").bind_value(state_12, "link")

                pts_atuais_12 = float(d12.get("pontos") or 0.0)
                cor_txt_12 = "text-green-600" if pts_atuais_12 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Selecionados: {valor_salvo_12 if valor_salvo_12 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Pontuação no Quesito 1.2: +{pts_atuais_12:.1f} pontos").classes(f"text-sm font-bold {cor_txt_12}")

                    ui.button("Salvar Quesito 1.2", on_click=salvar_12, icon="save").classes("bg-green-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios(
                    "1.2",
                    res_data,
                    on_save_callback=container_formulario_iamb.refresh,
                    tabela_nome=tabela,
                )

            # =============================================================================
            # QUESITO 2.0 • PROGRAMAS DE EDUCAÇÃO AMBIENTAL
            # =============================================================================
            ui.label("2.0 Programas de Educação Ambiental").classes(
                "text-h5 font-bold my-4 text-green-900"
            )

            opcoes_20 = {
                "Selecione...": 0.0,
                "Sim (10 pts)": 10.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.0",
                titulo="Programa de Educação Ambiental",
                pergunta="O Município participa de algum Programa de Educação Ambiental?",
                opcoes=opcoes_20,
                on_save_callback=container_formulario_iamb.refresh,
            )

# =============================================================================
            # QUESITO 2.1 • EDUCAÇÃO AMBIENTAL NA REDE ESCOLAR
            # =============================================================================
            # Este quesito é numérico (cálculo), similar ao exemplo 1.1.1 fornecido.
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 2.1 - Educação Ambiental na Rede Escolar Municipal").classes("text-lg font-bold text-green-900 mb-1")
                ui.label("Sobre programa ou ação de educação ambiental na rede escolar municipal, informe:").classes("text-base font-semibold text-gray-800 mt-2 mb-3")

                # Buscar dados existentes
                d21 = res_data.get("2.1") or {}
                if not isinstance(d21, dict):
                    d21 = {"valor": "{}", "pontos": 0.0, "link": ""}

                # Decodificar JSON do valor salvo
                val_raw_21 = d21.get("valor", "{}")
                try:
                    val_json_21 = json.loads(val_raw_21) if isinstance(val_raw_21, str) else val_raw_21
                except Exception:
                    val_json_21 = {}

                evidencia_21_salva = str(d21.get("link") or "")

                # Estado local para os inputs (usando dados salvos ou padrões)
                # Nota: nº de escolas dos Anos Iniciais deve vir de i-Educ = E3.3, 
                # assumindo que esteja disponível em algum lugar ou precise ser preenchido aqui.
                state_21 = {
                    "n_escolas_ea": val_json_21.get("n_escolas_ea", 0),
                    "n_total_escolas_iniciais": val_json_21.get("n_total_escolas_iniciais", 0),
                    "link": evidencia_21_salva
                }

                def salvar_21():
                    try:
                        n_ea = int(state_21["n_escolas_ea"] or 0)
                        n_total = int(state_21["n_total_escolas_iniciais"] or 0)
                        
                        # Lógica de cálculo da pontuação
                        pts_finais = 0.0
                        if n_total > 0:
                            # P = nº de escolas com programa / nº total de escolas Anos Iniciais
                            proporcao = n_ea / n_total
                            # Garante que a proporção não exceda 1 (caso informem n_ea > n_total erroneamente)
                            proporcao = min(proporcao, 1.0)
                            # N = P x Pmáx (50 pontos)
                            pts_finais = proporcao * 50.0
                        
                        res_dict = {
                            "n_escolas_ea": n_ea,
                            "n_total_escolas_iniciais": n_total,
                            "formula": f"({n_ea} / {n_total}) * 50" if n_total > 0 else "Divisão por zero"
                        }
                        val_str = json.dumps(res_dict)
                        lnk_val = str(state_21["link"] or "").strip()

                        # Salvar no banco
                        save_resposta(
                            ano=ano_sel,
                            qid="2.1",
                            valor=val_str,
                            pontos=pts_finais,
                            link=lnk_val,
                            comentarios=_obter_lista_comentarios(d21),
                            status=d21.get("status", "Pendente"),
                            tabela_nome=tabela,
                        )
                        
                        # Atualizar dados locais
                        res_data["2.1"] = {"valor": val_str, "pontos": pts_finais, "link": lnk_val}
                        
                        ui.notify(f"Quesito 2.1 salvo com sucesso! Pontuação calculada: {pts_finais:.2f}", type="positive")
                        container_formulario_iamb.refresh()
                        
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 2.1: {err}", type="negative")

                # Inputs numéricos
                with ui.row().classes("w-full gap-4 mb-4"):
                    ui.number(
                        "Nº de escolas dos Anos Iniciais (1º ao 5º) com educação ambiental", 
                        min=0, 
                        format="%.0f"
                    ).classes("w-2/5").bind_value(state_21, "n_escolas_ea")
                    
                    ui.number(
                        "Nº total de escolas dos Anos Iniciais no município (i-Educ = E3.3)", 
                        min=0, 
                        format="%.0f"
                    ).classes("w-2/5").bind_value(state_21, "n_total_escolas_iniciais")

                # Campo de evidência
                ui.textarea(
                    "Página Eletrônica (Link / Evidência):",
                    value=evidencia_21_salva,
                    placeholder="Link para documento comprobatório, PPP das escolas, relatórios de atividades, etc..."
                ).classes("w-full mb-4").bind_value(state_21, "link")

                # Exibição da pontuação calculada atualmente salva
                pts_atuais_21 = float(d21.get("pontos") or 0.0)
                cor_txt_21 = "text-green-600" if pts_atuais_21 > 0 else "text-gray-500"
                
                with ui.row().classes("w-full justify-between items-center mb-2"):
                    ui.label(f"📊 Pontuação calculada: {pts_atuais_21:.2f} / 50.0 pontos").classes(f"text-base font-bold {cor_txt_21}")
                    ui.button("Salvar Quesito 2.1", on_click=salvar_21, icon="save").classes("bg-green-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios(
                    "2.1",
                    res_data,
                    on_save_callback=container_formulario_iamb.refresh,
                    tabela_nome=tabela,
                )

            # =============================================================================
            # TITULO SEÇÃO 3.0
            # =============================================================================
            ui.label("3.0 Uso Racional de Recursos Naturais").classes(
                "text-h5 font-bold my-4 text-green-900"
            )

            # =============================================================================
            # QUESITO 3.0 • ESTÍMULO AO USO RACIONAL
            # =============================================================================
            # Este quesito é de seleção única (rádio/select), usa o render_quesito padrão.
            opcoes_30 = {
                "Selecione...": 0.0,
                "Sim, para todos os órgãos e entidades (10 pts)": 10.0,
                "Parcialmente (03 pts)": 3.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.0",
                titulo="Estímulo ao Uso Racional de Recursos Naturais",
                pergunta="A prefeitura municipal estimula entre seus órgãos e entidades projetos e/ou ações que promovam o uso racional de recursos naturais?",
                opcoes=opcoes_30,
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 3.1 • TIPOS DE AÇÕES REALIZADAS
            # =============================================================================
            # Este quesito é de múltipla escolha (checkbox), similar ao exemplo 1.1.3 fornecido.
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 3.1 - Detalhamento das Ações de Uso Racional").classes("text-lg font-bold text-green-900 mb-1")
                ui.label("Assinale quais tipos de ações realizadas pela Prefeitura para o uso racional de recursos naturais:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                # Busca dados
                d31 = res_data.get("3.1") or {}
                if not isinstance(d31, dict):
                    d31 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_31 = d31.get("valor") or ""
                # Converter string salva em lista para marcar os checkboxes
                itens_salvos_31 = [i.strip() for i in valor_salvo_31.split(",") if i.strip()]
                evidencia_31_salva = str(d31.get("link") or "")

                # Dicionário de opções e pontos para facilitar iteração e renderização
                opcoes_checkbox_31 = [
                    ("Coleta seletiva", 1.5),
                    ("Uso racional da água", 1.5),
                    ("Uso racional de energia elétrica", 1.5),
                    ("Reúso de materiais", 1.5),
                    ("Horta coletiva", 1.5),
                    ("Compostagem", 1.5),
                    ("Instalação de bicicletários e vestiários para os servidores públicos", 1.5),
                    ("Implantação de caixas acopladas nos vasos sanitários", 1.5),
                    ("Substituição de lâmpadas fluorescentes por lâmpadas LED", 1.5),
                    ("Instalação de estruturas para a captação de água de chuva", 1.5),
                    ("Instalação de torneiras com redutores de pressão", 1.5),
                    ("Substituição de material descartável", 1.5),
                    ("Logística reversa de pilhas, baterias e eletrônicos", 1.5),
                    ("Outros", 0.5),
                ]

                # Estado local para os checkboxes (inicializado com base no que foi salvo)
                state_31 = {texto: (texto in itens_salvos_31) for texto, _ in opcoes_checkbox_31}
                state_31["link"] = evidencia_31_salva

                def salvar_31():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        # Itera sobre as opções para verificar o que está marcado no state
                        for texto, pontos in opcoes_checkbox_31:
                            if state_31.get(texto):
                                marcados.append(texto)
                                pts_acumulados += pontos

                        # Converter lista para string para salvar
                        val_str = ", ".join(marcados)
                        lnk_val = str(state_31["link"] or "").strip()

                        save_resposta(
                            ano=ano_sel,
                            qid="3.1",
                            valor=val_str,
                            pontos=pts_acumulados,
                            link=lnk_val,
                            comentarios=_obter_lista_comentarios(d31),
                            status=d31.get("status", "Pendente"),
                            tabela_nome=tabela,
                        )
                        res_data["3.1"] = {"valor": val_str, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 3.1 salvo com sucesso!", type="positive")
                        container_formulario_iamb.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 3.1: {err}", type="negative")

                # Renderização dinâmica dos checkboxes em 2 colunas para melhor visualização
                with ui.row().classes("w-full gap-x-8 gap-y-2 mb-4"):
                    # Divide as opções em duas metades
                    metade = (len(opcoes_checkbox_31) + 1) // 2
                    col1_ops = opcoes_checkbox_31[:metade]
                    col2_ops = opcoes_checkbox_31[metade:]

                    with ui.column().classes("w-1/2 gap-2"):
                        for texto, pontos in col1_ops:
                            ui.checkbox(f"{texto} ({pontos:.1f} pts)").bind_value(state_31, texto)
                    
                    with ui.column().classes("w-1/2 gap-2"):
                        # Ajuste específico para o checkbox "Outros" se necessário (ex: input de texto extra), 
                        # aqui mantido simples conforme padrão 1.1.3
                        for texto, pontos in col2_ops:
                            ui.checkbox(f"{texto} ({pontos:.1f} pts)").bind_value(state_31, texto)

                # Campo de evidência
                ui.textarea(
                    "Página Eletrônica (Link / Evidência):",
                    value=evidencia_31_salva,
                    placeholder="Link para fotos, faturas comprovando substituição, leis/decretos, contratos, etc..."
                ).classes("w-full mb-4").bind_value(state_31, "link")

                # Rodapé do card com info e botão salvar
                pts_atuais_31 = float(d31.get("pontos") or 0.0)
                cor_txt_31 = "text-green-600" if pts_atuais_31 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        # Exibe resumo do que está salvo atualmente no banco
                        texto_resumo_salvo = valor_salvo_31 if valor_salvo_31 else 'Nenhuma ação selecionada'
                        if len(texto_resumo_salvo) > 100:
                            texto_resumo_salvo = texto_resumo_salvo[:97] + "..."
                            
                        ui.label(f"📋 Salvo: {texto_resumo_salvo}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Pontuação atual no Quesito 3.1: +{pts_atuais_31:.1f} pontos").classes(f"text-sm font-bold {cor_txt_31}")

                    ui.button("Salvar Quesito 3.1", on_click=salvar_31, icon="save").classes("bg-green-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios(
                    "3.1",
                    res_data,
                    on_save_callback=container_formulario_iamb.refresh,
                    tabela_nome=tabela,
                )


# =============================================================================
# 5. ENTRADA ÚNICA DA APLICAÇÃO
# =============================================================================
def render_iamb():
    """Renderiza o formulário completo; o painel é criado apenas dentro dele."""
    container_formulario_iamb()


if __name__ in {"__main__", "__mp_main__"}:
    @ui.page("/")
    def main_page():
        render_iamb()
