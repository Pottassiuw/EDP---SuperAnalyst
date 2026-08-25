"""Regras compartilhadas para geração dos pacotes de notas EDP ES e SP."""

import os
import time

import numpy as np
import pandas as pd

CAUSAS_GERENCIAVEIS = (
    11, 20, 130, 180, 181, 182, 200, 322, 323, 324, 327, 329, 491, 492,
    820, 880, 890,
)
JANELA_OCORRENCIAS_RECENTES_DIAS = 90
FATOR_CLUSTER = {
    "Muito Alta": 2.0,
    "Alta": 1.7,
    "Média": 1.3,
    "Baixa": 1.0,
    "Muito Baixa": 1.0,
}
FATOR_OCORRENCIA_RECENTE = 1.5
PESO_PRIORIDADE_CONJUNTO = {1: 2.0, 2: 1.3, 3: 1.0}
BONUS_ALIMENTADOR_345KV = 50_000
PROTECOES_USAM_CLIENTES_BLOCO = ("RL", "DJ", "CF")
META_CSD_PCT = 0.8


def converter_numerico_br(df, colunas):
    """Converte números no formato brasileiro para float; valores inválidos viram zero."""
    for coluna in colunas:
        df[coluna] = pd.to_numeric(
            df[coluna].astype(str).str.replace(",", ".", regex=False).str.strip(),
            errors="coerce",
        ).fillna(0)
    return df


def esperar_download(caminho_final, timeout=300, intervalo=5):
    """Aguarda o arquivo final existir sem o arquivo parcial .crdownload correspondente."""
    parcial = f"{caminho_final}.crdownload"
    inicio = time.monotonic()
    while time.monotonic() - inicio <= timeout:
        if os.path.exists(caminho_final) and not os.path.exists(parcial):
            print(f"Download OK: {os.path.basename(caminho_final)}")
            return True
        time.sleep(intervalo)
    print(f"TIMEOUT: {os.path.basename(caminho_final)} não concluiu em {timeout}s")
    return False


def classificar_tipo_nota(notas):
    """Cria Tipo Nota para toda nota processada, independentemente de ser selecionada."""
    notas["Tipo Nota"] = np.where(
        notas["descrição_conjunto"].str.contains("PODA", case=False, na=False),
        "Poda",
        "Manutenção",
    )
    return notas


def atribuir_tipo_equipamento(notas_indicadores):
    """Extrai o tipo nas posições 3:5 de dispositivo_protecao, com fallback local."""
    disp_prot = notas_indicadores["dispositivo_protecao"].astype(str).str.strip()
    disp_valido = (
        notas_indicadores["dispositivo_protecao"].notna()
        & (disp_prot.str.len() >= 5)
        & (~disp_prot.str.lower().isin(["nan", "none", "", "null"]))
    )
    notas_indicadores["tipo_equipamento"] = np.where(
        disp_valido,
        disp_prot.str[3:5],
        notas_indicadores["local_instalacao"].astype(str).str[3:5],
    )
    return notas_indicadores


def calcular_prioridade(notas_indicadores):
    """Calcula CI ajustado e o ranking operacional sequencial."""
    notas_indicadores = notas_indicadores.copy()
    notas_indicadores["CI"] = pd.to_numeric(notas_indicadores["CI"], errors="coerce")

    origem_clientes = "clientes_bloco_protecao"
    if origem_clientes not in notas_indicadores.columns:
        origem_clientes = "Clientes_bloco_protecao"
    notas_indicadores["clientes_bloco_protecao"] = pd.to_numeric(
        notas_indicadores[origem_clientes], errors="coerce"
    ).fillna(0)
    notas_indicadores["Peso_Conj"] = pd.to_numeric(
        notas_indicadores["Peso_Conj"], errors="coerce"
    ).fillna(1.0)

    notas_indicadores["Fator_Cluster"] = (
        notas_indicadores["Cluster_Priorizacao"].map(FATOR_CLUSTER).fillna(1.0).astype(float)
    )
    notas_indicadores["Fator_Ocorr90d"] = np.where(
        pd.to_numeric(notas_indicadores["QTD_Ocorrencias_90d"], errors="coerce").fillna(0) > 0,
        FATOR_OCORRENCIA_RECENTE,
        1.0,
    ).astype(float)

    mask_substituir_ci = (
        (notas_indicadores["CI"].isna() | (notas_indicadores["CI"] == 0))
        & notas_indicadores["tipo_equipamento"].astype(str).str.upper().isin(PROTECOES_USAM_CLIENTES_BLOCO)
    )
    notas_indicadores["CI_Ajustado"] = (
        notas_indicadores["CI"]
        .mask(mask_substituir_ci, notas_indicadores["clientes_bloco_protecao"])
        .fillna(0)
        .astype(float)
    )
    notas_indicadores["Prioridade_Final"] = (
        notas_indicadores["CI_Ajustado"]
        * notas_indicadores["Fator_Cluster"]
        * notas_indicadores["Fator_Ocorr90d"]
        * notas_indicadores["Peso_Conj"]
        + np.where(
            notas_indicadores["alimentador"].astype(str).str.contains("34", na=False),
            BONUS_ALIMENTADOR_345KV,
            0,
        )
    )
    # Ordem do ranking: CI real >= 1000, CI substituído e, por fim, demais notas.
    prioridade_ranking = np.select(
        [notas_indicadores["CI"] >= 1000, mask_substituir_ci],
        [1, 2],
        default=3,
    )
    
    # Notas com mesmo tier e mesma Prioridade_Final compartilham rank; tiers diferentes nunca colidem. 
    # Rank continua batendo com a ordem das linhas, então a seleção respeita o CI ≥ 1000.

    notas_indicadores = notas_indicadores.assign(_prioridade_ranking=prioridade_ranking).sort_values(
        ["_prioridade_ranking", "Prioridade_Final"], ascending=[True, False], kind="stable"
    )
    chave = notas_indicadores[["_prioridade_ranking", "Prioridade_Final"]]
    notas_indicadores["Rank_Prioridade"] = chave.ne(chave.shift()).any(axis=1).cumsum()
    notas_indicadores = notas_indicadores.drop(columns="_prioridade_ranking")
    
    
    # notas_indicadores = notas_indicadores.assign(_prioridade_ranking=prioridade_ranking).sort_values(
    #     ["_prioridade_ranking", "Prioridade_Final"], ascending=[True, False], kind="stable"
    # ).drop(columns="_prioridade_ranking")
    # notas_indicadores["Rank_Prioridade"] = (notas_indicadores["Prioridade_Final"].rank(method="dense", ascending=False).astype(int))
    return notas_indicadores


