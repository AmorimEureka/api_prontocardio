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

from app_prontocardio.models import (
    DecisaoValidacaoSolicitacao,
    LocalSolicitacaoNota,
    StatusEmissaoNfse,
    StatusWorkflowSolicitacao,
    TipoAtendimento,
)


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


class SolicitacaoNotaCreate(BaseModel):
    codigo_atendimento: int = Field(gt=0)
    local: LocalSolicitacaoNota
    procedimento: str = Field(min_length=1, max_length=500)
    valor_nota: Decimal = Field(gt=0, max_digits=14, decimal_places=2)

    @field_validator('procedimento', mode='before')
    @classmethod
    def normalize_procedimento(cls, value):
        procedimento = str(value or '').strip()
        if not procedimento:
            raise ValueError('Informe o procedimento.')
        return procedimento


class SolicitacaoNotaUpdate(BaseModel):
    local: LocalSolicitacaoNota
    procedimento: str = Field(min_length=1, max_length=500)
    valor_nota: Decimal = Field(gt=0, max_digits=14, decimal_places=2)

    @field_validator('procedimento', mode='before')
    @classmethod
    def normalize_procedimento(cls, value):
        procedimento = str(value or '').strip()
        if not procedimento:
            raise ValueError('Informe o procedimento.')
        return procedimento


class AtendimentoSolicitacaoNotaPublic(BaseModel):
    codigo_atendimento: int
    codigo_paciente: int
    codigo_convenio: int
    nm_paciente: str
    convenio: str
    nr_cpf: str | None = None
    nr_cep: str | None = None
    ds_endereco: str | None = None
    nr_endereco: str | None = None
    nm_bairro: str | None = None
    ds_complemento: str | None = None
    email: str | None = None
    nr_fone: str | None = None
    tipo_atendimento: str


class SolicitacaoNotaPublic(AtendimentoSolicitacaoNotaPublic):
    model_config = ConfigDict(from_attributes=True)

    id: int
    local: LocalSolicitacaoNota
    procedimento: str
    valor_nota: Decimal | None = Field(default=None, ge=0)
    usuario_id: int
    cadastrado_por: str | None = None
    status: StatusWorkflowSolicitacao | None = None
    ativo: bool = True
    data_criacao: datetime


class ProcedimentoAtendimentoPublic(BaseModel):
    codigo: str | None = None
    descricao: str
    grupo: str | None = None
    quantidade: Decimal | None = None
    realizado_em: datetime | None = None
    prestador: str | None = None


class SolicitacaoNotaWorkflowPublic(SolicitacaoNotaPublic):
    workflow_id: int
    status: StatusWorkflowSolicitacao
    validacao: DecisaoValidacaoSolicitacao | None = None
    motivo_recusa: str | None = None
    validado_por_id: int | None = None
    validado_por: str | None = None
    validado_em: datetime | None = None
    inativado_por_id: int | None = None
    inativado_por: str | None = None
    inativado_em: datetime | None = None
    procedimentos_atendimento: list[ProcedimentoAtendimentoPublic] = Field(
        default_factory=list
    )
    procedimentos_atendimento_disponiveis: bool = True
    workflow_atualizado_em: datetime


class SolicitacaoNotaWorkflowList(BaseModel):
    solicitacoes: list[SolicitacaoNotaWorkflowPublic]
    total: int
    limit: int
    offset: int


class SolicitacaoNotaWorkflowFilter(BaseModel):
    status: StatusWorkflowSolicitacao = (
        StatusWorkflowSolicitacao.PENDENTE_VALIDACAO
    )
    incluir_inativas: bool = False
    nome_paciente: str | None = Field(default=None, max_length=200)
    cpf: str | None = Field(default=None, max_length=20)
    tipo_atendimento: str | None = Field(default=None, max_length=50)
    local: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SolicitacaoNotaFilter(BaseModel):
    codigo_atendimento: int | None = Field(default=None, gt=0)
    nome_paciente: str | None = Field(default=None, max_length=200)
    convenio: str | None = Field(default=None, max_length=100)
    local: LocalSolicitacaoNota | None = None
    status: StatusWorkflowSolicitacao | None = None
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SolicitacaoNotaEmissaoFilter(BaseModel):
    nome_paciente: str | None = Field(default=None, max_length=200)
    cpf: str | None = Field(default=None, max_length=20)
    tipo_atendimento: str | None = Field(default=None, max_length=50)
    local: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ValidacaoSolicitacaoNotaInput(BaseModel):
    decisao: DecisaoValidacaoSolicitacao
    motivo_recusa: str | None = Field(default=None, max_length=500)

    @field_validator('motivo_recusa', mode='before')
    @classmethod
    def normalize_motivo_recusa(cls, value):
        motivo = str(value or '').strip()
        return motivo or None

    @model_validator(mode='after')
    def validate_motivo_recusa(self):
        if (
            self.decisao == DecisaoValidacaoSolicitacao.RECUSADA
            and not self.motivo_recusa
        ):
            raise ValueError('Informe o motivo da recusa.')
        if self.decisao == DecisaoValidacaoSolicitacao.VALIDADA:
            self.motivo_recusa = None
        return self


