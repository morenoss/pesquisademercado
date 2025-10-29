# logica.py
import pandas as pd
import numpy as np
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation

def _quant(n: int) -> Decimal:
    n = max(0, min(7, int(n or 0)))
    return Decimal('1') if n == 0 else Decimal('1.' + ('0' * n))

def arredonda_nbr5891(valor: float | int | str, casas: int) -> float:
    """Arredonda conforme ABNT NBR 5891 (empate para par) com 0..7 casas."""
    try:
        d = Decimal(str(valor))
        return float(d.quantize(_quant(casas), rounding=ROUND_HALF_EVEN))
    except (InvalidOperation, ValueError, TypeError):
        return float(valor or 0)

# LISTA DE FONTES PÚBLICAS (AJUSTADA CONFORME SOLICITADO)
FONTES_PUBLICAS = ['Contrato', 'Banco de Preços/Comprasnet', 'Ata de Registro de Preços']

def calcular_preco_mercado(
    df_precos: pd.DataFrame,
    limiar_elevado: float,
    limiar_inexequivel: float,
    casas_decimais: int = 2,
    aplicar_nbr5891: bool = True
):
    """
    Função principal que aplica as regras do manual e calcula os resultados.
    Aplica arredondamento (NBR 5891) e casas decimais APENAS no resultado.
    """
    # Normaliza coluna de preço para aceitar PREÇO ou PRECO
    if 'PREÇO' not in df_precos.columns and 'PRECO' in df_precos.columns:
        df_precos = df_precos.rename(columns={'PRECO': 'PREÇO'})

    if df_precos.empty or df_precos['PREÇO'].isnull().all():
        return {}

    dados = df_precos.dropna(subset=['PREÇO']).copy()
    dados['PREÇO'] = pd.to_numeric(dados['PREÇO'], errors='coerce')
    dados = dados.dropna(subset=['PREÇO']).copy()

    dados['AVALIAÇÃO'] = "VÁLIDO"
    dados['OBSERVAÇÃO_CALCULADA'] = ""
    resultados = {'problemas': []}

    # 1. Excluir preços excessivamente elevados (comparando com a média dos demais)
    precos_validos = dados.copy()
    for idx, row in dados.iterrows():
        media_outros = precos_validos.drop(idx)['PREÇO'].mean()
        if not np.isnan(media_outros) and row['PREÇO'] > (1 + limiar_elevado / 100) * media_outros:
            dados.loc[idx, 'AVALIAÇÃO'] = "EXCESSIVAMENTE ELEVADO"
            dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = "<p style='color:red;'>Preço excessivamente elevado.</p>"

    dados_sem_altos = dados[dados['AVALIAÇÃO'] == "VÁLIDO"]

    # 2. Excluir preços inexequíveis (com exceção para fontes públicas)
    for idx, row in dados_sem_altos.iterrows():
        media_outros_final = dados_sem_altos.drop(idx)['PREÇO'].mean()
        if not np.isnan(media_outros_final) and row['PREÇO'] < (limiar_inexequivel / 100) * media_outros_final:
            percentual = (row['PREÇO'] / media_outros_final) * 100 if media_outros_final > 0 else 0
            if row.get('TIPO DE FONTE') in FONTES_PUBLICAS:
                dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = (
                    f"<p style='color:orange;'>Apesar de inexequível ({percentual:.2f}% da média), "
                    f"é considerado válido por ser um preço praticado pela Administração Pública.</p>"
                )
            else:
                dados.loc[idx, 'AVALIAÇÃO'] = "INEXEQUÍVEL"
                dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = (
                    f"<p style='color:red;'>Preço inexequível ({percentual:.2f}% da média dos demais).</p>"
                )

    precos_finais_df = dados[dados['AVALIAÇÃO'] == "VÁLIDO"]
    precos_finais = precos_finais_df['PREÇO']
    resultados['df_avaliado'] = dados

    # 3. Verificação de problemas na pesquisa
    if len(precos_finais) < 3:
        resultados['problemas'].append(
            "A pesquisa possui menos de 3 preços válidos, o que pode diminuir a confiabilidade da estimativa."
        )

    # >>> NOVA REGRA (apenas sobre fontes públicas válidas, sem exigir maioria)
    validos_df = dados[dados['AVALIAÇÃO'] == "VÁLIDO"].copy()
    if 'TIPO DE FONTE' in validos_df.columns:
        n_publicos_validos = validos_df['TIPO DE FONTE'].isin(FONTES_PUBLICAS).sum()
    else:
        n_publicos_validos = 0

    # Se houver ≥ 3 preços públicos válidos, NÃO sinalizar problema algum.
    # Caso contrário, apenas um aviso brando (sem "inconsistência por maioria não pública").
    if n_publicos_validos == 0:
        resultados['problemas'].append(
            "Nenhum preço válido proveniente de fonte pública foi identificado."
        )
    elif n_publicos_validos < 3:
        resultados['problemas'].append(
            "Foram encontrados menos de 3 preços válidos de fontes públicas; se possível, complemente a pesquisa."
        )

    # 4. Cálculos estatísticos
    preco_medio = precos_finais.mean()
    preco_mediana = precos_finais.median()
    preco_minimo_row = precos_finais_df.loc[precos_finais.idxmin()]
    desvio_padrao = precos_finais.std(ddof=0) if len(precos_finais) > 1 else 0
    coef_variacao = (desvio_padrao / preco_medio) * 100 if preco_medio > 0 else 0

    if coef_variacao <= 25:
        preco_mercado = preco_medio
        metodo = "MÉDIA"
    else:
        preco_mercado = preco_mediana
        metodo = "MEDIANA"

    # Guardar valores brutos e método
    resultados.update({
        'media': preco_medio,
        'desvio_padrao': desvio_padrao,
        'coef_variacao': coef_variacao,
        'metodo_sugerido': metodo,
        'preco_mercado_bruto': preco_mercado,    # sem arredondar
        'preco_mercado_calculado': preco_mercado,  # será substituído abaixo
        'melhor_preco_info': preco_minimo_row
    })

    # Aplicar arredondamento e casas decimais ao resultado (NBR 5891 por padrão)
    nd = max(0, min(7, casas_decimais))
    if aplicar_nbr5891:
        resultados['preco_mercado_calculado'] = arredonda_nbr5891(preco_mercado, nd)
        resultados['media'] = arredonda_nbr5891(preco_medio, nd)
    else:
        resultados['preco_mercado_calculado'] = round(preco_mercado, nd)
        resultados['media'] = round(preco_medio, nd)

    # Coluna auxiliar apenas para exibição
    dados['PREÇO_ARREDONDADO'] = (
        dados['PREÇO'].apply(lambda v: arredonda_nbr5891(v, nd))
        if aplicar_nbr5891 else
        dados['PREÇO'].round(nd)
    )

    resultados['df_avaliado'] = dados
    resultados['casas_decimais'] = nd
    resultados['aplicar_nbr5891'] = bool(aplicar_nbr5891)
    return resultados

# Fim do arquivo logica.py