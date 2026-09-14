import ast
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# CONFIGURAÇÃO DE BANCO DE DADOS
# =============================================================================
REGEX_PURE_URL = r"https?://[^\s]+"

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
                    CREATE TABLE IF NOT EXISTS respostas_iamb_oficial (
                        qid VARCHAR(50) NOT NULL,
                        ano INTEGER NOT NULL,
                        valor TEXT,
                        pontos REAL DEFAULT 0.0,
                        link TEXT,
                        comentarios JSONB DEFAULT '[]'::jsonb,
                        status VARCHAR(20) DEFAULT 'Pendente',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (ano, qid)
                    );
                """)
                conn.commit()
    except Exception as e:
        print(f"❌ Erro DB Init: {e}")


init_db()


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


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iamb_oficial
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()
                for row in rows:
                    link_val = row["link"] if row["link"] != "EMPTY_STRING" else ""
                    try:
                        pts_val = float(row["pontos"]) if row["pontos"] is not None else 0.0
                    except (ValueError, TypeError):
                        pts_val = 0.0

                    respostas[str(row["qid"]).strip()] = {
                        "valor": row["valor"] if row["valor"] is not None else "",
                        "pontos": pts_val,
                        "link": link_val or "",
                        "comentarios": _obter_lista_comentarios({"comentarios": row["comentarios"]}),
                        "status": row["status"] if row["status"] else "Pendente",
                    }
    except Exception as e:
        print(f"❌ Erro load_respostas: {e}")
    return respostas


def save_resposta(ano, qid, valor, pontos, link, comentarios=None, status="Pendente"):
    qid_str = str(qid).strip()
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid_str, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
    link_final = str(link).strip() if link else ""

    try:
        pontos_float = float(pontos)
    except (ValueError, TypeError):
        pontos_float = 0.0

    query = """
        INSERT INTO respostas_iamb_oficial (ano, qid, valor, pontos, link, comentarios, status)
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
                        qid_str,
                        str(valor),
                        pontos_float,
                        link_final,
                        Json(comentarios_validos),
                        str(status),
                    ),
                )
                conn.commit()
    except Exception as e:
        print(f"❌ Erro save_resposta ({qid_str}): {e}")


def zerar_questionario_db(ano):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM respostas_iamb_oficial WHERE ano = %s;", (int(ano),))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro zerar_db: {e}")


def obter_pontuacao_total_bd(ano):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(pontos), 0.0) as total FROM respostas_iamb_oficial WHERE ano = %s;", (int(ano),))
                row = cur.fetchone()
                return float(row["total"]) if row else 0.0
    except Exception as e:
        print(f"❌ Erro total_bd: {e}")
        return 0.0


# =============================================================================
# PAINEL LATERAL
# =============================================================================
def render_painel_controle(on_refresh_callback=None):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_atual = app.storage.user.get("ano_referencia_global", 2026)

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label("🌱 Painel de Controle (iAmb)").classes("text-lg font-bold mb-2 text-green-900")

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

        total_pts = obter_pontuacao_total_bd(ano_atual)
        res_data = load_respostas(ano_atual)

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
            ui.label("PONTUAÇÃO TOTAL").classes("text-xs text-gray-500 font-bold uppercase")
            ui.label(f"{total_pts:.1f} pts").classes("text-2xl font-black text-gray-800")
            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        ui.separator().classes("my-2")
        ui.button("🔄 ATUALIZAR QUESTIONÁRIO", on_click=on_refresh_callback).classes("w-full bg-blue-600 text-white mb-2")


