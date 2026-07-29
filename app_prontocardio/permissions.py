import json

TELAS_SISTEMA = (
    'indicadores',
    'follow_up_glosas',
    'triagem',
    'acompanhamento',
    'conciliacao_manual',
    'conciliacao_financeira',
    'consultar_conciliacoes',
    'follow_up_solicitacoes',
    'emissao_nfse',
    'acompanhamento_particular',
    'solicitar_nota',
    'solicitacoes_cadastradas',
    'solicitacoes_recusas',
    'configuracao_convenio',
    'empresas_nfse',
)
TELAS_PADRAO_JSON = json.dumps(list(TELAS_SISTEMA))


def telas_padrao() -> list[str]:
    return list(TELAS_SISTEMA)


def normalizar_telas(telas: list[str]) -> list[str]:
    telas_informadas = set(telas)
    invalidas = telas_informadas.difference(TELAS_SISTEMA)
    if invalidas:
        raise ValueError('Telas inválidas: ' + ', '.join(sorted(invalidas)))
    return [tela for tela in TELAS_SISTEMA if tela in telas_informadas]
