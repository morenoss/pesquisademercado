# gerador_pdf.py — robusto + análises por modo (Prorrogação / Mapa) + retorno bytes

from fpdf import FPDF
from datetime import datetime
import pandas as pd
import re

# -------------------- utilitários --------------------
def br_currency(valor: float) -> str:
    try:
        v = float(valor)
    except Exception:
        v = 0.0
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def sanitize(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt)
    # evita erros de fonte/encoding (use somente ASCII)
    txt = txt.replace("–", "-").replace("—", "-").replace("…", "...")
    # tira HTML e estilos
    txt = re.sub(r"<[^>]+>", "", txt)
    # colapsa espaços
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

# -------------------- classe PDF --------------------
class PDF(FPDF):
    def __init__(self, num_processo, tipo_analise, *args, **kwargs):
        super().__init__(orientation="L", format="A4", *args, **kwargs)
        self.alias_nb_pages()
        self.set_auto_page_break(False)  # controlamos manualmente
        self.num_processo = sanitize(num_processo)
        self.tipo_analise = sanitize(tipo_analise)

        # Cores institucionais (azul STJ-ish)
        self.color_blue = (0, 65, 100)
        self.fill_gray = (235, 235, 235)
        self.fill_green = (204, 255, 204)

        # Métricas e margens
        self.line_h = 6
        self.l_margin = 10
        self.r_margin = 10
        self.t_margin = 12
        self.b_margin = 12
        self.set_margins(self.l_margin, self.t_margin, self.r_margin)

        # Estado da tabela (para repetir cabeçalho)
        self._current_table = None  # (headers, widths, aligns, font_size)

    # -------- header/footer --------
    def header(self):
        # ---- layout base
        AZUL = (0, 65, 100)        # #004164
        CINZA_LINHA = (210, 210, 210)

        img_w = 18                 # largura do brasão
        y_img = self.t_margin
        x_img = self.l_margin
        gap   = 4                  # espaço entre brasão e texto

        # brasão
        try:
            self.image("assets/marca_stj_brasao_cor_vert_compacta.png", x=x_img, y=y_img, w=img_w)
        except Exception:
            pass

        # título alinhado verticalmente um pouquinho abaixo do topo do brasão
        x_text = x_img + img_w + gap
        y_text = y_img + 2          # ↓ desce o título 2pt (cola no brasão sem “subir” demais)
        self.set_xy(x_text, y_text)

        self.set_text_color(*AZUL)
        self.set_font("Helvetica", "B", 15)
        self.cell(0, 8, "RELATORIO DE PESQUISA DE MERCADO", ln=1)

        # subtítulo (Processo | Tipo) logo abaixo do título
        self.set_x(x_text)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, f"Processo: {self.num_processo}  |  Tipo de Analise: {self.tipo_analise}", ln=1)

        # posiciona a linha divisória logo abaixo do ponto mais baixo (brasão ou subtítulo)
        y_base = max(self.get_y(), y_img + 20)   # 20 ~ altura visual do brasão usado
        self.set_y(y_base + 0.5)                   # 0.5 pt de respiro antes da linha

        self.set_draw_color(*CINZA_LINHA)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)                               # 3pt de respiro DEPOIS da linha
        self.set_text_color(0, 0, 0)             # restaura

  
    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        # à esquerda: data/hora
        self.cell(0, 5, f"Gerado em: {now}", ln=0)
        # à direita: paginação
        self.set_y(-14)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}}", align="R")


    # -------- helpers de quebra/altura --------
    @property
    def usable_w(self):
        return self.w - self.l_margin - self.r_margin

    def split_lines(self, w, text):
        return self.multi_cell(w, self.line_h, sanitize(text), split_only=True)

    def row_height(self, widths, row_texts):
        max_lines = 1
        for w, txt in zip(widths, row_texts):
            lines = self.split_lines(w, txt)
            max_lines = max(max_lines, max(1, len(lines)))
        return max_lines * self.line_h

    def ensure_space(self, h, redraw_header=False):
        if self.get_y() + h > (self.h - self.b_margin):
            self.add_page()
            if redraw_header and self._current_table:
                headers, widths, aligns, font_size = self._current_table
                self.table_header(headers, widths, aligns, font_size)

    # -------- tabela com cabeçalho “sticky” --------
    def table_header(self, headers, widths, aligns, font_size=8):
        self.set_font("Helvetica", "B", font_size)
        self.set_fill_color(*self.fill_gray)
        x0, y0 = self.get_x(), self.get_y()
        h = self.row_height(widths, headers)
        self.ensure_space(h, redraw_header=False)
        for w, text, al in zip(widths, headers, aligns):
            x, y = self.get_x(), self.get_y()
            self.rect(x, y, w, h)
            self.multi_cell(w, self.line_h, sanitize(text), border=0, align=al)
            self.set_xy(x + w, y)
        self.set_xy(x0, y0 + h)

    def table_rows(self, rows, widths, aligns, font_size=8):
        self.set_font("Helvetica", "", font_size)
        for row in rows:
            h = self.row_height(widths, row)
            self.ensure_space(h, redraw_header=True)
            x0, y0 = self.get_x(), self.get_y()
            for w, text, al in zip(widths, row, aligns):
                x, y = self.get_x(), self.get_y()
                self.rect(x, y, w, h)
                self.multi_cell(w, self.line_h, sanitize(text), border=0, align=al)
                self.set_xy(x + w, y)
            self.set_xy(x0, y0 + h)

    def start_table(self, headers, widths, aligns, font_size=8):
        self._current_table = (headers, widths, aligns, font_size)
        self.table_header(headers, widths, aligns, font_size)

