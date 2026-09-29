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
    opcoes=None,
    tipo_input="radio",
    placeholder_link="Insira o link da evidência...",
    on_save_callback=None,
):
    if opcoes is None:
        opcoes = {}

    dados_q = res_data.get(qid, {})
    link_atual = dados_q.get("link", "")

    # Trata valor inicial conforme o tipo de input
    if tipo_input == "checkbox":
        valor_bruto = dados_q.get("valor", [])
        if isinstance(valor_bruto, list):
            valor_atual = valor_bruto
        elif valor_bruto in opcoes:
            valor_atual = [valor_bruto]
        else:
            valor_atual = []
    elif tipo_input in ["number", "float"]:
        valor_bruto = dados_q.get("valor", 0.0)
        try:
            valor_atual = float(valor_bruto)
        except (ValueError, TypeError):
            valor_atual = 0.0
    elif tipo_input == "text":
        valor_atual = str(dados_q.get("valor", ""))
    else:  # radio
        primeira_opcao_valida = list(opcoes.keys())[0] if opcoes else ""
        valor_atual = dados_q.get("valor", primeira_opcao_valida)
        if valor_atual not in opcoes and opcoes:
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
            # Lado Esquerdo: Checkboxes, Radio, Input Numérico ou Texto
            if tipo_input == "checkbox":
                with ui.column().classes("gap-2 w-full"):
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

            elif tipo_input in ["number", "float"]:
                # Campo numérico de 0 a 250 pontos
                input_num = (
                    ui.number(
                        label="Pontuação do Quesito (0 a 250):",
                        value=state["opcao"],
                        min=0,
                        max=250,
                        step=0.1,
                    )
                    .classes("w-full")
                    .props("outlined color=blue")
                    .bind_value(state, "opcao")
                )

            elif tipo_input == "text":
                # Campo para textos/respostas dissertativas
                ui.textarea(
                    label="Resposta / Comentário:",
                    value=state["opcao"],
                    placeholder="Digite sua resposta...",
                ).classes("w-full").props("outlined rows=4").bind_value(
                    state, "opcao"
                )

            else:  # radio
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
            if tipo_input in ["number", "float"]:
                try:
                    return float(opcao_sel)
                except (ValueError, TypeError):
                    return 0.0
            elif tipo_input == "text":
                return 0.0
            elif isinstance(opcao_sel, list):
                return sum(opcoes.get(opt, 0.0) for opt in opcao_sel)
            return opcoes.get(opcao_sel, 0.0)

        pts_atuais = calcular_pontos(state["opcao"])
        label_impacto = ui.label(
            f"📊 Impacto de Pontuação no Quesito {qid}: {pts_atuais:.1f} pontos"
        ).classes("text-sm font-bold text-green-600 my-4")

        def atualizar_impacto(e=None):
            novos_pts = calcular_pontos(state["opcao"])
            label_impacto.set_text(
                f"📊 Impacto de Pontuação no Quesito {qid}: {novos_pts:.1f} pontos"
            )

        # Eventos para atualização em tempo real
        if tipo_input == "radio":
            input_radio.on("update:model-value", atualizar_impacto)
        elif tipo_input in ["number", "float"]:
            input_num.on("update:model-value", atualizar_impacto)

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
                        "Sim – 00 pts": 0.0,
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
                        "Sim – 00 pts": 0.0,
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
                        "Sim – 14 pts": 14.0,
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
                    # QUESITO 11.1 (Percentual de Crédito Adicional Suplementar na LOA)
                    # ==========================================
                    import json
                    import ast

                    raw_q111 = res_data.get("11.1") or res_data.get(11.1) or {}
                    q111_data = raw_q111 if isinstance(raw_q111, dict) else {}

                    # Extrai e converte o campo "valor" do banco (seja dict, string JSON ou string dict do Python)
                    raw_val = q111_data.get("valor", {})
                    val_salvo = {}

                    if isinstance(raw_val, dict):
                        val_salvo = raw_val
                    elif isinstance(raw_val, str) and raw_val.strip():
                        try:
                            # Primeiro tenta converter como JSON
                            val_salvo = json.loads(raw_val)
                        except Exception:
                            try:
                                # Se falhar (por conta de aspas simples do banco), converte via ast.literal_eval
                                val_salvo = ast.literal_eval(raw_val)
                            except Exception:
                                val_salvo = {}

                    # Garante extração dos valores convertidos
                    p_aut = float(val_salvo.get("perc_autorizado", 0.0)) if isinstance(val_salvo, dict) else 0.0
                    i_per = float(val_salvo.get("inflacao_periodo", 0.0)) if isinstance(val_salvo, dict) else 0.0

                    state_111 = {
                        "perc_autorizado": p_aut,
                        "inflacao_periodo": i_per,
                        "link": str(q111_data.get("link", "") or ""),
                        "pts": float(q111_data.get("pontos", 0.0))
                    }

                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("11.1 • Percentual Autorizado para Crédito Adicional Suplementar").classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label("Análise do percentual autorizado na Lei Orçamentária Anual (LOA) para abertura de crédito adicional suplementar em comparação com a inflação do período:").classes("text-base font-bold text-black mb-2")
                        
                        # Bloco Informativo de Regras
                        with ui.expansion("ℹ️ Regras de Pontuação e Cálculo Automático", icon="info").classes("w-full mb-4 bg-gray-50 border border-gray-200 rounded"):
                            ui.markdown("""
                            * **Percentual Autorizado <= Inflação do Período:** Ganha **6,0 pontos**
                            * **Percentual Autorizado > Inflação do Período:** Ganha **0,0 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        # Seção da Calculadora
                        with ui.card().classes("w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"):
                            ui.label("🧮 Calculadora Automática do Quesito 11.1").classes("font-bold text-blue-700 mb-2")
                            
                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_perc_autorizado = (
                                    ui.number(label="Percentual Autorizado na LOA (%)", value=state_111["perc_autorizado"], format="%.2f")
                                    .classes("w-full")
                                    .props("outlined bg-white suffix='%'")
                                )
                                input_inflacao = (
                                    ui.number(label="Inflação do Período (%)", value=state_111["inflacao_periodo"], format="%.2f")
                                    .classes("w-full")
                                    .props("outlined bg-white suffix='%'")
                                )

                            lbl_resultado_comp = ui.label().classes("text-sm font-bold text-gray-800 mt-2")
                            lbl_pts_111 = ui.label().classes("text-sm font-bold text-green-600 mt-1")

                            def calcular_111(_=None):
                                perc_aut = float(input_perc_autorizado.value or 0.0)
                                inflacao = float(input_inflacao.value or 0.0)
                                
                                state_111["perc_autorizado"] = perc_aut
                                state_111["inflacao_periodo"] = inflacao

                                # Regra: Se % Autorizado <= Inflação -> 6.0 pontos, senão 0.0
                                if perc_aut <= inflacao:
                                    pts = 6.0
                                    lbl_resultado_comp.set_text(f"Resultado: Percentual Autorizado ({perc_aut:.2f}%) é MENOR ou IGUAL à Inflação ({inflacao:.2f}%)")
                                else:
                                    pts = 0.0
                                    lbl_resultado_comp.set_text(f"Resultado: Percentual Autorizado ({perc_aut:.2f}%) é MAIOR que a Inflação ({inflacao:.2f}%)")
                                
                                state_111["pts"] = pts
                                lbl_pts_111.set_text(f"📊 Pontuação Calculada: {pts:.1f} pontos")

                            input_perc_autorizado.on("update:model-value", calcular_111)
                            input_inflacao.on("update:model-value", calcular_111)
                            calcular_111()

                        # Campo de Evidência / Link
                        input_link_111 = ui.textarea(
                            label="Link de Evidência / Documento / Fonte da Inflação:",
                            value=state_111["link"],
                            placeholder="Insira o link com a LOA e a fonte do índice de inflação utilizado..."
                        ).classes("w-full mb-4").props("outlined rows=3")

                        # Ação de Salvamento
                        def salvar_111():
                            dict_valor = {
                                "perc_autorizado": state_111["perc_autorizado"],
                                "inflacao_periodo": state_111["inflacao_periodo"]
                            }
                            
                            # Atualiza a memória local exatamente no formato salvo
                            res_data["11.1"] = {
                                "valor": dict_valor,
                                "pontos": state_111["pts"],
                                "link": input_link_111.value,
                                "comentarios": q111_data.get("comentarios", []),
                                "status": q111_data.get("status", "Pendente")
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="11.1",
                                valor=dict_valor,
                                pontos=state_111["pts"],
                                link=input_link_111.value,
                                comentarios=q111_data.get("comentarios", []),
                                status=q111_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 11.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_111).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("11.1", res_data, render_conteudo.refresh)
                        
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
                        "Sim  – 00 pts": 0.0,
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

    # ==========================================
                    # QUESITO 13.0 (Acompanhamento da Execução do Planejamento - Radio)
                    # ==========================================
                    opcoes_130 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.0",
                        titulo="Acompanhamento da Execução do Planejamento",
                        pergunta="Há acompanhamento da execução do planejamento?",
                        tipo_input="radio",
                        opcoes=opcoes_130,
                        placeholder_link="Insira o link com evidências do acompanhamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.1 (Audiências Públicas Quadrimestrais - Checkbox)
                    # ==========================================
                    opcoes_131 = {
                        "Realizou Audiência pública do 1º Quadrimestre até o final do mês de maio de 2025 – 02 pts": 2.0,
                        "Realizou Audiência pública do 2º Quadrimestre até o final do mês de setembro de 2025 – 02 pts": 2.0,
                        "Realizou Audiência pública do 3º Quadrimestre até o final do mês de fevereiro de 2026 – 02 pts": 2.0,
                        "Não realizou audiência pública quadrimestral dentro do prazo – 00 pts": 0.0,
                        "Não realizou nenhuma audiência pública quadrimestral na Câmara Municipal – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.1",
                        titulo="Audiências Públicas Quadrimestrais das Metas Fiscais",
                        pergunta="A prefeitura demonstra e avalia, com periodicidade quadrimestral, o cumprimento das metas fiscais em audiências públicas? (Art. 9º, § 4º, da LRF)",
                        tipo_input="checkbox",
                        opcoes=opcoes_131,
                        placeholder_link="Insira o link das atas/convocações das audiências públicas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.1.1 (Relatórios Quadrimestrais das Metas Fiscais - Checkbox)
                    # ==========================================
                    opcoes_1311 = {
                        "Relatório da Audiência pública do 1º Quadrimestre – 01 pt": 1.0,
                        "Relatório da Audiência pública do 2º Quadrimestre – 01 pt": 1.0,
                        "Relatório da Audiência pública do 3º Quadrimestre – 01 pt": 1.0,
                        "Não elaborou relatório de nenhuma audiência pública quadrimestral – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.1.1",
                        titulo="Relatórios Quadrimestrais das Metas Fiscais",
                        pergunta="Foram elaborados os Relatórios Quadrimestrais das metas fiscais para as audiências públicas?",
                        tipo_input="checkbox",
                        opcoes=opcoes_1311,
                        placeholder_link="Insira o link dos relatórios quadrimestrais elaborados...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.1.1.1 (Divulgação dos Relatórios Quadrimestrais - Radio)
                    # ==========================================
                    # Nota: Se XYZ ganha 0 pts, se for diferente de XYZ (<> XYZ) ganha 2 pts.
                    opcoes_13111 = {
                        "Link/Página eletrônica disponível (<> XYZ) – 02 pts": 2.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.1.1.1",
                        titulo="Divulgação dos Relatórios Quadrimestrais de Metas Fiscais",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação dos Relatórios Quadrimestrais de Metas Fiscais (Se não estiver disponível na internet, inserir no campo de link o texto 'XYZ'):",
                        tipo_input="radio",
                        opcoes=opcoes_13111,
                        placeholder_link="Cole o link dos Relatórios Quadrimestrais ou digite XYZ se não estiver disponível...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 13.2 (Acompanhamento Mensal com o Prefeito - Radio)
                    # ==========================================
                    opcoes_132 = {
                        "Selecione...": 0.0,
                        "Sim – 04 pts": 4.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.2",
                        titulo="Acompanhamento Mensal com Participação do Prefeito",
                        pergunta="Houve acompanhamento mensal da execução orçamentária com participação do Prefeito?",
                        tipo_input="radio",
                        opcoes=opcoes_132,
                        placeholder_link="Insira o link das atas de reunião ou comprovações do acompanhamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 13.3 (Retroalimentação do Replanejamento - Radio)
                    # ==========================================
                    opcoes_133 = {
                        "Selecione...": 0.0,
                        "Sim, com emissão de relatórios e ciência do prefeito – 20 pts": 20.0,
                        "Sim, com emissão de relatório e sem ciência do prefeito – 10 pts": 10.0,
                        "Sim, sem emissão de relatório e sem ciência do prefeito – 05 pts": 5.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="13.3",
                        titulo="Retroalimentação para Replanejamento Orçamentário",
                        pergunta="O acompanhamento e avaliação da execução orçamentária serve de retroalimentação para o replanejamento dos programas e metas das peças orçamentárias?",
                        tipo_input="radio",
                        opcoes=opcoes_133,
                        placeholder_link="Insira o link com evidências/relatórios de replanejamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.0 (Regulamentação do SCI - Radio)
                    # ==========================================
                    opcoes_140 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.0",
                        titulo="Instituição do Sistema de Controle Interno",
                        pergunta="Houve a instituição e regulamentação das operações do Sistema de Controle Interno?",
                        tipo_input="radio",
                        opcoes=opcoes_140,
                        placeholder_link="Insira o link da norma de criação/regulamentação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.1 (Instrumento Normativo do SCI - Text/Info)
                    # ==========================================
                    opcoes_141 = {
                        "Selecione...": 0.0,
                        "Instrumento informado/anexado – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.1",
                        titulo="Instrumento Normativo de Regulamentação do SCI",
                        pergunta="Informe o instrumento normativo de regulamentação do Sistema de Controle Interno, Número e Data da publicação:",
                        tipo_input="radio",
                        opcoes=opcoes_141,
                        placeholder_link="Informe o número, data da publicação ou insira o link da norma...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.2 (Divulgação do Instrumento do SCI - Text/Radio)
                    # ==========================================
                    opcoes_142 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.2",
                        titulo="Divulgação do Instrumento de Regulamentação do SCI",
                        pergunta="Página eletrônica (link na internet) de divulgação do instrumento de regulamentação do sistema de controle interno (Se não estiver disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_142,
                        placeholder_link="Cole o link de divulgação ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.3 (Funções do Sistema de Controle Interno - Checkbox)
                    # ==========================================
                    opcoes_143 = {
                        "Avaliar o cumprimento das metas físicas e financeiras dos planos orçamentários, bem como a eficiência de seus resultados – 01 pt": 1.0,
                        "Comprovar a legalidade da gestão orçamentária, financeira e patrimonial – 01 pt": 1.0,
                        "Comprovar a legalidade dos repasses a entidades do terceiro setor, avaliando a eficácia e a eficiência dos resultados alcançados – 01 pt": 1.0,
                        "Exercer o controle das operações de crédito, avais e garantias, bem como dos direitos e haveres do Município – 01 pt": 1.0,
                        "Em conjunto com autoridades da Administração Financeira do Município, assinar o Relatório de Gestão Fiscal – 01 pt": 1.0,
                        "Atestar a regularidade da tomada de contas dos ordenadores de despesa, recebedores, tesoureiros, pagadores ou assemelhados – 01 pt": 1.0,
                        "Apoiar o Tribunal de Contas no exercício de sua missão institucional – 01 pt": 1.0,
                        "Comprovar a eficácia e a eficiência da gestão orçamentária, financeira e patrimonial – 01 pt": 1.0,
                        "Acompanhar as metas de superávit orçamentário, primário e nominal – 01 pt": 1.0,
                        "Observar se as operações de créditos sujeitam-se aos limites e condições das Resoluções 40 e 43/2001, do Senado – 01 pt": 1.0,
                        "Verificar se os empréstimos e financiamentos vêm sendo pagos tal qual previsto nos respectivos contratos – 01 pt": 1.0,
                        "Verificar se está sendo providenciada a recondução da despesa de pessoal e da dívida consolidada a seus limites fiscais – 01 pt": 1.0,
                        "Comprovar se os recursos da alienação de ativos estão sendo despendidos em gastos de capital e, não, em despesas correntes – 01 pt": 1.0,
                        "Constatar se está sendo satisfeito o limite para gastos totais das Câmaras Municipais – 01 pt": 1.0,
                        "Verificar a fidelidade funcional dos responsáveis por bens e valores públicos – 01 pt": 1.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.3",
                        titulo="Funções Atribuídas ao Sistema de Controle Interno",
                        pergunta="Assinale as funções atribuídas ao sistema de controle interno:",
                        tipo_input="checkbox",
                        opcoes=opcoes_143,
                        placeholder_link="Insira o link do normativo com a atribuição de funções...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4 (Recursos Humanos para o SCI - Radio)
                    # ==========================================
                    opcoes_144 = {
                        "Selecione...": 0.0,
                        "Sim – 0,5 pt": 0.5,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4",
                        titulo="Recursos Humanos para o Controle Interno",
                        pergunta="A prefeitura dispõe de recursos humanos para operacionalização das atividades do sistema de controle interno?",
                        tipo_input="radio",
                        opcoes=opcoes_144,
                        placeholder_link="Insira o link da composição do quadro do Controle Interno...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.1 (Vínculo do Responsável pela UCCI - Radio)
                    # ==========================================
                    opcoes_1441 = {
                        "Selecione...": 0.0,
                        "Sim (Ocupa cargo efetivo) – 05 pts": 5.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.1",
                        titulo="Cargo Efetivo do Responsável pela UCCI",
                        pergunta="O responsável pela Unidade Central de Controle Interno (UCCI / Controlador Interno ou Geral) ocupa cargo efetivo na Administração Municipal?",
                        tipo_input="radio",
                        opcoes=opcoes_1441,
                        placeholder_link="Insira o link do ato de nomeação/comprovação de vínculo efetivo...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 14.4.2 (Treinamento do Quadro do SCI - Radio)
                    # ==========================================
                    opcoes_1442 = {
                        "Selecione...": 0.0,
                        "Sim (Treinamento periódico pelo menos 1 vez ao ano) – 06 pts": 6.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.2",
                        titulo="Treinamento Específico da Equipe do SCI",
                        pergunta="O quadro funcional do Sistema de Controle Interno recebe treinamento específico para execução das atividades inerentes ao cargo (Periodicidade mínima de 1 vez ao ano)?",
                        tipo_input="radio",
                        opcoes=opcoes_1442,
                        placeholder_link="Insira o link dos comprovantes/certificados de treinamento...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.3 (Segregação de Funções - Radio)
                    # ==========================================
                    opcoes_1443 = {
                        "Selecione...": 0.0,
                        "Sim – 05 pts": 5.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.3",
                        titulo="Segregação de Funções Financeiras e de Controle",
                        pergunta="Na Prefeitura existe formalização da segregação de funções financeiras e de controle?",
                        tipo_input="radio",
                        opcoes=opcoes_1443,
                        placeholder_link="Insira o link do ato normativo ou organograma que formaliza a segregação...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.4 (Autonomia e Independência da UCCI - Radio)
                    # ==========================================
                    opcoes_1444 = {
                        "Selecione...": 0.0,
                        "Sim – 06 pts": 6.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.4",
                        titulo="Autonomia e Independência da UCCI",
                        pergunta="A Unidade Central de Controle Interno (UCCI) possui autonomia e independência para o exercício de suas funções?",
                        tipo_input="radio",
                        opcoes=opcoes_1444,
                        placeholder_link="Insira o link do regimento interno ou norma que ateste a autonomia...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.4.1 (Subordinação da UCCI - Radio)
                    # ==========================================
                    opcoes_14441 = {
                        "Selecione...": 0.0,
                        "Gabinete do Prefeito – 00 pts": 0.0,
                        "Administração – -06 pts": -6.0,
                        "Finanças/Fazenda – -06 pts": -6.0,
                        "Planejamento/Orçamento/Gestão – -06 pts": -6.0,
                        "Outra – -06 pts": -6.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.4.1",
                        titulo="Subordinação Hierárquica da UCCI",
                        pergunta="A estrutura organizacional da Unidade Central de Controle Interno (UCCI) está associada ou subordinada a qual secretaria/diretoria?",
                        tipo_input="radio",
                        opcoes=opcoes_14441,
                        placeholder_link="Insira o link do organograma ou lei de estrutura...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.4.2 (Comunicação de Irregularidade em 2025 - Radio)
                    # ==========================================
                    opcoes_14442 = {
                        "Selecione...": 0.0,
                        "Sim, houve comunicação da irregularidade ou ilegalidade – 00 pts": 0.0,
                        "Houve irregularidade ou ilegalidade, mas não procedeu a comunicação – -03 pts": -3.0,
                        "Não houve irregularidades nem ilegalidades – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.4.2",
                        titulo="Comunicação de Irregularidades em 2025",
                        pergunta="A Unidade Central de Controle Interno (UCCI) procedeu com alguma comunicação de irregularidade ou ilegalidade em 2025?",
                        tipo_input="radio",
                        opcoes=opcoes_14442,
                        placeholder_link="Insira o link com comprovação das comunicações expedidas...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.4.2.1 (Quantidade de Comunicações ao TCESP e MPSP - Radio/Text)
                    # ==========================================
                    opcoes_144421 = {
                        "Selecione...": 0.0,
                        "Informado / Sem pontuação aplicada – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.4.2.1",
                        titulo="Quantidade de Irregularidades Comunicadas (TCESP / MPSP)",
                        pergunta="Informe a quantidade de irregularidades ou ilegalidades comunicadas ao Tribunal de Contas do Estado de São Paulo (TCESP) e ao Ministério Público do Estado de São Paulo (MPSP):",
                        tipo_input="radio",
                        opcoes=opcoes_144421,
                        placeholder_link="Informe os quantitativos (ex: TCESP: X, MPSP: Y) e insira o link...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.5 (Relatórios Periódicos da UCCI - Radio)
                    # ==========================================
                    opcoes_1445 = {
                        "Selecione...": 0.0,
                        "Sim (Periodicidade mínima anual) – 05 pts": 5.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.5",
                        titulo="Apresentação de Relatórios Periódicos da UCCI",
                        pergunta="O responsável pela Unidade Central de Controle Interno (UCCI) apresentou relatórios periódicos que demonstram efetivo exercício de suas atribuições (Periodicidade mínima anual)?",
                        tipo_input="radio",
                        opcoes=opcoes_1445,
                        placeholder_link="Insira o link dos relatórios periódicos apresentados...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 14.4.5.1 (Providências do Prefeito - Radio)
                    # ==========================================
                    opcoes_14451 = {
                        "Selecione...": 0.0,
                        "Sim - de todos os apontamentos – 06 pts": 6.0,
                        "Sim - de parte dos apontamentos – 02 pts": 2.0,
                        "Não – 00 pts": 0.0,
                        "Não foram relatadas irregularidades – 06 pts": 6.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.5.1",
                        titulo="Providências do Prefeito Diante dos Apontamentos do Controle Interno",
                        pergunta="Com base no relatório do Controle Interno, o Prefeito determinou as providências cabíveis diante das irregularidades e ilegalidades apontadas?",
                        tipo_input="radio",
                        opcoes=opcoes_14451,
                        placeholder_link="Insira o link com despachos, determinações ou atos do Prefeito...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.4.5.1.1 (Acompanhamento das Medidas pelo SCI - Radio)
                    # ==========================================
                    opcoes_144511 = {
                        "Selecione...": 0.0,
                        "Sim - de todas as providências determinadas pelo Prefeito – 00 pts": 0.0,
                        "Sim - de parte das providências determinadas pelo Prefeito – 00 pts": 0.0,
                        "Não – -03 pts": -3.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.4.5.1.1",
                        titulo="Acompanhamento das Medidas e Prazos pelo Controle Interno",
                        pergunta="O Controle Interno acompanhou as medidas e os prazos das providências determinadas pelo Prefeito diante dos apontamentos do relatório do Controle Interno?",
                        tipo_input="radio",
                        opcoes=opcoes_144511,
                        placeholder_link="Insira o link das planilhas/relatórios de acompanhamento de providências...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.5 (Elaboração do Plano Operativo Anual - Radio)
                    # ==========================================
                    opcoes_145 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.5",
                        titulo="Elaboração do Plano Operativo Anual do Controle Interno",
                        pergunta="Houve a elaboração de Plano Operativo Anual? (Obs.: Planejamento das atividades a serem executadas no exercício seguinte)",
                        tipo_input="radio",
                        opcoes=opcoes_145,
                        placeholder_link="Insira o link do Plano Operativo Anual elaborado...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 14.5.1 (Atividades no Plano Operativo Anual - Checkbox)
                    # ==========================================
                    # Regra de cálculo: 1 a 5 opções = 1.0 pt; 6 a 10 opções = 3.0 pts; >= 11 opções = 5.0 pts.
                    import json
                    import ast

                    raw_1451 = res_data.get("14.5.1") or res_data.get(14.51) or {}
                    q1451_data = raw_1451 if isinstance(raw_1451, dict) else {}

                    # Trata o valor recuperado do banco (pode vir como lista ou string)
                    raw_val_1451 = q1451_data.get("valor", [])
                    
                    if isinstance(raw_val_1451, str) and raw_val_1451.strip():
                        try:
                            marcados_salvos = json.loads(raw_val_1451)
                        except Exception:
                            try:
                                marcados_salvos = ast.literal_eval(raw_val_1451)
                            except Exception:
                                marcados_salvos = []
                    elif isinstance(raw_val_1451, list):
                        marcados_salvos = raw_val_1451
                    else:
                        marcados_salvos = []

                    lista_opcoes_1451 = [
                        "Receitas",
                        "Despesas",
                        "Administração de pessoal",
                        "Estoques e almoxarifados",
                        "Administração do patrimônio",
                        "Cumprimento das metas do PPA e a execução dos programas de governo e dos orçamentos (LOA e LDO)",
                        "Cumprimento das metas fiscais, físicas e de resultados dos programas de governo, no que tange a eficiência, eficácia e efetividade",
                        "Aplicação de recursos públicos por entidades de direito público",
                        "Aplicação de recursos públicos por entidades de direito privado",
                        "Os limites e condições para a inscrição de despesas em Restos a Pagar",
                        "Cumprimento da legislação de licitações e fiscalização de contratos",
                        "Cumprimento do limite de gastos totais dos legislativos municipais, inclusive no que se refere ao atingimento de metas fiscais (Gestão Fiscal)",
                        "Transferência para o Legislativo Municipal (Repasses de Duodécimos)",
                        "Contabilidade",
                        "Transparência",
                        "Lei de Acesso à Informação",
                        "Outros",
                    ]

                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("14.5.1 • Atividades Previstas no Plano Operativo Anual").classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label("Assinale as atividades previstas no Plano Operativo Anual:").classes("text-base font-bold text-black mb-2")
                        
                        # Bloco de Regras
                        with ui.expansion("ℹ️ Regras de Pontuação (Faixa por quantidade de itens)", icon="info").classes("w-full mb-4 bg-gray-50 border border-gray-200 rounded"):
                            ui.markdown("""
                            * **Nenhum item assinalado:** **0,0 ponto**
                            * **1 a 5 itens assinalados:** **1,0 ponto**
                            * **6 a 10 itens assinalados:** **3,0 pontos**
                            * **11 ou mais itens assinalados:** **5,0 pontos**
                            """).classes("text-sm text-gray-700 p-2")

                        # Dicionário para armazenar a referência das instâncias de ui.checkbox
                        checkboxes_dict = {}
                        
                        lbl_qtd_1451 = ui.label().classes("text-sm font-bold text-gray-800 mt-2")
                        lbl_pts_1451 = ui.label().classes("text-sm font-bold text-green-600 mt-1")
                        state_1451 = {"pts": float(q1451_data.get("pontos", 0.0))}

                        def calcular_1451(_=None):
                            # Filtra as opções que estão marcadas (value == True)
                            marcados = [op for op, cb in checkboxes_dict.items() if cb.value]
                            qtd = len(marcados)
                            
                            # Regra de cálculo por Faixa
                            if qtd == 0:
                                pts = 0.0
                            elif 1 <= qtd <= 5:
                                pts = 1.0
                            elif 6 <= qtd <= 10:
                                pts = 3.0
                            else:  # >= 11
                                pts = 5.0

                            state_1451["pts"] = pts
                            lbl_qtd_1451.set_text(f"Itens selecionados: {qtd} de {len(lista_opcoes_1451)}")
                            lbl_pts_1451.set_text(f"📊 Pontuação Calculada: {pts:.1f} pontos")

                        # Grid de Checkboxes individuais
                        with ui.grid(columns=1).classes("w-full mb-4 md:grid-cols-2 gap-2"):
                            for opcao in lista_opcoes_1451:
                                is_checked = opcao in marcados_salvos
                                cb = ui.checkbox(text=opcao, value=is_checked)
                                cb.on("change", calcular_1451)
                                checkboxes_dict[opcao] = cb

                        calcular_1451()

                        # Campo de Link / Evidência
                        input_link_1451 = ui.textarea(
                            label="Link de Evidência / Documento do Plano Operativo:",
                            value=str(q1451_data.get("link", "") or ""),
                            placeholder="Insira o link comprovando o escopo do Plano Operativo..."
                        ).classes("w-full mb-4").props("outlined rows=3")

                        # Ação de Salvamento
                        def salvar_1451():
                            marcados = [op for op, cb in checkboxes_dict.items() if cb.value]
                            
                            res_data["14.5.1"] = {
                                "valor": marcados,
                                "pontos": state_1451["pts"],
                                "link": input_link_1451.value,
                                "comentarios": q1451_data.get("comentarios", []),
                                "status": q1451_data.get("status", "Pendente")
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="14.5.1",
                                valor=marcados,
                                pontos=state_1451["pts"],
                                link=input_link_1451.value,
                                comentarios=q1451_data.get("comentarios", []),
                                status=q1451_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 14.5.1 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_1451).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("14.5.1", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO 15.0 (Criação da Ouvidoria Pública - Radio)
                    # ==========================================
                    opcoes_150 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.0",
                        titulo="Criação da Ouvidoria Pública Municipal",
                        pergunta="Houve a criação da ouvidoria pública no âmbito do Poder Executivo Municipal?",
                        tipo_input="radio",
                        opcoes=opcoes_150,
                        placeholder_link="Insira o link da norma de criação da Ouvidoria...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.1 (Instrumento Normativo da Ouvidoria - Radio/Text)
                    # ==========================================
                    opcoes_151 = {
                        "Selecione...": 0.0,
                        "Instrumento informado/anexado – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.1",
                        titulo="Instrumento Normativo de Criação da Ouvidoria",
                        pergunta="Informe o instrumento normativo de criação da ouvidoria pública, número e data da publicação:",
                        tipo_input="radio",
                        opcoes=opcoes_151,
                        placeholder_link="Informe número, data da publicação e insira o link da norma...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.2 (Divulgação da Norma da Ouvidoria - Radio/Text)
                    # ==========================================
                    opcoes_152 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.2",
                        titulo="Divulgação do Instrumento Normativo da Ouvidoria",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do instrumento normativo de criação da Ouvidoria Pública (Se não estiver disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_152,
                        placeholder_link="Cole o link de divulgação ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.3 (Características da Ouvidoria - Checkbox)
                    # ==========================================
                    # Regra de cálculo: Perde -0.5 pt para cada item principal NÃO assinalado (pontuação varia de 0 a -2,5 pts).
                    opcoes_153 = {
                        "Independência": 0.0,
                        "Isenção": 0.0,
                        "Acessibilidade": 0.0,
                        "Transparência": 0.0,
                        "Confidencialidade": 0.0,
                        "Outros": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.3",
                        titulo="Características de Execução da Ouvidoria",
                        pergunta="Assinale as características que a ouvidoria dispõe para a execução de suas atribuições (Cada item principal não marcado perde -0,5 pt):",
                        tipo_input="checkbox",
                        opcoes=opcoes_153,
                        placeholder_link="Insira o link demonstrando o regimento/funcionamento da Ouvidoria...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.4 (Relatório de Gestão da Ouvidoria 2025 - Radio)
                    # ==========================================
                    opcoes_154 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.4",
                        titulo="Relatório de Gestão da Ouvidoria (Exercício 2025)",
                        pergunta="A ouvidoria elaborou Relatório de Gestão do exercício de 2025 contendo a consolidação das manifestações encaminhadas pelos usuários de serviços públicos, e com base nelas, apontou falhas e sugeriu melhorias em sua prestação?",
                        tipo_input="radio",
                        opcoes=opcoes_154,
                        placeholder_link="Insira o link do Relatório de Gestão da Ouvidoria 2025...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.4.1 (Conteúdo dos Relatórios da Ouvidoria - Checkbox)
                    # ==========================================
                    # Regra de cálculo: Perde -2,5 pts para cada item não assinalado (pontuação de 0 a -10 pts).
                    opcoes_1541 = {
                        "Número de manifestações recebidas no exercício anterior": 0.0,
                        "Motivos das Manifestações": 0.0,
                        "Análise dos Pontos recorrentes": 0.0,
                        "Providências adotadas pela administração pública nas soluções apresentadas": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.4.1",
                        titulo="Informações Constantes nos Relatórios Gerenciais da Ouvidoria",
                        pergunta="Assinale as informações constantes nos relatórios gerenciais elaborados pela ouvidoria (Cada item não marcado perde -2,5 pts):",
                        tipo_input="checkbox",
                        opcoes=opcoes_1541,
                        placeholder_link="Insira o link demonstrando o conteúdo dos relatórios...",
                        on_save_callback=render_conteudo.refresh,
                    )
                   
                    # ==========================================
                    # QUESITO 15.4.2 (Divulgação do Relatório de Gestão 2025 - Radio/Text)
                    # ==========================================
                    # Regra de cálculo: Se XYZ perde -10 pts; Se <> XYZ ganha 0 pts.
                    opcoes_1542 = {
                        "Link/Página eletrônica disponível (<> XYZ) – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – -10 pts": -10.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.4.2",
                        titulo="Divulgação do Relatório de Gestão de 2025 na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do Relatório de Gestão do exercício de 2025 (Se não estiver disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_1542,
                        placeholder_link="Cole o link do relatório ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 15.5 (Iniciativas de Divulgação da Ouvidoria - Checkbox)
                    # ==========================================
                    # Regra de cálculo: Perde -0.5 pt apenas para os 2 itens digitais/página se NÃO assinalados.
                    import json
                    import ast

                    raw_155 = res_data.get("15.5") or res_data.get(15.5) or {}
                    q155_data = raw_155 if isinstance(raw_155, dict) else {}

                    # Recupera itens salvos do banco
                    raw_val_155 = q155_data.get("valor", [])
                    
                    if isinstance(raw_val_155, str) and raw_val_155.strip():
                        try:
                            marcados_salvos = json.loads(raw_val_155)
                        except Exception:
                            try:
                                marcados_salvos = ast.literal_eval(raw_val_155)
                            except Exception:
                                marcados_salvos = []
                    elif isinstance(raw_val_155, list):
                        marcados_salvos = raw_val_155
                    else:
                        marcados_salvos = []

                    # Apenas estes 2 itens geram penalidade (-0.5 cada) se NÃO estiverem marcados
                    itens_com_penalidade_155 = [
                        "Link da página eletrônica da ouvidoria no sítio da Prefeitura Municipal",
                        "Utilização de outras plataformas digitais para a divulgação da missão, do modo de trabalho das ouvidorias e incentivando a participação popular. Ex.: instagram, facebook, twitter etc."
                    ]

                    # Lista completa das opções
                    todos_itens_155 = [
                        "Link da página eletrônica da ouvidoria no sítio da Prefeitura Municipal",
                        "Utilização de outras plataformas digitais para a divulgação da missão, do modo de trabalho das ouvidorias e incentivando a participação popular. Ex.: instagram, facebook, twitter etc.",
                        "Realização de palestras para grupos e instituições. Ex.: escolas, igrejas, associações civis, outros grupos organizados etc.",
                        "Realização de eventos que estimulem a participação e coleta das demandas sociais. Ex.: realização de audiências públicas para divulgação dos trabalhos desempenhados pela ouvidoria e ouvir as demandas da população.",
                        "Outras"
                    ]

                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("15.5 • Iniciativas de Divulgação e Mobilização Social das Ouvidorias").classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label("Assinale as iniciativas de divulgação e mobilização social das ouvidorias efetuadas:").classes("text-base font-bold text-black mb-2")
                        
                        # Bloco Informativo de Regras
                        with ui.expansion("ℹ️ Regras de Pontuação / Penalidade", icon="info").classes("w-full mb-4 bg-gray-50 border border-gray-200 rounded"):
                            ui.markdown("""
                            * **Itens de Página/Plataformas Digitais assinalados:** **0,0 ponto** (Sem penalidade)
                            * **Para cada um dos 2 itens digitais NÃO assinalado:** Penalidade de **-0,5 ponto**
                            """).classes("text-sm text-gray-700 p-2")

                        checkboxes_dict_155 = {}
                        lbl_penalidade_155 = ui.label().classes("text-sm font-bold text-gray-800 mt-2")
                        lbl_pts_155 = ui.label().classes("text-sm font-bold text-green-600 mt-1")
                        state_155 = {"pts": float(q155_data.get("pontos", 0.0))}

                        def calcular_155(_=None):
                            marcados = [op for op, cb in checkboxes_dict_155.items() if cb.value]
                            
                            # Avalia a ausência APENAS nos 2 primeiros itens digitais
                            nao_marcados_alvo = sum(1 for item in itens_com_penalidade_155 if item not in marcados)
                            
                            # Penalidade: -0.5 por item ausente
                            pts = nao_marcados_alvo * -0.5

                            state_155["pts"] = pts
                            lbl_penalidade_155.set_text(f"Itens digitais obrigatórios não assinalados: {nao_marcados_alvo} de {len(itens_com_penalidade_155)}")
                            lbl_pts_155.set_text(f"📊 Pontuação / Penalidade Calculada: {pts:.1f} pontos")

                        # Renderiza todos os checkboxes na tela
                        with ui.grid(columns=1).classes("w-full mb-4 gap-2"):
                            for opcao in todos_itens_155:
                                is_checked = opcao in marcados_salvos
                                cb = ui.checkbox(text=opcao, value=is_checked)
                                cb.on("change", calcular_155)
                                checkboxes_dict_155[opcao] = cb

                        calcular_155()

                        # Campo de Link / Evidência
                        input_link_155 = ui.textarea(
                            label="Link de Evidência / Comprovação das Ações:",
                            value=str(q155_data.get("link", "") or ""),
                            placeholder="Insira o link com comprovação das ações de divulgação..."
                        ).classes("w-full mb-4").props("outlined rows=3")

                        # Ação de Salvamento
                        def salvar_155():
                            marcados = [op for op, cb in checkboxes_dict_155.items() if cb.value]
                            
                            res_data["15.5"] = {
                                "valor": marcados,
                                "pontos": state_155["pts"],
                                "link": input_link_155.value,
                                "comentarios": q155_data.get("comentarios", []),
                                "status": q155_data.get("status", "Pendente")
                            }

                            save_resposta(
                                ano=ano_sel,
                                qid="15.5",
                                valor=marcados,
                                pontos=state_155["pts"],
                                link=input_link_155.value,
                                comentarios=q155_data.get("comentarios", []),
                                status=q155_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito 15.5 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_155).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("15.5", res_data, render_conteudo.refresh)
                    # ==========================================
                    # QUESITO 16.0 (Elaboração da Carta de Serviços ao Usuário - Radio)
                    # ==========================================
                    opcoes_160 = {
                        "Selecione...": 0.0,
                        "Sim – 04 pts": 4.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.0",
                        titulo="Carta de Serviços ao Usuário (Lei 13.460/2017)",
                        pergunta="A prefeitura elaborou a 'Carta de Serviço ao Usuário', que trata dos serviços prestados pelos seus órgãos e entidades, as formas de acesso a esses serviços e seus compromissos e padrões de qualidade de atendimento ao público?",
                        tipo_input="radio",
                        opcoes=opcoes_160,
                        placeholder_link="Insira o link do ato de instituição da Carta de Serviços...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.1 (Divulgação da Carta de Serviços - Radio/Text)
                    # ==========================================
                    # Regra de cálculo: Se XYZ ganha 0 pts; Se <> XYZ ganha 2 pts.
                    opcoes_161 = {
                        "Link/Página eletrônica disponível (<> XYZ) – 02 pts": 2.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.1",
                        titulo="Divulgação da Carta de Serviços ao Usuário na Internet",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação da 'Carta de Serviço ao Usuário' (Se não estiver disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_161,
                        placeholder_link="Cole o link da Carta de Serviços ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

    # ==========================================
                    # QUESITO 16.2 (Atualização da Carta de Serviços - Radio)
                    # ==========================================
                    opcoes_162 = {
                        "Selecione...": 0.0,
                        "Sim – 02 pts": 2.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.2",
                        titulo="Atualização da Carta de Serviços ao Usuário",
                        pergunta="A 'Carta de Serviço ao Usuário' está atualizada?",
                        tipo_input="radio",
                        opcoes=opcoes_162,
                        placeholder_link="Insira o link demonstrando a atualização...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.3 (Regulamentação da Carta de Serviços - Radio)
                    # ==========================================
                    opcoes_163 = {
                        "Selecione...": 0.0,
                        "Sim – 04 pts": 4.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.3",
                        titulo="Regulamentação da Carta de Serviços ao Usuário",
                        pergunta="A prefeitura regulamentou a operacionalização da Carta de Serviços ao Usuário, conforme o artigo 7°, § 5°, da Lei Federal n° 13.460/2017?",
                        tipo_input="radio",
                        opcoes=opcoes_163,
                        placeholder_link="Insira o link do decreto ou norma regulamentadora...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.3.1 (Instrumento Normativo da Carta de Serviços - Radio/Text)
                    # ==========================================
                    opcoes_1631 = {
                        "Selecione...": 0.0,
                        "Instrumento informado/anexado – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.3.1",
                        titulo="Instrumento Normativo da Carta de Serviços",
                        pergunta="Informe o instrumento normativo que regulamentou a 'Carta de Serviço ao Usuário', Número e Data da publicação:",
                        tipo_input="radio",
                        opcoes=opcoes_1631,
                        placeholder_link="Informe o número, data da publicação e insira o link da norma...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 16.3.2 (Divulgação da Regulamentação da Carta - Radio/Text)
                    # ==========================================
                    opcoes_1632 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="16.3.2",
                        titulo="Divulgação da Regulamentação da Carta de Serviços",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação do instrumento normativo que regulamentou a 'Carta de Serviço ao Usuário' (Se não disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_1632,
                        placeholder_link="Cole o link de divulgação ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.0 (Conselho de Usuários - Radio)
                    # ==========================================
                    opcoes_170 = {
                        "Selecione...": 0.0,
                        "Sim – 04 pts": 4.0,
                        "Não – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.0",
                        titulo="Instituição do Conselho de Usuários",
                        pergunta="A prefeitura regulamentou e instituiu o Conselho de Usuários, nos termos definidos nos artigos 18 a 21 da Lei Federal nº 13.460/2017?",
                        tipo_input="radio",
                        opcoes=opcoes_170,
                        placeholder_link="Insira o link do ato de criação do Conselho de Usuários...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.1 (Instrumento Normativo do Conselho - Radio/Text)
                    # ==========================================
                    opcoes_171 = {
                        "Selecione...": 0.0,
                        "Instrumento informado/anexado – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.1",
                        titulo="Instrumento Normativo do Conselho de Usuários",
                        pergunta="Informe o instrumento normativo que regulamentou os Conselhos de Usuários, Número e Data da publicação:",
                        tipo_input="radio",
                        opcoes=opcoes_171,
                        placeholder_link="Informe o número, data da publicação e insira o link da norma...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 17.2 (Divulgação da Norma do Conselho - Radio/Text)
                    # ==========================================
                    opcoes_172 = {
                        "Link/Página eletrônica disponível – 00 pts": 0.0,
                        "Não disponível (Texto XYZ) – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="17.2",
                        titulo="Divulgação da Regulamentação do Conselho de Usuários",
                        pergunta="Informe a página eletrônica (link na internet) de divulgação da regulamentação do Conselho de Usuários (Se não disponível, inserir XYZ):",
                        tipo_input="radio",
                        opcoes=opcoes_172,
                        placeholder_link="Cole o link de divulgação ou digite XYZ...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 18.0 (Plano Diretor - Radio)
                    # ==========================================
                    opcoes_180 = {
                        "Selecione...": 0.0,
                        "Sim – 00 pts": 0.0,
                        "Não – 00 pts": 0.0,
                        "Não se aplica – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="18.0",
                        titulo="Elaboração do Plano Diretor",
                        pergunta="O município elaborou Plano Diretor conforme Lei nº 10.257/01?",
                        tipo_input="radio",
                        opcoes=opcoes_180,
                        placeholder_link="Insira o link da Lei do Plano Diretor...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 18.1 (Atualização do Plano Diretor - Radio)
                    # ==========================================
                    # Regra de cálculo: Se a data de atualização <= 31/12/2015 perde -10 pts. Se > 31/12/2015 ganha 0 pts.
                    opcoes_181 = {
                        "Selecione...": 0.0,
                        "Data <= 31/12/2015 – -10 pts": -10.0,
                        "Data > 31/12/2015 – 00 pts": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="18.1",
                        titulo="Data da Última Atualização do Plano Diretor",
                        pergunta="Informe a data da última atualização do Plano Diretor (Se <= 31/12/2015 perde -10 pts | Se > 31/12/2015 ganha 0 pts):",
                        tipo_input="radio",
                        opcoes=opcoes_181,
                        placeholder_link="Informe a data de atualização e insira o link da lei alteradora...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO 19.0 (Impressões e Comentários - Texto)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="19.0",
                        titulo="Impressões, Comentários e Sugestões",
                        pergunta="Gostaria de registrar suas impressões, comentários e sugestões a respeito do presente questionário?",
                        tipo_input="text",
                        opcoes={},  # Dicionário vazio para satisfazer o parâmetro obrigatório
                        placeholder_link="Escreva aqui suas impressões, comentários e sugestões...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO P1 (Coerência Indicadores x Metas - Entrada Manual)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="P1",
                        titulo="Coerência entre os Resultados dos Indicadores dos Programas e das Metas das Ações",
                        pergunta="Informe a pontuação calculada com base na média dos resultados dos indicadores comparada à média das ações do programa, conforme o Relatório de Atividades:",
                        tipo_input="number",
                        placeholder_link="Insira o link ou anexo da memória de cálculo e do Relatório de Atividades...",
                        on_save_callback=render_conteudo.refresh,
                    )

                    # ==========================================
                    # QUESITO P2 (Resultado Físico x Recursos Financeiros - Entrada Manual)
                    # ==========================================
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="P2",
                        titulo="Confronto entre o Resultado Físico Alcançado pelas Metas das Ações e os Recursos Financeiros Utilizados",
                        pergunta="Apresenta o valor alcançado de cada uma das ações, dividindo-se o valor da meta física realizada pelo valor estipulado inicialmente no planejamento; e o quanto dos recursos disponibilizados foram utilizados, dividindo-se o valor liquidado pelo valor fixado atualizado, a partir dos dados constantes da Lei Orçamentária Anual, por meio do seguinte cálculo:",
                        tipo_input="number",
                        placeholder_link="Insira o link ou anexo da memória de cálculo e do demonstrativo financeiro...",
                        on_save_callback=render_conteudo.refresh,
                    )
 
                    # ==========================================
                    # QUESITO P3 (Percentual de Alteração do Planejamento Inicial - Calculadora Monetária)
                    # ==========================================
                    import json
                    import ast

                    # Estado local para persistência e cálculo dos valores monetários
                    raw_p3 = res_data.get("P3") or res_data.get("p3") or {}
                    p3_data = raw_p3 if isinstance(raw_p3, dict) else {}

                    # Tratamento do campo 'valor' para restaurar I e J salvos
                    raw_val_p3 = p3_data.get("valor", {})
                    val_dict_p3 = {}

                    if isinstance(raw_val_p3, str) and raw_val_p3.strip():
                        try:
                            val_dict_p3 = json.loads(raw_val_p3)
                        except Exception:
                            try:
                                val_dict_p3 = ast.literal_eval(raw_val_p3)
                            except Exception:
                                val_dict_p3 = {}
                    elif isinstance(raw_val_p3, dict):
                        val_dict_p3 = raw_val_p3

                    state_p3 = {
                        "val_i": float(val_dict_p3.get("I", 0.0)),
                        "val_j": float(val_dict_p3.get("J", 0.0)),
                        "pts": float(p3_data.get("pontos", 0.0))
                    }

                    with ui.card().classes("w-full p-6 mb-6 border border-gray-300 rounded-lg shadow-sm bg-white"):
                        ui.label("P3 • Percentual de Alteração do Planejamento Inicial").classes("text-xl font-semibold text-blue-500 mb-3")
                        ui.label("Total dos valores dos programas estabelecidos inicialmente na LOA comparado com os valores finais apurados para os mesmos programas (K = J / I):").classes("text-base font-bold text-black mb-2")
                        
                        # Bloco Informativo de Regras de Pontuação
                        with ui.expansion("ℹ️ Tabela de Regras do Indicador K", icon="info").classes("w-full mb-4 bg-gray-50 border border-gray-200 rounded"):
                            ui.markdown("""
                            * **K >= 1,3:** Penalidade máxima (**-30,0 pontos**)
                            * **0,9 < K < 1,3:** Dentro do limite aceitável (**0,0 ponto**)
                            * **0,5 < K <= 0,9:** Graduação proporcional **`((0,9 - K) / 0,4) * -30`**
                            * **K <= 0,5:** Penalidade máxima (**-30,0 pontos**)
                            """).classes("text-sm text-gray-700 p-2")

                        # Seção da Calculadora Monetária
                        with ui.card().classes("w-full p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg"):
                            ui.label("🧮 Calculadora Automática do Indicador K").classes("font-bold text-blue-700 mb-2")

                            with ui.grid(columns=2).classes("w-full gap-4"):
                                input_i = (
                                    ui.number(label="Valor Total Inicial LOA (I)", value=state_p3["val_i"], format="%.2f")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )
                                input_j = (
                                    ui.number(label="Valor Total Final Apurado (J)", value=state_p3["val_j"], format="%.2f")
                                    .classes("w-full")
                                    .props("outlined bg-white prefix='R$'")
                                )

                            lbl_k = ui.label().classes("text-sm font-bold text-gray-800 mt-2")
                            lbl_pts = ui.label().classes("text-sm font-bold text-green-600 mt-1")

                            def calcular_k(_=None):
                                i = input_i.value or 0.0
                                j = input_j.value or 0.0
                                state_p3["val_i"] = i
                                state_p3["val_j"] = j

                                if i > 0:
                                    k = j / i
                                    if k >= 1.3:
                                        pts = -30.0
                                    elif 0.9 < k < 1.3:
                                        pts = 0.0
                                    elif 0.5 < k <= 0.9:
                                        pts = ((0.9 - k) / 0.4) * -30.0
                                    else:
                                        pts = -30.0
                                    
                                    state_p3["pts"] = pts
                                    lbl_k.set_text(f"Resultado K (J / I): {k:.4f}")
                                    lbl_pts.set_text(f"📊 Impacto de Pontuação Calculado: {pts:.2f} pontos")
                                else:
                                    lbl_k.set_text("Resultado K: Indefinido (O Valor Inicial 'I' deve ser maior que R$ 0,00)")
                                    lbl_pts.set_text("📊 Impacto de Pontuação Calculado: 0.00 pontos")
                                    state_p3["pts"] = 0.0

                            input_i.on("update:model-value", calcular_k)
                            input_j.on("update:model-value", calcular_k)
                            calcular_k()

                        # Campo de Evidência / Link
                        input_link_p3 = ui.textarea(
                            label="Link de Evidência / Documento:",
                            value=str(p3_data.get("link", "") or ""),
                            placeholder="Insira o link ou anexo com a memória de cálculo dos valores I e J..."
                        ).classes("w-full mb-4").props("outlined rows=3")

                        # Ação de Salvamento
                        def salvar_p3():
                            # Monta o dicionário com os valores calculados
                            valores_dict = {"I": state_p3["val_i"], "J": state_p3["val_j"]}
                            
                            # Atualiza a memória local da aplicação
                            res_data["P3"] = {
                                "valor": valores_dict,
                                "pontos": state_p3["pts"],
                                "link": input_link_p3.value,
                                "comentarios": p3_data.get("comentarios", []),
                                "status": p3_data.get("status", "Pendente")
                            }

                            # Salva no Banco de Dados (Convertendo 'valor' para string JSON se necessário)
                            save_resposta(
                                ano=ano_sel,
                                qid="P3",
                                valor=json.dumps(valores_dict),
                                pontos=state_p3["pts"],
                                link=input_link_p3.value,
                                comentarios=p3_data.get("comentarios", []),
                                status=p3_data.get("status", "Pendente"),
                            )
                            ui.notify("Quesito P3 salvo com sucesso!", type="positive")
                            if render_conteudo.refresh:
                                render_conteudo.refresh()

                        ui.button("SALVAR RESPOSTA", on_click=salvar_p3).classes("bg-blue-500 text-white font-bold px-5 py-2 rounded-md shadow my-2")
                        ui.separator().classes("my-2")
                        bloco_comentarios("P3", res_data, render_conteudo.refresh)

                    # ==========================================
                    # QUESITO P4 (Pontualidade na Entrega de Documentos AUDESP)
                    # ==========================================
                    opcoes_p4 = {
                        "Documentos relativos às Peças de Planejamento entregues no prazo – 150 pts": 150.0,
                        "Documentos relativos às Peças de Planejamento entregues fora do prazo ou não entregue – 00 pts": 0.0,
                    }

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="P4",
                        titulo="Pontualidade na Entrega de Documentos relativos às Peças de Planejamento",
                        pergunta="A resposta à seguinte questão será extraída do sistema AUDESP: 'Os documentos relativos às peças de planejamento (Atas de audiência de avaliação do cumprimento de metas, Relatório de Atividades, PPA, LDO e LOA) são entregues no prazo ao Tribunal de Contas do Estado de São Paulo?'",
                        tipo_input="radio",
                        opcoes=opcoes_p4,
                        placeholder_link="Insira o link ou comprovante do protocolo de envio no AUDESP...",
                        on_save_callback=render_conteudo.refresh,
                    )

import io
import os
import logging
from datetime import datetime
from io import BytesIO

import psycopg2
import psycopg2.extras

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# -----------------------------------------------------------------------------
# 1. MAPEAMENTOS E TETOS DE PONTUAÇÃO DO I-PLAN
# -----------------------------------------------------------------------------
PONTUACOES_MAX_IAMB = {
    "1.1.2": 20.0, "1.1.3": 5.0, "1.2": 20.0, "2.0": 10.0, "2.1": 50.0,
    "3.0": 10.0, "3.1": 20.0, "4.0": 20.0, "5.2.1": 20.0, "6.0": 20.0,
    "6.1": 50.0, "6.2": 25.0, "7.2": 2.0, "7.3": 10.0, "7.3.1": 20.0,
    "7.4": 10.0, "7.4.1": 20.0, "7.5": 30.0, "7.7": 30.0, "7.8": 20.0,
    "7.8.1": 50.0, "7.9": 3.0, "8.2": 2.0, "8.3": 10.0, "8.4": 20.0,
    "8.4.1": 10.0, "8.4.2": 30.0, "8.4.3": 50.0, "9.2": 100.0, "9.3": 5.0,
    "9.3.1": 5.0, "11.2": 2.0, "11.3": 30.0, "11.3.2": 20.0, "11.3.3": 40.0,
    "11.5": 10.0, "12.1": 54.0, "14.3": 30.0, "15": 2.0, "15.1": 3.0,
    "A4.1.1": 90.0, "A4.1.2": 20.0, "A4.1.3": 22.0, "A6": 5.0
}

# Alias para evitar erros de referência no ReportLab
PONTUACOES_MAX = PONTUACOES_MAX_IAMB

PENALIDADES_MAX = {
    "5.2": -15.0, "5.3": -10.0, "7.3.2": -5.0, "7.4.2": -5.0, "7.5.1": -5.0, 
    "8.4.4": -30.0, "9.1": -30.0, "10.0": -100.0, "10.1": -30.0, "14.0": -30.0, "A1": -200.0
}

# String de conexão com o PostgreSQL
DATABASE_URL = "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


# -----------------------------------------------------------------------------
# 2. FUNÇÕES AUXILIARES E REGRAS DE NEGÓCIO I-AMB
# -----------------------------------------------------------------------------
def get_db_connection():
    """Cria conexão segura com o Neon PostgreSQL usando linhas por nome de coluna."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def calcular_percentual_checklist(resp, total_itens):
    """Calcula a porcentagem de itens atendidos em um checklist."""
    if not resp:
        return 0.0
    if isinstance(resp, list):
        qtd = len(resp)
    elif isinstance(resp, str):
        qtd = len([item for item in resp.split(',') if item.strip()])
    else:
        qtd = 0
    return min((qtd / total_itens) * 100.0, 100.0)


def obter_regra_ods_iamb(qid, resp):
    """Mapeia os quesitos do I-AMB para as metas da Agenda 2030 (ODS) e seu status."""
    resp_l = str(resp).strip().lower()
    metas = "-"
    status = "Não Atendido"

    if qid in ["1.0", "1.1"]:
        metas = "12.2, 15.2, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "1.1.2":
        metas = "12.8"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "2.0":
        metas = "4.7, 12.8, 15.1"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "3.0":
        metas = "12.2, 16.6, 17.14"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "4.0":
        metas = "12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "5.0":
        metas = "5.0"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "6.0":
        metas = "6.4, 6.b, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "6.2":
        metas = "6.4, 6.5, 6.b, 16.6"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid in ["7.0", "7.3"]:
        metas = "6.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid in ["7.4", "7.5"]:
        metas = "6.2, 6.3"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "7.7.1":
        metas = "6.0, 16.6"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid == "7.8":
        metas = "6.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "7.8.1":
        metas = "6.2, 6.3"
        status = "Atendido" if "todas as metas foram cumpridas dentro do prazo" in resp_l else "Não Atendido"
    elif qid == "7.9":
        metas = "6.2, 6.3"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid in ["8.0", "8.3", "8.4", "9.0"]:
        metas = "11.6, 12.5"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "8.3.1":
        metas = "11.6, 12.5, 12.4"
        pct = calcular_percentual_checklist(resp, 3)
        status = f"{pct:.1f}% Atendido"
    elif qid == "8.4.1":
        metas = "11.6, 12.5, 12.4"
        pct = calcular_percentual_checklist(resp, 4)
        status = f"{pct:.1f}% Atendido"
    elif qid in ["10.0", "10.1"]:
        metas = "11.6, 12.5, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "10.2":
        metas = "11.6, 12.5, 16.6"
        status = "Atendido" if "todos os bairros do município são atendidos" in resp_l else "Não Atendido"
    elif qid == "10.3":
        metas = "11.6, 12.5, 12.4, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "11.0":
        metas = "11.6, 12.4, 12.5, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "12.0":
        metas = "11.6, 12.5, 12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "13.0":
        metas = "11.6, 12.4"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"
    elif qid == "14.0":
        metas = "11.6, 12.4"
        status = "Atendido" if "não" in resp_l else "Não Atendido"
    elif qid == "15.0":
        metas = "12.0, 16.6"
        status = "Atendido" if "sim" in resp_l else "Não Atendido"

    return metas, status


def get_all_years_data():
    """Busca a série histórica do I-AMB no PostgreSQL Neon DB."""
    all_data = {}
    query = """
        SELECT qid, ano, valor, pontos, link, comentarios
        FROM respostas_iplan
        ORDER BY ano ASC;
    """
    try:
        with get_db_connection() as conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            except Exception:
                cur = conn.cursor()

            cur.execute(query)
            rows = cur.fetchall()

            for row in rows:
                if isinstance(row, dict):
                    qid = str(row["qid"]).strip()
                    ano = int(row["ano"])
                    valor = row["valor"] or ""
                    pontos = float(row["pontos"]) if row["pontos"] is not None else 0.0
                    link = row["link"] if row["link"] != "EMPTY_STRING" else ""
                    comentarios = row["comentarios"] if isinstance(row["comentarios"], list) else []
                else:
                    qid = str(row[0]).strip()
                    ano = int(row[1])
                    valor = row[2] or ""
                    pontos = float(row[3]) if row[3] is not None else 0.0
                    link = row[4] if row[4] != "EMPTY_STRING" else ""
                    comentarios = row[5] if isinstance(row[5], list) else []

                if ano not in all_data:
                    all_data[ano] = {}

                all_data[ano][qid] = {
                    "valor": valor,
                    "pontos": pontos,
                    "link": link,
                    "comentarios": comentarios
                }
    except Exception as e:
        print(f"❌ Erro ao buscar série histórica I-AMB no Neon DB: {e}")

    return all_data


def converter_para_float(val):
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(',', '.').strip())
    except (ValueError, TypeError):
        return 0.0


