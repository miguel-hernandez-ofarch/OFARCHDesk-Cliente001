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
import re
from glob import glob

ROOT_DIR = Path(__file__).resolve().parent

# Branding configurable
ARTIFACT_PREFIX = os.environ.get("ARTIFACT_PREFIX", "OFARCHDesk")

# Rutas comunes del proyecto Flutter
FLUTTER_DIR_DEFAULT = ROOT_DIR / "flutter"

def run(cmd, cwd=None, env=None, shell=False):
    """Wrapper con trazas para ejecutar comandos."""
    print(f"[RUN] {cmd}  (cwd={cwd or os.getcwd()})")
    subprocess.check_call(cmd, cwd=cwd or os.getcwd(), env=env or os.environ.copy(), shell=shell)

def file_exists(p: Path) -> bool:
    return p.exists() and p.is_file()

def dir_exists(p: Path) -> bool:
    return p.exists() and p.is_dir()

def read_version_from_cargo() -> str:
    cargo = ROOT_DIR / "Cargo.toml"
    if not file_exists(cargo):
        return ""
    txt = cargo.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else ""

def version_value(cli_version: str) -> str:
    return cli_version or os.environ.get("VERSION") or read_version_from_cargo() or "dev"

def arch_suffix() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m or "unknown"

def ensure_flutter(flutter_dir: Path):
    run(["flutter", "--version"], cwd=flutter_dir)
    run(["flutter", "pub", "get"], cwd=flutter_dir)

def cargo_build_lib(features: str):
    env = os.environ.copy()
    if platform.system() == "Darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = "10.14"
    cmd = ["cargo", "build", "--lib", "--release"]
    if features:
        cmd += ["--features", features]
    run(cmd, cwd=str(ROOT_DIR), env=env)

def copy_macos_dylib_variants():
    # En algunos repos se genera liblibrustdesk.dylib y se requiere duplicar a librustdesk.dylib
    src = ROOT_DIR / "target" / "release" / "liblibrustdesk.dylib"
    dst = ROOT_DIR / "target" / "release" / "librustdesk.dylib"
    if file_exists(src):
        shutil.copy2(src, dst)
        print(f"[INFO] Copiado {src.name} -> {dst.name}")

def find_macos_app() -> Path:
    candidates = [
        ROOT_DIR / "flutter" / "build" / "macos" / "Build" / "Products" / "Release",
        ROOT_DIR / "build" / "macos" / "Build" / "Products" / "Release",
        ROOT_DIR / "target" / "release" / "bundle" / "osx",
    ]
    for base in candidates:
        if dir_exists(base):
            apps = sorted(Path(base).glob("*.app"))
            if apps:
                return apps[0]
    return Path()

def find_windows_release_dir(flutter_dir: Path) -> Path:
    cand = flutter_dir / "build" / "windows" / "x64" / "runner" / "Release"
    if dir_exists(cand):
        return cand
    for p in (flutter_dir / "build").rglob("runner"):
        rel = p / "Release"
        if dir_exists(rel):
            return rel
    return Path()

