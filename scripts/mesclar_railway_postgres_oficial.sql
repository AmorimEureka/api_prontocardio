\set ON_ERROR_STOP on

BEGIN;

-- Este script pressupõe que o dump do Railway foi exposto como foreign
-- tables no schema railway_import. O banco oficial sempre é o destino e
-- seus registros são preservados. IDs do Railway são remapeados para evitar
-- colisões entre sequências que evoluíram de forma independente.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.schemata
         WHERE schema_name = 'railway_import'
    ) THEN
        RAISE EXCEPTION 'Schema railway_import não configurado';
    END IF;
END
$$;

-- Usuários são conciliados pelo e-mail. Credenciais e permissões do Railway
-- prevalecem porque eram as utilizadas pela aplicação antes da migração.
UPDATE api_prontocardio.usuarios_api AS destino
   SET senha = origem.senha,
       perfil = origem.perfil,
       ativo = origem.ativo,
       telas_permitidas = origem.telas_permitidas
  FROM railway_import.usuarios_api AS origem
 WHERE LOWER(BTRIM(destino.email)) = LOWER(BTRIM(origem.email));

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM railway_import.usuarios_api AS origem
          LEFT JOIN api_prontocardio.usuarios_api AS por_email
            ON LOWER(BTRIM(por_email.email)) = LOWER(BTRIM(origem.email))
          JOIN api_prontocardio.usuarios_api AS por_nome
            ON LOWER(BTRIM(por_nome.nome)) = LOWER(BTRIM(origem.nome))
         WHERE por_email.id IS NULL
    ) THEN
        RAISE EXCEPTION 'Usuário novo do Railway colide por nome no destino';
    END IF;
END
$$;

CREATE TEMP TABLE map_usuarios (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;

WITH base AS (
    SELECT COALESCE(MAX(id), 0) AS max_id
      FROM api_prontocardio.usuarios_api
), novos AS (
    SELECT origem.*,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS novo_id
      FROM railway_import.usuarios_api AS origem
      CROSS JOIN base
      LEFT JOIN api_prontocardio.usuarios_api AS destino
        ON LOWER(BTRIM(destino.email)) = LOWER(BTRIM(origem.email))
     WHERE destino.id IS NULL
)
INSERT INTO api_prontocardio.usuarios_api
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.usuarios_api,
        TO_JSONB(novos) || JSONB_BUILD_OBJECT('id', novos.novo_id)
    )
).*
  FROM novos;

INSERT INTO map_usuarios (railway_id, oficial_id)
SELECT origem.id, destino.id
  FROM railway_import.usuarios_api AS origem
  JOIN api_prontocardio.usuarios_api AS destino
    ON LOWER(BTRIM(destino.email)) = LOWER(BTRIM(origem.email));

-- Cadastros de referência: mantém descrições oficiais e acrescenta códigos
-- que existiam somente no Railway. Configuração de prazo do Railway prevalece.
INSERT INTO api_prontocardio.tiss
SELECT origem.*
  FROM railway_import.tiss AS origem
ON CONFLICT (codigo_termo) DO NOTHING;

UPDATE api_prontocardio.prazos_recurso_convenio AS destino
   SET convenio = origem.convenio,
       dias_para_recurso = origem.dias_para_recurso,
       data_atualizacao = origem.data_atualizacao,
       habilitado = origem.habilitado
  FROM railway_import.prazos_recurso_convenio AS origem
 WHERE destino.cd_convenio = origem.cd_convenio;

WITH base AS (
    SELECT COALESCE(MAX(id), 0) AS max_id
      FROM api_prontocardio.prazos_recurso_convenio
), novos AS (
    SELECT origem.*,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS novo_id
      FROM railway_import.prazos_recurso_convenio AS origem
      CROSS JOIN base
      LEFT JOIN api_prontocardio.prazos_recurso_convenio AS destino
        ON destino.cd_convenio = origem.cd_convenio
     WHERE destino.id IS NULL
)
INSERT INTO api_prontocardio.prazos_recurso_convenio
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.prazos_recurso_convenio,
        TO_JSONB(novos) || JSONB_BUILD_OBJECT('id', novos.novo_id)
    )
).*
  FROM novos;