def converter_pontos_em_faixa_iegm(pontos):
    pts = float(pontos)
    if pts < 500.0:
        return "C"
    elif 500.0 <= pts <= 599.9:
        return "C+"
    elif 600.0 <= pts <= 749.9:
        return "B"
    elif 750.0 <= pts <= 899.9:
        return "B+"
    else:
        return "A"


# -----------------------------------------------------------------------------
# 3. GERADOR DE RELATÓRIO PDF COMPLETO (REPORTLAB)
# -----------------------------------------------------------------------------
def gerar_relatorio_pdf(dados, ano, total, faixa, todos_dados=None):
    """Gere o relatório completo e não-resumido do I-PLAN em PDF."""
    
    lista_alvo_iamb = [
        "1.0", "1.1", "5.0", "7.0", "7.6", "7.6.1", 
        "8.0", "9.0", "10.3", "11.0", "11.1", "11.6", "11.6.1", "12.0"
    ]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )
    elements = []
    styles = getSampleStyleSheet()

    # Definindo e registrando estilos estilizados do relatório
    styles.add(ParagraphStyle('TitleCapa', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=24, textColor=colors.HexColor("#1b4f72"), alignment=TA_CENTER))
    styles.add(ParagraphStyle('SubTitleCapa', parent=styles['Normal'], fontName='Helvetica', fontSize=14, textColor=colors.HexColor("#5D6D7E"), alignment=TA_CENTER))
    styles.add(ParagraphStyle('ItemEsq', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#2C3E50")))
    styles.add(ParagraphStyle('PagDir', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#1B4F72"), alignment=TA_RIGHT))
    styles.add(ParagraphStyle('ThStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle('TdStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=8, alignment=TA_LEFT))
    styles.add(ParagraphStyle('TdCenter', parent=styles['Normal'], fontName='Helvetica', fontSize=8, alignment=TA_CENTER))
    styles.add(ParagraphStyle('CellLink', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor("#1A5276")))

    ano_normalizado = int(str(ano).strip()[:4])
    ano_ant = ano_normalizado - 1
    todos_dados = todos_dados or {}

    # -------------------------------------------------------------------------
    # CAPA
    # -------------------------------------------------------------------------
    elements.append(Spacer(1, 40))
    logo_path = "iegm.png"
    if os.path.exists(logo_path):
        try:
            logo = Image(logo_path, width=350, height=160)
            logo.hAlign = 'CENTER'
            elements.append(logo)
        except Exception:
            elements.append(Paragraph("<b>[IEGM - Planejamento]</b>", styles["TitleCapa"]))
    else:
        elements.append(Paragraph("<b>[IEGM - Planejamento]</b>", styles["TitleCapa"]))

    elements.append(Spacer(1, 40))
    
    # Título limpo em linha única
    elements.append(Paragraph("<b>Relatório I-PLAN</b>", styles['TitleCapa']))
    elements.append(Spacer(1, 15))
    
    # Subtítulo com Ano de Referência
    elements.append(Paragraph(f"Exercício de Referência: <b>{ano_normalizado}</b>", styles['SubTitleCapa']))
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # SUMÁRIO
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>SUMÁRIO DE CONTEÚDO</b>", styles["Heading1"]))
    elements.append(Spacer(1, 20))

    itens_sumario = [
        [Paragraph("1. Resumo Executivo e Evolução Comparativa", styles['ItemEsq']), Paragraph("Pág. 3", styles['PagDir'])],
        [Paragraph("2. Análise Detalhada de Desempenho por Quesito", styles['ItemEsq']), Paragraph("Pág. 3", styles['PagDir'])],
        [Paragraph("3. Quadro de Penalidades e Impactos Negativos", styles['ItemEsq']), Paragraph("Pág. 4", styles['PagDir'])],
        [Paragraph("4. Diagnóstico de Reincidências de Fracasso", styles['ItemEsq']), Paragraph("Pág. 4", styles['PagDir'])],
        [Paragraph("5. Alinhamento com a Agenda 2030 (Metas ODS)", styles['ItemEsq']), Paragraph("Pág. 5", styles['PagDir'])],
        [Paragraph("6. Evolução Temporal da Série Histórica (I-PLAN)", styles['ItemEsq']), Paragraph("Pág. 5", styles['PagDir'])],
        [Paragraph("7. Quesitos de Conformidade Operacional (Sem Pontuação Direta)", styles['ItemEsq']), Paragraph("Pág. 6", styles['PagDir'])],
    ]

    tabela_sumario = Table(itens_sumario, colWidths=[380, 100])
    tabela_sumario.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
    ]))
    elements.append(tabela_sumario)
    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 1. RESUMO EXECUTIVO
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>1. RESUMO EXECUTIVO E EVOLUÇÃO COMPARATIVA</b>", styles["Heading2"]))
    elements.append(Spacer(1, 8))

    nota_atual = converter_para_float(total)
    dados_ano_anterior = todos_dados.get(ano_ant) or todos_dados.get(str(ano_ant)) or {}
    nota_anterior = 0.0

    if isinstance(dados_ano_anterior, dict):
        for qid_ant, info_ant in dados_ano_anterior.items():
            if str(qid_ant).startswith("COM_"):
                continue
            pts = info_ant.get("pontos", 0.0) if isinstance(info_ant, dict) else info_ant
            nota_anterior += converter_para_float(pts)

    faixa_anterior = converter_pontos_em_faixa_iegm(nota_anterior)
    faixa_atual = faixa if faixa else converter_pontos_em_faixa_iegm(nota_atual)
    variacao_nominal = nota_atual - nota_anterior

    if nota_anterior > 0:
        variacao_pct = (variacao_nominal / nota_anterior) * 100.0
        str_pct = f"{variacao_pct:+.2f}%"
    else:
        str_pct = f"{variacao_nominal:+.1f} pts"

    cor_var = colors.HexColor("#27AE60") if variacao_nominal >= 0 else colors.HexColor("#C0392B")
    simbolo = "▲" if variacao_nominal > 0 else ("▼" if variacao_nominal < 0 else "■")

    style_var_cell = ParagraphStyle('VarCell', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=cor_var, alignment=TA_CENTER)

    dados_exec = [
        [Paragraph("Exercício", styles['ThStyle']), Paragraph("Pontuação Obteve", styles['ThStyle']), Paragraph("Faixa IEGM", styles['ThStyle']), Paragraph("Variação Nominal", styles['ThStyle']), Paragraph("Variação %", styles['ThStyle'])],
        [Paragraph(str(ano_ant), styles['TdCenter']), Paragraph(f"{nota_anterior:.1f} pts", styles['TdCenter']), Paragraph(faixa_anterior, styles['TdCenter']), Paragraph("-", styles['TdCenter']), Paragraph("-", styles['TdCenter'])],
        [Paragraph(str(ano_normalizado), styles['TdCenter']), Paragraph(f"{nota_atual:.1f} pts", styles['TdCenter']), Paragraph(faixa_atual, styles['TdCenter']), Paragraph(f"{simbolo} {variacao_nominal:+.1f}", style_var_cell), Paragraph(f"{simbolo} {str_pct}", style_var_cell)]
    ]

    t_exec = Table(dados_exec, colWidths=[80, 100, 90, 105, 105])
    t_exec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4F72")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_exec)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 2. DESEMPENHO POR QUESITO (FORTES E FRACOS)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>2. ANÁLISE DETALHADA DE DESEMPENHO POR QUESITO</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    pontos_fortes, pontos_fracos, reincidencias = [], [], []

    for qid_raw, info in dados.items():
        if str(qid_raw).startswith("COM_"):
            continue

        qid = str(qid_raw).replace("Q_", "").strip()
        pts_max = PONTUACOES_MAX_IPLAN.get(qid, 0.0)

        if pts_max <= 0:
            continue

        if isinstance(info, dict):
            pts_obt = converter_para_float(info.get("pontos", 0.0))
            resp_val = str(info.get("valor", ""))
            link_ev = str(info.get("link", ""))
        else:
            pts_obt = converter_para_float(info)
            resp_val = str(info)
            link_ev = ""

        eficiencia = (pts_obt / pts_max) * 100.0
        item = {"qid": qid, "pts": pts_obt, "max": pts_max, "efic": eficiencia, "resp": resp_val, "link": link_ev}

        if eficiencia >= 70.0:
            pontos_fortes.append(item)
        else:
            pontos_fracos.append(item)

            # Teste de reincidência
            info_ant = dados_ano_anterior.get(qid_raw) or dados_ano_anterior.get(qid)
            if info_ant:
                pts_ant = converter_para_float(info_ant.get("pontos") if isinstance(info_ant, dict) else info_ant)
                if pts_ant < pts_max and pts_obt < pts_max:
                    reincidencias.append({
                        "qid": qid,
                        "max": pts_max,
                        "ant": pts_ant,
                        "atual": pts_obt
                    })

    # Tabela Pontos Fortes
    if pontos_fortes:
        elements.append(Paragraph("<b>✅ Pontos Fortes (Eficiência ≥ 70%)</b>", styles["Heading3"]))
        df_fortes = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for f in sorted(pontos_fortes, key=lambda x: x["efic"], reverse=True):
            lnk = f"<br/><a href='{f['link']}'>{f['link']}</a>" if f['link'] else ""
            evid = f"<b>{f['resp']}</b>{lnk}"
            df_fortes.append([f['qid'], f"{f['pts']:.1f} / {f['max']:.1f}", f"{f['efic']:.1f}%", Paragraph(evid, styles['CellLink'])])

        tf = Table(df_fortes, colWidths=[65, 75, 65, 275])
        tf.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27AE60")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(tf)
        elements.append(Spacer(1, 10))

    # Tabela Pontos Fracos
    if pontos_fracos:
        elements.append(Paragraph("<b>⚠️ Pontos Fracos (Eficiência < 70%)</b>", styles["Heading3"]))
        df_fracos = [["Quesito", "Nota / Teto", "Eficiência", "Resposta / Evidência"]]
        for fr in sorted(pontos_fracos, key=lambda x: x["efic"]):
            lnk = f"<br/><a href='{fr['link']}'>{fr['link']}</a>" if fr['link'] else ""
            evid = f"<b>{fr['resp']}</b>{lnk}"
            df_fracos.append([fr['qid'], f"{fr['pts']:.1f} / {fr['max']:.1f}", f"{fr['efic']:.1f}%", Paragraph(evid, styles['CellLink'])])

        tfr = Table(df_fracos, colWidths=[65, 75, 65, 275])
        tfr.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E67E22")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(tfr)

    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 3. PENALIDADES E IMPACTOS NEGATIVOS
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>3. ANÁLISE DE IMPACTO E PENALIDADES</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    data_penal = [["Quesito", "Penalidade Máxima", "Aplicada?", "Valor Aplicado"]]
    tem_penal = False

    for qid_p, p_max in PENALIDADES_MAX.items():
        info_p = dados.get(qid_p) or dados.get(f"Q_{qid_p}")
        val_p = 0.0
        aplicada = "Não"

        if info_p:
            pts_p = converter_para_float(info_p.get("pontos") if isinstance(info_p, dict) else info_p)
            if pts_p < 0:
                val_p = pts_p
                aplicada = "Sim"
                tem_penal = True

        data_penal.append([qid_p, f"{p_max:.1f} pts", aplicada, f"{val_p:.1f} pts"])

    t_penal = Table(data_penal, colWidths=[100, 120, 110, 150])
    t_penal.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#C0392B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(t_penal)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 4. DIAGNÓSTICO DE REINCIDÊNCIAS
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>4. DIAGNÓSTICO DE REINCIDÊNCIAS</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    if reincidencias:
        data_reinc = [["Quesito", "Teto Máximo", f"Pontos {ano_ant}", f"Pontos {ano_normalizado}", "Situação"]]
        for r in reincidencias:
            data_reinc.append([
                r["qid"], f"{r['max']:.1f}", f"{r['ant']:.1f}", f"{r['atual']:.1f}", "Reincidente"
            ])
        tr = Table(data_reinc, colWidths=[80, 80, 95, 95, 130])
        tr.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D35400")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]))
        elements.append(tr)
    else:
        elements.append(Paragraph("<i>Nenhuma reincidência de pontuação insatisfatória foi detectada entre os exercícios analisados.</i>", styles['TdStyle']))

    elements.append(PageBreak())

    # -------------------------------------------------------------------------
    # 5. ALINHAMENTO COM AGENDA 2030 (ODS)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>5. ALINHAMENTO COM A AGENDA 2030 (METAS ODS)</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    data_ods = [["Quesito", "Resposta Declarada", "Metas ODS Vinculadas", "Status do Atendimento"]]
    
    for qid_k, info_k in dados.items():
        if str(qid_k).startswith("COM_"):
            continue
        qid_c = str(qid_k).replace("Q_", "").strip()
        resp_c = info_k.get("valor", "") if isinstance(info_k, dict) else str(info_k)
        
        metas, status_ods = obter_regra_ods_iamb(qid_c, resp_c)
        if metas != "-":
            data_ods.append([qid_c, str(resp_c)[:35], metas, status_ods])

    t_ods = Table(data_ods, colWidths=[65, 160, 125, 130])
    t_ods.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16A085")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(t_ods)
    elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 6. SÉRIE HISTÓRICA DO I-PLAN
    # -------------------------------------------------------------------------
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart

    elements.append(Paragraph("<b>6. SÉRIE HISTÓRICA DO I-PLAN</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    data_hist = [["Exercício / Ano", "Pontuação Acumulada", "Faixa / Conceito"]]
    anos_ordenados = sorted(todos_dados.keys())

    anos_labels = []
    pontos_valores = []

    for a in anos_ordenados:
        sub_d = todos_dados[a]
        tot_a = 0.0
        if isinstance(sub_d, dict):
            for k_a, v_a in sub_d.items():
                if str(k_a).startswith("COM_"):
                    continue
                tot_a += converter_para_float(v_a.get("pontos") if isinstance(v_a, dict) else v_a)
        
        f_a = converter_pontos_em_faixa_iegm(tot_a)
        data_hist.append([str(a), f"{tot_a:.1f} pts", f_a])

        # Coleta dados para o gráfico
        anos_labels.append(str(a))
        pontos_valores.append(tot_a)

    # 1. Tabela
    t_hist = Table(data_hist, colWidths=[120, 180, 180])
    t_hist.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(t_hist)
    elements.append(Spacer(1, 15))

    # 2. Gráfico de Barras Vertical
    if pontos_valores:
        d = Drawing(480, 180)
        
        bc = VerticalBarChart()
        bc.x = 40
        bc.y = 25
        bc.height = 135
        bc.width = 410
        bc.data = [pontos_valores]
        
        # Eixo X (Anos)
        bc.categoryAxis.categoryNames = anos_labels
        bc.categoryAxis.labels.fontSize = 9
        bc.categoryAxis.labels.dy = -10
        
        # Eixo Y (Pontuação)
        max_v = max(pontos_valores) if pontos_valores else 100
        bc.valueAxis.valueMin = 0
        bc.valueAxis.valueMax = max(max_v * 1.15, 100)
        bc.valueAxis.valueStep = 20
        bc.valueAxis.labels.fontSize = 8
        
        # Estilo das Barras
        bc.bars[0].fillColor = colors.HexColor("#2980B9")
        bc.bars[0].strokeColor = colors.HexColor("#1B4F72")

        d.add(bc)
        elements.append(d)
        elements.append(Spacer(1, 15))

    # -------------------------------------------------------------------------
    # 7. QUESITOS SEM PONTUAÇÃO DIRETA (I-PLAN - CONFORMIDADE OPERACIONAL)
    # -------------------------------------------------------------------------
    elements.append(Paragraph("<b>7. QUESITOS SEM PONTUAÇÃO DIRETA (I-PLAN - CONFORMIDADE OPERACIONAL)</b>", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    data_sp = [["Quesito Target", "Resposta Apresentada", "Situação de Conformidade"]]

    for qsp in lista_alvo_iplan:
        info_sp = dados.get(qsp) or dados.get(f"Q_{qsp}")
        resp_sp = "-"
        status_sp = "Não Informado"

        if info_sp:
            resp_sp = info_sp.get("valor", "") if isinstance(info_sp, dict) else str(info_sp)
            status_sp = "Em Conformidade" if "sim" in str(resp_sp).lower() else "Fora de Conformidade"

        data_sp.append([qsp, Paragraph(str(resp_sp), styles['TdStyle']), status_sp])

    t_sp = Table(data_sp, colWidths=[100, 250, 130])
    t_sp.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
    ]))
    elements.append(t_sp)

    # Construção do Documento PDF
    doc.build(elements)
    return buffer.getvalue()

import asyncio
import logging
from nicegui import app, ui
from fastapi import Response

# -----------------------------------------------------------------------------
# 4. CARD E EVENTOS DE EMISSÃO DO RELATÓRIO PDF (NICEGUI)
# -----------------------------------------------------------------------------
def renderizar_card_relatorio_iamb(res_data=None, ano_sel=2026):
    """
    Componente NiceGUI para renderizar o Card de Emissão do PDF do I-PLAN.
    """
    res_data = res_data or {}
    
    with ui.card().classes('w-full p-6 my-6 border border-blue-200 rounded-lg shadow-sm bg-blue-50'):
        ui.label("📄 Emissão de Relatório Analítico - I-PLAN (Planejamento)").classes("text-xl font-bold text-blue-900 mb-1")
        ui.label("Gere o relatório completo em formato PDF contendo análises de tendência, diagnóstico de reincidências e metas ODS da Agenda 2030.").classes("text-sm text-gray-700 mb-4")

        async def baixar_pdf():
            n = ui.notify("Gerando PDF do I-PLAN, aguarde...", type="info", timeout=0)
            
            try:
                await asyncio.sleep(0.3)

                dados_locais = res_data or {}
                ano_alvo = int(ano_sel)

                total_pts = float(sum(
                    v.get("pontos", 0) 
                    for k, v in dados_locais.items() 
                    if isinstance(v, dict) and not str(k).startswith("COM_")
                ))

                faixa = converter_pontos_em_faixa_iegm(total_pts)
                historico_todos_anos = get_all_years_data() or {}

                pdf_bytes = gerar_relatorio_pdf(
                    dados=dados_locais,
                    ano=ano_alvo,
                    total=total_pts,
                    faixa=faixa,
                    todos_dados=historico_todos_anos
                )
                
                rota_pdf = f"/relatorio_iplan_temp_{ano_alvo}.pdf"
                
                try:
                    @app.get(rota_pdf)
                    def relatorio_endpoint():
                        return Response(content=pdf_bytes, media_type="application/pdf")
                except Exception:
                    pass

                ui.run_javascript(f"window.open('{rota_pdf}', '_blank');")
                ui.notify("Relatório I-PLAN aberto com sucesso!", type="positive")

            except Exception as e:
                print(f"ERRO CRÍTICO AO GERAR PDF I-PLAN: {e}")
                logging.exception("Erro no PDF I-PLAN:")
                ui.notify(f"Erro ao gerar o PDF: {e}", type="negative", close_button=True)

            finally:
                if n is not None:
                    try:
                        n.dismiss()
                    except Exception:
                        pass

        ui.button("📥 GERAR E ABRIR RELATÓRIO PDF (I-PLAN)", on_click=baixar_pdf).classes("bg-blue-700 text-white font-bold my-2")


def container_formulario_iplan(ano=None, res_data=None, ano_sel=2026):
    """
    Ponto de entrada público do módulo i-PLAN.

    A função render_conteudo é criada dentro de _render_formulario_iplan,
    portanto ela deve ser chamada nesse mesmo escopo. A versão anterior
    tentava chamá-la aqui fora, causando NameError.
    """
    ano_inicial = ano if ano is not None else ano_sel

    # Renderiza o formulário completo. Esta chamada cria e executa
    # render_conteudo dentro do escopo em que ele está definido.
    _render_formulario_iamb(ano=ano_inicial)

    # Renderiza o card do relatório PDF com os dados do ano atualmente ativo.
    ano_relatorio = int(app.storage.user.get("ano_referencia_global", ano_inicial))
    dados_relatorio = res_data if res_data is not None else load_respostas(ano_relatorio)
    renderizar_card_relatorio_iplan(
        res_data=dados_relatorio,
        ano_sel=ano_relatorio,
    )

                    
    render_conteudo()
