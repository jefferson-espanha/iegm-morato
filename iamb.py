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
                cur.execute(query, (ano,))
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
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente"
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
                        ano,
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
                cur.execute(query, (ano,))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")


# =============================================================================
# PAINEL LATERAL / CONTROLE
# =============================================================================
def render_painel_controle(ano_atual=None, on_refresh=None):
    if ano_atual is None:
        ano_atual = app.storage.user.get("ano_referencia_global", 2026)

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

        def ao_mudar_ano(e):
            app.storage.user["ano_referencia_global"] = e.value
            ui.notify(f"Ano alterado para {e.value}", type="info")
            if on_refresh:
                on_refresh()

        ui.select(
            options=anos,
            value=ano_atual,
            label="Ano de Referência:",
            on_change=ao_mudar_ano,
        ).classes("w-full mb-4")

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

        def zerar_acao():
            zerar_questionario_db(ano_atual)
            ui.notify(
                f"✅ Questionário de {ano_atual} zerado!", type="positive"
            )
            if on_refresh:
                on_refresh()

        ui.button(
            "🔄 Atualizar UI",
            on_click=on_refresh if on_refresh else lambda: None,
        ).classes("w-full bg-blue-700 text-white mb-2")
        ui.button("🗑️ Zerar Ano", on_click=zerar_acao).classes(
            "w-full bg-red-700 text-white"
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
# PÁGINA PRINCIPAL E FORMULÁRIO REATIVO
# =============================================================================
@ui.page("/")
def main_page():

    @ui.refreshable
    def render_conteudo_pagina():
        ano_atual = app.storage.user.get("ano_referencia_global", 2026)
        respostas = load_respostas(ano_atual)

        with ui.row().classes("w-full gap-4 items-start"):
            # Coluna Esquerda: Painel Lateral
            with ui.column().classes("w-1/4"):
                render_painel_controle(
                    ano_atual, on_refresh=render_conteudo_pagina.refresh
                )

            # Coluna Direita: Formulário de Quesitos
            with ui.column().classes("w-3/4 p-4 border rounded-lg bg-white"):
                ui.label(
                    f"📋 Formulário de Quesitos — Ano {ano_atual}"
                ).classes("text-xl font-bold mb-4")

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
                        "font-bold text-blue-900"
                    )
                    ui.label(
                        "Os servidores responsáveis pelo Meio Ambiente receberam treinamento "
                        "específico voltado ao Meio Ambiente em 2025?"
                    ).classes("text-sm text-gray-700 my-1 font-medium")

                    # Valor atual ou padrão 'Não'
                    val_inicial = (
                        dados_1_1_2["valor"]
                        if dados_1_1_2["valor"] in opcoes_1_1_2
                        else "Não"
                    )

                    select_1_1_2 = ui.select(
                        options=opcoes_1_1_2,
                        value=val_inicial,
                        label="Resposta:",
                    ).classes("w-full mb-2")

                    link_1_1_2 = ui.input(
                        "Link da Evidência / Comprovação:",
                        value=dados_1_1_2["link"],
                    ).classes("w-full mb-2")

                    def salvar_1_1_2():
                        resposta_sel = select_1_1_2.value
                        pts = 20.0 if resposta_sel == "Sim" else 0.0

                        save_resposta(
                            ano=ano_atual,
                            qid=qid_1_1_2,
                            valor=resposta_sel,
                            pontos=pts,
                            link=link_1_1_2.value,
                        )
                        ui.notify(
                            f"Quesito {qid_1_1_2} salvo com sucesso! ({pts} pts)",
                            type="positive",
                        )
                        render_conteudo_pagina.refresh()

                    ui.button(
                        f"💾 Salvar Quesito {qid_1_1_2}", on_click=salvar_1_1_2
                    ).classes("bg-green-600 text-white mt-2")

    render_conteudo_pagina()


ui.run(storage_secret="sua_chave_secreta_aqui")
