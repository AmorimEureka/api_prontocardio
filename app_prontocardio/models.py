from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, registry, relationship

from app_prontocardio.settings import Settings

table_registry = registry()
settings = Settings()


class TipoAtendimento(str, Enum):
    AMBULATORIO = 'Ambulatório'
    EXTERNO = 'Externo'
    URGENCIA = 'Urgência'
    INTERNACAO = 'Internação'


class LocalSolicitacaoNota(str, Enum):
    CLINICA_1 = 'Clinica 1'
    CLINICA_2 = 'Clinica 2'
    EMERGENCIA = 'Emergencia'


class StatusWorkflowSolicitacao(str, Enum):
    PENDENTE_VALIDACAO = 'PENDENTE_VALIDACAO'
    RECUSADA = 'RECUSADA'
    VALIDADA = 'VALIDADA'
    EMISSAO_SOLICITADA = 'EMISSAO_SOLICITADA'
    EMITIDA = 'EMITIDA'
    ERRO_EMISSAO = 'ERRO_EMISSAO'


class DecisaoValidacaoSolicitacao(str, Enum):
    VALIDADA = 'VALIDADA'
    RECUSADA = 'RECUSADA'


class TipoLoteEmissaoNfse(str, Enum):
    INDIVIDUAL = 'INDIVIDUAL'
    LOTE = 'LOTE'


class StatusEmissaoNfse(str, Enum):
    PENDENTE = 'PENDENTE'
    PROCESSANDO = 'PROCESSANDO'
    EMITIDA = 'EMITIDA'
    ERRO = 'ERRO'


@table_registry.mapped_as_dataclass
class Usuario:
    __tablename__ = 'usuarios_api'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    nome: Mapped[str] = mapped_column(String, unique=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    senha: Mapped[str]
    perfil: Mapped[str] = mapped_column(
        String(20), default='usuario', server_default=text("'usuario'")
    )
    ativo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text('true')
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False, server_default=func.now()
    )


@table_registry.mapped_as_dataclass
class TokenRedefinicaoSenha:
    __tablename__ = 'tokens_redefinicao_senha'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    utilizado: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text('false')
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False, server_default=func.now()
    )


