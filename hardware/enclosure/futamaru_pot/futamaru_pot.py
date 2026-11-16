"""ふたまる 鉢植え型（まんまるキャラ）の筐体を Fusion で組み立てるスクリプト。

Fusion の［スクリプトとアドイン］から実行すると、新しいドキュメントに次の部品を作る。
寸法は「ユーザー パラメータ」に入るので、［修正］→［パラメータを変更］で数字を直せば本体の形が追随する
（顔のふた・腕・足と差し込み穴は実行時に本体の形から作るので、パラメータを変えたら再実行する）。

- 本体: 3号のプラ鉢ごと落とし込む、樽のようにふくらんだ鉢カバー。前面に丸い電子部品の部屋
  （植物の水とは壁で仕切る）。部屋の中に、Voice Base を載せる台と XIAO を差し込む枠がある。
  給電と書き込みの USB-C ケーブルは、XIAO の真下の縦の穴から本体の底の溝を通して背面へ出す
- 顔のふた: 本体と同じ曲面を丸く切り出したふた。磁石で付き、はめると継ぎ目の円しか見えない。
  電子部品は付けない。裏に浅い音の部屋を掘り、表には目を貼る平らな座面と、にっこりした弧にならべた
  口の穴がある。口の穴の奥には Voice Base のスピーカーの面が当たる
- 目: 黒く刷って顔のふたの座面に貼る、楕円の立体の目（2 個）
- 腕: 下がふっくらした卵形。本体の側面を卵形に掘り下げたくぼみに、平らな面をはめてピン 2 本で留める
- 足: 正面から見てアーチ形の、背の高い足。本体の穴に差し込み、根元は本体の曲面に沿わせてある
- 参照部品: 干渉チェック用の部品の外形（印刷しない）。Voice Base・XIAO・ジャンパー線のコネクタ・
  USB-C のプラグとケーブルを、組み立てたときの位置に置く

目・腕・足・口の形は、チャムさんのデザイン案（futamaru_3d_v2）を参考にした。
ディスプレイは実装の手間を考えて今回は付けない（2026-11-16 にチームで決定）。表情は立体の目で固定になる。
電子部品は、パーツ選定で決めた XIAO ESP32S3（Sense なし）と M5Stack Atomic Voice Base（A149）の 2 つ
（2026-11-16）。2 つはジャンパー線（メス）でつなぐ。

座標: Z が上、-Y が正面（顔の向き）。部品の高さは comp_zc から、奥行きは顔の基準面（Y = -face_y）から測る。
"""

from __future__ import annotations

import math
import os
import traceback

import adsk.core
import adsk.fusion

# 書き出し先。リポジトリの中から実行したときは hardware/enclosure/out、
# Fusion の Scripts フォルダにコピーしたものや MCP から実行したときは下の既定の場所
# （リポジトリを置いた場所が違うときは、ここを自分の hardware/enclosure/out に直す）
_DEFAULT_OUT = os.path.expanduser("~/Desktop/Lit/team4/hardware/enclosure/out")
try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = ""
OUT_DIR = (
    os.path.join(os.path.dirname(_here), "out")
    if _here.replace(os.sep, "/").endswith("hardware/enclosure/futamaru_pot")
    else _DEFAULT_OUT
)

# (名前, 値[mm], 説明)。順番は［パラメータを変更］の表示順になる
PARAMS = [
    # --- 本体（樽のようにふくらんだ鉢カバー。下の5点を通るなめらかな曲線を回転させる） ---
    ("body_h", 104.0, "本体の高さ（幅との比をチャムさんの案の 1.4 くらいにする）"),
    ("r_bot", 61.0, "底の半径"),
    ("r_low", 70.0, "下のふくらみの半径"),
    ("z_low", 20.0, "下のふくらみの高さ"),
    ("r_belly", 75.0, "いちばん太いところの半径"),
    ("z_belly", 50.0, "いちばん太いところの高さ（高さのほぼ真ん中）"),
    ("r_sh", 73.5, "肩の半径"),
    ("z_sh", 82.0, "肩の高さ"),
    ("r_top", 70.0, "上の縁の半径（鉢の穴の後ろの壁が薄くなりすぎない大きさ）"),
    ("body_fillet_bot", 8.0, "底の角の丸み"),
    ("body_fillet_top", 2.0, "上の縁の丸み（背面の壁が薄いので大きくできない）"),
    ("face_y", 60.5, "本体の中心から顔の基準面までの距離（部品の奥行きはここから測る）"),
    # --- 植物の鉢が入る穴（3号のプラ鉢: 上の直径 約90、下 約65、高さ 約80） ---
    ("pot_top_d", 96.0, "鉢の穴の上の直径"),
    ("pot_bot_d", 74.0, "鉢の穴の底の直径"),
    ("cav_off", 19.5, "鉢の穴の中心を後ろへずらす量（前に電子部品の部屋を取るため）"),
    ("floor_t", 14.0, "底の厚さ（鉢の縁が本体の縁から沈みすぎないように上げる）"),
    ("rib_d", 8.0, "鉢を浮かせる突起の直径"),
    ("rib_h", 3.0, "鉢を浮かせる突起の高さ（受け皿の水から鉢底を離す）"),
    ("rib_r", 22.0, "突起を置く半径"),
    # --- 電子部品の部屋と顔（丸） ---
    ("comp_zc", 57.0, "電子部品と顔の位置の基準の高さ"),
    ("face_dz", -3.0, "丸い部屋・顔の中心の高さ（comp_zc から）"),
    ("comp_d", 78.0, "部屋の内側の直径"),
    ("comp_depth", 30.0, "顔の面から部屋の奥の壁までの深さ"),
    ("plate_d", 82.0, "顔のふたの直径"),
    ("plate_t", 2.0, "基準面から顔のふたの背面（本体のくぼみの底）まで"),
    ("plate_gap", 0.2, "顔のふたと本体のすき間（片側）"),
    ("notch_d", 10.0, "顔のふたを外すための指かけの直径（あごの下）"),
    # --- 顔のふたを留める磁石（Φ6×3 のネオジム磁石を 4 組。位置は MAG_POINTS） ---
    ("mag_d", 6.2, "磁石の穴の直径"),
    ("mag_t", 3.1, "磁石の穴の深さ"),
    ("mag_boss_d", 12.0, "本体側の磁石のボスの直径（部屋の壁につながる太さ）"),
    ("mag_boss_len", 5.5, "本体側の磁石のボスの長さ"),
    # --- 目（チャムさんの案の、黒く刷って顔のふたに貼る立体の目） ---
    ("eye_x", 22.0, "目の中心・横（顔の中心から）"),
    ("eye_dz", -3.0, "目の中心の高さ（comp_zc から）。口の端との間を 3mm 以上あける"),
    ("eye_w", 18.0, "目の幅"),
    ("eye_h", 22.0, "目の高さ"),
    ("eye_t", 5.5, "目の厚さ"),
    ("eye_round", 4.0, "目のふちの丸み"),
    ("eye_seat", 1.0, "目を貼る平らな座面の、ふたの表面からの深さ（目の中心で）"),
    ("eye_gap", 0.25, "目の座面のすき間（片側）"),
    # --- 口（にっこりした弧にならべた丸い穴。奥にふたの裏の音の部屋があり、Voice Base のスピーカーが当たる） ---
    ("mouth_w", 21.0, "口の幅（両端の穴の中心どうし）。穴がみな Voice Base の前に来る幅にする"),
    ("mouth_drop", 6.0, "口の両端から、いちばん下までの深さ（大きいほど笑う）"),
    ("mouth_dz", -19.3, "口の両端の高さ（comp_zc から）"),
    ("mouth_hole_d", 2.4, "口の穴の直径（奥の部品が見えにくい細さ）"),
    ("mouth_front", -8.5, "ふたの裏の音の部屋の前の面（基準面から。マイナスは前）"),
    # --- M5Stack Atomic Voice Base（24 × 24 × 10mm、上にピンが 4.1mm）。スピーカー・マイク・アンプ入り ---
    # スピーカーのスリットがある側面を前（口の裏）に、ピンを上に向けて、部屋の台に載せる
    ("vb_size", 24.0, "Voice Base の幅・奥行き"),
    ("vb_h", 10.0, "Voice Base の箱の高さ（ピンを除く）"),
    ("vb_z0", -28.0, "Voice Base の底の高さ（comp_zc から）。スピーカーの面を口の穴の奥に合わせる"),
    ("vb_gap", 0.3, "Voice Base の台のすき間（片側）"),
    # --- XIAO ESP32S3（Sense なし。21 × 17.8mm）。部屋の右、奥の壁の枠に差し込む。USB は下向き ---
    # ピンヘッダはふつうに（部品のない面へピンが出る向きに）付け、ピンを前に向ける。部品の面は奥の壁を向く
    ("xiao_cx", 22.5, "XIAO の中心・横"),
    ("xiao_z0", -20.0, "XIAO の下の端（USB の口）の高さ（comp_zc から）"),
    (
        "xiao_standoff",
        3.0,
        "奥の壁から基板の部品の面まで（部品とアンテナのコネクタの高さ。両面テープで貼る）",
    ),
    ("xiao_gap", 0.3, "XIAO の枠のすき間（片側）"),
    # --- 給電と書き込みの USB-C ケーブル（XIAO に直接挿し、底の溝から背面へ出す） ---
    ("plug_w", 12.5, "USB-C のプラグの根元（太いところ）の幅（ふつうのケーブルは 11〜12.5）"),
    ("plug_t", 7.5, "プラグの根元の厚さ"),
    ("plug_len", 20.0, "プラグの根元の長さ（これより長いとケーブルが溝へ曲がれない）"),
    ("usb_ch_w", 14.0, "プラグを通す縦の穴の幅"),
    ("usb_ch_d", 10.0, "プラグを通す縦の穴の奥行き"),
    ("cable_d", 4.5, "ケーブルの太さ"),
    ("groove_w", 7.0, "底の溝の幅"),
    ("groove_h", 7.0, "底の溝の深さ"),
    # --- 腕・足の差し込み ---
    ("peg_d", 6.0, "差し込みピンの直径"),
    ("peg_len", 7.0, "差し込みピンの長さ"),
    ("peg_hole_d", 6.3, "差し込み穴の直径"),
    ("peg_hole_depth", 8.0, "差し込み穴の深さ"),
    ("arm_z", 40.0, "腕のいちばん太いところの高さ（低めにして、ぶら下がった手に見せる）"),
    ("arm_r", 19.0, "腕の前後の半径"),
    ("arm_top", 32.0, "いちばん太いところから腕の上の端まで（上は細くとがる）"),
    ("arm_bot", 23.0, "いちばん太いところから腕の下の端まで"),
    ("arm_out", 34.0, "腕の取り付け面から外への出っ張り"),
    ("arm_x0", 66.0, "腕の取り付け面（本体の中心から）。本体の側面をここまで卵形に掘り下げる"),
    ("arm_gap", 0.25, "腕のくぼみのすき間（片側）"),
    ("foot_w", 30.0, "足の幅（正面から見たアーチの幅）"),
    ("foot_h", 24.0, "足の高さ（これより高いと、正面から見て顔のふたにかかる）"),
    ("foot_out", 14.0, "足のつま先を本体の表面から前へ出す量"),
    ("foot_toe", 8.0, "つま先の丸み"),
]

