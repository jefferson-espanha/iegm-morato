import dataclasses
from datetime import date, datetime
import io
import logging
import os
from typing import List, Optional

import pandas as pd
from nicegui import app, ui

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)
ULTIMO_ERRO_BANCO = ""


def _normalizar_dados_acao(dados: dict) -> dict:
    """Converte campos vazios e datas para os tipos aceitos pelo schema."""
    global ULTIMO_ERRO_BANCO
    resultado = {chave: (valor.strip() if isinstance(valor, str) else valor)
                 for chave, valor in dados.items()}

    # Limites definidos no schema plano_acao_iegm. O banco rejeita a operação
    # quando um texto ultrapassa VARCHAR(n); cortar somente esses campos evita
    # que uma descrição longa impeça o salvamento dos demais dados.
    limites_varchar = {
        "dimensao": 50,
        "integracao_planejamento_municipal": 100,
        "periodo_report": 50,
        "responsavel": 150,
        "status": 50,
    }
    for chave, limite in limites_varchar.items():
        valor = resultado.get(chave)
        if isinstance(valor, str) and len(valor) > limite:
            logger.warning(
                "Campo %s excedeu VARCHAR(%s) e foi reduzido de %s para %s caracteres.",
                chave, limite, len(valor), limite,
            )
            resultado[chave] = valor[:limite]

    for chave in ("data_inicio", "data_conclusao"):
        valor = resultado.get(chave)
        if valor in ("", None):
            resultado[chave] = None
        elif isinstance(valor, str):
            try:
                resultado[chave] = datetime.strptime(valor[:10], "%Y-%m-%d").date()
            except ValueError as exc:
                ULTIMO_ERRO_BANCO = f"Data inválida em {chave}: {valor}"
                raise ValueError(ULTIMO_ERRO_BANCO) from exc
    obrigatorios = {
        "dimensao": "Dimensão",
        "acao": "Título da ação",
        "responsavel": "Responsável",
        "status": "Status",
    }
    faltantes = [rotulo for chave, rotulo in obrigatorios.items()
                 if not str(resultado.get(chave) or "").strip()]
    if faltantes:
        ULTIMO_ERRO_BANCO = "Preencha os campos obrigatórios: " + ", ".join(faltantes)
        raise ValueError(ULTIMO_ERRO_BANCO)
    return resultado


def obter_ultimo_erro_banco() -> str:
    return ULTIMO_ERRO_BANCO

# ==========================================
# GESTÃO DE CONEXÃO COM O NEON POSTGRESQL
# ==========================================
def obter_conexao():
    """Obtém o conector do projeto ou conecta diretamente ao PostgreSQL."""
    global ULTIMO_ERRO_BANCO
    try:
        try:
            from icidade_completo import get_connection
            return get_connection()
        except (ImportError, AttributeError):
            try:
                from icidade import get_connection
                return get_connection()
            except (ImportError, AttributeError):
                pass
        if psycopg2 is None:
            ULTIMO_ERRO_BANCO = "psycopg2 não está instalado e não há conector do projeto."
            logger.error("psycopg2 não está instalado e não há conector do projeto")
            return None
        database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
        if not database_url:
            try:
                import main
                database_url = getattr(main, "NEON_URL", None)
            except Exception:
                database_url = None
        if not database_url:
            ULTIMO_ERRO_BANCO = "DATABASE_URL/NEON_URL não configurada."
            logger.error("DATABASE_URL/NEON_URL não configurada")
            return None
        return psycopg2.connect(database_url)
    except Exception as exc:
        ULTIMO_ERRO_BANCO = f"Erro de conexão com o banco: {exc}"
        logger.exception("Erro ao conectar ao banco: %s", exc)
        return None


def garantir_schema_plano_acao(conn) -> None:
    """Garante a coluna nova sem apagar dados ou colunas legadas."""
    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE plano_acao_iegm "
            "ADD COLUMN IF NOT EXISTS metas_mensuraveis TEXT;"
        )
    conn.commit()


