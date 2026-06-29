from datetime import date, datetime
from decimal import Decimal

from pydantic import (
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
    nr_guia: int | None = None
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
    qtd_registro: Decimal = Field(gt=0, exclude=True)
    qtd_glosada: Decimal = Field(gt=0)
    valor_glosado: Decimal = Field(gt=0)
    dt_recurso: date
    dt_pagamento: date
    dt_recebimento: date | None = None
    valor_recebido: Decimal | None = None
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
        if self.qtd_glosada > self.qtd_registro:
            raise ValueError(
                'A quantidade glosada/acatada nao pode exceder '
                'a quantidade do registro.'
            )
        if self.valor_glosado > self.valor:
            raise ValueError(
                'O valor glosado/acatado nao pode exceder o valor do registro.'
            )
        if self.sn_glosado == 'not' and (
            self.dt_recebimento is not None
            or self.valor_recebido is not None
            or self.observacao_recebimento
        ):
            raise ValueError(
                'Acatos nao podem possuir dados de recebimento.'
            )
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
    qtd_glosada: Decimal | None = None
    valor_glosado: Decimal | None = None
    dt_recurso: date | None = None
    dt_pagamento: date | None = None
    dt_recebimento: date | None = None
    valor_recebido: Decimal | None = None
    observacao_recebimento: str | None = None
    sn_glosado: str
    sn_ativo: str
    data_criacao: datetime


class RegistroGlosas(BaseModel):
    glosas: list[RegistroGlosaPublic]


class RegistroGlosaRecebimentoUpdate(BaseModel):
    dt_recebimento: date
    valor_recebido: Decimal = Field(gt=0)
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
    nm_convenio: str | None = None
    cd_gru_fat: int | None = None
    ds_gru_fat: str | None = None
    cd_pro_fat: int | None = None
    descricao: str | None = None
    nr_guia: int | None = None
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
    cd_ati_med: int | None = None
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
