from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, inspect, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app_prontocardio.database import oracle_engine, postgres_engine
from app_prontocardio.models import (
    AuditoriaConciliacaoFaturamento,
    ConciliacaoFaturamento,
    ConciliacaoFaturamentoRemessa,
    GlosaNaoVinculadaIpm,
    ModelContaAtendimento,
    ModelGruPro,
    ModelHpcContaBancaria,
    ModelProFat,
    NfseXml,
    ProcessoConciliacaoRemessa,
    RecebimentoRemessa,
    RegistroGlosa,
    RegistroGlosaDemonstrativoIpm,
    RemessaFinanceira,
    TipoAtendimento,
    Usuario,
)
from app_prontocardio.services.importacao_glosas_ipm import (
    AssociacaoDemonstrativo,
    ChaveProcesso,
    IndicesItensOracle,
    associar_processos_a_remessas,
    chave_conta_bancaria,
    classificar_demonstrativos_sem_processo_por_oracle,
    hash_nfse_ipm,
    indexar_itens_oracle,
    normalizar_competencia,
    normalizar_digitos,
    normalizar_dinheiro,
    normalizar_mes_ano,
    normalizar_texto,
    resolver_correspondencia_item_oracle,
)

DATA_INICIAL_PADRAO = date(2025, 12, 1)
DATA_FINAL_PADRAO = date(2026, 6, 30)
MESES_POR_ANO = 12
CONVENIO_IPM = 10


@dataclass(frozen=True)
class ItemGlosaPlano:
    conta: int
    cd_lancamento: int | None
    demonstrativos: tuple[dict, ...]
    itens_oracle: tuple[dict, ...]
    codigo_glosa: str | None = None
    criterios_correspondencia: tuple[str | None, ...] = ()

    @property
    def valor_glosa(self) -> Decimal:
        return normalizar_dinheiro(
            sum(
                (
                    normalizar_dinheiro(item['valor_glosa'])
                    for item in self.demonstrativos
                ),
                Decimal('0.00'),
            )
        )


@dataclass(frozen=True)
class RemessaPlano:
    processo: ChaveProcesso
    cd_remessa: int
    dados_oracle: dict
    itens_glosa: tuple[ItemGlosaPlano, ...]
    valor_glosa_nao_vinculada: Decimal = Decimal('0.00')

    @property
    def valor_total(self) -> Decimal:
        return normalizar_dinheiro(self.dados_oracle['valor_total'])

    @property
    def valor_glosado(self) -> Decimal:
        return normalizar_dinheiro(
            sum(
                (item.valor_glosa for item in self.itens_glosa),
                self.valor_glosa_nao_vinculada,
            )
        )

    @property
    def valor_recebido(self) -> Decimal:
        return normalizar_dinheiro(self.valor_total - self.valor_glosado)


@dataclass(frozen=True)
class ProcessoPlano:
    numero_processo: str
    nota: dict
    dados_processo: dict
    conta_bancaria_id: int
    remessas: tuple[RemessaPlano, ...]

    @property
    def valor_nfse(self) -> Decimal:
        return normalizar_dinheiro(
            sum(
                (remessa.valor_recebido for remessa in self.remessas),
                Decimal('0.00'),
            )
        )


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Audita e importa glosas do demonstrativo IPM, vinculando '
            'processos, remessas, NFS-e e recebimentos existentes.'
        )
    )
    parser.add_argument(
        '--data-inicial',
        type=date.fromisoformat,
        default=DATA_INICIAL_PADRAO,
    )
    parser.add_argument(
        '--data-final',
        type=date.fromisoformat,
        default=DATA_FINAL_PADRAO,
    )
    parser.add_argument('--usuario-id', type=int)
    parser.add_argument('--aplicar', action='store_true')
    parser.add_argument(
        '--substituir-carga-planilha',
        action='store_true',
        help=(
            'Simula ou aplica a substituição integral da carga anterior da '
            'planilha, preservando os demais dados de origem.'
        ),
    )
    parser.add_argument(
        '--dump-seguranca',
        type=Path,
        help=(
            'Dump completo já validado, obrigatório ao aplicar a '
            'substituição da carga anterior.'
        ),
    )
    parser.add_argument(
        '--confirmar-gravacao',
        action='store_true',
        help='Confirma explicitamente a gravação na conexão configurada.',
    )
    parser.add_argument(
        '--diretorio-relatorios',
        type=Path,
        default=None,
    )
    return parser.parse_args()


def _proximo_dia(data_final: date) -> date:
    return date.fromordinal(data_final.toordinal() + 1)


def _primeiro_dia_mes_seguinte(ano: int, mes: int) -> date:
    if mes == MESES_POR_ANO:
        return date(ano + 1, 1, 1)
    return date(ano, mes + 1, 1)


def _carregar_postgres(
    session: Session,
    data_inicial: date,
    data_final_exclusiva: date,
) -> dict:
    demonstrativos = (
        session
        .execute(
            text(
                """
            SELECT *
              FROM api_prontocardio.demonstrativo_processos_ipm
             WHERE referencia >= :inicio
               AND referencia < :fim
             ORDER BY referencia, id_registro
            """
            ),
            {'inicio': data_inicial, 'fim': data_final_exclusiva},
        )
        .mappings()
        .all()
    )
    demonstrativos_fisicos = (
        session
        .execute(
            text(
                """
            SELECT MIN(referencia) AS inicio, MAX(referencia) AS fim
              FROM api_prontocardio.demonstrativo_processos_ipm
            """
            )
        )
        .mappings()
        .one()
    )
    tabelas = (
        'processos_ipm',
        'processos_nota_fiscal_ipm',
        'processos_empenho_ipm',
    )
    resultado = {
        'demonstrativos': [dict(item) for item in demonstrativos],
        'inicio_fisico': demonstrativos_fisicos['inicio'],
        'fim_fisico': demonstrativos_fisicos['fim'],
    }
    for tabela in tabelas:
        resultado[tabela] = [
            dict(item)
            for item in session.execute(
                text(f'SELECT * FROM api_prontocardio.{tabela}')
            ).mappings()
        ]
    return resultado


def _associar_demonstrativos_da_view(
    demonstrativos: list[dict],
) -> tuple[AssociacaoDemonstrativo, set[ChaveProcesso]]:
    unicas: dict[str, ChaveProcesso] = {}
    sem_processo = []
    ambiguas = []
    processos_historicos: set[ChaveProcesso] = set()

    for linha in demonstrativos:
        status = normalizar_texto(linha.get('status_associacao'))
        if status.startswith('ASSOCIADO'):
            competencia = normalizar_competencia(
                linha.get('competencia_producao')
            )
            valor = linha.get('valor_protocolo_cogestao')
            numero = normalizar_texto(linha.get('numero_processo'))
            if competencia is None or valor is None or not numero:
                sem_processo.append(linha)
                continue
            chave = ChaveProcesso(
                numero_processo=numero,
                competencia=competencia,
                valor_protocolo=normalizar_dinheiro(valor),
            )
            unicas[str(linha['id_registro'])] = chave
            processos_historicos.add(chave)
            continue

        if status == 'AMBIGUO':
            candidatos = []
            for candidato in linha.get('candidatos_associacao') or []:
                competencia = normalizar_competencia(
                    candidato.get('competencia_producao')
                )
                valor = candidato.get('valor_protocolo')
                numero = normalizar_texto(candidato.get('numero_processo'))
                if competencia is None or valor is None or not numero:
                    continue
                chave = ChaveProcesso(
                    numero_processo=numero,
                    competencia=competencia,
                    valor_protocolo=normalizar_dinheiro(valor),
                )
                candidatos.append(chave)
                processos_historicos.add(chave)
            ambiguas.append((linha, tuple(sorted(set(candidatos)))))
            continue

        sem_processo.append(linha)

    return (
        AssociacaoDemonstrativo(
            unicas=unicas,
            sem_processo=tuple(sem_processo),
            ambiguas=tuple(ambiguas),
        ),
        processos_historicos,
    )


