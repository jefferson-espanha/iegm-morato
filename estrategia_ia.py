from datetime import date
import io
import json
import logging
import os
from typing import List, Optional

from google import genai
from google.genai import types
from nicegui import run, ui
from pydantic import BaseModel, Field

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

# String de conexão integrada (prioriza variável de ambiente se existir)
DEFAULT_NEON_URL = (
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@"
    "ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/"
    "neondb?sslmode=require"
)

DIMENSOES_IEGM = [
    "i-Gov TI",
    "i-Educ",
    "i-Saúde",
    "i-Plan",
    "i-Amb",
    "i-Cidade",
    "i-Fiscal",
]


# ==============================================================================
# 1. SCHEMAS PYDANTIC (SAÍDA ESTRUTURADA DA IA)
# ==============================================================================
class EstrategiaQuestao(BaseModel):
    quesito_codigo: str = Field(
        description="Código exato do quesito no relatório (ex: 3.3, 8.2.2, 9.1)."
    )
    quesito_pergunta: str = Field(
        description="Texto completo da pergunta extraído do Anexo do relatório."
    )
    situacao_atual: str = Field(
        description="Nota obtida, eficiência e resposta atual registrada."
    )
    diagnostico_tecnico: str = Field(
        description="Causa raiz da perda de pontos ou reincidência."
    )
    estrategia_resolucao: str = Field(
        description="Estratégia institucional e técnica para sanar a lacuna."
    )
    etapas_execucao: str = Field(
        description="Passo a passo operacional (5W2H resumido) para implementação."
    )
    setor_responsavel: str = Field(
        description="Secretaria, diretoria ou equipe responsável."
    )
    evidencia_comprobatoria: str = Field(
        description="Documento exato exigido pelo TCE-SP para pontuar no próximo ciclo."
    )
    impacto_pontos: float = Field(
        description="Quantidade de pontos possíveis de recuperar neste quesito."
    )
    prioridade: str = Field(
        description="Alta, Média ou Baixa."
    )


class AnaliseCompletaRelatorio(BaseModel):
    municipio: str = Field(
        description="Nome do município identificado no relatório."
    )
    dimensao: str = Field(
        description="Dimensão avaliada (ex: i-Gov TI)."
    )
    exercicio: int = Field(
        description="Ano/exercício do relatório analisado."
    )
    pontuacao_atual: float = Field(
        description="Pontuação total obtida no exercício."
    )
    faixa_atual: str = Field(
        description="Conceito/faixa obtida (ex: B+, A, B, C)."
    )
    resumo_executivo: str = Field(
        description="Síntese gerencial dos pontos críticos e potencial de evolução de faixa."
    )
    estrategias: List[EstrategiaQuestao] = Field(
        description="Lista de estratégias ordenadas por maior impacto de pontos."
    )