# 足の角度は正面（-Y）から測る。右が +。パラメータにすると Fusion の単位が混ざるので定数にする。
# これより正面に寄せると、足が顔のふたの前にかかってふたを外せなくなる
FOOT_ANGLE = 32.0
# 腕のピンの高さ（arm_z から、mm）。2 本にして腕が回らないようにする
ARM_PEGS_DZ = (-6.0, 10.0)
# 磁石の位置（丸い顔の中心から、mm）。スピーカーのリング・マイクのポケット・顔のくぼみの縁を避けた場所
MAG_POINTS = [(34.0, 0.0), (-34.0, 0.0), (24.0, -24.0), (-24.0, -24.0)]
# 口の穴の数。弧の上に等間隔にならべる（穴と穴のすき間が 1.5mm 以上になる数）
MOUTH_HOLES = 7
# 部品の決まった寸法（mm）。XIAO は基板 21 × 17.8 × 1.2、USB-C の受け口は幅 9 × 厚さ 3.2 × 長さ 7.3 で
# 基板の端から 1mm 出る。ピンヘッダは樹脂の厚さ 2.5、ジャンパー線のメスのコネクタは 2.54 角 × 長さ 14
XIAO_L, XIAO_W, XIAO_PCB = 21.0, 17.8, 1.2
USB_W, USB_T, USB_L, USB_OVER = 9.0, 3.2, 7.3, 1.0
HEADER_T, DUPONT_L = 2.5, 14.0
# Voice Base のピンの列（上の面の前と後ろのふちから 3mm 内側、左右の中央に 5 本と 4 本）
VB_PIN_INSET, VB_PINS = 3.0, (5, 4)
# 色（R, G, B）。チャムさんの案に合わせて、体はクリーム色、目は黒
BODY_RGB = (237, 230, 214)
EYE_RGB = (25, 25, 25)

_app: adsk.core.Application | None = None
_design: adsk.fusion.Design | None = None
_log: list[str] = []
_planes: dict = {}

H = adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
V = adsk.fusion.DimensionOrientations.VerticalDimensionOrientation
NEW = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
JOIN = adsk.fusion.FeatureOperations.JoinFeatureOperation
CUT = adsk.fusion.FeatureOperations.CutFeatureOperation
AXES = "xyz"


def log(msg: str) -> None:
    _log.append(msg)


def ev(expr: str) -> float:
    """式を評価して内部単位（cm）で返す。"""
    return _design.unitsManager.evaluateExpression(expr, "mm")


def vi(expr: str) -> adsk.core.ValueInput:
    return adsk.core.ValueInput.createByString(expr)


def mm(v: float) -> str:
    return f"{v} mm"


def world_point(coords: dict[str, float]) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(
        coords.get("x", 0.0), coords.get("y", 0.0), coords.get("z", 0.0)
    )


def plane(comp: adsk.fusion.Component, axis: str, expr: str) -> adsk.fusion.ConstructionPlane:
    """axis（x/y/z）の座標が expr になる平面。原点の平面からのオフセットで作る。"""
    key = (comp.entityToken, axis, expr)
    if key in _planes:
        return _planes[key]
    base = {
        "x": comp.yZConstructionPlane,
        "y": comp.xZConstructionPlane,
        "z": comp.xYConstructionPlane,
    }[axis]
    if abs(ev(expr)) < 1e-7:
        _planes[key] = base
        return base
    n = base.geometry.normal
    sign = {"x": n.x, "y": n.y, "z": n.z}[axis]
    inp = comp.constructionPlanes.createInput()
    inp.setByOffset(base, vi(expr if sign > 0 else f"-({expr})"))
    pl = comp.constructionPlanes.add(inp)
    pl.isLightBulbOn = False
    _planes[key] = pl
    return pl


class Sk:
    """ワールド座標の式で図形を描き、寸法に式を入れるスケッチ。"""

    def __init__(self, comp: adsk.fusion.Component, axis: str, expr: str, name: str):
        self.comp = comp
        self.fixed = axis
        self.fixed_val = ev(expr)
        self.sk = comp.sketches.add(plane(comp, axis, expr))
        self.sk.name = name
        self.sk.isComputeDeferred = True
        self.zero_lines: dict[str, adsk.fusion.SketchLine] = {}

    def sp(self, coords: dict[str, float]) -> adsk.core.Point3D:
        c = dict(coords)
        c[self.fixed] = self.fixed_val
        p = self.sk.modelToSketchSpace(world_point(c))
        return adsk.core.Point3D.create(p.x, p.y, 0)

    def orient(self, axis: str):
        a = self.sk.modelToSketchSpace(world_point({}))
        b = self.sk.modelToSketchSpace(world_point({axis: 1.0}))
        return H if abs(b.x - a.x) > 0.5 else V

    def zero_line(self, axis: str) -> adsk.fusion.SketchLine:
        """axis の座標が 0 の線（補助線）。"""
        if axis not in self.zero_lines:
            other = [a for a in AXES if a not in (axis, self.fixed)][0]
            ln = self.sk.sketchCurves.sketchLines.addByTwoPoints(
                self.sk.originPoint, self.sp({other: 1.0})
            )
            ln.isConstruction = True
            if self.orient(other) == H:
                self.sk.geometricConstraints.addHorizontal(ln)
            else:
                self.sk.geometricConstraints.addVertical(ln)
            self.zero_lines[axis] = ln
        return self.zero_lines[axis]

    def pin(self, spt: adsk.fusion.SketchPoint, axis: str, expr: str) -> None:
        """点の axis 座標を式で拘束する。"""
        val = ev(expr)
        if abs(val) < 1e-7:
            self.sk.geometricConstraints.addCoincident(spt, self.zero_line(axis))
            return
        g = spt.geometry
        text = adsk.core.Point3D.create(g.x + 0.3, g.y + 0.3, 0)
        try:
            d = self.sk.sketchDimensions.addDistanceDimension(
                self.sk.originPoint, spt, self.orient(axis), text
            )
        except RuntimeError as e:
            # 同じ座標の点があると Fusion が自動で整列拘束を付けるので、その軸は寸法なしで決まっている
            if "OVER_CONSTRAINTS" in str(e):
                log(f"NOTE {self.sk.name}: {axis} の寸法は自動の拘束で決まっているので省略")
                return
            raise
        d.parameter.expression = expr if val > 0 else f"-({expr})"

    def circle(self, center: dict[str, str], d_expr: str) -> None:
        cv = {a: ev(e) for a, e in center.items()}
        circ = self.sk.sketchCurves.sketchCircles.addByCenterRadius(self.sp(cv), ev(d_expr) / 2)
        g = circ.centerSketchPoint.geometry
        dd = self.sk.sketchDimensions.addDiameterDimension(
            circ, adsk.core.Point3D.create(g.x + 1, g.y + 1, 0)
        )
        dd.parameter.expression = d_expr
        for a, e in center.items():
            self.pin(circ.centerSketchPoint, a, e)

    def rect(self, center: dict[str, str], size: dict[str, str]) -> None:
        a1, a2 = list(center.keys())
        lo = {a: f"({center[a]}) - ({size[a]}) / 2" for a in (a1, a2)}
        hi = {a: f"({center[a]}) + ({size[a]}) / 2" for a in (a1, a2)}
        p_lo = self.sp({a: ev(lo[a]) for a in lo})
        p_hi = self.sp({a: ev(hi[a]) for a in hi})
        lines = self.sk.sketchCurves.sketchLines.addTwoPointRectangle(p_lo, p_hi)
        pts = [lines.item(i).startSketchPoint for i in range(lines.count)]

        def nearest(target):
            return min(pts, key=lambda s: s.geometry.distanceTo(target))

        c_lo, c_hi = nearest(p_lo), nearest(p_hi)
        for a in (a1, a2):
            self.pin(c_lo, a, lo[a])
            self.pin(c_hi, a, hi[a])

    def profiles(self) -> adsk.core.ObjectCollection:
        self.sk.isComputeDeferred = False
        coll = adsk.core.ObjectCollection.create()
        for p in self.sk.profiles:
            coll.add(p)
        self.sk.isVisible = False
        return coll

    def normal_along(self, axis: str) -> float:
        n = self.sk.xDirection.crossProduct(self.sk.yDirection)
        return {"x": n.x, "y": n.y, "z": n.z}[axis]


