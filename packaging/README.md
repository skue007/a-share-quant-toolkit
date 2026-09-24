# packaging — 打包说明

本目录存放桌面分发的打包脚本与模板。产物（exe / 便携包 zip）体积大，不入库，按下面步骤本地重建。

## 1. PyInstaller 单文件 exe（sector-market-resonance / industry-mismatch）

前置：Python 3.10+ venv，`pip install pyinstaller` 及两个工具各自的依赖。

```bash
cd <repo-root>

# sector-market-resonance（CLI 出图）
python -m PyInstaller --onefile --clean --noconfirm --name sector-resonance \
  --paths sector-market-resonance/src \
  --exclude-module streamlit --exclude-module plotly --exclude-module altair \
  --exclude-module pyarrow --exclude-module tkinter \
  --distpath packaging/dist --workpath packaging/build --specpath packaging \
  packaging/run_sector_resonance.py

# industry-mismatch（内置行业映射 JSON 必须随包）
python -m PyInstaller --onefile --clean --noconfirm --name industry-mismatch \
  --paths industry-mismatch \
  --add-data "$(pwd)/industry-mismatch/industry_mismatch/data/industry_map.json;industry_mismatch/data" \
  --exclude-module streamlit --exclude-module plotly --exclude-module altair \
  --exclude-module pyarrow --exclude-module tkinter --exclude-module matplotlib \
  --distpath packaging/dist --workpath packaging/build --specpath packaging \
  packaging/run_industry_mismatch.py
```

注意：
- `--add-data` 必须用**绝对路径**（相对路径是相对 spec 文件解析的，会报找不到文件）。
- `--exclude-module streamlit ...` 必须加：两个工具的 CLI 里都有函数内 `import streamlit`
  （ui 子命令），不加会把整个 Streamlit+plotly 塞进 exe（+40~60MB）。
- 产物 230~240MB 是正常体积：PyInstaller 默认不压缩原生 pyd（pandas/numpy/matplotlib），
  开 UPX 可再压一半但易触发杀软误报，未采用。
- 警告 `WARNING: Hidden import ... not found` 若不影响运行可忽略。

## 2. 绿色便携包（linkage-stock-picker，Streamlit 网页应用）

Streamlit 不支持冻结打包，采用「内嵌 Python + 依赖 + 启动脚本」便携包，解压即用、免装 Python。

```bash
cd packaging/portable
# 1) 下载 Windows embeddable Python（必须与 site-packages 的 wheel 版本一致！）
curl -LO https://www.python.org/ftp/python/3.13.7/python-3.13.7-embed-amd64.zip
unzip -q python-3.13.7-embed-amd64.zip -d python
# 2) python/python313._pth 内容：
#    python313.zip
#    .
#    ../site-packages
#    import site
# 3) 用 Python 3.13 的 pip 装依赖到独立目录（--target）
#    ⚠️ 若用 3.10 的 pip 装，wheel 是 cp310，3.13 解释器 import numpy 必挂
"C:/.../python-3.13.x/python.exe" -m pip install --target site-packages \
  streamlit pandas numpy python-dotenv baostock networkx pyarrow
# 4) 拷贝应用源码 + 启动脚本
cp -r ../../linkage-stock-picker app
# 5) 实测 + 打 zip
./python/python.exe -m streamlit run app/app.py --server.port 8765 --server.headless true
```

产物结构：

```
linkage-stock-picker-portable/
├── python/            内嵌 Python 3.13
├── site-packages/     全部依赖（约 400MB）
├── app/               联动选股源码
├── 启动联动选股.bat    双击启动（localhost:8765）
└── 使用说明.txt
```

## 3. custom-pattern-screener

不打包：依赖 5308 个 parquet 数据文件，exe 化无意义；继续用 `pip install` 方式运行。
