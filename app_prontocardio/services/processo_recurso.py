from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update

from app_prontocardio.models import ProcessoRecursoGlosa, RegistroGlosa


def sincronizar_processo_recurso_issec(session, payload, usuario_id) -> None:
    """Mantém modal, Recursos e PDF no mesmo processo de recurso."""
    if (
        'ISSEC' not in str(payload.convenio or '').upper()
        or payload.sn_glosado != 'true'
        or 'processo_recurso' not in payload.model_fields_set
    ):
        return
    processo = str(payload.processo_controle_fatura_gab or '').strip()
    if not processo:
        return
    numero_recurso = str(payload.processo_recurso or '').strip()
    chave = processo.casefold()
    cadastro = session.scalar(
        select(ProcessoRecursoGlosa).where(
            ProcessoRecursoGlosa.processo_original_normalizado == chave
        )
    )
    if numero_recurso:
        if cadastro is None:
            cadastro = ProcessoRecursoGlosa(
                processo_original=processo,
                processo_original_normalizado=chave,
                processo_recurso=numero_recurso,
                usuario_id=usuario_id,
            )
            agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(
                tzinfo=None
            )
            cadastro.data_criacao = agora
            cadastro.data_atualizacao = agora
            session.add(cadastro)
        else:
            cadastro.processo_recurso = numero_recurso
            cadastro.usuario_id = usuario_id
            cadastro.data_atualizacao = datetime.now(
                ZoneInfo('America/Sao_Paulo')
            ).replace(tzinfo=None)
    elif cadastro is not None:
        session.delete(cadastro)
    session.execute(
        update(RegistroGlosa)
        .where(
            func.lower(func.trim(RegistroGlosa.processo_controle_fatura_gab))
            == chave,
            RegistroGlosa.convenio.ilike('%ISSEC%'),
            RegistroGlosa.sn_ativo == 'true',
            RegistroGlosa.sn_glosado == 'true',
        )
        .values(processo_recurso=numero_recurso or None)
    )
