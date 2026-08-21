BEGIN;

DO $$
DECLARE
    remessas_nao_ipm INTEGER;
    processos_nao_ipm INTEGER;
    conciliacoes_nao_ipm INTEGER;
    vinculos_nao_ipm INTEGER;
    registros_nao_ipm INTEGER;
    recebimentos_nao_ipm INTEGER;
    tratativas_nao_ipm INTEGER;
    conciliacoes_mistas INTEGER;
BEGIN
    SELECT COUNT(*)
      INTO remessas_nao_ipm
      FROM api_prontocardio.remessas_financeiras
     WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

    SELECT COUNT(*)
      INTO processos_nao_ipm
      FROM api_prontocardio.processos_conciliacao_remessa AS proc
     WHERE EXISTS (
               SELECT 1
                 FROM api_prontocardio.remessas_financeiras AS rem
                WHERE rem.cd_remessa = proc.cd_remessa
                  AND COALESCE(UPPER(BTRIM(rem.convenio)), '') <> 'IPM'
           );

    SELECT COUNT(*)
      INTO conciliacoes_nao_ipm
      FROM api_prontocardio.conciliacoes_faturamento
     WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

    SELECT COUNT(*)
      INTO vinculos_nao_ipm
      FROM api_prontocardio.conciliacoes_faturamento_remessas
     WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

    SELECT COUNT(*)
      INTO registros_nao_ipm
      FROM api_prontocardio.registros_glosa
     WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

    SELECT COUNT(*)
      INTO recebimentos_nao_ipm
      FROM api_prontocardio.recebimentos_remessas AS recebimento
      JOIN api_prontocardio.remessas_financeiras AS rem
        ON rem.cd_remessa = recebimento.cd_remessa
     WHERE COALESCE(UPPER(BTRIM(rem.convenio)), '') <> 'IPM';

    SELECT COUNT(*)
      INTO tratativas_nao_ipm
      FROM api_prontocardio.registros_glosa
     WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM'
       AND dt_recurso IS NOT NULL;

    SELECT COUNT(*)
      INTO conciliacoes_mistas
      FROM (
            SELECT conciliacao_id
              FROM api_prontocardio.conciliacoes_faturamento_remessas
             GROUP BY conciliacao_id
            HAVING BOOL_OR(UPPER(BTRIM(convenio)) = 'IPM')
               AND BOOL_OR(UPPER(BTRIM(convenio)) <> 'IPM')
           ) AS mistas;

    IF ROW(
        remessas_nao_ipm,
        processos_nao_ipm,
        conciliacoes_nao_ipm,
        vinculos_nao_ipm,
        registros_nao_ipm,
        recebimentos_nao_ipm,
        tratativas_nao_ipm,
        conciliacoes_mistas
    ) <> ROW(466, 466, 499, 499, 15631, 0, 0, 0) THEN
        RAISE EXCEPTION
            'Carga não IPM divergente: remessas=%, processos=%, '
            'conciliações=%, vínculos=%, glosas=%, recebimentos=%, '
            'tratativas=%, mistas=%',
            remessas_nao_ipm,
            processos_nao_ipm,
            conciliacoes_nao_ipm,
            vinculos_nao_ipm,
            registros_nao_ipm,
            recebimentos_nao_ipm,
            tratativas_nao_ipm,
            conciliacoes_mistas;
    END IF;
END
$$;

DELETE FROM api_prontocardio.registros_glosa
 WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

DELETE FROM api_prontocardio.conciliacoes_faturamento
 WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

DELETE FROM api_prontocardio.processos_conciliacao_remessa AS proc
 WHERE EXISTS (
           SELECT 1
             FROM api_prontocardio.remessas_financeiras AS rem
            WHERE rem.cd_remessa = proc.cd_remessa
              AND COALESCE(UPPER(BTRIM(rem.convenio)), '') <> 'IPM'
       );

DELETE FROM api_prontocardio.remessas_financeiras
 WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM';

DO $$
DECLARE
    registros_restantes INTEGER;
BEGIN
    SELECT SUM(quantidade)
      INTO registros_restantes
      FROM (
            SELECT COUNT(*) AS quantidade
              FROM api_prontocardio.remessas_financeiras
             WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM'
            UNION ALL
            SELECT COUNT(*)
              FROM api_prontocardio.conciliacoes_faturamento
             WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM'
            UNION ALL
            SELECT COUNT(*)
              FROM api_prontocardio.conciliacoes_faturamento_remessas
             WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM'
            UNION ALL
            SELECT COUNT(*)
              FROM api_prontocardio.registros_glosa
             WHERE COALESCE(UPPER(BTRIM(convenio)), '') <> 'IPM'
           ) AS contagens;

    IF registros_restantes <> 0 THEN
        RAISE EXCEPTION
            'Ainda existem % registros não IPM após a limpeza',
            registros_restantes;
    END IF;
END
$$;

COMMIT;
