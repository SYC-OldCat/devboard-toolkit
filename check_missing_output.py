#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path


# ==================== 配置区 ====================
# 修改这里的路径即可运行，无需命令行参数
TXT_PATH = r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space\SYC\testbed\list\yaq_463.txt"
OUTPUT_DIR = r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space\SYC\testbed\list\output\YAQ\20260818_V3.1.4_2M_3.1.27223.2272\0452new\output"
# 可选: 是否覆盖写回原 txt（True=覆盖原文件，False=仅打印结果）
WRITE_BACK = False
# 可选: 另存为新文件路径（为空则不另存；非空时写入该路径）
OUT_PATH = r"\\hz-iotfs02\Model_Test\TestSpace\Personal_Space\SYC\testbed\list\yaq_463_1.txt"
# ==============================================


def get_basename(filepath: str) -> str:
    """获取文件名(不含扩展名)"""
    return Path(filepath.strip()).stem


def load_txt_lines(txt_path: str) -> list:
    """读取 txt 文件,返回非空行列表"""
    with open(txt_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_output_basenames(output_dir: str) -> set:
    """扫描 output 目录,返回所有文件的 basename 集合"""
    basenames = set()
    if not os.path.isdir(output_dir):
        return basenames
    for entry in os.listdir(output_dir):
        full = os.path.join(output_dir, entry)
        if os.path.isfile(full):
            basenames.add(Path(entry).stem)
    return basenames


def compare(txt_lines, output_basenames):
    """对比,返回 (existing, missing, missing_lines)"""
    existing, missing, missing_lines = [], [], []
    for line in txt_lines:
        bn = get_basename(line)
        if bn in output_basenames:
            existing.append(bn)
        else:
            missing.append(bn)
            missing_lines.append(line)
    return existing, missing, missing_lines


def write_lines(filepath, lines):
    """用 UTF-8 无 BOM 写入"""
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def main():
    txt_path = TXT_PATH
    output_dir = OUTPUT_DIR

    # 校验
    if not os.path.isfile(txt_path):
        print(f"[错误] txt 不存在: {txt_path}")
        sys.exit(1)
    if not os.path.isdir(output_dir):
        print(f"[错误] output 目录不存在: {output_dir}")
        sys.exit(1)

    # 加载
    txt_lines = load_txt_lines(txt_path)
    output_basenames = load_output_basenames(output_dir)

    # 对比
    existing, missing, missing_lines = compare(txt_lines, output_basenames)

    # 输出结果
    print()
    print("=" * 40)
    print(f"  Txt 路径:      {txt_path}")
    print(f"  Output 目录:   {output_dir}")
    print(f"  txt 总行数:    {len(txt_lines)}")
    print(f"  output 文件数: {len(output_basenames)}")
    print(f"  已存在:        {len(existing)}")
    print(f"  缺失:          {len(missing)}")
    print("=" * 40)
    print()

    if missing:
        print("[缺失文件名列表]")
        for m in missing:
            print(f"  {m}")
        print()

    # 写回 / 另存
    write_back = WRITE_BACK or bool(OUT_PATH)
    if write_back:
        target = OUT_PATH if OUT_PATH else txt_path
        write_lines(target, missing_lines)
        new_count = len(load_txt_lines(target))
        print(f"[完成] 已将 {new_count} 条缺失路径写入:")
        print(f"  {target}")
    else:
        print("[提示] 未执行写回。如需写回，修改配置区 WRITE_BACK = True 或设置 OUT_PATH。")


if __name__ == "__main__":
    main()
