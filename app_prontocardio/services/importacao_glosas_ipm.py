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


@dataclass(frozen=True)
class IndicesItensOracle:
    competencia_guia_servico_carteira: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    atendimento_guia_servico_carteira_valor: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    lancamento_dia_coalesce_servico_carteira: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    competencia_servico_carteira: Mapping[tuple, tuple[Mapping, ...]]
    competencia_tuss_carteira: Mapping[tuple, tuple[Mapping, ...]]
    lancamento_coalesce_servico_carteira: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    competencia_coalesce_servico_valor: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    atendimento_guia_coalesce_servico_valor: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]
    lancamento_pro_fat_carteira_valor: Mapping[
        tuple,
        tuple[Mapping, ...],
    ]


@dataclass(frozen=True)
class CorrespondenciaItemOracle:
    status: str
    criterio: str | None = None
    cd_remessa: int | None = None
    resolucao: ResolucaoItem | None = None
    itens: tuple[Mapping, ...] = ()
    remessas_candidatas: tuple[int, ...] = ()


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


def normalizar_data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    bruto = str(valor or '').strip()
    for formato in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(bruto[:10], formato).date()
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


def normalizar_competencia_mensal(valor) -> str | None:
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return valor.strftime('%m/%Y')

    bruto = str(valor or '').strip()
    for formato in ('%m/%Y', '%m/%y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(bruto[:10], formato).strftime('%m/%Y')
        except ValueError:
            continue
    return None


def indexar_processos(
    linhas: Iterable[Mapping],
) -> tuple[
    dict[tuple[str, str], set[ChaveProcesso]],
    dict[ChaveProcesso, Mapping],
]:
    indice: dict[tuple[str, str], set[ChaveProcesso]] = defaultdict(set)
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
        competencia_mensal = normalizar_competencia_mensal(
            linha['competencia_producao']
        )
        if competencia_mensal is None:
            continue
        for identificador in (linha.get('nr'), linha.get('nr_origem')):
            protocolo = normalizar_texto(identificador)
            if protocolo:
                indice[(protocolo, competencia_mensal)].add(chave)
    return dict(indice), dados


def associar_demonstrativos_a_processos(
    demonstrativos: Iterable[Mapping],
    indice_processos: Mapping[tuple[str, str], set[ChaveProcesso]],
) -> AssociacaoDemonstrativo:
    unicas = {}
    sem_processo = []
    ambiguas = []
    for linha in demonstrativos:
        competencia_mensal = normalizar_competencia_mensal(
            linha.get('data_realizacao')
        )
        candidatos = set()
        if competencia_mensal is not None:
            candidatos = set(indice_processos.get(
                (
                    normalizar_texto(linha['numero_protocolo']),
                    competencia_mensal,
                ),
                set(),
            ))
        if len(candidatos) > 1:
            valor_mensal = linha.get('valor_protocolo_mes')
            if valor_mensal is None:
                valor_mensal = linha.get('valor_protocolo')
            if valor_mensal is not None:
                valor_normalizado = normalizar_dinheiro(valor_mensal)
                candidatos_por_valor = {
                    candidato
                    for candidato in candidatos
                    if candidato.valor_protocolo == valor_normalizado
                }
                if candidatos_por_valor:
                    candidatos = candidatos_por_valor
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
    remessas_por_valor: Mapping[tuple[Decimal, str], set[int]],
) -> AssociacaoRemessa:
    unicas = {}
    ambiguas = []
    nao_encontradas = []
    for processo in sorted(set(processos)):
        candidatos = remessas_por_valor.get(
            (
                processo.valor_protocolo,
                processo.competencia.strftime('%m/%Y'),
            ),
            set(),
        )
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