@table_registry.mapped_as_dataclass
class RegistroGlosa:
    __tablename__ = 'registros_glosa'
    __table_args__ = (
        CheckConstraint(
            "origem_registro IN ('triagem', 'conciliacao')",
            name='ck_registros_glosa_origem',
        ),
        CheckConstraint(
            'conciliacao_remessa_id IS NULL OR '
            "origem_registro = 'conciliacao'",
            name='ck_registros_glosa_origem_vinculo',
        ),
        UniqueConstraint(
            'conciliacao_remessa_id',
            'conta',
            'cd_lancamento',
            'sn_glosado',
            name='uq_registro_glosa_conciliacao_item',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    codigo_paciente: Mapped[int]
    nm_paciente: Mapped[str | None] = mapped_column(String, nullable=True)
    cd_remessa: Mapped[int]
    cd_atendimento: Mapped[int]
    conta: Mapped[int]
    cd_prestador: Mapped[int]
    cd_convenio: Mapped[int]
    tp_atendimento: Mapped[TipoAtendimento] = mapped_column(String)
    procedimento: Mapped[str] = mapped_column(String)
    convenio: Mapped[str] = mapped_column(String)
    guia: Mapped[str] = mapped_column(String)
    prestador: Mapped[str] = mapped_column(String)
    data_atendimento: Mapped[datetime]
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    processo_controle_fatura_gab: Mapped[str] = mapped_column(String)
    processo_recurso: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    data_glosa: Mapped[date] = mapped_column(Date)
    motivo_glosa: Mapped[str] = mapped_column(String)
    descricao_glosa: Mapped[str] = mapped_column(String)
    qtd_recursado: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    valor_recursado: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    dt_recurso: Mapped[date | None] = mapped_column(Date, nullable=True)
    dt_pagamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    dt_recebimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    valor_recebido: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    qtd_recebida: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    observacao_recebimento: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    cd_lancamento: Mapped[int | None] = mapped_column(default=None)
    qtd_registro: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        default=None,
    )
    descricao_item: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    data_alta: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
    )
    data_lancamento: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
    )
    cd_gru_pro: Mapped[int | None] = mapped_column(default=None)
    ds_gru_pro: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    cd_gru_fat: Mapped[int | None] = mapped_column(default=None)
    ds_gru_fat: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    conciliacao_remessa_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.'
            'conciliacoes_faturamento_remessas.id',
            ondelete='SET NULL',
        ),
        default=None,
    )
    origem_registro: Mapped[str] = mapped_column(
        String(20),
        default='triagem',
        server_default=text("'triagem'"),
    )
    sn_glosado: Mapped[str] = mapped_column(String, default='true')
    sn_ativo: Mapped[str] = mapped_column(String, default='true')
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )
    conciliacao_remessa: Mapped[ConciliacaoFaturamentoRemessa | None] = (
        relationship(
            back_populates='registros_glosa',
            init=False,
        )
    )

    @property
    def valor_glosa_origem(self) -> Decimal | None:
        if self.conciliacao_remessa is None:
            return self.valor_recursado
        return self.conciliacao_remessa.valor_glosado

    @property
    def valor_glosa_pendente(self) -> Decimal | None:
        if self.conciliacao_remessa is None:
            return None
        valor_alocado = sum(
            (
                registro.valor_recursado
                for registro in self.conciliacao_remessa.registros_glosa
                if registro.sn_ativo == 'true'
                and registro.valor_recursado is not None
            ),
            start=Decimal('0.00'),
        )
        return max(
            self.conciliacao_remessa.valor_glosado - valor_alocado,
            Decimal('0.00'),
        )

    @property
    def status_tratativa(self) -> str:
        if self.dt_recurso is not None and (
            self.sn_glosado == 'not' or self.processo_recurso is not None
        ):
            return 'acato' if self.sn_glosado == 'not' else 'recurso'
        return 'pendente'

    @property
    def valor_indicador(self) -> Decimal:
        if self.status_tratativa != 'pendente':
            return self.valor_recursado or Decimal('0.00')
        if self.conciliacao_remessa is None:
            return self.valor_recursado or Decimal('0.00')

        registros_ativos = [
            registro
            for registro in self.conciliacao_remessa.registros_glosa
            if registro.sn_ativo == 'true'
        ]
        valor_tratado = sum(
            (
                registro.valor_recursado
                for registro in registros_ativos
                if registro.status_tratativa != 'pendente'
                and registro.valor_recursado is not None
            ),
            start=Decimal('0.00'),
        )
        registros_pendentes = [
            registro
            for registro in registros_ativos
            if registro.status_tratativa == 'pendente'
        ]
        if not registros_pendentes:
            return Decimal('0.00')
        primeiro_pendente = min(
            registros_pendentes,
            key=lambda registro: registro.id or 0,
        )
        if self is not primeiro_pendente:
            return Decimal('0.00')
        return max(
            self.conciliacao_remessa.valor_glosado - valor_tratado,
            Decimal('0.00'),
        )


@table_registry.mapped_as_dataclass
class PrazoRecursoConvenio:
    __tablename__ = 'prazos_recurso_convenio'
    __table_args__ = (
        UniqueConstraint('cd_convenio', name='uq_prazos_recurso_cd_convenio'),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    cd_convenio: Mapped[int]
    convenio: Mapped[str] = mapped_column(String)
    dias_para_recurso: Mapped[int]
    habilitado: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text('true'),
    )
    data_atualizacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )


