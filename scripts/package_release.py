import argparse
import io
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

from app_build import app

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--platform', choices=['android', 'linux', 'windows', 'macos'], required=True)
parser.add_argument('--abi', action='append', choices=['arm64-v8a', 'armeabi-v7a', 'x86_64'])
parser.add_argument('--identity', default='',
                    help='macOS 签名身份；默认 ad-hoc 签名（-），有 Developer ID 时传证书名')
options = parser.parse_args()
match = re.search(r'^version:\s*([\w.+-]+)\s*$', (root / 'pubspec.yaml').read_text(encoding='utf-8'), re.MULTILINE)
if not match:
    raise SystemExit('pubspec.yaml 缺少合法版本号。')
version = match.group(1)
output = root / 'dist' / options.platform
output.mkdir(parents=True, exist_ok=True)
artifacts = []


def run(arguments, cwd=None):
    subprocess.run([str(item) for item in arguments], cwd=cwd, check=True)


def macho_architectures(path):
    result = subprocess.run(['lipo', '-info', str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'不是 Mach-O 文件：{path}')
    return result.stdout.strip()


def verify_macos_application(application):
    info = plistlib.loads((application / 'Contents' / 'Info.plist').read_bytes())
    executable = application / 'Contents' / 'MacOS' / info['CFBundleExecutable']
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit('macOS 包缺少可执行文件。')
    architectures = macho_architectures(executable)
    if 'arm64' not in architectures or 'x86_64' not in architectures:
        raise SystemExit('macOS 包应同时包含 arm64 和 x86_64：' + architectures)
    symbols = subprocess.run(['nm', '-gU', str(executable)], capture_output=True, text=True).stdout
    for symbol in ['_DuanjuRequest', '_DuanjuFree']:
        if symbol not in symbols:
            raise SystemExit('macOS 包缺少 FFI 入口：' + symbol)
    frameworks = application / 'Contents' / 'Frameworks'
    for required in ['App.framework', 'FlutterMacOS.framework', 'Mpv.framework', 'ffmpegkit.framework']:
        if not (frameworks / required).is_dir():
            raise SystemExit('macOS 包缺少框架：' + required)


def sign_macos_application(application, identity):
    flags = ['codesign', '--force', '--sign', identity or '-']
    if identity and identity != '-':
        flags += ['--options', 'runtime', '--timestamp']
    for framework in sorted((application / 'Contents' / 'Frameworks').glob('*')):
        run(flags + [framework])
    entitlements = root / 'macos' / 'Runner' / 'Release.entitlements'
    run(flags + ['--entitlements', entitlements, application])


def draw_dmg_background(path, display):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    width, height = 620, 380
    image = Image.new('RGB', (width, height), '#F5F9FC')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 96), fill='#E8F1FA')
    arrow = '#B9CBDD'
    draw.line((300, 178, 400, 178), fill=arrow, width=6)
    draw.line((384, 166, 402, 178), fill=arrow, width=6)
    draw.line((384, 190, 402, 178), fill=arrow, width=6)
    font_path = None
    for candidate in ('/System/Library/Fonts/PingFang.ttc',
                      '/System/Library/Fonts/Hiragino Sans GB.ttc',
                      '/System/Library/Fonts/STHeiti Medium.ttc'):
        if Path(candidate).exists():
            font_path = candidate
            break
    title = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
    hint = ImageFont.truetype(font_path, 15) if font_path else ImageFont.load_default()
    draw.text((width / 2, 48), display, font=title, fill='#3B4552', anchor='mm')
    draw.text((width / 2, 292), f'把「{display}」拖到右侧 Applications 完成安装',
              font=hint, fill='#7A8796', anchor='mm')
    image.save(path)
    return path


