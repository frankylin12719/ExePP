#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
依赖：pip install PyQt5
"""

import sys
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import subprocess
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
    QTabWidget,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont


# ---------- 工作线程 ----------
class WorkThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config  # 包含所有配置的字典

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        try:
            # 1. 原地混淆
            self.log("[1/4] 开始原地混淆...")
            if not self._obfuscate():
                self.finished_signal.emit(False, "混淆失败，请查看日志")
                return

            # 2. 整理加固文件夹
            self.log("[2/4] 整理加固文件夹...")
            if not self._prepare_deploy_folder():
                self.finished_signal.emit(False, "整理加固文件夹失败")
                return

            # 3. 修改 ISS 脚本（基于现有模板）
            self.log("[3/4] 修改现有 ISS 脚本...")
            iss_path = self._modify_iss_script()
            if not iss_path:
                self.finished_signal.emit(False, "修改 ISS 脚本失败")
                return

            # 4. 编译安装包
            self.log("[4/4] 编译安装包...")
            if not self._compile_installer(iss_path):
                self.finished_signal.emit(False, "编译安装包失败")
                return

            self.finished_signal.emit(
                True, f"打包完成！安装包位于: {self.config['output_dir']}"
            )
        except Exception as e:
            self.log(f"发生异常: {str(e)}")
            self.finished_signal.emit(False, str(e))

    # ---------- 混淆 ----------
    def _obfuscate(self):
        """原地混淆：修改 .nrproj，输出目录设置为输入文件夹"""
        try:
            input_exe = self.config["input_exe"]
            input_dir = os.path.dirname(input_exe)
            tree = ET.parse(self.config["nrproj_template"])
            root = tree.getroot()
            for tag in ["InputAssembly", "Main_Assembly"]:
                node = root.find(f".//{tag}")
                if node is not None:
                    node.text = input_exe
                    self.log(f"  设置 {tag}: {input_exe}")
            out_node = root.find(".//OutputDirectory")
            if out_node is not None:
                out_node.text = input_dir
                self.log(f"  设置 OutputDirectory: {input_dir} (原地混淆)")
            fd, temp_proj = tempfile.mkstemp(suffix=".nrproj")
            os.close(fd)
            tree.write(temp_proj, encoding="utf-8", xml_declaration=True)
            cmd = f'"{self.config["reactor_path"]}" -project "{temp_proj}"'
            self.log(f"执行命令: {cmd}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                self.log(f"[Reactor] {line.strip()}")
            proc.wait()
            return proc.returncode == 0
        except Exception as e:
            self.log(f"混淆异常: {str(e)}")
            return False

    # ---------- 文件整理 ----------
    def _prepare_deploy_folder(self):
        """将混淆后的 EXE 复制到加固根目录，其他文件复制到 exeOther"""
        try:
            deploy_dir = self.config["deploy_dir"]
            input_exe = self.config["input_exe"]
            input_dir = os.path.dirname(input_exe)
            exe_name = os.path.basename(input_exe)

            if os.path.exists(deploy_dir):
                shutil.rmtree(deploy_dir)
            os.makedirs(deploy_dir)

            dest_exe = os.path.join(deploy_dir, exe_name)
            shutil.copy2(input_exe, dest_exe)
            self.log(f"  已复制混淆 EXE: {dest_exe}")

            other_dir = os.path.join(deploy_dir, "exeOther")
            os.makedirs(other_dir)
            for item in os.listdir(input_dir):
                if item == exe_name:
                    continue
                src = os.path.join(input_dir, item)
                dst = os.path.join(other_dir, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                self.log(f"  已复制: {item} -> exeOther/")
            return True
        except Exception as e:
            self.log(f"整理加固文件夹异常: {str(e)}")
            return False

    # ---------- 智能修改现有 ISS 脚本 ----------
    def _modify_iss_script(self):
        """
        读取现有的 .iss 模板文件，修改：
        1. #define 常量
        2. [Setup] 区段的 OutputDir 和 SetupIconFile
        3. [Files] 区段（整体替换为动态生成的两条 Source 指令）
        返回修改后的临时文件路径
        """
        try:
            with open(self.config["iss_template"], "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 1. 修改 #define 常量（逐行匹配替换）
            defines = {
                "MyAppName": self.config["app_name"],
                "MyAppVersion": self.config["app_version"],
                "MyAppPublisher": self.config["app_publisher"],
                "MyAppURL": self.config["app_url"],
                "MyAppExeName": self.config["app_exe_name"],
                "MyAppAssocName": self.config["assoc_name"],
                "MyAppAssocExt": self.config["assoc_ext"],
            }
            for i, line in enumerate(lines):
                for name, value in defines.items():
                    # 匹配 #define MyAppName "..." 或 #define MyAppName '...'
                    pattern = rf'^(#define\s+{name}\s+)(["\'])(.*?)(\2)'
                    match = re.match(pattern, line.strip())
                    if match:
                        lines[i] = (
                            f"{match.group(1)}{match.group(2)}{value}{match.group(4)}\n"
                        )
                        self.log(f"  已修改 #define {name} = {value}")
                        break

            # 2. 修改 [Setup] 区段的 OutputDir 和 SetupIconFile
            output_dir_escaped = self.config["output_dir"].replace("\\", "\\\\")
            icon_path_escaped = self.config["icon_path"].replace("\\", "\\\\")
            in_setup_section = False
            for i, line in enumerate(lines):
                if line.strip().startswith("[Setup]"):
                    in_setup_section = True
                    continue
                if in_setup_section and line.strip().startswith("["):
                    in_setup_section = False
                if in_setup_section:
                    # 匹配 OutputDir=... 行
                    if re.match(r"OutputDir\s*=", line):
                        lines[i] = f"OutputDir={output_dir_escaped}\n"
                        self.log(f"  已修改 OutputDir = {self.config['output_dir']}")
                    # 匹配 SetupIconFile=... 行
                    if self.config["icon_path"] and re.match(
                        r"SetupIconFile\s*=", line
                    ):
                        lines[i] = f"SetupIconFile={icon_path_escaped}\n"
                        self.log(f"  已修改 SetupIconFile = {self.config['icon_path']}")

            # 3. 替换 [Files] 区段（基于行解析，更稳健）
            new_files_section = self._generate_files_section()
            new_files_lines = new_files_section.splitlines(keepends=True)
            # 找到 [Files] 区段的起始行和结束行
            start_idx = None
            end_idx = None
            for i, line in enumerate(lines):
                if line.strip().startswith("[Files]"):
                    start_idx = i
                    # 继续寻找下一个 '[' 开头的行作为结束
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith("["):
                            end_idx = j - 1
                            break
                    if end_idx is None:
                        end_idx = len(lines) - 1
                    break
            if start_idx is not None:
                # 替换区段内容
                lines = lines[:start_idx] + new_files_lines + lines[end_idx:]
                self.log("  已替换 [Files] 区段内容")
            else:
                self.log("  警告: 未找到 [Files] 区段，将追加到文件末尾")
                lines.extend(["\n"] + new_files_lines)

            # 保存到临时文件
            fd, temp_iss = tempfile.mkstemp(suffix=".iss")
            os.close(fd)
            with open(temp_iss, "w", encoding="utf-8") as f:
                f.writelines(lines)
            self.log(f"  已生成修改后的 ISS 脚本: {temp_iss}")
            return temp_iss
        except Exception as e:
            self.log(f"修改 ISS 脚本异常: {str(e)}")
            return None

    def _generate_files_section(self):
        """生成 [Files] 区段内容"""
        deploy_dir = self.config["deploy_dir"].replace("\\", "\\\\")
        exe_name = self.config["app_exe_name"]
        return f"""[Files]
