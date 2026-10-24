# -*- coding: utf-8 -*-
__title__ = "Conectar\nTubos"
__doc__ = """Conecta automaticamente todos os segmentos de tubo."""
__author__ = "Guilherme Geraldo da Silva"

from pyrevit import DB, forms, revit

# Importa os submódulos que possuem a lógica isolada de cada rota
import op1_lavatorio_caixa
import op2_caixa_coletor
import op3_direto_coletor
import op4_hidraulico

doc = revit.doc
uidoc = revit.uidoc

OPCOES = {
    u"1. Lavatório → Caixa sifonada": op1_lavatorio_caixa,
    u"2. Caixa sifonada → Coletor (Y a 45°)": op2_caixa_coletor,
    u"3. Aparelho direto → Coletor": op3_direto_coletor,
    u"4. Ligações Hidráulica": op4_hidraulico,
}

def main():
    if doc.ActiveView.ViewType != DB.ViewType.FloorPlan:
        forms.alert(u"Use este comando numa planta de piso.")
        return
        
    escolha = forms.CommandSwitchWindow.show(sorted(OPCOES.keys()), message=u"O que o script deve ligar?")
    if not escolha: return
    
    # Chama a função principal (executar) do módulo selecionado
    modulo_escolhido = OPCOES[escolha]
    modulo_escolhido.executar(doc, uidoc)

if __name__ == "__main__":
    main()