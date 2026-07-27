from datetime import date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import get_session_oracle
from app_prontocardio.models import Usuario
from app_prontocardio.security import valida_token_usuario_atual

router = APIRouter(prefix="/biq", tags=["biq"])

ValidaUsuarioAtual = Annotated[Usuario, Depends(valida_token_usuario_atual)]



def _normalizar_convenios(cd_convenio: str | None):
    if not cd_convenio:
        return None
    codigos = [item.strip() for item in str(cd_convenio).split(',') if item.strip()]
    if not codigos:
        return None
    if any(not item.isdigit() for item in codigos):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="cd_convenio deve conter apenas números separados por vírgula.",
        )
    return ','.join(dict.fromkeys(codigos))


def _periodo_inclusivo(
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = None,
    procedimento: str | None = None,
):
    if data_fim < data_inicio:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="data_fim deve ser igual ou posterior a data_inicio.",
        )
    data_fim_exclusiva = data_fim + timedelta(days=1)
    return {
        "data_inicio": data_inicio,
        "data_fim_exclusiva": data_fim_exclusiva,
        "data_inicio_receita": max(data_inicio, data_fim_exclusiva - timedelta(days=7)),
        "cd_convenio": _normalizar_convenios(cd_convenio),
        "procedimento": procedimento,
    }


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows_to_dict(rows):
    return [
        {key: _json_value(value) for key, value in dict(row).items()}
        for row in rows
    ]

CONSULTA_TESTE_ERGOMETRICO = text(
    """
    SELECT i.CD_IT_AGENDA_CENTRAL AS cd_it_agenda_central,
           a.CD_ATENDIMENTO AS cd_atendimento,
           i.HR_AGENDA AS data_agenda,
           a.HR_ATENDIMENTO AS dt_atendimento,
           i.CD_PACIENTE AS cd_paciente,
           pac.NM_PACIENTE AS nm_paciente,
           a.CD_PRESTADOR AS cd_prestador,
           p.NM_PRESTADOR AS nm_prestador,
           p.DS_CODIGO_CONSELHO AS crm,
           ia.CD_ITEM_AGENDAMENTO AS cd_item_agendamento,
           ia.DS_ITEM_AGENDAMENTO AS ds_item_agendamento,
           i.DS_OBSERVACAO AS ds_observacao,
           i.DS_OBSERVACAO_GERAL AS ds_observacao_geral,
           a.TP_ATENDIMENTO AS tp_atendimento
      FROM DBAMV.IT_AGENDA_CENTRAL i
      JOIN DBAMV.ITEM_AGENDAMENTO ia
        ON ia.CD_ITEM_AGENDAMENTO = i.CD_ITEM_AGENDAMENTO
      LEFT JOIN DBAMV.PACIENTE pac
        ON pac.CD_PACIENTE = i.CD_PACIENTE
      LEFT JOIN DBAMV.ATENDIME a
        ON a.CD_PACIENTE = i.CD_PACIENTE
       AND a.TP_ATENDIMENTO = 'E'
       AND TRUNC(a.HR_ATENDIMENTO) = TRUNC(i.HR_AGENDA)
      LEFT JOIN DBAMV.PRESTADOR p
        ON p.CD_PRESTADOR = a.CD_PRESTADOR
     WHERE i.HR_AGENDA >= :data_inicio
       AND i.HR_AGENDA < :data_fim
       AND UPPER(ia.DS_ITEM_AGENDAMENTO) LIKE '%ERGOMETR%'
       AND (
            UPPER(NVL(i.DS_OBSERVACAO, '')) LIKE '%CONFIRMADO%'
         OR UPPER(NVL(i.DS_OBSERVACAO_GERAL, '')) LIKE '%CONFIRMADO%'
       )
     ORDER BY i.HR_AGENDA, pac.NM_PACIENTE, a.CD_ATENDIMENTO
    """
)

CONSULTA_INDICADORES_HOSPITALARES_RESUMO = text(
    """
    WITH leitos AS (
        SELECT l.cd_leito
        FROM dbamv.leito l
        WHERE NVL(l.tp_situacao, 'A') <> 'I'
          AND NVL(l.dt_ativacao, TRUNC(SYSDATE)) <= TRUNC(SYSDATE)
          AND NVL(l.dt_desativacao, TRUNC(SYSDATE) + 1) > TRUNC(SYSDATE)
    ),
    ocupacao AS (
        SELECT
            COUNT(DISTINCT l.cd_leito) AS total_leitos,
            COUNT(DISTINCT CASE
                WHEN a.cd_atendimento IS NOT NULL THEN l.cd_leito
            END) AS leitos_ocupados
        FROM leitos l
        LEFT JOIN dbamv.atendime a
          ON a.cd_leito = l.cd_leito
         AND a.tp_atendimento = 'I'
         AND TRUNC(a.dt_atendimento) <= TRUNC(SYSDATE)
         AND NVL(TRUNC(a.dt_alta), TRUNC(SYSDATE) + 1) > TRUNC(SYSDATE)
         AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
    )
    SELECT
        (SELECT NVL(SUM(valor_item), 0)
           FROM (
                SELECT
                    CASE
                        WHEN NVL(irf.sn_pertence_pacote, 'N') = 'S' THEN 0
                        ELSE NVL(irf.vl_total_conta, 0)
                    END AS valor_item
                FROM dbamv.reg_fat rf
                JOIN dbamv.itreg_fat irf
                  ON irf.cd_reg_fat = rf.cd_reg_fat
                WHERE irf.dt_lancamento >= :data_inicio_receita
                  AND irf.dt_lancamento <  :data_fim_exclusiva
                  AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(rf.cd_convenio) || ',') > 0)
                UNION ALL
                SELECT
                    CASE
                        WHEN NVL(ira.sn_pertence_pacote, 'N') = 'S' THEN 0
                        ELSE NVL(ira.vl_total_conta, 0)
                    END AS valor_item
                FROM dbamv.reg_amb ra
                JOIN dbamv.itreg_amb ira
                  ON ira.cd_reg_amb = ra.cd_reg_amb
                WHERE ra.dt_lancamento >= :data_inicio_receita
                  AND ra.dt_lancamento <  :data_fim_exclusiva
                  AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(ira.cd_convenio, ra.cd_convenio)) || ',') > 0)
           )
        ) AS receita_periodo,
        (SELECT COUNT(*)
          FROM dbamv.atendime a
          WHERE a.dt_atendimento >= :data_inicio
            AND a.dt_atendimento <  :data_fim_exclusiva
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)) AS atendimentos,
        (SELECT COUNT(*)
           FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_atendimento >= :data_inicio
            AND a.dt_atendimento <  :data_fim_exclusiva
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)) AS internacoes,
        (SELECT COUNT(*)
           FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_atendimento >= TRUNC(SYSDATE) - 6
            AND a.dt_atendimento <  TRUNC(SYSDATE) + 1
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)) AS internacoes_7d,
        (SELECT ROUND(
                    leitos_ocupados / NULLIF(total_leitos, 0),
                    4
                )
           FROM ocupacao) AS taxa_ocupacao_atual,
        (SELECT ROUND(
                    AVG(TRUNC(a.dt_alta) - TRUNC(a.dt_atendimento)),
                    2
                )
           FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_alta IS NOT NULL
            AND a.dt_alta >= :data_inicio
            AND a.dt_alta <  :data_fim_exclusiva
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)) AS permanencia_media,
        (SELECT COUNT(*)
          FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_alta IS NULL
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
        ) AS internados_atual,
        (SELECT COUNT(*)
           FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_alta IS NULL
            AND TRUNC(SYSDATE) - TRUNC(a.dt_atendimento) > 5
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
        ) AS internados_mais_5_dias,
        (SELECT NVL(SUM(
                    GREATEST(
                        LEAST(NVL(TRUNC(a.dt_alta) + 1, :data_fim_exclusiva), :data_fim_exclusiva) -
                        GREATEST(TRUNC(a.dt_atendimento), :data_inicio),
                        0
                    )
                ), 0)
           FROM dbamv.atendime a
          WHERE a.tp_atendimento = 'I'
            AND a.dt_atendimento < :data_fim_exclusiva
            AND NVL(a.dt_alta, :data_fim_exclusiva) >= :data_inicio
            AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
        ) AS paciente_dia_periodo
    FROM dual
    """
)


