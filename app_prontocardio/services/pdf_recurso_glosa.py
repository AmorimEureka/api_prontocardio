from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

CENTAVOS = Decimal('0.01')
FUNDO_CABECALHO = colors.HexColor('#ffff99')
CNPJ_PRESTADOR = '08.711.085/0001-28'
NOME_PRESTADOR = 'HOSPITAL PRONTOCARDIO'
CONTATO_FATURAMENTO = (
    'Maria Letícia (85) 3466. 3011 | faturamento.pronto@gmail.com'
)


def _valor(objeto, campo: str, padrao=None):
    if isinstance(objeto, dict):
        return objeto.get(campo, padrao)
    return getattr(objeto, campo, padrao)


def _money(valor) -> Decimal:
    if valor in (None, ''):
        return Decimal('0.00')
    try:
        return Decimal(str(valor)).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if valor:
        try:
            return date.fromisoformat(str(valor)[:10])
        except ValueError:
            return None
    return None


def _formatar_data(valor) -> str:
    data = _data(valor)
    return data.strftime('%d/%m/%Y') if data else '-'


def _formatar_decimal(valor) -> str:
    numero = Decimal(str(valor or 0))
    if numero == numero.to_integral():
        return str(int(numero))
    return f'{numero:.2f}'.replace('.', ',')


def _formatar_reais(valor) -> str:
    numero = _money(valor)
    formatado = f'{numero:,.2f}'.replace(',', '_').replace('.', ',')
    return f'R$ {formatado.replace("_", ".")}'


def _ratear_valores_recurso(
    itens: list[dict],
) -> list[tuple[dict, object, Decimal]]:
    grupos: dict[object, list[tuple[dict, object]]] = defaultdict(list)
    for item in itens:
        registro = item.get('registro_recusa')
        if not registro or _valor(registro, 'sn_ativo', 'true') != 'true':
            continue
        registro_id = _valor(registro, 'id')
        chave = (
            ('registro', registro_id)
            if registro_id
            else ('objeto', id(registro))
        )
        grupos[chave].append((item, registro))

    resultado = []
    for grupo in grupos.values():
        total_glosado = sum(
            (_money(item.get('valor_glosa')) for item, _ in grupo),
            Decimal('0.00'),
        )
        total_recurso = min(
            _money(_valor(grupo[0][1], 'valor_recursado')),
            total_glosado,
        )
        restante = total_recurso
        for indice, (item, registro) in enumerate(grupo):
            valor_glosa = _money(item.get('valor_glosa'))
            if total_glosado <= 0:
                valor_recurso = Decimal('0.00')
            elif indice == len(grupo) - 1:
                valor_recurso = min(valor_glosa, restante)
            else:
                valor_recurso = min(
                    valor_glosa,
                    _money(total_recurso * valor_glosa / total_glosado),
                )
            restante -= valor_recurso
            if valor_recurso > 0:
                resultado.append((item, registro, valor_recurso))
    return resultado


def montar_linhas_recurso_glosa(card: dict) -> list[dict]:
    itens = [
        item
        for paciente in card.get('pacientes') or []
        for item in paciente.get('itens') or []
    ]
    linhas = []
    processo = _valor(card.get('processo') or {}, 'numero_processo', '-')
    protocolo_card = card.get('numero_protocolo')
    for item, registro, valor_recurso in _ratear_valores_recurso(itens):
        linhas.append(
            {
                'processo_inicial': processo or '-',
                'remessa': (
                    item.get('numero_protocolo')
                    or protocolo_card
                    or card.get('cd_remessa')
                    or '-'
                ),
                'paciente': item.get('nm_paciente') or '-',
                'atend_alta': _formatar_data(
                    item.get('dt_alta') or item.get('dt_atendimento')
                ),
                'item_glosado': item.get('descricao') or '-',
                'qtde_apre': _formatar_decimal(item.get('qt_lancamento') or 0),
                'qtde_glosada': _formatar_decimal(
                    item.get('qt_lancamento') or 0
                ),
                'valor_apres': _money(item.get('valor_processado')),
                'valor_pago': _money(item.get('valor_liberado')),
                'valor_glosado': _money(item.get('valor_glosa')),
                'motivo_glosa': item.get('motivo_glosa_descricao') or '-',
                'valor_recurso': valor_recurso,
                'justificativa': (
                    _valor(registro, 'descricao_glosa', '') or '-'
                ),
                'data_recurso': _data(_valor(registro, 'dt_recurso')),
            }
        )
    return linhas


def _paragrafo(valor, estilo: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(valor or '-')).replace('\n', '<br/>'), estilo)


