import ast
from datetime import datetime
import json
import os
import re
from nicegui import app, ui
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# =============================================================================
# EXPRESSÕES REGULARES E CONFIGURAÇÃO DO BANCO DE DADOS (NEON)
# =============================================================================
REGEX_PURE_URL = r"https?://[^\s]+"

DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require",
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Garante que a tabela respostas_iamb exista com as colunas certas."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS respostas_iamb (
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
        print(f"❌ Erro ao inicializar tabela respostas_iamb: {e}")


init_db()


def load_respostas(ano):
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_iamb
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                rows = cur.fetchall()
                for row in rows:
                    link_val = row["link"]
                    if link_val is None or link_val == "EMPTY_STRING":
                        link_val = ""

                    respostas[row["qid"]] = {
                        "valor": row["valor"] if row["valor"] is not None else "",
                        "pontos": (
                            float(row["pontos"])
                            if row["pontos"] is not None
                            else 0.0
                        ),
                        "link": link_val,
                        "comentarios": (
                            row["comentarios"]
                            if isinstance(row["comentarios"], list)
                            else []
                        ),
                        "status": (
                            row["status"]
                            if row["status"] is not None
                            else "Pendente"
                        ),
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB (iAmb): {e}")

    return respostas


def save_resposta(
    ano, qid, valor, pontos, link, comentarios=None, status="Pendente"
):
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
    link_final = link.strip() if link else ""

    query = """
        INSERT INTO respostas_iamb (ano, qid, valor, pontos, link, comentarios, status)
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
                        ano,
                        str(qid),
                        str(valor),
                        float(pontos),
                        link_final,
                        Json(comentarios_validos),
                        str(status),
                    ),
                )
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao salvar resposta no Neon DB (iAmb): {e}")


def zerar_questionario_db(ano):
    query = "DELETE FROM respostas_iamb WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB (iAmb): {e}")


def _obter_lista_comentarios(dados_banco):
    raw = dados_banco.get("comentarios", [])
    if isinstance(raw, str):
        if raw in ["EMPTY_STRING", "", "null", "None"]:
            return []
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, list):
        return raw
    return []


def gerar_relatorio_pdf_bytes(res_data, ano, total_pts, faixa):
    conteudo = f"RELATÓRIO TÉCNICO iAmb ({ano})\n"
    conteudo += f"Pontuação Total: {total_pts:.1f} pts | Faixa: {faixa}\n\n"
    for qid, dados in res_data.items():
        conteudo += f"Quesito {qid}: {dados.get('valor')} | Pontos: {dados.get('pontos')} | Link: {dados.get('link')}\n"
    return conteudo.encode("utf-8")


# =============================================================================
# 1. PAINEL LATERAL / CONTROLE
# =============================================================================
def render_painel_controle(on_refresh_callback=None):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_atual = app.storage.user.get("ano_referencia_global", 2026)

    with ui.card().classes("w-full bg-slate-100 p-4 border rounded-lg shadow-sm"):
        ui.label("🛠️ Painel de Controle (iAmb)").classes(
            "text-lg font-bold mb-2 text-green-900"
        )

        def ao_mudar_ano(e):
            app.storage.user["ano_referencia_global"] = e.value
            ui.notify(f"Ano alterado para {e.value}", type="info")
            if on_refresh_callback:
                on_refresh_callback()

        ui.select(
            options=anos,
            value=ano_atual,
            label="Ano de Referência:",
            on_change=ao_mudar_ano,
        ).classes("w-full mb-4")

        res_data = load_respostas(ano_atual)
        total_pts = sum(
            float(item.get("pontos", 0)) for item in res_data.values()
        )

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

        with ui.card().classes("w-full mb-4 p-3 bg-white shadow-sm border"):
            ui.label("Pontuação Total").classes(
                "text-xs text-gray-500 font-bold uppercase"
            )
            ui.label(f"{total_pts:.1f} pts").classes(
                "text-2xl font-black text-gray-800"
            )

            with ui.row().classes("items-center gap-1 mt-1"):
                ui.label("Faixa:").classes("font-bold text-sm")
                ui.label(faixa).classes(f"text-xl font-bold {cor}")

        ui.separator().classes("my-2")
        ui.label("⚙️ Gerenciamento").classes("font-bold text-sm mb-2")

        def atualizar_dados():
            ui.notify("Questionário iAmb atualizado!", type="positive", icon="refresh")
            if on_refresh_callback:
                on_refresh_callback()

        ui.button("🔄 Atualizar Questionário", on_click=atualizar_dados).classes(
            "w-full bg-green-700 text-white mb-2"
        )
        ui.separator().classes("my-2")

        with ui.dialog() as dialog_zerar, ui.card().classes("w-96 p-4"):
            ui.label("🔒 Confirmação de Segurança").classes(
                "text-lg font-bold text-red-600"
            )
            ui.label(
                f"Você está prestes a apagar todas as respostas do iAmb de {ano_atual}. Esta ação é irreversível!"
            ).classes("text-sm my-2")

            input_senha = ui.input(
                "Digite a senha de administrador:", password=True
            ).classes("w-full mb-4")

            def executar_zerar():
                if input_senha.value == "fidelios":
                    zerar_questionario_db(ano_atual)
                    ui.notify(
                        f"✅ Questionário iAmb de {ano_atual} foi zerado!",
                        type="positive",
                    )
                    dialog_zerar.close()
                    if on_refresh_callback:
                        on_refresh_callback()
                else:
                    ui.notify("❌ Senha incorreta!", type="negative")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancelar", on_click=dialog_zerar.close).props("flat")
                ui.button(
                    "Confirmar e Zerar", on_click=executar_zerar
                ).classes("bg-red-600 text-white")

        with ui.row().classes("w-full gap-2 no-wrap"):
            pdf_bytes = gerar_relatorio_pdf_bytes(
                res_data, ano_atual, total_pts, faixa
            )
            ui.button(
                "📄 Relatório",
                on_click=lambda: ui.download(
                    pdf_bytes, f"Relatorio_iAmb_{ano_atual}.pdf"
                ),
            ).classes("flex-1 bg-green-700 text-white")
            ui.button("🗑️ Zerar", on_click=dialog_zerar.open).classes(
                "flex-1 bg-red-700 text-white"
            )

        ui.separator().classes("my-4")
        ui.html("""
            <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 12px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
        """).classes("w-full")


# =============================================================================
# 2. BLOCO DE COMENTÁRIOS INTERNOS
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
                            f"""<div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #2e7d32; border: 1px solid #e0e0e0; width: 100%;">
                                <span style="font-size: 11px; color: #2e7d32; font-weight: bold;">👤 {autor}</span> 
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
            "bg-green-700 text-white mt-2"
        )


