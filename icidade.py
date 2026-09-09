import base64
from datetime import datetime, date
import json
import os
import sys
from nicegui import app, ui

# =============================================================================
# INJEÇÃO E CONFIGURAÇÃO DE AMBIENTE (NEON / POSTGRES)
# =============================================================================
NEON_URL = "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"

# Garantir visibilidade do diretório atual
current_dir = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in locals()
    else os.getcwd()
)
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Helper para compatibilidade de simulação de st.secrets ou variáveis de ambiente
def get_secret(key, default=None):
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key == "DATABASE_URL":
        return os.environ.get("DATABASE_URL", NEON_URL)
    return default

# --- CARREGAMENTO OTIMIZADO DE MÓDULOS ---
def import_local_module(module_name):
    try:
        import importlib
        return importlib.import_module(module_name)
    except Exception:
        return None

# Importação de Módulos IEG-M (dando preferência a 'icidade')
icidade = import_local_module("icidade") or import_local_module("icidade_completo")
igov = import_local_module("igov")
iamb = import_local_module("iamb")
ifiscal = import_local_module("ifiscal")
iplan = import_local_module("iplan")
ieduc = import_local_module("ieduc")
isaude = import_local_module("isaude")
iegm_final = import_local_module("iegmfinal")

# Módulos de Gestão
bib_core = import_local_module("biblioteca")
admin_core = import_local_module("administrador")
atividade = import_local_module("atividade")
plano_acao = import_local_module("plano_acao")

# Módulo Inteligência Artificial
hal_core = import_local_module("hal")

# Mock / Stubs de persistência no banco para integridade funcional dos callbacks
def load_respostas(ano):
    return app.storage.user.get(f"respostas_{ano}", {})

def save_resp(qid, valor, pontos, link, comentarios, ano=2026):
    chave_ano = f"respostas_{ano}"
    respostas = app.storage.user.get(chave_ano, {})
    respostas[qid] = {
        "valor": valor,
        "pontos": pontos,
        "link": link,
        "comentarios": comentarios
    }
    app.storage.user[chave_ano] = respostas

# =============================================================================
# HELPER DE SANITIZAÇÃO DE COMENTÁRIOS
# =============================================================================
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

# =============================================================================
# CALLBACKS DE COMENTÁRIO E QUESITO
# =============================================================================
def cb_postar_comentario(qid, ano_sel, usuario_atual, texto_comentario, container_ref, id_chave=None):
    texto = texto_comentario.strip()
    if texto:
        dados_banco = load_respostas(ano_sel).get(qid, {})
        comentarios = _obter_lista_comentarios(dados_banco)
        
        status_atual = "Pendente"
        for com in reversed(comentarios):
            if isinstance(com, dict) and "status_definido" in com:
                status_atual = com["status_definido"]
                break
                
        nova_mensagem = {
            "autor": usuario_atual,
            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "texto": texto,
            "status_definido": status_atual
        }
        comentarios.append(nova_mensagem)
        
        save_resp(
            qid=qid,
            valor=dados_banco.get("valor", ""),
            pontos=dados_banco.get("pontos", 0),
            link=dados_banco.get("link", ""),
            comentarios=comentarios,
            ano=ano_sel
        )
        ui.notify("Comentário publicado!", type="positive")
        container_ref.refresh()

def cb_alterar_status(qid, ano_sel, usuario_atual, novo_status, container_ref):
    if not novo_status:
        return

    dados_banco = load_respostas(ano_sel).get(qid, {})
    comentarios = _obter_lista_comentarios(dados_banco)
    
    log_mudanca = {
        "autor": "Sistema / " + usuario_atual,
        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "texto": f"ℹ️ Alterou o status do quesito para: **{novo_status.upper()}**.",
        "status_definido": novo_status
    }
    comentarios.append(log_mudanca)
    
    save_resp(
        qid=qid,
        valor=dados_banco.get("valor", ""),
        pontos=dados_banco.get("pontos", 0),
        link=dados_banco.get("link", ""),
        comentarios=comentarios,
        ano=ano_sel
    )
    ui.notify(f"Status alterado para {novo_status}", type="info")
    container_ref.refresh()