# -------------------- páginas --------------------
def pagina_consolidada(pdf: PDF, itens_analisados, tipo_analise):
    pdf.add_page()

    # ---------- BANNER COMPARATIVO (cores STJ) ----------
    texto_banner = ""
    if tipo_analise == "Prorrogacao":
        total_m = sum(i.get("valor_total_mercado", 0) for i in itens_analisados)
        total_c = sum(i.get("valor_total_contratado", 0) for i in itens_analisados)
        diff = total_c - total_m
        sentido = "MAIS CARO" if diff > 0 else ("MAIS BARATO" if diff < 0 else "IGUAL")
        texto_banner = (
            f"VALOR TOTAL OBTIDO NA PESQUISA DE MERCADO: R$ {br_currency(total_m)} | "
            f"VALOR TOTAL CONTRATADO: R$ {br_currency(total_c)} | "
            f"DIFERENCA: R$ {br_currency(abs(diff))} - {sentido}"
        )
    elif tipo_analise == "Mapa de Precos":
        total_m = sum(i.get("valor_total_mercado", 0) for i in itens_analisados)
        total_best = sum(i.get("valor_total_melhor_preco", 0) for i in itens_analisados)
        diff = total_m - total_best
        sentido = "MAIS BARATO" if diff > 0 else ("MAIS CARO" if diff < 0 else "IGUAL")
        texto_banner = (
            f"VALOR TOTAL OBTIDO NA PESQUISA DE MERCADO: R$ {br_currency(total_m)} | "
            f"VALOR TOTAL DOS MELHORES PRECOS: R$ {br_currency(total_best)} | "
            f"DIFERENCA: R$ {br_currency(abs(diff))} - {sentido}"
        )

    # um respiro depois do cabeçalho
    pdf.ln(4)

    if texto_banner:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(0, 65, 100)     # azul STJ
        pdf.set_fill_color(230, 230, 230)  # cinza médio (um pouco mais escuro)
        pdf.set_draw_color(200, 200, 200)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.usable_w, 7, sanitize(texto_banner), border=1, align="C", fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    # ---------- TÍTULO DO QUADRO ----------
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "QUADRO RESUMO CONSOLIDADO", ln=1, align="C")
    pdf.ln(1)

    # ---------- TABELAS POR TIPO ----------
    if tipo_analise == "Prorrogacao":
        # ITEM | DESCRICAO | VU(M) | VT(M) | VU(C) | VT(C) | AVAL
        widths  = [14, 85, 30, 34, 30, 34, 50]   # soma = 277
        headers = ["ITEM", "DESCRICAO", "V.UNIT(MERC)", "V.TOTAL(MERC)",
                   "V.UNIT(CONTR)", "V.TOTAL(CONTR)", "AVALIACAO"]
        aligns  = ["C", "L", "R", "R", "R", "R", "C"]

        pdf.start_table(headers, widths, aligns, font_size=8)
        rows = []
        for it in itens_analisados:
            rows.append([
                str(it.get("item_num", "")),
                sanitize(it.get("descricao", "")),
                "R$ " + br_currency(it.get("valor_unit_mercado", 0)),
                "R$ " + br_currency(it.get("valor_total_mercado", 0)),
                "R$ " + br_currency(it.get("valor_unit_contratado", 0)),
                "R$ " + br_currency(it.get("valor_total_contratado", 0)),
                sanitize(it.get("avaliacao_preco_contratado", "")),
            ])
        pdf.table_rows(rows, widths, aligns, font_size=8)

    elif tipo_analise == "Mapa de Precos":
        # ITEM | DESCRICAO | METODO | VU(MERC) | VT(MERC) | VU(MELHOR) | VT(MELHOR) | DADOS
        widths  = [14, 70, 28, 28, 32, 28, 32, 50]   # soma = 277
        headers = ["ITEM", "DESCRICAO", "METODO",
                   "VU(MERC)", "VT(MERC)", "VU(MELHOR)", "VT(MELHOR)", "DADOS"]
        aligns  = ["C", "L", "C", "R", "R", "R", "R", "L"]

        pdf.start_table(headers, widths, aligns, font_size=8)
        rows = []
        for it in itens_analisados:
            rows.append([
                str(it.get("item_num", "")),
                sanitize(it.get("descricao", "")),
                sanitize(it.get("metodo_final", "")),
                "R$ " + br_currency(it.get("valor_unit_mercado", 0)),
                "R$ " + br_currency(it.get("valor_total_mercado", 0)),
                "R$ " + br_currency(it.get("valor_unit_melhor_preco", 0)),
                "R$ " + br_currency(it.get("valor_total_melhor_preco", 0)),
                sanitize(it.get("dados_melhor_proposta", "")),
            ])
        pdf.table_rows(rows, widths, aligns, font_size=8)

    else:
        # Pesquisa Padrão — ITEM | DESCRICAO | QTD | UNID. | V.UNIT(MERC) | V.TOTAL(MERC) | OBS
        widths  = [14, 120, 20, 25, 30, 30, 38]     # soma = 277
        headers = ["ITEM", "DESCRICAO", "QTD", "UNID.",
                   "V.UNIT(MERC)", "V.TOTAL(MERC)", "OBS"]
        aligns  = ["C", "L", "C", "C", "R", "R", "L"]

        pdf.start_table(headers, widths, aligns, font_size=8)
        rows = []
        for it in itens_analisados:
            rows.append([
                str(it.get("item_num", "")),
                sanitize(it.get("descricao", "")),
                str(it.get("quantidade", "")),
                sanitize(it.get("unidade", "")),
                "R$ " + br_currency(it.get("valor_unit_mercado", 0)),
                "R$ " + br_currency(it.get("valor_total_mercado", 0)),
                "",
            ])
        pdf.table_rows(rows, widths, aligns, font_size=8)


