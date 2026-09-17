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
                    opcoes_1451 = {
                        "Receitas": 0.0,
                        "Despesas": 0.0,
                        "Administração de pessoal": 0.0,
                        "Estoques e almoxarifados": 0.0,
                        "Administração do patrimônio": 0.0,
                        "Cumprimento das metas do PPA e a execução dos programas de governo e dos orçamentos (LOA e LDO)": 0.0,
                        "Cumprimento das metas fiscais, físicas e de resultados dos programas de governo, no que tange a eficiência, eficácia e efetividade": 0.0,
                        "Aplicação de recursos públicos por entidades de direito público": 0.0,
                        "Aplicação de recursos públicos por entidades de direito privado": 0.0,
                        "Os limites e condições para a inscrição de despesas em Restos a Pagar": 0.0,
                        "Cumprimento da legislação de licitações e fiscalização de contratos": 0.0,
                        "Cumprimento do limite de gastos totais dos legislativos municipais, inclusive no que se refere ao atingimento de metas fiscais (Gestão Fiscal)": 0.0,
                        "Transferência para o Legislativo Municipal (Repasses de Duodécimos)": 0.0,
                        "Contabilidade": 0.0,
                        "Transparência": 0.0,
                        "Lei de Acesso à Informação": 0.0,
                        "Outros": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="14.5.1",
                        titulo="Atividades Previstas no Plano Operativo Anual",
                        pergunta="Assinale as atividades previstas no Plano Operativo Anual (1 a 5 assinalados = 1 pt | 6 a 10 = 3 pts | 11 ou mais = 5 pts):",
                        tipo_input="checkbox",
                        opcoes=opcoes_1451,
                        placeholder_link="Insira o link comprovando o escopo do Plano Operativo...",
                        on_save_callback=render_conteudo.refresh,
                    )

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
                    # Regra de cálculo: Perde -0,5 pt para cada item principal não assinalado.
                    opcoes_155 = {
                        "Link da página eletrônica da ouvidoria no sítio da Prefeitura Municipal": 0.0,
                        "Utilização de outras plataformas digitais para a divulgação da missão, do modo de trabalho das ouvidorias e incentivando a participação popular. Ex.: instagram, facebook, twitter etc.": 0.0,
                        "Realização de palestras para grupos e instituições. Ex.: escolas, igrejas, associações civis, outros grupos organizados etc.": 0.0,
                        "Realização de eventos que estimulem a participação e coleta das demandas sociais. Ex.: realização de audiências públicas para divulgação dos trabalhos desempenhados pela ouvidoria e ouvir as demandas da população.": 0.0,
                        "Outras": 0.0,
                    }
                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="15.5",
                        titulo="Iniciativas de Divulgação e Mobilização Social das Ouvidorias",
                        pergunta="Assinale as iniciativas de divulgação e mobilização social das ouvidorias (Perde -0,5 pt para cada item não assinalado):",
                        tipo_input="checkbox",
                        opcoes=opcoes_155,
                        placeholder_link="Insira o link com comprovação das ações de divulgação...",
                        on_save_callback=render_conteudo.refresh,
                    )

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
                    # QUESITO P3 (Percentual de Alteração do Planejamento Inicial - Calculadora Integrada)
                    # ==========================================
                    opcoes_p3 = {
                        "K >= 1,3 (Penalidade máxima) – -30 pts": -30.0,
                        "0,9 < K < 1,3 (Dentro do limite) – 00 pts": 0.0,
                        "0,5 < K <= 0,9 (Proporcional: ((0.9-K)/0.4)*-30)": 0.0,
                        "K <= 0,5 (Penalidade máxima) – -30 pts": -30.0,
                    }

                    # Dicionário local para armazenar o estado da calculadora
                    calc_p3_state = {"pts": 0.0}

                    with ui.card().classes("w-full p-4 mb-2 bg-blue-50 border border-blue-200 rounded-lg"):
                        ui.label("🧮 Calculadora Automática do Indicador K (Quesito P3)").classes("font-bold text-blue-700 mb-2")
                        with ui.grid(columns=2).classes("w-full gap-4"):
                            input_i = ui.number(label="Valor Inicial LOA (I)", value=0.0, format="%.2f").classes("w-full").props("outlined bg-white")
                            input_j = ui.number(label="Valor Final Apurado (J)", value=0.0, format="%.2f").classes("w-full").props("outlined bg-white")
                        
                        lbl_resultado_k = ui.label("Informe os valores acima para calcular K e a pontuação.").classes("text-sm font-semibold text-gray-700 mt-2")

                        def recalcular_p3(_=None):
                            i = input_i.value or 0.0
                            j = input_j.value or 0.0
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
                                calc_p3_state["pts"] = pts
                                lbl_resultado_k.set_text(f"Resultado: K = {k:.4f} ➔ Pontuação Calculada: {pts:.2f} pontos")
                            else:
                                lbl_resultado_k.set_text("Aguardando valor de 'I' maior que zero...")

                        input_i.on("update:model-value", recalcular_p3)
                        input_j.on("update:model-value", recalcular_p3)

                    render_quesito(
                        ano=ano_sel,
                        res_data=res_data,
                        qid="P3",
                        titulo="Percentual de Alteração do Planejamento Inicial",
                        pergunta="Total dos valores dos programas na LOA (I) comparado aos valores finais (J). Selecione a faixa apurada na calculadora acima:",
                        tipo_input="radio",
                        opcoes=opcoes_p3,
                        placeholder_link="Insira o link ou anexo com a memória de cálculo dos valores I e J...",
                        on_save_callback=render_conteudo.refresh,
                    )
                    
    render_conteudo()