def cb_deletar_comentario(qid, ano_sel, idx, container_ref):
    dados_banco = load_respostas(ano_sel).get(qid, {})
    comentarios = _obter_lista_comentarios(dados_banco)
    
    if 0 <= idx < len(comentarios):
        comentarios.pop(idx)
        save_resp(
            qid=qid,
            valor=dados_banco.get("valor", ""),
            pontos=dados_banco.get("pontos", 0),
            link=dados_banco.get("link", ""),
            comentarios=comentarios,
            ano=ano_sel
        )
        ui.notify("Comentário removido.", type="warning")
        container_ref.refresh()

def cb_salvar_questao(qid, ano_sel, usuario_atual, novo_valor, novo_link, novos_pontos, texto_pendente, container_ref):
    dados_banco = load_respostas(ano_sel).get(qid, {})
    comentarios = _obter_lista_comentarios(dados_banco)
    
    if texto_pendente and texto_pendente.strip():
        status_atual = "Pendente"
        for com in reversed(comentarios):
            if isinstance(com, dict) and "status_definido" in com:
                status_atual = com["status_definido"]
                break
                
        comentarios.append({
            "autor": usuario_atual,
            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "texto": texto_pendente.strip(),
            "status_definido": status_atual
        })
        
    save_resp(
        qid=qid,
        valor=novo_valor,
        pontos=novos_pontos,
        link=novo_link,
        comentarios=comentarios,
        ano=ano_sel
    )
    ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive")
    container_ref.refresh()

# =============================================================================
# AUXILIARES E COMPONENTES
# =============================================================================
def get_image_base64(filename):
    full_path = os.path.join(current_dir, filename)
    if os.path.exists(full_path):
        with open(full_path, "rb") as img_file:
            return f"data:image/png;base64,{base64.b64encode(img_file.read()).decode()}"
    return None

def render_rodape():
    ui.html(
        """
        <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 12px; font-family: sans-serif; line-height: 1.5; margin-top: 20px;">
            ⚙️ <b>Desenvolvido por:</b><br>
            <span style="font-size: 13px;">Jefferson Espanha</span><br>
            <span>Procuradoria do Município</span><br>
            <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
        </div>
        """
    )

AVAILABLE_YEARS = [2024, 2025, 2026, 2027, 2028, 2029, 2030]

DIMENSIONS_DATA = {
    "i-Gov TI": {"img": "i_gov_ti.png", "desc": "Governança de Tecnologia da Informação."},
    "i-Educ": {"img": "i_educ.png", "desc": "Gestão da Educação Municipal"},
    "i-Saúde": {"img": "i_saude.png", "desc": "Gestão da Saúde municipal."},
    "i-Plan": {"img": "i_plan.png", "desc": "Eficiência do planejamento orçamentário."},
    "i-Amb": {"img": "i_amb.png", "desc": "Políticas de meio ambiente e sustentabilidade."},
    "i-Cidade": {"img": "i_cidade.png", "desc": "Defesa Civil e infraestrutura urbana."},
    "i-Fiscal": {"img": "i_fiscal.png", "desc": "Gestão fiscal e execução financeira."},
    "ieg-m": {"img": "i_iegmfinal.png", "desc": "Dashboard"},
    "Relatório de Atividades": {"img": "relatorio_atividade.png", "desc": "Monitoramento do PPA"},
    "Plano de Ação": {"img": "plano_acao.png", "desc": "Plano de Ação Corretiva e Metas Estratégicas"},
    "Biblioteca": {"img": "biblioteca.png", "desc": "Biblioteca Digital"},
    "Consulta Rápida": {"img": "hal9000.png", "desc": "Consulte quesitos"},
}

ADMIN_DATA = {
    "Administrador": {
        "img": "administrador.png",
        "desc": "Painel de Administração e Configurações do Sistema.",
    }
}

