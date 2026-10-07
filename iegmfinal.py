"""Painel IEG-M final para integração com o main.py NiceGUI.

A API pública esperada pelo carregador é:
    container_painel_iegm_final(ano)
    mostrar_painel_iegm_final(ano)
"""

import logging
import os
from typing import Any, Callable, Optional

from nicegui import app, ui

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ANOS_DISPONIVEIS = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
TABELAS_DIMENSOES = {
    "iplan": "respostas_iplan",
    "ieduc": "respostas_ieduc",
    "isaude": "respostas_isaude",
    "igov": "respostas_igov",
    "iamb": "respostas_iamb",
}

# Primeiro tenta o conector já usado pelo módulo i-Cidade; em seguida,
# utiliza psycopg2 diretamente com DATABASE_URL/NEON_DATABASE_URL.
_conector_externo: Optional[Callable] = None
try:
    from icidade_completo import get_connection as _conector_externo
except (ImportError, AttributeError):
    try:
        from icidade import get_connection as _conector_externo
    except (ImportError, AttributeError):
        _conector_externo = None


def _abrir_conexao():
    if _conector_externo is not None:
        try:
            return _conector_externo()
        except Exception as exc:
            logger.warning("Conector existente falhou; tentando conexão direta: %s", exc)

    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 não está instalado") from exc

    database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not database_url:
        # O main normalmente expõe NEON_URL. A leitura é feita somente em
        # tempo de execução para evitar import circular durante o carregamento.
        try:
            import main
            database_url = getattr(main, "NEON_URL", None)
        except Exception:
            database_url = None
    if not database_url:
        raise RuntimeError("DATABASE_URL/NEON_DATABASE_URL não configurada")
    return psycopg2.connect(database_url)


def _valor_primeira_coluna(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    try:
        return row[0]
    except (IndexError, KeyError, TypeError):
        return None


def _numero(valor: Any) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _soma_tabela(tabela: str, ano: int) -> float:
    if tabela not in set(TABELAS_DIMENSOES.values()):
        return 0.0
    with _abrir_conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COALESCE(SUM(pontos), 0) AS total "
                f"FROM {tabela} WHERE ano = %s",
                (int(ano),),
            )
            return _numero(_valor_primeira_coluna(cur.fetchone()))


def puxar_nota_iplan(ano: int) -> float:
    return _soma_tabela("respostas_iplan", ano)


def puxar_nota_ieduc(ano: int) -> float:
    return _soma_tabela("respostas_ieduc", ano)


def puxar_nota_isaude(ano: int) -> float:
    return _soma_tabela("respostas_isaude", ano)


def puxar_nota_igov(ano: int) -> float:
    return _soma_tabela("respostas_igov", ano)


def puxar_nota_iamb(ano: int) -> float:
    return max(0.0, round(_soma_tabela("respostas_iamb", ano), 1))


def puxar_nota_ifiscal(ano: int) -> float:
    with _abrir_conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pontos FROM respostas_ifiscal WHERE ano = %s",
                (int(ano),),
            )
            valores = [_numero(_valor_primeira_coluna(row)) for row in cur.fetchall()]
    if not valores or any(valor <= -100.0 for valor in valores):
        return 0.0
    return sum(valor for valor in valores if valor > -100.0)


def puxar_nota_icidade(ano: int) -> float:
    with _abrir_conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(pontos), 0) FROM respostas "
                "WHERE dimensao = 'icidade' AND ano = %s",
                (int(ano),),
            )
            valor = _numero(_valor_primeira_coluna(cur.fetchone()))
            if valor > 0:
                return valor
            cur.execute(
                "SELECT COALESCE(SUM(pontos), 0) FROM respostas_icidade WHERE ano = %s",
                (int(ano),),
            )
            return _numero(_valor_primeira_coluna(cur.fetchone()))


def carregar_pontuacoes(ano: int) -> dict[str, float]:
    """Carrega todas as dimensões sem derrubar a página se uma tabela faltar."""
    consultas = {
        "I-CIDADE": puxar_nota_icidade,
        "I-GOV TI": puxar_nota_igov,
        "I-PLAN": puxar_nota_iplan,
        "I-FISCAL": puxar_nota_ifiscal,
        "I-AMB": puxar_nota_iamb,
        "I-EDUC": puxar_nota_ieduc,
        "I-SAÚDE": puxar_nota_isaude,
    }
    resultado = {}
    for nome, consulta in consultas.items():
        try:
            resultado[nome] = round(float(consulta(ano)), 1)
        except Exception as exc:
            logger.exception("Falha ao carregar %s/%s: %s", nome, ano, exc)
            resultado[nome] = 0.0
    return resultado