CONSULTA_INDICADORES_HOSPITALARES_SERIES = text(
    """
    WITH dias AS (
        SELECT :data_inicio + LEVEL - 1 AS dt_ref
        FROM dual
        CONNECT BY :data_inicio + LEVEL - 1 < :data_fim_exclusiva
    ),
    agenda AS (
        SELECT
            TRUNC(i.hr_agenda) AS dt_ref,
            COUNT(*) AS agendamentos,
            SUM(CASE WHEN i.cd_atendimento IS NOT NULL THEN 1 ELSE 0 END)
                AS atendidos
        FROM dbamv.it_agenda_central i
        WHERE i.hr_agenda >= :data_inicio
          AND i.hr_agenda <  :data_fim_exclusiva
          AND NVL(i.sn_bloqueado, 'N') = 'N'
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(i.cd_convenio) || ',') > 0)
        GROUP BY TRUNC(i.hr_agenda)
    ),
    internacao AS (
        SELECT
            TRUNC(a.dt_atendimento) AS dt_ref,
            COUNT(*) AS internacoes
        FROM dbamv.atendime a
        WHERE a.tp_atendimento = 'I'
          AND a.dt_atendimento >= :data_inicio
          AND a.dt_atendimento <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
        GROUP BY TRUNC(a.dt_atendimento)
    ),
    altas AS (
        SELECT
            TRUNC(a.dt_alta) AS dt_ref,
            COUNT(*) AS altas
        FROM dbamv.atendime a
        WHERE a.tp_atendimento = 'I'
          AND a.dt_alta >= :data_inicio
          AND a.dt_alta <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
        GROUP BY TRUNC(a.dt_alta)
    ),
    receita AS (
        SELECT
            dt_ref,
            SUM(valor_item) AS receita
        FROM (
            SELECT
                TRUNC(irf.dt_lancamento) AS dt_ref,
                CASE
                    WHEN NVL(irf.sn_pertence_pacote, 'N') = 'S' THEN 0
                    ELSE NVL(irf.vl_total_conta, 0)
                END AS valor_item
            FROM dbamv.reg_fat rf
            JOIN dbamv.itreg_fat irf
              ON irf.cd_reg_fat = rf.cd_reg_fat
            WHERE irf.dt_lancamento >= :data_inicio_receita
              AND irf.dt_lancamento <  :data_fim_exclusiva
              AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(rf.cd_convenio) || ',') > 0)
            UNION ALL
            SELECT
                TRUNC(ra.dt_lancamento) AS dt_ref,
                CASE
                    WHEN NVL(ira.sn_pertence_pacote, 'N') = 'S' THEN 0
                    ELSE NVL(ira.vl_total_conta, 0)
                END AS valor_item
            FROM dbamv.reg_amb ra
            JOIN dbamv.itreg_amb ira
              ON ira.cd_reg_amb = ra.cd_reg_amb
            WHERE ra.dt_lancamento >= :data_inicio_receita
              AND ra.dt_lancamento <  :data_fim_exclusiva
              AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(ira.cd_convenio, ra.cd_convenio)) || ',') > 0)
        )
        GROUP BY dt_ref
    )
    SELECT
        d.dt_ref,
        NVL(a.agendamentos, 0) AS agendamentos,
        NVL(a.atendidos, 0) AS atendidos,
        NVL(i.internacoes, 0) AS internacoes,
        NVL(al.altas, 0) AS altas,
        NVL(r.receita, 0) AS receita
    FROM dias d
    LEFT JOIN agenda a
      ON a.dt_ref = d.dt_ref
    LEFT JOIN internacao i
      ON i.dt_ref = d.dt_ref
    LEFT JOIN altas al
      ON al.dt_ref = d.dt_ref
    LEFT JOIN receita r
      ON r.dt_ref = d.dt_ref
    ORDER BY d.dt_ref
    """
)


CONSULTA_AGENDA_AMBULATORIAL = text(
    """
    WITH itens AS (
        SELECT
            i.cd_agenda_central,
            COUNT(*) AS itens_agenda_registrados,
            SUM(CASE WHEN i.cd_paciente IS NOT NULL THEN 1 ELSE 0 END)
                AS agendamentos_marcados,
            SUM(CASE WHEN i.cd_atendimento IS NOT NULL THEN 1 ELSE 0 END)
                AS agendamentos_com_atendimento,
            SUM(CASE
                WHEN UPPER(NVL(i.ds_observacao, '') || ' ' ||
                           NVL(i.ds_observacao_geral, '')) LIKE '%CONFIRMADO%'
                    THEN 1
                ELSE 0
            END) AS agendamentos_confirmados
            ,
            SUM(CASE
                WHEN UPPER(NVL(i.ds_observacao, '') || ' ' ||
                           NVL(i.ds_observacao_geral, '')) LIKE '%CONFIRMADO%'
                 AND i.cd_atendimento IS NULL
                    THEN 1
                ELSE 0
            END) AS confirmados_sem_atendimento
        FROM dbamv.it_agenda_central i
        WHERE i.hr_agenda >= :data_inicio
          AND i.hr_agenda <  :data_fim_exclusiva
          AND NVL(i.sn_bloqueado, 'N') = 'N'
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(i.cd_convenio) || ',') > 0)
        GROUP BY i.cd_agenda_central
    )
    SELECT
        TRUNC(ac.dt_agenda) AS dt_agenda,
        CASE
            WHEN TRUNC(ac.dt_agenda) - TRUNC(ac.dt_agenda, 'IW')
                 BETWEEN 0 AND 4
                THEN 'SEMANA'
            ELSE 'FINAL_SEMANA'
        END AS tipo_dia,
        ac.cd_prestador,
        pr.nm_prestador,
        ac.cd_setor,
        s.nm_setor,
        ac.cd_recurso_central,
        rc.ds_recurso_central,
        CASE
            WHEN TO_NUMBER(TO_CHAR(ac.hr_inicio, 'HH24')) < 12
                THEN 'MANHA'
            WHEN TO_NUMBER(TO_CHAR(ac.hr_inicio, 'HH24')) < 18
                THEN 'TARDE'
            ELSE 'NOITE'
        END AS turno,
        TO_CHAR(ac.hr_inicio, 'HH24:MI') AS hora_inicio_turno,
        TO_CHAR(ac.hr_fim, 'HH24:MI') AS hora_fim_turno,
        ac.qt_atendimento AS capacidade_agenda,
        NVL(it.agendamentos_marcados, 0) AS agendamentos_marcados,
        NVL(it.itens_agenda_registrados, 0) AS itens_agenda_registrados,
        NVL(it.agendamentos_com_atendimento, 0)
            AS agendamentos_com_atendimento,
        NVL(it.agendamentos_confirmados, 0) AS agendamentos_confirmados,
        NVL(it.confirmados_sem_atendimento, 0) AS confirmados_sem_atendimento,
        GREATEST(
            NVL(it.agendamentos_marcados, 0) -
            NVL(it.agendamentos_com_atendimento, 0),
            0
        ) AS marcados_sem_atendimento,
        GREATEST(
            NVL(ac.qt_atendimento, 0) - NVL(it.agendamentos_marcados, 0),
            0
        ) AS vagas_nao_marcadas,
        ROUND(
            NVL(it.agendamentos_marcados, 0) /
                NULLIF(ac.qt_atendimento, 0),
            4
        ) AS perc_agenda_marcada,
        ROUND(
            NVL(it.agendamentos_com_atendimento, 0) /
                NULLIF(it.agendamentos_marcados, 0),
            4
        ) AS perc_comparecimento_marcados,
        ROUND(
            GREATEST(
                NVL(it.agendamentos_marcados, 0) -
                NVL(it.agendamentos_com_atendimento, 0),
                0
            ) / NULLIF(it.agendamentos_marcados, 0),
            4
        ) AS perc_absenteismo_marcados,
        ROUND(
            NVL(it.confirmados_sem_atendimento, 0) /
                NULLIF(it.agendamentos_confirmados, 0),
            4
        ) AS perc_absenteismo_confirmados
    FROM dbamv.agenda_central ac
    LEFT JOIN itens it
      ON it.cd_agenda_central = ac.cd_agenda_central
    LEFT JOIN dbamv.prestador pr
      ON pr.cd_prestador = ac.cd_prestador
    LEFT JOIN dbamv.setor s
      ON s.cd_setor = ac.cd_setor
    LEFT JOIN dbamv.recurso_central rc
      ON rc.cd_recurso_central = ac.cd_recurso_central
    WHERE ac.tp_agenda = 'A'
      AND NVL(ac.sn_ativo, 'S') = 'S'
      AND ac.dt_agenda >= :data_inicio
      AND ac.dt_agenda <  :data_fim_exclusiva
      AND (:cd_convenio IS NULL OR it.cd_agenda_central IS NOT NULL)
    ORDER BY dt_agenda, nm_prestador, turno
    """
)