def _carregar_remessas_oracle(
    session: Session,
    cnpjs: set[str],
) -> tuple[dict[tuple[Decimal, str], set[int]], dict[int, dict]]:
    if not cnpjs:
        return {}, {}
    contas = (
        select(
            ModelContaAtendimento.cd_remessa.label('cd_remessa'),
            ModelContaAtendimento.cd_convenio.label('cd_convenio'),
            ModelContaAtendimento.cnpj_convenio.label('cnpj_convenio'),
            ModelContaAtendimento.nm_convenio.label('convenio'),
            ModelContaAtendimento.dt_competencia.label('competencia'),
            func.max(ModelContaAtendimento.vl_total_conta).label(
                'valor_total_conta'
            ),
            func.max(ModelContaAtendimento.vl_total_registro).label(
                'valor_total_registro'
            ),
        )
        .where(
            ModelContaAtendimento.cd_remessa.is_not(None),
            ModelContaAtendimento.sn_pertence_pacote == 'N',
            ModelContaAtendimento.cd_convenio == CONVENIO_IPM,
            func.regexp_replace(
                ModelContaAtendimento.cnpj_convenio,
                '[^0-9]',
                '',
            ).in_(cnpjs),
        )
        .group_by(
            ModelContaAtendimento.cd_remessa,
            ModelContaAtendimento.cd_convenio,
            ModelContaAtendimento.cnpj_convenio,
            ModelContaAtendimento.nm_convenio,
            ModelContaAtendimento.dt_competencia,
        )
        .subquery()
    )
    query = select(
        contas.c.cd_remessa,
        contas.c.cd_convenio,
        contas.c.cnpj_convenio,
        contas.c.convenio,
        func.sum(func.coalesce(contas.c.valor_total_conta, 0)).label(
            'valor_total'
        ),
        func.min(contas.c.competencia).label('competencia'),
    ).group_by(
        contas.c.cd_remessa,
        contas.c.cd_convenio,
        contas.c.cnpj_convenio,
        contas.c.convenio,
        func.to_char(contas.c.competencia, 'MM/YYYY'),
    )
    indice: dict[tuple[Decimal, str], set[int]] = defaultdict(set)
    dados = {}
    for linha in session.execute(query).mappings():
        item = dict(linha)
        codigo = int(item['cd_remessa'])
        item['cd_remessa'] = codigo
        item['cnpj_convenio'] = normalizar_digitos(item['cnpj_convenio'])
        item['valor_total'] = normalizar_dinheiro(item['valor_total'])
        competencia = item['competencia'].strftime('%m/%Y')
        indice[(item['valor_total'], competencia)].add(codigo)
        dados[codigo] = item
    return dict(indice), dados


def _carregar_itens_oracle(
    session: Session,
    cnpjs: set[str],
    competencias: set[tuple[int, int]],
) -> tuple[list[dict], dict[tuple[int, int, int], dict]]:
    if not cnpjs or not competencias:
        return [], {}
    ano_inicial, mes_inicial = min(competencias)
    ano_final, mes_final = max(competencias)
    data_inicial = date(ano_inicial, mes_inicial, 1)
    data_final_exclusiva = _primeiro_dia_mes_seguinte(
        ano_final,
        mes_final,
    )
    itens = []
    por_identidade = {}
    query = (
        select(
            ModelContaAtendimento.cd_remessa,
            ModelContaAtendimento.cd_reg,
            ModelContaAtendimento.cd_lancamento,
            ModelContaAtendimento.cd_atendimento,
            ModelContaAtendimento.cd_paciente,
            ModelContaAtendimento.nm_paciente,
            ModelContaAtendimento.cd_prestador,
            ModelContaAtendimento.nm_prestador,
            ModelContaAtendimento.cd_convenio,
            ModelContaAtendimento.cnpj_convenio,
            ModelContaAtendimento.nm_convenio,
            ModelContaAtendimento.tp_atendimento,
            ModelContaAtendimento.nr_guia,
            ModelContaAtendimento.nr_carteira,
            ModelContaAtendimento.cd_pro_fat,
            ModelContaAtendimento.cd_tuss,
            ModelContaAtendimento.descricao,
            ModelContaAtendimento.dt_atendimento,
            ModelContaAtendimento.dt_alta,
            ModelContaAtendimento.dt_competencia,
            ModelContaAtendimento.dt_lancamento,
            ModelContaAtendimento.qt_lancamento,
            ModelContaAtendimento.vl_total_conta,
            ModelContaAtendimento.cd_gru_fat,
            ModelContaAtendimento.ds_gru_fat,
            ModelGruPro.cd_gru_pro,
            ModelGruPro.ds_gru_pro,
        )
        .select_from(ModelContaAtendimento)
        .outerjoin(
            ModelProFat,
            ModelProFat.cd_pro_fat == ModelContaAtendimento.cd_pro_fat,
        )
        .outerjoin(
            ModelGruPro,
            ModelGruPro.cd_gru_pro == ModelProFat.cd_gru_pro,
        )
        .where(
            ModelContaAtendimento.cd_remessa.is_not(None),
            or_(
                (
                    ModelContaAtendimento.dt_competencia >= data_inicial
                )
                & (
                    ModelContaAtendimento.dt_competencia
                    < data_final_exclusiva
                ),
                (
                    ModelContaAtendimento.dt_lancamento >= data_inicial
                )
                & (
                    ModelContaAtendimento.dt_lancamento
                    < data_final_exclusiva
                ),
                (
                    ModelContaAtendimento.dt_atendimento >= data_inicial
                )
                & (
                    ModelContaAtendimento.dt_atendimento
                    < data_final_exclusiva
                ),
            ),
            func.regexp_replace(
                ModelContaAtendimento.cnpj_convenio,
                '[^0-9]',
                '',
            ).in_(cnpjs),
        )
    )
    for linha in session.execute(query).mappings():
        item = dict(linha)
        identidade = (
            int(item['cd_remessa']),
            int(item['cd_reg']),
            int(item['cd_lancamento']),
        )
        if identidade in por_identidade:
            continue
        por_identidade[identidade] = item
        itens.append(item)
    return itens, por_identidade