def chave_item_demonstrativo(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['numero_guia_senha']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_oracle(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(linha['nr_guia']),
        normalizar_texto(linha['cd_pro_fat']),
        normalizar_carteira(linha['nr_carteira']),
    )


def chave_item_sem_guia_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_sem_guia_oracle(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(linha['cd_pro_fat']),
        normalizar_carteira(linha['nr_carteira']),
    )


def chave_item_sem_guia_tuss_demonstrativo(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
        normalizar_dinheiro(linha['valor_processado']),
    )


def chave_item_sem_guia_tuss_oracle(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(linha['cd_tuss']),
        normalizar_carteira(linha['nr_carteira']),
        normalizar_dinheiro(linha.get('vl_total_conta')),
    )


def chave_item_lancamento_coalesce_demonstrativo(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_lancamento_dia_coalesce_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_data(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
    )


def chave_item_lancamento_coalesce_oracle(linha: Mapping) -> tuple:
    codigo_servico = linha.get('cd_pro_fat')
    if codigo_servico is None:
        codigo_servico = linha.get('cd_tuss')
    return (
        normalizar_mes_ano(linha['dt_lancamento']),
        normalizar_texto(codigo_servico),
        normalizar_carteira(linha['nr_carteira']),
    )


def chave_item_lancamento_dia_coalesce_oracle(linha: Mapping) -> tuple:
    codigo_servico = linha.get('cd_pro_fat')
    if codigo_servico is None:
        codigo_servico = linha.get('cd_tuss')
    return (
        normalizar_data(linha['dt_lancamento']),
        normalizar_texto(codigo_servico),
        normalizar_carteira(linha['nr_carteira']),
    )


def chave_item_competencia_coalesce_valor_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_dinheiro(linha['valor_processado']),
    )


def chave_item_competencia_coalesce_valor_oracle(linha: Mapping) -> tuple:
    codigo_servico = linha.get('cd_tuss')
    if codigo_servico is None:
        codigo_servico = linha.get('cd_pro_fat')
    return (
        normalizar_mes_ano(linha['dt_competencia']),
        normalizar_texto(codigo_servico),
        normalizar_dinheiro(linha.get('vl_total_conta')),
    )


def chave_item_atendimento_guia_coalesce_valor_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['numero_guia_senha']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_dinheiro(linha['valor_processado']),
    )


def chave_item_atendimento_guia_coalesce_valor_oracle(
    linha: Mapping,
) -> tuple:
    codigo_servico = linha.get('cd_tuss')
    if codigo_servico is None:
        codigo_servico = linha.get('cd_pro_fat')
    return (
        normalizar_mes_ano(linha['dt_atendimento']),
        normalizar_texto(linha['nr_guia']),
        normalizar_texto(codigo_servico),
        normalizar_dinheiro(linha.get('vl_total_conta')),
    )


def chave_item_atendimento_guia_servico_carteira_valor_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['numero_guia_senha']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
        normalizar_dinheiro(linha['valor_processado']),
    )


def chave_item_atendimento_guia_servico_carteira_valor_oracle(
    linha: Mapping,
) -> tuple:
    codigo_servico = linha.get('cd_tuss')
    if codigo_servico is None:
        codigo_servico = linha.get('cd_pro_fat')
    return (
        normalizar_mes_ano(linha['dt_atendimento']),
        normalizar_texto(linha['nr_guia']),
        normalizar_texto(codigo_servico),
        normalizar_carteira(linha['nr_carteira']),
        normalizar_dinheiro(linha.get('vl_total_conta')),
    )


def chave_item_lancamento_pro_fat_valor_demonstrativo(
    linha: Mapping,
) -> tuple:
    return (
        normalizar_mes_ano(linha['data_realizacao']),
        normalizar_texto(linha['codigo_servico']),
        normalizar_carteira(linha['codigo_beneficiario']),
        normalizar_dinheiro(linha['valor_processado']),
    )


