import pytest

from app_prontocardio.services import iss_fortaleza


class _Response:
    def __init__(self, *, text='', content=b'', url=''):
        self.text = text
        self.content = content
        self.url = url

    @staticmethod
    def raise_for_status():
        return None


def test_baixa_pdf_pela_validacao_publica_do_iss(monkeypatch):
    formulario = '''
        <form id="validarNotaForm" action="/grpfor/validar;jsessionid=1">
          <input name="validarNotaForm:j_id92" value="Consultar">
          <input name="javax.faces.ViewState" value="j_id1">
        </form>
    '''
    resultado = '''
        <object id="j_id32:pdfLink" data="/grpfor/documento.pdf"></object>
    '''
    chamadas = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            chamadas.append(('GET', url, None))
            if url.endswith('validarNota.seam'):
                return _Response(
                    text=formulario,
                    url=iss_fortaleza.ISS_FORTALEZA_VALIDACAO_URL,
                )
            return _Response(
                content=b'%PDF-1.7\nconteudo',
                url=url,
            )

        def post(self, url, data):
            chamadas.append(('POST', url, data))
            return _Response(text=resultado, url=url)

    monkeypatch.setattr(iss_fortaleza.httpx, 'Client', Client)

    conteudo = iss_fortaleza.baixar_pdf_nfse_publica(
        '54321',
        '123456789',
        '59932105000121',
    )

    assert conteudo == b'%PDF-1.7\nconteudo'
    assert chamadas[1][2]['validarNotaForm:numNfse'] == '54321'
    assert (
        chamadas[1][2]['validarNotaForm:numCodVerificacao']
        == '123456789'
    )
    assert chamadas[2][1] == (
        'https://iss.fortaleza.ce.gov.br/grpfor/documento.pdf'
    )


def test_rejeita_endereco_de_pdf_fora_do_iss():
    with pytest.raises(
        iss_fortaleza.IssFortalezaIndisponivelError,
        match='endereço de documento inválido',
    ):
        iss_fortaleza._pdf_url(
            '<object id="j_id32:pdfLink" '
            'data="https://example.com/documento.pdf"></object>',
            iss_fortaleza.ISS_FORTALEZA_VALIDACAO_URL,
        )