def extrude(s: Sk, dist: str, toward: str, op, bodies=None, name: str = "", profiles=None):
    """toward は '+y' のようにワールドの向きで指定する。profiles がなければスケッチの全部を使う。"""
    sign = 1 if toward[0] == "+" else -1
    feats = s.comp.features.extrudeFeatures
    inp = feats.createInput(profiles if profiles is not None else s.profiles(), op)
    direction = (
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if s.normal_along(toward[1]) * sign > 0
        else adsk.fusion.ExtentDirections.NegativeExtentDirection
    )
    inp.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(vi(dist)), direction)
    if bodies:
        inp.participantBodies = bodies
    f = feats.add(inp)
    if name:
        f.name = name
    return f


def fillet(comp: adsk.fusion.Component, edges, r: str, name: str = ""):
    coll = adsk.core.ObjectCollection.create()
    for e in edges:
        coll.add(e)
    if coll.count == 0:
        raise RuntimeError("丸める辺が見つからない")
    fi = comp.features.filletFeatures.createInput()
    try:
        fi.edgeSetInputs.addConstantRadiusEdgeSet(coll, vi(r), True)
    except AttributeError:
        fi.addConstantRadiusEdgeSet(coll, vi(r), True)
    f = comp.features.filletFeatures.add(fi)
    if name:
        f.name = name
    return f


def edges_along(faces, axis: str) -> list:
    """faces の辺のうち、axis 方向の直線の辺（ポケットや箱の角）。"""
    vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    found = {}
    for fc in faces:
        for e in fc.edges:
            g = e.geometry
            if isinstance(g, adsk.core.Line3D):
                v = g.startPoint.vectorTo(g.endPoint)
                if v.length < 1e-6:
                    continue
                v.normalize()
                if abs(v.x * vec[0] + v.y * vec[1] + v.z * vec[2]) > 0.999:
                    found[e.tempId] = e
    return list(found.values())


def step(name: str, fn) -> bool:
    try:
        fn()
        log(f"OK   {name}")
        return True
    except Exception:
        log(f"FAIL {name}\n{traceback.format_exc()}")
        return False


def new_component(root: adsk.fusion.Component, name: str, transform=None) -> adsk.fusion.Occurrence:
    occ = root.occurrences.addNewComponent(transform or adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ


# ---------------------------------------------------------------- 形の共通部品


def direction(angle_from_front: float) -> tuple[float, float]:
    """正面（-Y）から右回りに測った角度の、水平の向き。"""
    a = math.radians(angle_from_front)
    return math.sin(a), -math.cos(a)


def barrel_profile(s: Sk, parametric: bool) -> adsk.fusion.SketchLine:
    """本体の外形（回転させる半断面）を描き、回転の軸にする線を返す。"""
    pts = [
        ("r_bot", "0 mm"),
        ("r_low", "z_low"),
        ("r_belly", "z_belly"),
        ("r_sh", "z_sh"),
        ("r_top", "body_h"),
    ]
    fit = adsk.core.ObjectCollection.create()
    for r, z in pts:
        fit.add(s.sp({"x": ev(r), "z": ev(z)}))
    spline = s.sk.sketchCurves.sketchFittedSplines.add(fit)
    lines = s.sk.sketchCurves.sketchLines
    bottom = lines.addByTwoPoints(s.sk.originPoint, spline.startSketchPoint)
    top = lines.addByTwoPoints(spline.endSketchPoint, s.sp({"x": 0.0, "z": ev("body_h")}))
    axis = lines.addByTwoPoints(top.endSketchPoint, s.sk.originPoint)
    gc = s.sk.geometricConstraints
    for ln, horizontal in ((bottom, s.orient("x") == H), (top, s.orient("x") == H)):
        if horizontal:
            gc.addHorizontal(ln)
        else:
            gc.addVertical(ln)
    if s.orient("z") == V:
        gc.addVertical(axis)
    else:
        gc.addHorizontal(axis)
    if parametric:
        for (r, z), p in zip(pts, spline.fitPoints, strict=True):
            s.pin(p, "x", r)
            if z != "0 mm":
                s.pin(p, "z", z)
    return axis


def revolve(s: Sk, axis, op, name: str):
    feats = s.comp.features.revolveFeatures
    inp = feats.createInput(s.profiles().item(0), axis, op)
    inp.setAngleExtent(False, vi("360 deg"))
    f = feats.add(inp)
    f.name = name
    return f


def egg_profiles(
    s: Sk, h: str, c: dict[str, float], r: float, top: float, bot: float, half: bool
) -> tuple[adsk.core.ObjectCollection, adsk.fusion.SketchLine | None]:
    """上が細い卵形（上下で縦の半径が違う 2 つの楕円をつないだ形）の、内側のプロファイルを返す。

    値は cm。h は卵の横幅の向きの軸、c は中心。half のときは h の + 側の半分だけを返し、
    回転の軸にする縦の線も返す（回転させると卵の立体になる）。
    """
    ch, cz = c[h], c["z"]

    def p(hv: float, zv: float) -> adsk.core.Point3D:
        return s.sp({h: hv, "z": zv})

    curves = s.sk.sketchCurves
    for v in (top, -bot):
        # 楕円は長い軸の端を先に渡す
        if abs(v) >= r:
            curves.sketchEllipses.add(p(ch, cz), p(ch, cz + v), p(ch + r, cz))
        else:
            curves.sketchEllipses.add(p(ch, cz), p(ch + r, cz), p(ch, cz + v))
    curves.sketchLines.addByTwoPoints(p(ch if half else ch - r - 0.1, cz), p(ch + r + 0.1, cz))
    axis = None
    if half:
        tall = max(top, bot) + 0.1
        axis = curves.sketchLines.addByTwoPoints(p(ch, cz - tall), p(ch, cz + tall))
    s.sk.isComputeDeferred = False
    s.sk.isVisible = False
    # 2 つの楕円が重なるので、縦の半径が小さい側には「小さい楕円の内側」と「三日月形のすき間」ができる。
    # 小さい側は楕円の内側（面積が近いほう）だけ、大きい側は全部を使う
    small_up = top < bot
    want = math.pi * r * min(top, bot) / (4 if half else 2)
    big, small = [], []
    for pr in s.sk.profiles:
        ap = pr.areaProperties()
        m = s.sk.sketchToModelSpace(ap.centroid)
        mh, mz = {"x": m.x, "y": m.y, "z": m.z}[h], m.z
        if half and mh < ch + 1e-3:
            continue
        (small if (mz > cz) == small_up else big).append((abs(ap.area - want), pr))
    coll = adsk.core.ObjectCollection.create()
    for _, pr in big:
        coll.add(pr)
    if small:
        coll.add(min(small, key=lambda t: t[0])[1])
    return coll, axis


def circle_edges_at(body: adsk.fusion.BRepBody, z_cm: float) -> list:
    out = []
    for e in body.edges:
        g = e.geometry
        if isinstance(g, adsk.core.Circle3D) and abs(g.center.z - z_cm) < 1e-4:
            out.append(e)
    return out


def make_barrel(comp: adsk.fusion.Component, parametric: bool, name: str) -> adsk.fusion.BRepBody:
    s = Sk(comp, "y", "0 mm", name)
    axis = barrel_profile(s, parametric)
    body = revolve(s, axis, NEW, name).bodies.item(0)
    body.name = name
    edges = circle_edges_at(body, 0.0)
    fillet(
        comp, [e for e in edges if e.geometry.radius > 1.0], "body_fillet_bot", f"{name}_底の丸み"
    )
    edges = circle_edges_at(body, ev("body_h"))
    fillet(comp, edges, "body_fillet_top", f"{name}_上の丸み")
    return body


def surface_radius(body: adsk.fusion.BRepBody, angle: float, z_cm: float) -> float:
    """角度 angle・高さ z で、外から中心へ向かって本体の表面に当たる半径（cm）。"""
    dx, dy = direction(angle)
    inside = (
        adsk.fusion.PointContainment.PointInsidePointContainment,
        adsk.fusion.PointContainment.PointOnPointContainment,
    )

    def is_in(r):
        return body.pointContainment(adsk.core.Point3D.create(r * dx, r * dy, z_cm)) in inside

    hi = 12.0
    lo = hi
    while lo > 0 and not is_in(lo):
        lo -= 0.05
    for _ in range(30):
        mid = (lo + hi) / 2
        if is_in(mid):
            lo = mid
        else:
            hi = mid
    return lo


def radial_plane(comp, angle: float, r0: float) -> adsk.fusion.ConstructionPlane:
    """角度 angle の向き（水平）に垂直で、Z 軸から r0（cm）離れた平面。"""
    dx, dy = direction(angle)
    phi = math.degrees(math.atan2(dy, dx))
    planes = comp.constructionPlanes
    rot = None
    for a in (phi, -phi):
        inp = planes.createInput()
        inp.setByAngle(comp.zConstructionAxis, vi(f"{a} deg"), comp.yZConstructionPlane)
        pl = planes.add(inp)
        n = pl.geometry.normal
        if abs(n.x * dx + n.y * dy) > 0.999:
            rot = pl
            sign = 1 if n.x * dx + n.y * dy > 0 else -1
            break
        pl.deleteMe()
    if rot is None:
        raise RuntimeError("斜めの平面が作れない")
    rot.isLightBulbOn = False
    inp = planes.createInput()
    inp.setByOffset(rot, adsk.core.ValueInput.createByReal(sign * r0))
    pl = planes.add(inp)
    pl.isLightBulbOn = False
    return pl


def radial_cut(
    comp, body, angle: float, r0: float, z_cm: float, draw, dist: str, name: str
) -> None:
    """角度 angle の向きに垂直な平面（Z 軸から r0 cm）に draw(sk, 中心) で図形を描き、外へ dist だけ削る。

    外へ削ると表面の平らな座面に、平面を表面より奥に置いて外へ削ると表面からの穴になる。
    """
    dx, dy = direction(angle)
    sk = comp.sketches.add(radial_plane(comp, angle, r0))
    sk.name = name
    draw(sk, adsk.core.Point3D.create(r0 * dx, r0 * dy, z_cm))
    sk.isVisible = False
    feats = comp.features.extrudeFeatures
    inp = feats.createInput(sk.profiles.item(0), CUT)
    n = sk.xDirection.crossProduct(sk.yDirection)
    out = n.x * dx + n.y * dy > 0
    inp.setOneSideExtent(
        adsk.fusion.DistanceExtentDefinition.create(vi(dist)),
        adsk.fusion.ExtentDirections.PositiveExtentDirection
        if out
        else adsk.fusion.ExtentDirections.NegativeExtentDirection,
    )
    inp.participantBodies = [body]
    feats.add(inp).name = name


def radial_hole(comp, body, angle: float, z_cm: float, name: str) -> None:
    """本体の表面から中心へ向かう差し込み穴。"""
    r0 = surface_radius(body, angle, z_cm) - ev("peg_hole_depth")

    def draw(sk, center):
        c = sk.modelToSketchSpace(center)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(c.x, c.y, 0), ev("peg_hole_d") / 2
        )

    radial_cut(comp, body, angle, r0, z_cm, draw, "peg_hole_depth + 10 mm", name)