# =============================================================================
# 3. RENDERIZADOR DE QUESITO
# =============================================================================
def render_quesito(ano, res_data, qid, titulo, pergunta, opcoes, placeholder_link, on_save_callback):
    dados_qid = res_data.get(qid, {})
    valor_atual = dados_qid.get("valor", "Selecione...")
    link_atual = dados_qid.get("link", "")

    with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
        ui.label(f"📌 Quesito {qid} - {titulo}").classes("text-lg font-bold text-blue-900 mb-1")
        ui.label(pergunta).classes("text-base font-semibold text-gray-800 mt-2 mb-4")

        # Seleção de opções e link
        radio_opcao = ui.radio(list(opcoes.keys()), value=valor_atual).classes("mb-4")
        input_link = ui.textarea("Página Eletrônica (Link / Evidência):", value=link_atual, placeholder=placeholder_link).classes("w-full mb-4")

        def cb_salvar():
            try:
                val_sel = radio_opcao.value
                pts_sel = opcoes.get(val_sel, 0.0)
                lnk_sel = input_link.value or ""

                # 1. Grava na tabela oficial do iAmb
                salvar_no_banco_iamb(qid, ano, val_sel, pts_sel, lnk_sel)

                # 2. Atualiza a memória local para recalcular o Painel
                res_data[qid] = {
                    "valor": val_sel,
                    "pontos": pts_sel,
                    "link": lnk_sel
                }

                ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive")
                if on_save_callback:
                    on_save_callback()
            except Exception as err:
                ui.notify(f"Erro ao salvar Quesito {qid}: {err}", type="negative")

        pts_atuais = dados_qid.get("pontos", 0.0)
        cor_txt = "text-green-600" if pts_atuais > 0 else "text-gray-500"

        with ui.row().classes("w-full justify-between items-center mb-4"):
            ui.label(f"📊 Impacto de Pontuação no Quesito {qid}: +{pts_atuais:.1f} pontos").classes(f"text-sm font-bold {cor_txt}")
            ui.button(f"SALVAR QUESITO {qid}", on_click=cb_salvar, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

        ui.separator().classes("my-2")
        bloco_comentarios(qid, res_data, ano)


# =============================================================================
# 4. CONTAINER PRINCIPAL REFRESHABLE
# =============================================================================
@ui.refreshable
def container_formulario_iamb():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes("w-full gap-6 items-start"):
        with ui.column().classes("col-span-1 w-full"):
            render_painel_controle(
                on_refresh_callback=container_formulario_iamb.refresh
            )

        with ui.column().classes("col-span-3 w-full"):
            ui.label(
                f"Formulário iAmb - Gestão Ambiental ({ano_sel})"
            ).classes("text-h4 mb-1 font-bold text-green-900")
            ui.label(
                "Preencha as evidências e questões do indicador iAmb."
            ).classes("text-gray-600 mb-6")

            ui.label("1.0 Governança e Licenciamento Ambiental").classes(
                "text-h5 font-bold my-4 text-green-900"
            )

            # =============================================================================
            # HELPER DE PERSISTÊNCIA CORRIGIDO PARA A TABELA OFICIAL (iAmb)
            # =============================================================================
            def salvar_no_banco_iamb(qid_val, ano_val, valor_val, pontos_val, link_val):
                """Grava as respostas diretamente na tabela respostas_iamb_oficial."""
                try:
                    with get_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO respostas_iamb_oficial (qid, ano, valor, pontos, link, updated_at)
                                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                ON CONFLICT (ano, qid) 
                                DO UPDATE SET 
                                    valor = EXCLUDED.valor,
                                    pontos = EXCLUDED.pontos,
                                    link = EXCLUDED.link,
                                    updated_at = CURRENT_TIMESTAMP;
                            """, (qid_val, ano_val, str(valor_val), float(pontos_val), str(link_val)))
                            conn.commit()
                except Exception as err_db:
                    print(f"❌ Erro de gravação no banco (Quesito {qid_val}): {err_db}")
                    raise err_db

            # =============================================================================
            # QUESITO 1.0 • ESTRUTURA ORGANIZACIONAL DE MEIO AMBIENTE
            # =============================================================================
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Estrutura Organizacional de Meio Ambiente",
                pergunta="A prefeitura possui alguma estrutura organizacional para tratar de assuntos ligados ao Meio Ambiente Municipal?",
                opcoes=opcoes_10,
                placeholder_link="Insira o link da lei da estrutura administrativa, organograma ou decreto...",
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 1.1 • RECURSOS HUMANOS EM MEIO AMBIENTE
            # =============================================================================
            opcoes_11 = {
                "Selecione...": 0.0,
                "Sim – 30 pts": 30.0,
                "Não – 00 pts": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1",
                titulo="Recursos Humanos em Meio Ambiente",
                pergunta="A Prefeitura possui recursos humanos para operacionalização dos assuntos ligados ao Meio Ambiente?",
                opcoes=opcoes_11,
                placeholder_link="Insira o link da folha simplificada, ato de nomeação ou relatório do RH...",
                on_save_callback=container_formulario_iamb.refresh,
            )

            # =============================================================================
            # QUESITO 1.1.1 • QUANTIDADE DE RECURSOS HUMANOS
            # =============================================================================
            with ui.card().classes("w-full p-6 mb-6 border border-gray-200 rounded-lg shadow-sm bg-white"):
                ui.label("📌 Quesito 1.1.1 - Quantidade de Servidores de Meio Ambiente").classes("text-lg font-bold text-blue-900 mb-1")
                ui.label("Informe a quantidade de servidores:").classes("text-base font-semibold text-gray-800 mt-2 mb-1")
                
                with ui.card().classes("w-full p-3 mb-4 bg-blue-50 border-l-4 border-blue-600 rounded-r-md shadow-none"):
                    ui.label("Fórmula de cálculo:").classes("text-xs font-bold text-blue-900 uppercase tracking-wide")
                    ui.label(
                        "Nº de efetivos + Nº de comissionados + Nº de terceirizados/contratados > 0 — 30 pontos"
                    ).classes("text-sm text-blue-800 font-medium")

                def parse_int_seguro(val):
                    if val is None:
                        return 0
                    if isinstance(val, (int, float)):
                        return int(val)
                    val_str = str(val).strip()
                    if not val_str or not val_str.isdigit():
                        return 0
                    return int(val_str)

                # Recuperação dos dados do dicionário res_data
                d111 = res_data.get("1.1.1") or {}
                if not isinstance(d111, dict):
                    d111 = {"valor": "0", "pontos": 0.0, "link": ""}

                v_efe_i, v_com_i, v_ter_i = 0, 0, 0
                evidencia_111_salva = ""
                raw_link = str(d111.get("link") or "")

                if raw_link:
                    if "|LINK:" in raw_link:
                        contadores_part, evidencia_111_salva = raw_link.split("|LINK:", 1)
                    else:
                        contadores_part, evidencia_111_salva = raw_link, ""

                    import re
                    match_ef = re.search(r'EF:(\d+)', contadores_part)
                    match_co = re.search(r'CO:(\d+)', contadores_part)
                    match_te = re.search(r'TE:(\d+)', contadores_part)

                    v_efe_i = int(match_ef.group(1)) if match_ef else 0
                    v_com_i = int(match_co.group(1)) if match_co else 0
                    v_ter_i = int(match_te.group(1)) if match_te else 0

                state_111 = {
                    "efe": v_efe_i,
                    "com": v_com_i,
                    "ter": v_ter_i,
                    "link": evidencia_111_salva
                }

                def cb_processa_e_salva_111():
                    try:
                        ef_val = parse_int_seguro(state_111["efe"])
                        co_val = parse_int_seguro(state_111["com"])
                        te_val = parse_int_seguro(state_111["ter"])
                        lnk_val = str(state_111["link"] or "").strip()

                        total_p = ef_val + co_val + te_val
                        pts_calculados = 30.0 if total_p > 0 else 0.0
                        composite_string = f"EF:{ef_val},CO:{co_val},TE:{te_val}|LINK:{lnk_val}"

                        # Salva na tabela do iAmb
                        salvar_no_banco_iamb("1.1.1", ano_sel, str(total_p), pts_calculados, composite_string)
                        
                        # Atualiza a memória local para cálculo imediato do painel
                        res_data["1.1.1"] = {
                            "valor": str(total_p), 
                            "pontos": pts_calculados, 
                            "link": composite_string
                        }
                        
                        ui.notify("Quesito 1.1.1 salvo com sucesso!", type="positive")
                        container_formulario_iamb.refresh()
                    except Exception as err:
                        ui.notify(f"Erro ao salvar Quesito 1.1.1: {err}", type="negative")

                # Inputs Numéricos
                with ui.grid(columns=2).classes("w-full gap-4 mb-4 md:grid-cols-3"):
                    ui.number(
                        "Nº de efetivos:", 
                        value=v_efe_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_111, "efe")
                    
                    ui.number(
                        "Nº de comissionados:", 
                        value=v_com_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_111, "com")
                    
                    ui.number(
                        "Nº de terceirizados/contratados:", 
                        value=v_ter_i, 
                        min=0, 
                        step=1
                    ).classes("w-full").bind_value(state_111, "ter")

                ui.textarea(
                    "Página Eletrônica (Link / Evidência do Pessoal de Meio Ambiente):",
                    value=evidencia_111_salva,
                    placeholder="Insira o link da portaria de lotação, contratos de terceirização ou folha do setor de Meio Ambiente..."
                ).classes("w-full mb-4").bind_value(state_111, "link")

                total_pessoal = parse_int_seguro(d111.get("valor"))
                pts_atuais_111 = float(d111.get("pontos") or 0.0)
                cor_txt_111 = "text-green-600" if pts_atuais_111 == 30.0 else "text-gray-500"

                with ui.row().classes("w-full justify-between items-center mb-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"👥 Total de Servidores Computados: {total_pessoal} funcionário(s)").classes("text-sm font-semibold text-gray-700")
                        ui.label(f"📊 Impacto de Pontuação no Quesito 1.1.1: +{pts_atuais_111:.1f} pontos").classes(f"text-sm font-bold {cor_txt_111}")
                    
                    ui.button("Salvar Quesito 1.1.1", on_click=cb_processa_e_salva_111, icon="save").classes("bg-blue-800 text-white font-medium px-4 py-2 rounded-md")

                ui.separator().classes("my-2")
                bloco_comentarios("1.1.1", res_data, ano_sel)

            # =============================================================================
            # QUESITO 1.1.2 • TREINAMENTO DOS SERVIDORES EM MEIO AMBIENTE
            # =============================================================================
            opcoes_112 = {
                "Selecione...": 0.0,
                "Sim – 20 pts": 20.0,
                "Não – 00 pts": 0.0,
            }

            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1.2",
                titulo="Treinamento dos Servidores de Meio Ambiente",
                pergunta="Os servidores responsáveis pelo Meio Ambiente receberam treinamento específico voltado ao Meio Ambiente em 2025?",
                opcoes=opcoes_112,
                placeholder_link="Insira o link dos certificados, lista de presença ou comprovante de capacitação...",
                on_save_callback=container_formulario_iamb.refresh,
            )
