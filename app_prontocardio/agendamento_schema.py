from datetime import date, datetime

from pydantic import BaseModel, Field


class HorarioDisponivel(BaseModel):
    cd_it_agenda_central: int
    cd_agenda_central: int
    cd_tip_mar: int | None = None
    cd_item_agendamento: int
    ds_item_agendamento: str
    data_agenda: date
    horario: datetime
    cd_unidade_atendimento: int | None = None
    ds_unidade_atendimento: str | None = None
    ds_local_unidade_atendimento: str | None = None
    cd_prestador: int | None = None
    nm_prestador: str | None = None


class HorariosDisponiveis(BaseModel):
    horarios: list[HorarioDisponivel]
    total: int


class PacienteResumo(BaseModel):
    cd_paciente: int
    nm_paciente: str
    dt_nascimento: date | None = None
    tp_sexo: str | None = None
    cpf_final: str | None = None
    email: str | None = None
    nr_ddd_celular: str | None = None
    nr_celular: str | None = None


class PacientesEncontrados(BaseModel):
    pacientes: list[PacienteResumo]
    total: int


class CadastroPacienteInput(BaseModel):
    nm_paciente: str = Field(min_length=3, max_length=200)
    nr_cpf: str = Field(min_length=11, max_length=14)
    dt_nascimento: date
    tp_sexo: str = Field(pattern='^[FMIO]$')
    email: str | None = Field(default=None, max_length=200)
    nr_ddd_celular: str | None = Field(default=None, max_length=4)
    nr_celular: str | None = Field(default=None, max_length=20)
    nr_cep: str | None = Field(default=None, max_length=12)
    ds_endereco: str | None = Field(default=None, max_length=200)
    nr_endereco: int | None = Field(default=None, ge=0)
    ds_complemento: str | None = Field(default=None, max_length=100)
    nm_bairro: str | None = Field(default=None, max_length=100)
    cd_cidade: int | None = Field(default=None, gt=0)
    cd_convenio: int = Field(gt=0)
    cd_con_pla: int = Field(gt=0)
    nr_carteira: str | None = Field(default=None, max_length=25)


class PacienteCadastrado(BaseModel):
    cd_paciente: int
    nm_paciente: str
    nr_cpf: str
    mensagem: str


class AtualizacaoPacienteInput(BaseModel):
    email: str | None = Field(default=None, max_length=200)
    nr_ddd_celular: str | None = Field(default=None, max_length=4)
    nr_celular: str | None = Field(default=None, max_length=20)
    cd_convenio: int | None = Field(default=None, gt=0)
    cd_con_pla: int | None = Field(default=None, gt=0)
    nr_carteira: str | None = Field(default=None, max_length=25)


class PacienteAtualizado(BaseModel):
    cd_paciente: int
    mensagem: str


class UltimoAtendimentoPaciente(BaseModel):
    cd_atendimento: int
    horario_atendimento: datetime
    tipo_atendimento: str | None = None
    ds_tipo_atendimento: str | None = None
    cd_prestador: int | None = None
    nm_prestador: str | None = None
    cd_convenio: int | None = None
    nm_convenio: str | None = None
    cd_con_pla: int | None = None
    ds_con_pla: str | None = None


class HistoricoPaciente(BaseModel):
    ultimo_atendimento: UltimoAtendimentoPaciente | None = None
    total: int


class LinhaCuidadoResposta(BaseModel):
    campo: str | None = None
    identificador: str | None = None
    resposta: str | None = None


class LinhaCuidadoPaciente(BaseModel):
    cd_documento: int
    cd_registro: int | None = None
    cd_atendimento: int | None = None
    ds_documento: str | None = None
    ds_tipo_documento: str | None = None
    tp_status: str | None = None
    dh_documento: datetime | None = None
    dh_fechamento: datetime | None = None
    cd_usuario_criou: str | None = None
    respostas: list[LinhaCuidadoResposta] = Field(default_factory=list)


class LinhasCuidadoPaciente(BaseModel):
    linhas: list[LinhaCuidadoPaciente]
    total: int


class PlanoDisponivel(BaseModel):
    cd_convenio: int
    nm_convenio: str
    cd_con_pla: int
    ds_con_pla: str


class PlanosDisponiveis(BaseModel):
    planos: list[PlanoDisponivel]
    total: int


class ItemAgendamentoResumo(BaseModel):
    cd_item_agendamento: int
    ds_item_agendamento: str
    cd_exa_rx: int | None = None
    duracao_minutos: int | None = None


class ItensAgendamentoEncontrados(BaseModel):
    itens: list[ItemAgendamentoResumo]
    total: int