-- Fontes auxiliares sem dependências transacionais.
INSERT INTO api_prontocardio.nfse_xml
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.nfse_xml,
        TO_JSONB(origem)
    )
).*
  FROM railway_import.nfse_xml AS origem
ON CONFLICT (_dlt_id) DO NOTHING;

INSERT INTO api_prontocardio.remessas_financeiras
SELECT origem.*
  FROM railway_import.remessas_financeiras AS origem
ON CONFLICT (cd_remessa) DO UPDATE
SET recebimento_integral = (
        api_prontocardio.remessas_financeiras.recebimento_integral
        OR EXCLUDED.recebimento_integral
    ),
    data_competencia = COALESCE(
        api_prontocardio.remessas_financeiras.data_competencia,
        EXCLUDED.data_competencia
    );

-- Lançamentos bancários recebem novos IDs e são usados nas conciliações.
CREATE TEMP TABLE map_lancamentos (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;

INSERT INTO map_lancamentos
SELECT origem.id,
       base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.lancamentos_extrato_bancario AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.lancamentos_extrato_bancario
  ) AS base;

INSERT INTO api_prontocardio.lancamentos_extrato_bancario
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.lancamentos_extrato_bancario,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT('id', mapa.oficial_id)
    )
).*
  FROM railway_import.lancamentos_extrato_bancario AS origem
  JOIN map_lancamentos AS mapa ON mapa.railway_id = origem.id;

-- Processo por remessa possui chave natural cd_remessa. Registros coincidentes
-- são reutilizados; os demais são incluídos com usuários remapeados.
CREATE TEMP TABLE map_processos_remessa (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL
) ON COMMIT DROP;

WITH base AS (
    SELECT COALESCE(MAX(id), 0) AS max_id
      FROM api_prontocardio.processos_conciliacao_remessa
), novos AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.processos_conciliacao_remessa AS origem
      CROSS JOIN base
      LEFT JOIN api_prontocardio.processos_conciliacao_remessa AS destino
        ON destino.cd_remessa = origem.cd_remessa
     WHERE destino.id IS NULL
)
INSERT INTO map_processos_remessa
SELECT origem.id, COALESCE(destino.id, novos.oficial_id)
  FROM railway_import.processos_conciliacao_remessa AS origem
  LEFT JOIN api_prontocardio.processos_conciliacao_remessa AS destino
    ON destino.cd_remessa = origem.cd_remessa
  LEFT JOIN novos ON novos.railway_id = origem.id;

INSERT INTO api_prontocardio.processos_conciliacao_remessa
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.processos_conciliacao_remessa,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_id', usuario.oficial_id,
            'usuario_atualizacao_id', usuario_atualizacao.oficial_id
        )
    )
).*
  FROM railway_import.processos_conciliacao_remessa AS origem
  JOIN map_processos_remessa AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN api_prontocardio.processos_conciliacao_remessa AS existente
    ON existente.id = mapa.oficial_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
  LEFT JOIN map_usuarios AS usuario_atualizacao
    ON usuario_atualizacao.railway_id = origem.usuario_atualizacao_id
 WHERE existente.id IS NULL;

-- Empresas são conciliadas por CNPJ e recebem o estado atual do Railway.
UPDATE api_prontocardio.empresas_emissoras AS destino
   SET razao_social = origem.razao_social,
       ativo = origem.ativo,
       usuario_atualizacao_id = usuario.oficial_id,
       data_atualizacao = origem.data_atualizacao
  FROM railway_import.empresas_emissoras AS origem
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_atualizacao_id
 WHERE destino.cnpj = origem.cnpj;

CREATE TEMP TABLE map_empresas (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL
) ON COMMIT DROP;

WITH base AS (
    SELECT COALESCE(MAX(id), 0) AS max_id
      FROM api_prontocardio.empresas_emissoras
), novos AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.empresas_emissoras AS origem
      CROSS JOIN base
      LEFT JOIN api_prontocardio.empresas_emissoras AS destino
        ON destino.cnpj = origem.cnpj
     WHERE destino.id IS NULL
)
INSERT INTO map_empresas
SELECT origem.id, COALESCE(destino.id, novos.oficial_id)
  FROM railway_import.empresas_emissoras AS origem
  LEFT JOIN api_prontocardio.empresas_emissoras AS destino
    ON destino.cnpj = origem.cnpj
  LEFT JOIN novos ON novos.railway_id = origem.id;

