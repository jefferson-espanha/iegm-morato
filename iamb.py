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

                    key_qid = str(row["qid"]).strip()
                    respostas[key_qid] = {
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

    qid_str = str(qid).strip()

    if comentarios is None:
        dados_atuais = load_respostas(ano, tabela_nome).get(qid_str, {})
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
                        qid_str,
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

    qid_str = str(qid).strip()
    dados_q = res_data.get(qid_str, {})
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
                qid=qid_str,
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
                        qid=qid_str,
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
                    qid=qid_str,
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
    qid_str = str(qid).strip()
    d_data = res_data.get(qid_str, {})

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
                                sel_list = json.loads(v_salvo)
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
                    val = json.dumps(selecionados)
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

                dados_atuais_db = load_respostas(ano_ativo, tabela_nome).get(qid_str, {})
                st = dados_atuais_db.get("status", "Pendente")
                comms = dados_atuais_db.get("comentarios", [])

                save_resposta(
                    ano=ano_ativo,
                    qid=qid_str,
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
                qid_str, res_data, on_save_callback=on_save_callback, tabela_nome=tabela_nome
            )


# =============================================================================
# 4. CONTAINER PRINCIPAL DO IAMB
# =============================================================================
def container_formulario_iamb(quesitos_lista=None):
    container_pai = ui.column().classes("w-full")

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

                # QUESITO 1.0
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
                    on_save_callback=render_conteudo.refresh,
                )

                # QUESITO 1.1
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
                    on_save_callback=render_conteudo.refresh,
                )

                # QUESITO 1.1.1
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

                    input_efetivos = ui.number("Efetivos", value=val_json.get("efetivos", 0), min=0).classes("w-full mb-2")
                    input_comissionados = ui.number("Comissionados", value=val_json.get("comissionados", 0), min=0).classes("w-full mb-2")
                    input_terceirizados = ui.number("Terceirizados", value=val_json.get("terceirizados", 0), min=0).classes("w-full mb-2")
                    input_link_111 = ui.textarea("Link de Evidência", value=evidencia_111_salva).classes("w-full mb-4").props("outlined rows=2")

                    def salvar_111():
                        try:
                            res_dict = {
                                "efetivos": int(input_efetivos.value or 0),
                                "comissionados": int(input_comissionados.value or 0),
                                "terceirizados": int(input_terceirizados.value or 0)
                            }
                            save_resposta(
                                ano=ano_sel,
                                qid="1.1.1",
                                valor=json.dumps(res_dict),
                                pontos=0.0,
                                link=input_link_111.value or "",
                                tabela_nome=tabela
                            )
                            ui.notify("Quesito 1.1.1 salvo com sucesso!", type="positive")
                            render_conteudo.refresh()
                        except Exception as ex:
                            ui.notify(f"Erro ao salvar Quesito 1.1.1: {ex}", type="negative")

                    ui.button("💾 Salvar Quesito 1.1.1", on_click=salvar_111).classes("bg-blue-800 text-white mt-2")
                    bloco_comentarios("1.1.1", res_data, on_save_callback=render_conteudo.refresh, tabela_nome=tabela)

    with container_pai:
        render_conteudo()
