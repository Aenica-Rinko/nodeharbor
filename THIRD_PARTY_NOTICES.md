# 第三方组件

NodeHarbor 使用 GPL-3.0-only。源码版通过包管理器安装依赖；Windows EXE 包含运行时与依赖，并在 `licenses/` 中附带其完整许可证。Mihomo 由用户自行下载，发布包不包含其二进制文件。

| 组件 | 许可与来源 |
|---|---|
| Python / Tcl/Tk | [Python 许可及第三方许可](https://docs.python.org/3/license.html) |
| Requests | [Apache-2.0](https://github.com/psf/requests/blob/main/LICENSE) |
| PyYAML | [MIT](https://github.com/yaml/pyyaml/blob/main/LICENSE) |
| urllib3 | [MIT](https://github.com/urllib3/urllib3/blob/main/LICENSE.txt) |
| idna | [BSD-3-Clause](https://github.com/kjd/idna/blob/master/LICENSE.md) |
| charset-normalizer | [MIT](https://github.com/jawah/charset_normalizer/blob/master/LICENSE) |
| certifi | [MPL-2.0](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| OpenSSL 3 | [Apache-2.0](docs/licenses/OpenSSL.txt) |
| Tcl / Tk | [Tcl](docs/licenses/Tcl.txt)、[Tk](docs/licenses/Tk.txt) |
| PyInstaller bootloader | [GPL-2.0-or-later，包含 bootloader 分发例外](https://pyinstaller.org/en/stable/license.html) |
| Mihomo | [GPL-3.0](https://github.com/MetaCubeX/mihomo/blob/Meta/LICENSE) |

开发工具 pytest、build、setuptools 不随应用捆绑。Mihomo 和 Xray 检测核心由用户自行提供。重新分发包含第三方软件的安装包时，应分别核对对应版本的许可证、版权声明和源码提供要求；本文件不替代各组件许可证。