CONSULTA_FLUXO_AMBULATORIO_EXAMES = text(
    """
    WITH eventos AS (
        SELECT
            stp.cd_atendimento,
            stp.cd_triagem_atendimento,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 1  THEN stp.dh_processo END) AS dh_totem,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 20 THEN stp.dh_processo END) AS dh_cha_atd_adm,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 21 THEN stp.dh_processo END) AS dh_cadastro_ini,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 22 THEN stp.dh_processo END) AS dh_cadastro_fim,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 30 THEN stp.dh_processo END) AS dh_cham_atd_med,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 31 THEN stp.dh_processo END) AS dh_consulta_ini,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 32 THEN stp.dh_processo END) AS dh_consulta_fim,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 90 THEN stp.dh_processo END) AS dh_alta
        FROM dbamv.sacr_tempo_processo stp
        WHERE stp.dh_processo >= :data_inicio
          AND stp.dh_processo <  :data_fim_exclusiva
          AND stp.cd_atendimento IS NOT NULL
        GROUP BY
            stp.cd_atendimento,
            stp.cd_triagem_atendimento
    ),
    base AS (
        SELECT
            e.cd_atendimento,
            NVL(ta.cd_paciente, a.cd_paciente) AS cd_paciente,
            NVL(ta.nm_paciente, p.nm_paciente) AS nm_paciente,
            ta.ds_senha,
            a.tp_atendimento,
            CASE a.tp_atendimento
                WHEN 'A' THEN 'AMBULATORIO'
                WHEN 'E' THEN 'EXAMES'
                ELSE a.tp_atendimento
            END AS tipo_fluxo,
            a.cd_convenio,
            c.nm_convenio,
            pr.nm_prestador,
            oa.ds_ori_ate,
            e.dh_totem,
            e.dh_cha_atd_adm,
            e.dh_cadastro_ini,
            e.dh_cadastro_fim,
            e.dh_cham_atd_med,
            e.dh_consulta_ini,
            e.dh_consulta_fim,
            e.dh_alta,
            ROUND((NVL(e.dh_cadastro_ini, e.dh_cha_atd_adm) - e.dh_totem) * 1440, 2)
                AS min_senha_ate_guiche,
            ROUND((NVL(e.dh_consulta_ini, e.dh_cham_atd_med) - NVL(e.dh_cadastro_fim, e.dh_cadastro_ini)) * 1440, 2)
                AS min_guiche_ate_medico,
            ROUND((e.dh_alta - NVL(e.dh_consulta_fim, e.dh_consulta_ini)) * 1440, 2)
                AS min_medico_ate_alta,
            ROUND((NVL(e.dh_alta, e.dh_consulta_fim) - e.dh_totem) * 1440, 2)
                AS min_total_fluxo
        FROM eventos e
        LEFT JOIN dbamv.triagem_atendimento ta
          ON ta.cd_triagem_atendimento = e.cd_triagem_atendimento
        LEFT JOIN dbamv.atendime a
          ON a.cd_atendimento = e.cd_atendimento
        LEFT JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.convenio c
          ON c.cd_convenio = a.cd_convenio
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = a.cd_prestador
        LEFT JOIN dbamv.ori_ate oa
          ON oa.cd_ori_ate = a.cd_ori_ate
        WHERE a.tp_atendimento IN ('A', 'E')
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
    )
    SELECT *
    FROM (
        SELECT *
        FROM base
        WHERE dh_totem IS NOT NULL
           OR dh_cha_atd_adm IS NOT NULL
           OR dh_cham_atd_med IS NOT NULL
        ORDER BY NVL(dh_totem, NVL(dh_cha_atd_adm, dh_cham_atd_med)) DESC
    )
    WHERE ROWNUM <= :limite
    """
)


CONSULTA_EXAMES_PROCEDIMENTOS = text(
    """
    WITH exames AS (
        SELECT
            'LABORATORIO' AS tipo_exame_real,
            pl.cd_atendimento,
            a.cd_paciente,
            p.nm_paciente,
            NVL(pl.cd_convenio, a.cd_convenio) AS cd_convenio,
            c.nm_convenio,
            NVL(pl.cd_prestador, a.cd_prestador) AS cd_prestador,
            pr.nm_prestador,
            a.cd_ori_ate,
            oa.ds_ori_ate,
            pl.cd_ped_lab AS cd_pedido_exame,
            il.cd_itped_lab AS cd_item_exame,
            il.cd_exa_lab AS cd_exame,
            el.nm_exa_lab AS procedimento_exame,
            el.nm_mnemonico AS mnemonico_exame,
            el.cd_pro_fat,
            NVL(pl.hr_ped_lab, pl.dt_pedido) AS dh_exame
        FROM dbamv.ped_lab pl
        JOIN dbamv.itped_lab il
          ON il.cd_ped_lab = pl.cd_ped_lab
        JOIN dbamv.exa_lab el
          ON el.cd_exa_lab = il.cd_exa_lab
        LEFT JOIN dbamv.atendime a
          ON a.cd_atendimento = pl.cd_atendimento
        LEFT JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.convenio c
          ON c.cd_convenio = NVL(pl.cd_convenio, a.cd_convenio)
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = NVL(pl.cd_prestador, a.cd_prestador)
        LEFT JOIN dbamv.ori_ate oa
          ON oa.cd_ori_ate = a.cd_ori_ate
        WHERE pl.cd_atendimento IS NOT NULL
          AND NVL(pl.hr_ped_lab, pl.dt_pedido) >= :data_inicio
          AND NVL(pl.hr_ped_lab, pl.dt_pedido) <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(pl.cd_convenio, a.cd_convenio)) || ',') > 0)

        UNION ALL

        SELECT
            'IMAGEM' AS tipo_exame_real,
            prx.cd_atendimento,
            a.cd_paciente,
            p.nm_paciente,
            NVL(prx.cd_convenio, a.cd_convenio) AS cd_convenio,
            c.nm_convenio,
            NVL(prx.cd_prestador, a.cd_prestador) AS cd_prestador,
            pr.nm_prestador,
            a.cd_ori_ate,
            oa.ds_ori_ate,
            prx.cd_ped_rx AS cd_pedido_exame,
            irx.cd_itped_rx AS cd_item_exame,
            irx.cd_exa_rx AS cd_exame,
            erx.ds_exa_rx AS procedimento_exame,
            erx.nm_mnemonico AS mnemonico_exame,
            erx.exa_rx_cd_pro_fat AS cd_pro_fat,
            NVL(prx.hr_pedido, prx.dt_pedido) AS dh_exame
        FROM dbamv.ped_rx prx
        JOIN dbamv.itped_rx irx
          ON irx.cd_ped_rx = prx.cd_ped_rx
        JOIN dbamv.exa_rx erx
          ON erx.cd_exa_rx = irx.cd_exa_rx
        LEFT JOIN dbamv.atendime a
          ON a.cd_atendimento = prx.cd_atendimento
        LEFT JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.convenio c
          ON c.cd_convenio = NVL(prx.cd_convenio, a.cd_convenio)
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = NVL(prx.cd_prestador, a.cd_prestador)
        LEFT JOIN dbamv.ori_ate oa
          ON oa.cd_ori_ate = a.cd_ori_ate
        WHERE prx.cd_atendimento IS NOT NULL
          AND NVL(prx.hr_pedido, prx.dt_pedido) >= :data_inicio
          AND NVL(prx.hr_pedido, prx.dt_pedido) <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(prx.cd_convenio, a.cd_convenio)) || ',') > 0)
    )
    SELECT *
    FROM (
        SELECT *
        FROM exames
        ORDER BY dh_exame DESC, procedimento_exame
    )
    WHERE ROWNUM <= :limite
    """
)


