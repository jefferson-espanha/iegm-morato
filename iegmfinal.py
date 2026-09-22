import logging
import pandas as pd
import plotly.express as px
from nicegui import app, ui

# Importa a conexão do icidade_completo.py
try:
    from icidade_completo import get_connection
except ImportError:
    try:
        from icidade import get_connection
    except ImportError as e:
        logging.error(f"Erro ao importar get_connection: {e}")

        def get_connection():
            raise ImportError("Não foi possível importar 'get_connection'.")


# =============================================================================
# FUNÇÕES DE CONSULTA E BANCO DE DADOS
# =============================================================================


def buscar_pontuacao_dimensao(tabela: str, ano: int) -> float:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                sql = f"SELECT COALESCE(SUM(pontos), 0) FROM {tabela} WHERE ano = %s;"
                cursor.execute(sql, (int(ano),))
                res = cursor.fetchone()

                if res and res[0] is not None:
                    return float(res[0])
    except Exception as e:
        logging.warning(
            f"[IEG-M Final] Erro ao ler tabela '{tabela}' para ano {ano}: {e}"
        )

    return 0.0


def puxar_nota_iplan(ano: int) -> float:
    return buscar_pontuacao_dimensao("respostas_iplan", ano)


def puxar_nota_ifiscal(ano: int) -> float:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                sql = "SELECT pontos FROM respostas_ifiscal WHERE ano = %s;"
                cursor.execute(sql, (int(ano),))
                rows = cursor.fetchall()

                if not rows:
                    return 0.0

                pontos_lista = [float(r[0]) for r in rows if r[0] is not None]

                if any(p <= -100.0 for p in pontos_lista):
                    return 0.0

                total = sum(p for p in pontos_lista if p > -100.0)
                return float(total)
    except Exception as e:
        logging.warning(f"[i-Fiscal] Falha ao ler ano {ano}: {e}")
        return 0.0


def puxar_nota_ieduc(ano: int) -> float:
    return buscar_pontuacao_dimensao("respostas_ieduc", ano)


def puxar_nota_isaude(ano: int) -> float:
    return buscar_pontuacao_dimensao("respostas_isaude", ano)


def puxar_nota_iamb(ano: int) -> float:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                sql = """
                    SELECT COALESCE(SUM(pontos), 0.0) 
                    FROM respostas_iamb 
                    WHERE ano = %s;
                """
                cursor.execute(sql, (int(ano),))
                res = cursor.fetchone()

                if res and res[0] is not None:
                    total_pontos = float(res[0])
                    return float(max(0.0, round(total_pontos, 1)))

    except Exception as e:
        logging.warning(
            f"[i-Amb] Erro ao ler tabela respostas_iamb para o ano {ano}: {e}"
        )

    return 0.0


def puxar_nota_icidade(ano: int) -> float:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                sql_respostas = "SELECT COALESCE(SUM(pontos), 0) FROM respostas WHERE dimensao = 'icidade' AND ano = %s;"
                cursor.execute(sql_respostas, (int(ano),))
                res = cursor.fetchone()

                if res and res[0] is not None and float(res[0]) > 0:
                    return float(res[0])

                sql_icidade = "SELECT COALESCE(SUM(pontos), 0) FROM respostas_icidade WHERE ano = %s;"
                cursor.execute(sql_icidade, (int(ano),))
                res_fallback = cursor.fetchone()

                if res_fallback and res_fallback[0] is not None:
                    return float(res_fallback[0])
    except Exception as e:
        logging.warning(
            f"[i-Cidade] Erro ao consultar pontos para o ano {ano}: {e}"
        )

    return 0.0


def puxar_nota_igov(ano: int) -> float:
    return buscar_pontuacao_dimensao("respostas_igov", ano)


# =============================================================================
# CÁLCULOS OFICIAIS TCESP
# =============================================================================


def calcular_nota_final(
    plan: float,
    fiscal: float,
    educ: float,
    saude: float,
    amb: float,
    cidade: float,
    gov: float,
) -> float:
    try:
        soma = (
            (float(plan) * 0.20)
            + (float(fiscal) * 0.20)
            + (float(educ) * 0.20)
            + (float(saude) * 0.20)
            + (float(amb) * 0.10)
            + (float(cidade) * 0.05)
            + (float(gov) * 0.05)
        )
        return round(soma, 1)
    except Exception:
        return 0.0


def obter_faixa_classificacao(nota: float):
    if nota >= 900:
        return "A (Altamente Efetiva)", "#10B981"
    elif nota >= 750:
        return "B+ (Muito Efetiva)", "#3B82F6"
    elif nota >= 600:
        return "B (Efetiva)", "#F59E0B"
    elif nota >= 500:
        return "C+ (Em Fase de Adequação)", "#F97316"
    else:
        return "C (Baixo Nível de Adequação)", "#EF4444"


# =============================================================================
# PAINEL PRINCIPAL NICEGUI (COMPATÍVEL COM O MAIN.PY)
# =============================================================================


