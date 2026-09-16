import base64
from datetime import datetime
import json
import os
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
                    CREATE TABLE IF NOT EXISTS respostas_iamb (
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
        print(f"❌ Erro ao inicializar tabela respostas_iamb: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iamb
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
                            row["link"] if row["link"] != "EMPTY_STRING" else ""
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
    query = "DELETE FROM respostas_iamb WHERE ano = %s;"
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
        ui.label("🛠️ Painel de Controle (iAmb)").classes(
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
def container_formulario_iamb(ano=None):
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
                ui.label(f"📋 Módulo i-Amb — Ano {ano_sel}").classes(
                    "text-xl font-bold mb-4 text-slate-800 border-b pb-2"
                )

                # =============================================================================
                # QUESITO 1.0 • ESTRUTURA ORGANIZACIONAL DE MEIO AMBIENTE
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
                    titulo="Estrutura Organizacional do Meio Ambiente",
                    pergunta="A prefeitura possui alguma estrutura organizacional para tratar de assuntos ligados ao Meio Ambiente Municipal?",
                    opcoes=opcoes_10,
                    placeholder_link="Insira o link da lei da estrutura administrativa ou organograma...",
                    on_save_callback=render_conteudo.refresh,
                )

                # =============================================================================
                # QUESITO 1.1 • RECURSOS HUMANOS EM MEIO AMBIENTE
                # =============================================================================
                with ui.card().classes(
                    "w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"
                ):
                    ui.label(
                        "📌 Quesito 1.1 - Recursos Humanos em Meio Ambiente"
                    ).classes("text-lg font-bold text-blue-900 mb-1")

                    ui.label(
                        "A Prefeitura possui recursos humanos para operacionalização dos assuntos ligados ao Meio Ambiente? Informe a quantidade:"
                    ).classes("text-base font-semibold text-gray-800 mt-2 mb-1")

                    with ui.card().classes(
                        "w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"
                    ):
                        ui.label("Instrução de preenchimento:").classes(
                            "text-xs font-bold text-blue-900 uppercase tracking-wide"
                        )
                        ui.label(
                            "Informe o quantitativo de servidores efetivos, comissionados e terceirizados/contratados alocados no setor."
                        ).classes("text-sm text-blue-800 font-medium")

                    def salvar_no_banco_11(
                        qid_val, valor_val, pontos_val, link_val
                    ):
                        try:
                            with get_db_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        """
                                        INSERT INTO respostas_iamb (qid, ano, valor, pontos, link, updated_at)
                                        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                        ON CONFLICT (ano, qid) 
                                        DO UPDATE SET 
                                            valor = EXCLUDED.valor,
                                            pontos = EXCLUDED.pontos,
                                            link = EXCLUDED.link,
                                            updated_at = CURRENT_TIMESTAMP;
                                    """,
                                        (
                                            qid_val,
                                            ano_sel,
                                            str(valor_val),
                                            float(pontos_val),
                                            str(link_val),
                                        ),
                                    )
                                    conn.commit()
                        except Exception as err_db:
                            print(
                                f"❌ Erro ao salvar no banco (Quesito {qid_val}): {err_db}"
                            )
                            raise err_db

                    def parse_int_seguro(val):
                        if val is None:
                            return 0
                        if isinstance(val, (int, float)):
                            return int(val)
                        val_str = str(val).strip()
                        if not val_str or not val_str.isdigit():
                            return 0
                        return int(val_str)

                    d11 = res_data.get("1.1") or {}
                    if not isinstance(d11, dict):
                        d11 = {"valor": "0", "pontos": 0.0, "link": ""}

                    v_efet_i, v_comi_i, v_terc_i = 0, 0, 0
                    evidencia_11_salva = ""
                    raw_link = str(d11.get("link") or "")

                    if raw_link:
                        if "|LINK:" in raw_link:
                            contadores_part, evidencia_11_salva = (
                                raw_link.split("|LINK:", 1)
                            )
                        else:
                            contadores_part, evidencia_11_salva = (
                                raw_link,
                                "",
                            )

                        import re

                        match_e = re.search(r"E:(\d+)", contadores_part)
                        match_co = re.search(r"Co:(\d+)", contadores_part)
                        match_t = re.search(r"T:(\d+)", contadores_part)

                        v_efet_i = int(match_e.group(1)) if match_e else 0
                        v_comi_i = int(match_co.group(1)) if match_co else 0
                        v_terc_i = int(match_t.group(1)) if match_t else 0

                    state_11 = {
                        "efet": v_efet_i,
                        "comi": v_comi_i,
                        "terc": v_terc_i,
                        "link": evidencia_11_salva,
                    }

                    def cb_processa_e_salva_11():
                        try:
                            ef_val = parse_int_seguro(state_11["efet"])
                            co_val = parse_int_seguro(state_11["comi"])
                            te_val = parse_int_seguro(state_11["terc"])
                            lnk_val = str(state_11["link"] or "").strip()

                            total_p = ef_val + co_val + te_val
                            pts_calculados = 0.0
                            composite_string = f"E:{ef_val},Co:{co_val},T:{te_val}|LINK:{lnk_val}"

                            salvar_no_banco_11(
                                "1.1",
                                str(total_p),
                                pts_calculados,
                                composite_string,
                            )

                            res_data["1.1"] = {
                                "valor": str(total_p),
                                "pontos": pts_calculados,
                                "link": composite_string,
                            }

                            ui.notify(
                                "Quesito 1.1 salvo com sucesso!",
                                type="positive",
                            )
                            render_conteudo.refresh()
                        except Exception as err:
                            ui.notify(
                                f"Erro ao salvar Quesito 1.1: {err}",
                                type="negative",
                            )

                    with ui.grid(columns=2).classes(
                        "w-full gap-4 mb-4 md:grid-cols-3"
                    ):
                        ui.number(
                            "Nº de efetivos:",
                            value=v_efet_i,
                            min=0,
                            step=1,
                        ).classes("w-full").bind_value(state_11, "efet")

                        ui.number(
                            "Nº de comissionados:",
                            value=v_comi_i,
                            min=0,
                            step=1,
                        ).classes("w-full").bind_value(state_11, "comi")

                        ui.number(
                            "Nº de terceirizados/contratados:",
                            value=v_terc_i,
                            min=0,
                            step=1,
                        ).classes("w-full").bind_value(state_11, "terc")

                    ui.textarea(
                        "Página Eletrônica (Link / Evidência da Composição):",
                        value=evidencia_11_salva,
                        placeholder="Insira o link da portaria de lotação, folha de pagamento simplificada ou declaração de RH...",
                    ).classes("w-full mb-4").bind_value(state_11, "link")

                    total_pessoal = parse_int_seguro(d11.get("valor"))

                    with ui.row().classes(
                        "w-full justify-between items-center mb-4"
                    ):
                        with ui.column().classes("gap-0"):
                            ui.label(
                                f"👥 Total de Pessoal Computado: {total_pessoal} servidor(es)"
                            ).classes("text-sm font-semibold text-gray-700")

                        ui.button(
                            "Salvar Quesito 1.1",
                            on_click=cb_processa_e_salva_11,
                            icon="save",
                        ).classes(
                            "bg-blue-800 text-white font-medium px-4 py-2 rounded-md"
                        )

                    ui.separator().classes("my-2")

                    bloco_comentarios("1.1", res_data, render_conteudo.refresh)

                # =============================================================================
                # QUESITO 1.1.2 • TREINAMENTO DOS SERVIDORES EM MEIO AMBIENTE
                # =============================================================================
                ano_treinamento = ano_sel - 1
                opcoes_112 = {
                    "Selecione...": 0.0,
                    "Sim – 20 pts": 20.0,
                    "Não – 00 pts": 0.0,
                }
                render_quesito(
                    ano=ano_sel,
                    res_data=res_data,
                    qid="1.1.2",
                    titulo="Treinamento Específico dos Servidores",
                    pergunta=f"Os servidores responsáveis pelo Meio Ambiente receberam treinamento específico voltado ao Meio Ambiente em {ano_treinamento}?",
                    opcoes=opcoes_112,
                    placeholder_link="Insira o link de certificados emitidos, ordem de serviço ou relatório de treinamento...",
                    on_save_callback=render_conteudo.refresh,
                )

    render_conteudo()


# Aliases para o main.py
mostrar_formulario_iamb = container_formulario_iamb
main = container_formulario_iamb
