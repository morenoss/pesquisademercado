# logica.py
import pandas as pd
import numpy as np

# LISTA DE FONTES PÚBLICAS (AJUSTADA CONFORME SOLICITADO)
FONTES_PUBLICAS = ['Contrato', 'Banco de Preços/Comprasnet', 'Ata de Registro de Preços']

def calcular_preco_mercado(df_precos, limiar_elevado, limiar_inexequivel):
    """
    Função principal que aplica as regras do manual e calcula os resultados.
    """
    if df_precos.empty or df_precos['PREÇO'].isnull().all():
        return {}

    dados = df_precos.dropna(subset=['PREÇO']).copy()
    dados['AVALIAÇÃO'] = "VÁLIDO"
    dados['OBSERVAÇÃO_CALCULADA'] = ""
    resultados = {'problemas': []}

    # 1. Excluir preços excessivamente elevados
    precos_validos = dados.copy()
    for idx, row in dados.iterrows():
        media_outros = precos_validos.drop(idx)['PREÇO'].mean()
        if not np.isnan(media_outros) and row['PREÇO'] > (1 + limiar_elevado / 100) * media_outros:
            dados.loc[idx, 'AVALIAÇÃO'] = "EXCESSIVAMENTE ELEVADO"
            dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = f"<p style='color:red;'>Preço excessivamente elevado.</p>"

    dados_sem_altos = dados[dados['AVALIAÇÃO'] == "VÁLIDO"]

    # 2. Excluir preços inexequíveis (com exceção para fontes públicas)
    for idx, row in dados_sem_altos.iterrows():
        media_outros_final = dados_sem_altos.drop(idx)['PREÇO'].mean()
        
        if not np.isnan(media_outros_final) and row['PREÇO'] < (limiar_inexequivel / 100) * media_outros_final:
            percentual = (row['PREÇO'] / media_outros_final) * 100 if media_outros_final > 0 else 0
            
            if row['TIPO DE FONTE'] in FONTES_PUBLICAS:
                dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = (f"<p style='color:orange;'>Apesar de inexequível ({percentual:.2f}% da média), "
                                                      f"é considerado válido por ser um preço praticado pela Administração Pública.</p>")
            else:
                dados.loc[idx, 'AVALIAÇÃO'] = "INEXEQUIÍVEL"
                dados.loc[idx, 'OBSERVAÇÃO_CALCULADA'] = f"<p style='color:red;'>Preço inexequível ({percentual:.2f}% da média dos demais).</p>"
    
    precos_finais_df = dados[dados['AVALIAÇÃO'] == "VÁLIDO"]
    precos_finais = precos_finais_df['PREÇO']
    resultados['df_avaliado'] = dados

    # 3. Verificação de problemas na pesquisa
    if len(precos_finais) < 3:
        resultados['problemas'].append("A pesquisa possui menos de 3 preços válidos, o que pode diminuir a confiabilidade da estimativa.")
    
    # NOVA VERIFICAÇÃO (movida para cá)
    fontes_publicas_count = dados['TIPO DE FONTE'].isin(FONTES_PUBLICAS).sum()
    total_fontes = len(dados)
    if total_fontes > 0 and (fontes_publicas_count / total_fontes) < 0.5:
        resultados['problemas'].append("Os preços praticados pela Administração Pública não são a maioria das fontes.")

    if precos_finais.empty:
        resultados['problemas'].append("Nenhum preço válido encontrado para realizar o cálculo.")
        return resultados

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
    
    resultados.update({
        'media': preco_medio,
        'desvio_padrao': desvio_padrao,
        'coef_variacao': coef_variacao,
        'metodo_sugerido': metodo,
        'preco_mercado_calculado': preco_mercado,
        'melhor_preco_info': preco_minimo_row
    })
    
    return resultados