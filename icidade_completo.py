import os
import sys
import re
import html
import json
import warnings
import logging
from datetime import datetime, date
from io import BytesIO

import psycopg2
from psycopg2.extras import RealDictCursor, Json
import streamlit as st
import plotly.graph_objects as go

# Configuração de Layout do Streamlit
st.set_page_config(
    page_title="Painel IEG-M | Gestão de Quesitos",
    page_icon="🏙️",
    layout="wide"
)

# =============================================================================
# CONSTANTES GLOBAIS & MAPEAMENTOS DO IEG-M
# =============================================================================

REGEX_PURE_URL = r'(https?://[^\s]+)'

FAIXA_CORES = {
    "C": "#ef4444",    # Vermelho
    "C+": "#f97316",   # Laranja
    "B": "#eab308",    # Amarelo
    "B+": "#22c55e",   # Verde Claro
    "A": "#16a34a"     # Verde
}

PONTUACOES_MAX = {
    "1.0": 40.0, "1.3": 5.0, "1.4": 50.0, "2.0": 20.0, "2.1": 30.0, "2.2": 10.0,
    "3.0": 10.0, "3.1": 10.0, "4.2": 10.0, "5.0": 30.0, "5.1.1": 20.0, "5.2": 10.0,
    "6.0": 30.0, "7.0": 30.0, "7.1": 10.0, "7.2": 80.0, "7.3": 10.0, "7.4": 10.0,
    "7.5": 10.0, "7.6": 10.0, "8.0": 30.0, "8.1.1.1": 20.0, "8.2": 10.0, "9.0": 30.0,
    "10.0": 0.0, "11.1": 20.0, "11.1.1": 10.0, "11.2": 10.0, "12.1": 20.0, "12.1.3": 10.0,
    "14.0": 30.0, "15.0": 50.0, "16.0": 50.0, "C1.1": 0.0
}

CATEGORIAS_MAP = {
    "DEFESA_CIVIL": {
        "label": "Defesa Civil / COMPDEC",
        "qids": ["1.0", "1.3", "1.4"]
    },
    "PLANEJAMENTO": {
        "label": "Planejamento e Gestão",
        "qids": ["2.0", "2.1", "2.2", "3.0", "3.1"]
    },
    "INFRAESTRUTURA": {
        "label": "Infraestrutura e Obras",
        "qids": ["4.2", "5.0", "5.1.1", "5.2", "6.0"]
    },
    "MEIO_AMBIENTE": {
        "label": "Meio Ambiente e Riscos",
        "qids": ["7.0", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6"]
    },
    "SAUDE_EDUCACAO": {
        "label": "Saúde e Educação",
        "qids": ["8.0", "8.1.1.1", "8.2", "9.0", "11.1", "11.1.1", "11.2"]
    },
    "TRANSPARENCIA": {
        "label": "Transparência e Governança",
        "qids": ["12.1", "12.1.3", "14.0", "15.0", "16.0"]
    }
}

# =============================================================================
# CONEXÃO OTIMIZADA E SEGURA COM O NEON (POSTGRESQL)
# =============================================================================

def get_db_url():
    """Recupera, higieniza e valida a URL de conexão do Neon."""
    db_url = os.environ.get("DATABASE_URL") or st.secrets.get("DATABASE_URL", "")
    if not db_url:
        st.error("❌ A variável DATABASE_URL do Neon não foi configurada nos Segredos do Streamlit!")
        st.stop()
    
    if "channel_binding=" in db_url:
        db_url = db_url.split("&channel_binding=")[0].split("?channel_binding=")[0]
    
    if "sslmode=require" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
        
    return db_url


class get_connection:
    """Context manager seguro para conexões diretas e gerenciadas com o Neon."""
    def __enter__(self):
        try:
            self.conn = psycopg2.connect(get_db_url())
            return self.conn
        except Exception as e:
            logging.error(f"Erro ao conectar com o Neon PostgreSQL: {e}")
            raise e

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "conn") and self.conn:
            try:
                if getattr(self.conn, "closed", 0) == 0:
                    if exc_type:
                        self.conn.rollback()
                    else:
                        self.conn.commit()
            except Exception as e:
                logging.error(f"Erro no encerramento da transação: {e}")
            finally:
                try:
                    self.conn.close()
                except Exception:
                    pass