def build_macos_dmg(display, staging, staged, target):
    background = draw_dmg_background(staging.parent / 'dmg-background.png', display)
    installed = subprocess.run([sys.executable, '-c', 'import dmgbuild'],
                               capture_output=True).returncode == 0
    if installed:
        settings = staging.parent / 'dmg-settings.py'
        layout = [
            f'volume_name = {display!r}',
            "format = 'UDZO'",
            f'files = [{staged.name!r}]',
            "symlinks = {'Applications': '/Applications'}",
            "default_view = 'icon-view'",
            'icon_size = 128',
            'text_size = 13',
            'window_rect = ((240, 140), (620, 380))',
            f'icon_locations = {{{staged.name!r}: (155, 175), "Applications": (465, 175)}}',
            'show_status_bar = False',
            'show_tab_view = False',
            'show_toolbar = False',
            'show_pathbar = False',
            'show_sidebar = False',
            'arrange_by = None',
        ]
        if background is not None:
            layout.append(f'background = {str(background)!r}')
        settings.write_text('\n'.join(layout) + '\n')
        run([sys.executable, '-m', 'dmgbuild', '-s', settings, display, target], cwd=staging)
    else:
        print('未安装 dmgbuild，退回普通 dmg（没有拖拽安装的窗口布局）；'
              '需要时执行 pip install dmgbuild。', flush=True)
        os.symlink('/Applications', staging / 'Applications')
        run(['hdiutil', 'create', '-volname', display, '-srcfolder', staging, '-ov',
             '-fs', 'HFS+', '-format', 'UDZO', '-imagekey', 'zlib-level=9', target])


def package_macos(application):
    display = app.display_name
    staging = root / 'build' / 'macos-dmg'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staged = staging / f'{display}.app'
    shutil.copytree(application, staged, symlinks=True)
    plist_path = staged / 'Contents' / 'Info.plist'
    info = plistlib.loads(plist_path.read_bytes())
    info['CFBundleDisplayName'] = display
    info['CFBundleName'] = display
    info['CFBundleIdentifier'] = app.bundle_identifier
    plist_path.write_bytes(plistlib.dumps(info))
    sign_macos_application(staged, options.identity)
    verify_macos_application(staged)
    target = output / f'{app.slug}-{version}-macos-universal.dmg'
    if target.exists():
        target.unlink()
    build_macos_dmg(display, staging, staged, target)
    mount = root / 'build' / 'macos-dmg-mount'
    if mount.exists():
        shutil.rmtree(mount)
    mount.mkdir(parents=True)
    try:
        run(['hdiutil', 'attach', target, '-nobrowse', '-readonly', '-mountpoint', mount])
        if not (mount / f'{display}.app').is_dir():
            raise SystemExit('dmg 里缺少应用：' + display)
        if not (mount / 'Applications').is_symlink():
            raise SystemExit('dmg 里缺少 Applications 快捷方式。')
        if not (mount / '.DS_Store').is_file():
            print('警告：dmg 里没有 .DS_Store，双击后不会按预期摆放图标。', flush=True)
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], capture_output=True)
    return target


def add_tar_member(archive, path, arcname):
    info = archive.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = 'root'
    info.gname = 'root'
    if info.isreg():
        with path.open('rb') as handle:
            archive.addfile(info, handle)
    else:
        archive.addfile(info)


def write_debian_archive(target, members):
    with target.open('wb') as archive:
        archive.write(b'!<arch>\n')
        for name, path in members:
            data = path.read_bytes()
            header = (f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}').encode()
            archive.write(header + b'`\n')
            archive.write(data)
            if len(data) % 2:
                archive.write(b'\n')


def linux_desktop_entry():
    return '\n'.join([
        '[Desktop Entry]',
        'Type=Application',
        f'Name={app.display_name}',
        'Comment=独立运行的多端短剧应用',
        f'Exec={app.slug} %U',
        f'Icon={app.slug}',
        'Terminal=false',
        'Categories=AudioVideo;Video;Player;',
        f'StartupWMClass={app.bundle_identifier}',
        '',
    ])