CONSULTA_INTERNACOES_DETALHADAS = text(
    """
    SELECT
        a.cd_atendimento,
        a.cd_paciente,
        p.nm_paciente,
        a.cd_prestador,
        pr.nm_prestador,
        TRUNC(a.dt_atendimento) +
            (NVL(a.hr_atendimento, a.dt_atendimento) -
             TRUNC(NVL(a.hr_atendimento, a.dt_atendimento)))
            AS dh_atendimento,
        CASE
            WHEN a.hr_alta IS NOT NULL THEN
                TRUNC(a.dt_alta) + (a.hr_alta - TRUNC(a.hr_alta))
            ELSE a.dt_alta
        END AS dh_alta,
        a.cd_convenio,
        c.nm_convenio,
        a.cd_ori_ate,
        oa.ds_ori_ate,
        CASE
            WHEN TRUNC(a.dt_atendimento) -
                 TRUNC(a.dt_atendimento, 'IW') BETWEEN 0 AND 4
                THEN 'SEMANA'
            ELSE 'FINAL_SEMANA'
        END AS tipo_dia
    FROM dbamv.atendime a
    LEFT JOIN dbamv.paciente p
      ON p.cd_paciente = a.cd_paciente
    LEFT JOIN dbamv.prestador pr
      ON pr.cd_prestador = a.cd_prestador
    LEFT JOIN dbamv.convenio c
      ON c.cd_convenio = a.cd_convenio
    LEFT JOIN dbamv.ori_ate oa
      ON oa.cd_ori_ate = a.cd_ori_ate
    WHERE a.tp_atendimento = 'I'
      AND pr.cd_tip_presta = 8
      AND (
            TRUNC(a.dt_atendimento) +
            (NVL(a.hr_atendimento, a.dt_atendimento) -
             TRUNC(NVL(a.hr_atendimento, a.dt_atendimento)))
          ) >= :data_inicio
      AND (
            TRUNC(a.dt_atendimento) +
            (NVL(a.hr_atendimento, a.dt_atendimento) -
             TRUNC(NVL(a.hr_atendimento, a.dt_atendimento)))
          ) < :data_fim_exclusiva
      AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0)
    ORDER BY dh_atendimento DESC, pr.nm_prestador, p.nm_paciente
    """
)


CONSULTA_FLUXO_PA_TEMPOS = text(
    """
    WITH eventos AS (
        SELECT
            stp.cd_atendimento,
            stp.cd_triagem_atendimento,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 1  THEN stp.dh_processo END) AS dh_totem,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 20 THEN stp.dh_processo END) AS dh_cha_atd_adm,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 21 THEN stp.dh_processo END) AS dh_cadastro_ini,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 22 THEN stp.dh_processo END) AS dh_cadastro_fim,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 10 THEN stp.dh_processo END) AS dh_cha_class,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 11 THEN stp.dh_processo END) AS dh_classificacao_ini,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 12 THEN stp.dh_processo END) AS dh_classificacao_fim,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 30 THEN stp.dh_processo END) AS dh_cham_atd_med,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 31 THEN stp.dh_processo END) AS dh_consulta_ini,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 32 THEN stp.dh_processo END) AS dh_consulta_fim,
            MIN(CASE WHEN stp.cd_tipo_tempo_processo = 90 THEN stp.dh_processo END) AS dh_alta
        FROM dbamv.sacr_tempo_processo stp
        WHERE stp.dh_processo >= :data_inicio
          AND stp.dh_processo <  :data_fim_exclusiva
          AND stp.cd_atendimento IS NOT NULL
        GROUP BY
            stp.cd_atendimento,
            stp.cd_triagem_atendimento
    ),
    base AS (
        SELECT
            e.cd_atendimento,
            NVL(ta.cd_paciente, a.cd_paciente) AS cd_paciente,
            NVL(ta.nm_paciente, p.nm_paciente) AS nm_paciente,
            ta.ds_senha,
            a.tp_atendimento,
            a.cd_convenio,
            sc.ds_tipo_risco,
            pr.nm_prestador,
            e.dh_totem,
            e.dh_cha_atd_adm,
            e.dh_cadastro_ini,
            e.dh_cadastro_fim,
            e.dh_cha_class,
            e.dh_classificacao_ini,
            e.dh_classificacao_fim,
            e.dh_cham_atd_med,
            e.dh_consulta_ini,
            e.dh_consulta_fim,
            e.dh_alta,
            ROUND((NVL(e.dh_cadastro_ini, e.dh_cha_atd_adm) - e.dh_totem) * 1440, 2)
                AS min_senha_ate_guiche,
            ROUND((NVL(e.dh_consulta_ini, e.dh_cham_atd_med) - NVL(e.dh_cadastro_fim, e.dh_cadastro_ini)) * 1440, 2)
                AS min_guiche_ate_medico,
            ROUND((e.dh_alta - NVL(e.dh_consulta_fim, e.dh_consulta_ini)) * 1440, 2)
                AS min_medico_ate_alta,
            ROUND((NVL(e.dh_alta, e.dh_consulta_fim) - e.dh_totem) * 1440, 2)
                AS min_total_pa
        FROM eventos e
        LEFT JOIN dbamv.triagem_atendimento ta
          ON ta.cd_triagem_atendimento = e.cd_triagem_atendimento
        LEFT JOIN dbamv.sacr_classificacao sc
          ON sc.cd_classificacao = ta.cd_classificacao
        LEFT JOIN dbamv.atendime a
          ON a.cd_atendimento = e.cd_atendimento
        LEFT JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = a.cd_prestador
        WHERE :cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(a.cd_convenio) || ',') > 0
    )
    SELECT *
    FROM (
        SELECT *
        FROM base
        WHERE dh_totem IS NOT NULL
           OR dh_cha_atd_adm IS NOT NULL
           OR dh_cham_atd_med IS NOT NULL
        ORDER BY NVL(dh_totem, NVL(dh_cha_atd_adm, dh_cham_atd_med)) DESC
    )
    WHERE ROWNUM <= :limite
    """
)


CONSULTA_CONVENIOS_INDICADORES = text(
    """
    SELECT
        cd_convenio,
        nm_convenio
    FROM (
        SELECT DISTINCT
            c.cd_convenio,
            c.nm_convenio
        FROM dbamv.atendime a
        JOIN dbamv.convenio c
          ON c.cd_convenio = a.cd_convenio
        WHERE a.dt_atendimento >= :data_inicio
          AND a.dt_atendimento <  :data_fim_exclusiva
        UNION
        SELECT DISTINCT
            c.cd_convenio,
            c.nm_convenio
        FROM dbamv.it_agenda_central i
        JOIN dbamv.convenio c
          ON c.cd_convenio = i.cd_convenio
        WHERE i.hr_agenda >= :data_inicio
          AND i.hr_agenda <  :data_fim_exclusiva
          AND NVL(i.sn_bloqueado, 'N') = 'N'
    )
    ORDER BY nm_convenio
    """
)