# ==============================================================================
# 2. CONEXÃO E PERSISTÊNCIA NO NEON POSTGRESQL
# ==============================================================================
def _conectar_banco():
    if psycopg2 is None:
        raise RuntimeError(
            "O pacote 'psycopg2-binary' não está instalado. Execute: pip install psycopg2-binary"
        )

    db_url = (
        os.getenv("NEON_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_NEON_URL
    )
    return psycopg2.connect(db_url)


def inicializar_tabelas_ia():
    conn = _conectar_banco()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ia_analises_iegm (
                    id SERIAL PRIMARY KEY,
                    municipio VARCHAR(150),
                    dimensao VARCHAR(50),
                    exercicio INTEGER,
                    pontuacao_atual NUMERIC(10, 2),
                    faixa_atual VARCHAR(20),
                    resumo_executivo TEXT,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ia_estrategias_questoes (
                    id SERIAL PRIMARY KEY,
                    analise_id INTEGER REFERENCES ia_analises_iegm(id) ON DELETE CASCADE,
                    quesito_codigo VARCHAR(30),
                    quesito_pergunta TEXT,
                    situacao_atual TEXT,
                    diagnostico_tecnico TEXT,
                    estrategia_resolucao TEXT,
                    etapas_execucao TEXT,
                    setor_responsavel VARCHAR(150),
                    evidencia_comprobatoria TEXT,
                    impacto_pontos NUMERIC(10, 2),
                    prioridade VARCHAR(20),
                    status_execucao VARCHAR(40) DEFAULT 'Pendente'
                );
            """)
        conn.commit()
    finally:
        conn.close()


def salvar_analise_no_banco(analise: AnaliseCompletaRelatorio) -> int:
    inicializar_tabelas_ia()
    conn = _conectar_banco()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ia_analises_iegm (
                    municipio, dimensao, exercicio, pontuacao_atual, faixa_atual, resumo_executivo
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    analise.municipio[:150],
                    analise.dimensao[:50],
                    analise.exercicio,
                    analise.pontuacao_atual,
                    analise.faixa_atual[:20],
                    analise.resumo_executivo,
                ),
            )
            analise_id = cur.fetchone()[0]

            for est in analise.estrategias:
                cur.execute(
                    """
                    INSERT INTO ia_estrategias_questoes (
                        analise_id, quesito_codigo, quesito_pergunta, situacao_atual,
                        diagnostico_tecnico, estrategia_resolucao, etapas_execucao,
                        setor_responsavel, evidencia_comprobatoria, impacto_pontos, prioridade
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        analise_id,
                        est.quesito_codigo[:30],
                        est.quesito_pergunta,
                        est.situacao_atual,
                        est.diagnostico_tecnico,
                        est.estrategia_resolucao,
                        est.etapas_execucao,
                        est.setor_responsavel[:150],
                        est.evidencia_comprobatoria,
                        est.impacto_pontos,
                        est.prioridade[:20],
                    ),
                )
        conn.commit()
        return analise_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def listar_analises_salvas() -> List[dict]:
    inicializar_tabelas_ia()
    conn = _conectar_banco()
    try:
        cursor_factory = RealDictCursor if RealDictCursor else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            cur.execute("""
                SELECT a.*, COUNT(e.id) AS total_questoes,
                       COALESCE(SUM(e.impacto_pontos), 0) AS pontos_recuperaveis
                FROM ia_analises_iegm a
                LEFT JOIN ia_estrategias_questoes e ON e.analise_id = a.id
                GROUP BY a.id
                ORDER BY a.id DESC;
            """)
            rows = cur.fetchall()
            if rows and isinstance(rows[0], dict):
                return [dict(r) for r in rows]
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def carregar_estrategias_da_analise(analise_id: int) -> List[dict]:
    conn = _conectar_banco()
    try:
        cursor_factory = RealDictCursor if RealDictCursor else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            cur.execute(
                """
                SELECT * FROM ia_estrategias_questoes
                WHERE analise_id = %s
                ORDER BY impacto_pontos DESC, quesito_codigo ASC;
                """,
                (analise_id,),
            )
            rows = cur.fetchall()
            if rows and isinstance(rows[0], dict):
                return [dict(r) for r in rows]
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def atualizar_status_questao(questao_id: int, novo_status: str):
    conn = _conectar_banco()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ia_estrategias_questoes SET status_execucao = %s WHERE id = %s;",
                (novo_status, questao_id),
            )
        conn.commit()
    finally:
        conn.close()


