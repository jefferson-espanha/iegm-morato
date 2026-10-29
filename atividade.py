import logging
import os
import sys
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from nicegui import app, ui


LOGGER = logging.getLogger(__name__)
UNIDADES_MEDIDA = ["Unidade / Mensurável", "Não Mensurável"]
TABELA_ATIVIDADE = "atividade_dados"


def _database_url() -> str:
    """Obtém a URL das variáveis de ambiente ou da configuração do app principal."""
    url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not url:
        # O aplicativo principal pode manter a URL em uma constante Python
        # (padrão usado por versões anteriores do projeto).
        modulo_main = sys.modules.get("main") or sys.modules.get("__main__")
        if modulo_main is None:
            try:
                import main as modulo_main  # type: ignore
            except Exception:
                modulo_main = None
        if modulo_main is not None:
            for nome in ("NEON_URL", "NEON_URI", "DATABASE_URL", "NEON_DATABASE_URL"):
                url = getattr(modulo_main, nome, None)
                if url:
                    break
    if not url:
        raise RuntimeError(
            "Não encontrei a URL do PostgreSQL. Defina DATABASE_URL/NEON_DATABASE_URL "
            "ou disponibilize NEON_URL/NEON_URI no main.py."
        )
    return str(url).strip()


def get_db_connection():
    return psycopg2.connect(_database_url(), cursor_factory=RealDictCursor)


def inicializar_tabela_atividade() -> None:
    """Cria a tabela do módulo se ainda não existir; não altera tabelas existentes."""
    sql = f"""
        CREATE TABLE IF NOT EXISTS {TABELA_ATIVIDADE} (
            id BIGSERIAL PRIMARY KEY,
            ano INTEGER NOT NULL UNIQUE,
            programas_p1 JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            programas_p2 JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def carregar_dados(ano: int) -> dict[str, dict[str, Any]]:
    inicializar_tabela_atividade()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT programas_p1, programas_p2 FROM {TABELA_ATIVIDADE} WHERE ano = %s",
                (int(ano),),
            )
            row = cur.fetchone()
    if not row:
        return {"p1": {}, "p2": {}}
    return {
        "p1": row["programas_p1"] or {},
        "p2": row["programas_p2"] or {},
    }


def salvar_dados(ano: int, programas_p1: dict, programas_p2: dict) -> None:
    inicializar_tabela_atividade()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {TABELA_ATIVIDADE}
                    (ano, programas_p1, programas_p2, atualizado_em)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (ano) DO UPDATE SET
                    programas_p1 = EXCLUDED.programas_p1,
                    programas_p2 = EXCLUDED.programas_p2,
                    atualizado_em = NOW();
                """,
                (int(ano), Json(programas_p1), Json(programas_p2)),
            )
        conn.commit()


def calcular_e1_p1(indicadores: list[dict]) -> float:
    if not indicadores:
        return 0.0
    valores = []
    for item in indicadores:
        previsto = float(item.get("A", 0) or 0)
        realizado = float(item.get("B", 0) or 0)
        valores.append(min(realizado / previsto, 1.0) if previsto > 0 else 0.0)
    return sum(valores) / len(valores)


def calcular_e2_p1(acoes: list[dict]) -> float:
    if not acoes:
        return 0.0
    valores = []
    for item in acoes:
        if item.get("unidade") == "Não Mensurável":
            valores.append(0.0)
        else:
            meta = float(item.get("C", 0) or 0)
            realizado = float(item.get("D", 0) or 0)
            valores.append(min(realizado / meta, 1.0) if meta > 0 else 0.0)
    return sum(valores) / len(valores)


def calcular_h1_p2(acoes: list[dict]) -> float:
    return calcular_e2_p1(acoes)


def calcular_h2_p2(acoes: list[dict]) -> float:
    if not acoes:
        return 0.0
    valores = []
    for item in acoes:
        orcamento = float(item.get("F", 0) or 0)
        gasto = float(item.get("G", 0) or 0)
        valores.append(min(gasto / orcamento, 1.0) if orcamento > 0 else 0.0)
    return sum(valores) / len(valores)


def pontuacao(valor: float) -> float:
    if valor <= 0.2:
        return 250.0
    if valor >= 0.4:
        return 0.0
    return ((0.4 - valor) / 0.2) * 250.0


