# -*- coding: utf-8 -*-
__title__ = "Ajuste de Toposolid\npor pontos"
__doc__ = "Recalcula corte e aterro de ajuste fino feito por inserção ou remoção de pontos."
__author__ = "Guilherme Geraldo da Silva"

#IMPORTS==================================================================================================================
import os
import json
import datetime
import math
import traceback

from Autodesk.Revit.DB import (XYZ, Toposolid, View3D, ReferenceIntersector,
                               FindReferenceTarget)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

#CONVERSÕES===============================================================================================================
FT_TO_M = 0.3048
FT3_TO_M3 = 0.028316846592
TOL_COTA_FT = 0.001
TOL_DELTA_M3 = 0.0001

#FUNÇÕES==================================================================================================================
class SomenteToposolid(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, Toposolid)
    def AllowReference(self, reference, position):
        return False

def selecionar_terreno():
    try:
        referencia = uidoc.Selection.PickObject(
            ObjectType.Element, SomenteToposolid(),
            "Selecione o TOPOSOLID do TN que voce ajustou manualmente")
    except OperationCanceledException:
        script.exit()
    return doc.GetElement(referencia.ElementId)

def escolher_json(terrain):
    pasta = os.path.dirname(doc.PathName)
    prefixo = "Talude_GradeV5_{}_".format(int(terrain.Id.Value))
    candidatos = []

    for nome in sorted(os.listdir(pasta)):
        if not nome.startswith(prefixo) or not nome.lower().endswith(".json"):
            continue
        caminho = os.path.join(pasta, nome)
        try:
            with open(caminho, "r") as arquivo:
                dados = json.load(arquivo)
            if (dados.get("versao") == 5 and
                    dados.get("metodo") == "grade_vertical_v5" and
                    dados.get("terreno_unique_id") == terrain.UniqueId):
                candidatos.append((nome, caminho, dados))
            else:
                output.print_md("JSON ignorado (nao e baseline V5 deste TN): `{}`".format(nome))
        except Exception as exc:
            output.print_md("JSON ignorado (falha ao ler): `{}` - `{}`".format(nome, exc))

    if not candidatos:
        raise Exception("Nao encontrei Talude_GradeV5_<TN>_<plato>.json na pasta do RVT. "
                        "Gere o talude com o Auto V5.1 antes de recalcular.")
    if len(candidatos) == 1:
        return candidatos[0][2], candidatos[0][1]

    opcoes = ["{} | sessao {}".format(nome, dados["sessao"])
              for nome, caminho, dados in candidatos]
    escolha = forms.SelectFromList.show(opcoes,
              title="Qual talude deseja recalcular?", multiselect=False)
    if not escolha:
        script.exit()
    escolhido = candidatos[opcoes.index(escolha)]
    return escolhido[2], escolhido[1]

def validar_json(data, terrain):
    if data.get("terreno_unique_id") != terrain.UniqueId:
        raise Exception("JSON pertence a outro Toposolid.")
    if int(data["terreno_id"]) != int(terrain.Id.Value):
        raise Exception("Id do TN nao confere com o baseline.")

    platform = doc.GetElement(str(data["plato_unique_id"]))
    if (platform is None or
            platform.UniqueId != data.get("plato_unique_id") or
            int(platform.Id.Value) != int(data["plato_id"])):
        raise Exception("O plato associado ao baseline nao foi encontrado neste RVT.")
    output.print_md("**ALVO DO RECALCULO (TN editado):** Id {} | UniqueId `{}`".format(
        terrain.Id.Value, terrain.UniqueId))
    output.print_md("**PLATO (somente validacao do JSON):** Id {} | UniqueId `{}`".format(
        platform.Id.Value, platform.UniqueId))
    if terrain.Id == platform.Id:
        raise Exception("Terreno e plato possuem o mesmo ElementId; selecione o TN editado.")

    if data.get("arquivo_rvt") != doc.PathName:
        output.print_md("**AVISO:** caminho do RVT diferente do JSON original. "
                        "IDs e UniqueIds do terreno/plato conferem; continuando.")

    grade = data["grade"]
    nx, ny = int(grade["nx"]), int(grade["ny"])
    if nx < 1 or ny < 1 or nx * ny != len(grade["cotas_ft"]):
        raise Exception("O JSON V5 possui grade incompleta/invalida.")
    if len(grade["bbox"]) != 4:
        raise Exception("BBox da grade invalida.")
    if float(grade["dx_ft"]) <= 0 or float(grade["dy_ft"]) <= 0:
        raise Exception("Espacamento da grade invalido.")
    if not data.get("sessao"):
        raise Exception("JSON V5 sem identificador de sessao.")
    return grade