def _carregar_contas_oracle(
    session: Session,
) -> dict[tuple[str, str], set[int]]:
    indice: dict[tuple[str, str], set[int]] = defaultdict(set)
    query = select(
        ModelHpcContaBancaria.cd_con_cor,
        ModelHpcContaBancaria.cd_agencia.label('codigo_agencia'),
        ModelHpcContaBancaria.nr_conta.label('conta'),
    )
    for linha in session.execute(query).mappings():
        indice[chave_conta_bancaria(linha)].add(int(linha['cd_con_cor']))
    return dict(indice)


def _indexar_por_processo(linhas: list[dict]) -> dict[str, list[dict]]:
    indice: dict[str, list[dict]] = defaultdict(list)
    for linha in linhas:
        indice[normalizar_texto(linha['numero_processo'])].append(linha)
    return dict(indice)


def _resolver_contas_processos(
    empenhos_por_processo: dict[str, list[dict]],
    contas_oracle: dict[tuple[str, str], set[int]],
) -> tuple[dict[str, int], dict[str, tuple[int, ...]], set[str]]:
    unicas = {}
    ambiguas = {}
    nao_encontradas = set()
    for processo, empenhos in empenhos_por_processo.items():
        contas = set()
        for empenho in empenhos:
            contas.update(
                contas_oracle.get(chave_conta_bancaria(empenho), set())
            )
        if len(contas) == 1:
            unicas[processo] = next(iter(contas))
        elif contas:
            ambiguas[processo] = tuple(sorted(contas))
        else:
            nao_encontradas.add(processo)
    return unicas, ambiguas, nao_encontradas


def _notas_unicas(
    notas_por_processo: dict[str, list[dict]],
) -> tuple[dict[str, dict], dict[str, tuple[str, ...]], set[str]]:
    unicas = {}
    ambiguas = {}
    sem_numero = set()
    for processo, notas in notas_por_processo.items():
        por_numero = {
            normalizar_texto(nota['numero_nfse']): nota
            for nota in notas
            if normalizar_texto(nota['numero_nfse'])
        }
        if len(por_numero) == 1:
            unicas[processo] = next(iter(por_numero.values()))
        elif por_numero:
            ambiguas[processo] = tuple(sorted(por_numero))
        else:
            sem_numero.add(processo)
    return unicas, ambiguas, sem_numero


def _gravar_csv(
    caminho: Path,
    cabecalhos: list[str],
    linhas: list[dict],
) -> None:
    with caminho.open('w', encoding='utf-8-sig', newline='') as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=cabecalhos,
            extrasaction='ignore',
            delimiter=';',
        )
        escritor.writeheader()
        escritor.writerows(linhas)


def _linha_processo_relatorio(
    processo: ChaveProcesso,
    remessas: tuple[int, ...] = (),
    no_escopo: bool = False,
) -> dict:
    return {
        'numero_processo': processo.numero_processo,
        'competencia_producao': processo.competencia.isoformat(),
        'valor_protocolo': str(processo.valor_protocolo),
        'remessas_candidatas': ','.join(str(item) for item in remessas),
        'quantidade_candidatas': len(remessas),
        'no_escopo_importacao': 'sim' if no_escopo else 'não',
    }


def _gerar_relatorios(  # noqa: PLR0913
    diretorio: Path,
    demonstrativos_sem_processo_iniciais: tuple,
    classificacao_sem_processo_oracle,
    associacao_historica,
    processos_escopo: set[ChaveProcesso],
    itens_sem_correspondencia: list[dict],
    exclusoes: list[dict],
    resumo: dict,
) -> None:
    diretorio.mkdir(parents=True, exist_ok=True)
    campos_demo = [
        'id_registro',
        'referencia',
        'data_realizacao',
        'numero_protocolo',
        'valor_protocolo',
        'valor_processado',
        'numero_guia_senha',
        'codigo_servico',
        'codigo_beneficiario',
        'codigo_glosa',
        'valor_glosa',
        'cnpj_operadora',
    ]
    _gravar_csv(
        diretorio / 'linhas_sem_processo.csv',
        campos_demo,
        [
            dict(item)
            for item in classificacao_sem_processo_oracle.sem_correspondencia
        ],
    )
    _gravar_csv(
        diretorio / 'linhas_localizadas_no_oracle.csv',
        [*campos_demo, 'cd_remessa', 'situacao'],
        [
            {
                **dict(item),
                'cd_remessa': classificacao_sem_processo_oracle.identificadas[
                    str(item['id_registro'])
                ],
                'situacao': (
                    {
                        'competencia_guia_servico_carteira': (
                            'competência (MM/AAAA), guia, CD_PRO_FAT e '
                            'carteira localizados; remessa obtida do item '
                            'Oracle'
                        ),
                        'competencia_servico_carteira': (
                            'competência (MM/AAAA), CD_PRO_FAT e carteira '
                            'localizados; remessa obtida do item Oracle; '
                            'guia desconsiderada'
                        ),
                        'competencia_tuss_carteira': (
                            'competência (MM/AAAA), CD_TUSS, carteira e '
                            'VL_TOTAL_CONTA/valor processado localizados; '
                            'remessa obtida do item Oracle; guia e '
                            'CD_PRO_FAT desconsiderados'
                        ),
                        'lancamento_coalesce_servico_carteira': (
                            'data de realização/data de lançamento '
                            '(MM/AAAA), COALESCE(CD_PRO_FAT, CD_TUSS) e '
                            'carteira localizados; remessa obtida do item '
                            'Oracle; guia e competência desconsideradas'
                        ),
                        'competencia_coalesce_servico_valor': (
                            'competência (MM/AAAA), '
                            'COALESCE(CD_TUSS, CD_PRO_FAT) e '
                            'VL_TOTAL_CONTA/valor processado localizados; '
                            'remessa obtida do item Oracle; guia e carteira '
                            'desconsideradas'
                        ),
                        'atendimento_guia_coalesce_servico_valor': (
                            'data de realização/data de atendimento '
                            '(MM/AAAA), guia, '
                            'COALESCE(CD_TUSS, CD_PRO_FAT) e '
                            'VL_TOTAL_CONTA/valor processado localizados; '
                            'remessa obtida do item Oracle; competência e '
                            'carteira desconsideradas'
                        ),
                        'lancamento_pro_fat_carteira_valor': (
                            'data de realização/data de lançamento '
                            '(MM/AAAA), CD_PRO_FAT, carteira e '
                            'VL_TOTAL_CONTA/valor processado localizados; '
                            'remessa obtida do item Oracle; guia e '
                            'competência desconsideradas'
                        ),
                    }[
                        classificacao_sem_processo_oracle.criterios[
                            str(item['id_registro'])
                        ]
                    ]
                    + '; processo IPM não localizado por protocolo, '
                    'competência (MM/AAAA) e valor de desempate'
                ),
            }
            for item in demonstrativos_sem_processo_iniciais
            if str(item['id_registro'])
            in classificacao_sem_processo_oracle.identificadas
        ],
    )
    _gravar_csv(
        diretorio / 'linhas_correspondencia_oracle_ambigua.csv',
        [*campos_demo, 'remessas_candidatas', 'quantidade_candidatas'],
        [
            {
                **dict(item),
                'remessas_candidatas': ','.join(
                    str(remessa) for remessa in remessas
                ),
                'quantidade_candidatas': len(remessas),
            }
            for item, remessas in classificacao_sem_processo_oracle.ambiguas
        ],
    )
    campos_processo = [
        'numero_processo',
        'competencia_producao',
        'valor_protocolo',
        'remessas_candidatas',
        'quantidade_candidatas',
        'no_escopo_importacao',
    ]
    _gravar_csv(
        diretorio / 'associacoes_ambiguas.csv',
        campos_processo,
        [
            _linha_processo_relatorio(
                processo,
                remessas,
                processo in processos_escopo,
            )
            for processo, remessas in associacao_historica.ambiguas
        ],
    )
    _gravar_csv(
        diretorio / 'remessas_nao_encontradas.csv',
        campos_processo,
        [
            _linha_processo_relatorio(
                processo,
                no_escopo=processo in processos_escopo,
            )
            for processo in associacao_historica.nao_encontradas
        ],
    )
    _gravar_csv(
        diretorio / 'itens_sem_correspondencia.csv',
        [*campos_demo, 'numero_processo', 'cd_remessa', 'motivo'],
        itens_sem_correspondencia,
    )
    _gravar_csv(
        diretorio / 'remessas_excluidas_importacao.csv',
        [
            'numero_processo',
            'cd_remessa',
            'competencia_producao',
            'valor_protocolo',
            'motivo',
        ],
        exclusoes,
    )
    (diretorio / 'resumo.json').write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2, default=str) + '\n',
        encoding='utf-8',
    )