# =============================================================================
# COMPONENTES DE INTERFACE DE QUESITO E COMENTÁRIOS
# =============================================================================
@ui.refreshable
def bloco_comentarios_ui(questao_id, res_data, sufixo=None):
    ano_sel = app.storage.user.get("ano_referencia_global", date.today().year)
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")
    id_chave = f"{questao_id}_{sufixo}" if sufixo else questao_id
    
    dados_questao = res_data.get(questao_id, {})
    historico = _obter_lista_comentarios(dados_questao)
    
    status_global = "Pendente"
    for com in reversed(historico):
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            break
            
    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    
    with ui.expansion(f"💬 Diálogo Interno {id_chave} | Status: {badge_status}", value=(status_global == "Pendente")).classes('w-full border rounded p-2'):
        opcoes_status = ["Resolvido", "Pendente"]
        ui.radio(
            opcoes_status, 
            value=status_global, 
            on_change=lambda e: cb_alterar_status(questao_id, ano_sel, usuario_atual, e.value, bloco_comentarios_ui)
        ).props('inline')

        if historico:
            for idx, com in enumerate(historico):
                if isinstance(com, str):
                    com = {"autor": "Usuário", "data": "", "texto": com}

                autor = com.get('autor', 'Anônimo')
                data_com = com.get('data', '')
                texto_com = com.get('texto', '')

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
                            f"""<div style="background-color: #f8f9fa; padding: 10px 15px; border-radius: 8px; border-left: 3px solid #1e88e5; width: 100%;">
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>"""
                        ).classes('w-full')
                    
                    ui.button('🗑️', on_click=lambda idx=idx: cb_deletar_comentario(questao_id, ano_sel, idx, bloco_comentarios_ui)).props('flat dense')

        txt_coment = ui.textarea(placeholder="Novo comentário...").classes('w-full')
        ui.button(
            "Postar Comentário", 
            on_click=lambda: cb_postar_comentario(questao_id, ano_sel, usuario_atual, txt_coment.value, bloco_comentarios_ui, id_chave)
        ).classes('bg-blue-600 text-white mt-2')

@ui.refreshable
def renderizar_questao_ui(qid, res_data):
    dados_q = res_data.get(qid, {})
    ano_sel = app.storage.user.get("ano_referencia_global", date.today().year)
    usuario_atual = app.storage.user.get("username", "Usuário Anônimo")
    
    val_existente = dados_q.get("valor", "")
    pts_existente = float(dados_q.get("pontos", 0.0))
    link_existente = dados_q.get("link", "")
    
    with ui.card().classes('w-full p-4 border'):
        ui.label(f"Quesito: {qid}").classes('text-lg font-bold')
        
        with ui.row().classes('w-full grid grid-cols-1 md:grid-cols-4 gap-4'):
            with ui.column().classes('col-span-3 w-full'):
                inp_val = ui.textarea('Resposta / Evidência:', value=val_existente).classes('w-full')
                inp_link = ui.input('Link da Evidência (opcional):', value=link_existente).classes('w-full')
            
            with ui.column().classes('col-span-1 w-full'):
                inp_pts = ui.number('Pontuação:', value=pts_existente, format='%.2f').classes('w-full')
                inp_coment = ui.textarea('Comentário Inicial').classes('w-full')
                ui.button(
                    f"💾 Salvar Quesito {qid}", 
                    on_click=lambda: cb_salvar_questao(
                        qid, ano_sel, usuario_atual, inp_val.value, inp_link.value, inp_pts.value, inp_coment.value, renderizar_questao_ui
                    )
                ).classes('w-full bg-blue-700 text-white mt-2')

        bloco_comentarios_ui(qid, res_data)

# =============================================================================
# PÁGINAS DO SISTEMA
# =============================================================================

