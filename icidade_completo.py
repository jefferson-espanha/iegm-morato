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

# =============================================================================
# 3. GERADOR DO RELATÓRIO PDF (REPORTLAB)
# =============================================================================

def gerar_relatorio_pdf(dados, ano, total, faixa):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()

    # -------------------------------------------------------------------------
    # FOLHA 1: CAPA
    # -------------------------------------------------------------------------
    elements.append(Spacer(1, 100))
    
    logo_path = "iegm.png"
    if os.path.exists(logo_path):
        try:
            logo = Image(logo_path, width=380, height=180)
            logo.hAlign = 'CENTER'
            elements.append(logo)
        except Exception:
            elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
    else:
        elements.append(Paragraph("[Logo: iegm.png]", styles["Title"]))
        
    elements.append(Spacer(1, 50))
    
    style_titulo_capa = ParagraphStyle(
        'TituloCapa', 
        parent=styles['Normal'], 
        fontName='Helvetica-Bold', 
        fontSize=24, 
        textColor=colors.HexColor("#2c3e50"), 
        alignment=1
    )

    elements.append(Paragraph("Relatório I-Cidade", style_titulo_capa))
    elements.append(Spacer(1, 15))
    
    style_ano_capa = ParagraphStyle('AnoCapa', parent=styles['Normal'], fontName='Helvetica', fontSize=16, textColor=colors.HexColor("#7f8c8d"), alignment=1)
    elements.append(Paragraph(str(ano), style_ano_capa))
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # FOLHA 2: SUMÁRIO
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>SUMÁRIO</b>", styles["h1"]))
    elements.append(Spacer(1, 30))

    style_item_esquerda = ParagraphStyle('ItemEsq', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor("#2c3e50"))
    style_pag_direita = ParagraphStyle('PagDir', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor("#1b4f72"), alignment=2)

    dados_sumario = [
        [Paragraph("1. Resumo Executivo (Análise Comparativa)", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
        [Paragraph("2. Análise de Desempenho por Quesito", style_item_esquerda), Paragraph("Pág. 3", style_pag_direita)],
        [Paragraph("3. Análise de Impacto e Penalidades", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
        [Paragraph("4. Diagnóstico de Reincidências", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
        [Paragraph("5. Alinhamento com a Agenda 2030 (ODS)", style_item_esquerda), Paragraph("Pág. 4", style_pag_direita)],
        [Paragraph("6. Série Histórica do I-cidade", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
        [Paragraph("7. Quesitos Sem Pontuação Direta", style_item_esquerda), Paragraph("Pág. 5", style_pag_direita)],
    ]
    
    tabela_sumario = Table(dados_sumario, colWidths=[400, 90])
    tabela_sumario.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7"), 1, (2, 4)), 
    ]))
    elements.append(tabela_sumario)
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA DE EXERCÍCIOS)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>1. RESUMO EXECUTIVO (ANÁLISE COMPARATIVA)</b>", styles["h2"]))
    elements.append(Spacer(1, 8))

    nota_atual = float(total)
    ano_atual = int(str(ano).strip()[:4])
    ano_ant = ano_atual - 1

    def converter_pontos_em_faixa_iegm(pontos):
        pts = float(pontos)
        if pts < 500.0:              return "C"
        elif 500.0 <= pts <= 599.9:  return "C+"
        elif 600.0 <= pts <= 749.9:  return "B"
        elif 750.0 <= pts <= 899.9:  return "B+"
        else:                        return "A"

    all_data = {}
    try:
        all_data = get_all_years_data()
    except Exception:
        all_data = {}

    dados_ano_anterior = all_data.get(ano_ant, {})
    nota_anterior = 0.0
    if ano_ant in all_data:
        nota_anterior = float(sum(
            info_ant.get("pontos", 0) 
            for qid_ant, info_ant in dados_ano_anterior.items() 
            if isinstance(info_ant, dict) and not qid_ant.startswith("COM_")
        ))

    faixa_anterior = converter_pontos_em_faixa_iegm(nota_anterior)
    faixa_real_atual = faixa if faixa else converter_pontos_em_faixa_iegm(nota_atual)

    variacao_pontos = nota_atual - nota_anterior
    if nota_anterior > 0:
        variacao_percentual = (variacao_pontos / nota_anterior) * 100
        texto_percentual = f"{variacao_percentual:+.2f}%"
    else:
        texto_percentual = "0.00%"

    if variacao_pontos > 0:
        cor_variacao = colors.HexColor("#28a745")
        seta_tendencia = "▲"
    elif variacao_pontos < 0:
        cor_variacao = colors.HexColor("#dc3545")
        seta_tendencia = "▼"
    else:
        cor_variacao = colors.HexColor("#6c757d")
        seta_tendencia = "■"

    style_th = ParagraphStyle('Th', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.whitesmoke, alignment=1)
    style_td_ano = ParagraphStyle('TdAno', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#2c3e50"), alignment=1)
    style_td_pts = ParagraphStyle('TdPts', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1)
    style_td_faixa = ParagraphStyle('TdFaixa', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor("#1b4f72"), alignment=1)
    style_td_var = ParagraphStyle('TdVar', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=cor_variacao, alignment=1)

    dados_comparativos = [
        [Paragraph("Exercício", style_th), Paragraph("Pontuação Obtida", style_th), Paragraph("Faixa / Conceito", style_th), Paragraph("Variação Nominal", style_th), Paragraph("Variação Percentual", style_th)],
        [Paragraph(str(ano_ant), style_td_ano), Paragraph(f"{nota_anterior:.1f} pts", style_td_pts), Paragraph(str(faixa_anterior), style_td_faixa), Paragraph("-", style_td_var), Paragraph("-", style_td_var)],
        [Paragraph(str(ano_atual), style_td_ano), Paragraph(f"{nota_atual:.1f} pts", style_td_pts), Paragraph(str(faixa_real_atual), style_td_faixa), Paragraph(f"{seta_tendencia} {variacao_pontos:+.1f} pts", style_td_var), Paragraph(f"{seta_tendencia} {texto_percentual}", style_td_var)]
    ]

    tabela_comp = Table(dados_comparativos, colWidths=[80, 105, 95, 105, 105])
    tabela_comp.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")), 
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8f9fa")), ("BACKGROUND", (0, 2), (-1, 2), colors.whitesmoke),          
    ]))
    elements.append(tabela_comp)
    elements.append(Spacer(1, 12))

    style_analise = ParagraphStyle('Analise', parent=styles['Normal'], fontSize=10, leading=14)
    if variacao_pontos > 0:
        texto_analise = f"<b>Análise de Tendência:</b> O município registrou uma evolução de desempenho com incremento de <b>{texto_percentual}</b> na sua pontuação global comparado ao exercício de {ano_ant}."
    elif variacao_pontos < 0:
        texto_analise = f"<b>Análise de Tendência:</b> <font color='#dc3545'><b>Alerta de Retrocesso:</b></font> Foi identificada uma redução de <b>{texto_percentual}</b> na eficiência dos indicadores em relação a {ano_ant}."
    else:
        texto_analise = f"<b>Análise de Tendência:</b> O município apresentou estagnação absoluta (0.00%) no seu índice geral de conformidade."

    elements.append(Paragraph(texto_analise, style_analise))
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 2. ANÁLISE DE DESEMPENHO POR QUESITO
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>2. ANÁLISE DE DESEMPENHO POR QUESITO</b>", styles["h2"]))
    elements.append(Spacer(1, 6))

    lista_pontos_fortes = []
    lista_pontos_fracos = []
    reincidencias_detectadas = []

    for qid, info in dados.items():
        if qid.startswith("COM_") or not isinstance(info, dict): continue
        pts_obtidos = float(info.get("pontos", 0))
        valor_resposta = info.get("valor", "")
        link_evidencia = info.get("link", "")
        pts_maximo = float(PONTUACOES_MAX.get(qid, 0))
        
        if pts_maximo > 0:
            eficiencia = (pts_obtidos / pts_maximo) * 100
            item_data = {"qid": qid, "pts_obtidos": pts_obtidos, "pts_maximo": pts_maximo, "eficiencia": eficiencia, "valor": valor_resposta, "link": link_evidencia}
            if eficiencia >= 70.0: lista_pontos_fortes.append(item_data)
            elif eficiencia < 50.0:
                lista_pontos_fracos.append(item_data)
                if qid in dados_ano_anterior:
                    info_ant = dados_ano_anterior[qid]
                    pts_anterior = float(info_ant.get("pontos", 0))
                    if pts_obtidos == pts_anterior:
                        reincidencias_detectadas.append({"qid": qid, "tipo": "Ponto Fraco", "detalhe": "Eficiência Crítica", "ant": f"{pts_anterior:.1f} pts", "atual": f"{pts_obtidos:.1f} pts"})

    if lista_pontos_fortes:
        elements.append(Paragraph("<b>✅ Pontos Fortes:</b>", styles["h3"]))
        data_fortes = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for item in sorted(lista_pontos_fortes, key=lambda x: x["pts_obtidos"], reverse=True):
            evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
            data_fortes.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
        tabela_fortes = Table(data_fortes, colWidths=[65, 75, 65, 285])
        tabela_fortes.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#28a745")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#28a745")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(tabela_fortes)
        elements.append(Spacer(1, 12))

    if lista_pontos_fracos:
        elements.append(Paragraph("<b>⚠️ Pontos Fracos Geral:</b>", styles["h3"]))
        data_fracos = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for item in sorted(lista_pontos_fracos, key=lambda x: x["pts_obtidos"]):
            evidencia = f"<b>{item['valor']}</b><br/>{item['link']}"
            data_fracos.append([item['qid'], f"{item['pts_obtidos']:.1f} / {item['pts_maximo']:.1f}", f"{item['eficiencia']:.1f}%", Paragraph(evidencia, styles["Normal"])])
        tabela_fracos = Table(data_fracos, colWidths=[65, 75, 65, 285])
        tabela_fracos.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e67e22")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (2, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e67e22")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(tabela_fracos)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 3. ANÁLISE DE IMPACTO E PENALIDADES
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>3. ANÁLISE DE IMPACTO E PENALIDADES (EFICIÊNCIA PREVENTIVA)</b>", styles["h2"]))
    elements.append(Spacer(1, 6))

    PENALIDADES_MAX = {"4.2": -50.0, "5.1.1": -100.0, "5.2": -50.0, "6.0": -50.0, "10": -100.0, "10.0": -100.0, "11.1": -20.0, "11.2": -20.0, "11.2.1": -20.0, "12.1.3": -50.0, "14.0": -50.0}

    lista_penalidades = []
    for qid, pen_max in PENALIDADES_MAX.items():
        if qid in dados:
            info = dados[qid]
            nota_real = float(info.get("pontos", 0))
            nota_risco = nota_real if nota_real <= 0 else 0.0
            eficiencia_preventiva = (1.0 - (nota_risco / pen_max)) * 100.0
            lista_penalidades.append({"qid": qid, "nota_real": nota_real, "pen_max": pen_max, "eficiencia": eficiencia_preventiva, "valor": info.get("valor", ""), "link": info.get("link", "")})
            if eficiencia_preventiva < 100.0 and qid in dados_ano_anterior:
                info_ant = dados_ano_anterior[qid]
                nota_real_ant = float(info_ant.get("pontos", 0))
                if nota_real == nota_real_ant:
                    reincidencias_detectadas.append({"qid": qid, "tipo": "Penalidade Aplicada", "detalhe": f"Impacto Recorrente de {nota_real:.1f} pts", "ant": f"{nota_real_ant:.1f} pts", "atual": f"{nota_real:.1f} pts"})

    if lista_penalidades:
        data_penalidades = [["Quesito", "Penalidade Aplicada", "Pior Cenário", "Eficiência Preventiva", "Status de Risco"]]
        for item in sorted(lista_penalidades, key=lambda x: x["eficiencia"]):
            nota_txt = f"{item['nota_real']:.1f} pts"; teto_txt = f"{item['pen_max']:.1f} pts"; ef_txt = f"{item['eficiencia']:.1f}%"
            if item['eficiencia'] == 100.0: status = "<font color='#28a745'><b>Risco Mitigado</b></font>"
            elif item['eficiencia'] <= 0.0: status = "<font color='#dc3545'><b>Impacto Máximo</b></font>"
            else: status = "<font color='#ffc107'><b>Impacto Parcial</b></font>"
            data_penalidades.append([item['qid'], nota_txt, teto_txt, ef_txt, Paragraph(status, styles["Normal"])])
        tabela_pen = Table(data_penalidades, colWidths=[65, 110, 80, 115, 120])
        tabela_pen.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b4f72")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#1b4f72")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elements.append(tabela_pen)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 4. DIAGNÓSTICO DE REINCIDÊNCIAS 
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>4. DIAGNÓSTICO DE REINCIDÊNCIAS </b>", styles["h2"]))
    elements.append(Spacer(1, 6))
    if reincidencias_detectadas:
        data_reinc = [["Quesito", "Origem da Falha", "Impacto Histórico", "Exercício Anterior", "Exercício Atual"]]
        for reinc in reincidencias_detectadas: data_reinc.append([reinc["qid"], reinc["tipo"], Paragraph(f"<b>{reinc['detalhe']}</b>", styles["Normal"]), reinc["ant"], reinc["atual"]])
        tabela_reinc = Table(data_reinc, colWidths=[65, 115, 170, 75, 65])
        tabela_reinc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0392b")), ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elements.append(tabela_reinc)
    else: elements.append(Paragraph("<font color='#28a745'><b>Nenhuma reincidência ativa detectada.</b></font>", styles["Normal"]))
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS / ONU)</b>", styles["h2"]))
    elements.append(Spacer(1, 6))
    def calcular_percentual_checklist(resposta_bruta, total_itens):
        if not resposta_bruta: return 0.0
        itens = [i.strip().lower() for i in str(resposta_bruta).split(",") if i.strip()]
        itens_validos = [i for i in itens if "outros" not in i]
        return min((len(itens_validos) / total_itens) * 100.0, 100.0) if total_itens > 0 else 0.0

    analise_ods = []
    for qid, info in dados.items():
        if qid.startswith("COM_") or not isinstance(info, dict): continue
        resp = str(info.get("valor", "")).strip(); resp_l = resp.lower(); metas = ""; status = ""
        if qid == "1.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "1.4": metas = "11.5, 16.6"; status = "Não Atendido" if "não atuam de forma sistêmica" in resp_l else "Atendido"
        elif qid == "2.0": metas = "11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "3.0": metas = "11.5, 16.7, 16.10, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "3.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
        elif qid == "4.0": metas = "1.5, 11.5, 11b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "5.0": metas = "1.5, 11.5, 16b"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "5.1": metas = "11b, 11.5, 16.7, 16.10"; status = f"{calcular_percentual_checklist(resp, 8):.1f}% Atendido"
        elif qid == "5.1.1": metas = "11b, 11.5, 16.6, 16.10"; status = "Atendido" if ("sim, integralmente" in resp_l or "sim, parcialmente" in resp_l) else "Não Atendido"
        elif qid == "5.1.1.1": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "5.1.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if "não" in resp_l else "Não Atendido"
        elif qid == "5.2": metas = "11b, 11.5, 16.6"; status = "Atendido" if ("sim" in resp_l or "parcialmente" in resp_l) else "Não Atendido"
        elif qid == "7.0": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "7.3": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "7.3.1": metas = "11b, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
        elif qid == "7.4": metas = "11b, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "7.4.1": metas = "11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 7):.1f}% Atendido"
        elif qid == "7.5": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
        elif qid == "7.6": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("sim, atualizado" in resp_l or "sim, mas não está atualizado" in resp_l) else "Não Atendido"
        elif qid in ["8", "8.0"]: metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "8.1": metas = "1.5, 11.5, 16.6"; status = f"{calcular_percentual_checklist(resp, 6):.1f}% Atendido"
        elif qid == "8.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "8.1.1.1": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "8.2": metas = "1.5, 11.5, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "9.0": metas = "1.5, 11.5, 16.6"; status = "Atendido" if ("todas as escolas" in resp_l or "maior parte" in resp_l) else "Não Atendido"
        elif qid in ["10", "10.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["11", "11.0"]: metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "11.1": metas = "11.2, 16.6"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "12.0": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "12.1.3": metas = "11.2, 17.0"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["13", "13.0"]: metas = "11.2, 11.7, 12.5"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid == "14.0": metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["15", "15.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"
        elif qid in ["16", "16.0"]: metas = "11.2, 17.14"; status = "Atendido" if "sim" in resp_l else "Não Atendido"

        if metas: analise_ods.append({"qid": qid, "status": status, "metas": metas, "resp": resp[:50]})

    if analise_ods:
        data_ods = [["Quesito", "Resposta Informada", "Vínculo Metas ODS", "Status de Cumprimento"]]
        style_td_ods = ParagraphStyle('TdOds', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, alignment=1)
        for item in sorted(analise_ods, key=lambda x: [float(i) if i.replace('.','',1).isdigit() else 999 for i in x['qid'].split('.')]):
            st_txt = item["status"]
            if "Não Atendido" in st_txt: st_p = Paragraph(f"<font color='#dc3545'><b>{st_txt}</b></font>", style_td_ods)
            elif "Atendido" in st_txt and "%" not in st_txt: st_p = Paragraph(f"<font color='#28a745'><b>{st_txt}</b></font>", style_td_ods)
            else: st_p = Paragraph(f"<font color='#007bff'><b>{st_txt}</b></font>", style_td_ods)
            data_ods.append([item["qid"], Paragraph(item["resp"], styles["Normal"]), item["metas"], st_p])
        tabela_ods = Table(data_ods, colWidths=[60, 200, 115, 110])
        tabela_ods.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f9d58")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("ALIGN", (0, 0), (0, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#0f9d58")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elements.append(tabela_ods)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 6. SÉRIE HISTÓRICA DO I-CIDADE
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>6. SÉRIE HISTÓRICA DO I-CIDADE</b>", styles["h2"]))
    elements.append(Spacer(1, 10))

    anos_serie = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    valores_serie = []
    for a in anos_serie:
        if a == ano_atual: valores_serie.append(nota_atual)
        elif a in all_data:
            valores_serie.append(float(sum(info_h.get("pontos", 0) for qid_h, info_h in all_data[a].items() if isinstance(info_h, dict) and not qid_h.startswith("COM_"))))
        else: valores_serie.append(0.0)

    desenho_grafico = Drawing(480, 165)
    bc = VerticalBarChart()
    bc.x = 45; bc.y = 25; bc.height = 110; bc.width = 410
    bc.data = [valores_serie]
    bc.categoryAxis.categoryNames = [str(a) for a in anos_serie]
    bc.categoryAxis.labels.fontSize = 9; bc.categoryAxis.labels.fontName = 'Helvetica-Bold'; bc.categoryAxis.labels.dy = -10
    
    bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = 1000; bc.valueAxis.valueStep = 200; bc.valueAxis.labels.fontSize = 8
    
    bc.barLabels.nudge = 8
    bc.barLabels.fontSize = 8
    bc.barLabels.fontName = 'Helvetica-Bold'
    bc.barLabelFormat = '%.1f'
    
    bc.bars[0].fillColor = colors.HexColor("#1b4f72")
    bc.bars[0].strokeColor = colors.HexColor("#2c3e50")
    bc.bars[0].strokeWidth = 0.5

    desenho_grafico.add(String(240, 150, "Série Histórica do I-cidade", textAnchor='middle', fontName='Helvetica-Bold', fontSize=12, fillColor=colors.HexColor("#2c3e50")))
    desenho_grafico.add(bc)
    
    elements.append(desenho_grafico)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>7. QUESITOS SEM PONTUAÇÃO DIRETA (ICIDADE - CONFORMIDADE OPERACIONAL)</b>", styles["h2"]))
    elements.append(Spacer(1, 6))

    lista_alvo_sp = [
        "4.0", "11.0", "12.0", "13.0", "4.1", "5.1", 
        "5.1.2", "5.1.2.1", "7.3.1", "8.4.1", "8.1", "8.1.1", "12.1.3.1", "14.1"
    ]

    analise_sp = []
    
    for qid in lista_alvo_sp:
        info = dados.get(qid) or dados.get(f"Q_{qid}") or {}
        
        if isinstance(info, dict):
            resp = str(info.get("valor", "")).strip()
        else:
            resp = str(info).strip()

        resp_l = resp.lower()
        is_adequado = False

        if qid in ["4.0", "11.0", "12.0", "13.0", "8.1.1"]:
            if any(x == resp_l or x in resp_l for x in ["sim", "1", "s", "true", "adequado"]):
                is_adequado = True

        elif qid == "4.1":
            opcoes = ["riscos geológicos", "riscos hidrológicos", "riscos meteorológicos", "riscos biológicos"]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "5.1":
            opcoes = ["epidemias", "estiagem", "incêndios", "ondas de calor ou ondas de frio", "inundações"]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "5.1.2":
            if any(x == resp_l or x in resp_l for x in ["não", "nao", "0", "n", "false"]):
                is_adequado = True

        elif qid == "5.1.2.1":
            opcoes = [
                "aplicação de sanções monetárias (multas)",
                "monitoramento (fiscalização)",
                "notificação dos infratores",
                "demolição das ocupações"
            ]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "7.3.1":
            opcoes = ["alerta via sms", "aviso por telefone", "aviso por email", "anúncio por rádio/televisão"]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "7.4.1":
            opcoes = [
                "sinal sonoro (sirene)",
                "sinal luminoso",
                "carros de emergência com sirenes",
                "avisos aos membros do nupdec"
            ]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "8.1":
            opcoes = ["telefone de emergências", "aplicativo de mensagens", "site da prefeitura", "redes sociais"]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        elif qid == "12.1.3.1":
            if "diariamente" in resp_l:
                is_adequado = True

        elif qid == "14.1":
            opcoes = [
                "calçadas com dimensões mínimas para circulação",
                "sinalização tátil em pisos",
                "rampas de acesso",
                "escadas com corrimão"
            ]
            if any(opt in resp_l for opt in opcoes):
                is_adequado = True

        status_txt = "Adequado" if is_adequado else "Inadequado"

        analise_sp.append({
            "qid": qid,
            "resp": resp if resp else "Não Informado",
            "status": status_txt
        })

    if analise_sp:
        style_td_sp = ParagraphStyle('TdSp', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, alignment=1)
        
        data_sp = [[
            Paragraph("Quesito", style_th), 
            Paragraph("Resposta Informada no Sistema", style_th), 
            Paragraph("Situação / Conformidade", style_th)
        ]]

        total_adequados = 0
        for item in analise_sp:
            if item["status"] == "Adequado":
                total_adequados += 1
                st_p = Paragraph("<font color='#28a745'><b>✅ Adequado</b></font>", style_td_sp)
            else:
                st_p = Paragraph("<font color='#dc3545'><b>❌ Inadequado</b></font>", style_td_sp)

            data_sp.append([
                Paragraph(f"<b>{item['qid']}</b>", style_td_sp),
                Paragraph(item["resp"], styles["Normal"]),
                st_p
            ])

        tabela_sp = Table(data_sp, colWidths=[70, 280, 135])
        tabela_sp.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ffffff")),
        ]))
        elements.append(tabela_sp)
        elements.append(Spacer(1, 8))

        pct_sp = (total_adequados / len(analise_sp)) * 100.0
        texto_sp = (
            f"A análise dinâmica dos quesitos de conformidade operacional do iCidade no exercício de <b>{ano_atual}</b> apontou "
            f"<b>{total_adequados} de {len(analise_sp)} itens adequados ({pct_sp:.1f}%)</b>. "
            f"O acompanhamento dessas respostas garante a conformidade com as diretrizes operacionais estabelecidas."
        )
        elements.append(Paragraph(texto_sp, style_analise))
        elements.append(Spacer(1, 15))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