def _existentes_postgres(
    session: Session,
    remessas: set[int],
) -> tuple[set[int], set[str]]:
    if not remessas:
        return set(), set()
    conciliadas = set(
        session.scalars(
            select(ConciliacaoFaturamentoRemessa.cd_remessa)
            .join(
                ConciliacaoFaturamento,
                ConciliacaoFaturamento.id
                == ConciliacaoFaturamentoRemessa.conciliacao_id,
            )
            .where(
                ConciliacaoFaturamento.ativo.is_(True),
                ConciliacaoFaturamentoRemessa.cd_remessa.in_(remessas),
            )
            .distinct()
        )
    )
    conciliadas.update(
        session.scalars(
            select(RemessaFinanceira.cd_remessa).where(
                RemessaFinanceira.cd_remessa.in_(remessas)
            )
        )
    )
    importados = set()
    if inspect(session.connection()).has_table(
        'registros_glosa_demonstrativo_ipm',
        schema='api_prontocardio',
    ):
        importados = set(
            session.scalars(select(RegistroGlosaDemonstrativoIpm.id_registro))
        )
    return conciliadas, importados


def _auditar_carga_substituivel(session: Session) -> dict[str, int]:
    totais = {
        'conciliacoes_faturamento': int(
            session.scalar(select(func.count(ConciliacaoFaturamento.id))) or 0
        ),
        'conciliacoes_faturamento_remessas': int(
            session.scalar(
                select(func.count(ConciliacaoFaturamentoRemessa.id))
            )
            or 0
        ),
        'registros_glosa': int(
            session.scalar(select(func.count(RegistroGlosa.id))) or 0
        ),
        'registros_glosa_triagem': int(
            session.scalar(
                select(func.count(RegistroGlosa.id)).where(
                    RegistroGlosa.origem_registro == 'triagem'
                )
            )
            or 0
        ),
        'registros_glosa_sem_vinculo': int(
            session.scalar(
                select(func.count(RegistroGlosa.id)).where(
                    RegistroGlosa.conciliacao_remessa_id.is_(None)
                )
            )
            or 0
        ),
        'recebimentos_remessas': int(
            session.scalar(select(func.count(RecebimentoRemessa.id))) or 0
        ),
        'processos_conciliacao_remessa': int(
            session.scalar(
                select(func.count(ProcessoConciliacaoRemessa.id))
            )
            or 0
        ),
        'remessas_financeiras': int(
            session.scalar(select(func.count(RemessaFinanceira.cd_remessa)))
            or 0
        ),
    }
    if (
        totais['registros_glosa_triagem'] > 0
        or totais['registros_glosa_sem_vinculo'] > 0
    ):
        raise RuntimeError(
            'Substituição recusada: existem registros de glosa manuais ou '
            'desvinculados da carga de conciliação.'
        )
    return totais


def _garantir_tabela_rastreio_ipm(session: Session) -> None:
    RegistroGlosaDemonstrativoIpm.__table__.create(
        bind=session.connection(),
        checkfirst=True,
    )


def _remover_carga_planilha_anterior(session: Session) -> dict[str, int]:
    exclusoes = (
        ('registros_glosa_demonstrativo_ipm', RegistroGlosaDemonstrativoIpm),
        ('registros_glosa', RegistroGlosa),
        ('recebimentos_remessas', RecebimentoRemessa),
        (
            'auditorias_conciliacao_faturamento',
            AuditoriaConciliacaoFaturamento,
        ),
        ('conciliacoes_faturamento_remessas', ConciliacaoFaturamentoRemessa),
        ('conciliacoes_faturamento', ConciliacaoFaturamento),
        ('processos_conciliacao_remessa', ProcessoConciliacaoRemessa),
        ('remessas_financeiras', RemessaFinanceira),
    )
    removidos = {}
    for nome, modelo in exclusoes:
        resultado = session.execute(delete(modelo))
        removidos[nome] = int(resultado.rowcount or 0)
    return removidos


def _sincronizar_glosas_nao_vinculadas(
    session: Session,
    pendencias: list[dict],
    ids_vinculados: set[str],
) -> int:
    ids_pendentes = {str(item['id_registro']) for item in pendencias}
    ids_resolvidos = ids_vinculados - ids_pendentes
    if ids_resolvidos:
        session.execute(
            delete(GlosaNaoVinculadaIpm).where(
                GlosaNaoVinculadaIpm.id_registro.in_(ids_resolvidos)
            )
        )

    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    for item in pendencias:
        valores = {
            'id_registro': str(item['id_registro']),
            'numero_processo': str(item['numero_processo']),
            'cd_remessa': int(item['cd_remessa']),
            'motivo': str(item['motivo']),
            'criterio_correspondencia': item.get(
                'criterio_correspondencia'
            ),
            'remessas_candidatas': item.get('remessas_candidatas') or [],
            'numero_protocolo': item.get('numero_protocolo'),
            'data_realizacao': item.get('data_realizacao'),
            'numero_guia_senha': item.get('numero_guia_senha'),
            'codigo_servico': item.get('codigo_servico'),
            'codigo_beneficiario': item.get('codigo_beneficiario'),
            'codigo_glosa': item.get('codigo_glosa'),
            'valor_processado': item.get('valor_processado'),
            'valor_glosa': normalizar_dinheiro(item['valor_glosa']),
            'data_ultima_tentativa': agora,
        }
        comando = pg_insert(GlosaNaoVinculadaIpm).values(**valores)
        session.execute(
            comando.on_conflict_do_update(
                index_elements=[GlosaNaoVinculadaIpm.id_registro],
                set_={
                    chave: valor
                    for chave, valor in valores.items()
                    if chave != 'id_registro'
                },
            )
        )
    return len(pendencias)


