#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import platform
import shutil
import hashlib
import argparse
import sys
from pathlib import Path
import subprocess

# ========= utilidades =========

def system2(cmd: str):
    code = os.system(cmd)
    if code != 0:
        sys.stderr.write(f"Error occurred when executing: `{cmd}`. Exiting.\n")
        sys.exit(-1)

def run_py(args_list):
    try:
        subprocess.run(args_list, check=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Error running {args_list} (exit={e.returncode}).\n")
        sys.exit(-1)

def ensure_pip(pyexe: Path):
    system2(f'"{pyexe}" -m ensurepip --upgrade')
    system2(f'"{pyexe}" -m pip install --upgrade pip')

def debug_list_dir(label: str, p: Path):
    print(f"[debug] {label}: {p}")
    try:
        if p.exists():
            for item in p.iterdir():
                try:
                    size = item.stat().st_size if item.is_file() else "-"
                    print(f"  - {item} ({'dir' if item.is_dir() else f'{size} bytes'})")
                except Exception:
                    print(f"  - {item}")
        else:
            print("  (no existe)")
    except Exception as e:
        print(f"  (error listando {p}: {e})")
        
def run_cmd(args_list):
    try:
        subprocess.run(args_list, check=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Error running {args_list} (exit={e.returncode}).\n")
        sys.exit(-1)

def find_signtool() -> str:
    """
    Devuelve la ruta completa a signtool.exe.
    Respeta la variable de entorno SIGNTOOL si está puesta.
    Si no, busca en PATH y en las rutas típicas del Windows SDK.
    """
    # 1) Respeta SIGNTOOL si el usuario la define
    env = os.environ.get("SIGNTOOL")
    if env and Path(env).exists():
        return str(Path(env))

    # 2) Prueba PATH
    exe = shutil.which("signtool.exe") or shutil.which("signtool")
    if exe:
        return exe

    # 3) Busca en instalaciones típicas del Windows SDK
    base = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if base.exists():
        candidates = []
        # Ordenar por versión descendente para tomar la más nueva
        for vdir in sorted(base.iterdir(), reverse=True):
            for arch in ("x64", "x86", "arm64", "arm"):
                p = vdir / arch / "signtool.exe"
                if p.exists():
                    candidates.append(str(p))
        if candidates:
            return candidates[0]

    # Si no lo encontró, falla con mensaje claro
    raise FileNotFoundError(
        "No se encontró signtool.exe. Instala el Windows 10/11 SDK o "
        "exporta SIGNTOOL con la ruta completa, por ejemplo:\n"
        r'SIGNTOOL="C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"'
    )
    
# ========= constantes =========

windows = platform.platform().startswith('Windows')
osx = platform.platform().startswith('Darwin') or platform.platform().startswith("macOS")

APP_NAME = "OFARCH"
APP_EXE  = APP_NAME + ("DESK.exe" if windows else "")
exe_path = Path("target/release") / APP_EXE

if windows:
    flutter_build_dir = 'build/windows/x64/runner/Release/'
elif osx:
    flutter_build_dir = 'build/macos/Build/Products/Release/'
else:
    flutter_build_dir = 'build/linux/x64/release/bundle/'
flutter_build_dir_2 = Path('flutter') / flutter_build_dir

DIST_DIR = Path("dist/win").resolve()
STAGING_DIR = DIST_DIR / "app"             # no-flutter
STAGING_DIR_FLUTTER = DIST_DIR / "app_fl"  # flutter

PORTABLE_DIR = Path("libs/portable").resolve()
PORTABLE_GENERATOR = PORTABLE_DIR / "generate.py"

ROOT_DIR = Path.cwd().resolve()
skip_cargo = False

# ========= parser / features =========

def get_version() -> str:
    with open("Cargo.toml", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("version"):
                return (line
                        .replace("version","")
                        .replace("=","")
                        .replace('"','')
                        .strip())
    return "0.0.0"

def make_parser():
    p = argparse.ArgumentParser(description="Build script.")
    p.add_argument("-f","--feature",dest="feature",metavar="N",type=str,nargs='+',default='',
                   help='Integrate features. "ALL" or "" (default).')
    p.add_argument("--flutter", action="store_true", help="Build flutter package", default=False)
    p.add_argument("--hwcodec", action="store_true", help="Enable feature hwcodec")
    p.add_argument("--vram", action="store_true", help="Enable feature vram")
    p.add_argument("--portable", action="store_true", help="Build windows portable")
    p.add_argument("--unix-file-copy-paste", action="store_true", help="Unix file copy paste feature")
    p.add_argument("--skip-cargo", action="store_true", help="Skip cargo build (solo flutter+Linux)")
    if windows:
        p.add_argument("--skip-portable-pack", action="store_true", help="(Windows+Flutter) saltar empaquetado")
    p.add_argument("--package", type=str)
    if osx:
        p.add_argument("--screencapturekit", action="store_true", help="Enable screencapturekit")
    return p

def get_features(args):
    feats = ['inline'] if not args.flutter else []
    if args.hwcodec: feats.append('hwcodec')
    if args.vram: feats.append('vram')
    if args.flutter: feats.append('flutter')
    if args.unix_file_copy_paste: feats.append('unix-file-copy-paste')
    if osx and getattr(args, "screencapturekit", False): feats.append('screencapturekit')
    print("features:", feats)
    return ",".join(feats)

# ========= helpers específicos =========

def copy_sciter_if_any(dst_dir: Path):
    """Copia sciter.dll si existe en ubicaciones típicas."""
    candidates = [
        Path("sciter.dll"),
        Path("target/release/sciter.dll"),
        Path("libs/sciter/sciter.dll"),
        Path("res/sciter.dll"),
    ]
    for c in candidates:
        if c.exists():
            shutil.copy2(c, dst_dir / "sciter.dll")
            return

def build_portable_packer_stub() -> Path:
    """
    Compila el stub/packer desde libs/portable/Cargo.toml y devuelve
    la ruta del .exe resultante en target/release.
    """
    cargo_toml = Path("libs/portable/Cargo.toml")
    if not cargo_toml.exists():
        print(f"[ERROR] No existe {cargo_toml}. No puedo compilar el stub del instalador.")
        sys.exit(-1)

    # Compila el packer (sin afectar el resto del workspace)
    system2('cargo build --manifest-path "libs/portable/Cargo.toml" --release')

    # Busca nombres habituales del packer
    tr = Path("target/release").resolve()
    candidates = []
    patterns = [
        "*portable*packer*.exe",     # p.ej. rustdesk-portable-packer.exe
        "*portable-packer*.exe",
        "*packer*.exe",
    ]
    for pat in patterns:
        candidates.extend(list(tr.glob(pat)))

    # filtra por tamaño > ~100KB para evitar exe triviales
    candidates = [p for p in candidates if p.is_file() and p.stat().st_size > 100_000]

    if not candidates:
        print("[ERROR] No se encontró ningún stub/packer compilado en target/release.")
        print("Listado de .exe en target/release:")
        for p in sorted(tr.glob("*.exe")):
            print("  -", p)
        sys.exit(-1)

    # el más reciente
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.stat().st_size), reverse=True)
    return candidates[0]

def run_generate_and_make_installer(staging_dir: Path, app_exe_name: str, version: str) -> Path:
    """
    Ejecuta generate.py para crear data.bin/app_metadata y luego
    compila y copia el stub como instalador final.
    """
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # 1) generate.py
    py = Path(sys.executable).resolve()
    if not PORTABLE_GENERATOR.exists():
        print(f"[ERROR] Falta {PORTABLE_GENERATOR}")
        sys.exit(-1)

    # -e debe ser el ejecutable que se lanzará tras instalar (relativo o nombre)
    exe_to_run = app_exe_name

    os.chdir(PORTABLE_DIR)
    ensure_pip(py)
    system2(f'"{py}" -m pip install -r requirements.txt')
    run_py([
        str(py), str(PORTABLE_GENERATOR.name),
        "-f", str(staging_dir.resolve()),
        "-o", str(DIST_DIR.resolve()),
        "-e", exe_to_run
    ])
    os.chdir(ROOT_DIR)

    data_bin = DIST_DIR / "data.bin"
    if not data_bin.exists():
        print("[ERROR] generate.py no produjo data.bin. Revisa rutas -f/-o/-e.")
        debug_list_dir("DIST_DIR", DIST_DIR)
        debug_list_dir("STAGING_DIR", staging_dir)
        sys.exit(-1)

    # 2) compilar y copiar el stub
    stub = build_portable_packer_stub()
    final_installer = DIST_DIR / f"{APP_NAME}-{version}-install.exe"
    shutil.copy2(stub, final_installer)

    # 3) firma opcional
    pa = os.environ.get('P')
    signtool_bin = find_signtool()
    if pa and final_installer.exists():
        run_cmd([
            signtool_bin,
            'sign', '/a', '/v',
            '/fd', 'sha256', '/td', 'sha256',
            '/p', pa,
            '/debug',
            '/f', str(Path('cert.pfx')),
            '/tr', 'http://timestamp.digicert.com',
            str(final_installer)
        ])
    else:
        print('Not signed')

    print(f'output location: {final_installer}')
    return final_installer

# ========= Windows (Flutter) =========

def build_flutter_windows(version: str, features: str, skip_portable_pack: bool):
    # 1) Rust lib para Flutter
    if not skip_cargo:
        cmd = 'cargo build --lib --release'
        if features:
            cmd += f' --features {features}'
        system2(cmd)
        if not Path("target/release/librustdesk.dll").exists():
            print("cargo build failed, missing librustdesk.dll")
            sys.exit(-1)

    # 2) Flutter
    os.chdir('flutter')
    system2('flutter build windows --release')
    os.chdir(ROOT_DIR)

    # 3) Copiar virtual display dll (si existe)
    vdisp = Path('target/release/deps/dylib_virtual_display.dll')
    if vdisp.exists():
        shutil.copy2(vdisp, flutter_build_dir_2 / 'dylib_virtual_display.dll')

    if skip_portable_pack:
        return

    # 4) Staging con SOLO la app
    shutil.rmtree(DIST_DIR, ignore_errors=True)
    STAGING_DIR_FLUTTER.mkdir(parents=True, exist_ok=True)
    for item in flutter_build_dir_2.iterdir():
        dst = STAGING_DIR_FLUTTER / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    # renombrar rustdesk.exe -> OFARCHDESK.exe si aplica
    src_exe = STAGING_DIR_FLUTTER / 'rustdesk.exe'
    dst_exe = STAGING_DIR_FLUTTER / APP_EXE
    if src_exe.exists():
        src_exe.replace(dst_exe)
    elif not dst_exe.exists():
        raise SystemExit(f"[ERROR] No se encontró {APP_EXE} en {STAGING_DIR_FLUTTER}")

    # 5) generate + stub => instalador
    run_generate_and_make_installer(STAGING_DIR_FLUTTER, APP_EXE, version)

# ========= Windows (NO Flutter) =========

def build_windows_non_flutter(version: str, features: str):
    # 1) Build Rust (bin)
    cmd = 'cargo build --release'
    if features:
        cmd += f' --features {features}'
    system2(cmd)

    # 2) firma opcional del exe
    pa = os.environ.get('P')
    signtool_bin = find_signtool()
    if pa and exe_path.exists(): run_cmd([ signtool_bin, 'sign', '/a', '/v', '/fd', 'sha256', '/td', 'sha256', '/p', pa, '/debug', '/f', str(Path('cert.pfx')), '/tr', 'http://timestamp.digicert.com', str(exe_path) ])
    else:
        print('Not signed')

    # 3) Staging limpio con SOLO lo necesario
    shutil.rmtree(DIST_DIR, ignore_errors=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    if not exe_path.exists():
        raise SystemExit(f"[ERROR] No existe {exe_path}")
    shutil.copy2(exe_path, STAGING_DIR / APP_EXE)

    # DLLs requeridas (sciter, etc.)
    copy_sciter_if_any(STAGING_DIR)

    # 4) generate + stub => instalador
    run_generate_and_make_installer(STAGING_DIR, APP_EXE, version)

# ========= main =========

def main():
    global skip_cargo
    parser = make_parser()
    args = parser.parse_args()

    # limpia exe previo
    try:
        if exe_path.exists():
            exe_path.unlink()
    except Exception:
        pass

    version = get_version()
    features = get_features(args)
    flutter = args.flutter

    # inline-sciter (no-flutter), si existe
    if not flutter:
        py = Path(sys.executable).resolve()
        inline = (Path("res") / "inline-sciter.py").resolve()
        if inline.exists():
            run_py([str(py), str(inline)])
        else:
            print(f"[warn] No existe {inline}, se omite.")

    if getattr(args, "skip_cargo", False):
        skip_cargo = True

    if windows:
        # dynlib virtual display (si existe el proyecto)
        vdisp_dir = Path('libs/virtual_display/dylib')
        if vdisp_dir.exists():
            os.chdir(vdisp_dir)
            system2('cargo build --release')
            os.chdir(ROOT_DIR)

        if flutter:
            build_flutter_windows(version, features, getattr(args, "skip_portable_pack", False))
        else:
            build_windows_non_flutter(version, features)

    else:
        print("Este build.py está centrado en Windows. Empaquetado Linux/macOS omitido.")

if __name__ == "__main__":
    main()