INSERT INTO api_prontocardio.empresas_emissoras
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.empresas_emissoras,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_criacao_id', criador.oficial_id,
            'usuario_atualizacao_id', atualizador.oficial_id
        )
    )
).*
  FROM railway_import.empresas_emissoras AS origem
  JOIN map_empresas AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN api_prontocardio.empresas_emissoras AS existente
    ON existente.id = mapa.oficial_id
  LEFT JOIN map_usuarios AS criador
    ON criador.railway_id = origem.usuario_criacao_id
  LEFT JOIN map_usuarios AS atualizador
    ON atualizador.railway_id = origem.usuario_atualizacao_id
 WHERE existente.id IS NULL;

-- Solicitações, lotes e emissões sempre recebem IDs novos, preservando em
-- paralelo os registros já existentes no banco oficial.
CREATE TEMP TABLE map_solicitacoes (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_solicitacoes
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.solicitacao_nota AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.solicitacao_nota
  ) AS base;

INSERT INTO api_prontocardio.solicitacao_nota
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.solicitacao_nota,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_id', usuario.oficial_id,
            'empresa_emissora_id', empresa.oficial_id
        )
    )
).*
  FROM railway_import.solicitacao_nota AS origem
  JOIN map_solicitacoes AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
  LEFT JOIN map_empresas AS empresa
    ON empresa.railway_id = origem.empresa_emissora_id;

CREATE TEMP TABLE map_lotes (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_lotes
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.lote_emissao_nfse AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.lote_emissao_nfse
  ) AS base;

INSERT INTO api_prontocardio.lote_emissao_nfse
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.lote_emissao_nfse,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_id', usuario.oficial_id
        )
    )
).*
  FROM railway_import.lote_emissao_nfse AS origem
  JOIN map_lotes AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id;

CREATE TEMP TABLE map_emissoes (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_emissoes
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.emissao_nfse AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.emissao_nfse
  ) AS base;

INSERT INTO api_prontocardio.emissao_nfse
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.emissao_nfse,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'solicitacao_nota_id', solicitacao.oficial_id,
            'lote_id', lote.oficial_id,
            'usuario_id', usuario.oficial_id,
            'empresa_emissora_id', empresa.oficial_id
        )
    )
).*
  FROM railway_import.emissao_nfse AS origem
  JOIN map_emissoes AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_solicitacoes AS solicitacao
    ON solicitacao.railway_id = origem.solicitacao_nota_id
  LEFT JOIN map_lotes AS lote ON lote.railway_id = origem.lote_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
  LEFT JOIN map_empresas AS empresa
    ON empresa.railway_id = origem.empresa_emissora_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.emissao_nfse_arquivo AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.emissao_nfse_arquivo
      ) AS base
)
INSERT INTO api_prontocardio.emissao_nfse_arquivo
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.emissao_nfse_arquivo,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'emissao_nfse_id', emissao.oficial_id
        )
    )
).*
  FROM railway_import.emissao_nfse_arquivo AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_emissoes AS emissao
    ON emissao.railway_id = origem.emissao_nfse_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.empresas_emissoras_eventos AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.empresas_emissoras_eventos
      ) AS base
)
INSERT INTO api_prontocardio.empresas_emissoras_eventos
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.empresas_emissoras_eventos,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'empresa_emissora_id', empresa.oficial_id,
            'usuario_id', usuario.oficial_id
        )
    )
).*
  FROM railway_import.empresas_emissoras_eventos AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_empresas AS empresa
    ON empresa.railway_id = origem.empresa_emissora_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.solicitacao_nota_evento AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.solicitacao_nota_evento
      ) AS base
)
INSERT INTO api_prontocardio.solicitacao_nota_evento
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.solicitacao_nota_evento,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'solicitacao_nota_id', solicitacao.oficial_id,
            'usuario_id', usuario.oficial_id
        )
    )
).*
  FROM railway_import.solicitacao_nota_evento AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_solicitacoes AS solicitacao
    ON solicitacao.railway_id = origem.solicitacao_nota_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.solicitacao_nota_workflow AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.solicitacao_nota_workflow
      ) AS base
)
INSERT INTO api_prontocardio.solicitacao_nota_workflow
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.solicitacao_nota_workflow,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'solicitacao_nota_id', solicitacao.oficial_id,
            'validado_por_id', validador.oficial_id
        )
    )
).*
  FROM railway_import.solicitacao_nota_workflow AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_solicitacoes AS solicitacao
    ON solicitacao.railway_id = origem.solicitacao_nota_id
  LEFT JOIN map_usuarios AS validador
    ON validador.railway_id = origem.validado_por_id;

