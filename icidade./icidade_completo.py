import sys
import os
import re
import html
import json
import warnings
import logging
from datetime import datetime, date
from io import BytesIO

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from nicegui import ui, app

# Silencia alertas e logs não críticos no console
warnings.filterwarnings("ignore")
logging.getLogger("uvicorn").setLevel(logging.ERROR)

# Bibliotecas para o PDF (Requer: pip install reportlab)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.barcharts import VerticalBarChart

# Bibliotecas para os Gráficos (Requer: pip install plotly)
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# =============================================================================
# CONSTANTES GLOBAIS
# =============================================================================

REGEX_PURE_URL = r'((https?://[^\s<>"]+))'

CATEGORIAS_MAP = {
    "planejamento":   {"label": "Planejamento",    "qids": ["1.0", "1.3", "1.4"]},
    "gestao_fiscal":  {"label": "Gestão Fiscal",   "qids": ["2.0", "2.1", "2.2", "10.0", "C1.1"]},
    "educacao":       {"label": "Educação",         "qids": ["3.0", "3.1", "11.1", "11.1.1", "11.2"]},
    "saude":          {"label": "Saúde",            "qids": ["4.2", "12.1", "12.1.3"]},
    "meio_ambiente":  {"label": "Meio Ambiente",    "qids": ["5.0", "5.1.1", "5.2", "14.0"]},
    "cidades_proteg": {"label": "Cidades Proteg.",  "qids": ["6.0", "15.0"]},
    "governanca_ti":  {"label": "Governança TI",    "qids": ["7.0", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "16.0"]},
    "transparencia":  {"label": "Transparência",    "qids": ["8.0", "8.1.1.1", "8.2", "9.0"]},
}

PONTUACOES_MAX = {
    "1.0": 40, "1.3": 5, "1.4": 50, "2.0": 20, "2.1": 30, "2.2": 10, 
    "3.0": 10, "3.1.1": 10, "5.0": 200, "7.0": 50, "7.1": 5, "7.2": 80, 
    "7.3": 50, "7.4": 50, "7.5": 10, "7.6": 10, "8.0": 50, "8.1.1.1": 20, 
    "8.2": 50, "9.0": 100, "15.0": 50, "16.0": 50, "C1.1": 50
}

FAIXA_CORES = {"C": "#ef4444", "C+": "#f97316", "B": "#eab308", "B+": "#22c55e", "A": "#16a34a"}

# =============================================================================
# CONEXÃO OTIMIZADA E SEGURA COM O NEON (POSTGRESQL)
# =============================================================================