def obter_vista3d(terrain):
    vista = doc.ActiveView
    if not isinstance(vista, View3D) or vista.IsTemplate:
        raise Exception("Abra uma vista 3D valida para o calculo.")
    if vista.IsSectionBoxActive:
        raise Exception("Desative a Section Box da vista 3D.")
    if terrain.IsHidden(vista):
        raise Exception("O Toposolid do terreno esta oculto nesta vista 3D.")
    if vista.GetCategoryHidden(terrain.Category.Id):
        raise Exception("A categoria do Toposolid esta oculta nesta vista 3D.")
    return vista

def obter_cota(probe, x, y, z_origem, alcance):
    menor_distancia = None
    for hit in probe.Find(XYZ(x, y, z_origem), XYZ.BasisZ.Negate()):
        distancia = hit.Proximity
        if 0 <= distancia <= alcance:
            if menor_distancia is None or distancia < menor_distancia:
                menor_distancia = distancia
    if menor_distancia is None:
        return None
    return float(z_origem - menor_distancia)

def amostrar_mesma_grade(probe, grade):
    xmin, ymin, _, _ = [float(v) for v in grade["bbox"]]
    nx, ny = int(grade["nx"]), int(grade["ny"])
    dx, dy = float(grade["dx_ft"]), float(grade["dy_ft"])
    origem, alcance = float(grade["z_origem_ft"]), float(grade["alcance_ft"])
    cotas_atuais = []
    passos_aviso = max(1, ny // 5)

    for j in range(ny):
        y = ymin + (j + 0.5) * dy
        for i in range(nx):
            x = xmin + (i + 0.5) * dx
            cotas_atuais.append(obter_cota(probe, x, y, origem, alcance))
        if (j + 1) % passos_aviso == 0 or (j + 1) == ny:
            output.print_md("Leitura atual: {}/{} linhas da grade".format(j + 1, ny))

    return cotas_atuais

def comparar_com_original(grade, atuais):
    originais = grade["cotas_ft"]
    if len(originais) != len(atuais):
        raise Exception("Amostragem atual e baseline possuem comprimentos diferentes.")

    area_ft2 = float(grade["dx_ft"]) * float(grade["dy_ft"])
    corte_ft3, aterro_ft3 = 0.0, 0.0
    alteradas, amostras, perderam_tn, novas_sem_baseline = 0, 0, 0, 0
    max_dz_ft = 0.0
    corte_celulas, aterro_celulas = 0, 0

    for z0, z1 in zip(originais, atuais):
        if z0 is None:
            if z1 is not None:
                novas_sem_baseline += 1
            continue
        if z1 is None:
            perderam_tn += 1
            continue
        amostras += 1
        dz = float(z1) - float(z0)
        max_dz_ft = max(max_dz_ft, abs(dz))
        if abs(dz) >= TOL_COTA_FT:
            alteradas += 1
        if dz < 0.0:
            corte_ft3 += -dz * area_ft2
            if abs(dz) >= TOL_COTA_FT:
                corte_celulas += 1
        elif dz > 0.0:
            aterro_ft3 += dz * area_ft2
            if abs(dz) >= TOL_COTA_FT:
                aterro_celulas += 1

    if perderam_tn:
        raise Exception("{} celulas que tinham TN no JSON nao encontraram TN agora. "
                        "Confira a vista 3D, geometria e limites da busca. "
                        "CSV preservado.".format(perderam_tn))
    if not amostras:
        raise Exception("Nao existem pares ANTES/DEPOIS validos para calculo.")
    if novas_sem_baseline:
        output.print_md("**AVISO:** {} celulas sem TN original agora tem TN. "
                        "Nao entram no volume: o JSON nao tem cota anterior nessas "
                        "posicoes.".format(novas_sem_baseline))

    return {"corte": corte_ft3 * FT3_TO_M3,
            "aterro": aterro_ft3 * FT3_TO_M3,
            "alteradas": alteradas,
            "validas": amostras,
            "celulas_corte": corte_celulas,
            "celulas_aterro": aterro_celulas,
            "max_dz_m": max_dz_ft * FT_TO_M}

def csv_da_sessao(data):
    pasta = os.path.dirname(doc.PathName)
    return os.path.join(pasta, "Talude_CorteAterro_V5_{}_{}_{}.csv".format(
        int(data["terreno_id"]), int(data["plato_id"]), data["sessao"]))

def numero_csv(texto):
    return float(texto.strip().replace(",", "."))

def ler_historico(caminho, data):
    """Retorna entradas validas da sessao na ordem gravada, ou erro explícito."""
    if not os.path.isfile(caminho) or os.path.getsize(caminho) == 0:
        return []

    historico = []
    with open(caminho, "r") as arquivo:
        for numero_linha, linha in enumerate(arquivo, 1):
            linha = linha.strip("\r\n")
            if not linha or linha.startswith("Data/Hora;"):
                continue
            partes = linha.split(";")
            if len(partes) != 7:
                raise Exception("CSV com {} colunas na linha {} (esperadas 7). "
                                "Historico nao alterado.".format(len(partes), numero_linha))
            if partes[6] != data["sessao"]:
                raise Exception("O CSV contem registro de outra sessao na linha {}. "
                                "Historico nao alterado.".format(numero_linha))
            try:
                corte, aterro = numero_csv(partes[1]), numero_csv(partes[2])
            except Exception:
                raise Exception("CSV com volume invalido na linha {}.".format(numero_linha))
            if (math.isnan(corte) or math.isinf(corte) or
                    math.isnan(aterro) or math.isinf(aterro)):
                raise Exception("CSV com volume nao finito na linha {}.".format(numero_linha))
            historico.append((corte, aterro, partes[0], partes[5]))
    return historico

def br(numero):
    return "{:.6f}".format(numero).replace(".", ",")

def acrescentar_historico(caminho, data, resultado, corte_anterior, aterro_anterior):
    """Acrescenta totais e diferencas. NUNCA soma novamente os totais anteriores."""
    existe = os.path.isfile(caminho) and os.path.getsize(caminho) > 0
    cabecalho = ("Data/Hora;Corte total (m3);Aterro total (m3);"
                 "Delta corte (m3);Delta aterro (m3);Origem;Sessao\n")
    corte, aterro = resultado["corte"], resultado["aterro"]
    delta_corte = corte - corte_anterior
    delta_aterro = aterro - aterro_anterior
    registro = ";".join([
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        br(corte), br(aterro), br(delta_corte), br(delta_aterro),
        "AjusteManualV5.4", str(data["sessao"])
    ]) + "\n"
    with open(caminho, "a") as arquivo:
        if not existe:
            arquivo.write(cabecalho)
        arquivo.write(registro)
        arquivo.flush()
    return delta_corte, delta_aterro

def relatar(grade, resultado, historico, caminho, vertices):
    output.print_md("## Recalculo do talude - V5.4")
    output.print_md("Toposolid: **{} vertices de superficie no estado atual** "
                    "(informativo; volume vem das cotas da grade).".format(vertices))
    output.print_md("Grade original: **{} x {} = {} celulas** | {:.3f} x {:.3f} m".format(
        grade["nx"], grade["ny"], len(grade["cotas_ft"]),
        grade["dx_ft"] * FT_TO_M, grade["dy_ft"] * FT_TO_M))
    output.print_md("Cotas validas: **{}** | alteradas desde o TN: **{}** "
                    "(corte {}, aterro {}) | maior |dZ|: **{:.4f} m**".format(
        resultado["validas"], resultado["alteradas"],
        resultado["celulas_corte"], resultado["celulas_aterro"],
        resultado["max_dz_m"]))
    output.print_md("### Volumes TOTAIS desde o TN original")
    output.print_md("**Corte: {:.3f} m³ | Aterro: {:.3f} m³ | Saldo aterro - corte: {:+.3f} m³**".format(
        resultado["corte"], resultado["aterro"],
        resultado["aterro"] - resultado["corte"]))
    if historico:
        anterior = historico[-1]
        inicial = historico[0]
        output.print_md("### Variacao desde a ultima medicao do CSV")
        output.print_md("Corte: **{:+.3f} m³** | Aterro: **{:+.3f} m³**".format(
            resultado["corte"] - anterior[0],
            resultado["aterro"] - anterior[1]))
        output.print_md("### Variacao acumulada desde a PRIMEIRA medicao registrada")
        output.print_md("Corte: **{:+.3f} m³** | Aterro: **{:+.3f} m³**".format(
            resultado["corte"] - inicial[0],
            resultado["aterro"] - inicial[1]))
    else:
        output.print_md("**Aviso:** CSV da geracao nao encontrado. "
                        "Nao ha medicao anterior para calcular o delta manual; "
                        "os totais contra o TN continuam validos.")
    output.print_md("CSV da sessao: `{}`".format(caminho))

#MAIN==================================================================================================================
def main():
    if not doc.PathName:
        forms.alert("Salve o RVT antes de recalcular, para localizar o JSON da sessao.",
                    exitscript=True)
    terrain = selecionar_terreno()
    try:
        data, caminho_json = escolher_json(terrain)
        grade = validar_json(data, terrain)
        vista = obter_vista3d(terrain)
        output.print_md("JSON V5/V5.1 original: `{}`".format(caminho_json))
        output.print_md("Sessao: **{}** | Plato Id: **{}** | Vista: **{}**".format(
            data["sessao"], data["plato_id"], vista.Name))

        probe = ReferenceIntersector(terrain.Id, FindReferenceTarget.Face, vista)
        atuais = amostrar_mesma_grade(probe, grade)
        resultado = comparar_com_original(grade, atuais)

        caminho_csv = csv_da_sessao(data)
        historico = ler_historico(caminho_csv, data)
        editor = terrain.GetSlabShapeEditor()
        vertices = len(list(editor.SlabShapeVertices)) if editor.IsEnabled else 0
        relatar(grade, resultado, historico, caminho_csv, vertices)

        if historico:
            corte_anterior, aterro_anterior = historico[-1][0:2]
            if (abs(resultado["corte"] - corte_anterior) < TOL_DELTA_M3 and
                    abs(resultado["aterro"] - aterro_anterior) < TOL_DELTA_M3):
                output.print_md("**Sem alteracao volumetrica desde a ultima medicao. "
                                "CSV nao recebeu linha duplicada.**")
                return
        else:
            # Sem o CSV inicial, NÃO inventar delta do ultimo ajuste.
            # A primeira linha da sessao registra os totais desde o TN (delta=total).
            corte_anterior, aterro_anterior = 0.0, 0.0
            if resultado["corte"] < TOL_DELTA_M3 and resultado["aterro"] < TOL_DELTA_M3:
                output.print_md("**Sem movimentacao detectada pela grade; CSV intacto.**")
                return

        delta_corte, delta_aterro = acrescentar_historico(
            caminho_csv, data, resultado, corte_anterior, aterro_anterior)
        output.print_md("**REGISTRO GRAVADO** | Delta corte {:+.3f} m³ | "
                        "Delta aterro {:+.3f} m³".format(delta_corte, delta_aterro))
        output.print_md("O JSON ORIGINAL nao foi modificado. O RVT nao foi editado pelo script.")

    except Exception as exc:
        output.print_md("**ERRO no recalculo:** `{}`".format(exc))
        output.print_md("```text\n{}\n```".format(traceback.format_exc()))
        forms.alert(("Falha no recalculo V5.4:\n{}\n\n"
                     "O script nao altera a geometria do Toposolid nem o JSON original.")
                    .format(exc))

if __name__ == "__main__":
    main()