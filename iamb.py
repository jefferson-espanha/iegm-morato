import base64
import json
import os
import psycopg2
from nicegui import app, ui
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
# MÓDULO PRINCIPAL DE REQUISITOS
# =============================================================================
def container_formulario_iamb(ano=None):
    if "ano_referencia_global" not in app.storage.user:
        app.storage.user["ano_referencia_global"] = ano if ano else 2026

    @ui.refreshable
    def render_conteudo():
        ano_atual = int(app.storage.user.get("ano_referencia_global", 2026))
        respostas = load_respostas(ano_atual)

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
                    ano_atual=ano_atual,
                    on_mudar_ano=alterar_ano,
                    on_refresh=render_conteudo.refresh,
                )

            # Coluna 2: Formulário (9/12)
            with ui.element("div").classes(
                "md:col-span-8 lg:col-span-9 bg-white p-6 border rounded-lg shadow-sm"
            ):
                ui.label(f"📋 Módulo i-Amb — Ano {ano_atual}").classes(
                    "text-xl font-bold mb-4 text-slate-800 border-b pb-2"
                )

                # -------------------------------------------------------------
                # QUESITO 1.0
                # -------------------------------------------------------------
                qid_1_0 = "1.0"
                dados_1_0 = respostas.get(
                    qid_1_0, {"valor": "Não", "pontos": 0.0, "link": ""}
                )

                with ui.card().classes(
                    "w-full p-4 mb-4 border rounded bg-slate-50 shadow-sm"
                ):
                    ui.label(f"Quesito {qid_1_0}").classes(
                        "font-bold text-blue-900 text-base"
                    )
                    ui.label(
                        "A prefeitura possui alguma estrutura organizacional para tratar de assuntos ligados ao Meio Ambiente Municipal?"
                    ).classes("text-sm text-gray-700 my-2 font-medium")

                    sel_1_0 = ui.select(
                        options=["Sim", "Não"],
                        value=(
                            dados_1_0["valor"]
                            if dados_1_0["valor"] in ["Sim", "Não"]
                            else "Não"
                        ),
                        label="Resposta:",
                    ).classes("w-full mb-2 bg-white")

                    link_1_0 = ui.input(
                        "Link da Evidência / Comprovação:",
                        value=dados_1_0["link"],
                    ).classes("w-full mb-2 bg-white")

                    def salvar_1_0():
                        save_resposta(
                            ano=ano_atual,
                            qid=qid_1_0,
                            valor=sel_1_0.value,
                            pontos=0.0,
                            link=link_1_0.value,
                        )
                        ui.notify(
                            f"Quesito {qid_1_0} salvo com sucesso!",
                            type="positive",
                        )
                        render_conteudo.refresh()

                    ui.button(
                        f"💾 Salvar Quesito {qid_1_0}", on_click=salvar_1_0
                    ).classes("bg-green-600 text-white font-bold mt-2")

                # -------------------------------------------------------------
                # QUESITO 1.1 E SUBQUESITO 1.1.1
                # -------------------------------------------------------------
                qid_1_1 = "1.1"
                dados_1_1 = respostas.get(
                    qid_1_1, {"valor": "Não", "pontos": 0.0, "link": ""}
                )

                qid_1_1_1 = "1.1.1"
                dados_1_1_1 = respostas.get(
                    qid_1_1_1, {"valor": "{}", "pontos": 0.0, "link": ""}
                )

                # Converte o JSON armazenado dos efetivos/comissionados
                try:
                    val_1_1_1_dict = json.loads(
                        dados_1_1_1.get("valor", "{}") or "{}"
                    )
                except Exception:
                    val_1_1_1_dict = {}

                with ui.card().classes(
                    "w-full p-4 mb-4 border rounded bg-slate-50 shadow-sm"
                ):
                    ui.label(f"Quesito {qid_1_1}").classes(
                        "font-bold text-blue-900 text-base"
                    )
                    ui.label(
                        "A Prefeitura possui recursos humanos para operacionalização dos assuntos ligados ao Meio Ambiente?"
                    ).classes("text-sm text-gray-700 my-2 font-medium")

                    sel_1_1 = ui.select(
                        options=["Sim", "Não"],
                        value=(
                            dados_1_1["valor"]
                            if dados_1_1["valor"] in ["Sim", "Não"]
                            else "Não"
                        ),
                        label="Resposta:",
                    ).classes("w-full mb-2 bg-white")

                    link_1_1 = ui.input(
                        "Link da Evidência / Comprovação:",
                        value=dados_1_1["link"],
                    ).classes("w-full mb-2 bg-white")

                    # Subseção para o Quesito 1.1.1 (Exibido apenas se 1.1 for "Sim")
                    container_1_1_1 = ui.column().classes(
                        "w-full pl-4 border-l-4 border-blue-400 my-2 bg-blue-50/50 p-3 rounded"
                    )
                    container_1_1_1.set_visibility(sel_1_1.value == "Sim")

                    with container_1_1_1:
                        ui.label(f"Quesito {qid_1_1_1} — Informe:").classes(
                            "font-bold text-blue-950 text-sm"
                        )

                        input_efetivos = ui.number(
                            "Nº de efetivos:",
                            value=val_1_1_1_dict.get("efetivos", 0),
                            min=0,
                            precision=0,
                        ).classes("w-full bg-white mb-1")
                        input_comissionados = ui.number(
                            "Nº de comissionados:",
                            value=val_1_1_1_dict.get("comissionados", 0),
                            min=0,
                            precision=0,
                        ).classes("w-full bg-white mb-1")
                        input_terceirizados = ui.number(
                            "Nº de terceirizados/contratados:",
                            value=val_1_1_1_dict.get("terceirizados", 0),
                            min=0,
                            precision=0,
                        ).classes("w-full bg-white mb-1")

                    sel_1_1.on(
                        "update:model-value",
                        lambda e: container_1_1_1.set_visibility(
                            e.args == "Sim"
                        ),
                    )

                    def salvar_1_1_e_sub():
                        save_resposta(
                            ano=ano_atual,
                            qid=qid_1_1,
                            valor=sel_1_1.value,
                            pontos=0.0,
                            link=link_1_1.value,
                        )

                        if sel_1_1.value == "Sim":
                            payload_1_1_1 = json.dumps({
                                "efetivos": int(input_efetivos.value or 0),
                                "comissionados": int(
                                    input_comissionados.value or 0
                                ),
                                "terceirizados": int(
                                    input_terceirizados.value or 0
                                ),
                            })
                            save_resposta(
                                ano=ano_atual,
                                qid=qid_1_1_1,
                                valor=payload_1_1_1,
                                pontos=0.0,
                                link="",
                            )

                        ui.notify(
                            f"Quesitos {qid_1_1} e {qid_1_1_1} salvos com sucesso!",
                            type="positive",
                        )
                        render_conteudo.refresh()

                    ui.button(
                        f"💾 Salvar Quesito {qid_1_1}", on_click=salvar_1_1_e_sub
                    ).classes("bg-green-600 text-white font-bold mt-2")

                # -------------------------------------------------------------
                # QUESITO 1.1.2
                # -------------------------------------------------------------
                qid_1_1_2 = "1.1.2"
                dados_1_1_2 = respostas.get(
                    qid_1_1_2, {"valor": "Não", "pontos": 0.0, "link": ""}
                )
                opcoes_1_1_2 = {"Sim": "Sim – 20", "Não": "Não – 00"}

                with ui.card().classes(
                    "w-full p-4 mb-4 border rounded bg-slate-50 shadow-sm"
                ):
                    ui.label(f"Quesito {qid_1_1_2}").classes(
                        "font-bold text-blue-900 text-base"
                    )
                    ui.label(
                        f"Os servidores responsáveis pelo Meio Ambiente receberam treinamento específico voltado ao Meio Ambiente em {ano_atual - 1}?"
                    ).classes("text-sm text-gray-700 my-2 font-medium")

                    val_1_1_2 = (
                        dados_1_1_2["valor"]
                        if dados_1_1_2["valor"] in opcoes_1_1_2
                        else "Não"
                    )

                    select_1_1_2 = ui.select(
                        options=opcoes_1_1_2,
                        value=val_1_1_2,
                        label="Resposta:",
                    ).classes("w-full mb-2 bg-white")

                    link_1_1_2 = ui.input(
                        "Link da Evidência / Comprovação:",
                        value=dados_1_1_2["link"],
                    ).classes("w-full mb-2 bg-white")

                    def salvar_1_1_2():
                        sel = select_1_1_2.value
                        pts = 20.0 if sel == "Sim" else 0.0

                        save_resposta(
                            ano=ano_atual,
                            qid=qid_1_1_2,
                            valor=sel,
                            pontos=pts,
                            link=link_1_1_2.value,
                        )
                        ui.notify(
                            f"Quesito {qid_1_1_2} salvo para {ano_atual}! ({pts} pts)",
                            type="positive",
                        )
                        render_conteudo.refresh()

                    ui.button(
                        f"💾 Salvar Quesito {qid_1_1_2}", on_click=salvar_1_1_2
                    ).classes("bg-green-600 text-white font-bold mt-2")

    render_conteudo()


# Aliases para o main.py
mostrar_formulario_iamb = container_formulario_iamb
main = container_formulario_iamb
