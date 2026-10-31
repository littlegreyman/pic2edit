"""PySide6 主界面：左侧待转换文件（列表+预览），右侧可编辑 Markdown（编辑/预览）+ 导出。"""
import os
import tempfile
import logging

from PySide6.QtCore import Qt, Signal, QObject, QThread
from PySide6.QtGui import QPixmap, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QTextEdit, QTextBrowser, QTabWidget, QStackedWidget,
    QToolBar, QFileDialog, QMessageBox, QProgressBar, QPushButton, QDialog,
    QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QGroupBox, QPlainTextEdit,
    QProgressDialog, QDoubleSpinBox, QSpinBox, QCheckBox,
)

import markdown

from config import Config, RESOLUTION_MODES, PROMPT_LABELS, ModelParams, MODEL_PARAM_PRESETS
from backends import OllamaBackend, TransformersBackend, LlamaCppBackend
from backends.server_launcher import ServerStarter
from worker import OcrWorker
from utils.input_prep import prepare_input, InputItem, InputDocument
from utils.export_docx import md_to_docx, save_text, md_pages_to_docx
from utils.assemble import assemble_document
from utils import usage_log
from utils.logging_setup import get_logger, log_env_on_error
from utils.app_home import resource_path

_logger = get_logger("gui")


class LogEmitter(QObject):
    """Bridge logging records to the Qt world via a signal (thread-safe)."""
    sig = Signal(str)


class GuiLogHandler(logging.Handler):
    """Route logging records into a PyQt widget safely (via signal)."""
    def __init__(self, emitter):
        super().__init__()
        self._emitter = emitter

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._emitter.sig.emit(msg)


class _StreamToSignal:
    """把 stdout/stderr 写入重定向到 Qt 信号（按行缓冲）。"""
    def __init__(self, emit):
        self._emit = emit
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit(line.rstrip("\r"))

    def flush(self):
        if self._buf.strip():
            self._emit(self._buf.rstrip("\r"))
            self._buf = ""


class DownloadThread(QThread):
    """后台下载官方 llama.cpp 引擎 + DeepSeek-OCR GGUF 到安装根目录（data_root）。"""
    log_line = Signal(str)
    # 注意：绝不能命名为 finished/started/terminated，否则会遮蔽 QThread 内置同名信号
    download_finished = Signal(bool, str)

    def run(self):
        import sys
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _StreamToSignal(self.log_line.emit)
        try:
            from download_models import download_all
            ok = download_all()
        except Exception as e:
            self.log_line.emit("[!] 下载异常: %r" % e)
            ok = False
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        self.download_finished.emit(bool(ok), "" if ok else "下载失败，详见上方日志")


