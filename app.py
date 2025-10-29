# app.py

import os
import re
import uuid
import pickle
import json
import subprocess
import datetime
import base64
import io, zipfile
import hmac, hashlib

from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

from unidades import UNIDADES_PERMITIDAS, normalizar_unidade
from logica import calcular_preco_mercado, arredonda_nbr5891
from relatorios import (
    gerar_relatorio_padrao,
    gerar_relatorio_prorrogacao,
    gerar_relatorio_mapa,
)
from gerador_pdf import criar_pdf_completo, set_decimal_places

# ============================== Configuração base ==============================

st.set_page_config(
    page_title="Avaliação de Pesquisa de Mercado",
    layout="wide",
    page_icon="assets/stj_favicon.ico",
    initial_sidebar_state="collapsed",
)
try:
    st.logo("assets/logo_stj.png", link="https://www.stj.jus.br", size="large")
except Exception:
    st.image("assets/logo_stj.png", use_container_width=False)


# --- Tutoriais em vídeo (pequenos, ao lado do uploader) ---
def _resolve_video_path(candidates: list[str]) -> str | None:
    """Tenta os caminhos em 'candidates'; se não achar, procura qualquer .mp4 em /mnt/data e assets/."""
    for p in candidates:
        try:
            if Path(p).exists():
                return p
        except Exception:
            pass
    for folder in ("/mnt/data", "assets"):
        try:
            for mp4 in Path(folder).glob("*.mp4"):
                return str(mp4)
        except Exception:
            pass
    return None

@st.cache_data(show_spinner=False)
def _load_video_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")

