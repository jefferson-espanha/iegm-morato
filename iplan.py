import base64
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
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
                # Criar tabela caso não exista
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS respostas_iplan (
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
        print(f"❌ Erro ao inicializar tabela respostas_iplan: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iplan
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                rows = cur.fetchall()
                for row in rows:
                    val_bruto = row["valor"] or ""
                    
                    # Se for uma lista salva como JSON string, converte de volta para Python
                    if val_bruto.startswith("[") and val_bruto.endswith("]"):
                        try:
                            val_final = json.loads(val_bruto)
                        except Exception:
                            val_final = val_bruto
                    else:
                        val_final = val_bruto

                    respostas[row["qid"]] = {
                        "valor": val_final,
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": (
                            row["link"]
                            if row["link"] != "EMPTY_STRING"
                            else ""
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
    ano, qid, valor, pontos, link="", comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(str(qid), {})
        comentarios = dados_atuais.get("comentarios", [])

    link_final = link.strip() if link else ""

    # Trata lista (Checkboxes) convertendo para JSON em texto
    if isinstance(valor, list):
        valor_str = json.dumps(valor)
    else:
        valor_str = str(valor) if valor is not None else ""

    query = """
        INSERT INTO respostas_iplan (ano, qid, valor, pontos, link, comentarios, status)
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
                        str(qid),
                        valor_str,
                        float(pontos),
                        link_final,
                        Json(comentarios),
                        str(status),
                    ),
                )
                conn.commit()
                print(f"✅ Quesito {qid} ({ano}) salvo com sucesso no banco!")
    except Exception as e:
        print(f"❌ Erro ao salvar resposta no Neon DB: {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_iplan WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (int(ano),))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")


def _obter_lista_comentarios(dados_q):
    coms = dados_q.get("comentarios", [])
    return coms if isinstance(coms, list) else []

# =============================================================================
# FUNÇÃO AUXILIAR DE RENDERIZAÇÃO DE QUESITOS (PADRÃO)
# =============================================================================
def render_quesito(
    ano,
    res_data,
    qid,
    titulo,
    pergunta,
    opcoes,
    tipo_input="radio",
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    dados_q = res_data.get(qid, {})
    link_atual = dados_q.get("link", "")

    # Trata valor inicial
    if tipo_input == "checkbox":
        valor_bruto = dados_q.get("valor", [])
        if isinstance(valor_bruto, list):
            valor_atual = valor_bruto
        elif valor_bruto in opcoes:
            valor_atual = [valor_bruto]
        else:
            valor_atual = []
    else:
        primeira_opcao_valida = list(opcoes.keys())[0] if opcoes else ""
        valor_atual = dados_q.get("valor", primeira_opcao_valida)
        if valor_atual not in opcoes:
            valor_atual = primeira_opcao_valida

    state = {
        "opcao": valor_atual,
        "link": link_atual,
    }

    with ui.card().classes(
        "w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"
    ):
        ui.label(f"{qid} • {titulo}").classes(
            "text-xl font-semibold text-blue-500 mb-3"
        )
        ui.label(pergunta).classes("text-base font-bold text-black mb-1")
        ui.label(
            "ℹ Preencha os campos abaixo e clique no botão de salvar."
        ).classes("text-xs text-gray-400 mb-6")

        with ui.grid(columns=2).classes("w-full gap-6 items-start mb-4"):
            # Lado Esquerdo: Checkboxes ou Radio Buttons
            if tipo_input == "checkbox":
                with ui.column().classes("gap-2 w-full"):
                    # Dicionário interno para monitorar o estado individual de cada checkbox
                    chk_states = {}

                    def make_on_change(opt):
                        def on_change(e):
                            if e.value and opt not in state["opcao"]:
                                state["opcao"].append(opt)
                            elif not e.value and opt in state["opcao"]:
                                state["opcao"].remove(opt)
                            atualizar_impacto()
                        return on_change

                    for opt_key in opcoes.keys():
                        is_checked = opt_key in state["opcao"]
                        chk = ui.checkbox(
                            text=opt_key,
                            value=is_checked,
                            on_change=make_on_change(opt_key)
                        ).props("color=blue")
                        chk_states[opt_key] = chk
            else:
                input_radio = (
                    ui.radio(
                        options=list(opcoes.keys()),
                        value=state["opcao"],
                    )
                    .props("color=blue")
                    .bind_value(state, "opcao")
                )

            # Lado Direito: Textarea para o Link
            ui.textarea(
                label="Link de Evidência / Documento:",
                value=state["link"],
                placeholder=placeholder_link,
            ).classes("w-full").props("outlined rows=5").bind_value(
                state, "link"
            )

        # Cálculo dinâmico de pontuação
        def calcular_pontos(opcao_sel):
            if isinstance(opcao_sel, list):
                return sum(opcoes.get(opt, 0.0) for opt in opcao_sel)
            return opcoes.get(opcao_sel, 0.0)

        pts_atuais = calcular_pontos(state["opcao"])
        label_impacto = ui.label(
            f"📊 Impacto de Pontuação no Quesito {qid}: {pts_atuais:.1f} pontos"
        ).classes("text-sm font-bold text-green-600 my-4")

        def atualizar_impacto():
            novos_pts = calcular_pontos(state["opcao"])
            label_impacto.set_text(
                f"📊 Impacto de Pontuação no Quesito {qid}: {novos_pts:.1f} pontos"
            )

        if tipo_input != "checkbox":
            input_radio.on("update:model-value", lambda e: atualizar_impacto())

        # Botão Salvar
        def salvar_acao():
            opcao_sel = state["opcao"]
            pts = calcular_pontos(opcao_sel)
            lnk = state["link"]

            save_resposta(
                ano=ano,
                qid=qid,
                valor=opcao_sel,
                pontos=pts,
                link=lnk,
                comentarios=dados_q.get("comentarios", []),
                status=dados_q.get("status", "Pendente"),
            )
            ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive")
            if on_save_callback:
                on_save_callback()

        ui.button(
            "SALVAR RESPOSTA", on_click=salvar_acao
        ).classes(
            "bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2"
        )

        ui.separator().classes("my-2")
        bloco_comentarios(qid, res_data, on_save_callback)

# =============================================================================
# PAINEL DE CONTROLE LATERAL
# =============================================================================
def render_painel_controle(ano_atual, on_mudar_ano, on_refresh):
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
        ui.label("🛠️ Painel de Controle (iPlan)").classes(
            "text-lg font-bold mb-2 text-blue-900"
        )

        ui.select(
            options=anos,
            value=int(ano_atual),
            label="Ano de Referência:",
            on_change=lambda e: on_mudar_ano(e.value),
        ).classes("w-full mb-4 bg-white")

        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("PONTUAÇÃO TOTAL").classes(
                "text-xs text-gray-500 font-bold uppercase tracking-wider"
            )
            ui.label(f"{total_pts:.1f} pts").classes(
                "text-3xl font-black text-gray-800 my-1"
            )
            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        def zerar_acao():
            zerar_questionario_db(ano_atual)
            ui.notify(
                f"✅ Questionário de {ano_atual} zerado!", type="positive"
            )
            on_refresh()

        ui.button("🔄 ATUALIZAR UI", on_click=on_refresh).classes(
            "w-full bg-blue-600 text-white font-bold mb-2"
        )
        ui.button("🗑️ ZERAR ANO", on_click=zerar_acao).classes(
            "w-full bg-red-600 text-white font-bold"
        )

        ui.separator().classes("my-4")
        ui.html("""
            <div style="text-align: center; color: #333; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 12px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
        """).classes("w-full")


# =============================================================================
# BLOCO DE COMENTÁRIOS INTERNOS
# =============================================================================
def bloco_comentarios(qid, res_data, on_save_callback=None):
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")

    dados_q = res_data.get(qid, {})
    historico = _obter_lista_comentarios(dados_q)

    status_global = dados_q.get("status", "Pendente")
    for com in reversed(historico):
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            break

    badge_status = (
        "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    )

    with ui.expansion(
        f"💬 Diálogo Interno {qid} | Status: {badge_status}",
        value=(status_global == "Pendente"),
    ).classes("w-full border rounded p-2 mt-3 bg-gray-50"):

        def alterar_status(e):
            novo_st = e.value
            log = {
                "autor": "Sistema / " + usuario_atual,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "texto": f"ℹ️ Alterou o status do quesito para: **{novo_st.upper()}**.",
                "status_definido": novo_st,
            }
            historico.append(log)
            save_resposta(
                ano=ano_sel,
                qid=qid,
                valor=dados_q.get("valor", ""),
                pontos=dados_q.get("pontos", 0.0),
                link=dados_q.get("link", ""),
                comentarios=historico,
                status=novo_st,
            )
            ui.notify(f"Status alterado para {novo_st}", type="info")
            if on_save_callback:
                on_save_callback()

        ui.radio(
            ["Resolvido", "Pendente"],
            value=status_global,
            on_change=alterar_status,
        ).props("inline")

        if historico:
            for idx, com in enumerate(historico):
                if isinstance(com, str):
                    com = {"autor": "Usuário", "data": "", "texto": com}

                autor = com.get("autor", "Anônimo")
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
                    ui.notify("Comentário removido.", type="warning")
                    if on_save_callback:
                        on_save_callback()

                with ui.row().classes(
                    "w-full items-center justify-between no-wrap mb-2"
                ):
                    if "Sistema /" in autor:
                        ui.html(
                            f"""<div style="background-color: #f1f3f5; padding: 6px 12px; border-radius: 6px; border-left: 3px solid #ced4da; width: 100%;">
                                <span style="font-size: 11px; color: #6c757d; font-style: italic;">{autor} - {data_com}</span>
                                <p style="margin: 2px 0 0 0; font-size: 12px; color: #495057;">{texto_com}</p>
                            </div>"""
                        ).classes("w-full")
                    else:
                        ui.html(
                            f"""<div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #1e88e5; border: 1px solid #e0e0e0; width: 100%;">
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>"""
                        ).classes("w-full")

                    ui.button("🗑️", on_click=deletar_comentario).props(
                        "flat dense"
                    )

        input_novo_comentario = (
            ui.textarea(placeholder="Novo comentário...")
            .classes("w-full")
            .props("outlined rows=2")
        )

        def postar_comentario():
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append({
                    "autor": usuario_atual,
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": txt,
                    "status_definido": status_global,
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

        ui.button("Postar Comentário", on_click=postar_comentario).classes(
            "bg-blue-600 text-white mt-2"
        )

# =============================================================================
# MÓDULO PRINCIPAL DE REQUISITOS
# =============================================================================
def container_formulario_plan(ano=None):
    if "ano_referencia_global" not in app.storage.user:
        app.storage.user["ano_referencia_global"] = ano if ano else 2026

    main_container = ui.element("div").classes("w-full")

    @ui.refreshable
    def render_conteudo():
        main_container.clear()
        with main_container:
            ano_sel = int(app.storage.user.get("ano_referencia_global", 2026))
            res_data = load_respostas(ano_sel)

            def alterar_ano(novo_ano):
                app.storage.user["ano_referencia_global"] = int(novo_ano)
                ui.notify(f"Ano alterado para {novo_ano}", type="info")
                render_conteudo.refresh()

            with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-12 gap-6 items-start"):
                # Coluna 1: Painel Lateral (3/12)
                with ui.element("div").classes("md:col-span-4 lg:col-span-3"):
                    render_painel_controle(
                        ano_atual=ano_sel,
                        on_mudar_ano=alterar_ano,
                        on_refresh=render_conteudo.refresh,
                    )

                # Coluna 2: Formulário Principal (9/12)
                with ui.element("div").classes("md:col-span-8 lg:col-span-9 flex flex-col gap-4"):
                    with ui.card().classes("w-full p-6 border rounded-lg shadow-sm bg-white"):
                        ui.label(f"📋 Módulo i-Plan — Ano {ano_sel}").classes(
                            "text-xl font-bold text-slate-800 border-b pb-2"
                        )

                    # ==========================================
                    # QUESITO 1.0 (Audiências Públicas)
                    # ==========================================
                    opcoes_10 = {
                        "Selecione...": 0.0,
                        "Sim – 01 pt": 1.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.0",
                        titulo="Audiências Públicas na Elaboração das Peças Orçamentárias",
                        pergunta="A Prefeitura realizou audiências públicas para elaboração das peças orçamentárias? (Obs: Serão consideradas apenas as audiências públicas realizadas durante o processo de planejamento municipal - PPA, LDO e LOA):",
                        opcoes=opcoes_10,
                        placeholder_link="Insira o link das atas ou publicações das audiências públicas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.1 (Peças Orçamentárias - Checkbox)
                    # ==========================================
                    opcoes_11 = {
                        "Selecione...": 0.0,
                        "Não – 00 pts": 0.0,
                        "PPA inicial 2026-2029 – 01 pt": 1.0,
                        "LDO 2026 – 01 pt": 1.0,
                        "LOA 2026 – 01 pt": 1.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.1",
                        titulo="Peças Orçamentárias com Audiência Pública",
                        pergunta="Assinale para quais peças orçamentárias foram realizadas as audiências públicas (Considerar as audiências públicas da LOA e LDO realizadas no exercício avaliado e o último PPA elaborado):",
                        tipo_input="checkbox",
                        opcoes=opcoes_11,
                        placeholder_link="Insira o link de comprovação das audiências (ex: ata, edital, transmissão)...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.2 (Dia e Horário - Checkbox)
                    # ==========================================
                    opcoes_12 = {
                        "Selecione...": 0.0,
                        "Dia de semana em horário comercial (ex: 8 as 18 horas) – 00 pts": 0.0,
                        "Dia de semana após horário comercial (ex: após às 18 horas) – 02 pts": 2.0,
                        "Aos sábados, domingos e feriados – 02 pts": 2.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.2",
                        titulo="Dia e Horário das Audiências Públicas",
                        pergunta="Assinale o dia e horário de realização das audiências públicas:",
                        tipo_input="checkbox",
                        opcoes=opcoes_12,
                        placeholder_link="Insira o link das atas, convocações ou documentos com os horários...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.3 (Transcrição de Audiências - Radio)
                    # ==========================================
                    opcoes_13 = {
                        "Selecione...": 0.0,
                        "Sim – 02 pts": 2.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.3",
                        titulo="Atas e Registro das Audiências Públicas",
                        pergunta="As audiências públicas são transcritas em atas ou outro documento de registro das demandas/sugestões apresentadas pela participação popular?",
                        tipo_input="radio",
                        opcoes=opcoes_13,
                        placeholder_link="Insira o link direto para as atas ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.3.1 (Página Eletrônica das Atas)
                    # ==========================================
                    # Nota: Este quesito valida se há link publicado. Se contiver 'XYZ' ganha 0 pts, caso contrário ganha 3 pts.
                    opcoes_131 = {
                        "Selecione...": 0.0,
                        "Link/Página eletrônica disponível – 03 pts": 3.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.3.1",
                        titulo="Divulgação das Atas na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação das atas de audiências públicas (Se não estiver disponível na internet, inserir no campo de link o texto 'XYZ'):",
                        tipo_input="radio",
                        opcoes=opcoes_131,
                        placeholder_link="Cole o link das atas ou digite XYZ se não estiver disponível...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 1.4 (Planejamento e Organização - Checkbox)
                    # ==========================================
                    opcoes_14 = {
                        "Selecione...": 0.0,
                        "Convocação contendo o dia, horário e local através dos jornais, rádios, Portal da Prefeitura e plataformas digitais – 0,5 pt": 0.5,
                        "Estabelecimento da Pauta – 0,5 pt": 0.5,
                        "Disponibilização prévia de material de apoio a respeito dos temas a serem debatidos – 0,5 pt": 0.5,
                        "Planejamento logístico (localização, acomodações, som, vídeo, iluminação, transmissão) – 01 pt": 1.0,
                        "Indicação de mediador qualificado – 0,5 pt": 0.5,
                        "Estabelecimento da abordagem de interação – 0,5 pt": 0.5,
                        "Definição de mecanismos de avaliação – 0,5 pt": 0.5,
                        "Elaboração e divulgação do Relatório contendo a análise das demandas e sugestões coletadas – 01 pt": 1.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="1.4",
                        titulo="Planejamento e Organização das Audiências",
                        pergunta="Assinale os elementos considerados no processo de planejamento e organização das audiências públicas:",
                        tipo_input="checkbox",
                        opcoes=opcoes_14,
                        placeholder_link="Insira o link das comprovações organizacionais (editais, fotos, relatórios, pautas)...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 2.0 (Consulta Pública Online - Radio)
                    # ==========================================
                    opcoes_20 = {
                        "Selecione...": 0.0,
                        "Sim – 06 pts": 6.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="2.0",
                        titulo="Consulta Pública Online PPA 2026-2029",
                        pergunta="Houve a realização de consulta pública online para coleta de sugestões para a elaboração do PPA 2026-2029?",
                        tipo_input="radio",
                        opcoes=opcoes_20,
                        placeholder_link="Insira o link do formulário ou plataforma da consulta pública online...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 2.1 (Glossário na Consulta - Radio)
                    # ==========================================
                    opcoes_21 = {
                        "Selecione...": 0.0,
                        "Sim – 02 pts": 2.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="2.1",
                        titulo="Glossário em Linguagem Simples",
                        pergunta="Na consulta pública online de elaboração do Plano Plurianual (PPA) foi disponibilizado glossário explicando os objetivos, como contribuir, em linguagem clara e simples?",
                        tipo_input="radio",
                        opcoes=opcoes_21,
                        placeholder_link="Insira o link de acesso ao glossário ou da plataforma...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.0 (Diagnóstico Prévio - Radio)
                    # ==========================================
                    opcoes_30 = {
                        "Selecione...": 0.0,
                        "Sim – 14 pts": 14.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.0",
                        titulo="Diagnóstico do Planejamento do PPA",
                        pergunta="Além das audiências públicas, a Prefeitura realizou diagnóstico anteriormente ao planejamento, através do levantamento formal de seus problemas, necessidades e deficiências? (Obs: Os Planos Municipais Setoriais só podem ser considerados se neles houver evidências do levantamento formal dos problemas):",
                        tipo_input="radio",
                        opcoes=opcoes_30,
                        placeholder_link="Insira o link do documento do diagnóstico ou planos municipais setoriais...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.1 (Planos Federal/Estadual - Radio)
                    # ==========================================
                    opcoes_31 = {
                        "Selecione...": 0.0,
                        "Sim – 02 pts": 2.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.1",
                        titulo="Articulação com Planos Federal/Estadual",
                        pergunta="A elaboração do diagnóstico levou em conta algum plano do governo federal e/ou estadual?",
                        tipo_input="radio",
                        opcoes=opcoes_31,
                        placeholder_link="Insira o link das evidências de alinhamento com planos federais ou estaduais...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.1.1 (Descrição dos Programas - Text Area/Radio)
                    # ==========================================
                    opcoes_311 = {
                        "Descrição apresentada no campo de evidências": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.1.1",
                        titulo="Programas Federal/Estadual Utilizados",
                        pergunta="Descreva quais programas do governo federal ou estadual foram utilizados para elaboração do diagnóstico:",
                        tipo_input="radio",
                        opcoes=opcoes_311,
                        placeholder_link="Descreva os programas utilizados e/ou insira o link...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 3.2 (Diagnóstico por Programa - Radio)
                    # ==========================================
                    opcoes_32 = {
                        "Selecione...": 0.0,
                        "Sim, para todos os programas do PPA – 10 pts": 10.0,
                        "Sim, para a maior parte dos programas do PPA – 05 pts": 5.0,
                        "Sim, para a menor parte dos programas do PPA – 03 pts": 3.0,
                        "Não foi realizado diagnóstico prévio para nenhum programa do PPA – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="3.2",
                        titulo="Diagnóstico Prévio dos Programas do PPA",
                        pergunta="Os programas do PPA 2026-2029 tiveram diagnóstico prévio? (Obs: Os Planos Municipais Setoriais só podem ser considerados se neles houver evidências do levantamento formal dos problemas):",
                        tipo_input="radio",
                        opcoes=opcoes_32,
                        placeholder_link="Insira o link com os diagnósticos específicos por programa...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.0 (Metas Físicas e Financeiras - Radio)
                    # ==========================================
                    opcoes_40 = {
                        "Selecione...": 0.0,
                        "Sim, com metas físicas e financeiras – 10 pts": 10.0,
                        "Sim, apenas com metas financeiras – 05 pts": 5.0,
                        "Sim, apenas com metas físicas – 05 pts": 5.0,
                        "Não houve o estabelecimento de metas anuais – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.0",
                        titulo="Estabelecimento de Metas Anuais no PPA",
                        pergunta="Há o estabelecimento de metas físicas e financeiras de forma anual nas ações previstas no PPA?",
                        tipo_input="radio",
                        opcoes=opcoes_40,
                        placeholder_link="Insira o link dos anexos de metas do PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1 (Programas Finalísticos - Radio)
                    # ==========================================
                    opcoes_41 = {
                        "Selecione...": 0.0,
                        "Todos os programas finalísticos do PPA – 15 pts": 15.0,
                        "A maior parte dos programas finalísticos – 10 pts": 10.0,
                        "A menor parte dos programas finalísticos – 05 pts": 5.0,
                        "Nenhum programa finalístico – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1",
                        titulo="Articulação dos Programas Finalísticos",
                        pergunta="Os programas finalísticos articulam um conjunto de ações que concorrem para um objetivo comum preestabelecido, visando à solução de um problema ou necessidade da sociedade?",
                        tipo_input="radio",
                        opcoes=opcoes_41,
                        placeholder_link="Insira o link da estrutura dos programas do PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1.1 (Avaliação da Implementação - Radio)
                    # ==========================================
                    opcoes_411 = {
                        "Selecione...": 0.0,
                        "Sim, para todos os programas finalísticos monitorados – 10 pts": 10.0,
                        "Sim, para a maior parte dos programas finalísticos monitorados – 07 pts": 7.0,
                        "Sim, para a menor parte dos programas finalísticos monitorados – 03 pts": 3.0,
                        "Não houve avaliação – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1.1",
                        titulo="Avaliação da Implementação dos Programas Finalísticos",
                        pergunta="Houve avaliação da implementação dos programas finalísticos em relação a seus indicadores, objetivos e metas?",
                        tipo_input="radio",
                        opcoes=opcoes_411,
                        placeholder_link="Insira o link das avaliações dos programas finalísticos...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1.1.1 (Relatório Anual de Avaliação - Radio)
                    # ==========================================
                    opcoes_4111 = {
                        "Selecione...": 0.0,
                        "Sim, para todos os programas finalísticos do PPA – 07 pts": 7.0,
                        "Sim, para a maior parte dos programas finalísticos – 04 pts": 4.0,
                        "Sim, para a menor parte dos programas finalísticos – 01 pt": 1.0,
                        "Não houve elaboração do Relatório Anual de Avaliação – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1.1.1",
                        titulo="Relatório Anual de Avaliação do PPA",
                        pergunta="Houve a elaboração de Relatório Anual de Avaliação dos programas finalísticos do PPA? (Caso não esteja disponível na internet, recomenda-se anexar o relatório conforme a Instrução de Preenchimento):",
                        tipo_input="radio",
                        opcoes=opcoes_4111,
                        placeholder_link="Insira o link do Relatório Anual de Avaliação do PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1.1.1.1 (Aspectos Analisados no Acompanhamento - Checkbox)
                    # ==========================================
                    opcoes_41111 = {
                        "Selecione...": 0.0,
                        "Percepção de coerência, em todos os programas, do necessário encadeamento lógico-causal entre os insumos mobilizados, os produtos/ações gerados, os resultados provocados e os impactos esperados pela sociedade – 20 pts": 20.0,
                        "Análise quanto a se Programas, Metas e Ações são mensurados por um ou mais indicadores próprios e adequados, permitindo aferir a situação atual e os avanços obtidos – 20 pts": 20.0,
                        "Avaliação entre os produtos ofertados à população e as reais demandas da sociedade, coletadas nas audiências públicas e demais instrumentos de diagnóstico – 20 pts": 20.0,
                        "Outros – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1.1.1.1",
                        titulo="Aspectos Analisados no Acompanhamento e Avaliação do PPA",
                        pergunta="Assinale os aspectos analisados no processo de acompanhamento e avaliação do PPA:",
                        tipo_input="checkbox",
                        opcoes=opcoes_41111,
                        placeholder_link="Insira o link com os documentos comprobatórios das análises e acompanhamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1.1.2 (Publicação dos Resultados - Radio)
                    # ==========================================
                    opcoes_4112 = {
                        "Selecione...": 0.0,
                        "Sim, para todos os programas finalísticos avaliados do PPA – 04 pts": 4.0,
                        "Sim, para a maior parte dos programas finalísticos avaliados – 03 pts": 3.0,
                        "Sim, para a menor parte dos programas finalísticos avaliados – 01 pt": 1.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1.1.2",
                        titulo="Publicação dos Resultados da Avaliação do PPA",
                        pergunta="Houve publicação dos resultados da avaliação dos programas finalísticos do PPA?",
                        tipo_input="radio",
                        opcoes=opcoes_4112,
                        placeholder_link="Insira o link com a publicação das avaliações do PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.1.1.2.1 (Página Eletrônica dos Resultados - Radio)
                    # ==========================================
                    opcoes_41121 = {
                        "Link/Página eletrônica disponível – Pontuação conforme item 4.1.1.2": 0.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.1.1.2.1",
                        titulo="Divulgação Eletrônica da Avaliação",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação dos resultados da avaliação dos programas finalísticos do PPA (Se não estiver disponível na internet, inserir no campo de link o texto 'XYZ'):",
                        tipo_input="radio",
                        opcoes=opcoes_41121,
                        placeholder_link="Cole o link dos resultados ou digite XYZ se não estiver disponível...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.2 (Coerência dos Indicadores - Radio)
                    # ==========================================
                    opcoes_42 = {
                        "Selecione...": 0.0,
                        "Todos os indicadores do PPA – 25 pts": 25.0,
                        "A maior parte dos indicadores – 17 pts": 17.0,
                        "A menor parte dos indicadores – 08 pts": 8.0,
                        "Nenhum indicador – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.2",
                        titulo="Mensuração e Coerência dos Indicadores",
                        pergunta="Os indicadores são mensuráveis e estão coerentes com as metas físico-financeiras estabelecidas?",
                        tipo_input="radio",
                        opcoes=opcoes_42,
                        placeholder_link="Insira o link das tabelas de indicadores do PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 4.3 (Planos Setoriais no PPA - Checkbox)
                    # ==========================================
                    opcoes_43 = {
                        "Plano Municipal da Educação – 2,5 pts": 2.5,
                        "Plano Municipal da Saúde – 2,5 pts": 2.5,
                        "Plano de Saneamento Básico – 2,5 pts": 2.5,
                        "Plano de Resíduos Sólidos – 2,5 pts": 2.5,
                        "Plano de Contingência Municipal – PLANCON de Defesa Civil – 2,5 pts": 2.5,
                        "Plano Diretor de Tecnologia da Informação – 2,5 pts": 2.5,
                        "Plano Diretor – 00 pts": 0.0,
                        "Plano Municipal pela Primeira Infância – 00 pts": 0.0,
                        "Plano de Mobilidade Urbana – 00 pts": 0.0,
                        "Não incorporou nenhum dos planos acima – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="4.3",
                        titulo="Planos Setoriais Incorporados ao PPA",
                        pergunta="Assinale os Planos Setoriais que foram incorporados no Plano Plurianual (PPA):",
                        tipo_input="checkbox",
                        opcoes=opcoes_43,
                        placeholder_link="Insira o link que comprove a incorporação dos planos setoriais no PPA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.0 (Estudo de Previsão de Receita - Radio)
                    # ==========================================
                    opcoes_50 = {
                        "Selecione...": 0.0,
                        "Sim – 06 pts": 6.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.0",
                        titulo="Estudo/Análise para Previsão de Receita",
                        pergunta="É realizado estudo/análise para previsão de receitas, no mínimo, anualmente? (Obs: A simples aplicação de índice inflacionário ao valor arrecadado do exercício anterior NÃO é considerada estudo/análise de previsão de receita):",
                        tipo_input="radio",
                        opcoes=opcoes_50,
                        placeholder_link="Insira o link da metodologia ou estudo de estimativa de receita...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.1 (Tipos de Tributos e Repasses - Checkbox)
                    # ==========================================
                    opcoes_51 = {
                        "Imposto sobre a Propriedade Predial e Territorial Urbano (IPTU) – 0,5 pt": 0.5,
                        "Imposto sobre a Transmissão de Bens Imóveis (ITBI) – 0,5 pt": 0.5,
                        "Imposto Sobre Serviços de Qualquer Natureza (ISSQN) – 0,5 pt": 0.5,
                        "Taxas – 0,25 pt": 0.25,
                        "Contribuições – 0,25 pt": 0.25,
                        "Transferências Obrigatórias Recebidas da União (ex: FPM, CIDE, ITR, Royalties e FUNDEB) – 01 pt": 1.0,
                        "Transferências Obrigatórias Recebidas do Estado (ex: ICMS, IPVA) – 01 pt": 1.0,
                        "Outros – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.1",
                        titulo="Tributos e Transferências na Previsão da Receita",
                        pergunta="Assinale os tipos de tributos e repasses/transferências avaliados na análise e estudo da previsão da receita:",
                        tipo_input="checkbox",
                        opcoes=opcoes_51,
                        placeholder_link="Insira o link com a metodologia ou memória de cálculo por fonte de receita...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.1.1 (Previsão de Repasse do ICMS - Radio)
                    # ==========================================
                    opcoes_511 = {
                        "Selecione...": 0.0,
                        "Sim, com reestimativa da receita prevista na LOA no decorrer da execução orçamentária-financeira – 02 pts": 2.0,
                        "Sim, somente para elaborar a LOA – 01 pt": 1.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.1.1",
                        titulo="Estimativa de Transferências Obrigatórias (ICMS)",
                        pergunta="A estimativa de transferências obrigatórias leva em consideração o cálculo de previsão de repasse do ICMS realizado periodicamente pela Fazenda Pública Estadual?",
                        tipo_input="radio",
                        opcoes=opcoes_511,
                        placeholder_link="Insira o link das memórias de cálculo ou acompanhamentos da Fazenda Estadual...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 5.2 (Metodologia de Projeção por Espécie - Radio)
                    # ==========================================
                    opcoes_52 = {
                        "Selecione...": 0.0,
                        "Sim – 06 pts": 6.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="5.2",
                        titulo="Variabilidade da Metodologia de Projeção",
                        pergunta="A metodologia utilizada para projeção da receita varia de acordo com a espécie da receita orçamentária projetada?",
                        tipo_input="radio",
                        opcoes=opcoes_52,
                        placeholder_link="Insira o link do documento metodológico detalhado por categoria de receita...",
                        on_save_callback=render_conteudo.refresh,
                    )
                    # ==========================================
                    # QUESITO 6.0 (Disposições da LDO - Checkbox)
                    # ==========================================
                    opcoes_60 = {
                        "Custos estimados, indicadores e metas físicas que se correlacionam com as ações do governo municipal – 0,5 pt": 0.5,
                        "Critérios para limitação de empenho e movimentação financeira (ressalvados dívida e inovação/desenvolvimento científico/tecnológico por fundo) – 0,5 pt": 0.5,
                        "Critérios para o Poder Executivo estabelecer a programação financeira mensal para todo o Município, nele incluído a Câmara – 01 pt": 1.0,
                        "Percentual da RCL que será retido na peça orçamentária enquanto Reserva de Contingência – 01 pt": 1.0,
                        "Critérios para contratação de horas extras quando o Poder superar o limite prudencial para pessoal (Executivo: 51,30%; Legislativo: 5,7%) – 0,5 pt": 0.5,
                        "Requisitos para início de novos projetos após adequado atendimento/manutenção dos em andamento – 0,5 pt": 0.5,
                        "Critérios para repasses a entidades do terceiro setor – 00 pts": 0.0,
                        "Critérios para ajuda financeira a entidades da Administração indireta – 00 pts": 0.0,
                        "Determinação do índice de preços para atualização monetária da Dívida Mobiliária Refinanciada – 00 pts": 0.0,
                        "Autorização para o Município auxiliar o custeio de despesas próprias do Estado e da União – 00 pts": 0.0,
                        "Dispor sobre pagamento de servidor com recursos vinculados à parceria com terceiro setor – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="6.0",
                        titulo="Disposições Gerais da LDO",
                        pergunta="Assinale os itens que a LDO dispõe:",
                        tipo_input="checkbox",
                        opcoes=opcoes_60,
                        placeholder_link="Insira o link do texto da LDO aprovada contendo os dispositivos citados...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 7.0 (Alteração Orçamentária por Decreto - Radio)
                    # ==========================================
                    opcoes_70 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.0",
                        titulo="Alterações Orçamentárias por Decreto",
                        pergunta="Houve alteração orçamentária decorrente de remanejamento, transposição ou transferência de uma categoria de programação para outra ou de um órgão para outro por decreto?",
                        tipo_input="radio",
                        opcoes=opcoes_70,
                        placeholder_link="Insira o link dos decretos de alteração orçamentária...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 7.1 (Classificação Funcional Afetada - Checkbox)
                    # ==========================================
                    opcoes_71 = {
                        "Selecione...": 0.0,
                        "10 - Saúde – -05 pts": -5.0,
                        "12 - Educação – -05 pts": -5.0,
                        "17 - Saneamento – -05 pts": -5.0,
                        "19 - Ciência e Tecnologia – 00 pts": 0.0,
                        "26 - Transporte – -05 pts": -5.0,
                        "Outras – -05 pts": -5.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="7.1",
                        titulo="Classificação Funcional das Alterações Orçamentárias",
                        pergunta="Assinale a classificação funcional da despesa, objeto de alterações orçamentárias decorrentes de remanejamento, transposição e transferências realizadas por decreto:",
                        tipo_input="checkbox",
                        opcoes=opcoes_71,
                        placeholder_link="Insira o link dos atos de alteração por função...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.0 (Anexo de Metas Fiscais - Radio)
                    # ==========================================
                    opcoes_80 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.0",
                        titulo="Anexo de Metas Fiscais na LDO",
                        pergunta="O Anexo de Metas Fiscais integra a Lei de Diretrizes Orçamentárias (LDO), nos termos exigidos pela Lei de Responsabilidade Fiscal? (Obs: Estabelecidas metas anuais, em valores correntes e constantes, relativas a receitas, despesas, resultados nominal e primário e montante da dívida pública, para o exercício a que se referirem e para os dois seguintes):",
                        tipo_input="radio",
                        opcoes=opcoes_80,
                        placeholder_link="Insira o link do Anexo de Metas Fiscais na LDO...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 8.1 (Divulgação do Anexo de Metas Fiscais - Radio)
                    # ==========================================
                    # Nota: Validação de link. Se contiver 'XYZ' perde 10 pts (-10.0), caso contrário ganha 0 pts.
                    opcoes_81 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.1",
                        titulo="Divulgação do Anexo de Metas Fiscais na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Anexo de Metas Fiscais (Se não estiver disponível na internet, inserir no campo de link o texto 'XYZ'):",
                        tipo_input="radio",
                        opcoes=opcoes_81,
                        placeholder_link="Cole o link do Anexo de Metas Fiscais ou digite XYZ se não estiver disponível...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 8.2 (Demonstrativos do Anexo de Metas Fiscais - Checkbox)
                    # ==========================================
                    opcoes_82 = {
                        "Metas Anuais – 0,7 pt": 0.7,
                        "Avaliação do Cumprimento das Metas Fiscais do Exercício Anterior – 0,7 pt": 0.7,
                        "Metas Fiscais Atuais Comparadas com as Metas Fiscais Fixadas nos três exercícios anteriores – 0,7 pt": 0.7,
                        "Evolução do Patrimônio Líquido – 0,7 pt": 0.7,
                        "Origem e Aplicação dos Recursos Obtidos com a Alienação de Ativos – 00 pts": 0.0,
                        "Avaliação da Situação Financeira e Atuarial do RPPS – 00 pts": 0.0,
                        "Estimativa e Compensação da Renúncia de Receita – 00 pts": 0.0,
                        "Margem de Expansão das Despesas Obrigatórias de Caráter Continuado – 1,2 pt": 1.2,
                        "Outros – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="8.2",
                        titulo="Demonstrativos do Anexo de Metas Fiscais",
                        pergunta="Assinale os demonstrativos contidos no Anexo de Metas Fiscais:",
                        tipo_input="checkbox",
                        opcoes=opcoes_82,
                        placeholder_link="Insira o link dos demonstrativos do Anexo de Metas Fiscais...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.0 (Anexo de Riscos Fiscais - Radio)
                    # ==========================================
                    opcoes_90 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.0",
                        titulo="Anexo de Riscos Fiscais na LDO",
                        pergunta="O Anexo de Riscos Fiscais integra a Lei de Diretrizes Orçamentárias (LDO), nos termos exigidos pela Lei de Responsabilidade Fiscal? (Avalia os passivos contingentes e outros riscos capazes de afetar as contas públicas, informando as providências a serem tomadas, caso se concretizem):",
                        tipo_input="radio",
                        opcoes=opcoes_90,
                        placeholder_link="Insira o link do Anexo de Riscos Fiscais na LDO...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.1 (Divulgação do Anexo de Riscos Fiscais - Radio)
                    # ==========================================
                    # Nota: Validação de link. Se contiver 'XYZ' perde 10 pts (-10.0), caso contrário ganha 0 pts.
                    opcoes_91 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.1",
                        titulo="Divulgação do Anexo de Riscos Fiscais na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Anexo de Riscos Fiscais (Se não estiver disponível na internet, inserir no campo de link o texto 'XYZ'):",
                        tipo_input="radio",
                        opcoes=opcoes_91,
                        placeholder_link="Cole o link do Anexo de Riscos Fiscais ou digite XYZ se não estiver disponível...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 9.2 (Gerenciamento de Riscos Fiscais - Checkbox)
                    # ==========================================
                    opcoes_92 = {
                        "Identificação do tipo de risco e da exposição ao risco – 0,5 pt": 0.5,
                        "Mensuração ou quantificação dessa exposição – 0,5 pt": 0.5,
                        "Estimativa do grau de tolerância das contas públicas ao comportamento frente ao risco – 0,5 pt": 0.5,
                        "Decisão estratégica sobre as opções para enfrentar o risco – 0,5 pt": 0.5,
                        "Implementação de condutas de mitigação do risco e de mecanismos de controle para prevenir perdas decorrentes do risco – 0,5 pt": 0.5,
                        "Monitoramento contínuo da exposição ao longo do tempo, preferencialmente através de sistemas institucionalizados (Controle Interno) – 01 pt": 1.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="9.2",
                        titulo="Etapas para Gerenciamento dos Riscos Fiscais",
                        pergunta="Assinale as etapas para gerenciamento dos riscos contidas no Anexo de Riscos Fiscais:",
                        tipo_input="checkbox",
                        opcoes=opcoes_92,
                        placeholder_link="Insira o link das etapas de gerenciamento de riscos...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 10.0 (Compatibilidade LOA, PPA e LDO - Checkbox)
                    # ==========================================
                    opcoes_100 = {
                        "Programas constantes do PPA constam na LOA – 01 pt": 1.0,
                        "Programas e ações constantes da LDO constam da LOA – 02 pts": 2.0,
                        "As receitas e despesas da LOA são compatíveis com o Resultado Primário da LDO, incluindo, no máximo, a variação da inflação do interregno temporal dos referidos projetos de lei – 02 pts": 2.0,
                        "O Resultado Nominal constante da LDO consta da LOA, com variação de no máximo a variação da inflação do interregno temporal dos referidos projetos de lei – 02 pts": 2.0,
                        "A estimativa de renúncia fiscal prevista na LDO coincide com o estimado na LOA com variação limitada à variação da inflação – 02 pts": 2.0,
                        "A estimativa de receita e respectivos critérios presentes na LOA são compatíveis com os previstos na LDO em relação à receita de IPTU – 02 pts": 2.0,
                        "A estimativa de receita e respectivos critérios presentes na LOA são compatíveis com os previstos na LDO em relação à receita de ISSQN – 02 pts": 2.0,
                        "A estimativa de receita e respectivos critérios presentes na LOA são compatíveis com os previstos na LDO em relação à receita de ITBI – 02 pts": 2.0,
                        "Os investimentos, parte das despesas de capital, previstas na LOA e LDO são compatíveis com as previsões do PPA – 02 pts": 2.0,
                        "A LDO e a LOA não são compatíveis com o PPA – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="10.0",
                        titulo="Compatibilidade entre LOA, PPA e LDO",
                        pergunta="Assinale os itens capazes de atestar a compatibilidade entre a LOA, PPA e LDO:",
                        tipo_input="checkbox",
                        opcoes=opcoes_100,
                        placeholder_link="Insira o link demonstrando a compatibilidade entre LOA, PPA e LDO...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 11.0 (Previsão na LOA - Radio)
                    # ==========================================
                    opcoes_110 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="11.0",
                        titulo="Previsão na Lei Orçamentária Anual (LOA)",
                        pergunta="Na Lei Orçamentária Anual (LOA), há previsão para:",
                        tipo_input="radio",
                        opcoes=opcoes_110,
                        placeholder_link="Insira o link do trecho correspondente na LOA...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 11.1 (Percentual de Crédito Adicional Suplementar na LOA - Radio)
                    # ==========================================
                    # Nota: Se % alteração <= inflação ganha -6 pts (perde 6), se > inflação ganha 0 pts.
                    opcoes_111 = {
                        "Selecione...": 0.0,
                        "Percentual <= Inflação – -06 pts": -6.0,
                        "Percentual > Inflação – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="11.1",
                        titulo="Percentual Autorizado para Crédito Adicional Suplementar",
                        pergunta="Qual o percentual autorizado na Lei Orçamentária Anual (LOA) para abertura de crédito adicional suplementar?",
                        tipo_input="radio",
                        opcoes=opcoes_111,
                        placeholder_link="Insira o link ou informe o percentual autorizado na LOA...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.0 (Estrutura Administrativa de Planejamento - Radio)
                    # ==========================================
                    opcoes_120 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.0",
                        titulo="Estrutura Administrativa para Planejamento",
                        pergunta="Há estrutura administrativa voltada para planejamento?",
                        tipo_input="radio",
                        opcoes=opcoes_120,
                        placeholder_link="Insira o link da lei de estrutura administrativa...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1 (Recursos Humanos para Planejamento - Radio)
                    # ==========================================
                    opcoes_121 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.1",
                        titulo="Recursos Humanos para Planejamento",
                        pergunta="A prefeitura dispõe de recursos humanos para operacionalização das atividades de planejamento?",
                        tipo_input="radio",
                        opcoes=opcoes_121,
                        placeholder_link="Insira o link com comprovação do quadro de pessoal...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1.1 (Qualificação Técnica da Equipe - Radio)
                    # ==========================================
                    opcoes_1211 = {
                        "Selecione...": 0.0,
                        "Sim, todos os servidores possuem qualificação técnica – 00 pts": 0.0,
                        "Sim, a maior parte dos servidores possuem qualificação técnica – -05 pts": -5.0,
                        "Sim, a menor parte dos servidores possuem qualificação técnica – -08 pts": -8.0,
                        "Não – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.1.1",
                        titulo="Qualificação Técnica da Equipe de Planejamento",
                        pergunta="Os servidores da equipe de planejamento possuem qualificação técnica para o exercício das atividades de planejamento, gestão e orçamento?",
                        tipo_input="radio",
                        opcoes=opcoes_1211,
                        placeholder_link="Insira o link dos currículos ou comprovantes de qualificação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 12.1.2 (Treinamento Específico da Equipe - Radio)
                    # ==========================================
                    opcoes_1212 = {
                        "Selecione...": 0.0,
                        "Sim (Treinamento periódico pelo menos 1 vez ao ano) – 00 pts": 0.0,
                        "Não – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="12.1.2",
                        titulo="Treinamento Específico em Planejamento",
                        pergunta="Os servidores responsáveis pelo planejamento recebem treinamento específico para a matéria (Treinamento periódico pelo menos 1 vez ao ano)?",
                        tipo_input="radio",
                        opcoes=opcoes_1212,
                        placeholder_link="Insira o link dos certificados/comprovantes de treinamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

    render_conteudo()