def carregar_dados_banco() -> List[dict]:
    """Carrega todos os registros do banco Neon PostgreSQL."""
    conn = obter_conexao()
    if not conn:
        return []

    try:
        garantir_schema_plano_acao(conn)
        if RealDictCursor is not None:
            try:
                cur_context = conn.cursor(cursor_factory=RealDictCursor)
            except (TypeError, AttributeError):
                cur_context = conn.cursor()
        else:
            cur_context = conn.cursor()
        with cur_context as cur:
            cur.execute("""
                SELECT id, dimensao, meta_estrategica, indicador_desempenho,
                       metas_mensuraveis, acao, descricao_acao, meta, resultados_esperados,
                       integracao_planejamento_municipal, alinhamento_ods,
                       data_inicio, data_conclusao, responsavel,
                       forma_execucao, evidencias, status
                FROM plano_acao_iegm
                ORDER BY id DESC;
            """)
            rows = cur.fetchall()
            if rows and isinstance(rows[0], dict):
                dados = [dict(row) for row in rows]
                for registro in dados:
                    registro["status"] = _normalizar_status(registro.get("status"))
                return dados
            colunas = [desc[0] for desc in (cur.description or [])]
            dados = [dict(zip(colunas, row)) for row in rows]
            for registro in dados:
                registro["status"] = _normalizar_status(registro.get("status"))
            return dados
    except Exception as e:
        logger.error(f"Erro ao carregar dados do Neon PostgreSQL: {e}")
        return []
    finally:
        conn.close()


def inserir_acao_banco(dados: dict) -> bool:
    """Insere uma nova ação no Neon PostgreSQL."""
    global ULTIMO_ERRO_BANCO
    try:
        dados = _normalizar_dados_acao(dados)
    except ValueError:
        return False
    conn = obter_conexao()
    if not conn:
        ULTIMO_ERRO_BANCO = "Não foi possível abrir conexão com o banco de dados."
        return False

    try:
        garantir_schema_plano_acao(conn)
    except Exception as exc:
        conn.close()
        ULTIMO_ERRO_BANCO = f"Não foi possível atualizar a estrutura da tabela: {exc}"
        return False

    query = """
        INSERT INTO plano_acao_iegm (
            dimensao, meta_estrategica, indicador_desempenho, acao,
            metas_mensuraveis, descricao_acao, meta, resultados_esperados,
            integracao_planejamento_municipal, alinhamento_ods,
            data_inicio, data_conclusao, responsavel, forma_execucao,
            evidencias, status
        ) VALUES (
            %(dimensao)s, %(meta_estrategica)s, %(indicador_desempenho)s, %(acao)s,
            %(metas_mensuraveis)s, %(descricao_acao)s, %(meta)s, %(resultados_esperados)s,
            %(integracao_planejamento_municipal)s, %(alinhamento_ods)s,
            %(data_inicio)s, %(data_conclusao)s, %(responsavel)s,
            %(forma_execucao)s, %(evidencias)s, %(status)s
        );
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query, dados)
            conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        ULTIMO_ERRO_BANCO = f"Erro ao inserir no PostgreSQL: {e}"
        logger.error(f"Erro ao inserir registro no Neon PostgreSQL: {e}")
        return False
    finally:
        conn.close()


def atualizar_acao_banco(registro_id: int, dados: dict) -> bool:
    """Atualiza um registro existente no Neon PostgreSQL pelo ID."""
    global ULTIMO_ERRO_BANCO
    try:
        dados = _normalizar_dados_acao(dados)
    except ValueError:
        return False
    conn = obter_conexao()
    if not conn:
        ULTIMO_ERRO_BANCO = "Não foi possível abrir conexão com o banco de dados."
        return False

    try:
        garantir_schema_plano_acao(conn)
    except Exception as exc:
        conn.close()
        ULTIMO_ERRO_BANCO = f"Não foi possível atualizar a estrutura da tabela: {exc}"
        return False

    dados["id"] = registro_id
    query = """
        UPDATE plano_acao_iegm SET
            dimensao = %(dimensao)s,
            meta_estrategica = %(meta_estrategica)s,
            indicador_desempenho = %(indicador_desempenho)s,
            metas_mensuraveis = %(metas_mensuraveis)s,
            acao = %(acao)s,
            descricao_acao = %(descricao_acao)s,
            meta = %(meta)s,
            resultados_esperados = %(resultados_esperados)s,
            integracao_planejamento_municipal = %(integracao_planejamento_municipal)s,
            alinhamento_ods = %(alinhamento_ods)s,
            data_inicio = %(data_inicio)s,
            data_conclusao = %(data_conclusao)s,
            responsavel = %(responsavel)s,
            forma_execucao = %(forma_execucao)s,
            evidencias = %(evidencias)s,
            status = %(status)s
        WHERE id = %(id)s;
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query, dados)
            conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        ULTIMO_ERRO_BANCO = f"Erro ao atualizar no PostgreSQL: {e}"
        logger.error(f"Erro ao atualizar registro no Neon PostgreSQL: {e}")
        return False
    finally:
        conn.close()


