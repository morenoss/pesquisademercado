# Ferramenta de Avaliação de Pesquisa de Mercado - STJ

## 📖 Visão Geral

Esta aplicação, desenvolvida com a framework Streamlit, tem como objetivo automatizar e padronizar o processo de avaliação de pesquisas de preços no Superior Tribunal de Justiça (STJ). A ferramenta implementa as regras de negócio definidas no **Manual de Pesquisa de Preços do STJ**, auxiliando na análise de cotações, identificação de preços inexequíveis ou excessivamente elevados e na consolidação dos resultados para gerar relatórios sintéticos e documentos em PDF.

## ✨ Funcionalidades Principais

* **Três Tipos de Análise**: Suporte para **Pesquisa Padrão**, **Prorrogação Contratual** e **Mapa Comparativo de Preços**.
* **Dois Modos de Lançamento**:
    * **Análise de Item**: Um fluxo guiado para analisar um item de cada vez, com cálculo e feedback imediato.
    * **Lançamento por Fonte**: Um método eficiente para inserir preços em lote, ideal para quando se tem múltiplas cotações de vários fornecedores.
* **Cálculo Automatizado**: Aplicação automática de regras para identificar preços válidos, calcular média, mediana, coeficiente de variação e sugerir o preço de mercado.
* **Geração de Relatórios**: Criação de relatórios consolidados na própria interface e exportação de um **PDF completo** com o resumo e a análise detalhada de cada item.
* **Persistência de Dados**: Funcionalidade para salvar e carregar o estado completo da análise num ficheiro (`.pkl`), permitindo continuar o trabalho posteriormente.

## 🚀 Começar

### Requisitos

* Python 3.10 ou superior
* Acesso à internet para descarregar as dependências

### Instalação

Recomenda-se o uso de um ambiente virtual para isolar as dependências do projeto.

**No Windows:**

1.  Execute o script `install_windows.bat` com um duplo clique. Ele fará todo o processo:
    * Criação do ambiente virtual na pasta `venv`.
    * Ativação do ambiente.
    * Instalação das bibliotecas listadas em `requirements.txt`.

**No Linux ou macOS:**

```bash
# 1. Crie o ambiente virtual
python3 -m venv venv

# 2. Ative o ambiente
source venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt
```

### Executar a Aplicação

Com o ambiente virtual ativado, execute o seguinte comando no seu terminal:

```bash
streamlit run app.py
```

A aplicação será aberta automaticamente no seu navegador.

## 🗂️ Estrutura do Projeto

```
/
|-- app.py                   # Ficheiro principal da aplicacao Streamlit
|-- logica.py                # Contem a logica de negocio para calculo de precos
|-- relatorios.py            # Funcoes para gerar as visualizacoes de relatorios na interface
|-- gerador_pdf.py           # Logica para criar o documento PDF final
|-- requirements.txt         # Lista de dependencias Python
|-- style.css                # Estilos CSS para personalizar a interface
|-- install_windows.bat      # Script de instalacao para Windows
|-- assets/                  # Pasta para recursos visuais
|   |-- logo_stj.png
|   |-- stj_favicon.ico
|   `-- marca_stj_brasao_cor_vert_compacta.png
`-- README.md                # Esta documentacao
```

## 🔧 Resolução de Problemas Comuns

* **O ícone (favicon) não atualiza**: O seu navegador pode guardar o ícone antigo em cache. Tente uma atualização forçada da página (Ctrl+F5).
* **Erro de codificação no PDF**: A aplicação tenta sanitizar os textos para evitar problemas. Garanta que os ficheiros de imagem (como o brasão) estão na pasta `assets` e acessíveis.