CONSULTA_PRODUCAO_CIRURGICA = text(
    """
    WITH base AS (
        SELECT
            TRUNC(ac.dt_aviso_cirurgia) AS dt_aviso_cirurgia,
            ca.cd_convenio,
            co.nm_convenio,
            ca.cd_cirurgia,
            c.ds_cirurgia,
            pr.cd_prestador,
            pr.nm_prestador,
            am.ds_ati_med,
            COUNT(pa.cd_aviso_cirurgia) AS qtd,
            CASE
                WHEN c.ds_cirurgia IN (
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE BIFURCACAO E DE TRONCO COM IMPLANTE DE STENT',
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE MULTIPLOS VASOS, COM IMPLANTE DE STENT',
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA POR BALAO (1 VASO)',
                    'CATETERISMO CARDIACO D E/OU E C/ ESTUDO CINEANGIOGRAFICO E DE REVASC. CIRURGICA DO MIOCARDIO',
                    'CATETERISMO CARDIACO E E/OU D COM CINEANGIOCORONARIOGRAFIA E VENTRICULOGRAFIA',
                    'IMPLANTE DE STENT CORONARIO DE VASO UNICO',
                    'RECANALIZACAO ARTERIAL NO IAM - ANGIOPLAST PRIMA - C/ OU S/ SUPORTE CIRCULATORIO (BALAO INTRAORTICO)',
                    'RECANALIZACAO MECANICA DO IAM (ANGIOPLASTIA PRIMARIA COM BALAO)',
                    'CATETERISMO CARDIACO D E/OU E COM  OU  SEM  CINECORONARIOGRAFIA / CINEANGIOGRAFIA C/ AVAL. DE REAT.',
                    'ANGIOPLASTIA CORONARIANA COM IMPLANTE DE STENT',
                    'ANGIOPLASTIA CORONARIANA C/ IMPLANTE DE DOIS STENTS'
                ) THEN 'HEMODINAMICA'
                WHEN c.ds_cirurgia IN (
                    'COLOCACAO DE PROTESE DE MAMA',
                    'IMPLANTE DE CATETER VENOSO CENTRAL P/ PUNCAO, P/ NPP, QT, HEMODEPURACAO OU P/ INFUSAO DE SORO/DROGA',
                    'LIPOASPIRACAO',
                    'MASTOPEXIA',
                    'PUNCAO LIQUORICA',
                    'REVASCULARIZACAO DO MIOCARDIO'
                ) THEN 'CENTRO_CIRURGICO'
                WHEN c.ds_cirurgia = 'INSTALACAO DE MARCA-PASSO EPIMIOCARDIO TEMPORARIO'
                    THEN 'ELETROFISIOLOGIA'
                WHEN c.ds_cirurgia IN (
                    'ANGIOGRAFIA POR CATETERISMO NAO SELETIVO DE GRANDE VASO',
                    'ANGIOGRAFIA POR CATETERISMO SELETIVO DE RAMO PRIMARIO - POR VASO',
                    'ANGIOGRAFIA POR CATETERISMO SUPERSELETIVO DE RAMO SECUNDARIO OU DISTAL - POR VASO',
                    'BLOQUEIO DE NERVO PERIFERICO - BLOQUEIOS ANESTESICOS DE NERVOS E ESTIMULOS NEUROVASCULARES',
                    'BLOQUEIO DO SISTEMA NERVOSO AUTONOMO',
                    'FISTULA ARTERIOVENOSA DOS MEMBROS'
                ) THEN 'VASCULAR'
                ELSE 'OUTROS'
            END AS area,
            CASE
                WHEN c.ds_cirurgia IN (
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE BIFURCACAO E DE TRONCO COM IMPLANTE DE STENT',
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE MULTIPLOS VASOS, COM IMPLANTE DE STENT',
                    'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA POR BALAO (1 VASO)',
                    'IMPLANTE DE CATETER VENOSO CENTRAL P/ PUNCAO, P/ NPP, QT, HEMODEPURACAO OU P/ INFUSAO DE SORO/DROGA',
                    'IMPLANTE DE STENT CORONARIO DE VASO UNICO',
                    'ANGIOPLASTIA CORONARIANA COM IMPLANTE DE STENT',
                    'RECANALIZACAO ARTERIAL NO IAM - ANGIOPLAST PRIMA - C/ OU S/ SUPORTE CIRCULATORIO (BALAO INTRAORTICO)',
                    'RECANALIZACAO MECANICA DO IAM (ANGIOPLASTIA PRIMARIA COM BALAO)',
                    'ANGIOPLASTIA CORONARIANA C/ IMPLANTE DE DOIS STENTS'
                ) THEN 'ANGIOPLASTIA'
                WHEN c.ds_cirurgia IN (
                    'CATETERISMO CARDIACO D E/OU E C/ ESTUDO CINEANGIOGRAFICO E DE REVASC. CIRURGICA DO MIOCARDIO',
                    'CATETERISMO CARDIACO E E/OU D COM CINEANGIOCORONARIOGRAFIA E VENTRICULOGRAFIA',
                    'CATETERISMO CARDIACO D E/OU E COM  OU  SEM  CINECORONARIOGRAFIA / CINEANGIOGRAFIA C/ AVAL. DE REAT.'
                ) THEN 'CATETERISMO'
                ELSE NVL(am.ds_ati_med, 'OUTROS')
            END AS tipo
        FROM dbamv.prestador_aviso pa
        LEFT JOIN dbamv.aviso_cirurgia ac
          ON ac.cd_aviso_cirurgia = pa.cd_aviso_cirurgia
        LEFT JOIN dbamv.cirurgia_aviso ca
          ON ca.cd_cirurgia_aviso = pa.cd_cirurgia_aviso
        LEFT JOIN dbamv.ati_med am
          ON am.cd_ati_med = pa.cd_ati_med
        LEFT JOIN dbamv.cirurgia c
          ON c.cd_cirurgia = ca.cd_cirurgia
        LEFT JOIN dbamv.convenio co
          ON co.cd_convenio = ca.cd_convenio
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = pa.cd_prestador
        WHERE pa.sn_principal = 'S'
          AND ac.tp_situacao = 'R'
          AND ac.cd_cen_cir IN (1, 2)
          AND ac.dt_aviso_cirurgia >= :data_inicio
          AND ac.dt_aviso_cirurgia <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(ca.cd_convenio) || ',') > 0)
          AND (:procedimento IS NULL OR c.ds_cirurgia = :procedimento)
        GROUP BY
            TRUNC(ac.dt_aviso_cirurgia),
            ca.cd_convenio,
            co.nm_convenio,
            ca.cd_cirurgia,
            c.ds_cirurgia,
            pr.cd_prestador,
            pr.nm_prestador,
            am.ds_ati_med
    )
    SELECT
        area,
        tipo,
        cd_convenio,
        nm_convenio,
        ds_cirurgia,
        cd_prestador,
        nm_prestador,
        SUM(qtd) AS prod_periodo,
        SUM(CASE
            WHEN dt_aviso_cirurgia >= GREATEST(:data_inicio, :data_fim_exclusiva - 7)
                THEN qtd
            ELSE 0
        END) AS prod_7d
    FROM base
    GROUP BY
        area,
        tipo,
        cd_convenio,
        nm_convenio,
        ds_cirurgia,
        cd_prestador,
        nm_prestador
    ORDER BY area, prod_periodo DESC, nm_convenio, ds_cirurgia, nm_prestador
    """
)


CONSULTA_PROCEDIMENTOS_CIRURGICOS = text(
    """
    SELECT DISTINCT
        CASE
            WHEN c.ds_cirurgia IN (
                'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE BIFURCACAO E DE TRONCO COM IMPLANTE DE STENT',
                'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA DE MULTIPLOS VASOS, COM IMPLANTE DE STENT',
                'ANGIOPLASTIA TRANSLUMINAL PERCUTANEA POR BALAO (1 VASO)',
                'CATETERISMO CARDIACO D E/OU E C/ ESTUDO CINEANGIOGRAFICO E DE REVASC. CIRURGICA DO MIOCARDIO',
                'CATETERISMO CARDIACO E E/OU D COM CINEANGIOCORONARIOGRAFIA E VENTRICULOGRAFIA',
                'IMPLANTE DE STENT CORONARIO DE VASO UNICO',
                'RECANALIZACAO ARTERIAL NO IAM - ANGIOPLAST PRIMA - C/ OU S/ SUPORTE CIRCULATORIO (BALAO INTRAORTICO)',
                'RECANALIZACAO MECANICA DO IAM (ANGIOPLASTIA PRIMARIA COM BALAO)',
                'CATETERISMO CARDIACO D E/OU E COM  OU  SEM  CINECORONARIOGRAFIA / CINEANGIOGRAFIA C/ AVAL. DE REAT.',
                'ANGIOPLASTIA CORONARIANA COM IMPLANTE DE STENT',
                'ANGIOPLASTIA CORONARIANA C/ IMPLANTE DE DOIS STENTS'
            ) THEN 'HEMODINAMICA'
            WHEN c.ds_cirurgia IN (
                'COLOCACAO DE PROTESE DE MAMA',
                'IMPLANTE DE CATETER VENOSO CENTRAL P/ PUNCAO, P/ NPP, QT, HEMODEPURACAO OU P/ INFUSAO DE SORO/DROGA',
                'LIPOASPIRACAO',
                'MASTOPEXIA',
                'PUNCAO LIQUORICA',
                'REVASCULARIZACAO DO MIOCARDIO'
            ) THEN 'CENTRO_CIRURGICO'
            WHEN c.ds_cirurgia = 'INSTALACAO DE MARCA-PASSO EPIMIOCARDIO TEMPORARIO'
                THEN 'ELETROFISIOLOGIA'
            WHEN c.ds_cirurgia IN (
                'ANGIOGRAFIA POR CATETERISMO NAO SELETIVO DE GRANDE VASO',
                'ANGIOGRAFIA POR CATETERISMO SELETIVO DE RAMO PRIMARIO - POR VASO',
                'ANGIOGRAFIA POR CATETERISMO SUPERSELETIVO DE RAMO SECUNDARIO OU DISTAL - POR VASO',
                'BLOQUEIO DE NERVO PERIFERICO - BLOQUEIOS ANESTESICOS DE NERVOS E ESTIMULOS NEUROVASCULARES',
                'BLOQUEIO DO SISTEMA NERVOSO AUTONOMO',
                'FISTULA ARTERIOVENOSA DOS MEMBROS'
            ) THEN 'VASCULAR'
            ELSE 'OUTROS'
        END AS area,
        c.ds_cirurgia AS procedimento
    FROM dbamv.prestador_aviso pa
    LEFT JOIN dbamv.aviso_cirurgia ac
      ON ac.cd_aviso_cirurgia = pa.cd_aviso_cirurgia
    LEFT JOIN dbamv.cirurgia_aviso ca
      ON ca.cd_cirurgia_aviso = pa.cd_cirurgia_aviso
    LEFT JOIN dbamv.cirurgia c
      ON c.cd_cirurgia = ca.cd_cirurgia
    WHERE pa.sn_principal = 'S'
      AND ac.tp_situacao = 'R'
      AND ac.cd_cen_cir IN (1, 2)
      AND ac.dt_aviso_cirurgia >= :data_inicio
      AND ac.dt_aviso_cirurgia <  :data_fim_exclusiva
      AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(ca.cd_convenio) || ',') > 0)
      AND c.ds_cirurgia IS NOT NULL
    ORDER BY area, procedimento
    """
)