def pagina_analise_item(pdf: PDF, item_info, analise):
    pdf.add_page()

    # --- Título ---
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"ANALISE DETALHADA - ITEM {item_info.get('item_num','')}", ln=1)
    pdf.ln(1)

    # --- Descrição (rótulo à esquerda + texto que quebra corretamente) ---
    desc = sanitize(item_info.get("descricao", "N/A"))
    pdf.set_font("Helvetica", "B", 10)
    label_w = 28  # largura do rótulo "Descricao:"
    pdf.cell(label_w, 6, "Descricao:", ln=0)               # não quebra linha aqui
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(pdf.usable_w - label_w, 6, desc)        # quebra e mantém dentro da margem

    # --- Quantidade e Unidade em uma linha logo abaixo da descrição ---
    pdf.set_x(pdf.l_margin)                                # garante retorno à margem esquerda
    q = item_info.get("quantidade", "N/A")
    u = sanitize(item_info.get("unidade", "N/A"))
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5.5, f"Quantidade: {q}    |    Unidade: {u}")  # ocupa a largura útil
    # (se quiser cada um em uma linha, troque por dois multi_cell(0, 5.5, ...))

    # Se for prorrogação, mostra o valor contratado logo abaixo
    if pdf.tipo_analise == "Prorrogacao" and (item_info.get("valor_unit_contratado", 0) or 0) > 0:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5.5, "Valor Unitario Contratado: R$ " + br_currency(item_info["valor_unit_contratado"]))

    pdf.ln(2)  # respiro pequeno antes da tabela

    # Tabela de fontes (avaliacao)
    df = analise.get("df_avaliado", pd.DataFrame())
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Avaliacao Detalhada dos Precos", ln=1)

    widths = [80, 28, 25, 40, 94]  # soma = 267
    headers = ["FONTE", "SEI", "PRECO", "AVALIACAO", "OBSERVACAO"]
    aligns  = ["L", "C", "R", "C", "L"]
    pdf.start_table(headers, widths, aligns, font_size=8)

    if not df.empty:
        col_fonte = df.get("EMPRESA/FONTE", pd.Series([""] * len(df))).astype(str)
        col_sei   = df.get("LOCALIZADOR SEI", pd.Series([""] * len(df))).astype(str)
        col_preco = df.get("PRECO", df.get("PREÇO", pd.Series([0.0] * len(df))))
        col_avali = df.get("AVALIACAO", df.get("AVALIAÇÃO", pd.Series([""] * len(df)))).astype(str)
        col_obs   = df.get("OBSERVACAO_CALCULADA", df.get("OBSERVAÇÃO_CALCULADA", pd.Series([""] * len(df)))).astype(str)

        rows = []
        for fonte, sei, preco, ava, obs in zip(col_fonte, col_sei, col_preco, col_avali, col_obs):
            rows.append([
                fonte,
                sei,
                "R$ " + br_currency(preco),
                ava,
                sanitize(obs),
            ])
        pdf.table_rows(rows, widths, aligns, font_size=8)

    pdf.ln(2)

    # Métricas principais
    media  = analise.get("media", 0)
    cv     = analise.get("coef_variacao", analise.get("coef_variacao", 0)) or analise.get("coef_variacao", 0)
    minimo = (analise.get("melhor_preco_info", {}) or {}).get("PREÇO", 0)
    metodo = item_info.get("metodo_final", analise.get("metodo_sugerido", "N/A"))

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Resultados da Analise", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"MEDIA (validos): R$ {br_currency(media)}    COEFICIENTE DE VARIACAO: {float(cv):.2f}%", ln=1)
    pdf.cell(0, 6, f"PRECO MINIMO (valido): R$ {br_currency(minimo)}    METODO ESTATISTICO: {sanitize(metodo)}", ln=1)
    pdf.ln(2)

    # Blocos especificos por modo
    if pdf.tipo_analise == "Mapa de Precos":
        mp = analise.get("melhor_preco_info", {}) or {}
        melhor_preco = mp.get("PREÇO", 0) or mp.get("PRECO", 0)
        fonte = mp.get("EMPRESA/FONTE", "")
        sei   = mp.get("LOCALIZADOR SEI", "")
        texto = f"Melhor preco da pesquisa (apos filtros): R$ {br_currency(melhor_preco)} - Fonte: {sanitize(fonte)} | SEI: {sanitize(sei)}"
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(*pdf.fill_gray)
        pdf.multi_cell(0, 7, sanitize(texto), border=1, align="C", fill=True)
        pdf.ln(1)

    if pdf.tipo_analise == "Prorrogacao":
        contratado = float(item_info.get("valor_unit_contratado", 0) or 0)
        mercado    = float(item_info.get("valor_unit_mercado", 0) or 0)
        delta = mercado - contratado
        comp = "mais caro" if delta > 0 else ("mais barato" if delta < 0 else "igual")
        txt = (
            f"Comparacao (unitario): Mercado = R$ {br_currency(mercado)} vs Contratado = R$ {br_currency(contratado)} "
            f"-> Mercado esta {comp} em R$ {br_currency(abs(delta))}."
        )
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(*pdf.fill_gray)
        pdf.multi_cell(0, 7, sanitize(txt), border=1, align="C", fill=True)
        # avaliacao salva
        aval = sanitize(item_info.get("avaliacao_preco_contratado", ""))
        if aval:
            pdf.ln(1)
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, f"Avaliacao: {aval}")

    # Problemas e Justificativa
    problemas = item_info.get("problemas", []) or []
    justificativa = (item_info.get("justificativa", "") or "").strip()

    if problemas:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "Problemas encontrados", ln=1)
        pdf.set_font("Helvetica", "", 9)
        for p in problemas:
            pdf.multi_cell(pdf.usable_w, 6, "- " + sanitize(p))
        pdf.ln(1)

    if justificativa:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "Justificativa", ln=1)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(pdf.usable_w, 6, sanitize(justificativa))
        pdf.ln(1)

    # Destaque final
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(*pdf.fill_green)
    pdf.cell(0, 9, "PRECO DE MERCADO UNITARIO: R$ " + br_currency(item_info.get("valor_unit_mercado", 0)),
             ln=1, align="C", fill=True)

