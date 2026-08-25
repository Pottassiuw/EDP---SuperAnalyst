# Estudo das alterações nos pacotes de notas ES e SP

## Objetivo deste documento

Este documento registra o que mudou na geração dos pacotes de notas de manutenção e poda, por que cada decisão foi tomada e como o fluxo passou a funcionar. Ele compara a estrutura anterior, na qual ES e SP tinham trechos parecidos mas não idênticos, com a estrutura atual, em que as regras estão concentradas em um único arquivo Python.

Os arquivos principais atuais são:

- `Gerar Pacote de Notas - ES.ipynb`
- `Gerar Pacote de Notas - SP.ipynb`
- `regras_pacote_notas.py`

Os dois notebooks usam o mesmo código de negócio. A diferença entre eles fica restrita à configuração do estado, como UF e arquivo de hierarquia.

## Como estava antes

Havia versões diferentes dos notebooks e cópias antigas em `beta_test/`. O notebook de ES também tinha um arquivo `.BACKUP.ipynb`. Alguns pontos estavam duplicados ou divergiam entre os estados:

- a conversão de números brasileiros e a espera de download apareciam como funções locais;
- a extração de `tipo_equipamento` não era igual entre ES e SP;
- a classificação de poda e manutenção era usada de maneiras diferentes durante a seleção;
- a lógica de priorização existia nos dois notebooks, mas não estava centralizada;
- os nomes de tipo no resumo podiam alternar entre `Manut` e `Manutenção`;
- havia arquivos legados que não participavam do fluxo final.

Esse cenário aumentava o risco de uma correção entrar em um estado e ficar ausente no outro.

## Estrutura adotada

A mudança principal foi separar configuração de regra de negócio.

### Notebooks

Os notebooks continuam responsáveis por:

1. definir o estado e os caminhos;
2. baixar os arquivos de ocorrências e indicadores;
3. carregar notas, hierarquia, capacidade e prioridades de conjunto;
4. combinar as tabelas;
5. chamar as funções compartilhadas;
6. gerar os dois arquivos Excel de saída.

### Módulo compartilhado

O arquivo `regras_pacote_notas.py` concentra a regra que precisa ser idêntica para ES e SP. Isso evita manter duas cópias da mesma função.

| Linhas | Função | Responsabilidade |
|---:|---|---|
| 27 a 34 | `converter_numerico_br` | Converte números com vírgula decimal para valores numéricos. |
| 37 a 47 | `esperar_download` | Espera o arquivo baixado existir sem o arquivo parcial `.crdownload`. |
| 50 a 57 | `classificar_tipo_nota` | Cria a coluna `Tipo Nota` para toda nota processada. |
| 60 a 73 | `atribuir_tipo_equipamento` | Obtém o tipo de equipamento a partir da proteção, com fallback para a instalação. |
| 76 a 131 | `calcular_prioridade` | Calcula CI ajustado, prioridade final e ranking. |
| 134 a 195 | `selecionar_notas_em_etapas` | Seleciona notas por etapa, com opção de agrupar por proteção. |
| 198 a 231 | `executar_selecao_por_supervisao` | Executa a seleção de poda e manutenção por CSD e supervisão. |

## Alterações detalhadas no fluxo

### 1. Classificação fixa de tipo de nota

A função `classificar_tipo_nota`, nas linhas 50 a 57, criou uma classificação única:

```python
notas["Tipo Nota"] = np.where(
    notas["descrição_conjunto"].str.contains("PODA", case=False, na=False),
    "Poda",
    "Manutenção",
)
```

A lógica é simples: se a descrição do conjunto contém `PODA`, a nota é poda. Caso contrário, dentro da base de notas de manutenção ou poda já filtrada, ela é manutenção.

A coluna é criada antes da seleção. Portanto, notas que não entram no pacote continuam identificadas como `Poda` ou `Manutenção` em `Notas_Selecionadas.xlsx`. Isso é importante para auditoria e para explicar por que uma nota ficou fora do pacote.

### 2. Padronização de `tipo_equipamento`

A função `atribuir_tipo_equipamento`, nas linhas 60 a 73, substituiu a extração que usava apenas `local_instalacao` em parte dos notebooks.

A regra final é:

1. usar as posições `3:5` de `dispositivo_protecao` quando o valor existe e é válido;
2. usar as posições `3:5` de `local_instalacao` quando a proteção está vazia, nula, curta ou contém valores como `nan`, `none` ou `null`.

Exemplo: em `045RL000270969`, as posições `3:5` retornam `RL`.

A decisão foi tomada porque o dispositivo de proteção é a fonte mais direta do tipo de equipamento. O fallback preserva o processamento de registros incompletos sem descartar a nota.

### 3. CI ajustado para proteções cadastradas

Nas linhas 100 a 108 de `calcular_prioridade`, o CI é substituído por `clientes_bloco_protecao` somente quando as duas condições são verdadeiras:

- o CI original é nulo ou igual a zero;
- `tipo_equipamento` é `RL`, `DJ` ou `CF`.

O resultado fica registrado em `CI_Ajustado`.

