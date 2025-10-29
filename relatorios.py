import streamlit as st
import pandas as pd
from decimal import Decimal, ROUND_HALF_EVEN

# ------------------ utilidades de formatação (dinâmicas) ------------------

def _decimals() -> int:
    """Lê as casas decimais definidas no app (fallback=2)."""
    try:
        return max(0, min(7, int(st.session_state.get("casas_decimais", 2))))
    except Exception:
        return 2

def _quant(n: int) -> Decimal:
    n = max(0, min(7, int(n or 0)))
    return Decimal("1") if n == 0 else Decimal("1." + ("0" * n))

def _br_number(valor: float, casas: int | None = None) -> str:
    """
    Formata número brasileiro com arredondamento ABNT (empate para par).
    Retorna só o número (sem 'R$').
    """
    n = _decimals() if casas is None else max(0, min(7, int(casas)))
    try:
        d = Decimal(str(float(valor))).quantize(_quant(n), rounding=ROUND_HALF_EVEN)
        s = f"{d:,.{n}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    except Exception:
        return ("0," + ("0" * n)) if n > 0 else "0"

def formatar_moeda(valor, casas: int | None = None) -> str:
    return f"R$ {_br_number(valor, casas)}"

def formatar_moeda_html(valor, casas: int | None = None) -> str:
    # usa &nbsp; para não quebrar "R$"
    return formatar_moeda(valor, casas).replace("R$", "R&#36;&nbsp;")

def _fmt_col(n: int) -> str:
    """Formato para NumberColumn com n casas."""
    return f"R$ %.{n}f"

# ------------------ Relatório: Pesquisa Padrão ------------------

