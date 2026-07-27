import asyncio
import importlib


def test_lifespan_nao_aguarda_sincronizacao_de_remessas(monkeypatch):
    app_module = importlib.import_module('app_prontocardio.app')
    chamadas = []

    class ThreadFalsa:
        def __init__(self, *, target, name, daemon):
            chamadas.append({
                'target': target,
                'name': name,
                'daemon': daemon,
            })

        def start(self):
            chamadas.append('iniciada')

    monkeypatch.setattr(
        app_module.settings,
        'RUN_MIGRATIONS_ON_STARTUP',
        True,
    )
    monkeypatch.setattr(app_module, 'ensure_postgres_schema', lambda: None)
    monkeypatch.setattr(app_module, 'run_postgres_migrations', lambda: None)
    monkeypatch.setattr(app_module, 'postgres_engine', object())
    monkeypatch.setattr(app_module, 'Thread', ThreadFalsa)

    async def executar_lifespan():
        async with app_module.lifespan(app_module.app):
            chamadas.append('api-disponivel')

    asyncio.run(executar_lifespan())

    assert chamadas[0]['name'] == 'sincronizacao-totais-remessas'
    assert chamadas[0]['daemon'] is True
    assert chamadas[1:] == ['iniciada', 'api-disponivel']
