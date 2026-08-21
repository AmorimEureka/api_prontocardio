BEGIN;

DO $$
DECLARE
    quantidade_protocolos INTEGER;
    valor_glosado NUMERIC(18, 2);
BEGIN
    WITH protocolos(numero_protocolo) AS (
        VALUES
            ('4534814'), ('4534875'), ('4534925'), ('4535040'),
            ('4535122'), ('4535377'), ('4535529'), ('4696396')
    ), totais AS (
        SELECT demo.numero_protocolo,
               SUM(demo.valor_glosa) AS valor_glosado
          FROM api_prontocardio.demonstrativo_conta_ipm AS demo
          JOIN protocolos
            ON protocolos.numero_protocolo = BTRIM(demo.numero_protocolo)
         GROUP BY demo.numero_protocolo
    )
    SELECT COUNT(*), SUM(totais.valor_glosado)
      INTO quantidade_protocolos, valor_glosado
      FROM totais;

    IF quantidade_protocolos <> 8 OR valor_glosado <> 1640.32 THEN
        RAISE EXCEPTION
            'Demonstrativo divergente: protocolos=%, valor=%',
            quantidade_protocolos, valor_glosado;
    END IF;
END
$$;

WITH remessas(cd_remessa, valor_total) AS (
    VALUES
        (16840, 1201.86::NUMERIC),
        (17058, 5957.56::NUMERIC),
        (17059, 19806.14::NUMERIC),
        (17064, 5864.81::NUMERIC),
        (17066, 32976.58::NUMERIC),
        (17069, 41610.38::NUMERIC),
        (17072, 58902.72::NUMERIC),
        (17088, 32619.45::NUMERIC)
)
INSERT INTO api_prontocardio.remessas_financeiras (
    cd_remessa,
    convenio,
    cnpj_convenio,
    valor_total,
    recebimento_integral,
    data_competencia
)
SELECT cd_remessa,
       'IPM',
       '07965184000173',
       valor_total,
       FALSE,
       DATE '2026-02-01'
  FROM remessas
ON CONFLICT (cd_remessa) DO NOTHING;

WITH associacoes(nr, cd_remessa) AS (
    VALUES
        ('4534814', 17058),
        ('4534875', 17059),
        ('4534925', 17064),
        ('4535040', 17066),
        ('4535122', 17069),
        ('4535377', 17072),
        ('4535529', 17088),
        ('4696396', 16840)
), usuario AS (
    SELECT id
      FROM api_prontocardio.usuarios_api
     WHERE LOWER(email) = 'raffaekk@gmail.com'
       AND ativo IS TRUE
)
INSERT INTO api_prontocardio.associacoes_remessas_ipm_manuais (
    numero_processo,
    competencia_producao,
    nr,
    cd_remessa,
    usuario_id
)
SELECT 'P094380/2026',
       '02/2026',
       associacoes.nr,
       associacoes.cd_remessa,
       usuario.id
  FROM associacoes
 CROSS JOIN usuario
ON CONFLICT DO NOTHING;

DO $$
DECLARE
    quantidade_associacoes INTEGER;
BEGIN
    SELECT COUNT(*)
      INTO quantidade_associacoes
      FROM api_prontocardio.associacoes_remessas_ipm_manuais
     WHERE UPPER(BTRIM(numero_processo)) = 'P094380/2026'
       AND competencia_producao = '02/2026'
       AND nr IN (
           '4534814', '4534875', '4534925', '4535040',
           '4535122', '4535377', '4535529', '4696396'
       );

    IF quantidade_associacoes <> 8 THEN
        RAISE EXCEPTION
            'Associações incompletas após correção: %',
            quantidade_associacoes;
    END IF;
END
$$;

COMMIT;