def mostrar_painel_iegm_final(ano_sel=None):
    """Função compatível com a chamada dinâmica do main.py."""
    if ano_sel is None:
        ano_sel = app.storage.user.get("ano_referencia_global", 2026)

    anos_disponiveis = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_estado = {"ano": int(ano_sel) if int(ano_sel) in anos_disponiveis else 2026}

    ui.label("Pontuação do IEG-M - Prévia").classes(
        "w-full text-center text-2xl font-bold italic mb-6 text-blue-900"
    )

    @ui.refreshable
    def render_conteudo():
        ano_atual = ano_estado["ano"]

        # Busca dados do banco de dados para o ano selecionado
        plan = puxar_nota_iplan(ano_atual)
        fiscal = puxar_nota_ifiscal(ano_atual)
        educ = puxar_nota_ieduc(ano_atual)
        saude = puxar_nota_isaude(ano_atual)
        amb = puxar_nota_iamb(ano_atual)
        cidade = puxar_nota_icidade(ano_atual)
        gov = puxar_nota_igov(ano_atual)

        nota_final = calcular_nota_final(
            plan, fiscal, educ, saude, amb, cidade, gov
        )

        with ui.grid(columns=12).classes("w-full gap-6 items-start"):
            # -----------------------------------------------------------------
            # COLUNA DA ESQUERDA: Seletor de Ano + Tabela
            # -----------------------------------------------------------------
            with ui.column().classes("col-span-12 md:col-span-4 w-full gap-2"):
                ui.select(
                    label="Exercício:",
                    options=anos_disponiveis,
                    value=ano_atual,
                    on_change=lambda e: (
                        ano_estado.update({"ano": e.value}),
                        app.storage.user.update({"ano_referencia_global": e.value}),
                        render_conteudo.refresh(),
                    ),
                ).classes("w-full").props("outlined dense")

                # Cabeçalho da Lista Textual
                with ui.row().classes(
                    "w-full justify-between font-bold italic border-b-2 border-gray-700 pb-1 mt-4 text-sm"
                ):
                    ui.label("Dimensão/IEG-M").classes("w-1/2")
                    ui.label("Pontuação").classes("w-1/4 text-center")
                    ui.label("Nota").classes("w-1/4 text-right")

                dimensoes = [
                    ("I-CIDADE", cidade),
                    ("I-GOV TI", gov),
                    ("I-PLAN", plan),
                    ("I-FISCAL", fiscal),
                    ("I-AMB", amb),
                    ("I-EDUC", educ),
                    ("I-SAÚDE", saude),
                ]

                # Linhas das Dimensões
                for nome, valor in dimensoes:
                    faixa_str, _ = obter_faixa_classificacao(valor)
                    sigla_faixa = faixa_str.split(" ")[0]

                    with ui.row().classes(
                        "w-full justify-between items-center py-1 text-sm font-bold text-gray-800"
                    ):
                        ui.label(nome).classes("w-1/2 italic")
                        ui.label(f"{round(valor)}").classes("w-1/4 text-center")
                        ui.label(sigla_faixa).classes("w-1/4 text-right")

                # Linha de Nota Final
                faixa_final_str, _ = obter_faixa_classificacao(nota_final)
                sigla_final = faixa_final_str.split(" ")[0]

                with ui.row().classes(
                    "w-full justify-between items-center pt-2 mt-2 border-t-2 border-gray-800 text-base font-bold text-gray-900"
                ):
                    ui.label("IEG-M FINAL").classes("w-1/2 italic")
                    ui.label(f"{round(nota_final)}").classes("w-1/4 text-center")
                    ui.label(sigla_final).classes("w-1/4 text-right")

            # -----------------------------------------------------------------
            # COLUNA DA DIREITA: Gráfico Plotly
            # -----------------------------------------------------------------
            with ui.column().classes("col-span-12 md:col-span-8 w-full"):
                labels_topo = []
                for v in [cidade, gov, plan, fiscal, amb, educ, saude, nota_final]:
                    fx, _ = obter_faixa_classificacao(v)
                    sigla = fx.split(" ")[0]
                    labels_topo.append(f"{round(v)}<br><b>{sigla}</b>")

                df_grafico = pd.DataFrame(
                    {
                        "Dimensão": [
                            "I-cidade",
                            "I-gov TI",
                            "I-Plan",
                            "I-fiscal",
                            "I-Amb",
                            "I-educ",
                            "i-saude",
                            "IEG-M final",
                        ],
                        "Pontuação": [
                            round(cidade),
                            round(gov),
                            round(plan),
                            round(fiscal),
                            round(amb),
                            round(educ),
                            round(saude),
                            round(nota_final),
                        ],
                        "LabelTopo": labels_topo,
                    }
                )

                fig = px.bar(
                    df_grafico,
                    x="Dimensão",
                    y="Pontuação",
                    text="LabelTopo",
                    range_y=[0, 1100],
                )

                fig.update_traces(
                    marker_color="#2563eb",
                    textposition="outside",
                    textfont=dict(size=12, family="Arial", color="#1e293b"),
                    cliponaxis=False,
                )

                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(
                        title="",
                        tickfont=dict(size=12, family="Arial"),
                        showgrid=False,
                    ),
                    yaxis=dict(
                        title="",
                        tickfont=dict(size=11),
                        tickvals=[0, 250, 500, 750, 1000],
                        showgrid=True,
                        gridcolor="#f0f0f0",
                    ),
                    height=460,
                    margin=dict(l=10, r=10, t=30, b=10),
                )

                ui.plotly(fig).classes("w-full h-full")

    render_conteudo()


# Alias para garantir compatibilidade com qualquer função procurada pelo _executar_modulo
container_painel_iegm_final = mostrar_painel_iegm_final
