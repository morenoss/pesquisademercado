# app.py

import os
import re
import uuid
import pickle
import json
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

from logica import calcular_preco_mercado
from relatorios import (
    gerar_relatorio_padrao,
    gerar_relatorio_prorrogacao,
    gerar_relatorio_mapa,
)
from gerador_pdf import criar_pdf_completo

# ============================== Configuração base ==============================

st.set_page_config(
    page_title="Avaliação de Pesquisa de Mercado",
    layout="wide",
    page_icon="assets/stj_favicon.ico",
    initial_sidebar_state="collapsed",
)
st.logo("assets/logo_stj.png", link="https://www.stj.jus.br", size="large")

# --- Google Analytics (GA4) ---
GA_MEASUREMENT_ID = "G-E1T298PPDR"  

# injeta o script do GA4 apenas uma vez por sessão
if not st.session_state.get("_ga_loaded", False):
    st.session_state._ga_loaded = True
    st.markdown(f"""
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{GA_MEASUREMENT_ID}');
    </script>
    """, unsafe_allow_html=True)

# ============================== Estado (session_state) ==============================

# Página atual do mini-router
if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = "inicio"

# Modo de análise selecionado na Home
if "tipo_analise" not in st.session_state:
    st.session_state.tipo_analise = None

# Itens do relatório consolidado (cada entrada é um dict com campos do item)
if "itens_analisados" not in st.session_state:
    st.session_state.itens_analisados = []

# Contador do "Item X" da tela de análise unitária
if "item_atual" not in st.session_state:
    st.session_state.item_atual = 1

# Índice do item sendo editado na análise unitária (ou None)
if "edit_item_index" not in st.session_state:
    st.session_state.edit_item_index = None

# Bases normalizadas para o fluxo “por fonte”
# itens:     [{id, descricao, unidade, quantidade, valor_unit_contratado?}]
# fontes:    [{id, nome, tipo}]
# propostas: [{item_id, fonte_id, preco, sei}]
if "itens" not in st.session_state:
    st.session_state.itens = []
if "fontes" not in st.session_state:
    st.session_state.fontes = []
if "propostas" not in st.session_state:
    st.session_state.propostas = []


# ============================== Helpers / Utilidades ==============================

def formatar_moeda(v) -> str:
    """Formata número como moeda BR."""
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_moeda_html(v) -> str:
    """Formata moeda com 'R$' seguro para HTML."""
    return formatar_moeda(v).replace("R$", "R&#36;&nbsp;")

_TAGS_RE = re.compile("<.*?>")

def strip_html(s: str) -> str:
    """Remove tags simples de HTML (uso em observações geradas)."""
    return _TAGS_RE.sub("", s or "")

def _todos_consolidados() -> bool:
    """True se TODOS os itens cadastrados (aba 1) já estiverem no relatório consolidado."""
    itens = st.session_state.get("itens", [])
    consol = st.session_state.get("itens_analisados", [])

    if not itens or not consol:
        return False

    # 1) Preferir checagem por id quando disponível (orig_item_id salvo no registro)
    ids_itens = {i.get("id") for i in itens if i.get("id")}
    ids_consol = {r.get("orig_item_id") for r in consol if r.get("orig_item_id")}
    if ids_consol:
        return ids_itens.issubset(ids_consol) and len(ids_consol) >= len(ids_itens)

    # 2) Fallback: comparar (descricao, unidade, quantidade)
    trip_itens = {
        (str(i.get("descricao", "")).strip(), str(i.get("unidade", "")).strip(), int(i.get("quantidade", 0)))
        for i in itens
    }
    trip_consol = {
        (str(r.get("descricao", "")).strip(), str(r.get("unidade", "")).strip(), int(r.get("quantidade", 0)))
        for r in consol
    }
    return trip_itens.issubset(trip_consol) and len(trip_consol) >= len(trip_itens)

def ga_track_page(page_key: str, page_title: str):
    """
    Dispara um 'page_view' do GA4 quando a página interna muda.
    Usa a querystring ?page=... como parte do path para separar as telas.
    """
    if not GA_MEASUREMENT_ID:
        return
    safe_title = page_title.replace("'", "\\'")
    safe_path  = f"/app?page={page_key}"
    st.markdown(f"""
    <script>
      try {{
        // envia um page_view "virtual" para o GA4
        gtag('event', 'page_view', {{
          page_title: '{safe_title}',
          page_location: window.location.href,
          page_path: '{safe_path}'
        }});
      }} catch(e) {{}}
    </script>
    """, unsafe_allow_html=True)

def _js_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")

def ga_event(name: str, params: dict | None = None):
    """
    Dispara um evento GA4 (gtag).
    Uso: ga_event('nome_evento', {'chave': 'valor', 'valor_numerico': 123})
    """
    if not GA_MEASUREMENT_ID:
        return
    payload = json.dumps(params or {}, ensure_ascii=False)
    safe_name = _js_escape(name)
    st.markdown(f"""
    <script>
      try {{
        gtag('event', '{safe_name}', {payload});
      }} catch(e) {{}}
    </script>
    """, unsafe_allow_html=True)


# ---------------- Navegação via querystring (API nova: st.query_params) ----------------

def _sync_page_from_query():
    """Sincroniza st.session_state.pagina_atual a partir de ?page=..."""
    page = st.query_params.get("page")  # retorna str ou None
    valid = {"inicio", "analise", "lancamento", "relatorios", "guia"}
    if page in valid and st.session_state.get("pagina_atual") != page:
        st.session_state.pagina_atual = page

def _goto(page: str):
    """Atualiza o router e a URL (?page=...)."""
    st.query_params["page"] = page
    st.session_state.pagina_atual = page
    _nomes_pag = {
        "inicio": "Início", "analise": "Análise de Item",
        "lancamento": "Lançar por Fonte", "relatorios": "Relatórios",
        "guia": "Guia",
    }
    ga_track_page(page, _nomes_pag.get(page, "Tela"))


