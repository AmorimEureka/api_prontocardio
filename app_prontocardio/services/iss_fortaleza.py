from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

ISS_FORTALEZA_ORIGIN = 'https://iss.fortaleza.ce.gov.br'
ISS_FORTALEZA_VALIDACAO_URL = (
    f'{ISS_FORTALEZA_ORIGIN}/grpfor/pagesPublic/validarNota.seam'
)
PDF_MAX_BYTES = 20 * 1024 * 1024


class IssFortalezaPdfError(RuntimeError):
    pass


class IssFortalezaIndisponivelError(IssFortalezaPdfError):
    pass


class IssFortalezaNotaNaoEncontradaError(IssFortalezaPdfError):
    pass


@dataclass(frozen=True)
class _FormularioValidacao:
    action: str
    view_state: str
    submit_name: str


class _ValidacaoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form_action: str | None = None
        self.in_form = False
        self.view_state: str | None = None
        self.submit_name: str | None = None
        self.pdf_path: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        atributos = dict(attrs)
        if tag == 'form' and atributos.get('id') == 'validarNotaForm':
            self.in_form = True
            self.form_action = atributos.get('action')
            return
        if tag == 'form':
            self.in_form = False
            return
        element_id = atributos.get('id') or ''
        if tag == 'object' and element_id.endswith(':pdfLink'):
            self.pdf_path = atributos.get('data')
            return
        if tag != 'input' or not self.in_form:
            return

        name = atributos.get('name')
        value = atributos.get('value')
        if name == 'javax.faces.ViewState':
            self.view_state = value
        elif value == 'Consultar' and name:
            self.submit_name = name

    def handle_endtag(self, tag: str) -> None:
        if tag == 'form':
            self.in_form = False


def _formulario_validacao(html: str, current_url: str) -> _FormularioValidacao:
    parser = _ValidacaoParser()
    parser.feed(html)
    if not (
        parser.form_action
        and parser.view_state
        and parser.submit_name
    ):
        raise IssFortalezaIndisponivelError(
            'O formulário público de validação do ISS está indisponível.'
        )
    action = _url_oficial(parser.form_action, current_url)
    return _FormularioValidacao(
        action=action,
        view_state=parser.view_state,
        submit_name=parser.submit_name,
    )


def _pdf_url(html: str, current_url: str) -> str:
    parser = _ValidacaoParser()
    parser.feed(html)
    if not parser.pdf_path:
        raise IssFortalezaNotaNaoEncontradaError(
            'O ISS Fortaleza não disponibilizou o PDF desta NFS-e.'
        )
    return _url_oficial(parser.pdf_path, current_url)


def _url_oficial(path: str, current_url: str) -> str:
    url = urljoin(current_url, path)
    parsed = urlparse(url)
    origin = urlparse(ISS_FORTALEZA_ORIGIN)
    if parsed.scheme != 'https' or parsed.netloc != origin.netloc:
        raise IssFortalezaIndisponivelError(
            'O ISS retornou um endereço de documento inválido.'
        )
    return url


def baixar_pdf_nfse_publica(
    numero_nfse: str,
    codigo_verificacao: str,
    prestador_cnpj: str,
    *,
    timeout: float = 30.0,
) -> bytes:
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={'User-Agent': 'Receita-Certa/1.0'},
        ) as client:
            page = client.get(ISS_FORTALEZA_VALIDACAO_URL)
            page.raise_for_status()
            formulario = _formulario_validacao(page.text, str(page.url))
            response = client.post(
                formulario.action,
                data={
                    'validarNotaForm': 'validarNotaForm',
                    'validarNotaForm:opConsulta': '0',
                    'validarNotaForm:numNfse': numero_nfse,
                    'validarNotaForm:numCodVerificacao': (
                        codigo_verificacao
                    ),
                    'validarNotaForm:opPrestadorNF': '1',
                    'validarNotaForm:nfseCnpjPrestador': prestador_cnpj,
                    formulario.submit_name: 'Consultar',
                    'javax.faces.ViewState': formulario.view_state,
                },
            )
            response.raise_for_status()
            pdf_url = _pdf_url(response.text, str(response.url))
            pdf_response = client.get(pdf_url)
            pdf_response.raise_for_status()
    except IssFortalezaPdfError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise IssFortalezaIndisponivelError(
            'O ISS Fortaleza está indisponível ou excedeu o tempo limite.'
        ) from exc
    except httpx.HTTPError as exc:
        raise IssFortalezaIndisponivelError(
            'Não foi possível consultar o documento no ISS Fortaleza.'
        ) from exc

    conteudo = pdf_response.content
    if (
        not conteudo.startswith(b'%PDF')
        or not conteudo
        or len(conteudo) > PDF_MAX_BYTES
    ):
        raise IssFortalezaIndisponivelError(
            'O ISS Fortaleza retornou um PDF inválido.'
        )
    return conteudo
