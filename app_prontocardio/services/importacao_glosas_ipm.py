from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Iterable, Mapping

CENTAVOS = Decimal('0.01')


@dataclass(frozen=True, order=True)
class ChaveProcesso:
    numero_processo: str
    competencia: date
    valor_protocolo: Decimal


@dataclass(frozen=True)
class AssociacaoDemonstrativo:
    unicas: dict[str, ChaveProcesso]
    sem_processo: tuple[Mapping, ...]
    ambiguas: tuple[tuple[Mapping, tuple[ChaveProcesso, ...]], ...]


@dataclass(frozen=True)
class AssociacaoRemessa:
    unicas: dict[ChaveProcesso, int]
    ambiguas: tuple[tuple[ChaveProcesso, tuple[int, ...]], ...]
    nao_encontradas: tuple[ChaveProcesso, ...]


@dataclass(frozen=True)
class ClassificacaoSemProcessoOracle:
    identificadas: dict[str, int]
    criterios: dict[str, str]
    sem_correspondencia: tuple[Mapping, ...]
    ambiguas: tuple[tuple[Mapping, tuple[int, ...]], ...]


@dataclass(frozen=True)
class ResolucaoItem:
    status: str
    conta: int | None = None
    cd_lancamento: int | None = None
    candidatos: tuple[tuple[int, int], ...] = ()


def normalizar_texto(valor) -> str:
    return str(valor or '').strip().upper()


def normalizar_digitos(valor) -> str:
    return re.sub(r'[^0-9]', '', str(valor or ''))


def normalizar_carteira(valor) -> str:
    return normalizar_digitos(valor).lstrip('0')


def normalizar_mes_ano(valor) -> tuple[int, int] | None:
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return valor.year, valor.month

    bruto = str(valor or '').strip()
    for formato in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            resultado = datetime.strptime(bruto[:10], formato)
            return resultado.year, resultado.month
        except ValueError:
            continue
    return None


def normalizar_dinheiro(valor) -> Decimal:
    return Decimal(valor or 0).quantize(CENTAVOS, ROUND_HALF_UP)


def normalizar_competencia(valor) -> date | None:
    bruto = str(valor or '').strip()
    for formato in ('%m/%Y', '%m/%y'):
        try:
            resultado = datetime.strptime(bruto, formato)
            return date(resultado.year, resultado.month, 1)
        except ValueError:
            continue
    return None


def indexar_processos(
    linhas: Iterable[Mapping],
) -> tuple[
    dict[tuple[str, Decimal], set[ChaveProcesso]],
    dict[ChaveProcesso, Mapping],
]:
    indice: dict[tuple[str, Decimal], set[ChaveProcesso]] = defaultdict(set)
    dados: dict[ChaveProcesso, Mapping] = {}
    for linha in linhas:
        competencia = normalizar_competencia(linha['competencia_producao'])
        if competencia is None or linha['valor_protocolo'] is None:
            continue
        chave = ChaveProcesso(
            numero_processo=normalizar_texto(linha['numero_processo']),
            competencia=competencia,
            valor_protocolo=normalizar_dinheiro(linha['valor_protocolo']),
        )
        if not chave.numero_processo:
            continue
        dados[chave] = linha
        for identificador in (linha.get('nr'), linha.get('nr_origem')):
            protocolo = normalizar_texto(identificador)
            if protocolo:
                indice[(protocolo, chave.valor_protocolo)].add(chave)
    return dict(indice), dados


def associar_demonstrativos_a_processos(
    demonstrativos: Iterable[Mapping],
    indice_processos: Mapping[tuple[str, Decimal], set[ChaveProcesso]],
) -> AssociacaoDemonstrativo:
    unicas = {}
    sem_processo = []
    ambiguas = []
    for linha in demonstrativos:
        candidatos = indice_processos.get(
            (
                normalizar_texto(linha['numero_protocolo']),
                normalizar_dinheiro(linha['valor_protocolo']),
            ),
            set(),
        )
        if not candidatos:
            sem_processo.append(linha)
        elif len(candidatos) > 1:
            ambiguas.append((linha, tuple(sorted(candidatos))))
        else:
            unicas[str(linha['id_registro'])] = next(iter(candidatos))
    return AssociacaoDemonstrativo(
        unicas=unicas,
        sem_processo=tuple(sem_processo),
        ambiguas=tuple(ambiguas),
    )


def associar_processos_a_remessas(
    processos: Iterable[ChaveProcesso],
    remessas_por_valor: Mapping[Decimal, set[int]],
) -> AssociacaoRemessa:
    unicas = {}
    ambiguas = []
    nao_encontradas = []
    for processo in sorted(set(processos)):
        candidatos = remessas_por_valor.get(processo.valor_protocolo, set())
        if not candidatos:
            nao_encontradas.append(processo)
        elif len(candidatos) > 1:
            ambiguas.append((processo, tuple(sorted(candidatos))))
        else:
            unicas[processo] = next(iter(candidatos))
    return AssociacaoRemessa(
        unicas=unicas,
        ambiguas=tuple(ambiguas),
        nao_encontradas=tuple(nao_encontradas),
    )


