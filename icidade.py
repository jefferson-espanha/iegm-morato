import os
import json
import re
from datetime import datetime
from nicegui import app, ui
import psycopg2
from psycopg2.extras import RealDictCursor

# =============================================================================
# EXPRESSÕES REGULARES E CONFIGURAÇÃO DO BANCO DE DADOS (NEON)
# =============================================================================
REGEX_PURE_URL = r'https?://[^\s]+'

# Connection string configurada para o seu cluster no Neon
DATABASE_URL = os.getenv(
    "NEON_DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"
)

def get_db_connection():
    """Cria e retorna uma conexão ativa com a base de dados do Neon."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def load_respostas(ano):
    """
    Carrega o dicionário de respostas salvas para o ano selecionado no Neon DB.
    """
    query = """
        SELECT qid, valor, pontos, link, comentarios, status
        FROM respostas_icidade
        WHERE ano = %s;
    """
    respostas = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
                rows = cur.fetchall()
                for row in rows:
                    respostas[row['qid']] = {
                        "valor": row['valor'] if row['valor'] is not None else "",
                        "pontos": float(row['pontos']) if row['pontos'] is not None else 0.0,
                        "link": row['link'] if row['link'] is not None else "",
                        "comentarios": row['comentarios'] if isinstance(row['comentarios'], list) else [],
                        "status": row['status'] if row['status'] is not None else "Pendente"
                    }
    except Exception as e:
        print(f"❌ Erro ao carregar respostas do Neon DB: {e}")
        ui.notify(f"Erro ao carregar dados do banco Neon: {e}", type="negative")

    return respostas


def save_resposta(ano, qid, valor, pontos, link, comentarios=None, status="Pendente"):
    """
    Salva a resposta, link, pontos e o histórico de comentários de um quesito no Neon DB.
    Utiliza UPSERT (ON CONFLICT) para atualizar se já existir.
    """
    if comentarios is None:
        dados_atuais = load_respostas(ano).get(qid, {})
        comentarios = dados_atuais.get("comentarios", [])

    comentarios_validos = _obter_lista_comentarios({"comentarios": comentarios})
    comentarios_json = json.dumps(comentarios_validos)

    query = """
        INSERT INTO respostas_icidade (ano, qid, valor, pontos, link, comentarios, status)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
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
                cur.execute(query, (ano, qid, valor, pontos, link, comentarios_json, status))
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao salvar resposta no Neon DB: {e}")
        ui.notify(f"Erro ao salvar no banco Neon: {e}", type="negative")


def zerar_questionario_db(ano):
    """Limpa todas as respostas salvas do ano selecionado na tabela do Neon DB."""
    query = "DELETE FROM respostas_icidade WHERE ano = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (ano,))
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao zerar questionário no Neon DB: {e}")
        ui.notify(f"Erro ao apagar dados no banco Neon: {e}", type="negative")


def _obter_lista_comentarios(dados_banco):
    """Garante que o retorno de 'comentarios' seja sempre uma lista Python válida."""
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
    """Gera dados para download do relatório em PDF."""
    conteudo = f"RELATÓRIO TÉCNICO i-Cidade ({ano})\n"
    conteudo += f"Pontuação Total: {total_pts:.1f} pts | Faixa: {faixa}\n\n"
    for qid, dados in res_data.items():
        conteudo += f"Quesito {qid}: {dados.get('valor')} | Pontos: {dados.get('pontos')} | Link: {dados.get('link')}\n"
    return conteudo.encode('utf-8')


