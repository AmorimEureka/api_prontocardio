from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app_prontocardio.models import TipoAtendimento


class UserSchema(BaseModel):
    nome: str
    email: EmailStr
    senha: str = Field(min_length=8, max_length=128)
    perfil: str = Field(default='usuario', pattern='^(usuario|ti)$')


class UserPublic(BaseModel):
    id: int
    nome: str
    email: EmailStr
    perfil: str
    ativo: bool
    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    usuarios: list[UserPublic]


class UserStatusUpdate(BaseModel):
    ativo: bool


class UserPasswordUpdate(BaseModel):
    senha: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    nova_senha: str = Field(min_length=8, max_length=128)


class FilterPage(BaseModel):
    offset: int = Field(ge=0, default=0)
    limit: int = Field(ge=0, default=10)


class FilterSearch(FilterPage):
    cd_remessa: int | None = None
    cd_atendimento: int | None = None
    cd_reg: int | None = None
    nr_guia: str | None = None
    cd_senha: str | None = None
    nm_paciente: str | None = None
    nm_convenio: str | None = None
    descricao: str | None = None


class Message(BaseModel):
    message: str


class RegistroGlosaCreate(BaseModel):
    codigo_paciente: int
    nm_paciente: str | None = None
    cd_remessa: int
    cd_atendimento: int
    conta: int
    cd_lancamento: int | None = None
    cd_prestador: int
    cd_convenio: int
    tp_atendimento: TipoAtendimento
    procedimento: str
    convenio: str
    guia: str
    prestador: str
    data_atendimento: datetime
    valor: Decimal
    processo_controle_fatura_gab: str
    processo_recurso: str
    data_glosa: date
    motivo_glosa: str
    descricao_glosa: str
    qtd_registro: Decimal = Field(gt=0)
    descricao_item: str | None = None
    data_alta: datetime | None = None
    data_lancamento: datetime | None = None
    cd_gru_pro: int | None = None
    ds_gru_pro: str | None = None
    cd_gru_fat: int | None = None
    ds_gru_fat: str | None = None
    qtd_recursado: Decimal = Field(
        gt=0,
        validation_alias=AliasChoices(
            'qtd_recursado',
            'qtd_recursada',
            'qtd_glosada',
            'qtd_glosado',
        ),
    )
    valor_recursado: Decimal = Field(
        gt=0,
        validation_alias=AliasChoices('valor_recursado', 'valor_glosado'),
    )
    dt_recurso: date
    dt_pagamento: date
    dt_recebimento: date | None = None
    valor_recebido: Decimal | None = None
    qtd_recebida: Decimal | None = None
    observacao_recebimento: str | None = None
    sn_glosado: str = 'true'

    @field_validator(
        'processo_controle_fatura_gab',
        'processo_recurso',
        'motivo_glosa',
        mode='before',
    )
    @classmethod
    def validate_required_text(cls, value):
        text = str(value or '').strip()
        if not text:
            raise ValueError('campo obrigatorio')
        return text

    @model_validator(mode='after')
    def validate_glosa_business_rules(self):
        today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        if self.data_glosa > today:
            raise ValueError(
                'A data da glosa nao pode ser maior que a data atual.'
            )
        if self.dt_pagamento > today:
            raise ValueError(
                'A data do pagamento nao pode ser maior que a data atual.'
            )
        if self.dt_recurso > today:
            raise ValueError(
                'A data do recurso nao pode ser maior que a data atual.'
            )
        if self.data_glosa > self.dt_pagamento:
            raise ValueError(
                'A data da glosa deve ser igual ou anterior '
                'a data do pagamento.'
            )
        if (
            self.dt_recurso < self.data_glosa
            or self.dt_recurso < self.dt_pagamento
        ):
            raise ValueError(
                'A data do recurso nao pode ser anterior as datas '
                'da glosa ou do pagamento.'
            )
        if self.qtd_recursado > self.qtd_registro:
            raise ValueError(
                'A quantidade glosada/acatada nao pode exceder '
                'a quantidade do registro.'
            )
        if self.valor_recursado > self.valor:
            raise ValueError(
                'O valor glosado/acatado nao pode exceder o valor do registro.'
            )
        if self.sn_glosado == 'not' and (
            self.dt_recebimento is not None
            or self.valor_recebido is not None
            or self.qtd_recebida is not None
            or self.observacao_recebimento
        ):
            raise ValueError('Acatos nao podem possuir dados de recebimento.')
        return self

    @field_validator('sn_glosado', mode='before')
    @classmethod
    def normalize_sn_glosado(cls, value):
        if value in (False, 'false', 'False', 'not', 'NOT'):
            return 'not'
        return 'true'


class RegistroGlosaPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo_paciente: int
    nm_paciente: str | None = None
    cd_remessa: int
    cd_atendimento: int
    conta: int
    cd_lancamento: int | None = None
    cd_prestador: int
    cd_convenio: int
    tp_atendimento: TipoAtendimento
    procedimento: str
    convenio: str
    guia: str
    prestador: str
    data_atendimento: datetime
    valor: Decimal
    processo_controle_fatura_gab: str
    processo_recurso: str | None = None
    data_glosa: date
    motivo_glosa: str
    descricao_glosa: str
    qtd_registro: Decimal | None = None
    descricao_item: str | None = None
    data_alta: datetime | None = None
    data_lancamento: datetime | None = None
    cd_gru_pro: int | None = None
    ds_gru_pro: str | None = None
    cd_gru_fat: int | None = None
    ds_gru_fat: str | None = None
    qtd_recursado: Decimal | None = None
    valor_recursado: Decimal | None = None
    dt_recurso: date | None = None
    dt_pagamento: date | None = None
    dt_recebimento: date | None = None
    valor_recebido: Decimal | None = None
    qtd_recebida: Decimal | None = None
    observacao_recebimento: str | None = None
    sn_glosado: str
    sn_ativo: str
    data_criacao: datetime
    conciliacao_remessa_id: int | None = None
    valor_glosa_origem: Decimal | None = None
    valor_glosa_pendente: Decimal | None = None


class RegistroGlosas(BaseModel):
    glosas: list[RegistroGlosaPublic]


class RegistroGlosaRecebimentoUpdate(BaseModel):
    dt_recebimento: date
    valor_recebido: Decimal = Field(gt=0)
    qtd_recebida: Decimal = Field(gt=0)
    observacao_recebimento: str | None = None


class PrazoRecursoConvenioInput(BaseModel):
    cd_convenio: int
    convenio: str
    dias_para_recurso: int = Field(ge=0, le=365)
    habilitado: bool = True


class PrazoRecursoConvenioPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cd_convenio: int
    convenio: str
    dias_para_recurso: int | None = None
    configurado: bool = False
    habilitado: bool = True


class PrazoRecursoConvenioList(BaseModel):
    convenios: list[PrazoRecursoConvenioPublic]


class ConvenioPublic(BaseModel):
    cd_convenio: int
    nm_convenio: str


class ConvenioList(BaseModel):
    convenios: list[ConvenioPublic]


class TissPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo_termo: str
    termo: str
    dt_inicio_vigencia: date | None = None
    dt_fim_vigencia: date | None = None
    dt_fim_implantacao: date | None = None


class TissList(BaseModel):
    itens: list[TissPublic]


class Token(BaseModel):
    access_token: str
    token_type: str


class VersaoOracle(BaseModel):
    banner: str


