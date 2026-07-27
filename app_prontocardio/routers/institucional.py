from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter(tags=['institucional'])

_PRIVACY_HTML = '''<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pol?tica de Privacidade | Meu ProntoCardio</title>
  <style>
    :root { color-scheme: light; --azul:#08366d; --texto:#1f2d3d; --cinza:#607083; --borda:#d9e3ee; }
    body { margin:0; font-family: Arial, Helvetica, sans-serif; background:#f3f8fb; color:var(--texto); line-height:1.6; }
    header { background:linear-gradient(135deg,#082f5f,#0b4b86); color:white; padding:36px 24px; }
    main { max-width:920px; margin:0 auto; padding:28px 20px 48px; }
    .card { background:white; border:1px solid var(--borda); border-radius:18px; padding:28px; box-shadow:0 14px 34px rgba(8,54,109,.08); }
    h1 { margin:0; font-size:32px; }
    h2 { margin-top:28px; color:var(--azul); font-size:20px; }
    p, li { font-size:16px; }
    .muted { color:var(--cinza); }
    .brand { font-weight:700; letter-spacing:.04em; text-transform:uppercase; font-size:13px; opacity:.9; }
    footer { margin-top:28px; color:var(--cinza); font-size:14px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">Meu ProntoCardio</div>
    <h1>Pol?tica de Privacidade</h1>
    <p>Hospital ProntoCardio ? cuidado, conex?o e confian?a.</p>
  </header>
  <main>
    <section class="card">
      <p class="muted">?ltima atualiza??o: 21 de julho de 2026.</p>
      <p>Esta Pol?tica de Privacidade descreve como o Meu ProntoCardio e o Hospital ProntoCardio tratam dados pessoais utilizados em funcionalidades digitais de agendamento, confirma??o de consultas e exames, comunica??o com pacientes e apoio ao atendimento.</p>

      <h2>1. Dados que podemos tratar</h2>
      <p>Podemos tratar dados cadastrais e assistenciais necess?rios para identifica??o e organiza??o do atendimento, como nome, telefone, e-mail, data de nascimento, c?digo de paciente, conv?nio/plano, informa??es de agendamento, unidade, data, hor?rio, protocolo, orienta??es de preparo e hist?rico operacional relacionado ao atendimento.</p>

      <h2>2. Finalidades do tratamento</h2>
      <ul>
        <li>Realizar, confirmar, reagendar ou cancelar consultas e exames.</li>
        <li>Enviar comprovantes, lembretes e orienta??es de preparo por canais digitais, incluindo WhatsApp.</li>
        <li>Apoiar equipes internas do call center, recep??o e ?reas assistenciais.</li>
        <li>Gerar indicadores operacionais, como agenda, comparecimento e absente?smo.</li>
        <li>Cumprir obriga??es legais, regulat?rias e de seguran?a da informa??o.</li>
      </ul>

      <h2>3. Compartilhamento de dados</h2>
      <p>Os dados podem ser compartilhados com fornecedores tecnol?gicos e plataformas de comunica??o estritamente necess?rios para a presta??o dos servi?os, sempre observando medidas de seguran?a, confidencialidade e finalidade adequada. N?o vendemos dados pessoais.</p>

      <h2>4. WhatsApp e mensagens</h2>
      <p>Ao utilizar o WhatsApp para comunica??o, poderemos enviar mensagens relacionadas ao agendamento, confirma??o, lembrete, cancelamento e preparo de exames. O paciente pode responder ? mensagem para solicitar apoio ou orienta??o.</p>

      <h2>5. Seguran?a</h2>
      <p>Adotamos controles t?cnicos e administrativos para proteger os dados contra acessos n?o autorizados, uso indevido, altera??o, perda ou divulga??o indevida, respeitando o princ?pio do m?nimo necess?rio.</p>

      <h2>6. Direitos do titular</h2>
      <p>O titular dos dados pode solicitar informa??es, atualiza??o, corre??o ou outros direitos previstos na Lei Geral de Prote??o de Dados (LGPD), conforme aplic?vel.</p>

      <h2>7. Contato</h2>
      <p>Para d?vidas sobre esta pol?tica ou sobre o tratamento de dados pessoais, entre em contato com o Hospital ProntoCardio pelos canais oficiais de atendimento.</p>

      <footer>Esta p?gina ? disponibilizada para fins institucionais e para suporte aos servi?os digitais do Meu ProntoCardio.</footer>
    </section>
  </main>
</body>
</html>
'''

_PRIVACY_TEXT = '''Pol?tica de Privacidade - Meu ProntoCardio

O Meu ProntoCardio e o Hospital ProntoCardio tratam dados pessoais para identifica??o de pacientes, agendamento, confirma??o, cancelamento, orienta??es de preparo, lembretes e apoio operacional ao atendimento.

Os dados podem incluir nome, telefone, e-mail, data de nascimento, c?digo do paciente, conv?nio, plano, informa??es de agenda, unidade, data, hor?rio, protocolo e hist?rico operacional relacionado ao atendimento.

Os dados s?o utilizados para prestar servi?os assistenciais e administrativos, cumprir obriga??es legais e gerar indicadores operacionais. N?o vendemos dados pessoais.

Mensagens de WhatsApp podem ser enviadas para confirma??o, lembrete, cancelamento e orienta??es de preparo de consultas e exames.

Adotamos medidas de seguran?a para proteger os dados e respeitamos os direitos previstos na LGPD.

?ltima atualiza??o: 21 de julho de 2026.
'''


@router.get('/politica-privacidade', response_class=HTMLResponse, include_in_schema=False)
def politica_privacidade() -> HTMLResponse:
    return HTMLResponse(_PRIVACY_HTML)


@router.get('/meuprontocardio/politica-privacidade', response_class=HTMLResponse, include_in_schema=False)
def politica_privacidade_meuprontocardio() -> HTMLResponse:
    return HTMLResponse(_PRIVACY_HTML)


@router.get('/privacy-policy', response_class=HTMLResponse, include_in_schema=False)
def privacy_policy() -> HTMLResponse:
    return HTMLResponse(_PRIVACY_HTML)


@router.get('/politica-privacidade.txt', response_class=PlainTextResponse, include_in_schema=False)
def politica_privacidade_txt() -> PlainTextResponse:
    return PlainTextResponse(_PRIVACY_TEXT)
