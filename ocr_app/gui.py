"""PySide6 主界面：左侧待转换文件（列表+预览），右侧可编辑 Markdown（编辑/预览）+ 导出。"""
import os
import tempfile
import logging

from PySide6.QtCore import Qt, Signal, QObject, QThread
from PySide6.QtGui import QPixmap, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QTextEdit, QTextBrowser, QTabWidget,
    QToolBar, QFileDialog, QMessageBox, QProgressBar, QPushButton, QDialog,
    QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QGroupBox, QPlainTextEdit,
    QProgressDialog,
)

import markdown

from config import Config, RESOLUTION_MODES, PROMPT_LABELS
from backends import OllamaBackend, TransformersBackend, LlamaCppBackend
from backends.server_launcher import ServerStarter
from worker import OcrWorker
from utils.input_prep import prepare_input, InputItem
from utils.export_docx import md_to_docx, save_text
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
    finished = Signal(bool, str)

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
        self.finished.emit(bool(ok), "" if ok else "下载失败，详见上方日志")


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
        self.resize(520, 360)
        layout = QFormLayout(self)

        self.backend = QComboBox()
        self.backend.addItems(["ollama", "transformers", "llama_cpp"])
        self.backend.setCurrentText(cfg.backend)
        layout.addRow("推理后端", self.backend)

        gb_ollama = QGroupBox("Ollama 后端")
        ol = QFormLayout(gb_ollama)
        self.ollama_url = QLineEdit(cfg.ollama_url)
        self.ollama_model = QLineEdit(cfg.ollama_model)
        ol.addRow("服务地址", self.ollama_url)
        ol.addRow("模型名", self.ollama_model)
        layout.addRow(gb_ollama)

        gb_lcp = QGroupBox("llama.cpp 后端（本地 llama-server）")
        lcl = QFormLayout(gb_lcp)
        self.lcp_url = QLineEdit(cfg.llama_cpp_url)
        self.lcp_model = QLineEdit(cfg.llama_cpp_model)
        lcl.addRow("服务地址", self.lcp_url)
        lcl.addRow("模型名", self.lcp_model)
        layout.addRow(gb_lcp)

        gb_tf = QGroupBox("Transformers 后端（独立 Python 3.11 环境 + 本地服务）")
        tl = QFormLayout(gb_tf)
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
        tl.addRow("量化", self.tf_quant)
        layout.addRow(gb_tf)

        self.resolution = QComboBox()
        self.resolution.addItems(list(RESOLUTION_MODES.keys()))
        self.resolution.setCurrentText(cfg.resolution)
        self.output_dir = QLineEdit(cfg.output_dir)
        self.output_dir.setPlaceholderText("（空=与输入文件同目录）")
        layout.addRow("分辨率模式", self.resolution)
        layout.addRow("导出目录", self.output_dir)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

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
        return self.cfg


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.items: list = []
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
            ("打开文件", self.open_files),
            ("打开文件夹", self.open_folder),
            ("转换选中", self.convert_selected),
            ("转换全部", self.convert_all),
            ("导出", self.export_current),
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

        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：列表 + 预览
        left = QWidget()
        lv = QVBoxLayout(left)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self.on_select)
        lv.addWidget(self.list_widget, 1)
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
            return LlamaCppBackend(self.cfg.llama_cpp_url, self.cfg.llama_cpp_model)
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

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if not os.path.isfile(p) or not p.lower().endswith(SUPPORTED):
                continue
            try:
                new_items = prepare_input(p, self.work_dir)
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"{p}\n{e}")
                continue
            for it in new_items:
                self.items.append(it)
                QListWidgetItem(it.name, self.list_widget)
                added += 1
        self.statusBar().showMessage(f"已添加 {added} 个图像项，共 {len(self.items)} 个")
        _logger.info("added %d items; total=%d", added, len(self.items))
        if self.current < 0 and self.items:
            self.list_widget.setCurrentRow(0)

    # ---------- 选择/预览 ----------
    def on_select(self, row):
        if row < 0 or row >= len(self.items):
            return
        self.current = row
        it = self.items[row]
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

    # ---------- 转换 ----------
    def convert_selected(self):
        rows = [i for i in range(self.list_widget.count())
                if self.list_widget.item(i).isSelected()]
        if not rows:
            rows = [self.current] if self.current >= 0 else []
        self._run(rows)

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
        # 更新列表状态显示
        item = self.list_widget.item(idx)
        if item:
            item.setText(f"{self.items[idx].name}  [{self.items[idx].status}]")

    def _on_finished(self):
        self.progress.setVisible(False)
        self.statusBar().showMessage(f"转换完成（共 {len(self.items)} 个图像项）")
        _logger.info("conversion finished; total items=%d", len(self.items))

    # ---------- 导出 ----------
    def export_current(self):
        if self.current < 0:
            QMessageBox.information(self, "提示", "请先选择要导出的文件")
            return
        it = self.items[self.current]
        if not it.result.strip():
            QMessageBox.information(self, "提示", "该文件尚未转换或结果为空")
            return
        out_dir = self.cfg.output_dir or os.path.dirname(it.source)
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(it.source))[0]
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
                md_to_docx(it.result, path)
            else:
                save_text(it.result, path)
            self.statusBar().showMessage(f"导出成功：{path}")
            _logger.info("exported: %s", path)
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
        thread.finished.connect(lambda ok, msg: self._on_download_finished(ok, msg, dlg))
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
        import shutil
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass
        event.accept()
