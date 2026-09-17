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

    render_conteudo()