# =============================================================================
# 1. PAINEL LATERAL / CONTROLE
# =============================================================================
def render_painel_controle(on_refresh_callback=None):
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_atual = app.storage.user.get("ano_referencia_global", 2026)

    with ui.card().classes('w-full bg-slate-100 p-4 border rounded-lg shadow-sm'):
        ui.label("🛠️ Painel de Controle").classes('text-lg font-bold mb-2 text-blue-900')

        def ao_mudar_ano(e):
            app.storage.user["ano_referencia_global"] = e.value
            ui.notify(f"Ano alterado para {e.value}", type="info")
            if on_refresh_callback:
                on_refresh_callback()

        ui.select(
            options=anos, 
            value=ano_atual, 
            label="Ano de Referência:",
            on_change=ao_mudar_ano
        ).classes('w-full mb-4')

        # Cálculo de Pontuação e Faixa (busca direto do Neon)
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

        # Card de Pontuação
        with ui.card().classes('w-full mb-4 p-3 bg-white shadow-sm border'):
            ui.label("Pontuação Total").classes('text-xs text-gray-500 font-bold uppercase')
            ui.label(f"{total_pts:.1f} pts").classes('text-2xl font-black text-gray-800')
            
            with ui.row().classes('items-center gap-1 mt-1'):
                ui.label("Faixa:").classes('font-bold text-sm')
                ui.label(faixa).classes(f'text-xl font-bold {cor}')

        ui.separator().classes('my-2')
        ui.label("⚙️ Gerenciamento").classes('font-bold text-sm mb-2')

        def atualizar_dados():
            ui.notify("Questionário atualizado!", type="positive", icon="refresh")
            if on_refresh_callback:
                on_refresh_callback()

        ui.button("🔄 Atualizar Questionário", on_click=atualizar_dados).classes('w-full bg-blue-700 text-white mb-2')
        ui.separator().classes('my-2')

        # Modal de Confirmação para Zerar
        with ui.dialog() as dialog_zerar, ui.card().classes('w-96 p-4'):
            ui.label("🔒 Confirmação de Segurança").classes('text-lg font-bold text-red-600')
            ui.label(f"Você está prestes a apagar todas as respostas de {ano_atual}. Esta ação é irreversível!").classes('text-sm my-2')
            
            input_senha = ui.input("Digite a senha de administrador:", password=True).classes('w-full mb-4')

            def executar_zerar():
                if input_senha.value == "fidelios":
                    zerar_questionario_db(ano_atual)
                    ui.notify(f"✅ Questionário de {ano_atual} foi zerado!", type="positive")
                    dialog_zerar.close()
                    if on_refresh_callback:
                        on_refresh_callback()
                else:
                    ui.notify("❌ Senha incorreta!", type="negative")

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button("Cancelar", on_click=dialog_zerar.close).props('flat')
                ui.button("Confirmar e Zerar", on_click=executar_zerar).classes('bg-red-600 text-white')

        with ui.row().classes('w-full gap-2 no-wrap'):
            pdf_bytes = gerar_relatorio_pdf_bytes(res_data, ano_atual, total_pts, faixa)
            ui.button("📄 Relatório", on_click=lambda: ui.download(pdf_bytes, f"Relatorio_iCidade_{ano_atual}.pdf")).classes('flex-1 bg-green-700 text-white')
            ui.button("🗑️ Zerar", on_click=dialog_zerar.open).classes('flex-1 bg-red-700 text-white')

        ui.separator().classes('my-4')
        ui.html("""
            <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 12px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
        """).classes('w-full')


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
            
    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    
    with ui.expansion(f"💬 Diálogo Interno {qid} | Status: {badge_status}", value=(status_global == "Pendente")).classes('w-full border rounded p-2 mt-3 bg-gray-50'):
        
        def alterar_status(e):
            novo_st = e.value
            log = {
                "autor": "Sistema / " + usuario_atual,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "texto": f"ℹ️ Alterou o status do quesito para: **{novo_st.upper()}**.",
                "status_definido": novo_st
            }
            historico.append(log)
            save_resposta(
                ano=ano_sel, qid=qid, 
                valor=dados_q.get("valor", ""), 
                pontos=dados_q.get("pontos", 0.0), 
                link=dados_q.get("link", ""), 
                comentarios=historico,
                status=novo_st
            )
            ui.notify(f"Status alterado para {novo_st}", type="info")
            if on_save_callback:
                on_save_callback()

        ui.radio(["Resolvido", "Pendente"], value=status_global, on_change=alterar_status).props('inline')

        if historico:
            for idx, com in enumerate(historico):
                if isinstance(com, str):
                    com = {"autor": "Usuário", "data": "", "texto": com}

                autor = com.get('autor', 'Anônimo')
                data_com = com.get('data', '')
                texto_com = com.get('texto', '')

                def deletar_comentario(i=idx):
                    historico.pop(i)
                    save_resposta(
                        ano=ano_sel, qid=qid, 
                        valor=dados_q.get("valor", ""), 
                        pontos=dados_q.get("pontos", 0.0), 
                        link=dados_q.get("link", ""), 
                        comentarios=historico,
                        status=status_global
                    )
                    ui.notify("Comentário removido.", type="warning")
                    if on_save_callback:
                        on_save_callback()

                with ui.row().classes('w-full items-center justify-between no-wrap mb-2'):
                    if "Sistema /" in autor:
                        ui.html(
                            f"""<div style="background-color: #f1f3f5; padding: 6px 12px; border-radius: 6px; border-left: 3px solid #ced4da; width: 100%;">
                                <span style="font-size: 11px; color: #6c757d; font-style: italic;">{autor} - {data_com}</span>
                                <p style="margin: 2px 0 0 0; font-size: 12px; color: #495057;">{texto_com}</p>
                            </div>"""
                        ).classes('w-full')
                    else:
                        ui.html(
                            f"""<div style="background-color: #ffffff; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #1e88e5; border: 1px solid #e0e0e0; width: 100%;">
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>"""
                        ).classes('w-full')
                    
                    ui.button('🗑️', on_click=deletar_comentario).props('flat dense')

        input_novo_comentario = ui.textarea(placeholder="Novo comentário...").classes('w-full').props('outlined rows=2')
        
        def postar_comentario():
            txt = input_novo_comentario.value.strip()
            if txt:
                historico.append({
                    "autor": usuario_atual,
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": txt,
                    "status_definido": status_global
                })
                save_resposta(
                    ano=ano_sel, qid=qid, 
                    valor=dados_q.get("valor", ""), 
                    pontos=dados_q.get("pontos", 0.0), 
                    link=dados_q.get("link", ""), 
                    comentarios=historico,
                    status=status_global
                )
                ui.notify("Comentário publicado!", type="positive")
                if on_save_callback:
                    on_save_callback()

        ui.button("Postar Comentário", on_click=postar_comentario).classes('bg-blue-600 text-white mt-2')