CONSULTA_FATURAMENTO_BASE = """
    WITH producao_fat AS (
        SELECT
            'HOSPITALAR' AS origem_conta,
            rf.cd_reg_fat AS cd_conta,
            irf.cd_lancamento,
            rf.cd_atendimento,
            a.cd_paciente,
            p.nm_paciente,
            a.cd_prestador,
            pr.nm_prestador,
            NVL(rf.cd_convenio, a.cd_convenio) AS cd_convenio,
            conv.nm_convenio,
            a.cd_ori_ate,
            oa.ds_ori_ate AS origem_atendimento,
            a.tp_atendimento,
            CASE a.tp_atendimento
                WHEN 'I' THEN 'INTERNACAO'
                WHEN 'A' THEN 'AMBULATORIO'
                WHEN 'U' THEN 'EMERGENCIA'
                WHEN 'E' THEN 'EXAMES'
                ELSE a.tp_atendimento
            END AS tipo_atendimento,
            irf.cd_setor,
            s.nm_setor,
            irf.dt_lancamento,
            irf.hr_lancamento,
            irf.cd_gru_fat,
            irf.cd_pro_fat,
            pf.ds_pro_fat,
            CAST(irf.cd_procedimento AS VARCHAR2(30)) AS cd_procedimento,
            irf.qt_lancamento,
            irf.vl_unitario,
            irf.vl_acrescimo,
            irf.vl_desconto,
            NVL(irf.sn_pertence_pacote, 'N') AS sn_pertence_pacote,
            NVL(irf.vl_total_conta, 0) AS vl_total_item_bruto,
            CASE
                WHEN NVL(irf.sn_pertence_pacote, 'N') = 'S'
                    THEN 0
                ELSE NVL(irf.vl_total_conta, 0)
            END AS vl_total_item,
            rf.sn_fechada,
            rf.dt_fechamento,
            CASE
                WHEN NVL(rf.sn_fechada, 'N') = 'S'
                  OR rf.dt_fechamento IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS faturado,
            rf.cd_remessa,
            rf.dt_remessa,
            CASE
                WHEN rf.cd_remessa IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS em_remessa,
            CASE
                WHEN (NVL(rf.sn_fechada, 'N') = 'S' OR rf.dt_fechamento IS NOT NULL)
                 AND rf.cd_remessa IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS faturado_em_remessa
        FROM dbamv.reg_fat rf
        JOIN dbamv.itreg_fat irf
          ON irf.cd_reg_fat = rf.cd_reg_fat
        JOIN dbamv.atendime a
          ON a.cd_atendimento = rf.cd_atendimento
        JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = a.cd_prestador
        LEFT JOIN dbamv.convenio conv
          ON conv.cd_convenio = NVL(rf.cd_convenio, a.cd_convenio)
        LEFT JOIN dbamv.ori_ate oa
          ON oa.cd_ori_ate = a.cd_ori_ate
        LEFT JOIN dbamv.setor s
          ON s.cd_setor = irf.cd_setor
        LEFT JOIN dbamv.pro_fat pf
          ON pf.cd_pro_fat = irf.cd_pro_fat
        WHERE irf.dt_lancamento >= :data_inicio
          AND irf.dt_lancamento <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(rf.cd_convenio, a.cd_convenio)) || ',') > 0)

        UNION ALL

        SELECT
            'AMBULATORIAL' AS origem_conta,
            ra.cd_reg_amb AS cd_conta,
            ira.cd_lancamento,
            ira.cd_atendimento,
            a.cd_paciente,
            p.nm_paciente,
            a.cd_prestador,
            pr.nm_prestador,
            NVL(ira.cd_convenio, NVL(ra.cd_convenio, a.cd_convenio)) AS cd_convenio,
            conv.nm_convenio,
            a.cd_ori_ate,
            oa.ds_ori_ate AS origem_atendimento,
            a.tp_atendimento,
            CASE a.tp_atendimento
                WHEN 'I' THEN 'INTERNACAO'
                WHEN 'A' THEN 'AMBULATORIO'
                WHEN 'U' THEN 'EMERGENCIA'
                WHEN 'E' THEN 'EXAMES'
                ELSE a.tp_atendimento
            END AS tipo_atendimento,
            ira.cd_setor,
            s.nm_setor,
            ra.dt_lancamento,
            ira.hr_lancamento,
            ira.cd_gru_fat,
            ira.cd_pro_fat,
            pf.ds_pro_fat,
            CAST(NULL AS VARCHAR2(30)) AS cd_procedimento,
            ira.qt_lancamento,
            ira.vl_unitario,
            ira.vl_acrescimo,
            ira.vl_desconto,
            NVL(ira.sn_pertence_pacote, 'N') AS sn_pertence_pacote,
            NVL(ira.vl_total_conta, 0) AS vl_total_item_bruto,
            CASE
                WHEN NVL(ira.sn_pertence_pacote, 'N') = 'S'
                    THEN 0
                ELSE NVL(ira.vl_total_conta, 0)
            END AS vl_total_item,
            NVL(ira.sn_fechada, ra.sn_fechada) AS sn_fechada,
            ira.dt_fechamento,
            CASE
                WHEN NVL(NVL(ira.sn_fechada, ra.sn_fechada), 'N') = 'S'
                  OR ira.dt_fechamento IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS faturado,
            ra.cd_remessa,
            ra.dt_remessa,
            CASE
                WHEN ra.cd_remessa IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS em_remessa,
            CASE
                WHEN (NVL(NVL(ira.sn_fechada, ra.sn_fechada), 'N') = 'S' OR ira.dt_fechamento IS NOT NULL)
                 AND ra.cd_remessa IS NOT NULL
                    THEN 'SIM'
                ELSE 'NAO'
            END AS faturado_em_remessa
        FROM dbamv.reg_amb ra
        JOIN dbamv.itreg_amb ira
          ON ira.cd_reg_amb = ra.cd_reg_amb
        JOIN dbamv.atendime a
          ON a.cd_atendimento = ira.cd_atendimento
        JOIN dbamv.paciente p
          ON p.cd_paciente = a.cd_paciente
        LEFT JOIN dbamv.prestador pr
          ON pr.cd_prestador = a.cd_prestador
        LEFT JOIN dbamv.convenio conv
          ON conv.cd_convenio = NVL(ira.cd_convenio, NVL(ra.cd_convenio, a.cd_convenio))
        LEFT JOIN dbamv.ori_ate oa
          ON oa.cd_ori_ate = a.cd_ori_ate
        LEFT JOIN dbamv.setor s
          ON s.cd_setor = ira.cd_setor
        LEFT JOIN dbamv.pro_fat pf
          ON pf.cd_pro_fat = ira.cd_pro_fat
        WHERE ra.dt_lancamento >= :data_inicio
          AND ra.dt_lancamento <  :data_fim_exclusiva
          AND (:cd_convenio IS NULL OR INSTR(',' || :cd_convenio || ',', ',' || TO_CHAR(NVL(ira.cd_convenio, NVL(ra.cd_convenio, a.cd_convenio))) || ',') > 0)
    )
"""

CONSULTA_FATURAMENTO_CONVENIO = text(
    CONSULTA_FATURAMENTO_BASE + """
    SELECT *
    FROM (
        SELECT
            origem_conta,
            cd_conta,
            cd_lancamento,
            cd_atendimento,
            cd_paciente,
            nm_paciente,
            cd_prestador,
            nm_prestador,
            cd_convenio,
            nm_convenio,
            cd_ori_ate,
            origem_atendimento,
            tp_atendimento,
            tipo_atendimento,
            cd_setor,
            nm_setor,
            dt_lancamento,
            hr_lancamento,
            cd_gru_fat,
            cd_pro_fat,
            ds_pro_fat,
            cd_procedimento,
            qt_lancamento,
            vl_unitario,
            vl_acrescimo,
            vl_desconto,
            sn_pertence_pacote,
            vl_total_item_bruto,
            vl_total_item,
            sn_fechada,
            dt_fechamento,
            faturado,
            cd_remessa,
            dt_remessa,
            em_remessa,
            faturado_em_remessa
        FROM producao_fat
        ORDER BY dt_lancamento DESC, origem_conta, cd_conta, cd_lancamento
    )
    WHERE ROWNUM <= :limite
    """
)