@ui.page('/')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/dashboard')
        return

    ui.query('.q-page').classes('bg-white')
    
    with ui.column().classes('absolute-center w-full max-w-md p-4 items-center'):
        logo_b64 = get_image_base64("iegm.png")
        if logo_b64:
            ui.html(f'<div style="text-align:center; margin-bottom:20px;"><img src="{logo_b64}" style="max-width:100%; height:auto;"></div>').classes('w-full')

        ui.html('<div style="background: #001A4D; border: 2px solid #001A4D; border-radius: 4px; padding: 12px 20px; text-align: center; color: #FFFFFF; font-size: 16px; font-weight: bold; width: 100%;">Sistema de Preenchimento do IEG-M</div>').classes('w-full mb-4')
        
        username_input = ui.input('👤 Usuário', placeholder='jefferson.espanha').classes('w-full')
        password_input = ui.input('🔐 Senha', password=True, placeholder='••••••••').classes('w-full mb-4')

        def tentar_login():
            u_val = username_input.value.strip().lower() if username_input.value else ""
            p_val = password_input.value.strip() if password_input.value else ""

            if not u_val or not p_val:
                ui.notify("⚠️ Preencha todos os campos!", type="warning")
                return

            # 1. ACESSO MESTRE / EMERGÊNCIA
            if u_val == "jefferson.espanha" and p_val == "fodasse":
                app.storage.user['authenticated'] = True
                app.storage.user['username'] = "jefferson.espanha"
                app.storage.user['role'] = "admin"
                app.storage.user['needs_password_change'] = True
                ui.navigate.to('/dashboard')
                return

            # 2. CONSULTA USUÁRIOS CRIADOS PELO ADMINISTRADOR.PY (SESSION OU JSON)
            lista_usuarios = app.storage.user.get("usuarios", [])
            if not lista_usuarios and os.path.exists("usuarios.json"):
                try:
                    with open("usuarios.json", "r", encoding="utf-8") as f:
                        lista_usuarios = json.load(f).get("usuarios", [])
                except Exception:
                    pass

            user_found = next((u for u in lista_usuarios if str(u.get("usuario", "")).strip().lower() == u_val), None)

            if user_found and str(user_found.get("senha", "")).strip() == p_val:
                app.storage.user['authenticated'] = True
                app.storage.user['username'] = user_found.get("usuario")
                app.storage.user['role'] = "admin" if user_found.get("perfil") == "Administrador" else "user"
                app.storage.user['needs_password_change'] = user_found.get("primeiro_acesso", True)
                ui.navigate.to('/dashboard')
            else:
                ui.notify("❌ Usuário ou senha incorretos.", type="negative")

        ui.button('🔓 ENTRAR NO SISTEMA', on_click=tentar_login).classes('w-full bg-blue-900 text-white font-bold py-2')
        render_rodape()