def _preparar_itens_glosa(  # noqa: PLR0913
    demonstrativos: list[dict],
    processos_demo: dict[str, ChaveProcesso],
    remessas_processos: dict[ChaveProcesso, int],
    indices_itens_oracle: IndicesItensOracle,
    itens_por_identidade: dict[tuple[int, int, int], dict],
) -> tuple[dict[int, tuple[ItemGlosaPlano, ...]], list[dict], set[str]]:
    agrupados: dict[
        tuple[int, int, int | None, str | None],
        list[tuple[dict, tuple, str | None]],
    ] = defaultdict(list)
    sem_correspondencia = []
    avaliados = set()
    for demonstrativo in demonstrativos:
        if normalizar_dinheiro(demonstrativo['valor_glosa']) <= 0:
            continue
        processo = processos_demo.get(str(demonstrativo['id_registro']))
        if processo is None:
            continue
        remessa_processo = remessas_processos.get(processo)
        if remessa_processo is None:
            continue
        avaliados.add(str(demonstrativo['id_registro']))
        correspondencia = resolver_correspondencia_item_oracle(
            demonstrativo,
            indices_itens_oracle,
            cd_remessa_esperada=remessa_processo,
        )
        motivo = None
        if correspondencia.status in {'nao_encontrado', 'ambiguo'}:
            motivo = correspondencia.status
        elif correspondencia.cd_remessa != remessa_processo:
            motivo = 'remessa_divergente'
        if motivo is not None:
            sem_correspondencia.append({
                **demonstrativo,
                'numero_processo': processo.numero_processo,
                'cd_remessa': remessa_processo,
                'motivo': motivo,
                'criterio_correspondencia': correspondencia.criterio,
                'remessas_candidatas': list(
                    correspondencia.remessas_candidatas
                ),
            })
            continue
        resolucao = correspondencia.resolucao
        if resolucao is None:
            raise RuntimeError('Correspondência Oracle sem resolução do item.')
        chave_grupo = (
            remessa_processo,
            int(resolucao.conta),
            resolucao.cd_lancamento,
            normalizar_texto(demonstrativo.get('codigo_glosa')) or None,
        )
        agrupados[chave_grupo].append(
            (
                demonstrativo,
                resolucao.candidatos,
                correspondencia.criterio,
            )
        )

    por_remessa: dict[int, list[ItemGlosaPlano]] = defaultdict(list)
    for (
        cd_remessa,
        conta,
        lancamento,
        codigo_glosa,
    ), linhas in agrupados.items():
        identidades = {
            (cd_remessa, conta, item_lancamento)
            for _, candidatos, _ in linhas
            for item_conta, item_lancamento in candidatos
            if item_conta == conta
        }
        itens_oracle = tuple(
            itens_por_identidade[item]
            for item in sorted(identidades)
            if item in itens_por_identidade
        )
        por_remessa[cd_remessa].append(
            ItemGlosaPlano(
                conta=conta,
                cd_lancamento=lancamento,
                codigo_glosa=codigo_glosa,
                demonstrativos=tuple(item[0] for item in linhas),
                itens_oracle=itens_oracle,
                criterios_correspondencia=tuple(
                    item[2] for item in linhas
                ),
            )
        )
    return (
        {
            remessa: tuple(
                sorted(
                    itens,
                    key=lambda item: (
                        item.conta,
                        item.cd_lancamento or -1,
                        item.codigo_glosa or '',
                    ),
                )
            )
            for remessa, itens in por_remessa.items()
        },
        sem_correspondencia,
        avaliados,
    )


def _preparar_plano(  # noqa: PLR0912, PLR0913, PLR0915
    associacao_escopo,
    dados_remessas: dict[int, dict],
    itens_glosa: dict[int, tuple[ItemGlosaPlano, ...]],
    glosas_nao_vinculadas: list[dict],
    processos: dict[str, dict],
    notas: dict[str, dict],
    contas_bancarias: dict[str, int],
    remessas_conciliadas: set[int],
) -> tuple[tuple[ProcessoPlano, ...], list[dict]]:
    remessas_por_processo: dict[str, list[RemessaPlano]] = defaultdict(list)
    exclusoes = []
    glosa_nao_vinculada_por_remessa: dict[int, Decimal] = defaultdict(
        lambda: Decimal('0.00')
    )
    for item in glosas_nao_vinculadas:
        glosa_nao_vinculada_por_remessa[int(item['cd_remessa'])] += (
            normalizar_dinheiro(item['valor_glosa'])
        )
    for chave, cd_remessa in associacao_escopo.unicas.items():
        motivo = None
        processo = processos.get(chave.numero_processo)
        if cd_remessa in remessas_conciliadas:
            motivo = 'remessa já modelada ou conciliada'
        elif processo is None:
            motivo = 'processo não encontrado em processos_ipm'
        elif chave.numero_processo not in notas:
            motivo = 'NFS-e ausente ou ambígua no processo'
        elif chave.numero_processo not in contas_bancarias:
            motivo = 'conta bancária ausente ou ambígua no Oracle'
        elif (
            normalizar_texto(processo['status_processo']) == 'FINALIZADO'
            and processo['data_abertura'] is None
        ):
            motivo = 'processo finalizado sem data de abertura'
        dados_remessa = dados_remessas.get(cd_remessa)
        if dados_remessa is None:
            motivo = motivo or 'dados da remessa não encontrados no Oracle'
        if motivo is None:
            remessa = RemessaPlano(
                processo=chave,
                cd_remessa=cd_remessa,
                dados_oracle=dados_remessa,
                itens_glosa=itens_glosa.get(cd_remessa, ()),
                valor_glosa_nao_vinculada=normalizar_dinheiro(
                    glosa_nao_vinculada_por_remessa[cd_remessa]
                ),
            )
            if remessa.valor_glosado > remessa.valor_total:
                motivo = 'valor glosado maior que o total da remessa'
            else:
                remessas_por_processo[chave.numero_processo].append(remessa)
        if motivo is not None:
            exclusoes.append({
                'numero_processo': chave.numero_processo,
                'cd_remessa': cd_remessa,
                'competencia_producao': chave.competencia,
                'valor_protocolo': chave.valor_protocolo,
                'motivo': motivo,
            })

    plano = []
    for numero_processo, remessas in sorted(remessas_por_processo.items()):
        plano.append(
            ProcessoPlano(
                numero_processo=numero_processo,
                nota=notas[numero_processo],
                dados_processo=processos[numero_processo],
                conta_bancaria_id=contas_bancarias[numero_processo],
                remessas=tuple(
                    sorted(remessas, key=lambda item: item.cd_remessa)
                ),
            )
        )
    return tuple(plano), exclusoes


