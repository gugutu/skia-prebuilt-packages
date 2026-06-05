#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


PACKAGE_TARGET = "skia_prebuilt_package"
PACKAGE_MARKER_BEGIN = "# SKIA_PREBUILT_PACKAGE_BEGIN"
PACKAGE_MARKER_END = "# SKIA_PREBUILT_PACKAGE_END"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skia", required=True, type=Path)
    args = parser.parse_args()

    skia = args.skia.resolve()
    if not (skia / "BUILD.gn").is_file():
        raise SystemExit(f"Skia source tree is missing: {skia}")

    adapt_dawn_build_helpers(skia)
    add_unified_static_package_target(skia)
    return 0


def adapt_dawn_build_helpers(skia: Path) -> None:
    dawn_root = skia / "third_party/dawn"
    cmake_utils = dawn_root / "cmake_utils.py"
    build_dawn = dawn_root / "build_dawn.py"

    if cmake_utils.is_file():
        replace_once(
            cmake_utils,
            marker='if os == "ios":',
            needle="""  if os == "mac":
    target_cpu_map = {
      "arm64": "arm64",
      "x64": "x86_64",
    }
    return "Darwin", target_cpu_map[cpu]

""",
            replacement="""  if os == "mac":
    target_cpu_map = {
      "arm64": "arm64",
      "x64": "x86_64",
    }
    return "Darwin", target_cpu_map[cpu]

  if os == "ios":
    target_cpu_map = {
      "arm64": "arm64",
      "x64": "x86_64",
    }
    return "iOS", target_cpu_map[cpu]

""",
            description="Dawn iOS CMake OS mapping",
        )
        replace_once(
            cmake_utils,
            marker="SKIA_DAWN_WINDOWS_HOST_TOOL_CPU",
            needle="""  # Explicitly tell CMake where to find the Resource Compiler, Manifest Tool, and Archiver.
  rc_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             args.target_cpu, "rc.exe")
  win_cfgs.append(f"-DCMAKE_RC_COMPILER={rc_exe_path.replace(os.sep, '/')}")
  mt_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             args.target_cpu, "mt.exe")
  win_cfgs.append(f"-DCMAKE_MT={mt_exe_path.replace(os.sep, '/')}")

""",
            replacement="""  # Explicitly tell CMake where to find the Resource Compiler, Manifest Tool, and Archiver.
  # rc.exe and mt.exe run on the host during configure/link steps, so ARM64
  # cross-builds from x64 runners must keep using x64 host tools.
  host_tool_cpu = os.environ.get("SKIA_DAWN_WINDOWS_HOST_TOOL_CPU", args.target_cpu)
  rc_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             host_tool_cpu, "rc.exe")
  win_cfgs.append(f"-DCMAKE_RC_COMPILER={rc_exe_path.replace(os.sep, '/')}")
  mt_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             host_tool_cpu, "mt.exe")
  win_cfgs.append(f"-DCMAKE_MT={mt_exe_path.replace(os.sep, '/')}")

""",
            description="Dawn Windows host tool selection",
        )

    if build_dawn.is_file():
        replace_once(
            build_dawn,
            marker="SKIA_DAWN_IOS_SYSROOT",
            needle="""  if target_os == "Darwin" or target_os == "iOS":
    configure_cmd.append(f"-DCMAKE_OSX_ARCHITECTURES={target_cpu}")

""",
            replacement="""  if target_os == "Darwin" or target_os == "iOS":
    configure_cmd.append(f"-DCMAKE_OSX_ARCHITECTURES={target_cpu}")

  if target_os == "iOS":
    ios_sysroot = os.environ.get("SKIA_DAWN_IOS_SYSROOT")
    if ios_sysroot:
      configure_cmd.append(f"-DCMAKE_OSX_SYSROOT={ios_sysroot}")
    ios_deployment_target = os.environ.get("SKIA_DAWN_IOS_DEPLOYMENT_TARGET")
    if ios_deployment_target:
      configure_cmd.append(f"-DCMAKE_OSX_DEPLOYMENT_TARGET={ios_deployment_target}")

""",
            description="Dawn iOS SDK selection",
        )
        replace_once(
            build_dawn,
            marker="SKIA_DAWN_TRIM_UNUSED_OPTIONS",
            needle='''      "-DTINT_ENABLE_INSTALL=OFF",
''',
            replacement='''      "-DTINT_ENABLE_INSTALL=OFF",
      # SKIA_DAWN_TRIM_UNUSED_OPTIONS
      # Skia Graphite/Dawn creates WGSL shader modules and then lets Dawn
      # translate them to the selected platform backend. Keep WGSL reader and
      # each enabled backend writer, but do not build optional tools, alternate
      # shader input paths, unused backends, samples, fuzzers, or debug helpers.
      "-DDAWN_ENABLE_NULL=OFF",
      "-DDAWN_ENABLE_WEBGPU_ON_WEBGPU=OFF",
      "-DDAWN_ENABLE_DESKTOP_GL=OFF",
      "-DDAWN_ENABLE_ASAN=OFF",
      "-DDAWN_ENABLE_TSAN=OFF",
      "-DDAWN_ENABLE_MSAN=OFF",
      "-DDAWN_ENABLE_UBSAN=OFF",
      "-DDAWN_ENABLE_RTTI=OFF",
      "-DDAWN_USE_WAYLAND=OFF",
      "-DDAWN_USE_X11=OFF",
      "-DDAWN_USE_WINDOWS_UI=OFF",
      "-DDAWN_USE_BUILT_DXC=OFF",
      "-DDAWN_DXC_ENABLE_ASSERTS_IN_NDEBUG=OFF",
      "-DDAWN_ENABLE_SWIFTSHADER=OFF",
      "-DDAWN_ALWAYS_ASSERT=OFF",
      "-DDAWN_BUILD_NODE_BINDINGS=OFF",
      "-DDAWN_BUILD_FUZZERS=OFF",
      "-DDAWN_EMIT_COVERAGE=OFF",
      "-DTINT_BUILD_CMD_TOOLS=OFF",
      "-DTINT_BUILD_SPV_READER=OFF",
      "-DTINT_BUILD_GLSL_WRITER=OFF",
      "-DTINT_BUILD_GLSL_VALIDATOR=OFF",
      "-DTINT_BUILD_WGSL_WRITER=OFF",
      "-DTINT_BUILD_NULL_WRITER=OFF",
      "-DTINT_BUILD_FUZZERS=OFF",
      "-DTINT_BUILD_FUZZER_VULKAN_SUPPORT=OFF",
      "-DTINT_BUILD_TINTD=OFF",
      "-DTINT_BUILD_AS_OTHER_OS=OFF",
      "-DTINT_BUILD_MESA=OFF",
      "-DTINT_ENABLE_IR_DUMPING=OFF",
      "-DTINT_ENABLE_IR_VALIDATION_ASSERTS=OFF",
      "-DTINT_ENABLE_BREAK_IN_DEBUGGER=OFF",
      "-DTINT_RANDOMIZE_HASHES=OFF",
''',
            description="Dawn optional feature trimming",
        )
        replace_if_present(
            build_dawn,
            old='''      f"-DDAWN_ENABLE_SPIRV_VALIDATION={gn_bool_to_cmake(args.dawn_enable_vulkan)}",
''',
            new='''      "-DDAWN_ENABLE_SPIRV_VALIDATION=OFF",
''',
        )