def get_db_url():
    """Recupera, higieniza e valida a URL de conexão do Neon."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        ui.notify("❌ A variável DATABASE_URL do Neon não foi configurada nas variáveis de ambiente!", type="negative")
        return None
    
    if "channel_binding=" in db_url:
        db_url = db_url.split("&channel_binding=")[0].split("?channel_binding=")[0]
    
    if "sslmode=require" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
        
    return db_url

class get_connection:
    """Context manager seguro para conexões diretas com o Neon."""
    def __enter__(self):
        try:
            url = get_db_url()
            if not url:
                raise ValueError("DATABASE_URL ausente.")
            self.conn = psycopg2.connect(url)
            return self.conn
        except Exception as e:
            logging.error(f"Erro ao conectar com o Neon PostgreSQL: {e}")
            raise e

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "conn") and self.conn:
            try:
                if getattr(self.conn, "closed", 0) == 0:
                    if exc_type:
                        self.conn.rollback()
                    else:
                        self.conn.commit()
            except Exception as e:
                logging.error(f"Erro no encerramento da transação: {e}")
            finally:
                try:
                    self.conn.close()
                except Exception:
                    pass

# =============================================================================
# 1. FUNÇÕES DE BANCO DE DADOS (NEON POSTGRESQL)
# =============================================================================

def load_respostas(ano: int) -> dict:
    """Busca do banco de dados todas as respostas relativas a um ano específico."""
    respostas = {}
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id, valor, pontos, link, comentarios FROM respostas WHERE ano = %s",
                    (ano,)
                )
                rows = cursor.fetchall()
                for row in rows:
                    comentarios_bruto = row.get("comentarios")
                    comentarios = []
                    
                    if isinstance(comentarios_bruto, list):
                        comentarios = comentarios_bruto
                    elif isinstance(comentarios_bruto, str) and comentarios_bruto and comentarios_bruto != "EMPTY_STRING":
                        try:
                            comentarios = json.loads(comentarios_bruto)
                        except Exception:
                            comentarios = []

                    respostas[str(row["id"])] = {
                        "valor": row["valor"] or "",
                        "pontos": float(row["pontos"] or 0.0),
                        "link": row["link"] or "",
                        "comentarios": comentarios
                    }
    except Exception as e:
        logging.error(f"Erro ao carregar respostas do ano {ano}: {e}")
    return respostas


def save_resp(qid, valor, pontos, link, comentarios=None, ano_sel=None):
    """Salva/Atualiza a resposta mantendo os comentários intactos caso não sejam informados."""
    if not ano_sel:
        ano_sel = app.storage.user.get("ano_referencia_global", datetime.now().year)

    if comentarios is None:
        dados_atuais = load_respostas(ano_sel)
        comentarios = dados_atuais.get(str(qid), {}).get("comentarios", [])

    if not isinstance(comentarios, list):
        comentarios = []

    comentarios_json = json.dumps(comentarios, ensure_ascii=False)
    timestamp_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO respostas (id, ano, valor, pontos, link, comentarios, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (id, ano) DO UPDATE SET
                        valor = EXCLUDED.valor,
                        pontos = EXCLUDED.pontos,
                        link = EXCLUDED.link,
                        comentarios = EXCLUDED.comentarios,
                        atualizado_em = EXCLUDED.atualizado_em;
                """, (
                    str(qid), 
                    int(ano_sel), 
                    str(valor), 
                    float(pontos), 
                    str(link), 
                    comentarios_json,
                    timestamp_atual
                ))
            conn.commit()
            ui.notify(f"Questão {qid} salva com sucesso!", type="positive", icon="check")
    except Exception as e:
        ui.notify(f"Erro ao salvar {qid} no banco de dados: {e}", type="negative")

# =============================================================================
# 2. BLOCO DE COMENTÁRIOS E RENDERIZAÇÃO DA QUESTÃO
# =============================================================================

def bloco_comentarios(questao_id, res_data, refresh_callback=None, sufixo=None):
    """Gera o diálogo interno avançado com histórico e status no NiceGUI."""
    ano_sel = app.storage.user.get("ano_referencia_global", date.today().year)
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")
    
    id_chave = f"{questao_id}_{sufixo}" if sufixo else questao_id
    dados_questao = res_data.get(questao_id, {})
    historico = list(dados_questao.get("comentarios", []))
    
    status_global = "Resolvido"
    for com in historico:
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            
    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    
    with ui.expansion(f"💬 Diálogo Interno {id_chave} | Status: {badge_status}", icon="chat").classes('w-full bg-slate-50 border rounded-lg my-2'):
        opcoes_status = ["Resolvido", "Pendente"]
        novo_status = ui.radio(opcoes_status, value=status_global).props('inline').classes('mb-2')
        
        # Histórico de mensagens
        container_historico = ui.column().classes('w-full gap-2 mb-4')
        with container_historico:
            if historico:
                for idx, com in enumerate(historico):
                    if not isinstance(com, dict):
                        continue
                    
                    autor = html.escape(str(com.get('autor', 'Anônimo')))
                    data_com = html.escape(str(com.get('data', '')))
                    texto_com = html.escape(str(com.get('texto', '')))
                    
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        if "Sistema /" in autor:
                            ui.html(f"""
                                <div style="background-color: #f1f3f5; padding: 6px 12px; border-radius: 6px; border-left: 3px solid #ced4da; width: 100%;">
                                    <span style="font-size: 11px; color: #6c757d; font-style: italic;">{autor} - {data_com}</span>
                                    <p style="margin: 2px 0 0 0; font-size: 12px; color: #495057;">{texto_com}</p>
                                </div>
                            """).classes('w-full')
                        else:
                            ui.html(f"""
                                <div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #1e88e5; width: 100%;">
                                    <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">{autor}</span> 
                                    <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                                </div>
                            """).classes('w-full')
                        
                        def deletar_comentario(i=idx):
                            historico.pop(i)
                            save_resp(questao_id, dados_questao.get("valor", ""), dados_questao.get("pontos", 0), dados_questao.get("link", ""), historico, ano_sel)
                            if refresh_callback:
                                refresh_callback()

                        ui.button(icon='delete', on_click=deletar_comentario).props('flat dense color=negative')

        # Campo de entrada e envio
        novo_texto = ui.textarea(placeholder="Novo comentário...").classes('w-full')
        
        def postar():
            texto_limpo = novo_texto.value.strip()
            houve_mudanca_status = (novo_status.value != status_global)
            
            if texto_limpo or houve_mudanca_status:
                if houve_mudanca_status:
                    historico.append({
                        "autor": "Sistema / " + usuario_atual,
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "texto": f"ℹ️ Alterou o status do quesito para: **{novo_status.value.upper()}**.",
                        "status_definido": novo_status.value
                    })
                
                if texto_limpo:
                    historico.append({
                        "autor": usuario_atual,
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "texto": texto_limpo,
                        "status_definido": novo_status.value
                    })
                
                save_resp(questao_id, dados_questao.get("valor", ""), dados_questao.get("pontos", 0), dados_questao.get("link", ""), historico, ano_sel)
                novo_texto.value = ""
                if refresh_callback:
                    refresh_callback()
            else:
                ui.notify("Digite um comentário ou altere o status antes de postar.", type="warning")

        ui.button("Postar Comentário", on_click=postar).props('color=primary').classes('mt-2')


