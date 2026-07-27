import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import HTTPException, status

from app_prontocardio.settings import Settings


settings = Settings()
TELEFONE_E164_MIN_LENGTH = 10
TELEFONE_E164_MAX_LENGTH = 15


def whatsapp_config() -> tuple[str, str, str]:
    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='WHATSAPP_PHONE_NUMBER_ID nao configurado.',
        )
    if not settings.WHATSAPP_ACCESS_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='WHATSAPP_ACCESS_TOKEN nao configurado.',
        )

    return (
        settings.WHATSAPP_GRAPH_API_VERSION,
        settings.WHATSAPP_PHONE_NUMBER_ID,
        settings.WHATSAPP_ACCESS_TOKEN,
    )


def normalizar_telefone_whatsapp(telefone: str) -> str:
    apenas_digitos = ''.join(ch for ch in telefone if ch.isdigit())
    if (
        len(apenas_digitos) < TELEFONE_E164_MIN_LENGTH
        or len(apenas_digitos) > TELEFONE_E164_MAX_LENGTH
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Telefone invalido. Use DDI + DDD + numero, sem +.',
        )
    return apenas_digitos


def post_graph_messages(payload: dict[str, Any]) -> dict[str, Any]:
    versao, phone_number_id, token = whatsapp_config()
    url = f'https://graph.facebook.com/{versao}/{phone_number_id}/messages'
    body = json.dumps(payload).encode('utf-8')
    request = UrlRequest(
        url,
        data=body,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detalhe = exc.read().decode('utf-8', errors='replace')
        try:
            detalhe_json = json.loads(detalhe)
        except json.JSONDecodeError:
            detalhe_json = {'message': detalhe}
        raise HTTPException(
            status_code=exc.code,
            detail=detalhe_json,
        ) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Falha ao conectar na Graph API: {exc.reason}',
        ) from exc


def enviar_template_whatsapp(
    *,
    telefone: str,
    nome_template: str,
    idioma: str = 'pt_BR',
    parametros: list[str] | None = None,
) -> dict[str, Any]:
    telefone_normalizado = normalizar_telefone_whatsapp(telefone)
    template: dict[str, Any] = {
        'name': nome_template,
        'language': {'code': idioma},
    }
    if parametros:
        template['components'] = [
            {
                'type': 'body',
                'parameters': [
                    {'type': 'text', 'text': str(parametro)}
                    for parametro in parametros
                ],
            }
        ]

    resposta = post_graph_messages(
        {
            'messaging_product': 'whatsapp',
            'to': telefone_normalizado,
            'type': 'template',
            'template': template,
        }
    )
    return {
        'status': 'enviado',
        'telefone': telefone_normalizado,
        'template': nome_template,
        'retorno_meta': resposta,
    }


def enviar_texto_whatsapp(*, telefone: str, mensagem: str) -> dict[str, Any]:
    telefone_normalizado = normalizar_telefone_whatsapp(telefone)
    resposta = post_graph_messages(
        {
            'messaging_product': 'whatsapp',
            'to': telefone_normalizado,
            'type': 'text',
            'text': {'body': mensagem},
        }
    )
    return {
        'status': 'enviado',
        'telefone': telefone_normalizado,
        'retorno_meta': resposta,
    }