def package_linux(bundle):
    required = [app.slug, 'libduanju_core.so', 'lib/libflutter_linux_gtk.so',
                'lib/libapp.so', 'lib/libffmpegkit.so', 'data/icudtl.dat']
    missing = [name for name in required if not (bundle / name).is_file()]
    if missing:
        raise SystemExit('Linux 安装包缺少文件：' + ', '.join(missing))
    nm = shutil.which('nm')
    if nm:
        symbols = subprocess.run([nm, '-D', '--defined-only', str(bundle / 'libduanju_core.so')],
                                 capture_output=True, text=True).stdout
        for symbol in ['DuanjuRequest', 'DuanjuFree']:
            if symbol not in symbols:
                raise SystemExit('Linux 核心库缺少 FFI 入口：' + symbol)
    icon = root / 'macos' / 'Runner' / 'Assets.xcassets' / 'AppIcon.appiconset' / 'app_icon_512.png'
    if not icon.is_file():
        raise SystemExit('缺少图标：' + str(icon))

    staging = root / 'build' / 'linux-package'
    if staging.exists():
        shutil.rmtree(staging)
    application = staging / 'usr' / 'lib' / app.slug
    shutil.copytree(bundle, application, symlinks=True)
    binary = staging / 'usr' / 'bin' / app.slug
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.symlink_to(f'../lib/{app.slug}/{app.slug}')
    desktop = staging / 'usr' / 'share' / 'applications' / f'{app.bundle_identifier}.desktop'
    desktop.parent.mkdir(parents=True)
    desktop.write_text(linux_desktop_entry(), encoding='utf-8')
    icon_target = staging / 'usr' / 'share' / 'icons' / 'hicolor' / '512x512' / 'apps' / f'{app.slug}.png'
    icon_target.parent.mkdir(parents=True)
    shutil.copy2(icon, icon_target)
    copyright_dir = staging / 'usr' / 'share' / 'doc' / app.slug
    copyright_dir.mkdir(parents=True)
    shutil.copy2(root / 'LICENSE', copyright_dir / 'copyright')

    installed = sum(path.stat().st_size for path in application.rglob('*') if path.is_file())
    control = '\n'.join([
        f'Package: {app.slug}',
        f'Version: {version}',
        'Section: video',
        'Priority: optional',
        'Architecture: amd64',
        f'Installed-Size: {max(1, (installed + 1023) // 1024)}',
        'Maintainer: guoapp <guoapp@users.noreply.github.com>',
        'Depends: libc6 (>= 2.35), libgtk-3-0, libmpv2 | libmpv1, libjson-glib-1.0-0, '
        'libepoxy0, libstdc++6, ffmpeg',
        f'Description: {app.display_name}',
        ' 独立运行的多端短剧应用。',
        '',
    ])

    working = root / 'build' / 'linux-deb'
    if working.exists():
        shutil.rmtree(working)
    working.mkdir(parents=True)
    control_tar = working / 'control.tar.gz'
    refresh = (
        'if command -v update-desktop-database >/dev/null 2>&1; then\n'
        '  update-desktop-database -q || true\n'
        'fi\n'
        'if command -v gtk-update-icon-cache >/dev/null 2>&1; then\n'
        '  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true\n'
        'fi\n'
    )
    with tarfile.open(control_tar, 'w:gz') as archive:
        for name, text, mode in [
            ('./control', control, 0o644),
            ('./postinst', '#!/bin/sh\nset -e\n' + refresh + 'exit 0\n', 0o755),
            ('./postrm', '#!/bin/sh\nset -e\n' + refresh + 'exit 0\n', 0o755),
        ]:
            data = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            info.mtime = int(time.time())
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            archive.addfile(info, io.BytesIO(data))
    data_tar = working / 'data.tar.gz'
    with tarfile.open(data_tar, 'w:gz') as archive:
        for path in sorted(staging.rglob('*')):
            add_tar_member(archive, path, './' + path.relative_to(staging).as_posix())
    debian_binary = working / 'debian-binary'
    debian_binary.write_text('2.0\n', encoding='ascii')

    portable = output / f'{app.slug}-{version}-linux-x64.tar.gz'
    desktop_source = working / f'{app.slug}.desktop'
    desktop_source.write_text(linux_desktop_entry(), encoding='utf-8')
    with tarfile.open(portable, 'w:gz') as archive:
        add_tar_member(archive, bundle, './' + app.slug)
        for path in sorted(bundle.rglob('*')):
            add_tar_member(archive, path, f'./{app.slug}/{path.relative_to(bundle).as_posix()}')
        add_tar_member(archive, desktop_source, f'./{app.slug}.desktop')
        add_tar_member(archive, icon, f'./{app.slug}.png')
        add_tar_member(archive, root / 'LICENSE', './LICENSE')

    target = output / f'{app.slug}-{version}-linux-x64.deb'
    write_debian_archive(target, [('debian-binary', debian_binary),
                                  ('control.tar.gz', control_tar),
                                  ('data.tar.gz', data_tar)])
    if shutil.which('dpkg-deb'):
        run(['dpkg-deb', '--info', target])
        contents = subprocess.run(['dpkg-deb', '--contents', target], capture_output=True, text=True)
        if contents.returncode != 0 or f'./usr/lib/{app.slug}/{app.slug}' not in contents.stdout:
            raise SystemExit('deb 内容校验失败：' + contents.stderr.strip())
    elif shutil.which('ar'):
        members = subprocess.run(['ar', 't', target], capture_output=True, text=True).stdout.split()
        if members != ['debian-binary', 'control.tar.gz', 'data.tar.gz']:
            raise SystemExit('deb 归档校验失败：' + ' '.join(members))
    return [portable, target]