def excluir_analise(analise_id: int):
    conn = _conectar_banco()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ia_analises_iegm WHERE id = %s;", (analise_id,))
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# 3. MOTOR DE INTELIGÊNCIA ARTIFICIAL (GEMINI MULTIMODAL)
# ==============================================================================
def analisar_pdf_com_ia(pdf_bytes: bytes, dimensao_hint: str) -> AnaliseCompletaRelatorio:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Variável de ambiente GEMINI_API_KEY não encontrada. Configure-a antes de analisar."
        )

    client = genai.Client(api_key=api_key)

    prompt = f"""
    Você é um Consultor Sênior Especialista em Governança Pública Municipal e Auditoria do IEG-M (TCE-SP).
    Analise integralmente o PDF do relatório anexado (Dimensão sugerida: {dimensao_hint}).

    TAREFAS OBRIGATÓRIAS:
    1. Extraia o cabeçalho: Município, Dimensão, Exercício, Pontuação Obtida e Faixa/Conceito.
    2. Varra todo o relatório em busca de:
       - Quesitos na seção "Pontos Fracos" ou com eficiência inferior a 100%;
       - Quesitos listados no "Diagnóstico de Reincidências";
       - Quesitos com penalidades aplicadas ou riscos não mitigados;
       - Metas da Agenda 2030 (ODS) com status "Não Atendido";
       - Perguntas no "Anexo - Questionário Completo" respondidas como "Não", parcialmente atendidas ou "Não informado".
    3. Cruze cada código de quesito com o enunciado exato do Anexo para não perder contexto.
    4. Calcule o impacto em pontos (Teto - Nota obtida) e ordene da maior perda para a menor.
    5. Para cada questão crítica, elabore:
       - Diagnóstico técnico objetivo;
       - Estratégia institucional viável para prefeitura;
       - Etapas práticas de execução;
       - Setor responsável;
       - Evidência documental exata que o TCE-SP exige para validar a resposta no próximo exercício.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AnaliseCompletaRelatorio,
            temperature=0.2,
        ),
    )

    return AnaliseCompletaRelatorio(**json.loads(response.text))


# ==============================================================================
# 4. GERADOR DE PDF EXECUTIVO DO CADERNO DE ESTRATÉGIAS
# ==============================================================================
class RodapeCanvas(canvas.Canvas):
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
            self._desenhar_rodape(num_pages)
            super().showPage()
        super().save()

    def _desenhar_rodape(self, total_paginas):
        self.saveState()
        self.setFont("Helvetica", 8.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(32, 36, letter[0] - 32, 36)
        self.drawString(32, 22, "Caderno de Estratégias IEG-M — Diagnóstico Assistido por IA")
        self.drawRightString(letter[0] - 32, 22, f"Página {self._pageNumber} de {total_paginas}")
        self.restoreState()


def gerar_pdf_estrategias(analise: dict, questoes: List[dict]) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=32,
        leftMargin=32,
        topMargin=40,
        bottomMargin=50,
    )
    styles = getSampleStyleSheet()
    story = []

    st_titulo = ParagraphStyle(
        "TituloIA",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=8,
    )
    st_sub = ParagraphStyle(
        "SubIA",
        parent=styles["Normal"],
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#475569"),
        spaceAfter=16,
    )
    st_h2 = ParagraphStyle(
        "H2IA",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=14,
        spaceAfter=8,
    )
    st_bold = ParagraphStyle(
        "B", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold"
    )
    st_norm = ParagraphStyle("N", parent=styles["Normal"], fontSize=8.5, leading=11)
    st_white = ParagraphStyle(
        "W",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=12,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )

    story.append(
        Paragraph(f"Caderno de Estratégias IEG-M — {analise.get('dimensao', '')}", st_titulo)
    )
    story.append(
        Paragraph(
            f"Município: <b>{analise.get('municipio', '')}</b> | Exercício Base: <b>{analise.get('exercicio', '')}</b> | "
            f"Nota Atual: <b>{analise.get('pontuacao_atual', 0)} pts (Faixa {analise.get('faixa_atual', '-')})</b> | "
            f"Emitido em: {date.today().strftime('%d/%m/%Y')}",
            st_sub,
        )
    )

    story.append(Paragraph("Diagnóstico Executivo (IA)", st_h2))
    story.append(Paragraph(str(analise.get("resumo_executivo", "")), st_norm))
    story.append(Spacer(1, 14))

    story.append(
        Paragraph(f"Estratégias por Quesito Crítico ({len(questoes)} questões mapeadas)", st_h2)
    )

    for q in questoes:
        cabecalho = Table(
            [
                [
                    Paragraph(
                        f"QUESITO {q['quesito_codigo']} — Prioridade {q['prioridade']} (+{q['impacto_pontos']} pts)",
                        st_white,
                    ),
                    Paragraph(f"Status: {q.get('status_execucao', 'Pendente')}", st_white),
                ]
            ],
            colWidths=[400, 148],
        )
        cabecalho.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E3A8A")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(cabecalho)

        corpo = Table(
            [
                [
                    Paragraph("Pergunta do Questionário:", st_bold),
                    Paragraph(str(q["quesito_pergunta"]), st_norm),
                ],
                [
                    Paragraph("Situação Atual / Nota:", st_bold),
                    Paragraph(str(q["situacao_atual"]), st_norm),
                ],
                [
                    Paragraph("Diagnóstico Técnico:", st_bold),
                    Paragraph(str(q["diagnostico_tecnico"]), st_norm),
                ],
                [
                    Paragraph("Estratégia Recomendada:", st_bold),
                    Paragraph(str(q["estrategia_resolucao"]), st_norm),
                ],
                [
                    Paragraph("Etapas de Execução:", st_bold),
                    Paragraph(str(q["etapas_execucao"]), st_norm),
                ],
                [
                    Paragraph("Responsável:", st_bold),
                    Paragraph(str(q["setor_responsavel"]), st_norm),
                ],
                [
                    Paragraph("Evidência Exigida (TCE):", st_bold),
                    Paragraph(str(q["evidencia_comprobatoria"]), st_norm),
                ],
            ],
            colWidths=[135, 413],
        )
        corpo.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
                    ("BACKGROUND", (1, 0), (1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(corpo)
        story.append(Spacer(1, 12))

    doc.build(story, canvasmaker=RodapeCanvas)
    buffer.seek(0)
    return buffer


# ==============================================================================
# 5. INTERFACE NICEGUI AUTÔNOMA (PÁGINA DEDICADA /ia-estrategias)
# ==============================================================================
@ui.page("/ia-estrategias")
def pagina_ia_estrategias():
    try:
        inicializar_tabelas_ia()
        banco_ok = True
        erro_banco_msg = ""
    except Exception as exc:
        logger.exception("Falha ao conectar no Neon PostgreSQL")
        banco_ok = False
        erro_banco_msg = str(exc)

    estado = {"pdf_bytes": None, "analise_selecionada_id": None}

    with ui.header().classes("bg-slate-900 text-white px-6 py-3 items-center justify-between"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("psychology", size="md").classes("text-emerald-400")
            ui.label("Inteligência Estratégica IEG-M").classes("text-lg font-bold")
        with ui.row().classes("items-center gap-2"):
            ui.badge(
                "Neon DB Conectado" if banco_ok else "Erro de Banco",
                color="emerald" if banco_ok else "red",
            )

    if not banco_ok:
        with ui.card().classes("max-w-4xl mx-auto mt-8 p-6 bg-red-50 border-l-4 border-red-600"):
            ui.label("Erro ao conectar ao Neon PostgreSQL").classes("text-lg font-bold text-red-900")
            ui.label(erro_banco_msg).classes("text-sm text-red-700 mt-1")
        return

    with ui.row().classes("w-full max-w-7xl mx-auto p-6 gap-6 no-wrap items-start"):
        # COLUNA ESQUERDA: UPLOAD E HISTÓRICO DE ANÁLISES
        with ui.column().classes("w-1/3 gap-4"):
            with ui.card().classes("w-full p-4 border-t-4 border-emerald-600"):
                ui.label("1. Novo Diagnóstico por IA").classes("text-base font-bold text-slate-800")
                ui.label(
                    "Envie o PDF exportado pelo sistema para mapear falhas e gerar estratégias."
                ).classes("text-xs text-slate-500 mb-2")

                sel_dim = ui.select(
                    DIMENSOES_IEGM, value="i-Gov TI", label="Dimensão do Relatório"
                ).classes("w-full")

                def ao_carregar_pdf(e):
                    estado["pdf_bytes"] = e.content.read()
                    btn_processar.enable()
                    ui.notify(f"PDF '{e.name}' carregado.", type="info")

                ui.upload(
                    label="Selecionar Relatório PDF",
                    auto_upload=True,
                    on_upload=ao_carregar_pdf,
                ).props("accept=.pdf max-file-size=20971520").classes("w-full")

                async def rodar_ia():
                    if not estado["pdf_bytes"]:
                        return
                    btn_processar.disable()
                    spinner_box.visible = True
                    try:
                        resultado = await run.io_bound(
                            analisar_pdf_com_ia,
                            estado["pdf_bytes"],
                            sel_dim.value,
                        )
                        novo_id = await run.io_bound(salvar_analise_no_banco, resultado)
                        estado["analise_selecionada_id"] = novo_id
                        ui.notify("Análise concluída e gravada no Neon DB!", type="positive")
                        atualizar_lista_historico()
                        renderizar_detalhes_analise(novo_id)
                    except Exception as exc:
                        logger.exception("Erro no processamento IA")
                        ui.notify(f"Erro ao analisar relatório: {exc}", type="negative")
                    finally:
                        spinner_box.visible = False
                        btn_processar.enable()

                with ui.row().classes("w-full items-center gap-2 mt-2") as spinner_box:
                    ui.spinner(size="sm")
                    ui.label("Lendo questionário e cruzando evidências...").classes(
                        "text-xs text-blue-700"
                    )
                spinner_box.visible = False

                btn_processar = ui.button(
                    "Gerar Estratégias com IA",
                    icon="auto_awesome",
                    on_click=rodar_ia,
                ).props("color=emerald").classes("w-full mt-2")
                btn_processar.disable()

            # HISTÓRICO DE DIAGNÓSTICOS SALVOS NO NEON
            with ui.card().classes("w-full p-4"):
                ui.label("2. Diagnósticos Salvos (Neon)").classes(
                    "text-base font-bold text-slate-800 mb-2"
                )
                lista_historico = ui.column().classes("w-full gap-2")

        # COLUNA DIREITA: PAINEL DE ESTRATÉGIAS POR QUESTÃO
        with ui.column().classes("w-2/3"):
            painel_detalhes = ui.column().classes("w-full gap-4")

    def atualizar_lista_historico():
        lista_historico.clear()
        analises = listar_analises_salvas()
        with lista_historico:
            if not analises:
                ui.label("Nenhum relatório analisado ainda.").classes("text-xs text-slate-400")
                return
            for a in analises:
                ativo = a["id"] == estado["analise_selecionada_id"]
                borda = (
                    "border-blue-600 bg-blue-50"
                    if ativo
                    else "border-slate-200 hover:bg-slate-50"
                )
                with ui.card().classes(f"w-full p-3 cursor-pointer border {borda}").on(
                    "click", lambda _, aid=a["id"]: selecionar_analise(aid)
                ):
                    with ui.row().classes("w-full justify-between items-center"):
                        ui.label(f"{a['dimensao']} ({a['exercicio']})").classes(
                            "font-bold text-sm text-slate-800"
                        )
                        ui.badge(f"Faixa {a['faixa_atual']}", color="blue")
                    ui.label(f"{a['municipio']} — {a['pontuacao_atual']} pts").classes(
                        "text-xs text-slate-600"
                    )
                    ui.label(
                        f"{a['total_questoes']} questões críticas (+{a['pontos_recuperaveis']} pts recuperáveis)"
                    ).classes("text-xs font-medium text-emerald-700 mt-1")

    def selecionar_analise(analise_id: Optional[int]):
        estado["analise_selecionada_id"] = analise_id
        atualizar_lista_historico()
        renderizar_detalhes_analise(analise_id)

    def renderizar_detalhes_analise(analise_id: Optional[int]):
        painel_detalhes.clear()
        analises = listar_analises_salvas()
        analise = next((a for a in analises if a["id"] == analise_id), None)

        with painel_detalhes:
            if not analise:
                with ui.card().classes("w-full p-8 items-center text-center bg-slate-50"):
                    ui.icon("analytics", size="xl").classes("text-slate-400")
                    ui.label(
                        "Envie um relatório PDF ao lado ou selecione um diagnóstico salvo."
                    ).classes("text-slate-500 mt-2")
                return

            questoes = carregar_estrategias_da_analise(analise_id)

            # Cabeçalho do Diagnóstico
            with ui.card().classes("w-full p-5 bg-white border-l-4 border-blue-800"):
                with ui.row().classes("w-full justify-between items-start"):
                    with ui.column().classes("gap-1"):
                        ui.label(
                            f"{analise['municipio']} — {analise['dimensao']} ({analise['exercicio']})"
                        ).classes("text-xl font-bold text-slate-900")
                        ui.label(
                            f"Pontuação Atual: {analise['pontuacao_atual']} pts | Faixa: {analise['faixa_atual']} | "
                            f"Potencial de Ganho: +{analise['pontos_recuperaveis']} pts"
                        ).classes("text-sm font-semibold text-emerald-700")

                    with ui.row().classes("gap-2"):

                        def baixar_pdf_atual():
                            buf = gerar_pdf_estrategias(analise, questoes)
                            caminho = f"/tmp/estrategias_iegm_{analise['id']}.pdf"
                            with open(caminho, "wb") as f:
                                f.write(buf.getvalue())
                            ui.download(caminho)

                        ui.button(
                            "Exportar Caderno PDF",
                            icon="picture_as_pdf",
                            on_click=baixar_pdf_atual,
                        ).props("color=primary")
                        ui.button(
                            icon="delete",
                            on_click=lambda: (
                                excluir_analise(analise_id),
                                selecionar_analise(None),
                                ui.notify("Diagnóstico removido.", type="warning"),
                            ),
                        ).props("flat color=negative")

                ui.separator().classes("my-3")
                ui.label("Síntese Executiva da IA").classes(
                    "text-xs font-bold uppercase text-slate-500"
                )
                ui.label(analise["resumo_executivo"]).classes("text-sm text-slate-700 mt-1")

            # Cards individuais por questão
            for q in questoes:
                cor_prio = (
                    "red"
                    if q["prioridade"] == "Alta"
                    else ("amber" if q["prioridade"] == "Média" else "blue")
                )
                with ui.card().classes("w-full p-4 border border-slate-200"):
                    with ui.row().classes("w-full justify-between items-center"):
                        with ui.row().classes("items-center gap-2"):
                            ui.badge(f"Quesito {q['quesito_codigo']}", color="slate-800")
                            ui.badge(f"Prioridade {q['prioridade']}", color=cor_prio)
                            ui.badge(f"+{q['impacto_pontos']} pts", color="emerald")

                        ui.select(
                            ["Pendente", "Em Execução", "Evidência Pronta", "Concluído"],
                            value=q.get("status_execucao") or "Pendente",
                            on_change=lambda e, qid=q["id"]: (
                                atualizar_status_questao(qid, e.value),
                                ui.notify("Status atualizado no Neon DB.", type="positive"),
                            ),
                        ).props("dense outlined").classes("w-44")

                    ui.label(q["quesito_pergunta"]).classes(
                        "font-bold text-sm text-slate-900 mt-2"
                    )
                    ui.label(f"Situação atual: {q['situacao_atual']}").classes(
                        "text-xs text-rose-700 font-medium"
                    )

                    with ui.grid(columns=2).classes("w-full gap-4 mt-3 bg-slate-50 p-3 rounded"):
                        with ui.column().classes("gap-1"):
                            ui.label("Diagnóstico Técnico").classes(
                                "text-xs font-bold text-slate-600"
                            )
                            ui.label(q["diagnostico_tecnico"]).classes("text-xs text-slate-800")

                            ui.label("Estratégia Recomendada").classes(
                                "text-xs font-bold text-blue-800 mt-2"
                            )
                            ui.label(q["estrategia_resolucao"]).classes("text-xs text-slate-800")

                        with ui.column().classes("gap-1"):
                            ui.label("Etapas de Execução").classes(
                                "text-xs font-bold text-slate-600"
                            )
                            ui.label(q["etapas_execucao"]).classes("text-xs text-slate-800")

                            ui.label("Evidência Comprobatória (TCE-SP)").classes(
                                "text-xs font-bold text-emerald-800 mt-2"
                            )
                            ui.label(q["evidencia_comprobatoria"]).classes(
                                "text-xs text-slate-800"
                            )

                    ui.label(f"Setor Responsável: {q['setor_responsavel']}").classes(
                        "text-xs font-semibold text-slate-600 mt-2"
                    )

    # Inicialização da tela
    analises_iniciais = listar_analises_salvas()
    if analises_iniciais:
        estado["analise_selecionada_id"] = analises_iniciais[0]["id"]
    atualizar_lista_historico()
    renderizar_detalhes_analise(estado["analise_selecionada_id"])


# Permite rodar o arquivo diretamente para teste isolado
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Inteligência Estratégica IEG-M", port=8080)
