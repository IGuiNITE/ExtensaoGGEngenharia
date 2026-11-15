# -*- coding: utf-8 -*-
import math
import traceback
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import (
    Form, Label, TextBox, Button, DialogResult,
    FormStartPosition, FormBorderStyle
)
from System.Drawing import Point, Size

from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import DB, UI, revit, script

from geom_utils import *
from revit_mep import *
from ui_utils import *
from db_hidraulica import carregar_tabela

# ----------------------------------------------------------------------------
# CONFIGURAÇÃO
# ----------------------------------------------------------------------------
P_UC = u"Unidades de carga"          # leitura

# Parâmetros de gravação (já existem nos tubos do modelo).
# Conversão de unidade é feita pelo tipo do parâmetro (comprimento, vazão, velocidade, pressão).
PARAMS = {
    "leq":  u"Comprimento Equivalente",          # m
    "dz":   u"Diferença de Cota (m)",            # m
    "q":    u"Vazão (L/s)",                      # L/s
    "v":    u"Velocidade (m/s)",                 # m/s
    "j":    u"Perda de Carga Unitária",          # mca/m (gravado como pressão: mca x 9,80665 = kPa)
    "htub": u"Perda de Carga Tubulação",         # mca
    "hloc": u"Perda de Carga Localizada",        # mca
    "htot": u"Perda de Carga Total",             # mca
    "pin":  u"Pressão Disponivel",               # mca (início do tubo)
    "pout": u"Pressão Disponivel Residual",      # mca (fim do tubo)
    "pmca": u"Pressão (mca)",                    # mca, tipo Número (compartilhado, para etiqueta): pressão no fim do tubo
}
COMPRIMENTOS = ("leq", "dz")

# DN: (DI mm, [Joelho90, Joelho45, Curva90, Curva45, TeDir, TeLa, TeBi, Gaveta, Esfera])
TABELA = carregar_tabela()
IDX = {"J90": 0, "J45": 1, "C90": 2, "C45": 3, "TeDir": 4, "TeLa": 5, "TeBi": 6, "Gaveta": 7, "Esfera": 8}
ROTULO = {"J90": u"Joelho 90", "J45": u"Joelho 45", "C90": u"Curva 90", "C45": u"Curva 45",
          "TeDir": u"Tê direto", "TeLa": u"Tê lateral", "TeBi": u"Tê bilateral",
          "Gaveta": u"Reg. gaveta", "Esfera": u"Reg. esfera"}

DIVERGENCIA_DI = 0.15  # mm


# ----------------------------------------------------------------------------
# INTERFACE CPYTHON 3 (substitutos de pyrevit.forms)
# ----------------------------------------------------------------------------
def avisar(msg):
    UI.TaskDialog.Show(u"Cálculo Hidráulico", msg)


def pedir_texto(prompt, default=u"", title=u"Cálculo Hidráulico"):
    form = Form()
    form.Text = title
    form.Size = Size(380, 175)
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False

    lbl = Label()
    lbl.Text = prompt
    lbl.Location = Point(15, 15)
    lbl.Size = Size(335, 40)
    form.Controls.Add(lbl)

    txt = TextBox()
    txt.Text = default
    txt.Location = Point(15, 60)
    txt.Size = Size(335, 25)
    form.Controls.Add(txt)

    btn_ok = Button()
    btn_ok.Text = u"OK"
    btn_ok.DialogResult = DialogResult.OK
    btn_ok.Location = Point(190, 98)
    form.Controls.Add(btn_ok)

    btn_cancel = Button()
    btn_cancel.Text = u"Cancelar"
    btn_cancel.DialogResult = DialogResult.Cancel
    btn_cancel.Location = Point(275, 98)
    form.Controls.Add(btn_cancel)

    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    if form.ShowDialog() == DialogResult.OK:
        return txt.Text
    return None


# ----------------------------------------------------------------------------
# AUXILIARES
# ----------------------------------------------------------------------------
class FiltroTubo(UI.Selection.ISelectionFilter):
    __namespace__ = "GG_FiltroTubo_Hidraulico"

    def __init__(self, ids=None):
        self.ids = ids

    def AllowElement(self, elem):
        if not isinstance(elem, DB.Plumbing.Pipe):
            return False
        return self.ids is None or eid(elem.Id) in self.ids

    def AllowReference(self, ref, pt):
        return False