@table_registry.mapped_as_dataclass
class NfseXml:
    """Nota fiscal importada pelo pipeline do ISS Fortaleza."""

    __tablename__ = 'nfse_xml'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    row_hash: Mapped[str] = mapped_column(String, primary_key=True, init=False)
    data_hora: Mapped[datetime | None] = mapped_column(DateTime, init=False)
    numero_nfse: Mapped[str | None] = mapped_column(String, init=False)
    prestador_cnpj: Mapped[str | None] = mapped_column(String, init=False)
    prestador_razao_social: Mapped[str | None] = mapped_column(
        String,
        init=False,
    )
    tomador_cnpj: Mapped[str | None] = mapped_column(String, init=False)
    tomador_razao_social: Mapped[str | None] = mapped_column(
        String,
        init=False,
    )
    valor_pis: Mapped[str | None] = mapped_column(String, init=False)
    valor_cofins: Mapped[str | None] = mapped_column(String, init=False)
    valor_csll: Mapped[str | None] = mapped_column(String, init=False)
    valor_ir: Mapped[str | None] = mapped_column(String, init=False)
    outras_retencoes: Mapped[str | None] = mapped_column(String, init=False)
    valor_liquido_nfse: Mapped[str | None] = mapped_column(String, init=False)
    cancelamento_codigo: Mapped[str | None] = mapped_column(String, init=False)


@table_registry.mapped_as_dataclass
class LancamentoExtratoBancario:
    __tablename__ = 'lancamentos_extrato_bancario'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    conta_bancaria_id: Mapped[int]
    data_lancamento: Mapped[date] = mapped_column(Date)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    descricao: Mapped[str | None] = mapped_column(String, default=None)
    documento: Mapped[str | None] = mapped_column(String, default=None)
    conciliado: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text('false'),
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )


@table_registry.mapped_as_dataclass
class ConciliacaoFaturamento:
    __tablename__ = 'conciliacoes_faturamento'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    nfse_row_hash: Mapped[str] = mapped_column(String)
    numero_nfse: Mapped[str] = mapped_column(String)
    cnpj_convenio: Mapped[str] = mapped_column(String)
    convenio: Mapped[str] = mapped_column(String)
    valor_nfse: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    impostos: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    processo_recebimento: Mapped[str] = mapped_column(String)
    data_previsao_recebimento: Mapped[date] = mapped_column(Date)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    data_recebimento: Mapped[date | None] = mapped_column(
        Date,
        default=None,
    )
    conta_bancaria_id: Mapped[int | None] = mapped_column(default=None)
    conta_plano_contas: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    conta_centro_custo: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    lancamento_extrato_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.lancamentos_extrato_bancario.id'
        ),
        default=None,
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )
    ativo: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text('true'),
    )
    usuario_atualizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id'),
        default=None,
    )
    data_atualizacao: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
    )
    usuario_inativacao_id: Mapped[int | None] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id'),
        default=None,
    )
    data_inativacao: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
    )


@table_registry.mapped_as_dataclass
class ProcessoConciliacaoRemessa:
    __tablename__ = 'processos_conciliacao_remessa'
    __table_args__ = (
        UniqueConstraint(
            'cd_remessa',
            name='uq_processos_conciliacao_remessa_codigo',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    cd_remessa: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.remessas_financeiras.cd_remessa'
        )
    )
    processo_recebimento: Mapped[str] = mapped_column(String)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )
    usuario_atualizacao_id: Mapped[int | None] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id'),
        default=None,
    )
    data_atualizacao: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=None,
    )


@table_registry.mapped_as_dataclass
class AuditoriaConciliacaoFaturamento:
    __tablename__ = 'auditorias_conciliacao_faturamento'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    conciliacao_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.conciliacoes_faturamento.id',
            ondelete='CASCADE',
        )
    )
    acao: Mapped[str] = mapped_column(String(40))
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    dados_anteriores: Mapped[dict | None] = mapped_column(
        JSON,
        default=None,
    )
    dados_novos: Mapped[dict | None] = mapped_column(
        JSON,
        default=None,
    )
    data_operacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )


@table_registry.mapped_as_dataclass
class ConciliacaoFaturamentoRemessa:
    __tablename__ = 'conciliacoes_faturamento_remessas'
    __table_args__ = (
        UniqueConstraint(
            'conciliacao_id',
            'cd_remessa',
            name='uq_conciliacoes_remessas_conciliacao_remessa',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    conciliacao_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.conciliacoes_faturamento.id',
            ondelete='CASCADE',
        )
    )
    cd_remessa: Mapped[int]
    convenio: Mapped[str] = mapped_column(String)
    cnpj_convenio: Mapped[str] = mapped_column(String)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    sn_glosado: Mapped[str] = mapped_column(
        String,
        default='not',
        server_default=text("'not'"),
    )
    valor_glosado: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=Decimal('0.00'),
        server_default=text('0'),
    )
    tp_conciliacao: Mapped[str] = mapped_column(
        String,
        default='faturamento',
        server_default=text("'faturamento'"),
    )
    processo_remessa_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.processos_conciliacao_remessa.id',
            ondelete='CASCADE',
        ),
        default=None,
    )
    valor_alocado_nfse: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=Decimal('0.00'),
        server_default=text('0'),
    )
    registros_glosa: Mapped[list[RegistroGlosa]] = relationship(
        back_populates='conciliacao_remessa',
        init=False,
    )