# =============================================================================
# BLOCO DE COMENTÁRIOS ESTILO IGOV
# =============================================================================
def bloco_comentarios(qid, res_data, on_save_callback=None):
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    usuario_atual = app.storage.user.get("username", "jefferson.espanha")

    dados_q = res_data.get(str(qid).strip(), {})
    historico = _obter_lista_comentarios(dados_q)
    status_global = dados_q.get("status", "Pendente")

    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"

    with ui.card().classes("w-full border rounded-lg p-4 mt-3 bg-gray-50"):
        ui.label(f"💬 Diálogo Interno {qid} | Status: {badge_status}").classes("font-bold text-sm text-gray-700 mb-2")

        def alterar_status(e):
            novo_st = e.value
            save_resposta(
                ano=ano_sel,
                qid=qid,
                valor=dados_q.get("valor", ""),
                pontos=dados_q.get("pontos", 0.0),
                link=dados_q.get("link", ""),
                comentarios=historico,
                status=novo_st,
            )
            if on_save_callback:
                on_save_callback()

        ui.radio(["Resolvido", "Pendente"], value=status_global, on_change=alterar_status).props("inline").classes("mb-3")

        # Lista de comentários no estilo do iGov
        if historico:
            with ui.column().classes("w-full gap-2 mb-3"):
                for idx, com in enumerate(historico):
                    if isinstance(com, str):
                        com = {"autor": usuario_atual, "data": "", "texto": com}

                    autor = com.get("autor", usuario_atual)
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
                        if on_save_callback:
                            on_save_callback()

                    with ui.card().classes("w-full p-3 bg-white border rounded shadow-none"):
                        with ui.row().classes("w-full justify-between items-center no-wrap"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("person", size="xs").classes("text-blue-600")
                                ui.label(autor).classes("font-bold text-xs text-blue-800")
                                ui.label(data_com).classes("text-xs text-gray-400")
                            ui.button(icon="delete", on_click=deletar_comentario).props("flat dense").classes("text-gray-400 hover:text-red-600")
                        ui.label(texto_com).classes("text-sm text-gray-800 mt-1")

        input_novo_comentario = ui.textarea(placeholder="Novo comentário...").classes("w-full bg-white").props("outlined rows=2")

        def postar_comentario():
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append({
                    "autor": usuario_atual,
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": txt,
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

        ui.button("POSTAR COMENTÁRIO", on_click=postar_comentario).classes("bg-blue-500 text-white font-bold mt-2")


# =============================================================================
# QUESITO COM ATUALIZAÇÃO CORRETA DA OPÇÃO DE SELEÇÃO
# =============================================================================
def render_quesito(ano, res_data, qid, titulo, pergunta, opcoes=None, on_save_callback=None):
    qid_key = str(qid).strip()
    d_data = res_data.get(qid_key, {})

    with ui.card().classes("w-full mb-4 p-4 border rounded-lg shadow-sm"):
        ui.label(f"📌 Quesito {qid_key} - {titulo}").classes("text-h6 text-green-900")
        ui.label(f"{qid_key} • {titulo}").classes("text-lg font-bold text-green-800 mt-1")
        ui.label(pergunta).classes("text-body1 font-bold my-2")

        with ui.row().classes("w-full gap-4 items-start"):
            with ui.column().classes("flex-1"):
                lista_opcoes = list(opcoes.keys()) if isinstance(opcoes, dict) else opcoes
                valor_salvo = d_data.get("valor", "Selecione...")
                if valor_salvo not in lista_opcoes:
                    valor_salvo = "Selecione..."

                radio_opcao = ui.radio(options=lista_opcoes, value=valor_salvo).classes("gap-2")

            with ui.column().classes("flex-1"):
                input_link = ui.textarea(
                    label="Link de Evidência / Documento:",
                    value=d_data.get("link", ""),
                ).classes("w-full").props("outlined rows=3")

                container_links = ui.row().classes("mt-1")

                def atualizar_links_visuais():
                    container_links.clear()
                    links = re.findall(REGEX_PURE_URL, input_link.value or "")
                    if links:
                        with container_links:
                            ui.label("Links Ativos: ").classes("font-bold text-caption")
                            for url in links:
                                ui.link(url, target=url, new_tab=True).classes("text-caption text-blue-600 mr-2")

                input_link.on("update:model-value", atualizar_links_visuais)
                atualizar_links_visuais()

        pts_atuais = d_data.get("pontos", 0.0)
        cor_pts = "text-green-600" if pts_atuais > 0 else "text-red-500"
        ui.label(f"📊 Impacto de Pontuação no Quesito {qid_key}: {pts_atuais:.1f} pontos").classes(f"mt-3 font-bold {cor_pts}")

        def salvar():
            opcao_selecionada = radio_opcao.value
            pts = opcoes.get(opcao_selecionada, 0.0) if isinstance(opcoes, dict) else 0.0
            link = input_link.value or ""

            save_resposta(
                ano=ano,
                qid=qid_key,
                valor=opcao_selecionada,
                pontos=pts,
                link=link,
                comentarios=d_data.get("comentarios", []),
                status=d_data.get("status", "Pendente"),
            )
            ui.notify(f"Quesito {qid_key} salvo!", type="positive")
            if on_save_callback:
                on_save_callback()

        ui.button(f"💾 SALVAR QUESITO {qid_key}", on_click=salvar).classes("bg-blue-500 text-white font-bold mt-3")

        bloco_comentarios(qid_key, res_data, on_save_callback=on_save_callback)


# =============================================================================
# REFRESHABLE PRINCIPAL
# =============================================================================
@ui.refreshable
def container_formulario_iamb():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes("w-full gap-6 items-start"):
        with ui.column().classes("col-span-1 w-full"):
            render_painel_controle(on_refresh_callback=container_formulario_iamb.refresh)

        with ui.column().classes("col-span-3 w-full"):
            ui.label(f"1.0 Estrutura de Gestão Ambiental").classes("text-h5 font-bold my-2 text-green-900")

            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Estrutura Orgânica de Meio Ambiente",
                pergunta="O Município possui órgão ou unidade administrativa específica destinada à gestão do Meio Ambiente?",
                opcoes=opcoes_10,
                on_save_callback=container_formulario_iamb.refresh,
            )