# =============================================================================
# FUNÇÕES DE BANCO DE DADOS
# =============================================================================

@st.cache_data(ttl=5)
def load_respostas(ano: int) -> dict:
    """Busca do banco de dados todas as respostas relativas a um ano específico."""
    respostas = {}
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id, valor, pontos, link, comentarios FROM respostas WHERE ano = %s",
                    (ano,)
                )
                rows = cursor.fetchall()
                for row in rows:
                    comentarios_bruto = row.get("comentarios")
                    comentarios = []
                    
                    if isinstance(comentarios_bruto, list):
                        comentarios = comentarios_bruto
                    elif isinstance(comentarios_bruto, str) and comentarios_bruto and comentarios_bruto != "EMPTY_STRING":
                        try:
                            comentarios = json.loads(comentarios_bruto)
                        except Exception:
                            comentarios = []

                    respostas[str(row["id"])] = {
                        "valor": row["valor"] or "",
                        "pontos": float(row["pontos"] or 0.0),
                        "link": row["link"] or "",
                        "comentarios": comentarios
                    }
    except Exception as e:
        logging.error(f"Erro ao carregar respostas do ano {ano}: {e}")
    return respostas


def get_all_years_data() -> dict:
    """Carrega as respostas de todos os anos disponíveis no banco."""
    all_data = {}
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    for a in anos:
        resp = load_respostas(a)
        if resp:
            all_data[a] = resp
    return all_data


def save_resp(qid, valor, pontos, link, comentarios=None):
    """Salva/Atualiza a resposta mantendo os comentários intactos caso não sejam informados."""
    ano_sel = st.session_state.get("ano_referencia_global")
    if not ano_sel:
        st.warning("Nenhum ano de referência selecionado!")
        return

    if comentarios is None:
        dados_atuais = load_respostas(ano_sel)
        comentarios = dados_atuais.get(str(qid), {}).get("comentarios", [])

    if not isinstance(comentarios, list):
        comentarios = []

    comentarios_json = json.dumps(comentarios, ensure_ascii=False)
    timestamp_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO respostas (id, ano, valor, pontos, link, comentarios, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (id, ano) DO UPDATE SET
                        valor = EXCLUDED.valor,
                        pontos = EXCLUDED.pontos,
                        link = EXCLUDED.link,
                        comentarios = EXCLUDED.comentarios,
                        atualizado_em = EXCLUDED.atualizado_em;
                """, (
                    str(qid), 
                    int(ano_sel), 
                    str(valor), 
                    float(pontos), 
                    str(link), 
                    comentarios_json,
                    timestamp_atual
                ))
            conn.commit()
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Erro ao salvar quesito {qid} no banco de dados: {e}")

# =============================================================================
# BLOCO DE COMENTÁRIOS E DIÁLOGO INTERNO
# =============================================================================

def bloco_comentarios(questao_id, res_data, sufixo=None):
    """Gera o diálogo interno avançado com histórico e controle de status."""
    ano_sel = st.session_state.get("ano_referencia_global", date.today().year)
    usuario_atual = st.session_state.get("username", st.session_state.get("usuario", "Usuário Anônimo"))
    
    id_chave = f"{questao_id}_{sufixo}" if sufixo else questao_id
    key_texto = f"v_txt_com_{id_chave}_{ano_sel}"
    key_radio = f"rad_status_{id_chave}_{ano_sel}"
    
    if key_texto not in st.session_state:
        st.session_state[key_texto] = ""
        
    dados_questao = res_data.get(questao_id, {})
    historico = list(dados_questao.get("comentarios", []))
    
    status_global = "Resolvido"
    for com in historico:
        if isinstance(com, dict) and "status_definido" in com:
            status_global = com["status_definido"]
            
    badge_status = "🔴 PENDENTE" if status_global == "Pendente" else "🟢 RESOLVIDO"
    
    with st.expander(f"💬 Diálogo Interno {id_chave} | Status: {badge_status}", expanded=(status_global == "Pendente")):
        opcoes_status = ["Resolvido", "Pendente"]
        idx_status_atual = opcoes_status.index(status_global) if status_global in opcoes_status else 0
        
        novo_status = st.radio(
            f"Definir status para {id_chave}:",
            options=opcoes_status,
            index=idx_status_atual,
            horizontal=True,
            key=key_radio
        )
        
        if historico:
            for idx, com in enumerate(historico):
                if not isinstance(com, dict):
                    continue
                col_balao, col_lixeira = st.columns([11, 1])
                
                with col_balao:
                    autor = html.escape(str(com.get('autor', 'Anônimo')))
                    data_com = html.escape(str(com.get('data', '')))
                    texto_com = html.escape(str(com.get('texto', '')))
                    
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
                                <span style="font-size: 11px; color: #1e88e5; font-weight: bold;">{autor}</span> 
                                <span style="font-size: 10px; color: #999; margin-left: 10px;">{data_com}</span>
                                <p style="margin: 4px 0 0 0; font-size: 13px; color: #333;">{texto_com}</p>
                            </div>""", unsafe_allow_html=True
                        )
                
                with col_lixeira:
                    if st.button("🗑️", key=f"btn_del_com_{id_chave}_{idx}_{ano_sel}"):
                        historico.pop(idx)
                        save_resp(
                            qid=questao_id,
                            valor=dados_questao.get("valor", ""),
                            pontos=dados_questao.get("pontos", 0),
                            link=dados_questao.get("link", ""),
                            comentarios=historico
                        )
                        st.rerun()

        novo_texto = st.text_area("Novo comentário:", key=key_texto, height=70, label_visibility="collapsed")
        
        if st.button("Postar Comentário", key=f"btn_com_{id_chave}_{ano_sel}", type="primary"):
            texto_limpo = novo_texto.strip()
            houve_mudanca_status = (novo_status != status_global)
            
            if texto_limpo or houve_mudanca_status:
                if houve_mudanca_status:
                    historico.append({
                        "autor": "Sistema / " + usuario_atual,
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "texto": f"ℹ️ Alterou o status do quesito para: **{novo_status.upper()}**.",
                        "status_definido": novo_status
                    })
                
                if texto_limpo:
                    historico.append({
                        "autor": usuario_atual,
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "texto": texto_limpo,
                        "status_definido": novo_status
                    })
                
                save_resp(
                    qid=questao_id, 
                    valor=dados_questao.get("valor", ""), 
                    pontos=dados_questao.get("pontos", 0), 
                    link=dados_questao.get("link", ""),
                    comentarios=historico
                )
                
                del st.session_state[key_texto]
                st.rerun()
            else:
                st.warning("Digite um comentário ou altere o status antes de postar.")