Source: "{deploy_dir}\\{exe_name}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{deploy_dir}\\exeOther\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs"""

    def _compile_installer(self, iss_path):
        """调用 ISCC 编译"""
        cmd = f'"{self.config["iscc_path"]}" "{iss_path}"'
        self.log(f"执行命令: {cmd}")
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
            for line in proc.stdout:
                self.log(f"[ISCC] {line.strip()}")
            proc.wait()
            return proc.returncode == 0
        except Exception as e:
            self.log(f"编译异常: {str(e)}")
            return False


# ---------- 主窗口（同前，略作调整）----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("自动化混淆加壳打包工具")
        self.setMinimumSize(900, 800)
        self.work_thread = None

        self.default_reactor = self._find_default_reactor()
        self.default_iscc = self._find_default_iscc()

        self._init_ui()

    def _find_default_reactor(self):
        candidates = [
            r"C:\Program Files (x86)\Eziriz\.NET Reactor\dotnet_reactor.exe",
            r"C:\Program Files\Eziriz\.NET Reactor\dotnet_reactor.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return ""

    def _find_default_iscc(self):
        candidates = [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return ""

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

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
                padding: 0 5px;
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

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # 页1：基本配置
        basic_tab = QWidget()
        tabs.addTab(basic_tab, "基本配置")
        basic_layout = QFormLayout(basic_tab)

        self.reactor_edit = self._create_browse_row(
            ".NET Reactor:", self.default_reactor, "exe"
        )
        self.iscc_edit = self._create_browse_row(
            "Inno Setup 编译器:", self.default_iscc, "exe"
        )
        self.nrproj_edit = self._create_browse_row(".nrproj 模板:", "", "nrproj")
        self.iss_template_edit = self._create_browse_row(".iss 模板:", "", "iss")
        basic_layout.addRow(self.reactor_edit[0], self.reactor_edit[1])
        basic_layout.addRow(self.iscc_edit[0], self.iscc_edit[1])
        basic_layout.addRow(self.nrproj_edit[0], self.nrproj_edit[1])
        basic_layout.addRow(self.iss_template_edit[0], self.iss_template_edit[1])

        self.input_exe_edit = self._create_browse_row(
            "待混淆 EXE (原地覆盖):", "", "exe"
        )
        self.deploy_dir_edit = self._create_browse_row(
            "加固文件夹 (整理后文件):", "", "dir"
        )
        self.output_dir_edit = self._create_browse_row("安装包输出目录:", "", "dir")
        self.icon_edit = self._create_browse_row("安装程序图标 (.ico):", "", "ico")
        basic_layout.addRow(self.input_exe_edit[0], self.input_exe_edit[1])
        basic_layout.addRow(self.deploy_dir_edit[0], self.deploy_dir_edit[1])
        basic_layout.addRow(self.output_dir_edit[0], self.output_dir_edit[1])
        basic_layout.addRow(self.icon_edit[0], self.icon_edit[1])

        # 页2：应用信息
        info_tab = QWidget()
        tabs.addTab(info_tab, "应用信息")
        info_layout = QFormLayout(info_tab)

        self.app_name_edit = QLineEdit("MyApplication")
        self.app_version_edit = QLineEdit("1.0.0")
        self.app_publisher_edit = QLineEdit("MyCompany")
        self.app_url_edit = QLineEdit("https://www.mycompany.com")
        self.app_exe_name_edit = QLineEdit("MyApp.exe")
        self.assoc_name_edit = QLineEdit("MyApp文件")
        self.assoc_ext_edit = QLineEdit(".myp")

        info_layout.addRow("应用程序名称 (MyAppName):", self.app_name_edit)
        info_layout.addRow("版本号 (MyAppVersion):", self.app_version_edit)
        info_layout.addRow("发布者 (MyAppPublisher):", self.app_publisher_edit)
        info_layout.addRow("网站 URL (MyAppURL):", self.app_url_edit)
        info_layout.addRow("主程序文件名 (MyAppExeName):", self.app_exe_name_edit)
        info_layout.addRow("关联文件类型名称 (MyAppAssocName):", self.assoc_name_edit)
        info_layout.addRow("关联扩展名 (MyAppAssocExt):", self.assoc_ext_edit)

        self.start_btn = QPushButton("开始混淆打包")
        self.start_btn.clicked.connect(self.start_process)
        layout.addWidget(self.start_btn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        log_label = QLabel("运行日志")
        log_label.setFont(QFont("Consolas", 9))
        layout.addWidget(log_label)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_text)

    def _create_browse_row(self, label_text, default_text, file_type):
        edit = QLineEdit(default_text)
        btn = QPushButton("浏览")
        if file_type == "dir":
            btn.clicked.connect(lambda: self._browse_dir(edit))
        else:
            filter_map = {
                "exe": "可执行文件 (*.exe)",
                "nrproj": "项目文件 (*.nrproj)",
                "iss": "Inno Setup 脚本 (*.iss)",
                "ico": "图标文件 (*.ico)",
            }
            btn.clicked.connect(
                lambda: self._browse_file(
                    edit, filter_map.get(file_type, "所有文件 (*.*)")
                )
            )
        layout = QHBoxLayout()
        layout.addWidget(edit)
        layout.addWidget(btn)
        return (QLabel(label_text), layout)

    def _browse_file(self, line_edit, filter_str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filter_str)
        if path:
            line_edit.setText(path)

    def _browse_dir(self, line_edit):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(path)

    def start_process(self):
        config = {
            "reactor_path": self.reactor_edit[1].itemAt(0).widget().text().strip(),
            "iscc_path": self.iscc_edit[1].itemAt(0).widget().text().strip(),
            "nrproj_template": self.nrproj_edit[1].itemAt(0).widget().text().strip(),
            "iss_template": self.iss_template_edit[1].itemAt(0).widget().text().strip(),
            "input_exe": self.input_exe_edit[1].itemAt(0).widget().text().strip(),
            "deploy_dir": self.deploy_dir_edit[1].itemAt(0).widget().text().strip(),
            "output_dir": self.output_dir_edit[1].itemAt(0).widget().text().strip(),
            "icon_path": self.icon_edit[1].itemAt(0).widget().text().strip(),
            "app_name": self.app_name_edit.text().strip(),
            "app_version": self.app_version_edit.text().strip(),
            "app_publisher": self.app_publisher_edit.text().strip(),
            "app_url": self.app_url_edit.text().strip(),
            "app_exe_name": self.app_exe_name_edit.text().strip(),
            "assoc_name": self.assoc_name_edit.text().strip(),
            "assoc_ext": self.assoc_ext_edit.text().strip(),
        }

        required = [
            "reactor_path",
            "iscc_path",
            "nrproj_template",
            "iss_template",
            "input_exe",
            "deploy_dir",
            "output_dir",
            "app_exe_name",
        ]
        for key in required:
            if not config[key]:
                QMessageBox.warning(self, "警告", f"请填写 {key} 字段")
                return
        if not os.path.isfile(config["reactor_path"]):
            QMessageBox.warning(self, "警告", ".NET Reactor 可执行文件不存在")
            return
        if not os.path.isfile(config["iscc_path"]):
            QMessageBox.warning(self, "警告", "Inno Setup 编译器不存在")
            return
        if not os.path.isfile(config["nrproj_template"]):
            QMessageBox.warning(self, "警告", ".nrproj 模板文件不存在")
            return
        if not os.path.isfile(config["iss_template"]):
            QMessageBox.warning(self, "警告", ".iss 模板文件不存在")
            return
        if not os.path.isfile(config["input_exe"]):
            QMessageBox.warning(self, "警告", "待混淆的 EXE 文件不存在")
            return
        if config["icon_path"] and not os.path.isfile(config["icon_path"]):
            QMessageBox.warning(self, "警告", "图标文件不存在，将忽略")
            config["icon_path"] = ""

        reply = QMessageBox.question(
            self,
            "确认",
            "原地混淆将直接覆盖原始 EXE 文件，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.work_thread = WorkThread(config)
        self.work_thread.log_signal.connect(self.append_log)
        self.work_thread.finished_signal.connect(self.on_finished)

        self.start_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.log_text.clear()
        self.work_thread.start()

    def append_log(self, text):
        self.log_text.append(text)

    def on_finished(self, success, message):
        self.start_btn.setEnabled(True)
        self.progress.setVisible(False)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "错误", f"处理失败: {message}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