# =============================================================================
# 4. DOWNLOAD DO RELATÓRIO VIA NICEGUI
# =============================================================================

def disparar_download_pdf(respostas_atuais, ano_sel, total_pontos, faixa_conceito):
    """Gera o arquivo PDF na memória e aciona o download nativo no navegador pelo NiceGUI."""
    bytes_pdf = gerar_relatorio_pdf(respostas_atuais, ano_sel, total_pontos, faixa_conceito)
    ui.download(bytes_pdf, filename=f"Relatorio_ICidade_{ano_sel}.pdf")


# =============================================================================
# 5. PÁGINA PRINCIPAL DA APLICAÇÃO NICEGUI
# =============================================================================

@ui.page('/')
def main_page():
    # Inicializa variáveis na sessão do usuário
    if 'ano_referencia_global' not in app.storage.user:
        app.storage.user['ano_referencia_global'] = date.today().year

    ano_sel = app.storage.user['ano_referencia_global']
    respostas_atuais = load_respostas(ano_sel)

    # Cálculo do Total Geral
    total_pontos = sum(
        v.get("pontos", 0.0) for k, v in respostas_atuais.items() 
        if isinstance(v, dict) and not k.startswith("COM_")
    )

    if total_pontos < 500.0:
        faixa_conceito = "C"
    elif 500.0 <= total_pontos <= 599.9:
        faixa_conceito = "C+"
    elif 600.0 <= total_pontos <= 749.9:
        faixa_conceito = "B"
    elif 750.0 <= total_pontos <= 899.9:
        faixa_conceito = "B+"
    else:
        faixa_conceito = "A"

    # Bar do topo / Header
    with ui.header().classes('bg-slate-800 text-white flex justify-between items-center px-6 py-3'):
        ui.label('🏛️ Sistema de Gestão e Diagnóstico I-Cidade').classes('text-xl font-bold')
        
        with ui.row().classes('items-center gap-4'):
            ui.label("Ano de Referência:").classes('text-sm')
            anos_disponiveis = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
            
            def ao_mudar_ano(e):
                app.storage.user['ano_referencia_global'] = e.value
                ui.open('/')

            ui.select(options=anos_disponiveis, value=ano_sel, on_change=ao_mudar_ano).props('dense options-dense').classes('bg-white rounded px-2 text-black')

    # Conteúdo Principal
    with ui.column().classes('w-full max-w-7xl mx-auto p-6 gap-6'):
        
        # Placar / Dash de Resultados Globais
        with ui.row().classes('w-full justify-between items-center bg-white p-6 rounded-xl border shadow-sm'):
            with ui.column():
                ui.label(f"Exercício Selecionado: {ano_sel}").classes('text-sm text-slate-500 font-semibold')
                ui.label(f"{total_pontos:.1f} Pontos").classes('text-4xl font-extrabold text-slate-800')
            
            with ui.column().classes('items-center'):
                ui.label("Faixa / Conceito").classes('text-sm text-slate-500 font-semibold')
                cor_badge = FAIXA_CORES.get(faixa_conceito, "#6c757d")
                ui.html(f"""
                    <div style="background-color: {cor_badge}; color: white; font-weight: bold; font-size: 24px; padding: 6px 24px; border-radius: 9999px;">
                        {faixa_conceito}
                    </div>
                """)

            ui.button(
                "📄 Baixar Relatório PDF", 
                on_click=lambda: disparar_download_pdf(respostas_atuais, ano_sel, total_pontos, faixa_conceito)
            ).props('color=primary icon=download').classes('h-12')

        # Guias de navegação (Tabs)
        with ui.tabs().classes('w-full') as tabs:
            tab_questoes = ui.tab('Quesitos e Evidências', icon='edit_note')
            tab_analise = ui.tab('Diagnóstico e Impacto', icon='analytics')
            tab_historico = ui.tab('Série Histórica', icon='show_chart')

        with ui.tab_panels(tabs, value=tab_questoes).classes('w-full bg-transparent'):
            
            # PAINEL 1: Quesitos divididos por categorias
            with ui.tab_panel(tab_questoes):
                @ui.refreshable
                def render_painel_questoes():
                    for cat_key, cat_info in CATEGORIAS_MAP.items():
                        with ui.expansion(f"📁 {cat_info['label']}", icon='folder').classes('w-full bg-white border rounded-xl mb-3'):
                            for qid in cat_info["qids"]:
                                renderizar_questao(qid, respostas_atuais, refresh_callback=render_painel_questoes.refresh)

                render_painel_questoes()

            # PAINEL 2: Análise de Desempenho e Reincidências
            with ui.tab_panel(tab_analise):
                pontos_fortes, criticos_zero, criticos_negativos = analyze_performance(respostas_atuais)
                reincidencias = analyze_recurrence(ano_sel, respostas_atuais)

                ui.label("Análise de Performance e Fragilidades").classes('text-xl font-bold text-slate-800 mb-4')

                with ui.row().classes('w-full gap-4'):
                    # Pontos Fortes
                    with ui.card().classes('flex-1 border-emerald-200 bg-emerald-50/30 p-4'):
                        ui.label(f"✅ Pontos Fortes ({len(pontos_fortes)})").classes('text-lg font-bold text-emerald-800')
                        for item in pontos_fortes:
                            ui.label(f"• Quesito {item[0]}: {item[1]} pts").classes('text-sm text-slate-700')

                    # Reincidências
                    with ui.card().classes('flex-1 border-rose-200 bg-rose-50/30 p-4'):
                        ui.label(f"⚠️ Reincidências Detectadas ({len(reincidencias)})").classes('text-lg font-bold text-rose-800')
                        for item in reincidencias:
                            ui.label(f"• Quesito {item[0]} (Anterior: {item[2]} pts | Atual: {item[3]} pts)").classes('text-sm text-slate-700')

            # PAINEL 3: Gráfico de Evolução
            with ui.tab_panel(tab_historico):
                ui.label("Evolução Multianual do I-Cidade").classes('text-xl font-bold text-slate-800 mb-4')
                
                todos_dados_anos = get_all_years_data()
                anos_eixo = sorted(list(set(list(todos_dados_anos.keys()) + [ano_sel])))
                valores_eixo = [
                    sum(v.get("pontos", 0) for k, v in todos_dados_anos.get(a, {}).items() if isinstance(v, dict) and not k.startswith("COM_"))
                    for a in anos_eixo
                ]

                fig = px.bar(x=anos_eixo, y=valores_eixo, labels={'x': 'Exercício', 'y': 'Pontuação Global'}, text_auto='.1f')
                fig.update_traces(marker_color='#1b4f72')
                fig.update_layout(template='plotly_white', margin=dict(l=20, r=20, t=30, b=20))
                
                ui.plotly(fig).classes('w-full h-96')