def chave_item_demonstrativo(linha: Mapping, cd_remessa: int) -> tuple:
    return (
        cd_remessa,
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['numero_guia_senha']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_oracle(linha: Mapping) -> tuple:
    return (
        int(linha['cd_remessa']),
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(linha['nr_guia']),
        normalizar_texto(linha['cd_pro_fat']),
        normalizar_carteira(linha['nr_carteira']),
    )


def chave_item_sem_guia_demonstrativo(
    linha: Mapping,
    cd_remessa: int,
) -> tuple:
    return (
        cd_remessa,
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_sem_guia_oracle(linha: Mapping) -> tuple:
    return (
        int(linha['cd_remessa']),
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(linha['cd_pro_fat']),
        normalizar_carteira(linha['nr_carteira']),
    )


def resolver_item(
    candidatos: Iterable[tuple[int, int]],
) -> ResolucaoItem:
    itens = tuple(sorted(set(candidatos)))
    if not itens:
        return ResolucaoItem(status='nao_encontrado')
    if len(itens) == 1:
        conta, lancamento = itens[0]
        return ResolucaoItem(
            status='item_unico',
            conta=conta,
            cd_lancamento=lancamento,
            candidatos=itens,
        )
    contas = {item[0] for item in itens}
    if len(contas) == 1:
        return ResolucaoItem(
            status='conta_unica',
            conta=next(iter(contas)),
            cd_lancamento=None,
            candidatos=itens,
        )
    return ResolucaoItem(status='ambiguo', candidatos=itens)


def _resolver_remessas_por_criterios(
    linha: Mapping,
    remessas: Iterable[int],
    criterios: Iterable[
        tuple[
            str,
            Mapping[tuple, Iterable[Mapping]],
            Callable[[Mapping, int], tuple],
        ]
    ],
) -> tuple[list[int], list[int], dict[int, str]]:
    remessas_seguras = []
    remessas_ambiguas = []
    criterios_seguros = {}
    fontes = tuple(criterios)

    for cd_remessa in remessas:
        criterio_encontrado = None
        itens = ()
        for criterio, indice, gerar_chave in fontes:
            itens = indice.get(gerar_chave(linha, cd_remessa), ())
            if itens:
                criterio_encontrado = criterio
                break

        resolucao = resolver_item(
            (
                int(item['cd_reg']),
                int(item['cd_lancamento']),
            )
            for item in itens
        )
        if resolucao.status in {'item_unico', 'conta_unica'}:
            remessas_seguras.append(cd_remessa)
            criterios_seguros[cd_remessa] = str(criterio_encontrado)
        elif resolucao.status == 'ambiguo':
            remessas_ambiguas.append(cd_remessa)

    return remessas_seguras, remessas_ambiguas, criterios_seguros


def classificar_demonstrativos_sem_processo_por_oracle(
    demonstrativos: Iterable[Mapping],
    remessas_por_valor: Mapping[Decimal, set[int]],
    itens_por_chave: Mapping[tuple, Iterable[Mapping]],
) -> ClassificacaoSemProcessoOracle:
    identificadas = {}
    criterios = {}
    sem_correspondencia = []
    ambiguas = []
    itens_por_chave_sem_guia: dict[tuple, list[Mapping]] = defaultdict(list)
    for itens in itens_por_chave.values():
        for item in itens:
            if normalizar_mes_ano(item.get('dt_competencia')) is None:
                continue
            itens_por_chave_sem_guia[
                chave_item_sem_guia_oracle(item)
            ].append(item)

    for linha in demonstrativos:
        remessas_candidatas = sorted(
            remessas_por_valor.get(
                normalizar_dinheiro(linha['valor_protocolo']),
                set(),
            )
        )
        (
            remessas_seguras,
            remessas_ambiguas,
            criterios_seguros,
        ) = _resolver_remessas_por_criterios(
            linha,
            remessas_candidatas,
            (
                (
                    'competencia_guia_servico_carteira',
                    itens_por_chave,
                    chave_item_demonstrativo,
                ),
            ),
        )

        if not remessas_seguras and not remessas_ambiguas:
            (
                remessas_seguras,
                remessas_ambiguas,
                criterios_seguros,
            ) = _resolver_remessas_por_criterios(
                linha,
                remessas_candidatas,
                (
                    (
                        'competencia_servico_carteira',
                        itens_por_chave_sem_guia,
                        chave_item_sem_guia_demonstrativo,
                    ),
                ),
            )

        candidatas = tuple(
            sorted({
                *remessas_seguras,
                *remessas_ambiguas,
            })
        )
        if len(remessas_seguras) == 1 and not remessas_ambiguas:
            id_registro = str(linha['id_registro'])
            remessa = remessas_seguras[0]
            identificadas[id_registro] = remessa
            criterios[id_registro] = criterios_seguros[remessa]
        elif candidatas:
            ambiguas.append((linha, candidatas))
        else:
            sem_correspondencia.append(linha)

    return ClassificacaoSemProcessoOracle(
        identificadas=identificadas,
        criterios=criterios,
        sem_correspondencia=tuple(sem_correspondencia),
        ambiguas=tuple(ambiguas),
    )


def chave_conta_bancaria(linha: Mapping) -> tuple[str, str]:
    return (
        normalizar_digitos(linha['codigo_agencia']),
        normalizar_digitos(linha['conta']),
    )


def hash_nfse_ipm(id_registro: str) -> str:
    return f'ipm:{str(id_registro).strip()}'