def renderizar_questao(qid, res_data, refresh_callback=None):
    """Renderiza a interface do quesito dentro de um card NiceGUI."""
    dados_q = res_data.get(qid, {})
    val_existente = dados_q.get("valor", "")
    pts_existente = float(dados_q.get("pontos", 0.0))
    link_existente = dados_q.get("link", "")
    comentarios_existentes = dados_q.get("comentarios", [])
    
    with ui.card().classes('w-full border p-4 shadow-sm mb-4'):
        ui.label(f"Quesito: {qid}").classes('text-lg font-bold text-slate-700')
        
        with ui.row().classes('w-full gap-4 items-start'):
            with ui.column().classes('flex-1 gap-2'):
                novo_valor = ui.textarea("Resposta / Evidência:", value=val_existente).classes('w-full')
                novo_link = ui.input("Link da Evidência (opcional):", value=link_existente).classes('w-full')
            
            with ui.column().classes('w-48 gap-2'):
                novos_pontos = ui.number("Pontuação:", value=pts_existente, format='%.1f').classes('w-full')
                
                def salvar():
                    save_resp(
                        qid=qid,
                        valor=novo_valor.value,
                        pontos=novos_pontos.value or 0.0,
                        link=novo_link.value,
                        comentarios=comentarios_existentes
                    )
                    if refresh_callback:
                        refresh_callback()

                ui.button(f"💾 Salvar {qid}", on_click=salvar).props('color=primary').classes('w-full mt-4')

        bloco_comentarios(qid, res_data, refresh_callback)

# =============================================================================
# 4. FUNÇÕES DE ANÁLISE E HISTÓRICO
# =============================================================================

def get_all_years_data():
    """Retorna todos os registros agrupados por ano."""
    all_data = {}
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT id, ano, valor, pontos, link, comentarios FROM respostas ORDER BY ano DESC")
                rows = cursor.fetchall()
                for row in rows:
                    qid, ano = str(row["id"]), row["ano"]
                    comentarios_bruto = row.get("comentarios")
                    comentarios = []
                    
                    if isinstance(comentarios_bruto, list):
                        comentarios = comentarios_bruto
                    elif isinstance(comentarios_bruto, str) and comentarios_bruto and comentarios_bruto != "EMPTY_STRING":
                        try:
                            comentarios = json.loads(comentarios_bruto)
                        except Exception:
                            comentarios = []

                    if ano not in all_data:
                        all_data[ano] = {}
                    all_data[ano][qid] = {
                        "valor": row["valor"] or "", 
                        "pontos": float(row["pontos"] or 0.0), 
                        "link": row["link"] or "", 
                        "comentarios": comentarios
                    }
    except Exception as e:
        logging.error(f"Erro ao buscar dados históricos: {e}")
    return all_data


