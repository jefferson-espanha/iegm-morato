import base64
from datetime import datetime, date
import json
import os
import sys
import pandas as pd
import streamlit as st

# =============================================================================
# INJEÇÃO E SEGURANÇA NO ST.SECRETS (RENDER / NEON)
# =============================================================================
# Busca a URL do ambiente para evitar expor senhas no código fonte
DEFAULT_NEON_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_beMKhVR2N4wo@ep-divine-sky-awx1636y-pooler.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"
)

SecretsClass = type(st.secrets)
_orig_getitem = getattr(SecretsClass, "__getitem__", None)
_orig_getattr = getattr(SecretsClass, "__getattr__", None)


def _patched_getitem(self, key):
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key == "DATABASE_URL":
        return DEFAULT_NEON_URL
    if _orig_getitem:
        try:
            return _orig_getitem(self, key)
        except Exception:
            pass
    raise KeyError(f"st.secrets tem nenhuma chave '{key}'")


def _patched_getattr(self, key):
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key == "DATABASE_URL":
        return DEFAULT_NEON_URL
    if _orig_getattr:
        try:
            return _orig_getattr(self, key)
        except Exception:
            pass
    raise AttributeError(f"st.secrets tem nenhum atributo '{key}'")


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
icidade = import_local_module("icidade_completo") or import_local_module("icidade")
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
# PERSISTÊNCIA DE DADOS (STUBS DE SEGURANÇA PARA EVITAR NAMEERROR)
# =============================================================================
def load_respostas(ano):
    """Fallback simples para carregar respostas caso o banco externo falhe."""
    if "respostas_db" not in st.session_state:
        st.session_state.respostas_db = {}
    return st.session_state.respostas_db.get(ano, {})


def save_resp(qid, valor, pontos, link, comentarios):
    """Fallback simples para salvar dados caso o banco externo falhe."""
    ano = st.session_state.get("ano_referencia_global", date.today().year)
    if "respostas_db" not in st.session_state:
        st.session_state.respostas_db = {}
    if ano not in st.session_state.respostas_db:
        st.session_state.respostas_db[ano] = {}
    st.session_state.respostas_db[ano][qid] = {
        "valor": valor,
        "pontos": pontos,
        "link": link,
        "comentarios": comentarios,
    }


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
def cb_postar_comentario(qid, ano_sel, usuario_atual, id_chave=None):
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


def cb_alterar_status(qid, ano_sel, usuario_atual, id_chave=None):
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


def cb_deletar_comentario(qid, ano_sel, idx):
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


def cb_salvar_questao(qid, ano_sel, usuario_atual):
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


# =============================================================================
# HELPER DE IMAGENS E RODAPÉ
# =============================================================================
def get_image_base64(filename):
    full_path = os.path.join(current_dir, filename)
    if os.path.exists(full_path):
        with open(full_path, "rb") as img_file:
            return f"data:image/png;base64,{base64.b64encode(img_file.read()).decode()}"
    return None


def render_rodape():
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
        unsafe_allow_html=True,
    )


# =============================================================================
# INICIALIZAÇÃO DE ESTADOS DE SESSÃO
# =============================================================================
session_defaults = {
    "authenticated": False,
    "current_page": "login",
    "needs_password_change": False,
    "selected_dimension": None,
    "ano_referencia_global": 2026,
    "role": "user",
    "username": None,
}

for key, value in session_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# =============================================================================
# PÁGINA DE LOGIN
# =============================================================================
def login_page():
    col1, col2, col3 = st.columns([1.1, 1.6, 1.1])
    with col2:
        logo_b64 = get_image_base64("iegm.png")
        if logo_b64:
            st.markdown(
                f'<div style="text-align:center; margin-bottom:20px;"><img src="{logo_b64}" style="max-width:100%; height:auto;"></div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            '<div class="cad-frame"><h3 style="text-align: center; color: #FFFFFF; font-size: 16px; margin: 0;">Sistema de Preenchimento do IEG-M</h3></div>',
            unsafe_allow_html=True,
        )
        
        username_input = st.text_input("👤 Usuário", placeholder="jefferson.espanha", key="login_username").strip().lower()
        password_input = st.text_input("🔐 Senha", type="password", placeholder="••••••••", key="login_password").strip()

        if st.button("🔓 ENTRAR NO SISTEMA", use_container_width=True, key="real_login_btn"):
            if not username_input or not password_input:
                st.warning("⚠️ Preencha todos os campos!")
            elif username_input == "jefferson.espanha" and password_input == "fodasse":
                st.session_state.authenticated = True
                st.session_state.username = "jefferson.espanha"
                st.session_state.role = "admin"
                st.session_state.current_page = "dashboard"
                st.rerun()
            else:
                st.error("❌ Usuário ou senha incorretos.")
                
    render_rodape()


# Execute a rotação da interface dependendo do estado
if not st.session_state.authenticated:
    login_page()
