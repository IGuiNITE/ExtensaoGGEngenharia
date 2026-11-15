#! python3
# -*- coding: utf-8 -*-
__title__ = "Cálculos\nHidráulicos"
__doc__ = """Calcula vazão, velocidade, perdas de carga e pressão em cada tubo de água fria.

COMO USAR
1. Selecione os tubos do trecho (só água fria), da origem até os pontos de consumo. Se não selecionar nada antes, o botão pede a seleção (clique em Concluir).
2. Clique no botão e escolha "1. Cálculo Hidráulico".
3. Clique no PRIMEIRO tubo, perto da ponta de onde a água vem. O aviso aparece na barra de status (canto inferior esquerdo).
4. Informe a pressão disponível na origem, em mca.

GRAVA EM CADA TUBO
Vazão (L/s), Velocidade (m/s), Comprimento Equivalente, Diferença de Cota (m), Perda de Carga (Unitária, Tubulação, Localizada e Total), Pressão Disponivel, Pressão Disponivel Residual e Pressão (mca).

CRITÉRIOS
Vazão = 0,3·√(unidades de carga). Perda de carga = maior valor entre Flamant e Hazen-Williams (C = 150). Limites NBR 5626: velocidade máx. 3 m/s."""
__author__ = "Guilherme Geraldo da Silva"

from pyrevit import DB, UI, revit

# Importa os submódulos que possuem a lógica isolada de cada rota
import op1_calculo_hidraulico

doc = revit.doc
uidoc = revit.uidoc

OPCOES = {
    u"1. Cálculo Hidráulico": op1_calculo_hidraulico,
}

LINKS = [
    UI.TaskDialogCommandLinkId.CommandLink1,
    UI.TaskDialogCommandLinkId.CommandLink2,
    UI.TaskDialogCommandLinkId.CommandLink3,
    UI.TaskDialogCommandLinkId.CommandLink4,
]

RESULTADOS = {
    UI.TaskDialogResult.CommandLink1: 0,
    UI.TaskDialogResult.CommandLink2: 1,
    UI.TaskDialogResult.CommandLink3: 2,
    UI.TaskDialogResult.CommandLink4: 3,
}


def escolher_opcao(opcoes, mensagem):
    chaves = sorted(opcoes.keys())
    dialog = UI.TaskDialog(u"Cálculos Automáticos")
    dialog.MainInstruction = mensagem
    dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel

    for i, chave in enumerate(chaves[:4]):
        dialog.AddCommandLink(LINKS[i], chave)

    res = dialog.Show()
    if res in RESULTADOS:
        return chaves[RESULTADOS[res]]
    return None


def main():
    escolha = escolher_opcao(OPCOES, u"O que o script deve ligar?")
    if not escolha:
        return

    # Chama a função principal (executar) do módulo selecionado
    modulo_escolhido = OPCOES[escolha]
    modulo_escolhido.executar(doc, uidoc)


if __name__ == "__main__":
    main()