# ---------------------------------------------------------------- 本体


def build_body(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody | None:
    st = {}
    fc = "comp_zc + face_dz"

    def base():
        st["body"] = make_barrel(comp, True, "本体")

    step("本体の樽の形", base)
    body = st.get("body")
    if body is None:
        return None

    def cavity():
        s1 = Sk(comp, "z", "floor_t", "鉢の穴_底")
        s1.circle({"x": "0 mm", "y": "cav_off"}, "pot_bot_d")
        s2 = Sk(comp, "z", "body_h + 2 mm", "鉢の穴_上")
        s2.circle(
            {"x": "0 mm", "y": "cav_off"},
            "pot_top_d + (pot_top_d - pot_bot_d) * 2 mm / (body_h - floor_t)",
        )
        li = comp.features.loftFeatures.createInput(CUT)
        li.loftSections.add(s1.profiles().item(0))
        li.loftSections.add(s2.profiles().item(0))
        li.isSolid = True
        li.participantBodies = [body]
        comp.features.loftFeatures.add(li).name = "鉢の穴"

    step("鉢の穴", cavity)

    def ribs():
        s = Sk(comp, "z", "floor_t", "鉢を浮かせる突起")
        s.circle({"x": "0 mm", "y": "cav_off + rib_r"}, "rib_d")
        s.circle({"x": "-rib_r * 0.866", "y": "cav_off - rib_r / 2"}, "rib_d")
        s.circle({"x": "rib_r * 0.866", "y": "cav_off - rib_r / 2"}, "rib_d")
        extrude(s, "rib_h", "+z", JOIN, [body], "鉢を浮かせる突起")

    step("鉢を浮かせる突起", ribs)

    def compartment():
        # 平らに切り落とさず、曲面に丸い穴をあけて顔のふたを受ける
        s = Sk(comp, "y", "-face_y - 30 mm", "顔のふたの穴")
        s.circle({"x": "0 mm", "z": fc}, "plate_d + 2 * plate_gap")
        extrude(s, "30 mm + plate_t", "+y", CUT, [body], "顔のふたの穴")
        s2 = Sk(comp, "y", "-face_y + plate_t", "部屋")
        s2.circle({"x": "0 mm", "z": fc}, "comp_d")
        extrude(s2, "comp_depth - plate_t", "+y", CUT, [body], "部屋")
        s3 = Sk(comp, "y", "-face_y - 30 mm", "指かけ")
        s3.circle({"x": "0 mm", "z": f"{fc} - plate_d / 2"}, "notch_d")
        extrude(s3, "30 mm + plate_t", "+y", CUT, [body], "指かけ")

    step("電子部品の部屋と顔のふたの穴", compartment)

    def magnets():
        pts = [(f"{x} mm", f"face_dz + {z} mm") for x, z in MAG_POINTS]
        y0 = "-face_y + plate_t"
        s = Sk(comp, "y", y0, "磁石のボス")
        for x, z in pts:
            s.circle({"x": x, "z": f"comp_zc + {z}"}, "mag_boss_d")
        extrude(s, "mag_boss_len", "+y", JOIN, [body], "磁石のボス")
        s2 = Sk(comp, "y", y0, "磁石の穴")
        for x, z in pts:
            s2.circle({"x": x, "z": f"comp_zc + {z}"}, "mag_d")
        extrude(s2, "mag_t", "+y", CUT, [body], "磁石の穴")

    step("磁石のボス", magnets)

    back = "-face_y + comp_depth"
    vb_front = "-face_y + plate_t + 0.2 mm"
    vb_bot = "comp_zc + vb_z0"

    def vb_stand():
        # 部屋の底から台を立てて Voice Base を載せる。前はふたの裏に 0.2mm 手前まで、後ろは奥の壁まで
        s = Sk(comp, "z", vb_bot, "Voice Baseの台")
        s.rect(
            {"x": "0 mm", "y": f"(({vb_front}) + ({back})) / 2"},
            {"x": "vb_size + 2 * vb_gap + 2.4 mm", "y": "comp_depth - plate_t - 0.2 mm"},
        )
        extrude(s, "20 mm", "-z", JOIN, [body], "Voice Baseの台")
        # 左右の縁と後ろの止めで位置を決める。前はふたが押さえる
        s2 = Sk(comp, "z", vb_bot, "Voice Baseの縁")
        for sx in ("-", ""):
            s2.rect(
                {
                    "x": f"{sx}(vb_size / 2 + vb_gap + 0.6 mm)",
                    "y": f"(({vb_front}) + ({back})) / 2",
                },
                {"x": "1.2 mm", "y": "comp_depth - plate_t - 0.2 mm"},
            )
        stop_y0 = f"({vb_front}) + vb_size + vb_gap"
        s2.rect(
            {"x": "0 mm", "y": f"(({stop_y0}) + ({back})) / 2"},
            {"x": "12 mm", "y": f"({back}) - ({stop_y0})"},
        )
        extrude(s2, "4 mm", "+z", JOIN, [body], "Voice Baseの縁")

    step("Voice Base の台", vb_stand)

    z0 = "comp_zc + xiao_z0"
    half = f"{XIAO_W / 2} mm"

    def xiao_frame():
        # 奥の壁から生やした枠に、XIAO を部品の面を奥にして差し込む（USB は下）。下は USB のプラグを
        # 通すので、角だけの小さな棚にする。前へ抜けないように、部品の面と壁の間に両面テープを貼る
        s = Sk(comp, "y", back, "XIAOの枠")
        h_all = f"{XIAO_L} mm + 2 * xiao_gap + 2.4 mm"
        zc = f"{z0} + {XIAO_L / 2} mm"
        for sx in ("-", "+"):
            s.rect(
                {"x": f"xiao_cx {sx} ({half} + xiao_gap + 0.6 mm)", "z": zc},
                {"x": "1.2 mm", "z": h_all},
            )
        s.rect(
            {"x": "xiao_cx", "z": f"{z0} + {XIAO_L} mm + xiao_gap + 0.6 mm"},
            {"x": f"2 * ({half} + xiao_gap + 1.2 mm)", "z": "1.2 mm"},
        )
        ledge = f"({half} + xiao_gap + 1.2 mm - plug_w / 2 - 0.5 mm)"
        for sx in ("-", "+"):
            s.rect(
                {
                    "x": f"xiao_cx {sx} (plug_w / 2 + 0.5 mm + {ledge} / 2)",
                    "z": f"{z0} - xiao_gap - 0.6 mm",
                },
                {"x": ledge, "z": "1.2 mm"},
            )
        extrude(s, f"xiao_standoff + {XIAO_PCB + 1.0} mm", "-y", JOIN, [body], "XIAOの枠")

    step("XIAO の枠", xiao_frame)

    def usb_cable():
        # プラグは底から縦の穴に通して XIAO に挿す。穴は USB の受け口の高さまで掘り、受け口と
        # プラグの根元が奥の壁に当たらないようにする。ケーブルは底の溝を通って背面へ出る
        yc = usb_center_y()
        s = Sk(comp, "z", "-1 mm", "USBケーブルの縦の穴")
        s.rect({"x": "xiao_cx", "y": yc}, {"x": "usb_ch_w", "y": "usb_ch_d"})
        extrude(
            s, f"comp_zc + xiao_z0 + {USB_L + 2.0} mm", "+z", CUT, [body], "USBケーブルの縦の穴"
        )
        s2 = Sk(comp, "z", "-1 mm", "USBケーブルの底の溝")
        s2.rect(
            {"x": "xiao_cx", "y": f"(({yc}) + 80 mm) / 2"},
            {"x": "groove_w", "y": f"80 mm - ({yc})"},
        )
        extrude(s2, "groove_h + 1 mm", "+z", CUT, [body], "USBケーブルの底の溝")

    step("USB ケーブルの縦の穴と底の溝", usb_cable)
    return body


def usb_center_y() -> str:
    """XIAO の USB の受け口の中心の前後の位置（式）。受け口は部品の面から奥の壁の側へ出ている。"""
    return f"-face_y + comp_depth - xiao_standoff + {USB_T / 2} mm"


def arm_egg(grow: float) -> tuple[float, float, float]:
    """腕の取り付け面の卵形の (前後の半径, 上, 下)。grow だけ外へ広げる（cm）。"""
    return ev("arm_r") + grow, ev("arm_top") + grow, ev("arm_bot") + grow


def build_peg_holes(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> None:
    """腕のくぼみとピンの穴、足の差し込み穴。"""
    for sign, out, label in (("", "+x", "右"), ("-", "-x", "左")):
        inward = "-x" if out == "+x" else "+x"

        def seat(sign=sign, out=out, inward=inward, label=label):
            # 腕は真横に付けるので、取り付け面は X 軸に垂直な平面になる
            s = Sk(comp, "x", f"{sign}arm_x0", f"腕のくぼみ_{label}")
            coll, _ = egg_profiles(
                s, "y", {"y": 0.0, "z": ev("arm_z")}, *arm_egg(ev("arm_gap")), half=False
            )
            extrude(s, "20 mm", out, CUT, [body], f"腕のくぼみ_{label}", coll)
            s2 = Sk(comp, "x", f"{sign}arm_x0", f"腕の穴_{label}")
            for dz in ARM_PEGS_DZ:
                s2.circle({"y": "0 mm", "z": f"arm_z + ({dz} mm)"}, "peg_hole_d")
            extrude(s2, "peg_hole_depth", inward, CUT, [body], f"腕の穴_{label}")

        step(f"腕のくぼみ_{label}", seat)
    for a, label in ((FOOT_ANGLE, "右"), (-FOOT_ANGLE, "左")):
        step(
            f"足の穴_{label}",
            lambda a=a, label=label: radial_hole(
                comp, body, a, ev("foot_h / 2"), f"足の穴_{label}"
            ),
        )


# ---------------------------------------------------------------- 顔のふた


def build_plate(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody | None:
    """顔のふた。本体と同じ樽の曲面を丸く切り出したもので、はめると継ぎ目の円しか見えない。

    電子部品は付けない。表には目を貼る座面（build_eye_seats）と、口の穴だけが出る。
    """
    st = {}
    back = "-face_y + plate_t"

    def base():
        s = Sk(comp, "y", "-face_y - 30 mm", "顔のふた_外形")
        s.circle({"x": "0 mm", "z": "comp_zc + face_dz"}, "plate_d")
        cyl = extrude(s, "30 mm + plate_t", "+y", NEW, name="顔のふた_外形").bodies.item(0)
        barrel = make_barrel(comp, False, "顔のふた_削り型")
        tools = adsk.core.ObjectCollection.create()
        tools.add(barrel)
        inp = comp.features.combineFeatures.createInput(cyl, tools)
        inp.operation = adsk.fusion.FeatureOperations.IntersectFeatureOperation
        inp.isKeepToolBodies = False
        comp.features.combineFeatures.add(inp).name = "顔のふた_本体の曲面で切る"
        cyl.name = "顔のふた"
        st["body"] = cyl

    step("顔のふた", base)
    body = st.get("body")
    if body is None:
        return None

    # 口の奥には、ふたの裏から浅い音の部屋を掘る。部屋の真後ろに Voice Base のスピーカーの面が当たり、
    # 音は部屋から口の穴を通って外へ出る。部屋は Voice Base より左右に広く、はみ出したところから
    # 本体の部屋（Voice Base のマイクがある）へも外の音が入る
    def chamber():
        s = Sk(comp, "y", back, "口の奥の音の部屋")
        s.rect(
            {"x": "0 mm", "z": "comp_zc + mouth_dz - mouth_drop / 2"},
            {"x": "mouth_w + mouth_hole_d + 4.8 mm", "z": "mouth_drop + mouth_hole_d + 1.2 mm"},
        )
        extrude(s, "plate_t - mouth_front", "-y", CUT, [body], "口の奥の音の部屋")

    step("口の奥の音の部屋", chamber)

    def mouth():
        s = Sk(comp, "y", "-face_y + mouth_front + 0.5 mm", "口")
        for x, z in mouth_holes():
            s.circle({"x": f"{x * 10:.4f} mm", "z": f"{z * 10:.4f} mm"}, "mouth_hole_d")
        extrude(s, "20 mm", "-y", CUT, [body], "口")
        pts = mouth_holes()
        gap = math.dist(pts[0], pts[1]) * 10 - ev("mouth_hole_d") * 10
        log(f"MOUTH 口の穴 {len(pts)} 個、穴と穴のすき間 {gap:.2f} mm")

    step("口", mouth)

    def back_holes():
        s = Sk(comp, "y", back, "磁石の穴")
        for x, z in MAG_POINTS:
            s.circle({"x": f"{x} mm", "z": f"comp_zc + face_dz + {z} mm"}, "mag_d")
        extrude(s, "mag_t", "-y", CUT, [body], "磁石の穴")

    step("磁石の穴", back_holes)
    return body


def build_eye_seats(comp: adsk.fusion.Component, cap: adsk.fusion.BRepBody) -> list:
    """顔のふたの表に、目を貼る平らな楕円の座面を 2 つ掘る。目を置く場所（座面の中心と向き）を返す。

    ふたは曲面なので、座面は目の中心での表面の向きに垂直な平面にする（目が顔の丸みに沿って少し外を向く）。
    """
    z_e = ev("comp_zc + eye_dz")
    # 本体は回転体なので、表面までの半径は高さだけで決まる
    r_s = surface_radius(cap, 0.0, z_e)
    r0 = r_s - ev("eye_seat")
    half_w, half_h = ev("eye_w / 2 + eye_gap"), ev("eye_h / 2 + eye_gap")
    spots = []
    for sign, label in ((1, "右"), (-1, "左")):
        angle = math.degrees(math.asin(sign * ev("eye_x") / r_s))
        dx, dy = direction(angle)

        def draw(sk, c, dx=dx, dy=dy):
            def sp(p):
                q = sk.modelToSketchSpace(p)
                return adsk.core.Point3D.create(q.x, q.y, 0)

            top = adsk.core.Point3D.create(c.x, c.y, c.z + half_h)
            side = adsk.core.Point3D.create(c.x - dy * half_w, c.y + dx * half_w, c.z)
            sk.sketchCurves.sketchEllipses.add(sp(c), sp(top), sp(side))

        step(
            f"目の座面_{label}",
            lambda angle=angle, draw=draw, label=label: radial_cut(
                comp, cap, angle, r0, z_e, draw, "15 mm", f"目の座面_{label}"
            ),
        )
        spots.append((adsk.core.Point3D.create(r0 * dx, r0 * dy, z_e), dx, dy))
    return spots


def eye_transform(origin: adsk.core.Point3D, dx: float, dy: float) -> adsk.core.Matrix3D:
    """目の部品（平らな面が XY 平面、+Z が表）を、座面の上に表を外へ向けて置く変換。"""
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(
        origin,
        adsk.core.Vector3D.create(-dy, dx, 0),
        adsk.core.Vector3D.create(0, 0, 1),
        adsk.core.Vector3D.create(dx, dy, 0),
    )
    return m


def build_eye(comp: adsk.fusion.Component) -> None:
    """楕円の立体の目。平らな面を下にして黒で刷り、顔のふたの座面に貼る。"""
    st = {}

    def base():
        sk = comp.sketches.add(comp.xYConstructionPlane)
        sk.name = "目"
        sk.sketchCurves.sketchEllipses.add(
            adsk.core.Point3D.create(0, 0, 0),
            adsk.core.Point3D.create(0, ev("eye_h / 2"), 0),
            adsk.core.Point3D.create(ev("eye_w / 2"), 0, 0),
        )
        sk.isVisible = False
        feats = comp.features.extrudeFeatures
        inp = feats.createInput(sk.profiles.item(0), NEW)
        inp.setDistanceExtent(False, vi("eye_t"))
        f = feats.add(inp)
        f.name = "目"
        st["body"] = f.bodies.item(0)
        st["body"].name = "目"
        st["top"] = f.endFaces.item(0)

    step("目", base)
    if "body" not in st:
        return
    for r in ("eye_round", "eye_round * 0.6"):
        if step(
            f"目のふちの丸み {r}", lambda r=r: fillet(comp, list(st["top"].edges), r, "目の丸み")
        ):
            break


def front_wall(body: adsk.fusion.BRepBody, x_cm: float, z_cm: float, y_start: float) -> float:
    """(x, z) で、y_start から前（-Y）へ進んで材料が終わるまでの厚さ（mm）。"""
    inside = (
        adsk.fusion.PointContainment.PointInsidePointContainment,
        adsk.fusion.PointContainment.PointOnPointContainment,
    )
    y = y_start - 0.001
    while body.pointContainment(adsk.core.Point3D.create(x_cm, y, z_cm)) in inside:
        y -= 0.002
    return (y_start - y) * 10


def mouth_geometry() -> tuple[float, float, float]:
    """口の穴をならべる円弧の、円の中心の高さ・半径・両端の角度（真下から）。長さは cm。"""
    half, drop = ev("mouth_w") / 2, ev("mouth_drop")
    r = (half**2 + drop**2) / (2 * drop)
    zc = ev("comp_zc + mouth_dz") - drop + r
    return zc, r, math.asin(half / r)


def mouth_holes() -> list[tuple[float, float]]:
    """口の穴の中心 (x, z)（cm）。円弧の上に等間隔にならべる。"""
    zc, r, phi0 = mouth_geometry()
    out = []
    for k in range(MOUTH_HOLES):
        ph = -phi0 + 2 * phi0 * k / (MOUTH_HOLES - 1)
        out.append((r * math.sin(ph), zc - r * math.cos(ph)))
    return out


def check_front_walls(cap: adsk.fusion.BRepBody) -> None:
    """口の奥の音の部屋の前に残るふたの厚さを、穴にかからない点で測ってログに書く。"""
    y0 = ev("-face_y + mouth_front")
    half_x = ev("mouth_w + mouth_hole_d + 4.8 mm") / 2
    zc = ev("comp_zc + mouth_dz - mouth_drop / 2")
    half_z = ev("mouth_drop + mouth_hole_d + 1.2 mm") / 2
    holes = mouth_holes()
    keep = ev("mouth_hole_d") / 2 + 0.03
    vals = []
    for i in range(13):
        for j in range(5):
            # 部屋の縁から 0.3mm 内側までの格子
            x = -half_x + 0.03 + (2 * half_x - 0.06) * i / 12
            z = zc - half_z + 0.03 + (2 * half_z - 0.06) * j / 4
            if all(math.hypot(x - hx, z - hz) > keep for hx, hz in holes):
                vals.append(front_wall(cap, x, z, y0))
    log(f"WALL 口の奥の部屋の前: 最小 {min(vals):.2f} mm / 最大 {max(vals):.2f} mm")


# ---------------------------------------------------------------- 腕・足
# 部品は本体の中心を原点にして +X 方向に作り、Z 軸まわりに回して置く。
# 足の根元は本体と同じ樽の形で削って、本体の曲面にぴったり沿わせる。
# 腕は平らな取り付け面を、本体の側面に掘った卵形のくぼみにはめる。


def fit_to_barrel(comp: adsk.fusion.Component, part: adsk.fusion.BRepBody, name: str) -> None:
    barrel = make_barrel(comp, False, f"{name}_削り型")
    tools = adsk.core.ObjectCollection.create()
    tools.add(barrel)
    inp = comp.features.combineFeatures.createInput(part, tools)
    inp.operation = CUT
    inp.isKeepToolBodies = False
    comp.features.combineFeatures.add(inp).name = f"{name}_根元を本体に沿わせる"


def add_peg(
    comp: adsk.fusion.Component, part: adsk.fusion.BRepBody, r_s: float, z_cm: float, name: str
) -> None:
    s = Sk(comp, "x", f"{r_s * 10 + 3} mm", f"{name}のピン")
    s.circle({"y": "0 mm", "z": f"{z_cm * 10} mm"}, "peg_d")
    extrude(s, "peg_len + 3 mm", "-x", JOIN, [part], f"{name}のピン")


def build_arm(comp: adsk.fusion.Component) -> None:
    """下がふっくらした卵形の腕。卵の立体を外へ引き伸ばし、取り付け面から外の半分だけを使う。

    右の腕として +X に作る（左はこの部品を Z 軸まわりに 180° 回して置く）。取り付け面は平らなので、
    印刷は取り付け面を下にすればサポートがいらない。
    """
    st = {}
    x0, z0 = ev("arm_x0"), ev("arm_z")
    r = ev("arm_r")

    def base():
        s = Sk(comp, "y", "0 mm", "腕")
        coll, axis = egg_profiles(s, "x", {"x": x0, "z": z0}, *arm_egg(0.0), half=True)
        feats = comp.features.revolveFeatures
        inp = feats.createInput(coll, axis, NEW)
        inp.setAngleExtent(False, vi("360 deg"))
        f = feats.add(inp)
        f.name = "腕_卵"
        egg = f.bodies.item(0)
        # 回転体は前後と外向きの半径が同じなので、外向きだけ arm_out まで伸ばす
        ents = adsk.core.ObjectCollection.create()
        ents.add(egg)
        center = s.sk.sketchPoints.add(s.sp({"x": x0, "z": z0}))
        si = comp.features.scaleFeatures.createInput(ents, center, vi("1"))
        si.setToNonUniform(adsk.core.ValueInput.createByReal(ev("arm_out") / r), vi("1"), vi("1"))
        comp.features.scaleFeatures.add(si).name = "腕_外へ伸ばす"
        egg = comp.bRepBodies.item(0)
        s2 = Sk(comp, "x", "arm_x0", "腕の取り付け面")
        s2.rect({"y": "0 mm", "z": "arm_z"}, {"y": "arm_r * 3", "z": "(arm_top + arm_bot) * 3"})
        extrude(s2, "arm_out + 5 mm", "-x", CUT, [egg], "腕の取り付け面")
        egg.name = "腕"
        st["body"] = egg

    step("腕", base)
    if "body" not in st:
        return

    def pegs():
        s = Sk(comp, "x", "arm_x0", "腕のピン")
        for dz in ARM_PEGS_DZ:
            s.circle({"y": "0 mm", "z": f"arm_z + ({dz} mm)"}, "peg_d")
        extrude(s, "peg_len", "-x", JOIN, [st["body"]], "腕のピン")

    step("腕のピン", pegs)


def build_foot(comp: adsk.fusion.Component, r_s: float) -> None:
    """正面から見てアーチ形の、背の高い足。つま先を丸くして、底は平らにして本体と一緒に机に立つ。"""
    st = {}
    zc = ev("foot_h / 2")
    # 根元は本体の中まで伸ばしておき、あとで本体の曲面で削る
    x_in, x_out = (r_s - 1.2) * 10, (r_s + ev("foot_out")) * 10

    def base():
        s = Sk(comp, "z", "0 mm", "足")
        s.rect(
            {"x": f"{(x_in + x_out) / 2} mm", "y": "0 mm"},
            {"x": f"{x_out - x_in} mm", "y": "foot_w"},
        )
        f = extrude(s, "foot_h", "+z", NEW, name="足")
        st["body"] = f.bodies.item(0)
        st["body"].name = "足"
        fillet(comp, edges_along([f.endFaces.item(0)], "x"), "foot_w / 2 - 0.6 mm", "足のアーチ")

    step("足", base)
    if "body" not in st:
        return

    def toe(r: str) -> None:
        body = st["body"]
        tip = max(
            (fc for fc in body.faces if isinstance(fc.geometry, adsk.core.Plane)),
            key=lambda fc: fc.pointOnFace.x,
        )
        edges = [e for e in tip.edges if e.boundingBox.maxPoint.z > 1e-4]
        fillet(comp, edges, r, "足のつま先")

    for r in ("foot_toe", "foot_toe * 0.6"):
        if step(f"足のつま先 {r}", lambda r=r: toe(r)):
            break
    step("足の根元", lambda: fit_to_barrel(comp, st["body"], "足"))
    step("足のピン", lambda: add_peg(comp, st["body"], r_s, zc, "足"))


def rotation_for(angle: float) -> adsk.core.Matrix3D:
    m = adsk.core.Matrix3D.create()
    m.setToRotation(
        math.radians(angle - 90.0),
        adsk.core.Vector3D.create(0, 0, 1),
        adsk.core.Point3D.create(0, 0, 0),
    )
    return m


# ---------------------------------------------------------------- 参照部品（印刷しない）


# 組み立てたときの部品の外形を箱で置き、干渉チェックで本体・ふたにぶつからないかを見る。
# ジャンパー線のコネクタと USB-C のプラグも入れて、線を挿す場所まで確かめる。


def all_bodies(occ: adsk.fusion.Occurrence) -> list:
    out = list(occ.bRepBodies)
    for child in occ.childOccurrences:
        out += all_bodies(child)
    return out


def ref_box(comp, axis: str, at: str, center: dict, size: dict, dist: str, toward: str, name: str):
    s = Sk(comp, axis, at, name)
    s.rect(center, size)
    extrude(s, dist, toward, NEW, name=name).bodies.item(0).name = name


def build_refs(comp: adsk.fusion.Component) -> None:
    vb_front = "-face_y + plate_t + 0.2 mm"
    vb_bot = "comp_zc + vb_z0"
    wall = "-face_y + comp_depth"
    z0 = "comp_zc + xiao_z0"
    parts = []

    # Voice Base の箱と、上のピンに挿したジャンパー線のコネクタ（前と後ろの 2 列）
    parts.append(
        lambda: ref_box(
            comp,
            "y",
            vb_front,
            {"x": "0 mm", "z": f"{vb_bot} + vb_h / 2"},
            {"x": "vb_size", "z": "vb_h"},
            "vb_size",
            "+y",
            "参照_Voice Base",
        )
    )
    for n, y in zip(
        VB_PINS,
        (f"{vb_front} + {VB_PIN_INSET} mm", f"{vb_front} + vb_size - {VB_PIN_INSET} mm"),
        strict=True,
    ):
        parts.append(
            lambda n=n, y=y: ref_box(
                comp,
                "z",
                f"{vb_bot} + vb_h",
                {"x": "0 mm", "y": y},
                {"x": f"{n * 2.54} mm", "y": "2.54 mm"},
                f"{DUPONT_L} mm",
                "+z",
                "参照_Voice Baseのコネクタ",
            )
        )
    # XIAO: 基板（前へ）、シールドと USB の受け口（奥の壁の側へ）
    comp_face = f"{wall} - xiao_standoff"
    parts.append(
        lambda: ref_box(
            comp,
            "y",
            comp_face,
            {"x": "xiao_cx", "z": f"{z0} + {XIAO_L / 2} mm"},
            {"x": f"{XIAO_W} mm", "z": f"{XIAO_L} mm"},
            f"{XIAO_PCB} mm",
            "-y",
            "参照_XIAO",
        )
    )
    parts.append(
        lambda: ref_box(
            comp,
            "y",
            comp_face,
            {"x": "xiao_cx", "z": f"{z0} + 14 mm"},
            {"x": "13 mm", "z": "12 mm"},
            "2.3 mm",
            "+y",
            "参照_XIAOのシールド",
        )
    )
    parts.append(
        lambda: ref_box(
            comp,
            "y",
            comp_face,
            {"x": "xiao_cx", "z": f"{z0} + {USB_L / 2 - USB_OVER} mm"},
            {"x": f"{USB_W} mm", "z": f"{USB_L} mm"},
            f"{USB_T} mm",
            "+y",
            "参照_XIAOのUSB",
        )
    )
    # XIAO のピンヘッダとジャンパー線のコネクタ（両側に 7 本ずつ、列の間隔 15.24mm）
    for sx in ("-", "+"):
        parts.append(
            lambda sx=sx: ref_box(
                comp,
                "y",
                f"{comp_face} - {XIAO_PCB} mm",
                {"x": f"xiao_cx {sx} 7.62 mm", "z": f"{z0} + {XIAO_L / 2} mm"},
                {"x": "2.54 mm", "z": f"{7 * 2.54} mm"},
                f"{HEADER_T + DUPONT_L} mm",
                "-y",
                "参照_XIAOのコネクタ",
            )
        )
    # USB-C のプラグの根元と、ケーブル（縦の部分と底の溝の部分。曲がるところは描かない）
    yc = usb_center_y()
    plug_bot = f"{z0} - {USB_OVER} mm - plug_len"
    parts.append(
        lambda: ref_box(
            comp,
            "z",
            f"{z0} - {USB_OVER} mm",
            {"x": "xiao_cx", "y": yc},
            {"x": "plug_w", "y": "plug_t"},
            "plug_len",
            "-z",
            "参照_USBプラグ",
        )
    )

    def cable():
        s = Sk(comp, "z", plug_bot, "参照_ケーブル_縦")
        s.circle({"x": "xiao_cx", "y": yc}, "cable_d")
        extrude(
            s, f"({plug_bot}) - groove_h / 2 - 5 mm", "-z", NEW, name="参照_ケーブル"
        ).bodies.item(0).name = "参照_ケーブル"
        s2 = Sk(comp, "y", f"{yc} + 5 mm", "参照_ケーブル_溝")
        s2.circle({"x": "xiao_cx", "z": "groove_h / 2"}, "cable_d")
        extrude(s2, f"75 mm - ({yc})", "+y", NEW, name="参照_ケーブル").bodies.item(
            0
        ).name = "参照_ケーブル"

    parts.append(cable)
    for k, fn in enumerate(parts):
        step(f"参照部品 {k + 1}", fn)


# ---------------------------------------------------------------- 確認と書き出し


def body_label(b) -> str:
    ctx = b.assemblyContext
    return (ctx.fullPathName if ctx else b.parentComponent.name) + " / " + b.name


def check_interference(
    printable: list[adsk.fusion.Occurrence], refs: list[adsk.fusion.Occurrence]
) -> None:
    """印刷する部品どうし、印刷する部品と参照部品の干渉を調べる（参照部品どうしは見ない）。"""
    coll = adsk.core.ObjectCollection.create()
    for occ in printable + refs:
        for b in all_bodies(occ):
            coll.add(b)
    inp = _design.createInterferenceInput(coll)
    inp.areCoincidentFacesIncluded = True
    res = _design.analyzeInterference(inp)
    names = tuple(o.fullPathName for o in printable)
    comps = {o.component.name for o in printable}

    def is_printable(b) -> bool:
        ctx = b.assemblyContext
        return ctx.fullPathName.startswith(names) if ctx else b.parentComponent.name in comps

    hits = 0
    for r in res:
        l1, l2 = body_label(r.entityOne), body_label(r.entityTwo)
        if not (is_printable(r.entityOne) or is_printable(r.entityTwo)):
            continue
        vol = r.interferenceBody.volume * 1000 if r.interferenceBody else 0.0
        if vol < 0.01:
            continue
        hits += 1
        log(f"INTERFERENCE {l1}  x  {l2} : {vol:.2f} mm3")
    log(
        f"INTERFERENCE 件数 {hits}（全体 {res.count} 組のうち、印刷部品が関係して 0.01mm3 以上のもの）"
    )


def bbox_mm(b: adsk.fusion.BRepBody) -> str:
    x = b.boundingBox
    return f"X {x.minPoint.x * 10:.1f}..{x.maxPoint.x * 10:.1f}  Y {x.minPoint.y * 10:.1f}..{x.maxPoint.y * 10:.1f}  Z {x.minPoint.z * 10:.1f}..{x.maxPoint.z * 10:.1f}"


def plastic(name: str, rgb: tuple[int, int, int]) -> adsk.core.Appearance:
    """つや消しプラスチックの外観を、色を変えてデザインに入れる。

    Fusion の表示の言語で外観ライブラリの名前が変わるので、日本語と英語の両方で探す。
    """
    have = _design.appearances.itemByName(name)
    if have:
        return have
    base = None
    for lib_name, app_name in (
        ("Fusion 外観ライブラリ", "プラスチック - マット(白)"),
        ("Fusion Appearance Library", "Plastic - Matte (White)"),
    ):
        lib = _app.materialLibraries.itemByName(lib_name)
        if lib:
            base = lib.appearances.itemByName(app_name)
            if base:
                break
    if base is None:
        raise RuntimeError("つや消しプラスチックの外観が見つからない")
    a = _design.appearances.addByCopy(base, name)
    for prop in a.appearanceProperties:
        if isinstance(prop, adsk.core.ColorProperty) and prop.value is not None:
            prop.value = adsk.core.Color.create(*rgb, 255)
    return a


def apply_colors(parts: list[adsk.fusion.Occurrence], eyes: list[adsk.fusion.Occurrence]) -> None:
    """印刷する部品をクリーム色、目を黒にする（画像で、刷ったときの見た目に近づけるため）。"""
    body_look, eye_look = plastic("ふたまる_クリーム", BODY_RGB), plastic("ふたまる_黒", EYE_RGB)
    for occ in parts:
        look = eye_look if occ in eyes else body_look
        for b in occ.bRepBodies:
            b.appearance = look
            # 削り型から移った面ごとの外観が残ると、ふただけ色が違って見える
            for f in b.faces:
                if (
                    f.appearanceSourceType
                    == adsk.core.AppearanceSourceTypes.OverrideAppearanceSource
                ):
                    f.appearance = None


def save_view(name: str, eye, target, up=(0, 0, 1)) -> None:
    vp = _app.activeViewport
    cam = vp.camera
    cam.isSmoothTransition = False
    cam.eye = adsk.core.Point3D.create(*eye)
    cam.target = adsk.core.Point3D.create(*target)
    cam.upVector = adsk.core.Vector3D.create(*up)
    vp.camera = cam
    vp.fit()
    adsk.doEvents()
    vp.refresh()
    vp.saveAsImageFile(os.path.join(OUT_DIR, f"{name}.png"), 1600, 1200)


def run(context) -> None:
    global _app, _design
    _log.clear()
    _planes.clear()
    try:
        _app = adsk.core.Application.get()
        os.makedirs(OUT_DIR, exist_ok=True)
        _app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        _design = adsk.fusion.Design.cast(_app.activeProduct)
        _design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = _design.rootComponent
        for name, value, comment in PARAMS:
            _design.userParameters.add(name, vi(mm(value)), "mm", comment)

        body_occ = new_component(root, "本体")
        plate_occ = new_component(root, "顔のふた")
        ref_occ = new_component(root, "参照部品（印刷しない）")
        body = build_body(body_occ.component)
        cap = build_plate(plate_occ.component)
        build_refs(ref_occ.component)

        # 目は右の座面に置いて作り、左は同じ部品を左の座面に置く
        eye_occs = []
        if cap is not None:
            spots = build_eye_seats(plate_occ.component, cap)
            eye_occs.append(new_component(root, "目", eye_transform(*spots[0])))
            build_eye(eye_occs[0].component)
            eye_occs.append(
                root.occurrences.addExistingComponent(
                    eye_occs[0].component, eye_transform(*spots[1])
                )
            )

        # 足の根元の位置は、本体の表面を測ってから決める（差し込み穴より先に測る）
        r_arm = surface_radius(body, 90.0, ev("arm_z"))
        r_foot = surface_radius(body, FOOT_ANGLE, ev("foot_h / 2"))
        log(
            f"SURFACE 腕の高さの半径 {r_arm * 10:.1f} mm（くぼみの深さ {(r_arm - ev('arm_x0')) * 10:.1f} mm）"
            f" / 足の高さの半径 {r_foot * 10:.1f} mm"
        )
        build_peg_holes(body_occ.component, body)

        # 腕は右（+X）に作り、左は Z 軸まわりに 180° 回した同じ部品
        arm_occ = new_component(root, "腕")
        build_arm(arm_occ.component)
        arm_occ2 = root.occurrences.addExistingComponent(arm_occ.component, rotation_for(-90.0))
        foot_occ = new_component(root, "足", rotation_for(FOOT_ANGLE))
        build_foot(foot_occ.component, r_foot)
        foot_occ2 = root.occurrences.addExistingComponent(
            foot_occ.component, rotation_for(-FOOT_ANGLE)
        )

        printable = [body_occ, plate_occ, arm_occ, arm_occ2, foot_occ, foot_occ2] + eye_occs
        if cap is not None:
            step("ふたの厚さ", lambda: check_front_walls(cap))

        step(
            "f3d の書き出し",
            lambda: _design.exportManager.execute(
                _design.exportManager.createFusionArchiveExportOptions(
                    os.path.join(OUT_DIR, "futamaru_pot.f3d")
                )
            ),
        )

        refs = [ref_occ]
        for occ in printable + refs:
            for b in occ.bRepBodies:
                if b.isVisible:
                    log(f"BBOX {occ.name} / {b.name}: {bbox_mm(b)}  vol {b.volume * 1000:.0f} mm3")
        step("干渉チェック", lambda: check_interference(printable, refs))

        def export():
            em = _design.exportManager
            for occ, fname in (
                (body_occ, "futamaru_pot_body"),
                (plate_occ, "futamaru_pot_face_cap"),
                (arm_occ, "futamaru_pot_arm"),
                *[(o, "futamaru_pot_eye") for o in eye_occs[:1]],
                (foot_occ, "futamaru_pot_foot"),
            ):
                bodies = occ.component.bRepBodies
                if bodies.count == 0:
                    continue
                opts = em.createSTLExportOptions(
                    bodies.item(0), os.path.join(OUT_DIR, fname + ".stl")
                )
                opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
                opts.sendToPrintUtility = False
                em.execute(opts)

        step("STL の書き出し", export)

        step("色", lambda: apply_colors(printable, eye_occs))

        def views():
            for comp in [o.component for o in printable + [ref_occ]] + [root]:
                for sk in comp.sketches:
                    sk.isVisible = False
            # 部品の中で作業中のままだと、ほかの部品が半透明に写る。辺の線も消して、刷ったときの見た目に近づける
            _design.activateRootComponent()
            _app.activeViewport.visualStyle = adsk.core.VisualStyles.ShadedVisualStyle
            save_view("view_iso", (-30, -45, 25), (0, 0, 4.8))
            save_view("view_front", (0, -60, 4.8), (0, 0, 4.8))
            save_view("view_side", (45, 50, 6), (0, 0, 4.8))
            save_view("view_bottom", (0, 0, -60), (0, 0, 4.8), (0, 1, 0))
            save_view("view_top", (0, 0, 60), (0, 0, 4.8), (0, 1, 0))
            save_view("view_back_low", (25, 45, -10), (0, 0, 4.8))
            plate_occ.isLightBulbOn = False
            for o in eye_occs:
                o.isLightBulbOn = False
            save_view("view_inside_front", (0, -60, 4.8), (0, 0, 4.8))
            save_view("view_inside_iso", (-20, -40, 20), (0, -4, 4.8))
            plate_occ.isLightBulbOn = True
            for o in eye_occs:
                o.isLightBulbOn = True
            save_view("view_iso", (-30, -45, 25), (0, 0, 4.8))

        step("画像の保存", views)
    except Exception:
        log("FATAL\n" + traceback.format_exc())
    finally:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, "build_log.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(_log))
        except Exception:
            pass
    fails = [line for line in _log if line.startswith(("FAIL", "FATAL"))]
    summary = (
        f"steps={len(_log)} fails={len(fails)}  ログ: {os.path.join(OUT_DIR, 'build_log.txt')}"
    )
    print(summary)
    # 結果はダイアログにしない。MCP から実行したときもダイアログが出て、閉じるまで Fusion が止まるため。
    # Fusion の画面から実行したときは［テキスト コマンド］に出る
    if _app:
        _app.log(summary)