@table_registry.mapped_as_dataclass
class RemessaFinanceira:
    __tablename__ = 'remessas_financeiras'
    __table_args__ = (
        CheckConstraint(
            'valor_total >= 0',
            name='ck_remessas_financeiras_valor_total',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    cd_remessa: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=False,
    )
    convenio: Mapped[str] = mapped_column(String)
    cnpj_convenio: Mapped[str] = mapped_column(String)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    recebimento_integral: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text('false'),
    )
    data_competencia: Mapped[date | None] = mapped_column(
        Date,
        default=None,
    )
    data_registro: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )
    recebimentos: Mapped[list['RecebimentoRemessa']] = relationship(
        back_populates='remessa',
        init=False,
    )


@table_registry.mapped_as_dataclass
class RecebimentoRemessa:
    __tablename__ = 'recebimentos_remessas'
    __table_args__ = (
        CheckConstraint(
            'valor_recebido > 0',
            name='ck_recebimentos_remessas_valor_positivo',
        ),
        ForeignKeyConstraint(
            ['conciliacao_id', 'cd_remessa'],
            [
                f'{settings.POSTGRES_SCHEMA}.'
                'conciliacoes_faturamento_remessas.conciliacao_id',
                f'{settings.POSTGRES_SCHEMA}.'
                'conciliacoes_faturamento_remessas.cd_remessa',
            ],
            name='fk_recebimento_conciliacao_remessa',
            ondelete='CASCADE',
        ),
        Index(
            'ix_recebimentos_remessas_conciliacao_remessa',
            'conciliacao_id',
            'cd_remessa',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    cd_remessa: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.remessas_financeiras.cd_remessa'
        )
    )
    conciliacao_id: Mapped[int]
    numero_nfse: Mapped[str] = mapped_column(String)
    data_recebimento: Mapped[date] = mapped_column(Date)
    valor_recebido: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    conta_bancaria_id: Mapped[int]
    recebimento_integral: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text('false'),
    )
    conta_plano_contas: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    conta_centro_custo: Mapped[str | None] = mapped_column(
        String,
        default=None,
    )
    lancamento_extrato_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.lancamentos_extrato_bancario.id'
        ),
        default=None,
    )
    data_registro: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
    )
    remessa: Mapped[RemessaFinanceira] = relationship(
        back_populates='recebimentos',
        init=False,
    )
    conciliacao_remessa: Mapped[ConciliacaoFaturamentoRemessa] = relationship(
        init=False,
        viewonly=True,
    )
    usuario: Mapped[Usuario] = relationship(init=False)
    lancamento_extrato: Mapped[LancamentoExtratoBancario | None] = (
        relationship(init=False)
    )


@table_registry.mapped_as_dataclass
class Tiss:
    __tablename__ = 'tiss'
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

    codigo_termo: Mapped[str] = mapped_column(String, primary_key=True)
    termo: Mapped[str] = mapped_column(String, init=False)
    dt_inicio_vigencia: Mapped[date | None] = mapped_column(Date, init=False)
    dt_fim_vigencia: Mapped[date | None] = mapped_column(Date, init=False)
    dt_fim_implantacao: Mapped[date | None] = mapped_column(Date, init=False)
    fonte: Mapped[str] = mapped_column(String, init=False)
    pagina_pdf: Mapped[int] = mapped_column(init=False)
    data_criacao: Mapped[datetime] = mapped_column(
        'created_at',
        init=False,
        server_default=func.now(),
    )