# =============================================================================
# RENDERIZADOR GENÉRICO DE QUESITO
# =============================================================================

def renderizar_questao(qid, res_data, titulo="", descricao="", opcoes_dict=None):
    """Renderiza visualmente o quesito com entradas, seleção de pontos e comentários."""
    dados_q = res_data.get(qid, {})
    ano_sel = st.session_state.get("ano_referencia_global", 2026)
    
    val_existente = dados_q.get("valor", "Selecione...")
    pts_existente = float(dados_q.get("pontos", 0.0))
    link_existente = dados_q.get("link", "")
    comentarios_existentes = dados_q.get("comentarios", [])
    
    with st.container(border=True):
        st.markdown(f"#### Quesito `{qid}` — {titulo}")
        if descricao:
            st.write(f"**{descricao}**")
        
        col_input, col_meta = st.columns([3, 1])
        
        with col_input:
            if opcoes_dict:
                lista_opcoes = list(opcoes_dict.keys())
                idx_sel = lista_opcoes.index(val_existente) if val_existente in lista_opcoes else 0
                novo_valor = st.radio(
                    "Resposta do Indicador:",
                    options=lista_opcoes,
                    index=idx_sel,
                    key=f"rad_val_{qid}_{ano_sel}"
                )
                novos_pontos = opcoes_dict.get(novo_valor, 0.0)
            else:
                novo_valor = st.text_area(
                    "Resposta / Evidência:", 
                    value=val_existente, 
                    key=f"txt_val_{qid}_{ano_sel}",
                    height=80
                )
                novos_pontos = pts_existente

            novo_link = st.text_input(
                "Link da Evidência (URL / Documento):", 
                value=link_existente, 
                key=f"txt_link_{qid}_{ano_sel}"
            )

        with col_meta:
            if not opcoes_dict:
                novos_pontos = st.number_input(
                    "Pontuação:", 
                    value=pts_existente, 
                    key=f"num_pts_{qid}_{ano_sel}"
                )
            else:
                st.metric("Pontos Atribuídos", f"{novos_pontos:.1f} pts")
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            if st.button(f"💾 Salvar Quesito {qid}", key=f"btn_save_{qid}_{ano_sel}", type="primary", use_container_width=True):
                save_resp(
                    qid=qid, 
                    valor=novo_valor, 
                    pontos=novos_pontos, 
                    link=novo_link,
                    comentarios=comentarios_existentes
                )
                st.toast(f"Quesito {qid} salvo com sucesso!", icon="✅")
                st.rerun()

        bloco_comentarios(qid, res_data)

