from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, registry

from app_prontocardio.settings import Settings

table_registry = registry()
settings = Settings()


class TipoAtendimento(str, Enum):
    AMBULATORIO = 'Ambulatório'
    EXTERNO = 'Externo'
    URGENCIA = 'Urgência'
    INTERNACAO = 'Internação'


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
    __table_args__ = {'schema': settings.POSTGRES_SCHEMA}

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
    qtd_glosada: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    valor_glosado: Mapped[Decimal | None] = mapped_column(
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
    observacao_recebimento: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    sn_glosado: Mapped[str] = mapped_column(String, default='true')
    sn_ativo: Mapped[str] = mapped_column(String, default='true')
    data_criacao: Mapped[datetime] = mapped_column(
        init=False,
        server_default=text("timezone('America/Sao_Paulo', now())"),
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
class ModelConvenio:
    __tablename__ = 'CONVENIO'
    __table_args__ = {'schema': 'DBAMV'}

    cd_convenio: Mapped[int] = mapped_column(primary_key=True, init=False)
    nm_convenio: Mapped[str] = mapped_column(String, init=False)
    sn_ativo: Mapped[str] = mapped_column(String, init=False)


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
    nm_convenio: Mapped[str | None] = mapped_column(String, init=False)
    cd_gru_fat: Mapped[int | None] = mapped_column(init=False)
    ds_gru_fat: Mapped[str | None] = mapped_column(String, init=False)
    cd_pro_fat: Mapped[int | None] = mapped_column(init=False)
    descricao: Mapped[str | None] = mapped_column(String, init=False)
    nr_guia: Mapped[int | None] = mapped_column(init=False)
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
    cd_ati_med: Mapped[int | None] = mapped_column(init=False)
    ds_ati_med: Mapped[str | None] = mapped_column(String, init=False)
    cd_usuario: Mapped[str | None] = mapped_column(String, init=False)
    nm_usuario: Mapped[str | None] = mapped_column(String, init=False)
    tp_atendimento: Mapped[str | None] = mapped_column(
        String,
        init=False,
    )
    dt_ordenacao: Mapped[datetime | None] = mapped_column(DateTime, init=False)