@ui.page('/dashboard')
def dashboard_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    username = app.storage.user.get('username', 'Usuário')

    with ui.column().classes('w-full max-w-7xl mx-auto p-6 gap-6'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.html(f'<div style="text-align: center;"><h1 style="color: #001A4D; font-size: 28px; font-weight: bold; margin:0;">IEG-M Francisco Morato</h1><p style="color: #003D99; font-weight: bold; margin:0;">Bem-vindo, {username}!</p></div>')
            ui.button('🚪 Sair', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/'))).classes('bg-red-700 text-white')

        # 1. SISTEMA DE GESTÃO AVANÇADA
        ui.label('📊 Sistema de Preenchimento').classes('text-xl font-bold text-gray-800 border-b w-full pb-2')
        
        with ui.grid(columns=4).classes('w-full gap-4'):
            for dim_name, dim_info in DIMENSIONS_DATA.items():
                img_b64 = get_image_base64(dim_info["img"])
                img_html = f'<img src="{img_b64}" style="max-height:85px; max-width:100%; object-fit:contain;" />' if img_b64 else '<div style="font-size:42px;">📊</div>'
                
                with ui.card().classes('flex flex-col items-center text-center p-4 cursor-pointer hover:shadow-lg transition-all border rounded-lg h-64 justify-between'):
                    ui.html(img_html)
                    ui.label(dim_name).classes('text-blue-900 font-bold text-base')
                    ui.label(dim_info["desc"]).classes('text-gray-500 text-xs')
                    
                    def abrir_dimensao(nome=dim_name):
                        app.storage.user['selected_dimension'] = nome
                        ui.navigate.to('/dimension')

                    ui.button('Acessar', on_click=abrir_dimensao).classes('w-full bg-blue-800 text-white')

        # 2. GESTÃO E ADMINISTRAÇÃO
        ui.label('⚙️ Gestão e Administração').classes('text-xl font-bold text-gray-800 border-b w-full pb-2 mt-6')
        
        with ui.grid(columns=4).classes('w-full gap-4'):
            for admin_name, admin_info in ADMIN_DATA.items():
                img_b64 = get_image_base64(admin_info["img"])
                img_html = f'<img src="{img_b64}" style="max-height:85px; max-width:100%; object-fit:contain;" />' if img_b64 else '<div style="font-size:42px;">⚙️</div>'
                
                with ui.card().classes('flex flex-col items-center text-center p-4 cursor-pointer hover:shadow-lg transition-all border-b-4 border-red-700 rounded-lg h-64 justify-between'):
                    ui.html(img_html)
                    ui.label(admin_name).classes('text-blue-900 font-bold text-base')
                    ui.label(admin_info["desc"]).classes('text-gray-500 text-xs')
                    
                    def abrir_admin(nome=admin_name):
                        app.storage.user['selected_dimension'] = nome
                        ui.navigate.to('/dimension')

                    ui.button('Acessar', on_click=abrir_admin).classes('w-full bg-red-700 text-white')

@ui.page('/dimension')
def dimension_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    dimension = app.storage.user.get('selected_dimension', 'Módulo')
    year = app.storage.user.get('ano_referencia_global', 2026)

    with ui.column().classes('w-full max-w-7xl mx-auto p-6 gap-6'):
        with ui.row().classes('w-full justify-between items-center border-b pb-4'):
            ui.button('⬅️ Voltar', on_click=lambda: ui.navigate.to('/dashboard')).classes('bg-gray-600 text-white')
            ui.html(f'<h2 style="color: #001A4D; font-weight: bold; font-size: 24px; margin: 0;">{dimension} - {year}</h2>')
            ui.button('🚪 Sair', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/'))).classes('bg-red-700 text-white')

        # ROTEAMENTO CENTRAL DAS SUBPÁGINAS DO ECOSSISTEMA
        if dimension == "Administrador":
            if admin_core and hasattr(admin_core, "mostrar_painel_admin"):
                admin_core.mostrar_painel_admin(year)
            else:
                ui.label("Erro técnico: O arquivo 'administrador.py' não foi detectado no sistema ou não possui 'mostrar_painel_admin'.").classes('text-red-600 font-bold')

        elif dimension == "Biblioteca":
            ui.label("📚 Biblioteca de Documentos").classes('text-xl font-bold')
            ui.label("Acesse o acervo documental completo e referências diretamente no Google Drive.")
            ui.link("🔗 Acessar Biblioteca no Google Drive", "https://drive.google.com/drive/folders/1iwiuHHbQYZ-p6aEMB9oSjugDEvdB8GVK?usp=drive_link", new_tab=True).classes('bg-blue-600 text-white p-3 rounded text-center w-full block')

        elif dimension in ["Consulta Rápida", "HAL 9000"]:
            ui.label("🔴 Consulta Rápida").classes('text-xl font-bold')
            if hal_core:
                if hasattr(hal_core, "mostrar_chat_hal"):
                    hal_core.mostrar_chat_hal()
                elif hasattr(hal_core, "main"):
                    hal_core.main()
                else:
                    ui.label("Módulo 'hal.py' carregado, mas nenhuma função de renderização conhecida foi encontrada.").classes('text-yellow-600')
                    ui.input(placeholder="Como posso ajudar hoje? (Modo de Segurança)").classes('w-full')
            else:
                ui.label("Erro técnico: O arquivo 'hal.py' não foi detectado no sistema.").classes('text-red-600')
                ui.input(placeholder="Como posso ajudar hoje? (Modo Offline)").classes('w-full')

        elif dimension == "i-Cidade":
            if icidade is None:
                ui.label("❌ O arquivo 'icidade.py' não foi encontrado ou falhou ao ser importado.").classes('text-red-600 font-bold')
            else:
                try:
                    if hasattr(icidade, "init_db"):
                        icidade.init_db()

                    funcao_encontrada = None
                    for nome_fn in ["mostrar_formulario_cidade", "mostrar_formulario_icidade", "mostrar_icidade", "main_page", "run", "main", "app"]:
                        if hasattr(icidade, nome_fn):
                            funcao_encontrada = getattr(icidade, nome_fn)
                            break

                    if funcao_encontrada:
                        funcao_encontrada()
                    else:
                        funcoes_disponiveis = [f for f in dir(icidade) if not f.startswith("_") and callable(getattr(icidade, f))]
                        ui.label(f"⚠️ Nenhuma função padrão foi encontrada. Funções detectadas no arquivo: {funcoes_disponiveis}").classes('text-yellow-600')
                except Exception as e:
                    ui.label(f"❌ Erro ao executar o i-Cidade: {e}").classes('text-red-600')

        elif dimension == "i-Gov TI" and igov:
            if hasattr(igov, "mostrar_formulario_igov"):
                igov.mostrar_formulario_igov()
        elif dimension == "i-Amb" and iamb:
            if hasattr(iamb, "mostrar_formulario_iamb"):
                iamb.mostrar_formulario_iamb()
        elif dimension == "i-Fiscal" and ifiscal:
            if hasattr(ifiscal, "mostrar_formulario_ifiscal"):
                ifiscal.mostrar_formulario_ifiscal()
        elif dimension == "i-Plan" and iplan:
            if hasattr(iplan, "mostrar_formulario_plan"):
                iplan.mostrar_formulario_plan()
        elif dimension == "i-Educ" and ieduc:
            if hasattr(ieduc, "mostrar_formulario_educ"):
                ieduc.mostrar_formulario_educ()
        elif dimension == "i-Saúde" and isaude:
            if hasattr(isaude, "mostrar_formulario_saude"):
                isaude.mostrar_formulario_saude()
        elif dimension == "ieg-m":
            if iegm_final and hasattr(iegm_final, "mostrar_painel_iegm_final"):
                iegm_final.mostrar_painel_iegm_final(year)
            else:
                ui.label("Erro: Módulo 'iegmfinal.py' não localizado.").classes('text-red-600')

        elif dimension == "Relatório de Atividades":
            if atividade:
                if hasattr(atividade, "mostrar_formulario_atividade"):
                    atividade.mostrar_formulario_atividade()
                else:
                    ui.label("Módulo 'atividade.py' carregado, mas a função 'mostrar_formulario_atividade' não foi encontrada.").classes('text-yellow-600')
            else:
                ui.label("Erro: Módulo 'atividade.py' não localizado.").classes('text-red-600')

        elif dimension == "Plano de Ação":
            if plano_acao:
                if hasattr(plano_acao, "mostrar_formulario_plano_acao"):
                    plano_acao.mostrar_formulario_plano_acao()
                elif hasattr(plano_acao, "mostrar_painel_plano_acao"):
                    plano_acao.mostrar_painel_plano_acao()
                else:
                    ui.label("Módulo carregado, mas a função de renderização padrão não foi encontrada.").classes('text-yellow-600')
            else:
                ui.label("Erro: Módulo 'plano_acao.py' não localizado.").classes('text-red-600')

# =============================================================================
# INICIALIZAÇÃO DE SERVIDOR NICEGUI
# =============================================================================
port = int(os.environ.get("PORT", 8080))
storage_secret = os.environ.get("STORAGE_SECRET", "secret_key_iegm_morato_2026")

ui.run(
    host='0.0.0.0',
    port=port,
    title="IEG-M Francisco Morato",
    storage_secret=storage_secret,
    reload=False
)