# =============================================================================
# SIDEBAR
# =============================================================================

def zerar_questionario(ano):
    """Deleta todas as respostas do ano selecionado no Neon PostgreSQL."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM respostas WHERE ano = %s", (ano,))
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Erro ao zerar questionário: {e}")


@st.dialog("🔒 Confirmação de Segurança")
def confirmar_zerar_dialog(ano):
    st.warning(f"Você está prestes a apagar todas as respostas de {ano}. Esta ação é irreversível!")
    
    senha = st.text_input("Digite a senha de administrador:", type="password")
    
    col_Sim, col_Nao = st.columns(2)
    with col_Sim:
        if st.button("Confirmar e Zerar", type="primary", use_container_width=True):
            if senha == "fidelios":
                zerar_questionario(ano)
                st.success(f"✅ Questionário de {ano} foi zerado!")
                st.rerun()
            else:
                st.error("❌ Senha incorreta!")
    with col_Nao:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()


def render_sidebar():
    st.sidebar.title("🛠️ Painel de Controle")
    anos = [2024, 2025, 2026, 2027, 2028, 2029, 2030]
    ano_sel = st.sidebar.selectbox("Ano de Referência:", anos, index=2, key="ano_referencia_global")

    res_data = load_respostas(ano_sel)
    total_pts = sum(item.get("pontos", 0) for item in res_data.values())

    if total_pts <= 500:   faixa, cor = "C",  "#ef4444"
    elif total_pts <= 599: faixa, cor = "C+", "#f97316"
    elif total_pts <= 749: faixa, cor = "B",  "#eab308"
    elif total_pts <= 899: faixa, cor = "B+", "#22c55e"
    else:                  faixa, cor = "A",  "#16a34a"

    st.sidebar.metric("Pontuação Total", f"{total_pts:.1f} pts")
    st.sidebar.markdown(
        f"**Faixa IEG-M:** <span style='color:{cor}; font-size:20px; font-weight:bold;'>{faixa}</span>",
        unsafe_allow_html=True
    )

    st.sidebar.divider()
    st.sidebar.subheader("⚙️ Gerenciamento")

    if st.sidebar.button("🔄 Atualizar Questionário", use_container_width=True):
        st.cache_data.clear()
        st.toast("Questionário atualizado!", icon="🔄")
        st.rerun()

    if st.sidebar.button("🗑️ Zerar Ano", help="Limpar todas as respostas do ano selecionado", use_container_width=True):
        confirmar_zerar_dialog(ano_sel)

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        <div style="text-align: center; color: #000000; font-weight: bold; font-style: italic; font-size: 11px; font-family: sans-serif; line-height: 1.5;">
            ⚙️ <b>Desenvolvido por:</b><br>
            <span style="font-size: 12px;">Jefferson Espanha</span><br>
            <span>Procuradoria do Município</span><br>
            <span style="font-size: 10px;">© 2026 • Francisco Morato / SP</span>
        </div>
        """,
        unsafe_allow_html=True
    )

    return total_pts, res_data, ano_sel

# =============================================================================
# GRÁFICOS
# =============================================================================