# -------------------- orquestração --------------------
def criar_pdf_completo(itens_analisados, num_processo, tipo_analise):
    # padroniza rótulos para evitar acentos/traços que geram erro de fonte
    tipo_norm = (tipo_analise or "").strip()
    if tipo_norm.lower().startswith("prorroga"):
        tipo_norm = "Prorrogacao"
    elif "mapa" in tipo_norm.lower():
        tipo_norm = "Mapa de Precos"
    else:
        tipo_norm = "Pesquisa Padrao"

    pdf = PDF(num_processo=num_processo, tipo_analise=tipo_norm)

    if itens_analisados:
        pagina_consolidada(pdf, itens_analisados, tipo_norm)

    # páginas por item
    for item in itens_analisados:
        df_original = pd.DataFrame(item.get("df_original", []))
        if df_original.empty:
            analise = {
                "df_avaliado": pd.DataFrame(),
                "media": item.get("valor_unit_mercado", 0),
                "coef_variacao": 0.0,
                "melhor_preco_info": {"PREÇO": item.get("valor_unit_mercado", 0)},
                "metodo_sugerido": item.get("metodo_final", "N/A"),
            }
        else:
            from logica import calcular_preco_mercado
            analise = calcular_preco_mercado(df_original, 25, 75) or {}
            mraw = analise.get("melhor_preco_info", {})
            if isinstance(mraw, pd.Series):
                analise["melhor_preco_info"] = mraw.to_dict()

        pagina_analise_item(pdf, item, analise)

    # --- retorno como bytes (sem .encode()) ---
    out = pdf.output(dest="S")   # bytes ou bytearray (fpdf2)
    return bytes(out) if isinstance(out, bytearray) else out
