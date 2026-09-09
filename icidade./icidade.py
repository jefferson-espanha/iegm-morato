import re
from nicegui import ui
import icidade  # Módulo importado diretamente

# Expressão regular para identificar links válidos
REGEX_PURE_URL = r'https?://[^\s]+'

# Dicionário de estado para carregamento inicial de dados
res_data = {}

def render_quesito(qid, titulo, pergunta, opcoes=None, is_text_area=False, placeholder_text=""):
    """
    Função reutilizável para renderizar cada bloco de quesito no NiceGUI usando icidade.py.
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

                    # Visualizador de links
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

            # Renderiza o bloco de comentários diretamente via icidade.py
            icidade.bloco_comentarios(qid, res_data)

            # Indicador dinâmico de pontuação
            lbl_pontos = ui.html().classes('mt-3 font-bold')

            def atualizar_label_pontos(pts, val):
                if opcoes is None:
                    lbl_pontos.set_content(f"<span style='color:#6c757d;'>📊 Impacto de Pontuação no Quesito {qid}: 0.0 pontos (Informativo)</span>")
                else:
                    cor = "#28a745" if pts > 0 else ("#dc3545" if val != "Selecione..." else "#6c757d")
                    lbl_pontos.set_content(f"<span style='color:{cor};'>📊 Impacto de Pontuação no Quesito {qid}: {pts:.1f} pontos</span>")

            atualizar_label_pontos(d_data.get("pontos", 0.0), d_data.get("valor", ""))

            # Ação de Salvamento utilizando o salvamento do icidade.py
            def salvar():
                val = input_valor.value
                link = input_link.value
                pts = opcoes.get(val, 0.0) if opcoes else 0.0
                coments = res_data.get(qid, {}).get("comentarios", [])

                # Chamada da função de salvamento do icidade.py
                icidade.save_resp(
                    qid=qid, 
                    valor=val, 
                    pontos=pts, 
                    link=link, 
                    comentarios=coments
                )
                
                atualizar_label_pontos(pts, val)
                ui.notify(f"Quesito {qid} salvo com sucesso!", type="positive", icon="check_circle")

            ui.button(f"💾 Salvar Quesito {qid}", on_click=salvar).props('color=primary').classes('mt-4')


@ui.page('/')
def main_page():
    ui.label("Formulário COMPDEC - Defesa Civil").classes('text-h4 mb-6')

    # QUESITO 1.0
    opcoes_10 = {
        "Selecione...": 0.0,
        "Sim (40 pts)": 40.0,
        "Não (00 pts)": 0.0
    }
    render_quesito(
        qid="1.0",
        titulo="Criação da COMPDEC ou Órgão Similar",
        pergunta="Foi criada a Coordenadoria Municipal de Proteção e Defesa Civil-COMPDEC ou órgão similar responsável pela execução, coordenação e mobilização de todas as ações de defesa civil no município?",
        opcoes=opcoes_10
    )

    # QUESITO 1.1
    render_quesito(
        qid="1.1",
        titulo="Dados do Instrumento Normativo COMPDEC",
        pergunta="Informe o Instrumento normativo, Número e Data da publicação da criação da COMPDEC ou órgão similar:",
        is_text_area=True,
        placeholder_text="Ex: Decreto nº 123 de 01/01/2025"
    )

    # QUESITO 1.2
    render_quesito(
        qid="1.2",
        titulo="Endereço Eletrônico do Instrumento Normativo",
        pergunta="Informe a página eletrônica (link na internet) do instrumento normativo que criou a COMPDEC ou órgão similar:",
        is_text_area=True,
        placeholder_text="https://www.municipio.sp.gov.br/legislacao"
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
        qid="1.3",
        titulo="Secretaria ou Diretoria de Subordinação",
        pergunta="A COMPDEC ou órgão similar está associada ou subordinada a qual secretaria/diretoria?",
        opcoes=opcoes_13
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
        qid="1.4",
        titulo="Atuação Sistêmica e Articulação da Defesa Civil",
        pergunta="Os órgãos e entidades da administração pública municipal atuam de forma sistêmica, articulados com a COMPDEC, nas ações de prevenção, mitigação, preparação, resposta e recuperação de acordo com a Política Nacional de Proteção e Defesa Civil - PNPDEC?",
        opcoes=opcoes_14
    )

ui.run(port=8080, title="COMPDEC - Defesa Civil")