def render_small_video(title: str, candidates: list[str], width_px: int = 200) -> None:
    """
    Mostra um player compacto (largura fixa) com fallback.
    - title: título curto acima do player
    - candidates: caminhos preferidos (ordem de prioridade)
    """
    st.caption(title)
    path = _resolve_video_path(candidates)
    if not path:
        st.info("Vídeo não encontrado. Coloque o MP4 em /mnt/data ou assets/ e recarregue.")
        try:
            lista_mnt = [p.name for p in Path("/mnt/data").glob("*.mp4")]
            lista_assets = [p.name for p in Path("assets").glob("*.mp4")]
            if lista_mnt or lista_assets:
                st.caption(f"MP4s — /mnt/data: {lista_mnt or 'nenhum'} • assets/: {lista_assets or 'nenhum'}")
        except Exception:
            pass
        return

    try:
        b64 = _load_video_b64(path)
        st.markdown(
            f"""
            <div style="max-width:{width_px}px">
              <video width="{width_px}" controls preload="metadata"
                     style="border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.08);display:block;">
                <source src="data:video/mp4;base64,{b64}" type="video/mp4" />
                Seu navegador não suporta vídeo HTML5.
              </video>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        st.video(path, start_time=0)  # fallback (pode ficar mais largo)

# Assinatura HMAC para estado salvo (ZIP/Pickle)
STATE_HMAC_SECRET = os.getenv("STATE_HMAC_SECRET", "").encode("utf-8")

# Durante a transição: deixe como false (padrão).
# No "corte definitivo": exporte REQUIRE_SIGNED_STATE=true no ambiente.
REQUIRE_SIGNED_STATE = os.getenv("REQUIRE_SIGNED_STATE", "false").lower() in ("1","true","yes","on")


# --- Google Analytics (GA4) ---
# Lido do ambiente do Rancher. Ex.: GA4_MEASUREMENT_ID = "G-XXXXXXX"
GA_MEASUREMENT_ID = os.getenv("GA4_MEASUREMENT_ID", "").strip()

GA_DEBUG = os.getenv("GA4_DEBUG", "false").lower() in ("1", "true", "yes", "on")

# --- GA singleton ---
GA_KEY = "ga_injected_once"

def _ensure_ga_bootstrap():
    """Carrega o GA4 uma única vez no topo da página."""
    if not GA_MEASUREMENT_ID:
        return
    if st.session_state.get(GA_KEY):
        return
    st.session_state[GA_KEY] = True

    st_html(f"""
    <script>
    (function(){{
      try {{
        const TOP = window.top || window;
        TOP.dataLayer = TOP.dataLayer || [];
        TOP.gtag = TOP.gtag || function(){{ TOP.dataLayer.push(arguments); }};
        if (!TOP.__GA4_LOADED__) {{
          const s = TOP.document.createElement('script');
          s.async = true;
          s.src = 'https://www.googletagmanager.com/gtag/js?id={_js_escape(GA_MEASUREMENT_ID)}';
          s.crossOrigin = 'anonymous';
          TOP.document.head.appendChild(s);

          TOP.gtag('js', new Date());
          TOP.gtag('config', '{_js_escape(GA_MEASUREMENT_ID)}', {{
            send_page_view: false,
            debug_mode: {str(GA_DEBUG).lower()}
          }});
          TOP.__GA4_LOADED__ = true;
        }}
      }} catch(e) {{
        console.error('GA4 bootstrap error:', e);
      }}
    }})();
    </script>
    """, height=1)

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
    
# Nº do processo — uma única "fonte da verdade"
if "num_processo_pdf_final" not in st.session_state:
    st.session_state["num_processo_pdf_final"] = (st.query_params.get("processo") or "").strip()

# Índice do item sendo editado na análise unitária (ou None)
if "edit_item_index" not in st.session_state:
    st.session_state.edit_item_index = None
if "itens" not in st.session_state:
    st.session_state.itens = []
if "fontes" not in st.session_state:
    st.session_state.fontes = []
if "propostas" not in st.session_state:
    st.session_state.propostas = []
    
# Casas decimais e regra de arredondamento (ABNT NBR 5891)
if "casas_decimais" not in st.session_state:
    st.session_state.casas_decimais = 2  # padrão
    
if "usar_nbr5891" not in st.session_state:
    st.session_state.usar_nbr5891 = True


# ============================== Helpers / Utilidades ==============================

def formatar_moeda(v) -> str:
    """Formata número como moeda BR."""
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_moeda_n(v, n: int = 2) -> str:
    n = max(0, min(7, int(n or 0)))
    try:
        f = f"R$ {{:,.{n}f}}"
        return f.format(float(v)).replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ 0,{('0'*n)}"
    
def _step_from_casas() -> float:
    n = max(0, min(7, int(st.session_state.casas_decimais or 0)))
    return 1.0 if n == 0 else 10 ** (-n)

def formatar_moeda_html_n(v, n: int = 2) -> str:
    return formatar_moeda_n(v, n).replace("R$", "R&#36;&nbsp;")

def validar_processo(numero: str) -> str | None:
    """
    Valida: 6 dígitos + '/' + ano. Ano não pode ser futuro.
    Retorna msg de erro (str) ou None se válido.
    """
    if not (numero or "").strip():
        return "Informe o número do processo."
    m = re.fullmatch(r"(\d{6})/(\d{4})", numero.strip())
    if not m:
        return "Formato inválido. Use 6 dígitos + '/' + ano (ex.: 011258/2025)."
    ano = int(m.group(2))
    ano_atual = datetime.date.today().year
    if ano > ano_atual:
        return f"O ano {ano} é maior que o atual ({ano_atual})."
    return None

def validar_sei(sei: str) -> str | None:
    """
    Valida nº do documento SEI: exatamente 7 dígitos.
    Retorna msg de erro (str) ou None se válido.
    """
    if not (sei or "").strip():
        return "Informe o nº do documento SEI (7 dígitos)."
    if not re.fullmatch(r"\d{7}", sei.strip()):
        faltam = 7 - len(sei.strip())
        return f"O documento SEI deve ter exatamente 7 dígitos ({'faltam' if faltam>0 else 'sobram'} {abs(faltam)} dígito(s))."
    return None

def _is_nan(x):
    return x is None or (isinstance(x, float) and pd.isna(x))

def sincronizar_para_lote_a_partir_de_analisados(force: bool = False):
    """
    Gera/atualiza st.session_state.itens, .fontes e .propostas
    a partir de st.session_state.itens_analisados.

    - Não apaga nada existente (apenas inclui o que faltar).
    - Evita duplicatas (usa chaves por conteúdo).
    - Se force=False, só roda quando itens/fontes ainda estão vazios.
    """
    analisados = st.session_state.get("itens_analisados", [])
    if not analisados:
        return

    # Só sementeia automaticamente se ainda não existe nada
    if not force and st.session_state.get("itens") and st.session_state.get("fontes"):
        return

    itens   = list(st.session_state.get("itens", []))
    fontes  = list(st.session_state.get("fontes", []))
    props   = list(st.session_state.get("propostas", []))

    # Índices para evitar duplicatas
    fonte_key_to_id = {(f["nome"], f["tipo"]): f["id"] for f in fontes}
    item_key_to_id  = {
    (i.get("descricao","").strip(),
     normalizar_unidade(i.get("unidade","")) or "",
     int(i.get("quantidade", 1))): i.get("id")
    for i in itens}
    prop_seen       = {(p["item_id"], p["fonte_id"], float(p["preco"]), p.get("sei","")) for p in props}

    def ensure_fonte(nome, tipo):
        nome = (nome or "").strip() or "—"
        tipo = (tipo or "").strip() or "Fornecedor"
        k = (nome, tipo)
        if k in fonte_key_to_id:
            return fonte_key_to_id[k]
        fid = novo_id("fonte")
        fontes.append({"id": fid, "nome": nome, "tipo": tipo})
        fonte_key_to_id[k] = fid
        return fid

    def ensure_item(desc, unid, qtd, valor_contratado=0.0):
        desc = (desc or "").strip()
        unid = normalizar_unidade((unid or "").strip())  # 👈 normaliza
        qtd  = int(qtd or 1)
        k = (desc, unid or "", qtd)
        if k in item_key_to_id:
            return item_key_to_id[k]
        iid = novo_id("item")
        itens.append({
            "id": iid,
            "descricao": desc,
            "unidade": unid or "UNIDADE",  # 👈 fallback seguro
            "quantidade": qtd,
            "valor_unit_contratado": float(valor_contratado or 0.0),
        })
        item_key_to_id[k] = iid
        return iid

    for reg in analisados:
        iid = ensure_item(
            reg.get("descricao",""),
            reg.get("unidade",""),
            reg.get("quantidade",1),
            # Só terá valor contratado no modo Prorrogação
            reg.get("valor_unit_contratado", 0.0)
        )

        # semeia justificativas por item (se vieram do fluxo unitário)
        jmap = st.session_state.setdefault("justificativas_por_item", {})
        just = (reg.get("justificativa") or "").strip()
        # não sobrescreve se já existir; sobrescreva se quiser quando force=True
        if just and (force or iid not in jmap):
            jmap[iid] = just
       

        for row in (reg.get("df_original") or []):
            preco = row.get("PREÇO", None)
            if _is_nan(preco):
                continue
            fid = ensure_fonte(row.get("EMPRESA/FONTE",""), row.get("TIPO DE FONTE",""))
            sei = (row.get("LOCALIZADOR SEI","") or "").strip()
            tup = (iid, fid, float(preco), sei)
            if tup in prop_seen:
                continue
            props.append({"item_id": iid, "fonte_id": fid, "preco": float(preco), "sei": sei})
            prop_seen.add(tup)

    st.session_state.itens = itens
    st.session_state.fontes = fontes
    st.session_state.propostas = props

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

def _make_export_state() -> dict:
    """Estado completo a ser salvo (reaproveitado nas telas)."""
    return {
        "itens_analisados": st.session_state.itens_analisados,
        "item_atual": st.session_state.item_atual,
        "tipo_analise": st.session_state.tipo_analise,

        # fluxo por fonte
        "itens": st.session_state.itens,
        "fontes": st.session_state.fontes,
        "propostas": st.session_state.propostas,

        # preferências (mantêm casas, arredondamento e critérios)
        "casas_decimais": st.session_state.get("casas_decimais", 2),
        "usar_nbr5891": st.session_state.get("usar_nbr5891", True),
        "limiar_elevado": st.session_state.get("limiar_elevado", 25),
        "limiar_inexequivel": st.session_state.get("limiar_inexequivel", 75),
        "usar_preco_minimo": st.session_state.get("usar_preco_minimo", False),
        
         # persistir número do processo e o mapa de justificativas
        "num_processo_pdf_final": st.session_state.get("num_processo_pdf_final", ""),
        "justificativas_por_item": st.session_state.get("justificativas_por_item", {}),
    }

def _zip_bytes_with_pkl(state: dict, inner_name: str = "pesquisa_mercado_salva.pkl") -> bytes:
    payload = pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
    sig = _hmac_sign(payload)
    envelope = {
        "__format__": "stj-pesquisa-v1",
        "payload_pickle": payload,
        "hmac_sha256": sig,
    }
    blob = pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, blob)
    return buf.getvalue()

def _hmac_sign(data: bytes) -> str:
    if not STATE_HMAC_SECRET:
        return ""  # sem secret → sem assinatura (modo legado)
    mac = hmac.new(STATE_HMAC_SECRET, data, hashlib.sha256).hexdigest()
    return mac

def _hmac_verify(data: bytes, sig: str) -> bool:
    if not STATE_HMAC_SECRET:
        return True  # sem secret → aceita (compatibilidade)
    try:
        mac = hmac.new(STATE_HMAC_SECRET, data, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, sig or "")
    except Exception:
        return False

def _load_state_from_upload(uploaded_file):
    """
    Lê .zip (com .pkl dentro) ou .pkl direto (envelope assinado).
    Trata PDF/HTML acidental. Se houver vários .pkl no ZIP, pega o mais novo.
    """
    data = uploaded_file.read()
    head8 = data[:8]

    def _unpack_envelope(raw: bytes) -> dict:
        # Aceita envelope assinado (novo) e payload direto (legado)
        obj = pickle.loads(raw)

        # NOVO FORMATO (envelope assinado)
        if isinstance(obj, dict) and obj.get("__format__") == "stj-pesquisa-v1":
            payload = obj.get("payload_pickle", b"")
            sig = obj.get("hmac_sha256", "")
            if not _hmac_verify(payload, sig):
                raise ValueError("Assinatura inválida do arquivo de projeto (HMAC falhou).")
            return pickle.loads(payload)

        # LEGADO (pickle “puro”)
        if REQUIRE_SIGNED_STATE:
            # corte definitivo: não aceitar mais
            raise ValueError(
                "Arquivo antigo sem assinatura. Abra no Ambiente de Migração e reexporte (ZIP) para usar aqui."
            )
        else:
            # período de transição: aceitar mas avisar
            try:
                st.info(
                    "Arquivo legado (sem assinatura) carregado. "
                    "Ao salvar/exportar novamente aqui, ele será assinado (mais seguro)."
                )
            except Exception:
                pass
            try:
                ga_event('import_legacy_detectado', {'tela': st.session_state.get('pagina_atual','inicio')})
            except Exception:
                pass
            return obj

    # ZIP?
    if head8.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # escolher o .pkl mais RECENTE
            pkls = [zi for zi in zf.infolist() if zi.filename.lower().endswith(".pkl")]
            if not pkls:
                raise ValueError("O ZIP não contém um arquivo .pkl.")
            pkls.sort(key=lambda z: z.date_time, reverse=True)
            latest = pkls[0]
            raw = zf.read(latest)
            return _unpack_envelope(raw)

    # Pickle “puro” (protocol header 0x80) — NÃO confie na extensão
    if head8[:1] == b"\x80":
        return _unpack_envelope(data)

    # Erros comuns
    if data[:5] == b"%PDF-":
        raise ValueError("Você enviou um PDF, não um arquivo de projeto (.zip/.pkl).")
    if data.lstrip()[:5].lower().startswith(b"<html"):
        raise ValueError("O arquivo recebido é uma página HTML (provável bloqueio do proxy). Baixe a opção ZIP no app.")

    raise ValueError("Formato não reconhecido. Envie o .zip (recomendado) ou o .pkl gerado pelo app.")

def _js_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")

def _ga_fire(kind: str, name: str | None, params: dict | None):
    if not GA_MEASUREMENT_ID:
        return
    payload = json.dumps(params or {}, ensure_ascii=False)
    st_html(f"""
    <script>
    (function(){{
      try {{
        const TOP = window.top || window;
        TOP.dataLayer = TOP.dataLayer || [];
        TOP.gtag = TOP.gtag || function(){{ TOP.dataLayer.push(arguments); }};

        const data = Object.assign({{debug_mode: {str(GA_DEBUG).lower()}}}, {payload});
        if ('{_js_escape(kind)}' === 'page_view') {{
          TOP.gtag('event', 'page_view', data);
        }} else {{
          TOP.gtag('event', '{_js_escape(name or "")}', data);
        }}
      }} catch(e) {{
        console.error('GA4 event error:', e);
      }}
    }})();
    </script>
    """, height=1)

def ga_page_view(page_path: str, page_title: str):
    if not GA_MEASUREMENT_ID:
        return
    _ga_fire("page_view", None, {
        "page_path": page_path,
        "page_title": page_title,
    })

def ga_event(name: str, params: dict | None = None):
    if not GA_MEASUREMENT_ID:
        return
    _ga_fire("event", name, params or {})
    
def _autoformat_processo(raw: str) -> str | None:
    """
    Se 'raw' for 1–6 dígitos (com ou sem '/AAAA'), retorna 'dddddd/AAAA'
    (com zeros à esquerda e, se faltar, ano atual). Caso contrário, None.
    Não altera quando o formato fugir dessas possibilidades (mantém regras atuais).
    """
    s = (raw or "").strip()
    m = re.fullmatch(r"(\d{1,6})(?:\s*/\s*(\d{4}))?", s)
    if not m:
        return None
    numero = m.group(1).zfill(6)
    ano = m.group(2) or str(datetime.date.today().year)
    return f"{numero}/{ano}"
    
def input_num_processo(label: str, *, placeholder: str = "011258/2025") -> str:
    """
    Campo unificado do nº do processo, com autoformatação em tela.
    - WKEY (widget) é separado do valor canônico (CANO) para evitar conflitos.
    - Usa callback on_change para atualizar o texto visível imediatamente.
    """
    ss = st.session_state
    WKEY = "num_processo_pdf_final_input"   # key do widget
    CANO = "num_processo_pdf_final"         # valor canônico

    # --------- Seed do valor canônico a partir de sessão / querystring / legado ---------
    atual = str(ss.get(CANO) or "").strip()
    if not atual:
        qp = ""
        try:
            qp = str(st.query_params.get("processo") or "").strip()
        except Exception:
            pass
        legado = str(ss.get("num_processo_pdf_final_lanc") or "").strip()
        atual = qp or legado or ""

    # Autoformata seed, se couber nas novas regras (sem mudar regras anteriores)
    seed_fmt = _autoformat_processo(atual) or atual
    if CANO not in ss:
        ss[CANO] = seed_fmt
    else:
        # mantém o que já havia no canônico se existir
        if not ss[CANO]:
            ss[CANO] = seed_fmt

    # --------- Garante que o widget mostre o canônico já formatado ---------
    if WKEY not in ss or ss[WKEY] != ss[CANO]:
        ss[WKEY] = ss[CANO]

    # --------- Callback para autoformatar e refletir na tela ---------
    def _cb_processo():
        raw = (st.session_state.get(WKEY) or "").strip()
        fmt = _autoformat_processo(raw) or raw
        # atualiza canônico
        st.session_state[CANO] = fmt
        # atualiza o texto do input visível
        st.session_state[WKEY] = fmt
        # sincroniza URL
        try:
            if fmt:
                qp_set("processo", fmt)
        except Exception:
            pass

    # --------- Input controlado ---------
    st.text_input(
        label,
        key=WKEY,
        placeholder=placeholder,
        on_change=_cb_processo,   # <- atualiza na tela ao confirmar a edição
    )

    # Sincroniza URL também no primeiro render (sem depender do callback)
    try:
        if ss[CANO] and qp_get("processo") != ss[CANO]:
            qp_set("processo", ss[CANO])
    except Exception:
        pass

    return ss[CANO]

# ---------------- Navegação via querystring (API nova: st.query_params) ----------------

def _sync_page_from_query():
    """Sincroniza st.session_state.pagina_atual a partir de ?page=..."""
    page = st.query_params.get("page")  # retorna str ou None
    valid = {"inicio", "analise", "lancamento", "relatorios", "guia"}
    if page in valid and st.session_state.get("pagina_atual") != page:
        st.session_state.pagina_atual = page

def _goto(page: str):
    """Atualiza o router e a URL (?page=...) e dispara page_view (SPA)."""
    st.query_params["page"] = page
    st.session_state.pagina_atual = page

    nomes = {
        "inicio": "Início",
        "analise": "Análise de Item",
        "lancamento": "Lançar por Fonte",
        "relatorios": "Relatórios",
        "guia": "Guia",
    }
    titulo = nomes.get(page, page.title())
    ga_page_view(f"/app?page={page}", titulo)

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

def qp_get(key: str, default: str = "") -> str:
    try:
        return str(st.query_params.get(key) or default)
    except Exception:
        return default

def qp_set(key: str, value: str) -> None:
    try:
        st.query_params[key] = value
    except Exception:
        pass

# URLs (com fallback sensato)
REPO_URL = os.environ.get("APP_REPO_URL", "https://github.com/morenoss/pesquisademercado")

TIPOS_FONTE = [
    "Fornecedor", "Contrato", "Banco de Preços/Comprasnet",
    "Ata de Registro de Preços", "Pesquisa da Internet",
    "Mídia Especializada", "Outros"
]

def rodape_stj():
    st.markdown(
        f"""
        <div class="stj-footer">
          <strong>Projeto desenvolvido pela Secretaria de Administração do STJ.</strong> 
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
    Ordem de preferência:
      1) APP_VERSION ou STREAMLIT_APP_VERSION (CI/Rancher)
      2) (GitLab CI) CI_COMMIT_TAG
      3) (GitLab CI) pré-release a partir de branch + pipeline + sha
      4) arquivo VERSION
      5) git describe --tags
      6) '0.0.0-dev'
    """
    # 1) Vars explícitas (CI ou Rancher Workload)
    v = os.getenv("APP_VERSION") or os.getenv("STREAMLIT_APP_VERSION")
    if v:
        return v.strip()

    # 2) Em ambiente GitLab CI, usar a tag se existir
    if os.getenv("GITLAB_CI") == "true":
        tag = os.getenv("CI_COMMIT_TAG")
        if tag:
            return tag.strip()
        # 3) Sem tag: gera pré-release descritiva com branch + pipeline + sha curto
        ref = os.getenv("CI_COMMIT_REF_NAME", "no-branch")
        ref_norm = re.sub(r"[^0-9A-Za-z_.-]", "-", ref)
        iid = os.getenv("CI_PIPELINE_IID", "")
        sha = os.getenv("CI_COMMIT_SHORT_SHA") or (os.getenv("CI_COMMIT_SHA") or "")[:8]
        suffix = f"{ref_norm}"
        if iid:
            suffix += f".p{iid}"
        if sha:
            suffix += f"+{sha}"
        return f"0.0.0-{suffix}"

    # 4) Arquivo VERSION
    vf = Path("VERSION")
    if vf.exists():
        try:
            return vf.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # 5) git describe (funciona melhor com fetch --tags)
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass

    # 6) Fallback
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

    # --- Uploader à esquerda + Tutoriais à direita (lado a lado) ---
    col_up, col_tut = st.columns([2, 2], gap="large")

    with col_up:
        # REPLACE: uploader da página inicial
        st.subheader("Ou Carregue uma Análise Salva")
        st.caption("⚠️ Por segurança, só carregue arquivos gerados por esta própria aplicação.")
        uploaded_file = st.file_uploader(
            "Carregar pesquisa salva (.zip ou .pkl)",
            type=["zip", "pkl", "bin"],
            label_visibility="collapsed"
        )
        if uploaded_file is not None:
            try:
                loaded_state = _load_state_from_upload(uploaded_file)
                st.session_state.update(loaded_state)
                
                # Normaliza e garante string
                val = str(st.session_state.get("num_processo_pdf_final") or "").strip()
                if not val and st.session_state.get("num_processo_pdf_final_lanc"):
                    val = str(st.session_state["num_processo_pdf_final_lanc"]).strip()

                st.session_state["num_processo_pdf_final"] = val

                # Sincroniza a querystring (permite voltar à mesma análise por URL)
                try:
                    if val:
                        st.query_params["processo"] = val
                except Exception:
                    pass
                
                st.session_state.setdefault("justificativas_por_item", {})

                # Compat: arquivo antigo com a chave de lote
                if st.session_state.get("num_processo_pdf_final_lanc") and not st.session_state.get("num_processo_pdf_final"):
                    st.session_state["num_processo_pdf_final"] = st.session_state["num_processo_pdf_final_lanc"]

                # sincroniza a URL com o nº de processo carregado
                if val and qp_get("processo") != val:
                    qp_set("processo", val)
                
                st.success("Análise carregada. Revise os cards acima e escolha como deseja continuar.")
                sincronizar_para_lote_a_partir_de_analisados(force=False)

                #  refaz o render já com os novos valores nas outras telas
                st.rerun()
                # 
            except Exception as e:
                st.error(f"Erro ao carregar o ficheiro: {e}")


    with col_tut:
        st.subheader("Quer saber mais sobre a ferramenta?")
        st.markdown(
            """
            <div style="padding:14px 16px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;">
            <p style="margin:0 0 8px 0;">Na página <b>Guia</b> você encontra:</p>
            <ul style="margin:0 0 12px 18px;padding:0; color:#374151; font-size:0.95rem; line-height:1.4;">
                <li>Dois vídeos curtos, sobre a importância da pesquisa de mercado e sobre a ferramenta;</li>
                <li>Fluxo geral desta ferramenta e dicas rápidas;</li>
            </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
        # Espaço para não "colar" no card
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.button(
            "Abrir Guia sobre a Ferramenta",
            icon=":material/play_circle:",
            type="secondary",
            use_container_width=True,
            on_click=lambda: _goto("guia"),
        )

   
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
    
    # Pré-preencher justificativa quando estiver editando um item importado/antigo
    if modo_edicao:
        j_existente = (dados_atuais.get("justificativa") or "").strip()
        if j_existente and "justificativa_atual" not in st.session_state:
            st.session_state["justificativa_atual"] = j_existente

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
        
        op_unid = ["— SELECIONE —"] + UNIDADES_PERMITIDAS
        unid_inicial = normalizar_unidade(dados_atuais.get("unidade", ""))
        idx_unid = op_unid.index(unid_inicial) if unid_inicial in op_unid else 0
        item_unidade_sel = cols[1].selectbox("Unidade de Medida (padronizada)", op_unid, index=idx_unid)
        item_unidade = "" if item_unidade_sel == "— SELECIONE —" else item_unidade_sel

        if st.session_state.tipo_analise == "Prorrogação":
            minv = _step_from_casas()
            # pega o salvo (ou 0.0) e garante >= minv
            default_raw = float(dados_atuais.get("valor_unit_contratado", 0.0) or 0.0)
            default_val = max(default_raw, minv)

            item_valor_contratado = cols[2].number_input(
                "Valor Unitário Contratado (R$)",
                min_value=minv,                    # > 0, respeitando as casas
                step=minv,
                format=f"%.{st.session_state.casas_decimais}f",
                value=default_val,                 # nunca abaixo do min
            )

    # ------------------------ Tabela de preços ------------------------
    with st.container(border=True):
        st.subheader("Dados da Pesquisa de Preços")
        tipos_de_fonte_opcoes = TIPOS_FONTE

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
                "PREÇO": st.column_config.NumberColumn(format=f"R$ %.{st.session_state.casas_decimais}f"),
                "LOCALIZADOR SEI": st.column_config.TextColumn(
                help="Informe o nº do documento SEI com 7 dígitos (ex.: 0653878)"),
            },
            use_container_width=True,
            key=f"editor_{st.session_state.edit_item_index}",
        )
        
        # --- Exibir justificativa existente (somente enquanto NÃO houver análise atual) ---
        if modo_edicao and not st.session_state.get("analise_resultados"):
            probs_salvos = list(dados_atuais.get("problemas", []) or [])
            just_padrao = (dados_atuais.get("justificativa") or "").strip()
            if probs_salvos or just_padrao:
                with st.container(border=True):
                    st.subheader("Problemas e justificativa já registrados")
                    if probs_salvos:
                        st.error("Problemas identificados na gravação anterior:")
                        for p in probs_salvos:
                            st.warning(f"- {p}")
                    if just_padrao:
                        # Pré-preenche a justificativa única que será usada após a nova análise
                        st.session_state["justificativa_atual"] = just_padrao
                        st.caption("Justificativa registrada anteriormente (você poderá editá-la após a nova análise):")
                        st.markdown(f"> {just_padrao}")


    # ------------------------ Critérios & análise ------------------------
    with st.container(border=True):
        st.subheader("Critérios e Resultados")
        with st.expander("⚙️ Configurações da análise"):
            st.caption("Regras de arredondamento e casas decimais")
            st.session_state.casas_decimais = st.slider(
                "Casas decimais (0 a 7)", 0, 7, st.session_state.casas_decimais
            )
            st.session_state.usar_nbr5891 = st.checkbox(
                "Arredondar conforme ABNT NBR 5891",
                value=st.session_state.usar_nbr5891
            )
            limiar_elevado = st.slider(
                "Percentual para Preço Excessivamente Elevado (%)", 0, 100,
                int(st.session_state.get("limiar_elevado", 25))
            )
            st.session_state.limiar_elevado = int(limiar_elevado)

            limiar_inexequivel = st.slider(
                "Percentual Mínimo para Preço Inexequível (%)", 0, 100,
                int(st.session_state.get("limiar_inexequivel", 75))
            )
            st.session_state.limiar_inexequivel = int(limiar_inexequivel)

            usar_preco_minimo = st.checkbox(
                "Utilizar PREÇO MÍNIMO como resultado final?",
                value=bool(st.session_state.get("usar_preco_minimo", False))
            )
            st.session_state.usar_preco_minimo = bool(usar_preco_minimo)

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
                    df_com_preco,
                    int(st.session_state.limiar_elevado),
                    int(st.session_state.limiar_inexequivel),
                    casas_decimais=st.session_state.casas_decimais,
                    aplicar_nbr5891=st.session_state.usar_nbr5891
                )

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
            df_vis = df_show.copy()
            df_vis["PREÇO (BR)"] = df_vis["PREÇO"].map(lambda v: formatar_moeda_n(v, st.session_state.casas_decimais) if pd.notna(v) else "")
            st.dataframe(
                df_vis[["EMPRESA/FONTE","TIPO DE FONTE","LOCALIZADOR SEI","PREÇO (BR)","AVALIAÇÃO","OBSERVAÇÃO"]],
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

            # >>> Pré-preencher sem sobrescrever o que o usuário já digitou
            if "justificativa_atual" not in st.session_state:
                # 1) tenta a justificativa que veio do item (edição ou importado)
                base = (dados_atuais.get("justificativa") or "").strip()
                # 2) mantém o que já estiver em session_state, se existir
                st.session_state["justificativa_atual"] = base

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
                st.metric("MÉDIA (válidos)", formatar_moeda_n(resultados.get('media', 0), st.session_state.casas_decimais))
                st.metric("PREÇO MÍNIMO (válido)", formatar_moeda_n(resultados.get('melhor_preco_info', {}).get('PREÇO', 0), st.session_state.casas_decimais))
            with col_res2:
                st.metric("COEFICIENTE DE VARIAÇÃO", f"{resultados.get('coef_variacao', 0):.2f}%")
                st.metric("MÉTODO ESTATÍSTICO", metodo_final)

            st.success(f"**PREÇO DE MERCADO UNITÁRIO: {formatar_moeda_n(preco_mercado_final, st.session_state.casas_decimais)}**")

            # Caixas informativas
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # Mapa de Preços: destacar melhor preço
            if st.session_state.tipo_analise == "Mapa de Preços":
                mp = resultados.get("melhor_preco_info", {})
                fonte = mp.get("EMPRESA/FONTE", "—")
                loc   = mp.get("LOCALIZADOR SEI", "—")
                preco = mp.get("PREÇO", 0.0)
                texto_mp = (
                    f"Melhor preço da pesquisa (após filtros): "
                    f"{formatar_moeda_n(preco, st.session_state.casas_decimais).replace('R$', 'R&#36;&nbsp;')}"
                    f"— <b>Fonte:</b> {fonte} <b>| Localizador SEI:</b> {loc}"
                )

                st.markdown(
                    f"<div style='background:#e8f0fe;padding:12px 14px;border-radius:8px;"
                    f"font-size:1.05rem;font-weight:bold;color:#1a3d8f;margin-bottom:10px;'>{texto_mp}</div>",
                    unsafe_allow_html=True,
                )

            # Prorrogação: comparar contratado x mercado
            if st.session_state.tipo_analise == "Prorrogação":
                # valor contratado a exibir (ajustado às regras)
                if modo_edicao:
                    contratado_salvo = dados_atuais.get("valor_unit_contratado", None)
                    valor_contratado_vis = float(contratado_salvo) if contratado_salvo is not None else float(item_valor_contratado)
                else:
                    valor_contratado_vis = float(item_valor_contratado)

                # aplica arredondamento/decimais ao contratado para comparação
                if st.session_state.usar_nbr5891:
                    valor_contratado_vis = arredonda_nbr5891(valor_contratado_vis, st.session_state.casas_decimais)
                else:
                    valor_contratado_vis = round(valor_contratado_vis, st.session_state.casas_decimais)

                # texto + delta usando o que o usuário escolheu (média/mediana OU mínimo)
                delta = preco_mercado_final - valor_contratado_vis                
                mais_barato_caro = "mais caro" if delta > 0 else ("mais barato" if delta < 0 else "igual")

                texto = (
                    "Comparação (unitário): "
                    f"Mercado = {formatar_moeda_html_n(preco_mercado_final, st.session_state.casas_decimais)} "
                    f"vs Contratado = {formatar_moeda_html_n(valor_contratado_vis, st.session_state.casas_decimais)} "
                    f"→ Mercado está {mais_barato_caro} em {formatar_moeda_html_n(abs(delta), st.session_state.casas_decimais)}."
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
                # Validar SEI das linhas com PREÇO preenchido
               
                erros_sei = []
                for idx_row, row in df_salvar.iterrows():
                    preco = row.get("PREÇO", None)
                    if preco is None or (isinstance(preco, float) and pd.isna(preco)):
                        continue  # só valida SEI quando há preço
                    sei_val = (row.get("LOCALIZADOR SEI", "") or "").strip()
                    msg = validar_sei(sei_val)
                    if msg:
                        fonte_nome = row.get("EMPRESA/FONTE", "—")
                        erros_sei.append(f"Linha {idx_row+1} ({fonte_nome}): {msg}")

                if erros_sei:
                    st.error("Corrija os campos 'LOCALIZADOR SEI' antes de salvar o item:")
                    for e in erros_sei:
                        st.markdown(f"- {e}")
                    st.stop()
                    
                # valida campos obrigatórios do cabeçalho do item
                erros_obrig = []
                if not item_descricao.strip():
                    erros_obrig.append("Preencha o campo **Item Pesquisado (Descrição completa)**.")
                if not item_unidade.strip():
                    erros_obrig.append("Selecione a **Unidade de Medida** padronizada.")

                if erros_obrig:
                    st.error("Corrija os campos obrigatórios do item:")
                    for e in erros_obrig:
                        st.markdown(f"- {e}")
                    st.stop()
                    
                # exigir EMPRESA/FONTE, TIPO DE FONTE e PREÇO > 0 nas linhas com preço
                erros_tabela = []
                for idx_row, row in df_salvar.iterrows():
                    preco = row.get("PREÇO", None)
                    if preco is None or (isinstance(preco, float) and pd.isna(preco)):
                        # sem preço → não exige os demais (linha ignorada no cálculo)
                        continue
                    fonte_nome = (row.get("EMPRESA/FONTE","") or "").strip()
                    tipo_fonte = (row.get("TIPO DE FONTE","") or "").strip()
                    if not fonte_nome:
                        erros_tabela.append(f"Linha {idx_row+1}: informe **EMPRESA/FONTE**.")
                    if not tipo_fonte:
                        erros_tabela.append(f"Linha {idx_row+1}: selecione **TIPO DE FONTE**.")
                    try:
                        if float(preco) <= 0:
                            erros_tabela.append(f"Linha {idx_row+1}: **PREÇO** deve ser maior que zero.")
                    except Exception:
                        erros_tabela.append(f"Linha {idx_row+1}: **PREÇO** inválido.")

                if erros_tabela:
                    st.error("Corrija os dados da tabela antes de salvar o item:")
                    for e in erros_tabela:
                        st.markdown(f"- {e}")
                    st.stop()
                    
                # Use a justificativa digitada pós-análise (preferencial) ou a prévia, se existir
                justificativa_final = (st.session_state.get("justificativa_atual", "")).strip()

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
                    "justificativa": justificativa_final,
                }
                
                    #critérios e preferências usados NESTE item
                registro.update({
                    "limiar_elevado": int(st.session_state.get("limiar_elevado", 25)),
                    "limiar_inexequivel": int(st.session_state.get("limiar_inexequivel", 75)),
                    "usar_preco_minimo": bool(st.session_state.get("usar_preco_minimo", False)),
                    "casas_decimais": int(st.session_state.get("casas_decimais", 2)),
                    "usar_nbr5891": bool(st.session_state.get("usar_nbr5891", True)),
                })

                # PRORROGAÇÃO
                if st.session_state.tipo_analise == "Prorrogação":
                    valor_unit_contratado_raw = float(item_valor_contratado)
                    if st.session_state.usar_nbr5891:
                        valor_unit_contratado = arredonda_nbr5891(valor_unit_contratado_raw, st.session_state.casas_decimais)
                    else:
                        valor_unit_contratado = round(valor_unit_contratado_raw, st.session_state.casas_decimais)

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
                # Mantém o fluxo "por fonte" em sincronia imediatamente
                sincronizar_para_lote_a_partir_de_analisados(force=True)
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
                        f"<small>Valor Unitário (mercado): {formatar_moeda_n(item.get('valor_unit_mercado', 0), st.session_state.casas_decimais)}</small>",
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
            state_to_save = _make_export_state()

            zip_bytes = _zip_bytes_with_pkl(state_to_save)
            st.download_button(
                label="💾 Exportar Pesquisa (ZIP)",
                data=zip_bytes,
                file_name="pesquisa_mercado_salva.zip",
                mime="application/zip",
                use_container_width=True,
                type="primary",
            )
           
        with exp_cols[1]:
            st.markdown("**Gerar Relatório Final em PDF**")
            num_processo_pdf = input_num_processo("Nº do Processo (para PDF)")

            if not st.session_state.itens_analisados:
                st.info("Adicione itens para gerar o PDF.")
            else:
                if not (num_processo_pdf or "").strip():
                    st.warning("Informe o nº do processo.")
                else:
                    erro_proc = validar_processo(num_processo_pdf)
                    if erro_proc:
                        st.error(erro_proc)
                    else:
                        set_decimal_places(int(st.session_state.get("casas_decimais", 2)))
                        pdf_bytes = criar_pdf_completo(
                            st.session_state.itens_analisados,
                            num_processo_pdf,
                            st.session_state.tipo_analise,
                            limiar_elevado = int(st.session_state.get("limiar_elevado", 25)),
                            limiar_inexequivel = int(st.session_state.get("limiar_inexequivel", 75)),
                            usar_preco_minimo = bool(st.session_state.get("usar_preco_minimo", False)),
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
    input_num_processo("Número do Processo (para visualização)")
    num_processo = st.session_state.get("num_processo_pdf_final", "")

    if not num_processo:
        st.info("Informe um número de processo para visualizar os relatórios.")
        return

    erro_proc = validar_processo(num_processo or "")
    if erro_proc:
        st.error(erro_proc)
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
    # Garante que itens/fontes/propostas existam mesmo que o usuário
    # tenha começado pela Análise de Item ou importado um PKL antigo.
    sincronizar_para_lote_a_partir_de_analisados(force=False)

    # === Configurações globais (defina ANTES de lançar preços) ===
    
    tabs = st.tabs([
        "1) Itens",
        "2) Fontes",
        "3) Lançar Preços por Fonte",
        "4) Consolidar em Itens Analisados"
    ])

    with st.expander("⚙️ Configurações da análise (aplicadas ao lote todo)", expanded=False):
        c1, c2, c3 = st.columns(3)

        # Casas decimais + ABNT (persistem no session_state)
        st.session_state.casas_decimais = c1.slider(
            "Casas decimais (0 a 7)", 0, 7,
            st.session_state.get("casas_decimais", 2),
            key="casas_decimais_lote"
        )
        st.session_state.usar_nbr5891 = c2.checkbox(
            "Arredondar conforme ABNT NBR 5891",
            value=st.session_state.get("usar_nbr5891", True),
            key="usar_nbr5891_lote"
        )

        # Critérios de análise (limiares) e modo de resultado final
        st.session_state.limiar_elevado = c1.slider(
            "Excessivo > (%) da média", 0, 100,
            st.session_state.get("limiar_elevado", 25),
            key="limiar_elevado_lote"
        )
        st.session_state.limiar_inexequivel = c2.slider(
            "Inexequível < (%) da média", 0, 100,
            st.session_state.get("limiar_inexequivel", 75),
            key="limiar_inexequivel_lote"
        )
        st.session_state.usar_preco_minimo = c3.checkbox(
            "Usar PREÇO MÍNIMO como resultado final?",
            value=st.session_state.get("usar_preco_minimo", False),
            key="usar_preco_minimo_lote"
        )

        st.caption(
            "Defina isto antes de lançar preços. Alterar aqui não apaga o que já foi salvo, "
            "mas pode limpar edições **não salvas** abertas na aba 3."
        )

    # ---------------------- TAB 1: ITENS ----------------------
    with tabs[0]:
        st.markdown("Cadastre os **itens e quantidades** primeiro.")

        df_itens = pd.DataFrame(
            st.session_state.itens or [],
            columns=["id", "descricao", "unidade", "quantidade", "valor_unit_contratado"]
        )

        if df_itens.empty:
            df_itens = pd.DataFrame(
                columns=["id", "descricao", "unidade", "quantidade", "valor_unit_contratado"]
            )

        cols_map = {
            "descricao": "DESCRIÇÃO",
            "unidade": "UNIDADE",
            "quantidade": "QUANTIDADE",
        }

        if st.session_state.tipo_analise == "Prorrogação":
            cols_map["valor_unit_contratado"] = "VALOR UNIT. CONTRATADO"

        df_show = df_itens.rename(columns=cols_map)

        ordem = ["DESCRIÇÃO", "UNIDADE", "QUANTIDADE"] + (
            ["VALOR UNIT. CONTRATADO"] if "VALOR UNIT. CONTRATADO" in df_show.columns else []
        )
        df_show = df_show[ordem]

        column_cfg = {
            "QUANTIDADE": st.column_config.NumberColumn(min_value=1, step=1)
        }

        # tornar UNIDADE um select padronizado
        column_cfg["UNIDADE"] = st.column_config.SelectboxColumn(
            options=UNIDADES_PERMITIDAS,
            help="Selecione a unidade padronizada definida pela área técnica."
        )

        if "VALOR UNIT. CONTRATADO" in df_show.columns:
            column_cfg["VALOR UNIT. CONTRATADO"] = st.column_config.NumberColumn(
                format=f"R$ %.{st.session_state.casas_decimais}f",
                min_value=0.0
            )

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
                return (
                    (s is None)
                    or (isinstance(s, float) and pd.isna(s))
                    or (str(s).strip() == "")
                )
                
            for i in range(len(edited)):
                desc = edited.iloc[i].get("DESCRIÇÃO", "")
                unid_bruta = edited.iloc[i].get("UNIDADE", "")
                qtde = edited.iloc[i].get("QUANTIDADE", None)

                # linha totalmente vazia → ignora
                if (
                    _blank(desc)
                    and _blank(unid_bruta)
                    and _blank(qtde)
                    and (
                        "VALOR UNIT. CONTRATADO" not in edited.columns
                        or _blank(edited.iloc[i].get("VALOR UNIT. CONTRATADO", None))
                    )
                ):
                    continue

                # valida obrigatórios (descrição, unidade, quantidade)
                if _blank(desc):
                    erros.append(f"Linha {i+1}: preencha **DESCRIÇÃO**.")
                unid_norm = normalizar_unidade(unid_bruta)
                if not unid_norm:
                    erros.append(f"Linha {i+1}: selecione uma **UNIDADE** válida.")
                if (qtde is None) or pd.isna(qtde) or int(qtde) < 1:
                    erros.append(f"Linha {i+1}: **QUANTIDADE** deve ser >= 1.")

                valor_contr = 0.0
                if (
                    st.session_state.tipo_analise == "Prorrogação"
                    and "VALOR UNIT. CONTRATADO" in edited.columns
                ):
                    v = edited.iloc[i].get("VALOR UNIT. CONTRATADO", None)
                    if v is None or (isinstance(v, float) and pd.isna(v)) or float(v) <= 0:
                        erros.append(f"Linha {i+1}: **VALOR UNIT. CONTRATADO** deve ser > 0.")
                    else:
                        valor_contr = float(v)

                old_id = df_itens["id"].iloc[i] if (i < len(df_itens)) else None

                item = {
                    "id": old_id
                    if (old_id is not None and not (isinstance(old_id, float) and pd.isna(old_id)))
                    else novo_id("item"),
                    "descricao": str(desc or "").strip(),
                    "unidade": unid_norm,  # 👈 padronizado
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
                st.session_state.propostas = [
                    p for p in st.session_state.propostas if p.get("item_id") in ids_validos
                ]
                st.session_state.itens = novos
                st.success("Itens salvos.")
 
    # ---------------------- TAB 2: FONTES ----------------------
    with tabs[1]:
        st.markdown("Cadastre aqui as **fontes/fornecedores**.")
        tipos_de_fonte_opcoes = TIPOS_FONTE

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

            # Índice como “Nº” 1..n
            df_lanc = df_lanc.reset_index(drop=True)
            df_lanc.index = pd.RangeIndex(start=1, stop=len(df_lanc) + 1, name="Nº")

            with st.form(f"form_precos_{fonte_id}"):
                edited = st.data_editor(
                    df_lanc[["ITEM", "UNID.", "QUANT.", "PREÇO UNIT.", "LOCALIZADOR SEI"]],
                    num_rows="fixed",
                    use_container_width=True,
                    column_config={
                        "ITEM":  st.column_config.TextColumn(disabled=True),
                        "UNID.": st.column_config.TextColumn(disabled=True),
                        "QUANT.": st.column_config.NumberColumn(disabled=True),
                        "PREÇO UNIT.": st.column_config.NumberColumn(
                            format=f"R$ %.{st.session_state.casas_decimais}f", min_value=0.0
                        ),
                        "LOCALIZADOR SEI": st.column_config.TextColumn(
                            help="Obrigatório quando houver preço. Deve ter exatamente 7 dígitos (ex.: 0653878)."
                        ),
                    },
                    hide_index=False,  # 👈 mostra o “Nº” automático
                    key=f"editor_precos_{fonte_id}",
                )

                submitted = st.form_submit_button("Salvar Preços desta Fonte")
                if submitted:
                    erros = []
                    novas_propostas = []

                    for i, it in enumerate(st.session_state.itens):
                        preco = edited.iloc[i]["PREÇO UNIT."]
                        sei   = (edited.iloc[i]["LOCALIZADOR SEI"] or "").strip()

                        if preco is None or (isinstance(preco, float) and pd.isna(preco)):
                            # remover proposta existente
                            st.session_state.propostas = [
                                p for p in st.session_state.propostas
                                if not (p["item_id"] == it["id"] and p["fonte_id"] == fonte_id)
                            ]
                            continue

                        msg = validar_sei(sei)
                        if msg:
                            erros.append(f"Item '{it['descricao']}': {msg}")
                            continue

                        novas_propostas.append({
                            "item_id": it["id"],
                            "fonte_id": fonte_id,
                            "preco": float(preco),
                            "sei": sei,
                        })

                    if erros:
                        st.error("Não foi possível salvar os preços desta fonte:")
                        for e in erros:
                            st.markdown(f"- {e}")
                    else:
                        st.session_state.propostas = [
                            p for p in st.session_state.propostas if p["fonte_id"] != fonte_id
                        ] + novas_propostas
                        ga_event('salvar_precos_fonte', {
                            'tela': 'lancamento_por_fonte',
                            'fonte_nome': fonte_nome,
                            'qtd_itens': int(len(st.session_state.itens)),
                        })
                        st.success("Preços salvos para esta fonte.")
 
    # ------------- TAB 4: CONSOLIDAR EM ITENS ANALISADOS -------------
    with tabs[3]:
        st.markdown("Gere os **itens analisados** automaticamente a partir das propostas lançadas.")

        # Use os valores definidos no topo
        limiar_elevado     = int(st.session_state.get("limiar_elevado", 25))
        limiar_inexequivel = int(st.session_state.get("limiar_inexequivel", 75))
        usar_preco_minimo  = bool(st.session_state.get("usar_preco_minimo", False))
        casas_decimais     = int(st.session_state.get("casas_decimais", 2))
        aplicar_nbr        = bool(st.session_state.get("usar_nbr5891", True))

        st.caption(
            f"Critérios: Excessivo > {limiar_elevado}% | Inexequível < {limiar_inexequivel}% | "
            f"Resultado final: {'PREÇO MÍNIMO' if usar_preco_minimo else 'método estatístico'} • "
            f"Arredondamento: {'ABNT NBR 5891' if aplicar_nbr else 'round padrão'} • "
            f"Casas decimais: {casas_decimais}"
        )
        

        substituir = st.checkbox("Substituir os itens já existentes no relatório", value=True)

        # --- ETAPA 1: Gerar PRÉVIA (não grava ainda) ---
        if st.button("Gerar PRÉVIA"):
            fontes_by_id = {f["id"]: f for f in st.session_state.fontes}
            buffer = []

            for idx_item, it in enumerate(st.session_state.itens, start=1):
                # Junte as propostas desse item a partir de st.session_state.propostas
                registros = []
                for p in st.session_state.propostas:
                    if p.get("item_id") != it["id"]:
                        continue
                    fonte = fontes_by_id.get(p["fonte_id"], {"nome": "—", "tipo": "Fornecedor"})
                    registros.append({
                        "EMPRESA/FONTE": fonte.get("nome", "—"),
                        "TIPO DE FONTE": fonte.get("tipo", "Fornecedor"),
                        "LOCALIZADOR SEI": p.get("sei", ""),
                        "PREÇO": float(p.get("preco", 0.0) or 0.0),
                    })

                df_precos = pd.DataFrame(registros)

                # Sem preços válidos? Pule para o próximo item
                if df_precos.empty or df_precos["PREÇO"].dropna().empty:
                    continue

                df_precos = (
                    df_precos
                    .sort_values(by="PREÇO", ascending=True, na_position="last")
                    .reset_index(drop=True)
                )

                # Calcula estatística
                resultados = calcular_preco_mercado(
                    df_precos,
                    limiar_elevado, limiar_inexequivel,
                    casas_decimais=casas_decimais,
                    aplicar_nbr5891=aplicar_nbr
                )

                preco_merc = float(resultados.get("preco_mercado_calculado", 0.0))
                metodo     = resultados.get("metodo_sugerido", "N/A")

                # Normaliza melhor preço
                melhor_raw = resultados.get("melhor_preco_info", None)
                if isinstance(melhor_raw, pd.Series):
                    melhor = melhor_raw.to_dict()
                elif isinstance(melhor_raw, dict):
                    melhor = melhor_raw
                else:
                    melhor = {}
                melhor_unit = float(melhor.get("PREÇO", 0.0))

                # Resultado final (mínimo opcional)
                preco_final = melhor_unit if usar_preco_minimo else preco_merc

                # ---- SÓ AGORA montamos 'registro' ----
                registro = {
                    "item_num": 0,  # será renumerado na confirmação
                    "descricao": it["descricao"].strip(),
                    "unidade": it["unidade"].strip(),
                    "quantidade": int(it["quantidade"]),
                    "metodo_final": "PREÇO MÍNIMO" if usar_preco_minimo else metodo,
                    "valor_unit_mercado": float(preco_final),
                    "valor_total_mercado": float(preco_final) * int(it["quantidade"]),
                    "df_original": df_precos.to_dict("records"),  # já ordenado
                    "problemas": resultados.get("problemas", []),
                    "justificativa": "",
                }

                # Campos específicos por modo
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

                # ---- Linha de PRÉVIA (inclui Nº agora que 'registro' existe) ----
                linha_preview = {
                    "Nº": idx_item,
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

                # ---- Append único no buffer (com item_num salvo) ----
                buffer.append({
                    "item_uid": it["id"],
                    "item_num": idx_item,             # ➜ usado no título do expander
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
            prev_vis = prev_df.copy()
            for c in [
                "VALOR UNIT. MERCADO","VALOR TOTAL MERCADO","VALOR UNIT. MELHOR","VALOR TOTAL MELHOR",
                "VALOR UNIT. CONTRATADO","VALOR TOTAL CONTRATADO"
            ]:
                if c in prev_vis.columns:
                    if c.startswith("VALOR UNIT."):
                        prev_vis[c + " (BR)"] = prev_vis[c].map(
                            lambda v: formatar_moeda_n(v, st.session_state.casas_decimais) if pd.notna(v) else ""
                        )
                    else:
                        prev_vis[c + " (BR)"] = prev_vis[c].map(
                            lambda v: formatar_moeda(v) if pd.notna(v) else ""
                        )
            cols_vis_base = ["Nº", "DESCRIÇÃO", "UNID.", "QTD.", "MÉTODO", "DADOS DA PROPOSTA"]
            cols_val = [c for c in prev_vis.columns if c.endswith("(BR)")]
            cols_extras = [c for c in prev_vis.columns if c in cols_vis_base]
            cols_vis = [c for c in cols_vis_base if c in cols_extras] + cols_val  # “Nº” primeiro
            st.dataframe(prev_vis[cols_vis], use_container_width=True, hide_index=True)       

            # Campos de justificativa por item PROBLEMÁTICO
            st.markdown("----")
            st.markdown("**Justificativas obrigatórias para itens com problemas:**")
            faltantes = []
            for b in buffer:
                probs = b.get("problemas", []) or []
                if not probs:
                    continue
                num = b.get("item_num", 0)
                titulo = f"Item {num}: {b['descricao']} — {len(probs)} problema(s)"
                with st.expander(titulo):
                    for p in probs:
                        st.warning(f"- {p}")

                    key = f"just_{b['item_uid']}"

                    # Pré-preenche a caixa de justificativa
                    if key not in st.session_state:
                        # 1) tenta do dicionário persistente (se já existir em sessão/export)
                        padrao = (st.session_state.get("justificativas_por_item", {}) or {}).get(b["item_uid"], "")
                        # 2) fallback: busca em itens_analisados pelo orig_item_id (se já consolidou antes)
                        if not padrao:
                            for it in st.session_state.get("itens_analisados", []):
                                if it.get("orig_item_id") == b["item_uid"]:
                                    padrao = it.get("justificativa", "") or ""
                                    break
                        st.session_state[key] = padrao  # deixa o text_area já preenchido

                    st.text_area(
                        "Justificativa",
                        key=key,
                        placeholder="Descreva as tratativas, diligências, validações etc.",
                        height=130
                    )

            # Botões de ação
            c1, c2 = st.columns([1, 1])
            
            if c1.button("Confirmar consolidação no relatório", type="primary"):
                # 1) Validar justificativas obrigatórias
                faltantes = []
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
                    # 2) Aplicar ao relatório (com opção de substituir)
                    if substituir:
                        st.session_state.itens_analisados = []

                    for b in buffer:
                        reg = dict(b["registro"])
                        reg["justificativa"] = (st.session_state.get(f"just_{b['item_uid']}", "") or "").strip()
                        reg["orig_item_id"] = b["item_uid"]

                        # Persistir para as próximas PRÉVIAS
                        st.session_state.setdefault("justificativas_por_item", {})[b["item_uid"]] = reg["justificativa"]

                        st.session_state.itens_analisados.append(reg)

                    # 3) Renumerar e finalizar
                    for i, item in enumerate(st.session_state.itens_analisados):
                        item["item_num"] = i + 1

                    st.success(f"{len(buffer)} item(ns) consolidados no relatório.")
                    ga_event('confirmar_consolidacao', {
                        'tela': 'lancamento_por_fonte',
                        'itens_consolidados': int(len(buffer)),
                        'substituir_existentes': bool(substituir),
                    })
                    st.dataframe(prev_vis[cols_vis], use_container_width=True, hide_index=True)
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

                # 1) Exportar .pkl dentro do .zip com todo o estado
                with exp_cols[0]:
                    st.markdown("**Salvar Análise Atual**")
                    state_to_save = _make_export_state()

                    zip_bytes = _zip_bytes_with_pkl(state_to_save)
                    st.download_button(
                        label="💾 Exportar Pesquisa (ZIP)",
                        data=zip_bytes,
                        file_name="pesquisa_mercado_salva.zip",
                        mime="application/zip",
                        use_container_width=True,
                        type="primary",
                    )

                # 2) Gerar PDF completo
                with exp_cols[1]:
                    st.markdown("**Gerar Relatório Final em PDF**")
                    num_processo_pdf = input_num_processo("Nº do Processo (para PDF)")
                    if not st.session_state.itens_analisados:
                        st.info("Consolide itens no relatório (acima) para gerar o PDF.")
                    else:
                        if not (num_processo_pdf or "").strip():
                            st.warning("Informe o nº do processo.")
                        else:
                            erro_proc = validar_processo(num_processo_pdf)
                            if erro_proc:
                                st.error(erro_proc)
                            else:
                                set_decimal_places(int(st.session_state.get("casas_decimais", 2)))
                                pdf_bytes = criar_pdf_completo(
                                    st.session_state.itens_analisados,
                                    num_processo_pdf,
                                    st.session_state.tipo_analise,
                                    limiar_elevado = int(st.session_state.get("limiar_elevado", 25)),
                                    limiar_inexequivel = int(st.session_state.get("limiar_inexequivel", 75)),
                                    usar_preco_minimo = bool(st.session_state.get("usar_preco_minimo", False)),
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
    
    st.markdown("---")
    st.subheader("📹 Tutoriais em vídeo")

    # Para deixar a Guia leve: só carrega os players quando o usuário pedir
    mostrar = st.toggle("Carregar vídeos", value=False)
    if mostrar:
        col_v1, col_v2 = st.columns(2, gap="large")

        with col_v1:
            st.markdown("**Acertando o Preço (~6 min)**")
            render_small_video(
                title="",
                candidates=[
                    "/mnt/data/Acertando_o_Preço.mp4",
                    "assets/Acertando_o_Preço.mp4",
                ],
                width_px=600,
            )

        with col_v2:
            st.markdown("**Ferramenta de Pesquisa do STJ (~6 min)**")
            render_small_video(
                title="",
                candidates=[
                    "/mnt/data/Ferramenta_de_Pesquisa_do_STJ.mp4",
                    "assets/Ferramenta_de_Pesquisa_do_STJ.mp4",
                ],
                width_px=600,
            )

        # (opcional) Telemetria: registrar que os tutoriais foram carregados
        ga_event('gui_videos_carregados', {'tela': 'guia'})

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
- **Exportar** (menu “Opções da Pesquisa Completa”): salva todo o trabalho em um **.zip** (contém um .pkl interno).
- **Importar** (página inicial): carregue um **.zip** (recomendado). Também aceitamos **.pkl** (legado) e arquivos antigos que chegaram como **.bin**.

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
- **Manual STJ**: acesse o *[Manual de Pesquisa de Preços do STJ](https://www.stj.jus.br/publicacaoinstitucional/index.php/MOP/issue/archive).* para as regras normativas.
""")

# ============================== Bootstrap / Router ==============================

carregar_estilo()
_sync_page_from_query()  # garante que ?page=... reflita na navegação
nav_lateral()
breadcrumb_topo()
_ensure_ga_bootstrap()

# --- Debounce do primeiro page_view (SPA) ---
if "ga_pv_sent" not in st.session_state:
    st.session_state.ga_pv_sent = False

_nomes_pag = {
    "inicio": "Início",
    "analise": "Análise de Item",
    "lancamento": "Lançar por Fonte",
    "relatorios": "Relatórios",
    "guia": "Guia",
}
if not st.session_state.ga_pv_sent:
    _pag_key = st.session_state.get("pagina_atual", "inicio")
    ga_page_view(f"/app?page={_pag_key}", _nomes_pag.get(_pag_key, "Tela"))
    st.session_state.ga_pv_sent = True

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

rodape_stj()