def calcular_nota_final(plan: float, fiscal: float, educ: float, saude: float,
                        amb: float, cidade: float, gov: float) -> float:
    return round(
        float(plan) * 0.20 + float(fiscal) * 0.20 + float(educ) * 0.20
        + float(saude) * 0.20 + float(amb) * 0.10
        + float(cidade) * 0.05 + float(gov) * 0.05,
        1,
    )


def obter_faixa_classificacao(nota: float):
    if nota >= 900:
        return "A", "#10B981", "Altamente Efetiva"
    if nota >= 750:
        return "B+", "#3B82F6", "Muito Efetiva"
    if nota >= 600:
        return "B", "#F59E0B", "Efetiva"
    if nota >= 500:
        return "C+", "#F97316", "Em Fase de Adequação"
    return "C", "#EF4444", "Baixo Nível de Adequação"


def _opcoes_grafico(pontuacoes: dict[str, float], final: float) -> dict:
    nomes = list(pontuacoes) + ["IEG-M FINAL"]
    valores = list(pontuacoes.values()) + [final]
    return {
        "animation": True,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": {"left": 45, "right": 25, "top": 45, "bottom": 65},
        "xAxis": {"type": "category", "data": nomes, "axisLabel": {"rotate": 25}},
        "yAxis": {"type": "value", "min": 0, "max": 1100, "interval": 250},
        "series": [{
            "name": "Pontuação",
            "type": "bar",
            "data": valores,
            "barMaxWidth": 55,
            "label": {"show": True, "position": "top", "formatter": "{c}"},
            "itemStyle": {"color": "#2563eb"},
        }],
    }


def mostrar_painel_iegm_final(ano_sel=None):
    try:
        ano_inicial = int(ano_sel if ano_sel is not None else
                          app.storage.user.get("ano_referencia_global", 2026))
    except (TypeError, ValueError):
        ano_inicial = 2026
    if ano_inicial not in ANOS_DISPONIVEIS:
        ano_inicial = 2026

    estado = {"ano": ano_inicial}
    ui.label("Pontuação do IEG-M - Prévia").classes(
        "w-full text-center text-2xl font-bold italic mb-6 text-blue-900"
    )

    @ui.refreshable
    def render_conteudo():
        ano = estado["ano"]
        pontos = carregar_pontuacoes(ano)
        final = calcular_nota_final(
            pontos["I-PLAN"], pontos["I-FISCAL"], pontos["I-EDUC"],
            pontos["I-SAÚDE"], pontos["I-AMB"], pontos["I-CIDADE"],
            pontos["I-GOV TI"],
        )
        faixa, cor, descricao = obter_faixa_classificacao(final)

        with ui.row().classes("w-full justify-end"):
            def mudar_ano(e):
                try:
                    novo = int(e.value)
                except (TypeError, ValueError):
                    return
                if novo in ANOS_DISPONIVEIS:
                    estado["ano"] = novo
                    app.storage.user["ano_referencia_global"] = novo
                    render_conteudo.refresh()

            ui.select(ANOS_DISPONIVEIS, value=ano, label="Exercício",
                      on_change=mudar_ano).props("outlined dense")

        with ui.grid(columns=12).classes("w-full gap-6 items-start"):
            with ui.column().classes("col-span-12 md:col-span-4 w-full"):
                with ui.card().classes("w-full border-2").style(f"border-color: {cor}"):
                    ui.label(f"IEG-M FINAL — {ano}").classes("text-lg font-bold")
                    ui.label(f"{final:.1f} pontos").classes("text-4xl font-black")
                    ui.label(f"Faixa {faixa} — {descricao}").style(f"color: {cor}; font-weight: bold")
                with ui.card().classes("w-full"):
                    ui.label("Pontuações por dimensão").classes("font-bold border-b pb-2")
                    for nome, valor in pontos.items():
                        faixa_dim, cor_dim, _ = obter_faixa_classificacao(valor)
                        with ui.row().classes("w-full justify-between py-1"):
                            ui.label(nome)
                            ui.label(f"{valor:.1f} — {faixa_dim}").style(f"color: {cor_dim}; font-weight: bold")

            with ui.column().classes("col-span-12 md:col-span-8 w-full"):
                ui.label("Pontuações do IEG-M").classes("text-lg font-bold")
                ui.echart(_opcoes_grafico(pontos, final)).classes("w-full").style("height: 500px")

    render_conteudo()


container_painel_iegm_final = mostrar_painel_iegm_final