-- Conciliações do Railway são preservadas como a versão ativa. Registros
-- oficiais equivalentes permanecem auditáveis, mas são inativados.
UPDATE api_prontocardio.conciliacoes_faturamento AS destino
   SET ativo = FALSE,
       data_inativacao = COALESCE(
           destino.data_inativacao,
           TIMEZONE('America/Sao_Paulo', NOW())
       )
  FROM railway_import.conciliacoes_faturamento AS origem
 WHERE destino.ativo
   AND origem.ativo
   AND destino.nfse_row_hash = origem.nfse_row_hash;

CREATE TEMP TABLE map_conciliacoes (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_conciliacoes
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.conciliacoes_faturamento AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.conciliacoes_faturamento
  ) AS base;

INSERT INTO api_prontocardio.conciliacoes_faturamento
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.conciliacoes_faturamento,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_id', usuario.oficial_id,
            'usuario_atualizacao_id', atualizador.oficial_id,
            'usuario_inativacao_id', inativador.oficial_id,
            'lancamento_extrato_id', lancamento.oficial_id
        )
    )
).*
  FROM railway_import.conciliacoes_faturamento AS origem
  JOIN map_conciliacoes AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
  LEFT JOIN map_usuarios AS atualizador
    ON atualizador.railway_id = origem.usuario_atualizacao_id
  LEFT JOIN map_usuarios AS inativador
    ON inativador.railway_id = origem.usuario_inativacao_id
  LEFT JOIN map_lancamentos AS lancamento
    ON lancamento.railway_id = origem.lancamento_extrato_id;

CREATE TEMP TABLE map_conciliacoes_remessas (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_conciliacoes_remessas
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.conciliacoes_faturamento_remessas AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.conciliacoes_faturamento_remessas
  ) AS base;

INSERT INTO api_prontocardio.conciliacoes_faturamento_remessas
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.conciliacoes_faturamento_remessas,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'conciliacao_id', conciliacao.oficial_id,
            'processo_remessa_id', processo.oficial_id
        )
    )
).*
  FROM railway_import.conciliacoes_faturamento_remessas AS origem
  JOIN map_conciliacoes_remessas AS mapa
    ON mapa.railway_id = origem.id
  JOIN map_conciliacoes AS conciliacao
    ON conciliacao.railway_id = origem.conciliacao_id
  LEFT JOIN map_processos_remessa AS processo
    ON processo.railway_id = origem.processo_remessa_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.recebimentos_remessas AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.recebimentos_remessas
      ) AS base
)
INSERT INTO api_prontocardio.recebimentos_remessas
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.recebimentos_remessas,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'conciliacao_id', conciliacao.oficial_id,
            'usuario_id', usuario.oficial_id,
            'lancamento_extrato_id', lancamento.oficial_id
        )
    )
).*
  FROM railway_import.recebimentos_remessas AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_conciliacoes AS conciliacao
    ON conciliacao.railway_id = origem.conciliacao_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
  LEFT JOIN map_lancamentos AS lancamento
    ON lancamento.railway_id = origem.lancamento_extrato_id;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.auditorias_conciliacao_faturamento AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.auditorias_conciliacao_faturamento
      ) AS base
)
INSERT INTO api_prontocardio.auditorias_conciliacao_faturamento
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.auditorias_conciliacao_faturamento,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'conciliacao_id', conciliacao.oficial_id,
            'usuario_id', usuario.oficial_id
        )
    )
).*
  FROM railway_import.auditorias_conciliacao_faturamento AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  JOIN map_conciliacoes AS conciliacao
    ON conciliacao.railway_id = origem.conciliacao_id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id;