```python
mask_substituir_ci = (
    (notas_indicadores["CI"].isna() | (notas_indicadores["CI"] == 0))
    & notas_indicadores["tipo_equipamento"].astype(str).str.upper()
      .isin(PROTECOES_USAM_CLIENTES_BLOCO)
)
```

A substituição não altera a coluna original `CI`. Isso permite diferenciar, na saída, um desligamento com indicador real de um caso em que o valor veio da quantidade de clientes protegidos pelo bloco.

### 4. Cálculo da prioridade final

As linhas 110 a 120 mantêm a fórmula de prioridade final:

```python
Prioridade_Final = (
    CI_Ajustado
    * Fator_Cluster
    * Fator_Ocorr90d
    * Peso_Conj
    + bônus para alimentador com "34"
)
```

Os fatores usados são:

- cluster: conforme a classificação de prioridade do cluster;
- ocorrência recente: fator adicional quando houve ocorrência nos últimos 90 dias;
- conjunto: peso vindo da tabela de prioridade de conjuntos;
- alimentador de 34,5 kV: bônus configurado em `BONUS_ALIMENTADOR_345KV`.

Essa fórmula continua sendo usada para ordenar notas de mesma prioridade operacional.

### 5. Ranking final

A primeira versão da alteração criou uma coluna visível chamada `Tier_CI`. Ela foi removida após o ajuste de entendimento da regra.

O resultado final não cria uma seção, aba ou coluna adicional no arquivo Excel. A prioridade é aplicada diretamente na montagem de `Rank_Prioridade`.

As linhas 122 a 130 usam uma coluna temporária chamada `_prioridade_ranking`:

```python
prioridade_ranking = np.select(
    [notas_indicadores["CI"] >= 1000, mask_substituir_ci],
    [1, 2],
    default=3,
)
```

A ordem do ranking é:

1. notas com CI real maior ou igual a 1000;
2. notas sem CI, ou com CI zero, que receberam `clientes_bloco_protecao` por serem RL, DJ ou CF;
3. demais notas.

Dentro de cada grupo, a ordenação segue `Prioridade_Final` de forma decrescente. Depois disso, o código gera `Rank_Prioridade` de 1 até o fim da base.

A coluna temporária é removida imediatamente após a ordenação:

```python
.drop(columns="_prioridade_ranking")
```

A razão para usar essa coluna apenas internamente é deixar o resultado simples para quem lê a planilha, sem perder a regra operacional desejada. O CI real maior ou igual a 1000 sempre aparece antes. Em seguida vêm os casos sem indicador cujo impacto foi estimado pelo bloco de proteção.

### 6. Seleção de notas e agrupamento por proteção

A seleção foi centralizada nas linhas 134 a 231.

Para cada combinação de CSD e supervisão, o fluxo lê as metas de poda e manutenção. A seleção procura candidatos nas etapas já usadas no processo:

1. Planejamento e até 1 ano;
2. Planejamento e até 2 anos;
3. CSD e até 1 ano;
4. CSD e até 2 anos.

Os candidatos já chegam ordenados por `Rank_Prioridade` e, em caso de empate, por `Priorizacao`.

Para poda, a seleção é feita nota a nota. Para manutenção, a chamada usa `agrupar_por_protecao=True`. Nesse caso, o código tenta selecionar primeiro as notas da mesma `Protecao_Final` antes de passar para a próxima proteção. A intenção é não fragmentar uma atividade de manutenção de um mesmo bloco em vários pacotes quando isso puder ser evitado.

A coluna `Tipo Nota Selecionada` passou a registrar sempre `Poda` ou `Manutenção`. O resumo também usa `Tipo Nota Selecionada`, sem a abreviação `Manut`.

## Células alteradas nos notebooks

A tabela abaixo usa o índice da célula no arquivo Jupyter, começando em zero. Os dois notebooks possuem a mesma estrutura e os mesmos índices.