@table_registry.mapped_as_dataclass
class SolicitacaoNota:
    __tablename__ = 'solicitacao_nota'
    __table_args__ = (
        CheckConstraint(
            "local IN ('Clinica 1', 'Clinica 2', 'Emergencia')",
            name='ck_solicitacao_nota_local',
        ),
        CheckConstraint(
            'valor_nota IS NULL OR valor_nota > 0',
            name='ck_solicitacao_nota_valor_nota',
        ),
        Index(
            'ix_solicitacao_nota_codigo_atendimento',
            'codigo_atendimento',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    codigo_atendimento: Mapped[int]
    codigo_paciente: Mapped[int]
    codigo_convenio: Mapped[int]
    nm_paciente: Mapped[str] = mapped_column(String(200))
    convenio: Mapped[str] = mapped_column(String(100))
    local: Mapped[str] = mapped_column(String(20))
    procedimento: Mapped[str] = mapped_column(String(500))
    tipo_atendimento: Mapped[str] = mapped_column(String(50))
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    valor_nota: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        default=None,
    )
    nr_cpf: Mapped[str | None] = mapped_column(String(20), default=None)
    nr_cep: Mapped[str | None] = mapped_column(String(20), default=None)
    ds_endereco: Mapped[str | None] = mapped_column(
        String(200),
        default=None,
    )
    nr_endereco: Mapped[str | None] = mapped_column(String(30), default=None)
    nm_bairro: Mapped[str | None] = mapped_column(
        String(100),
        default=None,
    )
    ds_complemento: Mapped[str | None] = mapped_column(
        String(100),
        default=None,
    )
    email: Mapped[str | None] = mapped_column(String(150), default=None)
    nr_fone: Mapped[str | None] = mapped_column(String(50), default=None)
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
    )


@table_registry.mapped_as_dataclass
class SolicitacaoNotaWorkflow:
    __tablename__ = 'solicitacao_nota_workflow'
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'PENDENTE_VALIDACAO', 'RECUSADA', 'VALIDADA', "
            "'EMISSAO_SOLICITADA', 'EMITIDA', 'ERRO_EMISSAO'"
            ')',
            name='ck_solicitacao_nota_workflow_status',
        ),
        CheckConstraint(
            "validacao IS NULL OR validacao IN ('VALIDADA', 'RECUSADA')",
            name='ck_solicitacao_nota_workflow_validacao',
        ),
        Index(
            'ix_solicitacao_nota_workflow_status',
            'status',
            'solicitacao_nota_id',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    solicitacao_nota_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.solicitacao_nota.id',
            ondelete='CASCADE',
        ),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(30))
    validacao: Mapped[str | None] = mapped_column(
        String(20),
        default=None,
    )
    motivo_recusa: Mapped[str | None] = mapped_column(
        String(500),
        default=None,
    )
    validado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id'),
        default=None,
    )
    validado_em: Mapped[datetime | None] = mapped_column(default=None)
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
    )
    data_atualizacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@table_registry.mapped_as_dataclass
class SolicitacaoNotaEvento:
    __tablename__ = 'solicitacao_nota_evento'
    __table_args__ = (
        Index(
            'ix_solicitacao_nota_evento_solicitacao',
            'solicitacao_nota_id',
            'data_criacao',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    solicitacao_nota_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.solicitacao_nota.id',
            ondelete='CASCADE',
        )
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    tipo_acao: Mapped[str] = mapped_column(String(40))
    observacao: Mapped[str | None] = mapped_column(
        String(500),
        default=None,
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
    )


@table_registry.mapped_as_dataclass
class LoteEmissaoNfse:
    __tablename__ = 'lote_emissao_nfse'
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('INDIVIDUAL', 'LOTE')",
            name='ck_lote_emissao_nfse_tipo',
        ),
        CheckConstraint(
            "status IN ('PENDENTE', 'PROCESSANDO', 'EMITIDA', 'ERRO')",
            name='ck_lote_emissao_nfse_status',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    tipo: Mapped[str] = mapped_column(String(20))
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    status: Mapped[str] = mapped_column(String(20))
    dag_run_id: Mapped[str | None] = mapped_column(
        String(250),
        default=None,
    )
    airflow_disparado_em: Mapped[datetime | None] = mapped_column(
        default=None,
    )
    erro_disparo: Mapped[str | None] = mapped_column(
        String(1000),
        default=None,
    )
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
    )