def add_unified_static_package_target(skia: Path) -> None:
    build_config = skia / "gn/BUILDCONFIG.gn"
    root_build = skia / "BUILD.gn"
    package_gn_dir = skia / "skia_prebuilt_package_gen"

    replace_once(
        build_config,
        marker='set_defaults("component") {\n  configs = default_configs\n}\n',
        needle='''set_defaults("component") {
  configs = default_configs
  if (!is_component_build) {
    complete_static_lib = true
  }
}
''',
        replacement='''set_defaults("component") {
  configs = default_configs
}
''',
        description="component static-library defaults",
    )

    text = root_build.read_text(encoding="utf-8")
    if PACKAGE_MARKER_BEGIN not in text:
        block = f'''
{PACKAGE_MARKER_BEGIN}
static_library("{PACKAGE_TARGET}") {{
  complete_static_lib = true
  sources = [ "skia_prebuilt_package_gen/empty.cpp" ]
  public_deps = [
    "//:skia",
    "//modules/skparagraph:skparagraph",
    "//modules/skresources:skresources",
    "//modules/skshaper:skshaper",
    "//modules/skunicode",
    "//modules/svg:svg",
  ]
}}
{PACKAGE_MARKER_END}
'''
        root_build.write_text(text.rstrip() + "\n" + block, encoding="utf-8")

    package_gn_dir.mkdir(parents=True, exist_ok=True)
    (package_gn_dir / "empty.cpp").write_text(
        "namespace skia_prebuilt_package {\nvoid anchor() {}\n}\n",
        encoding="utf-8",
    )


def replace_once(path: Path, *, marker: str, needle: str, replacement: str, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    if needle not in text:
        raise SystemExit(f"could not find {description} in {path}")
    path.write_text(text.replace(needle, replacement), encoding="utf-8")


def replace_if_present(path: Path, *, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