def _numero(valor: Any) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def mostrar_formulario_atividade(ano: int | None = None) -> None:
    """Renderiza o módulo P1/P2 no NiceGUI, para uso integrado ou standalone."""
    try:
        ano_atual = int(
            ano
            or app.storage.user.get("ano_referencia_global")
            or app.storage.user.get("ano_sel")
            or 2026
        )
        dados = carregar_dados(ano_atual)
    except Exception as exc:
        LOGGER.exception("Falha ao conectar/carregar o módulo Atividade")
        ui.label(f"Não foi possível abrir o módulo Atividade: {exc}").classes(
            "text-red-700 font-bold"
        )
        return

    programas_p1 = dados["p1"]
    programas_p2 = dados["p2"]

    def persistir(mensagem: str = "Alterações salvas no PostgreSQL") -> None:
        try:
            salvar_dados(ano_atual, programas_p1, programas_p2)
            ui.notify(mensagem, type="positive")
        except Exception as exc:
            LOGGER.exception("Falha ao salvar o módulo Atividade")
            ui.notify(f"Erro ao salvar: {exc}", type="negative")

    ui.label(f"Painel Integrado de Auditoria i-Plan — Atividade ({ano_atual})").classes(
        "text-2xl font-bold text-blue-900"
    )
    ui.label("P1 — Coerência do planejamento | P2 — Confronto físico x financeiro").classes(
        "text-gray-600 mb-4"
    )

    with ui.row().classes("w-full justify-end"):
        ui.button("Salvar tudo", icon="save", on_click=lambda: persistir()).props(
            "color=primary"
        )

    with ui.tabs().classes("w-full") as tabs:
        tab_p1 = ui.tab("P1 — Coerência do planejamento")
        tab_p2 = ui.tab("P2 — Físico x financeiro")
    with ui.tab_panels(tabs, value=tab_p1).classes("w-full"):
        with ui.tab_panel(tab_p1):
            painel_p1 = ui.column().classes("w-full")
        with ui.tab_panel(tab_p2):
            painel_p2 = ui.column().classes("w-full")

    def editar_item(tipo: str, programa: str, idx: int | None = None) -> None:
        eh_p1_ind = tipo == "p1_ind"
        eh_p1_acao = tipo == "p1_acao"
        registro = (programas_p1[programa]["indicadores"][idx] if eh_p1_ind and idx is not None else
                    programas_p1[programa]["acoes"][idx] if eh_p1_acao and idx is not None else
                    programas_p2[programa]["acoes"][idx] if tipo == "p2_acao" and idx is not None else {})
        titulo = {"p1_ind": "Indicador P1", "p1_acao": "Ação P1", "p2_acao": "Ação P2"}[tipo]
        with ui.dialog() as dialog, ui.card().classes("w-[650px] max-w-full"):
            ui.label(f"{titulo}: {'editar' if idx is not None else 'cadastrar'}").classes(
                "text-lg font-bold"
            )
            nome = ui.input("Nome", value=registro.get("nome", "")).classes("w-full")
            unidade = None
            if eh_p1_ind:
                a = ui.number("Previsto inicial (A)", value=_numero(registro.get("A")), min=0).classes("w-full")
                b = ui.number("Realizado eficiente (B)", value=_numero(registro.get("B")), min=0).classes("w-full")
            else:
                unidade = ui.select(
                    UNIDADES_MEDIDA,
                    value=registro.get("unidade", UNIDADES_MEDIDA[0]),
                    label="Métrica física",
                ).classes("w-full")
                c = ui.number("Meta projetada (C)", value=_numero(registro.get("C")), min=0).classes("w-full")
                d = ui.number("Realizado físico (D)", value=_numero(registro.get("D")), min=0).classes("w-full")
                if tipo == "p2_acao":
                    f = ui.number("Orçamento fixado (F)", value=_numero(registro.get("F")), min=0).classes("w-full")
                    g = ui.number("Gasto liquidado (G)", value=_numero(registro.get("G")), min=0).classes("w-full")

            def salvar_item() -> None:
                nome_txt = (nome.value or "").strip()
                if not nome_txt:
                    ui.notify("Informe o nome do indicador ou da ação.", type="warning")
                    return
                if eh_p1_ind:
                    novo = {"nome": nome_txt, "A": _numero(a.value), "B": _numero(b.value)}
                    lista = programas_p1[programa]["indicadores"]
                else:
                    nao_mensuravel = unidade.value == "Não Mensurável"
                    novo = {
                        "nome": nome_txt,
                        "unidade": unidade.value,
                        "C": 0.0 if nao_mensuravel else _numero(c.value),
                        "D": 0.0 if nao_mensuravel else _numero(d.value),
                    }
                    if tipo == "p2_acao":
                        novo.update({"F": _numero(f.value), "G": _numero(g.value)})
                        lista = programas_p2[programa]["acoes"]
                    else:
                        lista = programas_p1[programa]["acoes"]
                if idx is None:
                    lista.append(novo)
                else:
                    lista[idx] = novo
                persistir()
                dialog.close()
                renderizar_p1()
                renderizar_p2()

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancelar", on_click=dialog.close).props("flat")
                ui.button("Salvar", icon="save", on_click=salvar_item).props("color=primary")
        dialog.open()

    def excluir_item(lista: list, idx: int, atualizar) -> None:
        lista.pop(idx)
        persistir("Registro excluído e alterações salvas")
        atualizar()

    def adicionar_programa(p1: bool, campo: Any) -> None:
        nome = (campo.value or "").strip()
        if not nome:
            ui.notify("Digite o nome do programa.", type="warning")
            return
        programas = programas_p1 if p1 else programas_p2
        if nome in programas:
            ui.notify("Esse programa já existe.", type="warning")
            return
        programas[nome] = {"indicadores": [], "acoes": []} if p1 else {"acoes": []}
        campo.value = ""
        persistir("Programa cadastrado")
        renderizar_p1()
        renderizar_p2()

    def renderizar_p1() -> None:
        painel_p1.clear()
        with painel_p1:
            ui.label("P1 — Resultados dos indicadores versus metas das ações").classes(
                "text-xl font-semibold"
            )
            with ui.row().classes("w-full items-end"):
                novo_nome = ui.input("Novo programa P1").classes("grow")
                ui.button("Adicionar programa", icon="add", on_click=lambda: adicionar_programa(True, novo_nome))
            if not programas_p1:
                ui.label("Cadastre um programa para começar.").classes("text-gray-600")
                return
            seletor = ui.select(list(programas_p1), value=next(iter(programas_p1)), label="Programa em execução").classes("w-full")
            conteudo = ui.column().classes("w-full")

            def desenhar_programa(nome_programa: str) -> None:
                conteudo.clear()
                if not nome_programa or nome_programa not in programas_p1:
                    return
                info = programas_p1[nome_programa]
                indicadores = info.setdefault("indicadores", [])
                acoes = info.setdefault("acoes", [])
                with conteudo:
                    with ui.row().classes("w-full"):
                        ui.button("Adicionar indicador", icon="add", on_click=lambda: editar_item("p1_ind", nome_programa))
                        ui.button("Adicionar ação", icon="add", on_click=lambda: editar_item("p1_acao", nome_programa))
                    with ui.row().classes("w-full items-start"):
                        with ui.card().classes("grow"):
                            ui.label("Indicadores (A: previsto inicial; B: realizado eficiente)").classes("font-bold")
                            if not indicadores:
                                ui.label("Nenhum indicador cadastrado.")
                            for i, item in enumerate(indicadores):
                                with ui.row().classes("w-full items-center"):
                                    ui.label(f"{item.get('nome','')} — A: {_numero(item.get('A')):g} | B: {_numero(item.get('B')):g}").classes("grow")
                                    ui.button(icon="edit", on_click=lambda i=i: editar_item("p1_ind", nome_programa, i)).props("flat dense")
                                    ui.button(icon="delete", on_click=lambda i=i: excluir_item(indicadores, i, renderizar_p1)).props("flat dense color=negative")
                        with ui.card().classes("grow"):
                            ui.label("Ações (C: meta; D: realizado físico)").classes("font-bold")
                            if not acoes:
                                ui.label("Nenhuma ação cadastrada.")
                            for i, item in enumerate(acoes):
                                with ui.row().classes("w-full items-center"):
                                    ui.label(f"{item.get('nome','')} — {item.get('unidade','')} | C: {_numero(item.get('C')):g} | D: {_numero(item.get('D')):g}").classes("grow")
                                    ui.button(icon="edit", on_click=lambda i=i: editar_item("p1_acao", nome_programa, i)).props("flat dense")
                                    ui.button(icon="delete", on_click=lambda i=i: excluir_item(acoes, i, renderizar_p1)).props("flat dense color=negative")
                    linhas = []
                    total_a = 0.0
                    total_c = 0.0
                    for prog, valores in programas_p1.items():
                        inds = valores.get("indicadores", [])
                        acts = valores.get("acoes", [])
                        soma_a = sum(_numero(item.get("A")) for item in inds)
                        soma_c = sum(_numero(item.get("C")) for item in acts)
                        total_a += soma_a
                        total_c += soma_c
                        m1 = calcular_e1_p1(inds)
                        m2 = calcular_e2_p1(acts)
                        tem_acao_nao_mensuravel = any(item.get("unidade") == "Não Mensurável" for item in acts)
                        if not inds or soma_a == 0 or not acts or (soma_c == 0 and not tem_acao_nao_mensuravel):
                            desvio = 1.0
                            m1_exibido = 0.0
                            m2_exibido = 0.0
                        else:
                            desvio = abs(m1 - m2)
                            m1_exibido = m1
                            m2_exibido = m2
                        linhas.append({"Programa": prog, "μE1 (média indicadores)": round(m1_exibido, 4), "μE2 (média ações)": round(m2_exibido, 4), "Desvio E": round(desvio, 4)})
                    ui.label("Tabela consolidada P1").classes("text-lg font-bold")
                    ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in linhas[0]], rows=linhas).classes("w-full")
                    media_desvio = 1.0 if total_a == 0 or total_c == 0 or not linhas else sum(r["Desvio E"] for r in linhas) / len(linhas)
                    ui.label(f"Ef final do município: {media_desvio:.4f} | Pontuação P1 (máx. 250): {pontuacao(media_desvio):.2f}").classes("text-lg font-bold text-blue-800")

            seletor.on_value_change(lambda e: desenhar_programa(e.value))
            desenhar_programa(seletor.value)

    def renderizar_p2() -> None:
        painel_p2.clear()
        with painel_p2:
            ui.label("P2 — Confronto entre resultado físico e recursos financeiros").classes(
                "text-xl font-semibold"
            )
            with ui.row().classes("w-full items-end"):
                novo_nome = ui.input("Novo programa P2").classes("grow")
                ui.button("Adicionar programa", icon="add", on_click=lambda: adicionar_programa(False, novo_nome))
            if not programas_p2:
                ui.label("Cadastre os programas para começar.").classes("text-gray-600")
                return
            seletor = ui.select(list(programas_p2), value=next(iter(programas_p2)), label="Programa em execução").classes("w-full")
            conteudo = ui.column().classes("w-full")

            def desenhar_programa(nome_programa: str) -> None:
                conteudo.clear()
                if not nome_programa or nome_programa not in programas_p2:
                    return
                info = programas_p2[nome_programa]
                acoes = info.setdefault("acoes", [])
                with conteudo:
                    ui.button("Lançar ação P2", icon="add", on_click=lambda: editar_item("p2_acao", nome_programa))
                    with ui.card().classes("w-full"):
                        ui.label("Ações cadastradas (C/D: físico; F/G: orçamento e gasto)").classes("font-bold")
                        if not acoes:
                            ui.label("Nenhuma ação cadastrada.")
                        for i, item in enumerate(acoes):
                            with ui.row().classes("w-full items-center"):
                                ui.label(f"{item.get('nome','')} — {item.get('unidade','')} | C: {_numero(item.get('C')):g} | D: {_numero(item.get('D')):g} | F: {_numero(item.get('F')):g} | G: {_numero(item.get('G')):g}").classes("grow")
                                ui.button(icon="edit", on_click=lambda i=i: editar_item("p2_acao", nome_programa, i)).props("flat dense")
                                ui.button(icon="delete", on_click=lambda i=i: excluir_item(acoes, i, renderizar_p2)).props("flat dense color=negative")
                    linhas = []
                    total_c = 0.0
                    total_f = 0.0
                    for prog, valores in programas_p2.items():
                        lista = valores.get("acoes", [])
                        soma_c = sum(_numero(item.get("C")) for item in lista)
                        soma_f = sum(_numero(item.get("F")) for item in lista)
                        total_c += soma_c
                        total_f += soma_f
                        h1 = calcular_h1_p2(lista)
                        h2 = calcular_h2_p2(lista)
                        tem_acao_nao_mensuravel = any(item.get("unidade") == "Não Mensurável" for item in lista)
                        if not lista or (soma_c == 0 and not tem_acao_nao_mensuravel) or soma_f == 0:
                            desvio = 1.0
                            h1_exibido = 0.0
                            h2_exibido = 0.0
                        else:
                            desvio = abs(h1 - h2)
                            h1_exibido = h1
                            h2_exibido = h2
                        linhas.append({"Programa": prog, "μH1 (média física)": round(h1_exibido, 4), "μH2 (média gasto)": round(h2_exibido, 4), "Desvio H": round(desvio, 4)})
                    ui.label("Tabela consolidada P2").classes("text-lg font-bold")
                    ui.table(columns=[{"name": k, "label": k, "field": k, "align": "left"} for k in linhas[0]], rows=linhas).classes("w-full")
                    media_desvio = 1.0 if total_c == 0 or total_f == 0 or not linhas else sum(r["Desvio H"] for r in linhas) / len(linhas)
                    ui.label(f"Hf final do município: {media_desvio:.4f} | Pontuação P2 (máx. 250): {pontuacao(media_desvio):.2f}").classes("text-lg font-bold text-blue-800")

            seletor.on_value_change(lambda e: desenhar_programa(e.value))
            desenhar_programa(seletor.value)

    renderizar_p1()
    renderizar_p2()


# Aliases para compatibilidade com o roteamento do app principal.
container_formulario_atividade = mostrar_formulario_atividade
main = mostrar_formulario_atividade
