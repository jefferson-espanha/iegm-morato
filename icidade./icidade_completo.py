import re
from nicegui import ui

# Simuladores das funções e dados do seu backend
REGEX_PURE_URL = r'https?://[^\s]+'
res_data = {}  # Seu dicionário de dados persistentes

def save_resp(qid, valor, pontos, link, comentarios):
    """Sua função de salvamento no banco de dados/estado"""
    res_data[qid] = {
        "valor": valor,
        "pontos": pontos,
        "link": link,
        "comentarios": comentarios
    }

def bloco_comentarios(qid, res_data):
    """Substitua pela sua lógica/componente de comentários no NiceGUI"""
    ui.label(f"💬 Bloco de Comentários do Quesito {qid}").classes('text-caption text-grey-7 mt-2')


def render_quesito(qid, titulo, pergunta, opcoes=None, is_text_area=False, placeholder_text=""):
    """
    Função reutilizável para renderizar cada bloco de quesito no NiceGUI.
    """
    d_data = res_data.get(qid) or {"valor": "Selecione..." if opcoes else "", "pontos": 0.0, "link": "", "comentarios": []}
    
    with ui.card().classes('w-full mb-4 p-4 shadow-1 border-1'):
        with ui.expansion(f"📌 Quesito {qid} - {titulo}", value=True).classes('w-full font-bold'):
            ui.label(f"{qid} • {titulo}").classes('text-h6 text-primary mt-2')
            ui.label(pergunta).classes('text-body1 font-bold my-2')
            ui.label("ℹ Preencha os campos abaixo e clique no botão de salvar.").classes('text-caption text-grey-6 mb-4')

            with ui.row().classes('w-full gap-4 items-start'):
                # Coluna 1: Opções de seleção ou Área de Texto de dados
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

                # Coluna 2: Evidências/Links
                with ui.column().classes('flex-1'):
                    input_link = ui.textarea(
                        label="Link de Evidência / Documento:",
                        value=d_data.get("link", "")
                    ).classes('w-full').props('outlined rows=3')

                    # Visualizador reativo de links
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
                    
                    # Chamada inicial para carregar links salvos
                    atualizar_links_visuais()

            # Renderiza Comentários
            bloco_comentarios(qid, res_data)

            # Rótulo de Pontuação Reativo
            lbl_pontos = ui.html().classes('mt-3 font-bold')

            def atualizar_label_pontos(pts, val):
                if opcoes is None:
                    lbl_pontos.set_content(f"<span style='color:#6c757d;'>📊 Impacto de Pontuação no Quesito {qid}: 0.0 pontos (Informativo)</span>")
                else:
                    cor = "#28a745" if pts > 0 else ("#dc3545" if val != "Selecione..." else "#6c757d")
                    lbl_pontos.set_content(f"<span style='color:{cor};'>📊 Impacto de Pontuação no Quesito {qid}: {pts:.1f} pontos</span>")

            atualizar_label_pontos(d_data.get("pontos", 0.0), d_data.get("valor", ""))

            # Ação de Salvamento
            def salvar():
                val = input_valor.value
                link = input_link.value
                pts = opcoes.get(val, 0.0) if opcoes else 0.0
                coments = res_data.get(qid, {}).get("comentarios", [])

                save_resp(qid=qid, valor=val, pontos=pts, link=link, comentarios=coments)
                
                atualizar_label_pontos(pts, val)
                ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive", icon="check_circle")

            ui.button(f"💾 Salvar Quesito {qid}", on_click=salvar).props('color=primary').classes('mt-4')
