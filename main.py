import base64
from datetime import datetime, date
import json
import os
import sys
from nicegui import app, ui

# =============================================================================
# CONFIGURAÇÕES DE AMBIENTE E BANCO
# =============================================================================
DEFAULT_NEON_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"
)

current_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in locals() else os.getcwd()
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Estado global da aplicação para armazenamento em memória
db_respostas = {}

def load_respostas(ano):
    return db_respostas.get(ano, {})

def save_resp(qid, valor, pontos, link, comentarios, ano=2026):
    if ano not in db_respostas:
        db_respostas[ano] = {}
    db_respostas[ano][qid] = {
        "valor": valor,
        "pontos": pontos,
        "link": link,
        "comentarios": comentarios,
    }

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

def get_image_base64(filename):
    full_path = os.path.join(current_dir, filename)
    if os.path.exists(full_path):
        with open(full_path, "rb") as img_file:
            return f"data:image/png;base64,{base64.b64encode(img_file.read()).decode()}"
    return None

# =============================================================================
# COMPONENTES DA INTERFACE (NICEGUI)
# =============================================================================
def render_rodape():
    with ui.footer().classes("bg-transparent text-gray-800 justify-center p-4"):
        ui.html(
            """
            <div style="text-align: center; font-weight: bold; font-style: italic; font-size: 12px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 13px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
            """
        )

@ui.page('/dashboard')
def dashboard_page():
    # Verifica autenticação na sessão do NiceGUI
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    ui.colors(primary='#1a56db')
    
    # Header / Toolbar
    with ui.header().classes('justify-between items-center bg-blue-900 text-white p-4'):
        ui.label('📊 IEG-M Francisco Morato').classes('text-xl font-bold')
        with ui.row().classes('items-center gap-4'):
            ui.label(f"👤 {app.storage.user.get('username', 'Usuário')}")
            ui.button('Sair', icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/'))).props('flat color=white')

    # Conteúdo Principal
    with ui.column().classes('w-full max-w-5xl mx-auto p-6 gap-6'):
        ui.label('Painel Principal de Gestão IEG-M').classes('text-2xl font-bold text-gray-800')
        
        with ui.card().classes('w-full p-4'):
            ui.label('Quesitos e Avaliação').classes('text-lg font-semibold mb-2')
            
            val_input = ui.input('Valor / Resposta').classes('w-full')
            link_input = ui.input('Link do Comprovante').classes('w-full')
            pts_input = ui.number('Pontos', value=0.0, format='%.2f').classes('w-full')
            
            coment_input = ui.textarea('Novo Comentário').classes('w-full')

            def salvar():
                qid = "Q_EXEMPLO_1"
                comentarios = [{
                    "autor": app.storage.user.get('username'),
                    "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "texto": coment_input.value,
                    "status_definido": "Pendente"
                }] if coment_input.value else []

                save_resp(
                    qid=qid,
                    valor=val_input.value,
                    pontos=pts_input.value,
                    link=link_input.value,
                    comentarios=comentarios
                )
                ui.notify('Dados salvos com sucesso!', type='positive')
                coment_input.value = ''

            ui.button('Salvar Quesito', icon='save', on_click=salvar).classes('bg-blue-600 text-white mt-2')

    render_rodape()

@ui.page('/')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/dashboard')
        return

    with ui.column().classes('absolute-center w-full max-w-md p-4 items-center'):
        logo_b64 = get_image_base64("iegm.png")
        if logo_b64:
            ui.image(logo_b64).classes('w-48 mb-4')

        with ui.card().classes('w-full p-6 shadow-lg rounded-lg'):
            ui.html('<h3 style="text-align: center; color: #1a56db; font-weight: bold; margin-bottom: 16px;">Sistema de Preenchimento do IEG-M</h3>')
            
            username = ui.input('👤 Usuário').classes('w-full mb-2').props('autofocus')
            password = ui.input('🔐 Senha', password=True, password_toggle_button=True).classes('w-full mb-4')

            def autenticar():
                u_val = username.value.strip().lower() if username.value else ""
                p_val = password.value.strip() if password.value else ""

                if not u_val or not p_val:
                    ui.notify('⚠️ Preencha todos os campos!', type='warning')
                elif u_val == "jefferson.espanha" and p_val == "fodasse":
                    app.storage.user['authenticated'] = True
                    app.storage.user['username'] = u_val
                    app.storage.user['role'] = 'admin'
                    ui.navigate.to('/dashboard')
                else:
                    ui.notify('❌ Usuário ou senha incorretos.', type='negative')

            ui.button('🔓 ENTRAR NO SISTEMA', on_click=autenticar).classes('w-full bg-blue-700 text-white font-bold py-2')

    render_rodape()

# =============================================================================
# EXECUÇÃO DO SERVIDOR (COMPATÍVEL COM RENDER E PORTA DINÂMICA)
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