CONSULTA_FATURAMENTO_AGREGADO = text(
    CONSULTA_FATURAMENTO_BASE + """
    SELECT
        nivel,
        chave,
        descricao,
        tp_atendimento,
        tipo_atendimento,
        cd_convenio,
        nm_convenio,
        cd_prestador,
        nm_prestador,
        producao_total,
        producao_aberta,
        contas_fechadas,
        em_remessa,
        faturado_em_remessa,
        valor_bruto_original,
        qtd_itens,
        qtd_contas,
        qtd_atendimentos,
        qtd_itens_pacote,
        ticket_medio_atendimento,
        ticket_medio_conta,
        perc_faturado_remessa
    FROM (
        SELECT
            'GERAL' AS nivel,
            'GERAL' AS chave,
            'Geral' AS descricao,
            CAST(NULL AS VARCHAR2(1)) AS tp_atendimento,
            CAST(NULL AS VARCHAR2(30)) AS tipo_atendimento,
            CAST(NULL AS NUMBER) AS cd_convenio,
            CAST(NULL AS VARCHAR2(200)) AS nm_convenio,
            CAST(NULL AS NUMBER) AS cd_prestador,
            CAST(NULL AS VARCHAR2(200)) AS nm_prestador,
            SUM(vl_total_item) AS producao_total,
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END) AS producao_aberta,
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END) AS contas_fechadas,
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) AS em_remessa,
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) AS faturado_em_remessa,
            SUM(vl_total_item_bruto) AS valor_bruto_original,
            COUNT(*) AS qtd_itens,
            COUNT(DISTINCT cd_conta) AS qtd_contas,
            COUNT(DISTINCT cd_atendimento) AS qtd_atendimentos,
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END) AS qtd_itens_pacote,
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2) AS ticket_medio_atendimento,
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2) AS ticket_medio_conta,
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4) AS perc_faturado_remessa
        FROM producao_fat

        UNION ALL

        SELECT
            'TIPO_ATENDIMENTO',
            NVL(tp_atendimento, 'N/I'),
            NVL(tipo_atendimento, 'Nao informado'),
            tp_atendimento,
            tipo_atendimento,
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            SUM(vl_total_item),
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(vl_total_item_bruto),
            COUNT(*),
            COUNT(DISTINCT cd_conta),
            COUNT(DISTINCT cd_atendimento),
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2),
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4)
        FROM producao_fat
        GROUP BY tp_atendimento, tipo_atendimento

        UNION ALL

        SELECT
            'CONVENIO',
            TO_CHAR(cd_convenio),
            NVL(nm_convenio, 'Sem convenio'),
            CAST(NULL AS VARCHAR2(1)),
            CAST(NULL AS VARCHAR2(30)),
            cd_convenio,
            nm_convenio,
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            SUM(vl_total_item),
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(vl_total_item_bruto),
            COUNT(*),
            COUNT(DISTINCT cd_conta),
            COUNT(DISTINCT cd_atendimento),
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2),
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4)
        FROM producao_fat
        GROUP BY cd_convenio, nm_convenio

        UNION ALL

        SELECT
            'PRESTADOR',
            TO_CHAR(cd_prestador),
            NVL(nm_prestador, 'Sem prestador'),
            CAST(NULL AS VARCHAR2(1)),
            CAST(NULL AS VARCHAR2(30)),
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            cd_prestador,
            nm_prestador,
            SUM(vl_total_item),
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(vl_total_item_bruto),
            COUNT(*),
            COUNT(DISTINCT cd_conta),
            COUNT(DISTINCT cd_atendimento),
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2),
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4)
        FROM producao_fat
        GROUP BY cd_prestador, nm_prestador

        UNION ALL

        SELECT
            'TIPO_CONVENIO',
            NVL(tp_atendimento, 'N/I') || '|' || TO_CHAR(cd_convenio),
            NVL(tipo_atendimento, 'Nao informado') || ' - ' || NVL(nm_convenio, 'Sem convenio'),
            tp_atendimento,
            tipo_atendimento,
            cd_convenio,
            nm_convenio,
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            SUM(vl_total_item),
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(vl_total_item_bruto),
            COUNT(*),
            COUNT(DISTINCT cd_conta),
            COUNT(DISTINCT cd_atendimento),
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2),
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4)
        FROM producao_fat
        GROUP BY tp_atendimento, tipo_atendimento, cd_convenio, nm_convenio

        UNION ALL

        SELECT
            'TIPO_PRESTADOR',
            NVL(tp_atendimento, 'N/I') || '|' || TO_CHAR(cd_prestador),
            NVL(tipo_atendimento, 'Nao informado') || ' - ' || NVL(nm_prestador, 'Sem prestador'),
            tp_atendimento,
            tipo_atendimento,
            CAST(NULL AS NUMBER),
            CAST(NULL AS VARCHAR2(200)),
            cd_prestador,
            nm_prestador,
            SUM(vl_total_item),
            SUM(CASE WHEN faturado = 'NAO' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END),
            SUM(vl_total_item_bruto),
            COUNT(*),
            COUNT(DISTINCT cd_conta),
            COUNT(DISTINCT cd_atendimento),
            SUM(CASE WHEN sn_pertence_pacote = 'S' THEN 1 ELSE 0 END),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_atendimento), 0), 2),
            ROUND(SUM(vl_total_item) / NULLIF(COUNT(DISTINCT cd_conta), 0), 2),
            ROUND(SUM(CASE WHEN faturado_em_remessa = 'SIM' THEN vl_total_item ELSE 0 END) / NULLIF(SUM(vl_total_item), 0), 4)
        FROM producao_fat
        GROUP BY tp_atendimento, tipo_atendimento, cd_prestador, nm_prestador
    )
    ORDER BY nivel, producao_total DESC
    """
)



@router.get("/teste-ergometrico", status_code=HTTPStatus.OK)
def consultar_teste_ergometrico(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    if data_fim <= data_inicio:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="data_fim deve ser posterior a data_inicio.",
        )

    try:
        rows = (
            session.execute(
                CONSULTA_TESTE_ERGOMETRICO,
                {"data_inicio": data_inicio, "data_fim": data_fim},
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar os exames de teste ergométrico no MV.",
        ) from exc

    agendados = [dict(row) for row in rows]
    exames = [row for row in agendados if row.get("cd_atendimento")]
    pendentes = [row for row in agendados if not row.get("cd_atendimento")]

    prestadores = {}
    for row in exames:
        prestador_key = row.get("cd_prestador") or row.get("nm_prestador") or "sem_prestador"
        current = prestadores.setdefault(
            str(prestador_key),
            {
                "cd_prestador": row.get("cd_prestador"),
                "nm_prestador": row.get("nm_prestador") or "Prestador não informado",
                "crm": row.get("crm") or "-",
                "exames": 0,
            },
        )
        current["exames"] += 1

    return {
        "resumo": {
            "agendados_confirmados": len(agendados),
            "atendimentos_realizados": len(exames),
            "conciliados": len(exames),
            "pendentes": len(pendentes),
            "prestadores": len(prestadores),
        },
        "prestadores": list(prestadores.values()),
        "agendados": agendados,
        "exames": exames,
        "pendentes": pendentes,
        "total": len(exames),
    }


def _executar_consulta_indicador(
    sql_query,
    data_inicio: date,
    data_fim: date,
    session: Session,
    cd_convenio: str | None = None,
    procedimento: str | None = None,
):
    params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio, procedimento)
    try:
        return session.execute(sql_query, params).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar os indicadores hospitalares no MV.",
        ) from exc


@router.get("/indicadores-hospitalares/resumo", status_code=HTTPStatus.OK)
def consultar_indicadores_hospitalares_resumo(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_INDICADORES_HOSPITALARES_RESUMO,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    resumo = _rows_to_dict(rows)[0] if rows else {}
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "resumo": resumo,
    }


@router.get("/indicadores-hospitalares/series", status_code=HTTPStatus.OK)
def consultar_indicadores_hospitalares_series(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_INDICADORES_HOSPITALARES_SERIES,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    series = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "series": series,
        "total": len(series),
    }