def grafico_pontos_por_ano(all_data):
    """Gráfico de barras vertical com pontos totais por ano."""
    anos = sorted(all_data.keys())
    totais, cores = [], []
    
    for ano in anos:
        res = all_data[ano]
        total = sum(v.get("pontos", 0) for k, v in res.items() if not k.startswith("COM_"))
        totais.append(total)
        
        if total <= 500:   cores.append("#ef4444")
        elif total <= 599: cores.append("#f97316")
        elif total <= 749: cores.append("#eab308")
        elif total <= 899: cores.append("#22c55e")
        else:              cores.append("#16a34a")
    
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[str(a) for a in anos],
        y=totais,
        marker_color=cores,
        text=[f"{t:.1f} pts" for t in totais],
        textposition="outside",
        hovertemplate="<b>Ano: %{x}</b><br>Pontos: %{y:.1f}<extra></extra>",
    ))
    
    fig.update_layout(
        title="Evolução da Pontuação Total por Ano",
        xaxis_title="Ano do Exercício",
        yaxis_title="Pontos Obtidos",
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=False,
        height=400,
    )
    return fig


def render_graficos(res_data_atual, ano_sel):
    st.header("📊 Métricas e Desempenho")
    all_data = get_all_years_data()
    
    if not all_data:
        st.info("Nenhum dado registrado ainda. Preencha os quesitos para gerar relatórios visuais.")
        return

    st.plotly_chart(grafico_pontos_por_ano(all_data), use_container_width=True)

# =============================================================================
# FORMULÁRIO PRINCIPAL & INSERÇÃO DAS QUESTÕES
# =============================================================================

def mostrar_formulario_cidade():
    total_pts, res_data, ano_sel = render_sidebar()

    st.title(f"🏙️ Painel IEG-M — Exercício {ano_sel}")

    aba_questionario, aba_graficos = st.tabs(["📋 Questionário de Quesitos", "📊 Gráficos de Desempenho"])

    with aba_questionario:
        st.info("Preencha as informações dos quesitos abaixo. As alterações são sincronizadas em tempo real com o banco de dados Neon.")

        # --- QUESITO 1.0 ---
        renderizar_questao(
            qid="1.0",
            res_data=res_data,
            titulo="Criação da COMPDEC / Defesa Civil",
            descricao="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil (COMPDEC) ou órgão similar no município?",
            opcoes_dict={
                "Selecione...": 0.0,
                "Sim (40 pts)": 40.0,
                "Não (00 pts)": 0.0
            }
        )

        # --- QUESITO 1.3 ---
        renderizar_questao(
            qid="1.3",
            res_data=res_data,
            titulo="Plano de Contingência de Proteção e Defesa Civil",
            descricao="O município possui Plano de Contingência formalmente instituído e atualizado?",
            opcoes_dict={
                "Selecione...": 0.0,
                "Sim (05 pts)": 5.0,
                "Não (00 pts)": 0.0
            }
        )

        # --- QUESITO 1.4 ---
        renderizar_questao(
            qid="1.4",
            res_data=res_data,
            titulo="Mapeamento de Áreas de Risco",
            descricao="Existe mapeamento oficial das áreas de risco de desastres no território municipal?",
            opcoes_dict={
                "Selecione...": 0.0,
                "Sim (50 pts)": 50.0,
                "Não (00 pts)": 0.0
            }
        )

        # --- QUESITO 2.0 ---
        renderizar_questao(
            qid="2.0",
            res_data=res_data,
            titulo="Capacitação da Equipe Técnico-Operacional",
            descricao="Os servidores do órgão participaram de treinamentos ou capacitações em gestão de riscos?",
            opcoes_dict={
                "Selecione...": 0.0,
                "Sim (20 pts)": 20.0,
                "Não (00 pts)": 0.0
            }
        )

        # --- QUESITO 3.0 ---
        renderizar_questao(
            qid="3.0",
            res_data=res_data,
            titulo="Sistema de Alerta Precoce",
            descricao="O município conta com sistema operando para emissão de alertas precoces à população?",
            opcoes_dict={
                "Selecione...": 0.0,
                "Sim (10 pts)": 10.0,
                "Não (00 pts)": 0.0
            }
        )

    with aba_graficos:
        render_graficos(res_data, ano_sel)

# =============================================================================
# EXECUÇÃO PRINCIPAL
# =============================================================================

if __name__ == "__main__":
    mostrar_formulario_cidade()
