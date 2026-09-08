import base64
from datetime import datetime, date
from administrador import carregar_dados_json
import json
import os
import sys
import pandas as pd
import streamlit as st

# =============================================================================
# INJEÇÃO DEFINITIVA E BLINDADA NO ST.SECRETS (RENDER / NEON)
# =============================================================================
NEON_URL = "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"

SecretsClass = type(st.secrets)

_orig_getitem = getattr(SecretsClass, "__getitem__", None)
_orig_getattr = getattr(SecretsClass, "__getattr__", None)


def _patched_getitem(self, key):
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key == "DATABASE_URL":
        return os.environ.get("DATABASE_URL", NEON_URL)
    if _orig_getitem:
        try:
            return _orig_getitem(self, key)
        except Exception:
            pass
    raise KeyError(f"st.secrets has no key '{key}'")


def _patched_getattr(self, key):
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key == "DATABASE_URL":
        return os.environ.get("DATABASE_URL", NEON_URL)
    if _orig_getattr:
        try:
            return _orig_getattr(self, key)
        except Exception:
            pass
    raise AttributeError(f"st.secrets has no attribute '{key}'")


SecretsClass.__getitem__ = _patched_getitem
SecretsClass.__getattr__ = _patched_getattr
# =============================================================================

# Força o interpretador a enxergar a pasta atual
current_dir = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in locals()
    else os.getcwd()
)
if current_dir not in sys.path:
    sys.path.append(current_dir)


# --- CARREGAMENTO OTIMIZADO DE MÓDULOS ---
def import_local_module(module_name):
    try:
        import importlib

        return importlib.import_module(module_name)
    except Exception:
        return None


# Importação de Módulos IEG-M
icidade = import_local_module("icidade_completo") or import_local_module(
    "icidade"
)
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

# Configuração da página
st.set_page_config(
    page_title="IEG-M Francisco Morato",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# HELPER DE SANITIZAÇÃO DE COMENTÁRIOS (CORRIGE O EMPTY_STRING)
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
# CALLBACKS DE COMENTÁRIO E QUESITO (CORRIGIDOS)
# =============================================================================

def cb_postar_comentario(qid, ano_sel, usuario_atual, id_chave=None):
    """Callback disparado ao clicar em 'Postar Comentário'."""
    chave_busca = id_chave if id_chave else qid
    key_texto = f"v_txt_com_{chave_busca}_{ano_sel}"
    texto = st.session_state.get(key_texto, "").strip()
    
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
            comentarios=comentarios
        )
        st.session_state[key_texto] = ""
        if hasattr(load_respostas, "clear"):
            load_respostas.clear()


def cb_alterar_status(qid, ano_sel, usuario_atual, id_chave=None):
    """Callback disparado ao trocar o Radio Button de Pendente/Resolvido."""
    chave_busca = id_chave if id_chave else qid
    key_radio = f"rad_status_{chave_busca}_{ano_sel}"
    novo_status = st.session_state.get(key_radio)
    
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
        comentarios=comentarios
    )
    if hasattr(load_respostas, "clear"):
        load_respostas.clear()


def cb_deletar_comentario(qid, ano_sel, idx):
    """Callback para apagar um comentário específico."""
    dados_banco = load_respostas(ano_sel).get(qid, {})
    comentarios = _obter_lista_comentarios(dados_banco)
    
    if 0 <= idx < len(comentarios):
        comentarios.pop(idx)
        save_resp(
            qid=qid,
            valor=dados_banco.get("valor", ""),
            pontos=dados_banco.get("pontos", 0),
            link=dados_banco.get("link", ""),
            comentarios=comentarios
        )
        if hasattr(load_respostas, "clear"):
            load_respostas.clear()