def eid(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return element_id.IntegerValue


def _piping_conns(elem):
    return [c for c in iter_connectors(elem) if c.Domain == DB.Domain.DomainPiping]


def ler_uc(pipe):
    p = pipe.LookupParameter(P_UC)
    if p is None or not p.HasValue:
        return None
    try:
        if p.StorageType == DB.StorageType.Double:
            return p.AsDouble()
        return float(p.AsString().replace(",", "."))
    except Exception:
        return None


def _para_interno(p, chave, valor):
    """Converte o valor (m, L/s, m/s, mca ou número) para a unidade interna do Revit, conforme o tipo do parâmetro."""
    try:
        tid = p.Definition.GetDataType().TypeId.lower()
    except Exception:
        return valor / 0.3048 if chave in COMPRIMENTOS else valor
    U = DB.UnitUtils
    if "length" in tid:
        return U.ConvertToInternalUnits(valor, DB.UnitTypeId.Meters)
    if "flow" in tid:
        return U.ConvertToInternalUnits(valor, DB.UnitTypeId.LitersPerSecond)
    if "velocity" in tid:
        return U.ConvertToInternalUnits(valor, DB.UnitTypeId.MetersPerSecond)
    if "pressure" in tid:
        return U.ConvertToInternalUnits(valor * 9.80665, DB.UnitTypeId.Kilopascals)
    return valor


def gravar(pipe, chave, valor, faltando):
    nome = PARAMS[chave]
    p = pipe.LookupParameter(nome)
    if p is None or p.IsReadOnly:
        faltando.add(nome)
        return
    try:
        if p.StorageType == DB.StorageType.String:
            p.Set(u"{:.3f}".format(valor).replace(".", ","))
        elif p.StorageType == DB.StorageType.Double:
            p.Set(_para_interno(p, chave, valor))
        else:
            faltando.add(nome)
    except Exception:
        faltando.add(nome)


# ----------------------------------------------------------------------------
# TOPOLOGIA: segue conectores a partir de um tubo, atravessando conexões
# ----------------------------------------------------------------------------
def _seguir(conn, ids, cadeia, prof):
    """A partir de um conector, retorna [(tubo, conector_do_tubo, cadeia)].
    cadeia = [(conexão, conector_montante, conector_jusante)]."""
    achados = []
    if prof > 6:
        return achados
    dono_atual = eid(conn.Owner.Id)
    for r in conn.AllRefs:
        dono = r.Owner
        if eid(dono.Id) == dono_atual:
            continue  # AllRefs inclui o próprio conector
        if isinstance(dono, DB.Plumbing.Pipe):
            if eid(dono.Id) in ids:
                achados.append((dono, r, cadeia))
        elif isinstance(dono, DB.FamilyInstance):
            for oc in _piping_conns(dono):
                if oc.Id == r.Id:
                    continue
                achados += _seguir(oc, ids, cadeia + [(dono, r, oc)], prof + 1)
    return achados


def cadeia_raiz(up):
    for r in up.AllRefs:
        if eid(r.Owner.Id) == eid(up.Owner.Id):
            continue
        if isinstance(r.Owner, DB.FamilyInstance):
            outros = [c for c in _piping_conns(r.Owner) if c.Id != r.Id and c.IsConnected]
            return [(r.Owner, outros[0] if outros else None, r)]
    return []


# ----------------------------------------------------------------------------
# COMPRIMENTO EQUIVALENTE DA CONEXÃO
# ----------------------------------------------------------------------------
def classificar(conexao, fin, fout):
    """Retorna chave da tabela (ou None se não há perda localizada)."""
    nome = (type_name(conexao) + u" " + element_label(conexao)).lower()
    conns = _piping_conns(conexao)

    try:
        eh_acessorio = conexao.Category.BuiltInCategory == DB.BuiltInCategory.OST_PipeAccessory
    except Exception:
        eh_acessorio = False
    if eh_acessorio:
        if u"gaveta" in nome:
            return "Gaveta"
        if u"esfera" in nome or u"bola" in nome:
            return "Esfera"
        if u"registro" in nome and (u"pressão" in nome or u"pressao" in nome):
            return "Esfera"  # registro de pressão = esfera
        return None

    if fin is None:
        return None

    if len(conns) == 2:
        dot = max(-1.0, min(1.0, fin.CoordinateSystem.BasisZ.DotProduct(fout.CoordinateSystem.BasisZ)))
        defl = 180.0 - math.degrees(math.acos(dot))
        if defl < 10.0:
            return None  # luva, união, redução
        curva = u"curva" in nome
        if defl < 67.5:
            return "C45" if curva else "J45"
        return "C90" if curva else "J90"

    # tê (ou similar com 3+ conectores)
    branch = None
    for i in range(len(conns)):
        for j in range(i + 1, len(conns)):
            if conns[i].CoordinateSystem.BasisZ.DotProduct(conns[j].CoordinateSystem.BasisZ) < -0.95:
                resto = [c for k, c in enumerate(conns) if k not in (i, j)]
                if resto:
                    branch = resto[0]
    if branch is None:
        return "TeLa"
    if fin.Id == branch.Id:
        return "TeBi"
    if fout.Id == branch.Id:
        return "TeLa"
    return "TeDir"


def leq_cadeia(cadeia, dn):
    total, rotulos, aviso = 0.0, [], []
    for conexao, fin, fout in cadeia:
        chave = classificar(conexao, fin, fout)
        if chave is None:
            continue
        if dn not in TABELA:
            aviso.append(u"DN {} fora da tabela: comprimento equivalente de '{}' ignorado.".format(dn, ROTULO[chave]))
            continue
        total += TABELA[dn][1][IDX[chave]]
        rotulos.append(ROTULO[chave])
    return total, rotulos, aviso


# ----------------------------------------------------------------------------
# FÓRMULAS (exatamente as do Excel)
# ----------------------------------------------------------------------------
def vazao(uc):
    return 0.3 * math.sqrt(uc)


def velocidade(q, di):
    return (q * 0.001) / (0.785 * ((di / 1000.0) ** 2))


def flamant(q, di):
    """Perda por metro (mca/m). Q em L/s, DI em mm."""
    return (8.69 * 1000000.0 * (q ** 1.75) * (di ** -4.75)) / 10.0


def hazen(q, di):
    """Perda por metro (mca/m), C = 150. Q em L/s, DI em mm."""
    return 10.643 * ((q / 1000.0) ** 1.85) * (150.0 ** -1.85) * ((di / 1000.0) ** -4.87)


def perdas(q, di, ltot):
    """Pior caso entre Flamant e Hazen-Williams.
    Retorna (J unitária mca/m, perda total mca, método)."""
    f, h = flamant(q, di), hazen(q, di)
    if f >= h:
        return f, f * ltot, u"Flamant"
    return h, h * ltot, u"Hazen-Williams"


# ----------------------------------------------------------------------------
# PRINCIPAL
# ----------------------------------------------------------------------------
def selecionar_tubos(uidoc, doc):
    pre = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    pre = [e for e in pre if isinstance(e, DB.Plumbing.Pipe)]
    if pre:
        return pre
    refs = uidoc.Selection.PickObjects(
        UI.Selection.ObjectType.Element,
        FiltroTubo(),
        u"Selecione os tubos do trecho e clique em Concluir"
    )
    return [doc.GetElement(r.ElementId) for r in refs]


def executar(doc, uidoc):
    try:
        tubos = selecionar_tubos(uidoc, doc)
        if not tubos:
            avisar(u"Nenhum tubo selecionado.")
            return
        ids = set(eid(t.Id) for t in tubos)

        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            FiltroTubo(ids),
            u"Clique no PRIMEIRO tubo (perto da extremidade de onde a água vem)"
        )
        raiz = doc.GetElement(ref.ElementId)
        clique = ref.GlobalPoint
    except OperationCanceledException:
        return
    except Exception:
        print(traceback.format_exc())
        return

    txt = pedir_texto(
        default=u"2.0",
        prompt=u"Pressão disponível na origem do trecho (mca):\n(de outro local, pressurizador ou bomba)",
        title=u"Cálculo Hidráulico"
    )
    if txt is None:
        return
    try:
        p0 = float(txt.replace(",", "."))
    except Exception:
        avisar(u"Valor de pressão inválido.")
        return

    avisos = []

    # extremidade a montante do primeiro tubo
    ends = end_piping(raiz)
    if len(ends) != 2:
        avisar(u"O primeiro tubo não tem 2 conectores de tubulação.")
        return
    ids_sem = set(ids)
    ids_sem.discard(eid(raiz.Id))
    cont = [len(_seguir(c, ids_sem, [], 0)) for c in ends]
    if cont[0] > 0 and cont[1] == 0:
        up = ends[1]
    elif cont[1] > 0 and cont[0] == 0:
        up = ends[0]
    else:
        up = min(ends, key=lambda c: c.Origin.DistanceTo(clique))

    # percorre a árvore do primeiro tubo para os pontos de consumo
    fila = [(raiz, up, cadeia_raiz(up), None)]
    visitados, ordem = set(), []
    while fila:
        pipe, upc, cad, pai = fila.pop(0)
        k = eid(pipe.Id)
        if k in visitados:
            continue
        visitados.add(k)
        ordem.append((pipe, upc, cad, pai))
        down = [c for c in end_piping(pipe) if c.Id != upc.Id]
        if not down:
            continue
        for filho, fup, fcad in _seguir(down[0], ids, [], 0):
            fila.append((filho, fup, fcad, k))

    fora = ids - visitados
    if fora:
        avisos.append(u"{} tubo(s) selecionado(s) não foram alcançados a partir do primeiro tubo e ficaram sem cálculo.".format(len(fora)))

    # cálculos
    resultados, pout_de = [], {}
    for pipe, upc, cad, pai in ordem:
        k = eid(pipe.Id)
        dn = int(round(to_mm(pipe.Diameter)))
        try:
            di_rev = to_mm(pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_INNER_DIAM_PARAM).AsDouble())
        except Exception:
            di_rev = None

        if dn in TABELA:
            di = TABELA[dn][0]
            if di_rev and abs(di_rev - di) > DIVERGENCIA_DI:
                avisos.append(u"Tubo {}: DI do modelo ({:.1f} mm) difere da tabela ({:.1f} mm) para DN {}. Usei a tabela.".format(eid(pipe.Id), di_rev, di, dn))
        elif di_rev:
            di = di_rev
            avisos.append(u"Tubo {}: DN {} não está na tabela. Usei o DI do modelo ({:.1f} mm).".format(eid(pipe.Id), dn, di))
        else:
            avisos.append(u"Tubo {}: sem diâmetro interno. Ignorado.".format(eid(pipe.Id)))
            continue

        uc = ler_uc(pipe)
        pin = pout_de[pai] if pai is not None else p0

        down = [c for c in end_piping(pipe) if c.Id != upc.Id]
        z_up = to_mm(upc.Origin.Z) / 1000.0
        z_dn = to_mm(down[0].Origin.Z) / 1000.0 if down else z_up
        dz = z_up - z_dn  # positivo quando a água desce

        if uc is None or uc <= 0:
            avisos.append(u"Tubo {}: sem unidades de carga. Sem perda de carga.".format(eid(pipe.Id)))
            q = v = leq = ltot = j = htot = 0.0
            rotulos = []
            metodo = u"-"
        else:
            q = vazao(uc)
            v = velocidade(q, di)
            leq, rotulos, av = leq_cadeia(cad, dn)
            avisos += [u"Tubo {}: {}".format(eid(pipe.Id), a) for a in av]
            ltot = to_mm(pipe.Location.Curve.Length) / 1000.0 + leq
            j, htot, metodo = perdas(q, di, ltot)

        pout = pin - htot + dz
        pout_de[k] = pout
        if pout < 0:
            avisos.append(u"Tubo {}: pressão final NEGATIVA ({:.2f} mca).".format(eid(pipe.Id), pout))
        resultados.append((pipe, dn, di, uc or 0.0, q, v, leq, rotulos, ltot, j, htot, dz, pin, pout, metodo))

    # gravação
    faltando = set()
    with revit.Transaction(u"Cálculo hidráulico"):
        for (pipe, dn, di, uc, q, v, leq, rotulos, ltot, j, htot, dz, pin, pout, metodo) in resultados:
            lpipe = ltot - leq
            gravar(pipe, "leq", leq, faltando)
            gravar(pipe, "dz", dz, faltando)
            gravar(pipe, "q", q, faltando)
            gravar(pipe, "v", v, faltando)
            gravar(pipe, "j", j, faltando)
            gravar(pipe, "htub", j * lpipe, faltando)
            gravar(pipe, "hloc", j * leq, faltando)
            gravar(pipe, "htot", htot, faltando)
            gravar(pipe, "pin", pin, faltando)
            gravar(pipe, "pout", pout, faltando)
            gravar(pipe, "pmca", pout, faltando)

    # relatório
    saida = script.get_output()
    linhas = []
    for (pipe, dn, di, uc, q, v, leq, rotulos, ltot, j, htot, dz, pin, pout, metodo) in resultados:
        linhas.append([
            saida.linkify(pipe.Id), dn, u"{:.1f}".format(di), u"{:.3f}".format(uc),
            u"{:.3f}".format(q), u"{:.2f}".format(v), u", ".join(rotulos) or u"-",
            u"{:.2f}".format(leq), u"{:.2f}".format(ltot), u"{:.4f}".format(j),
            u"{:.4f}".format(htot), metodo, u"{:.2f}".format(dz), u"{:.2f}".format(pin), u"{:.2f}".format(pout)
        ])
    saida.print_table(
        table_data=linhas,
        title=u"Cálculo hidráulico",
        columns=[
            u"Tubo", u"DN", u"DI (mm)", u"UC", u"Q (L/s)", u"V (m/s)", u"Conexão a montante",
            u"Leq (m)", u"L total (m)", u"J (mca/m)", u"Perda total (mca)", u"Método", u"ΔZ (m)",
            u"P inicial (mca)", u"P final (mca)"
        ]
    )
    if faltando:
        log(u"**Parâmetros não encontrados (ou somente leitura) nos tubos, não gravados:**\n\n" +
            u"\n".join(u"- " + n for n in sorted(faltando)))
    if avisos:
        log(u"**Avisos:**\n\n" + u"\n".join(u"- " + a for a in avisos))