def deletar_acao_banco(registro_id: int) -> bool:
    """Deleta um registro no Neon PostgreSQL pelo ID."""
    conn = obter_conexao()
    if not conn:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM plano_acao_iegm WHERE id = %s;", (registro_id,)
            )
            conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Erro ao deletar registro no Neon PostgreSQL: {e}")
        return False
    finally:
        conn.close()


def recarregar_session_state():
    """Compatibilidade legada: retorna os dados atuais do banco."""
    return carregar_dados_banco()


def init_session_state():
    """Mantido para compatibilidade; o NiceGUI recarrega por componente."""
    return None


def converter_para_df() -> pd.DataFrame:
    """Converte os registros atuais em DataFrame sem depender de estado externo."""
    return pd.DataFrame(carregar_dados_banco(), columns=COLUNAS)


# ==========================================
# GERADOR DE PDF COM REPORTLAB
# ==========================================
class NumberedCanvas(canvas.Canvas):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        if self._pageNumber == 1:
            return
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#718096"))
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(30, 40, letter[0] - 30, 40)

        texto_pagina = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(letter[0] - 30, 25, texto_pagina)
        self.drawString(
            30, 25, "Plano de Ação Executivo para o IEG-M — Controle Interno"
        )
        self.restoreState()


def gerar_pdf_relatorio(df_dados, ano_selecionado):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=30,
        leftMargin=30,
        topMargin=40,
        bottomMargin=50,
    )
    story = []

    styles = getSampleStyleSheet()

    style_capa_titulo = ParagraphStyle(
        "CapaTitulo",
        parent=styles["Heading1"],
        fontSize=26,
        leading=32,
        textColor=colors.HexColor("#1A365D"),
        alignment=1,
        spaceBefore=20,
    )
    style_capa_sub = ParagraphStyle(
        "CapaSub",
        parent=styles["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#4A5568"),
        alignment=1,
        spaceBefore=15,
    )
    style_capa_meta = ParagraphStyle(
        "CapaMeta",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.gray,
        alignment=1,
        spaceBefore=180,
    )

    style_h1 = ParagraphStyle(
        "H1PDF",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1A365D"),
        spaceBefore=10,
        spaceAfter=15,
    )
    style_dimensao = ParagraphStyle(
        "DimPDF",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=18,
        spaceAfter=10,
    )
    style_sumario_item = ParagraphStyle(
        "SumItem",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=6,
    )

    style_texto_bold = ParagraphStyle(
        "TxtBold",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        fontName="Helvetica-Bold",
    )
    style_texto_normal = ParagraphStyle(
        "TxtNorm", parent=styles["Normal"], fontSize=8.5, leading=11
    )

    def grafico_pizza_status(df):
        """Cria um gráfico de pizza vetorial para o relatório PDF."""
        contagem = df["status"].fillna("Sem status").astype(str).value_counts()
        desenho = Drawing(500, 230)
        if contagem.empty:
            desenho.add(String(180, 110, "Sem dados de status", fontSize=12))
            return desenho
        pizza = Pie()
        pizza.x = 20
        pizza.y = 20
        pizza.width = 185
        pizza.height = 185
        pizza.data = list(contagem.values)
        pizza.labels = [str(indice) for indice in contagem.index]
        pizza.slices.strokeWidth = 0.8
        cores_pizza = ["#10B981", "#F59E0B", "#EF4444", "#2563EB", "#8B5CF6", "#64748B"]
        for indice in range(len(pizza.data)):
            status = str(contagem.index[indice]).lower()
            cor_status = "#10B981" if status.startswith("verde") else "#F59E0B" if status.startswith("amarelo") else "#EF4444" if status.startswith("vermelho") else cores_pizza[indice % len(cores_pizza)]
            pizza.slices[indice].fillColor = colors.HexColor(cor_status)
            pizza.slices[indice].strokeColor = colors.white
        desenho.add(pizza)
        desenho.add(String(250, 195, "Distribuição por status", fontName="Helvetica-Bold", fontSize=12, fillColor=colors.HexColor("#1A365D")))
        for indice, (status, quantidade) in enumerate(contagem.items()):
            y = 165 - (indice * 24)
            status = str(status).lower()
            cor = colors.HexColor("#10B981" if status.startswith("verde") else "#F59E0B" if status.startswith("amarelo") else "#EF4444" if status.startswith("vermelho") else cores_pizza[indice % len(cores_pizza)])
            desenho.add(String(250, y, "■", fontSize=14, fillColor=cor))
            desenho.add(String(268, y + 1, f"{status}: {quantidade} ação(ões)", fontSize=9, fillColor=colors.HexColor("#2D3748")))
        return desenho

    # 1. CAPA
    story.append(Spacer(1, 20))
    if os.path.exists("iegm.png"):
        try:
            logo = Image("iegm.png", width=380, height=180)
            logo.hAlign = "CENTER"
            story.append(logo)
        except Exception:
            story.append(
                Paragraph(
                    "[Erro ao renderizar arquivo iegm.png]", style_capa_sub
                )
            )
    else:
        story.append(
            Paragraph(
                "<b>[Arquivo iegm.png não localizado na pasta do script]</b>",
                style_capa_sub,
            )
        )

    story.append(Spacer(1, 20))

    titulo_capa_dinamico = (
        f"Plano de Ação — {ano_selecionado}"
        if ano_selecionado != "Todos"
        else "Plano de Ação — Plurianual"
    )

    story.append(Paragraph(titulo_capa_dinamico, style_capa_titulo))
    story.append(
        Paragraph(
            "Relatório Estratégico de Consolidação de Metas e Auditoria IEG-M",
            style_capa_sub,
        )
    )
    story.append(
        Paragraph(
            f"Emitido em: {date.today().strftime('%d/%m/%Y')} | Gestão Municipal"
            " Ativa",
            style_capa_meta,
        )
    )
    story.append(PageBreak())

    # 2. SUMÁRIO
    story.append(Paragraph("SUMÁRIO ANALÍTICO", style_h1))
    story.append(
        Paragraph(
            "Abaixo estão listadas as dimensões de controle do IEG-M avaliadas e"
            " consolidadas neste livrete executivo:",
            style_capa_sub,
        )
    )
    story.append(Spacer(1, 15))

    dimensoes_presentes = sorted(df_dados["dimensao"].dropna().unique())
    for d_item in dimensoes_presentes:
        texto_sumario = (
            f"• Dimensão Temática: <b>{str(d_item).upper()}</b>"
            " ............................................................................................................"
            " Ver Seção Detalhada"
        )
        story.append(Paragraph(texto_sumario, style_sumario_item))

    story.append(PageBreak())

    # 3. PAINEL EXECUTIVO
    story.append(Paragraph("PAINEL EXECUTIVO", style_h1))
    total_acoes = len(df_dados)
    total_dimensoes = len(dimensoes_presentes)
    total_concluidas = int(df_dados["status"].astype(str).str.lower().str.startswith("verde").sum())
    total_pendentes = total_acoes - total_concluidas
    metricas = [
        [Paragraph("<b>AÇÕES</b>", style_texto_bold), Paragraph("<b>DIMENSÕES</b>", style_texto_bold), Paragraph("<b>CONCLUÍDAS</b>", style_texto_bold), Paragraph("<b>EM ACOMPANHAMENTO</b>", style_texto_bold)],
        [Paragraph(f"<font size=20 color='#2563EB'><b>{total_acoes}</b></font>", style_texto_normal), Paragraph(f"<font size=20 color='#8B5CF6'><b>{total_dimensoes}</b></font>", style_texto_normal), Paragraph(f"<font size=20 color='#10B981'><b>{total_concluidas}</b></font>", style_texto_normal), Paragraph(f"<font size=20 color='#F59E0B'><b>{total_pendentes}</b></font>", style_texto_normal)],
    ]
    tabela_metricas = Table(metricas, colWidths=[125, 125, 125, 125])
    tabela_metricas.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(tabela_metricas)
    story.append(Spacer(1, 18))
    story.append(grafico_pizza_status(df_dados))
    story.append(PageBreak())

    # 4. DADOS DAS MATRIZES
    for dim in dimensoes_presentes:
        story.append(Paragraph(f"🏛️ DIMENSÃO: {str(dim).upper()}", style_dimensao))

        df_dim = df_dados[df_dados["dimensao"] == dim]
        for _, row in df_dim.iterrows():
            dt_conc = (
                row["data_conclusao"].strftime("%d/%m/%Y")
                if isinstance(row["data_conclusao"], (date, datetime))
                else "Não definido"
            )

            dados_tabela = [
                [
                    Paragraph("Ação Prática:", style_texto_bold),
                    Paragraph(str(row["acao"]), style_texto_bold),
                    Paragraph("Status:", style_texto_bold),
                    Paragraph(str(row["status"]), style_texto_normal),
                ],
                [
                    Paragraph("Meta Estratégica:", style_texto_bold),
                    Paragraph(
                        str(row["meta_estrategica"]), style_texto_normal
                    ),
                    Paragraph("Responsável:", style_texto_bold),
                    Paragraph(str(row["responsavel"]), style_texto_normal),
                ],
                [
                    Paragraph("Descrição:", style_texto_bold),
                    Paragraph(str(row["descricao_acao"]), style_texto_normal),
                    Paragraph("Prazo Final:", style_texto_bold),
                    Paragraph(dt_conc, style_texto_normal),
                ],
                [
                    Paragraph("Indicadores de Desempenho:", style_texto_bold),
                    Paragraph(str(row.get("indicador_desempenho", "")), style_texto_normal),
                    Paragraph("Metas Mensuráveis:", style_texto_bold),
                    Paragraph(str(row.get("metas_mensuraveis", "")), style_texto_normal),
                ],
                [
                    Paragraph("Resultados Esperados:", style_texto_bold),
                    Paragraph(str(row.get("resultados_esperados", "")), style_texto_normal),
                    Paragraph("Integração PPA/LDO/LOA:", style_texto_bold),
                    Paragraph(str(row.get("integracao_planejamento_municipal", "")), style_texto_normal),
                ],
                [
                    Paragraph("Alinhamento ODS:", style_texto_bold),
                    Paragraph(str(row.get("alinhamento_ods", "")), style_texto_normal),
                    Paragraph("Forma de execução:", style_texto_bold),
                    Paragraph(str(row.get("forma_execucao", "")), style_texto_normal),
                ],
            ]

            t = Table(dados_tabela, colWidths=[110, 170, 80, 190])
            t.setStyle(
                TableStyle([
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        colors.HexColor("#F7FAFC"),
                    ),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                    (
                        "INNERGRID",
                        (0, 0),
                        (-1, -1),
                        0.5,
                        colors.HexColor("#E2E8F0"),
                    ),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ])
            )
            story.append(t)
            story.append(Spacer(1, 10))

        story.append(Spacer(1, 10))

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer



# ==========================================
# PAINEL NICEGUI — PLANO DE AÇÃO
# ==========================================
COLUNAS = [
    "id", "dimensao", "meta_estrategica", "indicador_desempenho",
    "metas_mensuraveis", "acao", "descricao_acao", "meta", "resultados_esperados",
    "integracao_planejamento_municipal", "alinhamento_ods", "data_inicio",
    "data_conclusao", "responsavel", "forma_execucao", "evidencias", "status",
]
DIMENSOES = ["i-Gov TI", "i-Educ", "i-Saúde", "i-Plan", "i-Amb", "i-Cidade", "i-Fiscal"]
STATUS = ["Verde - atendido", "Amarelo - em análise", "Vermelho - pendente"]


def _cor_status(status):
    valor = _texto(status).lower()
    if valor.startswith("verde"):
        return "#16A34A"
    if valor.startswith("amarelo"):
        return "#D97706"
    if valor.startswith("vermelho"):
        return "#DC2626"
    return "#64748B"


def _normalizar_status(status):
    valor = _texto(status).strip().lower()
    if valor in {"concluída", "concluida", "atendido", "verde - atendido"}:
        return "Verde - atendido"
    if valor in {"atrasada", "cancelada", "pendente", "vermelho - pendente"}:
        return "Vermelho - pendente"
    if valor in {"planejada", "em andamento", "em análise", "amarelo - em análise"}:
        return "Amarelo - em análise"
    return status or "Amarelo - em análise"