def gerar_pdf_recurso_glosa(card: dict) -> bytes:
    linhas = montar_linhas_recurso_glosa(card)
    if not linhas:
        raise ValueError('O card não possui recursos registrados.')

    buffer = BytesIO()
    pagina = landscape(A4)
    largura_util = pagina[0] - 10 * mm
    documento = SimpleDocTemplate(
        buffer,
        pagesize=pagina,
        leftMargin=5 * mm,
        rightMargin=5 * mm,
        topMargin=5 * mm,
        bottomMargin=5 * mm,
        title='Recurso de Glosa IPM',
        author=NOME_PRESTADOR,
    )
    estilo = ParagraphStyle(
        'celula',
        fontName='Helvetica',
        fontSize=6.2,
        leading=7.2,
        alignment=TA_CENTER,
        textColor=colors.black,
        splitLongWords=True,
    )
    estilo_negrito = ParagraphStyle(
        'celula-negrito',
        parent=estilo,
        fontName='Helvetica-Bold',
    )
    estilo_titulo = ParagraphStyle(
        'titulo',
        parent=estilo_negrito,
        fontSize=8,
        leading=9,
    )
    datas_recurso = [
        linha['data_recurso']
        for linha in linhas
        if linha['data_recurso']
    ]
    data_recurso = max(datas_recurso) if datas_recurso else date.today()
    total_recurso = sum(
        (linha['valor_recurso'] for linha in linhas),
        Decimal('0.00'),
    )

    tabela_titulo = Table(
        [[
            _paragrafo(
                f'RECURSO DE GLOSA IPM {data_recurso.year}',
                estilo_titulo,
            )
        ]],
        colWidths=[largura_util],
    )
    tabela_titulo.setStyle(
        TableStyle(
            [
                ('GRID', (0, 0), (-1, -1), 0.7, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]
        )
    )
    cabecalho = Table(
        [
            [
                _paragrafo('CNPJ', estilo_negrito),
                _paragrafo('PRESTADOR', estilo_negrito),
                '',
            ],
            [
                _paragrafo(CNPJ_PRESTADOR, estilo),
                _paragrafo(NOME_PRESTADOR, estilo),
                '',
            ],
            [
                _paragrafo('PESSOA / FONE / E-MAIL', estilo_negrito),
                _paragrafo('DATA DO RECURSO', estilo_negrito),
                _paragrafo('VALOR TOTAL DO RECURSO', estilo_negrito),
            ],
            [
                _paragrafo(CONTATO_FATURAMENTO, estilo),
                _paragrafo(_formatar_data(data_recurso), estilo),
                _paragrafo(_formatar_reais(total_recurso), estilo_negrito),
            ],
        ],
        colWidths=[
            largura_util * 0.52,
            largura_util * 0.24,
            largura_util * 0.24,
        ],
    )
    cabecalho.setStyle(
        TableStyle(
            [
                ('GRID', (0, 0), (-1, -1), 0.7, colors.black),
                ('BACKGROUND', (0, 0), (-1, 0), FUNDO_CABECALHO),
                ('BACKGROUND', (0, 2), (-1, 2), FUNDO_CABECALHO),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]
        )
    )

    titulos = (
        'PROCESSO<br/>INICIAL',
        'REMESSA',
        'PACIENTE',
        'ATEND.<br/>ALTA',
        'ITEM GLOSADO',
        'QTDE<br/>APRE',
        'QTDE<br/>GLOSADA',
        'VALOR<br/>APRES',
        'VALOR<br/>PAGO',
        'VALOR<br/>GLOSADO',
        'MOTIVO DA GLOSA',
        'VALOR DO<br/>RECURSO',
        'JUSTIFICATIVA',
    )
    dados = [[Paragraph(titulo, estilo_negrito) for titulo in titulos]]
    for linha in linhas:
        dados.append(
            [
                _paragrafo(linha['processo_inicial'], estilo),
                _paragrafo(linha['remessa'], estilo),
                _paragrafo(linha['paciente'], estilo),
                _paragrafo(linha['atend_alta'], estilo),
                _paragrafo(linha['item_glosado'], estilo),
                _paragrafo(linha['qtde_apre'], estilo),
                _paragrafo(linha['qtde_glosada'], estilo),
                _paragrafo(_formatar_reais(linha['valor_apres']), estilo),
                _paragrafo(_formatar_reais(linha['valor_pago']), estilo),
                _paragrafo(_formatar_reais(linha['valor_glosado']), estilo),
                _paragrafo(linha['motivo_glosa'], estilo),
                _paragrafo(_formatar_reais(linha['valor_recurso']), estilo),
                _paragrafo(linha['justificativa'], estilo),
            ]
        )
    larguras = [
        largura_util * proporcao
        for proporcao in (
            0.073,
            0.067,
            0.085,
            0.067,
            0.115,
            0.042,
            0.052,
            0.067,
            0.06,
            0.064,
            0.102,
            0.066,
            0.14,
        )
    ]
    tabela_itens = Table(dados, colWidths=larguras, repeatRows=1)
    tabela_itens.setStyle(
        TableStyle(
            [
                ('GRID', (0, 0), (-1, -1), 0.7, colors.black),
                ('BACKGROUND', (0, 0), (-1, 0), FUNDO_CABECALHO),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]
        )
    )
    documento.build(
        [tabela_titulo, cabecalho, Spacer(1, 1.5 * mm), tabela_itens]
    )
    return buffer.getvalue()