def windows_product_name(path):
    import ctypes
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ''
    data = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW(str(path), 0, size, data)
    value = ctypes.c_wchar_p()
    length = ctypes.c_uint()
    version.VerQueryValueW(data, r'\StringFileInfo\040904e4\ProductName', ctypes.byref(value), ctypes.byref(length))
    return value.value or ''


def find_inno_compiler():
    candidates = [shutil.which('iscc')]
    for variable, folder in (('LOCALAPPDATA', 'Programs'), ('ProgramFiles(x86)', ''), ('ProgramFiles', '')):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / folder / 'Inno Setup 6' / 'ISCC.exe'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit('找不到 Inno Setup 6 的 ISCC.exe，请先安装：winget install JRSoftware.InnoSetup')


def package_windows_installer(bundle, bundle_executable):
    base_name = f'{app.slug}-{version}-windows-x64-setup'
    run([find_inno_compiler(), '/Q',
         f'/DAppId={app.bundle_identifier}',
         f'/DAppName={app.display_name}',
         f'/DAppVersion={version}',
         f'/DNumericVersion={version.replace("+", ".")}',
         f'/DSlug={app.slug}',
         f'/DBundleExecutable={bundle_executable}',
         f'/DSourceDir={bundle}',
         f'/DOutputDir={output}',
         f'/DOutputBaseName={base_name}',
         root / 'windows' / 'installer' / 'setup.iss'])
    target = output / f'{base_name}.exe'
    if not target.is_file():
        raise SystemExit('Windows 安装包没有生成：' + str(target))
    return target


if options.platform == 'android':
    for abi in options.abi or ['arm64-v8a', 'armeabi-v7a', 'x86_64']:
        source = root / 'build' / 'app' / 'outputs' / 'flutter-apk' / f'app-{abi}-release.apk'
        if not source.is_file():
            raise SystemExit('缺少 APK：' + str(source))
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            required = [f'lib/{abi}/{library}' for library in
                        ['libduanju_core.so', 'libflutter.so', 'libapp.so', 'libmpv.so', 'libffmpegkit.so']]
            missing = set(required) - names
            if missing:
                raise SystemExit('APK 缺少原生库：' + ', '.join(sorted(missing)))
        target = output / f'{app.slug}-{version}-{abi}.apk'
        shutil.copy2(source, target)
        artifacts.append(target)
elif options.platform == 'linux':
    bundle = root / 'build' / 'linux' / 'x64' / 'release' / 'bundle'
    if not bundle.is_dir():
        raise SystemExit('缺少 Linux 应用：' + str(bundle))
    artifacts.extend(package_linux(bundle))
elif options.platform == 'windows':
    bundle = root / 'build' / 'windows' / 'x64' / 'runner' / 'Release'
    bundle_executable = 'guoapp.exe'
    required = [bundle_executable, 'duanju_core.dll', 'flutter_windows.dll', 'libffmpegkit.dll',
                'libmpv-2.dll', 'msvcp140.dll', 'vcruntime140.dll',
                'data/icudtl.dat', 'data/app.so']
    missing = [name for name in required if not (bundle / name).is_file()]
    if missing:
        raise SystemExit('Windows 安装包缺少文件：' + ', '.join(missing))
    built = windows_product_name(bundle / bundle_executable)
    if built != app.bundle_identifier:
        raise SystemExit(f'构建产物是 {built or "未知版本"}，与要打包的 {app.bundle_identifier} 不符，'
                         '请先用 build_windows.py 以相同参数重新构建。')
    target = output / f'{app.slug}-{version}-windows-x64.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(bundle.rglob('*')):
            if source.is_file():
                relative = source.relative_to(bundle).as_posix()
                if relative == bundle_executable:
                    relative = app.slug + '.exe'
                archive.write(source, relative)
        archive.writestr('userdata/', '')
    artifacts.append(target)
    artifacts.append(package_windows_installer(bundle, bundle_executable))
else:
    if options.platform != 'macos':
        raise SystemExit('未知平台：' + options.platform)
    application = root / 'build' / 'macos' / 'Build' / 'Products' / 'Release' / 'duanju_app.app'
    if not application.is_dir():
        raise SystemExit('缺少 macOS 应用：' + str(application))
    verify_macos_application(application)
    artifacts.append(package_macos(application))

for artifact in artifacts:
    print(artifact)