def analyze_performance(res_data):
    """Mapeia os pontos fortes e fragilidades do ano atual."""
    pontos_fortes = []
    criticos_zero = {"Alta": [], "Média": [], "Baixa": []}
    criticos_negativos = {"Alta": [], "Média": [], "Baixa": []}

    pontuacoes_referencia = {
        "1.0": {"max": 40}, "1.3": {"max": 5}, "1.4": {"max": 50},
        "2.0": {"max": 20}, "2.1": {"max": 30}, "2.2": {"max": 10},
        "3.0": {"max": 10}, "3.1": {"max": 10}, "4.2": {"max": 10},
        "5.0": {"max": 30}, "5.1.1": {"max": 20}, "5.2": {"max": 10},
        "6.0": {"max": 30}, "7.0": {"max": 30}, "7.1": {"max": 10},
        "7.2": {"max": 80}, "7.3": {"max": 10}, "7.4": {"max": 10},
        "7.5": {"max": 10}, "7.6": {"max": 10}, "8.0": {"max": 30},
        "8.1.1.1": {"max": 20}, "8.2": {"max": 10}, "9.0": {"max": 30},
        "10.0": {"max": 0}, "11.1": {"max": 20}, "11.1.1": {"max": 10},
        "11.2": {"max": 10}, "12.1": {"max": 20}, "12.1.3": {"max": 10},
        "14.0": {"max": 30}, "15.0": {"max": 30}, "16.0": {"max": 30},
        "C1.1": {"max": 0}
    }

    def classificar_relevancia(impacto):
        abs_impacto = abs(impacto)
        if abs_impacto >= 16:
            return "Alta"
        elif 6 <= abs_impacto <= 15:
            return "Média"
        else:
            return "Baixa"

    for qid, info in res_data.items():
        qid_str = str(qid)
        if qid_str.startswith("COM_") or qid_str not in pontuacoes_referencia:
            continue

        pontos_atuais = info.get("pontos", 0)
        max_pontos = pontuacoes_referencia[qid_str]["max"]

        if pontos_atuais == max_pontos:
            pontos_fortes.append((qid_str, pontos_atuais, info.get("valor", ""), info.get("link", "")))
        else:
            impacto = max_pontos - pontos_atuais
            relevancia = classificar_relevancia(impacto)

            if pontos_atuais < 0:
                criticos_negativos[relevancia].append(
                    (qid_str, pontos_atuais, info.get("valor", ""), info.get("link", ""), impacto)
                )
            else:
                criticos_zero[relevancia].append(
                    (qid_str, pontos_atuais, info.get("valor", ""), info.get("link", ""), impacto)
                )

    pontos_fortes.sort(key=lambda x: x[1], reverse=True)
    for rel in ["Alta", "Média", "Baixa"]:
        criticos_zero[rel].sort(key=lambda x: x[4], reverse=True)
        criticos_negativos[rel].sort(key=lambda x: x[4], reverse=True)

    return pontos_fortes, criticos_zero, criticos_negativos


def analyze_recurrence(ano_atual, res_data_atual):
    """Compara as perdas de pontos do ano selecionado com anos anteriores (reincidências)."""
    reincidencias = []
    all_data = get_all_years_data()

    qids_pontuaveis = [
        "1.0", "1.3", "1.4", "2.0", "2.1", "2.2", "3.0", "3.1", "4.2",
        "5.0", "5.1.1", "5.2", "6.0", "7.0", "7.1", "7.2", "7.3", "7.4",
        "7.5", "7.6", "8.0", "8.1.1.1", "8.2", "9.0", "10.0", "11.1",
        "11.1.1", "11.2", "12.1", "12.1.3", "14.0", "15.0", "16.0", "C1.1"
    ]

    anos_anteriores = sorted([a for a in all_data.keys() if a < ano_atual], reverse=True)

    for qid_atual, info_atual in res_data_atual.items():
        qid_str = str(qid_atual)
        if qid_str.startswith("COM_") or qid_str not in qids_pontuaveis:
            continue
            
        pontos_atual = info_atual.get("pontos", 0)
        
        if pontos_atual <= 0:
            for ano_anterior in anos_anteriores:
                if qid_str in all_data[ano_anterior]:
                    pontos_anterior = all_data[ano_anterior][qid_str].get("pontos", 0)
                    if pontos_anterior <= 0:
                        reincidencias.append((qid_str, ano_anterior, pontos_anterior, pontos_atual))
                        break

    return reincidencias