@router.get(
    "/indicadores-hospitalares/agenda-ambulatorial",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_agenda_ambulatorial(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_AGENDA_AMBULATORIAL,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    agenda = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "agenda": agenda,
        "total": len(agenda),
    }


@router.get(
    "/indicadores-hospitalares/fluxo-ambulatorio-exames",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_fluxo_ambulatorio_exames(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    limite: int = Query(default=1000, ge=1, le=5000),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    params["limite"] = limite
    try:
        rows = session.execute(
            CONSULTA_FLUXO_AMBULATORIO_EXAMES,
            params,
        ).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar os tempos do fluxo ambulatorial e exames no MV.",
        ) from exc
    fluxo = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "fluxo_ambulatorio_exames": fluxo,
        "total": len(fluxo),
        "limite": limite,
    }


@router.get(
    "/indicadores-hospitalares/exames-procedimentos",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_exames_procedimentos(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    limite: int = Query(default=5000, ge=1, le=10000),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    params["limite"] = limite
    try:
        rows = session.execute(CONSULTA_EXAMES_PROCEDIMENTOS, params).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Nao foi possivel consultar os procedimentos de exames no MV.",
        ) from exc
    exames = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "exames_procedimentos": exames,
        "total": len(exames),
        "limite": limite,
    }




@router.get(
    "/indicadores-hospitalares/fluxo-pa-tempos",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_fluxo_pa_tempos(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    limite: int = Query(default=1000, ge=1, le=5000),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    params["limite"] = limite
    try:
        rows = session.execute(CONSULTA_FLUXO_PA_TEMPOS, params).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar os tempos do fluxo PA no MV.",
        ) from exc
    fluxo = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "fluxo_pa_tempos": fluxo,
        "total": len(fluxo),
        "limite": limite,
    }

@router.get(
    "/indicadores-hospitalares/internacoes-detalhadas",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_internacoes_detalhadas(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    limite: int = Query(default=500, ge=1, le=5000),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_INTERNACOES_DETALHADAS,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    internacoes = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "internacoes": internacoes[:limite],
        "total": len(internacoes),
        "limite": limite,
    }


@router.get(
    "/indicadores-hospitalares/producao-cirurgica",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_producao_cirurgica(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    procedimento: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_PRODUCAO_CIRURGICA,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
        procedimento,
    )
    producao = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "producao_cirurgica": producao,
        "total": len(producao),
    }


@router.get(
    "/indicadores-hospitalares/faturamento",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_faturamento(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    limite: int = Query(default=2000, ge=1, le=10000),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    params["limite"] = limite
    try:
        rows = session.execute(CONSULTA_FATURAMENTO_CONVENIO, params).mappings().all()
        agregado_rows = session.execute(CONSULTA_FATURAMENTO_AGREGADO, params).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar o faturamento no MV.",
        ) from exc

    faturamento = _rows_to_dict(rows)
    agregados = _rows_to_dict(agregado_rows)

    def por_nivel(nivel: str):
        return [row for row in agregados if row.get("nivel") == nivel]

    resumo_lista = por_nivel("GERAL")
    resumo_faturamento = resumo_lista[0] if resumo_lista else {}

    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "resumo_faturamento": resumo_faturamento,
        "faturamento_por_tipo_atendimento": por_nivel("TIPO_ATENDIMENTO"),
        "faturamento_por_convenio": por_nivel("CONVENIO"),
        "faturamento_por_prestador": por_nivel("PRESTADOR"),
        "faturamento_por_tipo_convenio": por_nivel("TIPO_CONVENIO"),
        "faturamento_por_tipo_prestador": por_nivel("TIPO_PRESTADOR"),
        "faturamento": faturamento,
        "total": len(faturamento),
        "limite": limite,
    }


@router.get(
    "/indicadores-hospitalares/convenios",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_convenios(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_CONVENIOS_INDICADORES,
        data_inicio,
        data_fim,
        session,
    )
    convenios = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "convenios": convenios,
        "total": len(convenios),
    }


@router.get(
    "/indicadores-hospitalares/procedimentos-cirurgicos",
    status_code=HTTPStatus.OK,
)
def consultar_indicadores_hospitalares_procedimentos_cirurgicos(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    rows = _executar_consulta_indicador(
        CONSULTA_PROCEDIMENTOS_CIRURGICOS,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    procedimentos = _rows_to_dict(rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "procedimentos_cirurgicos": procedimentos,
        "total": len(procedimentos),
    }


@router.get("/indicadores-hospitalares", status_code=HTTPStatus.OK)
def consultar_indicadores_hospitalares(
    usuario_atual: ValidaUsuarioAtual,
    data_inicio: date,
    data_fim: date,
    cd_convenio: str | None = Query(default=None),
    procedimento: str | None = Query(default=None),
    session: Session = Depends(get_session_oracle),
):
    del usuario_atual
    resumo_rows = _executar_consulta_indicador(
        CONSULTA_INDICADORES_HOSPITALARES_RESUMO,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    series_rows = _executar_consulta_indicador(
        CONSULTA_INDICADORES_HOSPITALARES_SERIES,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    agenda_rows = _executar_consulta_indicador(
        CONSULTA_AGENDA_AMBULATORIAL,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    internacao_rows = _executar_consulta_indicador(
        CONSULTA_INTERNACOES_DETALHADAS,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    producao_rows = _executar_consulta_indicador(
        CONSULTA_PRODUCAO_CIRURGICA,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
        procedimento,
    )
    faturamento_params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    faturamento_params["limite"] = 2000
    try:
        faturamento_rows = session.execute(
            CONSULTA_FATURAMENTO_CONVENIO,
            faturamento_params,
        ).mappings().all()
    except SQLAlchemyError:
        faturamento_rows = []
    convenio_rows = _executar_consulta_indicador(
        CONSULTA_CONVENIOS_INDICADORES,
        data_inicio,
        data_fim,
        session,
    )
    procedimento_rows = _executar_consulta_indicador(
        CONSULTA_PROCEDIMENTOS_CIRURGICOS,
        data_inicio,
        data_fim,
        session,
        cd_convenio,
    )
    fluxo_params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    fluxo_params["limite"] = 1000
    try:
        fluxo_rows = session.execute(
            CONSULTA_FLUXO_PA_TEMPOS,
            fluxo_params,
        ).mappings().all()
    except SQLAlchemyError:
        fluxo_rows = []
    fluxo_ambulatorio_params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    fluxo_ambulatorio_params["limite"] = 1000
    try:
        fluxo_ambulatorio_rows = session.execute(
            CONSULTA_FLUXO_AMBULATORIO_EXAMES,
            fluxo_ambulatorio_params,
        ).mappings().all()
    except SQLAlchemyError:
        fluxo_ambulatorio_rows = []
    exames_procedimentos_params = _periodo_inclusivo(data_inicio, data_fim, cd_convenio)
    exames_procedimentos_params["limite"] = 5000
    try:
        exames_procedimentos_rows = session.execute(
            CONSULTA_EXAMES_PROCEDIMENTOS,
            exames_procedimentos_params,
        ).mappings().all()
    except SQLAlchemyError:
        exames_procedimentos_rows = []

    agenda = _rows_to_dict(agenda_rows)
    internacoes = _rows_to_dict(internacao_rows)
    producao_cirurgica = _rows_to_dict(producao_rows)
    fluxo_pa = _rows_to_dict(fluxo_rows)
    fluxo_ambulatorio = _rows_to_dict(fluxo_ambulatorio_rows)
    exames_procedimentos = _rows_to_dict(exames_procedimentos_rows)
    faturamento = _rows_to_dict(faturamento_rows)
    return {
        "periodo": {
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
        },
        "resumo": _rows_to_dict(resumo_rows)[0] if resumo_rows else {},
        "series": _rows_to_dict(series_rows),
        "agenda_ambulatorial": agenda,
        "internacoes_detalhadas": internacoes[:500],
        "fluxo_pa_tempos": fluxo_pa,
        "fluxo_ambulatorio_exames": fluxo_ambulatorio,
        "exames_procedimentos": exames_procedimentos,
        "producao_cirurgica": producao_cirurgica,
        "faturamento": faturamento,
        "convenios": _rows_to_dict(convenio_rows),
        "procedimentos_cirurgicos": _rows_to_dict(procedimento_rows),
        "totais": {
            "agenda_ambulatorial": len(agenda),
            "internacoes_detalhadas": len(internacoes),
            "fluxo_pa_tempos": len(fluxo_pa),
            "fluxo_ambulatorio_exames": len(fluxo_ambulatorio),
            "exames_procedimentos": len(exames_procedimentos),
            "producao_cirurgica": len(producao_cirurgica),
            "faturamento": len(faturamento),
        },
    }
