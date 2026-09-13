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

                # Conversor ultraforte contra valores vazios ('', None, texto)
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

                # Extração segura dos contadores do banco
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

                # Estado reativo local do formulário
                state_11 = {
                    "conc": v_conc_i,
                    "comi": v_comi_i,
                    "esta": v_esta_i,
                    "outr": v_outr_i,
                    "link": evidencia_11_salva
                }

                # Callback para Processar e Salvar os dados
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

                        # Grava no banco de dados
                        save_resp("1.1", str(total_p), pts_calculados, composite_string)
                        
                        # Atualiza a memória local da página
                        res_data["1.1"] = {
                            "valor": str(total_p), 
                            "pontos": pts_calculados, 
                            "link": composite_string
                        }
                        
                        ui.notify("Quesito 1.1 salvo com sucesso no banco!", type="positive")
                        container_formulario_igov_ti.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.1: {err}", type="negative")
                        print(f"❌ Erro interno no callback do 1.1: {err}")

                # Grid com os 4 Inputs de Pessoal
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

                # Área de Link e Evidências
                ui.textarea(
                    "Página Eletrônica (Link / Evidência da Composição):",
                    value=evidencia_11_salva,
                    placeholder="Insira o link do decreto de lotação de pessoal, relatório do setor de RH ou folha simplificada da TI..."
                ).classes("w-full mb-4").bind_value(state_11, "link")

                # Rodapé com Indicador de Pontuação e Botão de Salvar
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
