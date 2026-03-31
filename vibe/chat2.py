# 根据待混淆的Exe目录，动态调整.nrproj文件的Main_Assembly

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化EXE混淆+打包工具
依赖：pip install PyQt5
"""

import sys
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import tempfile
import shutil
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QGroupBox,
    QFormLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QIcon


# ---------- 工作线程 ----------
class WorkThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        reactor_path,
        iscc_path,
        nrproj_template,
        iss_template,
        input_exe,
        output_dir,
        parent=None,
    ):
        super().__init__(parent)
        self.reactor_path = reactor_path
        self.iscc_path = iscc_path
        self.nrproj_template = nrproj_template
        self.iss_template = iss_template
        self.input_exe = input_exe
        self.output_dir = output_dir

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        try:
            # 1. 准备输出目录
            os.makedirs(self.output_dir, exist_ok=True)
            obfuscated_dir = os.path.join(self.output_dir, "Obfuscated")
            os.makedirs(obfuscated_dir, exist_ok=True)

            # 2. 修改 .nrproj 并执行混淆
            self.log("[1/4] 准备 .NET Reactor 项目文件...")
            nrproj_modified = self._prepare_nrproj(
                self.nrproj_template, self.input_exe, obfuscated_dir
            )
            self.log("[2/4] 执行 .NET Reactor 混淆...")
            success = self._run_reactor(self.reactor_path, nrproj_modified)
            if not success:
                self.finished_signal.emit(False, "混淆失败，请查看日志")
                return

            # 查找混淆后的 exe 文件
            obfuscated_exe = self._find_obfuscated_exe(obfuscated_dir, self.input_exe)
            if not obfuscated_exe:
                self.finished_signal.emit(False, "未找到混淆后的 exe 文件")
                return
            self.log(f"混淆后的程序: {obfuscated_exe}")

            # 3. 修改 .iss 并执行打包
            self.log("[3/4] 准备 Inno Setup 脚本...")
            iss_modified = self._prepare_iss(
                self.iss_template, obfuscated_exe, self.output_dir
            )
            self.log("[4/4] 执行 Inno Setup 打包...")
            success = self._run_iscc(self.iscc_path, iss_modified)
            if not success:
                self.finished_signal.emit(False, "打包失败，请查看日志")
                return

            self.finished_signal.emit(True, f"打包完成！输出目录: {self.output_dir}")

        except Exception as e:
            self.log(f"发生异常: {str(e)}")
            self.finished_signal.emit(False, str(e))

    def _prepare_nrproj(self, template_path, input_exe, output_dir):
        """读取模板 .nrproj，修改输入、主程序集和输出路径，返回临时文件路径"""
        tree = ET.parse(template_path)
    
        root = tree.getroot()
    
        # 修改 InputAssembly
        input_asm = root.find(".//InputAssembly")
        if input_asm is not None:
            input_asm.text = input_exe
            self.log(f"  设置 InputAssembly: {input_exe}")
        else:
            self.log("  警告: 未找到 <InputAssembly> 标签")
    
        # 修改 Main_Assembly（用户特别要求）
        main_asm = root.find(".//Main_Assembly")
        if main_asm is not None:
            main_asm.text = input_exe
            self.log(f"  设置 Main_Assembly: {input_exe}")
        else:
            self.log("  注意: 未找到 <Main_Assembly> 标签（可选）")
    
        # 修改 OutputDirectory
        out_dir = root.find(".//OutputDirectory")
        if out_dir is not None:
            out_dir.text = output_dir
            self.log(f"  设置 OutputDirectory: {output_dir}")
        else:
            self.log("  警告: 未找到 <OutputDirectory> 标签")
    
        # 保存到临时文件
        fd, temp_path = tempfile.mkstemp(suffix=".nrproj")
        os.close(fd)
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        self.log(f"  已生成临时项目文件: {temp_path}")
        return temp_path

    def _run_reactor(self, reactor_exe, project_path):
        """调用 .NET Reactor 命令行"""
        cmd = f'"{reactor_exe}" -project "{project_path}"'
        self.log(f"执行: {cmd}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in iter(proc.stdout.readline, ""):
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

    def _find_obfuscated_exe(self, obfuscated_dir, original_exe):
        """在输出目录中查找混淆后的 exe（通常与原文件同名或带后缀）"""
        original_name = os.path.basename(original_exe)
        base, ext = os.path.splitext(original_name)

        # 常见命名规则：原名称.exe 或 原名称_Obfuscated.exe
        candidates = [
            os.path.join(obfuscated_dir, original_name),
            os.path.join(obfuscated_dir, f"{base}_Obfuscated{ext}"),
            os.path.join(obfuscated_dir, f"{base}_Secure{ext}"),
        ]
        for cand in candidates:
            if os.path.isfile(cand):
                return cand
        # 若未找到，遍历目录下所有 exe
        for f in os.listdir(obfuscated_dir):
            if f.lower().endswith(".exe"):
                return os.path.join(obfuscated_dir, f)
        return None

    def _prepare_iss(self, template_path, obfuscated_exe, output_dir):
        """读取 .iss 模板，替换占位符，返回临时文件路径"""
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 提取应用程序名称（从 exe 文件名）
        app_name = os.path.splitext(os.path.basename(obfuscated_exe))[0]

        replacements = {
            "{OBFUSCATED_EXE}": obfuscated_exe,
            "{OUTPUT_DIR}": output_dir,
            "{APP_NAME}": app_name,
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        # 保存临时文件
        fd, temp_path = tempfile.mkstemp(suffix=".iss")
        os.close(fd)
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        self.log(f"  已生成临时 ISS 文件: {temp_path}")
        return temp_path

    def _run_iscc(self, iscc_exe, iss_path):
        """调用 Inno Setup 编译器"""
        cmd = f'"{iscc_exe}" "{iss_path}"'
        self.log(f"执行: {cmd}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in iter(proc.stdout.readline, ""):
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


# ---------- 主窗口 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("自动化混淆打包工具")
        self.setMinimumSize(800, 600)
        self.work_thread = None

        # 默认路径自动检测
        self.default_reactor = self._find_default_reactor()
        self.default_iscc = self._find_default_iscc()

        self._init_ui()

    def _find_default_reactor(self):
        """检测 .NET Reactor 默认安装路径"""
        candidates = [
            r"C:\Program Files (x86)\Eziriz\.NET Reactor\dotnet_reactor.exe",
            r"C:\Program Files\Eziriz\.NET Reactor\dotnet_reactor.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _find_default_iscc(self):
        """检测 Inno Setup 编译器默认路径"""
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

        # 样式：modern 风格
        self.setStyleSheet(
            """
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
        """
        )

        # 工具路径组
        tool_group = QGroupBox("工具路径")
        tool_layout = QFormLayout()

        self.reactor_edit = QLineEdit(self.default_reactor)
        reactor_btn = QPushButton("浏览")
        reactor_btn.clicked.connect(
            lambda: self._browse_file(self.reactor_edit, "可执行文件 (*.exe)")
        )
        reactor_layout = QHBoxLayout()
        reactor_layout.addWidget(self.reactor_edit)
        reactor_layout.addWidget(reactor_btn)
        tool_layout.addRow(".NET Reactor:", reactor_layout)

        self.iscc_edit = QLineEdit(self.default_iscc)
        iscc_btn = QPushButton("浏览")
        iscc_btn.clicked.connect(
            lambda: self._browse_file(self.iscc_edit, "可执行文件 (*.exe)")
        )
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
        nrproj_btn.clicked.connect(
            lambda: self._browse_file(self.nrproj_edit, "项目文件 (*.nrproj)")
        )
        nrproj_layout = QHBoxLayout()
        nrproj_layout.addWidget(self.nrproj_edit)
        nrproj_layout.addWidget(nrproj_btn)
        template_layout.addRow(".nrproj 模板:", nrproj_layout)

        self.iss_edit = QLineEdit()
        iss_btn = QPushButton("浏览")
        iss_btn.clicked.connect(
            lambda: self._browse_file(self.iss_edit, "Inno Setup 脚本 (*.iss)")
        )
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
        input_exe_btn.clicked.connect(
            lambda: self._browse_file(self.input_exe_edit, "可执行文件 (*.exe)")
        )
        input_exe_layout = QHBoxLayout()
        input_exe_layout.addWidget(self.input_exe_edit)
        input_exe_layout.addWidget(input_exe_btn)
        io_layout.addRow("待混淆的 EXE:", input_exe_layout)

        self.output_dir_edit = QLineEdit()
        output_dir_btn = QPushButton("浏览")
        output_dir_btn.clicked.connect(lambda: self._browse_dir(self.output_dir_edit))
        output_dir_layout = QHBoxLayout()
        output_dir_layout.addWidget(self.output_dir_edit)
        output_dir_layout.addWidget(output_dir_btn)
        io_layout.addRow("输出目录:", output_dir_layout)

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
        # 校验输入
        reactor = self.reactor_edit.text().strip()
        iscc = self.iscc_edit.text().strip()
        nrproj = self.nrproj_edit.text().strip()
        iss = self.iss_edit.text().strip()
        input_exe = self.input_exe_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()

        if not all([reactor, iscc, nrproj, iss, input_exe, output_dir]):
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

        # 禁止重复启动
        if self.work_thread and self.work_thread.isRunning():
            QMessageBox.information(self, "提示", "任务正在进行中，请稍后")
            return

        # 清空日志
        self.log_text.clear()
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 无限进度

        # 启动线程
        self.work_thread = WorkThread(reactor, iscc, nrproj, iss, input_exe, output_dir)
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


# ---------- 入口 ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
