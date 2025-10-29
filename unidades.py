# unidades.py
import re

UNIDADES_PERMITIDAS: list[str] = [
    "ATIVIDADE",
    "BALDE",
    "BANDEIJA",
    "BARRA",
    "BISNAGA",
    "BLOCO",
    "BOBINA",
    "BOLSA",
    "BOMBONA",
    "CARGA",
    "CAIXA",
    "CENTÍMETRO",
    "CENTO",
    "CHAPA",
    "CONJUNTO",
    "DÚZIA",
    "EMBALAGEM",
    "ENVELOPE",
    "FARDO",
    "FOLHA",
    "FRASCO",
    "GALÃO",
    "GARRAFA",
    "GRAMA",
    "JOGO",
    "LATA",
    "LITRO",
    "LITRO DILUÍDO",
    "MAÇO",
    "METRO",
    "METRO CÚBICO",
    "METRO LINEAR",
    "METRO QUADRADO",
    "MILHEIRO",
    "MILILITRO",
    "PACOTE",
    "PAR",
    "PEÇA",
    "POTE",
    "REFIL",
    "RECIPIENTE",
    "RESMA",
    "ROLO",
    "SACO",
    "TABLETE",
    "TAMBOR",
    "TONELADA",
    "TUBO",
    "UNIDADE",
    "VIDRO",
    "QUILOGRAMA",
]

# sinônimos comuns -> forma padronizada
_UNID_SINONIMOS = {
    "M": "METRO",
    "M.": "METRO",
    "METROS": "METRO",
    "ML": "MILILITRO",
    "M L": "MILILITRO",
    "MILILITROS": "MILILITRO",
    "L": "LITRO",
    "LITROS": "LITRO",
    "KG": "QUILOGRAMA",
    "KILO": "QUILOGRAMA",
    "QUILO": "QUILOGRAMA",
    "G": "GRAMA",
    "GRAMAS": "GRAMA",
    "M2": "METRO QUADRADO",
    "M²": "METRO QUADRADO",
    "M3": "METRO CÚBICO",
    "M³": "METRO CÚBICO",
    "M/L": "METRO LINEAR",
    "METRO LINEAR.": "METRO LINEAR",
    "UN": "UNIDADE",
    "UND": "UNIDADE",
    "UNID": "UNIDADE",
    "UNIDADE(S)": "UNIDADE",
}

def normalizar_unidade(txt: str) -> str:
    """
    Retorna a unidade padronizada (string em UNIDADES_PERMITIDAS) ou "" se não reconhecida.
    """
    if not txt:
        return ""
    u = str(txt).strip().upper()
    u = re.sub(r"\s+", " ", u)
    u = _UNID_SINONIMOS.get(u, u)

    if u in UNIDADES_PERMITIDAS:
        return u

    # comparação "sem acentos/pontuação"
    def _plain(s: str) -> str:
        return re.sub(r"[^\w ]", "", s, flags=re.UNICODE)
    pu = _plain(u)
    for cand in UNIDADES_PERMITIDAS:
        if _plain(cand) == pu:
            return cand
    return ""

__all__ = ["UNIDADES_PERMITIDAS", "normalizar_unidade"]
