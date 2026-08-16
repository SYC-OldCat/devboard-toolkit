"""列表回灌启动脚本生成

交互式收集 5 个用户输入(感知包名称/用户/日期/清单文件名/车型选择),
自动提取感知包后缀、自动查表填入标定名称,替换模板占位符生成最终脚本。

输出文件: ./start_with_resume_<车型>.sh
"""

from pathlib import Path
from typing import Dict

from .config import load_car_models, load_replay_list_template


# 感知包公共前缀,用于提取后缀
APP_PREFIX = "NH_ADAS_PERCEPTION_"


def _extract_suffix(app_path: str) -> str:
    """提取感知包后缀: 去掉 NH_ADAS_PERCEPTION_ 前缀

    例:
      NH_ADAS_PERCEPTION_V3.1.3_8M_3.1.27223.2251   → V3.1.3_8M_3.1.27223.2251
      NH_ADAS_PERCEPTION_SPC030_V3.1_2M_3.1.27223.2252 → SPC030_V3.1_2M_3.1.27223.2252
    若不含前缀,原样返回
    """
    if app_path.startswith(APP_PREFIX):
        return app_path[len(APP_PREFIX):]
    return app_path


def _prompt(msg: str, default: str = "") -> str:
    """单行输入提示,带默认值"""
    suffix = f" [默认: {default}]" if default else ""
    while True:
        try:
            val = input(f"{msg}{suffix}: ").strip()
        except EOFError:
            val = ""
        if not val and default:
            return default
        if val:
            return val
        print("  [!] 不能为空,请重新输入")


def _select_car_model(car_models: Dict[str, str]) -> tuple:
    """选择车型,返回 (车型, 标定名称)"""
    keys = list(car_models.keys())
    print("\n[5/5] 选择车型(输入序号):")
    for i, k in enumerate(keys, 1):
        v = car_models[k]
        # 标定名称和车型相同时,只显示车型(简洁)
        if v == k:
            print(f"  {i:2d}) {k}")
        else:
            print(f"  {i:2d}) {k:<10s} →  {v}")
    while True:
        try:
            sel = input("> ").strip()
            idx = int(sel)
            if 1 <= idx <= len(keys):
                k = keys[idx - 1]
                return k, car_models[k]
            print(f"  [!] 序号范围 1-{len(keys)}")
        except ValueError:
            # 也支持直接输入车型名
            if sel in car_models:
                return sel, car_models[sel]
            print("  [!] 请输入有效序号或车型名")


def _render_template(template: str, vars: Dict[str, str]) -> str:
    """替换模板占位符 {{KEY}}"""
    out = template
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def gen_list_replay_interactive(out_dir: str = ".") -> str:
    """交互式生成列表回灌启动脚本

    Returns:
        生成的脚本文件路径
    """
    car_models = load_car_models()
    template = load_replay_list_template()

    if not template:
        raise RuntimeError("config.yaml 中未找到 replay_list_template")
    if not car_models:
        raise RuntimeError("config.yaml 中未找到 car_models")

    print("=" * 60)
    print("  列表回灌启动脚本生成")
    print("=" * 60)

    # [1/5] 感知包名称
    app_path = _prompt("[1/5] 感知包名称(如 NH_ADAS_PERCEPTION_V3.1.3_8M_3.1.27223.2251)")
    suffix = _extract_suffix(app_path)
    print(f"      → 自动提取后缀: {suffix}\n")

    # [2/5] 用户名
    user = _prompt("[2/5] 用户名(如 syc53636)")

    # [3/5] 日期
    date = _prompt("[3/5] 日期(如 20260811)")

    # [4/5] 素材清单文件名
    list_file = _prompt("[4/5] 素材清单文件名(不含 .txt,如 vpd_list_1)")

    # [5/5] 选择车型
    car_model, calibration = _select_car_model(car_models)
    print(f"      → 已选: 车型={car_model} / 标定={calibration}\n")

    # 占位符替换
    vars_map = {
        "APP_PATH": app_path,
        "APP_SUFFIX": suffix,
        "USER": user,
        "DATE": date,
        "CAR_MODEL": car_model,
        "CALIBRATION": calibration,
        "LIST_FILE": list_file,
    }
    script_content = _render_template(template, vars_map)
    script_content = script_content.replace("\r\n", "\n").replace("\r", "\n")

    # 生成文件名 + 写入
    filename = f"start_with_resume_{car_model}.sh"
    # 文件名清理:替换不安全字符(避免 / : 等)
    safe_filename = filename.replace("/", "_").replace(":", "_").replace(" ", "_")
    out_path = Path(out_dir) / safe_filename
    # 显式 newline="\n": Windows 默认会写 CRLF(\r\n), 传到 Linux 板子后 shebang 里的 \r 会导致 "not found"
    out_path.write_text(script_content, encoding="utf-8", newline="\n")

    # 摘要
    print("=" * 60)
    print("  生成完成")
    print("=" * 60)
    print(f"  脚本文件: {out_path}")
    print(f"  感知包  : {app_path}")
    print(f"  后缀    : {suffix}")
    print(f"  用户    : {user}")
    print(f"  日期    : {date}")
    print(f"  车型    : {car_model}")
    print(f"  标定    : {calibration}")
    print(f"  清单    : {list_file}.txt")
    print(f"  输出路径: output/{user}/{date}_{suffix}/{car_model}")
    print("=" * 60)
    return str(out_path)