def cb_salvar_questao(qid, ano_sel, usuario_atual):
    """Callback acionado ao clicar em 'Salvar Quesito'."""
    key_val = f"txt_val_{qid}"
    key_link = f"txt_link_{qid}"
    key_pts = f"num_pts_{qid}"
    key_texto = f"v_txt_com_{qid}_{ano_sel}"
    
    novo_valor = st.session_state.get(key_val, "")
    novo_link = st.session_state.get(key_link, "")
    novos_pontos = st.session_state.get(key_pts, 0.0)
    
    dados_banco = load_respostas(ano_sel).get(qid, {})
    comentarios = _obter_lista_comentarios(dados_banco)
    texto_pendente = st.session_state.get(key_texto, "").strip()
    
    if texto_pendente:
        status_atual = "Pendente"
        for com in reversed(comentarios):
            if isinstance(com, dict) and "status_definido" in com:
                status_atual = com["status_definido"]
                break
                
        comentarios.append({
            "autor": usuario_atual,
            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "texto": texto_pendente,
            "status_definido": status_atual
        })
        st.session_state[key_texto] = ""
        
    save_resp(
        qid=qid,
        valor=novo_valor,
        pontos=novos_pontos,
        link=novo_link,
        comentarios=comentarios
    )
    if hasattr(load_respostas, "clear"):
        load_respostas.clear()


# =============================================================================
# COMPONENTES DE INTERFACE DE RENDERIZAÇÃO
# =============================================================================

def renderizar_questao(qid, res_data):
    """Renderiza a questão."""
    dados_q = res_data.get(qid, {})
    ano_sel = st.session_state.get("ano_referencia_global", date.today().year)
    usuario_atual = st.session_state.get("username", st.session_state.get("usuario", "Usuário Anônimo"))
    
    val_existente = dados_q.get("valor", "")
    pts_existente = float(dados_q.get("pontos", 0.0))
    link_existente = dados_q.get("link", "")
    
    with st.container(border=True):
        st.markdown(f"#### Quesito: `{qid}`")
        
        col_txt, col_meta = st.columns([3, 1])
        
        with col_txt:
            st.text_area("Resposta / Evidência:", value=val_existente, key=f"txt_val_{qid}", height=100)
            st.text_input("Link da Evidência (opcional):", value=link_existente, key=f"txt_link_{qid}")

        with col_meta:
            st.number_input("Pontuação:", value=pts_existente, key=f"num_pts_{qid}")
            st.markdown("<br>", unsafe_allow_html=True)
            
            st.button(
                f"💾 Salvar Quesito {qid}", 
                key=f"btn_save_{qid}", 
                type="primary", 
                use_container_width=True,
                on_click=cb_salvar_questao,
                args=(qid, ano_sel, usuario_atual)
            )

        bloco_comentarios(qid, res_data)


def bloco_comentarios(questao_id, res_data, sufixo=None):
    """Renderiza a caixa de comentários e histórico formatado."""
    ano_sel = st.session_state.get("ano_referencia_global", date.today().year)
    usuario_atual = st.session_state.get("username", st.session_state.get("usuario", "Usuário Anônimo"))
    
    id_chave = f"{questao_id}_{sufixo}" if sufixo else questao_id
    key_texto = f"v_txt_com_{id_chave}_{ano_sel}"
    key_radio = f"rad_status_{id_chave}_{ano_sel}"
    
    dados_questao = res_data.get(questao_id, {})
    historico = _obter_lista_comentarios(dados_questao)
    
    status_global = "Pendente"
    for com in reversed(historico):
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            break
            
    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    
    with st.expander(f"💬 Diálogo Interno {id_chave} | Status: {badge_status}", expanded=(status_global == "Pendente")):
        opcoes_status = ["Resolvido", "Pendente"]
        idx_status_atual = opcoes_status.index(status_global) if status_global in opcoes_status else 1
        
        st.radio(
            f"Definir status para {id_chave}:",
            options=opcoes_status,
            index=idx_status_atual,
            horizontal=True,
            key=key_radio,
            on_change=cb_alterar_status,
            args=(questao_id, ano_sel, usuario_atual, id_chave)
        )

        if historico:
            for idx, com in enumerate(historico):
                if isinstance(com, str):
                    com = {"autor": "Usuário", "data": "", "texto": com}

                col_balao, col_lixeira = st.columns([11, 1])
                
                with col_balao:
                    autor = com.get('autor', 'Anônimo')
                    data_com = com.get('data', '')
                    texto_com = com.get('texto', '')
                    
                    if "Sistema /" in autor:
                        st.markdown(
                            f"""<div style="background-color: #f1f3f5; padding: 6px 12px; border-radius: 6px; margin-bottom: 4px; border-left: 3px solid #ced4da;">
                                <span style="font-size: 11px; color: #6c757d; font-style: italic;">{autor} - {data_com}</span>
                                <p style="margin: 2px 0 0 0; font-size: 12px; color: #495057;">{texto_com}</p>
                            </div>""", unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            f"""<div style="background-color: #f8f9fa; padding: 10px 15px; border-radius: 8px; margin-bottom: 6px; border-left: 3px solid #1e88e5;">
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">👤 {autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>""", unsafe_allow_html=True
                        )
                
                with col_lixeira:
                    st.button(
                        "🗑️", 
                        key=f"btn_del_com_{id_chave}_{idx}_{ano_sel}",
                        on_click=cb_deletar_comentario,
                        args=(questao_id, ano_sel, idx)
                    )
        
        st.text_area("Novo comentário:", key=key_texto, height=70, label_visibility="collapsed")
        
        st.button(
            "Postar Comentário", 
            key=f"btn_com_{id_chave}_{ano_sel}", 
            type="primary",
            on_click=cb_postar_comentario,
            args=(questao_id, ano_sel, usuario_atual, id_chave)
        )