@table_registry.mapped_as_dataclass
class EmissaoNfse:
    __tablename__ = 'emissao_nfse'
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDENTE', 'PROCESSANDO', 'EMITIDA', 'ERRO')",
            name='ck_emissao_nfse_status',
        ),
        Index(
            'ix_emissao_nfse_status',
            'status',
            'solicitacao_nota_id',
        ),
        {'schema': settings.POSTGRES_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    solicitacao_nota_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.solicitacao_nota.id',
            ondelete='RESTRICT',
        )
    )
    lote_id: Mapped[int] = mapped_column(
        ForeignKey(
            f'{settings.POSTGRES_SCHEMA}.lote_emissao_nfse.id',
            ondelete='RESTRICT',
        )
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey(f'{settings.POSTGRES_SCHEMA}.usuarios_api.id')
    )
    status: Mapped[str] = mapped_column(String(20))
    numero_nfse: Mapped[str | None] = mapped_column(
        String(100),
        default=None,
    )
    protocolo: Mapped[str | None] = mapped_column(
        String(200),
        default=None,
    )
    erro: Mapped[str | None] = mapped_column(String(1000), default=None)
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
    )
    data_atualizacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@table_registry.mapped_as_dataclass
class ModelConvenio:
    __tablename__ = 'CONVENIO'
    __table_args__ = {'schema': 'DBAMV'}

    cd_convenio: Mapped[int] = mapped_column(primary_key=True, init=False)
    nm_convenio: Mapped[str] = mapped_column(String, init=False)
    sn_ativo: Mapped[str] = mapped_column(String, init=False)


@table_registry.mapped_as_dataclass
class ModelHpcConvenio:
    __tablename__ = 'HPC_V_CONVENIOS'
    __table_args__ = {'schema': 'DBAMV'}

    cd_convenio: Mapped[int] = mapped_column(primary_key=True, init=False)
    cnpj_convenio: Mapped[str | None] = mapped_column(String, init=False)
    nm_convenio: Mapped[str] = mapped_column(String, init=False)


@table_registry.mapped_as_dataclass
class ModelHpcContaBancaria:
    __tablename__ = 'HPC_V_CONTAS_BANCARIAS'
    __table_args__ = {'schema': 'DBAMV'}

    cd_con_cor: Mapped[int] = mapped_column(primary_key=True, init=False)
    ds_con_cor: Mapped[str] = mapped_column(String, init=False)
    cd_agencia: Mapped[str] = mapped_column(String, init=False)
    cd_digito_agencia: Mapped[str | None] = mapped_column(String, init=False)
    nr_conta: Mapped[str] = mapped_column(String, init=False)
    cd_digito_conta_corrente: Mapped[str | None] = mapped_column(
        String,
        init=False,
    )


@table_registry.mapped_as_dataclass
class ModelProFat:
    __tablename__ = 'PRO_FAT'
    __table_args__ = {'schema': 'DBAMV'}

    cd_pro_fat: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        init=False,
    )
    cd_gru_pro: Mapped[int | None] = mapped_column(init=False)


@table_registry.mapped_as_dataclass
class ModelGruPro:
    __tablename__ = 'GRU_PRO'
    __table_args__ = {'schema': 'DBAMV'}

    cd_gru_pro: Mapped[int] = mapped_column(primary_key=True, init=False)
    ds_gru_pro: Mapped[str | None] = mapped_column(String, init=False)


