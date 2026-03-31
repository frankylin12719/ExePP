# 混淆是在输入文件夹原地混淆，混淆的exe文件会覆盖原来的exe,那么不需要指定输出目录给.NET Reactor（输出目录为输入文件夹本身），那么可以直接从输入文件夹复制混淆后的exe到待打包文件夹根目录，其他文件到待打包文件夹的exeOther文件夹，修改iss文件的[Files]配置
# '''
# [Files]
# Source: "D:\work\protected\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
# Source: "D:\work\protected\exefile\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
# '''
# 进行打包，并修改OutputDir为输出文件夹

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化混淆打包工具（原地混淆 + 自定义文件整理 + 动态 [Files] 节）
依赖：pip install PyQt5
"""

import sys
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import subprocess
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog, QMessageBox,
    QProgressBar, QGroupBox, QFormLayout
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont


class WorkThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, reactor_path, iscc_path, nrproj_template, iss_template,
                 input_exe, deploy_dir, output_dir, parent=None):
        super().__init__(parent)
        self.reactor_path = reactor_path
        self.iscc_path = iscc_path
        self.nrproj_template = nrproj_template
        self.iss_template = iss_template
        self.input_exe = input_exe          # 原始 EXE 路径（也是输入文件夹路径）
        self.deploy_dir = deploy_dir        # 部署文件夹（整理后文件存放处）
        self.output_dir = output_dir        # 最终安装包输出目录

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        try:
            # 1. 原地混淆（输出目录设置为输入文件夹）
            self.log("[1/4] 准备 .NET Reactor 项目文件（原地混淆）...")
            input_dir = os.path.dirname(self.input_exe)
            nrproj_modified = self._prepare_nrproj(self.nrproj_template,
                                                   self.input_exe,
                                                   input_dir)   # 输出目录 = 输入文件夹
            self.log("[2/4] 执行 .NET Reactor 混淆（将覆盖原文件）...")
            success = self._run_reactor(self.reactor_path, nrproj_modified)
            if not success:
                self.finished_signal.emit(False, "混淆失败，请查看日志")
                return

            # 混淆后的 EXE 路径就是原始路径（已被覆盖）
            obfuscated_exe = self.input_exe
            self.log(f"混淆完成，已覆盖原文件: {obfuscated_exe}")

            # 2. 整理部署文件夹
            self.log("[3/4] 整理部署文件夹...")
            if not self._prepare_deploy_folder(obfuscated_exe, self.deploy_dir):
                self.finished_signal.emit(False, "整理部署文件夹失败")
                return
            self.log(f"部署文件夹已准备: {self.deploy_dir}")

            # 3. 生成 .iss 脚本并打包
            self.log("[4/4] 生成 Inno Setup 脚本并打包...")
            iss_modified = self._prepare_iss(self.iss_template,
                                             self.deploy_dir,
                                             self.output_dir)
            success = self._run_iscc(self.iscc_path, iss_modified)
            if not success:
                self.finished_signal.emit(False, "打包失败，请查看日志")
                return

            self.finished_signal.emit(True, f"打包完成！安装包位于: {self.output_dir}")

        except Exception as e:
            self.log(f"发生异常: {str(e)}")
            self.finished_signal.emit(False, str(e))

    # ---------- 混淆相关 ----------
    def _prepare_nrproj(self, template_path, input_exe, output_dir):
        """修改 .nrproj：InputAssembly、Main_Assembly 和 OutputDirectory（设为输入文件夹）"""
        tree = ET.parse(template_path)
        root = tree.getroot()

        input_asm = root.find(".//InputAssembly")
        if input_asm is not None:
            input_asm.text = input_exe
            self.log(f"  设置 InputAssembly: {input_exe}")

        main_asm = root.find(".//Main_Assembly")
        if main_asm is not None:
            main_asm.text = input_exe
            self.log(f"  设置 Main_Assembly: {input_exe}")

        out_dir = root.find(".//OutputDirectory")
        if out_dir is not None:
            out_dir.text = output_dir
            self.log(f"  设置 OutputDirectory: {output_dir} (原地混淆)")
        else:
            self.log("  警告: 未找到 <OutputDirectory> 标签，混淆输出可能不可控")

        fd, temp_path = tempfile.mkstemp(suffix=".nrproj")
        os.close(fd)
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        self.log(f"  已生成临时项目文件: {temp_path}")
        return temp_path

    def _run_reactor(self, reactor_exe, project_path):
        cmd = f'"{reactor_exe}" -project "{project_path}"'
        self.log(f"执行: {cmd}")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    shell=True, text=True, encoding='utf-8', errors='replace')
            for line in iter(proc.stdout.readline, ''):
                if line:
                    self.log(f"[Reactor] {line.strip()}")
            proc.wait()
            if proc.returncode != 0:
                self.log(f".NET Reactor 退出码: {proc.returncode}")
                return False
            return True
        except Exception as e:
            self.log(f"执行失败: {str(e)}")
            return False

    # ---------- 文件整理 ----------
    def _prepare_deploy_folder(self, obfuscated_exe, deploy_dir):
        """创建部署文件夹：
           - 根目录：混淆后的 EXE
           - exeOther 子目录：输入文件夹中除该 EXE 外的所有其他文件和子目录
        """
        try:
            # 清空并重建部署文件夹
            if os.path.exists(deploy_dir):
                shutil.rmtree(deploy_dir)
            os.makedirs(deploy_dir, exist_ok=True)

            # 1. 复制混淆后的 exe 到部署根目录
            dest_exe = os.path.join(deploy_dir, os.path.basename(obfuscated_exe))
            shutil.copy2(obfuscated_exe, dest_exe)
            self.log(f"  已复制混淆EXE: {dest_exe}")

            # 2. 准备 exeOther 目录
            other_dir = os.path.join(deploy_dir, "exeOther")
            os.makedirs(other_dir, exist_ok=True)

            # 3. 获取输入文件夹及主 exe 文件名
            input_dir = os.path.dirname(obfuscated_exe)
            main_exe_name = os.path.basename(obfuscated_exe)

            # 4. 复制输入文件夹中除主 exe 外的所有内容到 exeOther
            for item in os.listdir(input_dir):
                src_path = os.path.join(input_dir, item)
                if item == main_exe_name:
                    continue
                dst_path = os.path.join(other_dir, item)
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(src_path, dst_path)
                self.log(f"  已复制: {item} -> exeOther/")

            return True
        except Exception as e:
            self.log(f"整理部署文件夹出错: {str(e)}")
            return False

    # ---------- Inno Setup 脚本生成 ----------
    def _prepare_iss(self, template_path, deploy_dir, output_dir):
        """读取 .iss 模板，替换 {FILES_SECTION} 占位符为动态生成的 [Files] 节"""
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取应用程序名称（从部署文件夹中的 exe 文件名）
        exe_files = [f for f in os.listdir(deploy_dir) if f.lower().endswith('.exe')]
        if not exe_files:
            raise Exception("部署文件夹中没有找到 exe 文件")
        app_name = os.path.splitext(exe_files[0])[0]

        # 动态生成 [Files] 节
        files_section = self._generate_files_section(deploy_dir, app_name)

        replacements = {
            '{FILES_SECTION}': files_section,
            '{OUTPUT_DIR}': output_dir,
            '{APP_NAME}': app_name,
            '{DEPLOY_DIR}': deploy_dir,
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        fd, temp_path = tempfile.mkstemp(suffix=".iss")
        os.close(fd)
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(content)
        self.log(f"  已生成临时 ISS 文件: {temp_path}")
        return temp_path

    def _generate_files_section(self, deploy_dir, app_name):
        """生成符合用户要求的 [Files] 节：
           Source: "deploy_dir\app.exe"; DestDir: "{app}"; Flags: ignoreversion
           Source: "deploy_dir\exeOther\*"; DestDir: "{app}\exeOther"; Flags: ignoreversion recursesubdirs createallsubdirs
        """
        deploy_dir_norm = deploy_dir.replace('\\', '\\\\')
        lines = []
        lines.append("[Files]")
        # 主 EXE
        lines.append(f'Source: "{deploy_dir_norm}\\{app_name}.exe"; DestDir: "{{app}}"; Flags: ignoreversion')
        # exeOther 目录下的所有内容
        lines.append(f'Source: "{deploy_dir_norm}\\exeOther\\*"; DestDir: "{{app}}\\exeOther"; Flags: ignoreversion recursesubdirs createallsubdirs')
        return "\n".join(lines)

    def _run_iscc(self, iscc_exe, iss_path):
        cmd = f'"{iscc_exe}" "{iss_path}"'
        self.log(f"执行: {cmd}")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    shell=True, text=True, encoding='utf-8', errors='replace')
            for line in iter(proc.stdout.readline, ''):
                if line:
                    self.log(f"[ISCC] {line.strip()}")
            proc.wait()
            if proc.returncode != 0:
                self.log(f"Inno Setup 编译器退出码: {proc.returncode}")
                return False
            return True
        except Exception as e:
            self.log(f"执行失败: {str(e)}")
            return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("自动化混淆打包工具（原地混淆+自定义整理）")
        self.setMinimumSize(800, 700)
        self.work_thread = None

        self.default_reactor = self._find_default_reactor()
        self.default_iscc = self._find_default_iscc()

        self._init_ui()

    def _find_default_reactor(self):
        candidates = [
            r"C:\Program Files (x86)\Eziriz\.NET Reactor\dotnet_reactor.exe",
            r"C:\Program Files\Eziriz\.NET Reactor\dotnet_reactor.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _find_default_iscc(self):
        candidates = [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #cccccc;
                border-radius: 3px;
                padding: 3px;
            }
        """)

        # 工具路径组
        tool_group = QGroupBox("工具路径")
        tool_layout = QFormLayout()

        self.reactor_edit = QLineEdit(self.default_reactor)
        reactor_btn = QPushButton("浏览")
        reactor_btn.clicked.connect(lambda: self._browse_file(self.reactor_edit, "可执行文件 (*.exe)"))
        reactor_layout = QHBoxLayout()
        reactor_layout.addWidget(self.reactor_edit)
        reactor_layout.addWidget(reactor_btn)
        tool_layout.addRow(".NET Reactor:", reactor_layout)

        self.iscc_edit = QLineEdit(self.default_iscc)
        iscc_btn = QPushButton("浏览")
        iscc_btn.clicked.connect(lambda: self._browse_file(self.iscc_edit, "可执行文件 (*.exe)"))
        iscc_layout = QHBoxLayout()
        iscc_layout.addWidget(self.iscc_edit)
        iscc_layout.addWidget(iscc_btn)
        tool_layout.addRow("Inno Setup 编译器:", iscc_layout)

        tool_group.setLayout(tool_layout)
        main_layout.addWidget(tool_group)

        # 模板文件组
        template_group = QGroupBox("模板文件")
        template_layout = QFormLayout()

        self.nrproj_edit = QLineEdit()
        nrproj_btn = QPushButton("浏览")
        nrproj_btn.clicked.connect(lambda: self._browse_file(self.nrproj_edit, "项目文件 (*.nrproj)"))
        nrproj_layout = QHBoxLayout()
        nrproj_layout.addWidget(self.nrproj_edit)
        nrproj_layout.addWidget(nrproj_btn)
        template_layout.addRow(".nrproj 模板:", nrproj_layout)

        self.iss_edit = QLineEdit()
        iss_btn = QPushButton("浏览")
        iss_btn.clicked.connect(lambda: self._browse_file(self.iss_edit, "Inno Setup 脚本 (*.iss)"))
        iss_layout = QHBoxLayout()
        iss_layout.addWidget(self.iss_edit)
        iss_layout.addWidget(iss_btn)
        template_layout.addRow(".iss 模板:", iss_layout)

        template_group.setLayout(template_layout)
        main_layout.addWidget(template_group)

        # 输入输出组
        io_group = QGroupBox("输入与输出")
        io_layout = QFormLayout()

        self.input_exe_edit = QLineEdit()
        input_exe_btn = QPushButton("浏览")
        input_exe_btn.clicked.connect(lambda: self._browse_file(self.input_exe_edit, "可执行文件 (*.exe)"))
        input_exe_layout = QHBoxLayout()
        input_exe_layout.addWidget(self.input_exe_edit)
        input_exe_layout.addWidget(input_exe_btn)
        io_layout.addRow("待混淆的 EXE（原地覆盖）:", input_exe_layout)

        self.deploy_dir_edit = QLineEdit()
        deploy_dir_btn = QPushButton("浏览")
        deploy_dir_btn.clicked.connect(lambda: self._browse_dir(self.deploy_dir_edit))
        deploy_dir_layout = QHBoxLayout()
        deploy_dir_layout.addWidget(self.deploy_dir_edit)
        deploy_dir_layout.addWidget(deploy_dir_btn)
        io_layout.addRow("部署文件夹（整理后文件）:", deploy_dir_layout)

        self.output_dir_edit = QLineEdit()
        output_dir_btn = QPushButton("浏览")
        output_dir_btn.clicked.connect(lambda: self._browse_dir(self.output_dir_edit))
        output_dir_layout = QHBoxLayout()
        output_dir_layout.addWidget(self.output_dir_edit)
        output_dir_layout.addWidget(output_dir_btn)
        io_layout.addRow("安装包输出目录:", output_dir_layout)

        io_group.setLayout(io_layout)
        main_layout.addWidget(io_group)

        # 按钮和进度
        self.start_btn = QPushButton("开始混淆打包")
        self.start_btn.clicked.connect(self.start_process)
        main_layout.addWidget(self.start_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 日志区域
        log_label = QLabel("运行日志")
        log_label.setFont(QFont("Consolas", 9))
        main_layout.addWidget(log_label)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        main_layout.addWidget(self.log_text)

    def _browse_file(self, line_edit, filter_str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filter_str)
        if path:
            line_edit.setText(path)

    def _browse_dir(self, line_edit):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(path)

    def start_process(self):
        reactor = self.reactor_edit.text().strip()
        iscc = self.iscc_edit.text().strip()
        nrproj = self.nrproj_edit.text().strip()
        iss = self.iss_edit.text().strip()
        input_exe = self.input_exe_edit.text().strip()
        deploy_dir = self.deploy_dir_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()

        if not all([reactor, iscc, nrproj, iss, input_exe, deploy_dir, output_dir]):
            QMessageBox.warning(self, "警告", "请填写所有字段")
            return

        if not os.path.isfile(reactor):
            QMessageBox.warning(self, "警告", ".NET Reactor 可执行文件不存在")
            return
        if not os.path.isfile(iscc):
            QMessageBox.warning(self, "警告", "Inno Setup 编译器不存在")
            return
        if not os.path.isfile(nrproj):
            QMessageBox.warning(self, "警告", ".nrproj 模板文件不存在")
            return
        if not os.path.isfile(iss):
            QMessageBox.warning(self, "警告", ".iss 模板文件不存在")
            return
        if not os.path.isfile(input_exe):
            QMessageBox.warning(self, "警告", "待混淆的 EXE 文件不存在")
            return

        # 确认原地混淆会覆盖原文件，给出警告
        reply = QMessageBox.question(self, "确认", "原地混淆将直接覆盖原始 EXE 文件，是否继续？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        if self.work_thread and self.work_thread.isRunning():
            QMessageBox.information(self, "提示", "任务正在进行中，请稍后")
            return

        self.log_text.clear()
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.work_thread = WorkThread(reactor, iscc, nrproj, iss, input_exe, deploy_dir, output_dir)
        self.work_thread.log_signal.connect(self.append_log)
        self.work_thread.finished_signal.connect(self.on_finished)
        self.work_thread.start()

    def append_log(self, text):
        self.log_text.append(text)

    def on_finished(self, success, message):
        self.start_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "错误", f"处理失败: {message}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())