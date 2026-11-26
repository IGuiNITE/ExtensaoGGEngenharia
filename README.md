# GG Engenharia — Extensão pyRevit

Extensão para o [pyRevit](https://github.com/pyrevitlabs/pyRevit) que automatiza tarefas repetitivas de **arquitetura**, **instalações hidrossanitárias** e **topografia** no Autodesk Revit: cotas de paredes, anotação de tubos, conexões de esgoto, água fria e ventilação, cálculo hidráulico e talude de terrenos.

Autor: **Guilherme Geraldo da Silva**
Contribuidor: **Antigravity (Google DeepMind)** — apoio em desenvolvimento, auditoria e documentação

---

## Sumário

- [Comandos disponíveis](#comandos-disponíveis)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Como usar cada comando](#como-usar-cada-comando)
  - [Arquitetura](#arquitetura)
  - [Hidrossanitário](#hidrossanitário)
  - [Topografia](#topografia)
- [Pré-requisitos do modelo](#pré-requisitos-do-modelo)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Configuração](#configuração)
- [Contribuindo](#contribuindo)
- [Licença](#licença)

---

## Comandos disponíveis

A extensão cria a aba **GG** na faixa de opções do Revit, com três painéis:

| Painel | Botão | O que faz |
|---|---|---|
| **Arquitetura** | Cotar Lote Auto | Cota várias paredes selecionadas de uma só vez |
| **Hidrossanitário** | Anotar Tubos | Anota tubos de esgoto (setas, inclinação, UHC, coluna) e água fria (cotas + diâmetro) |
| | Conectar Tubos | Cria as ligações de esgoto, água fria e ventilação (5 rotinas) |
| | Cálculos Hidráulicos | Calcula vazão, velocidade, perdas de carga e pressão em cada tubo de água fria |
| **Topografia** | Gerar talude automático | Gera o talude de um platô sobre o terreno natural e registra as cotas originais |
| | Ajuste de Toposolid por pontos | Recalcula corte e aterro após ajustes manuais no terreno |

Os botões com mais de uma rotina (Anotar, Conectar, Cálculos) abrem um menu para escolher a opção.

---

## Requisitos

- **Autodesk Revit 2025** (desenvolvido e usado nesta versão). Os comandos de topografia usam `Toposolid`, que só existe a partir do Revit 2024.
- **pyRevit 6.x**, com o motor **CPython 3** habilitado. O botão *Cálculos Hidráulicos* roda em Python 3 (`#! python3`); os demais usam IronPython.
- Os comandos foram feitos para o idioma e as famílias do projeto da GG (veja [Pré-requisitos do modelo](#pré-requisitos-do-modelo)).

---

## Instalação

1. Instale o [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases).
2. Clone este repositório dentro de uma pasta de sua escolha. A pasta clonada precisa se chamar `GG.extension`:

   ```bash
   cd "C:\Revit\Plugins"
   git clone https://github.com/IGuiNITE/ExtensaoGGEngenharia.git GG.extension
   ```

3. No Revit, abra **pyRevit → Settings → Custom Extension Directories** e adicione a pasta **que contém** `GG.extension` (no exemplo, `C:\Revit\Plugins`).
4. Clique em **Save Settings and Reload**. A aba **GG** aparece na faixa de opções.

Para atualizar, rode `git pull` dentro de `GG.extension` e faça o **Reload** do pyRevit.

---

## Como usar cada comando

### Arquitetura

#### Cotar Lote Auto

Cota várias paredes de uma só vez em uma **planta**.

1. Abra uma vista de planta.
2. Clique no botão e selecione as paredes (múltiplas) e clique em **Concluir**.
3. Para cada parede é criada uma cota com as faces da parede e das paredes transversais que a tocam.

Paredes horizontais são cotadas **para cima**; verticais, **para a esquerda**. O afastamento padrão é de 80 cm.

---

### Hidrossanitário

#### Anotar Tubos

Anota os tubos da vista ativa. Se houver tubos selecionados, apenas eles são anotados. Ao clicar, escolha:

**1. Esgoto**
- Tubos de **ventilação**: etiqueta de diâmetro com seta de um lado e inclinação do outro.
- **Demais tubos**: etiqueta com seta, inclinação (acima da seta) e UHC (do outro lado).
- **Tubos verticais acima de 2 m**: símbolo de coluna com linha de chamada. Segmentos verticais no mesmo eixo (tolerância de 5 cm) são somados como uma única prumada.

**2. Água Fria**
- Cria **cotas do eixo dos tubos**, até a face de uma parede de referência (opcional), e a **etiqueta de diâmetro** em cada tubo.
- Funciona em **plantas** e em **vistas 3D travadas** (isométricos).
- No 3D, a linha de cota fica **em cima do tubo principal** de cada direção (X, Y e prumadas), em vez de afastada do desenho.
- Se a cota já existe na vista, ela é recriada na nova posição (configurável).

> Para o 3D, trave a vista antes: *Salvar orientação e travar vista*.

#### Conectar Tubos

Funciona em **plantas de piso**. Escolha uma das rotinas:

| # | Rotina | Descrição |
|---|---|---|
| 1 | Lavatório para Caixa sifonada | Liga a saída do lavatório a uma entrada livre da caixa. Se a caixa precisar ser deslocada para alinhar (mais de 300 mm), pede confirmação. |
| 2 | Caixa sifonada para Coletor | Traça o ramal da caixa até o coletor com **junção Y a 45°**, respeitando o declive. Ajusta a altura da caixa se necessário. |
| 3 | Aparelho direto para Coletor | Liga a saída de um aparelho sanitário diretamente ao coletor, também com junção Y a 45°. |
| 4 | Ligações Hidráulica | Alinha as peças de água fria selecionadas, cria os pontos de consumo e as prumadas, monta o ramal com tês e cria o registro geral (de gaveta) e os registros de pressão, com a opção de escolher o lado do registro. |
| 5 | Ligações Ventilação | Seleciona a coluna de ventilação (tubo vertical) e desenha o traçado clicando pontos na planta (ESC para concluir). A rota é ajustada a múltiplos de 45° e sobe em direção à coluna com **1%** de declive, criando ramais nos cruzamentos com os tubos de esgoto. |

Valores padrão (editáveis nas constantes de cada arquivo): declive de esgoto **2%**, declive de ventilação **1%**, altura mínima do ramal de ventilação **300 mm**.

#### Cálculos Hidráulicos

Dimensiona a rede de **água fria** e grava os resultados nos próprios tubos.

1. Selecione os tubos do trecho (só água fria), da origem até os pontos de consumo.
2. Clique no botão e escolha **1. Cálculo Hidráulico**.
3. Clique no **primeiro tubo**, perto da ponta de onde a água vem.
4. Informe a **pressão disponível na origem**, em mca.

**Parâmetros gravados em cada tubo:**
Vazão (L/s), Velocidade (m/s), Comprimento Equivalente, Diferença de Cota (m), Perda de Carga (Unitária, Tubulação, Localizada e Total), Pressão Disponivel, Pressão Disponivel Residual e Pressão (mca).

**Critérios de cálculo:**
- Vazão = 0,3 · √(Σ unidades de carga)
- Perda de carga = maior valor entre **Flamant** e **Hazen-Williams** (C = 150)
- Limite de velocidade: **3 m/s** (NBR 5626)
- Comprimentos equivalentes das conexões (joelhos, curvas, tês, registros) vêm de uma tabela de PVC em **SQLite** (`Lib/data/hidraulica.db`), de DN 20 a DN 110. O banco é criado e preenchido automaticamente na primeira execução.

---

### Topografia

#### Gerar talude automático

Gera o talude em volta de um platô e registra o terreno natural **antes** da edição.

1. Salve o projeto `.rvt` (o JSON da sessão é gravado na mesma pasta).
2. Abra uma **vista 3D** com o terreno e a categoria *Toposolids* visíveis e a **Section Box desligada**.
3. Selecione o **platô** e depois o **terreno natural** (que será editado).
4. Informe o **ângulo do talude** (padrão 45°), os **pontos por aresta** (padrão 10) e os **pontos no leque de cada canto** (padrão 20).

O comando amostra o terreno por raios, grava `Talude_GradeV5_<id do TN>_<platô>.json` e insere os pontos de controle no Toposolid. Se algum raio falhar, **nada é alterado**.

#### Ajuste de Toposolid por pontos

Recalcula corte e aterro depois de você ajustar o terreno manualmente (inserindo ou removendo pontos).

1. Salve o `.rvt` e selecione o Toposolid ajustado.
2. O comando localiza o JSON da sessão, reamostra a mesma grade e compara com o terreno natural original.
3. Mostra no relatório os volumes de **corte**, **aterro** e **saldo**, a variação desde a última medição e a variação acumulada.
4. Grava uma linha no **CSV da sessão**. Se não houve mudança desde a última medição, o CSV não recebe linha duplicada.

O JSON original e a geometria do Revit **não são modificados** por este comando.

---

## Pré-requisitos do modelo

Os comandos procuram famílias, tipos e sistemas pelo **nome**. Se o projeto usar nomes diferentes, ajuste as constantes no início dos arquivos.

| Comando | Precisa existir no projeto |
|---|---|
| Anotar Tubos — Esgoto | Etiquetas de tubo: `Anotação Tubos 4 : DN + Seta 1-1`, `Anotação Tubos 3 : Inclinação`, `Anotação Tubos 3 : UHC` e `Anotacao_Coluna : Coluna`. Tubos de ventilação são identificados por "vent" no sistema. |
| Anotar Tubos — Água Fria | Etiqueta `Anotação Tubos 3 : DN` (ou `Anotacao_Tubo Inicial : Diâmetro`). Tubos com sistema "água fria" ou "cold water". |
| Conectar — Esgoto (1, 2, 3) | Tipo de tubo `PVC Esgoto Série Normal` e sistema `Esgoto Sanitário` (usa o do elemento selecionado se existir). Famílias de junção com parâmetro de **ângulo** editável. |
| Conectar — Hidráulica (4) | Sistema `Água Fria Potável`; famílias `RG_Registro de Gaveta c/ PVC` e `RP_Registro de Pressão C/ PVC`; tipo de tubo soldável (marrom). |
| Conectar — Ventilação (5) | Sistema de ventilação no projeto e tubos de esgoto na planta. |
| Cálculos Hidráulicos | Parâmetro `Unidades de carga` nos tubos e os parâmetros de gravação listados acima, já criados nos tubos. |

---

## Estrutura do projeto

```text
GG.extension/
├── GG.tab/
│   ├── Arquitetura.panel/
│   │   └── Cotas Automáticas.pushbutton/        # Cotar Lote Auto
│   ├── Hidrossanitário.panel/
│   │   ├── Anotações Automáticas.pushbutton/    # script.py + op1_esgoto + op2_agua_fria
│   │   ├── Conexões Automáticas.pushbutton/     # script.py + op1..op5 (lavatório, caixa, direto, hidráulica, ventilação)
│   │   └── Cálculos Automáticos.pushbutton/     # script.py + op1_calculo_hidraulico
│   └── Topografia.panel/
│       └── Topografia.panel/
│           ├── AjusteDePontos.pushbutton/
│           └── TaludeDeToposolid.pushbutton/
├── Lib/                                         # código compartilhado entre os botões
│   ├── geom_utils.py                            # geometria 2D, constantes de esgoto
│   ├── revit_mep.py                             # conectores, tubos, curvas, junções Y
│   ├── ui_utils.py                              # seleção de rota, escolha de saídas
│   ├── db_hidraulica.py                         # acesso ao banco SQLite
│   └── data/
│       ├── criar_banco.py                       # cria e popula hidraulica.db
│       └── hidraulica.db                        # gerado automaticamente (ignorado pelo Git)
└── .gitignore
```

Cada botão com várias opções segue o mesmo padrão: o `script.py` mostra o menu e chama a função `executar(doc, uidoc)` do módulo `opN_*.py` escolhido. Para adicionar uma nova rotina, crie o módulo, importe-o no `script.py` e registre-o no dicionário `OPCOES`.

---

## Configuração

Os parâmetros ficam em constantes no topo de cada arquivo. As principais:

**`op2_agua_fria.py`**

| Constante | Padrão | Função |
|---|---|---|
| `COTA_SOBRE_TUBO` | `True` | No 3D, a linha da cota fica em cima do tubo principal |
| `COTA_SOBRE_TUBO_MM` | `8.0` | Folga (mm no papel) entre o topo do tubo e a linha da cota |
| `REPOSICIONAR_COTAS` | `True` | Recria cotas já existentes na nova posição |
| `COTAR_PRUMADAS` | `True` | Inclui as prumadas nas cotas |
| `TAG_DIAMETRO` | `Anotação Tubos 3 : DN` | Etiqueta de diâmetro |
| `NOME_TIPO_COTA` | `None` | Tipo de cota a usar (`None` = padrão do projeto) |

**Outros**

| Arquivo | Constante | Padrão |
|---|---|---|
| `op1_esgoto.py` | `COLUNA_ALTURA_MIN_M` | `2.0` m |
| `geom_utils.py` | `DECLIVE` (esgoto) | `0.02` (2%) |
| `op5_ventilacao.py` | `DECLIVE_VENT`, `ALTURA_MIN_RAMAL` | `0.01`, `300` mm |
| `op4_hidraulico.py` | `ALTURA_BAIXA`, `ALTURA_ALTA`, `ALTURA_REGISTRO_GERAL` | `1000`, `1500`, `2100` mm |
| `Cotas Automáticas` | `OFFSET_CM` | `80` cm |
| `TaludeDeToposolid` | `PASSO_GRID_M`, `MAX_CELULAS` | `0.50` m, `25000` |

---

## Contribuindo

1. Faça um fork do repositório.
2. Crie uma branch: `git checkout -b minha-melhoria`.
3. Faça o commit: `git commit -m "Descreve a melhoria"`.
4. Envie: `git push origin minha-melhoria` e abra um Pull Request.

Antes de enviar, teste o comando no Revit e confira que o `Reload` do pyRevit carrega a aba sem erros.

---

## Licença

Este projeto está licenciado sob a **GNU General Public License v3.0 (GPLv3)** — veja o arquivo [LICENSE](LICENSE) para mais detalhes.
