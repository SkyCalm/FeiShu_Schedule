"""白名单打包，避免把用户课表、应用密钥或缓存分享到公共仓库。"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
FILES = ['app.py','schedule_parser.py','feishu.py','package_release.py','start.bat','start.sh',
         'README.md','LICENSE','.gitignore','.github/workflows/test.yml',
         'static/index.html','static/app.js','static/style.css','examples/demo.html',
         'examples/feishu-config.example.json',
         'docs/preview.png','tests/test_schedule.py','tests/test_feishu.py','tests/test_app.py','tests/test_package.py']


def main():
    output = ROOT/'dist'/'schedule-widget.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT/name, 'schedule-widget/'+name)
    print(output)


if __name__ == '__main__': main()