class Atendimento(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cd_reg: int
    cd_lancamento: int
    cd_atendimento: int | None = None
    cd_paciente: int | None = None
    nm_paciente: str | None = None
    cd_remessa: int | None = None
    cd_regra: int | None = None
    ds_regra: str | None = None
    cd_convenio: int | None = None
    cnpj_convenio: str | None = None
    nm_convenio: str | None = None
    cd_gru_fat: int | None = None
    ds_gru_fat: str | None = None
    cd_pro_fat: str | None = None
    descricao: str | None = None
    nr_guia: str | None = None
    cd_senha: str | None = None
    dt_atendimento: datetime | None = None
    dt_alta: datetime | None = None
    dt_remessa: datetime | None = None
    dt_competencia: date | None = None
    dt_fechamento: datetime | None = None
    dt_lancamento: datetime | None = None
    hr_lancamento: datetime | None = None
    cd_prestador: int | None = None
    nm_prestador: str | None = None
    sn_fechada: str | None = None
    sn_pertence_pacote: str | None = None
    qt_lancamento: Decimal | None = None
    vl_unitario: Decimal | None = None
    vl_total_conta: Decimal | None = None
    vl_honorario_unitario: Decimal | None = None
    vl_acrescimo: Decimal | None = None
    vl_desconto: Decimal | None = None
    cd_ati_med: str | None = None
    ds_ati_med: str | None = None
    cd_usuario: str | None = None
    nm_usuario: str | None = None
    tp_atendimento: TipoAtendimento | None = None
    dt_ordenacao: datetime | None = None


class Atendimentos(BaseModel):
    atendimentos: list[Atendimento]
    total: int
    limit: int | None = None
    offset: int


class NfsePendenteConciliacao(BaseModel):
    row_hash: str
    numero_nfse: str
    data_emissao: datetime | None = None
    convenio: str
    cnpj_convenio: str
    impostos: Decimal
    valor_nfse: Decimal


class NfsesPendentesConciliacao(BaseModel):
    notas: list[NfsePendenteConciliacao]
    total: int
    valor_total_nfse: Decimal
    limit: int
    offset: int


class RemessaConciliacaoPublic(BaseModel):
    cd_remessa: int
    cd_convenio: int | None = None
    convenio: str
    cnpj_convenio: str
    valor_total: Decimal
    possui_recurso_aberto: bool = False
    valor_recursado: Decimal = Decimal('0.00')
    tp_conciliacao: str = 'faturamento'
    valor_remessa_original: Decimal | None = None
    valor_recebimento_pendente: Decimal = Decimal('0.00')
    valor_total_acatado: Decimal = Decimal('0.00')
    saldo_cobravel: Decimal = Decimal('0.00')
    valor_elegivel_conciliacao: Decimal = Decimal('0.00')
    situacao_financeira: str = 'aberta'


class RestricaoRemessaConciliacaoPublic(BaseModel):
    cd_remessa: int
    motivo: str
    message: str
    valor_total_acatado: Decimal = Decimal('0.00')
    saldo_cobravel: Decimal | None = None
    remessa_recebida_integralmente: bool = False
    remessa_encerrada_financeiramente: bool = False


class RemessasConciliacaoList(BaseModel):
    remessas: list[RemessaConciliacaoPublic]
    message: str | None = None
    restricao: RestricaoRemessaConciliacaoPublic | None = None


class HistoricoNfseRemessaPublic(BaseModel):
    id: int
    numero_nfse: str
    data_emissao: datetime | None = None
    valor_nfse: Decimal
    valor_alocado: Decimal
    valor_glosado: Decimal
    tipo_conciliacao: str
    data_previsao_recebimento: date
    data_recebimento: date | None = None
    conta_bancaria_id: int | None = None
    data_conciliacao: datetime


class RemessaFaturamentoCardPublic(BaseModel):
    cd_remessa: int
    data_competencia: date | None = None
    convenio: str
    cnpj_convenio: str
    valor_remessa: Decimal
    valor_conciliado: Decimal
    valor_acatado: Decimal
    valor_nao_conciliado: Decimal
    valor_recurso_disponivel: Decimal
    valor_disponivel_conciliacao: Decimal
    processo_recebimento: str | None = None
    historico: list[HistoricoNfseRemessaPublic]


class RemessasFaturamentoList(BaseModel):
    remessas: list[RemessaFaturamentoCardPublic]
    total: int
    valor_total_nao_conciliado: Decimal
    limit: int
    offset: int


class NfseSaldoRemessaPublic(BaseModel):
    row_hash: str
    numero_nfse: str
    data_emissao: datetime | None = None
    convenio: str
    cnpj_convenio: str
    valor_nfse: Decimal
    valor_utilizado: Decimal
    saldo_nfse: Decimal
    valor_sugerido: Decimal


class NfsesSaldoRemessaList(BaseModel):
    notas: list[NfseSaldoRemessaPublic]
    message: str | None = None
    valor_disponivel_remessa: Decimal


class NfseConciliacaoRemessaInput(BaseModel):
    nfse_row_hash: str = Field(min_length=1, max_length=256)
    valor_alocado: Decimal = Field(gt=0)
    sn_glosado: bool = False
    valor_glosado: Decimal = Field(default=Decimal('0.00'), ge=0)
    data_previsao_recebimento: date
    data_recebimento: date | None = None
    conta_bancaria_id: int | None = Field(default=None, gt=0)
    conta_plano_contas: str | None = Field(default=None, max_length=255)
    conta_centro_custo: str | None = Field(default=None, max_length=255)
    lancamento_extrato_id: int | None = Field(default=None, gt=0)

    @field_validator('nfse_row_hash', mode='before')
    @classmethod
    def validate_nfse_row_hash(cls, value):
        normalized = str(value or '').strip()
        if not normalized:
            raise ValueError('campo obrigatorio')
        return normalized

    @field_validator('conta_plano_contas', 'conta_centro_custo')
    @classmethod
    def normalize_optional_text(cls, value):
        normalized = str(value or '').strip()
        return normalized or None

    @model_validator(mode='after')
    def validate_glosa_e_recebimento(self):
        if self.sn_glosado and self.valor_glosado <= 0:
            raise ValueError(
                'Informe um valor de glosa maior que zero para a NFS-e.'
            )
        if not self.sn_glosado and self.valor_glosado != 0:
            raise ValueError(
                'NFS-e sem glosa deve possuir valor glosado igual a zero.'
            )
        if (
            self.data_recebimento is not None
            and self.conta_bancaria_id is None
        ):
            raise ValueError(
                'Selecione a conta bancaria quando a data de recebimento '
                'for informada.'
            )
        if self.data_recebimento is None and (
            self.conta_bancaria_id is not None
            or self.lancamento_extrato_id is not None
        ):
            raise ValueError(
                'Informe a data de recebimento para vincular conta bancaria '
                'ou lancamento do extrato.'
            )
        today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        if (
            self.data_recebimento is not None
            and self.data_recebimento > today
        ):
            raise ValueError(
                'A data do recebimento nao pode ser maior que a data atual.'
            )
        return self


class ConciliacaoRemessaCreate(BaseModel):
    processo_recebimento: str = Field(min_length=1, max_length=255)
    notas: list[NfseConciliacaoRemessaInput] = Field(min_length=1)

    @field_validator('processo_recebimento', mode='before')
    @classmethod
    def validate_processo_recebimento(cls, value):
        normalized = str(value or '').strip()
        if not normalized:
            raise ValueError('campo obrigatorio')
        return normalized

    @model_validator(mode='after')
    def validate_notas_unicas(self):
        hashes = [nota.nfse_row_hash for nota in self.notas]
        if len(hashes) != len(set(hashes)):
            raise ValueError(
                'Uma mesma NFS-e nao pode ser adicionada mais de uma vez.'
            )
        return self


class ConciliacaoRemessaPublic(BaseModel):
    processo_remessa_id: int
    cd_remessa: int
    processo_recebimento: str
    quantidade_notas: int
    valor_alocado: Decimal
    valor_glosado: Decimal
    valor_nao_conciliado: Decimal
    message: str


class ContaBancariaRecebimentoPublic(BaseModel):
    id: int
    banco: str
    agencia: str
    digito_agencia: str | None = None
    conta: str
    digito: str | None = None
    descricao: str | None = None


class ContasBancariasRecebimentoList(BaseModel):
    contas: list[ContaBancariaRecebimentoPublic]


class LancamentoExtratoBancarioPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conta_bancaria_id: int
    data_lancamento: date
    valor: Decimal
    descricao: str | None = None
    documento: str | None = None


class LancamentosExtratoBancarioList(BaseModel):
    lancamentos: list[LancamentoExtratoBancarioPublic]


class RemessaConciliacaoInput(BaseModel):
    cd_remessa: int = Field(gt=0)
    sn_glosado: bool = False
    valor_glosado: Decimal = Field(default=Decimal('0.00'), ge=0)

    @model_validator(mode='after')
    def validate_valor_glosado(self):
        if self.sn_glosado and self.valor_glosado <= 0:
            raise ValueError(
                'Informe um valor de glosa maior que zero para a remessa.'
            )
        if not self.sn_glosado and self.valor_glosado != 0:
            raise ValueError(
                'Remessa sem glosa deve possuir valor glosado igual a zero.'
            )
        return self


class ConciliacaoFaturamentoCreate(BaseModel):
    nfse_row_hash: str = Field(min_length=1, max_length=256)
    processo_recebimento: str = Field(min_length=1, max_length=255)
    data_previsao_recebimento: date
    data_recebimento: date | None = None
    conta_bancaria_id: int | None = Field(default=None, gt=0)
    conta_plano_contas: str | None = Field(default=None, max_length=255)
    conta_centro_custo: str | None = Field(default=None, max_length=255)
    lancamento_extrato_id: int | None = Field(default=None, gt=0)
    remessas: list[RemessaConciliacaoInput] = Field(min_length=1)

    @field_validator(
        'nfse_row_hash',
        'processo_recebimento',
        mode='before',
    )
    @classmethod
    def validate_required_text(cls, value):
        normalized = str(value or '').strip()
        if not normalized:
            raise ValueError('campo obrigatorio')
        return normalized

    @field_validator('conta_plano_contas', 'conta_centro_custo')
    @classmethod
    def normalize_optional_text(cls, value):
        normalized = str(value or '').strip()
        return normalized or None

    @model_validator(mode='after')
    def validate_recebimento(self):
        if (
            self.data_recebimento is not None
            and self.conta_bancaria_id is None
        ):
            raise ValueError(
                'Selecione a conta bancaria quando a data de recebimento '
                'for informada.'
            )
        if self.data_recebimento is None and (
            self.conta_bancaria_id is not None
            or self.lancamento_extrato_id is not None
        ):
            raise ValueError(
                'Informe a data de recebimento para vincular conta bancaria '
                'ou lancamento do extrato.'
            )
        today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        if (
            self.data_recebimento is not None
            and self.data_recebimento > today
        ):
            raise ValueError(
                'A data do recebimento nao pode ser maior que a data atual.'
            )
        return self


class ConciliacaoFaturamentoPublic(BaseModel):
    id: int
    nfse_row_hash: str
    numero_nfse: str
    processo_recebimento: str
    valor_nfse: Decimal
    total_remessas: Decimal
    total_glosas: Decimal
    message: str


class RecebimentoRemessaCreate(BaseModel):
    conciliacao_id: int | None = Field(default=None, gt=0)
    cd_remessa: int = Field(gt=0)
    numero_nfse: str = Field(min_length=1, max_length=255)
    data_recebimento: date
    valor_recebido: Decimal = Field(gt=0)
    conta_bancaria_id: int = Field(gt=0)
    conta_plano_contas: str | None = Field(default=None, max_length=255)
    conta_centro_custo: str | None = Field(default=None, max_length=255)
    lancamento_extrato_id: int | None = Field(default=None, gt=0)

    @field_validator('numero_nfse', mode='before')
    @classmethod
    def validate_numero_nfse(cls, value):
        normalized = str(value or '').strip()
        if not normalized:
            raise ValueError('campo obrigatorio')
        return normalized

    @field_validator('conta_plano_contas', 'conta_centro_custo')
    @classmethod
    def normalize_optional_text(cls, value):
        normalized = str(value or '').strip()
        return normalized or None

    @model_validator(mode='after')
    def validate_data_recebimento(self):
        today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        if self.data_recebimento > today:
            raise ValueError(
                'A data do recebimento nao pode ser maior que a data atual.'
            )
        return self


class RecebimentoRemessaPublic(BaseModel):
    id: int
    cd_remessa: int
    conciliacao_id: int
    numero_nfse: str
    data_recebimento: date
    valor_recebido: Decimal
    usuario_id: int
    conta_bancaria_id: int
    conta_plano_contas: str | None
    conta_centro_custo: str | None
    lancamento_extrato_id: int | None
    data_registro: datetime
    recebimento_integral: bool
    remessa_recebida_integralmente: bool
    remessa_encerrada_financeiramente: bool
    valor_total_remessa: Decimal
    valor_total_recebido: Decimal
    valor_total_acatado: Decimal
    saldo_em_aberto: Decimal


class RecebimentosRemessaList(BaseModel):
    recebimentos: list[RecebimentoRemessaPublic]
    total: int
    limit: int
    offset: int


class RemessaSemRecebimentoPublic(BaseModel):
    cd_remessa: int
    tp_conciliacao: str
    valor_remessa: Decimal
    valor_glosado: Decimal
    valor_pendente: Decimal


class ConciliacaoSemRecebimentoPublic(BaseModel):
    id: int
    numero_nfse: str
    convenio: str
    cnpj_convenio: str
    processo_recebimento: str
    data_previsao_recebimento: date
    data_criacao: datetime
    valor_nfse: Decimal
    quantidade_remessas: int
    quantidade_remessas_sem_recebimento: int
    valor_total_remessas: Decimal
    valor_total_glosas: Decimal
    valor_previsto_recebimento: Decimal
    valor_recebido: Decimal
    valor_pendente: Decimal
    situacao: str
    em_atraso: bool
    dias_em_atraso: int
    remessas: list[RemessaSemRecebimentoPublic]


class ConciliacoesSemRecebimentoList(BaseModel):
    conciliacoes: list[ConciliacaoSemRecebimentoPublic]
    total: int
    total_remessas_sem_recebimento: int
    valor_total_pendente: Decimal
    limit: int
    offset: int


class ItemFollowUpGlosaPublic(BaseModel):
    cd_paciente: int
    nm_paciente: str | None = None
    cd_remessa: int
    cd_atendimento: int
    cd_reg: int
    cd_lancamento: int | None = None
    cd_prestador: int
    nm_prestador: str
    cd_convenio: int
    nm_convenio: str
    tp_atendimento: TipoAtendimento
    cd_pro_fat: str
    cd_gru_pro: int | None = None
    ds_gru_pro: str | None = None
    cd_gru_fat: int | None = None
    ds_gru_fat: str | None = None
    descricao: str | None = None
    nr_guia: str
    dt_atendimento: datetime
    dt_alta: datetime | None = None
    dt_lancamento: datetime | None = None
    qt_lancamento: Decimal
    vl_total_conta: Decimal
    registro_glosa: RegistroGlosaPublic


class PacienteFollowUpGlosaPublic(BaseModel):
    codigo_paciente: int
    nm_paciente: str
    itens: list[ItemFollowUpGlosaPublic]


class CardFollowUpGlosaPublic(BaseModel):
    conciliacao_remessa_id: int
    cd_remessa: int
    convenio: str
    data_entrega: date
    numero_nfse: str
    valor_remessa: Decimal
    valor_glosado: Decimal
    valor_glosa_pendente: Decimal
    valor_total_tratado: Decimal
    pacientes: list[PacienteFollowUpGlosaPublic]


class FollowUpGlosasList(BaseModel):
    cards: list[CardFollowUpGlosaPublic]
    total: int
    valor_total_glosado: Decimal
    valor_total_pendente: Decimal
    valor_total_tratado: Decimal
    limit: int
    offset: int