def _valor_item_oracle(item: ItemGlosaPlano) -> Decimal:
    valor = sum(
        (
            normalizar_dinheiro(linha['vl_total_conta'])
            for linha in item.itens_oracle
        ),
        Decimal('0.00'),
    )
    return max(normalizar_dinheiro(valor), item.valor_glosa)


def _quantidade_item_oracle(item: ItemGlosaPlano) -> Decimal:
    quantidade = sum(
        (Decimal(linha['qt_lancamento'] or 0) for linha in item.itens_oracle),
        Decimal('0.00'),
    )
    return max(quantidade, Decimal('1.00'))


def _resolver_nfse_row_hash(session: Session, nota_processo: dict) -> str:
    numero_nfse = normalizar_texto(nota_processo.get('numero_nfse'))
    cnpj_prestador = normalizar_digitos(
        nota_processo.get('cnpj_cpf_nif_prestador')
    )
    if numero_nfse:
        candidatas = session.scalars(
            select(NfseXml).where(
                or_(
                    NfseXml.numero_nfse == numero_nfse,
                    func.ltrim(NfseXml.numero_nfse, '0')
                    == (numero_nfse.lstrip('0') or '0'),
                )
            )
        ).all()
        nfse = next(
            (
                candidata
                for candidata in candidatas
                if not cnpj_prestador
                or normalizar_digitos(candidata.prestador_cnpj)
                == cnpj_prestador
            ),
            candidatas[0] if len(candidatas) == 1 else None,
        )
        if nfse is not None:
            return nfse.row_hash
    return hash_nfse_ipm(nota_processo['id_registro'])


def _criar_registro_glosa(  # noqa: PLR0913
    session: Session,
    plano: ProcessoPlano,
    remessa: RemessaPlano,
    vinculo: ConciliacaoFaturamentoRemessa,
    item: ItemGlosaPlano,
    agora: datetime,
) -> None:
    origem = item.itens_oracle[0]
    descricoes = sorted({
        str(linha['descricao_servico']).strip()
        for linha in item.demonstrativos
        if str(linha.get('descricao_servico') or '').strip()
    })
    datas_glosa = [
        linha.get('data_envio_lote') or linha['referencia']
        for linha in item.demonstrativos
    ]
    finalizado = (
        normalizar_texto(plano.dados_processo['status_processo'])
        == 'FINALIZADO'
    )
    registro = RegistroGlosa(
        codigo_paciente=int(origem['cd_paciente'] or 0),
        nm_paciente=origem['nm_paciente'],
        cd_remessa=remessa.cd_remessa,
        cd_atendimento=int(origem['cd_atendimento'] or 0),
        conta=item.conta,
        cd_prestador=int(origem['cd_prestador'] or 0),
        cd_convenio=int(origem['cd_convenio'] or 0),
        tp_atendimento=(
            origem['tp_atendimento'] or TipoAtendimento.EXTERNO.value
        ),
        procedimento=str(origem['cd_pro_fat'] or '-'),
        convenio=origem['nm_convenio'] or 'Convênio não informado',
        guia=str(origem['nr_guia'] or '-'),
        prestador=origem['nm_prestador'] or 'Prestador não informado',
        data_atendimento=(
            origem['dt_atendimento'] or origem['dt_lancamento'] or agora
        ),
        valor=_valor_item_oracle(item),
        processo_controle_fatura_gab=plano.numero_processo,
        processo_recurso=None,
        data_glosa=max(datas_glosa),
        motivo_glosa=(
            item.codigo_glosa
            or next(
                (
                    normalizar_texto(linha.get('codigo_glosa'))
                    for linha in item.demonstrativos
                    if normalizar_texto(linha.get('codigo_glosa'))
                ),
                None,
            )
        ),
        descricao_glosa=(
            f'{"; ".join(descricoes) or "Item do demonstrativo IPM"}. '
            f'Valor glosado na origem: R$ {item.valor_glosa}.'
        ),
        qtd_recursado=None,
        valor_recursado=None,
        dt_recurso=None,
        dt_pagamento=(
            plano.dados_processo['data_abertura'] if finalizado else None
        ),
        dt_recebimento=None,
        valor_recebido=None,
        qtd_recebida=None,
        observacao_recebimento=None,
        cd_lancamento=item.cd_lancamento,
        qtd_registro=_quantidade_item_oracle(item),
        descricao_item=origem['descricao'],
        data_alta=origem['dt_alta'],
        data_lancamento=origem['dt_lancamento'],
        cd_gru_pro=int(origem['cd_gru_pro'] or 0),
        ds_gru_pro=origem['ds_gru_pro'] or 'Grupo não informado',
        cd_gru_fat=int(origem['cd_gru_fat'] or 0),
        ds_gru_fat=origem['ds_gru_fat'] or 'Grupo não informado',
        cd_tuss=(
            str(origem.get('cd_tuss')).strip()
            if origem.get('cd_tuss')
            else None
        ),
        conciliacao_remessa_id=vinculo.id,
        origem_registro='conciliacao',
        sn_glosado='true',
        sn_ativo='true',
    )
    registro.data_criacao = agora
    session.add(registro)
    session.flush()
    criterios = item.criterios_correspondencia or tuple(
        None for _ in item.demonstrativos
    )
    for demonstrativo, criterio in zip(
        item.demonstrativos,
        criterios,
        strict=True,
    ):
        rastreio = RegistroGlosaDemonstrativoIpm(
            id_registro=str(demonstrativo['id_registro']),
            registro_glosa_id=registro.id,
            criterio_correspondencia=criterio,
        )
        rastreio.data_importacao = agora
        session.add(rastreio)