def chave_item_lancamento_pro_fat_valor_oracle(linha: Mapping) -> tuple:
    return (
        normalizar_mes_ano(linha['dt_lancamento']),
        normalizar_texto(linha['cd_pro_fat']),
        normalizar_carteira(linha['nr_carteira']),
        normalizar_dinheiro(linha.get('vl_total_conta')),
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


def indexar_itens_oracle(  # noqa: PLR0912
    itens: Iterable[Mapping],
) -> IndicesItensOracle:
    por_guia: dict[tuple, list[Mapping]] = defaultdict(list)
    por_atendimento_guia_carteira: dict[tuple, list[Mapping]] = defaultdict(
        list
    )
    por_lancamento_dia: dict[tuple, list[Mapping]] = defaultdict(list)
    por_servico: dict[tuple, list[Mapping]] = defaultdict(list)
    por_tuss: dict[tuple, list[Mapping]] = defaultdict(list)
    por_lancamento: dict[tuple, list[Mapping]] = defaultdict(list)
    por_competencia_valor: dict[tuple, list[Mapping]] = defaultdict(list)
    por_atendimento_guia_valor: dict[tuple, list[Mapping]] = defaultdict(list)
    por_lancamento_pro_fat_valor: dict[tuple, list[Mapping]] = defaultdict(
        list
    )
    for item in itens:
        if item.get('cd_remessa') is None:
            continue
        senha = normalizar_texto(item.get('cd_senha'))
        guia = normalizar_texto(item.get('nr_guia'))
        item_por_senha = (
            {**item, 'nr_guia': item.get('cd_senha')}
            if senha and senha != guia
            else None
        )
        if normalizar_mes_ano(item.get('dt_competencia')) is not None:
            por_guia[chave_item_oracle(item)].append(item)
            if item_por_senha is not None:
                por_guia[chave_item_oracle(item_por_senha)].append(item)
            por_servico[chave_item_sem_guia_oracle(item)].append(item)
            if normalizar_texto(item.get('cd_tuss')):
                por_tuss[chave_item_sem_guia_tuss_oracle(item)].append(item)
        if (
            normalizar_data(item.get('dt_lancamento')) is not None
            and normalizar_texto(
                item.get('cd_pro_fat')
                if item.get('cd_pro_fat') is not None
                else item.get('cd_tuss')
            )
        ):
            por_lancamento_dia[
                chave_item_lancamento_dia_coalesce_oracle(item)
            ].append(item)
        codigo_coalesce_competencia = item.get('cd_tuss')
        if codigo_coalesce_competencia is None:
            codigo_coalesce_competencia = item.get('cd_pro_fat')
        if (
            normalizar_mes_ano(item.get('dt_competencia')) is not None
            and normalizar_texto(codigo_coalesce_competencia)
        ):
            por_competencia_valor[
                chave_item_competencia_coalesce_valor_oracle(item)
            ].append(item)
        if (
            normalizar_mes_ano(item.get('dt_atendimento')) is not None
            and normalizar_texto(item.get('nr_guia'))
            and normalizar_texto(codigo_coalesce_competencia)
        ):
            por_atendimento_guia_valor[
                chave_item_atendimento_guia_coalesce_valor_oracle(item)
            ].append(item)
            if normalizar_carteira(item.get('nr_carteira')):
                por_atendimento_guia_carteira[
                    chave_item_atendimento_guia_servico_carteira_valor_oracle(
                        item
                    )
                ].append(item)
        if (
            item_por_senha is not None
            and normalizar_mes_ano(item.get('dt_atendimento')) is not None
            and normalizar_texto(codigo_coalesce_competencia)
        ):
            por_atendimento_guia_valor[
                chave_item_atendimento_guia_coalesce_valor_oracle(
                    item_por_senha
                )
            ].append(item)
            if normalizar_carteira(item.get('nr_carteira')):
                por_atendimento_guia_carteira[
                    chave_item_atendimento_guia_servico_carteira_valor_oracle(
                        item_por_senha
                    )
                ].append(item)
        codigo_coalesce_lancamento = item.get('cd_pro_fat')
        if codigo_coalesce_lancamento is None:
            codigo_coalesce_lancamento = item.get('cd_tuss')
        if (
            normalizar_mes_ano(item.get('dt_lancamento')) is not None
            and normalizar_texto(codigo_coalesce_lancamento)
        ):
            por_lancamento[
                chave_item_lancamento_coalesce_oracle(item)
            ].append(item)
        if (
            normalizar_mes_ano(item.get('dt_lancamento')) is not None
            and normalizar_texto(item.get('cd_pro_fat'))
        ):
            por_lancamento_pro_fat_valor[
                chave_item_lancamento_pro_fat_valor_oracle(item)
            ].append(item)
    return IndicesItensOracle(
        competencia_guia_servico_carteira={
            chave: tuple(linhas) for chave, linhas in por_guia.items()
        },
        atendimento_guia_servico_carteira_valor={
            chave: tuple(linhas)
            for chave, linhas in por_atendimento_guia_carteira.items()
        },
        lancamento_dia_coalesce_servico_carteira={
            chave: tuple(linhas)
            for chave, linhas in por_lancamento_dia.items()
        },
        competencia_servico_carteira={
            chave: tuple(linhas) for chave, linhas in por_servico.items()
        },
        competencia_tuss_carteira={
            chave: tuple(linhas) for chave, linhas in por_tuss.items()
        },
        lancamento_coalesce_servico_carteira={
            chave: tuple(linhas) for chave, linhas in por_lancamento.items()
        },
        competencia_coalesce_servico_valor={
            chave: tuple(linhas)
            for chave, linhas in por_competencia_valor.items()
        },
        atendimento_guia_coalesce_servico_valor={
            chave: tuple(linhas)
            for chave, linhas in por_atendimento_guia_valor.items()
        },
        lancamento_pro_fat_carteira_valor={
            chave: tuple(linhas)
            for chave, linhas in por_lancamento_pro_fat_valor.items()
        },
    )


def resolver_correspondencia_item_oracle(  # noqa: PLR0912
    linha: Mapping,
    indices: IndicesItensOracle,
    cd_remessa_esperada: int | None = None,
    criterios_permitidos: set[str] | None = None,
) -> CorrespondenciaItemOracle:
    fontes: tuple[
        tuple[
            str,
            Mapping[tuple, tuple[Mapping, ...]],
            Callable[[Mapping], tuple],
        ],
        ...,
    ] = (
        (
            'competencia_guia_servico_carteira',
            indices.competencia_guia_servico_carteira,
            chave_item_demonstrativo,
        ),
        (
            'atendimento_guia_servico_carteira_valor',
            indices.atendimento_guia_servico_carteira_valor,
            chave_item_atendimento_guia_servico_carteira_valor_demonstrativo,
        ),
        (
            'lancamento_dia_coalesce_servico_carteira',
            indices.lancamento_dia_coalesce_servico_carteira,
            chave_item_lancamento_dia_coalesce_demonstrativo,
        ),
        (
            'competencia_servico_carteira',
            indices.competencia_servico_carteira,
            chave_item_sem_guia_demonstrativo,
        ),
        (
            'competencia_tuss_carteira',
            indices.competencia_tuss_carteira,
            chave_item_sem_guia_tuss_demonstrativo,
        ),
        (
            'lancamento_coalesce_servico_carteira',
            indices.lancamento_coalesce_servico_carteira,
            chave_item_lancamento_coalesce_demonstrativo,
        ),
        (
            'competencia_coalesce_servico_valor',
            indices.competencia_coalesce_servico_valor,
            chave_item_competencia_coalesce_valor_demonstrativo,
        ),
        (
            'atendimento_guia_coalesce_servico_valor',
            indices.atendimento_guia_coalesce_servico_valor,
            chave_item_atendimento_guia_coalesce_valor_demonstrativo,
        ),
        (
            'lancamento_pro_fat_carteira_valor',
            indices.lancamento_pro_fat_carteira_valor,
            chave_item_lancamento_pro_fat_valor_demonstrativo,
        ),
    )
    primeira_conta_unica = None
    primeira_ambiguidade = None
    primeira_divergencia = None
    for criterio, indice, gerar_chave in fontes:
        if (
            criterios_permitidos is not None
            and criterio not in criterios_permitidos
        ):
            continue
        itens = indice.get(gerar_chave(linha), ())
        if not itens:
            continue

        itens_por_remessa: dict[int, list[Mapping]] = defaultdict(list)
        for item in itens:
            itens_por_remessa[int(item['cd_remessa'])].append(item)

        seguras: list[tuple[int, ResolucaoItem, tuple[Mapping, ...]]] = []
        ambiguas = []
        for cd_remessa, itens_remessa in itens_por_remessa.items():
            resolucao = resolver_item(
                (
                    int(item['cd_reg']),
                    int(item['cd_lancamento']),
                )
                for item in itens_remessa
            )
            if resolucao.status in {'item_unico', 'conta_unica'}:
                seguras.append((cd_remessa, resolucao, tuple(itens_remessa)))
            elif resolucao.status == 'ambiguo':
                ambiguas.append(cd_remessa)

        remessas = tuple(
            sorted({item[0] for item in seguras} | set(ambiguas))
        )
        if len(seguras) == 1 and not ambiguas:
            cd_remessa, resolucao, itens_resolvidos = seguras[0]
            correspondencia = CorrespondenciaItemOracle(
                status=resolucao.status,
                criterio=criterio,
                cd_remessa=cd_remessa,
                resolucao=resolucao,
                itens=itens_resolvidos,
                remessas_candidatas=remessas,
            )
            if (
                cd_remessa_esperada is None
                or cd_remessa == cd_remessa_esperada
            ):
                if resolucao.status == 'item_unico':
                    return correspondencia
                if primeira_conta_unica is None:
                    primeira_conta_unica = correspondencia
                continue
            if primeira_divergencia is None:
                primeira_divergencia = correspondencia
            continue
        if primeira_ambiguidade is None:
            primeira_ambiguidade = CorrespondenciaItemOracle(
                status='ambiguo',
                criterio=criterio,
                itens=tuple(itens),
                remessas_candidatas=remessas,
            )

    return (
        primeira_conta_unica
        or primeira_divergencia
        or primeira_ambiguidade
        or CorrespondenciaItemOracle(status='nao_encontrado')
    )


def classificar_demonstrativos_sem_processo_por_oracle(
    demonstrativos: Iterable[Mapping],
    indices_itens_oracle: IndicesItensOracle,
) -> ClassificacaoSemProcessoOracle:
    identificadas = {}
    criterios = {}
    sem_correspondencia = []
    ambiguas = []
    for linha in demonstrativos:
        correspondencia = resolver_correspondencia_item_oracle(
            linha,
            indices_itens_oracle,
        )
        if correspondencia.cd_remessa is not None:
            id_registro = str(linha['id_registro'])
            identificadas[id_registro] = correspondencia.cd_remessa
            criterios[id_registro] = str(correspondencia.criterio)
        elif correspondencia.remessas_candidatas:
            ambiguas.append((linha, correspondencia.remessas_candidatas))
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