def find_windows_exe(flutter_dir: Path) -> Path:
    rel = find_windows_release_dir(flutter_dir)
    if not dir_exists(rel):
        return Path()
    exes = sorted(rel.glob("*.exe"), key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return exes[0] if exes else Path()

def build_flutter_windows(flutter_dir: Path):
    ensure_flutter(flutter_dir)
    run(["flutter", "build", "windows", "--release"], cwd=flutter_dir)

def build_flutter_macos(flutter_dir: Path):
    ensure_flutter(flutter_dir)
    run(["flutter", "build", "macos", "--release"], cwd=flutter_dir)

def build_flutter_android(flutter_dir: Path):
    ensure_flutter(flutter_dir)
    run(["flutter", "build", "apk", "--release"], cwd=flutter_dir)

def maybe_copy_service_into_app(app_path: Path):
    svc = ROOT_DIR / "target" / "release" / "service"
    dest = app_path / "Contents" / "MacOS" / "service"
    if file_exists(svc):
        shutil.copy2(svc, dest)
        print(f"[INFO] Copiado service -> {dest}")

def find_create_dmg() -> Path:
    try:
        out = subprocess.check_output(["bash", "-lc", "command -v create-dmg || true"], text=True).strip()
        if out:
            try:
                real = subprocess.check_output(["bash", "-lc", f"readlink -f {out} || echo {out}"], text=True).strip()
                return Path(real)
            except Exception:
                return Path(out)
    except Exception:
        pass
    return Path()

def patch_create_dmg(path: Path):
    if not file_exists(path):
        return
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
        new = re.sub(r"MAXIMUM_UNMOUNTING_ATTEMPTS=3", "MAXIMUM_UNMOUNTING_ATTEMPTS=7", txt)
        if new != txt:
            path.write_text(new, encoding="utf-8")
            print("[INFO] create-dmg parchado para aumentar intentos de desmontaje")
    except Exception as e:
        print(f"[WARN] No se pudo parchar create-dmg: {e}")

def create_unsigned_dmg(app_path: Path, version: str):
    tool = find_create_dmg()
    if not file_exists(tool):
        print("[WARN] No se encontró create-dmg en PATH. Saltando creación de DMG.")
        return None
    patch_create_dmg(tool)
    dmg = ROOT_DIR / f"{ARTIFACT_PREFIX}-{version}-{arch_suffix()}.dmg"
    app_name = app_path.name
    cmd = [
        str(tool),
        "--volname", f"{ARTIFACT_PREFIX}",
        "--overwrite",
        "--window-pos", "200", "120",
        "--window-size", "800", "400",
        "--icon-size", "120",
        "--icon", app_name, "200", "190",
        "--hide-extension", app_name,
        "--app-drop-link", "600", "185",
        str(dmg),
        str(app_path),
    ]
    run(cmd, cwd=str(ROOT_DIR))
    print(f"[OK] DMG creado: {dmg.name}")
    return dmg

def windows_portable_pack(flutter_dir: Path, version: str, skip_portable_pack: bool):
    if skip_portable_pack:
        print("[INFO] Empaquetado portable omitido por bandera.")
        return None

    candidates = [
        ROOT_DIR / "libs" / "portable" / "generate.py",
        ROOT_DIR / "generate.py",
    ]
    gen = next((c for c in candidates if file_exists(c)), None)
    if not gen:
        print("[WARN] No se encontró generate.py para portable. Saltando empaquetado.")
        return None

    exe_real = find_windows_exe(flutter_dir)
    if not exe_real:
        raise RuntimeError("No se encontró el .exe generado por Flutter en Windows.")

    out_dir = gen.parent
    rel_exe_from_out = os.path.relpath(exe_real, start=out_dir).replace("\\", "/")
    cmd = [
        sys.executable if sys.executable else "python3",
        str(gen.name),
        "-f", "../../" + str(exe_real.parent).replace("\\", "/"),
        "-o", ".",
        "-e", rel_exe_from_out,
    ]
    run(cmd, cwd=str(out_dir))

    portable_exe = out_dir / "rustdesk_portable.exe"
    if file_exists(portable_exe):
        final = ROOT_DIR / f"{ARTIFACT_PREFIX}-{version}-install.exe"
        shutil.move(str(portable_exe), str(final))
        print(f"[OK] Instalador portable creado: {final.name}")
        return final

    print("[WARN] No se generó rustdesk_portable.exe. Verifica generate.py.")
    return None

def rename_android_apk(flutter_dir: Path, version: str):
    apk = flutter_dir / "build" / "app" / "outputs" / "flutter-apk" / "app-release.apk"
    if file_exists(apk):
        final = ROOT_DIR / f"{ARTIFACT_PREFIX}-{version}-android.apk"
        shutil.copy2(apk, final)
        print(f"[OK] APK copiado como {final.name}")
        return final
    print("[WARN] APK release no encontrado. Revisa la salida de Flutter.")
    return None

def main():
    parser = argparse.ArgumentParser(description="Build script unificado para Windows, macOS y Android")
    parser.add_argument("--platform", choices=["windows", "macos", "android", "auto"], default="auto",
                        help="Selecciona plataforma objetivo")
    parser.add_argument("--flutter", action="store_true", help="Construir app Flutter para la plataforma")
    parser.add_argument("--hwcodec", action="store_true", help="Activa la feature hwcodec de cargo")
    parser.add_argument("--features", default="", help="Features extra para cargo separadas por coma")
    parser.add_argument("--version", default="", help="Version para nombrar artefactos")
    parser.add_argument("--skip-portable-pack", action="store_true", help="Omite empaquetado portable en Windows")
    parser.add_argument("--flutter-dir", default=str(FLUTTER_DIR_DEFAULT), help="Ruta del proyecto Flutter")
    parser.add_argument("--unix-file-copy-paste",
                    dest="feat_unix_file_copy_paste",
                    action="store_true",
                    help="Activa la feature unix-file-copy-paste en cargo")

    parser.add_argument("--screencapturekit",
                    dest="feat_screencapturekit",
                    action="store_true",
                    help="Activa la feature screencapturekit en cargo")
    args = parser.parse_args()

    version = version_value(args.version)
    flutter_dir = Path(args.flutter_dir).resolve()

    # Features por defecto
    feats = []
    if args.hwcodec:
        feats.append("hwcodec")
    if args.flutter:
        feats.append("flutter")

    # Auto para macOS, puedes mantenerlo o quitarlo. Duplicados se eliminan más abajo.
    if platform.system() == "Darwin":
        feats += ["unix-file-copy-paste", "screencapturekit"]

    # Flags explícitos del workflow
    if getattr(args, "feat_unix_file_copy_paste", False):
        feats.append("unix-file-copy-paste")
    if getattr(args, "feat_screencapturekit", False):
        feats.append("screencapturekit")

    # Mezcla con --features manuales si las pasas
    manual_feats = [f.strip() for f in args.features.split(",") if f.strip()]
    all_feats = ",".join(sorted(set(feats + manual_feats)))

    # Plataforma objetivo
    target = args.platform
    if target == "auto":
        sysname = platform.system()
        if sysname == "Windows":
            target = "windows"
        elif sysname == "Darwin":
            target = "macos"
        else:
            target = "android" if "ANDROID_HOME" in os.environ else "windows"

    print(f"[INFO] Plataforma objetivo: {target}")
    print(f"[INFO] Version: {version}")
    print(f"[INFO] Features cargo: {all_feats or '(ninguna)'}")
    print(f"[INFO] Flutter dir: {flutter_dir}")

    if target == "windows":
        if args.flutter:
            build_flutter_windows(flutter_dir)
        try:
            windows_portable_pack(flutter_dir, version, args.skip_portable_pack)
        except Exception as e:
            print(f"[WARN] Empaquetado portable falló: {e}")
        print("[OK] Build Windows completado.")
        return

    if target == "macos":
        cargo_build_lib(all_feats)
        copy_macos_dylib_variants()
        if args.flutter:
            build_flutter_macos(flutter_dir)
        app = find_macos_app()
        if not app or not app.exists():
            print("[ERROR] No se encontró bundle .app tras el build de macOS.")
            sys.exit(2)
        maybe_copy_service_into_app(app)
        create_unsigned_dmg(app, version)
        print("[OK] Build macOS completado.")
        return

    if target == "android":
        if not args.flutter:
            print("[ERROR] Para Android es necesario --flutter.")
            sys.exit(3)
        build_flutter_android(flutter_dir)
        rename_android_apk(flutter_dir, version)
        print("[OK] Build Android completado.")
        return

    print(f"[ERROR] Plataforma {target} no soportada por este script.")
    sys.exit(4)

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as cpe:
        print(f"[ERROR] Comando falló con código {cpe.returncode}")
        sys.exit(cpe.returncode)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