class EmissaoNfseCreate(BaseModel):
    solicitacao_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator('solicitacao_ids')
    @classmethod
    def validate_solicitacoes_unicas(cls, value):
        if any(solicitacao_id <= 0 for solicitacao_id in value):
            raise ValueError('Solicitação inválida.')
        if len(value) != len(set(value)):
            raise ValueError('Não repita solicitações no lote.')
        return value


class EmissaoNfsePublic(BaseModel):
    id: int
    solicitacao_nota_id: int
    lote_id: int
    usuario_id: int
    status: str
    numero_nfse: str | None = None
    protocolo: str | None = None
    erro: str | None = None
    data_criacao: datetime
    data_atualizacao: datetime

    model_config = ConfigDict(from_attributes=True)


class SolicitacaoNotaEmissaoPublic(SolicitacaoNotaWorkflowPublic):
    emissao_id: int | None = None
    lote_id: int | None = None
    status_emissao: StatusEmissaoNfse | None = None
    numero_nfse: str | None = None
    protocolo: str | None = None
    erro_emissao: str | None = None
    emissao_criada_em: datetime | None = None
    emissao_atualizada_em: datetime | None = None
    arquivo_disponivel: bool = False


class SolicitacaoNotaResumoStatus(BaseModel):
    status: StatusWorkflowSolicitacao
    quantidade: int = Field(ge=0)
    valor_total: Decimal = Field(default=Decimal('0'), ge=0)


class SolicitacaoNotaList(BaseModel):
    solicitacoes: list[SolicitacaoNotaEmissaoPublic]
    resumo_status: list[SolicitacaoNotaResumoStatus]
    total: int
    limit: int
    offset: int


class SolicitacaoNotaEmissaoList(BaseModel):
    solicitacoes: list[SolicitacaoNotaEmissaoPublic]
    total: int
    limit: int
    offset: int


class LoteEmissaoNfsePublic(BaseModel):
    lote_id: int
    tipo: str
    status: str
    quantidade: int
    dag_run_id: str | None = None
    airflow_disparado_em: datetime | None = None
    erro_disparo: str | None = None
    data_criacao: datetime
    emissoes: list[EmissaoNfsePublic]
    message: str | None = None


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
    processo_recurso: str | None = None
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
    qtd_recursado: Decimal | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices(
            'qtd_recursado',
            'qtd_recursada',
            'qtd_glosada',
            'qtd_glosado',
        ),
    )
    valor_recursado: Decimal | None = Field(
        default=None,
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
        'motivo_glosa',
        mode='before',
    )
    @classmethod
    def validate_required_text(cls, value):
        text = str(value or '').strip()
        if not text:
            raise ValueError('campo obrigatorio')
        return text

    @field_validator('processo_recurso', mode='before')
    @classmethod
    def normalize_optional_processo_recurso(cls, value):
        text = str(value or '').strip()
        return text or None

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
        if self.sn_glosado == 'true' and (
            self.processo_recurso is None
            or self.qtd_recursado is None
            or self.valor_recursado is None
        ):
            raise ValueError(
                'Informe processo, quantidade e valor para registrar recurso.'
            )
        if (
            self.qtd_recursado is not None
            and self.qtd_recursado > self.qtd_registro
        ):
            raise ValueError(
                'A quantidade glosada/acatada nao pode exceder '
                'a quantidade do registro.'
            )
        if (
            self.valor_recursado is not None
            and self.valor_recursado > self.valor
        ):
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
    origem_registro: str
    data_criacao: datetime
    conciliacao_remessa_id: int | None = None
    valor_glosa_origem: Decimal | None = None
    valor_glosa_pendente: Decimal | None = None
    status_tratativa: str
    valor_indicador: Decimal


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
    valor_total_conciliado: Decimal
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
    remessa: RemessaFaturamentoCardPublic
    message: str


class ValorConciliacaoRemessaUpdate(BaseModel):
    cd_remessa: int = Field(gt=0)
    valor_glosado: Decimal = Field(ge=0)
    valor_recebido: Decimal = Field(gt=0)