def gerar_relatorio_padrao(itens, num_processo, printable=False):
    n = _decimals()

    st.header("RELATÓRIO SINTÉTICO - PESQUISA PADRÃO") if not printable else st.markdown("### RELATÓRIO SINTÉTICO - PESQUISA PADRÃO")
    st.subheader("CONSOLIDAÇÃO DOS VALORES DA PESQUISA DE MERCADO")

    if not itens:
        return
    df = pd.DataFrame(itens)
    total = pd.to_numeric(df["valor_total_mercado"], errors="coerce").fillna(0).sum()

    st.markdown(
        f"""
        <div style="background:#e8f0fe;padding:12px 14px;border-radius:8px;font-size:1.1rem;font-weight:bold;color:#1a3d8f;margin:0 0 15px 0;">
            <b>VALOR TOTAL OBTIDO NA PESQUISA DE MERCADO:</b> {formatar_moeda_html(total, n)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    df_display = df.rename(columns={
        "item_num":"ITEM","descricao":"DESCRIÇÃO","unidade":"UNID.","metodo_final":"MÉTODO ESTATÍSTICO",
        "valor_unit_mercado":"VALOR UNIT. DE MERCADO","valor_total_mercado":"VALOR TOTAL DE MERCADO"
    })
    df_display["Nº DO PROCESSO"] = num_processo

    st.dataframe(
        df_display[["ITEM","Nº DO PROCESSO","DESCRIÇÃO","UNID.","MÉTODO ESTATÍSTICO","VALOR UNIT. DE MERCADO","VALOR TOTAL DE MERCADO"]],
        column_config={
            "VALOR UNIT. DE MERCADO": st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR TOTAL DE MERCADO": st.column_config.NumberColumn(format=_fmt_col(n)),
        },
        hide_index=True, use_container_width=True
    )

# ------------------ Relatório: Prorrogação ------------------

def gerar_relatorio_prorrogacao(itens, num_processo, printable=False):
    n = _decimals()

    st.header("RELATÓRIO SINTÉTICO - PRORROGAÇÃO CONTRATUAL") if not printable else st.markdown("### RELATÓRIO SINTÉTICO - PRORROGAÇÃO CONTRATUAL")
    st.subheader("CONSOLIDAÇÃO DOS VALORES DA PESQUISA DE MERCADO")

    if not itens:
        return
    df = pd.DataFrame(itens)
    total_mercado    = pd.to_numeric(df["valor_total_mercado"], errors="coerce").fillna(0).sum()
    total_contratado = pd.to_numeric(df["valor_total_contratado"], errors="coerce").fillna(0).sum()
    diff = total_contratado - total_mercado
    sentido = "MAIS CARO" if diff > 0 else ("MAIS BARATO" if diff < 0 else "IGUAL")

    st.markdown(
        f"""
        <div style="background:#e8f0fe;padding:12px 14px;border-radius:8px;font-size:1.1rem;font-weight:bold;color:#1a3d8f;margin:0 0 15px 0;">
            <b>VALOR TOTAL OBTIDO NA PESQUISA DE MERCADO:</b> {formatar_moeda_html(total_mercado, n)}<br>
            <b>VALOR TOTAL CONTRATADO:</b> {formatar_moeda_html(total_contratado, n)}<br>
            <b>DIFERENÇA:</b> {formatar_moeda_html(abs(diff), n)} — {sentido}
        </div>
        """,
        unsafe_allow_html=True,
    )

    df_display = df.rename(columns={
        "item_num":"ITEM","descricao":"DESCRIÇÃO","unidade":"UNID.","metodo_final":"MÉTODO ESTATÍSTICO",
        "valor_unit_mercado":"VALOR UNIT. DE MERCADO","valor_total_mercado":"VALOR TOTAL DE MERCADO",
        "valor_unit_contratado":"VALOR UNIT. CONTRATADO","valor_total_contratado":"VALOR TOTAL CONTRATADO",
        "avaliacao_preco_contratado":"AVALIAÇÃO DO PREÇO CONTRATADO"
    })
    df_display["Nº DO PROCESSO"] = num_processo

    st.dataframe(
        df_display[[
            "ITEM","Nº DO PROCESSO","DESCRIÇÃO","MÉTODO ESTATÍSTICO",
            "VALOR UNIT. DE MERCADO","VALOR TOTAL DE MERCADO",
            "VALOR UNIT. CONTRATADO","VALOR TOTAL CONTRATADO","AVALIAÇÃO DO PREÇO CONTRATADO"]],
        column_config={
            "VALOR UNIT. DE MERCADO":     st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR TOTAL DE MERCADO":     st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR UNIT. CONTRATADO":     st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR TOTAL CONTRATADO":     st.column_config.NumberColumn(format=_fmt_col(n)),
        },
        hide_index=True, use_container_width=True
    )

# ------------------ Relatório: Mapa de Preços ------------------

def gerar_relatorio_mapa(itens, num_processo, printable=False):
    n = _decimals()

    st.header("RELATÓRIO SINTÉTICO - MAPA COMPARATIVO DE PREÇOS") if not printable else st.markdown("### RELATÓRIO SINTÉTICO - MAPA COMPARATIVO DE PREÇOS")

    if not itens:
        return
    df = pd.DataFrame(itens)

    total_mercado = pd.to_numeric(df["valor_total_mercado"], errors="coerce").fillna(0).sum()
    total_best    = pd.to_numeric(df["valor_total_melhor_preco"], errors="coerce").fillna(0).sum()
    diff = total_mercado - total_best
    frase = "MAIS BARATO" if diff > 0 else ("MAIS CARO" if diff < 0 else "IGUAL")

    st.markdown(
        f"""
        <div style="background:#e8f0fe;padding:12px 14px;border-radius:8px;font-size:1.1rem;font-weight:bold;color:#1a3d8f;margin:0 0 15px 0;">
            <b>VALOR TOTAL OBTIDO NA PESQUISA DE MERCADO:</b> {formatar_moeda_html(total_mercado, n)} |
            <b>VALOR TOTAL DOS MELHORES PREÇOS DA PESQUISA:</b> {formatar_moeda_html(total_best, n)} |
            <b>OS MELHORES PREÇOS SÃO {formatar_moeda_html(abs(diff), n)} {frase} QUE O APURADO NA PESQUISA</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    df_display = df.rename(columns={
        "item_num":"ITEM","descricao":"DESCRIÇÃO","unidade":"UNID.","metodo_final":"MÉTODO ESTATÍSTICO",
        "valor_unit_mercado":"VALOR UNITÁRIO (MERCADO)","valor_total_mercado":"VALOR TOTAL (MERCADO)",
        "valor_unit_melhor_preco":"VALOR UNITÁRIO (MELHOR PREÇO)","valor_total_melhor_preco":"VALOR TOTAL (MELHOR PREÇO)",
        "dados_melhor_proposta":"DADOS DA PROPOSTA"
    })
    df_display["Nº DO PROCESSO"] = num_processo

    st.dataframe(
        df_display[[
            "ITEM","Nº DO PROCESSO","DESCRIÇÃO","MÉTODO ESTATÍSTICO",
            "VALOR UNITÁRIO (MERCADO)","VALOR TOTAL (MERCADO)",
            "VALOR UNITÁRIO (MELHOR PREÇO)","VALOR TOTAL (MELHOR PREÇO)",
            "DADOS DA PROPOSTA"]],
        column_config={
            "VALOR UNITÁRIO (MERCADO)":       st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR TOTAL (MERCADO)":          st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR UNITÁRIO (MELHOR PREÇO)":  st.column_config.NumberColumn(format=_fmt_col(n)),
            "VALOR TOTAL (MELHOR PREÇO)":     st.column_config.NumberColumn(format=_fmt_col(n)),
        },
        hide_index=True, use_container_width=True
    )
# ------------------ Fim do arquivo ------------------