# =============================================================================
# 3. RENDERIZADOR DE QUESITO
# =============================================================================
def render_quesito(ano, res_data, qid, titulo, pergunta, opcoes=None, is_text_area=False, placeholder_text="", on_save_callback=None):
    d_data = res_data.get(qid) or {"valor": "Selecione..." if opcoes else "", "pontos": 0.0, "link": "", "comentarios": [], "status": "Pendente"}
    
    with ui.card().classes('w-full mb-4 p-4 border rounded-lg shadow-sm'):
        with ui.expansion(f"📌 Quesito {qid} - {titulo}", value=True).classes('w-full font-bold'):
            ui.label(f"{qid} • {titulo}").classes('text-h6 text-primary mt-2')
            ui.label(pergunta).classes('text-body1 font-bold my-2')
            ui.label("ℹ Preencha os campos abaixo e clique no botão de salvar.").classes('text-caption text-grey-6 mb-4')

            with ui.row().classes('w-full gap-4 items-start'):
                with ui.column().classes('flex-1'):
                    if opcoes:
                        lista_opcoes = list(opcoes.keys())
                        v_salvo = d_data.get("valor", "Selecione...")
                        valor_inicial = v_salvo if v_salvo in lista_opcoes else lista_opcoes[0]
                        
                        input_valor = ui.radio(
                            options=lista_opcoes, 
                            value=valor_inicial
                        ).classes('gap-2')
                    else:
                        input_valor = ui.textarea(
                            label="Dados do quesito:",
                            placeholder=placeholder_text,
                            value=d_data.get("valor", "")
                        ).classes('w-full').props('outlined rows=3')

                with ui.column().classes('flex-1'):
                    input_link = ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=d_data.get("link", "")
                    ).classes('w-full').props('outlined rows=3')

                    container_links = ui.row().classes('mt-1')
                    
                    def atualizar_links_visuais():
                        container_links.clear()
                        txt_total = (input_valor.value if is_text_area else "") + " " + (input_link.value or "")
                        links = re.findall(REGEX_PURE_URL, txt_total)
                        if links:
                            with container_links:
                                ui.label("Links Ativos: ").classes('font-bold text-caption')
                                for url in links:
                                    ui.link(url, url=url, new_tab=True).classes('text-caption text-blue-6 mr-2')

                    input_link.on('update:model-value', atualizar_links_visuais)
                    if is_text_area:
                        input_valor.on('update:model-value', atualizar_links_visuais)
                    
                    atualizar_links_visuais()

            lbl_pontos = ui.html().classes('mt-3 font-bold')

            def atualizar_label_pontos(pts, val):
                if opcoes is None:
                    lbl_pontos.set_content(f"<span style='color:#6c757d;'>📊 Impacto de Pontuação no Quesito {qid}: 0.0 pontos (Informativo)</span>")
                else:
                    cor = "#28a745" if pts > 0 else ("#dc3545" if val != "Selecione..." else "#6c757d")
                    lbl_pontos.set_content(f"<span style='color:{cor};'>📊 Impacto de Pontuação no Quesito {qid}: {pts:.1f} pontos</span>")

            atualizar_label_pontos(d_data.get("pontos", 0.0), d_data.get("valor", ""))

            def salvar():
                val = input_valor.value
                link = input_link.value
                pts = opcoes.get(val, 0.0) if opcoes else 0.0
                st = d_data.get("status", "Pendente")
                
                save_resposta(ano, qid, val, pts, link, status=st)
                atualizar_label_pontos(pts, val)
                ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive", icon="check_circle")
                if on_save_callback:
                    on_save_callback()

            ui.button(f"💾 Salvar Quesito {qid}", on_click=salvar).classes('bg-blue-800 text-white mt-4')

            # Renderiza o bloco de comentários do quesito
            bloco_comentarios(qid, res_data, on_save_callback=on_save_callback)