class DownloadDialog(QDialog):
    """下载进度对话框：实时显示 download_models 的日志，完成时自动关闭。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载模型 / 引擎")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("正在下载 DeepSeek-OCR 引擎与模型（约 2GB，请保持联网）。\n"
                               "下载完成后会自动关闭本窗口，无需点击。请勿关闭程序。"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("border:1px solid #ccc; background:#fafafa; "
                               "font-family:Consolas,monospace; font-size:10px;")
        layout.addWidget(self.log, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 不确定模式（下载无总大小）
        layout.addWidget(self.progress)

    def append(self, line):
        self.log.appendPlainText(line)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())


SUPPORTED = (".jpg", ".jpeg", ".tif", ".tiff", ".pdf", ".png", ".bmp", ".webp")


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置")
        self.resize(580, 560)
        layout = QFormLayout(self)

        # 顶部：选择后端模型服务器类型
        self.backend = QComboBox()
        self.backend.addItems(["ollama", "transformers", "llama_cpp"])
        self.backend.setCurrentText(cfg.backend)
        self.backend.currentTextChanged.connect(self._on_backend_changed)
        layout.addRow("后端模型服务器类型", self.backend)

        # 动态子页面：按所选后端仅显示其一（其余由 QStackedWidget 自动隐藏）
        self.stack = QStackedWidget()

        # --- 子页面一：Ollama ---
        page_ollama = QWidget()
        ol = QFormLayout(page_ollama)
        self.ollama_url = QLineEdit(cfg.ollama_url)
        self.ollama_model = QLineEdit(cfg.ollama_model)
        ol.addRow("服务地址", self.ollama_url)
        ol.addRow("模型名", self.ollama_model)
        gb_mp_o, mp_o = self._make_model_params_group()
        ol.addRow(gb_mp_o)
        self.stack.addWidget(page_ollama)

        # --- 子页面二：Transformers（独立 Python 3.11 环境 + 本地服务）---
        page_tf = QWidget()
        tl = QFormLayout(page_tf)
        self.tf_url = QLineEdit(cfg.tf_url)
        self.tf_url.setPlaceholderText("http://localhost:8001")
        self.tf_python = QLineEdit(cfg.tf_python)
        self.tf_python.setPlaceholderText("如 .venv_tf/Scripts/python.exe")
        self.tf_model = QLineEdit(cfg.tf_model_path)
        self.tf_model.setPlaceholderText("本地 HF 模型目录（空则自动找 models_tf/）")
        self.tf_quant = QComboBox()
        self.tf_quant.addItems(["8bit", "4bit", "none"])
        self.tf_quant.setCurrentText(cfg.tf_quant)
        tl.addRow("服务地址", self.tf_url)
        tl.addRow("Python 解释器", self.tf_python)
        tl.addRow("模型目录", self.tf_model)
        tl.addRow("量化精度", self.tf_quant)
        gb_mp_tf, mp_tf = self._make_model_params_group()
        tl.addRow(gb_mp_tf)
        self.stack.addWidget(page_tf)

        # --- 子页面三：llama.cpp（本地 llama-server）---
        page_lcp = QWidget()
        lcl = QFormLayout(page_lcp)
        self.lcp_url = QLineEdit(cfg.llama_cpp_url)
        self.lcp_model = QLineEdit(cfg.llama_cpp_model)
        self.resolution = QComboBox()
        self.resolution.addItems(list(RESOLUTION_MODES.keys()))
        self.resolution.setCurrentText(cfg.resolution)
        lcl.addRow("服务地址", self.lcp_url)
        lcl.addRow("模型名", self.lcp_model)
        lcl.addRow("分辨率模式", self.resolution)
        gb_mp_l, mp_l = self._make_model_params_group()
        lcl.addRow(gb_mp_l)
        self.stack.addWidget(page_lcp)

        self._mp_widgets = {
            "ollama": mp_o,
            "transformers": mp_tf,
            "llama_cpp": mp_l,
        }
        layout.addRow(self.stack)
        self._on_backend_changed(self.backend.currentText())

        # 通用（所有后端共享）
        self.output_dir = QLineEdit(cfg.output_dir)
        self.output_dir.setPlaceholderText("（空=与输入文件同目录）")
        layout.addRow("导出目录", self.output_dir)

        # 上次成功复用开关
        self.auto_reuse = QCheckBox("启动时自动套用上次成功参数（模型参数 + 输出格式）")
        self.auto_reuse.setChecked(bool(getattr(cfg, "auto_reuse_last_success", True)))
        self.auto_reuse.setToolTip("开启后，每次启动自动采用最近一次“全部成功”作业的参数，"
                                  "便于快速回到可用配置。")
        layout.addRow(self.auto_reuse)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _on_backend_changed(self, text):
        """顶部下拉切换后端时，QStackedWidget 仅显示匹配的子页面。"""
        idx = self.backend.findText(text)
        if idx < 0:
            idx = 0
        self.stack.setCurrentIndex(idx)

    def _make_model_params_group(self):
        """构建一组“模型参数”控件，供每个后端子页各放一份（互不干扰）。

        默认值来自 cfg.model_params；预设 / 应用上次成功 仅作用于本组控件。
        """
        gb = QGroupBox("模型参数（传给模型的采样参数）")
        fl = QFormLayout(gb)
        temperature = QDoubleSpinBox()
        temperature.setRange(0.0, 2.0)
        temperature.setSingleStep(0.05)
        temperature.setValue(self.cfg.model_params.temperature)
        temperature.setToolTip("贪心确定性采样必须保持 0；temp>0 会让密集页随机退化/整篇重列。")
        fl.addRow("temperature（贪心，保持 0）", temperature)
        repeat_penalty = QDoubleSpinBox()
        repeat_penalty.setRange(0.8, 1.5)
        repeat_penalty.setSingleStep(0.01)
        repeat_penalty.setValue(self.cfg.model_params.repeat_penalty)
        fl.addRow("repeat_penalty", repeat_penalty)
        repeat_last_n = QSpinBox()
        repeat_last_n.setRange(0, 8192)
        repeat_last_n.setSingleStep(64)
        repeat_last_n.setValue(self.cfg.model_params.repeat_last_n)
        repeat_last_n.setSpecialValueText("默认(64)")
        repeat_last_n.setToolTip("惩罚回看窗口；默认 4096 才能抑制“整篇重列”长程退化。0=引擎默认。")
        fl.addRow("repeat_last_n", repeat_last_n)
        max_tokens = QSpinBox()
        max_tokens.setRange(256, 65536)
        max_tokens.setSingleStep(256)
        max_tokens.setValue(self.cfg.model_params.max_tokens)
        fl.addRow("max_tokens", max_tokens)
        preset = QComboBox()
        preset.addItem("（自定义）")
        for name in MODEL_PARAM_PRESETS:
            preset.addItem(name)
        preset.currentTextChanged.connect(
            lambda name, t=temperature, rp=repeat_penalty, rln=repeat_last_n, mt=max_tokens:
                self._apply_preset(name, t, rp, rln, mt)
        )
        fl.addRow("快速预设", preset)
        apply_btn = QPushButton("应用上次成功参数（模型）")
        apply_btn.clicked.connect(
            lambda _=False, t=temperature, rp=repeat_penalty, rln=repeat_last_n, mt=max_tokens:
                self._apply_last_success_to(t, rp, rln, mt)
        )
        fl.addRow(apply_btn)
        return gb, (temperature, repeat_penalty, repeat_last_n, max_tokens)

    def _apply_preset(self, name, temperature, repeat_penalty, repeat_last_n, max_tokens):
        preset = MODEL_PARAM_PRESETS.get(name)
        if preset is None:
            return
        temperature.setValue(preset.temperature)
        repeat_penalty.setValue(preset.repeat_penalty)
        repeat_last_n.setValue(preset.repeat_last_n)
        max_tokens.setValue(preset.max_tokens)

    def _apply_last_success_to(self, temperature, repeat_penalty, repeat_last_n, max_tokens):
        snap = usage_log.last_success_snapshot()
        mp = (snap or {}).get("model_params")
        if not isinstance(mp, dict):
            return
        temperature.setValue(float(mp.get("temperature", 0.0)))
        repeat_penalty.setValue(float(mp.get("repeat_penalty", 1.10)))
        repeat_last_n.setValue(int(mp.get("repeat_last_n", 4096)))
        max_tokens.setValue(int(mp.get("max_tokens", 16384)))

    def get_config(self) -> Config:
        self.cfg.backend = self.backend.currentText()
        self.cfg.ollama_url = self.ollama_url.text().strip()
        self.cfg.ollama_model = self.ollama_model.text().strip()
        self.cfg.llama_cpp_url = self.lcp_url.text().strip()
        self.cfg.llama_cpp_model = self.lcp_model.text().strip()
        self.cfg.tf_url = self.tf_url.text().strip()
        self.cfg.tf_python = self.tf_python.text().strip()
        self.cfg.tf_model_path = self.tf_model.text().strip()
        self.cfg.tf_quant = self.tf_quant.currentText()
        self.cfg.resolution = self.resolution.currentText()
        self.cfg.output_dir = self.output_dir.text().strip()
        # 模型参数：仅读取“当前选中后端”那一页的控件（其余子页隐藏）。
        _t, _rp, _rln, _mt = self._mp_widgets[self.cfg.backend]
        self.cfg.model_params = ModelParams(
            temperature=_t.value(),
            repeat_penalty=_rp.value(),
            repeat_last_n=_rln.value(),
            max_tokens=_mt.value(),
        )
        self.cfg.auto_reuse_last_success = self.auto_reuse.isChecked()
        return self.cfg


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        # 启动时自动套用上次成功参数（模型参数 + 输出格式），便于快速回到可用配置。
        # 仅作用于本次会话内存，不覆盖磁盘上用户显式保存的配置。
        if getattr(self.cfg, "auto_reuse_last_success", False) and self.cfg.last_success:
            ls = self.cfg.last_success or {}
            pt = ls.get("prompt_template")
            if pt and pt in PROMPT_LABELS:
                self.cfg.prompt_template = pt
            mp = ls.get("model_params")
            if isinstance(mp, dict):
                self.cfg.model_params = ModelParams(**mp)
        self.items: list = []           # 扁平图像项（供 OcrWorker / 导出使用）
        self.documents: list = []       # 文档分组（InputDocument），供树形展示/整文档导出（P0 起）
        self.page_tree_items: list = [] # 与 self.items 平行：每个图像项的树子节点（更新状态用）
        self.current = -1
        self.worker = None
        self.work_dir = tempfile.mkdtemp(prefix="pic2edit_")
        self.backend = self._build_backend()

        # 窗口图标（logo 随包收集；开发态在项目根，冻结态在 sys._MEIPASS）
        icon_path = resource_path("pic2edit-logo.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setWindowTitle("图片转可编辑文档（DeepSeek-OCR 本地离线）")
        self.resize(1100, 720)
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)
        for text, slot in [
            ("转换选中", self.convert_selected),
            ("转换全部", self.convert_all),
            ("导出", self.export_selected),
            ("下载模型", self.open_download),
            ("设置", self.open_settings),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)

        # 输出格式（提示词模板）下拉：让“表格”模式在主线即可选，不必进设置
        toolbar.addWidget(QLabel("  输出格式："))
        self.template_combo = QComboBox()
        for key, label in PROMPT_LABELS.items():
            self.template_combo.addItem(label, key)
        self._sync_template_combo()
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        toolbar.addWidget(self.template_combo)

        # 列表操作控件（打开文件/打开文件夹/删除/上移/下移/清空）已移至左侧“待转换文档”上方。

        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：树形文档列表（顶层=源文件，子项=页）+ 预览
        left = QWidget()
        lv = QVBoxLayout(left)

        # 列表操作区（添加 + 文档级管理）：置于“待转换文档”树形上方
        list_bar = QHBoxLayout()
        list_bar.addWidget(QLabel("列表操作："))
        self.btn_open_files = QPushButton("打开文件")
        self.btn_open_files.clicked.connect(self.open_files)
        self.btn_open_folder = QPushButton("打开文件夹")
        self.btn_open_folder.clicked.connect(self.open_folder)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_up = QPushButton("上移")
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down = QPushButton("下移")
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_all)
        for b in (self.btn_open_files, self.btn_open_folder, self.btn_delete,
                  self.btn_up, self.btn_down, self.btn_clear):
            list_bar.addWidget(b)
        lv.addLayout(list_bar)

        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabel("待转换文档（源文件 / 页面）")
        self.tree_widget.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )
        self.tree_widget.currentItemChanged.connect(self.on_select)
        self.tree_widget.itemSelectionChanged.connect(self._update_list_buttons)
        self._update_list_buttons()
        lv.addWidget(self.tree_widget, 1)
        self.preview = QLabel("待转换文件预览")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet("border:1px solid #ccc; background:#fafafa;")
        lv.addWidget(self.preview, 1)

        # 右：编辑 + 预览
        right = QWidget()
        rv = QVBoxLayout(right)
        self.tabs = QTabWidget()
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        self.editor.textChanged.connect(self._on_edit)
        self.viewer = QTextBrowser()
        self.tabs.addTab(self.editor, "编辑（Markdown）")
        self.tabs.addTab(self.viewer, "预览")
        rv.addWidget(self.tabs, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        # 底部“运行状态”面板：实时显示动作流水与错误环境摘要
        self.status_log = QPlainTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setMaximumHeight(150)
        self.status_log.setStyleSheet(
            "border:1px solid #ccc; background:#fafafa; "
            "color:#1565c0; "
            "font-family:Consolas,monospace; font-size:10px;"
        )
        self._log_emitter = LogEmitter()
        self._log_emitter.sig.connect(self._append_log)
        self._gui_handler = GuiLogHandler(self._log_emitter)
        self._gui_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        get_logger().addHandler(self._gui_handler)
        get_logger().info("GUI started; backend=%s", self.cfg.backend)

        container = QWidget()
        cvbox = QVBoxLayout(container)
        cvbox.setContentsMargins(6, 6, 6, 6)
        cvbox.addWidget(split, 1)
        cvbox.addWidget(QLabel("运行状态："))
        cvbox.addWidget(self.status_log)
        self.setCentralWidget(container)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress, 1)
        self.statusBar().showMessage(self._backend_status())

    # ---------- 后端 ----------
    def _build_backend(self):
        if self.cfg.backend == "transformers":
            _logger.info("backend selected: transformers (url=%s)", self.cfg.tf_url)
            return TransformersBackend(self.cfg.tf_url, "deepseek-ocr-local")
        if self.cfg.backend == "llama_cpp":
            _logger.info("backend selected: llama_cpp (url=%s)", self.cfg.llama_cpp_url)
            return LlamaCppBackend(self.cfg.llama_cpp_url, self.cfg.llama_cpp_model,
                                  self.cfg.model_params)
        _logger.info("backend selected: ollama (url=%s)", self.cfg.ollama_url)
        return OllamaBackend(self.cfg.ollama_url, self.cfg.ollama_model)

    def _backend_status(self) -> str:
        ok = self.backend.available()
        tag = "就绪" if ok else "不可用"
        return f"后端[{self.backend.name}] {tag}（{self.cfg.resolution} / {self.cfg.prompt_template}）"

    # ---------- 输出格式（提示词模板）----------
    def _sync_template_combo(self):
        idx = self.template_combo.findData(self.cfg.prompt_template)
        if idx >= 0:
            self.template_combo.setCurrentIndex(idx)

    def _on_template_changed(self, _idx):
        key = self.template_combo.currentData()
        if key and key != self.cfg.prompt_template:
            self.cfg.prompt_template = key
            self.cfg.save()
            self.statusBar().showMessage(self._backend_status())

    # ---------- 文件导入 ----------
    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", "", "图片/PDF (*.jpg *.jpeg *.tif *.tiff *.pdf *.png *.bmp *.webp)"
        )
        self._add_paths(files)

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(SUPPORTED)
        ]
        self._add_paths(sorted(files))

    def _add_document(self, doc: InputDocument):
        """把一个 InputDocument（源文件）挂到树：顶层项=文档，子项=各页。"""
        self.documents.append(doc)
        top = QTreeWidgetItem(self.tree_widget)
        label = doc.name + (f"  （{len(doc.pages)} 页）" if doc.is_multi else "")
        top.setText(0, label)
        top.setData(0, Qt.UserRole, doc)  # 顶层存 InputDocument
        # 多页文档默认展开，方便直接看到页；单页也展开（只有一个子项）
        top.setExpanded(True)
        for it in doc.pages:
            self.items.append(it)
            child = QTreeWidgetItem(top)
            child.setText(0, it.name)
            child.setData(0, Qt.UserRole, it)                 # 子项存 InputItem
            child.setData(0, Qt.UserRole + 1, len(self.items) - 1)  # 子项存扁平索引
            self.page_tree_items.append(child)

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if not os.path.isfile(p) or not p.lower().endswith(SUPPORTED):
                continue
            try:
                new_docs = prepare_input(p, self.work_dir)
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"{p}\n{e}")
                continue
            for doc in new_docs:
                before = len(self.items)
                self._add_document(doc)
                added += len(self.items) - before
        self.statusBar().showMessage(f"已添加 {added} 个页面，共 {len(self.items)} 个")
        _logger.info("added %d pages; total=%d", added, len(self.items))
        if self.current < 0 and self.items:
            self.tree_widget.setCurrentItem(self.tree_widget.topLevelItem(0))

    # ---------- 列表管理（仅文档级）----------
    def _reindex(self):
        """以树为唯一事实源，重建扁平 self.items / self.page_tree_items，
        并重写子项扁平索引 (UserRole+1)，同步 self.documents 顺序。

        删除 / 移动 / 清空后调用，确保扁平索引与树一致。
        """
        self.items = []
        self.page_tree_items = []
        self.documents = []
        for ti in range(self.tree_widget.topLevelItemCount()):
            top = self.tree_widget.topLevelItem(ti)
            doc = top.data(0, Qt.UserRole)
            self.documents.append(doc)
            for ci in range(top.childCount()):
                child = top.child(ci)
                it = child.data(0, Qt.UserRole)
                child.setData(0, Qt.UserRole + 1, len(self.items))
                self.items.append(it)
                self.page_tree_items.append(child)

    def _update_list_buttons(self):
        """根据当前选择更新列表管理按钮可用状态。

        规则：删除/移动仅在“纯文档级（一级）选择”时可用；选中任何二级页时禁用，
        因为二级页不允许删除/移动（锁定决策）。
        """
        sel = self.tree_widget.selectedItems()
        top_sel = [tw for tw in sel
                   if isinstance(tw.data(0, Qt.UserRole), InputDocument)]
        child_sel = [tw for tw in sel
                    if not isinstance(tw.data(0, Qt.UserRole), InputDocument)]
        doc_only = len(child_sel) == 0 and len(top_sel) >= 1
        single_doc = len(top_sel) == 1 and len(child_sel) == 0
        self.btn_delete.setEnabled(doc_only)
        if single_doc:
            idx = self.tree_widget.indexOfTopLevelItem(top_sel[0])
            self.btn_up.setEnabled(idx > 0)
            self.btn_down.setEnabled(idx < self.tree_widget.topLevelItemCount() - 1)
        else:
            self.btn_up.setEnabled(False)
            self.btn_down.setEnabled(False)
        self.btn_clear.setEnabled(self.tree_widget.topLevelItemCount() > 0)

    def delete_selected(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "正在转换中，请等待当前任务完成。")
            return
        sel = self.tree_widget.selectedItems()
        top_items = [tw for tw in sel
                    if isinstance(tw.data(0, Qt.UserRole), InputDocument)]
        child_sel = any(not isinstance(tw.data(0, Qt.UserRole), InputDocument)
                       for tw in sel)
        if not top_items:
            QMessageBox.information(
                self, "提示",
                "删除仅针对一级文档节点；二级页面不允许删除，请选择文档顶层项。"
            )
            return
        if child_sel:
            QMessageBox.information(
                self, "提示",
                "所选包含二级页面，无法删除。请只选中要删除的文档顶层项。"
            )
            return
        names = "、".join(t.text(0) for t in top_items)
        ret = QMessageBox.question(
            self, "确认删除",
            f"将删除以下 {len(top_items)} 个文档（含其全部页面）：\n{names}\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        for t in top_items:
            self.tree_widget.takeTopLevelItem(
                self.tree_widget.indexOfTopLevelItem(t)
            )
        self._reindex()
        if self.items:
            self.tree_widget.setCurrentItem(self.tree_widget.topLevelItem(0))
        else:
            self.current = -1
            self.editor.clear()
            self.preview.setText("待转换文件预览")
            self._render_preview("")
        self._update_list_buttons()
        self.statusBar().showMessage(f"已删除文档，剩余 {len(self.documents)} 个")

    def move_selected(self, delta: int):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "正在转换中，请等待当前任务完成。")
            return
        sel = self.tree_widget.selectedItems()
        top_items = [tw for tw in sel
                    if isinstance(tw.data(0, Qt.UserRole), InputDocument)]
        child_sel = any(not isinstance(tw.data(0, Qt.UserRole), InputDocument)
                       for tw in sel)
        if len(top_items) != 1 or child_sel:
            QMessageBox.information(
                self, "提示",
                "上下移动仅针对单个一级文档节点（二级页面不可移动），"
                "请只选中一个文档顶层项。"
            )
            return
        t = top_items[0]
        idx = self.tree_widget.indexOfTopLevelItem(t)
        new = idx + delta
        if new < 0 or new >= self.tree_widget.topLevelItemCount():
            return
        self.tree_widget.takeTopLevelItem(idx)
        self.tree_widget.insertTopLevelItem(new, t)
        self._reindex()
        self.tree_widget.setCurrentItem(t)
        self._update_list_buttons()

    def clear_all(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "正在转换中，请等待当前任务完成。")
            return
        if self.tree_widget.topLevelItemCount() == 0:
            return
        ret = QMessageBox.question(
            self, "确认清空",
            "将移除所有文档与页面，此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.tree_widget.clear()
        self._reindex()
        self.current = -1
        self.editor.clear()
        self.preview.setText("待转换文件预览")
        self._render_preview("")
        self._update_list_buttons()
        self.statusBar().showMessage("已清空列表")

    # ---------- 选择/预览 ----------
    def on_select(self, current, previous):
        if current is None:
            return
        obj = current.data(0, Qt.UserRole)
        if isinstance(obj, InputDocument):
            # 顶层（文档）被选中：预览其第一页
            if obj.pages:
                page = obj.pages[0]
                self._show_page(page, self.items.index(page))
        else:
            # 子项（页）被选中
            idx = current.data(0, Qt.UserRole + 1)
            self._show_page(obj, idx)

    def _show_page(self, it: InputItem, idx: int):
        self.current = idx
        pix = QPixmap(it.image_path)
        if not pix.isNull():
            self.preview.setPixmap(
                pix.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            )
        self.editor.blockSignals(True)
        self.editor.setPlainText(it.result or "")
        self.editor.blockSignals(False)
        self._render_preview(it.result or "")

    def _on_edit(self):
        if self.current >= 0:
            self.items[self.current].result = self.editor.toPlainText()
            self._render_preview(self.editor.toPlainText())

    def _render_preview(self, text):
        try:
            html = markdown.markdown(text, extensions=["tables", "fenced_code"])
        except Exception:
            html = f"<pre>{text}</pre>"
        self.viewer.setHtml(html)

    def _append_log(self, msg: str):
        self.status_log.appendPlainText(msg)
        sb = self.status_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 选择收集（树形 -> 扁平索引）----------
    def _gather_selection(self):
        """收集当前树选择。

        返回 (top_docs, child_pages)：
        - top_docs: 被选中的“文档级（顶层）”InputDocument 列表；
        - child_pages: 被选中的“页级（子项）”列表，元素为 (doc, item, flat_index)。
        """
        top_docs = []
        child_pages = []
        for tw in self.tree_widget.selectedItems():
            obj = tw.data(0, Qt.UserRole)
            if isinstance(obj, InputDocument):
                top_docs.append(obj)
            else:
                doc = tw.parent().data(0, Qt.UserRole)
                idx = tw.data(0, Qt.UserRole + 1)
                child_pages.append((doc, obj, idx))
        return top_docs, child_pages

    def _doc_flat_indices(self, doc: InputDocument):
        """返回该文档所有页的 (item, flat_index) 列表（保持页序）。"""
        return [(it, self.items.index(it)) for it in doc.pages]

    def _build_units(self, top_docs, child_pages):
        """把选择规约为“转换单元”列表，每个单元 = (doc, [flat_index,...])。

        规则：
        - 选中顶层文档 -> 转换该文档全部页（忽略二级选择）；
        - 仅选中二级页 -> 按文档分组，把同文档所选页作为一单元（多页则合并导出）；
        - 跨源文档多选二级页 -> 返回 None（不允许）。
        """
        units = []
        covered = set()
        for doc in top_docs:
            idxs = [i for _, i in self._doc_flat_indices(doc)]
            units.append((doc, idxs))
            covered.add(id(doc))
        by_doc = {}
        for doc, it, idx in child_pages:
            if id(doc) in covered:
                continue  # 该文档已按整文档处理
            by_doc.setdefault(id(doc), (doc, []))[1].append((it, idx))
        if len(by_doc) > 1:
            return None  # 跨源多选，禁止
        for _did, (doc, lst) in by_doc.items():
            order = {it: idx for it, idx in lst}
            idxs = [order[it] for it in doc.pages if it in order]
            units.append((doc, idxs))
        return units

    # ---------- 转换 ----------
    def convert_selected(self):
        top_docs, child_pages = self._gather_selection()
        if not top_docs and not child_pages:
            # 未显式多选：退化为当前聚焦页
            if self.current >= 0:
                self._run([self.current])
            else:
                QMessageBox.information(self, "提示", "请先选择要转换的文件")
            return
        units = self._build_units(top_docs, child_pages)
        if units is None:
            QMessageBox.warning(
                self, "跨源多选",
                "多页选择仅允许在同一个源文档内，不允许跨文档多选页面。\n"
                "请只选择同一个文档的页面，或选择文档顶层以转换整篇。"
            )
            return
        indices = []
        for _doc, idxs in units:
            indices.extend(idxs)
        if not indices:
            QMessageBox.information(self, "提示", "所选内容没有可转换的页面")
            return
        self._run(indices)

    def convert_all(self):
        self._run(list(range(len(self.items))))

    def _run(self, indices):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "正在转换中，请等待当前任务完成。")
            return
        if not indices:
            QMessageBox.information(self, "提示", "请先选择要转换的文件")
            return
        if not self.backend.available():
            # 后端不可用：先尝试按需自动拉起本地 OCR 引擎（GUI 关闭时也会清理）
            self._try_autostart_then_run(indices)
            return
        self._begin_convert(indices)

    def _begin_convert(self, indices):
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.worker = OcrWorker(
            self.backend, self.items, self.cfg.prompt(), indices,
            as_table=(self.cfg.prompt_template == "table"),
            prompt_template=self.cfg.prompt_template,
            model_params=self.cfg.model_params,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.worker_finished.connect(self._on_finished)
        self.worker.error.connect(lambda e: (_logger.error("worker error: %s", e), QMessageBox.critical(self, "错误", e)))
        self.worker.start()

    def _try_autostart_then_run(self, indices):
        """后端不可用时，弹窗拉起本地引擎并等待就绪；成功后自动进入转换。"""
        starter = ServerStarter(self.backend.name, self.backend.base_url, self.cfg)
        self._server_starter = starter
        starter.log.connect(self._append_log)

        dlg = QProgressDialog(
            "正在启动本地 OCR 引擎（%s）…\n首次加载模型到显存约需 1–3 分钟，请稍候；"
            "启动日志见主界面下方“运行状态”。" % self.backend.name,
            "取消", 0, 0, self,
        )
        dlg.setWindowTitle("启动 OCR 引擎")
        dlg.setModal(True)
        dlg.setMinimumDuration(0)

        def on_started():
            self._server_proc = starter._proc
            dlg.accept()
            self._begin_convert(indices)

        def on_failed(msg):
            if dlg.wasCanceled():  # 用户已取消，不再弹警告
                return
            dlg.reject()
            self._show_backend_unavailable(msg)

        starter.server_started.connect(on_started)
        starter.failed.connect(on_failed)
        dlg.canceled.connect(starter.stop)  # 用户取消：终止正在启动的引擎
        starter.start()
        dlg.exec()  # 阻塞直到 started / failed / 取消

    def _show_backend_unavailable(self, detail=None):
        hints = {
            "ollama": "请确认 Ollama 服务已启动，且已执行 `ollama pull deepseek-ocr`。",
            "llama_cpp": "请确认 llama-server 已启动（如 `llama-server -m xxx.gguf --mmproj xxx-mmproj.gguf "
                         "--port 8080`；注意：不要加 --chat-template deepseek-ocr，否则会 HTTP 400 / 只回废话），"
                         "且服务地址/模型名正确。",
            "transformers": "请确认已用 download_models_tf.bat 建好 .venv_tf 并下载模型，且已运行 run_transformers_server.bat 启动本地服务（默认 http://localhost:8001）。",
        }
        ready = self._engine_and_model_ready()
        msg = f"当前后端 [{self.backend.name}] 不可用。\n{hints.get(self.backend.name, '')}"
        if detail:
            msg += "\n\n自动启动失败详情：\n" + detail
        if self.backend.name == "llama_cpp" and not ready:
            msg += "\n\n提示：本地尚未就绪，可点击“下载模型/引擎”按钮自动下载（需联网，约 2GB）。"
        _logger.warning("backend [%s] unavailable; detail=%s", self.backend.name, detail)
        log_env_on_error(_logger, self.cfg, extra=f"detail={detail}")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("后端不可用")
        box.setText(msg)
        btn_ok = box.addButton(QMessageBox.StandardButton.Ok)
        btn_dl = None
        if self.backend.name == "llama_cpp" and not ready:
            btn_dl = QPushButton("下载模型/引擎")
            box.addButton(btn_dl, QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if btn_dl is not None and box.clickedButton() == btn_dl:
            self.open_download()

    def _on_progress(self, n, total, name):
        self.progress.setMaximum(total)
        self.progress.setValue(n)
        self.statusBar().showMessage(f"正在转换 第 {n}/{total} 项：{name} …")

    def _on_item_done(self, idx, text):
        if idx == self.current:
            self.editor.blockSignals(True)
            self.editor.setPlainText(text)
            self.editor.blockSignals(False)
            self._render_preview(text)
        # 更新树状态显示
        tw = self.page_tree_items[idx] if idx < len(self.page_tree_items) else None
        if tw is not None:
            tw.setText(0, f"{self.items[idx].name}  [{self.items[idx].status}]")

    def _on_finished(self):
        self.progress.setVisible(False)
        self.statusBar().showMessage(f"转换完成（共 {len(self.items)} 个图像项）")
        _logger.info("conversion finished; total items=%d", len(self.items))
        # 记录本次作业到 usage_log.jsonl（后端 / 输出格式 / 模型参数 / 成功失败计数）
        try:
            idxs = list(getattr(self.worker, "indices", range(len(self.items)))) if self.worker else list(range(len(self.items)))
            processed = [self.items[i] for i in idxs if 0 <= i < len(self.items)]
            n_done = sum(1 for it in processed if it.status == "完成")
            n_fail = sum(1 for it in processed if it.status == "失败")
            n_total = len(processed)
            success = (n_fail == 0 and n_done > 0)
            mp = self.cfg.model_params
            usage_log.record({
                "backend": self.cfg.backend,
                "prompt_template": self.cfg.prompt_template,
                "model_params": {
                    "temperature": mp.temperature,
                    "repeat_penalty": mp.repeat_penalty,
                    "repeat_last_n": mp.repeat_last_n,
                    "max_tokens": mp.max_tokens,
                },
                "n_total": n_total, "n_done": n_done, "n_fail": n_fail,
                "success": success,
            })
            # 全部成功：更新上次成功快照，供“应用上次成功 / 自动复用”
            if success:
                self.cfg.last_success = {
                    "prompt_template": self.cfg.prompt_template,
                    "model_params": {
                        "temperature": mp.temperature,
                        "repeat_penalty": mp.repeat_penalty,
                        "repeat_last_n": mp.repeat_last_n,
                        "max_tokens": mp.max_tokens,
                    },
                }
                try:
                    self.cfg.save()
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- 导出（按选择，单一按钮）----------
    def export_selected(self):
        """导出（按当前树选择）：

        - 选中一级目录（顶层文档） -> 导出该文档全部页（装配成一个文件）；
        - 选中二级页面（1 个或多个，需同属一个文档） -> 导出这些页组成的文档；
        - 跨文档多选二级页 -> 拒绝。
        格式：docx 在页间插入真实分页符；md/txt 用分隔线/页码标记。

        未显式多选时，以当前聚焦项为准：聚焦顶层文档=整文档，聚焦某页=该页。
        """
        top_docs, child_pages = self._gather_selection()
        if not top_docs and not child_pages:
            # 未显式多选：以当前聚焦项为准（顶层=整文档，子页=该页）
            cur = self.tree_widget.currentItem()
            if cur is None:
                QMessageBox.information(self, "提示", "请先选择要导出的文档或页面")
                return
            obj = cur.data(0, Qt.UserRole)
            if isinstance(obj, InputDocument):
                units = [(obj, [i for _, i in self._doc_flat_indices(obj)])]
            else:
                doc = cur.parent().data(0, Qt.UserRole)
                idx = cur.data(0, Qt.UserRole + 1)
                units = [(doc, [idx])]
        else:
            units = self._build_units(top_docs, child_pages)
            if units is None:
                QMessageBox.warning(
                    self, "跨源多选",
                    "多页选择仅允许在同一个源文档内，不允许跨文档多选页面。\n"
                    "请只选择同一个文档的页面，或选择文档顶层以导出整篇。"
                )
                return
            if not units:
                QMessageBox.information(self, "提示", "请先选择要导出的文档或页面")
                return

        # 多文档选择时仅导出第一个并提示（保持导出为单一文件）
        doc, idxs = units[0]
        if len(units) > 1:
            QMessageBox.information(
                self, "提示",
                f"检测到选中了 {len(units)} 个文档，本次仅导出第一个：{doc.name}\n"
                "如需分别导出，请逐个文档选择后导出。"
            )
        pages = [self.items[i] for i in idxs]
        texts = [it.result for it in pages]
        if not any(t.strip() for t in texts):
            QMessageBox.information(self, "提示", "该文档尚未转换或结果为空")
            return
        out_dir = self.cfg.output_dir or os.path.dirname(doc.source_path)
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(doc.source_path))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "导出", os.path.join(out_dir, base),
            "Word 文档 (*.docx);;Markdown (*.md);;文本 (*.txt)"
        )
        if not path:
            self.statusBar().showMessage("已取消导出")
            return
        self.statusBar().showMessage(f"正在导出：{os.path.basename(path)} …")
        try:
            if path.lower().endswith(".docx"):
                md_pages_to_docx(texts, path)   # 每页之间插入真实分页符
            else:
                save_text(assemble_document(texts), path)
            self.statusBar().showMessage(f"导出成功：{path}")
            _logger.info("exported: %s (%d pages)", path, len(pages))
            QMessageBox.information(self, "导出成功", f"已保存到：{path}")
        except Exception as e:
            self.statusBar().showMessage(f"导出失败：{e}")
            _logger.exception("export failed")
            QMessageBox.critical(self, "导出失败", str(e))

    # ---------- 模型/引擎下载 ----------
    def _engine_and_model_ready(self):
        """检查本地 llama.cpp 引擎与 GGUF 模型是否已就绪。"""
        try:
            import run_llamacpp_server as rls
            exe, model, _ = rls.discover()
            return bool(exe and model)
        except Exception:
            return False

    def open_download(self):
        """打开下载对话框，后台拉取官方 llama.cpp 引擎 + DeepSeek-OCR GGUF。"""
        if self._engine_and_model_ready():
            ret = QMessageBox.question(
                self, "模型已就绪",
                "本地已有 llama.cpp 引擎与模型。是否仍要重新下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        dlg = DownloadDialog(self)
        thread = DownloadThread()
        self._dl_thread = thread  # 防止被 GC
        thread.log_line.connect(dlg.append)
        thread.download_finished.connect(lambda ok, msg: self._on_download_finished(ok, msg, dlg))
        dlg.show()
        thread.start()

    def _on_download_finished(self, ok, msg, dlg):
        dlg.append("")
        if ok:
            dlg.append("[完成] 引擎与模型已就绪。现在可以开始转换（首次转换会自动拉起本地引擎）。")
            dlg.progress.setRange(0, 1)
            dlg.progress.setValue(1)
            QMessageBox.information(
                self, "下载完成",
                "DeepSeek-OCR 引擎与模型已下载到程序目录。\n"
                "现在可直接转换；若后端未启动，转换时会自动拉起本地引擎。"
            )
        else:
            dlg.append("[失败] " + (msg or "下载未完成。"))
            dlg.progress.setRange(0, 1)
            dlg.progress.setValue(0)
            QMessageBox.warning(self, "下载失败", msg or "下载未完成，请查看上方日志或手动下载。")
        # 下载完成后允许用户关闭对话框
        dlg.setWindowTitle("下载完成" if ok else "下载失败")
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        dlg.layout().addWidget(close_btn)

    # ---------- 设置 ----------
    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dlg.get_config()
            self.cfg.save()
            self.backend = self._build_backend()
            self._sync_template_combo()
            self.statusBar().showMessage(self._backend_status())

    def closeEvent(self, event):
        # GUI 自动拉起的本地 OCR 引擎（llama-server / transformers 服务）是 GUI 的子进程，
        # 关闭窗口时一并终止，避免残留进程占用显存。
        # 注意：_server_proc 仅在自动启动“成功”后才被赋值；若启动“失败/仍在进行中”时关闭窗口，
        # 进程由 ServerStarter._proc 持有，故这里两者都要清理，否则会留下孤儿进程持续占显存。
        proc = getattr(self, "_server_proc", None)
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        starter = getattr(self, "_server_starter", None)
        if starter is not None:
            try:
                starter.stop()  # 终止 starter 内部持有的子进程（覆盖启动失败/进行中的情况）
            except Exception:
                pass
        import shutil
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass
        event.accept()