# =============================================================================
# FUNÇÃO AUXILIAR DE IMAGEM
# =============================================================================
def get_image_base64(filename):
    full_path = os.path.join(current_dir, filename)
    if os.path.exists(full_path):
        with open(full_path, "rb") as img_file:
            return f"data:image/png;base64,{base64.b64encode(img_file.read()).decode()}"
    return None


# Estilização CSS
st.markdown(
    """
    <style>
    .stApp {
        background-color: #FFFFFF !important;
        color: #333333;
    }
    
    .cad-frame {
        border: 2px solid #001A4D;
        border-radius: 4px;
        padding: 12px 20px;
        background: #001A4D;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        margin: 0 auto 20px auto;
        max-width: 320px;
    }

    .card-container {
        position: relative;
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 16px;
        padding: 20px;
        text-align: center;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.03);
        transition: all 0.3s ease;
        min-height: 250px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        cursor: pointer;
    }
    
    .card-container:hover {
        transform: translateY(-5px);
        box-shadow: 0 12px 22px rgba(0, 26, 77, 0.1);
        border-color: #003D99;
    }

    .card-img-container {
        height: 90px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 12px;
        pointer-events: none;
    }

    .card-img-container img {
        max-height: 85px;
        max-width: 100%;
        object-fit: contain;
    }

    .card-title {
        color: #001A4D;
        font-size: 16px;
        font-weight: 700;
        margin-bottom: 6px;
        pointer-events: none;
    }

    .card-text {
        color: #64748B;
        font-size: 12px;
        line-height: 1.4;
        pointer-events: none;
    }

    .hidden-btn-container {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        opacity: 0;
        z-index: 99;
    }
    
    .hidden-btn-container div.stButton > button {
        width: 100% !important;
        height: 250px !important;
        background: transparent !important;
        border: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Inicialização de Estado Global
if "users_db" not in st.session_state:
    st.session_state.users_db = {
        "jefferson.espanha": {
            "senha": "fodasse",
            "email": "jefferson@franciscomorato.sp.gov.br",
        }
    }

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.username = None
    st.session_state.role = None
    st.session_state.current_page = "login"
    st.session_state.selected_dimension = None
    st.session_state.ano_referencia_global = 2026
    st.session_state.needs_password_change = False

AVAILABLE_YEARS = [2024, 2025, 2026, 2027, 2028, 2029, 2030]

DIMENSIONS_DATA = {
    "i-Gov TI": {
        "img": "i_gov_ti.png",
        "desc": "Governança de Tecnologia da Informação.",
    },
    "i-Educ": {"img": "i_educ.png", "desc": "Gestão da Educação Municipal"},
    "i-Saúde": {"img": "i_saude.png", "desc": "Gestão da Saúde municipal."},
    "i-Plan": {
        "img": "i_plan.png",
        "desc": "Eficiência do planejamento orçamentário.",
    },
    "i-Amb": {
        "img": "i_amb.png",
        "desc": "Políticas de meio ambiente e sustentabilidade.",
    },
    "i-Cidade": {
        "img": "i_cidade.png",
        "desc": "Defesa Civil e infraestrutura urbana.",
    },
    "i-Fiscal": {
        "img": "i_fiscal.png",
        "desc": "Gestão fiscal e execução financeira.",
    },
    "ieg-m": {
        "img": "i_iegmfinal.png",
        "desc": "Dashboard",
    },
    "Relatório de Atividades": {
        "img": "relatorio_atividade.png",
        "desc": "Monitoramento do PPA",
    },
    "Plano de Ação": {
        "img": "plano_acao.png",
        "desc": "Plano de Ação Corretiva e Metas Estratégicas",
    },
    "Biblioteca": {
        "img": "biblioteca.png",
        "desc": "Biblioteca Digital",
    },
    "Consulta Rápida": {  # Título atualizado
        "img": "hal9000.png",
        "desc": "Consulte quesitos",  # Subtexto atualizado
    },
}


ADMIN_DATA = {
    "Administrador": {
        "img": "administrador.png",
        "desc": "Painel de Administração e Configurações do Sistema.",
    }
}


def login_page():
    col1, col2, col3 = st.columns([1.1, 1.6, 1.1])
    with col2:
        logo_b64 = (
            get_image_base64("iegm.png")
            if "get_image_base64" in globals()
            else None
        )
        if logo_b64:
            st.markdown(
                f'<div style="text-align:center; margin-bottom:20px;"><img src="{logo_b64}" style="max-width:100%; height:auto;"></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='padding: 20px;'></div>", unsafe_allow_html=True
            )

        st.markdown(
            '<div class="cad-frame"><h3 style="text-align: center; color: #FFFFFF; font-size: 16px; margin: 0;">Sistema de Preenchimento do IEG-M</h3></div>',
            unsafe_allow_html=True,
        )
        
        username_input = st.text_input(
            "👤 Usuário",
            placeholder="jefferson.espanha",
            key="login_username",
        ).strip().lower()
        
        password_input = st.text_input(
            "🔐 Senha", type="password", placeholder="••••••••", key="login_password"
        ).strip()

        if st.button(
            "🔓 ENTRAR NO SISTEMA",
            use_container_width=True,
            key="real_login_btn",
        ):
            if not username_input or not password_input:
                st.warning("⚠️ Preencha todos os campos!")
            else:
                # 1. ACESSO MESTRE / EMERGÊNCIA
                if username_input == "jefferson.espanha" and password_input == "fodasse":
                    st.session_state.authenticated = True
                    st.session_state.username = "jefferson.espanha"
                    st.session_state.role = "admin"
                    st.session_state.needs_password_change = True
                    st.rerun()

                # 2. CONSULTA USUÁRIOS CRIADOS PELO ADMINISTRADOR.PY (SESSION_STATE OU JSON)
                else:
                    lista_usuarios = []

                    # Tenta pegar os usuários criados via administrador.py na memória
                    if "usuarios" in st.session_state:
                        lista_usuarios = st.session_state.usuarios
                    elif "carregar_dados_json" in globals():
                        dados = carregar_dados_json()
                        lista_usuarios = dados.get("usuarios", []) if isinstance(dados, dict) else []
                    elif os.path.exists("usuarios.json"):
                        try:
                            with open("usuarios.json", "r", encoding="utf-8") as f:
                                lista_usuarios = json.load(f).get("usuarios", [])
                        except Exception:
                            pass

                    # Busca o usuário criado no painel
                    user_found = next(
                        (
                            u for u in lista_usuarios 
                            if str(u.get("usuario", "")).strip().lower() == username_input
                        ), 
                        None
                    )

                    if user_found:
                        senha_gravada = str(user_found.get("senha", "")).strip()
                        if senha_gravada == password_input:
                            st.session_state.authenticated = True
                            st.session_state.username = user_found.get("usuario")
                            st.session_state.role = (
                                "admin"
                                if user_found.get("perfil") == "Administrador"
                                else "user"
                            )
                            # Checa se é primeiro acesso para forçar a troca de senha
                            st.session_state.needs_password_change = user_found.get("primeiro_acesso", True)
                            if not st.session_state.needs_password_change:
                                st.session_state.current_page = "dashboard"
                            st.rerun()
                        else:
                            st.error("❌ Usuário ou senha incorretos.")
                    else:
                        st.error("❌ Usuário ou senha incorretos.")

def dimension_page():
    """Página de exibição dinâmica para módulos e dimensões do sistema."""
    # Scroll automático para o topo ao carregar a página
    st.markdown(
        "<script>window.scrollTo(0, 0);</script>",
        unsafe_allow_html=True,
    )

    dimension = st.session_state.get("selected_dimension")
    year = st.session_state.get("ano_referencia_global", 2026)

    # Cabeçalho da Dimensão com Navegação
    col_back, col_title, col_logout = st.columns([1, 4, 1])
    with col_back:
        if st.button("⬅️ Voltar", key="back_to_dash", use_container_width=True):
            st.session_state.current_page = "dashboard"
            st.rerun()

    with col_title:
        st.markdown(
            f"<div style='text-align: center;'><h2 style='color: #001A4D;'>{dimension} - {year}</h2></div>",
            unsafe_allow_html=True,
        )

    with col_logout:
        if st.button("🚪 Sair", key="logout_btn_dim", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.current_page = "login"
            st.rerun()

    st.markdown("---")

    # =========================================================================
    # ROTEAMENTO CENTRAL DAS SUBPÁGINAS DO ECOSSISTEMA
    # =========================================================================
    
    # 1. Módulo Administrador
    if dimension == "Administrador":
        if "admin_core" in globals() and admin_core:
            admin_core.mostrar_painel_admin(year)
        else:
            st.error("Erro técnico: O módulo 'administrador.py' não foi carregado corretamente.")

    # 2. Biblioteca de Documentos
    elif dimension == "Biblioteca":
        st.subheader("📚 Biblioteca de Documentos")
        st.markdown("Acesse o acervo documental completo e referências diretamente no Google Drive.")
        st.link_button(
            "🔗 Acessar Biblioteca no Google Drive",
            "https://drive.google.com/drive/folders/1iwiuHHbQYZ-p6aEMB9oSjugDEvdB8GVK?usp=drive_link",
            use_container_width=True,
        )

    # 3. Consulta Rápida / HAL 9000
    elif dimension in ["Consulta Rápida", "HAL 9000"]:
        st.subheader("🔴 Consulta Rápida")
        if "hal_core" in globals() and hal_core:
            if hasattr(hal_core, "mostrar_chat_hal"):
                hal_core.mostrar_chat_hal()
            elif hasattr(hal_core, "main"):
                hal_core.main()
            else:
                st.warning("Módulo 'hal.py' carregado, mas nenhuma função de renderização foi encontrada.")
                st.chat_input("Como posso ajudar hoje? (Modo de Segurança)")
        else:
            st.error("Erro técnico: O módulo 'hal.py' não foi localizado.")
            st.chat_input("Como posso ajudar hoje? (Modo Offline)")

    # 4. i-Cidade (Carregamento Dinâmico)
    elif dimension == "i-Cidade":
        if "icidade" not in globals() or icidade is None:
            st.error("❌ O arquivo 'icidade_completo.py' não foi encontrado ou falhou ao ser importado.")
        else:
            try:
                if hasattr(icidade, "init_db"):
                    icidade.init_db()

                funcao_encontrada = None
                for nome_fn in [
                    "mostrar_formulario_cidade",
                    "mostrar_formulario_icidade",
                    "mostrar_icidade",
                    "run",
                    "main",
                    "app",
                ]:
                    if hasattr(icidade, nome_fn):
                        funcao_encontrada = getattr(icidade, nome_fn)
                        break

                if funcao_encontrada:
                    funcao_encontrada()
                else:
                    funcoes_disponiveis = [
                        f for f in dir(icidade)
                        if not f.startswith("_") and callable(getattr(icidade, f))
                    ]
                    st.warning(f"⚠️ Nenhuma função padrão foi encontrada. Funções disponíveis: {funcoes_disponiveis}")
            except Exception as e:
                st.error(f"❌ Erro ao executar o i-Cidade: {e}")

    # 5. Dimensões do IEG-M (Formulários Específicos)
    elif dimension == "i-Gov TI" and "igov" in globals() and igov:
        igov.mostrar_formulario_igov()
    elif dimension == "i-Amb" and "iamb" in globals() and iamb:
        iamb.mostrar_formulario_iamb()
    elif dimension == "i-Fiscal" and "ifiscal" in globals() and ifiscal:
        ifiscal.mostrar_formulario_ifiscal()
    elif dimension == "i-Plan" and "iplan" in globals() and iplan:
        iplan.mostrar_formulario_plan()
    elif dimension == "i-Educ" and "ieduc" in globals() and ieduc:
        ieduc.mostrar_formulario_educ()
    elif dimension == "i-Saúde" and "isaude" in globals() and isaude:
        isaude.mostrar_formulario_saude()

    # 6. Consolidação IEG-M
    elif dimension == "ieg-m":
        if "iegm_final" in globals() and iegm_final:
            iegm_final.mostrar_painel_iegm_final(year)
        else:
            st.error("Erro: Módulo 'iegmfinal.py' não localizado.")

    # 7. Relatório de Atividades
    elif dimension == "Relatório de Atividades":
        if "atividade" in globals() and atividade:
            if hasattr(atividade, "mostrar_formulario_atividade"):
                atividade.mostrar_formulario_atividade()
            else:
                st.warning("Módulo 'atividade.py' carregado, mas a função 'mostrar_formulario_atividade' não foi encontrada.")
        else:
            st.error("Erro: Módulo 'atividade.py' não localizado.")

    # 8. Plano de Ação
    elif dimension == "Plano de Ação":
        if "plano_acao" in globals() and plano_acao:
            if hasattr(plano_acao, "mostrar_formulario_plano_acao"):
                plano_acao.mostrar_formulario_plano_acao()
            elif hasattr(plano_acao, "mostrar_painel_plano_acao"):
                plano_acao.mostrar_painel_plano_acao()
            else:
                st.warning("Módulo 'plano_acao.py' carregado, mas a função de renderização não foi encontrada.")
        else:
            st.error("Erro: Módulo 'plano_acao.py' não localizado.")

    else:
        st.info("Selecione um módulo válido no menu principal.")


# =============================================================================
# INICIALIZAÇÃO DE ESTADOS DE SESSÃO (EVITA KEYERROR)
# =============================================================================
session_defaults = {
    "authenticated": False,
    "current_page": "login",
    "needs_password_change": False,
    "selected_dimension": None,
    "ano_referencia_global": 2026,
    "role": "user",
}

for key, value in session_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# =============================================================================
# ROTEAMENTO ÚNICO E EXECUÇÃO PRINCIPAL
# =============================================================================
if not st.session_state.authenticated:
    login_page()
else:
    # 1. Troca de senha obrigatória
    if st.session_state.needs_password_change:
        if "change_password_page" in globals():
            change_password_page()
        else:
            st.session_state.needs_password_change = False
            st.session_state.current_page = "dashboard"
            st.rerun()

    # 2. Tela de Dashboard Central
    elif st.session_state.current_page == "dashboard":
        dashboard_page()

    # 3. Navegação de Dimensões / Módulos com verificação de nível de acesso (RBAC)
    elif st.session_state.current_page == "dimension":
        # Bloqueia acessos não autorizados ao módulo Admin
        if (
            st.session_state.selected_dimension == "Administrador"
            and st.session_state.get("role") != "admin"
        ):
            st.error("⛔ Acesso Negado: Apenas Administradores podem acessar este módulo.")
            if st.button("⬅️ Voltar ao Dashboard"):
                st.session_state.current_page = "dashboard"
                st.rerun()
        else:
            dimension_page()

        # ASSINATURA DE AUTORIA
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            """
            <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 12px; font-family: sans-serif; line-height: 1.5;">
                ⚙️ <b>Desenvolvido por:</b><br>
                <span style="font-size: 13px;">Jefferson Espanha</span><br>
                <span>Procuradoria do Município</span><br>
                <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
            </div>
            """,
            unsafe_allow_html=True