# =============================================================================
# 4. CONTAINER PRINCIPAL REFRESHABLE
# =============================================================================
@ui.refreshable
def container_formulario_icidade():
    ano_sel = app.storage.user.get("ano_referencia_global", 2026)
    res_data = load_respostas(ano_sel)

    with ui.grid(columns=4).classes('w-full gap-6 items-start'):
        
        # Coluna da Esquerda (Painel de Controle)
        with ui.column().classes('col-span-1 w-full'):
            render_painel_controle(on_refresh_callback=container_formulario_icidade.refresh)

        # Coluna da Direita (Quesitos do Formulário)
        with ui.column().classes('col-span-3 w-full'):
            ui.label(f"Formulário COMPDEC - Defesa Civil ({ano_sel})").classes('text-h4 mb-1 font-bold text-blue-900')
            ui.label("Preencha as evidências e questões do indicador i-Cidade.").classes('text-gray-600 mb-6')

            # QUESITO 1.0
            opcoes_10 = {
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.0",
                titulo="Criação da COMPDEC ou Órgão Similar",
                pergunta="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil-COMPDEC ou órgão similar responsável pela execução, coordenação e mobilização de todas as ações de defesa civil no município?",
                opcoes=opcoes_10,
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.1
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.1",
                titulo="Dados do Instrumento Normativo COMPDEC",
                pergunta="Informe o Instrumento normativo, Número e Data da publicação da criação da COMPDEC ou órgão similar:",
                is_text_area=True,
                placeholder_text="Ex: Decreto nº 123 de 01/01/2025",
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.2
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.2",
                titulo="Endereço Eletrônico do Instrumento Normativo",
                pergunta="Informe a página eletrônica (link na internet) do instrumento normativo que criou a COMPDEC ou órgão similar:",
                is_text_area=True,
                placeholder_text="https://www.municipio.sp.gov.br/legislacao",
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.3
            opcoes_13 = {
                "Selecione...": 0.0,
                "Gabinete do Prefeito (05 pts)": 5.0,
                "Segurança Pública (00 pts)": 0.0,
                "Controladoria (00 pts)": 0.0,
                "Outra (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.3",
                titulo="Secretaria ou Diretoria de Subordinação",
                pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
                opcoes=opcoes_13,
                on_save_callback=container_formulario_icidade.refresh
            )

            # QUESITO 1.4
            opcoes_14 = {
                "Selecione...": 0.0,
                "Sim, inclusive com a participação de entidades privadas e da comunidade (50 pts)": 50.0,
                "Sim, com participação de entidades privadas (20 pts)": 20.0,
                "Sim, com participação da comunidade (20 pts)": 20.0,
                "Sim, apenas com representantes da administração municipal (10 pts)": 10.0,
                "Não atuam de forma sistêmica (00 pts)": 0.0
            }
            render_quesito(
                ano=ano_sel,
                res_data=res_data,
                qid="1.4",
                titulo="Atuação Sistêmica e Articulação da Defesa Civil",
                pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a Política Nacional de Proteção e Defesa Civil - PNPDEC?",
                opcoes=opcoes_14,
                on_save_callback=container_formulario_icidade.refresh
            )


# =============================================================================
# 5. ENTRY POINT PRINCIPAL
# =============================================================================
def mostrar_formulario_icidade():
    container_formulario_icidade()