| Célula | Alteração | Motivo |
|---:|---|---|
| 2 | A instalação automática via `pip` foi substituída por uma observação sobre dependências. | Evita que o notebook altere o ambiente ao ser executado. |
| 3 | Inclusão dos imports de `regras_pacote_notas.py`. | Faz ES e SP usarem as mesmas funções e constantes. |
| 5 e 7 | Funções e constantes locais duplicadas foram removidas e substituídas por referência ao módulo compartilhado. | Evita duas implementações da mesma regra. |
| 9 | Configuração específica do estado: `UF`, caminhos e `ARQUIVO_HIERARQUIA`. | Mantém a única diferença necessária entre ES e SP. |
| 12 | Nomes dos CSVs passaram a usar `UF`; limpeza de arquivos parciais foi mantida. | Evita manter nomes ES e SP manualmente em várias células. |
| 13 e 14 | Download passou a usar os nomes parametrizados e `esperar_download`. | Usa a mesma espera de download nos dois estados. |
| 16 e 17 | Leitura dos CSVs foi padronizada por `arquivo1` e `arquivo2`. | Centraliza a definição dos nomes dos arquivos. |
| 25 e 26 | Leitura de `Notas_Pendentes.xlsx` e criação de `Tipo Nota`. | Garante a classificação para toda a base processada. |
| 36 | Leitura da hierarquia por `ARQUIVO_HIERARQUIA`. | ES e SP mantêm seus arquivos auxiliares próprios sem alterar o fluxo. |
| 38 e 39 | Leitura de capacidade e divisão por supervisão por `os.path.join`. | Padroniza a montagem dos caminhos. |
| 52 | Leitura de `Prioridade_Conjuntos_ENG.xlsx` por caminho padronizado. | Evita variação de barras e concatenação manual de texto. |
| 53 | Chamada de `atribuir_tipo_equipamento`. | Aplica a mesma regra de proteção e fallback para ES e SP. |
| 57 | Chamada de `calcular_prioridade`. | Aplica CI ajustado e ranking em uma única implementação. |
| 60 | Chamada de `executar_selecao_por_supervisao`. | Mantém poda e manutenção com os mesmos critérios nos dois estados. |
| 63 e 64 | Exportação de `Notas_Selecionadas.xlsx` e `Resumo_Selecao_Notas.xlsx`. | Mantém os arquivos de saída e o formato de exportação. |

Também foram removidos os outputs gravados nas células. Eles poderiam mostrar dados de uma execução antiga e confundir a revisão do notebook.

## Diferenças permitidas entre ES e SP

Depois da padronização, a célula 9 é a única diferença de código entre os notebooks.

| Configuração | ES | SP |
|---|---|---|
| `UF` | `ES` | `SP` |
| `ARQUIVO_HIERARQUIA` | `hierarquia.xlsx` | `Hierarquia_SP_2025_Supervisão.xlsx` |

Os nomes dos arquivos de ocorrências e indicadores são formados a partir de `UF`, por exemplo `Base_Ocorrencias_ES.csv` e `Base_Ocorrencias_SP.csv`.

## Arquivos removidos

Foram removidos arquivos que não faziam parte da estrutura final:

- `Gerar Pacote de Notas - ES.BACKUP.ipynb`;
- pasta `beta_test/`;
- `legado_ES.py` e `legado_SP.py`, que estavam dentro dessa pasta;
- arquivos temporários usados durante a padronização.

A estrutura de trabalho ficou com os dois notebooks oficiais e o módulo compartilhado.

## Validações realizadas

Foram executadas verificações de sintaxe nas células de código dos dois notebooks e no módulo Python. Também foram usados dados artificiais para validar os pontos abaixo:

- classificação de `Poda` e `Manutenção`;
- fallback de `tipo_equipamento`;
- substituição de CI para RL, DJ e CF;
- ordem do ranking: CI real maior ou igual a 1000, depois CI substituído, depois demais notas;
- ausência da coluna `Tier_CI` no resultado final;
- preenchimento de `Tipo Nota Selecionada` no pacote e no resumo;
- igualdade do código de ES e SP fora da célula de configuração.

As bases reais não foram baixadas nem processadas durante essa validação. Antes de usar o fluxo em produção, vale executar os dois notebooks com uma cópia das bases e conferir as primeiras posições de `Rank_Prioridade` e os totais do resumo.

## Melhorias futuras sugeridas

Estas melhorias não foram implementadas. Elas são opções para uma próxima etapa.

### Validar colunas de entrada

Antes de processar, o módulo pode conferir se todas as colunas necessárias existem, como `CI`, `dispositivo_protecao`, `local_instalacao`, `Protecao_Final`, `CSD` e `SUPERVISAO`. Em caso de ausência, o notebook pode mostrar uma mensagem clara em vez de falhar mais adiante com um erro genérico.

### Registrar métricas do pacote

Pode ser útil criar uma aba adicional com indicadores simples:

- quantidade total de notas por tipo;
- quantidade selecionada e não selecionada;
- quantidade de notas com CI real maior ou igual a 1000;
- quantidade de notas cujo CI foi substituído;
- metas atendidas e faltantes por CSD e supervisão.

Isso facilitaria a conferência antes do envio do pacote.

### Criar testes permanentes

Os testes usados na alteração foram pontuais. Um arquivo de testes automatizados poderia manter cenários fixos para ranking, fallback de equipamento e agrupamento por proteção. Assim, uma mudança futura no código poderia ser verificada antes de atualizar os notebooks.

### Parametrizar a configuração

Os caminhos de ES e SP ainda ficam nas células de configuração. Um arquivo de configuração simples, por exemplo JSON ou YAML, permitiria cadastrar novos estados ou mudar caminhos sem editar o código do notebook.

### Revisar a regra de corte de grupo de manutenção

Hoje a manutenção agrupa por `Protecao_Final`, mas respeita a quantidade faltante da meta. Quando um grupo é maior que o saldo, o código seleciona somente a parte que cabe. Se a operação exigir que todo grupo seja sempre mantido junto, a regra pode ser alterada para permitir ultrapassar a meta ou pular o grupo e seguir para outro. Essa decisão depende da regra operacional.
