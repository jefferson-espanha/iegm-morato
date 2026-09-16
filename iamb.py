import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from nicegui import app, ui

# =============================================================================
# BANCO DE DADOS
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
                        "pontos": float(row["pontos"]) if row["pontos"] is not None else 0.0,
                        "link": row["link"] if row["link"] != "EMPTY_STRING" else "",
                        "comentarios": row["comentarios"] if isinstance(row["comentarios"], list) else [],
                        "status": row["status"] or "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB: {e}")
    return respostas

def save_resposta(ano, qid, valor, pontos, link, comentarios=None, status="Pendente"):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    link_final = link.strip() if link else ""

    # CORRIGIDO: Nome da tabela alterado de respostas_amb para respostas_iamb
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
                cur.execute(query, (ano, str(qid), str(valor), float(pontos), link_final, Json(comentarios), str(status)))
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
# INTERFACE DE USUÁRIO (NICEGUI)
# =============================================================================

def render_painel_controle(ano_atual, on_refresh):
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

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label("🛠️ Painel de Controle (iAmb)").classes("text-lg font-bold mb-2 text-blue-900")

        def ao_mudar_ano(e):
            app.storage.user["ano_referencia_global"] = e.value
            ui.notify(f"Ano alterado para {e.value}", type="info")
            on_refresh()

        ui.select(
            options=anos,
            value=ano_atual,
            label="Ano de Referência:",
            on_change=ao_mudar_ano,
        ).classes("w-full mb-4")

        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("Pontuação Total").classes("text-xs text-gray-500 font-bold uppercase")
            ui.label(f"{total_pts:.1f} pts").classes("text-2xl font-black text-gray-800")
            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        def zerar_acao():
            zerar_questionario_db(ano_atual)
            ui.notify(f"✅ Questionário de {ano_atual} zerado!", type="positive")
            on_refresh()

        ui.button("🔄 Atualizar UI", on_click=on_refresh).classes("w-full bg-blue-700 text-white mb-2")
        ui.button("🗑️ Zerar Ano", on_click=zerar_acao).classes("w-full bg-red-700 text-white")

# CONTAINER PRINCIPAL DA PÁGINA
@ui.page("/")
def main_page():
    
    @ui.refreshable
    def render_conteudo_pagina():
        ano_atual = app.storage.user.get("ano_referencia_global", 2026)
        respostas = load_respostas(ano_atual)

        with ui.row().classes("w-full gap-4 items-start"):
            # Coluna Esquerda: Painel Lateral
            with ui.column().classes("w-1/4"):
                render_painel_controle(ano_atual, on_refresh=render_conteudo_pagina.refresh)

            # Coluna Direita: Formulário de Quesitos
            with ui.column().classes("w-3/4 p-4 border rounded-lg bg-white"):
                ui.label(f"📋 Formulario de Quesitos — Ano {ano_atual}").classes("text-xl font-bold mb-4")

                # Exemplo de Quesito
                qid = "1.1"
                dados_q = respostas.get(qid, {"valor": "", "pontos": 0.0, "link": ""})

                ui.label(f"Quesito {qid} - Preservação Ambiental").classes("font-bold")
                in_valor = ui.input("Valor/Resposta", value=dados_q["valor"]).classes("w-full")
                in_pontos = ui.number("Pontos", value=dados_q["pontos"]).classes("w-full")
                in_link = ui.input("Link da Evidência", value=dados_q["link"]).classes("w-full")

                def salvar_e_atualizar():
                    save_resposta(
                        ano=ano_atual,
                        qid=qid,
                        valor=in_valor.value,
                        pontos=in_pontos.value or 0,
                        link=in_link.value,
                    )
                    ui.notify("Quesito salvo com sucesso!", type="positive")
                    render_conteudo_pagina.refresh()  # Recarrega a tela instantaneamente

                ui.button("💾 Salvar Quesito", on_click=salvar_e_atualizar).classes("bg-green-600 text-white mt-2")

    render_conteudo_pagina()

ui.run(storage_secret="sua_chave_secreta_aqui")