class ConciliacaoFaturamentoUpdate(BaseModel):
    processo_recebimento: str | None = Field(default=None, max_length=255)
    data_previsao_recebimento: date | None = None
    remessas: list[ValorConciliacaoRemessaUpdate] | None = None

    @field_validator('processo_recebimento', mode='before')
    @classmethod
    def normalize_processo_recebimento(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError('campo obrigatorio')
        return normalized

    @model_validator(mode='after')
    def validate_campos_informados(self):
        if (
            self.processo_recebimento is None
            and self.data_previsao_recebimento is None
            and not self.remessas
        ):
            raise ValueError('Informe ao menos um campo para atualizar.')
        if self.remessas:
            codigos = [item.cd_remessa for item in self.remessas]
            if len(codigos) != len(set(codigos)):
                raise ValueError(
                    'Uma remessa nao pode ser informada mais de uma vez.'
                )
        return self


class UsuarioOperacaoFinanceiraPublic(BaseModel):
    id: int
    nome: str
    email: str


class AuditoriaConciliacaoPublic(BaseModel):
    id: int
    conciliacao_origem_id: int
    numero_nfse: str
    acao: str
    usuario: UsuarioOperacaoFinanceiraPublic
    dados_anteriores: dict | None = None
    dados_novos: dict | None = None
    data_operacao: datetime


class RecebimentoConciliacaoPublic(BaseModel):
    id: int
    cd_remessa: int
    data_recebimento: date
    valor_recebido: Decimal
    conta_bancaria_id: int
    conta_plano_contas: str | None = None
    conta_centro_custo: str | None = None
    lancamento_extrato_id: int | None = None
    data_registro: datetime
    usuario: UsuarioOperacaoFinanceiraPublic


class NotaFiscalConciliacaoHistoricoPublic(BaseModel):
    id: int
    numero_nfse: str
    tipo_conciliacao: str
    valor_nfse: Decimal
    valor_vinculado_remessa: Decimal
    valor_alocado_nfse: Decimal
    valor_glosado: Decimal
    data_previsao_recebimento: date
    data_recebimento: date | None = None
    data_criacao: datetime
    data_atualizacao: datetime | None = None
    data_inativacao: datetime | None = None
    valor_nfse: Decimal
    ativo: bool
    situacao_recebimento: str
    usuario_criacao: UsuarioOperacaoFinanceiraPublic
    usuario_atualizacao: UsuarioOperacaoFinanceiraPublic | None = None
    usuario_inativacao: UsuarioOperacaoFinanceiraPublic | None = None
    recebimentos: list[RecebimentoConciliacaoPublic]


class ConciliacaoGerenciamentoPublic(BaseModel):
    cd_remessa: int
    convenio: str
    cnpj_convenio: str
    processo_recebimento: str
    data_competencia: date | None = None
    valor_remessa: Decimal
    valor_alocado_nfse: Decimal
    valor_glosado: Decimal
    ativo: bool
    situacao_recebimento: str
    notas: list[NotaFiscalConciliacaoHistoricoPublic]
    auditoria: list[AuditoriaConciliacaoPublic]


class ConciliacoesGerenciamentoList(BaseModel):
    conciliacoes: list[ConciliacaoGerenciamentoPublic]
    total: int
    total_ativas: int
    total_inativas: int
    total_recebidas: int
    total_sem_recebimento: int
    limit: int
    offset: int


class ConciliacaoAlteracaoPublic(BaseModel):
    id: int
    ativo: bool
    processo_recebimento: str
    data_previsao_recebimento: date
    usuario_operacao_id: int
    data_operacao: datetime
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


class RecebimentoRemessaUpdate(BaseModel):
    data_recebimento: date
    valor_recebido: Decimal = Field(gt=0)
    conta_bancaria_id: int = Field(gt=0)
    conta_plano_contas: str | None = Field(default=None, max_length=255)
    conta_centro_custo: str | None = Field(default=None, max_length=255)
    lancamento_extrato_id: int | None = Field(default=None, gt=0)

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


class RecebimentoAnteriorNfsePublic(BaseModel):
    id: int
    data_recebimento: date
    valor_recebido: Decimal
    saldo_financeiro: Decimal
    conta_bancaria_id: int
    conta_plano_contas: str | None = None
    conta_centro_custo: str | None = None
    lancamento_extrato_id: int | None = None
    lancamento_extrato: LancamentoExtratoBancarioPublic | None = None
    data_registro: datetime


class NfseSemRecebimentoPublic(BaseModel):
    id: int
    numero_nfse: str
    tp_conciliacao: str
    data_previsao_recebimento: date
    data_criacao: datetime
    valor_nfse: Decimal
    valor_vinculado_remessa: Decimal
    valor_glosado: Decimal
    valor_recebido: Decimal
    valor_pendente: Decimal
    situacao: str
    em_atraso: bool
    dias_em_atraso: int
    recebimentos: list[RecebimentoAnteriorNfsePublic]


class RemessaSemRecebimentoPublic(BaseModel):
    cd_remessa: int
    convenio: str
    cnpj_convenio: str
    processo_recebimento: str
    data_competencia: date | None = None
    valor_remessa: Decimal
    quantidade_nfses_sem_recebimento: int
    valor_total_glosas: Decimal
    valor_recebido: Decimal
    valor_pendente: Decimal
    situacao: str
    em_atraso: bool
    dias_em_atraso: int
    notas: list[NfseSemRecebimentoPublic]


class ConciliacoesSemRecebimentoList(BaseModel):
    conciliacoes: list[RemessaSemRecebimentoPublic]
    total: int
    total_remessas_sem_recebimento: int
    valor_total_recebido: Decimal
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
    registro_recusa: RegistroGlosaPublic | None = None
    registro_acato: RegistroGlosaPublic | None = None


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
