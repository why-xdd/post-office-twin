"""Модели оборудования отделения для объёмного вида: одна GLB на тип.

Запуск:  blender -b --factory-startup --python tools/models/build_models.py -- apps/web/public/models

Соглашения, на которые опирается клиент (canvas/models3d.ts):
- метры; начало координат — центр основания объекта на полу;
- ширина по X, глубина по Y, высота по Z (в glTF это X, −Z, Y);
- сторона к посетителю — +Y: на плане это сторона, противоположная месту сотрудника;
- номинальные габариты (ширина, глубина, высота) лежат в extras объекта как `nominal`:
  клиент растягивает модель под габариты конкретного объекта на плане.
"""

import math
import os
import sys

import bpy

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "models"
os.makedirs(OUT, exist_ok=True)

_materials: dict[str, bpy.types.Material] = {}


def mat(name, color, rough=0.5, metal=0.0, emit=0.0, alpha=1.0):
    key = name
    if key in _materials:
        return _materials[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit > 0:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emit
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        m.blend_method = "BLEND" if hasattr(m, "blend_method") else m.blend_method
    _materials[key] = m
    return m


def palette():
    return {
        "counter": mat("Корпус стойки", (0.86, 0.83, 0.76), 0.55, 0.02),
        "top": mat("Столешница", (0.52, 0.37, 0.24), 0.6),
        "wood": mat("Дерево", (0.62, 0.46, 0.3), 0.65),
        "steel": mat("Сталь", (0.62, 0.64, 0.65), 0.32, 0.85),
        "steel_dark": mat("Тёмная сталь", (0.18, 0.2, 0.21), 0.38, 0.8),
        "plastic": mat("Пластик", (0.8, 0.8, 0.77), 0.5),
        "dark": mat("Тёмный корпус", (0.1, 0.11, 0.12), 0.45, 0.2),
        "screen": mat("Экран", (0.16, 0.36, 0.5), 0.2, 0.0, emit=1.2),
        "glass": mat("Стекло", (0.75, 0.86, 0.9), 0.05, 0.0, alpha=0.28),
        "fabric": mat("Обивка", (0.36, 0.45, 0.47), 0.95),
        "blue": mat("Синяя эмаль", (0.06, 0.2, 0.42), 0.4, 0.3),
        "cardboard": mat("Картон", (0.6, 0.45, 0.28), 0.85),
        "panel": mat("Панель стенда", (0.92, 0.9, 0.84), 0.7),
        "accent": mat("Полоса навигации", (0.16, 0.3, 0.42), 0.6),
        "leaf": mat("Листва", (0.2, 0.36, 0.17), 0.8),
        "pot": mat("Кашпо", (0.32, 0.3, 0.28), 0.7),
        "curtain": mat("Штора", (0.55, 0.6, 0.62), 0.95),
        "rubber": mat("Резина", (0.05, 0.05, 0.05), 0.9),
    }


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _materials.clear()


def box(name, size, at, material, bevel=0.008):
    """Брусок: size — (x, y, z), at — центр основания (x, y, низ по z)."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(at[0], at[1], at[2] + size[2] / 2))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    if bevel > 0 and min(size) > bevel * 2.5:
        mod = obj.modifiers.new("Фаска", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = "ANGLE"
    obj.data.materials.append(material)
    return obj


def cyl(name, radius, height, at, material, vertices=24, rotate_x=0.0):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=height, location=(at[0], at[1], at[2] + height / 2)
    )
    obj = bpy.context.active_object
    obj.name = name
    if rotate_x:
        obj.rotation_euler[0] = rotate_x
        bpy.ops.object.transform_apply(rotation=True)
    obj.data.materials.append(material)
    return obj


def blob(name, radius, at, material, squash=1.0):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=at)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (1, 1, squash)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(material)
    return obj


def tilt(obj, angle_deg, pivot):
    """Наклон детали вокруг оси X через точку pivot (экран терминала, табло)."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.scene.cursor.location = pivot
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    obj.rotation_euler[0] = math.radians(angle_deg)
    bpy.ops.object.transform_apply(rotation=True)


def finish(code, nominal):
    """Применяет фаски, сливает детали в один объект и выгружает GLB."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for obj in meshes:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        for mod in list(obj.modifiers):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    model = bpy.context.active_object
    model.name = code
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    model["nominal"] = list(nominal)
    tris = sum(len(p.vertices) - 2 for p in model.data.polygons)
    path = os.path.join(OUT, f"{code}.glb")
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_extras=True,
        export_yup=True,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
    )
    print(f"{code}: {tris} треугольников, {os.path.getsize(path) // 1024} КБ")


# ---------- модели ----------


def counter(code, width, depth, height, glass=True, windows=1):
    p = palette()
    box("Корпус", (width, depth, height - 0.04), (0, 0, 0), p["counter"])
    box("Цоколь", (width - 0.04, depth - 0.06, 0.08), (0, 0.0, 0), p["dark"], 0.004)
    box("Столешница", (width + 0.04, depth + 0.04, 0.04), (0, 0, height - 0.04), p["top"], 0.006)
    # Полоса по фасаду к посетителю и полка для документов.
    box("Полоса фасада", (width - 0.06, 0.012, 0.06), (0, depth / 2 + 0.004, height * 0.55), p["accent"], 0.003)
    box("Полка посетителя", (width - 0.1, 0.18, 0.03), (0, depth / 2 + 0.09, height - 0.2), p["top"], 0.005)
    # Монитор сотрудника на стороне служебной зоны.
    for i in range(windows):
        x = -width / 2 + width * (i + 0.5) / windows
        # Экран смотрит на сотрудника, то есть в −Y.
        box("Подставка монитора", (0.08, 0.06, 0.18), (x, -depth / 2 + 0.3, height), p["dark"], 0.004)
        box("Монитор", (0.5, 0.03, 0.32), (x, -depth / 2 + 0.28, height + 0.14), p["dark"], 0.006)
        box("Экран монитора", (0.46, 0.005, 0.28), (x, -depth / 2 + 0.262, height + 0.16), p["screen"], 0)
    if glass:
        gap = 0.16
        box("Стекло", (width - 0.04, 0.012, 0.62), (0, depth / 2 - 0.05, height + gap), p["glass"], 0)
        for side in (-1, 1):
            box("Стойка рамы", (0.03, 0.03, 0.62 + gap), (side * (width / 2 - 0.015), depth / 2 - 0.05, height), p["steel"], 0.004)
        box("Верх рамы", (width, 0.03, 0.03), (0, depth / 2 - 0.05, height + gap + 0.62), p["steel"], 0.004)


def build_all():
    p = None

    reset(); counter("cashier_window", 1.2, 0.8, 1.15); finish("cashier_window", (1.2, 0.8, 1.15))
    reset(); counter("pvz_counter", 2.0, 0.8, 1.1, glass=False, windows=2)
    p = palette()
    box("Сканер", (0.14, 0.1, 0.12), (0.55, 0.15, 1.1), p["dark"], 0.004)
    box("Посылка", (0.35, 0.25, 0.2), (-0.5, 0.1, 1.1), p["cardboard"], 0.01)
    finish("pvz_counter", (2.0, 0.8, 1.1))

    # Терминал электронной очереди: колонна с наклонным экраном.
    reset(); p = palette()
    box("Основание", (0.6, 0.6, 0.06), (0, 0, 0), p["steel_dark"], 0.008)
    box("Колонна", (0.42, 0.34, 1.1), (0, -0.04, 0.06), p["plastic"], 0.02)
    head = box("Голова", (0.5, 0.16, 0.42), (0, 0.04, 1.08), p["plastic"], 0.02)
    tilt(head, -18, (0, 0.04, 1.08))
    scr = box("Экран", (0.42, 0.01, 0.32), (0, 0.13, 1.13), p["screen"], 0)
    tilt(scr, -18, (0, 0.04, 1.08))
    box("Щель талона", (0.16, 0.02, 0.02), (0, 0.13, 0.9), p["dark"], 0)
    finish("self_service_kiosk", (0.6, 0.6, 1.5))

    # Стойка с терминалом оплаты.
    reset(); p = palette()
    cyl("Основание", 0.22, 0.03, (0, 0, 0), p["steel_dark"], 32)
    cyl("Стойка", 0.035, 1.02, (0, 0, 0.03), p["steel"], 16)
    t = box("Терминал", (0.11, 0.2, 0.06), (0, 0.02, 1.05), p["dark"], 0.008)
    tilt(t, 25, (0, 0.02, 1.05))
    s = box("Экран терминала", (0.08, 0.08, 0.005), (0, 0.06, 1.11), p["screen"], 0)
    tilt(s, 25, (0, 0.02, 1.05))
    finish("payment_terminal_stand", (0.5, 0.5, 1.2))

    # Почтовый ящик на ножке: корпус эмалью, щель для писем к посетителю.
    reset(); p = palette()
    box("Ножка", (0.12, 0.12, 0.6), (0, 0, 0), p["steel_dark"], 0.006)
    box("Корпус ящика", (0.5, 0.4, 0.62), (0, 0, 0.6), p["blue"], 0.03)
    box("Козырёк", (0.46, 0.08, 0.03), (0, 0.22, 1.08), p["blue"], 0.01)
    box("Щель", (0.3, 0.02, 0.03), (0, 0.205, 1.03), p["dark"], 0)
    box("Дверца выемки", (0.36, 0.01, 0.3), (0, 0.205, 0.68), p["blue"], 0.004)
    finish("package_drop_box", (0.5, 0.4, 1.3))

    # Табло и медиапанель: тонкая рамка и светящийся экран.
    for code, w, h in (("info_display", 1.1, 0.65), ("media_panel", 1.0, 0.8)):
        reset(); p = palette()
        box("Рамка", (w, 0.06, h), (0, -0.02, 0), p["dark"], 0.008)
        box("Экран", (w - 0.04, 0.01, h - 0.04), (0, 0.015, 0.02), p["screen"], 0)
        box("Крепление", (0.3, 0.04, 0.2), (0, -0.05, h / 2 - 0.1), p["steel_dark"], 0.004)
        finish(code, (w, 0.1, h))

    # Скамья ожидания: три кресла на общей балке.
    reset(); p = palette()
    box("Балка", (2.0, 0.06, 0.05), (0, -0.05, 0.36), p["steel"], 0.006)
    for x in (-0.85, 0.85):
        box("Опора", (0.05, 0.42, 0.04), (x, 0, 0), p["steel"], 0.006)
        box("Стойка опоры", (0.05, 0.05, 0.36), (x, -0.05, 0.02), p["steel"], 0.006)
    for x in (-0.64, 0.0, 0.64):
        box("Сиденье", (0.56, 0.46, 0.08), (x, 0.02, 0.41), p["fabric"], 0.03)
        back = box("Спинка", (0.56, 0.07, 0.42), (x, -0.2, 0.47), p["fabric"], 0.03)
        tilt(back, 10, (x, -0.2, 0.47))
    for x in (-0.96, -0.32, 0.32, 0.96):
        box("Подлокотник", (0.05, 0.38, 0.03), (x, 0.0, 0.62), p["steel_dark"], 0.008)
        box("Стойка подлокотника", (0.03, 0.03, 0.2), (x, 0.0, 0.42), p["steel"], 0)
    finish("waiting_seat", (2.0, 0.5, 0.85))

    # Банкомат: корпус, тёмный фасад с экраном, полка с клавиатурой.
    reset(); p = palette()
    box("Корпус", (0.7, 0.5, 1.6), (0, -0.02, 0), p["steel"], 0.02)
    box("Фасад", (0.62, 0.02, 0.9), (0, 0.23, 0.62), p["dark"], 0.006)
    box("Экран", (0.36, 0.01, 0.28), (0, 0.245, 1.12), p["screen"], 0)
    box("Полка", (0.58, 0.2, 0.05), (0, 0.32, 0.9), p["steel_dark"], 0.01)
    box("Клавиатура", (0.2, 0.12, 0.012), (0, 0.33, 0.95), p["dark"], 0.002)
    box("Купюроприёмник", (0.24, 0.02, 0.04), (0, 0.245, 0.75), p["steel_dark"], 0.002)
    box("Подсветка", (0.6, 0.02, 0.05), (0, 0.235, 1.48), p["screen"], 0)
    finish("atm", (0.7, 0.5, 1.6))

    # Почтомат: сетка ячеек и экранный модуль посередине.
    reset(); p = palette()
    box("Корпус", (1.8, 0.6, 2.0), (0, 0, 0), p["plastic"], 0.015)
    cols, rows = 6, 6
    for c in range(cols):
        for r in range(rows):
            x = -0.9 + 0.15 + c * 0.3
            if c in (2, 3) and 2 <= r <= 3:
                continue
            box("Дверца", (0.27, 0.01, 0.28), (x, 0.3, 0.1 + r * 0.31), p["steel"], 0.003)
    box("Модуль экрана", (0.56, 0.02, 0.6), (0, 0.3, 0.72), p["dark"], 0.006)
    box("Экран", (0.36, 0.01, 0.26), (0, 0.312, 1.0), p["screen"], 0)
    finish("parcel_locker", (1.8, 0.6, 2.0))

    # Рабочее место сотрудника: стол, монитор, клавиатура.
    reset(); p = palette()
    box("Столешница", (1.2, 0.8, 0.03), (0, 0, 0.72), p["wood"], 0.006)
    for x in (-0.56, 0.56):
        box("Опора", (0.05, 0.7, 0.72), (x, 0, 0), p["steel"], 0.006)
    box("Царга", (1.06, 0.02, 0.3), (0, 0.36, 0.42), p["steel"], 0.004)
    # Сотрудник сидит со стороны −Y: монитор у дальнего края, экран и клавиатура к нему.
    box("Подставка", (0.08, 0.06, 0.16), (0, 0.27, 0.75), p["dark"], 0.004)
    box("Монитор", (0.55, 0.03, 0.34), (0, 0.25, 0.88), p["dark"], 0.006)
    box("Экран", (0.51, 0.005, 0.3), (0, 0.233, 0.9), p["screen"], 0)
    box("Клавиатура", (0.44, 0.15, 0.02), (0, -0.1, 0.75), p["dark"], 0.004)
    finish("back_office_workplace", (1.2, 0.8, 0.75))

    reset(); p = palette()
    box("Корпус", (0.5, 0.4, 0.38), (0, 0, 0), p["plastic"], 0.015)
    box("Лоток", (0.36, 0.14, 0.03), (0, 0.24, 0.12), p["plastic"], 0.006)
    box("Панель", (0.12, 0.06, 0.02), (0.15, 0.12, 0.38), p["dark"], 0.003)
    box("Крышка сканера", (0.48, 0.38, 0.06), (0, 0, 0.38), p["plastic"], 0.01)
    finish("printer", (0.5, 0.4, 0.45))

    # Стеллаж для посылок: стойки, полки, коробки.
    reset(); p = palette()
    for x in (-0.98, 0.98):
        for y in (-0.28, 0.28):
            box("Стойка", (0.04, 0.04, 2.0), (x, y, 0), p["steel"], 0.004)
    shelves = (0.1, 0.55, 1.0, 1.45, 1.9)
    for z in shelves:
        box("Полка", (2.0, 0.6, 0.025), (0, 0, z), p["steel"], 0.004)
    parcels = [(-0.6, 0.12, 0.4, 0.3), (0.1, 0.12, 0.5, 0.25), (0.65, 0.12, 0.3, 0.35), (-0.4, 0.57, 0.6, 0.28),
               (0.5, 0.57, 0.45, 0.3), (-0.7, 1.02, 0.35, 0.22), (0.2, 1.02, 0.55, 0.32), (-0.1, 1.47, 0.4, 0.2)]
    for x, z, w, h in parcels:
        box("Посылка", (w, 0.42, h), (x, 0, z + 0.025), p["cardboard"], 0.01)
    finish("storage_rack", (2.0, 0.6, 2.0))

    # Стеллаж для товаров: дерево, задняя стенка, товары на полках.
    reset(); p = palette()
    box("Задняя стенка", (1.0, 0.02, 1.8), (0, -0.19, 0), p["wood"], 0.004)
    for x in (-0.49, 0.49):
        box("Боковина", (0.02, 0.4, 1.8), (x, 0, 0), p["wood"], 0.004)
    goods = [mat("Товар красный", (0.6, 0.18, 0.15), 0.5), mat("Товар жёлтый", (0.8, 0.65, 0.2), 0.5),
             mat("Товар синий", (0.2, 0.32, 0.55), 0.5), mat("Товар белый", (0.88, 0.88, 0.85), 0.5)]
    for i, z in enumerate((0.05, 0.47, 0.89, 1.31, 1.75)):
        box("Полка", (0.96, 0.38, 0.02), (0, 0, z), p["wood"], 0.004)
        if z < 1.7:
            for j in range(5):
                box("Товар", (0.14, 0.22, 0.16 + 0.05 * ((i + j) % 3)), (-0.38 + j * 0.19, 0.02, z + 0.02), goods[(i + j) % 4], 0.006)
    finish("goods_rack", (1.0, 0.4, 1.8))

    # Весы: тумба, площадка, табло.
    reset(); p = palette()
    box("Тумба", (0.5, 0.5, 0.8), (0, 0, 0), p["counter"], 0.015)
    box("Площадка", (0.46, 0.4, 0.04), (0, 0.02, 0.8), p["steel"], 0.006)
    box("Табло", (0.18, 0.04, 0.08), (0, 0.27, 0.72), p["dark"], 0.004)
    box("Индикатор", (0.14, 0.005, 0.04), (0, 0.29, 0.74), p["screen"], 0)
    finish("weighing_station", (0.6, 0.6, 0.9))

    # Информационный стенд: две стойки и щит с полосой навигации.
    reset(); p = palette()
    for x in (-0.46, 0.46):
        box("Стойка", (0.04, 0.04, 1.8), (x, 0, 0), p["steel"], 0.004)
        box("Опора", (0.06, 0.2, 0.03), (x, 0, 0), p["steel_dark"], 0.004)
    box("Щит", (0.96, 0.03, 1.1), (0, 0.02, 0.62), p["panel"], 0.006)
    box("Шапка", (0.96, 0.035, 0.16), (0, 0.022, 1.56), p["accent"], 0.004)
    for i in range(4):
        box("Строка", (0.7 - 0.12 * (i % 2), 0.034, 0.03), (-0.05, 0.022, 1.32 - i * 0.12), p["accent"], 0)
    finish("info_stand", (1.0, 0.2, 1.8))

    # Сейф: тёмная сталь, дверь со штурвалом.
    reset(); p = palette()
    box("Корпус", (0.6, 0.6, 0.9), (0, 0, 0), p["steel_dark"], 0.012)
    box("Дверь", (0.5, 0.02, 0.78), (0, 0.3, 0.06), p["steel_dark"], 0.006)
    cyl("Штурвал", 0.07, 0.03, (0.1, 0.32, 0.45), p["steel"], 20, rotate_x=math.radians(90))
    cyl("Замок", 0.03, 0.02, (-0.12, 0.32, 0.55), p["steel"], 16, rotate_x=math.radians(90))
    finish("safe", (0.6, 0.6, 0.9))

    reset(); p = palette()
    cyl("Урна", 0.19, 0.66, (0, 0, 0), p["steel"], 28)
    cyl("Крышка", 0.2, 0.04, (0, 0, 0.66), p["steel_dark"], 28)
    finish("trash_bin", (0.4, 0.4, 0.7))

    # Растение: кашпо и крона из нескольких сфер.
    reset(); p = palette()
    cyl("Кашпо", 0.2, 0.4, (0, 0, 0), p["pot"], 24)
    cyl("Земля", 0.18, 0.02, (0, 0, 0.38), p["rubber"], 24)
    for x, y, z, r in ((0, 0, 0.75, 0.24), (0.1, 0.06, 0.92, 0.18), (-0.09, -0.05, 0.95, 0.17), (0.02, -0.08, 0.6, 0.18)):
        blob("Крона", r, (x, y, z), p["leaf"], 1.15)
    cyl("Ствол", 0.02, 0.4, (0, 0, 0.4), p["wood"], 8)
    finish("potted_plant", (0.5, 0.5, 1.1))

    # Примерочная: каркас и штора, открытая сторона к посетителю.
    reset(); p = palette()
    for x in (-0.58, 0.58):
        for y in (-0.58, 0.58):
            box("Стойка", (0.04, 0.04, 2.1), (x, y, 0), p["steel"], 0.004)
    box("Задняя стенка", (1.16, 0.02, 2.0), (0, -0.58, 0.05), p["panel"], 0.004)
    for x in (-0.59, 0.59):
        box("Боковина", (0.02, 1.16, 2.0), (x, 0, 0.05), p["panel"], 0.004)
    box("Штора", (0.7, 0.03, 1.9), (-0.22, 0.58, 0.15), p["curtain"], 0.01)
    box("Карниз", (1.2, 0.03, 0.03), (0, 0.58, 2.07), p["steel"], 0)
    finish("fitting_room", (1.2, 1.2, 2.1))

    # Огнетушитель на кронштейне: красный баллон, головка, шланг.
    reset(); p = palette()
    red = mat("Красная эмаль", (0.62, 0.06, 0.05), 0.35, 0.2)
    cyl("Баллон", 0.07, 0.46, (0, 0.02, 0.04), red, 24)
    blob("Купол", 0.07, (0, 0.02, 0.5), red, 0.6)
    cyl("Головка", 0.025, 0.08, (0, 0.02, 0.52), p["steel"], 12)
    box("Рычаг", (0.03, 0.1, 0.015), (0, 0.05, 0.59), p["dark"], 0)
    box("Шланг", (0.02, 0.02, 0.32), (0.06, 0.06, 0.2), p["rubber"], 0)
    box("Кронштейн", (0.12, 0.04, 0.06), (0, -0.07, 0.25), p["steel_dark"], 0.004)
    finish("fire_extinguisher", (0.2, 0.2, 0.6))

    reset(); p = palette()
    box("Кронштейн", (0.06, 0.12, 0.04), (0, -0.04, 0.11), p["plastic"], 0.006)
    blob("Купол", 0.08, (0, 0.03, 0.08), p["dark"], 0.8)
    finish("camera", (0.2, 0.2, 0.15))


build_all()