@table_registry.mapped_as_dataclass
class ModelContaAtendimento:
    __tablename__ = 'HPC_V_CONTA_ATENDIMENTO'
    __table_args__ = {'schema': 'DBAMV'}

    cd_reg: Mapped[int] = mapped_column(primary_key=True, init=False)
    cd_lancamento: Mapped[int] = mapped_column(primary_key=True, init=False)
    cd_atendimento: Mapped[int | None] = mapped_column(init=False)
    cd_paciente: Mapped[int | None] = mapped_column(init=False)
    nm_paciente: Mapped[str | None] = mapped_column(String, init=False)
    cd_remessa: Mapped[int | None] = mapped_column(init=False)
    cd_regra: Mapped[int | None] = mapped_column(init=False)
    ds_regra: Mapped[str | None] = mapped_column(String, init=False)
    cd_convenio: Mapped[int | None] = mapped_column(init=False)
    cnpj_convenio: Mapped[str | None] = mapped_column(String, init=False)
    nm_convenio: Mapped[str | None] = mapped_column(String, init=False)
    cd_gru_fat: Mapped[int | None] = mapped_column(init=False)
    ds_gru_fat: Mapped[str | None] = mapped_column(String, init=False)
    cd_pro_fat: Mapped[str | None] = mapped_column(String, init=False)
    descricao: Mapped[str | None] = mapped_column(String, init=False)
    nr_guia: Mapped[str | None] = mapped_column(String, init=False)
    cd_senha: Mapped[str | None] = mapped_column(String, init=False)
    dt_atendimento: Mapped[datetime | None] = mapped_column(
        DateTime,
        init=False,
    )
    dt_alta: Mapped[datetime | None] = mapped_column(DateTime, init=False)
    dt_remessa: Mapped[datetime | None] = mapped_column(DateTime, init=False)
    dt_competencia: Mapped[date | None] = mapped_column(Date, init=False)
    dt_fechamento: Mapped[datetime | None] = mapped_column(
        DateTime,
        init=False,
    )
    dt_lancamento: Mapped[datetime | None] = mapped_column(
        DateTime,
        init=False,
    )
    hr_lancamento: Mapped[datetime | None] = mapped_column(
        DateTime,
        init=False,
    )
    cd_prestador: Mapped[int | None] = mapped_column(init=False)
    nm_prestador: Mapped[str | None] = mapped_column(String, init=False)
    sn_fechada: Mapped[str | None] = mapped_column(String, init=False)
    sn_pertence_pacote: Mapped[str | None] = mapped_column(String, init=False)
    qt_lancamento: Mapped[Decimal | None] = mapped_column(Numeric, init=False)
    vl_unitario: Mapped[Decimal | None] = mapped_column(Numeric, init=False)
    vl_total_conta: Mapped[Decimal | None] = mapped_column(Numeric, init=False)
    vl_honorario_unitario: Mapped[Decimal | None] = mapped_column(
        Numeric,
        init=False,
    )
    vl_acrescimo: Mapped[Decimal | None] = mapped_column(Numeric, init=False)
    vl_desconto: Mapped[Decimal | None] = mapped_column(Numeric, init=False)
    cd_ati_med: Mapped[str | None] = mapped_column(String, init=False)
    ds_ati_med: Mapped[str | None] = mapped_column(String, init=False)
    cd_usuario: Mapped[str | None] = mapped_column(String, init=False)
    nm_usuario: Mapped[str | None] = mapped_column(String, init=False)
    tp_atendimento: Mapped[str | None] = mapped_column(
        String,
        init=False,
    )
    dt_ordenacao: Mapped[datetime | None] = mapped_column(DateTime, init=False)


@table_registry.mapped_as_dataclass
class ModelHpcPaciente:
    __tablename__ = 'HPC_V_PACIENTES'
    __table_args__ = {'schema': 'DBAMV'}

    cd_paciente: Mapped[int] = mapped_column(primary_key=True, init=False)
    paciente: Mapped[str] = mapped_column(String, init=False)
    nome_mae: Mapped[str | None] = mapped_column(String, init=False)
    cpf: Mapped[str | None] = mapped_column(String, init=False)
    cep: Mapped[str | None] = mapped_column(String, init=False)
    rua: Mapped[str | None] = mapped_column(String, init=False)
    numero_casa: Mapped[int | None] = mapped_column(init=False)
    bairro: Mapped[str | None] = mapped_column(String, init=False)
    complemento: Mapped[str | None] = mapped_column(String, init=False)
    email: Mapped[str | None] = mapped_column(String, init=False)
    contato: Mapped[str | None] = mapped_column(String, init=False)
