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
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Garante que a tabela respostas_igovti exista com as colunas certas."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS respostas_igovti (
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
        print(f"❌ Erro ao inicializar tabela respostas_igovti: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_igovti
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
        print(f"❌ Erro ao carregar respostas do Neon DB (iGov-TI): {e}")

    return respostas


def save_resposta(
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
    link_final = link.strip() if link else ""

    query = """
        INSERT INTO respostas_igovti (ano, qid, valor, pontos, link, comentarios, status)
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
        print(f"❌ Erro ao salvar resposta no Neon DB (iGov-TI): {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_igovti WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")


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


def gerar_relatorio_pdf_bytes(res_data, ano, total_pts, faixa):
    conteudo = f"RELATÓRIO TÉCNICO iGov-TI ({ano})\n"
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

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label("🛠️ Painel de Controle (iGov-TI)").classes(
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
            ui.notify("Questionário atualizado!", type="positive", icon="refresh")
            if on_refresh_callback:
                on_refresh_callback()

        ui.button("🔄 Atualizar Questionário", on_click=atualizar_dados).classes(
            "w-full bg-blue-700 text-white mb-2"
        )
        ui.separator().classes("my-2")

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
                    pdf_bytes, f"Relatorio_iGovTI_{ano_atual}.pdf"
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
    tipo="radio",
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

                # Tenta chamar save_resp do main se existir, senão usa local
                if "save_resp" in globals():
                    try:
                        save_resp(qid, val, pts, link, comms, ano)
                    except TypeError:
                        save_resposta(
                            ano=ano,
                            qid=qid,
                            valor=val,
                            pontos=pts,
                            link=link,
                            comentarios=comms,
                            status=st,
                        )
                else:
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

            bloco_comentarios(qid, res_data, on_save_callback=on_save_callback)


# =============================================================================
# 4. CONTAINER PRINCIPAL REFRESHABLE
# =============================================================================
@ui.refreshable
def container_formulario_igov_ti():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes("w-full gap-6 items-start"):
        with ui.column().classes("col-span-1 w-full"):
            render_painel_controle(
                on_refresh_callback=container_formulario_igov_ti.refresh
            )

        with ui.column().classes("col-span-3 w-full"):
            ui.label(
                f"Formulário iGov-TI - Governança de TI ({ano_sel})"
            ).classes("text-h4 mb-1 font-bold text-blue-900")
            ui.label(
                "Preencha as evidências e questões do indicador iGov-TI."
            ).classes("text-gray-600 mb-6")

            ui.label("1.0 Estrutura de TIC").classes(
                "text-h5 font-bold my-4 text-blue-900"
            )

            # QUESITO 1.0
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Setor de Tecnologia da Informação e Comunicação",
                pergunta="A Prefeitura possui uma área ou setor que cuida de Tecnologia da Informação e Comunicação (TIC)?",
                opcoes=opcoes_10,
                placeholder_link="Insira o link da lei de estrutura administrativa...",
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 1.1 • COMPOSIÇÃO DA EQUIPE DE TIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                # Cabeçalho do Quesito
                ui.label("📌 Quesito 1.1 - Recursos Humanos em TIC").classes("text-lg font-bold text-blue-900 mb-1")
                
                # Enunciado
                ui.label("Informe a quantidade:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                
                # Bloco Informativo da Fórmula de Cálculo
                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "Funcionários concursados + Funcionários comissionados + Estagiários no suporte e atendimento de primeiro nível > 0 — 30 pontos"
                    ).classes("text-sm text-blue-800 font-medium")

                # Função local de persistência para evitar 'save_resp is not defined'
                def salvar_no_banco_11(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                # Conversor seguro para prevenir crash com strings vazias
                def parse_int_seguro(val):
                    if val is None:
                        return 0
                    if isinstance(val, (int, float)):
                        return int(val)
                    val_str = str(val).strip()
                    if not val_str or not val_str.isdigit():
                        return 0
                    return int(val_str)

                # Recuperação do banco
                d11 = res_data.get("1.1") or {}
                if not isinstance(d11, dict):
                    d11 = {"valor": "0", "pontos": 0.0, "link": ""}

                v_conc_i, v_comi_i, v_esta_i, v_outr_i = 0, 0, 0, 0
                evidencia_11_salva = ""
                raw_link = str(d11.get("link") or "")

                # Extração dos contadores armazenados
                if raw_link:
                    if "|LINK:" in raw_link:
                        contadores_part, evidencia_11_salva = raw_link.split("|LINK:", 1)
                    else:
                        contadores_part, evidencia_11_salva = raw_link, ""

                    import re
                    match_c = re.search(r'C:(\d+)', contadores_part)
                    match_co = re.search(r'Co:(\d+)', contadores_part)
                    match_e = re.search(r'E:(\d+)', contadores_part)
                    match_o = re.search(r'O:(\d+)', contadores_part)

                    v_conc_i = int(match_c.group(1)) if match_c else 0
                    v_comi_i = int(match_co.group(1)) if match_co else 0
                    v_esta_i = int(match_e.group(1)) if match_e else 0
                    v_outr_i = int(match_o.group(1)) if match_o else 0

                # Estado reativo do formulário
                state_11 = {
                    "conc": v_conc_i,
                    "comi": v_comi_i,
                    "esta": v_esta_i,
                    "outr": v_outr_i,
                    "link": evidencia_11_salva
                }

                # Callback do Botão de Salvar
                def cb_processa_e_salva_11():
                    try:
                        c_val = parse_int_seguro(state_11["conc"])
                        co_val = parse_int_seguro(state_11["comi"])
                        e_val = parse_int_seguro(state_11["esta"])
                        o_val = parse_int_seguro(state_11["outr"])
                        lnk_val = str(state_11["link"] or "").strip()

                        total_p = c_val + co_val + e_val
                        pts_calculados = 30.0 if total_p > 0 else 0.0
                        composite_string = f"C:{c_val},Co:{co_val},E:{e_val},O:{o_val}|LINK:{lnk_val}"

                        # Executa salvamento persistente no PostgreSQL
                        salvar_no_banco_11("1.1", str(total_p), pts_calculados, composite_string)
                        
                        # Atualiza dict local em memória
                        res_data["1.1"] = {
                            "valor": str(total_p), 
                            "pontos": pts_calculados, 
                            "link": composite_string
                        }
                        
                        ui.notify("Quesito 1.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.1: {err}", type="negative")

                # Grid com os Inputs Numéricos
                with ui.grid(columns=2).classes("w-full gap-4 mb-4 md:grid-cols-4"):
                    ui.number(
                        "Funcionários concursados:", 
                        value=v_conc_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_11, "conc")
                    
                    ui.number(
                        "Funcionários comissionados:", 
                        value=v_comi_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_11, "comi")
                    
                    ui.number(
                        "Estagiários no suporte e atendimento de primeiro nível:", 
                        value=v_esta_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_11, "esta")
                    
                    ui.number(
                        "Outros:", 
                        value=v_outr_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_11, "outr")

                # Área do Link / Evidência
                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Composição):",
                    value=evidencia_11_salva,
                    placeholder="Insira o link do decreto de lotação de pessoal, relatório do setor de RH ou folha simplificada da TI..."
                ).classes("w-full mb-4").bind_value(state_11, "link")

                # Rodapé do Quesito
                total_pessoal = parse_int_seguro(d11.get("valor"))
                pts_atuais_11 = float(d11.get("pontos") or 0.0)
                cor_txt_11 = "text-green-600" if pts_atuais_11 == 30.0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"👥 Total de Pessoal Efetivo Computado (Concursados + Comissionados + Estagiários): {total_pessoal} funcionário(s)").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 1.1: +{pts_atuais_11:.1f} pontos").classes(f"text-sm font-bold {cor_txt_11}")
                    
                    ui.button("Salvar Quesito 1.1", on_click=cb_processa_e_salva_11, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")

                # Bloco de Comentários Integrado ao Quesito
                bloco_comentarios("1.1", res_data, ano_sel)

            # QUESITO 1.2
            opcoes_12 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.2",
                titulo="Definição de Atribuições Formais da Equipe",
                pergunta="A prefeitura municipal definiu formalmente as atribuições do pessoal do setor de Tecnologia da Informação e Comunicação (TIC)?",
                opcoes=opcoes_12,
                placeholder_link="Insira o link do manual de cargos, decreto de atribuições...",
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # QUESITO 1.3
            opcoes_13 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.3",
                titulo="Capacitação e Treinamento do Pessoal de TIC",
                pergunta="A prefeitura disponibilizou capacitação para o pessoal da área de Tecnologia da Informação e Comunicação (TIC)?",
                opcoes=opcoes_13,
                placeholder_link="Insira o link de certificados emitidos, notas de empenho ou plano de capacitação...",
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 1.3.1 • ÁREAS DE CAPACITAÇÃO EM TIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                # Cabeçalho do Quesito
                ui.label("📌 Quesito 1.3.1 - Áreas de Capacitação em TIC").classes("text-lg font-bold text-blue-900 mb-1")
                
                # Enunciado
                ui.label("Informe em quais áreas houve capacitação:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                
                # Bloco Informativo da Fórmula de Cálculo
                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "Somatório das 4 primeiras opções (a opção 'Outros' não entra na contagem da pontuação)."
                    ).classes("text-sm text-blue-800 font-medium mb-1")
                    ui.label(
                        "• 3 ou 4 itens marcados = 30 pontos\n"
                        "• 2 itens marcados = 15 pontos\n"
                        "• 1 item marcado = 5 pontos\n"
                        "• Somente a opção 'Outros' = 0 pontos"
                    ).classes("text-xs text-blue-900 whitespace-pre-line font-mono")

                # Função local de persistência
                def salvar_no_banco_131(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                # Recuperação do Banco de Dados
                d131 = res_data.get("1.3.1") or {}
                if not isinstance(d131, dict):
                    d131 = {"valor": "", "pontos": 0.0, "link": ""}

                # Parse das opções previamente marcadas (armazenadas separadas por vírgula no campo 'valor')
                valor_salvo = str(d131.get("valor") or "")
                itens_salvos = [i.strip() for i in valor_salvo.split(",") if i.strip()]
                evidencia_131_salva = str(d131.get("link") or "")

                # Estado Reativo das Checkboxes
                state_131 = {
                    "infra": "Infraestrutura e Redes" in itens_salvos,
                    "dev": "Desenvolvimento e Software" in itens_salvos,
                    "dados": "Análise de Dados" in itens_salvos,
                    "gestao": "Gestão e Segurança" in itens_salvos,
                    "outros": "Outros" in itens_salvos,
                    "link": evidencia_131_salva
                }

                # Callback do Botão de Salvar
                def cb_processa_e_salva_131():
                    try:
                        # Identifica os marcados
                        marcados = []
                        if state_131["infra"]:
                            marcados.append("Infraestrutura e Redes")
                        if state_131["dev"]:
                            marcados.append("Desenvolvimento e Software")
                        if state_131["dados"]:
                            marcados.append("Análise de Dados")
                        if state_131["gestao"]:
                            marcados.append("Gestão e Segurança")

                        # Contagem das 4 opções válidas para pontuação
                        qtd_validos = len(marcados)

                        if state_131["outros"]:
                            marcados.append("Outros")

                        # Cálculo de Pontuação
                        if qtd_validos >= 3:
                            pts_calculados = 30.0
                        elif qtd_validos == 2:
                            pts_calculados = 15.0
                        elif qtd_validos == 1:
                            pts_calculados = 5.0
                        else:
                            pts_calculados = 0.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_131["link"] or "").strip()

                        # Salva no PostgreSQL
                        salvar_no_banco_131("1.3.1", valor_string, pts_calculados, lnk_val)

                        # Atualiza em memória
                        res_data["1.3.1"] = {
                            "valor": valor_string,
                            "pontos": pts_calculados,
                            "link": lnk_val
                        }

                        ui.notify("Quesito 1.3.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.3.1: {err}", type="negative")

                # Checkboxes
                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Infraestrutura e Redes").bind_value(state_131, "infra")
                    ui.checkbox("Desenvolvimento e Software").bind_value(state_131, "dev")
                    ui.checkbox("Análise de Dados").bind_value(state_131, "dados")
                    ui.checkbox("Gestão e Segurança").bind_value(state_131, "gestao")
                    ui.checkbox("Outros").bind_value(state_131, "outros")

                # Campo de Link / Evidência
                ui.textarea(
                    "Página Eletrônica (Link / Evidência das Capacitações):",
                    value=evidencia_131_salva,
                    placeholder="Insira o link dos certificados, plano de capacitação, relatório de treinamentos..."
                ).classes("w-full mb-4").bind_value(state_131, "link")

                # Rodapé com Indicador e Botão
                pts_atuais_131 = float(d131.get("pontos") or 0.0)
                cor_txt_131 = "text-green-600" if pts_atuais_131 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Opções selecionadas: {valor_salvo if valor_salvo else 'Nenhuma'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 1.3.1: +{pts_atuais_131:.1f} pontos").classes(f"text-sm font-bold {cor_txt_131}")

                    ui.button("Salvar Quesito 1.3.1", on_click=cb_processa_e_salva_131, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")

                # Bloco de Comentários
                bloco_comentarios("1.3.1", res_data, ano_sel)

# =============================================================================
            # QUESITO 1.4 • PARTICIPAÇÃO DO PESSOAL DE TIC NAS LICITAÇÕES E CONTRATOS
            # =============================================================================
            opcoes_14 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.4",
                titulo="Participação do Pessoal de TIC nas Licitações e Contratos",
                pergunta="Nas licitações e contratos que tenham como soluções o uso de Tecnologia da Informação e Comunicação, houve participação formalizada do pessoal de TIC? (Considerar somente compras com verba municipal)",
                opcoes=opcoes_14,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 1.4.1 • ETAPAS DE PARTICIPAÇÃO DO PESSOAL DE TIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                # Cabeçalho do Quesito
                ui.label("📌 Quesito 1.4.1 - Etapas de Participação do Pessoal de TIC").classes("text-lg font-bold text-blue-900 mb-1")
                
                # Enunciado
                ui.label("Assinale as etapas que o pessoal de TIC participa:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                
                # Bloco Informativo da Pontuação Somada
                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Critérios de pontuação (somatório dos itens marcados):").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Elaboração do edital / Especificação técnica — 15 pontos\n"
                        "• Comissão de Licitação / Equipe de Apoio — 10 pontos\n"
                        "• Recebimento / Gestão de Contrato — 15 pontos"
                    ).classes("text-xs text-blue-900 whitespace-pre-line font-mono mt-1")

                # Função local de persistência no PostgreSQL
                def salvar_no_banco_141(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                # Recuperação do Banco de Dados
                d141 = res_data.get("1.4.1") or {}
                if not isinstance(d141, dict):
                    d141 = {"valor": "", "pontos": 0.0, "link": ""}

                # Parse dos itens marcados armazenados no banco (separados por vírgula)
                valor_salvo = str(d141.get("valor") or "")
                itens_salvos = [i.strip() for i in valor_salvo.split(",") if i.strip()]
                evidencia_141_salva = str(d141.get("link") or "")

                # Estado Reativo das Checkboxes
                state_141 = {
                    "edital": "Elaboração do edital / Especificação técnica" in itens_salvos,
                    "comissao": "Comissão de Licitação / Equipe de Apoio" in itens_salvos,
                    "gestao": "Recebimento / Gestão de Contrato" in itens_salvos,
                    "link": evidencia_141_salva
                }

                # Callback para Calcular a Soma dos Pontos e Salvar
                def cb_processa_e_salva_141():
                    try:
                        marcados = []
                        pontos_acumulados = 0.0

                        if state_141["edital"]:
                            marcados.append("Elaboração do edital / Especificação técnica")
                            pontos_acumulados += 15.0

                        if state_141["comissao"]:
                            marcados.append("Comissão de Licitação / Equipe de Apoio")
                            pontos_acumulados += 10.0

                        if state_141["gestao"]:
                            marcados.append("Recebimento / Gestão de Contrato")
                            pontos_acumulados += 15.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_141["link"] or "").strip()

                        # Grava persistentemente no banco
                        salvar_no_banco_141("1.4.1", valor_string, pontos_acumulados, lnk_val)

                        # Atualiza a estrutura na memória local da página
                        res_data["1.4.1"] = {
                            "valor": valor_string,
                            "pontos": pontos_acumulados,
                            "link": lnk_val
                        }

                        ui.notify("Quesito 1.4.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.4.1: {err}", type="negative")

                # Interface com as 3 opções do quesito
                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Elaboração do edital / Especificação técnica (15 pts)").bind_value(state_141, "edital")
                    ui.checkbox("Comissão de Licitação / Equipe de Apoio (10 pts)").bind_value(state_141, "comissao")
                    ui.checkbox("Recebimento / Gestão de Contrato (15 pts)").bind_value(state_141, "gestao")

                # Campo de Evidências / Links
                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Participação):",
                    value=evidencia_141_salva,
                    placeholder="Insira o link de portarias de nomeação, termos de referência assinados ou atas de comissões..."
                ).classes("w-full mb-4").bind_value(state_141, "link")

                # Exibição dos pontos calculados no rodapé
                pts_atuais_141 = float(d141.get("pontos") or 0.0)
                cor_txt_141 = "text-green-600" if pts_atuais_141 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Etapas Selecionadas: {valor_salvo if valor_salvo else 'Nenhuma'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 1.4.1: +{pts_atuais_141:.1f} pontos").classes(f"text-sm font-bold {cor_txt_141}")

                    ui.button("Salvar Quesito 1.4.1", on_click=cb_processa_e_salva_141, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")

                # Bloco de Comentários Integrado ao Quesito
                bloco_comentarios("1.4.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 1.4.2 • ANÁLISE PRÉVIA PARA CONTRATAÇÃO DE SOFTWARES
            # =============================================================================
            opcoes_142 = {
                "Selecione...": 0.0,
                "Sim, para todos os softwares (20 pts)": 20.0,
                "Sim, para a maior parte dos softwares (15 pts)": 15.0,
                "Sim, para a menor parte dos softwares (08 pts)": 8.0,
                "Não foi realizado (00 pts)": 0.0,
                "Não foi adquirido nenhum software nos últimos 5 anos (20 pts)": 20.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.4.2",
                titulo="Análise Prévias para Aquisição de Softwares",
                pergunta="Sobre programas de computador (softwares) adquiridos ou licenciados nos últimos 5 anos, foi realizada análise ou estudo antes de sua contratação com a participação do pessoal de Tecnologia da Informação e Comunicação (TIC)?",
                opcoes=opcoes_142,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 2.0 • PLANO DIRETOR DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO (PDTIC)
            # =============================================================================
            opcoes_20 = {
                "Selecione...": 0.0,
                "SIM, com metas acima de 02 anos (40 pts)": 40.0,
                "SIM, com metas para até 02 anos (30 pts)": 30.0,
                "NÃO POSSUI PDTIC (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="2.0",
                titulo="Plano Diretor de Tecnologia da Informação e Comunicação - PDTIC",
                pergunta="A prefeitura municipal possui um PDTIC – Plano Diretor de Tecnologia da Informação e Comunicação – vigente que estabeleça diretrizes e metas de atingimento no futuro?",
                opcoes=opcoes_20,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 2.1 • PÁGINA ELETRÔNICA DO PDTIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 2.1 - Divulgação do PDTIC").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe a página eletrônica (link na internet) do PDTIC:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                
                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Se informado o texto 'XYZ' (ou caso não esteja disponível) — 0 pontos\n"
                        "• Se informada URL/Link válido (diferente de 'XYZ') — 20 pontos"
                    ).classes("text-xs text-blue-900 font-medium")

                def salvar_no_banco_21(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d21 = res_data.get("2.1") or {}
                if not isinstance(d21, dict):
                    d21 = {"valor": "", "pontos": 0.0, "link": ""}

                link_salvo_21 = str(d21.get("link") or d21.get("valor") or "")
                state_21 = {"link": link_salvo_21}

                def cb_processa_e_salva_21():
                    try:
                        lnk_input = str(state_21["link"] or "").strip()
                        if not lnk_input or lnk_input.upper() == "XYZ":
                            pts_calc = 0.0
                            val_str = "XYZ"
                        else:
                            pts_calc = 20.0
                            val_str = lnk_input

                        salvar_no_banco_21("2.1", val_str, pts_calc, lnk_input)
                        res_data["2.1"] = {"valor": val_str, "pontos": pts_calc, "link": lnk_input}
                        ui.notify("Quesito 2.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 2.1: {err}", type="negative")

                ui.input(
                    "Página Eletrônica (Link do PDTIC):",
                    value=link_salvo_21,
                    placeholder="Cole o link aqui ou digite XYZ se não estiver disponível..."
                ).classes("w-full mb-4").bind_value(state_21, "link")

                pts_atuais_21 = float(d21.get("pontos") or 0.0)
                cor_txt_21 = "text-green-600" if pts_atuais_21 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📊 Impacto de Pontuação no Quesito 2.1: +{pts_atuais_21:.1f} pontos").classes(f"text-sm font-bold {cor_txt_21}")
                    ui.button("Salvar Quesito 2.1", on_click=cb_processa_e_salva_21, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("2.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 2.2 • ALOCAÇÃO DE RECURSOS NO PDTIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 2.2 - Conteúdo do Plano de TIC").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("O plano de TIC vigente contempla:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Critérios de pontuação (10 pontos por item assinalado):").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Alocação de recursos orçamentários — 10 pontos\n"
                        "• Alocação de recursos humanos — 10 pontos\n"
                        "• Alocação de recursos materiais — 10 pontos\n"
                        "• Estratégia de execução indireta (terceirização) — 10 pontos"
                    ).classes("text-xs text-blue-900 whitespace-pre-line font-mono mt-1")

                def salvar_no_banco_22(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d22 = res_data.get("2.2") or {}
                if not isinstance(d22, dict):
                    d22 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_22 = str(d22.get("valor") or "")
                itens_salvos_22 = [i.strip() for i in valor_salvo_22.split(",") if i.strip()]
                evidencia_22_salva = str(d22.get("link") or "")

                state_22 = {
                    "orc": "Alocação de recursos orçamentários" in itens_salvos_22,
                    "rh": "Alocação de recursos humanos" in itens_salvos_22,
                    "mat": "Alocação de recursos materiais" in itens_salvos_22,
                    "terc": "Estratégia de execução indireta (terceirização)" in itens_salvos_22,
                    "link": evidencia_22_salva
                }

                def cb_processa_e_salva_22():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        if state_22["orc"]:
                            marcados.append("Alocação de recursos orçamentários")
                            pts_acumulados += 10.0
                        if state_22["rh"]:
                            marcados.append("Alocação de recursos humanos")
                            pts_acumulados += 10.0
                        if state_22["mat"]:
                            marcados.append("Alocação de recursos materiais")
                            pts_acumulados += 10.0
                        if state_22["terc"]:
                            marcados.append("Estratégia de execução indireta (terceirização)")
                            pts_acumulados += 10.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_22["link"] or "").strip()

                        salvar_no_banco_22("2.2", valor_string, pts_acumulados, lnk_val)
                        res_data["2.2"] = {"valor": valor_string, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 2.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 2.2: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Alocação de recursos orçamentários (10 pts)").bind_value(state_22, "orc")
                    ui.checkbox("Alocação de recursos humanos (10 pts)").bind_value(state_22, "rh")
                    ui.checkbox("Alocação de recursos materiais (10 pts)").bind_value(state_22, "mat")
                    ui.checkbox("Estratégia de execução indireta - terceirização (10 pts)").bind_value(state_22, "terc")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência dos itens do PDTIC):",
                    value=evidencia_22_salva,
                    placeholder="Insira o link com a indicação dos capítulos ou páginas do documento..."
                ).classes("w-full mb-4").bind_value(state_22, "link")

                pts_atuais_22 = float(d22.get("pontos") or 0.0)
                cor_txt_22 = "text-green-600" if pts_atuais_22 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Itens Selecionados: {valor_salvo_22 if valor_salvo_22 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 2.2: +{pts_atuais_22:.1f} pontos").classes(f"text-sm font-bold {cor_txt_22}")

                    ui.button("Salvar Quesito 2.2", on_click=cb_processa_e_salva_22, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("2.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 2.3 • DATA DA ÚLTIMA ATUALIZAÇÃO DO PDTIC
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 2.3 - Atualização do PDTIC").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Qual a data da última atualização do PDTIC? (Se não foi atualizado, informar a data da publicação)").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo (Tempo decorrido até o ano de referência):").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Data <= 5 anos — 20 pontos\n"
                        "• 5 < Data <= 10 anos — 10 pontos\n"
                        "• Data > 10 anos (ou não informado) — 0 pontos"
                    ).classes("text-xs text-blue-900 font-medium")

                def salvar_no_banco_23(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d23 = res_data.get("2.3") or {}
                if not isinstance(d23, dict):
                    d23 = {"valor": "", "pontos": 0.0, "link": ""}

                val_data_salva = str(d23.get("valor") or "")
                evidencia_23_salva = str(d23.get("link") or "")

                state_23 = {
                    "data": val_data_salva,
                    "link": evidencia_23_salva
                }

                def cb_processa_e_salva_23():
                    try:
                        from datetime import datetime
                        dt_str = str(state_23["data"] or "").strip()
                        pts_calc = 0.0

                        if dt_str:
                            try:
                                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d")
                            except ValueError:
                                try:
                                    dt_obj = datetime.strptime(dt_str, "%d/%m/%Y")
                                except ValueError:
                                    dt_obj = None

                            if dt_obj:
                                ano_ref = int(ano_sel)
                                anos_diferenca = ano_ref - dt_obj.year

                                if anos_diferenca <= 5:
                                    pts_calc = 20.0
                                elif 5 < anos_diferenca <= 10:
                                    pts_calc = 10.0
                                else:
                                    pts_calc = 0.0

                        lnk_val = str(state_23["link"] or "").strip()
                        salvar_no_banco_23("2.3", dt_str, pts_calc, lnk_val)
                        res_data["2.3"] = {"valor": dt_str, "pontos": pts_calc, "link": lnk_val}

                        ui.notify("Quesito 2.3 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 2.3: {err}", type="negative")

                ui.input(
                    "Data da publicação/atualização (AAAA-MM-DD ou DD/MM/AAAA):",
                    value=val_data_salva,
                    placeholder="Exemplo: 2024-05-10"
                ).classes("w-full mb-4").bind_value(state_23, "data")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Data):",
                    value=evidencia_23_salva,
                    placeholder="Link do diário oficial ou portaria que comprova a data..."
                ).classes("w-full mb-4").bind_value(state_23, "link")

                pts_atuais_23 = float(d23.get("pontos") or 0.0)
                cor_txt_23 = "text-green-600" if pts_atuais_23 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📊 Impacto de Pontuação no Quesito 2.3: +{pts_atuais_23:.1f} pontos").classes(f"text-sm font-bold {cor_txt_23}")
                    ui.button("Salvar Quesito 2.3", on_click=cb_processa_e_salva_23, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("2.3", res_data, ano_sel)

            # =============================================================================
            # QUESITO 3.0 • POLÍTICA DE SEGURANÇA DA INFORMAÇÃO (POSI)
            # =============================================================================
            opcoes_30 = {
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.0",
                titulo="Política de Segurança da Informação",
                pergunta="A Prefeitura dispõe de Política de Segurança da Informação formalmente instituída e de cumprimento obrigatório?",
                opcoes=opcoes_30,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 3.1 • TERMO DE RESPONSABILIDADE / COMPROMISSO
            # =============================================================================
            opcoes_31 = {
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.1",
                titulo="Termo de Responsabilidade / Compromisso",
                pergunta="A Prefeitura estabelece procedimentos e responsabilidades quanto ao uso da tecnologia da informação pelos funcionários municipais, conhecido como Termo de Responsabilidade/Compromisso?",
                opcoes=opcoes_31,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.1.1 • USO DE ASSINATURA ELETRÔNICA NO TERMO
            # =============================================================================
            opcoes_311 = {
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.1.1",
                titulo="Uso da Assinatura Eletrônica no Termo",
                pergunta="O Termo de Responsabilidade/Compromisso dispõe sobre o uso da assinatura eletrônica pelos funcionários municipais?",
                opcoes=opcoes_311,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.1.1.1 • TIPO DE ASSINATURA ELETRÔNICA UTILIZADA
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 3.1.1.1 - Tipo de Assinatura Eletrônica").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe o tipo de assinatura eletrônica utilizada nos documentos digitais:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Critérios de pontuação:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Assinatura eletrônica de uso gratuito — 10 pontos\n"
                        "• Assinatura eletrônica onerosa — 0 pontos"
                    ).classes("text-xs text-blue-900 font-mono mt-1")

                def salvar_no_banco_3111(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d3111 = res_data.get("3.1.1.1") or {}
                if not isinstance(d3111, dict):
                    d3111 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_3111 = str(d3111.get("valor") or "")
                itens_salvos_3111 = [i.strip() for i in valor_salvo_3111.split(",") if i.strip()]
                evidencia_3111_salva = str(d3111.get("link") or "")

                state_3111 = {
                    "gratuita": "Assinatura eletrônica de uso gratuito" in itens_salvos_3111,
                    "onerosa": "Assinatura eletrônica onerosa" in itens_salvos_3111,
                    "link": evidencia_3111_salva
                }

                def cb_processa_e_salva_3111():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        if state_3111["gratuita"]:
                            marcados.append("Assinatura eletrônica de uso gratuito")
                            pts_acumulados += 10.0
                        if state_3111["onerosa"]:
                            marcados.append("Assinatura eletrônica onerosa")
                            pts_acumulados += 0.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_3111["link"] or "").strip()

                        salvar_no_banco_3111("3.1.1.1", valor_string, pts_acumulados, lnk_val)
                        res_data["3.1.1.1"] = {"valor": valor_string, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 3.1.1.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 3.1.1.1: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Assinatura eletrônica de uso gratuito (10 pts)").bind_value(state_3111, "gratuita")
                    ui.checkbox("Assinatura eletrônica onerosa (00 pts)").bind_value(state_3111, "onerosa")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Assinatura):",
                    value=evidencia_3111_salva,
                    placeholder="Link da ferramenta ou contrato da solução..."
                ).classes("w-full mb-4").bind_value(state_3111, "link")

                pts_atuais_3111 = float(d3111.get("pontos") or 0.0)
                cor_txt_3111 = "text-green-600" if pts_atuais_3111 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Selecionados: {valor_salvo_3111 if valor_salvo_3111 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 3.1.1.1: +{pts_atuais_3111:.1f} pontos").classes(f"text-sm font-bold {cor_txt_3111}")

                    ui.button("Salvar Quesito 3.1.1.1", on_click=cb_processa_e_salva_3111, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("3.1.1.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 3.2 • IDENTIFICAÇÃO DE RISCOS (ISO/IEC 27000)
            # =============================================================================
            opcoes_32 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.2",
                titulo="Identificação de Riscos de TIC (ISO/IEC 27000)",
                pergunta="Os riscos de TIC são identificados de acordo com as normas brasileiras da família ISO/IEC 27000?",
                opcoes=opcoes_32,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.2.1 • NORMAS ISO/IEC 27000 UTILIZADAS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 3.2.1 - Normas ISO/IEC 27000 Aplicadas").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe quais normas da família ISO/IEC 27000 são utilizadas nos processos de segurança no uso de TIC:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Pontuação por norma selecionada:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• ISO/IEC 27000 — 1,5 pontos\n"
                        "• ISO/IEC 27001 — 1,5 pontos\n"
                        "• ISO/IEC 27002 — 1,5 pontos\n"
                        "• ISO/IEC 27003 — 1,5 pontos\n"
                        "• ISO/IEC 27004 — 2,0 pontos\n"
                        "• ISO/IEC 27005 — 2,0 pontos"
                    ).classes("text-xs text-blue-900 font-mono mt-1")

                def salvar_no_banco_321(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d321 = res_data.get("3.2.1") or {}
                if not isinstance(d321, dict):
                    d321 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_321 = str(d321.get("valor") or "")
                itens_salvos_321 = [i.strip() for i in valor_salvo_321.split(",") if i.strip()]
                evidencia_321_salva = str(d321.get("link") or "")

                state_321 = {
                    "27000": "ISO/IEC 27000" in itens_salvos_321,
                    "27001": "ISO/IEC 27001" in itens_salvos_321,
                    "27002": "ISO/IEC 27002" in itens_salvos_321,
                    "27003": "ISO/IEC 27003" in itens_salvos_321,
                    "27004": "ISO/IEC 27004" in itens_salvos_321,
                    "27005": "ISO/IEC 27005" in itens_salvos_321,
                    "link": evidencia_321_salva
                }

                def cb_processa_e_salva_321():
                    try:
                        marcados = []
                        pts_acumulados = 0.0

                        if state_321["27000"]:
                            marcados.append("ISO/IEC 27000")
                            pts_acumulados += 1.5
                        if state_321["27001"]:
                            marcados.append("ISO/IEC 27001")
                            pts_acumulados += 1.5
                        if state_321["27002"]:
                            marcados.append("ISO/IEC 27002")
                            pts_acumulados += 1.5
                        if state_321["27003"]:
                            marcados.append("ISO/IEC 27003")
                            pts_acumulados += 1.5
                        if state_321["27004"]:
                            marcados.append("ISO/IEC 27004")
                            pts_acumulados += 2.0
                        if state_321["27005"]:
                            marcados.append("ISO/IEC 27005")
                            pts_acumulados += 2.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_321["link"] or "").strip()

                        salvar_no_banco_321("3.2.1", valor_string, pts_acumulados, lnk_val)
                        res_data["3.2.1"] = {"valor": valor_string, "pontos": pts_acumulados, "link": lnk_val}
                        ui.notify("Quesito 3.2.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 3.2.1: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("ISO/IEC 27000 (1,5 pts)").bind_value(state_321, "27000")
                    ui.checkbox("ISO/IEC 27001 (1,5 pts)").bind_value(state_321, "27001")
                    ui.checkbox("ISO/IEC 27002 (1,5 pts)").bind_value(state_321, "27002")
                    ui.checkbox("ISO/IEC 27003 (1,5 pts)").bind_value(state_321, "27003")
                    ui.checkbox("ISO/IEC 27004 (2,0 pts)").bind_value(state_321, "27004")
                    ui.checkbox("ISO/IEC 27005 (2,0 pts)").bind_value(state_321, "27005")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Adoção das Normas):",
                    value=evidencia_321_salva,
                    placeholder="Insira o link para relatórios de auditoria, políticas ou mapeamento de processos..."
                ).classes("w-full mb-4").bind_value(state_321, "link")

                pts_atuais_321 = float(d321.get("pontos") or 0.0)
                cor_txt_321 = "text-green-600" if pts_atuais_321 > 0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Normas Aplicadas: {valor_salvo_321 if valor_salvo_321 else 'Nenhuma'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 3.2.1: +{pts_atuais_321:.1f} pontos").classes(f"text-sm font-bold {cor_txt_321}")

                    ui.button("Salvar Quesito 3.2.1", on_click=cb_processa_e_salva_321, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("3.2.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 3.3 • IDENTIFICAÇÃO DE RISCOS (ABNT NBR ISO/IEC 31000)
            # =============================================================================
            opcoes_33 = {
                "Selecione...": 0.0,
                "Sim (30 pts)": 30.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.3",
                titulo="Identificação de Riscos (ISO/IEC 31000)",
                pergunta="Os riscos de TIC são identificados de acordo com as normas da ABNT NBR ISO/IEC 31000? (Nota: Se tiver apenas antivírus e firewall, a resposta é NÃO)",
                opcoes=opcoes_33,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.4 • PLANO DE CONTINUIDADE DOS SERVIÇOS DE TIC
            # =============================================================================
            opcoes_34 = {
                "Selecione...": 0.0,
                "Sim (30 pts)": 30.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.4",
                titulo="Plano de Continuidade dos Serviços de TIC",
                pergunta="A Prefeitura possui um Plano de Continuidade dos Serviços de Tecnologia da Informação e Comunicação (TIC)?",
                opcoes=opcoes_34,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.5 • POLÍTICA DE CÓPIAS DE SEGURANÇA (BACKUP)
            # =============================================================================
            opcoes_35 = {
                "Selecione...": 0.0,
                "Sim (30 pts)": 30.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.5",
                titulo="Política de Cópias de Segurança (Backup)",
                pergunta="A Prefeitura dispõe de política de cópias de segurança (backup) formalmente instituída como norma de cumprimento obrigatório?",
                opcoes=opcoes_35,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 3.6 • INVENTÁRIO ATUALIZADO DOS ATIVOS DE TIC
            # =============================================================================
            opcoes_36 = {
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="3.6",
                titulo="Inventário Atualizado dos Ativos de TIC",
                pergunta="A Prefeitura possui inventário atualizado dos ativos de TIC? (Considerar switches, roteadores, servidores, firewalls, SOs, backup, storages, etc.)",
                opcoes=opcoes_36,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 3.6.1 • COMPOSIÇÃO DA BASE DE ATIVOS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 3.6.1 - Composição da Base de Ativos").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Como é composta a base de ativos:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_361(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d361 = res_data.get("3.6.1") or {}
                if not isinstance(d361, dict):
                    d361 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_361 = str(d361.get("valor") or "")
                itens_salvos_361 = [i.strip() for i in valor_salvo_361.split(",") if i.strip()]
                evidencia_361_salva = str(d361.get("link") or "")

                state_361 = {
                    "inf": "Ativos de informação" in itens_salvos_361,
                    "soft": "Ativos de software" in itens_salvos_361,
                    "fis": "Ativos físicos" in itens_salvos_361,
                    "serv": "Serviços" in itens_salvos_361,
                    "pess": "Pessoas e suas qualificações, habilidades e experiências" in itens_salvos_361,
                    "link": evidencia_361_salva
                }

                def cb_processa_e_salva_361():
                    try:
                        marcados = []
                        if state_361["inf"]: marcados.append("Ativos de informação")
                        if state_361["soft"]: marcados.append("Ativos de software")
                        if state_361["fis"]: marcados.append("Ativos físicos")
                        if state_361["serv"]: marcados.append("Serviços")
                        if state_361["pess"]: marcados.append("Pessoas e suas qualificações, habilidades e experiências")

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_361["link"] or "").strip()

                        salvar_no_banco_361("3.6.1", valor_string, 0.0, lnk_val)
                        res_data["3.6.1"] = {"valor": valor_string, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 3.6.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 3.6.1: {err}", type="negative")

                with ui.column().classes("w-full gap-2 mb-4"):
                    ui.checkbox("Ativos de informação").bind_value(state_361, "inf")
                    ui.checkbox("Ativos de software").bind_value(state_361, "soft")
                    ui.checkbox("Ativos físicos").bind_value(state_361, "fis")
                    ui.checkbox("Serviços").bind_value(state_361, "serv")
                    ui.checkbox("Pessoas e suas qualificações, habilidades e experiências").bind_value(state_361, "pess")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Base de Ativos):",
                    value=evidencia_361_salva,
                    placeholder="Link do sistema de inventário ou documento..."
                ).classes("w-full mb-4").bind_value(state_361, "link")

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📋 Selecionados: {valor_salvo_361 if valor_salvo_361 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                    ui.button("Salvar Quesito 3.6.1", on_click=cb_processa_e_salva_361, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("3.6.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 4.0 • REGULAMENTAÇÃO DA LEI DE ACESSO À INFORMAÇÃO (LAI)
            # =============================================================================
            opcoes_40 = {
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="4.0",
                titulo="Regulamentação da Lei de Acesso à Informação (LAI)",
                pergunta="O município regulamentou a Lei de Acesso à Informação? (Lei Federal nº 12.527/2011, art. 45)",
                opcoes=opcoes_40,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 4.1 • DADOS NORMATIVOS DA LAI
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 4.1 - Dados do Instrumento Normativo da LAI").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_41(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d41 = res_data.get("4.1") or {}
                if not isinstance(d41, dict):
                    d41 = {"valor": "", "pontos": 0.0, "link": ""}

                val_salvo_41 = str(d41.get("valor") or "")
                state_41 = {"texto": val_salvo_41}

                def cb_processa_e_salva_41():
                    try:
                        txt_val = str(state_41["texto"] or "").strip()
                        salvar_no_banco_41("4.1", txt_val, 0.0, "")
                        res_data["4.1"] = {"valor": txt_val, "pontos": 0.0, "link": ""}
                        ui.notify("Quesito 4.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 4.1: {err}", type="negative")

                ui.input(
                    "Instrumento normativo, Número e Data:",
                    value=val_salvo_41,
                    placeholder="Ex: Decreto Municipal nº 1.234, de 15 de Maio de 2015"
                ).classes("w-full mb-4").bind_value(state_41, "texto")

                with ui.row().classes("w-full justify-end mb-4"):
                    ui.button("Salvar Quesito 4.1", on_click=cb_processa_e_salva_41, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("4.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 4.2 • LINK DA NORMA DA LAI
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 4.2 - Link do Instrumento Normativo da LAI").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Página eletrônica (link na internet) do instrumento normativo:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label("• Se informado XYZ = 0 pontos\n• Se diferente de XYZ = 0 pontos (Registro informativo)").classes("text-xs text-blue-900 font-medium")

                def salvar_no_banco_42(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d42 = res_data.get("4.2") or {}
                if not isinstance(d42, dict):
                    d42 = {"valor": "", "pontos": 0.0, "link": ""}

                link_salvo_42 = str(d42.get("link") or d42.get("valor") or "")
                state_42 = {"link": link_salvo_42}

                def cb_processa_e_salva_42():
                    try:
                        lnk_input = str(state_42["link"] or "").strip()
                        val_str = "XYZ" if not lnk_input or lnk_input.upper() == "XYZ" else lnk_input
                        salvar_no_banco_42("4.2", val_str, 0.0, lnk_input)
                        res_data["4.2"] = {"valor": val_str, "pontos": 0.0, "link": lnk_input}
                        ui.notify("Quesito 4.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 4.2: {err}", type="negative")

                ui.input(
                    "Página Eletrônica (Link da LAI):",
                    value=link_salvo_42,
                    placeholder="Cole o link aqui ou digite XYZ..."
                ).classes("w-full mb-4").bind_value(state_42, "link")

                with ui.row().classes("w-full justify-end mb-4"):
                    ui.button("Salvar Quesito 4.2", on_click=cb_processa_e_salva_42, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("4.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 5.0 • LEI SOBRE EFICIÊNCIA PÚBLICA (GOVERNO DIGITAL)
            # =============================================================================
            opcoes_50 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.0",
                titulo="Regulamentação do Governo Digital",
                pergunta="O município regulamentou a Lei sobre Eficiência Pública (Governo Digital)? (Lei Federal nº 14.129/2021)",
                opcoes=opcoes_50,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 5.1 • DADOS NORMATIVOS DO GOVERNO DIGITAL
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 5.1 - Dados do Instrumento Normativo do Governo Digital").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_51(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d51 = res_data.get("5.1") or {}
                if not isinstance(d51, dict):
                    d51 = {"valor": "", "pontos": 0.0, "link": ""}

                val_salvo_51 = str(d51.get("valor") or "")
                state_51 = {"texto": val_salvo_51}

                def cb_processa_e_salva_51():
                    try:
                        txt_val = str(state_51["texto"] or "").strip()
                        salvar_no_banco_51("5.1", txt_val, 0.0, "")
                        res_data["5.1"] = {"valor": txt_val, "pontos": 0.0, "link": ""}
                        ui.notify("Quesito 5.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 5.1: {err}", type="negative")

                ui.input(
                    "Instrumento normativo, Número e Data:",
                    value=val_salvo_51,
                    placeholder="Ex: Lei Municipal nº 5.678, de 10 de Março de 2022"
                ).classes("w-full mb-4").bind_value(state_51, "texto")

                with ui.row().classes("w-full justify-end mb-4"):
                    ui.button("Salvar Quesito 5.1", on_click=cb_processa_e_salva_51, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("5.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 5.2 • LINK DA NORMA DO GOVERNO DIGITAL
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 5.2 - Link da Norma de Governo Digital").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Página eletrônica (link na internet) do instrumento normativo:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label("• Se não estiver disponível na internet, inserir XYZ").classes("text-xs text-blue-900 font-medium")

                def salvar_no_banco_52(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d52 = res_data.get("5.2") or {}
                if not isinstance(d52, dict):
                    d52 = {"valor": "", "pontos": 0.0, "link": ""}

                link_salvo_52 = str(d52.get("link") or d52.get("valor") or "")
                state_52 = {"link": link_salvo_52}

                def cb_processa_e_salva_52():
                    try:
                        lnk_input = str(state_52["link"] or "").strip()
                        val_str = "XYZ" if not lnk_input or lnk_input.upper() == "XYZ" else lnk_input
                        salvar_no_banco_52("5.2", val_str, 0.0, lnk_input)
                        res_data["5.2"] = {"valor": val_str, "pontos": 0.0, "link": lnk_input}
                        ui.notify("Quesito 5.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 5.2: {err}", type="negative")

                ui.input(
                    "Página Eletrônica (Link da Norma):",
                    value=link_salvo_52,
                    placeholder="Cole o link aqui ou digite XYZ..."
                ).classes("w-full mb-4").bind_value(state_52, "link")

                with ui.row().classes("w-full justify-end mb-4"):
                    ui.button("Salvar Quesito 5.2", on_click=cb_processa_e_salva_52, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("5.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 5.3 • TRÂMITE DIGITAL DE PROCESSOS ADMINISTRATIVOS
            # =============================================================================
            opcoes_53 = {
                "Selecione...": 0.0,
                "Sim, para todos os processos administrativos": 0.0,
                "Sim, para a maior parte dos processos administrativos": 0.0,
                "Sim, para a menor parte dos processos administrativos": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="5.3",
                titulo="Soluções Digitais para Trâmite Processual",
                pergunta="A Prefeitura implantou soluções digitais para trâmite de processos administrativos?",
                opcoes=opcoes_53,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 6.0 • MANUTENÇÃO DO SITE DA PREFEITURA NA INTERNET
            # =============================================================================
            opcoes_60 = {
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.0",
                titulo="Portal Institucional / Site na Internet",
                pergunta="A prefeitura mantém site na Internet com informações atualizadas?",
                opcoes=opcoes_60,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 6.1 • FERRAMENTA DE PESQUISA/BUSCA INTERNA DE CONTEÚDO
            # =============================================================================
            opcoes_61 = {
                "Selecione...": 0.0,
                "Sim, para todo o conteúdo do site (20 pts)": 20.0,
                "Sim, para a maior parte do conteúdo do site (10 pts)": 10.0,
                "Sim, para a menor parte do conteúdo do site (05 pts)": 5.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.1",
                titulo="Ferramenta de Pesquisa / Busca Interna",
                pergunta="O site eletrônico da prefeitura continha ferramenta de pesquisa/busca interna de conteúdo? (Não considerar a opção de busca do próprio browser - Ctrl + F)",
                opcoes=opcoes_61,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 6.2 • DOWNLOAD DE DADOS EM FORMATOS ABERTOS E NÃO PROPRIETÁRIOS
            # =============================================================================
            opcoes_62 = {
                "Selecione...": 0.0,
                "Possibilita para todos os relatórios (20 pts)": 20.0,
                "Possibilita para a maior parte dos relatórios (10 pts)": 10.0,
                "Possibilita para a menor parte dos relatórios (05 pts)": 5.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.2",
                titulo="Download em Formatos Abertos e Não Proprietários",
                pergunta="O site possibilita o download de dados/informações em formatos abertos e não proprietários? (Exemplos: JSON, XML, CSV, ODS, RDF, etc.)",
                opcoes=opcoes_62,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 6.3 • RESPOSTAS A PERGUNTAS MAIS FREQUENTES (FAQ)
            # =============================================================================
            opcoes_63 = {
                "Selecione...": 0.0,
                "Sim (10 pts)": 10.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.3",
                titulo="Perguntas Mais Frequentes (FAQ)",
                pergunta="O site disponibiliza as respostas a perguntas mais frequentes da sociedade?",
                opcoes=opcoes_63,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 6.4 • ACESSIBILIDADE DE CONTEÚDO PARA PESSOAS COM DEFICIÊNCIA
            # =============================================================================
            opcoes_64 = {
                "Selecione...": 0.0,
                "Sim, para todo o conteúdo do site (30 pts)": 30.0,
                "Sim, para a maior parte do conteúdo do site (15 pts)": 15.0,
                "Sim, para a menor parte do conteúdo do site (05 pts)": 5.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="6.4",
                titulo="Acessibilidade de Conteúdo para PCD",
                pergunta="O site disponibiliza acessibilidade de conteúdo para pessoas com deficiência?",
                opcoes=opcoes_64,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 7.0 • DISPONIBILIZAÇÃO DO e-SIC NO SITE
            # =============================================================================
            opcoes_70 = {
                "Selecione...": 0.0,
                "Sim (25 pts)": 25.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.0",
                titulo="Serviço de Informação ao Cidadão (e-SIC)",
                pergunta="A Prefeitura disponibiliza no site o Serviço de Informação ao Cidadão/e-SIC (Lei Federal nº 12.527/2011)?",
                opcoes=opcoes_70,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 7.1 • SOLICITAÇÃO SIMPLIFICADA NO e-SIC
            # =============================================================================
            opcoes_71 = {
                "Selecione...": 0.0,
                "Sim (10 pts)": 10.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.1",
                titulo="Solicitação Simplificada no e-SIC",
                pergunta="A solicitação por meio do e-SIC é simplificada (sem a exigência de itens de identificação do requerente e demais dados desnecessários à solicitação)?",
                opcoes=opcoes_71,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 7.2 • ACOMPANHAMENTO DA SOLICITAÇÃO NO e-SIC
            # =============================================================================
            opcoes_72 = {
                "Selecione...": 0.0,
                "Sim (10 pts)": 10.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.2",
                titulo="Acompanhamento de Solicitação no e-SIC",
                pergunta="O Serviço de Informação ao Cidadão/e-SIC apresentou possibilidade de acompanhamento da solicitação?",
                opcoes=opcoes_72,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 7.3 • EXIGÊNCIA DE MOTIVOS PARA A SOLICITAÇÃO
            # =============================================================================
            opcoes_73 = {
                "Selecione...": 0.0,
                "Sim (00 pts)": 0.0,
                "Não (05 pts)": 5.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="7.3",
                titulo="Exigência de Motivação para Solicitação",
                pergunta="Há necessidade de ser informado os motivos para a solicitação de informações de interesse público?",
                opcoes=opcoes_73,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# =============================================================================
            # QUESITO 8.0 • SOFTWARES PARA GESTÃO DE PROCESSOS
            # =============================================================================
            opcoes_80 = {
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.0",
                titulo="Softwares para Gestão de Processos",
                pergunta="A Prefeitura possui programas de computador (softwares) para gestão de processos? (Ex: Contabilidade, Tributos, Dívida Ativa, etc. Próprio ou terceirizado)",
                opcoes=opcoes_80,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 8.1 • SETORES ENGLOBADOS PELOS SOFTWARES
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 8.1 - Processos e Setores Englobados por Softwares").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Os programas de computador (softwares) englobam quais processos/setores?").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_81(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d81 = res_data.get("8.1") or {}
                if not isinstance(d81, dict):
                    d81 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_81 = str(d81.get("valor") or "")
                itens_salvos_81 = [i.strip() for i in valor_salvo_81.split(",") if i.strip()]
                evidencia_81_salva = str(d81.get("link") or "")

                opcoes_setores_81 = [
                    "Contabilidade", "Gestão de tributos (arrecadação)", "Dívida Ativa", "Precatórios",
                    "Gestão patrimonial (bens e equipamentos)", "Gestão de negócios (Business Intelligence)",
                    "Planejamento", "Recursos humanos / Departamento pessoal", "Almoxarifado",
                    "Controle de frotas", "Controle Interno", "Saúde", "Ensino (educação)",
                    "Compras, licitações e contratos", "Certidões e alvarás", "Saneamento", "Cemitérios"
                ]

                state_81 = {item: item in itens_salvos_81 for item in opcoes_setores_81}
                state_81["link"] = evidencia_81_salva

                def cb_processa_e_salva_81():
                    try:
                        marcados = [item for item in opcoes_setores_81 if state_81[item]]
                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_81["link"] or "").strip()

                        salvar_no_banco_81("8.1", valor_string, 0.0, lnk_val)
                        res_data["8.1"] = {"valor": valor_string, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 8.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 8.1: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in opcoes_setores_81:
                        ui.checkbox(item).bind_value(state_81, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência dos Softwares):",
                    value=evidencia_81_salva,
                    placeholder="Link dos manuais, contratos ou telas dos sistemas..."
                ).classes("w-full mb-4").bind_value(state_81, "link")

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📋 Selecionados ({len([i for i in opcoes_setores_81 if state_81[i]])}): {valor_salvo_81 if valor_salvo_81 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                    ui.button("Salvar Quesito 8.1", on_click=cb_processa_e_salva_81, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("8.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 8.2 • SISTEMAS INTEGRADOS À CONTABILIDADE
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 8.2 - Sistemas Integrados ao Sistema de Contabilidade").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe quais sistemas encontram-se integrados ao Sistema de Contabilidade do município:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_82(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d82 = res_data.get("8.2") or {}
                if not isinstance(d82, dict):
                    d82 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_82 = str(d82.get("valor") or "")
                itens_salvos_82 = [i.strip() for i in valor_salvo_82.split(",") if i.strip()]
                evidencia_82_salva = str(d82.get("link") or "")

                opcoes_integracao_82 = [
                    "Gestão de tributos (arrecadação)", "Dívida Ativa", "Precatórios",
                    "Gestão patrimonial (bens e equipamentos)", "Gestão de negócios (Business Intelligence)",
                    "Planejamento", "Recursos humanos / Departamento pessoal", "Almoxarifado",
                    "Controle de frotas", "Controle Interno", "Saúde", "Ensino (educação)",
                    "Compras, licitações e contratos", "Certidões e alvarás", "Saneamento", "Cemitérios"
                ]

                state_82 = {item: item in itens_salvos_82 for item in opcoes_integracao_82}
                state_82["link"] = evidencia_82_salva

                def cb_processa_e_salva_82():
                    try:
                        marcados = [item for item in opcoes_integracao_82 if state_82[item]]
                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_82["link"] or "").strip()

                        salvar_no_banco_82("8.2", valor_string, 0.0, lnk_val)
                        res_data["8.2"] = {"valor": valor_string, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 8.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 8.2: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in opcoes_integracao_82:
                        ui.checkbox(item).bind_value(state_82, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Integração):",
                    value=evidencia_82_salva,
                    placeholder="Link da documentação de integração ou declaração..."
                ).classes("w-full mb-4").bind_value(state_82, "link")

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📋 Integrados ({len([i for i in opcoes_integracao_82 if state_82[i]])}): {valor_salvo_82 if valor_salvo_82 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                    ui.button("Salvar Quesito 8.2", on_click=cb_processa_e_salva_82, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("8.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 8.2.1 • NÍVEL DE INTEGRAÇÃO DÍVIDA ATIVA X CONTABILIDADE
            # =============================================================================
            opcoes_821 = {
                "Selecione...": 0.0,
                "Totalmente integrado (Inscrição / Atualização e Baixa) (50 pts)": 50.0,
                "Somente as Inscrições / Atualizações estão integradas (10 pts)": 10.0,
                "Somente as Baixas estão integradas (10 pts)": 10.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.2.1",
                titulo="Nível de Integração: Dívida Ativa x Contabilidade",
                pergunta="Informe o nível de integração entre o Sistema da Dívida Ativa e o de Contabilidade:",
                opcoes=opcoes_821,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 8.2.2 • NÍVEL DE INTEGRAÇÃO PRECATÓRIOS X CONTABILIDADE
            # =============================================================================
            opcoes_822 = {
                "Selecione...": 0.0,
                "Totalmente integrado (Provisão e Baixa) (30 pts)": 30.0,
                "Somente as Provisões estão integradas (05 pts)": 5.0,
                "Somente as Baixas estão integradas (05 pts)": 5.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="8.2.2",
                titulo="Nível de Integração: Precatórios x Contabilidade",
                pergunta="Informe o nível de integração entre o Sistema de Precatórios e o de Contabilidade:",
                opcoes=opcoes_822,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

# Lista padrão de setores/sistemas para os quesitos 8.3 e 8.4
            setores_padrao_8 = [
                "Contabilidade", "Gestão de tributos (arrecadação)", "Dívida Ativa", "Precatórios",
                "Gestão patrimonial (bens e equipamentos)", "Gestão de negócios (Business Intelligence)",
                "Planejamento", "Recursos humanos / Departamento pessoal", "Almoxarifado",
                "Controle de frotas", "Controle Interno", "Saúde", "Ensino (educação)",
                "Compras, licitações e contratos", "Certidões e alvarás", "Saneamento", "Cemitérios"
            ]

            # =============================================================================
            # QUESITO 8.3 • BASES DE DADOS SOB GESTÃO DIRETA DA PREFEITURA
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 8.3 - Bases de Dados sob Gestão Direta").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Assinale quais bases de dados encontram-se sob gestão direta da Prefeitura:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Regra de Pontuação:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Gestão Direta: Empresa terceira não pode alterar dados sem conhecimento prévio da Prefeitura.\n"
                        "• Para cada opção NÃO assinalada, perde 3 pontos.\n"
                        "• Pontuação Máxima de Perda (Pmáx) = -51 pontos."
                    ).classes("text-xs text-blue-900 font-medium mt-1")

                def salvar_no_banco_83(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d83 = res_data.get("8.3") or {}
                if not isinstance(d83, dict):
                    d83 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_83 = str(d83.get("valor") or "")
                itens_salvos_83 = [i.strip() for i in valor_salvo_83.split(",") if i.strip()]
                evidencia_83_salva = str(d83.get("link") or "")

                state_83 = {item: item in itens_salvos_83 for item in setores_padrao_8}
                state_83["link"] = evidencia_83_salva

                def cb_processa_e_salva_83():
                    try:
                        marcados = [item for item in setores_padrao_8 if state_83[item]]
                        nao_marcados = [item for item in setores_padrao_8 if not state_83[item]]
                        
                        # Abate -3 pontos para cada item não assinalado
                        pts_penalidade = -3.0 * len(nao_marcados)
                        if pts_penalidade < -51.0:
                            pts_penalidade = -51.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_83["link"] or "").strip()

                        salvar_no_banco_83("8.3", valor_string, pts_penalidade, lnk_val)
                        res_data["8.3"] = {"valor": valor_string, "pontos": pts_penalidade, "link": lnk_val}
                        ui.notify("Quesito 8.3 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 8.3: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in setores_padrao_8:
                        ui.checkbox(item).bind_value(state_83, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Gestão Direta):",
                    value=evidencia_83_salva,
                    placeholder="Link do termo de gestão de dados, contrato ou política de BD..."
                ).classes("w-full mb-4").bind_value(state_83, "link")

                pts_atuais_83 = float(d83.get("pontos") or 0.0)
                cor_txt_83 = "text-red-600" if pts_atuais_83 < 0 else "text-gray-700"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Bases sob Gestão Direta ({len([i for i in setores_padrao_8 if state_83[i]])}): {valor_salvo_83 if valor_salvo_83 else 'Nenhuma'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 8.3: {pts_atuais_83:.1f} pontos").classes(f"text-sm font-bold {cor_txt_83}")

                    ui.button("Salvar Quesito 8.3", on_click=cb_processa_e_salva_83, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("8.3", res_data, ano_sel)

            # =============================================================================
            # QUESITO 8.4 • SISTEMAS COM CONTROLE DE ACESSO À INFORMAÇÃO
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 8.4 - Controle de Acesso à Informação nos Sistemas").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Assinale quais sistemas possuem controle de acesso à informação:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Regra de Pontuação:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "• Controle de Acesso: Gravação de histórico (logs), níveis de acesso e registro de ocorrências/eventos.\n"
                        "• Para cada opção NÃO assinalada, perde 3 pontos.\n"
                        "• Pontuação Máxima de Perda (Pmáx) = -51 pontos."
                    ).classes("text-xs text-blue-900 font-medium mt-1")

                def salvar_no_banco_84(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d84 = res_data.get("8.4") or {}
                if not isinstance(d84, dict):
                    d84 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_84 = str(d84.get("valor") or "")
                itens_salvos_84 = [i.strip() for i in valor_salvo_84.split(",") if i.strip()]
                evidencia_84_salva = str(d84.get("link") or "")

                state_84 = {item: item in itens_salvos_84 for item in setores_padrao_8}
                state_84["link"] = evidencia_84_salva

                def cb_processa_e_salva_84():
                    try:
                        marcados = [item for item in setores_padrao_8 if state_84[item]]
                        nao_marcados = [item for item in setores_padrao_8 if not state_84[item]]
                        
                        # Abate -3 pontos para cada item não assinalado
                        pts_penalidade = -3.0 * len(nao_marcados)
                        if pts_penalidade < -51.0:
                            pts_penalidade = -51.0

                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_84["link"] or "").strip()

                        salvar_no_banco_84("8.4", valor_string, pts_penalidade, lnk_val)
                        res_data["8.4"] = {"valor": valor_string, "pontos": pts_penalidade, "link": lnk_val}
                        ui.notify("Quesito 8.4 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 8.4: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in setores_padrao_8:
                        ui.checkbox(item).bind_value(state_84, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência dos Controles de Acesso):",
                    value=evidencia_84_salva,
                    placeholder="Link de manuais, relatórios de auditoria de logs ou telas de permissão..."
                ).classes("w-full mb-4").bind_value(state_84, "link")

                pts_atuais_84 = float(d84.get("pontos") or 0.0)
                cor_txt_84 = "text-red-600" if pts_atuais_84 < 0 else "text-gray-700"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Sistemas com Controle de Acesso ({len([i for i in setores_padrao_8 if state_84[i]])}): {valor_salvo_84 if valor_salvo_84 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 8.4: {pts_atuais_84:.1f} pontos").classes(f"text-sm font-bold {cor_txt_84}")

                    ui.button("Salvar Quesito 8.4", on_click=cb_processa_e_salva_84, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("8.4", res_data, ano_sel)

# =============================================================================
            # QUESITO 9.0 • SERVIÇOS OFERECIDOS DE FORMA ONLINE
            # =============================================================================
            opcoes_90 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="9.0",
                titulo="Oferta de Serviços Online",
                pergunta="A Prefeitura ofereceu serviços de forma online? (Ex: alvarás, certidões, licenças, consulta de protocolos, ouvidoria, débitos municipais, etc.)",
                opcoes=opcoes_90,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 9.1 • TIPOS DE SERVIÇOS ONLINE (7,5 PONTOS POR ITEM)
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 9.1 - Tipos de Serviços Online Prestados").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Quais tipos de serviços são oferecidos de forma online?").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Regra de Pontuação:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label("• Cada opção assinalada soma 7,5 pontos na avaliação final do quesito.").classes("text-xs text-blue-900 font-medium mt-1")

                def salvar_no_banco_91(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d91 = res_data.get("9.1") or {}
                if not isinstance(d91, dict):
                    d91 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_91 = str(d91.get("valor") or "")
                itens_salvos_91 = [i.strip() for i in valor_salvo_91.split(",") if i.strip()]
                evidencia_91_salva = str(d91.get("link") or "")

                opcoes_servicos_91 = [
                    "Alvarás / licenças de funcionamento", "Certidões", "Licenças / autorizações",
                    "Ouvidoria", "Consulta de débitos municipais", "Emissão de guias/boletos dos débitos municipais",
                    "Solicitação de serviços de zeladoria", "Solicitação de obras e serviços de urbanização",
                    "Inscrições em oficinas, cursos, eventos e vagas", "Nota fiscal eletrônica",
                    "Canal de denúncias", "Cadastro de fornecedores", "Agendamento de consultas na rede pública de saúde",
                    "Agendamento de exames em relação a doenças crônicas na rede pública de saúde",
                    "Pesquisa de satisfação em relação aos serviços prestados pela Prefeitura",
                    "Consulta a status de protocolos de todos os atendimentos dos serviços assinalados acima"
                ]

                state_91 = {item: item in itens_salvos_91 for item in opcoes_servicos_91}
                state_91["link"] = evidencia_91_salva

                def cb_processa_e_salva_91():
                    try:
                        marcados = [item for item in opcoes_servicos_91 if state_91[item]]
                        pts_totais = 7.5 * len(marcados)
                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_91["link"] or "").strip()

                        salvar_no_banco_91("9.1", valor_string, pts_totais, lnk_val)
                        res_data["9.1"] = {"valor": valor_string, "pontos": pts_totais, "link": lnk_val}
                        ui.notify("Quesito 9.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 9.1: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in opcoes_servicos_91:
                        ui.checkbox(f"{item} (+7.5 pts)").bind_value(state_91, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência dos Serviços Online):",
                    value=evidencia_91_salva,
                    placeholder="Link da carta de serviços, portal do cidadão..."
                ).classes("w-full mb-4").bind_value(state_91, "link")

                pts_atuais_91 = float(d91.get("pontos") or 0.0)

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"📋 Serviços Selecionados ({len([i for i in opcoes_servicos_91 if state_91[i]])}): {valor_salvo_91 if valor_salvo_91 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Pontuação Total no Quesito 9.1: {pts_atuais_91:.1f} pontos").classes("text-sm font-bold text-green-700")

                    ui.button("Salvar Quesito 9.1", on_click=cb_processa_e_salva_91, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("9.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 9.2 • FORMAS DE ATENDIMENTO À DISTÂNCIA
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 9.2 - Formas de Atendimento à Distância").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Quais as formas de atendimento à distância disponibilizadas ao público pela Prefeitura?").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                def salvar_no_banco_92(qid_val, valor_val, pontos_val, link_val):
                    try:
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        link = EXCLUDED.link,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, (qid_val, ano_sel, str(valor_val), float(pontos_val), str(link_val)))
                                conn.commit()
                    except Exception as err_db:
                        print(f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}")
                        raise err_db

                d92 = res_data.get("9.2") or {}
                if not isinstance(d92, dict):
                    d92 = {"valor": "", "pontos": 0.0, "link": ""}

                valor_salvo_92 = str(d92.get("valor") or "")
                itens_salvos_92 = [i.strip() for i in valor_salvo_92.split(",") if i.strip()]
                evidencia_92_salva = str(d92.get("link") or "")

                opcoes_atendimento_92 = [
                    "Telefone", "Site da Prefeitura", "Aplicativo de mensagens",
                    "Redes sociais", "Aplicativo da Prefeitura", "Correio eletrônico (e-mail)", "Outros"
                ]

                state_92 = {item: item in itens_salvos_92 for item in opcoes_atendimento_92}
                state_92["link"] = evidencia_92_salva

                def cb_processa_e_salva_92():
                    try:
                        marcados = [item for item in opcoes_atendimento_92 if state_92[item]]
                        valor_string = ", ".join(marcados)
                        lnk_val = str(state_92["link"] or "").strip()

                        salvar_no_banco_92("9.2", valor_string, 0.0, lnk_val)
                        res_data["9.2"] = {"valor": valor_string, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 9.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 9.2: {err}", type="negative")

                with ui.grid(columns=2).classes("w-full gap-2 mb-4"):
                    for item in opcoes_atendimento_92:
                        ui.checkbox(item).bind_value(state_92, item)

                ui.textarea(
                    "Página Eletrônica (Link / Evidência dos Canais de Atendimento):",
                    value=evidencia_92_salva,
                    placeholder="Link dos contatos oficiais, lista de ramais..."
                ).classes("w-full mb-4").bind_value(state_92, "link")

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label(f"📋 Canais Assinalados ({len([i for i in opcoes_atendimento_92 if state_92[i]])}): {valor_salvo_92 if valor_salvo_92 else 'Nenhum'}").classes("text-sm font-semibold text-gray-700")
                    ui.button("Salvar Quesito 9.2", on_click=cb_processa_e_salva_92, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("9.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 10.0 • REGULAMENTAÇÃO DA LGPD
            # =============================================================================
            opcoes_100 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="10.0",
                titulo="Regulamentação da LGPD",
                pergunta="A Prefeitura Municipal regulamentou o tratamento de dados pessoais, inclusive nos meios digitais, segundo a LGPD (Lei Federal nº 13.709/2018)?",
                opcoes=opcoes_100,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 10.1 • INSTRUMENTO NORMATIVO LGPD
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 10.1 - Instrumento Normativo da LGPD").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe o Instrumento normativo, Número e Data da publicação:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                d101 = res_data.get("10.1") or {}
                if not isinstance(d101, dict):
                    d101 = {"valor": "", "pontos": 0.0, "link": ""}

                val_101_salvo = str(d101.get("valor") or "")

                state_101 = {"texto": val_101_salvo}

                def cb_processa_e_salva_101():
                    try:
                        txt_val = str(state_101["texto"] or "").strip()
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        pontos = EXCLUDED.pontos,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, ("10.1", ano_sel, txt_val, 0.0, ""))
                                conn.commit()

                        res_data["10.1"] = {"valor": txt_val, "pontos": 0.0, "link": ""}
                        ui.notify("Quesito 10.1 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 10.1: {err}", type="negative")

                ui.input(
                    "Instrumento Normativo, Número e Data:",
                    value=val_101_salvo,
                    placeholder="Ex: Decreto Municipal nº 1.234, de 10 de maio de 2022"
                ).classes("w-full mb-4").bind_value(state_101, "texto")

                with ui.row().classes("w-full justify-end items-center mb-4"):
                    ui.button("Salvar Quesito 10.1", on_click=cb_processa_e_salva_101, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("10.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 10.2 • PÁGINA ELETRÔNICA DA NORMA LGPD
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 10.2 - Link da Norma da LGPD").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe a página eletrônica (link na internet):").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                ui.label("Nota: Se não estiver disponível na internet, inserir no campo de resposta o texto XYZ").classes("text-xs text-gray-500 italic mb-2")

                d102 = res_data.get("10.2") or {}
                if not isinstance(d102, dict):
                    d102 = {"valor": "", "pontos": 0.0, "link": ""}

                val_102_salvo = str(d102.get("valor") or "")

                state_102 = {"link": val_102_salvo}

                def cb_processa_e_salva_102():
                    try:
                        lnk_val = str(state_102["link"] or "").strip()
                        with get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO respostas_igovti (qid, ano, valor, pontos, link, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                    ON CONFLICT (ano, qid) 
                                    DO UPDATE SET 
                                        valor = EXCLUDED.valor,
                                        link = EXCLUDED.link,
                                        pontos = EXCLUDED.pontos,
                                        updated_at = CURRENT_TIMESTAMP;
                                """, ("10.2", ano_sel, lnk_val, 0.0, lnk_val))
                                conn.commit()

                        res_data["10.2"] = {"valor": lnk_val, "pontos": 0.0, "link": lnk_val}
                        ui.notify("Quesito 10.2 salvo com sucesso!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 10.2: {err}", type="negative")

                ui.input(
                    "Link da publicação da LGPD (ou XYZ se indisponível):",
                    value=val_102_salvo,
                    placeholder="https://... ou XYZ"
                ).classes("w-full mb-4").bind_value(state_102, "link")

                with ui.row().classes("w-full justify-end items-center mb-4"):
                    ui.button("Salvar Quesito 10.2", on_click=cb_processa_e_salva_102, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("10.2", res_data, ano_sel)

            # =============================================================================
            # QUESITO 10.3 • CLÁUSULAS DE LGPD EM CONTRATOS
            # =============================================================================
            opcoes_103 = {
                "Selecione...": 0.0,
                "Todos os contratos vigentes": 0.0,
                "A maior parte dos contratos vigentes": 0.0,
                "A menor parte dos contratos vigentes": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="10.3",
                titulo="Cláusulas de LGPD nos Contratos com Prestadores",
                pergunta="Os contratos com os prestadores de serviços contêm cláusulas de observância à LGPD?",
                opcoes=opcoes_103,
                on_save_callback=container_formulario_igov_ti.refresh,
            )

            # =============================================================================
            # QUESITO 10.4 • MAPEAMENTO DE DADOS (DATA MAPPING)
            # =============================================================================
            opcoes_104 = {
                "Selecione...": 0.0,
                "Sim": 0.0,
                "Não": 0.0,
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="10.4",
                titulo="Mapeamento de Dados (Data Mapping)",
                pergunta="A Prefeitura Municipal realizou mapeamento de dados (data mapping)?",
                opcoes=opcoes_104,
                on_save_callback=container_formulario_igov_ti.refresh,
            )


# Ponte universal de execução para importação do main.py
def render_igovti():
    container_formulario_igov_ti()


# =============================================================================
# ENTRYPOINT DA APLICAÇÃO (Execução direta deste arquivo)
# =============================================================================
if __name__ in {"__main__", "__mp_main__"}:
    @ui.page("/")
    def main_page():
        container_formulario_igov_ti()

    ui.run(storage_secret="chave_secreta_igovti_2026", title="iGov-TI - Governança")