def carregar_estilo():
    """Injeta o style.css, se existir."""
    try:
        with open("style.css", "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass  # estilo é opcional


def nav_lateral():
    """Menu lateral compacto, com 'links' + ícone e destaque do selecionado."""
    itens = [
        ("inicio",      "Início",            ":material/home:"),
        ("analise",     "Análise de Item",   ":material/analytics:"),
        ("lancamento",  "Lançar por Fonte",  ":material/library_add:"),
        ("relatorios",  "Relatórios",        ":material/receipt_long:"),
        ("guia",        "Guia",              ":material/menu_book:"),
    ]
    atual = st.session_state.get("pagina_atual", "inicio")

    with st.sidebar:
        # Estilização leve do menu
        st.markdown(
            """
            <style>
            [data-testid="stSidebar"] .stButton > button {
                width: 100%;
                text-align: left;
                border-radius: 10px;
                padding: 10px 12px;
            }
            [data-testid="stSidebar"] .stButton > button:hover {
                background: #f1f5f9 !important;
            }
            .st-nav-title { 
                font-weight: 600; 
                margin: 6px 0 8px 2px; 
                color: #111827;
                font-size: 0.95rem;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div class="st-nav-title">Navegação</div>', unsafe_allow_html=True)

        for key, label, ic in itens:
            ativo = (key == atual)
            st.button(
                label,
                key=f"nav_{key}",
                icon=ic,                              # ícone Material (não é emoji)
                type="primary" if ativo else "secondary",
                use_container_width=True,
                on_click=lambda p=key: _goto(p),      # muda a página + querystring
            )
        st.divider()
        st.caption(f"Avaliação de Pesquisa de Mercado — v{get_app_version()}")


def breadcrumb_topo():
    """Mostra um 'você está em…' discreto no topo da página (sem emoji)."""
    nomes = {
        "inicio": "Início",
        "analise": "Análise de Item",
        "lancamento": "Lançar por Fonte",
        "relatorios": "Relatórios",
        "guia": "Guia",
    }
    atual = nomes.get(st.session_state.get("pagina_atual", "inicio"), "Início")
    st.markdown(
        f"<div style='color:#6b7280;font-size:0.9rem;margin-top:4px;margin-bottom:8px'>"
        f"<strong>{atual}</strong></div>",
        unsafe_allow_html=True,
    )

def novo_id(prefixo="id") -> str:
    """Gera um id curto e legível para itens/fontes."""
    return f"{prefixo}_{uuid.uuid4().hex[:8]}"

# URLs (com fallback sensato)
REPO_URL = os.environ.get("APP_REPO_URL", "https://github.com/morenoss/pesquisademercado")
APP_URL  = os.environ.get("APP_URL",  "https://persquisamercadostj.streamlit.app/")  # corrigido o typo
def rodape_stj():
    st.markdown(
        f"""
        <div class="stj-footer">
          Projeto desenvolvido pela <strong>Secretaria de Administração (STJ)</strong>.
          Contato: <a href="mailto:stj.sad@stj.jus.br">stj.sad@stj.jus.br</a> •
          <a href="mailto:morenos@stj.jus.br">morenos@stj.jus.br</a><br/>
          <small>
            Código licenciado sob
            <a href="{REPO_URL}/blob/main/LICENSE.txt" target="_blank" rel="noopener">MIT</a>.
            Marcas e brasões: uso institucional.
          </small>
        </div>
        """,
        unsafe_allow_html=True,
    )

@st.cache_data(show_spinner=False)
def get_app_version() -> str:
    """
    Resolve a versão da aplicação (em ordem de preferência):
      1) APP_VERSION (variável de ambiente) ou STREAMLIT_APP_VERSION
      2) arquivo VERSION (raiz do projeto)
      3) git describe --tags (se repositório presente)
      4) fallback '0.0.0-dev'
    """
    # 1) var de ambiente (ex.: APP_VERSION=1.4.2)
    v = os.environ.get("APP_VERSION") or os.environ.get("STREAMLIT_APP_VERSION")
    if v:
        return v.strip()

    # 2) arquivo VERSION (contendo algo como: 1.4.2)
    vf = Path("VERSION")
    if vf.exists():
        try:
            return vf.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # 3) tag do git (em dev)
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass

    # 4) fallback
    return "0.0.0-dev"


# ============================== Callbacks (botões de linha) ==============================

def acao_editar(index: int):
    st.session_state.edit_item_index = index
    st.session_state.analise_resultados = None

def acao_excluir(index: int):
    if index < len(st.session_state.itens_analisados):
        st.session_state.itens_analisados.pop(index)
        if st.session_state.edit_item_index == index:
            st.session_state.edit_item_index = None

def acao_mover(index: int, direcao: int):
    novo_index = index + direcao
    if 0 <= novo_index < len(st.session_state.itens_analisados):
        item = st.session_state.itens_analisados.pop(index)
        st.session_state.itens_analisados.insert(novo_index, item)

def acao_duplicar(index: int):
    if 0 <= index < len(st.session_state.itens_analisados):
        item = st.session_state.itens_analisados[index].copy()
        item["item_num"] = len(st.session_state.itens_analisados) + 1
        st.session_state.itens_analisados.append(item)

def ir_para_inicio(): _goto("inicio")
def ir_para_analise(): _goto("analise")
def ir_para_lancamento(): _goto("lancamento")
def ir_para_relatorios(): _goto("relatorios")


# ============================== Páginas ==============================

def pagina_inicial():
    """Página inicial com a seleção do tipo de análise e carregamento de PKL."""
    st.title("Bem-vindo à Ferramenta de Avaliação de Pesquisa de Mercado")
    st.markdown(
        "Esta aplicação foi desenvolvida para **automatizar os cálculos e validações da pesquisa de mercado**, "
        "seguindo as diretrizes do "
        "[Manual de Pesquisa de Preços do STJ](https://www.stj.jus.br/publicacaoinstitucional/index.php/MOP/issue/archive)."
    )
    st.divider()
    st.subheader("Tipos de Análise Disponíveis")

    def selecionar(tipo: str):
        st.session_state.tipo_analise = tipo

    selecionado = st.session_state.get("tipo_analise")

    col1, col2, col3 = st.columns(3, gap="large")

    # --- Pesquisa Padrão (AZUL) ---
    with col1:
        st.info(
            "**Pesquisa Padrão**\n\n"
            "Análise padrão para novas contratações (pregão eletrônico ou dispensa com disputa)",
            icon=":material/bar_chart:",
        )
        ativo = selecionado == "Pesquisa Padrão"
        if ativo:
            st.markdown('<span class="stj-chip">Selecionado</span>', unsafe_allow_html=True)

        if st.button(
            "Pesquisa Padrão selecionada" if ativo else "Iniciar Pesquisa Padrão",
            type="primary" if ativo else "secondary",
            icon=":material/check_circle:" if ativo else ":material/play_circle:",
            use_container_width=True,
            key="btn_tipo_padrao",
        ):
            selecionar("Pesquisa Padrão")
            ga_event('selecionar_tipo', {'tipo_analise': 'Pesquisa Padrão'})

    # --- Prorrogação (VERDE) ---
    with col2:
        st.success(
            "**Prorrogação Contratual**\n\n"
            "Opção específica para prorogação contratual por comparar preço contratado x preço de mercado",
            icon=":material/update:",
        )
        ativo = selecionado == "Prorrogação"
        if ativo:
            st.markdown('<span class="stj-chip stj-chip--green">Selecionado</span>', unsafe_allow_html=True)

        if st.button(
            "Prorrogação selecionada" if ativo else "Iniciar Prorrogação",
            type="primary" if ativo else "secondary",
            icon=":material/check_circle:" if ativo else ":material/play_circle:",
            use_container_width=True,
            key="btn_tipo_prorrog",
        ):
            selecionar("Prorrogação")
            ga_event('selecionar_tipo', {'tipo_analise': 'Prorrogação'})

    # --- Mapa de Preços (AMARELO) ---
    with col3:
        st.warning(
            "**Mapa de Preços**\n\n"
            "Análise padrão para dispensa sem disputa, em que se destaca o melhor preço da pesquisa (após filtros)",
            icon=":material/map:",
        )
        ativo = selecionado == "Mapa de Preços"
        if ativo:
            st.markdown('<span class="stj-chip stj-chip--yellow">Selecionado</span>', unsafe_allow_html=True)

        if st.button(
            "Mapa de Preços selecionado" if ativo else "Iniciar Mapa de Preços",
            type="primary" if ativo else "secondary",
            icon=":material/check_circle:" if ativo else ":material/play_circle:",
            use_container_width=True,
            key="btn_tipo_mapa",
        ):
            selecionar("Mapa de Preços")
            ga_event('selecionar_tipo', {'tipo_analise': 'Mapa de Preços'})

    # ---- Próximos passos: mostram só DEPOIS da seleção ----
    if selecionado:
        st.markdown("---")
        st.subheader(f"Próximos passos para **{selecionado}**")

        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.markdown(
                """
                <div class="stj-next stj-next--green">
                  <h4 class="stj-next__title">Análise de Item</h4>
                  <p class="stj-next__desc">
                    Trabalhe <b>item por item</b> e veja o cálculo imediatamente.<br>
                    Ideal para poucos itens e <b>obter visão detalhada</b>.
                  </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.button(
                "Ir para Análise de Item",
                type="primary",
                icon=":material/analytics:",
                use_container_width=True,
                on_click=ir_para_analise,
                key="go_analise_item",
            )

        with c2:
            st.markdown(
                """
                <div class="stj-next stj-next--yellow">
                  <h4 class="stj-next__title">Lançar por Fonte (em lote)</h4>
                  <p class="stj-next__desc">
                    Ideal para muitos itens. Cadastre <b>itens e fontes</b> e informe os <b>preços por fornecedor</b> de uma vez.<br>
                    Depois, <b>consolide</b> automaticamente em <i>Itens Analisados</i>.
                  </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.button(
                "Ir para Lançar por Fonte",
                type="secondary",
                icon=":material/library_add:",
                use_container_width=True,
                on_click=ir_para_lancamento,
                key="go_lancar_fonte",
            )

    st.divider()
    st.subheader("Ou Carregue uma Análise Salva")
    uploaded_file = st.file_uploader("Carregar pesquisa salva (.pkl)", type="pkl", label_visibility="collapsed")
    if uploaded_file is not None:
        try:
            loaded_state = pickle.load(uploaded_file)
            st.session_state.update(loaded_state)
            st.success("Análise carregada. Revise os cards acima e escolha como deseja continuar.")
        except Exception as e:
            st.error(f"Erro ao carregar o ficheiro: {e}")


def pagina_analise():
    """Fluxo de análise de um único item."""
    if not st.session_state.tipo_analise:
        st.warning("Por favor, selecione um tipo de análise na página inicial para começar.")
        if st.button("Ir para a Página Inicial"):
            st.session_state.pagina_atual = "inicio"
        return

    # Quando NÃO estiver editando, garanta que o contador = último + 1
    if st.session_state.edit_item_index is None:
        st.session_state.item_atual = len(st.session_state.itens_analisados) + 1

    dados_atuais = {}
    modo_edicao = st.session_state.edit_item_index is not None
    if modo_edicao and st.session_state.edit_item_index < len(st.session_state.itens_analisados):
        dados_atuais = st.session_state.itens_analisados[st.session_state.edit_item_index]
    else:
        st.session_state.edit_item_index = None
        modo_edicao = False

    st.title(f"Análise de Preços - Modo: {st.session_state.tipo_analise}")
    st.markdown("---")

    header_text = f"Análise do Item {dados_atuais.get('item_num', st.session_state.item_atual)}"
    if modo_edicao:
        header_text += " (Modo de Edição)"
    st.header(header_text)

    # ------------------------ Formulário do item ------------------------
    with st.container(border=True):
        st.subheader("Informações Iniciais do Item")
        item_descricao = st.text_area(
            "Item Pesquisado (Descrição completa)",
            height=100,
            value=dados_atuais.get("descricao", ""),
        )
        cols = st.columns(3)
        item_quantidade = cols[0].number_input(
            "Quantidade", min_value=1, step=1, value=dados_atuais.get("quantidade", 1)
        )
        item_unidade = cols[1].text_input(
            "Unidade de Medida", placeholder="Ex: Unidade, Caixa", value=dados_atuais.get("unidade", "")
        )
        if st.session_state.tipo_analise == "Prorrogação":
            item_valor_contratado = cols[2].number_input(
                "Valor Unitário Contratado", min_value=0.01, format="%.2f",
                value=dados_atuais.get("valor_unit_contratado", 0.01)
            )

    # ------------------------ Tabela de preços ------------------------
    with st.container(border=True):
        st.subheader("Dados da Pesquisa de Preços")
        tipos_de_fonte_opcoes = [
            "Fornecedor", "Contrato", "Banco de Preços/Comprasnet",
            "Ata de Registro de Preços", "Pesquisa da Internet",
            "Mídia Especializada", "Outros"
        ]
        df_precos_inicial = pd.DataFrame(
            dados_atuais.get(
                "df_original",
                [
                    {"EMPRESA/FONTE": "", "TIPO DE FONTE": "Fornecedor", "LOCALIZADOR SEI": "", "PREÇO": None},
                    {"EMPRESA/FONTE": "", "TIPO DE FONTE": "Banco de Preços/Comprasnet", "LOCALIZADOR SEI": "", "PREÇO": None},
                ],
            )
        )
        df_editado = st.data_editor(
            df_precos_inicial,
            num_rows="dynamic",
            column_config={
                "TIPO DE FONTE": st.column_config.SelectboxColumn(options=tipos_de_fonte_opcoes),
                "PREÇO": st.column_config.NumberColumn(format="R$ %.2f"),
            },
            use_container_width=True,
            key=f"editor_{st.session_state.edit_item_index}",
        )

    # ------------------------ Critérios & análise ------------------------
    with st.container(border=True):
        st.subheader("Critérios e Resultados")
        with st.expander("Configurar Critérios de Análise"):
            limiar_elevado = st.slider("Percentual para Preço Excessivamente Elevado (%)", 0, 100, 25)
            limiar_inexequivel = st.slider("Percentual Mínimo para Preço Inexequível (%)", 0, 100, 75)
            usar_preco_minimo = st.checkbox("Utilizar PREÇO MÍNIMO como resultado final?")

        c_analisar, c_cancelar_top = st.columns(2)
        clicou_analisar = c_analisar.button("Analisar Preços", type="primary")
        if modo_edicao:
            if c_cancelar_top.button("Cancelar edição", type="secondary"):
                st.session_state.edit_item_index = None
                st.session_state.analise_resultados = None
                st.rerun()
        else:
            c_cancelar_top.write("")

        if clicou_analisar:
            # ORDENAÇÃO: ordenar preços asc para calcular (NAs por último)
            df_com_preco = (
                df_editado
                .dropna(subset=["PREÇO"])
                .sort_values(by="PREÇO", ascending=True, na_position="last")
                .reset_index(drop=True)
                .copy()
            )
            if not df_com_preco.empty:
                st.session_state.analise_resultados = calcular_preco_mercado(
                    df_com_preco, limiar_elevado, limiar_inexequivel
                )
                st.session_state.usar_preco_minimo = usar_preco_minimo
                # GA4: clique em analisar
                ga_event('analisar_precos', {
                    'tela': 'analise_item',
                    'tipo_analise': st.session_state.tipo_analise,
                    'precos_informados': int(df_com_preco.shape[0]),
                    'limiar_elevado_pct': int(limiar_elevado),
                    'limiar_inexequivel_pct': int(limiar_inexequivel),
                    'usar_preco_minimo': bool(usar_preco_minimo),
                }) 
            else:
                st.warning("Nenhum preço foi inserido para análise.")

    # ------------------------ Exibição dos resultados ------------------------
    if "analise_resultados" in st.session_state and st.session_state.analise_resultados:
        resultados = st.session_state.analise_resultados
        usar_preco_minimo = st.session_state.get("usar_preco_minimo", False)
        st.markdown("---")
        st.subheader("Avaliação Detalhada dos Preços")

        # Tabela de preços avaliados (ordenada por PREÇO asc)
        df_avaliado = resultados.get("df_avaliado", pd.DataFrame())
        if not df_avaliado.empty:
            df_show = (
                df_avaliado
                .copy()
                .sort_values(by="PREÇO", ascending=True, na_position="last")
                .reset_index(drop=True)
            )
            df_show["OBSERVAÇÃO"] = df_show["OBSERVAÇÃO_CALCULADA"].apply(strip_html)
            st.dataframe(
                df_show[["EMPRESA/FONTE", "TIPO DE FONTE", "LOCALIZADOR SEI", "PREÇO", "AVALIAÇÃO", "OBSERVAÇÃO"]],
                use_container_width=True,
                hide_index=True,
            )

            # Observações detalhadas (visual simples, cinza, sem fundo)
            obs_items = []
            for _, r in df_show.iterrows():
                txt = strip_html(r.get("OBSERVAÇÃO_CALCULADA", ""))
                if txt.strip():
                    fonte = r.get("EMPRESA/FONTE", "—")
                    obs_items.append(f"<li><b>{fonte}</b>: {txt}</li>")

            if obs_items:
                st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
                st.markdown(
                    "<div style='color:#6b7280;font-size:0.95rem;line-height:1.45;'>"
                    "<div style='font-weight:600;margin-bottom:6px;'>Observações detalhadas</div>"
                    "<ul style='margin:0 0 0 18px;padding:0;list-style:disc;'>"
                    + "".join(obs_items) +
                    "</ul></div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        problemas = resultados.get("problemas", [])
        justificativa_usuario = ""
        necessita_justificativa = len(problemas) > 0
        if necessita_justificativa:
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            st.error("Problemas da Pesquisa (Necessário Justificar no Processo):")
            for p in problemas:
                st.warning(f"- {p}")
            justificativa_usuario = st.text_area(
                "**Justificativa dos Problemas Encontrados:**",
                height=150,
                placeholder="Descreva aqui as ações tomadas...",
                key="justificativa_atual",
            )

        if "preco_mercado_calculado" in resultados:
            col_res1, col_res2 = st.columns(2)
            metodo_final = "PREÇO MÍNIMO" if usar_preco_minimo else resultados.get("metodo_sugerido", "N/A")
            preco_mercado_final = (
                resultados.get("melhor_preco_info", {}).get("PREÇO", 0)
                if usar_preco_minimo
                else resultados.get("preco_mercado_calculado", 0)
            )
            with col_res1:
                st.metric("MÉDIA (válidos)", f"R$ {resultados.get('media', 0):.2f}")
                st.metric("PREÇO MÍNIMO (válido)", f"R$ {resultados.get('melhor_preco_info', {}).get('PREÇO', 0):.2f}")
            with col_res2:
                st.metric("COEFICIENTE DE VARIAÇÃO", f"{resultados.get('coef_variacao', 0):.2f}%")
                st.metric("MÉTODO ESTATÍSTICO", metodo_final)

            st.success(f"**PREÇO DE MERCADO UNITÁRIO: R$ {preco_mercado_final:.2f}**")

            # Caixas informativas
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # Mapa de Preços: destacar melhor preço
            if st.session_state.tipo_analise == "Mapa de Preços":
                mp = resultados.get("melhor_preco_info", {})
                fonte = mp.get("EMPRESA/FONTE", "—")
                loc   = mp.get("LOCALIZADOR SEI", "—")
                preco = mp.get("PREÇO", 0.0)
                texto_mp = (
                    f"Melhor preço da pesquisa (após filtros): {formatar_moeda_html(preco)} "
                    f"— <b>Fonte:</b> {fonte} <b>| Localizador SEI:</b> {loc}"
                )
                st.markdown(
                    f"<div style='background:#e8f0fe;padding:12px 14px;border-radius:8px;"
                    f"font-size:1.05rem;font-weight:bold;color:#1a3d8f;margin-bottom:10px;'>{texto_mp}</div>",
                    unsafe_allow_html=True,
                )

            # Prorrogação: comparar contratado x mercado
            if st.session_state.tipo_analise == "Prorrogação":
                if modo_edicao:
                    contratado_salvo = dados_atuais.get("valor_unit_contratado", None)
                    valor_contratado_vis = float(contratado_salvo) if contratado_salvo is not None else float(item_valor_contratado)
                else:
                    valor_contratado_vis = float(item_valor_contratado)

                delta = preco_mercado_final - valor_contratado_vis
                mais_barato_caro = "mais caro" if delta > 0 else ("mais barato" if delta < 0 else "igual")

                texto = (
                    "Comparação (unitário): "
                    f"Mercado = {formatar_moeda_html(preco_mercado_final)} "
                    f"vs Contratado = {formatar_moeda_html(valor_contratado_vis)} "
                    f"→ Mercado está {mais_barato_caro} em {formatar_moeda_html(abs(delta))}."
                )
                st.markdown(
                    f"<div style='background:#e8f0fe;padding:12px 14px;border-radius:8px;"
                    f"font-size:1.05rem;font-weight:bold;color:#1a3d8f;margin-bottom:10px;'>{texto}</div>",
                    unsafe_allow_html=True,
                )

            # Ações (salvar/atualizar/cancelar)
            botao_desativado = necessita_justificativa and not justificativa_usuario.strip()
            label_botao = "Atualizar Item" if modo_edicao else "Adicionar Item ao Relatório"

            acoes_col1, acoes_col2 = st.columns([1, 1])
            clicou_salvar = acoes_col1.button(label_botao, type="primary", disabled=botao_desativado, use_container_width=True)
            clicou_cancelar = False
            if modo_edicao:
                clicou_cancelar = acoes_col2.button("Cancelar edição", type="secondary", use_container_width=True)

            if clicou_cancelar:
                st.session_state.edit_item_index = None
                st.session_state.analise_resultados = None
                st.rerun()

            if clicou_salvar:
                # ORDENAÇÃO: persistir df_original ordenado por PREÇO asc
                try:
                    df_salvar = (
                        pd.DataFrame(df_editado)
                        .sort_values(by="PREÇO", ascending=True, na_position="last")
                        .reset_index(drop=True)
                    )
                except Exception:
                    df_salvar = pd.DataFrame(df_editado)

                registro = {
                    "item_num": dados_atuais.get("item_num", st.session_state.item_atual),
                    "descricao": item_descricao.strip(),
                    "unidade": item_unidade.strip(),
                    "quantidade": int(item_quantidade),
                    "metodo_final": metodo_final,
                    "valor_unit_mercado": float(preco_mercado_final),
                    "valor_total_mercado": float(preco_mercado_final) * int(item_quantidade),
                    "df_original": df_salvar.to_dict("records"),
                    "problemas": problemas,
                    "justificativa": justificativa_usuario.strip(),
                }

                # PRORROGAÇÃO
                if st.session_state.tipo_analise == "Prorrogação":
                    valor_unit_contratado = float(item_valor_contratado)
                    valor_total_contratado = valor_unit_contratado * int(item_quantidade)
                    if preco_mercado_final < valor_unit_contratado:
                        avaliacao_contratado = "Negociar preço"
                    elif preco_mercado_final > valor_unit_contratado:
                        avaliacao_contratado = "Vantajoso"
                    else:
                        avaliacao_contratado = "Igual ao mercado"
                    registro.update({
                        "valor_unit_contratado": valor_unit_contratado,
                        "valor_total_contratado": valor_total_contratado,
                        "avaliacao_preco_contratado": avaliacao_contratado,
                        # campos do modo mapa (não usados aqui)
                        "valor_unit_melhor_preco": 0.0,
                        "valor_total_melhor_preco": 0.0,
                        "dados_melhor_proposta": "",
                    })

                # MAPA DE PREÇOS
                elif st.session_state.tipo_analise == "Mapa de Preços":
                    mp = resultados.get("melhor_preco_info", {})
                    melhor_unit = float(mp.get("PREÇO", 0.0))
                    registro.update({
                        "valor_unit_contratado": 0.0,
                        "valor_total_contratado": 0.0,
                        "avaliacao_preco_contratado": "",
                        "valor_unit_melhor_preco": melhor_unit,
                        "valor_total_melhor_preco": melhor_unit * int(item_quantidade),
                        "dados_melhor_proposta": f"FONTE: {mp.get('EMPRESA/FONTE','—')} | LOCALIZADOR SEI: {mp.get('LOCALIZADOR SEI','—')}",
                    })

                # PESQUISA PADRÃO
                else:
                    registro.update({
                        "valor_unit_contratado": 0.0,
                        "valor_total_contratado": 0.0,
                        "avaliacao_preco_contratado": "",
                        "valor_unit_melhor_preco": 0.0,
                        "valor_total_melhor_preco": 0.0,
                        "dados_melhor_proposta": "",
                    })

                if modo_edicao:
                    st.session_state.itens_analisados[st.session_state.edit_item_index] = registro
                    st.session_state.edit_item_index = None
                else:
                    st.session_state.itens_analisados.append(registro)
                    # garanta que o próximo número seja último+1
                    st.session_state.item_atual = len(st.session_state.itens_analisados) + 1

                if "justificativa_atual" in st.session_state:
                    del st.session_state["justificativa_atual"]
                st.session_state.analise_resultados = None
                # GA4: salvar item (novo/edição)
                ga_event('salvar_item', {
                    'tela': 'analise_item',
                    'tipo_analise': st.session_state.tipo_analise,
                    'modo': 'edicao' if modo_edicao else 'novo',
                    'tem_problemas': bool(problemas),
                    'usar_preco_minimo': bool(usar_preco_minimo),
                    'valor_unit_mercado': float(preco_mercado_final),
                    'quantidade': int(item_quantidade),
                })               
                st.success("Item salvo no relatório.")
                st.rerun()

    # ------------------------ Lista de itens salvos ------------------------
    st.markdown("---")
    if st.session_state.itens_analisados:
        st.subheader("Itens Salvos no Relatório")
        for i, item in enumerate(st.session_state.itens_analisados):
            item["item_num"] = i + 1
        for i, item in enumerate(st.session_state.itens_analisados):
            with st.container(border=True):
                cols = st.columns([0.6, 0.4])
                with cols[0]:
                    st.markdown(f"**Item {item['item_num']}:** {item.get('descricao', 'N/A')}")
                    st.markdown(
                        f"<small>Valor Unitário (mercado): R$ {item.get('valor_unit_mercado', 0):.2f}</small>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    btn_cols = st.columns([1, 1, 1, 0.5, 0.5])
                    btn_cols[0].button("✏️ Editar", key=f"edit_{i}", on_click=acao_editar, args=(i,), use_container_width=True)
                    btn_cols[1].button("🗑️ Excluir", key=f"delete_{i}", on_click=acao_excluir, args=(i,), use_container_width=True)
                    btn_cols[2].button("📑 Duplicar", key=f"dup_{i}", on_click=acao_duplicar, args=(i,), use_container_width=True)
                    btn_cols[3].button("▲", key=f"up_{i}", on_click=acao_mover, args=(i, -1), disabled=(i==0), use_container_width=True)
                    btn_cols[4].button("▼", key=f"down_{i}", on_click=acao_mover, args=(i, 1), disabled=(i==len(st.session_state.itens_analisados)-1), use_container_width=True)

    # ------------------------ Exportar + PDF ------------------------
    st.markdown("---")
    with st.container(border=True):
        st.subheader("Opções da Pesquisa Completa")
        exp_cols = st.columns(2)
        with exp_cols[0]:
            st.markdown("**Salvar Análise Atual**")
            state_to_save = {
                "itens_analisados": st.session_state.itens_analisados,
                "item_atual": st.session_state.item_atual,
                "tipo_analise": st.session_state.tipo_analise,
                # Persistir o fluxo por fonte
                "itens": st.session_state.itens,
                "fontes": st.session_state.fontes,
                "propostas": st.session_state.propostas,
            }
           
            # GA4: opções de exportação exibidas (analise_item)
            ga_event('mostrar_opcoes_exportacao', {
                'tela': 'analise_item'
            })

            st.download_button(
                label="💾 Exportar Pesquisa (.pkl)",
                data=pickle.dumps(state_to_save),
                file_name="pesquisa_mercado_salva.pkl",
                mime="application/octet-stream",
                use_container_width=True,
            )
        with exp_cols[1]:
            st.markdown("**Gerar Relatório Final em PDF**")
            num_processo_pdf = st.text_input("Nº do Processo (para PDF)", key="num_processo_pdf_final")
            if not st.session_state.itens_analisados:
                st.info("Adicione itens para gerar o PDF.")
            elif not num_processo_pdf.strip():
                st.warning("Informe o nº do processo.")
            else:
                pdf_bytes = criar_pdf_completo(
                    st.session_state.itens_analisados,
                    num_processo_pdf,
                    st.session_state.tipo_analise
                )
                st.download_button(
                    label="📄 Gerar PDF Completo",
                    data=pdf_bytes,
                    file_name=f"Relatorio_Completo_{num_processo_pdf.replace('/', '-')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )


def pagina_relatorio():
    """Visualização consolidada do relatório (somente leitura)."""
    st.title("Relatório Consolidado (Visualização)")
    num_processo = st.text_input(
        "Número do Processo (para visualização)",
        st.session_state.get("num_processo_pdf_final", "")
    )
    if not num_processo:
        st.info("Informe um número de processo para visualizar os relatórios.")
        return
    if not st.session_state.itens_analisados:
        st.warning("Nenhum item foi analisado ainda.")
        return
    st.markdown("---")

    tipo_analise = st.session_state.get("tipo_analise", "N/A")
    if tipo_analise == "Pesquisa Padrão":
        gerar_relatorio_padrao(st.session_state.itens_analisados, num_processo)
    elif tipo_analise == "Prorrogação":
        gerar_relatorio_prorrogacao(st.session_state.itens_analisados, num_processo)
    elif tipo_analise == "Mapa de Preços":
        gerar_relatorio_mapa(st.session_state.itens_analisados, num_processo)
    else:
        st.warning("Tipo de análise não identificado.")


def pagina_lancamento_por_fonte():
    """Fluxo em lote: cadastrar itens/fontes, lançar preços e consolidar."""
    st.title("Lançamento em Lote (por Fonte)")
    tabs = st.tabs([
        "1) Itens",
        "2) Fontes",
        "3) Lançar Preços por Fonte",
        "4) Consolidar em Itens Analisados"
    ])

    # ---------------------- TAB 1: ITENS ----------------------
    with tabs[0]:
        st.markdown("Cadastre os **itens e quantidades** primeiro.")

        df_itens = pd.DataFrame(
            st.session_state.itens or [],
            columns=["id", "descricao", "unidade", "quantidade", "valor_unit_contratado"]
        )
        if df_itens.empty:
            df_itens = pd.DataFrame(columns=["id", "descricao", "unidade", "quantidade", "valor_unit_contratado"])

        cols_map = {"descricao": "DESCRIÇÃO", "unidade": "UNIDADE", "quantidade": "QUANTIDADE"}
        if st.session_state.tipo_analise == "Prorrogação":
            cols_map["valor_unit_contratado"] = "VALOR UNIT. CONTRATADO"

        df_show = df_itens.rename(columns=cols_map)
        ordem = ["DESCRIÇÃO", "UNIDADE", "QUANTIDADE"] + (
            ["VALOR UNIT. CONTRATADO"] if "VALOR UNIT. CONTRATADO" in df_show.columns else []
        )
        df_show = df_show[ordem]

        column_cfg = {"QUANTIDADE": st.column_config.NumberColumn(min_value=1, step=1)}
        if "VALOR UNIT. CONTRATADO" in df_show.columns:
            column_cfg["VALOR UNIT. CONTRATADO"] = st.column_config.NumberColumn(format="R$ %.2f", min_value=0.0)

        edited = st.data_editor(
            df_show,
            num_rows="dynamic",
            use_container_width=True,
            column_config=column_cfg,
            key="editor_itens",
        )

        if st.button("Salvar Itens"):
            erros = []
            novos = []

            def _blank(s):
                return (s is None) or (isinstance(s, float) and pd.isna(s)) or (str(s).strip() == "")

            for i in range(len(edited)):
                desc  = edited.iloc[i].get("DESCRIÇÃO", "")
                unid  = edited.iloc[i].get("UNIDADE", "")
                qtde  = edited.iloc[i].get("QUANTIDADE", None)

                # linha totalmente vazia → ignora
                if _blank(desc) and _blank(unid) and _blank(qtde) and \
                   ("VALOR UNIT. CONTRATADO" not in edited.columns or _blank(edited.iloc[i].get("VALOR UNIT. CONTRATADO", None))):
                    continue

                # valida obrigatórios
                if _blank(desc) or _blank(unid) or (qtde is None) or pd.isna(qtde) or int(qtde) < 1:
                    erros.append(f"Linha {i+1}: preencha **DESCRIÇÃO**, **UNIDADE** e **QUANTIDADE (>=1)**.")

                valor_contr = 0.0
                if st.session_state.tipo_analise == "Prorrogação" and "VALOR UNIT. CONTRATADO" in edited.columns:
                    v = edited.iloc[i].get("VALOR UNIT. CONTRATADO", None)
                    if v is None or (isinstance(v, float) and pd.isna(v)) or float(v) <= 0:
                        erros.append(f"Linha {i+1}: **VALOR UNIT. CONTRATADO** deve ser > 0.")
                    else:
                        valor_contr = float(v)

                old_id = df_itens["id"].iloc[i] if (i < len(df_itens)) else None
                item = {
                    "id": old_id if (old_id is not None and not (isinstance(old_id, float) and pd.isna(old_id))) else novo_id("item"),
                    "descricao": str(desc or "").strip(),
                    "unidade": str(unid or "").strip(),
                    "quantidade": int(qtde or 1),
                    "valor_unit_contratado": valor_contr,
                }
                novos.append(item)

            if erros:
                st.error("Não foi possível salvar os itens:")
                for e in erros:
                    st.markdown(f"- {e}")
            else:
                # remove propostas órfãs (itens excluídos)
                ids_validos = {i["id"] for i in novos}
                st.session_state.propostas = [p for p in st.session_state.propostas if p.get("item_id") in ids_validos]
                st.session_state.itens = novos
                st.success("Itens salvos.")

    # ---------------------- TAB 2: FONTES ----------------------
    with tabs[1]:
        st.markdown("Cadastre aqui as **fontes/fornecedores**.")
        tipos_de_fonte_opcoes = [
            "Fornecedor", "Contrato", "Banco de Preços/Comprasnet",
            "Ata de Registro de Preços", "Pesquisa da Internet",
            "Mídia Especializada", "Outros"
        ]

        df_fontes = pd.DataFrame(st.session_state.fontes or [], columns=["id", "nome", "tipo"])
        if df_fontes.empty:
            df_fontes = pd.DataFrame(columns=["id", "nome", "tipo"])

        df_show = df_fontes.rename(columns={"nome": "EMPRESA/FONTE", "tipo": "TIPO DE FONTE"})
        df_show = df_show[["EMPRESA/FONTE", "TIPO DE FONTE"]]

        edited = st.data_editor(
            df_show,
            num_rows="dynamic",
            use_container_width=True,
            column_config={"TIPO DE FONTE": st.column_config.SelectboxColumn(options=tipos_de_fonte_opcoes)},
            key="editor_fontes",
        )

        if st.button("Salvar Fontes"):
            erros = []
            novos = []

            def _blank(s):
                return (s is None) or (isinstance(s, float) and pd.isna(s)) or (str(s).strip() == "")

            for i in range(len(edited)):
                nome = edited.iloc[i].get("EMPRESA/FONTE", "")
                tipo = edited.iloc[i].get("TIPO DE FONTE", "")
                # linha totalmente vazia → ignora
                if _blank(nome) and _blank(tipo):
                    continue
                if _blank(nome) or _blank(tipo):
                    erros.append(f"Linha {i+1}: preencha **EMPRESA/FONTE** e **TIPO DE FONTE**.")

                old_id = df_fontes["id"].iloc[i] if (i < len(df_fontes)) else None
                fonte = {
                    "id": old_id if (old_id is not None and not (isinstance(old_id, float) and pd.isna(old_id))) else novo_id("fonte"),
                    "nome": str(nome or "").strip(),
                    "tipo": str(tipo or "").strip(),
                }
                novos.append(fonte)

            if erros:
                st.error("Não foi possível salvar as fontes:")
                for e in erros:
                    st.markdown(f"- {e}")
            else:
                # remove propostas órfãs (fontes excluídas)
                ids_validos = {f["id"] for f in novos}
                st.session_state.propostas = [p for p in st.session_state.propostas if p.get("fonte_id") in ids_validos]
                st.session_state.fontes = novos
                st.success("Fontes salvas.")

    # ---------------- TAB 3: LANÇAR PREÇOS POR FONTE ----------------
    with tabs[2]:
        if not st.session_state.fontes or not st.session_state.itens:
            st.info("Cadastre **Itens** e **Fontes** nas abas anteriores.")
        else:
            fontes_opts = {f["nome"]: f["id"] for f in st.session_state.fontes}
            fonte_nome = st.selectbox("Selecione a Fonte/Fornecedor", list(fontes_opts.keys()))
            fonte_id = fontes_opts[fonte_nome]

            # índice das propostas existentes por (item_id, fonte_id)
            idx = {(p["item_id"], p["fonte_id"]): p for p in st.session_state.propostas}

            linhas = []
            for it in st.session_state.itens:
                existente = idx.get((it["id"], fonte_id), {})
                linhas.append({
                    "ITEM": it["descricao"],
                    "UNID.": it["unidade"],
                    "QUANT.": it["quantidade"],
                    "PREÇO UNIT.": existente.get("preco", None),
                    "LOCALIZADOR SEI": existente.get("sei", ""),
                    "_item_id": it["id"],
                })

            df_lanc = pd.DataFrame(linhas)

            edited = st.data_editor(
                df_lanc[["ITEM", "UNID.", "QUANT.", "PREÇO UNIT.", "LOCALIZADOR SEI"]],
                num_rows="fixed",            # não adiciona/remove linhas
                use_container_width=True,
                column_config={
                    "ITEM":  st.column_config.TextColumn(disabled=True),
                    "UNID.": st.column_config.TextColumn(disabled=True),
                    "QUANT.": st.column_config.NumberColumn(disabled=True),
                    "PREÇO UNIT.": st.column_config.NumberColumn(format="R$ %.2f", min_value=0.0),
                    "LOCALIZADOR SEI": st.column_config.TextColumn(),
                },
                key=f"editor_precos_{fonte_id}",
            )

            if st.button("Salvar Preços desta Fonte"):
                # regrava por item desta fonte
                for i, it in enumerate(st.session_state.itens):
                    preco = edited.iloc[i]["PREÇO UNIT."]
                    sei   = (edited.iloc[i]["LOCALIZADOR SEI"] or "").strip()

                    # se preço vazio → remove proposta existente
                    if preco is None or (isinstance(preco, float) and pd.isna(preco)):
                        st.session_state.propostas = [
                            p for p in st.session_state.propostas
                            if not (p["item_id"] == it["id"] and p["fonte_id"] == fonte_id)
                        ]
                        continue

                    # grava/atualiza
                    st.session_state.propostas = [
                        p for p in st.session_state.propostas
                        if not (p["item_id"] == it["id"] and p["fonte_id"] == fonte_id)
                    ]
                    st.session_state.propostas.append({
                        "item_id": it["id"],
                        "fonte_id": fonte_id,
                        "preco": float(preco),
                        "sei": sei,
                    })
                # GA4: salvar preços da fonte
                ga_event('salvar_precos_fonte', {
                    'tela': 'lancamento_por_fonte',
                    'fonte_nome': fonte_nome,
                     'qtd_itens': int(len(st.session_state.itens)),
                })
   
                st.success("Preços salvos para esta fonte.")

    # ------------- TAB 4: CONSOLIDAR EM ITENS ANALISADOS -------------
    with tabs[3]:
        st.markdown("Gere os **itens analisados** automaticamente a partir das propostas lançadas.")
        colc = st.columns(3)
        with colc[0]:
            limiar_elevado = st.slider("Excessivo > (%) da média dos demais", 0, 100, 25)
        with colc[1]:
            limiar_inexequivel = st.slider("Inexequível < (%) da média dos demais", 0, 100, 75)
        with colc[2]:
            usar_preco_minimo = st.checkbox("Usar PREÇO MÍNIMO como resultado final?")

        substituir = st.checkbox("Substituir os itens já existentes no relatório", value=True)

        # --- ETAPA 1: Gerar PRÉVIA (não grava ainda) ---
        if st.button("Gerar PRÉVIA"):
            fontes_by_id = {f["id"]: f for f in st.session_state.fontes}
            buffer = []       # lista de entradas para confirmar depois

            for it in st.session_state.itens:
                # df de preços do item a partir das propostas
                registros = []
                for p in st.session_state.propostas:
                    if p["item_id"] != it["id"]:
                        continue
                    fonte = fontes_by_id.get(p["fonte_id"])
                    if not fonte:
                        continue
                    registros.append({
                        "EMPRESA/FONTE": fonte["nome"],
                        "TIPO DE FONTE": fonte["tipo"],
                        "LOCALIZADOR SEI": p.get("sei", ""),
                        "PREÇO": p.get("preco", None),
                    })
                df_precos = pd.DataFrame(registros)

                # ignorar itens sem preço e ORDENAR por PREÇO asc (NAs ao final)
                if df_precos.empty or df_precos["PREÇO"].dropna().empty:
                    continue
                df_precos = df_precos.sort_values(by="PREÇO", ascending=True, na_position="last").reset_index(drop=True)

                resultados = calcular_preco_mercado(df_precos, limiar_elevado, limiar_inexequivel)

                preco_merc = float(resultados.get("preco_mercado_calculado", 0.0))
                metodo     = resultados.get("metodo_sugerido", "N/A")

                # melhor preço pode vir Series ou dict → normaliza para dict
                melhor_raw = resultados.get("melhor_preco_info", None)
                if isinstance(melhor_raw, pd.Series):
                    melhor = melhor_raw.to_dict()
                elif isinstance(melhor_raw, dict):
                    melhor = melhor_raw
                else:
                    melhor = {}
                melhor_unit = float(melhor.get("PREÇO", 0.0))

                preco_final = melhor_unit if usar_preco_minimo else preco_merc

                registro = {
                    "item_num": 0,  # será renumerado na confirmação
                    "descricao": it["descricao"].strip(),
                    "unidade": it["unidade"].strip(),
                    "quantidade": int(it["quantidade"]),
                    "metodo_final": "PREÇO MÍNIMO" if usar_preco_minimo else metodo,
                    "valor_unit_mercado": float(preco_final),
                    "valor_total_mercado": float(preco_final) * int(it["quantidade"]),
                    # ORDENAÇÃO: persistir df_original já ordenado
                    "df_original": df_precos.to_dict("records"),
                    "problemas": resultados.get("problemas", []),
                    "justificativa": "",
                }

                # MAPA DE PREÇOS
                if st.session_state.tipo_analise == "Mapa de Preços":
                    registro.update({
                        "valor_unit_melhor_preco": melhor_unit,
                        "valor_total_melhor_preco": melhor_unit * int(it["quantidade"]),
                        "dados_melhor_proposta": (
                            f"FONTE: {melhor.get('EMPRESA/FONTE','—')} | "
                            f"LOCALIZADOR SEI: {melhor.get('LOCALIZADOR SEI','—')}"
                        ),
                    })
                else:
                    registro.update({
                        "valor_unit_melhor_preco": 0.0,
                        "valor_total_melhor_preco": 0.0,
                        "dados_melhor_proposta": "",
                    })

                # PRORROGAÇÃO
                if st.session_state.tipo_analise == "Prorrogação":
                    contr_unit = float(it.get("valor_unit_contratado", 0.0) or 0.0)
                    contr_tot  = contr_unit * int(it["quantidade"])
                    if preco_final < contr_unit:
                        avaliacao = "Negociar preço"
                    elif preco_final > contr_unit:
                        avaliacao = "Vantajoso"
                    else:
                        avaliacao = "Igual ao mercado"
                    registro.update({
                        "valor_unit_contratado": contr_unit,
                        "valor_total_contratado": contr_tot,
                        "avaliacao_preco_contratado": avaliacao,
                    })
                else:
                    registro.update({
                        "valor_unit_contratado": 0.0,
                        "valor_total_contratado": 0.0,
                        "avaliacao_preco_contratado": "",
                    })

                # Linha da prévia (resumo)
                linha_preview = {
                    "DESCRIÇÃO": it["descricao"],
                    "UNID.": it["unidade"],
                    "QTD.": int(it["quantidade"]),
                    "MÉTODO": registro["metodo_final"],
                    "VALOR UNIT. MERCADO": registro["valor_unit_mercado"],
                    "VALOR TOTAL MERCADO": registro["valor_total_mercado"],
                }
                if st.session_state.tipo_analise == "Mapa de Preços":
                    linha_preview.update({
                        "VALOR UNIT. MELHOR": registro["valor_unit_melhor_preco"],
                        "VALOR TOTAL MELHOR": registro["valor_total_melhor_preco"],
                        "DADOS DA PROPOSTA": registro["dados_melhor_proposta"],
                    })
                if st.session_state.tipo_analise == "Prorrogação":
                    linha_preview.update({
                        "VALOR UNIT. CONTRATADO": registro["valor_unit_contratado"],
                        "VALOR TOTAL CONTRATADO": registro["valor_total_contratado"],
                        "AVALIAÇÃO CONTRATADO": registro.get("avaliacao_preco_contratado", ""),
                    })

                buffer.append({
                    "item_uid": it["id"],
                    "descricao": it["descricao"],
                    "registro": registro,
                    "preview": linha_preview,
                    "problemas": registro["problemas"],
                })

            st.session_state.consol_buffer = buffer
            # GA4: gerar prévia
            ga_event('gerar_previa', {
                'tela': 'lancamento_por_fonte',
                'tipo_analise': st.session_state.tipo_analise,
                'itens_com_preco': int(len(buffer)),
            })


        # --- Se houver PRÉVIA, mostra, permite justificar e confirmar ---
        buffer = st.session_state.get("consol_buffer", [])
        if buffer:
            st.subheader("Prévia da Consolidação")
            prev_df = pd.DataFrame([b["preview"] for b in buffer])
            colcfg = {}
            for c in [
                "VALOR UNIT. MERCADO", "VALOR TOTAL MERCADO",
                "VALOR UNIT. MELHOR", "VALOR TOTAL MELHOR",
                "VALOR UNIT. CONTRATADO", "VALOR TOTAL CONTRATADO"
            ]:
                if c in prev_df.columns:
                    colcfg[c] = st.column_config.NumberColumn(format="R$ %.2f")
            st.dataframe(prev_df, use_container_width=True, hide_index=True, column_config=colcfg)

            # Campos de justificativa por item PROBLEMÁTICO
            st.markdown("----")
            st.markdown("**Justificativas obrigatórias para itens com problemas:**")
            faltantes = []
            for b in buffer:
                probs = b.get("problemas", []) or []
                if not probs:
                    continue
                with st.expander(f"Item: {b['descricao']} — {len(probs)} problema(s)"):
                    for p in probs:
                        st.warning(f"- {p}")
                    st.text_area(
                        "Justificativa",
                        key=f"just_{b['item_uid']}",
                        placeholder="Descreva as tratativas, diligências, validações etc.",
                        height=130
                    )

            # Botões de ação
            c1, c2 = st.columns([1, 1])
            if c1.button("Confirmar consolidação no relatório", type="primary"):
                # valida justificativas
                for b in buffer:
                    if b.get("problemas"):
                        texto = (st.session_state.get(f"just_{b['item_uid']}", "") or "").strip()
                        if not texto:
                            faltantes.append(b["descricao"])

                if faltantes:
                    st.error("Informe a justificativa para todos os itens com problemas:")
                    for desc in faltantes:
                        st.markdown(f"- {desc}")
                else:
                    # aplica ao relatório
                    if substituir:
                        st.session_state.itens_analisados = []
                    for b in buffer:
                        reg = dict(b["registro"])
                        reg["justificativa"] = (st.session_state.get(f"just_{b['item_uid']}", "") or "").strip()
                        reg["orig_item_id"] = b["item_uid"]
                        st.session_state.itens_analisados.append(reg)

                    # renumera item_num
                    for i, item in enumerate(st.session_state.itens_analisados):
                        item["item_num"] = i + 1

                    st.success(f"{len(buffer)} item(ns) consolidados no relatório.")
                    # GA4: confirmar consolidação
                    ga_event('confirmar_consolidacao', {
                        'tela': 'lancamento_por_fonte',
                        'itens_consolidados': int(len(buffer)),
                        'substituir_existentes': bool(substituir),
                    })


                    st.dataframe(prev_df, use_container_width=True, hide_index=True, column_config=colcfg)

                    # limpa prévia (sem rerun, para manter feedback visível)
                    del st.session_state["consol_buffer"]

            if c2.button("Descartar PRÉVIA"):
                del st.session_state["consol_buffer"]
                st.info("Prévia descartada.")
                ga_event('descartar_previa', {'tela': 'lancamento_por_fonte'})

        # ---- Exportar e Gerar PDF: somente quando TODOS os itens estiverem consolidados ----
        if _todos_consolidados():
            st.markdown("---")
            with st.container(border=True):
                st.subheader("Opções da Pesquisa Completa")

                exp_cols = st.columns(2)

                # 1) Exportar .pkl com todo o estado
                with exp_cols[0]:
                    st.markdown("**Salvar Análise Atual**")
                    state_to_save = {
                        "itens_analisados": st.session_state.itens_analisados,
                        "item_atual": st.session_state.item_atual,
                        "tipo_analise": st.session_state.tipo_analise,
                        "itens": st.session_state.itens,
                        "fontes": st.session_state.fontes,
                        "propostas": st.session_state.propostas,
                    }
                    # GA4: opções de exportação exibidas (lancamento_por_fonte)
                    ga_event('mostrar_opcoes_exportacao', {
                        'tela': 'lancamento_por_fonte',
                        'todos_consolidados': True
                    })

                    st.download_button(
                        label="💾 Exportar Pesquisa (.pkl)",
                        data=pickle.dumps(state_to_save),
                        file_name="pesquisa_mercado_salva.pkl",
                        mime="application/octet-stream",
                        use_container_width=True,
                    )

                # 2) Gerar PDF completo
                with exp_cols[1]:
                    st.markdown("**Gerar Relatório Final em PDF**")
                    num_processo_pdf = st.text_input("Nº do Processo (para PDF)", key="num_processo_pdf_final_lanc")

                    if not st.session_state.itens_analisados:
                        st.info("Consolide itens no relatório (acima) para gerar o PDF.")
                    elif not (num_processo_pdf or "").strip():
                        st.warning("Informe o nº do processo.")
                    else:
                        pdf_bytes = criar_pdf_completo(
                            st.session_state.itens_analisados,
                            num_processo_pdf,
                            st.session_state.tipo_analise
                        )
                        st.download_button(
                            label="📄 Gerar PDF Completo",
                            data=pdf_bytes,
                            file_name=f"Relatorio_Completo_{num_processo_pdf.replace('/', '-')}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            type="primary",
                        )
        else:
            st.info("As opções de exportação e PDF ficam disponíveis quando **todos os itens** cadastrados estiverem consolidados no relatório.")


def pagina_guia():
    """Guia resumido embutido (sem botão flutuante)."""
    st.title("Guia rápido da Ferramenta de Avaliação de Pesquisa de Mercado")
    st.caption("Versão resumida, embutida no aplicativo • Atalhos e exemplos")

    st.markdown("""
### 🧭 Fluxo geral
1. **Escolha o tipo de análise** na página inicial: *Pesquisa Padrão*, *Prorrogação* ou *Mapa de Preços*.
2. Siga por um dos caminhos:
   - **Análise de Item** (item a item, com cálculo imediato);
   - **Lançar por Fonte** (cadastre itens e fornecedores e lance preços em lote).
3. **Consolide** os itens para formar o relatório e **gere o PDF**.

---

### 📦 Análise de Item
- Preencha **Descrição**, **Quantidade** e **Unidade** (em *Prorrogação*, informe também o **Valor Unitário Contratado**).
- Adicione as cotações com **EMPRESA/FONTE**, **TIPO DE FONTE**, **LOCALIZADOR SEI** e **PREÇO**.
- Em **Critérios e Resultados**, ajuste:
  - *Preço Excessivo (> % da média)*
  - *Preço Inexequível (< % da média)*
  - **Usar PREÇO MÍNIMO** como resultado (opcional)
- Clique **Analisar Preços** para ver:
  - Avaliação de cada preço (Válido, Inexequível, etc.);
  - **Média** (dos válidos), **Coef. de Variação**, **Método Estatístico**;
  - **Preço de Mercado Unitário**.
- Havendo **problemas** (ex.: poucos válidos), escreva a **Justificativa** para salvar.

> **Salvar**: “Adicionar Item ao Relatório” (ou “Atualizar Item”, se estiver editando).

---

### 🧾 Lançar por Fonte (em lote)
Aba **1) Itens**  
• Cadastre todos os itens (descrição, unidade, quantidade e, se for *Prorrogação*, o **valor contratado**).