def _aplicar_plano(
    session: Session,
    plano: tuple[ProcessoPlano, ...],
    usuario_id: int,
) -> dict:
    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    totais = defaultdict(int)
    for processo in plano:
        data_abertura = processo.dados_processo['data_abertura']
        finalizado = (
            normalizar_texto(processo.dados_processo['status_processo'])
            == 'FINALIZADO'
        )
        conciliacao = ConciliacaoFaturamento(
            nfse_row_hash=_resolver_nfse_row_hash(session, processo.nota),
            numero_nfse=str(processo.nota['numero_nfse']).strip(),
            cnpj_convenio=processo.remessas[0].dados_oracle['cnpj_convenio'],
            convenio=processo.remessas[0].dados_oracle['convenio'],
            valor_nfse=processo.valor_nfse,
            impostos=Decimal('0.00'),
            processo_recebimento=processo.numero_processo,
            data_previsao_recebimento=data_abertura,
            usuario_id=usuario_id,
            data_recebimento=data_abertura if finalizado else None,
            conta_bancaria_id=(
                processo.conta_bancaria_id if finalizado else None
            ),
            conta_plano_contas=None,
            conta_centro_custo=None,
            lancamento_extrato_id=None,
        )
        conciliacao.data_criacao = agora
        session.add(conciliacao)
        session.flush()
        vinculos = []
        for remessa in processo.remessas:
            financeira = RemessaFinanceira(
                cd_remessa=remessa.cd_remessa,
                convenio=remessa.dados_oracle['convenio'],
                cnpj_convenio=remessa.dados_oracle['cnpj_convenio'],
                valor_total=remessa.valor_total,
                recebimento_integral=(
                    finalizado and remessa.valor_glosado == 0
                ),
                data_competencia=remessa.dados_oracle.get(
                    'competencia', remessa.processo.competencia
                ),
            )
            financeira.data_registro = agora
            session.add(financeira)
            session.flush()
            processo_remessa = ProcessoConciliacaoRemessa(
                cd_remessa=remessa.cd_remessa,
                processo_recebimento=processo.numero_processo,
                usuario_id=usuario_id,
                usuario_atualizacao_id=None,
                data_atualizacao=None,
            )
            processo_remessa.data_criacao = agora
            session.add(processo_remessa)
            session.flush()
            vinculo = ConciliacaoFaturamentoRemessa(
                conciliacao_id=conciliacao.id,
                cd_remessa=remessa.cd_remessa,
                convenio=remessa.dados_oracle['convenio'],
                cnpj_convenio=remessa.dados_oracle['cnpj_convenio'],
                valor_total=remessa.valor_total,
                sn_glosado='true' if remessa.valor_glosado > 0 else 'not',
                valor_glosado=remessa.valor_glosado,
                tp_conciliacao='faturamento',
                processo_remessa_id=processo_remessa.id,
                valor_alocado_nfse=remessa.valor_recebido,
                valor_impostos=Decimal('0.00'),
            )
            session.add(vinculo)
            session.flush()
            vinculos.append(vinculo)
            for item in remessa.itens_glosa:
                _criar_registro_glosa(
                    session,
                    processo,
                    remessa,
                    vinculo,
                    item,
                    agora,
                )
                totais['registros_glosa'] += 1
                totais['linhas_demonstrativo'] += len(item.demonstrativos)
            if finalizado and remessa.valor_recebido > 0:
                recebimento = RecebimentoRemessa(
                    cd_remessa=remessa.cd_remessa,
                    conciliacao_id=conciliacao.id,
                    numero_nfse=conciliacao.numero_nfse,
                    data_recebimento=data_abertura,
                    valor_recebido=remessa.valor_recebido,
                    usuario_id=usuario_id,
                    conta_bancaria_id=processo.conta_bancaria_id,
                    recebimento_integral=remessa.valor_glosado == 0,
                    conta_plano_contas=None,
                    conta_centro_custo=None,
                    lancamento_extrato_id=None,
                )
                recebimento.data_registro = agora
                session.add(recebimento)
                totais['recebimentos'] += 1
            totais['remessas'] += 1
        auditoria = AuditoriaConciliacaoFaturamento(
            conciliacao_id=conciliacao.id,
            acao='importacao_demonstrativo_ipm',
            usuario_id=usuario_id,
            dados_anteriores=None,
            dados_novos={
                'origem': 'demonstrativo_conta_ipm',
                'numero_processo': processo.numero_processo,
                'numero_nfse': conciliacao.numero_nfse,
                'remessas': [item.cd_remessa for item in processo.remessas],
                'conta_bancaria_id': processo.conta_bancaria_id,
                'data_recebimento': (
                    str(data_abertura) if finalizado else None
                ),
            },
        )
        auditoria.data_operacao = agora
        session.add(auditoria)
        totais['conciliacoes'] += 1
    session.commit()
    return dict(totais)


