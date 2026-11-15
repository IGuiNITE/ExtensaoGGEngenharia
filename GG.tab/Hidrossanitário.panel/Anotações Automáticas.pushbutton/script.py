# -*- coding: utf-8 -*-
__title__ = "Anotar\nTubos"
__doc__ = """Anota automaticamente todos os segmentos de tubo das plantas:
- Ventilação: seta (DN + Seta 1-1) de um lado e inclinação do outro
- Demais tubos: seta, inclinação (acima da seta) e UHC (do outro lado)
- Tubos verticais acima de 2 m: símbolo de coluna com linha de chamada
Se houver tubos selecionados na vista ativa, apenas eles são anotados.

Ao clicar, escolha a opção:
1. Esgoto: seta, inclinação e UHC nos tubos (e símbolo de coluna).
2. Água Fria: cotas do eixo dos tubos (até a parede de referência, se houver) e etiqueta de diâmetro."""
__author__ = "Guilherme Geraldo da Silva"

from pyrevit import forms, revit

# Submódulos com a lógica de cada opção
import op1_esgoto
import op2_agua_fria

doc = revit.doc
uidoc = revit.uidoc

OPCOES = {
    u"1. Esgoto": op1_esgoto,
    u"2. Água Fria": op2_agua_fria,
}

def main():
    escolha = forms.CommandSwitchWindow.show(sorted(OPCOES.keys()), message=u"O que deseja anotar?")
    if not escolha: return
    OPCOES[escolha].executar(doc, uidoc)

if __name__ == "__main__":
    main()
