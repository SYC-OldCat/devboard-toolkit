# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: 打包开发板工具箱为 exe

产物:
  dist/devboard_toolkit.exe   - 主 GUI
  dist/per_board_runner.exe   - 单板回灌执行器 (被 GUI 调用)

数据文件:
  config.yaml                - 用户配置 (exe 旁边)
  tool/fcf_calibration/      - 标定文件
  tool/lib/                  - 板端 .so 库
"""

import os

block_cipher = None

# --- 数据文件 ---
datas = []
_project_root = os.path.abspath('.')

# config.yaml (存在才打包)
config_yaml = os.path.join(_project_root, 'config.yaml')
if os.path.exists(config_yaml):
    datas.append((config_yaml, '.'))

# tool/fcf_calibration 标定文件
calib_dir = os.path.join(_project_root, 'tool', 'fcf_calibration')
if os.path.exists(calib_dir):
    datas.append((calib_dir, 'tool/fcf_calibration'))

# tool/lib .so 库
lib_dir = os.path.join(_project_root, 'tool', 'lib')
if os.path.exists(lib_dir):
    datas.append((lib_dir, 'tool/lib'))

# --- 隐式导入 ---
hiddenimports = [
    'paramiko',
    'yaml',
    'openpyxl',
    'bs4',
    'tqdm',
    'requests',
    'json',
    'csv',
    'subprocess',
    'threading',
    'queue',
    'pathlib',
    'devboard_toolkit',
    'devboard_toolkit.config',
    'devboard_toolkit.cli',
    'devboard_toolkit.gui',
    'devboard_toolkit.batch_replay',
    'devboard_toolkit.script_gen',
    'devboard_toolkit.ssh_client',
    'devboard_toolkit.usage_check',
    'devboard_toolkit.classify_by_car',
    'devboard_toolkit.shell',
    'devboard_toolkit.data_preproc',
    'devboard_toolkit.data_preproc.pipeline',
    'devboard_toolkit.data_preproc.preprocessor',
    'devboard_toolkit.data_preproc.classifier',
    'devboard_toolkit.data_preproc.excel_report',
    'devboard_toolkit.data_preproc.file_ops',
    'devboard_toolkit.data_preproc.jira_auth',
    'devboard_toolkit.data_preproc.jira_extractor',
]

# ============================================================
# EXE 1: devboard_toolkit.exe (主 GUI)
# ============================================================
gui_a = Analysis(
    ['devboard_toolkit\\gui.py'],
    pathex=[_project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'PyQt5', 'PySide2'],
    cipher=block_cipher,
    noarchive=False,
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    [],
    name='devboard_toolkit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 保留控制台, 方便查看日志
    icon=None,  # 可加 icon='icon.ico'
)

# ============================================================
# EXE 2: per_board_runner.exe (单板回灌执行器)
# ============================================================
runner_a = Analysis(
    ['per_board_runner.py'],
    pathex=[_project_root],
    binaries=[],
    datas=[],  # 不需要数据文件, 共享主 exe 旁边的 config.yaml
    hiddenimports=[
        'paramiko',
        'yaml',
        'devboard_toolkit',
        'devboard_toolkit.config',
        'devboard_toolkit.batch_replay',
        'devboard_toolkit.ssh_client',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'PyQt5', 'PySide2'],
    cipher=block_cipher,
    noarchive=False,
)

runner_pyz = PYZ(runner_a.pure, runner_a.zipped_data, cipher=block_cipher)

runner_exe = EXE(
    runner_pyz,
    runner_a.scripts,
    runner_a.binaries,
    runner_a.zipfiles,
    runner_a.datas,
    [],
    name='per_board_runner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