CREATE TEMP TABLE map_registros_glosa (
    railway_id INTEGER PRIMARY KEY,
    oficial_id INTEGER NOT NULL UNIQUE
) ON COMMIT DROP;
INSERT INTO map_registros_glosa
SELECT origem.id, base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id)
  FROM railway_import.registros_glosa AS origem
  CROSS JOIN (
      SELECT COALESCE(MAX(id), 0) AS max_id
        FROM api_prontocardio.registros_glosa
  ) AS base;

INSERT INTO api_prontocardio.registros_glosa
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.registros_glosa,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'conciliacao_remessa_id', conciliacao_remessa.oficial_id
        )
    )
).*
  FROM railway_import.registros_glosa AS origem
  JOIN map_registros_glosa AS mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_conciliacoes_remessas AS conciliacao_remessa
    ON conciliacao_remessa.railway_id = origem.conciliacao_remessa_id;

INSERT INTO api_prontocardio.registros_glosa_demonstrativo_ipm (
    id_registro, registro_glosa_id, data_importacao,
    criterio_correspondencia
)
SELECT origem.id_registro, glosa.oficial_id, origem.data_importacao,
       origem.criterio_correspondencia
  FROM railway_import.registros_glosa_demonstrativo_ipm AS origem
  JOIN map_registros_glosa AS glosa
    ON glosa.railway_id = origem.registro_glosa_id
  JOIN api_prontocardio.demonstrativo_conta_ipm AS demonstrativo
    ON demonstrativo.id_registro = origem.id_registro
ON CONFLICT (id_registro) DO NOTHING;

INSERT INTO api_prontocardio.glossas_nao_vinculadas_ipm
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.glossas_nao_vinculadas_ipm,
        TO_JSONB(origem)
    )
).*
  FROM railway_import.glossas_nao_vinculadas_ipm AS origem
ON CONFLICT (id_registro) DO NOTHING;

WITH mapa AS (
    SELECT origem.id AS railway_id,
           base.max_id + ROW_NUMBER() OVER (ORDER BY origem.id) AS oficial_id
      FROM railway_import.associacoes_remessas_ipm_manuais AS origem
      CROSS JOIN (
          SELECT COALESCE(MAX(id), 0) AS max_id
            FROM api_prontocardio.associacoes_remessas_ipm_manuais
      ) AS base
)
INSERT INTO api_prontocardio.associacoes_remessas_ipm_manuais
SELECT (
    JSONB_POPULATE_RECORD(
        NULL::api_prontocardio.associacoes_remessas_ipm_manuais,
        TO_JSONB(origem) || JSONB_BUILD_OBJECT(
            'id', mapa.oficial_id,
            'usuario_id', usuario.oficial_id
        )
    )
).*
  FROM railway_import.associacoes_remessas_ipm_manuais AS origem
  JOIN mapa ON mapa.railway_id = origem.id
  LEFT JOIN map_usuarios AS usuario
    ON usuario.railway_id = origem.usuario_id
ON CONFLICT DO NOTHING;

-- Reposiciona todas as sequências tocadas pelos IDs explícitos.
DO $$
DECLARE
    tabela TEXT;
    sequencia TEXT;
    maior_id BIGINT;
BEGIN
    FOREACH tabela IN ARRAY ARRAY[
        'usuarios_api', 'prazos_recurso_convenio',
        'lancamentos_extrato_bancario',
        'processos_conciliacao_remessa', 'empresas_emissoras',
        'solicitacao_nota', 'lote_emissao_nfse', 'emissao_nfse',
        'emissao_nfse_arquivo', 'empresas_emissoras_eventos',
        'solicitacao_nota_evento', 'solicitacao_nota_workflow',
        'conciliacoes_faturamento',
        'conciliacoes_faturamento_remessas',
        'recebimentos_remessas',
        'auditorias_conciliacao_faturamento', 'registros_glosa',
        'associacoes_remessas_ipm_manuais'
    ]
    LOOP
        sequencia := PG_GET_SERIAL_SEQUENCE(
            FORMAT('api_prontocardio.%I', tabela), 'id'
        );
        EXECUTE FORMAT(
            'SELECT COALESCE(MAX(id), 0) FROM api_prontocardio.%I',
            tabela
        ) INTO maior_id;
        IF sequencia IS NOT NULL AND maior_id > 0 THEN
            PERFORM SETVAL(sequencia, maior_id, TRUE);
        END IF;
    END LOOP;
END
$$;

COMMIT;