def main() -> None:  # noqa: PLR0915
    args = _argumentos()
    if args.data_final < args.data_inicial:
        raise RuntimeError(
            'A data final deve ser igual ou posterior à inicial.'
        )
    if postgres_engine is None:
        raise RuntimeError('DATABASE_URL não configurada.')
    fim_exclusivo = _proximo_dia(args.data_final)
    instante = datetime.now().strftime('%Y%m%d_%H%M%S')
    diretorio = args.diretorio_relatorios or Path(
        f'relatorios/importacao_glosas_ipm_{instante}'
    )

    with Session(postgres_engine) as session_postgres:
        fontes = _carregar_postgres(
            session_postgres,
            args.data_inicial,
            fim_exclusivo,
        )
        associacao_demo, processos_historicos = (
            _associar_demonstrativos_da_view(fontes['demonstrativos'])
        )
        processos_escopo = set(associacao_demo.unicas.values())
        cnpjs = {
            normalizar_digitos(item['cnpj_operadora'])
            for item in fontes['demonstrativos']
            if normalizar_digitos(item['cnpj_operadora'])
        }
        with Session(oracle_engine) as session_oracle:
            indice_remessas, dados_remessas = _carregar_remessas_oracle(
                session_oracle,
                cnpjs,
            )
            associacao_escopo = associar_processos_a_remessas(
                processos_escopo,
                indice_remessas,
            )
            associacao_historica = associar_processos_a_remessas(
                processos_historicos,
                indice_remessas,
            )
            competencias_demonstrativo = {
                competencia
                for item in fontes['demonstrativos']
                if (competencia := normalizar_mes_ano(item['data_realizacao']))
                is not None
            }
            itens_oracle, itens_por_identidade = _carregar_itens_oracle(
                session_oracle,
                cnpjs,
                competencias_demonstrativo,
            )
            indices_itens_oracle = indexar_itens_oracle(itens_oracle)
            classificacao_sem_processo_oracle = (
                classificar_demonstrativos_sem_processo_por_oracle(
                    associacao_demo.sem_processo,
                    indices_itens_oracle,
                )
            )
            contas_oracle = _carregar_contas_oracle(session_oracle)

        itens_glosa, itens_sem_correspondencia, _ids_avaliados = (
            _preparar_itens_glosa(
                fontes['demonstrativos'],
                associacao_demo.unicas,
                associacao_escopo.unicas,
                indices_itens_oracle,
                itens_por_identidade,
            )
        )
        processos = {
            normalizar_texto(item['numero_processo']): item
            for item in fontes['processos_ipm']
        }
        notas_por_processo = _indexar_por_processo(
            fontes['processos_nota_fiscal_ipm']
        )
        notas, notas_ambiguas, notas_sem_numero = _notas_unicas(
            notas_por_processo
        )
        empenhos_por_processo = _indexar_por_processo(
            fontes['processos_empenho_ipm']
        )
        contas, contas_ambiguas, contas_nao_encontradas = (
            _resolver_contas_processos(
                empenhos_por_processo,
                contas_oracle,
            )
        )
        remessas_conciliadas, importados = _existentes_postgres(
            session_postgres,
            set(associacao_escopo.unicas.values()),
        )
        remessas_existentes = remessas_conciliadas
        importados_existentes = importados
        carga_anterior = None
        if args.substituir_carga_planilha:
            carga_anterior = _auditar_carga_substituivel(session_postgres)
            remessas_conciliadas = set()
            importados = set()
        plano, exclusoes = _preparar_plano(
            associacao_escopo,
            dados_remessas,
            itens_glosa,
            itens_sem_correspondencia,
            processos,
            notas,
            contas,
            remessas_conciliadas,
        )
        numeros_processos_escopo = {
            item.numero_processo for item in processos_escopo
        }
        resumo = {
            'periodo': {
                'data_inicial': args.data_inicial,
                'data_final': args.data_final,
            },
            'linhas_demonstrativo': len(fontes['demonstrativos']),
            'linhas_glosa_positiva': sum(
                normalizar_dinheiro(item['valor_glosa']) > 0
                for item in fontes['demonstrativos']
            ),
            'linhas_sem_associacao_processo_ipm': len(
                associacao_demo.sem_processo
            ),
            'linhas_localizadas_no_oracle': len(
                classificacao_sem_processo_oracle.identificadas
            ),
            'linhas_localizadas_chave_competencia_guia_servico_carteira': sum(
                criterio == 'competencia_guia_servico_carteira'
                for criterio in (
                    classificacao_sem_processo_oracle.criterios.values()
                )
            ),
            'linhas_localizadas_chave_competencia_servico_carteira': sum(
                criterio == 'competencia_servico_carteira'
                for criterio in (
                    classificacao_sem_processo_oracle.criterios.values()
                )
            ),
            'linhas_localizadas_chave_competencia_tuss_carteira': sum(
                criterio == 'competencia_tuss_carteira'
                for criterio in (
                    classificacao_sem_processo_oracle.criterios.values()
                )
            ),
            'linhas_localizadas_chave_lancamento_coalesce_servico_carteira': (
                sum(
                    criterio == 'lancamento_coalesce_servico_carteira'
                    for criterio in (
                        classificacao_sem_processo_oracle.criterios.values()
                    )
                )
            ),
            'linhas_localizadas_chave_competencia_coalesce_servico_valor': (
                sum(
                    criterio == 'competencia_coalesce_servico_valor'
                    for criterio in (
                        classificacao_sem_processo_oracle.criterios.values()
                    )
                )
            ),
            (
                'linhas_localizadas_chave_atendimento_'
                'guia_coalesce_servico_valor'
            ): (
                sum(
                    criterio == 'atendimento_guia_coalesce_servico_valor'
                    for criterio in (
                        classificacao_sem_processo_oracle.criterios.values()
                    )
                )
            ),
            'linhas_localizadas_chave_lancamento_pro_fat_carteira_valor': sum(
                criterio == 'lancamento_pro_fat_carteira_valor'
                for criterio in (
                    classificacao_sem_processo_oracle.criterios.values()
                )
            ),
            'linhas_correspondencia_oracle_ambigua': len(
                classificacao_sem_processo_oracle.ambiguas
            ),
            'linhas_sem_processo': len(
                classificacao_sem_processo_oracle.sem_correspondencia
            ),
            'linhas_processo_ambiguo': len(associacao_demo.ambiguas),
            'processos_remessa_unica': len(associacao_escopo.unicas),
            'associacoes_ambiguas_escopo': len(associacao_escopo.ambiguas),
            'remessas_nao_encontradas_escopo': len(
                associacao_escopo.nao_encontradas
            ),
            'associacoes_ambiguas_relatorio_historico': len(
                associacao_historica.ambiguas
            ),
            'remessas_nao_encontradas_relatorio_historico': len(
                associacao_historica.nao_encontradas
            ),
            'itens_sem_correspondencia': len(itens_sem_correspondencia),
            'remessas_com_itens_nao_vinculados': len({
                int(item['cd_remessa'])
                for item in itens_sem_correspondencia
            }),
            'notas_ambiguas': len(
                set(notas_ambiguas) & numeros_processos_escopo
            ),
            'notas_sem_numero': len(
                notas_sem_numero & numeros_processos_escopo
            ),
            'contas_ambiguas': len(
                set(contas_ambiguas) & numeros_processos_escopo
            ),
            'contas_nao_encontradas': len(
                contas_nao_encontradas & numeros_processos_escopo
            ),
            'remessas_ja_modeladas_ou_conciliadas': len(remessas_existentes),
            'linhas_ja_importadas': len(importados_existentes),
            'substituicao_carga_planilha': args.substituir_carga_planilha,
            'carga_anterior_a_substituir': carga_anterior,
            'processos_planejados': len(plano),
            'remessas_planejadas': sum(len(item.remessas) for item in plano),
            'registros_glosa_planejados': sum(
                len(remessa.itens_glosa)
                for processo in plano
                for remessa in processo.remessas
            ),
            'linhas_demonstrativo_planejadas': sum(
                len(item.demonstrativos)
                for processo in plano
                for remessa in processo.remessas
                for item in remessa.itens_glosa
            ),
            'valor_glosa_planejado': normalizar_dinheiro(
                sum(
                    (
                        remessa.valor_glosado
                        for processo in plano
                        for remessa in processo.remessas
                    ),
                    Decimal('0.00'),
                )
            ),
        }
        _gerar_relatorios(
            diretorio,
            associacao_demo.sem_processo,
            classificacao_sem_processo_oracle,
            associacao_historica,
            processos_escopo,
            itens_sem_correspondencia,
            exclusoes,
            resumo,
        )
        print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
        print(f'Relatórios gerados em {diretorio.resolve()}')
        if not args.aplicar:
            print('Simulação concluída; nenhuma alteração foi gravada.')
            return
        if not args.confirmar_gravacao:
            raise RuntimeError(
                'Use --confirmar-gravacao para autorizar a gravação.'
            )
        if args.usuario_id is None:
            raise RuntimeError('Informe --usuario-id para aplicar a carga.')
        if session_postgres.get(Usuario, args.usuario_id) is None:
            raise RuntimeError(f'Usuário {args.usuario_id} não encontrado.')
        if args.substituir_carga_planilha:
            if args.dump_seguranca is None:
                raise RuntimeError(
                    'Informe --dump-seguranca para aplicar a substituição.'
                )
            if not args.dump_seguranca.is_file():
                raise RuntimeError(
                    f'Dump de segurança não encontrado: {args.dump_seguranca}'
                )
            if not plano:
                raise RuntimeError(
                    'Substituição recusada porque o novo plano está vazio.'
                )
            _auditar_carga_substituivel(session_postgres)
            _garantir_tabela_rastreio_ipm(session_postgres)
            removidos = _remover_carga_planilha_anterior(session_postgres)
            print(
                'Carga anterior removida na transação: '
                + ', '.join(
                    f'{chave}={valor}' for chave, valor in removidos.items()
                )
            )
        elif not inspect(session_postgres.connection()).has_table(
            'registros_glosa_demonstrativo_ipm',
            schema='api_prontocardio',
        ):
            raise RuntimeError(
                'Execute as migrações antes de aplicar a importação.'
            )
        ids_vinculados = set(importados)
        ids_vinculados.update(
            str(demonstrativo['id_registro'])
            for processo in plano
            for remessa in processo.remessas
            for item in remessa.itens_glosa
            for demonstrativo in item.demonstrativos
        )
        totais_pendentes = _sincronizar_glosas_nao_vinculadas(
            session_postgres,
            itens_sem_correspondencia,
            ids_vinculados,
        )
        totais = _aplicar_plano(session_postgres, plano, args.usuario_id)
        totais['glosas_nao_vinculadas'] = totais_pendentes
        print(
            'Importação concluída: '
            + ', '.join(f'{chave}={valor}' for chave, valor in totais.items())
        )


if __name__ == '__main__':
    main()