def _texto(v):
    if v is None:
        return ""
    return str(v)


def _data(v):
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    return _texto(v)[:10] if _texto(v) else ""


def _valor_data(v):
    return v if v else None


def _dados_formulario(campos):
    return {k: _valor_data(c.value) for k, c in campos.items()}


def _notificar_erro(mensagem):
    logger.error(mensagem)
    ui.notify(mensagem, type="negative")


def _gerar_baixar_pdf(registros, ano):
    if not registros:
        ui.notify("Não há ações para gerar o relatório.", type="warning")
        return
    df = pd.DataFrame(registros)
    pdf = gerar_pdf_relatorio(df, ano)
    caminho = f"/tmp/plano_acao_iegm_{ano}.pdf"
    with open(caminho, "wb") as arq:
        arq.write(pdf.getvalue() if hasattr(pdf, "getvalue") else pdf)
    ui.download(caminho)
    ui.notify("Relatório PDF gerado.", type="positive")


def _opcoes_pizza_status(registros):
    contagem = {}
    for registro in registros:
        status = _texto(registro.get("status")) or "Sem status"
        contagem[status] = contagem.get(status, 0) + 1
    cores_status = ["#2563EB", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#64748B"]
    return {
        "tooltip": {"trigger": "item"},
        "legend": {"bottom": 0},
        "series": [{
            "name": "Ações",
            "type": "pie",
            "radius": ["35%", "70%"],
            "avoidLabelOverlap": True,
            "label": {"show": True, "formatter": "{b}: {c}"},
            "data": [
                {"value": quantidade, "name": status,
                 "itemStyle": {"color": _cor_status(status) if _cor_status(status) != "#64748B" else cores_status[indice % len(cores_status)]}}
                for indice, (status, quantidade) in enumerate(contagem.items())
            ],
        }],
    }


def _formulario_acao(on_save, registro=None):
    registro = registro or {}
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-5xl"):
        ui.label("Editar ação" if registro else "Nova ação").classes("text-xl font-bold text-blue-900")
        with ui.scroll_area().classes("w-full").style("max-height: 70vh"):
            with ui.grid(columns=2).classes("w-full gap-3"):
                campos = {}
                def campo(chave, rotulo, area=False):
                    comp = ui.textarea(rotulo, value=_texto(registro.get(chave))) if area else ui.input(rotulo, value=_texto(registro.get(chave)))
                    return comp.classes("w-full")
                campos["dimensao"] = ui.select(DIMENSOES, value=registro.get("dimensao") or DIMENSOES[0], label="Dimensão").classes("w-full")
                campos["status"] = ui.select(STATUS, value=registro.get("status") or STATUS[0], label="Status").classes("w-full")
                campos["meta_estrategica"] = campo("meta_estrategica", "Meta estratégica")
                campos["indicador_desempenho"] = campo("indicador_desempenho", "Indicadores de desempenho")
                campos["metas_mensuraveis"] = campo("metas_mensuraveis", "Metas mensuráveis", area=True)
                campos["acao"] = campo("acao", "Título da ação")
                campos["meta"] = campo("meta", "Meta alvo")
                campos["resultados_esperados"] = campo("resultados_esperados", "Resultados esperados")
                campos["responsavel"] = campo("responsavel", "Responsável").props("maxlength=150")
                campos["data_inicio"] = ui.input("Data de início", value=_data(registro.get("data_inicio"))).props("type=date").classes("w-full")
                campos["data_conclusao"] = ui.input("Data de conclusão", value=_data(registro.get("data_conclusao"))).props("type=date").classes("w-full")
                campos["alinhamento_ods"] = campo("alinhamento_ods", "Alinhamento ODS")
                campos["integracao_planejamento_municipal"] = campo(
                    "integracao_planejamento_municipal",
                    "Integração das ações ao PPA, LDO e LOA",
                ).props("maxlength=100")
                campos["forma_execucao"] = campo("forma_execucao", "Forma de execução")
                campos["evidencias"] = campo("evidencias", "Evidências")
                campos["descricao_acao"] = campo("descricao_acao", "Descrição detalhada", area=True)
            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Cancelar", on_click=dialog.close).props("flat")
                def salvar():
                    dados = _dados_formulario(campos)
                    ok = atualizar_acao_banco(registro["id"], dados) if registro else inserir_acao_banco(dados)
                    if ok:
                        dialog.close()
                        ui.notify("Ação salva com sucesso.", type="positive")
                        on_save()
                    else:
                        detalhe = obter_ultimo_erro_banco()
                        _notificar_erro(detalhe or "Não foi possível salvar a ação.")
                ui.button("Salvar", on_click=salvar).classes("bg-blue-700 text-white")
    dialog.open()


def mostrar_formulario_plano_acao(ano_sel=None):
    estado = {"dados": [], "dim": "Todas", "status": "Todos", "ano": "Todos"}
    anos = ["Todos", 2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ui.label("Plano de Ação IEG-M").classes("w-full text-center text-2xl font-bold text-blue-900")
    ui.label("Cadastro, acompanhamento e relatório das ações estratégicas").classes("w-full text-center text-gray-600 mb-4")

    @ui.refreshable
    def painel():
        try:
            estado["dados"] = carregar_dados_banco()
        except Exception as exc:
            estado["dados"] = []
            logger.exception("Erro ao carregar plano de ação: %s", exc)
        dados = estado["dados"]
        filtrados = [r for r in dados if (estado["dim"] == "Todas" or r.get("dimensao") == estado["dim"])
                     and (estado["status"] == "Todos" or r.get("status") == estado["status"])
                     and (estado["ano"] == "Todos" or str(_data(r.get("data_conclusao")))[:4] == str(estado["ano"]))]
        concluidas = sum(1 for r in filtrados if _texto(r.get("status")).lower().startswith("verde"))
        atrasadas = sum(1 for r in filtrados if _texto(r.get("status")).lower().startswith("vermelho"))
        em_andamento = sum(1 for r in filtrados if _texto(r.get("status")).lower().startswith("amarelo"))
        with ui.grid(columns=4).classes("w-full gap-3 mt-2"):
            for titulo, valor, cor in [
                ("Total de ações", len(filtrados), "#2563EB"),
                ("Concluídas", concluidas, "#10B981"),
                ("Em andamento", em_andamento, "#F59E0B"),
                ("Atrasadas", atrasadas, "#EF4444"),
            ]:
                with ui.card().classes("p-4 border-l-4 shadow-sm").style(f"border-left-color: {cor}"):
                    ui.label(titulo).classes("text-sm text-gray-500")
                    ui.label(str(valor)).classes("text-3xl font-black").style(f"color: {cor}")
        with ui.row().classes("w-full justify-between items-center"):
            ui.label(f"{len(filtrados)} ação(ões) encontrada(s)").classes("font-bold")
            with ui.row().classes("gap-2"):
                ui.button("Nova ação", on_click=lambda: _formulario_acao(painel.refresh)).classes("bg-blue-700 text-white")
                ui.button("Baixar PDF", on_click=lambda: _gerar_baixar_pdf(filtrados, estado["ano"])).props("outline")
        with ui.row().classes("w-full gap-3 mt-3"):
            ui.select(["Todas"] + DIMENSOES, value=estado["dim"], label="Dimensão", on_change=lambda e: (estado.update(dim=e.value), painel.refresh())).classes("w-1/3")
            ui.select(["Todos"] + STATUS, value=estado["status"], label="Status", on_change=lambda e: (estado.update(status=e.value), painel.refresh())).classes("w-1/3")
            ui.select(anos, value=estado["ano"], label="Ano de conclusão", on_change=lambda e: (estado.update(ano=e.value), painel.refresh())).classes("w-1/3")
        if not filtrados:
            ui.label("Nenhuma ação localizada para os filtros selecionados.").classes("text-gray-600 mt-6")
            return
        with ui.card().classes("w-full mt-4 border shadow-sm"):
            ui.label("Distribuição das ações por status").classes("text-lg font-bold text-blue-900")
            ui.echart(_opcoes_pizza_status(filtrados)).classes("w-full").style("height: 300px")
        for registro in filtrados:
            with ui.card().classes("w-full mt-3 border"):
                with ui.row().classes("w-full justify-between items-start"):
                    ui.label(f"#{registro.get('id')} — {_texto(registro.get('acao')) or 'Ação sem título'}").classes("text-lg font-bold text-blue-900")
                    ui.badge(
                        _texto(registro.get("status")) or "Sem status"
                    ).style(
                        f"background-color: {_cor_status(registro.get('status'))}; color: white;"
                    )
                with ui.grid(columns=2).classes("w-full gap-2 mt-2"):
                    ui.label(f"Dimensão: {_texto(registro.get('dimensao'))}")
                    ui.label(f"Responsável: {_texto(registro.get('responsavel')) or 'Não definido'}")
                    ui.label(f"Meta: {_texto(registro.get('meta')) or 'Não definida'}")
                    ui.label(f"Prazo: {_data(registro.get('data_conclusao')) or 'Não definido'}")
                ui.label(_texto(registro.get("descricao_acao"))).classes("text-gray-700 mt-2")
                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Editar", on_click=lambda r=registro: _formulario_acao(painel.refresh, r)).props("outline dense")
                    def excluir(r=registro):
                        if deletar_acao_banco(r["id"]):
                            ui.notify("Ação excluída.", type="positive")
                            painel.refresh()
                        else:
                            _notificar_erro("Não foi possível excluir a ação.")
                    ui.button("Excluir", on_click=excluir).props("outline dense color=negative")

    painel()


def container_formulario_plano_acao(ano_sel=None):
    return mostrar_formulario_plano_acao(ano_sel)
