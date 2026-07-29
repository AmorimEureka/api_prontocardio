import asyncio
import importlib


def test_aplicacao_registra_rotas_criticas():
    app_module = importlib.import_module('app_prontocardio.app')
    rotas = {
        (rota.path, metodo)
        for rota in app_module.app.routes
        for metodo in getattr(rota, 'methods', set())
    }

    assert {
        ('/autenticacao/token', 'POST'),
        ('/usuarios/me', 'GET'),
        ('/app_glosas/', 'GET'),
    } <= rotas


def test_lifespan_disponibiliza_api_sem_sincronizacao_oracle(monkeypatch):
    app_module = importlib.import_module('app_prontocardio.app')
    chamadas = []

    monkeypatch.setattr(
        app_module.settings,
        'RUN_MIGRATIONS_ON_STARTUP',
        True,
    )
    monkeypatch.setattr(
        app_module,
        'ensure_postgres_schema',
        lambda: chamadas.append('schema'),
    )
    monkeypatch.setattr(
        app_module,
        'run_postgres_migrations',
        lambda: chamadas.append('migrations'),
    )

    async def executar_lifespan():
        async with app_module.lifespan(app_module.app):
            chamadas.append('api-disponivel')

    asyncio.run(executar_lifespan())

    assert chamadas == ['schema', 'migrations', 'api-disponivel']