class ConvenioPaciente(BaseModel):
    cd_convenio: int
    nm_convenio: str
    cd_con_pla: int
    ds_con_pla: str
    ultimo_atendimento: datetime | None = None


class ConveniosPaciente(BaseModel):
    convenios: list[ConvenioPaciente]
    total: int


class AgendamentoPaciente(BaseModel):
    protocolo: int | None = None
    item_movimento_id: int | None = None
    cd_it_agenda_central: int
    cd_agenda_central: int
    cd_item_agendamento: int
    ds_item_agendamento: str
    horario: datetime
    cd_prestador: int | None = None
    nm_prestador: str | None = None
    ds_unidade_atendimento: str | None = None
    status: str | None = None
    cd_tip_mar: int | None = None
    cd_convenio: int | None = None
    nm_convenio: str | None = None
    cd_con_pla: int | None = None
    ds_con_pla: str | None = None


class AgendamentosPaciente(BaseModel):
    agendamentos: list[AgendamentoPaciente]
    total: int


class CancelarAgendamentoInput(BaseModel):
    motivo: str = Field(min_length=1, max_length=200)
    chave_idempotencia: str = Field(min_length=8, max_length=100)


class AgendamentoCancelado(BaseModel):
    status: str
    mensagem: str
    horario_id: int
    retorno_mv: str | None = None
    whatsapp_status: str | None = None
    whatsapp_mensagem: str | None = None


class PrestadorAgendamento(BaseModel):
    cd_prestador: int
    nm_prestador: str
    ds_codigo_conselho: str | None = None


class PrestadoresAgendamento(BaseModel):
    prestadores: list[PrestadorAgendamento]
    total: int
    exige_prestador: bool


class TipoMarcacaoConsulta(BaseModel):
    cd_tip_mar: int
    ds_tip_mar: str


class TiposMarcacaoConsulta(BaseModel):
    tipos: list[TipoMarcacaoConsulta]
    total: int


class PreValidacaoAgendamentoInput(BaseModel):
    cd_paciente: int = Field(gt=0)
    cd_item_agendamento: int = Field(gt=0)
    cd_it_agenda_central: int = Field(gt=0)
    cd_convenio: int = Field(gt=0)
    cd_con_pla: int = Field(gt=0)
    cd_prestador: int | None = Field(default=None, gt=0)
    cd_tip_mar: int | None = Field(default=None, gt=0)


class PreValidacaoAgendamento(BaseModel):
    pode_agendar: bool
    alertas: list[str]
    cd_paciente: int
    nm_paciente: str
    cd_item_agendamento: int
    ds_item_agendamento: str
    cd_it_agenda_central: int
    cd_agenda_central: int
    cd_tip_mar: int | None = None
    horario: datetime
    cd_convenio: int
    nm_convenio: str
    cd_con_pla: int
    ds_con_pla: str
    cd_prestador: int | None = None
    nm_prestador: str | None = None
    ds_unidade_atendimento: str | None = None
    ds_local_unidade_atendimento: str | None = None
    agendamento_existente_slot: int | None = None
    agendamento_existente_horario: datetime | None = None


class ConfirmarAgendamentoInput(PreValidacaoAgendamentoInput):
    """Dados necessarios para confirmar um agendamento unico.

    O contrato existe desde ja, mas a escrita permanece protegida ate a
    procedure transacional do MV ser configurada.
    """

    chave_idempotencia: str = Field(min_length=8, max_length=100)
    cd_agenda_central: int = Field(gt=0)
    cd_it_agenda_fim: int | None = Field(default=None, gt=0)
    cd_atendimento: int | None = Field(default=None, gt=0)
    nr_ddd_celular: str | None = Field(default=None, max_length=4)
    nr_celular: str | None = Field(default=None, max_length=20)
    observacao: str | None = Field(default=None, max_length=600)


class AgendamentoConfirmado(BaseModel):
    status: str
    mensagem: str
    protocolo: int | None = None
    movimento_id: int | None = None
    item_movimento_id: int | None = None
    horario_id: int
    agenda_id: int
    whatsapp_status: str | None = None
    whatsapp_mensagem: str | None = None


class ReagendarAgendamentoInput(ConfirmarAgendamentoInput):
    cd_it_agenda_central_anterior: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=200)


class AgendamentoReagendado(AgendamentoConfirmado):
    horario_anterior_id: int


class OrientacaoExame(BaseModel):
    cd_item_agendamento: int
    cd_exa_rx: int | None = None
    ds_exa_rx: str | None = None
    orientacoes: list[str]
    total: int