def selecionar_notas_em_etapas(df, base_mask, qtd_alvo, tipo_nota, csd, supervisao, agrupar_por_protecao=False):
    """Seleciona por etapa, priorizando blocos de proteção quando solicitado."""
    faltante = int(max(0, qtd_alvo))
    etapas = (
        ("1 Planejamento + Até 1 ano", "Planejamento", "Até 1 ano"),
        ("2 Planejamento + Até 2 anos", "Planejamento", "Até 2 anos"),
        ("3 CSD + Até 1 ano", "CSD", "Até 1 ano"),
        ("4 CSD + Até 2 anos", "CSD", "Até 2 anos"),
    )
    contagens = {etapa[0]: 0 for etapa in etapas}

    for nome_etapa, solicitante, classe_tempo in etapas:
        if faltante <= 0:
            break
        classes_tempo = [classe_tempo, "Até 2 ano"] if classe_tempo == "Até 2 anos" else [classe_tempo]
        candidatos = df.loc[
            base_mask
            & (df["Notas Selecionadas"] == 0)
            & (df["Solicitante"] == solicitante)
            & (df["Classificação Tempo"].isin(classes_tempo))
        ].sort_values(["Rank_Prioridade", "Priorizacao"], ascending=[True, False], kind="stable")
        if candidatos.empty:
            continue

        if not agrupar_por_protecao:
            indices = candidatos.head(faltante).index.tolist()
        else:
            candidatos = candidatos.copy()
            candidatos["_ordem"] = range(len(candidatos))
            candidatos["_protecao"] = np.where(
                candidatos["Protecao_Final"].notna(),
                candidatos["Protecao_Final"].astype(str),
                "__SEM_PROTECAO__" + candidatos.index.astype(str),
            )
            grupos = candidatos.groupby("_protecao", sort=False)
            indices = []
            for _, grupo in grupos:
                if faltante <= 0:
                    break
                idx_grupo = grupo.sort_values("_ordem").index.tolist()
                idx_adicionar = idx_grupo[:faltante]
                indices.extend(idx_adicionar)
                faltante -= len(idx_adicionar)

        if indices:
            df.loc[indices, "Notas Selecionadas"] = 1
            df.loc[indices, "Tipo Nota Selecionada"] = tipo_nota
            df.loc[indices, "Etapa Selecionada"] = nome_etapa
            contagens[nome_etapa] += len(indices)
            if not agrupar_por_protecao:
                faltante -= len(indices)

    resumo = {
        "CSD": csd,
        "Supervisão": supervisao,
        "Tipo Nota Selecionada": tipo_nota,
        "Meta": int(qtd_alvo),
        "Selecionadas": int(qtd_alvo - faltante),
        **contagens,
        "Faltante": int(faltante),
    }
    return faltante, resumo


def executar_selecao_por_supervisao(notas_indicadores, divisao_supervisao):
    """Executa a seleção de poda e manutenção com a mesma regra para ambos os estados."""
    notas_indicadores["Notas Selecionadas"] = 0
    notas_indicadores["Tipo Nota Selecionada"] = pd.NA
    notas_indicadores["Etapa Selecionada"] = pd.NA
    resumos, faltas = [], []

    for _, linha in divisao_supervisao.iterrows():
        csd, supervisao = linha["CSD"], linha["Supervisão"]
        base = (notas_indicadores["CSD"] == csd) & (notas_indicadores["SUPERVISAO"] == supervisao)
        for tipo, coluna_meta, agrupar in (
            ("Poda", "QTD Notas Poda", False),
            ("Manutenção", "QTD Notas Manut", True),
        ):
            meta = int(np.nan_to_num(linha[coluna_meta], nan=0))
            faltante, resumo = selecionar_notas_em_etapas(
                notas_indicadores,
                base & (notas_indicadores["Tipo Nota"] == tipo),
                meta,
                tipo,
                csd,
                supervisao,
                agrupar_por_protecao=agrupar,
            )
            resumos.append(resumo)
            if faltante:
                faltas.append({
                    "CSD": csd,
                    "Supervisão": supervisao,
                    "Tipo Nota": tipo,
                    "Quantidade Faltante": faltante,
                })
    return notas_indicadores, pd.DataFrame(resumos), pd.DataFrame(faltas)