Aba **2) Fontes**  
• Cadastre fornecedores/fontes e o **tipo** (Fornecedor, Comprasnet, Contrato, etc).

Aba **3) Lançar Preços por Fonte**  
• Selecione uma fonte e informe o **Preço Unitário** e o **Localizador SEI** para cada item.

Aba **4) Consolidar em Itens Analisados**  
• Ajuste os **critérios** e clique **Gerar PRÉVIA**.  
• Preencha **justificativas** para itens com problemas.  
• Clique **Confirmar consolidação no relatório**.

> Quando **todos** os itens cadastrados estiverem consolidados, ficam visíveis os botões para **Exportar .pkl** e **Gerar PDF**.

---

### 📤 Exportar / Importar
- **Exportar** (menu “Opções da Pesquisa Completa”): salva todo o trabalho em um `.pkl`.
- **Importar** (página inicial): carregue um `.pkl` para continuar de onde parou.

---

### 🧠 Dicas rápidas
- **Mapa de Preços**: o destaque vai para o **melhor preço válido** (após filtros).
- **Prorrogação**: o app compara **Mercado x Contratado** e sinaliza a diferença.
- Tabelas aceitam aumentar/diminuir linhas dinamicamente.
- No PDF, cabeçalho segue o padrão visual do STJ (brasão, título em azul #004164).

---

### ❓ Suporte
Dúvidas, sugestões e melhorias:
- **E-mail**: stj.sad@stj.jus.br / morenos@stj.jus.br
- **Manual STJ**: acesse o *Manual de Pesquisa de Preços do STJ* para as regras de negócio.
""")

# ============================== Bootstrap / Router ==============================

carregar_estilo()
nav_lateral()        # menu lateral (colapsado por padrão)
breadcrumb_topo()    # trilha no topo da página
_sync_page_from_query()  # garante que ?page=... reflita na navegação

# --- GA4: page_view por tela interna ---
_nomes_pag = {
    "inicio": "Início",
    "analise": "Análise de Item",
    "lancamento": "Lançar por Fonte",
    "relatorios": "Relatórios",
    "guia": "Guia",
}
_pag_key = st.session_state.get("pagina_atual", "inicio")
ga_track_page(_pag_key, _nomes_pag.get(_pag_key, "Tela"))

# Router simples
if st.session_state.pagina_atual == "inicio":
    pagina_inicial()
elif st.session_state.pagina_atual == "analise":
    pagina_analise()
elif st.session_state.pagina_atual == "lancamento":
    pagina_lancamento_por_fonte()
elif st.session_state.pagina_atual == "relatorios":
    pagina_relatorio()
elif st.session_state.pagina_atual == "guia":
    pagina_guia()

# Importante: REMOVIDO o botão flutuante "Guia"
# (o usuário acessa o Guia pelo menu lateral)

rodape_stj()
